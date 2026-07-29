"""Explicit opt-in, immutable live MCP verification boundary."""
from __future__ import annotations

import asyncio
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


def _bound_immutable_live_database(path: Path):
    """Bind the explicit DB and always verify its digest during teardown."""
    from contextlib import contextmanager
    from kreports.release_artifact import _bound_explicit_runtime

    @contextmanager
    def bound():
        before = _sha256(path)
        try:
            with _bound_explicit_runtime(path):
                yield path, before
        finally:
            after = _sha256(path)
            print(json.dumps({"live_db_sha256_before": before, "live_db_sha256_after": after}))
            assert after == before, "live MCP verification changed the selected database"

    return bound()


@pytest.fixture
def immutable_live_database():
    """Bind handlers only to an explicitly selected immutable SQLite database."""
    path = _live_database_path()
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
    with _bound_immutable_live_database(path) as bound:
        yield bound


def test_immutable_live_digest_runs_when_runtime_binding_setup_fails(
    tmp_path, monkeypatch,
):
    """A failed bind must still execute the immutable-database hash teardown."""
    import kreports.release_artifact as release_artifact

    path = tmp_path / "immutable.db"
    path.write_bytes(b"immutable fixture")

    class FailingRuntime:
        def __enter__(self):
            raise RuntimeError("runtime setup failed")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        release_artifact,
        "_bound_explicit_runtime",
        lambda _path: FailingRuntime(),
    )
    with pytest.raises(RuntimeError, match="runtime setup failed"):
        with _bound_immutable_live_database(path):
            pytest.fail("body must not execute after setup failure")


@pytest.mark.live
def test_live_database_requires_explicit_absolute_regular_file():
    """Default test runs must never discover or open a repository database."""
    assert _live_database_path().is_file()


@pytest.mark.live
def test_samsung_fy2025_professional_public_result_matrix(immutable_live_database):
    """Record every live tool identically through legacy, envelope, and stdio."""
    from kreports.mcp.dispatch import dispatch_tool
    from kreports.mcp.resources import read_resource
    from kreports.mcp.server import handle_call_tool
    from kreports.mcp.tools import call_tool

    path, _before = immutable_live_database
    matrix = []
    outputs = {}
    for tool_name, arguments in LIVE_TOOLS:
        out = json.loads(call_tool(tool_name, arguments))
        envelope = dispatch_tool(tool_name, arguments).model_dump(mode="json")
        content, stdio = asyncio.run(handle_call_tool(tool_name, arguments))
        assert stdio == envelope
        assert content[0].text == envelope["answer"]
        assert out["data_quality"]["status"] == envelope["data_quality"]["status"]
        assert (
            out["data_quality"]["section_statuses"]
            == envelope["data_quality"]["section_statuses"]
        )
        for key in ("answer", "answer_pack", "domain_verdict"):
            assert out.get(key) == envelope.get(key)
        outputs[tool_name] = out
        quality = out.get("data_quality") or {}
        pack = out.get("answer_pack") or {}
        answer = str(out.get("answer") or "")
        row = {
            "tool": tool_name,
            "canonical_status": quality.get("status"),
            "domain_verdict": out.get("domain_verdict"),
            "fact_count": len(out.get("confirmed_facts") or []),
            "evidence_count": len(envelope.get("evidence") or []),
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

    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        procedure_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(audit_procedure_items)")
        }
    finally:
        connection.close()
    schema_gap = "kam_items" not in tables and "kam_item_id" not in procedure_columns
    print(json.dumps({"live_schema_gap": schema_gap}, ensure_ascii=False))
    affected = {
        "build_audit_acceptance_pack",
        "get_audit_report_sections",
        "get_kam_lifecycle",
        "compare_peer_kam_topics",
    }
    rendered = json.dumps([outputs[name] for name in affected], ensure_ascii=False)
    if schema_gap:
        for tool_name in affected:
            out = outputs[tool_name]
            assert out["data_quality"]["status"] == "error"
            assert out["answer_pack"]["tables"][0]["id"] == "availability"
        assert all(token not in rendered for token in (
            "no such table", "no such column", "kam_items", "kam_item_id",
            "audit_procedure_items", "OperationalError",
        ))
    else:
        assert all(outputs[name]["data_quality"]["status"] != "error" for name in affected)
        assert all(
            token not in rendered
            for token in (
                "no such table",
                "no such column",
                "OperationalError",
                "kam_items",
                "kam_item_id",
                "audit_procedure_items",
            )
        )
        for tool_name, output in outputs.items():
            pack = output.get("answer_pack") or {}
            resource_uri = pack.get("resource_uri")
            if not isinstance(resource_uri, str):
                continue
            resource = read_resource(resource_uri)
            resource_rendered = json.dumps(resource, ensure_ascii=False)
            assert output["data_quality"]["status"] in resource_rendered
            assert all(
                token not in resource_rendered
                for token in (
                    "no such table",
                    "no such column",
                    "OperationalError",
                    "kam_items",
                    "kam_item_id",
                    "audit_procedure_items",
                )
            )
            for source in pack.get("sources") or []:
                receipt = source.get("rcept_no") if isinstance(source, dict) else None
                if receipt:
                    assert str(receipt) in resource_rendered, tool_name
