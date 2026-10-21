"""Practice tests use temporary libraries only; no engine or provider execution."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import chess
import pytest
from src.library import GameLibrary
from src.practice_models import Discover, Start, Event
from src.practice_store import PracticeStore, PracticeError, Limits
from src.puzzles import compose, grade, root_board, canonical, account_key
from src.understanding_models import UnderstandingBudget

PGN = '[White "Alice"]\n[Black "Bob"]\n\n1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0'
GUIDED = '[White "Alice"]\n[Black "Bob"]\n\n1. e4 e5 2. Nf3 Nc6 *'
ACCOUNT = {'source': 'lichess', 'username': 'Alice'}


@pytest.fixture
def practice(tmp_path):
    library = (tmp_path/'games.db').resolve()
    with GameLibrary(str(library)) as lib:
        game, _ = lib.add_game(PGN, source='lichess')
        other, _ = lib.add_game(GUIDED, source='lichess')
    store = PracticeStore((tmp_path/'practice.db').resolve(), library)
    store.initialize()
    return store, game.game_id, other.game_id


def discovery(gid, text=PGN, ply=6, account=ACCOUNT):
    return Discover(account=account, games=[{'game_id': gid, 'expected_pgn_sha256': hashlib.sha256(text.encode()).hexdigest(), 'plies': [ply]}])


def create(practice, guided=False):
    s, gid, other = practice
    result = s.discover(discovery(other if guided else gid, GUIDED if guided else PGN, 4 if guided else 6))
    assert result['items']
    return result['items'][0]


def start(s, q, key='start', parent=None):
    return s.start(Start(puzzle_id=q['id'], request_key=key, retry_of=parent))


def event(s, a, kind='move', uci='h5f7', key='move'):
    return s.event(a['id'], Event(kind=kind, revision=a['revision'], request_key=key, uci=uci if kind == 'move' else None))


def test_mate_workflow_durable_and_answer_safe(practice):
    s, _, _ = practice
    q = create(practice)
    assert q['tier'] == 'VERIFIED_PUZZLE'
    encoded = json.dumps(q)
    for secret in ('h5f7', 'Qxf7', 'witness', 'finding', 'best_move', 'solution'):
        assert secret not in encoded
    assert len(q['moves']) == 6
    begin = start(s, q)
    a = begin['attempt']
    assert event(s, a, uci='a1a8')['attempt']['feedback']['code'] == 'ILLEGAL_MOVE'
    a = s.attempt(a['id'])['attempt']
    miss = event(s, a, uci='a2a3', key='miss')['attempt']
    assert miss['feedback']['code'] == 'OBJECTIVE_NOT_MET' and miss['state'] == 'ACTIVE'
    win = event(s, miss, key='win')['attempt']
    assert win['state'] == 'OBJECTIVE_MET' and not win['assisted']
    reopened = PracticeStore(s.path, s.library)
    assert reopened.attempt(a['id'])['attempt'] == win
    assert reopened.start(Start(puzzle_id=q['id'], request_key='start')) == begin
    assert len(reopened.history(ACCOUNT)['items']) == 1


def test_every_alternative_mate_is_checked_not_witness_list():
    # Setup fixture tests grading predicate directly, not verified discovery qualification.
    board = chess.Board('7k/5K2/8/8/8/8/8/2Q5 w - - 0 1')
    mates = []
    for m in board.legal_moves:
        b = board.copy(); b.push(m)
        if b.is_checkmate(): mates.append(m.uci())
    assert len(mates) > 1
    d = {'context': {'root_fen': board.fen(), 'fen': board.fen(), 'moves': []}, 'objective': 'CHECKMATE_IN_ONE', 'witness': mates[0]}
    for move in mates:
        assert grade(d, move)['code'] == 'OBJECTIVE_MET'


def test_guided_alternative_not_wrong(practice):
    s, _, _ = practice
    q = create(practice, True)
    assert q['tier'] == 'GUIDED_EXERCISE'
    d, _ = s._record(q['id'])
    a = start(s, q)['attempt']
    b = root_board(d['context'])
    alternative = next(m.uci() for m in b.legal_moves if m.uci() != d['witness'])
    result = event(s, a, uci=alternative)['attempt']
    assert result['state'] == 'LEGAL_ALTERNATIVE_UNASSESSED'
    new = start(s, q, 'again', a['id'])['attempt']
    assert event(s, new, uci=d['witness'], key='witness')['attempt']['state'] == 'DEMONSTRATED_TARGET'


def test_hints_reveal_retry_and_resume(practice):
    s, _, _ = practice; q = create(practice); a = start(s, q)['attempt']
    one = event(s, a, 'hint', key='hint1')['attempt']
    assert one['hints'] == 1 and one['assisted'] and 'h5f7' not in json.dumps(one)
    two = event(s, one, 'hint', key='hint2')['attempt']
    revealed = event(s, two, 'reveal', key='reveal')['attempt']
    assert revealed['feedback']['solution']['uci'] == 'h5f7'
    assert s.attempt(a['id'])['attempt'] == revealed
    retry = start(s, q, 'retry', a['id'])['attempt']
    assert retry['assisted']
    # Starting without explicitly naming a parent cannot erase known assistance.
    assert start(s, q, 'fresh')['attempt']['assisted']


def test_idempotent_concurrent_events_and_revision(practice):
    s, _, _ = practice; q = create(practice); a = start(s, q)['attempt']
    req = Event(kind='hint', revision=0, request_key='same')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: s.event(a['id'], req), range(2)))
    assert results[0] == results[1]
    assert s.status()['counts']['attempt_events'] == 1
    with pytest.raises(PracticeError, match='IDEMPOTENCY_CONFLICT'):
        s.event(a['id'], Event(kind='reveal', revision=0, request_key='same'))
    with pytest.raises(PracticeError, match='REVISION_CONFLICT'):
        event(s, a, 'reveal', key='other')


@pytest.mark.parametrize('delete', [False, True])
def test_stale_source_blocks_grading_and_private_resume(practice, delete):
    s, gid, _ = practice; q = create(practice); a = start(s, q)['attempt']
    event(s, a, 'reveal', key='r')
    with GameLibrary(str(s.library)) as lib:
        if delete: lib._conn.execute('DELETE FROM games WHERE game_id=?', (gid,))
        else: lib._conn.execute('UPDATE games SET pgn=pgn||? WHERE game_id=?', (' {edit}', gid))
        lib._conn.commit()
    with pytest.raises(PracticeError, match='STALE'):
        event(s, a, key='stale')
    resumed = s.attempt(a['id'])
    assert resumed['events'] == [] and resumed['attempt']['feedback'] is None
    assert resumed['question']['source']['freshness'] == 'STALE_OR_MISSING_SOURCE'


def test_duplicate_discovery_and_exact_identity(practice):
    s, gid, _ = practice
    q = create(practice)
    again = s.discover(discovery(gid, account={'source': 'lichess', 'username': 'ALICE'}))
    assert again['items'][0]['id'] == q['id']
    assert s.status()['counts']['puzzles'] == 1
    for account in ({'source': 'chess.com', 'username': 'Alice'}, {'source': 'lichess', 'username': 'Ali'}):
        with pytest.raises(ValueError): s.discover(discovery(gid, account=account))
    assert account_key({'source': 'local', 'username': 'ALICE'}) != account_key({'source': 'local', 'username': 'Alice'})


def test_concurrent_init_missing_alias_and_capacity(practice, tmp_path):
    s, _, _ = practice
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _: s.initialize(), range(2)))
    missing = PracticeStore((tmp_path/'missing.db').resolve(), s.library)
    with pytest.raises(PracticeError): missing.status()
    assert not missing.path.exists()
    with pytest.raises(ValueError): PracticeStore(s.library, s.library)
    alias = tmp_path/'alias.db'; alias.symlink_to(s.path)
    with pytest.raises(Exception): PracticeStore(alias, s.library)
    limited = PracticeStore((tmp_path/'limited.db').resolve(), s.library, Limits(payload_bytes=1024))
    limited.initialize()
    with pytest.raises(PracticeError, match='CAPACITY'): limited.discover(discovery(practice[1]))
    assert limited.status()['counts']['puzzles'] == 0


def test_source_changed_during_composition(practice, monkeypatch):
    s, gid, _ = practice
    import src.practice_store as module
    original = module.compose
    def edited(*args):
        result = original(*args)
        with GameLibrary(str(s.library)) as lib:
            lib._conn.execute('UPDATE games SET pgn=pgn||? WHERE game_id=?', (' {change}', gid)); lib._conn.commit()
        return result
    monkeypatch.setattr(module, 'compose', edited)
    with pytest.raises(PracticeError, match='STALE'): s.discover(discovery(gid))
    assert s.status()['counts']['puzzles'] == 0


def test_setup_mate_guided_terminal_rejected_and_opponent_turn(practice):
    s, _, _ = practice
    setup = '[SetUp "1"]\n[FEN "7k/8/5KQ1/8/8/8/8/8 w - - 0 1"]\n\n*'
    d, _ = compose(s.library, ACCOUNT, setup, 0, UnderstandingBudget())
    assert d['tier'] == 'GUIDED_EXERCISE'
    terminal = '[SetUp "1"]\n[FEN "7k/6Q1/6K1/8/8/8/8/8 b - - 0 1"]\n\n*'
    assert compose(s.library, ACCOUNT, terminal, 0, UnderstandingBudget())[0] is None
    result = s.discover(discovery(practice[1], ply=5))
    assert not result['items']


@pytest.mark.parametrize('uci', ['e2e4', 'e2e5', 'a7a8n'])
def test_input_move_contract(uci):
    assert Event(kind='move', revision=0, request_key='x', uci=uci).uci == uci
    with pytest.raises(ValueError): Event(kind='hint', revision=0, request_key='x', uci=uci)


def test_parallel_attempt_cannot_erase_hint_assistance(practice):
    s, _, _ = practice; q = create(practice)
    first = start(s, q, 'first')['attempt']; other = start(s, q, 'parallel')['attempt']
    event(s, first, 'hint', key='h')
    assert event(s, other, key='win')['attempt']['assisted']


def test_source_change_after_commit_invalidates_accepted_result(practice, monkeypatch):
    s, gid, _ = practice; q = create(practice); a = start(s, q)['attempt']
    original = s.postcheck
    def race(pid, aid):
        with GameLibrary(str(s.library)) as lib:
            lib._conn.execute('UPDATE games SET pgn=pgn||? WHERE game_id=?', (' {race}', gid));lib._conn.commit()
        return original(pid, aid)
    monkeypatch.setattr(s, 'postcheck', race)
    with pytest.raises(PracticeError, match='STALE'): event(s, a)
    with s.connection() as c:
        assert c.execute('SELECT state FROM attempts WHERE id=?', (a['id'],)).fetchone()[0] == 'STALE_SOURCE'
    assert s.history(ACCOUNT)['items'][0]['state'] == 'STALE_SOURCE'


def test_same_puzzle_exact_history_other_game_source_link(practice):
    s, gid, _ = practice; q = create(practice)
    alternate = PGN.replace('[Black "Bob"]', '[Black "Other"]')
    with GameLibrary(str(s.library)) as lib: g, _ = lib.add_game(alternate, source='lichess')
    second = s.discover(discovery(g.game_id, alternate))['items'][0]
    assert second['id'] == q['id']
    with s.connection() as c: assert c.execute('SELECT count(*) FROM puzzle_sources').fetchone()[0] == 2
    with GameLibrary(str(s.library)) as lib:
        lib._conn.execute('DELETE FROM games WHERE game_id=?', (gid,));lib._conn.commit()
    assert start(s, q)['attempt']['state'] == 'ACTIVE'


def test_different_history_never_deduplicated(practice):
    s, _, _ = practice; q = create(practice)
    text = '[White "Alice"]\n[Black "Bob"]\n\n1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0'
    with GameLibrary(str(s.library)) as lib: game, _ = lib.add_game(text, source='lichess')
    other = s.discover(discovery(game.game_id, text))['items'][0]
    assert other['fen'] == q['fen']
    assert other['moves'] != q['moves'] and other['id'] != q['id']


def test_discovery_budget_and_request_cap(practice, monkeypatch):
    s, gid, _ = practice
    import src.practice_store as module
    def spent(*args): raise module.BudgetExceeded('TIME_BUDGET')
    monkeypatch.setattr(module, 'compose', spent)
    result = s.discover(discovery(gid))
    assert result['partial'] and not result['items'] and result['skipped'][0]['reason'] == 'ROOT_BUDGET'
    with pytest.raises(ValueError): Discover(account=ACCOUNT, games=[{'game_id': gid, 'expected_pgn_sha256': '0'*64, 'plies': list(range(7))}])


def test_cross_store_alias_and_hardlink_refused(practice, monkeypatch, tmp_path):
    s, _, _ = practice
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB', str(s.path))
    with pytest.raises(ValueError): PracticeStore(s.path, s.library)
    hard = tmp_path/'hard.db';hard.hardlink_to(s.library)
    with pytest.raises(Exception): PracticeStore(hard, s.library)


def test_ambiguous_account_rejected(practice):
    s, _, _ = practice
    text = PGN.replace('[Black "Bob"]', '[Black "ALICE"]')
    with GameLibrary(str(s.library)) as lib: g, _ = lib.add_game(text, source='lichess')
    with pytest.raises(ValueError): s.discover(discovery(g.game_id, text))


def test_repeated_reveal_does_not_duplicate_events(practice):
    s, _, _ = practice; q = create(practice); a = start(s, q)['attempt']
    first = event(s, a, 'reveal', key='r')['attempt']
    second = event(s, first, 'reveal', key='new-key')['attempt']
    assert second == first
    assert s.status()['counts']['attempt_events'] == 1


def test_new_store_concurrent_initialization_and_unknown_version(practice, tmp_path):
    old, _, _ = practice
    fresh = PracticeStore((tmp_path/'new-concurrent.db').resolve(), old.library)
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(lambda _: fresh.initialize(), range(2)))
    assert fresh.status()['counts']['puzzles'] == 0
    with fresh.connection(True) as c: c.execute('UPDATE practice_meta SET version=2')
    before = fresh.path.read_bytes()
    with pytest.raises(PracticeError): fresh.initialize()
    assert fresh.path.read_bytes() == before


def test_terminal_automatic_draw_does_not_create_puzzle(practice):
    s, _, _ = practice
    text = '[SetUp "1"]\n[FEN "7k/8/5KQ1/8/8/8/8/8 w - - 150 76"]\n\n*'
    assert compose(s.library, ACCOUNT, text, 0, UnderstandingBudget())[1]['reason'] == 'TERMINAL_ROOT'


def test_black_learner_mate_and_promotion_legality(practice):
    s, _, _ = practice
    text = '[White "Other"]\n[Black "Alice"]\n\n1. f3 e5 2. g4 Qh4# 0-1'
    with GameLibrary(str(s.library)) as lib: g, _ = lib.add_game(text, source='lichess')
    q = s.discover(discovery(g.game_id, text, 3))['items'][0]
    assert q['learner_color'] == 'black' and q['tier'] == 'VERIFIED_PUZZLE'
    a = start(s, q)['attempt']
    assert event(s, a, uci='d8h4')['attempt']['state'] == 'OBJECTIVE_MET'
    b = chess.Board('7k/P7/6K1/8/8/8/8/8 w - - 0 1')
    d = {'context': {'root_fen': b.fen(), 'fen': b.fen(), 'moves': []}, 'objective': 'EXAMINE_WITNESS', 'witness': 'a7a8q'}
    assert grade(d, 'a7a8n')['code'] == 'LEGAL_ALTERNATIVE_UNASSESSED'
    assert grade(d, 'a7a8')['code'] == 'ILLEGAL_MOVE'


def test_legacy_mistake_only_supplies_root_seed(practice):
    s, gid, _ = practice
    with GameLibrary(str(s.library)) as lib:
        lib._conn.execute('INSERT INTO analyses(game_id,analyzed_at,analysis_data) VALUES(?,?,?)',
                          (gid, 'fixture', json.dumps({'moves': [{'ply': 7, 'cpl': 1000, 'best_move_uci': 'a1a8'}]})))
        lib._conn.commit()
    request = discovery(gid); request.games[0].plies = []
    result = s.discover(request)
    assert result['seed_coverage'][0]['status'] == 'LEGACY_SEEDS_ONLY_NOT_PROOF'
    assert result['examined'][0]['ply'] == 6
    assert result['items'][0]['tier'] == 'VERIFIED_PUZZLE'
    d, _ = s._record(result['items'][0]['id'])
    assert d['witness'] == 'h5f7'  # fake legacy best move was ignored
