# Task 4 — Expose Materiality Conflict Provenance

## Scope

- Base: `27c68393fccb765429ee27e05812b734f12555c9`
- Changed implementation/test files: `kreports/analysis/materiality_benchmark.py`,
  `kreports/mcp/professional_surfaces/audit_effort.py`, and
  `tests/test_materiality_benchmark.py`.
- No live `kreports.db` was accessed.

## RED

Added a literal public conflict fixture with identical amount/receipt values and
different `source_account_id` values, then ran:

```sh
UV_CACHE_DIR=/tmp/upbit-order-flow-uv-cache uv run pytest \
  tests/test_materiality_benchmark.py \
  -k materiality_rejected_conflicts_expose_provenance_without_amount -q
```

Result: `1 failed, 24 deselected`. The failure showed `_rejected_row` omitted
`source_account_id` and `source_table`.

## GREEN

- Added both provenance fields to bounded `_rejected_row` diagnostics.
- Added both fields to the materiality answer-pack `rejected_rows` table.
- Kept rejected `amount` out of both surfaces.

Validated with:

```sh
UV_CACHE_DIR=/tmp/upbit-order-flow-uv-cache uv run pytest \
  tests/test_materiality_benchmark.py tests/test_mcp_answer_pack.py \
  tests/test_professional_mcp_contract.py tests/test_mcp_contracts.py -q
UV_CACHE_DIR=/tmp/upbit-order-flow-uv-cache uv run ruff check \
  kreports/analysis/materiality_benchmark.py \
  kreports/mcp/professional_surfaces/audit_effort.py \
  tests/test_materiality_benchmark.py
git diff --check
```

Results: `117 passed`; Ruff and diff check clean.

## Commit

Implementation commit: `034bed2a17adc44fa4a44242e4ee235b5830a1e5`.

The Luna-high sandbox could not write Git's linked-worktree index lock, so the
controller created the commit from the unchanged, verified worktree diff.
