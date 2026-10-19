from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

import chess
import pytest

from src.library import GameLibrary
from src.position_schema import DDL, initialize
from src.positions import canonical_position, prepare_game, PositionIndex, PositionQueries, UnsupportedVariant

FIXTURES = Path(__file__).parent / "data/positions"
TRANSPOSE = "rnbqkb1r/ppp1pp1p/5np1/3p4/8/5NP1/PPPPPPBP/RNBQK2R w KQkq -"


def pgn(name):
    return (FIXTURES / (name + ".pgn")).read_text()


@pytest.fixture
def lib(tmp_path):
    with GameLibrary(str(tmp_path / "positions.db")) as library:
        yield library


def drain(lib):
    idx = PositionIndex(lib._conn)
    while game_id := idx.next_pending():
        assert idx.index_game(game_id) == "COMPLETE"


def test_identity_ep_clocks_turn_castling():
    b = chess.Board()
    assert canonical_position(b) == canonical_position(b.fen().rsplit(" ", 2)[0])
    assert canonical_position(b) == canonical_position(b.fen().replace("0 1", "40 23"))
    assert canonical_position(b) != canonical_position(b.fen().replace(" w ", " b "))
    assert canonical_position(b) != canonical_position(b.fen().replace("KQkq", "-"))
    b.push_san("e4")
    assert canonical_position(b.fen(en_passant="fen")) == canonical_position(b.fen())
    for fen, same in [("k7/8/8/3pP3/8/8/8/4K3 w - d6 0 1", False),
                      ("k3r3/8/8/3pP3/8/8/8/4K3 w - d6 0 1", True)]:
        assert (canonical_position(fen) == canonical_position(fen.replace("d6", "-"))) is same
    for invalid in ["bad", "8/8/8/8/8/8/8/4K3 w K - 0 1"]:
        with pytest.raises(ValueError):
            canonical_position(invalid)
    with pytest.raises(UnsupportedVariant):
        canonical_position(chess.Board(chess960=True))
    with pytest.raises(UnsupportedVariant):
        canonical_position(chess.STARTING_FEN, "chess960")


def test_transposition_filters_and_distinct_results(lib):
    for name in ("transposition_a", "transposition_b"):
        lib.add_game(pgn(name))
    q = PositionQueries(lib._conn)
    assert q.find(TRANSPOSE)["coverage"]["partial_index"]
    drain(lib)
    found = q.find(TRANSPOSE)
    assert (found["occurrence_count"], found["distinct_game_count"]) == (2, 2)
    assert not found["coverage"]["partial_index"]
    move = q.moves(found["position_id"])["items"][0]
    assert (move["uci"], move["san"], move["move_frequency"]) == ("e1g1", "O-O", 1)
    assert (move["white_wins"], move["black_wins"], move["completed_games"]) == (1, 1, 2)
    assert move["white_win_rate"] == move["white_score_rate"] == 0.5
    for color, n in [("white", 1), ("black", 1), ("either", 2)]:
        query = PositionQueries(lib._conn, "aLiCe", color)
        assert query.find(TRANSPOSE)["distinct_game_count"] == n
        assert len(query.games(found["position_id"])["items"]) == n
    # Final halfmove clocks differ but exploration positions agree.
    final = lib._conn.execute("SELECT position_id FROM position_occurrences WHERE ply=7").fetchall()
    assert final[0][0] == final[1][0]


def test_repeated_position_counts_outcomes_once_per_move(lib):
    g, _ = lib.add_game(pgn("repetition"))
    drain(lib)
    q = PositionQueries(lib._conn)
    pos = q.find(chess.STARTING_FEN)
    assert (pos["occurrence_count"], pos["distinct_game_count"], pos["final_occurrence_count"]) == (3, 1, 1)
    assert [r["ply"] for r in q.occurrences(pos["position_id"])["items"]] == [0, 4, 8]
    move = q.moves(pos["position_id"])["items"][0]
    assert (move["occurrence_count"], move["distinct_game_count"], move["draws"]) == (2, 1, 1)
    assert (move["white_win_rate"], move["white_score_rate"]) == (0, 0.5)
    assert lib._conn.execute("SELECT count(*) FROM position_occurrences WHERE game_id=?", (g.game_id,)).fetchone()[0] == 9


def test_one_game_can_contribute_to_multiple_moves(lib):
    lib.add_game('[Result "1-0"]\n\n1. Nf3 Nf6 2. Ng1 Ng8 3. e4 1-0')
    drain(lib)
    q = PositionQueries(lib._conn)
    pos = q.find(chess.STARTING_FEN)
    moves = q.moves(pos["position_id"])["items"]
    assert pos["distinct_game_count"] == 1
    assert sum(r["distinct_game_count"] for r in moves) == 2
    assert [r["move_frequency"] for r in moves] == [0.5, 0.5]
    assert all(r["white_wins"] == 1 for r in moves)


