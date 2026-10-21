"""Explicit selected-position GET. No startup/store initialization or mutations."""
import sqlite3
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from src.understanding import understand_game, SourceChanged, SourceMissing
from src.positions import UnsupportedVariant
from src.understanding_models import UnderstandingResponse

router = APIRouter(prefix='/api/games', tags=['understanding'])


@router.get('/{game_id}/understanding', response_model=UnderstandingResponse)
def understanding(game_id: str, ply: int = Query(..., ge=0, le=10000),
                  expected_pgn_sha256: str = Query(..., pattern=r'^[a-f0-9]{64}$'),
                  evidence: Literal['none', 'accepted_local'] = 'none',
                  config_hash: str | None = Query(None, pattern=r'^[a-f0-9]{64}$')):
    from api import main
    try:
        response = understand_game(main.DB_PATH, game_id, ply, expected_pgn_sha256, evidence=evidence, config_hash=config_hash)
        if len(response.model_dump_json().encode()) > 256*1024:
            # Features are optional detail, not permission for unbounded output.
            response.features = {'before': None, 'after': None}
            response.changes['changed'] = []
            response.explanations = [e for e in response.explanations if not any(x.startswith('features.') for x in e.get('supports', []))]
            response.coverage.update(partial=True, features='OUTPUT_BUDGET')
            response.limitations.append('Feature maps omitted under the 256 KiB output budget.')
        if len(response.model_dump_json().encode()) > 256*1024:
            raise HTTPException(413, 'UNDERSTANDING_RESPONSE_BUDGET')
        return response
    except SourceMissing:raise HTTPException(404, 'GAME_NOT_FOUND') from None
    except SourceChanged as exc:raise HTTPException(409, str(exc)) from None
    except UnsupportedVariant:raise HTTPException(422, 'STANDARD_CHESS_ONLY') from None
    except ValueError as exc:
        reason = str(exc)
        raise HTTPException(413 if reason == 'PGN_TOO_LARGE' else 422, reason if reason.isupper() and len(reason) < 100 else 'INVALID_GAME_OR_POSITION') from None
    except sqlite3.DatabaseError:raise HTTPException(503, 'UNDERSTANDING_LIBRARY_UNAVAILABLE') from None
