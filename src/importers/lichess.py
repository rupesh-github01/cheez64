"""
Lichess public games importer.
"""

from datetime import datetime, timezone
import io
import re
from typing import Optional, Iterable, Tuple, Dict, Any, List
import urllib.parse
import chess.pgn

from .base import (
    BaseImporter,
    ImportBatch,
    UserNotFoundError,
    RateLimitError,
    APIError,
    NetworkError,
    parse_date_bound,
)


class LichessImporter(BaseImporter):
    """
    Importer for Lichess public games using the Lichess Games API.
    """

    source: str = "lichess"
    base_url: str = "https://lichess.org/api/games/user"

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: int = 30,
        batch_size: int = 50,
    ):
        super().__init__(user_agent=user_agent, timeout=timeout)
        self.batch_size = batch_size

    def _extract_source_game_id(self, game: chess.pgn.Game) -> Optional[str]:
        site = game.headers.get("Site", "")
        link = game.headers.get("Link", "")
        game_id = game.headers.get("GameId", "")
        if game_id:
            return game_id.strip()

        combined = f"{site} {link}".strip()
        match = re.search(r"lichess\.org/([a-zA-Z0-9]+)", combined)
        return match.group(1) if match else None

    def _extract_game_timestamp(self, game: chess.pgn.Game) -> Optional[datetime]:
        """Extract UTC datetime from PGN headers."""
        utc_date = game.headers.get("UTCDate", "").strip()
        utc_time = game.headers.get("UTCTime", "").strip()
        if utc_date and utc_date != "????.??.??":
            try:
                date_part = utc_date.replace(".", "-")
                if utc_time and utc_time != "??:??:??":
                    return datetime.fromisoformat(f"{date_part}T{utc_time}").replace(tzinfo=timezone.utc)
                return datetime.fromisoformat(date_part).replace(tzinfo=timezone.utc)
            except Exception:
                pass

        d = game.headers.get("Date", "").strip()
        t = game.headers.get("Time", "").strip()
        if d and d != "????.??.??":
            try:
                date_part = d.replace(".", "-")
                if t and t != "??:??:??":
                    return datetime.fromisoformat(f"{date_part}T{t}").replace(tzinfo=timezone.utc)
                return datetime.fromisoformat(date_part).replace(tzinfo=timezone.utc)
            except Exception:
                pass
        return None

    def fetch_games(
        self,
        username: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        max_games: Optional[int] = None,
    ) -> Iterable[Tuple[str, Optional[str]]]:
        """
        Request PGN stream from Lichess and yield (pgn_text, source_game_id) for each game.
        Preserved for backward compatibility.
        """
        clean_user = urllib.parse.quote(username.strip())
        params: Dict[str, Any] = {
            "moves": "true",
            "clocks": "true",
            "evals": "false",
            "opening": "true",
        }

        if since is not None:
            params["since"] = int(since.timestamp() * 1000)
        if until is not None:
            params["until"] = int(until.timestamp() * 1000)
        if max_games is not None:
            params["max"] = max_games

        query_string = urllib.parse.urlencode(params)
        url = f"{self.base_url}/{clean_user}?{query_string}"

        headers = {
            "Accept": "application/x-chess-pgn",
        }

        try:
            raw_bytes = self._make_request(url, headers=headers)
        except UserNotFoundError:
            raise UserNotFoundError(f"Lichess user '{username}' was not found.")

        # Decode response (Lichess returns UTF-8 PGN stream)
        pgn_stream = raw_bytes.decode("utf-8", errors="replace")
        if not pgn_stream.strip():
            return

        stream = io.StringIO(pgn_stream)

        games_count = 0
        while True:
            try:
                game_obj = chess.pgn.read_game(stream)
            except Exception:
                break

            if game_obj is None:
                break

            exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
            pgn_text = game_obj.accept(exporter)
            src_id = self._extract_source_game_id(game_obj)

            yield pgn_text, src_id
            games_count += 1

            if max_games is not None and games_count >= max_games:
                break

    def fetch_games_batched(
        self,
        username: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        max_games: Optional[int] = None,
        checkpoint: Optional[str] = None,
        batch_size: Optional[int] = None,
    ) -> Iterable[ImportBatch]:
        """
        Stream PGN games from Lichess in bounded batches without accumulating
        the full history in memory (Safeguard #2).
        Supports resuming from a timestamp checkpoint.
        """
        effective_batch_size = batch_size or self.batch_size
        clean_user = urllib.parse.quote(username.strip())
        params: Dict[str, Any] = {
            "moves": "true",
            "clocks": "true",
            "evals": "false",
            "opening": "true",
        }

        # Checkpoint resume: if checkpoint provided, bound until to checkpoint timestamp
        effective_until = until
        if checkpoint:
            try:
                cp_dt = parse_date_bound(checkpoint, is_until=True)
                if cp_dt:
                    if effective_until is None or cp_dt < effective_until:
                        effective_until = cp_dt
            except Exception:
                pass

        if since is not None:
            params["since"] = int(since.timestamp() * 1000)
        if effective_until is not None:
            params["until"] = int(effective_until.timestamp() * 1000)
        if max_games is not None:
            params["max"] = max_games

        query_string = urllib.parse.urlencode(params)
        url = f"{self.base_url}/{clean_user}?{query_string}"

        headers = {
            "Accept": "application/x-chess-pgn",
        }

        resp = self._open_stream_with_retry(url, headers=headers)
        try:
            if isinstance(resp, io.StringIO):
                stream = resp
            elif hasattr(resp, "read"):
                # Stream directly from socket without accumulating in memory
                stream = io.TextIOWrapper(resp, encoding="utf-8", errors="replace")
            else:
                stream = resp

            batch_games: List[Tuple[str, Optional[str]]] = []
            games_count = 0
            batch_num = 1
            last_game_ts: Optional[str] = checkpoint

            while True:
                try:
                    game_obj = chess.pgn.read_game(stream)
                except Exception:
                    break

                if game_obj is None:
                    break

                exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
                pgn_text = game_obj.accept(exporter)
                src_id = self._extract_source_game_id(game_obj)

                g_dt = self._extract_game_timestamp(game_obj)
                if g_dt:
                    last_game_ts = g_dt.isoformat()

                batch_games.append((pgn_text, src_id))
                games_count += 1

                reached_max = max_games is not None and games_count >= max_games

                if len(batch_games) >= effective_batch_size or reached_max:
                    yield ImportBatch(
                        games=batch_games,
                        checkpoint=last_game_ts,
                        has_more=not reached_max,
                        batch_label=f"lichess batch {batch_num} ({len(batch_games)} games)",
                    )
                    batch_games = []
                    batch_num += 1

                if reached_max:
                    break

            if batch_games:
                yield ImportBatch(
                    games=batch_games,
                    checkpoint=last_game_ts,
                    has_more=False,
                    batch_label=f"lichess batch {batch_num} ({len(batch_games)} games)",
                )
        finally:
            if hasattr(resp, "close"):
                try:
                    resp.close()
                except Exception:
                    pass
