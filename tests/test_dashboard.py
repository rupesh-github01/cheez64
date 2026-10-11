"""
Tests for Player Dashboard & Statistics aggregation and API endpoint.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import GameLibrary
from src.dashboard import build_dashboard_data, resolve_target_player
from api.main import app


SAMPLE_PGN_1 = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.10.01"]
[Round "?"]
[White "DrCheezFighter"]
[Black "OpponentA"]
[Result "1-0"]
[WhiteElo "1500"]
[BlackElo "1480"]
[ECO "C00"]

1. e4 e6 2. Nf3 d5 1-0
"""

SAMPLE_PGN_2 = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.10.02"]
[Round "?"]
[White "OpponentB"]
[Black "DrCheezFighter"]
[Result "1-0"]
[WhiteElo "1520"]
[BlackElo "1495"]
[ECO "C44"]

1. e4 e5 2. Nf3 Nc6 1-0
"""

SAMPLE_ANALYSIS_1 = {
    "schema_version": "1.1.0",
    "metadata": {
        "white": "DrCheezFighter",
        "black": "OpponentA",
        "result": "1-0",
        "date": "2026.10.01",
        "eco": "C00",
    },
    "summary": {
        "total_plies": 4,
        "critical_positions_count": 0,
        "critical_episodes_count": 0,
    },
    "moves": [
        {
            "ply": 1,
            "move_number": 1,
            "color": "White",
            "played_move": "e4",
            "best_move": "e4",
            "centipawn_loss": 0,
            "classification": "good",
            "is_critical": False,
        },
        {
            "ply": 2,
            "move_number": 1,
            "color": "Black",
            "played_move": "e6",
            "best_move": "e6",
            "centipawn_loss": 5,
            "classification": "good",
            "is_critical": False,
        },
        {
            "ply": 3,
            "move_number": 2,
            "color": "White",
            "played_move": "Nf3",
            "best_move": "d4",
            "centipawn_loss": 12,
            "classification": "good",
            "is_critical": False,
        },
        {
            "ply": 4,
            "move_number": 2,
            "color": "Black",
            "played_move": "d5",
            "best_move": "d5",
            "centipawn_loss": 8,
            "classification": "good",
            "is_critical": False,
        },
    ],
    "episodes": [],
}

SAMPLE_ANALYSIS_2 = {
    "schema_version": "1.1.0",
    "metadata": {
        "white": "OpponentB",
        "black": "DrCheezFighter",
        "result": "1-0",
        "date": "2026.10.02",
        "eco": "C44",
    },
    "summary": {
        "total_plies": 4,
        "critical_positions_count": 1,
        "critical_episodes_count": 1,
    },
    "moves": [
        {
            "ply": 1,
            "move_number": 1,
            "color": "White",
            "played_move": "e4",
            "best_move": "e4",
            "centipawn_loss": 0,
            "classification": "good",
            "is_critical": False,
        },
        {
            "ply": 2,
            "move_number": 1,
            "color": "Black",
            "played_move": "e5",
            "best_move": "e5",
            "centipawn_loss": 2,
            "classification": "good",
            "is_critical": False,
        },
        {
            "ply": 3,
            "move_number": 2,
            "color": "White",
            "played_move": "Nf3",
            "best_move": "Nf3",
            "centipawn_loss": 0,
            "classification": "good",
            "is_critical": False,
        },
        {
            "ply": 4,
            "move_number": 2,
            "color": "Black",
            "played_move": "Nc6",
            "best_move": "Nc6",
            "centipawn_loss": 320,
            "classification": "blunder",
            "is_critical": True,
            "tactical_finding": {
                "category": "material_loss",
                "confidence": "high",
                "evidence": {"net_material_loss": 3},
            },
            "explanation": {
                "summary": "Tactical mistake conceding material.",
                "better_move": "Nf6",
            },
        },
    ],
    "episodes": [],
}


@pytest.fixture
def tmp_library():
    """Create a temporary GameLibrary instance."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    lib = GameLibrary(db_path)
    yield lib
    lib.close()
    if os.path.exists(db_path):
        os.unlink(db_path)


