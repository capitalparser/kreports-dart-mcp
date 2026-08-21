from __future__ import annotations


def _seed_peer_fixture():
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    peer_margins = [10, 20, 20, 30, 40, 50]
    with get_session() as session:
        session.add(
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="Subject",
                market="KOSPI",
                induty_code="26410",
            )
        )
        session.add(
            Financial(
                corp_code="00000001",
                year=2024,
                quarter=4,
                fs_div="CFS",
                revenue=100,
                operating_profit=20,
                net_income=15,
                total_assets=1_000,
                total_debt=400,
                total_equity=600,
            )
        )
        for index, margin in enumerate(
            peer_margins,
            start=2,
        ):
            code = f"{index:08d}"
            session.add(
                Company(
                    corp_code=code,
                    stock_code=f"{index:06d}",
                    corp_name=f"Peer {index}",
                    market="KOSPI",
                    induty_code=f"264{index:02d}",
                )
            )
            session.add(
                Financial(
                    corp_code=code,
                    year=2024,
                    quarter=4,
                    fs_div="CFS",
                    revenue=100,
                    operating_profit=margin,
                    net_income=margin,
                    total_assets=1_000 + index,
                    total_debt=400,
                    total_equity=600,
                )
            )


def test_display_limit_does_not_change_statistical_denominator(
    temp_engine,
):
    from kreports.analysis.peer_quality import (
        compare_custom_peer_financials,
    )

    _seed_peer_fixture()

    small = compare_custom_peer_financials(
        "00000001",
        year=2024,
        metrics=["영업이익률"],
        years_back=1,
        peer_limit=2,
    )
    large = compare_custom_peer_financials(
        "00000001",
        year=2024,
        metrics=["영업이익률"],
        years_back=1,
        peer_limit=20,
    )

    small_cell = small["results"][2024][
        "영업이익률"
    ]
    large_cell = large["results"][2024][
        "영업이익률"
    ]

    assert small["peer_count"] == 6
    assert small["returned_peer_count"] == 2
    assert small["presentation_truncated"] is True
    assert large["returned_peer_count"] == 6
    assert small_cell["n"] == large_cell["n"] == 6
    assert small_cell["p50"] == large_cell["p50"]
    assert (
        small["cohort_snapshot"]["cohort_id"]
        == large["cohort_snapshot"]["cohort_id"]
    )
    assert (
        small["cohort_snapshot"]["member_codes_hash"]
        == large["cohort_snapshot"]["member_codes_hash"]
    )


def test_percentile_uses_midrank_and_exposes_ties(
    temp_engine,
):
    from kreports.analysis.peer_quality import (
        compare_custom_peer_financials,
    )

    _seed_peer_fixture()

    out = compare_custom_peer_financials(
        "00000001",
        year=2024,
        metrics=["영업이익률"],
        years_back=1,
        peer_limit=2,
    )
    cell = out["results"][2024]["영업이익률"]

    assert cell["tie_count"] == 2
    assert cell["percentile_method"] == "midrank"
    assert cell["midrank_percentile"] == 33.3
    assert cell["percentile"] == 33.3
    assert cell["confidence"] == "sufficient_n"


def test_small_peer_population_suppresses_official_percentile(
    temp_engine,
):
    from kreports.analysis.peer_quality import (
        compare_custom_peer_financials,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        for index, margin in (
            (1, 20),
            (2, 10),
            (3, 30),
        ):
            code = f"{index:08d}"
            session.add(
                Company(
                    corp_code=code,
                    stock_code=f"{index:06d}",
                    corp_name=f"Company {index}",
                    market="KOSPI",
                    induty_code="26410",
                )
            )
            session.add(
                Financial(
                    corp_code=code,
                    year=2024,
                    quarter=4,
                    fs_div="CFS",
                    revenue=100,
                    operating_profit=margin,
                    net_income=margin,
                    total_assets=100,
                    total_debt=40,
                    total_equity=60,
                )
            )

    out = compare_custom_peer_financials(
        "00000001",
        year=2024,
        metrics=["영업이익률"],
        years_back=1,
        peer_criteria={
            "industry_basis": "custom_codes",
            "included_corp_codes": [
                "00000002",
                "00000003",
            ],
        },
    )
    cell = out["results"][2024]["영업이익률"]

    assert cell["n"] == 2
    assert cell["percentile"] is None
    assert cell["midrank_percentile"] == 50.0
    assert cell["confidence"] == "insufficient_n"
    assert out["data_quality"]["status"] == "limited"
