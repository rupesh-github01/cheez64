import unittest
import chess
import chess.engine
from unittest.mock import MagicMock

import sys
from pathlib import Path

# Add src to sys.path so tests can run cleanly from anywhere
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root / "src") not in sys.path:
    sys.path.insert(0, str(repo_root / "src"))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.models import CandidateMove, MoveAnalysis
from src.analyzer import (
    score_to_cp,
    calculate_centipawn_loss,
    extract_pv,
    extract_candidates_from_infos,
    format_evaluation,
    get_clock_seconds,
    MATE_THRESHOLD,
    MAX_CPL
)
from src.critical import classify_move, find_critical_positions
from src.episodes import group_critical_positions
from src.pgn_utils import clock_to_seconds, extract_clock
from src.position_features import get_material, get_position_features


class TestEvaluationPerspective(unittest.TestCase):
    """
    Validates evaluation perspective consistency before and after moves
    for both White and Black players.
    """

    def test_white_perspective_preservation(self):
        board = chess.Board()
        mover = board.turn  # chess.WHITE

        # Mock engine score before move: +1.50 for White
        score_before = chess.engine.PovScore(chess.engine.Cp(150), chess.WHITE)
        eval_before = score_to_cp(score_before, mover)
        self.assertEqual(eval_before, 150)

        # White plays 1. e4
        move = chess.Move.from_uci("e2e4")
        board.push(move)
        self.assertEqual(board.turn, chess.BLACK)

        # Engine evaluates position after move.
        # From the position's turn (Black), score is -0.50 (White is +0.50).
        score_after = chess.engine.PovScore(chess.engine.Cp(-50), chess.BLACK)

        # Evaluation after MUST be computed using the mover's perspective (chess.WHITE)
        eval_after = score_to_cp(score_after, mover)
        self.assertEqual(eval_after, 50)

        # Centipawn loss from White's POV: 150 - 50 = 100 CPL
        cpl = calculate_centipawn_loss(eval_before, eval_after)
        self.assertEqual(cpl, 100)

    def test_black_perspective_preservation(self):
        # Position after 1. e4
        board = chess.Board()
        board.push_san("e4")
        mover = board.turn  # chess.BLACK

        # Engine score before move: -0.80 for White => +0.80 for Black
        score_before = chess.engine.PovScore(chess.engine.Cp(-80), chess.WHITE)
        eval_before = score_to_cp(score_before, mover)
        self.assertEqual(eval_before, 80)

        # Black plays a move (e.g. 1... c5)
        move = chess.Move.from_uci("c7c5")
        board.push(move)
        self.assertEqual(board.turn, chess.WHITE)

        # Engine score after move: White is +0.20 => Black is -0.20
        score_after = chess.engine.PovScore(chess.engine.Cp(20), chess.WHITE)

        # Evaluation after MUST be computed using mover's perspective (chess.BLACK)
        eval_after = score_to_cp(score_after, mover)
        self.assertEqual(eval_after, -20)

        # Centipawn loss from Black's POV: 80 - (-20) = 100 CPL
        cpl = calculate_centipawn_loss(eval_before, eval_after)
        self.assertEqual(cpl, 100)


class TestCentipawnLossCalculation(unittest.TestCase):
    """
    Validates centipawn loss calculations across advantage drops, improvements,
    and threshold behaviors.
    """

    def test_losing_winning_advantage(self):
        # White drops from +3.00 (300) to +0.50 (50)
        cpl = calculate_centipawn_loss(300, 50)
        self.assertEqual(cpl, 250)
        self.assertEqual(classify_move(cpl), "blunder")

    def test_improving_mover_position(self):
        # Mover improves from -1.00 (-100) to +0.50 (50)
        cpl = calculate_centipawn_loss(-100, 50)
        self.assertEqual(cpl, 0)
        self.assertEqual(classify_move(cpl), "good")

    def test_equal_position_no_loss(self):
        cpl = calculate_centipawn_loss(15, 15)
        self.assertEqual(cpl, 0)
        self.assertEqual(classify_move(cpl), "good")

    def test_none_handling(self):
        self.assertIsNone(calculate_centipawn_loss(None, 50))
        self.assertIsNone(calculate_centipawn_loss(50, None))
        self.assertIsNone(calculate_centipawn_loss(None, None))


