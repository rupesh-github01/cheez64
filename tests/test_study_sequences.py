"""All stores are explicitly initialized in canonical temporary directories."""
from concurrent.futures import ThreadPoolExecutor
import chess
import pytest
from src.study_store import StudyStore, StudySequenceError, StudyConflict, StudyUnavailable
from test_studies_api import api, HEADERS

@pytest.fixture
def line(tmp_path):
    s=StudyStore((tmp_path/'study.db').resolve());s.initialize()
    st=s.create('study',{'title':'Study'});sub=s.create('sub',{'title':'Sub'},st['id'])
    return s,s.create('line',{'title':'Line'},sub['id'])['id']

def payload(s,i,moves,fen=chess.STARTING_FEN,confirm=False):
    return dict(initial_fen=fen,moves=moves,expected_updated_at=s.sequence(i)['updated_at'],confirm_discard=confirm)

@pytest.mark.parametrize('fen,moves,sans',[
 (chess.STARTING_FEN,['e2e4','e7e5','f2f4','d7d5','e4d5'],['e4','e5','f4','d5','exd5']),
 ('r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1',['e1g1','e8c8'],['O-O','O-O-O']),
 ('k7/8/8/3pP3/8/8/8/4K3 w - d6 0 20',['e5d6'],['exd6']),
 ('7k/P7/8/8/8/8/8/4K3 w - - 0 1',['a7a8n'],['a8=N']),
 ('7k/8/8/8/8/8/7p/4K3 b - - 0 27',['h2h1q'],['h1=Q+']),
 (chess.STARTING_FEN,['f2f3','e7e5','g2g4','d8h4'],['f3','e5','g4','Qh4#']),
])
def test_legal_sequences_reopen_exact_boards(line,fen,moves,sans):
    s,i=line;result=s.save_sequence(i,payload(s,i,moves,fen))
    assert result['initial_fen']==fen
    assert [m['san'] for m in result['moves']]==sans
    board=chess.Board(fen)
    for n,(move,row) in enumerate(zip(moves,result['moves']),1):
        board.push_uci(move);assert row['fen_after']==board.fen(en_passant='fen') and row['ply_index']==n
    assert StudyStore(s.path).sequence(i)==result

@pytest.mark.parametrize('fen',[
 'bad', '8/8/8/8/8/8/8/8 w - - 0 1',
 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -',
 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - -1 1',
 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq e3 0 1',
])
def test_bad_fen_rolls_back(line,fen):
    s,i=line;before=s.sequence(i)
    with pytest.raises(ValueError):s.save_sequence(i,payload(s,i,[],fen))
    assert s.sequence(i)==before

@pytest.mark.parametrize('moves',[
 ['e2e4','e7e4'], ['f2f3','e7e5','g2g4','d8h4','b1c3'],
])
def test_illegal_and_terminal_rollback(line,moves):
    s,i=line;before=s.sequence(i)
    with pytest.raises(StudySequenceError):s.save_sequence(i,payload(s,i,moves))
    assert s.sequence(i)==before


def test_prefix_ids_comments_confirm_and_stale(line):
    s,i=line;s.save_sequence(i,payload(s,i,['e2e4','e7e5','g1f3']))
    with s.connection(True) as c:c.execute("UPDATE line_moves SET comment='Retain me' WHERE line_id=?",(i,))
    old=s.sequence(i);body=payload(s,i,['e2e4','c7c5'])
    with pytest.raises(StudyConflict,match='Confirm replacement'):s.save_sequence(i,body)
    assert s.sequence(i)==old
    body['confirm_discard']=True;new=s.save_sequence(i,body)
    assert new['moves'][0]['id']==old['moves'][0]['id'] and new['moves'][0]['has_comment']
    with s.connection() as c:assert c.execute('SELECT comment FROM line_moves WHERE id=?',(old['moves'][0]['id'],)).fetchone()[0]=='Retain me'
    with pytest.raises(StudyConflict,match='changed'):s.save_sequence(i,body)
    reset=payload(s,i,[],confirm=False)
    with pytest.raises(StudyConflict):s.save_sequence(i,reset)
    reset['confirm_discard']=True;assert s.save_sequence(i,reset)['moves']==[]


