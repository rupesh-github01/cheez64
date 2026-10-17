"""
End-to-End Integration, Reliability, and Concurrency Tests (Segment 12).

Tests:
1. Complete functional workflow: Import -> Deduplication -> Queue -> Real Stockfish Worker -> Persistence -> FastAPI API -> Dashboard.
2. Import reliability: Interrupted import, checkpoint persistence, resumption, concurrent 409 conflict, startup stale lease recovery.
3. Worker reliability: Expired lease recovery, atomic multi-worker claiming, safe re-analysis.
4. Real subprocess SIGTERM termination of AnalysisWorker running Stockfish, with OS-level verification of child engine cleanup.
5. SQLite multi-threaded concurrency under load, with PRAGMA integrity_check and foreign_key_check.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import signal
import subprocess
import sys
import tempfile
import time
from typing import List, Tuple
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from api.main import app, set_management_deps
from api.import_manager import ImportManager
from src.library import GameLibrary
from src.analysis_queue import AnalysisQueue, AnalysisConfig
from src.analysis_worker import AnalysisWorker, STOCKFISH_PATH
from src.importers.base import BaseImporter, ImportBatch, ImportResult


FIXTURE_PGN = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2024.01.15"]
[White "alice"]
[Black "bob"]
[Result "1-0"]
[ECO "C50"]
[WhiteElo "1500"]
[BlackElo "1480"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. d3 Nf6 5. Nc3 d6 6. Bg5 h6 7. Bxf6 Qxf6 8. Nd5 Qd8 9. c3 a6 10. d4 exd4 11. cxd4 Ba7 12. h3 O-O 13. O-O Re8 14. Re1 Be6 15. Rc1 Rc8 16. a3 Ne7 17. Nxe7+ Rxe7 18. Bxe6 Rxe6 19. Qb3 Bb6 20. Qd5 c6 21. Qf5 Qe8 22. d5 Rf6 23. Qg4 Rg6 24. Qf4 Rf6 25. Qd2 cxd5 26. exd5 Rxc1 27. Qxc1 Qd7 28. Qc3 Rg6 29. Kf1 Qb5+ 30. Re2 Qxd5 31. Qc8+ Kh7 32. Re8 Re6 33. Rh8+ Kg6 34. Qc2+ Qe4 35. Qxe4+ Rxe4 36. Rb8 Re7 37. Rc8 Rc7 38. Rxc7 Bxc7 39. Ke2 Kf5 40. Kd3 Bb6 41. Ke2 Ke4 42. Nd2+ Kd5 43. f3 Bd4 44. b3 b5 45. Kd3 Bb2 46. a4 bxa4 47. bxa4 Kc5 48. Kc2 Be5 49. Kb3 d5 50. Nf1 Bf4 51. g3 Bc1 52. Kc2 Ba3 53. Kb3 Bc1 54. Kc2 Bg5 55. f4 Bf6 56. Ne3 Bd4 57. Nf5 Bf2 58. g4 g6 59. Nxh6 f6 60. f5 gxf5 61. Nxf5 Kb4 62. h4 Kxa4 63. h5 Bc5 64. h6 Bf8 65. h7 1-0
"""

