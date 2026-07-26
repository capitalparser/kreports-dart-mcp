from importlib import reload
import json
import os
from unittest.mock import patch

import pytest

from kreports.db.models import AuditFee, FetchLog
from kreports.collector.audit_fee_sources import normalize_endpoint_result
from kreports.collector.audit_fee_sources import AuditFeeObservation


CORP_CODE = "00126380"


def test_ds002_normalizes_contract_and_actual_fields_independently():
    observation = normalize_endpoint_result(
        year=2024,
        status="000",
        rows=[
            {
                "bsns_year": "제56기\n(당기)",
                "adtor": "삼정회계법인",
                "adt_cntrct_dtls_mendng": "7,500",
                "adt_cntrct_dtls_time": "78,000",
                "real_exc_dtls_mendng": "7,800",
                "real_exc_dtls_time": "76,830",
            }
        ],
        corp_code=CORP_CODE,
    )

    assert observation.contract_fee_m == 7500
    assert observation.contract_hours == 78000
    assert observation.actual_fee_m == 7800
    assert observation.actual_hours == 76830
    assert observation.availability_status == "available"
    assert observation.quality_status == "verified"


def test_unsupported_historical_period_is_not_transport_error():
    observation = normalize_endpoint_result(
        year=2021,
        status="013",
        rows=[],
        corp_code=CORP_CODE,
    )

    assert observation.availability_status == "not_available_from_endpoint"
    assert observation.quality_status == "missing"


def test_supported_period_013_is_missing_not_unsupported_or_transport_error():
    observation = normalize_endpoint_result(
        year=2025,
        status="013",
        rows=[],
        corp_code=CORP_CODE,
        source_supported=True,
    )

    assert observation.availability_status == "partial"
    assert observation.quality_status == "missing"


def test_unsupported_empty_success_rows_are_endpoint_gap():
    observation = normalize_endpoint_result(
        year=2021,
        status="000",
        rows=[],
        corp_code=CORP_CODE,
        source_supported=False,
    )

    assert observation.availability_status == "not_available_from_endpoint"
    assert observation.quality_status == "missing"


@pytest.fixture
def fresh_audit_fee_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_audit_fee.db"
    original_db_url = os.environ.get("DB_URL")
    monkeypatch.setenv("DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")

    from kreports import config as _cfg
    reload(_cfg)
    from kreports.db import engine as _eng
    reload(_eng)
    _eng.init_db()

    from kreports.collector import audit_fee_collector as _collector
    reload(_collector)

    yield _eng, _collector

    if original_db_url is None:
        os.environ.pop("DB_URL", None)
    else:
        os.environ["DB_URL"] = original_db_url
    reload(_cfg)
    reload(_eng)
    import kreports.analysis.api as _api
    import kreports.analysis.peer as _peer
    import kreports.analysis.readiness as _readiness
    _api._engine = _eng.engine
    _peer.engine = _eng.engine
    _readiness.engine = _eng.engine


def test_collect_audit_fee_success_saves_row(fresh_audit_fee_db):
    eng, collector = fresh_audit_fee_db
    response = {
        "status": "000",
        "list": [
            {
                "se": "당기",
                "nm": "삼일회계법인",
                "adt_fee": "1,000",
                "adt_time": "12,345",
                "nadt_fee": "250",
                "nadt_time": "300",
            }
        ],
    }

    with patch.object(collector, "fetch_audit_fee", return_value=response), \
         patch("kreports.config.settings.request_delay", 0):
        result = collector.collect_audit_fees_for(CORP_CODE, [2024])

    assert result == {"saved": 1, "no_data": 0, "error": 0}
    with eng.get_session() as session:
        row = session.query(AuditFee).filter_by(corp_code=CORP_CODE, bsns_year=2024).one()
        assert row.audit_fee_m == 1000
        assert row.non_audit_fee_m == 250
        assert row.nas_ratio == 0.25
        assert row.independence_risk_flag is False


