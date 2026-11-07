"""Shared change answers across CLI, MCP and storage adapters."""
import json
import subprocess
import sys
from test_backend_portable import release,SID
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.cli import main
from gitrx_backend.mcp import Server


def test_cli_library_and_mcp_change_answers(release,capsys):
    assert main(['--config',str(release),'--backend','sqlite','version-changes',SID,'2','--limit','1'])==0
    cli=json.loads(capsys.readouterr().out)
    with open_reader(release,'sqlite') as r:
        q=Query(r)
        assert cli==q.version_changes(SID,2,limit=1)
        assert cli['previous_version']==1 and cli['next_offset']==1
        s=Server(r);s.initialized=s.ready=True
        call={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'search_changes','arguments':{'sid':SID,'phrase':'changed','limit':1}}}
        answer=s.dispatch(call)['result']['structuredContent']
        assert answer==q.search_changes(SID,'changed',limit=1)
        assert len(answer['hits'])==1 and answer['continuation']
        second=q.search_changes(SID,'changed',limit=1,cursor=answer['continuation'])
        assert len(second['hits'])==1 and not second['partial']
        assert q.version_changes(SID,1)['baseline']
        assert main(['--config',str(release),'--backend','sqlite','search-changes',SID,'changed','--limit','1'])==0
        assert json.loads(capsys.readouterr().out)==answer


def test_adapters_agree_on_change_locations_and_literals(release):
    answers=[]
    for backend in ('sqlite','git'):
        with open_reader(release,backend) as r:
            q=Query(r);d=q.version_changes(SID,2);s=q.search_changes(SID,'changed')
            answers.append((d['summary'],d['changes'],[(x['previous_version'],x['version'],x['before'],x['after'],x['matches']) for x in s['hits']]))
    assert answers[0]==answers[1]


def test_actual_stdio_change_tools(release):
    requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
              {'jsonrpc':'2.0','method':'notifications/initialized'},
              {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'version_changes','arguments':{'sid':SID,'version':2,'limit':1}}},
              {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'search_changes','arguments':{'sid':SID,'phrase':'changed','limit':1}}}]
    p=subprocess.run([sys.executable,'-B','-m','gitrx_backend.mcp','--config',str(release),'--backend','sqlite'],input=''.join(json.dumps(r)+'\n' for r in requests),capture_output=True,text=True,timeout=20)
    assert p.returncode==0,p.stderr
    replies={r['id']:r for r in map(json.loads,p.stdout.splitlines())}
    assert not replies[2]['result']['isError'] and replies[2]['result']['structuredContent']['previous_version']==1
    assert not replies[3]['result']['isError'] and replies[3]['result']['structuredContent']['hits']
