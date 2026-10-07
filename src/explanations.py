from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
import chess

try:
    from models import MoveAnalysis
    from tactical_analysis import (
        TacticalFinding,
        CATEGORY_MATERIAL_LOSS,
        CATEGORY_MISSED_CAPTURE,
        CATEGORY_HANGING_PIECE,
        CATEGORY_MISSED_CHECK_OR_FORCING,
        CATEGORY_UNCLASSIFIED,
        CONFIDENCE_HIGH,
        CONFIDENCE_MEDIUM,
        CONFIDENCE_LOW,
    )
except ImportError:
    from src.models import MoveAnalysis
    from src.tactical_analysis import (
        TacticalFinding,
        CATEGORY_MATERIAL_LOSS,
        CATEGORY_MISSED_CAPTURE,
        CATEGORY_HANGING_PIECE,
        CATEGORY_MISSED_CHECK_OR_FORCING,
        CATEGORY_UNCLASSIFIED,
        CONFIDENCE_HIGH,
        CONFIDENCE_MEDIUM,
        CONFIDENCE_LOW,
    )


@dataclass
class MoveExplanation:
    """
    Structured human-readable explanation of an analyzed chess move.
    """
    summary: str
    what_happened: str
    why_it_matters: str
    better_move: Optional[str]
    variation: str
    motif: str
    confidence: str
    limitations: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "what_happened": self.what_happened,
            "why_it_matters": self.why_it_matters,
            "better_move": self.better_move,
            "variation": self.variation,
            "motif": self.motif,
            "confidence": self.confidence,
            "limitations": self.limitations,
        }


def format_variation(variation: Any, max_plies: int = 6) -> str:
    """Format a principal variation or move list into readable SAN string."""
    if not variation:
        return ""
    if isinstance(variation, str):
        return variation
    san_moves = []
    for item in variation[:max_plies]:
        if isinstance(item, str):
            san_moves.append(item)
    return " ".join(san_moves)


def format_material_points(pts: int) -> str:
    """Format material point count with proper pluralization."""
    abs_pts = abs(pts)
    return f"{abs_pts} point" if abs_pts == 1 else f"{abs_pts} points"


def get_opponent_color(color: str) -> str:
    """Return opponent color name."""
    return "Black" if color.lower() == "white" else "White"


def is_forking_check(board_before: chess.Board, move_san: str) -> bool:
    """
    Check if a candidate move delivers check while attacking at least one other enemy piece.
    """
    try:
        move = board_before.parse_san(move_san)
    except (ValueError, chess.IllegalMoveError):
        return False

    if not board_before.is_legal(move) or not board_before.gives_check(move):
        return False

    sim_board = board_before.copy()
    sim_board.push(move)
    to_sq = move.to_square
    mover = board_before.turn
    opponent = not mover
    opp_king_sq = sim_board.king(opponent)

    for sq in chess.SQUARES:
        if sq == opp_king_sq:
            continue
        p = sim_board.piece_at(sq)
        if p and p.color == opponent:
            attackers = sim_board.attackers(mover, sq)
            if to_sq in attackers:
                return True

    return False


def explain_missed_capture(
    analysis: MoveAnalysis,
    evidence: Dict[str, Any],
    confidence: str,
    limitations: Optional[str]
) -> MoveExplanation:
    better_move = evidence.get("missed_capture_move") or analysis.best_move or ""
    captured_piece = evidence.get("captured_piece", "piece")
    captured_val = evidence.get("captured_value", 0)
    net_gain = evidence.get("net_material_gain", 0)
    target_sq = evidence.get("target_square", "")
    seq = evidence.get("candidate_sequence", [])
    variation_str = format_variation(seq)

    article = "an" if captured_piece.lower().startswith(("a", "e", "i", "o", "u")) else "a"

    if net_gain == captured_val:
        summary = f"You missed {better_move}, which wins {article} {captured_piece}."
    else:
        summary = f"You missed {better_move}, which wins {article} {captured_piece} for a net gain of {format_material_points(net_gain)}."

    at_target = f" on {target_sq}" if target_sq else ""
    what_happened = f"You played {analysis.played_move} instead of capturing the {captured_piece}{at_target} with {better_move}."
    why_it_matters = f"The capture sequence secures a net advantage of {format_material_points(net_gain)} of material."

    return MoveExplanation(
        summary=summary,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        better_move=better_move,
        variation=variation_str,
        motif=CATEGORY_MISSED_CAPTURE,
        confidence=confidence,
        limitations=limitations,
    )


