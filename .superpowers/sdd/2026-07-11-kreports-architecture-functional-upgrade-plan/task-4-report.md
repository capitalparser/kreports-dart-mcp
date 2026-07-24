# Task 4 Report: Runtime Write Policy And Release-Aware Readiness

## Result

- Implementation commit: `abdfc329fab98944b3ae472c8a0b1b7d69b25e11`
- Read-only is fail-closed for DB/object-store writes. Caller-keyed on-demand DART fetches are ephemeral unless collector mode, raw opt-in, and external non-inline storage are all enabled.
- `/readyz` now uses one read-only public-runtime gate. It fails closed for missing release manifest/schema support, investor-core coverage gaps, table accessibility failures, non-readonly mode, incorrect tool count, and stale running backfills.

## Red / green evidence

- RED: `uv run pytest tests/test_runtime_write_policy.py tests/test_http_mcp_server.py -q` initially failed collection because `raw_persistence_allowed` did not exist.
- GREEN: `uv run pytest tests/test_runtime_write_policy.py tests/test_readonly_mcp.py tests/test_on_demand_disclosure_fetch.py tests/test_http_mcp_server.py tests/test_mcp_tools_registration.py -q` passed: `41 passed`.
- `uv run kreports mcp-doctor` passed and reported exactly `31` tools.

## Review follow-up

- Follow-up implementation commit: `93dd23bc18d950f36d2f6d86510c6bd83fea2bc7`
- Added a central `get_session()` guard for ORM unit-of-work changes and Core/text DML, including when tests monkeypatch `SessionLocal`.
- `RawDocumentStore.write()` now enforces the same collector/raw-policy boundary directly; GCS also requires a bucket.
- Empty manifest tables no longer pass readiness. DB inspection failures and unexpected `/readyz` gate exceptions now return a stable HTTP 503 payload with `runtime_db_unavailable`.
- Follow-up verification: the original focused tests plus raw-storage coverage passed: `64 passed`; `uv run kreports mcp-doctor` passed with 31 tools.

## Second review follow-up

- Follow-up commit: `PENDING`
- Central SQL detection now strips leading block and line comments and detects mutating CTE text while preserving ordinary `SELECT` and `WITH ... SELECT` reads.
- The session barrier now guards SQLAlchemy bulk persistence methods, `EvidenceBlobStore.write()` requires collector mode, and `init_db()` is a collector-only schema mutation.
- Verification: runtime policy, raw/evidence storage, HTTP, tool, and DB-init collector regressions passed: `86 passed`; MCP doctor still passed with 31 tools.

## Limitations

- Task 3's versioned schema migration, manifest validation, and company-year quality ledger are not implemented in this worktree. The gate reports `schema_version: unknown` and returns HTTP 503 with `release_manifest_unavailable` rather than fabricating a validated version.
- Dataset version is only an honestly derived maximum `fetched_at` timestamp when available; otherwise it is `unknown`.
- Existing legacy collector timestamps still use `datetime.utcnow()`; Task 4 did not introduce new naive timestamps.
