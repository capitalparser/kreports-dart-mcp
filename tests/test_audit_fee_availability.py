import json

import pytest
from sqlalchemy import create_engine, text

import kreports.db.engine as engine_module
from kreports.analysis.audit_reporting import (
    audit_fee_availability,
    audit_fee_availability_trend,
)
from kreports.collector.audit_fee_sources import (
    AuditFeeObservation,
    merge_audit_fee_observations,
    observations_json,
)
from kreports.collector.audit_fee_collector import (
    upsert_audit_fee_observations,
)
from kreports.db.models import AuditFee


def _observation(source: str, fee: int | None, hours: int | None):
    return AuditFeeObservation(
        corp_code="001",
        bsns_year=2024,
        source_class=source,
        actual_fee_m=fee,
        actual_hours=hours,
        availability_status="available",
        quality_status="verified",
        source_rcept_no=f"{source}-receipt",
    )


def test_merge_prefers_actual_values_and_records_compatibility_basis():
    result = merge_audit_fee_observations(
        [
            AuditFeeObservation(
                corp_code="001",
                bsns_year=2024,
                source_class="opendart_ds002",
                contract_fee_m=900,
                contract_hours=9000,
                availability_status="partial",
                quality_status="verified",
            ),
            _observation("cached_business_report", 1000, 10000),
        ]
    )

    assert result.audit_fee_m == 1000
    assert result.audit_hours == 10000
    assert result.contract_fee_m == 900
    assert result.actual_fee_m == 1000
    assert result.compatibility_basis == "actual"


def test_conflict_above_five_percent_retains_both_but_exact_boundary_does_not():
    conflict = merge_audit_fee_observations(
        [_observation("opendart_ds002", 100, 1000), _observation("cached_business_report", 106, 1060)]
    )
    boundary = merge_audit_fee_observations(
        [_observation("opendart_ds002", 95, 950), _observation("cached_business_report", 100, 1000)]
    )

    assert conflict.conflict_status == "conflict"
    assert {item["metric"] for item in conflict.conflicts} == {
        "actual_fee_m",
        "actual_hours",
    }
    assert len(json.loads(conflict.source_observations_json)) == 2
    assert boundary.conflict_status == "none"


def test_missing_observation_does_not_erase_previous_verified_values():
    result = merge_audit_fee_observations(
        [
            AuditFeeObservation(
                corp_code="001",
                bsns_year=2024,
                source_class="opendart_ds002",
                availability_status="transport_error",
                quality_status="error",
            )
        ],
        previous={
            "actual_fee_m": 1200,
            "actual_hours": 10500,
            "audit_fee_m": 1200,
            "audit_hours": 10500,
            "compatibility_basis": "actual",
            "availability_status": "available",
            "quality_status": "verified",
        },
    )

    assert result.audit_fee_m == 1200
    assert result.audit_hours == 10500
    assert result.availability_status == "transport_error"
    assert result.quality_status == "error"


def test_read_only_availability_exposes_typed_values_and_conflict(temp_engine):
    provenance = [
        _observation("opendart_ds002", 1000, 10000).to_dict(),
        _observation("cached_business_report", 1200, 10500).to_dict(),
    ]
    with temp_engine.begin() as conn:
        conn.execute(
            AuditFee.__table__.insert(),
            {
                "corp_code": "001",
                "bsns_year": 2024,
                "audit_fee_m": 1200,
                "audit_hours": 10500,
                "contract_fee_m": 900,
                "contract_hours": 9000,
                "actual_fee_m": 1200,
                "actual_hours": 10500,
                "source_class": "cached_business_report",
                "source_rcept_no": "receipt",
                "source_period": "2024",
                "availability_status": "conflict",
                "quality_status": "conflict",
                "compatibility_basis": "actual",
                "conflict_status": "conflict",
                "source_observations_json": json.dumps(provenance),
            },
        )

    out = audit_fee_availability("001", 2024)

    assert out["availability_status"] == "conflict"
    assert out["selected"]["audit_fee_m"] == 1200
    assert out["selected"]["basis"] == "actual"
    assert out["conflicts"][0]["percentage_variance"] > 0.05
    assert len(out["source_observations"]) == 2


