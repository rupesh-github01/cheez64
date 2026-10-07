"""
Unit tests for Game Library & Analysis Management Layer.
"""

import unittest
from pathlib import Path
import sys
import tempfile
import shutil

# Ensure src is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root / "src") not in sys.path:
    sys.path.insert(0, str(repo_root / "src"))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import (
    GameLibrary,
    Game,
    GameAnalysisRecord,
    compute_game_fingerprint,
    extract_game_metadata,
    import_existing_games,
    ANALYSIS_NOT_ANALYZED,
    ANALYSIS_ANALYZING,
    ANALYSIS_ANALYZED,
    ANALYSIS_FAILED,
)

SAMPLE_PGN_1 = """[Event "Casual Game"]
[Site "Chess.com"]
[Date "2026.10.01"]
[Round "1"]
[White "PlayerOne"]
[Black "PlayerTwo"]
[Result "1-0"]
[WhiteElo "1500"]
[BlackElo "1480"]
[TimeControl "300+0"]
[ECO "C00"]
[Link "https://www.chess.com/game/live/12345678"]

1. e4 e6 2. d4 d5 3. e5 c5 4. c3 Nc6 5. Nf3 Qb6 6. a3 Nh6 7. b4 cxd4 8. cxd4 Nf5 9. Bb2 Bd7 10. g4 Nfe7 1-0
"""

SAMPLE_PGN_1_WITH_CLOCKS = """[Event "Casual Game"]
[Site "Chess.com"]
[Date "2026.10.01"]
[Round "?"]
[White "PlayerOne"]
[Black "PlayerTwo"]
[Result "1-0"]
[WhiteElo "1500"]
[BlackElo "1480"]
[TimeControl "300+0"]
[ECO "C00"]

1. e4 {[%clk 0:05:00]} 1... e6 {[%clk 0:04:58]} 2. d4 {[%clk 0:04:55]} 2... d5 {[%clk 0:04:50]} 
3. e5 {[%clk 0:04:52]} 3... c5 {[%clk 0:04:45]} 4. c3 {[%clk 0:04:50]} 4... Nc6 {[%clk 0:04:40]} 
5. Nf3 {[%clk 0:04:45]} 5... Qb6 {[%clk 0:04:35]} 6. a3 {[%clk 0:04:40]} 6... Nh6 {[%clk 0:04:30]} 
7. b4 {[%clk 0:04:38]} 7... cxd4 {[%clk 0:04:25]} 8. cxd4 {[%clk 0:04:35]} 8... Nf5 {[%clk 0:04:20]} 
9. Bb2 {[%clk 0:04:30]} 9... Bd7 {[%clk 0:04:15]} 10. g4 {[%clk 0:04:25]} 10... Nfe7 {[%clk 0:04:10]} 1-0
"""

SAMPLE_PGN_2 = """[Event "Tournament Round 1"]
[Site "Local Club"]
[Date "2026.10.02"]
[Round "1"]
[White "PlayerThree"]
[Black "PlayerOne"]
[Result "0-1"]
[WhiteElo "1600"]
[BlackElo "1510"]
[TimeControl "600+5"]
[ECO "B01"]

1. e4 d5 2. exd5 Qxd5 3. Nc3 Qa5 4. d4 Nf6 5. Nf3 c6 6. Bc4 Bf5 7. Bd2 e6 8. Qe2 Bb4 9. O-O-O Nbd7 10. a3 Bxc3 0-1
"""


