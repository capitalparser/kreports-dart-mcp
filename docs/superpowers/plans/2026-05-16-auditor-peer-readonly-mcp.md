# Auditor Peer Read-Only MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make KReports MCP usable by external auditors without a DART API key, with dataset readiness reporting and auditor-oriented peer group / industry comparison tools backed by the pre-built local DB.

**Architecture:** Split the product into two modes: collection mode, where maintainers use DART API keys to build `kreports.db`, and read-only MCP mode, where end users only query cached DB data. Add a reusable peer-resolution layer that produces explicit peer selection evidence, then build auditor peer comparison tools on top of financials, audit fees, auditors, disclosures, and cached accounting policies.

**Tech Stack:** Python 3.12, Typer CLI, SQLAlchemy/SQLite, MCP Python SDK, Pydantic schemas, pytest.

---

## Scope And Non-Goals

Required scope:
- Dataset aggregation and readiness CLI for auditor use, with business reports, audit reports, auditor history, and annual financials as the required core dataset.
- DART-key-free read-only MCP runtime.
- Cache-first accounting policy lookup.
- Explicit peer group selection tool.
- Auditor-oriented peer comparison using disclosures, auditor history, financial risk metrics, and audit fee/hour data as an optional benchmark layer.
- CLI smoke tests for all externally exposed read-only MCP tools.

Non-goals:
- Do not implement audit procedures, standard audit hour conclusions, KAM determination, or audit opinion judgment.
- Do not require external MCP users to provide `DART_API_KEY`.
- Do not fetch DART data from MCP tool handlers.

## File Structure

- Modify `kreports/config.py`
  - Add runtime mode settings for `readonly` vs `collector`.
- Create `kreports/runtime.py`
  - Central helper for runtime mode checks and read-only guard messages.
- Create `kreports/analysis/readiness.py`
  - Dataset aggregation and auditor-readiness metrics.
- Modify `kreports/cli/main.py`
  - Add `dataset-auditor-readiness`, `mcp-smoke`, and improve `collect-policies` target selection.
- Modify `kreports/analysis/queries.py`
  - Add cached accounting policy query.
- Modify `kreports/analysis/api.py`
  - Add cache-first `get_accounting_policy`, `select_peer_group`, `compare_peer_audit_fees`, and `compare_peer_risk_profile`.
- Modify `kreports/analysis/peer.py`
  - Add `fs_strategy=auto` and peer reason-code output.
- Modify `kreports/mcp/tools.py`
  - Add MCP tool schemas/handlers for peer selection and auditor peer comparisons.
- Modify `kreports/mcp/server.py`
  - Keep read-only guard explicit in logging and avoid leaking arguments that may contain secrets.
- Add `tests/test_readonly_mcp.py`
  - Prove MCP tools work without `DART_API_KEY`.
- Add `tests/test_auditor_readiness.py`
  - Prove readiness metrics and thresholds.
- Add `tests/test_peer_selection.py`
  - Prove explicit peer reasons and `fs_strategy=auto`.
- Add `tests/test_accounting_policy_cache.py`
  - Prove cache-first policy lookup does not call DART.
- Add `tests/test_auditor_peer_tools.py`
  - Prove audit fee/risk peer comparison shapes.

---

### Task 1: Add Runtime Mode Guard

**Files:**
- Modify: `kreports/config.py`
- Create: `kreports/runtime.py`
- Test: `tests/test_readonly_mcp.py`

- [ ] **Step 1: Write failing tests for read-only mode**

Add to `tests/test_readonly_mcp.py`:

```python
import os

from kreports.runtime import is_readonly_mode, require_collector_mode, readonly_cache_miss


def test_readonly_mode_defaults_to_true_for_mcp(monkeypatch):
    monkeypatch.delenv("KREPORTS_RUNTIME_MODE", raising=False)
    assert is_readonly_mode() is True


def test_collector_mode_can_be_enabled(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    assert is_readonly_mode() is False


def test_require_collector_mode_blocks_readonly(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    try:
        require_collector_mode("collect-policies")
    except RuntimeError as exc:
        assert "collect-policies requires collector mode" in str(exc)
    else:
        raise AssertionError("collector guard did not raise")


def test_readonly_cache_miss_message_does_not_request_dart_key():
    msg = readonly_cache_miss("accounting_policy", "00126380", 2025)
    assert "pre-built DB" in msg
    assert "DART_API_KEY" not in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_readonly_mcp.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'kreports.runtime'`.

- [ ] **Step 3: Implement runtime helper**

Create `kreports/runtime.py`:

```python
from __future__ import annotations

import os
from typing import Any


READONLY = "readonly"
COLLECTOR = "collector"


def runtime_mode() -> str:
    raw = os.environ.get("KREPORTS_RUNTIME_MODE", READONLY).strip().lower()
    if raw not in {READONLY, COLLECTOR}:
        return READONLY
    return raw


def is_readonly_mode() -> bool:
    return runtime_mode() == READONLY


def require_collector_mode(operation: str) -> None:
    if is_readonly_mode():
        raise RuntimeError(
            f"{operation} requires collector mode. Set "
            "KREPORTS_RUNTIME_MODE=collector on the maintainer machine."
        )


def readonly_cache_miss(dataset: str, company: str | None = None, year: Any = None) -> str:
    parts = [f"{dataset} is not available in the pre-built DB"]
    if company:
        parts.append(f"company={company}")
    if year is not None:
        parts.append(f"year={year}")
    parts.append("refresh the maintainer dataset and redeploy the DB artifact")
    return "; ".join(parts)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/pytest tests/test_readonly_mcp.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add kreports/runtime.py tests/test_readonly_mcp.py
git commit -m "feat: add read-only runtime guard"
```

---

### Task 2: Add Auditor Dataset Readiness CLI

**Files:**
- Create: `kreports/analysis/readiness.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_auditor_readiness.py`

- [ ] **Step 1: Write failing readiness tests**

Add to `tests/test_auditor_readiness.py`:

