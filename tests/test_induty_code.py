"""
test_induty_code.py — Company.induty_code 컬럼 · 마이그레이션 · 백필 · retry 검증.

Phase 2 항목 1의 완결성 테스트:
  1. Company 모델에 induty_code 컬럼이 존재
  2. _migrate_existing_tables ALTER가 멱등 (두 번 실행해도 안전)
  3. sector → induty_code 백필 SQL이 5자리 숫자만 이전
  4. 백필이 invalid sector 값(alphabet, length≠5)을 건드리지 않음
  5. idx_companies_induty 인덱스가 생성됨
  6. _fetch_company_info_with_retry가 None 반환 시 재시도
  7. get_company가 induty_code 필드를 노출
"""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from kreports.db import engine as engine_module
from kreports.db.models import Base, Company


# ---------------------------------------------------------------------------
# 1. 모델 레이어
# ---------------------------------------------------------------------------

class TestCompanyModel:
    def test_induty_code_column_defined(self):
        """Company 모델에 induty_code 컬럼이 선언되어 있어야 한다."""
        col_names = {c.name for c in Company.__table__.columns}
        assert "induty_code" in col_names

    def test_induty_code_type_and_nullable(self):
        """induty_code는 String(5) nullable."""
        col = Company.__table__.columns["induty_code"]
        assert col.nullable is True
        assert col.type.length == 5


# ---------------------------------------------------------------------------
# 2~5. 마이그레이션 · 백필 · 인덱스 (temp SQLite로 격리)
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_engine(monkeypatch):
    """
    In-memory SQLite 엔진으로 engine_module.engine을 교체.
    _migrate_existing_tables는 모듈 전역 engine을 참조하므로 monkeypatch.
    """
    test_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(engine_module, "engine", test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield test_engine
    test_engine.dispose()


def _insert_company(engine, **fields):
    defaults = {
        "corp_code": "00000001",
        "stock_code": "000001",
        "corp_name": "테스트기업",
        "market": None,
        "sector": None,
        "induty_code": None,
    }
    defaults.update(fields)
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO companies "
                "(corp_code, stock_code, corp_name, market, sector, induty_code, updated_at) "
                "VALUES (:corp_code, :stock_code, :corp_name, :market, :sector, :induty_code, "
                "datetime('now'))"
            ),
            defaults,
        )
        conn.commit()


def _fetch_induty(engine, corp_code: str):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT induty_code FROM companies WHERE corp_code = :c"),
            {"c": corp_code},
        ).scalar()


class TestMigrationIdempotency:
    def test_migration_twice_no_error(self, temp_engine):
        """_migrate_existing_tables를 두 번 실행해도 오류 없이 통과."""
        engine_module._migrate_existing_tables()
        engine_module._migrate_existing_tables()

    def test_induty_code_index_created(self, temp_engine):
        """idx_companies_induty 인덱스가 생성되어야 한다."""
        engine_module._migrate_existing_tables()
        with temp_engine.connect() as conn:
            names = [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='index' AND tbl_name='companies'"
                    )
                ).fetchall()
            ]
        assert "idx_companies_induty" in names