class TestGameLibrary(unittest.TestCase):
    """Deterministic tests for GameLibrary operations, fingerprinting, and analysis attachment."""

    def setUp(self):
        # Use an in-memory SQLite database for test isolation and speed
        self.lib = GameLibrary(":memory:")

    def tearDown(self):
        if hasattr(self, "lib") and self.lib:
            self.lib.close()

    def test_add_and_retrieve_game(self):
        """Verify adding a game and retrieving it by game_id."""
        game, is_new = self.lib.add_game(SAMPLE_PGN_1)
        self.assertTrue(is_new)
        self.assertIsNotNone(game.game_id)
        self.assertEqual(game.white, "PlayerOne")
        self.assertEqual(game.black, "PlayerTwo")
        self.assertEqual(game.result, "1-0")
        self.assertEqual(game.date, "2026.10.01")
        self.assertEqual(game.event, "Casual Game")
        self.assertEqual(game.site, "Chess.com")
        self.assertEqual(game.source, "chess.com")
        self.assertEqual(game.source_game_id, "12345678")
        self.assertEqual(game.white_rating, 1500)
        self.assertEqual(game.black_rating, 1480)
        self.assertEqual(game.eco, "C00")
        self.assertEqual(game.analysis_status, ANALYSIS_NOT_ANALYZED)
        self.assertIsNone(game.analysis_reference)

        retrieved = self.lib.get_game(game.game_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.game_id, game.game_id)
        self.assertEqual(retrieved.fingerprint, game.fingerprint)

    def test_duplicate_detection_identical_pgn(self):
        """Verify that adding identical PGN twice returns the existing game without duplication."""
        game1, is_new1 = self.lib.add_game(SAMPLE_PGN_1)
        self.assertTrue(is_new1)

        game2, is_new2 = self.lib.add_game(SAMPLE_PGN_1)
        self.assertFalse(is_new2)
        self.assertEqual(game1.game_id, game2.game_id)
        self.assertEqual(self.lib.count_games(), 1)

    def test_duplicate_detection_harmless_metadata_differences(self):
        """
        Verify that a game with clock comments or round differences is recognized
        as the exact same game by fingerprint.
        """
        game1, is_new1 = self.lib.add_game(SAMPLE_PGN_1)
        self.assertTrue(is_new1)

        # Same game, but with [%clk] annotations and Round='?' instead of '1'
        game2, is_new2 = self.lib.add_game(SAMPLE_PGN_1_WITH_CLOCKS)
        self.assertFalse(is_new2, "Same game with clock comments must be recognized as duplicate.")
        self.assertEqual(game1.game_id, game2.game_id)
        self.assertEqual(self.lib.count_games(), 1)

    def test_genuinely_different_games(self):
        """Verify that different games have distinct fingerprints and both are added."""
        game1, is_new1 = self.lib.add_game(SAMPLE_PGN_1)
        game2, is_new2 = self.lib.add_game(SAMPLE_PGN_2)

        self.assertTrue(is_new1)
        self.assertTrue(is_new2)
        self.assertNotEqual(game1.game_id, game2.game_id)
        self.assertNotEqual(game1.fingerprint, game2.fingerprint)
        self.assertEqual(self.lib.count_games(), 2)

    def test_list_games_and_filtering(self):
        """Verify listing and filtering by player, color, result, source, and analysis status."""
        g1, _ = self.lib.add_game(SAMPLE_PGN_1)
        g2, _ = self.lib.add_game(SAMPLE_PGN_2)

        # Filter by player
        player_one_games = self.lib.list_games(player="PlayerOne")
        self.assertEqual(len(player_one_games), 2)

        # Filter by player and color
        white_games = self.lib.list_games(player="PlayerOne", color="White")
        self.assertEqual(len(white_games), 1)
        self.assertEqual(white_games[0].game_id, g1.game_id)

        black_games = self.lib.list_games(player="PlayerOne", color="Black")
        self.assertEqual(len(black_games), 1)
        self.assertEqual(black_games[0].game_id, g2.game_id)

        # Filter by result
        wins_white = self.lib.list_games(result="1-0")
        self.assertEqual(len(wins_white), 1)
        self.assertEqual(wins_white[0].game_id, g1.game_id)

        # Filter by date range
        date_filtered = self.lib.list_games(date_from="2026.10.02")
        self.assertEqual(len(date_filtered), 1)
        self.assertEqual(date_filtered[0].game_id, g2.game_id)

        # Filter by status
        unanalyzed = self.lib.list_games(analysis_status=ANALYSIS_NOT_ANALYZED)
        self.assertEqual(len(unanalyzed), 2)

    def test_game_analysis_separation_and_attach(self):
        """
        Verify that a game can exist unanalyzed, and an analysis can be attached,
        retrieved, and removed independently.
        """
        game, _ = self.lib.add_game(SAMPLE_PGN_1)
        self.assertEqual(game.analysis_status, ANALYSIS_NOT_ANALYZED)
        self.assertIsNone(self.lib.get_analysis(game.game_id))

        # Attach mock analysis data
        mock_analysis = {
            "schema_version": "1.1.0",
            "summary": {
                "critical_positions_count": 3,
                "critical_episodes_count": 1,
            },
            "moves": [{"move_number": 1, "played_move": "e4", "is_critical": False}]
        }
        success = self.lib.attach_analysis(
            game_id=game.game_id,
            analysis_data=mock_analysis,
            file_path="data/games/sample_analysis.json",
            engine_depth=16,
        )
        self.assertTrue(success)

        # Verify updated game status
        updated_game = self.lib.get_game(game.game_id)
        self.assertEqual(updated_game.analysis_status, ANALYSIS_ANALYZED)
        self.assertEqual(updated_game.analysis_reference, "data/games/sample_analysis.json")
        self.assertIsNotNone(updated_game.analyzed_at)

        # Retrieve analysis payload
        retrieved_analysis = self.lib.get_analysis(game.game_id)
        self.assertIsNotNone(retrieved_analysis)
        self.assertEqual(retrieved_analysis["schema_version"], "1.1.0")
        self.assertEqual(retrieved_analysis["summary"]["critical_positions_count"], 3)

        # Retrieve analysis record
        record = self.lib.get_analysis_record(game.game_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.critical_count, 3)
        self.assertEqual(record.engine_depth, 16)

        # Remove analysis
        removed = self.lib.remove_analysis(game.game_id)
        self.assertTrue(removed)
        game_after_remove = self.lib.get_game(game.game_id)
        self.assertEqual(game_after_remove.analysis_status, ANALYSIS_NOT_ANALYZED)
        self.assertIsNone(game_after_remove.analysis_reference)
        self.assertIsNone(self.lib.get_analysis(game.game_id))

    def test_analysis_lifecycle_states(self):
        """Verify transitioning through NOT_ANALYZED -> ANALYZING -> FAILED -> ANALYZED."""
        game, _ = self.lib.add_game(SAMPLE_PGN_1)
        self.assertEqual(game.analysis_status, ANALYSIS_NOT_ANALYZED)

        # Transition to ANALYZING
        self.lib.set_analysis_status(game.game_id, ANALYSIS_ANALYZING)
        self.assertEqual(self.lib.get_game(game.game_id).analysis_status, ANALYSIS_ANALYZING)

        # Transition to FAILED
        self.lib.set_analysis_status(game.game_id, ANALYSIS_FAILED)
        self.assertEqual(self.lib.get_game(game.game_id).analysis_status, ANALYSIS_FAILED)

        # Transition to ANALYZED
        self.lib.set_analysis_status(game.game_id, ANALYSIS_ANALYZED)
        self.assertEqual(self.lib.get_game(game.game_id).analysis_status, ANALYSIS_ANALYZED)

    def test_update_game_metadata(self):
        """Verify updating mutable metadata fields."""
        game, _ = self.lib.add_game(SAMPLE_PGN_1)
        updated = self.lib.update_game_metadata(
            game.game_id,
            event="World Championship",
            white_rating=1600,
            black_rating=1550,
            round="5",
        )
        self.assertEqual(updated.event, "World Championship")
        self.assertEqual(updated.white_rating, 1600)
        self.assertEqual(updated.black_rating, 1550)
        self.assertEqual(updated.round, "5")

    def test_delete_game(self):
        """Verify deleting a game cascades and removes analysis."""
        game, _ = self.lib.add_game(SAMPLE_PGN_1)
        self.lib.attach_analysis(game.game_id, {"schema_version": "1.1.0"})

        deleted = self.lib.delete_game(game.game_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.lib.get_game(game.game_id))
        self.assertIsNone(self.lib.get_analysis(game.game_id))
        self.assertEqual(self.lib.count_games(), 0)

    def test_idempotent_migration(self):
        """
        Verify repeatable and idempotent import process against a directory of PGNs.
        """
        temp_dir = tempfile.mkdtemp()
        try:
            # Create two PGNs and one matching analysis JSON
            p1 = Path(temp_dir) / "game_a.pgn"
            p1.write_text(SAMPLE_PGN_1, encoding="utf-8")

            p1_analysis = Path(temp_dir) / "game_a_analysis.json"
            p1_analysis.write_text('{"schema_version": "1.1.0", "moves": []}', encoding="utf-8")

            p2 = Path(temp_dir) / "game_b.pgn"
            p2.write_text(SAMPLE_PGN_2, encoding="utf-8")

            # First migration run
            res1 = import_existing_games(self.lib, temp_dir)
            self.assertEqual(res1["imported_games"], 2)
            self.assertEqual(res1["duplicates_skipped"], 0)
            self.assertEqual(res1["analyses_attached"], 1)
            self.assertEqual(self.lib.count_games(), 2)

            # Second migration run (idempotent)
            res2 = import_existing_games(self.lib, temp_dir)
            self.assertEqual(res2["imported_games"], 0)
            self.assertEqual(res2["duplicates_skipped"], 2)
            self.assertEqual(res2["analyses_attached"], 1)
            self.assertEqual(self.lib.count_games(), 2)

        finally:
            shutil.rmtree(temp_dir)

    def test_build_player_profile_from_library(self):
        """Verify building player profile directly from games stored in the library."""
        from src.profile import build_player_profile_from_library

        # Add game and attach sample analysis
        game, _ = self.lib.add_game(SAMPLE_PGN_1)
        sample_analysis = {
            "schema_version": "1.1.0",
            "game_id": game.game_id,
            "metadata": {
                "white": "PlayerOne",
                "black": "PlayerTwo",
                "result": "1-0",
                "date": "2026.10.01"
            },
            "summary": {
                "total_moves": 20,
                "white_moves": 10,
                "black_moves": 10,
                "critical_positions": 1,
                "white_cpl": 15.0,
                "black_cpl": 45.0
            },
            "moves": [
                {
                    "move_number": 1,
                    "color": "White",
                    "played_move": "e4",
                    "best_move": "e4",
                    "centipawn_loss": 0,
                    "is_critical": False,
                    "classification": "best_move",
                    "tactical_opportunity": None,
                    "tactical_evidence": None,
                    "explanation": None
                }
            ],
            "episodes": []
        }
        self.lib.attach_analysis(game.game_id, sample_analysis)

        profile = build_player_profile_from_library(self.lib, target_player="PlayerOne")
        self.assertEqual(profile.player_name, "PlayerOne")
        self.assertEqual(profile.games_analyzed, 1)
        self.assertEqual(profile.moves_analyzed, 1)


if __name__ == "__main__":
    unittest.main()
