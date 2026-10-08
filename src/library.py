"""
Game Library & Analysis Management Module.

Provides a canonical Game representation, deterministic duplicate detection via
game fingerprinting, separation of game records from analysis data, explicit
analysis lifecycle management, and a queryable persistent SQLite library.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import hashlib
import io
import json
import re
import sys

# Ensure repository root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import chess
import chess.pgn

# Analysis status constants
ANALYSIS_NOT_ANALYZED = "NOT_ANALYZED"
ANALYSIS_ANALYZING = "ANALYZING"
ANALYSIS_ANALYZED = "ANALYZED"
ANALYSIS_FAILED = "FAILED"

VALID_ANALYSIS_STATUSES = {
    ANALYSIS_NOT_ANALYZED,
    ANALYSIS_ANALYZING,
    ANALYSIS_ANALYZED,
    ANALYSIS_FAILED,
}

DEFAULT_DB_PATH = "data/library.db"


@dataclass
class Game:
    """Canonical representation of a chess game."""
    game_id: str
    fingerprint: str
    source: str = "local"  # "local", "chess.com", "lichess", "upload"
    source_game_id: Optional[str] = None
    white: str = ""
    black: str = ""
    result: str = "*"
    date: Optional[str] = None
    event: Optional[str] = None
    site: Optional[str] = None
    round: Optional[str] = None
    time_control: Optional[str] = None
    white_rating: Optional[int] = None
    black_rating: Optional[int] = None
    eco: Optional[str] = None
    ply_count: int = 0
    pgn: str = ""
    analysis_status: str = ANALYSIS_NOT_ANALYZED
    analysis_reference: Optional[str] = None
    imported_at: str = ""
    analyzed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert game record to dictionary."""
        return asdict(self)


