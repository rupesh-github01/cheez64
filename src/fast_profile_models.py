"""Versioned evidence contracts for engine-free descriptive analytics."""
from typing import Any, Literal
from pydantic import BaseModel, Field


class Metric(BaseModel):
    value: Any = None
    numerator: float | None = None
    denominator: float | None = None
    unit: str
    provenance: Literal['OBSERVED', 'HEURISTIC', 'ENGINE_VERIFIED']
    status: Literal['OK', 'PARTIAL', 'INSUFFICIENT_DATA', 'UNSUPPORTED']
    method: str
    coverage: dict[str, Any]
    sample: dict[str, Any]
    limitations: list[str] = Field(default_factory=list)


class Selector(BaseModel):
    source: str
    username: str
    color: Literal['either', 'white', 'black'] = 'either'
    date_from: str | None = None
    date_to: str | None = None
    time_control: str | None = None
    matching: str


class Completeness(BaseModel):
    metadata_complete: bool
    replay_complete: bool | None
    requested_section: str
    eligible_games: int
    processed_games: int
    budget_exhausted: bool


class FastProfileResponse(BaseModel):
    schema_version: Literal['1.0']
    generated_at: str
    selector: Selector
    completeness: Completeness
    coverage: dict[str, Any]
    overview: dict[str, Any] | None = None
    trends: dict[str, Any] | None = None
    repertoire: dict[str, Any] | None = None
    time_management: dict[str, Any] | None = None
    move_tendencies: dict[str, Any] | None = None
    positions: dict[str, Any] | None = None
    engine_analysis_availability: dict[str, Any]
    methodology_versions: dict[str, str]
    limitations: list[str]


def metric(value, unit='count', method='recorded_count_v1', *, numerator=None,
           denominator=None, eligible=0, observed=None, games=0, moves=None,
           heuristic=False, partial=False, limitations=(), unsupported=False):
    observed = eligible if observed is None else observed
    return Metric(value=value, numerator=numerator, denominator=denominator, unit=unit,
                  method=method, provenance='HEURISTIC' if heuristic else 'OBSERVED',
                  status='UNSUPPORTED' if unsupported else ('INSUFFICIENT_DATA' if value is None else ('PARTIAL' if partial else 'OK')),
                  coverage={'eligible': eligible, 'observed': observed, 'missing': max(0, eligible-observed)},
                  sample={'games': games, 'moves': moves, 'preliminary': games < 20},
                  limitations=list(limitations)).model_dump()


def rate(numerator, denominator, *, games=0, heuristic=False, method='frequency_v1', partial=False):
    return metric(numerator/denominator if denominator else None, 'fraction', method,
                  numerator=numerator, denominator=denominator, eligible=denominator,
                  games=games, heuristic=heuristic, partial=partial)
