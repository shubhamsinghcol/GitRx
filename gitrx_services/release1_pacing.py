"""Persisted adaptive pacing; a global start-to-start/window limit, no concurrency."""
from pathlib import Path
import json,statistics,time
from email.utils import parsedate_to_datetime
from .release1_http import atomic,utc

LADDER=(2.,1.,.5,.25,.2)

def retry_after(value,now):
    if value is None:return 0.
    try:return now+max(0.,float(value))
    except (ValueError,TypeError):
        try:return max(now,parsedate_to_datetime(value).timestamp())
        except (ValueError,TypeError,OverflowError):return now+60

class Pacer:
    def __init__(self,root,baseline=None):
        self.root=Path(root);self.path=self.root/'network/pacing.json'
        if self.path.exists():self.s=json.loads(self.path.read_bytes())
        else:
            self.s={'version':1,'started_at':utc(),'started_epoch':time.time(),'index':1,'spacing_seconds':1.,'baseline_seconds':baseline or .5,'baseline_method':'historical retained response timestamp proxy','clean':0,'latencies':[],'recent_timeouts':[],'events':0,'event_epochs':[],'hold_until':0.,'wait_until':0.,'permanent_one_second':False,'ramp_steps_reached':[1.],'requests_observed':0,'last_decision':None}
            self.save();self.decision('start',time.time(),spacing_seconds=1.)
    def save(self):atomic(self.path,self.s)
    def decision(self,kind,now,**data):
        d={'at':utc(),'epoch':now,'decision':kind,**data};self.s['last_decision']=d
        with (self.root/'network/pacing-decisions.jsonl').open('a') as f:f.write(json.dumps(d)+'\n')
        self.save()
    def spacing(self,now):
        if self.s['permanent_one_second'] and now>=self.s['hold_until'] and self.s['index']==0:
            self.s['index']=1;self.s['spacing_seconds']=1.;self.decision('permanent_one_second_after_hold',now,spacing_seconds=1.)
        return LADDER[self.s['index']]
    def allowed_at(self,rate,now):
        spacing=max(.2,self.spacing(now))
        starts=[float(t) for t in rate.get('recent_starts',[]) if t>now-10]
        due=max(now,float(rate.get('last_start',0))+spacing,float(rate.get('wait_until',0)),self.s['wait_until'])
        if len(starts)>=50:due=max(due,starts[-50]+10.000001)
        return due
    def identity_mismatch(self,now,sid,http_id=None):
        s=self.s;s['index']=max(0,s['index']-2);s['spacing_seconds']=LADDER[s['index']]
        s['hold_until']=max(s['hold_until'],now+900);s['clean']=0;s['latencies']=[]
        s['identity_mismatch_events']=s.get('identity_mismatch_events',0)+1
        self.decision('identity_mismatch_backoff',now,requested_setid=sid,http_id=http_id,spacing_seconds=s['spacing_seconds'],hold_until=s['hold_until'])
    def note(self,status,latency,error,headers,now):
        s=self.s;signals=[];rv=next((v for k,v in headers.items() if k.lower()=='retry-after'),None)
        timeout=bool(error and ('timeout' in error.lower() or 'timed out' in error.lower()))
        s['recent_timeouts']=(s['recent_timeouts']+[timeout])[-20:]
        if status in (429,503):signals.append('HTTP_'+str(status))
        if rv is not None:signals.append('Retry-After');s['wait_until']=max(s['wait_until'],retry_after(rv,now))
        if timeout and sum(s['recent_timeouts'])>=3:signals.append('timeout_burst_3_in_20')
        if error and any(x in error.lower() for x in ['reset','brokenpipe','remotedisconnected']):signals.append('connection_reset')
        if latency>2*s['baseline_seconds']:signals.append('latency_over_2x_baseline')
        s['requests_observed']+=1;s['last_latency_seconds']=latency;s['last_HTTP_status']=status
        if signals:
            s['events']+=1;recent=any(now-t<=3600 for t in s['event_epochs']);s['event_epochs']=(s['event_epochs']+[now])[-100:]
            s['index']=0 if recent else max(0,s['index']-2)
            s['hold_until']=max(s['hold_until'],now+(1800 if recent else 900));s['clean']=0;s['latencies']=[]
            if s['events']>=3:s['permanent_one_second']=True
            s['spacing_seconds']=LADDER[s['index']]
            self.decision('backoff',now,event=s['events'],signals=signals,spacing_seconds=s['spacing_seconds'],hold_until=s['hold_until'],wait_until=s['wait_until'],permanent_one_second=s['permanent_one_second'])
        else:
            s['clean']+=1;s['latencies']=(s['latencies']+[latency])[-300:]
            if s['clean']>=300 and s['clean']%300==0:
                median=statistics.median(s['latencies']);advance=now>=s['hold_until'] and median<=1.5*s['baseline_seconds'] and not s['permanent_one_second'] and s['index']<len(LADDER)-1
                if advance:
                    s['index']+=1;s['clean']=0;s['latencies']=[]
                    if LADDER[s['index']] not in s['ramp_steps_reached']:s['ramp_steps_reached'].append(LADDER[s['index']])
                s['spacing_seconds']=LADDER[s['index']]
                self.decision('ramp' if advance else 'ramp_held',now,median_latency_seconds=median,spacing_seconds=s['spacing_seconds'],clean_requests=300,hold_until=s['hold_until'],permanent_one_second=s['permanent_one_second'])
        self.save();return signals
