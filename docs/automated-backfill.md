# Automated DART-Limit-Aware Backfill

The collector should run on a private maintainer machine. The public MCP
endpoint remains read-only and must not have `DART_API_KEY`.

## Runtime Contract

- `scripts/dart_limit_aware_backfill.sh` is idempotent.
- It exits when another live backfill is already running.
- It closes stale `backfill_runs` records whose PID no longer exists.
- It probes DART once before starting the full backfill.
- If DART returns usage-limit status `020`, it logs and waits for the next
  scheduled run instead of failing loudly.

## Private Collector Env

Store collector secrets outside the repository:

```bash
mkdir -p ~/.config/kreports
chmod 700 ~/.config/kreports
$EDITOR ~/.config/kreports/collector.env
chmod 600 ~/.config/kreports/collector.env
```

Required content:

```env
DART_API_KEY=<opendart-key>
KREPORTS_RUNTIME_MODE=collector
```

## Install macOS launchd Job

```bash
scripts/install_launchd_backfill.sh
```

The job runs at 00:20, 06:20, 12:20, and 18:20 local time, plus once at load.
This intentionally does not assume the exact DART reset time. The probe is
cheap, and the wrapper will skip while the API limit is still exhausted.

Status:

```bash
launchctl print gui/$(id -u)/com.kjun.kreports-dart-backfill
tail -f logs/dart-limit-aware-backfill.log
tail -f logs/full-dataset-backfill.log
```

Uninstall:

```bash
scripts/uninstall_launchd_backfill.sh
```
