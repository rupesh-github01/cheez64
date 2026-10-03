import chess
import chess.pgn
import chess.engine

from models import MoveAnalysis
from pgn_utils import extract_clock

from critical import (
    classify_move,
    find_critical_positions
)

from models import (
    MoveAnalysis,
    CandidateMove
)

from position_features import (
    get_position_features
)

from episodes import (
    group_critical_positions
)


PGN_PATH = "data/games/test_game.pgn"
STOCKFISH_PATH = "/opt/homebrew/bin/stockfish"

DEPTH = 16
MULTI_PV = 3




def evaluate_position(engine, board):

    return engine.analyse(
        board,
        chess.engine.Limit(depth=DEPTH)
    )


def score_to_cp(score, turn):

    return score.pov(turn).score(
        mate_score=100000
    )


def get_clock_seconds(node):

    if not node.comment:
        return None

    return extract_clock(node.comment)


def analyze_game():

    # --------------------------------
    # Load PGN
    # --------------------------------

    with open(PGN_PATH, "r") as pgn_file:
        game = chess.pgn.read_game(pgn_file)

    engine = chess.engine.SimpleEngine.popen_uci(
        STOCKFISH_PATH
    )

    board = game.board()

    analyses = []

    node = game

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

        # Actual move in SAN
        played_move = board.san(move)

        # Engine analysis before move
        infos_before = engine.analyse(
            board,
            chess.engine.Limit(depth=DEPTH),
            multipv=MULTI_PV
        )

        info_before = infos_before[0]

        best_move = info_before["pv"][0]

        best_move_san = board.san(
            best_move
        )

        evaluation_before = score_to_cp(
            info_before["score"],
            player
        )

        # Principal variation
        pv = []

        pv_board = board.copy()

        for pv_move in info_before["pv"][:8]:

            try:
                pv.append(
                    pv_board.san(pv_move)
                )

                pv_board.push(pv_move)

            except ValueError:
                break

        # --------------------------------
        # Play actual move
        # --------------------------------

        board.push(move)

        fen_after = board.fen()

        # Engine analysis after move
        info_after = evaluate_position(
            engine,
            board
        )

        evaluation_after = score_to_cp(
            info_after["score"],
            player
        )

        # --------------------------------
        # Centipawn loss
        # --------------------------------

        if (
            evaluation_before is not None
            and evaluation_after is not None
        ):
            centipawn_loss = max(
                0,
                evaluation_before - evaluation_after
            )
        else:
            centipawn_loss = None

        # --------------------------------
        # Clock
        # --------------------------------

        clock_seconds = get_clock_seconds(
            node.next()
        )

        # --------------------------------
        # Store
        # --------------------------------

        analysis = MoveAnalysis(

            move_number=move_number,

            color=color,

            played_move=played_move,

            best_move=best_move_san,

            evaluation_before=evaluation_before,

            evaluation_after=evaluation_after,

            centipawn_loss=centipawn_loss,

            fen_before=fen_before,

            fen_after=fen_after,

            principal_variation=pv,

            clock_seconds=clock_seconds,

            position_features=pos_features
        )

        analyses.append(analysis)

        node = node.next()

    engine.quit()

    return game, analyses

def get_candidate_moves(engine, board):

    infos = engine.analyse(
        board,
        chess.engine.Limit(depth=DEPTH),
        multipv=MULTI_PV
    )

    candidates = []

    for info in infos:

        move = info["pv"][0]

        move_san = board.san(move)

        evaluation = score_to_cp(
            info["score"],
            board.turn
        )

        pv_board = board.copy()

        pv = []

        for pv_move in info["pv"][:8]:

            try:

                pv.append(
                    pv_board.san(pv_move)
                )

                pv_board.push(pv_move)

            except ValueError:
                break

        candidates.append(
            CandidateMove(
                move=move_san,
                evaluation=evaluation,
                principal_variation=pv
            )
        )

    return candidates

if __name__ == "__main__":

    game, analyses = analyze_game()

    # -------------------------
    # Move-by-move analysis
    # -------------------------

    for analysis in analyses:

        print(
            f"{analysis.move_number:2d}. "
            f"{analysis.color:5s} | "
            f"{analysis.played_move:6s} | "
            f"Best: {analysis.best_move:6s} | "
            f"CPL: {analysis.centipawn_loss:4d} | "
            f"Clock: {analysis.clock_seconds}"
        )

    # -------------------------
    # Critical positions
    # -------------------------

    print("\n" + "=" * 80)
    print("CRITICAL POSITIONS")
    print("=" * 80)

    critical = find_critical_positions(
        analyses
    )

    for analysis in critical:

        classification = classify_move(
            analysis.centipawn_loss
        )

        print(
            f"\nMove {analysis.move_number}"
        )

        print(
            f"Player: {analysis.color}"
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
            f"{analysis.evaluation_before / 100:.2f}"
            f" → "
            f"{analysis.evaluation_after / 100:.2f}"
        )

        print(
            f"PV: "
            f"{' '.join(analysis.principal_variation)}"
        )

    # -------------------------
    # Critical episodes
    # -------------------------

    episodes = group_critical_positions(
        analyses
    )

    print("\n" + "=" * 80)
    print("CRITICAL EPISODES")
    print("=" * 80)

    for i, episode in enumerate(
        episodes,
        start=1
    ):

        print(f"\nEpisode {i}")

        for analysis in episode:

            print(
                f"Move {analysis.move_number}"
                f"{'.' if analysis.color == 'White' else '...'} "
                f"{analysis.played_move} "
                f"({analysis.centipawn_loss} CPL)"
            )