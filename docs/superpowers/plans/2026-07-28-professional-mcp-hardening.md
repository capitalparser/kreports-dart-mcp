# Professional MCP Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make KReports’ auditor and investor MCP tools return decision-ready, filing-grounded chatbot answers whose status, facts, tables, and sources remain consistent across `answer`, `answer_pack`, and detailed resources.

**Architecture:** Normalize one canonical data-quality status before any presentation layer runs, while preserving allowlisted tool-specific outcomes as a separate `domain_verdict`. Add a shared annual-filing provenance resolver and isolated professional-surface registries, then implement audit-effort, auditor, and investor slices in parallel worktrees from the same shared-base commit. Integrate only after each slice passes synthetic public-path tests; run explicit read-only live verification on the integrated result.

**Tech Stack:** Python 3.12.7, SQLAlchemy 2, Pydantic v2, MCP 1.x, SQLite, pytest, Ruff, existing KReports dispatcher, answer-envelope, visualization contracts, and immutable DART cache.

## Global Constraints

- Base all implementation work on commit `4b6b7fb19d679026e8e805256f905ac24edf2a20` plus the shared-base tasks in this plan.
- Preserve the original dirty worktree at `/Users/kjun/vault/01_Projects/kreports_dart_mcp`.
- Do not copy the original uncommitted three-year audit-effort patch without re-validating its source and status behavior.
- Keep domain extraction, professional interpretation, and presentation in separate modules.
- The canonical statuses are exactly `usable`, `limited`, `missing`, and `error`.
- Preserve only per-tool allowlisted analytical outcomes such as `monitor`, `stable`, `partial_model`, and `not_assessed` in `domain_verdict`; do not overload the canonical status or promote arbitrary legacy verdict strings.
- `usable` requires purpose-specific required data and valid public provenance for every material confirmed fact.
- A valid DART source requires a 14-digit receipt number resolved through `kreports.analysis.evidence.parent_rcept_no`.
- `missing` means local-cache absence and never proves filing absence.
- Do not infer a standard audit hour, acceptance decision, audit opinion, investment recommendation, or valuation conclusion.
- Do not treat unavailable investor checks as failed checks.
- Do not display a DCF enterprise value, equity value, or valuation chart when the value was not calculated.
- Keep chatbot display order `answer` → `answer_pack` → detailed resource.
- Put a conclusion and a 5–10-row core Markdown table in the chatbot when tabular comparison is material.
- Keep complete tables in `answer_pack`; bound chatbot tables rather than discarding underlying rows.
- Do not expose internal snake_case status or signal keys in the chatbot.
- Do not modify, migrate, backfill, or regenerate the live `kreports.db`.
- Use synthetic companies and receipts in committed tests; do not commit confidential fee or engagement data.
- Run production-path tests under Python 3.12.7.
- Treat the known Python 3.11 KAM parser compatibility issue as a separately reported residual unless this work directly changes that parser behavior.

---

## Current Baseline

At `4b6b7fb`, live read-only Samsung Electronics FY2025 calls demonstrated:

- `build_audit_acceptance_pack`: answer `limited`, pack `missing`, despite policy,
  KAM, and audit-matter payloads.
- `compare_peer_risk_profile`: answer `limited`, pack `missing`, despite subject
  and peer metrics.
- `get_audit_history`: five years of auditor/opinion receipts, but no confirmed
  facts and a missing pack.
- `get_kam_lifecycle`: `usable` with no cited facts.
- `compare_peer_kam_topics`: answer `usable`, pack `missing`.
- `get_financial_snapshot`: five annual rows reduced to one generic fact.
- `compare_to_industry_multi`: forty metric rows with zero sources.
- `get_investor_signals`: missing FCF and CFO/NI treated as false checks.
- `get_dcf_input_candidates`: candidate data marked `usable` while WACC and
  working-capital inputs remain unresolved.
- `build_dcf_model_pack`: partial model language can precede an unavailable
  enterprise value.

The plan closes these output-contract failures without claiming release-data
completeness.

## What Already Exists

- `kreports.mcp.contracts._data_quality()` already normalizes legacy status
  aliases and downgrades uncited confirmed facts. Task 1 deepens this path
  instead of adding a second status engine.
- `kreports.analysis.evidence` already validates DART receipts, extracts parent
  receipts from attachment identifiers, and rejects unsafe public URLs. Task 2
  reuses these primitives.
- `financial_analysis._annual_report_source()` already resolves a financial
  filing for investor facts. Task 2 extracts and tests that behavior for reuse.
- `answer_pack.py` already has dedicated packs for DCF, QoE, investor signals,
  audit fees, KAM lifecycle, subsidiaries, events, and peer benchmarks. The new
  surface registry preserves those builders while moving new professional
  decisions out of the 1,400-line central file.
- `renderers.py` already renders the professional envelope before detail and
  can append Markdown fallback tables. The plan removes status recomputation
  rather than replacing the renderer.
- `compare_peer_audit_fees`, `estimate_audit_hours_proxy`, and
  `build_audit_acceptance_pack` already assemble the raw screening payloads.
  Task 3 and Task 4 preserve those calculations and repair evidence/status
  semantics.
- The original dirty worktree contains a tested three-year subject-scale
  candidate. Task 3 uses its demonstrated row shape as reference but
  reimplements provenance and fail-closed coverage in an isolated worktree.
- `resources.py` already exposes dataset readiness. Task 8 publishes a bounded
  subset instead of inventing another release gate.

## NOT in Scope

- Production database schema migrations or historical backfill: current gaps
  must remain visible, and live storage mutation needs a separate approved
  data-readiness slice.
- Korean standard-audit-hours rule calculation: public DART inputs alone do not
  establish the statutory/firm-methodology result.
- Audit acceptance approval, rejection, independence clearance, or management
  integrity conclusion: the MCP remains a screening evidence tool.
- Investment recommendations, target prices, fairness opinions, or automated
  forecasts: investor tools remain evidence and readiness surfaces.
- Rewriting the peer selection algorithm: this plan preserves the existing
  cohort method and adds denominator/provenance transparency.
- Reclassifying all historical disclosure events in storage: this plan corrects
  user-facing certainty and leaves durable event reindexing to a separate data
  quality task.
- Fixing the existing Python 3.11 KAM parser compatibility issue: report it as a
  residual unless a changed codepath creates a new regression.
- Dashboard or standalone web UI redesign: the MCP chatbot is the primary
  surface.
- Deployment, push, PR, or release publication: execution stops at a verified
  local integration branch unless separately authorized.

## Data Flow And Status State Machine

```text
MCP client
   |
   v
typed input model
   |
   v
handler --------------------------+
   |                              |
   v                              |
domain query/calculation          |
   |                              |
   +--> facts + sources           |
   +--> analysis                  |
   +--> domain_verdict            |
   +--> data_quality inputs       |
   |                              |
   v                              |
normalize_answer_result() <-------+
   |
   +--> canonical data_quality.status
   +--> AnswerEnvelopeV1.verdict
   |
   +---------> chatbot answer
   +---------> answer_pack
   +---------> detailed resource
                    |
                    v
       same status, facts, and source set
```

```text
tool exception? ------------------------------ yes --> error
      |
      no
      v
any purpose-relevant local evidence? --------- no  --> missing
      |
      yes
      v
required field/period/source/denominator gap?  yes --> limited
      |
      no
      v
usable

domain_verdict runs beside this state machine.
It never changes or replaces canonical status.
```

Add short versions of these diagrams as inline comments beside
`normalize_answer_result()` and the DCF availability branch. Do not add
diagrams to simple table-builder functions.

## Failure Modes

| Codepath | Production failure | Test | Error handling | User outcome |
|---|---|---|---|---|
| Audit-effort schema inspection | Live DB lacks optional typed fee/source columns | absent-column fixture | select only discovered columns | limited row with exact provenance gap |
| Filing provenance | A newer filing belongs to another company or year | cross-company/year fixture | resolver returns `None` | uncitable limitation, no false DART link |
| Canonical status | Nested payload exists but generic pack emits missing | non-empty peer/KAM regression | normalized status reaches every layer | one limited result, not contradictory states |
| Audit matter classification | Boilerplate resembles other-matter language | boilerplate fixture | signal requires classified section and excerpt | no false acceptance signal |
| Investor checks | `None` is coerced to false | unknown-check fixture | explicit `unknown` branch | coverage warning, no supportive conclusion |
| Event classification | Cached type overstates control-change certainty | public-path event fixture | fact confirms title/date only | classifier shown as screening label |
| DCF candidates | WACC or working capital is absent | blocker fixture | valuation readiness becomes blocked | exact owner, impact, and next action |
| DCF model | EV remains `None` after partial model assembly | unavailable-model fixture | suppress bridge/chart/value rows | answer begins with `산출 불가` |
| Release context | Manifest/readiness lookup fails | unavailable-context fixture | bounded fail-closed fallback | question answer survives with release warning |
| Live verification | A supposedly read-only call mutates SQLite | before/after digest check | stop completion immediately | no completion or release claim |

No listed failure mode is silent without both a test and a user-visible
fallback.

## Test Coverage Map

