# KReports Domain and MCP Implementation Rules

This file supplements the repository-root `AGENTS.md` for work under
`kreports/`. The root anti-patchwork, source-traceability, readonly, chatbot,
context-window, and Codex-validation requirements remain authoritative.

## Audit Report Package And Financial-Statement Source Contract

KReports uses `audit report` in the broad professional sense of the DART audit
report submission package, not only the independent auditor's opinion pages.
The package includes:

- the independent auditor's report and opinion;
- the audited statement of financial position, income or comprehensive-income
  statement, cash-flow statement, and statement of changes in equity;
- the complete attached financial-statement notes;
- significant accounting policies;
- significant estimates and judgments; and
- related schedules included in the same audit report submission.

### Canonical source ownership

- The audit report package is the canonical source for audited financial
  statements, financial-statement notes, accounting policies, significant
  estimates and judgments, audit opinion, KAM, audit procedures, going-concern
  paragraphs, emphasis paragraphs, and other audit-report matters.
- The business report is the canonical source for business overview, products
  and services, sales and purchase structure, R&D, risks, major contracts,
  governance, and other statutory business-report disclosures.
- Financial-statement note functionality must not use the business report as its
  default source merely because a duplicate or similar note appears there.
- Existing business-report-derived note rows are legacy fallback evidence. They
  may be returned only when the audit report package source is unavailable and
  the response preserves the true source type, receipt number, CFS/OFS basis,
  availability, and limitation.
- A fallback must never be relabelled as audit-report evidence or presented as
  equivalent to a verified audit report package source.

### Implementation rules

Before changing note, policy, financial-statement, KAM, or audit-evidence code,
record the complete source path:

```text
DART audit report submission and attachments
→ raw/source document
→ package and attachment selection
→ parser/extractor
→ structured or evidence row
→ analysis service
→ MCP/API result
→ user-visible source link and limitation
```

- One canonical service must own report-package selection and source precedence.
  Handlers, renderers, dashboards, and exports must not independently decide
  whether a business-report copy can substitute for an audit-report package.
- Source selection must be deterministic and testable by receipt number, report
  type, business year, and CFS/OFS basis.
- Do not silently merge text from the business report and audit report package
  into one apparent source excerpt.
- A migration from legacy business-report note data must inventory affected
  rows, preserve old provenance, rebuild from audit-report packages, and report
  unresolved coverage gaps rather than overwriting history without evidence.
- A new feature is incomplete if its answer shows note text without the actual
  source package, receipt number or DART link when available, and source status.

### Required regressions

Tests for financial-statement notes and accounting policies must prove that:

- an available audit report package is selected ahead of a business-report copy;
- the attached financial statements and notes are treated as part of the audit
  report package;
- a business-report fallback is explicit and never labelled as the canonical
  audit source;
- different CFS/OFS bases are not silently combined;
- receipt numbers and source links belong to the same evidence used in the
  answer;
- missing local audit-package evidence is not interpreted as absence from the
  company's filing; and
- MCP, API, dashboard, export, and resource paths consume the same source
  decision.

### Real-filing verification

For changes to report-package selection, parsing, audited financial statements,
financial-statement notes, accounting policies, KAM, audit procedures, or DART
links:

- Add deterministic fixture-based regression coverage.
- Also compare the result with representative real DART filing evidence when
  the required public filing is locally or lawfully available.
- Record the company, business year, receipt number, CFS/OFS basis, item checked,
  expected source, and comparison result in the Pull Request.
- Confirm that the receipt number and DART link identify the same source package
  actually used by the answer.
- If real-filing verification cannot be performed, state the missing prerequisite
  explicitly and keep the limitation visible.
- Do not imply that fixture-only validation proves live-data coverage,
  extraction completeness, or release readiness.
- Do not place confidential client or audit data into fixtures, PRs, comments, or
  screenshots; use public DART evidence or synthetic legally safe samples.

## Customized Peer-Selection Transparency Contract

Peer selection is a user-controlled analytical decision. Any feature that uses a
peer group must preserve and expose the exact relationship between the user's
request, the criteria actually applied, the resulting population, and the order
shown to the user.

### Canonical ownership

