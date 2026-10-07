"""
Deterministic unit tests for Opening Detection, Theory Exit, and Repertoire Layer.
"""

import unittest
from pathlib import Path
import sys

# Ensure src is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import GameLibrary
from src.openings import (
    OpeningDatabase,
    OpeningAnalysis,
    RepertoireProfile,
    detect_opening,
    build_player_repertoire,
    build_player_repertoire_from_library,
)

# Small local opening fixture for isolated testing without network
TEST_OPENINGS_FIXTURE = [
    {
        "eco": "C50",
        "name": "Italian Game",
        "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bc4",
        "san": ["e4", "e5", "Nf3", "Nc6", "Bc4"],
        "uci": ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"],
        "epd": "r1bqk1nr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq -",
        "ply": 5,
    },
    {
        "eco": "C54",
        "name": "Italian Game: Classical Variation, Greco Gambit",
        "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4 exd4 6. cxd4 Bb4+ 7. Bd2",
        "san": ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d4", "exd4", "cxd4", "Bb4+", "Bd2"],
        "uci": ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5", "c2c3", "g8f6", "d2d4", "e5d4", "c3d4", "c5b4", "c1d2"],
        "epd": "r1bqk2r/pppp1ppp/2n2n2/8/1b1PP3/5N2/PP1B1PPP/RN1QKB1R b KQkq -",
        "ply": 13,
    },
    {
        "eco": "B12",
        "name": "Caro-Kann Defense: Advance Variation",
        "pgn": "1. e4 c6 2. d4 d5 3. e5 Bf5",
        "san": ["e4", "c6", "d4", "d5", "e5", "Bf5"],
        "uci": ["e2e4", "c7c6", "d2d4", "d7d5", "e4e5", "c8f5"],
        "epd": "rn1qkbnr/pp2pppp/2p5/3pPb2/3P4/8/PPP2PPP/RNBQKBNR w KQkq -",
        "ply": 6,
    },
    {
        "eco": "C00",
        "name": "French Defense",
        "pgn": "1. e4 e6",
        "san": ["e4", "e6"],
        "uci": ["e2e4", "e7e6"],
        "epd": "rnbqkbnr/pppp1ppp/4p3/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -",
        "ply": 2,
    },
    {
        "eco": "C01",
        "name": "French Defense: Exchange Variation",
        "pgn": "1. e4 e6 2. d4 d5 3. exd5 exd5",
        "san": ["e4", "e6", "d4", "d5", "exd5", "exd5"],
        "uci": ["e2e4", "e7e6", "d2d4", "d7d5", "e4d5", "e6d5"],
        "epd": "rnbqkbnr/ppp2ppp/8/3p4/3P4/8/PPP2PPP/RNBQKBNR w KQkq -",
        "ply": 6,
    },
    {
        "eco": "D00",
        "name": "Queen's Pawn Game",
        "pgn": "1. d4 d5",
        "san": ["d4", "d5"],
        "uci": ["d2d4", "d7d5"],
        "epd": "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq -",
        "ply": 2,
    },
    {
        "eco": "A00",
        "name": "Grob Opening",
        "pgn": "1. g4",
        "san": ["g4"],
        "uci": ["g2g4"],
        "epd": "rnbqkbnr/pppppppp/8/8/6P1/8/PPPPPP1P/RNBQKBNR b KQkq -",
        "ply": 1,
    },
]


