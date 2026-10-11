"""
Server-authoritative context compiler for the Chess Coach.

Extracts position data, engine candidate evaluations, tactical findings,
and player recommendations directly from GameLibrary and existing analysis modules.
Never trusts client-supplied chess evidence.
"""

from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import sys

# Ensure repo root is on sys.path
repo_root = Path(__file__).resolve().parent.parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import GameLibrary, Game
from src.dashboard import build_dashboard_data, resolve_target_player


def validate_game_and_ply(
    library: GameLibrary,
    game_id: Optional[str],
    ply: Optional[int],
) -> Tuple[Optional[Game], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Validate that game_id and ply exist in the server-side GameLibrary.
    Returns (game, analysis, move).
    Raises ValueError if game_id or ply is invalid.
    """
    if not game_id:
        return None, None, None

    game = library.get_game(game_id)
    if not game:
        raise ValueError(f"Game '{game_id}' not found in library")

    analysis = library.get_analysis(game_id)
    if not analysis:
        # Game exists, but not analyzed yet
        return game, None, None

    moves: List[Dict[str, Any]] = analysis.get("moves", [])

    if ply is None or ply == 0:
        return game, analysis, None

    matching = [m for m in moves if m.get("ply") == ply]
    if not matching:
        raise ValueError(f"Ply {ply} not found in analysis for game '{game_id}'")

    return game, analysis, matching[0]


def build_move_context(
    game: Optional[Game],
    analysis: Optional[Dict[str, Any]],
    move: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build structured move-level context from server-side data.
    """
    if not move:
        return {
            "has_move": False,
            "has_analysis": analysis is not None,
            "game_id": game.game_id if game else None,
            "white": game.white if game else None,
            "black": game.black if game else None,
            "result": game.result if game else None,
        }

    # Candidates formatting
    candidates_data = []
    for c in move.get("candidates", []):
        if isinstance(c, dict):
            candidates_data.append({
                "move": c.get("move"),
                "evaluation": c.get("evaluation"),
                "pv": c.get("principal_variation", [])[:4],
            })

    # Tactical finding formatting
    tf = move.get("tactical_finding") or {}
    explanation = move.get("explanation") or {}

    # Episode context
    episode_info = None
    ep_idx = move.get("episode_index")
    if ep_idx is not None and analysis:
        episodes = analysis.get("episodes", [])
        if 0 <= ep_idx < len(episodes):
            ep_moves = episodes[ep_idx]
            episode_info = {
                "episode_index": ep_idx,
                "moves_in_episode": len(ep_moves),
                "critical_moves": [
                    f"{m.get('move_number')}. {m.get('played_move')} ({m.get('classification', 'critical')})"
                    for m in ep_moves if isinstance(m, dict)
                ],
            }

    return {
        "has_move": True,
        "has_analysis": True,
        "game_id": game.game_id if game else None,
        "white": game.white if game else None,
        "black": game.black if game else None,
        "result": game.result if game else None,
        "ply": move.get("ply"),
        "move_number": move.get("move_number"),
        "color": move.get("color"),
        "side_to_move": move.get("color"),
        "played_move": move.get("played_move"),
        "best_move": move.get("best_move"),
        "evaluation_before": move.get("evaluation_before"),
        "evaluation_after": move.get("evaluation_after"),
        "centipawn_loss": move.get("centipawn_loss"),
        "raw_centipawn_loss": move.get("raw_centipawn_loss"),
        "classification": move.get("classification"),
        "is_forced": move.get("is_forced", False),
        "fen_before": move.get("fen_before"),
        "fen_after": move.get("fen_after"),
        "principal_variation": move.get("principal_variation", []),
        "pv_after": move.get("pv_after", []),
        "candidate_moves": candidates_data,
        "tactical_finding": {
            "category": tf.get("category"),
            "confidence": tf.get("confidence"),
            "evidence": tf.get("evidence", {}),
            "limitations": tf.get("limitations"),
        } if tf else None,
        "explanation": {
            "summary": explanation.get("summary"),
            "what_happened": explanation.get("what_happened"),
            "why_it_matters": explanation.get("why_it_matters"),
            "motif": explanation.get("motif"),
            "variation": explanation.get("variation"),
            "confidence": explanation.get("confidence"),
            "limitations": explanation.get("limitations"),
        } if explanation else None,
        "episode_info": episode_info,
        "position_features": move.get("position_features"),
    }


def build_player_context(
    library: GameLibrary,
    player: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Retrieve player-wide profile and recommendations from existing modules.
    """
    target_player = resolve_target_player(library, player)
    if not target_player:
        return {"player_name": "Unknown", "recommendations": [], "strengths": []}

    dashboard = build_dashboard_data(library, target_player)

    return {
        "player_name": dashboard.get("player_name"),
        "overview": dashboard.get("overview", {}),
        "recommendations": dashboard.get("recommendations", [])[:3],
        "strengths": dashboard.get("strengths", [])[:2],
        "openings": dashboard.get("openings", [])[:3],
        "confidence": dashboard.get("confidence", {}),
        "limitations": dashboard.get("limitations", []),
    }


def build_grounded_context_prompt(
    move_ctx: Dict[str, Any],
    player_ctx: Optional[Dict[str, Any]] = None,
    include_player_profile: bool = False,
) -> str:
    """
    Assemble the authoritative prompt context block.
    Distinguishes engine-confirmed facts from interpretation.
    """
    sections = []

    if not move_ctx.get("has_analysis"):
        sections.append("=== ANALYSIS STATUS ===\nNO ANALYSIS AVAILABLE FOR THIS GAME.")
        return "\n\n".join(sections)

    if not move_ctx.get("has_move"):
        sections.append("=== POSITION STATUS ===\nNO SPECIFIC MOVE SELECTED (Starting Position).")
    else:
        # Move details
        ply = move_ctx.get("ply")
        move_num = move_ctx.get("move_number")
        color = move_ctx.get("color")
        played = move_ctx.get("played_move")
        best = move_ctx.get("best_move")
        cpl = move_ctx.get("centipawn_loss")
        cls = move_ctx.get("classification")
        eval_b = move_ctx.get("evaluation_before")
        eval_a = move_ctx.get("evaluation_after")

        move_block = [
            "=== AUTHORITATIVE STOCKFISH ENGINE ANALYSIS ===",
            f"Ply: {ply}",
            f"Move Number: {move_num}",
            f"Side to Move: {color}",
            f"Played Move: {played}",
            f"Best Move: {best}",
            f"Classification: {cls}",
            f"Centipawn Loss: {cpl} cp",
            f"Evaluation Before: {eval_b} cp",
            f"Evaluation After: {eval_a} cp",
            f"FEN Before: {move_ctx.get('fen_before')}",
            f"FEN After: {move_ctx.get('fen_after')}",
        ]

        pv = move_ctx.get("principal_variation", [])
        if pv:
            move_block.append(f"Engine Recommended Line: {' '.join(pv[:5])}")

        pv_after = move_ctx.get("pv_after", [])
        if pv_after:
            move_block.append(f"Refutation Line (pv_after): {' '.join(pv_after[:5])}")

        sections.append("\n".join(move_block))

        # Candidates
        candidates = move_ctx.get("candidate_moves", [])
        if candidates:
            cand_lines = ["=== CANDIDATE MOVES (RANKED BY ENGINE) ==="]
            for idx, c in enumerate(candidates, 1):
                cand_lines.append(
                    f"{idx}. {c.get('move')} (Eval: {c.get('evaluation')} cp, Line: {' '.join(c.get('pv', [])[:3])})"
                )
            sections.append("\n".join(cand_lines))

        # Tactical finding
        tf = move_ctx.get("tactical_finding")
        if tf and tf.get("category"):
            tf_lines = [
                "=== TACTICAL EVIDENCE ===",
                f"Tactical Category: {tf.get('category')}",
                f"Confidence: {tf.get('confidence')}",
            ]
            ev = tf.get("evidence", {})
            for k, v in ev.items():
                tf_lines.append(f"- {k}: {v}")
            if tf.get("limitations"):
                tf_lines.append(f"Limitations: {tf.get('limitations')}")
            sections.append("\n".join(tf_lines))
        else:
            sections.append("=== TACTICAL EVIDENCE ===\nNo tactical motif or consequence detected by the engine.")

        # Deterministic explanation
        expl = move_ctx.get("explanation")
        if expl and expl.get("summary"):
            expl_lines = [
                "=== DETERMINISTIC EXPLANATION ===",
                f"Explanation Summary: {expl.get('summary')}",
                f"What Happened: {expl.get('what_happened')}",
                f"Why It Matters: {expl.get('why_it_matters')}",
                f"Tactical Motif: {expl.get('motif')}",
                f"Explanation Variation: {expl.get('variation')}",
            ]
            if expl.get("limitations"):
                expl_lines.append(f"Explanation Limitations: {expl.get('limitations')}")
            sections.append("\n".join(expl_lines))

        # Critical Episode
        ep = move_ctx.get("episode_info")
        if ep:
            ep_lines = [
                "=== CRITICAL EPISODE CONTEXT ===",
                f"Episode Index: {ep.get('episode_index')}",
                f"Total Critical Moves in Episode: {ep.get('moves_in_episode')}",
                f"Critical Moves in Sequence: {', '.join(ep.get('critical_moves', []))}",
            ]
            sections.append("\n".join(ep_lines))

    # Player Context
    if include_player_profile and player_ctx:
        recs = player_ctx.get("recommendations", [])
        if recs:
            p_lines = [
                "=== PLAYER COACHING RECOMMENDATIONS ===",
                f"Player: {player_ctx.get('player_name')}",
            ]
            for r in recs:
                p_lines.append(f"- **{r.get('title')}** (Priority: {r.get('priority')}): {r.get('description')}")
                aff = r.get("affected_moves", [])
                if aff:
                    samples = [f"Game {m.get('game_id')} Move {m.get('move_text')} ({m.get('cpl')} cp)" for m in aff[:2]]
                    p_lines.append(f"  Examples: {'; '.join(samples)}")
            sections.append("\n".join(p_lines))

    return "\n\n".join(sections)
