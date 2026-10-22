"""Bounded independent evaluation APIs. Read routes never initialize a database."""
import hmac
import os
import sqlite3
import time

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import Field

from src.evaluation_cache import EvaluationCache, CacheUnavailable
from src.evaluation_cloud import CloudResolver
from src.evaluation_models import CacheLimits, Lookup, PositionContext, StrictModel, canonical_json


class BoundedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def bounded(request: Request):
            if request.method == 'POST':
                body = bytearray()
                async for part in request.stream():
                    if len(body)+len(part) > 131072:
                        raise HTTPException(413, 'Evaluation request exceeds 128 KiB')
                    body.extend(part)
                request._body = bytes(body)
            return await handler(request)
        return bounded


router = APIRouter(prefix='/api/evaluations', tags=['evaluations'], route_class=BoundedRoute)


class LocalBatch(StrictModel):
    items: list[Lookup] = Field(min_length=1, max_length=100)


class ResolveBatch(StrictModel):
    items: list[Lookup] = Field(min_length=1, max_length=5)


def cache_from_environment():
    path = os.environ.get('CHESS_COACH_EVAL_CACHE_DB', '')
    if not path.strip():
        raise CacheUnavailable('CACHE_NOT_CONFIGURED')
    try:
        limits = CacheLimits.model_validate_json(os.environ.get('CHESS_COACH_EVAL_LIMITS', '{}'))
    except ValueError as exc:
        raise CacheUnavailable('CACHE_LIMITS_INVALID') from exc
    try:
        cache = EvaluationCache(path, limits)
        if cache.path.exists() and not cache.path.is_file():
            raise CacheUnavailable('CACHE_PATH_REFUSED')
        return cache
    except (CacheUnavailable, ValueError, OSError) as exc:
        raise CacheUnavailable('CACHE_PATH_REFUSED') from exc


def enabled():
    return os.environ.get('CHESS_COACH_EVAL_CLOUD_ENABLED', '').lower() == 'true'


def unavailable(reason='CACHE_UNAVAILABLE'):
    return {'status': 'UNAVAILABLE', 'reason': reason, 'evaluation': None, 'context_compatibility': 'UNAVAILABLE',
            'partial': False, 'available': [], 'available_truncated': False, 'coverage_basis': 'requested_context_only'}


def cache_failure(exc):
    # Return only allowlisted codes, never paths, SQL, env values or exception text.
    if isinstance(exc, CacheUnavailable):
        if isinstance(exc.__cause__, sqlite3.DatabaseError):
            cause = exc.__cause__
            primary = getattr(cause, 'sqlite_errorcode', 0) & 255
            if primary in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                return 'CACHE_BUSY'
            if primary in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CANTOPEN):
                return 'CACHE_DATABASE_ERROR'
        code = str(exc)
        if code in {'CACHE_NOT_CONFIGURED', 'CACHE_LIMITS_INVALID', 'CACHE_PATH_REFUSED'}:
            return code
        return {'CACHE_UNINITIALIZED': 'CACHE_FILE_MISSING',
                'CACHE_UNINITIALIZED_OR_INCOMPATIBLE': 'CACHE_SCHEMA_INVALID',
                'INCOMPATIBLE_CACHE_VERSION': 'CACHE_VERSION_INCOMPATIBLE'}.get(code, 'CACHE_UNAVAILABLE')
    if isinstance(exc, sqlite3.DatabaseError):
        if getattr(exc, 'sqlite_errorcode', 0) & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return 'CACHE_BUSY'
        return 'CACHE_DATABASE_ERROR'
    if isinstance(exc, OSError):
        return 'CACHE_IO_ERROR'
    return 'CACHE_DATA_INVALID'


def get_cache():
    try:
        return cache_from_environment()
    except (ValueError, CacheUnavailable, OSError) as exc:
        return unavailable(cache_failure(exc))


def local_batch(cache, items):
    if isinstance(cache, dict):
        return [cache.copy() for _ in items]
    try:
        with cache.reader() as conn:
            memo = {}
            out = []
            for item in items:
                key = canonical_json(item.model_dump())
                if key not in memo:
                    memo[key] = cache.lookup(item, conn)
                out.append(memo[key])
            return out
    except (CacheUnavailable, sqlite3.DatabaseError, OSError, ValueError) as exc:
        return [unavailable(cache_failure(exc)) for _ in items]


