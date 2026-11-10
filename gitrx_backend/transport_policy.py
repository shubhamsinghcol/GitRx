"""Versioned shared request pacing and bounded download concurrency."""
from pathlib import Path
import hashlib
import json
import statistics
import sqlite3
import time
import os
from .jobs import atomic


class AdaptivePacing:
    def __init__(self,root,policy_path,ledger_path=None):
        network_root=Path(root);self.root=network_root;policy_path=Path(policy_path)
        self.ledger_path=Path(ledger_path) if ledger_path is not None else network_root/'ledger.sqlite'
        raw=policy_path.read_bytes();self.policy=json.loads(raw)
        p=self.policy
        if p['format']!='gitrx-transport-policy-1' or type(p['version']) is not int or p['version']<1:
            raise ValueError('Unsupported transport policy')
        if not .05<=p['minimum_spacing_seconds']<=p['initial_spacing_seconds']<=p['maximum_spacing_seconds']<=60:
            raise ValueError('Transport must stay at or below twenty starts/second')
        if type(p.get('download_lanes',1)) is not int or not 1<=p.get('download_lanes',1)<=60:
            raise ValueError('Download lanes must be between one and sixty')
        fallbacks=p.get('lane_fallbacks')
        if fallbacks is not None and (not isinstance(fallbacks,list) or not fallbacks
            or any(type(x) is not int or not 1<=x<=60 for x in fallbacks)
            or p.get('download_lanes') not in fallbacks or any(a<=b for a,b in zip(fallbacks,fallbacks[1:]))):
            raise ValueError('Invalid lane fallback sequence')
        if p.get('clean_lane_upgrade') is not None and (type(p['clean_lane_upgrade']) is not int
            or not p['download_lanes']<p['clean_lane_upgrade']<=40
            or p['clean_lane_upgrade']>p.get('ramp_max_lanes',p['download_lanes'])):
            raise ValueError('Invalid clean lane upgrade')
        if p.get('lane_trial',False) and not 1<=p['download_lanes']<=p.get('ramp_max_lanes',20)<=20:raise ValueError('Invalid lane trial')
        if p.get('fixed_rate',False) and (p.get('controller')!='server-feedback-v1' or p['initial_spacing_seconds']!=p['minimum_spacing_seconds']):raise ValueError('Fixed rate requires matching target intervals')
        if p.get('staged_ramp',False):
            if p.get('controller')!='server-feedback-v1' or not 1<=p['download_lanes']<=p.get('ramp_max_lanes',15)<=20:
                raise ValueError('Invalid staged ramp policy')
        if type(p.get('wait_for_retry_after',False)) is not bool:raise ValueError('Retry wait must be explicitly boolean')
        if p.get('controller','legacy') not in ('legacy','server-feedback-v1'):raise ValueError('Unsupported pacing controller')
        if not 0<=p.get('success_checkpoint_seconds',0)<=1:raise ValueError('Success checkpoint interval must be 0..1 seconds')
        if p.get('clean_requests_to_speed_up',300)<20:raise ValueError('Clean pilot too short')
        if p.get('state_root'):
            self.root=Path(p['state_root']).expanduser().absolute();self.root.mkdir(parents=True,exist_ok=True)
        self.digest=hashlib.sha256(raw).hexdigest();self.path=self.root/'pacing.json'
        if self.root!=network_root:
            alias=network_root/'pacing.json'
            if alias.exists() and not alias.is_symlink():
                history=network_root/'policies';history.mkdir(exist_ok=True)
                saved=history/('retired-state-'+hashlib.sha256(alias.read_bytes()).hexdigest()+'.json')
                if not saved.exists():os.rename(alias,saved)
                else:alias.unlink()
            if alias.is_symlink() and alias.resolve()!=self.path.resolve():raise RuntimeError('Pacing state alias changed')
            if not alias.is_symlink():os.symlink(str(self.path),alias)
        history=self.root/'policies'/f'{self.digest}.json';history.parent.mkdir(exist_ok=True)
        if history.exists() and history.read_bytes()!=raw:raise RuntimeError('Transport policy evidence changed')
        if not history.exists():
            with history.open('xb') as f:f.write(raw);f.flush();__import__('os').fsync(f.fileno())
        self.state=json.loads(self.path.read_bytes()) if self.path.exists() else {}
        if self.state and self.state['policy_sha256']!=self.digest and p['version']<=self.state['policy_version']:
            raise RuntimeError('Changed transport policy requires a newer version')
        if self.state.get('policy_sha256')!=self.digest:
            old_hold=self.state.get('hold_until',0)
            old_spacing=self.state.get('spacing_seconds',0)
            if p.get('controller')=='server-feedback-v1' and self.state.get('controller')!='server-feedback-v1':
                # Legacy latency-only holds are not server feedback. Preserve the
                # authoritative server deadline independently from that state.
                old_hold=0;old_spacing=0
                ledger=Path(ledger_path) if ledger_path is not None else network_root/'ledger.sqlite'
                if ledger.exists():
                    with sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True) as db:
                        row=db.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
                        if row:old_hold=float(row[0])
            self.state={'policy_sha256':self.digest,'policy_version':p['version'],
                'spacing_seconds':max(p['initial_spacing_seconds'],old_spacing) if old_hold>time.time() else p['initial_spacing_seconds'],
                'clean':0,'hold_until':old_hold if old_hold>time.time() else 0,
                'latencies':[],'requests':0,'backoffs':0,'updated_epoch':time.time()}
            if p.get('fixed_rate'):self.state['spacing_seconds']=p['initial_spacing_seconds']
            self.save()

    @property
    def spacing(self):return self.state['spacing_seconds']

    def save(self):
        self.state['controller']=self.policy.get('controller','legacy')
        self.state['download_lanes']=self.state.get('active_lanes',self.policy.get('download_lanes',1))
        self.state['updated_epoch']=time.time();atomic(self.path,self.state)
        self.last_saved_monotonic=time.monotonic()

    def note(self,status,failure,latency,headers,now=None):
        now=time.time() if now is None else now;s=self.state;p=self.policy
        s['requests']+=1;s['last_status']=status;s['last_latency_seconds']=latency
        s['latencies']=(s['latencies']+[latency])[-20:]
        if p.get('controller')=='server-feedback-v1':
            self._feedback(status,failure,headers,now)
            coalesce=(p.get('fixed_rate') and not p.get('lane_trial')
                      and not p.get('clean_lane_upgrade') and status==200
                      and not failure and 'retry-after' not in headers)
            if (not coalesce or time.monotonic()-getattr(self,'last_saved_monotonic',0)
                >=p.get('success_checkpoint_seconds',0)):
                self.save()
            return
        pressure=bool(failure) or status in (408,429,500,502,503,504) or 'retry-after' in headers
        slow=len(s['latencies'])==20 and statistics.median(s['latencies'])>p.get('slow_median_seconds',2)
        if pressure or slow:
            s['spacing_seconds']=min(p['maximum_spacing_seconds'],max(2,s['spacing_seconds']*2))
            s['hold_until']=max(s['hold_until'],now+p.get('hold_seconds',300));s['clean']=0;s['backoffs']+=1
        elif status==200:
            s['clean']+=1
            if s['clean']>=p.get('clean_requests_to_speed_up',300) and now>=s['hold_until']:
                s['spacing_seconds']=max(p['minimum_spacing_seconds'],s['spacing_seconds']/2);s['clean']=0
        self.save()

    def _feedback(self,status,failure,headers,now):
        s=self.state;p=self.policy
        pressure=bool(failure) or status in (408,429,500,502,503,504) or 'retry-after' in headers
        if p.get('fixed_rate',False) or p.get('lane_trial',False):
            # A cooldown pauses starts; it must never become a permanently
            # reduced request rate. Source response headers remain untouched.
            rate=s.setdefault('target_hz',1/p['initial_spacing_seconds'])
            s['spacing_seconds']=1/rate
            if pressure:
                if status==429 and p.get('lane_fallbacks') and now>=s['hold_until']:
                    s['escalation_stopped']=True
                    choices=p['lane_fallbacks'];current=s.get('active_lanes',p['download_lanes'])
                    if current in choices and choices.index(current)+1<len(choices):
                        s['active_lanes']=choices[choices.index(current)+1]
                        s.setdefault('fallback_steps',[]).append({'epoch':now,'from':current,'to':s['active_lanes'],'status':429})
                if p.get('lane_trial') and not s.get('ramp_stopped'):
                    s['active_lanes']=max(1,s.get('active_lanes',p['download_lanes'])-1);s['ramp_stopped']=True
                from gitrx.current.archive import retry_delay
                delay=max(p.get('hold_seconds',60),retry_delay(list(headers.items()),0) if 'retry-after' in headers else 0)
                s['hold_until']=max(s['hold_until'],now+delay);s['clean']=0;s['backoffs']+=1
                if self.ledger_path.exists():
                    with sqlite3.connect(self.ledger_path) as db:
                        row=db.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
                        db.execute("INSERT OR REPLACE INTO transport_state VALUES('retry_after',?)",(max(s['hold_until'],float(row[0]) if row else 0),))
            elif status==200:
                s['clean']+=1
                if p.get('clean_lane_upgrade'):
                    began=s.setdefault('upgrade_started',now)
                    current=s.get('active_lanes',p['download_lanes'])
                    if (not s.get('escalation_stopped') and now>=s['hold_until']
                        and now-began>=60 and s['clean']>=20 and current<p['clean_lane_upgrade']):
                        s['active_lanes']=p['clean_lane_upgrade']
                        s.setdefault('ramp_steps',[]).append({'epoch':now,'hz':rate,'lanes':s['active_lanes']})
                if p.get('lane_trial'):
                    began=s.setdefault('stage_started',now);lanes=s.setdefault('active_lanes',p['download_lanes'])
                    if (not s.get('ramp_stopped') and now>=s['hold_until'] and now-began>=15
                        and s['clean']>=20 and lanes<p.get('ramp_max_lanes',20)):
                        lanes+=1;s['active_lanes']=lanes;s['stage_started']=now;s['clean']=0
                        s.setdefault('ramp_steps',[]).append({'epoch':now,'hz':rate,'lanes':lanes})
            return
        if pressure:
            if p.get('staged_ramp'):s['ramp_stopped']=True
            # One congested flight causes one reduction, not one reduction per
            # response in that flight. Retry-After may always extend the hold.
            if now>=s['hold_until']:
                s['spacing_seconds']=min(p['maximum_spacing_seconds'],s['spacing_seconds']*2)
                s['backoffs']+=1
            hold=p.get('hold_seconds',60)
            if 'retry-after' in headers:
                from gitrx.current.archive import retry_delay
                hold=max(hold,retry_delay(list(headers.items()),0))
            s['hold_until']=max(s['hold_until'],now+hold)
            s['clean']=0
        elif status==200:
            s['clean']+=1
            if p.get('staged_ramp'):
                began=s.setdefault('stage_started',now)
                lanes=s.setdefault('active_lanes',p['download_lanes'])
                if (not s.get('ramp_stopped') and now>=s['hold_until']
                    and now-began>=10 and s['clean']>=20
                    and lanes<p.get('ramp_max_lanes',15)):
                    lanes+=1;s['active_lanes']=lanes
                    s['spacing_seconds']=max(p['minimum_spacing_seconds'],1/lanes)
                    s['stage_started']=now;s['clean']=0
                    s.setdefault('ramp_steps',[]).append({'epoch':now,'hz':1/s['spacing_seconds'],'lanes':lanes})
                return
            if (s['clean']>=p.get('clean_requests_to_speed_up',20)
                and now>=s['hold_until'] and now>=s.get('last_increase',0)+5):
                # Add 0.5 starts/second after sustained success, rather than
                # doubling into the same server limit again.
                rate=1/s['spacing_seconds']
                s['spacing_seconds']=max(p['minimum_spacing_seconds'],1/(rate+.5))
                s['last_increase']=now;s['clean']=0
