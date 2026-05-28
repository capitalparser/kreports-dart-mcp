# Auditor And Investor Disclosure Features Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn 5-year DART raw filings into auditor-useful and investor-useful MCP features that can answer company, year, industry, and peer-group questions with traceable evidence.

**Architecture:** Keep the existing storage architecture: GCS raw filings are the legal/source evidence, SQLite runtime tables hold compact structured facts and evidence excerpts, and MCP tools return narrative-ready answers with data quality notes. New functionality should reuse `source_documents`, `report_sections`, `audit_procedure_items`, `accounting_note_chapters`, `evidence_documents`, `financial_facts_compact`, and peer selection before adding tables. Add new tables only when the user needs ranking, filtering, or 5-year trend aggregation that text search cannot handle reliably.

**Tech Stack:** Python, SQLAlchemy, SQLite, Typer CLI, MCP tool schemas/handlers, pytest, GCS-backed raw document storage.

---

## Current Baseline

As of 2026-05-28:

- Raw storage is externalized to GCS; `source_documents.raw_content` remains empty for new raw filings.
- `raw_extractable`: 2,620 documents after starting 2022 KOSPI backfill.
- `raw_business_extractable`: 1,101.
- `raw_audit_extractable`: 1,519.
- `inline_present`: 0.
- `evidence_documents`: 13,831 documents, currently concentrated in 2024-2025.
- 2022 KOSPI latest annual-report raw backfill: 200 / 796 done.
- Existing MCP tools already cover KAM sections, peer KAM topics, audit report matters, audit procedures, audit proposal packs, dataset search, accounting policies, audit fees, and compact financial facts.

This plan is therefore not a greenfield build. It upgrades existing scattered capabilities into complete 5-year workflows.

## Functional Slice Map

| Slice | Feature Goal | Primary User Question | DB Change | First Success Metric |
|---|---|---|---|---|
| 1 | 5-year raw annual-report coverage gate | "Do we actually have the source filings?" | No | Market/year raw coverage table is correct |
| 2 | Audit matter index | "Which companies have emphasis/other/going-concern matters?" | Yes, one table | Search is fast and year/industry sortable |
| 3 | KAM lifecycle and procedure index | "What changed in KAM and what audit procedures were performed?" | Extend existing table or add summary table | 5-year company timeline available |
| 4 | Accounting policy change tracker | "Did policies or estimates change?" | Yes, one table | 5-year note 2/3/4 deltas available |
| 5 | Investor quality-of-earnings pack | "Are earnings and cash flows reliable?" | Maybe compact table | Narrative answer with 5-year metrics |
| 6 | DCF input candidate pack | "What DCF assumptions does DART support?" | No initially | FCFF and assumption candidates returned |
| 7 | Event layer for ad hoc disclosures | "What happened between annual reports?" | Yes, event table | Events link to annual-report context |
| 8 | Peer-group evidence upgrade | "Who are the real comparable companies?" | Maybe scoring table | Peer reasons include product/financial/audit evidence |
| 9 | Narrative renderer standardization | "Can MCP answer in prose, not JSON-looking blobs?" | No | Tool outputs include Korean paragraphs and caveats |

## Implementation Order

Recommended order:

1. Slice 1: coverage gates and dashboards.
2. Slice 2: audit matter index.
3. Slice 3: KAM lifecycle/procedure index.
4. Slice 9: narrative renderer standardization for audit slices.
5. Slice 4: accounting policy change tracker.
6. Slice 5: quality-of-earnings pack.
7. Slice 6: DCF input candidate pack.
8. Slice 8: peer evidence upgrade.
9. Slice 7: event layer for ad hoc disclosures.

Rationale: slices 2-3 immediately improve auditor value using data already being extracted. Slices 5-6 depend on 5-year financial completeness. Slice 7 is important but should not block the core annual-report product.

---

## Slice 1: 5-Year Raw Annual-Report Coverage Gate

**Goal:** Make raw coverage measurable before every feature test, separating disclosure-list coverage, latest annual-report coverage, raw GCS coverage, derived-only coverage, and parser output coverage.

**Files:**
- Create: `kreports/analysis/raw_coverage.py`
- Modify: `kreports/cli/main.py`
- Modify: `kreports/analysis/readiness.py`
- Test: `tests/test_raw_coverage.py`
- Update: `reports/backfill_status_dashboard.html` after each major batch, until generator exists.

