"""Additive position-index schema. No historical replay occurs during setup."""
from datetime import datetime, timezone

SCHEMA_VERSION = 1
IDENTITY_VERSION = 1

DDL = (
    """CREATE TABLE IF NOT EXISTS position_index_meta (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        schema_version INTEGER NOT NULL, identity_version INTEGER NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS positions (
        position_id INTEGER PRIMARY KEY, identity_version INTEGER NOT NULL,
        variant TEXT NOT NULL, canonical_fen TEXT NOT NULL,
        UNIQUE(identity_version, variant, canonical_fen))""",
    """CREATE TABLE IF NOT EXISTS position_occurrences (
        game_id TEXT NOT NULL REFERENCES games(game_id) ON DELETE CASCADE,
        ply INTEGER NOT NULL CHECK(ply >= 0),
        position_id INTEGER NOT NULL REFERENCES positions(position_id),
        next_move_uci TEXT, PRIMARY KEY(game_id, ply)) WITHOUT ROWID""",
    """CREATE INDEX IF NOT EXISTS idx_position_occurrences_lookup
        ON position_occurrences(position_id, next_move_uci, game_id, ply)""",
    """CREATE TABLE IF NOT EXISTS game_position_index (
        game_id TEXT PRIMARY KEY REFERENCES games(game_id) ON DELETE CASCADE,
        target_version INTEGER NOT NULL, indexed_version INTEGER,
        indexed_fingerprint TEXT,
        status TEXT NOT NULL CHECK(status IN ('PENDING','COMPLETE','FAILED','UNSUPPORTED')),
        indexed_plies INTEGER, indexed_at TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0, error_summary TEXT,
        updated_at TEXT NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS idx_game_position_index_work
        ON game_position_index(status, target_version, game_id)""",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def check_version(conn):
    row = conn.execute("SELECT schema_version, identity_version FROM position_index_meta WHERE singleton=1").fetchone()
    if row is None or tuple(row) != (SCHEMA_VERSION, IDENTITY_VERSION):
        raise ValueError("Unsupported position index schema/identity version")


def initialize(conn):
    """Serialize first-time DDL; steady-state opens are read-only checks.

    Called after the legacy GameLibrary schema transaction has finished. DDL is
    deliberately executed statement-by-statement, not through executescript.
    """
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='position_index_meta'").fetchone():
        check_version(conn)
        return
    try:
        conn.execute("BEGIN IMMEDIATE")
        for statement in DDL:
            conn.execute(statement)
        conn.execute("INSERT OR IGNORE INTO position_index_meta VALUES (1,?,?,?)",
                     (SCHEMA_VERSION, IDENTITY_VERSION, utc_now()))
        check_version(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def schedule(conn, game_id):
    """Participate in the caller's game INSERT transaction; never commit here."""
    conn.execute("""INSERT INTO game_position_index
        (game_id,target_version,status,updated_at) VALUES (?,?,'PENDING',?)""",
                 (game_id, IDENTITY_VERSION, utc_now()))
