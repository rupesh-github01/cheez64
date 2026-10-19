"""Bounded position work commands. Requires an explicit non-production database."""
import argparse
import json
import math
from pathlib import Path
import time

from .library import GameLibrary
from .positions import PositionIndex, PositionQueries

PROTECTED_DB = Path(__file__).resolve().parents[1] / "data/library.db"


def safe_database(path):
    candidate = Path(path).expanduser().resolve()
    if candidate == PROTECTED_DB.resolve() or (candidate.exists() and PROTECTED_DB.exists() and candidate.samefile(PROTECTED_DB)):
        raise ValueError("The existing data/library.db is protected; use a separate database")
    return str(candidate)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["backfill", "run", "retry", "reindex", "status"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--game-id")
    parser.add_argument("--max-games", type=int, default=100)
    parser.add_argument("--max-seconds", type=float, default=30)
    parser.add_argument("--batch-size", type=int, default=100)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Process at most one pending game")
    mode.add_argument("--follow", action="store_true", help="Poll until the time/game budget is reached")
    args = parser.parse_args(argv)
    if args.max_games < 1 or not math.isfinite(args.max_seconds) or args.max_seconds <= 0 or not 1 <= args.batch_size <= 1000:
        parser.error("Positive game/time budgets and batch-size 1..1000 are required")
    if args.command in ("retry", "reindex") and not args.game_id:
        parser.error("retry/reindex require --game-id")
    try:
        path = safe_database(args.db)
    except ValueError as exc:
        parser.error(str(exc))
    started = time.monotonic()
    outcomes = {}
    reconciled = processed = 0
    with GameLibrary(path) as lib:
        index = PositionIndex(lib._get_connection())
        if args.command in ("retry", "reindex"):
            if not index.request_reindex(args.game_id, retry_only=args.command == "retry"):
                parser.error("Unknown game ID")
        elif args.command in ("run", "backfill"):
            while time.monotonic() - started < args.max_seconds and processed < args.max_games:
                game_id = index.next_pending()
                # Drain a bounded batch before rescanning historical games.
                # No persisted cursor can hide work inserted behind it.
                if args.command == "backfill" and game_id is None:
                    reconciled += index.reconcile(min(args.batch_size, args.max_games-processed))
                    game_id = index.next_pending()
                if game_id:
                    result = index.index_game(game_id)
                    outcomes[result] = outcomes.get(result, 0) + 1
                    processed += 1
                    if args.once:
                        break
                elif args.follow:
                    time.sleep(min(0.25, max(0, args.max_seconds-(time.monotonic()-started))))
                else:
                    break
        result = {"processed": processed, "reconciled": reconciled, "outcomes": outcomes,
                  "elapsed_seconds": time.monotonic()-started,
                  "coverage": PositionQueries(lib._get_connection()).coverage()}
    print(json.dumps(result, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
