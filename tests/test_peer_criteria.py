from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_legacy_arguments_normalize_to_explainable_profile():
    from kreports.analysis.peer_criteria import coerce_peer_criteria

    profile, requested, legacy = coerce_peer_criteria(
        ["industry", "size"],
        prefix_len_start=3,
        size_bucket_decade=1.0,
        exclude_other_sectors=True,
    )

    assert legacy is True
    assert requested == ["industry", "size"]
    assert profile.prefix_len == 3
    assert profile.fallback_prefix_len == 2
    assert profile.size_metric == "total_assets"
    assert profile.excluded_sector_groups == ["financial", "holding", "real_estate"]


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"size_log10_tolerance": 1.0}, "size_metric"),
        ({"industry_basis": "custom_codes"}, "included_corp_codes"),
        ({"included_corp_codes": ["00000001"], "excluded_corp_codes": ["00000001"]}, "겹칠 수 없습니다"),
        ({"weights": {"llm": 0.1}}, "가중치 차원"),
    ],
)
def test_profile_rejects_ambiguous_or_unbounded_input(payload, message):
    from kreports.analysis.peer_criteria import PeerCriteriaProfile

    with pytest.raises(ValidationError, match=message):
        PeerCriteriaProfile(**payload)


def test_profile_normalizes_and_bounds_customization():
    from kreports.analysis.peer_criteria import PeerCriteriaProfile

    profile = PeerCriteriaProfile(
        mode="ranked",
        excluded_sector_groups=["Financial", "financial"],
        included_corp_codes=["00000002"],
        required_business_tags=[" 반도체 ", "반도체"],
        weights={"industry": 0.5, "business": 0.4},
    )

    assert profile.excluded_sector_groups == ["financial"]
    assert profile.included_corp_codes == ["00000002"]
    assert profile.required_business_tags == ["반도체"]
    assert profile.weights == {"business": 0.4, "industry": 0.5}


def test_typed_custom_codes_profile_is_applied_and_explained(temp_engine):
    from kreports.analysis.peer_benchmarks import select_peer_group
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", stock_code="000002", corp_name="Included", induty_code="64110"),
            Company(corp_code="00000003", stock_code="000003", corp_name="Excluded", induty_code="26410"),
            Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", total_assets=100),
            Financial(corp_code="00000002", year=2024, quarter=4, fs_div="CFS", total_assets=100),
            Financial(corp_code="00000003", year=2024, quarter=4, fs_div="CFS", total_assets=100),
        ])

    out = select_peer_group(
        "00000001",
        criteria={
            "mode": "strict",
            "industry_basis": "ksic",
            "included_corp_codes": ["00000002"],
            "excluded_corp_codes": ["00000003"],
        },
        year=2024,
        _read_engine=temp_engine,
    )

    assert [peer["corp_code"] for peer in out["peers"]] == ["00000002"]
    policy = out["selection_policy"]
    assert policy["selection_mode"] == "strict"
    assert policy["criteria_applied"]["industry_basis"] == "ksic"
    assert policy["exclusion_reasons"]["00000003"] == ["excluded_by_user"]
    assert policy["coverage"]["by_peer"]["00000002"] == 1.0


def test_mcp_input_accepts_profile_alias_and_rejects_duplicate_profile():
    from kreports.mcp.input_models import SelectPeerGroupInput

    parsed = SelectPeerGroupInput(
        company="00000001",
        peer_criteria={"mode": "ranked", "weights": {"industry": 0.5}},
    )
    assert parsed.peer_criteria is not None
    assert parsed.peer_criteria.mode == "ranked"
    with pytest.raises(ValidationError, match="동시에"):
        SelectPeerGroupInput(
            company="00000001",
            criteria=["industry"],
            peer_criteria={"mode": "adaptive"},
        )


def test_strict_profile_does_not_use_ksic_fallback(temp_engine):
    from kreports.analysis.peer_benchmarks import select_peer_group
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Only two digit peer", induty_code="26510"),
            Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", total_assets=100),
            Financial(corp_code="00000002", year=2024, quarter=4, fs_div="CFS", total_assets=100),
        ])

    out = select_peer_group(
        "00000001",
        criteria={"mode": "strict", "prefix_len": 3},
        year=2024,
        _read_engine=temp_engine,
    )

    assert out["peer_count"] == 0
    assert out["selection_policy"]["fallback_used"] is False