```text
CODE PATHS                                      PUBLIC USER FLOWS
[Task 1] normalize legacy result                [all public tools]
  +-- usable + cited                              +-- answer status parity
  +-- usable + uncited -> limited                 +-- pack status parity
  +-- partial -> limited                          +-- missing cache wording
  +-- empty -> missing                            +-- error wording
  +-- exception -> error

[Task 3] audit-effort inputs                    [auditor asks fee/hours]
  +-- complete 3 years                            +-- sees assets/revenue/fee/hours
  +-- missing year/field                          +-- sees exact missing fields
  +-- missing provenance                          +-- sees no fabricated source
  +-- actual/contract separation                  +-- sees not_assessed boundary

[Tasks 4-5] auditor evidence                    [acceptance/opinion/KAM review]
  +-- risk/history/acceptance                      +-- evidence matrix
  +-- opinion vs opinion basis                     +-- category-specific next check
  +-- boilerplate rejection                        +-- no approval conclusion
  +-- KAM semantic coverage                        +-- timeline plus limitations

[Tasks 6-7] investor evidence                   [investor first pass]
  +-- five-year financial rows                    +-- compact trend table
  +-- pass/fail/unknown checks                     +-- honest coverage
  +-- cohort denominator/source                    +-- reproducible peer result
  +-- candidate vs valuation readiness             +-- blocked inputs before model
  +-- calculated vs unavailable DCF                +-- value or 산출 불가, never None

[Tasks 8-9] release/live integration            [chatbot host]
  +-- release ready/unready/unavailable            +-- separate release context
  +-- Samsung read-only probes                     +-- actual MCP result matrix
  +-- DB digest equality                           +-- no storage side effect
```

Pure status, source, and calculation branches use unit tests. Handler-to-answer
flows use public `call_tool` integration tests. No LLM prompt or generative
output changes require an evaluation suite.

## File Structure

### New shared modules

- `kreports/analysis/filing_provenance.py`
  - Resolve same-company, same-year DART filing sources for structured facts.
- `kreports/analysis/audit_effort_inputs.py`
  - Build three-year audit-effort inputs and coverage decisions.
- `kreports/analysis/auditor_decisions.py`
  - Compose acceptance, peer-risk, and history evidence without changing the
    legacy peer algorithms during parallel work.
- `kreports/analysis/investor_peer_evidence.py`
  - Wrap peer selection and comparison with denominators and provenance.
- `kreports/mcp/professional_surfaces/__init__.py`
  - Merge domain-specific pack and detail-renderer registries.
- `kreports/mcp/professional_surfaces/audit_effort.py`
  - Render standard-hours input preparation and audit-effort tables.
- `kreports/mcp/professional_surfaces/auditor.py`
  - Render acceptance, risk, history, opinion, matter, and KAM surfaces.
- `kreports/mcp/professional_surfaces/investor.py`
  - Render financial trend, peer, investor quality, QoE, and DCF surfaces.

### Shared files

- `kreports/mcp/contracts.py`
  - Canonical status and `domain_verdict` contract.
- `kreports/mcp/answer_pack.py`
  - Central builder dispatch only; domain builders move to registries.
- `kreports/mcp/renderers.py`
  - Professional envelope and central renderer dispatch only.
- `kreports/mcp/visual_contracts.py`
  - Enforce canonical pack status without independently inferring availability.

### Audit-effort slice

- `kreports/mcp/input_models.py`
- `kreports/mcp/catalog.py`
- `kreports/mcp/handlers/audit_effort.py`
- `kreports/mcp/handlers/__init__.py`

### Auditor slice

- `kreports/analysis/audit_reporting.py`
- `kreports/analysis/auditor_decisions.py`
- `kreports/mcp/handlers/auditor.py`

### Investor slice

- `kreports/analysis/financial_analysis.py`
- `kreports/analysis/investor_quality.py`
- `kreports/analysis/dcf_inputs.py`
- `kreports/analysis/dcf_model.py`
- `kreports/analysis/investor_peer_evidence.py`
- `kreports/mcp/handlers/company.py`
- `kreports/mcp/handlers/search.py`
- `kreports/mcp/handlers/investor.py`

### Release context

- `kreports/mcp/resources.py`
- `kreports/mcp/dispatch.py`

### New contract tests

- `tests/test_professional_status_truth.py`
- `tests/test_filing_provenance.py`
- `tests/test_standard_audit_hours_inputs.py`
- `tests/test_auditor_decision_surfaces.py`
- `tests/test_audit_report_semantics.py`
- `tests/test_investor_decision_surfaces.py`
- `tests/test_dcf_readiness_surface.py`
- `tests/test_professional_release_context.py`
- `tests/test_professional_mcp_contract.py`
- `tests/test_professional_mcp_live.py`

## Dependency And Worktree Map

Tasks 1 and 2 are the shared base and must be implemented sequentially.

After Task 2 passes, record the shared-base commit SHA and create:

| Worktree | Branch | Tasks | Owner |
|---|---|---|---|
| `.worktrees/professional-audit-effort` | `codex/professional-audit-effort` | 3 | Terra High |
| `.worktrees/professional-auditor` | `codex/professional-auditor` | 4–5 | Terra High |
| `.worktrees/professional-investor` | `codex/professional-investor` | 6–7 | Terra High |
| `.worktrees/professional-integration` | `codex/professional-integration` | 8–9 | Primary integrator |

Each slice owns one new module:
`professional_surfaces/audit_effort.py`,
`professional_surfaces/auditor.py`, or
`professional_surfaces/investor.py`. Central registry, catalog, and handler
registry additions are resolved in the integration worktree. Business-logic
conflicts are not deferred to integration: each lane owns distinct analysis
modules and may call legacy peer functions without editing them.

### Module-level dependency table

| Lane | Modules | Depends on |
|---|---|---|
| Shared base | `analysis/filing_provenance`, `mcp/contracts`, `mcp/professional_surfaces` | existing note-search integration |
| Audit effort | `analysis/audit_effort_inputs`, `mcp/handlers/audit_effort`, audit-effort surface | shared base |
| Auditor | `analysis/audit_reporting`, `analysis/auditor_decisions`, auditor surface | shared base |
| Investor | `analysis/financial_analysis`, `analysis/investor_peer_evidence`, `analysis/investor_quality`, DCF modules, investor surface | shared base |
| Integration | central handler/catalog registries, dispatch/resources, cross-slice tests | all three domain lanes |

### Parallel lanes

```text
Lane S: Task 1 -> Task 2                         shared, sequential
                    |
                    +--> Lane A: Task 3           audit effort
                    +--> Lane B: Task 4 -> Task 5 auditor
                    +--> Lane C: Task 6 -> Task 7 investor
                                      |
                                      v
                         Lane I: Task 8 -> Task 9 integration
```

Lane A, B, and C launch together after the shared-base SHA is recorded. The
integration lane starts only after all three slice reviews pass.

### Conflict flags

- Domain lanes must not edit shared `peer_benchmarks.py` during parallel work.
  Audit effort uses `audit_effort_inputs.py`; auditor decisions use
  `auditor_decisions.py`; investor peer enrichment uses
  `investor_peer_evidence.py`.
- Central handler and catalog registries are integration-owned except for the
  new audit-effort tool registration.
- Domain pack/rendering logic belongs in the three isolated surface modules;
  no slice may add new domain branches directly to central `answer_pack.py` or
  `renderers.py`.
- If a lane discovers that the legacy peer algorithm itself must change, it
  stops that sub-slice and records a shared-base follow-up instead of creating
  a semantic merge conflict for the integrator.

### Performance constraints

- Three-year audit-effort preparation uses at most one financial query, one
  audit-fee query, and one bounded filing-provenance query per requested year.
- Pack and renderer code performs no database access.
- Peer and financial rows are queried in batches, never once per displayed row.
- Release context performs a bounded manifest/readiness lookup once per public
  dispatch and is reused by envelope, pack, and resource generation.
- Tests use SQLAlchemy query counters to reject new per-row query behavior in
  audit-effort and financial-trend paths.

---

### Task 1: Canonical Status Truth Kernel And Surface Registries

**Files:**
- Modify: `kreports/mcp/contracts.py:15-264`
- Modify: `kreports/mcp/answer_pack.py:54-132`
- Modify: `kreports/mcp/renderers.py:1024-1255`
- Modify: `kreports/mcp/visual_contracts.py`
- Create: `kreports/mcp/professional_surfaces/__init__.py`
- Create: `kreports/mcp/professional_surfaces/audit_effort.py`
- Create: `kreports/mcp/professional_surfaces/auditor.py`
- Create: `kreports/mcp/professional_surfaces/investor.py`
- Create: `tests/test_professional_status_truth.py`
- Modify: `tests/test_mcp_contracts.py`
- Modify: `tests/test_mcp_answer_pack.py`
- Modify: `tests/test_mcp_narrative_renderers.py`

**Interfaces:**
- Consumes: any legacy MCP result dictionary.
- Produces:

```python
def normalize_answer_result(
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach one canonical quality status before pack or prose rendering."""
```