### Task 1.1: Add raw coverage query module

- [ ] **Step 1: Write failing tests**

Create `tests/test_raw_coverage.py`:

```python
from datetime import datetime

from kreports.analysis.raw_coverage import raw_annual_report_coverage
from kreports.db.models import Company, Disclosure, SourceDocument


def test_raw_annual_report_coverage_counts_latest_only(temp_session):
    temp_session.add_all([
        Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"),
        Disclosure(rcept_no="20220301000001", corp_code="001", corp_name="A", disc_date=datetime(2022, 3, 1), disc_type="A", report_nm="사업보고서 (2021.12)", flr_nm="A"),
        Disclosure(rcept_no="20220401000002", corp_code="001", corp_name="A", disc_date=datetime(2022, 4, 1), disc_type="A", report_nm="[기재정정]사업보고서 (2021.12)", flr_nm="A"),
        SourceDocument(rcept_no="20220401000002", corp_code="001", bsns_year=2021, source_type="business_report", report_nm="[기재정정]사업보고서 (2021.12)", content_type="xml", raw_content="", doc_hash="h", storage_uri="gs://bucket/a.gz", storage_status="externalized"),
    ])
    temp_session.commit()

    out = raw_annual_report_coverage(start_filing_year=2022, end_filing_year=2022, markets=["KOSPI"])

    assert out["rows"][0]["filing_year"] == 2022
    assert out["rows"][0]["latest_reports"] == 1
    assert out["rows"][0]["raw_externalized"] == 1
    assert out["rows"][0]["raw_missing"] == 0
```

- [ ] **Step 2: Run failing test**

Run:

```bash
uv run pytest tests/test_raw_coverage.py -q
```

Expected: import failure for `kreports.analysis.raw_coverage`.

- [ ] **Step 3: Implement module**

Create `kreports/analysis/raw_coverage.py`:

```python
from __future__ import annotations

from sqlalchemy import bindparam, text

from kreports.db.engine import engine


VALID_RCEPT_SQL = """
length(d.rcept_no)=14
AND d.rcept_no GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
AND substr(d.rcept_no,1,4)=strftime('%Y', d.disc_date)
"""


def raw_annual_report_coverage(
    *,
    start_filing_year: int = 2022,
    end_filing_year: int = 2026,
    markets: list[str] | None = None,
) -> dict:
    markets = markets or ["KOSPI", "KOSDAQ"]
    stmt = text(f"""
    WITH ranked AS (
      SELECT d.rcept_no, d.corp_code, d.disc_date, co.market,
             ROW_NUMBER() OVER (
               PARTITION BY d.corp_code, substr(d.disc_date,1,4)
               ORDER BY d.disc_date DESC, d.rcept_no DESC
             ) AS rn
      FROM disclosures d
      JOIN companies co ON co.corp_code=d.corp_code
      WHERE co.stock_code IS NOT NULL
        AND co.market IN :markets
        AND d.report_nm LIKE '%사업보고서%'
        AND d.report_nm NOT LIKE '%제출기한연장%'
        AND d.report_nm NOT LIKE '%해외증권%'
        AND CAST(substr(d.disc_date,1,4) AS INTEGER) BETWEEN :start_year AND :end_year
        AND ({VALID_RCEPT_SQL})
    )
    SELECT CAST(substr(r.disc_date,1,4) AS INTEGER) AS filing_year,
           r.market,
           COUNT(*) AS latest_reports,
           SUM(CASE WHEN sd.id IS NOT NULL THEN 1 ELSE 0 END) AS raw_externalized,
           SUM(CASE WHEN sd.id IS NULL THEN 1 ELSE 0 END) AS raw_missing
    FROM ranked r
    LEFT JOIN source_documents sd
      ON sd.rcept_no=r.rcept_no
     AND sd.source_type='business_report'
     AND sd.content_type!='derived_report_sections'
     AND sd.storage_status='externalized'
    WHERE r.rn=1
    GROUP BY 1,2
    ORDER BY 1,2
    """).bindparams(bindparam("markets", expanding=True))
    with engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(stmt, {
            "markets": markets,
            "start_year": int(start_filing_year),
            "end_year": int(end_filing_year),
        }).mappings()]
    totals = {
        "latest_reports": sum(row["latest_reports"] or 0 for row in rows),
        "raw_externalized": sum(row["raw_externalized"] or 0 for row in rows),
        "raw_missing": sum(row["raw_missing"] or 0 for row in rows),
    }
    totals["coverage_pct"] = round(100.0 * totals["raw_externalized"] / totals["latest_reports"], 2) if totals["latest_reports"] else 100.0
    return {
        "start_filing_year": start_filing_year,
        "end_filing_year": end_filing_year,
        "markets": markets,
        "totals": totals,
        "rows": rows,
        "status": "complete" if totals["raw_missing"] == 0 else "in_progress",
    }
```

