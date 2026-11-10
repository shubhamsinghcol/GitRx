import json
from pathlib import Path
import pytest
from gitrx_backend.transport_policy import AdaptivePacing


def policy(tmp_path):
 p=tmp_path/'policy.json';p.write_text(json.dumps({'format':'gitrx-transport-policy-1','version':1,'minimum_spacing_seconds':.2,'initial_spacing_seconds':1,'maximum_spacing_seconds':30,'clean_requests_to_speed_up':20,'hold_seconds':300}))
 return p


def test_ramp_is_bounded_and_server_backoff_survives_restart(tmp_path):
 p=policy(tmp_path);a=AdaptivePacing(tmp_path,p)
 for n in range(60):a.note(200,None,.05,{},now=n)
 assert a.spacing==.2
 a.note(429,None,.05,{'retry-after':'30'},now=100)
 assert a.spacing>=2 and a.state['hold_until']==400
 b=AdaptivePacing(tmp_path,p)
 assert b.spacing==a.spacing
 for n in range(30):b.note(200,None,.05,{},now=200+n)
 assert b.spacing>=2
 for n in range(20):b.note(200,None,.05,{},now=401+n)
 assert b.spacing>=.2


def test_unversioned_policy_changes_and_excess_rate_are_refused(tmp_path):
 p=policy(tmp_path);AdaptivePacing(tmp_path,p)
 v=json.loads(p.read_bytes());v['initial_spacing_seconds']=2;p.write_text(json.dumps(v))
 with pytest.raises(RuntimeError,match='newer version'):AdaptivePacing(tmp_path,p)
 v['minimum_spacing_seconds']=.01;p.write_text(json.dumps(v))
 with pytest.raises(ValueError,match='twenty starts'):AdaptivePacing(tmp_path,p)


def test_two_lane_policy_upgrade_preserves_active_server_backoff(tmp_path):
 import time
 p=policy(tmp_path);a=AdaptivePacing(tmp_path,p)
 a.note(429,None,.1,{'retry-after':'30'})
 hold=a.state['hold_until']
 value=json.loads(p.read_bytes());value.update(version=2,download_lanes=2,minimum_spacing_seconds=.1,initial_spacing_seconds=.1)
 p.write_text(json.dumps(value));b=AdaptivePacing(tmp_path,p)
 assert b.spacing>=2 and b.state['hold_until']==hold and b.state['download_lanes']==2


def test_twenty_hertz_requires_versioned_policy_and_preserves_backoff(tmp_path):
 p=policy(tmp_path);a=AdaptivePacing(tmp_path,p)
 a.note(429,None,.1,{'retry-after':'30'})
 hold=a.state['hold_until']
 value=json.loads(p.read_bytes());value.update(version=2,download_lanes=20,minimum_spacing_seconds=.05,initial_spacing_seconds=.05)
 p.write_text(json.dumps(value));b=AdaptivePacing(tmp_path,p)
 assert b.spacing>=2 and b.state['hold_until']==hold
 value.update(version=3,minimum_spacing_seconds=.049,initial_spacing_seconds=.049)
 p.write_text(json.dumps(value))
 with pytest.raises(ValueError,match='twenty starts'):AdaptivePacing(tmp_path,p)


def test_feedback_slow_successes_do_not_backoff_and_ramp_gradually(tmp_path):
 p=policy(tmp_path);v=json.loads(p.read_text());v.update(controller='server-feedback-v1',initial_spacing_seconds=.2,minimum_spacing_seconds=1/15)
 p.write_text(json.dumps(v));a=AdaptivePacing(tmp_path,p)
 for n in range(100):a.note(200,None,3,{},now=1000+n)
 assert a.state['backoffs']==0 and 1/7.6<a.spacing<.2


