"""Position identity, bounded replay, atomic publication and SQL exploration.

No engine, opening classification or game fingerprint behavior is changed here.
A PositionIndex owns no connection: callers must supply their own connection and
must not use that connection concurrently or nest these write transactions.
"""
from contextlib import contextmanager
import io
import sqlite3
import time

import chess
import chess.pgn

from .position_schema import IDENTITY_VERSION, check_version, utc_now

MAX_PGN_BYTES = 2 * 1024 * 1024
MAX_PLIES = 10000


class UnsupportedVariant(ValueError):
    pass


def canonical_position(fen_or_board, variant="standard"):
    if variant != "standard":
        raise UnsupportedVariant("Only standard chess position indexing is supported")
    if isinstance(fen_or_board, str):
        parts = fen_or_board.split()
        if len(parts) == 4:
            parts += ["0", "1"]
        if len(parts) != 6:
            raise ValueError("FEN must contain four or six fields")
        board = chess.Board(" ".join(parts))
    else:
        board = fen_or_board
    if type(board) is not chess.Board or board.chess960:
        raise UnsupportedVariant("Only standard chess position indexing is supported")
    if not board.is_valid():
        raise ValueError("Invalid standard chess position")
    return board.epd(en_passant="legal", promoted=False)


def prepare_game(pgn, max_pgn_bytes=MAX_PGN_BYTES, max_plies=MAX_PLIES):
    if len(pgn.encode("utf-8")) > max_pgn_bytes:
        raise ValueError("PGN exceeds indexing byte limit")
    headers = chess.pgn.read_headers(io.StringIO(pgn))
    if headers and headers.get("Variant", "standard").lower() not in (
        "standard", "chess", "classical", "normal", "from position"
    ):
        raise UnsupportedVariant("Only standard chess position indexing is supported")
    stream = io.StringIO(pgn)
    game = chess.pgn.read_game(stream)
    if game is None:
        raise ValueError("No PGN game")
    # Detect variants before interpreting them as orthodox positions.
    board = game.board()
    canonical_position(board)
    if game.errors:
        raise ValueError("PGN parse errors: " + str(game.errors[0]))
    if stream.read().strip():
        raise ValueError("Expected one game per library record")
    rows = []
    for ply, move in enumerate(game.mainline_moves()):
        if ply >= max_plies:
            raise ValueError("PGN exceeds indexing ply limit")
        if not move or not board.is_legal(move):
            raise ValueError("Null or illegal mainline move")
        rows.append((ply, canonical_position(board), board.uci(move)))
        board.push(move)
    rows.append((len(rows), canonical_position(board), None))
    return rows


@contextmanager
def write_transaction(conn):
    """Bounded retries on writer acquisition only; never retry partial work."""
    if conn.in_transaction:
        raise RuntimeError("Position writes require an independent transaction")
    # Respect a smaller caller-configured timeout, cap legacy's 30s timeout.
    original = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    conn.execute(f"PRAGMA busy_timeout={min(original, 250)}")
    try:
        for attempt in range(4):
            try:
                conn.execute("BEGIN IMMEDIATE")
                break
            except sqlite3.OperationalError as exc:
                if exc.sqlite_errorcode not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == 3:
                    raise
                time.sleep(0.025 * (attempt + 1))
        try:
            yield
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.execute(f"PRAGMA busy_timeout={original}")


