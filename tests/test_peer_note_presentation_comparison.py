from datetime import date

import pytest
import json
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from kreports.analysis.peer_benchmarks import compare_peer_accounting_policies
from kreports.db.models import AccountingPolicyItem, Company, Disclosure, Financial
from kreports.mcp.answer_pack import build_answer_pack
from kreports.mcp.dispatch import dispatch_tool, legacy_result, raw_result
from kreports.mcp.input_models import ComparePeerAccountingPoliciesInput


def _seed_peer_note_comparison(engine, *, peer_receipt="20250302000001"):
    session = sessionmaker(bind=engine)()
    session.add_all([
        Company(corp_code="00000001", stock_code="000001", corp_name="대상", market="KOSPI", induty_code="26111"),
        Company(corp_code="00000002", stock_code="000002", corp_name="알고리즘피어", market="KOSPI", induty_code="26112"),
        Company(corp_code="00000003", stock_code="000003", corp_name="직접피어", market="KOSDAQ", induty_code="70100"),
    ])
    session.add_all([
        Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", revenue=1000, operating_profit=100, total_assets=2000, total_debt=300, total_equity=1700, revenue_yoy=0.1, source="fixture"),
        Financial(corp_code="00000002", year=2024, quarter=4, fs_div="CFS", revenue=950, operating_profit=80, total_assets=1900, total_debt=400, total_equity=1500, revenue_yoy=0.08, source="fixture"),
        Financial(corp_code="00000003", year=2024, quarter=4, fs_div="CFS", revenue=None, operating_profit=None, total_assets=None, total_debt=None, total_equity=None, revenue_yoy=None, source="fixture"),
    ])
    session.add_all([
        Disclosure(rcept_no="20250301000001", corp_code="00000001", corp_name="대상", disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)"),
        Disclosure(rcept_no="20250302000001", corp_code="00000002", corp_name="알고리즘피어", disc_date=date(2025, 3, 2), disc_type="A", report_nm="사업보고서 (2024.12)"),
        Disclosure(rcept_no="20250303000001", corp_code="00000003", corp_name="직접피어", disc_date=date(2025, 3, 3), disc_type="A", report_nm="사업보고서 (2024.12)"),
        AccountingPolicyItem(corp_code="00000001", bsns_year=2024, fs_div="CFS", rcept_no="20250301000001", item_key="revenue_recognition", heading="2. 수익인식", body="대상회사는 통제 이전 시점에 수익을 인식합니다." * 30, body_hash="subject", body_length=900),
        AccountingPolicyItem(corp_code="00000002", bsns_year=2024, fs_div="CFS", rcept_no=peer_receipt, item_key="revenue_recognition", heading="3. 수익", body="피어는 수행의무 이행 시점에 수익을 인식합니다.", body_hash="peer", body_length=24),
        AccountingPolicyItem(corp_code="00000003", bsns_year=2024, fs_div="CFS", rcept_no="20250303000001", item_key="inventory", heading="4. 재고", body="재고자산 정책", body_hash="direct", body_length=8),
    ])
    session.commit()
    session.close()


def test_peer_note_comparison_returns_presentations_selection_and_only_proven_links(temp_engine):
    """Would fail if the legacy coverage-only response omits note presentation evidence."""
    _seed_peer_note_comparison(temp_engine)

    out = compare_peer_accounting_policies(
        "00000001", year=2024, peer_limit=2, item_key="revenue_recognition",
        selection_profile="investor", include_peers=["00000003"],
        peer_weights={"size": 0.7, "profitability": 0.3},
    )

    assert out["selected_topic"] == {"item_key": "revenue_recognition", "keyword": None}
    assert [row["corp_code"] for row in out["note_presentations"]] == ["00000001", "00000003", "00000002"]
    subject = out["note_presentations"][0]
    assert len(subject["body_excerpt"]) <= 400
    assert subject["provenance_status"] == "proven_annual_filing"
    assert subject["source_url"].endswith("20250301000001")
    direct = next(row for row in out["peer_selection"] if row["corp_code"] == "00000003")
    assert direct["selection_status"] == "included"
    assert direct["selection_reason"] == "direct_include_override"
    assert direct["score_components"]["size"] is None
    assert "missing_financial_dimensions:size" in direct["limitations"]
    assert out["methodology"]["selection_profile"] == "investor"
    assert "accounting treatment conclusion" in out["methodology"]["comparison_limitations"]


