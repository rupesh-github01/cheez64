"""
Deterministic unit tests for Personalized Coaching Recommendation Engine.
"""

import unittest
from pathlib import Path
import sys

# Ensure src is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.profile import PlayerProfile, ProfileWeakness, ProfileStrength, ProfileConfidence, TacticalSummary, MaterialSummary, PositionalSummary, EndgameSummary
from src.openings import RepertoireProfile, RepertoireEntry, RepertoirePerformance
from src.recommendations import (
    CoachingRecommendation,
    CoachingProfile,
    generate_coaching_recommendations,
    build_coaching_profile,
    build_coaching_profile_from_library,
    _compute_recommendation_score,
)
from src.library import GameLibrary


def make_dummy_profile(
    weaknesses=None,
    strengths=None,
    total_games=5,
    player_name="Hero",
) -> PlayerProfile:
    """Helper to construct dummy PlayerProfile for recommendation testing."""
    from src.profile import SeveritySummary
    return PlayerProfile(
        player_name=player_name,
        games_analyzed=total_games,
        moves_analyzed=150,
        critical_positions=20,
        episodes_count=10,
        overall_average_cpl=45.0,
        strengths=strengths or [],
        weaknesses=weaknesses or [],
        tactical_summary=TacticalSummary(10, 5, 3, 2, 5, 200.0),
        material_summary=MaterialSummary(15, 5, 10, 12, 10, {"knight": 2, "pawn": 4}, 10),
        positional_summary=PositionalSummary(5, 80.0, 1, 4),
        endgame_summary=EndgameSummary(30, 4, 2, 1, 1, 120.0),
        severity_summary=SeveritySummary(5, 5, 5, None),
        confidence=ProfileConfidence("medium", total_games, 150, "Preliminary baseline"),
    )


