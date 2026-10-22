import copy
import hashlib
import json
import pytest
from src.library import GameLibrary
from src.reasoning import reason_game
from src.reasoning_import import ArtifactStore
from src.reasoning_models import Bundle, validate_bundle, Budget
from src.understanding_models import BudgetExceeded
from src.understanding import SourceChanged
from tests.reasoning_fixtures import PGN, bundle, bundle_data, context, line, settings


@pytest.fixture
def data(tmp_path):
    path = (tmp_path/'library.db').resolve()
    with GameLibrary(str(path)) as lib: game, _ = lib.add_game(PGN)
    return path, game.game_id, hashlib.sha256(PGN.encode()).hexdigest(), ArtifactStore((tmp_path/'artifacts').resolve())


def run(data, b=None, **kwargs):
    path, gid, sha, store = data
    identifier = store.accept(b or bundle(), synthetic=True)
    return reason_game(path, gid, 2, sha, directory=store.directory, bundle_id=identifier, allow_synthetic=True, **kwargs).model_dump()


@pytest.mark.parametrize('mode', ['ROOT_MULTIPV_COHORT', 'SIBLING_CHILD_COHORT'])
def test_comparison_lines_and_claim_references(data, mode):
    value = run(data, bundle(mode), alternative='b1c3')
    assert value['status'] == 'AVAILABLE'
    assert value['evidence']['origin'] == 'SYNTHETIC_TEST'
    assert value['evidence']['is_genuine_engine_evidence'] is False
    assert value['comparison']['preferred_recorded_uci'] == 'g1f3'
    assert value['comparison']['cp_differences'][0]['for_root_mover'] == 25
    assert value['comparison']['cp_differences'][0]['recorded_scores_close']
    assert value['lines'][0]['opponent_response']['uci'] == 'b8c6'
    assert value['lines'][1]['opponent_response']['uci'] == 'g8f6'
    ids = {'root', *value['evidence']['record_ids'], *(x['id'] for x in value['lines']), *(x['id'] for x in value['checkpoints'])}
    assert all(set(c['references']) <= ids for c in value['claims'])
    assert any('g1 to f3' in c['text'] for c in value['claims'])
    assert all(c['class'] != 'ENGINE_OBSERVATION' for c in value['claims'])
    assert len(value['checkpoints']) <= 8 and value['coverage']['findings'] <= 32


def test_missing_alternative_not_zero(data):
    result = run(data, alternative='f1c4')
    assert result['status'] == 'PARTIAL'
    alt = next(c for c in result['candidates'] if c['uci'] == 'f1c4')
    assert alt['score'] is None and alt['quality'] == 'MISSING_EVIDENCE'
    branch = next(x for x in result['lines'] if x['candidate_uci'] == 'f1c4')
    assert branch['moves'] == ['f1c4'] and branch['opponent_response'] is None


def test_synthetic_disabled_and_no_evidence_structural_fallback(data):
    path, gid, sha, store = data
    identifier = store.accept(bundle(), synthetic=True)
    result = reason_game(path, gid, 2, sha, directory=store.directory, bundle_id=identifier, alternative='g1f3').model_dump()
    assert result['status'] == 'INSUFFICIENT_EVIDENCE'
    assert all(c['score'] is None for c in result['candidates'])
    empty = reason_game(path, gid, 2, sha, alternative='g1f3').model_dump()
    assert empty['checkpoints'] and empty['lines'][0]['moves'] == ['g1f3']
    assert empty['comparison']['preferred_recorded_uci'] is None


@pytest.mark.parametrize('change', ['depth', 'session', 'cohort', 'settings'])
def test_sibling_mismatch_no_numerical_ranking(data, change):
    raw = bundle_data('SIBLING_CHILD_COHORT'); r = raw['records'][1]
    if change == 'depth':
        r['achieved_depth'] = 17; r['lines'][0]['depth'] = 17
    elif change == 'settings': r['settings']['applied_options']['Hash'] = 128; r['settings']['requested_options']['Hash'] = 128
    else: r[change] = 'different'
    result = run(data, Bundle.model_validate(raw))
    assert result['status'] in ('INCOMPATIBLE', 'CONFIG_REQUIRED')
    assert result['comparison'].get('preferred_recorded_uci') is None
    assert not result['comparison'].get('cp_differences')


@pytest.mark.parametrize('change', ['partial', 'bound', 'shallow', 'restricted'])
def test_quality_refusal(data, change):
    raw = bundle_data(); r = raw['records'][0]
    if change == 'partial': r['lines'].pop()
    if change == 'bound': r['lines'][0]['bound'] = 'lower'
    if change == 'shallow':
        r['achieved_depth'] = 12
        for l in r['lines']: l['depth'] = 12
    if change == 'restricted':
        r['settings']['search_scope'] = 'restricted'; r['settings']['searched_roots'] = ['g1f3', 'b1c3']
    result = run(data, Bundle.model_validate(raw))
    assert result['comparison'].get('preferred_recorded_uci') is None
    assert all(c['score'] is None for c in result['candidates'])


