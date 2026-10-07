"""
Opening detection, variation identification, theory-exit tracking, and repertoire profiling.
"""

from dataclasses import dataclass, field
from pathlib import Path
import json
import io
import re
from typing import Optional, List, Dict, Any, Tuple, Union
import chess.pgn
import chess


DEFAULT_OPENINGS_JSON = "data/openings.json"
DEFAULT_OPENINGS_TSV = "data/openings.tsv"


@dataclass
class OpeningEntry:
    """
    Reference opening entry from the canonical opening book.
    """
    eco: str
    name: str
    opening_name: str
    variation_name: Optional[str]
    pgn: str
    san: List[str]
    uci: List[str]
    epd: str
    ply: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OpeningEntry":
        name = data.get("name", "Unknown Opening")
        if ":" in name:
            parts = name.split(":", 1)
            op_name = parts[0].strip()
            var_name = parts[1].strip() or None
        else:
            op_name = name.strip()
            var_name = None

        return cls(
            eco=data.get("eco", "A00"),
            name=name,
            opening_name=op_name,
            variation_name=var_name,
            pgn=data.get("pgn", ""),
            san=data.get("san", []),
            uci=data.get("uci", []),
            epd=data.get("epd", ""),
            ply=data.get("ply", len(data.get("uci", []))),
        )


@dataclass
class OpeningAnalysis:
    """
    Opening identification and theory-exit metrics for a single game.
    """
    opening_name: str
    variation_name: Optional[str]
    eco: str
    full_name: str
    matched_moves: List[str]
    matched_ply: int
    theory_exit_ply: Optional[int]
    theory_exit_move: Optional[str]
    divergence_side: Optional[str]
    played_opening_line: List[str]
    is_transposition: bool
    confidence: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "opening_name": self.opening_name,
            "variation_name": self.variation_name,
            "eco": self.eco,
            "full_name": self.full_name,
            "matched_moves": list(self.matched_moves),
            "matched_ply": self.matched_ply,
            "theory_exit_ply": self.theory_exit_ply,
            "theory_exit_move": self.theory_exit_move,
            "divergence_side": self.divergence_side,
            "played_opening_line": list(self.played_opening_line),
            "is_transposition": self.is_transposition,
            "confidence": self.confidence,
        }


@dataclass
class RepertoirePerformance:
    """
    Performance statistics for a player inside or outside recognized opening theory.
    """
    moves_count: int = 0
    avg_cpl: Optional[float] = None
    critical_mistakes: int = 0
    blunders: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "moves_count": self.moves_count,
            "avg_cpl": self.avg_cpl,
            "critical_mistakes": self.critical_mistakes,
            "blunders": self.blunders,
        }


@dataclass
class RepertoireEntry:
    """
    Aggregated repertoire statistics for a specific opening line.
    """
    color: str
    first_move_group: str
    opening_name: str
    variation_name: Optional[str]
    eco: str
    full_name: str
    total_games: int
    distinct_games: int
    game_ids: List[str]
    wins: int
    draws: int
    losses: int
    win_rate: float
    analyzed_games: int
    avg_cpl_overall: Optional[float]
    before_theory: RepertoirePerformance
    after_theory: RepertoirePerformance
    repertoire_status: str
    status_description: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "color": self.color,
            "first_move_group": self.first_move_group,
            "opening_name": self.opening_name,
            "variation_name": self.variation_name,
            "eco": self.eco,
            "full_name": self.full_name,
            "total_games": self.total_games,
            "distinct_games": self.distinct_games,
            "game_ids": list(self.game_ids),
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "analyzed_games": self.analyzed_games,
            "avg_cpl_overall": self.avg_cpl_overall,
            "before_theory": self.before_theory.to_dict(),
            "after_theory": self.after_theory.to_dict(),
            "repertoire_status": self.repertoire_status,
            "status_description": self.status_description,
        }