class PositionIndex:
    def __init__(self, conn):
        self.conn = conn
        check_version(conn)
        if conn.execute("PRAGMA journal_mode").fetchone()[0] not in ("wal", "memory"):
            raise RuntimeError("Disk position indexing requires WAL mode")

    def reconcile(self, limit=100):
        """A bounded anti-join, without a chronological-ID/cursor assumption."""
        if not 1 <= limit <= 1000:
            raise ValueError("Reconciliation limit must be 1..1000")
        with write_transaction(self.conn):
            rows = self.conn.execute("""SELECT g.game_id FROM games g
                LEFT JOIN game_position_index s ON s.game_id=g.game_id
                WHERE s.game_id IS NULL ORDER BY g.game_id LIMIT ?""", (limit,)).fetchall()
            self.conn.executemany("""INSERT INTO game_position_index
                (game_id,target_version,status,updated_at) VALUES (?,?,'PENDING',?)""",
                [(r[0], IDENTITY_VERSION, utc_now()) for r in rows])
        return len(rows)

    def request_reindex(self, game_id, retry_only=False):
        with write_transaction(self.conn):
            if not self.conn.execute("SELECT 1 FROM games WHERE game_id=?", (game_id,)).fetchone():
                return False
            self.conn.execute("""INSERT OR IGNORE INTO game_position_index
                (game_id,target_version,status,updated_at) VALUES (?,?,'PENDING',?)""",
                (game_id, IDENTITY_VERSION, utc_now()))
            condition = " AND status IN ('FAILED','UNSUPPORTED')" if retry_only else ""
            self.conn.execute("""UPDATE game_position_index SET status='PENDING',
                target_version=?,error_summary=NULL,updated_at=? WHERE game_id=?""" + condition,
                (IDENTITY_VERSION, utc_now(), game_id))
        return True

    def next_pending(self):
        row = self.conn.execute("""SELECT game_id FROM game_position_index
            WHERE status='PENDING' AND target_version=? ORDER BY game_id LIMIT 1""",
            (IDENTITY_VERSION,)).fetchone()
        return row[0] if row else None

    def index_game(self, game_id):
        game = self.conn.execute("SELECT pgn,fingerprint FROM games WHERE game_id=?", (game_id,)).fetchone()
        if not game:
            return "DELETED"
        pgn, fingerprint = game
        try:
            rows = prepare_game(pgn)
        except ValueError as exc:
            status = "UNSUPPORTED" if isinstance(exc, UnsupportedVariant) else "FAILED"
            with write_transaction(self.conn):
                # A concurrent successful publisher must not be overwritten.
                self.conn.execute("""UPDATE game_position_index SET status=?,
                    error_summary=?,attempt_count=attempt_count+1,updated_at=?
                    WHERE game_id=? AND status='PENDING' AND target_version=?
                    AND EXISTS (SELECT 1 FROM games g WHERE g.game_id=? AND g.fingerprint=? AND g.pgn=?)""",
                    (status, str(exc)[:1000], utc_now(), game_id, IDENTITY_VERSION, game_id, fingerprint, pgn))
            return status
        with write_transaction(self.conn):
            current = self.conn.execute("""SELECT g.pgn,g.fingerprint,s.status,s.target_version
                FROM games g JOIN game_position_index s ON s.game_id=g.game_id
                WHERE g.game_id=?""", (game_id,)).fetchone()
            if not current:
                return "DELETED"
            if current[0] != pgn or current[1] != fingerprint or current[3] != IDENTITY_VERSION:
                return "STALE"
            if current[2] != "PENDING":
                return "SKIPPED"
            self.conn.execute("DELETE FROM position_occurrences WHERE game_id=?", (game_id,))
            # One game's bounded set; parameter count never grows with corpus size.
            keys = list(dict.fromkeys(row[1] for row in rows))
            self.conn.executemany("""INSERT INTO positions(identity_version,variant,canonical_fen)
                VALUES (?,'standard',?) ON CONFLICT DO NOTHING""",
                [(IDENTITY_VERSION, key) for key in keys])
            ids = {key: self.conn.execute("""SELECT position_id FROM positions
                WHERE identity_version=? AND variant='standard' AND canonical_fen=?""",
                (IDENTITY_VERSION, key)).fetchone()[0] for key in keys}
            self.conn.executemany("""INSERT INTO position_occurrences
                (game_id,ply,position_id,next_move_uci) VALUES (?,?,?,?)""",
                [(game_id, ply, ids[key], move) for ply, key, move in rows])
            self.conn.execute("""UPDATE game_position_index SET status='COMPLETE',
                indexed_version=?,indexed_fingerprint=?,indexed_plies=?,indexed_at=?,
                updated_at=?,error_summary=NULL,attempt_count=attempt_count+1 WHERE game_id=?""",
                (IDENTITY_VERSION, fingerprint, len(rows)-1, utc_now(), utc_now(), game_id))
        return "COMPLETE"


