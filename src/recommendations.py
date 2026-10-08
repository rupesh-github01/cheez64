"""
Personalized Coaching Recommendation Engine.

Converts multi-game move evidence, tactical classifications, episode clusters,
material accounting, and opening repertoire performance into a prioritized,
defensible training plan without generative LLM hallucination.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Union
import json
import math
import sys

# Ensure repository root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.profile import PlayerProfile, ProfileWeakness, ProfileStrength, build_player_profile
from src.openings import RepertoireProfile, RepertoireEntry, build_player_repertoire


SCORING_FORMULA_EXPLANATION = (
    "Recommendation Priority Score = EpisodeOccurrences * (1.0 + 2.5 * CrossGameRecurrence) * "
    "min(3.0, 0.5 + AvgCPL / 150.0 + 0.1 * PeakCPL / 300.0) * ConfidenceMultiplier. "
    "Episode occurrences cluster continuous tactical sequences into single units. "
    "CrossGameRecurrence is (distinct_games / total_games). Severe recurring mistakes across "
    "multiple games heavily outrank isolated trivial errors."
)

VALID_CATEGORIES = {
    "tactical_awareness",
    "material_management",
    "forcing_moves",
    "piece_safety",
    "positional_decision_making",
    "endgame",
    "opening_repertoire",
    "opening_transition",
}

TRAINING_TYPE_MAP = {
    "tactical_awareness": "tactical puzzles / calculation exercises",
    "material_management": "exchange calculation / material valuation drills",
    "forcing_moves": "checks-captures-threats exercises",
    "piece_safety": "blunder-check / hanging-piece exercises",
    "positional_decision_making": "middlegame strategy / pawn structure & piece activity study",
    "endgame": "endgame positions and conversion exercises",
    "opening_repertoire": "focused repertoire study",
    "opening_transition": "middlegame plans from recurring openings",
}

EPISODE_LABEL_MAP = {
    "tactical_awareness": "tactical episodes",
    "material_management": "material-loss episodes",
    "forcing_moves": "missed-forcing-move episodes",
    "piece_safety": "hanging-piece episodes",
    "positional_decision_making": "evaluation-drop episodes",
    "endgame": "endgame-critical episodes",
    "opening_repertoire": "opening episodes",
    "opening_transition": "opening-transition episodes",
}


@dataclass
class CoachingRecommendation:
    """
    A single evidence-grounded coaching recommendation with ranked priority.
    """
    recommendation_id: str
    priority: int
    category: str
    title: str
    summary: str
    rationale: str
    evidence: List[str]
    affected_games: List[Any]
    affected_moves: List[Dict[str, Any]]
    suggested_training_type: str
    confidence: str  # "high", "medium", "low"
    limitations: str
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CoachingProfile:
    """
    Comprehensive personalized coaching profile aggregating prioritized recommendations,
    demonstrated strengths, and methodological limitations.
    """
    target_player: str
    generated_at: str
    games_analyzed: int
    confidence: str  # "high", "medium", "low"
    top_recommendations: List[CoachingRecommendation]
    strengths: List[Dict[str, Any]]
    limitations: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_player": self.target_player,
            "generated_at": self.generated_at,
            "games_analyzed": self.games_analyzed,
            "confidence": self.confidence,
            "top_recommendations": [rec.to_dict() for rec in self.top_recommendations],
            "strengths": list(self.strengths),
            "limitations": list(self.limitations),
        }

    def summary(self) -> str:
        """
        Generate a concise, executive human-readable coaching summary.
        """
        lines = []
        lines.append("=" * 80)
        lines.append(f"PERSONALIZED COACHING RECOMMENDATIONS: {self.target_player}")
        lines.append(f"Status: {self.confidence.upper()} confidence based on {self.games_analyzed} analyzed games")
        lines.append("=" * 80)

        # Executive Takeaway
        lines.append("\nEXECUTIVE OVERVIEW")
        lines.append("-" * 40)
        if self.top_recommendations:
            top_rec = self.top_recommendations[0]
            lines.append(f"Primary Training Priority: {top_rec.title}")
            lines.append(f"Summary: {top_rec.summary}")
            lines.append(f"Suggested Training: {top_rec.suggested_training_type}")
        else:
            lines.append("No critical recurring weaknesses identified in the analyzed sample.")

        # Preserved Strengths
        if self.strengths:
            lines.append("\nDEMONSTRATED STRENGTHS TO BUILD UPON")
            lines.append("-" * 40)
            for idx, s in enumerate(self.strengths, start=1):
                lines.append(f"{idx}. {s.get('area', 'Strength')} (across {s.get('games_ratio', 'N/A')} games)")
                lines.append(f"   • {s.get('description', '')}")

        # Prioritized Recommendations
        lines.append("\nRANKED TRAINING PRIORITIES")
        lines.append("-" * 40)
        for rec in self.top_recommendations:
            lines.append(f"\nPriority {rec.priority}: {rec.title} [{rec.category}] (Score: {rec.score:.1f})")
            lines.append(f"• Summary: {rec.summary}")
            lines.append(f"• Rationale: {rec.rationale}")
            lines.append(f"• Suggested Training: {rec.suggested_training_type}")
            lines.append(f"• Confidence: {rec.confidence.upper()} ({rec.limitations})")
            lines.append("• Key Evidence:")
            for ev in rec.evidence:
                lines.append(f"    - {ev}")
            if rec.affected_moves:
                sample_strs = [
                    f"Game {m.get('game_id')}, Move {m.get('move')} ({m.get('played_move', '')} vs best {m.get('best_move', '')}, -{m.get('cpl', 0)} cp)"
                    for m in rec.affected_moves[:2]
                ]
                lines.append(f"• Example Positions: {'; '.join(sample_strs)}")

        # Methodological Limitations
        lines.append("\nDATA & METHODOLOGICAL LIMITATIONS")
        lines.append("-" * 40)
        for lim in self.limitations:
            lines.append(f"• {lim}")

        lines.append("\n" + "=" * 80)
        return "\n".join(lines)


def _categorize_weakness(weakness: ProfileWeakness) -> str:
    """
    Map ProfileWeakness to one of the canonical recommendation categories.
    """
    cat_lower = weakness.category.lower()
    sub_lower = " ".join(s.lower() for s in weakness.subcategories)
    pillar_lower = weakness.pillar.lower()

    if "hanging" in cat_lower or "hanging" in sub_lower:
        return "piece_safety"
    elif "forcing" in cat_lower or "forcing" in sub_lower or "missed_opportunity" in weakness.mistake_type:
        return "forcing_moves"
    elif "material loss" in cat_lower or "material" in sub_lower:
        return "tactical_awareness"
    elif "king activity" in cat_lower or "endgame" in pillar_lower or "endgame" in cat_lower:
        return "endgame"
    elif "positional" in cat_lower or "strategic" in pillar_lower or "strategic" in cat_lower:
        return "positional_decision_making"
    else:
        return "tactical_awareness"


def _compute_recommendation_score(
    episodes: int,
    distinct_games: int,
    total_games: int,
    avg_cpl: float,
    peak_cpl: int,
    confidence_level: str
) -> float:
    """
    Compute priority score using the documented scoring formula.
    """
    recurrence_ratio = (distinct_games / total_games) if total_games > 0 else 0.2
    recurrence_factor = 1.0 + 2.5 * recurrence_ratio
    severity_factor = min(3.0, max(0.5, 0.5 + (avg_cpl / 150.0) + 0.1 * (peak_cpl / 300.0)))

    conf_mult = 1.2 if confidence_level == "high" else (1.0 if confidence_level == "medium" else 0.8)
    score = episodes * recurrence_factor * severity_factor * conf_mult
    return round(score, 1)


def generate_coaching_recommendations(
    profile: PlayerProfile,
    repertoire: Optional[RepertoireProfile] = None,
    game_analyses: Optional[List[Dict[str, Any]]] = None,
) -> CoachingProfile:
    """
    Generate prioritized coaching recommendations from player profile, repertoire, and move evidence.
    """
    total_games = profile.games_analyzed
    target_player = profile.player_name
    now_iso = datetime.now(timezone.utc).isoformat()

    # Determine overall profile confidence
    if total_games >= 15:
        overall_conf = "high"
        conf_limitation = f"Based on {total_games} games. Represents a substantial statistical baseline."
    elif total_games >= 5:
        overall_conf = "medium"
        conf_limitation = f"Based on {total_games} games. Early evidence suggests clear preliminary trends."
    else:
        overall_conf = "low"
        conf_limitation = f"Based on only {total_games} games. Findings are preliminary indicators."

    recommendations: List[CoachingRecommendation] = []
    used_categories: Set[str] = set()

    # Check for player strengths to integrate strength-aware coaching
    has_clean_baseline = any("Clean Positions" in s.area or "Low-Error" in s.area for s in profile.strengths)

    # -------------------------------------------------------------
    # 1. Process Weaknesses from PlayerProfile
    # -------------------------------------------------------------
    # Group weaknesses by recommendation category to avoid duplicate
    # recommendations when multiple profile weaknesses map to the same category
    # (e.g., two endgame subcategories both mapping to "endgame").
    category_groups: Dict[str, List[ProfileWeakness]] = {}
    for w in profile.weaknesses:
        category = _categorize_weakness(w)
        if category not in VALID_CATEGORIES:
            continue
        if category not in category_groups:
            category_groups[category] = []
        category_groups[category].append(w)

    for category, weakness_group in category_groups.items():
        # Merge statistics across weaknesses in the same category
        merged_episodes = sum(w.episode_occurrences for w in weakness_group)
        merged_occurrences = sum(w.occurrences for w in weakness_group)
        merged_game_ids = sorted(set(gid for w in weakness_group for gid in w.game_ids))
        merged_distinct_games = len(merged_game_ids)
        merged_total_cpl = sum(w.total_cpl for w in weakness_group)
        merged_avg_cpl = (merged_total_cpl / merged_occurrences) if merged_occurrences > 0 else 0.0
        merged_max_cpl = max(w.max_cpl for w in weakness_group)
        merged_high_conf = sum(w.high_confidence_count for w in weakness_group)
        games_ratio = f"{merged_distinct_games}/{total_games}"

        # Use strongest confidence from constituent weaknesses
        conf_levels = [w.confidence for w in weakness_group]
        if "high" in conf_levels:
            merged_conf = "high"
        elif "medium" in conf_levels:
            merged_conf = "medium"
        else:
            merged_conf = "low"

        # Collect sample moves (up to 3) from all constituent weaknesses
        merged_samples: List[Dict[str, Any]] = []
        for w in weakness_group:
            merged_samples.extend(w.sample_moves)
        merged_samples = merged_samples[:3]

        score = _compute_recommendation_score(
            episodes=merged_episodes,
            distinct_games=merged_distinct_games,
            total_games=total_games,
            avg_cpl=merged_avg_cpl,
            peak_cpl=merged_max_cpl,
            confidence_level=merged_conf,
        )

        # Category-aware episode label
        episode_label = EPISODE_LABEL_MAP.get(category, "episodes")

        # Contextual titles, rationales, and summaries
        if category == "tactical_awareness":
            title = "Defensive Tactical Awareness & Material Retention"
            summary = (
                f"Tactical blunders conceding significant material occurred across {games_ratio} games "
                f"({merged_episodes} distinct episodes). Strengthening tactical defense will immediately improve results."
            )
            rationale = (
                "Material-loss episodes represent the most severe evaluation drops in the player's games. "
                "In complex positions, candidate defensive moves and opponent replies are overlooked, leading to unreciprocated piece loss."
            )
            if has_clean_baseline:
                rationale += " Given the player's solid baseline in uncomplicated positions, practice should emphasize complex middlegame transitions where board tension peaks."

        elif category == "piece_safety":
            title = "Piece Safety & Undefended Blunder Checks"
            summary = (
                f"Hanging or undefended pieces were conceded across {games_ratio} games. "
                "Implementing a habitual pre-move safety verification will eliminate avoidable piece giveaways."
            )
            rationale = (
                "Several severe errors involved leaving pieces with zero defenders or failing to recognize an opponent attack. "
                "Establishing a consistent blunder-checking checkpoint before finalizing moves is the most efficient safeguard."
            )

        elif category == "forcing_moves":
            title = "Calculation Discipline for Forcing Candidate Moves"
            summary = (
                f"Missed forcing tactical opportunities recurred across {games_ratio} games. "
                "Calculating checks, captures, and threats first will convert winning chances."
            )
            rationale = (
                "The player frequently defaulted to routine moves when concrete forcing moves (checks or captures) "
                "were available to win material or establish a decisive advantage."
            )

        elif category == "endgame":
            title = "Endgame Phase Accuracy"
            summary = (
                f"Evaluation drops were detected in endgame-phase positions across {games_ratio} games "
                f"({merged_episodes} distinct episodes). Improving accuracy in simplified positions will prevent avoidable losses."
            )
            rationale = (
                "In positions with queens off the board or low total material, the player's moves produced "
                "significant engine evaluation drops. These represent inaccuracies during piece and pawn "
                "maneuvering in simplified positions. Note: specific endgame concepts (opposition, key squares) "
                "are not computed — these findings reflect general CPL-based evaluation swings in the endgame phase."
            )

        elif category == "positional_decision_making":
            title = "Middlegame Planning & Strategic Pawn Play"
            summary = (
                f"Gradual positional evaluation concessions (>75 CPL) were observed across {games_ratio} games. "
                "Formulating concrete middlegame plans and maintaining active piece coordination will sustain opening advantages."
            )
            rationale = (
                "These evaluation drops did not involve immediate material loss, but rather passive piece retreats "
                "and conceding central outposts. Note: positional evaluations are inferred from engine swings rather than human pawn-structure rules."
            )
        else:
            title = f"Targeted Improvement in {category}"
            summary = f"Observed across {games_ratio} games with an average centipawn loss of {merged_avg_cpl:.1f}."
            rationale = weakness_group[0].coaching_takeaway

        # Build concrete evidence bullet points with category-aware labels
        evidence_points = [
            f"{merged_episodes} {episode_label} ({merged_occurrences} critical moves) across {games_ratio} games.",
            f"Mean centipawn loss of {merged_avg_cpl:.1f} cp (peak loss: {merged_max_cpl} cp, cumulative: {merged_total_cpl} cp).",
        ]
        if merged_high_conf > 0:
            evidence_points.append(f"{merged_high_conf} moves confirmed with high tactical confidence.")
        if category == "tactical_awareness" and profile.material_summary.actual_net_material_loss > 0:
            evidence_points.append(
                f"Contributed to {profile.material_summary.actual_net_material_loss} pawn-equivalent material points "
                f"conceded across critical positions."
            )

        # Extract affected moves
        affected_moves = []
        for sm in merged_samples[:3]:
            affected_moves.append({
                "game_id": sm.get("game_id"),
                "move": sm.get("move"),
                "played_move": sm.get("played_move", sm.get("move", "")),
                "best_move": sm.get("best_move"),
                "cpl": sm.get("cpl"),
                "summary": sm.get("summary", ""),
            })

        rec = CoachingRecommendation(
            recommendation_id=f"rec_{category}_{len(recommendations) + 1:02d}",
            priority=0,  # Will be assigned after sorting
            category=category,
            title=title,
            summary=summary,
            rationale=rationale,
            evidence=evidence_points,
            affected_games=merged_game_ids,
            affected_moves=affected_moves,
            suggested_training_type=TRAINING_TYPE_MAP.get(category, "targeted exercises"),
            confidence=merged_conf,
            limitations=conf_limitation,
            score=score,
        )
        recommendations.append(rec)
        used_categories.add(category)

    # -------------------------------------------------------------
    # 2. Process Opening Repertoire Evidence
    # -------------------------------------------------------------
    if repertoire is not None:
        # Check recurring opening choices (Requirement 8)
        # Only recommend if an opening appears repeatedly (>= 2 games) and post-theory struggle is verified
        for entry in repertoire.opening_statistics:
            if entry.total_games >= 2 and entry.after_theory.moves_count >= 10:
                in_cpl = entry.before_theory.avg_cpl or 0.0
                out_cpl = entry.after_theory.avg_cpl or 0.0
                cpl_delta = out_cpl - in_cpl

                if cpl_delta >= 30.0 and out_cpl >= 60.0:
                    op_score = _compute_recommendation_score(
                        episodes=entry.after_theory.critical_mistakes,
                        distinct_games=entry.distinct_games,
                        total_games=total_games,
                        avg_cpl=out_cpl,
                        peak_cpl=int(out_cpl * 1.5),
                        confidence_level="medium" if entry.total_games >= 3 else "low",
                    )
                    var_str = f" ({entry.variation_name})" if entry.variation_name else ""
                    rec_op = CoachingRecommendation(
                        recommendation_id=f"rec_opening_transition_{len(recommendations) + 1:02d}",
                        priority=0,
                        category="opening_transition",
                        title=f"Middlegame Transition Plans in the {entry.opening_name}{var_str}",
                        summary=(
                            f"Your recurring {entry.opening_name} line ({entry.eco}) shows a substantial accuracy drop "
                            f"after leaving established theory across {entry.total_games} games."
                        ),
                        rationale=(
                            f"In the {entry.opening_name}, post-theory average centipawn loss ({out_cpl:.1f} cp) "
                            f"is significantly higher than in-theory play ({in_cpl:.1f} cp). "
                            "Studying thematic middlegame pawn breaks and typical piece maneuvers will smooth the transition from the opening."
                        ),
                        evidence=[
                            f"Played in {entry.total_games} games as {entry.color} ({entry.win_rate * 100:.0f}% win rate).",
                            f"In-theory performance: {in_cpl:.1f} avg CPL ({entry.before_theory.critical_mistakes} critical mistakes across {entry.before_theory.moves_count} moves).",
                            f"Post-theory performance: {out_cpl:.1f} avg CPL ({entry.after_theory.critical_mistakes} critical mistakes across {entry.after_theory.moves_count} moves).",
                        ],
                        affected_games=entry.game_ids,
                        affected_moves=[],
                        suggested_training_type=TRAINING_TYPE_MAP["opening_transition"],
                        confidence="medium" if entry.total_games >= 3 else "low",
                        limitations=(
                            f"Based on {entry.total_games} games in this specific variation. "
                            "Reflects transition accuracy, not a diagnosis of opening knowledge."
                        ),
                        score=op_score,
                    )
                    recommendations.append(rec_op)

    # -------------------------------------------------------------
    # 3. Rank Recommendations by Composite Score
    # -------------------------------------------------------------
    recommendations.sort(key=lambda r: r.score, reverse=True)
    for idx, rec in enumerate(recommendations, start=1):
        rec.priority = idx

    # -------------------------------------------------------------
    # 4. Formulate Limitations and Methodological Constraints
    # -------------------------------------------------------------
    limitations_list = [
        conf_limitation,
        "Engine centipawn loss measures tactical accuracy, not human psychological factors or time trouble.",
        "Positional evaluations are inferred from Stockfish evaluation swings rather than human pawn-structure rules.",
    ]
    if repertoire is not None:
        single_game_openings = sum(1 for e in repertoire.opening_statistics if e.total_games == 1)
        if single_game_openings == len(repertoire.opening_statistics) and single_game_openings > 0:
            limitations_list.append(
                f"All {single_game_openings} played openings appeared in only 1 game each. "
                "In accordance with evidence discipline, no repertoire change is recommended."
            )

    strengths_data = [s.to_dict() for s in profile.strengths]

    return CoachingProfile(
        target_player=target_player,
        generated_at=now_iso,
        games_analyzed=total_games,
        confidence=overall_conf,
        top_recommendations=recommendations,
        strengths=strengths_data,
        limitations=limitations_list,
    )


def build_coaching_profile(
    game_analyses: List[Dict[str, Any]],
    target_player: str,
) -> CoachingProfile:
    """
    Build a complete CoachingProfile from raw game analysis JSON dictionaries.
    """
    profile = build_player_profile(game_analyses, target_player=target_player)
    repertoire = build_player_repertoire(game_analyses, target_player=target_player)
    return generate_coaching_recommendations(profile, repertoire=repertoire, game_analyses=game_analyses)


def build_coaching_profile_from_library(
    library: Any,
    target_player: str,
) -> CoachingProfile:
    """
    Build a complete CoachingProfile directly from a persistent GameLibrary instance.
    """
    from src.profile import build_player_profile_from_library
    from src.openings import build_player_repertoire_from_library

    profile = build_player_profile_from_library(library, target_player=target_player)
    repertoire = build_player_repertoire_from_library(library, target_player=target_player)
    return generate_coaching_recommendations(profile, repertoire=repertoire)