```python
class SectionSourceV1(BaseModel):
    source_label: str
    source_url: str
    rcept_no: str | None = None


class SectionStatusV1(BaseModel):
    status: Literal["usable", "limited", "missing", "error"]
    required: bool
    applicability: Literal["applicable", "not_applicable", "unknown"]
    coverage: dict[str, int | float | str | None] = Field(
        default_factory=dict,
    )
    blockers: list[str] = Field(default_factory=list)
    sources: list[SectionSourceV1] = Field(default_factory=list)
    not_applicable_basis: str | None = None


class DataQualityV1(BaseModel):
    status: Literal["usable", "limited", "missing", "error"]
    grade: Literal["A", "B", "C", "D"] | None = None
    dataset_version: str
    schema_version: str
    covered_years: list[int] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    section_statuses: dict[str, SectionStatusV1] = Field(default_factory=dict)


class AnswerEnvelopeV1(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    tool_name: str
    verdict: Literal["usable", "limited", "missing", "error"]
    domain_verdict: str | None = None
    answer: str
    confirmed_facts: list[dict[str, Any]]
    analysis: list[AnalysisItemV1]
    evidence: list[EvidenceRefV1]
    data_quality: DataQualityV1
    warnings: list[str]
    next_checks: list[str]
    answer_pack: dict[str, Any] | None = None
```

```python
PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]
PACK_BUILDERS: dict[str, PackBuilder]
DETAIL_RENDERERS: dict[str, DetailRenderer]
```

- Invariant: `enriched["data_quality"]["status"] == envelope.verdict ==
  answer_pack["summary"]["status"]`.
- Preservation invariant:
  `enriched["data_quality"]["section_statuses"]` is deep-equal to the same
  object under envelope data quality, answer-pack data quality, and the
  visualization resource. Presentation layers may not rebuild or summarize it
  into a different shape.

- [ ] **Step 1: Write failing canonical-status tests**

Create `tests/test_professional_status_truth.py` with:

```python
from kreports.mcp.contracts import enrich_answer_response


def test_enrichment_uses_one_canonical_status_across_layers():
    out = enrich_answer_response("compare_peer_risk_profile", {
        "verdict": "승인",
        "subject": {"corp_name": "A"},
        "subject_metrics": {"revenue": 100},
        "benchmarks": {"revenue": {"n": 10, "p50": 90}},
        "data_quality": {
            "status": "limited",
            "missing_fields": ["receivables"],
        },
    })

    assert out["data_quality"]["status"] == "limited"
    assert out["domain_verdict"] is None
    assert out["answer_pack"]["summary"]["status"] == "limited"
    assert "판정:\n- limited" in out["answer"]
    assert "승인" not in out["answer"]
```

Add tests proving:

- a cited complete result remains `usable`;
- an uncited confirmed fact downgrades `usable` to `limited`;
- a non-empty `limited` result never receives a `missing` availability pack;
- `missing` includes the cache-absence disclaimer;
- `error` remains `error`;
- old schema `1.0` payloads without `domain_verdict` still validate, and the
  optional additive field serializes without requiring a version bump;
- typed section statuses survive deep-equal across the normalized result,
  envelope, pack summary, and visualization resource;
- injected legacy verdicts `승인`, `거절`, `매수`, `매도`, and
  `적정 의견 확정` remain in the raw domain payload only and never appear as
  `domain_verdict` or professional prose;
- all three empty domain surface registries import without changing existing
  note-search behavior.

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py -q
```

Expected: failures because envelope schema 1.0 overloads `verdict`, enrichment
does not attach canonical quality before rendering, and the
professional surface registry does not exist.

- [ ] **Step 3: Add `domain_verdict` and canonical normalization**

In `contracts.py`, implement:

```python
_CANONICAL_STATUSES = {"usable", "limited", "missing", "error"}
DOMAIN_VERDICT_ALLOWLISTS = {
    "get_quality_of_earnings_pack": {"stable", "monitor"},
    "get_dcf_input_candidates": {"screen_grade", "partial", "blocked"},
    "build_dcf_model_pack": {
        "reviewable_model",
        "partial_model",
        "calculation_unavailable",
    },
    "prepare_standard_audit_hours_inputs": {"not_assessed"},
}


def normalize_answer_result(
    tool_name: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(result)
    quality = _data_quality(normalized)
    raw_verdict = str(
        normalized.get("domain_verdict")
        or normalized.get("verdict")
        or ""
    ).strip()
    allowed = DOMAIN_VERDICT_ALLOWLISTS.get(tool_name, set())
    if raw_verdict in allowed:
        normalized["domain_verdict"] = raw_verdict
    else:
        normalized["domain_verdict"] = None
    normalized["data_quality"] = quality.model_dump()
    normalized["quality_status"] = quality.status
    return normalized
```

Update `build_answer_envelope()` so `verdict` is always
`quality.status`, and `domain_verdict` is populated independently. Update
`enrich_answer_response()` to normalize first, then build the pack and answer
from that same dictionary. `_data_quality()` must validate a bounded dictionary
from `data_quality.section_statuses` as `SectionStatusV1`; malformed entries
become explicit limited blockers rather than passing through untyped.

Keep envelope schema `1.0`: `domain_verdict` and typed optional
`section_statuses` are additive fields. Do not require consumers to opt into a
new schema version. Preserve the original legacy `verdict` only inside the raw
domain payload for compatibility; neither the normalizer nor any renderer may
promote an unallowlisted value into professional judgment.

- [ ] **Step 4: Add isolated surface registries**

Create `professional_surfaces/__init__.py`:

```python
from .audit_effort import DETAIL_RENDERERS as AUDIT_EFFORT_DETAILS
from .audit_effort import PACK_BUILDERS as AUDIT_EFFORT_PACKS
from .auditor import DETAIL_RENDERERS as AUDITOR_DETAILS
from .auditor import PACK_BUILDERS as AUDITOR_PACKS
from .investor import DETAIL_RENDERERS as INVESTOR_DETAILS
from .investor import PACK_BUILDERS as INVESTOR_PACKS

PACK_BUILDERS = {
    **AUDIT_EFFORT_PACKS,
    **AUDITOR_PACKS,
    **INVESTOR_PACKS,
}
DETAIL_RENDERERS = {
    **AUDIT_EFFORT_DETAILS,
    **AUDITOR_DETAILS,
    **INVESTOR_DETAILS,
}
```

Each domain module initially contains typed empty dictionaries:

```python
from collections.abc import Callable
from typing import Any

PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]

PACK_BUILDERS: dict[str, PackBuilder] = {}
DETAIL_RENDERERS: dict[str, DetailRenderer] = {}
```

Merge `PACK_BUILDERS` into central answer-pack dispatch and
`DETAIL_RENDERERS` into central detail-renderer dispatch. Keep accounting-note
search routing higher priority than the generic fallback.

- [ ] **Step 5: Make prose and visualization consume canonical status**

Change `_render_professional_envelope()` to render:

```text
판정:
- {envelope.verdict}

업무 결론:
- {envelope.domain_verdict or "별도 결론 없음"}
```

Remove nested status recomputation from legacy detail renderers. A detail
renderer may describe `data_quality.section_statuses`, but it cannot emit a
different top-level verdict. Ensure `build_visualization_pack()` preserves the
provided summary status.

- [ ] **Step 6: Verify GREEN and note-search regression**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py \
  tests/test_accounting_note_answer_surface.py \
  tests/test_accounting_note_mcp_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add \
  kreports/mcp/contracts.py \
  kreports/mcp/answer_pack.py \
  kreports/mcp/renderers.py \
  kreports/mcp/visual_contracts.py \
  kreports/mcp/professional_surfaces \
  tests/test_professional_status_truth.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py
git commit -m "refactor: establish professional MCP status truth"
```

---

### Task 2: Same-Year Filing Provenance Resolver

**Files:**
- Create: `kreports/analysis/filing_provenance.py`
- Modify: `kreports/analysis/financial_analysis.py:19-75`
- Create: `tests/test_filing_provenance.py`
- Modify: `tests/test_api_evidence_packs.py`

**Interfaces:**
- Consumes: `corp_code`, business year, source table, optional FS basis.
- Produces:

```python
def annual_filing_source(
    corp_code: str,
    bsns_year: int,
    *,
    source_table: str,
    fs_div: str | None = None,
) -> dict[str, Any] | None:
    """Resolve the latest valid same-company, same-year annual filing."""
```

The returned dictionary contains:

```python
{
    "corp_code": "00126380",
    "corp_name": "삼성전자",
    "report_nm": "사업보고서 (2025.12)",
    "bsns_year": 2025,
    "rcept_no": "20260310002820",
    "section_title": "재무제표",
    "source_table": "financial_facts_compact",
    "fs_div": "CFS",
}
```

Invalid, cross-company, or cross-year receipts return `None`.

- [ ] **Step 1: Write failing provenance tests**

Use a temporary database with:

- two annual filings for the same company/year, where the later valid receipt
  wins;
- a synthetic attachment receipt whose parent resolves correctly;
- a receipt from another company that must not be borrowed;
- a year with financial facts but no filing, which returns `None`.

