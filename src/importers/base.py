"""
Base abstractions, results model, and exceptions for chess platform importers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
import calendar
import io
import re
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