def test_peer_note_comparison_does_not_normalize_contaminated_or_missing_topic_to_absence(temp_engine):
    """A synthetic receipt must never produce a DART link or a filing-absence claim."""
    _seed_peer_note_comparison(temp_engine, peer_receipt="synthetic-20250302000001-attachment")

    out = compare_peer_accounting_policies(
        "00000001", year=2024, item_key="revenue_recognition", peer_limit=2,
        include_peers=["00000003"],
    )

    peer = next(row for row in out["note_presentations"] if row["corp_code"] == "00000002")
    assert peer["provenance_status"] == "invalid_receipt"
    assert peer.get("source_url") is None
    missing = next(row for row in out["topic_coverage"] if row["corp_code"] == "00000003")
    assert missing["status"] == "cache_missing_not_filing_absence"
    assert "not disclosed" not in out["coverage_note"].lower()


def test_peer_note_comparison_dispatch_and_answer_pack_show_dedicated_tables(temp_engine):
    """The typed MCP boundary must carry the real comparison to the visual answer pack."""
    _seed_peer_note_comparison(temp_engine)

    envelope = raw_result("compare_peer_accounting_policies", {
        "company": "00000001", "year": 2024, "item_key": "revenue_recognition",
        "include_peers": ["00000003"], "peer_limit": 2,
    })
    pack = build_answer_pack("compare_peer_accounting_policies", envelope)
    public = legacy_result("compare_peer_accounting_policies", {
        "company": "00000001", "year": 2024, "item_key": "revenue_recognition",
        "include_peers": ["00000003"], "peer_limit": 2,
    })

    assert envelope["selected_topic"]["item_key"] == "revenue_recognition"
    assert {table["id"] for table in pack["tables"]} >= {
        "peer_policy_methodology", "peer_policy_selection", "peer_note_presentations",
        "peer_policy_topic_coverage",
    }
    assert {table["id"] for table in public["answer_pack"]["tables"]} >= {
        "peer_policy_selection", "peer_note_presentations", "peer_policy_topic_coverage",
    }
    assert [source["rcept_no"] for source in pack["sources"]] == [
        "20250301000001", "20250302000001",
    ]


def test_peer_policy_input_rejects_conflicting_overrides_and_unknown_weight():
    """Input validation must fail closed before any peer resolution."""
    with pytest.raises(ValidationError):
        ComparePeerAccountingPoliciesInput(
            company="000001", include_peers=["000002"], exclude_peers=["000002"],
        )
    with pytest.raises(ValidationError):
        ComparePeerAccountingPoliciesInput(
            company="000001", peer_weights={"unsupported": 1.0},
        )


@pytest.mark.parametrize("receipt", ["20250202000001", "20250301000001"])
def test_peer_note_comparison_rejects_older_or_foreign_but_canonical_receipt(temp_engine, receipt):
    """A 14-digit value is not evidence unless it is this peer's latest annual filing."""
    _seed_peer_note_comparison(temp_engine, peer_receipt=receipt)

    out = compare_peer_accounting_policies(
        "00000001", year=2024, item_key="revenue_recognition", peer_limit=1,
    )

    peer = next(row for row in out["note_presentations"] if row["corp_code"] == "00000002")
    assert peer["provenance_status"] == "unproven_annual_filing"
    assert peer.get("rcept_no") is None
    assert peer.get("source_url") is None


