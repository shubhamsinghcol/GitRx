# Plain label-history browser

The local browser interface is available at **http://127.0.0.1:8766/** while the
GitRx browser service is running. Find a family, choose a version or publication
cutoff, select a source occurrence, compare versions, or look up first-observed
wording. The workflow uses ordinary browser forms and links; no terminal or JSON
is required to use it. No account or remote service is involved.

Dates, missing versions, incomplete text and repeated occurrences appear beside
active results. Source section codes remain occurrence selectors, not interpreted
clinical context. Tables retain cells and valid spans; comparison sections with
tables show the original versions side by side. Where nested layout cannot be
reconstructed, the cell retains its text and displays a local limitation message.
Unavailable images are identified in place. Source HTML/XML is never executed.

Reference controls copy a self-contained snippet containing family, version,
occurrence, publication/effective dates, full release identity, source hash and an
exact local link. It opens that immutable release, not whatever becomes current.
Saved release configurations are retained under `runtime/web/releases`. Reopening
references requires this local service and the retained archive to remain available;
these are local references, not public permanent hosting.

## Operator startup

Installed entry point: `gitrx-web`. From this checkout:

```sh
.venv/bin/python -B -m gitrx_backend.web --config reports/query-limits-20261106/accelerated-config.json --port 8766
```

The service binds loopback only and rejects foreign Host headers. Queries run on
its main thread with existing deadlines; no acquisition/admin endpoints are exposed.
It serves static assets and rendered HTML, using the shared native query library.
The chosen release is fixed at startup. Restart with a new retained configuration
to register another release; existing exact links remain bound to their saved one.
Keep the same port for previously copied links. First startup reads the source
selection to derive per-version extraction status; a hash-checked coverage cache
speeds subsequent starts. Do not remove release configuration/coverage files merely
because a newer release is registered.

This is the local functional interface. It has not been deployed as a public HTTP
service or qualified for internet-facing load. [Verification](../reports/browser-workflow-20261106/REPORT.md).
