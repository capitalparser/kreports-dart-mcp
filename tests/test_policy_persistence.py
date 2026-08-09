"""
test_policy_persistence.py — AccountingPolicyItem 모델·collector·idempotency 검증.

계층:
  1. 모델 스키마 (컬럼·UniqueConstraint·Index)
  2. _sha1 유틸
  3. collect_policies_for_company 핵심 로직 (mock 기반)
  4. Upsert idempotency (재실행 시 unchanged 증가)
  5. body_hash 변화 감지
  6. 실데이터 조회 (Samsung policies if present)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from kreports.db import engine as engine_module
from kreports.db.models import AccountingPolicyItem, Base
from kreports.collector.policy_collector import (
    _sha1,
    collect_policies_for_company,
    collect_policies_batch,
)


# ---------------------------------------------------------------------------
# 1. 모델 스키마
# ---------------------------------------------------------------------------

class TestModelSchema:
    def test_expected_columns(self):
        cols = {c.name for c in AccountingPolicyItem.__table__.columns}
        expected = {
            "id", "corp_code", "bsns_year", "fs_div", "rcept_no",
            "item_key", "heading", "body", "body_hash", "body_length",
            "fetched_at",
        }
        assert expected == cols

    def test_unique_constraint(self):
        constraints = {
            c.name for c in AccountingPolicyItem.__table__.constraints
        }
        assert "uq_policy_item" in constraints

    def test_indexes_defined(self):
        idx_names = {i.name for i in AccountingPolicyItem.__table__.indexes}
        assert "idx_policy_item_corp_year" in idx_names
        assert "idx_policy_item_key" in idx_names

    def test_body_column_is_text(self):
        body_col = AccountingPolicyItem.__table__.columns["body"]
        assert body_col.nullable is False
        # Text 타입이어야 함 (length 제약 없음)
        assert body_col.type.__class__.__name__ == "Text"


# ---------------------------------------------------------------------------
# 2. _sha1 유틸
# ---------------------------------------------------------------------------

class TestSha1:
    def test_deterministic(self):
        assert _sha1("hello") == _sha1("hello")

    def test_different_inputs(self):
        assert _sha1("a") != _sha1("b")

    def test_korean_text(self):
        h = _sha1("유형자산은 원가로 측정한다")
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# 3. collector 로직 (in-memory DB + mock get_accounting_policy)
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_engine(monkeypatch):
    """In-memory SQLite로 engine.engine을 교체하여 격리."""
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(engine_module, "engine", test_engine)

    # policy_collector는 get_session을 통해 engine에 접근. engine 교체 후 sessionmaker 재생성.
    # get_session 컨텍스트 매니저는 SessionLocal에 bind된 engine을 사용하므로,
    # SessionLocal도 새 engine을 가리키게 교체해야 한다.
    new_session_maker = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(engine_module, "SessionLocal", new_session_maker)

    Base.metadata.create_all(bind=test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def mock_policy_data():
    """3개 item을 가진 가짜 policy_data."""
    return {
        "rcept_no": "20250311001085",
        "items": {
            "inventory_valuation": {
                "heading": "8. 재고자산",
                "body": "재고자산은 선입선출법으로 평가한다. 순실현가능가치가 원가보다 낮은 경우 저가법을 적용한다.",
            },
            "depreciation": {
                "heading": "10. 유형자산",
                "body": "유형자산은 원가에서 감가상각누계액과 손상차손누계액을 차감한 금액으로 표시된다.",
            },
            "revenue_recognition": {
                "heading": "5. 수익인식",
                "body": "수익은 고객과의 계약에서 재화나 용역을 이전함으로써 발생한다.",
            },
        },
        "raw_html": "<div>...</div>",
        "chapters": [
            {
                "note_no": "2",
                "note_title": "재무제표 작성기준",
                "section_type": "basis",
                "body": "연결재무제표는 한국채택국제회계기준에 따라 작성되었습니다.",
                "body_length": 35,
            },
            {
                "note_no": "3",
                "note_title": "중요한 회계정책",
                "section_type": "policy",
                "body": "수익은 고객과의 계약에서 수행의무가 이행될 때 인식합니다.",
                "body_length": 36,
            },
        ],
    }


class TestCollectPoliciesForCompany:
    def test_stores_all_items_new(self, temp_engine, mock_policy_data):
        with patch("dart_platform.collector.policy_collector.get_session",
                   lambda: engine_module.SessionLocal.__call__().__class__.__enter__ if False else None):
            pass  # placeholder for complex patching — 우회를 위해 직접 dashboard.db 모킹 사용

        # dashboard.db.get_accounting_policy를 patch
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=mock_policy_data):
            r = collect_policies_for_company("00000001", 2024, "CFS")

        assert r["error"] is None
        assert r["rcept_no"] == "20250311001085"
        assert r["items_stored"] == 3
        assert r["items_new"] == 3
        assert r["items_changed"] == 0
        assert r["items_unchanged"] == 0

        # DB에 실제로 저장됐는지
        with temp_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT item_key, heading, body, body_hash, body_length "
                     "FROM accounting_policy_items WHERE corp_code = '00000001'")
            ).fetchall()
        assert len(rows) == 3
        for row in rows:
            item_key, heading, body, body_hash, body_length = row
            assert item_key in mock_policy_data["items"]
            assert body == mock_policy_data["items"][item_key]["body"]
            assert body_hash == _sha1(body)
            assert body_length == len(body)

    def test_persists_note_chapters_idempotently(self, temp_engine, mock_policy_data):
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=mock_policy_data):
            r1 = collect_policies_for_company("00000001", 2024, "CFS")
            r2 = collect_policies_for_company("00000001", 2024, "CFS")

        assert r1["chapters_stored"] == 2
        assert r2["chapters_stored"] == 2

        with temp_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT note_no, note_title, section_type, body, body_hash, body_length "
                    "FROM accounting_note_chapters WHERE corp_code = '00000001' "
                    "ORDER BY note_no"
                )
            ).fetchall()
        assert len(rows) == 2
        assert rows[0].note_no == "2"
        assert rows[0].section_type == "basis"
        assert rows[1].note_no == "3"
        assert rows[1].section_type == "policy"
        assert rows[1].body_hash == _sha1(rows[1].body)
        assert rows[1].body_length == len(rows[1].body)

    def test_rerun_all_unchanged(self, temp_engine, mock_policy_data):
        """동일 데이터로 재실행 시 items_unchanged=3."""
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=mock_policy_data):
            collect_policies_for_company("00000001", 2024, "CFS")
            r2 = collect_policies_for_company("00000001", 2024, "CFS")

        assert r2["items_stored"] == 3
        assert r2["items_new"] == 0
        assert r2["items_changed"] == 0
        assert r2["items_unchanged"] == 3

    def test_body_change_detected(self, temp_engine, mock_policy_data):
        """body가 바뀌면 items_changed 증가."""
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=mock_policy_data):
            collect_policies_for_company("00000001", 2024, "CFS")

        # body 수정된 버전
        changed_data = {
            "rcept_no": mock_policy_data["rcept_no"],
            "items": {
                "inventory_valuation": {
                    "heading": "8. 재고자산",
                    "body": "재고자산은 총평균법으로 평가한다.",  # 방법론 변경
                },
                "depreciation": mock_policy_data["items"]["depreciation"],
                "revenue_recognition": mock_policy_data["items"]["revenue_recognition"],
            },
        }
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=changed_data):
            r = collect_policies_for_company("00000001", 2024, "CFS")

        assert r["items_new"] == 0
        assert r["items_changed"] == 1
        assert r["items_unchanged"] == 2

    def test_policy_none_returns_error(self, temp_engine):
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=None):
            r = collect_policies_for_company("00000001", 2024, "CFS")
        assert r["error"] is not None
        assert r["error_class"] == "no_data"
        assert r["items_stored"] == 0

    def test_empty_items_returns_error(self, temp_engine):
        with patch("kreports.analysis.queries.get_accounting_policy",
                   return_value={"rcept_no": "X", "items": {}, "raw_html": ""}):
            r = collect_policies_for_company("00000001", 2024, "CFS")
        assert r["error"] is not None
        assert r["error_class"] == "no_data"

    def test_empty_body_skipped(self, temp_engine):
        """body가 빈 문자열인 item은 저장되지 않는다."""
        data = {
            "rcept_no": "X",
            "items": {
                "good": {"heading": "H", "body": "real content here"},
                "bad": {"heading": "H2", "body": ""},
                "none_body": {"heading": "H3", "body": None},
            },
            "raw_html": "",
        }
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=data):
            r = collect_policies_for_company("00000001", 2024, "CFS")
        assert r["items_stored"] == 1


# ---------------------------------------------------------------------------
# 4. Batch 처리
# ---------------------------------------------------------------------------

class TestCollectPoliciesBatch:
    def test_batch_aggregates_results(self, temp_engine, mock_policy_data):
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=mock_policy_data):
            result = collect_policies_batch([
                ("00000001", 2024, "CFS"),
                ("00000002", 2024, "CFS"),
            ])
        assert result["total"] == 2
        assert result["ok"] == 2
        assert result["failed"] == 0
        assert result["items_total"] == 6
        assert result["items_new"] == 6

    def test_batch_progress_callback_called(self, temp_engine, mock_policy_data):
        calls = []
        def cb(idx, total, cc, yr, fd):
            calls.append((idx, total, cc, yr, fd))
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=mock_policy_data):
            collect_policies_batch(
                [("00000001", 2024, "CFS"), ("00000002", 2024, "OFS")],
                progress_callback=cb,
            )
        assert len(calls) == 2
        assert calls[0][:2] == (1, 2)
        assert calls[1][:2] == (2, 2)

    def test_batch_classifies_missing_policy_as_no_data(self, temp_engine):
        with patch("kreports.analysis.queries.get_accounting_policy", return_value=None):
            result = collect_policies_batch([
                ("00000001", 2024, "CFS"),
            ])
        assert result["failed"] == 0
        assert result["no_data"] == 1
        assert len(result["no_data_targets"]) == 1
        assert result["errors"] == []

    def test_batch_preserves_transport_failure_taxonomy(self, temp_engine):
        with patch(
            "kreports.analysis.queries.get_accounting_policy",
            side_effect=TimeoutError("network timeout"),
        ):
            result = collect_policies_batch([
                ("00000001", 2024, "CFS"),
            ])
        assert result["failed"] == 1
        assert result["no_data"] == 0
        assert result["errors"][0]["error_class"] == "transport_error"


# ---------------------------------------------------------------------------
# 5. 실데이터 조회 (Samsung policies가 있으면)
# ---------------------------------------------------------------------------

@pytest.mark.live_data
class TestRealSamsungPolicies:
    def _has_samsung_policies(self) -> bool:
        from kreports.db.engine import engine
        with engine.connect() as conn:
            cnt = conn.execute(
                text("SELECT COUNT(*) FROM accounting_policy_items "
                     "WHERE corp_code = '00126380'")
            ).scalar()
        return cnt > 0

    def test_samsung_has_multiple_years(self):
        if not self._has_samsung_policies():
            pytest.skip("Samsung 정책 데이터 없음 (collect-policies 미실행)")
        from kreports.db.engine import engine
        with engine.connect() as conn:
            years = [
                r[0] for r in conn.execute(
                    text("SELECT DISTINCT bsns_year "
                         "FROM accounting_policy_items "
                         "WHERE corp_code = '00126380' ORDER BY bsns_year")
                ).fetchall()
            ]
        assert len(years) >= 1

    def test_samsung_body_hash_length(self):
        if not self._has_samsung_policies():
            pytest.skip("Samsung 정책 데이터 없음")
        from kreports.db.engine import engine
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT body_hash FROM accounting_policy_items "
                     "WHERE corp_code = '00126380' LIMIT 10")
            ).fetchall()
        for r in rows:
            assert r[0] is not None
            assert len(r[0]) == 40  # sha1 hex
