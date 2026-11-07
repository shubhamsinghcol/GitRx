"""Local operational observations, separate from immutable evidence releases."""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps
from contextlib import contextmanager

FORMAT = 'gitrx-operational-status-1'

def now(): return datetime.now(timezone.utc).isoformat()

@contextmanager
def journal(root):
    Path(root).mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(Path(root)/'operations.sqlite',timeout=10)
    try:
        c.executescript('CREATE TABLE IF NOT EXISTS operations(id INTEGER PRIMARY KEY,kind,started,finished,state,details);'
                       'CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY,operation_id,setid,attempted,verified,state,details);'
                       'CREATE INDEX IF NOT EXISTS checks_family ON checks(setid,id);')
        with c:yield c
    finally:c.close()

def start(root,kind):
    with journal(root) as c:
        return c.execute('INSERT INTO operations(kind,started,state,details) VALUES(?,?,?,?)',(kind,now(),'started','{}')).lastrowid

def progress(root,ident,phase,details):
    if ident is None:return
    with journal(root) as c:
        c.execute('UPDATE operations SET state=?,details=? WHERE id=?',('building',json.dumps(dict(details,last_recorded_build_phase=phase),ensure_ascii=True),ident))

def finish(root,ident,state,details):
    with journal(root) as c:
        row=c.execute('SELECT details FROM operations WHERE id=?',(ident,)).fetchone()
        prior=json.loads(row[0]) if row else {}
        details=dict(prior,**details)
        c.execute('UPDATE operations SET finished=?,state=?,details=? WHERE id=?',(now(),state,json.dumps(details,ensure_ascii=True),ident))

def check(root,operation_id,sid,state,details,verified=False):
    with journal(root) as c:
        c.execute('INSERT INTO checks(operation_id,setid,attempted,verified,state,details) VALUES(?,?,?,?,?,?)',
                  (operation_id,sid,now(),now() if verified else None,state,json.dumps(details,ensure_ascii=True)))

def tracked(kind):
    def wrap(fn):
        @wraps(fn)
        def run(self,*args,**kwargs):
            ident=start(self.root,kind)
            previous=getattr(self,'_operation_id',None);self._operation_id=ident
            try:
                result=fn(self,*args,**kwargs)
            except BaseException as exc:
                finish(self.root,ident,'failed',{'error_type':type(exc).__name__,'message':str(exc)[:2000]})
                raise
            else:
                state=result.get('state') or ('incident' if result.get('incidents') else 'completed')
                finish(self.root,ident,state,result)
                return result
            finally:self._operation_id=previous
        return run
    return wrap

def status(root,config_path):
    root=Path(root);path=Path(config_path);cfg=json.loads(path.read_bytes())
    result={'format':FORMAT,'operation_mode':'manual','current_config':str(path),'workspace':str(root),
            'selected_release':{'snapshot':cfg['snapshot'],'meta_commit':cfg.get('meta_commit')},
            'serving_profile':{'backend':cfg.get('backend','sqlite'),'search_accelerated':'search_index' in cfg,
                               'evidence_metadata':'evidence_metadata' in cfg,
                               'basis':'Job selected configuration; independently running clients or explicit configurations may differ'},
            'freshness_meaning':'Only individually recorded agreeing inventory checks are verified. No corpus-wide freshness or installed scheduler is implied.',
            'recent_operations':[],'family_checks':[],'family_checks_limit':200,'family_checks_total':0}
    db=root/'operations.sqlite'
    if not db.exists():
        result['observation_status']='no recorded operations';return result
    c=sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA query_only=ON')
        for r in c.execute('SELECT * FROM operations ORDER BY id DESC LIMIT 20'):
            result['recent_operations'].append(dict(r,details=json.loads(r['details']),
                completion_status='unfinished-outcome-unknown' if r['finished'] is None else 'recorded-result'))
        result['family_checks_total']=c.execute('SELECT COUNT(DISTINCT setid) FROM checks').fetchone()[0]
        for r in c.execute('SELECT * FROM checks WHERE id IN (SELECT MAX(id) FROM checks GROUP BY setid) ORDER BY id DESC LIMIT 200'):
            row=dict(r,details=json.loads(r['details']))
            row['last_successful_agreeing_inventory']=c.execute('SELECT MAX(verified) FROM checks WHERE setid=?',(r['setid'],)).fetchone()[0]
            result['family_checks'].append(row)
        result['family_checks_truncated']=result['family_checks_total']>len(result['family_checks'])
        result['observation_status']='recorded operations; timestamps are source-check observations, not content release dates'
    finally:c.close()
    return result
