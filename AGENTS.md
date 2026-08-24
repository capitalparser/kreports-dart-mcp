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
- Also follow the nearest scoped instructions: `kreports/AGENTS.md` for domain
  and MCP work, `kreports/db/AGENTS.md` for database work, and `docs/AGENTS.md`
  for documentation, demos, and submission artifacts.

## Shared Database And Data-Mutation Policy

Shared maintainer, team-query, release-candidate, and runtime databases are
controlled artifacts.

- Treat every shared database as read-only unless the user explicitly authorizes
  the current task to mutate that exact database.
- Only the designated database maintainer may run shared migrations, backfills,
  regeneration jobs, runtime exports, or release-manifest builds.
- Contributors and agents must use disposable test databases, fixtures, or
  explicitly supplied read-only database copies for development and validation.
- Never repair a data defect by manually editing a shared database row. Fix the
  canonical collector, parser, transformation, or analysis logic; add a
  regression test; merge the change; then let the database maintainer rebuild
  or migrate the shared artifact.
- Before executing any command that may write to a non-disposable database,
  state the target database, expected mutations, rollback path, and required
  approval. Stop if authorization or target identity is unclear.
- Do not infer permission to write merely because a database path, environment
  variable, credential, or writable file is available.
- A read-only runtime must not initialize, migrate, backfill, persist a silent
  cache, or switch to an unrelated database when the intended artifact is
  missing or invalid.

## Task-Scoped Git And Pull-Request Workflow

Use task-scoped branches rather than long-lived person-scoped branches.

- The default unit is one Issue, one task branch, and one Pull Request.
- Create every task branch from the latest approved `main`.
- Name branches by work type and Issue, for example:
  `feat/10-audit-package-note-source`,
  `fix/15-wrong-dart-link`,
  `docs/13-submission-report`,
  `test/17-kam-golden-cases`, or
  `chore/18-release-db-baseline`.
- Do not create or continue long-lived branches named only after a contributor,
  such as `ye`, `ei`, or `kj`, for new work. Existing transition branches may be
  completed only for their already-open PR and then retired.
- Assign one primary branch owner. Other contributors participate through
  review, pair work, comments, or separately scoped prerequisite branches.
- Do not expand a Pull Request to include an unrelated problem discovered during
  implementation. Open a separate Issue and task branch.
- Stage only confirmed paths. Never use `git add .`, `git add -A`,
  `git add --all`, or equivalent broad staging commands.
- A review requesting changes does not require reverting the Worktree. Apply the
  requested fixes on the same task branch, rerun validation, commit, and push to
  update the existing Pull Request.
- Do not use `git reset --hard`, force-push, rebase a published shared branch,
  delete a shared branch, or rewrite history without explicit authorization.
- After merge, confirm that no uncommitted work remains, remove the completed
  Worktree and task branch, and start the next Issue from the latest `main`.
- If a defect is found after merge, create a new Issue and a new `fix/...`
  branch; do not revive the completed feature branch.

## Non-Developer Handoff And Explainability

KReports is developed with accounting professionals who may not be software
engineers. Every implementation handoff must remain reviewable by a
non-developer domain owner.

Before marking work ready for review, provide a plain-language Korean summary
covering:

1. the user or business problem being solved;
2. the source report and data used;
3. the end-to-end path from source filing to user answer;
4. the existing canonical modules, tables, tools, and tests reused;
5. the files changed and why;
6. the automated validation executed;
7. the real DART filings or representative fixtures compared; and
8. known limitations, missing coverage, and remaining blockers.

Do not mark AI-generated work ready merely because it compiles or a test passes.
The branch owner must be able to explain the input, output, source, calculation
or selection rule, failure behavior, and validation evidence. When a technical
term is necessary in a team-facing explanation, preserve the correct term and
add a short plain-language explanation on first use.

## Architecture Integrity and Anti-Patchwork Rules

AI-assisted development must strengthen the existing system rather than adding
parallel implementations, copied logic, or transport-specific substitutes.

### Reuse map before implementation

Before writing code, inspect the repository and record the following in the PR
or implementation notes:

1. the existing modules, contracts, tables, and tests that already address the
   requested concept;
2. the canonical owner that will remain responsible after the change;
3. the existing functions and data paths that the new behavior will reuse;
4. any legacy path that will be replaced, retained temporarily, or deprecated;
   and
