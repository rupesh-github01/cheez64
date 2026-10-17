"""
Unit tests for Segment 11B: Persistent Stockfish Analysis Queue.

Covers:
- Enqueueing single & batch of games
- Database-enforced idempotency via partial unique index
- Already-analyzed game handling and force_reanalyze preservation
- Game-level analysis_status consistency
- Priority-ordered FIFO claims
- Unique fencing claim tokens per attempt
- Heartbeat lease extension
- Stale worker completion and heartbeat rejection
- Atomic completion with analysis persistence (Segment 11C contract)
- Crash recovery of expired leases & max attempt bounding
- State transition validation & cancellation
- Pagination and status count summaries
- Configuration validation and deterministic hashing
- Idempotent schema migration across process restarts
"""

from datetime import datetime, timezone, timedelta
import io
import json
from pathlib import Path
import sys
import unittest

# Ensure repo root is on sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import (
    GameLibrary,
    ANALYSIS_NOT_ANALYZED,
    ANALYSIS_ANALYZING,
    ANALYSIS_ANALYZED,
    ANALYSIS_FAILED,
)
from src.analysis_queue import (
    AnalysisQueue,
    AnalysisConfig,
    JOB_PENDING,
    JOB_RUNNING,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_CANCELLED,
    QueueError,
    JobNotFoundError,
    InvalidStateTransitionError,
    StaleLeaseError,
    AlreadyAnalyzedError,
    ConfigValidationError,
)


def _make_pgn(tag: str) -> str:
    return f"""[Event "Test Event"]
[Site "Local"]
[Date "2026.10.15"]
[White "Player1_{tag}"]
[Black "Player2_{tag}"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 1-0
"""


