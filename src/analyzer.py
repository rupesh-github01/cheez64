import chess
import chess.pgn
import chess.engine
from typing import Optional, List

try:
    from models import MoveAnalysis, CandidateMove
    from pgn_utils import extract_clock
    from critical import (
        classify_move,
        find_critical_positions
    )
    from position_features import (
        get_position_features
    )
    from episodes import (
        group_critical_positions
    )
    from export import (
        export_to_json_file
    )
except ImportError:
    from src.models import MoveAnalysis, CandidateMove
    from src.pgn_utils import extract_clock
    from src.critical import (
        classify_move,
        find_critical_positions
    )
    from src.position_features import (
        get_position_features
    )
    from src.episodes import (
        group_critical_positions
    )
    from src.export import (
        export_to_json_file
    )


PGN_PATH = "data/games/test_game.pgn"
STOCKFISH_PATH = "/opt/homebrew/bin/stockfish"

DEPTH = 16
MULTI_PV = 3

MATE_THRESHOLD = 90000
MAX_CPL = 1000


def evaluate_position(engine, board, depth: int = DEPTH):

    return engine.analyse(
        board,
        chess.engine.Limit(depth=depth)
    )


def score_to_cp(score, turn):

    if score is None:
        return None

    return score.pov(turn).score(
        mate_score=100000
    )


def calculate_centipawn_loss(
    evaluation_before: Optional[int],
    evaluation_after: Optional[int]
) -> Optional[int]:
    """
    Calculate centipawn loss from the mover's perspective.
    Both evaluations must be expressed from the mover's POV.

    Handles mate scores cleanly:
    - Non-mate positions: min(MAX_CPL, max(0, evaluation_before - evaluation_after))
    - Winning mate maintained: 0 CPL
    - Already in losing forced mate: 0 CPL
    - Blundered into forced mate or threw away forced mate: MAX_CPL (1000)
    """
    if evaluation_before is None or evaluation_after is None:
        return None

    is_mate_before = abs(evaluation_before) >= MATE_THRESHOLD
    is_mate_after = abs(evaluation_after) >= MATE_THRESHOLD

    # Normal non-mate evaluation
    if not is_mate_before and not is_mate_after:
        return min(MAX_CPL, max(0, evaluation_before - evaluation_after))

    # Player had a winning forced mate and kept a winning forced mate
    if evaluation_before >= MATE_THRESHOLD and evaluation_after >= MATE_THRESHOLD:
        return 0

    # Player was already lost in forced mate and remains in forced mate
    if evaluation_before <= -MATE_THRESHOLD and evaluation_after <= -MATE_THRESHOLD:
        return 0

    # Player threw away a forced winning mate
    if evaluation_before >= MATE_THRESHOLD and evaluation_after < MATE_THRESHOLD:
        return MAX_CPL

    # Player was not in forced mate, but blundered into a forced mate against them
    if evaluation_before > -MATE_THRESHOLD and evaluation_after <= -MATE_THRESHOLD:
        return MAX_CPL

    return min(MAX_CPL, max(0, evaluation_before - evaluation_after))


def extract_pv(board: chess.Board, moves: list, limit: int = 8) -> List[str]:
    """
    Extract legal SAN moves for a principal variation without mutating board.
    """
    pv = []
    pv_board = board.copy()

    for pv_move in moves[:limit]:
        if not pv_board.is_legal(pv_move):
            break
        pv.append(pv_board.san(pv_move))
        pv_board.push(pv_move)

    return pv


def get_clock_seconds(node):

    if node is None or not node.comment:
        return None

    return extract_clock(node.comment)


def extract_candidates_from_infos(board: chess.Board, infos: list, turn: chess.Color) -> List[CandidateMove]:
    """
    Build CandidateMove list from MultiPV engine analysis infos.
    """
    candidates = []

    for info in infos:
        pv_moves = info.get("pv", [])
        if not pv_moves:
            continue

        cand_move = pv_moves[0]
        cand_san = board.san(cand_move)
        evaluation = score_to_cp(
            info.get("score"),
            turn
        )
        pv = extract_pv(board, pv_moves)

        candidates.append(
            CandidateMove(
                move=cand_san,
                evaluation=evaluation,
                principal_variation=pv
            )
        )

    return candidates