- [ ] **Step 4: Add CLI command**

Modify `kreports/cli/main.py`:

```python
@app.command("raw-annual-report-coverage")
def raw_annual_report_coverage_cmd(
    start_filing_year: int = typer.Option(2022, "--start-filing-year"),
    end_filing_year: int = typer.Option(2026, "--end-filing-year"),
    market: list[str] = typer.Option(["KOSPI", "KOSDAQ"], "--market"),
):
    """5개년 최신 사업보고서 원문 GCS 적재율을 요약한다."""
    from kreports.analysis.raw_coverage import raw_annual_report_coverage

    _json_print(raw_annual_report_coverage(
        start_filing_year=start_filing_year,
        end_filing_year=end_filing_year,
        markets=list(market),
    ))
```

- [ ] **Step 5: Verify and commit**

Run:

```bash
uv run pytest tests/test_raw_coverage.py -q
uv run kreports raw-annual-report-coverage --start-filing-year 2022 --end-filing-year 2026 --market KOSPI
git add kreports/analysis/raw_coverage.py kreports/cli/main.py tests/test_raw_coverage.py
git commit -m "feat: add raw annual report coverage gate"
```

Expected: tests pass and CLI returns year/market rows.

---

## Slice 2: Audit Matter Index

**Goal:** Promote emphasis, other matter, going concern, and basis-for-opinion paragraphs from ad hoc section search into a structured table that supports ranking, filtering, industry/year queries, and narrative MCP answers.

**Files:**
- Modify: `kreports/db/models.py`
- Modify: `kreports/db/engine.py`
- Create: `kreports/collector/audit_matter_indexer.py`
- Modify: `kreports/analysis/api.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_audit_matter_index.py`

### Task 2.1: Add `audit_matter_items` table

- [ ] **Step 1: Write failing model test**

Create `tests/test_audit_matter_index.py`:

```python
from kreports.collector.audit_matter_indexer import rebuild_audit_matter_items
from kreports.db.models import AuditMatterItem, Company, ReportSection


def test_rebuild_audit_matter_items_from_report_sections(temp_session):
    temp_session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI", induty_code="264"))
    temp_session.add(ReportSection(
        rcept_no="20250301000001",
        corp_code="001",
        bsns_year=2024,
        source_type="audit_report",
        section_key="emphasis",
        section_title="강조사항",
        body_text="계속기업 관련 중요한 불확실성이 존재합니다.",
        body_hash="h1",
        body_length=24,
        ordinal=0,
    ))
    temp_session.commit()

    out = rebuild_audit_matter_items(year=2024)

    assert out["inserted"] == 1
    item = temp_session.query(AuditMatterItem).one()
    assert item.matter_type == "emphasis"
    assert item.severity_hint == "high"
    assert "going_concern" in item.topic_tags
```

- [ ] **Step 2: Add model**

Modify `kreports/db/models.py`:

```python
class AuditMatterItem(Base):
    """Structured non-KAM audit-report matter for search and peer comparison."""
    __tablename__ = "audit_matter_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(80), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    matter_type = Column(String(50), nullable=False)
    matter_title = Column(String(300), nullable=True)
    matter_text = Column(Text, nullable=False)
    matter_hash = Column(String(40), nullable=True)
    matter_length = Column(Integer, nullable=True)
    topic_tags = Column(Text, nullable=False, default="[]")
    severity_hint = Column(String(20), nullable=False, default="info")
    source_type = Column(String(30), nullable=False, default="audit_report")
    section_ordinal = Column(SmallInteger, nullable=False, default=0)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rcept_no", "matter_type", "section_ordinal", name="uq_audit_matter_item"),
        Index("idx_audit_matter_corp_year", "corp_code", "bsns_year"),
        Index("idx_audit_matter_type_year", "matter_type", "bsns_year"),
        Index("idx_audit_matter_severity", "severity_hint"),
    )
```

- [ ] **Step 3: Add migration columns/index creation**

