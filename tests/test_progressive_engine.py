import json
import os
from pathlib import Path
import signal
import sys
import time
import pytest
from src.progressive_engine import UCIProcess,prove_host_safety,EngineSafetyError
from src.evaluation_models import PositionContext,SearchConfig,Source
import chess

FAKE=Path(__file__).parent/'data/fake_progressive_uci.py'


def test_dispatch_closed_without_proof(tmp_path):
    with pytest.raises(EngineSafetyError): UCIProcess([sys.executable,str(FAKE)],tmp_path/'lock')


def test_fake_host_proof_and_typed_search(tmp_path):
    proof=prove_host_safety(); assert proof.passed
    engine=UCIProcess([sys.executable,str(FAKE)],tmp_path/'lock',proof)
    cfg=SearchConfig(source=Source(kind='local_stockfish',engine_name='ProgressiveFixture-v1',engine_version='ProgressiveFixture-v1',engine_build='fake',nnue='fake',provenance='fake-only'),
                     applied_options={'Threads':1,'Hash':64},applied_options_complete=True,limit_kind='combined',depth=10,time_seconds=.1)
    try:
        e=engine.search(PositionContext(fen=chess.STARTING_FEN,root_fen=chess.STARTING_FEN,root_history_complete=True),cfg)
        assert e.pvs[0].cp==23 and e.achieved_depth==18
    finally: engine.close()
    assert engine.process.poll() is not None


def test_guardian_hard_death_blocks_until_owned_child_dies(tmp_path):
    marker=tmp_path/'owned.json';lock=tmp_path/'lock'
    engine=UCIProcess([sys.executable,str(FAKE),'hang',str(marker)],lock,probe=True)
    owned=json.loads(marker.read_text())
    engine.send('go depth 10');engine.until('info',1)
    engine.process.kill();engine.process.wait(timeout=2)
    try:
        with pytest.raises(EngineSafetyError): UCIProcess([sys.executable,str(FAKE)],lock,probe=True)
    finally:
        # Known live child identity came from our fixture. Never kill a reconstructed stale PID.
        os.killpg(owned['pgid'],signal.SIGKILL)
        engine.closed=True;engine.selector.close();engine.process.stdin.close();engine.process.stdout.close();engine.process.stderr.close()
    deadline=time.monotonic()+2
    while True:
        try: again=UCIProcess([sys.executable,str(FAKE)],lock,probe=True);again.close();break
        except EngineSafetyError:
            if time.monotonic()>deadline: raise
            time.sleep(.02)


def test_black_perspective_and_full_history(tmp_path):
    proof=prove_host_safety();marker=tmp_path/'trace'
    engine=UCIProcess([sys.executable,str(FAKE),'normal',str(marker)],tmp_path/'lock',proof)
    board=chess.Board();board.push_uci('e2e4')
    cfg=SearchConfig(source=Source(kind='local_stockfish',engine_name='ProgressiveFixture-v1',engine_version='ProgressiveFixture-v1',engine_build='fake',nnue='fake',provenance='fake-only'),
                     applied_options={'Threads':1,'Hash':64},applied_options_complete=True,limit_kind='combined',depth=10,time_seconds=.1)
    try:
        result=engine.search(PositionContext(fen=board.fen(),root_fen=chess.STARTING_FEN,moves=['e2e4'],root_history_complete=True),cfg)
        assert result.pvs[0].cp==-23
        assert 'moves e2e4' in Path(str(marker)+'.trace').read_text()
        options=Path(str(marker)+'.options').read_text()
        assert 'UCI_Chess960 value false' in options and 'Ponder value false' in options and 'MultiPV value 1' in options
    finally:engine.close()


def test_native_owned_resource_accounting_and_bad_proof(tmp_path):
    from src.progressive_resources import resident_bytes
    from src.progressive_engine import SafetyProof
    assert resident_bytes(os.getpid())>0
    with pytest.raises(EngineSafetyError):UCIProcess([sys.executable,str(FAKE)],tmp_path/'lock',SafetyProof('outdated',sys.platform,True))


def test_fake_crash_is_bounded_and_releases_owned_slot(tmp_path):
    from src.progressive_engine import EngineExited
    proof=prove_host_safety();engine=UCIProcess([sys.executable,str(FAKE),'crash'],tmp_path/'lock',proof)
    cfg=SearchConfig(source=Source(kind='local_stockfish',engine_name='ProgressiveFixture-v1',engine_version='ProgressiveFixture-v1',engine_build='fake',nnue='fake',provenance='fake-only'),
                     applied_options={'Threads':1,'Hash':64},applied_options_complete=True,limit_kind='combined',depth=10,time_seconds=.1)
    with pytest.raises(EngineExited):engine.search(PositionContext(fen=chess.STARTING_FEN),cfg)
    assert engine.closed and engine.process.poll() is not None
    again=UCIProcess([sys.executable,str(FAKE)],tmp_path/'lock',proof);again.close()


def test_proved_adapter_binary_nnue_and_option_attestation(tmp_path):
    from src.progressive_worker import ProvedUCIAdapter
    proof=prove_host_safety();adapter=ProvedUCIAdapter([sys.executable,str(FAKE)],tmp_path/'lock',proof,exclusive_host=True)
    cfg=SearchConfig(source=Source(kind='local_stockfish',engine_name='ProgressiveFixture-v1',engine_version='ProgressiveFixture-v1',engine_build=adapter.build,nnue='embedded:'+adapter.build,provenance='pcis-progressive:v1:reset-per-context:fake-test'),
                     applied_options={'Threads':1,'Hash':64},applied_options_complete=True,limit_kind='combined',depth=10,time_seconds=.1)
    try:
        data=cfg.model_dump();data['source']['nnue']='embedded:sha256:'+'0'*64
        with pytest.raises(EngineSafetyError):adapter.search(PositionContext(fen=chess.STARTING_FEN),SearchConfig.model_validate(data))
        assert adapter.search(PositionContext(fen=chess.STARTING_FEN),cfg).pvs[0].cp==23
    finally:adapter.close()


