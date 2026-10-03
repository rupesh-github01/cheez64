from dataclasses import dataclass, field
from typing import List, Optional


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

    principal_variation: List[str] = field(default_factory=list)

    clock_seconds: Optional[float] = None