```python
from kreports.analysis.readiness import readiness_verdict


def test_readiness_verdict_passes_core_thresholds():
    snapshot = {
        "markets": {
            "KOSPI": {"listed": 838, "financial_any_2025": 835, "audit_fee_2025": 835, "disclosure_recent": 838},
            "KOSDAQ": {"listed": 1817, "financial_any_2025": 1808, "audit_fee_2025": 1809, "disclosure_recent": 1817},
        },
        "policy_corps": 7,
        "auditor_2025_corps": 740,
    }
    out = readiness_verdict(snapshot)
    assert out["verdict"] == "conditional_pass"
    assert "accounting_policy" in out["recommended_gaps"]


def test_readiness_verdict_fails_when_financial_coverage_low():
    snapshot = {
        "markets": {
            "KOSPI": {"listed": 838, "financial_any_2025": 400, "audit_fee_2025": 835, "disclosure_recent": 838},
            "KOSDAQ": {"listed": 1817, "financial_any_2025": 1808, "audit_fee_2025": 1809, "disclosure_recent": 1817},
        },
        "policy_corps": 7,
        "auditor_2025_corps": 740,
    }
    out = readiness_verdict(snapshot)
    assert out["verdict"] == "fail"
    assert "financial_any_2025" in out["required_gaps"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_auditor_readiness.py -q
```

Expected: FAIL with missing module/function.

- [ ] **Step 3: Implement readiness module**

Create `kreports/analysis/readiness.py`:

```python
from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import engine


CORE_MARKETS = ("KOSPI", "KOSDAQ")


def pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def auditor_readiness_snapshot(year: int = 2025) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                WITH listed AS (
                  SELECT corp_code, market
                  FROM companies
                  WHERE stock_code IS NOT NULL
                    AND market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                ),
                fin_any AS (
                  SELECT DISTINCT corp_code FROM financials
                  WHERE year=:year AND quarter=4
                ),
                fin_cfs AS (
                  SELECT DISTINCT corp_code FROM financials
                  WHERE year=:year AND quarter=4 AND fs_div='CFS'
                ),
                fin_ofs AS (
                  SELECT DISTINCT corp_code FROM financials
                  WHERE year=:year AND quarter=4 AND fs_div='OFS'
                ),
                fee AS (
                  SELECT DISTINCT corp_code FROM audit_fees WHERE bsns_year=:year
                ),
                aud AS (
                  SELECT DISTINCT corp_code FROM auditors
                  WHERE bsns_year=:year AND fs_div='CFS'
                ),
                disc AS (
                  SELECT DISTINCT corp_code FROM disclosures
                  WHERE disc_date >= :recent_start
                ),
                pol AS (
                  SELECT DISTINCT corp_code FROM accounting_policy_items
                  WHERE bsns_year=:year AND fs_div='CFS'
                )
                SELECT l.market,
                       COUNT(*) listed,
                       SUM(l.corp_code IN fin_any) financial_any_2025,
                       SUM(l.corp_code IN fin_cfs) financial_cfs_2025,
                       SUM(l.corp_code IN fin_ofs) financial_ofs_2025,
                       SUM(l.corp_code IN fee) audit_fee_2025,
                       SUM(l.corp_code IN aud) auditor_2025,
                       SUM(l.corp_code IN disc) disclosure_recent,
                       SUM(l.corp_code IN pol) policy_2025
                FROM listed l
                GROUP BY l.market
                ORDER BY l.market
                """
            ),
            {"year": year, "recent_start": f"{year}-01-01"},
        ).mappings().all()

        policy_corps = conn.execute(
            text("SELECT COUNT(DISTINCT corp_code) FROM accounting_policy_items")
        ).scalar() or 0
        auditor_2025_corps = conn.execute(
            text("SELECT COUNT(DISTINCT corp_code) FROM auditors WHERE bsns_year=:year"),
            {"year": year},
        ).scalar() or 0

    markets = {row["market"]: dict(row) for row in rows}
    return {
        "year": year,
        "markets": markets,
        "policy_corps": int(policy_corps),
        "auditor_2025_corps": int(auditor_2025_corps),
    }


def readiness_verdict(snapshot: dict) -> dict:
    required_gaps: list[str] = []
    recommended_gaps: list[str] = []

    for market in CORE_MARKETS:
        row = snapshot["markets"].get(market, {})
        listed = int(row.get("listed") or 0)
        if pct(int(row.get("financial_any_2025") or 0), listed) < 95.0:
            required_gaps.append("financial_any_2025")
        if pct(int(row.get("audit_fee_2025") or 0), listed) < 95.0:
            required_gaps.append("audit_fee_2025")
        if pct(int(row.get("disclosure_recent") or 0), listed) < 95.0:
            required_gaps.append("disclosure_recent")

    if int(snapshot.get("policy_corps") or 0) < 100:
        recommended_gaps.append("accounting_policy")
    if int(snapshot.get("auditor_2025_corps") or 0) < 1000:
        recommended_gaps.append("auditor_history")

    verdict = "pass"
    if required_gaps:
        verdict = "fail"
    elif recommended_gaps:
        verdict = "conditional_pass"
    return {
        "verdict": verdict,
        "required_gaps": sorted(set(required_gaps)),
        "recommended_gaps": sorted(set(recommended_gaps)),
    }
```

- [ ] **Step 4: Add CLI command**

Modify `kreports/cli/main.py`:

```python
@app.command("dataset-auditor-readiness")
def dataset_auditor_readiness_cmd(
    year: int = typer.Option(2025, "--year", help="기준 사업연도"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """감사인 peer/MCP 배포용 데이터셋 readiness를 점검한다."""
    from kreports.analysis.readiness import auditor_readiness_snapshot, readiness_verdict, pct

    snapshot = auditor_readiness_snapshot(year)
    verdict = readiness_verdict(snapshot)
    payload = {**snapshot, **verdict}
    if json_output:
        _json_print(payload)
        return

    typer.echo(f"Auditor dataset readiness: {verdict['verdict']}")
    for market, row in snapshot["markets"].items():
        listed = int(row["listed"] or 0)
        typer.echo(
            f"- {market}: financial(any) {row['financial_any_2025']}/{listed} "
            f"({pct(row['financial_any_2025'], listed)}%), "
            f"CFS {row['financial_cfs_2025']}/{listed} "
            f"({pct(row['financial_cfs_2025'], listed)}%), "
            f"audit_fee {row['audit_fee_2025']}/{listed} "
            f"({pct(row['audit_fee_2025'], listed)}%), "
            f"disclosure {row['disclosure_recent']}/{listed} "
            f"({pct(row['disclosure_recent'], listed)}%)"
        )
    typer.echo(f"required_gaps: {', '.join(verdict['required_gaps']) or '-'}")
    typer.echo(f"recommended_gaps: {', '.join(verdict['recommended_gaps']) or '-'}")
```

- [ ] **Step 5: Run tests and CLI**

Run:

```bash
.venv/bin/pytest tests/test_auditor_readiness.py -q
.venv/bin/kreports dataset-auditor-readiness
.venv/bin/kreports dataset-auditor-readiness --json
```

