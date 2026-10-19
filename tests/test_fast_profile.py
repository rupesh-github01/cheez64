from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from src.library import GameLibrary
from src.fast_profile import build_fast_profile, profile_reader, complete_date, month_key
from src.positions import PositionIndex


def pgn(white='Alice', black='Bob', result='*', day='2024.01.01', moves='1. e4 e5 *', tc='300+5', extra=''):
    return f'[White "{white}"]\n[Black "{black}"]\n[Result "{result}"]\n[Date "{day}"]\n[TimeControl "{tc}"]\n{extra}\n{moves}'


@pytest.fixture
def lib(tmp_path):
    with GameLibrary(str(tmp_path/'profile.db')) as l:
        yield l


def profile(lib, name='Alice', source='local', **kw):
    with profile_reader(lib.db_path) as conn:
        return build_fast_profile(conn, source, name, **kw)


def test_outcome_oracle_and_color(lib):
    # Player outcomes: W,W,L,D,unfinished,unknown, distributed over both colors.
    expected = ['wins', 'wins', 'losses', 'draws', 'unfinished', 'unknown']
    results = ['1-0', '0-1', '0-1', '1/2-1/2', '*', 'arbitrary']
    for i, result in enumerate(results):
        white = i % 2 == 0
        g, _ = lib.add_game(pgn('Alice' if white else f'Opp{i}', f'Opp{i}' if white else 'Alice', day=f'2024.01.{i+1:02}', result='*'))
        with lib._conn: lib._conn.execute('UPDATE games SET result=? WHERE game_id=?', (result, g.game_id))
    r = profile(lib)
    o = r['overview']['outcomes']
    assert o['total_games'] == 6 and o['completed_games'] == 4
    assert [o[k] for k in ['wins', 'losses', 'draws', 'unfinished', 'unknown']] == [2, 1, 1, 1, 1]
    assert o['win_rate']['value'] == 0.5 and o['score_rate']['value'] == 0.625
    assert o['win_rate']['sample']['preliminary']
    assert r['overview']['by_color']['white']['total_games'] == 3
    assert profile(lib, color='black')['overview']['outcomes']['wins'] == 1


def test_no_completed_results_are_insufficient(lib):
    lib.add_game(pgn())
    r = profile(lib)
    assert r['overview']['outcomes']['win_rate']['value'] is None
    assert r['overview']['outcomes']['score_rate']['status'] == 'INSUFFICIENT_DATA'
    assert r['time_management']['usable_duration_moves'] == 0
    assert r['time_management']['very_fast_move_fraction']['value'] is None


def test_exact_identity_source_and_both_sides(lib):
    lib.add_game(pgn(), source='local')
    lib.add_game(pgn('Alice2', day='2024.01.02'), source='local')
    lib.add_game(pgn('Alice', day='2024.01.03'), source='lichess')
    lib.add_game(pgn('ALICE', day='2024.01.04'), source='lichess')
    lib.add_game(pgn('Alice', 'Alice', day='2024.01.05'), source='local')
    assert profile(lib)['coverage']['eligible_games'] == 1
    assert profile(lib)['coverage']['ambiguous_identity_games'] == 1
    assert profile(lib, name='alice', source='lichess')['coverage']['eligible_games'] == 2
    with pytest.raises(KeyError): profile(lib, name='alice')
    with pytest.raises(KeyError): profile(lib, source='chess.com')
    with pytest.raises(ValueError): profile(lib, name=' Alice')


def test_metadata_dates_ratings_and_recent(lib):
    for i, day in enumerate(['2024.01.01', '2024.01.??', '2024.02.31', '????.??.??', '2024.??.??']):
        lib.add_game(pgn(day=day, white=f'Opp{i}', black='Alice', result='0-1', extra='[WhiteElo "1600"]\n[BlackElo "1500"]'))
    r = profile(lib, today=date(2024, 1, 20))
    assert r['coverage']['metadata']['valid_dates'] == 1
    assert r['coverage']['metadata']['month_dates'] == 2
    assert r['coverage']['metadata']['date_partial'] == 2
    assert r['coverage']['metadata']['date_invalid'] == 1
    assert r['trends']['recent_calendar']['total_games'] == 1
    assert r['trends']['monthly'][0]['total_games'] == 2
    assert r['overview']['ratings_by_time_control'][0]['median'] == 1500
    assert r['overview']['opponent_rating_bands'][0]['band'] == '1600-1799'
    assert complete_date('2024.02.31') is None and month_key('2024x01x01') is None
    filtered = profile(lib, date_from='2024-01-01', date_to='2024-01-31')
    assert filtered['coverage']['eligible_games'] == 1
    with pytest.raises(ValueError): profile(lib, date_from='2024-02-31')


