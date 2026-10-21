"""Versioned selected-position contracts, independent of legacy MoveAnalysis."""
from dataclasses import dataclass, field
import time
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class BudgetExceeded(ValueError):
    pass


@dataclass
class UnderstandingBudget:
    clock: object = time.monotonic
    board_seconds: float = 1.0
    total_seconds: float = 2.0
    max_pushes: int = 512
    started: float = field(init=False)
    pushes: int = 0
    probes: int = 0

    def __post_init__(self):
        self.started = self.clock()

    def check(self, *, evidence=False):
        if self.clock() - self.started >= (self.total_seconds if evidence else self.board_seconds):
            raise BudgetExceeded('TIME_BUDGET')

    def push(self, *, evidence=False):
        self.check(evidence=evidence)
        if self.pushes >= self.max_pushes:
            raise BudgetExceeded('CHILD_PUSH_BUDGET')
        self.pushes += 1


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Finding(Contract):
    id: str
    motif: str
    rule_version: int = 1
    perspective: Literal['white', 'black']
    stage: Literal['before', 'after', 'transition', 'candidate']
    provenance: Literal['BOARD_FACT', 'HEURISTIC', 'ENGINE_VERIFIED'] = 'BOARD_FACT'
    predicate_status: Literal['ESTABLISHED', 'CANDIDATE', 'NOT_ASSESSED'] = 'ESTABLISHED'
    squares: list[str] = Field(default_factory=list)
    facts: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class UnderstandingResponse(Contract):
    schema_version: Literal['position-understanding/1'] = 'position-understanding/1'
    policy_version: int = 1
    generated_at: str
    source: dict
    selection: dict
    positions: dict
    features: dict
    changes: dict
    findings: list[Finding] = Field(default_factory=list, max_length=32)
    explanations: list[dict] = Field(default_factory=list, max_length=8)
    coverage: dict
    engine_evidence: dict
    limitations: list[str]
