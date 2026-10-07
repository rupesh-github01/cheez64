"""
Player Weakness & Strength Profiler Module.

Aggregates move-level analysis, tactical evidence, and explanations across multiple
games into a structured profile of recurring strengths and weaknesses.
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Set
import json
import math


SCORING_FORMULA_DOC = (
    "Score = EpisodeOccurrences * (1.0 + 2.0 * CrossGameRecurrence) * "
    "min(3.0, 0.5 + AvgCPL / 150.0) * ConfidenceMultiplier. "
    "CrossGameRecurrence is (distinct_games / total_games). "
    "Episode occurrences deduplicate clustered moves in the same episode."
)


@dataclass
class ProfileWeakness:
    """A ranked weakness identified from recurring evidence across games."""
    category: str
    pillar: str  # "Tactical Awareness", "Strategic & Positional", "Endgame Technique"
    subcategories: List[str]
    mistake_type: str  # "tactical_blunder", "missed_opportunity", "positional_inaccuracy", "endgame_technique"
    occurrences: int
    episode_occurrences: int
    distinct_games: int
    game_ids: List[int]
    games_ratio: str
    average_cpl: float
    max_cpl: int
    total_cpl: int
    high_confidence_count: int
    severity: str  # "high", "medium", "low"
    confidence: str  # "high", "medium", "low"
    composite_score: float
    sample_moves: List[Dict[str, Any]]
    coaching_takeaway: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProfileStrength:
    """A preliminary strength grounded in positive evidence across games."""
    area: str
    evidence_count: int
    distinct_games: int
    games_ratio: str
    description: str
    sample_evidence: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TacticalSummary:
    """Aggregated tactical statistics."""
    total_tactical_errors: int
    blunders_count: int
    missed_opportunities_count: int
    hanging_pieces_count: int
    material_loss_count: int
    average_tactical_cpl: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MaterialSummary:
    """Aggregated material impact summary."""
    total_net_points_conceded: int
    unreciprocated_losses_count: int
    missed_material_points: int
    pieces_lost_breakdown: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PositionalSummary:
    """Aggregated positional evaluation concessions."""
    positional_errors_count: int
    average_positional_cpl: float
    opening_errors_count: int
    middlegame_errors_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EndgameSummary:
    """Aggregated endgame statistics and patterns."""
    endgame_moves_count: int
    endgame_critical_count: int
    king_activity_errors_count: int
    pawn_endgame_errors_count: int
    endgame_piece_errors_count: int
    average_endgame_cpl: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SeveritySummary:
    """Breakdown of critical move severities."""
    inaccuracies_count: int  # 75 - 199 CPL
    mistakes_count: int      # 200 - 299 CPL
    blunders_count: int      # >= 300 CPL
    peak_cpl_move: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProfileConfidence:
    """Confidence assessment for the generated profile."""
    level: str  # "preliminary", "moderate", "high"
    games_analyzed: int
    moves_analyzed: int
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlayerProfile:
    """Complete aggregated player profile."""
    player_name: str
    games_analyzed: int
    moves_analyzed: int
    critical_positions: int
    episodes_count: int
    overall_average_cpl: float
    strengths: List[ProfileStrength]
    weaknesses: List[ProfileWeakness]
    tactical_summary: TacticalSummary
    material_summary: MaterialSummary
    positional_summary: PositionalSummary
    endgame_summary: EndgameSummary
    severity_summary: SeveritySummary
    confidence: ProfileConfidence
    scoring_formula: str = SCORING_FORMULA_DOC

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_name": self.player_name,
            "games_analyzed": self.games_analyzed,
            "moves_analyzed": self.moves_analyzed,
            "critical_positions": self.critical_positions,
            "episodes_count": self.episodes_count,
            "overall_average_cpl": round(self.overall_average_cpl, 2),
            "strengths": [s.to_dict() for s in self.strengths],
            "weaknesses": [w.to_dict() for w in self.weaknesses],
            "tactical_summary": self.tactical_summary.to_dict(),
            "material_summary": self.material_summary.to_dict(),
            "positional_summary": self.positional_summary.to_dict(),
            "endgame_summary": self.endgame_summary.to_dict(),
            "severity_summary": self.severity_summary.to_dict(),
            "confidence": self.confidence.to_dict(),
            "scoring_formula": self.scoring_formula,
        }


def is_endgame_position(pos_features: Optional[Dict[str, Any]]) -> bool:
    """
    Check if position is in the endgame based on queens and total non-king material.
    """
    if not pos_features:
        return False
    piece_counts = pos_features.get("piece_counts", {})
    w_queens = piece_counts.get("white", {}).get("queen", 0)
    b_queens = piece_counts.get("black", {}).get("queen", 0)
    if w_queens == 0 and b_queens == 0:
        return True
    material = pos_features.get("material", {})
    total_mat = material.get("white", 0) + material.get("black", 0)
    return total_mat <= 26


def auto_detect_target_player(game_analyses: List[Dict[str, Any]]) -> Optional[str]:
    """
    Auto-detect the most common player across the analyzed games.
    """
    player_counts: Dict[str, int] = {}
    for game in game_analyses:
        meta = game.get("metadata", {})
        w = meta.get("white")
        b = meta.get("black")
        if w:
            player_counts[w] = player_counts.get(w, 0) + 1
        if b:
            player_counts[b] = player_counts.get(b, 0) + 1

    if not player_counts:
        return None
    # Pick player with maximum appearances
    best_player, count = max(player_counts.items(), key=lambda item: item[1])
    return best_player


def classify_coaching_category(move: Dict[str, Any]) -> Dict[str, str]:
    """
    Determine higher-level coaching category, pillar, and mistake type.
    """
    tf = move.get("tactical_finding") or {}
    cat = tf.get("category", "unclassified")
    pf = move.get("position_features") or {}
    is_eg = is_endgame_position(pf)
    played = move.get("played_move", "")
    best = move.get("best_move", "")

    if cat == "material_loss":
        return {
            "category": "Tactical Awareness: Material Loss",
            "pillar": "Tactical Awareness",
            "subcategory": "material_loss",
            "mistake_type": "tactical_blunder",
        }
    elif cat == "hanging_piece":
        return {
            "category": "Tactical Awareness: Hanging Pieces",
            "pillar": "Tactical Awareness",
            "subcategory": "hanging_piece",
            "mistake_type": "tactical_blunder",
        }
    elif cat == "missed_capture":
        return {
            "category": "Tactical Awareness: Missed Forcing Moves & Tactics",
            "pillar": "Tactical Awareness",
            "subcategory": "missed_capture",
            "mistake_type": "missed_opportunity",
        }
    elif cat == "missed_check_or_forcing_move":
        return {
            "category": "Tactical Awareness: Missed Forcing Moves & Tactics",
            "pillar": "Tactical Awareness",
            "subcategory": "missed_check_or_forcing_move",
            "mistake_type": "missed_opportunity",
        }
    elif is_eg:
        # Endgame specific categorization
        if played.startswith("K") or best.startswith("K"):
            return {
                "category": "Endgame Technique: King Activity & Opposition",
                "pillar": "Endgame Technique",
                "subcategory": "endgame_king_activity",
                "mistake_type": "endgame_technique",
            }
        elif len(played) >= 2 and played[0] in "abcdefgh":
            return {
                "category": "Endgame Technique: Pawn Endgame Play",
                "pillar": "Endgame Technique",
                "subcategory": "endgame_pawn_play",
                "mistake_type": "endgame_technique",
            }
        else:
            return {
                "category": "Endgame Technique: Endgame Piece Play",
                "pillar": "Endgame Technique",
                "subcategory": "endgame_piece_play",
                "mistake_type": "endgame_technique",
            }
    else:
        # Middlegame / Opening unclassified strategic concession
        return {
            "category": "Strategic & Positional Decisions",
            "pillar": "Strategic & Positional",
            "subcategory": "positional_evaluation_drop",
            "mistake_type": "positional_inaccuracy",
        }


def calculate_weakness_score(
    episode_occurrences: int,
    distinct_games: int,
    total_games: int,
    average_cpl: float,
    confidence_level: str
) -> float:
    """
    Calculate composite ranking score combining frequency, cross-game recurrence,
    severity, and confidence.
    """
    if total_games <= 0 or episode_occurrences <= 0:
        return 0.0

    # Cross-game recurrence factor (0.0 - 1.0)
    recurrence_ratio = distinct_games / total_games
    recurrence_multiplier = 1.0 + (2.0 * recurrence_ratio)

    # Severity factor based on average CPL
    severity_factor = min(3.0, max(0.5, 0.5 + (average_cpl / 150.0)))

    # Confidence multiplier
    conf_mult = 1.0 if confidence_level == "high" else (0.8 if confidence_level == "medium" else 0.6)

    score = episode_occurrences * recurrence_multiplier * severity_factor * conf_mult
    return round(score, 2)


def build_player_profile(
    game_analyses: List[Dict[str, Any]],
    target_player: Optional[str] = None
) -> PlayerProfile:
    """
    Build a comprehensive PlayerProfile by aggregating multiple analyzed games.
    """
    if not game_analyses:
        return PlayerProfile(
            player_name=target_player or "Unknown",
            games_analyzed=0,
            moves_analyzed=0,
            critical_positions=0,
            episodes_count=0,
            overall_average_cpl=0.0,
            strengths=[],
            weaknesses=[],
            tactical_summary=TacticalSummary(0, 0, 0, 0, 0, 0.0),
            material_summary=MaterialSummary(0, 0, 0, {}),
            positional_summary=PositionalSummary(0, 0.0, 0, 0),
            endgame_summary=EndgameSummary(0, 0, 0, 0, 0, 0.0),
            severity_summary=SeveritySummary(0, 0, 0, None),
            confidence=ProfileConfidence("low", 0, 0, "No games provided."),
        )

    # Auto-detect target player if not specified
    if not target_player:
        target_player = auto_detect_target_player(game_analyses) or "Analyzed Player"

    total_games = len(game_analyses)
    total_hero_moves = 0
    total_cpl_sum = 0
    hero_critical_moves: List[Dict[str, Any]] = []
    hero_all_moves: List[Dict[str, Any]] = []

    # Category aggregation map
    # category_name -> Dict of accumulated stats
    category_buckets: Dict[str, Dict[str, Any]] = {}

    # Strengths tracking metrics
    accurate_captures: List[Dict[str, Any]] = []
    accurate_checks: List[Dict[str, Any]] = []
    low_error_games: List[Dict[str, Any]] = []
    opening_errors = 0
    endgame_total_moves = 0
    endgame_critical_moves = 0
    pieces_lost_counts: Dict[str, int] = {}
    total_net_points_conceded = 0
    missed_material_points_total = 0

    peak_cpl_move_info = None
    max_observed_cpl = -1

    for game_idx, game_data in enumerate(game_analyses, start=1):
        meta = game_data.get("metadata", {})
        white_player = meta.get("white", "")
        black_player = meta.get("black", "")

        # Determine hero color in this game
        if target_player.lower() in white_player.lower():
            hero_color = "White"
        elif target_player.lower() in black_player.lower():
            hero_color = "Black"
        else:
            # If target player is not in headers, default to checking all moves if single player
            hero_color = "White" if game_idx % 2 != 0 else "Black"

        moves = game_data.get("moves", [])
        game_hero_moves = [m for m in moves if m.get("color") == hero_color]
        total_hero_moves += len(game_hero_moves)

        # Track game-level accuracy for strengths
        game_cpls = [m.get("centipawn_loss", 0) for m in game_hero_moves if m.get("centipawn_loss") is not None]
        game_avg_cpl = (sum(game_cpls) / len(game_cpls)) if game_cpls else 0
        game_crit_count = sum(1 for m in game_hero_moves if m.get("is_critical"))
        if game_avg_cpl < 20.0 and len(game_hero_moves) >= 15:
            low_error_games.append({
                "game_id": game_idx,
                "color": hero_color,
                "moves": len(game_hero_moves),
                "avg_cpl": round(game_avg_cpl, 1),
                "critical_count": game_crit_count,
            })

        # Process each hero move
        for m in game_hero_moves:
            hero_all_moves.append(m)
            cpl = m.get("centipawn_loss") or 0
            total_cpl_sum += cpl
            pf = m.get("position_features") or {}
            played = m.get("played_move", "")
            is_eg = is_endgame_position(pf)

            if is_eg:
                endgame_total_moves += 1

            # Check positive actions
            if cpl < 25:
                if "x" in played or played in pf.get("captures_available", []):
                    accurate_captures.append({
                        "game_id": game_idx,
                        "move": f"{m.get('move_number')}. {played}",
                        "cpl": cpl,
                    })
                if "+" in played or played in pf.get("checks_available", []):
                    accurate_checks.append({
                        "game_id": game_idx,
                        "move": f"{m.get('move_number')}. {played}",
                        "cpl": cpl,
                    })

            if not m.get("is_critical"):
                continue

            # Critical move processing
            hero_critical_moves.append(m)
            if is_eg:
                endgame_critical_moves += 1
            if m.get("move_number", 99) <= 10:
                opening_errors += 1

            if cpl > max_observed_cpl:
                max_observed_cpl = cpl
                peak_cpl_move_info = {
                    "game_id": game_idx,
                    "move_number": m.get("move_number"),
                    "color": m.get("color"),
                    "played_move": played,
                    "best_move": m.get("best_move"),
                    "cpl": cpl,
                    "summary": (m.get("explanation") or {}).get("summary", "")
                }

            cat_info = classify_coaching_category(m)
            cat_name = cat_info["category"]

            # Initialize category bucket if needed
            if cat_name not in category_buckets:
                category_buckets[cat_name] = {
                    "pillar": cat_info["pillar"],
                    "mistake_type": cat_info["mistake_type"],
                    "subcategories": set(),
                    "moves": [],
                    "game_ids": set(),
                    # Episode deduplication tracking: (game_id, episode_index)
                    "episodes_set": set(),
                    "cpl_list": [],
                    "high_conf_count": 0,
                }

            bucket = category_buckets[cat_name]
            bucket["subcategories"].add(cat_info["subcategory"])
            bucket["moves"].append(m)
            bucket["game_ids"].add(game_idx)
            bucket["cpl_list"].append(cpl)

            ep_idx = m.get("episode_index")
            # If no episode_index, use unique move identifier to avoid over-collapsing non-episode moves
            ep_key = (game_idx, ep_idx) if ep_idx is not None else (game_idx, f"move_{m.get('move_number')}")
            bucket["episodes_set"].add(ep_key)

            tf = m.get("tactical_finding") or {}
            if tf.get("confidence") == "high":
                bucket["high_conf_count"] += 1

            # Accumulate material details
            ev = tf.get("evidence", {})
            if cat_info["subcategory"] == "material_loss":
                total_net_points_conceded += ev.get("net_material_loss", 0)
                caps = ev.get("played_captures", [])
                for cap in caps:
                    p = cap.get("captured_piece")
                    if p:
                        pieces_lost_counts[p] = pieces_lost_counts.get(p, 0) + 1
            elif cat_info["subcategory"] in ("missed_capture", "missed_check_or_forcing_move"):
                missed_material_points_total += ev.get("net_material_gain", 0)

    # -------------------------------------------------------------
    # Build Ranked Weaknesses
    # -------------------------------------------------------------
    weaknesses: List[ProfileWeakness] = []
    for cat_name, b in category_buckets.items():
        occurrences = len(b["moves"])
        episode_occ = len(b["episodes_set"])
        distinct_games = len(b["game_ids"])
        cpl_list = b["cpl_list"]
        avg_cpl = (sum(cpl_list) / len(cpl_list)) if cpl_list else 0.0
        max_cpl = max(cpl_list) if cpl_list else 0
        total_cpl = sum(cpl_list)

        # Confidence assessment for this category
        if distinct_games >= 3 and b["high_conf_count"] >= 3:
            conf_str = "high"
        elif distinct_games >= 2 or occurrences >= 3:
            conf_str = "medium"
        else:
            conf_str = "low"

        # Severity
        if avg_cpl >= 300 or max_cpl >= 500:
            sev_str = "high"
        elif avg_cpl >= 150:
            sev_str = "medium"
        else:
            sev_str = "low"

        score = calculate_weakness_score(
            episode_occurrences=episode_occ,
            distinct_games=distinct_games,
            total_games=total_games,
            average_cpl=avg_cpl,
            confidence_level=conf_str
        )

        # Collect sample moves (up to 3 representative moves)
        samples = []
        for sm in b["moves"][:3]:
            exp = sm.get("explanation") or {}
            samples.append({
                "move": f"{sm.get('move_number')}. {sm.get('played_move')}",
                "best_move": sm.get("best_move"),
                "cpl": sm.get("centipawn_loss"),
                "summary": exp.get("summary", ""),
            })

        # Generate human takeaway
        ratio_str = f"{distinct_games}/{total_games}"
        if distinct_games >= total_games - 1 and total_games >= 3:
            freq_desc = f"Observed repeatedly across {distinct_games} of {total_games} games."
        elif distinct_games >= 2:
            freq_desc = f"Observed in {distinct_games} separate games."
        else:
            freq_desc = f"Observed primarily in a single game ({occurrences} instances)."

        if b["mistake_type"] == "tactical_blunder":
            takeaway = f"{freq_desc} The player repeatedly concedes material in complex positions."
        elif b["mistake_type"] == "missed_opportunity":
            takeaway = f"{freq_desc} The player frequently overlooks forcing candidate moves and tactical wins."
        elif b["mistake_type"] == "endgame_technique":
            takeaway = f"{freq_desc} Concessions in the endgame point to training needs in king activity and opposition."
        else:
            takeaway = f"{freq_desc} Substantial non-tactical evaluation drops indicate passive positional choices."

        weaknesses.append(ProfileWeakness(
            category=cat_name,
            pillar=b["pillar"],
            subcategories=sorted(list(b["subcategories"])),
            mistake_type=b["mistake_type"],
            occurrences=occurrences,
            episode_occurrences=episode_occ,
            distinct_games=distinct_games,
            game_ids=sorted(list(b["game_ids"])),
            games_ratio=ratio_str,
            average_cpl=round(avg_cpl, 1),
            max_cpl=max_cpl,
            total_cpl=total_cpl,
            high_confidence_count=b["high_conf_count"],
            severity=sev_str,
            confidence=conf_str,
            composite_score=score,
            sample_moves=samples,
            coaching_takeaway=takeaway
        ))

    # Sort weaknesses descending by composite score
    weaknesses.sort(key=lambda w: w.composite_score, reverse=True)

    # -------------------------------------------------------------
    # Build Preliminary Strengths
    # -------------------------------------------------------------
    strengths: List[ProfileStrength] = []

    # Strength 1: Tactical Execution (Accurate Captures)
    if len(accurate_captures) >= 5:
        cap_games = len(set(c["game_id"] for c in accurate_captures))
        strengths.append(ProfileStrength(
            area="Tactical Execution: Direct Material Captures",
            evidence_count=len(accurate_captures),
            distinct_games=cap_games,
            games_ratio=f"{cap_games}/{total_games}",
            description=(
                f"Successfully executed {len(accurate_captures)} tactical captures with near-zero "
                f"centipawn loss across {cap_games} games. When direct tactical targets are identified, "
                "conversion is highly reliable."
            ),
            sample_evidence=accurate_captures[:4]
        ))

    # Strength 2: Forcing Move Execution (Checks)
    if len(accurate_checks) >= 3:
        chk_games = len(set(c["game_id"] for c in accurate_checks))
        strengths.append(ProfileStrength(
            area="Forcing Move Execution (Checks & Attacks)",
            evidence_count=len(accurate_checks),
            distinct_games=chk_games,
            games_ratio=f"{chk_games}/{total_games}",
            description=(
                f"Delivered {len(accurate_checks)} accurate checks that maintained initiative or forced "
                f"mating sequences across {chk_games} games."
            ),
            sample_evidence=accurate_checks[:3]
        ))

    # Strength 3: High-Accuracy Capability (Peak Game Performance)
    if low_error_games:
        strengths.append(ProfileStrength(
            area="High-Accuracy Baseline in Structured Positions",
            evidence_count=len(low_error_games),
            distinct_games=len(low_error_games),
            games_ratio=f"{len(low_error_games)}/{total_games}",
            description=(
                f"Demonstrated master-level precision in clean games (e.g., Game {low_error_games[0]['game_id']} "
                f"with {low_error_games[0]['avg_cpl']} average CPL and {low_error_games[0]['critical_count']} critical positions), "
                "proving strong baseline capability when avoiding wild tactical complications."
            ),
            sample_evidence=low_error_games
        ))

    # Strength 4: Opening Stability
    if total_games >= 3 and opening_errors <= 3:
        strengths.append(ProfileStrength(
            area="Early-Game Stability (Moves 1–10)",
            evidence_count=total_games - opening_errors,
            distinct_games=total_games,
            games_ratio=f"{total_games}/{total_games}",
            description=(
                f"Maintained solid opening play with only {opening_errors} critical inaccuracies across "
                f"all {total_games} games in moves 1–10, indicating stable opening fundamentals."
            ),
            sample_evidence=[{"opening_critical_count": opening_errors, "games_analyzed": total_games}]
        ))

    # -------------------------------------------------------------
    # Specialized Summaries
    # -------------------------------------------------------------
    tactical_errors = [m for m in hero_critical_moves if (m.get("tactical_finding") or {}).get("category") in (
        "material_loss", "hanging_piece", "missed_capture", "missed_check_or_forcing_move"
    )]
    tactical_cpls = [m.get("centipawn_loss", 0) for m in tactical_errors]
    avg_tac_cpl = (sum(tactical_cpls) / len(tactical_cpls)) if tactical_cpls else 0.0

    blunders = sum(1 for m in tactical_errors if (m.get("tactical_finding") or {}).get("category") in ("material_loss", "hanging_piece"))
    missed_opps = sum(1 for m in tactical_errors if (m.get("tactical_finding") or {}).get("category") in ("missed_capture", "missed_check_or_forcing_move"))
    hanging = sum(1 for m in tactical_errors if (m.get("tactical_finding") or {}).get("category") == "hanging_piece")
    mat_loss = sum(1 for m in tactical_errors if (m.get("tactical_finding") or {}).get("category") == "material_loss")

    tactical_summary = TacticalSummary(
        total_tactical_errors=len(tactical_errors),
        blunders_count=blunders,
        missed_opportunities_count=missed_opps,
        hanging_pieces_count=hanging,
        material_loss_count=mat_loss,
        average_tactical_cpl=round(avg_tac_cpl, 1)
    )

    material_summary = MaterialSummary(
        total_net_points_conceded=total_net_points_conceded,
        unreciprocated_losses_count=sum(pieces_lost_counts.values()),
        missed_material_points=missed_material_points_total,
        pieces_lost_breakdown=pieces_lost_counts
    )

    pos_errors = [m for m in hero_critical_moves if (m.get("tactical_finding") or {}).get("category") == "unclassified" and not is_endgame_position(m.get("position_features"))]
    pos_cpls = [m.get("centipawn_loss", 0) for m in pos_errors]
    avg_pos_cpl = (sum(pos_cpls) / len(pos_cpls)) if pos_cpls else 0.0

    positional_summary = PositionalSummary(
        positional_errors_count=len(pos_errors),
        average_positional_cpl=round(avg_pos_cpl, 1),
        opening_errors_count=opening_errors,
        middlegame_errors_count=len(pos_errors) - opening_errors
    )

    eg_errors = [m for m in hero_critical_moves if is_endgame_position(m.get("position_features"))]
    eg_cpls = [m.get("centipawn_loss", 0) for m in eg_errors]
    avg_eg_cpl = (sum(eg_cpls) / len(eg_cpls)) if eg_cpls else 0.0

    king_errors = sum(1 for m in eg_errors if m.get("played_move", "").startswith("K") or m.get("best_move", "").startswith("K"))
    pawn_errors = sum(1 for m in eg_errors if len(m.get("played_move", "")) >= 2 and m.get("played_move", "")[0] in "abcdefgh")
    piece_errors = len(eg_errors) - (king_errors + pawn_errors)

    endgame_summary = EndgameSummary(
        endgame_moves_count=endgame_total_moves,
        endgame_critical_count=len(eg_errors),
        king_activity_errors_count=king_errors,
        pawn_endgame_errors_count=pawn_errors,
        endgame_piece_errors_count=piece_errors,
        average_endgame_cpl=round(avg_eg_cpl, 1)
    )

    # Severity Summary
    inacc = sum(1 for m in hero_critical_moves if 75 <= (m.get("centipawn_loss") or 0) < 200)
    mist = sum(1 for m in hero_critical_moves if 200 <= (m.get("centipawn_loss") or 0) < 300)
    blund = sum(1 for m in hero_critical_moves if (m.get("centipawn_loss") or 0) >= 300)

    severity_summary = SeveritySummary(
        inaccuracies_count=inacc,
        mistakes_count=mist,
        blunders_count=blund,
        peak_cpl_move=peak_cpl_move_info
    )

    # Confidence assessment
    if total_games >= 15:
        conf_level = "high"
        conf_rationale = f"High confidence based on comprehensive sample of {total_games} games ({total_hero_moves} moves)."
    elif total_games >= 7:
        conf_level = "moderate"
        conf_rationale = f"Moderate confidence based on {total_games} games ({total_hero_moves} moves). Patterns are recurring."
    else:
        conf_level = "preliminary"
        conf_rationale = (
            f"Preliminary — based on {total_games} games ({total_hero_moves} moves analyzed). "
            "Patterns represent initial coaching trends rather than permanent diagnoses."
        )

    profile_conf = ProfileConfidence(
        level=conf_level,
        games_analyzed=total_games,
        moves_analyzed=total_hero_moves,
        rationale=conf_rationale
    )

    overall_avg = (total_cpl_sum / total_hero_moves) if total_hero_moves else 0.0

    return PlayerProfile(
        player_name=target_player,
        games_analyzed=total_games,
        moves_analyzed=total_hero_moves,
        critical_positions=len(hero_critical_moves),
        episodes_count=len(set((m.get("game_id", 1), m.get("episode_index")) for m in hero_critical_moves if m.get("episode_index") is not None)),
        overall_average_cpl=overall_avg,
        strengths=strengths,
        weaknesses=weaknesses,
        tactical_summary=tactical_summary,
        material_summary=material_summary,
        positional_summary=positional_summary,
        endgame_summary=endgame_summary,
        severity_summary=severity_summary,
        confidence=profile_conf,
    )


def generate_human_readable_profile(profile: PlayerProfile) -> str:
    """
    Format a concise, executive human-readable profile summary.
    """
    lines = []
    lines.append("=" * 80)
    lines.append(f"PLAYER PROFILE: {profile.player_name}")
    lines.append(f"Status: {profile.confidence.rationale}")
    lines.append(f"Games Analyzed: {profile.games_analyzed} | Moves Analyzed: {profile.moves_analyzed} | Critical Moves: {profile.critical_positions} | Average CPL: {profile.overall_average_cpl:.1f}")
    lines.append("=" * 80)

    # Executive Overview
    lines.append("\nEXECUTIVE SUMMARY")
    lines.append("-" * 40)
    if profile.weaknesses:
        top_weakness = profile.weaknesses[0].category
        lines.append(f"Early evidence suggests that {top_weakness} is the primary area requiring")
        lines.append(f"targeted training, observed repeatedly across {profile.weaknesses[0].games_ratio} games.")
        if len(profile.weaknesses) > 1:
            second = profile.weaknesses[1].category
            lines.append(f"Secondary areas of concern include {second}.")
    else:
        lines.append("No recurring critical weaknesses were detected in the analyzed sample.")

    if profile.strengths:
        lines.append(f"On the positive side, {profile.strengths[0].area} represents a strong baseline.")

    # Ranked Weaknesses
    lines.append("\nRANKED TRAINING PRIORITIES (WEAKNESSES)")
    lines.append("-" * 40)
    for idx, w in enumerate(profile.weaknesses, start=1):
        lines.append(f"\n{idx}. {w.category} [Score: {w.composite_score:.1f}]")
        lines.append(f"   • Evidence: {w.episode_occurrences} episodes ({w.occurrences} moves) across {w.games_ratio} games")
        lines.append(f"   • Severity: {w.severity.upper()} (Avg CPL: {w.average_cpl:.1f}, Peak CPL: {w.max_cpl})")
        lines.append(f"   • Confidence: {w.confidence.upper()}")
        lines.append(f"   • Coaching Takeaway: {w.coaching_takeaway}")
        if w.sample_moves:
            sample_strs = [f"{s['move']} (best: {s['best_move']}, -{s['cpl']} cp)" for s in w.sample_moves[:2]]
            lines.append(f"   • Representative Instances: {'; '.join(sample_strs)}")

    # Demonstrated Strengths
    lines.append("\nDEMONSTRATED STRENGTHS")
    lines.append("-" * 40)
    for idx, s in enumerate(profile.strengths, start=1):
        lines.append(f"\n{idx}. {s.area}")
        lines.append(f"   • Evidence: Observed in {s.games_ratio} games ({s.evidence_count} verified instances)")
        lines.append(f"   • Details: {s.description}")

    # Priority Recommendations
    lines.append("\nRECOMMENDED FOCUS AREAS")
    lines.append("-" * 40)
    if profile.weaknesses:
        for idx, w in enumerate(profile.weaknesses[:3], start=1):
            if "Material Loss" in w.category:
                rec = "Practice tactical defense and exchange calculations to prevent tactical piece loss in complex positions."
            elif "Forcing Moves" in w.category:
                rec = "Build calculation discipline for candidate checks and forcing tactical captures before routine moves."
            elif "King Activity" in w.category:
                rec = "Study endgame king paths and opposition rules in king and pawn endgames."
            elif "Positional" in w.category:
                rec = "Work on middlegame planning and avoiding passive piece retreats that surrender central squares."
            elif "Hanging Pieces" in w.category:
                rec = "Develop a habitual blunder check for piece undefendedness before confirming moves."
            else:
                rec = f"Focus targeted study on {w.category}."
            lines.append(f"{idx}. {rec}")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


def load_game_analyses(file_paths: List[str]) -> List[Dict[str, Any]]:
    """
    Load game analysis JSON files from disk.
    """
    games = []
    for path in file_paths:
        with open(path, "r", encoding="utf-8") as f:
            games.append(json.load(f))
    return games


def export_profile_to_json(profile: PlayerProfile, output_path: str) -> None:
    """
    Export player profile to a formatted JSON file.
    """
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(profile.to_dict(), f, indent=2)
