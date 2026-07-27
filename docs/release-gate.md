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
32-tool wire hash, isolated real-dispatch smoke for all catalog tools, and the
approved packaged golden-contract hash. The user-keyed DART fetch is proven
fail-closed when no request-scoped key is supplied; the release check never
injects or persists a credential.
Drift or any current blocker returns non-zero.

## Readiness meaning

The common predicate is:

```text
report.ok is true AND report.required_failures is empty
```

`/readyz` returns HTTP 200 only for that state. It reads the deployment artifact
without rerunning 32 tools on every probe and fails closed on a missing/invalid
artifact, non-empty WAL, DB file-name/size/hash drift, catalog drift, golden
drift, or stored blockers. The DB digest is computed once per process and file
identity, then reused until device, inode, size, mtime, or ctime changes.
Pre-deployment `verify-release-artifact` remains the mandatory full proof. HTTP
transport success, `data_quality.status=usable`, code tests, Ruff, doctor, or
smoke cannot hide a failed live-data gate.

Typical named blockers include missing schema tables or indexes, dataset
manifest mismatch, inline or quality drift, duplicate keys, insufficient
investor coverage, catalog drift, and golden-contract drift.

## Immutable proof

Build and verify reject non-empty SQLite WAL state. They fingerprint DB/WAL/SHM
content and metadata across proof collection and reject a file swap. Explicit
evidence queries use the selected immutable DB; legacy tool handlers execute in
an isolated child process whose temporary engine binding cannot mutate the
calling CLI or HTTP server process.
Manifest output cannot alias the DB through the same path, a symlink, or a hard
link. A failed temp write or atomic replace preserves the previous manifest.