def test_latest_same_source_observation_controls_eligibility(temp_engine):
    upsert_audit_fee_observations(
        [
            AuditFeeObservation(
                corp_code="corrected",
                bsns_year=2024,
                source_class="opendart_ds002",
                source_period="2024",
                source_eligibility="eligible",
                availability_status="partial",
                quality_status="missing",
            )
        ]
    )
    upsert_audit_fee_observations(
        [
            AuditFeeObservation(
                corp_code="corrected",
                bsns_year=2024,
                source_class="opendart_ds002",
                source_period="2024",
                source_eligibility="not_eligible",
                availability_status="not_available_from_endpoint",
                quality_status="missing",
            )
        ]
    )

    out = audit_fee_availability("corrected", 2024)

    assert out["availability_status"] == "not_available_from_endpoint"
    assert out["source_eligibility"] == "not_eligible"
    assert len(out["source_observations"]) == 2


def test_legacy_row_is_inferred_without_rewrite(monkeypatch):
    legacy = create_engine("sqlite:///:memory:")
    with legacy.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE audit_fees (
                    id INTEGER PRIMARY KEY,
                    corp_code VARCHAR(8) NOT NULL,
                    bsns_year SMALLINT NOT NULL,
                    auditor_nm VARCHAR(100),
                    audit_fee_m INTEGER,
                    audit_hours INTEGER,
                    non_audit_fee_m INTEGER,
                    non_audit_hours INTEGER,
                    nas_ratio FLOAT
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO audit_fees "
                "(corp_code, bsns_year, audit_fee_m, audit_hours) "
                "VALUES ('legacy', 2023, 700, 8000)"
            )
        )
    monkeypatch.setattr(engine_module, "engine", legacy)

    out = audit_fee_availability("legacy", 2023)

    assert out["selected"]["audit_fee_m"] == 700
    assert out["selected"]["basis"] == "legacy_inferred"
    assert out["availability_status"] == "available"


def test_row_missing_legacy_fetch_log_uses_official_boundary_without_write(
    temp_engine,
):
    with temp_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO fetch_log "
                "(task_type, corp_code, year, status, fetched_at) VALUES "
                "('audit_fee', 'legacy-gap', 2014, 'no_data', CURRENT_TIMESTAMP), "
                "('audit_fee', 'legacy-gap', 2019, 'no_data', CURRENT_TIMESTAMP)"
            )
        )
        before_audit_fees = conn.execute(
            text("SELECT COUNT(*) FROM audit_fees")
        ).scalar_one()
        before_fetch_logs = conn.execute(
            text(
                "SELECT task_type, corp_code, year, status "
                "FROM fetch_log ORDER BY year"
            )
        ).all()

    unsupported = audit_fee_availability("legacy-gap", 2014)
    supported = audit_fee_availability("legacy-gap", 2019)

    with temp_engine.connect() as conn:
        after_audit_fees = conn.execute(
            text("SELECT COUNT(*) FROM audit_fees")
        ).scalar_one()
        after_fetch_logs = conn.execute(
            text(
                "SELECT task_type, corp_code, year, status "
                "FROM fetch_log ORDER BY year"
            )
        ).all()

    assert unsupported["availability_status"] == "not_available_from_endpoint"
    assert unsupported["source_eligibility"] == "not_eligible"
    assert supported["availability_status"] == "partial"
    assert supported["source_eligibility"] == "eligible"
    assert after_audit_fees == before_audit_fees
    assert after_fetch_logs == before_fetch_logs


