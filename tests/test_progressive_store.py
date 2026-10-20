from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
import chess
import pytest
from src.library import GameLibrary
from src.evaluation_models import Evaluation,PV,PositionContext,SearchConfig,Source,canonical_json
from src.progressive_models import Schedule,ProgressiveLimits
from src.progressive_store import ProgressiveStore,ProgressiveError,StaleWork
from src.progressive_prepare import replay,shortlist
from src.progressive_worker import ProgressiveWorker


def profile():
    return SearchConfig(source=Source(kind='local_stockfish',engine_name='fixture',engine_version='fixture',engine_build='fixture',nnue='fixture',provenance='synthetic only'),
                        applied_options={'Threads':1,'Hash':64},applied_options_complete=True,limit_kind='combined',depth=10,time_seconds=.1)


class MockAdapter:
    simulated=True
    def __init__(self,cost=.001,fail=False):self.virtual_cost=cost;self.calls=0;self.fail=fail
    def search(self,ctx,cfg):
        self.calls+=1
        if self.fail:raise RuntimeError('mock crash')
        board=chess.Board(ctx.fen)
        return Evaluation(context=ctx,config=cfg,achieved_depth=cfg.depth,nodes=100,pvs=[PV(kind='cp',cp=0,moves=[next(iter(board.legal_moves)).uci()])])
    def close(self):pass


@pytest.fixture
def env(tmp_path):
    p=tmp_path/'library.db'
    with GameLibrary(str(p)) as lib:
        a,_=lib.add_game('[White "A"]\n[Black "B"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *')
        b,_=lib.add_game('[White "C"]\n[Black "D"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *')
    s=ProgressiveStore(tmp_path/'progressive.db',p,clock=lambda:1000.);s.initialize();pid=s.register_profile(profile(),simulation=True)
    return s,[a.game_id,b.game_id],pid


def enqueue(env,prepare_only=False,budget=300,ids=None):
    s,gids,pid=env
    result=s.enqueue(Schedule(game_ids=ids or gids,profile_id=pid,prepare_only=prepare_only,budget_seconds=budget,idempotency_key='request'))
    for jid in result['job_ids']:s.prepare(jid)
    return result


def test_prepare_only_useful_and_readonly_source(env):
    s,gids,pid=env;before=s.library.read_bytes(); r=enqueue(env,True)
    for jid in r['job_ids']:
        data=s.get(jid);assert data['state']=='COMPLETED' and data['artifact']['partial']
        assert data['artifact']['coverage']['discovered_positions']==7
        assert data['artifact']['candidates'] and data['artifact']['verification']['engine_verified_decisions']==0
        assert not data['work']
    assert s.library.read_bytes()==before
    assert not ProgressiveWorker(s).step()


def test_replay_identity_and_cap():
    frames,_,_=replay('1. e4 e5 2. Nf3 Nc6 *')
    for frame in frames:assert canonical_json(PositionContext.model_validate(frame['request']).identity())==frame['canonical']
    text=' '.join(['Nf3 Nf6 Ng1 Ng8']*251)+' *'
    with pytest.raises(ValueError,match='1000'):replay(text)


def test_shared_work_idempotency_and_full_mock_progress(env):
    s,gids,pid=env;r=enqueue(env)
    assert s.enqueue(Schedule(game_ids=gids,profile_id=pid,prepare_only=False,idempotency_key='request'))==r
    with s.connection() as c:
        assert c.execute('SELECT count(*) FROM work').fetchone()[0]==7
        assert c.execute('SELECT count(*) FROM interests').fetchone()[0]==14
    adapter=MockAdapter();worker=ProgressiveWorker(s,adapter=adapter)
    for _ in range(100):
        if not worker.step():worker.refresh();break
    for jid in r['job_ids']:
        d=s.get(jid);assert d['state'] in ('COMPLETED','PARTIAL'),d
        assert any(w['tier']=='deep' for w in d['work'])
        assert d['artifact']['verification']['engine_verified_decisions']==0
    with s.connection() as c:
        assert c.execute('SELECT count(*) FROM attempts WHERE charged IS NULL').fetchone()[0]==0
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert adapter.calls<42


