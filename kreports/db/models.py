from datetime import datetime, timezone
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, Float, Integer,
    ForeignKey, SmallInteger, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    revision = Column(String(40), primary_key=True)
    checksum = Column(String(64), nullable=False)
    description = Column(String(300), nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=False)


class DatasetManifest(Base):
    __tablename__ = "dataset_manifest"

    manifest_id = Column(String(80), primary_key=True)
    schema_version = Column(String(40), nullable=False)
    dataset_version = Column(String(80), nullable=False)
    generated_at = Column(DateTime(timezone=True), nullable=False)
    year_from = Column(SmallInteger, nullable=True)
    year_to = Column(SmallInteger, nullable=True)
    company_count = Column(Integer, nullable=False)
    disclosure_count = Column(Integer, nullable=False)
    evidence_document_count = Column(Integer, nullable=False)
    quality_snapshot_json = Column(Text, nullable=False, default="{}")
    notes = Column(Text, nullable=True)


class CompanyYearQuality(Base):
    """Factual feature coverage and deterministic product grades by year."""

    __tablename__ = "company_year_quality"

    corp_code = Column(String(8), primary_key=True)
    bsns_year = Column(SmallInteger, primary_key=True)
    market = Column(String(10), nullable=True)
    financial_core_status = Column(String(24), nullable=False)
    auditor_status = Column(String(24), nullable=False)
    audit_fee_status = Column(String(24), nullable=False)
    policy_status = Column(String(24), nullable=False)
    kam_status = Column(String(24), nullable=False)
    audit_procedure_status = Column(String(24), nullable=False)
    group_audit_status = Column(String(24), nullable=False)
    investor_grade = Column(String(1), nullable=False)
    auditor_grade = Column(String(1), nullable=False)
    group_audit_grade = Column(String(1), nullable=False)
    blockers_json = Column(Text, nullable=False, default="[]")
    quality_version = Column(String(20), nullable=False, default="v1")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("idx_company_year_quality_year_market", "bsns_year", "market"),
    )


