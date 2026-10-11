"""
Player Dashboard & Aggregated Performance Statistics Module.

Provides read-only multi-game performance aggregation, chronological trends,
opening repertoire breakdown, and links recommendations and weaknesses
to specific game reviews and positions without duplicating chess domain logic.
"""

from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
import sys

# Ensure repository root is on sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.library import GameLibrary, Game
from src.profile import (
    PlayerProfile,
    build_player_profile,
    build_player_profile_from_library,
)
from src.recommendations import (
    CoachingProfile,
    build_coaching_profile_from_library,
)
from src.openings import (
    RepertoireProfile,
    build_player_repertoire_from_library,
)


def resolve_target_player(library: GameLibrary, requested_player: Optional[str] = None) -> Optional[str]:
    """
    Resolve the primary player name for dashboard analysis.
    If requested_player is given, verifies or uses it.
    Otherwise, picks the player with the highest game count in the library.
    """
    if requested_player and requested_player.strip():
        return requested_player.strip()

    all_games = library.list_games(limit=500)
    if not all_games:
        return None

    player_counts: Dict[str, int] = {}
    for g in all_games:
        if g.white:
            player_counts[g.white] = player_counts.get(g.white, 0) + 1
        if g.black:
            player_counts[g.black] = player_counts.get(g.black, 0) + 1

    if not player_counts:
        return None

    best_player, _ = max(player_counts.items(), key=lambda item: item[1])
    return best_player


def _parse_game_result_for_color(result_str: str, color: str) -> str:
    """
    Returns 'win', 'loss', 'draw', or 'unknown' for a given color.
    """
    if result_str == "1-0":
        return "win" if color == "White" else "loss"
    elif result_str == "0-1":
        return "win" if color == "Black" else "loss"
    elif result_str in ("1/2-1/2", "0.5-0.5", "draw"):
        return "draw"
    return "unknown"


def _find_move_ply(analysis: Dict[str, Any], color: str, move_num: int, played_move: str) -> Optional[int]:
    """
    Find the exact ply in an analysis for a given move number and move text.
    """
    moves = analysis.get("moves", [])
    for m in moves:
        if (
            m.get("move_number") == move_num
            and m.get("color", "").lower() == color.lower()
        ):
            if not played_move or m.get("played_move") == played_move:
                return m.get("ply")

    # Fallback to standard ply formula
    if color.lower() == "white":
        return (move_num - 1) * 2 + 1
    else:
        return (move_num - 1) * 2 + 2


