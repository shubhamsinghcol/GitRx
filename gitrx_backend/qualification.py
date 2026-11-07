"""Explicit offline workload qualification in isolated main-thread processes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import select
import subprocess
import sys
import tempfile
import time

from .benchmark import canonical, digest, hardware, code_hashes, process_peak_rss
from .config import open_reader
from .query import Query
from .mcp import tools
from gitrx_services.schema import errors

FORMAT='gitrx-qualification-run-1'
PAGES={'search':('hits','continuation','cursor'),
       'inventory_on_date':('rows','next_offset','offset'),
       'token_provenance':('tokens','next_offset','offset'),
       'phrase_history':('observations','continuation','cursor'),
       'search_changes':('hits','continuation','cursor'),
       'versions':('versions','next_offset','offset'),
       'compare':('comparisons','next_offset','offset'),
       'version_changes':('changes','next_offset','offset')}


def normalized(value):
    # Preserve evidence and locations; adapter representation keys may differ.
    if isinstance(value,dict):
        return {k:normalized(v) for k,v in value.items() if k not in ('parse_key','meta_commit','label_commit','commit')}
    if isinstance(value,list):return [normalized(v) for v in value]
    return value


def validate_case(case):
    if not isinstance(case,dict) or set(case)-{'id','profile','backend','method','arguments','traverse','max_pages','repeat','timeout_seconds','max_seconds','equivalence_group'}:
        raise ValueError('Unknown qualification case fields')
    if not isinstance(case.get('id'),str) or not case['id'] or len(case['id'])>200:raise ValueError('Case id required')
    if case.get('backend') not in ('git','sqlite'):raise ValueError('Explicit case backend required')
    schema=tools().get(case.get('method'))
    if schema is None:raise ValueError('Unknown shared query')
    problems=errors(case.get('arguments',{}),schema['inputSchema'])
    if problems:raise ValueError('; '.join(problems))
    if 'timeout_seconds' in case.get('arguments',{}):raise ValueError('Set case timeout_seconds separately')
    if case.get('traverse','first') not in ('first','all'):raise ValueError('Choose first or all traversal')
    if case.get('traverse')=='all' and case['method'] not in PAGES:raise ValueError('No maintained traversal for this query')
    for field,default,maximum in [('repeat',2,10),('max_pages',10000,10000)]:
        n=case.get(field,default)
        if type(n) is not int or not 1<=n<=maximum:raise ValueError('Invalid '+field)
    for field,default,maximum in [('timeout_seconds',30,60),('max_seconds',300,3600)]:
        n=case.get(field,default)
        if type(n) not in (int,float) or not .01<=n<=maximum:raise ValueError('Invalid '+field)


def traversal(query,case):
    args=dict(case.get('arguments',{}));method=case['method'];total_bytes=0;pages=0;count=0
    h=hashlib.sha256();truncated=0;complete=False;seen=set();exclusions={}
    while pages<case.get('max_pages',10000):
        answer=getattr(query,method)(**args,timeout_seconds=case.get('timeout_seconds',30))
        total_bytes+=len(canonical(answer).encode());pages+=1
        if method in PAGES:
            field,continuation,argument=PAGES[method];rows=answer[field];next_value=answer.get(continuation)
        else:rows=[answer];next_value=None
        for row in rows:
            h.update(canonical(normalized(row)).encode()+b'\n');count+=1
            if isinstance(row,dict):truncated+=bool(row.get('truncated') or row.get('matches_truncated'))
        for key in ('unexamined_occurrences','date_unknown_transitions_excluded','extraction_uncertain_transitions'):
            exclusions[key]=exclusions.get(key,0)+answer.get(key,0)
        complete=next_value is None
        if complete or case.get('traverse','first')=='first':break
        key=canonical(next_value)
        if key in seen:raise ValueError('Non-advancing query continuation')
        seen.add(key);args[argument]=next_value
        if argument=='cursor':
            for key in ('offset','family_offset','section_offset'):args.pop(key,None)
    if case.get('traverse')=='all' and not complete:raise ValueError('Traversal exceeded max_pages; no complete result')
    return {'result_digest':h.hexdigest(),'records':count,'pages':pages,'serialized_bytes':total_bytes,
            'traversal_complete':complete,'truncated_records':truncated,'exclusions':exclusions,
            'meaning':'Completion covers selected captured records; row text/excerpts may be truncated. Not source completeness.'}


def worker(config,case):
    validate_case(case);start=time.perf_counter()
    with open_reader(config,case['backend']) as reader:
        startup=time.perf_counter()-start;query=Query(reader);samples=[]
        # First query is explicitly measured with a new reader; later repetitions
        # measure the same workload with application caches warmed by that query.
        for _ in range(case.get('repeat',2)):
            start=time.perf_counter();result=traversal(query,case);samples.append(dict(result,seconds=time.perf_counter()-start))
        return {'id':case['id'],'snapshot':reader.snapshot,'reader_startup_seconds':startup,
                'first':samples[0],'warm':samples[1:],'repeat_results_identical':len({x['result_digest'] for x in samples})==1,
                'process_peak_rss_bytes':process_peak_rss(),
                'memory_meaning':'Fresh process lifetime peak RSS; excludes Git children. OS disk cache uncontrolled.'}


def isolated(config,case):
    with tempfile.TemporaryDirectory(prefix='gitrx-qualification-') as tmp:
        task=Path(tmp)/'task.json';task.write_text(canonical({'config':str(config),'case':case}))
        command=[sys.executable,'-B','-m','gitrx_backend.qualification','--worker',str(task)]
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as err:
            process=subprocess.Popen(command,stdout=output,stderr=err,start_new_session=True)
            try:
                process.wait(timeout=case.get('max_seconds',300))
            except BaseException:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait();raise
            output.seek(0);err.seek(0)
            if process.returncode:raise RuntimeError('Qualification worker failed: '+err.read(4000).decode(errors='replace'))
            return json.loads(output.read())


def transport_probe(config,backend,sid,version):
    """Actual stdio cancellation/recovery, with pinned evidence on both sides."""
    command=[sys.executable,'-B','-m','gitrx_backend.mcp','--config',str(config),'--backend',backend]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    buffer=b'';received=[]
    def send(method,ident=None,params=None):
        request={'jsonrpc':'2.0','method':method}
        if ident is not None:request['id']=ident
        if params is not None:request['params']=params
        process.stdin.write((canonical(request)+'\n').encode());process.stdin.flush()
    def receive(seconds=15):
        nonlocal buffer
        until=time.monotonic()+seconds
        while b'\n' not in buffer:
            if not select.select([process.stdout],[],[],max(0,until-time.monotonic()))[0]:raise TimeoutError('MCP qualification response timed out')
            raw=os.read(process.stdout.fileno(),65536)
            if not raw:raise RuntimeError('MCP qualification output closed')
            buffer+=raw
        line,buffer=buffer.split(b'\n',1);answer=json.loads(line);received.append(answer.get('id'));return answer
    try:
        send('initialize',1,{'protocolVersion':'2025-11-25'});assert receive()['id']==1
        send('notifications/initialized')
        send('tools/call',2,{'name':'show','arguments':{'sid':sid,'version':version,'ordinal':1,'max_chars':100}})
        before=receive();assert not before['result']['isError'];snapshot=before['result']['structuredContent']['snapshot']
        send('tools/call',3,{'name':'search','arguments':{'phrase':'GitRx-qualification-cancel-absent-20261107','max_families':7239,'time_budget_seconds':55,'timeout_seconds':60}})
        time.sleep(.05);start=time.perf_counter();send('ping',4)
        ping=receive();overtook=ping.get('id')==4
        while ping.get('id')!=4:ping=receive()
        ping_seconds=time.perf_counter()-start
        start=time.perf_counter();send('notifications/cancelled',params={'requestId':3})
        send('tools/call',5,{'name':'show','arguments':{'sid':sid,'version':version,'ordinal':1,'max_chars':100}})
        after=receive()
        while after.get('id')!=5:after=receive()
        recovery=time.perf_counter()-start
        assert not after['result']['isError'] and after['result']['structuredContent']['snapshot']==snapshot
        process.stdin.close();process.wait(timeout=15)
        # Drain any late reply; cancelled work must not produce evidence later.
        for line in (buffer+process.stdout.read()).splitlines():received.append(json.loads(line).get('id'))
        return {'snapshot':snapshot,'reply_ids':received,'ping_overtook_query':overtook,
                'ping_seconds':ping_seconds,'cancellation_recovery_seconds':recovery,
                'cancelled_reply_suppressed':3 not in received,'returncode':process.returncode,
                'passed':overtook and 3 not in received and process.returncode==0,
                'meaning':'One actual local stdio session and cancelled corpus scan, not a latency SLO.'}
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL);process.wait()
        for stream in (process.stdin,process.stdout,process.stderr):stream.close()


def run(plan_path):
    path=Path(plan_path).resolve();raw=path.read_bytes();plan=json.loads(raw)
    if set(plan)-{'format','profiles','cases','concurrency','transport'} or plan.get('format')!='gitrx-qualification-plan-1':raise ValueError('Invalid qualification plan')
    profiles={name:(path.parent/p).resolve() for name,p in plan['profiles'].items()}
    cases=plan['cases'];ids=[x['id'] for x in cases]
    if not cases or len(cases)>100 or len(set(ids))!=len(ids):raise ValueError('Select 1..100 uniquely named cases')
    for case in cases:
        validate_case(case)
        if case.get('profile') not in profiles:raise ValueError('Unknown case profile')
    concurrency=plan.get('concurrency',{})
    if set(concurrency)-{'case','workers'}:raise ValueError('Unknown concurrency fields')
    if concurrency and (concurrency.get('case') not in ids or type(concurrency.get('workers')) is not int or not 1<=concurrency['workers']<=8):raise ValueError('Invalid concurrency cohort')
    pins={name:{'configuration':json.loads(p.read_bytes()),'config_sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for name,p in profiles.items()}
    software=code_hashes()
    rows=[]
    for case in cases:rows.append(isolated(profiles[case['profile']],case))
    groups={}
    for case,row in zip(cases,rows):
        if case.get('equivalence_group'):groups.setdefault(case['equivalence_group'],[]).append(row['first']['result_digest'])
    concurrent=None
    if concurrency:
        case=next(x for x in cases if x['id']==concurrency['case']);start=time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency['workers']) as pool:
            futures=[pool.submit(isolated,profiles[case['profile']],case) for _ in range(concurrency['workers'])]
            workers=[f.result() for f in futures]
        concurrent={'workers':workers,'wall_seconds':time.perf_counter()-start,'same_results':len({r['first']['result_digest'] for r in workers})==1,
                    'scope':'Independent local main-thread reader processes; no hosted capacity or aggregate memory guarantee.'}
    transport=None
    if plan.get('transport'):
        t=plan['transport'];transport=transport_probe(profiles[t['profile']],t['backend'],t['sid'],t['version'])
    if software!=code_hashes():raise RuntimeError('Query software changed during qualification; discard mixed-software measurement')
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=pins[name]['config_sha256'] for name,p in profiles.items()):
        raise RuntimeError('Profile changed during qualification; discard mixed-profile measurement')
    return {'format':FORMAT,'recorded_at':datetime.now(timezone.utc).isoformat(),'plan_sha256':hashlib.sha256(raw).hexdigest(),
            'plan':plan,'profiles':pins,'hardware_runtime':hardware(),'software_sha256':software,'cases':rows,
            'equivalence_groups':{k:len(set(v))==1 for k,v in groups.items()},'concurrent':concurrent,'transport':transport,
            'passed':all(r['repeat_results_identical'] for r in rows) and all(len(set(v))==1 for v in groups.values()) and (concurrent is None or concurrent['same_results']) and (transport is None or transport['passed'])}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--worker');p.add_argument('--plan');p.add_argument('--output');a=p.parse_args()
    if a.worker:
        task=json.loads(Path(a.worker).read_bytes());print(canonical(worker(task['config'],task['case'])));return
    if not a.plan or not a.output:p.error('Choose --plan and separate --output JSONL')
    plan=Path(a.plan).resolve();output=Path(a.output).resolve()
    if output.suffix!='.jsonl' or output==plan:raise ValueError('Use a separate JSONL qualification ledger')
    data=json.loads(plan.read_bytes())
    for profile in data['profiles'].values():
        config=(plan.parent/profile).resolve();cfg=json.loads(config.read_bytes())
        if output==config:raise ValueError('Output conflicts with configuration')
        for key in ('release_root','export_root'):
            if key in cfg and output.is_relative_to((config.parent/cfg[key]).resolve()):raise ValueError('Output must be outside evidence/export roots')
    if output.exists() and any(json.loads(line).get('format')!=FORMAT for line in output.read_text().splitlines() if line.strip()):raise ValueError('Output is not a qualification ledger')
    result=run(plan);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('a') as f:f.write(canonical(result)+'\n');f.flush();os.fsync(f.fileno())
    print(canonical({'passed':result['passed'],'cases':len(result['cases']),'output':str(output)}))
    if not result['passed']:raise SystemExit(1)

if __name__=='__main__':main()
