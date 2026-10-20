"""Operator-controlled progressive work; read routes never initialize/dispatch."""
import hmac
import os
import sqlite3
from fastapi import APIRouter,HTTPException,Header,Query
from api.evaluations import BoundedRoute
from src.evaluation_cache import CacheUnavailable
from src.evaluation_models import canonical_json
from src.progressive_models import ProgressiveLimits,Schedule
from src.progressive_store import ProgressiveStore,ProgressiveError

router=APIRouter(prefix='/api/progressive-analysis',tags=['progressive-analysis'],route_class=BoundedRoute)


def store():
    try:
        return ProgressiveStore(os.environ.get('CHESS_COACH_PROGRESSIVE_DB'),os.environ.get('CHESS_COACH_DB','data/library.db'),
                         ProgressiveLimits.model_validate_json(os.environ.get('CHESS_COACH_PROGRESSIVE_LIMITS','{}')))
    except (ValueError,CacheUnavailable,ProgressiveError):raise HTTPException(503,'Progressive store unavailable') from None


def authorize(key):
    expected=os.environ.get('CHESS_COACH_PROGRESSIVE_KEY','')
    if len(expected)<16 or not key or not hmac.compare_digest(key.encode(),expected.encode()):raise HTTPException(403,'Operator key required')


def invoke(call):
    try:
        result=call()
        if len(canonical_json(result).encode())>2*1024**2:raise HTTPException(413,'Response budget exceeded; request smaller page')
        return result
    except KeyError:raise HTTPException(404,'Unknown progressive job') from None
    except ValueError:raise HTTPException(422,'Invalid progressive request/policy') from None
    except (sqlite3.DatabaseError,ProgressiveError,CacheUnavailable):raise HTTPException(503,'Progressive store unavailable or busy') from None


@router.post('/jobs',status_code=202)
def enqueue_one(body:Schedule,x_progressive_key:str|None=Header(None)):
    authorize(x_progressive_key)
    if len(body.game_ids)!=1:raise HTTPException(422,'Single game required')
    return invoke(lambda:store().enqueue(body))


@router.post('/jobs/batch',status_code=202)
def enqueue_batch(body:Schedule,x_progressive_key:str|None=Header(None)):
    authorize(x_progressive_key);return invoke(lambda:store().enqueue(body))


@router.get('/jobs')
def list_jobs(limit:int=Query(50,ge=1,le=100),after:str=Query('',max_length=64)):
    def read():
        with store().connection() as c:return {'items':[dict(r) for r in c.execute('SELECT id,game_id,state,phase,reason,revision FROM jobs WHERE id>? ORDER BY id LIMIT ?',(after,limit))]}
    return invoke(read)


@router.get('/jobs/{jid}')
@router.get('/jobs/{jid}/result')
def result(jid:str,limit:int=Query(20,ge=1,le=100),after:str=Query('',max_length=64)):
    return invoke(lambda:store().get(jid,limit,after))


@router.get('/games/{game_id}/progress')
def game_progress(game_id:str):
    def read():
        s=store()
        with s.connection() as c:rows=c.execute('SELECT id FROM jobs WHERE game_id=? ORDER BY created DESC LIMIT 10',(game_id,)).fetchall()
        return {'items':[s.get(x[0],1) for x in rows]}
    return invoke(read)


@router.post('/jobs/{jid}/cancel',status_code=202)
def cancel(jid:str,x_progressive_key:str|None=Header(None)):
    authorize(x_progressive_key)
    return invoke(lambda:(store().cancel(jid) or {'job_id':jid,'status':'CANCEL_REQUESTED_OR_DETACHED'}))


@router.post('/jobs/{jid}/retry',status_code=202)
def retry(jid:str,x_progressive_key:str|None=Header(None)):
    authorize(x_progressive_key);return invoke(lambda:(store().retry(jid) or {'job_id':jid,'status':'RETRY_REQUESTED'}))


@router.get('/health')
def health():return invoke(lambda:store().status())