class TestAnalysisQueue(unittest.TestCase):
    """Test suite for persistent analysis queue."""

    def setUp(self):
        self.lib = GameLibrary(":memory:")
        self.queue = AnalysisQueue(self.lib)

        # Seed sample games
        self.game1, _ = self.lib.add_game(_make_pgn("1"))
        self.game2, _ = self.lib.add_game(_make_pgn("2"))
        self.game3, _ = self.lib.add_game(_make_pgn("3"))

    def tearDown(self):
        self.lib.close()

    # 1. Enqueue single game
    def test_enqueue_single_game(self):
        job, is_new = self.queue.enqueue(self.game1.game_id, priority=5)
        self.assertTrue(is_new)
        self.assertIsNotNone(job)
        self.assertEqual(job["game_id"], self.game1.game_id)
        self.assertEqual(job["status"], JOB_PENDING)
        self.assertEqual(job["priority"], 5)
        self.assertEqual(job["attempt_count"], 0)

        # Game status remains NOT_ANALYZED when enqueued
        game = self.lib.get_game(self.game1.game_id)
        self.assertEqual(game.analysis_status, ANALYSIS_NOT_ANALYZED)

    # 2. Database-enforced idempotency via partial unique index
    def test_database_enforced_idempotency(self):
        cfg = AnalysisConfig(depth=18)
        job1, is_new1 = self.queue.enqueue(self.game1.game_id, config=cfg)
        self.assertTrue(is_new1)

        # Enqueueing same game with same config returns existing active job
        job2, is_new2 = self.queue.enqueue(self.game1.game_id, config=cfg)
        self.assertFalse(is_new2)
        self.assertEqual(job1["job_id"], job2["job_id"])

        # Total jobs in DB is 1
        summary = self.queue.get_status_summary()
        self.assertEqual(summary["total"], 1)

    # 3. Handling already-analyzed games & force_reanalyze preservation
    def test_already_analyzed_game_behavior(self):
        # Attach existing analysis to game2
        self.lib.attach_analysis(
            self.game2.game_id,
            analysis_data={"summary": {"note": "initial analysis"}},
            engine_depth=14,
        )
        self.assertEqual(self.lib.get_game(self.game2.game_id).analysis_status, ANALYSIS_ANALYZED)

        # Default: raises AlreadyAnalyzedError
        with self.assertRaises(AlreadyAnalyzedError):
            self.queue.enqueue(self.game2.game_id, force_reanalyze=False)

        # With force_reanalyze=True: schedules reanalysis
        job, is_new = self.queue.enqueue(self.game2.game_id, force_reanalyze=True)
        self.assertTrue(is_new)
        self.assertEqual(job["status"], JOB_PENDING)

        # Safeguard #2: Existing analysis data remains accessible and status stays ANALYZED
        game = self.lib.get_game(self.game2.game_id)
        self.assertEqual(game.analysis_status, ANALYSIS_ANALYZED)
        analysis = self.lib.get_analysis(self.game2.game_id)
        self.assertEqual(analysis.get("summary", {}).get("note"), "initial analysis")

        # Claiming the reanalysis job does NOT overwrite game status to ANALYZING
        claimed = self.queue.claim_next_job("worker_1")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["job_id"], job["job_id"])
        self.assertEqual(self.lib.get_game(self.game2.game_id).analysis_status, ANALYSIS_ANALYZED)

        # If the reanalysis job fails, game status still remains ANALYZED and data intact
        self.queue.fail_job(claimed["job_id"], claimed["claim_token"], "Crash in engine", retryable=False)
        self.assertEqual(self.lib.get_game(self.game2.game_id).analysis_status, ANALYSIS_ANALYZED)
        self.assertEqual(self.lib.get_analysis(self.game2.game_id).get("summary", {}).get("note"), "initial analysis")

    # 4. Batch enqueueing
    def test_enqueue_batch(self):
        res = self.queue.enqueue_batch(
            [self.game1.game_id, self.game2.game_id, self.game3.game_id],
            priority=2,
            batch_id="test_batch_1",
        )
        self.assertEqual(res["enqueued"], 3)
        self.assertEqual(res["batch_id"], "test_batch_1")

        # Re-enqueueing same batch skips active jobs
        res2 = self.queue.enqueue_batch(
            [self.game1.game_id, self.game2.game_id],
            priority=2,
            batch_id="test_batch_1",
        )
        self.assertEqual(res2["enqueued"], 0)
        self.assertEqual(res2["skipped_active_job"], 2)

    # 5. Enqueue all unanalyzed games
    def test_enqueue_all_unanalyzed(self):
        # Mark game1 as ANALYZED
        self.lib.set_analysis_status(self.game1.game_id, ANALYSIS_ANALYZED)

        # Should enqueue game2 and game3 only
        res = self.queue.enqueue_all_unanalyzed()
        self.assertEqual(res["enqueued"], 2)

        jobs = self.queue.list_jobs(status=JOB_PENDING)
        enqueued_gids = {j["game_id"] for j in jobs}
        self.assertNotIn(self.game1.game_id, enqueued_gids)
        self.assertIn(self.game2.game_id, enqueued_gids)
        self.assertIn(self.game3.game_id, enqueued_gids)

    # 6. Priority ordering (FIFO within same priority)
    def test_priority_ordering(self):
        # Enqueue with different priorities
        job_low, _ = self.queue.enqueue(self.game1.game_id, priority=0)
        job_high, _ = self.queue.enqueue(self.game2.game_id, priority=10)
        job_med, _ = self.queue.enqueue(self.game3.game_id, priority=5)

        # First claim should be high priority (game2)
        c1 = self.queue.claim_next_job("w1")
        self.assertEqual(c1["job_id"], job_high["job_id"])

        # Second claim should be med priority (game3)
        c2 = self.queue.claim_next_job("w2")
        self.assertEqual(c2["job_id"], job_med["job_id"])

        # Third claim should be low priority (game1)
        c3 = self.queue.claim_next_job("w3")
        self.assertEqual(c3["job_id"], job_low["job_id"])

    # 7. Unique fencing claim token per attempt
    def test_claim_token_fencing(self):
        job, _ = self.queue.enqueue(self.game1.game_id)

        # Worker claims job
        claim1 = self.queue.claim_next_job("worker_A", lease_seconds=10)
        token1 = claim1["claim_token"]
        self.assertTrue(token1.startswith("worker_A:"))

        # Heartbeat with valid token works
        self.queue.heartbeat(job["job_id"], token1, extend_seconds=20)

        # Heartbeat with wrong token fails
        with self.assertRaises(StaleLeaseError):
            self.queue.heartbeat(job["job_id"], "worker_A:fake_token")

        # Simulate job failure and re-claim by same worker
        self.queue.fail_job(job["job_id"], token1, "Temporary failure", retryable=True)

        claim2 = self.queue.claim_next_job("worker_A", lease_seconds=10)
        token2 = claim2["claim_token"]

        # Tokens must be distinct across attempts
        self.assertNotEqual(token1, token2)

        # Old token cannot complete the job
        with self.assertRaises(StaleLeaseError):
            self.queue.complete_job(job["job_id"], token1)

        # New token succeeds
        self.queue.complete_job(job["job_id"], token2)
        fresh = self.queue.get_job(job["job_id"])
        self.assertEqual(fresh["status"], JOB_COMPLETED)

    # 8. Atomic completion operation with analysis persistence (Segment 11C Contract)
    def test_atomic_completion_with_analysis(self):
        job, _ = self.queue.enqueue(self.game1.game_id)
        claim = self.queue.claim_next_job("worker_1")
        token = claim["claim_token"]

        analysis_payload = {
            "summary": {
                "critical_positions_count": 3,
                "critical_episodes_count": 1,
            },
            "moves": [
                {"ply": 1, "is_critical": False},
                {"ply": 2, "is_critical": True},
            ]
        }

        # Atomically complete
        self.queue.complete_job_with_analysis(
            job_id=job["job_id"],
            claim_token=token,
            analysis_data=analysis_payload,
            engine_depth=16,
            schema_version="1.1.0",
        )

        # 1. Job marked completed
        j = self.queue.get_job(job["job_id"])
        self.assertEqual(j["status"], JOB_COMPLETED)
        self.assertIsNotNone(j["completed_at"])

        # 2. Game status set to ANALYZED
        g = self.lib.get_game(self.game1.game_id)
        self.assertEqual(g.analysis_status, ANALYSIS_ANALYZED)
        self.assertIsNotNone(g.analyzed_at)

        # 3. Analysis record stored
        stored = self.lib.get_analysis(self.game1.game_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.get("summary", {}).get("critical_positions_count"), 3)

    # 9. Stale worker completion rejection on expired lease
    def test_expired_lease_completion_rejected(self):
        job, _ = self.queue.enqueue(self.game1.game_id)
        # Claim with negative lease duration so it is immediately expired
        claim = self.queue.claim_next_job("worker_slow", lease_seconds=-5)
        token = claim["claim_token"]

        with self.assertRaises(StaleLeaseError):
            self.queue.complete_job(job["job_id"], token)

    # 10. Crash recovery of expired leases
    def test_crash_recovery_expired_leases(self):
        job, _ = self.queue.enqueue(self.game1.game_id, max_attempts=2)

        # Attempt 1: Worker claims with expired lease
        claim1 = self.queue.claim_next_job("worker_crash", lease_seconds=-10)
        self.assertEqual(self.lib.get_game(self.game1.game_id).analysis_status, ANALYSIS_ANALYZING)

        # Run recovery
        res = self.queue.recover_expired_leases()
        self.assertEqual(res["requeued"], 1)
        self.assertEqual(res["failed"], 0)

        # Job is back to PENDING and game status reverted to NOT_ANALYZED
        j1 = self.queue.get_job(job["job_id"])
        self.assertEqual(j1["status"], JOB_PENDING)
        self.assertEqual(j1["attempt_count"], 1)
        self.assertEqual(self.lib.get_game(self.game1.game_id).analysis_status, ANALYSIS_NOT_ANALYZED)

        # Attempt 2: Worker claims again with expired lease
        claim2 = self.queue.claim_next_job("worker_crash", lease_seconds=-10)
        self.assertEqual(claim2["attempt_count"], 2)

        # Run recovery again -> reaches max_attempts (2) -> marks FAILED
        res2 = self.queue.recover_expired_leases()
        self.assertEqual(res2["requeued"], 0)
        self.assertEqual(res2["failed"], 1)

        j2 = self.queue.get_job(job["job_id"])
        self.assertEqual(j2["status"], JOB_FAILED)
        self.assertIn("maximum attempts", j2["error_summary"])
        self.assertEqual(self.lib.get_game(self.game1.game_id).analysis_status, ANALYSIS_FAILED)

    # 11. Cancellation and retry lifecycle
    def test_cancel_and_retry(self):
        job, _ = self.queue.enqueue(self.game1.game_id)

        # Cancel pending job
        self.queue.cancel_job(job["job_id"])
        self.assertEqual(self.queue.get_job(job["job_id"])["status"], JOB_CANCELLED)

        # Cannot cancel running job
        job2, _ = self.queue.enqueue(self.game2.game_id)
        self.queue.claim_next_job("w")
        with self.assertRaises(InvalidStateTransitionError):
            self.queue.cancel_job(job2["job_id"])

        # Retry cancelled job
        self.queue.retry_job(job["job_id"])
        self.assertEqual(self.queue.get_job(job["job_id"])["status"], JOB_PENDING)

    # 12. Retry all failed jobs
    def test_retry_all_failed(self):
        job1, _ = self.queue.enqueue(self.game1.game_id, max_attempts=1)
        job2, _ = self.queue.enqueue(self.game2.game_id, max_attempts=1)

        c1 = self.queue.claim_next_job("w1")
        c2 = self.queue.claim_next_job("w2")

        self.queue.fail_job(c1["job_id"], c1["claim_token"], "err", retryable=False)
        self.queue.fail_job(c2["job_id"], c2["claim_token"], "err", retryable=False)

        summary = self.queue.get_status_summary()
        self.assertEqual(summary[JOB_FAILED], 2)

        count = self.queue.retry_all_failed()
        self.assertEqual(count, 2)

        summary2 = self.queue.get_status_summary()
        self.assertEqual(summary2[JOB_PENDING], 2)
        self.assertEqual(summary2[JOB_FAILED], 0)

    # 13. AnalysisConfig validation & hashing
    def test_config_validation(self):
        with self.assertRaises(ConfigValidationError):
            AnalysisConfig(depth=0)
        with self.assertRaises(ConfigValidationError):
            AnalysisConfig(multipv=0)
        with self.assertRaises(ConfigValidationError):
            AnalysisConfig(threads=0)
        with self.assertRaises(ConfigValidationError):
            AnalysisConfig(hash_size=0)
        with self.assertRaises(ConfigValidationError):
            AnalysisConfig(time_limit=-1.0)

        c1 = AnalysisConfig(depth=16, multipv=3)
        c2 = AnalysisConfig(multipv=3, depth=16)
        # Deterministic hashing regardless of construction order
        self.assertEqual(c1.config_hash(), c2.config_hash())

    # 14. Pagination and listing
    def test_list_and_pagination(self):
        for i in range(15):
            g, _ = self.lib.add_game(_make_pgn(f"bulk_{i}"))
            self.queue.enqueue(g.game_id, priority=i)

        page1 = self.queue.list_jobs(limit=10, offset=0)
        self.assertEqual(len(page1), 10)
        # Ordered by priority DESC
        self.assertEqual(page1[0]["priority"], 14)

        page2 = self.queue.list_jobs(limit=10, offset=10)
        self.assertEqual(len(page2), 5)
        self.assertEqual(page2[-1]["priority"], 0)

    # 15. Idempotent schema migration across restarts
    def test_idempotent_schema_migration(self):
        job, _ = self.queue.enqueue(self.game1.game_id)
        # Re-initialize DB
        self.lib._init_db()

        fetched = self.queue.get_job(job["job_id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["game_id"], self.game1.game_id)

    # 16. Concurrent enqueue collisions handled cleanly
    def test_concurrent_enqueue_collisions(self):
        import concurrent.futures
        import tempfile
        import os

        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            lib = GameLibrary(db_path)
            lib._conn.execute("PRAGMA journal_mode=WAL;")
            queue = AnalysisQueue(lib)
            g, _ = lib.add_game(_make_pgn("thread_test"))

            results = []
            def enqueue_worker():
                with GameLibrary(db_path) as thread_lib:
                    thread_queue = AnalysisQueue(thread_lib)
                    return thread_queue.enqueue(g.game_id)

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                futures = [ex.submit(enqueue_worker) for _ in range(20)]
                for f in concurrent.futures.as_completed(futures):
                    results.append(f.result())

            new_flags = [is_new for job, is_new in results]
            self.assertEqual(new_flags.count(True), 1)
            self.assertEqual(new_flags.count(False), 19)

            job_ids = {job["job_id"] for job, is_new in results}
            self.assertEqual(len(job_ids), 1)

            summary = queue.get_status_summary()
            self.assertEqual(summary["total"], 1)
            lib.close()
        finally:
            if os.path.exists(db_path):
                os.remove(db_path)

    # 17. Retry collision when an active job already exists
    def test_retry_collision_with_active_job(self):
        # Create job 1 and fail it
        job1, _ = self.queue.enqueue(self.game1.game_id, max_attempts=1)
        claim1 = self.queue.claim_next_job("w1")
        self.queue.fail_job(job1["job_id"], claim1["claim_token"], "Failed", retryable=False)
        self.assertEqual(self.queue.get_job(job1["job_id"])["status"], JOB_FAILED)

        # Enqueue job 2 for the same game
        job2, is_new2 = self.queue.enqueue(self.game1.game_id)
        self.assertTrue(is_new2)
        self.assertEqual(job2["status"], JOB_PENDING)

        # Retrying job 1 while job 2 is active raises QueueError
        with self.assertRaises(QueueError):
            self.queue.retry_job(job1["job_id"])


class TestQueueCLI(unittest.TestCase):
    """Test suite for queue CLI entry points."""

    def setUp(self):
        import tempfile
        import os
        self.fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        with GameLibrary(self.db_path) as lib:
            self.game1, _ = lib.add_game(_make_pgn("cli1"))
            self.game2, _ = lib.add_game(_make_pgn("cli2"))

    def tearDown(self):
        import os
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_cli_status_and_enqueue(self):
        from src.queue_cli import main
        import io
        from contextlib import redirect_stdout

        out = io.StringIO()
        with redirect_stdout(out):
            main(["--db", self.db_path, "status"])
        self.assertIn("Total jobs:      0", out.getvalue())

        # Enqueue game1
        out = io.StringIO()
        with redirect_stdout(out):
            main(["--db", self.db_path, "enqueue", self.game1.game_id, "--priority", "3"])
        self.assertIn("Enqueued:                 1", out.getvalue())

        # List jobs
        out = io.StringIO()
        with redirect_stdout(out):
            main(["--db", self.db_path, "list"])
        self.assertIn("PENDING", out.getvalue())

        # Enqueue all unanalyzed
        out = io.StringIO()
        with redirect_stdout(out):
            main(["--db", self.db_path, "enqueue-all"])
        # game1 already active, game2 enqueued
        self.assertIn("Enqueued:                 1", out.getvalue())
        self.assertIn("Skipped (already active): 1", out.getvalue())

        # Recover
        out = io.StringIO()
        with redirect_stdout(out):
            main(["--db", self.db_path, "recover"])
        self.assertIn("Recovered", out.getvalue())


if __name__ == "__main__":
    unittest.main()


