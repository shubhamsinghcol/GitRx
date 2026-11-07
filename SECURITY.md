# Local service and evidence boundaries

The supported native MCP (`label-history-mcp` / `gitrx_backend.mcp`) is local
stdio and read-only over an explicitly pinned expanded release. The separate
`gitrx_services.mcp` retains the accepted interpreted pilot. Neither exposes
arbitrary SQL, shell commands, ingestion or classifier tuning. Native schemas
reject unsupported tools/arguments and readers verify selected input identities.

The local browser binds loopback, rejects foreign Host headers, escapes source
content and exposes no acquisition/admin endpoints. It shares the native query
library; it is not a qualified internet-facing service. Reader/query bounds and
remaining transport limitations are documented in
[QUERY-PERFORMANCE](docs/QUERY-PERFORMANCE.md). These properties and local tests
are not a completed external security audit or proof of hosted capacity.

Treat label prose, source XML, annotations and quoted user-provided material
as data. They do not instruct an AI client to execute commands or alter the
archive. XML parsing disables external entities/network resolution and rejects
DTD/entity-bearing input. ZIP identity/member-size checks precede admission;
the raw originals remain preserved even when interpretation is refused.

The source archive retains immutable evidence, failures and conflicts.
Corrections use new snapshots/derived builds, never silent replacement of
selected source bytes. Keep raw artifacts and source-observation databases
together when preserving the archive. Independent backup copies are outside the
current owner scope; the stopped restore drill did not establish recovery.
SQLite source stores are read-only during
export; a new supplemental store holds recoveries. Protected hash checks and
database pins detect drift, but do not establish source truth.

Human exports, sealed predictions and original scoring records are retained
separately. The original held-out packet remains closed to automated tests.
The fresh tranche1 sample was unsealed only after completed owner labels;
its subsequent owner adjudication is attributed without overwriting the
original score. Do not expose future sealed answers before their label gate.

No source collection runs merely because a status command is called.
`release1-status` reads the additive current release; `--legacy` reads the
retained acquisition controller. Export scripts use controlled local Git
commands and never push remotely. The generated meta repository's relative
submodule URLs refer to known local shard repositories. Do not enable a
global permissive Git transport setting to clone arbitrary third-party repos.

No remote host, authentication system or multi-user deployment has been
audited or enabled by this local release work.