class Company(Base):
    __tablename__ = "companies"

    corp_code = Column(String(8), primary_key=True)
    stock_code = Column(String(6), unique=True, nullable=True)
    corp_name = Column(String(100), nullable=False)
    market = Column(String(10), nullable=True)         # KOSPI / KOSDAQ / KONEX
    sector = Column(String(10), nullable=True)         # deprecated: induty_code 편법 저장 (backward compat)
    induty_code = Column(String(5), nullable=True)     # KSIC 업종코드 5자리 (통계청 한국표준산업분류)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Financial(Base):
    __tablename__ = "financials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False, index=True)
    year = Column(SmallInteger, nullable=False)
    quarter = Column(SmallInteger, nullable=False)
    fs_div = Column(String(3), nullable=False)         # CFS / OFS
    revenue = Column(BigInteger, nullable=True)
    operating_profit = Column(BigInteger, nullable=True)
    net_income = Column(BigInteger, nullable=True)
    total_assets = Column(BigInteger, nullable=True)
    total_debt = Column(BigInteger, nullable=True)
    total_equity = Column(BigInteger, nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Judge 레이어 — 기본
    account_map_confidence = Column(Float, nullable=True)   # 0~1, 핵심 6개 계정 매핑률
    cfs_ofs_gap_flag = Column(Boolean, nullable=True)       # CFS/OFS 순이익 괴리 ±20% 초과
    accrual_ratio = Column(Float, nullable=True)            # (순이익-영업CF)/|순이익|
    op_cf_divergence_flag = Column(Boolean, nullable=True)  # 영업이익 양수 & 영업CF 음수
    # Judge 레이어 — 단기
    op_net_divergence_flag = Column(Boolean, nullable=True)     # 영업이익>0 & 순이익<0 (일회성 비용)
    equity_negative_flag = Column(Boolean, nullable=True)       # 자본 < 0 (자본잠식)
    going_concern_flag = Column(Boolean, nullable=True)         # 영업이익<0 & 순이익<0 (계속기업 우려)
    revenue_yoy = Column(Float, nullable=True)                  # 전년 동분기 대비 매출 성장률
    revenue_vol_flag = Column(Boolean, nullable=True)           # |매출성장률| ≥ 30%
    amendment_count_annual = Column(SmallInteger, nullable=True) # 해당연도 정정공시 건수
    # Judge 레이어 — 중기
    operating_cf = Column(BigInteger, nullable=True)            # 영업활동현금흐름
    # Judge 레이어 — 장기 (Beneish M-Score)
    beneish_dsri = Column(Float, nullable=True)    # Days Sales Receivable Index
    beneish_gmi = Column(Float, nullable=True)     # Gross Margin Index
    beneish_aqi = Column(Float, nullable=True)     # Asset Quality Index
    beneish_sgi = Column(Float, nullable=True)     # Sales Growth Index
    beneish_depi = Column(Float, nullable=True)    # Depreciation Index
    beneish_sgai = Column(Float, nullable=True)    # SGA Expense Index
    beneish_lvgi = Column(Float, nullable=True)    # Leverage Index
    beneish_tata = Column(Float, nullable=True)    # Total Accruals to Total Assets
    beneish_m_score = Column(Float, nullable=True) # M-Score (-4.84 기준)
    beneish_flag = Column(Boolean, nullable=True)  # M > -1.78 → 이익 조작 가능성
    # 데이터 출처
    # 'acntall' = fnlttSinglAcntAll (XBRL 풀 계정, financial_facts 동반)
    # 'acnt'    = fnlttSinglAcnt 폴백 (6개 요약만, financial_facts 없음)
    source = Column(String(20), nullable=True)

    __table_args__ = (
        UniqueConstraint("corp_code", "year", "quarter", "fs_div", name="uq_financial"),
    )


class Disclosure(Base):
    __tablename__ = "disclosures"

    rcept_no = Column(String(14), primary_key=True)
    corp_code = Column(String(8), nullable=False, index=True)
    corp_name = Column(String(100), nullable=False)
    disc_date = Column(Date, nullable=False)
    disc_type = Column(String(1), nullable=False)
    report_nm = Column(String(300), nullable=False)
    flr_nm = Column(String(100), nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_disc_corp_date", "corp_code", "disc_date"),
    )


class Auditor(Base):
    __tablename__ = "auditors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    fs_div = Column(String(3), nullable=False)          # CFS / OFS
    auditor_nm = Column(String(100), nullable=False)
    audit_opinion = Column(String(20), nullable=True)   # 적정/한정/부적정/의견거절
    rcept_no = Column(String(14), nullable=True)
    is_auditor_changed = Column(Boolean, nullable=True)
    consecutive_years = Column(SmallInteger, nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("corp_code", "bsns_year", "fs_div", name="uq_auditor"),
        Index("idx_auditors_corp_year", "corp_code", "bsns_year"),
    )


class FinancialFact(Base):
    """
    XBRL 구조 기반 전체 계정과목 저장.
    fnlttSinglAcntAll 응답의 모든 행을 저장한다 (상장사 대상).
    account_id = XBRL element ID (예: ifrs-full_Revenue).
    비상장사는 Financial(6개 요약)만 사용.
    """
    __tablename__ = "financial_facts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    reprt_code = Column(String(6), nullable=False)    # 11013/11012/11014/11011
    fs_div = Column(String(3), nullable=False)         # CFS / OFS
    sj_div = Column(String(10), nullable=False)        # BS/IS/CIS/CF/SCE
    account_id = Column(String(200), nullable=False)   # XBRL element (synthetic if missing)
    account_nm = Column(String(300), nullable=False)   # 한글 계정명
    ord = Column(SmallInteger, nullable=True)           # 표시 순서 (들여쓰기 레벨)
    thstrm_amount = Column(BigInteger, nullable=True)        # 당기 누적
    frmtrm_amount = Column(BigInteger, nullable=True)        # 전기 누적
    bfefrmtrm_amount = Column(BigInteger, nullable=True)     # 전전기 누적
    thstrm_add_amount = Column(BigInteger, nullable=True)    # 당분기 (비누적, 분기보고서)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div", "account_id",
            name="uq_financial_fact",
        ),
        Index("idx_fact_corp_year", "corp_code", "bsns_year"),
        Index("idx_fact_sj", "corp_code", "bsns_year", "fs_div", "sj_div"),
    )


