"""Explicit local practice mutations; library reads remain read-only."""
import ipaddress
import json
import os
import sqlite3
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.routing import APIRoute
from src.evaluation_cache import CacheUnavailable
from src.practice_models import Discover, Start, Event
from src.practice_store import PracticeStore, PracticeError, Limits


class PracticeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request):
            if request.method == 'POST':
                body = bytearray()
                async for part in request.stream():
                    if len(body)+len(part) > 65536:
                        raise HTTPException(413, 'Practice request exceeds 64 KiB')
                    body.extend(part)
                request._body = bytes(body)
            return await handler(request)
        return bounded


router = APIRouter(prefix='/api/practice', tags=['practice'], route_class=PracticeRoute)


def writable():
    return (os.environ.get('CHESS_COACH_READ_ONLY', '').lower() != 'true'
            and os.environ.get('CHESS_COACH_PRACTICE_WRITES', '').lower() == 'true')


def authorize(request):
    if not writable():
        raise HTTPException(403, 'Practice writes disabled')
    client = request.client.host if request.client else ''
    try:
        local = ipaddress.ip_address(client).is_loopback
    except ValueError:
        local = client == 'testclient'  # Starlette in-process test transport, never a forwarded header
    host = urlsplit('http://'+request.headers.get('host', '')).hostname
    origin = request.headers.get('origin')
    if (not local or host not in ('localhost', '127.0.0.1', '::1', 'testserver') or
        (origin is not None and origin not in ('http://localhost:3000', 'http://127.0.0.1:3000')) or
        request.headers.get('x-practice-action') != 'local-practice' or
        request.headers.get('content-type', '').split(';')[0] != 'application/json'):
        raise HTTPException(403, 'Local JSON practice action required')


def store():
    from api import main
    return PracticeStore(os.environ.get('CHESS_COACH_PRACTICE_DB'), main.DB_PATH,
                         Limits.model_validate_json(os.environ.get('CHESS_COACH_PRACTICE_LIMITS', '{}')))


def invoke(call):
    try:
        result = call()
        if len(json.dumps(result).encode()) > 256*1024:
            raise HTTPException(413, 'Practice response budget exceeded; request a smaller page')
        return result
    except KeyError:
        raise HTTPException(404, 'Practice item not found') from None
    except (CacheUnavailable, PracticeError) as exc:
        message = str(exc)
        code = 409 if any(x in message for x in ('STALE', 'CONFLICT', 'FINISHED', 'RETRY', 'EVENT_LIMIT', 'HINT_LIMIT')) else 503
        if 'TOO_LARGE' in message:
            code = 413
        raise HTTPException(code, message) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except sqlite3.DatabaseError:
        raise HTTPException(503, 'Practice or source store unavailable, busy or incompatible') from None


@router.get('/status')
def status():
    try:
        result = store().status()
    except (ValueError, CacheUnavailable, sqlite3.DatabaseError):
        result = {'status': 'UNAVAILABLE', 'message': 'Initialize a dedicated practice store explicitly; no file was created.'}
    return {**result, 'writes_enabled': writable(), 'engine_dispatch': False}


@router.get('/puzzles')
def puzzles(source: str = Query(..., min_length=1, max_length=100), username: str = Query(..., min_length=1, max_length=200),
            after: str = Query('', max_length=64), limit: int = Query(20, ge=1, le=50)):
    return invoke(lambda: store().listing({'source': source, 'username': username}, after, limit))


@router.post('/discover')
def discover(body: Discover, request: Request):
    authorize(request)
    return invoke(lambda: store().discover(body))


@router.get('/puzzles/{pid}')
def puzzle(pid: str):
    if len(pid) > 64:
        raise HTTPException(404)
    return invoke(lambda: store().get_question(pid))


@router.post('/attempts')
def start(body: Start, request: Request):
    authorize(request)
    return invoke(lambda: store().start(body))


@router.get('/attempts/{aid}')
def attempt(aid: str):
    if len(aid) > 64:
        raise HTTPException(404)
    return invoke(lambda: store().attempt(aid))


@router.post('/attempts/{aid}/events')
def event(aid: str, body: Event, request: Request):
    authorize(request)
    return invoke(lambda: store().event(aid, body))


@router.get('/history')
def history(source: str = Query(..., min_length=1, max_length=100), username: str = Query(..., min_length=1, max_length=200),
            after: str = Query('', max_length=64), limit: int = Query(20, ge=1, le=50)):
    return invoke(lambda: store().history({'source': source, 'username': username}, after, limit))