def envelope(items, results):
    # Enforce serialized output bounds before FastAPI constructs the final response.
    output = []; size = 65536  # reserve envelope + up to 100 explicit deferred results
    for result in results:
        encoded = canonical_json(result).encode()
        if size+len(encoded) > 2*1024**2:
            result = {**unavailable('RESPONSE_BUDGET'), 'status': 'DEFERRED', 'partial': True}
            encoded = canonical_json(result).encode()
        output.append(result); size += len(encoded)+2
    statuses = [r['status'] for r in output]
    trusted = sum(r['evaluation'] is not None and r['context_compatibility']=='TRUSTED_CONTEXT_MATCH' for r in output)
    advisory = sum(r['evaluation'] is not None and r['context_compatibility']=='ADVISORY_CONTEXT_UNVERIFIED' for r in output)
    return {'items': output, 'coverage': {'basis': 'this_request_only', 'requested_count': len(items),
            'distinct_requested_count': len({canonical_json(i.model_dump()) for i in items}),
            'compatible_hits': trusted, 'advisory_hits': advisory,
            'misses': sum(s in ('CACHE_MISS','CLOUD_MISS') for s in statuses),
            'incompatible': sum(s in ('INCOMPATIBLE','CONFIG_REQUIRED') for s in statuses),
            'unavailable': sum(s in ('UNAVAILABLE','CLOUD_DISABLED','RATE_LIMITED') for s in statuses),
            'deferred': statuses.count('DEFERRED')},
            'partial': any(r['partial'] or r['evaluation'] is None for r in output)}


@router.get('/lookup')
def lookup(fen: str, allow_advisory: bool = False, min_depth: int = 0, min_pvs: int = 1,
           config_hash: str | None = None, family_hash: str | None = None):
    try:
        query = Lookup(context=PositionContext(fen=fen), allow_advisory=allow_advisory, min_depth=min_depth,
                       min_pvs=min_pvs, config_hash=config_hash, family_hash=family_hash)
    except ValueError as exc:
        raise HTTPException(422, 'Invalid evaluation context or constraints') from exc
    return local_batch(get_cache(), [query])[0]


@router.post('/lookup-batch')
def lookup_batch(body: LocalBatch):
    return envelope(body.items, local_batch(get_cache(), body.items))


@router.get('/status')
def status():
    cache = get_cache()
    capabilities = {'cloud_enabled': enabled(), 'cloud_requires_operator_key': True,
                    'read_only': os.environ.get('CHESS_COACH_READ_ONLY', '').lower() == 'true',
                    'cloud_operator_key_configured': len(os.environ.get('CHESS_COACH_EVAL_RESOLVE_KEY', '')) >= 16,
                    'cloud_resolution_allowed': enabled() and os.environ.get('CHESS_COACH_READ_ONLY', '').lower() != 'true'
                    and len(os.environ.get('CHESS_COACH_EVAL_RESOLVE_KEY', '')) >= 16}
    try:
        if isinstance(cache, dict):
            return {**cache, **capabilities}
        return {'status': 'OK', **capabilities, **cache.status()}
    except (CacheUnavailable, sqlite3.DatabaseError, OSError, ValueError) as exc:
        return {**unavailable(cache_failure(exc)), **capabilities}


def authorize(items, key):
    if enabled() and any(i.allow_cloud for i in items):
        expected = os.environ.get('CHESS_COACH_EVAL_RESOLVE_KEY', '')
        if len(expected) < 16 or not key or not hmac.compare_digest(expected.encode(), key.encode()):
            raise HTTPException(403, 'Cloud resolution requires a configured operator key')


async def resolve_items(items, key):
    authorize(items, key)
    cache = get_cache()
    if isinstance(cache, dict):
        return [cache.copy() for _ in items]
    resolver = CloudResolver(cache, enabled=enabled())
    deadline = time.monotonic()+15
    memo = {}; results = []
    for item in items:
        identity = canonical_json(item.model_dump())
        if identity not in memo:
            try:
                memo[identity] = await resolver.resolve(item, deadline=deadline)
            except (CacheUnavailable, sqlite3.DatabaseError, OSError, ValueError) as exc:
                memo[identity] = unavailable(cache_failure(exc))
        results.append(memo[identity])
    return results


@router.post('/resolve')
async def resolve(body: Lookup, x_evaluation_key: str | None = Header(None)):
    return (await resolve_items([body], x_evaluation_key))[0]


@router.post('/resolve-batch')
async def resolve_batch(body: ResolveBatch, x_evaluation_key: str | None = Header(None)):
    return envelope(body.items, await resolve_items(body.items, x_evaluation_key))
