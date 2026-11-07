"""Route-qualified presentation; a failed request never proves global absence."""
import json
from urllib.parse import urlsplit


def route_name(retrieval):
    endpoint=urlsplit(retrieval.get('url','')).path.rsplit('/',1)[-1]
    return endpoint or retrieval.get('kind') or 'recorded route'


def route_failure(version):
    routes=sorted({route_name(r) for r in version.get('retrievals',[])})
    return 'not retrievable by '+(', '.join(routes) or 'an admitted route (no attempt recorded)')+' in this snapshot; untested or unadmitted routes remain unknown'


def family_statuses(conn):
    return {r['setid']:{'label_status':r['label_status'] or 'unknown',
                       'evidence':json.loads(r['evidence_json'] or '[]')}
            for r in conn.execute('''SELECT f.setid,s.label_status,s.evidence_json FROM families f
              LEFT JOIN family_label_status s USING(setid) ORDER BY f.setid''')}


def state_text(state):
    return 'not retrievable by recorded route(s)' if state=='unavailable' else state
