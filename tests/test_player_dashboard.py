import hashlib
import sqlite3
import pytest
from fastapi.testclient import TestClient
from src.library import GameLibrary


@pytest.fixture
def dashboard(tmp_path,monkeypatch):
    path=tmp_path/'library.db'
    ids=[]
    with GameLibrary(str(path)) as lib:
        for name,source,white,black,date in [('a','chess.com','Hero','Other','2026.01.02'),('b','chess.com','hero','Else','2026.01.01'),
           ('c','lichess','Hero','Other','2026.01.03'),('d','chess.com','Heron','Other','2026.01.04'),
           ('e','local','hero','Other','????.??.??'),('f','local','Hero','Other','2026.01.05'),('g','chess.com','HERO','hero','2026.01.06')]:
            g,_=lib.add_game(f'[White "{white}"]\n[Black "{black}"]\n[Event "{name}"]\n[Date "{date}"]\n[Result "1-0"]\n[TimeControl "300+0"]\n\n1. e4 e5 1-0',source=source)
            ids.append(g.game_id)
    from api import main
    monkeypatch.setattr(main,'DB_PATH',str(path));monkeypatch.setenv('CHESS_COACH_READ_ONLY','true')
    with TestClient(main.app) as client:yield client,path,ids


def test_identity_discovery_and_scoped_cursor(dashboard):
    client,path,ids=dashboard
    first=client.get('/api/players/identities',params={'source':'chess.com','q':'Hero','limit':1}).json()
    assert len(first['items'])==1 and first['next_cursor']
    assert first['items'][0]['matching']=='exact_ascii_nocase'
    query={'source':'chess.com','username':'Hero','limit':1}
    page=client.get('/api/players/games',params=query).json()
    assert page['selected_total']==2 and page['matching_total']==2
    assert page['items'][0]['game_id']==ids[0]
    assert 'pgn' not in page['items'][0]
    second=client.get('/api/players/games',params={**query,'after':page['next_cursor']}).json()
    assert second['items'][0]['game_id']==ids[1] and second['next_cursor'] is None
    assert client.get('/api/players/games',params={**query,'source':'lichess','after':page['next_cursor']}).status_code==422
    assert client.get('/api/players/games',params={**query,'after':'invalid'}).status_code==422
    assert client.get('/api/players/games',params={**query,'source':'local'}).json()['selected_total']==1
    assert client.get('/api/players/games',params={**query,'date_from':'2026-01-02'}).json()['selected_total']==1
    assert client.get('/api/players/games',params={**query,'time_control':'600'}).json()['selected_total']==0
    assert client.get('/api/players/games',params={**query,'game_ids':ids[2]}).json()['matching_total']==0


def test_readonly_startup_and_legacy_reads_do_not_initialize(dashboard,monkeypatch):
    client,path,ids=dashboard
    before=path.read_bytes()
    monkeypatch.setattr(GameLibrary,'_init_db',lambda *_:(_ for _ in ()).throw(AssertionError('Initialization forbidden')))
    assert client.get('/api/players/overview',params={'source':'chess.com','username':'Hero'}).status_code==200
    assert client.get('/api/games/'+ids[0]).status_code==200
    assert client.get('/api/games/'+ids[0]+'/analysis').status_code==404
    assert client.post('/api/progressive-analysis/jobs',json={}).status_code==403
    assert client.post('/api/evaluations/resolve',json={}).status_code==403
    assert client.get('/api/management/summary').status_code==403
    assert client.get('/api/coach/chat').status_code==403
    assert path.read_bytes()==before
    with GameLibrary(str(path),read_only=True) as lib:
        with pytest.raises(sqlite3.OperationalError):lib._conn.execute('DELETE FROM games')


def test_opening_examples_bounded_and_cohort_scoped(dashboard):
    client,path,ids=dashboard
    response=client.get('/api/players/fast-profile',params={'source':'chess.com','username':'Hero'}).json()
    examples=[gid for row in response['repertoire']['openings'] for gid in row['evidence_game_ids']]
    assert set(examples)==set(ids[:2])
    assert all(len(x['evidence_game_ids'])<=5 for x in response['repertoire']['openings'])
    assert response['coverage']['ambiguous_identity_games']==1


