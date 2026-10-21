import json
import chess
import pytest
from fastapi.testclient import TestClient
from src.library import GameLibrary
from src.understanding import pgn_digest, understand_game
from src.understanding_models import UnderstandingBudget
from src.evaluation_cache import EvaluationCache
from src.evaluation_models import Evaluation, PV, PositionContext, SearchConfig, Source
from src.progressive_store import ProgressiveStore
from src.progressive_models import Schedule, tier_config
from src.progressive_prepare import replay


@pytest.fixture
def data(tmp_path,monkeypatch):
    path=(tmp_path/'library.db').resolve();text='[White "A"]\n[Black "B"]\n\n1. e4 e5 *'
    with GameLibrary(str(path)) as lib:g,_=lib.add_game(text)
    from api import main
    monkeypatch.setattr(main,'DB_PATH',str(path));monkeypatch.setenv('CHESS_COACH_READ_ONLY','true')
    monkeypatch.setenv('CHESS_COACH_DB',str(path))
    monkeypatch.setenv('CHESS_COACH_PROGRESSIVE_DB',str((tmp_path/'progressive.db').resolve()))
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str((tmp_path/'cache.db').resolve()))
    with TestClient(main.app) as client:yield client,path,g.game_id,text


def config():
    # Synthetic metadata tests admission contracts, never claims engine execution.
    return SearchConfig(source=Source(kind='local_stockfish',engine_name='synthetic admission fixture',engine_version='fixture',
        engine_build='sha256:'+'a'*64,nnue='embedded:sha256:'+'a'*64,
        provenance='pcis-progressive:v1:reset-per-context:synthetic-admission-test'),
        applied_options={'Threads':1,'Hash':64},applied_options_complete=True,limit_kind='combined',depth=10,time_seconds=.1)


def value(ctx,cfg,depth=16):
    b=chess.Board(ctx.fen)
    return Evaluation(context=ctx,config=cfg,achieved_depth=depth,nodes=12,
                      pvs=[PV(kind='cp',cp=0,moves=[next(iter(b.legal_moves)).uci()])])


def accepted_fixture(data,monkeypatch,tmp_path,simulation=False):
    _,path,gid,text=data
    s=ProgressiveStore((tmp_path/'progressive.db').resolve(),path);s.initialize()
    pid=s.register_profile(config(),simulation=simulation)
    jid=s.enqueue(Schedule(game_ids=[gid],profile_id=pid,prepare_only=False,idempotency_key='fixture'))['job_ids'][0]
    s.prepare(jid)
    frames,_,_=replay(text)
    with s.connection(True) as c:
        job=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        for frame in frames[:2]:s._add_work(c,job,frame,tier_config(config(),'deep'),'deep',[0,0])
    for _ in range(10):
        task=s.claim()
        if task is None:break
        ctx=PositionContext.model_validate(task['context']);cfg=SearchConfig.model_validate(task['config'])
        s.complete(task['id'],task['token'],value(ctx,cfg,16 if task['tier']=='deep' else 10),cost=.001,simulation=simulation)
    return s,jid,frames


def test_api_readonly_and_missing_stores(data,monkeypatch,tmp_path):
    client,path,gid,text=data;before=path.read_bytes()
    def forbidden(*a,**k):pytest.fail('writer/engine/provider invoked')
    monkeypatch.setattr(GameLibrary,'_init_db',forbidden)
    import src.progressive_engine,src.progressive_store,httpx
    monkeypatch.setattr(src.progressive_engine,'UCIProcess',forbidden)
    for method in ('initialize','enqueue','prepare','advance','claim','flush_outbox'):
        monkeypatch.setattr(src.progressive_store.ProgressiveStore,method,forbidden)
    monkeypatch.setattr(httpx,'AsyncClient',forbidden)
    q={'ply':0,'expected_pgn_sha256':pgn_digest(text)}
    response=client.get(f'/api/games/{gid}/understanding',params=q)
    assert response.status_code==200,response.text
    body=response.json();assert body['positions']['before'] is None and body['engine_evidence']['status']=='NOT_REQUESTED'
    body=client.get(f'/api/games/{gid}/understanding',params={**q,'evidence':'accepted_local'}).json()
    assert not body['engine_evidence']['accepted']
    assert path.read_bytes()==before and not (tmp_path/'cache.db').exists() and not (tmp_path/'progressive.db').exists()
    assert client.get(f'/api/games/{gid}/understanding',params={**q,'expected_pgn_sha256':'a'*64}).status_code==409
    assert client.get(f'/api/games/{gid}/understanding',params={**q,'ply':10}).status_code==422
    assert client.get(f'/api/games/{gid}/understanding',params={'ply':0}).status_code==422
    assert client.get('/api/games/absent/understanding',params=q).status_code==404


