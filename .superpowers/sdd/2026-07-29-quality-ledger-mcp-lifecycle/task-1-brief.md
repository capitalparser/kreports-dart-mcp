# Task 1 Brief: Canonical Quality Evidence Summary

## Objective

Add a pure module that converts the exact quality status/grade contract,
blockers, and quality version into one bounded canonical object and a stable
SHA-256 fingerprint.

## Contract

- Status keys must exactly equal the ordered seven-key status contract.
- Grade keys must exactly equal the ordered three-key grade contract.
- Duplicate blockers are removed and blockers are sorted.
- Unknown or missing status/grade keys fail closed with `ValueError`.
- Canonical summaries contain no timestamp or other volatile value.
- A semantic change to any status, grade, blocker, or quality version changes
  the fingerprint.

## TDD boundary

1. Add tests that import the absent module and express the complete contract.
2. Run the focused file and retain the expected import failure as RED evidence.
3. Add only the pure canonicalization and hashing implementation.
4. Run the focused quality file and Ruff before self-review and commit.

No database, MCP, network, or persistence behavior changes in this task.
