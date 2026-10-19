"""Supervisor safeguards tested without running expensive benchmarks or ps."""
from types import SimpleNamespace

import pytest

from scripts import benchmark_positions as benchmark


@pytest.mark.parametrize("budget,memory,reason", [(-1, 512, "wall budget"), (120, 1, "RSS budget")])
def test_supervisor_kills_child_on_budget(monkeypatch, budget, memory, reason):
    state = {"killed": False}
    class Child:
        pid = 123
        returncode = None
        def poll(self):
            return self.returncode
        def wait(self, timeout):
            self.returncode = -9
            return -9
    child = Child()
    monkeypatch.setattr(benchmark.subprocess, "Popen", lambda *a, **kw: child)
    monkeypatch.setattr(benchmark.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="2048", stderr="", returncode=0))
    def kill(pid, sig):
        assert pid == 123
        state["killed"] = True
    monkeypatch.setattr(benchmark.os, "killpg", kill)
    data = benchmark.supervise(50, "synthetic", budget, memory)
    assert state["killed"]
    assert reason in data["failed"]


def test_deterministic_dataset_includes_edge_states():
    from src.positions import prepare_game
    for n in range(6):
        first = benchmark.synthetic_pgn(n)
        assert first == benchmark.synthetic_pgn(n)
        assert len(prepare_game(first)) >= 2
    assert prepare_game(benchmark.synthetic_pgn(3))[0][2] == "e7e8q"
    assert prepare_game(benchmark.synthetic_pgn(4))[0][2] == "e5d6"
    assert prepare_game(benchmark.synthetic_pgn(5))[0][2] == "e1g1"