class FinancialFactCompact(Base):
    """Small annual metric subset used by deployable runtime DBs."""
    __tablename__ = "financial_facts_compact"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    fs_div = Column(String(3), nullable=False)
    metric_key = Column(String(50), nullable=False)
    metric_name = Column(String(200), nullable=False)
    amount = Column(BigInteger, nullable=True)
    source_account_id = Column(String(200), nullable=True)
    source_account_nm = Column(String(300), nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "corp_code", "bsns_year", "fs_div", "metric_key",
            name="uq_financial_facts_compact",
        ),
        Index("idx_fin_compact_corp_year", "corp_code", "bsns_year"),
        Index("idx_fin_compact_metric", "metric_key"),
    )


class AuditFee(Base):
    """Source- and period-aware audit fee/hour compatibility record."""
    __tablename__ = "audit_fees"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    auditor_nm = Column(String(100), nullable=True)      # 감사인명
    audit_fee_m = Column(Integer, nullable=True)          # 감사보수 (백만원)
    audit_hours = Column(Integer, nullable=True)          # 감사시간 (시간)
    contract_fee_m = Column(Integer, nullable=True)
    contract_hours = Column(Integer, nullable=True)
    actual_fee_m = Column(Integer, nullable=True)
    actual_hours = Column(Integer, nullable=True)
    source_class = Column(String(40), nullable=True)
    source_rcept_no = Column(String(80), nullable=True)
    source_period = Column(String(80), nullable=True)
    availability_status = Column(String(40), nullable=True)
    quality_status = Column(String(24), nullable=True)
    compatibility_basis = Column(String(40), nullable=True)
    conflict_status = Column(String(24), nullable=True)
    source_observations_json = Column(Text, nullable=True)
    non_audit_fee_m = Column(Integer, nullable=True)      # 비감사보수 (백만원)
    non_audit_hours = Column(Integer, nullable=True)      # 비감사시간 (시간)
    nas_ratio = Column(Float, nullable=True)              # 비감사보수/감사보수
    independence_risk_flag = Column(Boolean, nullable=True)  # NAS ratio > 1.0
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("corp_code", "bsns_year", name="uq_audit_fee"),
        Index("idx_audit_fee_corp", "corp_code"),
        Index(
            "idx_audit_fee_availability_year",
            "bsns_year",
            "availability_status",
        ),
    )


class AccountingPolicyItem(Base):
    """
    사업보고서 주석에서 추출한 회계정책 항목 (item_key 단위 영속화).

    dashboard.db._extract_policy_items_from_xml의 결과를 여기 저장하여
    - 연도별 정책 텍스트 변화 추적
    - 업종 내 동일 item_key 비교
    - 대시보드/MCP에서 즉시 조회 (사업보고서 ZIP 재파싱 없이)
    를 가능하게 한다.
    """
    __tablename__ = "accounting_policy_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    fs_div = Column(String(3), nullable=False)           # CFS / OFS
    rcept_no = Column(String(14), nullable=False)        # 원본 사업보고서
    item_key = Column(String(50), nullable=False)        # revenue_recognition 등
    heading = Column(String(500), nullable=True)         # "2-2-11 유형자산"
    body = Column(Text, nullable=False)                  # 본문 전체 (원문 텍스트)
    body_hash = Column(String(40), nullable=True)        # sha1 (정책 변화 감지용)
    body_length = Column(Integer, nullable=True)         # 문자 수 (요약용)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "corp_code", "bsns_year", "fs_div", "item_key",
            name="uq_policy_item",
        ),
        Index("idx_policy_item_corp_year", "corp_code", "bsns_year"),
        Index("idx_policy_item_key", "item_key"),
    )


class AccountingNoteChapter(Base):
    """Financial statement note chapters extracted from annual report packages.

    This stores the chapter-level evidence around basis of preparation,
    significant accounting policies, and significant estimates/judgments. Topic
    level `AccountingPolicyItem` rows can be derived from this broader source.
    """
    __tablename__ = "accounting_note_chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    fs_div = Column(String(3), nullable=False)          # CFS / OFS
    rcept_no = Column(String(14), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    source_type = Column(String(30), nullable=False, default="business_report")
    note_no = Column(String(20), nullable=False)        # "2", "3", "4", etc.
    note_title = Column(String(500), nullable=True)
    section_type = Column(String(40), nullable=False)   # basis / policy / estimate_judgment / other_note
    body = Column(Text, nullable=False)
    body_hash = Column(String(40), nullable=True)
    body_length = Column(Integer, nullable=True)
    full_text_uri = Column(String(500), nullable=True)
    full_text_hash = Column(String(40), nullable=True)
    full_text_length = Column(Integer, nullable=True)
    full_text_compressed_length = Column(Integer, nullable=True)
    full_text_storage_status = Column(String(30), nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "corp_code", "bsns_year", "fs_div", "note_no", "section_type",
            name="uq_accounting_note_chapter",
        ),
        Index("idx_note_chapter_corp_year", "corp_code", "bsns_year", "fs_div"),
        Index("idx_note_chapter_section_type", "section_type"),
        Index("idx_note_chapter_full_text_uri", "full_text_uri"),
    )


