"""
Background Import Manager with SQLite Lease-Based Ownership.

Coordinates background execution of platform imports (Chess.com / Lichess)
while ensuring single-import concurrency, durable SQLite leases, periodic
lease heartbeats, and startup recovery of stale imports.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import logging
import os
import threading
import time
from typing import Optional, Dict, Any
import uuid

from src.library import GameLibrary, DEFAULT_DB_PATH
from src.importers import bulk_import_platform_games, ImporterError

logger = logging.getLogger("import_manager")


class ImportAlreadyRunningError(Exception):
    """Raised when an import for the account or job is already actively running."""
    pass


class ImportNotFoundError(Exception):
    """Raised when the requested import job cannot be found."""
    pass


class ImportLeaseHeartbeat:
    """Background thread that keeps an import job's lease alive."""

    def __init__(
        self,
        db_path: str,
        job_id: str,
        claimed_by: str,
        interval_seconds: float = 25.0,
        lease_seconds: int = 120,
    ):
        self.db_path = db_path
        self.job_id = job_id
        self.claimed_by = claimed_by
        self.interval = interval_seconds
        self.lease_seconds = lease_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"import-heartbeat-{self.job_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        try:
            lib = GameLibrary(self.db_path)
        except Exception as e:
            logger.error(f"Failed to open DB for import heartbeat: {e}")
            return

        try:
            while not self._stop_event.wait(timeout=self.interval):
                try:
                    success = lib.heartbeat_import_job(
                        self.job_id,
                        self.claimed_by,
                        lease_seconds=self.lease_seconds,
                    )
                    if not success:
                        logger.warning(f"Import heartbeat failed for job {self.job_id} (lease lost)")
                        break
                except Exception as e:
                    logger.error(f"Error extending import lease for job {self.job_id}: {e}")
        finally:
            try:
                lib.close()
            except Exception:
                pass


class ImportManager:
    """
    Manages background import executions with exclusive SQLite leases.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.manager_id = f"impmgr_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="import-worker")
        self._active_job_id: Optional[str] = None
        self._lock = threading.Lock()

    def recover_stale_imports(self, stale_threshold_seconds: int = 180) -> int:
        """Recover any stale import jobs with expired leases on startup."""
        with GameLibrary(self.db_path) as lib:
            recovered = lib.recover_stale_import_jobs(stale_threshold_seconds)
            if recovered > 0:
                logger.info(f"Recovered {recovered} stale import jobs on startup")
            return recovered

    def start_import(
        self,
        platform: str,
        username: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
        max_games: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create and start a new background import job.
        Raises ImportAlreadyRunningError if an import is already running for this account.
        """
        norm_platform = platform.strip().lower()
        norm_username = username.strip()

        if norm_platform not in ("chess.com", "chesscom", "lichess", "lichess.org"):
            raise ValueError(f"Unsupported platform '{platform}'. Use 'chess.com' or 'lichess'.")

        if not norm_username:
            raise ValueError("Username cannot be empty.")

        with self._lock:
            with GameLibrary(self.db_path) as lib:
                # 1. Check if an active import exists for this account
                active = lib.find_active_import_for_account(norm_platform, norm_username)
                if active:
                    raise ImportAlreadyRunningError(
                        f"An active import is already running for {norm_platform}/{norm_username} "
                        f"(job {active['job_id']})"
                    )

                # 2. Check for resumable job or create new
                resumable = lib.find_resumable_import_job(norm_platform, norm_username)
                if resumable:
                    job_id = resumable["job_id"]
                    claimed = lib.claim_import_job(job_id, self.manager_id, lease_seconds=120)
                    if not claimed:
                        raise ImportAlreadyRunningError(
                            f"Cannot claim resumable import job {job_id} (lease collision)"
                        )
                    job = lib.get_import_job(job_id)
                else:
                    job = lib.create_import_job(
                        platform=norm_platform,
                        username=norm_username,
                        date_from=since,
                        date_to=until,
                        claimed_by=self.manager_id,
                        lease_seconds=120,
                    )
                    job_id = job["job_id"]

            # 3. Submit execution to background executor
            self._active_job_id = job_id
            self._executor.submit(
                self._run_import,
                job_id,
                norm_platform,
                norm_username,
                since,
                until,
                max_games,
            )

            return job

    def resume_import(self, job_id: str) -> Dict[str, Any]:
        """
        Resume an existing import job from its last checkpoint.
        """
        with self._lock:
            with GameLibrary(self.db_path) as lib:
                job = lib.get_import_job(job_id)
                if not job:
                    raise ImportNotFoundError(f"Import job '{job_id}' not found.")

                if job["status"] == "completed":
                    raise ValueError(f"Import job '{job_id}' is already completed.")

                # Check if actively leased by another live process
                if job["status"] == "running" and job.get("lease_expires_at"):
                    now_iso = datetime.now(timezone.utc).isoformat()
                    if job["lease_expires_at"] > now_iso and job.get("claimed_by") != self.manager_id:
                        raise ImportAlreadyRunningError(
                            f"Import job '{job_id}' is already running under process {job.get('claimed_by')}"
                        )

                # Claim the job
                claimed = lib.claim_import_job(job_id, self.manager_id, lease_seconds=120)
                if not claimed:
                    raise ImportAlreadyRunningError(f"Could not claim import job '{job_id}'")

                updated_job = lib.get_import_job(job_id)

            # Submit execution to background executor
            self._active_job_id = job_id
            self._executor.submit(
                self._run_import,
                job_id,
                job["platform"],
                job["username"],
                job.get("date_from"),
                job.get("date_to"),
                None,
            )

            return updated_job

    def get_active_import_job(self) -> Optional[Dict[str, Any]]:
        """Get the currently executing import job if any."""
        if not self._active_job_id:
            return None
        with GameLibrary(self.db_path) as lib:
            return lib.get_import_job(self._active_job_id)

    def _run_import(
        self,
        job_id: str,
        platform: str,
        username: str,
        since: Optional[str],
        until: Optional[str],
        max_games: Optional[int],
    ) -> None:
        """Background execution worker for an import job."""
        heartbeat = ImportLeaseHeartbeat(
            db_path=self.db_path,
            job_id=job_id,
            claimed_by=self.manager_id,
            interval_seconds=25.0,
            lease_seconds=120,
        )
        heartbeat.start()

        try:
            logger.info(f"Starting background import for job {job_id} ({platform}/{username})...")
            with GameLibrary(self.db_path) as lib:
                result = bulk_import_platform_games(
                    source=platform,
                    username=username,
                    library=lib,
                    since=since,
                    until=until,
                    max_games=max_games,
                    job_id=job_id,
                )
                logger.info(
                    f"Background import completed for job {job_id}: {result.games_imported} imported, "
                    f"{result.duplicates_skipped} dupes, {result.invalid_games} failed"
                )
        except Exception as e:
            logger.error(f"Background import failed for job {job_id}: {e}")
            try:
                with GameLibrary(self.db_path) as lib:
                    lib.fail_import_job(job_id, str(e))
            except Exception:
                pass
        finally:
            heartbeat.stop()
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
                try:
                    with GameLibrary(self.db_path) as lib:
                        lib.release_import_job(job_id, self.manager_id)
                except Exception:
                    pass

    def shutdown(self) -> None:
        """Shutdown the executor gracefully."""
        self._executor.shutdown(wait=False)