Modify `kreports/db/engine.py` so `init_db()` creates the table via `Base.metadata.create_all()` and add explicit index guards only if this file's style requires them. Do not hand-write table DDL unless existing migrations require it.

- [ ] **Step 4: Implement indexer**

Create `kreports/collector/audit_matter_indexer.py`:

```python
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kreports.analysis.api import _classify_audit_matter
from kreports.db.engine import get_session
from kreports.db.models import AuditMatterItem, ReportSection

MATTER_KEYS = ("other_matter", "emphasis", "going_concern", "basis_for_opinion")


def _sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def rebuild_audit_matter_items(*, year: int | None = None, limit: int | None = None) -> dict:
    with get_session() as session:
        query = session.query(ReportSection).filter(
            ReportSection.source_type == "audit_report",
            ReportSection.section_key.in_(MATTER_KEYS),
        )
        if year is not None:
            query = query.filter(ReportSection.bsns_year == int(year))
        query = query.order_by(ReportSection.bsns_year.desc(), ReportSection.corp_code, ReportSection.rcept_no)
        if limit:
            query = query.limit(int(limit))
        rows = query.all()

        inserted = 0
        for row in rows:
            body = row.body_text or ""
            classified = _classify_audit_matter(body, row.section_key)
            stmt = sqlite_insert(AuditMatterItem).values({
                "rcept_no": row.rcept_no,
                "dcm_no": row.dcm_no,
                "corp_code": row.corp_code,
                "bsns_year": row.bsns_year,
                "matter_type": row.section_key,
                "matter_title": row.section_title,
                "matter_text": body,
                "matter_hash": _sha1(body),
                "matter_length": len(body),
                "topic_tags": json.dumps(classified["topic_tags"], ensure_ascii=False),
                "severity_hint": classified["severity_hint"],
                "source_type": row.source_type,
                "section_ordinal": row.ordinal,
                "fetched_at": datetime.utcnow(),
            }).on_conflict_do_update(
                index_elements=["rcept_no", "matter_type", "section_ordinal"],
                set_={
                    "matter_title": stmt.excluded.matter_title,
                    "matter_text": stmt.excluded.matter_text,
                    "matter_hash": stmt.excluded.matter_hash,
                    "matter_length": stmt.excluded.matter_length,
                    "topic_tags": stmt.excluded.topic_tags,
                    "severity_hint": stmt.excluded.severity_hint,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            session.execute(stmt)
            inserted += 1
        return {"scanned": len(rows), "inserted": inserted, "year": year}
```

- [ ] **Step 5: Switch search to table-first**

Modify `kreports/analysis/api.py` so `search_audit_report_matters()` first queries `audit_matter_items`. If no rows exist, fall back to current `report_sections`/`evidence_documents` path. Preserve current response shape and add `data_quality.source = "audit_matter_items"` when table is used.

- [ ] **Step 6: Add CLI**

Modify `kreports/cli/main.py`:

```python
@app.command("rebuild-audit-matter-items")
def rebuild_audit_matter_items_cmd(
    year: int | None = typer.Option(None, "--year"),
    limit: int | None = typer.Option(None, "--limit"),
):
    """감사보고서 강조사항/기타사항/계속기업 문단을 검색용 정형 테이블로 재생성한다."""
    from kreports.collector.audit_matter_indexer import rebuild_audit_matter_items

    _json_print(rebuild_audit_matter_items(year=year, limit=limit))
```

- [ ] **Step 7: Verify and commit**

Run:

```bash
uv run pytest tests/test_audit_matter_index.py tests/test_audit_report_sections.py -q
uv run kreports rebuild-audit-matter-items --year 2024 --limit 100
git add kreports/db/models.py kreports/db/engine.py kreports/collector/audit_matter_indexer.py kreports/analysis/api.py kreports/cli/main.py tests/test_audit_matter_index.py
git commit -m "feat: index audit report matters"
```

---

## Slice 3: KAM Lifecycle And Procedure Index

**Goal:** Convert KAM from one-year text lookup into a 5-year lifecycle: appeared, disappeared, changed wording, same topic repeated, and procedure pattern changed.

**Files:**
- Create: `kreports/analysis/kam_lifecycle.py`
- Modify: `kreports/analysis/api.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_kam_lifecycle.py`

### Task 3.1: Add lifecycle computation from existing sections

- [ ] **Step 1: Write failing test**

