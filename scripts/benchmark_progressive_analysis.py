"""Independent Segment 17 temporary game-library/control-plane benchmarks.

No Stockfish or external HTTP. All N games are prepared; virtual 300s compute
budget explicitly limits mock work. Parent enforces 120s/scale, 600s total.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import subprocess
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))


def rss():return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)

def percentile(values):
    ordered=sorted(values)
    return {'samples':len(values),'p50':ordered[math.ceil(len(ordered)*.5)-1],'p95':ordered[math.ceil(len(ordered)*.95)-1]} if ordered else {'samples':0}


def corpus_game(number):
    import chess
    import chess.pgn
    board=chess.Board();mode=number%3
    prefix=('e4 e5 Nf3 Nc6' if mode==0 else ('Nf3 Nf6 Nc3 Nc6' if number%2 else 'Nc3 Nc6 Nf3 Nf6'))
    for san in prefix.split():board.push_san(san)
    rng=random.Random(17000+number if mode==2 else 17000+mode)
    while len(board.move_stack)<40 and not board.is_game_over():board.push(rng.choice(list(board.legal_moves)))
    game=chess.pgn.Game.from_board(board);game.headers.update(White=f'Benchmark{number}',Black='Opponent',Result='*')
    return game.accept(chess.pgn.StringExporter(headers=True,comments=False,variations=False))


def profile():
    from src.evaluation_models import SearchConfig,Source
    return SearchConfig(source=Source(kind='local_stockfish',engine_name='synthetic-fixture',engine_version='synthetic-fixture',engine_build='fixture',nnue='fixture',provenance='synthetic mock, not chess truth'),
                        applied_options={'Threads':1,'Hash':64},applied_options_complete=True,limit_kind='combined',depth=10,time_seconds=.1)


def child(directory,count):
    import chess
    from src.library import GameLibrary
    from src.evaluation_cache import EvaluationCache
    from src.evaluation_models import PositionContext,Evaluation,PV,SearchConfig
    from src.progressive_prepare import replay
    from src.progressive_store import ProgressiveStore,StaleWork
    from src.progressive_models import Schedule
    from src.progressive_mock import MockEngineAdapter
    from src.progressive_worker import ProgressiveWorker
    from scripts.benchmark_evaluations import library_contention
    directory=Path(directory).resolve();started=time.perf_counter();cpu_started=time.process_time();stop=threading.Event();virtual=[1000.]
    def phase(name):
        (directory/'progress.json').write_text(json.dumps({'phase':name,'seconds':time.perf_counter()-started,'peak_rss_mib':rss()}))
    def watch():
        while not stop.wait(.05):
            if rss()>512 or time.perf_counter()-started>120:os._exit(4)
    threading.Thread(target=watch,daemon=True).start()
    library=directory/'library.db';os.environ['CHESS_COACH_DB']=str(library)
    os.environ['CHESS_COACH_PROGRESSIVE_DB']=str(directory/'progressive.db')
    os.environ.pop('CHESS_COACH_EVAL_CLOUD_ENABLED',None)
    phase('dataset_import');t=time.perf_counter();ids=[];corpus_hash=hashlib.sha256();first=None
    with GameLibrary(str(library)) as lib:
        for i in range(count):
            text=corpus_game(i);corpus_hash.update(text.encode());first=first or text
            game,new=lib.add_game(text,source='benchmark');assert new;ids.append(game.game_id)
    setup=time.perf_counter()-t
    s=ProgressiveStore(directory/'progressive.db',library,clock=lambda:virtual[0]);s.initialize();pid=s.register_profile(profile(),simulation=True)
    cache=EvaluationCache(directory/'evaluations.db');cache.initialize()
    frames,_,_=replay(first)
    for frame in frames[:8]:
        ctx=PositionContext.model_validate(frame['request']);board=chess.Board(ctx.fen)
        cache.ingest(Evaluation(context=ctx,config=profile(),achieved_depth=10,pvs=[PV(kind='cp',cp=0,moves=[next(iter(board.legal_moves)).uci()])]))
    phase('enqueue');t=time.perf_counter();jobs=[];bid=None;chunk=min(100,max(1,count//5))
    for start in range(0,count,chunk):
        req=Schedule(game_ids=ids[start:start+chunk],profile_id=pid,prepare_only=False,batch_id=bid,
                     priority=(start//chunk)%3,idempotency_key=f'chunk-{start}')
        result=s.enqueue(req);bid=result['batch_id'];jobs+=result['job_ids']
    enqueue_time=time.perf_counter()-t
    phase('preparation_and_cache');before=hashlib.sha256(library.read_bytes()).hexdigest();t=time.perf_counter()
    for jid in jobs:s.prepare(jid,cache)
    preparation=time.perf_counter()-t
    assert hashlib.sha256(library.read_bytes()).hexdigest()==before
    with s.connection() as c:
        aggregate={};timing={}
        for row in c.execute('SELECT artifact FROM payloads'):
            artifact=json.loads(row[0])
            for key,value in artifact['coverage'].items():aggregate[key]=aggregate.get(key,0)+value
            for key,value in artifact['timings'].items():timing[key]=timing.get(key,0)+value
        cache_suppressed=c.execute("SELECT count(*) FROM work WHERE state='SUCCEEDED' AND json_extract(result,'$.origin')='CACHE_REUSE'").fetchone()[0]
        counts={k:c.execute(f'SELECT count(*) FROM {k}').fetchone()[0] for k in ('jobs','contexts','work','interests')}
    assert counts['jobs']==count
    phase('mock_dispatch_and_interference')
    # Every mock search charges a declared fixed synthetic 4.1s, not real engine time.
    adapter=MockEngineAdapter(cost=4.1);worker=ProgressiveWorker(s,cache,adapter)
    barrier=threading.Barrier(4);background=library_contention(library,barrier)
    t=time.perf_counter();dispatch=[];queue_waits=[]
    def dispatch_loop():
        barrier.wait()
        for _ in range(200):
            tick=time.perf_counter()
            if not worker.step():break
            dispatch.append(time.perf_counter()-tick);virtual[0]+=4.1
        else:raise AssertionError('Declared virtual budget did not bound dispatch')
        return len(dispatch)
    with ThreadPoolExecutor(4) as pool:
        futures=[pool.submit(fn) for fn in background]+[pool.submit(dispatch_loop)]
        interference=[f.result() for f in futures]
    dispatch_time=time.perf_counter()-t
    s.finish_budget(bid)
    with s.connection() as c:
        lanes=dict(c.execute('SELECT j.priority,count(*) FROM attempts a JOIN jobs j ON j.id=a.job_id GROUP BY j.priority').fetchall())
        queue_waits=[r[0] for r in c.execute('SELECT a.started-j.created FROM attempts a JOIN jobs j ON j.id=a.job_id')]
        final_states=dict(c.execute('SELECT state,count(*) FROM jobs GROUP BY state').fetchall())
        assert not c.execute('SELECT 1 FROM attempts WHERE charged IS NULL').fetchone()
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok';assert not c.execute('PRAGMA foreign_key_check').fetchall()
    # Separate fully drained, low-cost synthetic control; no claim of real Stockfish speed.
    phase('fully_drained_control');control=ProgressiveStore(directory/'control.db',library,clock=lambda:virtual[0]);control.initialize()
    cp=control.register_profile(profile(),simulation=True);cj=control.enqueue(Schedule(game_ids=ids[:7],profile_id=cp,prepare_only=False,idempotency_key='control'))['job_ids']
    for jid in cj:control.prepare(jid)
    ca=MockEngineAdapter();cw=ProgressiveWorker(control,adapter=ca);t=time.perf_counter()
    for _ in range(1000):
        if not cw.step():cw.refresh();break
    else:raise AssertionError('Control did not drain')
    control_time=time.perf_counter()-t
    control_states=[control.get(j)['state'] for j in cj]
    assert all(x in ('COMPLETED','PARTIAL') for x in control_states),control_states
    phase('recovery');extra=control.enqueue(Schedule(game_ids=ids[:1],profile_id=cp,prepare_only=False,idempotency_key='recovery'))['job_ids'][0]
    # A different configuration makes fresh recovery work without editing existing identities.
    cfg=profile().model_dump();cfg['source']['provenance']='synthetic recovery fixture';rp=control.register_profile(SearchConfig.model_validate(cfg),simulation=True)
    extra=control.enqueue(Schedule(game_ids=ids[:1],profile_id=rp,prepare_only=False,idempotency_key='recovery-work'))['job_ids'][0]
    control.prepare(extra);task=control.claim();assert task
    virtual[0]+=31;t=time.perf_counter();recovered=control.recover();recovery_time=time.perf_counter()-t
    assert recovered==1
    try:control.complete(task['id'],task['token'],cost=0,error='stale')
    except StaleWork:pass
    else:raise AssertionError('Stale owner published')
    t=time.perf_counter();worker.prepare_pending(1);outbox_pending=s.status()['outbox_pending'];maintenance=s.maintain();maintenance_time=time.perf_counter()-t
    result={'games':count,'seed':17000,'corpus_sha256':corpus_hash.hexdigest(),'python':platform.python_version(),'platform':platform.platform(),
            'dataset_import_seconds':setup,'enqueue_seconds':enqueue_time,'preparation_plus_cache_seconds':preparation,
            'preparation_components_seconds':timing,'preparation_other_seconds':preparation-sum(timing.values()),
            'coverage':aggregate,'admission_counts':counts,'shared_work_suppressed':counts['interests']-counts['work'],
            'mock_dispatch_interference_seconds':dispatch_time,'mock_dispatch_latency_seconds':percentile(dispatch),
            'cache_avoided_mock_invocations':cache_suppressed,'trusted_cache_occurrence_hit_ratio':aggregate['trusted_cache']/aggregate['discovered_positions'],
            'mock_invocations':adapter.calls,'dispatch_count':len(dispatch),'lane_dispatch_counts':lanes,'virtual_queue_wait_seconds':percentile(queue_waits),
            'interference':interference,'final_job_states':final_states,'budget':s.status()['budget'][0],
            'fully_drained_control':{'games':len(cj),'mock_calls':ca.calls,'seconds':control_time,'states':control_states},
            'recovery_seconds':recovery_time,'recovered_attempts':recovered,'maintenance_seconds':maintenance_time,
            'maintenance':maintenance,'outbox_pending':outbox_pending,'storage_bytes':s.sizes(),'cache_storage_bytes':cache.sizes(),
            'peak_rss_mib':rss(),'total_cpu_seconds':time.process_time()-cpu_started,'elapsed_seconds':time.perf_counter()-started,
            'engine_seconds':'unmeasured; virtual costs are synthetic, no Stockfish launched','live_cloud_requests':0}
    result['acceptance']={'rss_512mib':result['peak_rss_mib']<=512,'elapsed_120s':result['elapsed_seconds']<=120,
                          'every_game_prepared':counts['jobs']==count,'budget_ledger':result['budget']['spent']<=300.001 and result['budget']['reserved']==0,
                          'all_outcomes_visible':sum(final_states.values())==count,'source_unchanged_by_preparation':True,
                          'all_lanes_served':all(lanes.get(k,0)>0 for k in (0,1,2)),
                          'shared_work_deduplicated':counts['interests']>counts['work'],
                          'recovery_fenced':recovered==1}
    stop.set();phase('complete');return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--scales',nargs='+',type=int,default=[50,1000,5000]);p.add_argument('--output');p.add_argument('--child',nargs=2)
    a=p.parse_args()
    if a.child:print(json.dumps(child(*[a.child[0],int(a.child[1])])));return
    if any(n not in (50,1000,5000) for n in a.scales):p.error('Approved scales: 50, 1000, 5000')
    started=time.perf_counter();results=[]
    for scale in a.scales:
        with tempfile.TemporaryDirectory(prefix='segment17-benchmark-') as directory:
            timeout=min(120,600-(time.perf_counter()-started))
            try:run=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child',directory,str(scale)],capture_output=True,text=True,timeout=timeout,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            except subprocess.TimeoutExpired as exc:raise RuntimeError(f'Scale {scale} exceeded budget; workload was not reduced') from exc
            if run.returncode:
                progress=Path(directory)/'progress.json'
                raise RuntimeError(f'Scale {scale}: {run.stderr[-4000:]} progress={progress.read_text() if progress.exists() else "none"}')
            result=json.loads(run.stdout);results.append(result);print(json.dumps({'games':scale,'acceptance':result['acceptance'],'seconds':result['elapsed_seconds']}),flush=True)
    report={'scales':results,'total_seconds':time.perf_counter()-started,'all_passed':all(all(x['acceptance'].values()) for x in results)}
    if a.output:Path(a.output).write_text(json.dumps(report,indent=2)+'\n')
    else:print(json.dumps(report,indent=2))
    if not report['all_passed']:raise SystemExit(2)


if __name__=='__main__':main()
