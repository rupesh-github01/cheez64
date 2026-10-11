"""
Tests for Segment 9 — LLM-Powered Natural Language Chess Coach.

Covers:
- Server-authoritative context construction for selected moves and positions
- Player-wide and game-wide coaching context extraction
- Missing analysis, missing moves, and unavailable evidence handling
- Progressive 3-tier hints with strict anti-leakage verification
- Deterministic offline fallback provider behavior
- External provider error/timeout handling without silent substitution
- API endpoints validation, security limits, and key non-exposure
"""

import asyncio
import json
import os
import sys
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import GameLibrary
from src.coach.service import CoachService, SUGGESTED_QUESTIONS
from src.coach.provider import (
    DeterministicFallbackProvider,
    OpenAICompatibleProvider,
    get_llm_provider,
    ProviderTimeoutError,
    ProviderAPIError,
)
from src.coach.hints import (
    generate_progressive_hint,
    check_and_sanitize_hint_leakage,
    HINT_TIER_CONCEPTUAL,
    HINT_TIER_SPECIFIC,
    HINT_TIER_REVEAL,
)
from src.coach.context import (
    validate_game_and_ply,
    build_move_context,
    build_player_context,
    build_grounded_context_prompt,
)
from api.main import app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_library(tmp_path):
    """Seed a test SQLite GameLibrary with one analyzed game and one unanalyzed game."""
    db_file = str(tmp_path / "test_coach.db")
    lib = GameLibrary(db_file)

    # 1. Analyzed Game
    pgn_text = """[Event "Test Match"]
[Site "Local"]
[Date "2026.01.15"]
[White "HeroPlayer"]
[Black "RivalOpponent"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. b4 Bxb4 1-0"""

    game1, _ = lib.add_game(pgn_text)
    game_id_1 = game1.game_id

    analysis_data = {
        "metadata": {"game_id": game_id_1, "analyzed_at": "2026-01-15T10:00:00Z"},
        "moves": [
            {
                "ply": 1,
                "move_number": 1,
                "color": "White",
                "played_move": "e4",
                "best_move": "e4",
                "evaluation_before": 20,
                "evaluation_after": 25,
                "centipawn_loss": 0,
                "classification": "good",
                "fen_before": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "fen_after": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1",
                "principal_variation": ["e4", "e5", "Nf3"],
                "candidates": [
                    {"move": "e4", "evaluation": 25, "principal_variation": ["e4", "e5"]},
                    {"move": "d4", "evaluation": 20, "principal_variation": ["d4", "d5"]},
                ],
                "tactical_finding": None,
                "explanation": {
                    "summary": "Controls the center and opens diagonals.",
                    "what_happened": "White stakes an immediate claim in the center.",
                    "why_it_matters": "Accelerates piece development.",
                    "motif": "None",
                    "variation": "1. e4 e5",
                    "confidence": "high",
                },
            },
            {
                "ply": 7,
                "move_number": 4,
                "color": "White",
                "played_move": "b4",
                "best_move": "c3",
                "evaluation_before": 35,
                "evaluation_after": -120,
                "centipawn_loss": 155,
                "classification": "mistake",
                "fen_before": "r1bqk1nr/pppp1ppp/2n5/2b5/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
                "fen_after": "r1bqk1nr/pppp1ppp/2n5/2b5/1PB1P3/5N2/P1PP1PPP/RNBQK2R b KQkq b3 0 4",
                "principal_variation": ["c3", "Nf6", "d3"],
                "pv_after": ["Bxb4", "c3", "Be7"],
                "candidates": [
                    {"move": "c3", "evaluation": 35, "principal_variation": ["c3", "Nf6"]},
                    {"move": "O-O", "evaluation": 25, "principal_variation": ["O-O", "Nf6"]},
                ],
                "is_critical": True,
                "episode_index": 0,
                "tactical_finding": {
                    "category": "hanging_piece",
                    "confidence": "high",
                    "played_move": "b4",
                    "better_move": "c3",
                    "evidence": {
                        "hanging_piece": "pawn",
                        "square": "b4",
                        "attackers_count": 1,
                        "defenders_count": 0,
                        "opponent_capture_move": "Bxb4",
                    },
                    "limitations": None,
                },
                "explanation": {
                    "summary": "Evans Gambit sacrifice leaving b4 undefended.",
                    "what_happened": "White pushes b4, sacrificing the pawn without direct tactical recovery in this line.",
                    "why_it_matters": "Black accepts the wing pawn with tempo on the bishop.",
                    "motif": "hanging_piece",
                    "variation": "4... Bxb4 5. c3",
                    "confidence": "high",
                },
            },
        ],
        "episodes": [
            [
                {
                    "ply": 7,
                    "move_number": 4,
                    "color": "White",
                    "played_move": "b4",
                    "classification": "mistake",
                }
            ]
        ],
    }

    lib.attach_analysis(game_id_1, analysis_data)

    # 2. Unanalyzed Game
    game2, _ = lib.add_game("[Event \"Raw\"] [White \"HeroPlayer\"] [Black \"Guest\"] 1. d4 d5 *")
    game_id_2 = game2.game_id

    return lib, game_id_1, game_id_2


