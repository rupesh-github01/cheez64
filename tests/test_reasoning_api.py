import hashlib
import pytest
from fastapi.testclient import TestClient
from src.library import GameLibrary
from src.reasoning_import import ArtifactStore
from tests.reasoning_fixtures import PGN, bundle


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = (tmp_path/'library.db').resolve()
    with GameLibrary(str(path)) as lib: game, _ = lib.add_game(PGN)
    store = ArtifactStore((tmp_path/'artifacts').resolve());identifier = store.accept(bundle(), synthetic=True)
    from api import main
    monkeypatch.setattr(main, 'DB_PATH', str(path))
    monkeypatch.setenv('CHESS_COACH_DB', str(path));monkeypatch.setenv('CHESS_COACH_READ_ONLY', 'true')
    monkeypatch.setenv('CHESS_COACH_REASONING_DIR', str(store.directory));monkeypatch.setenv('CHESS_COACH_REASONING_ALLOW_SYNTHETIC', 'true')
    url = f'/api/games/{game.game_id}/reasoning?ply=2&expected_pgn_sha256={hashlib.sha256(PGN.encode()).hexdigest()}'
    with TestClient(main.app) as client: yield client, path, store, identifier, url


def test_readonly_api_and_no_engine_provider_or_store_writes(api, monkeypatch):
    client, path, store, identifier, url = api
    import src.progressive_engine,src.progressive_store,httpx
    def forbidden(*a, **k): pytest.fail('Forbidden execution or mutation')
    monkeypatch.setattr(src.progressive_engine, 'UCIProcess', forbidden)
    for name in ('initialize', 'enqueue', 'prepare', 'advance', 'claim', 'flush_outbox'):
        monkeypatch.setattr(src.progressive_store.ProgressiveStore, name, forbidden)
    monkeypatch.setattr(GameLibrary, '_init_db', forbidden)
    monkeypatch.setattr(ArtifactStore, 'accept', forbidden);monkeypatch.setattr(httpx.AsyncClient, 'request', forbidden)
    before = path.read_bytes(); files = {p.name:p.read_bytes() for p in store.directory.iterdir() if p.is_file()}
    response = client.get(url+'&bundle_id='+identifier+'&alternative_uci=b1c3')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'AVAILABLE'
    available = client.get(url.replace('/reasoning?', '/reasoning/availability?'))
    assert available.status_code == 200 and available.json()['bundles'][0]['id'] == identifier
    assert client.post(url, json={}).status_code == 403
    assert path.read_bytes() == before
    assert {p.name:p.read_bytes() for p in store.directory.iterdir() if p.is_file()} == files


def test_api_missing_invalid_and_stale(api, monkeypatch, tmp_path):
    client, path, store, identifier, url = api
    assert client.get(url+'&alternative_uci=e2e5').status_code == 422
    assert client.get(url.replace('expected_pgn_sha256=', 'expected_pgn_sha256=0')).status_code == 422
    import re
    stale = re.sub(r'expected_pgn_sha256=[a-f0-9]+', 'expected_pgn_sha256='+'0'*64, url)
    assert client.get(stale).status_code == 409
    monkeypatch.setenv('CHESS_COACH_REASONING_ALLOW_SYNTHETIC', 'false')
    assert client.get(url+'&bundle_id='+identifier).json()['status'] == 'INSUFFICIENT_EVIDENCE'
    monkeypatch.setenv('CHESS_COACH_REASONING_DIR', str((tmp_path/'missing-artifacts').resolve()))
    value = client.get(url+'&bundle_id='+identifier+'&alternative_uci=g1f3').json()
    assert value['status'] == 'INCOMPATIBLE' and value['checkpoints']
    assert not (tmp_path/'missing-artifacts').exists()