@dataclass
class RepertoireProfile:
    """
    Comprehensive multi-game opening repertoire profile for a target player.
    """
    target_player: str
    total_games: int
    white_games: int
    black_games: int
    white_repertoire: Dict[str, List[RepertoireEntry]]
    black_repertoire: Dict[str, List[RepertoireEntry]]
    opening_statistics: List[RepertoireEntry]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_player": self.target_player,
            "total_games": self.total_games,
            "white_games": self.white_games,
            "black_games": self.black_games,
            "white_repertoire": {
                k: [entry.to_dict() for entry in v]
                for k, v in self.white_repertoire.items()
            },
            "black_repertoire": {
                k: [entry.to_dict() for entry in v]
                for k, v in self.black_repertoire.items()
            },
            "opening_statistics": [entry.to_dict() for entry in self.opening_statistics],
        }

    def summary(self) -> str:
        """
        Human-readable summary of player's observed repertoire.
        """
        lines = []
        lines.append("=" * 80)
        lines.append(f"OPENING REPERTOIRE PROFILE: {self.target_player}")
        lines.append(f"Analyzed {self.total_games} games ({self.white_games} as White, {self.black_games} as Black)")
        lines.append("=" * 80)

        # White Repertoire
        lines.append("\nWHITE REPERTOIRE")
        lines.append("-" * 40)
        if not self.white_repertoire:
            lines.append("No games recorded as White.")
        else:
            for group, entries in sorted(self.white_repertoire.items()):
                lines.append(f"\n{group}")
                for e in entries:
                    var_str = f": {e.variation_name}" if e.variation_name else ""
                    perf_str = f", {e.win_rate * 100:.0f}% win rate ({e.wins}W/{e.draws}D/{e.losses}L)"
                    lines.append(f"  ├── {e.opening_name}{var_str} ({e.eco}) — {e.total_games} game{'s' if e.total_games > 1 else ''}{perf_str}")
                    lines.append(f"  │   Status: {e.status_description}")
                    if e.before_theory.avg_cpl is not None and e.after_theory.avg_cpl is not None:
                        lines.append(f"  │   In theory: {e.before_theory.avg_cpl:.1f} avg CPL ({e.before_theory.critical_mistakes} crits) | Out of theory: {e.after_theory.avg_cpl:.1f} avg CPL ({e.after_theory.critical_mistakes} crits)")

        # Black Repertoire
        lines.append("\nBLACK REPERTOIRE")
        lines.append("-" * 40)
        if not self.black_repertoire:
            lines.append("No games recorded as Black.")
        else:
            for group, entries in sorted(self.black_repertoire.items()):
                lines.append(f"\n{group}")
                for e in entries:
                    var_str = f": {e.variation_name}" if e.variation_name else ""
                    perf_str = f", {e.win_rate * 100:.0f}% win rate ({e.wins}W/{e.draws}D/{e.losses}L)"
                    lines.append(f"  ├── {e.opening_name}{var_str} ({e.eco}) — {e.total_games} game{'s' if e.total_games > 1 else ''}{perf_str}")
                    lines.append(f"  │   Status: {e.status_description}")
                    if e.before_theory.avg_cpl is not None and e.after_theory.avg_cpl is not None:
                        lines.append(f"  │   In theory: {e.before_theory.avg_cpl:.1f} avg CPL ({e.before_theory.critical_mistakes} crits) | Out of theory: {e.after_theory.avg_cpl:.1f} avg CPL ({e.after_theory.critical_mistakes} crits)")

        lines.append("\n" + "=" * 80)
        return "\n".join(lines)


class _TrieNode:
    """Internal node in the move-sequence prefix tree."""
    def __init__(self):
        self.entry: Optional[OpeningEntry] = None
        self.children: Dict[str, _TrieNode] = {}


