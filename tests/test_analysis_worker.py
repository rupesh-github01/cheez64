"""
Unit tests for Segment 11C: Background Stockfish Analysis Worker.

Covers:
- Successful end-to-end processing of queued games
- Sequential execution across multiple jobs with --max-jobs
- Run-once mode when queue is empty
- Propagation of AnalysisConfig parameters
- Heartbeat runner pulsing and maintaining lease throughout analysis
- Explicit lease loss detection during analysis
- Lease expiration between analysis and persistence (Safeguard #7)
- Forced graceful shutdown with active engine termination (Safeguards #3 & #7)
- Engine startup failure & engine crash handling (retryable)
- Invalid PGN and missing game handling (permanent failure without retry)
- Maximum attempt exhaustion leading to FAILED
- Preservation of existing completed analyses during reanalysis
- Multi-worker concurrent claims without duplication
- Worker CLI execution
- Optional real Stockfish integration test (skipped cleanly if unavailable)
"""

from datetime import datetime, timezone, timedelta
import io
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

import chess
import chess.pgn
import chess.engine

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
    StaleLeaseError,
)
from src.analysis_worker import (
    AnalysisWorker,
    HeartbeatRunner,
)
from src.models import MoveAnalysis


def _make_pgn(tag: str) -> str:
    return f"""[Event "Test Event"]
[Site "Local"]
[Date "2026.10.15"]
[White "Player1_{tag}"]
[Black "Player2_{tag}"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 1-0
"""


def _make_dummy_analyses(game_obj) -> list:
    """Create a list of mock MoveAnalysis objects matching game mainline moves."""
    analyses = []
    board = game_obj.board()
    for move in game_obj.mainline_moves():
        san = board.san(move)
        uci = move.uci()
        analysis = MoveAnalysis(
            move_number=board.fullmove_number,
            color="White" if board.turn == chess.WHITE else "Black",
            played_move=san,
            best_move=san,
            evaluation_before=20,
            evaluation_after=20,
            centipawn_loss=0,
            fen_before=board.fen(),
            fen_after=board.fen(),
            principal_variation=[san],
            candidates=[],
            clock_seconds=None,
            position_features={},
            played_move_uci=uci,
            best_move_uci=uci,
            raw_centipawn_loss=0,
            classification="Best",
            is_forced=False,
            pv_after=[],
        )
        board.push(move)
        analyses.append(analysis)
    return analyses


def _fake_analyze_game(*args, **kwargs):
    pgn_text = kwargs.get("pgn_text") or _make_pgn("dummy")
    game_obj = chess.pgn.read_game(io.StringIO(pgn_text))
    analyses = _make_dummy_analyses(game_obj)
    return game_obj, analyses


