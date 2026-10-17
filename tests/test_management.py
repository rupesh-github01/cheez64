"""
Tests for Management API (Segment 11D).

Covers:
- System summary endpoint
- Background import creation, duplication (409), listing, and resumption
- Analysis queue batch enqueue, all_unanalyzed enqueue, listing, retry, cancel, retry-all
- Worker status reporting (offline, online/idle, online/analyzing, stale)
- Loopback security validation for mutation endpoints
"""

import os
import tempfile
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from api.main import app, set_management_deps
from api.import_manager import ImportManager
from src.library import GameLibrary
from src.analysis_queue import AnalysisQueue, AnalysisConfig
from src.importers.base import ImportResult


SAMPLE_PGN = """[Event "Test Match"]
[Site "Chess.com"]
[Date "2024.01.15"]
[White "alice"]
[Black "bob"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O Nf6 5. d3 d6 1-0
"""

SAMPLE_PGN_2 = """[Event "Match 2"]
[Site "Chess.com"]
[Date "2024.01.16"]
[White "charlie"]
[Black "dave"]
[Result "0-1"]

1. d4 d5 2. c4 c6 3. Nf3 Nf6 4. e3 Bf5 0-1
"""


@pytest.fixture
def test_setup():
    """Create isolated temp DB and configure app dependencies."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    # Initialize library schema
    with GameLibrary(db_path) as lib:
        lib.add_game(SAMPLE_PGN, source="chess.com", source_game_id="gm1")
        lib.add_game(SAMPLE_PGN_2, source="chess.com", source_game_id="gm2")

    mgr = ImportManager(db_path)
    set_management_deps(db_path, mgr)

    client = TestClient(app, base_url="http://localhost:8000")

    yield client, db_path, mgr

    mgr.shutdown()
    try:
        os.unlink(db_path)
    except Exception:
        pass


class TestManagementSummary:
    def test_summary_structure_and_counts(self, test_setup):
        client, db_path, mgr = test_setup
        r = client.get("/api/management/summary")
        assert r.status_code == 200
        data = r.json()

        assert "library" in data
        assert data["library"]["total_games"] == 2
        assert data["library"]["unanalyzed_games"] == 2
        assert data["library"]["analyzed_games"] == 0

        assert "imports" in data
        assert data["imports"]["total"] == 0

        assert "analysis_queue" in data
        assert data["analysis_queue"]["pending"] == 0

        assert "worker" in data
        assert data["worker"]["status"] == "offline"


class TestImportEndpoints:
    @patch("api.import_manager.bulk_import_platform_games")
    def test_start_import_success(self, mock_import, test_setup):
        client, db_path, mgr = test_setup
        mock_import.return_value = ImportResult(
            source="chess.com",
            username="testuser",
            requested_range="all available",
            games_found=5,
            games_imported=5,
        )

        r = client.post("/api/management/imports", json={
            "platform": "chess.com",
            "username": "testuser",
            "max_games": 10,
        })
        assert r.status_code == 202
        data = r.json()
        assert "job" in data
        assert data["job"]["username"] == "testuser"
        assert data["job"]["platform"] == "chess.com"

    def test_start_import_duplicate_conflict(self, test_setup):
        client, db_path, mgr = test_setup

        with GameLibrary(db_path) as lib:
            # Create an active running job with valid lease
            lib.create_import_job(
                platform="chess.com",
                username="activeuser",
                claimed_by="other_process",
                lease_seconds=300,
            )

        r = client.post("/api/management/imports", json={
            "platform": "chess.com",
            "username": "activeuser",
        })
        assert r.status_code == 409
        assert "already running" in r.json()["detail"].lower()

    def test_start_import_invalid_input(self, test_setup):
        client, db_path, mgr = test_setup

        # Invalid platform
        r = client.post("/api/management/imports", json={
            "platform": "unknown_site",
            "username": "alice",
        })
        assert r.status_code == 400

        # Empty username
        r = client.post("/api/management/imports", json={
            "platform": "chess.com",
            "username": "   ",
        })
        assert r.status_code == 400

    def test_list_and_get_imports(self, test_setup):
        client, db_path, mgr = test_setup

        with GameLibrary(db_path) as lib:
            job1 = lib.create_import_job(platform="chess.com", username="user1")
            lib.complete_import_job(job1["job_id"])
            job2 = lib.create_import_job(platform="lichess", username="user2")

        # List all
        r = client.get("/api/management/imports")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 2
        assert len(data["jobs"]) == 2

        # Filter by status
        r = client.get("/api/management/imports?status=completed")
        assert r.status_code == 200
        assert r.json()["total"] == 1

        # Get specific
        r = client.get(f"/api/management/imports/{job1['job_id']}")
        assert r.status_code == 200
        assert r.json()["job"]["job_id"] == job1["job_id"]

        # Not found
        r = client.get("/api/management/imports/non_existent_id")
        assert r.status_code == 404

    @patch("api.import_manager.bulk_import_platform_games")
    def test_resume_import_job(self, mock_import, test_setup):
        client, db_path, mgr = test_setup
        mock_import.return_value = ImportResult(
            source="chess.com",
            username="resumable_user",
            requested_range="all available",
            games_found=2,
            games_imported=2,
        )

        with GameLibrary(db_path) as lib:
            job = lib.create_import_job(platform="chess.com", username="resumable_user")
            lib.fail_import_job(job["job_id"], "temporary error")

        r = client.post(f"/api/management/imports/{job['job_id']}/resume")
        assert r.status_code == 202
        assert r.json()["job"]["job_id"] == job["job_id"]


class TestAnalysisQueueEndpoints:
    def test_enqueue_specific_games(self, test_setup):
        client, db_path, mgr = test_setup

        with GameLibrary(db_path) as lib:
            games = lib.list_games()
            game_ids = [g.game_id for g in games]

        r = client.post("/api/management/analysis/queue", json={
            "game_ids": [game_ids[0]],
            "priority": 50,
            "depth": 14,
        })
        assert r.status_code == 202
        data = r.json()
        assert data["queued_count"] == 1
        assert len(data["job_ids"]) == 1

    def test_enqueue_all_unanalyzed(self, test_setup):
        client, db_path, mgr = test_setup

        r = client.post("/api/management/analysis/queue", json={
            "all_unanalyzed": True,
        })
        assert r.status_code == 202
        assert r.json()["queued_count"] == 2

    def test_list_queue_cancel_and_retry(self, test_setup):
        client, db_path, mgr = test_setup

        # Enqueue all
        client.post("/api/management/analysis/queue", json={"all_unanalyzed": True})

        # List queue
        r = client.get("/api/management/analysis/queue")
        assert r.status_code == 200
        data = r.json()
        assert len(data["jobs"]) == 2
        assert data["summary"]["PENDING"] == 2

        job_id = data["jobs"][0]["job_id"]

        # Cancel job
        r_cancel = client.post(f"/api/management/analysis/queue/{job_id}/cancel")
        assert r_cancel.status_code == 200
        assert r_cancel.json()["job"]["status"] == "CANCELLED"

        # Retry job
        r_retry = client.post(f"/api/management/analysis/queue/{job_id}/retry")
        assert r_retry.status_code == 200
        assert r_retry.json()["job"]["status"] == "PENDING"

    def test_retry_all_failed(self, test_setup):
        client, db_path, mgr = test_setup

        with GameLibrary(db_path) as lib:
            games = lib.list_games()
            game_ids = [g.game_id for g in games]
            queue = AnalysisQueue(lib)
            batch = queue.enqueue_batch(game_ids)
            job_ids = [j["job_id"] for j in batch["jobs"]]
            with lib._get_connection() as conn:
                for jid in job_ids:
                    conn.execute("UPDATE analysis_jobs SET status = 'FAILED' WHERE job_id = ?", (jid,))

        r = client.post("/api/management/analysis/queue/retry-all")
        assert r.status_code == 200
        assert r.json()["retried_count"] == 2


class TestWorkerStatusEndpoints:
    def test_worker_status_progression(self, test_setup):
        client, db_path, mgr = test_setup

        # 1. Offline (no heartbeats)
        r = client.get("/api/management/workers")
        assert r.status_code == 200
        assert r.json()["status"] == "offline"

        # 2. Online/idle (fresh heartbeat)
        with GameLibrary(db_path) as lib:
            lib.record_worker_heartbeat("worker_1", current_job_id=None, pid=1234, hostname="local")

        r = client.get("/api/management/workers")
        assert r.json()["status"] == "online/idle"
        assert r.json()["active_worker_count"] == 1

        # 3. Online/analyzing (worker has active job)
        with GameLibrary(db_path) as lib:
            lib.record_worker_heartbeat("worker_1", current_job_id="job_active_123", pid=1234, hostname="local")

        r = client.get("/api/management/workers")
        assert r.json()["status"] == "online/analyzing"


class TestSecurityValidation:
    def test_foreign_host_rejected_for_mutations(self, test_setup):
        client, db_path, mgr = test_setup

        r = client.post(
            "/api/management/imports",
            json={"platform": "chess.com", "username": "alice"},
            headers={"Host": "malicious.com"},
        )
        assert r.status_code == 403
        assert "forbidden" in r.json()["detail"].lower()

    def test_foreign_origin_rejected_for_mutations(self, test_setup):
        client, db_path, mgr = test_setup

        r = client.post(
            "/api/management/imports",
            json={"platform": "chess.com", "username": "alice"},
            headers={"Origin": "https://evil-site.com"},
        )
        assert r.status_code == 403
        assert "forbidden" in r.json()["detail"].lower()
