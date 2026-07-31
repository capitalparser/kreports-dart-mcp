import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause

from kreports.config import settings
from kreports.db.models import (
    Base,
    Company,
    CompanyYearQuality,
    DatasetManifest,
    Disclosure,
    EvidenceDocument,
    Financial,
    FinancialFactCompact,
)
from kreports.db.quality_snapshot import (
    QUALITY_CONTENT_FIELDS,
    QUALITY_VERSION,
    quality_content_digest,
)
from kreports.runtime import is_readonly_mode

_sqlite_connect_args = {
    "check_same_thread": False,
    "timeout": 60,
}


class ReadonlySQLiteSnapshotUnavailable(RuntimeError):
    """A readonly runtime cannot safely serve an uncheckpointed WAL snapshot."""


class ReadonlySQLiteConfigurationError(RuntimeError):
    """A readonly SQLite URI cannot retain parameters outside the safe subset."""


def _readonly_sqlite_database_path(database_url: str) -> Path | None:
    """Return the file-backed SQLite target eligible for immutable readonly use."""
    if not is_readonly_mode():
        return None
    try:
        parsed = make_url(database_url)
    except Exception:
        return None
    if parsed.get_backend_name() != "sqlite":
        return None
    database = parsed.database
    if not database or database == ":memory:":
        return None
    allowed_query_keys = {"immutable", "mode", "uri"}
    unexpected_query_keys = set(parsed.query) - allowed_query_keys
    if unexpected_query_keys:
        raise ReadonlySQLiteConfigurationError(
            "readonly SQLite URI has unsupported query parameters"
        )
    if not database.startswith("file:"):
        if parsed.query:
            raise ReadonlySQLiteConfigurationError(
                "readonly SQLite URL must not discard query parameters"
            )
        return Path(database).resolve()

    parsed_file_uri = urlsplit(database)
    if (
        parsed_file_uri.scheme != "file"
        or parsed_file_uri.netloc not in {"", "localhost"}
        or parsed_file_uri.query
        or parsed_file_uri.fragment
    ):
        raise ReadonlySQLiteConfigurationError(
            "readonly SQLite file URI is not safely normalizable"
        )
    return Path(unquote(parsed_file_uri.path)).resolve()


def _open_checkpointed_readonly_sqlite(database_path: Path) -> sqlite3.Connection:
    """Open a non-writing reader only when its immutable snapshot is complete."""
    journal_path = Path(f"{database_path}-journal")
    if journal_path.exists() and journal_path.stat().st_size > 0:
        raise ReadonlySQLiteSnapshotUnavailable(
            "runtime_db_unavailable:hot_rollback_journal"
        )

    wal_path = Path(f"{database_path}-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        raise ReadonlySQLiteSnapshotUnavailable(
            "runtime_db_unavailable:uncheckpointed_wal"
        )
    return sqlite3.connect(
        f"{database_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        check_same_thread=False,
        timeout=60,
    )


_readonly_sqlite_path = _readonly_sqlite_database_path(settings.db_url)
if _readonly_sqlite_path is not None:
    engine = create_engine(
        "sqlite://",
        creator=lambda: _open_checkpointed_readonly_sqlite(
            _readonly_sqlite_path
        ),
        poolclass=NullPool,
        echo=False,
    )
else:
    engine = create_engine(
        settings.db_url,
        connect_args=(
            _sqlite_connect_args if "sqlite" in settings.db_url else {}
        ),
        echo=False,
    )

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def dispose_engine() -> None:
    """Release KReports-owned pooled connections without issuing SQL."""
    engine.dispose()


_TEXT_WRITE_PREFIX = re.compile(
    r"^\s*(?:INSERT|UPDATE|DELETE|REPLACE|MERGE|CREATE|ALTER|DROP|TRUNCATE|VACUUM|PRAGMA)\b",
    flags=re.IGNORECASE,
)
_TEXT_WRITE_KEYWORD = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|REPLACE|MERGE|CREATE|ALTER|DROP|TRUNCATE|VACUUM)\b",
    flags=re.IGNORECASE,
)


def _strip_leading_sql_comments(sql: str) -> str:
    """Remove leading SQL whitespace/comments before classifying a statement."""
    remaining = sql
    while True:
        remaining = remaining.lstrip()
        if remaining.startswith("--"):
            newline = remaining.find("\n")
            remaining = "" if newline < 0 else remaining[newline + 1:]
            continue
        if remaining.startswith("/*"):
            end = remaining.find("*/", 2)
            remaining = "" if end < 0 else remaining[end + 2:]
            continue
        return remaining