def test_fencing_recovery_and_conservative_charges(env):
    s,_,_=env;now=[1000.];s.clock=lambda:now[0];r=enqueue(env)
    task=s.claim();assert task
    with pytest.raises(StaleWork):s.heartbeat(task['id'],'bad')
    now[0]+=31;assert s.recover()==1
    with pytest.raises(StaleWork):s.complete(task['id'],task['token'],cost=0,error='stale')
    status=s.status();assert status['budget'][0]['spent']==task['reservation']
    assert status['budget'][0]['reserved']==0


def test_cancel_shared_interest_and_deletion(env):
    s,gids,_=env;r=enqueue(env);s.cancel(r['job_ids'][0])
    task=s.claim();assert task
    e=MockAdapter().search(PositionContext.model_validate(task['context']),SearchConfig.model_validate(task['config']))
    s.complete(task['id'],task['token'],e,cost=.001,simulation=True)
    with GameLibrary(str(s.library)) as lib:lib.delete_game(gids[1])
    assert s.get(r['job_ids'][1])['state']=='SOURCE_MISSING'
    assert s.reconcile()==1


def test_last_cancel_refuses_publication(env):
    s,_,_=env;r=enqueue(env);task=s.claim()
    for jid in r['job_ids']:s.cancel(jid)
    e=MockAdapter().search(PositionContext.model_validate(task['context']),SearchConfig.model_validate(task['config']))
    s.complete(task['id'],task['token'],e,cost=.001)
    with s.connection() as c:assert c.execute('SELECT state FROM work WHERE id=?',(task['id'],)).fetchone()[0]=='CANCELLED'


def test_budget_and_policy_validation(env):
    s,_,_=env;enqueue(env,budget=0)
    assert s.claim() is None
    assert s.status()['budget'][0]['spent']==0
    with pytest.raises(ValueError):ProgressiveLimits(max_bytes=1)
    with pytest.raises(ValueError):Schedule(game_ids=['a']*101,idempotency_key='bad')


def test_busy_rollback_and_concurrent_enqueue(env):
    s,gids,pid=env
    def insert(_):return s.enqueue(Schedule(game_ids=gids,idempotency_key='concurrent'))
    with ThreadPoolExecutor(4) as pool:results=list(pool.map(insert,range(8)))
    assert all(r==results[0] for r in results)
    with pytest.raises(RuntimeError):
        with s.connection(True) as c:c.execute('DELETE FROM jobs');raise RuntimeError('interrupted')
    assert sum(s.status()['jobs'].values())==2


def test_retry_backoff_and_no_cost_reset(env):
    s,_,_=env;now=[1000.];s.clock=lambda:now[0];enqueue(env)
    task=s.claim();s.complete(task['id'],task['token'],cost=.01,error='fake crash')
    with s.connection() as c:
        w=c.execute('SELECT * FROM work WHERE id=?',(task['id'],)).fetchone()
        assert w['state']=='RETRY_WAIT' and w['not_before']>1001
    assert s.status()['budget'][0]['spent']==.01


