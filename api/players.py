"""Read-only consolidated and selective engine-free player endpoints."""
import sqlite3
from typing import Literal
from fastapi import APIRouter, HTTPException, Query

from src.fast_profile import build_fast_profile, profile_reader
from src.fast_profile_models import FastProfileResponse

router = APIRouter(prefix='/api/players', tags=['players'])


def _endpoint(section):
    def endpoint(source: str = Query(..., min_length=1, max_length=100),
                 username: str = Query(..., min_length=1, max_length=200),
                 color: Literal['either', 'white', 'black'] = 'either',
                 date_from: str | None = None, date_to: str | None = None,
                 time_control: str | None = Query(None, max_length=100),
                 top_k: int = Query(10, ge=1, le=50),
                 max_games: int = Query(20000, ge=1, le=20000),
                 max_seconds: float = Query(30, gt=0, le=30),
                 clock_convention: Literal['unverified', 'post_increment'] = 'unverified'):
        from api import main
        try:
            with profile_reader(main.DB_PATH) as conn:
                return build_fast_profile(conn, source, username, color=color, date_from=date_from,
                                          date_to=date_to, time_control_filter=time_control,
                                          section=section, top_k=top_k, max_games=max_games,
                                          max_seconds=max_seconds, clock_convention=clock_convention)
        except KeyError:
            raise HTTPException(404, 'No matching player label in the specified source') from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            raise HTTPException(503, 'Player statistics database unavailable or incompatible') from exc
    endpoint.__name__ = 'fast_player_'+section.replace('-', '_')
    return endpoint


for path, section in [('fast-profile', 'all'), ('overview', 'overview'), ('trends', 'trends'),
                      ('repertoire', 'repertoire'), ('time-management', 'time-management'),
                      ('tendencies', 'tendencies'), ('coverage', 'coverage')]:
    router.add_api_route('/'+path, _endpoint(section), methods=['GET'], response_model=FastProfileResponse,
                         response_model_exclude_none=True)