Expected:
- pytest passes.
- CLI prints `conditional_pass` for current KOSPI/KOSDAQ dataset.
- JSON output includes `required_gaps: []`.

- [ ] **Step 6: Commit**

```bash
git add kreports/analysis/readiness.py kreports/cli/main.py tests/test_auditor_readiness.py
git commit -m "feat: add auditor dataset readiness check"
```

---

### Task 3: Make Accounting Policy Cache-First And DART-Key-Free In MCP

**Files:**
- Modify: `kreports/analysis/queries.py`
- Modify: `kreports/analysis/api.py`
- Test: `tests/test_accounting_policy_cache.py`

- [ ] **Step 1: Write failing cache-first tests**

Add to `tests/test_accounting_policy_cache.py`:

```python
from datetime import datetime
from unittest.mock import patch

from kreports.analysis.api import get_accounting_policy
from kreports.db.models import AccountingPolicyItem, Company


def test_get_accounting_policy_reads_cache_without_dart_key(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI", induty_code="264"))
        session.add(AccountingPolicyItem(
            corp_code="00126380",
            bsns_year=2025,
            fs_div="CFS",
            rcept_no="20260301000001",
            item_key="revenue_recognition",
            heading="수익인식",
            body="고객과의 계약에서 생기는 수익은 수행의무 이행 시 인식한다.",
            body_hash="abc",
            body_length=35,
            fetched_at=datetime.utcnow(),
        ))

    with patch("kreports.analysis.queries.get_accounting_policy") as live_fetch:
        out = get_accounting_policy("005930", 2025, fs_div="CFS")
        live_fetch.assert_not_called()

    assert out["corp_code"] == "00126380"
    assert out["item_count"] == 1
    assert out["items"]["revenue_recognition"]["body"].startswith("고객과의 계약")


def test_get_accounting_policy_cache_miss_does_not_request_dart_key(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI", induty_code="264"))

    with patch("kreports.analysis.queries.get_accounting_policy") as live_fetch:
        out = get_accounting_policy("005930", 2025, fs_div="CFS")
        live_fetch.assert_not_called()

    assert out["item_count"] == 0
    assert "pre-built DB" in out["note"]
    assert "DART_API_KEY" not in out["note"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_accounting_policy_cache.py -q
```

Expected: FAIL because `get_accounting_policy` calls live DART-backed query.

- [ ] **Step 3: Add cached query**

Modify `kreports/analysis/queries.py`:

```python
def get_cached_accounting_policy(corp_code: str, bsns_year: int, fs_div: str = "CFS") -> dict | None:
    with get_session() as session:
        rows = (
            session.query(AccountingPolicyItem)
            .filter_by(corp_code=corp_code, bsns_year=bsns_year, fs_div=fs_div)
            .order_by(AccountingPolicyItem.item_key.asc())
            .all()
        )
    if not rows:
        return None
    return {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "fs_div": fs_div,
        "rcept_no": rows[0].rcept_no,
        "items": {
            row.item_key: {
                "heading": row.heading,
                "body": row.body,
                "body_length": row.body_length,
                "body_hash": row.body_hash,
            }
            for row in rows
        },
    }
```

- [ ] **Step 4: Change API to cache-first only**

Modify `kreports/analysis/api.py` `get_accounting_policy`:

```python
    data = _queries.get_cached_accounting_policy(corp_code, bsns_year, fs_div=fs_div)
    if data is None:
        from kreports.runtime import readonly_cache_miss

        return {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "items": {},
            "item_count": 0,
            "note": readonly_cache_miss("accounting_policy", corp_code, bsns_year),
        }
```

Keep the existing result shape for cached hits.

- [ ] **Step 5: Run tests and MCP smoke for policy**

Run:

```bash
.venv/bin/pytest tests/test_accounting_policy_cache.py tests/test_policy_persistence.py -q
env -u DART_API_KEY KREPORTS_RUNTIME_MODE=readonly .venv/bin/python - <<'PY'
import json
from kreports.mcp.tools import call_tool
out = json.loads(call_tool("get_accounting_policy", {"company": "005930", "bsns_year": 2025}))
assert "DART_API_KEY" not in json.dumps(out, ensure_ascii=False)
print(out.get("item_count"), out.get("note", "cached"))
PY
```

Expected:
- Tests pass.
- Python smoke prints item count or cache-miss note.
- No `DART_API_KEY` string in output.

- [ ] **Step 6: Commit**

```bash
git add kreports/analysis/queries.py kreports/analysis/api.py tests/test_accounting_policy_cache.py
git commit -m "fix: make accounting policy read-only cache first"
```

---

### Task 4: Add `fs_strategy=auto` To Peer Resolution

**Files:**
- Modify: `kreports/analysis/peer.py`
- Modify: `kreports/analysis/api.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_peer_selection.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_peer_selection.py`:

```python
from kreports.analysis.peer import resolve_fs_div_for_company


def test_resolve_fs_strategy_auto_prefers_cfs(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="A", induty_code="264"))
        session.add(Financial(corp_code="00000001", year=2025, quarter=4, fs_div="CFS", total_assets=100))

    assert resolve_fs_div_for_company("00000001", 2025, "auto") == "CFS"


def test_resolve_fs_strategy_auto_falls_back_to_ofs(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="A", induty_code="264"))
        session.add(Financial(corp_code="00000001", year=2025, quarter=4, fs_div="OFS", total_assets=100))

    assert resolve_fs_div_for_company("00000001", 2025, "auto") == "OFS"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_peer_selection.py -q
```

Expected: FAIL because `resolve_fs_div_for_company` does not exist.

- [ ] **Step 3: Implement FS strategy helper**

Add to `kreports/analysis/peer.py`:

```python
def resolve_fs_div_for_company(corp_code: str, year: int | None, fs_strategy: str = "auto") -> str:
    strategy = (fs_strategy or "auto").upper()
    if strategy in {"CFS", "OFS"}:
        return strategy
    if strategy != "AUTO":
        return "CFS"
    with engine.connect() as conn:
        if year is None:
            row = conn.execute(
                text("SELECT MAX(year) FROM financials WHERE corp_code=:cc AND quarter=4"),
                {"cc": corp_code},
            ).first()
            year = row[0] if row and row[0] else None
        if year is None:
            return "CFS"
        cfs = conn.execute(
            text("SELECT 1 FROM financials WHERE corp_code=:cc AND year=:y AND quarter=4 AND fs_div='CFS' LIMIT 1"),
            {"cc": corp_code, "y": year},
        ).first()
        if cfs:
            return "CFS"
        ofs = conn.execute(
            text("SELECT 1 FROM financials WHERE corp_code=:cc AND year=:y AND quarter=4 AND fs_div='OFS' LIMIT 1"),
            {"cc": corp_code, "y": year},
        ).first()
        return "OFS" if ofs else "CFS"
```

