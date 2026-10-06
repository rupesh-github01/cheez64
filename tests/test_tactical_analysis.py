import unittest
import chess
import sys
from pathlib import Path

# Add src to sys.path so tests can run cleanly from anywhere
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root / "src") not in sys.path:
    sys.path.insert(0, str(repo_root / "src"))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.models import MoveAnalysis, CandidateMove
from src.tactical_analysis import (
    detect_tactical_consequence,
    simulate_sequence,
    check_hanging_piece,
    check_missed_capture,
    check_missed_check_or_forcing_move,
    check_material_loss,
    CATEGORY_MATERIAL_LOSS,
    CATEGORY_MISSED_CAPTURE,
    CATEGORY_HANGING_PIECE,
    CATEGORY_MISSED_CHECK_OR_FORCING,
    CATEGORY_UNCLASSIFIED,
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_LOW,
)


class TestTacticalAnalysis(unittest.TestCase):
    """
    Deterministic unit tests for tactical consequence detector.
    Uses synthetic positions and verified legal move sequences.
    """

    def test_hanging_piece_detected(self):
        """
        Verify detection of a piece left completely undefended and captured in opponent's PV.
        Synthetic position: White Queen on g2, Black King on e8, Black Queen on f4, Black Rook on a8.
        Black plays 18... Qxf4 leaving the Rook on a8 undefended to 19. Qxa8+.
        """
        board_before = chess.Board("r3k2r/p3p2p/4p1p1/4n3/1q3B2/1P1PN3/P3PPQP/5K1R b kq - 3 18")
        # Black plays Qxf4
        move = chess.Move.from_uci("b4f4")
        board_after = board_before.copy()
        board_after.push(move)

        analysis = MoveAnalysis(
            move_number=18,
            color="Black",
            played_move="Qxf4",
            best_move="Rc8",
            evaluation_before=60,
            evaluation_after=-750,
            centipawn_loss=810,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            principal_variation=["Rc8", "Qg3", "Rc1+"],
            candidates=[CandidateMove("Rc8", 60, ["Rc8", "Qg3", "Rc1+"])],
            pv_after=["Qxa8+", "Kf7", "Qxh8"],
            classification="blunder",
        )

        finding = detect_tactical_consequence(analysis)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.category, CATEGORY_HANGING_PIECE)
        self.assertEqual(finding.confidence, CONFIDENCE_HIGH)
        self.assertEqual(finding.played_move, "Qxf4")
        self.assertEqual(finding.better_move, "Rc8")
        self.assertEqual(finding.evidence["hanging_piece"], "rook")
        self.assertEqual(finding.evidence["square"], "a8")
        self.assertEqual(finding.evidence["defenders_count"], 0)
        self.assertEqual(finding.evidence["opponent_capture_move"], "Qxa8+")

    def test_piece_attacked_but_not_lost_is_not_hanging(self):
        """
        Verify that a piece being attacked but sufficiently defended is NOT
        falsely classified as a hanging piece or tactical loss.
        E.g. 1. e4 e5 2. Nf3 Nc6: Black's pawn on e5 is attacked by Nf3, but defended by Nc6.
        Continuation is 3. Bb5 (not capturing e5).
        """
        board_before = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 1 2")
        board_after = chess.Board("r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 2 2")

        analysis = MoveAnalysis(
            move_number=3,
            color="White",
            played_move="Bb5",
            best_move="Bb5",
            evaluation_before=20,
            evaluation_after=20,
            centipawn_loss=0,  # not critical, but let's test check_hanging_piece directly
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            principal_variation=["Bb5", "a6"],
            pv_after=["a6", "Ba4"],
        )

        # Black's pawn on e5 is attacked by Nf3, but defended by Nc6
        hanging = check_hanging_piece(board_after, chess.BLACK, analysis.pv_after)
        self.assertIsNone(hanging)

    def test_missed_winning_capture(self):
        """
        Verify detection of a missed winning capture.
        Position: Black to move, White has an undefended Knight on d5.
        Candidate move 'exd5' captures the knight for a free piece.
        Played move 'Nxe5' misses the capture.
        """
        board_before = chess.Board("rq2k2r/p2np2p/4p1p1/3NN3/5B2/1P1P4/P3PPQP/4K2R b Kkq - 0 16")
        board_after = chess.Board("rq2k2r/p2np2p/4p1p1/3Nn3/5B2/1P1P4/P3PPQP/4K2R w Kkq - 0 17")

        analysis = MoveAnalysis(
            move_number=16,
            color="Black",
            played_move="Nxe5",
            best_move="exd5",
            evaluation_before=-267,
            evaluation_after=-508,
            centipawn_loss=241,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            principal_variation=["exd5", "Qxd5", "Nxe5", "Bxe5"],
            candidates=[CandidateMove("exd5", -267, ["exd5", "Qxd5", "Nxe5", "Bxe5"])],
            pv_after=["Ne3", "Qb4+"],
            classification="serious_mistake",
        )

        finding = detect_tactical_consequence(analysis)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.category, CATEGORY_MISSED_CAPTURE)
        self.assertEqual(finding.confidence, CONFIDENCE_HIGH)
        self.assertEqual(finding.played_move, "Nxe5")
        self.assertEqual(finding.better_move, "exd5")
        self.assertEqual(finding.evidence["captured_piece"], "knight")
        self.assertEqual(finding.evidence["captured_value"], 3)
        self.assertEqual(finding.evidence["target_square"], "d5")

    def test_missed_check_or_forcing_move(self):
        """
        Verify detection when player misses an immediate forcing check.
        Position: White to move. White can play 17. Nc7+ (forking King and Rook with check).
        Instead, White plays 17. Ne3 (passive retreat, not a check).
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
            principal_variation=["Nc7+", "Kd7", "Bxe5", "Qb4+"],
            candidates=[CandidateMove("Nc7+", 508, ["Nc7+", "Kd7", "Bxe5", "Qb4+"])],
            pv_after=["Qb4+", "Kf1"],
            classification="blunder",
        )

        finding = detect_tactical_consequence(analysis)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.category, CATEGORY_MISSED_CHECK_OR_FORCING)
        self.assertEqual(finding.confidence, CONFIDENCE_HIGH)
        self.assertEqual(finding.played_move, "Ne3")
        self.assertEqual(finding.better_move, "Nc7+")
        self.assertTrue(finding.evidence["is_check"])
        self.assertFalse(finding.evidence["played_move_is_check"])

    def test_material_loss_in_played_continuation(self):
        """
        Verify detection of material loss over a multi-move sequence.
        White plays 13. b3 instead of 13. Ng5.
        In the played continuation, Black plays Nd5 followed by capturing White's Rook on a1 (Bxa1).
        """
        board_before = chess.Board("rq2k2r/p2np1bp/4pnp1/8/8/2NP1N2/PP2PPQP/R1B1K2R w KQkq - 1 13")
        board_after = chess.Board("rq2k2r/p2np1bp/4pnp1/8/8/1PNP1N2/P3PPQP/R1B1K2R b KQkq - 0 13")

        analysis = MoveAnalysis(
            move_number=13,
            color="White",
            played_move="b3",
            best_move="Ng5",
            evaluation_before=378,
            evaluation_after=84,
            centipawn_loss=294,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            principal_variation=["Ng5", "a5", "Nxe6"],
            candidates=[CandidateMove("Ng5", 378, ["Ng5", "a5", "Nxe6"])],
            pv_after=["Nd5", "Nxd5", "Bxa1"],
            classification="blunder",
        )

        finding = detect_tactical_consequence(analysis)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.category, CATEGORY_MATERIAL_LOSS)
        self.assertIn("net_material_loss", finding.evidence)
        self.assertGreaterEqual(finding.evidence["net_material_loss"], 1)

    def test_positional_blunder_is_unclassified(self):
        """
        Verify that a critical move with no immediate concrete material loss,
        missed capture, hanging piece, or missed check is correctly categorized as 'unclassified'.
        E.g. 1. g4 (Grob opening): positional weakness, no immediate tactical loss.
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
            principal_variation=["d4", "d5", "c4", "e6"],
            candidates=[CandidateMove("d4", 32, ["d4", "d5", "c4", "e6"])],
            pv_after=["e5", "h3", "d5", "Bg2"],
            classification="serious_mistake",
        )

        finding = detect_tactical_consequence(analysis)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.category, CATEGORY_UNCLASSIFIED)
        self.assertEqual(finding.confidence, CONFIDENCE_LOW)
        self.assertIn("reason", finding.evidence)

    def test_empty_or_invalid_pvs_handled_safely(self):
        """
        Verify that empty or malformed PVs do not crash and fall back cleanly.
        """
        board_before = chess.Board()
        board_after = chess.Board()
        board_after.push_san("e4")

        analysis = MoveAnalysis(
            move_number=1,
            color="White",
            played_move="e4",
            best_move="d4",
            evaluation_before=30,
            evaluation_after=-70,
            centipawn_loss=100,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            principal_variation=["invalid_move_san", "also_invalid"],
            candidates=[CandidateMove("d4", 30, ["invalid_san_1", "invalid_san_2"])],
            pv_after=["invalid_pv_after"],
            classification="mistake",
        )

        finding = detect_tactical_consequence(analysis)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.category, CATEGORY_UNCLASSIFIED)

    def test_missing_candidates_handled_safely(self):
        """
        Verify safe handling when candidates list is empty and best_move is missing.
        """
        board_before = chess.Board()
        board_after = chess.Board()
        board_after.push_san("e4")

        analysis = MoveAnalysis(
            move_number=1,
            color="White",
            played_move="e4",
            best_move="",
            evaluation_before=30,
            evaluation_after=-80,
            centipawn_loss=110,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            candidates=[],
            principal_variation=[],
            pv_after=[],
            classification="mistake",
        )

        finding = detect_tactical_consequence(analysis)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.category, CATEGORY_UNCLASSIFIED)
        self.assertIn("Missing", finding.limitations)

    def test_non_critical_move_returns_none(self):
        """
        Verify that non-critical moves (CPL < 75) return None.
        """
        board_before = chess.Board()
        board_after = chess.Board()
        board_after.push_san("e4")

        analysis = MoveAnalysis(
            move_number=1,
            color="White",
            played_move="e4",
            best_move="e4",
            evaluation_before=25,
            evaluation_after=25,
            centipawn_loss=0,
            fen_before=board_before.fen(),
            fen_after=board_after.fen(),
            classification="good",
        )

        finding = detect_tactical_consequence(analysis)
        self.assertIsNone(finding)


if __name__ == "__main__":
    unittest.main()