def build_dashboard_data(
    library: GameLibrary,
    player: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build the comprehensive dashboard overview, trends, weaknesses, strengths,
    and opening statistics for the specified or detected player.
    """
    total_library_games = library.count_games()
    all_library_games = library.list_games(limit=1000)
    all_analyzed_games = [g for g in all_library_games if g.analysis_status == "ANALYZED"]
    library_analyzed_count = len(all_analyzed_games)
    library_unanalyzed_count = total_library_games - library_analyzed_count

    target_player = resolve_target_player(library, player)

    # Empty library or no player detected
    if not target_player or not all_library_games:
        return {
            "player_name": target_player or "Unknown",
            "overview": {
                "total_library_games": total_library_games,
                "library_analyzed_games": library_analyzed_count,
                "library_unanalyzed_games": library_unanalyzed_count,
                "player_games_total": 0,
                "player_games_analyzed": 0,
                "player_games_unanalyzed": 0,
                "results": {"wins": 0, "draws": 0, "losses": 0, "unknown": 0, "win_rate": 0.0},
                "white_stats": {"games": 0, "wins": 0, "draws": 0, "losses": 0, "win_rate": 0.0},
                "black_stats": {"games": 0, "wins": 0, "draws": 0, "losses": 0, "win_rate": 0.0},
                "analyzed_metrics": None,
            },
            "trends": [],
            "weaknesses": [],
            "strengths": [],
            "recommendations": [],
            "openings": [],
            "confidence": {
                "level": "none",
                "sample_size": 0,
                "label": "No games found",
                "warning": "The game library is currently empty. Import games to view player statistics.",
            },
            "limitations": ["No games available for performance aggregation."],
        }

    # Filter games where target player participated
    player_games: List[Game] = []
    for g in all_library_games:
        if (target_player.lower() in g.white.lower()) or (target_player.lower() in g.black.lower()):
            player_games.append(g)

    player_analyzed_games = [g for g in player_games if g.analysis_status == "ANALYZED"]
    player_unanalyzed_games = [g for g in player_games if g.analysis_status != "ANALYZED"]

    # Results breakdown across all player games
    white_wins = white_draws = white_losses = 0
    black_wins = black_draws = black_losses = 0
    unknown_results = 0

    for g in player_games:
        is_white = target_player.lower() in g.white.lower()
        color = "White" if is_white else "Black"
        outcome = _parse_game_result_for_color(g.result, color)

        if outcome == "win":
            if is_white:
                white_wins += 1
            else:
                black_wins += 1
        elif outcome == "loss":
            if is_white:
                white_losses += 1
            else:
                black_losses += 1
        elif outcome == "draw":
            if is_white:
                white_draws += 1
            else:
                black_draws += 1
        else:
            unknown_results += 1

    total_wins = white_wins + black_wins
    total_draws = white_draws + black_draws
    total_losses = white_losses + black_losses
    decided_games = total_wins + total_draws + total_losses

    overall_win_rate = round(total_wins / decided_games, 3) if decided_games > 0 else 0.0
    white_decided = white_wins + white_draws + white_losses
    white_win_rate = round(white_wins / white_decided, 3) if white_decided > 0 else 0.0
    black_decided = black_wins + black_draws + black_losses
    black_win_rate = round(black_wins / black_decided, 3) if black_decided > 0 else 0.0

    # Analyzed moves & metrics (ONLY for player's moves in analyzed games)
    analyzed_metrics: Optional[Dict[str, Any]] = None
    if player_analyzed_games:
        total_hero_moves = 0
        total_hero_cpl = 0
        hero_cpl_count = 0
        blunders_count = 0
        mistakes_count = 0
        inaccuracies_count = 0
        good_moves_count = 0

        for g in player_analyzed_games:
            analysis = library.get_analysis(g.game_id)
            if not analysis:
                continue
            is_white = target_player.lower() in g.white.lower()
            hero_color = "White" if is_white else "Black"

            for m in analysis.get("moves", []):
                if m.get("color", "").lower() != hero_color.lower():
                    continue
                total_hero_moves += 1
                cpl = m.get("centipawn_loss")
                if cpl is not None:
                    total_hero_cpl += cpl
                    hero_cpl_count += 1
                    if cpl >= 300:
                        blunders_count += 1
                    elif cpl >= 150:
                        mistakes_count += 1
                    elif cpl >= 75:
                        inaccuracies_count += 1
                    else:
                        good_moves_count += 1

        avg_hero_cpl = round(total_hero_cpl / hero_cpl_count, 1) if hero_cpl_count > 0 else 0.0

        analyzed_metrics = {
            "games_analyzed": len(player_analyzed_games),
            "moves_analyzed": total_hero_moves,
            "average_cpl": avg_hero_cpl,
            "classifications": {
                "blunder": blunders_count,
                "mistake": mistakes_count,
                "inaccuracy": inaccuracies_count,
                "good": good_moves_count,
            },
        }

    # Chronological Performance Trends
    # Sort games by date ascending (or imported_at fallback)
    sorted_player_games = sorted(
        player_games,
        key=lambda g: (g.date or "1970.01.01", g.imported_at or ""),
    )

    trends: List[Dict[str, Any]] = []
    # Map analyzed game indices (1..N) to Game objects for recommendation move resolution
    # build_coaching_profile_from_library lists games in library.list_games(analysis_status="ANALYZED") order
    analyzed_games_in_order = library.list_games(analysis_status="ANALYZED")
    analyzed_game_index_map: Dict[int, Game] = {
        idx: g for idx, g in enumerate(analyzed_games_in_order, start=1)
    }

    # Cache analyses for quick lookup
    analysis_cache: Dict[str, Dict[str, Any]] = {}
    for g in player_analyzed_games:
        a = library.get_analysis(g.game_id)
        if a:
            analysis_cache[g.game_id] = a

    for g in sorted_player_games:
        is_white = target_player.lower() in g.white.lower()
        hero_color = "White" if is_white else "Black"
        outcome = _parse_game_result_for_color(g.result, hero_color)

        opening_data = library.get_game_opening(g.game_id, auto_detect=False)
        eco = (opening_data.get("eco") if opening_data else None) or g.eco or "A00"
        opening_name = (opening_data.get("opening_name") if opening_data else None) or "Unknown Opening"
        variation_name = opening_data.get("variation_name") if opening_data else None

        trend_item: Dict[str, Any] = {
            "game_id": g.game_id,
            "date": g.date or "Unknown Date",
            "color": hero_color,
            "opponent": g.black if is_white else g.white,
            "result": outcome,
            "raw_result": g.result,
            "player_rating": g.white_rating if is_white else g.black_rating,
            "opponent_rating": g.black_rating if is_white else g.white_rating,
            "eco": eco,
            "opening_name": opening_name,
            "variation_name": variation_name,
            "is_analyzed": g.analysis_status == "ANALYZED",
            "avg_cpl": None,
            "blunders": None,
            "mistakes": None,
            "inaccuracies": None,
            "moves_count": None,
        }

        if g.game_id in analysis_cache:
            a = analysis_cache[g.game_id]
            hero_moves = [
                m for m in a.get("moves", [])
                if m.get("color", "").lower() == hero_color.lower()
            ]
            cpls = [
                m.get("centipawn_loss", 0) for m in hero_moves
                if m.get("centipawn_loss") is not None
            ]
            if cpls:
                trend_item["avg_cpl"] = round(sum(cpls) / len(cpls), 1)
            trend_item["blunders"] = sum(1 for c in cpls if c >= 300)
            trend_item["mistakes"] = sum(1 for c in cpls if 150 <= c < 300)
            trend_item["inaccuracies"] = sum(1 for c in cpls if 75 <= c < 150)
            trend_item["moves_count"] = len(hero_moves)

        trends.append(trend_item)

    # Coaching Profile & Recommendations
    coaching_profile: Optional[CoachingProfile] = None
    try:
        coaching_profile = build_coaching_profile_from_library(library, target_player)
    except Exception:
        coaching_profile = None

    # Player Profile for raw weaknesses & summaries
    player_profile: Optional[PlayerProfile] = None
    try:
        player_profile = build_player_profile_from_library(library, target_player)
    except Exception:
        player_profile = None

    # Enrich recommendations with clickable links to game review (game_id, ply)
    recommendations_list: List[Dict[str, Any]] = []
    if coaching_profile:
        for rec in coaching_profile.top_recommendations:
            enriched_moves = []
            for sm in rec.affected_moves:
                raw_game_idx = sm.get("game_id")
                target_game = analyzed_game_index_map.get(raw_game_idx) if isinstance(raw_game_idx, int) else None
                real_game_id = target_game.game_id if target_game else str(raw_game_idx)
                
                # Determine ply and color for move
                move_str = sm.get("move", "")
                move_num = 1
                try:
                    if "." in move_str:
                        move_num = int(move_str.split(".")[0].strip())
                except Exception:
                    pass

                # Resolve hero color for target game
                target_hero_color = "Black"
                if target_game and target_player.lower() in target_game.white.lower():
                    target_hero_color = "White"

                # Find exact ply
                ply = None
                if target_game and target_game.game_id in analysis_cache:
                    ply = _find_move_ply(
                        analysis_cache[target_game.game_id],
                        target_hero_color,
                        move_num,
                        sm.get("played_move", ""),
                    )
                if ply is None:
                    ply = (move_num - 1) * 2 + (1 if target_hero_color == "White" else 2)

                enriched_moves.append({
                    "game_id": real_game_id,
                    "game_date": target_game.date if target_game else None,
                    "opponent": (target_game.black if target_hero_color == "White" else target_game.white) if target_game else None,
                    "ply": ply,
                    "move_number": move_num,
                    "move_text": move_str,
                    "played_move": sm.get("played_move"),
                    "best_move": sm.get("best_move"),
                    "cpl": sm.get("cpl"),
                    "summary": sm.get("summary"),
                })

            # Map affected games list to real game IDs
            mapped_affected_games = []
            for g_ref in rec.affected_games:
                if isinstance(g_ref, int) and g_ref in analyzed_game_index_map:
                    g_obj = analyzed_game_index_map[g_ref]
                    mapped_affected_games.append({
                        "game_id": g_obj.game_id,
                        "date": g_obj.date,
                        "white": g_obj.white,
                        "black": g_obj.black,
                        "result": g_obj.result,
                    })
                else:
                    mapped_affected_games.append({"game_id": str(g_ref)})

            rec_dict = rec.to_dict()
            rec_dict["affected_moves"] = enriched_moves
            rec_dict["affected_games_detail"] = mapped_affected_games
            recommendations_list.append(rec_dict)

    # Enrich Strengths
    strengths_list: List[Dict[str, Any]] = []
    if coaching_profile and coaching_profile.strengths:
        for s in coaching_profile.strengths:
            enriched_evidence = []
            for ev in s.get("sample_evidence", []):
                ev_copy = dict(ev)
                raw_gid = ev.get("game_id")
                if isinstance(raw_gid, int) and raw_gid in analyzed_game_index_map:
                    ev_copy["real_game_id"] = analyzed_game_index_map[raw_gid].game_id
                enriched_evidence.append(ev_copy)
            
            s_copy = dict(s)
            s_copy["sample_evidence"] = enriched_evidence
            strengths_list.append(s_copy)

    # Repertoire & Opening Statistics
    repertoire_entries: List[Dict[str, Any]] = []
    try:
        rep_profile = build_player_repertoire_from_library(library, target_player)
        for entry in rep_profile.opening_statistics:
            e_dict = entry.to_dict()
            total_g = entry.total_games
            is_preliminary = total_g < 3
            e_dict["is_preliminary"] = is_preliminary
            e_dict["sample_label"] = (
                f"Preliminary ({total_g} game)" if total_g == 1 else (
                    f"Preliminary ({total_g} games)" if total_g < 3 else f"{total_g} games"
                )
            )
            repertoire_entries.append(e_dict)
    except Exception:
        repertoire_entries = []

    # Methodological limitations and sample size notice
    analyzed_count = len(player_analyzed_games)
    conf_level = "preliminary"
    if analyzed_count >= 10:
        conf_level = "high"
    elif analyzed_count >= 4:
        conf_level = "moderate"

    limitations = [
        f"Analyzed dataset contains {analyzed_count} games for {target_player}. Early evidence indicates preliminary patterns rather than definitive rating assessments.",
        "Centipawn loss reflects move-by-move engine evaluation drops, not overall playing strength or human tactical complexity.",
        "Positional evaluations are inferred from Stockfish evaluation swings rather than human pawn-structure rules.",
        "Openings appearing in only 1–2 games are labeled as preliminary; no repertoire alterations are recommended without recurring evidence.",
    ]

    return {
        "player_name": target_player,
        "overview": {
            "total_library_games": total_library_games,
            "library_analyzed_games": library_analyzed_count,
            "library_unanalyzed_games": library_unanalyzed_count,
            "player_games_total": len(player_games),
            "player_games_analyzed": analyzed_count,
            "player_games_unanalyzed": len(player_unanalyzed_games),
            "results": {
                "wins": total_wins,
                "draws": total_draws,
                "losses": total_losses,
                "unknown": unknown_results,
                "win_rate": overall_win_rate,
            },
            "white_stats": {
                "games": len([g for g in player_games if target_player.lower() in g.white.lower()]),
                "wins": white_wins,
                "draws": white_draws,
                "losses": white_losses,
                "win_rate": white_win_rate,
            },
            "black_stats": {
                "games": len([g for g in player_games if target_player.lower() in g.black.lower()]),
                "wins": black_wins,
                "draws": black_draws,
                "losses": black_losses,
                "win_rate": black_win_rate,
            },
            "analyzed_metrics": analyzed_metrics,
        },
        "trends": trends,
        "weaknesses": [w.to_dict() for w in player_profile.weaknesses] if player_profile else [],
        "strengths": strengths_list,
        "recommendations": recommendations_list,
        "openings": repertoire_entries,
        "confidence": {
            "level": conf_level,
            "sample_size": analyzed_count,
            "label": f"{conf_level.capitalize()} ({analyzed_count} analyzed games)",
            "warning": (
                "Statistical sample is small. Observations are preliminary."
                if analyzed_count < 10 else "Sufficient sample size for recurring pattern identification."
            ),
        },
        "limitations": limitations,
    }
