# Full DailyMed work started — November 7, 2026

> Latest: [completed step-1 measurements and consolidated storage budget](FULL-DAILYMED-PREPARATION-RESULT.md). Earlier entries below retain their planning context.

The owner's November 7 request supersedes the older roadmap's prohibition on
scope expansion. Local acquisition is authorized; public upload, bucket creation,
and recurring fresh collection have not been performed.

## Verified capacity and scope

The internal data volume has 147 GiB free (about 158 GB). The external APFS SSD
`/Volumes/Untitled` has 517 GiB free (about 555 GB). GitRx occupies 158 GiB by `du`,
plus a separate 23 GiB functional-recovery directory. Apparent sizes are not a
physical allocation accounting on APFS; neither proves 300 GB of unique scraped
DailyMed bytes. Inside GitRx, cohorts account for 116 GiB; reports, exports, data,
and work also contain retained builds and intermediate representations.
Nothing was deleted or moved from the retained archive.

160 GB free is not a safe entire-corpus build budget. Even the older human-Rx
estimate exceeded it. The external SSD is appropriate for bounded discovery and
pilot acquisition, but full historical capacity is still unqualified.

The live unfiltered `/services/v2/spls.json?pagesize=100&page=1` response reported
158,967 entries / 1,590 pages. This is a current listing count, not a completed
unique-setid census or a historical denominator. Archived-only families,
history lengths, media and category distributions require separate measurement.

## Work running now

A finite, checkpointed **current listing census** runs on the SSD at:

`/Volumes/Untitled/GitRx/full-dailymed-20261107/`

`status.json` reports saved pages, observed families and the reported denominator.
`network/ledger.sqlite` preserves request observations and content hashes;
`network/bodies` retains exact response bodies. `census.sqlite` transactionally
commits each page and its families together. `manifest.json` is written only after
all pages and counts agree. This is an observation interval, not an atomic upstream
snapshot; same-count upstream mutations cannot be excluded.

The maintained HTTP client is reused with sequential two-second request spacing,
a persisted 10,000-request lifetime cap, original response bytes, hash-checked reuse,
streaming space checks and persisted Retry-After deferral. A 20 GiB reserve applies.
Duplicate setids, shifted page metadata, missing/corrupt saved bodies and a changed
configuration stop the job. Start a new workspace for a changed listing; do not
edit saved pages or remove evidence to force completion.

This job does **not** download every historical ZIP, classify new categories,
build a new Git release, promote a selection or publish data. Those stages need
an explicit frozen scope and a measured category/history sample. The existing
release-cycle deliberately refuses unknown families and must be extended through
reviewed admission, rather than bypassing its guard.

## Sleep, shutdown and resumption

The runner is installed in `~/Library/Application Support/GitRx/`; its source
package is a local installed copy, so editing the checkout does not mutate the
running census. The login LaunchAgent is
`~/Library/LaunchAgents/org.gitrx.full-dailymed-census.plist`.
It starts this same finite job at login, with no KeepAlive loop or calendar schedule.
After completion, subsequent starts verify saved page hashes and make no requests.
If the SSD is missing at login, the launcher exits; attach it and resume manually.
A marker and device/mount checks prevent fallback writes to a plain laptop directory.

`caffeinate -i` prevents idle sleep while running. It does not prevent closed-lid
sleep, power loss or shutdown. During sleep the job pauses. Interrupted transfers
are retained as partials and never reused as completed responses. On restart,
SQLite recovers its committed transactions, page hashes are checked and completed
responses can be reused before an uncommitted page is retried. This is crash
resumption, not a guarantee against hardware failure or unsafe SSD removal.
The interruption of a response may require downloading that response again.

Resume after a stopped process (or after attaching an SSD missed at login):

```sh
launchctl kickstart gui/$(id -u)/org.gitrx.full-dailymed-census
```

Pause at a page boundary by creating `STOP` in the SSD workspace. Remove `STOP`
and run the resume command to continue. Logs are `census.log` and
`census-error.log` in Application Support/GitRx. To unload the login launcher:

```sh
launchctl bootout gui/$(id -u)/org.gitrx.full-dailymed-census
```

## Distribution decisions

