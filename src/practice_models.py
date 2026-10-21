"""Strict practice inputs and deliberately separate answer-safe projections."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Account(Contract):
    source: str = Field(min_length=1, max_length=100)
    username: str = Field(min_length=1, max_length=200)


class Selection(Contract):
    game_id: str = Field(min_length=1, max_length=100)
    expected_pgn_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    plies: list[int] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def bounds(self):
        if any(isinstance(n, bool) or not 0 <= n <= 10000 for n in self.plies):
            raise ValueError('INVALID_PLY')
        if len(set(self.plies)) != len(self.plies):
            raise ValueError('DUPLICATE_PLY')
        return self


class Discover(Contract):
    account: Account
    games: list[Selection] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def unique(self):
        if len({g.game_id for g in self.games}) != len(self.games):
            raise ValueError('DUPLICATE_GAME')
        if sum(len(g.plies) for g in self.games) > 12:
            raise ValueError('ROOT_LIMIT')
        return self


class Start(Contract):
    puzzle_id: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=100)
    retry_of: str | None = Field(default=None, max_length=64)


class Event(Contract):
    kind: Literal['move', 'hint', 'reveal', 'abandon']
    revision: int = Field(ge=0, le=64)
    request_key: str = Field(min_length=1, max_length=100)
    uci: str | None = Field(default=None, pattern=r'^[a-h][1-8][a-h][1-8][qrbn]?$')

    @model_validator(mode='after')
    def shape(self):
        if (self.kind == 'move') != (self.uci is not None):
            raise ValueError('Move requires UCI; other events forbid it')
        return self


class PuzzleDefinition(Contract):
    schema_version: Literal['personal-puzzle/1'] = 'personal-puzzle/1'
    policy_version: Literal[1] = 1
    account: dict
    library: str
    context: dict
    objective: Literal['CHECKMATE_IN_ONE', 'EXAMINE_WITNESS']
    tier: Literal['VERIFIED_PUZZLE', 'GUIDED_EXERCISE']
    witness: str
    theme: str
    finding: dict
    coverage: dict
    limitations: list[str]


class PuzzleQuestion(Contract):
    schema_version: Literal['personal-puzzle/1'] = 'personal-puzzle/1'
    policy_version: Literal[1] = 1
    id: str
    tier: str
    objective: str
    prompt: str
    fen: str
    root_fen: str
    moves: list[str]
    learner_color: str
    history_complete: bool
    source: dict
    coverage: dict
    limitations: list[str]
    difficulty: Literal['UNKNOWN'] = 'UNKNOWN'
