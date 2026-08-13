import importlib.util
from pathlib import Path


def _load_evaluator():
    path = Path("scripts/evaluate_current_mcp_quality.py")
    spec = importlib.util.spec_from_file_location("evaluate_current_mcp_quality", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_quality_row_reports_accounting_note_chapter_coverage(monkeypatch):
    evaluator = _load_evaluator()

    def fake_call(name, args):
        if name == "get_financial_snapshot":
            return {"rows": [{"year": 2025}]}
        if name == "select_peer_group":
            return {"peer_count": 3}
        if name == "compare_peer_audit_fees":
            return {"peer_count": 3}
        if name == "compare_peer_accounting_policies":
            return {"data_quality": {"status": "usable", "peer_coverage_pct": 80}, "subject_policy_count": 4}
        if name == "get_audit_report_sections":
            return {"data_quality": {"status": "usable"}, "section_count": 2}
        if name == "get_business_overview":
            return {"data_quality": {"status": "usable"}, "section_count": 4}
        if name == "build_audit_acceptance_pack":
            return {"data_quality": {"policy_cache": {"status": "usable"}, "kam_body": {"status": "usable"}}}
        if name == "search_dataset":
            return {
                "data_quality": {"status": "usable"},
                "total_records": 2,
                "companies": [{"records": [{"section_type": "policy"}, {"section_type": "estimate_judgment"}]}],
            }
        raise AssertionError(name)

    monkeypatch.setattr(evaluator, "_call", fake_call)

    row = evaluator._row("005930", 2025)

    assert row["note_chapter_status"] == "usable"
    assert row["note_chapter_records"] == 2