def test_metadata_updates_duplicates_and_deletion(lib):
    text = pgn(extra='[WhiteElo "1500?"]')
    g, _ = lib.add_game(text)
    assert not lib.add_game(text.replace('e4', 'e4 {[%clk 0:04:59]}'))[1]
    r = profile(lib)
    assert r['coverage']['eligible_games'] == 1 and r['coverage']['clock_moves'] == 0
    assert r['coverage']['metadata']['raw_rating_warnings'] == 1
    lib.update_game_metadata(g.game_id, white_rating=1700, time_control='600')
    updated = profile(lib)
    assert updated['overview']['ratings_by_time_control'][0]['median'] == 1700
    assert updated['coverage']['metadata']['tc_conflicts'] == 1
    lib.delete_game(g.game_id)
    with pytest.raises(KeyError): profile(lib)


def test_partial_replay_does_not_truncate_metadata(lib):
    for i in range(3): lib.add_game(pgn(day=f'2024.01.{i+1:02}'))
    r = profile(lib, max_games=1)
    assert r['overview']['outcomes']['total_games'] == 3
    assert r['coverage']['processed_games'] == 1
    assert r['completeness']['budget_exhausted'] and not r['completeness']['replay_complete']
    assert r['move_tendencies']['played_moves']['status'] == 'PARTIAL'


def test_bad_pgn_and_unsupported_keep_metadata(lib):
    g, _ = lib.add_game(pgn())
    with lib._conn: lib._conn.execute('UPDATE games SET pgn=? WHERE game_id=?', ('1. -- *', g.game_id))
    lib.add_game(pgn(day='2024.01.02', extra='[Variant "Chess960"]'))
    r = profile(lib)
    assert r['overview']['outcomes']['total_games'] == 2
    assert r['coverage']['unsupported_games'] == r['coverage']['replay_failed_games'] == 1


def test_read_only_missing_index_and_no_engine(lib):
    lib.add_game(pgn())
    with lib._conn:
        for table in ('position_occurrences', 'game_position_index', 'positions', 'position_index_meta'):
            lib._conn.execute('DROP TABLE '+table)
    before = hashlib.sha256(Path(lib.db_path).read_bytes()).hexdigest()
    with patch('chess.engine.SimpleEngine.popen_uci', side_effect=AssertionError('engine forbidden')), \
         patch('src.library.GameLibrary.__init__', side_effect=AssertionError('migration forbidden')):
        r = profile(lib)
    assert not r['positions']['coverage']['schema_available']
    assert r['completeness']['replay_complete']
    assert hashlib.sha256(Path(lib.db_path).read_bytes()).hexdigest() == before


def test_index_scoping_repetitions_and_continuation_perspective(lib):
    # Repeat's initial position at plies 0/4/8; position after Nf3 at 1/5.
    text = (Path(__file__).parent/'data/positions/repetition.pgn').read_text()
    g, _ = lib.add_game(text, source='lichess')
    other, _ = lib.add_game(text.replace('"Cycle"', '"Other"'), source='chess.com')
    idx = PositionIndex(lib._conn)
    assert profile(lib, name='repeat', source='lichess')['positions']['coverage']['partial_index']
    for gid in (g.game_id, other.game_id): idx.index_game(gid)
    r = profile(lib, name='Cycle', source='lichess', top_k=50)
    assert r['positions']['coverage']['indexed_games'] == 1
    row = next(p for p in r['positions']['player_decisions'] if p['first_ply'] == 1)
    assert row['distinct_game_count'] == 1 and row['occurrence_count'] == 2
    continuation = next(p for p in r['positions']['continuations'] if p['position_id'] == row['position_id'])
    m = continuation['items'][0]
    assert m['occurrence_count'] == 2 and m['outcomes']['draws'] == 1
    assert m['outcomes']['score_rate']['value'] == .5
    assert m['move_frequency']['denominator'] == 2


