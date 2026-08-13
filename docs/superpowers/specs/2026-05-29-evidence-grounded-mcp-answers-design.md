# Evidence-Grounded MCP Answers Design

## Verdict

Approved design direction: KReports MCP answers should combine DART filing links, verified facts from local datasets, and clearly separated analysis. The output must not feel like a raw JSON dump or a generic LLM web-search summary.

## Problem

Simple lists of disclosure text, section excerpts, or financial rows are not enough for a professional MCP product. Commercial LLMs can already search and summarize public filings. KReports must differentiate by showing:

- which filing and section supports each statement,
- which statements are verified facts from the dataset,
- which statements are analytical interpretation,
- what data is missing or only partially cached.

## Design Principle

Each MCP answer should read like a professional memo, not a database export.

Avoid visible numbered evidence labels in the user-facing prose. They are too rigid. Instead, write natural Korean paragraphs and attach source lines directly under the relevant claim or paragraph.

Preferred style:

```text
SK이터닉스는 2025년 사업보고서에서 태양광, 풍력, 연료전지 및 ESS를 주요 사업 포트폴리오로 설명하고 있습니다. 특히 풍력은 신안우이와 굴업도 등 대형 해상풍력 파이프라인을 보유한 것으로 기재되어 있습니다.

출처: SK이터닉스 사업보고서 (2025.12), II. 사업의 내용, 접수번호 20260316001520
공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260316001520
```

Avoid this style:

```text
1번 근거: 회사는 태양광, 풍력, 연료전지 및 ESS를 영위합니다.
2번 근거: 회사는 신안우이 프로젝트를 보유합니다.
```

Machine-readable `fact_id` values may still exist in structured fields for tests, UI rendering, and downstream agents, but they should not be rendered as primary user-facing labels.

## Answer Shape

Each upgraded MCP response should contain four conceptual layers.

### 1. Verdict And Short Answer

Start with a concise verdict and a direct answer to the user question.

Examples:

- `판정: usable`
- `판정: limited`
- `판정: cache_missing`

Then one or two paragraphs explaining the answer in plain Korean.

### 2. Confirmed From Filings

This section contains statements directly backed by local DART-derived data.

Rules:

- Do not include interpretation here.
- Every paragraph or bullet must have a source line.
- Prefer report name, year, section title, receipt number, and DART link.
- If the statement comes from structured tables, name the table-level source, such as `financial_facts_compact`, `audit_fees`, or `audit_procedure_items`.

User-facing heading:

```text
공시에서 확인되는 내용
```

Source line format:

```text
출처: {회사명} {보고서명}, {섹션명}, 접수번호 {rcept_no}
공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}
```

If the source is an attached audit report with synthetic rcept number, also show the parent business report receipt number when available.

### 3. Analysis

This section contains KReports' analytical reading of the confirmed facts.

Rules:

- Explicitly label it as analysis.
- Tie each analytical point back to one or more source-backed statements by natural source wording, not `Fact N`.
- Do not imply legal, audit opinion, investment recommendation, or valuation conclusion.
- Use domain-specific wording for auditor and investor perspectives.

User-facing headings:

```text
감사인 관점 해석
투자자 관점 해석
```

Examples:

```text
감사인 관점에서는 EPC와 장기 프로젝트 매출이 포함되어 있으므로 총공사수익, 총공사원가, 진행률, 미청구공사 회수가능성이 우선 검토 포인트입니다. 이 판단은 사업보고서의 신재생에너지/EPC 사업 설명과 수주현황 기재에 근거합니다.
```

### 4. Limits And Next Checks

This section states gaps honestly.

Rules:

- Distinguish `missing source`, `full_text fallback`, `derived_only placeholder`, `low peer coverage`, and `DART quota blocked`.
- Empty results must not be described as absence in the filing unless source coverage is sufficient.
- Recommend the next source check or tool call.

User-facing heading:

```text
확인 한계와 다음 확인
```

## Structured Contract

Public MCP tools should continue returning structured dictionaries for programmatic use. The answer renderer should use those fields to generate the professional narrative.

Recommended fields:

```python
{
    "answer": str,
    "confirmed_facts": [
        {
            "fact_id": "optional-machine-id",
            "statement": str,
            "source": {
                "corp_code": str,
                "corp_name": str,
                "report_nm": str,
                "bsns_year": int,
                "rcept_no": str,
                "parent_rcept_no": str | None,
                "section_key": str | None,
                "section_title": str | None,
                "dart_url": str,
                "source_table": str,
            },
            "excerpt": str | None,
        }
    ],
    "analysis": [
        {
            "perspective": "auditor" | "investor" | "both",
            "statement": str,
            "basis": list[str],
            "risk_level": "ok" | "watch" | "warn" | "bad" | None,
        }
    ],
    "data_quality": dict,
    "next_checks": list[str],
}
```

The renderer must not simply dump this structure. It should render natural Korean prose with source lines.

## Scope Order

Implementation should proceed in this order.

1. Build common citation/evidence helpers.
2. Upgrade business report and investor-facing tools first:
   - `get_business_overview`
   - `get_investor_signals`
   - `get_quality_of_earnings_pack`
   - `get_dcf_input_candidates`
   - `search_disclosure_events`
3. Upgrade auditor-facing tools:
   - `get_audit_report_sections`
   - `search_audit_report_matters`
   - `search_audit_procedures`
   - `get_kam_lifecycle`
   - `compare_peer_kam_topics`
   - `build_audit_acceptance_pack`
4. Add tests for narrative style, source linking, and gap wording.

## DART Link Policy

For ordinary filings, construct the DART URL as:

```text
https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}
```

For attached audit reports or derived document rows, use `parent_rcept_no` when available. If only a synthetic `rcept_no` exists, show both:

```text
공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo={parent_rcept_no}
첨부문서 식별자: {rcept_no}
```

## Error And Gap Semantics

Required statuses:

- `usable`: source-backed answer is suitable for professional screening.
- `limited`: partial answer exists, but a key source layer is missing or only full-text fallback is available.
- `cache_missing`: local DB has no usable body or derived section for the requested question.
- `quota_blocked`: collection or on-demand source fetch failed because of DART API limit.

The answer should explain the status in user-facing Korean.

## Non-Goals

- Do not call a commercial LLM inside the MCP server in this phase.
- Do not make investment recommendations.
- Do not make audit opinion conclusions.
- Do not hide source gaps behind polished prose.
- Do not expose raw JSON as the primary answer.

## Verification

Implementation should include:

- unit tests for DART URL construction,
- renderer tests proving numbered evidence labels are not shown,
- tests proving source lines are present for confirmed facts,
- tool smoke tests on SK이터닉스 2025 and 삼성전자 2025,
- regression tests for `limited` and `cache_missing` wording.
