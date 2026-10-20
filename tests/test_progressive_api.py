import hashlib
import json
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from api.progressive import router
from test_progressive_store import env
from src.progressive_store import ProgressiveStore


@pytest.fixture
def api(env,monkeypatch):
    s,ids,_=env
    import time
    s.clock=time.time
    monkeypatch.setenv('CHESS_COACH_PROGRESSIVE_DB',str(s.path));monkeypatch.setenv('CHESS_COACH_DB',str(s.library))
    monkeypatch.setenv('CHESS_COACH_PROGRESSIVE_KEY','operator-key-123456')
    app=FastAPI();app.include_router(router)
    return TestClient(app),env


def test_enqueue_auth_readonly_and_engine_free(api):
    client,(s,ids,_)=api
    body={'game_ids':[ids[0]],'idempotency_key':'api'}
    assert client.post('/api/progressive-analysis/jobs',json=body).status_code==403
    response=client.post('/api/progressive-analysis/jobs',json=body,headers={'X-Progressive-Key':'operator-key-123456'})
    assert response.status_code==202,response.text
    jid=response.json()['job_ids'][0];s.prepare(jid)
    before=hashlib.sha256(s.path.read_bytes()).hexdigest()
    with patch('src.library.GameLibrary.__init__',side_effect=AssertionError('legacy writer')),patch('src.progressive_engine.UCIProcess',side_effect=AssertionError('engine')),patch('httpx.AsyncClient',side_effect=AssertionError('cloud')):
        assert client.get('/api/progressive-analysis/jobs/'+jid+'/result').json()['artifact']['partial']
        assert client.get('/api/progressive-analysis/health').status_code==200
    assert hashlib.sha256(s.path.read_bytes()).hexdigest()==before


def test_bounds_and_missing_store(api,monkeypatch,tmp_path):
    client,(s,ids,_)=api
    assert client.get('/api/progressive-analysis/jobs?limit=101').status_code==422
    assert client.post('/api/progressive-analysis/jobs',content=b'x'*131073).status_code==413
    monkeypatch.setenv('CHESS_COACH_PROGRESSIVE_DB',str(tmp_path/'absent.db'))
    assert client.get('/api/progressive-analysis/health').status_code==503
    assert not (tmp_path/'absent.db').exists()


def test_get_does_not_expose_deleted_source(api):
    from src.library import GameLibrary
    client,(s,ids,_)=api
    response=client.post('/api/progressive-analysis/jobs',json={'game_ids':[ids[0]],'idempotency_key':'deletion'},headers={'X-Progressive-Key':'operator-key-123456'})
    jid=response.json()['job_ids'][0];s.prepare(jid)
    with GameLibrary(str(s.library)) as lib:lib.delete_game(ids[0])
    result=client.get('/api/progressive-analysis/jobs/'+jid+'/result').json()
    assert result['state']=='SOURCE_MISSING' and result['artifact'] is None
