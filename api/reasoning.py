"""Explicit GET-only selected-position reasoning. No import or analysis execution."""
import json
import os
import sqlite3
from fastapi import APIRouter, HTTPException, Query
from src.reasoning import reason_game, availability
from src.reasoning_models import ReasoningResponse
from src.understanding import SourceChanged, SourceMissing
from src.understanding_models import BudgetExceeded

router = APIRouter(prefix='/api/games', tags=['reasoning'])


def invoke(call):
    try:
        result = call()
        data = result.model_dump_json() if isinstance(result, ReasoningResponse) else json.dumps(result)
        if len(data.encode()) > 256*1024:
            raise HTTPException(413, 'Reasoning response budget exceeded')
        return result
    except SourceMissing:
        raise HTTPException(404, 'Game not found') from None
    except SourceChanged:
        raise HTTPException(409, 'Source changed; refresh the game') from None
    except BudgetExceeded:
        raise HTTPException(503, 'Source validation exceeded the reasoning budget; no valid prefix was assumed') from None
    except ValueError:
        raise HTTPException(422, 'Invalid position, history or legal alternative') from None
    except (sqlite3.DatabaseError, OSError):
        raise HTTPException(503, 'Read-only source unavailable') from None


@router.get('/{game_id}/reasoning/availability')
def evidence_availability(game_id: str, ply: int = Query(..., ge=0, le=10000),
                          expected_pgn_sha256: str = Query(..., pattern=r'^[a-f0-9]{64}$')):
    from api import main
    return invoke(lambda: availability(main.DB_PATH, game_id, ply, expected_pgn_sha256,
                                      os.environ.get('CHESS_COACH_REASONING_DIR')))


@router.get('/{game_id}/reasoning', response_model=ReasoningResponse)
def reasoning(game_id: str, ply: int = Query(..., ge=0, le=10000),
              expected_pgn_sha256: str = Query(..., pattern=r'^[a-f0-9]{64}$'),
              alternative_uci: str | None = Query(None, pattern=r'^[a-h][1-8][a-h][1-8][qrbn]?$'),
              bundle_id: str | None = Query(None, pattern=r'^[a-f0-9]{64}$'),
              profile_id: str | None = Query(None, pattern=r'^[a-f0-9]{64}$')):
    from api import main
    return invoke(lambda: reason_game(main.DB_PATH, game_id, ply, expected_pgn_sha256,
        alternative=alternative_uci, directory=os.environ.get('CHESS_COACH_REASONING_DIR'), bundle_id=bundle_id,
        profile_id=profile_id, allow_synthetic=os.environ.get('CHESS_COACH_REASONING_ALLOW_SYNTHETIC', '').lower() == 'true'))
