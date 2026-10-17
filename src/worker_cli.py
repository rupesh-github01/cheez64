"""
Command-line interface for the Background Stockfish Analysis Worker.
"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure repo root is on sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import DEFAULT_DB_PATH
from src.analysis_worker import AnalysisWorker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.worker_cli",
        description="Background Stockfish Analysis Worker CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Worker command")

    run_parser = subparsers.add_parser("run", help="Start the analysis worker")
    run_parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to SQLite library database"
    )
    run_parser.add_argument(
        "--stockfish-path",
        default=None,
        help="Path to Stockfish executable"
    )
    run_parser.add_argument(
        "--worker-id",
        default=None,
        help="Custom worker identifier"
    )
    run_parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
        help="Seconds to wait when queue is empty (default: 3.0)"
    )
    run_parser.add_argument(
        "--lease-seconds",
        type=int,
        default=300,
        help="Claim lease duration in seconds (default: 300)"
    )
    run_parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=60.0,
        help="Seconds between lease heartbeats (default: 60.0)"
    )
    run_parser.add_argument(
        "--grace-period",
        type=float,
        default=15.0,
        help="Graceful shutdown timeout in seconds (default: 15.0)"
    )
    run_parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Maximum number of jobs to process before exiting"
    )
    run_parser.add_argument(
        "--once",
        action="store_true",
        help="Process available pending jobs until queue is empty, then exit"
    )
    run_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose debug logging"
    )

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    log_level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.command == "run":
        worker = AnalysisWorker(
            db_path=args.db,
            worker_id=args.worker_id,
            stockfish_path=args.stockfish_path,
            poll_interval=args.poll_interval,
            lease_seconds=args.lease_seconds,
            heartbeat_interval=args.heartbeat_interval,
            grace_period_seconds=args.grace_period,
        )

        try:
            worker.run(max_jobs=args.max_jobs, run_once=args.once)
        except KeyboardInterrupt:
            worker.request_shutdown()


if __name__ == "__main__":
    main()
