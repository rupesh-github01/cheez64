"""
Lichess public games importer.
"""

from datetime import datetime
import io
import re
from typing import Optional, Iterable, Tuple, Dict, Any
import urllib.parse
import chess.pgn

from .base import BaseImporter, UserNotFoundError, RateLimitError, APIError, NetworkError


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
    ):
        super().__init__(user_agent=user_agent, timeout=timeout)

    def _extract_source_game_id(self, game: chess.pgn.Game) -> Optional[str]:
        site = game.headers.get("Site", "")
        link = game.headers.get("Link", "")
        game_id = game.headers.get("GameId", "")
        if game_id:
            return game_id.strip()

        combined = f"{site} {link}".strip()
        match = re.search(r"lichess\.org/([a-zA-Z0-9]+)", combined)
        return match.group(1) if match else None

    def fetch_games(
        self,
        username: str,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        max_games: Optional[int] = None,
    ) -> Iterable[Tuple[str, Optional[str]]]:
        """
        Request PGN stream from Lichess and yield (pgn_text, source_game_id) for each game.
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
            except Exception as e:
                # If stream reader hits an error, attempt to break or recover
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