def player_clause(player=None, player_color="either"):
    if player_color not in ("either", "white", "black"):
        raise ValueError("player_color must be white, black or either")
    if player is None:
        return "1=1", []
    if not player or len(player) > 200:
        raise ValueError("player must contain 1..200 characters")
    if player_color == "either":
        return "(g.white=? COLLATE NOCASE OR g.black=? COLLATE NOCASE)", [player, player]
    return f"g.{player_color}=? COLLATE NOCASE", [player]


class PositionQueries:
    """Read-only queries; caller supplies a read transaction for snapshots."""
    def __init__(self, conn, player=None, player_color="either"):
        self.conn = conn
        self.where, self.params = player_clause(player, player_color)
        self.filters = {"player": player, "player_color": player_color}
        self.available = bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='position_index_meta'").fetchone())
        if self.available:
            check_version(conn)

    def coverage(self):
        total = self.conn.execute("SELECT count(*) FROM games g WHERE " + self.where, self.params).fetchone()[0]
        counts = {s: 0 for s in ("PENDING", "COMPLETE", "FAILED", "UNSUPPORTED")}
        indexed = 0
        missing = total
        if self.available:
            states = self.conn.execute("""SELECT s.status,count(*) FROM games g
                JOIN game_position_index s ON s.game_id=g.game_id WHERE """ + self.where + " GROUP BY s.status", self.params)
            counts.update(dict(states.fetchall()))
            missing = total - sum(counts.values())
            indexed = self.conn.execute("""SELECT count(*) FROM games g
                JOIN game_position_index s ON s.game_id=g.game_id
                WHERE s.indexed_version=? AND s.indexed_fingerprint=g.fingerprint AND """ + self.where,
                [IDENTITY_VERSION] + self.params).fetchone()[0]
        partial = missing > 0 or counts["COMPLETE"] != total or indexed != total
        return {"schema_available": self.available, "identity_version": IDENTITY_VERSION,
                "total_games": total, "indexed_games": indexed, "missing_games": missing,
                "pending_games": counts["PENDING"], "complete_games": counts["COMPLETE"],
                "failed_games": counts["FAILED"], "unsupported_games": counts["UNSUPPORTED"],
                "partial_index": partial, "absence_is_conclusive": not partial,
                "filters": self.filters, "as_of": utc_now()}

    def find(self, fen, variant="standard"):
        key = canonical_position(fen, variant)
        row = self.conn.execute("""SELECT position_id FROM positions
            WHERE identity_version=? AND variant=? AND canonical_fen=?""",
            (IDENTITY_VERSION, variant, key)).fetchone() if self.available else None
        return self.summary(row[0] if row else None, key)

    def require(self, position_id):
        row = self.conn.execute("SELECT canonical_fen FROM positions WHERE position_id=? AND identity_version=? AND variant='standard'",
                                (position_id, IDENTITY_VERSION)).fetchone() if self.available else None
        if not row:
            raise KeyError(position_id)
        return row[0]

    def summary(self, position_id, key=None):
        if key is None:
            key = self.require(position_id)
        counts = (0, 0, 0)
        if position_id is not None:
            counts = tuple(self.conn.execute("""SELECT count(*),count(DISTINCT o.game_id),
                count(o.next_move_uci) FROM position_occurrences o JOIN games g ON g.game_id=o.game_id
                WHERE o.position_id=? AND """ + self.where, [position_id] + self.params).fetchone())
        return {"position_id": position_id, "identity_version": IDENTITY_VERSION,
                "variant": "standard", "canonical_fen": key, "occurrence_count": counts[0],
                "distinct_game_count": counts[1], "outgoing_occurrence_count": counts[2],
                "final_occurrence_count": counts[0]-counts[2], "coverage": self.coverage()}

    def occurrences(self, position_id, limit=50, after_game="", after_ply=-1):
        self.require(position_id)
        rows = self.conn.execute("""SELECT o.game_id,o.ply,o.next_move_uci
            FROM position_occurrences o JOIN games g ON g.game_id=o.game_id
            WHERE o.position_id=? AND (o.game_id,o.ply) > (?,?) AND """ + self.where +
            " ORDER BY o.game_id,o.ply LIMIT ?", [position_id, after_game, after_ply] + self.params + [limit+1]).fetchall()
        items = [dict(row) for row in rows[:limit]]
        cursor = {"after_game": items[-1]["game_id"], "after_ply": items[-1]["ply"]} if len(rows) > limit else None
        return {"items": items, "next_cursor": cursor, "coverage": self.coverage()}

    def games(self, position_id, limit=50, after_game=""):
        self.require(position_id)
        rows = self.conn.execute("""SELECT g.game_id,g.white,g.black,g.result,g.date,
            g.source,g.analysis_status,op.opening_name,op.eco,
            count(*) AS occurrence_count,min(o.ply) AS first_ply
            FROM position_occurrences o JOIN games g ON g.game_id=o.game_id
            LEFT JOIN game_openings op ON op.game_id=g.game_id
            WHERE o.position_id=? AND g.game_id>? AND """ + self.where +
            " GROUP BY g.game_id ORDER BY g.game_id LIMIT ?",
            [position_id, after_game] + self.params + [limit+1]).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["game_id"] if len(rows) > limit else None,
                "matching_plies_endpoint": f"/api/positions/{position_id}/occurrences",
                "coverage": self.coverage()}

    def moves(self, position_id):
        key = self.require(position_id)
        # First group by move AND game. Repeated visits contribute to frequency,
        # but each game contributes exactly once to that move's outcome sample.
        rows = self.conn.execute("""WITH per_game AS (
            SELECT o.next_move_uci AS uci,o.game_id,g.result,count(*) AS visits
            FROM position_occurrences o JOIN games g ON g.game_id=o.game_id
            WHERE o.position_id=? AND o.next_move_uci IS NOT NULL AND """ + self.where + """
            GROUP BY o.next_move_uci,o.game_id)
            SELECT uci,sum(visits) AS occurrence_count,count(*) AS distinct_game_count,
                sum(result='1-0') AS white_wins,sum(result='0-1') AS black_wins,
                sum(result='1/2-1/2') AS draws,sum(result='*') AS unfinished,
                sum(result NOT IN ('1-0','0-1','1/2-1/2','*')) AS unknown_result
            FROM per_game GROUP BY uci ORDER BY uci""", [position_id]+self.params).fetchall()
        items = [dict(row) for row in rows]
        total = sum(row["occurrence_count"] for row in items)
        board = chess.Board(key + " 0 1")
        for row in items:
            row["san"] = board.san(chess.Move.from_uci(row["uci"]))
            row["move_frequency"] = row["occurrence_count"] / total if total else None
            n = row["white_wins"] + row["black_wins"] + row["draws"]
            row["completed_games"] = n
            row["white_win_rate"] = row["white_wins"] / n if n else None
            row["white_score_rate"] = (row["white_wins"] + 0.5*row["draws"]) / n if n else None
        return {"items": items, "outgoing_occurrence_count": total,
                "frequency_basis": "occurrences", "outcome_basis": "distinct_games_per_move",
                "result_perspective": "white", "coverage": self.coverage()}


