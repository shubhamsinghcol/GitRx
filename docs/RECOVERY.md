# Backup and restore tool (for maintainers)

`gitrx-recovery` makes a content-addressed local backup of evidence, audits it, and restores it into a new folder.
Raw objects, source observations, failed-response ledgers, manifests and release databases are included; derived indexes are
only restore accelerators. Independent off-site backups are outside the current project scope, and the restore drill has
not been completed, so recovery is not demonstrated.

## Policy

Keep source evidence and releases indefinitely unless the owner authorizes a deletion. Never overwrite a finished backup.
Store the SHA-256 of `manifest.json` outside the backup. A backup without a `COMPLETE` file is not restorable.
There is no pruning, encryption-key handling, scheduler or cloud access. Files are created private (directories 0700, objects 0600).

## Use

Write a plan:

```json
{
  "root": "/path/to/GitRx",
  "includes": ["cohorts", "artifacts", "research", "data", "reports", "exports"],
  "mutable_sqlite": ["cohorts/release1/control.sqlite"]
}
```

```sh
gitrx-recovery create  --plan plan.json --destination /volume/new-backup
gitrx-recovery audit   --backup /volume/new-backup --manifest-sha256 PIN
gitrx-recovery restore --backup /volume/new-backup --manifest-sha256 PIN --destination /new/restore
```

The destination must be new and outside the source root, and evidence must not be changing during the backup. Listed mutable SQLite files are
copied with SQLite's online backup, so their hash can differ from the source. Other SQLite files are copied byte for byte and refused if a
non-empty journal exists. Every copied object is hashed, the inventory is audited before restore, and every restored file is hashed.
A wrong pin, a corrupt or missing object, a changed source, an unsafe path or an existing destination is refused. Both modes need 20 GiB free.
On macOS, `--clone` uses copy-on-write for a local drill and never silently falls back to full copies.

## Checking a restore

Restoring files is necessary but not enough. Install the package in a clean environment, point a config at the restored tree with the same snapshot and
pins, run both readers on saved examples, check database pins and hash-named files, and run SQLite `quick_check`. Old scripts with absolute paths need
remapping; a config must never point back at the original checkout. See the [recovery report](../reports/recovery-performance-20261106/REPORT.md).
