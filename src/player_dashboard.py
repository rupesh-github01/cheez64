"""Bounded read-only account discovery and exact cohort game navigation."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import time
from .fast_profile import selector_sql, complete_date
from .position_schema import check_version


def cursor_decode(value, key):
    try:
        data=json.loads(base64.urlsafe_b64decode(value.encode()))
        if data['key']!=key or not isinstance(data['after'],list) or len(data['after'])!=2 or not all(isinstance(x,str) and len(x)<=200 for x in data['after']):raise ValueError()
        return data['after']
    except (ValueError,KeyError,TypeError,UnicodeError):raise ValueError('Invalid cursor for this selection') from None


def cursor_encode(after,key):
    return base64.urlsafe_b64encode(json.dumps({'after':after,'key':key},separators=(',',':')).encode()).decode()


def selection_key(values):return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()


def identities(conn,source=None,q='',limit=20,after=''):
    key=selection_key([source,q]);last=cursor_decode(after,key) if after else ['','']
    rows=conn.execute('''WITH labels AS (
        SELECT source,white AS name,game_id FROM games UNION ALL SELECT source,black,game_id FROM games),
        grouped AS (SELECT source,CASE WHEN source IN ('chess.com','lichess') THEN lower(name) ELSE name END AS identity,
        min(name) AS username,count(DISTINCT game_id) AS games FROM labels
        WHERE name IS NOT NULL AND name!='' AND (? IS NULL OR source=?) AND instr(lower(name),lower(?))>0
        GROUP BY source,identity)
        SELECT * FROM grouped WHERE source>? OR (source=? AND identity>?) ORDER BY source,identity LIMIT ?''',
        (source,source,q,last[0],last[0],last[1],limit+1)).fetchall()
    items=[{'source':r['source'],'username':r['username'],'games':r['games'],
            'matching':'exact_ascii_nocase' if r['source'] in ('chess.com','lichess') else 'exact_case_sensitive'} for r in rows[:limit]]
    return {'items':items,'next_cursor':cursor_encode([rows[limit-1]['source'],rows[limit-1]['identity']],key) if len(rows)>limit else None}


def games(conn,source,username,color='either',date_from=None,date_to=None,time_control=None,
          result=None,analysis_status=None,eco=None,position_id=None,game_ids=None,limit=20,after=''):
    cte,params,selector=selector_sql(source,username,color,date_from,date_to,time_control)
    ids=game_ids.split(',') if game_ids else []
    if len(ids)>20 or len(set(ids))!=len(ids) or any(not x or len(x)>100 for x in ids):raise ValueError('At most 20 distinct example IDs')
    filters=[];extra=[]
    for column,value in [('result',result),('analysis_status',analysis_status),('eco',eco)]:
        if value is not None:filters.append(f'g.{column}=?');extra.append(value)
    if ids:filters.append('g.game_id IN ('+','.join('?' for _ in ids)+')');extra.extend(ids)
    position='NULL'
    if position_id is not None:
        try:check_version(conn)
        except ValueError:raise sqlite3.DatabaseError("Position index unavailable or incompatible") from None
        filters.append('''EXISTS(SELECT 1 FROM position_occurrences o JOIN positions p USING(position_id)
          JOIN game_position_index ix ON ix.game_id=o.game_id WHERE o.game_id=g.game_id AND o.position_id=?
          AND p.identity_version=1 AND p.variant='standard' AND ix.indexed_version=1 AND ix.indexed_fingerprint=g.fingerprint)''');extra.append(position_id)
        position='(SELECT min(o.ply) FROM position_occurrences o WHERE o.game_id=g.game_id AND o.position_id=?)'
    selected_total=conn.execute(cte+'SELECT count(*) FROM selected',params).fetchone()[0]
    statuses=dict(conn.execute(cte+'SELECT analysis_status,count(*) FROM selected GROUP BY analysis_status',params).fetchall())
    stored=conn.execute(cte+'SELECT count(*) FROM selected g JOIN analyses a USING(game_id)',params).fetchone()[0]
    filtered=cte+', filtered AS (SELECT g.* FROM selected g'+(' WHERE '+' AND '.join(filters) if filters else '')+')'
    matching=conn.execute(filtered+' SELECT count(*) FROM filtered',params+extra).fetchone()[0]
    conn.create_function('played_date',1,lambda v:complete_date(v).isoformat() if complete_date(v) else '',deterministic=True)
    key=selection_key([selector,result,analysis_status,eco,position_id,ids]);last=cursor_decode(after,key) if after else None
    query=filtered+''', ordered AS (SELECT g.*,played_date(g.date) AS sort_date FROM filtered g)
      SELECT g.game_id,g.white,g.black,g.result,g.date,g.source,g.time_control,g.white_rating,g.black_rating,
      g.analysis_status,g.eco,g.imported_at,g.w,g.sort_date,
      EXISTS(SELECT 1 FROM analyses a WHERE a.game_id=g.game_id) AS legacy_analysis_available,'''+position+''' AS first_ply
      FROM ordered g'''
    values=params+extra+([position_id] if position_id is not None else [])
    if last:query+=' WHERE (g.sort_date<? OR (g.sort_date=? AND g.game_id>?))';values+= [last[0],last[0],last[1]]
    rows=conn.execute(query+' ORDER BY g.sort_date DESC,g.game_id LIMIT ?',values+[limit+1]).fetchall()
    items=[]
    for row in rows[:limit]:
        d=dict(row);d['player_color']='white' if d.pop('w') else 'black';d.pop('sort_date');d['legacy_analysis_available']=bool(d['legacy_analysis_available']);items.append(d)
    return {'selector':selector,'generated_at':datetime.now(timezone.utc).isoformat(),'selected_total':selected_total,
            'matching_total':matching,'legacy_analysis_counts':{'stored_analysis':stored,'by_status':statuses},'items':items,
            'next_cursor':cursor_encode([rows[limit-1]['sort_date'],rows[limit-1]['game_id']],key) if len(rows)>limit else None}