def test_accepted_evidence_pairing_and_source_scope(data,monkeypatch,tmp_path):
    s,jid,frames=accepted_fixture(data,monkeypatch,tmp_path)
    _,path,gid,text=data;before=s.path.read_bytes()
    r=understand_game(path,gid,1,pgn_digest(text),evidence='accepted_local')
    e=r.engine_evidence
    assert e['status']=='ACCEPTED_NON_SIMULATED' and len(e['accepted'])==2,e
    assert e['score_change_cp_white']==0
    assert all(x['evaluation']['achieved_depth']==16 and x['evaluation']['config']['depth']==18 for x in e['accepted'])
    assert s.path.read_bytes()==before
    # No cross-game accepted evidence, even with the same board/history.
    other=text.replace('[White "A"]','[White "C"]')
    with GameLibrary(str(path)) as lib:g,_=lib.add_game(other)
    r=understand_game(path,g.game_id,1,pgn_digest(other),evidence='accepted_local')
    assert not r.engine_evidence['accepted']


def test_simulation_cache_outbox_cannot_upgrade_provenance(data,monkeypatch,tmp_path):
    s,jid,frames=accepted_fixture(data,monkeypatch,tmp_path,simulation=True)
    cache=EvaluationCache((tmp_path/'cache.db').resolve());cache.initialize()
    s.flush_outbox(cache)
    _,path,gid,text=data
    from src.evaluation_models import digest
    chosen=digest(tier_config(config(),'deep').model_dump())
    r=understand_game(path,gid,1,pgn_digest(text),evidence='accepted_local',config_hash=chosen)
    assert not r.engine_evidence['accepted']
    assert r.engine_evidence['status']=='PRODUCER_UNATTESTED'
    assert any(x['context_compatibility']=='TRUSTED_CONTEXT_MATCH' for x in r.engine_evidence['contexts'])
    # Removing the known simulation result still cannot attest the cache producer.
    with s.connection(True) as c:c.execute('DELETE FROM interests')
    assert not understand_game(path,gid,0,pgn_digest(text),evidence='accepted_local').engine_evidence['accepted']


@pytest.mark.parametrize('mutation,reason', [
    ("UPDATE profiles SET simulation=1",'SIMULATION'),
    ("UPDATE work SET state='RUNNING'",'NO_ACCEPTED_PUBLICATION'),
    ("UPDATE attempts SET uncertain=1",'SETTLEMENT_UNATTESTED'),
])
def test_admission_rejects_unsafe_metadata(data,monkeypatch,tmp_path,mutation,reason):
    s,_,_=accepted_fixture(data,monkeypatch,tmp_path)
    with s.connection(True) as c:c.execute(mutation)
    _,path,gid,text=data
    r=understand_game(path,gid,0,pgn_digest(text),evidence='accepted_local')
    assert not r.engine_evidence['accepted']
    assert reason in r.engine_evidence['contexts'][0].get('rejected_reasons',[])