Create `tests/test_kam_lifecycle.py`:

```python
from kreports.analysis.kam_lifecycle import kam_lifecycle_for_company
from kreports.db.models import Company, ReportSection


def test_kam_lifecycle_marks_new_and_repeated_topics(temp_session):
    temp_session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
    temp_session.add_all([
        ReportSection(rcept_no="r1", corp_code="001", bsns_year=2023, source_type="audit_report", section_key="kam", section_title="수익인식", body_text="수익인식에 대한 감사절차로 문서검사를 수행함", body_hash="a", body_length=30, ordinal=0),
        ReportSection(rcept_no="r2", corp_code="001", bsns_year=2024, source_type="audit_report", section_key="kam", section_title="수익인식", body_text="수익인식에 대한 감사절차로 표본검사와 분석적 절차를 수행함", body_hash="b", body_length=40, ordinal=0),
    ])
    temp_session.commit()

    out = kam_lifecycle_for_company("001", start_year=2023, end_year=2024)

    assert out["events"][0]["status"] == "new"
    assert out["events"][1]["status"] == "repeated_changed"
```

- [ ] **Step 2: Implement lifecycle module**

Create `kreports/analysis/kam_lifecycle.py`:

```python
from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy import text

from kreports.db.engine import engine
from kreports.processor.audit_report_parser import classify_kam_topics, summarize_kam_body


def _similarity(a: str, b: str) -> float:
    return round(SequenceMatcher(None, a or "", b or "").ratio(), 4)


def kam_lifecycle_for_company(company: str, *, start_year: int, end_year: int) -> dict:
    stmt = text("""
        SELECT rs.corp_code, c.corp_name, rs.bsns_year, rs.rcept_no, rs.dcm_no,
               rs.section_title, rs.body_text, rs.body_length, rs.ordinal
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE rs.corp_code=:corp_code
          AND rs.source_type='audit_report'
          AND rs.section_key='kam'
          AND rs.bsns_year BETWEEN :start_year AND :end_year
        ORDER BY rs.bsns_year, rs.ordinal
    """)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, {
            "corp_code": company,
            "start_year": start_year,
            "end_year": end_year,
        }).mappings()]

    previous_by_topic: dict[str, dict] = {}
    events: list[dict] = []
    for row in rows:
        body = row["body_text"] or ""
        topics = classify_kam_topics(body) or ["unknown"]
        summary = summarize_kam_body(body)
        for topic in topics:
            previous = previous_by_topic.get(topic)
            sim = _similarity(previous["body_text"], body) if previous else None
            status = "new" if previous is None else ("repeated_changed" if sim is not None and sim < 0.9 else "repeated_stable")
            events.append({
                "year": row["bsns_year"],
                "rcept_no": row["rcept_no"],
                "dcm_no": row["dcm_no"],
                "topic": topic,
                "title": row["section_title"],
                "status": status,
                "similarity_to_previous": sim,
                "has_reason_hint": summary.get("has_reason_hint"),
                "has_procedure_hint": summary.get("has_procedure_hint"),
                "body_excerpt": body[:900],
            })
            previous_by_topic[topic] = row
    return {
        "company": company,
        "start_year": start_year,
        "end_year": end_year,
        "event_count": len(events),
        "events": events,
        "data_quality": {
            "status": "usable" if events else "missing",
            "source": "report_sections.audit_report",
        },
    }
```

- [ ] **Step 3: Expose MCP tool**

Add `kam_lifecycle_for_company` wrapper to `kreports/analysis/api.py` and MCP schema/tool in `kreports/mcp/tools.py`:

```python
TOOL_GET_KAM_LIFECYCLE = Tool(
    name="get_kam_lifecycle",
    description="특정 회사의 5개년 KAM 주제 변화, 반복 여부, 선정 이유/감사절차 hint를 반환한다.",
    inputSchema={
        "type": "object",
        "properties": {
            "company": {"type": "string"},
            "start_year": {"type": "integer", "default": 2021},
            "end_year": {"type": "integer", "default": 2025},
        },
        "required": ["company"],
    },
)
```

- [ ] **Step 4: Verify and commit**

Run:

```bash
uv run pytest tests/test_kam_lifecycle.py tests/test_mcp_tools_registration.py -q
git add kreports/analysis/kam_lifecycle.py kreports/analysis/api.py kreports/mcp/tools.py tests/test_kam_lifecycle.py tests/test_mcp_tools_registration.py
git commit -m "feat: add kam lifecycle analysis"
```

