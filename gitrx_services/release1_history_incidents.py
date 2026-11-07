"""Incident-scoped acquisition decisions; no classifier quirks or identity aliases."""
from pathlib import Path
import json,os,time
from .release1_http import Stop,hash_file,canonical,atomic,utc

class HistoryIdentityConflict(Exception):pass

def empty_envelope(sid,url,response,body):
    try:
        d=body['data'];m=body['metadata']
        return (response['status']==200 and not response.get('error') and d['spl']['setid']=='' and d['spl'].get('title')=='' and d['history']==[] and int(m['total_elements'])==0 and int(m['total_pages'])==0 and int(m['current_page'])==1 and canonical(m['current_url'])==canonical(url) and canonical(response['url'])==canonical(url) and canonical(response.get('final_url',url))==canonical(url))
    except (KeyError,ValueError,TypeError):return False

def handle(runner,sid,url,response,body):
    runner.t.pacer.identity_mismatch(time.time(),sid,response.get('id'))
    p=runner.root/'incidents/history-identity-conflicts'/f'{sid}.json'
    incident=json.loads(p.read_bytes()) if p.exists() else {}
    approved=(incident.get('cause')=='server_empty_history' and incident.get('requested_setid')==sid and canonical(incident.get('request_url',''))==canonical(url) and empty_envelope(sid,url,response,body))
    if hash_file(response['path'])!=response['sha256']:raise Stop('PROTECTED_HISTORY_HASH_CHANGED')
    dest=runner.root/'quarantine/history'/sid/(response['sha256']+'.json');dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        if hash_file(dest)!=response['sha256']:raise Stop('QUARANTINE_HASH_CHANGED')
    else:os.link(response['path'],dest)
    event={'at':utc(),'requested_setid':sid,'observed_setid':body.get('data',{}).get('spl',{}).get('setid'),'request_url':url,'http_id':response.get('id'),'sha256':response['sha256'],'quarantine_path':str(dest),'cause':'server_empty_history' if approved else 'unexplained_identity_mismatch','admitted_as_history':False}
    with (dest.parent/'observations.jsonl').open('a') as f:f.write(json.dumps(event)+'\n')
    if not approved:raise Stop('HISTORY_IDENTITY_MISMATCH')
    raise HistoryIdentityConflict('Diagnosed server empty history; quarantined metadata only')
