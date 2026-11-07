"""User-space bounded restart loop. Never retries hard stops or changes launchd."""
from pathlib import Path
import argparse,fcntl,json,os,signal,sqlite3,subprocess,sys,threading,time
from .release1_http import PROJECT,Stop,atomic,guard,utc

DEFAULT_CONFIG=PROJECT/'reports/block3c/config.json'

def process_info(pid,fragment):
    from .release1_job import process_info as info
    return info(pid,fragment)

def notify(root,message):
    script='on run argv\ndisplay notification (item 1 of argv) with title "GitRx release1"\nend run'
    try:
        p=subprocess.run(['/usr/bin/osascript','-e',script,'--',message],capture_output=True,text=True,timeout=10)
        event={'at':utc(),'message':message,'notification_request_succeeded':p.returncode==0,'error_type':None if p.returncode==0 else 'osascript_exit_'+str(p.returncode)}
    except (OSError,subprocess.TimeoutExpired) as exc:event={'at':utc(),'message':message,'notification_request_succeeded':False,'error_type':type(exc).__name__}
    with (Path(root)/'run/notifications.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
    atomic(Path(root)/'run/latest-notification.json',event)
    (PROJECT/'reports/block3c/ALERT.md').write_text('# Latest release1 notification request\n\n'+event['at']+'\n\n'+message+'\n\nmacOS accepted request: '+str(event['notification_request_succeeded'])+'. Banner visibility is not verified.\n')

def preflight(config):
    from .release1_job import protected_check
    root=Path(config['data_root']);guard(root,256*1024**2);guard(PROJECT/'reports',16*1024**2)
    if (root/'fatal-stop.json').exists():raise Stop(json.loads((root/'fatal-stop.json').read_bytes())['reason'])
    db=root/'network/ledger.sqlite'
    if db.exists():
        c=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True);n=c.execute('SELECT COUNT(*) FROM http').fetchone()[0]+config.get('prior_requests',0);c.close()
        if n>=config['request_cap']:raise Stop('REQUEST_CAP')
    protected_check()

def caffeinate_pid(supervisor_pid):
    rows=subprocess.check_output(['ps','-axo','pid=,ppid=,command='],text=True)
    for line in rows.splitlines():
        p=line.strip().split(None,2)
        if len(p)==3 and p[1]==str(supervisor_pid) and '/usr/bin/caffeinate ' in p[2]:return int(p[0])
    return None

class Supervisor:
    def __init__(self,config,worker_command=None,restart_cap=8,initial_backoff=5,max_backoff=300,check=preflight,notification=notify):
        self.config=config;self.root=Path(config['data_root']);self.root.mkdir(parents=True,exist_ok=True);(self.root/'run').mkdir(exist_ok=True)
        self.worker_command=worker_command or [sys.executable,'-B','-m','gitrx_services.release1_job','--config',config.get('_config_path',str(DEFAULT_CONFIG)),'run']
        self.restart_cap=restart_cap;self.initial_backoff=initial_backoff;self.max_backoff=max_backoff;self.check=check;self.notification=notification;self.stop_requested=threading.Event();self.child=None
    def disabled(self):
        p=self.root/'run/supervisor-disabled.json'
        return self.stop_requested.is_set() or (p.exists() and json.loads(p.read_bytes()).get('disabled',False))
    def state(self,phase,**kw):
        atomic(self.root/'run/supervisor.json',{'at':utc(),'phase':phase,'supervisor_pid':os.getpid(),'worker_pid':self.child.pid if self.child else None,'restart_cap':self.restart_cap,**kw})
    def run(self):
        lock=(self.root/'run/supervisor.lock').open('a+')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Supervisor already running')
        (self.root/'run/supervisor.pid').write_text(str(os.getpid())+'\n')
        cap=caffeinate_pid(os.getpid())
        if cap:(self.root/'run/caffeinate.pid').write_text(str(cap)+'\n')
        restarts=0
        try:
            while True:
                if self.disabled():self.state('disabled',restarts=restarts);self.notification(self.root,'Supervisor disabled; worker stopped.');return 'disabled'
                try:self.check(self.config)
                except Stop as exc:
                    self.state('hard_stop',reason=str(exc),restarts=restarts);self.notification(self.root,'Hard stop; no automatic restart: '+str(exc));return 'hard_stop'
                log=(self.root/'run/release1.log').open('ab')
                self.child=subprocess.Popen(self.worker_command,cwd=PROJECT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT);log.close()
                (self.root/'run/worker.pid').write_text(str(self.child.pid)+'\n');self.state('running',restarts=restarts)
                while self.child.poll() is None:
                    if self.disabled():
                        self.child.terminate()
                        try:self.child.wait(timeout=30)
                        except subprocess.TimeoutExpired:self.child.kill();self.child.wait()
                        break
                    self.stop_requested.wait(.5)
                rc=self.child.returncode;outcome_path=self.root/'run'/f'worker-exit-{self.child.pid}.json'
                outcome=json.loads(outcome_path.read_bytes()) if outcome_path.exists() else {}
                self.notification(self.root,f'Worker stopped (exit {rc}): '+str(outcome.get('reason') or outcome.get('phase') or 'unexpected process exit'))
                if self.disabled():self.state('disabled',restarts=restarts);return 'disabled'
                if rc==78 or outcome.get('hard_stop'):
                    self.state('hard_stop',reason=outcome.get('reason','worker hard-stop exit78'),restarts=restarts);return 'hard_stop'
                if rc==0:
                    self.state('finished',worker_phase=outcome.get('phase','complete'),restarts=restarts);self.notification(self.root,'Release1 finished; check status, failure counts and export reports.');return 'finished'
                if restarts>=self.restart_cap:
                    self.state('restart_cap_exhausted',restarts=restarts);self.notification(self.root,'Restart cap reached; automatic restarts disabled. Check the log.');return 'restart_cap_exhausted'
                delay=min(self.max_backoff,self.initial_backoff*2**restarts);restarts+=1
                self.state('backoff',restarts=restarts,delay_seconds=delay)
                end=time.monotonic()+delay
                while time.monotonic()<end and not self.disabled():self.stop_requested.wait(min(.5,max(0,end-time.monotonic())))
        finally:
            if self.child is not None and self.child.poll() is None:self.child.terminate();self.child.wait()
            fcntl.flock(lock,fcntl.LOCK_UN);lock.close()

