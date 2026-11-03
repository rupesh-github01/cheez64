import pytest
from fastapi.testclient import TestClient
from src.study_store import StudyStore
HEADERS={'x-study-action':'local-study','origin':'http://localhost:3000','content-type':'application/json'}

@pytest.fixture
def api(tmp_path,monkeypatch):
    from api import main
    monkeypatch.setenv('CHESS_COACH_DB',str((tmp_path/'absent-library.db').resolve()))
    monkeypatch.setattr(main,'DB_PATH',str((tmp_path/'absent-library.db').resolve()))
    monkeypatch.setenv('CHESS_COACH_STUDY_DB',str((tmp_path/'studies.db').resolve()))
    monkeypatch.setenv('CHESS_COACH_READ_ONLY','false')
    s=StudyStore();s.initialize()
    return TestClient(main.app),s


def test_api_hierarchy_and_mutations(api):
    c,s=api
    st=c.post('/api/studies',json={'title':"King's Gambit"},headers=HEADERS);assert st.status_code==201;st=st.json()
    sub=c.post(f"/api/studies/{st['id']}/sub-studies",json={'title':'GothamChess Video','source_url':'https://www.youtube.com/watch?v=test'},headers=HEADERS);assert sub.status_code==201;sub=sub.json()
    line=c.post(f"/api/studies/sub-studies/{sub['id']}/lines",json={'title':'Falkbeer'},headers=HEADERS);assert line.status_code==201;line=line.json()
    for prefix,item in [('',st),('/sub-studies',sub),('/lines',line)]:
        path=f"/api/studies{prefix}/{item['id']}";assert c.get(path).status_code==200
        assert c.put(path,json={'title':'Renamed','description':'Saved'},headers=HEADERS).json()['title']=='Renamed'
    assert c.get('/api/studies').json()['total']==1
    assert c.get(f"/api/studies/{st['id']}/sub-studies").json()['total']==1
    assert c.get(f"/api/studies/sub-studies/{sub['id']}/lines").json()['total']==1
    assert c.post(f"/api/studies/sub-studies/{sub['id']}/lines/reorder",json={'ids':[]},headers=HEADERS).status_code==409
    assert c.delete(f"/api/studies/{st['id']}",headers=HEADERS).status_code==200
    assert c.get(f"/api/studies/lines/{line['id']}").status_code==404


def test_readonly_and_local_guard(api,monkeypatch):
    c,s=api;assert c.post('/api/studies',json={'title':'x'}).status_code==403
    assert c.post('/api/studies',json={'title':'x'},headers={**HEADERS,'origin':'https://attacker.example'}).status_code==403
    st=s.create('study',dict(title='x'));monkeypatch.setenv('CHESS_COACH_READ_ONLY','true')
    assert c.get('/api/studies').json()['total']==1
    assert c.get('/api/studies/status').json()['writes_enabled'] is False
    for method,path in [('POST','/api/studies'),('PUT',f"/api/studies/{st['id']}"),('DELETE',f"/api/studies/{st['id']}")]:assert c.request(method,path,json={'title':'z'},headers=HEADERS).status_code==403
    assert s.get('study',st['id'])['title']=='x'


def test_uninitialized_gets_no_creation(api,monkeypatch,tmp_path):
    c,_=api;missing=(tmp_path/'never-created.db').resolve();monkeypatch.setenv('CHESS_COACH_STUDY_DB',str(missing))
    assert c.get('/api/studies/status').json()['status']=='UNINITIALIZED'
    assert c.get('/api/studies').status_code==503
    assert c.post('/api/studies',json={'title':'x'},headers=HEADERS).status_code==503
    assert not missing.exists() and not list(tmp_path.glob('never-created.db*'))


def test_bad_metadata_parent_and_body_bounds(api):
    c,_=api
    assert c.post('/api/studies',json={'title':' '},headers=HEADERS).status_code==422
    assert c.post('/api/studies',json={'title':'x','initial_fen':'bad'},headers=HEADERS).status_code==422
    assert c.post('/api/studies/'+'f'*32+'/sub-studies',json={'title':'x'},headers=HEADERS).status_code==404
    assert c.post('/api/studies',content=' '*32769,headers=HEADERS).status_code==413
    assert c.get('/api/studies?limit=101').status_code==422
    pre=c.options('/api/studies/x',headers={'origin':'http://localhost:3000','access-control-request-method':'PUT','access-control-request-headers':'x-study-action,content-type'})
    assert pre.status_code==200


def test_individual_deletion_routes_and_sub_order(api):
    c,s=api;st=s.create('study',dict(title='Disposable'));sub=s.create('sub',dict(title='Sub'),st['id']);line=s.create('line',dict(title='Line'),sub['id'])
    assert c.post(f"/api/studies/{st['id']}/sub-studies/reorder",json={'ids':[sub['id']]},headers=HEADERS).status_code==200
    assert c.delete(f"/api/studies/lines/{line['id']}",headers=HEADERS).status_code==200
    assert c.get(f"/api/studies/lines/{line['id']}").status_code==404
    assert c.delete(f"/api/studies/sub-studies/{sub['id']}",headers=HEADERS).status_code==200
    assert c.get(f"/api/studies/{st['id']}").status_code==200