- [ ] **Step 4: Wire into compare APIs**

Modify `compare_to_industry_multi` signature in `kreports/analysis/api.py`:

```python
def compare_to_industry_multi(
    company: str,
    metrics: Optional[list[str]] = None,
    years_back: int = 5,
    fs_div: str = "CFS",
    fs_strategy: str = "CFS",
    ...
) -> dict:
```

Before calling `resolve_peers`, compute:

```python
from kreports.analysis.peer import resolve_fs_div_for_company

requested_fs_div = fs_div
if fs_strategy.lower() == "auto":
    fs_div = resolve_fs_div_for_company(corp_code, None, "auto")
```

Add to response:

```python
"fs_strategy": fs_strategy,
"requested_fs_div": requested_fs_div,
"fs_div_used": fs_div,
```

Modify MCP schema in `kreports/mcp/tools.py` for `compare_to_industry_multi`:

```python
"fs_strategy": {
    "type": "string",
    "enum": ["CFS", "OFS", "auto"],
    "default": "auto",
    "description": "auto면 CFS 우선, 없으면 OFS로 비교한다.",
},
```

And handler:

```python
fs_strategy=_optional_enum(args, "fs_strategy", {"CFS", "OFS", "auto"}, "auto"),
```

- [ ] **Step 5: Run tests and sample CLI smoke**

Run:

```bash
.venv/bin/pytest tests/test_peer_selection.py tests/test_compare_industry_multi.py -q
env -u DART_API_KEY KREPORTS_RUNTIME_MODE=readonly .venv/bin/python - <<'PY'
import json
from kreports.mcp.tools import call_tool
out = json.loads(call_tool("compare_to_industry_multi", {"company": "005930", "fs_strategy": "auto", "years_back": 2}))
assert out["fs_div_used"] in {"CFS", "OFS"}
assert out["n_peers"] >= 5
print(out["fs_div_used"], out["n_peers"], out["confidence"])
PY
```

Expected:
- Tests pass.
- Smoke prints selected fs_div, peer count, confidence.

- [ ] **Step 6: Commit**

```bash
git add kreports/analysis/peer.py kreports/analysis/api.py kreports/mcp/tools.py tests/test_peer_selection.py
git commit -m "feat: add automatic fs strategy for peer comparison"
```

---

### Task 5: Add Explicit `select_peer_group`

**Files:**
- Modify: `kreports/analysis/api.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_peer_selection.py`

- [ ] **Step 1: Write failing API and MCP tests**

Append to `tests/test_peer_selection.py`:

```python
import json

from kreports.analysis.api import select_peer_group
from kreports.mcp.tools import call_tool


def test_select_peer_group_returns_reason_codes_for_real_db():
    out = select_peer_group("005930", criteria=["industry", "size"], peer_limit=10)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["peer_count"] > 0
    first = out["peers"][0]
    assert "corp_code" in first
    assert "include_reasons" in first
    assert "same_ksic_prefix" in first["include_reasons"]
    assert "selection_policy" in out


def test_select_peer_group_mcp_dispatch():
    out = json.loads(call_tool("select_peer_group", {"company": "005930", "peer_limit": 5}))
    assert out["peer_count"] > 0
    assert out["_meta"]["tool"] == "select_peer_group"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_peer_selection.py -q
```

Expected: FAIL because `select_peer_group` is missing.

- [ ] **Step 3: Implement API function**

Add to `kreports/analysis/api.py`:

```python
def select_peer_group(
    company: str,
    criteria: Optional[list[str]] = None,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    prefix_len_start: int = 3,
    size_bucket_decade: Optional[float] = None,
    exclude_other_sectors: bool = True,
) -> dict:
    criteria = criteria or ["industry", "sector", "financial_data"]
    corp_code = resolve_corp_code(company)
    if corp_code is None:
        return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

    with _engine.connect() as conn:
        subject_row = conn.execute(
            text("SELECT corp_name, stock_code, market, induty_code FROM companies WHERE corp_code=:cc"),
            {"cc": corp_code},
        ).first()
    if subject_row is None:
        return {"error": f"corp_code '{corp_code}' 미등록"}

    from kreports.analysis.peer import resolve_fs_div_for_company, resolve_peers

    fs_div_used = resolve_fs_div_for_company(corp_code, None, fs_strategy)
    pr = resolve_peers(
        corp_code=corp_code,
        prefix_len_start=prefix_len_start,
        min_n=5,
        exclude_other_sectors=exclude_other_sectors,
        size_bucket_decade=size_bucket_decade,
        fs_div=fs_div_used,
    )

    peers: list[dict] = []
    if pr.peer_corp_codes:
        stmt = text(
            """
            SELECT c.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   f.total_assets, f.revenue,
                   af.audit_fee_m, af.audit_hours, af.nas_ratio
            FROM companies c
            LEFT JOIN financials f
              ON f.corp_code=c.corp_code
             AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
            LEFT JOIN audit_fees af
              ON af.corp_code=c.corp_code AND af.bsns_year=:year
            WHERE c.corp_code IN :ccs
            ORDER BY f.total_assets DESC NULLS LAST
            LIMIT :limit
            """
        ).bindparams(bindparam("ccs", expanding=True))
        with _engine.connect() as conn:
            rows = conn.execute(
                stmt,
                {
                    "ccs": pr.peer_corp_codes,
                    "year": pr.resolved_year,
                    "fs": fs_div_used,
                    "limit": peer_limit,
                },
            ).mappings().all()
        for row in rows:
            reasons = ["same_ksic_prefix", f"sector_group:{pr.sector_group.value}"]
            if size_bucket_decade is not None:
                reasons.append("asset_size_bucket")
            if row["audit_fee_m"] is not None:
                reasons.append("audit_fee_available")
            peers.append({**dict(row), "include_reasons": reasons})

    return {
        "subject": {
            "corp_code": corp_code,
            "stock_code": subject_row[1],
            "corp_name": subject_row[0],
            "market": subject_row[2],
            "induty_code": subject_row[3],
        },
        "selection_policy": {
            "criteria": criteria,
            "prefix_len_start": prefix_len_start,
            "matched_prefix_len": pr.matched_prefix_len,
            "exclude_other_sectors": exclude_other_sectors,
            "size_bucket_decade": size_bucket_decade,
            "fs_strategy": fs_strategy,
            "fs_div_used": fs_div_used,
            "resolved_year": pr.resolved_year,
        },
        "peer_count": pr.n_peers,
        "returned_peer_count": len(peers),
        "confidence": pr.confidence,
        "peers": peers,
        "excluded_categories": pr.excluded_categories,
        "note": pr.note,
    }
```

