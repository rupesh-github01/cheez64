"""
Background Stockfish Analysis Worker.

Processes queued chess games using the existing Stockfish analysis pipeline.
Features:
- Atomic job claiming with unique per-attempt claim fencing tokens
- Independent background heartbeat thread active throughout analysis AND persistence
- Explicit lease loss detection (never persists results after lease loss)
- Bounded graceful shutdown with active engine termination on timeout
- Clean separation of retryable vs permanent errors
- Thread-safe SQLite connection handling
"""

from datetime import datetime, timezone
import logging
import os
import shutil
import signal
import socket
import sys
import threading
import time
from typing import Optional, Dict, Any, Tuple

import chess
import chess.pgn
import chess.engine

from .library import GameLibrary, DEFAULT_DB_PATH
from .analyzer import analyze_game, STOCKFISH_PATH
from .export import serialize_analysis, SCHEMA_VERSION
from .analysis_queue import (
    AnalysisQueue,
    AnalysisConfig,
    JOB_PENDING,
    JOB_RUNNING,
    JOB_COMPLETED,
    JOB_FAILED,
    StaleLeaseError,
    QueueError,
)

logger = logging.getLogger("analysis_worker")


class HeartbeatRunner:
    """
    Background heartbeat thread that extends the lease of an actively claimed job.
    Uses an independent SQLite connection to avoid thread-sharing issues.
    """

    def __init__(
        self,
        db_path: str,
        job_id: str,
        claim_token: str,
        interval_seconds: float = 60.0,
        lease_extension_seconds: int = 300,
        worker_id: Optional[str] = None,
    ):
        self.db_path = db_path
        self.job_id = job_id
        self.claim_token = claim_token
        self.interval = interval_seconds
        self.lease_extension = lease_extension_seconds
        self.worker_id = worker_id

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.lease_lost = False
        self.heartbeat_error: Optional[Exception] = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run,
            name=f"heartbeat-{self.job_id[:12]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        # Open independent connection for this thread
        try:
            lib = GameLibrary(self.db_path)
            queue = AnalysisQueue(lib)
        except Exception as e:
            self.heartbeat_error = e
            logger.error(f"Failed to initialize heartbeat connection for job {self.job_id}: {e}")
            return

        try:
            while not self._stop_event.wait(timeout=self.interval):
                try:
                    queue.heartbeat(
                        self.job_id,
                        self.claim_token,
                        extend_seconds=self.lease_extension,
                    )
                    logger.debug(f"Heartbeat extended for job {self.job_id}")
                    if self.worker_id:
                        try:
                            lib.record_worker_heartbeat(
                                worker_id=self.worker_id,
                                current_job_id=self.job_id,
                                pid=os.getpid(),
                                hostname=socket.gethostname(),
                            )
                        except Exception:
                            pass
                except StaleLeaseError as e:
                    self.lease_lost = True
                    self.heartbeat_error = e
                    logger.warning(f"Lease lost for job {self.job_id} during heartbeat: {e}")
                    break
                except Exception as e:
                    self.heartbeat_error = e
                    logger.error(f"Unexpected error pulsing heartbeat for job {self.job_id}: {e}")
        finally:
            try:
                lib.close()
            except Exception:
                pass


