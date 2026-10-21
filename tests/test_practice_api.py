import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from src.library import GameLibrary
from src.practice_store import PracticeStore
from test_practice import PGN, ACCOUNT

HEADERS = {'x-practice-action': 'local-practice', 'origin': 'http://localhost:3000'}


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = (tmp_path/'library.db').resolve()
    with GameLibrary(str(path)) as lib: game, _ = lib.add_game(PGN, source='lichess')
    from api import main
    monkeypatch.setattr(main, 'DB_PATH', str(path))
    monkeypatch.setenv('CHESS_COACH_DB', str(path))
    monkeypatch.setenv('CHESS_COACH_PRACTICE_DB', str((tmp_path/'practice.db').resolve()))
    monkeypatch.setenv('CHESS_COACH_PRACTICE_WRITES', 'true')
    monkeypatch.setenv('CHESS_COACH_READ_ONLY', 'false')
    store = PracticeStore((tmp_path/'practice.db').resolve(), path); store.initialize()
    with TestClient(main.app) as client:
        yield client, store, game.game_id


def discover(client, gid):
    return client.post('/api/practice/discover', json={'account': ACCOUNT, 'games': [{'game_id': gid,
        'expected_pgn_sha256': hashlib.sha256(PGN.encode()).hexdigest(), 'plies': [6]}]}, headers=HEADERS)


def test_api_end_to_end_and_no_engine_calls(api, monkeypatch):
    client, store, gid = api
    import src.progressive_store, src.progressive_engine, httpx
    def forbidden(*a, **k): pytest.fail('Engine/network/progressive mutation invoked')
    monkeypatch.setattr(src.progressive_engine, 'UCIProcess', forbidden)
    for name in ('initialize', 'enqueue', 'prepare', 'claim', 'flush_outbox'):
        monkeypatch.setattr(src.progressive_store.ProgressiveStore, name, forbidden)
    monkeypatch.setattr(httpx.AsyncClient, 'request', forbidden)
    response = discover(client, gid)
    assert response.status_code == 200, response.text
    q = response.json()['items'][0]
    for path in (f'/api/practice/puzzles/{q["id"]}', '/api/practice/puzzles?source=lichess&username=ALICE'):
        answer = client.get(path)
        assert answer.status_code == 200 and 'h5f7' not in answer.text
    start = client.post('/api/practice/attempts', json={'puzzle_id': q['id'], 'request_key': 's'}, headers=HEADERS)
    assert start.status_code == 200 and 'h5f7' not in start.text
    a = start.json()['attempt']
    win = client.post(f'/api/practice/attempts/{a["id"]}/events', json={'kind': 'move', 'uci': 'h5f7', 'revision': 0, 'request_key': 'win'}, headers=HEADERS)
    assert win.status_code == 200 and win.json()['attempt']['state'] == 'OBJECTIVE_MET'
    assert client.get(f'/api/practice/attempts/{a["id"]}').json()['attempt']['state'] == 'OBJECTIVE_MET'
    assert len(client.get('/api/practice/history?source=lichess&username=Alice').json()['items']) == 1
    assert store.status()['counts']['attempt_events'] == 1


@pytest.mark.parametrize('headers', [{}, {'origin': 'https://evil.example', 'x-practice-action': 'local-practice'},
                                       {'host': 'evil.example', 'x-practice-action': 'local-practice'},
                                       {'x-forwarded-for': '127.0.0.1'}])
def test_write_origin_host_and_action_guard(api, headers):
    client, _, _ = api
    assert client.post('/api/practice/attempts', json={'puzzle_id': 'missing', 'request_key': 's'}, headers=headers).status_code == 403


def test_readonly_and_disabled_missing_store(api, monkeypatch, tmp_path):
    client, _, gid = api
    missing = (tmp_path/'absent.db').resolve()
    monkeypatch.setenv('CHESS_COACH_PRACTICE_DB', str(missing))
    assert client.get('/api/practice/status').json()['status'] == 'UNAVAILABLE'
    assert not missing.exists()
    monkeypatch.setenv('CHESS_COACH_READ_ONLY', 'true')
    assert discover(client, gid).status_code == 403
    monkeypatch.setenv('CHESS_COACH_READ_ONLY', 'false')
    monkeypatch.setenv('CHESS_COACH_PRACTICE_WRITES', 'false')
    assert discover(client, gid).status_code == 403


def test_body_bounds_and_source_hash(api):
    client, _, gid = api
    assert client.post('/api/practice/discover', content=' '*65537, headers={**HEADERS, 'content-type': 'application/json'}).status_code == 413
    payload = {'account': ACCOUNT, 'games': [{'game_id': gid, 'expected_pgn_sha256': '0'*64, 'plies': [6]}]}
    assert client.post('/api/practice/discover', json=payload, headers=HEADERS).status_code == 409
    payload['games'][0]['plies'] = [True]
    assert client.post('/api/practice/discover', json=payload, headers=HEADERS).status_code == 422
