"""Opt-in Lichess advisory adapter; injectable HTTP/clock, no engine fallback."""
import asyncio
from email.utils import parsedate_to_datetime
import json
import time
import uuid

import chess
import httpx

from .evaluation_cache import CacheUnavailable
from .evaluation_models import Evaluation, PositionContext, PV, SearchConfig, Source, digest
from .positions import canonical_position

URL = 'https://lichess.org/api/cloud-eval'


def miss_key(request):
    return digest({'adapter': 1, 'fen': request.context.normalized_fen(), 'variant': 'standard', 'multiPv': request.cloud_multipv})


def retry_after(value, now):
    try:
        if value and value.strip().isdigit():
            # Avoid huge numeric parsing and float overflow; effectively disable for 100 years.
            if len(value.strip()) > 10:
                return now+100*365*86400
            return now+max(60, int(value))
        parsed = parsedate_to_datetime(value).timestamp()
        return max(now+60, parsed)
    except (ValueError, TypeError, OverflowError, AttributeError):
        return now+60


class CloudCoordinator:
    def __init__(self, cache):
        self.cache = cache

    def acquire(self):
        now = self.cache.clock(); limits = self.cache.limits
        with self.cache.writer() as conn:
            row = dict(conn.execute('SELECT * FROM eval_cloud_control WHERE id=1').fetchone())
            if now < row['last_clock']:
                return None, 'CLOCK_REGRESSION'
            if row['owner'] and row['lease_until'] > now:
                return None, 'IN_FLIGHT'
            if max(row['next_allowed'], row['cooldown']) > now:
                return None, 'COOLDOWN'
            starts = [t for t in json.loads(row['starts']) if t > now-3600]
            minute_count = sum(t > now-60 for t in starts)
            if minute_count >= limits.per_minute or len(starts) >= limits.per_hour:
                return None, 'RATE_BUDGET'
            starts.append(now)
            token = uuid.uuid4().hex
            conn.execute('''UPDATE eval_cloud_control SET owner=?,lease_until=?,next_allowed=?,last_clock=?,
                minute_start=?,minute_count=?,hour_start=?,hour_count=?,starts=? WHERE id=1''',
                (token, now+30, now+limits.min_interval, now, now-60, minute_count+1, now-3600, len(starts), json.dumps(starts)))
            return token, None

    def finish(self, token, cooldown=0, negative_key=None, publish=None):
        """Fence completion before publishing. SQLite slot remains owned through ingestion."""
        now = self.cache.clock()
        with self.cache.writer() as conn:
            row = conn.execute('SELECT owner,lease_until,last_clock FROM eval_cloud_control WHERE id=1').fetchone()
            if row['owner'] != token or row['lease_until'] <= now or now < row['last_clock']:
                raise CacheUnavailable('CLOUD_LEASE_LOST')
            if negative_key:
                conn.execute('DELETE FROM eval_cloud_misses WHERE expires<=?', (now,))
                conn.execute('INSERT INTO eval_cloud_misses VALUES(?,?) ON CONFLICT(key) DO UPDATE SET expires=excluded.expires', (negative_key, now+300))
                n = conn.execute('SELECT count(*) FROM eval_cloud_misses').fetchone()[0]
                if n > self.cache.limits.max_misses:
                    conn.execute('DELETE FROM eval_cloud_misses WHERE key IN (SELECT key FROM eval_cloud_misses ORDER BY expires,key LIMIT ?)', (n-self.cache.limits.max_misses,))
            conn.execute('UPDATE eval_cloud_control SET cooldown=max(cooldown,?),last_clock=? WHERE id=1', (cooldown, now))
        # Never call a nested writer transaction. Owner remains reserved; 30s lease > 8s HTTP + bounded storage.
        if publish is not None:
            self.cache.ingest(publish, fence=(token, now))
        with self.cache.writer() as conn:
            row = conn.execute('SELECT owner,lease_until FROM eval_cloud_control WHERE id=1').fetchone()
            if row['owner'] != token or row['lease_until'] <= self.cache.clock():
                raise CacheUnavailable('CLOUD_LEASE_LOST')
            conn.execute('UPDATE eval_cloud_control SET owner=NULL,lease_until=0 WHERE id=1 AND owner=?', (token,))


