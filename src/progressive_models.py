"""Independent progressive contracts; never legacy MoveAnalysis/CPL."""
from typing import Literal
from pydantic import Field, StrictBool, StrictInt, model_validator
from .evaluation_models import StrictModel, SearchConfig


class ProgressiveLimits(StrictModel):
    max_bytes: StrictInt = Field(1024**3, ge=16*1024**2, le=1024**4)
    max_jobs: StrictInt = Field(50000, ge=1, le=10000000)
    max_work: StrictInt = Field(500000, ge=1, le=100000000)
    max_active: StrictInt = Field(10000, ge=1, le=1000000)
    retention_days: StrictInt = Field(90, ge=1, le=36500)
    lease_seconds: float = Field(30, ge=10, le=300)
    max_attempts: StrictInt = Field(3, ge=1, le=3)
    screen_contexts: StrictInt = Field(48, ge=2, le=48)
    shortlist: StrictInt = Field(8, ge=2, le=8)
    deepen_decisions: StrictInt = Field(4, ge=1, le=4)
    game_budget: float = Field(30, gt=0, le=300)


class Schedule(StrictModel):
    game_ids: list[str] = Field(min_length=1, max_length=100)
    batch_id: str | None = Field(None, max_length=64)
    profile_id: str | None = Field(None, max_length=64)
    prepare_only: StrictBool = True
    priority: Literal[0,1,2] = 1
    budget_seconds: float = Field(300, ge=0, le=3600)
    idempotency_key: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def ids(self):
        if any(not x or len(x)>100 for x in self.game_ids) or len(set(self.game_ids))!=len(self.game_ids):
            raise ValueError('Game IDs must be distinct and bounded')
        return self


def tier_config(base: SearchConfig, tier):
    depth, seconds = {'screen':(10,.1),'medium':(14,.3),'deep':(18,1.)}[tier]
    data=base.model_dump(); data.update(limit_kind='combined',depth=depth,time_seconds=seconds,multipv=1)
    return SearchConfig.model_validate(data)


TIERS = {'screen':8,'medium':12,'deep':16}
