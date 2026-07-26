from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import sqlite3

import pytest
from sqlalchemy import create_engine


def _seed_financial_cohort(temp_engine, *, peer_count: int = 5) -> None:
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    rows = [
        (
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="Subject",
                market="KOSPI",
                induty_code="26410",
            ),
            Financial(
                corp_code="00000001",
                year=2024,
                quarter=4,
                fs_div="CFS",
                revenue=1_000,
                operating_profit=100,
                net_income=80,
                total_assets=2_000,
                total_debt=800,
                total_equity=1_200,
            ),
        )
    ]
    for index in range(peer_count):
        corp_code = f"{index + 2:08d}"
        rows.append(
            (
                Company(
                    corp_code=corp_code,
                    stock_code=f"{index + 2:06d}",
                    corp_name=f"Peer {index + 1}",
                    market="KOSPI",
                    induty_code="26410",
                ),
                Financial(
                    corp_code=corp_code,
                    year=2024,
                    quarter=4,
                    fs_div="CFS",
                    revenue=900 + index * 50,
                    operating_profit=90 + index * 10,
                    net_income=70 + index * 5,
                    total_assets=1_800 + index * 100,
                    total_debt=700 + index * 20,
                    total_equity=1_100 + index * 80,
                ),
            )
        )
    with get_session() as session:
        for company, financial in rows:
            session.add(company)
            session.add(financial)


def test_peer_cohort_is_typed_immutable_and_explains_members(temp_engine):
    from kreports.analysis.peer import PeerCohort, build_peer_cohort

    _seed_financial_cohort(temp_engine)

    cohort = build_peer_cohort("00000001", 2024, "investor", 3)

    assert isinstance(cohort, PeerCohort)
    assert cohort.requested_year == 2024
    assert cohort.fs_div == "CFS"
    assert len(cohort.members) == 3
    assert cohort.total_candidates == 6
    assert cohort.eligible_count == 5
    assert all(member.reason_codes for member in cohort.members)
    assert all(dict(member.score_components) for member in cohort.members)
    assert dict(cohort.exclusion_counts)["outside_limit"] == 2
    with pytest.raises(FrozenInstanceError):
        cohort.profile = "audit_fee"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("peer_count", "percentile_available", "decile_available"),
    [(4, False, False), (5, True, False), (9, True, False), (10, True, True)],
)
def test_metric_rank_thresholds_are_fail_closed(
    temp_engine,
    peer_count,
    percentile_available,
    decile_available,
):
    from kreports.analysis.peer import build_peer_cohort, compare_metric

    _seed_financial_cohort(temp_engine, peer_count=peer_count)
    cohort = build_peer_cohort("00000001", 2024, "investor", peer_count)
    comparison = compare_metric(cohort, "operating_margin")

    assert (comparison.percentile is not None) is percentile_available
    assert (comparison.decile is not None) is decile_available
    assert comparison.n == peer_count
    assert comparison.unit == "ratio"
    if not percentile_available:
        assert comparison.confidence == "insufficient_n"


def test_metric_comparison_discloses_selected_cohort_truncation(temp_engine):
    from kreports.analysis.peer import build_peer_cohort, compare_metric

    _seed_financial_cohort(temp_engine, peer_count=100)
    cohort = build_peer_cohort("00000001", 2024, "investor", 5)
    comparison = compare_metric(cohort, "operating_margin")

    assert cohort.eligible_count == 100
    assert len(cohort.members) == 5
    assert dict(cohort.denominator_metadata) == {
        "company_universe": 101,
        "subject_excluded": 1,
        "candidate_peers": 100,
        "common_eligible": 100,
        "selected": 5,
        "outside_limit": 95,
        "outside_limit_is_presentation_exclusion": True,
    }
    assert "cohort_truncated:5/100" in comparison.limitations
    assert comparison.n == 5
    assert comparison.confidence == "sufficient_n"


def test_typed_constructors_reject_mutable_or_invalid_contracts():
    from kreports.analysis.peer import PeerCohort, PeerMember

    with pytest.raises(TypeError, match="reason_codes must be a tuple"):
        PeerMember(
            corp_code="00000001",
            corp_name="A",
            induty_code="26410",
            fs_div="CFS",
            score=1.0,
            reason_codes=["listed"],  # type: ignore[arg-type]
            score_components=(),
            metric_values=(),
        )

    with pytest.raises(ValueError, match="unsupported peer profile"):
        PeerCohort(
            subject_corp_code="00000001",
            subject_name="A",
            requested_year=2024,
            profile="invalid",
            fs_div="CFS",
            members=(),
            exclusions=(),
            exclusion_counts=(),
            total_candidates=1,
            eligible_count=0,
        )


def test_unknown_profile_and_metric_fail_closed(temp_engine):
    from kreports.analysis.peer import build_peer_cohort, compare_metric

    _seed_financial_cohort(temp_engine)
    with pytest.raises(ValueError, match="unsupported peer profile"):
        build_peer_cohort("00000001", 2024, "not-a-profile", 5)

    cohort = build_peer_cohort("00000001", 2024, "investor", 5)
    with pytest.raises(ValueError, match="unsupported metric key"):
        compare_metric(cohort, "invented_metric")


