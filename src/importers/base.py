"""
Base abstractions, results model, and exceptions for chess platform importers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
import calendar
import io
import re
import time
from typing import Optional, List, Dict, Any, Tuple, Iterable, Union
import urllib.request
import urllib.error
import urllib.parse
import chess.pgn


class ImporterError(Exception):
    """Base exception for all game import operations."""
    pass


class UserNotFoundError(ImporterError):
    """Raised when a user account cannot be found on the target platform."""
    pass


class RateLimitError(ImporterError):
    """Raised when an external API rate limit (HTTP 429) is encountered."""
    pass


class NetworkError(ImporterError):
    """Raised when an underlying network, connection, or timeout error occurs."""
    pass


class APIError(ImporterError):
    """Raised when an external API returns an unexpected HTTP error code."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class ImportResult:
    """
    Structured outcome of an import/synchronization run.
    """
    source: str
    username: str
    requested_range: Optional[str] = None
    games_found: int = 0
    games_imported: int = 0
    duplicates_skipped: int = 0
    invalid_games: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    imported_game_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "username": self.username,
            "requested_range": self.requested_range,
            "games_found": self.games_found,
            "games_imported": self.games_imported,
            "duplicates_skipped": self.duplicates_skipped,
            "invalid_games": self.invalid_games,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "imported_game_ids": list(self.imported_game_ids),
        }

    def summary(self) -> str:
        """
        Human-readable summary suitable for CLI or UI display.
        """
        lines = [
            f"Imported {self.games_imported} games from {self.source} for {self.username}:",
            f"  {self.games_imported} new",
            f"  {self.duplicates_skipped} already in library",
            f"  {self.invalid_games} invalid",
            f"  {self.games_found} total found",
        ]
        if self.errors:
            lines.append(f"  Errors ({len(self.errors)}): {'; '.join(self.errors)}")
        if self.warnings:
            lines.append(f"  Warnings ({len(self.warnings)}): {'; '.join(self.warnings)}")
        return "\n".join(lines)


@dataclass
class ImportBatch:
    """A bounded batch of games fetched from a platform, with checkpoint metadata."""
    games: List[Tuple[str, Optional[str]]]  # (pgn_text, source_game_id)
    checkpoint: Optional[str] = None
    has_more: bool = True
    batch_label: str = ""


