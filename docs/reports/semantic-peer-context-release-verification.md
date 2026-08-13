# Semantic peer-context release verification

Verified 2026-08-02 16:22 KST against commit
`b589ea22f600322bbd5a7d4d4159e777520a203e`
(`fix: enforce bounded context provenance`). This is a read-only verification;
it is not a dataset release approval.

## Result

| Evidence stream | Result | Meaning |
| --- | --- | --- |
| Code, semantic workflow, catalog, and contract regressions | PASS | The checked code surface is internally consistent. |
| Current retained runtime DB release proof | BLOCKED | The existing dataset and its July 27 manifest do not meet the current release contract. |
| Disk preflight | CONSTRAINED | 2.8 GiB free of 228 GiB (99% used); the retained DB is 7.4 GiB. No copy, rebuild, backfill, or manifest write was attempted. |

## Code and frozen-contract evidence

The current catalog has 34 tools. `FROZEN_TOOL_COUNT` is 34, and the computed
wire hash equals the frozen hash:

```text
4f63c50bd91bb5fb69197bcef3d79e4ac8cdf0b354abc6b0c65e81fa043b3c51
```

The semantic-context byte-budget regressions include large nested
`fs_div_selection` metadata. They assert actual UTF-8 serialized budgets
(context pack <= 60,000 bytes; workflow <= 100,000 bytes) while preserving
DART provenance and supplied IR/news source identifiers.

The following command passed:

```bash
KREPORTS_RUNTIME_MODE=readonly uv run pytest \
  tests/test_mcp_prompts.py tests/test_semantic_workflow_docs.py \
  tests/test_context_pack.py tests/test_mcp_workflows.py \
  tests/test_answer_contracts.py tests/test_semantic_index.py \
  tests/test_note_comparison.py tests/test_semantic_context_mcp.py \
  tests/test_mcp_catalog.py tests/test_mcp_tools_registration.py \
  tests/test_dart_mcp.py tests/test_all_tools_contract.py \
  tests/test_company_year_quality.py tests/test_quality_release_gate.py \
  tests/test_release_artifact.py -q
```

Result: `220 passed, 2463 warnings in 32.20s`. The warnings are existing
SQLAlchemy/Python datetime and SQLite adapter deprecations; no test failed.

Static checks also passed:

```bash
uv run ruff check kreports/analysis/context_pack.py kreports/mcp/workflows.py \
  tests/test_context_pack.py tests/test_mcp_workflows.py
uv run python -m compileall -q kreports/analysis/context_pack.py kreports/mcp/workflows.py
git diff --check
```

## Retained DB and runtime evidence

The following commands were executed without credentials, API calls,
backfill, DB initialization, or manifest creation:

```bash
DB_URL="sqlite:////Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db" \
KREPORTS_RUNTIME_MODE=readonly \
uv run kreports quality-release-gate --profile public_runtime --json

uv run kreports verify-release-artifact \
  --db /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db \
  --manifest /Users/kjun/vault/01_Projects/kreports_dart_mcp/kreports.db.release.json \
  --profile public_runtime --json
```

Both correctly exited 1 (blocked). The direct read-only gate reported:

```text
investor_core_coverage
release_manifest_unavailable
schema_migration_contract_mismatch
```

It also reported degraded `accounting_policy` and `audit_procedure` features.
The immutable artifact verifier reported the same data gate failures plus:

```text
contracts_evidence_mismatch
tool_contract_evidence_mismatch
missing_required_index:idx_audit_fee_availability_year
missing_required_index:idx_audit_procedure_kam_item
missing_required_index:idx_audit_procedure_method_year
```

The verifier opens the explicit SQLite file with `mode=ro&immutable=1` and
`PRAGMA query_only=ON`. Pre/post file identity checks were identical:

```text
kreports.db              7,959,240,704 bytes, inode 26174087
kreports.db-wal          0 bytes
kreports.db-shm          32,768 bytes, unchanged
kreports.db.release.json 2,389 bytes, inode 43736885
```

The retained database has zero rows in both `dataset_manifest` and
`company_year_quality`. Its retained manifest was generated on 2026-07-27 and
records a 32-tool contract with wire hash
`055f54993bf45f2e4a1388642871d09c1e2f45fc0b5fde1e83228bb910b38339`, so it
cannot prove the current 34-tool frozen contract.

## Release disposition and follow-up boundary

Code verification is a pass, but deployment readiness is blocked. To create a
releasable dataset artifact, a separately authorized maintainer operation must
prepare a DB with the required schema/indexes, populate and validate the
dataset manifest and company-year quality ledger, then build and verify a new
release manifest against that exact DB. Those actions can require substantial
space and data work and were intentionally not performed here.