def test_mixed_cp_mate_no_sentinel_arithmetic(data):
    raw = bundle_data(); raw['records'][0]['lines'][0]['score'].update(kind='mate', cp=None, mate=3)
    result = run(data, Bundle.model_validate(raw))
    assert result['comparison']['score_relation'] == 'TYPED_QUALITATIVE_ONLY_NO_CP_MATE_ARITHMETIC'
    assert result['comparison']['preferred_recorded_uci'] is None
    assert result['comparison']['cp_differences'] == []


def test_black_orientation(data):
    path, _, _, store = data
    text = '[White "A"]\n[Black "B"]\n\n1. e4 *'
    with GameLibrary(str(path)) as lib: game, _ = lib.add_game(text)
    raw = bundle_data(); raw['root'] = context(['e2e4'])
    r = raw['records'][0]; r['context'] = raw['root']
    r['lines'] = [line(['e7e5', 'g1f3'], 50), line(['c7c5', 'g1f3'], -20, 2)]
    identifier = store.accept(Bundle.model_validate(raw), synthetic=True)
    value = reason_game(path, game.game_id, 1, hashlib.sha256(text.encode()).hexdigest(), directory=store.directory,
        bundle_id=identifier, allow_synthetic=True).model_dump()
    assert value['comparison']['preferred_recorded_uci'] == 'c7c5'
    assert value['comparison']['cp_differences'][0]['for_root_mover'] == 70


def test_full_pv_validation_not_only_display_prefix(data):
    raw = bundle_data(); raw['records'][0]['lines'][0]['score']['moves'] += ['a1a8']
    with pytest.raises(ValueError): validate_bundle(Bundle.model_validate(raw))
    raw = bundle_data(); raw['records'][0]['lines'][0]['iteration'] = 17
    with pytest.raises(ValueError, match='MIXED_FINAL'): Bundle.model_validate(raw)


def test_source_change_during_composition(data, monkeypatch):
    path, gid, sha, store = data
    import src.reasoning as module
    original = module.inspect_checkpoint
    def edit(*a):
        value = original(*a)
        with GameLibrary(str(path)) as lib:
            lib._conn.execute('UPDATE games SET pgn=pgn||? WHERE game_id=?', (' {edit}', gid));lib._conn.commit()
        return value
    monkeypatch.setattr(module, 'inspect_checkpoint', edit)
    with pytest.raises(SourceChanged): run(data)


def test_bounded_work_and_illegal_alternative(data):
    path, gid, sha, store = data
    with pytest.raises(ValueError): reason_game(path, gid, 2, sha, alternative='e2e5')
    with pytest.raises(BudgetExceeded): reason_game(path, gid, 2, sha, budget=Budget(seconds=0))
    result = run(data, budget=Budget(max_pushes=3))
    assert result['status'] == 'RESOURCE_LIMIT' and not result['lines'] and not result['claims']


def test_incompatible_context_and_invalid_artifact_fallback(data):
    path, gid, sha, store = data
    identifier = store.accept(bundle(), synthetic=True)
    wrong = reason_game(path, gid, 0, sha, directory=store.directory, bundle_id=identifier, alternative='e2e4', allow_synthetic=True).model_dump()
    assert wrong['status'] == 'INCOMPATIBLE' and all(c['score'] is None for c in wrong['candidates'])
    (store.directory/(identifier+'.json')).write_text('{}')
    malformed = reason_game(path, gid, 2, sha, directory=store.directory, bundle_id=identifier, alternative='g1f3').model_dump()
    assert malformed['status'] == 'INCOMPATIBLE' and malformed['checkpoints']


def test_frozen_corpus_contracts_are_not_chess_strength(tmp_path):
    from pathlib import Path
    corpus = json.loads((Path(__file__).parent/'data/reasoning/corpus.json').read_text())
    assert corpus['genuine_chess_quality_status'] == 'NOT_RUN'
    assert len(corpus['cases']) == 14 and len({c['id'] for c in corpus['cases']}) == 14
    for case in corpus['cases']:
        folder = (tmp_path/case['id']).resolve();folder.mkdir()
        path = folder/'library.db'
        with GameLibrary(str(path)) as lib: game, _ = lib.add_game(case['pgn'])
        kwargs = {}
        if case['synthetic_bundle']:
            store = ArtifactStore(folder/'artifacts')
            identifier = store.accept(Bundle.model_validate(case['synthetic_bundle']), synthetic=True)
            kwargs.update(directory=store.directory, bundle_id=identifier, allow_synthetic=True)
        value = reason_game(path, game.game_id, case['ply'], hashlib.sha256(case['pgn'].encode()).hexdigest(), **kwargs).model_dump()
        assert value['status'] == case['expected_contract_status'], (case['id'],value['evidence'])
        assert case['genuine_quality']['acceptable_move_set'] is None
        assert all(c['class'] != 'ENGINE_OBSERVATION' for c in value['claims'])
        if case['id'] == 'D1':
            assert value['root']['legal_move_count'] == 1
            assert any('only legal move' in c['text'] for c in value['claims'])