class ReportDocument(Base):
    """Downloaded DART report document metadata.

    This separates disclosure-list coverage from actual document body coverage.
    A disclosure row means DART listed the filing; this row means we fetched and
    parsed its document.xml payload.
    """
    __tablename__ = "report_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(14), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    source_type = Column(String(30), nullable=False)  # business_report / audit_report
    report_nm = Column(String(300), nullable=False)
    doc_hash = Column(String(40), nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rcept_no", "source_type", name="uq_report_document"),
        Index("idx_report_doc_corp_year", "corp_code", "bsns_year", "source_type"),
    )


class SourceDocument(Base):
    """Raw disclosure document cache used as the source for extractors."""
    __tablename__ = "source_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(80), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    source_type = Column(String(30), nullable=False)  # business_report / audit_report
    report_nm = Column(String(300), nullable=False)
    content_type = Column(String(30), nullable=False, default="xml")
    raw_content = Column(Text, nullable=False)
    doc_hash = Column(String(40), nullable=False)
    storage_uri = Column(String(500), nullable=True)
    content_length = Column(Integer, nullable=True)
    compressed_length = Column(Integer, nullable=True)
    storage_status = Column(String(30), nullable=False, default="inline")
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rcept_no", "source_type", name="uq_source_document"),
        Index("idx_source_doc_corp_year", "corp_code", "bsns_year", "source_type"),
        Index("idx_source_doc_hash", "doc_hash"),
        Index("idx_source_doc_storage_status", "storage_status"),
        Index("idx_source_doc_storage_uri", "storage_uri"),
    )


class ExtractionRun(Base):
    """Extractor execution history for raw source documents."""
    __tablename__ = "extraction_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_document_id = Column(Integer, nullable=True)
    rcept_no = Column(String(80), nullable=False)
    source_type = Column(String(30), nullable=False)
    extractor_name = Column(String(80), nullable=False)
    extractor_version = Column(String(30), nullable=False, default="v1")
    source_doc_hash = Column(String(40), nullable=True)
    status = Column(String(20), nullable=False)  # success / error / skipped
    rows_written = Column(Integer, nullable=False, default=0)
    error_msg = Column(Text, nullable=True)
    extracted_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_extraction_runs_doc", "rcept_no", "source_type"),
        Index("idx_extraction_runs_extractor", "extractor_name", "status"),
    )


class ReportSection(Base):
    """Normalized sections extracted from business/audit report bodies."""
    __tablename__ = "report_sections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(14), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    source_type = Column(String(30), nullable=False)  # business_report / audit_report
    section_key = Column(String(50), nullable=False)  # audit_opinion / kam / emphasis ...
    section_title = Column(String(300), nullable=True)
    body_text = Column(Text, nullable=False)
    body_hash = Column(String(40), nullable=True)
    body_length = Column(Integer, nullable=True)
    full_text_uri = Column(String(500), nullable=True)
    full_text_hash = Column(String(40), nullable=True)
    full_text_length = Column(Integer, nullable=True)
    full_text_compressed_length = Column(Integer, nullable=True)
    full_text_storage_status = Column(String(30), nullable=True)
    ordinal = Column(SmallInteger, nullable=False, default=0)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rcept_no", "source_type", "section_key", "ordinal", name="uq_report_section"),
        Index("idx_report_section_corp_year", "corp_code", "bsns_year", "source_type"),
        Index("idx_report_section_key", "source_type", "section_key"),
        Index("idx_report_section_full_text_uri", "full_text_uri"),
    )


