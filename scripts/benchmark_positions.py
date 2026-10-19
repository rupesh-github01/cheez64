"""Segment 14 isolated benchmarks. Never opens the personal database for SQL.

Parent supervises one fresh child per dataset, with hard elapsed/RSS budgets.
All SQLite files live under TemporaryDirectory. No engine or network access.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEEDS = [
    "e4 e5 Nf3 Nc6 Bb5 a6".split(),
    "e4 e5 Nf3 Nc6 Bc4 Bc5".split(),
    "e4 c5 Nf3 d6 d4 cxd4 Nxd4 Nf6".split(),
    "e4 e6 d4 d5 Nc3 Bb4".split(),
    "d4 d5 c4 e6 Nc3 Nf6".split(),
    "d4 Nf6 c4 g6 Nc3 Bg7".split(),
    "c4 e5 Nc3 Nf6 g3 d5".split(),
]


def synthetic_pgn(number):
    import chess
    import chess.pgn
    rng = random.Random(14000 + number)
    board = chess.Board()
    if number % 20 == 3:
        board = chess.Board("k7/4P3/8/8/8/8/8/4K3 w - - 0 1")
        line = ["e8=Q+"]
    elif number % 20 == 4:
        board = chess.Board("k7/8/8/3pP3/8/8/8/4K3 w - d6 0 1")
        line = ["exd6"]
    elif number % 20 == 5:
        board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
        line = ["O-O", "O-O-O"]
    elif number % 20 == 0:
        line = "Nf3 Nf6 Ng1 Ng8 Nf3 Nf6 Ng1 Ng8".split()
    elif number % 20 == 1:
        line = "Nf3 d5 g3 Nf6 Bg2 g6 O-O".split()
    elif number % 20 == 2:
        line = "g3 g6 Bg2 d5 Nf3 Nf6 O-O".split()
    else:
        line = SEEDS[number % len(SEEDS)]
    for san in line:
        board.push_san(san)
    if number % 20 not in (0, 1, 2, 3, 4, 5):
        for _ in range(max(0, 20 + number % 81 - len(line))):
            if board.is_game_over(claim_draw=False):
                break
            board.push(rng.choice(list(board.legal_moves)))
    game = chess.pgn.Game.from_board(board)
    game.headers.update({"White": f"Synthetic{number % 13}", "Black": f"Opponent{number % 17}",
                         "Event": f"Seed14000-{number}",
                         "Date": f"{2000 + number // 365:04d}.{1+(number//28)%12:02d}.{1+number%28:02d}",
                         "Result": ["1-0", "0-1", "1/2-1/2", "*"][number % 4]})
    return game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))


def percentiles(samples):
    ordered = sorted(samples)
    return {"samples": len(samples), "p50_ms": round(ordered[math.ceil(len(samples)*0.5)-1]*1000, 3),
            "p95_ms": round(ordered[math.ceil(len(samples)*0.95)-1]*1000, 3)}


def timed(operation, samples=200):
    for _ in range(20):
        operation()
    times = []
    for _ in range(samples):
        t = time.perf_counter()
        operation()
        times.append(time.perf_counter()-t)
    return percentiles(times)


def rss_mib():
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw / (1024*1024 if sys.platform == "darwin" else 1024)


def child_benchmark(directory, scale, dataset):
    # Configure before importing API: every dependency must refer to this temp DB.
    db_path = str(Path(directory) / "positions.db")
    os.environ["CHESS_COACH_DB"] = db_path
    from src.library import GameLibrary
    from src.positions import PositionIndex, PositionQueries, canonical_position
    from src.position_cli import safe_database
    from api import main as api
    from api.import_manager import ImportManager
    from fastapi.testclient import TestClient
    import chess

    safe_database(db_path)
    api.DB_PATH = db_path
    mgr = ImportManager(db_path)
    api.set_management_deps(db_path, mgr)
    client = TestClient(api.app)
    digest = hashlib.sha256()
    lengths = []
    generation_seconds = import_seconds = indexing_seconds = 0.0
    started = time.perf_counter()
    local_paths = sorted((ROOT / "data/games").glob("*.pgn"))
    ids = []
    with GameLibrary(db_path) as lib:
        for number in range(scale):
            t = time.perf_counter()
            text = local_paths[number].read_text() if dataset == "local" else synthetic_pgn(number)
            generation_seconds += time.perf_counter()-t
            digest.update(text.encode())
            t = time.perf_counter()
            game, new = lib.add_game(text)
            import_seconds += time.perf_counter()-t
            if new:
                ids.append(game.game_id)
                lengths.append(game.ply_count)
        plain_query = lambda: lib.list_games(limit=20)
        plain_idle = timed(plain_query)
        pragmas = {name: lib._conn.execute("PRAGMA " + name).fetchone()[0]
                   for name in ("journal_mode", "synchronous", "page_size", "busy_timeout", "cache_size")}
    size_before = Path(db_path).stat().st_size
    baseline_path = str(Path(directory) / "noindex-baseline.db")
    source = sqlite3.connect(db_path)
    destination = sqlite3.connect(baseline_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    api.DB_PATH = baseline_path
    baseline_mgr = ImportManager(baseline_path)
    api.set_management_deps(baseline_path, baseline_mgr)
    baseline_concurrent = concurrency_track(baseline_path, client, None, scale, with_positions=False)
    baseline_mgr.shutdown()
    api.DB_PATH = db_path
    api.set_management_deps(db_path, mgr)
    with GameLibrary(db_path) as lib:
        idx = PositionIndex(lib._conn)
        t = time.perf_counter()
        outcomes = {}
        while game_id := idx.next_pending():
            state = idx.index_game(game_id)
            outcomes[state] = outcomes.get(state, 0)+1
        indexing_seconds = time.perf_counter()-t
        q = PositionQueries(lib._conn)
        position_count = lib._conn.execute("SELECT count(*) FROM positions").fetchone()[0]
        occurrence_count = lib._conn.execute("SELECT count(*) FROM position_occurrences").fetchone()[0]
        assert occurrence_count == sum(lengths)+len(ids)
        assert outcomes == {"COMPLETE": len(ids)}
        initial = q.find(chess.STARTING_FEN)
        common = lib._conn.execute("SELECT canonical_fen FROM positions p JOIN position_occurrences o USING(position_id) WHERE o.ply=6 LIMIT 1").fetchone()[0]
        rare_fen = lib._conn.execute("""SELECT canonical_fen FROM positions p JOIN position_occurrences o USING(position_id)
            GROUP BY p.position_id HAVING count(*)=1 ORDER BY max(o.ply) DESC LIMIT 1""").fetchone()
        rare_fen = rare_fen[0] if rare_fen else common
        absent = "8/8/8/8/8/2k5/8/4K2R w - -"
        pid = initial["position_id"]
        # Raw key lookup isolates SQL from coverage/canonicalization cost.
        direct_lookup = lambda: lib._conn.execute("SELECT position_id FROM positions WHERE identity_version=1 AND variant='standard' AND canonical_fen=?", (initial["canonical_fen"],)).fetchone()
        first_t = time.perf_counter()
        direct_lookup()
        first_query_ms = (time.perf_counter()-first_t)*1000
        query_metrics = {
            "canonicalization": timed(lambda: canonical_position(chess.STARTING_FEN)),
            "raw_fen_sql": timed(direct_lookup),
            "lookup_hot_with_coverage": timed(lambda: q.find(chess.STARTING_FEN)),
            "lookup_common_with_coverage": timed(lambda: q.find(common)),
            "lookup_rare_with_coverage": timed(lambda: q.find(rare_fen)),
            "lookup_absent_with_coverage": timed(lambda: q.find(absent)),
            "position_games": timed(lambda: q.games(pid, limit=20)),
            "move_aggregation": timed(lambda: q.moves(pid)),
            "player_filtered": timed(lambda: PositionQueries(lib._conn, "Synthetic0").games(pid, limit=20)),
            "occurrence_page": timed(lambda: q.occurrences(pid, limit=20)),
            "http_lookup": timed(lambda: checked_get(client, "/api/positions/lookup", {"fen": chess.STARTING_FEN})),
        }
        query_plan = [list(row) for row in lib._conn.execute("""EXPLAIN QUERY PLAN SELECT count(*),count(DISTINCT game_id)
            FROM position_occurrences WHERE position_id=?""", (pid,))]
        aggregation_plan = [list(row) for row in lib._conn.execute("""EXPLAIN QUERY PLAN SELECT next_move_uci,game_id,count(*)
            FROM position_occurrences WHERE position_id=? GROUP BY next_move_uci,game_id""", (pid,))]
        t = time.perf_counter()
        # Reimport the exact deterministic corpus, streamed, not held in RAM.
        duplicates = 0
        for number in range(scale):
            text = local_paths[number].read_text() if dataset == "local" else synthetic_pgn(number)
            duplicates += not lib.add_game(text)[1]
        duplicate_seconds = time.perf_counter()-t
        t = time.perf_counter()
        idx.request_reindex(ids[0])
        idx.index_game(ids[0])
        reindex_seconds = time.perf_counter()-t
        coverage = q.coverage()
        active_sizes = {"main_bytes": Path(db_path).stat().st_size,
                        "wal_bytes": Path(db_path+"-wal").stat().st_size if Path(db_path+"-wal").exists() else 0}
        idle_indexed_games = timed(lambda: lib.list_games(limit=20))
    size_after = Path(db_path).stat().st_size
    concurrent = concurrency_track(db_path, client, pid, scale)
    with GameLibrary(db_path) as lib:
        integrity = lib._conn.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [list(r) for r in lib._conn.execute("PRAGMA foreign_key_check")]
        final_coverage = PositionQueries(lib._conn).coverage()
    mgr.shutdown()
    api.import_manager.shutdown()
    return {"dataset": dataset, "requested_games": scale, "distinct_games": len(ids),
            "dataset_sha256": digest.hexdigest(), "seed": 14000 if dataset == "synthetic" else None,
            "lengths": {"min": min(lengths), "max": max(lengths), "mean": sum(lengths)/len(lengths)},
            "generation_seconds": generation_seconds, "import_seconds": import_seconds,
            "indexing_seconds": indexing_seconds, "outcomes": outcomes,
            "games_per_second": len(ids)/indexing_seconds, "occurrences_per_second": occurrence_count/indexing_seconds,
            "plies_per_second": (occurrence_count-len(ids))/indexing_seconds,
            "unique_positions": position_count, "occurrences": occurrence_count,
            "unique_to_occurrence_ratio": position_count/occurrence_count,
            "db_before_bytes": size_before, "db_after_bytes": size_after,
            "index_growth_bytes": size_after-size_before,
            "growth_bytes_per_occurrence": (size_after-size_before)/occurrence_count,
            "active_sizes": active_sizes, "peak_rss_mib": rss_mib(),
            "first_raw_lookup_ms_process_warm": first_query_ms,
            "query_metrics": query_metrics, "plain_list_idle_before_index": plain_idle,
            "plain_list_idle_after_index": idle_indexed_games,
            "duplicate_reimport_count": duplicates, "duplicate_reimport_seconds_including_generation": duplicate_seconds,
            "single_game_reindex_seconds": reindex_seconds,
            "pragmas": pragmas, "query_plan": query_plan, "aggregation_plan": aggregation_plan,
            "coverage_before_concurrency": coverage, "concurrent": concurrent,
            "noindex_concurrent": baseline_concurrent,
            "final_coverage": final_coverage, "integrity_check": integrity, "foreign_key_check": foreign_keys,
            "elapsed_seconds": time.perf_counter()-started}


def checked_get(client, route, params=None):
    response = client.get(route, params=params)
    if response.status_code != 200:
        raise AssertionError(f"{route}: {response.status_code}: {response.text}")
    return response


def concurrency_track(db_path, client, pid, scale, with_positions=True):
    from src.library import GameLibrary
    from src.positions import PositionIndex, PositionQueries
    from src.analysis_queue import AnalysisQueue
    # Three background workers and four API readers use independent connections.
    start = threading.Barrier(7)
    imported = threading.Event()
    heartbeats = []
    import_count = 120
    with GameLibrary(db_path) as lib:
        before = lib.count_games()
        job, _ = AnalysisQueue(lib).enqueue(lib.list_games(limit=1)[0].game_id)
    def writer():
        with GameLibrary(db_path) as lib:
            start.wait(timeout=10)
            try:
                for i in range(import_count):
                    lib.add_game(synthetic_pgn(100000+scale*100+i))
                    time.sleep(0.03)
            finally:
                imported.set()
        return import_count
    def indexer():
        count = 0
        with GameLibrary(db_path) as lib:
            idx = PositionIndex(lib._conn)
            start.wait(timeout=10)
            if not with_positions:
                imported.wait(timeout=30)
                return 0
            while not imported.is_set() or idx.next_pending():
                if game_id := idx.next_pending():
                    idx.index_game(game_id)
                    count += 1
                else:
                    time.sleep(0.002)
        return count
    def queue_writer():
        with GameLibrary(db_path) as lib:
            q = AnalysisQueue(lib)
            claimed = q.claim_next_job("benchmark", lease_seconds=60)
            start.wait(timeout=10)
            for _ in range(30):
                t = time.perf_counter()
                q.heartbeat(claimed["job_id"], claimed["claim_token"])
                lib.record_worker_heartbeat("benchmark", current_job_id=claimed["job_id"])
                heartbeats.append(time.perf_counter()-t)
                time.sleep(0.002)
            q.complete_job_with_analysis(claimed["job_id"], claimed["claim_token"], {"moves": [], "summary": {}})
        return True
    def reader():
        start.wait(timeout=10)
        routes = [("games_http", "/api/games?limit=20")]
        if with_positions:
            routes += [("lookup_http", f"/api/positions/{pid}"), ("moves_http", f"/api/positions/{pid}/moves")]
        values = {key: [] for key, _ in routes}
        active = {key: [] for key, _ in routes}
        backlog = []
        for _ in range(50):
            for key, route in routes:
                during_import = not imported.is_set()
                t = time.perf_counter()
                response = checked_get(client, route)
                duration = time.perf_counter()-t
                values[key].append(duration)
                if during_import and not imported.is_set():
                    active[key].append(duration)
                if key == "lookup_http":
                    backlog.append(response.json()["coverage"]["pending_games"])
        return {"values": values, "active": active, "backlog": backlog}
    with ThreadPoolExecutor(max_workers=7) as pool:
        fs = [pool.submit(writer), pool.submit(indexer), pool.submit(queue_writer)]
        reads = [pool.submit(reader) for _ in range(4)]
        results = [f.result() for f in fs]  # failures propagate, never disappear
        collected = [f.result() for f in reads]
    with GameLibrary(db_path) as lib:
        assert lib.count_games() == before+import_count
        if with_positions:
            assert not PositionQueries(lib._conn).coverage()["partial_index"]
        assert AnalysisQueue(lib).get_job(job["job_id"])["status"] == "COMPLETED"
    metrics = {key: percentiles([v for sample in collected for v in sample["values"][key]]) for key in collected[0]["values"]}
    active_metrics = {}
    for key in metrics:
        samples = [v for sample in collected for v in sample["active"][key]]
        active_metrics[key] = percentiles(samples) if samples else {"samples": 0}
    backlog = [v for sample in collected for v in sample["backlog"]]
    return {"games_before": before, "games_after": before+import_count,
            "imported": results[0], "indexed": results[1], "queue_completed": results[2],
            "reader_threads": 4, "errors": [], "latencies": metrics,
            "latencies_during_active_import": active_metrics,
            "observed_max_pending_games": max(backlog, default=0),
            "heartbeat_write": percentiles(heartbeats)}


def supervise(scale, dataset, max_seconds, max_mib):
    with tempfile.TemporaryDirectory(prefix="segment14-benchmark-") as directory:
        output = Path(directory) / "result.json"
        command = [sys.executable, str(Path(__file__).resolve()), "--child-dir", directory,
                   "--child-scale", str(scale), "--child-dataset", dataset, "--output", str(output)]
        with tempfile.TemporaryFile(mode="w+") as log:
            child = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=True)
            started = time.monotonic()
            failure = None
            peak_sample = 0
            try:
                while child.poll() is None:
                    elapsed = time.monotonic()-started
                    if elapsed > max_seconds:
                        failure = f"Exceeded {max_seconds}s wall budget"
                        break
                    result = subprocess.run(["ps", "-o", "rss=", "-p", str(child.pid)], capture_output=True, text=True, timeout=1)
                    if result.returncode and child.poll() is None:
                        raise RuntimeError("Unable to sample child RSS: " + result.stderr)
                    if result.stdout.strip():
                        peak_sample = max(peak_sample, int(result.stdout.strip())/1024)
                    if peak_sample > max_mib:
                        failure = f"Exceeded {max_mib} MiB RSS budget"
                        break
                    time.sleep(0.05)
            finally:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            if failure or child.returncode:
                log.seek(0)
                return {"dataset": dataset, "requested_games": scale, "failed": failure or f"exit {child.returncode}",
                        "diagnostic": log.read()[-4000:], "sampled_peak_rss_mib": peak_sample}
            data = json.loads(output.read_text())
            data["supervisor_sampled_peak_rss_mib"] = peak_sample
            data["supervisor_wall_seconds"] = time.monotonic()-started
            if data["peak_rss_mib"] > max_mib:
                data["failed"] = "Peak RSS exceeded budget between supervisor samples"
            return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", nargs="+", type=int, default=[50, 1000, 5000])
    parser.add_argument("--include-local", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--max-rss-mib", type=float, default=512)
    parser.add_argument("--output", default="segment_14_position_benchmarks.json")
    parser.add_argument("--child-dir", help=argparse.SUPPRESS)
    parser.add_argument("--child-scale", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--child-dataset", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child_dir:
        # Internal children may only write SQLite inside a generated temp directory.
        directory = Path(args.child_dir).resolve()
        temporary_root = Path(tempfile.gettempdir()).resolve()
        if directory.parent != temporary_root or not directory.name.startswith("segment14-benchmark-"):
            parser.error("Child directory must be a harness-created temporary directory")
        Path(args.output).write_text(json.dumps(child_benchmark(directory, args.child_scale, args.child_dataset), indent=2))
        return
    if not 0 < args.max_seconds <= 120 or not 0 < args.max_rss_mib <= 512 or any(n < 1 or n > 5000 for n in args.scales):
        parser.error("Limits must stay within 120s, 512MiB and 1..5000 games")
    # Do not let an output argument overwrite a database or source file.
    output = Path(args.output).resolve()
    if output.suffix != ".json" or output in (ROOT / "data/library.db",):
        parser.error("Output must be a JSON report path")
    started = time.monotonic()
    results = []
    tasks = ([('local', len(list((ROOT / 'data/games').glob('*.pgn'))))] if args.include_local else [])
    tasks += [("synthetic", n) for n in args.scales]
    for dataset, scale in tasks:
        remaining = 600-(time.monotonic()-started)
        if remaining <= 0:
            results.append({"dataset": dataset, "requested_games": scale, "failed": "600s total deadline exhausted"})
            break
        print(f"Benchmarking {dataset}: {scale} games", flush=True)
        result = supervise(scale, dataset, min(args.max_seconds, remaining), args.max_rss_mib)
        results.append(result)
        report = {"environment": {"python": sys.version, "platform": platform.platform(),
                                   "machine": platform.machine(), "cpu_count": os.cpu_count(),
                                   "sqlite": sqlite3.sqlite_version},
                  "method": "nearest-rank p50/p95; 20 warmups + 200 samples; one run per scale; process RSS",
                  "budgets": {"seconds_per_scale": args.max_seconds, "rss_mib": args.max_rss_mib, "total_seconds": 600},
                  "results": results}
        output.write_text(json.dumps(report, indent=2))
        print(json.dumps({k: result.get(k) for k in ("dataset", "requested_games", "elapsed_seconds", "peak_rss_mib", "failed")}), flush=True)
        if result.get("failed"):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
