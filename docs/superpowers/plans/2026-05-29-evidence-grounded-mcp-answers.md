# Evidence-Grounded MCP Answers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade KReports MCP answers so they combine natural Korean prose, DART filing links, verified filing facts, and clearly labeled analysis without rigid numbered fact labels.

**Architecture:** Add a small evidence/citation helper layer under `kreports/analysis`, then enrich tool result dictionaries with `confirmed_facts`, `analysis`, and `next_checks`. Keep domain extraction in analysis modules and user-facing prose in `kreports/mcp/renderers.py`.

**Tech Stack:** Python 3.12, SQLAlchemy/SQLite, existing MCP renderer, pytest.

---

## File Structure

- Create `kreports/analysis/evidence.py`: DART URL construction, parent receipt extraction, source line formatting, and confirmed fact helpers.
- Modify `kreports/analysis/api.py`: enrich `get_business_overview` first, then investor/auditor tools in later slices.
- Modify `kreports/mcp/renderers.py`: render `confirmed_facts`, `analysis`, `next_checks` as professional Korean prose with source lines.
- Add `tests/test_evidence_helpers.py`: helper tests.
- Extend `tests/test_mcp_narrative_renderers.py`: renderer tests proving natural source lines and no numbered evidence labels.
- Extend `tests/test_business_report_cached_tools.py`: business overview evidence contract tests.

## Task 1: Common Evidence Helpers

**Files:**
- Create: `kreports/analysis/evidence.py`
- Test: `tests/test_evidence_helpers.py`

- [ ] **Step 1: Write failing helper tests**

```python
from kreports.analysis.evidence import dart_filing_url, parent_rcept_no, source_line


def test_dart_filing_url_uses_plain_receipt_number():
    assert dart_filing_url("20260316001520") == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260316001520"


def test_parent_rcept_no_extracts_from_attached_document_id():
    assert parent_rcept_no("20260316001520_20260316001520_00761_xml") == "20260316001520"


def test_source_line_uses_parent_receipt_for_dart_link():
    source = {
        "corp_name": "SK이터닉스",
        "report_nm": "사업보고서 (2025.12)",
        "section_title": "II. 사업의 내용",
        "rcept_no": "20260316001520_20260316001520_00761_xml",
    }
    line = source_line(source)
    assert "출처: SK이터닉스 사업보고서 (2025.12), II. 사업의 내용, 접수번호 20260316001520" in line
    assert "공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260316001520" in line
    assert "첨부문서 식별자: 20260316001520_20260316001520_00761_xml" in line
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_evidence_helpers.py -q`

Expected: import error for `kreports.analysis.evidence`.

- [ ] **Step 3: Implement helper module**

```python
from __future__ import annotations

import re
from typing import Any

DART_FILING_URL_PREFIX = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
_PLAIN_RCEPT_RE = re.compile(r"^\d{14}$")


def parent_rcept_no(rcept_no: str | None) -> str | None:
    value = str(rcept_no or "").strip()
    if _PLAIN_RCEPT_RE.match(value):
        return value
    match = re.search(r"(\d{14})", value)
    return match.group(1) if match else None


def dart_filing_url(rcept_no: str | None) -> str | None:
    parent = parent_rcept_no(rcept_no)
    return f"{DART_FILING_URL_PREFIX}{parent}" if parent else None


def source_line(source: dict[str, Any]) -> str:
    corp_name = source.get("corp_name") or source.get("corp_code") or "대상 회사"
    report_nm = source.get("report_nm") or source.get("source_table") or "공시자료"
    section = source.get("section_title") or source.get("section_key")
    rcept_no = source.get("parent_rcept_no") or parent_rcept_no(source.get("rcept_no"))
    parts = [f"출처: {corp_name} {report_nm}"]
    if section:
        parts[0] += f", {section}"
    if rcept_no:
        parts[0] += f", 접수번호 {rcept_no}"
    url = dart_filing_url(rcept_no or source.get("rcept_no"))
    if url:
        parts.append(f"공시 링크: {url}")
    if source.get("rcept_no") and source.get("rcept_no") != rcept_no:
        parts.append(f"첨부문서 식별자: {source.get('rcept_no')}")
    return "\n".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_evidence_helpers.py -q`

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/evidence.py tests/test_evidence_helpers.py
git commit -m "feat: add MCP evidence citation helpers"
```

## Task 2: Renderer Support For Confirmed Facts

**Files:**
- Modify: `kreports/mcp/renderers.py`
- Test: `tests/test_mcp_narrative_renderers.py`

- [ ] **Step 1: Write failing renderer test**

```python
from kreports.mcp.renderers import render_answer


