"""
Deterministic unit tests for Chess.com and Lichess Game Import Layer.
"""

import unittest
from unittest.mock import patch, MagicMock
import json
from pathlib import Path
import sys

# Ensure src is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import (
    GameLibrary,
    ANALYSIS_NOT_ANALYZED,
    ANALYSIS_ANALYZED,
)
from src.importers import (
    ChessComImporter,
    LichessImporter,
    ImportResult,
    UserNotFoundError,
    RateLimitError,
    NetworkError,
    APIError,
    import_platform_games,
)


CHESSCOM_ARCHIVES_JSON = json.dumps({
    "archives": [
        "https://api.chess.com/pub/player/testuser/games/2026/09",
        "https://api.chess.com/pub/player/testuser/games/2026/10",
    ]
}).encode("utf-8")

CHESSCOM_GAMES_SEPT_JSON = json.dumps({
    "games": [
        {
            "url": "https://www.chess.com/game/live/10001",
            "pgn": """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.09.15"]
[White "testuser"]
[Black "opponent1"]
[Result "1-0"]
[WhiteElo "1550"]
[BlackElo "1520"]
[TimeControl "300+0"]
[ECO "B01"]

1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 1-0
""",
            "end_time": 1789430400,
            "rated": True
        }
    ]
}).encode("utf-8")

CHESSCOM_GAMES_OCT_JSON = json.dumps({
    "games": [
        {
            "url": "https://www.chess.com/game/live/10002",
            "pgn": """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.10.02"]
[White "opponent2"]
[Black "testuser"]
[Result "0-1"]
[WhiteElo "1600"]
[BlackElo "1560"]
[TimeControl "180+2"]
[ECO "C00"]

1. e4 e6 2. d4 d5 3. e5 c5 0-1
""",
            "end_time": 1790899200,
            "rated": True
        }
    ]
}).encode("utf-8")

LICHESS_PGN_STREAM = """[Event "Rated Blitz game"]
[Site "https://lichess.org/lich001"]
[Date "2026.10.01"]
[White "testuser"]
[Black "rival1"]
[Result "1-0"]
[WhiteElo "1700"]
[BlackElo "1680"]
[TimeControl "180+0"]
[ECO "C20"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 1-0

[Event "Rated Rapid game"]
[Site "https://lichess.org/lich002"]
[Date "2026.10.03"]
[White "rival2"]
[Black "testuser"]
[Result "0-1"]
[WhiteElo "1750"]
[BlackElo "1710"]
[TimeControl "600+5"]
[ECO "B20"]

1. e4 c5 2. Nf3 d6 3. d4 cxd4 0-1
""".encode("utf-8")


