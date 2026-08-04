# Release Gate And Artifact Proof

## Build versus verify

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
34-tool wire hash, isolated real-dispatch smoke for all catalog tools, and the
approved packaged golden-contract hash. The user-keyed DART fetch is proven
fail-closed when no request-scoped key is supplied; the release check never
injects or persists a credential.
Drift or any current blocker returns non-zero.

The read-only quality gate returns `blocker_guidance` alongside each named
failure. Each entry identifies the responsible maintainer role and the next
required action; it never performs that action. Artifact verification likewise
returns `diagnostics` for proof-contract drift. In particular, a stale
32-tool proof versus the frozen 34-tool catalog directs the dataset-release
maintainer to rebuild the manifest from the current approved catalog rather
than weakening the contract or masking the failure.

## Readiness meaning

The common predicate is:

```text
report.ok is true AND report.required_failures is empty
```

`/readyz` returns HTTP 200 only for that state. It reads the deployment artifact
without rerunning 34 tools on every probe and fails closed on a missing/invalid
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

The command opens only the explicit SQLite file with `mode=ro&immutable=1` and
reports the exact 95% target (or `--threshold-pct`), deterministic company-year
requests, annual filing anchors, proof-row rejections, and missing disclosure
metadata. It is a no-network preflight: it does not prove DART availability,
API quota or request success, historical listing eligibility, or release
readiness. Run the actual authorized backfill and then the full release gate
separately; this command never weakens the gate or writes the DB.

## Immutable proof

Build and verify reject non-empty SQLite WAL state. They fingerprint DB/WAL/SHM
content and metadata across proof collection and reject a file swap. Explicit
evidence queries use the selected immutable DB; legacy tool handlers execute in
an isolated child process whose temporary engine binding cannot mutate the
calling CLI or HTTP server process.
Manifest output cannot alias the DB through the same path, a symlink, or a hard
link. A failed temp write or atomic replace preserves the previous manifest.