def test_common_exclusions_are_counted_and_bounded_deterministically(temp_engine):
    from kreports.analysis.peer import build_peer_cohort
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    _seed_financial_cohort(temp_engine, peer_count=2)
    companies = [
        Company(corp_code="00000100", corp_name="Unlisted", induty_code="26410"),
        Company(
            corp_code="00000101",
            stock_code="000101",
            corp_name="Bad industry",
            induty_code=None,
        ),
        Company(
            corp_code="00000102",
            stock_code="000102",
            corp_name="Financial sector",
            induty_code="64110",
        ),
        Company(
            corp_code="00000103",
            stock_code="000103",
            corp_name="No requested year",
            induty_code="26410",
        ),
        Company(
            corp_code="00000104",
            stock_code="000104",
            corp_name="OFS only",
            induty_code="26410",
        ),
        Company(
            corp_code="00000105",
            stock_code="000105",
            corp_name="Missing metric",
            induty_code="26410",
        ),
        Company(
            corp_code="00000106",
            stock_code="000106",
            corp_name="Size outlier",
            induty_code="26410",
        ),
    ]
    financials = [
        Financial(
            corp_code="00000104",
            year=2024,
            quarter=4,
            fs_div="OFS",
            revenue=100,
            total_assets=100,
        ),
        Financial(
            corp_code="00000105",
            year=2024,
            quarter=4,
            fs_div="CFS",
            total_assets=100,
        ),
        Financial(
            corp_code="00000106",
            year=2024,
            quarter=4,
            fs_div="CFS",
            revenue=1,
            total_assets=1,
        ),
    ]
    with get_session() as session:
        session.add_all(companies + financials)

    first = build_peer_cohort("00000001", 2024, "investor", 1)
    second = build_peer_cohort("00000001", 2024, "investor", 1)
    counts = dict(first.exclusion_counts)

    assert {
        "subject",
        "unlisted",
        "invalid_industry",
        "sector_mismatch",
        "year_unavailable",
        "fs_basis_mismatch",
        "missing_required_metric",
        "size_outlier",
        "outside_limit",
    } <= counts.keys()
    assert first.exclusions == second.exclusions
    assert len(first.exclusions) <= 50


def test_ranking_ties_use_stable_corp_code_order(temp_engine):
    from kreports.analysis.peer import build_peer_cohort
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    _seed_financial_cohort(temp_engine, peer_count=0)
    with get_session() as session:
        for corp_code in ("00000009", "00000002"):
            session.add(
                Company(
                    corp_code=corp_code,
                    stock_code=corp_code[-6:],
                    corp_name=corp_code,
                    market="KOSPI",
                    induty_code="26410",
                )
            )
            session.add(
                Financial(
                    corp_code=corp_code,
                    year=2024,
                    quarter=4,
                    fs_div="CFS",
                    revenue=1_000,
                    operating_profit=100,
                    total_assets=2_000,
                )
            )

    cohort = build_peer_cohort("00000001", 2024, "investor", 2)
    assert [member.corp_code for member in cohort.members] == [
        "00000002",
        "00000009",
    ]


def test_audit_fee_profile_never_mixes_actual_and_contract_populations(temp_engine):
    from kreports.analysis.peer import build_peer_cohort, compare_metric
    from kreports.db.engine import get_session
    from kreports.db.models import AuditFee

    _seed_financial_cohort(temp_engine, peer_count=2)
    with get_session() as session:
        session.add_all(
            [
                AuditFee(
                    corp_code="00000001",
                    bsns_year=2024,
                    actual_fee_m=100,
                    actual_hours=1_000,
                    contract_fee_m=95,
                    contract_hours=950,
                    audit_fee_m=100,
                    audit_hours=1_000,
                    compatibility_basis="actual",
                ),
                AuditFee(
                    corp_code="00000002",
                    bsns_year=2024,
                    actual_fee_m=110,
                    actual_hours=1_100,
                    audit_fee_m=110,
                    audit_hours=1_100,
                    compatibility_basis="actual",
                ),
                AuditFee(
                    corp_code="00000003",
                    bsns_year=2024,
                    contract_fee_m=90,
                    contract_hours=900,
                    audit_fee_m=90,
                    audit_hours=900,
                    compatibility_basis="contract",
                ),
            ]
        )

    cohort = build_peer_cohort("00000001", 2024, "audit_fee", 10)
    selected = compare_metric(cohort, "audit_fee")
    contract = compare_metric(cohort, "audit_fee_contract")

    assert {member.corp_code for member in cohort.members} == {
        "00000002",
        "00000003",
    }
    assert selected.basis == "actual"
    assert selected.subject_value == 100_000_000
    assert selected.peer_values == (110_000_000.0,)
    assert selected.unavailable_count == 1
    assert contract.basis == "contract"
    assert contract.subject_value == 95_000_000
    assert contract.peer_values == (90_000_000.0,)
    assert contract.unavailable_count == 1


