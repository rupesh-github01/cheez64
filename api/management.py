"""
FastAPI Router for Import and Analysis Management.

Provides endpoints to:
- Monitor system summary (counts of games, imports, queue, worker status)
- Start and resume background platform imports (Chess.com, Lichess)
- Enqueue games or batches for Stockfish analysis
- Inspect and manage analysis queue jobs (retry, cancel, retry-all)
- Inspect worker heartbeat and operational status
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.library import GameLibrary, DEFAULT_DB_PATH
from src.analysis_queue import (
    AnalysisQueue,
    AnalysisConfig,
    JobNotFoundError,
    InvalidStateTransitionError,
    QueueError,
)
from api.import_manager import (
    ImportManager,
    ImportAlreadyRunningError,
    ImportNotFoundError,
)

logger = logging.getLogger("management_api")

router = APIRouter(prefix="/api/management", tags=["management"])

# Module-level references initialized by main.py
_import_manager: Optional[ImportManager] = None
_db_path: str = DEFAULT_DB_PATH


def set_management_deps(db_path: str, import_manager: ImportManager) -> None:
    """Initialize dependencies for the management router."""
    global _db_path, _import_manager
    _db_path = db_path
    _import_manager = import_manager


def get_import_manager() -> ImportManager:
    """Retrieve the global ImportManager instance."""
    global _import_manager
    if _import_manager is None:
        _import_manager = ImportManager(_db_path)
    return _import_manager


def get_library() -> GameLibrary:
    """Open a short-lived GameLibrary connection."""
    return GameLibrary(_db_path)


def validate_local_request(request: Request) -> None:
    """
    Validate that administrative mutation requests originate from loopback interfaces.
    Protects local instances when bound or tunneled.
    """
    allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}

    # Check Host header
    host_header = request.headers.get("host", "").split(":")[0].lower()
    if host_header and host_header not in allowed_hosts:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Administrative mutation forbidden: invalid Host header '{host_header}'",
        )

    # Check Origin header if present
    origin = request.headers.get("origin")
    if origin:
        parsed_origin = urlparse(origin)
        origin_host = parsed_origin.hostname
        if origin_host and origin_host.lower() not in allowed_hosts:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Administrative mutation forbidden: invalid Origin header '{origin}'",
            )


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class ImportRequest(BaseModel):
    platform: str = Field(..., description="Platform: 'chess.com' or 'lichess'")
    username: str = Field(..., description="Platform username to import from")
    since: Optional[str] = Field(None, description="ISO date string or YYYY-MM-DD")
    until: Optional[str] = Field(None, description="ISO date string or YYYY-MM-DD")
    max_games: Optional[int] = Field(None, ge=1, description="Maximum number of games to import")


class QueueAnalysisRequest(BaseModel):
    game_ids: Optional[List[str]] = Field(None, description="List of specific game IDs to queue")
    all_unanalyzed: bool = Field(False, description="Queue all unanalyzed games in library")
    priority: int = Field(100, description="Priority integer (lower = higher priority)")
    force_reanalyze: bool = Field(False, description="Force re-analysis of already analyzed games")
    depth: Optional[int] = Field(None, ge=5, le=30, description="Optional Stockfish evaluation depth")
    multipv: Optional[int] = Field(None, ge=1, le=10, description="Optional Stockfish MultiPV count")


# ---------------------------------------------------------------------------
# Endpoints: System Summary
# ---------------------------------------------------------------------------

@router.get("/summary")
def get_management_summary() -> Dict[str, Any]:
    """
    Comprehensive overview of the library, import jobs, analysis queue, and worker health.
    """
    mgr = get_import_manager()

    with get_library() as lib:
        # Library game counts
        total_games = lib.count_games()
        analyzed_games = lib.count_games(analysis_status="ANALYZED")
        unanalyzed_games = lib.count_games(analysis_status="NOT_ANALYZED")

        # Import job counts
        import_total = lib.count_import_jobs()
        import_running = lib.count_import_jobs(status="running")
        import_completed = lib.count_import_jobs(status="completed")
        import_failed = lib.count_import_jobs(status="failed")
        active_import = mgr.get_active_import_job()

        # Analysis queue summary
        queue = AnalysisQueue(lib)
        queue_summary = queue.get_status_summary()

        # Worker status
        active_workers = lib.get_active_worker_heartbeats(max_age_seconds=120.0)
        worker_count = len(active_workers)

        worker_status = "offline"
        last_heartbeat = None
        current_job_id = None

        if worker_count > 0:
            last_heartbeat = active_workers[0]["last_seen_at"]
            # Check if any worker is actively working on a job
            busy_workers = [w for w in active_workers if w.get("current_job_id")]
            if busy_workers or queue_summary.get("RUNNING", 0) > 0:
                worker_status = "online/analyzing"
                current_job_id = busy_workers[0]["current_job_id"] if busy_workers else None
            else:
                worker_status = "online/idle"
        else:
            # Check if any heartbeats exist in DB (stale check)
            with lib._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT last_seen_at FROM worker_heartbeats ORDER BY last_seen_at DESC LIMIT 1"
                )
                row = cursor.fetchone()
                if row:
                    worker_status = "stale"
                    last_heartbeat = row["last_seen_at"]
                else:
                    worker_status = "offline"

    return {
        "library": {
            "total_games": total_games,
            "analyzed_games": analyzed_games,
            "unanalyzed_games": unanalyzed_games,
        },
        "imports": {
            "total": import_total,
            "running": import_running,
            "completed": import_completed,
            "failed": import_failed,
            "active_job": active_import,
        },
        "analysis_queue": {
            "pending": queue_summary.get("PENDING", 0),
            "running": queue_summary.get("RUNNING", 0),
            "completed": queue_summary.get("COMPLETED", 0),
            "failed": queue_summary.get("FAILED", 0),
            "cancelled": queue_summary.get("CANCELLED", 0),
            "total": sum(queue_summary.values()),
        },
        "worker": {
            "status": worker_status,
            "active_worker_count": worker_count,
            "last_heartbeat": last_heartbeat,
            "current_job_id": current_job_id,
            "command": "./run.sh worker",
        },
    }


# ---------------------------------------------------------------------------
# Endpoints: Imports
# ---------------------------------------------------------------------------

@router.post("/imports", status_code=status.HTTP_202_ACCEPTED)
def start_import(req: ImportRequest, request: Request) -> Dict[str, Any]:
    """
    Start a background platform import for Chess.com or Lichess.
    Returns 202 Accepted with job metadata, or 409 Conflict if an import for this account is already active.
    """
    validate_local_request(request)
    mgr = get_import_manager()

    try:
        job = mgr.start_import(
            platform=req.platform,
            username=req.username,
            since=req.since,
            until=req.until,
            max_games=req.max_games,
        )
        return {
            "message": f"Import started for {req.platform}/{req.username}",
            "job": job,
        }
    except ImportAlreadyRunningError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/imports")
def list_imports(
    status: Optional[str] = Query(None, description="Filter by status: running, completed, failed"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """
    List historical and current import jobs.
    """
    with get_library() as lib:
        jobs = lib.list_import_jobs(status=status, limit=limit, offset=offset)
        total = lib.count_import_jobs(status=status)

    return {
        "jobs": jobs,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/imports/{job_id}")
def get_import_details(job_id: str) -> Dict[str, Any]:
    """
    Get detailed status and checkpoint progress of a specific import job.
    """
    with get_library() as lib:
        job = lib.get_import_job(job_id)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Import job '{job_id}' not found.")
        return {"job": job}


@router.post("/imports/{job_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_import(job_id: str, request: Request) -> Dict[str, Any]:
    """
    Resume an interrupted or failed import job from its last checkpoint.
    """
    validate_local_request(request)
    mgr = get_import_manager()

    try:
        job = mgr.resume_import(job_id)
        return {
            "message": f"Import job '{job_id}' resumed",
            "job": job,
        }
    except ImportNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except ImportAlreadyRunningError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


# ---------------------------------------------------------------------------
# Endpoints: Analysis Queue
# ---------------------------------------------------------------------------

@router.post("/analysis/queue", status_code=status.HTTP_202_ACCEPTED)
def enqueue_analysis(req: QueueAnalysisRequest, request: Request) -> Dict[str, Any]:
    """
    Enqueue games for Stockfish analysis. Supports individual game IDs, batches, or all unanalyzed games.
    """
    validate_local_request(request)

    config: Optional[AnalysisConfig] = None
    if req.depth or req.multipv:
        kwargs: Dict[str, Any] = {}
        if req.depth:
            kwargs["depth"] = req.depth
        if req.multipv:
            kwargs["multipv"] = req.multipv
        config = AnalysisConfig(**kwargs)

    with get_library() as lib:
        queue = AnalysisQueue(lib)

        if req.all_unanalyzed:
            batch_result = queue.enqueue_all_unanalyzed(config=config, priority=req.priority)
            return {
                "message": f"Queued {batch_result['enqueued']} unanalyzed games for Stockfish analysis",
                "queued_count": batch_result["enqueued"],
                "batch_id": batch_result.get("batch_id"),
            }

        if req.game_ids:
            if not isinstance(req.game_ids, list) or len(req.game_ids) == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game_ids must be a non-empty list of game IDs.",
                )

            batch_result = queue.enqueue_batch(
                game_ids=req.game_ids,
                config=config,
                priority=req.priority,
                force_reanalyze=req.force_reanalyze,
            )
            job_ids = [j["job_id"] for j in batch_result.get("jobs", [])]
            return {
                "message": f"Queued {batch_result['enqueued']} games for Stockfish analysis",
                "queued_count": batch_result["enqueued"],
                "job_ids": job_ids,
                "batch_id": batch_result.get("batch_id"),
            }

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either 'game_ids' or set 'all_unanalyzed': true.",
        )


@router.get("/analysis/queue")
def list_analysis_queue(
    status: Optional[str] = Query(None, description="Filter by status: PENDING, RUNNING, COMPLETED, FAILED, CANCELLED"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """
    List jobs in the analysis queue with status summary.
    """
    with get_library() as lib:
        queue = AnalysisQueue(lib)
        jobs = queue.list_jobs(status=status, limit=limit, offset=offset)
        summary = queue.get_status_summary()

    return {
        "jobs": jobs,
        "summary": summary,
        "limit": limit,
        "offset": offset,
    }


@router.post("/analysis/queue/{job_id}/retry")
def retry_analysis_job(job_id: str, request: Request) -> Dict[str, Any]:
    """
    Retry a FAILED or CANCELLED analysis job.
    """
    validate_local_request(request)

    with get_library() as lib:
        queue = AnalysisQueue(lib)
        try:
            queue.retry_job(job_id)
            job = queue.get_job(job_id)
            return {
                "message": f"Job '{job_id}' retried and set to PENDING",
                "job": job,
            }
        except JobNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except InvalidStateTransitionError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/analysis/queue/{job_id}/cancel")
def cancel_analysis_job(job_id: str, request: Request) -> Dict[str, Any]:
    """
    Cancel a PENDING or FAILED analysis job.
    """
    validate_local_request(request)

    with get_library() as lib:
        queue = AnalysisQueue(lib)
        try:
            queue.cancel_job(job_id)
            job = queue.get_job(job_id)
            return {
                "message": f"Job '{job_id}' cancelled",
                "job": job,
            }
        except JobNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
        except InvalidStateTransitionError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/analysis/queue/retry-all")
def retry_all_failed_jobs(request: Request) -> Dict[str, Any]:
    """
    Retry all currently FAILED analysis jobs.
    """
    validate_local_request(request)

    with get_library() as lib:
        queue = AnalysisQueue(lib)
        count = queue.retry_all_failed()
        return {
            "message": f"Retried {count} failed analysis jobs",
            "retried_count": count,
        }


# ---------------------------------------------------------------------------
# Endpoints: Worker Status
# ---------------------------------------------------------------------------

@router.get("/workers")
def get_worker_status() -> Dict[str, Any]:
    """
    Retrieve worker process heartbeats and operational health status.
    """
    with get_library() as lib:
        active_workers = lib.get_active_worker_heartbeats(max_age_seconds=120.0)
        queue = AnalysisQueue(lib)
        summary = queue.get_status_summary()

        running_jobs = summary.get("RUNNING", 0)
        worker_count = len(active_workers)

        if worker_count > 0:
            has_busy = any(w.get("current_job_id") for w in active_workers) or running_jobs > 0
            state = "online/analyzing" if has_busy else "online/idle"
        else:
            with lib._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT last_seen_at FROM worker_heartbeats ORDER BY last_seen_at DESC LIMIT 1"
                )
                row = cursor.fetchone()
                state = "stale" if row else "offline"

    return {
        "status": state,
        "active_worker_count": worker_count,
        "workers": active_workers,
        "running_jobs_count": running_jobs,
        "instructions": "Run './run.sh worker' in a terminal to start the background analysis worker.",
    }
