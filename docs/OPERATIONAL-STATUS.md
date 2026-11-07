# Observed release-cycle status

`./release-cycle status` reports the selected immutable snapshot/configuration,
backend and presence of pinned evidence metadata/search acceleration, recent local
operations, and individually recorded family checks. It does not contact DailyMed,
refresh content, create a journal, select a release or install a scheduler.
The serving profile describes the job-selected configuration; independently running
clients and explicitly selected configurations may differ.
Operation mode is explicitly **manual**. A workspace without recorded operations
reports that fact rather than deriving a freshness date from file timestamps.

Actions append observations to a separate `operations.sqlite` operational journal.
This is not a source-evidence store and is not included in a release's evidence
identity. Existing network ledgers and failed response bodies remain unchanged.
Each refresh records attempted families, two agreeing complete inventories, then
candidate acquisition outcomes or no-change results. Last successful agreeing
inventory time remains available after later failures. An inventory check that
finds no new candidates can advance that family's check time without creating a
new content snapshot. A successful inventory is not proof that later ZIP downloads
succeeded. Invalid/empty/inconsistent inventories do not advance successful time.

Only checked families are verified. If a later family fails, earlier successful
checks remain recorded and the remaining scope is counted as unattempted. A
successful check of one family never marks the whole supported corpus fresh.
The status shows at most 200 latest family rows and reports total/truncation;
recent operations are bounded to 20. The journal retains earlier observations.
No national/subnational stale thresholds or freshness SLA are inferred.

Candidate snapshot/job identity and last recorded build phase (payloads, Git export,
metadata, search projection, reader verification or selection) remain visible even
when the build fails or is interrupted. These are phase observations, not a worker
heartbeat.

Cycle/build and activation outcomes are distinct: no-change, quarantined, failed,
retained, promoted and selected are reported from actual results. An operation
without a finished result is indeterminate (for example a terminated
process), not proof that a worker is still running or that selection occurred.
Always inspect the current selected snapshot after interruption. Individual
build checkpoint and quarantine files remain retained in their job directories.

## Optional search projection during release staging

A job configuration can set `"search_projection": true` to prepare the literal
text accelerator for each changed candidate. `false` disables preparation.
When omitted, a parent's configured `search_index` causes the changed release to
prepare its own projection. An unaccelerated parent remains unaccelerated unless
explicitly enabled. `status.serving_profile.search_accelerated` describes the
selected configuration, not a performance guarantee.

The new projection is built in a separate staging directory, verified against
its own hash, release snapshot and Unicode policy, and only then included in the
candidate configuration before selection. A retained completed projection can be
reused after verification on restart. Interrupted staging directories remain
retained; no old release projection is reused for new evidence. The requested
build profile is frozen in the job's build inputs. Changing this setting does not
rebuild or reselect a completed unchanged evidence job automatically.

These operations do not alter the current manually operated freshness promise.
No live acquisition or release promotion is performed merely to inspect status.