def parse_date_bound(val: Any, is_until: bool = False) -> Optional[datetime]:
    """
    Normalize various date inputs (YYYY-MM, YYYY-MM-DD, date, datetime)
    into a timezone-aware UTC datetime.
    """
    if not val:
        return None
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val.astimezone(timezone.utc)
    if isinstance(val, date):
        t = datetime.max.time() if is_until else datetime.min.time()
        return datetime.combine(val, t, tzinfo=timezone.utc)

    s = str(val).strip()
    if not s:
        return None

    # Format YYYY-MM
    if len(s) == 7 and s[4] == "-":
        y, m = int(s[:4]), int(s[5:7])
        if is_until:
            _, last_day = calendar.monthrange(y, m)
            return datetime(y, m, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
        else:
            return datetime(y, m, 1, 0, 0, 0, tzinfo=timezone.utc)

    # Format YYYY-MM-DD
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        y, m, d = int(s[:4]), int(s[5:7]), int(s[8:10])
        if is_until:
            return datetime(y, m, d, 23, 59, 59, 999999, tzinfo=timezone.utc)
        else:
            return datetime(y, m, d, 0, 0, 0, tzinfo=timezone.utc)

    # Fallback to general ISO parsing
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except Exception as e:
        raise ValueError(f"Cannot parse date '{val}': {e}")


class BaseImporter(ABC):
    """
    Abstract base class for all platform game importers.
    """

    source: str = "base"
    user_agent: str = "PersonalChessCoach/1.0 (+https://github.com/rup-git/personal-chess-coach)"
    timeout: int = 15

    def __init__(self, user_agent: Optional[str] = None, timeout: int = 15):
        if user_agent:
            self.user_agent = user_agent
        self.timeout = timeout

    def _make_request(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        method: str = "GET"
    ) -> bytes:
        """
        Execute an HTTP request with error classification.
        """
        req_headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
        }
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UserNotFoundError(f"Resource not found (HTTP 404) at {url}") from e
            elif e.code == 429:
                raise RateLimitError(f"Rate limit exceeded (HTTP 429) from {url}") from e
            else:
                raise APIError(f"API request failed with HTTP {e.code} for {url}", status_code=e.code) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise NetworkError(f"Network error connecting to {url}: {e}") from e

    # ---- Retry & streaming helpers (Segment 11A) ----

    def _make_request_with_retry(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        method: str = "GET",
        max_retries: int = 3,
        base_delay: float = 2.0,
    ) -> bytes:
        """
        HTTP request with bounded exponential backoff for transient errors.

        Retries on: HTTP 429 (rate limit), 5xx server errors, network/timeout errors.
        Does NOT retry on: 401, 403, 404, other permanent client errors.
        """
        last_error: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return self._make_request(url, headers=headers, method=method)
            except RateLimitError as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise
            except NetworkError as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise
            except APIError as e:
                if e.status_code is not None and e.status_code >= 500 and attempt < max_retries:
                    last_error = e
                    time.sleep(base_delay * (2 ** attempt))
                    continue
        if last_error is not None:
            raise last_error
        raise ImporterError(f"Request failed for {url}")

    def _open_stream(self, url: str, headers: Optional[Dict[str, str]] = None):
        """Open a streaming HTTP connection, returning the raw response object."""
        req_headers = {
            "User-Agent": self.user_agent,
            "Accept-Encoding": "identity",
        }
        if headers:
            req_headers.update(headers)
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UserNotFoundError(f"Resource not found (HTTP 404) at {url}") from e
            elif e.code == 429:
                raise RateLimitError(f"Rate limit exceeded (HTTP 429) from {url}") from e
            else:
                raise APIError(
                    f"API request failed with HTTP {e.code} for {url}",
                    status_code=e.code,
                ) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise NetworkError(f"Network error connecting to {url}: {e}") from e

    def _open_stream_with_retry(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        base_delay: float = 2.0,
    ):
        """Open a streaming HTTP connection with bounded retry for transient errors."""
        last_error: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return self._open_stream(url, headers=headers)
            except RateLimitError as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise
            except NetworkError as e:
                last_error = e
                if attempt < max_retries:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise
            except APIError as e:
                if e.status_code is not None and e.status_code >= 500 and attempt < max_retries:
                    last_error = e
                    time.sleep(base_delay * (2 ** attempt))
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise ImporterError(f"Request failed for {url}")

    def _validate_pgn(
        self, pgn_text: str, src_id: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate a PGN entry.
        Returns (is_valid, warning_message_or_none).
        """
        if not pgn_text or not pgn_text.strip():
            return False, "Skipped empty PGN entry"
        try:
            parsed = chess.pgn.read_game(io.StringIO(pgn_text))
            has_moves = any(parsed.mainline_moves()) if parsed else False
            is_empty_or_default = (
                not has_moves
                and parsed.headers.get("White", "?") in ("?", "Unknown", "")
                and parsed.headers.get("Black", "?") in ("?", "Unknown", "")
            ) if parsed else True

            if parsed is None or is_empty_or_default or parsed.errors:
                err_detail = (
                    "; ".join(str(e) for e in parsed.errors)
                    if (parsed and parsed.errors)
                    else "No moves or player headers"
                )
                return False, f"Skipped invalid PGN (id={src_id or 'unknown'}): {err_detail}"
        except Exception as parse_err:
            return False, f"Malformed PGN (id={src_id or 'unknown'}): {parse_err}"
        return True, None

    @abstractmethod
    def fetch_games(
        self,
        username: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        max_games: Optional[int] = None,
    ) -> Iterable[Tuple[str, Optional[str]]]:
        """
        Fetch games from external source.
        Yields (pgn_text, source_game_id).
        """
        pass

    def fetch_games_batched(
        self,
        username: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        max_games: Optional[int] = None,
        checkpoint: Optional[str] = None,
    ) -> Iterable[ImportBatch]:
        """
        Fetch games in bounded batches with checkpoint support.
        Yields ImportBatch objects.

        Default implementation wraps fetch_games() into a single batch.
        Subclasses should override for proper batched/streaming behavior.
        """
        games = list(self.fetch_games(
            username, since=since, until=until, max_games=max_games
        ))
        yield ImportBatch(
            games=games,
            checkpoint=None,
            has_more=False,
            batch_label=f"{self.source} (all)",
        )

    def import_games(
        self,
        username: str,
        library: Any,
        since: Optional[Any] = None,
        until: Optional[Any] = None,
        max_games: Optional[int] = None,
        raise_on_error: bool = False,
    ) -> ImportResult:
        """
        Fetch games from external source and persist them into the provided GameLibrary.
        Handles duplicates idempotently, preserves existing analyses, and classifies results.
        """
        norm_since = parse_date_bound(since, is_until=False)
        norm_until = parse_date_bound(until, is_until=True)

        if norm_since and norm_until:
            range_desc = f"{norm_since.strftime('%Y-%m-%d')} to {norm_until.strftime('%Y-%m-%d')}"
        elif norm_since:
            range_desc = f"from {norm_since.strftime('%Y-%m-%d')}"
        elif norm_until:
            range_desc = f"until {norm_until.strftime('%Y-%m-%d')}"
        else:
            range_desc = "all available"

        result = ImportResult(
            source=self.source,
            username=username,
            requested_range=range_desc,
        )

        try:
            for pgn_text, src_id in self.fetch_games(
                username=username,
                since=norm_since,
                until=norm_until,
                max_games=max_games,
            ):
                if not pgn_text or not pgn_text.strip():
                    result.invalid_games += 1
                    result.warnings.append("Skipped empty PGN entry")
                    continue

                # Validation using python-chess
                try:
                    parsed = chess.pgn.read_game(io.StringIO(pgn_text))
                    has_moves = any(parsed.mainline_moves()) if parsed else False
                    is_empty_or_default = (
                        not has_moves and
                        parsed.headers.get("White", "?") in ("?", "Unknown", "") and
                        parsed.headers.get("Black", "?") in ("?", "Unknown", "")
                    ) if parsed else True

                    if parsed is None or is_empty_or_default or parsed.errors:
                        result.invalid_games += 1
                        err_detail = "; ".join(str(e) for e in parsed.errors) if (parsed and parsed.errors) else "No moves or player headers"
                        result.warnings.append(f"Skipped invalid/malformed PGN entry (id={src_id or 'unknown'}): {err_detail}")
                        continue
                except Exception as parse_err:
                    result.invalid_games += 1
                    result.warnings.append(f"Malformed PGN (id={src_id or 'unknown'}): {parse_err}")
                    continue

                result.games_found += 1

                # Persist into canonical library
                try:
                    game, is_new = library.add_game(
                        pgn_text,
                        source=self.source,
                        source_game_id=src_id,
                    )
                    if is_new:
                        result.games_imported += 1
                        result.imported_game_ids.append(game.game_id)
                    else:
                        result.duplicates_skipped += 1
                except Exception as db_err:
                    result.errors.append(f"Failed to persist game (id={src_id or 'unknown'}): {db_err}")

        except ImporterError as err:
            result.errors.append(str(err))
            if raise_on_error:
                raise
        except Exception as unexp_err:
            result.errors.append(f"Unexpected error during import: {unexp_err}")
            if raise_on_error:
                raise

        return result

    def bulk_import_games(
        self,
        username: str,
        library: Any,
        since: Optional[Any] = None,
        until: Optional[Any] = None,
        max_games: Optional[int] = None,
        progress_callback: Optional[Any] = None,
        dry_run: bool = False,
        job_id: Optional[str] = None,
    ) -> ImportResult:
        """
        Bulk import with checkpointing, bounded retries, and progress reporting.
        Automatically resumes an interrupted import if one exists for this
        platform + username, or uses the specific job_id if provided.
        """
        norm_since = parse_date_bound(since, is_until=False)
        norm_until = parse_date_bound(until, is_until=True)

        if norm_since and norm_until:
            range_desc = f"{norm_since.strftime('%Y-%m-%d')} to {norm_until.strftime('%Y-%m-%d')}"
        elif norm_since:
            range_desc = f"from {norm_since.strftime('%Y-%m-%d')}"
        elif norm_until:
            range_desc = f"until {norm_until.strftime('%Y-%m-%d')}"
        else:
            range_desc = "all available"

        # Find or create import job
        if dry_run:
            job = {
                "job_id": "dry_run",
                "last_checkpoint": None,
                "games_fetched": 0,
                "games_imported": 0,
                "duplicates_skipped": 0,
                "games_failed": 0,
            }
            is_resume = False
        elif job_id:
            job = library.get_import_job(job_id)
            if not job:
                raise ValueError(f"Import job '{job_id}' not found.")
            is_resume = job.get("last_checkpoint") is not None
        else:
            job = library.find_resumable_import_job(self.source, username)
            is_resume = job is not None

            if job is None:
                job = library.create_import_job(
                    platform=self.source,
                    username=username,
                    date_from=norm_since.isoformat() if norm_since else None,
                    date_to=norm_until.isoformat() if norm_until else None,
                )

        checkpoint = job.get("last_checkpoint")

        # Initialize counters from resume state
        total_fetched = job.get("games_fetched", 0)
        total_imported = job.get("games_imported", 0)
        total_dupes = job.get("duplicates_skipped", 0)
        total_failed = job.get("games_failed", 0)
        all_errors: List[str] = []
        all_warnings: List[str] = []
        all_imported_ids: List[str] = []

        try:
            for batch in self.fetch_games_batched(
                username=username,
                since=norm_since,
                until=norm_until,
                max_games=max_games,
                checkpoint=checkpoint,
            ):
                valid_games: List[Tuple[str, Optional[str], Optional[str]]] = []
                batch_invalid = 0

                for pgn_text, src_id in batch.games:
                    is_valid, warning = self._validate_pgn(pgn_text, src_id)
                    if not is_valid:
                        batch_invalid += 1
                        if warning:
                            all_warnings.append(warning)
                        continue
                    valid_games.append((pgn_text, self.source, src_id))

                total_fetched += len(valid_games)
                total_failed += batch_invalid

                if not dry_run and valid_games:
                    try:
                        results = library.add_games_batch(valid_games)
                        for game, is_new in results:
                            if is_new:
                                total_imported += 1
                                all_imported_ids.append(game.game_id)
                            else:
                                total_dupes += 1
                    except Exception as batch_err:
                        all_errors.append(
                            f"Batch persist failed ({batch.batch_label}): {batch_err}"
                        )
                        continue  # Don't update checkpoint on failed batch

                # Checkpoint ONLY after successful batch processing
                if not dry_run:
                    library.update_import_job(
                        job["job_id"],
                        games_fetched=total_fetched,
                        games_imported=total_imported,
                        duplicates_skipped=total_dupes,
                        games_failed=total_failed,
                        last_checkpoint=batch.checkpoint,
                        has_more=1 if batch.has_more else 0,
                    )

                if progress_callback:
                    progress_callback({
                        "batch_label": batch.batch_label,
                        "games_fetched": total_fetched,
                        "games_imported": total_imported,
                        "duplicates_skipped": total_dupes,
                        "games_failed": total_failed,
                        "has_more": batch.has_more,
                        "is_resume": is_resume,
                    })

            # All batches processed successfully
            if not dry_run:
                library.complete_import_job(job["job_id"])

        except ImporterError as err:
            all_errors.append(str(err))
            if not dry_run:
                library.fail_import_job(job["job_id"], str(err))
        except Exception as unexp_err:
            all_errors.append(f"Unexpected error: {unexp_err}")
            if not dry_run:
                library.fail_import_job(job["job_id"], str(unexp_err))

        return ImportResult(
            source=self.source,
            username=username,
            requested_range=range_desc,
            games_found=total_fetched,
            games_imported=total_imported,
            duplicates_skipped=total_dupes,
            invalid_games=total_failed,
            errors=all_errors,
            warnings=all_warnings,
            imported_game_ids=all_imported_ids,
        )
