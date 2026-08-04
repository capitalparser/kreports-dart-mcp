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
    assert profile.excluded_sector_groups == []


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
    industry = out["peers"][0]["reason_components"]["industry_match"]
    assert industry == {
        "matched": False,
        "basis": "explicit_override",
        "requested_basis": "ksic",
        "override": True,
        "matched_prefix_len": 3,
        "subject_induty_code": "26410",
        "peer_induty_code": "64110",
    }


def test_batched_candidate_lookup_keeps_null_industry_but_rejects_missing_company(temp_engine):
    from kreports.analysis.peer_benchmarks import select_peer_group
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001", stock_code="000001",
                corp_name="Subject", induty_code="26410",
            ),
            Company(
                corp_code="00000002", stock_code="000002",
                corp_name="Known peer", induty_code="26420",
            ),
            Company(
                corp_code="00000003", stock_code="000003",
                corp_name="Unknown-sector peer", induty_code=None,
            ),
            Financial(
                corp_code="00000001", year=2024, quarter=4,
                fs_div="CFS", total_assets=100,
            ),
            Financial(
                corp_code="00000002", year=2024, quarter=4,
                fs_div="CFS", total_assets=100,
            ),
            Financial(
                corp_code="00000003", year=2024, quarter=4,
                fs_div="CFS", total_assets=100,
            ),
        ])

    out = select_peer_group(
        "00000001",
        criteria={
            "included_corp_codes": ["00000003", "99999999"],
        },
        year=2024,
        _read_engine=temp_engine,
    )

    peers = {peer["corp_code"]: peer for peer in out["peers"]}
    assert peers["00000003"]["reason_components"]["sector_match"] == {
        "matched": False,
        "basis": "not_required",
    }
    assert out["selection_policy"]["exclusion_reasons"]["99999999"] == [
        "company_not_found"
    ]


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


def test_revenue_size_metric_excludes_outside_tolerance(temp_engine):
    from kreports.analysis.peer_benchmarks import select_peer_group
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Comparable", induty_code="26420"),
            Company(corp_code="00000003", corp_name="Revenue outlier", induty_code="26430"),
            Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", total_assets=100, revenue=100),
            Financial(corp_code="00000002", year=2024, quarter=4, fs_div="CFS", total_assets=100, revenue=500),
            Financial(corp_code="00000003", year=2024, quarter=4, fs_div="CFS", total_assets=100, revenue=100_000),
        ])

    out = select_peer_group(
        "00000001",
        criteria={
            "size_metric": "revenue",
            "size_log10_tolerance": 1.0,
        },
        year=2024,
        _read_engine=temp_engine,
    )

    assert [peer["corp_code"] for peer in out["peers"]] == ["00000002"]
    assert out["selection_policy"]["exclusion_reasons"]["00000003"] == [
        "size_metric_outside_tolerance:revenue"
    ]


def test_typed_sector_exclusion_is_explicit_not_implicit(temp_engine):
    from kreports.analysis.peer_benchmarks import select_peer_group
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="64201"),
            Company(corp_code="00000002", corp_name="Financial", induty_code="64202"),
            Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", total_assets=100),
            Financial(corp_code="00000002", year=2024, quarter=4, fs_div="CFS", total_assets=100),
        ])

    allowed = select_peer_group(
        "00000001",
        criteria={"prefix_len": 3},
        year=2024,
        _read_engine=temp_engine,
    )
    excluded = select_peer_group(
        "00000001",
        criteria={"prefix_len": 3, "excluded_sector_groups": ["financial"]},
        year=2024,
        _read_engine=temp_engine,
    )

    assert [peer["corp_code"] for peer in allowed["peers"]] == ["00000002"]
    assert excluded["peer_count"] == 0
    assert excluded["selection_policy"]["exclusion_reasons"]["00000002"] == [
        "excluded_sector_group:financial"
    ]


def test_legacy_sector_filter_keeps_same_holding_sector_peers(temp_engine):
    from kreports.analysis.peer_benchmarks import select_peer_group
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject holding", induty_code="64201"),
            Company(corp_code="00000002", corp_name="Peer holding", induty_code="64201"),
            Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", total_assets=100),
            Financial(corp_code="00000002", year=2024, quarter=4, fs_div="CFS", total_assets=100),
        ])

    out = select_peer_group(
        "00000001",
        criteria=["industry"],
        year=2024,
        _read_engine=temp_engine,
    )

    assert [peer["corp_code"] for peer in out["peers"]] == ["00000002"]


def test_ranked_mode_preserves_score_order_before_peer_limit(temp_engine):
    from kreports.analysis.peer_benchmarks import select_peer_group
    from kreports.db.engine import get_session
    from kreports.db.models import AuditFee, Company, Financial

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="High score", induty_code="26420"),
            Company(corp_code="00000003", corp_name="High assets", induty_code="26430"),
            Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", total_assets=100),
            Financial(corp_code="00000002", year=2024, quarter=4, fs_div="CFS", total_assets=10),
            Financial(corp_code="00000003", year=2024, quarter=4, fs_div="CFS", total_assets=10_000),
            AuditFee(corp_code="00000002", bsns_year=2024, audit_fee_m=1),
        ])

    out = select_peer_group(
        "00000001",
        criteria={
            "mode": "ranked",
            "required_features": ["audit_fees"],
            "weights": {"coverage": 1.0},
        },
        peer_limit=1,
        year=2024,
        _read_engine=temp_engine,
    )

    assert [peer["corp_code"] for peer in out["peers"]] == ["00000002"]
    assert out["peers"][0]["selection_score"] == 1.0


def test_custom_codes_have_truthful_reasons_and_own_confidence(temp_engine):
    from kreports.analysis.peer_benchmarks import select_peer_group
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    included_codes = [f"{index:08d}" for index in range(2, 7)]
    with get_session() as session:
        session.add(Company(corp_code="00000001", corp_name="Subject", induty_code="26410"))
        session.add(Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", total_assets=100))
        for code in included_codes:
            session.add(Company(corp_code=code, corp_name=f"Custom {code}", induty_code="64110"))
            session.add(Financial(corp_code=code, year=2024, quarter=4, fs_div="CFS", total_assets=100))

    out = select_peer_group(
        "00000001",
        criteria={"industry_basis": "custom_codes", "included_corp_codes": included_codes},
        year=2024,
        _read_engine=temp_engine,
    )

    assert out["confidence"] == "low"
    assert out["selection_policy"]["confidence"] == "low"
    assert out["peers"][0]["include_reasons"] == ["explicit_custom_code"]
    assert out["peers"][0]["reason_components"]["industry_match"]["basis"] == "custom_codes"


def test_resolve_peers_keeps_existing_positional_parameter_order(temp_engine):
    from kreports.analysis.peer import resolve_peers
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Subject", induty_code="26410"),
            Company(corp_code="00000002", corp_name="Peer", induty_code="26420"),
            Financial(corp_code="00000001", year=2024, quarter=4, fs_div="CFS", total_assets=100),
            Financial(corp_code="00000002", year=2024, quarter=4, fs_div="CFS", total_assets=100),
        ])

    resolution = resolve_peers(
        "00000001", 3, 1, False, None, "CFS", 2024, temp_engine
    )

    assert resolution.peer_corp_codes == ["00000002"]