def test_feedback_congested_flight_reduces_once_and_preserves_retry(tmp_path):
 p=policy(tmp_path);v=json.loads(p.read_text());v.update(controller='server-feedback-v1',initial_spacing_seconds=.2,minimum_spacing_seconds=1/15,hold_seconds=60)
 p.write_text(json.dumps(v));a=AdaptivePacing(tmp_path,p)
 for n in range(15):a.note(429,None,.1,{'retry-after':'60'},now=100+n)
 assert a.spacing==.4 and a.state['backoffs']==1 and a.state['hold_until']==174
 b=AdaptivePacing(tmp_path,p);assert b.spacing==.4
 for n in range(20):b.note(200,None,3,{},now=150+n)
 assert b.spacing==.4


def test_feedback_migration_drops_latency_hold_but_keeps_server_deadline(tmp_path):
 import sqlite3,time
 p=policy(tmp_path);a=AdaptivePacing(tmp_path,p);a.note(200,None,3,{})
 a.state.update(spacing_seconds=30,hold_until=time.time()+300);a.save()
 deadline=time.time()+60
 with sqlite3.connect(tmp_path/'ledger.sqlite') as c:
  c.execute('CREATE TABLE transport_state(key TEXT PRIMARY KEY,value REAL)');c.execute('INSERT INTO transport_state VALUES(?,?)',('retry_after',deadline))
 v=json.loads(p.read_text());v.update(version=2,controller='server-feedback-v1',initial_spacing_seconds=.2)
 p.write_text(json.dumps(v));b=AdaptivePacing(tmp_path,p)
 assert b.spacing==.2 and b.state['hold_until']==deadline


def test_staged_ramp_observes_ten_seconds_and_stops_on_429(tmp_path):
 p=policy(tmp_path);v=json.loads(p.read_text());v.update(controller='server-feedback-v1',initial_spacing_seconds=.1,minimum_spacing_seconds=1/15,download_lanes=10,staged_ramp=True,ramp_max_lanes=15,hold_seconds=60)
 p.write_text(json.dumps(v));a=AdaptivePacing(tmp_path,p)
 for n in range(100):a.note(200,None,.1,{},now=100+n/10)
 assert a.spacing==.1 and a.state['active_lanes']==10
 a.note(200,None,.1,{},now=110)
 assert a.spacing==1/11 and a.state['active_lanes']==11
 for n in range(30):a.note(200,None,.1,{},now=111+n/10)
 assert a.state['active_lanes']==11
 a.note(429,None,.1,{'retry-after':'60'},now=115)
 for n in range(100):a.note(200,None,.1,{},now=200+n)
 assert a.state['ramp_stopped'] and a.state['active_lanes']==11 and a.spacing==2/11


def test_fixed_rate_never_drifts_and_pressure_persists_cooldown(tmp_path):
 import sqlite3
 p=policy(tmp_path);v=json.loads(p.read_text());v.update(controller='server-feedback-v1',fixed_rate=True,initial_spacing_seconds=1/12,minimum_spacing_seconds=1/12,download_lanes=12,hold_seconds=60)
 p.write_text(json.dumps(v))
 with sqlite3.connect(tmp_path/'ledger.sqlite') as c:c.execute('CREATE TABLE transport_state(key TEXT PRIMARY KEY,value REAL)')
 a=AdaptivePacing(tmp_path,p)
 for n in range(50):a.note(200,None,10,{},now=100+n)
 assert a.spacing==1/12
 a.note(429,None,.1,{'retry-after':'120'},now=200)
 assert a.spacing==1/12 and a.state['hold_until']==320
 with sqlite3.connect(tmp_path/'ledger.sqlite') as c:assert c.execute('SELECT value FROM transport_state').fetchone()[0]==320
 b=AdaptivePacing(tmp_path,p);assert b.spacing==1/12
 for n in range(50):b.note(200,None,10,{},now=400+n)
 assert b.spacing==1/12


