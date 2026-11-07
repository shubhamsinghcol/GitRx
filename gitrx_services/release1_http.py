"""One sequential resumable HTTP ledger; immutable retained bodies, hard caps."""
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl,hashlib,json,os,shutil,sqlite3,time,http.client
from urllib import request,error,parse

PROJECT=Path(__file__).resolve().parents[1]
FLOOR=20*1024**3

class Stop(RuntimeError): pass

def utc():return datetime.now(timezone.utc).isoformat()
def digest(body):return hashlib.sha256(body).hexdigest()
def hash_file(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
def guard(path,additional=0):
    p=Path(path)
    while not p.exists():p=p.parent
    if shutil.disk_usage(p).free-additional<FLOOR:raise Stop('FREE_SPACE_FLOOR')
def atomic(path,value):
    path=Path(path);body=(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode();guard(path,len(body)+1024**2)
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('wb') as f:f.write(body);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
def canonical(url):
    u=parse.urlsplit(url);return parse.urlunsplit((u.scheme,u.netloc,u.path,parse.urlencode(sorted(parse.parse_qsl(u.query,keep_blank_values=True))),''))

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k):return None

class Transport:
    def __init__(self,root,config):
        self.root=Path(root);self.config=config;guard(self.root,128*1024**2)
        if not (self.root/'network/ledger.sqlite').exists() and ((self.root/'network/responses').exists() and any((self.root/'network/responses').iterdir())):raise Stop('REQUEST_LEDGER_MISSING')
        for name in ['network/objects','network/responses','network/partial']: (self.root/name).mkdir(parents=True,exist_ok=True)
        self.c=sqlite3.connect(self.root/'network/ledger.sqlite',timeout=60);self.c.row_factory=sqlite3.Row
        self.c.executescript('''PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS http(id INTEGER PRIMARY KEY,url TEXT,origin_url TEXT,started_at TEXT,start_epoch REAL,status INTEGER,sha256 TEXT,bytes INTEGER DEFAULT 0,error TEXT,headers_json TEXT,final_url TEXT,archive_request INTEGER DEFAULT 0,complete INTEGER DEFAULT 0);CREATE TABLE IF NOT EXISTS cache(url TEXT PRIMARY KEY,observation_id INTEGER);CREATE TABLE IF NOT EXISTS imports(url TEXT PRIMARY KEY,path TEXT,sha256 TEXT,status INTEGER,metadata_json TEXT);''');self.c.commit()
        largest=self.c.execute('SELECT COALESCE(MAX(id),0) FROM http').fetchone()[0]
        if any(int(p.stem)>largest for p in (self.root/'network/responses').glob('*.json') if p.stem.isdigit()):raise Stop('REQUEST_LEDGER_CONFLICT')
        from .release1_connection import SingleConnection
        from .release1_pacing import Pacer
        self.opener=SingleConnection();self.on_idle=None
        baseline=PROJECT/'reports/block3c-4/BASELINE.json'
        self.pacer=Pacer(self.root,json.loads(baseline.read_bytes())['median_seconds'] if baseline.exists() else None)
        columns={r[1] for r in self.c.execute('PRAGMA table_info(http)')}
        for name,kind in [('spacing_seconds','REAL'),('latency_seconds','REAL'),('completed_epoch','REAL'),('connection_serial','INTEGER'),('pacing_signals_json','TEXT')]:
            if name not in columns:self.c.execute('ALTER TABLE http ADD COLUMN '+name+' '+kind)
        self.c.commit()
    def recover_interrupted(self):
        for row in self.c.execute('SELECT * FROM http WHERE complete=0').fetchall():
            p=self.root/'network/partial'/str(row['id']);sha=hash_file(p) if p.exists() else digest(b'');size=p.stat().st_size if p.exists() else 0
            if p.exists() and not self._path(sha).exists():os.link(p,self._path(sha))
            with self.c:self.c.execute("UPDATE http SET status=COALESCE(status,0),sha256=?,bytes=?,error='interrupted response retained; not reused',complete=1 WHERE id=?",(sha,size,row['id']))
        # Recover a kill after durable HTTP completion but before cache-index commit.
        for row in self.c.execute("SELECT * FROM http WHERE complete=1 AND error IS NULL AND status IN (200,404,410) ORDER BY id").fetchall():
            if not row['sha256'] or not self._path(row['sha256']).exists():raise Stop('PROTECTED_RESPONSE_MISSING')
            existing=self.c.execute('SELECT observation_id FROM cache WHERE url=?',(canonical(row['origin_url']),)).fetchone()
            if not existing or existing[0]<row['id']:
                if hash_file(self._path(row['sha256']))!=row['sha256']:raise Stop('PROTECTED_RESPONSE_HASH_CHANGED')
                with self.c:self.c.execute('INSERT OR REPLACE INTO cache VALUES(?,?)',(canonical(row['origin_url']),row['id']))
    def count(self):return self.config.get('prior_requests',0)+self.c.execute('SELECT COUNT(*) FROM http').fetchone()[0]
    def _path(self,h):return self.root/'network/objects'/h
    def cached(self,url):
        key=canonical(url);row=self.c.execute('SELECT h.* FROM cache c JOIN http h ON h.id=c.observation_id WHERE c.url=?',(key,)).fetchone()
        if row:
            path=self._path(row['sha256'])
            if hash_file(path)!=row['sha256']:raise Stop('PROTECTED_CACHED_HASH_CHANGED')
            return dict(row)|{'url':row['origin_url'],'headers':json.loads(row['headers_json'] or '[]'),'path':str(path),'cached':True,'provenance':'release1-http'}
        row=self.c.execute('SELECT * FROM imports WHERE url=?',(key,)).fetchone()
        if row:
            if hash_file(row['path'])!=row['sha256']:raise Stop('PROTECTED_IMPORTED_HASH_CHANGED')
            return json.loads(row['metadata_json'])|{'url':url,'path':row['path'],'sha256':row['sha256'],'status':row['status'],'cached':True,'provenance':'prior-verified-observation'}
    def import_observation(self,url,path,sha,status,meta):
        if hash_file(path)!=sha:raise Stop('PROTECTED_IMPORTED_HASH_CHANGED')
        old=self.c.execute('SELECT sha256 FROM imports WHERE url=?',(canonical(url),)).fetchone()
        if old and old[0]!=sha:raise Stop('CONFLICTING_CACHE_BYTES')
        with self.c:self.c.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?,?,?)',(canonical(url),str(path),sha,status,json.dumps(meta)))
    def get(self,origin,cache=True):
        if cache:
            old=self.cached(origin)
            if old:return old
        last=None
        for attempt in range(3):
            url=origin
            for hop in range(8):
                u=parse.urlsplit(url)
                if u.scheme!='https' or not u.hostname or not (u.hostname=='nlm.nih.gov' or u.hostname.endswith('.nlm.nih.gov')):raise ValueError('Only official NLM HTTPS allowed')
                with (PROJECT/'reports/dailymed-global-rate.lock').open('a+') as lock:
                    fcntl.flock(lock,fcntl.LOCK_EX);rp=PROJECT/'reports/dailymed-global-rate.json'
                    rate=json.loads(rp.read_bytes()) if rp.exists() else {'last_start':0,'wait_until':0}
                    while True:
                        now=time.time();delay=self.pacer.allowed_at(rate,now)-now
                        if delay<=0:break
                        if self.on_idle:self.on_idle()
                        time.sleep(min(.1,delay))
                    guard(self.root,256*1024**2);guard(PROJECT/'reports',16*1024**2)
                    if (self.root/'fatal-stop.json').exists():raise Stop(json.loads((self.root/'fatal-stop.json').read_bytes())['reason'])
                    if self.count()>=self.config['request_cap']:raise Stop('REQUEST_CAP')
                    epoch=time.time();spacing=self.pacer.spacing(epoch);rate['last_start']=epoch;rate['recent_starts']=([t for t in rate.get('recent_starts',[]) if t>epoch-10]+[epoch])[-50:];atomic(rp,rate);at=utc()
                    archive=int('getArchivalFile.cfm' in url or '/archives/' in url)
                    with self.c:ident=self.c.execute('INSERT INTO http(url,origin_url,started_at,start_epoch,archive_request) VALUES(?,?,?,?,?)',(url,origin,at,epoch,archive)).lastrowid
                    partial=self.root/'network/partial'/str(ident);status=0;headers={};failure=None;size=0;h=hashlib.sha256();network_start=time.monotonic()
                    try:
                        req=request.Request(url,headers={'User-Agent':self.config['user_agent'].replace('sequential >=2 seconds','adaptive sequential single connection, <=5 starts/second'),'Accept-Encoding':'identity'})
                        try:response=self.opener.open(req,timeout=60)
                        except error.HTTPError as exc:response=exc
                        with response,partial.open('xb') as f:
                            status=response.code;headers=dict(response.headers.items())
                            while True:
                                guard(self.root,64*1024**2)
                                chunk=response.read(1024*1024)
                                if not chunk:break
                                f.write(chunk);h.update(chunk);size+=len(chunk)
                            f.flush();os.fsync(f.fileno())
                    except Stop:raise
                    except (OSError,TimeoutError,http.client.HTTPException) as exc:failure=type(exc).__name__;self.opener.close()
                    latency=time.monotonic()-network_start;completed=time.time()
                    if not partial.exists():partial.write_bytes(b'')
                    # Retain incomplete transport bodies too; never silently resume a partial as a successful response.
                    sha=hash_file(partial);dest=self._path(sha)
                    if dest.exists() and hash_file(dest)!=sha:raise Stop('CONFLICTING_CAS_BYTES')
                    if not dest.exists():os.link(partial,dest)
                    size=partial.stat().st_size
                    rec={'id':ident,'url':origin,'final_url':url,'started_at':at,'retrieved_at':at,'start_epoch':epoch,'status':status,'sha256':sha,'bytes':size,'error':failure,'headers':list(headers.items()),'attempt':attempt+1,'path':str(dest),'cached':False,'provenance':'release1-http','spacing_seconds':spacing,'latency_seconds':latency,'completed_epoch':completed,'connection_serial':self.opener.serial}
                    atomic(self.root/'network/responses'/f'{ident}.json',rec)
                    with self.c:self.c.execute('UPDATE http SET status=?,sha256=?,bytes=?,error=?,headers_json=?,final_url=?,complete=1 WHERE id=?',(status,sha,size,failure,json.dumps(list(headers.items())),url,ident))
                    signals=self.pacer.note(status,latency,failure,headers,completed)
                    with self.c:self.c.execute('UPDATE http SET spacing_seconds=?,latency_seconds=?,completed_epoch=?,connection_serial=?,pacing_signals_json=? WHERE id=?',(spacing,latency,completed,self.opener.serial,json.dumps(signals),ident))
                    retry=next((v for k,v in headers.items() if k.lower()=='retry-after'),None)
                    if retry:
                        try:wait=float(retry)
                        except ValueError:
                            try:wait=max(0,parsedate_to_datetime(retry).timestamp()-time.time())
                            except (ValueError,TypeError):wait=60
                        rate['wait_until']=time.time()+wait;atomic(rp,rate)
                last=rec
                location=next((v for k,v in headers.items() if k.lower()=='location'),None)
                if status in (301,302,303,307,308) and location:url=parse.urljoin(url,location);continue
                break
            if last['error'] or last['status'] in (408,429,500,502,503,504):
                if attempt<2:time.sleep(2**(attempt+1));continue
            if cache and not last['error'] and last['status'] in (200,404,410):
                with self.c:self.c.execute('INSERT OR REPLACE INTO cache VALUES(?,?)',(canonical(origin),last['id']))
            return last
        return last
