"""FastAPI 응답 스키마 (Pydantic v2)."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, ConfigDict


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    corp_code: str
    corp_name: str
    stock_code: Optional[str] = None
    market: Optional[str] = None


class FinancialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    year: int
    quarter: int
    fs_div: str
    # 억원 단위로 변환된 값
    revenue: Optional[float] = None
    operating_profit: Optional[float] = None
    net_income: Optional[float] = None
    total_assets: Optional[float] = None
    total_debt: Optional[float] = None
    total_equity: Optional[float] = None
    operating_cf: Optional[float] = None
    # 판단 플래그
    account_map_confidence: Optional[float] = None
    cfs_ofs_gap_flag: Optional[bool] = None
    accrual_ratio: Optional[float] = None
    op_cf_divergence_flag: Optional[bool] = None
    op_net_divergence_flag: Optional[bool] = None
    equity_negative_flag: Optional[bool] = None
    going_concern_flag: Optional[bool] = None
    revenue_yoy: Optional[float] = None
    revenue_vol_flag: Optional[bool] = None
    amendment_count_annual: Optional[int] = None
    beneish_m_score: Optional[float] = None
    beneish_flag: Optional[bool] = None
    # 파생 지표
    operating_margin: Optional[float] = None
    net_margin: Optional[float] = None
    debt_ratio: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None


class RiskSummaryOut(BaseModel):
    has_data: bool
    cfs_ofs_gap_count: int = 0
    low_map_conf_count: int = 0
    auditor_change_count: int = 0
    non_clean_opinion_count: int = 0
    equity_negative_count: int = 0
    going_concern_count: int = 0
    op_net_divergence_count: int = 0
    beneish_alert_count: int = 0
    latest_beneish_m: Optional[float] = None
    nas_risk_count: int = 0
    revenue_vol_count: int = 0
    op_cf_divergence_count: int = 0
    high_accrual_count: int = 0
    amendment_heavy_count: int = 0


class DisclosureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rcept_no: str
    disc_date: str
    disc_type: str
    report_nm: str
    flr_nm: Optional[str] = None


class AuditorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bsns_year: int
    fs_div: str
    auditor_nm: str
    audit_opinion: Optional[str] = None
    is_auditor_changed: Optional[bool] = None
    consecutive_years: Optional[int] = None


class AuditFeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bsns_year: int
    auditor_nm: Optional[str] = None
    audit_fee_m: Optional[int] = None
    audit_hours: Optional[int] = None
    non_audit_fee_m: Optional[int] = None
    non_audit_hours: Optional[int] = None
    nas_ratio: Optional[float] = None
    independence_risk_flag: Optional[bool] = None
