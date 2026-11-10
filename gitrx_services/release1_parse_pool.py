"""Bounded CPU-only extraction; one coordinator retains all native SQLite writes."""
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool
import hashlib,json,multiprocessing,os,pickle,resource,socket,subprocess,time,zipfile,threading,sys
from .release1_http import Stop,hash_file,guard,atomic,utc

GIB=1024**3

def settings():
    cores=os.cpu_count() or 3
    return {'workers':max(1,min(6,cores-2)), 'reserved_cores':2,'worker_RSS_limit_bytes':GIB,'memory_enforcement':'RSS watchdog250ms plus aggregate monitor; macOS RLIMIT_DATA unsupported','coordinator_allowance_bytes':2*GIB,'total_RSS_budget_bytes':8*GIB,'queue_limit':12,'queue_inflated_estimate_budget_bytes':1536*1024**2,'start_method':'spawn','max_tasks_per_child':25}

def initialize_worker():
    def memory_watchdog():
        while True:
            rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)
            if rss>GIB:os._exit(75)
            time.sleep(.25)
    threading.Thread(target=memory_watchdog,daemon=True).start()
    real=socket.socket
    def no_network(family=socket.AF_INET,*a,**kw):
        if family in (socket.AF_INET,socket.AF_INET6):raise RuntimeError('Parser worker networking forbidden')
        return real(family,*a,**kw)
    socket.socket=no_network

def plain(rows):
    for row in rows:
        for excerpt in row['excerpts']:
            n=excerpt['narrative'];excerpt['narrative']=SimpleNamespace(blocks=n.blocks,tables=n.tables,diagnostics=n.diagnostics)
    return rows

def extraction(path,version,registry,spool,report_errors=False):
    from gitrx.current.spl_parser_v1 import parse_document,CONFIG
    from gitrx.current.db import dumps
    from gitrx.current.archive import read_xml,NS
    path=Path(path);spool=Path(spool);want=version['selected_sha256']
    if hash_file(path)!=want:raise Stop('PROTECTED_PARSE_INPUT_HASH_CHANGED')
    config_hash=hashlib.sha256(dumps(CONFIG).encode()).hexdigest()
    base=spool/want[:2]/want/config_hash;base.mkdir(parents=True,exist_ok=True)
    manifest=base/'manifest.json';payload=base/'payload.pickle'
    if manifest.exists():
        md=json.loads(manifest.read_bytes())
        if md['raw_sha256']!=want or hash_file(payload)!=md['payload_sha256']:raise Stop('PROTECTED_PARSE_SPOOL_HASH_CHANGED')
        return md|{'payload_path':str(payload),'reused':True}
    with zipfile.ZipFile(path) as z:xml=z.read(version['xml_member'])
    doc=read_xml(xml);sid=doc.find('s:setId',NS);num=doc.find('s:versionNumber',NS)
    if sid is None or num is None or sid.get('root','').lower()!=version['setid'] or int(num.get('value'))!=version['version']:raise Stop('PARSER_BOUNDARY_IDENTITY_MISMATCH')
    try:rows,diagnostics=parse_document(xml,set(registry))
    except (ValueError,TypeError,KeyError) as exc:
        if not report_errors:raise
        return {'extraction_error':{'type':type(exc).__name__,'message':str(exc)}}
    data=pickle.dumps({'rows':plain(rows),'diagnostics':diagnostics},protocol=5)
    if len(data)>256*1024**2:raise MemoryError('Derived parse payload exceeds coordinator budget')
    guard(base,len(data)+64*1024**2);tmp=base/('payload-'+str(os.getpid())+'-'+str(time.time_ns())+'.partial')
    with tmp.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    sha=hash_file(tmp)
    if payload.exists():
        if hash_file(payload)!=sha:raise Stop('CONFLICTING_PARSE_SPOOL_BYTES')
    else:os.link(tmp,payload)
    md={'at':utc(),'raw_sha256':want,'parser_config_sha256':config_hash,'payload_sha256':sha,'payload_bytes':len(data),'section_count':len(rows),'worker_pid':os.getpid()}
    atomic(manifest,md);return md|{'payload_path':str(payload),'reused':False}

def persist_prepared(c,root,version,registry,prepared):
    from gitrx.current import spl_parser_v1 as engine
    from gitrx.current.parser import persist_document
    if prepared is None:return persist_document(c,root,version,registry)
    if hash_file(prepared['payload_path'])!=prepared['payload_sha256']:raise Stop('PROTECTED_PARSE_SPOOL_HASH_CHANGED')
    with Path(prepared['payload_path']).open('rb') as f:data=pickle.load(f)
    original=engine.parse_document
    def precomputed(xml,known):return data['rows'],data['diagnostics']
    try:
        engine.parse_document=precomputed
        return persist_document(c,root,version,registry)
    finally:engine.parse_document=original

