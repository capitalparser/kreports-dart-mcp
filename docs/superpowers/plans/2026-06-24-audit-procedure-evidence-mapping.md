# Audit Procedure Evidence Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen KAM audit-procedure extraction so KReports can answer which audit procedures were performed, why they were tied to a KAM, and which DART disclosure materials support the answer.

**Architecture:** Add a read-only evidence-map diagnostic layer before changing extraction behavior, then improve the parser and reindex from existing cached `report_sections` / `evidence_documents` only. Runtime MCP/API responses compute linkage summaries from structured rows and normalized evidence; no DART raw-body backfill is part of this feature.

**Tech Stack:** Python 3.12, Typer CLI, SQLAlchemy, SQLite, pytest, existing KReports parser/API modules.

## Global Constraints

- Do not collect or persist new full raw DART source documents for this task.
- Do not clear, rewrite, or compact the current database as part of parser work.
- Preserve current MCP/API return shapes; add fields under `data_quality`, `linkages`, or `evidence_map` only.
- Audit-report KAM is the primary source for procedure detail; business-report KAM is summary-only supporting context.
- Each implementation slice must end with a targeted pytest command and a git commit containing only files touched by that slice.
- Existing dirty files outside this feature remain untouched: `scripts/dart_limit_aware_backfill.sh`, `scripts/run_complete_dataset_backfill.sh`, `tests/test_auditor_readiness.py`, `scripts/backfill_preflight.sh`, and `docs/superpowers/plans/2026-06-18-dcf-valuation-workbook-pack.md`.

---

## Current Baseline

Commands run on 2026-06-24:

```bash
.venv/bin/kreports auditor-feature-readiness --year 2025 --json
.venv/bin/kreports audit-kam-quality --year 2025 --json
```

Observed baseline:

- `kam_sections`: 5,044 in auditor readiness; KAM coverage is usable.
- `audit_procedure_items`: 54 rows across 19 companies; degraded.
- `audit_procedure_company_coverage`: 0.7%.
- `audit-kam-quality` finds 4,256 KAM sections in its quality scope, but 4,241 are shorter than 300 chars.
- Many repair candidates have `body_length` 26-33 and body text like `핵심감사사항은 우리의 전문가적 판단에 따라 당기`, which means the current parser/index can see a KAM heading but often loses the detailed KAM body.

Root-cause hypothesis to test first:

1. Some KAM sections are complete but `extract_audit_procedure_items()` misses procedure markers and bullet styles.
2. Many KAM sections are not complete: the section extractor captures only the generic KAM intro sentence and not the detailed KAM topic blocks.
3. Current API cannot explain procedure-to-disclosure linkage because it returns procedure rows without a semantic evidence map.

## File Structure

- Create `kreports/analysis/audit_procedure_evidence.py`
  - Read-only diagnostics and linkage classification.
  - No DB writes.
  - Exposes `classify_audit_procedure_linkages(...)` and `build_audit_procedure_evidence_map(...)`.
- Modify `kreports/cli/main.py`
  - Add `audit-procedure-evidence-map` command.
- Modify `kreports/processor/audit_report_parser.py`
  - Expand procedure zone markers.
  - Add false-positive guard for generic auditor-responsibility procedure wording.
  - Add KAM detail recovery helper for paragraph-heading/table-ish KAM bodies.
- Modify `kreports/collector/report_document_collector.py`
  - Use recovered KAM detail text when indexing audit procedures from existing sections.
- Modify `kreports/analysis/api.py`
  - Attach linkage fields and clearer source-basis notes to `search_audit_procedures()` and `compare_peer_audit_procedures()`.
- Create `tests/test_audit_procedure_evidence.py`
  - Unit tests for linkage classification and diagnostic summary shape.
- Modify `tests/test_audit_report_sections.py`
  - Parser regression tests for Korean KAM response patterns and false positives.
- Modify or add focused API tests if existing peer/tool tests cover these functions.

---

### Task 1: Read-Only Evidence Map Diagnostic

**Files:**
- Create: `kreports/analysis/audit_procedure_evidence.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_audit_procedure_evidence.py`

**Interfaces:**
- Consumes: existing `kreports.db.engine._engine`, `companies`, `report_sections`, `audit_procedure_items`, `accounting_note_chapters`, `accounting_policy_items`, `financial_facts_compact`, `disclosure_events`.
- Produces:
  - `classify_audit_procedure_linkages(text: str, kam_topic: str | None = None) -> list[dict[str, str]]`
  - `build_audit_procedure_evidence_map(year: int, company: str | None = None, market: str | None = None, limit: int = 100) -> dict`

