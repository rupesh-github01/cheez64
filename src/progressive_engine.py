"""Bounded UCI protocol adapter and explicit host-safety capability.

No default executable. Safety attestation is issued only by fake-process probes.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from .evaluation_models import Evaluation, PositionContext, PV


class EngineSafetyError(RuntimeError):
    def __init__(self,message,diagnostics=None):
        self.diagnostics=diagnostics or []
        super().__init__(message+(' '+json.dumps(self.diagnostics,separators=(',',':')) if self.diagnostics else ''))
class EngineExited(RuntimeError): pass


@dataclass(frozen=True)
class SafetyProof:
    code_hash: str
    platform: str
    passed: bool


def code_hash():
    h=hashlib.sha256()
    for name in ('progressive_engine.py','progressive_engine_guard.py','progressive_resources.py','progressive_worker.py'):
        h.update(Path(__file__).with_name(name).read_bytes())
    return h.hexdigest()


class UCIProcess:
    def __init__(self,command,lock_path,proof=None,probe=False):
        if os.name!='posix' or sys.platform not in ('darwin','linux'):
            raise EngineSafetyError('Unsupported host; dispatch disabled')
        if not probe and (proof is None or not proof.passed or proof.code_hash!=code_hash() or proof.platform!=sys.platform):
            raise EngineSafetyError('No current host-safety proof; dispatch disabled')
        self.process=subprocess.Popen([sys.executable,'-m','src.progressive_engine_guard',str(lock_path),json.dumps(command)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,bufsize=0)
        self.buffer=b'';self.diagnostic_buffer=b'';self.diagnostic_bytes=0
        self.diagnostics=[{'event':'HANDSHAKE_START','deadline_ms':2000}]
        self.selector=selectors.DefaultSelector()
        self.selector.register(self.process.stdout,selectors.EVENT_READ,'stdout')
        self.selector.register(self.process.stderr,selectors.EVENT_READ,'diagnostics')
        self.name=None; self.closed=False
        try:
            self.send('uci'); lines=self.until('uciok',2)
            names=[x[8:] for x in lines if x.startswith('id name ')]
            if not names or 'info string guardian-ready' not in lines: raise EngineSafetyError('Missing UCI identity/resource guard')
            self.name=names[0]
            self.option_names=[];self.managed_options=set()
            for line in lines:
                if line.startswith('option name ') and ' type ' in line and ' type button' not in line:
                    name=line[12:line.index(' type ')]
                    if name not in ('MultiPV','UCI_Chess960','Ponder'):self.option_names.append(name)
                    else:self.managed_options.add(name)
        except (EngineExited,TimeoutError,OSError,EngineSafetyError) as exc:
            self.close()
            events={x['event'] for x in self.diagnostics}
            reason=next((name for name in ('LOCK_BUSY','LOCK_UNSAFE','RSS_REFUSED','RSS_UNAVAILABLE','OS_ERROR','GUARDIAN_ERROR') if name in events),None)
            reason=reason or ('HANDSHAKE_TIMEOUT' if isinstance(exc,TimeoutError) else 'STARTUP_FAILED')
            raise EngineSafetyError(reason,self.diagnostics) from exc
        except BaseException:self.close();raise

    def _diagnostic_data(self,data):
        self.diagnostic_bytes+=len(data)
        if self.diagnostic_bytes>8192:return
        self.diagnostic_buffer+=data
        while b'\n' in self.diagnostic_buffer:
            raw,self.diagnostic_buffer=self.diagnostic_buffer.split(b'\n',1)
            try:
                event=json.loads(raw)
                allowed={'GUARDIAN_START','LOCK_OPEN','LOCK_UNSAFE','LOCK_BUSY','LOCK_ACQUIRED','CHILD_SPAWN','CHILD_SPAWNED',
                         'RSS_UNAVAILABLE','RSS_READY','RSS_REFUSED','PARENT_EOF','CHILD_EOF','RELAY_LIMIT','RELAY_BROKEN_PIPE',
                         'CHILD_EXIT_OBSERVED','OS_ERROR','GUARDIAN_ERROR','CHILD_STOP','CHILD_KILL','CHILD_REAPED','GUARDIAN_CLOSED'}
                if isinstance(event,dict) and event.get('event') in allowed and len(self.diagnostics)<34:
                    self.diagnostics.append({k:v for k,v in event.items() if k=='event' or
                                             (k in ('pid','errno','rss','returncode','signal') and type(v) is int)})
            except (ValueError,TypeError):pass

    def send(self,line):
        if '\n' in line or '\r' in line:raise ValueError('UCI line injection rejected')
        data=(line+'\n').encode()
        if len(data)>16384: raise ValueError('UCI command too large')
        # Commands are bounded; nonblocking pipe writes prevent a nonreading child hang.
        fd=self.process.stdin.fileno(); os.set_blocking(fd,False); deadline=time.monotonic()+.25
        while data:
            try: n=os.write(fd,data); data=data[n:]
            except BlockingIOError:
                if time.monotonic()>deadline: raise EngineSafetyError('UCI pipe timeout')
                time.sleep(.002)

    def until(self,marker,timeout):
        deadline=time.monotonic()+timeout; lines=[]
        while time.monotonic()<deadline:
            while b'\n' in self.buffer:
                raw,self.buffer=self.buffer.split(b'\n',1); line=raw.decode(errors='strict').strip(); lines.append(line)
                if line==marker or line.startswith(marker+' '): return lines
                if len(lines)>4096: raise EngineSafetyError('UCI output flood')
            for key,_ in self.selector.select(max(0,min(.05,deadline-time.monotonic()))):
                part=os.read(key.fileobj.fileno(),4096)
                if key.data=='diagnostics':
                    if part:self._diagnostic_data(part)
                    else:self.selector.unregister(key.fileobj)
                    continue
                if not part:raise EngineExited('Owned engine exited')
                self.buffer+=part
                if len(self.buffer)>65536:raise EngineSafetyError('UCI output too large')
        raise TimeoutError('UCI deadline exceeded')

    def search(self,context,config):
        context=PositionContext.model_validate(context.model_dump())
        if config.applied_options.get('Threads')!=1 or config.applied_options.get('Hash')!=64:
            raise EngineSafetyError('Initial resource admission requires Threads=1, Hash=64')
        if self.name!=config.source.engine_name or self.name!=config.source.engine_version:
            raise EngineSafetyError('UCI identity does not match registered profile')
        if set(config.applied_options)!=set(self.option_names):raise EngineSafetyError('Incomplete applied/default option profile')
        self.send('ucinewgame'); self.send('setoption name Clear Hash')
        for name,value in config.applied_options.items():
            if not isinstance(value,(str,int,bool)) or len(str(value))>200:raise EngineSafetyError('Unsupported applied option value')
            value=str(value).lower() if isinstance(value,bool) else value
            self.send(f'setoption name {name} value {value}')
        for name,value in (('MultiPV','1'),('UCI_Chess960','false'),('Ponder','false')):
            if name in self.managed_options:self.send(f'setoption name {name} value {value}')
        self.send('isready'); self.until('readyok',1)
        command='position fen '+' '.join(context.root_fen.split())+' moves '+' '.join(context.moves) if context.root_fen else 'position fen '+context.fen
        self.send(command)
        started=time.monotonic(); self.send(f'go depth {config.depth} movetime {max(1,int(config.time_seconds*1000))}')
        try: lines=self.until('bestmove',config.time_seconds+.5)
        except BaseException: self.close(); raise
        latest=None
        for line in lines:
            fields=line.split()
            if fields[:1]!=['info'] or 'score' not in fields or 'pv' not in fields: continue
            if 'lowerbound' in fields or 'upperbound' in fields: continue
            try:
                si=fields.index('score'); kind=fields[si+1]; value=int(fields[si+2]); depth=int(fields[fields.index('depth')+1])
                import chess
                # UCI scores are side-to-move; canonical cache scores are White-relative.
                value*=1 if chess.Board(context.fen).turn else -1
                pv=PV(kind=kind,**{kind:value},moves=fields[fields.index('pv')+1:])
                nodes=int(fields[fields.index('nodes')+1]) if 'nodes' in fields else None
                latest=Evaluation(context=context,config=config,achieved_depth=depth,nodes=nodes,
                                  elapsed_seconds=time.monotonic()-started,pvs=[pv])
            except (ValueError,IndexError): continue
        if latest is None: raise ValueError('NO_EXACT_SCORE')
        return latest

    def close(self):
        if self.closed: return
        self.closed=True
        try:
            try: self.process.stdin.close()
            except OSError: pass
            # EOF asks guardian to stop, kill and reap its owned engine; do not kill guardian early.
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            # Keep safety closed; child retains lifetime lock if guardian must be killed.
            self.process.kill(); self.process.wait(timeout=2)
            raise EngineSafetyError('Guardian cleanup failed; slot must remain blocked')
        finally:
            os.set_blocking(self.process.stderr.fileno(),False)
            # Drain only after guardian death, under a fixed total byte ceiling.
            while self.diagnostic_bytes<=8192:
                try:data=os.read(self.process.stderr.fileno(),4096)
                except BlockingIOError:break
                if not data:break
                self._diagnostic_data(data)
            self.diagnostics.append({'event':'GUARDIAN_EXIT','returncode':self.process.returncode})
            self.selector.close();self.process.stdout.close();self.process.stderr.close()


def prove_host_safety():
    """Execute only the bundled benign fake UCI, in temporary directories.

    Proof covers busy slot, normal/stubborn cleanup, timeout and parent death.
    No user-supplied binary is used for the probe.
    """
    import tempfile
    fake=Path(__file__).resolve().parents[1]/'tests/data/fake_progressive_uci.py'
    if not fake.is_file(): raise EngineSafetyError('Bundled safety fixture unavailable')
    with tempfile.TemporaryDirectory(prefix='progressive-safety-') as directory:
        lock=Path(directory)/'slot.lock'
        for mode in ('normal','stubborn','hang'):
            engine=UCIProcess([sys.executable,str(fake),mode],lock,probe=True)
            try:
                try:
                    contender=UCIProcess([sys.executable,str(fake)],lock,probe=True)
                except EngineSafetyError: pass
                else:
                    contender.close(); raise EngineSafetyError('Slot oversubscribed')
                if mode=='hang':
                    engine.send('go depth 10');
                    try: engine.until('bestmove',.02)
                    except TimeoutError: pass
                    else: raise EngineSafetyError('Timeout probe failed')
            finally: engine.close()
            again=UCIProcess([sys.executable,str(fake)],lock,probe=True); again.close()
        # A killed worker leaves the guardian alive; EOF must stop/kill/reap its child.
        marker=Path(directory)/'owned.json'
        script='from src.progressive_engine import UCIProcess; import sys,time; e=UCIProcess([sys.executable,sys.argv[1],"stubborn",sys.argv[3]],sys.argv[2],probe=True); print("READY",flush=True); time.sleep(60)'
        parent=subprocess.Popen([sys.executable,'-c',script,str(fake),str(lock),str(marker)],stdout=subprocess.PIPE,text=True)
        try:
            sel=selectors.DefaultSelector();sel.register(parent.stdout,selectors.EVENT_READ)
            if not sel.select(3) or parent.stdout.readline().strip()!='READY': raise EngineSafetyError('Parent probe startup failed')
            parent.kill();parent.wait(timeout=2)
            # Busy until guardian finishes cleanup; never forcibly free a slot.
            deadline=time.monotonic()+3
            while True:
                try: again=UCIProcess([sys.executable,str(fake)],lock,probe=True);again.close();break
                except EngineSafetyError:
                    if time.monotonic()>deadline: raise EngineSafetyError('Orphan slot not released after cleanup')
                    time.sleep(.02)
        finally:
            if parent.poll() is None: parent.kill();parent.wait(timeout=2)
            parent.stdout.close()
        marker=Path(directory)/'hard-owned.json'
        engine=UCIProcess([sys.executable,str(fake),'hang',str(marker)],lock,probe=True)
        engine.send('go depth 10');engine.until('info',1)
        owned=json.loads(marker.read_text());engine.process.kill();engine.process.wait(timeout=2)
        try:
            try: contender=UCIProcess([sys.executable,str(fake)],lock,probe=True)
            except EngineSafetyError: pass
            else:
                contender.close();raise EngineSafetyError('Live orphan did not retain slot')
        finally:
            os.killpg(owned['pgid'],signal.SIGKILL)
            engine.closed=True;engine.selector.close();engine.process.stdin.close();engine.process.stdout.close();engine.process.stderr.close()
        deadline=time.monotonic()+2
        while True:
            try: again=UCIProcess([sys.executable,str(fake)],lock,probe=True);again.close();break
            except EngineSafetyError:
                if time.monotonic()>deadline:raise
                time.sleep(.02)
    return SafetyProof(code_hash(),sys.platform,True)
