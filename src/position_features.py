import chess


PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def get_material(board):

    white = 0
    black = 0

    for piece_type, value in PIECE_VALUES.items():

        white += (
            len(board.pieces(piece_type, chess.WHITE))
            * value
        )

        black += (
            len(board.pieces(piece_type, chess.BLACK))
            * value
        )

    return {
        "white": white,
        "black": black,
        "difference": white - black,
    }


def get_piece_counts(board):

    result = {
        "white": {},
        "black": {},
    }

    for color, name in [
        (chess.WHITE, "white"),
        (chess.BLACK, "black"),
    ]:

        for piece_type in PIECE_VALUES:

            result[name][
                chess.piece_name(piece_type)
            ] = len(
                board.pieces(
                    piece_type,
                    color
                )
            )

    return result


def get_checks(board):

    checks = []

    for move in board.legal_moves:

        if board.gives_check(move):

            checks.append(
                board.san(move)
            )

    return checks


def get_captures(board):

    captures = []

    for move in board.legal_moves:

        if board.is_capture(move):

            captures.append(
                board.san(move)
            )

    return captures


def get_position_features(board):

    return {

        "fen": board.fen(),

        "side_to_move":
            "White"
            if board.turn == chess.WHITE
            else "Black",

        "material":
            get_material(board),

        "piece_counts":
            get_piece_counts(board),

        "checks_available":
            get_checks(board),

        "captures_available":
            get_captures(board),

        "in_check":
            board.is_check(),

        "is_checkmate":
            board.is_checkmate(),

        "legal_moves":
            len(list(board.legal_moves)),
    }