"""Segment 16 temporary-only identity/storage regressions; no engines/network."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import sqlite3

import chess
import pytest
from pydantic import ValidationError

from src.evaluation_models import CacheLimits, PositionContext, Source, SearchConfig, PV, Evaluation, Lookup, digest
from src.evaluation_cache import EvaluationCache, CacheUnavailable, CapacityError


def context(moves=(), complete=True):
    b = chess.Board()
    for move in moves: b.push_uci(move)
    return PositionContext(fen=b.fen(), root_fen=chess.STARTING_FEN, moves=list(moves), root_history_complete=complete)


def config(depth=16, version='fixture1', multipv=1, **extra):
    return SearchConfig(source=Source(kind='local_stockfish', engine_name='fixture', engine_version=version,
                        engine_build='test-only', nnue='fixture', provenance='synthetic test; not a chess evaluation'),
                        requested_options={'Threads': 1}, applied_options={'Threads': 1, 'Hash': 64}, applied_options_complete=True, limit_kind='depth', depth=depth, multipv=multipv, **extra)


def evaluation(ctx=None, depth=16, cp=0, **kw):
    ctx = ctx or context()
    b = chess.Board(ctx.fen)
    return Evaluation(context=ctx, config=config(depth=depth, **kw), achieved_depth=depth,
                      pvs=[PV(kind='cp', cp=cp, moves=[next(iter(b.legal_moves)).uci()])])


@pytest.fixture
def cache(tmp_path):
    c = EvaluationCache(tmp_path/'evaluations.sqlite3'); c.initialize(); return c


@pytest.mark.parametrize('halfmove', [0,99,100,150])
def test_clock_separates_contexts(halfmove):
    fen = chess.STARTING_FEN.split(); fen[4] = str(halfmove)
    ctx = PositionContext(fen=' '.join(fen))
    assert ctx.identity()['halfmove'] == halfmove
    assert ctx.identity()['position'] == context().identity()['position']


def test_identity_history_ep_fullmove():
    a = context(['g1f3','g8f6','f3g1','f6g8'])
    b = context(['b1c3','b8c6','c3b1','c6b8'])
    assert a.identity()['position'] == b.identity()['position']
    assert digest(a.identity()) != digest(b.identity())
    fen = a.fen.split(); fen[5] = '10'
    standalone = PositionContext(fen=' '.join(fen))
    assert digest(standalone.identity()) == digest(PositionContext(fen=a.fen).identity())
    ep = PositionContext(fen='k7/8/8/3pP3/8/8/8/4K3 w - d6 0 1')
    assert ep.identity()['position'].endswith('d6')
    ineffective = PositionContext(fen='rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1')
    assert ineffective.identity()['position'].endswith('-')


@pytest.mark.parametrize('values', [dict(fen=' '.join(chess.STARTING_FEN.split()[:4])),
    dict(fen=chess.STARTING_FEN, root_history_complete=True),
    dict(fen=chess.STARTING_FEN, variant='chess960'),
    dict(fen=chess.STARTING_FEN, root_fen=chess.STARTING_FEN, moves=['e2e4']),
    dict(fen=chess.STARTING_FEN, root_fen=chess.STARTING_FEN, moves=['0000'])])
def test_invalid_context(values):
    with pytest.raises(ValueError): PositionContext(**values)


@pytest.mark.parametrize('kwargs', [dict(kind='cp',cp=True),dict(kind='mate',mate=0),dict(kind='cp',cp=0,mate=1),
    dict(kind='mate',mate=1,moves=['e2e4']*65),dict(kind='cp',cp=1000001)])
def test_typed_score_rejections(kwargs):
    with pytest.raises(ValueError): PV(**kwargs)


def test_local_match_and_advisory(cache):
    e = evaluation(cp=0); cache.ingest(e)
    r = cache.lookup(Lookup(context=e.context, require_trusted=True))
    assert r['context_compatibility'] == 'TRUSTED_CONTEXT_MATCH'
    assert r['evaluation']['pvs'][0]['cp'] == 0
    assert r['evaluation']['perspective'] == 'white'
    assert cache.lookup(Lookup(context=PositionContext(fen=e.context.fen), allow_advisory=True))['evaluation'] is None
    incomplete = evaluation(context(complete=False)); cache.ingest(incomplete)
    assert cache.lookup(Lookup(context=incomplete.context))['evaluation'] is None
    assert cache.lookup(Lookup(context=incomplete.context, allow_advisory=True))['context_compatibility'] == 'ADVISORY_CONTEXT_UNVERIFIED'


def test_history_never_substituted(cache):
    a = context(['g1f3','g8f6','f3g1','f6g8'])
    b = context(['b1c3','b8c6','c3b1','c6b8'])
    cache.ingest(evaluation(a))
    assert cache.lookup(Lookup(context=b,require_trusted=True))['evaluation'] is None


def test_selection_versions_and_limits(cache):
    cache.ingest(evaluation(depth=12,cp=12)); cache.ingest(evaluation(depth=20,cp=20))
    r = cache.lookup(Lookup(context=context(),require_trusted=True))
    assert r['evaluation']['achieved_depth'] == 20
    cache.ingest(evaluation(depth=8,version='fixture2'))
    assert cache.lookup(Lookup(context=context(),require_trusted=True))['status'] == 'CONFIG_REQUIRED'
    assert cache.lookup(Lookup(context=context(),require_trusted=True,config_hash=digest(config(12).model_dump())))['evaluation']['achieved_depth'] == 12
    assert cache.lookup(Lookup(context=context(),require_trusted=True,family_hash=config(20).family()))['evaluation']['achieved_depth'] == 20


def test_duplicate_concurrent_atomic_and_readonly(cache):
    e = evaluation()
    with ThreadPoolExecutor(4) as pool:
        ids = list(pool.map(lambda _: cache.ingest(e), range(12)))
    assert len(set(ids)) == 1
    before = hashlib.sha256(cache.path.read_bytes()).hexdigest()
    with cache.reader() as c:
        with pytest.raises(sqlite3.OperationalError): c.execute('DELETE FROM eval_records')
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
    cache.lookup(Lookup(context=context()))
    assert hashlib.sha256(cache.path.read_bytes()).hexdigest() == before
    with pytest.raises(RuntimeError):
        with cache.writer() as c:
            c.execute('DELETE FROM eval_records'); raise RuntimeError('interrupted')
    assert cache.status()['counts']['eval_records'] == 1


def test_capacity_retention_and_missing(tmp_path):
    cache = EvaluationCache(tmp_path/'cache.db',CacheLimits(max_records=1)); cache.initialize()
    cache.ingest(evaluation())
    with pytest.raises(CapacityError): cache.ingest(evaluation(context(['e2e4'])))
    assert cache.status()['counts']['eval_contexts'] == 1
    cache.maintain()
    cache.ingest(evaluation(context(['e2e4'])))
    assert cache.status()['counts']['eval_records'] == 1
    absent = EvaluationCache(tmp_path/'absent.db')
    with pytest.raises(CacheUnavailable): absent.lookup(Lookup(context=context()))
    assert not absent.path.exists()


def test_per_config_preserves_best(tmp_path):
    cache = EvaluationCache(tmp_path/'cache.db',CacheLimits(per_config=2)); cache.initialize()
    for cp in range(10): cache.ingest(evaluation(cp=cp))
    assert cache.status()['counts']['eval_records'] == 2


def test_initializer_concurrency_and_wrong_database(tmp_path):
    path = tmp_path/'concurrent.db'
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: EvaluationCache(path).initialize(),range(8)))
    with sqlite3.connect(path) as c: c.execute('UPDATE eval_cache_meta SET identity_version=99')
    with pytest.raises(CacheUnavailable): EvaluationCache(path).initialize()
    wrong = tmp_path/'library.db'
    with sqlite3.connect(wrong) as c: c.execute('CREATE TABLE games(id)')
    with pytest.raises(CacheUnavailable): EvaluationCache(wrong).initialize()


def test_alias_and_settings_bounds(tmp_path):
    path = tmp_path/'cache.db'; path.touch(); alias = tmp_path/'alias.db'; alias.symlink_to(path)
    with pytest.raises(CacheUnavailable): EvaluationCache(alias)
    for values in [dict(max_bytes=1),dict(per_config=33),dict(per_context=1,per_config=2),dict(per_minute=13),dict(min_interval=float('nan'))]:
        with pytest.raises(ValueError): CacheLimits(**values)


def test_mate_pv_legality_and_partial_local(cache):
    data=evaluation().model_dump(); data['pvs']=[{'kind':'mate','mate':-2,'moves':['g1h3']}]
    cache.ingest(Evaluation.model_validate(data))
    assert cache.lookup(Lookup(context=context(),require_trusted=True))['evaluation']['pvs'][0]['mate']==-2
    data['pvs'][0]['moves']=['e2e5']
    with pytest.raises(ValueError): Evaluation.model_validate(data)
    data=evaluation(multipv=3).model_dump(); cache.ingest(Evaluation.model_validate(data))
    assert cache.lookup(Lookup(context=context(),require_trusted=True,config_hash=digest(config(multipv=3).model_dump())))['evaluation'] is None
    assert cache.lookup(Lookup(context=context(),require_trusted=True,allow_partial=True,config_hash=digest(config(multipv=3).model_dump())))['partial']


def test_unknown_settings_terminal_and_protected_alias(tmp_path):
    with pytest.raises(ValueError): config().model_copy(update={'applied_options':None}).__class__.model_validate(config().model_copy(update={'applied_options':None}).model_dump())
    with pytest.raises(CacheUnavailable): EvaluationCache('data/library.db-wal')
    with pytest.raises(CacheUnavailable): EvaluationCache('data/backups/another.db')
    e=evaluation(); data=e.model_dump(); data['pvs']=[{'kind':'terminal_checkmate','mate':0,'winner':'white'}]
    with pytest.raises(ValueError): Evaluation.model_validate(data)


def test_physical_wal_pressure_fails_closed(tmp_path):
    c=EvaluationCache(tmp_path/'bounded.db',CacheLimits(max_bytes=8*1024**2)); c.initialize()
    # Keep a reader open to prevent automatic WAL truncation; admission reserves 4 MiB.
    with c.reader() as reader:
        reader.execute('SELECT count(*) FROM eval_records').fetchone()
        for i in range(1000):
            fen=chess.STARTING_FEN.split(); fen[4]=str(i)
            try: c.ingest(evaluation(PositionContext(fen=' '.join(fen))))
            except CapacityError: break
        else: raise AssertionError('Expected physical admission limit')
        assert sum(c.sizes().values())<=c.limits.max_bytes
        assert c.lookup(Lookup(context=PositionContext(fen=chess.STARTING_FEN),allow_advisory=True))['evaluation'] is not None
    c.maintain()
    assert sum(c.sizes().values())<=c.limits.max_bytes


def test_failed_schema_transaction_rolls_back(tmp_path,monkeypatch):
    import src.evaluation_cache as module
    path=tmp_path/'failed.db'
    monkeypatch.setattr(module,'DDL',module.DDL+('INVALID SQL',))
    with pytest.raises(sqlite3.OperationalError): EvaluationCache(path).initialize()
    with sqlite3.connect(path) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='eval_cache_meta'").fetchall()


def test_time_limit_classes_and_applied_metadata(cache):
    e=evaluation(); data=e.model_dump()
    data['config'].update(limit_kind='time',depth=None,time_seconds=1.0)
    cache.ingest(Evaluation.model_validate(data)); cache.ingest(e)
    assert cache.lookup(Lookup(context=context(),require_trusted=True))['status']=='CONFIG_REQUIRED'
    data['config'].update(limit_kind='combined',depth=16,time_seconds=1.0)
    cache.ingest(Evaluation.model_validate(data))
    assert cache.lookup(Lookup(context=context(),require_trusted=True,family_hash=e.config.family()))['evaluation']['config']['limit_kind']=='depth'
    data['config']['applied_options_complete']=False
    with pytest.raises(ValueError): Evaluation.model_validate(data)


def test_stale_advisory_requires_explicit_acceptance(cache):
    from src.evaluation_cloud import parse_cloud
    from test_evaluation_cloud import payload,query
    now=[1000.0]; cache.clock=lambda:now[0]; cache.ingest(parse_cloud(payload(),query()))
    now[0]+=31*86400
    assert cache.lookup(query())['evaluation'] is None
    assert cache.lookup(query(allow_stale=True))['evaluation']['stale']