def test_basis_dependent_generic_metric_fails_closed_without_subject_basis(
    temp_engine,
):
    from kreports.analysis.peer import build_peer_cohort, compare_metric
    from kreports.db.engine import get_session
    from kreports.db.models import AuditFee

    _seed_financial_cohort(temp_engine, peer_count=3)
    with get_session() as session:
        session.add_all(
            [
                AuditFee(
                    corp_code="00000001",
                    bsns_year=2024,
                    actual_hours=1_000,
                ),
                AuditFee(
                    corp_code="00000002",
                    bsns_year=2024,
                    actual_fee_m=110,
                    nas_ratio=0.2,
                ),
                AuditFee(
                    corp_code="00000003",
                    bsns_year=2024,
                    contract_fee_m=90,
                    nas_ratio=0.3,
                ),
                AuditFee(
                    corp_code="00000004",
                    bsns_year=2024,
                    audit_fee_m=80,
                    nas_ratio=0.4,
                ),
            ]
        )

    cohort = build_peer_cohort("00000001", 2024, "audit_fee", 10)
    generic = compare_metric(cohort, "audit_fee")
    nas = compare_metric(cohort, "nas_ratio")
    actual = compare_metric(cohort, "audit_fee_actual")
    contract = compare_metric(cohort, "audit_fee_contract")

    for comparison in (generic, nas):
        assert comparison.peer_values == ()
        assert comparison.n == 0
        assert comparison.unavailable_count == 3
        assert comparison.percentile is None
        assert comparison.decile is None
        assert comparison.confidence == "subject_unavailable"
        assert "subject_basis_unavailable" in comparison.limitations
    assert actual.basis == "actual"
    assert actual.peer_values == (110_000_000.0,)
    assert actual.confidence == "subject_unavailable"
    assert contract.basis == "contract"
    assert contract.peer_values == (90_000_000.0,)
    assert contract.confidence == "subject_unavailable"


@pytest.mark.parametrize("reverse_order", [False, True])
def test_duplicate_audit_fee_rows_merge_complementary_claims_without_order_bias(
    temp_engine,
    reverse_order,
):
    from sqlalchemy import text

    from kreports.analysis.peer import build_peer_cohort, compare_metric

    _seed_financial_cohort(temp_engine, peer_count=1)
    with temp_engine.begin() as connection:
        connection.execute(text("DROP TABLE audit_fees"))
        connection.execute(text(
            "CREATE TABLE audit_fees ("
            "corp_code TEXT, bsns_year INTEGER, "
            "actual_fee_m INTEGER, actual_hours INTEGER, "
            "contract_fee_m INTEGER, contract_hours INTEGER, "
            "audit_fee_m INTEGER, audit_hours INTEGER, nas_ratio REAL)"
        ))
        claims = [
            ("00000001", 2024, 100, None, None, None, None, None, None),
            ("00000001", 2024, None, None, 90, 900, None, None, None),
            ("00000002", 2024, 110, None, None, None, None, None, None),
            ("00000002", 2024, None, None, 95, 950, None, None, None),
        ]
        if reverse_order:
            claims.reverse()
        connection.execute(
            text(
                "INSERT INTO audit_fees VALUES "
                "(:cc, :year, :actual_fee, :actual_hours, :contract_fee, "
                ":contract_hours, :legacy_fee, :legacy_hours, :nas)"
            ),
            [
                {
                    "cc": row[0],
                    "year": row[1],
                    "actual_fee": row[2],
                    "actual_hours": row[3],
                    "contract_fee": row[4],
                    "contract_hours": row[5],
                    "legacy_fee": row[6],
                    "legacy_hours": row[7],
                    "nas": row[8],
                }
                for row in claims
            ],
        )

    cohort = build_peer_cohort("00000001", 2024, "audit_fee", 5)

    assert compare_metric(cohort, "audit_fee_actual").peer_values == (
        110_000_000.0,
    )
    assert compare_metric(cohort, "audit_fee_contract").peer_values == (
        95_000_000.0,
    )
    bases = dict(cohort.members[0].metric_bases)
    assert {
        key: bases[key]
        for key in (
            "audit_fee",
            "audit_fee_actual",
            "audit_fee_contract",
            "audit_hours",
            "audit_hours_contract",
        )
    } == {
        "audit_fee": "actual",
        "audit_fee_actual": "actual",
        "audit_fee_contract": "contract",
        "audit_hours": "contract",
        "audit_hours_contract": "contract",
    }