def test_pre_typed_rows_use_official_boundary_without_rewrite(monkeypatch):
    legacy = create_engine("sqlite:///:memory:")
    with legacy.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE audit_fees (
                    id INTEGER PRIMARY KEY,
                    corp_code VARCHAR(8) NOT NULL,
                    bsns_year SMALLINT NOT NULL,
                    audit_fee_m INTEGER,
                    audit_hours INTEGER
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE fetch_log (
                    id INTEGER PRIMARY KEY,
                    task_type VARCHAR(50) NOT NULL,
                    corp_code VARCHAR(8),
                    year SMALLINT,
                    status VARCHAR(20) NOT NULL,
                    error_msg TEXT,
                    fetched_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO audit_fees (corp_code, bsns_year) VALUES "
                "('pretyped', 2014), ('pretyped', 2019)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO fetch_log "
                "(task_type, corp_code, year, status, fetched_at) VALUES "
                "('audit_fee', 'pretyped', 2014, 'no_data', CURRENT_TIMESTAMP), "
                "('audit_fee', 'pretyped', 2019, 'no_data', CURRENT_TIMESTAMP)"
            )
        )
        before = {
            "fees": conn.execute(
                text(
                    "SELECT corp_code, bsns_year, audit_fee_m, audit_hours "
                    "FROM audit_fees ORDER BY bsns_year"
                )
            ).all(),
            "logs": conn.execute(
                text(
                    "SELECT task_type, corp_code, year, status "
                    "FROM fetch_log ORDER BY year"
                )
            ).all(),
        }
    monkeypatch.setattr(engine_module, "engine", legacy)

    unsupported = audit_fee_availability("pretyped", 2014)
    supported = audit_fee_availability("pretyped", 2019)

    with legacy.connect() as conn:
        after = {
            "fees": conn.execute(
                text(
                    "SELECT corp_code, bsns_year, audit_fee_m, audit_hours "
                    "FROM audit_fees ORDER BY bsns_year"
                )
            ).all(),
            "logs": conn.execute(
                text(
                    "SELECT task_type, corp_code, year, status "
                    "FROM fetch_log ORDER BY year"
                )
            ).all(),
        }

    assert unsupported["availability_status"] == "not_available_from_endpoint"
    assert unsupported["source_eligibility"] == "not_eligible"
    assert supported["availability_status"] == "partial"
    assert supported["source_eligibility"] == "eligible"
    assert after == before


def test_five_year_trend_keeps_unavailable_periods_as_null_gaps(temp_engine):
    with temp_engine.begin() as conn:
        conn.execute(
            AuditFee.__table__.insert(),
            {
                "corp_code": "trend",
                "bsns_year": 2024,
                "audit_fee_m": 900,
                "audit_hours": 9000,
                "actual_fee_m": 900,
                "actual_hours": 9000,
                "availability_status": "available",
                "quality_status": "verified",
                "compatibility_basis": "actual",
            },
        )
        conn.execute(
            text(
                "INSERT INTO fetch_log "
                "(task_type, corp_code, year, status, fetched_at) "
                "VALUES ('audit_fee', 'trend', 2022, 'no_data', CURRENT_TIMESTAMP)"
            )
        )

    out = audit_fee_availability_trend("trend", 2025)
    by_year = {row["year"]: row for row in out["periods"]}

    assert len(out["periods"]) == 5
    assert by_year[2022]["availability_status"] == "partial"
    assert by_year[2022]["source_eligibility"] == "eligible"
    assert by_year[2022]["selected_fee_m"] is None
    assert by_year[2024]["selected_fee_m"] == 900
    assert all(row["selected_fee_m"] != 0 for row in out["periods"])


def test_feature_grade_excludes_endpoint_unsupported_years(monkeypatch):
    import kreports.quality.company_year as quality_module

    def availability(_corp_code, year):
        if year < 2024:
            return {
                "availability_status": "not_available_from_endpoint",
                "source_eligibility": "not_eligible",
                "selected": {"audit_fee_m": None, "audit_hours": None},
            }
        return {
            "availability_status": "available",
            "source_eligibility": "eligible",
            "selected": {"audit_fee_m": 100, "audit_hours": 1000},
        }

    monkeypatch.setattr(quality_module, "audit_fee_availability", availability)

    assert quality_module._audit_fee_peer_grade("001", 2025) == "A"


def test_newer_same_source_observation_supersedes_older_selection():
    older = AuditFeeObservation(
        corp_code="001",
        bsns_year=2024,
        source_class="cached_business_report",
        actual_fee_m=100,
        actual_hours=1000,
        source_period="2023",
        source_rcept_no="20240318000001",
        availability_status="available",
        quality_status="verified",
    )
    newer = AuditFeeObservation(
        corp_code="001",
        bsns_year=2024,
        source_class="cached_business_report",
        actual_fee_m=104,
        actual_hours=1040,
        source_period="2024",
        source_rcept_no="20250318000001",
        availability_status="available",
        quality_status="verified",
    )

    result = merge_audit_fee_observations([older, newer])

    assert result.actual_fee_m == 104
    assert result.actual_hours == 1040
    assert result.source_period == "2024"
    assert len(json.loads(result.source_observations_json)) == 2


