"""
Chess.com public games importer.
"""

from datetime import datetime, timezone
import calendar
import json
import re
from typing import Optional, Iterable, Tuple, Dict, Any, List
import urllib.parse

from .base import BaseImporter, UserNotFoundError, RateLimitError, APIError, NetworkError


class ChessComImporter(BaseImporter):
    """
    Importer for Chess.com public games using the Chess.com PubAPI.
    """

    source: str = "chess.com"
    base_url: str = "https://api.chess.com/pub/player"

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: int = 15,
        reverse_chronological: bool = True,
    ):
        super().__init__(user_agent=user_agent, timeout=timeout)
        self.reverse_chronological = reverse_chronological

    def _extract_source_game_id(self, game_url: Optional[str]) -> Optional[str]:
        if not game_url:
            return None
        match = re.search(r"/(?:live|daily)/(\d+)", game_url)
        return match.group(1) if match else None

    def get_archive_urls(self, username: str) -> List[str]:
        """
        Fetch the list of monthly game archive URLs for a player.
        """
        clean_user = urllib.parse.quote(username.strip().lower())
        url = f"{self.base_url}/{clean_user}/games/archives"
        try:
            data = self._make_request(url, headers={"Accept": "application/json"})
            parsed = json.loads(data.decode("utf-8"))
            return parsed.get("archives", [])
        except UserNotFoundError:
            raise UserNotFoundError(f"Chess.com user '{username}' was not found.")

    def filter_archive_urls(
        self,
        archive_urls: List[str],
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[str]:
        """
        Filter monthly archive URLs according to the requested since/until range.
        """
        filtered = []
        for arch_url in archive_urls:
            match = re.search(r"/games/(\d{4})/(\d{2})$", arch_url)
            if not match:
                filtered.append(arch_url)
                continue

            year = int(match.group(1))
            month = int(match.group(2))
            _, last_day = calendar.monthrange(year, month)

            start_of_month = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
            end_of_month = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)

            if since and end_of_month < since:
                continue
            if until and start_of_month > until:
                continue

            filtered.append(arch_url)

        return filtered

    def fetch_games(
        self,
        username: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        max_games: Optional[int] = None,
    ) -> Iterable[Tuple[str, Optional[str]]]:
        """
        Traverse monthly archives, fetching games and yielding (pgn_text, source_game_id).
        """
        archives = self.get_archive_urls(username)
        matching_archives = self.filter_archive_urls(archives, since=since, until=until)

        if self.reverse_chronological:
            matching_archives = list(reversed(matching_archives))

        games_yielded = 0

        for arch_url in matching_archives:
            try:
                data = self._make_request(arch_url, headers={"Accept": "application/json"})
                month_data = json.loads(data.decode("utf-8"))
            except Exception as e:
                # If an individual archive is unavailable, log and continue
                continue

            games_list = month_data.get("games", [])
            if self.reverse_chronological:
                games_list = list(reversed(games_list))

            for g in games_list:
                pgn = g.get("pgn", "")
                if not pgn:
                    continue

                # Date filtering by end_time timestamp if present
                end_time = g.get("end_time")
                if end_time is not None:
                    game_dt = datetime.fromtimestamp(end_time, tz=timezone.utc)
                    if since and game_dt < since:
                        continue
                    if until and game_dt > until:
                        continue

                url = g.get("url")
                src_id = self._extract_source_game_id(url)

                yield pgn, src_id
                games_yielded += 1

                if max_games is not None and games_yielded >= max_games:
                    return