@pytest.mark.parametrize('moves,first,reply', [
    (['e2e4','a7a6','e4e5','d7d5'], 'e5d6', 'c7d6'),
    (['e2e4','e7e5','g1f3','b8c6','f1c4','g8f6'], 'e1g1', 'f8c5')])
def test_concrete_special_move_steps(tmp_path, moves, first, reply):
    import chess, chess.pgn
    b = chess.Board();game = chess.pgn.Game();node = game
    for uci in moves:
        move = b.parse_uci(uci);node = node.add_main_variation(move);b.push(move)
    text = str(game)
    path = (tmp_path/'library.db').resolve()
    with GameLibrary(str(path)) as lib: source, _ = lib.add_game(text)
    # No imported evidence is needed for an explicit legal immediate transition.
    result = reason_game(path, source.game_id, len(moves), hashlib.sha256(text.encode()).hexdigest(), alternative=first).model_dump()
    step = result['lines'][0]['steps'][0]
    if first == 'e5d6': assert step['events'][0]['capture']['square'] == 'd5'
    else: assert step['events'][1]['kind'] == 'castling_rook' and step['events'][1]['to'] == 'f1'
    assert result['candidates'][0]['score'] is None


def test_equal_recorded_scores_do_not_create_distinct_preference(data):
    raw = bundle_data();raw['records'][0]['lines'][1]['score']['cp'] = 35
    value = run(data, Bundle.model_validate(raw))
    assert value['comparison']['preferred_recorded_uci'] is None
    assert len(value['comparison']['leading_recorded_ucis']) == 2
    assert any('share the leading recorded cp' in c['text'] for c in value['claims'])


def test_existing_progressive_reader_budget_protocol_is_compatible(data, tmp_path, monkeypatch):
    from src.progressive_store import ProgressiveStore
    path, gid, sha, _ = data
    store = ProgressiveStore((tmp_path/'progressive.db').resolve(), path);store.initialize()
    monkeypatch.setenv('CHESS_COACH_PROGRESSIVE_DB', str(store.path))
    result = reason_game(path, gid, 2, sha, alternative='g1f3').model_dump()
    assert result['evidence']['retained_local']['status'] == 'UNAVAILABLE'
    assert result['evidence']['retained_local']['comparison_admission'] == 'NO_REASONING_COHORT_RECEIPT'
    assert result['checkpoints'] and result['status'] == 'INSUFFICIENT_EVIDENCE'


def test_root_proposal_scores_never_blend_into_sibling_child_ranking(data):
    raw = bundle_data('SIBLING_CHILD_COHORT')
    raw['records'].insert(0, bundle_data()['records'][0])
    raw['records'][1]['lines'][0]['score']['cp'] = -100
    raw['records'][2]['lines'][0]['score']['cp'] = 100
    value = run(data, Bundle.model_validate(raw))
    assert value['comparison']['method'] == 'SIBLING_CHILD_COHORT'
    assert value['comparison']['preferred_recorded_uci'] == 'b1c3'
    assert value['comparison']['cp_differences'][0]['for_root_mover'] == 200
    assert all(c['record_id'] != 'root-search' for c in value['candidates'])


def test_display_prefix_requires_validation_of_the_hidden_tail(data):
    raw = bundle_data()
    raw['records'][0]['lines'][0]['score']['moves'] = 'g1f3 b8c6 f1c4 g8f6 d2d3 f8c5 c1g5 d7d6 b1c3 c8e6'.split()
    value = run(data, Bundle.model_validate(raw))
    main = next(l for l in value['lines'] if l['candidate_uci'] == 'g1f3')
    assert len(main['moves']) == 8 and main['full_validated_plies'] == 10 and main['display_truncated']
    raw['records'][0]['lines'][0]['score']['moves'].append('a1a8')
    with pytest.raises(ValueError): validate_bundle(Bundle.model_validate(raw))


def test_operator_attestation_does_not_assert_independent_genuine_execution(data):
    # Fabricated declaration only tests the attestation boundary, never chess quality.
    path, gid, sha, store = data
    raw = bundle_data();raw['producer'].update(simulation=False, method='external_export', name='Declared exporter contract test')
    identifier = store.accept(Bundle.model_validate(raw), acknowledge_external=True)
    value = reason_game(path, gid, 2, sha, directory=store.directory, bundle_id=identifier).model_dump()
    assert value['status'] == 'AVAILABLE'
    assert value['evidence']['origin'] == 'EXTERNAL_ATTESTED'
    assert value['evidence']['is_genuine_engine_evidence'] is None
    assert value['evidence']['execution_independently_verified'] is False
    assert 'NOT INDEPENDENT NUMERICAL PROOF' in value['evidence']['interpretation']