def test_startup_diagnostics_distinguish_spawn_exit_timeout_and_lock(tmp_path):
    import subprocess
    secret='do-not-expose-startup-secret'
    with pytest.raises(EngineSafetyError,match='OS_ERROR') as error:
        UCIProcess([str(tmp_path/secret)],tmp_path/'missing-lock',probe=True)
    assert secret not in str(error.value)
    assert any(x.get('errno')==2 for x in error.value.diagnostics)
    assert any(x['event']=='LOCK_ACQUIRED' for x in error.value.diagnostics)
    engine=UCIProcess([sys.executable,str(FAKE)],tmp_path/'busy-lock',probe=True)
    try:
        with pytest.raises(EngineSafetyError,match='LOCK_BUSY'):
            UCIProcess([sys.executable,str(FAKE)],tmp_path/'busy-lock',probe=True)
    finally:engine.close()
    for command,expected in (([sys.executable,'-c','import sys; sys.exit(42)'],'STARTUP_FAILED'),
                             ([sys.executable,'-c','import time; time.sleep(30)'],'HANDSHAKE_TIMEOUT')):
        with pytest.raises(EngineSafetyError,match=expected) as error:
            UCIProcess(command,tmp_path/'failure-lock',probe=True)
        assert any(x['event']=='CHILD_REAPED' for x in error.value.diagnostics)
        if expected=='STARTUP_FAILED':assert any(x.get('returncode')==42 for x in error.value.diagnostics)
        assert len(str(error.value))<8192


def test_guardian_resource_diagnostics_fail_closed(tmp_path):
    import subprocess
    for patch,event in (("lambda pid: 600*1024**2",'RSS_REFUSED'),
                        ("lambda pid: (_ for _ in ()).throw(OSError(1,'private details'))",'RSS_UNAVAILABLE')):
        code=f'import src.progressive_engine_guard as g; g.resident_bytes={patch}; raise SystemExit(g.main())'
        run=subprocess.run([sys.executable,'-c',code,str(tmp_path/'rss-lock'),json.dumps([sys.executable,str(FAKE)])],
                           input=b'uci\n',capture_output=True,timeout=5)
        events=[json.loads(line) for line in run.stderr.splitlines()]
        assert any(x['event']==event for x in events)
        assert any(x['event']=='CHILD_REAPED' for x in events)
        assert b'private details' not in run.stderr
        assert run.returncode==78


def test_startup_signal_and_diagnostic_sanitization(tmp_path):
    with pytest.raises(EngineSafetyError) as error:
        UCIProcess([sys.executable,'-c','import os,signal; os.kill(os.getpid(),signal.SIGTERM)'],tmp_path/'signal-lock',probe=True)
    assert any(x.get('signal')==signal.SIGTERM for x in error.value.diagnostics)
    engine=UCIProcess([sys.executable,str(FAKE)],tmp_path/'sanitized-lock',probe=True)
    try:
        engine._diagnostic_data(b'{"event":"OS_ERROR","errno":2,"private":"credential-secret"}\n')
        assert 'credential-secret' not in json.dumps(engine.diagnostics)
        for _ in range(100):engine._diagnostic_data(b'{"event":"OS_ERROR","errno":2}\n')
        assert len(engine.diagnostics)<=34
        engine._diagnostic_data(b'x'*20000)
        assert len(engine.diagnostic_buffer)<=8192
    finally:engine.close()


def test_guardian_memory_breakdown_reads_each_role_once(tmp_path):
    import subprocess
    code='''import os
import src.progressive_engine_guard as g
calls=[]
def measure(pid):
    assert pid not in calls
    calls.append(pid)
    return (16 if pid==os.getpid() else 32 if pid==os.getppid() else 520)*1024**2
g.resident_bytes=measure
raise SystemExit(g.main())
'''
    run=subprocess.run([sys.executable,'-c',code,str(tmp_path/'memory-lock'),json.dumps([sys.executable,str(FAKE)])],
                       input=b'uci\n',capture_output=True,timeout=5)
    assert run.returncode==78
    events=[json.loads(x) for x in run.stderr.splitlines()]
    for event in [x for x in events if x['event'] in ('RSS_READY','RSS_REFUSED')]:
        assert len({event['worker_pid'],event['guardian_pid'],event['child_pid']})==3
        assert event['worker_rss']==32*1024**2
        assert event['guardian_rss']==16*1024**2
        assert event['child_rss']==520*1024**2
        assert event['rss']==event['worker_rss']+event['guardian_rss']+event['child_rss']==568*1024**2
        assert event['limit']==512*1024**2
        assert event['elapsed_ms']>=0
    assert any(x['event']=='CHILD_REAPED' for x in events)


def test_memory_diagnostic_fields_survive_bounded_parser(tmp_path):
    engine=UCIProcess([sys.executable,str(FAKE)],tmp_path/'memory-parser-lock',probe=True)
    try:
        event={'event':'RSS_SAMPLE','rss':6,'worker_rss':1,'guardian_rss':2,'child_rss':3,
               'worker_pid':10,'guardian_pid':11,'child_pid':12,'limit':512*1024**2,'elapsed_ms':100,'secret':'must-not-leak'}
        engine._diagnostic_data((json.dumps(event)+'\n').encode())
        assert engine.diagnostics[-1]=={k:v for k,v in event.items() if k!='secret'}
    finally:engine.close()
