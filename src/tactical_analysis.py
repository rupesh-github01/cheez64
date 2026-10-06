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
    pv_after: List[str]
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
                captured_square = first_move.to_square
                captured_piece = board_after.piece_at(captured_square)

                # Did the opponent capture a piece belonging to the mover?
                if captured_piece and captured_piece.color == mover:
                    piece_val = get_piece_value(captured_piece.piece_type)
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

    if net_gain > 0 or captured_val >= 3:
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
    if not better_move:
        return None

    try:
        played_obj = board_before.parse_san(played_move)
        played_gives_check = board_before.gives_check(played_obj)
    except (ValueError, chess.IllegalMoveError):
        played_gives_check = False

    try:
        better_obj = board_before.parse_san(better_move)
        better_gives_check = board_before.gives_check(better_obj)
    except (ValueError, chess.IllegalMoveError):
        better_gives_check = False

    # Mover missed an immediate check that is the top candidate
    if better_gives_check and not played_gives_check:
        pv_moves = candidate_pv if candidate_pv else [better_move]
        evidence = {
            "forcing_candidate_move": better_move,
            "is_check": True,
            "played_move_is_check": False,
            "eval_difference": eval_diff,
            "forcing_sequence": pv_moves[:4],
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
    hanging_finding = check_hanging_piece(board_after, mover, pv_after)
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