- [ ] **Step 1: Write failing linkage tests**

Add to `tests/test_audit_procedure_evidence.py`:

```python
from kreports.analysis.audit_procedure_evidence import classify_audit_procedure_linkages


def test_classify_audit_procedure_linkages_maps_procedure_to_accounts_and_notes():
    rows = classify_audit_procedure_linkages(
        "매출 관련 내부통제 이해 및 평가, 계약서 문서검사, 기간귀속 테스트를 수행하였습니다.",
        kam_topic="revenue",
    )

    keys = {(row["category"], row["key"]) for row in rows}
    assert ("audit_report_kam", "revenue") in keys
    assert ("financial_statement_account", "revenue") in keys
    assert ("accounting_note", "revenue_policy") in keys


def test_classify_audit_procedure_linkages_maps_impairment_to_valuation_evidence():
    rows = classify_audit_procedure_linkages(
        "손상검사에 사용된 미래현금흐름과 할인율의 합리성을 평가하고 민감도 분석을 수행하였습니다.",
        kam_topic="impairment",
    )

    keys = {(row["category"], row["key"]) for row in rows}
    assert ("audit_report_kam", "impairment") in keys
    assert ("financial_statement_account", "impairment") in keys
    assert ("accounting_note", "impairment_assumption") in keys
```

- [ ] **Step 2: Run failing tests**

Run:

```bash
uv run pytest tests/test_audit_procedure_evidence.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'kreports.analysis.audit_procedure_evidence'`.

- [ ] **Step 3: Implement minimal linkage classifier**

Create `kreports/analysis/audit_procedure_evidence.py`:

```python
from __future__ import annotations

from typing import Any

from sqlalchemy import bindparam, text

from kreports.db.engine import engine as _engine


_TOPIC_TO_LINKS: dict[str, list[dict[str, str]]] = {
    "revenue": [
        {"category": "audit_report_kam", "key": "revenue", "label": "KAM: 수익인식"},
        {"category": "financial_statement_account", "key": "revenue", "label": "재무제표: 매출액"},
        {"category": "accounting_note", "key": "revenue_policy", "label": "주석: 수익인식 회계정책"},
    ],
    "inventory": [
        {"category": "audit_report_kam", "key": "inventory", "label": "KAM: 재고자산"},
        {"category": "financial_statement_account", "key": "inventory", "label": "재무제표: 재고자산"},
        {"category": "accounting_note", "key": "inventory_policy", "label": "주석: 재고자산 평가정책"},
    ],
    "impairment": [
        {"category": "audit_report_kam", "key": "impairment", "label": "KAM: 손상검사"},
        {"category": "financial_statement_account", "key": "impairment", "label": "재무제표: 손상 관련 자산"},
        {"category": "accounting_note", "key": "impairment_assumption", "label": "주석: 회수가능액 및 주요 가정"},
    ],
    "fair_value": [
        {"category": "audit_report_kam", "key": "fair_value", "label": "KAM: 공정가치"},
        {"category": "financial_statement_account", "key": "fair_value", "label": "재무제표: 공정가치 측정 항목"},
        {"category": "accounting_note", "key": "fair_value_hierarchy", "label": "주석: 공정가치 서열체계"},
    ],
    "provision": [
        {"category": "audit_report_kam", "key": "provision", "label": "KAM: 충당부채/우발부채"},
        {"category": "financial_statement_account", "key": "provision", "label": "재무제표: 충당부채"},
        {"category": "accounting_note", "key": "contingency", "label": "주석: 우발부채 및 약정사항"},
    ],
    "consolidation": [
        {"category": "audit_report_kam", "key": "consolidation", "label": "KAM: 연결/종속기업"},
        {"category": "financial_statement_account", "key": "subsidiary_investment", "label": "재무제표: 종속기업 투자"},
        {"category": "accounting_note", "key": "consolidation_scope", "label": "주석: 연결범위"},
    ],
    "tax": [
        {"category": "audit_report_kam", "key": "tax", "label": "KAM: 법인세"},
        {"category": "financial_statement_account", "key": "deferred_tax", "label": "재무제표: 이연법인세"},
        {"category": "accounting_note", "key": "income_tax", "label": "주석: 법인세"},
    ],
}

_TEXT_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "revenue": ("매출", "수익", "기간귀속", "계약서", "수행의무"),
    "inventory": ("재고", "순실현가능가치", "평가충당"),
    "impairment": ("손상", "회수가능", "현금창출단위", "할인율", "미래현금흐름"),
    "fair_value": ("공정가치", "가치평가", "평가기법", "외부평가기관"),
    "provision": ("충당부채", "우발", "소송", "복구충당"),
    "consolidation": ("연결", "종속기업", "사업결합", "지배력"),
    "tax": ("법인세", "이연법인세", "세무조사"),
}

_DISCLOSURE_EVENT_HINTS: dict[str, tuple[str, ...]] = {
    "auditor_change": ("감사인", "교체", "지정감사"),
    "capital_market_event": ("유상증자", "전환사채", "신주인수권", "사채"),
    "business_combination": ("합병", "분할", "양수", "양도", "사업결합"),
    "litigation": ("소송", "중재", "분쟁"),
}


def _dedupe_links(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (row["category"], row["key"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def classify_audit_procedure_linkages(text: str, kam_topic: str | None = None) -> list[dict[str, str]]:
    body = text or ""
    topics: list[str] = []
    if kam_topic:
        topics.append(kam_topic)
    for topic, keywords in _TEXT_TOPIC_KEYWORDS.items():
        if any(keyword in body for keyword in keywords):
            topics.append(topic)
    links: list[dict[str, str]] = []
    for topic in topics:
        links.extend(_TOPIC_TO_LINKS.get(topic, []))
    for event_key, keywords in _DISCLOSURE_EVENT_HINTS.items():
        if any(keyword in body for keyword in keywords):
            links.append({
                "category": "disclosure_event",
                "key": event_key,
                "label": f"수시공시 이벤트: {event_key}",
            })
    return _dedupe_links(links)
```