class TestBackfillSQL:
    def test_backfills_valid_5digit_numeric_sector(self, temp_engine):
        """5자리 숫자 sector 값이 induty_code로 이전되어야 한다."""
        _insert_company(
            temp_engine,
            corp_code="00000001",
            stock_code="000001",
            sector="27199",  # KSIC 5자리
            induty_code=None,
        )
        engine_module._migrate_existing_tables()
        assert _fetch_induty(temp_engine, "00000001") == "27199"

    def test_does_not_overwrite_existing_induty_code(self, temp_engine):
        """induty_code가 이미 있으면 sector 값이 덮어쓰지 않는다."""
        _insert_company(
            temp_engine,
            corp_code="00000002",
            stock_code="000002",
            sector="99999",
            induty_code="27199",  # 이미 설정됨
        )
        engine_module._migrate_existing_tables()
        assert _fetch_induty(temp_engine, "00000002") == "27199"

    def test_does_not_backfill_non_numeric_sector(self, temp_engine):
        """sector 값이 알파벳·혼합 문자열이면 백필 대상이 아니다."""
        _insert_company(
            temp_engine,
            corp_code="00000003",
            stock_code="000003",
            sector="ABC12",
            induty_code=None,
        )
        engine_module._migrate_existing_tables()
        assert _fetch_induty(temp_engine, "00000003") is None

    def test_does_not_backfill_wrong_length(self, temp_engine):
        """length != 5인 sector 값은 백필 대상이 아니다."""
        _insert_company(
            temp_engine,
            corp_code="00000004",
            stock_code="000004",
            sector="1234",  # 4자리
            induty_code=None,
        )
        _insert_company(
            temp_engine,
            corp_code="00000005",
            stock_code="000005",
            sector="123456",  # 6자리
            induty_code=None,
        )
        engine_module._migrate_existing_tables()
        assert _fetch_induty(temp_engine, "00000004") is None
        assert _fetch_induty(temp_engine, "00000005") is None

    def test_null_sector_stays_null(self, temp_engine):
        """sector가 NULL이면 induty_code도 NULL로 유지."""
        _insert_company(
            temp_engine,
            corp_code="00000006",
            stock_code="000006",
            sector=None,
            induty_code=None,
        )
        engine_module._migrate_existing_tables()
        assert _fetch_induty(temp_engine, "00000006") is None


# ---------------------------------------------------------------------------
# 6. retry 로직
# ---------------------------------------------------------------------------

class TestFetchRetry:
    def test_success_first_attempt_no_retry(self):
        """첫 호출 성공 시 재시도하지 않는다."""
        from kreports.collector import corp_sync

        expected = {"market": "KOSPI", "induty_code": "27199"}
        with patch.object(corp_sync, "fetch_company_info", return_value=expected) as m, \
                patch.object(corp_sync.time, "sleep"):
            result = corp_sync._fetch_company_info_with_retry("00000001")
        assert result == expected
        assert m.call_count == 1

    def test_retry_once_on_none(self):
        """첫 호출이 None이면 1회 재시도 후 성공."""
        from kreports.collector import corp_sync

        expected = {"market": "KOSPI", "induty_code": "27199"}
        with patch.object(
            corp_sync, "fetch_company_info", side_effect=[None, expected]
        ) as m, patch.object(corp_sync.time, "sleep"):
            result = corp_sync._fetch_company_info_with_retry("00000001")
        assert result == expected
        assert m.call_count == 2

    def test_returns_none_after_max_attempts(self):
        """max_attempts 이후에도 실패하면 None 반환."""
        from kreports.collector import corp_sync

        with patch.object(
            corp_sync, "fetch_company_info", return_value=None
        ) as m, patch.object(corp_sync.time, "sleep"):
            result = corp_sync._fetch_company_info_with_retry("00000001", max_attempts=2)
        assert result is None
        assert m.call_count == 2


# ---------------------------------------------------------------------------
# 7. dashboard/db.py 통합 스모크 (실제 DB)
# ---------------------------------------------------------------------------

class TestGetCompanyReturnsInduty:
    def test_get_company_has_induty_code_key(self):
        """
        get_company 반환 dict에 induty_code 키가 포함되어야 한다.
        (enrich-market 실행 전이라도 키는 존재, 값만 None일 수 있음)
        """
        from kreports.db.engine import get_session
        from kreports.db.models import Company as RealCompany

        with get_session() as session:
            row = session.query(RealCompany).filter(
                RealCompany.stock_code.isnot(None)
            ).first()
            if row is None:
                pytest.skip("상장사가 DB에 없음")
            stock_code = row.stock_code

        from kreports.analysis.queries import get_company
        result = get_company(stock_code)
        assert result is not None
        assert "induty_code" in result