5. the end-to-end path from source data through domain logic, MCP/API, chatbot
   presentation, and tests.

Do not create a new module merely because the existing implementation is hard
to understand. First determine whether the existing module should be extended,
split, or made to delegate to a new canonical service.

### One owner per domain concept

- Each domain concept must have one canonical implementation. Examples include
  peer criteria, note reference generation, note-text recovery, optional note
  facet extraction, CFS/OFS selection, DART link construction, pagination, and
  quality status.
- Search, comparison, API, MCP, dashboard, and export layers must call the same
  domain service. They must not maintain their own copies of matching,
  normalization, grading, or calculation rules.
- Transport and presentation modules may format a domain result but may not
  recompute or reinterpret the underlying accounting, financial, or evidence
  result.
- When a new canonical service is introduced, migrate existing callers to it in
  the same workstream where practical. Do not leave two silent sources of truth.

### No copy-and-modify development

- Do not copy a function or query into a new file and modify it independently.
  Extract the shared behavior or add an explicit parameter to the canonical
  implementation.
- Do not duplicate SQL for the same business result across handlers. Centralize
  the query or return a reusable typed result.
- Do not add alternate modules named `new_*`, `enhanced_*`, `v2_*`, `final_*`, or
  similar as permanent domain implementations. Versioned transport adapters are
  allowed only when the protocol itself differs and both versions delegate to
  the same domain layer.
- Do not solve a missing field in one output by inserting ad hoc calculations in
  a renderer. Add the field at the domain or application-contract boundary and
  reuse it everywhere.

### Compatibility adapters are bounded exceptions

- Dynamic installation, monkey-patching, wrappers, and dual-runtime adapters are
  permitted only at an explicit compatibility boundary, never as the owner of
  domain behavior.
- Every compatibility adapter must be idempotent, tested, documented with its
  reason, and have a stated removal or migration condition.
- New domain behavior must remain usable without the adapter through a normal
  Python call or typed service contract.
- A compatibility layer must delegate to existing behavior; it must not fork the
  business logic.

### Integrate, do not merely attach

A feature is not complete when a new file exists. The change must be connected
to all relevant existing paths:

```text
source/model
→ canonical domain service
→ existing analysis workflows
→ MCP/API contract
→ user-facing answer and structured UI
→ resource/drill-down path
→ regression tests
```

- Preserve provenance, data-quality status, and read-only guarantees across the
  entire path.
- Update invalidation and cache dependencies when a new input affects downstream
  results.
- Remove or explicitly deprecate superseded code. Do not keep unused fallback
  implementations “just in case.”
- If a legacy path must remain, add parity tests proving that both paths delegate
  to the same domain semantics.

### Module responsibility and size

- Prefer cohesive services with narrow public APIs over large utility files or
  cross-module imports of private helpers.
- A new module must state its responsibility in its docstring and expose a
  bounded `__all__` when it is intended as a shared service.
- Do not turn renderers, handlers, or transport servers into domain service
  containers.
- When a file has multiple unrelated reasons to change, split by responsibility
  before adding another substantial feature.

### Required duplication and integration checks

Before requesting review, verify and report:

- repository search found no second implementation of the new rule;
- user-visible and structured outputs are derived from the same result;
- no existing caller still uses a superseded calculation silently;
- no new N+1 query or repeated external read was introduced;
- the first five-company page and follow-up pages use the same stored result;
- full note or filing text stays outside the normal model context;
- source links, hashes, and quality statuses remain tied to the same evidence;
- targeted tests cover the canonical service and at least one end-to-end caller;
  and
- the PR explains reused code, new code, removed/deprecated code, and remaining
  compatibility boundaries.

### AI-generated change prohibition list

Do not merge AI-generated changes that:

- add a second source of truth;
- silently ignore unsupported user criteria;
- weaken assertions to make new code pass;
- preserve broken behavior behind an undocumented fallback;
- place internal identifiers or implementation messages in user-facing answers;
- send entire cached documents into the model context when a reference or
  resource can be used;
- perform the same external blob read, database scan, or statistical calculation
  more than once per request without an explicit reason; or
- leave dead, unreachable, or unreferenced files after a refactor.

## Internal Chatbot User-Answer Contract