- [ ] **Step 4: Run linkage tests**

Run:

```bash
uv run pytest tests/test_audit_procedure_evidence.py -q
```

Expected: PASS for the two classifier tests.

- [ ] **Step 5: Write failing diagnostic shape test**

Append to `tests/test_audit_procedure_evidence.py`:

```python
from datetime import date

from kreports.analysis.audit_procedure_evidence import build_audit_procedure_evidence_map
from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, Company, ReportSection


def test_build_audit_procedure_evidence_map_reports_short_kam_and_linkages(temp_engine):
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(ReportSection(
            corp_code="00126380",
            bsns_year=2025,
            rcept_no="20260301000001_100",
            dcm_no="100",
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="핵심감사사항은 우리의 전문가적 판단에 따라 당기",
            body_length=26,
            ordinal=1,
            fetched_at=date(2026, 3, 1),
        ))
        session.add(AuditProcedureItem(
            corp_code="00126380",
            bsns_year=2025,
            rcept_no="20260301000001_100",
            dcm_no="100",
            source_type="audit_report",
            section_ordinal=1,
            kam_topic="revenue",
            procedure_type="substantive_test",
            procedure_text="매출 계약서 문서검사와 기간귀속 테스트를 수행하였습니다.",
            procedure_hash="p1",
            procedure_length=32,
            procedure_ordinal=1,
        ))

    result = build_audit_procedure_evidence_map(year=2025, company="005930", limit=10)

    assert result["verdict"] == "fail"
    assert result["counts"]["kam_sections"] == 1
    assert result["counts"]["short_kam_sections"] == 1
    assert result["counts"]["procedure_items"] == 1
    assert result["samples"][0]["linkages"][0]["category"] == "audit_report_kam"
    assert "short_kam_body" in result["required_gaps"]
```

- [ ] **Step 6: Implement diagnostic function**

Add to `kreports/analysis/audit_procedure_evidence.py` below the classifier:

