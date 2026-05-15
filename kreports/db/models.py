from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, Float, Integer,
    SmallInteger, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


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


class AuditFee(Base):
    """DS002 회계감사용역계약 체결현황 (사업보고서 기준)."""
    __tablename__ = "audit_fees"

    id = Column(Integer, primary_key=True, autoincrement=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    auditor_nm = Column(String(100), nullable=True)      # 감사인명
    audit_fee_m = Column(Integer, nullable=True)          # 감사보수 (백만원)
    audit_hours = Column(Integer, nullable=True)          # 감사시간 (시간)
    non_audit_fee_m = Column(Integer, nullable=True)      # 비감사보수 (백만원)
    non_audit_hours = Column(Integer, nullable=True)      # 비감사시간 (시간)
    nas_ratio = Column(Float, nullable=True)              # 비감사보수/감사보수
    independence_risk_flag = Column(Boolean, nullable=True)  # NAS ratio > 1.0
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("corp_code", "bsns_year", name="uq_audit_fee"),
        Index("idx_audit_fee_corp", "corp_code"),
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
    )
