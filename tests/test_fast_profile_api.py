import hashlib
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from src.library import GameLibrary
from src.fast_profile import build_fast_profile
from test_fast_profile import pgn


@pytest.fixture
def env(tmp_path, monkeypatch):
    import api.main as api
    path = tmp_path/'api-profile.db'
    with GameLibrary(str(path)) as lib:
        game, _ = lib.add_game(pgn())
        lib.attach_analysis(game.game_id, {'metadata': {}, 'moves': [], 'summary': {}})
    monkeypatch.setattr(api, 'DB_PATH', str(path))
    return TestClient(api.app), path


@pytest.mark.parametrize('route', ['fast-profile', 'overview', 'trends', 'repertoire', 'time-management', 'tendencies', 'coverage'])
def test_all_endpoints_readonly_and_engine_free(env, route):
    client, path = env
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with patch('src.library.GameLibrary.__init__', side_effect=AssertionError('schema writes')), \
         patch('chess.engine.SimpleEngine.popen_uci', side_effect=AssertionError('engine search')), \
         patch('src.library.GameLibrary.get_analysis', side_effect=AssertionError('engine data read')):
        response = client.get('/api/players/'+route, params={'source': 'local', 'username': 'Alice'})
    assert response.status_code == 200, response.text
    r = response.json()
    assert r['selector']['matching'] == 'exact_case_sensitive'
    assert r['coverage']['eligible_games'] == 1
    assert r['engine_analysis_availability']['games_with_stored_analysis'] == 1
    assert r['engine_analysis_availability']['not_used_by_fast_profile']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    if route == 'fast-profile':
        assert r['overview']['outcomes']['win_rate']['value'] is None
        assert all(k in r for k in ('overview', 'trends', 'repertoire', 'time_management', 'move_tendencies', 'positions'))


@pytest.mark.parametrize('params,status', [({}, 422), ({'source': 'local', 'username': 'Alice2'}, 404),
    ({'source': 'local', 'username': 'Alice', 'max_games': 0}, 422),
    ({'source': 'local', 'username': 'Alice', 'max_seconds': 'nan'}, 422),
    ({'source': 'local', 'username': 'Alice', 'date_to': '2024-02-31'}, 422),
    ({'source': 'local', 'username': 'Alice', 'color': 'red'}, 422)])
def test_validation(env, params, status):
    assert env[0].get('/api/players/fast-profile', params=params).status_code == status


def test_missing_database_no_create(env, tmp_path, monkeypatch):
    import api.main as api
    path = tmp_path/'absent.db'
    monkeypatch.setattr(api, 'DB_PATH', str(path))
    assert env[0].get('/api/players/overview', params={'source': 'local', 'username': 'Alice'}).status_code == 503
    assert not path.exists()


def test_response_limits_do_not_limit_total_games(env):
    client, path = env
    with GameLibrary(str(path)) as lib:
        for i in range(12): lib.add_game(pgn(day=f'2024.{1+i:02}.01'))
    r = client.get('/api/players/fast-profile', params={'source': 'local', 'username': 'Alice', 'top_k': 1}).json()
    # The first record duplicates January; total is 12, not the display limit.
    assert r['coverage']['eligible_games'] == r['coverage']['replayed_games'] == 12
    assert len(r['trends']['monthly']) == 1 and r['trends']['monthly_bucket_count'] == 12


def test_incompatible_index_is_a_service_error(env):
    client, path = env
    import sqlite3
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE position_index_meta SET identity_version=99')
    assert client.get('/api/players/fast-profile', params={'source': 'local', 'username': 'Alice'}).status_code == 503
    # Metadata-only analytics remain available without using the position index.
    assert client.get('/api/players/overview', params={'source': 'local', 'username': 'Alice'}).status_code == 200
