import unittest
from pathlib import Path
import sys

# Ensure src is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root / "src") not in sys.path:
    sys.path.insert(0, str(repo_root / "src"))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.profile import (
    build_player_profile,
    calculate_weakness_score,
    is_endgame_position,
    classify_coaching_category,
    generate_human_readable_profile,
    PlayerProfile,
    ProfileWeakness,
    ProfileStrength,
)


class TestPlayerProfile(unittest.TestCase):
    """Deterministic tests for the player weakness & strength profiling layer."""

    def _create_mock_move(
        self,
        move_number: int = 1,
        color: str = "White",
        played_move: str = "e4",
        best_move: str = "e4",
        cpl: int = 0,
        is_critical: bool = False,
        category: str = "unclassified",
        confidence: str = "high",
        episode_index: int = None,
        is_endgame: bool = False,
        evidence: dict = None,
    ):
        """Helper to create a structured move dict for testing."""
        pf = {
            "piece_counts": {
                "white": {"queen": 0 if is_endgame else 1},
                "black": {"queen": 0 if is_endgame else 1},
            },
            "material": {"white": 10 if is_endgame else 39, "black": 10 if is_endgame else 39},
            "captures_available": ["Nxe4"] if "x" in played_move else [],
            "checks_available": ["Qf5+"] if "+" in played_move else [],
        }
        tf = {
            "category": category,
            "confidence": confidence,
            "evidence": evidence or {},
        }
        return {
            "move_number": move_number,
            "color": color,
            "played_move": played_move,
            "best_move": best_move,
            "centipawn_loss": cpl,
            "is_critical": is_critical,
            "episode_index": episode_index,
            "position_features": pf,
            "tactical_finding": tf,
            "explanation": {"summary": f"Move {played_move} test explanation"},
        }

    def test_repeated_weakness_across_games(self):
        """Verify that a weakness occurring across multiple games is properly aggregated."""
        game1 = {
            "metadata": {"white": "Hero", "black": "Opponent1"},
            "moves": [
                self._create_mock_move(10, "White", "Nf3", "Be2", 150, True, "material_loss", episode_index=1)
            ]
        }
        game2 = {
            "metadata": {"white": "Hero", "black": "Opponent2"},
            "moves": [
                self._create_mock_move(12, "White", "Rad1", "Re1", 200, True, "material_loss", episode_index=1)
            ]
        }
        profile = build_player_profile([game1, game2], target_player="Hero")

        self.assertEqual(profile.games_analyzed, 2)
        self.assertEqual(profile.critical_positions, 2)
        self.assertTrue(any("Material Loss" in w.category for w in profile.weaknesses))

        mat_loss_w = next(w for w in profile.weaknesses if "Material Loss" in w.category)
        self.assertEqual(mat_loss_w.distinct_games, 2)
        self.assertEqual(mat_loss_w.games_ratio, "2/2")
        self.assertEqual(mat_loss_w.occurrences, 2)

    def test_same_weakness_multiple_times_in_one_game_vs_cross_game(self):
        """
        Verify that 6 occurrences in 1 game do NOT outrank 4 occurrences across 4 distinct games.
        """
        score_single_game = calculate_weakness_score(
            episode_occurrences=2,  # 6 moves clustered in 2 episodes
            distinct_games=1,
            total_games=5,
            average_cpl=200,
            confidence_level="high",
        )
        score_cross_game = calculate_weakness_score(
            episode_occurrences=4,  # 4 moves in 4 distinct episodes
            distinct_games=4,
            total_games=5,
            average_cpl=200,
            confidence_level="high",
        )
        self.assertGreater(
            score_cross_game,
            score_single_game,
            "A recurring weakness across 4 games must outrank clustered errors in 1 game."
        )

    def test_episode_deduplication(self):
        """
        Verify that 4 consecutive mistakes in the same episode count as 1 episode occurrence.
        """
        moves = [
            self._create_mock_move(15, "White", "d5", "Nf3", 150, True, "material_loss", episode_index=1),
            self._create_mock_move(16, "White", "e5", "Bc4", 250, True, "material_loss", episode_index=1),
            self._create_mock_move(17, "White", "f5", "O-O", 180, True, "material_loss", episode_index=1),
        ]
        game = {
            "metadata": {"white": "Hero", "black": "Opponent"},
            "moves": moves
        }
        profile = build_player_profile([game], target_player="Hero")
        mat_loss_w = next(w for w in profile.weaknesses if "Material Loss" in w.category)

        self.assertEqual(mat_loss_w.occurrences, 3, "Raw occurrences must count all 3 moves.")
        self.assertEqual(mat_loss_w.episode_occurrences, 1, "Episode deduplication must collapse to 1 episode.")

    def test_severity_weighting(self):
        """
        Verify that a blunder with 400 CPL receives higher ranking score than an inaccuracy with 80 CPL.
        """
        score_blunder = calculate_weakness_score(
            episode_occurrences=2,
            distinct_games=2,
            total_games=5,
            average_cpl=400,
            confidence_level="high",
        )
        score_inaccuracy = calculate_weakness_score(
            episode_occurrences=2,
            distinct_games=2,
            total_games=5,
            average_cpl=80,
            confidence_level="high",
        )
        self.assertGreater(score_blunder, score_inaccuracy)

    def test_cross_game_recurrence(self):
        """
        Verify that recurrence across 4/5 games produces higher multiplier than 1/5 games.
        """
        score_high_rec = calculate_weakness_score(
            episode_occurrences=3,
            distinct_games=4,
            total_games=5,
            average_cpl=150,
            confidence_level="high",
        )
        score_low_rec = calculate_weakness_score(
            episode_occurrences=3,
            distinct_games=1,
            total_games=5,
            average_cpl=150,
            confidence_level="high",
        )
        self.assertGreater(score_high_rec, score_low_rec)

    def test_ambiguous_classification(self):
        """
        Verify that moves with unclassified or ambiguous findings are handled gracefully.
        """
        game = {
            "metadata": {"white": "Hero", "black": "Opponent"},
            "moves": [
                self._create_mock_move(20, "White", "a3", "Nf3", 120, True, "unclassified", confidence="low", episode_index=1),
            ]
        }
        profile = build_player_profile([game], target_player="Hero")
        self.assertEqual(len(profile.weaknesses), 1)
        self.assertEqual(profile.weaknesses[0].pillar, "Strategic & Positional")

    def test_preliminary_confidence(self):
        """
        Verify confidence level thresholds: 5 games = preliminary, 15 games = high.
        """
        games_5 = [
            {"metadata": {"white": "Hero", "black": f"Opp{i}"}, "moves": []}
            for i in range(5)
        ]
        profile_5 = build_player_profile(games_5, target_player="Hero")
        self.assertEqual(profile_5.confidence.level, "preliminary")
        self.assertIn("Preliminary — based on 5 games", profile_5.confidence.rationale)

        games_15 = [
            {"metadata": {"white": "Hero", "black": f"Opp{i}"}, "moves": []}
            for i in range(15)
        ]
        profile_15 = build_player_profile(games_15, target_player="Hero")
        self.assertEqual(profile_15.confidence.level, "high")

    def test_positive_strengths(self):
        """
        Verify that defensible strengths (low-error games, opening stability, endgame conversion)
        are properly generated with concrete evidence.
        """
        # Create a clean game with 25 moves, low CPL, and 0 blunders
        moves = [
            self._create_mock_move(i, "White", f"Nf{i % 4 + 1}", f"Nf{i % 4 + 1}", cpl=5, is_critical=False)
            for i in range(1, 26)
        ]
        game = {
            "metadata": {"white": "Hero", "black": "Opponent", "result": "1-0"},
            "moves": moves
        }
        profile = build_player_profile([game], target_player="Hero")
        self.assertTrue(len(profile.strengths) >= 1)
        clean_str = next((s for s in profile.strengths if "Low-Error Performance" in s.area), None)
        self.assertIsNotNone(clean_str)
        self.assertIn("average CPL", clean_str.description)
        self.assertNotIn("master-level", clean_str.description.lower())

    def test_material_accounting_distinction(self):
        """
        Regression test: Verify that gross material lost, gross recaptured,
        actual net material lost, and opportunity cost are strictly distinguished.
        """
        # A move where player trades rooks (gross lost: 5, gross won: 5, net: 0, opp cost: 3)
        ev_even_trade = {
            "net_material_loss": 3,
            "played_line_net": 0,
            "played_captures": [
                {"move": "Rxe1+", "captured_piece": "rook", "captured_value": 5, "by_color": "White"},
                {"move": "Rxe1", "captured_piece": "rook", "captured_value": 5, "by_color": "Black"}
            ]
        }
        # A move where player loses a knight unreciprocated (gross lost: 3, gross won: 0, net: 3, opp cost: 3)
        ev_knight_loss = {
            "net_material_loss": 3,
            "played_line_net": -3,
            "played_captures": [
                {"move": "bxc4", "captured_piece": "knight", "captured_value": 3, "by_color": "Black"}
            ]
        }
        game = {
            "metadata": {"white": "Hero", "black": "Opponent"},
            "moves": [
                self._create_mock_move(10, "White", "Rad1", "Re1", 150, True, "material_loss", episode_index=1, evidence=ev_even_trade),
                self._create_mock_move(20, "White", "Kg8", "Bc5", 250, True, "material_loss", episode_index=2, evidence=ev_knight_loss),
            ]
        }
        profile = build_player_profile([game], target_player="Hero")
        mat = profile.material_summary

        self.assertEqual(mat.gross_material_lost, 8, "5 (rook) + 3 (knight) = 8 gross points lost.")
        self.assertEqual(mat.gross_material_captured, 5, "5 (rook recaptured) = 5 gross points captured.")
        self.assertEqual(mat.actual_net_material_loss, 3, "Only the knight (3 pts) was a net loss in played lines.")
        self.assertEqual(mat.opportunity_cost_loss, 6, "3 (even trade deficit) + 3 (knight deficit) = 6.")
        self.assertEqual(mat.unreciprocated_pieces_lost.get("knight"), 1)
        self.assertNotIn("rook", mat.unreciprocated_pieces_lost, "Even trade rook must not appear in unreciprocated losses.")

    def test_episodes_count_across_distinct_games(self):
        """
        Regression test: Verify that episode indices from different games are not collapsed together.
        Game 1 episode 1 and Game 2 episode 1 must count as 2 distinct episodes.
        """
        game1 = {
            "metadata": {"white": "Hero", "black": "Opp1"},
            "moves": [
                self._create_mock_move(10, "White", "d5", "Nf3", 150, True, "material_loss", episode_index=1)
            ]
        }
        game2 = {
            "metadata": {"white": "Hero", "black": "Opp2"},
            "moves": [
                self._create_mock_move(10, "White", "d5", "Nf3", 150, True, "material_loss", episode_index=1)
            ]
        }
        profile = build_player_profile([game1, game2], target_player="Hero")
        self.assertEqual(profile.episodes_count, 2, "Episode 1 in Game 1 and Episode 1 in Game 2 must be 2 distinct episodes.")

    def test_language_discipline_no_rating_claims(self):
        """
        Regression test: Verify that generated text summaries contain no rating or master-level claims.
        """
        moves = [
            self._create_mock_move(i, "White", f"Nf{i % 4 + 1}", f"Nf{i % 4 + 1}", cpl=5, is_critical=False)
            for i in range(1, 26)
        ]
        game = {
            "metadata": {"white": "Hero", "black": "Opponent", "result": "1-0"},
            "moves": moves
        }
        profile = build_player_profile([game], target_player="Hero")
        summary_text = generate_human_readable_profile(profile)

        forbidden_phrases = ["master-level", "grandmaster", "expert-level", "elo rating", "mastery"]
        for phrase in forbidden_phrases:
            self.assertNotIn(phrase, summary_text.lower(), f"Forbidden phrase '{phrase}' found in summary text.")

    def test_empty_and_small_dataset(self):
        """
        Verify that empty game input produces safe empty profile without crashing.
        """
        profile = build_player_profile([], target_player="Hero")
        self.assertEqual(profile.games_analyzed, 0)
        self.assertEqual(profile.moves_analyzed, 0)
        self.assertEqual(profile.critical_positions, 0)
        self.assertEqual(len(profile.weaknesses), 0)
        self.assertEqual(len(profile.strengths), 0)

        # Valid serialization and formatting
        profile_dict = profile.to_dict()
        self.assertIsInstance(profile_dict, dict)
        summary_str = generate_human_readable_profile(profile)
        self.assertIn("PLAYER PROFILE: Hero", summary_str)

    def test_white_and_black_games(self):
        """
        Verify that moves are correctly attributed when the hero played White in Game 1 and Black in Game 2.
        """
        game_white = {
            "metadata": {"white": "Hero", "black": "Opponent1"},
            "moves": [
                self._create_mock_move(1, "White", "e4", "e4", cpl=0, is_critical=False),
                self._create_mock_move(1, "Black", "e5", "e5", cpl=150, is_critical=True, category="material_loss"),
            ]
        }
        game_black = {
            "metadata": {"white": "Opponent2", "black": "Hero"},
            "moves": [
                self._create_mock_move(1, "White", "d4", "d4", cpl=200, is_critical=True, category="material_loss"),
                self._create_mock_move(1, "Black", "d5", "d5", cpl=0, is_critical=False),
            ]
        }
        profile = build_player_profile([game_white, game_black], target_player="Hero")
        # In game 1, Hero was White (0 critical). In game 2, Hero was Black (0 critical).
        self.assertEqual(profile.moves_analyzed, 2)
        self.assertEqual(profile.critical_positions, 0)
        self.assertEqual(len(profile.weaknesses), 0)


if __name__ == "__main__":
    unittest.main()
