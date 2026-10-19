"""Small deterministic corpus check; expensive scales run through explicit harness."""
from scripts.benchmark_evaluations import fixtures
from src.evaluation_models import digest


def test_deterministic_legal_distinct_contexts():
    first=fixtures(50); second=fixtures(50)
    assert [e.model_dump() for e,_ in first]==[e.model_dump() for e,_ in second]
    assert len({digest(q.context.identity()) for _,q in first})==50
    assert any(e.pvs[0].kind=='mate' for e,_ in first)
    assert any(len(e.pvs)<e.config.multipv for e,_ in first)
    assert {e.config.source.engine_version for e,_ in first}=={'v1','v2'}
