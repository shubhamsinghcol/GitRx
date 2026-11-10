"""Durable bounded download-ahead queue, separate from native parser admission.

One acquisition thread exclusively owns HTTP/ledger and operational queue writes.
Only the native consumer decides which source histories/versions are admitted.
Prefetch hints are untrusted data, never accepted inventory or parsed evidence.
"""
from concurrent.futures import Future,TimeoutError
import json
from pathlib import Path
import queue
import sqlite3
import threading
import time
from urllib.parse import urlencode
from .jobs import atomic,sha


class DownloadBuffer:
    def __init__(self,http,root,config_pin,download_guard,consumer_guard,policy):
        self.http=http;self.root=Path(root);self.config_pin=config_pin
        self.download_guard=download_guard;self.consumer_guard=consumer_guard
        self.all_batches=policy.get('download_ahead_all_batches',False)
        if type(self.all_batches) is not bool:raise ValueError('Cross-batch download-ahead must be explicitly boolean')
        self.plan_seeded=False
        self.prefetch_confirm=policy.get('history_confirmation_prefetch',False)
        if type(self.prefetch_confirm) is not bool:raise ValueError('History confirmation prefetch must be explicitly boolean')
        self.max_items=policy.get('download_buffer_items',120)
        self.max_bytes=policy.get('download_buffer_bytes',1024**3)
        self.window=policy.get('download_window_items',policy['download_lanes'])
        if type(self.window) is not int or not policy['download_lanes']<=self.window<=200:
            raise ValueError('Download scheduling window must be lanes..200')
        if self.max_items is not None and (type(self.max_items) is not int or not 10<=self.max_items<=1000):raise ValueError('Download buffer item cap must be null or 10..1000')
        if type(self.max_bytes) is not int or not 100*1024**2<=self.max_bytes<=16*1024**3:raise ValueError('Download buffer byte cap must be 100 MiB..16 GiB')
        self.commands=queue.Queue();self.fault=None;self.batch=None;self.paused=True
        self.stopping=False;self.started=threading.Event();self.last_publish=0
        self.ledger_path=Path(http.c.execute('PRAGMA database_list').fetchone()[2])
        self.thread=threading.Thread(target=self._run,name='gitrx-acquisition',daemon=True)
        self.thread.start();self.started.wait(10);self.check()
        if not self.started.is_set():raise RuntimeError('Download buffer failed to initialize')
        self.read_db=sqlite3.connect((self.root/'download-buffer.sqlite').absolute().as_uri()+'?mode=ro',uri=True);self.read_db.row_factory=sqlite3.Row
        self.read_http=sqlite3.connect(self.ledger_path.absolute().as_uri()+'?mode=ro',uri=True);self.read_http.row_factory=sqlite3.Row
        self.plan_seeded=bool(self.read_db.execute("SELECT 1 FROM metadata WHERE key='all_batches_seeded'").fetchone())

    def check(self):
        if self.fault is not None:raise RuntimeError('Download buffer stopped: '+str(self.fault)) from self.fault

    def call(self,action,*args,ready=None):
        self.check();future=Future();self.commands.put((action,args,future))
        while True:
            try:return future.result(timeout=.1)
            except TimeoutError:
                if future.done():return future.result()
                self.check();self.consumer_guard()
                if ready is not None:
                    result=ready()
                    if result is not None and future.cancel():return result

    def activate(self,batch,plan,skip):return self.call('activate',batch,plan,skip)
    def pause(self):return self.call('pause')
    def family_done(self,sid):self.check();self.commands.put(('family_done',(sid,),None))
    def acknowledge(self,url):self.check();self.commands.put(('ack',(url,),None))
    def request(self,url,reuse=True):
        self.check()
        if reuse:
            saved=self._read_ready(url)
            if saved:return saved
        return self.call('request',url,reuse,ready=(lambda:self._read_ready(url)) if reuse else None)
    def get_many(self,urls):
        self.check();urls=list(urls);saved=[self._read_ready(url) for url in urls]
        if all(saved):return saved
        def ready():
            saved=[self._read_ready(url) for url in urls]
            return saved if all(saved) else None
        return self.call('many',urls,ready=ready)

    def confirmation(self,url):
        if not self.prefetch_confirm:return None
        task=self.read_db.execute("SELECT * FROM history_confirmations WHERE url=? AND state IN ('ready','consumed')",(url,)).fetchone()
        if task is None:return None
        first=self.read_http.execute('SELECT * FROM requests WHERE id=?',(task['first_request_id'],)).fetchone()
        second=self.read_http.execute('SELECT * FROM requests WHERE id=?',(task['request_id'],)).fetchone()
        original=self.read_db.execute('SELECT request_id FROM tasks WHERE url=?',(url,)).fetchone()
        if (not first or not second or not original or original[0]!=first['id'] or first['id']==second['id']
            or second['started']<=first['started']):raise RuntimeError('Independent history observation identity changed')
        if second['status']!=200 or second['error']:return None
        path=self.http.body_path(second['sha256'])
        if sha(path)!=second['sha256']:raise RuntimeError('History confirmation body hash changed')
        return url,dict(second),path

    def _read_ready(self,url):
        return self._retained_response(self.read_db,self.read_http,url)

    def _retained_response(self,tasks,ledger,url):
        task=tasks.execute("SELECT request_id FROM tasks WHERE url=? AND state IN ('ready','consumed')",(url,)).fetchone()
        if task is None or task[0] is None:return None
        row=ledger.execute('SELECT * FROM requests WHERE id=?',(task[0],)).fetchone()
        if row is None or not row['sha256']:raise RuntimeError('Buffered HTTP observation missing')
        path=self.http.body_path(row['sha256'])
        if sha(path)!=row['sha256']:raise RuntimeError('Buffered response hash changed')
        return url,dict(row),path

    def ready_hashes(self):
        # Main-thread read connections are used only after acquisition pauses.
        hashes=set()
        for request_id, in self.read_db.execute("SELECT request_id FROM tasks WHERE state='ready' UNION SELECT request_id FROM history_confirmations WHERE state='ready'"):
            row=self.read_http.execute('SELECT sha256 FROM requests WHERE id=?',(request_id,)).fetchone()
            if row is None or not row[0]:raise RuntimeError('Ready response lacks retained HTTP evidence')
            hashes.add(row[0])
        return hashes

    def close(self):
        self.stopping=True
        self.thread.join(timeout=self.http.max_response_seconds*3+10)
        if self.thread.is_alive():raise RuntimeError('Acquisition thread did not close; review required')
        self.read_db.close();self.read_http.close()

    def _queue(self,sid,ordinal,url,kind,batch=None,latest=0):
        self.db.execute("INSERT INTO tasks(url,batch,setid,ordinal,kind,state,latest_version) VALUES(?,?,?,?,?,'queued',?) ON CONFLICT(url) DO UPDATE SET latest_version=MAX(tasks.latest_version,excluded.latest_version)",
            (url,self.batch if batch is None else batch,sid,ordinal,kind,latest))

    def _cached(self,url):
        return self._retained_response(self.db,self.http.c,url)

    def _request(self,url,reuse):
        if reuse:
            saved=self._cached(url)
            if saved:return saved
        before=self.http.c.execute('SELECT COALESCE(MAX(id),0) FROM requests').fetchone()[0]
        try:path=self.http.get(url,reuse=reuse)
        except ValueError:
            row=self.http.c.execute('SELECT * FROM requests WHERE id>? ORDER BY id DESC LIMIT 1',(before,)).fetchone()
            if row is None or row['error'] or row['status'] not in (404,410):raise
            path=self.http.body_path(row['sha256'])
        else:
            row=self.http.c.execute('SELECT * FROM requests WHERE id>? AND sha256=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1',(before,sha(path))).fetchone()
            if row is None:row=self.http.c.execute('SELECT * FROM requests WHERE url=? AND sha256=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1',(url,sha(path))).fetchone()
        if row is None:raise RuntimeError('Buffered response has no HTTP observation')
        return url,dict(row),path

    def _many(self,urls):
        output={};missing=[]
        for n,url in enumerate(urls):
            saved=self._cached(url)
            if saved:output[n]=saved
            else:missing.append((n,url))
        if missing:
            results=self.http.get_many([u for _,u in missing])
            for (n,_),(url,row,path) in zip(missing,results):output[n]=(url,dict(row),path)
        return [output[n] for n in range(len(urls))]

    def _hints(self,task,path,request_id=None):
        if task['kind']!='history' or path.stat().st_size>10*1024**2:return
        try:
            value=json.loads(path.read_bytes());data=value['data'];meta=value['metadata'];sid=task['setid']
            if data['spl']['setid'].lower()!=sid:return
            versions={int(x['spl_version']) for x in data['history']}
            versions.add(task['latest_version'])
            pages=int(meta['total_pages']);page=int(meta['current_page'])
            if not 1<=page<=pages<=100:return
        except (ValueError,KeyError,TypeError,AttributeError):return
        if self.prefetch_confirm and request_id is not None and pages==page==1:
            self.db.execute("INSERT OR IGNORE INTO history_confirmations(url,batch,setid,ordinal,first_request_id,state) VALUES(?,?,?,?,?,'queued')",
                (task['url'],task['batch'],sid,task['ordinal'],request_id))
        # Hints only schedule raw retrieval. The unchanged native collector
        # still requires complete agreeing inventories before admission.
        count=self.db.execute('SELECT COUNT(*) FROM tasks WHERE batch=?',(task['batch'],)).fetchone()[0]
        for v in sorted(versions):
            if v<1 or (sid,v) in self.skip or count>=10000:continue
            self._queue(sid,task['ordinal'],'https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?'+urlencode({'type':'zip','setid':sid,'version':v}),'zip',batch=task['batch']);count+=1
        for p in range(page+1,pages+1):
            if count>=10000:break
            self._queue(sid,task['ordinal'],'https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/'+sid+'/history.json?'+urlencode({'pagesize':100,'page':p}),'history',batch=task['batch'],latest=task['latest_version']);count+=1

    def _publish(self):
        if time.monotonic()-self.last_publish<1:return
        self.last_publish=time.monotonic()
        where,args=('',()) if self.all_batches else ('WHERE batch=?',(self.batch,))
        counts=dict(self.db.execute('SELECT state,COUNT(*) FROM tasks '+where+' GROUP BY state',args))
        size=self.db.execute("SELECT COALESCE(SUM(bytes),0) FROM tasks WHERE state='ready'"+('' if self.all_batches else ' AND batch=?'),args).fetchone()[0]
        confirmations=dict(self.db.execute('SELECT state,COUNT(*) FROM history_confirmations '+where+' GROUP BY state',args))
        for key,value in confirmations.items():counts[key]=counts.get(key,0)+value
        _,extra=self._confirmation_usage();size+=extra
        atomic(self.root/'download-buffer-status.json',{'format':'gitrx-download-buffer-1','batch':self.batch,
            'ready_files':counts.get('ready',0),'ready_bytes':size,'queued_files':counts.get('queued',0),
            'max_ready_files':self.max_items,'max_ready_bytes':self.max_bytes,
            'lookahead_all_batches':self.all_batches,'plan_seeded':self.plan_seeded,
            'history_confirmation_prefetch':self.prefetch_confirm,'ready_history_confirmations':confirmations.get('ready',0),
            'transport_profile':dict(self.http.download_pool.metrics) if getattr(self.http,'download_pool',None) else {},
            'paused':self.paused,'download_lanes':self.http.pacing.state.get('active_lanes',self.http.pacing.policy['download_lanes']),'updated_epoch':time.time()})

    def _command(self,action,args):
        if action=='activate':
            batch,plan,skip=args
            if self.batch==batch and not self.paused:return
            self.batch=batch;self.skip=set(skip)
            for item in plan:
                sid,ordinal,latest=item[:3];target_batch=item[3] if len(item)>3 else batch
                self._queue(sid,ordinal,'https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/'+sid+'/history.json?'+urlencode({'pagesize':100,'page':1}),'history',batch=target_batch,latest=latest)
            if self.all_batches:
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES('all_batches_seeded','1')")
            self.db.commit();self.plan_seeded=self.all_batches or self.plan_seeded
            self._restore_ready_bodies()
            if self.prefetch_confirm:
                for task in self.db.execute("SELECT * FROM tasks WHERE kind='history' AND state='ready' AND url NOT IN (SELECT url FROM history_confirmations)").fetchall():
                    cached=self._cached(task['url'])
                    if cached and cached[1]['status']==200:self._hints(task,cached[2],cached[1]['id'])
                self.db.commit()
            self.paused=False
        elif action=='pause':self.paused=True
        elif action=='family_done':
            self.db.execute("UPDATE tasks SET state='consumed' WHERE batch=? AND setid=?",(self.batch,args[0]))
            self.db.execute("UPDATE history_confirmations SET state='consumed' WHERE batch=? AND setid=?",(self.batch,args[0]));self.db.commit()
        elif action=='ack':
            self.db.execute("UPDATE tasks SET state='consumed' WHERE url=?",(args[0],));self.db.commit()
        elif action=='request':
            result=self._request(*args)
            if not args[1] and self.prefetch_confirm:self._save_confirmation(args[0],result)
            return result
        elif action=='many':return self._many(*args)
        elif action=='stop':self.stopping=True
        else:raise RuntimeError('Unknown download buffer command')

    def _restore_ready_bodies(self):
        # Older cross-batch profiles could archive future responses before
        # consuming them. Rehydrate only this bounded ready cache for offline use.
        from .backfill_staging import copy_verified
        ids=self.db.execute("SELECT request_id FROM tasks WHERE state='ready' UNION SELECT request_id FROM history_confirmations WHERE state='ready'").fetchall()
        for request_id, in ids:
            row=self.http.c.execute('SELECT sha256 FROM requests WHERE id=?',(request_id,)).fetchone()
            if row is None or not row[0]:raise RuntimeError('Ready cache HTTP evidence is missing')
            target=self.http.root/'bodies'/row[0]
            if not target.exists():
                self.download_guard();copy_verified(self.http.body_path(row[0]),target,row[0],link_immutable=True)

    def _save_confirmation(self,url,result):
        _,row,path=result
        state='ready' if row['status']==200 and not row['error'] else 'deferred'
        self.db.execute("UPDATE history_confirmations SET request_id=?,bytes=?,state=? WHERE url=? AND state='queued' AND first_request_id!=?",
                        (row['id'],path.stat().st_size,state,url,row['id']))
        self.db.commit()

    def _confirmation_usage(self):
        where,args=('',()) if self.all_batches else (' AND batch=?',(self.batch,))
        return self.db.execute("SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM history_confirmations WHERE state='ready'"+where,args).fetchone()

    def _urgent_waiting(self):
        # Bookkeeping acknowledgments must not abort acquisition waves.
        with self.commands.mutex:
            return any(future is not None and not future.cancelled()
                       for action,args,future in self.commands.queue)

    def _ready(self,task,row,path):
        # Publish each retained response as soon as its ledger commit is durable;
        # the native consumer need not wait for an unrelated slow response.
        self.db.execute("UPDATE tasks SET state='ready',request_id=?,bytes=?,error=? WHERE url=?",
                        (row['id'],path.stat().st_size,row['error'],task['url']))
        if row['status']==200 and not row['error']:self._hints(task,path,row['id'])
        self.db.commit();self._publish()

    def _run(self):
        self.db=None
        try:
            self.root.mkdir(parents=True,exist_ok=True)
            self.db=sqlite3.connect(self.root/'download-buffer.sqlite');self.db.row_factory=sqlite3.Row
            self.db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA fullfsync=ON;
                CREATE TABLE IF NOT EXISTS metadata(key PRIMARY KEY,value);
                CREATE TABLE IF NOT EXISTS tasks(url TEXT PRIMARY KEY,batch INTEGER,setid TEXT,ordinal INTEGER,kind TEXT,
                    state TEXT,request_id INTEGER,bytes INTEGER DEFAULT 0,error TEXT);
                CREATE INDEX IF NOT EXISTS tasks_batch_state ON tasks(batch,state,ordinal);
                CREATE INDEX IF NOT EXISTS tasks_state_ordinal ON tasks(state,ordinal);
                CREATE TABLE IF NOT EXISTS history_confirmations(url TEXT PRIMARY KEY,batch INTEGER,setid TEXT,ordinal INTEGER,
                    first_request_id INTEGER,request_id INTEGER,state TEXT,bytes INTEGER DEFAULT 0);
                CREATE INDEX IF NOT EXISTS confirmations_state ON history_confirmations(state,ordinal);''')
            if 'latest_version' not in {r[1] for r in self.db.execute('PRAGMA table_info(tasks)')}:
                self.db.execute('ALTER TABLE tasks ADD COLUMN latest_version INTEGER NOT NULL DEFAULT 0')
                self.db.commit()
            if self.db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Download buffer database integrity failure')
            pin=self.db.execute("SELECT value FROM metadata WHERE key='config_pin'").fetchone()
            if pin and pin[0]!=self.config_pin:raise RuntimeError('Download buffer configuration pin changed')
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES('config_pin',?)",(self.config_pin,));self.db.commit()
            self.http.guard=self.download_guard
            self.started.set()
            while not self.stopping:
                if not self.paused:self.download_guard()
                try:command=self.commands.get(timeout=.01)
                except queue.Empty:command=None
                if command:
                    action,args,future=command
                    if future is not None and not future.set_running_or_notify_cancel():continue
                    try:
                        result=self._command(action,args)
                        if future is not None:future.set_result(result)
                    except BaseException as exc:
                        if future is not None:future.set_exception(exc)
                        # Ordinary source-fetch errors are delivered to native
                        # handling; unknown/integrity failures stop acquisition.
                        if not isinstance(exc,ValueError):raise
                    self._publish();continue
                self._publish()
                if self.paused:continue
                args=() if self.all_batches else (self.batch,)
                count,size=self.db.execute("SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM tasks WHERE state='ready'"+('' if self.all_batches else ' AND batch=?'),args).fetchone()
                extra_count,extra_bytes=self._confirmation_usage();count+=extra_count;size+=extra_bytes
                # Reserve a full response per lane for bounded in-flight work.
                room=min(self.window,
                    self.max_items-count if self.max_items is not None else self.window,
                    (self.max_bytes-size)//(100*1024**2))
                if room<=0:continue
                if self.prefetch_confirm:
                    confirmations=self.db.execute("SELECT * FROM history_confirmations WHERE state='queued'"+('' if self.all_batches else ' AND batch=?')+' ORDER BY ordinal LIMIT ?',(*args,min(room,self.http.pacing.policy['download_lanes']))).fetchall()
                    if confirmations:
                        self.http.get_many([t['url'] for t in confirmations],reuse=False,
                            interrupt=lambda:self._urgent_waiting() or self.stopping,
                            on_result=lambda url,row,path:self._save_confirmation(url,(url,row,path)))
                        continue
                tasks=self.db.execute("SELECT * FROM tasks WHERE state='queued'"+('' if self.all_batches else ' AND batch=?')+' ORDER BY ordinal,kind,url LIMIT ?',(*args,room)).fetchall()
                if not tasks:continue
                by_url={t['url']:t for t in tasks}
                try:self.http.get_many([t['url'] for t in tasks],
                    interrupt=lambda:self._urgent_waiting() or self.stopping,
                    on_result=lambda url,row,path:self._ready(by_url[url],row,path))
                except ValueError as exc:
                    for t in tasks:self.db.execute("UPDATE tasks SET state='deferred',error=? WHERE url=?",(str(exc),t['url']))
                    self.db.commit();continue
                self.db.commit()
        except BaseException as exc:
            self.fault=exc;self.started.set()
        finally:
            self.http.close()
            if self.db is not None:self.db.close()
