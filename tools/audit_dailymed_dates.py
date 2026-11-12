"""Audit DailyMed date fields and document identity across a GitRx rebuild (read-only).

Usage: python -I tools/audit_dailymed_dates.py "$GITRX_DATA_ROOT/rebuild/spl-sections-0.2.4__srlc-changes-0.3.2"
Synthetic fixture families are excluded. Output: JSON counts quoted in docs/SOURCE-DATA-ISSUES.md.
"""
import sqlite3, glob, json, sys, statistics
from datetime import date
R=sys.argv[1]
c=dict(versions=0,real=0,no_pub=0,no_eff=0,inversion=0,inv_fams=set(),eff_after_pub=0,eff_after_pub_30=0,
       setid_mismatch=0,version_mismatch=0,pub_before_eff_365=0,same_pub_multi=0)
lags=[]
def d(s):
    if not s: return None
    s=s.replace('-','')
    try: return date(int(s[:4]),int(s[4:6]),int(s[6:8]))
    except Exception: return None
for f in sorted(glob.glob(R+'/shards/*.sqlite')):
    db=sqlite3.connect('file:%s?mode=ro'%f,uri=True)
    prev=None
    for setid,ver,pub,eff,ps,pv,st in db.execute("select setid,version,pub_date,effective_date,parsed_setid,parsed_version,status from versions where setid not like 'a0000000%' and setid not like '00000000-0000%' order by setid,version"):
        c['versions']+=1
        p,e=d(pub),d(eff)
        if p is None: c['no_pub']+=1
        if e is None: c['no_eff']+=1
        if st!='no_raw':
            c['real']+=1
            if ps and ps!=setid: c['setid_mismatch']+=1
            if pv is not None and pv!=ver: c['version_mismatch']+=1
        if p and e:
            lag=(p-e).days; lags.append(lag)
            if lag<0: c['eff_after_pub']+=1
            if lag<-30: c['eff_after_pub_30']+=1
            if lag>365: c['pub_before_eff_365']+=1
        if prev and prev[0]==setid and p and prev[1] and p<prev[1]:
            c['inversion']+=1; c['inv_fams'].add(setid)
        prev=(setid,p)
    db.close()
c['inv_fams']=len(c['inv_fams'])
lags.sort()
c['pub_minus_eff_days']={'n':len(lags),'median':statistics.median(lags),'p90':lags[int(.9*len(lags))],'p99':lags[int(.99*len(lags))],'gt90':sum(l>90 for l in lags),'gt365':sum(l>365 for l in lags)}
print(json.dumps(c,indent=1))
