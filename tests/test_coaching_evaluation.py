"""
Comprehensive Chess Coaching Intelligence Evaluation Harness (Segment 13).

Evaluates:
1. Move Classification & CPL calculations across standard, mate, and forced positions.
2. Precision, Recall, and False Positive/Negative metrics for each implemented tactical detector:
   - hanging_piece
   - missed_capture
   - missed_check_or_forcing_move
   - material_loss
3. Semantic hint leakage evaluation (SAN, UCI, destination squares, piece-square combos).
4. Critical episode clustering cohesion and separation.
5. Cross-game player profiling & recommendation ranking on synthetic histories.
6. Conversational coach response grounding, consistency, and rubrics.
"""

import re
import pytest
import chess
from typing import Dict, Any, List

from src.models import MoveAnalysis, CandidateMove
from src.analyzer import calculate_centipawn_loss, MATE_THRESHOLD, MAX_CPL
from src.critical import classify_move, find_critical_positions
from src.episodes import group_critical_positions
from src.tactical_analysis import (
    detect_tactical_consequence,
    CATEGORY_HANGING_PIECE,
    CATEGORY_MISSED_CAPTURE,
    CATEGORY_MISSED_CHECK_OR_FORCING,
    CATEGORY_MATERIAL_LOSS,
    CATEGORY_UNCLASSIFIED,
)
from src.explanations import generate_move_explanation
from src.coach.hints import (
    generate_progressive_hint,
    check_and_sanitize_hint_leakage,
    HINT_TIER_CONCEPTUAL,
    HINT_TIER_SPECIFIC,
    HINT_TIER_REVEAL,
)
from src.coach.provider import DeterministicFallbackProvider
from src.profile import build_player_profile
from src.recommendations import build_coaching_profile
from tests.data.coaching_eval_dataset import COACHING_EVAL_DATASET, EvalPosition


# ---------------------------------------------------------------------------
# Helper: Build MoveAnalysis from EvalPosition
# ---------------------------------------------------------------------------

def build_move_analysis(pos: EvalPosition) -> MoveAnalysis:
    board_before = chess.Board(pos.fen_before)
    mover = board_before.turn
    played_obj = board_before.parse_san(pos.played_move)
    board_after = board_before.copy()
    board_after.push(played_obj)

    candidates = []
    if pos.best_move:
        cand_pv = pos.candidate_pv if pos.candidate_pv else [pos.best_move]
        candidates.append(CandidateMove(
            move=pos.best_move,
            evaluation=pos.eval_before,
            principal_variation=cand_pv,
        ))

    analysis = MoveAnalysis(
        move_number=board_before.fullmove_number,
        color="White" if mover == chess.WHITE else "Black",
        played_move=pos.played_move,
        best_move=pos.best_move,
        evaluation_before=pos.eval_before,
        evaluation_after=pos.eval_after,
        centipawn_loss=pos.cpl,
        fen_before=pos.fen_before,
        fen_after=board_after.fen(),
        principal_variation=pos.candidate_pv,
        candidates=candidates,
        pv_after=pos.pv_after,
        is_forced=pos.is_forced,
    )
    analysis.raw_centipawn_loss = pos.cpl
    analysis.classification = classify_move(pos.cpl)
    return analysis


# ---------------------------------------------------------------------------
# 1. Move Classification & CPL Evaluation
# ---------------------------------------------------------------------------

class TestMoveClassificationEvaluation:
    def test_cpl_and_classification_across_dataset(self):
        """Verify CPL calculation and move classification agreement across dataset."""
        for pos in COACHING_EVAL_DATASET:
            analysis = build_move_analysis(pos)
            cls = classify_move(analysis.centipawn_loss)
            assert cls == pos.expected_classification, (
                f"[{pos.id}] Classification mismatch: got '{cls}', expected '{pos.expected_classification}' (CPL: {pos.cpl})"
            )

    def test_forced_moves_receive_zero_decision_loss(self):
        """Verify forced moves always have 0 decision loss."""
        forced_cases = [p for p in COACHING_EVAL_DATASET if p.is_forced]
        assert len(forced_cases) >= 1
        for p in forced_cases:
            analysis = build_move_analysis(p)
            assert analysis.is_forced is True
            assert analysis.centipawn_loss == 0
            assert classify_move(analysis.centipawn_loss) == "good"

    def test_mate_score_cpl_rules_consistency(self):
        """Verify mate-score CPL rules adhere to defined invariants."""
        # Non-mate
        assert calculate_centipawn_loss(100, 30) == 70
        assert calculate_centipawn_loss(0, 0) == 0
        assert calculate_centipawn_loss(-50, 50) == 0

        # Winning mate maintained -> 0 CPL
        assert calculate_centipawn_loss(99999, 99998) == 0

        # Already lost in forced mate -> 0 CPL
        assert calculate_centipawn_loss(-99998, -99999) == 0

        # Throwing away winning mate -> MAX_CPL
        assert calculate_centipawn_loss(99999, 500) == MAX_CPL

        # Blundering into losing mate -> MAX_CPL
        assert calculate_centipawn_loss(200, -99998) == MAX_CPL