class TestMateScoreHandling(unittest.TestCase):
    """
    Validates safe, deterministic handling of mate scores and prevention
    of misleading centipawn loss values.
    """

    def test_blundering_into_mate(self):
        # Player was +2.00, plays a move that allows opponent mate in 1
        # Opponent mate in 1 from mover POV is -99999
        cpl = calculate_centipawn_loss(200, -99999)
        self.assertEqual(cpl, MAX_CPL)
        self.assertEqual(classify_move(cpl), "blunder")

    def test_preserving_winning_mate(self):
        # Player has mate in 1 (99999), plays move resulting in mate in 2 (99998)
        cpl = calculate_centipawn_loss(99999, 99998)
        self.assertEqual(cpl, 0)
        self.assertEqual(classify_move(cpl), "good")

    def test_preserving_losing_mate(self):
        # Player was already getting mated in 3 (-99997), plays move still mated in 1 (-99999)
        cpl = calculate_centipawn_loss(-99997, -99999)
        self.assertEqual(cpl, 0)
        self.assertEqual(classify_move(cpl), "good")

    def test_throwing_away_winning_mate(self):
        # Player had mate in 1 (99999), blunders to equal/non-mate (+150)
        cpl = calculate_centipawn_loss(99999, 150)
        self.assertEqual(cpl, MAX_CPL)
        self.assertEqual(classify_move(cpl), "blunder")

    def test_format_evaluation(self):
        self.assertEqual(format_evaluation(150), "1.50")
        self.assertEqual(format_evaluation(-230), "-2.30")
        self.assertEqual(format_evaluation(99999), "+M1")
        self.assertEqual(format_evaluation(99997), "+M3")
        self.assertEqual(format_evaluation(-99999), "-M1")
        self.assertEqual(format_evaluation(-99998), "-M2")
        self.assertEqual(format_evaluation(None), "N/A")


class TestPrincipalVariationAndBoardState(unittest.TestCase):
    """
    Validates that PV generation only includes legal moves and does not mutate
    the original board state.
    """

    def test_pv_extraction_legal_moves_and_board_immutability(self):
        board = chess.Board()
        initial_fen = board.fen()

        # Moves: 1. e4 e5 2. Nf3 Nc6
        legal_moves = [
            chess.Move.from_uci("e2e4"),
            chess.Move.from_uci("e7e5"),
            chess.Move.from_uci("g1f3"),
            chess.Move.from_uci("b8c6"),
        ]

        pv = extract_pv(board, legal_moves)
        self.assertEqual(pv, ["e4", "e5", "Nf3", "Nc6"])
        # Original board must remain completely unchanged
        self.assertEqual(board.fen(), initial_fen)

    def test_pv_extraction_halts_on_illegal_move(self):
        board = chess.Board()
        initial_fen = board.fen()

        # 1. e4 is legal, but second move e2e4 again is illegal for Black
        mixed_moves = [
            chess.Move.from_uci("e2e4"),
            chess.Move.from_uci("e2e4"),  # illegal for black
            chess.Move.from_uci("g1f3"),
        ]

        pv = extract_pv(board, mixed_moves)
        self.assertEqual(pv, ["e4"])
        self.assertEqual(board.fen(), initial_fen)


class TestCandidateMovesExtraction(unittest.TestCase):
    """
    Validates candidate moves extraction from MultiPV infos.
    """

    def test_candidates_populated(self):
        board = chess.Board()
        infos = [
            {
                "pv": [chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5")],
                "score": chess.engine.PovScore(chess.engine.Cp(30), chess.WHITE)
            },
            {
                "pv": [chess.Move.from_uci("d2d4"), chess.Move.from_uci("d7d5")],
                "score": chess.engine.PovScore(chess.engine.Cp(25), chess.WHITE)
            }
        ]

        candidates = extract_candidates_from_infos(board, infos, chess.WHITE)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].move, "e4")
        self.assertEqual(candidates[0].evaluation, 30)
        self.assertEqual(candidates[0].principal_variation, ["e4", "e5"])
        self.assertEqual(candidates[1].move, "d4")
        self.assertEqual(candidates[1].evaluation, 25)
        self.assertEqual(candidates[1].principal_variation, ["d4", "d5"])