def analyze_game(
    pgn_path: str = PGN_PATH,
    stockfish_path: str = STOCKFISH_PATH,
    depth: int = DEPTH,
    multipv: int = MULTI_PV,
):
    """
    Analyze a PGN game move-by-move.

    Optimized engine queries:
    Instead of searching each position twice (before and after), every unique
    board position is searched once with MultiPV at depth. The evaluation of
    position P_k serves as the 'after' score for move k and the 'before' score
    for move k+1, preserving mover perspectives.
    """
    # --------------------------------
    # Load PGN
    # --------------------------------

    with open(pgn_path, "r") as pgn_file:
        game = chess.pgn.read_game(pgn_file)

    if game is None:
        return None, []

    engine = chess.engine.SimpleEngine.popen_uci(
        stockfish_path
    )

    try:
        board = game.board()

        analyses = []

        node = game

        # Search initial pre-game position once with MultiPV
        current_infos = engine.analyse(
            board,
            chess.engine.Limit(depth=depth),
            multipv=multipv
        )

        # --------------------------------
        # Analyze every move
        # --------------------------------

        for move in game.mainline_moves():

            player = board.turn

            move_number = board.fullmove_number

            color = (
                "White"
                if player == chess.WHITE
                else "Black"
            )

            # Position before move
            fen_before = board.fen()
            pos_features = get_position_features(board)
            legal_moves_count = len(list(board.legal_moves))
            is_forced = (legal_moves_count == 1)

            # Actual move representation
            played_move_san = board.san(move)
            played_move_uci = move.uci()

            # Pre-move engine analysis (reused from previous step)
            infos_before = current_infos
            info_before = infos_before[0] if infos_before else {}

            pv_moves = info_before.get("pv", [])
            if pv_moves:
                best_move = pv_moves[0]
                best_move_san = board.san(best_move)
                best_move_uci = best_move.uci()
            else:
                best_move = None
                best_move_san = None
                best_move_uci = None

            evaluation_before = score_to_cp(
                info_before.get("score"),
                player
            )

            # Principal variation
            pv = extract_pv(board, pv_moves)

            # MultiPV candidates
            candidates = extract_candidates_from_infos(
                board,
                infos_before,
                player
            )

            # --------------------------------
            # Play actual move
            # --------------------------------

            board.push(move)

            fen_after = board.fen()

            # Search new position once with MultiPV
            next_infos = engine.analyse(
                board,
                chess.engine.Limit(depth=depth),
                multipv=multipv
            )

            info_after = next_infos[0] if next_infos else {}

            # Evaluate position after move from the mover's POV
            evaluation_after = score_to_cp(
                info_after.get("score"),
                player
            )

            # --------------------------------
            # Centipawn loss (Raw vs Decision)
            # --------------------------------

            # Raw evaluation delta
            raw_cpl = calculate_centipawn_loss(
                evaluation_before,
                evaluation_after
            )

            # Decision loss normalization:
            # If the player made the engine's best move (or matched top candidate evaluation),
            # or if the move was forced (only 1 legal move), decision loss is 0.
            is_best = False
            if best_move_san and played_move_san == best_move_san:
                is_best = True
            elif best_move_uci and played_move_uci == best_move_uci:
                is_best = True

            if is_forced or is_best:
                cpl = 0
            else:
                cpl = raw_cpl

            classification = classify_move(cpl)

            # --------------------------------
            # Clock
            # --------------------------------

            next_node = node.next() if node is not None else None
            clock_seconds = get_clock_seconds(next_node)

            # --------------------------------
            # Store
            # --------------------------------

            analysis = MoveAnalysis(

                move_number=move_number,

                color=color,

                played_move=played_move_san,

                best_move=best_move_san if best_move_san else "",

                evaluation_before=evaluation_before,

                evaluation_after=evaluation_after,

                centipawn_loss=cpl,

                fen_before=fen_before,

                fen_after=fen_after,

                principal_variation=pv,

                candidates=candidates,

                clock_seconds=clock_seconds,

                position_features=pos_features,

                played_move_uci=played_move_uci,

                best_move_uci=best_move_uci,

                raw_centipawn_loss=raw_cpl,

                classification=classification,

                is_forced=is_forced
            )

            analyses.append(analysis)

            # Advance current position search and node for next move
            current_infos = next_infos
            node = next_node

        return game, analyses

    finally:
        engine.quit()


