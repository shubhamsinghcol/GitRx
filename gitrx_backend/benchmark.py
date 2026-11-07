"""Explicit offline native benchmarks; append measurements without changing evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import time

from . import __version__
from .config import open_reader
from .query import Query
from .readers import canonical_sid

FORMAT = 'gitrx-performance-run-1'
POLICY = 'native-fixed-pages-1'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def hardware():
    return {'system':platform.system(), 'release':platform.release(),
            'machine':platform.machine(), 'processor':platform.processor(),
            'cpu_count':os.cpu_count(), 'python':platform.python_version(),
            'implementation':platform.python_implementation()}


def process_peak_rss():
    try:
        import resource
        value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if platform.system()=='Darwin' else value*1024)
    except (ImportError,AttributeError):
        return None


def code_hashes():
    base = Path(__file__).parent
    # Code changes should remain comparable; each run separately identifies them.
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(base.glob('*.py'))}
    from gitrx_services import section_diff_v2
    hashes['gitrx_services/section_diff_v2.py']=hashlib.sha256(Path(section_diff_v2.__file__).read_bytes()).hexdigest()
    return hashes


def compare(current, prior, ratio=1.5, seconds=0.1):
    """Compare the last run with exactly the same workload/data/environment descriptor."""
    if not math.isfinite(ratio) or ratio <= 1 or not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Regression ratio must exceed 1 and absolute tolerance must be nonnegative')
    earlier = next((r for r in reversed(prior) if r.get('format') == FORMAT
                    and r.get('comparison_key') == current['comparison_key']), None)
    if earlier is None:
        return {'baseline':None, 'regressions':[], 'correctness_changes':[]}
    old = {r['id']:r for r in earlier['operations']}
    slow, changes = [], []
    before, after = earlier.get('reader_startup_seconds',0), current.get('reader_startup_seconds',0)
    if after > before * ratio and after - before > seconds:
        slow.append({'operation':'reader_startup','before_seconds':before,'after_seconds':after})
    for row in current['operations']:
        previous = old.get(row['id'])
        if previous is None: continue
        before, after = previous['median_seconds'], row['median_seconds']
        if after > before * ratio and after - before > seconds:
            slow.append({'operation':row['id'], 'before_seconds':before, 'after_seconds':after})
        if row['result_digest'] != previous['result_digest'] or not row['repeat_results_identical']:
            changes.append(row['id'])
    return {'baseline':earlier['recorded_at'], 'regressions':slow,
            'correctness_changes':changes, 'ratio_tolerance':ratio, 'absolute_tolerance_seconds':seconds}


def measure(config_path, backend, setids, phrases=(), repeat=3, timeout_seconds=10,
            reader_factory=open_reader, clock=time.perf_counter):
    """One new reader startup, one unmeasured warm-up per operation, then repeat calls."""
    if backend not in ('git','sqlite'): raise ValueError('Choose an explicit git or sqlite backend')
    if type(repeat) is not int or not 1 <= repeat <= 10: raise ValueError('repeat must be 1..10')
    setids = sorted(set(canonical_sid(s) for s in setids))
    if not setids or len(setids)>20: raise ValueError('Select 1..20 explicit family setids')
    phrases = list(dict.fromkeys(phrases))
    if len(phrases)>10 or any(not isinstance(p,str) or not p.strip() or len(p)>500 for p in phrases):
        raise ValueError('Select at most 10 nonempty literal phrases of at most 500 characters')
    cfg = json.loads(Path(config_path).read_bytes())
    rss_start=process_peak_rss()
    start = clock()
    reader = reader_factory(config_path, backend)
    startup = clock() - start
    try:
        q = Query(reader)
        workload = []
        for sid in setids:
            versions = reader.versions(sid)
            if not versions: raise ValueError('Selected family has no captured versions: '+sid)
            first, last = versions[0]['version'], versions[-1]['version']
            workload.append(('show:'+sid, 'show', {'sid':sid,'version':last,'max_chars':16000,'section_limit':100}))
            workload.append(('compare:'+sid, 'compare', {'sid':sid,'a':first,'b':last,'max_chars':16000,'limit':50}))
            for phrase in phrases:
                workload.append(('phrase_history:'+sid+':'+digest(phrase)[:16], 'phrase_history',
                                 {'sid':sid,'phrase':phrase,'limit':50}))
        descriptor = {'policy':POLICY, 'snapshot':reader.snapshot, 'backend':backend,
                      'meta_commit':cfg.get('meta_commit') if backend=='git' else None,
                      'native_index_sha256':getattr(reader,'pin',{}).get('database_sha256') if backend=='sqlite' else None,
                      'evidence_metadata_sha256':cfg.get('evidence_metadata_sha256'),
                      'search_index_sha256':cfg.get('search_index_sha256'),
                      'cache_families':cfg.get('cache_families',32) if backend=='git' else None,
                      'cache_condition':'new application reader, OS disk cache uncontrolled; one operation warm-up before timed repeats',
                      'hardware_runtime':hardware(), 'repeat':repeat, 'timeout_seconds':timeout_seconds,
                      'harness_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                      'workload':[{'id':ident,'method':method,'arguments':args} for ident,method,args in workload]}
        operations=[]
        for ident,method,args in workload:
            fn=getattr(q,method)
            fn(timeout_seconds=timeout_seconds,**args)
            samples, answers = [], []
            for _ in range(repeat):
                start=clock();answer=fn(timeout_seconds=timeout_seconds,**args);samples.append(clock()-start)
                answers.append(answer)
            digests=[digest(a) for a in answers];answer=answers[-1]
            operations.append({'id':ident,'method':method,'seconds':samples,
                               'median_seconds':statistics.median(samples),
                               'serialized_bytes':len(canonical(answer).encode()),
                               'result_digest':digests[-1], 'repeat_results_identical':len(set(digests))==1,
                               'page_scope':{k:answer.get(k) for k in ('partial','next_offset','next_section_offset','continuation') if k in answer},
                               'scope_note':'Fixed first response page only; continuation/truncation is retained, not exhausted.'})
        return {'format':FORMAT,'recorded_at':datetime.now(timezone.utc).isoformat(),
                'package_version':__version__,'code_sha256':code_hashes(),
                'comparison_descriptor':descriptor,'comparison_key':digest(descriptor),
                'reader_startup_seconds':startup,'memory':{'process_peak_rss_bytes_at_start':rss_start,'process_peak_rss_bytes_at_end':process_peak_rss(),'meaning':'Process-lifetime high-water RSS, includes earlier work in this Python process; excludes Git children. Not a per-query measurement or memory budget.'},'reader_startup_note':'Includes configured index hash verification; no OS cache eviction.',
                'operations':operations}
    finally: reader.close()


def record(config, backend, setids, phrases, output, repeat=3, timeout_seconds=10,
           ratio=1.5, seconds=0.1):
    output=Path(output)
    # Refuse known evidence suffixes and never open the output until measurement succeeds.
    if output.suffix != '.jsonl': raise ValueError('Use a separate .jsonl performance ledger')
    config_path=Path(config).resolve();cfg=json.loads(config_path.read_bytes());destination=output.resolve()
    if destination==config_path:raise ValueError('Performance output must not replace its configuration')
    for key in ('release_root','export_root'):
        if key in cfg and destination.is_relative_to((config_path.parent/cfg[key]).resolve()):
            raise ValueError('Performance output must be outside evidence and export roots')
    prior=[json.loads(line) for line in output.read_text().splitlines() if line.strip()] if output.exists() else []
    if any(not isinstance(r,dict) or r.get('format')!=FORMAT for r in prior):raise ValueError('Existing output is not a native performance ledger')
    run=measure(config,backend,setids,phrases,repeat,timeout_seconds)
    run['comparison']=compare(run,prior,ratio,seconds)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('a') as f:
        f.write(canonical(run)+'\n')
        f.flush();os.fsync(f.fileno())
    return run


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True);p.add_argument('--backend',choices=['git','sqlite'],required=True)
    p.add_argument('--setid',action='append',required=True);p.add_argument('--phrase',action='append',default=[])
    p.add_argument('--output',required=True);p.add_argument('--repeat',type=int,default=3)
    p.add_argument('--timeout-seconds',type=float,default=10)
    p.add_argument('--regression-ratio',type=float,default=1.5)
    p.add_argument('--regression-seconds',type=float,default=.1)
    a=p.parse_args()
    run=record(a.config,a.backend,a.setid,a.phrase,a.output,a.repeat,a.timeout_seconds,a.regression_ratio,a.regression_seconds)
    result={'recorded_at':run['recorded_at'],'comparison_key':run['comparison_key'],
            'reader_startup_seconds':run['reader_startup_seconds'],'operations':len(run['operations']),
            'comparison':run['comparison']}
    print(json.dumps(result,indent=2))
    if run['comparison']['regressions'] or run['comparison']['correctness_changes'] or any(not r['repeat_results_identical'] for r in run['operations']):
        raise SystemExit(1)

if __name__=='__main__':main()