def test_peer_note_comparison_fails_closed_for_subject_topic_cache_miss(temp_engine):
    """No selected subject topic is a cache limitation, never a disclosure-absence result."""
    _seed_peer_note_comparison(temp_engine)

    out = compare_peer_accounting_policies(
        "00000001", year=2024, item_key="lease_policy", peer_limit=1,
    )

    assert out["data_quality"]["status"] == "limited"
    assert "subject_topic_cache_missing_not_filing_absence" in out["data_quality"]["limitations"]
    assert out["note_presentations"][0]["provenance_status"] == "cache_missing_not_filing_absence"


def test_peer_note_comparison_exposes_candidate_and_final_selection_before_notes(temp_engine):
    """Selection criteria and contribution math must be inspectable independently of excerpts."""
    _seed_peer_note_comparison(temp_engine)

    out = compare_peer_accounting_policies(
        "00000001", year=2024, item_key="revenue_recognition", peer_limit=1,
        exclude_peers=["00000002"], include_peers=["00000003"],
    )

    assert out["selection_policy"]["preselection_criteria"]["candidate_universe"]
    assert out["candidate_universe"]["candidate_count"] >= 1
    selected = next(row for row in out["peer_selection"] if row["corp_code"] == "00000003")
    excluded = next(row for row in out["peer_selection"] if row["corp_code"] == "00000002")
    assert selected["selection_reason"] == "direct_include_override"
    assert excluded["selection_reason"] == "user_exclude_override"
    assert set(selected["component_contributions"]) == {"size", "leverage", "profitability", "growth"}


def test_default_peer_policy_pack_has_legacy_rows_but_not_empty_side_by_side_claim(temp_engine):
    """The legacy raw result still needs an informative, non-empty chatbot pack."""
    _seed_peer_note_comparison(temp_engine)

    result = raw_result("compare_peer_accounting_policies", {
        "company": "00000001", "year": 2024, "peer_limit": 1,
    })
    tables = {table["id"]: table for table in build_answer_pack(
        "compare_peer_accounting_policies", result,
    )["tables"]}

    assert tables["peer_policy_methodology"]["rows"]
    assert tables["peer_policy_selection"]["rows"]
    assert tables["peer_note_presentations"]["rows"]
    assert tables["peer_note_presentations"]["title"] == "대상회사 회계정책 캐시"


def test_dispatch_envelope_carries_extended_peer_policy_answer_pack(temp_engine):
    _seed_peer_note_comparison(temp_engine)

    envelope = dispatch_tool("compare_peer_accounting_policies", {
        "company": "00000001", "year": 2024, "item_key": "revenue_recognition",
        "peer_limit": 1,
    })

    assert envelope.tool_name == "compare_peer_accounting_policies"
    assert {table["id"] for table in envelope.answer_pack["tables"]} >= {
        "peer_policy_methodology", "peer_policy_selection", "peer_note_presentations",
    }


def test_unproven_or_missing_final_peer_topic_makes_selected_comparison_limited(temp_engine):
    """A proven subject row cannot conceal a final peer's unproven/missing topic."""
    _seed_peer_note_comparison(
        temp_engine, peer_receipt="synthetic-20250302000001-attachment",
    )

    out = compare_peer_accounting_policies(
        "00000001", year=2024, item_key="revenue_recognition", peer_limit=1,
    )

    assert out["data_quality"]["status"] == "limited"
    assert out["data_quality"]["peer_topic_quality"] == {
        "final_peer_count": 1,
        "cache_missing_count": 0,
        "unproven_receipt_count": 1,
        "proven_count": 0,
    }
    assert "peer_topic_receipt_not_proven" in out["data_quality"]["limitations"]


def test_custom_weights_are_complete_effective_map_and_include_limit_fails_closed(temp_engine):
    """A custom map replaces profile weights; more direct peers than slots is invalid."""
    _seed_peer_note_comparison(temp_engine)

    out = compare_peer_accounting_policies(
        "00000001", year=2024, item_key="revenue_recognition", peer_limit=1,
        peer_weights={"size": 0.7, "profitability": 0.3},
    )

    assert out["selection_policy"]["weights"] == {
        "size": 0.7, "leverage": 0.0, "profitability": 0.3, "growth": 0.0,
    }
    with pytest.raises(ValueError, match="include_peers"):
        compare_peer_accounting_policies(
            "00000001", year=2024, peer_limit=1,
            include_peers=["00000002", "00000003"],
        )


