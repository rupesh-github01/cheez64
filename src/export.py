import json
from pathlib import Path
from typing import List, Optional, Dict, Any

try:
    from models import MoveAnalysis, CandidateMove
    from critical import classify_move, find_critical_positions
    from episodes import group_critical_positions
except ImportError:
    from src.models import MoveAnalysis, CandidateMove
    from src.critical import classify_move, find_critical_positions
    from src.episodes import group_critical_positions


SCHEMA_VERSION = "1.1.0"
# Migration note: Schema 1.1.0 adds an optional 'tactical_finding' object to critical moves
# and 'tactical_motifs_count' to the summary. All 1.0.0 fields remain unchanged.


def serialize_analysis(
    game,
    analyses: List[MoveAnalysis],
    critical: Optional[List[MoveAnalysis]] = None,
    episodes: Optional[List[List[MoveAnalysis]]] = None,
    minimum_cpl: int = 75
) -> Dict[str, Any]:
    """
    Serialize chess game analysis into a clean, JSON-compatible dictionary.

    Schema:
    - schema_version: Semantic version of JSON format.
    - metadata: PGN game headers (players, event, date, elo, result, etc.).
    - summary: Aggregated statistics (total moves, critical counts, etc.).
    - moves: Move-by-move evaluation, candidates, PVs, and features.
    - episodes: Grouped tactical sequences of critical moves.
    """
    if critical is None:
        critical = find_critical_positions(analyses, minimum_cpl=minimum_cpl)

    if episodes is None:
        episodes = group_critical_positions(analyses, minimum_cpl=minimum_cpl)

    # Build lookup for episode index per move
    # An analysis object's identity or (move_number, color)
    move_to_episode: Dict[int, int] = {}
    for ep_idx, ep in enumerate(episodes, start=1):
        for m in ep:
            move_to_episode[id(m)] = ep_idx

    critical_ids = {id(m) for m in critical}

    # Extract metadata from game headers
    metadata = {}
    if game is not None and hasattr(game, "headers"):
        for key, value in game.headers.items():
            metadata[key.lower()] = value

    # Serialize moves
    serialized_moves = []
    for ply_idx, analysis in enumerate(analyses, start=1):
        # Serialize candidates
        serialized_candidates = []
        for cand in analysis.candidates:
            serialized_candidates.append({
                "move": cand.move,
                "evaluation": cand.evaluation,
                "principal_variation": list(cand.principal_variation)
            })

        # Determine classification if not already set
        classification = analysis.classification
        if not classification and analysis.centipawn_loss is not None:
            classification = classify_move(analysis.centipawn_loss)

        move_data = {
            "ply": ply_idx,
            "move_number": analysis.move_number,
            "color": analysis.color,
            "played_move": analysis.played_move,
            "played_move_uci": analysis.played_move_uci,
            "best_move": analysis.best_move if analysis.best_move else None,
            "best_move_uci": analysis.best_move_uci,
            "is_forced": analysis.is_forced,
            "evaluation_before": analysis.evaluation_before,
            "evaluation_after": analysis.evaluation_after,
            "raw_centipawn_loss": analysis.raw_centipawn_loss,
            "centipawn_loss": analysis.centipawn_loss,
            "classification": classification,
            "tactical_finding": getattr(analysis, "tactical_finding", None),
            "is_critical": id(analysis) in critical_ids,
            "episode_index": move_to_episode.get(id(analysis)),
            "fen_before": analysis.fen_before,
            "fen_after": analysis.fen_after,
            "clock_seconds": analysis.clock_seconds,
            "principal_variation": list(analysis.principal_variation),
            "candidates": serialized_candidates,
            "position_features": analysis.position_features
        }
        serialized_moves.append(move_data)

    # Serialize episodes
    serialized_episodes = []
    for ep_idx, ep in enumerate(episodes, start=1):
        ep_moves = []
        for m in ep:
            ep_moves.append({
                "move_number": m.move_number,
                "color": m.color,
                "played_move": m.played_move,
                "centipawn_loss": m.centipawn_loss,
                "classification": m.classification or classify_move(m.centipawn_loss),
                "tactical_finding": getattr(m, "tactical_finding", None)
            })
        serialized_episodes.append({
            "episode_index": ep_idx,
            "move_count": len(ep),
            "moves": ep_moves
        })

    tactical_summary: Dict[str, int] = {}
    for a in critical:
        tf = getattr(a, "tactical_finding", None)
        cat = tf.get("category", "unclassified") if isinstance(tf, dict) else "unclassified"
        tactical_summary[cat] = tactical_summary.get(cat, 0) + 1

    summary = {
        "total_plies": len(analyses),
        "total_full_moves": analyses[-1].move_number if analyses else 0,
        "critical_positions_count": len(critical),
        "critical_episodes_count": len(episodes),
        "tactical_motifs_count": tactical_summary
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": metadata,
        "summary": summary,
        "moves": serialized_moves,
        "episodes": serialized_episodes
    }


def export_to_json_file(
    game,
    analyses: List[MoveAnalysis],
    output_path: str,
    critical: Optional[List[MoveAnalysis]] = None,
    episodes: Optional[List[List[MoveAnalysis]]] = None,
    minimum_cpl: int = 75,
    indent: int = 2
) -> str:
    """
    Serialize analysis and write to a JSON file at output_path.
    """
    data = serialize_analysis(
        game,
        analyses,
        critical=critical,
        episodes=episodes,
        minimum_cpl=minimum_cpl
    )

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=indent, ensure_ascii=False)

    return str(path.resolve())