def test_lane_trial_keeps_rate_and_steps_back_once_on_pressure(tmp_path):
 p=policy(tmp_path);v=json.loads(p.read_text());v.update(controller='server-feedback-v1',lane_trial=True,initial_spacing_seconds=1/12,minimum_spacing_seconds=1/12,download_lanes=13,ramp_max_lanes=20,hold_seconds=60)
 p.write_text(json.dumps(v));a=AdaptivePacing(tmp_path,p)
 for n in range(140):a.note(200,None,.1,{},now=100+n/10)
 assert a.spacing==1/12 and a.state['download_lanes']==13
 a.note(200,None,.1,{},now=115)
 assert a.spacing==1/12 and a.state['download_lanes']==14
 a.note(0,'timed out',30,{},now=117)
 assert a.spacing==1/12 and a.state['download_lanes']==13 and a.state['ramp_stopped']
 a.note(429,None,.1,{'retry-after':'60'},now=118)
 assert a.state['download_lanes']==13
 for n in range(100):a.note(200,None,.1,{},now=200+n)
 assert a.state['download_lanes']==13 and a.spacing==1/12


def test_fixed_rate_lane_fallbacks_reduce_once_per_429_flight(tmp_path):
 p=policy(tmp_path);v=json.loads(p.read_text());v.update(controller='server-feedback-v1',fixed_rate=True,initial_spacing_seconds=1/12,minimum_spacing_seconds=1/12,download_lanes=30,lane_fallbacks=[30,25,20],hold_seconds=60)
 p.write_text(json.dumps(v));a=AdaptivePacing(tmp_path,p)
 a.note(429,None,.1,{'retry-after':'60'},now=100)
 assert a.state['download_lanes']==25 and a.spacing==1/12
 a.note(429,None,.1,{'retry-after':'60'},now=101)
 assert a.state['download_lanes']==25
 b=AdaptivePacing(tmp_path,p)
 b.note(429,None,.1,{'retry-after':'60'},now=200)
 assert b.state['download_lanes']==20 and b.spacing==1/12
 b.note(429,None,.1,{'retry-after':'60'},now=300)
 assert b.state['download_lanes']==20
 v.update(version=2,download_lanes=61,lane_fallbacks=[61,25,20]);p.write_text(json.dumps(v))
 with pytest.raises(ValueError,match='sixty'):AdaptivePacing(tmp_path,p)


def test_clean_minute_upgrades_30_to_40_and_429_falls_back_without_reupgrade(tmp_path):
 p=policy(tmp_path);v=json.loads(p.read_text());v.update(controller='server-feedback-v1',fixed_rate=True,initial_spacing_seconds=1/12,minimum_spacing_seconds=1/12,download_lanes=30,ramp_max_lanes=40,lane_fallbacks=[40,30,25,20],clean_lane_upgrade=40,hold_seconds=60)
 p.write_text(json.dumps(v));a=AdaptivePacing(tmp_path,p)
 for n in range(590):a.note(200,None,.1,{},now=100+n/10)
 assert a.state['download_lanes']==30
 a.note(200,None,.1,{},now=160)
 assert a.state['download_lanes']==40 and a.spacing==1/12
 a.note(429,None,.1,{'retry-after':'60'},now=170)
 assert a.state['download_lanes']==30 and a.state['escalation_stopped']
 for n in range(100):a.note(200,None,.1,{},now=300+n)
 assert a.state['download_lanes']==30
 a.note(429,None,.1,{'retry-after':'60'},now=500)
 assert a.state['download_lanes']==25


def test_success_checkpoint_coalesces_only_operational_successes(tmp_path,monkeypatch):
 import gitrx_backend.transport_policy as module
 clock=[0.];monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
 p=policy(tmp_path);v=json.loads(p.read_text());v.update(controller='server-feedback-v1',fixed_rate=True,initial_spacing_seconds=1/12,minimum_spacing_seconds=1/12,download_lanes=60,success_checkpoint_seconds=1,hold_seconds=60)
 p.write_text(json.dumps(v));a=AdaptivePacing(tmp_path,p);writes=[];original=module.atomic
 def write(path,value):writes.append(value.copy());return original(path,value)
 monkeypatch.setattr(module,'atomic',write)
 for n in range(20):a.note(200,None,.1,{},now=100+n)
 assert writes==[]
 a.note(429,None,.1,{'retry-after':'60'},now=130)
 assert len(writes)==1 and writes[0]['hold_until']==190
 assert AdaptivePacing(tmp_path,p).state['hold_until']==190
 clock[0]=1.1;a.note(200,None,.1,{},now=200)
 assert len(writes)==2
