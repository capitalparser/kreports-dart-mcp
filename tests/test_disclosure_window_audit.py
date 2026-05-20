from datetime import date
from uuid import uuid4

from kreports.collector import disc_collector
from kreports.collector.disc_collector import audit_disclosure_window
from kreports.db.engine import get_session
from kreports.db.models import Disclosure


def test_audit_disclosure_window_detects_missing_and_filters(monkeypatch):
    calls = []
    rcept_base = uuid4().hex[:8]
    target_rcept_no = f"2099{rcept_base[:8]}01"[:14]

    def fake_fetch(corp_code, start_date, end_date, disc_type=""):
        calls.append((corp_code, start_date, end_date, disc_type))
        return [
            {
                "rcept_no": target_rcept_no,
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "rcept_dt": "20250331",
                "report_nm": "사업보고서 (2024.12)",
                "flr_nm": "삼성전자",
            },
            {
                "rcept_no": f"2099{rcept_base[:8]}02"[:14],
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "rcept_dt": "20250331",
                "report_nm": "분기보고서",
                "flr_nm": "삼성전자",
            },
            {
                "rcept_no": f"2099{rcept_base[:8]}03"[:14],
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "rcept_dt": "20250331",
                "report_nm": "사업보고서 제출기한연장",
                "flr_nm": "삼성전자",
            },
        ]

    monkeypatch.setattr(disc_collector, "fetch_disclosure_list", fake_fetch)

    out = audit_disclosure_window(
        start_date="20250301",
        end_date="20250331",
        disc_type="A",
        report_keyword="사업보고서",
        exclude_keywords=["제출기한연장"],
    )

    assert calls == [(None, "20250301", "20250331", "A")]
    assert out["target_rows"] == 1
    assert out["local_rows"] == 0
    assert out["missing_rows"] == 1
    assert out["coverage_pct"] == 0.0
    assert out["verdict"] == "fail"
    assert out["missing_samples"][0]["rcept_no"] == target_rcept_no


def test_audit_disclosure_window_can_persist_missing(monkeypatch):
    target_rcept_no = f"2099{uuid4().hex[:8]}04"[:14]

    def fake_fetch(corp_code, start_date, end_date, disc_type=""):
        return [
            {
                "rcept_no": target_rcept_no,
                "corp_code": "00126380",
                "corp_name": "삼성전자",
                "rcept_dt": "20250331",
                "report_nm": "사업보고서 (2024.12)",
                "flr_nm": "삼성전자",
            },
        ]

    monkeypatch.setattr(disc_collector, "fetch_disclosure_list", fake_fetch)

    out = audit_disclosure_window(
        start_date="20250301",
        end_date="20250331",
        report_keyword="사업보고서",
        persist_missing=True,
    )

    assert out["missing_rows"] == 1
    assert out["saved_missing"] == 1
    with get_session() as session:
        row = session.get(Disclosure, target_rcept_no)
        assert row is not None
        assert row.corp_code == "00126380"
        assert row.disc_date == date(2025, 3, 31)

    out_after = audit_disclosure_window(
        start_date="20250301",
        end_date="20250331",
        report_keyword="사업보고서",
    )
    assert out_after["missing_rows"] == 0
    assert out_after["local_rows"] == 1
    assert out_after["verdict"] == "pass"


def test_audit_disclosure_window_empty_target_is_not_pass(monkeypatch):
    monkeypatch.setattr(disc_collector, "fetch_disclosure_list", lambda *args, **kwargs: [])

    out = audit_disclosure_window(
        start_date="20250301",
        end_date="20250331",
        report_keyword="사업보고서",
    )

    assert out["target_rows"] == 0
    assert out["missing_rows"] == 0
    assert out["coverage_pct"] == 100.0
    assert out["verdict"] == "empty"


def test_audit_disclosure_window_errors_are_fail(monkeypatch):
    def fake_fetch(*args, **kwargs):
        raise RuntimeError("DART list.json status=020: usage exceeded")

    monkeypatch.setattr(disc_collector, "fetch_disclosure_list", fake_fetch)

    out = audit_disclosure_window(
        start_date="20250301",
        end_date="20250331",
        report_keyword="사업보고서",
    )

    assert out["target_rows"] == 0
    assert out["errors"]
    assert out["verdict"] == "fail"