def test_feature_grade_fails_when_any_eligible_period_has_source_blocker(
    monkeypatch,
):
    import kreports.quality.company_year as quality_module

    def availability(_corp_code, year):
        if year == 2025:
            return {
                "availability_status": "transport_error",
                "source_eligibility": "eligible",
                "selected": {"audit_fee_m": 100, "audit_hours": 1000},
            }
        return {
            "availability_status": "available",
            "source_eligibility": "eligible",
            "selected": {"audit_fee_m": 100, "audit_hours": 1000},
        }

    monkeypatch.setattr(quality_module, "audit_fee_availability", availability)

    assert quality_module._audit_fee_peer_grade("001", 2025) == "D"


def test_feature_grade_excludes_unknown_periods_from_denominator(monkeypatch):
    import kreports.quality.company_year as quality_module

    def availability(_corp_code, year):
        if year == 2025:
            return {
                "availability_status": "available",
                "source_eligibility": "eligible",
                "selected": {"audit_fee_m": 100, "audit_hours": 1000},
            }
        return {
            "availability_status": "missing",
            "source_eligibility": "unknown",
            "selected": {"audit_fee_m": None, "audit_hours": None},
        }

    monkeypatch.setattr(quality_module, "audit_fee_availability", availability)

    assert quality_module._audit_fee_peer_grade("001", 2025) == "A"


def test_feature_grade_excludes_schema_unknown_periods(monkeypatch):
    import kreports.quality.company_year as quality_module

    def availability(_corp_code, year):
        if year == 2025:
            return {
                "availability_status": "available",
                "source_eligibility": "eligible",
                "selected": {"audit_fee_m": 100, "audit_hours": 1000},
            }
        return {
            "availability_status": "schema_unavailable",
            "source_eligibility": "unknown",
            "selected": {"audit_fee_m": None, "audit_hours": None},
        }

    monkeypatch.setattr(quality_module, "audit_fee_availability", availability)

    assert quality_module._audit_fee_peer_grade("001", 2025) == "A"


def test_feature_grade_counts_explicit_eligible_partial_period(monkeypatch):
    import kreports.quality.company_year as quality_module

    def availability(_corp_code, year):
        if year == 2025:
            return {
                "availability_status": "available",
                "source_eligibility": "eligible",
                "selected": {"audit_fee_m": 100, "audit_hours": 1000},
            }
        if year == 2024:
            return {
                "availability_status": "partial",
                "source_eligibility": "eligible",
                "selected": {"audit_fee_m": None, "audit_hours": None},
            }
        return {
            "availability_status": "missing",
            "source_eligibility": "unknown",
            "selected": {"audit_fee_m": None, "audit_hours": None},
        }

    monkeypatch.setattr(quality_module, "audit_fee_availability", availability)

    assert quality_module._audit_fee_peer_grade("001", 2025) == "C"


def test_feature_grade_with_zero_explicit_eligible_periods_is_not_applicable(
    monkeypatch,
):
    import kreports.quality.company_year as quality_module

    monkeypatch.setattr(
        quality_module,
        "audit_fee_availability",
        lambda _corp_code, _year: {
            "availability_status": "missing",
            "source_eligibility": "unknown",
            "selected": {"audit_fee_m": None, "audit_hours": None},
        },
    )

    assert quality_module._audit_fee_peer_grade("001", 2025) == "not_applicable"


def test_malformed_provenance_fails_closed_with_limitation(temp_engine):
    with temp_engine.begin() as conn:
        conn.execute(
            AuditFee.__table__.insert(),
            {
                "corp_code": "malformed",
                "bsns_year": 2024,
                "audit_fee_m": 900,
                "audit_hours": 9000,
                "actual_fee_m": 900,
                "actual_hours": 9000,
                "availability_status": "available",
                "quality_status": "verified",
                "compatibility_basis": "actual",
                "source_observations_json": "{not-json",
            },
        )

    out = audit_fee_availability("malformed", 2024)

    assert out["availability_status"] == "parse_error"
    assert out["quality_status"] == "error"
    assert out["selected"]["audit_fee_m"] == 900
    assert any("provenance" in item.lower() for item in out["limitations"])


