from __future__ import annotations

from dataclasses import FrozenInstanceError
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
    comparison = compare_metric(cohort, "audit_fee")

    assert [member.corp_code for member in cohort.members] == ["00000002"]
    assert dict(cohort.exclusion_counts)["missing_required_metric"] == 1
    assert comparison.basis == "actual"
    assert comparison.subject_value == 100_000_000
    assert comparison.peer_values == (110_000_000.0,)


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
                session.add(
                    AuditProcedureItem(
                        corp_code=corp_code,
                        bsns_year=2024,
                        rcept_no=f"R{corp_code}",
                        source_type="audit_report",
                        procedure_type="inspection",
                        procedure_text="inspected invoices",
                        section_ordinal=1,
                        procedure_ordinal=1,
                    )
                )

    cohort = build_peer_cohort("00000001", 2024, profile, 10)

    assert [member.corp_code for member in cohort.members] == ["00000002"]
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