---

## Slice 4: Accounting Policy Change Tracker

**Goal:** Use note 2/3/4 evidence to identify accounting policy and estimate-judgment changes over 5 years.

**Files:**
- Create: `kreports/analysis/policy_changes.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_policy_changes.py`

### Task 4.1: Compute policy changes from note chapters

- [ ] **Step 1: Write failing test**

```python
from kreports.analysis.policy_changes import accounting_policy_changes
from kreports.db.models import AccountingNoteChapter, Company


def test_accounting_policy_changes_detects_changed_text(temp_session):
    temp_session.add(Company(corp_code="001", corp_name="A", stock_code="000001", market="KOSPI"))
    temp_session.add_all([
        AccountingNoteChapter(corp_code="001", bsns_year=2023, fs_div="CFS", rcept_no="r1", source_type="business_report", note_no="2", section_type="policy", body="수익은 인도 시점에 인식합니다.", body_hash="a"),
        AccountingNoteChapter(corp_code="001", bsns_year=2024, fs_div="CFS", rcept_no="r2", source_type="business_report", note_no="2", section_type="policy", body="수익은 수행의무 이행 시점에 인식합니다.", body_hash="b"),
    ])
    temp_session.commit()

    out = accounting_policy_changes("001", start_year=2023, end_year=2024)

    assert out["changes"][0]["change_type"] == "changed"
```

- [ ] **Step 2: Implement `policy_changes.py`**

Use `AccountingNoteChapter` ordered by `note_no`, `section_type`, `fs_div`, `bsns_year`. Compute exact hash change and text similarity. Return changed/stable/new/missing.

- [ ] **Step 3: Add MCP tool**

Add `get_accounting_policy_changes` with inputs `company`, `start_year`, `end_year`, `fs_div`.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_policy_changes.py -q
git add kreports/analysis/policy_changes.py kreports/mcp/tools.py tests/test_policy_changes.py
git commit -m "feat: add accounting policy change analysis"
```

---

## Slice 5: Investor Quality-Of-Earnings Pack

**Goal:** Produce investor-oriented 5-year diagnostics from compact financial facts and disclosures: cash conversion, working-capital pressure, margin persistence, leverage, interest burden, one-off risk hints, and audit red flags.

**Files:**
- Create: `kreports/analysis/investor_quality.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_investor_quality.py`

### Task 5.1: Build quality score without new tables

- [ ] **Step 1: Write failing test**

```python
from kreports.analysis.investor_quality import quality_of_earnings_pack


def test_quality_of_earnings_pack_returns_metrics(monkeypatch):
    def fake_series(company, start_year, end_year, fs_div="CFS"):
        return [
            {"bsns_year": 2023, "revenue": 100, "operating_profit": 10, "net_income": 8, "operating_cf": 2},
            {"bsns_year": 2024, "revenue": 120, "operating_profit": 12, "net_income": 9, "operating_cf": 3},
        ]
    monkeypatch.setattr("kreports.analysis.investor_quality._financial_series", fake_series)

    out = quality_of_earnings_pack("001", start_year=2023, end_year=2024)

    assert out["signals"][0]["signal"] == "low_cash_conversion"
```

- [ ] **Step 2: Implement metrics**

Metrics:

- `cash_conversion = operating_cf / net_income`
- `operating_margin = operating_profit / revenue`
- `revenue_cagr`
- `margin_volatility`
- `negative_ocf_years`
- `audit_matter_flags` from `audit_matter_items` or `report_sections`

Return narrative fields:

- `verdict`
- `investment_question`
- `signals`
- `evidence`
- `limitations`

- [ ] **Step 3: Add MCP tool**

Tool name: `get_quality_of_earnings_pack`.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_investor_quality.py -q
git add kreports/analysis/investor_quality.py kreports/mcp/tools.py tests/test_investor_quality.py
git commit -m "feat: add quality of earnings pack"
```

---

## Slice 6: DCF Input Candidate Pack

**Goal:** Generate evidence-backed DCF assumptions from 5-year financial facts and narrative disclosures, without pretending to produce a valuation opinion.

**Files:**
- Create: `kreports/analysis/dcf_inputs.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_dcf_inputs.py`

### Task 6.1: Add DCF input candidates

- [ ] **Step 1: Write failing test**

