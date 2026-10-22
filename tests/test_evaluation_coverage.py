"""Small temporary-cache preference contracts; all HTTP mocked, no engines."""
import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.evaluations import router
from src.evaluation_cache import EvaluationCache
from src.evaluation_cloud import CloudResolver, parse_cloud
from src.evaluation_models import Lookup, SearchConfig, Source, digest
from test_evaluation_cloud import payload
from test_evaluation_cache import context


@pytest.fixture
def cache(tmp_path):
    store = EvaluationCache((tmp_path / 'cache.db').resolve())
    store.initialize()
    return store


def query(**extra):
    return Lookup(context=context(), source='lichess_cloud', allow_advisory=True,
                  allow_partial=True, allow_cloud=True, cloud_multipv=3,
                  prefer_cloud_pvs=True, **extra)


def seed(cache, count, requested=None, depth=20):
    pvs = [{'cp': 30-i*10, 'moves': move} for i, move in enumerate(['e2e4', 'd2d4', 'g1f3'][:count])]
    q = query().model_copy(update={'cloud_multipv': requested or count})
    cache.ingest(parse_cloud(payload(depth=depth, pvs=pvs), q))


def resolve(cache, response, q=None, enabled=True):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.params['multiPv'] == '3'
        return response
    value = asyncio.run(CloudResolver(cache, enabled, httpx.MockTransport(handler)).resolve(q or query()))
    return value, calls


@pytest.mark.parametrize('count', [1, 2, 3])
def test_explicit_upgrade_reuses_single_record_without_fictional_lines(cache, count):
    seed(cache, 1)
    lines = [{'cp': 20, 'moves': move} for move in ['e2e4', 'd2d4', 'g1f3'][:count]]
    value, calls = resolve(cache, httpx.Response(200, json=payload(pvs=lines)))
    assert len(calls) == 1
    assert value['evaluation']['returned_multipv'] == count
    assert value['pv_goal_met'] is (count == 3)
    assert value['evaluation']['context_compatibility'] == 'ADVISORY_CONTEXT_UNVERIFIED'
    assert value['requested_pvs'] == 3
    if count == 3:
        again, calls = resolve(cache, httpx.Response(500))
        assert not calls and again['pv_goal_met']


def test_three_lines_preferred_over_deeper_partial_same_configuration(cache):
    seed(cache, 1, requested=3, depth=30)
    seed(cache, 3, requested=3, depth=12)
    value, calls = resolve(cache, httpx.Response(500))
    assert not calls and value['evaluation']['achieved_depth'] == 12
    assert value['evaluation']['returned_multipv'] == 3


def test_missing_upgrade_preserves_one_line_and_negative_cache(cache):
    seed(cache, 1)
    result, calls = resolve(cache, httpx.Response(404))
    assert len(calls) == 1 and result['evaluation']['returned_multipv'] == 1
    assert result['status'] == 'CACHE_HIT' and not result['pv_goal_met']
    assert result['cloud_resolution_status'] == 'CLOUD_MISS'
    result, calls = resolve(cache, httpx.Response(500))
    assert not calls and result['cloud_resolution_status'] == 'CLOUD_MISS'


@pytest.mark.parametrize('code,status', [(429, 'RATE_LIMITED'), (500, 'UNAVAILABLE')])
def test_failed_upgrade_retains_cached_evidence_and_respects_cooldown(cache, code, status):
    seed(cache, 2)
    result, calls = resolve(cache, httpx.Response(code))
    assert len(calls) == 1 and result['evaluation']['returned_multipv'] == 2
    assert result['cloud_resolution_status'] == status and not result['pv_goal_met']
    again, calls = resolve(cache, httpx.Response(200, json=payload()))
    assert not calls and again['cloud_resolution_status'] == 'DEFERRED'


def test_explicit_config_constraint_not_overridden(cache):
    seed(cache, 1)
    cfg = SearchConfig(source=Source(kind='lichess_cloud', provenance='https://lichess.org/api/cloud-eval'), limit_kind='cloud', multipv=1)
    result, calls = resolve(cache, httpx.Response(200, json=payload()), query(config_hash=digest(cfg.model_dump())))
    assert not calls and result['evaluation']['config']['multipv'] == 1
    assert result['cloud_resolution_status'] == 'INCOMPATIBLE' and not result['pv_goal_met']


def test_disabled_preference_cache_reuse_without_initialization_or_network(cache):
    seed(cache, 1)
    with patch('src.library.GameLibrary.__init__', side_effect=AssertionError('library')), \
         patch('chess.engine.SimpleEngine.popen_uci', side_effect=AssertionError('engine')), \
         patch.object(EvaluationCache, 'initialize', side_effect=AssertionError('migration')):
        result, calls = resolve(cache, httpx.Response(200, json=payload()), enabled=False)
    assert not calls and result['evaluation']['returned_multipv'] == 1
    assert result['cloud_resolution_status'] == 'CLOUD_DISABLED'


@pytest.mark.parametrize('update', [{'allow_cloud':False}, {'allow_advisory':False}, {'require_trusted':True}, {'source':'local_stockfish'}])
def test_preference_cannot_enable_trusted_or_implicit_cloud(update):
    values=query().model_dump(); values.update(update)
    with pytest.raises(ValidationError): Lookup.model_validate(values)


def test_status_capability_no_key_disclosure(cache, monkeypatch):
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB', str(cache.path))
    monkeypatch.setenv('CHESS_COACH_EVAL_CLOUD_ENABLED', 'true')
    monkeypatch.setenv('CHESS_COACH_EVAL_RESOLVE_KEY', 'synthetic-operator-key')
    app=FastAPI(); app.include_router(router); client=TestClient(app)
    for readonly, expected in [('true', False), ('false', True)]:
        monkeypatch.setenv('CHESS_COACH_READ_ONLY', readonly)
        value=client.get('/api/evaluations/status').json()
        assert value['cloud_resolution_allowed'] is expected
        assert 'synthetic-operator-key' not in str(value)
    monkeypatch.delenv('CHESS_COACH_EVAL_RESOLVE_KEY')
    assert not client.get('/api/evaluations/status').json()['cloud_resolution_allowed']
