from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
import chess

try:
    from models import MoveAnalysis, CandidateMove
    from position_features import PIECE_VALUES
except ImportError:
    from src.models import MoveAnalysis, CandidateMove
    from src.position_features import PIECE_VALUES


# Tactical consequence categories
CATEGORY_MATERIAL_LOSS = "material_loss"
CATEGORY_MISSED_CAPTURE = "missed_capture"
CATEGORY_HANGING_PIECE = "hanging_piece"
CATEGORY_MISSED_CHECK_OR_FORCING = "missed_check_or_forcing_move"
CATEGORY_UNCLASSIFIED = "unclassified"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"


@dataclass
class TacticalFinding:
    category: str
    confidence: str
    played_move: str
    better_move: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    limitations: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "confidence": self.confidence,
            "played_move": self.played_move,
            "better_move": self.better_move,
            "evidence": self.evidence,
            "limitations": self.limitations,
        }


def get_piece_value(piece_type: int) -> int:
    return PIECE_VALUES.get(piece_type, 0)


def calculate_board_material(board: chess.Board, color: chess.Color) -> int:
    """Calculate total material for a color using standard piece values."""
    total = 0
    for piece_type, val in PIECE_VALUES.items():
        total += len(board.pieces(piece_type, color)) * val
    return total


def simulate_sequence(
    board: chess.Board,
    moves: List[str],
    mover: chess.Color,
    max_plies: int = 6
) -> Tuple[int, List[Dict[str, Any]], List[str], chess.Board]:
    """
    Simulate a legal sequence of moves from board, tracking net material change for mover.

    Returns:
    - net_mover_material_change: (final_mover - initial_mover) - (final_opp - initial_opp)
    - captures: list of capture events
    - simulated_moves: list of SAN moves successfully pushed
    - final_board: board after simulation
    """
    sim_board = board.copy()
    opponent = not mover

    initial_mover_mat = calculate_board_material(sim_board, mover)
    initial_opp_mat = calculate_board_material(sim_board, opponent)

    captures = []
    simulated_moves = []

    for move_san in moves[:max_plies]:
        try:
            move_obj = sim_board.parse_san(move_san)
        except (ValueError, chess.IllegalMoveError):
            break

        if not sim_board.is_legal(move_obj):
            break

        # Record capture info if applicable
        if sim_board.is_capture(move_obj):
            captured_type = None
            if sim_board.is_en_passant(move_obj):
                captured_type = chess.PAWN
            else:
                target_piece = sim_board.piece_at(move_obj.to_square)
                if target_piece:
                    captured_type = target_piece.piece_type

            if captured_type:
                captures.append({
                    "move": move_san,
                    "captured_piece": chess.piece_name(captured_type),
                    "captured_value": get_piece_value(captured_type),
                    "by_color": "White" if sim_board.turn == chess.WHITE else "Black",
                })

        sim_board.push(move_obj)
        simulated_moves.append(move_san)

    final_mover_mat = calculate_board_material(sim_board, mover)
    final_opp_mat = calculate_board_material(sim_board, opponent)

    net_mover_material_change = (final_mover_mat - initial_mover_mat) - (final_opp_mat - initial_opp_mat)

    return net_mover_material_change, captures, simulated_moves, sim_board