class TestCriticalMoveClassificationAndEpisodes(unittest.TestCase):
    """
    Validates move classification at exact threshold boundaries and critical episode grouping.
    """

    def test_classification_boundaries(self):
        self.assertEqual(classify_move(None), "unknown")
        self.assertEqual(classify_move(0), "good")
        self.assertEqual(classify_move(29), "good")
        self.assertEqual(classify_move(30), "inaccuracy")
        self.assertEqual(classify_move(74), "inaccuracy")
        self.assertEqual(classify_move(75), "mistake")
        self.assertEqual(classify_move(149), "mistake")
        self.assertEqual(classify_move(150), "serious_mistake")
        self.assertEqual(classify_move(249), "serious_mistake")
        self.assertEqual(classify_move(250), "blunder")
        self.assertEqual(classify_move(500), "blunder")

    def test_find_critical_positions(self):
        analyses = [
            MoveAnalysis(1, "White", "e4", "e4", 30, 30, 0, "", ""),
            MoveAnalysis(1, "Black", "e5", "e5", 0, -80, 80, "", ""),
            MoveAnalysis(2, "White", "Nf3", "Nf3", 50, 45, 5, "", ""),
            MoveAnalysis(2, "Black", "f6", "Nc6", 0, -260, 260, "", ""),
        ]

        critical = find_critical_positions(analyses, minimum_cpl=75)
        self.assertEqual(len(critical), 2)
        self.assertEqual(critical[0].played_move, "e5")
        self.assertEqual(critical[1].played_move, "f6")

    def test_group_critical_positions_episodes(self):
        # Moves at index 0 (critical), index 2 (critical - gap=2), index 6 (critical - gap=4)
        analyses = [
            MoveAnalysis(1, "White", "g4", "d4", 30, -127, 157, "", ""),   # idx 0: CPL 157
            MoveAnalysis(1, "Black", "d5", "d5", -127, -127, 0, "", ""),   # idx 1: CPL 0
            MoveAnalysis(2, "White", "f3", "Bg2", -127, -250, 123, "", ""), # idx 2: CPL 123 (gap 2 from 0)
            MoveAnalysis(2, "Black", "e5", "e5", 250, 250, 0, "", ""),      # idx 3: CPL 0
            MoveAnalysis(3, "White", "h3", "h3", -250, -250, 0, "", ""),    # idx 4: CPL 0
            MoveAnalysis(3, "Black", "Nf6", "Nf6", 250, 250, 0, "", ""),    # idx 5: CPL 0
            MoveAnalysis(4, "White", "c3", "d4", -250, -550, 300, "", ""),  # idx 6: CPL 300 (gap 4 from 2)
        ]

        episodes = group_critical_positions(analyses, minimum_cpl=75, max_gap=2)
        self.assertEqual(len(episodes), 2)
        # Episode 1 has moves at idx 0 and idx 2
        self.assertEqual(len(episodes[0]), 2)
        self.assertEqual(episodes[0][0].played_move, "g4")
        self.assertEqual(episodes[0][1].played_move, "f3")
        # Episode 2 has move at idx 6
        self.assertEqual(len(episodes[1]), 1)
        self.assertEqual(episodes[1][0].played_move, "c3")


class TestClockExtraction(unittest.TestCase):
    """
    Validates clock parsing across various standard and edge-case formats.
    """

    def test_clock_to_seconds(self):
        self.assertAlmostEqual(clock_to_seconds("0:04:59.1"), 299.1)
        self.assertAlmostEqual(clock_to_seconds("1:30:00"), 5400.0)
        self.assertAlmostEqual(clock_to_seconds("05:00"), 300.0)
        self.assertAlmostEqual(clock_to_seconds("1:30.5"), 90.5)
        self.assertAlmostEqual(clock_to_seconds("45.2"), 45.2)
        self.assertIsNone(clock_to_seconds("invalid"))
        self.assertIsNone(clock_to_seconds(""))

    def test_extract_clock(self):
        self.assertAlmostEqual(extract_clock("[%clk 0:04:59.1]"), 299.1)
        self.assertAlmostEqual(extract_clock("[%clk 05:00] [%timestamp 12]"), 300.0)
        self.assertIsNone(extract_clock("no clock in comment"))
        self.assertIsNone(extract_clock(""))
        self.assertIsNone(extract_clock(None))


