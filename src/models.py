from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class CandidateMove:

    move: str
    evaluation: Optional[int]
    principal_variation: List[str] = field(
        default_factory=list
    )


@dataclass
class MoveAnalysis:

    move_number: int
    color: str

    played_move: str
    best_move: str

    evaluation_before: Optional[int]
    evaluation_after: Optional[int]

    centipawn_loss: Optional[int]

    fen_before: str
    fen_after: str

    principal_variation: List[str] = field(
        default_factory=list
    )

    candidates: List[CandidateMove] = field(
        default_factory=list
    )

    clock_seconds: Optional[float] = None
    position_features: Optional[Dict[str, Any]] = None

    # Extended fields
    played_move_uci: Optional[str] = None
    best_move_uci: Optional[str] = None
    raw_centipawn_loss: Optional[int] = None
    classification: Optional[str] = None
    is_forced: bool = False