"""
Tests for the read-only FastAPI chess coach API.
Uses FastAPI TestClient backed by a temp-file SQLite database.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

SAMPLE_PGN = """[Event "Test"]
[White "Alice"]
[Black "Bob"]
[Result "1-0"]
[Date "2024.01.15"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1-0
"""

SAMPLE_ANALYSIS = {
    "schema_version": "1.1.0",
    "metadata": {"white": "Alice", "black": "Bob", "result": "1-0"},
    "summary": {
        "total_plies": 6,
        "critical_positions_count": 2,
        "critical_episodes_count": 1,
    },
    "moves": [
        {
            "ply": 1,
            "move_number": 1,
            "color": "White",
            "played_move": "e4",
            "played_move_uci": "e2e4",
            "best_move": "d4",
            "centipawn_loss": 5,
            "classification": "good",
            "is_critical": False,
            "episode_index": None,
            "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "fen_after": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            "tactical_finding": None,
            "explanation": {
                "summary": "e4 was solid.",
                "what_happened": "Played e4.",
                "why_it_matters": "Central control.",
                "better_move": "d4",
                "variation": "d4 d5",
                "motif": "good_move",
                "confidence": "medium",
                "limitations": None,
            },
            "position_features": {
                "piece_counts": {
                    "white": {"queen": 1, "pawn": 8, "knight": 2, "bishop": 2, "rook": 2, "king": 1},
                    "black": {"queen": 1, "pawn": 8, "knight": 2, "bishop": 2, "rook": 2, "king": 1},
                },
                "material": {"white": 39, "black": 39},
            },
        },
        {
            "ply": 3,
            "move_number": 2,
            "color": "White",
            "played_move": "Nf3",
            "played_move_uci": "g1f3",
            "best_move": "Nc3",
            "centipawn_loss": 250,
            "classification": "blunder",
            "is_critical": True,
            "episode_index": 0,
            "fen_before": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            "fen_after": "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
            "tactical_finding": {
                "category": "material_loss",
                "confidence": "high",
                "evidence": {},
            },
            "explanation": {
                "summary": "Blunder – lost a piece.",
                "what_happened": "Played Nf3 losing a piece.",
                "why_it_matters": "Dropped material.",
                "better_move": "Nc3",
                "variation": "Nc3 d6",
                "motif": "material_loss",
                "confidence": "high",
                "limitations": None,
            },
            "position_features": {
                "piece_counts": {
                    "white": {"queen": 1, "pawn": 8, "knight": 2, "bishop": 2, "rook": 2, "king": 1},
                    "black": {"queen": 1, "pawn": 8, "knight": 2, "bishop": 2, "rook": 2, "king": 1},
                },
                "material": {"white": 39, "black": 39},
            },
        },
        {
            "ply": 5,
            "move_number": 3,
            "color": "White",
            "played_move": "Bb5",
            "played_move_uci": "f1b5",
            "best_move": "Bc4",
            "centipawn_loss": 80,
            "classification": "inaccuracy",
            "is_critical": True,
            "episode_index": None,
            "fen_before": "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3",
            "fen_after": "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
            "tactical_finding": None,
            "explanation": {
                "summary": "Inaccuracy – Bb5 was imprecise.",
                "what_happened": "Played Bb5.",
                "why_it_matters": "Bc4 was sharper.",
                "better_move": "Bc4",
                "variation": "Bc4 Nf6",
                "motif": "positional_inaccuracy",
                "confidence": "medium",
                "limitations": None,
            },
            "position_features": {
                "piece_counts": {
                    "white": {"queen": 1, "pawn": 8, "knight": 1, "bishop": 2, "rook": 2, "king": 1},
                    "black": {"queen": 1, "pawn": 8, "knight": 2, "bishop": 2, "rook": 2, "king": 1},
                },
                "material": {"white": 36, "black": 39},
            },
        },
    ],
    "episodes": [
        {
            "episode_index": 0,
            "start_ply": 3,
            "end_ply": 3,
            "category": "material_loss",
            "moves": [{"ply": 3, "move_number": 2, "color": "White"}],
        }
    ],
}

SAMPLE_OPENING = {
    "opening_name": "Ruy Lopez",
    "variation_name": "Morphy Defense",
    "eco": "C60",
    "full_name": "Ruy Lopez: Morphy Defense",
    "matched_ply": 5,
    "theory_exit_ply": 6,
    "theory_exit_move": "a6",
    "divergence_side": "Black",
    "is_transposition": False,
    "confidence": "high",
}


# ---------------------------------------------------------------------------
# Fixtures — seed a temp-file SQLite database once per module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded_client():
    """
    Create a temporary SQLite DB, seed it with one game+analysis+opening,
    point the API env var at it, and return a TestClient + game_id.
    """
    from src.library import GameLibrary

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        # Seed data
        lib = GameLibrary(db_path)
        game, _ = lib.add_game(SAMPLE_PGN)
        lib.attach_analysis(game.game_id, SAMPLE_ANALYSIS)
        lib.set_game_opening(game.game_id, SAMPLE_OPENING)
        lib.close()

        # Point API at the seeded DB and reimport so it picks up the env var
        os.environ["CHESS_COACH_DB"] = db_path
        import importlib
        import api.main as api_module
        importlib.reload(api_module)

        from fastapi.testclient import TestClient
        client = TestClient(api_module.app)
        yield client, game.game_id, db_path, api_module

    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


@pytest.fixture(scope="module")
def bare_client(seeded_client):
    """Client + bare game_id (game with no analysis or opening)."""
    _, _, db_path, api_module = seeded_client
    from src.library import GameLibrary

    lib = GameLibrary(db_path)
    bare_game, _ = lib.add_game(
        """[Event "Bare"][White "X"][Black "Y"][Result "*"]\n1. d4 *"""
    )
    lib.close()

    from fastapi.testclient import TestClient
    client = TestClient(api_module.app)
    return client, bare_game.game_id


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Game list
# ---------------------------------------------------------------------------

class TestGameList:
    def test_list_returns_games(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get("/api/games")
        assert r.status_code == 200
        body = r.json()
        assert "games" in body
        assert body["total"] >= 1
        assert any(g["game_id"] == game_id for g in body["games"])

    def test_list_includes_opening(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get("/api/games")
        assert r.status_code == 200
        game = next(g for g in r.json()["games"] if g["game_id"] == game_id)
        assert game["opening"] is not None
        assert game["opening"]["eco"] == "C60"
        assert game["opening"]["opening_name"] == "Ruy Lopez"

    def test_list_includes_analysis_status(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get("/api/games")
        game = next(g for g in r.json()["games"] if g["game_id"] == game_id)
        assert game["analysis_status"] == "ANALYZED"

    def test_filter_by_player(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games?player=Alice")
        assert r.status_code == 200
        games = r.json()["games"]
        assert len(games) >= 1
        assert all("Alice" in g["white"] or "Alice" in g["black"] for g in games)

    def test_filter_by_result(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games?result=1-0")
        assert r.status_code == 200
        games = r.json()["games"]
        assert all(g["result"] == "1-0" for g in games)

    def test_filter_by_analysis_status(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games?analysis_status=ANALYZED")
        assert r.status_code == 200
        for g in r.json()["games"]:
            assert g["analysis_status"] == "ANALYZED"

    def test_pagination_limit(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games?limit=1")
        assert r.status_code == 200
        assert len(r.json()["games"]) <= 1

    def test_response_has_pagination_fields(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games?limit=5&offset=0")
        body = r.json()
        assert "total" in body
        assert "offset" in body
        assert "limit" in body


# ---------------------------------------------------------------------------
# Single game
# ---------------------------------------------------------------------------

class TestGetGame:
    def test_get_existing_game(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["game_id"] == game_id
        assert body["white"] == "Alice"
        assert body["black"] == "Bob"
        assert body["opening"]["eco"] == "C60"

    def test_get_missing_game_returns_404(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games/nonexistent_game_id")
        assert r.status_code == 404

    def test_game_contains_pgn(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}")
        assert "pgn" in r.json()
        assert "1. e4" in r.json()["pgn"]


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

class TestGetAnalysis:
    def test_analysis_returns_moves_and_episodes(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/analysis")
        assert r.status_code == 200
        body = r.json()
        assert "moves" in body
        assert "episodes" in body
        assert len(body["moves"]) == 3

    def test_analysis_contains_explanation(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/analysis")
        move = r.json()["moves"][0]
        assert "explanation" in move
        assert "summary" in move["explanation"]

    def test_analysis_missing_returns_404(self, bare_client):
        client, bare_id = bare_client
        r = client.get(f"/api/games/{bare_id}/analysis")
        assert r.status_code == 404

    def test_analysis_nonexistent_game_returns_404(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games/no_game/analysis")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Moves endpoint
# ---------------------------------------------------------------------------

class TestGetMoves:
    def test_all_moves_returned(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/moves")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 3
        assert len(body["moves"]) == 3

    def test_color_filter(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/moves?color=White")
        assert r.status_code == 200
        for m in r.json()["moves"]:
            assert m["color"] == "White"

    def test_critical_only_filter(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/moves?critical_only=true")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        for m in body["moves"]:
            assert m["is_critical"] is True

    def test_category_tactical_filter(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/moves?category=tactical")
        assert r.status_code == 200
        moves = r.json()["moves"]
        assert len(moves) == 1
        assert moves[0]["tactical_finding"]["category"] == "material_loss"

    def test_category_positional_filter(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/moves?category=positional")
        assert r.status_code == 200
        moves = r.json()["moves"]
        for m in moves:
            tf = m.get("tactical_finding") or {}
            assert tf.get("category") not in {
                "material_loss", "hanging_piece", "missed_capture", "missed_check_or_forcing_move"
            }

    def test_moves_missing_game_returns_404(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games/no_game/moves")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Critical moves
# ---------------------------------------------------------------------------

class TestGetCritical:
    def test_critical_returns_count(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/critical")
        assert r.status_code == 200
        body = r.json()
        assert body["critical_count"] == 2

    def test_critical_episode_grouping(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/critical")
        body = r.json()
        assert len(body["episode_groups"]) == 1
        assert body["episode_groups"][0]["episode_index"] == 0
        assert len(body["standalone_critical"]) == 1

    def test_critical_missing_game_returns_404(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games/no_such_game/critical")
        assert r.status_code == 404

    def test_critical_response_shape(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/critical")
        body = r.json()
        assert "game_id" in body
        assert "critical_count" in body
        assert "episode_groups" in body
        assert "standalone_critical" in body


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------

class TestGetEpisodes:
    def test_episodes_returned(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/episodes")
        assert r.status_code == 200
        body = r.json()
        assert "episodes" in body
        assert len(body["episodes"]) == 1
        assert body["episodes"][0]["episode_index"] == 0

    def test_episodes_missing_analysis_returns_404(self, bare_client):
        client, bare_id = bare_client
        r = client.get(f"/api/games/{bare_id}/episodes")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------

class TestGetOpening:
    def test_opening_returned(self, seeded_client):
        client, game_id, *_ = seeded_client
        r = client.get(f"/api/games/{game_id}/opening")
        assert r.status_code == 200
        body = r.json()
        assert body["eco"] == "C60"
        assert body["opening_name"] == "Ruy Lopez"

    def test_opening_missing_game_returns_404(self, seeded_client):
        client, *_ = seeded_client
        r = client.get("/api/games/no_such_game/opening")
        assert r.status_code == 404

    def test_opening_not_detected_returns_404(self, bare_client):
        client, bare_id = bare_client
        r = client.get(f"/api/games/{bare_id}/opening")
        assert r.status_code == 404

    def test_opening_no_auto_detect(self, bare_client):
        """GET /opening must not trigger auto-detection (read-only)."""
        client, bare_id = bare_client
        r = client.get(f"/api/games/{bare_id}/opening")
        # Should still be 404 — no auto-detect happened
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Read-only contract
# ---------------------------------------------------------------------------

class TestReadOnlyContract:
    def test_no_mutating_methods(self, seeded_client):
        """Every route must expose only GET (+ HEAD/OPTIONS) methods."""
        _, _, _, api_module = seeded_client
        for route in api_module.app.routes:
            if hasattr(route, "methods"):
                non_get = route.methods - {"GET", "HEAD", "OPTIONS"}
                assert non_get == set(), (
                    f"Route {route.path} exposes non-GET method(s): {non_get}"
                )