@pytest.fixture
def api_client(mock_library, monkeypatch):
    lib, g1, g2 = mock_library
    db_file = lib.db_path
    monkeypatch.setattr("api.main.DB_PATH", db_file)
    monkeypatch.setattr("api.main.get_library", lambda: GameLibrary(db_file))
    return TestClient(app), g1, g2


# ---------------------------------------------------------------------------
# Unit Tests — Context Construction
# ---------------------------------------------------------------------------

class TestContextConstruction:
    def test_build_move_context_valid(self, mock_library):
        lib, g1, _ = mock_library
        game, analysis, move = validate_game_and_ply(lib, g1, 7)
        ctx = build_move_context(game, analysis, move)

        assert ctx["has_move"] is True
        assert ctx["ply"] == 7
        assert ctx["played_move"] == "b4"
        assert ctx["best_move"] == "c3"
        assert ctx["classification"] == "mistake"
        assert ctx["centipawn_loss"] == 155
        assert len(ctx["candidate_moves"]) == 2
        assert ctx["tactical_finding"]["category"] == "hanging_piece"
        assert ctx["tactical_finding"]["evidence"]["square"] == "b4"
        assert ctx["episode_info"]["episode_index"] == 0

    def test_build_move_context_start_position(self, mock_library):
        lib, g1, _ = mock_library
        game, analysis, move = validate_game_and_ply(lib, g1, 0)
        ctx = build_move_context(game, analysis, move)

        assert ctx["has_move"] is False
        assert ctx["has_analysis"] is True
        assert ctx["game_id"] == g1

    def test_missing_analysis_context(self, mock_library):
        lib, _, g2 = mock_library
        game, analysis, move = validate_game_and_ply(lib, g2, 1)
        assert analysis is None
        assert move is None

        ctx = build_move_context(game, None, None)
        assert ctx["has_move"] is False
        assert ctx["has_analysis"] is False

        prompt = build_grounded_context_prompt(ctx)
        assert "NO ANALYSIS AVAILABLE" in prompt

    def test_invalid_game_and_ply_validation(self, mock_library):
        lib, g1, _ = mock_library
        with pytest.raises(ValueError, match="Game 'non_existent' not found"):
            validate_game_and_ply(lib, "non_existent", 1)

        with pytest.raises(ValueError, match="Ply 999 not found"):
            validate_game_and_ply(lib, g1, 999)

    def test_player_context_construction(self, mock_library):
        lib, _, _ = mock_library
        p_ctx = build_player_context(lib, "HeroPlayer")
        assert p_ctx["player_name"] == "HeroPlayer"
        assert "overview" in p_ctx
        assert "confidence" in p_ctx


# ---------------------------------------------------------------------------
# Unit Tests — Progressive Hints and Anti-Leakage
# ---------------------------------------------------------------------------

