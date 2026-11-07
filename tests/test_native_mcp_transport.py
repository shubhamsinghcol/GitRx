"""Native transport resilience with temporary fixtures and subprocess clients."""
import json
import subprocess
import sys

from test_backend_portable import release
from gitrx_backend.mcp import Server, encode_response, MAX_RESPONSE_BYTES
from gitrx_backend.config import open_reader


def test_invalid_unicode_and_deep_request_leave_server_usable(release):
    with open_reader(release) as reader:
        server = Server(reader)
        for value in ('\ud800', '\udfff'):
            response = server.dispatch({'jsonrpc':'2.0','id':1,'method':'ping','params':{'x':value}})
            assert response['error']['code'] == -32600
        nested = None
        for _ in range(70): nested = [nested]
        assert server.dispatch({'jsonrpc':'2.0','id':2,'method':'ping','params':{'x':nested}})['error']
        assert server.dispatch({'jsonrpc':'2.0','id':3,'method':'ping'})['result'] == {}


def test_full_wire_size_limit_is_explicit_non_evidence_error():
    answer = {'snapshot':'f'*64, 'sections':[{'title':'x'*MAX_RESPONSE_BYTES}], 'citation':{'raw_sha256':'a'*64}}
    response = {'jsonrpc':'2.0','id':9,'result':{'structuredContent':answer}}
    raw = encode_response(response)
    result = json.loads(raw)['result']
    assert len(raw.encode()) <= MAX_RESPONSE_BYTES
    assert result['isError'] and result['structuredContent']['evidence_answer'] is False
    assert result['structuredContent']['snapshot'] == 'f'*64
    assert result['structuredContent']['error']['type'] == 'ResponseTooLarge'
    # Ordinary Unicode and even invalid local-data scalars remain valid wire JSON.
    assert json.loads(encode_response({'result':{'text':'α\ud800'}}))['result']['text'] == 'α\ud800'


def test_real_stdio_survives_bad_unicode_and_nesting(release):
    requests = [
        {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
        {'jsonrpc':'2.0','method':'notifications/initialized'},
        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'search','arguments':{'phrase':'\ud800'}}},
        {'jsonrpc':'2.0','id':3,'method':'ping'},
    ]
    wire = ''.join(json.dumps(r)+'\n' for r in requests)
    wire += '{"jsonrpc":"2.0","id":4,"method":"ping","params":'+ '['*2000+'0'+']'*2000+'}\n'
    wire += json.dumps({'jsonrpc':'2.0','id':5,'method':'ping'})+'\n'
    p = subprocess.run([sys.executable,'-B','-m','gitrx_backend.mcp','--config',str(release)],
                       input=wire,capture_output=True,text=True,timeout=10)
    assert p.returncode == 0, p.stderr
    replies = [json.loads(line) for line in p.stdout.splitlines()]
    assert len(replies) == 5
    assert replies[1]['error'] and replies[2]['id'] == 3
    assert replies[3]['error'] and replies[4]['id'] == 5
