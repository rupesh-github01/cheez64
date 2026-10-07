"""
Platform Game Importers for Chess.com and Lichess.
"""

from typing import Optional, Any
from .base import (
    BaseImporter,
    ImportResult,
    parse_date_bound,
    ImporterError,
    UserNotFoundError,
    RateLimitError,
    NetworkError,
    APIError,
)
from .chess_com import ChessComImporter
from .lichess import LichessImporter


def get_importer(source: str, **kwargs) -> BaseImporter:
    """
    Factory function to retrieve an importer by platform name.
    """
    normalized = source.strip().lower()
    if normalized in ("chess.com", "chesscom"):
        return ChessComImporter(**kwargs)
    elif normalized in ("lichess", "lichess.org"):
        return LichessImporter(**kwargs)
    else:
        raise ValueError(f"Unknown importer platform '{source}'. Supported: 'chess.com', 'lichess'")


def import_platform_games(
    source: str,
    username: str,
    library: Any,
    since: Optional[Any] = None,
    until: Optional[Any] = None,
    max_games: Optional[int] = None,
    raise_on_error: bool = False,
    **kwargs,
) -> ImportResult:
    """
    Convenience function to instantiate the proper platform importer and import games into a library.
    """
    importer = get_importer(source, **kwargs)
    return importer.import_games(
        username=username,
        library=library,
        since=since,
        until=until,
        max_games=max_games,
        raise_on_error=raise_on_error,
    )


__all__ = [
    "BaseImporter",
    "ChessComImporter",
    "LichessImporter",
    "ImportResult",
    "parse_date_bound",
    "ImporterError",
    "UserNotFoundError",
    "RateLimitError",
    "NetworkError",
    "APIError",
    "get_importer",
    "import_platform_games",
]
