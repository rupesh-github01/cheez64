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
    limits = CacheLimits.model_validate_json(os.environ.get('CHESS_COACH_EVAL_LIMITS', '{}'))
    return EvaluationCache(os.environ.get('CHESS_COACH_EVAL_CACHE_DB'), limits)


def enabled():
    return os.environ.get('CHESS_COACH_EVAL_CLOUD_ENABLED', '').lower() == 'true'


def unavailable(reason='CACHE_UNAVAILABLE'):
    return {'status': 'UNAVAILABLE', 'reason': reason, 'evaluation': None, 'context_compatibility': 'UNAVAILABLE',
            'partial': False, 'available': [], 'available_truncated': False, 'coverage_basis': 'requested_context_only'}


def get_cache():
    try:
        return cache_from_environment()
    except (ValueError, CacheUnavailable):
        return None


def local_batch(cache, items):
    if cache is None:
        return [unavailable('CACHE_UNINITIALIZED_OR_INVALID_CONFIGURATION') for _ in items]
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
    except (CacheUnavailable, sqlite3.DatabaseError):
        return [unavailable() for _ in items]


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
    try:
        if cache is None:
            raise CacheUnavailable('No cache configured')
        return {'status': 'OK', 'cloud_enabled': enabled(), 'cloud_requires_operator_key': True, **cache.status()}
    except (CacheUnavailable, sqlite3.DatabaseError):
        return {**unavailable('CACHE_UNINITIALIZED_OR_INCOMPATIBLE'), 'cloud_enabled': enabled()}


def authorize(items, key):
    if enabled() and any(i.allow_cloud for i in items):
        expected = os.environ.get('CHESS_COACH_EVAL_RESOLVE_KEY', '')
        if len(expected) < 16 or not key or not hmac.compare_digest(expected.encode(), key.encode()):
            raise HTTPException(403, 'Cloud resolution requires a configured operator key')


async def resolve_items(items, key):
    authorize(items, key)
    cache = get_cache()
    if cache is None:
        return [unavailable('CACHE_UNINITIALIZED_OR_INVALID_CONFIGURATION') for _ in items]
    resolver = CloudResolver(cache, enabled=enabled())
    deadline = time.monotonic()+15
    memo = {}; results = []
    for item in items:
        identity = canonical_json(item.model_dump())
        if identity not in memo:
            try:
                memo[identity] = await resolver.resolve(item, deadline=deadline)
            except (CacheUnavailable, sqlite3.DatabaseError):
                memo[identity] = unavailable()
        results.append(memo[identity])
    return results


@router.post('/resolve')
async def resolve(body: Lookup, x_evaluation_key: str | None = Header(None)):
    return (await resolve_items([body], x_evaluation_key))[0]


@router.post('/resolve-batch')
async def resolve_batch(body: ResolveBatch, x_evaluation_key: str | None = Header(None)):
    return envelope(body.items, await resolve_items(body.items, x_evaluation_key))