def _statement_mutates(statement: object) -> bool:
    """Return whether a Session.execute statement can change persistent state."""
    if isinstance(statement, (Insert, Update, Delete)):
        return True
    if isinstance(statement, TextClause):
        sql = _strip_leading_sql_comments(statement.text or "")
        return bool(
            _TEXT_WRITE_PREFIX.match(sql)
            or (sql.lstrip().upper().startswith("WITH") and _TEXT_WRITE_KEYWORD.search(sql))
        )
    return bool(getattr(statement, "is_dml", False) or getattr(statement, "is_ddl", False))


def _require_session_write(operation: str) -> None:
    # Lazy import avoids making config/database initialization depend on a
    # runtime import during module import time.
    from kreports.runtime import require_runtime_write

    require_runtime_write(operation)


def _guard_runtime_writes(session: Session) -> Session:
    """Install write checks on a session, including monkeypatched SessionLocal.

    Reads retain normal SQLAlchemy behavior.  Core and textual DML are blocked
    before execution; ORM unit-of-work changes are blocked before flush/commit.
    """
    original_execute = session.execute
    original_flush = session.flush
    original_commit = session.commit

    def execute(statement, *args, **kwargs):
        if _statement_mutates(statement):
            _require_session_write("database statement")
        return original_execute(statement, *args, **kwargs)

    def flush(*args, **kwargs):
        if session.new or session.dirty or session.deleted:
            _require_session_write("database ORM flush")
        return original_flush(*args, **kwargs)

    def commit(*args, **kwargs):
        if session.new or session.dirty or session.deleted:
            _require_session_write("database ORM commit")
        return original_commit(*args, **kwargs)

    def guarded_bulk(original_method, operation: str):
        def bulk(*args, **kwargs):
            _require_session_write(operation)
            return original_method(*args, **kwargs)
        return bulk

    session.execute = execute  # type: ignore[method-assign]
    session.flush = flush  # type: ignore[method-assign]
    session.commit = commit  # type: ignore[method-assign]
    for method_name in ("bulk_save_objects", "bulk_insert_mappings", "bulk_update_mappings"):
        original_method = getattr(session, method_name, None)
        if original_method is not None:
            setattr(session, method_name, guarded_bulk(original_method, f"database {method_name}"))
    return session


def init_db() -> None:
    _require_session_write("initialize database schema")
    Base.metadata.create_all(bind=engine)
    from kreports.db.migrations import apply_schema_migrations

    with engine.begin() as connection:
        apply_schema_migrations(connection)
    _migrate_existing_tables()


