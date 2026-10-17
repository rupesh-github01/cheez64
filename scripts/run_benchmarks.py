"""
Personal Chess Coach — Segment 12 Benchmark Runner.

Measures:
1. PGN Import throughput (games/sec), peak memory, and SQLite database size
   across 50, 1,000, and 5,000 realistic distinct games.
2. API & query latencies (p50, p95) under both idle and concurrent background load.
3. Stockfish analysis performance across 5 representative games at depth 10, 12, and 14.
4. Enforces strict memory and runtime budgets.
"""

import os
import sys
import time
import math
import random
import tempfile
import threading
import tracemalloc
from pathlib import Path
from typing import List, Dict, Any, Tuple

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import chess
from starlette.testclient import TestClient
import api.main
from src.library import GameLibrary
from src.analyzer import analyze_game


# ---------------------------------------------------------------------------
# Distinct Realistic PGN Generator
# ---------------------------------------------------------------------------

OPENINGS_SEEDS = [
    # Ruy Lopez
    ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7", "Re1", "b5", "Bb3", "d6", "c3", "O-O"],
    # Italian Game
    ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5", "c3", "Nf6", "d3", "d6", "O-O", "a6", "Bb3", "Ba7", "Nbd2", "O-O"],
    # Sicilian Defense (Najdorf)
    ["e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6", "Be3", "e5", "Nb3", "Be6", "f3", "Be7"],
    # French Defense (Winawer)
    ["e4", "e6", "d4", "d5", "Nc3", "Bb4", "e5", "c5", "a3", "Bxc3+", "bxc3", "Ne7", "Qg4", "Qc7", "Qxg7", "Rg8"],
    # Caro-Kann Defense
    ["e4", "c6", "d4", "d5", "Nc3", "dxe4", "Nxe4", "Bf5", "Ng3", "Bg6", "h4", "h6", "Nf3", "Nd7", "h5", "Bh7"],
    # Queen's Gambit Declined
    ["d4", "d5", "c4", "e6", "Nc3", "Nf6", "Bg5", "Be7", "e3", "O-O", "Nf3", "h6", "Bh4", "b6", "cxd5", "Nxd5"],
    # King's Indian Defense
    ["d4", "Nf6", "c4", "g6", "Nc3", "Bg7", "e4", "d6", "Nf3", "O-O", "Be2", "e5", "O-O", "Nc6", "d5", "Ne7"],
    # English Opening
    ["c4", "e5", "Nc3", "Nf6", "Nf3", "Nc6", "g3", "d5", "cxd5", "Nxd5", "Bg2", "Nb6", "O-O", "Be7", "d3", "O-O"],
]

ECO_MAP = {
    0: ("C70", "Ruy Lopez"),
    1: ("C50", "Italian Game"),
    2: ("B90", "Sicilian Defense, Najdorf"),
    3: ("C18", "French Defense, Winawer"),
    4: ("B18", "Caro-Kann Defense"),
    5: ("D53", "Queen's Gambit Declined"),
    6: ("E92", "King's Indian Defense"),
    7: ("A28", "English Opening"),
}

PLAYERS_POOL = [
    "MagnusCarlsen", "HikaruNakamura", "FabianoCaruana", "AlirezaFirouzja",
    "DingLiren", "IanNepomniachtchi", "WesleySo", "LevonAronian",
    "AnishGiri", "ViswanathanAnand", "AliceWonder", "BobBuilder", "CharlieBrown"
]


def generate_distinct_pgn(index: int, target_plies: int = 40) -> str:
    """Generate a valid, distinct PGN with varied moves, headers, and openings."""
    rng = random.Random(index * 7919 + 42)
    opening_idx = index % len(OPENINGS_SEEDS)
    opening_moves = OPENINGS_SEEDS[opening_idx]
    eco_code, opening_name = ECO_MAP[opening_idx]

    board = chess.Board()
    for san in opening_moves:
        board.push_san(san)

    # Continue with legal random moves up to target_plies
    moves_left = max(10, target_plies - len(opening_moves))
    for _ in range(moves_left):
        if board.is_game_over():
            break
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            break
        chosen_move = rng.choice(legal_moves)
        board.push(chosen_move)

    # Determine result
    if board.is_checkmate():
        res = "1-0" if board.turn == chess.BLACK else "0-1"
    elif board.is_stalemate() or board.is_insufficient_material() or board.can_claim_draw():
        res = "1/2-1/2"
    else:
        res = rng.choice(["1-0", "0-1", "1/2-1/2"])

    white_player = PLAYERS_POOL[index % len(PLAYERS_POOL)]
    black_player = PLAYERS_POOL[(index + 3) % len(PLAYERS_POOL)]
    year = 2020 + (index % 6)
    month = 1 + (index % 12)
    day = 1 + (index % 28)
    date_str = f"{year}.{month:02d}.{day:02d}"

    # Export moves as PGN
    move_board = chess.Board()
    move_strings = []
    for i, move in enumerate(board.move_stack):
        san = move_board.san(move)
        if i % 2 == 0:
            move_strings.append(f"{(i // 2) + 1}. {san}")
        else:
            move_strings.append(san)
        move_board.push(move)

    moves_text = " ".join(move_strings) + f" {res}"

    pgn = f"""[Event "Benchmark Event {index}"]
[Site "PersonalChessCoach Engine"]
[Date "{date_str}"]
[Round "{index}"]
[White "{white_player}"]
[Black "{black_player}"]
[Result "{res}"]
[ECO "{eco_code}"]
[Opening "{opening_name}"]
[SourceGameId "bench_{index}_{year}"]

{moves_text}
"""
    return pgn