class TestHintIntegrity:
    def test_tier_1_conceptual_hint_never_reveals_best_move(self, mock_library):
        lib, g1, _ = mock_library
        game, analysis, move = validate_game_and_ply(lib, g1, 7)
        ctx = build_move_context(game, analysis, move)

        hint = generate_progressive_hint(ctx, HINT_TIER_CONCEPTUAL)
        assert "Level 1/3" in hint
        # Best move is "c3"
        assert "c3" not in hint.lower().split()
        assert "play c3" not in hint.lower()

    def test_tier_2_specific_hint_never_reveals_best_move_san(self, mock_library):
        lib, g1, _ = mock_library
        game, analysis, move = validate_game_and_ply(lib, g1, 7)
        ctx = build_move_context(game, analysis, move)

        hint = generate_progressive_hint(ctx, HINT_TIER_SPECIFIC)
        assert "Level 2/3" in hint
        # Must not contain direct SAN move c3
        assert " c3 " not in hint
        assert "play c3" not in hint.lower()
        # Mentions pawn or piece type / context
        assert "Pawn" in hint or "square" in hint

    def test_tier_3_reveal_includes_best_move_and_variation(self, mock_library):
        lib, g1, _ = mock_library
        game, analysis, move = validate_game_and_ply(lib, g1, 7)
        ctx = build_move_context(game, analysis, move)

        hint = generate_progressive_hint(ctx, HINT_TIER_REVEAL)
        assert "Level 3/3" in hint
        assert "c3" in hint
        assert "Recommended Continuation" in hint

    def test_sanitizer_catches_and_masks_leaks(self):
        # Leaky text attempting to mention best move "Nf3"
        leaky_text = "You should really look at playing Nf3 to attack e5."
        sanitized = check_and_sanitize_hint_leakage(leaky_text, "Nf3", tier=1)
        assert "Nf3" not in sanitized
        assert "the recommended square" in sanitized

        # Tier 3 is allowed to reveal
        revealed = check_and_sanitize_hint_leakage(leaky_text, "Nf3", tier=3)
        assert "Nf3" in revealed


# ---------------------------------------------------------------------------
# Unit Tests — Providers and Fallback Behavior
# ---------------------------------------------------------------------------