class AnalysisWorker:
    """
    Persistent analysis worker that claims jobs and runs Stockfish evaluations.
    """

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        worker_id: Optional[str] = None,
        stockfish_path: Optional[str] = None,
        poll_interval: float = 3.0,
        lease_seconds: int = 300,
        heartbeat_interval: float = 60.0,
        grace_period_seconds: float = 15.0,
    ):
        self.db_path = db_path
        self.worker_id = worker_id or f"worker_{os.getpid()}_{int(time.time())}"
        self.stockfish_path = stockfish_path or os.environ.get("STOCKFISH_PATH") or STOCKFISH_PATH
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.heartbeat_interval = heartbeat_interval
        self.grace_period_seconds = grace_period_seconds

        self._shutdown_requested = threading.Event()
        self._active_job_id: Optional[str] = None
        self._active_claim_token: Optional[str] = None
        self._active_engine_holder: Dict[str, Any] = {"engine": None}

        # Track runtime metrics
        self.jobs_processed = 0
        self.jobs_succeeded = 0
        self.jobs_failed = 0

    def request_shutdown(self) -> None:
        """Signal worker to stop accepting new jobs and gracefully finish."""
        logger.info(f"Shutdown requested for {self.worker_id}...")
        self._shutdown_requested.set()

    def run(self, max_jobs: Optional[int] = None, run_once: bool = False) -> None:
        """
        Main worker execution loop.
        """
        # Register signal handlers if in main thread
        try:
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGINT, lambda s, f: self.request_shutdown())
                signal.signal(signal.SIGTERM, lambda s, f: self.request_shutdown())
        except (ValueError, AttributeError):
            pass

        logger.info(f"Starting AnalysisWorker '{self.worker_id}' against {self.db_path}...")
        with GameLibrary(self.db_path) as lib:
            queue = AnalysisQueue(lib)

            # Recover any expired leases on startup
            try:
                recovery = queue.recover_expired_leases()
                if recovery["requeued"] or recovery["failed"]:
                    logger.info(
                        f"Startup recovery: requeued {recovery['requeued']}, failed {recovery['failed']} expired jobs"
                    )
            except Exception as e:
                logger.warning(f"Error during initial lease recovery: {e}")

            consecutive_empty = 0
            processed_this_run = 0

            try:
                # Initial worker heartbeat
                try:
                    lib.record_worker_heartbeat(
                        self.worker_id,
                        current_job_id=None,
                        pid=os.getpid(),
                        hostname=socket.gethostname(),
                    )
                except Exception:
                    pass

                while not self._shutdown_requested.is_set():
                    if max_jobs is not None and processed_this_run >= max_jobs:
                        logger.info(f"Reached max_jobs limit ({max_jobs}). Stopping.")
                        break

                    # Periodic lease recovery (every ~10 empty cycles)
                    if consecutive_empty > 0 and consecutive_empty % 10 == 0:
                        try:
                            queue.recover_expired_leases()
                        except Exception:
                            pass

                    # Pulse worker heartbeat while idle
                    try:
                        lib.record_worker_heartbeat(
                            self.worker_id,
                            current_job_id=None,
                            pid=os.getpid(),
                            hostname=socket.gethostname(),
                        )
                    except Exception:
                        pass

                    # Claim next job
                    try:
                        job = queue.claim_next_job(
                            self.worker_id,
                            lease_seconds=self.lease_seconds,
                        )
                    except Exception as e:
                        logger.error(f"Error claiming job from queue: {e}")
                        time.sleep(self.poll_interval)
                        continue

                    if not job:
                        consecutive_empty += 1
                        if run_once:
                            logger.info("No pending jobs remaining and run_once=True. Exiting.")
                            break
                        # Wait for next poll
                        self._shutdown_requested.wait(timeout=self.poll_interval)
                        continue

                    consecutive_empty = 0
                    self._active_job_id = job["job_id"]
                    self._active_claim_token = job["claim_token"]

                    # Pulse worker heartbeat with claimed job
                    try:
                        lib.record_worker_heartbeat(
                            self.worker_id,
                            current_job_id=job["job_id"],
                            pid=os.getpid(),
                            hostname=socket.gethostname(),
                        )
                    except Exception:
                        pass

                    logger.info(
                        f"[CLAIMED] job_id={job['job_id']} game_id={job['game_id']} "
                        f"attempt={job['attempt_count']}/{job['max_attempts']} priority={job['priority']}"
                    )

                    # Process the claimed job
                    success = self._process_claimed_job(job, queue, lib)
                    self.jobs_processed += 1
                    processed_this_run += 1
                    if success:
                        self.jobs_succeeded += 1
                    else:
                        self.jobs_failed += 1

                    self._active_job_id = None
                    self._active_claim_token = None
            finally:
                # Clear worker heartbeat on shutdown
                try:
                    lib.clear_worker_heartbeat(self.worker_id)
                except Exception:
                    pass

        logger.info(
            f"Worker '{self.worker_id}' finished. Processed: {self.jobs_processed} "
            f"(Succeeded: {self.jobs_succeeded}, Failed: {self.jobs_failed})"
        )

    def _process_claimed_job(
        self,
        job: Dict[str, Any],
        queue: AnalysisQueue,
        lib: GameLibrary,
    ) -> bool:
        """
        Execute analysis for a claimed job while running background heartbeats.
        Ensures heartbeats stay active throughout analysis AND persistence.
        """
        job_id = job["job_id"]
        game_id = job["game_id"]
        claim_token = job["claim_token"]
        start_time = time.time()

        # Start heartbeat runner with independent DB connection
        heartbeat_runner = HeartbeatRunner(
            db_path=self.db_path,
            job_id=job_id,
            claim_token=claim_token,
            interval_seconds=self.heartbeat_interval,
            lease_extension_seconds=self.lease_seconds,
            worker_id=self.worker_id,
        )
        heartbeat_runner.start()

        analysis_success = False

        try:
            # 1. Fetch canonical game
            game = lib.get_game(game_id)
            if not game:
                raise ValueError(f"Game '{game_id}' not found in GameLibrary.")

            if not game.pgn or not game.pgn.strip():
                raise ValueError(f"Game '{game_id}' has empty PGN content.")

            # 2. Parse AnalysisConfig
            config_data = {}
            if job.get("config_json"):
                import json
                try:
                    config_data = json.loads(job["config_json"])
                except Exception:
                    pass
            config = AnalysisConfig.from_dict(config_data)

            # 3. Check for shutdown request before starting heavy computation
            if self._shutdown_requested.is_set():
                logger.warning(f"Shutdown requested before starting analysis for job {job_id}.")
                return False

            # 4. Check lease loss before starting analysis
            if heartbeat_runner.lease_lost:
                logger.warning(f"Lease lost for job {job_id} before analysis started. Aborting.")
                return False

            # 5. Run analysis pipeline
            logger.info(
                f"[ANALYZING] job_id={job_id} depth={config.depth} multipv={config.multipv} "
                f"threads={config.threads} hash={config.hash_size}MB"
            )

            # Spawn shutdown watchdog thread to enforce graceful shutdown deadline
            watchdog_stop = threading.Event()
            watchdog_thread = threading.Thread(
                target=self._enforce_shutdown_timeout,
                args=(watchdog_stop, self.grace_period_seconds),
                daemon=True,
            )
            watchdog_thread.start()

            try:
                game_obj, analyses = analyze_game(
                    stockfish_path=self.stockfish_path,
                    depth=config.depth,
                    multipv=config.multipv,
                    pgn_text=game.pgn,
                    threads=config.threads,
                    hash_size=config.hash_size,
                    time_limit=config.time_limit,
                    active_engine_holder=self._active_engine_holder,
                )
            finally:
                watchdog_stop.set()

            if game_obj is None or not analyses:
                raise ValueError("Analysis pipeline returned no game or move evaluations.")

            # 6. Verify lease has not been lost before persisting
            if heartbeat_runner.lease_lost:
                logger.warning(
                    f"[LEASE LOST] Lease for job {job_id} was lost during analysis. Refusing to persist results."
                )
                return False

            # 7. Serialize analysis results into canonical format
            analysis_dict = serialize_analysis(game_obj, analyses)

            # Record engine details in metadata
            analysis_dict["metadata"]["stockfish_path"] = str(self.stockfish_path)
            analysis_dict["metadata"]["engine_depth"] = config.depth
            analysis_dict["metadata"]["multipv"] = config.multipv

            # 8. Atomic completion via lease-validated queue method
            queue.complete_job_with_analysis(
                job_id=job_id,
                claim_token=claim_token,
                analysis_data=analysis_dict,
                schema_version=config.schema_version,
                engine_depth=config.depth,
            )

            elapsed = time.time() - start_time
            crit_count = analysis_dict.get("summary", {}).get("critical_positions_count", 0)
            ep_count = analysis_dict.get("summary", {}).get("critical_episodes_count", 0)
            logger.info(
                f"[COMPLETED] job_id={job_id} in {elapsed:.2f}s "
                f"(critical: {crit_count}, episodes: {ep_count})"
            )
            analysis_success = True
            return True

        except StaleLeaseError as e:
            # Lease was revoked or expired; do not overwrite or attempt fail_job
            logger.warning(f"[LEASE LOST] Job {job_id} lease expired or stolen: {e}")
            return False

        except Exception as e:
            elapsed = time.time() - start_time
            err_msg = str(e)
            retryable = self._is_retryable_error(e)

            # If shutdown was the cause, do not fail permanently
            if self._shutdown_requested.is_set():
                logger.info(f"Job {job_id} interrupted by shutdown ({err_msg}). Reverting to PENDING.")
                retryable = True

            logger.error(
                f"[FAILED] job_id={job_id} error='{err_msg}' "
                f"retryable={retryable} (elapsed {elapsed:.2f}s)"
            )

            # Safely report failure to queue if lease is still active
            if not heartbeat_runner.lease_lost:
                try:
                    queue.fail_job(
                        job_id=job_id,
                        claim_token=claim_token,
                        error_summary=err_msg,
                        retryable=retryable,
                    )
                except StaleLeaseError:
                    logger.warning(f"Could not fail job {job_id}: lease was lost.")
                except Exception as fail_err:
                    logger.error(f"Error calling fail_job for {job_id}: {fail_err}")

            return False

        finally:
            # Stop heartbeat only after persistence or failure handling is fully completed (Safeguard #1)
            heartbeat_runner.stop()

    def _is_retryable_error(self, err: Exception) -> bool:
        """
        Classify whether an exception during analysis is retryable.
        """
        # Permanent errors:
        # - Missing game in DB
        # - Empty or corrupt PGN
        # - Missing Stockfish binary
        if isinstance(err, (ValueError, FileNotFoundError)):
            return False

        msg = str(err).lower()
        if "not found" in msg or "no such file" in msg:
            return False
        if "pgn" in msg and ("empty" in msg or "invalid" in msg or "corrupt" in msg):
            return False

        # Retryable errors:
        # - Engine crashes or pipe disconnects
        # - Engine timeouts
        # - Transient SQLite errors
        return True

    def _enforce_shutdown_timeout(self, stop_event: threading.Event, timeout: float) -> None:
        """
        Monitor shutdown signal during active analysis. If shutdown is requested and
        the grace period elapses, terminate the running Stockfish engine process cleanly.
        """
        poll = 0.5
        waited = 0.0

        while not stop_event.is_set():
            if self._shutdown_requested.is_set():
                waited += poll
                if waited >= timeout:
                    logger.warning(
                        f"Graceful shutdown timeout ({timeout}s) exceeded during active analysis. "
                        "Terminating Stockfish engine process..."
                    )
                    eng = self._active_engine_holder.get("engine")
                    if eng:
                        try:
                            eng.close()
                        except Exception:
                            pass
                    break
            time.sleep(poll)