def check_hanging_piece(
    board_after: chess.Board,
    mover: chess.Color,
    pv_after: List[str],
    board_before: Optional[chess.Board] = None,
    played_move: Optional[str] = None,
) -> Optional[TacticalFinding]:
    """
    Check if a mover's piece was left hanging (undefended or insufficiently defended)
    and captured or immediately capturable in the continuation.
    """
    opponent = not mover

    # First check concrete continuation in pv_after (the engine's principal response)
    if pv_after:
        first_san = pv_after[0]
        try:
            first_move = board_after.parse_san(first_san)
            if board_after.is_legal(first_move) and board_after.is_capture(first_move):
                # If first_move delivers checkmate, the game is over - not a hanging piece!
                board_next = board_after.copy()
                board_next.push(first_move)
                if board_next.is_checkmate():
                    return None

                captured_square = first_move.to_square
                captured_piece = board_after.piece_at(captured_square)

                # Did the opponent capture a piece belonging to the mover?
                if captured_piece and captured_piece.color == mover:
                    piece_val = get_piece_value(captured_piece.piece_type)

                    # Check if played_move was a capture on this same square (recapture / trade)
                    if board_before and played_move:
                        try:
                            played_obj = board_before.parse_san(played_move)
                            if board_before.is_capture(played_obj) and played_obj.to_square == captured_square:
                                if board_before.is_en_passant(played_obj):
                                    mover_cap_val = 1
                                else:
                                    mover_cap_target = board_before.piece_at(played_obj.to_square)
                                    mover_cap_val = get_piece_value(mover_cap_target.piece_type) if mover_cap_target else 0
                                # If mover captured equal or higher value, mover did NOT lose material on this piece!
                                if mover_cap_val >= piece_val:
                                    return None
                        except (ValueError, chess.IllegalMoveError):
                            pass

                    # Simulate continuation from board_after to verify net material loss for mover
                    cont_net, cont_caps, cont_sim, _ = simulate_sequence(
                        board_after,
                        pv_after,
                        mover,
                        max_plies=4
                    )
                    # If mover does NOT lose net material in continuation, piece was not lost
                    if cont_net >= 0:
                        return None

                    defenders = len(board_after.attackers(mover, captured_square))
                    attackers = len(board_after.attackers(opponent, captured_square))

                    attacker_piece = board_after.piece_at(first_move.from_square)
                    attacker_val = get_piece_value(attacker_piece.piece_type) if attacker_piece else 0

                    is_hanging = False
                    reason = ""

                    if defenders == 0:
                        is_hanging = True
                        reason = "completely_undefended"
                    elif piece_val > attacker_val:
                        # Attacker has lower value (e.g., minor piece takes Rook or Queen)
                        is_hanging = True
                        reason = "favorable_attacker_trade"
                    elif attackers > defenders:
                        is_hanging = True
                        reason = "insufficiently_defended"

                    if is_hanging:
                        evidence = {
                            "hanging_piece": chess.piece_name(captured_piece.piece_type),
                            "square": chess.square_name(captured_square),
                            "piece_value": piece_val,
                            "defenders_count": defenders,
                            "attackers_count": attackers,
                            "opponent_capture_move": first_san,
                            "reason": reason,
                        }
                        return TacticalFinding(
                            category=CATEGORY_HANGING_PIECE,
                            confidence=CONFIDENCE_HIGH,
                            played_move="",
                            better_move=None,
                            evidence=evidence,
                            limitations=None,
                        )
        except (ValueError, chess.IllegalMoveError):
            pass

    # If pv_after was empty or didn't capture, check if any major/minor piece is left
    # completely undefended and attacked by opponent on board_after
    for sq in chess.SQUARES:
        piece = board_after.piece_at(sq)
        if piece and piece.color == mover and piece.piece_type != chess.KING:
            piece_val = get_piece_value(piece.piece_type)
            if piece_val >= 3:  # Knight, Bishop, Rook, Queen
                attackers = list(board_after.attackers(opponent, sq))
                defenders = list(board_after.attackers(mover, sq))
                if len(attackers) > 0 and len(defenders) == 0:
                    # Verify that opponent has a legal capture on sq
                    legal_caps = [
                        m for m in board_after.legal_moves
                        if m.to_square == sq and m.from_square in attackers
                    ]
                    if not legal_caps:
                        continue

                    # Verify mover didn't just capture equal or greater piece on this square
                    if board_before and played_move:
                        try:
                            played_obj = board_before.parse_san(played_move)
                            if board_before.is_capture(played_obj) and played_obj.to_square == sq:
                                mover_cap_target = board_before.piece_at(played_obj.to_square)
                                mover_cap_val = get_piece_value(mover_cap_target.piece_type) if mover_cap_target else 0
                                if mover_cap_val >= piece_val:
                                    continue
                        except (ValueError, chess.IllegalMoveError):
                            pass

                    evidence = {
                        "hanging_piece": chess.piece_name(piece.piece_type),
                        "square": chess.square_name(sq),
                        "piece_value": piece_val,
                        "defenders_count": 0,
                        "attackers_count": len(attackers),
                        "reason": "completely_undefended",
                    }
                    return TacticalFinding(
                        category=CATEGORY_HANGING_PIECE,
                        confidence=CONFIDENCE_MEDIUM,
                        played_move="",
                        better_move=None,
                        evidence=evidence,
                        limitations="Continuation PV does not immediately capture; piece is undefended and attacked in post-move position",
                    )

    return None