@pytest.mark.parametrize("reverse_order", [False, True])
def test_duplicate_audit_fee_conflict_without_recency_fails_closed_per_metric(
    temp_engine,
    reverse_order,
):
    from sqlalchemy import text

    from kreports.analysis.peer import build_peer_cohort, compare_metric

    _seed_financial_cohort(temp_engine, peer_count=1)
    with temp_engine.begin() as connection:
        connection.execute(text("DROP TABLE audit_fees"))
        connection.execute(text(
            "CREATE TABLE audit_fees ("
            "corp_code TEXT, bsns_year INTEGER, "
            "actual_fee_m INTEGER, contract_fee_m INTEGER)"
        ))
        claims = [
            {"cc": "00000001", "actual": 100, "contract": 90},
            {"cc": "00000001", "actual": 120, "contract": None},
            {"cc": "00000002", "actual": 110, "contract": 95},
            {"cc": "00000002", "actual": 130, "contract": None},
        ]
        if reverse_order:
            claims.reverse()
        connection.execute(
            text(
                "INSERT INTO audit_fees VALUES "
                "(:cc, 2024, :actual, :contract)"
            ),
            claims,
        )

    cohort = build_peer_cohort("00000001", 2024, "audit_fee", 5)
    actual = compare_metric(cohort, "audit_fee_actual")
    contract = compare_metric(cohort, "audit_fee_contract")

    assert actual.subject_value is None
    assert actual.peer_values == ()
    assert actual.confidence == "subject_unavailable"
    assert "duplicate_audit_fee_conflict:actual_fee_m" in cohort.limitations
    assert "duplicate_audit_fee_conflict:actual_fee_m" in cohort.members[0].limitations
    assert contract.subject_value == 90_000_000
    assert contract.peer_values == (95_000_000.0,)


@pytest.mark.parametrize("reverse_order", [False, True])
def test_duplicate_audit_fee_uses_newest_claim_and_preserves_older_complements(
    temp_engine,
    reverse_order,
):
    from sqlalchemy import text

    from kreports.analysis.peer import build_peer_cohort, compare_metric

    _seed_financial_cohort(temp_engine, peer_count=1)
    with temp_engine.begin() as connection:
        connection.execute(text("DROP TABLE audit_fees"))
        connection.execute(text(
            "CREATE TABLE audit_fees ("
            "id INTEGER, fetched_at TEXT, corp_code TEXT, bsns_year INTEGER, "
            "actual_fee_m INTEGER, contract_fee_m INTEGER)"
        ))
        claims = [
            (3, "2025-02-01", "00000001", 120, None),
            (1, "2025-01-01", "00000001", 100, 90),
            (4, "2025-02-01", "00000002", 130, None),
            (2, "2025-01-01", "00000002", 110, 95),
        ]
        if reverse_order:
            claims.reverse()
        connection.execute(
            text(
                "INSERT INTO audit_fees VALUES "
                "(:id, :fetched_at, :cc, 2024, :actual, :contract)"
            ),
            [
                {
                    "id": row[0],
                    "fetched_at": row[1],
                    "cc": row[2],
                    "actual": row[3],
                    "contract": row[4],
                }
                for row in claims
            ],
        )

    cohort = build_peer_cohort("00000001", 2024, "audit_fee", 5)

    assert compare_metric(cohort, "audit_fee_actual").subject_value == 120_000_000
    assert compare_metric(cohort, "audit_fee_actual").peer_values == (
        130_000_000.0,
    )
    assert compare_metric(cohort, "audit_fee_contract").subject_value == 90_000_000
    assert compare_metric(cohort, "audit_fee_contract").peer_values == (
        95_000_000.0,
    )


@pytest.mark.parametrize("reverse_order", [False, True])
def test_duplicate_financial_without_discriminator_excludes_ambiguous_peer(
    temp_engine,
    reverse_order,
):
    from sqlalchemy import text

    from kreports.analysis.peer import build_peer_cohort

    _seed_financial_cohort(temp_engine, peer_count=2)
    with temp_engine.begin() as connection:
        connection.execute(text("DROP TABLE financials"))
        connection.execute(text(
            "CREATE TABLE financials ("
            "corp_code TEXT, year INTEGER, quarter INTEGER, fs_div TEXT, "
            "revenue INTEGER, operating_profit INTEGER, net_income INTEGER, "
            "total_assets INTEGER, total_debt INTEGER, total_equity INTEGER)"
        ))
        peer_two_rows = [
            ("00000002", 2024, 4, "CFS", 900, 90, 70, 1800, 700, 1100),
            ("00000002", 2024, 4, "CFS", 800, 90, 70, 1800, 700, 1100),
        ]
        if reverse_order:
            peer_two_rows.reverse()
        rows = [
            ("00000001", 2024, 4, "CFS", 1000, 100, 80, 2000, 800, 1200),
            *peer_two_rows,
            ("00000003", 2024, 4, "CFS", 950, 95, 75, 1900, 750, 1150),
            ("00000003", 2024, 4, "OFS", 500, 50, 40, 1000, 400, 600),
        ]
        connection.execute(
            text(
                "INSERT INTO financials VALUES "
                "(:cc, :year, :quarter, :fs, :revenue, :op, :net, "
                ":assets, :debt, :equity)"
            ),
            [
                {
                    "cc": row[0],
                    "year": row[1],
                    "quarter": row[2],
                    "fs": row[3],
                    "revenue": row[4],
                    "op": row[5],
                    "net": row[6],
                    "assets": row[7],
                    "debt": row[8],
                    "equity": row[9],
                }
                for row in rows
            ],
        )

    cohort = build_peer_cohort("00000001", 2024, "investor", 5)

    assert [member.corp_code for member in cohort.members] == ["00000003"]
    assert dict(cohort.exclusion_counts)["duplicate_financial_ambiguous"] == 1
    exclusion = next(
        item for item in cohort.exclusions if item.corp_code == "00000002"
    )
    assert exclusion.reason_code == "duplicate_financial_ambiguous"