def get_candidate_moves(engine, board, depth: int = DEPTH, multipv: int = MULTI_PV):

    infos = engine.analyse(
        board,
        chess.engine.Limit(depth=depth),
        multipv=multipv
    )

    return extract_candidates_from_infos(board, infos, board.turn)


def format_evaluation(eval_val: Optional[int]) -> str:
    """Format evaluation centipawns or mate for human readability."""
    if eval_val is None:
        return "N/A"
    if abs(eval_val) >= MATE_THRESHOLD:
        moves = 100000 - eval_val if eval_val > 0 else -100000 - eval_val
        return f"+M{moves}" if moves > 0 else f"-M{abs(moves)}"
    return f"{eval_val / 100:.2f}"


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(description="Personal Chess Coach Analysis Pipeline")
    parser.add_argument("--pgn", default=PGN_PATH, help=f"Path to PGN game file (default: {PGN_PATH})")
    parser.add_argument("--stockfish", default=STOCKFISH_PATH, help=f"Path to Stockfish binary (default: {STOCKFISH_PATH})")
    parser.add_argument("--depth", type=int, default=DEPTH, help=f"Search depth (default: {DEPTH})")
    parser.add_argument("--export", "-o", help="Optional output path to export structured analysis JSON")

    args = parser.parse_args()

    game, analyses = analyze_game(
        pgn_path=args.pgn,
        stockfish_path=args.stockfish,
        depth=args.depth
    )

    # -------------------------
    # Move-by-move analysis
    # -------------------------

    for analysis in analyses:

        cpl_str = f"{analysis.centipawn_loss:4d}" if analysis.centipawn_loss is not None else " N/A"
        clk_str = f"{analysis.clock_seconds}" if analysis.clock_seconds is not None else "None"
        best_str = analysis.best_move if analysis.best_move else "None"

        print(
            f"{analysis.move_number:2d}. "
            f"{analysis.color:5s} | "
            f"{analysis.played_move:6s} | "
            f"Best: {best_str:6s} | "
            f"CPL: {cpl_str} | "
            f"Clock: {clk_str}"
        )

    # -------------------------
    # Critical positions
    # -------------------------

    critical = find_critical_positions(analyses)
    episodes = group_critical_positions(analyses)

    print("\n" + "=" * 80)
    print(f"CRITICAL POSITIONS ({len(critical)} detected across {len(analyses)} half-moves)")
    print("=" * 80)

    for analysis in critical:

        classification = analysis.classification or classify_move(
            analysis.centipawn_loss
        )

        color_dot = "." if analysis.color == "White" else "..."

        print(
            f"\nMove {analysis.move_number}{color_dot} ({analysis.color})"
        )

        print(
            f"Played: {analysis.played_move}"
        )

        print(
            f"Best: {analysis.best_move}"
        )

        print(
            f"CPL: {analysis.centipawn_loss}"
        )

        print(
            f"Classification: {classification}"
        )

        print(
            f"Evaluation: "
            f"{format_evaluation(analysis.evaluation_before)}"
            f" → "
            f"{format_evaluation(analysis.evaluation_after)}"
        )

        print(
            f"PV: "
            f"{' '.join(analysis.principal_variation)}"
        )

        print(
            f"Position Features:"
            f"{analysis.position_features}"
        )

    # -------------------------
    # Critical episodes
    # -------------------------

    print("\n" + "=" * 80)
    print(f"CRITICAL EPISODES ({len(episodes)} detected)")
    print("=" * 80)

    for i, episode in enumerate(
        episodes,
        start=1
    ):

        print(f"\nEpisode {i} ({len(episode)} moves):")

        for analysis in episode:

            print(
                f"  Move {analysis.move_number}"
                f"{'.' if analysis.color == 'White' else '...'} "
                f"{analysis.played_move} "
                f"({analysis.centipawn_loss} CPL, {analysis.classification or classify_move(analysis.centipawn_loss)})"
            )

    print("\n" + "=" * 80)
    print(
        f"ANALYSIS SUMMARY: {len(analyses)} half-moves analyzed | "
        f"{len(critical)} critical positions | "
        f"{len(episodes)} critical episodes"
    )
    print("=" * 80)

    # -------------------------
    # Optional JSON Export
    # -------------------------

    if args.export:
        exported_path = export_to_json_file(
            game,
            analyses,
            args.export,
            critical=critical,
            episodes=episodes
        )
        print(f"\nStructured analysis successfully exported to: {exported_path}")