def check_missed_capture(
    board_before: chess.Board,
    mover: chess.Color,
    better_move: str,
    candidate_pv: List[str]
) -> Optional[TacticalFinding]:
    """
    Check if the better candidate move wins material through a legal capture sequence
    that the played move failed to execute.
    """
    if not better_move:
        return None

    try:
        better_move_obj = board_before.parse_san(better_move)
    except (ValueError, chess.IllegalMoveError):
        return None

    if not board_before.is_legal(better_move_obj):
        return None

    if not board_before.is_capture(better_move_obj):
        return None

    # Identify captured piece
    captured_piece = None
    if board_before.is_en_passant(better_move_obj):
        captured_type = chess.PAWN
    else:
        target = board_before.piece_at(better_move_obj.to_square)
        captured_type = target.piece_type if target else chess.PAWN

    captured_val = get_piece_value(captured_type)

    # Simulate candidate PV to verify net material gain
    pv_moves = candidate_pv if candidate_pv else [better_move]
    net_gain, captures, sim_moves, _ = simulate_sequence(
        board_before,
        pv_moves,
        mover,
        max_plies=4
    )

    if net_gain > 0:
        evidence = {
            "missed_capture_move": better_move,
            "target_square": chess.square_name(better_move_obj.to_square),
            "captured_piece": chess.piece_name(captured_type),
            "captured_value": captured_val,
            "net_material_gain": net_gain,
            "candidate_sequence": sim_moves,
        }
        return TacticalFinding(
            category=CATEGORY_MISSED_CAPTURE,
            confidence=CONFIDENCE_HIGH,
            played_move="",
            better_move=better_move,
            evidence=evidence,
            limitations=None if len(sim_moves) >= 2 else "Candidate PV short",
        )

    return None


def check_missed_check_or_forcing_move(
    board_before: chess.Board,
    mover: chess.Color,
    played_move: str,
    better_move: str,
    candidate_pv: List[str],
    eval_diff: int
) -> Optional[TacticalFinding]:
    """
    Check if a candidate check or forcing sequence produces a demonstrably better result.
    """
    if not better_move or eval_diff < 75:
        return None

    try:
        played_obj = board_before.parse_san(played_move)
        played_gives_check = board_before.gives_check(played_obj)
    except (ValueError, chess.IllegalMoveError):
        played_gives_check = False

    try:
        better_obj = board_before.parse_san(better_move)
        if not board_before.is_legal(better_obj):
            return None
        better_gives_check = board_before.gives_check(better_obj)
    except (ValueError, chess.IllegalMoveError):
        better_gives_check = False

    # Mover missed an immediate check that is the top candidate
    if better_gives_check and not played_gives_check:
        pv_moves = candidate_pv if candidate_pv else [better_move]
        _, _, sim_moves, _ = simulate_sequence(board_before, pv_moves, mover, max_plies=4)
        evidence = {
            "forcing_candidate_move": better_move,
            "is_check": True,
            "played_move_is_check": False,
            "eval_difference": eval_diff,
            "forcing_sequence": sim_moves if sim_moves else pv_moves[:4],
        }
        return TacticalFinding(
            category=CATEGORY_MISSED_CHECK_OR_FORCING,
            confidence=CONFIDENCE_HIGH,
            played_move=played_move,
            better_move=better_move,
            evidence=evidence,
            limitations=None,
        )

    return None