@pytest.mark.parametrize("name,plies,moves", [("promotion", 1, ["e7e8q"]), ("castling", 2, ["e1g1", "e8c8"])])
def test_setup_promotion_castling(lib, name, plies, moves):
    rows = prepare_game(pgn(name))
    assert len(rows) == plies+1
    assert [r[2] for r in rows[:-1]] == moves
    lib.add_game(pgn(name))
    drain(lib)
    q = PositionQueries(lib._conn)
    found = q.find(rows[0][1])
    m = q.moves(found["position_id"])["items"][0]
    assert m["unfinished"] == 1 and m["white_win_rate"] is None
    final = q.find(rows[-1][1])
    assert q.moves(final["position_id"])["items"] == []


def test_ep_capture_underpromotion_empty_and_parse_limits():
    ep = '[SetUp "1"]\n[FEN "k7/8/8/3pP3/8/8/8/4K3 w - d6 0 1"]\n\n1. exd6 *'
    assert prepare_game(ep)[0][2] == "e5d6"
    assert prepare_game(pgn("promotion").replace("e8=Q+", "e8=N"))[0][2] == "e7e8n"
    assert len(prepare_game('[White "Nobody"]\n\n*')) == 1
    for invalid in ["", "1. e4 e5 2. Bh6 *", "1. -- *", pgn("transposition_a") + '\n' + pgn("transposition_b")]:
        with pytest.raises(ValueError):
            prepare_game(invalid)
    with pytest.raises(ValueError, match="byte limit"):
        prepare_game(pgn("repetition"), max_pgn_bytes=1)
    with pytest.raises(ValueError, match="ply limit"):
        prepare_game(pgn("repetition"), max_plies=2)


def test_duplicate_import_schedule_and_batch_rollback(lib):
    g, _ = lib.add_game(pgn("repetition"))
    drain(lib)
    assert lib.add_game(pgn("repetition"))[1] is False
    assert lib.add_games_batch([(pgn("repetition"), None, None)])[0][1] is False
    assert PositionQueries(lib._conn).find(chess.STARTING_FEN)["occurrence_count"] == 3
    before = lib.count_games()
    with patch("src.library.schedule_positions", side_effect=RuntimeError("schedule failure")):
        with pytest.raises(RuntimeError):
            lib.add_game(pgn("transposition_a"))
        with pytest.raises(RuntimeError):
            lib.add_games_batch([(pgn("transposition_b"), None, None)])
    assert lib.count_games() == before
    assert lib._conn.execute("SELECT count(*) FROM game_position_index").fetchone()[0] == before