def test_append_preserves_all_old_rows_and_metadata_revision(line):
    s,i=line;a=s.save_sequence(i,payload(s,i,['e2e4']))
    b=s.save_sequence(i,payload(s,i,['e2e4','e7e5']))
    assert a['moves'][0]==b['moves'][0]
    body=payload(s,i,['e2e4','e7e5','g1f3']);s.update('line',i,{'title':'Renamed'})
    with pytest.raises(StudyConflict):s.save_sequence(i,body)


def test_concurrent_savers_one_wins(line):
    s,i=line;body=payload(s,i,['e2e4'])
    def write(_):
        try:s.save_sequence(i,body);return 'saved'
        except StudyConflict:return 'conflict'
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(write,range(2)))==['conflict','saved']


def test_corrupt_rows_fail_closed(line):
    s,i=line;s.save_sequence(i,payload(s,i,['e2e4']))
    with s.connection(True) as c:c.execute("UPDATE line_moves SET san='fiction' WHERE line_id=?",(i,))
    with pytest.raises(StudyUnavailable):s.sequence(i)


def test_sequence_api_boundaries(api,monkeypatch,tmp_path):
    c,s=api;st=s.create('study',{'title':'s'});sub=s.create('sub',{'title':'v'},st['id']);i=s.create('line',{'title':'l'},sub['id'])['id']
    path=f'/api/studies/lines/{i}/sequence';before=c.get(path).json();body=payload(s,i,['e2e4','e7e5'])
    assert c.put(path,json=body).status_code==403
    assert c.put(path,json={**body,'moves':['e2e5']},headers=HEADERS).status_code==400
    for invalid in [{**body,'san':'fake'}, {**body,'moves':['0000']}, {**body,'moves':['e2e4']*501}, {**body,'confirm_discard':'true'}]:
        assert c.put(path,json=invalid,headers=HEADERS).status_code==422
    assert c.get(path).json()==before
    saved=c.put(path,json=body,headers=HEADERS);assert saved.status_code==200 and saved.json()['moves'][1]['san']=='e5'
    assert c.put(path,json=body,headers=HEADERS).status_code==409
    assert c.get('/api/studies/lines/missing/sequence').status_code==404
    assert c.put('/api/studies/lines/missing/sequence',json=body,headers=HEADERS).status_code==404
    monkeypatch.setenv('CHESS_COACH_READ_ONLY','true')
    assert c.get(path).status_code==200 and c.put(path,json=body,headers=HEADERS).status_code==403
    missing=(tmp_path/'absent-study.db').resolve();monkeypatch.setenv('CHESS_COACH_STUDY_DB',str(missing))
    assert c.get(path).status_code==503 and not missing.exists()
    assert not (tmp_path/'absent-library.db').exists()


def test_automatic_draw_is_terminal_but_claimable_draw_is_not(line):
    s,i=line;cycle=['g1f3','g8f6','f3g1','f6g8']
    # Threefold is only claimable, so continuing is legal; fivefold is automatic.
    assert len(s.save_sequence(i,payload(s,i,cycle*3))['moves'])==12
    before=s.sequence(i)
    with pytest.raises(StudySequenceError,match='terminal'):
        s.save_sequence(i,payload(s,i,cycle*4+['g1f3']))
    assert s.sequence(i)==before


def test_actual_sql_failure_rolls_back_tail_deletion(line):
    import sqlite3
    s,i=line;s.save_sequence(i,payload(s,i,['e2e4','e7e5']))
    before=s.sequence(i)
    with s.connection(True) as c:
        c.execute("CREATE TRIGGER reject_tail BEFORE INSERT ON line_moves BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError):s.save_sequence(i,payload(s,i,['e2e4','c7c5'],confirm=True))
    assert s.sequence(i)==before