- [ ] **Step 4: Register MCP tool**

Modify imports and registry in `kreports/mcp/tools.py`:

```python
from kreports.analysis.api import select_peer_group
```

Add handler:

```python
def _handle_select_peer_group(args: dict) -> dict:
    company = _resolve_or_error(_require_string(args, "company"))
    criteria = args.get("criteria")
    if criteria is not None and not isinstance(criteria, list):
        return {"error": "criteria는 array여야 합니다."}
    return select_peer_group(
        company=company,
        criteria=criteria,
        peer_limit=_optional_int(args, "peer_limit", 30, min_value=1, max_value=200) or 30,
        fs_strategy=_optional_enum(args, "fs_strategy", {"CFS", "OFS", "auto"}, "auto"),
        prefix_len_start=_optional_int(args, "prefix_len_start", 3, min_value=2, max_value=5) or 3,
        size_bucket_decade=_optional_float_or_none(args, "size_bucket_decade", min_value=0.5, max_value=3.0),
        exclude_other_sectors=_optional_bool(args, "exclude_other_sectors", True),
    )
```

Add `TOOL_SELECT_PEER_GROUP` with this schema:

```python
TOOL_SELECT_PEER_GROUP = Tool(
    name="select_peer_group",
    description=(
        "감사인 관점 peer group 선정 근거팩. KSIC 업종, sector 분리, 자산규모 bucket, "
        "재무데이터/감사보수 coverage를 기준으로 peer 목록과 include_reasons를 반환한다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {"type": "string"},
            "criteria": {"type": "array", "items": {"type": "string"}},
            "peer_limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
            "fs_strategy": {"type": "string", "enum": ["CFS", "OFS", "auto"], "default": "auto"},
            "prefix_len_start": {"type": "integer", "default": 3, "minimum": 2, "maximum": 5},
            "size_bucket_decade": {"type": "number", "minimum": 0.5, "maximum": 3.0},
            "exclude_other_sectors": {"type": "boolean", "default": True},
        },
        "required": ["company"],
    },
)
```

Insert it into `ALL_TOOLS` before `TOOL_COMPARE_TO_INDUSTRY_MULTI` and add `"select_peer_group": _handle_select_peer_group` to `HANDLERS`.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv/bin/pytest tests/test_peer_selection.py tests/test_dart_mcp.py tests/test_mcp_tools_registration.py -q
```

Expected:
- Peer selection tests pass.
- Tool count tests fail if `EXPECTED_TOOL_COUNT` still equals 12.

- [ ] **Step 6: Update tool-count tests**

Modify `tests/test_dart_mcp.py`:

```python
EXPECTED_TOOL_COUNT = 13  # base tools plus auditor peer tools
```

Run:

```bash
.venv/bin/pytest tests/test_peer_selection.py tests/test_dart_mcp.py tests/test_mcp_tools_registration.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add kreports/analysis/api.py kreports/mcp/tools.py tests/test_peer_selection.py tests/test_dart_mcp.py
git commit -m "feat: add explicit auditor peer selection tool"
```

---

### Task 6: Add Auditor Peer Audit-Fee Benchmark

**Files:**
- Modify: `kreports/analysis/api.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_auditor_peer_tools.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_auditor_peer_tools.py`:

```python
import json

from kreports.analysis.api import compare_peer_audit_fees
from kreports.mcp.tools import call_tool


