from importlib import reload
import os
from unittest.mock import patch

import pytest

from kreports.db.models import AuditFee


CORP_CODE = "00126380"


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
        assert session.query(AuditFee).count() == 0


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
        assert session.query(AuditFee).count() == 0