def _migrate_existing_tables() -> None:
    """Bring legacy tables forward using inspection instead of ignored errors."""
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
    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            for pragma_sql in (
                "PRAGMA journal_mode=WAL",
                "PRAGMA busy_timeout=60000",
                "PRAGMA synchronous=NORMAL",
            ):
                conn.execute(text(pragma_sql))
            conn.commit()

    with engine.begin() as conn:
        inspector = inspect(conn)
        table_names = set(inspector.get_table_names())
        columns_by_table = {
            table_name: {
                column["name"]
                for column in inspector.get_columns(table_name)
            }
            for table_name in table_names
        }
        for table, col_def in new_columns:
            column_name = col_def.split(maxsplit=1)[0]
            if (
                table in table_names
                and column_name not in columns_by_table[table]
            ):
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_def}"))
                columns_by_table[table].add(column_name)

        for table_name, ddl in (
            (
                "financial_facts_compact",
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
            ),
        ):
            if table_name not in table_names:
                conn.execute(text(ddl))
                table_names.add(table_name)

        # financial_facts 인덱스 (테이블 자체는 create_all이 생성)
        index_statements = (
            ("financial_facts", "idx_fact_corp_year", "CREATE INDEX idx_fact_corp_year ON financial_facts(corp_code, bsns_year)"),
            ("financial_facts", "idx_fact_sj", "CREATE INDEX idx_fact_sj ON financial_facts(corp_code, bsns_year, fs_div, sj_div)"),
            ("financial_facts_compact", "idx_fin_compact_corp_year", "CREATE INDEX idx_fin_compact_corp_year ON financial_facts_compact(corp_code, bsns_year)"),
            ("financial_facts_compact", "idx_fin_compact_metric", "CREATE INDEX idx_fin_compact_metric ON financial_facts_compact(metric_key)"),
            ("fetch_log", "idx_fetchlog_task_target_status", "CREATE INDEX idx_fetchlog_task_target_status ON fetch_log(task_type, corp_code, year, quarter, status)"),
            ("subsidiary_auditor_matrix", "idx_subsidiary_matrix_parent_year", "CREATE INDEX idx_subsidiary_matrix_parent_year ON subsidiary_auditor_matrix(parent_corp_code, bsns_year)"),
            ("backfill_runs", "idx_backfill_runs_key_status", "CREATE INDEX idx_backfill_runs_key_status ON backfill_runs(task_type, year, market, status)"),
            ("backfill_runs", "idx_backfill_runs_started", "CREATE INDEX idx_backfill_runs_started ON backfill_runs(started_at)"),
            ("source_documents", "idx_source_doc_corp_year", "CREATE INDEX idx_source_doc_corp_year ON source_documents(corp_code, bsns_year, source_type)"),
            ("source_documents", "idx_source_doc_hash", "CREATE INDEX idx_source_doc_hash ON source_documents(doc_hash)"),
            ("source_documents", "idx_source_doc_storage_status", "CREATE INDEX idx_source_doc_storage_status ON source_documents(storage_status)"),
            ("source_documents", "idx_source_doc_storage_uri", "CREATE INDEX idx_source_doc_storage_uri ON source_documents(storage_uri)"),
            ("extraction_runs", "idx_extraction_runs_doc", "CREATE INDEX idx_extraction_runs_doc ON extraction_runs(rcept_no, source_type)"),
            ("extraction_runs", "idx_extraction_runs_extractor", "CREATE INDEX idx_extraction_runs_extractor ON extraction_runs(extractor_name, status)"),
            ("accounting_note_chapters", "idx_note_chapter_corp_year", "CREATE INDEX idx_note_chapter_corp_year ON accounting_note_chapters(corp_code, bsns_year, fs_div)"),
            ("accounting_note_chapters", "idx_note_chapter_section_type", "CREATE INDEX idx_note_chapter_section_type ON accounting_note_chapters(section_type)"),
            ("accounting_note_chapters", "idx_note_chapter_full_text_uri", "CREATE INDEX idx_note_chapter_full_text_uri ON accounting_note_chapters(full_text_uri)"),
            ("evidence_documents", "idx_evidence_doc_corp_year", "CREATE INDEX idx_evidence_doc_corp_year ON evidence_documents(corp_code, bsns_year, source_type)"),
            ("evidence_documents", "idx_evidence_doc_scope", "CREATE INDEX idx_evidence_doc_scope ON evidence_documents(evidence_scope)"),
            ("evidence_documents", "idx_evidence_doc_full_text_uri", "CREATE INDEX idx_evidence_doc_full_text_uri ON evidence_documents(full_text_uri)"),
            ("report_sections", "idx_report_section_full_text_uri", "CREATE INDEX idx_report_section_full_text_uri ON report_sections(full_text_uri)"),
            ("audit_procedure_items", "idx_audit_procedure_corp_year", "CREATE INDEX idx_audit_procedure_corp_year ON audit_procedure_items(corp_code, bsns_year)"),
            ("audit_procedure_items", "idx_audit_procedure_type", "CREATE INDEX idx_audit_procedure_type ON audit_procedure_items(procedure_type)"),
            ("audit_procedure_items", "idx_audit_procedure_topic", "CREATE INDEX idx_audit_procedure_topic ON audit_procedure_items(kam_topic)"),
        )
        indexes_by_table = {
            table_name: {
                index["name"]
                for index in inspect(conn).get_indexes(table_name)
            }
            for table_name in table_names
        }
        for table_name, index_name, idx_sql in index_statements:
            if (
                table_name in table_names
                and index_name not in indexes_by_table[table_name]
            ):
                conn.execute(text(idx_sql))
                indexes_by_table[table_name].add(index_name)

        # companies.induty_code 백필: 기존에 sector 컬럼에 편법 저장된 KSIC 5자리를 옮긴다.
        # 5자리이고 숫자로만 구성된 값만 KSIC 코드로 간주 (이전 다른 용도 값 제외).
        if (
            conn.dialect.name == "sqlite"
            and {"sector", "induty_code"}.issubset(
                columns_by_table.get("companies", set())
            )
        ):
            conn.execute(text(
                "UPDATE companies SET induty_code = sector "
                "WHERE induty_code IS NULL "
                "AND sector IS NOT NULL "
                "AND length(sector) = 5 "
                "AND sector GLOB '[0-9][0-9][0-9][0-9][0-9]'"
            ))

        # companies.induty_code 조회 성능용 인덱스
        if (
            "companies" in table_names
            and "idx_companies_induty"
            not in indexes_by_table["companies"]
        ):
            conn.execute(text(
                "CREATE INDEX idx_companies_induty ON companies(induty_code)"
            ))