def test_compare_peer_audit_fees_real_db_shape():
    out = compare_peer_audit_fees("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["year"] == 2025
    assert out["peer_count"] > 0
    assert "audit_fee_m" in out["subject_metrics"]
    assert "audit_fee_to_assets_bps" in out["benchmarks"]


def test_compare_peer_audit_fees_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_audit_fees", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_audit_fees"
    assert out["peer_count"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_auditor_peer_tools.py -q
```

Expected: FAIL because `compare_peer_audit_fees` is missing.

- [ ] **Step 3: Implement API function**

Add to `kreports/analysis/api.py`:

```python
def _percentile(value: float | None, values: list[float]) -> float | None:
    if value is None or not values:
        return None
    below = sum(1 for v in values if v < value)
    return round(100.0 * below / len(values), 1)


def _metric_quantiles(values: list[float]) -> dict:
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    return {
        "n": n,
        "p25": round(_quantile(vals, 0.25), 2) if n >= 5 else None,
        "p50": round(_quantile(vals, 0.50), 2) if n else None,
        "p75": round(_quantile(vals, 0.75), 2) if n >= 5 else None,
    }


def compare_peer_audit_fees(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    size_bucket_decade: Optional[float] = None,
) -> dict:
    base = select_peer_group(
        company=company,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        size_bucket_decade=size_bucket_decade,
    )
    if "error" in base:
        return base
    corp_code = base["subject"]["corp_code"]
    fs_div = base["selection_policy"]["fs_div_used"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes

    stmt = text(
        """
        SELECT c.corp_code, c.corp_name, f.total_assets,
               af.audit_fee_m, af.audit_hours, af.non_audit_fee_m, af.nas_ratio,
               CASE WHEN f.total_assets > 0 AND af.audit_fee_m IS NOT NULL
                    THEN 10000.0 * af.audit_fee_m * 1000000.0 / f.total_assets END AS fee_assets_bps,
               CASE WHEN af.audit_hours > 0 AND af.audit_fee_m IS NOT NULL
                    THEN 1.0 * af.audit_fee_m / af.audit_hours END AS fee_per_hour_m
        FROM companies c
        LEFT JOIN financials f
          ON f.corp_code=c.corp_code AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
        LEFT JOIN audit_fees af
          ON af.corp_code=c.corp_code AND af.bsns_year=:year
        WHERE c.corp_code IN :ccs
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine.connect() as conn:
        rows = conn.execute(stmt, {"ccs": all_codes, "year": year, "fs": fs_div}).mappings().all()

    by_cc = {row["corp_code"]: dict(row) for row in rows}
    subject_row = by_cc.get(corp_code, {})
    peer_rows = [by_cc[cc] for cc in peer_codes if cc in by_cc]
    metrics = {
        "audit_fee_m": [r["audit_fee_m"] for r in peer_rows if r["audit_fee_m"] is not None],
        "audit_hours": [r["audit_hours"] for r in peer_rows if r["audit_hours"] is not None],
        "nas_ratio": [r["nas_ratio"] for r in peer_rows if r["nas_ratio"] is not None],
        "audit_fee_to_assets_bps": [r["fee_assets_bps"] for r in peer_rows if r["fee_assets_bps"] is not None],
        "audit_fee_per_hour_m": [r["fee_per_hour_m"] for r in peer_rows if r["fee_per_hour_m"] is not None],
    }
    benchmarks = {k: _metric_quantiles([float(v) for v in vals]) for k, vals in metrics.items()}
    for key, vals in metrics.items():
        subj_key = {
            "audit_fee_to_assets_bps": "fee_assets_bps",
            "audit_fee_per_hour_m": "fee_per_hour_m",
        }.get(key, key)
        subj_val = subject_row.get(subj_key)
        benchmarks[key]["subject_percentile"] = _percentile(float(subj_val) if subj_val is not None else None, [float(v) for v in vals])

    return {
        "subject": base["subject"],
        "year": year,
        "fs_div_used": fs_div,
        "peer_count": len(peer_rows),
        "subject_metrics": subject_row,
        "benchmarks": benchmarks,
        "peers": peer_rows[:peer_limit],
        "selection_policy": base["selection_policy"],
        "note": "DART audit fee contract/status data; audit judgment not performed.",
    }
```

- [ ] **Step 4: Register MCP tool**

In `kreports/mcp/tools.py`, import and register `compare_peer_audit_fees`.

Add handler:

```python
def _handle_compare_peer_audit_fees(args: dict) -> dict:
    company = _resolve_or_error(_require_string(args, "company"))
    return compare_peer_audit_fees(
        company=company,
        year=_optional_int(args, "year", 2025, min_value=2000, max_value=2100) or 2025,
        peer_limit=_optional_int(args, "peer_limit", 30, min_value=1, max_value=200) or 30,
        fs_strategy=_optional_enum(args, "fs_strategy", {"CFS", "OFS", "auto"}, "auto"),
        size_bucket_decade=_optional_float_or_none(args, "size_bucket_decade", min_value=0.5, max_value=3.0),
    )
```

Add `TOOL_COMPARE_PEER_AUDIT_FEES` and register it in `ALL_TOOLS` and `HANDLERS`.

- [ ] **Step 5: Update tool count and run tests**

Modify `tests/test_dart_mcp.py`:

```python
EXPECTED_TOOL_COUNT = 14  # base tools plus auditor peer tools
```

Run:

```bash
.venv/bin/pytest tests/test_auditor_peer_tools.py tests/test_dart_mcp.py tests/test_mcp_tools_registration.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/analysis/api.py kreports/mcp/tools.py tests/test_auditor_peer_tools.py tests/test_dart_mcp.py
git commit -m "feat: add auditor audit-fee peer benchmark"
```

---

### Task 7: Add Auditor Peer Risk Profile

**Files:**
- Modify: `kreports/analysis/api.py`
- Modify: `kreports/mcp/tools.py`
- Test: `tests/test_auditor_peer_tools.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_auditor_peer_tools.py`:

```python
from kreports.analysis.api import compare_peer_risk_profile


def test_compare_peer_risk_profile_shape():
    out = compare_peer_risk_profile("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "receivables_to_revenue" in out["benchmarks"]
    assert "disclosure_event_counts" in out


def test_compare_peer_risk_profile_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_risk_profile", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_risk_profile"
    assert out["peer_count"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_auditor_peer_tools.py -q
```

Expected: FAIL because `compare_peer_risk_profile` is missing.

- [ ] **Step 3: Implement minimal risk profile using available facts**

Add to `kreports/analysis/api.py`:

```python
_RISK_FACT_PATTERNS = {
    "receivables": ["매출채권", "수취채권"],
    "inventory": ["재고자산"],
    "intangible": ["무형자산", "개발비"],
    "borrowings": ["차입금", "사채"],
    "provisions": ["충당부채"],
}


def compare_peer_risk_profile(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict:
    base = select_peer_group(company=company, peer_limit=peer_limit, fs_strategy=fs_strategy)
    if "error" in base:
        return base
    corp_code = base["subject"]["corp_code"]
    fs_div = base["selection_policy"]["fs_div_used"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes

    stmt = text(
        """
        SELECT f.corp_code, c.corp_name, f.revenue, f.total_assets,
               f.operating_profit, f.net_income, f.operating_cf,
               f.accrual_ratio, f.beneish_m_score,
               f.op_cf_divergence_flag, f.going_concern_flag
        FROM financials f
        JOIN companies c ON c.corp_code=f.corp_code
        WHERE f.corp_code IN :ccs AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, {"ccs": all_codes, "year": year, "fs": fs_div}).mappings().all()]
        disc_rows = conn.execute(
            text(
                """
                SELECT corp_code,
                       SUM(report_nm LIKE '%정정%') restatement_like,
                       SUM(report_nm LIKE '%주요사항%') major_event_like,
                       COUNT(*) total_disclosures
                FROM disclosures
                WHERE corp_code IN :ccs
                  AND disc_date >= :start_date
                  AND disc_date <= :end_date
                GROUP BY corp_code
                """
            ).bindparams(bindparam("ccs", expanding=True)),
            {"ccs": all_codes, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31"},
        ).mappings().all()

    disc_by_cc = {r["corp_code"]: dict(r) for r in disc_rows}
    by_cc = {r["corp_code"]: r for r in rows}
    subject_row = by_cc.get(corp_code, {})
    peer_rows = [by_cc[cc] for cc in peer_codes if cc in by_cc]

    def ratio(row: dict, numerator: str, denominator: str) -> float | None:
        n = row.get(numerator)
        d = row.get(denominator)
        if n is None or not d:
            return None
        return 100.0 * float(n) / float(d)

    derived = {
        "op_cf_to_operating_profit": [
            ratio(r, "operating_cf", "operating_profit") for r in peer_rows
        ],
        "accrual_ratio": [r.get("accrual_ratio") for r in peer_rows],
        "beneish_m_score": [r.get("beneish_m_score") for r in peer_rows],
        "receivables_to_revenue": [],
        "inventory_to_revenue": [],
    }
    benchmarks = {
        k: _metric_quantiles([float(v) for v in vals if v is not None])
        for k, vals in derived.items()
    }
    return {
        "subject": base["subject"],
        "year": year,
        "fs_div_used": fs_div,
        "peer_count": len(peer_rows),
        "subject_metrics": subject_row,
        "benchmarks": benchmarks,
        "disclosure_event_counts": {
            "subject": disc_by_cc.get(corp_code, {}),
            "peers": {cc: disc_by_cc.get(cc, {}) for cc in peer_codes[:peer_limit]},
        },
        "selection_policy": base["selection_policy"],
        "note": "Risk profile is a DART-based signal pack, not audit risk assessment.",
    }
```

- [ ] **Step 4: Register MCP tool**

Add `compare_peer_risk_profile` import, handler, `TOOL_COMPARE_PEER_RISK_PROFILE`, and registry entries in `kreports/mcp/tools.py`.

Schema:

```python
{
    "type": "object",
    "properties": {
        "company": {"type": "string"},
        "year": {"type": "integer", "default": 2025, "minimum": 2000, "maximum": 2100},
        "peer_limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
        "fs_strategy": {"type": "string", "enum": ["CFS", "OFS", "auto"], "default": "auto"},
    },
    "required": ["company"],
}
```

- [ ] **Step 5: Update tool count and run tests**

Modify `tests/test_dart_mcp.py`:

```python
EXPECTED_TOOL_COUNT = 15  # base tools plus auditor peer tools
```

Run:

```bash
.venv/bin/pytest tests/test_auditor_peer_tools.py tests/test_dart_mcp.py tests/test_mcp_tools_registration.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/analysis/api.py kreports/mcp/tools.py tests/test_auditor_peer_tools.py tests/test_dart_mcp.py
git commit -m "feat: add auditor peer risk profile"
```

---

### Task 8: Add CLI MCP Smoke Test Without DART API Key

**Files:**
- Modify: `kreports/cli/main.py`
- Test: `tests/test_readonly_mcp.py`

- [ ] **Step 1: Write failing CLI smoke test**

Append to `tests/test_readonly_mcp.py`:

```python
import subprocess


def test_mcp_smoke_cli_works_without_dart_key():
    proc = subprocess.run(
        [".venv/bin/kreports", "mcp-smoke", "--company", "005930"],
        text=True,
        capture_output=True,
        env={"PATH": os.environ["PATH"], "KREPORTS_RUNTIME_MODE": "readonly"},
    )
    assert proc.returncode == 0
    assert "RESULT: OK" in proc.stdout
    assert "DART_API_KEY" not in proc.stdout
    assert "DART_API_KEY" not in proc.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_readonly_mcp.py::test_mcp_smoke_cli_works_without_dart_key -q
```

Expected: FAIL because `mcp-smoke` command is missing.

- [ ] **Step 3: Implement CLI command**

Add to `kreports/cli/main.py`:

```python
@app.command("mcp-smoke")
def mcp_smoke_cmd(
    company: str = typer.Option("005930", "--company", help="스모크 테스트 기준 회사"),
):
    """DART key 없이 read-only MCP 주요 도구를 호출한다."""
    import json
    from kreports.mcp.tools import call_tool

    calls = [
        ("search_company", {"query": company, "limit": 3}),
        ("get_financial_snapshot", {"company": company}),
        ("select_peer_group", {"company": company, "peer_limit": 5}),
        ("compare_to_industry_multi", {"company": company, "years_back": 2, "fs_strategy": "auto"}),
        ("compare_peer_audit_fees", {"company": company, "year": 2025}),
        ("compare_peer_risk_profile", {"company": company, "year": 2025}),
        ("get_accounting_policy", {"company": company, "bsns_year": 2025}),
    ]
    failures = []
    for name, args in calls:
        out = json.loads(call_tool(name, args))
        if "error" in out and "pre-built DB" not in str(out.get("error")):
            failures.append(f"{name}: {out['error']}")
        typer.echo(f"- {name}: {'FAIL' if name in failures else 'OK'}")
    if failures:
        typer.echo("RESULT: CHECK REQUIRED")
        for item in failures:
            typer.echo(item)
        raise typer.Exit(1)
    typer.echo("RESULT: OK")
```

- [ ] **Step 4: Run smoke tests**

Run:

```bash
env -u DART_API_KEY KREPORTS_RUNTIME_MODE=readonly .venv/bin/kreports mcp-smoke --company 005930
.venv/bin/pytest tests/test_readonly_mcp.py -q
```

Expected:
- CLI prints `RESULT: OK`.
- Tests pass.
- Output does not contain `DART_API_KEY`.

- [ ] **Step 5: Commit**

```bash
git add kreports/cli/main.py tests/test_readonly_mcp.py
git commit -m "feat: add read-only MCP smoke CLI"
```

---

### Task 9: Improve Policy Collection Targets For Peer Coverage

**Files:**
- Modify: `kreports/cli/main.py`
- Test: `tests/test_auditor_readiness.py`

- [ ] **Step 1: Write failing target-selection test**

Append to `tests/test_auditor_readiness.py`:

```python
from kreports.cli.main import _select_policy_targets


def test_select_policy_targets_by_market_and_limit_real_db():
    targets = _select_policy_targets(year=2025, fs_div="CFS", market="KOSPI", limit=10, missing_only=False)
    assert len(targets) == 10
    assert all(len(t[0]) == 8 and t[1] == 2025 and t[2] == "CFS" for t in targets)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_auditor_readiness.py::test_select_policy_targets_by_market_and_limit_real_db -q
```

Expected: FAIL because `_select_policy_targets` is missing.

- [ ] **Step 3: Implement target selector**

Add helper to `kreports/cli/main.py` near `collect-policies`:

```python
def _select_policy_targets(
    *,
    year: int,
    fs_div: str,
    market: str | None,
    limit: int | None,
    missing_only: bool,
) -> list[tuple[str, int, str]]:
    stmt = (
        "SELECT c.corp_code FROM companies c "
        "WHERE c.stock_code IS NOT NULL "
    )
    params: dict[str, object] = {}
    if market:
        stmt += "AND c.market = :market "
        params["market"] = market
    if missing_only:
        stmt += (
            "AND NOT EXISTS ("
            "SELECT 1 FROM accounting_policy_items p "
            "WHERE p.corp_code=c.corp_code AND p.bsns_year=:year AND p.fs_div=:fs_div"
            ") "
        )
        params["year"] = year
        params["fs_div"] = fs_div
    stmt += "ORDER BY c.market, c.corp_code "
    if limit:
        stmt += "LIMIT :limit"
        params["limit"] = limit
    with get_session() as session:
        rows = session.execute(text(stmt), params).all()
    return [(row[0], year, fs_div) for row in rows]
```

- [ ] **Step 4: Extend collect-policies CLI**

Modify `collect_policies_cmd` options:

```python
market: Optional[str] = typer.Option(None, "--market", help="KOSPI/KOSDAQ/KONEX 대상 일괄 수집"),
limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 회사 수"),
missing_only: bool = typer.Option(True, "--missing-only/--include-existing", help="이미 캐시된 정책 제외"),
```

When `market` is provided:

```python
if market:
    if year is None:
        typer.echo("--market 사용 시 --year 필요", err=True)
        raise typer.Exit(1)
    targets.extend(
        _select_policy_targets(
            year=year,
            fs_div=fs_div,
            market=market,
            limit=limit,
            missing_only=missing_only,
        )
    )
    typer.echo(f"정책 수집 대상: market={market} year={year} fs_div={fs_div} targets={len(targets)}")
```

- [ ] **Step 5: Run tests and dry CLI target**

Run:

```bash
.venv/bin/pytest tests/test_auditor_readiness.py -q
KREPORTS_RUNTIME_MODE=collector .venv/bin/kreports collect-policies --market KOSPI --year 2025 --limit 1
```

Expected:
- Tests pass.
- CLI processes one KOSPI company when `DART_API_KEY` is present on maintainer machine.
- If `DART_API_KEY` is missing, CLI exits with existing collector error and no read-only MCP behavior is affected.

- [ ] **Step 6: Commit**

```bash
git add kreports/cli/main.py tests/test_auditor_readiness.py
git commit -m "feat: add market policy collection targets"
```

---

### Task 10: Final CLI Verification Gate

**Files:**
- No code changes expected.

- [ ] **Step 1: Run dataset readiness**

Run:

```bash
.venv/bin/kreports dataset-auditor-readiness
.venv/bin/kreports dataset-auditor-readiness --json
```

Expected:
- Verdict is `conditional_pass` or `pass`.
- `required_gaps` is empty for KOSPI/KOSDAQ.
- `recommended_gaps` may include `accounting_policy` and `auditor_history`.

- [ ] **Step 2: Run read-only MCP smoke without DART key**

Run:

```bash
env -u DART_API_KEY KREPORTS_RUNTIME_MODE=readonly .venv/bin/kreports mcp-doctor
env -u DART_API_KEY KREPORTS_RUNTIME_MODE=readonly .venv/bin/kreports mcp-smoke --company 005930
```

Expected:
- `mcp-doctor` prints `RESULT: OK`.
- `mcp-smoke` prints `RESULT: OK`.
- No output asks the user to set `DART_API_KEY`.

- [ ] **Step 3: Run full focused test suite**

Run:

```bash
.venv/bin/pytest \
  tests/test_readonly_mcp.py \
  tests/test_auditor_readiness.py \
  tests/test_peer_selection.py \
  tests/test_accounting_policy_cache.py \
  tests/test_auditor_peer_tools.py \
  tests/test_compare_industry_multi.py \
  tests/test_audit_landscape.py \
  tests/test_dart_mcp.py \
  tests/test_mcp_tools_registration.py \
  -q
```

Expected: all selected tests pass, skipped tests are only environment-dependent HTTP tests already marked skip.

- [ ] **Step 4: Run DB integrity and secret scan**

Run:

```bash
sqlite3 kreports.db "PRAGMA integrity_check;"
rg -n "crtfc_key=[A-Za-z0-9]{20,}|DART_API_KEY=.*[A-Za-z0-9]{20,}|df786[c]" logs kreports tests scripts README.md docs 2>/dev/null || true
```

Expected:
- Integrity check prints `ok`.
- Secret scan prints nothing.

- [ ] **Step 5: Run MCP tool count sanity**

Run:

```bash
.venv/bin/python - <<'PY'
from kreports.mcp.tools import ALL_TOOLS
names = [t.name for t in ALL_TOOLS]
assert len(names) == len(set(names))
for required in [
    "select_peer_group",
    "compare_peer_audit_fees",
    "compare_peer_risk_profile",
    "compare_to_industry_multi",
    "get_accounting_policy",
]:
    assert required in names, required
print(len(names), names)
PY
```

Expected: prints a unique tool list containing all auditor peer tools.

- [ ] **Step 6: Commit verification docs if changed**

If verification output is captured in a doc or release note:

```bash
git add docs README.md
git commit -m "docs: document auditor peer read-only MCP readiness"
```

---

## Dataset Backfill Runbook

Run this only on the maintainer machine with `DART_API_KEY` configured. Do not put the key in command text or logs.

```bash
export KREPORTS_RUNTIME_MODE=collector

.venv/bin/kreports collect-all --year-from 2021 --year-to 2025
.venv/bin/kreports collect-disclosures --market KOSPI --start-date 20210101 --end-date 20260515
.venv/bin/kreports collect-disclosures --market KOSDAQ --start-date 20210101 --end-date 20260515
.venv/bin/kreports collect-auditors
.venv/bin/kreports collect-policies --market KOSPI --year 2025 --limit 100
.venv/bin/kreports collect-policies --market KOSDAQ --year 2025 --limit 100
.venv/bin/kreports dataset-auditor-readiness --year 2025 --years-back 5
```

Expected readiness target before external MCP deployment:
- KOSPI/KOSDAQ `financial_any` >= 95% for every required year in 2021~2025.
- KOSPI/KOSDAQ `business_report` >= 95% for every required year in 2021~2025.
- KOSPI/KOSDAQ `audit_report` >= 95% for every required year in 2021~2025.
- KOSPI/KOSDAQ `auditor` >= 95% for every required year in 2021~2025.
- KOSPI/KOSDAQ `disclosure_recent` >= 95%.
- Audit fee coverage is recommended for fee benchmarking, not a required readiness gate.
- Accounting policy remains recommended until broad cache coverage is intentionally expanded.

---

## Self-Review

Spec coverage:
- Dataset aggregation: Task 2 and final runbook.
- DART-key-free external MCP: Tasks 1, 3, 8, 10.
- Peer group selection: Tasks 4 and 5.
- Auditor-oriented peer comparison: Tasks 6 and 7.
- Accounting policy cache: Tasks 3 and 9.
- CLI testing: Tasks 2, 8, 10.

Placeholder scan:
- No task uses unspecified implementation hooks.
- All new functions have concrete signatures, target files, and test commands.

Type consistency:
- `fs_strategy` uses string values `auto`, `CFS`, `OFS`.
- `fs_div_used` is the resolved value used in SQL.
- MCP handlers call API functions with the same parameter names defined in each task.
