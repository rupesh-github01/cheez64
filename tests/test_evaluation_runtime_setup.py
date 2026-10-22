"""Runtime configuration diagnostics: temporary files only; never launch app/services."""
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.evaluations import router
from src.evaluation_cache import EvaluationCache
from src.evaluation_models import Evaluation, PV
from test_evaluation_cache import config, context


@pytest.fixture
def client(monkeypatch):
    for key in ('CHESS_COACH_EVAL_CACHE_DB','CHESS_COACH_EVAL_LIMITS','CHESS_COACH_EVAL_CLOUD_ENABLED',
                'CHESS_COACH_EVAL_RESOLVE_KEY','CHESS_COACH_READ_ONLY'):
        monkeypatch.delenv(key, raising=False)
    app=FastAPI();app.include_router(router)
    return TestClient(app)


def lookup(client):
    return client.post('/api/evaluations/lookup-batch',json={'items':[{'context':context().model_dump(),'allow_advisory':True,'allow_partial':True}]}).json()['items'][0]


def assert_reason(client, code):
    value=lookup(client);assert value['status']=='UNAVAILABLE' and value['evaluation'] is None
    assert value['reason']==code
    assert client.get('/api/evaluations/status').json()['reason']==code


def test_unconfigured_does_not_initialize_or_read_library(client):
    with patch('src.library.GameLibrary.__init__',side_effect=AssertionError('personal library')), \
         patch.object(EvaluationCache,'initialize',side_effect=AssertionError('initialization')), \
         patch('src.evaluation_cloud.CloudResolver.resolve',side_effect=AssertionError('cloud')):
        assert_reason(client,'CACHE_NOT_CONFIGURED')


def test_missing_limits_path_and_schema_are_distinct(client,tmp_path,monkeypatch):
    cachepath=(tmp_path/'absent.sqlite3').resolve()
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(cachepath))
    assert_reason(client,'CACHE_FILE_MISSING'); assert not cachepath.exists()
    monkeypatch.setenv('CHESS_COACH_EVAL_LIMITS','{"max_records":0}')
    assert_reason(client,'CACHE_LIMITS_INVALID'); assert not cachepath.exists()
    monkeypatch.delenv('CHESS_COACH_EVAL_LIMITS')
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(tmp_path.resolve()))
    assert_reason(client,'CACHE_PATH_REFUSED')
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(Path('data/library.db').resolve()))
    assert_reason(client,'CACHE_PATH_REFUSED')  # Path refusal precedes any SQLite open.
    with sqlite3.connect(cachepath) as conn:conn.execute('CREATE TABLE unrelated (id INTEGER)')
    before=cachepath.read_bytes();monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(cachepath))
    assert_reason(client,'CACHE_SCHEMA_INVALID');assert cachepath.read_bytes()==before
    cachepath.write_bytes(b'not sqlite')
    assert_reason(client,'CACHE_DATABASE_ERROR');assert cachepath.read_bytes()==b'not sqlite'


def test_empty_and_wrong_version_are_not_the_same(client,tmp_path,monkeypatch):
    path=(tmp_path/'cache.sqlite3').resolve();c=EvaluationCache(path);c.initialize()
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(path))
    before=path.read_bytes()
    with patch.object(EvaluationCache,'initialize',side_effect=AssertionError('DDL')), \
         patch('chess.engine.SimpleEngine.popen_uci',side_effect=AssertionError('engine')):
        value=lookup(client);assert value['status']=='CACHE_MISS' and value['evaluation'] is None
        status=client.get('/api/evaluations/status').json()
        assert status['status']=='OK' and status['counts']['eval_records']==0
    assert path.read_bytes()==before
    with sqlite3.connect(path) as conn:conn.execute('UPDATE eval_cache_meta SET schema_version=999')
    before=path.read_bytes();assert_reason(client,'CACHE_VERSION_INCOMPATIBLE');assert path.read_bytes()==before


@pytest.mark.parametrize('count',[1,2,3])
def test_cached_numeric_score_and_actual_pv_counts(client,tmp_path,monkeypatch,count):
    path=(tmp_path/'cache.sqlite3').resolve();c=EvaluationCache(path);c.initialize()
    c.ingest(Evaluation(context=context(),config=config(multipv=count),achieved_depth=16,
        pvs=[PV(kind='cp',cp=35-i*10,moves=[move]) for i,move in enumerate(['e2e4','d2d4','g1f3'][:count])]))
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(path))
    with patch('src.evaluation_cloud.CloudResolver.resolve',side_effect=AssertionError('cloud')), \
         patch('chess.engine.SimpleEngine.popen_uci',side_effect=AssertionError('engine')):
        result=lookup(client)
    assert result['status']=='CACHE_HIT' and result['evaluation']['pvs'][0]['cp']==35
    assert len(result['evaluation']['pvs'])==count and result['evaluation']['returned_multipv']==count


def test_read_failure_is_not_reported_as_uninitialized(client,tmp_path,monkeypatch):
    path=(tmp_path/'cache.sqlite3').resolve();c=EvaluationCache(path);c.initialize()
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(path))
    secret='private-sql-text-do-not-expose'
    with patch.object(EvaluationCache,'lookup',side_effect=sqlite3.OperationalError(secret)):
        result=lookup(client)
    assert result['reason']=='CACHE_DATABASE_ERROR' and secret not in json.dumps(result)
    with patch.object(EvaluationCache,'status',side_effect=PermissionError(secret)):
        result=client.get('/api/evaluations/status').json()
    assert result['reason']=='CACHE_IO_ERROR' and secret not in json.dumps(result)
    busy=sqlite3.OperationalError('locked');busy.sqlite_errorcode=sqlite3.SQLITE_BUSY
    with patch.object(EvaluationCache,'lookup',side_effect=busy):assert lookup(client)['reason']=='CACHE_BUSY'


def test_status_explains_cloud_and_authorization_without_disclosing_key(client,monkeypatch):
    result=client.get('/api/evaluations/status').json()
    assert not result['cloud_enabled'] and not result['read_only'] and not result['cloud_operator_key_configured']
    monkeypatch.setenv('CHESS_COACH_EVAL_CLOUD_ENABLED','true')
    result=client.get('/api/evaluations/status').json()
    assert result['cloud_enabled'] and not result['cloud_resolution_allowed']
    monkeypatch.setenv('CHESS_COACH_EVAL_RESOLVE_KEY','private-test-key-at-least-16')
    monkeypatch.setenv('CHESS_COACH_READ_ONLY','true')
    result=client.get('/api/evaluations/status').json()
    assert result['read_only'] and result['cloud_operator_key_configured'] and not result['cloud_resolution_allowed']
    assert 'private-test-key' not in json.dumps(result)


def test_launcher_warning_only_checks_metadata(tmp_path,monkeypatch):
    text=Path('run.sh').read_text()
    function=re.search(r'check_evaluation_setup\(\) \{.*?\n\}',text,re.S).group()
    monkeypatch.delenv('CHESS_COACH_EVAL_CACHE_DB',raising=False)
    shell='warn() { echo "$*"; }; '+function+'\ncheck_evaluation_setup'
    result=subprocess.run(['bash','-c',shell],capture_output=True,text=True,check=True)
    assert 'not configured' in result.stdout
    target=(tmp_path/'missing.sqlite3').resolve();monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(target))
    result=subprocess.run(['bash','-c',shell],capture_output=True,text=True,check=True)
    assert 'file is missing' in result.stdout and not target.exists()
