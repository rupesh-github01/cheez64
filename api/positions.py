"""Read-only position exploration. No library initialization or indexing on reads."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Path as PathParameter
from pydantic import BaseModel

from src.positions import PositionQueries

router = APIRouter(prefix="/api/positions", tags=["positions"])


class Filters(BaseModel):
    player: str | None
    player_color: Literal["white", "black", "either"]


class Coverage(BaseModel):
    schema_available: bool
    identity_version: int
    total_games: int
    indexed_games: int
    missing_games: int
    pending_games: int
    complete_games: int
    failed_games: int
    unsupported_games: int
    partial_index: bool
    absence_is_conclusive: bool
    filters: Filters
    as_of: str


class PositionResponse(BaseModel):
    coverage: Coverage


class SummaryResponse(PositionResponse):
    position_id: int | None
    identity_version: int
    variant: Literal["standard"]
    canonical_fen: str
    occurrence_count: int
    distinct_game_count: int
    outgoing_occurrence_count: int
    final_occurrence_count: int


class GameItem(BaseModel):
    game_id: str
    white: str
    black: str
    result: str
    date: str | None
    source: str
    analysis_status: str
    opening_name: str | None
    eco: str | None
    occurrence_count: int
    first_ply: int


class GamesResponse(PositionResponse):
    items: list[GameItem]
    next_cursor: str | None
    matching_plies_endpoint: str


class OccurrenceItem(BaseModel):
    game_id: str
    ply: int
    next_move_uci: str | None


class OccurrenceCursor(BaseModel):
    after_game: str
    after_ply: int


class OccurrencesResponse(PositionResponse):
    items: list[OccurrenceItem]
    next_cursor: OccurrenceCursor | None


class MoveItem(BaseModel):
    uci: str
    san: str
    occurrence_count: int
    distinct_game_count: int
    white_wins: int
    black_wins: int
    draws: int
    unfinished: int
    unknown_result: int
    completed_games: int
    move_frequency: float | None
    white_win_rate: float | None
    white_score_rate: float | None


class MovesResponse(PositionResponse):
    items: list[MoveItem]
    outgoing_occurrence_count: int
    frequency_basis: Literal["occurrences"]
    outcome_basis: Literal["distinct_games_per_move"]
    result_perspective: Literal["white"]


@contextmanager
def reader(player=None, player_color="either"):
    # Resolve per request so app/test DB overrides do not leave stale dependencies.
    from api import main
    path = Path(main.DB_PATH).resolve()
    conn = None
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        yield PositionQueries(conn, player, player_color)
    except KeyError:
        raise HTTPException(404, "Unknown position ID") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except sqlite3.OperationalError as exc:
        raise HTTPException(503, "Position database unavailable") from exc
    finally:
        if conn is not None:
            conn.rollback()
            conn.close()


Color = Literal["white", "black", "either"]


@router.get("/status", response_model=PositionResponse)
def status(player: str | None = Query(None, min_length=1, max_length=200), player_color: Color = "either"):
    with reader(player, player_color) as queries:
        return {"coverage": queries.coverage()}


@router.get("/lookup", response_model=SummaryResponse)
def lookup(fen: str = Query(..., min_length=1, max_length=200), variant: str = "standard",
           player: str | None = Query(None, min_length=1, max_length=200), player_color: Color = "either"):
    with reader(player, player_color) as queries:
        return queries.find(fen, variant)


@router.get("/{position_id}", response_model=SummaryResponse)
def summary(position_id: int = PathParameter(..., ge=1, le=9223372036854775807), player: str | None = Query(None, min_length=1, max_length=200), player_color: Color = "either"):
    with reader(player, player_color) as queries:
        return queries.summary(position_id)


@router.get("/{position_id}/games", response_model=GamesResponse)
def games(position_id: int = PathParameter(..., ge=1, le=9223372036854775807), limit: int = Query(50, ge=1, le=100),
          after_game: str = Query("", max_length=100),
          player: str | None = Query(None, min_length=1, max_length=200), player_color: Color = "either"):
    with reader(player, player_color) as queries:
        return queries.games(position_id, limit, after_game)


@router.get("/{position_id}/occurrences", response_model=OccurrencesResponse)
def occurrences(position_id: int = PathParameter(..., ge=1, le=9223372036854775807), limit: int = Query(50, ge=1, le=100),
                after_game: str = Query("", max_length=100), after_ply: int = Query(-1, ge=-1),
                player: str | None = Query(None, min_length=1, max_length=200), player_color: Color = "either"):
    with reader(player, player_color) as queries:
        return queries.occurrences(position_id, limit, after_game, after_ply)


@router.get("/{position_id}/moves", response_model=MovesResponse)
def moves(position_id: int = PathParameter(..., ge=1, le=9223372036854775807), player: str | None = Query(None, min_length=1, max_length=200), player_color: Color = "either"):
    with reader(player, player_color) as queries:
        return queries.moves(position_id)