Assert `evidence_reference_fields(source)` returns a DART URL only for valid
results.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run --python 3.12.7 pytest tests/test_filing_provenance.py -q
```

Expected: import failure because `filing_provenance.py` does not exist.

- [ ] **Step 3: Implement deterministic source resolution**

Move the reusable behavior from `financial_analysis._annual_report_source` into
the new module. Query only annual `사업보고서` disclosures for the same company
and business year, prefer corrections/newer valid receipts, validate through
`parent_rcept_no()`, and return no source when identity or period cannot be
proven.

Do not accept a source-table name as evidence by itself.

- [ ] **Step 4: Replace the private investor helper**

Update `financial_analysis.py` to import and call
`annual_filing_source()`. Preserve the existing fact wording and source shape.

- [ ] **Step 5: Verify GREEN and evidence regression**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_filing_provenance.py \
  tests/test_api_evidence_packs.py \
  tests/test_accounting_note_mcp_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit and record shared-base SHA**

```bash
git add \
  kreports/analysis/filing_provenance.py \
  kreports/analysis/financial_analysis.py \
  tests/test_filing_provenance.py \
  tests/test_api_evidence_packs.py
git commit -m "feat: resolve structured facts to annual filings"
git rev-parse HEAD
```

Use the printed SHA as the base of the three parallel implementation
worktrees.

- [ ] **Step 7: Create the three parallel slice worktrees**

Run from `/Users/kjun/vault/01_Projects/kreports_dart_mcp`:

```bash
shared_base_sha="$(
  git -C .worktrees/professional-mcp-hardening-plan rev-parse HEAD
)"
test -n "$shared_base_sha"
git worktree add \
  .worktrees/professional-audit-effort \
  -b codex/professional-audit-effort \
  "$shared_base_sha"
git worktree add \
  .worktrees/professional-auditor \
  -b codex/professional-auditor \
  "$shared_base_sha"
git worktree add \
  .worktrees/professional-investor \
  -b codex/professional-investor \
  "$shared_base_sha"
```

Expected: all three worktrees start at the exact same SHA.

- [ ] **Step 8: Prepare identical Python 3.12.7 environments**

Run once in each new worktree:

```bash
uv sync --frozen --extra dev --python 3.12.7
uv run --python 3.12.7 pytest \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py -q
```

Expected: the shared baseline passes before slice-specific edits. Do not start a
slice from a worktree whose baseline fails.

---

### Task 3: Three-Year Standard Audit Hours Input Preparation

**Files:**
- Create: `kreports/analysis/audit_effort_inputs.py`
- Create: `kreports/mcp/handlers/audit_effort.py`
- Modify: `kreports/mcp/handlers/__init__.py`
- Modify: `kreports/mcp/input_models.py:179-190,423-430`
- Modify: `kreports/mcp/catalog.py:70-152`
- Modify: `kreports/mcp/professional_surfaces/audit_effort.py`
- Create: `tests/test_standard_audit_hours_inputs.py`
- Modify: `tests/test_auditor_peer_tools.py`
- Modify: `tests/test_mcp_catalog.py`
- Modify: `tests/test_all_tools_contract.py`
- Modify: `tests/test_dart_mcp.py`

**Interfaces:**
- New public tool:

```text
prepare_standard_audit_hours_inputs(
    company: str,
    year: int = 2025,
    fs_strategy: auto | CFS | OFS = auto
)
```

- Domain function:

```python
def prepare_standard_audit_hours_inputs(
    company: str,
    *,
    year: int = 2025,
    fs_strategy: str = "auto",
) -> dict[str, Any]:
    """Prepare three-year public inputs without calculating standard hours."""
```

- Required output:

```python
{
    "subject": {...},
    "requested_years": [2025, 2024, 2023],
    "fs_div_used": "CFS",
    "standard_audit_hours_assessment": "not_assessed",
    "rows": [{
        "year": 2025,
        "fs_div": "CFS",
        "total_assets": 1_000_000_000_000,
        "revenue": 750_000_000_000,
        "total_assets_100m": 10_000.0,
        "revenue_100m": 7_500.0,
        "audit_fee_m": 120,
        "audit_hours": 1_800,
        "hours_basis": "actual | contract | legacy_inferred | missing",
        "financial_source": {...} | None,
        "audit_source": {...} | None,
        "input_status": "usable | limited | missing",
        "missing_fields": [],
        "provenance_gaps": [],
    }],
    "data_quality": {
        "status": "usable | limited | missing",
        "covered_years": [...],
        "complete_years": [...],
        "missing_fields": [...],
        "limitations": [...],
    },
    "confirmed_facts": [...],
    "analysis": [...],
    "next_checks": [...],
}
```

- [ ] **Step 1: Write failing complete/incomplete fixture tests**

Create temporary-database tests for:

1. three complete, cited CFS years → input status `usable`;
2. 2023 fee/hours missing → canonical `limited`, exact missing fields preserved;
3. complete numbers but missing audit receipt → canonical `limited` and
   `uncitable` provenance gap;
4. CFS/OFS rows available → one basis selected for all three years;
5. actual and contract observations available → do not combine the fee from
   one basis with hours from another;
6. no rows → `missing` with cache-absence disclaimer;
7. `standard_audit_hours_assessment == "not_assessed"` in every non-error case.
8. a SQLAlchemy query counter stays at or below five queries for three years:
   one financial query, one audit-fee query, and at most one bounded provenance
   lookup per year.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run --python 3.12.7 pytest tests/test_standard_audit_hours_inputs.py -q
```

Expected: import and catalog failures because the domain function and public
tool do not exist.

- [ ] **Step 3: Implement the three-year query and fail-closed status**

Build `[year, year - 1, year - 2]`. Query `financials` with one `fs_div`, and
query `audit_fees` with schema-inspected optional columns:

```python
OPTIONAL_AUDIT_COLUMNS = (
    "actual_fee_m",
    "actual_hours",
    "contract_fee_m",
    "contract_hours",
    "compatibility_basis",
    "source_rcept_no",
    "source_class",
    "source_period",
)
```

Do not execute SQL that references a column absent from the live schema. Use
`annual_filing_source()` for financial provenance and a validated
`source_rcept_no` for audit provenance. Mark an otherwise complete row
`limited` when either material source is unresolved.

- [ ] **Step 4: Add the public tool**

Add `PrepareStandardAuditHoursInputsInput`, the handler, catalog description,
handler registration, and approved tool count `33`.

The description must say:

```text
표준감사시간 산정 전 공개자료 입력 준비 도구. 최근 3개년 총자산,
매출액, 감사보수, 감사시간과 누락·출처 상태를 반환하며 표준감사시간
또는 법정 산정값을 계산하지 않는다.
```

- [ ] **Step 5: Add the audit-effort pack and chatbot renderer**

Populate `professional_surfaces/audit_effort.py` with builders for:

- `prepare_standard_audit_hours_inputs`;
- `compare_peer_audit_fees`;
- `estimate_audit_hours_proxy`.

Use a core table:

```text
연도 | FS | 총자산(억원) | 매출(억원) | 감사보수(백만원) |
감사시간 | 기준 | 입력상태 | 미확보 항목
```

The chatbot first line after verdict must say:

```text
표준감사시간 결론: 산정하지 않음
```

Show all three subject rows before the peer table. Do not invent a receipt for
uncitable audit-fee rows.

- [ ] **Step 6: Expose a composable audit-effort helper**

Expose `prepare_standard_audit_hours_inputs()` for the existing fee comparison
and the later auditor-decision assembly to call after integration. The
audit-effort lane must not edit acceptance assembly or
`peer_benchmarks.py`. Attach `subject_scale_history` and its quality summary in
its own public tool and surface without duplicating SQL or status logic.

At integration, the acceptance canonical status is the worst
purpose-relevant status; a missing oldest-year fee/hour row makes the
audit-effort section `limited` but does not erase usable KAM or audit-history
sections.

- [ ] **Step 7: Verify GREEN and public registration**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_standard_audit_hours_inputs.py \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_catalog.py \
  tests/test_all_tools_contract.py \
  tests/test_dart_mcp.py -q
```

Expected: all selected tests pass and public tool count is 33.

- [ ] **Step 8: Commit**

```bash
git add \
  kreports/analysis/audit_effort_inputs.py \
  kreports/mcp/handlers/audit_effort.py \
  kreports/mcp/handlers/__init__.py \
  kreports/mcp/input_models.py \
  kreports/mcp/catalog.py \
  kreports/mcp/professional_surfaces/audit_effort.py \
  tests/test_standard_audit_hours_inputs.py \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_catalog.py \
  tests/test_all_tools_contract.py \
  tests/test_dart_mcp.py
git commit -m "feat: prepare three-year audit effort inputs"
```

---

### Task 4: Acceptance, Peer Risk, And Auditor History Decision Surfaces

**Files:**
- Create: `kreports/analysis/auditor_decisions.py`
- Modify: `kreports/analysis/audit_reporting.py:909-959`
- Modify: `kreports/mcp/handlers/auditor.py`
- Modify: `kreports/mcp/professional_surfaces/auditor.py`
- Create: `tests/test_auditor_decision_surfaces.py`
- Modify: `tests/test_auditor_peer_tools.py`
- Modify: `tests/test_mcp_answer_pack.py`
- Modify: `tests/test_mcp_narrative_responses.py`

**Interfaces:**
- Acceptance requirement:

```python
class AcceptanceRequirementV1(BaseModel):
    section_key: Literal[
        "peer_group",
        "audit_effort",
        "financial_risk",
        "audit_history",
        "accounting_policy",
        "kam",
        "audit_report_matters",
    ]
    required: bool
    applicability: Literal["applicable", "not_applicable", "unknown"]
    minimum_coverage: dict[str, int | float | bool | str]


