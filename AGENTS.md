# kreports_dart_mcp — Project System Context

This external-origin project turns Korean DART filings into structured
financial, audit, and investor signals through Python package, CLI, API,
dashboard, and MCP surfaces.

## Module Responsibilities

| Module | Responsibility | Location |
|---|---|---|
| Core Package | DART data models, collectors, signal logic, and query functions | `kreports/` |
| MCP/API | Agent-facing and HTTP-facing interfaces over core package behavior | `api/`, MCP modules |
| Dashboard | Streamlit or UI surfaces for human exploration | `dashboard/` |
| Scripts | Data collection, maintenance, and release helpers | `scripts/` |
| Tests/Fixtures | Regression coverage for DART parsing and investor/audit signals | `tests/` |
| Docs | User-facing setup, hosted/self-hosted modes, and release notes | `docs/`, `README.md` |

## Feature Addition Rules

- Core DART parsing and signal computation must not depend on MCP, FastAPI, or
  Streamlit.
- Investor-facing summaries must preserve source filing traceability.
- Audit/accounting professional features must separate evidence extraction from
  risk interpretation.
- Hosted and self-hosted modes must not diverge in domain semantics.
- Do not store DART API keys or private local database paths in docs, tests, or
  fixtures.

## Documentation Context

- Read `CONTEXT.md` before substantive work. Preserve its filing, company,
  investor-signal, audit-signal, source-filing, hosted-mode, and self-hosted-mode
  definitions unless the task explicitly changes the domain contract.
- Update `CONTEXT.md`, public schemas, tests, and user documentation together
  when a domain term or public behavior changes.

## Codex Validation Handoff — GitHub Actions Budget Policy

GitHub Actions minutes are treated as scarce. Codex local execution is the
primary validation engine for this repository.

### Non-negotiable workflow policy

- Do not add or restore automatic `push`, `pull_request`, or scheduled workflow
  triggers unless the user explicitly authorizes that change.
- Keep validation workflows manual-only with `workflow_dispatch` by default.
- Do not dispatch, rerun, or request a GitHub Actions workflow merely to obtain
  a green check. Run the equivalent commands locally in Codex first.
- Do not make GitHub Actions a required precondition for drafting or updating a
  PR when local Codex evidence is available.
- A historical failed Actions run is not current validation evidence and must
  not be rerun automatically.
- Never state that validation passed unless Codex actually executed the stated
  commands against the reported commit.

### Required Codex validation sequence

1. Inspect the exact PR diff and changed-file scope. Confirm that no secrets,
   local databases, raw filing bodies, generated caches, or unrelated files are
   included.
2. Install the package and test dependencies in an isolated environment.
3. Compile the changed Python runtime layers.
4. Initialize a disposable SQLite contract database in `collector` mode.
5. Switch to `readonly` mode and run the targeted contract/regression tests.
6. Fix failures, rerun the smallest failed set, and then rerun the complete
   targeted validation pack.
7. Run broader tests when shared catalog, dispatcher, database, parser, release,
   or answer-contract behavior changes.
8. Record the evidence in the PR before it is marked ready for review.

### Baseline commands for peer and accounting-note workflows

Run from the repository root in Codex:

```bash
python -m pip install -e ".[dev,api]"
python -m compileall -q kreports/analysis kreports/mcp

rm -rf .codex-validation
mkdir -p .codex-validation

DB_URL="sqlite:///./.codex-validation/kreports.db" \
KREPORTS_RUNTIME_MODE=collector \
kreports init

DB_URL="sqlite:///./.codex-validation/kreports.db" \
KREPORTS_RUNTIME_MODE=readonly \
pytest -q \
  tests/test_peer_criteria.py \
  tests/test_peer_workflows.py \
  tests/test_peer_workflow_mcp.py \
  tests/test_mcp_catalog.py \
  tests/test_mcp_contracts.py \
  tests/test_readonly_mcp.py
```

For a wider regression pass after shared MCP or analysis-layer changes:

```bash
DB_URL="sqlite:///./.codex-validation/kreports.db" \
KREPORTS_RUNTIME_MODE=readonly \
pytest -q \
  tests/test_all_tools_contract.py \
  tests/test_answer_contracts.py \
  tests/test_analysis_facade_parity.py
```

The disposable `.codex-validation/` directory must remain untracked and must not
be committed.

### Failure handling

- Classify each failure as one of: implementation regression, contract mismatch,
  fixture/database setup issue, missing optional dependency, pre-existing
  failure, or live-data-only dependency.
- Do not hide failures by weakening assertions, deleting coverage, or broadly
  marking tests as skipped.
- If a test requires a populated runtime artifact or live DART/GCS access,
  separate it from deterministic local contract tests and document the missing
  prerequisite.
- Preserve fail-closed behavior: missing cached data is a coverage gap, not
  proof that the original filing lacks the disclosure.

### PR validation evidence

Before marking a PR ready or recommending merge, add a PR note containing:

- validated head commit SHA;
- Python version and operating environment;
- exact commands executed;
- pass/fail/error/skipped counts;
- fixes applied after the first run;
- tests not run and the reason;
- remaining known limitations;
- confirmation that the runtime DB was disposable and no secrets were used.

Keep the PR as **Draft** when validation is pending. Do not merge into `main`
until local Codex validation is complete, the diff is reviewed, and the user
explicitly approves the merge.

A manual GitHub Actions dispatch is optional final confirmation only. It may be
performed once after local validation, and only with explicit user approval.

## Verification

- Run `uv run pytest` for the default deterministic test suite when the change
  scope warrants it.
- Run package/API-specific checks when changing public interfaces.
- Apply the Codex validation handoff above before relying on any manual workflow.