class TestImporters(unittest.TestCase):
    """
    Test suite for Chess.com and Lichess importers, edge case handling, and library persistence.
    """

    def setUp(self):
        self.lib = GameLibrary(":memory:")

    def tearDown(self):
        self.lib.close()

    @patch.object(ChessComImporter, "_make_request")
    def test_chesscom_valid_import(self, mock_req):
        """Verify fetching multiple monthly archives and persisting canonical games from Chess.com."""
        def fake_request(url, headers=None, method="GET"):
            if "archives" in url:
                return CHESSCOM_ARCHIVES_JSON
            elif "2026/09" in url:
                return CHESSCOM_GAMES_SEPT_JSON
            elif "2026/10" in url:
                return CHESSCOM_GAMES_OCT_JSON
            raise ValueError(f"Unexpected url: {url}")

        mock_req.side_effect = fake_request

        importer = ChessComImporter(reverse_chronological=False)
        result = importer.import_games("testuser", self.lib)

        self.assertEqual(result.source, "chess.com")
        self.assertEqual(result.games_found, 2)
        self.assertEqual(result.games_imported, 2)
        self.assertEqual(result.duplicates_skipped, 0)
        self.assertEqual(result.invalid_games, 0)
        self.assertEqual(len(result.errors), 0)

        # Verify games in library
        games = self.lib.list_games()
        self.assertEqual(len(games), 2)

        g1 = self.lib.get_game(result.imported_game_ids[0])
        self.assertEqual(g1.source, "chess.com")
        self.assertEqual(g1.source_game_id, "10001")
        self.assertEqual(g1.white, "testuser")
        self.assertEqual(g1.black, "opponent1")
        self.assertEqual(g1.white_rating, 1550)
        self.assertEqual(g1.black_rating, 1520)
        self.assertEqual(g1.time_control, "300+0")
        self.assertEqual(g1.eco, "B01")
        self.assertEqual(g1.analysis_status, ANALYSIS_NOT_ANALYZED)

    @patch.object(ChessComImporter, "_make_request")
    def test_chesscom_date_filtering(self, mock_req):
        """Verify date filtering skips archives outside requested range."""
        def fake_request(url, headers=None, method="GET"):
            if "archives" in url:
                return CHESSCOM_ARCHIVES_JSON
            elif "2026/10" in url:
                return CHESSCOM_GAMES_OCT_JSON
            raise ValueError(f"Should not have requested url: {url}")

        mock_req.side_effect = fake_request

        importer = ChessComImporter()
        # Request only 2026-10
        result = importer.import_games("testuser", self.lib, since="2026-10-01", until="2026-10-31")

        self.assertEqual(result.games_found, 1)
        self.assertEqual(result.games_imported, 1)
        games = self.lib.list_games()
        self.assertEqual(len(games), 1)
        self.assertEqual(games[0].source_game_id, "10002")

    @patch.object(LichessImporter, "_make_request")
    def test_lichess_valid_import(self, mock_req):
        """Verify parsing multi-game PGN stream and extracting metadata from Lichess."""
        mock_req.return_value = LICHESS_PGN_STREAM

        importer = LichessImporter()
        result = importer.import_games("testuser", self.lib)

        self.assertEqual(result.source, "lichess")
        self.assertEqual(result.games_found, 2)
        self.assertEqual(result.games_imported, 2)
        self.assertEqual(result.duplicates_skipped, 0)
        self.assertEqual(len(result.errors), 0)

        games = self.lib.list_games()
        self.assertEqual(len(games), 2)

        # Check metadata
        g1 = self.lib.get_game(result.imported_game_ids[0])
        self.assertEqual(g1.source, "lichess")
        self.assertEqual(g1.source_game_id, "lich001")
        self.assertEqual(g1.white, "testuser")
        self.assertEqual(g1.white_rating, 1700)
        self.assertEqual(g1.time_control, "180+0")
        self.assertEqual(g1.eco, "C20")
        self.assertEqual(g1.analysis_status, ANALYSIS_NOT_ANALYZED)

        g2 = self.lib.get_game(result.imported_game_ids[1])
        self.assertEqual(g2.source, "lichess")
        self.assertEqual(g2.source_game_id, "lich002")
        self.assertEqual(g2.black, "testuser")
        self.assertEqual(g2.black_rating, 1710)

    @patch.object(ChessComImporter, "_make_request")
    def test_repeated_synchronization_idempotency(self, mock_req):
        """Verify that a second synchronization skips all existing games without duplicates."""
        mock_req.side_effect = lambda url, headers=None, method="GET": (
            CHESSCOM_ARCHIVES_JSON if "archives" in url else CHESSCOM_GAMES_SEPT_JSON
        )

        importer = ChessComImporter()

        # Run 1
        res1 = importer.import_games("testuser", self.lib, since="2026-09", until="2026-09")
        self.assertEqual(res1.games_imported, 1)
        self.assertEqual(res1.duplicates_skipped, 0)
        self.assertEqual(self.lib.count_games(), 1)

        # Run 2 (same input)
        res2 = importer.import_games("testuser", self.lib, since="2026-09", until="2026-09")
        self.assertEqual(res2.games_imported, 0)
        self.assertEqual(res2.duplicates_skipped, 1)
        self.assertEqual(self.lib.count_games(), 1)

    @patch.object(LichessImporter, "_make_request")
    def test_existing_analyzed_game_preserves_analysis_on_duplicate_sync(self, mock_req):
        """Verify that re-importing an existing game does NOT overwrite or erase its analysis."""
        mock_req.return_value = LICHESS_PGN_STREAM

        importer = LichessImporter()
        res1 = importer.import_games("testuser", self.lib)
        game_id = res1.imported_game_ids[0]

        # Attach mock analysis to the game
        self.lib.attach_analysis(game_id, {"schema_version": "1.1.0", "summary": {"tested": True}})
        game_before = self.lib.get_game(game_id)
        self.assertEqual(game_before.analysis_status, ANALYSIS_ANALYZED)

        # Re-import same games from Lichess
        res2 = importer.import_games("testuser", self.lib)
        self.assertEqual(res2.duplicates_skipped, 2)
        self.assertEqual(res2.games_imported, 0)

        # Analysis status and record must be preserved
        game_after = self.lib.get_game(game_id)
        self.assertEqual(game_after.analysis_status, ANALYSIS_ANALYZED)
        analysis_data = self.lib.get_analysis(game_id)
        self.assertIsNotNone(analysis_data)
        self.assertTrue(analysis_data["summary"]["tested"])

    @patch.object(ChessComImporter, "_make_request")
    def test_invalid_user_handling(self, mock_req):
        """Verify graceful error reporting when username does not exist (HTTP 404)."""
        mock_req.side_effect = UserNotFoundError("Resource not found (HTTP 404)")

        importer = ChessComImporter()
        res = importer.import_games("nonexistent_user", self.lib)

        self.assertEqual(res.games_imported, 0)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("not found", res.errors[0].lower())

    @patch.object(LichessImporter, "_make_request")
    def test_rate_limit_handling(self, mock_req):
        """Verify graceful handling when encountering HTTP 429 rate limit."""
        mock_req.side_effect = RateLimitError("Rate limit exceeded (HTTP 429)")

        importer = LichessImporter()
        res = importer.import_games("active_user", self.lib)

        self.assertEqual(res.games_imported, 0)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("429", res.errors[0])

    @patch.object(ChessComImporter, "_make_request")
    def test_network_failure_handling(self, mock_req):
        """Verify network errors (timeout, connection reset) are captured in result.errors."""
        mock_req.side_effect = NetworkError("Network error connecting: Connection refused")

        importer = ChessComImporter()
        res = importer.import_games("testuser", self.lib)

        self.assertEqual(res.games_imported, 0)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("Network error", res.errors[0])

    @patch.object(LichessImporter, "_make_request")
    def test_empty_response_handling(self, mock_req):
        """Verify empty responses produce a clean 0-games result with no errors."""
        mock_req.return_value = b""

        importer = LichessImporter()
        res = importer.import_games("quiet_user", self.lib)

        self.assertEqual(res.games_found, 0)
        self.assertEqual(res.games_imported, 0)
        self.assertEqual(len(res.errors), 0)

    @patch.object(ChessComImporter, "_make_request")
    def test_malformed_pgn_handling(self, mock_req):
        """Verify malformed PGN entries increment invalid_games and do not crash the sync."""
        malformed_json = json.dumps({
            "games": [
                {
                    "url": "https://www.chess.com/game/live/999",
                    "pgn": "GARBAGE NOT A VALID PGN AT ALL !!!",
                    "end_time": 1790899200,
                },
                {
                    "url": "https://www.chess.com/game/live/1000",
                    "pgn": """[Event "Valid"]
[White "P1"]
[Black "P2"]
[Result "1-0"]

1. e4 e5 1-0
""",
                    "end_time": 1790899201,
                }
            ]
        }).encode("utf-8")

        def fake_request(url, headers=None, method="GET"):
            if "archives" in url:
                return json.dumps({"archives": ["https://api.chess.com/pub/player/u/games/2026/10"]}).encode("utf-8")
            return malformed_json

        mock_req.side_effect = fake_request

        importer = ChessComImporter()
        res = importer.import_games("u", self.lib)

        self.assertEqual(res.games_imported, 1)
        self.assertEqual(res.invalid_games, 1)
        self.assertEqual(len(res.warnings), 1)
        self.assertEqual(self.lib.count_games(), 1)

    @patch.object(ChessComImporter, "_make_request")
    def test_import_platform_games_factory(self, mock_req):
        """Verify high-level convenience function import_platform_games works as expected."""
        mock_req.side_effect = lambda url, headers=None, method="GET": (
            CHESSCOM_ARCHIVES_JSON if "archives" in url else CHESSCOM_GAMES_SEPT_JSON
        )

        res = import_platform_games("chess.com", "testuser", self.lib, since="2026-09", until="2026-09")
        self.assertEqual(res.source, "chess.com")
        self.assertEqual(res.games_imported, 1)


if __name__ == "__main__":
    unittest.main()
