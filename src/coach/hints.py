"""
Progressive Hint Engine for the Chess Coach.

Implements three distinct hint levels with strict anti-leakage protections:
- Tier 1 (Conceptual): Strategic theme or board area; never reveals the best move SAN.
- Tier 2 (Specific Clue): Target piece, tactic, or sector; never reveals the best move SAN.
- Tier 3 (Reveal): Authoritative Stockfish best move and principal variation.
"""

from typing import Dict, Any, Optional
import re

HINT_TIER_CONCEPTUAL = 1
HINT_TIER_SPECIFIC = 2
HINT_TIER_REVEAL = 3


def check_and_sanitize_hint_leakage(hint_text: str, best_move: Optional[str], tier: int) -> str:
    """
    Ensure Tier 1 and Tier 2 hints do not leak the best move's SAN.
    If the best move SAN or direct move token appears, mask or sanitize it.
    """
    if tier >= HINT_TIER_REVEAL or not best_move or not hint_text:
        return hint_text

    san_clean = best_move.strip().rstrip("+#?!")
    if not san_clean:
        return hint_text

    # Match exact word boundaries for the SAN (e.g. \bNf3\b, \be4\b, \bQxd5\b)
    pattern = re.compile(rf"\b{re.escape(san_clean)}\b", re.IGNORECASE)
    if pattern.search(hint_text):
        # Sanitize by replacing with generic indicator
        hint_text = pattern.sub("the recommended square", hint_text)

    # Also check if best_move without piece prefix (e.g. for Nf3 -> f3) is specifically named
    # only if length >= 2 to avoid single letter matches
    if len(san_clean) >= 3 and san_clean[0].isupper():
        dest_sq = san_clean[-2:]
        if re.match(r"^[a-h][1-8]$", dest_sq):
            pattern_dest = re.compile(rf"\b(move to |play to |to ){re.escape(dest_sq)}\b", re.IGNORECASE)
            hint_text = pattern_dest.sub(r"\1an active square", hint_text)

    return hint_text


def generate_progressive_hint(move_ctx: Dict[str, Any], tier: int) -> str:
    """
    Generate an authoritative, evidence-grounded progressive hint for the given tier (1, 2, or 3).
    """
    best_move = move_ctx.get("best_move", "")
    tactical_finding = move_ctx.get("tactical_finding") or {}
    explanation = move_ctx.get("explanation") or {}
    candidates = move_ctx.get("candidate_moves") or []
    engine_pv = move_ctx.get("principal_variation") or []
    pv_str = " ".join(engine_pv[:5]) if isinstance(engine_pv, list) else str(engine_pv)

    tf_cat = tactical_finding.get("category", "")
    evidence = tactical_finding.get("evidence", {})
    motif = explanation.get("motif", "")
    what_happened = explanation.get("what_happened", "")
    side = move_ctx.get("side_to_move", "White")

    # --- TIER 1: CONCEPTUAL HINT ---
    if tier == HINT_TIER_CONCEPTUAL:
        theme = "active piece play"
        sector = "the center and vulnerable enemy pieces"

        if "hanging" in tf_cat or "hanging" in motif.lower():
            theme = "tactical vulnerability and undefended pieces"
            sector = "where the opponent has loose, unprotected pieces"
        elif "material" in tf_cat or "capture" in tf_cat:
            theme = "a forcing capture or tactical sequence"
            sector = "pieces that are insufficiently protected"
        elif "check" in tf_cat or "forcing" in tf_cat:
            theme = "forcing candidate moves that limit your opponent's options"
            sector = "the enemy king's safety"
        elif motif and motif.lower() not in ("none", "unclassified"):
            theme = f"the tactical motif of a **{motif}**"
        elif "checkmate" in what_happened.lower() or "mate" in what_happened.lower():
            theme = "king defense and avoiding mate threats"
            sector = "safe squares for your king"

        hint = (
            f"💡 **Conceptual Hint (Level 1/3)**:\n\n"
            f"Focus on **{theme}**. Scan **{sector}** before calculating quiet moves. "
            f"Ask yourself: which opponent pieces are undefended, and how can your active pieces exploit them?"
        )
        return check_and_sanitize_hint_leakage(hint, best_move, tier)

    # --- TIER 2: SPECIFIC CLUE ---
    elif tier == HINT_TIER_SPECIFIC:
        clues = []

        # Which piece type should move?
        if best_move:
            first_char = best_move[0]
            piece_names = {"N": "Knight", "B": "Bishop", "R": "Rook", "Q": "Queen", "K": "King"}
            moving_piece = piece_names.get(first_char, "Pawn")
            clues.append(f"Consider moves with your **{moving_piece}**.")

        # Evidence target
        target_piece = evidence.get("hanging_piece") or evidence.get("captured_piece")
        target_square = evidence.get("square")

        if target_piece and target_square:
            clues.append(f"Notice the opponent's **{target_piece}** on **{target_square}**.")
        elif target_square:
            clues.append(f"Look closely at control over the **{target_square}** square.")

        if what_happened and not any(best_move.lower() in sentence.lower() for sentence in what_happened.split(".")):
            clues.append(f"Context: {what_happened}")

        hint_body = " ".join(clues) if clues else "Look for a forcing initiative that changes the balance in the center."

        hint = (
            f"🔎 **Specific Clue (Level 2/3)**:\n\n"
            f"{hint_body}\n\n"
            f"Try to visualize the position after that piece makes an active jump or strike."
        )
        return check_and_sanitize_hint_leakage(hint, best_move, tier)

    # --- TIER 3: FULL REVEAL ---
    else:
        reveal_lines = [
            f"🔓 **Best Move Revealed (Level 3/3)**:\n\n"
            f"The engine's best move in this position is **`{best_move}`**."
        ]

        if pv_str:
            reveal_lines.append(f"**Recommended Continuation**: `{pv_str}`")

        eval_b = move_ctx.get("evaluation_before")
        if eval_b is not None:
            reveal_lines.append(f"**Position Evaluation**: {eval_b:+d} centipawns (from mover's perspective).")

        why = explanation.get("why_it_matters") or explanation.get("what_happened")
        if why:
            reveal_lines.append(f"**Why it works**: {why}")

        return "\n\n".join(reveal_lines)