def test_reuse_wrapper_illegal_pv_config_and_quality_fail_closed(data,monkeypatch,tmp_path):
    s,_,frames=accepted_fixture(data,monkeypatch,tmp_path)
    ctx=PositionContext.model_validate(frames[0]['request']);sha=pgn_digest(data[3])
    with s.connection(True) as c:
        rows=c.execute("SELECT id,result FROM work WHERE tier='deep'").fetchall()
        original=[(r['id'],r['result']) for r in rows]
        for rid,text in original:
            result=json.loads(text);result['origin']='CACHE_REUSE';c.execute('UPDATE work SET result=? WHERE id=?',(json.dumps(result),rid))
    assert s.understanding_evidence(data[2],sha,ctx,UnderstandingBudget())['status']=='PRODUCER_UNATTESTED'
    with s.connection(True) as c:
        for rid,text in original:
            result=json.loads(text);result['evaluation']['pvs'][0]['moves']=['e2e5'];c.execute('UPDATE work SET result=? WHERE id=?',(json.dumps(result),rid))
    r=s.understanding_evidence(data[2],sha,ctx,UnderstandingBudget())
    assert not r['accepted'] and 'INVALID_EVIDENCE' in r['rejected_reasons']
    with s.connection(True) as c:
        for rid,text in original:c.execute('UPDATE work SET result=? WHERE id=?',(text,rid))
    assert not s.understanding_evidence(data[2],sha,ctx,UnderstandingBudget(),config_hash='0'*64)['accepted']


def test_unknown_root_and_long_history_only_disable_enrichment(data,tmp_path):
    _,path,_,_=data
    text='[SetUp "1"]\n[FEN "4k3/8/8/8/8/8/8/4K3 w - - 0 1"]\n\n1. Kd2 *'
    with GameLibrary(str(path)) as lib:g,_=lib.add_game(text)
    r=understand_game(path,g.game_id,1,pgn_digest(text),evidence='accepted_local')
    assert r.features['after'] and r.engine_evidence['status']=='UNKNOWN_ROOT_HISTORY'
    text=' '.join(['Nf3 Nf6 Ng1 Ng8']*251)+' *'
    with GameLibrary(str(path)) as lib:g,_=lib.add_game(text)
    r=understand_game(path,g.game_id,0,pgn_digest(text),evidence='accepted_local',budget=UnderstandingBudget(board_seconds=5,total_seconds=6))
    assert r.positions['after'] and r.engine_evidence['status']=='UNSUPPORTED_HISTORY'


def test_pv_budget_and_stale_source_admission(data,monkeypatch,tmp_path):
    from src.understanding_models import BudgetExceeded
    s,jid,frames=accepted_fixture(data,monkeypatch,tmp_path)
    ctx=PositionContext.model_validate(frames[0]['request'])
    with pytest.raises(BudgetExceeded):s.understanding_evidence(data[2],pgn_digest(data[3]),ctx,UnderstandingBudget(max_pushes=0))
    with GameLibrary(str(data[1])) as lib:
        lib._conn.execute('UPDATE games SET pgn=pgn||?',('\n{changed}',));lib._conn.commit()
    assert not s.understanding_evidence(data[2],pgn_digest(data[3]),ctx,UnderstandingBudget())['accepted']


def test_response_size_bound_preserves_no_dangling_feature_prompts(data,monkeypatch):
    client,path,gid,text=data
    result=understand_game(path,gid,0,pgn_digest(text))
    result.features['large_fixture']='x'*300000
    import api.understanding as module
    monkeypatch.setattr(module,'understand_game',lambda *a,**k:result)
    response=client.get(f'/api/games/{gid}/understanding',params={'ply':0,'expected_pgn_sha256':pgn_digest(text)})
    assert response.status_code==200 and len(response.content)<=256*1024
    body=response.json();assert body['coverage']['features']=='OUTPUT_BUDGET'
    assert not any(s.startswith('features.') for e in body['explanations'] for s in e['supports'])


def test_malformed_and_oversized_published_payloads_cannot_be_admitted(data,monkeypatch,tmp_path):
    s,_,frames=accepted_fixture(data,monkeypatch,tmp_path)
    ctx=PositionContext.model_validate(frames[0]['request'])
    for invalid in ('[]','{"evaluation":null,"origin":"LOCAL_SEARCH","simulation":false}', 'x'*131073):
        with s.connection(True) as c:c.execute('UPDATE work SET result=?',(invalid,))
        assert not s.understanding_evidence(data[2],pgn_digest(data[3]),ctx,UnderstandingBudget())['accepted']
