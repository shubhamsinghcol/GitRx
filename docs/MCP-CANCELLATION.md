# Responsive native MCP cancellation

Native 0.9.0 keeps protocol control in the parent process and runs tools and
resource reads in one separate main-thread query process. This preserves the
existing POSIX query deadlines and SQLite connection ownership. No network or
write tool is added. Both readers use one configuration frozen before startup;
restarts cannot silently follow a changed current-release pointer.

Pings, initialization, discovery lists and prompt requests can be answered while
a query is active. Responses may arrive out of request order: clients must match
JSON-RPC IDs rather than response positions. Tools/resource reads run serially;
at most 32 additional queries wait. Excess work receives an explicit queue-full
error. Message and full-response byte bounds remain in force.

Send `notifications/cancelled` with `params.requestId` to cancel active or queued
work. Cancelled calls produce no late result. Queued calls are removed. Active
work receives a cooperative signal that unwinds query timers and Git cleanup,
then its reader process is restarted to avoid reusing interrupted cache state.
If cancellation cannot unwind promptly, the worker is terminated and its tracked
Git process groups are removed. Pings remain responsive during that grace period.
A worker failure produces a non-evidence error for the affected uncancelled call;
queued work can resume on the same frozen release. Repeated startup failures are
bounded rather than retried forever.

Closing stdin drains accepted uncancelled requests, supporting ordinary piped
clients. Broken output pipes stop workers; output stalled after stdin closes is
bounded. Output buffering and input/query queues are bounded. The parent and query
worker each retain a reader, so memory/cold-start cost can exceed the former
single-process server. This is not a qualified multi-tenant remote service.

The native runtime remains POSIX/macOS/Linux. Cancellation and signal delivery
are not hard real-time guarantees; native code may delay cooperative handling.
The dedicated-process fallback bounds that delay without treating cancellation
as a successful empty evidence answer. In-process `Server.dispatch` remains a
synchronous helper; responsive dispatch is provided by the stdio transport.