def launch(config):
    root=Path(config['data_root']);guard(root,1024**3);(root/'run').mkdir(parents=True,exist_ok=True)
    handles=[]
    for name in ['supervisor','worker']:
        f=(root/'run'/f'{name}.lock').open('a+')
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('Already running; use ./release1-status');return
        handles.append(f)
    for f in handles:fcntl.flock(f,fcntl.LOCK_UN);f.close()
    atomic(root/'run/supervisor-disabled.json',{'disabled':False,'explicit_resume_at':utc()})
    log=(root/'run/supervisor.log').open('ab')
    cmd=['/usr/bin/nohup','/usr/bin/caffeinate','-i',sys.executable,'-B','-m','gitrx_services.release1_supervisor','--config',config.get('_config_path',str(DEFAULT_CONFIG)),'run']
    p=subprocess.Popen(cmd,cwd=PROJECT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True);log.close()
    (root/'run/launcher.pid').write_text(str(p.pid)+'\n');(root/'run/supervisor.pid').write_text(str(p.pid)+'\n')
    atomic(PROJECT/'reports/block3c/SUPERVISOR-LAUNCH.json',{'at':utc(),'supervisor_pid':p.pid,'command':cmd,'log':str(root/'run/supervisor.log'),'worker_log':str(root/'run/release1.log'),'system_configuration_changed':False})
    print(json.dumps({'supervisor_pid':p.pid,'status':'./release1-status','stop_disable':'./release1-stop','resume':'./release1-resume'}))

def stop(config):
    root=Path(config['data_root']);(root/'run').mkdir(parents=True,exist_ok=True)
    atomic(root/'run/supervisor-disabled.json',{'disabled':True,'at':utc(),'reason':'explicit user stop command'})
    for name,module in [('supervisor','gitrx_services.release1_supervisor'),('worker','gitrx_services.release1_job')]:
        p=root/'run'/f'{name}.pid'
        if p.exists():
            pid=int(p.read_text())
            if process_info(pid,module):os.kill(pid,signal.SIGTERM)
    print('Supervisor disabled; worker stop requested. Re-enable explicitly with ./release1-resume.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=DEFAULT_CONFIG);p.add_argument('command',choices=['run','resume','stop']);a=p.parse_args()
    config=json.loads(a.config.read_bytes());config['_config_path']=str(a.config.resolve())
    if a.command=='resume':launch(config)
    elif a.command=='stop':stop(config)
    else:
        s=Supervisor(config)
        signal.signal(signal.SIGTERM,lambda *_:s.stop_requested.set());signal.signal(signal.SIGINT,lambda *_:s.stop_requested.set())
        s.run()

if __name__=='__main__':main()
