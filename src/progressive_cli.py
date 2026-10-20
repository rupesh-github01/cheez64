"""Explicit independent-store controls; no default engine and no legacy writes."""
import argparse
import json
import os
from pathlib import Path
import signal
import tempfile
from .progressive_models import ProgressiveLimits,Schedule
from .progressive_store import ProgressiveStore
from .progressive_worker import ProgressiveWorker,ProvedUCIAdapter
from .progressive_engine import prove_host_safety
from .evaluation_models import SearchConfig
from .evaluation_cache import EvaluationCache


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['init','register','enqueue','prepare','work','status','result','cancel','retry','maintain','safety-test'])
    p.add_argument('--store',required=True);p.add_argument('--library',required=True)
    p.add_argument('--request-file');p.add_argument('--profile-file');p.add_argument('--job');p.add_argument('--cache')
    p.add_argument('--max-jobs',type=int,default=100);p.add_argument('--max-tasks',type=int,default=100)
    p.add_argument('--enable-engine',action='store_true');p.add_argument('--exclusive-engine-host',action='store_true')
    p.add_argument('--engine-command-file');p.add_argument('--mock',action='store_true');p.add_argument('--simulation',action='store_true')
    a=p.parse_args(argv)
    if not 1<=a.max_jobs<=100 or not 1<=a.max_tasks<=10000:p.error('Bounded jobs/tasks required')
    s=ProgressiveStore(a.store,a.library,ProgressiveLimits.model_validate_json(os.environ.get('CHESS_COACH_PROGRESSIVE_LIMITS','{}')))
    cache=EvaluationCache(a.cache) if a.cache else None
    result=None
    if a.action=='init':s.initialize();result=s.status()
    elif a.action=='register':result={'profile_id':s.register_profile(SearchConfig.model_validate_json(Path(a.profile_file).read_text()),simulation=a.simulation)}
    elif a.action=='enqueue':result=s.enqueue(Schedule.model_validate_json(Path(a.request_file).read_text()))
    elif a.action=='prepare':result={'prepared':ProgressiveWorker(s,cache).prepare_pending(a.max_jobs)}
    elif a.action=='status':result=s.status()
    elif a.action=='result':result=s.get(a.job)
    elif a.action=='cancel':s.cancel(a.job);result={'cancelled':a.job}
    elif a.action=='retry':s.retry(a.job);result={'retry':a.job}
    elif a.action=='maintain':result=s.maintain()
    elif a.action=='safety-test':result=prove_host_safety().__dict__
    elif a.action=='work':
        adapter=None
        if a.mock:
            for path in (s.path,s.library,cache.path if cache else s.path):
                if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):p.error('Mock work requires temporary databases')
            from .progressive_mock import MockEngineAdapter
            adapter=MockEngineAdapter()
        elif a.enable_engine:
            if not a.engine_command_file or not a.exclusive_engine_host:p.error('Explicit command and exclusive host required')
            import sqlite3
            from datetime import datetime,timezone,timedelta
            with sqlite3.connect(s.library.as_uri()+'?mode=ro',uri=True) as read:
                active=read.execute('SELECT 1 FROM worker_heartbeats WHERE last_seen_at>? LIMIT 1',((datetime.now(timezone.utc)-timedelta(seconds=30)).isoformat(),)).fetchone()
                if active:p.error('Legacy worker activity observed; engine dispatch refused')
            proof=prove_host_safety()
            adapter=ProvedUCIAdapter(json.loads(Path(a.engine_command_file).read_text()),str(Path('/private/tmp' if __import__('sys').platform=='darwin' else '/tmp')/f'pcis-progressive-{os.getuid()}.engine.lock'),proof,True)
        worker=ProgressiveWorker(s,cache,adapter)
        signal.signal(signal.SIGTERM,lambda *_:worker.shutdown());signal.signal(signal.SIGINT,lambda *_:worker.shutdown())
        try:
            worker.prepare_pending(a.max_jobs);count=0
            while count<a.max_tasks and worker.step():count+=1
            result={'dispatched_tasks':count,'engine_enabled':adapter is not None,**s.status()}
        finally:worker.shutdown()
    print(json.dumps(result,indent=2));return result


if __name__=='__main__':main()