def reconstruct_understanding_position(pgn, ply, budget):
    """Validate the entire mainline, retain only the selected pair of boards.

    Additive reader: no occurrence publication, schema or legacy replay changes.
    Variations are skipped; malformed mainline tails never become valid prefixes.
    """
    if len(pgn.encode('utf-8')) > MAX_PGN_BYTES:
        raise ValueError('PGN_TOO_LARGE')
    if not isinstance(ply, int) or isinstance(ply, bool) or not 0 <= ply <= MAX_PLIES:
        raise ValueError('INVALID_PLY')

    # python-chess intentionally skips unknown text; this read contract must not
    # silently accept a malformed tail. Audit tokens, not chess semantics, first.
    import re
    token = re.compile(r'(?:\s+|\{[^}]*\}|;[^\n]*|(?m:^%[^\n]*)|'
                       r'\[[A-Za-z0-9_+#=:-]+\s+"(?:[^"\\\n]|\\.)*"\s*\]|'
                       r'1/2-1/2|1-0|0-1|\*|\d+\.(?:\.\.)?|\.\.\.|\$\d{1,6}|'
                       r'[()!?]|--|Z0|0000|@@@@|'
                       r'(?:[KQRBN]?[a-h]?[1-8]?[x-]?[a-h][1-8](?:=?[QRBNqrbn])?|[O0]-[O0](?:-[O0])?)[+#]?)')
    cursor = 1 if pgn.startswith('\ufeff') else 0
    depth = 0; tokens = 0; in_moves = False; ended = False
    while cursor < len(pgn):
        budget.check()
        match = token.match(pgn, cursor)
        if match is None:raise ValueError('INVALID_PGN_TOKEN')
        text = match.group()
        if ended and not text.isspace() and not text.startswith(('{',';','%')):raise ValueError('TOKENS_AFTER_PGN_RESULT')
        if text in ('*','1-0','0-1','1/2-1/2') and depth == 0:ended = True
        if text.startswith('['):
            if in_moves:raise ValueError('UNEXPECTED_PGN_HEADER')
        elif not text.isspace() and not text.startswith(('{',';','%')):
            in_moves = True
        if text == '(' :depth += 1
        if text == ')' :depth -= 1
        if not 0 <= depth <= 64:raise ValueError('PGN_VARIATION_DEPTH')
        cursor = match.end(); tokens += 1
        if tokens > 100000:raise ValueError('PGN_TOKEN_LIMIT')
    if depth:raise ValueError('UNBALANCED_PGN_VARIATION')

    class Visitor(chess.pgn.GameBuilder):
        def begin_game(self):
            budget.check()
            self.count = 0
            return super().begin_game()

        def visit_header(self, name, value):
            budget.check()
            return super().visit_header(name, value)

        def visit_comment(self, comment):
            budget.check()  # Comments are not chess evidence and need not be retained.

        def begin_variation(self):
            budget.check()
            return chess.pgn.SKIP

        def end_variation(self):
            budget.check()

        def visit_move(self, board, move):
            budget.check()
            self.count += 1
            if self.count > MAX_PLIES:
                raise ValueError('PGN_PLY_LIMIT')
            if not move or not board.is_legal(move):
                raise ValueError('ILLEGAL_OR_NULL_MOVE')
            return super().visit_move(board, move)

        def handle_error(self, error):
            raise ValueError('INVALID_PGN') from None

    budget.check()
    stream = io.StringIO(pgn)
    game = chess.pgn.read_game(stream, Visitor=Visitor)
    if game is None or game.errors or stream.read().strip():
        raise ValueError('EXPECTED_ONE_LEGAL_PGN')
    if game.headers.get('Variant', 'standard').lower() not in ('standard', 'chess', 'classical', 'normal', 'from position'):
        raise UnsupportedVariant('STANDARD_CHESS_ONLY')
    board = game.board()
    canonical_position(board)
    root = board.fen(en_passant='legal')
    complete = root == chess.STARTING_FEN
    total = game.end().ply() - board.ply()
    if ply > total:
        raise ValueError('PLY_NOT_IN_GAME')
    # Original minor-piece history is meaningful only with a known standard root.
    actors = {chess.square_name(s): {'square': s, 'ever_moved': False}
              for s, p in board.piece_map().items() if p.piece_type in (chess.KNIGHT, chess.BISHOP)} if complete else None
    before = None; move_played = None; before_actors = None; prefix = []
    for index, move in enumerate(game.mainline_moves(), 1):
        if index > ply:
            break
        budget.check()
        if index == ply:
            before = board.copy(stack=True)
            before_actors = {k: dict(v) for k, v in actors.items()} if actors is not None else None
            move_played = move
        if actors is not None:
            victim = move.to_square
            for actor in actors.values():
                if actor['square'] == victim:
                    actor['square'] = None
                elif actor['square'] == move.from_square:
                    actor.update(square=move.to_square, ever_moved=True)
        board.push(move)
        if total <= 1000:
            prefix.append(move.uci())
    budget.check()
    return {'before': before, 'after': board, 'move': move_played, 'root_fen': root,
            'root_history_complete': complete, 'moves': prefix, 'total_plies': total,
            'before_development': before_actors, 'after_development': actors}
