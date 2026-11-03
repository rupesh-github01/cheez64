"""Local study metadata CRUD; no API initialization or connections to other stores."""
import ipaddress
import os
import sqlite3
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.routing import APIRoute
from src.study_models import Metadata,SubStudyInput,StudyView,SubStudyView,LineView,Page,Reorder
from src.study_store import StudyStore,StudyUnavailable,StudyConflict
from src.evaluation_cache import CacheUnavailable


class StudyRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def bounded(request):
            if request.method not in ('GET','HEAD','OPTIONS'):
                body=bytearray()
                async for chunk in request.stream():
                    if len(body)+len(chunk)>32768:raise HTTPException(413,'Study metadata request exceeds 32 KiB')
                    body.extend(chunk)
                request._body=bytes(body)
            return await handler(request)
        return bounded


router=APIRouter(prefix='/api/studies',tags=['studies'],route_class=StudyRoute)

def writable():return os.environ.get('CHESS_COACH_READ_ONLY','').lower()!='true'

def authorize(request):
    if not writable():raise HTTPException(403,'Study writes disabled in read-only mode')
    host=urlsplit('http://'+request.headers.get('host','')).hostname
    client=request.client.host if request.client else ''
    try:local=ipaddress.ip_address(client).is_loopback
    except ValueError:local=client=='testclient'
    if (not local or host not in ('localhost','127.0.0.1','::1','testserver') or
        request.headers.get('origin') not in (None,'http://localhost:3000','http://127.0.0.1:3000') or
        request.headers.get('x-study-action')!='local-study' or
        request.headers.get('content-type','').split(';')[0]!='application/json'):
        raise HTTPException(403,'Local JSON study action required')


def invoke(call):
    try:return call(StudyStore())
    except KeyError:raise HTTPException(404,'Study item or parent not found') from None
    except StudyConflict as e:raise HTTPException(409,str(e)) from None
    except (StudyUnavailable,CacheUnavailable):raise HTTPException(503,'Study store uninitialized, incompatible or unsafe. Explicit operator setup is required.') from None
    except ValueError:raise HTTPException(400,'Invalid study metadata or ordering') from None
    except sqlite3.IntegrityError:raise HTTPException(409,'Study relationship or order conflict') from None
    except (sqlite3.DatabaseError,OSError):raise HTTPException(503,'Independent study store unavailable or busy') from None


@router.get('/status')
def status():
    def read(s):return {**s.status(),'writes_enabled':writable()}
    return invoke(read)


@router.get('',response_model=Page[StudyView])
def studies(limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0)):
    return invoke(lambda s:s.listing('study',limit=limit,offset=offset))


@router.post('',response_model=StudyView,status_code=201)
def create_study(body:Metadata,request:Request):
    authorize(request);return invoke(lambda s:s.create('study',body.model_dump()))


@router.get('/{study_id}',response_model=StudyView)
def study(study_id:str):return invoke(lambda s:s.get('study',study_id))


@router.put('/{study_id}',response_model=StudyView)
def update_study(study_id:str,body:Metadata,request:Request):
    authorize(request);return invoke(lambda s:s.update('study',study_id,body.model_dump()))


@router.delete('/{study_id}')
def delete_study(study_id:str,request:Request):
    authorize(request);return invoke(lambda s:s.delete('study',study_id))


@router.get('/{study_id}/sub-studies',response_model=Page[SubStudyView])
def subs(study_id:str,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0)):
    return invoke(lambda s:s.listing('sub',study_id,limit,offset))


@router.post('/{study_id}/sub-studies',response_model=SubStudyView,status_code=201)
def create_sub(study_id:str,body:SubStudyInput,request:Request):
    authorize(request);return invoke(lambda s:s.create('sub',body.model_dump(),study_id))


@router.post('/{study_id}/sub-studies/reorder')
def reorder_sub(study_id:str,body:Reorder,request:Request):
    authorize(request);return invoke(lambda s:s.reorder('sub',study_id,body.ids))


@router.get('/sub-studies/{sub_id}',response_model=SubStudyView)
def sub(sub_id:str):return invoke(lambda s:s.get('sub',sub_id))


@router.put('/sub-studies/{sub_id}',response_model=SubStudyView)
def update_sub(sub_id:str,body:SubStudyInput,request:Request):
    authorize(request);return invoke(lambda s:s.update('sub',sub_id,body.model_dump()))


@router.delete('/sub-studies/{sub_id}')
def delete_sub(sub_id:str,request:Request):
    authorize(request);return invoke(lambda s:s.delete('sub',sub_id))


@router.get('/sub-studies/{sub_id}/lines',response_model=Page[LineView])
def lines(sub_id:str,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0)):
    return invoke(lambda s:s.listing('line',sub_id,limit,offset))


@router.post('/sub-studies/{sub_id}/lines',response_model=LineView,status_code=201)
def create_line(sub_id:str,body:Metadata,request:Request):
    authorize(request);return invoke(lambda s:s.create('line',body.model_dump(),sub_id))


@router.post('/sub-studies/{sub_id}/lines/reorder')
def reorder_line(sub_id:str,body:Reorder,request:Request):
    authorize(request);return invoke(lambda s:s.reorder('line',sub_id,body.ids))


@router.get('/lines/{line_id}',response_model=LineView)
def line(line_id:str):return invoke(lambda s:s.get('line',line_id))


@router.put('/lines/{line_id}',response_model=LineView)
def update_line(line_id:str,body:Metadata,request:Request):
    authorize(request);return invoke(lambda s:s.update('line',line_id,body.model_dump()))


@router.delete('/lines/{line_id}')
def delete_line(line_id:str,request:Request):
    authorize(request);return invoke(lambda s:s.delete('line',line_id))
