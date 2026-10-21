"""Bounded pure practice composition; no engine, provider, queue or cloud calls."""
import hashlib
import json
import time
from pathlib import Path
import chess
from .fast_profile import selector_sql, profile_reader
from .positions import reconstruct_understanding_position
from .understanding_models import UnderstandingBudget, BudgetExceeded
from .tactical_analysis import understanding_motifs
from .practice_models import PuzzleDefinition, PuzzleQuestion


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def account_key(account):
    source, username = account['source'], account['username']
    selector_sql(source, username)  # same validation/matching policy as profiles
    if source in ('chess.com', 'lichess'):
        username = username.translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))
    return {'source': source, 'username': username}


def source_game(library, account, gid):
    cte, params, _ = selector_sql(**account)
    with profile_reader(library) as conn:
        conn.execute('PRAGMA busy_timeout=250')
        deadline = time.monotonic() + 1
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        row = conn.execute(cte + '''SELECT game_id,fingerprint,w,length(CAST(pgn AS BLOB)) AS bytes
            FROM selected WHERE game_id=?''', params+[gid]).fetchone()
        if row is None:
            raise ValueError('SOURCE_MISSING_OR_ACCOUNT_MISMATCH')
        if row['bytes'] > 2*1024**2:
            raise ValueError('PGN_TOO_LARGE')
        pgn = conn.execute('SELECT pgn FROM games WHERE game_id=?', (gid,)).fetchone()[0]
    return {'game_id': gid, 'fingerprint': row['fingerprint'], 'pgn': pgn,
            'pgn_hash': hashlib.sha256(pgn.encode()).hexdigest(), 'color': 'white' if row['w'] else 'black'}


def legacy_seeds(library, gid):
    """Legacy labels only order roots; their evaluations never qualify answers."""
    with profile_reader(library) as conn:
        conn.execute('PRAGMA busy_timeout=250')
        row = conn.execute("SELECT CASE WHEN length(CAST(analysis_data AS BLOB))<=2097152 THEN analysis_data END FROM analyses WHERE game_id=?", (gid,)).fetchone()
    if row is None:
        return [], 'NO_STORED_ANALYSIS'
    if row[0] is None:
        return [], 'OVERSIZED_SEEDS_SKIPPED'
    try:
        moves = json.loads(row[0]).get('moves', [])
        if not isinstance(moves, list):
            raise ValueError()
        seeds = []
        for item in moves[:200]:
            if not isinstance(item, dict):
                continue
            ply, cpl = item.get('ply'), item.get('cpl', 0)
            if type(ply) is int and 1 <= ply <= 10000 and type(cpl) in (int, float) and cpl >= 75:
                seeds.append((-cpl, ply-1))
        return [ply for _, ply in sorted(seeds)], 'PARTIAL_200_ENTRIES' if len(moves) > 200 else 'LEGACY_SEEDS_ONLY_NOT_PROOF'
    except (ValueError, TypeError, AttributeError):
        return [], 'MALFORMED_SEEDS_SKIPPED'


def root_board(context):
    deadline = time.monotonic()+1
    if len(context['moves']) > 10000:
        raise ValueError('HISTORY_LIMIT')
    board = chess.Board(context['root_fen'])
    for uci in context['moves']:
        if time.monotonic() > deadline:
            raise ValueError('VALIDATION_BUDGET')
        board.push_uci(uci)
    if board.fen(en_passant='legal') != context['fen']:
        raise ValueError('CONTEXT_MISMATCH')
    return board


