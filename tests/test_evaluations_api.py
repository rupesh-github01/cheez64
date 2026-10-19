from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import sqlite3
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.evaluations import router
from src.evaluation_cache import EvaluationCache
from test_evaluation_cache import context, evaluation


@pytest.fixture
def api(tmp_path,monkeypatch):
    path=tmp_path/'api.db'; c=EvaluationCache(path); c.initialize(); c.ingest(evaluation())
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(path))
    monkeypatch.delenv('CHESS_COACH_EVAL_CLOUD_ENABLED',raising=False)
    monkeypatch.delenv('CHESS_COACH_EVAL_LIMITS',raising=False)
    app=FastAPI(); app.include_router(router)
    return TestClient(app),c


def body(**extra):
    return {'context':context().model_dump(),'require_trusted':True,**extra}


def test_readonly_and_no_engine_network(api):
    client,c=api; before=hashlib.sha256(c.path.read_bytes()).hexdigest()
    with patch('src.library.GameLibrary.__init__',side_effect=AssertionError('library')), \
         patch('chess.engine.SimpleEngine.popen_uci',side_effect=AssertionError('engine')), \
         patch('src.evaluation_cloud.CloudResolver.resolve',side_effect=AssertionError('cloud')), \
         patch.object(EvaluationCache,'initialize',side_effect=AssertionError('DDL')):
        r=client.post('/api/evaluations/lookup-batch',json={'items':[body(),body()]})
        assert r.status_code==200,r.text
        assert r.json()['coverage']['compatible_hits']==2
        assert r.json()['coverage']['distinct_requested_count']==1
        assert client.get('/api/evaluations/status').json()['status']=='OK'
        assert client.get('/api/evaluations/lookup',params={'fen':context().fen}).json()['evaluation'] is None
    assert hashlib.sha256(c.path.read_bytes()).hexdigest()==before


def test_trusted_cloud_and_bounds(api,monkeypatch):
    client,c=api
    assert client.post('/api/evaluations/lookup-batch',json={'items':[body()]*101}).status_code==422
    assert client.post('/api/evaluations/resolve-batch',json={'items':[body()]*6}).status_code==422
    assert client.post('/api/evaluations/lookup-batch',content=b' '*131073).status_code==413
    assert client.get('/api/evaluations/lookup',params={'fen':context().fen,'min_depth':-1}).status_code==422
    monkeypatch.setenv('CHESS_COACH_EVAL_CLOUD_ENABLED','true')
    assert client.post('/api/evaluations/resolve',json=body(allow_cloud=True)).status_code==403
    monkeypatch.setenv('CHESS_COACH_EVAL_RESOLVE_KEY','test-operator-key-16chars')
    r=client.post('/api/evaluations/resolve',json=body(allow_cloud=True),headers={'X-Evaluation-Key':'test-operator-key-16chars'})
    assert r.json()['context_compatibility']=='TRUSTED_CONTEXT_MATCH'


def test_missing_cache_and_bad_configuration(api,tmp_path,monkeypatch):
    client,c=api; path=tmp_path/'absent.db'; monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(path))
    assert client.get('/api/evaluations/status').json()['status']=='UNAVAILABLE'
    assert client.post('/api/evaluations/lookup-batch',json={'items':[body()]}).json()['coverage']['unavailable']==1
    assert not path.exists()
    monkeypatch.setenv('CHESS_COACH_EVAL_LIMITS','{"max_records":0}')
    assert client.get('/api/evaluations/status').json()['status']=='UNAVAILABLE'


def test_disabled_resolve_misses_no_network(api):
    client,c=api
    query={'context':context(['e2e4']).model_dump(),'require_trusted':True,'allow_cloud':True}
    with patch('httpx.AsyncClient',side_effect=AssertionError('external network')):
        r=client.post('/api/evaluations/resolve',json=query)
    assert r.json()['status']=='CLOUD_DISABLED'
    assert r.json()['evaluation'] is None


def test_busy_and_interrupted_initializer(api):
    _,cache=api
    with sqlite3.connect(cache.path,timeout=0.1) as lock:
        lock.execute('BEGIN IMMEDIATE')
        with pytest.raises(sqlite3.OperationalError): cache.ingest(evaluation(cp=99))
        lock.rollback()
    assert cache.status()['counts']['eval_records']==1


def test_batch_output_bound(api,monkeypatch):
    from api.evaluations import envelope
    from src.evaluation_models import Lookup
    requests=[Lookup.model_validate(body())]*100
    fake={'status':'CACHE_HIT','evaluation':{'fixture':'x'*40000},'context_compatibility':'TRUSTED_CONTEXT_MATCH','partial':False}
    response=envelope(requests,[fake]*100)
    assert len(json.dumps(response).encode())<2*1024**2
    assert response['coverage']['deferred']>0 and response['partial']