- `kreports.analysis.peer_criteria.PeerCriteriaProfile` owns valid user criteria.
- The existing canonical peer selector owns company membership and ordering.
- `kreports.analysis.peer_quality` owns full statistical-population separation
  from the bounded display page.
- A presentation/explanation projection may translate those facts into business
  language, but it must not query a second population, resort companies, or
  recalculate a score.
- MCP 1.x, MCP 2.x, API, dashboard, export, and demo surfaces must consume the
  same selection result and explanation contract.

### Required lifecycle

Every peer result must keep these stages distinguishable:

```text
user-requested criteria
→ validated criteria
→ criteria actually applied
→ resolved year / FS basis / industry fallback
→ full eligible population
→ display ordering rule
→ first five-company page
→ downstream analysis using the same population
```

A requested condition that was not applied, was used only as information, or is
unsupported must never be silently presented as applied.

### User-visible requirements

The default answer must state:

1. whether the criteria came from the user or a disclosed default;
2. the exact applied year and 연결/별도 basis;
3. the actual industry scope, including any fallback from a narrower to a wider
   scope;
4. the size metric and explicit allowed range, or that no size filter was used;
5. required data availability, direct inclusions, direct exclusions, and
   weighting choices when present;
6. the number of companies in the full eligible population;
7. the exact ordering used for the displayed companies; and
8. the criteria evidence for each company shown.

The applied-criteria table and the first five selected companies must be visible
together. An auxiliary criteria table must not be counted as a company page.

### Filtering and ordering are different

- A filter decides whether a company belongs to the eligible population.
- An ordering rule decides which eligible company is shown first.
- Do not call a list `관련성 높은 순`, `가장 유사한 순`, or equivalent unless
  the canonical selector actually ranks by the declared, evidence-backed
  dimensions.
- If the selector merely orders eligible companies by total assets, say exactly
  `조건을 충족한 회사 중 총자산이 큰 순`.
- A size tolerance is a range gate unless the canonical selector separately
  emits a continuous size-distance component. Do not imply that companies
  inside the range are ordered by closeness.
- Industry-prefix membership is not evidence of complete business-model,
  product, customer, geographic, or supply-chain similarity.

### Company-level explanation

- Each displayed company must reference the same canonical selection row and may
  state only criteria supported by that row.
- Direct user inclusion must be labeled explicitly and must not be described as
  economic similarity.
- If a company is included as an exception to the industry rule, show that
  exception.
- Do not infer an unavailable criterion from another metric. For example, do not
  substitute assets for employees or title keywords for business similarity.
- Internal scores and company codes may remain in structured metadata for
  reproducibility, but user-visible text must use company names and plain
  reasons.

### Criteria application statuses

Use distinct structured statuses and user-facing labels:

- `applied`: affected membership or ordering as stated;
- `informational`: measured or displayed but did not affect membership/order;
- `not_applied`: requested but missing a required parameter, such as a size
  metric without an allowed range; and
- `unsupported`: the required evidence/index does not currently exist.

Unsupported or non-applied criteria must be shown before the result is treated
as complete. They must not be hidden in developer-only logs.

### Population and pagination

- Statistics use the complete eligible population, not the five displayed rows.
- The first page shows five companies together with the applied criteria.
- `다음 5개` must reuse the stored population and ordering; it must not rerun
  selection or ask the model to reconstruct prior results.
- A criteria change invalidates the population, page tokens, and every dependent
  benchmark or note comparison.

### Performance and context

- Criteria explanation must be a pure projection over the canonical result and
  must not add database queries, external reads, or model calls.
- Large inclusion/exclusion details and all company rows stay outside routine
  model context; use result references and page tokens.
- User-visible and structured criteria explanations must derive from the same
  object so they cannot disagree.

### Regression requirements

Tests must prove that:

- custom criteria are shown exactly and alongside the selected first five;
- requested and applied criteria are distinguishable;
- fallback year, FS basis, and industry scope are visible;
- unsupported criteria are explicit;
- each company reason matches the canonical selection evidence;
- non-ranked output is not mislabeled as relevance ranking;
- auxiliary criteria tables do not alter company page counts;
- the display page does not change the statistical population; and
- downstream peer analyses reuse the same criteria explanation and population.
