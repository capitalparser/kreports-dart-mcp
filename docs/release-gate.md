# Release Gate And Artifact Proof

## Build versus verify

## Offline code-evidence lane

Run the default code-evidence lane with one command:

```bash
./scripts/run_offline_tests.sh
```

The runner deliberately overrides ambient shell and `.env` inputs with an
empty `DART_API_KEY`, a newly initialized mktemp SQLite file DB
(`DB_URL=sqlite:////.../kreports.db`), and
`KREPORTS_RUNTIME_MODE=readonly`. It clears live-DB opt-ins and blocks
non-loopback Python socket API connections in test processes and inherited
Python subprocesses. It excludes only the explicit `live`, `live_data`, and
`apfs_real` markers; ordinary tests are still collected and executed.

This is not OS-level network isolation: non-Python subprocesses and native
clients that bypass the patched Python socket APIs are outside this guard. Use
a CI network namespace, firewall policy, or an equivalent sandbox when that
stronger guarantee is required; such isolation is not implemented by this
runner.

This is code evidence, not a live-data release proof. Run explicit live lanes
separately, with an approved immutable database and their opt-in environment:

```bash
KREPORTS_LIVE_DB=/absolute/path/kreports.db \
  KREPORTS_RUNTIME_MODE=readonly \
  .venv/bin/python -m pytest -q -m live tests/test_professional_mcp_live.py

KREPORTS_RUN_LIVE_DB_TESTS=1 DB_URL=sqlite:////absolute/path/kreports.db \
  KREPORTS_RUNTIME_MODE=readonly \
  .venv/bin/python -m pytest -q -m live_data tests/test_golden_company_contracts.py

.venv/bin/python -m pytest -q -m apfs_real \
  tests/test_rehearsal_safety.py tests/test_kam_rehearsal_integration.py
```

```bash
kreports build-release-manifest --db artifacts/kreports-runtime.db
kreports verify-release-artifact --db artifacts/kreports-runtime.db
```

Build writes a strict JSON manifest atomically next to the DB by default. It is
allowed to finish when live data is not ready; the artifact then contains
`passed: false` and exact named blockers.

Verify is the deployment gate. It reopens the explicit DB immutably and
recomputes its hash and size, schema/table/index contract, dataset manifest,
inline raw count, current release gate, feature coverage and grades, the frozen
v1.3 33-tool public wire hash, isolated real-dispatch smoke for every public
tool, and the approved packaged golden-contract hash. The operator-opt-in
user-keyed DART fetch is outside the public release contract; the release check
never injects or persists a credential.
Drift or any current blocker returns non-zero.

The read-only quality gate returns `blocker_guidance` alongside each named
failure. Each entry identifies the responsible maintainer role and the next
required action; it never performs that action. Artifact verification likewise
returns `diagnostics` for proof-contract drift. In particular, a stale
32-tool proof versus the frozen 33-tool public catalog directs the dataset-release
maintainer to rebuild the manifest from the current approved catalog rather
than weakening the contract or masking the failure.

## Readiness meaning

The common predicate is:

```text
report.ok is true AND report.required_failures is empty
```

`/readyz` returns HTTP 200 only for that state. It reads the deployment artifact
without rerunning 33 tools on every probe and fails closed on a missing/invalid
artifact, non-empty WAL, DB file-name/size/hash drift, catalog drift, golden
drift, or stored blockers. The DB digest is computed once per process and file
identity, then reused until device, inode, size, mtime, or ctime changes.
Pre-deployment `verify-release-artifact` remains the mandatory full proof. HTTP
transport success, `data_quality.status=usable`, code tests, Ruff, doctor, or
smoke cannot hide a failed live-data gate.

Typical named blockers include missing schema tables or indexes, dataset
manifest mismatch, inline or quality drift, duplicate keys, insufficient
investor coverage, catalog drift, and golden-contract drift.

## Investor-core backfill preflight

When `investor_core_3y_coverage` is blocked, maintainers can create a
non-mutating request plan before any DART work:

```bash
kreports plan-investor-core-backfill --db artifacts/kreports-runtime.db --json
```

The command opens only an explicit, checkpointed SQLite snapshot with
`mode=ro&immutable=1`: a non-empty `-wal` or rollback `-journal` sidecar is
rejected because immutable reads could otherwise be stale. A standalone `-shm`
sidecar is allowed and never modified. The command reports the exact 95% target
(or `--threshold-pct`), deterministic company-year requests, annual filing
anchors, proof-row rejections, and missing disclosure metadata. It is a
no-network preflight: it does not prove DART availability,
API quota or request success, historical listing eligibility, or release
readiness. Run the actual authorized backfill and then the full release gate
separately; this command never weakens the gate or writes the DB.

The bounded execution is deliberately split into two commands. First repair
only the annual-filing metadata needed by non-source-ready planner candidates:

```bash
kreports run-investor-core-disclosure-backfill \
  --db artifacts/kreports-runtime.db \
  --as-of-date YYYY-MM-DD
```

Dry-run is the default and performs no network request or database write. An
authorized execution additionally requires collector mode, a DART API key, the
exact current database SHA-256, a positive request budget, and at least 10 GiB
of free space:

```bash
kreports run-investor-core-disclosure-backfill \
  --db artifacts/kreports-runtime.db \
  --as-of-date YYYY-MM-DD \
  --execute \
  --expected-db-sha256 SHA256 \
  --max-api-calls N
```

Each `list.json` page consumes one budget unit. The runner accepts only exact
company codes, 14-digit receipts whose prefix matches the exact DART receipt
date, annual reports for planner-selected years, and rows inside the explicit
query window. It updates an existing receipt only for the same company and
rolls back the target on a cross-company receipt collision. The single-writer
lock remains held through WAL checkpoint and post-run hash, row-count, and disk
evidence. A successful metadata phase is not financial backfill or release
readiness: rerun `plan-investor-core-backfill`, then run the bounded financial
phase on its freshly selected source-ready targets, rebuild derived quality
artifacts, and verify the release artifact separately.

After the financial phase succeeds, finalization can be restricted to the
exact company scope recorded by that fresh plan:

```bash
kreports run-investor-core-finalize \
  --db artifacts/kreports-runtime.db \
  --corp-code 00123456 \
  --corp-code 00654321 \
  --year-from 2021 --year-to 2025 --quality-year 2025 \
  --dataset-version investor-core-YYYYMMDD-v1
```

This is also dry-run by default. Execute mode requires the exact DB SHA and
collector runtime; it performs scoped compact rebuild, scoped company-year
quality rebuild, and a new immutable dataset manifest under one writer lock.
The command deliberately reports `release_ready=false`; run
`quality-release-gate` and `verify-release-artifact` separately as the final
readiness evidence.

## Immutable proof

Build and verify reject non-empty SQLite WAL state. They fingerprint DB/WAL/SHM
content and metadata across proof collection and reject a file swap. Explicit
evidence queries use the selected immutable DB; legacy tool handlers execute in
an isolated child process whose temporary engine binding cannot mutate the
calling CLI or HTTP server process.
Manifest output cannot alias the DB through the same path, a symlink, or a hard
link. A failed temp write or atomic replace preserves the previous manifest.
