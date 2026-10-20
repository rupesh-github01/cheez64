"""Independent progressive scheduler: immutable evidence, fenced costs and shared work."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from .evaluation_cache import safe_path,CacheUnavailable
from .evaluation_models import canonical_json,digest,SearchConfig,PositionContext,Lookup,Evaluation
from .progressive_models import ProgressiveLimits,Schedule,tier_config,TIERS
from .progressive_prepare import replay,shortlist


class ProgressiveError(RuntimeError): pass
class StaleWork(ProgressiveError): pass


DDL=(
 'CREATE TABLE IF NOT EXISTS progressive_meta(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,limits TEXT NOT NULL,tick INTEGER NOT NULL DEFAULT 0,last_clock REAL NOT NULL DEFAULT 0,refresh_tick INTEGER NOT NULL DEFAULT 0,library_key TEXT NOT NULL)',
 'CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY,config TEXT NOT NULL,simulation INTEGER NOT NULL DEFAULT 0)',
 'CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,profile TEXT,prepare_only INTEGER NOT NULL,budget REAL NOT NULL,spent REAL NOT NULL DEFAULT 0,reserved REAL NOT NULL DEFAULT 0)',
 '''CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL REFERENCES batches(id),game_id TEXT NOT NULL,
    request_key TEXT NOT NULL UNIQUE,priority INTEGER NOT NULL,state TEXT NOT NULL,reason TEXT,phase TEXT NOT NULL,
    fingerprint TEXT NOT NULL,pgn_hash TEXT NOT NULL,created REAL NOT NULL,last_dispatch REAL NOT NULL DEFAULT 0,
    token TEXT,lease REAL,generation INTEGER NOT NULL DEFAULT 0,spent REAL NOT NULL DEFAULT 0,reserved REAL NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,checked REAL NOT NULL DEFAULT 0)''',
 '''CREATE TABLE IF NOT EXISTS payloads(job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,pgn TEXT NOT NULL,artifact TEXT,previous_artifact TEXT)''',
 'CREATE TABLE IF NOT EXISTS request_keys(key TEXT PRIMARY KEY,payload TEXT NOT NULL,response TEXT NOT NULL,created REAL NOT NULL)',
 'CREATE TABLE IF NOT EXISTS contexts(id TEXT PRIMARY KEY,canonical TEXT NOT NULL,request TEXT NOT NULL)',
 '''CREATE TABLE IF NOT EXISTS work(id TEXT PRIMARY KEY,context_id TEXT NOT NULL REFERENCES contexts(id),config TEXT NOT NULL,tier TEXT NOT NULL,
    state TEXT NOT NULL,created REAL NOT NULL,not_before REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,generation INTEGER NOT NULL DEFAULT 0,
    token TEXT,lease REAL,sponsor TEXT REFERENCES jobs(id),reservation REAL NOT NULL DEFAULT 0,result TEXT,error TEXT)''',
 '''CREATE TABLE IF NOT EXISTS interests(job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    work_id TEXT NOT NULL REFERENCES work(id),active INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(job_id,work_id)) WITHOUT ROWID''',
 '''CREATE TABLE IF NOT EXISTS attempts(token TEXT PRIMARY KEY,work_id TEXT NOT NULL REFERENCES work(id),job_id TEXT NOT NULL REFERENCES jobs(id),
    batch_id TEXT NOT NULL REFERENCES batches(id),reserved REAL NOT NULL,charged REAL,uncertain INTEGER NOT NULL DEFAULT 0,started REAL NOT NULL)''',
 'CREATE TABLE IF NOT EXISTS outbox(work_id TEXT PRIMARY KEY REFERENCES work(id),sent INTEGER NOT NULL DEFAULT 0,error TEXT)',
 'CREATE INDEX IF NOT EXISTS work_claim ON work(state,not_before,created)',
 'CREATE INDEX IF NOT EXISTS work_lease ON work(state,lease)',
 'CREATE INDEX IF NOT EXISTS interest_work ON interests(work_id,active,job_id)',
 'CREATE INDEX IF NOT EXISTS job_game ON jobs(game_id,state)',
 'CREATE INDEX IF NOT EXISTS work_context ON work(context_id)',
)
ACTIVE=('PENDING','PREPARING','PREPARED','ACTIVE')


def source_game(path,gid):
    path=Path(path).resolve()
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.25)
    try:
        c.execute('PRAGMA query_only=ON')
        row=c.execute('SELECT pgn,fingerprint FROM games WHERE game_id=?',(gid,)).fetchone()
        if row is None: return None
        return {'pgn':row[0],'fingerprint':row[1],'hash':hashlib.sha256(row[0].encode()).hexdigest()}
    finally: c.close()


class ProgressiveStore:
    def __init__(self,path,library_path,limits=None,clock=time.time):
        self.path=safe_path(path);self.library=Path(library_path).resolve();self.limits=limits or ProgressiveLimits();self.clock=clock
        for other in (self.library,Path(os.environ.get('CHESS_COACH_EVAL_CACHE_DB','/nonexistent')).resolve()):
            if self.path==other or (self.path.exists() and other.exists() and self.path.samefile(other)):
                raise ProgressiveError('Scheduler cannot alias library/cache')

    def sizes(self):
        return {s:p.stat().st_size if p.exists() else 0 for s,p in [('main',self.path),('wal',Path(str(self.path)+'-wal')),('shm',Path(str(self.path)+'-shm'))]}

    @contextmanager
    def connection(self,write=False,initialize=False):
        safe_path(self.path)
        if not initialize and not self.path.is_file(): raise ProgressiveError('STORE_UNINITIALIZED')
        c=sqlite3.connect(self.path if write else self.path.as_uri()+'?mode=ro',uri=not write,timeout=.1,isolation_level=None)
        c.row_factory=sqlite3.Row
        deadline=time.monotonic()+5;c.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:
            c.execute('PRAGMA foreign_keys=ON')
            if write:
                c.execute('PRAGMA busy_timeout=100')
                pages=(self.limits.max_bytes-8*1024**2)//c.execute('PRAGMA page_size').fetchone()[0]
                c.execute(f'PRAGMA max_page_count={pages}')
                for attempt in range(4):
                    try:c.execute('BEGIN IMMEDIATE');break
                    except sqlite3.OperationalError:
                        if attempt==3:raise
                        time.sleep(.01*(attempt+1))
                if sum(self.sizes().values())+8*1024**2>self.limits.max_bytes: raise ProgressiveError('STORAGE_CAPACITY')
            else: c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
            if not initialize:
                row=c.execute('SELECT version,limits,last_clock,library_key FROM progressive_meta WHERE id=1').fetchone()
                if row is None or row['version']!=1: raise ProgressiveError('INCOMPATIBLE_SCHEMA')
                if row['library_key']!=digest(str(self.library)):raise ProgressiveError('LIBRARY_SCOPE_MISMATCH')
                if write and row['limits']!=self.limits.model_dump_json(): raise ProgressiveError('LIMIT_POLICY_MISMATCH')
                if write:
                    if self.clock()<row['last_clock']:raise ProgressiveError('CLOCK_REGRESSION; dispatcher paused')
                    c.execute('UPDATE progressive_meta SET last_clock=?',(self.clock(),))
            yield c
            if write:c.commit()
        except BaseException:
            if c.in_transaction:c.rollback()
            raise
        finally:c.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            with sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True) as c:
                tables={x[0] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and 'progressive_meta' not in tables: raise ProgressiveError('Refusing non-progressive store')
        with self.connection(True,True) as c:
            for sql in DDL:c.execute(sql)
            c.execute('INSERT OR IGNORE INTO progressive_meta VALUES(1,1,?,0,?,0,?)',(self.limits.model_dump_json(),self.clock(),digest(str(self.library))))
            row=c.execute('SELECT version,limits,library_key FROM progressive_meta').fetchone()
            if row['version']!=1 or row['limits']!=self.limits.model_dump_json() or row['library_key']!=digest(str(self.library)): raise ProgressiveError('Schema/policy mismatch')
        with sqlite3.connect(self.path) as c:c.execute('PRAGMA journal_mode=WAL')

    def register_profile(self,config,simulation=False):
        config=SearchConfig.model_validate(config.model_dump())
        if config.source.kind!='local_stockfish':raise ValueError('Local profile required')
        if not simulation:
            import re
            if not re.fullmatch(r'sha256:[a-f0-9]{64}',config.source.engine_build) or not config.source.provenance.startswith('pcis-progressive:v1:reset-per-context'):
                raise ValueError('Measured binary hash and reset-policy provenance required')
            if not (config.source.nnue.startswith('embedded:sha256:') or config.source.nnue.startswith('sha256:')):raise ValueError('NNUE provenance required')
        key=digest({'config':config.model_dump(),'simulation':simulation})
        with self.connection(True) as c:c.execute('INSERT OR IGNORE INTO profiles VALUES(?,?,?)',(key,config.model_dump_json(),int(simulation)))
        return key

    def enqueue(self,request:Schedule):
        request=Schedule.model_validate(request.model_dump()); snapshots=[];snapshot_bytes=0
        with self.connection() as c:prior=c.execute('SELECT payload,response FROM request_keys WHERE key=?',(request.idempotency_key,)).fetchone()
        if prior:
            if prior['payload']!=request.model_dump_json():raise ValueError('Idempotency payload mismatch')
            return json.loads(prior['response'])
        for gid in request.game_ids:
            snapshot=source_game(self.library,gid)
            if snapshot is None:raise ValueError('Unknown source game')
            if len(snapshot['pgn'].encode())>2*1024**2:raise ValueError('PGN exceeds 2 MiB')
            snapshot_bytes+=len(snapshot['pgn'].encode())
            if snapshot_bytes>2*1024**2:raise ValueError('Batch snapshot bytes exceed 2 MiB; use smaller chunks')
            snapshots.append((gid,snapshot))
        if sum(len(x['pgn'].encode()) for _,x in snapshots)>2*1024**2:raise ValueError('Batch snapshot bytes exceed 2 MiB; use smaller chunks')
        with self.connection(True) as c:
            prior=c.execute('SELECT payload,response FROM request_keys WHERE key=?',(request.idempotency_key,)).fetchone()
            if prior:
                if prior['payload']!=request.model_dump_json():raise ValueError('Idempotency payload mismatch')
                return json.loads(prior['response'])
            if request.profile_id and not c.execute('SELECT 1 FROM profiles WHERE id=?',(request.profile_id,)).fetchone():raise ValueError('Unknown profile')
            bid=request.batch_id or digest({'library':str(self.library),'key':request.idempotency_key})
            b=c.execute('SELECT * FROM batches WHERE id=?',(bid,)).fetchone()
            if b and (b['profile']!=request.profile_id or b['budget']!=request.budget_seconds or bool(b['prepare_only'])!=request.prepare_only):raise ValueError('Immutable batch policy mismatch')
            c.execute('INSERT OR IGNORE INTO batches(id,profile,prepare_only,budget) VALUES(?,?,?,?)',(bid,request.profile_id,request.prepare_only,request.budget_seconds))
            ids=[]
            for gid,snapshot in snapshots:
                key=digest({'batch':bid,'game':gid,'snapshot':snapshot['hash'],'key':request.idempotency_key})
                old=c.execute('SELECT id,priority FROM jobs WHERE request_key=?',(key,)).fetchone()
                if old:
                    if old['priority']!=request.priority:raise ValueError('Idempotency payload mismatch')
                    ids.append(old['id']);continue
                if c.execute('SELECT count(*) FROM jobs').fetchone()[0]>=self.limits.max_jobs or c.execute('SELECT count(*) FROM jobs WHERE batch_id=?',(bid,)).fetchone()[0]>=5000:raise ProgressiveError('JOB_CAPACITY')
                jid=uuid.uuid4().hex
                c.execute('''INSERT INTO jobs(id,batch_id,game_id,request_key,priority,state,phase,fingerprint,pgn_hash,created)
                          VALUES(?,?,?,?,?,'PENDING','prepare',?,?,?)''',(jid,bid,gid,key,request.priority,snapshot['fingerprint'],snapshot['hash'],self.clock()))
                c.execute('INSERT INTO payloads(job_id,pgn) VALUES(?,?)',(jid,snapshot['pgn']))
                ids.append(jid)
            result={'batch_id':bid,'job_ids':ids}
            c.execute('INSERT INTO request_keys VALUES(?,?,?,?)',(request.idempotency_key,request.model_dump_json(),canonical_json(result),self.clock()))
        return result

    def _current(self,job):
        current=source_game(self.library,job['game_id'])
        return 'SOURCE_MISSING' if current is None else ('STALE_SOURCE' if current['hash']!=job['pgn_hash'] or current['fingerprint']!=job['fingerprint'] else None)

    @staticmethod
    def _publish(c,jid,artifact):
        text=canonical_json(artifact)
        if len(text.encode())>1024**2:raise ProgressiveError('ARTIFACT_CAPACITY')
        c.execute('UPDATE payloads SET previous_artifact=artifact,artifact=? WHERE job_id=?',(text,jid))
        c.execute('UPDATE jobs SET revision=revision+1 WHERE id=?',(jid,))

    def _add_work(self,c,job,frame,config,tier,admission=None):
        key=digest({'context':frame['key'],'config':config.model_dump(),'tier':tier,'contract':1,'reset':'per-context-v1'})
        old=c.execute('SELECT id,context_id,config,tier FROM work WHERE id=?',(key,)).fetchone()
        context_row=c.execute('SELECT canonical FROM contexts WHERE id=?',(frame['key'],)).fetchone()
        if context_row and context_row[0]!=frame['canonical']:raise ProgressiveError('Context hash collision')
        if old and (old['context_id']!=frame['key'] or old['tier']!=tier or canonical_json(json.loads(old['config']))!=canonical_json(config.model_dump())):raise ProgressiveError('Work hash collision')
        if not old:
            counts=admission if admission is not None else list(c.execute("SELECT count(*),sum(state IN ('PENDING','RUNNING','RETRY_WAIT')) FROM work").fetchone())
            if counts[0]>=self.limits.max_work or (counts[1] or 0)>=self.limits.max_active:return None
            existing=c.execute('SELECT canonical FROM contexts WHERE id=?',(frame['key'],)).fetchone()
            if existing and existing[0]!=frame['canonical']:raise ProgressiveError('Context hash collision')
            c.execute('INSERT OR IGNORE INTO contexts VALUES(?,?,?)',(frame['key'],frame['canonical'],canonical_json(frame['request'])))
            c.execute('INSERT INTO work(id,context_id,config,tier,state,created) VALUES(?,?,?,?,?,?)',
                      (key,frame['key'],config.model_dump_json(),tier,'TERMINAL' if frame.get('terminal') else 'PENDING',self.clock()))
            if frame.get('terminal'):c.execute('UPDATE work SET result=? WHERE id=?',(canonical_json({'terminal_observation':frame['terminal'],'origin':'RULES'}),key))
            if admission is not None:admission[0]+=1;admission[1]=(admission[1] or 0)+1
        if old:c.execute("UPDATE work SET state='PENDING',attempts=0,generation=generation+1 WHERE id=? AND state='CANCELLED' AND result IS NULL",(key,))
        c.execute('INSERT OR IGNORE INTO interests(job_id,work_id) VALUES(?,?)',(job['id'],key))
        return key

    def prepare(self,jid,cache=None):
        token=uuid.uuid4().hex
        with self.connection(True) as c:
            job=c.execute('SELECT j.*,p.pgn,p.artifact FROM jobs j JOIN payloads p ON p.job_id=j.id WHERE j.id=?',(jid,)).fetchone()
            if job is None:raise KeyError(jid)
            if job['state']!='PENDING':return False
            c.execute("UPDATE jobs SET state='PREPARING',token=?,lease=? WHERE id=?",(token,self.clock()+self.limits.lease_seconds,jid))
            batch=c.execute('SELECT * FROM batches WHERE id=?',(job['batch_id'],)).fetchone()
            configrow=c.execute('SELECT config,simulation FROM profiles WHERE id=?',(batch['profile'],)).fetchone()
        config=SearchConfig.model_validate_json(configrow[0]) if configrow else None
        reason=self._current(job)
        try:
            if reason:raise ValueError(reason)
            replay_started=time.perf_counter()
            frames,decisions,terminal=replay(job['pgn'])
            replay_seconds=time.perf_counter()-replay_started
            cache_started=time.perf_counter()
            coverage={'discovered_positions':len(frames),'decisions':len(decisions),'trusted_cache':0,'advisory_cache':0,'cache_misses':0,'cache_unavailable':0,'incompatible':0}
            if cache is None:coverage['cache_unavailable']=len(frames)
            else:
                try:
                    with cache.reader() as conn:
                        for frame in frames:
                            # Cheap exact-key miss check avoids revalidating every already-replayed history.
                            exists=conn.execute('SELECT 1 FROM eval_contexts WHERE hash IN (?,?)',(frame['key'],frame['provider_key'])).fetchone()
                            if not exists:coverage['cache_misses']+=1;continue
                            ctx=PositionContext.model_validate(frame['request'])
                            query=Lookup(context=ctx,allow_advisory=True,source='local_stockfish' if config else None,
                                         config_hash=digest(tier_config(config,'screen').model_dump()) if config else None)
                            hit=cache.lookup(query,conn)
                            if hit['evaluation'] is None and config:
                                hit=cache.lookup(Lookup(context=ctx,allow_advisory=True,source='lichess_cloud'),conn)
                            if hit['evaluation'] is not None:
                                name='trusted_cache' if hit['context_compatibility']=='TRUSTED_CONTEXT_MATCH' else 'advisory_cache'
                                coverage[name]+=1;frame['cache_evidence']=hit['evaluation']
                            else:coverage['incompatible']+=1
                except (CacheUnavailable,sqlite3.DatabaseError):coverage['cache_unavailable']=len(frames)-sum(coverage[k] for k in ('trusted_cache','advisory_cache','cache_misses','incompatible'))
            cache_seconds=time.perf_counter()-cache_started
            ranking_started=time.perf_counter()
            for d in decisions:
                advisory=frames[d['ply']-1].get('cache_evidence')
                if advisory and advisory['context_compatibility']=='ADVISORY_CONTEXT_UNVERIFIED' and advisory['returned_multipv']==advisory['config']['multipv'] and advisory['pvs'][0]['moves'] and advisory['pvs'][0]['moves'][0]!=d['move']:
                    d['score']+=2;d['advisory_pv_disagreement']=True;d['advisory_context']='ADVISORY_CONTEXT_UNVERIFIED'
            selected=shortlist(decisions,self.limits.shortlist)
            artifact={'schema':'progressive-analysis/1','policy_version':1,'timings':{'replay_features_seconds':replay_seconds,'cache_lookup_seconds':cache_seconds,'candidate_ranking_seconds':time.perf_counter()-ranking_started},'coverage':coverage,'observations':decisions,
                      'candidates':selected,'terminal':terminal,'work_ids':[], 'verification':{'engine_verified_decisions':0},'tier_requirements':{'screen':{'requested_depth':10,'minimum_depth':8},'medium':{'requested_depth':14,'minimum_depth':12},'deep':{'requested_depth':18,'minimum_depth':16}},
                      'partial':True,'limitations':['Structural candidates are hypotheses, not blunders.','Unsearched positions are not engine verified.'],
                      'input_sha256':job['pgn_hash'],'source_freshness':'MATCHED_AT_PREPARATION'}
            state='COMPLETED' if batch['prepare_only'] else 'PREPARED'
            reason='PREPARE_ONLY' if batch['prepare_only'] else ('WAITING_PROFILE' if config is None else None)
        except ValueError as exc:
            frames=[];selected=[];artifact={'schema':'progressive-analysis/1','partial':True,'error':str(exc),'verification':{'engine_verified_decisions':0}}
            state='SOURCE_MISSING' if reason=='SOURCE_MISSING' else ('STALE_SOURCE' if reason=='STALE_SOURCE' else 'UNSUPPORTED');reason=str(exc)
        with self.connection(True) as c:
            row=c.execute('SELECT state,token,lease FROM jobs WHERE id=?',(jid,)).fetchone()
            if row['state']!='PREPARING' or row['token']!=token or row['lease']<=self.clock():raise StaleWork('Preparation fence lost')
            if state=='PREPARED' and config:
                admission=list(c.execute("SELECT count(*),sum(state IN ('PENDING','RUNNING','RETRY_WAIT')) FROM work").fetchone())
                chosen=[]
                for d in selected:
                    chosen.extend([d['ply']-1,d['ply']])
                # Fill remaining screen coverage with deterministic uniform anchors.
                chosen.extend(range(0,len(frames),max(1,len(frames)//24)))
                indices=list(dict.fromkeys(chosen))[:self.limits.screen_contexts]
                artifact['coverage'].update(planned_screen_contexts=len(indices),admitted_screen_contexts=0,deferred_screen_contexts=0)
                for i in indices:
                    wid=self._add_work(c,job,frames[i],tier_config(config,'screen'),'screen',admission)
                    if wid:
                        artifact['work_ids'].append(wid)
                        artifact['coverage']['admitted_screen_contexts']+=1
                        evidence=frames[i].get('cache_evidence')
                        if evidence and evidence['context_compatibility']=='TRUSTED_CONTEXT_MATCH' and evidence['achieved_depth']>=8:
                            c.execute("UPDATE work SET state='SUCCEEDED',result=? WHERE id=? AND state='PENDING'",(canonical_json({'evaluation':self._cache_to_evaluation(frames[i],evidence),'origin':'CACHE_REUSE','simulation':bool(configrow['simulation']),'quality':'MINIMUM_TIER_MET','minimum_depth':8,'requested_depth_met':evidence['achieved_depth']>=evidence['config']['depth']}),wid))
                    else:
                        artifact['limitations'].append('WORK_CAPACITY; some screening deferred')
                        artifact['coverage']['deferred_screen_contexts']=len(indices)-artifact['coverage']['admitted_screen_contexts']
                        break
            self._publish(c,jid,artifact)
            c.execute('UPDATE jobs SET state=?,reason=?,phase=?,token=NULL,lease=NULL WHERE id=?',(state,reason,'prepared',jid))
        return True

    @staticmethod
    def _cache_to_evaluation(frame,value):
        return {'context':frame['request'],'config':value['config'],'achieved_depth':value['achieved_depth'],
                'nodes':value['nodes'],'knodes':value['knodes'],'elapsed_seconds':value['elapsed_seconds'],
                'pvs':[{k:v for k,v in p.items() if k!='rank'} for p in value['pvs']], 'provider_fen':value['provider_fen']}

    def claim(self):
        now=self.clock()
        with self.connection(True) as c:
            tick=c.execute('SELECT tick FROM progressive_meta').fetchone()[0]
            lane=(2,2,1,2,1,2,0)[tick%7]
            row=c.execute('''SELECT w.*,j.id AS job,j.batch_id,j.spent AS jspent,j.reserved AS jreserved,b.spent AS bspent,b.reserved AS breserved,b.budget,pf.simulation AS profile_simulation,j.created AS jcreated
               FROM work w JOIN interests i ON i.work_id=w.id AND i.active=1 JOIN jobs j ON j.id=i.job_id
               JOIN batches b ON b.id=j.batch_id JOIN profiles pf ON pf.id=b.profile WHERE w.state IN ('PENDING','RETRY_WAIT') AND w.not_before<=?
               AND j.state IN ('PREPARED','ACTIVE')
               AND j.spent+j.reserved+json_extract(w.config,'$.time_seconds')+6<=?
               AND b.spent+b.reserved+json_extract(w.config,'$.time_seconds')+6<=b.budget
               ORDER BY (?-j.created>=60) DESC,(j.priority=?) DESC,j.last_dispatch,j.created,w.created,w.id LIMIT 1''',(now,self.limits.game_budget,now,lane)).fetchone()
            if row is None:return None
            cfg=SearchConfig.model_validate_json(row['config']); reserve=cfg.time_seconds+6
            if row['jspent']+row['jreserved']+reserve>self.limits.game_budget or row['bspent']+row['breserved']+reserve>row['budget']:
                c.execute("UPDATE jobs SET state='PARTIAL',reason='BUDGET_EXHAUSTED' WHERE id=?",(row['job'],));return None
            # One managed slot, also for in-memory mock adapters. Expiry recovery must settle first.
            if c.execute("SELECT 1 FROM work WHERE state='RUNNING'").fetchone():return None
            token=uuid.uuid4().hex
            c.execute("UPDATE work SET state='RUNNING',token=?,lease=?,attempts=attempts+1,sponsor=?,reservation=? WHERE id=?",(token,now+self.limits.lease_seconds,row['job'],reserve,row['id']))
            c.execute("UPDATE jobs SET state='ACTIVE',reserved=reserved+?,last_dispatch=? WHERE id=?",(reserve,now,row['job']))
            c.execute('UPDATE batches SET reserved=reserved+? WHERE id=?',(reserve,row['batch_id']))
            c.execute('INSERT INTO attempts(token,work_id,job_id,batch_id,reserved,started) VALUES(?,?,?,?,?,?)',(token,row['id'],row['job'],row['batch_id'],reserve,now))
            c.execute('UPDATE progressive_meta SET tick=tick+1')
            ctx=c.execute('SELECT request FROM contexts WHERE id=?',(row['context_id'],)).fetchone()[0]
            return {**dict(row),'token':token,'context':json.loads(ctx),'config':cfg.model_dump(),'reservation':reserve}

    def heartbeat(self,wid,token):
        with self.connection(True) as c:
            cursor=c.execute("UPDATE work SET lease=? WHERE id=? AND token=? AND state='RUNNING' AND lease>?",(self.clock()+self.limits.lease_seconds,wid,token,self.clock()))
            if cursor.rowcount!=1:raise StaleWork('Heartbeat fence lost')

    def _settle(self,c,token,cost,uncertain=False):
        a=c.execute('SELECT * FROM attempts WHERE token=?',(token,)).fetchone()
        if a is None or a['charged'] is not None:raise StaleWork('Already settled')
        if cost>a['reserved']+.001:raise ProgressiveError('RESOURCE_BUDGET_OVERRUN; stop dispatcher')
        cost=max(0,cost)
        c.execute('UPDATE attempts SET charged=?,uncertain=? WHERE token=?',(cost,int(uncertain),token))
        for table,key in [('jobs',a['job_id']),('batches',a['batch_id'])]:
            c.execute(f'UPDATE {table} SET reserved=max(0,reserved-?),spent=spent+? WHERE id=?',(a['reserved'],cost,key))

    def complete(self,wid,token,evaluation=None,cost=0,error=None,simulation=False,origin="LOCAL_SEARCH",retryable=True):
        if evaluation is not None:evaluation=Evaluation.model_validate(evaluation.model_dump())
        if isinstance(cost,bool) or cost<0 or not __import__('math').isfinite(cost):raise ValueError('Invalid measured cost')
        with self.connection() as reader:
            source_jobs=reader.execute('SELECT j.* FROM jobs j JOIN interests i ON i.job_id=j.id WHERE i.work_id=? AND i.active=1',(wid,)).fetchall()
        source_states=self._source_states(source_jobs)
        with self.connection(True) as c:
            w=c.execute('SELECT * FROM work WHERE id=?',(wid,)).fetchone()
            if w is None or w['token']!=token or w['state']!='RUNNING' or w['lease']<=self.clock():raise StaleWork('Completion fence lost')
            interests=c.execute('SELECT j.* FROM jobs j JOIN interests i ON i.job_id=j.id WHERE i.work_id=? AND i.active=1',(wid,)).fetchall()
            for job in interests:
                bad=source_states.get(job['id'])
                if bad:
                    c.execute('UPDATE jobs SET state=?,reason=? WHERE id=?',(bad,bad,job['id']));c.execute('UPDATE interests SET active=0 WHERE job_id=?',(job['id'],))
            active=c.execute('SELECT 1 FROM interests WHERE work_id=? AND active=1',(wid,)).fetchone()
            self._settle(c,token,cost)
            if not active:state='CANCELLED';result=None
            elif error:
                state='LIMIT_REACHED' if error=='NO_EXACT_SCORE' else ('RETRY_WAIT' if retryable and w['attempts']<self.limits.max_attempts else 'FAILED');result=None
            else:
                if evaluation is None:raise ValueError('Result required')
                ctx=c.execute('SELECT canonical FROM contexts WHERE id=?',(w['context_id'],)).fetchone()[0]
                if canonical_json(evaluation.context.identity())!=ctx or canonical_json(evaluation.config.model_dump())!=canonical_json(json.loads(w['config'])):raise ValueError('Incompatible task result')
                state='SUCCEEDED' if evaluation.achieved_depth>=TIERS[w['tier']] else 'LIMIT_REACHED'
                result=canonical_json({'evaluation':evaluation.model_dump(),'origin':origin,'simulation':simulation,'quality':'MINIMUM_TIER_MET' if state=='SUCCEEDED' else 'LIMIT_REACHED','minimum_depth':TIERS[w['tier']],'requested_depth_met':evaluation.achieved_depth>=evaluation.config.depth})
            c.execute('UPDATE work SET state=?,result=?,error=?,not_before=?,token=NULL,lease=NULL WHERE id=?',(state,result,error,self.clock()+2**w['attempts']+(int(wid[:2],16)%10)/100,wid))
            if result:c.execute('INSERT OR IGNORE INTO outbox(work_id) VALUES(?)',(wid,))

    def cancel(self,jid):
        with self.connection(True) as c:
            if not c.execute('SELECT 1 FROM jobs WHERE id=?',(jid,)).fetchone():raise KeyError(jid)
            c.execute("UPDATE jobs SET state='CANCELLED',generation=generation+1,token=NULL,lease=NULL WHERE id=?",(jid,))
            c.execute('UPDATE interests SET active=0 WHERE job_id=?',(jid,))
            c.execute("UPDATE work SET state='CANCELLED' WHERE state IN ('PENDING','RETRY_WAIT') AND NOT EXISTS(SELECT 1 FROM interests WHERE work_id=work.id AND active=1)")

    def recover(self):
        with self.connection(True) as c:
            rows=c.execute("SELECT * FROM work WHERE state='RUNNING' AND lease<=?",(self.clock(),)).fetchall()
            for row in rows:
                self._settle(c,row['token'],row['reservation'],True)
                state='RETRY_WAIT' if row['attempts']<self.limits.max_attempts else 'FAILED'
                c.execute('UPDATE work SET state=?,token=NULL,lease=NULL,error=?,not_before=? WHERE id=?',(state,'CRASH_UNCERTAIN_COST',self.clock()+2**row['attempts'],row['id']))
            c.execute("UPDATE jobs SET state='PENDING',token=NULL,lease=NULL WHERE state='PREPARING' AND lease<=?",(self.clock(),))
            return len(rows)

    def get(self,jid,limit=100,after=""):
        if not 1<=limit<=100 or len(after)>64:raise ValueError("Invalid result page")
        with self.connection() as c:
            row=c.execute('SELECT id,batch_id,game_id,state,reason,phase,revision,spent,reserved,pgn_hash,fingerprint,payloads.artifact FROM jobs JOIN payloads ON payloads.job_id=jobs.id WHERE id=?',(jid,)).fetchone()
            if row is None:raise KeyError(jid)
            data=dict(row)
            if self._current(row):return {'id':jid,'state':self._current(row),'artifact':None,'source_freshness':'UNAVAILABLE'}
            data['artifact']=json.loads(data['artifact']) if data['artifact'] else None
            work=[dict(r) for r in c.execute('SELECT w.id,w.tier,w.state,w.result,w.error FROM work w JOIN interests i ON i.work_id=w.id WHERE i.job_id=? AND w.id>? ORDER BY w.id LIMIT ?',(jid,after,limit+1))]
            data['work_truncated']=len(work)>limit;work=work[:limit]
            data['next_work_cursor']=work[-1]['id'] if data['work_truncated'] else None
            for w in work:
                if w['result']:w['result']=json.loads(w['result'])
            data['work']=work;data['source_freshness']='CURRENT_MATCH'
            data['live_work_counts']=[dict(x) for x in c.execute('SELECT w.tier,w.state,count(*) AS count FROM work w JOIN interests i ON i.work_id=w.id WHERE i.job_id=? GROUP BY w.tier,w.state',(jid,))]
            data['remaining_game_budget']=max(0,self.limits.game_budget-data['spent']-data['reserved'])
            return data

    def status(self):
        with self.connection() as c:
            return {'registered_profiles':c.execute('SELECT count(*) FROM profiles').fetchone()[0],
                    'running':[dict(x) for x in c.execute("SELECT id,tier,lease,attempts FROM work WHERE state='RUNNING' LIMIT 1")],
                    'recent_failures':[dict(x) for x in c.execute("SELECT id,error FROM work WHERE error IS NOT NULL ORDER BY created DESC LIMIT 8")],
                    'jobs':dict(c.execute('SELECT state,count(*) FROM jobs GROUP BY state').fetchall()),
                    'work':dict(c.execute('SELECT state,count(*) FROM work GROUP BY state').fetchall()),
                    'budget':[dict(x) for x in c.execute('SELECT id,budget,spent,reserved FROM batches LIMIT 100')],
                    'outbox_pending':c.execute('SELECT count(*) FROM outbox WHERE sent=0').fetchone()[0],
                    'storage_bytes':self.sizes(),'engine_dispatch_default':False}

    def advance(self,jid):
        """Publish bounded phase progress or create the next selective tier."""
        with self.connection() as c:
            job=c.execute('SELECT j.*,p.pgn,p.artifact FROM jobs j JOIN payloads p ON p.job_id=j.id WHERE j.id=?',(jid,)).fetchone()
            if job is None or job['state'] not in ('PREPARED','ACTIVE'):return False
            rows=[dict(x) for x in c.execute('SELECT w.* FROM work w JOIN interests i ON i.work_id=w.id WHERE i.job_id=? AND i.active=1',(jid,))]
            batch=c.execute('SELECT * FROM batches WHERE id=?',(job['batch_id'],)).fetchone()
            profile=c.execute('SELECT config,simulation FROM profiles WHERE id=?',(batch['profile'],)).fetchone()
        bad=self._current(job)
        if bad:
            self.cancel(jid)
            with self.connection(True) as c:c.execute('UPDATE jobs SET state=?,reason=? WHERE id=?',(bad,bad,jid))
            self.redact_missing();return True
        if any(x['state'] in ('PENDING','RUNNING','RETRY_WAIT') for x in rows):return False
        if profile is None:return False
        artifact=json.loads(job['artifact']); observations=artifact['observations']
        evidence={}
        for row in rows:
            if row['result']:
                result=json.loads(row['result'])
                if 'evaluation' not in result:continue
                e=result['evaluation']
                evidence[(row['tier'],len(e['context']['moves']))]=(e,result)
        for d in observations:
            base=3*d['check']+2*d['capture']+min(4,d['material_change'])+3*d['hanging_heuristic']+3*d['anchor']
            before=evidence.get(('screen',d['ply']-1));after=evidence.get(('screen',d['ply']))
            d['score']=base+2*bool(d.get('advisory_pv_disagreement'))
            if before and after:
                a,b=before[0],after[0]
                if a['config']==b['config'] and a['achieved_depth']==b['achieved_depth'] and a['context']['root_history_complete'] and b['context']['root_history_complete']:
                    x,y=a['pvs'][0],b['pvs'][0]
                    if x['kind']==y['kind']=='cp':
                        drop=max(0,(1 if d['color']=='white' else -1)*(x['cp']-y['cp']))
                        d['provisional_score_drop_cp']=drop;d['score']+=min(30,drop/10)
                    elif x['kind']!=y['kind']:d['score']+=25;d['mate_transition_hypothesis']=True
                    if x['moves'] and x['moves'][0]!=d['move']:d['score']+=8;d['pv_disagreement_hypothesis']=True
        candidates=shortlist(observations,self.limits.shortlist);artifact['candidates']=candidates
        artifact['coverage']['screened_contexts']=sum(x['tier']=='screen' and bool(x['result']) for x in rows)
        artifact['coverage']['target_met_contexts']=sum(x['state']=='SUCCEEDED' for x in rows)
        artifact['coverage']['limited_contexts']=sum(x['state']=='LIMIT_REACHED' for x in rows)
        artifact['coverage']['failed_contexts']=sum(x['state']=='FAILED' for x in rows)
        paired=0;verified=0
        for d in candidates:
            a=evidence.get(('deep',d['ply']-1));b=evidence.get(('deep',d['ply']))
            if a and b and all(x[0]['achieved_depth']>=16 and x[0]['context']['root_history_complete'] for x in (a,b)):
                paired+=1
                if not a[1].get('simulation') and not b[1].get('simulation'):verified+=1
        artifact['verification'].update(trusted_context_deep_pairs=paired,engine_verified_decisions=verified)
        next_tier={'prepared':'medium','medium':'deep'}.get(job['phase'])
        frames=[]
        if next_tier:frames,_,_=replay(job['pgn'])
        with self.connection(True) as c:
            fresh=c.execute('SELECT state,phase,generation FROM jobs WHERE id=?',(jid,)).fetchone()
            if fresh['state'] not in ('PREPARED','ACTIVE') or fresh['phase']!=job['phase'] or fresh['generation']!=job['generation']:return False
            new_ids=[]
            if next_tier:
                config=SearchConfig.model_validate_json(profile[0])
                admission=list(c.execute("SELECT count(*),sum(state IN ('PENDING','RUNNING','RETRY_WAIT')) FROM work").fetchone())
                # Diversity selection is preserved when reducing eight candidates to four.
                chosen=shortlist(candidates,self.limits.deepen_decisions)
                for d in chosen:
                    for index in (d['ply']-1,d['ply']):
                        wid=self._add_work(c,job,frames[index],tier_config(config,next_tier),next_tier,admission)
                        if wid:new_ids.append(wid)
                artifact['work_ids']=list(dict.fromkeys(artifact['work_ids']+new_ids))
            self._publish(c,jid,artifact)
            if new_ids:c.execute("UPDATE jobs SET phase=?,state='ACTIVE' WHERE id=?",(next_tier,jid))
            else:
                state='PARTIAL' if artifact['coverage'].get('limited_contexts') or artifact['coverage'].get('failed_contexts') or not paired else 'COMPLETED'
                c.execute('UPDATE jobs SET phase=?,state=?,reason=? WHERE id=?',('published',state,'BOUNDED_PLAN; unsearched positions remain',jid))
        return True

    def flush_outbox(self,cache,max_rows=8):
        if not 1<=max_rows<=8:raise ValueError('Outbox batch 1..8')
        with self.connection() as c:
            rows=c.execute('SELECT o.work_id,w.result FROM outbox o JOIN work w ON w.id=o.work_id WHERE o.sent=0 LIMIT ?',(max_rows,)).fetchall()
        sent=0
        for row in rows:
            try:
                cache.ingest(Evaluation.model_validate(json.loads(row['result'])['evaluation']))
                with self.connection(True) as c:c.execute('UPDATE outbox SET sent=1,error=NULL WHERE work_id=?',(row['work_id'],))
                sent+=1
            except (ValueError,CacheUnavailable,sqlite3.DatabaseError) as exc:
                with self.connection(True) as c:c.execute('UPDATE outbox SET error=? WHERE work_id=?',(type(exc).__name__,row['work_id']))
        return sent

    def reconcile(self,limit=100):
        with self.connection() as c:rows=c.execute("SELECT * FROM jobs WHERE state IN ('PENDING','PREPARING','PREPARED','ACTIVE') ORDER BY checked,created LIMIT ?",(limit,)).fetchall()
        changed=0
        states=self._source_states(rows)
        with self.connection(True) as c:
            c.executemany('UPDATE jobs SET checked=? WHERE id=?',[(self.clock(),x['id']) for x in rows])
        for row in rows:
            bad=states.get(row['id'])
            if bad:
                self.cancel(row['id'])
                with self.connection(True) as c:c.execute('UPDATE jobs SET state=?,reason=? WHERE id=?',(bad,bad,row['id']))
                with self.connection(True) as c:c.execute('UPDATE payloads SET pgn=?,artifact=NULL,previous_artifact=NULL WHERE job_id=?',('',row['id']))
                changed+=1
        return changed

    def retry(self,jid):
        with self.connection(True) as c:
            job=c.execute('SELECT j.*,p.pgn,p.artifact FROM jobs j JOIN payloads p ON p.job_id=j.id WHERE j.id=?',(jid,)).fetchone()
            if job is None:raise KeyError(jid)
            if job['state'] not in ('PARTIAL','FAILED'):raise ValueError('Only partial/failed jobs may retry remaining budget')
            c.execute("UPDATE jobs SET state='PREPARED',reason=NULL,generation=generation+1 WHERE id=?",(jid,))
            # Retry explicitly but do not reset attempts, settled costs, or batch/game budgets.
            c.execute("UPDATE work SET state='RETRY_WAIT',attempts=0,generation=generation+1,not_before=? WHERE state='FAILED' AND id IN(SELECT work_id FROM interests WHERE job_id=? AND active=1)",(self.clock(),jid))

    def maintain(self,max_rows=8):
        if not 1<=max_rows<=8:raise ValueError('Maintenance batch 1..8')
        max_rows=1  # one bounded 4 MiB source/artifact payload per maintenance transaction
        with self.connection(True) as c:
            c.execute('DELETE FROM request_keys WHERE key IN(SELECT key FROM request_keys WHERE created<? LIMIT 8)',(self.clock()-self.limits.retention_days*86400,))
            rows=c.execute("SELECT id FROM jobs WHERE state NOT IN ('PENDING','PREPARING','PREPARED','ACTIVE') AND created<? AND reserved=0 LIMIT ?",(self.clock()-self.limits.retention_days*86400,max_rows)).fetchall()
            for row in rows:
                c.execute('DELETE FROM attempts WHERE job_id=? AND charged IS NOT NULL',(row[0],))
                c.execute('UPDATE work SET sponsor=NULL WHERE sponsor=? AND state!=?',(row[0],'RUNNING'))
                c.execute('DELETE FROM jobs WHERE id=?',(row[0],))
            orphan=[r[0] for r in c.execute("SELECT id FROM work WHERE state!='RUNNING' AND NOT EXISTS(SELECT 1 FROM interests WHERE work_id=work.id) AND NOT EXISTS(SELECT 1 FROM attempts WHERE work_id=work.id) LIMIT ?",(max_rows,))]
            for wid in orphan:
                c.execute('DELETE FROM outbox WHERE work_id=?',(wid,));c.execute('DELETE FROM work WHERE id=?',(wid,))
            c.execute('DELETE FROM contexts WHERE id IN(SELECT id FROM contexts WHERE NOT EXISTS(SELECT 1 FROM work WHERE context_id=contexts.id) LIMIT ?)',(max_rows,))
            return {'deleted_jobs':len(rows),'deleted_work':len(orphan),'batch_limit':max_rows,'aggregate_batch_costs_retained':True}

    def _source_states(self,jobs):
        if not jobs:return {}
        c=sqlite3.connect(self.library.as_uri()+'?mode=ro',uri=True,timeout=.25)
        try:
            c.execute('PRAGMA query_only=ON');c.execute('BEGIN');result={}
            for offset in range(0,len(jobs),100):
                chunk=jobs[offset:offset+100];ids=[j['game_id'] for j in chunk]
                rows={r[0]:(hashlib.sha256(r[1].encode()).hexdigest(),r[2]) for r in c.execute('SELECT game_id,pgn,fingerprint FROM games WHERE game_id IN ('+','.join('?' for _ in ids)+')',ids)}
                for job in chunk:
                    current=rows.get(job['game_id'])
                    if current is None:result[job['id']]='SOURCE_MISSING'
                    elif current!=(job['pgn_hash'],job['fingerprint']):result[job['id']]='STALE_SOURCE'
            return result
        finally:c.close()

    def finish_budget(self,batch_id):
        with self.connection(True) as c:
            c.execute("UPDATE jobs SET state='PARTIAL',reason='BATCH_BUDGET_EXHAUSTED' WHERE batch_id=? AND state IN ('PREPARED','ACTIVE') AND reserved=0",(batch_id,))
            c.execute("UPDATE interests SET active=0 WHERE job_id IN(SELECT id FROM jobs WHERE batch_id=? AND state='PARTIAL')",(batch_id,))
            c.execute("UPDATE work SET state='CANCELLED' WHERE state IN ('PENDING','RETRY_WAIT') AND NOT EXISTS(SELECT 1 FROM interests WHERE work_id=work.id AND active=1)")

    def reconcile_work(self,wid):
        with self.connection() as c:jobs=c.execute('SELECT j.* FROM jobs j JOIN interests i ON i.job_id=j.id WHERE i.work_id=? AND i.active=1',(wid,)).fetchall()
        bad=self._source_states(jobs)
        with self.connection(True) as c:
            for jid,state in bad.items():
                c.execute('UPDATE interests SET active=0 WHERE job_id=?',(jid,))
                c.execute('UPDATE jobs SET state=?,reason=? WHERE id=?',(state,state,jid))
            return c.execute('SELECT count(*) FROM interests WHERE work_id=? AND active=1',(wid,)).fetchone()[0]

    def redact_missing(self):
        """Bounded private-source cleanup, independent of the 90-day audit retention."""
        with self.connection(True) as c:
            row=c.execute("SELECT p.job_id FROM payloads p JOIN jobs j ON j.id=p.job_id WHERE j.state IN ('SOURCE_MISSING','STALE_SOURCE') AND p.pgn!='' LIMIT 1").fetchone()
            if row:c.execute('UPDATE payloads SET pgn=?,artifact=NULL,previous_artifact=NULL WHERE job_id=?',('',row[0]))
            return int(row is not None)

    def mark_unfunded(self):
        """Finish only work which cannot fit remaining budgets; never replenish credits."""
        with self.connection(True) as c:
            if c.execute("SELECT 1 FROM work WHERE state='RUNNING'").fetchone():return 0
            rows=c.execute('''SELECT j.id,min(json_extract(w.config,'$.time_seconds')+6) AS needed,
                 min(?-j.spent-j.reserved,b.budget-b.spent-b.reserved) AS remaining
                 FROM jobs j JOIN batches b ON b.id=j.batch_id JOIN interests i ON i.job_id=j.id AND i.active=1
                 JOIN work w ON w.id=i.work_id WHERE j.state IN ('PREPARED','ACTIVE')
                 AND w.state IN ('PENDING','RETRY_WAIT') AND w.not_before<=?
                 GROUP BY j.id HAVING needed>remaining LIMIT 100''',(self.limits.game_budget,self.clock())).fetchall()
            for row in rows:
                c.execute("UPDATE jobs SET state='PARTIAL',reason='INSUFFICIENT_REMAINING_BUDGET' WHERE id=?",(row['id'],))
                c.execute('UPDATE interests SET active=0 WHERE job_id=?',(row['id'],))
            c.execute("UPDATE work SET state='CANCELLED' WHERE state IN ('PENDING','RETRY_WAIT') AND NOT EXISTS(SELECT 1 FROM interests WHERE work_id=work.id AND active=1)")
            return len(rows)

    def touch_refresh(self,ids):
        with self.connection(True) as c:
            c.execute('UPDATE progressive_meta SET refresh_tick=refresh_tick+1')
            tick=c.execute('SELECT refresh_tick FROM progressive_meta').fetchone()[0]
            c.executemany('UPDATE jobs SET checked=? WHERE id=?',[(tick,jid) for jid in ids])