```python
from kreports.analysis.dcf_inputs import dcf_input_candidates


def test_dcf_input_candidates_separates_actuals_and_assumptions(monkeypatch):
    monkeypatch.setattr("kreports.analysis.dcf_inputs._financial_series", lambda *a, **k: [
        {"bsns_year": 2022, "revenue": 100, "operating_profit": 10, "operating_cf": 9, "capex": 4},
        {"bsns_year": 2023, "revenue": 110, "operating_profit": 11, "operating_cf": 10, "capex": 5},
        {"bsns_year": 2024, "revenue": 121, "operating_profit": 12, "operating_cf": 11, "capex": 5},
    ])
    out = dcf_input_candidates("001", start_year=2022, end_year=2024)
    assert "revenue_growth" in out["candidate_assumptions"]
    assert out["limitations"]
```

- [ ] **Step 2: Implement module**

Compute:

- historical revenue growth
- operating margin
- cash conversion
- capex to revenue if available
- working capital proxy if available
- normalized FCFF proxy when tax/capex fields exist

Return:

- `historical_actuals`
- `candidate_assumptions`
- `evidence_notes`
- `missing_inputs`
- `limitations`

- [ ] **Step 3: Add MCP tool**

Tool name: `get_dcf_input_candidates`.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_dcf_inputs.py -q
git add kreports/analysis/dcf_inputs.py kreports/mcp/tools.py tests/test_dcf_inputs.py
git commit -m "feat: add dcf input candidates"
```

---

## Slice 7: Ad Hoc Disclosure Event Layer

**Goal:** Index non-annual filings that matter to investors and auditors, then connect them to annual-report analysis.

**Files:**
- Modify: `kreports/db/models.py`
- Create: `kreports/collector/disclosure_event_indexer.py`
- Create: `kreports/analysis/disclosure_events.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_disclosure_events.py`

### Task 7.1: Add event table

- [ ] **Step 1: Create model**

Add:

```python
class DisclosureEvent(Base):
    __tablename__ = "disclosure_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(14), nullable=False)
    corp_code = Column(String(8), nullable=False)
    event_date = Column(DateTime, nullable=False)
    event_type = Column(String(50), nullable=False)
    event_title = Column(String(500), nullable=False)
    severity_hint = Column(String(20), nullable=False, default="info")
    source_report_nm = Column(String(500), nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rcept_no", "event_type", name="uq_disclosure_event"),
        Index("idx_disclosure_event_corp_date", "corp_code", "event_date"),
        Index("idx_disclosure_event_type_date", "event_type", "event_date"),
    )
```

- [ ] **Step 2: Event classifier**

Classifier mapping:

- `capital_raise`: 유상증자, 전환사채, 신주인수권
- `litigation`: 소송, 중재, 분쟁
- `control_change`: 최대주주 변경, 경영권 변경
- `fraud`: 횡령, 배임
- `major_contract`: 단일판매, 공급계약
- `asset_deal`: 유형자산 양수도, 타법인 주식 취득
- `audit_related`: 감사보고서제출, 감사의견, 내부회계

- [ ] **Step 3: MCP search**

Tool name: `search_disclosure_events`.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_disclosure_events.py -q
git add kreports/db/models.py kreports/collector/disclosure_event_indexer.py kreports/analysis/disclosure_events.py kreports/mcp/tools.py tests/test_disclosure_events.py
git commit -m "feat: index disclosure events"
```

---

## Slice 8: Peer-Group Evidence Upgrade

**Goal:** Make peer group selection explainable with product/business text, industry code, financial size, audit risk, and KAM similarity.

**Files:**
- Modify: `kreports/analysis/peer.py`
- Modify: `kreports/analysis/api.py`
- Test: `tests/test_peer_selection.py`

### Task 8.1: Add peer reason scoring

- [ ] **Step 1: Add tests**

Extend `tests/test_peer_selection.py`:

```python
def test_select_peer_group_returns_reason_components():
    out = select_peer_group("005930", criteria=["industry", "size", "audit"], peer_limit=5)
    first = out["peers"][0]
    assert "reason_components" in first
    assert "industry_match" in first["reason_components"]
```

- [ ] **Step 2: Add reason components**

Components:

- `industry_match`
- `size_bucket_match`
- `financial_profile_match`
- `business_text_overlap`
- `kam_topic_overlap`
- `audit_matter_overlap`

