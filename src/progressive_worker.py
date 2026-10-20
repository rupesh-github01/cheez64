"""Independent bounded dispatcher. Engine disabled unless an explicitly proved adapter is supplied."""
import time
import sqlite3
import threading
import hashlib
from pathlib import Path
from .evaluation_models import PositionContext,SearchConfig,Lookup,Evaluation,digest
from .evaluation_cache import CacheUnavailable
from .progressive_engine import UCIProcess,EngineSafetyError,code_hash
from .progressive_store import ProgressiveError,StaleWork



def measured_build(command):
    h=hashlib.sha256()
    for part in command:
        h.update(part.encode())
        path=Path(part)
        if path.is_file():
            with path.open('rb') as f:
                while block:=f.read(65536):h.update(block)
    return 'sha256:'+h.hexdigest()

class ProvedUCIAdapter:
    simulated=False
    def __init__(self,command,lock_path,proof,exclusive_host=False):
        if not exclusive_host:raise EngineSafetyError('Exclusive engine host attestation required')
        self.build=measured_build(command)
        self.command=command;self.lock_path=lock_path;self.proof=proof
        self.engine=None
    def search(self,context,config):
        if config.source.engine_build!=self.build:raise EngineSafetyError('Registered binary/build hash mismatch')
        networks={k:Path(str(v)) for k,v in config.applied_options.items() if k.lower().startswith('evalfile')}
        if config.source.nnue.startswith('embedded:'):
            if config.source.nnue!='embedded:'+self.build:raise EngineSafetyError('Embedded NNUE/build identity mismatch')
            if any(path.is_file() or (Path(self.command[0]).resolve().parent/path).is_file() for path in networks.values()):raise EngineSafetyError('External NNUE requires measured hash provenance')
        else:
            hashes={}
            for name,path in networks.items():
                if not path.is_absolute() or not path.is_file():raise EngineSafetyError('Absolute NNUE file required')
                h=hashlib.sha256()
                with path.open('rb') as f:
                    while block:=f.read(65536):h.update(block)
                hashes[name]=h.hexdigest()
            expected=('sha256:'+next(iter(hashes.values()))) if len(hashes)==1 else ('sha256:'+digest(hashes))
            if not hashes or config.source.nnue!=expected:raise EngineSafetyError('NNUE file hash mismatch')
        if self.engine is None or self.engine.closed:
            if measured_build(self.command)!=self.build:raise EngineSafetyError('Binary changed before retry')
            self.engine=UCIProcess(self.command,self.lock_path,self.proof)
        return self.engine.search(context,config)
    def close(self):
        if self.engine is not None:self.engine.close()