def compose(library, account, pgn, ply, budget):
    selected = reconstruct_understanding_position(pgn, ply, budget)
    board = selected['after']
    if board.outcome(claim_draw=False):
        return None, {'reason': 'TERMINAL_ROOT'}
    findings, coverage, _, omitted = understanding_motifs(board, budget, 'after')
    mates = [f for f in findings if f['motif'] == 'mate_in_one']
    witnesses = mates + [f for f in findings if f['motif'] == 'tactical_opportunity']
    if not witnesses:
        return None, {'reason': 'NO_USABLE_WITNESS', 'detectors': coverage, 'omitted': omitted}
    finding = witnesses[0]
    complete = selected['root_history_complete'] and selected['total_plies'] <= 1000
    # Preserve the complete prefix even for guided long games; never truncate it.
    moves = [m.uci() for m in board.move_stack]
    objective = 'CHECKMATE_IN_ONE' if mates and complete else 'EXAMINE_WITNESS'
    limitations = ['No uniqueness or best-move claim. Other legal moves may be useful.',
                   'Discovery examines a bounded sample; absence is not evidence of no tactics.']
    if not complete:
        limitations.append('Prior history is unknown or the game exceeds the 1,000-ply trusted-context cap; guided only.')
    if board.can_claim_draw():
        limitations.append('A draw is claimable. The objective assumes play continues; it does not prove a forced game win.')
    context = {'root_fen': selected['root_fen'], 'fen': board.fen(en_passant='legal'), 'moves': moves,
               'root_history_complete': selected['root_history_complete'], 'trusted_history': complete,
               'draw_policy': 'fide_claims_and_automatic_v1', 'variant': 'standard'}
    value = PuzzleDefinition(account=account_key(account), library=str(Path(library).resolve()), context=context,
        objective=objective, tier='VERIFIED_PUZZLE' if objective == 'CHECKMATE_IN_ONE' else 'GUIDED_EXERCISE',
        witness=finding['facts']['uci'], theme=finding['motif'], finding=finding,
        coverage={'detectors': coverage, 'omitted_findings': omitted, 'alternatives': 'NOT_EXHAUSTIVELY_GRADED',
                  'engine_evidence': 'NOT_REQUESTED'}, limitations=limitations)
    return value.model_dump(), {'reason': 'USABLE', 'detectors': coverage}


def question(pid, definition, source, freshness):
    ctx = definition['context']
    return PuzzleQuestion(id=pid, tier=definition['tier'], objective=definition['objective'],
        prompt='Find a move that checkmates in one.' if definition['objective'] == 'CHECKMATE_IN_ONE' else
               'Explore a legal tactical idea. Use a hint for a theme; alternatives are not judged as bad moves.',
        fen=ctx['fen'], root_fen=ctx['root_fen'], moves=ctx['moves'],
        learner_color='white' if chess.Board(ctx['fen']).turn else 'black',
        history_complete=ctx['root_history_complete'], source={**source, 'freshness': freshness},
        coverage={'discovery_bounded': True, 'uniqueness': 'NOT_CLAIMED', 'engine_evidence': 'NOT_REQUESTED'},
        limitations=definition['limitations']).model_dump()


def grade(definition, uci):
    board = root_board(definition['context'])
    if board.outcome(claim_draw=False):
        raise ValueError('TERMINAL_ROOT')
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        return {'code': 'ILLEGAL_MOVE', 'message': 'That move is not legal here.'}
    if not move or move not in board.legal_moves:
        return {'code': 'ILLEGAL_MOVE', 'message': 'That move is not legal here.'}
    san = board.san(move)
    board.push(move)
    if definition['objective'] == 'CHECKMATE_IN_ONE':
        success = board.is_checkmate()
        return {'code': 'OBJECTIVE_MET' if success else 'OBJECTIVE_NOT_MET',
                'message': 'Checkmate: objective met.' if success else 'Legal move, but it does not give checkmate in one.',
                'submitted_san': san, 'fen': board.fen()}
    match = uci == definition['witness']
    return {'code': 'DEMONSTRATED_TARGET' if match else 'LEGAL_ALTERNATIVE_UNASSESSED',
            'message': 'You demonstrated the suggested legal idea. Optimality is unverified.' if match else
                       'Legal alternative. Its quality has not been assessed; it is not classified as wrong.',
            'submitted_san': san, 'fen': board.fen()}


def hint(definition, level):
    if level == 1:
        return 'Examine checks, captures and threats. Consider the opponent’s legal responses.'
    piece = root_board(definition['context']).piece_at(chess.Move.from_uci(definition['witness']).from_square)
    return f'Look for an idea with your {chess.piece_name(piece.piece_type)}. This is a clue, not a best-move claim.'


def reveal(definition):
    board = root_board(definition['context'])
    move = chess.Move.from_uci(definition['witness'])
    return {'uci': move.uci(), 'san': board.san(move), 'theme': definition['theme'],
            'provenance': 'BOARD_FACT' if definition['tier'] == 'VERIFIED_PUZZLE' else 'HEURISTIC',
            'explanation': 'This legal move gives checkmate.' if definition['tier'] == 'VERIFIED_PUZZLE' else
                           'This is one legal witness for the suggested tactical activity; no forced gain or optimality is established.',
            'finding': definition['finding'], 'limitations': definition['limitations']}
