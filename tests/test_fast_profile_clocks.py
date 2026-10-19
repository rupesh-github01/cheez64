import pytest
from src.fast_profile_extract import extract_game, annotation_seconds


def text(moves, tc='300+5', setup=''):
    return f'[White "Alice"]\n[Black "Bob"]\n[TimeControl "{tc}"]\n{setup}\n{moves}'


def test_increment_and_emt_convention():
    pgn = text('1. e4 {[%clk 0:04:57]} e5 {[%clk 0:04:59]} 2. Nf3 {[%clk 0:04:54]} *')
    assert extract_game(pgn, 'white', '300+5')['durations'] == {}
    f = extract_game(pgn, 'white', '300+5', clock_convention='post_increment')
    assert f['durations']['clock_difference'] == [8, 8]
    black = extract_game(pgn, 'black', '300+5', clock_convention='post_increment')
    assert black['durations']['clock_difference'] == [6]
    pgn = text('1. e4 {[%emt 0:00:03] [%clk 0:04:57]} e5 *')
    f = extract_game(pgn, 'white', '300+5', clock_convention='post_increment')
    assert f['durations'] == {'reported_emt': [3]}
    assert f['clocks']['emt_clock_disagreements'] == 1


def test_no_increment_missing_adjacency_negative_and_last_clock():
    f = extract_game(text('1. e4 {[%clk 0:04:55]} e5 2. Nf3 {[%clk 0:04:51]} Nc6 3. Bc4 *', '300'), 'white', '300', clock_convention='post_increment')
    assert f['durations']['clock_difference'] == [5, 4]
    assert 'last_recorded' not in f['checkpoints']
    f = extract_game(text('1. e4 {[%clk 0:04:55]} e5 2. Nf3 Nc6 3. Bc4 {[%clk 0:04:40]} *', '300'), 'white', '300', clock_convention='post_increment')
    assert f['durations']['clock_difference'] == [5]
    f = extract_game(text('1. e4 {[%clk 0:05:01]} e5 *', '300'), 'white', '300', clock_convention='post_increment')
    assert not f['durations'] and f['clocks']['invalid_inferred_intervals'] == 1


@pytest.mark.parametrize('tc', ['40/7200:3600', '300+5d', '*180', '1/86400', '?', '-', 'nan', '300+-1'])
def test_unsupported_clock_types_can_use_explicit_emt(tc):
    f = extract_game(text('1. e4 {[%emt 0:00:02] [%clk 0:04:59]} *', tc), 'white', tc, clock_convention='post_increment')
    assert f['durations'] == {'reported_emt': [2]} and not f['inference_supported']


@pytest.mark.parametrize('bad', ['nan', 'inf', '-1', '0:60:00', '0:00:60', '0:0:-2'])
def test_bad_annotations_are_not_durations(bad):
    assert annotation_seconds(bad) is None
    f = extract_game(text(f'1. e4 {{[%emt {bad}]}} *'), 'white', '300+5')
    assert not f['durations'] and f['clocks']['malformed_or_conflicting_annotations'] == 1


def test_setup_and_conflicting_metadata_do_not_invent_initial_time():
    setup = '[SetUp "1"]\n[FEN "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 12"]'
    f = extract_game(text('12... e5 {[%clk 0:04:50]} 13. e4 Nc6 {[%clk 0:04:40]} *', setup=setup), 'black', '300+5', clock_convention='post_increment')
    assert f['durations']['clock_difference'] == [15]
    assert not f['standard_start'] and f['development_at_ten'] is None
    f = extract_game(text('1. e4 {[%clk 0:04:50]} *'), 'white', '600', clock_convention='post_increment')
    assert f['tc_conflict'] and not f['durations']


def test_multiple_conflicting_clock_annotations():
    f = extract_game(text('1. e4 {[%clk 0:04:50] [%clk 0:04:55]} *'), 'white', '300+5', clock_convention='post_increment')
    assert f['clocks']['clock_moves'] == 0 and not f['durations']