class ProgressiveWorker:
    def __init__(self,store,cache=None,adapter=None):
        self.store=store;self.cache=cache;self.adapter=adapter;self.stopped=False
        if adapter is not None and not isinstance(adapter,ProvedUCIAdapter) and not getattr(adapter,'simulated',False):
            raise EngineSafetyError('Unproved engine adapter')

    def prepare_pending(self,max_jobs=100):
        with self.store.connection() as c:ids=[r[0] for r in c.execute("SELECT id FROM jobs WHERE state='PENDING' ORDER BY created LIMIT ?",(max_jobs,))]
        for jid in ids:self.store.prepare(jid,self.cache)
        return len(ids)

    def step(self):
        if self.stopped or self.adapter is None:return False
        self.store.recover()
        task=self.store.claim()
        if task is None:
            for _ in range(50):
                if self.store.mark_unfunded()==0:break
            # Shared/cache-only completions may unlock whole phases without a new search.
            for _ in range(150):
                if self.refresh()==0:return False
                task=self.store.claim()
                if task is not None:break
                for _ in range(50):
                    if self.store.mark_unfunded()==0:break
            if task is None:return False
        if bool(task['profile_simulation'])!=bool(getattr(self.adapter,'simulated',False)):
            self.stopped=True
            self.store.complete(task['id'],task['token'],cost=0,error='PROFILE_SIMULATION_MISMATCH',retryable=False)
            raise EngineSafetyError('Synthetic/real producer profile mismatch')
        ctx=PositionContext.model_validate(task['context']);cfg=SearchConfig.model_validate(task['config'])
        started=time.monotonic();monitor_stop=threading.Event()
        def monitor():
            last=time.monotonic()
            while not monitor_stop.wait(.1):
                try:
                    with self.store.connection() as c:active=c.execute('SELECT 1 FROM interests WHERE work_id=? AND active=1',(task['id'],)).fetchone()
                    if not active or time.monotonic()-started>task['reservation']:
                        self.shutdown();return
                    if time.monotonic()-last>=5:self.store.heartbeat(task['id'],task['token']);last=time.monotonic()
                except (StaleWork,ProgressiveError,sqlite3.DatabaseError):self.shutdown();return
        thread=threading.Thread(target=monitor,daemon=True);thread.start()
        try:
            if self.store.reconcile_work(task['id'])==0:
                self.store.complete(task['id'],task['token'],cost=0,error='SOURCE_MISSING');return True
            evaluation=None;used_cache=False
            if self.cache is not None:
                query=Lookup(context=ctx,require_trusted=ctx.root_history_complete,allow_advisory=not ctx.root_history_complete,
                             config_hash=digest(cfg.model_dump()),min_depth={'screen':8,'medium':12,'deep':16}[task['tier']])
                try:
                    hit=self.cache.lookup(query)
                    if hit['evaluation']:
                        used_cache=True;frame={'request':ctx.model_dump()};evaluation=Evaluation.model_validate(self.store._cache_to_evaluation(frame,hit['evaluation']))
                except (ProgressiveError,CacheUnavailable,sqlite3.DatabaseError,ValueError):pass
            if evaluation is None:evaluation=self.adapter.search(ctx,cfg)
            self.store.complete(task['id'],task['token'],evaluation,cost=0 if used_cache else getattr(self.adapter,'virtual_cost',time.monotonic()-started),simulation=getattr(self.adapter,'simulated',False),origin='CACHE_REUSE' if used_cache else 'LOCAL_SEARCH')
        except StaleWork:pass
        except EngineSafetyError as exc:
            self.stopped=True
            try:self.store.complete(task['id'],task['token'],cost=time.monotonic()-started,error='ENGINE_SAFETY_REFUSAL',retryable=False)
            except StaleWork:pass
        except (ValueError,RuntimeError,TimeoutError,OSError) as exc:
            try:self.store.complete(task['id'],task['token'],cost=time.monotonic()-started,error='NO_EXACT_SCORE' if str(exc)=='NO_EXACT_SCORE' else type(exc).__name__,retryable=not isinstance(exc,ValueError))
            except StaleWork:pass
        finally:
            monitor_stop.set();thread.join(timeout=3.5)
        if self.cache is not None:self.store.flush_outbox(self.cache)
        self.store.redact_missing()
        self.refresh(task['id'])
        return True

    def refresh(self,wid=None):
        with self.store.connection() as c:
            ids=[r[0] for r in c.execute("SELECT job_id FROM interests WHERE work_id=? AND active=1 LIMIT 100",(wid,))] if wid else [r[0] for r in c.execute("SELECT j.id FROM jobs j WHERE j.state IN ('PREPARED','ACTIVE') AND j.reason IS NOT 'WAITING_PROFILE' AND NOT EXISTS(SELECT 1 FROM interests i JOIN work w ON w.id=i.work_id WHERE i.job_id=j.id AND i.active=1 AND w.state IN ('PENDING','RUNNING','RETRY_WAIT')) ORDER BY j.checked,j.created LIMIT 100")]
        changed=sum(self.store.advance(jid) for jid in ids)
        if ids:self.store.touch_refresh(ids)
        return changed

    def shutdown(self):
        self.stopped=True
        if self.adapter is not None:self.adapter.close()
