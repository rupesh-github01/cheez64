"""Read-only collections for the unified dashboard. No initialization or replay."""
import sqlite3
import time
from typing import Literal
from fastapi import APIRouter,Query,HTTPException
from src.fast_profile import profile_reader
from src.player_dashboard import identities,games

router=APIRouter(prefix='/api/players',tags=['players'])

def read(call):
    from api import main
    try:
        with profile_reader(main.DB_PATH) as conn:
            deadline=time.monotonic()+3
            conn.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
            return call(conn)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except sqlite3.DatabaseError:raise HTTPException(503,'Player collection unavailable, incompatible or query budget exceeded') from None

@router.get('/identities')
def accounts(source:str|None=Query(None,min_length=1,max_length=100),q:str=Query('',max_length=100),
             limit:int=Query(20,ge=1,le=50),after:str=Query('',max_length=1000)):
    return read(lambda c:identities(c,source,q,limit,after))

@router.get('/games')
def selected_games(source:str=Query(...,min_length=1,max_length=100),username:str=Query(...,min_length=1,max_length=200),
                   color:Literal['either','white','black']='either',date_from:str|None=None,date_to:str|None=None,
                   time_control:str|None=Query(None,max_length=100),result:Literal['1-0','0-1','1/2-1/2','*']|None=None,
                   analysis_status:Literal['ANALYZED','NOT_ANALYZED','ANALYZING','FAILED']|None=None,
                   eco:str|None=Query(None,pattern='^[A-E][0-9]{2}$'),position_id:int|None=Query(None,ge=1),
                   game_ids:str|None=Query(None,max_length=2020),limit:int=Query(20,ge=1,le=50),after:str=Query('',max_length=1000)):
    return read(lambda c:games(c,source,username,color,date_from,date_to,time_control,result,analysis_status,eco,position_id,game_ids,limit,after))