def test_collect_audit_fee_parses_current_dart_shape(fresh_audit_fee_db):
    eng, collector = fresh_audit_fee_db
    response = {
        "status": "000",
        "list": [
            {
                "bsns_year": "제56기\n(당기)",
                "adtor": "삼정회계법인",
                "adt_cntrct_dtls_mendng": "7,800",
                "adt_cntrct_dtls_time": "78,000",
                "real_exc_dtls_mendng": "7,800",
                "real_exc_dtls_time": "76,830",
            }
        ],
        "non_audit_list": [
            {"servc_mendng": "43", "rm": "삼정회계법인"},
            {"servc_mendng": "57", "rm": "삼정회계법인"},
        ],
    }

    with patch.object(collector, "fetch_audit_fee", return_value=response), \
         patch("kreports.config.settings.request_delay", 0):
        result = collector.collect_audit_fees_for(CORP_CODE, [2024])

    assert result == {"saved": 1, "no_data": 0, "error": 0}
    with eng.get_session() as session:
        row = session.query(AuditFee).filter_by(corp_code=CORP_CODE, bsns_year=2024).one()
        assert row.auditor_nm == "삼정회계법인"
        assert row.audit_fee_m == 7800
        assert row.audit_hours == 76830
        assert row.non_audit_fee_m == 100
        assert row.nas_ratio == 0.0128


def test_collect_audit_fee_distinguishes_no_data(fresh_audit_fee_db):
    eng, collector = fresh_audit_fee_db

    with patch.object(
        collector,
        "fetch_audit_fee",
        return_value={"status": "013", "message": "조회된 데이터가 없습니다."},
    ), patch("kreports.config.settings.request_delay", 0):
        result = collector.collect_audit_fees_for(CORP_CODE, [2024])

    assert result == {"saved": 0, "no_data": 1, "error": 0}
    with eng.get_session() as session:
        row = session.query(AuditFee).one()
        assert row.availability_status == "partial"
        assert row.quality_status == "missing"
        outcome = (
            session.query(FetchLog)
            .filter_by(
                task_type="audit_fee",
                corp_code=CORP_CODE,
                year=2024,
            )
            .one()
        )
        assert outcome.status == "no_data"
        assert outcome.error_msg is None


def test_collect_audit_fee_counts_transport_failure_as_error(fresh_audit_fee_db):
    eng, collector = fresh_audit_fee_db

    with patch.object(
        collector,
        "fetch_audit_fee",
        return_value={"status": "ERR", "message": "Server disconnected"},
    ), patch("kreports.config.settings.request_delay", 0):
        result = collector.collect_audit_fees_for(CORP_CODE, [2024])

    assert result == {"saved": 0, "no_data": 0, "error": 1}
    with eng.get_session() as session:
        row = session.query(AuditFee).one()
        assert row.availability_status == "transport_error"
        assert row.quality_status == "error"
        outcome = (
            session.query(FetchLog)
            .filter_by(
                task_type="audit_fee",
                corp_code=CORP_CODE,
                year=2024,
            )
            .one()
        )
        assert outcome.status == "error"
        assert outcome.error_msg == "Server disconnected"


def test_typed_observation_upsert_is_idempotent_and_missing_does_not_erase(
    fresh_audit_fee_db,
):
    eng, collector = fresh_audit_fee_db
    verified = AuditFeeObservation(
        corp_code=CORP_CODE,
        bsns_year=2024,
        source_class="cached_business_report",
        actual_fee_m=1200,
        actual_hours=10500,
        source_rcept_no="20250318000001",
        availability_status="available",
        quality_status="verified",
    )
    missing = AuditFeeObservation(
        corp_code=CORP_CODE,
        bsns_year=2024,
        source_class="opendart_ds002",
        availability_status="transport_error",
        quality_status="error",
    )

    collector.upsert_audit_fee_observations([verified])
    collector.upsert_audit_fee_observations([verified])
    collector.upsert_audit_fee_observations([missing])

    with eng.get_session() as session:
        row = session.query(AuditFee).one()
        assert row.audit_fee_m == 1200
        assert row.audit_hours == 10500
        assert row.availability_status == "transport_error"
        assert row.quality_status == "error"
        assert len(__import__("json").loads(row.source_observations_json)) == 2