@dataclass
class GameAnalysisRecord:
    """Attached analysis data for a game."""
    game_id: str
    schema_version: str
    analyzed_at: str
    engine_depth: Optional[int] = None
    critical_count: int = 0
    episodes_count: int = 0
    data: Dict[str, Any] = field(default_factory=dict)
    file_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_game_fingerprint(pgn_text: str) -> str:
    """
    Compute a deterministic SHA-256 fingerprint for a chess game.

    Normalizes:
    - Mainline UCI move sequence (parsed via python-chess, ignoring comments,
      clock annotations, NAG glyphs, and formatting variations)
    - Starting board FEN (standard starting FEN or custom setup FEN)
    - Core metadata: white player, black player, date (lowercased, trimmed).
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        # Fallback for unparseable raw text
        cleaned = re.sub(r"\s+", " ", pgn_text.strip().lower())
        return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()

    headers = game.headers
    white = headers.get("White", "").strip().lower()
    black = headers.get("Black", "").strip().lower()
    date = headers.get("Date", "").strip()

    board = game.board()
    initial_fen = board.fen()
    moves_uci = [m.uci() for m in game.mainline_moves()]

    canonical_repr = f"{white}|{black}|{date}|{initial_fen}|{','.join(moves_uci)}"
    return hashlib.sha256(canonical_repr.encode("utf-8")).hexdigest()


def extract_game_metadata(pgn_text: str) -> Dict[str, Any]:
    """
    Extract structured metadata from PGN text.
    """
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None:
        return {
            "white": "Unknown",
            "black": "Unknown",
            "result": "*",
            "ply_count": 0,
            "pgn": pgn_text,
        }

    headers = game.headers
    white = headers.get("White", "Unknown").strip()
    black = headers.get("Black", "Unknown").strip()
    result = headers.get("Result", "*").strip()
    date = headers.get("Date", "").strip() or None
    event = headers.get("Event", "").strip() or None
    site = headers.get("Site", "").strip() or None
    round_val = headers.get("Round", "").strip() or None
    time_control = headers.get("TimeControl", "").strip() or None
    eco = headers.get("ECO", "").strip() or None

    def parse_int_rating(val: Optional[str]) -> Optional[int]:
        if not val:
            return None
        match = re.search(r"\d+", str(val))
        return int(match.group(0)) if match else None

    white_rating = parse_int_rating(headers.get("WhiteElo"))
    black_rating = parse_int_rating(headers.get("BlackElo"))

    # Determine source and source_game_id from Link or Site
    source = "local"
    source_game_id = None
    link = headers.get("Link", "") or ""
    combined_url = f"{link} {site or ''}".strip()

    if "chess.com" in combined_url.lower():
        source = "chess.com"
        match = re.search(r"/(?:live|daily)/(\d+)", combined_url)
        if match:
            source_game_id = match.group(1)
    elif "lichess" in combined_url.lower():
        source = "lichess"
        match = re.search(r"lichess\.org/([a-zA-Z0-9]+)", combined_url)
        if match:
            source_game_id = match.group(1)

    ply_count = sum(1 for _ in game.mainline_moves())

    return {
        "white": white,
        "black": black,
        "result": result,
        "date": date,
        "event": event,
        "site": site,
        "round": round_val,
        "time_control": time_control,
        "white_rating": white_rating,
        "black_rating": black_rating,
        "eco": eco,
        "source": source,
        "source_game_id": source_game_id,
        "ply_count": ply_count,
        "pgn": pgn_text.strip(),
    }


class GameLibrary:
    """
    SQLite-backed local game library and analysis repository.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path if db_path is not None else DEFAULT_DB_PATH
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        return self._conn

    def close(self) -> None:
        if hasattr(self, "_conn") and self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    fingerprint TEXT UNIQUE NOT NULL,
                    source TEXT NOT NULL DEFAULT 'local',
                    source_game_id TEXT,
                    white TEXT NOT NULL,
                    black TEXT NOT NULL,
                    result TEXT NOT NULL,
                    date TEXT,
                    event TEXT,
                    site TEXT,
                    round TEXT,
                    time_control TEXT,
                    white_rating INTEGER,
                    black_rating INTEGER,
                    eco TEXT,
                    ply_count INTEGER DEFAULT 0,
                    pgn TEXT NOT NULL,
                    analysis_status TEXT NOT NULL DEFAULT 'NOT_ANALYZED',
                    analysis_reference TEXT,
                    imported_at TEXT NOT NULL,
                    analyzed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_games_fingerprint ON games(fingerprint);
                CREATE INDEX IF NOT EXISTS idx_games_white ON games(white);
                CREATE INDEX IF NOT EXISTS idx_games_black ON games(black);
                CREATE INDEX IF NOT EXISTS idx_games_date ON games(date);
                CREATE INDEX IF NOT EXISTS idx_games_status ON games(analysis_status);

                CREATE TABLE IF NOT EXISTS analyses (
                    game_id TEXT PRIMARY KEY REFERENCES games(game_id) ON DELETE CASCADE,
                    schema_version TEXT,
                    analyzed_at TEXT NOT NULL,
                    engine_depth INTEGER,
                    critical_count INTEGER DEFAULT 0,
                    episodes_count INTEGER DEFAULT 0,
                    analysis_data TEXT NOT NULL,
                    file_path TEXT
                );

                CREATE TABLE IF NOT EXISTS game_openings (
                    game_id TEXT PRIMARY KEY REFERENCES games(game_id) ON DELETE CASCADE,
                    opening_name TEXT NOT NULL,
                    variation_name TEXT,
                    eco TEXT NOT NULL,
                    full_name TEXT NOT NULL,
                    matched_ply INTEGER NOT NULL,
                    theory_exit_ply INTEGER,
                    theory_exit_move TEXT,
                    divergence_side TEXT,
                    is_transposition INTEGER DEFAULT 0,
                    confidence TEXT NOT NULL,
                    opening_data TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_openings_eco ON game_openings(eco);
                CREATE INDEX IF NOT EXISTS idx_openings_name ON game_openings(opening_name);
            """)

    def _row_to_game(self, row: sqlite3.Row) -> Game:
        return Game(
            game_id=row["game_id"],
            fingerprint=row["fingerprint"],
            source=row["source"],
            source_game_id=row["source_game_id"],
            white=row["white"],
            black=row["black"],
            result=row["result"],
            date=row["date"],
            event=row["event"],
            site=row["site"],
            round=row["round"],
            time_control=row["time_control"],
            white_rating=row["white_rating"],
            black_rating=row["black_rating"],
            eco=row["eco"],
            ply_count=row["ply_count"],
            pgn=row["pgn"],
            analysis_status=row["analysis_status"],
            analysis_reference=row["analysis_reference"],
            imported_at=row["imported_at"],
            analyzed_at=row["analyzed_at"],
        )

    def add_game(
        self,
        pgn_text: str,
        source: Optional[str] = None,
        source_game_id: Optional[str] = None,
        allow_duplicate: bool = False,
    ) -> Tuple[Game, bool]:
        """
        Add a game to the library.

        Returns (Game, True) if newly inserted, or (ExistingGame, False) if duplicate.
        """
        fingerprint = compute_game_fingerprint(pgn_text)
        existing = self.get_game_by_fingerprint(fingerprint)
        if existing and not allow_duplicate:
            return existing, False

        meta = extract_game_metadata(pgn_text)
        game_id = f"game_{fingerprint[:16]}"
        now_iso = datetime.now(timezone.utc).isoformat()

        src = source or meta.get("source", "local")
        src_id = source_game_id or meta.get("source_game_id")

        game = Game(
            game_id=game_id,
            fingerprint=fingerprint,
            source=src,
            source_game_id=src_id,
            white=meta["white"],
            black=meta["black"],
            result=meta["result"],
            date=meta["date"],
            event=meta["event"],
            site=meta["site"],
            round=meta["round"],
            time_control=meta["time_control"],
            white_rating=meta["white_rating"],
            black_rating=meta["black_rating"],
            eco=meta["eco"],
            ply_count=meta["ply_count"],
            pgn=meta["pgn"],
            analysis_status=ANALYSIS_NOT_ANALYZED,
            analysis_reference=None,
            imported_at=now_iso,
            analyzed_at=None,
        )

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO games (
                    game_id, fingerprint, source, source_game_id,
                    white, black, result, date, event, site, round,
                    time_control, white_rating, black_rating, eco,
                    ply_count, pgn, analysis_status, analysis_reference,
                    imported_at, analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game.game_id, game.fingerprint, game.source, game.source_game_id,
                game.white, game.black, game.result, game.date, game.event, game.site, game.round,
                game.time_control, game.white_rating, game.black_rating, game.eco,
                game.ply_count, game.pgn, game.analysis_status, game.analysis_reference,
                game.imported_at, game.analyzed_at
            ))

        return game, True

    def get_game(self, game_id: str) -> Optional[Game]:
        """Retrieve a game by its game_id."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM games WHERE game_id = ?", (game_id,))
            row = cursor.fetchone()
            return self._row_to_game(row) if row else None

    def get_game_by_fingerprint(self, fingerprint: str) -> Optional[Game]:
        """Retrieve a game by its deterministic fingerprint."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM games WHERE fingerprint = ?", (fingerprint,))
            row = cursor.fetchone()
            return self._row_to_game(row) if row else None

    def find_duplicates(self, pgn_text: str) -> Optional[Game]:
        """Check if an identical game already exists."""
        fingerprint = compute_game_fingerprint(pgn_text)
        return self.get_game_by_fingerprint(fingerprint)

    def list_games(
        self,
        player: Optional[str] = None,
        color: Optional[str] = None,
        result: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        source: Optional[str] = None,
        analysis_status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Game]:
        """
        Query and filter games in the library.
        """
        query = "SELECT * FROM games WHERE 1=1"
        params: List[Any] = []

        if player:
            if color and color.lower() == "white":
                query += " AND LOWER(white) LIKE ?"
                params.append(f"%{player.lower()}%")
            elif color and color.lower() == "black":
                query += " AND LOWER(black) LIKE ?"
                params.append(f"%{player.lower()}%")
            else:
                query += " AND (LOWER(white) LIKE ? OR LOWER(black) LIKE ?)"
                params.extend([f"%{player.lower()}%", f"%{player.lower()}%"])

        if result:
            query += " AND result = ?"
            params.append(result)

        if date_from:
            query += " AND date >= ?"
            params.append(date_from)

        if date_to:
            query += " AND date <= ?"
            params.append(date_to)

        if source:
            query += " AND source = ?"
            params.append(source)

        if analysis_status:
            query += " AND analysis_status = ?"
            params.append(analysis_status)

        query += " ORDER BY date DESC, imported_at DESC"

        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
            if offset > 0:
                query += " OFFSET ?"
                params.append(offset)

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_game(row) for row in cursor.fetchall()]

    def count_games(self) -> int:
        """Return total number of games stored."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM games")
            return cursor.fetchone()[0]

    def delete_game(self, game_id: str) -> bool:
        """Delete a game and its attached analysis."""
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
            return cursor.rowcount > 0

    def update_game_metadata(self, game_id: str, **kwargs) -> Optional[Game]:
        """Update mutable metadata fields for a game."""
        allowed_fields = {
            "event", "site", "round", "time_control",
            "white_rating", "black_rating", "source", "source_game_id"
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return self.get_game(game_id)

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
        params = list(updates.values()) + [game_id]

        with self._get_connection() as conn:
            conn.execute(f"UPDATE games SET {set_clause} WHERE game_id = ?", params)

        return self.get_game(game_id)

    def set_analysis_status(
        self,
        game_id: str,
        status: str,
        analyzed_at: Optional[str] = None
    ) -> bool:
        """
        Transition analysis status: NOT_ANALYZED, ANALYZING, ANALYZED, FAILED.
        """
        if status not in VALID_ANALYSIS_STATUSES:
            raise ValueError(f"Invalid analysis status: {status}. Must be one of {VALID_ANALYSIS_STATUSES}")

        now_iso = analyzed_at or (datetime.now(timezone.utc).isoformat() if status == ANALYSIS_ANALYZED else None)

        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE games
                SET analysis_status = ?, analyzed_at = COALESCE(?, analyzed_at)
                WHERE game_id = ?
            """, (status, now_iso, game_id))
            return cursor.rowcount > 0

    def attach_analysis(
        self,
        game_id: str,
        analysis_data: Dict[str, Any],
        file_path: Optional[str] = None,
        schema_version: Optional[str] = None,
        engine_depth: Optional[int] = None,
        analyzed_at: Optional[str] = None,
    ) -> bool:
        """
        Attach structured analysis data to a game record.
        """
        game = self.get_game(game_id)
        if not game:
            return False

        version = schema_version or analysis_data.get("schema_version", "1.1.0")
        analyzed_time = analyzed_at or datetime.now(timezone.utc).isoformat()

        # Extract counts from summary or moves
        summary = analysis_data.get("summary", {})
        crit_count = summary.get("critical_positions_count")
        if crit_count is None:
            crit_count = sum(1 for m in analysis_data.get("moves", []) if m.get("is_critical"))

        ep_count = summary.get("critical_episodes_count")
        if ep_count is None:
            ep_count = len(analysis_data.get("episodes", []))

        json_str = json.dumps(analysis_data)
        ref_path = file_path or f"data/analyses/{game_id}.json"

        with self._get_connection() as conn:
            conn.execute("""
                INSERT INTO analyses (
                    game_id, schema_version, analyzed_at, engine_depth,
                    critical_count, episodes_count, analysis_data, file_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    analyzed_at = excluded.analyzed_at,
                    engine_depth = excluded.engine_depth,
                    critical_count = excluded.critical_count,
                    episodes_count = excluded.episodes_count,
                    analysis_data = excluded.analysis_data,
                    file_path = excluded.file_path
            """, (
                game_id, version, analyzed_time, engine_depth,
                crit_count, ep_count, json_str, file_path
            ))

            conn.execute("""
                UPDATE games
                SET analysis_status = ?,
                    analysis_reference = ?,
                    analyzed_at = ?
                WHERE game_id = ?
            """, (ANALYSIS_ANALYZED, ref_path, analyzed_time, game_id))

        return True

    def get_analysis(self, game_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve structured analysis data for a game.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM analyses WHERE game_id = ?", (game_id,))
            row = cursor.fetchone()
            if not row:
                return None

            data_str = row["analysis_data"]
            if data_str:
                try:
                    return json.loads(data_str)
                except json.JSONDecodeError:
                    pass

            # Fallback to file_path if stored externally
            fp = row["file_path"]
            if fp and Path(fp).exists():
                with open(fp, "r", encoding="utf-8") as f:
                    return json.load(f)

            return None

    def get_analysis_record(self, game_id: str) -> Optional[GameAnalysisRecord]:
        """Retrieve metadata record of attached analysis."""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM analyses WHERE game_id = ?", (game_id,))
            row = cursor.fetchone()
            if not row:
                return None

            analysis_dict = {}
            if row["analysis_data"]:
                try:
                    analysis_dict = json.loads(row["analysis_data"])
                except json.JSONDecodeError:
                    pass

            return GameAnalysisRecord(
                game_id=row["game_id"],
                schema_version=row["schema_version"],
                analyzed_at=row["analyzed_at"],
                engine_depth=row["engine_depth"],
                critical_count=row["critical_count"],
                episodes_count=row["episodes_count"],
                data=analysis_dict,
                file_path=row["file_path"],
            )

    def remove_analysis(self, game_id: str) -> bool:
        """
        Remove analysis data from game, reverting status to NOT_ANALYZED.
        """
        with self._get_connection() as conn:
            conn.execute("DELETE FROM analyses WHERE game_id = ?", (game_id,))
            cursor = conn.execute("""
                UPDATE games
                SET analysis_status = ?,
                    analysis_reference = NULL,
                    analyzed_at = NULL
                WHERE game_id = ?
            """, (ANALYSIS_NOT_ANALYZED, game_id))
            return cursor.rowcount > 0

    def set_game_opening(self, game_id: str, opening_data: Any) -> bool:
        """
        Store opening analysis for a game.
        """
        if hasattr(opening_data, "to_dict"):
            d = opening_data.to_dict()
        elif isinstance(opening_data, dict):
            d = opening_data
        else:
            raise ValueError("opening_data must be OpeningAnalysis or dict")

        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO game_openings (
                    game_id, opening_name, variation_name, eco, full_name,
                    matched_ply, theory_exit_ply, theory_exit_move, divergence_side,
                    is_transposition, confidence, opening_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_id,
                d["opening_name"],
                d.get("variation_name"),
                d["eco"],
                d.get("full_name", d["opening_name"]),
                d.get("matched_ply", 0),
                d.get("theory_exit_ply"),
                d.get("theory_exit_move"),
                d.get("divergence_side"),
                1 if d.get("is_transposition") else 0,
                d.get("confidence", "moderate"),
                json.dumps(d),
            ))
            return True

    def get_game_opening(self, game_id: str, auto_detect: bool = False) -> Optional[Dict[str, Any]]:
        """
        Retrieve opening analysis for a game, optionally computing on demand if missing.
        """
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT opening_data FROM game_openings WHERE game_id = ?", (game_id,))
            row = cursor.fetchone()
            if row and row["opening_data"]:
                try:
                    return json.loads(row["opening_data"])
                except json.JSONDecodeError:
                    pass

        if auto_detect:
            game = self.get_game(game_id)
            if game:
                from src.openings import detect_opening
                analysis = detect_opening(game.pgn)
                self.set_game_opening(game_id, analysis)
                return analysis.to_dict()

        return None

    def detect_all_openings(self, overwrite: bool = False) -> int:
        """
        Detect and store opening classifications for all games in the library.
        """
        from src.openings import detect_opening
        games = self.list_games()
        count = 0
        for g in games:
            if not overwrite and self.get_game_opening(g.game_id, auto_detect=False):
                continue
            analysis = detect_opening(g.pgn)
            self.set_game_opening(g.game_id, analysis)
            count += 1
        return count


def import_existing_games(
    library: GameLibrary,
    games_dir: str = "data/games"
) -> Dict[str, Any]:
    """
    Idempotent migration process to import existing PGNs and attach analysis JSONs.
    """
    dir_path = Path(games_dir)
    if not dir_path.exists():
        return {
            "imported_games": 0,
            "duplicates_skipped": 0,
            "analyses_attached": 0,
            "errors": [f"Directory {games_dir} does not exist"]
        }

    imported_count = 0
    duplicates_skipped = 0
    analyses_attached = 0
    errors = []

    # Find all PGN files
    pgn_files = sorted(dir_path.glob("*.pgn"))

    for pgn_file in pgn_files:
        try:
            with open(pgn_file, "r", encoding="utf-8") as f:
                pgn_content = f.read()

            game, is_new = library.add_game(pgn_content)
            if is_new:
                imported_count += 1
            else:
                duplicates_skipped += 1

            # Check if matching analysis JSON exists
            analysis_json_path = pgn_file.with_name(f"{pgn_file.stem}_analysis.json")
            if analysis_json_path.exists():
                with open(analysis_json_path, "r", encoding="utf-8") as f:
                    analysis_data = json.load(f)

                success = library.attach_analysis(
                    game_id=game.game_id,
                    analysis_data=analysis_data,
                    file_path=str(analysis_json_path),
                    schema_version=analysis_data.get("schema_version", "1.1.0"),
                )
                if success:
                    analyses_attached += 1

        except Exception as e:
            errors.append(f"Error processing {pgn_file.name}: {str(e)}")

    return {
        "imported_games": imported_count,
        "duplicates_skipped": duplicates_skipped,
        "analyses_attached": analyses_attached,
        "errors": errors,
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Game Library & Analysis Management CLI")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Import / Migrate
    import_parser = subparsers.add_parser("import", help="Import games from a directory")
    import_parser.add_argument("--dir", default="data/games", help="Directory containing PGNs and JSONs")
    import_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")

    # List
    list_parser = subparsers.add_parser("list", help="List games in the library")
    list_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")
    list_parser.add_argument("--player", default=None, help="Filter by player")
    list_parser.add_argument("--status", default=None, help="Filter by analysis status")

    # Summary
    summary_parser = subparsers.add_parser("summary", help="Show library summary statistics")
    summary_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")

    # Import Remote (Chess.com or Lichess)
    remote_parser = subparsers.add_parser("import-remote", help="Import games from Chess.com or Lichess")
    remote_parser.add_argument("platform", choices=["chess.com", "chesscom", "lichess"], help="Target platform")
    remote_parser.add_argument("username", help="Platform username")
    remote_parser.add_argument("--from", dest="since", default=None, help="Start date (YYYY-MM or YYYY-MM-DD)")
    remote_parser.add_argument("--to", dest="until", default=None, help="End date (YYYY-MM or YYYY-MM-DD)")
    remote_parser.add_argument("--max", dest="max_games", type=int, default=None, help="Max games to import")
    remote_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")

    # Openings detection
    openings_parser = subparsers.add_parser("openings-detect", help="Detect and store openings for all games")
    openings_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")
    openings_parser.add_argument("--overwrite", action="store_true", help="Overwrite existing detected openings")

    # Repertoire profile
    rep_parser = subparsers.add_parser("repertoire", help="Build player opening repertoire profile")
    rep_parser.add_argument("player", help="Target player name")
    rep_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")

    # Coaching recommendations
    coach_parser = subparsers.add_parser("coaching", help="Generate personalized coaching recommendations")
    coach_parser.add_argument("player", help="Target player name")
    coach_parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to SQLite database")

    args = parser.parse_args()

    if args.command == "import":
        with GameLibrary(args.db) as lib:
            result = import_existing_games(lib, args.dir)
            print(f"Migration completed:")
            print(f"  Imported games:     {result['imported_games']}")
            print(f"  Duplicates skipped: {result['duplicates_skipped']}")
            print(f"  Analyses attached:  {result['analyses_attached']}")
            if result["errors"]:
                print(f"  Errors ({len(result['errors'])}):")
                for err in result["errors"]:
                    print(f"    - {err}")
    elif args.command == "import-remote":
        from src.importers import import_platform_games
        with GameLibrary(args.db) as lib:
            result = import_platform_games(
                source=args.platform,
                username=args.username,
                library=lib,
                since=args.since,
                until=args.until,
                max_games=args.max_games,
            )
            print(result.summary())
    elif args.command == "openings-detect":
        with GameLibrary(args.db) as lib:
            count = lib.detect_all_openings(overwrite=args.overwrite)
            print(f"Opening detection complete: {count} games updated in library ({args.db}).")
    elif args.command == "repertoire":
        from src.openings import build_player_repertoire_from_library
        with GameLibrary(args.db) as lib:
            profile = build_player_repertoire_from_library(lib, args.player)
            print(profile.summary())
    elif args.command == "coaching":
        from src.recommendations import build_coaching_profile_from_library
        with GameLibrary(args.db) as lib:
            profile = build_coaching_profile_from_library(lib, args.player)
            print(profile.summary())
    elif args.command == "list":
        with GameLibrary(args.db) as lib:
            games = lib.list_games(player=args.player, analysis_status=args.status)
            print(f"Found {len(games)} games:")
            for g in games:
                analysis_info = f"[{g.analysis_status}]"
                op_info = lib.get_game_opening(g.game_id)
                op_str = f" | {op_info['eco']} {op_info['opening_name']}" if op_info else ""
                print(f"  {g.game_id[:8]}.. | {g.date or 'Unknown'} | {g.white} vs {g.black} ({g.result}){op_str} | {analysis_info}")
    elif args.command == "summary":
        with GameLibrary(args.db) as lib:
            total = lib.count_games()
            analyzed = len(lib.list_games(analysis_status=ANALYSIS_ANALYZED))
            not_analyzed = len(lib.list_games(analysis_status=ANALYSIS_NOT_ANALYZED))
            print(f"Game Library Summary ({args.db}):")
            print(f"  Total games:       {total}")
            print(f"  Analyzed:          {analyzed}")
            print(f"  Not analyzed:      {not_analyzed}")
    else:
        # Default: run migration on data/games and print summary
        with GameLibrary(DEFAULT_DB_PATH) as lib:
            res = import_existing_games(lib, "data/games")
            total = lib.count_games()
            analyzed = len(lib.list_games(analysis_status=ANALYSIS_ANALYZED))
            print(f"Default migration on data/games completed:")
            print(f"  Total games in library: {total} (Analyzed: {analyzed})")
            print(f"  Imported: {res['imported_games']}, Skipped: {res['duplicates_skipped']}, Attached: {res['analyses_attached']}")


if __name__ == "__main__":
    main()
