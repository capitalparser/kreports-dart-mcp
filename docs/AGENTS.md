# Documentation, Demo, And Submission Artifact Rules

This file applies to work under `docs/`. The repository-root `AGENTS.md` remains
authoritative for architecture, Git workflow, validation, database safety, and
source traceability.

## Audience And Terminology

KReports documentation is reviewed by accounting professionals who may not be
software engineers.

- Preserve the correct professional or technical term rather than replacing it
  with an inaccurate easy word.
- On first use, add a plain-language Korean explanation in parentheses when the
  intended reader may not know the term.
- Add a short KReports example when a definition alone may remain abstract.
- After a term has been explained once in the same document, the shorter term may
  be used consistently.
- Distinguish internal implementation names from user-facing language. Tool
  names, field names, hashes, database table names, and internal statuses belong
  in technical sections, not in the default business-user narrative.
- Do not describe an AI-generated suggestion as a confirmed design or completed
  implementation until the repository and validation evidence support it.

Example:

```text
MCP(Model Context Protocol, 챗봇이 KReports의 검색·분석 기능을 정해진
형식으로 호출하도록 연결하는 규격)
```

## Project And Source Terminology

Use the repository's canonical domain definitions.

- `감사보고서` in KReports means the complete DART audit report submission
  package: the independent auditor's report, audited financial statements,
  complete financial-statement notes, accounting policies, significant
  estimates and judgments, KAM, audit procedures, and related schedules.
- `사업보고서` is the canonical source for business overview, products and
  services, sales and purchase structure, R&D, risks, major contracts,
  governance, and other statutory business-report disclosures.
- Do not describe financial-statement notes as business-report evidence when the
  audit report package is the actual or required source.
- A business-report-derived note is a fallback only when the audit report
  package source is unavailable, and the limitation must be visible.

## Release Identity And Reproducibility

Every report, screenshot, demo, and video must identify or be traceable to the
actual release candidate used.

Record, where applicable:

- Git commit SHA;
- runtime database or dataset identity;
- release manifest or artifact version;
- company, business year, and CFS/OFS basis;
- DART receipt number and source type;
- MCP runtime or endpoint used;
- capture or measurement date.

Do not:

- combine screenshots or claims produced from different release candidates
  without explicitly labelling the difference;
- show one company or year in the screen while describing another in the text;
- use a mocked or manually edited result as evidence of a working E2E feature;
- describe planned, partially implemented, or fixture-only behavior as a live
  completed capability.

## Evidence And Claims

- Separate `확인된 사실`, `분석`, `예상 효과`, and `향후 계획`.
- Every factual product claim must match the current verified implementation and
  data coverage.
- Distinguish measured results from expected benefits.
- When reporting an improvement, state the sample, companies, questions,
  conditions, baseline process, and measurement method.
- Do not generalize a small sample into a product-wide percentage without
  evidence.
- Do not treat code-test success as proof of live-data completeness or release
  readiness.
- Preserve data limitations and source-access limitations in the report even
  when they make the result less visually attractive.

Example:

```text
측정 결과: 표본 3개 회사의 지정 질문에서 평균 탐색시간이 감소함.
예상 효과: 적용 회사와 질문 유형을 확대하면 반복 DART 탐색시간이 추가로
줄어들 가능성이 있음.
```

## Screenshots, Diagrams, And Demo Videos

- Screenshots must show only information that is safe to circulate.
- Remove API keys, bearer tokens, local file paths, user identifiers, personal
  data, customer information, and audit-confidential information.
- A process diagram must distinguish implemented paths from proposed paths.
- Use a visibly different label such as `현재 구현`, `조건부`, or `향후 계획`;
  do not rely on unstated visual implication.
- Demo questions, expected answers, actual outputs, and DART links must be
  checked against the same release candidate before recording.
- Include at least one truthful limitation or unavailable-data behavior when it
  is material to the scenario.
- Do not edit a screenshot or video in a way that changes the substantive result
  while presenting it as a live output.

## Submission Material Consistency

The idea video, Word explanation, screenshots, diagrams, and repository status
must tell the same product story.

Before finalization, verify that:

- the problem statement matches the implemented workflow;
- the feature list matches the actual MCP and data surfaces;
- the audit report package and business report are not confused;
- expected benefits are not presented as measured facts;
- the same representative scenario, company, year, and source are used where the
  materials claim they are the same;
- known limitations and conditional functions are not omitted;
- the final files can be traced to an approved release candidate.

## Security And File Handling

- Never include DART API keys, MCP tokens, signing keys, passwords, `.env`
  contents, private local paths, personal data, customer information, or
  audit-confidential information in documentation or media.
- Do not commit large video files, runtime databases, raw filings, generated
  caches, or private source documents to Git.
- Store text artifacts such as scripts, storyboards, outlines, screen lists,
  measurement tables, and QA checklists in Git.
- Store approved large binaries in the designated external file repository and
  record only a safe reference or manifest in Git.
- Use synthetic or legally safe fixtures in documentation examples unless the
  source is public DART material and the excerpt is necessary and appropriately
  bounded.

## Review And Handoff

A documentation PR is not complete merely because the prose reads well. The PR
must state:

- the intended audience;
- the code/data release it describes;
- which claims were verified and how;
- which sections describe future work;
- whether screenshots or metrics were refreshed;
- remaining inconsistencies or unresolved decisions.

When a document changes a domain definition, public behavior, source
precedence, or release claim, update the corresponding code contract, tests,
`CONTEXT.md`, and other affected documentation in the same workstream or record
an explicit blocking follow-up Issue.