def test_cached_business_report_adapter_merges_historical_actuals(
    fresh_audit_fee_db,
):
    eng, collector = fresh_audit_fee_db
    body = """
    <p>단위: 천원, 시간</p>
    <table>
      <tr>
        <th>사업연도</th><th>감사인</th>
        <th>실제수행보수</th><th>실제수행시간</th>
      </tr>
      <tr><td>2021</td><td>삼일회계법인</td><td>1,200,000</td><td>10,500</td></tr>
    </table>
    """

    result = collector.ingest_cached_audit_fee_table(
        body,
        corp_code=CORP_CODE,
        bsns_year=2021,
        rcept_no="20220318000001",
    )

    assert result["saved"] == 1
    with eng.get_session() as session:
        row = session.query(AuditFee).one()
        assert row.actual_fee_m == 1200
        assert row.actual_hours == 10500
        assert row.audit_fee_m == 1200
        assert row.compatibility_basis == "actual"
        assert row.source_class == "cached_business_report"


def test_endpoint_gap_and_error_are_persisted_as_typed_observations(
    fresh_audit_fee_db,
):
    eng, collector = fresh_audit_fee_db
    responses = {
        2021: {"status": "013", "message": "조회된 데이터가 없습니다."},
        2025: {"status": "ERR", "message": "Server disconnected"},
    }

    with patch.object(
        collector,
        "fetch_audit_fee",
        side_effect=lambda _corp, year: responses[year],
    ), patch("kreports.config.settings.request_delay", 0):
        result = collector.collect_audit_fees_for(CORP_CODE, [2021, 2025])

    assert result == {"saved": 0, "no_data": 1, "error": 1}
    with eng.get_session() as session:
        rows = {
            row.bsns_year: row
            for row in session.query(AuditFee).order_by(AuditFee.bsns_year)
        }
        assert rows[2021].availability_status == "not_available_from_endpoint"
        assert rows[2025].availability_status == "transport_error"
        assert all(json.loads(row.source_observations_json) for row in rows.values())


def test_supported_empty_endpoint_rows_are_typed_partial(
    fresh_audit_fee_db,
):
    eng, collector = fresh_audit_fee_db
    with patch.object(
        collector,
        "fetch_audit_fee",
        return_value={"status": "000", "list": []},
    ), patch("kreports.config.settings.request_delay", 0):
        result = collector.collect_audit_fees_for(CORP_CODE, [2025])

    assert result == {"saved": 0, "no_data": 1, "error": 0}
    with eng.get_session() as session:
        row = session.query(AuditFee).one()
        assert row.availability_status == "partial"
        assert row.quality_status == "missing"


def test_cached_parse_error_surfaces_without_erasing_verified_values(
    fresh_audit_fee_db,
):
    eng, collector = fresh_audit_fee_db
    verified = AuditFeeObservation(
        corp_code=CORP_CODE,
        bsns_year=2024,
        source_class="cached_business_report",
        actual_fee_m=1200,
        actual_hours=10500,
        source_period="2024",
        source_rcept_no="20250318000001",
        availability_status="available",
        quality_status="verified",
    )
    collector.upsert_audit_fee_observations([verified])

    result = collector.ingest_cached_audit_fee_table(
        "<table><tr><th>감사보수</th></tr></table>",
        corp_code=CORP_CODE,
        bsns_year=2024,
        rcept_no="20250318000002",
    )

    assert result["parse_error"] == 1
    with eng.get_session() as session:
        row = session.query(AuditFee).one()
        assert row.audit_fee_m == 1200
        assert row.audit_hours == 10500
        assert row.availability_status == "parse_error"
        assert row.quality_status == "error"
        assert len(json.loads(row.source_observations_json)) == 2


def test_cached_not_found_is_persisted_as_typed_gap(fresh_audit_fee_db):
    eng, collector = fresh_audit_fee_db

    result = collector.ingest_cached_audit_fee_table(
        "<table><tr><th>임원보수</th></tr><tr><td>10</td></tr></table>",
        corp_code=CORP_CODE,
        bsns_year=2024,
        rcept_no="20250318000003",
    )

    assert result["not_found"] == 1
    with eng.get_session() as session:
        row = session.query(AuditFee).one()
        assert row.availability_status == "not_found_in_cached_report"
        assert row.quality_status == "missing"
        assert json.loads(row.source_observations_json)[0][
            "source_rcept_no"
        ] == "20250318000003"