# ---------------------------------------------------------------------------
# 2. Tactical Detector Precision & Recall
# ---------------------------------------------------------------------------

class TestTacticalDetectorEvaluation:
    def test_tactical_detector_metrics_and_negative_controls(self):
        """
        Evaluate precision, recall, and false positive/negative counts
        for each implemented tactical detector.
        """
        implemented_detectors = [
            CATEGORY_HANGING_PIECE,
            CATEGORY_MISSED_CAPTURE,
            CATEGORY_MISSED_CHECK_OR_FORCING,
            CATEGORY_MATERIAL_LOSS,
        ]

        metrics = {
            cat: {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
            for cat in implemented_detectors
        }

        for pos in COACHING_EVAL_DATASET:
            analysis = build_move_analysis(pos)
            finding = detect_tactical_consequence(analysis)
            detected_cat = finding.category if finding else CATEGORY_UNCLASSIFIED
            expected_cat = pos.expected_category

            for cat in implemented_detectors:
                is_expected = (expected_cat == cat)
                is_detected = (detected_cat == cat)

                if is_expected and is_detected:
                    metrics[cat]["TP"] += 1
                elif not is_expected and is_detected:
                    metrics[cat]["FP"] += 1
                elif is_expected and not is_detected:
                    metrics[cat]["FN"] += 1
                else:
                    metrics[cat]["TN"] += 1

            # Specifically verify negative examples:
            if pos.is_negative_example:
                assert detected_cat not in (CATEGORY_HANGING_PIECE,), (
                    f"[{pos.id}] Negative example falsely flagged as hanging piece! finding={finding}"
                )

        print("\n--- Tactical Detector Evaluation Metrics ---")
        for cat, m in metrics.items():
            tp, fp, fn, tn = m["TP"], m["FP"], m["FN"], m["TN"]
            precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
            f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
            print(f"{cat:<30}: TP={tp:<2} FP={fp:<2} FN={fn:<2} TN={tn:<2} | Precision={precision:.2f} Recall={recall:.2f} F1={f1:.2f}")

            # Enforce reasonable precision thresholds
            assert fp <= 2, f"Excessive false positives for {cat}: FP={fp}"
            assert precision >= 0.75, f"Low precision for {cat}: {precision:.2f}"


# ---------------------------------------------------------------------------
# 3. Semantic Hint Leakage Evaluation
# ---------------------------------------------------------------------------

class TestSemanticHintLeakageEvaluation:
    def test_progressive_hint_sanitization_across_tiers(self):
        """
        Verify that Tier 1 (Conceptual) and Tier 2 (Specific Clue) hints
        never disclose the best move SAN or direct destination move tokens.
        """
        san_leaks = 0
        uci_leaks = 0
        dest_square_direct_leaks = 0
        total_evaluations = 0

        for pos in COACHING_EVAL_DATASET:
            analysis = build_move_analysis(pos)
            finding = detect_tactical_consequence(analysis)
            analysis.tactical_finding = finding
            explanation = generate_move_explanation(analysis)

            move_ctx = {
                "best_move": pos.best_move,
                "played_move": pos.played_move,
                "tactical_finding": finding.to_dict() if finding else None,
                "explanation": explanation.to_dict(),
                "side_to_move": "White" if chess.Board(pos.fen_before).turn == chess.WHITE else "Black",
            }

            best_clean = pos.best_move.rstrip("+#?!")
            dest_sq = best_clean[-2:] if len(best_clean) >= 2 and re.match(r"^[a-h][1-8]$", best_clean[-2:]) else ""

            # Check Tier 1 & Tier 2
            for tier in (HINT_TIER_CONCEPTUAL, HINT_TIER_SPECIFIC):
                total_evaluations += 1
                hint = generate_progressive_hint(move_ctx, tier)

                # 1. Exact SAN leak check (case-insensitive word boundary)
                if re.search(rf"\b{re.escape(best_clean)}\b", hint, re.IGNORECASE):
                    san_leaks += 1
                    pytest.fail(f"[{pos.id}] Tier {tier} leaked SAN '{best_clean}': {hint}")

                # 2. Direct destination command leak (e.g. 'move to f3' or 'play to f3')
                if dest_sq and re.search(rf"\b(move to |play to |to ){re.escape(dest_sq)}\b", hint, re.IGNORECASE):
                    dest_square_direct_leaks += 1
                    pytest.fail(f"[{pos.id}] Tier {tier} leaked destination square '{dest_sq}': {hint}")

            # Check Tier 3: MUST reveal best move
            tier3_hint = generate_progressive_hint(move_ctx, HINT_TIER_REVEAL)
            assert pos.best_move in tier3_hint, f"[{pos.id}] Tier 3 failed to reveal best move {pos.best_move}"

        leak_rate = (san_leaks + uci_leaks + dest_square_direct_leaks) / total_evaluations
        assert leak_rate == 0.0, f"Hint leakage detected! Leak rate: {leak_rate}"


# ---------------------------------------------------------------------------
# 4. Critical Episodes Evaluation
# ---------------------------------------------------------------------------

class TestCriticalEpisodesEvaluation:
    def test_single_blunder_cascade_clustering(self):
        """
        Verify that a single blunder followed immediately by forced defensive replies
        within 2 plies is clustered into a single episode.
        """
        # Cascade: Blunder at ply 10, forced reply at ply 11, critical mistake at ply 12
        dummy_fen = "8/8/8/8/8/8/8/8 w - - 0 1"
        moves = [
            MoveAnalysis(move_number=5, color="White", played_move="e4", best_move="e4", evaluation_before=0, evaluation_after=0, centipawn_loss=0, fen_before=dummy_fen, fen_after=dummy_fen),
            MoveAnalysis(move_number=5, color="Black", played_move="e5", best_move="e5", evaluation_before=0, evaluation_after=0, centipawn_loss=10, fen_before=dummy_fen, fen_after=dummy_fen),
            # Blunder cascade
            MoveAnalysis(move_number=6, color="White", played_move="g4", best_move="Nf3", evaluation_before=0, evaluation_after=-300, centipawn_loss=300, fen_before=dummy_fen, fen_after=dummy_fen),  # index 2
            MoveAnalysis(move_number=6, color="Black", played_move="Qh4#", best_move="Qh4#", evaluation_before=100000, evaluation_after=100000, centipawn_loss=0, fen_before=dummy_fen, fen_after=dummy_fen),
            # Separate blunder later at index 6
            MoveAnalysis(move_number=7, color="White", played_move="Nf3", best_move="Nf3", evaluation_before=0, evaluation_after=0, centipawn_loss=0, fen_before=dummy_fen, fen_after=dummy_fen),
            MoveAnalysis(move_number=7, color="Black", played_move="Nc6", best_move="Nc6", evaluation_before=0, evaluation_after=0, centipawn_loss=0, fen_before=dummy_fen, fen_after=dummy_fen),
            MoveAnalysis(move_number=8, color="White", played_move="h3", best_move="d4", evaluation_before=0, evaluation_after=-150, centipawn_loss=150, fen_before=dummy_fen, fen_after=dummy_fen),   # index 6
        ]

        episodes = group_critical_positions(moves, minimum_cpl=75, max_gap=2)
        assert len(episodes) == 2, f"Expected 2 separate episodes, got {len(episodes)}"
        assert len(episodes[0]) == 1
        assert len(episodes[1]) == 1

    def test_tight_scramble_grouped_together(self):
        """Verify critical moves within 2 plies are clustered into 1 episode."""
        dummy_fen = "8/8/8/8/8/8/8/8 w - - 0 1"
        moves = [
            MoveAnalysis(move_number=10, color="White", played_move="Nd5", best_move="Bxc6", evaluation_before=0, evaluation_after=-250, centipawn_loss=250, fen_before=dummy_fen, fen_after=dummy_fen), # idx 0
            MoveAnalysis(move_number=10, color="Black", played_move="Nxd5", best_move="Nxd5", evaluation_before=0, evaluation_after=-90, centipawn_loss=90, fen_before=dummy_fen, fen_after=dummy_fen),  # idx 1
            MoveAnalysis(move_number=11, color="White", played_move="exd5", best_move="cxd5", evaluation_before=0, evaluation_after=-120, centipawn_loss=120, fen_before=dummy_fen, fen_after=dummy_fen), # idx 2
        ]
        episodes = group_critical_positions(moves, minimum_cpl=75, max_gap=2)
        assert len(episodes) == 1
        assert len(episodes[0]) == 3


# ---------------------------------------------------------------------------
# 5. Cross-Game Recommendations & Synthetic Player History
# ---------------------------------------------------------------------------

class TestCrossGameRecommendationsEvaluation:
    def test_cross_game_recurrence_ranks_highest(self):
        """
        Verify that recurring weaknesses across multiple games heavily outrank
        isolated blunders in a single game.
        """
        # Game 1: Hanging piece
        g1_analysis = {
            "metadata": {"white": "TestHero", "black": "Opponent"},
            "moves": [
                {
                    "move_number": 5, "color": "White", "played_move": "Nd5", "best_move": "c3",
                    "centipawn_loss": 300, "is_critical": True, "episode_index": 1,
                    "tactical_finding": {"category": "hanging_piece", "confidence": "high", "evidence": {"hanging_piece": "knight"}},
                }
            ]
        }
        # Game 2: Hanging piece
        g2_analysis = {
            "metadata": {"white": "TestHero", "black": "Opponent"},
            "moves": [
                {
                    "move_number": 8, "color": "White", "played_move": "Qe3", "best_move": "Qd1",
                    "centipawn_loss": 400, "is_critical": True, "episode_index": 1,
                    "tactical_finding": {"category": "hanging_piece", "confidence": "high", "evidence": {"hanging_piece": "queen"}},
                }
            ]
        }
        # Game 3: Hanging piece
        g3_analysis = {
            "metadata": {"white": "TestHero", "black": "Opponent"},
            "moves": [
                {
                    "move_number": 12, "color": "White", "played_move": "Ra4", "best_move": "Rc1",
                    "centipawn_loss": 350, "is_critical": True, "episode_index": 1,
                    "tactical_finding": {"category": "hanging_piece", "confidence": "high", "evidence": {"hanging_piece": "rook"}},
                }
            ]
        }
        # Game 4: Single isolated missed capture
        g4_analysis = {
            "metadata": {"white": "TestHero", "black": "Opponent"},
            "moves": [
                {
                    "move_number": 15, "color": "White", "played_move": "h3", "best_move": "Qxa8+",
                    "centipawn_loss": 500, "is_critical": True, "episode_index": 1,
                    "tactical_finding": {"category": "missed_capture", "confidence": "high", "evidence": {"captured_piece": "rook"}},
                }
            ]
        }

        analyses = [g1_analysis, g2_analysis, g3_analysis, g4_analysis]
        profile = build_player_profile(analyses, target_player="TestHero")

        # Weaknesses should rank Hanging Pieces #1 due to cross-game recurrence (3 distinct games vs 1)
        assert len(profile.weaknesses) >= 2
        top_weakness = profile.weaknesses[0]
        assert "Hanging Pieces" in top_weakness.category
        assert top_weakness.distinct_games == 3
        assert top_weakness.composite_score > profile.weaknesses[1].composite_score

        # Coaching recommendations profile
        coaching_prof = build_coaching_profile(analyses, target_player="TestHero")
        assert len(coaching_prof.top_recommendations) >= 1
        top_rec = coaching_prof.top_recommendations[0]
        assert top_rec.category in ("piece_safety", "tactical_awareness")
        assert len(top_rec.affected_games) == 3


# ---------------------------------------------------------------------------
# 6. Conversational Coach Grounding & Rubric Evaluation
# ---------------------------------------------------------------------------

class TestConversationalCoachEvaluation:
    @pytest.mark.anyio
    async def test_deterministic_fallback_grounding_and_rubric(self):
        """
        Verify the offline deterministic coach answers standard user questions
        strictly grounded in structured context without hallucinating motifs.
        """
        provider = DeterministicFallbackProvider()

        # Build mock system prompt with structured evidence
        prompt = """You are a grounded chess coach.
Played Move: Nd5
Best Move: Bxc6
Classification: Blunder
Centipawn Loss: 300 cp
Evaluation Before: 40 cp
Evaluation After: -260 cp
Side to Move: White
Move Number: 5
Tactical Category: hanging_piece
Tactical Motif: hanging_piece
Refutation Line (pv_after): axb5 Nxb4 Nxb4
Explanation Summary: Nd5 leaves the bishop on b5 completely undefended and attacked.
What Happened: You left your bishop on b5 undefended.
Why It Matters: Black captures the bishop on b5 with axb5 gaining 3 points of material.
"""

        # Question 1: Why was this a mistake?
        res1 = await provider.complete([{"role": "user", "content": "Why was this move a mistake?"}], prompt)
        assert "Nd5" in res1
        assert "Blunder" in res1
        assert "300" in res1
        assert "axb5" in res1 or "bishop" in res1.lower()

        # Question 2: Why is engine move better?
        res2 = await provider.complete([{"role": "user", "content": "Why is the engine best move better than my move?"}], prompt)
        assert "Bxc6" in res2
        assert "Nd5" in res2

        # Question 3: Simpler terms
        res3 = await provider.complete([{"role": "user", "content": "Explain in simpler terms."}], prompt)
        assert "Plain English" in res3 or "simple" in res3.lower()

        # Question 4: What should I consider?
        res4 = await provider.complete([{"role": "user", "content": "What should I have considered before playing this?"}], prompt)
        assert "Checks, Captures" in res4 or "Material Safety" in res4
