# Maintainer DB archive lifecycle

This is a **local maintainer workstation** capacity policy. It does not change
the public MCP deployment: the public runtime reads its prepared local SQLite
artifact only and never mounts, queries, or authenticates to Google Drive.

```text
inactive local candidate / historic DB
  -> SHA-256 + immutable Drive upload
  -> readback byte/hash verification + append-only ledger
  -> seven-day grace period
  -> local prune only when free-space policy requires it
```

## Protected boundaries

- The current runtime DB and its release manifest are passed with `--protect`.
  They are never archive or prune candidates.
- A database with `-wal`, `-shm`, or `-journal` sidecars is rejected. Checkpoint
  and close it before treating it as an inactive artifact.
- Google Drive contains an immutable archive object and ledger evidence, not a
  mounted SQLite file. Restoring a historic artifact is an explicit scratch
  operation; it must not overwrite the active runtime DB.
- Archive/upload and local deletion are distinct actions. A local file remains
  during the grace period even after Drive verification succeeds.

## One-time collector configuration

Add the following to the private collector environment file (never the public
MCP environment):

```bash
KREPORTS_RUNTIME_MODE=collector
KREPORTS_ENABLE_DB_ARCHIVE=1
RAW_STORAGE_BACKEND=drive
RAW_STORAGE_DRIVE_REMOTE=vault:
RAW_STORAGE_SPOOL_DIR="$HOME/.cache/kreports/drive-spool"
KREPORTS_DB_ARCHIVE_PREFIX='KReports Data Lake/db-archive'
KREPORTS_DB_ARCHIVE_GRACE_DAYS=7
KREPORTS_DB_ARCHIVE_MIN_FREE_GIB=20
KREPORTS_DB_ARCHIVE_TARGET_FREE_GIB=25
```

`vault:` is an example named `rclone` remote. It must declare `type = drive`.
The DB archive prefix must be separate from the raw annual-report source
archive prefix.

## Inspect before writing

This command neither contacts Drive nor writes a ledger or deletes a file:

```bash
uv run kreports db-archive-plan \
  --candidate-root "$HOME/Library/Application Support/kreports/candidates" \
  --protect "$HOME/Library/Application Support/kreports/releases/kreports-compact-2021-2025-v2.db" \
  --ledger "$HOME/Library/Application Support/kreports/archive-ledger/db-archive.jsonl"
```

`eligible_paths` is the exact set that can be archived. Resolve every
`unsafe_paths` entry before an apply.

## Explicit archive and capacity-bound prune

The following uploads and verifies inactive artifacts. `--prune` only removes
objects recorded as `verified` in the append-only ledger, unchanged locally,
past the grace period, closed by all processes, and only while the workstation
has less than 20 GiB free. It stops once 25 GiB is free.

```bash
uv run kreports db-archive-run \
  --candidate-root "$HOME/Library/Application Support/kreports/candidates" \
  --protect "$HOME/Library/Application Support/kreports/releases/kreports-compact-2021-2025-v2.db" \
  --ledger "$HOME/Library/Application Support/kreports/archive-ledger/db-archive.jsonl" \
  --grace-days 7 --min-free-gib 20 --target-free-gib 25 \
  --apply --prune
```

Do not add the live runtime directory as a candidate root. Historical releases
may be listed only after they are no longer serving MCP traffic and their
sidecars are absent.

## Daily macOS schedule

The wrapper uses the same variables and defaults as above. It runs daily at
03:15 local time and safely exits if the explicit DB archive opt-in is absent:

```bash
scripts/install_launchd_db_archive.sh
launchctl print gui/$(id -u)/com.kjun.kreports-db-archive
tail -f logs/db-archive-lifecycle.log
```

It deliberately does not run at load, and it does not start source collection
or modify a candidate DB. Remove it with:

```bash
scripts/uninstall_launchd_db_archive.sh
```
