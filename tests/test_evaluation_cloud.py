"""All provider traffic is intercepted by httpx.MockTransport."""
import asyncio
from concurrent.futures import ProcessPoolExecutor
import json
import sqlite3

import chess
import httpx
import pytest

from src.evaluation_models import Lookup, PositionContext, CacheLimits
from src.evaluation_cloud import CloudResolver, CloudCoordinator, parse_cloud, retry_after
from src.evaluation_cache import EvaluationCache, CacheUnavailable
from test_evaluation_cache import context, evaluation


def query(**kw):
    return Lookup(context=context(), allow_advisory=True, allow_cloud=True, **kw)


def payload(**extra):
    return {'fen': chess.STARTING_FEN, 'depth': 20, 'knodes': 12, 'pvs': [{'cp': 0, 'moves': 'e2e4 e7e5'}], **extra}


def run(cache, response, req=None, enabled=True):
    async def handler(request):
        assert request.url.host == 'lichess.org'
        return response
    return asyncio.run(CloudResolver(cache, enabled, httpx.MockTransport(handler)).resolve(req or query()))


@pytest.fixture
def cache(tmp_path):
    c = EvaluationCache(tmp_path/'cloud.db'); c.initialize(); return c


def test_cloud_advisory_and_never_trusted(cache):
    r = run(cache, httpx.Response(200,json=payload()))
    assert r['status'] == 'CLOUD_HIT'
    assert r['context_compatibility'] == 'ADVISORY_CONTEXT_UNVERIFIED'
    assert r['evaluation']['pvs'][0]['cp'] == 0
    assert r['evaluation']['source']['engine_version'] is None
    trusted = cache.lookup(Lookup(context=context(),require_trusted=True,allow_advisory=True))
    assert trusted['evaluation'] is None
    assert trusted['status'] == 'INCOMPATIBLE'
    assert run(cache,httpx.Response(200,json=payload()),query(require_trusted=True))['status'] == 'INCOMPATIBLE'


@pytest.mark.parametrize('enabled,optin',[(False,True),(True,False),(False,False)])
def test_dual_disabled_no_http(cache,enabled,optin):
    def fail(request): raise AssertionError('Network forbidden')
    q = query().model_copy(update={'allow_cloud':optin})
    r = asyncio.run(CloudResolver(cache,enabled,httpx.MockTransport(fail)).resolve(q))
    assert r['status'] == 'CLOUD_DISABLED'


@pytest.mark.parametrize('score',[{'mate':3},{'mate':-5},{'cp':-50}])
def test_white_typed_scores(cache,score):
    r = run(cache,httpx.Response(200,json=payload(pvs=[{**score,'moves':'e2e4'}])))
    line = r['evaluation']['pvs'][0]
    assert line['cp'] == score.get('cp') and line['mate'] == score.get('mate')


def test_partial_cloud_and_threshold(cache):
    q = query(cloud_multipv=3)
    r = run(cache,httpx.Response(200,json=payload()),q)
    assert r['status'] == 'INCOMPATIBLE' and r['evaluation'] is None
    r = cache.lookup(q.model_copy(update={'allow_partial':True}))
    assert r['evaluation']['returned_multipv'] == 1 and r['partial']


@pytest.mark.parametrize('code,expected',[(404,'CLOUD_MISS'),(429,'RATE_LIMITED'),(500,'UNAVAILABLE'),(302,'UNAVAILABLE'),(403,'UNAVAILABLE')])
def test_errors(cache,code,expected):
    r = run(cache,httpx.Response(code,headers={'Retry-After':'120'}))
    assert r['status'] == expected
    assert cache.status()['counts']['eval_records'] == 0
    if code == 404:
        assert run(cache,httpx.Response(500))['negative_cache_hit']
    if code == 429:
        assert cache.status()['cloud_control']['cooldown'] >= cache.clock()+119


@pytest.mark.parametrize('data',[payload(depth=True),payload(pvs=[{'cp':True,'moves':'e2e4'}]),
    payload(pvs=[{'cp':1,'mate':1,'moves':'e2e4'}]),payload(pvs=[{'mate':0,'moves':''}]),
    payload(pvs=[{'cp':1,'moves':'e2e5'}]),payload(pvs=[]),payload(fen=chess.Board().mirror().fen().replace(' w ',' b ')),
    payload(pvs=[{'cp':0,'moves':' '.join(['g1f3','g8f6','f3g1','f6g8']*17)}])])
def test_malformed_no_publish(cache,data):
    r = run(cache,httpx.Response(200,json=data))
    assert r['status'] == 'UNAVAILABLE'
    assert cache.status()['counts']['eval_records'] == 0


def test_castling_and_terminal_mate():
    fen = 'r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1'
    q = Lookup(context=PositionContext(fen=fen),allow_advisory=True)
    e = parse_cloud(payload(fen=fen,pvs=[{'cp':10,'moves':'e1h1 e8a8'}]),q)
    assert e.pvs[0].moves == ['e1g1','e8c8']
    fen = '7k/6Q1/5K2/8/8/8/8/8 b - - 0 1'
    q = Lookup(context=PositionContext(fen=fen),allow_advisory=True)
    e = parse_cloud(payload(fen=fen,pvs=[{'mate':0,'moves':''}]),q)
    assert e.pvs[0].winner == 'white'


