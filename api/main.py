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
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Personal Chess Coach API",
    description="Read-only API exposing game library, analyses, and opening data.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# DB path can be overridden via environment variable
import os
DB_PATH = os.environ.get("CHESS_COACH_DB", str(repo_root / DEFAULT_DB_PATH))


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
