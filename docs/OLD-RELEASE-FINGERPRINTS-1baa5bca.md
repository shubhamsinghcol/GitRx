# Old release fingerprints (snapshot 1baa5bca)

Recorded 2026-11-11 before the default native release switched to the Step E rebuild
(`bbe9223a...`). **Nothing was deleted.** This is evidence for the owner-approved
later deletion of the old release data; deletion itself is a separate, explicitly
approved step.

- Old snapshot id: `1baa5bca2fd0091dfd8da9c82e260709483513515637df81a7c2db7765d61abc`
  (7,239 families / 51,357 versions, parser spl-sections-0.1.0).
- Old configuration `runtime/native-config.json` sha256
  `f392ef1f85d33e2262bc74419f633c0fba66ac493f0d1c4e77baffee2c4418bf` (content embedded in the JSON; still selectable
  with `GITRX_CONFIG=runtime/native-config.json`).
- Full machine-readable record: `/Volumes/Untitled/GitRx/old-release-fingerprints-1baa5bca.json`
  (sha256 `f910fcde0cf59f82c0bf8431c3955e1877f0653bb448bd69e69bb0182a3fa6df`, 15,225,028 bytes).

Contents of the JSON:

| Section | Count | Notes |
| --- | --- | --- |
| `git_repositories` | 66 | every repo under `exports/*` and `cohorts/release1/exports/*`: HEAD, all refs (branch/tag -> commit id), pack file names and sizes (164 refs; 13 repos have an unresolvable HEAD but their refs are listed) |
| `cohort_files` | 51,427 | every file in `cohorts/release1-recovered-20261106` (4,701,618,890 bytes): path, size, sha256 (includes `native-query.sqlite`, `reports/NATIVE-INDEX.json`, `export-payloads/`, `data/gitrx.sqlite*`) |
| `export_tree_files_excluding_dot_git` | 22,709 | every non-`.git` file in the export trees (5,122,015,177 bytes): path, size, sha256 |

Git object stores (`.git/objects`) are identified by commit ids and pack file
names/sizes, not hashed per object; the commit ids determine their content.
The build was hashed read-only; a 150-file re-hash sample matched with 0 mismatches.

## Needed before deletion

The Step E rebuild must be the verified default (see the verification report), the
owner must approve the specific deletion list, and this JSON should be copied to
a second location if the SSD is the only copy.
