"""Investor quality-of-earnings diagnostics from DART-derived facts."""
from __future__ import annotations

from statistics import pstdev

from sqlalchemy import bindparam, text

from kreports.db.engine import engine


def _safe_div(num: int | float | None, den: int | float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return round(float(num) / float(den), 4)


def _financial_series(company: str, start_year: int, end_year: int, fs_div: str = "CFS") -> list[dict]:
    metric_keys = [
        "revenue",
        "operating_profit",
        "profit_loss",
        "operating_cash_flow",
        "assets",
        "liabilities",
        "equity",
    ]
    stmt = text("""
        SELECT bsns_year, metric_key, amount
        FROM financial_facts_compact
        WHERE corp_code=:corp_code
          AND fs_div=:fs_div
          AND bsns_year BETWEEN :start_year AND :end_year
          AND metric_key IN :metric_keys
        ORDER BY bsns_year, metric_key
    """).bindparams(bindparam("metric_keys", expanding=True))
    by_year: dict[int, dict] = {}
    with engine.connect() as conn:
        for row in conn.execute(stmt, {
            "corp_code": company,
            "fs_div": fs_div,
            "start_year": int(start_year),
            "end_year": int(end_year),
            "metric_keys": metric_keys,
        }).mappings():
            item = by_year.setdefault(int(row["bsns_year"]), {"bsns_year": int(row["bsns_year"])})
            key = row["metric_key"]
            if key == "profit_loss":
                key = "net_income"
            elif key == "operating_cash_flow":
                key = "operating_cf"
            item[key] = row["amount"]
    return [by_year[year] for year in sorted(by_year)]


def _audit_matter_flags(company: str, start_year: int, end_year: int) -> list[dict]:
    if not company:
        return []
    stmt = text("""
        SELECT bsns_year, matter_type, severity_hint, COUNT(*) AS cnt
        FROM audit_matter_items
        WHERE corp_code=:corp_code
          AND bsns_year BETWEEN :start_year AND :end_year
        GROUP BY bsns_year, matter_type, severity_hint
        ORDER BY bsns_year, matter_type
    """)
    try:
        with engine.connect() as conn:
            return [dict(r) for r in conn.execute(stmt, {
                "corp_code": company,
                "start_year": int(start_year),
                "end_year": int(end_year),
            }).mappings()]
    except Exception:
        return []


def quality_of_earnings_pack(
    company: str,
    *,
    start_year: int,
    end_year: int,
    fs_div: str = "CFS",
) -> dict:
    """Return investor-facing quality-of-earnings signals."""
    series = _financial_series(company, start_year, end_year, fs_div=fs_div)
    if not series:
        return {
            "company": company,
            "start_year": int(start_year),
            "end_year": int(end_year),
            "fs_div": fs_div,
            "verdict": "insufficient_data",
            "signals": [],
            "evidence": [],
            "data_quality": {"status": "missing", "source": "financial_facts_compact"},
            "limitations": ["No compact annual financial facts are available for the requested range."],
        }

    evidence: list[dict] = []
    signals: list[dict] = []
    margins: list[float] = []
    negative_ocf_years = 0
    low_cash_conversion_years = 0
    for row in series:
        revenue = row.get("revenue")
        op = row.get("operating_profit")
        ni = row.get("net_income")
        ocf = row.get("operating_cf")
        margin = _safe_div(op, revenue)
        cash_conversion = _safe_div(ocf, ni)
        if margin is not None:
            margins.append(margin)
        if ocf is not None and ocf < 0:
            negative_ocf_years += 1
        if cash_conversion is not None and cash_conversion < 0.7:
            low_cash_conversion_years += 1
        evidence.append({
            "year": row["bsns_year"],
            "revenue": revenue,
            "operating_profit": op,
            "net_income": ni,
            "operating_cf": ocf,
            "operating_margin": margin,
            "cash_conversion": cash_conversion,
        })

    if low_cash_conversion_years:
        signals.append({
            "signal": "low_cash_conversion",
            "severity": "warning",
            "years": low_cash_conversion_years,
            "meaning": "순이익 대비 영업현금흐름 전환율이 낮은 연도가 있습니다.",
        })
    if negative_ocf_years:
        signals.append({
            "signal": "negative_operating_cash_flow",
            "severity": "warning",
            "years": negative_ocf_years,
            "meaning": "영업활동현금흐름이 음수인 연도가 있습니다.",
        })
    margin_volatility = round(pstdev(margins), 4) if len(margins) >= 2 else None
    if margin_volatility is not None and margin_volatility > 0.05:
        signals.append({
            "signal": "volatile_operating_margin",
            "severity": "monitor",
            "value": margin_volatility,
            "meaning": "영업이익률 변동성이 커서 정상화 마진 판단에 주의가 필요합니다.",
        })
    matter_flags = _audit_matter_flags(company, start_year, end_year)
    if any(row.get("severity_hint") in ("high", "warning") for row in matter_flags):
        signals.append({
            "signal": "audit_matter_present",
            "severity": "monitor",
            "count": sum(int(row.get("cnt") or 0) for row in matter_flags),
            "meaning": "감사보고서 강조사항/계속기업/기타사항 문단이 확인됩니다.",
        })

    verdict = "monitor" if signals else "stable"
    return {
        "company": company,
        "start_year": int(start_year),
        "end_year": int(end_year),
        "fs_div": fs_div,
        "verdict": verdict,
        "investment_question": "보고이익이 현금흐름과 반복 가능한 영업성과로 뒷받침되는가?",
        "signals": signals,
        "metrics": {
            "years": len(series),
            "margin_volatility": margin_volatility,
            "low_cash_conversion_years": low_cash_conversion_years,
            "negative_ocf_years": negative_ocf_years,
        },
        "evidence": evidence,
        "audit_matter_flags": matter_flags,
        "data_quality": {
            "status": "usable" if len(series) >= 3 else "limited",
            "source": "financial_facts_compact",
            "year_count": len(series),
        },
        "limitations": [
            "This is a DART-based screening pack, not an investment recommendation.",
            "One-off gains/losses require note-level review when compact facts do not expose them separately.",
        ],
    }