```python
def _resolve_company_filter(company: str | None) -> tuple[str, dict[str, Any]]:
    if not company:
        return "", {}
    return (
        "AND (c.corp_code=:company OR c.stock_code=:company OR c.corp_name LIKE :company_like)",
        {"company": company, "company_like": f"%{company}%"},
    )


def build_audit_procedure_evidence_map(
    *,
    year: int,
    company: str | None = None,
    market: str | None = None,
    limit: int = 100,
) -> dict:
    company_filter, params = _resolve_company_filter(company)
    market_filter = "AND c.market=:market" if market else ""
    if market:
        params["market"] = market
    params["year"] = int(year)
    params["limit"] = max(1, min(int(limit), 500))

    with _engine.connect() as conn:
        kam_rows = [dict(r) for r in conn.execute(
            text(f"""
                SELECT rs.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                       rs.bsns_year, rs.rcept_no, rs.dcm_no, rs.section_title,
                       rs.body_text, rs.body_length, rs.ordinal
                FROM report_sections rs
                JOIN companies c ON c.corp_code=rs.corp_code
                WHERE rs.bsns_year=:year
                  AND rs.source_type='audit_report'
                  AND rs.section_key='kam'
                  {company_filter}
                  {market_filter}
                ORDER BY rs.body_length ASC, c.market, c.corp_name
                LIMIT :limit
            """),
            params,
        ).mappings().all()]

        procedure_rows = [dict(r) for r in conn.execute(
            text(f"""
                SELECT api.corp_code, api.rcept_no, api.dcm_no, api.kam_topic,
                       api.procedure_type, api.procedure_text, api.procedure_length
                FROM audit_procedure_items api
                JOIN companies c ON c.corp_code=api.corp_code
                WHERE api.bsns_year=:year
                  {company_filter}
                  {market_filter}
                ORDER BY api.corp_code, api.rcept_no, api.procedure_ordinal
                LIMIT :limit
            """),
            params,
        ).mappings().all()]

    procedures_by_receipt: dict[str, list[dict[str, Any]]] = {}
    for row in procedure_rows:
        procedures_by_receipt.setdefault(str(row.get("rcept_no")), []).append(row)

    short_count = sum(1 for row in kam_rows if int(row.get("body_length") or 0) < 300)
    samples: list[dict[str, Any]] = []
    for row in kam_rows[: params["limit"]]:
        procedures = procedures_by_receipt.get(str(row.get("rcept_no")), [])
        text_basis = " ".join(str(p.get("procedure_text") or "") for p in procedures) or str(row.get("body_text") or "")
        kam_topic = procedures[0].get("kam_topic") if procedures else None
        samples.append({
            "corp_code": row.get("corp_code"),
            "stock_code": row.get("stock_code"),
            "corp_name": row.get("corp_name"),
            "market": row.get("market"),
            "year": row.get("bsns_year"),
            "rcept_no": row.get("rcept_no"),
            "dcm_no": row.get("dcm_no"),
            "section_title": row.get("section_title"),
            "body_length": row.get("body_length"),
            "procedure_count": len(procedures),
            "body_head": str(row.get("body_text") or "")[:180],
            "linkages": classify_audit_procedure_linkages(text_basis, kam_topic=kam_topic),
        })

    counts = {
        "kam_sections": len(kam_rows),
        "short_kam_sections": short_count,
        "procedure_items": len(procedure_rows),
        "procedure_receipts": len({row.get("rcept_no") for row in procedure_rows}),
    }
    required_gaps: list[str] = []
    if short_count:
        required_gaps.append("short_kam_body")
    if len(procedure_rows) == 0:
        required_gaps.append("audit_procedure_items")
    if not any(sample["linkages"] for sample in samples):
        required_gaps.append("procedure_evidence_linkages")
    verdict = "pass" if not required_gaps else ("conditional" if len(procedure_rows) else "fail")
    return {
        "verdict": verdict,
        "year": int(year),
        "company": company,
        "market": market,
        "counts": counts,
        "rates": {
            "short_kam_rate": round(short_count * 100.0 / len(kam_rows), 1) if kam_rows else 0.0,
            "procedure_to_kam_rate": round(len(procedure_rows) * 100.0 / len(kam_rows), 1) if kam_rows else 0.0,
        },
        "required_gaps": required_gaps,
        "samples": samples,
        "data_quality": {
            "source": "report_sections.audit_report_kam + audit_procedure_items",
            "note": "This diagnostic does not fetch new raw DART documents; it tests whether cached audit-report KAM bodies can support procedure-level answers.",
        },
    }
```

- [ ] **Step 7: Add CLI command**

Add to `kreports/cli/main.py` near `audit-kam-quality`:

```python
@app.command("audit-procedure-evidence-map")
def audit_procedure_evidence_map_cmd(
    year: int = typer.Option(2025, "--year", help="기준 사업연도"),
    company: Optional[str] = typer.Option(None, "--company", help="corp_code, stock_code, or 회사명"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ"),
    limit: int = typer.Option(100, "--limit", help="샘플 최대 행 수"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """감사절차가 어떤 공시/주석/계정 근거와 연결되는지 진단한다."""
    from kreports.analysis.audit_procedure_evidence import build_audit_procedure_evidence_map

    snapshot = build_audit_procedure_evidence_map(
        year=year,
        company=company,
        market=market,
        limit=limit,
    )
    if json_output:
        _json_print(snapshot)
        return
    typer.echo(f"Audit procedure evidence map: {snapshot['verdict']}")
    typer.echo(f"year: {snapshot['year']} | company: {snapshot['company'] or '-'} | market: {snapshot['market'] or '-'}")
    typer.echo("counts:")
    for key, value in snapshot["counts"].items():
        typer.echo(f"- {key}: {value}")
    typer.echo("rates:")
    for key, value in snapshot["rates"].items():
        typer.echo(f"- {key}: {value}%")
    typer.echo(f"required_gaps: {', '.join(snapshot['required_gaps']) or '-'}")
    for row in snapshot["samples"][:10]:
        linkage_labels = ", ".join(link["label"] for link in row["linkages"][:4]) or "-"
        typer.echo(f"  {row['stock_code'] or row['corp_code']} {row['corp_name']} len={row['body_length']} procedures={row['procedure_count']} links={linkage_labels}")
```

