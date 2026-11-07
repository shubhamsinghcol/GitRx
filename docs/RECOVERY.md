# Evidence recovery and retention

`gitrx-recovery` creates a content-addressed local backup, audits it and restores
into a new directory. Raw objects, source observations, failed-response ledgers,
manifests and selected releases belong in the recovery plan. Derived indexes are
convenient restore accelerators, not replacements for source evidence.

## Storage and retention policy

Keep source evidence, frozen releases and their manifests indefinitely unless the
owner explicitly authorizes a scoped deletion. Never overwrite a completed backup.
Keep the SHA-256 of manifest.json outside the backup directory (the dated report
records it). COMPLETE is written only after the object inventory is finished.
Retain partial attempts for diagnosis; absence of COMPLETE means not restorable.
This version has no pruning command, automatic retention deletion, encryption-key
manager, scheduler or cloud credentials. Files are created privately (directories
0700, objects 0600); underlying volume encryption is a separate storage property.

Owner decision (November 6): recovery tooling and drills are for functional
purposes only; independent backup copies are explicitly out of scope. No external
drive, cloud account, replication or independent-copy gate is required. Local clones
are a practical way to exercise recovery while preserving disk space.

## Create, audit and restore

Install the package and supply a JSON plan:

```json
{
  "root": "/path/to/GitRx",
  "includes": ["cohorts", "artifacts", "research", "data", "reports", "exports"],
  "mutable_sqlite": ["cohorts/release1/control.sqlite"]
}
```

Choose a new destination outside the source root. Immutable evidence sources must
be quiescent. Mutable SQLite paths explicitly listed in the plan use Python's
SQLite online backup API; their copied database digest may differ from the source
file digest and is recorded as a consistent snapshot. Immutable SQLite is retained
byte-for-byte and refused if a nonempty WAL/journal is present. Sidecars are not
copied as standalone evidence. Raw network response bodies remain included.
There is no cross-database transaction: independent immutable release pins establish
the release boundary; mutable control snapshots describe their individual backup time.

```sh
gitrx-recovery create --plan plan.json --destination /independent-volume/new-backup
gitrx-recovery audit --backup /independent-volume/new-backup --manifest-sha256 PIN
gitrx-recovery restore --backup /independent-volume/new-backup --manifest-sha256 PIN --destination /new/restore
```

On a macOS volume supporting clonefile, add `--clone` to create/restore for a local
copy-on-write drill. Full copies are the default. Clone mode does not fall back
silently to a full copy. Both modes enforce the existing 20 GiB free-space floor.
Hash every copied object, audit the complete inventory before restore, then hash
every restored file. A wrong pin, missing/corrupt object, source mutation, unsafe
path or existing destination causes refusal. Source-inode checksum reuse is limited
to identical device/inode/size/mtime within one quiescent backup; copies are never
hard links to source evidence. External symlinks require a separate explicit plan;
internal links are rewritten as portable relative links and their targets included.
Caches and OS metadata are excluded. Original source manifests are not rewritten.

## Restore validation

A successful file restore is necessary but not sufficient. Install the backed-up
wheel into an isolated environment outside the source checkout. Build an explicit
backend configuration pointing into the restored tree, retaining the same snapshot,
meta commit and scope pin. Run both adapters and compare saved example results.
Verify source database pins and content-addressed evidence names. Run SQLite
quick_check on restored databases. Record elapsed time, logical bytes, object count,
what was validated and any original-root mapping required by historical manifests.
Old stage scripts with absolute paths need explicit remapping; native reader
configuration must never reach the original checkout.

Backup targets, observed timings and limitations of the current exercise are in
[the recovery/performance report](../reports/recovery-performance-20261106/REPORT.md).