def test_generic_renderer_prints_confirmed_facts_with_source_lines():
    text = render_answer("get_business_overview", {
        "corp_name": "SK이터닉스",
        "data_quality": {"status": "usable"},
        "confirmed_facts": [{
            "statement": "SK이터닉스는 태양광, 풍력, 연료전지 및 ESS를 주요 사업으로 설명합니다.",
            "source": {
                "corp_name": "SK이터닉스",
                "report_nm": "사업보고서 (2025.12)",
                "section_title": "II. 사업의 내용",
                "rcept_no": "20260316001520",
            },
        }],
        "analysis": [{
            "perspective": "auditor",
            "statement": "EPC와 장기 프로젝트 매출은 진행률과 총공사원가 추정 검토가 필요합니다.",
        }],
        "next_checks": ["감사보고서 KAM 본문과 감사절차를 추가 확인하세요."],
    })
    assert "공시에서 확인되는 내용" in text
    assert "SK이터닉스는 태양광" in text
    assert "출처: SK이터닉스 사업보고서 (2025.12), II. 사업의 내용, 접수번호 20260316001520" in text
    assert "공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260316001520" in text
    assert "감사인 관점 해석" in text
    assert "1번 근거" not in text
    assert "[Fact" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_narrative_renderers.py::test_generic_renderer_prints_confirmed_facts_with_source_lines -q`

Expected: FAIL because renderer does not render `confirmed_facts`.

- [ ] **Step 3: Implement renderer helper**

Add `_render_evidence_grounded_sections(result)` in `kreports/mcp/renderers.py`. It should append:

```text
공시에서 확인되는 내용:
- {statement}
  출처: ...
  공시 링크: ...

감사인 관점 해석:
- ...

확인 한계와 다음 확인:
- ...
```

Use `kreports.analysis.evidence.source_line`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_narrative_renderers.py::test_generic_renderer_prints_confirmed_facts_with_source_lines -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kreports/mcp/renderers.py tests/test_mcp_narrative_renderers.py
git commit -m "feat: render evidence-grounded MCP answers"
```

## Task 3: `get_business_overview` Evidence Pack

**Files:**
- Modify: `kreports/analysis/api.py`
- Test: `tests/test_business_report_cached_tools.py`

- [ ] **Step 1: Write failing business overview evidence test**

```python
def test_get_business_overview_returns_confirmed_facts_with_sources(temp_engine):
    from kreports.analysis.api import get_business_overview
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Disclosure, ReportSection
    from datetime import date

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="대상", market="KOSPI", induty_code="411"))
        session.add(Disclosure(rcept_no="20260316001520", corp_code="00000001", corp_name="대상", disc_date=date(2026, 3, 16), disc_type="A", report_nm="사업보고서 (2025.12)"))
        session.add(ReportSection(rcept_no="20260316001520", corp_code="00000001", bsns_year=2025, source_type="business_report", section_key="business_overview", section_title="1. 사업의 개요", body_text="태양광, 풍력, 연료전지 및 ESS 사업을 영위합니다.", body_hash="h", body_length=32, ordinal=0))

    out = get_business_overview("000001", bsns_year=2025)
    fact = out["confirmed_facts"][0]
    assert "태양광" in fact["statement"]
    assert fact["source"]["report_nm"] == "사업보고서 (2025.12)"
    assert fact["source"]["rcept_no"] == "20260316001520"
    assert fact["source"]["dart_url"].endswith("20260316001520")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_business_report_cached_tools.py::test_get_business_overview_returns_confirmed_facts_with_sources -q`

Expected: FAIL because `confirmed_facts` is missing.

- [ ] **Step 3: Implement business overview fact assembly**

In `get_business_overview`, join `disclosures` by `rcept_no` for report name. Add 2-4 concise confirmed facts from available sections:

- business overview,
- business description,
- risk management,
- management plan.

Each fact source must include `corp_code`, `corp_name`, `report_nm`, `bsns_year`, `rcept_no`, `section_key`, `section_title`, `dart_url`, and `source_table`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_business_report_cached_tools.py::test_get_business_overview_returns_confirmed_facts_with_sources -q`

Expected: PASS.

- [ ] **Step 5: Run SK이터닉스 smoke**

Run:

```bash
uv run python - <<'PY'
import json
from kreports.mcp.tools import call_tool
out = json.loads(call_tool("get_business_overview", {"company": "475150", "bsns_year": 2025}))
print(out["answer"])
PY
```

Expected: answer includes `공시에서 확인되는 내용`, `출처: SK이터닉스 사업보고서`, and a DART link.

- [ ] **Step 6: Commit**

```bash
git add kreports/analysis/api.py tests/test_business_report_cached_tools.py
git commit -m "feat: add evidence pack to business overview"
```

## Task 4: Regression

**Files:**
- No new files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
uv run pytest tests/test_evidence_helpers.py tests/test_mcp_narrative_renderers.py tests/test_business_report_cached_tools.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full suite**

Run: `uv run pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Inspect git**

Run: `git status --short --branch`

Expected: clean branch after commits.
