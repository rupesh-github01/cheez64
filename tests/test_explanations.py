import unittest
import chess
from pathlib import Path
import sys

# Ensure src is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root / "src") not in sys.path:
    sys.path.insert(0, str(repo_root / "src"))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.models import MoveAnalysis, CandidateMove
from src.tactical_analysis import (
    detect_tactical_consequence,
    CATEGORY_MATERIAL_LOSS,
    CATEGORY_MISSED_CAPTURE,
    CATEGORY_HANGING_PIECE,
    CATEGORY_MISSED_CHECK_OR_FORCING,
    CATEGORY_UNCLASSIFIED,
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_LOW,
)
from src.explanations import (
    MoveExplanation,
    generate_move_explanation,
    format_variation,
    format_material_points,
    is_forking_check,
)


class TestMoveExplanations(unittest.TestCase):
    """
    Deterministic unit tests for evidence-grounded move explanation generation.
    """

    def test_white_missed_capture(self):
        """
        Verify explanation when White misses a winning capture (e.g. Qxc5 winning a pawn).
        """
        board_before = chess.Board("r3r1k1/p1pq1ppp/5n2/2p5/3P4/7P/PPQN1PP1/R4RK1 w - - 0 16")
        board_after = chess.Board("r3r1k1/p1pq1ppp/5n2/2P5/8/7P/PPQN1PP1/R4RK1 b - - 0 16")

        analysis = MoveAnalysis(
            move_number=16,
            color="White",
            played_move="dxc5",
            best_move="Qxc5",
            evaluation_before=90,
            evaluation_after=-495,
            centipawn_loss=585,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[CandidateMove("Qxc5", 90, ["Qxc5", "Re2", "Rad1", "g6"])],
            pv_after=["Re2"],
            classification="blunder",
        )
        analysis.tactical_finding = detect_tactical_consequence(analysis).to_dict()

        exp = generate_move_explanation(analysis, board_before=board_before, board_after=board_after)

        self.assertIsInstance(exp, MoveExplanation)
        self.assertEqual(exp.motif, CATEGORY_MISSED_CAPTURE)
        self.assertEqual(exp.better_move, "Qxc5")
        self.assertIn("You missed Qxc5, which wins a pawn", exp.summary)
        self.assertIn("dxc5", exp.what_happened)
        self.assertIn("c5", exp.what_happened)
        self.assertIn("1 point", exp.why_it_matters)
        self.assertEqual(exp.variation, "Qxc5 Re2 Rad1 g6")
        self.assertEqual(exp.confidence, CONFIDENCE_HIGH)

    def test_black_missed_capture(self):
        """
        Verify explanation when Black misses a winning capture (e.g. dxe5 winning a knight).
        """
        board_before = chess.Board("r3kbnr/ppp2ppp/3pbq2/4N3/4P3/2N5/PPP1BPPP/R1BQK2R b KQkq - 0 8")
        board_after = chess.Board("r3kbnr/ppp2ppp/3pb3/4q3/4P3/2N5/PPP1BPPP/R1BQK2R w KQkq - 0 9")

        analysis = MoveAnalysis(
            move_number=8,
            color="Black",
            played_move="Qxe5",
            best_move="dxe5",
            evaluation_before=-75,
            evaluation_after=-205,
            centipawn_loss=130,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[CandidateMove("dxe5", -75, ["dxe5", "O-O", "Qd8", "Qe1"])],
            pv_after=["O-O"],
            classification="mistake",
        )
        analysis.tactical_finding = detect_tactical_consequence(analysis).to_dict()

        exp = generate_move_explanation(analysis, board_before=board_before, board_after=board_after)

        self.assertEqual(exp.motif, CATEGORY_MISSED_CAPTURE)
        self.assertEqual(exp.better_move, "dxe5")
        self.assertIn("You missed dxe5, which wins a knight", exp.summary)
        self.assertIn("Qxe5", exp.what_happened)
        self.assertIn("e5", exp.what_happened)
        self.assertIn("3 points", exp.why_it_matters)
        self.assertEqual(exp.variation, "dxe5 O-O Qd8 Qe1")

    def test_material_loss_explanation(self):
        """
        Verify explanation for material loss in played continuation.
        Black plays 32... Ra8 instead of 32... a5, losing a pawn to 33. Rxc6.
        """
        board_before = chess.Board("5rk1/1R3p1p/p1p2pp1/2P5/1P6/P3R1PP/5PK1/r7 b - - 2 32")
        board_after = chess.Board("r5k1/1R3p1p/p1p2pp1/2P5/1P6/P3R1PP/5PK1/r7 w - - 3 33")

        analysis = MoveAnalysis(
            move_number=32,
            color="Black",
            played_move="Ra8",
            best_move="a5",
            evaluation_before=-208,
            evaluation_after=-455,
            centipawn_loss=247,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[CandidateMove("a5", -208, ["a5", "bxa5", "Rc1", "Ree7", "Rxc5", "a6"])],
            pv_after=["Rc7", "f5", "Rxc6"],
            classification="serious_mistake",
        )
        analysis.tactical_finding = detect_tactical_consequence(analysis).to_dict()

        exp = generate_move_explanation(analysis, board_before=board_before, board_after=board_after)

        self.assertEqual(exp.motif, CATEGORY_MATERIAL_LOSS)
        self.assertEqual(exp.better_move, "a5")
        self.assertIn("Ra8", exp.summary)
        self.assertIn("costing 1 point of material", exp.summary)
        self.assertIn("White", exp.what_happened)
        self.assertIn("net material deficit of 1 point", exp.why_it_matters)

    def test_genuine_hanging_piece_explanation(self):
        """
        Verify explanation for a genuinely hanging piece left undefended and capturable.
        Black plays 20... Rf8 leaving the rook on f8 undefended to 21. Qxf8+.
        """
        board_before = chess.Board("1k5r/ppp3Qp/5n2/q1r5/5P2/5B2/PPP3PP/R4RK1 b - - 0 20")
        board_after = chess.Board("1k3r2/ppp3Qp/5n2/q1r5/5P2/5B2/PPP3PP/R4RK1 w - - 1 21")

        analysis = MoveAnalysis(
            move_number=20,
            color="Black",
            played_move="Rf8",
            best_move="Rc8",
            evaluation_before=-786,
            evaluation_after=-99998,
            centipawn_loss=1000,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[CandidateMove("Rc8", -786, ["Rc8", "Qxf6"])],
            pv_after=["Qxf8+", "Ne8", "Qxe8#"],
            classification="blunder",
        )
        analysis.tactical_finding = detect_tactical_consequence(analysis).to_dict()

        exp = generate_move_explanation(analysis, board_before=board_before, board_after=board_after)

        self.assertEqual(exp.motif, CATEGORY_HANGING_PIECE)
        self.assertIn("Your rook on f8 was left vulnerable and could be captured", exp.summary)
        self.assertIn("completely undefended", exp.what_happened)
        self.assertIn("White can respond with Qxf8+", exp.why_it_matters)
        self.assertIn("5 points", exp.why_it_matters)

    def test_missed_forcing_fork_check_explanation(self):
        """
        Verify explanation for missed forcing check that forks the king and another piece.
        White plays 17. Ne3 instead of 17. Nc7+ (royal fork of King and a8 Rook).
        """
        board_before = chess.Board("rq2k2r/p3p2p/4p1p1/3Nn3/5B2/1P1P4/P3PPQP/4K2R w Kkq - 0 17")
        board_after = chess.Board("rq2k2r/p3p2p/4p1p1/4n3/5B2/1P1PN3/P3PPQP/4K2R b Kkq - 1 17")

        analysis = MoveAnalysis(
            move_number=17,
            color="White",
            played_move="Ne3",
            best_move="Nc7+",
            evaluation_before=508,
            evaluation_after=-67,
            centipawn_loss=575,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[CandidateMove("Nc7+", 508, ["Nc7+", "Kd7", "Bxe5", "Qb4+"])],
            pv_after=["Qb4+", "Kf1"],
            classification="blunder",
        )
        analysis.tactical_finding = detect_tactical_consequence(analysis).to_dict()

        exp = generate_move_explanation(analysis, board_before=board_before, board_after=board_after)

        self.assertEqual(exp.motif, CATEGORY_MISSED_CHECK_OR_FORCING)
        self.assertEqual(exp.better_move, "Nc7+")
        self.assertIn("Nc7+ was stronger because it gives check while creating a fork", exp.summary)
        self.assertIn("Black", exp.why_it_matters)
        self.assertIn("5.75 pawns", exp.why_it_matters)

    def test_unclassified_move_explanation(self):
        """
        Verify explanation for a positional/endgame mistake with no tactical motif.
        White plays 1. g4 (Grob opening).
        """
        board_before = chess.Board(chess.STARTING_FEN)
        board_after = chess.Board("rnbqkbnr/pppppppp/8/8/6P1/8/PPPPPP1P/RNBQKBNR b KQkq - 0 1")

        analysis = MoveAnalysis(
            move_number=1,
            color="White",
            played_move="g4",
            best_move="d4",
            evaluation_before=32,
            evaluation_after=-129,
            centipawn_loss=161,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[CandidateMove("d4", 32, ["d4", "d5", "c4", "e6"])],
            pv_after=["e5", "h3", "d5", "Bg2"],
            classification="serious_mistake",
        )
        analysis.tactical_finding = detect_tactical_consequence(analysis).to_dict()

        exp = generate_move_explanation(analysis, board_before=board_before, board_after=board_after)

        self.assertEqual(exp.motif, CATEGORY_UNCLASSIFIED)
        self.assertEqual(exp.better_move, "d4")
        self.assertIn("g4 was inaccurate, conceding an evaluation drop of 1.61 pawns compared to d4", exp.summary)
        self.assertIn("No immediate tactical loss or hanging piece was verified", exp.why_it_matters)
        self.assertIn("positional or endgame factors", exp.why_it_matters)

    def test_mate_terminal_position_explanation(self):
        """
        Verify explanation when a player blunders into a forced checkmate.
        Game 3 Move 29: Black plays gxh6 allowing immediate mate 30. Qxf7#.
        """
        board_before = chess.Board("1r5r/Q4ppk/4p2P/1q1pP3/2n5/bPB4P/N1P2P2/1K1R3R b - - 0 29")
        board_after = chess.Board("1r5r/Q4p1k/4p2p/1q1pP3/2n5/bPB4P/N1P2P2/1K1R3R w - - 0 30")

        analysis = MoveAnalysis(
            move_number=29,
            color="Black",
            played_move="gxh6",
            best_move="Rb7",
            evaluation_before=286,
            evaluation_after=-99999,
            centipawn_loss=1000,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[CandidateMove("Rb7", 286, ["Rb7", "Qd4"])],
            pv_after=["Qxf7#"],
            classification="blunder",
        )
        analysis.tactical_finding = detect_tactical_consequence(analysis).to_dict()

        exp = generate_move_explanation(analysis, board_before=board_before, board_after=board_after)

        self.assertEqual(exp.motif, CATEGORY_UNCLASSIFIED)
        self.assertIn("blundered into a forced checkmate against you", exp.summary)
        self.assertIn("White", exp.what_happened)
        self.assertIn("Rb7", exp.why_it_matters)

    def test_short_pv_limitation_preserved(self):
        """
        Verify that short PV limitations from tactical findings are faithfully preserved.
        """
        board_before = chess.Board("r3kbnr/ppp2ppp/3pbq2/4N3/4P3/2N5/PPP1BPPP/R1BQK2R b KQkq - 0 8")
        board_after = chess.Board("r3kbnr/ppp2ppp/3pb3/4q3/4P3/2N5/PPP1BPPP/R1BQK2R w KQkq - 0 9")

        analysis = MoveAnalysis(
            move_number=8,
            color="Black",
            played_move="Qxe5",
            best_move="dxe5",
            evaluation_before=-75,
            evaluation_after=-205,
            centipawn_loss=130,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[CandidateMove("dxe5", -75, ["dxe5"])],  # Short 1-ply PV
            pv_after=["O-O"],
            classification="mistake",
        )
        analysis.tactical_finding = detect_tactical_consequence(analysis).to_dict()

        exp = generate_move_explanation(analysis, board_before=board_before, board_after=board_after)

        self.assertEqual(exp.motif, CATEGORY_MISSED_CAPTURE)
        self.assertIsNotNone(exp.limitations)
        self.assertIn("short", exp.limitations.lower())

    def test_white_and_black_perspectives(self):
        """
        Verify that player color perspectives are consistently maintained.
        White mover refers to opponent as Black; Black mover refers to opponent as White.
        """
        # White move
        analysis_white = MoveAnalysis(
            move_number=1,
            color="White",
            played_move="f3",
            best_move="e4",
            evaluation_before=20,
            evaluation_after=-120,
            centipawn_loss=140,
            fen_before=chess.STARTING_FEN,
            fen_after="rnbqkbnr/pppppppp/8/8/8/5P2/PPPPP1PP/RNBQKBNR b KQkq - 0 1",
            candidates=[CandidateMove("e4", 20, ["e4", "e5"])],
            classification="mistake",
        )
        analysis_white.tactical_finding = {
            "category": CATEGORY_UNCLASSIFIED,
            "confidence": CONFIDENCE_LOW,
            "played_move": "f3",
            "better_move": "e4",
            "evidence": {"centipawn_loss": 140},
            "limitations": None
        }
        exp_white = generate_move_explanation(analysis_white)
        self.assertIn("f3 was inaccurate", exp_white.summary)

        # Black move
        analysis_black = MoveAnalysis(
            move_number=1,
            color="Black",
            played_move="f6",
            best_move="e5",
            evaluation_before=-20,
            evaluation_after=-160,
            centipawn_loss=140,
            fen_before="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            fen_after="rnbqkbnr/ppppp1pp/5p2/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            candidates=[CandidateMove("e5", -20, ["e5", "Nf3"])],
            classification="mistake",
        )
        analysis_black.tactical_finding = {
            "category": CATEGORY_UNCLASSIFIED,
            "confidence": CONFIDENCE_LOW,
            "played_move": "f6",
            "better_move": "e5",
            "evidence": {"centipawn_loss": 140},
            "limitations": None
        }
        exp_black = generate_move_explanation(analysis_black)
        self.assertIn("f6 was inaccurate", exp_black.summary)

    def test_non_critical_best_and_forced_moves(self):
        """
        Verify explanations for non-critical best and forced moves.
        """
        # Forced move
        analysis_forced = MoveAnalysis(
            move_number=10,
            color="White",
            played_move="Kh1",
            best_move="Kh1",
            evaluation_before=0,
            evaluation_after=0,
            centipawn_loss=0,
            fen_before=chess.STARTING_FEN,
            fen_after=chess.STARTING_FEN,
            is_forced=True,
            classification="forced",
        )
        exp_forced = generate_move_explanation(analysis_forced)
        self.assertEqual(exp_forced.motif, "forced")
        self.assertIn("forced", exp_forced.summary)

        # Best move (non-forced)
        analysis_best = MoveAnalysis(
            move_number=1,
            color="White",
            played_move="e4",
            best_move="e4",
            evaluation_before=25,
            evaluation_after=25,
            centipawn_loss=0,
            fen_before=chess.STARTING_FEN,
            fen_after=chess.STARTING_FEN,
            classification="best",
        )
        exp_best = generate_move_explanation(analysis_best)
        self.assertEqual(exp_best.motif, "best_move")
        self.assertIn("recommended", exp_best.summary)

    def test_material_loss_even_trade_with_candidate_win(self):
        """
        Regression test: When played line is an equal exchange (played_line_net == 0)
        and candidate line wins material (candidate_line_net == 3), the explanation
        must state that the player missed winning material, rather than falsely claiming
        a piece on the trade square was lost.
        """
        analysis = MoveAnalysis(
            move_number=16,
            color="Black",
            played_move="Rad8",
            best_move="Re2",
            evaluation_before=135,
            evaluation_after=-479,
            centipawn_loss=614,
            fen_before="r3r1k1/p1pq1ppp/5n2/2P5/8/7P/PPQN1PP1/R4RK1 b - - 0 16",
            fen_after="3rr1k1/p1pq1ppp/5n2/2P5/8/7P/PPQN1PP1/R4RK1 w - - 1 17",
            classification="blunder",
        )
        evidence = {
            "net_material_loss": 3,
            "played_line_net": 0,
            "candidate_line_net": 3,
            "played_captures": [
                {"move": "Rxe1+", "captured_piece": "rook", "captured_value": 5, "by_color": "Black"},
                {"move": "Rxe1", "captured_piece": "rook", "captured_value": 5, "by_color": "White"}
            ],
            "candidate_captures": [
                {"move": "Rxd2", "captured_piece": "knight", "captured_value": 3, "by_color": "Black"}
            ],
            "played_sequence": ["Rad8", "Nf3", "h6", "Rfe1", "Rxe1+", "Rxe1", "a5"],
            "better_sequence": ["Re2", "Rad1", "Rd8", "c6", "Rxd2", "Qa4"]
        }
        analysis.tactical_finding = {
            "category": CATEGORY_MATERIAL_LOSS,
            "confidence": CONFIDENCE_HIGH,
            "played_move": "Rad8",
            "better_move": "Re2",
            "evidence": evidence
        }
        exp = generate_move_explanation(analysis)
        self.assertEqual(exp.motif, CATEGORY_MATERIAL_LOSS)
        self.assertIn("miss out on winning 3 points of material compared to Re2", exp.summary)
        self.assertNotIn("rook on e1 is lost", exp.summary)

    def test_material_loss_player_gained_material(self):
        """
        Regression test: When the player actually gained net material in the played line
        (e.g. +2 points) but the candidate line gained +3 points, the explanation must NEVER
        say 'your pawn is lost' or 'you lost material'.
        """
        analysis = MoveAnalysis(
            move_number=37,
            color="White",
            played_move="Kb3",
            best_move="b5",
            evaluation_before=150,
            evaluation_after=63,
            centipawn_loss=87,
            fen_before="8/1p1k1p1p/p7/1P3p2/1P1K1P2/P5P1/n6P/8 w - - 1 37",
            fen_after="8/1p1k1p1p/p7/1P3p2/1PK2P2/P5P1/n6P/8 b - - 2 37",
            classification="inaccuracy",
        )
        evidence = {
            "net_material_loss": 1,
            "played_line_net": 2,
            "candidate_line_net": 3,
            "played_captures": [
                {"move": "Nxb4", "captured_piece": "pawn", "captured_value": 1, "by_color": "Black"},
                {"move": "Bxb4", "captured_piece": "knight", "captured_value": 3, "by_color": "White"}
            ],
            "candidate_captures": [
                {"move": "Kxa2", "captured_piece": "knight", "captured_value": 3, "by_color": "White"}
            ],
            "played_sequence": ["Kb3", "Nxb4", "Bxb4"],
            "better_sequence": ["b5", "Kd7", "Kb3", "Kc7", "Kxa2"]
        }
        analysis.tactical_finding = {
            "category": CATEGORY_MATERIAL_LOSS,
            "confidence": CONFIDENCE_HIGH,
            "played_move": "Kb3",
            "better_move": "b5",
            "evidence": evidence
        }
        exp = generate_move_explanation(analysis)
        self.assertEqual(exp.motif, CATEGORY_MATERIAL_LOSS)
        self.assertIn("you gain 2 points of material, but b5 wins 3 points", exp.summary)
        self.assertNotIn("is lost", exp.summary)

    def test_material_loss_queen_trade_with_piece_lost(self):
        """
        Regression test: When queens were traded and a minor piece was lost without
        compensation, the explanation must identify the lost minor piece (knight on b4),
        NOT the traded queen on d3.
        """
        analysis = MoveAnalysis(
            move_number=26,
            color="Black",
            played_move="Qd3",
            best_move="Qd5",
            evaluation_before=450,
            evaluation_after=-25,
            centipawn_loss=475,
            fen_before="2r2rk1/pp3pp1/4p2p/3p4/Nn6/3q2P1/PP3PBP/R2QR1K1 b - - 1 26",
            fen_after="2r2rk1/pp3pp1/4p2p/3p4/Nn6/3q2P1/PP3PBP/R2QR1K1 w - - 2 27",
            classification="blunder",
        )
        evidence = {
            "net_material_loss": 3,
            "played_line_net": -3,
            "candidate_line_net": 0,
            "played_captures": [
                {"move": "Nxd3", "captured_piece": "queen", "captured_value": 9, "by_color": "White"},
                {"move": "Rxf7", "captured_piece": "queen", "captured_value": 9, "by_color": "Black"},
                {"move": "Nxb4", "captured_piece": "knight", "captured_value": 3, "by_color": "White"}
            ],
            "candidate_captures": [],
            "played_sequence": ["Qd3", "Nxd3", "Rxf7", "Nxb4"],
            "better_sequence": ["Qd5", "Qf8", "Qd6"]
        }
        analysis.tactical_finding = {
            "category": CATEGORY_MATERIAL_LOSS,
            "confidence": CONFIDENCE_HIGH,
            "played_move": "Qd3",
            "better_move": "Qd5",
            "evidence": evidence
        }
        exp = generate_move_explanation(analysis)
        self.assertEqual(exp.motif, CATEGORY_MATERIAL_LOSS)
        self.assertIn("your knight on b4 is lost, costing 3 points of material", exp.summary)
        self.assertNotIn("queen on d3 is lost", exp.summary)


if __name__ == "__main__":
    unittest.main()