def test_missing_readonly_library_never_created(tmp_path,monkeypatch):
    from api import main
    path=tmp_path/'missing.db';monkeypatch.setattr(main,'DB_PATH',str(path));monkeypatch.setenv('CHESS_COACH_READ_ONLY','true')
    with TestClient(main.app) as client:
        assert client.get('/api/players/identities').status_code==503
    assert not path.exists()


def test_position_games_are_exact_scoped_and_examples_keep_totals(dashboard):
    client,path,ids=dashboard
    from src.positions import PositionIndex
    with GameLibrary(str(path)) as lib:
        index=PositionIndex(lib._conn)
        for gid in ids[:3]:index.index_game(gid)
        position=lib._conn.execute('SELECT position_id FROM position_occurrences WHERE game_id=? AND ply=1',(ids[0],)).fetchone()[0]
    query={'source':'chess.com','username':'Hero','position_id':position}
    data=client.get('/api/players/games',params=query).json()
    assert {x['game_id'] for x in data['items']}==set(ids[:2])
    assert all(x['first_ply']==1 for x in data['items'])
    assert data['position_coverage']['complete_games']==2
    assert not data['position_coverage']['partial_index']
    assert data['position_coverage']['absence_is_conclusive']
    subset=client.get('/api/players/games',params={**query,'game_ids':ids[0]}).json()
    assert subset['selected_total']==2 and subset['matching_total']==1


def test_readonly_startup_skips_recovery_and_local_lookup_never_dispatches(tmp_path,monkeypatch):
    from api import main
    from src.progressive_engine import UCIProcess
    path=tmp_path/'library.db'
    with GameLibrary(str(path)):pass
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(main,'DB_PATH',str(path));monkeypatch.setenv('CHESS_COACH_READ_ONLY','true')
    def forbidden(*args,**kwargs):raise AssertionError('Initialization, recovery or engine dispatch forbidden')
    monkeypatch.setattr(GameLibrary,'_init_db',forbidden)
    monkeypatch.setattr(main.import_manager,'recover_stale_imports',forbidden)
    monkeypatch.setattr(UCIProcess,'__init__',forbidden)
    monkeypatch.setenv('CHESS_COACH_EVAL_CACHE_DB',str(tmp_path/'missing-cache.db'))
    monkeypatch.setenv('CHESS_COACH_PROGRESSIVE_DB',str(tmp_path/'missing-progressive.db'))
    import httpx
    monkeypatch.setattr(httpx,'AsyncClient',forbidden)
    with TestClient(main.app) as client:
        assert client.get('/api/players/identities').json()['items']==[]
        assert client.get('/api/progressive-analysis/games/absent/progress').status_code==503
        import chess
        response=client.post('/api/evaluations/lookup-batch',json={'items':[{'context':{'fen':chess.STARTING_FEN,'root_fen':chess.STARTING_FEN,'root_history_complete':True},'require_trusted':True,'allow_cloud':False}]})
        assert response.status_code==200 and response.json()['items'][0]['status']=='UNAVAILABLE'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert not (tmp_path/'missing-cache.db').exists() and not (tmp_path/'missing-progressive.db').exists()


def test_partial_position_game_search_does_not_claim_absence(dashboard):
    client,path,ids=dashboard
    from src.positions import PositionIndex
    with GameLibrary(str(path)) as lib:
        PositionIndex(lib._conn).index_game(ids[0])
        position=lib._conn.execute('SELECT position_id FROM position_occurrences WHERE game_id=? AND ply=1',(ids[0],)).fetchone()[0]
    data=client.get('/api/players/games',params={'source':'chess.com','username':'Hero','position_id':position}).json()
    assert data['matching_total']==1 and data['selected_total']==2
    assert data['position_coverage']=={'total_games':2,'complete_games':1,'partial_index':True,'absence_is_conclusive':False}