FIXTURE_PGN_2 = """[Event "Live Chess 2"]
[Site "Chess.com"]
[Date "2024.01.16"]
[White "charlie"]
[Black "alice"]
[Result "0-1"]
[ECO "B01"]
[WhiteElo "1520"]
[BlackElo "1500"]

1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 4. d4 Nf6 5. Nf3 Bf5 6. Bc4 e6 7. O-O c6 8. Re1 Nbd7 9. Bd2 Bb4 10. a3 Bxc3 11. Bxc3 Qc7 12. Ne5 O-O 13. Bd3 Bxd3 14. Qxd3 Rad8 15. Qg3 Nd5 16. Bd2 Nxe5 17. dxe5 Ne7 18. Bg5 Rd5 19. Bxe7 Qxe7 20. Rad1 Rfd8 21. Rxd5 Rxd5 22. Qe3 b6 23. c4 Rd8 24. Qe4 Qd7 25. g3 c5 26. Kg2 Qd4 27. b4 cxb4 28. axb4 g6 29. h4 h5 30. Re3 Qxe4+ 31. Rxe4 Rd2 32. b5 Kf8 33. Kf3 Ke7 34. Ke3 Ra2 35. Rd4 Ra3+ 36. Ke4 Ra4 37. f3 Ra3 38. g4 Ra1 39. gxh5 gxh5 40. Kf4 Rg1 41. Rd6 Rc1 42. Rc6 Kd7 43. Kg5 Rf1 44. f4 Rf2 45. Rd6+ Ke7 46. Rd4 Rg2+ 47. Kxh5 f5 48. exf6+ Kxf6 49. Rd7 Rc2 50. Rc7 Kf5 51. Rf7+ Ke4 52. Kg6 Rxc4 53. h5 Rc8 54. h6 Rg8+ 55. Rg7 Rh8 56. h7 Kxf4 57. Rg8 Rxh7 58. Kxh7 e5 59. Ra8 e4 60. Rxa7 e3 61. Kg6 e2 62. Re7 Kf3 63. Kf5 Kf2 64. Re6 e1=Q 65. Rxe1 Kxe1 66. Ke4 Kd2 67. Kd4 Kc2 68. Kd5 Kb3 69. Kc6 Kb4 70. Kxb6 0-1
"""