class TestDashboardAggregation:
    """Test unit aggregation logic in src/dashboard.py."""

    def test_empty_library(self, tmp_library):
        data = build_dashboard_data(tmp_library)
        assert data["overview"]["total_library_games"] == 0
        assert data["overview"]["library_analyzed_games"] == 0
        assert data["overview"]["analyzed_metrics"] is None
        assert data["trends"] == []
        assert data["recommendations"] == []
        assert data["openings"] == []
        assert data["confidence"]["level"] == "none"

    def test_target_player_resolution(self, tmp_library):
        assert resolve_target_player(tmp_library) is None
        tmp_library.add_game(SAMPLE_PGN_1)
        assert resolve_target_player(tmp_library) == "DrCheezFighter"
        # Explicit override
        assert resolve_target_player(tmp_library, "OpponentA") == "OpponentA"

    def test_analyzed_vs_unanalyzed_distinction(self, tmp_library):
        # Add 1 analyzed game and 1 unanalyzed game
        g1, _ = tmp_library.add_game(SAMPLE_PGN_1)
        g2, _ = tmp_library.add_game(SAMPLE_PGN_2)

        tmp_library.attach_analysis(g1.game_id, SAMPLE_ANALYSIS_1)

        data = build_dashboard_data(tmp_library, player="DrCheezFighter")

        overview = data["overview"]
        assert overview["total_library_games"] == 2
        assert overview["library_analyzed_games"] == 1
        assert overview["library_unanalyzed_games"] == 1
        assert overview["player_games_total"] == 2
        assert overview["player_games_analyzed"] == 1
        assert overview["player_games_unanalyzed"] == 1

        # Win rate: 1 win (White), 1 loss (Black) -> 50%
        assert overview["results"]["wins"] == 1
        assert overview["results"]["losses"] == 1
        assert overview["results"]["win_rate"] == 0.5
        assert overview["white_stats"]["win_rate"] == 1.0
        assert overview["black_stats"]["win_rate"] == 0.0

        # Analyzed metrics strictly calculated over the 1 analyzed game
        metrics = overview["analyzed_metrics"]
        assert metrics is not None
        assert metrics["games_analyzed"] == 1
        assert metrics["moves_analyzed"] == 2  # White moves in game 1
        assert metrics["average_cpl"] == 6.0  # (0 + 12) / 2
        assert metrics["classifications"]["blunder"] == 0

    def test_chronological_trends(self, tmp_library):
        g1, _ = tmp_library.add_game(SAMPLE_PGN_1)
        g2, _ = tmp_library.add_game(SAMPLE_PGN_2)
        tmp_library.attach_analysis(g1.game_id, SAMPLE_ANALYSIS_1)
        tmp_library.attach_analysis(g2.game_id, SAMPLE_ANALYSIS_2)

        data = build_dashboard_data(tmp_library, player="DrCheezFighter")
        trends = data["trends"]
        assert len(trends) == 2
        # Chronological order by date
        assert trends[0]["date"] == "2026.10.01"
        assert trends[0]["color"] == "White"
        assert trends[0]["result"] == "win"
        assert trends[0]["player_rating"] == 1500
        assert trends[0]["opponent_rating"] == 1480
        assert trends[0]["is_analyzed"] is True

        assert trends[1]["date"] == "2026.10.02"
        assert trends[1]["color"] == "Black"
        assert trends[1]["result"] == "loss"
        assert trends[1]["player_rating"] == 1495
        assert trends[1]["blunders"] == 1
        assert trends[1]["avg_cpl"] == 161.0  # (2 + 320) / 2


class TestDashboardAPI:
    """Test GET /api/dashboard endpoint."""

    def test_get_dashboard_endpoint(self, tmp_library, monkeypatch):
        g1, _ = tmp_library.add_game(SAMPLE_PGN_1)
        tmp_library.attach_analysis(g1.game_id, SAMPLE_ANALYSIS_1)

        import api.main as api_module
        monkeypatch.setattr(api_module, "get_library", lambda: GameLibrary(tmp_library.db_path))

        client = TestClient(app)
        response = client.get("/api/dashboard")
        assert response.status_code == 200

        data = response.json()
        assert data["player_name"] == "DrCheezFighter"
        assert data["overview"]["total_library_games"] == 1
        assert data["overview"]["library_analyzed_games"] == 1
        assert len(data["trends"]) == 1
        assert "limitations" in data
        assert "confidence" in data
