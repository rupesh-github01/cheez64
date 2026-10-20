"""
Read-only FastAPI layer for the Personal Chess Coach.

Exposes existing GameLibrary data (games, analyses, openings) over HTTP.
No analysis execution, no game importing, no database mutation.
"""

from pathlib import Path
import sys
import json
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Ensure repo root is on path so src.* imports work
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import (
    GameLibrary,
    ANALYSIS_ANALYZED,
    ANALYSIS_NOT_ANALYZED,
    DEFAULT_DB_PATH,
)

# ---------------------------------------------------------------------------
# App setup
# DB path can be overridden via environment variable
import os
from contextlib import asynccontextmanager
DB_PATH = os.environ.get("CHESS_COACH_DB", str(repo_root / DEFAULT_DB_PATH))

from api.import_manager import ImportManager
from api.management import router as management_router, set_management_deps
from src.analysis_queue import AnalysisQueue

import_manager = ImportManager(DB_PATH)
set_management_deps(DB_PATH, import_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup recovery for stale imports and expired analysis queue leases."""
    try:
        import_manager.recover_stale_imports()
    except Exception:
        pass
    try:
        with GameLibrary(DB_PATH) as lib:
            queue = AnalysisQueue(lib)
            queue.recover_expired_leases()
    except Exception:
        pass
    yield
    """Graceful shutdown for background import threads."""
    import_manager.shutdown()


app = FastAPI(
    title="Personal Chess Coach API",
    description="API exposing game library, analyses, opening data, and management endpoints.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

from api.positions import router as positions_router
from api.players import router as players_router
from api.evaluations import router as evaluations_router
from api.progressive import router as progressive_router

app.include_router(management_router)
app.include_router(positions_router)
app.include_router(players_router)
app.include_router(evaluations_router)
app.include_router(progressive_router)


def get_library() -> GameLibrary:
    """Open a short-lived library connection per request."""
    return GameLibrary(DB_PATH)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/api/health", tags=["meta"])
def health() -> Dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Dashboard & Aggregated Statistics
# ---------------------------------------------------------------------------


@app.get("/api/dashboard", tags=["dashboard"])
def get_dashboard(
    player: Optional[str] = Query(None, description="Target player username (defaults to primary library player)"),
) -> Dict[str, Any]:
    """
    Return comprehensive aggregated dashboard metrics, chronological performance trends,
    coaching recommendations with linked game reviews, and opening statistics.
    """
    from src.dashboard import build_dashboard_data
    with get_library() as lib:
        data = build_dashboard_data(lib, player=player)
    return data



# ---------------------------------------------------------------------------
# Games — list and single record
# ---------------------------------------------------------------------------


@app.get("/api/games", tags=["games"])
def list_games(
    player: Optional[str] = Query(None, description="Filter by player name (partial match)"),
    result: Optional[str] = Query(None, description="Filter by result: 1-0, 0-1, 1/2-1/2"),
    analysis_status: Optional[str] = Query(None, description="Filter by status: ANALYZED, NOT_ANALYZED, ANALYZING, FAILED"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """
    Return a paginated list of games with their opening (if detected).
    """
    with get_library() as lib:
        games = lib.list_games(
            player=player,
            result=result,
            analysis_status=analysis_status,
            limit=limit,
            offset=offset,
        )
        total = lib.count_games()

        items = []
        for g in games:
            opening = lib.get_game_opening(g.game_id, auto_detect=False)
            items.append({
                **g.to_dict(),
                "opening": _slim_opening(opening),
            })

    return {"total": total, "offset": offset, "limit": limit, "games": items}


@app.get("/api/games/{game_id}", tags=["games"])
def get_game(game_id: str) -> Dict[str, Any]:
    """
    Return a single game record with its opening (if detected).
    """
    with get_library() as lib:
        game = lib.get_game(game_id)
        if not game:
            raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
        opening = lib.get_game_opening(game_id, auto_detect=False)

    return {**game.to_dict(), "opening": _slim_opening(opening)}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@app.get("/api/games/{game_id}/analysis", tags=["analysis"])
def get_analysis(game_id: str) -> Dict[str, Any]:
    """
    Return the full analysis blob for a game (metadata + all moves + episodes + summary).
    """
    with get_library() as lib:
        game = lib.get_game(game_id)
        if not game:
            raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")

        analysis = lib.get_analysis(game_id)
        if not analysis:
            raise HTTPException(
                status_code=404,
                detail=f"No analysis found for game '{game_id}'. Status: {game.analysis_status}",
            )

    return analysis


@app.get("/api/games/{game_id}/moves", tags=["analysis"])
def get_moves(
    game_id: str,
    color: Optional[str] = Query(None, description="Filter by color: White or Black"),
    critical_only: bool = Query(False, description="Return only critical moves"),
    category: Optional[str] = Query(
        None,
        description="Filter by coaching category: tactical, positional, endgame",
    ),
) -> Dict[str, Any]:
    """
    Return the moves array for a game, with optional filters.
    Lighter than the full analysis payload.
    """
    with get_library() as lib:
        _assert_game_exists(lib, game_id)
        analysis = lib.get_analysis(game_id)
        if not analysis:
            raise HTTPException(status_code=404, detail=f"No analysis for '{game_id}'")

    moves: List[Dict[str, Any]] = analysis.get("moves", [])

    if color:
        moves = [m for m in moves if m.get("color", "").lower() == color.lower()]

    if critical_only:
        moves = [m for m in moves if m.get("is_critical")]

    if category:
        moves = _filter_moves_by_category(moves, category)

    return {
        "game_id": game_id,
        "total": len(moves),
        "moves": moves,
    }


@app.get("/api/games/{game_id}/moves/{ply}", tags=["analysis"])
def get_move_by_ply(game_id: str, ply: int) -> Dict[str, Any]:
    """
    Return detailed analysis data for a single ply, including candidates,
    principal variation, position features, and explanation.
    """
    with get_library() as lib:
        _assert_game_exists(lib, game_id)
        analysis = lib.get_analysis(game_id)
        if not analysis:
            raise HTTPException(status_code=404, detail=f"No analysis for '{game_id}'")

    moves = analysis.get("moves", [])
    matching = [m for m in moves if m.get("ply") == ply]
    if not matching:
        raise HTTPException(
            status_code=404,
            detail=f"Ply {ply} not found in analysis for '{game_id}'",
        )

    return {
        "game_id": game_id,
        "ply": ply,
        "move": matching[0],
    }


@app.get("/api/games/{game_id}/critical", tags=["analysis"])
def get_critical_moves(game_id: str) -> Dict[str, Any]:
    """
    Return only critical moves, grouped by episode where applicable.
    Includes classification, CPL, tactical finding, and explanation.
    """
    with get_library() as lib:
        _assert_game_exists(lib, game_id)
        analysis = lib.get_analysis(game_id)
        if not analysis:
            raise HTTPException(status_code=404, detail=f"No analysis for '{game_id}'")

    all_moves = analysis.get("moves", [])
    critical = [m for m in all_moves if m.get("is_critical")]

    # Group by episode_index
    episodes: Dict[Any, List[Dict]] = {}
    standalone: List[Dict] = []
    for m in critical:
        ep = m.get("episode_index")
        if ep is not None:
            episodes.setdefault(ep, []).append(m)
        else:
            standalone.append(m)

    episode_groups = [
        {"episode_index": k, "moves": v}
        for k, v in sorted(episodes.items(), key=lambda x: x[0])
    ]

    return {
        "game_id": game_id,
        "critical_count": len(critical),
        "episode_groups": episode_groups,
        "standalone_critical": standalone,
    }


@app.get("/api/games/{game_id}/episodes", tags=["analysis"])
def get_episodes(game_id: str) -> Dict[str, Any]:
    """
    Return the episodes array from the analysis data.
    """
    with get_library() as lib:
        _assert_game_exists(lib, game_id)
        analysis = lib.get_analysis(game_id)
        if not analysis:
            raise HTTPException(status_code=404, detail=f"No analysis for '{game_id}'")

    return {
        "game_id": game_id,
        "episodes": analysis.get("episodes", []),
    }


# ---------------------------------------------------------------------------
# Openings
# ---------------------------------------------------------------------------


@app.get("/api/games/{game_id}/opening", tags=["openings"])
def get_opening(game_id: str) -> Dict[str, Any]:
    """
    Return the stored opening analysis for a game.
    Does NOT auto-detect — only returns what is already in the database.
    """
    with get_library() as lib:
        game = lib.get_game(game_id)
        if not game:
            raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")
        opening = lib.get_game_opening(game_id, auto_detect=False)

    if not opening:
        raise HTTPException(
            status_code=404,
            detail=f"No opening data for '{game_id}'. Run opening detection first.",
        )

    return opening


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _assert_game_exists(lib: GameLibrary, game_id: str) -> None:
    if not lib.get_game(game_id):
        raise HTTPException(status_code=404, detail=f"Game '{game_id}' not found")


def _slim_opening(opening: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return only the fields needed by the game list / game header."""
    if not opening:
        return None
    return {
        "eco": opening.get("eco"),
        "opening_name": opening.get("opening_name"),
        "variation_name": opening.get("variation_name"),
        "full_name": opening.get("full_name"),
        "matched_ply": opening.get("matched_ply"),
        "theory_exit_ply": opening.get("theory_exit_ply"),
        "confidence": opening.get("confidence"),
    }


TACTICAL_CATEGORIES = {"material_loss", "hanging_piece", "missed_capture", "missed_check_or_forcing_move"}


def _filter_moves_by_category(
    moves: List[Dict[str, Any]], category: str
) -> List[Dict[str, Any]]:
    cat = category.lower()
    result = []
    for m in moves:
        tf = m.get("tactical_finding") or {}
        tf_cat = (tf.get("category") or "").lower()
        pf = m.get("position_features") or {}
        piece_counts = pf.get("piece_counts", {})
        w_queens = piece_counts.get("white", {}).get("queen", 0)
        b_queens = piece_counts.get("black", {}).get("queen", 0)
        material = pf.get("material", {})
        total_mat = material.get("white", 0) + material.get("black", 0)
        is_endgame = (w_queens == 0 and b_queens == 0) or total_mat <= 26

        if cat == "tactical":
            if tf_cat in TACTICAL_CATEGORIES:
                result.append(m)
        elif cat == "endgame":
            if is_endgame:
                result.append(m)
        elif cat == "positional":
            if not is_endgame and tf_cat not in TACTICAL_CATEGORIES:
                result.append(m)

    return result


# ---------------------------------------------------------------------------
# Coach — LLM-powered natural language chess coach
# ---------------------------------------------------------------------------

from src.coach import CoachService, ProviderTimeoutError, ProviderAPIError
from src.coach.service import SUGGESTED_QUESTIONS

coach_service = CoachService()


@app.get("/api/coach/status", tags=["coach"])
def get_coach_status() -> Dict[str, Any]:
    """
    Return the current coach provider configuration and status.
    Never exposes API keys or secrets.
    """
    provider = coach_service.get_provider()
    return {
        "provider": provider.provider_name,
        "is_fallback": provider.is_fallback,
        "is_external_configured": not provider.is_fallback,
        "suggested_questions": SUGGESTED_QUESTIONS,
    }


@app.get("/api/coach/chat", tags=["coach"])
async def coach_chat(
    question: str = Query(..., max_length=1000, description="The user's chess question"),
    game_id: Optional[str] = Query(None, description="Current game ID"),
    ply: Optional[int] = Query(None, description="Current move ply"),
    hint_level: Optional[int] = Query(None, ge=1, le=3, description="Hint tier (1, 2, or 3)"),
    messages: Optional[str] = Query(None, description="Optional JSON-encoded previous messages array"),
    player: Optional[str] = Query(None, description="Player username"),
) -> Dict[str, Any]:
    """
    Conversational chess coach endpoint (read-only query).
    Answers questions grounded in server-side Stockfish analysis and GameLibrary records.
    """
    history_list: Optional[List[Dict[str, str]]] = None
    if messages:
        try:
            parsed = json.loads(messages)
            if isinstance(parsed, list):
                history_list = parsed
        except Exception:
            history_list = None

    with get_library() as lib:
        try:
            return await coach_service.chat(
                library=lib,
                question=question,
                game_id=game_id,
                ply=ply,
                hint_level=hint_level,
                messages=history_list,
                player=player,
            )
        except ValueError as e:
            msg = str(e)
            if "not found" in msg.lower():
                raise HTTPException(status_code=404, detail=msg)
            elif "exceeds" in msg.lower():
                raise HTTPException(status_code=422, detail=msg)
            raise HTTPException(status_code=400, detail=msg)
        except ProviderTimeoutError as e:
            raise HTTPException(status_code=504, detail=f"LLM provider timed out: {str(e)}. Please retry.")
        except ProviderAPIError as e:
            raise HTTPException(status_code=502, detail=f"LLM provider error: {str(e)}. Please retry.")



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=True)
