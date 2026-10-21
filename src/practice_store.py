"""Explicitly initialized local practice storage. Never initializes GameLibrary."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
import time
import uuid
import chess
from .positions import reconstruct_understanding_position
from pydantic import Field
from .evaluation_cache import safe_path, CacheUnavailable
from .practice_models import Contract, PuzzleDefinition
from .puzzles import (account_key, canonical, digest, source_game, compose, question, grade, hint, reveal, legacy_seeds)
from .understanding_models import UnderstandingBudget, BudgetExceeded


class PracticeError(ValueError):
    pass


class Limits(Contract):
    puzzles: int = Field(default=5000, ge=1, le=100000)
    attempts: int = Field(default=50000, ge=1, le=1000000)
    events: int = Field(default=500000, ge=1, le=10000000)
    payload_bytes: int = Field(default=512*1024**2, ge=1024, le=8*1024**3)


DDL = (
    'CREATE TABLE practice_meta(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,library TEXT NOT NULL,limits TEXT NOT NULL,used_bytes INTEGER NOT NULL DEFAULT 0)',
    'CREATE TABLE puzzles(id TEXT PRIMARY KEY,identity TEXT NOT NULL,account TEXT NOT NULL,definition TEXT NOT NULL,created REAL NOT NULL)',
    'CREATE INDEX puzzle_account ON puzzles(account,id)',
    '''CREATE TABLE puzzle_sources(puzzle_id TEXT NOT NULL REFERENCES puzzles(id),game_id TEXT NOT NULL,ply INTEGER NOT NULL,
        fingerprint TEXT NOT NULL,pgn_hash TEXT NOT NULL,PRIMARY KEY(puzzle_id,game_id,ply,pgn_hash))''',
    '''CREATE TABLE attempts(id TEXT PRIMARY KEY,puzzle_id TEXT NOT NULL REFERENCES puzzles(id),revision INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'ACTIVE',assisted INTEGER NOT NULL DEFAULT 0,hints INTEGER NOT NULL DEFAULT 0,
        retry_of TEXT REFERENCES attempts(id),created REAL NOT NULL,finished REAL,start_key TEXT NOT NULL UNIQUE,
        start_payload TEXT NOT NULL,start_response TEXT NOT NULL)''',
    'CREATE INDEX attempt_puzzle ON attempts(puzzle_id,created,id)',
    '''CREATE TABLE attempt_events(attempt_id TEXT NOT NULL REFERENCES attempts(id),revision INTEGER NOT NULL,
        request_key TEXT NOT NULL,payload TEXT NOT NULL,response TEXT NOT NULL,created REAL NOT NULL,
        PRIMARY KEY(attempt_id,revision),UNIQUE(attempt_id,request_key))''',
)


class PracticeStore:
    def __init__(self, path, library, limits=None):
        self.library = Path(library).resolve()
        self.path = safe_path(path)
        self.limits = limits or Limits()
        self._safe()

    def _safe(self):
        safe_path(self.path)
        protected = [self.library]
        for key in ('CHESS_COACH_EVAL_CACHE_DB', 'CHESS_COACH_PROGRESSIVE_DB'):
            if os.environ.get(key):
                protected.append(Path(os.environ[key]).resolve())
        protected += [Path(str(p)+s) for p in tuple(protected) for s in ('-wal', '-shm', '-journal')]
        for p in protected:
            if self.path == p or (p.exists() and self.path.exists() and os.path.samefile(p, self.path)):
                raise PracticeError('UNSAFE_STORE_ALIAS')

    @contextmanager
    def connection(self, write=False, initialize=False):
        self._safe()
        if not self.path.is_file() and not initialize:
            raise PracticeError('STORE_UNINITIALIZED')
        conn = sqlite3.connect(str(self.path) if write else self.path.as_uri()+'?mode=ro', uri=not write, timeout=.25)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('PRAGMA busy_timeout=250')
            if not write:
                conn.execute('PRAGMA query_only=ON')
            deadline = time.monotonic()+2
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if not initialize:
                self._meta(conn)
            yield conn
            if write:
                conn.commit()
        finally:
            conn.rollback()
            conn.close()

    def _meta(self, c):
        try:
            row = c.execute('SELECT * FROM practice_meta WHERE id=1').fetchone()
        except sqlite3.DatabaseError:
            raise PracticeError('STORE_INCOMPATIBLE') from None
        if not row or row['version'] != 1 or row['library'] != str(self.library) or row['limits'] != canonical(self.limits.model_dump()):
            raise PracticeError('STORE_INCOMPATIBLE')
        return row

    def initialize(self):
        with self.connection(True, initialize=True) as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables:
                self._meta(c)
                return
            for sql in DDL:
                c.execute(sql)
            c.execute('INSERT INTO practice_meta(id,version,library,limits) VALUES(1,1,?,?)',
                      (str(self.library), canonical(self.limits.model_dump())))

    def capacity(self, c, table, added_bytes):
        maximum = getattr(self.limits, {'puzzles': 'puzzles', 'attempts': 'attempts', 'attempt_events': 'events'}[table])
        if c.execute(f'SELECT count(*) FROM {table}').fetchone()[0] >= maximum:
            raise PracticeError('CAPACITY_REACHED')
        row = self._meta(c)
        if row['used_bytes'] + added_bytes > self.limits.payload_bytes:
            raise PracticeError('CAPACITY_REACHED')
        c.execute('UPDATE practice_meta SET used_bytes=used_bytes+? WHERE id=1', (added_bytes,))

    def status(self):
        with self.connection() as c:
            meta = self._meta(c)
            return {'status': 'AVAILABLE', 'version': 1, 'limits': self.limits.model_dump(),
                    'logical_payload_bytes': meta['used_bytes'],
                    'counts': {t: c.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('puzzles', 'attempts', 'attempt_events')}}

    def _record(self, pid):
        with self.connection() as c:
            row = c.execute('SELECT definition FROM puzzles WHERE id=?', (pid,)).fetchone()
            if not row:
                raise KeyError('PUZZLE_NOT_FOUND')
            links = [dict(r) for r in c.execute('SELECT * FROM puzzle_sources WHERE puzzle_id=? ORDER BY game_id,ply,pgn_hash LIMIT 50', (pid,))]
        definition = PuzzleDefinition.model_validate_json(row[0]).model_dump()
        return definition, links

    def freshness(self, definition, links):
        for link in links:
            try:
                current = source_game(self.library, definition['account'], link['game_id'])
                if current['pgn_hash'] == link['pgn_hash'] and current['fingerprint'] == link['fingerprint']:
                    return {'game_id': link['game_id'], 'ply': link['ply']}, 'CURRENT_MATCH_AT_CHECK'
            except ValueError:
                continue
        return ({'game_id': links[0]['game_id'], 'ply': links[0]['ply']} if links else {}), 'STALE_OR_MISSING_SOURCE'

    def checked(self, pid):
        definition, links = self._record(pid)
        source, state = self.freshness(definition, links)
        if state != 'CURRENT_MATCH_AT_CHECK':
            raise PracticeError('STALE_OR_MISSING_SOURCE')
        return definition, links, source

    def postcheck(self, pid, aid):
        try:
            self.checked(pid)
        except PracticeError:
            # Cross-store transactions are unavailable. Invalidate a committed
            # response if its final source check lost the race.
            with self.connection(True) as c:
                c.execute("UPDATE attempts SET state='STALE_SOURCE' WHERE id=?", (aid,))
            raise

    def get_question(self, pid):
        definition, links = self._record(pid)
        source, state = self.freshness(definition, links)
        return question(pid, definition, source, state)

    def save(self, definition, source, ply):
        identity = canonical({k: definition[k] for k in ('policy_version', 'account', 'library', 'context', 'objective', 'witness', 'theme')})
        pid = digest(json.loads(identity))
        payload = canonical(definition)
        if len(payload.encode()) > 256*1024:
            raise PracticeError('DEFINITION_TOO_LARGE')
        with self.connection(True) as c:
            row = c.execute('SELECT identity FROM puzzles WHERE id=?', (pid,)).fetchone()
            if row and row[0] != identity:
                raise PracticeError('IDENTITY_COLLISION')
            if not row:
                self.capacity(c, 'puzzles', len((payload+identity+canonical(definition['account'])).encode()))
                c.execute('INSERT INTO puzzles VALUES(?,?,?,?,?)', (pid, identity, canonical(definition['account']), payload, time.time()))
            link = (pid, source['game_id'], ply, source['fingerprint'], source['pgn_hash'])
            exists = c.execute('SELECT 1 FROM puzzle_sources WHERE puzzle_id=? AND game_id=? AND ply=? AND pgn_hash=?',
                               (pid, source['game_id'], ply, source['pgn_hash'])).fetchone()
            if not exists:
                size = len(canonical(link).encode())
                if self._meta(c)['used_bytes']+size > self.limits.payload_bytes:
                    raise PracticeError('CAPACITY_REACHED')
                c.execute('UPDATE practice_meta SET used_bytes=used_bytes+? WHERE id=1', (size,))
                c.execute('INSERT INTO puzzle_sources VALUES(?,?,?,?,?)', link)
        return pid

    def discover(self, request):
        # Verify initialization before expensive replay; no implicit setup.
        self.status()
        deadline = time.monotonic()+5
        pushed = 0
        result = {'items': [], 'examined': [], 'skipped': [], 'partial': False,
                  'seed_coverage': [], 'engine_evidence': 'NOT_REQUESTED', 'selection': 'Explicit games; bounded player-turn sample, not whole-game tactics.'}
        seen = set()
        for selection in request.games:
            current = source_game(self.library, request.account.model_dump(), selection.game_id)
            if current['pgn_hash'] != selection.expected_pgn_sha256:
                raise PracticeError('STALE_SOURCE')
            roots = selection.plies
            if not roots:
                budget = UnderstandingBudget(board_seconds=max(.001, min(1, deadline-time.monotonic())))
                try:
                    selected = reconstruct_understanding_position(current['pgn'], 0, budget)
                except BudgetExceeded:
                    result['partial'] = True
                    result['skipped'].append({'game_id': selection.game_id, 'reason': 'SOURCE_VALIDATION_BUDGET'})
                    continue
                total = selected['total_plies']
                offset = 0 if current['color'] == ('white' if selected['after'].turn else 'black') else 1
                turns = list(range(offset, total, 2))
                # Spread a small sample across actual turns; no all-position motif search.
                seeds, seed_status = legacy_seeds(self.library, selection.game_id)
                result['seed_coverage'].append({'game_id': selection.game_id, 'status': seed_status})
                eligible_seeds = list(dict.fromkeys(n for n in seeds if n in turns))[:2]
                sample = sorted({turns[round(i*(len(turns)-1)/5)] for i in range(6)}) if turns else []
                roots = list(dict.fromkeys(eligible_seeds+sample))[:6]
            for ply in roots:
                if len(result['examined'])+len(result['skipped']) >= 12 or time.monotonic() >= deadline or pushed >= 1024:
                    result['partial'] = True
                    result['skipped'].append({'game_id': selection.game_id, 'ply': ply, 'reason': 'REQUEST_BUDGET'})
                    continue
                budget = UnderstandingBudget(board_seconds=max(.001, min(1, deadline-time.monotonic())), max_pushes=min(512, 1024-pushed))
                try:
                    definition, coverage = compose(self.library, request.account.model_dump(), current['pgn'], ply, budget)
                    result['examined'].append({'game_id': selection.game_id, 'ply': ply, **coverage})
                    if definition:
                        board_color = 'white' if chess.Board(definition['context']['fen']).turn else 'black'
                        if current['color'] != board_color:
                            result['skipped'].append({'game_id': selection.game_id, 'ply': ply, 'reason': 'OPPONENT_TURN'})
                            continue
                        fresh = source_game(self.library, request.account.model_dump(), selection.game_id)
                        if fresh['pgn_hash'] != current['pgn_hash'] or fresh['fingerprint'] != current['fingerprint']:
                            raise PracticeError('STALE_SOURCE')
                        pid = self.save(definition, current, ply)
                        if pid not in seen:
                            result['items'].append(self.get_question(pid)); seen.add(pid)
                except BudgetExceeded:
                    result['partial'] = True
                    result['skipped'].append({'game_id': selection.game_id, 'ply': ply, 'reason': 'ROOT_BUDGET'})
                finally:
                    pushed += budget.pushes
        result['child_pushes'] = pushed
        result['partial'] |= bool(result['skipped'])
        return result

    def listing(self, account, after='', limit=20):
        with self.connection() as c:
            rows = c.execute('SELECT id FROM puzzles WHERE account=? AND id>? ORDER BY id LIMIT ?',
                             (canonical(account_key(account)), after, limit+1)).fetchall()
        return {'items': [self.get_question(r[0]) for r in rows[:limit]],
                'next_cursor': rows[limit-1][0] if len(rows) > limit else None}

    def _snapshot(self, row, feedback=None):
        return {'id': row['id'], 'puzzle_id': row['puzzle_id'], 'revision': row['revision'], 'state': row['state'],
                'assisted': bool(row['assisted']), 'hints': row['hints'], 'retry_of': row['retry_of'], 'feedback': feedback}

    def start(self, request):
        payload = canonical(request.model_dump())
        # Exact duplicate returns the original response, including after the attempt advanced.
        with self.connection() as c:
            old = c.execute('SELECT start_payload,start_response FROM attempts WHERE start_key=?', (request.request_key,)).fetchone()
        if old:
            if old[0] != payload:
                raise PracticeError('IDEMPOTENCY_CONFLICT')
            self.checked(request.puzzle_id)
            return json.loads(old[1])
        definition, links, source = self.checked(request.puzzle_id)
        pid = request.puzzle_id
        with self.connection(True) as c:
            old = c.execute('SELECT start_payload,start_response FROM attempts WHERE start_key=?', (request.request_key,)).fetchone()
            if old:
                if old[0] != payload:
                    raise PracticeError('IDEMPOTENCY_CONFLICT')
                return json.loads(old[1])
            assisted = 0
            if request.retry_of:
                parent = c.execute('SELECT * FROM attempts WHERE id=?', (request.retry_of,)).fetchone()
                if not parent or parent['puzzle_id'] != pid:
                    raise PracticeError('INVALID_RETRY_PARENT')
                assisted = parent['assisted']
            # Revealing/hinting this puzzle previously makes subsequent retries assisted.
            assisted |= bool(c.execute('SELECT 1 FROM attempts WHERE puzzle_id=? AND assisted=1 LIMIT 1', (pid,)).fetchone())
            aid = uuid.uuid4().hex
            row = {'id': aid, 'puzzle_id': pid, 'revision': 0, 'state': 'ACTIVE', 'assisted': assisted, 'hints': 0, 'retry_of': request.retry_of}
            response = {'attempt': self._snapshot(row), 'question': question(pid, definition, source, 'CURRENT_MATCH_AT_CHECK')}
            encoded = canonical(response)
            self.capacity(c, 'attempts', len((payload+encoded).encode()))
            c.execute('INSERT INTO attempts(id,puzzle_id,assisted,retry_of,created,start_key,start_payload,start_response) VALUES(?,?,?,?,?,?,?,?)',
                      (aid, pid, assisted, request.retry_of, time.time(), request.request_key, payload, encoded))
        self.postcheck(pid, aid)
        return response

    def attempt(self, aid):
        with self.connection() as c:
            row = c.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
            if not row:
                raise KeyError('ATTEMPT_NOT_FOUND')
            events = [json.loads(r[0]) for r in c.execute('SELECT response FROM attempt_events WHERE attempt_id=? ORDER BY revision', (aid,))]
        q = self.get_question(row['puzzle_id'])
        if q['source']['freshness'] != 'CURRENT_MATCH_AT_CHECK':
            row = dict(row); row['state'] = 'STALE_SOURCE'
            events = []  # never disclose a stale private reveal through resume
        return {'attempt': self._snapshot(row, events[-1]['attempt']['feedback'] if events else None), 'question': q, 'events': events}

    def event(self, aid, request):
        payload = canonical(request.model_dump())
        with self.connection() as c:
            row = c.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
            old = c.execute('SELECT payload,response FROM attempt_events WHERE attempt_id=? AND request_key=?', (aid, request.request_key)).fetchone()
        if row is None:
            raise KeyError('ATTEMPT_NOT_FOUND')
        definition, links, _ = self.checked(row['puzzle_id'])
        if old:
            if old[0] != payload:
                raise PracticeError('IDEMPOTENCY_CONFLICT')
            return json.loads(old[1])
        if request.kind == 'reveal' and row['state'] == 'REVEALED':
            with self.connection() as c:
                previous = c.execute('SELECT response FROM attempt_events WHERE attempt_id=? ORDER BY revision DESC LIMIT 1', (aid,)).fetchone()
            return json.loads(previous[0])
        if request.kind == 'hint' and row['hints'] >= 2:
            raise PracticeError('HINT_LIMIT')
        if row['revision'] != request.revision:
            raise PracticeError('REVISION_CONFLICT')
        if row['revision'] >= 64:
            raise PracticeError('EVENT_LIMIT')
        if row['state'] != 'ACTIVE' and request.kind not in ('reveal', 'abandon'):
            raise PracticeError('ATTEMPT_FINISHED_RETRY_REQUIRED')
        state = row['state']; assisted = row['assisted']; hints = row['hints']
        if request.kind == 'move':
            feedback = grade(definition, request.uci)
            if feedback['code'] in ('OBJECTIVE_MET', 'DEMONSTRATED_TARGET', 'LEGAL_ALTERNATIVE_UNASSESSED'):
                state = feedback['code']
        elif request.kind == 'hint':
            hints = min(2, hints+1); assisted = 1
            feedback = {'code': 'HINT', 'level': hints, 'message': hint(definition, hints)}
        elif request.kind == 'reveal':
            assisted = 1; state = 'REVEALED'
            feedback = {'code': 'REVEALED', 'message': 'One supported witness, not a uniqueness claim.', 'solution': reveal(definition)}
        else:
            state = 'ABANDONED'
            feedback = {'code': 'ABANDONED', 'message': 'Attempt closed.'}
        # Source validation and chess work above are outside the write lock.
        self.checked(row['puzzle_id'])
        with self.connection(True) as c:
            duplicate = c.execute('SELECT payload,response FROM attempt_events WHERE attempt_id=? AND request_key=?', (aid, request.request_key)).fetchone()
            if duplicate:
                if duplicate[0] != payload:
                    raise PracticeError('IDEMPOTENCY_CONFLICT')
                return json.loads(duplicate[1])
            latest = c.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
            if latest['revision'] != request.revision:
                raise PracticeError('REVISION_CONFLICT')
            assisted |= bool(c.execute('SELECT 1 FROM attempts WHERE puzzle_id=? AND assisted=1 LIMIT 1', (row['puzzle_id'],)).fetchone())
            updated = dict(latest); updated.update(revision=request.revision+1, state=state, assisted=assisted, hints=hints)
            response = {'attempt': self._snapshot(updated, feedback)}
            encoded = canonical(response)
            if len(encoded.encode()) > 8192:
                raise PracticeError('EVENT_TOO_LARGE')
            self.capacity(c, 'attempt_events', len((payload+encoded).encode()))
            c.execute('INSERT INTO attempt_events VALUES(?,?,?,?,?,?)', (aid, updated['revision'], request.request_key, payload, encoded, time.time()))
            c.execute('UPDATE attempts SET revision=?,state=?,assisted=?,hints=?,finished=? WHERE id=?',
                      (updated['revision'], state, assisted, hints, time.time() if state != 'ACTIVE' else None, aid))
        self.postcheck(row['puzzle_id'], aid)
        return response

    def history(self, account, after='', limit=20):
        with self.connection() as c:
            rows = c.execute('''SELECT a.* FROM attempts a JOIN puzzles p ON p.id=a.puzzle_id
                WHERE p.account=? AND a.id>? ORDER BY a.id LIMIT ?''', (canonical(account_key(account)), after, limit+1)).fetchall()
        items = []
        for row in rows[:limit]:
            item = {**self._snapshot(row), 'created_at': row['created'], 'finished_at': row['finished']}
            if self.get_question(row['puzzle_id'])['source']['freshness'] != 'CURRENT_MATCH_AT_CHECK':
                item['state'] = 'STALE_SOURCE'
            items.append(item)
        return {'items': items,
                'next_cursor': rows[limit-1]['id'] if len(rows) > limit else None,
                'limitations': ['Practice outcomes are not player-strength or weakness estimates. Repeats are not independent evidence.']}
