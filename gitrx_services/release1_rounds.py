"""Initial family pass and two bounded recovery sweeps in additive stores."""
import json,math,time
from .release1_http import Stop,atomic,utc

def run_all(r):
    from tools.block3c_discover import discover
    scope=discover(r.config,lambda s:r.update(phase='scope_discovery',scope_pages=s['pages'],scope_eligible_seen=s['eligible_seen']))
    with r.c:
        for i,sid in enumerate(scope['setids']):r.c.execute('INSERT OR IGNORE INTO families(setid,ordinal,tranche) VALUES(?,?,?)',(sid,i+1,i//r.config['tranche_size']+1))
    total=math.ceil(len(scope['setids'])/r.config['tranche_size'])
    for retry_round in range(3):
        r.retry_round=retry_round
        for n in range(1,total+1):
            if retry_round==0:
                verified=r.c.execute("SELECT 1 FROM tranches WHERE number=? AND status='verified'",(n,)).fetchone()
                rows=r.c.execute("SELECT * FROM families WHERE tranche=? AND status IN ('pending','in_progress') AND attempt_round=0 ORDER BY ordinal",(n,)).fetchall()
            else:
                verified=r.c.execute("SELECT 1 FROM retry_tranches WHERE round=? AND number=? AND status='verified'",(retry_round,n)).fetchone()
                rows=r.c.execute("SELECT * FROM families WHERE tranche=? AND ((status IN ('history_unstable','fetch_failed','history_identity_conflict') AND attempt_round<?) OR (status='in_progress' AND attempt_round=?)) ORDER BY ordinal",(n,retry_round,retry_round)).fetchall()
            if verified:
                if retry_round==0:r.exports(n,len(scope['setids']))
                continue
            if retry_round and not rows:continue
            root,c,registry,fetcher=r.init_tranche(n,retry_round)
            try:
                for row in rows:
                    if (r.root/'fatal-stop.json').exists():raise Stop(json.loads((r.root/'fatal-stop.json').read_bytes())['reason'])
                    r.process_family(n,scope,row['setid'],c,root,registry,fetcher,retry_round)
                if hasattr(r,'parse_pool'):r.parse_pool.drain(all=True)
                r.verify_tranche(n,c,root,retry_round)
            finally:c.close()
            if retry_round==0:r.exports(n,len(scope['setids']))
        r.update(phase='retry_sweep_complete' if retry_round else 'primary_pass_complete',current_retry_round=retry_round)
        if retry_round:
            family_sources=[dict(x) for x in r.c.execute('SELECT setid,status,attempt_round,source_root,tranche FROM families ORDER BY ordinal')]
            for item in family_sources:
                if not item['source_root']:item['source_root']=str(r.root/'tranches'/f"{item['tranche']:04d}")
            atomic(r.root/f'recovery-manifest-round-{retry_round}.json',{'at':utc(),'round':retry_round,
                'family_sources':family_sources,
                'retry_snapshots':[dict(x) for x in r.c.execute('SELECT * FROM retry_tranches ORDER BY round,number')],
                'semantics':'Use latest per-family source root for a new final export; preserve initial milestone exports and all previous shards.'})
    while r.c.execute("SELECT COUNT(*) FROM exports WHERE status NOT IN ('complete','needs_review')").fetchone()[0] or r.c.execute('SELECT COUNT(*) FROM exports').fetchone()[0]<3:
        r.exports(total,len(scope['setids']));time.sleep(5)
    r.update(phase='complete_exports_pending' if r.c.execute("SELECT COUNT(*) FROM exports WHERE status='needs_review'").fetchone()[0] else 'complete',finished_at=utc())