class KamItem(Base):
    """Matter-level KAM reconstructed from the best cached source body."""
    __tablename__ = "kam_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(80), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    source_type = Column(String(30), nullable=False)
    ordinal = Column(SmallInteger, nullable=False)
    title = Column(String(500), nullable=True)
    normalized_topic = Column(String(80), nullable=True)
    reason_text = Column(Text, nullable=True)
    audit_response_text = Column(Text, nullable=True)
    related_note_references_json = Column(Text, nullable=False, default="[]")
    full_body_hash = Column(String(40), nullable=False)
    full_body_length = Column(Integer, nullable=False, default=0)
    source_basis = Column(String(80), nullable=False)
    parser_version = Column(String(30), nullable=False, default="v1")
    quality_status = Column(String(20), nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "rcept_no",
            "source_type",
            "ordinal",
            "full_body_hash",
            name="uq_kam_item_source_ordinal_body",
        ),
        Index("idx_kam_item_corp_year", "corp_code", "bsns_year"),
        Index("idx_kam_item_quality_year", "bsns_year", "quality_status"),
        Index("idx_kam_item_receipt", "rcept_no", "source_type"),
    )


class EvidenceDocument(Base):
    """Markdown-like evidence bundle derived from normalized report tables.

    This is not the legal filing source. It is a compact, human-readable cache
    for MCP search and narrative responses, with raw source hashes kept in
    source_documents for re-parsing and verification.
    """
    __tablename__ = "evidence_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    source_type = Column(String(30), nullable=False)  # business_report / audit_report / mixed
    rcept_no = Column(String(80), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    evidence_scope = Column(String(40), nullable=False, default="auditor_view")
    title = Column(String(500), nullable=True)
    normalized_text = Column(Text, nullable=False)
    text_hash = Column(String(40), nullable=True)
    text_length = Column(Integer, nullable=True)
    full_text_uri = Column(String(500), nullable=True)
    full_text_hash = Column(String(40), nullable=True)
    full_text_length = Column(Integer, nullable=True)
    full_text_compressed_length = Column(Integer, nullable=True)
    full_text_storage_status = Column(String(30), nullable=True)
    source_count = Column(Integer, nullable=False, default=0)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "corp_code", "bsns_year", "source_type", "rcept_no", "evidence_scope",
            name="uq_evidence_document",
        ),
        Index("idx_evidence_doc_corp_year", "corp_code", "bsns_year", "source_type"),
        Index("idx_evidence_doc_scope", "evidence_scope"),
        Index("idx_evidence_doc_full_text_uri", "full_text_uri"),
    )


class AuditProcedureItem(Base):
    """Procedure-level evidence parsed from KAM audit-response paragraphs."""
    __tablename__ = "audit_procedure_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(80), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    source_type = Column(String(30), nullable=False)
    kam_item_id = Column(
        Integer,
        ForeignKey("kam_items.id"),
        nullable=True,
    )
    kam_topic = Column(String(50), nullable=True)
    method = Column(String(50), nullable=True)
    procedure_type = Column(String(50), nullable=False)
    procedure_text = Column(Text, nullable=False)
    procedure_hash = Column(String(40), nullable=True)
    procedure_length = Column(Integer, nullable=True)
    assertion_hints_json = Column(Text, nullable=True)
    linked_metric_keys_json = Column(Text, nullable=True)
    linked_note_keys_json = Column(Text, nullable=True)
    linked_event_keys_json = Column(Text, nullable=True)
    parser_version = Column(String(30), nullable=True)
    quality_status = Column(String(20), nullable=True)
    section_ordinal = Column(SmallInteger, nullable=False, default=0)
    procedure_ordinal = Column(SmallInteger, nullable=False, default=0)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "rcept_no", "source_type", "section_ordinal", "procedure_ordinal",
            name="uq_audit_procedure_item",
        ),
        Index("idx_audit_procedure_corp_year", "corp_code", "bsns_year"),
        Index("idx_audit_procedure_type", "procedure_type"),
        Index("idx_audit_procedure_topic", "kam_topic"),
        Index("idx_audit_procedure_kam_item", "kam_item_id"),
        Index("idx_audit_procedure_method_year", "method", "bsns_year"),
    )