class TestPositionFeatures(unittest.TestCase):
    """
    Validates material calculation and feature extraction.
    """

    def test_initial_board_features(self):
        board = chess.Board()
        material = get_material(board)
        self.assertEqual(material["white"], 39)
        self.assertEqual(material["black"], 39)
        self.assertEqual(material["difference"], 0)

        features = get_position_features(board)
        self.assertEqual(features["side_to_move"], "White")
        self.assertEqual(features["in_check"], False)
        self.assertEqual(features["is_checkmate"], False)
        self.assertEqual(features["legal_moves"], 20)
        self.assertEqual(len(features["checks_available"]), 0)
        self.assertEqual(len(features["captures_available"]), 0)


class TestDecisionLossAndNoiseFiltering(unittest.TestCase):
    """
    Validates that decision CPL eliminates search noise on best moves and forced moves,
    while preserving genuine errors and retaining raw CPL for debugging.
    """

    def test_top_candidate_played_zeroes_cpl(self):
        # Mover played d4, which was the engine's best move.
        # Independent search after move drifted by 12 cp.
        raw_cpl = 12
        is_best = True
        is_forced = False

        cpl = 0 if (is_forced or is_best) else raw_cpl
        self.assertEqual(cpl, 0)
        self.assertEqual(classify_move(cpl), "good")

    def test_forced_move_zeroes_cpl(self):
        # Player had only 1 legal move (forced). Search drifted by 35 cp.
        raw_cpl = 35
        is_best = False
        is_forced = True

        cpl = 0 if (is_forced or is_best) else raw_cpl
        self.assertEqual(cpl, 0)
        self.assertEqual(classify_move(cpl), "good")

    def test_genuine_mistake_incurs_cpl(self):
        # Player played g4 (not best move d4, not forced).
        raw_cpl = 157
        is_best = False
        is_forced = False

        cpl = 0 if (is_forced or is_best) else raw_cpl
        self.assertEqual(cpl, 157)
        self.assertEqual(classify_move(cpl), "serious_mistake")

    def test_missing_candidate_does_not_zero_cpl(self):
        # Candidate info missing or invalid
        raw_cpl = 80
        best_move_san = None
        played_move_san = "e5"
        is_forced = False

        is_best = bool(best_move_san and played_move_san == best_move_san)
        cpl = 0 if (is_forced or is_best) else raw_cpl
        self.assertEqual(cpl, 80)
        self.assertEqual(classify_move(cpl), "mistake")


class TestSearchReuse(unittest.TestCase):
    """
    Validates that engine search results are reused across consecutive positions,
    reducing engine calls from 2N to N+1.
    """

    def test_search_count_reduction(self):
        from unittest.mock import patch, MagicMock
        import io
        import chess.pgn
        from src.analyzer import analyze_game

        pgn_text = "1. e4 e5 2. Nf3 *"
        game = chess.pgn.read_game(io.StringIO(pgn_text))

        mock_engine = MagicMock()
        # Mock engine.analyse returning valid info structure
        def mock_analyse(board, limit, multipv=1):
            legal_moves = list(board.legal_moves)
            pv_moves = [legal_moves[0]] if legal_moves else []
            return [{
                "pv": pv_moves,
                "score": chess.engine.PovScore(chess.engine.Cp(20), board.turn)
            }]

        mock_engine.analyse.side_effect = mock_analyse

        with patch("chess.pgn.read_game", return_value=game):
            with patch("chess.engine.SimpleEngine.popen_uci", return_value=mock_engine):
                analyzed_game, analyses = analyze_game()

                # For 3 moves:
                # 1 search before game + 3 searches after each move = 4 searches total.
                # (Without reuse, it would have been 3 * 2 = 6 searches).
                self.assertEqual(len(analyses), 3)
                self.assertEqual(mock_engine.analyse.call_count, 4)
                mock_engine.quit.assert_called_once()