def test_duplicate_subject_financial_without_discriminator_fails_closed(
    temp_engine,
):
    from sqlalchemy import text

    from kreports.analysis.peer import build_peer_cohort

    _seed_financial_cohort(temp_engine, peer_count=1)
    with temp_engine.begin() as connection:
        connection.execute(text("DROP TABLE financials"))
        connection.execute(text(
            "CREATE TABLE financials ("
            "corp_code TEXT, year INTEGER, quarter INTEGER, fs_div TEXT, "
            "revenue INTEGER, operating_profit INTEGER, net_income INTEGER, "
            "total_assets INTEGER, total_debt INTEGER, total_equity INTEGER)"
        ))
        connection.execute(text(
            "INSERT INTO financials VALUES "
            "('00000001', 2024, 4, 'CFS', 1000, 100, 80, 2000, 800, 1200), "
            "('00000001', 2024, 4, 'CFS', 900, 100, 80, 2000, 800, 1200), "
            "('00000002', 2024, 4, 'CFS', 950, 95, 75, 1900, 750, 1150)"
        ))

    cohort = build_peer_cohort("00000001", 2024, "investor", 5)

    assert cohort.fs_div is None
    assert cohort.members == ()
    assert "duplicate_financial_ambiguous" in cohort.limitations


@pytest.mark.parametrize("discriminator", ["id", "fetched_at"])
@pytest.mark.parametrize("reverse_order", [False, True])
def test_duplicate_financial_uses_available_recency_discriminator(
    temp_engine,
    discriminator,
    reverse_order,
):
    from sqlalchemy import text

    from kreports.analysis.peer import build_peer_cohort

    _seed_financial_cohort(temp_engine, peer_count=1)
    optional_column = (
        "id INTEGER, " if discriminator == "id" else "fetched_at TEXT, "
    )
    with temp_engine.begin() as connection:
        connection.execute(text("DROP TABLE financials"))
        connection.execute(text(
            f"CREATE TABLE financials ({optional_column}"
            "corp_code TEXT, year INTEGER, quarter INTEGER, fs_div TEXT, "
            "revenue INTEGER, operating_profit INTEGER, net_income INTEGER, "
            "total_assets INTEGER, total_debt INTEGER, total_equity INTEGER)"
        ))
        if discriminator == "id":
            rows = [
                (2, "00000001", 1200, 120, 90, 2200, 900, 1300),
                (1, "00000001", 1000, 100, 80, 2000, 800, 1200),
                (3, "00000002", 1100, 110, 85, 2100, 850, 1250),
            ]
        else:
            rows = [
                (
                    "2025-02-01",
                    "00000001",
                    1200,
                    120,
                    90,
                    2200,
                    900,
                    1300,
                ),
                (
                    "2025-01-01",
                    "00000001",
                    1000,
                    100,
                    80,
                    2000,
                    800,
                    1200,
                ),
                (
                    "2025-02-01",
                    "00000002",
                    1100,
                    110,
                    85,
                    2100,
                    850,
                    1250,
                ),
            ]
        if reverse_order:
            rows.reverse()
        connection.execute(
            text(
                "INSERT INTO financials VALUES "
                "(:discriminator, :cc, 2024, 4, 'CFS', :revenue, :op, "
                ":net, :assets, :debt, :equity)"
            ),
            [
                {
                    "discriminator": row[0],
                    "cc": row[1],
                    "revenue": row[2],
                    "op": row[3],
                    "net": row[4],
                    "assets": row[5],
                    "debt": row[6],
                    "equity": row[7],
                }
                for row in rows
            ],
        )

    cohort = build_peer_cohort("00000001", 2024, "investor", 5)

    assert dict(cohort.subject_metrics)["revenue"] == 1200
    assert [member.corp_code for member in cohort.members] == ["00000002"]


def test_cohort_constructor_enforces_deep_and_denominator_invariants(temp_engine):
    from kreports.analysis.peer import PeerExclusion, build_peer_cohort

    _seed_financial_cohort(temp_engine, peer_count=3)
    cohort = build_peer_cohort("00000001", 2024, "investor", 2)

    assert dict(cohort.denominator_metadata)["outside_limit"] == 1
    with pytest.raises(TypeError, match="subject_metric_bases values"):
        replace(
            cohort,
            subject_metric_bases=(("assets", ["CFS"]),),  # type: ignore[list-item]
        )
    with pytest.raises(TypeError, match="score_policy values"):
        replace(
            cohort,
            score_policy=(("weight", {"assets": 1}),),  # type: ignore[dict-item]
        )
    with pytest.raises(ValueError, match="subject exclusion count"):
        replace(
            cohort,
            exclusion_counts=tuple(
                pair for pair in cohort.exclusion_counts if pair[0] != "subject"
            ),
        )
    with pytest.raises(ValueError, match="outside_limit"):
        replace(
            cohort,
            exclusion_counts=tuple(
                (reason, 2 if reason == "outside_limit" else count)
                for reason, count in cohort.exclusion_counts
            ),
        )
    with pytest.raises(ValueError, match="company universe"):
        replace(cohort, total_candidates=cohort.total_candidates + 1)
    with pytest.raises(ValueError, match="returned exclusion"):
        replace(
            cohort,
            exclusions=(
                *cohort.exclusions,
                PeerExclusion(
                    corp_code="99999999",
                    corp_name="Impossible",
                    reason_code="unlisted",
                ),
            ),
        )


