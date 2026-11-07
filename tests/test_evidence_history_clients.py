"""Client agreement for uncertainty metadata and complete literal histories."""
import json
import subprocess
import sys
from test_backend_portable import release, SID
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.cli import main
from gitrx_backend.mcp import Server


def test_cli_mcp_library_phrase_history_and_metadata_agree(release,capsys):
    args=['--config',str(release),'--backend','sqlite','phrase-history',SID,'changed','--limit','1']
    assert main(args)==0
    cli=json.loads(capsys.readouterr().out)
    with open_reader(release,'sqlite') as reader:
        q=Query(reader)
        assert cli==q.phrase_history(SID,'changed',limit=1)
        assert q.versions(SID)['versions'][0]['evidence']['extraction']['status']=='unknown'
        coverage=q.coverage(SID)
        assert coverage['inventory']['status']=='unknown'
        assert 'integer positions' in coverage['missing_version_definition']
        server=Server(reader);server.initialized=server.ready=True
        call={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'phrase_history','arguments':{'sid':SID,'phrase':'changed','limit':1}}}
        assert server.dispatch(call)['result']['structuredContent']==cli
        call['params']['arguments']={'sid':SID,'phrase':'changed','cursor':cli['continuation']}
        result=server.dispatch(call)['result']
        assert not result['isError'] and result['structuredContent']['observations'][0]['version']==2
        call['params']['arguments']['phrase']='different'
        result=server.dispatch(call)['result']
        assert result['isError'] and result['structuredContent']['evidence_answer'] is False


def test_real_stdio_discovers_and_pages_phrase_history(release):
    requests=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
              {'jsonrpc':'2.0','method':'notifications/initialized'},
              {'jsonrpc':'2.0','id':2,'method':'tools/list'},
              {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'phrase_history','arguments':{'sid':SID,'phrase':'warning','limit':1}}}]
    p=subprocess.run([sys.executable,'-B','-m','gitrx_backend.mcp','--config',str(release),'--backend','sqlite'],
        input=''.join(json.dumps(r)+'\n' for r in requests),text=True,capture_output=True,timeout=10)
    assert p.returncode==0,p.stderr
    replies=[json.loads(s) for s in p.stdout.splitlines()]
    assert 'phrase_history' in [t['name'] for t in replies[1]['result']['tools']]
    answer=replies[2]['result']['structuredContent']
    assert answer['partial'] and answer['continuation'] and len(answer['observations'])==1
    assert answer['observations'][0]['literal_presence']=='present'