class ParsePool:
    def __init__(self,runner):
        self.runner=runner;self.options=settings();self.pending={};self.fetched={};self.executor=None;self.recycles=0
        self.status_path=runner.root/'run/parse-pool.json';self.spool=runner.root/'parse-spool';self.publish()
    def start(self):
        if self.executor is None:self.executor=ProcessPoolExecutor(max_workers=self.options['workers'],mp_context=multiprocessing.get_context('spawn'),initializer=initialize_worker,max_tasks_per_child=25)
    def estimate(self,path,version):
        with zipfile.ZipFile(path) as z:size=z.getinfo(version['xml_member']).file_size
        return min(1536*1024**2,max(16*1024**2,size*8))
    def publish(self,**extra):
        atomic(self.status_path,{'at':utc(),**self.options,'pending':len(self.pending),'queue_estimated_bytes':sum(j['estimate'] for j in self.pending.values()),'worker_pids':list(self.executor._processes) if self.executor and self.executor._processes else [],'pool_recycles':self.recycles,**extra})
    def memory(self):
        pids=[os.getpid()]+(list(self.executor._processes) if self.executor and self.executor._processes else [])
        proc=subprocess.run(['ps','-o','rss=','-p',','.join(map(str,pids))],capture_output=True,text=True)
        rss=sum(int(x)*1024 for x in proc.stdout.split() if x.isdigit())
        if rss>8*GIB:self.recycle('aggregate_RSS_budget')
        return rss
    def recycle(self,reason):
        old=self.executor;self.executor=None;self.options['workers']=max(1,self.options['workers']//2);self.recycles+=1
        processes=list(old._processes.values()) if old and old._processes else []
        if old:old.shutdown(wait=False,cancel_futures=True)
        for p in processes:
            if p.is_alive():p.terminate()
        for p in processes:p.join(timeout=3)
        jobs=list(self.pending.values());self.pending={};self.start()
        for j in jobs:
            j['tries']+=1
            if j['tries']>3:
                self.runner.pool_failed(j['c'],j['version'],'WorkerProcessFailedAfterRetries');continue
            future=self.executor.submit(extraction,*j['args']);self.pending[future]=j
        self.publish(recycle_reason=reason)
    def submit(self,c,root,version,registry):
        from gitrx.current.spl_parser_v1 import PARSER_VERSION,CONFIG
        from gitrx.current.db import dumps
        version=dict(version)
        existing=c.execute('SELECT id FROM parse_runs WHERE version_id=? AND artifact_sha256=? AND parser_version=? AND config_json=?',(version['id'],version['selected_sha256'],PARSER_VERSION,dumps(CONFIG))).fetchone()
        if existing:self.runner.pool_commit(c,root,version,registry,None);return
        key=(str(root),version['id'])
        if any(j['key']==key for j in self.pending.values()):return
        path=root/c.execute('SELECT path FROM artifacts WHERE sha256=?',(version['selected_sha256'],)).fetchone()[0]
        cost=self.estimate(path,version)
        while self.pending and (len(self.pending)>=self.options['queue_limit'] or sum(j['estimate'] for j in self.pending.values())+cost>self.options['queue_inflated_estimate_budget_bytes']):self.drain(block=True)
        self.start();args=(str(path),version,sorted(registry),str(self.spool));future=self.executor.submit(extraction,*args)
        self.pending[future]={'key':key,'c':c,'root':root,'version':version,'registry':registry,'args':args,'estimate':cost,'tries':0};self.publish()
    def drain(self,block=False,all=False):
        while self.pending:
            ready=[f for f in self.pending if f.done()]
            if not ready:
                if not block and not all:break
                wait(self.pending,timeout=.5,return_when=FIRST_COMPLETED);self.memory();continue
            for f in ready:
                j=self.pending.pop(f)
                try:prepared=f.result()
                except Stop:raise
                except BrokenProcessPool:
                    self.pending[f]=j;self.recycle('worker_process_exit');break
                except MemoryError:
                    self.runner.pool_failed(j['c'],j['version'],'MemoryBudgetExceeded');continue
                except Exception as exc:
                    # The unmodified native persistence adapter reproduces diagnostics for ordinary extraction errors.
                    prepared=None
                    self.runner.update(parse_worker_exception_type=type(exc).__name__)
                self.runner.pool_commit(j['c'],j['root'],j['version'],j['registry'],prepared)
                self.finish_ready()
            self.publish(total_RSS_bytes=self.memory())
            if not all:break
        self.finish_ready()
    def mark_fetched(self,c,root,sid):self.fetched[(str(root),sid)]=(c,root);self.finish_ready()
    def finish_ready(self):
        for key,(c,root) in list(self.fetched.items()):
            sid=key[1]
            if any(j['key'][0]==str(root) and j['version']['setid']==sid for j in self.pending.values()):continue
            self.runner.finish_family(c,sid);del self.fetched[key]
    def close(self,interrupted=False):
        if self.executor:
            if not interrupted:self.drain(all=True)
            processes=list(self.executor._processes.values()) if self.executor._processes else []
            self.executor.shutdown(wait=not interrupted,cancel_futures=True)
            if interrupted:
                for p in processes:
                    if p.is_alive():p.terminate()
                for p in processes:p.join(timeout=3)
            self.executor=None
        self.publish()