class OpeningDatabase:
    """
    In-memory opening database backed by prefix trie and EPD transposition indices.
    """

    def __init__(
        self,
        json_path: Optional[str] = None,
        tsv_path: Optional[str] = None,
        openings_data: Optional[List[Dict[str, Any]]] = None,
    ):
        self.root = _TrieNode()
        self.epd_index: Dict[str, OpeningEntry] = {}
        self.entries_count = 0

        if openings_data is not None:
            self._load_from_data(openings_data)
        else:
            j_path = Path(json_path or DEFAULT_OPENINGS_JSON)
            t_path = Path(tsv_path or DEFAULT_OPENINGS_TSV)
            if j_path.exists():
                self._load_from_json(j_path)
            elif t_path.exists():
                self._load_from_tsv(t_path)
            else:
                self._load_minimal_fallback()

    def _load_from_json(self, path: Path) -> None:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._load_from_data(data)

    def _load_from_tsv(self, path: Path) -> None:
        raw_list = []
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines[1:]:
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                eco, name, pgn_str = parts[0], parts[1], parts[2]
                game = chess.pgn.read_game(io.StringIO(pgn_str))
                if game is None:
                    continue
                board = game.board()
                uci_moves = []
                san_moves = []
                for m in game.mainline_moves():
                    san_moves.append(board.san(m))
                    uci_moves.append(m.uci())
                    board.push(m)
                raw_list.append({
                    "eco": eco,
                    "name": name,
                    "pgn": pgn_str,
                    "san": san_moves,
                    "uci": uci_moves,
                    "epd": board.epd(),
                    "ply": len(uci_moves),
                })
        self._load_from_data(raw_list)

    def _load_minimal_fallback(self) -> None:
        """Minimal fallback opening dataset for standalone environments."""
        sample = [
            {"eco": "C50", "name": "Italian Game", "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bc4", "san": ["e4", "e5", "Nf3", "Nc6", "Bc4"], "uci": ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4"], "epd": "r1bqk1nr/pppp1ppp/2n5/2b1p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq -", "ply": 5},
            {"eco": "B20", "name": "Sicilian Defense", "pgn": "1. e4 c5", "san": ["e4", "c5"], "uci": ["e2e4", "c7c5"], "epd": "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -", "ply": 2},
            {"eco": "C00", "name": "French Defense", "pgn": "1. e4 e6", "san": ["e4", "e6"], "uci": ["e2e4", "e7e6"], "epd": "rnbqkbnr/pppp1ppp/4p3/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq -", "ply": 2},
            {"eco": "D00", "name": "Queen's Pawn Game", "pgn": "1. d4 d5", "san": ["d4", "d5"], "uci": ["d2d4", "d7d5"], "epd": "rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq -", "ply": 2},
        ]
        self._load_from_data(sample)

    def _load_from_data(self, data_list: List[Dict[str, Any]]) -> None:
        self.root = _TrieNode()
        self.epd_index = {}
        count = 0

        for item in data_list:
            entry = OpeningEntry.from_dict(item)
            if not entry.uci:
                continue

            curr = self.root
            for move in entry.uci:
                if move not in curr.children:
                    curr.children[move] = _TrieNode()
                curr = curr.children[move]

            # Store deepest / most specific entry
            if curr.entry is None or entry.ply >= curr.entry.ply:
                curr.entry = entry

            # Index by EPD for transposition lookups
            epd = entry.epd
            if epd:
                if epd not in self.epd_index or entry.ply > self.epd_index[epd].ply:
                    self.epd_index[epd] = entry

            count += 1

        self.entries_count = count

    def detect_opening(self, pgn_or_game: Union[str, chess.pgn.Game]) -> OpeningAnalysis:
        """
        Identify the opening, variation, ECO code, and theory exit from a PGN or chess.pgn.Game.
        """
        if isinstance(pgn_or_game, str):
            game = chess.pgn.read_game(io.StringIO(pgn_or_game))
        else:
            game = pgn_or_game

        if game is None:
            return OpeningAnalysis(
                opening_name="Unrecognized Opening",
                variation_name=None,
                eco="A00",
                full_name="Unrecognized Opening",
                matched_moves=[],
                matched_ply=0,
                theory_exit_ply=None,
                theory_exit_move=None,
                divergence_side=None,
                played_opening_line=[],
                is_transposition=False,
                confidence="unrecognized",
            )

        board = game.board()
        moves_uci: List[str] = []
        moves_san: List[str] = []
        epds: List[str] = []

        for m in game.mainline_moves():
            moves_san.append(board.san(m))
            moves_uci.append(m.uci())
            board.push(m)
            epds.append(board.epd())

        if not moves_uci:
            return OpeningAnalysis(
                opening_name="Unrecognized Opening",
                variation_name=None,
                eco="A00",
                full_name="Unrecognized Opening",
                matched_moves=[],
                matched_ply=0,
                theory_exit_ply=None,
                theory_exit_move=None,
                divergence_side=None,
                played_opening_line=[],
                is_transposition=False,
                confidence="unrecognized",
            )

        # 1. Walk Trie prefix
        curr = self.root
        last_seq_entry: Optional[OpeningEntry] = None
        last_seq_ply = 0

        for idx, uci_move in enumerate(moves_uci, start=1):
            if uci_move in curr.children:
                curr = curr.children[uci_move]
                if curr.entry:
                    last_seq_entry = curr.entry
                    last_seq_ply = idx
            else:
                break

        # 2. Check transposition via EPD index up to move 25
        deepest_epd_entry: Optional[OpeningEntry] = None
        deepest_epd_ply = 0
        deepest_epd_game_ply = 0

        for idx, epd in enumerate(epds[:25], start=1):
            if epd in self.epd_index:
                entry = self.epd_index[epd]
                if entry.ply > deepest_epd_ply:
                    deepest_epd_ply = entry.ply
                    deepest_epd_game_ply = idx
                    deepest_epd_entry = entry

        # Decide primary match
        if deepest_epd_entry and deepest_epd_ply > last_seq_ply:
            final_entry = deepest_epd_entry
            is_transpo = True
            matched_ply = deepest_epd_game_ply
            matched_moves = moves_san[:deepest_epd_game_ply]
        elif last_seq_entry:
            final_entry = last_seq_entry
            is_transpo = False
            matched_ply = last_seq_ply
            matched_moves = moves_san[:last_seq_ply]
        else:
            final_entry = None
            is_transpo = False
            matched_ply = 0
            matched_moves = []

        # Determine theory exit ply and divergence move
        total_game_plies = len(moves_uci)
        if matched_ply < total_game_plies:
            theory_exit_ply = matched_ply + 1
            divergence_move_idx = matched_ply
            exit_san = moves_san[divergence_move_idx]
            move_num = (theory_exit_ply + 1) // 2
            prefix = f"{move_num}." if theory_exit_ply % 2 != 0 else f"{move_num}..."
            theory_exit_move = f"{prefix} {exit_san}"
            divergence_side = "White" if theory_exit_ply % 2 != 0 else "Black"
        else:
            theory_exit_ply = None
            theory_exit_move = None
            divergence_side = None

        if final_entry is not None:
            opening_name = final_entry.opening_name
            variation_name = final_entry.variation_name
            eco = final_entry.eco
            full_name = final_entry.name

            # Calculate confidence
            if final_entry.variation_name and matched_ply >= 4:
                confidence = "high"
            elif matched_ply >= 2:
                confidence = "moderate"
            else:
                confidence = "low"
        else:
            opening_name = "Unrecognized Opening"
            variation_name = None
            eco = "A00"
            full_name = "Unrecognized Opening"
            confidence = "unrecognized"

        return OpeningAnalysis(
            opening_name=opening_name,
            variation_name=variation_name,
            eco=eco,
            full_name=full_name,
            matched_moves=matched_moves,
            matched_ply=matched_ply,
            theory_exit_ply=theory_exit_ply,
            theory_exit_move=theory_exit_move,
            divergence_side=divergence_side,
            played_opening_line=moves_san[:min(len(moves_san), max(matched_ply + 2, 8))],
            is_transposition=is_transpo,
            confidence=confidence,
        )


_GLOBAL_OPENING_DB: Optional[OpeningDatabase] = None


def get_default_opening_database() -> OpeningDatabase:
    """
    Get or lazily initialize the singleton OpeningDatabase.
    """
    global _GLOBAL_OPENING_DB
    if _GLOBAL_OPENING_DB is None:
        _GLOBAL_OPENING_DB = OpeningDatabase()
    return _GLOBAL_OPENING_DB


def detect_opening(
    pgn_or_game: Union[str, chess.pgn.Game],
    opening_db: Optional[OpeningDatabase] = None,
) -> OpeningAnalysis:
    """
    Detect opening from a PGN string or parsed Game object using the default or custom database.
    """
    db = opening_db or get_default_opening_database()
    return db.detect_opening(pgn_or_game)


def build_player_repertoire(
    game_analyses: List[Dict[str, Any]],
    target_player: str,
    opening_db: Optional[OpeningDatabase] = None,
) -> RepertoireProfile:
    """
    Aggregate games into a structured player opening repertoire.
    Supports both analyzed games and unanalyzed PGN records.
    """
    db = opening_db or get_default_opening_database()

    # Buckets: (color, group_key, full_name) -> aggregated data
    repertoire_map: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    total_games = 0
    white_games = 0
    black_games = 0

    for game_data in game_analyses:
        meta = game_data.get("metadata", {})
        white_player = meta.get("white", "")
        black_player = meta.get("black", "")
        result = meta.get("result", "*")
        game_id = game_data.get("game_id", meta.get("game_id", "unknown"))

        # Determine hero color
        if target_player.lower() in white_player.lower():
            hero_color = "White"
            is_hero_win = (result == "1-0")
            is_hero_loss = (result == "0-1")
            is_draw = (result == "1/2-1/2")
            white_games += 1
        elif target_player.lower() in black_player.lower():
            hero_color = "Black"
            is_hero_win = (result == "0-1")
            is_hero_loss = (result == "1-0")
            is_draw = (result == "1/2-1/2")
            black_games += 1
        else:
            # Default to White if ambiguous
            hero_color = "White"
            is_hero_win = False
            is_hero_loss = False
            is_draw = False
            white_games += 1

        total_games += 1

        # Extract or detect opening analysis
        pgn_text = game_data.get("pgn", "")
        if not pgn_text and "moves" in game_data:
            # Reconstruct minimal PGN if raw text missing
            moves_sans = [m.get("played_move") for m in game_data.get("moves", []) if m.get("played_move")]
            pgn_text = " ".join(f"{i//2 + 1}. {m}" if i % 2 == 0 else m for i, m in enumerate(moves_sans))

        opening_analysis = db.detect_opening(pgn_text)

        # Extract first move group
        first_move_group = "Unknown"
        if opening_analysis.played_opening_line:
            first_san = opening_analysis.played_opening_line[0]
            if hero_color == "White":
                first_move_group = f"1. {first_san}"
            else:
                first_move_group = f"vs 1. {first_san}"

        key = (hero_color, first_move_group, opening_analysis.full_name)
        if key not in repertoire_map:
            repertoire_map[key] = {
                "color": hero_color,
                "first_move_group": first_move_group,
                "opening_name": opening_analysis.opening_name,
                "variation_name": opening_analysis.variation_name,
                "eco": opening_analysis.eco,
                "full_name": opening_analysis.full_name,
                "game_ids": [],
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "analyzed_games": 0,
                "total_cpls": [],
                "before_cpls": [],
                "before_criticals": 0,
                "before_blunders": 0,
                "before_moves_count": 0,
                "after_cpls": [],
                "after_criticals": 0,
                "after_blunders": 0,
                "after_moves_count": 0,
            }

        entry_bucket = repertoire_map[key]
        entry_bucket["game_ids"].append(game_id)
        if is_hero_win:
            entry_bucket["wins"] += 1
        elif is_draw:
            entry_bucket["draws"] += 1
        elif is_hero_loss:
            entry_bucket["losses"] += 1

        # Process move evaluations if game is analyzed
        moves = game_data.get("moves", [])
        if moves:
            entry_bucket["analyzed_games"] += 1
            exit_ply = opening_analysis.theory_exit_ply

            for m in moves:
                if m.get("color") != hero_color:
                    continue

                cpl = m.get("centipawn_loss")
                is_crit = bool(m.get("is_critical"))
                is_blunder = (cpl is not None and cpl >= 300)
                move_num = m.get("move_number", 1)
                ply_num = (move_num - 1) * 2 + (1 if hero_color == "White" else 2)

                if cpl is not None:
                    entry_bucket["total_cpls"].append(cpl)

                if exit_ply is not None and ply_num < exit_ply:
                    # Before theory exit
                    entry_bucket["before_moves_count"] += 1
                    if cpl is not None:
                        entry_bucket["before_cpls"].append(cpl)
                    if is_crit:
                        entry_bucket["before_criticals"] += 1
                    if is_blunder:
                        entry_bucket["before_blunders"] += 1
                else:
                    # After theory exit
                    entry_bucket["after_moves_count"] += 1
                    if cpl is not None:
                        entry_bucket["after_cpls"].append(cpl)
                    if is_crit:
                        entry_bucket["after_criticals"] += 1
                    if is_blunder:
                        entry_bucket["after_blunders"] += 1

    # Convert buckets to RepertoireEntry objects
    white_rep: Dict[str, List[RepertoireEntry]] = {}
    black_rep: Dict[str, List[RepertoireEntry]] = {}
    all_entries: List[RepertoireEntry] = []

    for (color, group, _), b in repertoire_map.items():
        total_g = len(b["game_ids"])
        distinct_g = len(set(b["game_ids"]))
        wins = b["wins"]
        draws = b["draws"]
        losses = b["losses"]
        win_rate = round(wins / total_g, 3) if total_g > 0 else 0.0

        # Overall average CPL
        avg_cpl = round(sum(b["total_cpls"]) / len(b["total_cpls"]), 1) if b["total_cpls"] else None

        # Before theory performance
        before_avg = round(sum(b["before_cpls"]) / len(b["before_cpls"]), 1) if b["before_cpls"] else None
        before_perf = RepertoirePerformance(
            moves_count=b["before_moves_count"],
            avg_cpl=before_avg,
            critical_mistakes=b["before_criticals"],
            blunders=b["before_blunders"],
        )

        # After theory performance
        after_avg = round(sum(b["after_cpls"]) / len(b["after_cpls"]), 1) if b["after_cpls"] else None
        after_perf = RepertoirePerformance(
            moves_count=b["after_moves_count"],
            avg_cpl=after_avg,
            critical_mistakes=b["after_criticals"],
            blunders=b["after_blunders"],
        )

        # Repertoire status rules (Requirement 6)
        color_total = white_games if color == "White" else black_games
        ratio = (total_g / color_total) if color_total > 0 else 0.0

        if total_g == 1:
            rep_status = "observed_opening"
            status_desc = "Observed in 1 game. Insufficient evidence to establish as a repertoire choice."
        elif total_g >= 4 or (total_g >= 3 and ratio >= 0.5):
            rep_status = "primary_line"
            status_desc = f"Primary repertoire choice played across {total_g} games ({ratio * 100:.0f}% of games as {color})."
        else:
            rep_status = "recurring_choice"
            status_desc = f"Recurring repertoire choice played in {total_g} games."

        entry = RepertoireEntry(
            color=color,
            first_move_group=group,
            opening_name=b["opening_name"],
            variation_name=b["variation_name"],
            eco=b["eco"],
            full_name=b["full_name"],
            total_games=total_g,
            distinct_games=distinct_g,
            game_ids=sorted(list(set(b["game_ids"]))),
            wins=wins,
            draws=draws,
            losses=losses,
            win_rate=win_rate,
            analyzed_games=b["analyzed_games"],
            avg_cpl_overall=avg_cpl,
            before_theory=before_perf,
            after_theory=after_perf,
            repertoire_status=rep_status,
            status_description=status_desc,
        )

        all_entries.append(entry)
        target_dict = white_rep if color == "White" else black_rep
        if group not in target_dict:
            target_dict[group] = []
        target_dict[group].append(entry)

    # Sort entries by frequency descending
    for group in white_rep:
        white_rep[group].sort(key=lambda x: x.total_games, reverse=True)
    for group in black_rep:
        black_rep[group].sort(key=lambda x: x.total_games, reverse=True)
    all_entries.sort(key=lambda x: x.total_games, reverse=True)

    return RepertoireProfile(
        target_player=target_player,
        total_games=total_games,
        white_games=white_games,
        black_games=black_games,
        white_repertoire=white_rep,
        black_repertoire=black_rep,
        opening_statistics=all_entries,
    )


def build_player_repertoire_from_library(
    library: Any,
    target_player: str,
    opening_db: Optional[OpeningDatabase] = None,
) -> RepertoireProfile:
    """
    Build a RepertoireProfile directly from games and analyses stored in a GameLibrary.
    """
    all_games = library.list_games()
    game_payloads: List[Dict[str, Any]] = []

    for g in all_games:
        # Check if target player participated
        if (target_player.lower() not in g.white.lower()) and (target_player.lower() not in g.black.lower()):
            continue

        payload: Dict[str, Any] = {
            "game_id": g.game_id,
            "pgn": g.pgn,
            "metadata": {
                "game_id": g.game_id,
                "white": g.white,
                "black": g.black,
                "result": g.result,
                "date": g.date,
                "eco": g.eco,
            },
            "moves": [],
        }

        analysis = library.get_analysis(g.game_id)
        if analysis and "moves" in analysis:
            payload["moves"] = analysis["moves"]
            payload["summary"] = analysis.get("summary", {})

        game_payloads.append(payload)

    return build_player_repertoire(game_payloads, target_player=target_player, opening_db=opening_db)