def test_reindex_atomic_rollback_and_deletion(lib):
    g, _ = lib.add_game(pgn("repetition"))
    drain(lib)
    idx = PositionIndex(lib._conn)
    for _ in range(2):
        idx.request_reindex(g.game_id)
        assert idx.index_game(g.game_id) == "COMPLETE"
    idx.request_reindex(g.game_id)
    lib._conn.execute("""CREATE TEMP TRIGGER fail_publish BEFORE INSERT ON position_occurrences
        WHEN NEW.ply=2 BEGIN SELECT RAISE(ABORT,'injected interruption'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        idx.index_game(g.game_id)
    assert lib._conn.execute("SELECT count(*) FROM position_occurrences").fetchone()[0] == 9
    assert idx.next_pending() == g.game_id
    lib._conn.execute("DROP TRIGGER fail_publish")
    assert idx.index_game(g.game_id) == "COMPLETE"
    lib.delete_game(g.game_id)
    assert lib._conn.execute("SELECT count(*) FROM position_occurrences").fetchone()[0] == 0
    assert lib._conn.execute("SELECT count(*) FROM game_position_index").fetchone()[0] == 0
    assert PositionQueries(lib._conn).find(chess.STARTING_FEN)["occurrence_count"] == 0


def test_deletion_while_parsing(lib):
    g, _ = lib.add_game(pgn("repetition"))
    def deleting_parse(text):
        rows = prepare_game(text)
        with GameLibrary(lib.db_path) as other:
            other.delete_game(g.game_id)
        return rows
    with patch("src.positions.prepare_game", side_effect=deleting_parse):
        assert PositionIndex(lib._conn).index_game(g.game_id) == "DELETED"
    assert lib._conn.execute("SELECT count(*) FROM positions").fetchone()[0] == 0


def test_failures_dont_starve_and_reindex_retains_last_good(lib):
    g, _ = lib.add_game(pgn("repetition"))
    idx = PositionIndex(lib._conn)
    drain(lib)
    idx.request_reindex(g.game_id)
    with patch("src.positions.prepare_game", side_effect=ValueError("failure")):
        assert idx.index_game(g.game_id) == "FAILED"
    c = PositionQueries(lib._conn).coverage()
    assert c["indexed_games"] == 1 and c["partial_index"] and c["failed_games"] == 1
    assert lib._conn.execute("SELECT count(*) FROM position_occurrences").fetchone()[0] == 9
    idx.request_reindex(g.game_id, retry_only=True)
    assert idx.index_game(g.game_id) == "COMPLETE"
    other, _ = lib.add_game('[Variant "Chess960"]\n\n1. e4 *')
    assert idx.index_game(other.game_id) == "UNSUPPORTED"
    assert idx.next_pending() is None


def test_interrupted_historical_backfill_and_reconciliation(lib):
    for name in ("transposition_a", "transposition_b", "repetition"):
        lib.add_game(pgn(name))
    # Simulate records made by an older binary, without scheduling state.
    with lib._conn:
        lib._conn.execute("DELETE FROM game_position_index")
    idx = PositionIndex(lib._conn)
    assert idx.reconcile(1) == 1
    idx.index_game(idx.next_pending())
    with GameLibrary(lib.db_path) as resumed:
        idx2 = PositionIndex(resumed._conn)
        assert idx2.reconcile(1) == 1
        idx2.index_game(idx2.next_pending())
        assert idx2.reconcile(100) == 1
        drain(resumed)
        assert idx2.reconcile() == 0
        assert not PositionQueries(resumed._conn).coverage()["partial_index"]
    # Missing work behind any previous lexicographic progress is rediscovered.
    with lib._conn:
        lib._conn.execute("DELETE FROM game_position_index WHERE game_id=(SELECT min(game_id) FROM games)")
    assert idx.reconcile(1) == 1
    drain(lib)
    assert lib._conn.execute("SELECT count(*) FROM position_occurrences").fetchone()[0] == 25


def test_concurrent_initialization_and_indexers(tmp_path):
    db = str(tmp_path / "concurrent.db")
    def opening(_):
        with GameLibrary(db) as l:
            return l._conn.execute("SELECT count(*) FROM position_index_meta").fetchone()[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(opening, range(8))) == [1]*8
    with GameLibrary(db) as l:
        g, _ = l.add_game(pgn("repetition"))
    barrier = threading.Barrier(2)
    def indexing(_):
        with GameLibrary(db) as l:
            idx = PositionIndex(l._conn)
            barrier.wait(timeout=5)
            return idx.index_game(g.game_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(indexing, range(2)))
    assert sorted(results) == ["COMPLETE", "SKIPPED"]
    with GameLibrary(db) as l:
        assert l._conn.execute("SELECT count(*) FROM position_occurrences").fetchone()[0] == 9
        assert l._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert l._conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_is_additive_idempotent_and_version_checked(lib):
    g, _ = lib.add_game(pgn("repetition"))
    with lib._conn:
        for table in ("position_occurrences", "game_position_index", "positions", "position_index_meta"):
            lib._conn.execute("DROP TABLE " + table)
    old_version = lib._conn.execute("PRAGMA user_version").fetchone()[0]
    initialize(lib._conn)
    initialize(lib._conn)
    assert lib.get_game(g.game_id).pgn == pgn("repetition").strip()
    assert lib._conn.execute("PRAGMA user_version").fetchone()[0] == old_version
    assert PositionQueries(lib._conn).coverage()["missing_games"] == 1
    with lib._conn:
        lib._conn.execute("UPDATE position_index_meta SET identity_version=999")
    with pytest.raises(ValueError, match="Unsupported"):
        initialize(lib._conn)


def test_schema_creation_rolls_back_on_error(lib):
    with lib._conn:
        for table in ("position_occurrences", "game_position_index", "positions", "position_index_meta"):
            lib._conn.execute("DROP TABLE " + table)
    with patch("src.position_schema.DDL", DDL + ("INVALID SQL",)):
        with pytest.raises(sqlite3.OperationalError):
            initialize(lib._conn)
    assert lib._conn.execute("SELECT name FROM sqlite_master WHERE name='positions'").fetchone() is None
    initialize(lib._conn)


def test_concurrent_queue_heartbeat_completion_and_snapshot(lib):
    from src.analysis_queue import AnalysisQueue
    games = [lib.add_game(pgn("repetition").replace('"Repeat"', f'"R{i}"'))[0] for i in range(15)]
    queue = AnalysisQueue(lib)
    job, _ = queue.enqueue(games[0].game_id)
    barrier = threading.Barrier(3)
    def indexing():
        with GameLibrary(lib.db_path) as l:
            barrier.wait(timeout=5)
            drain(l)
    def analysis():
        with GameLibrary(lib.db_path) as l:
            q = AnalysisQueue(l)
            j = q.claim_next_job("position-test", lease_seconds=30)
            barrier.wait(timeout=5)
            for _ in range(15):
                q.heartbeat(j["job_id"], j["claim_token"])
                l.record_worker_heartbeat("position-test", current_job_id=j["job_id"])
            q.complete_job_with_analysis(j["job_id"], j["claim_token"], {"moves": [], "summary": {}})
    def reading():
        with GameLibrary(lib.db_path) as l:
            barrier.wait(timeout=5)
            for _ in range(30):
                l._conn.execute("BEGIN")
                try:
                    pos = PositionQueries(l._conn).find(chess.STARTING_FEN)
                    assert pos["occurrence_count"] == pos["distinct_game_count"]*3
                finally:
                    l._conn.rollback()
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(f) for f in (indexing, analysis, reading)]
        for f in futures:
            f.result(timeout=15)
    assert queue.get_job(job["job_id"])["status"] == "COMPLETED"
    assert lib.get_analysis(games[0].game_id) is not None
    assert not PositionQueries(lib._conn).coverage()["partial_index"]


def test_different_outcomes_exclude_unfinished_and_unknown(lib):
    for i, result in enumerate(["1-0", "0-1", "1/2-1/2", "*", "unexpected"]):
        g, _ = lib.add_game(f'[White "P{i}"]\n[Black "P{i}"]\n[Result "*"]\n\n1. e4 *')
        # The legacy library can contain unconventional result values; queries
        # must not quietly put these in the completed-game denominator.
        with lib._conn:
            lib._conn.execute("UPDATE games SET result=? WHERE game_id=?", (result, g.game_id))
    drain(lib)
    q = PositionQueries(lib._conn)
    pid = q.find(chess.STARTING_FEN)["position_id"]
    move = q.moves(pid)["items"][0]
    assert [move[k] for k in ("white_wins", "black_wins", "draws", "unfinished", "unknown_result")] == [1]*5
    assert move["completed_games"] == 3
    assert move["white_win_rate"] == 1/3 and move["white_score_rate"] == 0.5
    # A game with both matching names is not double-counted by the OR filter.
    both = PositionQueries(lib._conn, "P0").find(chess.STARTING_FEN)
    assert both["distinct_game_count"] == both["occurrence_count"] == 1


def test_deleted_shared_game_does_not_remove_other_occurrences(lib):
    a, _ = lib.add_game(pgn("transposition_a"))
    b, _ = lib.add_game(pgn("transposition_b"))
    drain(lib)
    pid = PositionQueries(lib._conn).find(TRANSPOSE)["position_id"]
    lib.delete_game(a.game_id)
    q = PositionQueries(lib._conn)
    assert q.summary(pid)["occurrence_count"] == 1
    assert q.games(pid)["items"][0]["game_id"] == b.game_id
    assert q.moves(pid)["items"][0]["black_wins"] == 1


def test_snapshot_retains_consistent_coverage_across_publish(lib):
    g, _ = lib.add_game(pgn("repetition"))
    lib._conn.execute("BEGIN")
    try:
        q = PositionQueries(lib._conn)
        assert q.coverage()["pending_games"] == 1
        with GameLibrary(lib.db_path) as other:
            PositionIndex(other._conn).index_game(g.game_id)
        assert q.find(chess.STARTING_FEN)["position_id"] is None
        assert q.coverage()["pending_games"] == 1
    finally:
        lib._conn.rollback()
    assert PositionQueries(lib._conn).find(chess.STARTING_FEN)["occurrence_count"] == 3


def test_bounded_busy_retry_and_state_recheck(lib):
    g, _ = lib.add_game(pgn("repetition"))
    with GameLibrary(lib.db_path) as writer:
        writer._conn.execute("BEGIN IMMEDIATE")
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                PositionIndex(lib._conn).index_game(g.game_id)
        finally:
            writer._conn.rollback()
    assert not lib._conn.in_transaction
    assert lib._conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    assert PositionIndex(lib._conn).next_pending() == g.game_id
    assert PositionIndex(lib._conn).index_game(g.game_id) == "COMPLETE"


def test_game_changed_during_parse_is_not_published(lib):
    g, _ = lib.add_game(pgn("repetition"))
    def changing_parse(text):
        rows = prepare_game(text)
        with GameLibrary(lib.db_path) as other:
            with other._conn:
                other._conn.execute("UPDATE games SET pgn=pgn || ' ' WHERE game_id=?", (g.game_id,))
        return rows
    with patch("src.positions.prepare_game", side_effect=changing_parse):
        assert PositionIndex(lib._conn).index_game(g.game_id) == "STALE"
    assert lib._conn.execute("SELECT count(*) FROM position_occurrences").fetchone()[0] == 0
    assert PositionIndex(lib._conn).index_game(g.game_id) == "COMPLETE"


def test_indexer_rejects_disk_connection_without_wal(lib):
    assert lib._conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    with pytest.raises(RuntimeError, match="WAL"):
        PositionIndex(lib._conn)
