"""Readable presentation only; identities, evidence and query semantics are fixed."""
import re


def readable(value, **_):
    if isinstance(value, dict):
        return '; '.join(k.replace('_',' ')+': '+readable(v) for k,v in value.items()) or 'none'
    if isinstance(value,(list,tuple)):
        return ', '.join(readable(v) for v in value) or 'none'
    if value is None: return 'not recorded'
    if isinstance(value,bool): return 'yes' if value else 'no'
    return str(value)


def family_title(title):
    return re.sub(r'\s*[-—(]?\s*\bhistorical\b\s*\)?\s*', ' ', title, flags=re.I).strip(' -—')


def status_text(status):
    text=status.get('label_status','unknown')
    evidence=status.get('evidence',status.get('evidence_json',[]))
    return text+'; '+('status evidence: '+readable(evidence) if evidence else 'no explicit active/archived status evidence')


def retrieval_text(route):
    return (f"{route['wording']}; observed {route['retrieved_at']}; {route['url']}; "
            f"raw SHA-256 {route['raw_sha256']}; observation {route['retrieval_id']}")


def coverage_text(result):
    numbers=sorted(v['version'] for v in result['versions'])
    missing=sorted(set(range(1,max(numbers)+1))-set(numbers))
    c=result['coverage']
    text=f"{c['downloaded']} of {c['listed']} listed versions; {len(missing)} unlisted version numbers within 1-{max(numbers)}"
    if missing:text+=' ('+', '.join(map(str,missing))+')'
    extra={k:v for k,v in c.items() if k not in ('downloaded','listed')}
    return text+'; '+readable(extra)