def build_acceptance_evidence(
    *,
    legacy_payload: dict[str, Any],
    audit_effort_section: SectionStatusV1 | None,
    audit_effort_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build seven sections; absent injected effort evidence stays limited."""
```

- `compare_peer_risk_profile` produces a top-level canonical status, confirmed
  subject facts, metric-specific peer denominators, and limitations.
- `get_audit_history` produces:

```python
{
    "history": [{
        "year": 2025,
        "fs_div": "CFS",
        "auditor_nm": "삼정회계법인",
        "audit_opinion": "적정",
        "auditor_changed": False,
        "consecutive_years": 3,
        "rcept_no": "20260310002820",
    }],
    "data_quality": {...},
    "confirmed_facts": [...],
    "next_checks": [...],
}
```

- `build_audit_acceptance_pack` produces section-level rows:

```text
검토영역 | 상태 | 확인 사실 | 값/coverage | 접수번호 | 필수 후속 확인
```

- [ ] **Step 1: Write failing public-surface tests**

Use synthetic fixtures to assert:

- non-empty peer risk cannot become a `missing` pack;
- raw enriched quality, answer verdict, pack summary, and detail status match;
- metric rows show peer `n`, P25, P50, P75, subject value, and limitation;
- auditor history retains a change year, opinion, tenure, and receipt;
- audit history pack never uses an audit-fee title;
- acceptance preserves policy, KAM, matter, risk, and audit-effort section
  statuses without flattening them to empty availability;
- each of the seven acceptance requirements applies its exact minimum coverage;
- a KAM row count without `semantic_complete=True` remains `limited`;
- a not-applicable section is accepted only with a filing-backed
  `not_applicable_basis`;
- no acceptance answer contains “승인”, “거절”, or an audit conclusion;
- internal keys such as `kam_body` and
  `audit_report_other_matter_paragraph_present` do not reach the chatbot.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_auditor_decision_surfaces.py \
  tests/test_auditor_peer_tools.py -q
```

Expected: failures for missing top-level statuses, missing facts, wrong history
builder, and internal signal labels.

- [ ] **Step 3: Enrich peer-risk results through the decision wrapper**

In `auditor_decisions.py`, call the legacy `compare_peer_risk_profile()` and
enrich its returned payload without editing `peer_benchmarks.py`:

- compute `data_quality.status` from required subject values and metric
  coverage;
- expose `data_quality.section_statuses.metric_coverage`;
- add cited subject facts where annual filing provenance resolves;
- keep uncited peer aggregates visible but label their cohort provenance;
- add next checks for missing receivables, inventory, cash-flow, Beneish, or
  event inputs.

Do not translate a percentile into an audit-risk conclusion.

- [ ] **Step 4: Normalize auditor history**

Add `auditor_changed` deterministically by comparing adjacent same-FS rows.
Build one confirmed fact per receipt-linked year. Use a dedicated audit-history
pack:

```text
연도 | FS | 감사인 | 감사의견 | 변경 여부 | 연속감사연수 | 접수번호
```

If history exists but any material row is uncitable, use `limited`; if no rows
exist, use `missing`.

- [ ] **Step 5: Build an acceptance evidence matrix**

Replace internal signal strings with public labels and explicit bases:

```python
PUBLIC_ACCEPTANCE_LABELS = {
    "non_audit_fee_exceeds_audit_fee": "비감사보수가 감사보수를 초과하여 독립성 검토가 필요합니다.",
    "loss_based_going_concern_flag": "손실·현금흐름 기반 계속기업 스크리닝 신호가 있습니다.",
    "audit_report_emphasis_paragraph_present": "감사보고서 강조사항 문단이 확인됩니다.",
    "audit_report_going_concern_paragraph_present": "계속기업 관련 문단이 확인됩니다.",
}
```

Build `data_quality.section_statuses` for peer group, audit effort, financial
risk, audit history, policy, KAM, and audit-report matters. Apply this exact
requirement matrix:

| Section | Minimum for `usable` |
|---|---|
| peer group | selection basis plus at least 5 included peers |
| audit effort | 3 requested years complete and cited |
| financial risk | required subject metrics plus at least 5 observations per required peer metric |
| audit history | current and prior year both receipt-linked |
| accounting policy | current-period subject policy plus filing source |
| KAM | current filing source and `semantic_complete=True`, or cited not-applicable basis |
| audit-report matters | current audit-report source coverage; zero classified matters only when section classification is proven complete |

Required applicable sections must meet their minimum. `unknown` applicability
is `limited`. `not_applicable` is accepted only when
`not_applicable_basis` and at least one source are present. Because Task 5 owns
the semantic reducer, Task 4 defaults absent `semantic_complete` to false; row
presence alone can never promote KAM to `usable`. Because Task 3 runs in
parallel, Task 4 accepts audit-effort evidence through the explicit function
arguments above. `None` or legacy-only audit-effort payloads always produce a
`limited` blocker named `audit_effort_helper_not_integrated`; they never infer
three-year completeness.

- [ ] **Step 6: Add domain pack builders and renderers**

Populate `professional_surfaces/auditor.py` with dedicated builders/renderers
for:

- `get_audit_history`;
- `compare_peer_risk_profile`;
- `build_audit_acceptance_pack`.

The chatbot acceptance table is limited to the seven review areas. Full metric
and peer tables remain in `answer_pack`.

- [ ] **Step 7: Verify GREEN**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_auditor_decision_surfaces.py \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_responses.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add \
  kreports/analysis/auditor_decisions.py \
  kreports/analysis/audit_reporting.py \
  kreports/mcp/handlers/auditor.py \
  kreports/mcp/professional_surfaces/auditor.py \
  tests/test_auditor_decision_surfaces.py \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_responses.py
git commit -m "feat: make audit acceptance evidence decision-ready"
```

---

### Task 5: Audit Opinion, Matter, And KAM Semantic Guards

**Files:**
- Modify: `kreports/analysis/audit_reporting.py:509-860,960-1430`
- Modify: `kreports/analysis/auditor_decisions.py`
- Modify: `kreports/mcp/professional_surfaces/auditor.py`
- Create: `tests/test_audit_report_semantics.py`
- Modify: `tests/test_api_evidence_packs.py`
- Modify: `tests/test_auditor_peer_tools.py`

**Interfaces:**
- Section-specific interpretation:

```python
def audit_section_guidance(section_key: str) -> tuple[list[dict], list[str]]:
    """Return category-specific analysis and next checks."""
```

- KAM coverage:

```python
{
    "timeline_status": "usable | limited | missing",
    "semantic_complete": bool,
    "topic_coverage": {"available": int, "total": int, "status": str},
    "reason_coverage": {"available": int, "total": int, "status": str},
    "procedure_coverage": {"available": int, "total": int, "status": str},
}
```

- [ ] **Step 1: Write failing category and KAM tests**

Assert:

- `audit_opinion` does not receive KAM guidance;
- `basis_for_opinion` does not become `other_matter`;
- a generic responsibilities/communication paragraph cannot create an
  acceptance matter signal;
- an opinion-basis excerpt without a parsed opinion conclusion remains
  `limited`;
- KAM timeline rows with zero topic/reason/procedure coverage are `limited`;
- KAM facts retain their receipt and excerpt even when semantic coverage is
  limited;
- peer KAM answer and pack use the same canonical status.
- `semantic_complete` is false unless current-period topic, reason, procedure,
  and receipt-linked source coverage all meet the requirement.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run --python 3.12.7 pytest tests/test_audit_report_semantics.py -q
```

Expected: failures because the current evidence helpers use KAM guidance for
multiple section types and KAM lifecycle can be `usable` without semantic
coverage.

- [ ] **Step 3: Add section-key-specific guidance**

Use explicit mappings:

```python
SECTION_GUIDANCE = {
    "audit_opinion": (
        "감사의견 문구와 대상 재무제표 범위를 원문에서 확인해야 합니다.",
        "의견 유형, 계속기업 문단, 강조사항을 각각 분리해 확인하세요.",
    ),
    "basis_for_opinion": (
        "의견근거 문단은 감사기준 준수와 독립성 진술을 확인하는 근거입니다.",
        "실제 의견 결론과 함께 읽고 KAM 또는 기타사항으로 재분류하지 마세요.",
    ),
    "kam": (
        "KAM 선정 이유와 수행 감사절차를 구분해 확인해야 합니다.",
        "관련 주석, 금액, 경영진 추정 및 절차 대응을 대조하세요.",
    ),
    "emphasis": (
        "강조사항은 의견 변형 여부와 강조 대상 주석을 함께 확인해야 합니다.",
        "강조 대상 주석과 후속 공시를 확인하세요.",
    ),
    "going_concern": (
        "계속기업 문단은 중요한 불확실성의 성격과 공시 적정성을 확인해야 합니다.",
        "현금흐름 전망, 차입약정, 자금조달 계획을 확인하세요.",
    ),
}
```

- [ ] **Step 4: Harden matter classification**

Require the classified source section and non-boilerplate excerpt before
creating an acceptance signal. Keep `basis_for_opinion`, `other_matter`,
`emphasis`, and `going_concern` as mutually explicit categories.

Do not infer a modified opinion from a basis paragraph alone.

- [ ] **Step 5: Compute KAM coverage truthfully**

Separate timeline existence from semantic completeness. A timeline with rows
but zero topics, reasons, or procedures is `limited`. Preserve evidence rows,
sources, and exact coverage denominators in the pack.

Set `semantic_complete=True` only when the current-period KAM population is
classified completely and every material item has topic, reason, procedure,
and validated source coverage. Feed that explicit flag to
`auditor_decisions.py`, then recompute the KAM acceptance section. Missing or
false flags stay `limited`; the acceptance wrapper must not derive the flag
from row counts.

Add dedicated KAM tables:

```text
연도 | KAM 주제 | 반복/신규 | 선정 이유 확보 | 감사절차 확보 | 접수번호
```

and:

```text
coverage 항목 | 확보 건수 | 전체 건수 | 상태
```

Populate the auditor surface registry for:

- `get_audit_report_sections`;
- `search_audit_report_matters`;
- `compare_peer_audit_report_matters`;
- `get_kam_lifecycle`;
- `compare_peer_kam_topics`.

- [ ] **Step 6: Verify GREEN**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_audit_report_semantics.py \
  tests/test_api_evidence_packs.py \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_narrative_renderers.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add \
  kreports/analysis/audit_reporting.py \
  kreports/analysis/auditor_decisions.py \
  kreports/mcp/professional_surfaces/auditor.py \
  tests/test_audit_report_semantics.py \
  tests/test_api_evidence_packs.py \
  tests/test_auditor_peer_tools.py \
  tests/test_mcp_narrative_renderers.py
git commit -m "fix: separate audit report evidence semantics"
```

---

### Task 6: Financial Trend, Peer, And Investor Check Surfaces

**Files:**
- Modify: `kreports/analysis/financial_analysis.py:261-658`
- Create: `kreports/analysis/investor_peer_evidence.py`
- Modify: `kreports/mcp/handlers/company.py`
- Modify: `kreports/mcp/handlers/search.py`
- Modify: `kreports/mcp/handlers/investor.py`
- Modify: `kreports/mcp/professional_surfaces/investor.py`
- Create: `tests/test_investor_decision_surfaces.py`
- Modify: `tests/test_compare_industry_multi.py`
- Modify: `tests/test_peer_cohorts.py`
- Modify: `tests/test_api_evidence_packs.py`
- Modify: `tests/test_mcp_narrative_renderers.py`

**Interfaces:**
- Investor check:

```python
CheckStatus = Literal["pass", "fail", "unknown"]


def evaluate_investor_check(
    *,
    name: str,
    value: float | None,
    predicate: Callable[[float], bool],
    meaning: str,
) -> dict[str, Any]:
    """Return pass/fail/unknown without converting None to fail."""
```

- Financial trend row:

```text
year | fs_div | revenue | operating_profit | net_income | operating_cf |
revenue_growth | operating_margin | source
```

- Peer metric row:

```text
year | fs_div | metric | unit | subject_value | percentile |
p25 | p50 | p75 | metric_n | cohort_n | missing_n | cohort_digest
```

- [ ] **Step 1: Write failing investor-surface tests**

Assert:

- five annual financial rows reach the financial snapshot pack and a bounded
  chatbot table;
- `None` FCF and CFO/NI checks are `unknown`, not `fail`;
- `evaluated_count`, `unknown_count`, and `coverage_status` are exact;
- a supportive takeaway is prohibited when a required cash-conversion check is
  unknown;
- peer-selection table retains company name, KSIC, scale, and include reason;
- multi-year peer rows show units, metric denominator, cohort denominator, and
  deterministic cohort digest;
- forty existing peer rows cannot produce zero sources without a provenance
  limitation;
- cached event classification is labeled a screening classification, not a
  confirmed control change;
- `search_disclosure_events` confirms only filing title/date/receipt while its
  `event_type` remains an explicitly labeled KReports screening classification.
- a SQLAlchemy query counter shows constant batch-query count as peer and
  financial row counts increase; any query count proportional to displayed
  rows fails the test.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run --python 3.12.7 pytest tests/test_investor_decision_surfaces.py -q
```

Expected: failures because financial rows and peer details are dropped, and
missing checks are currently represented as false values.

- [ ] **Step 3: Preserve financial trend rows and sources**

Return normalized five-year rows with units and per-year filing provenance.
Add a financial-trend pack:

```text
연도 | FS | 매출 | 영업이익 | 순이익 | 영업현금흐름 |
매출성장률 | 영업이익률
```

Show at most five annual rows in the chatbot and keep all requested years in
the pack.

- [ ] **Step 4: Implement three-state investor checks**

Replace boolean-only check construction with `pass`, `fail`, or `unknown`.
Compute:

```python
evaluated_count = sum(check["status"] in {"pass", "fail"} for check in checks)
unknown_count = sum(check["status"] == "unknown" for check in checks)
passed_count = sum(check["status"] == "pass" for check in checks)
```

Allow `quality_profile_supportive` only when every required cash-conversion
check is evaluated and the documented threshold is met.

- [ ] **Step 5: Preserve peer selection and peer metric provenance**

In `investor_peer_evidence.py`, call the legacy peer selector and multi-year
comparison, then enrich their returned payloads without editing
`peer_benchmarks.py`. Compute a deterministic cohort digest from sorted company
identifiers, year, FS basis, and selection policy. Do not render the
identifiers in the chatbot. Expose `cohort_n`, metric-specific `n`, and the
difference as missing/excluded coverage.

Resolve the subject filing source for each annual metric. If peer receipts are
not individually available, keep the aggregate `limited` and explain the
cohort provenance gap.

- [ ] **Step 6: Add investor pack builders and renderers**

Populate `professional_surfaces/investor.py` for:

- `get_financial_snapshot`;
- `select_peer_group`;
- `compare_to_industry_multi`;
- `get_investor_signals`;
- `search_disclosure_events`.

Use Korean public labels. The investor signal chatbot includes one checks table
and one short risk/event summary, not raw factor dictionaries.

- [ ] **Step 7: Verify GREEN**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_investor_decision_surfaces.py \
  tests/test_compare_industry_multi.py \
  tests/test_peer_cohorts.py \
  tests/test_api_evidence_packs.py \
  tests/test_mcp_narrative_renderers.py \
  tests/test_mcp_answer_pack.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add \
  kreports/analysis/financial_analysis.py \
  kreports/analysis/investor_peer_evidence.py \
  kreports/mcp/handlers/company.py \
  kreports/mcp/handlers/search.py \
  kreports/mcp/handlers/investor.py \
  kreports/mcp/professional_surfaces/investor.py \
  tests/test_investor_decision_surfaces.py \
  tests/test_compare_industry_multi.py \
  tests/test_peer_cohorts.py \
  tests/test_api_evidence_packs.py \
  tests/test_mcp_narrative_renderers.py
git commit -m "feat: preserve investor trend and peer evidence"
```

---

### Task 7: Quality-Of-Earnings And DCF Readiness Contracts

**Files:**
- Modify: `kreports/analysis/investor_quality.py:18-170`
- Modify: `kreports/analysis/dcf_inputs.py:28-180`
- Modify: `kreports/analysis/dcf_model.py`
- Modify: `kreports/analysis/financial_analysis.py:730-996`
- Modify: `kreports/mcp/professional_surfaces/investor.py`
- Create: `tests/test_dcf_readiness_surface.py`
- Modify: `tests/test_dcf_model.py`
- Modify: `tests/test_dcf_model_tool.py`
- Modify: `tests/test_api_evidence_packs.py`
- Modify: `tests/test_mcp_answer_pack.py`

**Interfaces:**
- DCF candidate result:

```python
{
    "candidate_status": "usable | limited | missing",
    "valuation_readiness": "ready | blocked",
    "valuation_blockers": [{
        "field": "wacc",
        "kind": "analyst_input_missing | source_fact_missing",
        "impact": "기업가치 할인 계산 불가",
        "owner": "analyst",
        "next_action": "자본구조·무위험수익률·베타·시장위험프리미엄으로 WACC를 산정하세요.",
    }],
}
```

- DCF model result:

```python
{
    "calculation_status": "calculated | unavailable",
    "domain_verdict": "reviewable_model | partial_model | calculation_unavailable",
    "enterprise_value": Decimal | None,
}
```

- QoE audit-matter summary:

```python
{
    "unique_receipt_count": int,
    "section_count": int,
    "dedupe_basis": "parent_rcept_no + matter_type + normalized_excerpt",
    "groups": [...],
}
```

- [ ] **Step 1: Write failing DCF and QoE tests**

Assert:

- candidate history can be `candidate_status=usable` while
  `valuation_readiness=blocked`;
- WACC, working capital, terminal growth, and missing source facts have exact
  blocker kinds, impacts, owners, and next actions;
- negative effective tax observations are identified and not silently used
  without a disclosed policy;
- `enterprise_value is None` forces
  `calculation_status=unavailable`;
- an unavailable model starts with `산출 불가`;
- unavailable EV/equity rows and valuation charts are absent;
- QoE matter count separates unique receipts from section rows and carries
  receipt-level sources;
- a latest financial filing link does not purport to source all audit matters.

- [ ] **Step 2: Verify RED**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_dcf_readiness_surface.py \
  tests/test_dcf_model.py \
  tests/test_dcf_model_tool.py -q
```

Expected: failures because readiness dimensions and receipt-level QoE matter
provenance are absent.

- [ ] **Step 3: Split candidate status from valuation readiness**

Keep candidate status based on historical observation coverage. Set valuation
readiness to `ready` only when every required model input and source fact is
available. Add one blocker object per unresolved field.

For effective tax observations:

- retain the raw observation;
- label negative or greater-than-one values as outliers;
- exclude outliers from the candidate median;
- disclose included and excluded observation counts.

- [ ] **Step 4: Fail closed on unavailable model calculation**

After the domain model is built:

```python
calculation_status = (
    "calculated"
    if result.enterprise_value is not None
    else "unavailable"
)
```

When unavailable:

- set domain verdict `calculation_unavailable`;
- keep source and assumption readiness tables;
- omit valuation bridge, sensitivity chart, EV, and equity-value rows;
- show exact missing accounts with year and FS basis.

- [ ] **Step 5: Add receipt-level QoE matter aggregation**

Query parent receipt, matter type, severity, year, and normalized excerpt.
Deduplicate by the documented key and return both unique receipt count and raw
section count. Link each displayed group to its audit-report receipt.

- [ ] **Step 6: Add DCF/QoE pack builders and chatbot copy**

Populate the investor surface registry for:

- `get_quality_of_earnings_pack`;
- `get_dcf_input_candidates`;
- `build_dcf_model_pack`.

Required opening copy:

```text
DCF 입력 후보 상태: {candidate_status}
가치평가 준비도: {valuation_readiness}
```

or, for an unavailable model:

```text
산출 불가: 필수 입력 또는 공시 실제값이 부족하여 기업가치를 계산하지 않았습니다.
```

- [ ] **Step 7: Verify GREEN**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_dcf_readiness_surface.py \
  tests/test_dcf_model.py \
  tests/test_dcf_model_tool.py \
  tests/test_api_evidence_packs.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit**

```bash
git add \
  kreports/analysis/investor_quality.py \
  kreports/analysis/dcf_inputs.py \
  kreports/analysis/dcf_model.py \
  kreports/analysis/financial_analysis.py \
  kreports/mcp/professional_surfaces/investor.py \
  tests/test_dcf_readiness_surface.py \
  tests/test_dcf_model.py \
  tests/test_dcf_model_tool.py \
  tests/test_api_evidence_packs.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_renderers.py
git commit -m "fix: distinguish DCF candidates from valuation readiness"
```

---

### Task 8: Bounded Release Context And Cross-Slice Integration

**Files:**
- Modify: `kreports/mcp/resources.py:260-390`
- Modify: `kreports/mcp/dispatch.py:90-150,249-340`
- Modify: `kreports/mcp/contracts.py`
- Modify: `kreports/mcp/answer_pack.py`
- Modify: `kreports/mcp/renderers.py`
- Modify: `kreports/mcp/handlers/__init__.py`
- Modify: `kreports/mcp/handlers/auditor.py`
- Modify: `kreports/mcp/catalog.py`
- Modify: `kreports/analysis/auditor_decisions.py`
- Create: `tests/test_professional_release_context.py`
- Modify: `tests/test_professional_status_truth.py`
- Modify: `tests/test_all_tools_contract.py`

**Interfaces:**
- Release context:

```python
class ReleaseContextV1(BaseModel):
    release_ready: bool
    manifest_available: bool
    required_failures: list[str] = Field(max_length=10)
    degraded_features: list[str] = Field(max_length=10)
    snapshot_version: str | None = None
```

`release_context` describes deployment/data readiness and cannot change the
question-level `data_quality.status`.

- [ ] **Step 1: Create the integration worktree from the shared-base SHA**

Run from the repository root:

```bash
shared_base_sha="$(
  git rev-parse codex/professional-mcp-hardening-plan
)"
test -n "$shared_base_sha"
git worktree add \
  .worktrees/professional-integration \
  -b codex/professional-integration \
  "$shared_base_sha"
```

Confirm that `shared_base_sha` equals the SHA recorded at Task 2 execution
before continuing.
Verify the branch and worktree:

```bash
git -C .worktrees/professional-integration status --short --branch
```

Expected: clean integration branch.

- [ ] **Step 2: Integrate the three verified slice commit series**

Cherry-pick the audit-effort, auditor, and investor commits in that order.
Resolve only:

- handler registry additions;
- catalog/tool-count additions;
- professional surface registry imports.

Do not resolve conflicts by dropping tests or reverting canonical status
behavior. `peer_benchmarks.py` must be unchanged by all three domain commits;
any semantic conflict there means a lane violated ownership and must be fixed
at its source before integration.

- [ ] **Step 3: Wire three-year audit effort into acceptance**

After the audit-effort commit is present, update the actual
`build_audit_acceptance_pack` handler/wrapper path to call
`prepare_standard_audit_hours_inputs()` exactly once and pass its typed section
status and three annual rows into `build_acceptance_evidence()`.

Add integration assertions using an oldest-year fee/hours-missing fixture:

- acceptance `section_statuses["audit_effort"].status == "limited"`;
- all three rows and their receipt/provenance fields reach the acceptance pack;
- the top-level acceptance status is `limited`;
- bypassing the helper or supplying only the legacy one-year payload fails with
  `audit_effort_helper_not_integrated`;
- a query spy proves the helper is called once per acceptance request.

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_standard_audit_hours_inputs.py \
  tests/test_auditor_decision_surfaces.py -q
```

Expected: all selected tests pass through the integrated public handler path.

- [ ] **Step 4: Write failing release-context tests**

Assert:

- manifest unavailable produces
  `manifest_available=False`, not an exception;
- `release_ready=False` is preserved;
- required failures and degraded features are bounded and user-facing;
- a question-level `usable` result remains `usable` when release readiness is
  false;
- release context does not appear as a confirmed filing fact;
- answer, pack, and resource expose identical release-context values.

- [ ] **Step 5: Verify RED**

Run:

```bash
uv run --python 3.12.7 pytest tests/test_professional_release_context.py -q
```

Expected: failures because professional envelopes do not yet carry bounded
release context.

- [ ] **Step 6: Implement bounded release context**

Reuse the existing dataset-readiness query and return only:

```python
{
    "release_ready": bool,
    "manifest_available": bool,
    "required_failures": list[:10],
    "degraded_features": list[:10],
    "snapshot_version": str | None,
}
```

Attach it once in dispatch metadata and copy it into the answer envelope and
pack. Do not recalculate release readiness in renderers.

If readiness lookup fails, return:

```python
{
    "release_ready": False,
    "manifest_available": False,
    "required_failures": ["release_context_unavailable"],
    "degraded_features": [],
    "snapshot_version": None,
}
```

- [ ] **Step 7: Run cross-slice focused tests**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_status_truth.py \
  tests/test_filing_provenance.py \
  tests/test_standard_audit_hours_inputs.py \
  tests/test_auditor_decision_surfaces.py \
  tests/test_audit_report_semantics.py \
  tests/test_investor_decision_surfaces.py \
  tests/test_dcf_readiness_surface.py \
  tests/test_professional_release_context.py \
  tests/test_accounting_note_mcp_contract.py -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Commit integration and release context**

```bash
git add \
  kreports/mcp/resources.py \
  kreports/mcp/dispatch.py \
  kreports/mcp/contracts.py \
  kreports/mcp/answer_pack.py \
  kreports/mcp/renderers.py \
  kreports/mcp/handlers/__init__.py \
  kreports/mcp/handlers/auditor.py \
  kreports/mcp/catalog.py \
  kreports/analysis/auditor_decisions.py \
  tests/test_professional_release_context.py \
  tests/test_professional_status_truth.py \
  tests/test_all_tools_contract.py
git commit -m "feat: attach bounded professional release context"
```

---

### Task 9: Public-Path, Live Immutable, And Full Regression Verification

**Files:**
- Create: `tests/test_professional_mcp_contract.py`
- Create: `tests/test_professional_mcp_live.py`
- Modify: `pyproject.toml`
- Modify: `docs/deploy-http-mcp.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: public `call_tool()`, `dispatch_tool()`, and low-level stdio MCP
  `handle_call_tool`.
- Produces: exact parity evidence and a documented professional response
  contract.

- [ ] **Step 1: Add all-tool canonical status tests**

For every public tool result that is not an input-validation error, assert:

```python
assert out["data_quality"]["status"] in {
    "usable", "limited", "missing", "error",
}
if out.get("answer_pack"):
    assert (
        out["answer_pack"]["summary"]["status"]
        == out["data_quality"]["status"]
    )
assert out["answer"].startswith("판정:")
```

For every priority tool define one mandatory pack contract. Assert:

- `answer_pack is not None`;
- the exact required table ID exists;
- its material row count is greater than zero when the domain payload is
  non-empty;
- its source count and confirmed-fact count satisfy that tool's declared
  minimum or the result carries an explicit source blocker and is `limited`;
- the chatbot answer contains the Korean heading for the core table.

An `availability` fallback never satisfies a priority-tool contract.

Use this exact priority matrix:

| Tool | Required table ID | Material rows |
|---|---|---|
| `prepare_standard_audit_hours_inputs` | `standard_audit_hours_inputs` | requested-year rows |
| `compare_peer_audit_fees` | `peer_audit_fee_benchmark` | subject plus peer rows |
| `estimate_audit_hours_proxy` | `audit_hours_proxy_inputs` | proxy-driver rows |
| `build_audit_acceptance_pack` | `acceptance_requirements` | seven requirement rows |
| `compare_peer_risk_profile` | `peer_risk_metrics` | required metric rows |
| `get_audit_history` | `audit_history` | annual history rows |
| `get_audit_report_sections` | `audit_report_sections` | classified section rows |
| `search_audit_report_matters` | `audit_report_matters` | matched matter rows |
| `compare_peer_audit_report_matters` | `peer_audit_report_matters` | subject/peer matter rows |
| `get_kam_lifecycle` | `kam_timeline` | annual KAM rows |
| `compare_peer_kam_topics` | `peer_kam_topics` | subject/peer topic rows |
| `get_financial_snapshot` | `financial_trend` | requested annual rows |
| `select_peer_group` | `peer_selection` | included peer rows |
| `compare_to_industry_multi` | `industry_metrics` | metric-year rows |
| `get_investor_signals` | `investor_checks` | required check rows |
| `search_disclosure_events` | `disclosure_events` | matched event rows |
| `get_quality_of_earnings_pack` | `quality_of_earnings` | annual QoE rows |
| `get_dcf_input_candidates` | `dcf_candidates` | actual/candidate rows |
| `build_dcf_model_pack` | `dcf_model_readiness` | input/blocker rows |

- [ ] **Step 2: Add synthetic golden professional workflows**

Create default, synthetic black-box tests in
`tests/test_professional_mcp_contract.py` for:

- audit-effort preparation with one missing year;
- acceptance pack with usable KAM and limited fee history;
- auditor change plus unmodified opinion history;
- financial trend plus peer comparison;
- investor checks containing unknown values;
- QoE matter receipts;
- DCF candidates usable but valuation blocked;
- DCF calculation unavailable.

Assert exact status parity, table IDs, row counts, source counts, and prohibited
phrases through all three public boundaries:

1. `call_tool()` JSON;
2. `dispatch_tool()` answer envelope;
3. low-level stdio `handle_call_tool` response content.

The normalized `data_quality.section_statuses` objects must be deep-equal
across enriched result, envelope, answer pack, and visualization resource.

- [ ] **Step 3: Verify the full focused contract suite**

Run:

```bash
uv run --python 3.12.7 pytest \
  tests/test_professional_mcp_contract.py \
  tests/test_all_tools_contract.py \
  tests/test_mcp_catalog.py \
  tests/test_dart_mcp.py \
  tests/test_mcp_contracts.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_narrative_responses.py \
  tests/test_mcp_narrative_renderers.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Add an explicit opt-in live test boundary**

Register the marker:

```toml
[tool.pytest.ini_options]
markers = [
  "live: requires explicit KREPORTS_LIVE_DB and immutable local DB",
]
```

Create `tests/test_professional_mcp_live.py` with
`@pytest.mark.live`. Skip it unless `KREPORTS_LIVE_DB` is explicitly set to an
absolute, existing regular file. Do not discover a database via a repository
symlink or default path. The fixture calculates SHA-256 before and after the
session and fails on any difference.

Default `uv run --python 3.12.7 pytest -q` therefore runs the synthetic
contract and skips the live test when the environment variable is absent.

- [ ] **Step 5: Run read-only Samsung professional probes**

Invoke the opt-in suite explicitly:

```bash
KREPORTS_LIVE_DB=/absolute/read-only/kreports.db \
  uv run --python 3.12.7 pytest \
  -m live tests/test_professional_mcp_live.py -q -s
```

Within that suite, call these public tools for `005930`, FY2025:

```text
prepare_standard_audit_hours_inputs
compare_peer_audit_fees
build_audit_acceptance_pack
compare_peer_risk_profile
get_audit_history
get_audit_report_sections
search_audit_report_matters
compare_peer_audit_report_matters
get_kam_lifecycle
compare_peer_kam_topics
get_financial_snapshot
compare_to_industry_multi
get_investor_signals
search_disclosure_events
get_quality_of_earnings_pack
get_dcf_input_candidates
build_dcf_model_pack
```

For each result record:

```text
tool | canonical status | domain verdict | fact count | evidence count |
pack status | table ids | source count | first answer paragraph
```

Required observations:

- 2023 audit fee/hours remain missing;
- standard-hours assessment remains `not_assessed`;
- auditor change/opinion rows are visible and cited;
- no non-empty peer or KAM payload becomes a missing pack;
- five-year financial and peer rows reach the pack;
- unknown investor checks remain unknown;
- DCF readiness is blocked when required inputs are missing;
- unavailable enterprise value is not rendered as a valuation result.

- [ ] **Step 6: Confirm database immutability**

Record the live fixture's before/after SHA-256 values in the verification log.
Expected: exact digest equality. If it differs, stop verification and
investigate the write path before any completion claim. Never commit the
database, digest log, or a database copy.

- [ ] **Step 7: Run Ruff and diff checks**

Run:

```bash
uvx ruff check kreports tests
shared_base_sha="$(
  git merge-base HEAD codex/professional-mcp-hardening-plan
)"
git diff --check "$shared_base_sha"..HEAD
```

Expected: both commands exit 0.

- [ ] **Step 8: Run the full Python 3.12.7 suite**

Run:

```bash
uv run --python 3.12.7 pytest -q
```

Expected: exit 0. Report existing skips separately. Do not call this CI unless
the same commit’s remote required jobs are independently verified.

- [ ] **Step 9: Update user-facing documentation**

Document:

- canonical status versus domain verdict;
- standard-hours input tool and `not_assessed` boundary;
- chatbot/pack/resource display order;
- DCF candidate versus valuation readiness;
- question usability versus release readiness;
- cache absence versus filing absence.

Do not include live company fee values in committed documentation.

- [ ] **Step 10: Commit verification and documentation**

```bash
git add \
  tests/test_professional_mcp_contract.py \
  tests/test_professional_mcp_live.py \
  pyproject.toml \
  docs/deploy-http-mcp.md \
  README.md
git commit -m "test: verify professional MCP decision surfaces"
```

---

## Final Acceptance Matrix

| Surface | Required chatbot content | Required pack | Fail-closed condition |
|---|---|---|---|
| Audit effort | 3-year subject table, no standard-hour conclusion | subject inputs + peer table | missing field/source → limited |
| Acceptance | seven review areas + next owner/action | section matrix + supporting tables | no approval/rejection conclusion |
| Audit history | auditor, opinion, change, tenure | five-year history | uncited material row → limited |
| Opinion/matters | category-specific interpretation | excerpt + receipt | boilerplate-only signal rejected |
| KAM | topic/reason/procedure coverage | timeline + coverage table | unknown-only timeline → limited |
| Financial trend | five annual rows | full requested history | missing source disclosed |
| Peer | cohort reason and denominator | metric rows + cohort digest | source/denominator gap → limited |
| Investor checks | pass/fail/unknown | checks + risk/event tables | unknown cannot support positive verdict |
| QoE | cash conversion + matter basis | annual metrics + receipt groups | aggregate without receipts → limited |
| DCF candidates | candidate status + blocked inputs | actuals + candidates + blockers | missing model input blocks valuation |
| DCF model | calculated value or `산출 불가` | valuation tables only when calculated | EV `None` suppresses valuation output |
| Release context | separate deployment warning | bounded context | never overrides question status |

## Final Handoff Evidence

The implementation handoff must name:

- integration worktree and branch;
- final commit SHA;
- commit series by slice;
- focused and full test counts;
- Ruff and `git diff --check` results;
- live database SHA before and after;
- actual Samsung result matrix;
- remaining data/backfill gaps;
- Python 3.11 compatibility residual;
- whether any push or PR was performed.

No push or PR is authorized by this plan.

## GSTACK REVIEW REPORT

**Final verdict:** APPROVE

The independent engineering review ran three passes. The first pass found five
P1 plan defects, the second found one remaining P1 integration gap, and the
final pass approved the corrected plan with zero unresolved P0/P1 findings.

| Review surface | Finding closed by the plan |
|---|---|
| Status contract | Typed `SectionStatusV1` and deep-equality across raw, envelope, pack, and resource |
| Judgment safety | Per-tool verdict allowlists; injected approval, rejection, buy, sell, and audit-opinion strings are suppressed |
| Acceptance | Seven exact requirements, conservative KAM semantic flag, cited not-applicable basis |
| Parallel ownership | Domain lanes do not edit shared `peer_benchmarks.py`; semantic conflicts return to the owning lane |
| Public MCP quality | Mandatory priority packs, exact table IDs, row/source/fact rules, and chatbot headings |
| Verification boundary | Synthetic default suite plus explicit `KREPORTS_LIVE_DB` opt-in immutable live suite |
| Integration | Public acceptance handler calls the three-year audit-effort helper once and preserves all rows/provenance |

Planning-worktree baseline evidence:

```text
Python 3.12.7 focused baseline: 51 passed
Plan shape: 9 tasks, 9 file maps, 9 interface maps, 73 executable checkboxes
Document checks: balanced fences, no placeholders, git diff check clean
Independent final review: APPROVE
```

No production code, live database, remote branch, pull request, or deployment
is changed by this planning commit.