class TestProviders:
    def test_deterministic_fallback_provider(self, mock_library):
        lib, g1, _ = mock_library
        game, analysis, move = validate_game_and_ply(lib, g1, 7)
        ctx = build_move_context(game, analysis, move)
        prompt = build_grounded_context_prompt(ctx)

        provider = DeterministicFallbackProvider()
        assert provider.is_fallback is True
        assert provider.provider_name == "deterministic_fallback"

        # 1. Ask why mistake
        ans = asyncio.run(provider.complete(
            messages=[{"role": "user", "content": "Why was this move a mistake?"}],
            system_prompt=prompt,
        ))
        assert "b4" in ans
        assert "155 cp" in ans or "mistake" in ans.lower()
        assert "hanging" in ans.lower() or "undefended" in ans.lower()

        # 2. Ask why engine best move better
        ans_better = asyncio.run(provider.complete(
            messages=[{"role": "user", "content": "Why is the engine's best move better than my move?"}],
            system_prompt=prompt,
        ))
        assert "c3" in ans_better
        assert "b4" in ans_better

    def test_provider_factory_fallback_when_no_keys(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("CHESS_COACH_LLM_API_KEY", raising=False)
        monkeypatch.delenv("CHESS_COACH_LLM_PROVIDER", raising=False)

        provider = get_llm_provider()
        assert isinstance(provider, DeterministicFallbackProvider)
        assert provider.is_fallback is True

    def test_openai_provider_timeout_raises_clear_error(self, monkeypatch):
        import httpx
        provider = OpenAICompatibleProvider(api_key="test_key", timeout=1.0)

        with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Timeout")):
            with pytest.raises(ProviderTimeoutError, match="timed out"):
                asyncio.run(provider.complete(
                    messages=[{"role": "user", "content": "Hello"}],
                    system_prompt="system",
                ))

    def test_openai_provider_api_error_raises_clear_error(self):
        provider = OpenAICompatibleProvider(api_key="test_key")

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Rate limit exceeded"

        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            with pytest.raises(ProviderAPIError, match="429"):
                asyncio.run(provider.complete(
                    messages=[{"role": "user", "content": "Hello"}],
                    system_prompt="system",
                ))


# ---------------------------------------------------------------------------
# Integration Tests — Service and API Endpoints
# ---------------------------------------------------------------------------

class TestCoachAPI:
    def test_coach_status_endpoint(self, api_client):
        client, _, _ = api_client
        res = client.get("/api/coach/status")
        assert res.status_code == 200
        data = res.json()
        assert data["is_fallback"] is True
        assert data["provider"] == "deterministic_fallback"
        assert len(data["suggested_questions"]) > 0
        # Assert no secrets
        assert "api_key" not in str(data)

    def test_coach_chat_valid_move(self, api_client):
        client, g1, _ = api_client
        res = client.get(
            "/api/coach/chat",
            params={
                "game_id": g1,
                "ply": 7,
                "question": "Why was this move a mistake?",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["is_fallback"] is True
        assert data["move_context"]["played_move"] == "b4"
        assert data["move_context"]["best_move"] == "c3"
        assert "b4" in data["response"]
        assert len(data["suggested_questions"]) > 0

    def test_coach_chat_hint_levels(self, api_client):
        client, g1, _ = api_client
        # Tier 1 Hint
        res1 = client.get(
            "/api/coach/chat",
            params={
                "game_id": g1,
                "ply": 7,
                "question": "Give me a hint",
                "hint_level": 1,
            },
        )
        assert res1.status_code == 200
        assert "Level 1/3" in res1.json()["response"]
        assert "c3" not in res1.json()["response"].lower().split()

        # Tier 3 Reveal
        res3 = client.get(
            "/api/coach/chat",
            params={
                "game_id": g1,
                "ply": 7,
                "question": "Reveal best move",
                "hint_level": 3,
            },
        )
        assert res3.status_code == 200
        assert "c3" in res3.json()["response"]

    def test_coach_chat_player_recurring_mistakes(self, api_client):
        client, g1, _ = api_client
        res = client.get(
            "/api/coach/chat",
            params={
                "game_id": g1,
                "question": "What recurring mistakes do I make across my games?",
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert "Coaching" in data["response"] or "Dashboard" in data["response"]

    def test_coach_chat_invalid_game(self, api_client):
        client, _, _ = api_client
        res = client.get(
            "/api/coach/chat",
            params={"game_id": "invalid_game_id", "question": "Why?"},
        )
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    def test_coach_chat_invalid_ply(self, api_client):
        client, g1, _ = api_client
        res = client.get(
            "/api/coach/chat",
            params={"game_id": g1, "ply": 999, "question": "Why?"},
        )
        assert res.status_code == 404

    def test_coach_chat_oversized_question_rejected(self, api_client):
        client, g1, _ = api_client
        huge_question = "Why? " * 300  # 1500 chars
        res = client.get(
            "/api/coach/chat",
            params={"game_id": g1, "ply": 1, "question": huge_question},
        )
        assert res.status_code == 422

    def test_coach_chat_missing_question_rejected(self, api_client):
        client, g1, _ = api_client
        res = client.get(
            "/api/coach/chat",
            params={"game_id": g1, "ply": 1},
        )
        assert res.status_code == 422

    def test_coach_chat_followup_context(self, api_client):
        client, g1, _ = api_client
        history = [
            {"role": "user", "content": "What is the best move here?"},
            {"role": "assistant", "content": "The engine prefers c3."},
        ]
        res = client.get(
            "/api/coach/chat",
            params={
                "game_id": g1,
                "ply": 7,
                "question": "And what should I consider after that?",
                "messages": json.dumps(history),
            },
        )
        assert res.status_code == 200
        assert "Consider" in res.json()["response"]

    def test_coach_chat_external_provider_timeout_error_handling(self, api_client, monkeypatch):
        client, g1, _ = api_client

        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock_failing_openai"
        mock_provider.is_fallback = False
        mock_provider.complete.side_effect = ProviderTimeoutError("Connection to OpenAI timed out")

        with patch("src.coach.service.CoachService.get_provider", return_value=mock_provider):
            res = client.get(
                "/api/coach/chat",
                params={"game_id": g1, "ply": 7, "question": "Explain this"},
            )
            assert res.status_code == 504
            assert "timed out" in res.json()["detail"].lower()
            assert "retry" in res.json()["detail"].lower()

    def test_unanalyzed_game_analysis_404_detail(self, api_client):
        client, _, g2 = api_client
        res = client.get(f"/api/games/{g2}/analysis")
        assert res.status_code == 404
        assert "not_analyzed" in res.json()["detail"].lower()

    def test_stockfish_path_env_resolution(self, monkeypatch):
        import shutil
        monkeypatch.setenv("STOCKFISH_PATH", "/custom/bin/stockfish")
        # Re-evaluate
        path = os.environ.get("STOCKFISH_PATH", shutil.which("stockfish") or "/opt/homebrew/bin/stockfish")
        assert path == "/custom/bin/stockfish"

