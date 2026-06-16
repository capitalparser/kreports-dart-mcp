import logging
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from contextlib import contextmanager
from kreports.config import settings, BASE_DIR
from kreports.db.models import Base  # noqa: F401 — 모든 모델 등록 트리거

_logger = logging.getLogger(__name__)

# 레거시 DB 파일명 자동 마이그레이션 (dart_platform.db → kreports.db)
_old_db = BASE_DIR / "dart_platform.db"
_new_db = BASE_DIR / "kreports.db"
if _old_db.exists() and not _new_db.exists():
    _old_db.rename(_new_db)
    _logger.info("DB 파일 마이그레이션: dart_platform.db → kreports.db")

_sqlite_connect_args = {
    "check_same_thread": False,
    "timeout": 60,
}

engine = create_engine(
    settings.db_url,
    connect_args=_sqlite_connect_args if "sqlite" in settings.db_url else {},
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_existing_tables()


def _migrate_existing_tables() -> None:
    """기존 테이블에 새 컬럼을 추가한다. 이미 존재하면 무시."""
    new_columns = [
        ("companies", "induty_code VARCHAR(5)"),
        ("financials", "account_map_confidence REAL"),
        ("financials", "cfs_ofs_gap_flag INTEGER"),
        ("financials", "accrual_ratio REAL"),
        ("financials", "op_cf_divergence_flag INTEGER"),
        # 단기 플래그
        ("financials", "op_net_divergence_flag INTEGER"),
        ("financials", "equity_negative_flag INTEGER"),
        ("financials", "going_concern_flag INTEGER"),
        ("financials", "revenue_yoy REAL"),
        ("financials", "revenue_vol_flag INTEGER"),
        ("financials", "amendment_count_annual INTEGER"),
        # 중기 플래그
        ("financials", "operating_cf INTEGER"),
        # Beneish M-Score
        ("financials", "beneish_dsri REAL"),
        ("financials", "beneish_gmi REAL"),
        ("financials", "beneish_aqi REAL"),
        ("financials", "beneish_sgi REAL"),
        ("financials", "beneish_depi REAL"),
        ("financials", "beneish_sgai REAL"),
        ("financials", "beneish_lvgi REAL"),
        ("financials", "beneish_tata REAL"),
        ("financials", "beneish_m_score REAL"),
        ("financials", "beneish_flag INTEGER"),
        # 데이터 출처 (acntall|acnt)
        ("financials", "source VARCHAR(20)"),
        # 감사보고서제출 첨부문서 식별자
        ("report_documents", "dcm_no VARCHAR(20)"),
        ("report_sections", "dcm_no VARCHAR(20)"),
        # raw document external storage manifest
        ("source_documents", "storage_uri VARCHAR(500)"),
        ("source_documents", "content_length INTEGER"),
        ("source_documents", "compressed_length INTEGER"),
        ("source_documents", "storage_status VARCHAR(30) DEFAULT 'inline' NOT NULL"),
        # long derived evidence external storage manifest
        ("accounting_note_chapters", "full_text_uri VARCHAR(500)"),
        ("accounting_note_chapters", "full_text_hash VARCHAR(40)"),
        ("accounting_note_chapters", "full_text_length INTEGER"),
        ("accounting_note_chapters", "full_text_compressed_length INTEGER"),
        ("accounting_note_chapters", "full_text_storage_status VARCHAR(30)"),
        ("evidence_documents", "full_text_uri VARCHAR(500)"),
        ("evidence_documents", "full_text_hash VARCHAR(40)"),
        ("evidence_documents", "full_text_length INTEGER"),
        ("evidence_documents", "full_text_compressed_length INTEGER"),
        ("evidence_documents", "full_text_storage_status VARCHAR(30)"),
        ("report_sections", "full_text_uri VARCHAR(500)"),
        ("report_sections", "full_text_hash VARCHAR(40)"),
        ("report_sections", "full_text_length INTEGER"),
        ("report_sections", "full_text_compressed_length INTEGER"),
        ("report_sections", "full_text_storage_status VARCHAR(30)"),
    ]
    with engine.connect() as conn:
        if "sqlite" in settings.db_url:
            for pragma_sql in [
                "PRAGMA journal_mode=WAL",
                "PRAGMA busy_timeout=60000",
                "PRAGMA synchronous=NORMAL",
            ]:
                try:
                    conn.execute(text(pragma_sql))
                    conn.commit()
                except Exception:
                    pass

        for table, col_def in new_columns:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_def}"))
                conn.commit()
            except Exception:
                pass  # 이미 존재하는 컬럼

        for ddl in [
            """
            CREATE TABLE IF NOT EXISTS financial_facts_compact (
              id INTEGER PRIMARY KEY,
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              fs_div VARCHAR(3) NOT NULL,
              metric_key VARCHAR(50) NOT NULL,
              metric_name VARCHAR(200) NOT NULL,
              amount BIGINT,
              source_account_id VARCHAR(200),
              source_account_nm VARCHAR(300),
              fetched_at DATETIME NOT NULL,
              CONSTRAINT uq_financial_facts_compact UNIQUE (corp_code, bsns_year, fs_div, metric_key)
            )
            """,
        ]:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                pass

        # financial_facts 인덱스 (테이블 자체는 create_all이 생성)
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_fact_corp_year ON financial_facts(corp_code, bsns_year)",
            "CREATE INDEX IF NOT EXISTS idx_fact_sj ON financial_facts(corp_code, bsns_year, fs_div, sj_div)",
            "CREATE INDEX IF NOT EXISTS idx_fin_compact_corp_year ON financial_facts_compact(corp_code, bsns_year)",
            "CREATE INDEX IF NOT EXISTS idx_fin_compact_metric ON financial_facts_compact(metric_key)",
            "CREATE INDEX IF NOT EXISTS idx_fetchlog_task_target_status ON fetch_log(task_type, corp_code, year, quarter, status)",
            "CREATE INDEX IF NOT EXISTS idx_subsidiary_matrix_parent_year ON subsidiary_auditor_matrix(parent_corp_code, bsns_year)",
            "CREATE INDEX IF NOT EXISTS idx_backfill_runs_key_status ON backfill_runs(task_type, year, market, status)",
            "CREATE INDEX IF NOT EXISTS idx_backfill_runs_started ON backfill_runs(started_at)",
            "CREATE INDEX IF NOT EXISTS idx_source_doc_corp_year ON source_documents(corp_code, bsns_year, source_type)",
            "CREATE INDEX IF NOT EXISTS idx_source_doc_hash ON source_documents(doc_hash)",
            "CREATE INDEX IF NOT EXISTS idx_source_doc_storage_status ON source_documents(storage_status)",
            "CREATE INDEX IF NOT EXISTS idx_source_doc_storage_uri ON source_documents(storage_uri)",
            "CREATE INDEX IF NOT EXISTS idx_extraction_runs_doc ON extraction_runs(rcept_no, source_type)",
            "CREATE INDEX IF NOT EXISTS idx_extraction_runs_extractor ON extraction_runs(extractor_name, status)",
            "CREATE INDEX IF NOT EXISTS idx_note_chapter_corp_year ON accounting_note_chapters(corp_code, bsns_year, fs_div)",
            "CREATE INDEX IF NOT EXISTS idx_note_chapter_section_type ON accounting_note_chapters(section_type)",
            "CREATE INDEX IF NOT EXISTS idx_note_chapter_full_text_uri ON accounting_note_chapters(full_text_uri)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_doc_corp_year ON evidence_documents(corp_code, bsns_year, source_type)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_doc_scope ON evidence_documents(evidence_scope)",
            "CREATE INDEX IF NOT EXISTS idx_evidence_doc_full_text_uri ON evidence_documents(full_text_uri)",
            "CREATE INDEX IF NOT EXISTS idx_report_section_full_text_uri ON report_sections(full_text_uri)",
            "CREATE INDEX IF NOT EXISTS idx_audit_procedure_corp_year ON audit_procedure_items(corp_code, bsns_year)",
            "CREATE INDEX IF NOT EXISTS idx_audit_procedure_type ON audit_procedure_items(procedure_type)",
            "CREATE INDEX IF NOT EXISTS idx_audit_procedure_topic ON audit_procedure_items(kam_topic)",
        ]:
            try:
                conn.execute(text(idx_sql))
                conn.commit()
            except Exception:
                pass

        # companies.induty_code 백필: 기존에 sector 컬럼에 편법 저장된 KSIC 5자리를 옮긴다.
        # 5자리이고 숫자로만 구성된 값만 KSIC 코드로 간주 (이전 다른 용도 값 제외).
        try:
            result = conn.execute(text(
                "UPDATE companies SET induty_code = sector "
                "WHERE induty_code IS NULL "
                "AND sector IS NOT NULL "
                "AND length(sector) = 5 "
                "AND sector GLOB '[0-9][0-9][0-9][0-9][0-9]'"
            ))
            conn.commit()
        except Exception:
            pass

        # companies.induty_code 조회 성능용 인덱스
        try:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_companies_induty ON companies(induty_code)"
            ))
            conn.commit()
        except Exception:
            pass


@contextmanager
def get_session() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
