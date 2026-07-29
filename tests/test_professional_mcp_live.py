"""Explicit opt-in, immutable live MCP verification boundary."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

LIVE_TOOLS = (
    ("prepare_standard_audit_hours_inputs", {"company": "005930", "year": 2025}),
    ("compare_peer_audit_fees", {"company": "005930", "year": 2025}),
    ("build_audit_acceptance_pack", {"company": "005930", "year": 2025}),
    ("compare_peer_risk_profile", {"company": "005930", "year": 2025}),
    ("get_audit_history", {"company": "005930"}),
    ("get_audit_report_sections", {"company": "005930", "year": 2025}),
    ("search_audit_report_matters", {"company": "005930", "year": 2025}),
    ("compare_peer_audit_report_matters", {"company": "005930", "year": 2025}),
    ("get_kam_lifecycle", {"company": "005930", "start_year": 2021, "end_year": 2025}),
    ("compare_peer_kam_topics", {"company": "005930", "year": 2025}),
    ("get_financial_snapshot", {"company": "005930", "years": 5}),
    ("compare_to_industry_multi", {"company": "005930", "years_back": 5}),
    ("get_investor_signals", {"company": "005930", "years": 5}),
    ("search_disclosure_events", {"company": "005930"}),
    ("get_quality_of_earnings_pack", {"company": "005930", "start_year": 2021, "end_year": 2025}),
    ("get_dcf_input_candidates", {"company": "005930", "start_year": 2021, "end_year": 2025}),
    ("build_dcf_model_pack", {"company": "005930", "base_year": 2025}),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _live_database_path() -> Path:
    raw = os.environ.get("KREPORTS_LIVE_DB")
    if not raw:
        pytest.skip("KREPORTS_LIVE_DB is not explicitly set")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        pytest.skip(
            "KREPORTS_LIVE_DB must be an absolute existing regular non-symlink file"
        )
    return path


@pytest.fixture
def immutable_live_database():
    """Bind handlers only to an explicitly selected immutable SQLite database."""
    from kreports.release_artifact import _bound_explicit_runtime

    path = _live_database_path()
    before = _sha256(path)
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        check_same_thread=False,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
    finally:
        connection.close()
    with _bound_explicit_runtime(path):
        yield path, before
    after = _sha256(path)
    print(json.dumps({"live_db_sha256_before": before, "live_db_sha256_after": after}))
    assert after == before, "live MCP verification changed the selected database"


@pytest.mark.live
def test_live_database_requires_explicit_absolute_regular_file():
    """Default test runs must never discover or open a repository database."""
    assert _live_database_path().is_file()


@pytest.mark.live
def test_samsung_fy2025_professional_public_result_matrix(immutable_live_database):
    """Record the complete Samsung matrix through the compatibility public path."""
    from kreports.mcp.tools import call_tool

    matrix = []
    outputs = {}
    for tool_name, arguments in LIVE_TOOLS:
        out = json.loads(call_tool(tool_name, arguments))
        outputs[tool_name] = out
        quality = out.get("data_quality") or {}
        pack = out.get("answer_pack") or {}
        answer = str(out.get("answer") or "")
        row = {
            "tool": tool_name,
            "canonical_status": quality.get("status"),
            "domain_verdict": out.get("domain_verdict"),
            "fact_count": len(out.get("confirmed_facts") or []),
            "evidence_count": len(out.get("evidence") or []),
            "pack_status": (pack.get("data_quality") or {}).get("status"),
            "table_ids": [table.get("id") for table in pack.get("tables") or []],
            "source_count": len(pack.get("sources") or []),
            "first_answer_paragraph": answer.split("\n\n", 1)[0],
        }
        if row["canonical_status"] is None:
            row["error"] = out.get("error")
        matrix.append(row)

    print(json.dumps({"samsung_005930_fy2025_matrix": matrix}, ensure_ascii=False))
    by_tool = {row["tool"]: row for row in matrix}
    assert all(
        row["canonical_status"] in {"usable", "limited", "missing", "error"}
        for row in matrix
    )
    assert all(str(row["first_answer_paragraph"]).startswith("판정:") for row in matrix)
    assert all(
        not row["pack_status"] or row["pack_status"] == row["canonical_status"]
        for row in matrix
    )
    assert "dcf_valuation_bridge" not in by_tool["build_dcf_model_pack"]["table_ids"]
    assert by_tool["build_dcf_model_pack"]["domain_verdict"] in {
        "calculation_unavailable", None,
    }
    prepared_rows = outputs["prepare_standard_audit_hours_inputs"].get("rows") or []
    year_2023 = next(row for row in prepared_rows if row.get("year") == 2023)
    assert year_2023.get("audit_fee_m") is None
    assert year_2023.get("audit_hours") is None
    assert outputs["prepare_standard_audit_hours_inputs"].get("domain_verdict") == "not_assessed"
    history = outputs["get_audit_history"].get("history") or []
    assert any(
        row.get("auditor_nm") and row.get("audit_opinion") and row.get("rcept_no")
        for row in history
    )
    financial_table = next(
        table for table in outputs["get_financial_snapshot"]["answer_pack"]["tables"]
        if table["id"] == "financial_trend"
    )
    peer_table = next(
        table for table in outputs["compare_to_industry_multi"]["answer_pack"]["tables"]
        if table["id"] == "industry_metrics"
    )
    assert len(financial_table["rows"]) == 5
    assert len(peer_table["rows"]) >= 5
    investor_checks = (
        outputs["get_investor_signals"].get("quality_snapshot") or {}
    ).get("checks") or {}
    assert any(check.get("status") == "unknown" for check in investor_checks.values())
    dcf_readiness = next(
        table for table in outputs["build_dcf_model_pack"]["answer_pack"]["tables"]
        if table["id"] == "dcf_model_readiness"
    )
    assert any(row.get("status") == "blocked" for row in dcf_readiness["rows"])