@pytest.mark.parametrize(
    "profile",
    ["audit_risk", "accounting_policy", "kam_procedure"],
)
def test_auditor_profiles_require_requested_year_evidence(temp_engine, profile):
    from kreports.analysis.peer import build_peer_cohort
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingPolicyItem,
        Auditor,
        AuditProcedureItem,
        KamItem,
    )

    _seed_financial_cohort(temp_engine, peer_count=2)
    with get_session() as session:
        for corp_code in ("00000001", "00000002"):
            if profile == "audit_risk":
                session.add(
                    Auditor(
                        corp_code=corp_code,
                        bsns_year=2024,
                        fs_div="CFS",
                        auditor_nm="Auditor",
                    )
                )
            elif profile == "accounting_policy":
                session.add(
                    AccountingPolicyItem(
                        corp_code=corp_code,
                        bsns_year=2024,
                        fs_div="CFS",
                        rcept_no=f"R{corp_code}",
                        item_key="revenue_recognition",
                        body="policy",
                    )
                )
            else:
                kam = KamItem(
                    corp_code=corp_code,
                    bsns_year=2024,
                    rcept_no=f"R{corp_code}",
                    source_type="audit_report",
                    ordinal=1,
                    normalized_topic="revenue",
                    full_body_hash=f"H{corp_code}",
                    full_body_length=100,
                    source_basis="full_body",
                    quality_status="verified",
                )
                session.add(kam)
                session.flush()
                session.add(
                    AuditProcedureItem(
                        corp_code=corp_code,
                        bsns_year=2024,
                        rcept_no=f"R{corp_code}",
                        source_type="audit_report",
                        kam_item_id=kam.id,
                        kam_topic="revenue",
                        procedure_type="inspection",
                        procedure_text="inspected invoices",
                        section_ordinal=1,
                        procedure_ordinal=1,
                    )
                )

    cohort = build_peer_cohort("00000001", 2024, profile, 10)

    assert [member.corp_code for member in cohort.members] == ["00000002"]
    assert dict(cohort.exclusion_counts)["missing_profile_evidence"] == 1


def test_kam_procedure_profile_requires_linked_kam_row(temp_engine):
    from kreports.analysis.peer import build_peer_cohort
    from kreports.db.engine import get_session
    from kreports.db.models import AuditProcedureItem, KamItem

    _seed_financial_cohort(temp_engine, peer_count=2)
    with get_session() as session:
        linked: dict[str, KamItem] = {}
        for corp_code in ("00000001", "00000003"):
            kam = KamItem(
                corp_code=corp_code,
                bsns_year=2024,
                rcept_no=f"R{corp_code}",
                source_type="audit_report",
                ordinal=1,
                normalized_topic="revenue",
                full_body_hash=f"H{corp_code}",
                full_body_length=100,
                source_basis="full_body",
                quality_status="verified",
            )
            session.add(kam)
            session.flush()
            linked[corp_code] = kam
            session.add(
                AuditProcedureItem(
                    corp_code=corp_code,
                    bsns_year=2024,
                    rcept_no=f"R{corp_code}",
                    source_type="audit_report",
                    kam_item_id=kam.id,
                    kam_topic="revenue",
                    procedure_type="inspection",
                    procedure_text="inspected invoices",
                    section_ordinal=1,
                    procedure_ordinal=1,
                )
            )
        session.add(
            AuditProcedureItem(
                corp_code="00000002",
                bsns_year=2024,
                rcept_no="R00000002",
                source_type="audit_report",
                kam_item_id=None,
                kam_topic="revenue",
                procedure_type="inspection",
                procedure_text="unlinked procedure",
                section_ordinal=1,
                procedure_ordinal=1,
            )
        )

    cohort = build_peer_cohort("00000001", 2024, "kam_procedure", 10)

    assert [member.corp_code for member in cohort.members] == ["00000003"]
    assert dict(cohort.exclusion_counts)["missing_profile_evidence"] == 1


def test_requested_year_is_never_replaced_by_a_newer_year(temp_engine):
    from kreports.analysis.peer import build_peer_cohort
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000001",
                    stock_code="000001",
                    corp_name="Subject",
                    induty_code="26410",
                ),
                Company(
                    corp_code="00000002",
                    stock_code="000002",
                    corp_name="Peer",
                    induty_code="26410",
                ),
                Financial(
                    corp_code="00000001",
                    year=2025,
                    quarter=4,
                    fs_div="CFS",
                    revenue=100,
                    total_assets=200,
                ),
                Financial(
                    corp_code="00000002",
                    year=2025,
                    quarter=4,
                    fs_div="CFS",
                    revenue=100,
                    total_assets=200,
                ),
            ]
        )

    cohort = build_peer_cohort("00000001", 2024, "investor", 5)

    assert cohort.requested_year == 2024
    assert cohort.fs_div is None
    assert cohort.members == ()
    assert "subject_year_unavailable" in cohort.limitations


