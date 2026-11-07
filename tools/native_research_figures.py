"""Reproduce descriptive captured-record figures from a pinned native release."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import sqlite3


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    config=Path(a.config).resolve();cfg=json.loads(config.read_bytes());out=Path(a.output).resolve()
    for key in ('release_root','export_root'):
        if key in cfg and out.is_relative_to((config.parent/cfg[key]).resolve()):raise ValueError('Figure output must be outside evidence/export roots')
    if out.exists():raise ValueError('Use a new output directory')
    # Use maintained readers to verify all index/metadata input hashes first.
    from gitrx_backend.config import open_reader
    with open_reader(config,'sqlite') as reader:
        families=reader.families();counts=[len(reader.versions(f['setid'])) for f in families]
        if not reader.evidence_metadata_available:raise ValueError('Pinned extraction metadata is required')
        db=reader._evidence_metadata.conn
        statuses=Counter(json.loads(r[0])['extraction']['status'] for r in db.execute('SELECT record FROM versions'))
        unknown=sum(json.loads(r[0]).get('unknown_upstream_history') is True for r in db.execute('SELECT record FROM families'))
        data={'snapshot':reader.snapshot,'native_index_sha256':reader.pin['database_sha256'],
              'evidence_metadata_sha256':reader.evidence_metadata_sha256,'families':len(families),
              'captured_versions':sum(counts),'versions_per_family_histogram':dict(sorted(Counter(counts).items())),
              'extraction_status':dict(statuses),'unknown_upstream_history_families':unknown}
    assert sum(statuses.values())==sum(counts)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    xs=sorted(set(counts));freq=Counter(counts)
    axes[0].bar(xs,[freq[x] for x in xs],color='#315c85',width=.85)
    axes[0].set(xlabel='Captured versions per setid family',ylabel='Families',title=f'Captured histories (n = {len(counts):,} families)')
    axes[0].set_yscale('log');axes[0].set_ylim(bottom=.8)
    labels=sorted(statuses);bars=axes[1].bar(labels,[statuses[x] for x in labels],color=['#315c85' if x=='complete' else '#ac7737' for x in labels])
    axes[1].bar_label(bars,labels=[f'{statuses[x]:,}' for x in labels],padding=4)
    axes[1].set(ylabel='Captured versions',title=f'Parser diagnostic status (n = {sum(statuses.values()):,})')
    axes[1].set_ylim(0,max(statuses.values())*1.17)
    fig.suptitle('GitRx selected DailyMed record — descriptive counts',fontsize=13)
    out.mkdir(parents=True)
    fig.savefig(out/'captured-record.png',dpi=180);fig.savefig(out/'captured-record.svg');plt.close(fig)
    caption=(f"Selected snapshot {data['snapshot']}. Left: captured-version counts per setid family; logarithmic family-count axis. "
             f"Right: parser diagnostic statuses per captured version. {unknown} family has unresolved upstream inventory. "
             "These are retained-record denominators, not historical completeness, extraction accuracy, approval or clinical-risk estimates.")
    (out/'CAPTION.txt').write_text(caption+'\n')
    data.update(script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),python=platform.python_version(),matplotlib=matplotlib.__version__,caption=caption)
    (out/'INPUTS.json').write_text(json.dumps(data,indent=2)+'\n')
    print(json.dumps({'families':data['families'],'versions':data['captured_versions'],'output':str(out)}))

if __name__=='__main__':main()
