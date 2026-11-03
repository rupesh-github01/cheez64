"""Independent, explicitly initialized study metadata store. No GameLibrary connection."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
import uuid
import chess
from .evaluation_cache import safe_path, CacheUnavailable, ROOT
from .study_models import Metadata, SubStudyInput, SequenceInput, CommentInput


class StudyUnavailable(ValueError):pass
class StudyConflict(ValueError):pass
class StudySequenceError(ValueError):pass
class StudyMigrationRequired(StudyUnavailable):pass


def replay_sequence(fen, moves):
    """Standard chess only; retain all six FEN fields, including the EP target."""
    import re
    fields=fen.split()
    if len(fields)!=6 or not re.fullmatch(r'0|[1-9][0-9]*',fields[4]) or not re.fullmatch(r'[1-9][0-9]*',fields[5]):
        raise StudySequenceError('Use a six-field FEN with valid move counters')
    try:board=chess.Board(fen)
    except ValueError:raise StudySequenceError('Invalid initial FEN') from None
    if not board.is_valid():raise StudySequenceError('Initial position is not a valid standard chess position')
    canonical=board.fen(en_passant='fen');records=[]
    for index,uci in enumerate(moves,1):
        if board.is_game_over(claim_draw=False):raise StudySequenceError(f'Ply {index}: no moves allowed after a terminal position')
        try:move=chess.Move.from_uci(uci)
        except ValueError:raise StudySequenceError(f'Ply {index}: invalid UCI move') from None
        if move not in board.legal_moves:raise StudySequenceError(f'Ply {index}: illegal move {uci}')
        san=board.san(move);board.push(move)
        records.append({'ply_index':index,'uci':uci,'san':san,'fen_after':board.fen(en_passant='fen')})
    return canonical,records


DDL = (
 'CREATE TABLE study_meta(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version=2))',
 '''CREATE TABLE studies(id TEXT PRIMARY KEY NOT NULL,title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),
 description TEXT NOT NULL CHECK(length(description)<=5000),created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''',
 '''CREATE TABLE sub_studies(id TEXT PRIMARY KEY NOT NULL,study_id TEXT NOT NULL REFERENCES studies(id) ON DELETE CASCADE,
 title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),description TEXT NOT NULL CHECK(length(description)<=5000),source_url TEXT CHECK(source_url IS NULL OR length(source_url)<=2048),
 sort_order INTEGER NOT NULL CHECK(sort_order>=0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(study_id,sort_order))''',
 '''CREATE TABLE study_lines(id TEXT PRIMARY KEY NOT NULL,sub_study_id TEXT NOT NULL REFERENCES sub_studies(id) ON DELETE CASCADE,
 title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 160),description TEXT NOT NULL CHECK(length(description)<=5000),initial_fen TEXT NOT NULL,
 starting_note TEXT NOT NULL DEFAULT '' CHECK(length(starting_note)<=5000),
 sort_order INTEGER NOT NULL CHECK(sort_order>=0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(sub_study_id,sort_order))''',
 '''CREATE TABLE line_moves(id TEXT PRIMARY KEY NOT NULL,line_id TEXT NOT NULL REFERENCES study_lines(id) ON DELETE CASCADE,
 ply_index INTEGER NOT NULL CHECK(ply_index>=1),uci TEXT NOT NULL,san TEXT NOT NULL,fen_after TEXT NOT NULL,comment TEXT NOT NULL DEFAULT '',UNIQUE(line_id,ply_index))''',
 'CREATE INDEX study_recent ON studies(updated_at DESC,id)',
)
TABLES={'study':('studies',None,None),'sub':('sub_studies','study_id','studies'),'line':('study_lines','sub_study_id','sub_studies')}


def now():return datetime.now(timezone.utc).isoformat()


class StudyStore:
    def __init__(self, path=None):
        self.path = safe_path(path or os.environ.get('CHESS_COACH_STUDY_DB') or ROOT/'data/studies.db')
        self._safe()

    def _safe(self, candidate=None):
        path = safe_path(candidate or self.path)
        protected=[ROOT/'data/practice.db',ROOT/'data/progressive.db',ROOT/'data/evaluation_cache.db']
        for key in ('CHESS_COACH_PRACTICE_DB','CHESS_COACH_PROGRESSIVE_DB','CHESS_COACH_EVAL_CACHE_DB'):
            if os.environ.get(key):protected.append(Path(os.environ[key]).resolve())
        for p in protected:
            if path in [p,*[Path(str(p)+s) for s in ('-wal','-shm','-journal')]]:raise StudyUnavailable('STUDY_PATH_ALIAS_FORBIDDEN')
        folders=[ROOT/x for x in ('.git','src','api','web','plan_report','data/reasoning')]
        if os.environ.get('CHESS_COACH_REASONING_DIR'):folders.append(Path(os.environ['CHESS_COACH_REASONING_DIR']).resolve())
        if any(path==p or p in path.parents for p in folders):raise StudyUnavailable('STUDY_PATH_PROTECTED')

    def _check(self,c):
        try:
            row=c.execute('SELECT version FROM study_meta WHERE id=1').fetchone()
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if row is None or row[0] not in (1,2) or c.execute('SELECT count(*) FROM study_meta').fetchone()[0]!=1 or tables != {'study_meta','studies','sub_studies','study_lines','line_moves'}:raise StudyUnavailable('STUDY_STORE_INCOMPATIBLE')
            # Fail before writes if a same-name but foreign schema was supplied.
            for table,cols in [('study_meta',{'id','version'}),('studies',{'id','title','description','created_at','updated_at'}),('sub_studies',{'id','study_id','title','description','source_url','sort_order','created_at','updated_at'}),('study_lines',{'id','sub_study_id','title','description','initial_fen','sort_order','created_at','updated_at'}),('line_moves',{'id','line_id','ply_index','uci','san','fen_after','comment'})]:
                if table=='study_lines' and row[0]==2:cols=cols|{'starting_note'}
                if {r[1] for r in c.execute(f'PRAGMA table_info({table})')}!=cols:raise StudyUnavailable('STUDY_STORE_INCOMPATIBLE')
            return row[0]
        except sqlite3.DatabaseError:raise StudyUnavailable('STUDY_STORE_INCOMPATIBLE') from None

    @contextmanager
    def connection(self, write=False, initialize=False):
        self._safe()
        if not initialize and not self.path.is_file():raise StudyUnavailable('STUDY_STORE_UNINITIALIZED')
        # mode=rw never creates files during CRUD. Only explicit initialize may use rwc.
        c=sqlite3.connect(self.path.as_uri()+('?mode=rwc' if initialize else '?mode=rw' if write else '?mode=ro'),uri=True,timeout=1)
        c.row_factory=sqlite3.Row
        try:
            c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=1000')
            if not write:c.execute('PRAGMA query_only=ON')
            c.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if not initialize:self._check(c)
            yield c
            if write:c.commit()
        finally:c.rollback();c.close()

    def initialize(self):
        with self.connection(True,True) as c:
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():self._check(c);return
            for sql in DDL:c.execute(sql)
            c.execute('INSERT INTO study_meta VALUES(1,2)')

    def migrate(self):
        """Explicit, transactional v1 -> v2 only; never called by HTTP or initialize."""
        with self.connection(True) as c:
            if self._check(c)==2:return
            c.execute("ALTER TABLE study_lines ADD COLUMN starting_note TEXT NOT NULL DEFAULT '' CHECK(length(starting_note)<=5000)")
            # v1's CHECK(version=1) cannot be updated in place. Rebuild only its singleton marker.
            c.execute('DROP TABLE study_meta');c.execute(DDL[0]);c.execute('INSERT INTO study_meta VALUES(1,2)')
            self._check(c)

    def status(self):
        try:
            with self.connection() as c:version=self._check(c)
            return {'status':'AVAILABLE','schema_version':version,'notes_enabled':version==2,'migration_required':version==1}
        except (CacheUnavailable,StudyUnavailable,sqlite3.DatabaseError,OSError):
            return {'status':'UNINITIALIZED' if not self.path.exists() else 'UNAVAILABLE','message':'Initialize an independent study store explicitly; no file was created by this read.'}

    @staticmethod
    def _get(c,table,identifier):
        r=c.execute(f'SELECT * FROM {table} WHERE id=?',(identifier,)).fetchone()
        if r is None:raise KeyError('STUDY_ITEM_NOT_FOUND')
        return dict(r)

    def get(self,kind,identifier):
        table,_,_=TABLES[kind]
        with self.connection() as c:return {k:v for k,v in self._get(c,table,identifier).items() if k!='starting_note'}

    def listing(self,kind,parent=None,limit=50,offset=0):
        if not 1<=limit<=100 or offset<0:raise ValueError('Invalid page')
        table,fk,parents=TABLES[kind]
        with self.connection() as c:
            if fk:self._get(c,parents,parent)
            where=f' WHERE {fk}=?' if fk else '';args=(parent,) if fk else ()
            total=c.execute(f'SELECT count(*) FROM {table}'+where,args).fetchone()[0]
            order='sort_order,id' if fk else 'updated_at DESC,id'
            columns=','.join(r[1] for r in c.execute(f'PRAGMA table_info({table})') if r[1]!='starting_note')
            rows=[dict(r) for r in c.execute(f'SELECT {columns} FROM {table}{where} ORDER BY {order} LIMIT ? OFFSET ?',(*args,limit,offset))]
            return {'items':rows,'total':total,'offset':offset,'limit':limit,'next_offset':offset+limit if offset+limit<total else None}

    def create(self,kind,body,parent=None):
        body=(SubStudyInput if kind=='sub' else Metadata).model_validate(body).model_dump()
        table,fk,parents=TABLES[kind];stamp=now();values={'id':uuid.uuid4().hex,**body,'created_at':stamp,'updated_at':stamp}
        with self.connection(True) as c:
            if fk:
                self._get(c,parents,parent)
                if c.execute(f'SELECT count(*) FROM {table} WHERE {fk}=?',(parent,)).fetchone()[0]>=500:raise StudyConflict('At most 500 items per parent')
                values[fk]=parent;values['sort_order']=c.execute(f'SELECT coalesce(max(sort_order),-1)+1 FROM {table} WHERE {fk}=?',(parent,)).fetchone()[0]
            if kind=='line':values['initial_fen']=chess.STARTING_FEN
            fields=','.join(values);marks=','.join('?' for _ in values)
            c.execute(f'INSERT INTO {table} ({fields}) VALUES ({marks})',tuple(values.values()))
            return {k:v for k,v in self._get(c,table,values['id']).items() if k!='starting_note'}

    def update(self,kind,identifier,body):
        values=(SubStudyInput if kind=='sub' else Metadata).model_validate(body).model_dump();table=TABLES[kind][0]
        with self.connection(True) as c:
            self._get(c,table,identifier);values['updated_at']=now()
            c.execute(f'UPDATE {table} SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),identifier))
            return {k:v for k,v in self._get(c,table,identifier).items() if k!='starting_note'}

    def delete(self,kind,identifier):
        table=TABLES[kind][0]
        with self.connection(True) as c:
            self._get(c,table,identifier);c.execute(f'DELETE FROM {table} WHERE id=?',(identifier,))
            return {'deleted':identifier}

    def _sequence(self,c,identifier):
        line=self._get(c,'study_lines',identifier)
        rows=[dict(r) for r in c.execute('SELECT * FROM line_moves WHERE line_id=? ORDER BY ply_index LIMIT 501',(identifier,))]
        if len(rows)>500:raise StudyUnavailable('STUDY_SEQUENCE_TOO_LONG')
        try:
            fen,checked=replay_sequence(line['initial_fen'],[r['uci'] for r in rows])
            if any(any(row[k]!=valid[k] for k in ('ply_index','uci','san','fen_after')) for row,valid in zip(rows,checked)):
                raise StudyUnavailable('STUDY_SEQUENCE_INCONSISTENT')
        except StudySequenceError:raise StudyUnavailable('STUDY_SEQUENCE_INCONSISTENT') from None
        return {'line_id':identifier,'initial_fen':fen,'updated_at':line['updated_at'],
                'starting_note':line.get('starting_note',''),'notes_enabled':'starting_note' in line,
                'moves':[{k:r[k] for k in ('id','ply_index','uci','san','fen_after','comment')}|{'has_comment':bool(r['comment'])} for r in rows]}

    def sequence(self,identifier):
        with self.connection() as c:return self._sequence(c,identifier)

    def save_sequence(self,identifier,body):
        body=SequenceInput.model_validate(body)
        # Validate bounded input before obtaining the write lock; nothing is written on failure.
        fen,records=replay_sequence(body.initial_fen,body.moves)
        with self.connection(True) as c:
            old=self._sequence(c,identifier)
            if old['updated_at']!=body.expected_updated_at:
                raise StudyConflict('Line changed since it was loaded. Reload the saved line before editing again.')
            prefix=0
            if fen==old['initial_fen']:
                for previous,current in zip(old['moves'],records):
                    if previous['uci']!=current['uci']:break
                    prefix+=1
            root_changed=fen!=old['initial_fen']
            if (prefix<len(old['moves']) or (root_changed and old['starting_note'])) and not body.confirm_discard:
                raise StudyConflict('Confirm replacement of the existing continuation and any associated comments')
            c.execute('DELETE FROM line_moves WHERE line_id=? AND ply_index>?',(identifier,prefix))
            for record in records[prefix:]:
                c.execute('INSERT INTO line_moves(id,line_id,ply_index,uci,san,fen_after) VALUES(?,?,?,?,?,?)',
                          (uuid.uuid4().hex,identifier,record['ply_index'],record['uci'],record['san'],record['fen_after']))
            c.execute('UPDATE study_lines SET initial_fen=?,updated_at=? WHERE id=?',(fen,now(),identifier))
            if root_changed and old['notes_enabled']:c.execute("UPDATE study_lines SET starting_note='' WHERE id=?",(identifier,))
            return self._sequence(c,identifier)

    def save_comment(self,identifier,move_id,body):
        body=CommentInput.model_validate(body)
        with self.connection(True) as c:
            line=self._get(c,'study_lines',identifier)
            if 'starting_note' not in line:raise StudyMigrationRequired('Study notes require explicit schema2 migration')
            if move_id is not None:
                row=c.execute('SELECT id FROM line_moves WHERE id=? AND line_id=?',(move_id,identifier)).fetchone()
                if row is None:raise KeyError('STUDY_MOVE_NOT_FOUND')
            if line['updated_at']!=body.expected_updated_at:
                raise StudyConflict('Line changed since it was loaded. Reload the saved line before editing again.')
            if move_id is None:c.execute('UPDATE study_lines SET starting_note=? WHERE id=?',(body.comment,identifier))
            else:c.execute('UPDATE line_moves SET comment=? WHERE id=? AND line_id=?',(body.comment,move_id,identifier))
            stamp=now();c.execute('UPDATE study_lines SET updated_at=? WHERE id=?',(stamp,identifier))
            return {'line_id':identifier,'move_id':move_id,'comment':body.comment,'updated_at':stamp}

    def reorder(self,kind,parent,ids):
        from .study_models import Reorder
        ids=Reorder(ids=ids).ids;table,fk,parents=TABLES[kind]
        if not fk:raise ValueError('Studies have no manual ordering')
        with self.connection(True) as c:
            self._get(c,parents,parent);rows=c.execute(f'SELECT id,sort_order FROM {table} WHERE {fk}=?',(parent,)).fetchall()
            if set(ids)!={r['id'] for r in rows}:raise StudyConflict('Order must include every current child exactly once; refresh the list')
            shift=max((r['sort_order'] for r in rows),default=-1)+1
            c.execute(f'UPDATE {table} SET sort_order=sort_order+? WHERE {fk}=?',(shift,parent))
            c.executemany(f'UPDATE {table} SET sort_order=?,updated_at=? WHERE id=?',[(i,now(),x) for i,x in enumerate(ids)])
            return {'ordered_ids':ids}

    def backup(self,destination):
        target=safe_path(destination)
        self._safe(target)
        if target.exists():raise StudyConflict('Backup destination already exists')
        # Exclusive creation; never overwrite an existing independent store.
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        try:
            with self.connection() as source:
                with sqlite3.connect(target) as dest:source.backup(dest)
            return {'backup_created':str(target)}
        except Exception:
            target.unlink(missing_ok=True);raise


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=['init','migrate','status','backup']);parser.add_argument('--db');parser.add_argument('--output');a=parser.parse_args();store=StudyStore(a.db)
    if a.command=='init':store.initialize();print(json.dumps(store.status()))
    elif a.command=='migrate':store.migrate();print(json.dumps(store.status()))
    elif a.command=='status':print(json.dumps(store.status()))
    else:
        if not a.output:parser.error('backup requires --output')
        print(json.dumps(store.backup(a.output)))


if __name__=='__main__':main()
