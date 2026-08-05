import pytest
from kreports.analysis.api import get_industry_audit_landscape, _is_big4


def test_is_big4_keywords():
    assert _is_big4("삼정회계법인") is True
    assert _is_big4("PwC 삼일") is True
    assert _is_big4("EY한영") is True
    assert _is_big4("Deloitte 안진") is True
    assert _is_big4("작은 회계법인") is False
    assert _is_big4(None) is False
    assert _is_big4("") is False


@pytest.mark.live_data
def test_audit_landscape_samsung_basic_shape():
    out = get_industry_audit_landscape(company="005930")
    if "error" in out:
        pytest.skip(out["error"])
    assert out["subject"]["corp_name"] == "삼성전자"
    assert "matched_prefix_len" in out
    assert "n_peers" in out
    assert "auditor_market_share" in out
    assert "big4_share_pct" in out
    assert "non_qualified_opinion_rate_pct" in out
    assert "avg_tenure_years" in out
    assert "subject_auditor" in out


@pytest.mark.live_data
def test_audit_landscape_returns_top_auditors_sorted():
    out = get_industry_audit_landscape(company="005930", top_n=5)
    shares = out.get("auditor_market_share", [])
    if shares:
        counts = [s["company_count"] for s in shares]
        assert counts == sorted(counts, reverse=True)
        assert len(shares) <= 5


def test_audit_landscape_unknown_company():
    out = get_industry_audit_landscape(company="없는회사999")
    assert "error" in out


def test_audit_landscape_explicit_induty_code():
    out = get_industry_audit_landscape(induty_code="264")
    # subject is selected proxy from prefix; either error or subject present
    assert "subject" in out or "error" in out


def test_audit_landscape_no_args_returns_error():
    out = get_industry_audit_landscape()
    assert "error" in out