def test_transpositions_opening_context(lib):
    folder = Path(__file__).parent/'data/positions'
    ids = [lib.add_game((folder/name).read_text(), source='local')[0].game_id for name in ('transposition_a.pgn', 'transposition_b.pgn')]
    idx = PositionIndex(lib._conn)
    for gid in ids: idx.index_game(gid)
    # Alice is White in one and Black in the other. Shared position must not
    # combine the opponent's continuation into the player's choice sample.
    r = profile(lib, top_k=50)
    shared = next(p for p in r['positions']['frequent_reached'] if p['first_ply'] == 6)
    assert shared['distinct_game_count'] == 2
    move = next(p for p in r['positions']['continuations'] if p['position_id'] == shared['position_id'])['items'][0]
    assert move['distinct_game_count'] == 1 and move['outcomes']['wins'] == 1


def test_snapshot_concurrent_writes_and_current_metadata(lib):
    g, _ = lib.add_game(pgn())
    with profile_reader(lib.db_path) as conn:
        assert build_fast_profile(conn, 'local', 'Alice', section='overview')['overview']['outcomes']['total_games'] == 1
        def mutate():
            with GameLibrary(lib.db_path) as other:
                other.add_game(pgn(day='2024.01.02'))
                other.delete_game(g.game_id)
        with ThreadPoolExecutor(1) as pool: pool.submit(mutate).result(timeout=5)
        r = build_fast_profile(conn, 'local', 'Alice')
        assert r['overview']['outcomes']['total_games'] == r['coverage']['replayed_games'] == 1
    assert profile(lib)['coverage']['eligible_games'] == 1
    assert lib._conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_coverage_route_does_not_invent_zero_clock_coverage(lib):
    lib.add_game(pgn())
    r = profile(lib, section='coverage')
    assert r['coverage']['clock_moves'] is None and not r['coverage']['replay_requested']
    assert r['completeness']['replay_complete'] is None


def test_indicators_have_heuristic_provenance_not_mistake_labels(lib):
    lib.add_game(pgn(moves='1. e4 e5 2. Qh5 Nc6 3. Qf3 Nf6 *'))
    r = profile(lib)
    tend = r['move_tendencies']
    assert tend['first_original_queen_move']['provenance'] == 'HEURISTIC'
    assert tend['event_frequencies']['checks']['provenance'] == 'OBSERVED'
    assert tend['queen_before_move6']['value'] == 1
    assert not any(word in json.dumps(tend).lower() for word in ('blunder', 'mistake', 'aggressive', 'passive', 'best_move'))


def test_bounded_opening_detection_preserves_existing_classification():
    import chess.pgn
    import io
    from scripts.benchmark_positions import synthetic_pgn
    from src.fast_profile import opening_book
    from src.fast_profile_extract import extract_game
    book, checksum, limit = opening_book()
    assert limit >= 25
    for i in range(70):
        text = synthetic_pgn(i)
        game = chess.pgn.read_game(io.StringIO(text))
        expected = book.detect_opening(game).to_dict()
        fact = extract_game(text, 'white', None, opening_db=book, opening_limit=limit)['opening']
        assert fact['confidence'] == expected['confidence']
        if expected['confidence'] != 'unrecognized':
            assert fact['name'] == expected['opening_name']
            assert fact['variation'] == expected['variation_name']
            assert fact['eco'] == expected['eco']