@pytest.mark.parametrize(
    "provenance",
    [
        "[123]",
        '[{"foo":"bar"}]',
    ],
)
def test_structurally_invalid_provenance_fails_closed(
    temp_engine,
    provenance,
):
    with temp_engine.begin() as conn:
        conn.execute(
            AuditFee.__table__.insert(),
            {
                "corp_code": f"invalid-{len(provenance)}",
                "bsns_year": 2024,
                "audit_fee_m": 900,
                "audit_hours": 9000,
                "actual_fee_m": 900,
                "actual_hours": 9000,
                "availability_status": "available",
                "quality_status": "verified",
                "compatibility_basis": "actual",
                "source_observations_json": provenance,
            },
        )

    out = audit_fee_availability(f"invalid-{len(provenance)}", 2024)

    assert out["availability_status"] == "parse_error"
    assert out["quality_status"] == "error"
    assert out["selected"]["audit_fee_m"] == 900
    assert out["source_observations"] == []
    assert any("provenance" in item.lower() for item in out["limitations"])


@pytest.mark.parametrize(
    ("observation_corp_code", "observation_year"),
    [
        ("different", 2024),
        ("identity", 2020),
    ],
)
def test_provenance_identity_mismatch_fails_closed(
    temp_engine,
    observation_corp_code,
    observation_year,
):
    observation = AuditFeeObservation(
        corp_code=observation_corp_code,
        bsns_year=observation_year,
        source_class="cached_business_report",
        actual_fee_m=900,
        actual_hours=9000,
        source_eligibility="eligible",
        availability_status="available",
        quality_status="verified",
    )
    with temp_engine.begin() as conn:
        conn.execute(
            AuditFee.__table__.insert(),
            {
                "corp_code": "identity",
                "bsns_year": 2024,
                "audit_fee_m": 900,
                "audit_hours": 9000,
                "actual_fee_m": 900,
                "actual_hours": 9000,
                "availability_status": "available",
                "quality_status": "verified",
                "compatibility_basis": "actual",
                "source_observations_json": json.dumps(
                    [observation.to_dict()]
                ),
            },
        )

    out = audit_fee_availability("identity", 2024)

    assert out["availability_status"] == "parse_error"
    assert out["quality_status"] == "error"
    assert out["selected"]["audit_fee_m"] == 900
    assert out["source_observations"] == []
    assert any("provenance" in item.lower() for item in out["limitations"])


def test_bounded_provenance_keeps_newest_observation_from_each_source():
    values = [
        AuditFeeObservation(
            corp_code="001",
            bsns_year=2024,
            source_class="cached_business_report",
            actual_fee_m=100 + index,
            source_period=str(2000 + index),
            source_rcept_no=f"{2000 + index}0318000001",
            availability_status="available",
            quality_status="verified",
        )
        for index in range(25)
    ]
    values.append(
        AuditFeeObservation(
            corp_code="001",
            bsns_year=2024,
            source_class="opendart_ds002",
            source_period="2024",
            availability_status="transport_error",
            quality_status="error",
        )
    )

    bounded = json.loads(observations_json(values, limit=20))

    assert len(bounded) == 20
    assert any(item["source_class"] == "opendart_ds002" for item in bounded)
    assert any(
        item["source_class"] == "cached_business_report"
        and item["source_period"] == "2024"
        for item in bounded
    )


def test_read_only_availability_does_not_create_missing_sqlite_or_sidecars(
    tmp_path,
    monkeypatch,
):
    missing = tmp_path / "missing.db"
    missing_engine = create_engine(f"sqlite:///{missing}")
    monkeypatch.setattr(engine_module, "engine", missing_engine)

    out = audit_fee_availability("001", 2024)

    assert out["availability_status"] == "schema_unavailable"
    assert not missing.exists()
    assert not (tmp_path / "missing.db-wal").exists()
    assert not (tmp_path / "missing.db-shm").exists()