def explain_material_loss(
    analysis: MoveAnalysis,
    evidence: Dict[str, Any],
    confidence: str,
    limitations: Optional[str],
    board_after: Optional[chess.Board] = None
) -> MoveExplanation:
    played_move = analysis.played_move
    better_move = analysis.best_move or ""
    net_loss = evidence.get("net_material_loss", 0)
    played_caps = evidence.get("played_captures", [])
    played_seq = evidence.get("played_sequence", [])
    better_seq = evidence.get("better_sequence", [])
    variation_str = format_variation(better_seq) if better_seq else format_variation(played_seq)
    opp_color = get_opponent_color(analysis.color)

    # Check if a specific piece lost by mover can be identified
    lost_piece_desc = None
    target_sq = None
    if played_caps:
        for cap in played_caps:
            if cap.get("by_color") == opp_color:
                lost_piece_desc = cap.get("captured_piece")
                cap_move = cap.get("move", "")
                if cap_move and len(cap_move) >= 2:
                    # Clean target square from SAN move (e.g. Bxe5+ -> e5)
                    clean = cap_move.rstrip("+#!?")
                    if len(clean) >= 2 and clean[-2] in "abcdefgh" and clean[-1] in "12345678":
                        target_sq = clean[-2:]
                break

    if lost_piece_desc:
        at_sq = f" on {target_sq}" if target_sq else ""
        summary = f"After {played_move}, your {lost_piece_desc}{at_sq} is lost, costing {format_material_points(net_loss)} of material."
        what_happened = f"Playing {played_move} allows {opp_color} to capture your {lost_piece_desc} in the continuation."
    else:
        summary = f"After {played_move}, you lose {format_material_points(net_loss)} of material compared to {better_move}."
        what_happened = f"In the played line, {opp_color} wins material through a concrete tactical sequence."

    why_it_matters = f"This leaves you at a net material deficit of {format_material_points(net_loss)} compared to playing {better_move}."

    return MoveExplanation(
        summary=summary,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        better_move=better_move,
        variation=variation_str,
        motif=CATEGORY_MATERIAL_LOSS,
        confidence=confidence,
        limitations=limitations,
    )


def explain_hanging_piece(
    analysis: MoveAnalysis,
    evidence: Dict[str, Any],
    confidence: str,
    limitations: Optional[str]
) -> MoveExplanation:
    played_move = analysis.played_move
    better_move = analysis.best_move or ""
    piece_name = evidence.get("hanging_piece", "piece")
    sq = evidence.get("square", "")
    val = evidence.get("piece_value", 0)
    defenders = evidence.get("defenders_count", 0)
    attackers = evidence.get("attackers_count", 0)
    opp_cap = evidence.get("opponent_capture_move")
    opp_color = get_opponent_color(analysis.color)

    at_sq = f" on {sq}" if sq else ""
    summary = f"Your {piece_name}{at_sq} was left vulnerable and could be captured."

    if defenders == 0:
        what_happened = f"Playing {played_move} left your {piece_name}{at_sq} completely undefended."
    else:
        what_happened = f"Playing {played_move} left your {piece_name}{at_sq} insufficiently defended against {attackers} attackers."

    if opp_cap:
        why_it_matters = f"{opp_color} can respond with {opp_cap}, winning your {piece_name} ({format_material_points(val)})."
        variation_str = opp_cap
    else:
        why_it_matters = f"The {piece_name} is exposed to immediate capture without adequate compensation, risking {format_material_points(val)}."
        variation_str = format_variation(analysis.pv_after) if analysis.pv_after else ""

    return MoveExplanation(
        summary=summary,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        better_move=better_move,
        variation=variation_str,
        motif=CATEGORY_HANGING_PIECE,
        confidence=confidence,
        limitations=limitations,
    )


def explain_missed_check_or_forcing_move(
    analysis: MoveAnalysis,
    evidence: Dict[str, Any],
    confidence: str,
    limitations: Optional[str],
    board_before: Optional[chess.Board] = None
) -> MoveExplanation:
    played_move = analysis.played_move
    better_move = evidence.get("forcing_candidate_move") or analysis.best_move or ""
    eval_diff = evidence.get("eval_difference", 0)
    forcing_seq = evidence.get("forcing_sequence", [])
    variation_str = format_variation(forcing_seq)
    opp_color = get_opponent_color(analysis.color)
    eval_pawns = eval_diff / 100.0 if eval_diff else 0.0

    is_fork = False
    if board_before and better_move:
        is_fork = is_forking_check(board_before, better_move)

    if is_fork:
        summary = f"{better_move} was stronger because it gives check while creating a fork."
        what_happened = f"You played {played_move}, missing {better_move} which checks the king and forks another piece."
    else:
        summary = f"{better_move} was stronger because it delivers a forcing check that keeps the initiative."
        what_happened = f"You played {played_move}, missing the forcing check {better_move}."

    why_it_matters = f"The check severely restricts {opp_color}'s choices, yielding a verified advantage of {eval_pawns:.2f} pawns."

    return MoveExplanation(
        summary=summary,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        better_move=better_move,
        variation=variation_str,
        motif=CATEGORY_MISSED_CHECK_OR_FORCING,
        confidence=confidence,
        limitations=limitations,
    )