def test_company_lookup_rejects_ambiguous_and_escapes_like_wildcards(temp_engine):
    from kreports.analysis.peer import build_peer_cohort
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        for index, name in enumerate(
            ("Alpha Holdings", "Beta Holdings", "A%Corp", "AXCorp"),
            start=1,
        ):
            corp_code = f"{index:08d}"
            session.add(
                Company(
                    corp_code=corp_code,
                    stock_code=f"{index:06d}",
                    corp_name=name,
                    induty_code="26410",
                )
            )
            session.add(
                Financial(
                    corp_code=corp_code,
                    year=2024,
                    quarter=4,
                    fs_div="CFS",
                    revenue=100,
                    operating_profit=10,
                    total_assets=200,
                )
            )

    with pytest.raises(ValueError, match="ambiguous company.*Alpha Holdings.*Beta Holdings"):
        build_peer_cohort("Holdings", 2024, "investor", 5)

    literal = build_peer_cohort("%Corp", 2024, "investor", 5)
    assert literal.subject_name == "A%Corp"


def test_missing_and_pre_migration_databases_do_not_create_or_change_files(
    monkeypatch,
    tmp_path,
):
    from kreports.analysis import peer

    missing = tmp_path / "missing.db"
    monkeypatch.setattr(peer, "engine", create_engine(f"sqlite:///{missing}"))
    with pytest.raises(peer.PeerDatabaseUnavailable, match="runtime_db_unavailable"):
        peer.build_peer_cohort("00000001", 2024, "investor", 5)
    assert not missing.exists()
    assert not (tmp_path / "missing.db-wal").exists()
    assert not (tmp_path / "missing.db-shm").exists()

    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as connection:
        connection.execute(
            "CREATE TABLE companies "
            "(corp_code TEXT PRIMARY KEY, corp_name TEXT, stock_code TEXT, "
            "market TEXT, induty_code TEXT)"
        )
    before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
    }
    monkeypatch.setattr(peer, "engine", create_engine(f"sqlite:///{legacy}"))
    with pytest.raises(peer.PeerDatabaseUnavailable, match="missing_schema:financials"):
        peer.build_peer_cohort("00000001", 2024, "investor", 5)
    after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in tmp_path.iterdir()
    }
    assert after == before


def test_nonempty_wal_fails_closed_without_file_changes(monkeypatch, tmp_path):
    from kreports.analysis import peer

    database = tmp_path / "wal.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE companies (corp_code TEXT PRIMARY KEY)")
        connection.commit()
        wal = tmp_path / "wal.db-wal"
        assert wal.stat().st_size > 0
        monkeypatch.setattr(peer, "engine", create_engine(f"sqlite:///{database}"))
        before = {
            path.name: (path.stat().st_size, path.stat().st_mtime_ns)
            for path in tmp_path.iterdir()
        }
        with pytest.raises(
            peer.PeerDatabaseUnavailable,
            match="uncheckpointed_wal",
        ):
            peer.build_peer_cohort("00000001", 2024, "investor", 5)
        after = {
            path.name: (path.stat().st_size, path.stat().st_mtime_ns)
            for path in tmp_path.iterdir()
        }
        assert after == before
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("profile", "partial_table"),
    [
        ("audit_risk", "auditors"),
        ("audit_risk", "report_sections"),
        ("audit_risk", "audit_matter_items"),
        ("audit_risk", "kam_items"),
        ("accounting_policy", "accounting_policy_items"),
        ("kam_procedure", "audit_procedure_items"),
    ],
)
def test_partial_profile_tables_fail_closed_without_operational_error(
    temp_engine,
    profile,
    partial_table,
):
    from sqlalchemy import text

    from kreports.analysis.peer import build_peer_cohort

    _seed_financial_cohort(temp_engine, peer_count=1)
    with temp_engine.begin() as connection:
        connection.execute(text(f"DROP TABLE {partial_table}"))
        connection.execute(text(f"CREATE TABLE {partial_table} (id INTEGER PRIMARY KEY)"))

    cohort = build_peer_cohort("00000001", 2024, profile, 5)

    assert f"profile_schema_unavailable:{partial_table}" in cohort.limitations
    assert cohort.members == ()


def test_real_peer_facade_propagates_typed_cohort_metadata_to_answer_pack(
    temp_engine,
):
    from kreports.analysis import api
    from kreports.analysis.peer import build_peer_cohort
    from kreports.mcp.answer_pack import build_answer_pack

    _seed_financial_cohort(temp_engine, peer_count=5)
    cohort = build_peer_cohort("00000001", 2024, "investor", 3)

    result = api.compare_to_industry_multi(
        "00000001",
        metrics=["영업이익률"],
        years_back=1,
        _cohort=cohort,
    )
    pack = build_answer_pack("compare_to_industry_multi", result)

    assert result["results"][2024]["영업이익률"]["n"] == 3
    assert result["cohort_metadata"]["selected_count"] == 3
    assert result["cohort_metadata"]["eligible_count"] == 5
    table = next(
        table
        for table in pack["tables"]
        if table["id"] == "peer_cohort_metadata"
    )
    assert table["rows"][0]["profile"] == "investor"


