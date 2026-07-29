# Task 1 Report: Canonical Quality Evidence Summary

## Result

- Added the exact ordered seven-status and three-grade contracts.
- Added deterministic mapping order, blocker deduplication/sorting, and
  canonical JSON SHA-256 hashing.
- Unknown or missing required keys fail closed with `ValueError`.
- Summary content is limited to statuses, grades, blockers, and the quality
  version; no timestamp or local path is accepted by the builder interface.

## RED

Command:

```bash
uv run pytest tests/test_company_year_quality.py -q
```

Result: collection failed with
`ModuleNotFoundError: No module named
'kreports.quality.company_year_fingerprint'`, establishing that the new
contract did not exist.

## GREEN

Commands:

```bash
uv run pytest tests/test_company_year_quality.py -q
uv run ruff check \
  kreports/quality/company_year_fingerprint.py \
  tests/test_company_year_quality.py
git diff --check
```

Results:

- `34 passed`, `200 warnings`, exit status `0`.
- Ruff: `All checks passed!`
- `git diff --check`: passed.

## Self-review

- The canonicalizer requires exact key-set equality before imposing the
  contract order.
- Semantic-change coverage independently changes one status, grade, blocker
  set, or quality version and verifies a different fingerprint.
- Stable-order coverage reverses mappings and repeats/reorders blockers.
- The fingerprint excludes computation time by construction.
- No database, network, DART, MCP, sidecar, migration, or grade/status
  algorithm changed.
- Two existing `timezone.utc` uses in the modified test file were converted to
  the Ruff-required `UTC` alias; this is mechanical and behavior-preserving.