def calculate_percentiles(values: List[float]) -> Tuple[float, float]:
    """Calculate p50 and p95 latencies in milliseconds."""
    if not values:
        return 0.0, 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    p50_idx = min(int(n * 0.50), n - 1)
    p95_idx = min(int(math.ceil(n * 0.95)) - 1, n - 1)
    return round(sorted_v[p50_idx] * 1000, 2), round(sorted_v[p95_idx] * 1000, 2)


# ---------------------------------------------------------------------------
# Benchmark Suite
# ---------------------------------------------------------------------------

def run_import_and_query_benchmark(count: int, max_runtime_sec: float = 120.0) -> Dict[str, Any]:
    """
    Run import, memory, DB size, and latency benchmarks on a dataset of `count` games.
    """
    print(f"\n========================================================")
    print(f"--- Running Benchmark for Scale: {count} Games ---")
    print(f"========================================================")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        # Pre-generate games
        print(f"Generating {count} distinct games...")
        t_gen_start = time.perf_counter()
        games_pgn = [
            generate_distinct_pgn(i, target_plies=30 + (i % 30))
            for i in range(count)
        ]
        t_gen = time.perf_counter() - t_gen_start
        print(f"Generated {count} games in {t_gen:.2f}s")

        # 1. Measure Batch Import Throughput & Memory
        print(f"Importing {count} games into isolated DB {db_path}...")
        tracemalloc.start()
        t_import_start = time.perf_counter()

        with GameLibrary(db_path) as lib:
            imported = 0
            for i, pgn in enumerate(games_pgn):
                lib.add_game(pgn, source="benchmark", source_game_id=f"bench_{count}_{i}")
                imported += 1

        t_import = time.perf_counter() - t_import_start
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        throughput = imported / t_import if t_import > 0 else 0
        peak_mb = peak_mem / (1024 * 1024)
        db_size_mb = os.path.getsize(db_path) / (1024 * 1024)

        print(f"Imported {imported} games in {t_import:.2f}s ({throughput:.1f} games/s)")
        print(f"Peak Memory: {peak_mb:.2f} MB, DB File Size: {db_size_mb:.2f} MB")

        # Configure API TestClient
        orig_db = api.main.DB_PATH
        api.main.DB_PATH = Path(db_path)
        client = TestClient(api.main.app)

        # 2. Measure Idle Query Latencies (50 iterations each)
        print("Measuring Idle Query Latency (50 iterations)...")
        list_times = []
        filter_times = []
        summary_times = []
        dashboard_times = []

        with GameLibrary(db_path) as lib:
            for _ in range(50):
                t0 = time.perf_counter()
                lib.list_games(limit=20)
                list_times.append(time.perf_counter() - t0)

                t0 = time.perf_counter()
                lib.list_games(player="MagnusCarlsen", limit=20)
                filter_times.append(time.perf_counter() - t0)

        for _ in range(50):
            t0 = time.perf_counter()
            r = client.get("/api/management/summary")
            summary_times.append(time.perf_counter() - t0)
            assert r.status_code == 200

        for _ in range(50):
            t0 = time.perf_counter()
            r = client.get("/api/dashboard?player=MagnusCarlsen")
            dashboard_times.append(time.perf_counter() - t0)
            assert r.status_code == 200

        list_p50, list_p95 = calculate_percentiles(list_times)
        filter_p50, filter_p95 = calculate_percentiles(filter_times)
        sum_p50, sum_p95 = calculate_percentiles(summary_times)
        dash_p50, dash_p95 = calculate_percentiles(dashboard_times)

        print(f"Idle Latencies:")
        print(f"  - list_games(20):       p50 = {list_p50} ms, p95 = {list_p95} ms")
        print(f"  - list_games(filtered): p50 = {filter_p50} ms, p95 = {filter_p95} ms")
        print(f"  - /management/summary:  p50 = {sum_p50} ms, p95 = {sum_p95} ms")
        print(f"  - /dashboard:           p50 = {dash_p50} ms, p95 = {dash_p95} ms")

        # 3. Measure Concurrent Query Latencies
        print("Measuring Concurrent Query Latency under active background readers/writers...")
        stop_event = threading.Event()

        def bg_worker_reader():
            while not stop_event.is_set():
                try:
                    with GameLibrary(db_path) as bg_lib:
                        bg_lib.list_games(limit=50)
                except Exception:
                    pass
                time.sleep(0.01)

        def bg_worker_writer():
            w_idx = 900000
            while not stop_event.is_set():
                try:
                    with GameLibrary(db_path) as bg_lib:
                        pgn_dummy = generate_distinct_pgn(w_idx, target_plies=20)
                        bg_lib.add_game(pgn_dummy, source="bg_load", source_game_id=f"bg_{w_idx}")
                        w_idx += 1
                except Exception:
                    pass
                time.sleep(0.05)

        t1 = threading.Thread(target=bg_worker_reader, daemon=True)
        t2 = threading.Thread(target=bg_worker_writer, daemon=True)
        t1.start()
        t2.start()

        conc_list_times = []
        conc_sum_times = []
        conc_dash_times = []

        try:
            with GameLibrary(db_path) as lib:
                for _ in range(50):
                    t0 = time.perf_counter()
                    lib.list_games(limit=20)
                    conc_list_times.append(time.perf_counter() - t0)

            for _ in range(50):
                t0 = time.perf_counter()
                r = client.get("/api/management/summary")
                conc_sum_times.append(time.perf_counter() - t0)
                assert r.status_code == 200

            for _ in range(50):
                t0 = time.perf_counter()
                r = client.get("/api/dashboard?player=MagnusCarlsen")
                conc_dash_times.append(time.perf_counter() - t0)
                assert r.status_code == 200
        finally:
            stop_event.set()
            t1.join(timeout=2.0)
            t2.join(timeout=2.0)
            api.main.DB_PATH = orig_db

        c_list_p50, c_list_p95 = calculate_percentiles(conc_list_times)
        c_sum_p50, c_sum_p95 = calculate_percentiles(conc_sum_times)
        c_dash_p50, c_dash_p95 = calculate_percentiles(conc_dash_times)

        print(f"Concurrent Latencies:")
        print(f"  - list_games(20):       p50 = {c_list_p50} ms, p95 = {c_list_p95} ms")
        print(f"  - /management/summary:  p50 = {c_sum_p50} ms, p95 = {c_sum_p95} ms")
        print(f"  - /dashboard:           p50 = {c_dash_p50} ms, p95 = {c_dash_p95} ms")

        # Enforce budget checks
        assert peak_mb < 512.0, f"Exceeded peak memory budget: {peak_mb:.1f} MB >= 512 MB"
        assert t_import < max_runtime_sec, f"Exceeded import runtime budget: {t_import:.1f}s >= {max_runtime_sec}s"

        return {
            "scale": count,
            "import_time_sec": round(t_import, 2),
            "throughput_games_sec": round(throughput, 1),
            "peak_memory_mb": round(peak_mb, 2),
            "db_size_mb": round(db_size_mb, 2),
            "idle": {
                "list_games": (list_p50, list_p95),
                "list_games_filtered": (filter_p50, filter_p95),
                "management_summary": (sum_p50, sum_p95),
                "dashboard": (dash_p50, dash_p95),
            },
            "concurrent": {
                "list_games": (c_list_p50, c_list_p95),
                "management_summary": (c_sum_p50, c_sum_p95),
                "dashboard": (c_dash_p50, c_dash_p95),
            }
        }

    finally:
        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except OSError:
                pass