def test_actual_move_events_promotions_castling_ep_and_censoring():
    from src.fast_profile_extract import extract_game
    folder = Path(__file__).parent/'data/positions'
    promoted = extract_game((folder/'promotion.pgn').read_text(), 'white', None)
    assert promoted['counts']['promotions'] == promoted['counts']['pawn_moves'] == 1
    castle = extract_game((folder/'castling.pgn').read_text(), 'white', None)
    assert castle['castled'] and castle['counts']['castling_short'] == 1
    ep = '[SetUp "1"]\n[FEN "k7/8/8/3pP3/8/8/8/4K3 w - d6 0 1"]\n\n1. exd6 *'
    captured = extract_game(ep, 'white', None)
    assert captured['counts']['captures'] == 1 and captured['captures']['pawn'] == 1
    short = extract_game(pgn(), 'white', '300+5')
    assert short['development_at_ten'] is None and short['queen_first_move'] is None


def test_zero_move_and_oversized_record_limits(lib):
    lib.add_game(pgn(moves='*'))
    r = profile(lib)
    assert r['coverage']['replayed_games'] == 1
    assert r['move_tendencies']['event_frequencies']['captures']['value'] is None
    with lib._conn: lib._conn.execute('UPDATE games SET pgn=?', ('x'* (2*1024*1024+1),))
    assert profile(lib)['coverage']['replay_failed_games'] == 1


def test_missing_and_invalid_metadata_stay_separate(lib):
    g, _ = lib.add_game(pgn(day='????.??.??', tc='-'))
    with lib._conn: lib._conn.execute('UPDATE games SET white_rating=0,black_rating=NULL WHERE game_id=?', (g.game_id,))
    r = profile(lib)
    coverage = r['coverage']['metadata']
    assert coverage['date_missing'] == 1
    assert coverage['player_rating_invalid'] == coverage['opponent_rating_missing'] == 1
    assert coverage['time_control_unlimited'] == 1
    assert r['overview']['time_controls'][0]['time_control'] == 'Unlimited'


def test_no_1000_game_truncation_and_no_persistent_cache(lib):
    items = [(pgn(day=f'{2000+i//365:04}.{1+(i//28)%12:02}.{1+i%28:02}', black=f'Opp{i}'), 'local', None) for i in range(1005)]
    lib.add_games_batch(items)
    assert profile(lib, section='overview')['coverage']['eligible_games'] == 1005
    r = profile(lib)
    assert r['coverage']['replayed_games'] == 1005
    assert r['overview']['outcomes']['total_games'] == 1005
    with lib._conn: lib._conn.execute('DELETE FROM games WHERE game_id=(SELECT min(game_id) FROM games)')
    assert profile(lib, section='overview')['coverage']['eligible_games'] == 1004


def test_bounded_duration_distribution_keeps_all_mean_observations():
    from src.fast_profile import Samples
    samples = Samples()
    for _ in range(5000): samples.add(2)
    r = samples.report(games=100, eligible=5000)
    assert r['mean_seconds']['value'] == 2
    assert r['mean_seconds']['coverage']['observed'] == 5000
    assert r['median_seconds']['value'] is None and r['median_seconds']['status'] == 'UNSUPPORTED'
    assert sum(r['duration_bins_upper_inclusive_seconds'].values()) == 5000
    assert len(samples.values) == 4096


def test_all_derived_move_rules_have_explicit_provenance(lib):
    lib.add_game(pgn(moves='1. Nf3 Nf6 2. Ng1 Ng8 *'))
    r = profile(lib)
    assert r['move_tendencies']['repeated_piece_opening_moves']['provenance'] == 'HEURISTIC'
    assert r['move_tendencies']['repeated_piece_opening_moves']['value'] == 1
    assert r['repertoire']['classification_provenance'] == 'HEURISTIC'


def test_service_requires_readonly_snapshot(lib):
    lib.add_game(pgn())
    with pytest.raises(RuntimeError, match='query-only'):
        build_fast_profile(lib._conn, 'local', 'Alice')


def test_header_only_eco_is_reported_not_promoted_to_known_variation(lib):
    lib.add_game(pgn(moves='*', extra='[ECO "B90"]'))
    r = profile(lib)
    assert r['repertoire']['reported_eco_headers']['counts'] == {'B90': 1}
    assert r['repertoire']['openings'][0]['name'] == 'Unknown'
    assert r['repertoire']['openings'][0]['eco'] is None