def check_material_loss(
    board_before: chess.Board,
    board_after: chess.Board,
    mover: chess.Color,
    played_move: str,
    better_move: Optional[str],
    candidate_pv: List[str],
    pv_after: List[str]
) -> Optional[TacticalFinding]:
    """
    Compare material balance between candidate line and played continuation line.
    """
    # 1. Candidate line net change
    cand_moves = candidate_pv if candidate_pv else ([better_move] if better_move else [])
    cand_net, cand_captures, cand_sim, _ = simulate_sequence(
        board_before,
        cand_moves,
        mover,
        max_plies=6
    )

    # 2. Played line net change
    # Note: board_after has already had played_move applied.
    # We first calculate material change of played_move itself:
    opponent = not mover
    played_move_mover_mat = calculate_board_material(board_after, mover) - calculate_board_material(board_before, mover)
    played_move_opp_mat = calculate_board_material(board_after, opponent) - calculate_board_material(board_before, opponent)
    played_move_delta = played_move_mover_mat - played_move_opp_mat

    # Then continuation after played_move:
    cont_net, cont_captures, cont_sim, _ = simulate_sequence(
        board_after,
        pv_after,
        mover,
        max_plies=6
    )
    total_played_net = played_move_delta + cont_net

    material_difference = cand_net - total_played_net

    # Safeguard: in opening moves (move <= 2), a 1-pawn difference in a deep search line
    # is opening exploration/gambit nuance rather than a tactical blunder.
    if board_before.fullmove_number <= 2 and material_difference < 2:
        return None

    if material_difference >= 1 and (cont_captures or played_move_delta < 0):
        evidence = {
            "net_material_loss": material_difference,
            "played_line_net": total_played_net,
            "candidate_line_net": cand_net,
            "played_captures": cont_captures,
            "candidate_captures": cand_captures,
            "played_sequence": [played_move] + cont_sim,
            "better_sequence": cand_sim,
        }
        return TacticalFinding(
            category=CATEGORY_MATERIAL_LOSS,
            confidence=CONFIDENCE_HIGH if len(cont_sim) >= 2 else CONFIDENCE_MEDIUM,
            played_move=played_move,
            better_move=better_move,
            evidence=evidence,
            limitations=None if len(cont_sim) >= 2 else "Played continuation PV short",
        )

    return None


def detect_tactical_consequence(analysis: MoveAnalysis) -> Optional[TacticalFinding]:
    """
    Analyze a critical move to identify concrete, verifiable tactical consequences.
    Returns None for non-critical moves (CPL < 75).
    """
    # Only analyze critical moves
    if analysis.centipawn_loss is None or analysis.centipawn_loss < 75:
        return None

    # Safety checks
    if not analysis.fen_before or not analysis.fen_after:
        return TacticalFinding(
            category=CATEGORY_UNCLASSIFIED,
            confidence=CONFIDENCE_LOW,
            played_move=analysis.played_move,
            better_move=analysis.best_move if analysis.best_move else None,
            evidence={"reason": "Missing FEN position data"},
            limitations="Incomplete analysis record",
        )

    try:
        board_before = chess.Board(analysis.fen_before)
        board_after = chess.Board(analysis.fen_after)
    except ValueError:
        return TacticalFinding(
            category=CATEGORY_UNCLASSIFIED,
            confidence=CONFIDENCE_LOW,
            played_move=analysis.played_move,
            better_move=analysis.best_move if analysis.best_move else None,
            evidence={"reason": "Invalid FEN string"},
            limitations="Corrupted FEN",
        )

    mover = board_before.turn
    played_move = analysis.played_move
    better_move = analysis.best_move if analysis.best_move else None
    cpl = analysis.centipawn_loss

    # Extract candidate PV and continuation PV
    candidate_pv = []
    if analysis.candidates:
        candidate_pv = list(analysis.candidates[0].principal_variation)
    elif analysis.principal_variation:
        candidate_pv = list(analysis.principal_variation)

    pv_after = list(analysis.pv_after) if hasattr(analysis, "pv_after") else []

    # 1. Hanging piece check (piece left capturable/undefended)
    hanging_finding = check_hanging_piece(
        board_after,
        mover,
        pv_after,
        board_before=board_before,
        played_move=played_move,
    )
    if hanging_finding:
        hanging_finding.played_move = played_move
        hanging_finding.better_move = better_move
        return hanging_finding

    # 2. Missed capture check (stronger candidate wins material through legal capture)
    if better_move:
        missed_cap_finding = check_missed_capture(
            board_before,
            mover,
            better_move,
            candidate_pv
        )
        if missed_cap_finding:
            missed_cap_finding.played_move = played_move
            return missed_cap_finding

    # 3. Missed check or forcing move check
    if better_move:
        forcing_finding = check_missed_check_or_forcing_move(
            board_before,
            mover,
            played_move,
            better_move,
            candidate_pv,
            cpl
        )
        if forcing_finding:
            return forcing_finding

    # 4. Material loss check (piece lost in played line vs better candidate line)
    mat_loss_finding = check_material_loss(
        board_before,
        board_after,
        mover,
        played_move,
        better_move,
        candidate_pv,
        pv_after
    )
    if mat_loss_finding:
        return mat_loss_finding

    # 5. Unclassified fallback (no supported tactical motif can be established)
    limitations = "Positional or strategic factor beyond verified short-tactics heuristics"
    if not candidate_pv:
        limitations = "Missing candidate PV continuation"

    return TacticalFinding(
        category=CATEGORY_UNCLASSIFIED,
        confidence=CONFIDENCE_LOW,
        played_move=played_move,
        better_move=better_move,
        evidence={
            "centipawn_loss": cpl,
            "raw_centipawn_loss": analysis.raw_centipawn_loss,
            "reason": "No concrete material loss, hanging piece, missed capture, or forcing sequence verified in search PV",
        },
        limitations=limitations,
    )


