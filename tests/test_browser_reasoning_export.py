"""Explicitly invented receipts test verifier contracts, never genuine engine execution."""
import copy
import pytest
import chess
from src.browser_reasoning_export import verify_package, OPTIONS, JS_SHA, WASM_SHA
from src.reasoning import quality
from src.reasoning_import import ArtifactStore


def mock_receipts(depth=16, black=False):
    session='12345678-1234-1234-1234-123456789abc'
    b=chess.Board();moves=[]
    if black:b.push_uci('e2e4');moves=['e2e4']
    rows=[]
    def add(kind,text=''):
        rows.append(dict(seq=len(rows),elapsed_ms=len(rows),worker=session+':1',session=session,kind=kind,text=text))
    add('worker_started');add('send','uci');add('recv','id name Stockfish 19 Lite WASM')
    for name,value in OPTIONS.items():
        typ='check' if type(value) is bool else 'spin' if type(value) is int else 'string'
        rendered=str(value).lower() if type(value) is bool else str(value)
        add('recv',f'option name {name} type {typ} default {rendered}'+(' min 0 max 33554432' if typ=='spin' else ''))
    add('recv','option name Clear Hash type button');add('recv','uciok')
    for name,value in OPTIONS.items():add('send',f'setoption name {name} value '+(str(value).lower() if type(value) is bool else str(value)))
    add('send','isready');add('recv','readyok');add('send','ucinewgame');add('send','setoption name Clear Hash');add('send','isready');add('recv','readyok')
    add('send','position fen '+chess.STARTING_FEN+(' moves e2e4' if black else ''));add('send','go depth 16 movetime 20000')
    pvs=['e7e5','c7c5','e7e6'] if black else ['e2e4','d2d4','g1f3']
    for rank,pv in enumerate(pvs,1):add('recv',f'info depth {depth} multipv {rank} score cp {30-rank} nodes 100 time 10 pv {pv}')
    add('recv','bestmove '+pvs[0]);add('terminated')
    return dict(schema_version='browser-reasoning-receipts/1',session=session,context=dict(fen=b.fen(),root_fen=chess.STARTING_FEN,moves=moves,root_history_complete=True),artifacts=dict(js_sha256=JS_SHA,wasm_sha256=WASM_SHA),events=rows,truncated=False,result=dict(status='complete',depth=depth,workerStarts=1),trust='BROWSER_OBSERVED_NOT_ATTESTED')


def test_structural_receipts_do_not_attest(tmp_path):
    b=verify_package(mock_receipts())
    assert quality(b.records[0])=='QUALIFIED_RECORDED_SEARCH'
    assert b.records[0].settings.applied_options['Clear Hash']=='triggered_per_context'
    with pytest.raises(ValueError,match='EXPLICIT_OPERATOR_ATTESTATION_REQUIRED'):
        ArtifactStore(tmp_path/'never-created').accept(b)
    assert not (tmp_path/'never-created').exists()


def test_depth_refusal_and_black_orientation():
    b=verify_package(mock_receipts(14,True))
    assert quality(b.records[0])=='DEPTH_BELOW_POLICY_MINIMUM'
    assert b.records[0].lines[0].score.cp==-29


@pytest.mark.parametrize('tamper', ['ready','uciok','reset','clear','history','session','order','options','hash','truncated','attestation','illegal','mixed','bestmove','termination','score_type'])
def test_tampered_or_incomplete_receipts_refused(tamper):
    p=copy.deepcopy(mock_receipts());events=p['events']
    if tamper in ['ready','uciok','reset','clear','termination']:
        text={'ready':'readyok','uciok':'uciok','reset':'ucinewgame','clear':'setoption name Clear Hash','termination':''}[tamper]
        index=next(i for i,x in enumerate(events) if (x['kind']=='terminated' if tamper=='termination' else x['text']==text))
        events.pop(index)
        for i,e in enumerate(events):e['seq']=i
    elif tamper=='history':next(x for x in events if x['text'].startswith('position '))['text']+=' moves e2e4'
    elif tamper=='session':events[-1]['session']='0'*36
    elif tamper=='order':events[2]['seq']=99
    elif tamper=='options':next(x for x in events if x['text']=='setoption name Hash value 16')['text']='setoption name Hash value 64'
    elif tamper=='hash':p['artifacts']['wasm_sha256']='a'*64
    elif tamper=='truncated':p['truncated']=True
    elif tamper=='attestation':p['acceptance']={'origin':'ENGINE_VERIFIED'}
    elif tamper=='illegal':next(x for x in events if ' pv e2e4' in x['text'])['text']+=' a1a8'
    elif tamper=='mixed':next(x for x in events if 'multipv 2' in x['text'])['text']=next(x for x in events if 'multipv 2' in x['text'])['text'].replace('depth 16','depth 15')
    elif tamper=='bestmove':next(x for x in events if x['text'].startswith('bestmove'))['text']='bestmove d2d4'
    elif tamper=='score_type':next(x for x in events if 'multipv 1' in x['text'])['text']=next(x for x in events if 'multipv 1' in x['text'])['text'].replace('score cp 29','score mate 0')
    with pytest.raises(ValueError):verify_package(p)