def run_stockfish_sample_benchmark(stockfish_path: str = "/opt/homebrew/bin/stockfish") -> List[Dict[str, Any]]:
    """
    Benchmark Stockfish analysis across 5 representative games at depth 10, 12, and 14.
    """
    print(f"\n========================================================")
    print(f"--- Running Stockfish Engine Benchmarks (5 Games) ---")
    print(f"========================================================")

    games_dir = REPO_ROOT / "data" / "games"
    sample_files = [f"game{i}.pgn" for i in range(1, 6)]
    sample_pgns = []
    for f in sample_files:
        p = games_dir / f
        if p.exists():
            sample_pgns.append((f, p.read_text(encoding="utf-8")))

    if not sample_pgns:
        print("Warning: Sample games not found in data/games/")
        return []

    depths = [10, 12, 14]
    results = []

    for depth in depths:
        print(f"\nEvaluating 5 games at depth {depth}...")
        total_time = 0.0
        total_moves = 0
        per_game = []

        for fname, pgn_content in sample_pgns:
            t0 = time.perf_counter()
            game, analyses = analyze_game(pgn_text=pgn_content, stockfish_path=stockfish_path, depth=depth)
            elapsed = time.perf_counter() - t0
            moves_count = len(analyses)
            total_time += elapsed
            total_moves += moves_count
            per_game.append({
                "game": fname,
                "moves": moves_count,
                "duration_sec": round(elapsed, 2),
                "sec_per_move": round(elapsed / moves_count, 3) if moves_count > 0 else 0
            })
            print(f"  - {fname}: {moves_count} moves in {elapsed:.2f}s ({elapsed/moves_count*1000:.1f} ms/move)")

        avg_sec_per_game = total_time / len(sample_pgns)
        avg_ms_per_move = (total_time / total_moves * 1000) if total_moves > 0 else 0

        res_entry = {
            "depth": depth,
            "total_games": len(sample_pgns),
            "total_moves": total_moves,
            "total_duration_sec": round(total_time, 2),
            "avg_sec_per_game": round(avg_sec_per_game, 2),
            "avg_ms_per_move": round(avg_ms_per_move, 1),
            "games_per_minute": round(60.0 / avg_sec_per_game, 2) if avg_sec_per_game > 0 else 0,
            "per_game": per_game,
        }
        results.append(res_entry)
        print(f"Summary (depth {depth}): Total {total_time:.2f}s | Avg {avg_sec_per_game:.2f}s/game | {avg_ms_per_move:.1f} ms/move | {res_entry['games_per_minute']} games/min")

    return results