The corporate chatbot is a business-user surface, not a developer console.
User-visible responses must answer the user's question directly and must not
expose implementation terminology.

### Required answer order

1. Start with the direct answer in one or two sentences.
2. Show only the few figures or companies necessary to support that answer.
3. Add a short table when it improves comprehension.
4. Link the relevant DART filing whenever a canonical receipt number exists.
5. State only limitations that materially affect the conclusion.
6. Offer concise follow-up actions in business language.

### Five-company paging

- Company lists must be packaged in pages of five companies.
- Plain Markdown shows only the first five-company page.
- Structured chatbot output may retain up to eight five-company pages for UI
  Previous/Next controls.
- Do not describe this as a row limit, result truncation, offset, or payload
  constraint in user-visible prose.
- Use natural prompts such as `다음 5개 비교회사를 보여줘`.

### Language rules

- Translate CFS/OFS to `연결`/`별도` in user-visible labels.
- Translate internal reason codes into phrases such as `같은 업종`, `회사
  규모가 유사`, `비교자료 확보`, or `사용자가 직접 선택`.
- Translate internal availability and comparison codes into plain Korean.
- Do not expose tool names, database/table names, field names, schema versions,
  hashes, cohort IDs, exception classes, or local paths in the default answer.
- Do not expose implementation terms such as `answer_pack`, `_meta`, `cell`,
  `coverage`, `mid-rank`, `exact`, `normalized`, `synonym`, `summary_only`,
  `unavailable`, `different_normalized_text`, `selection_score`, or
  `include_reasons`.
- Do not display UI implementation guidance to the end user.
- Preserve the technical values in internal structured metadata when required
  for reproducibility, but keep them out of visible titles, summaries, table
  labels, warnings, and follow-up prompts.

### Link rules

- When a valid 14-digit DART receipt number is present, provide a canonical
  clickable DART link next to the relevant company, note, or source.
- A link must never be fabricated from a company name alone.
- Keep the receipt number available for auditability, but present `공시 보기` as
  the primary user action.
- If no canonical source link is available, show no fake or inferred link.

### Accounting-note source-first rules

- The company's actual filing wording is the default answer. System-generated
  summaries, standardized wording, facet labels, or grades must not replace it.
- The initial answer may normalize whitespace, line breaks, unsafe HTML, and
  control characters only. It must not paraphrase, merge sentences, or rewrite
  the disclosure in chatbot language.
- Show the actual matched expression, a bounded original-text excerpt, and a
  user-friendly text-scope label such as `주석 전체 기준` or
  `일부 문구 기준 · 전체 주석 확인 필요`.
- Do not display `구체적`, `보통`, `간략`, completeness percentages, or company
  rankings in the default answer.
- Topic and information-element detection is an optional navigation index only.
  Use it when the user explicitly asks to compare a named facet such as amount,
  trigger condition, period, discount rate, or sensitivity.
- Every optional detected facet must include the exact supporting source span
  from the same note reference. A label without source text is not sufficient.
- Do not present registered facets as universal disclosure requirements or as a
  compliance checklist unless an explicit authoritative standard is separately
  supplied and cited.
- `현재 확인된 문구 없음` is not the same as `공시하지 않음`. An item not found
  in partial cached text must never be described as a filing omission.
- Use one canonical note reference and note-evidence service for search,
  comparison, resources, and UI actions.
- Full note pages and filing bodies must be retrieved lazily through a resource
  or application action and must not be inserted into the routine model context.

### User-facing regression requirements

For high-value chatbot workflows, tests must verify that:

- the first two sentences answer the user request;
- company lists render in five-company pages;
- valid receipt numbers produce clickable DART links;
- user-visible text contains no prohibited internal terminology;
- limited data does not become an affirmative conclusion;
- the structured answer and Markdown answer remain factually synchronized;
- note search and comparison show actual source wording and its text scope;
- the default answer contains no disclosure-depth grade or completeness score;
- partial evidence is not converted into an omission claim; and
- resource actions and source excerpts remain tied to the same note reference
  and source filing.

## MCP 2026 Conversation and State Contract

The optional MCP SDK v2 sidecar adopts the 2026-07-28 protocol without forcing
the existing MCP 1.x runtime to migrate in the same PR.

### Environment separation