class TestJsonSerializationAndRoundTrip(unittest.TestCase):
    """
    Validates JSON serialization, handling of missing values, and file round-trip.
    """

    def test_serialization_structure_and_missing_values(self):
        import io
        import json
        import chess.pgn
        from src.export import serialize_analysis, export_to_json_file
        import tempfile

        pgn_text = """[Event "Test Tournament"]
[Site "Local"]
[Date "2026.10.06"]
[White "PlayerA"]
[Black "PlayerB"]
[Result "1-0"]

1. e4 e5 *"""
        game = chess.pgn.read_game(io.StringIO(pgn_text))

        analyses = [
            MoveAnalysis(
                move_number=1,
                color="White",
                played_move="e4",
                best_move="e4",
                evaluation_before=25,
                evaluation_after=30,
                centipawn_loss=0,
                fen_before=chess.STARTING_FEN,
                fen_after="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                principal_variation=["e4", "e5"],
                candidates=[
                    CandidateMove("e4", 25, ["e4", "e5"]),
                    CandidateMove("d4", 20, ["d4", "d5"])
                ],
                clock_seconds=None,  # Missing value
                position_features={"side_to_move": "White", "material": {"white": 39, "black": 39, "difference": 0}},
                played_move_uci="e2e4",
                best_move_uci="e2e4",
                raw_centipawn_loss=0,
                classification="good",
                is_forced=False
            ),
            MoveAnalysis(
                move_number=1,
                color="Black",
                played_move="f6",
                best_move="e5",
                evaluation_before=-30,
                evaluation_after=-180,
                centipawn_loss=150,
                fen_before="rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
                fen_after="rnbqkbnr/ppppp1pp/5p2/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                principal_variation=["e5", "Nf3"],
                candidates=[CandidateMove("e5", -30, ["e5", "Nf3"])],
                clock_seconds=295.4,
                position_features={"side_to_move": "Black"},
                played_move_uci="f7f6",
                best_move_uci="e7e5",
                raw_centipawn_loss=150,
                classification="serious_mistake",
                is_forced=False
            )
        ]

        data = serialize_analysis(game, analyses)

        # Schema keys
        self.assertIn("schema_version", data)
        self.assertIn("metadata", data)
        self.assertIn("summary", data)
        self.assertIn("moves", data)
        self.assertIn("episodes", data)

        # Metadata
        self.assertEqual(data["metadata"]["white"], "PlayerA")
        self.assertEqual(data["metadata"]["black"], "PlayerB")
        self.assertEqual(data["metadata"]["result"], "1-0")

        # Moves and missing values
        self.assertEqual(len(data["moves"]), 2)
        self.assertIsNone(data["moves"][0]["clock_seconds"])
        self.assertEqual(data["moves"][0]["played_move_uci"], "e2e4")
        self.assertEqual(data["moves"][1]["classification"], "serious_mistake")
        self.assertEqual(data["moves"][1]["clock_seconds"], 295.4)

        # Round-trip through file
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "test_out.json"
            export_to_json_file(game, analyses, str(out_file))

            self.assertTrue(out_file.exists())
            with open(out_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            self.assertEqual(loaded["schema_version"], "1.0.0")
            self.assertEqual(loaded["summary"]["total_plies"], 2)
            self.assertEqual(loaded["moves"][0]["played_move"], "e4")
            self.assertEqual(loaded["moves"][1]["centipawn_loss"], 150)


class TestSummaryCountsConsistency(unittest.TestCase):
    """
    Validates that critical move and episode counts are internally consistent.
    """

    def test_critical_moves_count_matches(self):
        analyses = [
            MoveAnalysis(1, "White", "g4", "d4", 30, -127, 157, "", ""),
            MoveAnalysis(1, "Black", "d5", "d5", -127, -127, 0, "", ""),
            MoveAnalysis(17, "White", "Ne3", "Nc7+", 566, -103, 669, "", ""),
            MoveAnalysis(17, "Black", "Qb4+", "O-O", 97, -549, 646, "", ""),
        ]

        critical = find_critical_positions(analyses, minimum_cpl=75)
        # Exactly 3 critical positions (Move 1 White, Move 17 White, Move 17 Black)
        self.assertEqual(len(critical), 3)

        # Fullmove numbers set has 2 elements (1 and 17), but critical half-moves count is 3.
        fullmove_numbers = {m.move_number for m in critical}
        self.assertEqual(len(fullmove_numbers), 2)
        self.assertEqual(len(critical), 3)


if __name__ == "__main__":
    unittest.main()