def test_custom_selection_without_topic_is_bounded_and_keyword_results_are_truncated(temp_engine):
    """No selector never leaks every policy body, and broad keywords have explicit caps."""
    _seed_peer_note_comparison(temp_engine)
    session = sessionmaker(bind=temp_engine)()
    for number in range(10):
        session.add(AccountingPolicyItem(
            corp_code="00000001", bsns_year=2024, fs_div="CFS",
            rcept_no="20250301000001", item_key=f"extra_{number}",
            heading=f"공통 정책 {number}", body="공통 키워드 " * 100,
            body_hash=f"extra-{number}", body_length=600,
        ))
    session.commit()
    session.close()

    no_topic = compare_peer_accounting_policies(
        "00000001", year=2024, peer_limit=1, selection_profile="auditor",
    )
    broad = compare_peer_accounting_policies(
        "00000001", year=2024, peer_limit=1, keyword="공통",
    )

    assert no_topic["note_presentations"] == []
    assert "topic_selector_required" in no_topic["data_quality"]["limitations"]
    assert broad["presentation_truncation"]["truncated"] is True
    assert len(broad["note_presentations"]) <= 5
    assert all(len(row.get("body_excerpt") or "") <= 400 for row in broad["note_presentations"])


def test_legacy_duplicate_rows_conflict_but_identical_rows_dedupe_without_scoring_guess():
    """Legacy duplicate input is resolved before DB access; conflicting values are unusable."""
    from kreports.analysis.peer_benchmarks import _resolve_policy_financial_rows

    base = {
        "id": 2, "corp_code": "peer", "revenue": 100, "total_assets": 200,
        "total_debt": 50, "total_equity": 150, "operating_profit": 10,
        "revenue_yoy": 0.1,
    }
    identical, identical_conflicts, _ = _resolve_policy_financial_rows([base, {**base, "id": 1}])
    conflicting, conflicts, _ = _resolve_policy_financial_rows([base, {**base, "id": 1, "revenue": 1}])

    assert identical["peer"]["revenue"] == 100
    assert identical_conflicts == set()
    assert conflicting == {}
    assert conflicts == {"peer"}


def test_no_financial_score_uses_industry_sector_fallback_and_nonfinite_is_not_public(temp_engine):
    """Unavailable scoring data must not be labelled financial similarity or serialize Infinity."""
    _seed_peer_note_comparison(temp_engine)
    session = sessionmaker(bind=temp_engine)()
    peer = session.query(Financial).filter_by(corp_code="00000002", year=2024).one()
    peer.revenue = None
    peer.total_assets = None
    peer.total_debt = None
    peer.total_equity = None
    peer.operating_profit = None
    peer.revenue_yoy = float("inf")
    session.commit()
    session.close()

    out = compare_peer_accounting_policies(
        "00000001", year=2024, item_key="revenue_recognition", peer_limit=1,
    )
    peer_row = next(row for row in out["peer_selection"] if row["corp_code"] == "00000002")

    assert peer_row["algorithmic_score"] is None
    assert peer_row["selection_reason"] == "industry_sector_fallback_no_financial_score"
    assert "nonfinite_financial_values_unavailable" in peer_row["limitations"]
    assert "Infinity" not in json.dumps(out, allow_nan=False)


@pytest.mark.parametrize("selector", ["", " " * 101])
def test_peer_selector_bounds_and_finite_weight_validation(selector):
    with pytest.raises(ValidationError):
        ComparePeerAccountingPoliciesInput(company="000001", include_peers=[selector])
    with pytest.raises(ValidationError):
        ComparePeerAccountingPoliciesInput(company="000001", peer_weights={"size": float("nan")})
