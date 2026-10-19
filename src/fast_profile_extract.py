"""Pure bounded replay. No engine/provider/queue imports or persistent outputs."""
from collections import Counter, defaultdict
import io
import itertools
import math
import re

import chess
import chess.pgn

from .positions import MAX_PGN_BYTES, MAX_PLIES, UnsupportedVariant, canonical_position

ANNOTATION = re.compile(r'\[%(clk|emt)\s+([^\]]*)\]')
ALLOWED_VARIANTS = {'standard', 'chess', 'classical', 'normal', 'from position'}


def annotation_seconds(text):
    if len(text) > 64: return None
    parts = text.strip().split(':')
    if len(parts) != 3 or not all(re.fullmatch(r'\d+', x) for x in parts[:2]) or not re.fullmatch(r'\d+(?:\.\d+)?', parts[2]):
        return None
    hours, minutes, seconds = int(parts[0]), int(parts[1]), float(parts[2])
    value = hours*3600+minutes*60+seconds
    return value if minutes < 60 and seconds < 60 and math.isfinite(value) else None


def annotations(comment):
    found = defaultdict(list)
    bad = 0
    for kind, text in ANNOTATION.findall(comment):
        value = annotation_seconds(text)
        if value is None:
            bad += 1
        found[kind].append(value)
    values = {}
    for kind in ('clk', 'emt'):
        entries = found[kind]
        if len(entries) > 1:
            bad += 1
        values[kind] = entries[0] if len(entries) == 1 else None
    return values, bad


def time_control(raw):
    """Only exact finite nonnegative single-stage seconds[+increment]."""
    if not isinstance(raw, str) or not raw or len(raw) > 100 or not re.fullmatch(r'\d+(?:\+\d+(?:\.\d+)?)?', raw):
        return None
    parts = raw.split('+')
    base = int(parts[0]); increment = float(parts[1]) if len(parts) == 2 else 0.0
    if base <= 0 or not math.isfinite(increment):
        return None
    return base, increment


def material(board):
    return (chess.popcount(board.pawns) + 3*chess.popcount(board.knights | board.bishops)
            + 5*chess.popcount(board.rooks) + 9*chess.popcount(board.queens))


def side_material(board, color):
    mask = board.occupied_co[color]
    return (chess.popcount(board.pawns & mask) + 3*chess.popcount((board.knights | board.bishops) & mask)
            + 5*chess.popcount(board.rooks & mask) + 9*chess.popcount(board.queens & mask))