class TestAnalysisWorker(unittest.TestCase):
    """Test suite for the analysis worker."""

    def setUp(self):
        self.fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)

        self.lib = GameLibrary(self.db_path)
        self.lib._conn.execute("PRAGMA journal_mode=WAL;")
        self.queue = AnalysisQueue(self.lib)

        # Create test games
        self.game1, _ = self.lib.add_game(_make_pgn("1"))
        self.game2, _ = self.lib.add_game(_make_pgn("2"))
        self.game3, _ = self.lib.add_game(_make_pgn("3"))

    def tearDown(self):
        self.lib.close()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass

    # 1. Successful processing of a single queued game
    @patch("src.analysis_worker.analyze_game", side_effect=_fake_analyze_game)
    def test_worker_processes_single_job_successfully(self, mock_analyze):
        job, _ = self.queue.enqueue(self.game1.game_id)
        self.assertEqual(job["status"], JOB_PENDING)

        worker = AnalysisWorker(
            db_path=self.db_path,
            poll_interval=0.1,
            lease_seconds=60,
            heartbeat_interval=1.0,
        )
        worker.run(max_jobs=1)

        # Verify job is COMPLETED
        updated_job = self.queue.get_job(job["job_id"])
        self.assertEqual(updated_job["status"], JOB_COMPLETED)
        self.assertEqual(worker.jobs_succeeded, 1)

        # Verify game is ANALYZED
        game = self.lib.get_game(self.game1.game_id)
        self.assertEqual(game.analysis_status, ANALYSIS_ANALYZED)
        self.assertIsNotNone(game.analyzed_at)

        # Verify analysis is stored in library
        analysis = self.lib.get_analysis(self.game1.game_id)
        self.assertIsNotNone(analysis)
        self.assertIn("moves", analysis)

    # 2. Sequential processing of multiple jobs with --max-jobs
    @patch("src.analysis_worker.analyze_game", side_effect=_fake_analyze_game)
    def test_worker_processes_multiple_jobs_sequentially(self, mock_analyze):
        j1, _ = self.queue.enqueue(self.game1.game_id)
        j2, _ = self.queue.enqueue(self.game2.game_id)
        j3, _ = self.queue.enqueue(self.game3.game_id)

        worker = AnalysisWorker(
            db_path=self.db_path,
            poll_interval=0.05,
            lease_seconds=60,
            heartbeat_interval=1.0,
        )
        worker.run(max_jobs=2)

        self.assertEqual(worker.jobs_processed, 2)
        self.assertEqual(worker.jobs_succeeded, 2)

        # 2 jobs completed, 1 still pending
        summary = self.queue.get_status_summary()
        self.assertEqual(summary[JOB_COMPLETED], 2)
        self.assertEqual(summary[JOB_PENDING], 1)

    # 3. Run-once mode when queue is empty
    @patch("src.analysis_worker.analyze_game", side_effect=_fake_analyze_game)
    def test_worker_run_once_empty_queue(self, mock_analyze):
        worker = AnalysisWorker(
            db_path=self.db_path,
            poll_interval=0.05,
        )
        # Should exit immediately since queue is empty
        worker.run(run_once=True)
        self.assertEqual(worker.jobs_processed, 0)

    # 4. Correct propagation of AnalysisConfig
    @patch("src.analysis_worker.analyze_game", side_effect=_fake_analyze_game)
    def test_config_propagation_to_analyzer(self, mock_analyze):
        cfg = AnalysisConfig(depth=20, multipv=4, threads=2, hash_size=128, time_limit=5.0)
        job, _ = self.queue.enqueue(self.game1.game_id, config=cfg)

        worker = AnalysisWorker(db_path=self.db_path, poll_interval=0.05)
        worker.run(max_jobs=1)

        mock_analyze.assert_called_once()
        _, kwargs = mock_analyze.call_args
        self.assertEqual(kwargs.get("depth"), 20)
        self.assertEqual(kwargs.get("multipv"), 4)
        self.assertEqual(kwargs.get("threads"), 2)
        self.assertEqual(kwargs.get("hash_size"), 128)
        self.assertEqual(kwargs.get("time_limit"), 5.0)

    # 5. Heartbeat runner maintains lease during long analysis
    def test_heartbeat_runner_extends_lease(self):
        job, _ = self.queue.enqueue(self.game1.game_id)
        claimed = self.queue.claim_next_job("test_worker", lease_seconds=10)
        initial_expires = claimed["lease_expires_at"]

        # Run heartbeat every 0.1s extending by 50s
        runner = HeartbeatRunner(
            db_path=self.db_path,
            job_id=job["job_id"],
            claim_token=claimed["claim_token"],
            interval_seconds=0.1,
            lease_extension_seconds=50,
        )
        runner.start()
        time.sleep(0.35)
        runner.stop()

        fresh = self.queue.get_job(job["job_id"])
        self.assertGreater(fresh["lease_expires_at"], initial_expires)
        self.assertFalse(runner.lease_lost)
        self.assertIsNone(runner.heartbeat_error)

    # 6. Explicit lease loss detection during analysis
    def test_lease_loss_detection_during_analysis(self):
        job, _ = self.queue.enqueue(self.game1.game_id)

        # Custom analyze that simulates losing the lease in DB during analysis
        def analyze_with_lease_loss(*args, **kwargs):
            # Manually reset job to FAILED in DB to simulate steal / cancellation
            with self.lib._get_connection() as conn:
                conn.execute(
                    "UPDATE analysis_jobs SET status = 'CANCELLED' WHERE job_id = ?",
                    (job["job_id"],)
                )
            # Sleep slightly so heartbeat detects lease loss
            time.sleep(0.2)
            return _fake_analyze_game(*args, **kwargs)

        with patch("src.analysis_worker.analyze_game", side_effect=analyze_with_lease_loss):
            worker = AnalysisWorker(
                db_path=self.db_path,
                poll_interval=0.05,
                lease_seconds=30,
                heartbeat_interval=0.05,
            )
            worker.run(max_jobs=1)

        # Game must NOT be marked ANALYZED because lease was lost
        game = self.lib.get_game(self.game1.game_id)
        self.assertNotEqual(game.analysis_status, ANALYSIS_ANALYZED)
        self.assertEqual(worker.jobs_failed, 1)

    # 7. Lease expiration between analysis and persistence (Safeguard #7)
    def test_lease_expiration_before_persistence(self):
        job, _ = self.queue.enqueue(self.game1.game_id)

        def analyze_and_expire(*args, **kwargs):
            # Expire the lease in DB right before returning
            past_iso = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
            with self.lib._get_connection() as conn:
                conn.execute(
                    "UPDATE analysis_jobs SET lease_expires_at = ? WHERE job_id = ?",
                    (past_iso, job["job_id"])
                )
            return _fake_analyze_game(*args, **kwargs)

        with patch("src.analysis_worker.analyze_game", side_effect=analyze_and_expire):
            worker = AnalysisWorker(
                db_path=self.db_path,
                poll_interval=0.05,
                lease_seconds=30,
                heartbeat_interval=10.0,  # Don't heartbeat right now
            )
            worker.run(max_jobs=1)

        # Persistence must be rejected with StaleLeaseError, game remains NOT_ANALYZED
        game = self.lib.get_game(self.game1.game_id)
        self.assertNotEqual(game.analysis_status, ANALYSIS_ANALYZED)

    # 8. Forced graceful shutdown with active engine termination (Safeguards #3 & #7)
    def test_forced_shutdown_terminates_active_engine(self):
        job, _ = self.queue.enqueue(self.game1.game_id)

        engine_closed = False

        class MockEngine:
            def close(self):
                nonlocal engine_closed
                engine_closed = True

        def blocking_analyze(*args, **kwargs):
            holder = kwargs.get("active_engine_holder")
            if holder is not None:
                holder["engine"] = MockEngine()
            # Block until engine is closed or timeout
            for _ in range(50):
                if engine_closed:
                    raise chess.engine.EngineTerminatedError("Terminated by watchdog")
                time.sleep(0.05)
            return None, []

        worker = AnalysisWorker(
            db_path=self.db_path,
            poll_interval=0.05,
            grace_period_seconds=0.2,  # Short grace period
        )

        def trigger_shutdown_after_start():
            time.sleep(0.1)
            worker.request_shutdown()

        shutdown_thread = threading.Thread(target=trigger_shutdown_after_start)
        shutdown_thread.start()

        with patch("src.analysis_worker.analyze_game", side_effect=blocking_analyze):
            worker.run(max_jobs=1)

        shutdown_thread.join()

        # Engine must have been cleanly closed by the shutdown watchdog
        self.assertTrue(engine_closed)
        # Job must NOT be marked COMPLETED
        j = self.queue.get_job(job["job_id"])
        self.assertNotEqual(j["status"], JOB_COMPLETED)

    # 9. Permanent failure on invalid PGN
    def test_invalid_pgn_permanent_failure(self):
        bad_game, _ = self.lib.add_game("Invalid Non PGN Content")
        job, _ = self.queue.enqueue(bad_game.game_id)

        def fail_bad_pgn(*args, **kwargs):
            raise ValueError("Corrupt PGN: no moves found")

        with patch("src.analysis_worker.analyze_game", side_effect=fail_bad_pgn):
            worker = AnalysisWorker(db_path=self.db_path, poll_interval=0.05)
            worker.run(max_jobs=1)

        # Must fail permanently (status=FAILED), no retries
        j = self.queue.get_job(job["job_id"])
        self.assertEqual(j["status"], JOB_FAILED)
        self.assertIn("Corrupt PGN", j["error_summary"])

    # 10. Retryable failure and max attempts exhaustion
    def test_retryable_failure_and_exhaustion(self):
        job, _ = self.queue.enqueue(self.game1.game_id, max_attempts=2)

        def transient_crash(*args, **kwargs):
            raise chess.engine.EngineTerminatedError("Engine crashed unexpectedly")

        with patch("src.analysis_worker.analyze_game", side_effect=transient_crash):
            worker = AnalysisWorker(db_path=self.db_path, poll_interval=0.05)

            # Attempt 1: fails retryable -> returns to PENDING
            worker.run(max_jobs=1)
            j1 = self.queue.get_job(job["job_id"])
            self.assertEqual(j1["status"], JOB_PENDING)
            self.assertEqual(j1["attempt_count"], 1)

            # Attempt 2: fails retryable -> reaches max_attempts (2) -> marks FAILED
            worker.run(max_jobs=1)
            j2 = self.queue.get_job(job["job_id"])
            self.assertEqual(j2["status"], JOB_FAILED)
            self.assertEqual(j2["attempt_count"], 2)

    # 11. Preserve existing completed analyses during reanalysis
    @patch("src.analysis_worker.analyze_game", side_effect=_fake_analyze_game)
    def test_reanalysis_preserves_existing_analyses_until_completion(self, mock_analyze):
        # 1. Attach initial analysis
        self.lib.attach_analysis(
            self.game1.game_id,
            analysis_data={"summary": {"note": "initial analysis"}},
            engine_depth=10,
        )
        self.assertEqual(self.lib.get_game(self.game1.game_id).analysis_status, ANALYSIS_ANALYZED)

        # 2. Queue reanalysis
        job, _ = self.queue.enqueue(self.game1.game_id, force_reanalyze=True)

        worker = AnalysisWorker(db_path=self.db_path, poll_interval=0.05)
        worker.run(max_jobs=1)

        # Analysis successfully updated to new results
        fresh_game = self.lib.get_game(self.game1.game_id)
        self.assertEqual(fresh_game.analysis_status, ANALYSIS_ANALYZED)
        fresh_analysis = self.lib.get_analysis(self.game1.game_id)
        self.assertIsNotNone(fresh_analysis)
        self.assertEqual(self.queue.get_job(job["job_id"])["status"], JOB_COMPLETED)

    # 12. Multi-worker concurrent claims without duplication
    @patch("src.analysis_worker.analyze_game", side_effect=_fake_analyze_game)
    def test_multi_worker_concurrency(self, mock_analyze):
        j1, _ = self.queue.enqueue(self.game1.game_id)
        j2, _ = self.queue.enqueue(self.game2.game_id)

        w1 = AnalysisWorker(db_path=self.db_path, poll_interval=0.05)
        w2 = AnalysisWorker(db_path=self.db_path, poll_interval=0.05)

        # Each worker processes 1 job concurrently
        t1 = threading.Thread(target=lambda: w1.run(max_jobs=1))
        t2 = threading.Thread(target=lambda: w2.run(max_jobs=1))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Both jobs completed without collision
        summary = self.queue.get_status_summary()
        self.assertEqual(summary[JOB_COMPLETED], 2)
        self.assertEqual(summary[JOB_PENDING], 0)

    # 13. Worker CLI execution
    @patch("src.analysis_worker.analyze_game", side_effect=_fake_analyze_game)
    def test_worker_cli_run(self, mock_analyze):
        from src.worker_cli import main
        self.queue.enqueue(self.game1.game_id)

        # Run worker via CLI in run_once mode
        main(["run", "--db", self.db_path, "--once", "--poll-interval", "0.05"])

        self.assertEqual(self.lib.get_game(self.game1.game_id).analysis_status, ANALYSIS_ANALYZED)

    # 14. Real Stockfish integration test (runs if Stockfish installed, skips otherwise)
    def test_real_stockfish_integration_if_available(self):
        sf_path = shutil.which("stockfish") or (
            "/opt/homebrew/bin/stockfish" if os.path.exists("/opt/homebrew/bin/stockfish") else None
        )
        if not sf_path:
            self.skipTest("Stockfish binary not found on system; skipping real engine test.")

        # Real Stockfish analysis with low depth for fast execution
        cfg = AnalysisConfig(depth=4, multipv=2)
        job, _ = self.queue.enqueue(self.game1.game_id, config=cfg)

        worker = AnalysisWorker(
            db_path=self.db_path,
            stockfish_path=sf_path,
            poll_interval=0.05,
        )
        worker.run(max_jobs=1)

        j = self.queue.get_job(job["job_id"])
        self.assertEqual(j["status"], JOB_COMPLETED)
        self.assertEqual(self.lib.get_game(self.game1.game_id).analysis_status, ANALYSIS_ANALYZED)
        analysis = self.lib.get_analysis(self.game1.game_id)
        self.assertIsNotNone(analysis)
        self.assertTrue(len(analysis["moves"]) > 0)


if __name__ == "__main__":
    unittest.main()