- [ ] **Step 8: Run diagnostic tests and CLI smoke**

Run:

```bash
uv run pytest tests/test_audit_procedure_evidence.py -q
.venv/bin/kreports audit-procedure-evidence-map --year 2025 --limit 20 --json
```

Expected:

- pytest passes.
- CLI returns JSON with `counts`, `rates`, `required_gaps`, and `samples`.

- [ ] **Step 9: Commit Task 1**

```bash
git add kreports/analysis/audit_procedure_evidence.py kreports/cli/main.py tests/test_audit_procedure_evidence.py
git commit -m "feat: add audit procedure evidence diagnostics"
```

---

### Task 2: Parser Markers, False Positives, and KAM Detail Recovery

**Files:**
- Modify: `kreports/processor/audit_report_parser.py`
- Modify: `tests/test_audit_report_sections.py`

**Interfaces:**
- Consumes: `extract_audit_report_sections(xml_content: str) -> dict`, `extract_audit_procedure_items(kam_body: str) -> list[dict]`.
- Produces:
  - `recover_kam_detail_body(full_text: str, extracted_body: str) -> str`
  - improved `extract_audit_procedure_items(...)` behavior.

- [ ] **Step 1: Write failing tests for additional procedure markers**

Append to `tests/test_audit_report_sections.py`:

```python
def test_extract_audit_procedure_items_handles_auditor_response_heading():
    body = """
    수익인식
    핵심감사사항으로 선정한 이유
    계약 조건과 기간귀속 판단이 중요합니다.
    감사인의 대응
    우리는 다음의 감사절차를 수행하였습니다.
    가. 계약서 원본과 세금계산서 대사
    나. 보고기간 전후 매출의 기간귀속 테스트
    다. 매출채권 회수 여부 확인
    """

    items = extract_audit_procedure_items(body)

    assert len(items) == 3
    assert items[0]["procedure_type"] == "substantive_test"
    assert "기간귀속 테스트" in items[1]["procedure_text"]
    assert items[2]["procedure_type"] == "external_confirmation"


def test_extract_audit_procedure_items_excludes_generic_auditor_responsibility():
    body = """
    재무제표감사에 대한 감사인의 책임
    우리는 중요왜곡표시위험에 대응하는 감사절차를 설계하고 수행합니다.
    우리는 지배기구와 커뮤니케이션한 사항 중 핵심감사사항을 결정합니다.
    """

    assert extract_audit_procedure_items(body) == []
```

- [ ] **Step 2: Write failing KAM detail recovery test**

Append to `tests/test_audit_report_sections.py`:

```python
def test_extract_audit_report_sections_recovers_detail_after_short_kam_intro():
    xml = """
    <DOCUMENT>
      <P>감사의견</P>
      <P>우리는 재무제표가 적정하게 표시되어 있다고 판단합니다.</P>
      <P>핵심감사사항</P>
      <P>핵심감사사항은 우리의 전문가적 판단에 따라 당기 감사에서 가장 유의적인 사항입니다.</P>
      <P>수익인식</P>
      <P>핵심감사사항으로 선정한 이유: 계약 조건 판단과 기간귀속에 중요한 왜곡표시위험이 존재합니다.</P>
      <P>핵심감사사항이 감사에서 다루어진 방법</P>
      <P>ㆍ계약서와 세금계산서 대사를 수행하였습니다.</P>
      <P>ㆍ보고기간 전후 매출의 기간귀속 테스트를 수행하였습니다.</P>
      <P>재무제표에 대한 경영진의 책임</P>
      <P>경영진은 재무제표 작성 책임이 있습니다.</P>
    </DOCUMENT>
    """

    sections = extract_audit_report_sections(xml)

    assert "kam" in sections
    assert "계약 조건 판단" in sections["kam"]["body_text"]
    assert "기간귀속 테스트" in sections["kam"]["body_text"]
    assert sections["kam"]["length"] > 120
```

- [ ] **Step 3: Run failing parser tests**

Run:

