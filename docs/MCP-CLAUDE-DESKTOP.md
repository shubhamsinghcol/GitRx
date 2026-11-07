GitRx's local stdio server exposes five read-only tools:
`first_occurrence`, `section_first_present`, `show`, `diff`, `coverage`.
It serves the accepted **20-family pilot**, not unclassified tranche1 or an
unbuilt release1. No writes, HTTP access, arbitrary SQL or rule tuning tools.
Existing cached context assignments are read from their pinned snapshot.

Merge this entry into `mcpServers` in
`~/Library/Application Support/Claude/claude_desktop_config.json`, preserving
other servers, then restart Claude Desktop. This block did not modify that
application's configuration or launch an external host session.

```json
{
  "mcpServers": {
    "gitrx-local": {
      "command": "/Users/shubham/Desktop/GitRx/.venv/bin/python",
      "args": [
        "-B", "-m", "gitrx_services.mcp",
        "--data-root", "/Users/shubham/Desktop/GitRx/runtime/current",
        "--query-snapshot", "68f939ed3832fe7ba4a008b67afcb9d1baad6d8323ab27407cc1cbb636e71023"
      ],
      "env": {"PYTHONPATH": "/Users/shubham/Desktop/GitRx"}
    }
  }
}
```

Examples of tool arguments:

```json
{"family":"Levaquin","any_of":["myasthenia gravis"],"sections":["whole-document"],"ignore_case":true,"include_headings":false}
{"family":"AMBIEN","sections":["boxed-warning"],"scope":"full-label"}
{"family":"Levaquin","date":"2010-01-01","sections":["warnings"]}
{"family":"Glucophage","version_A":1,"version_B":2,"sections":["contraindications"]}
{"family":"Levaquin"}
```

Every successful evidence response identifies family/setid, query and source
archive snapshots, citation tuples `(setid, version, raw_sha256)`, explicitly
labeled DailyMed publication/SPL effective/HTTP retrieval dates, route-specific
retrieval failures and a separate unanchored inventory. The envelope moves
dedicated unanchored inventories out of the display result; it preserves
frozen headlines, qualification wording and source paths. Whole-document
literal queries retain all contexts under their native semantics; this is
not a scoped body assertion. Separate instances remain separate.

The stdlib server supports initialize-based protocol versions **2025-11-25**
and **2025-06-18**, newline-delimited UTF-8 JSON-RPC and the initialized
notification. It does not claim modern stateless 2026 support; a client must
support legacy fallback. `server/discover` returns MethodNotFound to permit
that fallback. Tests cover actual subprocess stdio initialization/list/call,
all five tool functions, read-only SQLite, metadata and error responses.
Claude Desktop UI compatibility has not been manually verified. Only protocol
messages go to stdout; process diagnostics use stderr. Invalid tools/arguments
are rejected, and snapshot/source mismatches fail closed.

References: [MCP stdio specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[official Claude Desktop connection guide](https://modelcontextprotocol.io/docs/develop/build-server#testing-your-server-with-claude-for-desktop).