# Segment 19: independent board predicates; no changes to legacy CPL consumers.
def understanding_motifs(board, budget, stage='after'):
    import hashlib
    import json
    try:
        from src.understanding_models import BudgetExceeded
    except ImportError:
        from understanding_models import BudgetExceeded

    findings = []; omitted = 0
    coverage = {name: {'status': 'NOT_ASSESSED'} for name in
                ('legal_moves', 'geometry', 'loose_pieces', 'overloaded_defenders', 'mate_in_one', 'opportunities')}
    legal = []
    def emit(motif, color, squares, facts, heuristic=False):
        nonlocal omitted
        squares = [chess.square_name(s) for s in squares]
        item = {'id': hashlib.sha256(json.dumps([stage, motif, color, squares, facts], sort_keys=True).encode()).hexdigest()[:16],
                'motif': motif, 'rule_version': 1, 'perspective': 'white' if color else 'black', 'stage': stage,
                'provenance': 'HEURISTIC' if heuristic else 'BOARD_FACT',
                'predicate_status': 'CANDIDATE' if heuristic else 'ESTABLISHED', 'squares': squares, 'facts': facts,
                'limitations': ['Geometry does not prove forced gain or objective advantage.']}
        if len(findings) < 128:
            findings.append(item)
        else:
            omitted += 1

    try:
        budget.check()
        outcome = board.outcome(claim_draw=False)
        if outcome is not None:
            if board.is_checkmate():
                emit('checkmate', not board.turn, [board.king(board.turn)], {'terminal': outcome.termination.name})
            else:
                emit('terminal_position', board.turn, [], {'termination': outcome.termination.name, 'winner': outcome.winner})
            return findings, {name: {'status': 'NOT_APPLICABLE', 'reason': 'TERMINAL_POSITION'} for name in coverage}, legal, omitted
        for move in board.legal_moves:
            budget.check()
            if len(legal) >= 256:
                break
            legal.append(move)
        legal_complete = len(legal) < 256
        coverage['legal_moves'] = {'status': 'COMPLETE' if legal_complete else 'PARTIAL_BUDGET', 'visited': len(legal)}
        captures = {}
        for m in legal:
            if board.is_capture(m):
                victim = m.to_square + (-8 if board.turn else 8) if board.is_en_passant(m) else m.to_square
                captures.setdefault(victim, []).append(m.uci())
        piece_map = board.piece_map()
        for sq, p in sorted(piece_map.items()):
            budget.check()
            targets = sorted(t for t in board.attacks(sq) if board.color_at(t) == (not p.color))
            important = [t for t in targets if board.piece_type_at(t) != chess.PAWN]
            if len(important) >= 2:
                emit('fork', p.color, [sq]+important, {'relation': 'geometric_attack', 'attacker': p.symbol(),
                      'targets': [chess.square_name(t) for t in important], 'attacker_pinned': board.is_pinned(p.color, sq)})
            elif len(targets) >= 2:
                emit('double_attack', p.color, [sq]+targets, {'relation': 'geometric_attack', 'targets': [chess.square_name(t) for t in targets]})
            if p.piece_type not in (chess.ROOK, chess.BISHOP, chess.QUEEN):
                continue
            directions = ((1,0),(-1,0),(0,1),(0,-1)) if p.piece_type == chess.ROOK else ((1,1),(1,-1),(-1,1),(-1,-1))
            if p.piece_type == chess.QUEEN:
                directions += ((1,0),(-1,0),(0,1),(0,-1))
            for df, dr in directions:
                occupants = []; f, r = chess.square_file(sq)+df, chess.square_rank(sq)+dr
                while 0 <= f < 8 and 0 <= r < 8:
                    target = chess.square(f, r); q = piece_map.get(target)
                    if q:
                        if q.color == p.color:
                            break
                        occupants.append(target)
                        if len(occupants) == 2:
                            break
                    f += df; r += dr
                if len(occupants) != 2:
                    continue
                front, rear = occupants; a, b = piece_map[front], piece_map[rear]
                if b.piece_type == chess.KING and board.is_pinned(b.color, front):
                    emit('absolute_pin', p.color, [sq, front, rear], {'relation': 'king_pin_ray', 'pinned_piece': a.symbol(),
                         'limitations': 'Moves along the pin ray may remain legal.'})
                elif a.piece_type != chess.KING and b.piece_type != chess.KING and get_piece_value(b.piece_type) > get_piece_value(a.piece_type):
                    emit('relative_pin', p.color, [sq, front, rear], {'relation': 'alignment', 'front': a.symbol(), 'rear': b.symbol()}, True)
                elif a.piece_type == chess.KING or (b.piece_type != chess.KING and get_piece_value(a.piece_type) > get_piece_value(b.piece_type)):
                    emit('skewer_alignment', p.color, [sq, front, rear], {'relation': 'alignment', 'front': a.symbol(), 'rear': b.symbol()})
        coverage['geometry'] = {'status': 'COMPLETE', 'pieces_visited': len(piece_map)}
        attacked = {}
        for sq, p in sorted(piece_map.items()):
            budget.check()
            if p.piece_type == chess.KING:
                continue
            attackers = sorted(board.attackers(not p.color, sq)); defenders = sorted(board.attackers(p.color, sq))
            if attackers:
                attacked[sq] = defenders
                if not defenders:
                    emit('loose_piece', p.color, [sq]+attackers, {'piece': p.symbol(), 'geometric_defenders': [],
                         'geometric_attackers': [chess.square_name(s) for s in attackers],
                         'legal_capture_now': captures.get(sq, []) if board.turn != p.color else None,
                         'legal_witness_status': ('COMPLETE' if legal_complete else 'PARTIAL_BUDGET') if board.turn != p.color else 'NOT_APPLICABLE',
                         'interpretation': 'Attacked and geometrically undefended; compensation is not assessed.'})
        coverage['loose_pieces'] = {'status': 'COMPLETE' if legal_complete else 'PARTIAL_BUDGET', 'legal_moves_visited': len(legal)}
        for sq, p in sorted(piece_map.items()):
            budget.check()
            if p.piece_type == chess.KING:
                continue
            duties = [t for t, defenders in attacked.items() if board.color_at(t) == p.color and sq in defenders]
            if len(duties) >= 2:
                emit('overloaded_defender_candidate', p.color, [sq]+duties, {'relation': 'geometric_defense',
                     'sole_geometric_defender_for': [chess.square_name(t) for t in duties if len(attacked[t]) == 1],
                     'defender_pinned': board.is_pinned(p.color, sq)}, True)
        coverage['overloaded_defenders'] = {'status': 'COMPLETE', 'meaning': 'Geometric duties only; overload is not proven.'}
        # Bounded quiet probes get their own quota, rather than being starved by forcing moves.
        ordered = sorted(legal, key=lambda m: m.uci()); chosen = []
        groups = {'checks': [m for m in ordered if board.gives_check(m)],
                  'captures': [m for m in ordered if board.is_capture(m)],
                  'quiet': [m for m in ordered if not board.gives_check(m) and not board.is_capture(m)]}
        for group in groups.values():
            for m in group[:4]:
                if m not in chosen and budget.probes + len(chosen) < 16:
                    chosen.append(m)
        inspected = 0
        for m in chosen:
            budget.push(); budget.probes += 1
            child = board.copy(stack=False); child.push(m); inspected += 1
            piece = child.piece_at(m.to_square)
            targets = sorted(t for t in child.attacks(m.to_square) if child.color_at(t) == (not board.turn) and child.piece_type_at(t) != chess.PAWN)
            if board.gives_check(m) or board.is_capture(m) or len(targets) >= 2:
                emit('tactical_opportunity', board.turn, [m.from_square, m.to_square]+targets,
                     {'uci': m.uci(), 'san': board.san(m), 'check': board.gives_check(m), 'capture': board.is_capture(m),
                      'new_double_attack_geometry': len(targets) >= 2 and not set(targets).issubset(set(board.attacks(m.from_square))), 'piece': piece.symbol() if piece else None,
                      'root_fen': board.fen(), 'line_origin': 'legal_witness'}, True)
        coverage['opportunities'] = {'status': 'COMPLETE' if inspected == len(legal) and legal_complete else 'PARTIAL_BUDGET',
                                     'inspected': inspected, 'legal_moves_visited': len(legal),
                                     'candidate_group_sizes': {k: len(v) for k, v in groups.items()}, 'global_probe_limit': 16}
        checked = 0
        for m in ordered:
            budget.push()
            child = board.copy(stack=False); child.push(m); checked += 1
            if child.is_checkmate():
                emit('mate_in_one', board.turn, [m.from_square, m.to_square],
                     {'uci': m.uci(), 'san': board.san(m), 'root_fen': board.fen(), 'line_origin': 'legal_witness',
                      'root_history_complete_required_for_game_adjudication': True})
        coverage['mate_in_one'] = {'status': 'COMPLETE' if legal_complete else 'PARTIAL_BUDGET', 'moves_inspected': checked}
    except BudgetExceeded as exc:
        for value in coverage.values():
            if value['status'] == 'NOT_ASSESSED':
                value.update(status='PARTIAL_BUDGET', reason=str(exc))
    return findings, coverage, legal, omitted