```bash
uv run pytest tests/test_audit_report_sections.py::test_extract_audit_procedure_items_handles_auditor_response_heading tests/test_audit_report_sections.py::test_extract_audit_procedure_items_excludes_generic_auditor_responsibility tests/test_audit_report_sections.py::test_extract_audit_report_sections_recovers_detail_after_short_kam_intro -q
```

Expected: at least the new marker/recovery tests fail.

- [ ] **Step 4: Add marker sets and false-positive guard**

Modify `kreports/processor/audit_report_parser.py`:

```python
_PROCEDURE_ZONE_MARKERS = (
    "핵심감사사항이 감사에서 다루어진 방법",
    "핵심 감사사항이 감사에서 다루어진 방법",
    "감사에서 다루어진 방법",
    "감사인의 대응",
    "감사인의 감사절차",
    "우리가 수행한 주요 감사절차",
    "주요 감사절차",
    "다음의 감사절차",
    "다음을 포함한 감사절차",
    "감사절차",
)

_GENERIC_AUDITOR_RESPONSIBILITY_PHRASES = (
    "재무제표감사에 대한 감사인의 책임",
    "중요왜곡표시위험에 대응하는 감사절차를 설계하고 수행",
    "핵심감사사항을 결정합니다",
    "감사보고서에 이러한 사항들을 기술합니다",
)

_PROCEDURE_BULLET_RE = re.compile(
    r"\n+|(?:^|\s)[·•ㆍ-]\s*|(?:^|\s)[가-하]\.\s*|(?:^|\s)\(\d+\)\s*|(?:^|\s)\d+\.\s*"
)
```

Replace `_procedure_zone(...)`:

```python
def _looks_like_generic_auditor_responsibility(text: str) -> bool:
    body = xml_to_text(text)
    return any(phrase in body for phrase in _GENERIC_AUDITOR_RESPONSIBILITY_PHRASES)


def _procedure_zone(text: str) -> str:
    body = xml_to_text(text)
    if _looks_like_generic_auditor_responsibility(body):
        return ""
    positions = [body.find(marker) for marker in _PROCEDURE_ZONE_MARKERS if body.find(marker) >= 0]
    if not positions:
        return body
    return body[min(positions):]
```

Update `extract_audit_procedure_items(...)` to split with `_PROCEDURE_BULLET_RE` and skip marker-only sentences:

```python
pieces = _PROCEDURE_BULLET_RE.split(zone)
...
if text in set(_PROCEDURE_ZONE_MARKERS):
    continue
if any(marker in text and len(text) < len(marker) + 18 for marker in _PROCEDURE_ZONE_MARKERS):
    continue
```

- [ ] **Step 5: Add KAM detail recovery helper**

Add to `kreports/processor/audit_report_parser.py`:

```python
_KAM_DETAIL_START_HINTS = (
    "핵심감사사항으로 선정한 이유",
    "핵심 감사사항으로 선정한 이유",
    "핵심감사사항이 감사에서 다루어진 방법",
    "감사에서 다루어진 방법",
    "감사인의 대응",
)


def recover_kam_detail_body(full_text: str, extracted_body: str) -> str:
    body = (extracted_body or "").strip()
    if len(body) >= 300 and any(hint in body for hint in _KAM_DETAIL_START_HINTS):
        return body
    text_value = xml_to_text(full_text)
    kam_pos = _find_heading_candidate(text_value, "kam", "핵심감사사항")
    if kam_pos < 0:
        return body
    end = len(text_value)
    for section_key in ("management_responsibility", "auditor_responsibility", "basis_for_opinion", "emphasis", "other_matter"):
        for keyword in SECTION_KEYWORDS[section_key]:
            pos = _find_heading_candidate(text_value[kam_pos + 1:], section_key, keyword)
            if pos >= 0:
                end = min(end, kam_pos + 1 + pos)
                break
    recovered = _trim_section_body("kam", text_value[kam_pos:end].strip())
    if len(recovered) > max(len(body), 120) and any(hint in recovered for hint in _KAM_DETAIL_START_HINTS):
        return recovered
    return body
```

Inside `extract_audit_report_sections(...)`, after the main result extraction and before returning, add:

```python
    if "kam" in result:
        recovered = recover_kam_detail_body(full_text, result["kam"]["body_text"])
        if recovered != result["kam"]["body_text"]:
            result["kam"]["body_text"] = recovered
            result["kam"]["length"] = len(recovered)
```

- [ ] **Step 6: Run parser regression tests**

Run:

```bash
uv run pytest tests/test_audit_report_sections.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add kreports/processor/audit_report_parser.py tests/test_audit_report_sections.py
git commit -m "fix: recover detailed KAM audit procedure text"
```

---

### Task 3: Reindex Audit Procedures From Existing Cached Sections

