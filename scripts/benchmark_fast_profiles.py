"""Segment 15 selected-player benchmarks; temporary databases, no engines/network."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts.benchmark_positions import synthetic_pgn, percentiles, rss_mib


def corpus_game(number):
    import chess.pgn
    game = chess.pgn.read_game(io.StringIO(synthetic_pgn(number)))
    game.headers['White'] = 'Hero'
    game.headers['WhiteElo'] = str(1300+number % 700)
    game.headers['BlackElo'] = str(1400+number % 650)
    tc = ['300+5', '600', '300+5', '40/7200:3600', '?'][number % 5]
    game.headers['TimeControl'] = tc
    clocks = [300.0 if tc == '300+5' else 600.0]*2
    board = game.board()
    for ply, node in enumerate(game.mainline()):
        who = int(board.turn)
        spent = float((number+ply) % 8)
        clocks[who] += (5 if tc == '300+5' else 0)-spent
        seconds = max(0, clocks[who]); m, s = divmod(seconds, 60)
        if ply % 11:
            node.comment = f'[%clk 0:{int(m):02}:{s:05.2f}]'
        if ply % 4 == 0:
            node.comment += f' [%emt 0:00:{spent:05.2f}]'
        board.push(node.move)
    return game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=True))


def request(client, **extra):
    response = client.get('/api/players/fast-profile', params={'source': 'benchmark', 'username': 'Hero', 'clock_convention': 'post_increment', **extra})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['completeness']['replay_complete'] and not data['completeness']['budget_exhausted'], data['completeness']
    assert all(section in data for section in ('overview', 'trends', 'repertoire', 'time_management', 'move_tendencies', 'positions'))
    return data, len(response.content)


def run_child(directory, count, samples, dataset="synthetic"):
    from src.library import GameLibrary
    from src.positions import PositionIndex
    from src.fast_profile import build_fast_profile, profile_reader, opening_book
    from src.fast_profile_extract import extract_game
    from src.analysis_queue import AnalysisQueue
    from fastapi.testclient import TestClient
    progress_path = Path(directory)/'progress.json'
    def progress(phase, **values):
        previous = json.loads(progress_path.read_text()) if progress_path.exists() else {}
        previous.update(values); previous['phase'] = phase
        progress_path.write_text(json.dumps(previous))
    path_object = Path(directory)/'profiles.db'
    if path_object.exists() or path_object.is_symlink():
        raise ValueError('Benchmark requires a fresh database; existing files/aliases are forbidden')
    path = str(path_object)
    os.environ['CHESS_COACH_DB'] = path
    from api import main as api
    from api.import_manager import ImportManager
    api.DB_PATH = path
    mgr = ImportManager(path); api.set_management_deps(path, mgr)
    client = TestClient(api.app)
    started = time.perf_counter(); digest = hashlib.sha256(); generation = insertion = 0.0
    ids = []
    first_text = None
    clock_settings = {'clock_convention': 'post_increment' if dataset == 'synthetic' else 'unverified'}
    local_files = sorted((ROOT/'data/games').glob('*.pgn'))
    with GameLibrary(path) as lib:
        for i in range(count):
            t = time.perf_counter(); text = corpus_game(i) if dataset == 'synthetic' else re.sub(r'(?m)^\[White "[^"]*"\]', '[White "Hero"]', local_files[i].read_text()); generation += time.perf_counter()-t
            if first_text is None: first_text = text
            digest.update(text.encode())
            t = time.perf_counter(); g, new = lib.add_game(text, source='benchmark'); insertion += time.perf_counter()-t
            assert new; ids.append(g.game_id)
        t = time.perf_counter(); idx = PositionIndex(lib._conn)
        while gid := idx.next_pending(): assert idx.index_game(gid) == 'COMPLETE'
        preprocessing = time.perf_counter()-t
        base_games = lib.count_games()
    progress('import_and_index_complete', generation_seconds=generation, import_seconds=insertion, position_preprocessing_seconds=preprocessing)
    before = Path(path).read_bytes(); storage_before = len(before)
    # First full HTTP request in this fresh child includes opening book init.
    t = time.perf_counter(); cold, size = request(client, **clock_settings); cold_seconds = time.perf_counter()-t
    progress('initial_report_complete', initial_complete_http_seconds=cold_seconds, eligible_games=cold['coverage']['eligible_games'])
    assert cold['coverage']['eligible_games'] == cold['coverage']['replayed_games'] == count
    assert cold['coverage']['position_index']['complete_games'] == count
    assert Path(path).read_bytes() == before
    warm = []
    # Predeclared sample count is chosen by caller, not silently shortened.
    for _ in range(samples):
        t = time.perf_counter(); request(client, **clock_settings); warm.append(time.perf_counter()-t)
    progress('warm_reports_complete', warm_complete_http=percentiles(warm))
    t = time.perf_counter()
    with profile_reader(path) as conn:
        pure = build_fast_profile(conn, 'benchmark', 'Hero', section='tendencies', clock_convention=clock_settings['clock_convention'])
    tendencies_seconds = time.perf_counter()-t
    # Parsing plus the analytics walk, excluding metadata/API/positions/book replay.
    t = time.perf_counter(); plies = 0
    with profile_reader(path) as conn:
        for row in conn.execute('SELECT pgn,time_control FROM games WHERE source=?', ('benchmark',)):
            fact = extract_game(row['pgn'], 'white', row['time_control'], clock_convention=clock_settings['clock_convention'])
            plies += fact['plies']
    extraction_seconds = time.perf_counter()-t
    reads_unchanged = Path(path).read_bytes() == before
    assert reads_unchanged
    storage_after_reads = Path(path).stat().st_size
    progress('extraction_complete', parse_and_extract_seconds=extraction_seconds)
    incremental = []
    for added in (1, 100):
        t = time.perf_counter()
        with GameLibrary(path) as lib:
            for i in range(added): lib.add_game(corpus_game(100000+count*100+i+sum(x['added'] for x in incremental)), source='benchmark')
        import_s = time.perf_counter()-t
        t = time.perf_counter(); data, _ = request(client, **clock_settings); report_s = time.perf_counter()-t
        incremental.append({'added': added, 'import_seconds': import_s, 'next_complete_report_seconds': report_s,
                            'selected_games': data['coverage']['eligible_games'], 'position_index_partial': data['positions']['coverage']['partial_index']})
    progress('incremental_complete', incremental=incremental)
    # Restore index coverage before contention. No statistical preprocessing exists.
    with GameLibrary(path) as lib:
        idx = PositionIndex(lib._conn)
        while gid := idx.next_pending(): idx.index_game(gid)
        q = AnalysisQueue(lib); job, _ = q.enqueue(ids[0])
    barrier = threading.Barrier(5); done = threading.Event(); heartbeat = []
    def importer():
        with GameLibrary(path) as lib:
            barrier.wait(timeout=10)
            try:
                for i in range(30):
                    lib.add_game(corpus_game(900000+count*100+i), source='benchmark')
                    time.sleep(.02)
            finally: done.set()
    def indexer():
        with GameLibrary(path) as lib:
            idx = PositionIndex(lib._conn); barrier.wait(timeout=10)
            while not done.is_set() or idx.next_pending():
                gid = idx.next_pending()
                if gid: idx.index_game(gid)
                else: time.sleep(.005)
    def queue():
        with GameLibrary(path) as lib:
            q = AnalysisQueue(lib); j = q.claim_next_job('profile-benchmark', lease_seconds=60)
            barrier.wait(timeout=10)
            for _ in range(20):
                t = time.perf_counter(); q.heartbeat(j['job_id'], j['claim_token']); lib.record_worker_heartbeat('profile-benchmark', current_job_id=j['job_id']); heartbeat.append(time.perf_counter()-t)
                time.sleep(.02)
            q.complete_job_with_analysis(j['job_id'], j['claim_token'], {'moves': [], 'summary': {}})
    def reader():
        barrier.wait(timeout=10); active = not done.is_set(); t = time.perf_counter()
        data, _ = request(client, **clock_settings)
        return {'seconds': time.perf_counter()-t, 'overlapped_active_import': active,
                'selected_games': data['coverage']['eligible_games'], 'wal_bytes_after': Path(path+'-wal').stat().st_size if Path(path+'-wal').exists() else 0}
    with ThreadPoolExecutor(5) as pool:
        fs = [pool.submit(f) for f in (importer, indexer, queue, reader, reader)]
        results = [f.result() for f in fs]
    progress('concurrency_complete', concurrent_readers=results[-2:])
    with GameLibrary(path) as lib:
        assert lib.count_games() == base_games+131
        assert AnalysisQueue(lib).get_job(job['job_id'])['status'] == 'COMPLETED'
        assert lib._conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not lib._conn.execute('PRAGMA foreign_key_check').fetchall()
        pragmas = {k: lib._conn.execute('PRAGMA '+k).fetchone()[0] for k in ('journal_mode', 'synchronous', 'page_size', 'cache_size')}
        t = time.perf_counter(); lib.add_game(first_text, source='benchmark'); duplicate_s = time.perf_counter()-t
        lib.delete_game(ids[-1]); lib.update_game_metadata(ids[0], white_rating=2001, time_control='600')
    t = time.perf_counter(); changed, _ = request(client, **clock_settings); mutation_report_s = time.perf_counter()-t
    assert changed['coverage']['eligible_games'] == count+130
    mgr.shutdown(); api.import_manager.shutdown()
    return {'selected_games': count, 'dataset': 'deterministic_synthetic_selected_white_player' if dataset == 'synthetic' else 'checked_in_PGNs_with_normalized_white_identity', 'seed': 14000,
            'dataset_sha256': digest.hexdigest(), 'generation_seconds': generation, 'import_seconds': insertion,
            'position_preprocessing_seconds': preprocessing, 'statistical_preprocessing_seconds': 0,
            'initial_complete_http_seconds': cold_seconds, 'target_under_10_seconds': cold_seconds < 10,
            'warm_complete_http': percentiles(warm), 'warm_samples': samples, 'response_bytes': size,
            'tendencies_complete_seconds': tendencies_seconds, 'parse_and_extract_seconds': extraction_seconds,
            'parse_and_extract_plies_per_second': plies/extraction_seconds, 'total_mainline_plies': plies,
            'reported_player_moves': cold['move_tendencies']['played_moves']['value'],
            'clock_move_coverage': cold['coverage']['clock_moves'], 'elapsed_move_coverage': cold['time_management']['usable_duration_moves'],
            'database_bytes_before_reads': storage_before, 'database_unchanged_by_reads': reads_unchanged,
            'statistical_storage_overhead_bytes': storage_after_reads-storage_before, 'initial_read_bytes_verified_unchanged': True,
            'incremental': incremental, 'duplicate_import_seconds_including_generation': duplicate_s,
            'delete_and_metadata_update_report_seconds': mutation_report_s,
            'concurrent': {'readers': results[-2:], 'complete_report_latency': percentiles([v['seconds'] for v in results[-2:]]), 'heartbeat': percentiles(heartbeat), 'queue_completed': True, 'background_errors': []},
            'peak_process_rss_mib': rss_mib(), 'pragmas': pragmas,
            'elapsed_seconds': time.perf_counter()-started}


def supervised(count, samples, seconds=120, rss_limit=512, dataset="synthetic"):
    with tempfile.TemporaryDirectory(prefix='segment15-profile-benchmark-') as directory:
        output = Path(directory)/'result.json'
        with tempfile.TemporaryFile(mode='w+') as log:
            p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--child', directory, '--scales', str(count), '--samples', str(samples), '--dataset', dataset, '--output', str(output)], stdout=log, stderr=log, start_new_session=True)
            start = time.monotonic(); reason = None; peak = 0
            try:
                while p.poll() is None:
                    if time.monotonic()-start > seconds: reason = 'runtime budget exceeded'; break
                    result = subprocess.run(['ps', '-o', 'rss=', '-p', str(p.pid)], capture_output=True, text=True, timeout=1)
                    if result.returncode and p.poll() is None: raise RuntimeError('Cannot enforce RSS budget: '+result.stderr)
                    if result.stdout.strip(): peak = max(peak, int(result.stdout.strip())/1024)
                    if peak > rss_limit: reason = 'RSS budget exceeded'; break
                    time.sleep(.05)
            finally:
                if p.poll() is None: os.killpg(p.pid, signal.SIGKILL)
                p.wait(timeout=5)
            if reason or p.returncode:
                log.seek(0)
                return {'selected_games': count, 'failed': reason or f'exit {p.returncode}', 'diagnostic': log.read()[-5000:], 'sampled_peak_rss_mib': peak,
                        'partial_measurements': json.loads((Path(directory)/'progress.json').read_text()) if (Path(directory)/'progress.json').exists() else {}}
            result = json.loads(output.read_text()); result['supervisor_seconds'] = time.monotonic()-start
            if result['peak_process_rss_mib'] > rss_limit: result['failed'] = 'Peak RSS above budget'
            return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scales', type=int, nargs='+', default=[50, 1000, 5000]); p.add_argument('--samples', type=int, default=3)
    p.add_argument('--include-local', action='store_true'); p.add_argument('--dataset', default='synthetic', choices=['synthetic','local'], help=argparse.SUPPRESS)
    p.add_argument('--output', default='segment_15_fast_profiling_benchmarks.json'); p.add_argument('--child', help=argparse.SUPPRESS)
    args = p.parse_args()
    if not 1 <= args.samples <= 30 or any(not 1 <= n <= 5000 for n in args.scales): p.error('1..30 samples and 1..5000 games required')
    if args.child:
        directory = Path(args.child).resolve()
        if directory.parent != Path(tempfile.gettempdir()).resolve() or not directory.name.startswith('segment15-profile-benchmark-'): p.error('Internal child must use a harness-created temporary directory')
        if Path(args.output).resolve() != directory/'result.json' or Path(args.output).exists() or Path(args.output).is_symlink():
            p.error('Internal child output must be a fresh result.json inside its temporary directory')
        Path(args.output).write_text(json.dumps(run_child(directory, args.scales[0], args.samples, args.dataset), indent=2)); return
    output = Path(args.output).resolve()
    if output.suffix != '.json' or output.is_relative_to(ROOT/'data'): p.error('Output must be a report JSON outside personal data')
    started = time.monotonic(); results = []
    tasks = ([('local', len(list((ROOT/'data/games').glob('*.pgn'))))] if args.include_local else []) + [('synthetic', n) for n in args.scales]
    for dataset, n in tasks:
        remaining = 600-(time.monotonic()-started)
        if remaining <= 0: raise SystemExit('600-second total budget exceeded')
        print(f'Benchmarking {n} selected-player games; {args.samples} complete warm samples', flush=True)
        result = supervised(n, args.samples, min(120, remaining), dataset=dataset); results.append(result)
        output.write_text(json.dumps({'environment': {'python': sys.version, 'sqlite': sqlite3.sqlite_version, 'platform': platform.platform(), 'cpu_count': os.cpu_count()},
                                     'budgets': {'per_scale_seconds': 120, 'total_seconds': 600, 'rss_mib': 512},
                                     'latency_method': 'nearest-rank p50/p95; full consolidated HTTP; no statistical cache', 'results': results}, indent=2))
        print(json.dumps({k: result.get(k) for k in ('selected_games', 'initial_complete_http_seconds', 'peak_process_rss_mib', 'elapsed_seconds', 'failed')}), flush=True)
        if result.get('failed'): raise SystemExit(1)


if __name__ == '__main__': main()