def explain_unclassified(
    analysis: MoveAnalysis,
    evidence: Dict[str, Any],
    confidence: str,
    limitations: Optional[str]
) -> MoveExplanation:
    played_move = analysis.played_move
    better_move = analysis.best_move or ""
    cpl = analysis.centipawn_loss or 0
    cpl_pawns = cpl / 100.0
    cand_pv = analysis.candidates[0].principal_variation if analysis.candidates else analysis.principal_variation
    variation_str = format_variation(cand_pv)
    opp_color = get_opponent_color(analysis.color)

    # Check for mate transitions
    eb = analysis.evaluation_before
    ea = analysis.evaluation_after
    if ea is not None and ea <= -90000 and (eb is None or eb > -90000):
        summary = f"{played_move} blundered into a forced checkmate against you."
        what_happened = f"You played {played_move}. The engine found a forced checkmate continuation for {opp_color}."
        why_it_matters = f"The position becomes lost; {better_move} was required to prevent immediate forced mate."
    elif eb is not None and eb >= 90000 and (ea is None or ea < 90000):
        summary = f"{played_move} allowed {opp_color} to escape a forced checkmate."
        what_happened = f"You played {played_move} instead of {better_move}, letting a winning forced checkmate sequence slip."
        why_it_matters = f"The decisive winning mate was postponed or lost."
    else:
        summary = f"{played_move} was inaccurate, conceding an evaluation drop of {cpl_pawns:.2f} pawns compared to {better_move}."
        what_happened = f"You played {played_move}. The engine preferred {better_move}."
        why_it_matters = "No immediate tactical loss or hanging piece was verified; the evaluation drop reflects positional or endgame factors beyond short tactics."

    return MoveExplanation(
        summary=summary,
        what_happened=what_happened,
        why_it_matters=why_it_matters,
        better_move=better_move,
        variation=variation_str,
        motif=CATEGORY_UNCLASSIFIED,
        confidence=confidence,
        limitations=limitations or "Positional or strategic factor beyond verified short-tactics heuristics",
    )


def generate_move_explanation(
    analysis: MoveAnalysis,
    board_before: Optional[chess.Board] = None,
    board_after: Optional[chess.Board] = None,
) -> MoveExplanation:
    """
    Generate a concise, human-readable, evidence-grounded chess explanation for an analyzed move.
    """
    if board_before is None and analysis.fen_before:
        try:
            board_before = chess.Board(analysis.fen_before)
        except ValueError:
            pass

    if board_after is None and analysis.fen_after:
        try:
            board_after = chess.Board(analysis.fen_after)
        except ValueError:
            pass

    tf = getattr(analysis, "tactical_finding", None)
    if isinstance(tf, TacticalFinding):
        tf_dict = tf.to_dict()
    elif isinstance(tf, dict):
        tf_dict = tf
    else:
        tf_dict = None

    # Handle non-critical moves without tactical findings
    if tf_dict is None:
        if analysis.is_forced:
            return MoveExplanation(
                summary=f"{analysis.played_move} was a forced move.",
                what_happened=f"{analysis.played_move} was the only legal move available.",
                why_it_matters="No alternatives existed in this position.",
                better_move=None,
                variation="",
                motif="forced",
                confidence=CONFIDENCE_HIGH,
                limitations=None,
            )

        is_best = False
        if analysis.best_move and analysis.played_move == analysis.best_move:
            is_best = True
        elif analysis.centipawn_loss == 0:
            is_best = True

        if is_best:
            cand_pv = analysis.candidates[0].principal_variation if analysis.candidates else analysis.principal_variation
            return MoveExplanation(
                summary=f"{analysis.played_move} was the engine's recommended move.",
                what_happened=f"You played the best move {analysis.played_move}.",
                why_it_matters="This maintains the optimal evaluation and strategic balance.",
                better_move=analysis.played_move,
                variation=format_variation(cand_pv),
                motif="best_move",
                confidence=CONFIDENCE_HIGH,
                limitations=None,
            )

        # Inaccuracy without critical threshold
        cpl = analysis.centipawn_loss or 0
        cand_pv = analysis.candidates[0].principal_variation if analysis.candidates else analysis.principal_variation
        return MoveExplanation(
            summary=f"{analysis.played_move} was solid, conceding only {cpl / 100:.2f} pawns.",
            what_happened=f"You played {analysis.played_move} (engine recommendation: {analysis.best_move}).",
            why_it_matters="The move preserves position stability without significant concession.",
            better_move=analysis.best_move if analysis.best_move else None,
            variation=format_variation(cand_pv),
            motif="inaccuracy" if cpl >= 25 else "good_move",
            confidence=CONFIDENCE_MEDIUM,
            limitations=None,
        )

    cat = tf_dict.get("category", CATEGORY_UNCLASSIFIED)
    conf = tf_dict.get("confidence", CONFIDENCE_LOW)
    ev = tf_dict.get("evidence", {})
    limitations = tf_dict.get("limitations")

    if cat == CATEGORY_MISSED_CAPTURE:
        return explain_missed_capture(analysis, ev, conf, limitations)
    elif cat == CATEGORY_MATERIAL_LOSS:
        return explain_material_loss(analysis, ev, conf, limitations, board_after=board_after)
    elif cat == CATEGORY_HANGING_PIECE:
        return explain_hanging_piece(analysis, ev, conf, limitations)
    elif cat == CATEGORY_MISSED_CHECK_OR_FORCING:
        return explain_missed_check_or_forcing_move(analysis, ev, conf, limitations, board_before=board_before)
    else:
        return explain_unclassified(analysis, ev, conf, limitations)
