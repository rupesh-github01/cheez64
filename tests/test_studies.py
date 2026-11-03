import concurrent.futures
import sqlite3
import chess
import pytest
from src.study_store import StudyStore,StudyUnavailable,StudyConflict
from src.study_models import Metadata,SubStudyInput

@pytest.fixture
def store(tmp_path):
    s=StudyStore((tmp_path/'studies.db').resolve());s.initialize();return s


def hierarchy(s):
    study=s.create('study',dict(title="King's Gambit",description='Preparation'))
    sub=s.create('sub',dict(title='GothamChess Video',source_url='https://www.youtube.com/watch?v=fixture'),study['id'])
    line=s.create('line',dict(title='Falkbeer Countergambit'),sub['id'])
    return study,sub,line


def test_explicit_initialization_and_idempotence(tmp_path):
    path=(tmp_path/'missing.db').resolve();s=StudyStore(path)
    assert s.status()['status']=='UNINITIALIZED'
    with pytest.raises(StudyUnavailable):s.listing('study')
    with pytest.raises(StudyUnavailable):s.create('study',dict(title='No implicit init'))
    assert not path.exists()
    s.initialize();s.initialize();assert s.listing('study')['items']==[]
    with s.connection() as c:
        assert c.execute('PRAGMA foreign_keys').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):c.execute("INSERT INTO studies VALUES('x','x','','x','x')")


def test_crud_persistence_and_default_fen(store):
    st,sub,line=hierarchy(store)
    assert line['initial_fen']==chess.STARTING_FEN and len(line['initial_fen'].split())==6
    for kind,item in [('study',st),('sub',sub),('line',line)]:
        body=dict(title='Renamed '+kind,description='New description')
        if kind=='sub':body['source_url']=None
        updated=store.update(kind,item['id'],body)
        assert updated['created_at']==item['created_at'] and updated['updated_at']>=item['updated_at']
        assert StudyStore(store.path).get(kind,item['id'])['title']==body['title']
    assert store.listing('sub',st['id'])['items'][0]['id']==sub['id']
    assert store.listing('line',sub['id'])['items'][0]['id']==line['id']
    store.delete('line',line['id']);assert store.listing('line',sub['id'])['items']==[]
    store.delete('sub',sub['id']);assert store.listing('sub',st['id'])['items']==[]
    store.delete('study',st['id']);assert store.listing('study')['items']==[]


def test_cascades_moves_and_foreign_keys(store):
    st,sub,line=hierarchy(store)
    with store.connection(True) as c:
        c.execute('INSERT INTO line_moves VALUES(?,?,?,?,?,?,?)',('move',line['id'],1,'e2e4','e4','fixture fen','Future note'))
        with pytest.raises(sqlite3.IntegrityError):c.execute('INSERT INTO line_moves VALUES(?,?,?,?,?,?,?)',('duplicate',line['id'],1,'e2e4','e4','fen',''))
        with pytest.raises(sqlite3.IntegrityError):c.execute("INSERT INTO sub_studies VALUES('bad','missing','t','',NULL,0,'t','t')")
    store.delete('study',st['id'])
    with store.connection() as c:
        assert all(c.execute('SELECT count(*) FROM '+table).fetchone()[0]==0 for table in ['studies','sub_studies','study_lines','line_moves'])
        assert c.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_stable_order_reorder_and_atomic_conflict(store):
    st,sub,first=hierarchy(store);second=store.create('line',dict(title='Classical Defense'),sub['id'])
    assert [r['id'] for r in store.listing('line',sub['id'])['items']]==[first['id'],second['id']]
    store.reorder('line',sub['id'],[second['id'],first['id']])
    assert [r['id'] for r in store.listing('line',sub['id'])['items']]==[second['id'],first['id']]
    with pytest.raises(StudyConflict):store.reorder('line',sub['id'],[first['id']])
    assert store.get('line',second['id'])['sort_order']==0
    store.delete('line',second['id']);third=store.create('line',dict(title='Third'),sub['id'])
    assert [r['id'] for r in store.listing('line',sub['id'])['items']]==[first['id'],third['id']]
    s2=store.create('sub',dict(title='Book'),st['id']);store.reorder('sub',st['id'],[s2['id'],sub['id']])
    assert store.listing('sub',st['id'],limit=1)['next_offset']==1
    assert store.listing('sub',st['id'],limit=1,offset=1)['items'][0]['id']==sub['id']


@pytest.mark.parametrize('kind', ['study','sub','line'])
def test_missing_ids_and_parents(store,kind):
    with pytest.raises(KeyError):store.get(kind,'f'*32)
    with pytest.raises(KeyError):store.delete(kind,'f'*32)
    if kind!='study':
        with pytest.raises(KeyError):store.create(kind,dict(title='x'),'f'*32)
        with pytest.raises(KeyError):store.listing(kind,'f'*32)


@pytest.mark.parametrize('value', ['', '  ', 'x'*161, None, 12, 'bad\0title'])
def test_invalid_names(value):
    with pytest.raises(ValueError):Metadata(title=value)


@pytest.mark.parametrize('url',['javascript:alert(1)','file:///etc/passwd','https://u:p@example.com','https://','https://example.com:bad','https://example.com/a b'])
def test_invalid_source_urls(url):
    with pytest.raises(ValueError):SubStudyInput(title='t',source_url=url)


def test_unknown_fields_and_fen_edit_not_available(store):
    with pytest.raises(ValueError):store.create('line',dict(title='t',initial_fen=chess.STARTING_FEN),'missing')
    assert SubStudyInput(title=' t ',source_url='   ').model_dump()==dict(title='t',description='',source_url=None)


def test_safe_aliases_and_foreign_database(tmp_path,monkeypatch):
    protected=(tmp_path/'practice.db').resolve();monkeypatch.setenv('CHESS_COACH_PRACTICE_DB',str(protected))
    with pytest.raises(StudyUnavailable):StudyStore(protected)
    foreign=(tmp_path/'foreign.db').resolve()
    with sqlite3.connect(foreign) as c:c.execute('CREATE TABLE personal (x TEXT)')
    before=foreign.read_bytes()
    with pytest.raises(StudyUnavailable):StudyStore(foreign).initialize()
    assert foreign.read_bytes()==before
    link=tmp_path/'alias.db';link.symlink_to(foreign)
    from src.evaluation_cache import CacheUnavailable
    with pytest.raises(CacheUnavailable):StudyStore(link)


def test_concurrent_init_and_append(tmp_path):
    path=(tmp_path/'studies.db').resolve()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:StudyStore(path).initialize(),range(2)))
    store=StudyStore(path);st=store.create('study',dict(title='Concurrent'))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda i:StudyStore(path).create('sub',dict(title=str(i)),st['id']),range(6)))
    assert [x['sort_order'] for x in store.listing('sub',st['id'])['items']]==list(range(6))


def test_online_backup_is_consistent_and_no_overwrite(store,tmp_path):
    st,_,_=hierarchy(store);backup=(tmp_path/'study-copy.sqlite3').resolve();store.backup(backup)
    assert StudyStore(backup).get('study',st['id'])['title']==st['title']
    with pytest.raises(StudyConflict):store.backup(backup)


def test_backup_cannot_alias_other_configured_store(store,tmp_path,monkeypatch):
    target=(tmp_path/'uninitialized-cache.db').resolve()
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(target))
    with pytest.raises(StudyUnavailable):store.backup(target)
    assert not target.exists()