- Keep the default project dependency at `mcp>=1.0,<2.0` while the legacy server
  remains supported.
- Build and validate `kreports-mcp-v2` only in the isolated environment described
  by `requirements-mcp-v2.txt`.
- Do not import `kreports.mcp.v2_server` in the default MCP 1.x test process.
- Do not modify the frozen 34-tool semantics merely to satisfy the v2 transport.

### Choice and Poll rules

- Ask for user input only when the choice materially changes the result and the
  trusted host explicitly marks the request interactive.
- Prefer explicit user criteria and saved conversation preferences over asking
  the same Poll again.
- Native 2026 clients receive an `InputRequiredResult`; older or custom clients
  receive the same application-neutral interaction contract through their UI
  adapter or a bounded Korean text fallback.
- Never collect API keys, credentials, payment data, OAuth secrets, or other
  sensitive input through a form Poll.
- A decline or cancel is a normal result and must not be converted into an
  approved/default selection silently.

### Identity and state rules

- User, conversation, and client identity come from the trusted chatbot host,
  not from model-generated tool arguments.
- State/page handles must be opaque, signed, identity-bound, expiring, and free
  of filing bodies, API keys, or local paths.
- Production multi-worker deployments require stable shared signing keys and a
  shared state/result store. Process-local state is development-only.
- Keep `KREPORTS_STATE_SIGNING_KEY` and
  `KREPORTS_MCP_REQUEST_STATE_KEY` outside source control and at least 32 bytes.
- A criteria change that affects peer membership invalidates the peer population
  and every dependent result reference.

### Context-window rules

- Model context is not the source of truth for workflow state.
- Supply at most eight bounded recent turns, one active-task summary, paused-task
  labels, and result references.
- Keep full company lists, multi-year raw metric rows, raw note text, raw filing
  text, and complete exclusion lists outside the model context.
- A `next 5` action must use a stored page token directly and must not ask the
  model to remember or reconstruct the previous population.
- Switching tasks in one chat must preserve separate task IDs and result refs.

### Performance rules

- Cache only deterministic read results using the prepared dataset identity,
  tool name, and normalized arguments.
- Never cache `fetch_disclosure_on_demand` or any call carrying user secrets.
- Coalesce identical concurrent requests with single-flight execution.
- Run existing synchronous handlers in a worker thread from the async MCP v2
  adapter and bound heavy-tool concurrency.
- The first answer carries only the current five-company page. Do not preload 40
  company rows into the initial structured response.
- Cache hits, shared execution, duration, state handles, and page tokens belong
  in application-only metadata or telemetry, never the user-facing prose.
- Do not use Tasks/background execution to disguise a slow ordinary peer query;
  peer selection, the latest-year benchmark, and next-page retrieval must remain
  fast synchronous interactions.

### Native v2 regression requirements

In the isolated v2 environment, tests must verify that:

- discovery negotiates `2026-07-28` and exposes the conversation extension;
- the same 34 tools have explicit input and output schemas;
- a form Poll completes through multi-round-trip input and sealed request state;
- accepted selections are applied exactly once;
- a five-company next-page request does not rerun domain analysis;
- structured content satisfies the advertised output schema;
- identical deterministic calls hit the server cache;
- handles cannot cross users or conversations; and
- no Actions run is required for local evidence.

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

For the conversation core in the default MCP 1.x environment:

```bash
pytest -q \
  tests/test_conversation_orchestration.py \
  tests/test_mcp_v2_runtime.py
```

For the native SDK v2 sidecar, create a separate environment and run:

```bash
python -m venv .venv-mcp-v2
. .venv-mcp-v2/bin/activate
python -m pip install -e . --no-deps
python -m pip install -r requirements-mcp-v2.txt
python -m compileall -q kreports/conversation kreports/mcp/v2_server.py
pytest -q tests/test_mcp_v2_native.py
```

For note-reference, source-first answers, and lazy full-note resources:

```bash
pytest -q \
  tests/test_note_evidence_depth.py \
  tests/test_note_resource_contract.py \
  tests/test_note_depth_chatbot.py \
  tests/test_note_search_quality.py \
  tests/test_note_quality.py
```

The disposable `.codex-validation/` and `.venv-mcp-v2/` directories must remain
untracked and must not be committed.

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
