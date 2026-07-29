# Scoped Baseline

## Repository state

- Worktree: `/Users/kjun/vault/01_Projects/kreports_dart_mcp/.worktrees/db-quality-lifecycle`
- Branch: `codex/db-quality-lifecycle`
- Base and starting HEAD: `7a631222751ac0dda3b5c1b1815d821fff87f3b8`
- Worktree was clean before baseline execution.
- Git directory and common Git directory differed, confirming an isolated linked worktree.

## Command

```bash
uv run pytest \
  tests/test_company_year_quality.py \
  tests/test_quality_release_gate.py \
  tests/test_dataset_manifest.py \
  tests/test_mcp_resources.py \
  tests/test_http_mcp_server.py \
  tests/test_all_tools_contract.py \
  -q
```

## Result

- `113 passed`
- `2657 warnings`
- Runtime: `11.41s`
- Exit status: `0`

The warnings were existing Python 3.12 SQLite adapter and
`datetime.utcnow()` deprecations. The scoped baseline used only the repository
fixtures that replace the shared session factory with in-memory or temporary
databases. It did not open the live database, call DART, use a sidecar outside
`tmp_path`, or contact remote Git.
