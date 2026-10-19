"""Segment 16 independent deterministic HTTP/cache benchmarks; temporary SQL only.

No real HTTP/engines. A parent supervises 120s/scale, 600s total; child watchdog
checks 512 MiB RSS. Scores are synthetic fixtures, never chess truth.
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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))


def rss_mib():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)


def percentiles(values):
    values=sorted(values)
    return {'samples':len(values),'p50_ms':values[math.ceil(len(values)*.5)-1]*1000,
            'p95_ms':values[math.ceil(len(values)*.95)-1]*1000}


def fixtures(count):
    import chess
    from src.evaluation_models import PositionContext,Source,SearchConfig,Evaluation,PV,Lookup
    rng=random.Random(16000); board=chess.Board(); result=[]
    for i in range(count):
        if board.is_game_over() or i%100==0: board=chess.Board()
        board.push(rng.choice(list(board.legal_moves)))
        if board.is_game_over(): board=chess.Board()
        fields=board.fen().split(); fields[4]=str(i)
        context=PositionContext(fen=' '.join(fields))
        cfg=SearchConfig(source=Source(kind='local_stockfish',engine_name='fixture',engine_version='v2' if i%17==0 else 'v1',
                         engine_build='deterministic',nnue='synthetic',provenance='benchmark mock; scores are not chess truth'),
                         requested_options={'Threads':1},applied_options={'Threads':1,'Hash':64},applied_options_complete=True,limit_kind='depth',depth=16,multipv=3 if i%19==0 else 1)
        move=next(iter(chess.Board(context.fen).legal_moves)).uci()
        line=PV(kind='mate',mate=(-3 if i%2 else 4),moves=[move]) if i%13==0 else PV(kind='cp',cp=i%401-200,moves=[move])
        e=Evaluation(context=context,config=cfg,achieved_depth=16,nodes=1000+i,pvs=[line])
        q=Lookup(context=context,allow_advisory=True,allow_partial=True)
        result.append((e,q))
    return result


def library_contention(path, barrier):
    from src.library import GameLibrary
    from src.analysis_queue import AnalysisQueue
    from src.positions import PositionIndex
    with GameLibrary(str(path)) as lib:
        for i in range(20):
            lib.add_game(f'[White "Fixture{i}"]\n[Black "Other"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 *',source='benchmark')
    def importer():
        barrier.wait()
        with GameLibrary(str(path)) as lib:
            for i in range(20,40): lib.add_game(f'[White "Fixture{i}"]\n[Black "Other"]\n[Result "*"]\n\n1. e4 e5 2. Nf3 *',source='benchmark')
        return 20
    def indexer():
        barrier.wait(); indexed=0
        with GameLibrary(str(path)) as lib:
            idx=PositionIndex(lib._conn)
            for _ in range(20):
                gid=idx.next_pending()
                if gid: assert idx.index_game(gid)=='COMPLETE'; indexed+=1
        return indexed
    def worker():
        barrier.wait(); times=[]
        with GameLibrary(str(path)) as lib:
            queue=AnalysisQueue(lib)
            for game in lib.list_games(limit=20):
                # list_games returns dictionaries in the current library contract.
                gid=game['game_id'] if isinstance(game,dict) else game.game_id
                queue.enqueue(gid)
            for _ in range(20):
                job=queue.claim_next_job('evaluation-benchmark')
                assert job
                t=time.perf_counter(); queue.heartbeat(job['job_id'],job['claim_token']); times.append(time.perf_counter()-t)
                queue.complete_job_with_analysis(job['job_id'],job['claim_token'],{'metadata':{'mock':True},'moves':[],'summary':{}})
        return {'completed_mock_jobs':len(times),'heartbeat':percentiles(times)}
    return importer,indexer,worker


def child(directory,count):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.evaluation_models import Lookup,PositionContext,Evaluation
    from src.evaluation_cache import EvaluationCache
    from src.evaluation_cloud import CloudCoordinator,CloudResolver
    import asyncio
    import httpx
    directory=Path(directory).resolve()
    os.environ['CHESS_COACH_DB']=str(directory/'library.db')
    os.environ['CHESS_COACH_EVAL_CACHE_DB']=str(directory/'evaluations.db')
    os.environ.pop('CHESS_COACH_EVAL_CLOUD_ENABLED',None)
    os.environ.pop('CHESS_COACH_EVAL_LIMITS',None)
    from api.evaluations import router
    stop=threading.Event(); started=time.perf_counter()
    def watchdog():
        while not stop.wait(.05):
            if rss_mib()>512 or time.perf_counter()-started>120: os._exit(4)
    threading.Thread(target=watchdog,daemon=True).start()
    def phase(name):
        (directory/'progress.json').write_text(json.dumps({'phase':name,'elapsed_seconds':time.perf_counter()-started,'peak_rss_mib':rss_mib()}))
    phase('generation'); t=time.perf_counter(); corpus=fixtures(count); generation=time.perf_counter()-t
    checksum=hashlib.sha256(''.join(e.model_dump_json() for e,_ in corpus).encode()).hexdigest()
    assert len({q.context.identity()['halfmove'] for _,q in corpus})==count
    cache=EvaluationCache(directory/'evaluations.db'); cache.initialize()
    phase('population'); t=time.perf_counter()
    for e,_ in corpus: cache.ingest(e)
    insertion=time.perf_counter()-t; populated_sizes=cache.sizes()
    app=FastAPI(); app.include_router(router); client=TestClient(app)
    queries=[q.model_dump() for _,q in corpus]
    def request(items):
        response=client.post('/api/evaluations/lookup-batch',json={'items':items})
        assert response.status_code==200,response.text
        assert response.json()['coverage']['advisory_hits']==len(items),response.text[:500]
        return len(response.content)
    phase('http_lookup'); t=time.perf_counter(); first_bytes=request(queries[:min(100,count)]); cold=time.perf_counter()-t
    singles=[]; batches=[]
    repeated=[queries[i%count] for i in range(100)]
    for i in range(100):
        t=time.perf_counter(); request([queries[i%count]]); singles.append(time.perf_counter()-t)
    for _ in range(20):
        t=time.perf_counter(); request(repeated); batches.append(time.perf_counter()-t)
    t=time.perf_counter()
    for offset in range(0,count,100): request(queries[offset:offset+100])
    sweep=time.perf_counter()-t
    misses=[]
    for i in range(100):
        body=json.loads(json.dumps(queries[i%count])); fields=body['context']['fen'].split(); fields[4]=str(100000+i); body['context']['fen']=' '.join(fields)
        t=time.perf_counter(); r=client.post('/api/evaluations/lookup-batch',json={'items':[body]}); misses.append(time.perf_counter()-t)
        assert r.json()['coverage']['misses']==1
    phase('duplicate_and_upgrade'); t=time.perf_counter()
    for e,_ in corpus: cache.ingest(e)
    duplicate=time.perf_counter()-t
    t=time.perf_counter()
    for e,_ in corpus:
        data=e.model_dump(); data['achieved_depth']=20; cache.ingest(Evaluation.model_validate(data))
    upgrade=time.perf_counter()-t
    phase('contention'); barrier=threading.Barrier(9)
    functions=list(library_contention(directory/'library.db',barrier))
    def reader():
        barrier.wait(); samples=[]
        for _ in range(20):
            t=time.perf_counter(); request(repeated); samples.append(time.perf_counter()-t)
        return percentiles(samples)
    def writer(number):
        barrier.wait()
        for e,_ in corpus[:50]:
            data=e.model_dump(); data['nodes']+=number+10; cache.ingest(Evaluation.model_validate(data))
        return min(50,count)
    with ThreadPoolExecutor(9) as pool:
        futures=[pool.submit(fn) for fn in functions]+[pool.submit(reader) for _ in range(4)]+[pool.submit(writer,i) for i in range(2)]
        contention=[f.result() for f in futures]
    phase('mock_cloud'); virtual=[10000.0]; mock_calls=[]
    mock=EvaluationCache(directory/'mock.db',clock=lambda:virtual[0]); mock.initialize()
    def handler(req):
        mock_calls.append(str(req.url)); return httpx.Response(404)
    q=Lookup(context=PositionContext(fen=corpus[0][0].context.fen),allow_advisory=True,allow_cloud=True)
    resolver=CloudResolver(mock,True,httpx.MockTransport(handler))
    r=asyncio.run(resolver.resolve(q)); assert r['status']=='CLOUD_MISS'
    assert asyncio.run(resolver.resolve(q))['negative_cache_hit'] and len(mock_calls)==1
    token,reason=CloudCoordinator(mock).acquire(); assert token is None and reason=='COOLDOWN'
    cleanup_started=time.perf_counter(); maintenance=cache.maintain(); cleanup=time.perf_counter()-cleanup_started
    with cache.reader() as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    single=percentiles(singles); batch=percentiles(batches)
    result={'positions':count,'seed':16000,'dataset_sha256':checksum,'python':platform.python_version(),'platform':platform.platform(),
            'distinct_exploration_positions':len({q.context.identity()['position'] for _,q in corpus}),
            'generation_seconds':generation,'insert_seconds':insertion,'duplicate_seconds':duplicate,'upgrade_seconds':upgrade,
            'first_complete_http_seconds':cold,'first_response_bytes':first_bytes,'single_http':single,'batch_100_http':batch,
            'miss_http':percentiles(misses),'complete_local_sweep_seconds':sweep,'populated_storage_bytes':populated_sizes,
            'final_storage_bytes':cache.sizes(),'peak_rss_mib':rss_mib(),'contention':contention,
            'mock_http_requests':len(mock_calls),'maintenance_seconds':cleanup,'maintenance':maintenance,
            'elapsed_seconds':time.perf_counter()-started,'scores':'synthetic; not chess truth'}
    result['acceptance']={'single_p95_25ms':single['p95_ms']<=25,'batch_p95_250ms':batch['p95_ms']<=250,
                          'sweep_10s':sweep<=10,'rss_512mib':result['peak_rss_mib']<=512,'elapsed_120s':result['elapsed_seconds']<=120}
    stop.set(); phase('complete')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scales',nargs='+',type=int,default=[50,1000,5000]); parser.add_argument('--output')
    parser.add_argument('--child',nargs=2,metavar=('DIRECTORY','COUNT'))
    args=parser.parse_args()
    if args.child:
        result=child(args.child[0],int(args.child[1])); print(json.dumps(result)); return
    if any(n not in (50,1000,5000) for n in args.scales): parser.error('Approved scales: 50, 1000, 5000')
    start=time.perf_counter(); results=[]
    for scale in args.scales:
        with tempfile.TemporaryDirectory(prefix='segment16-benchmark-') as directory:
            command=[sys.executable,str(Path(__file__).resolve()),'--child',directory,str(scale)]
            timeout=min(120,600-(time.perf_counter()-start))
            if timeout<=0: raise RuntimeError('600-second overall budget exhausted')
            try:
                process=subprocess.run(command,text=True,capture_output=True,timeout=timeout,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
                if process.returncode:
                    progress=Path(directory)/'progress.json'
                    raise RuntimeError(f'Scale {scale} failed: {process.stderr[-4000:]} progress={progress.read_text() if progress.exists() else "none"}')
                result=json.loads(process.stdout); results.append(result)
                print(json.dumps({'positions':scale,'acceptance':result['acceptance'],'elapsed_seconds':result['elapsed_seconds']}),flush=True)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f'Scale {scale} exceeded hard runtime; workload not reduced') from exc
    report={'scales':results,'total_seconds':time.perf_counter()-start,'all_passed':all(all(r['acceptance'].values()) for r in results)}
    if args.output: Path(args.output).write_text(json.dumps(report,indent=2)+'\n')
    else: print(json.dumps(report,indent=2))
    if not report['all_passed']: raise SystemExit(2)


if __name__=='__main__': main()