class TestCoachingRecommendations(unittest.TestCase):
    """
    Test suite for recommendation scoring, ranking, episode deduplication,
    opening advice conservatism, confidence, and strength integration.
    """

    def setUp(self):
        self.lib = GameLibrary(":memory:")

    def tearDown(self):
        self.lib.close()

    def test_ranking_recurring_severe_vs_isolated_trivial(self):
        """
        Verify that a severe mistake repeated across 4 games outranks
        multiple trivial mistakes occurring in a single game.
        """
        score_severe_recurring = _compute_recommendation_score(
            episodes=4,
            distinct_games=4,
            total_games=5,
            avg_cpl=350.0,
            peak_cpl=500,
            confidence_level="high",
        )
        score_trivial_isolated = _compute_recommendation_score(
            episodes=6,
            distinct_games=1,
            total_games=5,
            avg_cpl=75.0,
            peak_cpl=90,
            confidence_level="low",
        )

        self.assertGreater(score_severe_recurring, score_trivial_isolated)
        self.assertGreater(score_severe_recurring, score_trivial_isolated * 3)

    def test_recurring_tactical_weakness_recommendation(self):
        """Verify recurring tactical material loss generates Priority 1 tactical recommendation."""
        w1 = ProfileWeakness(
            category="Tactical Awareness: Material Loss",
            pillar="Tactical Awareness",
            subcategories=["material_loss"],
            mistake_type="tactical_blunder",
            occurrences=8,
            episode_occurrences=6,
            distinct_games=4,
            game_ids=[1, 2, 3, 4],
            games_ratio="4/5",
            average_cpl=320.0,
            max_cpl=600,
            total_cpl=2500,
            high_confidence_count=6,
            severity="high",
            confidence="high",
            composite_score=45.0,
            sample_moves=[{"game_id": 1, "move": "21. Be7", "best_move": "O-O", "cpl": 350, "summary": "Lost a rook"}],
            coaching_takeaway="Concedes material repeatedly."
        )

        profile = make_dummy_profile(weaknesses=[w1], total_games=5)
        coach = generate_coaching_recommendations(profile)

        self.assertEqual(len(coach.top_recommendations), 1)
        top_rec = coach.top_recommendations[0]
        self.assertEqual(top_rec.priority, 1)
        self.assertEqual(top_rec.category, "tactical_awareness")
        self.assertIn("tactical puzzles / calculation exercises", top_rec.suggested_training_type)
        self.assertEqual(len(top_rec.affected_moves), 1)
        self.assertIn("4/5 games", top_rec.summary)

    def test_episode_deduplication_preservation(self):
        """
        Verify that 3 moves within 1 episode are scored using episode_occurrences=1,
        not move occurrences=3.
        """
        w_single_ep = ProfileWeakness(
            category="Tactical Awareness: Hanging Pieces",
            pillar="Tactical Awareness",
            subcategories=["hanging_piece"],
            mistake_type="tactical_blunder",
            occurrences=3,  # 3 moves
            episode_occurrences=1,  # 1 episode
            distinct_games=1,
            game_ids=[1],
            games_ratio="1/5",
            average_cpl=400.0,
            max_cpl=500,
            total_cpl=1200,
            high_confidence_count=2,
            severity="high",
            confidence="medium",
            composite_score=5.0,
            sample_moves=[],
            coaching_takeaway="Left hanging piece."
        )

        profile = make_dummy_profile(weaknesses=[w_single_ep], total_games=5)
        coach = generate_coaching_recommendations(profile)
        rec = coach.top_recommendations[0]

        # The score must be proportional to 1 episode, not 3
        expected_score = _compute_recommendation_score(1, 1, 5, 400.0, 500, "medium")
        self.assertEqual(rec.score, expected_score)

    def test_opening_recommendation_only_when_recurring_and_struggling(self):
        """
        Verify that opening recommendations are triggered ONLY when an opening
        is recurring (>= 2 games) AND struggles after theory exit.
        """
        # Recurring opening with post-theory accuracy drop
        recurring_entry = RepertoireEntry(
            color="Black",
            first_move_group="vs 1. e4",
            opening_name="Sicilian Defense",
            variation_name="Dragon Variation",
            eco="B70",
            full_name="Sicilian Defense: Dragon Variation",
            total_games=3,
            distinct_games=3,
            game_ids=["g1", "g2", "g3"],
            wins=0,
            draws=1,
            losses=2,
            win_rate=0.0,
            analyzed_games=3,
            avg_cpl_overall=85.0,
            before_theory=RepertoirePerformance(moves_count=15, avg_cpl=12.0, critical_mistakes=0, blunders=0),
            after_theory=RepertoirePerformance(moves_count=45, avg_cpl=92.0, critical_mistakes=5, blunders=2),
            repertoire_status="recurring_choice",
            status_description="Recurring choice played in 3 games.",
        )

        repertoire = RepertoireProfile(
            target_player="Hero",
            total_games=3,
            white_games=0,
            black_games=3,
            white_repertoire={},
            black_repertoire={"vs 1. e4": [recurring_entry]},
            opening_statistics=[recurring_entry],
        )

        profile = make_dummy_profile(weaknesses=[], total_games=3)
        coach = generate_coaching_recommendations(profile, repertoire=repertoire)

        # Should generate an opening_transition recommendation
        op_recs = [r for r in coach.top_recommendations if r.category == "opening_transition"]
        self.assertEqual(len(op_recs), 1)
        self.assertIn("Sicilian Defense", op_recs[0].title)
        self.assertIn("middlegame plans from recurring openings", op_recs[0].suggested_training_type)

    def test_insufficient_opening_evidence_generates_no_repertoire_change(self):
        """
        Verify that 1-game openings do NOT generate an opening change recommendation
        and explicit conservatism is stated in limitations.
        """
        single_game_entry = RepertoireEntry(
            color="White",
            first_move_group="1. e4",
            opening_name="Italian Game",
            variation_name=None,
            eco="C50",
            full_name="Italian Game",
            total_games=1,
            distinct_games=1,
            game_ids=["g1"],
            wins=0,
            draws=0,
            losses=1,
            win_rate=0.0,
            analyzed_games=1,
            avg_cpl_overall=120.0,
            before_theory=RepertoirePerformance(moves_count=5, avg_cpl=5.0, critical_mistakes=0),
            after_theory=RepertoirePerformance(moves_count=20, avg_cpl=140.0, critical_mistakes=3),
            repertoire_status="observed_opening",
            status_description="Observed in 1 game.",
        )

        repertoire = RepertoireProfile(
            target_player="Hero",
            total_games=1,
            white_games=1,
            black_games=0,
            white_repertoire={"1. e4": [single_game_entry]},
            black_repertoire={},
            opening_statistics=[single_game_entry],
        )

        profile = make_dummy_profile(weaknesses=[], total_games=1)
        coach = generate_coaching_recommendations(profile, repertoire=repertoire)

        # Must NOT generate an opening recommendation for a 1-game sample
        op_recs = [r for r in coach.top_recommendations if r.category in ("opening_repertoire", "opening_transition")]
        self.assertEqual(len(op_recs), 0)

        # Must record limitation
        has_limitation = any("appeared in only 1 game each" in lim for lim in coach.limitations)
        self.assertTrue(has_limitation)

    def test_strength_aware_recommendation(self):
        """
        Verify that when a player has demonstrated clean play in quiet positions,
        tactical recommendations explicitly advise focusing on high-tension transitions.
        """
        w = ProfileWeakness(
            category="Tactical Awareness: Material Loss",
            pillar="Tactical Awareness",
            subcategories=["material_loss"],
            mistake_type="tactical_blunder",
            occurrences=5,
            episode_occurrences=4,
            distinct_games=3,
            game_ids=[1, 2, 3],
            games_ratio="3/5",
            average_cpl=280.0,
            max_cpl=550,
            total_cpl=1400,
            high_confidence_count=4,
            severity="high",
            confidence="medium",
            composite_score=30.0,
            sample_moves=[],
            coaching_takeaway="Concedes material."
        )

        s = ProfileStrength(
            area="Low-Error Performance in Structurally Clean Positions",
            evidence_count=40,
            distinct_games=1,
            games_ratio="1/5",
            description="Demonstrated 9.6 avg CPL in quiet positions.",
            sample_evidence=[]
        )

        profile = make_dummy_profile(weaknesses=[w], strengths=[s], total_games=5)
        coach = generate_coaching_recommendations(profile)

        rec = coach.top_recommendations[0]
        self.assertIn("uncomplicated positions", rec.rationale.lower())
        self.assertIn("complex middlegame transitions", rec.rationale)

    def test_confidence_levels_reflect_sample_size(self):
        """Verify confidence is low for <5 games, medium for 5-14 games, high for >=15 games."""
        p_small = make_dummy_profile(total_games=3)
        self.assertEqual(generate_coaching_recommendations(p_small).confidence, "low")

        p_med = make_dummy_profile(total_games=6)
        self.assertEqual(generate_coaching_recommendations(p_med).confidence, "medium")

        p_large = make_dummy_profile(total_games=16)
        self.assertEqual(generate_coaching_recommendations(p_large).confidence, "high")

    def test_empty_or_clean_dataset_handled_gracefully(self):
        """Verify profile with zero weaknesses produces clean profile without errors."""
        p_empty = make_dummy_profile(weaknesses=[], total_games=2)
        coach = generate_coaching_recommendations(p_empty)
        self.assertEqual(len(coach.top_recommendations), 0)
        self.assertEqual(coach.games_analyzed, 2)
        summary_str = coach.summary()
        self.assertIn("No critical recurring weaknesses", summary_str)

    def test_build_coaching_profile_from_library_integration(self):
        """Verify generating CoachingProfile directly from games in a GameLibrary."""
        # Add sample game and attach sample analysis
        game, _ = self.lib.add_game("""[Event "Casual"]
[White "Hero"]
[Black "Opponent"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 1-0
""")
        sample_analysis = {
            "schema_version": "1.1.0",
            "game_id": game.game_id,
            "metadata": {"white": "Hero", "black": "Opponent", "result": "1-0"},
            "summary": {"total_moves": 6},
            "moves": [
                {"move_number": 1, "color": "White", "centipawn_loss": 0, "is_critical": False},
                {"move_number": 2, "color": "White", "centipawn_loss": 0, "is_critical": False},
                {"move_number": 3, "color": "White", "centipawn_loss": 250, "is_critical": True, "tactical_evidence": {"category": "material_loss"}},
            ],
            "episodes": []
        }
        self.lib.attach_analysis(game.game_id, sample_analysis)

        coach_profile = build_coaching_profile_from_library(self.lib, "Hero")
        self.assertEqual(coach_profile.target_player, "Hero")
        self.assertEqual(coach_profile.games_analyzed, 1)
        self.assertEqual(coach_profile.confidence, "low")

    def test_white_and_black_evidence_handling(self):
        """Verify handling player moves played as both White and Black."""
        w_white = ProfileWeakness(
            category="Tactical Awareness: Material Loss",
            pillar="Tactical Awareness",
            subcategories=["material_loss"],
            mistake_type="tactical_blunder",
            occurrences=2,
            episode_occurrences=2,
            distinct_games=2,
            game_ids=[1, 2],
            games_ratio="2/4",
            average_cpl=300.0,
            max_cpl=400,
            total_cpl=600,
            high_confidence_count=2,
            severity="high",
            confidence="medium",
            composite_score=15.0,
            sample_moves=[
                {"game_id": 1, "move": "15. Ne4", "played_move": "Ne4", "best_move": "O-O", "cpl": 300},
                {"game_id": 2, "move": "20... Nd5", "played_move": "Nd5", "best_move": "Re8", "cpl": 300},
            ],
            coaching_takeaway="Conceded pieces in both White and Black games."
        )

        profile = make_dummy_profile(weaknesses=[w_white], total_games=4)
        coach = generate_coaching_recommendations(profile)
        rec = coach.top_recommendations[0]
        self.assertEqual(len(rec.affected_moves), 2)
        self.assertEqual(rec.affected_games, [1, 2])

    def test_ambiguous_tactical_classification_handling(self):
        """Verify unclassified or ambiguous tactical categories fall back gracefully."""
        w_ambig = ProfileWeakness(
            category="Unclassified Engine Swing",
            pillar="Strategic & Positional",
            subcategories=["unclassified"],
            mistake_type="positional_inaccuracy",
            occurrences=3,
            episode_occurrences=2,
            distinct_games=2,
            game_ids=[1, 2],
            games_ratio="2/3",
            average_cpl=150.0,
            max_cpl=200,
            total_cpl=450,
            high_confidence_count=0,
            severity="medium",
            confidence="low",
            composite_score=8.0,
            sample_moves=[],
            coaching_takeaway="General evaluation drop."
        )

        profile = make_dummy_profile(weaknesses=[w_ambig], total_games=3)
        coach = generate_coaching_recommendations(profile)
        # Should gracefully map to positional_decision_making or tactical_awareness without crashing
        self.assertEqual(len(coach.top_recommendations), 1)
        self.assertIn(coach.top_recommendations[0].category, ("positional_decision_making", "tactical_awareness"))

    def test_positional_evidence_uses_evaluation_drop_label(self):
        """
        Regression: positional_decision_making evidence must NOT say 'tactical episodes'.
        It must use 'evaluation-drop episodes' instead (Finding 1 & 5 from audit).
        """
        w_pos = ProfileWeakness(
            category="Strategic & Positional Decisions",
            pillar="Strategic & Positional",
            subcategories=["positional_evaluation_drop"],
            mistake_type="positional_inaccuracy",
            occurrences=5,
            episode_occurrences=4,
            distinct_games=3,
            game_ids=[1, 2, 3],
            games_ratio="3/5",
            average_cpl=180.0,
            max_cpl=400,
            total_cpl=900,
            high_confidence_count=0,
            severity="medium",
            confidence="medium",
            composite_score=20.0,
            sample_moves=[],
            coaching_takeaway="Evaluation drops."
        )

        profile = make_dummy_profile(weaknesses=[w_pos], total_games=5)
        coach = generate_coaching_recommendations(profile)
        rec = coach.top_recommendations[0]

        # Must use "evaluation-drop episodes", NOT "tactical episodes"
        has_correct_label = any("evaluation-drop episodes" in ev for ev in rec.evidence)
        has_wrong_label = any("tactical episodes" in ev for ev in rec.evidence)
        self.assertTrue(has_correct_label, f"Expected 'evaluation-drop episodes' in evidence: {rec.evidence}")
        self.assertFalse(has_wrong_label, f"'tactical episodes' should not appear for positional category: {rec.evidence}")

    def test_endgame_language_no_unsupported_concepts(self):
        """
        Regression: endgame recommendation must NOT reference 'king paths',
        'opposition rules', or 'key squares' since these are not computed (Finding 2).
        """
        w_eg = ProfileWeakness(
            category="Endgame Technique: King Activity & Opposition",
            pillar="Endgame Technique",
            subcategories=["endgame_king_activity"],
            mistake_type="endgame_technique",
            occurrences=4,
            episode_occurrences=3,
            distinct_games=2,
            game_ids=[1, 2],
            games_ratio="2/5",
            average_cpl=300.0,
            max_cpl=1000,
            total_cpl=1200,
            high_confidence_count=0,
            severity="high",
            confidence="medium",
            composite_score=15.0,
            sample_moves=[],
            coaching_takeaway="Endgame drops."
        )

        profile = make_dummy_profile(weaknesses=[w_eg], total_games=5)
        coach = generate_coaching_recommendations(profile)
        rec = coach.top_recommendations[0]

        # Must not contain unsupported chess concepts
        full_text = f"{rec.title} {rec.summary} {rec.rationale}"
        self.assertNotIn("king paths", full_text.lower())
        self.assertNotIn("opposition rules", full_text.lower())
        self.assertNotIn("conceded key squares", full_text.lower())
        # Title must reflect the evidence-grounded approach
        self.assertEqual(rec.title, "Endgame Phase Accuracy")

    def test_duplicate_endgame_weaknesses_merged_into_single_recommendation(self):
        """
        Regression: two endgame weakness buckets (king_activity + piece_play) that
        both map to category='endgame' must produce exactly ONE recommendation,
        not two duplicates (Finding 3).
        """
        w_eg_king = ProfileWeakness(
            category="Endgame Technique: King Activity & Opposition",
            pillar="Endgame Technique",
            subcategories=["endgame_king_activity"],
            mistake_type="endgame_technique",
            occurrences=4,
            episode_occurrences=3,
            distinct_games=2,
            game_ids=[1, 3],
            games_ratio="2/5",
            average_cpl=400.0,
            max_cpl=1000,
            total_cpl=1600,
            high_confidence_count=0,
            severity="high",
            confidence="medium",
            composite_score=18.0,
            sample_moves=[{"game_id": 1, "move": "47. Ke4", "best_move": "Bf2", "cpl": 1000, "summary": ""}],
            coaching_takeaway="King endgame errors."
        )

        w_eg_piece = ProfileWeakness(
            category="Endgame Technique: Endgame Piece Play",
            pillar="Endgame Technique",
            subcategories=["endgame_piece_play"],
            mistake_type="endgame_technique",
            occurrences=3,
            episode_occurrences=3,
            distinct_games=2,
            game_ids=[2, 3],
            games_ratio="2/5",
            average_cpl=120.0,
            max_cpl=147,
            total_cpl=360,
            high_confidence_count=0,
            severity="low",
            confidence="medium",
            composite_score=8.0,
            sample_moves=[{"game_id": 2, "move": "32. Re3", "best_move": "Rbc7", "cpl": 147, "summary": ""}],
            coaching_takeaway="Piece endgame errors."
        )

        profile = make_dummy_profile(weaknesses=[w_eg_king, w_eg_piece], total_games=5)
        coach = generate_coaching_recommendations(profile)

        endgame_recs = [r for r in coach.top_recommendations if r.category == "endgame"]
        self.assertEqual(len(endgame_recs), 1, f"Expected 1 merged endgame rec, got {len(endgame_recs)}")

        merged_rec = endgame_recs[0]
        # Must merge game_ids from both weaknesses
        self.assertIn(1, merged_rec.affected_games)
        self.assertIn(2, merged_rec.affected_games)
        self.assertIn(3, merged_rec.affected_games)
        # Must merge episode counts (3 + 3 = 6)
        self.assertIn("6 endgame-critical episodes", merged_rec.evidence[0])
        # Must merge sample moves from both
        self.assertGreaterEqual(len(merged_rec.affected_moves), 2)

    def test_material_units_say_pawn_equivalent(self):
        """
        Regression: material evidence must say 'pawn-equivalent material points'
        not just 'net material points' (Finding 4).
        """
        w = ProfileWeakness(
            category="Tactical Awareness: Material Loss",
            pillar="Tactical Awareness",
            subcategories=["material_loss"],
            mistake_type="tactical_blunder",
            occurrences=3,
            episode_occurrences=3,
            distinct_games=2,
            game_ids=[1, 2],
            games_ratio="2/3",
            average_cpl=250.0,
            max_cpl=400,
            total_cpl=750,
            high_confidence_count=3,
            severity="high",
            confidence="medium",
            composite_score=18.0,
            sample_moves=[],
            coaching_takeaway="Material losses."
        )

        profile = make_dummy_profile(weaknesses=[w], total_games=3)
        coach = generate_coaching_recommendations(profile)
        rec = coach.top_recommendations[0]

        mat_ev = [ev for ev in rec.evidence if "material points" in ev]
        self.assertTrue(len(mat_ev) > 0, "Expected material evidence bullet")
        self.assertIn("pawn-equivalent material points", mat_ev[0])
        self.assertNotIn("net material points", mat_ev[0])


if __name__ == "__main__":
    unittest.main()