def extract_game(pgn, color, stored_tc, *, clock_convention='unverified', opening_db=None, stored_opening=None, opening_limit=None):
    """One analytics walk; the unchanged opening detector may do its own walk.

    Clock convention is explicit input. No platform convention is assumed just
    from a game source; explicit EMT remains usable with unverified clocks.
    """
    if pgn is None or len(pgn.encode()) > MAX_PGN_BYTES:
        raise ValueError('PGN exceeds byte limit')
    headers = chess.pgn.read_headers(io.StringIO(pgn))
    if headers and headers.get('Variant', 'standard').lower() not in ALLOWED_VARIANTS:
        raise UnsupportedVariant('Only standard chess supported')
    stream = io.StringIO(pgn)
    game = chess.pgn.read_game(stream)
    if game is None or game.errors or stream.read().strip():
        raise ValueError('Malformed or multiple-game PGN')
    board = game.board()
    start_key = canonical_position(board)
    standard_start = board.fen() == chess.STARTING_FEN
    player = color == 'white'
    original_minors = set(chess.scan_forward((board.knights | board.bishops) & board.occupied_co[player])) if standard_start else set()
    identities = {sq: sq for sq in chess.scan_forward(board.occupied)}
    original_queens = set(chess.scan_forward(board.queens))
    removed_queens = set()
    developed = set(); seen_pieces = Counter(); counts = Counter(); phases = Counter()
    captures = Counter(); imbalances = Counter(); clocks = Counter(); durations = []
    phase_durations = defaultdict(list); duration_by_source = defaultdict(list)
    clock_checkpoints = {}; queen_first = developed_at_ten = pawn_signature = None
    initial_castling_rights = bool(board.castling_rights & board.occupied_co[player])
    own_moves = plies = 0; castled = False; prefix = []
    prior = None
    tc = time_control(stored_tc)
    tc_conflict = bool(stored_tc != game.headers.get('TimeControl'))
    infer = clock_convention == 'post_increment' and tc is not None and not tc_conflict
    if standard_start and infer:
        prior = tc[0]
    for node in game.mainline():
        if plies >= MAX_PLIES:
            raise ValueError('PGN exceeds ply limit')
        move = node.move
        # python-chess has already validated normal moves while parsing; null
        # moves can occur in analysis PGNs and are not played-game observations.
        if not move:
            raise ValueError('Null mainline move')
        own = board.turn == player
        moving_piece = board.piece_type_at(move.from_square)
        capture = board.is_capture(move)
        is_castle = board.is_castling(move)
        identity = identities.pop(move.from_square, move.from_square)
        captured_square = move.to_square
        if board.is_en_passant(move):
            captured_square += -8 if board.turn == chess.WHITE else 8
        if capture:
            captured_identity = identities.pop(captured_square, captured_square)
            if captured_identity in original_queens:
                removed_queens.add(captured_identity)
        if own:
            own_moves += 1
            phase = 'opening' if standard_start and plies < 20 else ('endgame' if not (board.queens & board.occupied_co[0]) and not (board.queens & board.occupied_co[1]) or material(board) <= 26 else 'middlegame')
            phases[phase] += 1
            counts['moves'] += 1
            counts['pawn_moves'] += moving_piece == chess.PAWN
            counts['captures'] += capture
            counts['promotions'] += move.promotion is not None
            if move.promotion:
                counts['promotion_'+chess.piece_name(move.promotion)] += 1
            if capture:
                captured = board.piece_type_at(captured_square)
                captures[chess.piece_name(captured)] += 1
            if is_castle:
                castled = True
                counts['castling_short' if move.to_square > move.from_square else 'castling_long'] += 1
            if standard_start:
                if identity in original_minors:
                    developed.add(identity)
                if identity in original_queens and queen_first is None:
                    queen_first = own_moves
                if plies < 20 and moving_piece in (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN):
                    counts['repeated_piece_moves'] += seen_pieces[identity] > 0
                    seen_pieces[identity] += 1
                if own_moves == 10:
                    developed_at_ten = len(developed)
            imbalances[side_material(board, player)-side_material(board, not player)] += 1
            values, bad = annotations(node.comment)
            clocks['malformed_or_conflicting_annotations'] += bad
            clock, emt = values['clk'], values['emt']
            clocks['clock_moves'] += clock is not None
            clocks['emt_moves'] += emt is not None
            derived = None
            if infer and prior is not None and clock is not None:
                candidate = prior + tc[1] - clock
                if candidate >= 0:
                    derived = candidate
                else:
                    clocks['invalid_inferred_intervals'] += 1
            if emt is not None and derived is not None and abs(emt-derived) > 1:
                clocks['emt_clock_disagreements'] += 1
            duration = emt if emt is not None else derived
            source = 'reported_emt' if emt is not None else 'clock_difference'
            if duration is not None:
                durations.append((own_moves, duration))
                duration_by_source[source].append(duration)
                phase_durations[phase].append(duration)
            clock_checkpoints.pop('last_recorded', None)
            if clock is not None:
                clock_checkpoints['last_recorded'] = clock
                if own_moves in (10, 20):
                    clock_checkpoints[str(own_moves)] = clock
            prior = clock  # a missing annotation breaks adjacency, not just one value
        if plies < 8:
            prefix.append(board.uci(move))
        board.push(move)
        if own: counts['checks'] += board.is_check()
        identities[move.to_square] = identity
        if is_castle:
            # Orthodox rook identity changes square too; it must not be mistaken
            # for a new piece if it moves after castling.
            rank = 0 if not board.turn else 7
            rook_from = chess.square(7 if move.to_square > move.from_square else 0, rank)
            rook_to = chess.square(5 if move.to_square > move.from_square else 3, rank)
            identities[rook_to] = identities.pop(rook_from, rook_from)
        plies += 1
        if own and own_moves == 10:
            pawn_signature = f'{board.pawns & board.occupied_co[0]:016x}:{board.pawns & board.occupied_co[1]:016x}'
    if stored_opening:
        opening = dict(stored_opening)
        opening_source = 'stored_classification'
    elif opening_db is not None:
        # The classifier only consults trie prefixes and the first 25 EPDs.
        # A limit >= max reference trie depth and 25 preserves every label/
        # confidence decision. Keep using the original classifier unchanged.
        cutoff = next(itertools.islice(game.mainline(), opening_limit-1, opening_limit), None) if opening_limit else None
        remaining = cutoff.variations if cutoff else None
        try:
            if cutoff: cutoff.variations = []
            opening = opening_db.detect_opening(game).to_dict()
        finally:
            if cutoff: cutoff.variations = remaining
        opening_source = 'local_book_match'
    else:
        opening = {'opening_name': None, 'variation_name': None, 'confidence': 'unrecognized'}
        opening_source = 'unavailable'
    known = opening.get('confidence') not in (None, 'unrecognized', 'unknown') and opening.get('opening_name') not in (None, 'Unrecognized Opening')
    rating_warnings = 0
    for key in ('WhiteElo', 'BlackElo'):
        raw = game.headers.get(key)
        if raw and (not raw.isascii() or not raw.isdecimal() or int(raw) <= 0): rating_warnings += 1
    last_five = [d for n, d in durations if n > own_moves-5]
    return {'counts': counts, 'phases': phases, 'captures': captures, 'imbalances': imbalances,
            'plies': plies, 'own_moves': own_moves, 'castled': castled, 'standard_start': standard_start, 'initial_castling_rights': initial_castling_rights,
            'development_at_ten': developed_at_ten, 'queen_first_move': queen_first,
            'original_queens_removed': len(original_queens) == 2 and removed_queens == original_queens,
            'pawn_signature': pawn_signature, 'clocks': clocks, 'durations': duration_by_source,
            'phase_durations': phase_durations, 'last_five': last_five, 'checkpoints': clock_checkpoints,
            'tc_conflict': tc_conflict, 'raw_rating_warnings': rating_warnings, 'inference_supported': infer,
            'opening': {'name': opening.get('opening_name') if known else 'Unknown',
                        'variation': opening.get('variation_name') if known else None,
                        'reported_eco': game.headers.get('ECO'), 'confidence': opening.get('confidence') or 'unknown', 'eco': opening.get('eco') if known else None, 'source': opening_source},
            'move_order': (start_key, tuple(prefix), plies < 8)}
