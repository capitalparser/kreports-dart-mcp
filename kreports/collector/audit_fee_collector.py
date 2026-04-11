"""
audit_fee_collector.py — DS002 회계감사용역계약 체결현황 수집.

DART hmvAuditFee.json API를 호출하여 감사보수·비감사보수를 수집하고
NAS ratio(비감사보수/감사보수) 및 독립성 위험 플래그를 계산한다.
"""
import logging
import time
from datetime import datetime, date

from kreports.collector.fetcher import fetch_audit_fee
from kreports.config import settings
from kreports.db.engine import get_session
from kreports.db.models import AuditFee, Company

logger = logging.getLogger(__name__)

# NAS ratio 임계값: 비감사보수가 감사보수를 초과하면 독립성 위험
_NAS_RISK_THRESHOLD = 1.0


def _parse_fee(value: str | None) -> int | None:
    """'1,234' 형식의 문자열을 정수로 변환. '-', '', None → None."""
    if not value:
        return None
    s = str(value).replace(",", "").strip()
    if s in ("", "-", "N/A", "해당없음"):
        return None
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def _extract_current_period(items: list[dict]) -> dict | None:
    """API 응답 list에서 당기(se='당기') 항목을 추출. 없으면 첫 번째 항목."""
    for item in items:
        se = str(item.get("se", "")).strip()
        if "당기" in se:
            return item
    return items[0] if items else None


def collect_audit_fees_for(corp_code: str, years: list[int]) -> dict:
    """
    단일 기업의 특정 연도 목록에 대해 DS002 감사보수를 수집한다.

    Returns:
        {"saved": int, "no_data": int, "error": int}
    """
    saved = 0
    no_data = 0
    error = 0

    for year in years:
        try:
            data = fetch_audit_fee(corp_code, year)
            if data.get("status") != "000" or not data.get("list"):
                no_data += 1
                continue

            item = _extract_current_period(data["list"])
            if item is None:
                no_data += 1
                continue

            auditor_nm = item.get("nm") or item.get("auditor_nm") or None
            audit_fee_m = _parse_fee(item.get("adt_fee"))
            audit_hours = _parse_fee(item.get("adt_time"))
            non_audit_fee_m = _parse_fee(item.get("nadt_fee"))
            non_audit_hours = _parse_fee(item.get("nadt_time"))

            # NAS ratio 계산
            nas_ratio: float | None = None
            independence_risk_flag: bool | None = None
            if audit_fee_m is not None and audit_fee_m > 0 and non_audit_fee_m is not None:
                nas_ratio = round(non_audit_fee_m / audit_fee_m, 4)
                independence_risk_flag = nas_ratio > _NAS_RISK_THRESHOLD

            _upsert_audit_fee(
                corp_code=corp_code,
                bsns_year=year,
                auditor_nm=auditor_nm,
                audit_fee_m=audit_fee_m,
                audit_hours=audit_hours,
                non_audit_fee_m=non_audit_fee_m,
                non_audit_hours=non_audit_hours,
                nas_ratio=nas_ratio,
                independence_risk_flag=independence_risk_flag,
            )
            saved += 1
            logger.debug("감사보수 저장 [%s %d] %s — 보수=%s 백만원, NAS=%.2f",
                         corp_code, year, auditor_nm or "-",
                         audit_fee_m or "-", nas_ratio or 0)

        except Exception as e:
            logger.warning("감사보수 수집 실패 [%s %d]: %s", corp_code, year, e)
            error += 1

        time.sleep(settings.request_delay)

    return {"saved": saved, "no_data": no_data, "error": error}


def _upsert_audit_fee(corp_code: str, bsns_year: int, **kwargs) -> None:
    with get_session() as session:
        existing = (
            session.query(AuditFee)
            .filter_by(corp_code=corp_code, bsns_year=bsns_year)
            .first()
        )
        if existing:
            for k, v in kwargs.items():
                setattr(existing, k, v)
            existing.fetched_at = datetime.utcnow()
        else:
            session.add(AuditFee(
                corp_code=corp_code,
                bsns_year=bsns_year,
                fetched_at=datetime.utcnow(),
                **kwargs,
            ))


def collect_audit_fees(corp_code: str, year_from: int | None = None, year_to: int | None = None) -> dict:
    """단일 기업 감사보수 수집 (연도 범위)."""
    current_year = date.today().year
    y_to = year_to or current_year
    y_from = year_from or (y_to - settings.collect_years + 1)
    years = list(range(y_from, y_to + 1))
    return collect_audit_fees_for(corp_code, years)


def collect_all_audit_fees(
    year_from: int | None = None,
    year_to: int | None = None,
    progress_callback=None,
) -> dict:
    """전체 상장사 감사보수 배치 수집."""
    with get_session() as session:
        companies = (
            session.query(Company.corp_code, Company.corp_name)
            .filter(Company.stock_code.isnot(None))
            .order_by(Company.corp_name)
            .all()
        )

    total = len(companies)
    totals = {"saved": 0, "no_data": 0, "error": 0}

    for idx, (corp_code, corp_name) in enumerate(companies, 1):
        if progress_callback:
            progress_callback(idx, total, corp_name)
        result = collect_audit_fees(corp_code, year_from, year_to)
        for k in totals:
            totals[k] += result[k]

    return totals
