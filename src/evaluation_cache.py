"""Independent additive SQLite evaluation store. No GameLibrary initialization."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

from .evaluation_models import CacheLimits, Evaluation, Lookup, canonical_json, digest

VERSION = (1, 1, 1)
ROOT = Path(__file__).resolve().parents[1]


class CacheUnavailable(RuntimeError):
    pass


class CapacityError(CacheUnavailable):
    pass


def safe_path(path):
    if not path:
        raise CacheUnavailable('CACHE_UNINITIALIZED')
    raw = Path(path).absolute()
    # Forbid symlink components and hard-link aliases before opening SQLite.
    if any(p.is_symlink() for p in (raw, *raw.parents)):
        raise CacheUnavailable('Unsafe cache alias')
    resolved = raw.resolve()
    protected = {ROOT/'data/library.db', Path(os.environ.get('CHESS_COACH_DB', ROOT/'data/library.db')).resolve()}
    protected |= {Path(str(p)+suffix) for p in tuple(protected) for suffix in ('-wal','-shm','-journal')}
    backup_dir = (ROOT/'data/backups').resolve()
    if resolved in protected or backup_dir in resolved.parents:
        raise CacheUnavailable('Cache cannot alias a library or backup')
    for candidate in [*protected, *backup_dir.glob('*')]:
        if candidate.is_file() and resolved.exists() and os.path.samefile(resolved, candidate):
            raise CacheUnavailable('Cache cannot alias a library or backup')
    if resolved.exists() and resolved.stat().st_nlink != 1:
        raise CacheUnavailable('Hard-linked cache forbidden')
    return resolved


DDL = (
    'CREATE TABLE IF NOT EXISTS eval_cache_meta (id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER NOT NULL, identity_version INTEGER NOT NULL, adapter_version INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS eval_positions (id INTEGER PRIMARY KEY, identity_version INTEGER NOT NULL, variant TEXT NOT NULL, fen TEXT NOT NULL, UNIQUE(identity_version,variant,fen))',
    'CREATE TABLE IF NOT EXISTS eval_contexts (id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL REFERENCES eval_positions(id), hash TEXT NOT NULL UNIQUE, canonical TEXT NOT NULL UNIQUE)',
    'CREATE TABLE IF NOT EXISTS eval_sources (id INTEGER PRIMARY KEY, hash TEXT NOT NULL UNIQUE, canonical TEXT NOT NULL UNIQUE)',
    'CREATE TABLE IF NOT EXISTS eval_configs (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES eval_sources(id), hash TEXT NOT NULL UNIQUE, family TEXT NOT NULL, canonical TEXT NOT NULL UNIQUE)',
    '''CREATE TABLE IF NOT EXISTS eval_records (id INTEGER PRIMARY KEY,
       context_id INTEGER NOT NULL REFERENCES eval_contexts(id), config_id INTEGER NOT NULL REFERENCES eval_configs(id),
       hash TEXT NOT NULL, depth INTEGER NOT NULL CHECK(depth>=0), nodes INTEGER, returned_pvs INTEGER NOT NULL CHECK(returned_pvs BETWEEN 1 AND 5),
       first_seen REAL NOT NULL, last_seen REAL NOT NULL, expires REAL, data TEXT NOT NULL,
       UNIQUE(context_id,config_id,hash))''',
    '''CREATE TABLE IF NOT EXISTS eval_pvs (record_id INTEGER NOT NULL REFERENCES eval_records(id) ON DELETE CASCADE,
       rank INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 5), kind TEXT NOT NULL, cp INTEGER, mate INTEGER, winner TEXT, moves TEXT NOT NULL, original_moves TEXT,
       PRIMARY KEY(record_id,rank), CHECK(
       (kind='cp' AND cp IS NOT NULL AND mate IS NULL AND winner IS NULL) OR
       (kind='mate' AND cp IS NULL AND mate IS NOT NULL AND mate!=0 AND winner IS NULL) OR
       (kind='terminal_checkmate' AND cp IS NULL AND mate=0 AND winner IN ('white','black')))) WITHOUT ROWID''',
    'CREATE TABLE IF NOT EXISTS eval_cloud_misses (key TEXT PRIMARY KEY, expires REAL NOT NULL) WITHOUT ROWID',
    '''CREATE TABLE IF NOT EXISTS eval_cloud_control (id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
       next_allowed REAL NOT NULL DEFAULT 0, cooldown REAL NOT NULL DEFAULT 0, last_clock REAL NOT NULL DEFAULT 0,
       minute_start REAL NOT NULL DEFAULT 0, minute_count INTEGER NOT NULL DEFAULT 0,
       hour_start REAL NOT NULL DEFAULT 0, hour_count INTEGER NOT NULL DEFAULT 0, starts TEXT NOT NULL DEFAULT '[]')''',
    'CREATE INDEX IF NOT EXISTS eval_record_lookup ON eval_records(context_id,config_id,depth DESC)',
    'CREATE INDEX IF NOT EXISTS eval_record_expiry ON eval_records(expires,first_seen)',
    'CREATE INDEX IF NOT EXISTS eval_config_family ON eval_configs(family)',
    'CREATE INDEX IF NOT EXISTS eval_record_config ON eval_records(config_id)',
    'CREATE INDEX IF NOT EXISTS eval_context_position ON eval_contexts(position_id)',
    'CREATE INDEX IF NOT EXISTS eval_config_source ON eval_configs(source_id)',
    'CREATE INDEX IF NOT EXISTS eval_miss_expiry ON eval_cloud_misses(expires)',
)


class EvaluationCache:
    def __init__(self, path, limits=None, clock=time.time):
        self.path = safe_path(path)
        self.limits = limits or CacheLimits()
        self.clock = clock

    def sizes(self):
        return {name: p.stat().st_size if p.exists() else 0 for name, p in
                [('main', self.path), ('wal', Path(str(self.path)+'-wal')), ('shm', Path(str(self.path)+'-shm'))]}

    def _check(self, conn):
        try:
            row = conn.execute('SELECT schema_version,identity_version,adapter_version FROM eval_cache_meta WHERE id=1').fetchone()
        except sqlite3.DatabaseError as exc:
            raise CacheUnavailable('CACHE_UNINITIALIZED_OR_INCOMPATIBLE') from exc
        if row is None or tuple(row) != VERSION:
            raise CacheUnavailable('INCOMPATIBLE_CACHE_VERSION')

    @contextmanager
    def reader(self):
        # Recheck aliases on every open, including after object construction.
        safe_path(self.path)
        if not self.path.is_file():
            raise CacheUnavailable('CACHE_UNINITIALIZED')
        conn = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=0.25)
        conn.row_factory = sqlite3.Row
        deadline = time.monotonic()+5
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            self._check(conn)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def writer(self, initializing=False, maintenance=False):
        safe_path(self.path)
        if not initializing and not self.path.is_file():
            raise CacheUnavailable('CACHE_UNINITIALIZED')
        conn = sqlite3.connect(self.path, timeout=0.05, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            page_size = conn.execute('PRAGMA page_size').fetchone()[0]
            conn.execute(f'PRAGMA max_page_count={(self.limits.max_bytes-self.limits.reserve_bytes)//page_size}')
            for attempt in range(4):
                try:
                    conn.execute('BEGIN IMMEDIATE')
                    break
                except sqlite3.OperationalError as exc:
                    if getattr(exc, 'sqlite_errorcode', None) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == 3:
                        raise
                    time.sleep(0.01*(attempt+1))
            if not initializing:
                self._check(conn)
            if sum(self.sizes().values())+(min(2*1024**2,self.limits.reserve_bytes) if maintenance else self.limits.reserve_bytes) > self.limits.max_bytes:
                raise CapacityError('PHYSICAL_CAPACITY; run cache maintenance')
            yield conn
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse arbitrary existing SQLite databases (particularly libraries).
        if self.path.exists() and self.path.stat().st_size:
            with sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if tables and 'eval_cache_meta' not in tables:
                    raise CacheUnavailable('Refusing non-cache database')
        with self.writer(initializing=True) as conn:
            for sql in DDL:
                conn.execute(sql)
            conn.execute('INSERT OR IGNORE INTO eval_cache_meta VALUES(1,?,?,?)', VERSION)
            self._check(conn)
            conn.execute('INSERT OR IGNORE INTO eval_cloud_control(id) VALUES(1)')
        # WAL is configured only on explicit initialization, never on reads.
        with sqlite3.connect(self.path, timeout=1) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA journal_size_limit=8388608')

    @staticmethod
    def _dictionary(conn, table, canonical, **columns):
        key = digest(canonical)
        text = canonical_json(canonical)
        row = conn.execute(f'SELECT id,canonical FROM {table} WHERE hash=?', (key,)).fetchone()
        if row:
            if row['canonical'] != text:
                raise CacheUnavailable('Identity digest collision')
            return row['id']
        fields = ['hash', 'canonical', *columns]
        values = [key, text, *columns.values()]
        return conn.execute(f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" for _ in fields)})', values).lastrowid

    def ingest(self, evaluation: Evaluation, fence=None):
        # Revalidate even caller-supplied model_construct/model_copy objects.
        evaluation = Evaluation.model_validate(evaluation.model_dump())
        cfg = evaluation.config
        provider = cfg.source.kind == 'lichess_cloud'
        context = evaluation.context.identity(provider=provider)
        now = self.clock()
        payload = evaluation.model_dump(exclude={'context', 'config', 'pvs'})
        content_hash = digest({'data': payload, 'pvs': [p.model_dump() for p in evaluation.pvs]})
        with self.writer() as conn:
            if fence is not None:
                row = conn.execute('SELECT owner,lease_until,last_clock FROM eval_cloud_control WHERE id=1').fetchone()
                if row['owner'] != fence[0] or row['lease_until'] <= self.clock() or self.clock() < row['last_clock']:
                    raise CacheUnavailable('CLOUD_LEASE_LOST')
            conn.execute('INSERT OR IGNORE INTO eval_positions(identity_version,variant,fen) VALUES(1,?,?)', (context['variant'], context['position']))
            pid = conn.execute('SELECT id FROM eval_positions WHERE identity_version=1 AND variant=? AND fen=?', (context['variant'], context['position'])).fetchone()[0]
            cid = self._dictionary(conn, 'eval_contexts', context, position_id=pid)
            sid = self._dictionary(conn, 'eval_sources', cfg.source.model_dump())
            config_id = self._dictionary(conn, 'eval_configs', cfg.model_dump(), source_id=sid, family=cfg.family())
            existing = conn.execute('SELECT id FROM eval_records WHERE context_id=? AND config_id=? AND hash=?', (cid, config_id, content_hash)).fetchone()
            if existing:
                conn.execute('UPDATE eval_records SET last_seen=?,expires=? WHERE id=?',
                             (now, now+self.limits.cloud_fresh_days*86400 if provider else None, existing[0]))
                return existing[0]
            if conn.execute('SELECT count(*) FROM eval_contexts').fetchone()[0] > self.limits.max_contexts:
                raise CapacityError('CONTEXT_CAPACITY')
            groups = conn.execute('SELECT count(DISTINCT config_id) FROM eval_records WHERE context_id=?', (cid,)).fetchone()[0]
            present = conn.execute('SELECT 1 FROM eval_records WHERE context_id=? AND config_id=?', (cid, config_id)).fetchone()
            if not present and groups >= self.limits.configs_per_context:
                raise CapacityError('CONFIG_CAPACITY')
            rid = conn.execute('''INSERT INTO eval_records(context_id,config_id,hash,depth,nodes,returned_pvs,first_seen,last_seen,expires,data)
                VALUES(?,?,?,?,?,?,?,?,?,?)''', (cid, config_id, content_hash, evaluation.achieved_depth,
                evaluation.nodes if evaluation.nodes is not None else (evaluation.knodes*1000 if evaluation.knodes is not None else None),
                len(evaluation.pvs), now, now, now+self.limits.cloud_fresh_days*86400 if provider else None, canonical_json(payload))).lastrowid
            for rank, pv in enumerate(evaluation.pvs, 1):
                conn.execute('INSERT INTO eval_pvs VALUES(?,?,?,?,?,?,?,?)', (rid, rank, pv.kind, pv.cp, pv.mate, pv.winner, canonical_json(pv.moves), pv.original_moves))
            self._prune_context(conn, cid, config_id)
            if not conn.execute('SELECT 1 FROM eval_records WHERE id=?', (rid,)).fetchone():
                raise CapacityError('Observation excluded by retention; stronger record preserved')
            if conn.execute('SELECT count(*) FROM eval_records').fetchone()[0] > self.limits.max_records:
                raise CapacityError('RECORD_CAPACITY; run maintenance')
            return rid

    def _prune_context(self, conn, cid, config_id):
        rows = conn.execute('SELECT id FROM eval_records WHERE context_id=? AND config_id=? ORDER BY depth DESC,nodes DESC,first_seen DESC,hash ASC', (cid, config_id)).fetchall()
        best = rows[0][0]
        recent = conn.execute('SELECT id FROM eval_records WHERE context_id=? AND config_id=? AND id!=? ORDER BY first_seen DESC,hash ASC', (cid, config_id, best)).fetchall()
        for row in recent[max(0, self.limits.per_config-1):]:
            conn.execute('DELETE FROM eval_records WHERE id=?', (row[0],))
        rows = conn.execute('''SELECT id,config_id FROM eval_records WHERE context_id=?
            ORDER BY depth DESC,nodes DESC,first_seen DESC,hash ASC''', (cid,)).fetchall()
        protected = set()
        seen = set()
        for row in rows:
            if row['config_id'] not in seen:
                seen.add(row['config_id']); protected.add(row['id'])
        surplus = len(rows)-self.limits.per_context
        candidates = conn.execute('SELECT id FROM eval_records WHERE context_id=? ORDER BY first_seen ASC,hash ASC', (cid,)).fetchall()
        for row in candidates:
            if surplus > 0 and row[0] not in protected:
                conn.execute('DELETE FROM eval_records WHERE id=?', (row[0],)); surplus -= 1
        if surplus > 0:
            raise CapacityError('PROTECTED_RECORD_CAPACITY')

    def lookup(self, request: Lookup, conn=None):
        if conn is None:
            with self.reader() as reader:
                return self.lookup(request, reader)
        exact = request.context.identity()
        provider = request.context.identity(provider=True)
        hashes = (digest(exact), digest(provider))
        rows = conn.execute('''SELECT r.*, c.hash AS config_hash,c.family,c.canonical AS config,
            x.canonical AS context FROM eval_records r JOIN eval_contexts x ON x.id=r.context_id
            JOIN eval_configs c ON c.id=r.config_id WHERE x.hash IN (?,?)
            ORDER BY r.depth DESC,r.nodes DESC,r.first_seen DESC,r.hash ASC LIMIT 513''', hashes).fetchall()
        if len(rows) > 512:
            raise CacheUnavailable('Context candidate limit exceeded')
        groups = {}
        eligible = []
        for row in rows:
            context = json.loads(row['context'])
            if canonical_json(context) not in (canonical_json(exact), canonical_json(provider)):
                raise CacheUnavailable('Context identity collision')
            config = json.loads(row['config']); source = config['source']['kind']
            trusted = source == 'local_stockfish' and context['history_complete']
            compatibility = 'TRUSTED_CONTEXT_MATCH' if trusted else 'ADVISORY_CONTEXT_UNVERIFIED'
            group = groups.setdefault(row['config_hash'], {'config_hash': row['config_hash'], 'family_hash': row['family'], 'source': config['source'],
                        'requested_depth': config['depth'], 'requested_multipv': config['multipv'], 'available_depths': set(),
                        'max_returned_pvs': 0, 'context_compatibility': compatibility})
            group['available_depths'].add(row['depth']); group['max_returned_pvs'] = max(group['max_returned_pvs'], row['returned_pvs'])
            if request.source and source != request.source: continue
            if request.config_hash and row['config_hash'] != request.config_hash: continue
            if request.family_hash and row['family'] != request.family_hash: continue
            if request.require_trusted and not trusted: continue
            if not trusted and not request.allow_advisory: continue
            if row['depth'] < request.min_depth: continue
            if row['returned_pvs'] < request.min_pvs: continue
            if row['returned_pvs'] < config['multipv'] and not request.allow_partial: continue
            stale = row['expires'] is not None and row['expires'] <= self.clock()
            if stale and not request.allow_stale: continue
            eligible.append((row, config, compatibility, stale))
        available = []
        for key in sorted(groups)[:20]:
            group = groups[key]; group['available_depths'] = sorted(group['available_depths'], reverse=True); available.append(group)
        result = {'status': 'INCOMPATIBLE' if rows else 'CACHE_MISS', 'evaluation': None,
                  'context_compatibility': 'INCOMPATIBLE' if rows else 'UNAVAILABLE',
                  'available': available, 'available_truncated': len(groups)>20,
                  'coverage_basis': 'requested_context_only', 'partial': False}
        if not eligible:
            return result
        local = [e for e in eligible if e[1]['source']['kind'] == 'local_stockfish']
        eligible = local or eligible
        if len({e[0]['family'] for e in eligible}) > 1:
            result['status'] = 'CONFIG_REQUIRED'
            return result
        row, config, compatibility, stale = eligible[0]
        pvs = [dict(p) for p in conn.execute('SELECT rank,kind,cp,mate,winner,moves,original_moves FROM eval_pvs WHERE record_id=? ORDER BY rank', (row['id'],))]
        for pv in pvs: pv['moves'] = json.loads(pv['moves'])
        value = json.loads(row['data'])
        value.update({'record_id': row['id'], 'pvs': pvs, 'perspective': 'white', 'source': config['source'], 'config': config,
                      'config_hash': row['config_hash'], 'family_hash': row['family'], 'observed_at': row['first_seen'],
                      'last_seen': row['last_seen'], 'stale': stale, 'returned_multipv': row['returned_pvs'],
                      'context_compatibility': compatibility,
                      'limitations': [] if compatibility == 'TRUSTED_CONTEXT_MATCH' else ['Draw/repetition context is not verified; advisory only']})
        result.update(status='CACHE_HIT', evaluation=value, context_compatibility=compatibility,
                      partial=row['returned_pvs'] < config['multipv'], selection_reason='compatible source/family; depth, nodes, observation time, digest')
        return result

    def status(self):
        with self.reader() as conn:
            counts = {table: conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in
                      ('eval_positions', 'eval_contexts', 'eval_records', 'eval_cloud_misses')}
            control = dict(conn.execute('SELECT cooldown,next_allowed,lease_until,minute_count,hour_count FROM eval_cloud_control WHERE id=1').fetchone())
        return {'schema_version': 1, 'identity_version': 1, 'counts': counts, 'storage_bytes': self.sizes(),
                'limits': self.limits.model_dump(), 'cloud_control': control, 'coverage_basis': 'stored_cache_only; not library coverage'}

    def maintain(self, max_rows=100, seconds=2):
        if type(max_rows) is not int or not 1 <= max_rows <= 100 or not 0 < seconds <= 30:
            raise ValueError('Maintenance bounds exceeded')
        deadline = time.monotonic()+seconds; now = self.clock(); deleted = 0
        # Keep worst-case B-tree/PV/orphan changes inside physical admission headroom.
        # Repeated invocations drain larger caches; this is reported, never a hidden full sweep.
        max_rows = min(max_rows, 8)
        # Maintenance must work at physical capacity; checkpoint first, without modifying library.
        with sqlite3.connect(self.path, timeout=0.25) as c:
            self._check(c); c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with self.writer(maintenance=True) as conn:
            conn.set_progress_handler(lambda: int(time.monotonic()>deadline), 1000)
            conn.execute('DELETE FROM eval_cloud_misses WHERE key IN (SELECT key FROM eval_cloud_misses WHERE expires<=? ORDER BY expires LIMIT ?)', (now, max_rows))
            rows = conn.execute('''SELECT id FROM eval_records WHERE expires<=? OR first_seen<?
                 ORDER BY coalesce(expires,first_seen),hash LIMIT ?''', (now, now-self.limits.retention_days*86400, max_rows)).fetchall()
            for row in rows:
                conn.execute('DELETE FROM eval_records WHERE id=?', (row[0],)); deleted += 1
            # Also free oldest records at configured logical capacity. Explicit maintenance may evict best records.
            surplus = max(0, conn.execute('SELECT count(*) FROM eval_records').fetchone()[0]-self.limits.max_records+1,
                          int(conn.execute('SELECT count(*) FROM eval_contexts').fetchone()[0] >= self.limits.max_contexts))
            for row in conn.execute('SELECT id FROM eval_records ORDER BY first_seen,hash LIMIT ?', (min(max_rows-deleted, surplus),)).fetchall():
                conn.execute('DELETE FROM eval_records WHERE id=?', (row[0],)); deleted += 1
            for table, child, fk in [('eval_contexts','eval_records','context_id'), ('eval_configs','eval_records','config_id'),
                                     ('eval_sources','eval_configs','source_id'), ('eval_positions','eval_contexts','position_id')]:
                conn.execute(f'DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE NOT EXISTS (SELECT 1 FROM {child} WHERE {fk}={table}.id) LIMIT ?)', (max_rows,))
        return {'deleted_records': deleted, 'effective_batch_limit': max_rows, 'bounded': True, 'storage_bytes': self.sizes()}
