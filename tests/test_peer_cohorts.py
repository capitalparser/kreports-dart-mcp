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