@pytest.mark.parametrize(
    "profile",
    ["audit_fee", "audit_risk", "accounting_policy", "kam_procedure"],
)
def test_auditor_profile_statement_count_is_bounded_independent_of_candidates(
    temp_engine,
    profile,
):
    from sqlalchemy import event

    from kreports.analysis.peer import build_peer_cohort
    from kreports.db.engine import get_session
    from kreports.db.models import (
        AccountingPolicyItem,
        Auditor,
        AuditFee,
        AuditProcedureItem,
        Company,
        Financial,
        KamItem,
    )

    def add_profile_evidence(session, corp_codes):
        for corp_code in corp_codes:
            if profile == "audit_fee":
                session.add(
                    AuditFee(
                        corp_code=corp_code,
                        bsns_year=2024,
                        actual_fee_m=100,
                    )
                )
            elif profile == "audit_risk":
                session.add(
                    Auditor(
                        corp_code=corp_code,
                        bsns_year=2024,
                        fs_div="CFS",
                        auditor_nm="Auditor",
                    )
                )
            elif profile == "accounting_policy":
                session.add(
                    AccountingPolicyItem(
                        corp_code=corp_code,
                        bsns_year=2024,
                        fs_div="CFS",
                        rcept_no=f"R{corp_code}",
                        item_key="revenue_recognition",
                        body="policy",
                    )
                )
            else:
                kam = KamItem(
                    corp_code=corp_code,
                    bsns_year=2024,
                    rcept_no=f"R{corp_code}",
                    source_type="audit_report",
                    ordinal=1,
                    normalized_topic="revenue",
                    full_body_hash=f"H{corp_code}",
                    full_body_length=100,
                    source_basis="full_body",
                    quality_status="verified",
                )
                session.add(kam)
                session.flush()
                session.add(
                    AuditProcedureItem(
                        corp_code=corp_code,
                        bsns_year=2024,
                        rcept_no=f"R{corp_code}",
                        source_type="audit_report",
                        kam_item_id=kam.id,
                        kam_topic="revenue",
                        procedure_type="inspection",
                        procedure_text="inspected invoices",
                        section_ordinal=1,
                        procedure_ordinal=1,
                    )
                )

    _seed_financial_cohort(temp_engine, peer_count=10)
    initial_codes = [f"{index:08d}" for index in range(1, 12)]
    with get_session() as session:
        add_profile_evidence(session, initial_codes)

    statements: list[str] = []

    def before_cursor_execute(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        if statement.lstrip().upper().startswith(("SELECT", "PRAGMA")):
            statements.append(statement)

    event.listen(temp_engine, "before_cursor_execute", before_cursor_execute)
    try:
        build_peer_cohort("00000001", 2024, profile, 5)
        small_count = len(statements)

        added_codes = [f"{index:08d}" for index in range(12, 102)]
        with get_session() as session:
            for index, corp_code in enumerate(added_codes, start=12):
                session.add(
                    Company(
                        corp_code=corp_code,
                        stock_code=f"{index:06d}",
                        corp_name=f"Peer {index}",
                        market="KOSPI",
                        induty_code="26410",
                    )
                )
                session.add(
                    Financial(
                        corp_code=corp_code,
                        year=2024,
                        quarter=4,
                        fs_div="CFS",
                        revenue=1_000,
                        operating_profit=100,
                        net_income=80,
                        total_assets=2_000,
                        total_debt=800,
                        total_equity=1_200,
                    )
                )
            add_profile_evidence(session, added_codes)

        statements.clear()
        cohort = build_peer_cohort("00000001", 2024, profile, 5)
        large_count = len(statements)

        thousand_peer_codes = [
            f"{index:08d}" for index in range(102, 1002)
        ]
        with get_session() as session:
            for index, corp_code in enumerate(
                thousand_peer_codes,
                start=102,
            ):
                session.add(
                    Company(
                        corp_code=corp_code,
                        stock_code=f"{index:06d}",
                        corp_name=f"Peer {index}",
                        market="KOSPI",
                        induty_code="26410",
                    )
                )
                session.add(
                    Financial(
                        corp_code=corp_code,
                        year=2024,
                        quarter=4,
                        fs_div="CFS",
                        revenue=1_000,
                        operating_profit=100,
                        net_income=80,
                        total_assets=2_000,
                        total_debt=800,
                        total_equity=1_200,
                    )
                )
            add_profile_evidence(session, thousand_peer_codes)

        statements.clear()
        cohort = build_peer_cohort("00000001", 2024, profile, 5)
        thousand_count = len(statements)
    finally:
        event.remove(temp_engine, "before_cursor_execute", before_cursor_execute)

    assert cohort.eligible_count == 1_000
    expected_query_counts = {
        "audit_fee": 9,
        "audit_risk": 15,
        "accounting_policy": 9,
        "kam_procedure": 10,
    }
    assert small_count == expected_query_counts[profile]
    assert large_count == expected_query_counts[profile]
    assert thousand_count == expected_query_counts[profile]
    assert small_count <= 30
    assert large_count <= 30
    assert thousand_count <= 30
    assert small_count == large_count == thousand_count
