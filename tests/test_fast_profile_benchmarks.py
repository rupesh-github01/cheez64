"""Supervisor safety without expensive benchmark runs or process inspection."""
from types import SimpleNamespace
import pytest
from scripts import benchmark_fast_profiles as bench
from src.fast_profile_extract import extract_game


@pytest.mark.parametrize('seconds,memory,reason', [(-1, 512, 'runtime'), (120, 1, 'RSS')])
def test_supervisor_terminates_over_budget_child(monkeypatch, seconds, memory, reason):
    killed = []
    class Child:
        pid = 123
        returncode = None
        def poll(self): return self.returncode
        def wait(self, timeout): self.returncode = -9; return -9
    child = Child()
    monkeypatch.setattr(bench.subprocess, 'Popen', lambda *a, **kw: child)
    monkeypatch.setattr(bench.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout='2048', stderr='', returncode=0))
    monkeypatch.setattr(bench.os, 'killpg', lambda pid, sig: killed.append(pid))
    result = bench.supervised(50, 5, seconds=seconds, rss_limit=memory)
    assert killed == [123] and reason in result['failed']


def test_dataset_is_selected_player_deterministic_with_timing():
    import chess.pgn, io
    for i in range(10):
        text = bench.corpus_game(i)
        assert text == bench.corpus_game(i)
        game = chess.pgn.read_game(io.StringIO(text))
        assert game.headers['White'] == 'Hero'
        fact = extract_game(text, 'white', game.headers.get('TimeControl'))
        assert fact['clocks']['emt_moves'] > 0 or fact['own_moves'] == 0


def test_child_refuses_existing_database_alias(tmp_path):
    # Fail before importing/configuring API or opening SQLite.
    target = tmp_path/'original.db'
    target.write_bytes(b'personal bytes must be preserved')
    folder = tmp_path/'child'; folder.mkdir()
    (folder/'profiles.db').symlink_to(target)
    with pytest.raises(ValueError, match='fresh database'):
        bench.run_child(folder, 50, 3)
    assert target.read_bytes() == b'personal bytes must be preserved'
