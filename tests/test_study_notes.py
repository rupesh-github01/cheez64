"""Notes and explicit migration use only canonical disposable stores/copies."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import chess
import pytest
from src.study_store import StudyStore,StudyConflict,StudyUnavailable,StudyMigrationRequired,DDL
from test_studies_api import api,HEADERS

@pytest.fixture
def notes(tmp_path):
    s=StudyStore((tmp_path/'notes.db').resolve());s.initialize()
    st=s.create('study',{'title':"King's Gambit"});sub=s.create('sub',{'title':'Video'},st['id']);line=s.create('line',{'title':'Falkbeer'},sub['id'])
    seq=s.sequence(line['id']);seq=s.save_sequence(line['id'],dict(initial_fen=chess.STARTING_FEN,moves=['e2e4','e7e5','f2f4','d7d5','e4d5'],expected_updated_at=seq['updated_at']))
    other=s.create('line',{'title':'Unrelated'},sub['id']);return s,seq,other

def body(seq,text):return dict(comment=text,expected_updated_at=seq['updated_at'])
def save(s,seq,index,text):
    s.save_comment(seq['line_id'],None if index is None else seq['moves'][index]['id'],body(seq,text));return s.sequence(seq['line_id'])

def test_exact_notes_edit_clear_reopen_and_metadata_isolation(notes):
    s,seq,other=notes;before_other=s.get('line',other['id']);text='  Black’s counterattack! ♞\n\nRemember <script>alert("no")</script> & “中心”.\n'
    seq=save(s,seq,3,text);seq=save(s,seq,4,'Separate explanation.');seq=save(s,seq,None,'Starting position\nKeep castling rights.')
    reopened=StudyStore(s.path).sequence(seq['line_id']);assert reopened==seq
    assert reopened['moves'][3]['comment']==text and reopened['moves'][4]['comment']=='Separate explanation.'
    assert reopened['starting_note']=='Starting position\nKeep castling rights.' and reopened['moves'][3]['has_comment']
    assert s.get('line',other['id'])==before_other
    assert 'starting_note' not in s.get('line',seq['line_id']) and 'starting_note' not in s.listing('line',other['sub_study_id'])['items'][0]
    seq=save(s,seq,3,'Edited');seq=save(s,seq,4,'');seq=save(s,seq,None,'')
    assert seq['moves'][3]['comment']=='Edited' and not seq['moves'][4]['has_comment'] and seq['starting_note']==''

@pytest.mark.parametrize('invalid',[{'comment':'x'*5001},{'comment':'bad\0note'},{'comment':None},{'comment':12},{'comment':'x','uci':'e2e4'}])
def test_invalid_notes_rollback(notes,invalid):
    s,seq,_=notes
    with pytest.raises(ValueError):s.save_comment(seq['line_id'],seq['moves'][0]['id'],{**body(seq,''),**invalid})
    assert s.sequence(seq['line_id'])==seq

def test_wrong_line_missing_deleted_and_revision_fencing(notes):
    s,seq,other=notes;move_id=seq['moves'][3]['id']
    with pytest.raises(KeyError):s.save_comment(other['id'],move_id,body(s.sequence(other['id']),'Wrong'))
    with pytest.raises(KeyError):s.save_comment(seq['line_id'],'missing',body(seq,'Wrong'))
    new=save(s,seq,3,'Correct')
    for target in [None,move_id]:
        with pytest.raises(StudyConflict):s.save_comment(seq['line_id'],target,body(seq,'Stale'))
    with pytest.raises(StudyConflict):s.save_sequence(seq['line_id'],dict(initial_fen=seq['initial_fen'],moves=[m['uci'] for m in seq['moves']],expected_updated_at=seq['updated_at']))
    s.save_sequence(seq['line_id'],dict(initial_fen=new['initial_fen'],moves=['e2e4'],expected_updated_at=new['updated_at'],confirm_discard=True))
    with pytest.raises(KeyError):s.save_comment(seq['line_id'],move_id,body(s.sequence(seq['line_id']),'Deleted'))


def test_concurrent_note_edits_and_sequence_conflict(notes):
    s,seq,_=notes
    def write(text):
        try:s.save_comment(seq['line_id'],seq['moves'][0]['id'],body(seq,text));return 'saved'
        except StudyConflict:return 'conflict'
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(write,['one','two']))==['conflict','saved']
    assert s.sequence(seq['line_id'])['moves'][0]['comment'] in ('one','two')


def test_replace_tail_never_transfers_notes_by_ply(notes):
    s,seq,_=notes
    # The same prefix is retained when 2...exf4 becomes 2...d5.
    seq=s.save_sequence(seq['line_id'],dict(initial_fen=seq['initial_fen'],moves=['e2e4','e7e5','f2f4','e5f4','g1f3'],expected_updated_at=seq['updated_at'],confirm_discard=True))
    for index in range(5):seq=save(s,seq,index,f'note {index}')
    seq=save(s,seq,None,'Root remains');before=seq
    request=dict(initial_fen=seq['initial_fen'],moves=['e2e4','e7e5','f2f4','d7d5'],expected_updated_at=seq['updated_at'])
    with pytest.raises(StudyConflict):s.save_sequence(seq['line_id'],request)
    assert s.sequence(seq['line_id'])==before
    seq=s.save_sequence(seq['line_id'],{**request,'confirm_discard':True})
    assert seq['moves'][:3]==before['moves'][:3] and seq['starting_note']=='Root remains'
    assert seq['moves'][3]['comment']=='' and seq['moves'][3]['id']!=before['moves'][3]['id']
    with s.connection() as c:assert not c.execute('SELECT 1 FROM line_moves WHERE id IN (?,?)',(before['moves'][3]['id'],before['moves'][4]['id'])).fetchone()


def test_root_note_changes_only_with_confirmed_new_root(notes):
    s,seq,_=notes;seq=save(s,seq,None,'Applies to standard root')
    seq=s.save_sequence(seq['line_id'],dict(initial_fen=seq['initial_fen'],moves=[],expected_updated_at=seq['updated_at'],confirm_discard=True))
    assert seq['starting_note']=='Applies to standard root'
    request=dict(initial_fen='k7/8/8/3pP3/8/8/8/4K3 w - d6 0 20',moves=[],expected_updated_at=seq['updated_at'])
    with pytest.raises(StudyConflict):s.save_sequence(seq['line_id'],request)
    assert s.save_sequence(seq['line_id'],{**request,'confirm_discard':True})['starting_note']==''


def legacy_store(path):
    with sqlite3.connect(path) as c:
        for statement in DDL:
            statement=statement.replace('CHECK(version=2)','CHECK(version=1)').replace(" starting_note TEXT NOT NULL DEFAULT '' CHECK(length(starting_note)<=5000),\n",'')
            c.execute(statement)
        c.execute('INSERT INTO study_meta VALUES(1,1)')
    return StudyStore(path)


def test_explicit_migration_preserves_disposable_backup_and_rows(tmp_path):
    s=legacy_store((tmp_path/'legacy.db').resolve());st=s.create('study',{'title':'Old'});sub=s.create('sub',{'title':'Video'},st['id']);i=s.create('line',{'title':'Line'},sub['id'])['id']
    seq=s.sequence(i);seq=s.save_sequence(i,dict(initial_fen=seq['initial_fen'],moves=['e2e4'],expected_updated_at=seq['updated_at']))
    with s.connection(True) as c:c.execute("UPDATE line_moves SET comment='Existing note' WHERE line_id=?",(i,))
    before=s.sequence(i);s.initialize();assert s.status()['schema_version']==1 and not before['notes_enabled']
    with pytest.raises(StudyMigrationRequired):s.save_comment(i,None,body(before,'Not yet'))
    backup=(tmp_path/'disposable-copy.db').resolve();s.backup(backup);copy=StudyStore(backup)
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:copy.migrate(),range(2)))
    copy.migrate();after=copy.sequence(i)
    assert after=={**before,'starting_note':'','notes_enabled':True} and copy.status()['schema_version']==2
    assert s.status()['schema_version']==1 and s.sequence(i)==before
    with copy.connection() as c:assert not c.execute('PRAGMA foreign_key_check').fetchall()
    assert save(copy,after,None,'Now editable')['starting_note']=='Now editable'


def test_failed_migration_rolls_back_and_unknown_missing_refused(tmp_path,monkeypatch):
    s=legacy_store((tmp_path/'legacy.db').resolve());original=s._check
    def fail_after_alter(c):
        result=original(c)
        if result==2:raise StudyUnavailable('fixture failure')
        return result
    monkeypatch.setattr(s,'_check',fail_after_alter)
    with pytest.raises(StudyUnavailable):s.migrate()
    with sqlite3.connect(s.path) as c:
        assert c.execute('SELECT version FROM study_meta').fetchone()[0]==1
        assert 'starting_note' not in {r[1] for r in c.execute('PRAGMA table_info(study_lines)')}
    missing=(tmp_path/'missing.db').resolve()
    with pytest.raises(StudyUnavailable):StudyStore(missing).migrate()
    assert not missing.exists()
    foreign=(tmp_path/'foreign.db').resolve()
    with sqlite3.connect(foreign) as c:c.execute('CREATE TABLE unrelated(value TEXT)')
    with pytest.raises(StudyUnavailable):StudyStore(foreign).migrate()


def test_notes_api_guards_persistence_and_errors(api,monkeypatch,tmp_path):
    c,s=api;st=s.create('study',{'title':'Test'});sub=s.create('sub',{'title':'Sub'},st['id']);i=s.create('line',{'title':'Line'},sub['id'])['id']
    seq=s.sequence(i);seq=s.save_sequence(i,dict(initial_fen=seq['initial_fen'],moves=['e2e4'],expected_updated_at=seq['updated_at']));mid=seq['moves'][0]['id']
    path=f'/api/studies/lines/{i}';comment=path+f'/moves/{mid}/comment';root=path+'/starting-note'
    assert c.put(comment,json=body(seq,'x')).status_code==403
    assert c.put(comment,json=body(seq,'x'),headers={**HEADERS,'origin':'https://invalid.example'}).status_code==403
    text='Exact\nUnicode ♟ & <b>text</b>  '
    r=c.put(comment,json=body(seq,text),headers=HEADERS);assert r.status_code==200
    assert r.json()['move_id']==mid and r.json()['comment']==text
    assert c.put(comment,json=body(seq,'stale'),headers=HEADERS).status_code==409
    seq=c.get(path+'/sequence').json();assert seq['moves'][0]['comment']==text
    assert c.put(root,json=body(seq,'Root'),headers=HEADERS).status_code==200
    seq=c.get(path+'/sequence').json();assert seq['starting_note']=='Root'
    assert c.put(root,json=body(seq,'x'*5001),headers=HEADERS).status_code==422
    assert c.put(root,json=body(seq,'\0'),headers=HEADERS).status_code==422
    assert c.put(path+'/moves/absent/comment',json=body(seq,'x'),headers=HEADERS).status_code==404
    assert c.put('/api/studies/lines/absent/starting-note',json=body(seq,'x'),headers=HEADERS).status_code==404
    monkeypatch.setenv('CHESS_COACH_READ_ONLY','true')
    assert c.put(comment,json=body(seq,'blocked'),headers=HEADERS).status_code==403
    assert c.put(root,json=body(seq,'blocked'),headers=HEADERS).status_code==403
    assert c.get(path+'/sequence').json()==seq
    assert not (tmp_path/'absent-library.db').exists()


def test_legacy_api_never_migrates_on_read_or_write(api,tmp_path,monkeypatch):
    c,_=api;s=legacy_store((tmp_path/'v1.db').resolve());st=s.create('study',{'title':'s'});sub=s.create('sub',{'title':'v'},st['id']);i=s.create('line',{'title':'l'},sub['id'])['id']
    monkeypatch.setenv('CHESS_COACH_STUDY_DB',str(s.path));path=f'/api/studies/lines/{i}'
    seq=c.get(path+'/sequence').json();assert seq['notes_enabled'] is False
    assert c.get('/api/studies/status').json()['migration_required'] is True
    assert c.put(path+'/starting-note',json=body(seq,'No implicit migration'),headers=HEADERS).status_code==503
    assert s.status()['schema_version']==1


def test_maximum_unicode_note_and_invalid_unicode(notes):
    s,seq,_=notes;seq=save(s,seq,None,'♞'*5000)
    assert len(seq['starting_note'])==5000
    with pytest.raises(ValueError):s.save_comment(seq['line_id'],None,body(seq,'\ud800'))
    assert s.sequence(seq['line_id'])==seq