Keep software source separate from generated history repositories and raw evidence.
Keep existing sixteen immutable shards as they are. For a future full-scope release,
measure the first-hex buckets and subdivide oversized ones by another setid nibble;
do not assume sixteen is the final count or rewrite old refs. New prefix routing
needs shared exporter/installer/reader tests before adoption. Target comfortably
below 1 GiB packed per new repository and measure individual push payloads;
GitHub enforces a 2 GiB single-push cap. A large aggregate collection is not
qualified merely because each repository fits.

Use R2 Standard as a candidate for content-addressed research evidence, chunked
packages and downloadable Git bundles. Bundle manifests should pin object SHA-256,
byte sizes, setid/version provenance, release IDs and optional prefix selection.
A downloader should verify and resume each object; do not require one huge browser
download or rebuild identical releases for every family batch. Avoid uploading
reproducibility duplicates. No R2 bucket or GitHub data repositories were created.

Official R2 pricing checked November 7: $0.015/GB-month for Standard, first 10
GB-month free, no Internet egress charge; request charges still apply. At 500 GB
stored continuously, storage alone would be about $7.35/month; 1 TB about $14.85.
These are scenarios, not an archive-size forecast.

Sources:
- https://dailymed.nlm.nih.gov/dailymed/webservices-help/v2/spls_api.cfm
- https://dailymed.nlm.nih.gov/dailymed/spl-resources-all-drug-labels.cfm
- https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits
- https://developers.cloudflare.com/r2/pricing/

## Required next stages

1. Finish and validate this census; reconcile current setids against bulk release
   inventories and capture an explicit archived-only discovery frame separately.
2. Sample histories and ZIP sizes by source document type/category using maintained
   history agreement, archive fallback, identity validation and parser components.
   Count unknown histories, partial parses and failures separately. Reuse old
   evidence through verified source pointers; do not copy the whole old workspace.
3. Forecast unique evidence, parsed content, Git packs and peak staging on the SSD.
   Persist finite request/byte/time budgets before broad ZIP acquisition.
4. Add scope admission and durable batch orchestration to shared release-cycle
   machinery, with crash/SSD-loss/quarantine regression fixtures. Candidate builds
   stay separate from the existing selected release.
5. Qualify new categories, prefix routing, portable manifests and resumed downloads;
   measure one representative deterministic build twice before distribution.

Focused census/release-cycle tests cover resumed page checkpoints, corrupt saved
bodies, duplicate pagination, malformed identities, changed-release restart,
quarantine and Retry-After surviving client restart. Physical power-loss and
unplug tests have not been performed. Historical held-out answers were not opened.

## Observed restart check

The login runner initially could not read its Desktop script. It was moved to
Application Support with an installed local wheel and lxml 6.1.3; the replacement
launch was observed running and saving new pages. Initial errors remain in the
append-only error log and do not describe the later running process.

A live forced-process termination was performed after 12 pages / 1,200 families.
SQLite `quick_check` returned `ok`. Restart via launchctl subsequently reached
23 pages / 2,300 families. This verifies this finite census resumed after a killed
process; it is not a physical shutdown or unplug test, nor qualification of future
ZIP backfill/build stages. Eleven focused tests passed in the final local run.
The new census tests are included in CI configuration; no remote CI run was started.

## Capacity explanation and census anomaly repair

A later storage inspection measured the original release1 workspace at 110 GiB:
about 41 GiB of reproducible parse-spool intermediates, 24 GiB network storage,
40 GiB additional tranche storage and 4.8 GiB intermediate exports (hard links
make independent directory sums unsuitable for physical-byte accounting).
The separate Desktop functional-recovery directory measures 23 GiB. These are
cleanup candidates only after retained-evidence and active-reference checks;
no deletion was performed.

Scaling the handoff's ~25.1 GB network store and ~2.60 GB single packed Git release
by 158,967/7,239 gives an illustrative ~608 GB combined, excluding parsed runtime
storage and staging. This is not a measured full-category forecast and excludes
unknown archived-only scope. A 1–2 TB working-storage allowance is a provisional
planning envelope, not a sufficiency guarantee.

The census stopped at page 1,351 because official listing rows included negative
`spl_version` values. Discovery now preserves those exact rows and reports their
count; it does not admit negative-version ZIPs or change identity checks. Four
focused census tests passed. The installed census module was updated with its
before/after hashes recorded locally, then the finite job resumed beyond page
1,355 (135,500 entries, 18 nonpositive listed versions). Prior error records remain.
