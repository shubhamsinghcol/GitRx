"""Local read-only MCP stdio server over the accepted frozen query snapshot."""
from pathlib import Path
import argparse, copy, json, re, sys

from .io import readonly, retrieved_by
from .schema import errors
from gitrx.current import query, tooling, section_history
from gitrx.current.availability import route_failure

PROTOCOLS = ('2025-11-25', '2025-06-18')
SCOPE = {'type': 'string', 'enum': ['full-label', 'highlights', 'both']}
STR = {'type': 'string', 'minLength': 1, 'maxLength': 5000}
STRINGS = {'type': 'array', 'items': STR, 'minItems': 1, 'maxItems': 50, 'uniqueItems': True}

def tool_schemas():
    common = {'family': {'type': 'string', 'minLength': 1, 'maxLength': 200}}
    scoped = common | {'scope': SCOPE, 'sections': STRINGS, 'any_of': STRINGS, 'ignore_case': {'type': 'boolean'}}
    defs = [
        ('first_occurrence', scoped | {'include_headings': {'type': 'boolean'}}, ['family', 'any_of'], 'Literal phrase history, any-of alternatives; frozen fail-closed scope semantics.'),
        ('section_first_present', scoped, ['family', 'sections'], 'Structural section history; optional any-of literal presence is separately labeled.'),
        ('show', scoped | {'date': {'type': 'string', 'format': 'date'}}, ['family', 'date'], 'DailyMed-published record observed as of a labeled publication date.'),
        ('diff', scoped | {'version_A': {'type': 'integer', 'minimum': 1}, 'version_B': {'type': 'integer', 'minimum': 1}}, ['family', 'version_A', 'version_B'], 'Section-level word diff; pairing is not durable section identity.'),
        ('coverage', common, ['family'], 'Listed/retrieved/partial counts for one family in the pinned pilot snapshot.')]
    return [{'name': n, 'description': d, 'inputSchema': {'type': 'object', 'properties': p, 'required': req, 'additionalProperties': False},
             'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}} for n, p, req, d in defs]

def separate_unanchored(value):
    """Move dedicated unanchored inventories to a labeled block; retain qualifiers."""
    value = copy.deepcopy(value)
    block = []
    def walk(x, path='$'):
        if isinstance(x, dict):
            for k in list(x):
                if k in ('unclassified_context', 'unanchored_results'):
                    if x[k]: block.append({'source_path': path + '.' + k, 'items': x[k]})
                    del x[k]
                else: walk(x[k], path + '.' + k)
        elif isinstance(x, list):
            for i, child in enumerate(x): walk(child, f'{path}[{i}]')
    walk(value)
    return value, block

class Archive:
    def __init__(self, root, snapshot=None):
        self.root = Path(root).resolve()
        self.c = readonly(self.root / 'data/gitrx.sqlite')
        self.snapshot = snapshot or (self.root / 'reports/QUERY-SNAPSHOT-ID.txt').read_text().strip()
        self.manifest, self.archive = query.load_snapshot(self.c, self.snapshot)
        self.schemas = {x['name']: x for x in tool_schemas()}

    def envelope(self, family, sid=None):
        versions = [v for v in self.archive['versions'] if v['setid'] == sid]
        return {'archive_snapshot_id': self.manifest['archive_snapshot'], 'query_snapshot_id': self.snapshot,
            'family_name': family, 'setid': sid, 'rules_tag': 'gitrx-rules-v1.0.1',
            'citations': [{'setid': sid, 'version': v['version'], 'raw_sha256': v['selected_sha256']} for v in versions if v['state'] == 'downloaded'],
            'dates': [{'setid': sid, **tooling.date_info(v)} for v in versions],
            'date_types': {'DailyMed_publication_dates': 'Observed DailyMed listing publication dates; not legal or FDA approval dates', 'SPL_effective_date': 'SPL XML effectiveTime; distinct from publication', 'retrieved_at': 'HTTP observation timestamp'},
            'retrieval_failures': [{'version': v['version'], 'reason': route_failure(v)} for v in versions if v['state'] != 'downloaded'],
            'unanchored_results': [], 'release_status': 'local; independent blind validation pending; no public release'}

    def call(self, name, args):
        if not isinstance(name, str) or name not in self.schemas:
            raise ValueError('Unknown tool')
        errs = errors(args, self.schemas[name]['inputSchema'])
        if errs:
            raise ValueError('; '.join(errs))
        sid, title = tooling.family_id(self.c, args['family'])
        env = self.envelope(title, sid)
        sections = args.get('sections', ['whole-document'])
        scope = args.get('scope', 'full-label')
        phrases = args.get('any_of', [])
        ignore = args.get('ignore_case', False)
        if name == 'first_occurrence':
            result = [query.first_occurrence(self.c, self.snapshot, sid, s, phrases, scope, ignore, args.get('include_headings', True)) for s in sections]
        elif name == 'section_first_present':
            result = [{'structural_existence': section_history.section_first_present(self.c, self.snapshot, sid, s, scope),
                       'literal_any_of_presence': query.first_occurrence(self.c, self.snapshot, sid, s, phrases, scope, ignore) if phrases else None} for s in sections]
        elif name == 'show':
            result = tooling.show(self.c, self.root, sid, args['date'], snapshot=self.snapshot, scope=scope, sections=sections, phrases=phrases, ignore_case=ignore)
        elif name == 'diff':
            result = tooling.diff(self.c, self.root, sid, args['version_A'], args['version_B'], snapshot=self.snapshot, scope=scope, sections=sections, phrases=phrases, ignore_case=ignore)
        else:
            versions = [v for v in self.archive['versions'] if v['setid'] == sid]
            nums = {v['version'] for v in versions}
            result = {'listed_versions': len(versions), 'retrieved_versions': sum(v['state'] == 'downloaded' for v in versions),
                'partial_parses': sum(p['status'] == 'partial' for v in versions for p in v['parse_runs']),
                'archive_route_versions': sum(retrieved_by(v, 'archive_route') for v in versions),
                'unlisted_version_numbers': sorted(set(range(1, max(nums) + 1)) - nums),
                'family_label_status': self.manifest['family_label_status'][sid]}
        env['result'], env['unanchored_results'] = separate_unanchored(result)
        return env

class Server:
    def __init__(self, archive):
        self.archive = archive
        self.negotiated = False
        self.ready = False

    def dispatch(self, request):
        ident = request.get('id') if isinstance(request, dict) else None
        def error(code, message):
            return {'jsonrpc': '2.0', 'id': ident, 'error': {'code': code, 'message': message}}
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str) or ('id' in request and type(ident) not in (str, int)):
            return error(-32600, 'Invalid request')
        method = request['method']
        if 'id' not in request:
            if method == 'notifications/initialized' and self.negotiated:
                self.ready = True
            return None
        params = request.get('params', {})
        if not isinstance(params, dict): return error(-32602, 'Params must be an object')
        if method == 'server/discover': return error(-32601, 'Legacy initialize-based stdio server; server/discover not supported')
        if method == 'ping': result = {}
        elif method == 'initialize':
            if self.negotiated: return error(-32600, 'Already initialized')
            if not isinstance(params.get('protocolVersion'), str): return error(-32602, 'protocolVersion required')
            self.negotiated = True
            result = {'protocolVersion': params['protocolVersion'] if params['protocolVersion'] in PROTOCOLS else PROTOCOLS[0],
                'capabilities': {'tools': {'listChanged': False}}, 'serverInfo': {'name': 'gitrx-local', 'version': '0.1.0'},
                'instructions': 'Read-only publication history; citations/date types/uncertainty are mandatory. No clinical or causal inference. Independent blind validation pending.'}
        elif not self.ready: return error(-32002, 'Initialize and send notifications/initialized first')
        elif method == 'tools/list': result = {'tools': list(self.archive.schemas.values())}
        elif method == 'tools/call':
            name, args = params.get('name'), params.get('arguments', {})
            if not isinstance(name, str) or name not in self.archive.schemas: return error(-32602, 'Unknown tool')
            try:
                env = self.archive.call(name, args)
                result = {'content': [{'type': 'text', 'text': json.dumps(env, ensure_ascii=False)}], 'structuredContent': env, 'isError': False}
            except (ValueError, RuntimeError) as exc:
                family = args.get('family', 'unresolved') if isinstance(args, dict) else 'unresolved'
                sid = None
                if isinstance(family, str):
                    try: sid, family = tooling.family_id(self.archive.c, family)
                    except ValueError: pass
                else: family = 'unresolved'
                env = self.archive.envelope(family, sid)
                env['error'] = {'type': type(exc).__name__, 'message': str(exc)}
                result = {'content': [{'type': 'text', 'text': json.dumps(env, ensure_ascii=False)}], 'structuredContent': env, 'isError': True}
        else: return error(-32601, 'Method not found')
        return {'jsonrpc': '2.0', 'id': ident, 'result': result}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-root', type=Path, default=tooling.DEFAULT_ROOT)
    p.add_argument('--query-snapshot')
    a = p.parse_args()
    server = Server(Archive(a.data_root, a.query_snapshot))
    try:
        for line in sys.stdin:
            try:
                if len(line.encode()) > 1024**2: raise ValueError('Request exceeds 1 MiB')
                request = json.loads(line)
                response = server.dispatch(request)
            except (ValueError, TypeError):
                response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON or oversized request'}}
            if response is not None:
                print(json.dumps(response, ensure_ascii=False, separators=(',', ':')), flush=True)
    finally:
        server.archive.c.close()

if __name__ == '__main__':
    main()