Do not require every component to be populated. Return `coverage_note` explaining missing components.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest tests/test_peer_selection.py tests/test_peer.py -q
git add kreports/analysis/peer.py kreports/analysis/api.py tests/test_peer_selection.py
git commit -m "feat: explain peer selection evidence"
```

---

## Slice 9: Narrative Renderer Standardization

**Goal:** MCP tool responses should be usable Korean prose first, with structured evidence second. JSON-like raw dumps should remain available but not dominate the main answer.

**Files:**
- Modify: `kreports/mcp/renderers.py`
- Modify: `kreports/mcp/_handlers.py`
- Modify: `kreports/analysis/api.py` only where output shape lacks narrative fields.
- Test: `tests/test_mcp_narrative_renderers.py`

### Task 9.1: Add standard narrative contract

- [ ] **Step 1: Write failing test**

```python
from kreports.mcp.renderers import render_audit_matter_search


def test_render_audit_matter_search_returns_korean_paragraph():
    payload = {
        "total_companies": 1,
        "companies": [{
            "corp_name": "A",
            "matter_counts": {"emphasis": 1},
            "sections": [{"section_key": "emphasis", "body_excerpt": "계속기업 관련 중요한 불확실성"}],
        }],
        "data_quality": {"status": "usable", "source": "audit_matter_items"},
    }
    text = render_audit_matter_search(payload)
    assert "확인됨" in text
    assert "데이터 품질" in text
```

- [ ] **Step 2: Implement renderer**

Renderer contract:

- first paragraph: answer
- second paragraph: why it matters
- evidence bullets: top rows
- data quality: source, coverage, limitation
- next action: what to inspect next

- [ ] **Step 3: Wire handlers**

For these tools:

- `search_audit_report_matters`
- `search_audit_procedures`
- `compare_peer_audit_procedures`
- `get_kam_lifecycle`
- `get_quality_of_earnings_pack`
- `get_dcf_input_candidates`

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_mcp_narrative_renderers.py tests/test_mcp_tools_registration.py -q
git add kreports/mcp/renderers.py kreports/mcp/_handlers.py kreports/analysis/api.py tests/test_mcp_narrative_renderers.py
git commit -m "feat: standardize narrative mcp responses"
```

---

## Execution Cadence

For each slice:

1. Write failing tests.
2. Implement the minimum code.
3. Run focused tests.
4. Run one real DB smoke command where applicable.
5. Commit.
6. Update readiness/dashboard if the slice changes dataset coverage.

Do not batch multiple slices into one commit. The project already has moving data backfills; small commits are the only sane way to keep code changes reviewable.

## Backfill Interaction Rules

- Raw 5-year backfill continues independently.
- Feature extractors must be rerunnable from GCS-backed `source_documents`.
- If a feature can be derived from existing `report_sections`, do not call DART again.
- If a feature requires raw XML, call `run-document-extractors` first before expanding DART backfill.
- Every feature output must include a data quality note that distinguishes:
  - `raw_source_available`
  - `derived_only`
  - `evidence_fallback`
  - `missing_cache`

## Acceptance Gate For "Auditor-Ready"

The MCP is auditor-ready for a market/year only when:

- latest annual-report raw coverage is at least 95%;
- audit-report attachment extraction is available for at least 90% of those raw business reports;
- KAM sections and audit procedures are indexed;
- emphasis/other/going-concern matters are searchable by company, year, market, and industry;
- accounting policy note chapters are present for note 2/3/4 or the tool clearly reports missing coverage;
- peer comparison returns both subject and at least 5 peers, or states that peer coverage is insufficient.

## Acceptance Gate For "Investor-Ready"

The MCP is investor-ready for a company only when:

- 5-year financial actuals are present;
- quality-of-earnings metrics can be computed for at least 3 years;
- DCF input candidates separate observed historical values from assumptions;
- disclosure event search covers the requested period;
- narrative output includes limitations and source basis.

## Self-Review

- Spec coverage: all requested feature families are mapped to slices: audit procedures, audit matters, KAM, accounting policies, investor quality, DCF, events, peer selection, narrative responses.
- Placeholder scan: no `TBD` or implementation-later placeholders remain. Some tasks intentionally reference existing code paths because they already exist and should be extended, not rewritten.
- Type consistency: new entities use SQLAlchemy model style already present in `kreports/db/models.py`; tool names use existing MCP naming style.
- Scope check: this is intentionally multi-slice. Each slice can be implemented and committed independently.