def main():
    print("========================================================")
    print(" Personal Chess Coach — Segment 12 Benchmark Suite")
    print("========================================================")
    
    # 1. Scale Benchmarks: 50, 1000, 5000 games
    scale_50 = run_import_and_query_benchmark(50, max_runtime_sec=15.0)
    scale_1000 = run_import_and_query_benchmark(1000, max_runtime_sec=45.0)
    scale_5000 = run_import_and_query_benchmark(5000, max_runtime_sec=120.0)

    # 2. Stockfish Benchmarks
    sf_results = run_stockfish_sample_benchmark()

    # 3. Print Final Markdown Summary
    print("\n\n========================================================")
    print("FINAL BENCHMARK REPORT SUMMARY")
    print("========================================================\n")

    print("### 1. Database & Import Throughput Benchmark")
    print("| Scale | Import Time | Throughput | Peak Memory | DB File Size |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for s in [scale_50, scale_1000, scale_5000]:
        print(f"| {s['scale']} games | {s['import_time_sec']}s | {s['throughput_games_sec']} games/s | {s['peak_memory_mb']} MB | {s['db_size_mb']} MB |")

    print("\n### 2. Query & API Latencies (p50 / p95 in milliseconds)")
    print("| Metric | 50 Games (Idle) | 50 Games (Load) | 1,000 Games (Idle) | 1,000 Games (Load) | 5,000 Games (Idle) | 5,000 Games (Load) |")
    print("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    
    metrics = [
        ("list_games(limit=20)", "list_games"),
        ("/api/management/summary", "management_summary"),
        ("/api/dashboard?player=...", "dashboard"),
    ]
    for label, key in metrics:
        v50_i = f"{scale_50['idle'][key][0]} / {scale_50['idle'][key][1]}"
        v50_c = f"{scale_50['concurrent'][key][0]} / {scale_50['concurrent'][key][1]}"
        v1k_i = f"{scale_1000['idle'][key][0]} / {scale_1000['idle'][key][1]}"
        v1k_c = f"{scale_1000['concurrent'][key][0]} / {scale_1000['concurrent'][key][1]}"
        v5k_i = f"{scale_5000['idle'][key][0]} / {scale_5000['idle'][key][1]}"
        v5k_c = f"{scale_5000['concurrent'][key][0]} / {scale_5000['concurrent'][key][1]}"
        print(f"| {label} | {v50_i} | {v50_c} | {v1k_i} | {v1k_c} | {v5k_i} | {v5k_c} |")

    print("\n### 3. Stockfish Engine Performance (5 Representative Games)")
    print("| Depth | Total Duration | Avg Game Duration | Avg Move Duration | Throughput |")
    print("| :--- | :--- | :--- | :--- | :--- |")
    for r in sf_results:
        print(f"| Depth {r['depth']} | {r['total_duration_sec']}s | {r['avg_sec_per_game']}s/game | {r['avg_ms_per_move']} ms/move | {r['games_per_minute']} games/min |")

    print("\nBenchmark completed successfully.")


if __name__ == "__main__":
    main()
