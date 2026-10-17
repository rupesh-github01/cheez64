"""
Command-line interface for importing games from Chess.com and Lichess.
"""

import argparse
import sys
from pathlib import Path

# Ensure src is on sys.path
repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import GameLibrary, DEFAULT_DB_PATH
from src.importers import import_platform_games, bulk_import_platform_games


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.importers",
        description="Import public games from Chess.com or Lichess into the GameLibrary"
    )
    parser.add_argument(
        "platform",
        choices=["chess.com", "chesscom", "lichess"],
        help="Platform to import from"
    )
    parser.add_argument(
        "username",
        help="Account username on the chosen platform"
    )
    parser.add_argument(
        "--from",
        dest="since",
        default=None,
        help="Start date/month (e.g. 2026-09 or 2026-09-01)"
    )
    parser.add_argument(
        "--to",
        dest="until",
        default=None,
        help="End date/month (e.g. 2026-10 or 2026-10-07)"
    )
    parser.add_argument(
        "--max",
        dest="max_games",
        type=int,
        default=None,
        help="Maximum number of games to import"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database file"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Fetch and validate games without saving to library"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Resume an interrupted bulk import job"
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        default=False,
        help="Use legacy non-batched import instead of bulk import"
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    def on_progress(p: dict):
        status_line = (
            f"  [{p.get('batch_label', 'batch')}] "
            f"Fetched: {p.get('games_fetched', 0)} | "
            f"Imported: {p.get('games_imported', 0)} | "
            f"Dupes: {p.get('duplicates_skipped', 0)} | "
            f"Failed: {p.get('games_failed', 0)}"
        )
        if p.get("is_resume"):
            status_line += " (resumed)"
        print(status_line)

    print(f"Connecting to GameLibrary at {args.db}...")
    with GameLibrary(args.db) as lib:
        if args.dry_run:
            print(f"[DRY RUN] Simulating import from {args.platform} for '{args.username}'...")
        else:
            print(f"Importing games from {args.platform} for '{args.username}'...")

        if args.legacy:
            result = import_platform_games(
                source=args.platform,
                username=args.username,
                library=lib,
                since=args.since,
                until=args.until,
                max_games=args.max_games,
            )
        else:
            result = bulk_import_platform_games(
                source=args.platform,
                username=args.username,
                library=lib,
                since=args.since,
                until=args.until,
                max_games=args.max_games,
                progress_callback=on_progress,
                dry_run=args.dry_run,
            )
        print(result.summary())
        if result.errors:
            sys.exit(1)


if __name__ == "__main__":
    main()

