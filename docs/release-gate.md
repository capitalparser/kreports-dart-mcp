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
32-tool wire hash, catalog-wide tool contract, and the golden fixture hash.
Drift or any current blocker returns non-zero.

## Readiness meaning

The common predicate is:

```text
report.ok is true AND report.required_failures is empty
```

`/readyz` returns HTTP 200 only for that state. HTTP transport success,
`data_quality.status=usable`, code tests, Ruff, doctor, or smoke cannot hide a
failed live-data gate.

Typical named blockers include missing schema tables or indexes, dataset
manifest mismatch, inline or quality drift, duplicate keys, insufficient
investor coverage, catalog drift, and golden-contract drift.

## Immutable proof

Build and verify reject non-empty SQLite WAL state. They fingerprint DB/WAL/SHM
content and metadata across proof collection, reject a file swap, and never use
the process-global SQLAlchemy engine when an explicit `--db` is supplied.
Manifest output cannot alias the DB through the same path, a symlink, or a hard
link. A failed temp write or atomic replace preserves the previous manifest.
