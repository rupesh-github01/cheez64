"""
Analysis Job Queue & Lifecycle Management.

Provides a persistent SQLite-backed job queue for scheduling chess games for
Stockfish analysis, with lease-based crash recovery, partial unique constraint
deduplication, unique per-attempt claim fencing tokens, and atomic completion.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import hashlib
import json
import sqlite3
from typing import Optional, List, Dict, Any, Tuple
import uuid

from .library import (
    GameLibrary,
    ANALYSIS_NOT_ANALYZED,
    ANALYSIS_ANALYZING,
    ANALYSIS_ANALYZED,
    ANALYSIS_FAILED,
)

# Queue status constants
JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_COMPLETED = "COMPLETED"
JOB_FAILED = "FAILED"
JOB_CANCELLED = "CANCELLED"

VALID_JOB_STATUSES = {
    JOB_PENDING,
    JOB_RUNNING,
    JOB_COMPLETED,
    JOB_FAILED,
    JOB_CANCELLED,
}


class QueueError(Exception):
    """Base exception for analysis queue operations."""
    pass


class JobNotFoundError(QueueError):
    """Raised when an analysis job ID is not found."""
    pass


class InvalidStateTransitionError(QueueError):
    """Raised when an illegal lifecycle status transition is attempted."""
    pass


class StaleLeaseError(QueueError):
    """Raised when an operation is attempted with an expired or invalid claim token."""
    pass


class AlreadyAnalyzedError(QueueError):
    """Raised when scheduling an already-analyzed game without force_reanalyze=True."""
    pass


class ConfigValidationError(QueueError):
    """Raised when analysis configuration parameters are invalid."""
    pass


@dataclass(frozen=True)
class AnalysisConfig:
    """
    Immutable, reproducible Stockfish analysis configuration.
    """
    depth: int = 16
    multipv: int = 3
    threads: int = 1
    hash_size: int = 64  # in megabytes
    time_limit: Optional[float] = None
    schema_version: str = "1.1.0"

    def __post_init__(self):
        if self.depth < 1:
            raise ConfigValidationError(f"Engine depth must be >= 1, got {self.depth}")
        if self.multipv < 1:
            raise ConfigValidationError(f"MultiPV must be >= 1, got {self.multipv}")
        if self.threads < 1:
            raise ConfigValidationError(f"Threads must be >= 1, got {self.threads}")
        if self.hash_size < 1:
            raise ConfigValidationError(f"Hash size must be >= 1 MB, got {self.hash_size}")
        if self.time_limit is not None and self.time_limit <= 0:
            raise ConfigValidationError(f"Time limit must be > 0, got {self.time_limit}")
        if not self.schema_version:
            raise ConfigValidationError("schema_version cannot be empty")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        """Produce deterministic sorted JSON string for hashing."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def config_hash(self) -> str:
        """16-char deterministic SHA-256 fingerprint of the configuration."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnalysisConfig":
        valid_keys = {"depth", "multipv", "threads", "hash_size", "time_limit", "schema_version"}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class AnalysisQueue:
    """
    Persistent SQLite-backed job queue for Stockfish game analysis.
    """

    def __init__(self, library: GameLibrary):
        self.library = library

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # -------------------------------------------------------------------------
    # Enqueue Operations
    # -------------------------------------------------------------------------

    def enqueue(
        self,
        game_id: str,
        config: Optional[AnalysisConfig] = None,
        priority: int = 0,
        batch_id: Optional[str] = None,
        max_attempts: int = 3,
        force_reanalyze: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """
        Enqueue a game for analysis.

        Returns (job_dict, is_new).
        If game does not exist, raises ValueError.
        If already analyzed and not force_reanalyze, raises AlreadyAnalyzedError.
        If an active job already exists with matching config_hash, returns (existing_job, False).
        """
        cfg = config or AnalysisConfig()
        cfg_hash = cfg.config_hash()
        cfg_json = cfg.canonical_json()

        game = self.library.get_game(game_id)
        if game is None:
            raise ValueError(f"Game '{game_id}' not found in library.")

        # Check existing analysis
        if game.analysis_status == ANALYSIS_ANALYZED and not force_reanalyze:
            raise AlreadyAnalyzedError(
                f"Game '{game_id}' is already analyzed. Pass force_reanalyze=True to schedule reanalysis."
            )

        job_id = f"job_an_{uuid.uuid4().hex[:12]}"
        now = self._now_iso()

        with self.library._get_connection() as conn:
            try:
                conn.execute("""
                    INSERT INTO analysis_jobs (
                        job_id, game_id, batch_id, priority, status,
                        config_hash, config_json, attempt_count, max_attempts,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """, (
                    job_id, game_id, batch_id, priority, JOB_PENDING,
                    cfg_hash, cfg_json, max_attempts, now, now
                ))
            except sqlite3.IntegrityError:
                # Active job already exists (enforced by idx_analysis_jobs_active_unique)
                cursor = conn.execute("""
                    SELECT * FROM analysis_jobs
                    WHERE game_id = ? AND config_hash = ? AND status IN ('PENDING', 'RUNNING')
                    LIMIT 1
                """, (game_id, cfg_hash))
                row = cursor.fetchone()
                if row:
                    return dict(row), False
                raise

        return self.get_job(job_id), True

    def enqueue_batch(
        self,
        game_ids: List[str],
        config: Optional[AnalysisConfig] = None,
        priority: int = 0,
        batch_id: Optional[str] = None,
        max_attempts: int = 3,
        force_reanalyze: bool = False,
    ) -> Dict[str, Any]:
        """
        Enqueue a batch of games in a single transaction.
        """
        cfg = config or AnalysisConfig()
        cfg_hash = cfg.config_hash()
        cfg_json = cfg.canonical_json()
        effective_batch_id = batch_id or f"batch_{uuid.uuid4().hex[:8]}"

        enqueued_count = 0
        skipped_analyzed = 0
        skipped_active = 0
        created_jobs: List[Dict[str, Any]] = []

        now = self._now_iso()

        with self.library._get_connection() as conn:
            for gid in game_ids:
                # Check game exists and analysis status
                cursor = conn.execute(
                    "SELECT game_id, analysis_status FROM games WHERE game_id = ?",
                    (gid,)
                )
                grow = cursor.fetchone()
                if not grow:
                    continue

                if grow["analysis_status"] == ANALYSIS_ANALYZED and not force_reanalyze:
                    skipped_analyzed += 1
                    continue

                job_id = f"job_an_{uuid.uuid4().hex[:12]}"
                try:
                    conn.execute("""
                        INSERT INTO analysis_jobs (
                            job_id, game_id, batch_id, priority, status,
                            config_hash, config_json, attempt_count, max_attempts,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                    """, (
                        job_id, gid, effective_batch_id, priority, JOB_PENDING,
                        cfg_hash, cfg_json, max_attempts, now, now
                    ))
                    enqueued_count += 1
                    created_jobs.append({
                        "job_id": job_id,
                        "game_id": gid,
                        "batch_id": effective_batch_id,
                        "status": JOB_PENDING,
                        "priority": priority,
                    })
                except sqlite3.IntegrityError:
                    skipped_active += 1

        return {
            "batch_id": effective_batch_id,
            "enqueued": enqueued_count,
            "skipped_already_analyzed": skipped_analyzed,
            "skipped_active_job": skipped_active,
            "total_requested": len(game_ids),
            "jobs": created_jobs,
        }

    def enqueue_all_unanalyzed(
        self,
        config: Optional[AnalysisConfig] = None,
        priority: int = 0,
        batch_id: Optional[str] = None,
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        """
        Find all games in the library with analysis_status != 'ANALYZED' and enqueue them.
        """
        with self.library._get_connection() as conn:
            cursor = conn.execute(
                "SELECT game_id FROM games WHERE analysis_status != ?",
                (ANALYSIS_ANALYZED,)
            )
            rows = cursor.fetchall()
            game_ids = [r["game_id"] for r in rows]

        return self.enqueue_batch(
            game_ids=game_ids,
            config=config,
            priority=priority,
            batch_id=batch_id,
            max_attempts=max_attempts,
            force_reanalyze=False,
        )

    # -------------------------------------------------------------------------
    # Worker Claim & Lease Management
    # -------------------------------------------------------------------------

    def claim_next_job(
        self,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> Optional[Dict[str, Any]]:
        """
        Atomically claim the highest-priority, oldest pending analysis job.

        Assigns a unique claim_token (fencing token per claim attempt) and lease expiration.
        Updates game-level status to ANALYZING only if game is not already ANALYZED.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        lease_expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        claim_token = f"{worker_id}:{uuid.uuid4().hex[:12]}"

        with self.library._get_connection() as conn:
            # Atomic claim query using subquery
            cursor = conn.execute("""
                SELECT job_id, game_id, attempt_count FROM analysis_jobs
                WHERE status = 'PENDING'
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            """)
            row = cursor.fetchone()
            if not row:
                return None

            job_id = row["job_id"]
            game_id = row["game_id"]
            new_attempts = row["attempt_count"] + 1

            # Update analysis job record
            cursor = conn.execute("""
                UPDATE analysis_jobs
                SET status = 'RUNNING',
                    claimed_by = ?,
                    claim_token = ?,
                    lease_expires_at = ?,
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?,
                    attempt_count = ?
                WHERE job_id = ? AND status = 'PENDING'
            """, (
                worker_id, claim_token, lease_expires,
                now_iso, now_iso, new_attempts, job_id
            ))

            if cursor.rowcount == 0:
                # Concurrent worker won the race
                return None

            # Only transition game status to ANALYZING if not already ANALYZED (Safeguard #2)
            conn.execute("""
                UPDATE games
                SET analysis_status = 'ANALYZING'
                WHERE game_id = ? AND analysis_status != 'ANALYZED'
            """, (game_id,))

        return self.get_job(job_id)

    def heartbeat(
        self,
        job_id: str,
        claim_token: str,
        extend_seconds: int = 300,
    ) -> None:
        """
        Extend the lease of an actively running job using its fencing claim token.
        Raises StaleLeaseError if token doesn't match or lease is already expired.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        new_lease = (now + timedelta(seconds=extend_seconds)).isoformat()

        with self.library._get_connection() as conn:
            cursor = conn.execute("""
                SELECT status, claim_token, lease_expires_at FROM analysis_jobs
                WHERE job_id = ?
            """, (job_id,))
            row = cursor.fetchone()
            if not row:
                raise JobNotFoundError(f"Job '{job_id}' not found.")

            if row["status"] != JOB_RUNNING or row["claim_token"] != claim_token:
                raise StaleLeaseError(
                    f"Job '{job_id}' is not actively claimed by token '{claim_token}'."
                )

            if row["lease_expires_at"] and row["lease_expires_at"] < now_iso:
                raise StaleLeaseError(
                    f"Lease for job '{job_id}' has already expired at {row['lease_expires_at']}."
                )

            conn.execute("""
                UPDATE analysis_jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND claim_token = ? AND status = 'RUNNING'
            """, (new_lease, now_iso, job_id, claim_token))

    # -------------------------------------------------------------------------
    # Atomic Completion Interface (Segment 11C Contract)
    # -------------------------------------------------------------------------

    def complete_job_with_analysis(
        self,
        job_id: str,
        claim_token: str,
        analysis_data: Dict[str, Any],
        file_path: Optional[str] = None,
        schema_version: Optional[str] = None,
        engine_depth: Optional[int] = None,
    ) -> None:
        """
        Atomic completion operation for Segment 11C.
        Verifies active lease and claim token, persists analysis data, marks job COMPLETED,
        and sets games.analysis_status = ANALYZED in a single transaction.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        with self.library._get_connection() as conn:
            cursor = conn.execute("""
                SELECT job_id, game_id, status, claim_token, lease_expires_at, config_json
                FROM analysis_jobs
                WHERE job_id = ?
            """, (job_id,))
            row = cursor.fetchone()
            if not row:
                raise JobNotFoundError(f"Job '{job_id}' not found.")

            if row["status"] != JOB_RUNNING or row["claim_token"] != claim_token:
                raise StaleLeaseError(
                    f"Completion rejected: Job '{job_id}' is not in RUNNING state or claim token mismatched."
                )

            if row["lease_expires_at"] and row["lease_expires_at"] < now_iso:
                raise StaleLeaseError(
                    f"Completion rejected: Lease for job '{job_id}' expired at {row['lease_expires_at']}."
                )

            game_id = row["game_id"]
            cfg_dict = json.loads(row["config_json"])
            ver = schema_version or cfg_dict.get("schema_version", "1.1.0")
            depth = engine_depth if engine_depth is not None else cfg_dict.get("depth", 16)

            summary = analysis_data.get("summary", {})
            crit_count = summary.get("critical_positions_count")
            if crit_count is None:
                crit_count = sum(1 for m in analysis_data.get("moves", []) if m.get("is_critical"))
            ep_count = summary.get("critical_episodes_count")
            if ep_count is None:
                ep_count = len(analysis_data.get("episodes", []))

            json_str = json.dumps(analysis_data)
            ref_path = file_path or f"data/analyses/{game_id}.json"

            # 1. Insert or update analysis record
            conn.execute("""
                INSERT INTO analyses (
                    game_id, schema_version, analyzed_at, engine_depth,
                    critical_count, episodes_count, analysis_data, file_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    analyzed_at = excluded.analyzed_at,
                    engine_depth = excluded.engine_depth,
                    critical_count = excluded.critical_count,
                    episodes_count = excluded.episodes_count,
                    analysis_data = excluded.analysis_data,
                    file_path = excluded.file_path
            """, (
                game_id, ver, now_iso, depth,
                crit_count, ep_count, json_str, ref_path
            ))

            # 2. Update game record to ANALYZED
            conn.execute("""
                UPDATE games
                SET analysis_status = 'ANALYZED',
                    analyzed_at = ?,
                    analysis_reference = ?
                WHERE game_id = ?
            """, (now_iso, ref_path, game_id))

            # 3. Mark analysis job as COMPLETED
            conn.execute("""
                UPDATE analysis_jobs
                SET status = 'COMPLETED',
                    completed_at = ?,
                    updated_at = ?,
                    claim_token = NULL,
                    lease_expires_at = NULL
                WHERE job_id = ?
            """, (now_iso, now_iso, job_id))

    def complete_job(
        self,
        job_id: str,
        claim_token: str,
    ) -> None:
        """
        Mark a job COMPLETED without updating analysis records (used when analysis
        was attached separately or in tests).
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        with self.library._get_connection() as conn:
            cursor = conn.execute("""
                SELECT job_id, game_id, status, claim_token, lease_expires_at
                FROM analysis_jobs
                WHERE job_id = ?
            """, (job_id,))
            row = cursor.fetchone()
            if not row:
                raise JobNotFoundError(f"Job '{job_id}' not found.")

            if row["status"] != JOB_RUNNING or row["claim_token"] != claim_token:
                raise StaleLeaseError(
                    f"Completion rejected: Job '{job_id}' is not in RUNNING state or claim token mismatched."
                )

            if row["lease_expires_at"] and row["lease_expires_at"] < now_iso:
                raise StaleLeaseError(
                    f"Completion rejected: Lease for job '{job_id}' expired at {row['lease_expires_at']}."
                )

            game_id = row["game_id"]

            conn.execute("""
                UPDATE analysis_jobs
                SET status = 'COMPLETED',
                    completed_at = ?,
                    updated_at = ?,
                    claim_token = NULL,
                    lease_expires_at = NULL
                WHERE job_id = ?
            """, (now_iso, now_iso, job_id))

            conn.execute("""
                UPDATE games
                SET analysis_status = 'ANALYZED', analyzed_at = COALESCE(analyzed_at, ?)
                WHERE game_id = ?
            """, (now_iso, game_id))

    def fail_job(
        self,
        job_id: str,
        claim_token: str,
        error_summary: str,
        retryable: bool = True,
    ) -> None:
        """
        Mark a job failed or return it to PENDING if retries remain.
        """
        now = self._now_iso()

        with self.library._get_connection() as conn:
            cursor = conn.execute("""
                SELECT job_id, game_id, status, claim_token, attempt_count, max_attempts
                FROM analysis_jobs
                WHERE job_id = ?
            """, (job_id,))
            row = cursor.fetchone()
            if not row:
                raise JobNotFoundError(f"Job '{job_id}' not found.")

            if row["status"] != JOB_RUNNING or row["claim_token"] != claim_token:
                raise StaleLeaseError(
                    f"Fail rejected: Job '{job_id}' is not currently running with claim token '{claim_token}'."
                )

            game_id = row["game_id"]
            attempts = row["attempt_count"]
            max_att = row["max_attempts"]

            if retryable and attempts < max_att:
                # Requeue to PENDING
                conn.execute("""
                    UPDATE analysis_jobs
                    SET status = 'PENDING',
                        error_summary = ?,
                        claim_token = NULL,
                        claimed_by = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE job_id = ?
                """, (f"Attempt {attempts} failed: {error_summary}", now, job_id))

                # Revert game status only if game is not already ANALYZED
                conn.execute("""
                    UPDATE games
                    SET analysis_status = 'NOT_ANALYZED'
                    WHERE game_id = ? AND analysis_status != 'ANALYZED'
                """, (game_id,))
            else:
                # Permanently failed
                conn.execute("""
                    UPDATE analysis_jobs
                    SET status = 'FAILED',
                        error_summary = ?,
                        claim_token = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE job_id = ?
                """, (error_summary, now, job_id))

                # Only set game status to FAILED if not already ANALYZED (Safeguard #2)
                conn.execute("""
                    UPDATE games
                    SET analysis_status = 'FAILED'
                    WHERE game_id = ? AND analysis_status != 'ANALYZED'
                """, (game_id,))

    # -------------------------------------------------------------------------
    # Job Control: Cancel & Retry
    # -------------------------------------------------------------------------

    def cancel_job(self, job_id: str) -> None:
        """
        Cancel a pending or failed job. Cannot cancel RUNNING or COMPLETED jobs directly.
        """
        now = self._now_iso()

        with self.library._get_connection() as conn:
            cursor = conn.execute(
                "SELECT job_id, game_id, status FROM analysis_jobs WHERE job_id = ?",
                (job_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise JobNotFoundError(f"Job '{job_id}' not found.")

            if row["status"] == JOB_COMPLETED:
                raise InvalidStateTransitionError(f"Cannot cancel completed job '{job_id}'.")
            if row["status"] == JOB_RUNNING:
                raise InvalidStateTransitionError(f"Cannot cancel running job '{job_id}'. Let lease expire or stop worker.")

            conn.execute("""
                UPDATE analysis_jobs
                SET status = 'CANCELLED', updated_at = ?
                WHERE job_id = ?
            """, (now, job_id))

            conn.execute("""
                UPDATE games
                SET analysis_status = 'NOT_ANALYZED'
                WHERE game_id = ? AND analysis_status != 'ANALYZED'
            """, (row["game_id"],))

    def retry_job(self, job_id: str, reset_attempts: bool = True) -> None:
        """
        Retry a FAILED or CANCELLED job.
        """
        now = self._now_iso()

        with self.library._get_connection() as conn:
            cursor = conn.execute(
                "SELECT job_id, status FROM analysis_jobs WHERE job_id = ?",
                (job_id,)
            )
            row = cursor.fetchone()
            if not row:
                raise JobNotFoundError(f"Job '{job_id}' not found.")

            if row["status"] not in (JOB_FAILED, JOB_CANCELLED):
                raise InvalidStateTransitionError(
                    f"Only FAILED or CANCELLED jobs can be retried. Job is '{row['status']}'."
                )

            reset_clause = "attempt_count = 0," if reset_attempts else ""
            try:
                conn.execute(f"""
                    UPDATE analysis_jobs
                    SET status = 'PENDING',
                        {reset_clause}
                        error_summary = NULL,
                        claimed_by = NULL,
                        claim_token = NULL,
                        lease_expires_at = NULL,
                        updated_at = ?
                    WHERE job_id = ?
                """, (now, job_id))
            except sqlite3.IntegrityError as e:
                raise QueueError(
                    f"Cannot retry job '{job_id}': an active analysis job already exists for this game and configuration."
                ) from e

    def retry_all_failed(self, batch_id: Optional[str] = None) -> int:
        """
        Reset all FAILED jobs back to PENDING.
        """
        now = self._now_iso()
        where_clause = "WHERE status = 'FAILED'"
        params = [now]
        if batch_id:
            where_clause += " AND batch_id = ?"
            params.append(batch_id)

        with self.library._get_connection() as conn:
            cursor = conn.execute(f"""
                UPDATE analysis_jobs
                SET status = 'PENDING',
                    attempt_count = 0,
                    error_summary = NULL,
                    claimed_by = NULL,
                    claim_token = NULL,
                    lease_expires_at = NULL,
                    updated_at = ?
                {where_clause}
            """, params)
            return cursor.rowcount

    # -------------------------------------------------------------------------
    # Crash Recovery: Expired Leases
    # -------------------------------------------------------------------------

    def recover_expired_leases(self) -> Dict[str, int]:
        """
        Scan for RUNNING jobs with expired leases and recover them.
        Jobs with attempt_count < max_attempts are requeued to PENDING.
        Jobs that reached max_attempts are marked FAILED.
        """
        now_iso = self._now_iso()
        requeued = 0
        failed = 0

        with self.library._get_connection() as conn:
            cursor = conn.execute("""
                SELECT job_id, game_id, attempt_count, max_attempts
                FROM analysis_jobs
                WHERE status = 'RUNNING' AND lease_expires_at < ?
            """, (now_iso,))
            expired_rows = cursor.fetchall()

            for r in expired_rows:
                jid = r["job_id"]
                gid = r["game_id"]
                att = r["attempt_count"]
                max_att = r["max_attempts"]

                if att < max_att:
                    conn.execute("""
                        UPDATE analysis_jobs
                        SET status = 'PENDING',
                            claimed_by = NULL,
                            claim_token = NULL,
                            lease_expires_at = NULL,
                            error_summary = 'Lease expired; automatically recovered for retry',
                            updated_at = ?
                        WHERE job_id = ?
                    """, (now_iso, jid))
                    # Revert game status if not already ANALYZED
                    conn.execute("""
                        UPDATE games
                        SET analysis_status = 'NOT_ANALYZED'
                        WHERE game_id = ? AND analysis_status != 'ANALYZED'
                    """, (gid,))
                    requeued += 1
                else:
                    conn.execute("""
                        UPDATE analysis_jobs
                        SET status = 'FAILED',
                            claimed_by = NULL,
                            claim_token = NULL,
                            lease_expires_at = NULL,
                            error_summary = 'Lease expired and maximum attempts reached',
                            updated_at = ?
                        WHERE job_id = ?
                    """, (now_iso, jid))
                    # Set game status to FAILED only if not already ANALYZED
                    conn.execute("""
                        UPDATE games
                        SET analysis_status = 'FAILED'
                        WHERE game_id = ? AND analysis_status != 'ANALYZED'
                    """, (gid,))
                    failed += 1

        return {"requeued": requeued, "failed": failed}

    # -------------------------------------------------------------------------
    # Inspection & Querying
    # -------------------------------------------------------------------------

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a job by its job_id."""
        with self.library._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM analysis_jobs WHERE job_id = ?",
                (job_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_job_for_game(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Find an active (PENDING or RUNNING) job for a game."""
        with self.library._get_connection() as conn:
            cursor = conn.execute("""
                SELECT * FROM analysis_jobs
                WHERE game_id = ? AND status IN ('PENDING', 'RUNNING')
                ORDER BY created_at DESC
                LIMIT 1
            """, (game_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_jobs(
        self,
        status: Optional[str] = None,
        batch_id: Optional[str] = None,
        game_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        List jobs with filtering and pagination.
        """
        query = "SELECT * FROM analysis_jobs WHERE 1=1"
        params: List[Any] = []

        if status:
            query += " AND status = ?"
            params.append(status.upper())
        if batch_id:
            query += " AND batch_id = ?"
            params.append(batch_id)
        if game_id:
            query += " AND game_id = ?"
            params.append(game_id)

        query += " ORDER BY priority DESC, created_at ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.library._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]

    def get_status_summary(self, batch_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get aggregated counts of analysis jobs by status.
        """
        where_clause = ""
        params: List[Any] = []
        if batch_id:
            where_clause = "WHERE batch_id = ?"
            params.append(batch_id)

        query = f"""
            SELECT status, COUNT(*) as count
            FROM analysis_jobs
            {where_clause}
            GROUP BY status
        """

        summary = {
            "total": 0,
            JOB_PENDING: 0,
            JOB_RUNNING: 0,
            JOB_COMPLETED: 0,
            JOB_FAILED: 0,
            JOB_CANCELLED: 0,
        }

        with self.library._get_connection() as conn:
            cursor = conn.execute(query, params)
            for row in cursor.fetchall():
                st = row["status"]
                cnt = row["count"]
                if st in summary:
                    summary[st] = cnt
                summary["total"] += cnt

        return summary
