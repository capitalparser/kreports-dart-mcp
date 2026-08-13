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

## Documentation Gap

- Add `CONTEXT.md` before the next substantive feature. Define filing, company,
  investor signal, audit signal, source filing, hosted mode, and self-hosted
  mode.

## Verification

- Run `uv run pytest` for default tests.
- Run package/API-specific checks when changing public interfaces.
