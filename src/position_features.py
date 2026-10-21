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

# Segment 19: additive descriptors. Legacy get_position_features is unchanged.
def extract_understanding_features(board, budget, development=None):
    """Counts and geometric sets, with heuristic interpretation kept explicit."""
    def names(squares):
        return [chess.square_name(s) for s in sorted(squares)]

    result = {'material': {'piece_counts': get_piece_counts(board),
                          'conventional_points': {**get_material(board), 'provenance': 'HEURISTIC', 'method': '1_3_3_5_9_v1'}},
              'terminal': {'check': board.is_check(), 'checkmate': board.is_checkmate(),
                           'stalemate': board.is_stalemate(),
                           'automatic_outcome': str(board.outcome(claim_draw=False).termination.name) if board.outcome(claim_draw=False) else None},
              'colors': {}}
    for color, label in ((chess.WHITE, 'white'), (chess.BLACK, 'black')):
        budget.check()
        own = set(board.pieces(chess.PAWN, color)); enemy = set(board.pieces(chess.PAWN, not color))
        pawn_attacks = set().union(*(set(board.attacks(s)) for s in own)) if own else set()
        opp_pawn_attacks = set().union(*(set(board.attacks(s)) for s in enemy)) if enemy else set()
        controlled = set(); activity = []
        for sq, piece in sorted(board.piece_map().items()):
            if piece.color != color:
                continue
            budget.check()
            attacks = set(board.attacks(sq)); controlled |= attacks
            activity.append({'square': chess.square_name(sq), 'piece': piece.symbol(),
                             'geometric_attacks': names(attacks),
                             'own_occupied_attack_squares': names(s for s in attacks if board.color_at(s) == color),
                             'legal_mobility': None})
        files = {f: [s for s in own if chess.square_file(s) == f] for f in range(8)}
        open_files = []; semi_open = []
        for f in range(8):
            opposing = [s for s in enemy if chess.square_file(s) == f]
            if not files[f] and not opposing:
                open_files.append(chess.FILE_NAMES[f])
            elif not files[f] and opposing:
                semi_open.append(chess.FILE_NAMES[f])
        isolated = [s for s in own if not any(abs(chess.square_file(s)-chess.square_file(t)) == 1 for t in own)]
        passed = [s for s in own if not any(abs(chess.square_file(s)-chess.square_file(t)) <= 1 and
                  ((chess.square_rank(t) > chess.square_rank(s)) if color else (chess.square_rank(t) < chess.square_rank(s))) for t in enemy)]
        connected = [s for s in own if any(abs(chess.square_file(s)-chess.square_file(t)) == 1 and
                     abs(chess.square_rank(s)-chess.square_rank(t)) <= 1 for t in own)]
        half = set(s for s in chess.SQUARES if (chess.square_rank(s) >= 4 if color else chess.square_rank(s) <= 3))
        king = board.king(color)
        ring = set(board.attacks(king)) if king is not None else set()
        challenged = set().union(*(set(board.attacks(s)) for s, p in board.piece_map().items() if p.color != color))
        candidates = [s for s, p in board.piece_map().items() if p.color == color and p.piece_type in (chess.KNIGHT, chess.BISHOP)
                      and s in half and s in pawn_attacks and s not in opp_pawn_attacks]
        result['colors'][label] = {
            'king_safety': {'king_square': chess.square_name(king) if king is not None else None,
                            'ring': names(ring), 'geometrically_attacked_ring': names(ring & challenged),
                            'nearby_friendly_pawns': names(s for s in own if chess.square_distance(s, king) <= 2),
                            'adjacent_exposed_files': [f for f in open_files+semi_open if abs(chess.FILE_NAMES.index(f)-chess.square_file(king)) <= 1],
                            'castling_rights': bool(board.has_castling_rights(color)),
                            'interpretation': 'HEURISTIC: ring pressure and shield geometry do not establish danger or safety.'},
            'pawn_structure': {'files': {chess.FILE_NAMES[f]: len(v) for f, v in files.items()},
                               'doubled_files': [chess.FILE_NAMES[f] for f, v in files.items() if len(v) >= 2],
                               'isolated': names(isolated), 'connected': names(connected), 'passed_by_pawn_geometry': names(passed),
                               'blocked_passers': names(s for s in passed if 0 <= s+(8 if color else -8) < 64 and board.piece_at(s+(8 if color else -8))),
                               'pawn_attacks': names(pawn_attacks), 'limitations': ['Structure alone is not a strength or weakness; promotion is not assured.']},
            'piece_activity': {'items': activity, 'legal_mobility_status': 'NOT_ASSESSED',
                               'limitations': ['Geometric attacks include pinned pieces. Legal mobility only for actual side to move.']},
            'development': {'status': 'KNOWN_STANDARD_ROOT' if development is not None else 'UNKNOWN_HISTORY',
                            'original_minors': {k: {'square': chess.square_name(v['square']) if v['square'] is not None else None,
                                                     'ever_moved': v['ever_moved']} for k, v in (development or {}).items() if (chess.square_rank(chess.parse_square(k)) == 0) == color},
                            'home_square_occupancy': names(s for s in (chess.B1, chess.C1, chess.F1, chess.G1) if board.color_at(s) == color) if color else names(s for s in (chess.B8, chess.C8, chess.F8, chess.G8) if board.color_at(s) == color),
                            'provenance': 'HEURISTIC', 'limitations': ['Ever moved is a history rule, not move quality; home occupancy does not identify original pieces.']},
            'files': {'open': open_files, 'semi_open': semi_open,
                      'rooks_queens_on_open_files': names(s for s, p in board.piece_map().items() if p.color == color and p.piece_type in (chess.ROOK, chess.QUEEN) and chess.FILE_NAMES[chess.square_file(s)] in open_files)},
            'weak_squares': {'outpost_candidates': names(candidates), 'provenance': 'HEURISTIC',
                             'method': 'minor_on_enemy_half_supported_by_pawn_not_currently_pawn_attacked_v1',
                             'limitations': ['Future pawn challenges, exchanges and sacrifices are not searched.']},
            'space': {'enemy_half_control': names(controlled & half), 'count': len(controlled & half), 'denominator': 32,
                      'provenance': 'HEURISTIC', 'method': 'geometric_enemy_half_control_v1'},
            'bishop_pair': bool(board.pieces_mask(chess.BISHOP, color) & chess.BB_DARK_SQUARES) and bool(board.pieces_mask(chess.BISHOP, color) & chess.BB_LIGHT_SQUARES),
        }
    return result


def compare_understanding_features(before, after):
    """Expose changed named descriptors without assigning a quality score."""
    if before is None:
        return []
    changes = []
    for color in ('white', 'black'):
        for name, value in after['colors'][color].items():
            if before['colors'][color][name] != value:
                changes.append({'color': color, 'feature': name, 'before': before['colors'][color][name], 'after': value,
                                'attribution': 'Observed after the move; strategic impact is not established.'})
    if before['material'] != after['material']:
        changes.insert(0, {'feature': 'material', 'before': before['material'], 'after': after['material']})
    # Large feature maps live in features; changes reference those maps, not duplicate them.
    return [{'color': x.get('color'), 'feature': x['feature'], 'before_ref': 'features.before', 'after_ref': 'features.after',
             'attribution': x.get('attribution', 'Piece counts changed; compensation is not inferred.')} for x in changes]