def parse_cloud(data, request):
    if not isinstance(data, dict) or not all(k in data for k in ('fen', 'depth', 'knodes', 'pvs')):
        raise ValueError('Missing provider fields')
    if not isinstance(data['fen'], str) or len(data['fen']) > 200 or len(data['fen'].split()) != 6:
        raise ValueError('Invalid provider FEN')
    if type(data['depth']) is not int or type(data['knodes']) is not int:
        raise ValueError('Provider depth/knodes must be integers')
    if canonical_position(data['fen']) != canonical_position(request.context.fen):
        raise ValueError('Provider position mismatch')
    if not isinstance(data['pvs'], list) or not 1 <= len(data['pvs']) <= request.cloud_multipv:
        raise ValueError('Invalid provider PV count')
    pvs = []
    for line in data['pvs']:
        if not isinstance(line, dict) or ('cp' in line) == ('mate' in line) or not isinstance(line.get('moves'), str):
            raise ValueError('Invalid provider score/PV')
        if len(line['moves']) > 512:
            raise ValueError('Provider PV too long')
        moves = line['moves'].split()
        if len(moves) > 64:
            raise ValueError('Provider PV exceeds 64 plies')
        board = chess.Board(request.context.fen); normalized = []
        for move in moves:
            parsed = board.parse_uci(move)
            if not parsed:
                raise ValueError('Null provider move')
            normalized.append(parsed.uci()); board.push(parsed)
        if 'cp' in line:
            pv = PV(kind='cp', cp=line['cp'], moves=normalized, original_moves=line['moves'])
        elif line['mate'] == 0 and type(line['mate']) is int:
            root = chess.Board(request.context.fen)
            pv = PV(kind='terminal_checkmate', mate=0, winner='black' if root.turn else 'white', moves=normalized, original_moves=line['moves'])
        else:
            pv = PV(kind='mate', mate=line['mate'], moves=normalized, original_moves=line['moves'])
        pvs.append(pv)
    # Do not attach a game's verified history to a provider's FEN-only search.
    context = PositionContext(fen=request.context.normalized_fen())
    return Evaluation(context=context, config=SearchConfig(source=Source(kind='lichess_cloud', provenance=URL), limit_kind='cloud', multipv=request.cloud_multipv),
                      achieved_depth=data['depth'], knodes=data['knodes'], pvs=pvs, provider_fen=data['fen'])


