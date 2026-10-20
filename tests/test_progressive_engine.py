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
        engine.closed=True;engine.selector.close();engine.process.stdin.close();engine.process.stdout.close()
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