class TestOpeningDetectionAndRepertoire(unittest.TestCase):
    """
    Test suite for opening identification, variation matching, transposition,
    theory exit, and multi-game repertoire aggregation.
    """

    def setUp(self):
        # Use isolated in-memory OpeningDatabase with local test fixture
        self.db = OpeningDatabase(openings_data=TEST_OPENINGS_FIXTURE)
        self.lib = GameLibrary(":memory:")

    def tearDown(self):
        self.lib.close()

    def test_known_standard_opening(self):
        """Verify identification of standard opening (Italian Game C50)."""
        pgn = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O Nf6"
        res = self.db.detect_opening(pgn)
        self.assertEqual(res.opening_name, "Italian Game")
        self.assertEqual(res.eco, "C50")
        self.assertEqual(res.matched_ply, 5)
        self.assertEqual(res.matched_moves, ["e4", "e5", "Nf3", "Nc6", "Bc4"])
        self.assertEqual(res.theory_exit_ply, 6)
        self.assertEqual(res.theory_exit_move, "3... Bc5")
        self.assertEqual(res.divergence_side, "Black")

    def test_known_variation(self):
        """Verify identification of detailed variation (Greco Gambit C54)."""
        pgn = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4 exd4 6. cxd4 Bb4+ 7. Bd2 Bxd2+ 8. Nbxd2"
        res = self.db.detect_opening(pgn)
        self.assertEqual(res.opening_name, "Italian Game")
        self.assertEqual(res.variation_name, "Classical Variation, Greco Gambit")
        self.assertEqual(res.eco, "C54")
        self.assertEqual(res.matched_ply, 13)
        self.assertEqual(res.theory_exit_ply, 14)
        self.assertEqual(res.theory_exit_move, "7... Bxd2+")
        self.assertEqual(res.confidence, "high")

    def test_incomplete_opening_sequence(self):
        """Verify handling of incomplete opening line (1 ply only)."""
        pgn = "1. e4"
        res = self.db.detect_opening(pgn)
        # 1. e4 matches French/Italian root or A00/B00
        # In our fixture, only full lines exist or French 1.e4 e6, so 1.e4 has ply=0 match in this small fixture
        # But for Grob (1. g4) which is 1 ply in fixture:
        res_grob = self.db.detect_opening("1. g4")
        self.assertEqual(res_grob.opening_name, "Grob Opening")
        self.assertEqual(res_grob.matched_ply, 1)
        self.assertIsNone(res_grob.theory_exit_ply)  # Game ended in theory

    def test_transposition_recognition(self):
        """
        Verify that French Defense: Exchange Variation reached via transposition
        (1. d4 e6 2. e4 d5 3. exd5 exd5) is detected by position matching.
        """
        # Transposed move order: 1. d4 e6 2. e4 d5 3. exd5 exd5 (normal is 1. e4 e6 2. d4 d5 3. exd5 exd5)
        pgn = "1. d4 e6 2. e4 d5 3. exd5 exd5 4. Nf3 Nf6"
        res = self.db.detect_opening(pgn)
        self.assertEqual(res.opening_name, "French Defense")
        self.assertEqual(res.variation_name, "Exchange Variation")
        self.assertEqual(res.eco, "C01")
        self.assertTrue(res.is_transposition)
        self.assertEqual(res.matched_ply, 6)
        self.assertEqual(res.theory_exit_ply, 7)
        self.assertEqual(res.theory_exit_move, "4. Nf3")

    def test_unknown_or_unrecognized_line(self):
        """Verify unrecognized line produces safe fallback without crashing."""
        pgn = "1. h4 h5 2. a4 a5 3. Rh3 Rh6"
        res = self.db.detect_opening(pgn)
        self.assertEqual(res.opening_name, "Unrecognized Opening")
        self.assertEqual(res.matched_ply, 0)
        self.assertEqual(res.confidence, "unrecognized")
        self.assertEqual(res.theory_exit_ply, 1)
        self.assertEqual(res.theory_exit_move, "1. h4")

    def test_external_eco_mismatch_uses_move_evidence(self):
        """
        Verify that if PGN header claims [ECO "A00"] but moves are 1. e4 c6 2. d4 d5 3. e5 Bf5,
        the detector identifies Caro-Kann B12 based on played moves rather than trusting header.
        """
        pgn = """[Event "Casual"]
[Site "Chess.com"]
[Date "2026.10.01"]
[White "Player1"]
[Black "Player2"]
[Result "1-0"]
[ECO "A00"]

1. e4 c6 2. d4 d5 3. e5 Bf5 4. Nf3 e6 1-0
"""
        res = self.db.detect_opening(pgn)
        self.assertEqual(res.opening_name, "Caro-Kann Defense")
        self.assertEqual(res.variation_name, "Advance Variation")
        self.assertEqual(res.eco, "B12")
        self.assertNotEqual(res.eco, "A00")

    def test_white_and_black_repertoire_aggregation(self):
        """Verify grouping of repertoire for White (by first move) and Black (by opponent first move)."""
        games = [
            # Game 1: Hero White playing Italian Game
            {
                "game_id": "g1",
                "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. c3 Nf6 5. d4 exd4 6. cxd4 Bb4+ 7. Bd2 d6 1-0",
                "metadata": {"white": "Hero", "black": "Opponent", "result": "1-0"},
                "moves": [
                    {"move_number": 1, "color": "White", "centipawn_loss": 5, "is_critical": False},
                    {"move_number": 2, "color": "White", "centipawn_loss": 10, "is_critical": False},
                    {"move_number": 3, "color": "White", "centipawn_loss": 0, "is_critical": False},
                    {"move_number": 4, "color": "White", "centipawn_loss": 15, "is_critical": False},
                    {"move_number": 5, "color": "White", "centipawn_loss": 0, "is_critical": False},
                    {"move_number": 6, "color": "White", "centipawn_loss": 0, "is_critical": False},
                    {"move_number": 7, "color": "White", "centipawn_loss": 0, "is_critical": False}, # ply 13 in theory
                    {"move_number": 8, "color": "White", "centipawn_loss": 80, "is_critical": True},  # ply 15 out of theory
                ]
            },
            # Game 2: Hero Black facing 1. d4 (Queen's Pawn)
            {
                "game_id": "g2",
                "pgn": "1. d4 d5 2. c4 c6 0-1",
                "metadata": {"white": "Opponent", "black": "Hero", "result": "0-1"},
                "moves": [
                    {"move_number": 1, "color": "Black", "centipawn_loss": 0, "is_critical": False},
                    {"move_number": 2, "color": "Black", "centipawn_loss": 120, "is_critical": True},
                ]
            }
        ]

        profile = build_player_repertoire(games, target_player="Hero", opening_db=self.db)
        self.assertEqual(profile.total_games, 2)
        self.assertEqual(profile.white_games, 1)
        self.assertEqual(profile.black_games, 1)

        # White repertoire group
        self.assertIn("1. e4", profile.white_repertoire)
        white_entry = profile.white_repertoire["1. e4"][0]
        self.assertEqual(white_entry.opening_name, "Italian Game")
        self.assertEqual(white_entry.wins, 1)
        self.assertEqual(white_entry.win_rate, 1.0)
        # Before theory vs after theory metrics
        self.assertIsNotNone(white_entry.before_theory.avg_cpl)
        self.assertEqual(white_entry.before_theory.critical_mistakes, 0)
        self.assertEqual(white_entry.after_theory.critical_mistakes, 1)

        # Black repertoire group
        self.assertIn("vs 1. d4", profile.black_repertoire)
        black_entry = profile.black_repertoire["vs 1. d4"][0]
        self.assertEqual(black_entry.opening_name, "Queen's Pawn Game")
        self.assertEqual(black_entry.wins, 1)

    def test_insufficient_evidence_for_primary_repertoire(self):
        """Verify that 1 game is classified as 'observed_opening' rather than 'primary_line'."""
        games = [
            {
                "game_id": "g1",
                "pgn": "1. e4 c6 2. d4 d5 3. e5 Bf5 1-0",
                "metadata": {"white": "Hero", "black": "Opponent", "result": "1-0"},
                "moves": []
            }
        ]
        profile = build_player_repertoire(games, target_player="Hero", opening_db=self.db)
        entry = profile.white_repertoire["1. e4"][0]
        self.assertEqual(entry.repertoire_status, "observed_opening")
        self.assertIn("Insufficient evidence", entry.status_description)

    def test_recurring_and_primary_repertoire_thresholds(self):
        """Verify that 4+ games upgrade an opening to 'primary_line'."""
        games = []
        for i in range(4):
            games.append({
                "game_id": f"g_{i}",
                "pgn": "1. e4 c6 2. d4 d5 3. e5 Bf5 1-0",
                "metadata": {"white": "Hero", "black": f"Opp_{i}", "result": "1-0"},
                "moves": []
            })
        profile = build_player_repertoire(games, target_player="Hero", opening_db=self.db)
        entry = profile.white_repertoire["1. e4"][0]
        self.assertEqual(entry.repertoire_status, "primary_line")
        self.assertIn("Primary repertoire choice", entry.status_description)

    def test_games_without_analysis_handled_gracefully(self):
        """Verify unanalyzed games (no engine moves) are aggregated without crashing."""
        games = [
            {
                "game_id": "unanalysed_1",
                "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bc4 1/2-1/2",
                "metadata": {"white": "Hero", "black": "Friend", "result": "1/2-1/2"},
                # moves is empty
                "moves": []
            }
        ]
        profile = build_player_repertoire(games, target_player="Hero", opening_db=self.db)
        entry = profile.white_repertoire["1. e4"][0]
        self.assertEqual(entry.total_games, 1)
        self.assertEqual(entry.analyzed_games, 0)
        self.assertIsNone(entry.avg_cpl_overall)
        self.assertEqual(entry.draws, 1)
        self.assertIsNone(entry.before_theory.avg_cpl)
        self.assertIsNone(entry.after_theory.avg_cpl)

    def test_gamelibrary_opening_persistence_and_cascade(self):
        """Verify storing opening in GameLibrary, auto-detection, and cascading deletion."""
        sample_pgn = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 1-0"
        game, is_new = self.lib.add_game(sample_pgn)

        # Detect and store opening
        analysis = self.db.detect_opening(sample_pgn)
        stored = self.lib.set_game_opening(game.game_id, analysis)
        self.assertTrue(stored)

        # Retrieve stored opening
        retrieved = self.lib.get_game_opening(game.game_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["opening_name"], "Italian Game")
        self.assertEqual(retrieved["eco"], "C50")

        # Verify cascading deletion
        self.lib.delete_game(game.game_id)
        self.assertIsNone(self.lib.get_game_opening(game.game_id))

    def test_build_repertoire_directly_from_gamelibrary(self):
        """Verify building RepertoireProfile directly from a GameLibrary instance."""
        pgn1 = """[Event "Casual"]
[White "Hero"]
[Black "Opponent"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 d6 1-0
"""
        self.lib.add_game(pgn1)

        profile = build_player_repertoire_from_library(self.lib, target_player="Hero", opening_db=self.db)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.total_games, 1)
        self.assertEqual(profile.white_games, 1)


if __name__ == "__main__":
    unittest.main()