def test_body_network_and_timeout(cache):
    assert run(cache,httpx.Response(200,content=b' '*65537))['reason'] == 'INVALID_PROVIDER_RESPONSE'
    for exc in [httpx.ConnectError('mock'), httpx.ReadTimeout('mock')]:
        cache.clock = lambda: 9999999999
        with cache.writer() as c: c.execute('UPDATE eval_cloud_control SET cooldown=0,next_allowed=0')
        def handler(request): raise exc
        r = asyncio.run(CloudResolver(cache,True,httpx.MockTransport(handler)).resolve(query()))
        assert r['reason'] in ('NETWORK_ERROR','TIMEOUT')


def test_retry_after_rules():
    assert retry_after('120',100) == 220
    assert retry_after('-1',100) == 160
    assert retry_after(None,100) == 160
    assert retry_after('Thu, 01 Jan 1970 00:05:00 GMT',100) == 300
    assert retry_after('9'*50,100)>100+365*86400


def acquire_in_process(path):
    cache = EvaluationCache(path, clock=lambda:1000)
    return CloudCoordinator(cache).acquire()


def test_multiprocess_slot_crash_fencing_and_clock(cache):
    with ProcessPoolExecutor(4) as pool:
        results = list(pool.map(acquire_in_process,[str(cache.path)]*4))
    tokens = [r[0] for r in results if r[0]]
    assert len(tokens) == 1
    cache.clock = lambda:1001
    assert CloudCoordinator(cache).acquire()[1] == 'IN_FLIGHT'
    cache.clock = lambda:1031
    token,_ = CloudCoordinator(cache).acquire()
    with pytest.raises(CacheUnavailable): CloudCoordinator(cache).finish(tokens[0],publish=evaluation())
    CloudCoordinator(cache).finish(token)
    cache.clock = lambda:1030
    assert CloudCoordinator(cache).acquire()[1] == 'CLOCK_REGRESSION'


def test_rolling_rate_budget_and_no_transaction_across_http(tmp_path):
    c = EvaluationCache(tmp_path/'rate.db',CacheLimits(per_minute=2),clock=lambda:1000); c.initialize()
    for now in [1000,1005]:
        c.clock=lambda:now
        token,_=CloudCoordinator(c).acquire(); assert token
        CloudCoordinator(c).finish(token)
    c.clock=lambda:1010
    assert CloudCoordinator(c).acquire()[1] == 'RATE_BUDGET'
    c.clock=lambda:1061
    def handler(request):
        with c.writer() as conn: conn.execute('SELECT 1')
        return httpx.Response(200,json=payload())
    assert asyncio.run(CloudResolver(c,True,httpx.MockTransport(handler)).resolve(query()))['status'] == 'CLOUD_HIT'


def test_total_async_deadline_and_cooldown(cache):
    import time
    async def slow(request):
        await asyncio.sleep(1)
        raise AssertionError('deadline should cancel transport')
    start=time.monotonic()
    result=asyncio.run(CloudResolver(cache,True,httpx.MockTransport(slow)).resolve(query(),deadline=start+.015))
    assert result['reason']=='TIMEOUT' and time.monotonic()-start<.5
    assert cache.status()['cloud_control']['cooldown']>cache.clock()


def test_positive_stampede_only_one_http(cache):
    calls=[]
    async def handler(request):
        calls.append(1); await asyncio.sleep(.02)
        return httpx.Response(200,json=payload())
    async def both():
        resolver=CloudResolver(cache,True,httpx.MockTransport(handler))
        return await asyncio.gather(resolver.resolve(query()),resolver.resolve(query()))
    results=asyncio.run(both())
    assert len(calls)==1
    assert {r['status'] for r in results}=={'CLOUD_HIT','DEFERRED'}


def test_hourly_rolling_window_no_boundary_burst(tmp_path):
    now=[10000.0]
    c=EvaluationCache(tmp_path/'rolling.db',CacheLimits(per_hour=2),clock=lambda:now[0]); c.initialize()
    for at in [10000,13595]:
        now[0]=at; token,_=CloudCoordinator(c).acquire(); assert token; CloudCoordinator(c).finish(token)
    now[0]=13600
    token,_=CloudCoordinator(c).acquire(); assert token; CloudCoordinator(c).finish(token)
    now[0]=13605
    assert CloudCoordinator(c).acquire()[1]=='RATE_BUDGET'


def test_negative_expiry_and_request_key(cache):
    now=[1000.0]; cache.clock=lambda:now[0]
    assert run(cache,httpx.Response(404))['status']=='CLOUD_MISS'
    now[0]=1006
    assert run(cache,httpx.Response(404),query(cloud_multipv=2)).get('negative_cache_hit') is not True
    now[0]=1301
    assert run(cache,httpx.Response(200,json=payload()))['status']=='CLOUD_HIT'


def test_reobserved_identical_cloud_content_refreshes_freshness(cache):
    now=[1000.0]; cache.clock=lambda:now[0]
    assert run(cache,httpx.Response(200,json=payload()))['status']=='CLOUD_HIT'
    now[0]+=31*86400
    assert cache.lookup(query())['evaluation'] is None
    assert run(cache,httpx.Response(200,json=payload()))['status']=='CLOUD_HIT'
    assert cache.status()['counts']['eval_records']==1
    assert cache.lookup(query())['evaluation']['last_seen']==now[0]


@pytest.mark.parametrize('extra',[{'fen':None},{'fen':123},{'knodes':None},{'knodes':True},{'depth':None}])
def test_provider_field_types_fail_as_structured_errors(cache,extra):
    assert run(cache,httpx.Response(200,json=payload(**extra)))['reason']=='INVALID_PROVIDER_RESPONSE'