@contextmanager
def get_session() -> Session:
    session = _guard_runtime_writes(SessionLocal())
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _year_value(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.year
    if isinstance(value, int):
        return value
    return int(str(value)[:4])


def write_dataset_manifest(
    dataset_version: str,
    notes: str | None = None,
) -> dict:
    """Record an immutable collector-built dataset contract.

    Surrounding version whitespace is removed before both identity fields are
    written, so equivalent caller input cannot create alias manifests.
    """
    from kreports.db.migrations import current_schema_version

    _require_session_write("write dataset manifest")
    if not isinstance(dataset_version, str):
        raise ValueError(  # noqa: TRY004 - preserve public validation contract
            "dataset_version must contain 1 to 80 characters"
        )
    normalized_version = dataset_version.strip()
    if not normalized_version or len(normalized_version) > 80:
        raise ValueError("dataset_version must contain 1 to 80 characters")

    generated_at = datetime.now(UTC)
    try:
        with get_session() as session:
            if session.get(DatasetManifest, normalized_version) is not None:
                raise ValueError(
                    f"dataset manifest already exists: {normalized_version}"
                )

            schema_version = current_schema_version(session.connection())
            if not schema_version:
                raise RuntimeError(
                    "schema migrations must be applied before writing a "
                    "dataset manifest"
                )

            company_count = session.scalar(
                select(func.count()).select_from(Company)
            )
            disclosure_count = session.scalar(
                select(func.count()).select_from(Disclosure)
            )
            evidence_document_count = session.scalar(
                select(func.count()).select_from(EvidenceDocument)
            )
            quality_rows = list(
                session.execute(
                    select(
                        *(
                            getattr(CompanyYearQuality, field)
                            for field in QUALITY_CONTENT_FIELDS
                        )
                    ).order_by(
                        CompanyYearQuality.corp_code,
                        CompanyYearQuality.bsns_year,
                    )
                ).mappings()
            )
            quality_row_count = len(quality_rows)
            quality_coverage_year = session.scalar(
                select(func.max(CompanyYearQuality.bsns_year))
            )
            quality_versions = sorted(
                {
                    str(row["quality_version"])
                    for row in quality_rows
                }
            )
            if quality_versions and quality_versions != [QUALITY_VERSION]:
                raise RuntimeError(
                    "company-year quality ledger must use supported quality "
                    f"version {QUALITY_VERSION}"
                )
            quality_coverage_year_row_count = (
                int(
                    session.scalar(
                        select(func.count())
                        .select_from(CompanyYearQuality)
                        .where(
                            CompanyYearQuality.bsns_year
                            == quality_coverage_year
                        )
                    )
                    or 0
                )
                if quality_coverage_year is not None
                else 0
            )
            quality_snapshot = {
                "content_digest": quality_content_digest(quality_rows),
                "coverage_year": (
                    int(quality_coverage_year)
                    if quality_coverage_year is not None
                    else None
                ),
                "coverage_year_row_count": (
                    quality_coverage_year_row_count
                ),
                "quality_version": QUALITY_VERSION,
                "row_count": quality_row_count,
            }
            year_bounds = (
                session.execute(
                    select(func.min(Financial.year), func.max(Financial.year))
                ).one(),
                session.execute(
                    select(
                        func.min(FinancialFactCompact.bsns_year),
                        func.max(FinancialFactCompact.bsns_year),
                    )
                ).one(),
                session.execute(
                    select(
                        func.min(EvidenceDocument.bsns_year),
                        func.max(EvidenceDocument.bsns_year),
                    )
                ).one(),
                session.execute(
                    select(
                        func.min(CompanyYearQuality.bsns_year),
                        func.max(CompanyYearQuality.bsns_year),
                    )
                ).one(),
            )
            represented_years = [
                year
                for bounds in year_bounds
                for year in (_year_value(bounds[0]), _year_value(bounds[1]))
                if year is not None
            ]
            manifest = DatasetManifest(
                manifest_id=normalized_version,
                schema_version=schema_version,
                dataset_version=normalized_version,
                generated_at=generated_at,
                year_from=min(represented_years) if represented_years else None,
                year_to=max(represented_years) if represented_years else None,
                company_count=int(company_count or 0),
                disclosure_count=int(disclosure_count or 0),
                evidence_document_count=int(evidence_document_count or 0),
                quality_snapshot_json=json.dumps(
                    quality_snapshot,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                notes=notes,
            )
            session.add(manifest)
            session.flush()
            result = {
                "manifest_id": manifest.manifest_id,
                "schema_version": manifest.schema_version,
                "dataset_version": manifest.dataset_version,
                "generated_at": generated_at.isoformat(),
                "year_from": manifest.year_from,
                "year_to": manifest.year_to,
                "company_count": manifest.company_count,
                "disclosure_count": manifest.disclosure_count,
                "evidence_document_count": manifest.evidence_document_count,
                "quality_snapshot_json": manifest.quality_snapshot_json,
                "notes": manifest.notes,
            }
    except IntegrityError as exc:
        raise ValueError(
            f"dataset manifest already exists: {normalized_version}"
        ) from exc
    return result