class AuditMatterItem(Base):
    """Structured non-KAM audit-report matter for search and peer comparison."""
    __tablename__ = "audit_matter_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(80), nullable=False)
    dcm_no = Column(String(20), nullable=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    matter_type = Column(String(50), nullable=False)
    matter_title = Column(String(300), nullable=True)
    matter_text = Column(Text, nullable=False)
    matter_hash = Column(String(40), nullable=True)
    matter_length = Column(Integer, nullable=True)
    topic_tags = Column(Text, nullable=False, default="[]")
    severity_hint = Column(String(20), nullable=False, default="info")
    source_type = Column(String(30), nullable=False, default="audit_report")
    section_ordinal = Column(SmallInteger, nullable=False, default=0)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rcept_no", "matter_type", "section_ordinal", name="uq_audit_matter_item"),
        Index("idx_audit_matter_corp_year", "corp_code", "bsns_year"),
        Index("idx_audit_matter_type_year", "matter_type", "bsns_year"),
        Index("idx_audit_matter_severity", "severity_hint"),
    )


class BusinessAffiliateAuditor(Base):
    """Subsidiary/equity affiliate auditor matrix cached from business reports."""
    __tablename__ = "subsidiary_auditor_matrix"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_corp_code = Column(String(8), nullable=False)
    parent_rcept_no = Column(String(14), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    name = Column(String(300), nullable=False)
    relation = Column(String(30), nullable=True)
    ownership_pct = Column(Float, nullable=True)
    listed_yn = Column(String(20), nullable=True)
    business = Column(Text, nullable=True)
    assets = Column(Text, nullable=True)
    source = Column(String(50), nullable=True)
    corp_code = Column(String(8), nullable=True)
    stock_code = Column(String(6), nullable=True)
    market = Column(String(10), nullable=True)
    auditor_nm = Column(String(100), nullable=True)
    audit_opinion = Column(String(20), nullable=True)
    auditor_fs_div = Column(String(3), nullable=True)
    auditor_year = Column(SmallInteger, nullable=True)
    ordinal = Column(SmallInteger, nullable=False, default=0)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("parent_rcept_no", "name", name="uq_subsidiary_auditor_matrix"),
        Index("idx_subsidiary_matrix_parent_year", "parent_corp_code", "bsns_year"),
    )


class GroupEntityRecord(Base):
    """Receipt-bound entity identity used by the group-audit graph."""
    __tablename__ = "group_entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_corp_code = Column(String(8), nullable=False)
    effective_year = Column(SmallInteger, nullable=False)
    entity_key = Column(String(120), nullable=False)
    original_name = Column(String(300), nullable=False)
    normalized_name = Column(String(300), nullable=False)
    resolved_corp_code = Column(String(8), nullable=True)
    stock_code = Column(String(6), nullable=True)
    market = Column(String(10), nullable=True)
    resolution_status = Column(String(24), nullable=False)
    resolution_reason = Column(String(80), nullable=False)
    listed_state = Column(String(20), nullable=True)
    component_auditor_name = Column(String(100), nullable=True)
    component_auditor_year = Column(SmallInteger, nullable=True)
    component_auditor_rcept_no = Column(String(80), nullable=True)
    component_auditor_fs_div = Column(String(3), nullable=True)
    auditor_gap_reason = Column(String(80), nullable=True)
    source_rcept_no = Column(String(80), nullable=False)
    source_table = Column(String(80), nullable=False)
    source_ordinal = Column(SmallInteger, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "parent_corp_code", "effective_year", "source_rcept_no",
            "source_table", "source_ordinal", "entity_key",
            name="uq_group_entity_source",
        ),
        Index("idx_group_entity_parent_year", "parent_corp_code", "effective_year"),
        Index("idx_group_entity_resolved_year", "resolved_corp_code", "effective_year"),
    )


class GroupRelationshipRecord(Base):
    """Direct, disclosed group relationship with source identity."""
    __tablename__ = "group_relationships"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_corp_code = Column(String(8), nullable=False)
    effective_year = Column(SmallInteger, nullable=False)
    relationship_key = Column(String(160), nullable=False)
    parent_entity_key = Column(String(120), nullable=False)
    child_entity_key = Column(String(120), nullable=False)
    relation_type = Column(String(40), nullable=False)
    ownership_pct = Column(Float, nullable=True)
    source_rcept_no = Column(String(80), nullable=False)
    source_table = Column(String(80), nullable=False)
    source_ordinal = Column(SmallInteger, nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "parent_corp_code", "effective_year", "source_rcept_no",
            "source_table", "source_ordinal", "relationship_key",
            name="uq_group_relationship_source",
        ),
        Index("idx_group_relationship_parent_year", "parent_corp_code", "effective_year"),
        Index("idx_group_relationship_nodes", "parent_entity_key", "child_entity_key"),
    )