def understanding_transition(before, after, move, budget):
    """Facts about the played transition, including multi-square special moves."""
    if before is None:
        return [], []
    import hashlib
    facts = []; discoveries = []; color = before.turn
    victim = move.to_square + (-8 if color else 8) if before.is_en_passant(move) else move.to_square
    capture = before.piece_at(victim) if before.is_capture(move) else None
    facts.append({'kind': 'played_move', 'uci': move.uci(), 'san': before.san(move),
                  'from': chess.square_name(move.from_square), 'to': chess.square_name(move.to_square),
                  'piece_before': before.piece_at(move.from_square).symbol(), 'piece_after': after.piece_at(move.to_square).symbol(),
                  'capture': {'piece': capture.symbol(), 'square': chess.square_name(victim)} if capture else None,
                  'promotion': chess.piece_name(move.promotion) if move.promotion else None,
                  'castling': before.is_castling(move), 'en_passant': before.is_en_passant(move)})
    if before.is_castling(move):
        rank = 0 if color else 7; kingside = chess.square_file(move.to_square) > chess.square_file(move.from_square)
        facts.append({'kind': 'castling_rook', 'from': chess.square_name(chess.square(7 if kingside else 0, rank)),
                      'to': chess.square_name(chess.square(5 if kingside else 3, rank))})
    for sq, piece in sorted(before.piece_map().items()):
        budget.check()
        if piece.color != color or piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN) or after.piece_at(sq) != piece:
            continue
        for target in after.attacks(sq):
            enemy = after.piece_at(target)
            if not enemy or enemy.color == color or before.piece_at(target) != enemy or target in before.attacks(sq):
                continue
            blockers = list(chess.SquareSet(chess.between(sq, target)) & before.occupied)
            if not blockers or any(after.piece_at(s) for s in blockers):
                continue
            squares = [chess.square_name(s) for s in [sq, target]+blockers]
            motif = 'discovered_check' if enemy.piece_type == chess.KING else 'discovered_attack'
            discoveries.append({'id': hashlib.sha256((motif+str(squares)).encode()).hexdigest()[:16],
                                'motif': motif, 'perspective': 'white' if color else 'black', 'stage': 'transition',
                                'provenance': 'BOARD_FACT', 'predicate_status': 'ESTABLISHED', 'squares': squares,
                                'facts': {'relation': 'new_unblocked_ray', 'blockers_removed': [chess.square_name(s) for s in blockers],
                                          'double_check': len(after.checkers()) >= 2 if enemy.piece_type == chess.KING else False},
                                'limitations': ['New attack geometry is not a forced material gain.']})
    return facts, discoveries