**Files:**
- Modify: `kreports/collector/report_document_collector.py`
- Modify: `tests/test_audit_report_sections.py` or create `tests/test_audit_procedure_indexer.py`

**Interfaces:**
- Consumes: improved `extract_audit_procedure_items(...)` and `recover_kam_detail_body(...)`.
- Produces: `index_audit_procedures_from_sections(...)` should extract more procedure rows from cached KAM bodies without fetching DART.

- [ ] **Step 1: Write failing indexer test**

Create `tests/test_audit_procedure_indexer.py`:

```python
from datetime import date

from kreports.collector.report_document_collector import index_audit_procedures_from_sections
from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, Company, ReportSection


def test_index_audit_procedures_uses_cached_kam_body_only(temp_engine):
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        session.add(ReportSection(
            corp_code="00126380",
            bsns_year=2025,
            rcept_no="20260301000001_100",
            dcm_no="100",
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text=(
                "수익인식\n"
                "핵심감사사항으로 선정한 이유: 기간귀속 판단에 중요한 왜곡표시위험이 있습니다.\n"
                "감사인의 대응\n"
                "가. 계약서와 세금계산서 대사를 수행하였습니다.\n"
                "나. 보고기간 전후 매출의 기간귀속 테스트를 수행하였습니다.\n"
            ),
            body_length=142,
            ordinal=1,
            fetched_at=date(2026, 3, 1),
        ))

    result = index_audit_procedures_from_sections(year=2025)

    assert result["rows_written"] == 2
    with get_session() as session:
        rows = session.query(AuditProcedureItem).order_by(AuditProcedureItem.procedure_ordinal).all()
    assert [row.procedure_type for row in rows] == ["substantive_test", "cutoff"]
```

- [ ] **Step 2: Run failing indexer test**

Run:

```bash
uv run pytest tests/test_audit_procedure_indexer.py -q
```

Expected: FAIL if current indexer does not write both rows with the improved classifications.

- [ ] **Step 3: Patch indexer to reuse parser output without raw fetch**

If the test fails because the collector bypasses the improved parser, modify `kreports/collector/report_document_collector.py` inside `index_audit_procedures_from_sections(...)` so it calls:

```python
items = extract_audit_procedure_items(section.body_text or "")
```

and preserves `rcept_no`, `dcm_no`, `section_ordinal`, and `procedure_ordinal`.

If the test already passes after Task 2, do not add code; keep this task as the verification slice and commit only the new test.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest tests/test_audit_procedure_indexer.py tests/test_audit_report_sections.py -q
```

Expected: PASS.

- [ ] **Step 5: Reindex a bounded real slice**

Run:

```bash
.venv/bin/kreports index-audit-procedures --year 2025 --limit 500
.venv/bin/kreports audit-procedure-evidence-map --year 2025 --limit 50 --json
```

Expected:

- `index-audit-procedures` reports `rows_written` greater than the pre-task baseline for the bounded slice if cached KAM bodies contain procedure text.
- `audit-procedure-evidence-map` still reports `short_kam_body` if cached KAM sections are truncated; this is a data quality gap, not a parser failure.

- [ ] **Step 6: Commit Task 3**

```bash
git add kreports/collector/report_document_collector.py tests/test_audit_procedure_indexer.py
git commit -m "test: verify cached audit procedure reindexing"
```

---

### Task 4: Attach Linkages to Audit Procedure Search APIs

**Files:**
- Modify: `kreports/analysis/api.py`
- Test: existing audit peer/API tests or create `tests/test_audit_procedure_api.py`

**Interfaces:**
- Consumes: `classify_audit_procedure_linkages(...)`.
- Produces: each procedure record in `search_audit_procedures()` and peer comparison evidence should expose `linkages`.

- [ ] **Step 1: Write failing API test**

Create `tests/test_audit_procedure_api.py`:

```python
from datetime import date

from kreports.analysis.api import search_audit_procedures
from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, Company


def test_search_audit_procedures_returns_linkages_and_source_note(temp_engine):
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI", induty_code="264"))
        session.add(AuditProcedureItem(
            corp_code="00126380",
            bsns_year=2025,
            rcept_no="20260301000001_100",
            dcm_no="100",
            source_type="audit_report",
            section_ordinal=1,
            kam_topic="revenue",
            procedure_type="substantive_test",
            procedure_text="매출 계약서 문서검사와 기간귀속 테스트를 수행하였습니다.",
            procedure_hash="p1",
            procedure_length=32,
            procedure_ordinal=1,
            fetched_at=date(2026, 3, 1),
        ))

    result = search_audit_procedures(company="005930", year=2025, limit=5)

    record = result["companies"][0]["records"][0]
    assert record["linkages"][0]["category"] == "audit_report_kam"
    assert result["data_quality"]["source"] == "audit_procedure_items"
    assert "audit-report KAM" in result["data_quality"]["interpretation"]