class GroupComponentMetricRecord(Base):
    """Component amount, denominator and QSC decision provenance."""
    __tablename__ = "group_component_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_corp_code = Column(String(8), nullable=False)
    effective_year = Column(SmallInteger, nullable=False)
    metric_identity = Column(String(180), nullable=False)
    source_rcept_no = Column(String(80), nullable=False)
    entity_key = Column(String(120), nullable=False)
    metric_key = Column(String(40), nullable=False)
    amount = Column(Float, nullable=True)
    unit = Column(String(30), nullable=True)
    numerator_source_rcept_no = Column(String(80), nullable=True)
    numerator_source_table = Column(String(80), nullable=True)
    denominator_amount = Column(Float, nullable=True)
    denominator_unit = Column(String(30), nullable=True)
    denominator_source_rcept_no = Column(String(80), nullable=True)
    denominator_source_table = Column(String(80), nullable=True)
    fs_div = Column(String(3), nullable=True)
    period = Column(String(40), nullable=True)
    elimination_basis = Column(String(40), nullable=True)
    share_pct = Column(Float, nullable=True)
    qsc_status = Column(String(20), nullable=False)
    qsc_basis = Column(String(200), nullable=False, default="")
    qsc_evidence_refs_json = Column(Text, nullable=False, default="[]")
    qsc_threshold_pct = Column(Float, nullable=False, default=10.0)
    quality_status = Column(String(24), nullable=False)
    gap_reason = Column(String(80), nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "parent_corp_code", "effective_year", "source_rcept_no",
            "metric_identity",
            name="uq_group_metric_source",
        ),
        Index("idx_group_metric_parent_year", "parent_corp_code", "effective_year"),
        Index("idx_group_metric_entity_kind", "entity_key", "metric_key"),
        Index("idx_group_metric_qsc_year", "effective_year", "qsc_status"),
    )


class FetchLog(Base):
    __tablename__ = "fetch_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(20), nullable=False)     # financial / disclosure / auditor
    corp_code = Column(String(8), nullable=True)
    year = Column(SmallInteger, nullable=True)
    quarter = Column(SmallInteger, nullable=True)
    status = Column(String(10), nullable=False)        # success / error / no_data / skipped
    error_msg = Column(Text, nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_fetchlog_status", "status", "fetched_at"),
        Index("idx_fetchlog_task_target_status", "task_type", "corp_code", "year", "quarter", "status"),
    )


class BackfillRun(Base):
    """Batch backfill execution ledger and concurrency guard."""
    __tablename__ = "backfill_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(50), nullable=False)
    year = Column(SmallInteger, nullable=True)
    market = Column(String(10), nullable=True)
    status = Column(String(20), nullable=False)  # running / success / error
    pid = Column(Integer, nullable=True)
    lease_key = Column(String(160), nullable=True)
    owner_token = Column(String(64), nullable=True)
    owner_host = Column(String(255), nullable=True)
    owner_process_start = Column(String(100), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    checkpoint_json = Column(Text, nullable=False, default="{}")
    attempted_count = Column(Integer, nullable=False, default=0)
    saved_count = Column(Integer, nullable=False, default=0)
    no_data_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)
    params_json = Column(Text, nullable=True)
    summary_json = Column(Text, nullable=True)
    error_msg = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_backfill_runs_key_status", "task_type", "year", "market", "status"),
        Index("idx_backfill_runs_started", "started_at"),
        Index(
            "uq_backfill_runs_active_lease",
            "lease_key",
            unique=True,
            sqlite_where=(status == "running"),
        ),
    )


class DisclosureEvent(Base):
    """Investor/auditor-relevant event indexed from DART disclosure titles."""
    __tablename__ = "disclosure_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rcept_no = Column(String(14), nullable=False)
    corp_code = Column(String(8), nullable=False)
    event_date = Column(DateTime, nullable=False)
    event_type = Column(String(50), nullable=False)
    event_title = Column(String(500), nullable=False)
    severity_hint = Column(String(20), nullable=False, default="info")
    source_report_nm = Column(String(500), nullable=False)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("rcept_no", "event_type", name="uq_disclosure_event"),
        Index("idx_disclosure_event_corp_date", "corp_code", "event_date"),
        Index("idx_disclosure_event_type_date", "event_type", "event_date"),
    )
