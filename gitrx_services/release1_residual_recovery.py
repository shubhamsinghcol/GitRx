"""One explicitly authorized additive residual-family recovery round."""
import json
from .release1_job import Runner,DEFAULT_CONFIG
from .release1_http import atomic,utc,Stop

def recover(r):
    scope=json.loads((r.root/'scope.json').read_bytes())
    rows=r.c.execute("SELECT * FROM families WHERE status IN ('history_unstable','history_identity_conflict','fetch_failed') OR (status='in_progress' AND attempt_round=3) ORDER BY ordinal").fetchall()
    for n in sorted({x['tranche'] for x in rows}):
        if r.c.execute("SELECT 1 FROM retry_tranches WHERE round=3 AND number=? AND status='verified'",(n,)).fetchone():continue
        root,c,registry,fetcher=r.init_tranche(n,3)
        try:
            for row in rows:
                if row['tranche']==n:r.process_family(n,scope,row['setid'],c,root,registry,fetcher,3)
            r.parse_pool.drain(all=True)
            r.verify_tranche(n,c,root,3)
        finally:c.close()
    sources=[dict(x) for x in r.c.execute('SELECT setid,status,attempt_round,source_root,tranche FROM families ORDER BY ordinal')]
    for x in sources:
        if not x['source_root']:x['source_root']=str(r.root/'tranches'/f"{x['tranche']:04d}")
    atomic(r.root/'recovery-manifest-round-3.json',{'at':utc(),'round':3,'family_sources':sources,'retry_snapshots':[dict(x) for x in r.c.execute('SELECT * FROM retry_tranches ORDER BY round,number')],'semantics':'Latest per-family source roots; previous manifests and snapshots retained; listing-only targets have explicit provenance and no invented history dates.'})
    r.update(phase='complete',current_retry_round=3,finished_at=utc())

def main():
    from . import release1_rounds
    release1_rounds.run_all=recover
    cfg=json.loads(DEFAULT_CONFIG.read_bytes())
    Runner(cfg).run()

if __name__=='__main__':main()