@pytest.fixture
def isolated_env():
    """Setup an isolated temporary SQLite database and configure app dependencies."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    import api.main as api_main
    orig_db = api_main.DB_PATH
    api_main.DB_PATH = db_path

    mgr = ImportManager(db_path)
    set_management_deps(db_path, mgr)
    client = TestClient(app, base_url="http://localhost:8000")

    yield client, db_path, mgr

    mgr.shutdown()
    api_main.DB_PATH = orig_db
    try:
        os.unlink(db_path)
    except Exception:
        pass


class TestFullE2EWorkflow:
    """
    Validate the complete pipeline:
    Import -> Deduplication -> Queue -> Real Stockfish Worker -> Persistence -> FastAPI -> Dashboard.
    """

    @pytest.mark.skipif(
        not os.path.exists(STOCKFISH_PATH) and not subprocess.run(["which", "stockfish"], capture_output=True).returncode == 0,
        reason="Stockfish binary not available on system",
    )
    def test_complete_e2e_pipeline_with_real_stockfish(self, isolated_env):
        client, db_path, mgr = isolated_env

        # 1. Store game in GameLibrary
        with GameLibrary(db_path) as lib:
            game1, is_new1 = lib.add_game(FIXTURE_PGN, source="chess.com", source_game_id="game_e2e_1")
            assert is_new1 is True
            assert game1.white == "alice"
            assert game1.black == "bob"
            assert game1.analysis_status == "NOT_ANALYZED"

            # 2. Test duplicate rejection
            dup_game, is_new_dup = lib.add_game(FIXTURE_PGN, source="chess.com", source_game_id="game_e2e_dup")
            assert is_new_dup is False
            assert dup_game.game_id == game1.game_id
            assert lib.count_games() == 1

        # 3. Enqueue via Management API with shallow depth for fast bounded execution
        r_queue = client.post("/api/management/analysis/queue", json={
            "game_ids": [game1.game_id],
            "priority": 10,
            "depth": 8,
        })
        assert r_queue.status_code == 202
        assert r_queue.json()["queued_count"] == 1
        job_id = r_queue.json()["job_ids"][0]

        # 4. Verify queue reflects PENDING job
        r_list = client.get("/api/management/analysis/queue?status=PENDING")
        assert r_list.status_code == 200
        assert any(j["job_id"] == job_id for j in r_list.json()["jobs"])

        # 5. Process job using real Stockfish worker (bounded single-job run)
        worker = AnalysisWorker(
            db_path=db_path,
            worker_id="test_e2e_worker",
            poll_interval=0.5,
            lease_seconds=60,
        )
        worker.run(max_jobs=1, run_once=True)
        assert worker.jobs_processed == 1
        assert worker.jobs_succeeded == 1

        # 6. Verify atomic completion in DB
        with GameLibrary(db_path) as lib:
            updated_game = lib.get_game(game1.game_id)
            assert updated_game.analysis_status == "ANALYZED"
            assert updated_game.analyzed_at is not None

            analysis_record = lib.get_analysis(game1.game_id)
            assert analysis_record is not None
            assert len(analysis_record.get("moves", [])) > 0
            assert "summary" in analysis_record
            assert "critical_positions_count" in analysis_record["summary"]

        # 7. Verify all FastAPI read endpoints serve the completed analysis
        r_game = client.get(f"/api/games/{game1.game_id}")
        assert r_game.status_code == 200
        assert r_game.json()["analysis_status"] == "ANALYZED"

        r_analysis = client.get(f"/api/games/{game1.game_id}/analysis")
        assert r_analysis.status_code == 200
        assert "summary" in r_analysis.json()
        assert len(r_analysis.json()["moves"]) > 0

        r_moves = client.get(f"/api/games/{game1.game_id}/moves")
        assert r_moves.status_code == 200
        assert len(r_moves.json()["moves"]) == updated_game.ply_count

        r_critical = client.get(f"/api/games/{game1.game_id}/critical")
        assert r_critical.status_code == 200

        r_episodes = client.get(f"/api/games/{game1.game_id}/episodes")
        assert r_episodes.status_code == 200

        # 8. Verify Dashboard calculates aggregates grounded in this analysis
        r_dash = client.get("/api/dashboard?player=alice")
        assert r_dash.status_code == 200
        dash_data = r_dash.json()
        assert dash_data["player_name"].lower() == "alice"
        assert dash_data["overview"]["player_games_analyzed"] == 1
        assert "trends" in dash_data

        # 9. Verify Management Summary reflects 0 unanalyzed and 1 completed
        r_summary = client.get("/api/management/summary")
        assert r_summary.status_code == 200
        sum_data = r_summary.json()
        assert sum_data["library"]["analyzed_games"] == 1
        assert sum_data["library"]["unanalyzed_games"] == 0
        assert sum_data["analysis_queue"]["completed"] == 1


class TestImportReliability:
    """
    Validate import interruption, checkpoints, resumption, concurrent 409 conflict, and stale lease recovery.
    """

    def test_interrupted_import_checkpoint_and_resumption(self, isolated_env):
        client, db_path, mgr = isolated_env

        # Custom mock importer that fails on batch 2
        class FlakyMockImporter(BaseImporter):
            source = "chess.com"

            def fetch_games(self, username, since=None, until=None, max_games=None):
                for batch in self.fetch_games_batched(username, since, until, max_games):
                    for pgn_text, src_id in batch.games:
                        yield (pgn_text, src_id)

            def fetch_games_batched(self, username, since=None, until=None, max_games=None, checkpoint=None):
                if checkpoint is None:
                    # Batch 1 succeeds
                    yield ImportBatch(
                        games=[(FIXTURE_PGN, "game_f1")],
                        checkpoint="checkpoint_batch_1",
                        has_more=True,
                        batch_label="2024-01",
                    )
                    # Simulated network drop on batch 2
                    raise ConnectionResetError("Simulated provider TCP disconnect")
                elif checkpoint == "checkpoint_batch_1":
                    # Resumption yields Batch 2
                    yield ImportBatch(
                        games=[(FIXTURE_PGN_2, "game_f2")],
                        checkpoint="checkpoint_batch_2",
                        has_more=False,
                        batch_label="2024-02",
                    )

        flaky = FlakyMockImporter()

        # Step 1: Initial import run fails on batch 2
        with GameLibrary(db_path) as lib:
            res1 = flaky.bulk_import_games(username="alice", library=lib)
            assert len(res1.errors) > 0
            assert res1.games_imported == 1

            # Verify checkpoint is safely persisted in import_jobs
            resumable_job = lib.find_resumable_import_job("chess.com", "alice")
            assert resumable_job is not None
            assert resumable_job["status"] == "failed"
            assert resumable_job["last_checkpoint"] == "checkpoint_batch_1"
            assert resumable_job["games_imported"] == 1

            # Step 2: Resume import from persisted checkpoint
            res2 = flaky.bulk_import_games(username="alice", library=lib, job_id=resumable_job["job_id"])
            assert len(res2.errors) == 0
            assert res2.games_imported == 2

            # Check job is now marked completed
            final_job = lib.get_import_job(resumable_job["job_id"])
            assert final_job["status"] == "completed"
            assert final_job["games_imported"] == 2

    def test_concurrent_import_conflict_returns_409(self, isolated_env):
        client, db_path, mgr = isolated_env

        # Create active lease
        with GameLibrary(db_path) as lib:
            lib.create_import_job(
                platform="chess.com",
                username="charlie",
                claimed_by="other_live_process",
                lease_seconds=300,
            )

        r = client.post("/api/management/imports", json={
            "platform": "chess.com",
            "username": "charlie",
        })
        assert r.status_code == 409
        assert "already running" in r.json()["detail"].lower()

    def test_stale_import_lease_recovery_on_startup(self, isolated_env):
        client, db_path, mgr = isolated_env

        # Create an orphaned import job with an expired lease
        with GameLibrary(db_path) as lib:
            job = lib.create_import_job(
                platform="lichess",
                username="abandoned_user",
                claimed_by="crashed_process",
                lease_seconds=-10,  # Expired in past
            )
            # Ensure status is running
            lib.claim_import_job(job["job_id"], "crashed_process", lease_seconds=-10)

        # Trigger recovery
        recovered_count = mgr.recover_stale_imports(stale_threshold_seconds=0)
        assert recovered_count >= 1

        with GameLibrary(db_path) as lib:
            recovered_job = lib.get_import_job(job["job_id"])
            assert recovered_job["status"] == "failed"
            assert "interrupted" in recovered_job["error_summary"].lower() or "resume" in recovered_job["error_summary"].lower()


class TestWorkerReliability:
    """
    Validate expired lease recovery, atomic multi-worker claiming, and safe re-analysis.
    """

    def test_expired_lease_requeued_with_incremented_attempts(self, isolated_env):
        client, db_path, mgr = isolated_env

        with GameLibrary(db_path) as lib:
            lib.add_game(FIXTURE_PGN, source="chess.com", source_game_id="game_exp_1")
            queue = AnalysisQueue(lib)
            job_ids = queue.enqueue_batch([lib.list_games()[0].game_id])
            job_id = job_ids["jobs"][0]["job_id"]

            # Claim with expired lease
            claimed_job = queue.claim_next_job("worker_crash", lease_seconds=-5)
            assert claimed_job["job_id"] == job_id
            assert claimed_job["attempt_count"] == 1

            # Run recovery
            recovery = queue.recover_expired_leases()
            assert recovery["requeued"] == 1

            # Job is back in PENDING
            fresh_job = queue.get_job(job_id)
            assert fresh_job["status"] == "PENDING"
            assert fresh_job["claim_token"] is None
            assert fresh_job["claimed_by"] is None

    def test_multi_worker_atomic_claiming_prevents_collisions(self, isolated_env):
        client, db_path, mgr = isolated_env

        with GameLibrary(db_path) as lib:
            lib.add_game(FIXTURE_PGN, source="chess.com", source_game_id="game_mw_1")
            queue = AnalysisQueue(lib)
            batch = queue.enqueue_batch([lib.list_games()[0].game_id])
            job_id = batch["jobs"][0]["job_id"]

            # Worker 1 claims job
            job_w1 = queue.claim_next_job("worker_1", lease_seconds=60)
            assert job_w1 is not None
            assert job_w1["job_id"] == job_id
            assert job_w1["claimed_by"] == "worker_1"

            # Worker 2 attempts to claim next job — queue is now empty
            job_w2 = queue.claim_next_job("worker_2", lease_seconds=60)
            assert job_w2 is None

    def test_safe_reanalysis_preserves_old_analysis_until_new_job_completes(self, isolated_env):
        client, db_path, mgr = isolated_env

        with GameLibrary(db_path) as lib:
            game, _ = lib.add_game(FIXTURE_PGN, source="chess.com", source_game_id="game_re_1")
            # Save dummy initial analysis
            lib.attach_analysis(game.game_id, {"version": "v1", "moves": []})
            assert lib.get_game(game.game_id).analysis_status == "ANALYZED"

            # Enqueue replacement analysis with force_reanalyze=True
            queue = AnalysisQueue(lib)
            batch = queue.enqueue_batch([game.game_id], force_reanalyze=True)
            assert batch["enqueued"] == 1
            new_job_id = batch["jobs"][0]["job_id"]

            # Verify old analysis remains ANALYZED and accessible while job is PENDING
            assert lib.get_game(game.game_id).analysis_status == "ANALYZED"
            old_analysis = lib.get_analysis(game.game_id)
            assert old_analysis["version"] == "v1"

            # Worker claims and completes new analysis
            claim = queue.claim_next_job("worker_re", lease_seconds=60)
            assert claim["job_id"] == new_job_id

            # While running, old analysis is still intact
            assert lib.get_game(game.game_id).analysis_status == "ANALYZED"

            # Complete with new analysis
            queue.complete_job_with_analysis(
                job_id=new_job_id,
                claim_token=claim["claim_token"],
                analysis_data={"version": "v2", "moves": [{"ply": 1}]},
            )

            # Updated atomically to v2
            updated_analysis = lib.get_analysis(game.game_id)
            assert updated_analysis["version"] == "v2"


class TestWorkerSigtermSubprocessCleanup:
    """
    Subprocess-level test launching worker CLI, verifying child Stockfish process,
    sending SIGTERM, and asserting clean process and child engine termination.
    """

    @pytest.mark.skipif(
        not os.path.exists(STOCKFISH_PATH) and not subprocess.run(["which", "stockfish"], capture_output=True).returncode == 0,
        reason="Stockfish binary not available on system",
    )
    def test_worker_sigterm_terminates_child_stockfish(self, isolated_env):
        client, db_path, mgr = isolated_env

        # 1. Setup game in DB
        with GameLibrary(db_path) as lib:
            game, _ = lib.add_game(FIXTURE_PGN, source="chess.com", source_game_id="game_sigterm_1")
            queue = AnalysisQueue(lib)
            # High depth so Stockfish runs long enough to receive SIGTERM
            queue.enqueue_batch([game.game_id], config=AnalysisConfig(depth=24, threads=1))

        # 2. Launch worker CLI as independent subprocess
        worker_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "src.worker_cli",
                "run",
                "--db",
                db_path,
                "--poll-interval",
                "0.2",
                "--grace-period",
                "1.0",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        child_stockfish_pid = None
        deadline = time.time() + 10.0

        # 3. Poll for child Stockfish process under worker PID
        try:
            while time.time() < deadline:
                try:
                    # Query child PIDs using pgrep
                    pgrep_out = subprocess.check_output(
                        ["pgrep", "-P", str(worker_proc.pid)],
                        stderr=subprocess.DEVNULL,
                    ).decode().strip()
                    if pgrep_out:
                        pids = [int(p) for p in pgrep_out.split()]
                        if pids:
                            child_stockfish_pid = pids[0]
                            break
                except subprocess.CalledProcessError:
                    pass
                time.sleep(0.3)

            assert child_stockfish_pid is not None, "Worker failed to spawn Stockfish child process within deadline"

            # Verify child Stockfish is running
            os.kill(child_stockfish_pid, 0)

            # 4. Send SIGTERM to the worker process
            os.kill(worker_proc.pid, signal.SIGTERM)

            # 5. Worker must terminate within bounded deadline (grace period 1.0s + buffer = 5.0s)
            worker_proc.wait(timeout=5.0)

            # 6. Verify child Stockfish process is cleanly terminated and gone from OS
            time.sleep(0.5)
            with pytest.raises(ProcessLookupError):
                os.kill(child_stockfish_pid, 0)

        finally:
            if worker_proc.poll() is None:
                worker_proc.kill()
                worker_proc.wait()
            if child_stockfish_pid is not None:
                try:
                    os.kill(child_stockfish_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        # 7. Check database: Job was not marked COMPLETED
        with GameLibrary(db_path) as lib:
            game_obj = lib.get_game(game.game_id)
            assert game_obj.analysis_status != "ANALYZED"


class TestSQLiteConcurrencyAndIntegrity:
    """
    Stress test SQLite WAL concurrency with simultaneous importer, queue worker,
    FastAPI reads, and dashboard computations.
    """

    def test_concurrent_subsystem_load_and_sqlite_integrity(self, isolated_env):
        client, db_path, mgr = isolated_env

        # Prepopulate with 5 baseline games
        with GameLibrary(db_path) as lib:
            for i in range(5):
                lib.add_game(
                    FIXTURE_PGN.replace("1500", str(1500 + i)),
                    source="chess.com",
                    source_game_id=f"base_game_{i}",
                )

        stop_event = False
        errors: List[str] = []

        def importer_thread():
            idx = 0
            while not stop_event:
                try:
                    with GameLibrary(db_path) as lib:
                        lib.add_game(
                            FIXTURE_PGN.replace("bob", f"bob_{idx}").replace("1480", str(1400 + (idx % 100))),
                            source="chess.com",
                            source_game_id=f"dyn_game_{idx}",
                        )
                        idx += 1
                except Exception as e:
                    errors.append(f"Importer error: {e}")
                time.sleep(0.05)

        def queue_worker_thread():
            worker = AnalysisWorker(
                db_path=db_path,
                worker_id="stress_worker",
                poll_interval=0.1,
                lease_seconds=30,
            )
            while not stop_event:
                try:
                    with GameLibrary(db_path) as lib:
                        queue = AnalysisQueue(lib)
                        # Queue all unanalyzed
                        queue.enqueue_all_unanalyzed()
                        # Claim job
                        job = queue.claim_next_job(worker.worker_id, lease_seconds=10)
                        if job:
                            # Mock rapid completion
                            queue.complete_job_with_analysis(
                                job["job_id"],
                                job["claim_token"],
                                {"version": "stress", "moves": []},
                            )
                except Exception as e:
                    errors.append(f"Queue/Worker error: {e}")
                time.sleep(0.05)

        def reader_api_thread():
            while not stop_event:
                try:
                    r1 = client.get("/api/games?limit=20")
                    assert r1.status_code == 200
                    r2 = client.get("/api/management/summary")
                    assert r2.status_code == 200
                except Exception as e:
                    errors.append(f"Reader error: {e}")
                time.sleep(0.04)

        def dashboard_thread():
            while not stop_event:
                try:
                    r = client.get("/api/dashboard?player=alice")
                    assert r.status_code == 200
                except Exception as e:
                    errors.append(f"Dashboard error: {e}")
                time.sleep(0.08)

        # Run concurrent threads for 4 seconds
        executor = ThreadPoolExecutor(max_workers=4)
        futures = [
            executor.submit(importer_thread),
            executor.submit(queue_worker_thread),
            executor.submit(reader_api_thread),
            executor.submit(dashboard_thread),
        ]

        time.sleep(4.0)
        stop_event = True
        executor.shutdown(wait=True)

        # Assert zero lock errors or exceptions
        assert len(errors) == 0, f"Encountered concurrency errors: {errors}"

        # Run SQLite integrity checks
        with GameLibrary(db_path) as lib:
            with lib._get_connection() as conn:
                integrity = conn.execute("PRAGMA integrity_check").fetchall()
                assert [tuple(r) for r in integrity] == [("ok",)], f"Integrity check failed: {integrity}"

                foreign_keys = conn.execute("PRAGMA foreign_key_check").fetchall()
                assert [tuple(r) for r in foreign_keys] == [], f"Foreign key check failed: {foreign_keys}"

                # Ensure game count is consistent
                total_games = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
                assert total_games >= 5
