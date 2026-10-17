"""
Command-line interface for the Analysis Job Queue.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional, List

# Ensure repo root is on sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import GameLibrary, DEFAULT_DB_PATH
from src.analysis_queue import (
    AnalysisQueue,
    AnalysisConfig,
    JOB_PENDING,
    JOB_RUNNING,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_CANCELLED,
    QueueError,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.queue_cli",
        description="Manage the Stockfish Game Analysis Queue"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database file"
    )

    subparsers = parser.add_subparsers(dest="command", help="Queue command to run")

    # status
    status_parser = subparsers.add_parser("status", help="View queue summary status counts")
    status_parser.add_argument("--batch-id", default=None, help="Filter counts by batch ID")

    # enqueue-all
    enqueue_all_parser = subparsers.add_parser("enqueue-all", help="Queue all unanalyzed games")
    enqueue_all_parser.add_argument("--priority", type=int, default=0, help="Priority (higher = sooner)")
    enqueue_all_parser.add_argument("--depth", type=int, default=16, help="Engine search depth")
    enqueue_all_parser.add_argument("--multipv", type=int, default=3, help="MultiPV candidate moves")
    enqueue_all_parser.add_argument("--batch-id", default=None, help="Optional batch ID tag")

    # enqueue
    enqueue_parser = subparsers.add_parser("enqueue", help="Queue one or more specific games")
    enqueue_parser.add_argument("game_ids", nargs="+", help="Game ID(s) to enqueue")
    enqueue_parser.add_argument("--priority", type=int, default=0, help="Priority (higher = sooner)")
    enqueue_parser.add_argument("--depth", type=int, default=16, help="Engine search depth")
    enqueue_parser.add_argument("--multipv", type=int, default=3, help="MultiPV candidate moves")
    enqueue_parser.add_argument("--force", action="store_true", help="Force reanalysis of already analyzed games")
    enqueue_parser.add_argument("--batch-id", default=None, help="Optional batch ID tag")

    # list
    list_parser = subparsers.add_parser("list", help="List analysis jobs")
    list_parser.add_argument("--status", choices=["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"], default=None)
    list_parser.add_argument("--batch-id", default=None)
    list_parser.add_argument("--game-id", default=None)
    list_parser.add_argument("--limit", type=int, default=20)
    list_parser.add_argument("--offset", type=int, default=0)

    # cancel
    cancel_parser = subparsers.add_parser("cancel", help="Cancel a pending job")
    cancel_parser.add_argument("job_id", help="Job ID to cancel")

    # retry
    retry_parser = subparsers.add_parser("retry", help="Retry a failed or cancelled job")
    retry_parser.add_argument("job_id", help="Job ID to retry")

    # retry-all
    retry_all_parser = subparsers.add_parser("retry-all", help="Retry all failed jobs")
    retry_all_parser.add_argument("--batch-id", default=None, help="Filter by batch ID")

    # recover
    recover_parser = subparsers.add_parser("recover", help="Recover expired job leases")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    with GameLibrary(args.db) as lib:
        queue = AnalysisQueue(lib)

        try:
            if args.command == "status":
                summary = queue.get_status_summary(batch_id=args.batch_id)
                print("Analysis Queue Status:")
                print(f"  Total jobs:      {summary['total']}")
                print(f"  PENDING:         {summary[JOB_PENDING]}")
                print(f"  RUNNING:         {summary[JOB_RUNNING]}")
                print(f"  COMPLETED:       {summary[JOB_COMPLETED]}")
                print(f"  FAILED:          {summary[JOB_FAILED]}")
                print(f"  CANCELLED:       {summary[JOB_CANCELLED]}")

            elif args.command == "enqueue-all":
                cfg = AnalysisConfig(depth=args.depth, multipv=args.multipv)
                result = queue.enqueue_all_unanalyzed(
                    config=cfg,
                    priority=args.priority,
                    batch_id=args.batch_id,
                )
                print(f"Batch {result['batch_id']}:")
                print(f"  Enqueued:                 {result['enqueued']}")
                print(f"  Skipped (already active): {result['skipped_active_job']}")
                print(f"  Skipped (already done):   {result['skipped_already_analyzed']}")

            elif args.command == "enqueue":
                cfg = AnalysisConfig(depth=args.depth, multipv=args.multipv)
                result = queue.enqueue_batch(
                    game_ids=args.game_ids,
                    config=cfg,
                    priority=args.priority,
                    batch_id=args.batch_id,
                    force_reanalyze=args.force,
                )
                print(f"Batch {result['batch_id']}:")
                print(f"  Enqueued:                 {result['enqueued']}")
                print(f"  Skipped (already active): {result['skipped_active_job']}")
                print(f"  Skipped (already done):   {result['skipped_already_analyzed']}")

            elif args.command == "list":
                jobs = queue.list_jobs(
                    status=args.status,
                    batch_id=args.batch_id,
                    game_id=args.game_id,
                    limit=args.limit,
                    offset=args.offset,
                )
                if not jobs:
                    print("No jobs found matching query.")
                    return

                print(f"{'Job ID':<18} {'Game ID':<20} {'Status':<10} {'Prio':<5} {'Attempts':<9} {'Created At'}")
                print("-" * 80)
                for j in jobs:
                    print(f"{j['job_id']:<18} {j['game_id']:<20} {j['status']:<10} {j['priority']:<5} {j['attempt_count']:<9} {j['created_at'][:19]}")

            elif args.command == "cancel":
                queue.cancel_job(args.job_id)
                print(f"Cancelled job '{args.job_id}'.")

            elif args.command == "retry":
                queue.retry_job(args.job_id)
                print(f"Reset job '{args.job_id}' to PENDING.")

            elif args.command == "retry-all":
                count = queue.retry_all_failed(batch_id=args.batch_id)
                print(f"Reset {count} failed jobs to PENDING.")

            elif args.command == "recover":
                res = queue.recover_expired_leases()
                print(f"Recovered {res['requeued']} expired jobs to PENDING; marked {res['failed']} jobs FAILED.")

        except QueueError as e:
            print(f"Queue Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