def test_paired_ranking_heuristics_diversity():
    _,decisions,_=replay('1. Nf3 Nf6 2. g3 g6 3. Bg2 Bg7 4. O-O O-O 5. d3 d6 6. e4 e5 7. Nc3 Nc6 8. Be3 Be6 9. Qd2 Qd7 10. Rfe1 Rfe8 11. Rad1 Rad8 12. h3 h6 *')
    chosen=shortlist(decisions)
    assert any(d['quiet'] for d in chosen)
    assert len({d['ply']//8 for d in chosen})==len(chosen)
    assert sum(d['phase']=='opening' for d in chosen)<=1


def test_idempotency_payload_conflict_and_snapshot_change(env):
    s,gids,pid=env
    s.enqueue(Schedule(game_ids=[gids[0]],idempotency_key='fixed'))
    with pytest.raises(ValueError,match='Idempotency'):s.enqueue(Schedule(game_ids=[gids[1]],idempotency_key='fixed'))
    r=enqueue(env,True)
    with sqlite3.connect(s.library) as c:c.execute('UPDATE games SET pgn=pgn||? WHERE game_id=?',(' {clock changed}',gids[0]))
    assert s.get(r['job_ids'][0])['state']=='STALE_SOURCE'


def test_cache_provenance_advisory_and_outbox(env,tmp_path):
    from src.evaluation_cache import EvaluationCache
    from src.evaluation_cloud import parse_cloud
    from src.evaluation_models import Lookup
    s,gids,_=env;cache=EvaluationCache(tmp_path/'cache.db');cache.initialize()
    frames,_,_=replay('1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *')
    ctx=PositionContext.model_validate(frames[0]['request'])
    cloud=parse_cloud({'fen':ctx.fen,'depth':40,'knodes':1,'pvs':[{'cp':100,'moves':'d2d4'}]},Lookup(context=ctx,allow_advisory=True))
    cache.ingest(cloud)
    result=s.enqueue(Schedule(game_ids=[gids[0]],idempotency_key='advisory'))
    jid=result['job_ids'][0];s.prepare(jid,cache);artifact=s.get(jid)['artifact']
    assert artifact['coverage']['advisory_cache']==1 and artifact['coverage']['trusted_cache']==0
    assert artifact['verification']['engine_verified_decisions']==0
    result=enqueue(env);task=s.claim();e=MockAdapter().search(PositionContext.model_validate(task['context']),SearchConfig.model_validate(task['config']))
    s.complete(task['id'],task['token'],e,cost=.001,simulation=True)
    assert s.flush_outbox(cache)==1
    assert s.flush_outbox(cache)==0
    assert cache.lookup(Lookup(context=e.context,require_trusted=True,config_hash=__import__('src.evaluation_models',fromlist=['digest']).digest(e.config.model_dump())))['context_compatibility']=='TRUSTED_CONTEXT_MATCH'


def test_incompatible_result_rolls_back_settlement(env):
    s,_,_=env;enqueue(env);task=s.claim()
    ctx=PositionContext(fen=chess.STARTING_FEN)
    e=MockAdapter().search(ctx,SearchConfig.model_validate(task['config']))
    with pytest.raises(ValueError,match='Incompatible'):s.complete(task['id'],task['token'],e,cost=.001)
    assert s.status()['budget'][0]['reserved']==task['reservation']
    with pytest.raises(ProgressiveError,match='OVERRUN'):s.complete(task['id'],task['token'],cost=100,error='bad')
    assert s.status()['budget'][0]['spent']==0


def test_terminal_and_no_exact_score_are_not_fabricated(env):
    s,gids,pid=env
    with GameLibrary(str(s.library)) as lib:
        g,_=lib.add_game('[SetUp "1"]\n[FEN "7k/6Q1/5K2/8/8/8/8/8 b - - 0 1"]\n\n1-0')
    j=s.enqueue(Schedule(game_ids=[g.game_id],profile_id=pid,prepare_only=False,idempotency_key='terminal'))['job_ids'][0]
    s.prepare(j)
    assert s.get(j)['work'][0]['state']=='TERMINAL'
    assert 'terminal_observation' in s.get(j)['work'][0]['result']
    enqueue(env);task=s.claim();s.complete(task['id'],task['token'],cost=.01,error='NO_EXACT_SCORE')
    with s.connection() as c:
        row=c.execute('SELECT state,result FROM work WHERE id=?',(task['id'],)).fetchone()
        assert row['state']=='LIMIT_REACHED' and row['result'] is None


def test_concurrent_initializer_policy_and_maintenance(env):
    s,_,_=env
    with ThreadPoolExecutor(4) as pool:list(pool.map(lambda _:s.initialize(),range(8)))
    other=ProgressiveStore(s.path,s.library,ProgressiveLimits(max_jobs=100))
    with pytest.raises(ProgressiveError):other.enqueue(Schedule(game_ids=env[1],idempotency_key='mismatch'))
    now=[1000.];s.clock=lambda:now[0];r=enqueue(env,True);now[0]+=91*86400
    assert s.maintain()['deleted_jobs']==1
    with s.connection() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()


def test_explicit_retry_retains_spent_cost_and_new_generation(env):
    s,_,_=env;r=enqueue(env);task=s.claim();s.complete(task['id'],task['token'],cost=.01,error='permanent',retryable=False)
    with s.connection(True) as c:c.execute("UPDATE jobs SET state='PARTIAL' WHERE id=?",(task['job'],))
    s.retry(task['job'])
    with s.connection() as c:assert c.execute('SELECT generation FROM work WHERE id=?',(task['id'],)).fetchone()[0]==1
    assert s.status()['budget'][0]['spent']==.01


def test_engine_safety_failure_stops_dispatch(env):
    from src.progressive_engine import EngineSafetyError
    s,_,_=env;enqueue(env)
    class Broken(MockAdapter):
        def search(self,*a):raise EngineSafetyError('proof lost')
    worker=ProgressiveWorker(s,adapter=Broken());assert worker.step() and worker.stopped
    assert not worker.step()


def test_setup_history_and_long_replay_engine_free():
    frames,decisions,term=replay('[SetUp "1"]\n[FEN "k7/8/8/8/8/8/8/4K3 w - - 0 1"]\n\n1. Kf2 *')
    assert not term['root_history_complete']
    assert PositionContext.model_validate(frames[-1]['request']).identity()['history_complete'] is False


def test_held_reader_storage_pressure_fails_closed(env):
    s,_,_=env;r=enqueue(env,True)
    # Use a fresh 16 MiB policy store to test admission while WAL cannot checkpoint.
    tiny=ProgressiveStore(s.path.with_name('tiny.db'),s.library,ProgressiveLimits(max_bytes=16*1024**2));tiny.initialize()
    req=tiny.enqueue(Schedule(game_ids=env[1][:1],idempotency_key='capacity'));jid=req['job_ids'][0];tiny.prepare(jid)
    with tiny.connection() as reader:
        reader.execute('SELECT count(*) FROM jobs').fetchone()
        for index in range(30):
            try:
                with tiny.connection(True) as c:tiny._publish(c,jid,{'schema':'fixture','padding':chr(65+index%26)*900000})
            except ProgressiveError:break
        else:raise AssertionError('Physical admission should stop writes')
        assert sum(tiny.sizes().values())<=tiny.limits.max_bytes


def test_low_capacity_deferral_and_no_history_weakening(env):
    s,gids,pid=env
    low=ProgressiveStore(s.path.with_name('low.db'),s.library,ProgressiveLimits(max_active=1));low.initialize();pid=low.register_profile(profile(),simulation=True)
    j=low.enqueue(Schedule(game_ids=gids[:1],profile_id=pid,prepare_only=False,idempotency_key='low'))['job_ids'][0];low.prepare(j)
    assert low.get(j)['artifact']['coverage']['deferred_screen_contexts']>0
    with low.connection() as c:assert c.execute("SELECT count(*) FROM work WHERE state='PENDING'").fetchone()[0]==1


def test_running_last_consumer_cancellation_closes_adapter(env):
    import threading,time
    s,_,_=env;r=enqueue(env)
    class Slow(MockAdapter):
        def __init__(self):super().__init__();self.closed=False;self.started=threading.Event()
        def search(self,ctx,cfg):self.started.set();time.sleep(.25);return super().search(ctx,cfg)
        def close(self):self.closed=True
    adapter=Slow();worker=ProgressiveWorker(s,adapter=adapter)
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(worker.step);assert adapter.started.wait(1)
        for jid in r['job_ids']:s.cancel(jid)
        future.result(timeout=3)
    assert adapter.closed and worker.stopped
    assert s.status()['budget'][0]['reserved']==0


def test_weighted_lanes_no_starvation(env):
    s,gids,pid=env;now=[1000.];s.clock=lambda:now[0]
    for lane in (0,1,2):
        jobs=s.enqueue(Schedule(game_ids=gids,profile_id=pid,prepare_only=False,priority=lane,idempotency_key='lane'+str(lane)))['job_ids']
        for jid in jobs:s.prepare(jid)
    lanes=[]
    for _ in range(7):
        task=s.claim();assert task
        with s.connection() as c:lanes.append(c.execute('SELECT priority FROM jobs WHERE id=?',(task['job'],)).fetchone()[0])
        result=MockAdapter().search(PositionContext.model_validate(task['context']),SearchConfig.model_validate(task['config']))
        s.complete(task['id'],task['token'],result,cost=.001,simulation=True);now[0]+=.001
    assert lanes.count(2)==4 and lanes.count(1)==2 and lanes.count(0)==1


def test_backward_clock_and_unknown_real_provenance_fail_closed(env):
    s,_,_=env
    s.clock=lambda:999.
    with pytest.raises(ProgressiveError,match='CLOCK_REGRESSION'):
        with s.connection(True):pass
    s.clock=lambda:1000.
    with pytest.raises(ValueError,match='binary hash'):s.register_profile(profile(),simulation=False)


def test_simulated_adapter_cannot_use_real_profile(env):
    s,gids,_=env;cfg=profile().model_dump()
    cfg['source'].update(engine_build='sha256:'+'a'*64,nnue='embedded:sha256:'+'a'*64,provenance='pcis-progressive:v1:reset-per-context:test')
    pid=s.register_profile(SearchConfig.model_validate(cfg),simulation=False)
    j=s.enqueue(Schedule(game_ids=gids[:1],profile_id=pid,prepare_only=False,idempotency_key='real-profile'))['job_ids'][0];s.prepare(j)
    from src.progressive_engine import EngineSafetyError
    worker=ProgressiveWorker(s,adapter=MockAdapter())
    with pytest.raises(EngineSafetyError):worker.step()
    assert worker.stopped


def test_deleted_source_private_payload_cleanup_is_bounded(env):
    s,gids,_=env;r=enqueue(env)
    with GameLibrary(str(s.library)) as lib:
        for gid in gids:lib.delete_game(gid)
    assert s.reconcile()==2
    assert s.redact_missing()==0
    with s.connection() as c:assert c.execute("SELECT count(*) FROM payloads WHERE pgn!=''").fetchone()[0]==0


def test_canonical_collision_is_rejected_even_for_existing_work(env):
    s,_,_=env;r=enqueue(env)
    with s.connection(True) as c:
        c.execute('UPDATE contexts SET canonical=? WHERE id=(SELECT context_id FROM work LIMIT 1)',('{"corrupted":true}',))
    frames,_,_=replay('1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *')
    with s.connection() as c:job=c.execute('SELECT * FROM jobs WHERE id=?',(r['job_ids'][0],)).fetchone()
    from src.progressive_models import tier_config
    with pytest.raises(ProgressiveError,match='collision'):
        with s.connection(True) as c:
            for frame in frames:s._add_work(c,job,frame,tier_config(profile(),'screen'),'screen')


def test_more_than_one_consumer_page_advances_without_extra_engine_work(env):
    s,gids,pid=env;jobs=[]
    for index in range(105):
        j=s.enqueue(Schedule(game_ids=gids[:1],profile_id=pid,prepare_only=False,idempotency_key='fanout-'+str(index)))['job_ids'][0]
        s.prepare(j);jobs.append(j)
    worker=ProgressiveWorker(s,adapter=MockAdapter())
    for _ in range(100):
        if not worker.step():break
    else:raise AssertionError('Shared control did not drain')
    assert all(s.get(j)['state'] in ('COMPLETED','PARTIAL') for j in jobs)
    assert worker.adapter.calls<30


def test_worker_marks_budget_deferral_without_launching_adapter(env):
    s,_,_=env;r=enqueue(env,budget=0);adapter=MockAdapter();worker=ProgressiveWorker(s,adapter=adapter)
    assert not worker.step() and adapter.calls==0
    assert all(s.get(j)['state']=='PARTIAL' for j in r['job_ids'])


def test_scheduler_library_scope_cannot_be_repointed(env,tmp_path):
    s,_,_=env
    other=ProgressiveStore(s.path,tmp_path/'another-library.db',clock=lambda:1000.)
    with pytest.raises(ProgressiveError,match='SCOPE'):other.status()
