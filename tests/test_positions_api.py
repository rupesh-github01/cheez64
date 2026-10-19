import hashlib
import json
from pathlib import Path
import sqlite3

import chess
import pytest
from fastapi.testclient import TestClient

from src.library import GameLibrary
from src.position_cli import main as cli, safe_database, PROTECTED_DB
from src.positions import PositionIndex

FIXTURES = Path(__file__).parent / "data/positions"


@pytest.fixture
def env(tmp_path, monkeypatch):
    import api.main as api
    path = tmp_path / "api-positions.db"
    with GameLibrary(str(path)) as lib:
        game, _ = lib.add_game((FIXTURES / "repetition.pgn").read_text())
    monkeypatch.setattr(api, "DB_PATH", str(path))
    # No lifespan: existing app startup recovery intentionally performs writes.
    return TestClient(api.app), path, game.game_id


def index(path, game_id):
    with GameLibrary(str(path)) as lib:
        PositionIndex(lib._conn).index_game(game_id)


def test_partial_absence_and_validation(env):
    client, path, game_id = env
    response = client.get("/api/positions/lookup", params={"fen": chess.STARTING_FEN})
    assert response.status_code == 200
    found = response.json()
    assert found["position_id"] is None and found["occurrence_count"] == 0
    assert found["coverage"]["partial_index"]
    assert not found["coverage"]["absence_is_conclusive"]
    assert client.get("/api/positions/status").json()["coverage"]["pending_games"] == 1
    for args in [{"fen": "bad"}, {"fen": chess.STARTING_FEN, "variant": "chess960"},
                 {"fen": chess.STARTING_FEN, "player_color": "blue"}]:
        assert client.get("/api/positions/lookup", params=args).status_code == 422
    assert client.get("/api/positions/999").status_code == 404
    assert client.get("/api/positions/1/games", params={"limit": 101}).status_code == 422
    index(path, game_id)
    found = client.get("/api/positions/lookup", params={"fen": chess.STARTING_FEN}).json()
    assert found["coverage"]["absence_is_conclusive"]
    assert found["occurrence_count"] == 3
    pid = found["position_id"]
    assert client.get(f"/api/positions/{pid}").json()["distinct_game_count"] == 1
    assert client.get(f"/api/positions/{pid}/moves").json()["items"][0]["draws"] == 1
    assert client.get(f"/api/positions/{pid}/games").json()["items"][0]["occurrence_count"] == 3
    cursor, seen = {}, []
    while True:
        page = client.get(f"/api/positions/{pid}/occurrences", params={"limit": 1, **cursor}).json()
        seen.extend(r["ply"] for r in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == [0, 4, 8]
    assert client.get(f"/api/positions/{pid}", params={"player": "missing"}).json()["occurrence_count"] == 0


def test_api_never_initializes_missing_schema(env):
    client, path, _ = env
    with sqlite3.connect(path) as conn:
        for table in ("position_occurrences", "game_position_index", "positions", "position_index_meta"):
            conn.execute("DROP TABLE " + table)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    data = client.get("/api/positions/lookup", params={"fen": chess.STARTING_FEN}).json()
    assert not data["coverage"]["schema_available"]
    assert data["coverage"]["missing_games"] == 1
    assert data["coverage"]["partial_index"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='positions'").fetchall() == []


def test_game_page_cursor_and_exact_player_filter(env):
    client, path, game_id = env
    with GameLibrary(str(path)) as lib:
        for i in range(3):
            lib.add_game((FIXTURES / "repetition.pgn").read_text().replace('"Repeat"', f'"Repeat{i}"'))
        idx = PositionIndex(lib._conn)
        while g := idx.next_pending():
            idx.index_game(g)
    pid = client.get("/api/positions/lookup", params={"fen": chess.STARTING_FEN}).json()["position_id"]
    seen, cursor = [], ""
    while True:
        r = client.get(f"/api/positions/{pid}/games", params={"limit": 1, "after_game": cursor}).json()
        seen.extend(g["game_id"] for g in r["items"])
        cursor = r["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(set(seen)) and len(seen) == 4
    r = client.get(f"/api/positions/{pid}/moves", params={"player": "repeat", "player_color": "white"}).json()
    assert r["items"][0]["distinct_game_count"] == 1
    assert r["coverage"]["total_games"] == 1


def test_cli_resume_reindex_and_production_guard(env, capsys, tmp_path):
    _, path, game_id = env
    base = ["--db", str(path)]
    cli(["run", *base, "--once"])
    assert json.loads(capsys.readouterr().out)["processed"] == 1
    cli(["reindex", *base, "--game-id", game_id])
    assert json.loads(capsys.readouterr().out)["coverage"]["pending_games"] == 1
    cli(["backfill", *base, "--max-games", "1"])
    assert json.loads(capsys.readouterr().out)["coverage"]["indexed_games"] == 1
    cli(["status", *base])
    assert json.loads(capsys.readouterr().out)["coverage"]["complete_games"] == 1
    with pytest.raises(ValueError, match="protected"):
        safe_database(PROTECTED_DB)
    alias = tmp_path / "alias.db"
    alias.symlink_to(PROTECTED_DB)
    with pytest.raises(ValueError, match="protected"):
        safe_database(alias)
    with pytest.raises(SystemExit):
        cli(["status", "--db", str(PROTECTED_DB)])


def test_missing_file_does_not_create_database(env, monkeypatch, tmp_path):
    client, _, _ = env
    import api.main as api
    path = tmp_path / "nonexistent.db"
    monkeypatch.setattr(api, "DB_PATH", str(path))
    assert client.get("/api/positions/status").status_code == 503
    assert not path.exists()


def test_path_id_bounds_and_cli_finite_budget(env):
    client, path, _ = env
    assert client.get('/api/positions/9223372036854775808').status_code == 422
    for seconds in ('nan', 'inf', '-1'):
        with pytest.raises(SystemExit):
            cli(['run', '--db', str(path), '--max-seconds', seconds])


def test_cli_backfill_discovers_in_batches_not_per_game(env, capsys, monkeypatch):
    _, path, game_id = env
    from src.positions import PositionIndex
    with GameLibrary(str(path)) as lib:
        for i in range(4):
            lib.add_game((FIXTURES / "repetition.pgn").read_text().replace('"Repeat"', f'"Old{i}"'))
        with lib._conn:
            lib._conn.execute('DELETE FROM game_position_index')
    calls = []
    reconcile = PositionIndex.reconcile
    def tracking(self, limit=100):
        calls.append(limit)
        return reconcile(self, limit)
    monkeypatch.setattr(PositionIndex, 'reconcile', tracking)
    result = cli(['backfill', '--db', str(path), '--batch-size', '2', '--max-games', '5'])
    capsys.readouterr()
    assert calls == [2, 2, 1]
    assert result['processed'] == result['reconciled'] == 5
    assert not result['coverage']['partial_index']
