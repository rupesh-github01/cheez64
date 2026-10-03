import chess


def get_material(board):

    values = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
        chess.KING: 0,
    }

    white = 0
    black = 0

    for piece_type, value in values.items():

        white += len(
            board.pieces(
                piece_type,
                chess.WHITE
            )
        ) * value

        black += len(
            board.pieces(
                piece_type,
                chess.BLACK
            )
        ) * value

    return {
        "white": white,
        "black": black,
        "difference": white - black
    }


def get_piece_counts(board):

    result = {
        "white": {},
        "black": {}
    }

    for color, name in [
        (chess.WHITE, "white"),
        (chess.BLACK, "black")
    ]:

        for piece_type in range(
            chess.PAWN,
            chess.KING + 1
        ):

            result[name][
                chess.piece_name(piece_type)
            ] = len(
                board.pieces(
                    piece_type,
                    color
                )
            )

    return result


def get_legal_moves_count(board):

    return {
        "white": (
            len(list(
                board.legal_moves
            ))
            if board.turn == chess.WHITE
            else None
        ),

        "black": (
            len(list(
                board.legal_moves
            ))
            if board.turn == chess.BLACK
            else None
        )
    }


def get_position_features(board):

    return {

        "material":
            get_material(board),

        "piece_counts":
            get_piece_counts(board),

        "side_to_move":
            "white"
            if board.turn == chess.WHITE
            else "black",

        "is_check":
            board.is_check(),

        "is_checkmate":
            board.is_checkmate(),

        "is_stalemate":
            board.is_stalemate(),

        "legal_moves":
            len(list(board.legal_moves)),

        "fullmove_number":
            board.fullmove_number
    }