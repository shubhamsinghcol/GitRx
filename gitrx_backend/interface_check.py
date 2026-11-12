"""Interface agreement check: the MCP server and the CLI must give the same answers.

Starts the MCP server as a real subprocess (stdio, the way a client does), lists its tools, runs a few tool
calls, runs the same questions through the CLI, and compares the answers (JSON, after normalisation).  It
exists to catch configuration or transport breakage that unit tests of Query alone cannot (a config the CLI can
open but the MCP server cannot, a tool that is advertised but missing, differing results).

  python -m gitrx_backend.interface_check --config CONFIG              # python -m modules
  python -m gitrx_backend.interface_check --mcp ./label-history-mcp --cli ./label-history   # shipped launchers
"""
import argparse
import json
import subprocess
import sys

EXPECTED_TOOLS = {'describe_release', 'families', 'resolve', 'versions', 'coverage', 'show', 'search', 'compare'}
VOLATILE = {'describe_release': ('freshness',)}   # update-job state is read live and compared against the release under a time budget


def _normalize(tool, value):
    value = json.loads(json.dumps(value, ensure_ascii=False))
    for key in VOLATILE.get(tool, ()):
        if isinstance(value, dict):
            value.pop(key, None)
    return value


def mcp_session(command, calls, timeout=180):
    """Run initialize, tools/list and the calls over stdio. Returns (tool names, [structured answers])."""
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
    for i, (name, arguments) in enumerate(calls):
        requests.append({'jsonrpc': '2.0', 'id': 10 + i, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})
    proc = subprocess.run(command, input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True, text=True,
                          timeout=timeout)
    replies = {}
    for line in proc.stdout.splitlines():
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        replies[reply.get('id')] = reply
    if 1 not in replies or 'result' not in replies[1]:
        raise RuntimeError('MCP server did not initialize (exit %s): %s' % (proc.returncode, proc.stderr.strip()[-500:]))
    names = {t['name'] for t in replies.get(2, {}).get('result', {}).get('tools', [])}
    answers = []
    for i in range(len(calls)):
        result = replies.get(10 + i, {}).get('result')
        answers.append(None if result is None else (result['structuredContent'], result['isError']))
    return names, answers


def cli_args(name, arguments):
    flags = {'limit': '--limit', 'max_chars': '--max-chars', 'section_limit': '--section-limit',
             'max_families': '--max-families', 'time_budget_seconds': '--time-budget-seconds'}
    positional = {'describe_release': [], 'families': [], 'resolve': ['family'], 'versions': ['sid'], 'coverage': ['sid'],
                  'show': ['sid', 'version'], 'search': ['phrase']}[name]
    command = [{'describe_release': 'describe-release'}.get(name, name)]
    command += [str(arguments[k]) for k in positional]
    for key, flag in flags.items():
        if key in arguments and key not in positional:
            command += [flag, str(arguments[key])]
    return command


def cli_call(command, name, arguments, timeout=180):
    proc = subprocess.run(command + cli_args(name, arguments), capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError('CLI %s failed (%s): %s' % (name, proc.returncode, proc.stderr.strip()[-300:]))
    return json.loads(proc.stdout)


def run(mcp_command, cli_command, phrase='warning'):
    """Returns a list of (name, ok, detail) checks."""
    checks = []
    first = [('describe_release', {}), ('families', {'limit': 3}),
             ('search', {'phrase': phrase, 'limit': 2, 'max_families': 400, 'time_budget_seconds': 30})]
    names, answers = mcp_session(mcp_command, first)
    checks.append(('mcp lists the expected tools', EXPECTED_TOOLS <= names, 'missing: %s' % sorted(EXPECTED_TOOLS - names)))
    hit = None
    if answers[2] and answers[2][0].get('hits'):
        hit = answers[2][0]['hits'][0]
    checks.append(('search found a readable family for the follow-up calls', hit is not None, phrase))
    plan = list(first)
    if hit:
        sid, version = hit['setid'], hit['version']
        follow = [('resolve', {'family': sid}), ('versions', {'sid': sid, 'limit': 3}), ('coverage', {'sid': sid}),
                  ('show', {'sid': sid, 'version': version, 'max_chars': 1500, 'section_limit': 2})]
        _, more = mcp_session(mcp_command, follow)
        plan += follow
        answers = answers + more
    for (name, arguments), answer in zip(plan, answers):
        label = 'MCP == CLI: %s' % name
        if answer is None:
            checks.append((label, False, 'no MCP reply'))
            continue
        structured, is_error = answer
        if is_error:
            checks.append((label, False, 'MCP returned an error: %s' % json.dumps(structured.get('error'))[:200]))
            continue
        try:
            cli = cli_call(cli_command, name, arguments)
        except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            checks.append((label, False, str(exc)[:300]))
            continue
        same = _normalize(name, structured) == _normalize(name, cli)
        checks.append((label, same, '' if same else 'answers differ'))
        if name == 'describe_release':
            checks.append(('freshness block present on both interfaces',
                           'freshness' in structured and 'freshness' in cli, ''))
    return checks


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', help='configuration for the python -m launchers (default: the launchers choose)')
    p.add_argument('--mcp', nargs='+', help='MCP launcher command (default: python -m gitrx_backend.mcp)')
    p.add_argument('--cli', nargs='+', help='CLI launcher command (default: python -m gitrx_backend.cli)')
    p.add_argument('--phrase', default='warning')
    a = p.parse_args(argv)
    extra = ['--config', a.config] if a.config else []
    mcp = a.mcp or [sys.executable, '-B', '-m', 'gitrx_backend.mcp'] + extra
    cli = a.cli or [sys.executable, '-B', '-m', 'gitrx_backend.cli'] + extra
    checks = run(mcp, cli, a.phrase)
    for name, ok, detail in checks:
        print('%s  %s%s' % ('PASS' if ok else 'FAIL', name, '  ' + detail if detail and not ok else ''))
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == '__main__':
    sys.exit(main())