```

- [ ] **Step 2: Run failing API test**

Run:

```bash
uv run pytest tests/test_audit_procedure_api.py -q
```

Expected: FAIL because `linkages` is not present.

- [ ] **Step 3: Attach linkages in API**

Modify `kreports/analysis/api.py`:

```python
from kreports.analysis.audit_procedure_evidence import classify_audit_procedure_linkages
```

Inside `search_audit_procedures(...)`, after `procedure_excerpt` is added:

```python
        row["linkages"] = classify_audit_procedure_linkages(
            text_value,
            kam_topic=row.get("kam_topic"),
        )
```

Update `data_quality["interpretation"]` to:

```python
"Procedure items are parsed hints from cached audit-report KAM response paragraphs. "
"The linkages explain which audit-report KAM, financial-statement account, accounting note, or disclosure-event evidence should be checked with the procedure."
```

- [ ] **Step 4: Run API tests**

Run:

```bash
uv run pytest tests/test_audit_procedure_api.py tests/test_audit_report_sections.py tests/test_audit_procedure_evidence.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add kreports/analysis/api.py tests/test_audit_procedure_api.py
git commit -m "feat: attach evidence linkages to audit procedure search"
```

---

### Task 5: Real Dataset Verification and Quality Classification

**Files:**
- Modify only if tests expose a bug in Tasks 1-4.
- No new raw-data files.

**Interfaces:**
- Consumes: CLI commands added above and existing readiness commands.
- Produces: verified before/after numbers for `audit_procedure_items`, short KAM rate, and linkage coverage.

- [ ] **Step 1: Capture before/after procedure counts**

Run:

```bash
.venv/bin/kreports auditor-feature-readiness --year 2025 --json
.venv/bin/kreports audit-kam-quality --year 2025 --json
.venv/bin/kreports audit-procedure-evidence-map --year 2025 --limit 100 --json
```

Expected:

- If `short_kam_body` remains high, report it as a cache-content gap.
- If `audit_procedure_items` improves after reindexing, report the exact row/company increase.
- If it does not improve, report that cached KAM sections are too short and require source-level repair from already externalized raw storage or future DART attachment fetch, not more parser keyword work.

- [ ] **Step 2: Run real API smoke**

Run:

```bash
.venv/bin/python -c "from kreports.analysis.api import search_audit_procedures; import json; print(json.dumps(search_audit_procedures(year=2025, keyword='매출', limit=5), ensure_ascii=False, indent=2)[:4000])"
```

Expected:

- Output has `companies`, `procedure_excerpt`, `linkages`, and `data_quality`.
- Empty output is acceptable only if the diagnostic says cached procedure rows are missing; the final report must classify that as a data gap.

- [ ] **Step 3: Run focused and full tests**

Run:

```bash
uv run pytest tests/test_audit_procedure_evidence.py tests/test_audit_report_sections.py tests/test_audit_procedure_indexer.py tests/test_audit_procedure_api.py -q
uv run pytest -q
```

Expected:

- Focused tests pass.
- Full suite passes or failures are explicitly unrelated to this feature and listed with file/test names.

- [ ] **Step 4: Commit Task 5 if code changed**

If verification required code changes:

```bash
git add <changed feature files only>
git commit -m "fix: align audit procedure evidence quality output"
```

If no code changed, do not create an empty commit.

---

## Implementation Order

1. Task 1 creates the diagnostic and proves the current failure mode without changing DB contents.
2. Task 2 fixes parser behavior where cached text contains useful KAM procedure detail.
3. Task 3 verifies reindexing from cached sections only.
4. Task 4 exposes linkages in MCP-facing API output.
5. Task 5 verifies real dataset quality and separates parser success from cache-content gaps.

## Final Acceptance Criteria

- `audit-procedure-evidence-map` exists and explains whether procedure extraction is blocked by parser logic or short cached KAM bodies.
- `extract_audit_procedure_items()` handles Korean headings `감사인의 대응`, `우리가 수행한 주요 감사절차`, numbered/Hangul bullets, and middle-dot bullets.
- Generic auditor responsibility wording no longer creates procedure rows.
- `search_audit_procedures()` returns procedure records with evidence linkages.
- Real dataset verification states exact row/company counts and does not claim completeness when KAM bodies are truncated.
- No new raw DART source documents are fetched or persisted by this work.