class CloudResolver:
    def __init__(self, cache, enabled=False, transport=None):
        self.cache = cache; self.enabled = enabled; self.transport = transport

    async def resolve(self, request, deadline=None):
        if not request.prefer_cloud_pvs:
            return await self._resolve(request, deadline)
        # Prefer count only within the explicitly requested provider configuration.
        # No cross-engine/family blending, force-refresh, retry or quota bypass.
        cfg = SearchConfig(source=Source(kind='lichess_cloud', provenance=URL),
                           limit_kind='cloud', multipv=request.cloud_multipv)
        scoped = request.model_copy(update={'prefer_cloud_pvs': False,
            'config_hash': request.config_hash or digest(cfg.model_dump())})
        goal = scoped.model_copy(update={'min_pvs': max(request.min_pvs, request.cloud_multipv)})
        attempt = await self._resolve(goal, deadline)
        selected = attempt if attempt['evaluation'] is not None else self.cache.lookup(scoped)
        if selected['evaluation'] is None:
            fallback = self.cache.lookup(request)
            if fallback['evaluation'] is not None:
                selected = fallback
        if selected['evaluation'] is None:
            selected = attempt
        return {**selected, 'requested_pvs': goal.min_pvs,
                'pv_goal_met': selected['evaluation'] is not None and selected['evaluation']['returned_multipv'] >= goal.min_pvs,
                'cloud_resolution_status': attempt['status'],
                'cloud_resolution_reason': attempt.get('reason')}

    async def _resolve(self, request, deadline=None):
        result = self.cache.lookup(request)
        if result['evaluation'] is not None:
            return result
        if not self.enabled or not request.allow_cloud:
            return {**result, 'status': 'CLOUD_DISABLED', 'reason': 'Server enablement and explicit request opt-in required'}
        if request.require_trusted or not request.allow_advisory or request.source == 'local_stockfish':
            return {**result, 'status': 'INCOMPATIBLE', 'reason': 'Cloud cannot satisfy trusted/local-only or advisory-disabled requests'}
        # Honor exact config/family constraints; never fetch unrelated cloud configurations.
        cfg = SearchConfig(source=Source(kind='lichess_cloud', provenance=URL), limit_kind='cloud', multipv=request.cloud_multipv)
        if (request.config_hash and request.config_hash != digest(cfg.model_dump())) or (request.family_hash and request.family_hash != cfg.family()):
            return {**result, 'status': 'INCOMPATIBLE', 'reason': 'Requested configuration is not this cloud contract'}
        remaining = 8 if deadline is None else min(8, deadline-time.monotonic())
        if remaining <= 0:
            return {**result, 'status': 'DEFERRED', 'reason': 'BATCH_DEADLINE'}
        with self.cache.reader() as conn:
            if conn.execute('SELECT 1 FROM eval_cloud_misses WHERE key=? AND expires>?', (miss_key(request), self.cache.clock())).fetchone():
                return {**result, 'status': 'CLOUD_MISS', 'negative_cache_hit': True}
        coordinator = CloudCoordinator(self.cache)
        token, reason = coordinator.acquire()
        if token is None:
            return {**result, 'status': 'DEFERRED', 'reason': reason}
        # Stampede protection: another request could have published before slot acquisition.
        current = self.cache.lookup(request)
        if current['evaluation'] is not None:
            coordinator.finish(token)
            return current
        status = 'UNAVAILABLE'; reason = None; evaluation = None; cooldown = 0; negative = None
        try:
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(transport=self.transport, follow_redirects=False, timeout=httpx.Timeout(remaining, connect=min(2, remaining))) as client:
                    async with client.stream('GET', URL, params={'fen': request.context.normalized_fen(), 'variant': 'standard', 'multiPv': request.cloud_multipv},
                                             headers={'User-Agent': 'PersonalChessCoach/Segment16'}) as response:
                        if response.status_code == 404:
                            status = 'CLOUD_MISS'; negative = miss_key(request)
                        elif response.status_code == 429:
                            status = 'RATE_LIMITED'; cooldown = retry_after(response.headers.get('Retry-After'), self.cache.clock())
                        elif response.status_code != 200:
                            reason = 'UPSTREAM_SERVER_ERROR' if response.status_code >= 500 else 'UPSTREAM_CONTRACT_ERROR'
                            cooldown = self.cache.clock()+10
                        else:
                            body = bytearray()
                            async for part in response.aiter_bytes(chunk_size=4096):
                                body.extend(part)
                                if len(body) > 65536:
                                    raise ValueError('Provider body exceeds 64 KiB')
                            evaluation = parse_cloud(json.loads(body), request)
                            status = 'CLOUD_HIT'
        except (TimeoutError, httpx.TimeoutException):
            reason = 'TIMEOUT'; cooldown = self.cache.clock()+10
        except httpx.RequestError:
            reason = 'NETWORK_ERROR'; cooldown = self.cache.clock()+10
        except (ValueError, TypeError, KeyError, OverflowError):
            reason = 'INVALID_PROVIDER_RESPONSE'; cooldown = self.cache.clock()+10
        finally:
            # Cancellation still leaves a conservative cooldown/slot fence; no engine fallback.
            coordinator.finish(token, cooldown=cooldown, negative_key=negative, publish=evaluation)
        if evaluation is not None:
            selected = self.cache.lookup(request)
            # Partial/shallow cloud evidence may be stored but cannot satisfy request thresholds.
            return {**selected, 'status': 'CLOUD_HIT' if selected['evaluation'] is not None else 'INCOMPATIBLE',
                    'cloud_observation_stored': True, 'reason': None if selected['evaluation'] else 'Cloud evidence does not meet requested quality'}
        return {**result, 'status': status, 'reason': reason}
