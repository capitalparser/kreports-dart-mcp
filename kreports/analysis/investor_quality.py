"""Investor quality-of-earnings diagnostics from DART-derived facts."""
from __future__ import annotations

from statistics import pstdev

from sqlalchemy import bindparam, text

from kreports.analysis.evidence import dart_filing_url, parent_rcept_no
from kreports.analysis.filing_provenance import valid_annual_filing_receipt
import kreports.db.engine as _engine_module
from kreports.semantic.metrics import CORE_FINANCIAL_METRICS, metric_output_key


def _safe_div(num: int | float | None, den: int | float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return round(float(num) / float(den), 4)


def _financial_series(
    company: str,
    start_year: int,
    end_year: int,
    fs_div: str = "CFS",
    metric_keys: tuple[str, ...] = CORE_FINANCIAL_METRICS,
    *,
    include_persisted_sources: bool = False,
) -> list[dict]:
    by_year: dict[int, dict] = {}
    citations_by_year: dict[int, set[tuple[object, object, object]]] = {}
    with _engine_module.engine.connect() as conn:
        compact_columns = (
            {
                row["name"]
                for row in conn.execute(
                    text("PRAGMA table_info(financial_facts_compact)")
                ).mappings()
            }
            if include_persisted_sources
            else set()
        )
        has_persisted_provenance = {
            "citation_rcept_no",
            "citation_report_nm",
            "citation_basis",
        }.issubset(compact_columns)
        provenance_select = (
            "citation_rcept_no, citation_report_nm, citation_basis"
            if has_persisted_provenance
            else (
                "NULL AS citation_rcept_no, "
                "NULL AS citation_report_nm, "
                "NULL AS citation_basis"
            )
        )
        stmt = text(f"""
            SELECT bsns_year, metric_key, amount, {provenance_select}
            FROM financial_facts_compact
            WHERE corp_code=:corp_code
              AND fs_div=:fs_div
              AND bsns_year BETWEEN :start_year AND :end_year
              AND metric_key IN :metric_keys
            ORDER BY bsns_year, metric_key
        """).bindparams(bindparam("metric_keys", expanding=True))
        for row in conn.execute(stmt, {
            "corp_code": company,
            "fs_div": fs_div,
            "start_year": int(start_year),
            "end_year": int(end_year),
            "metric_keys": metric_keys,
        }).mappings():
            year = int(row["bsns_year"])
            item = by_year.setdefault(year, {"bsns_year": year})
            item[metric_output_key(row["metric_key"])] = row["amount"]
            if has_persisted_provenance:
                citations_by_year.setdefault(year, set()).add((
                    row.get("citation_rcept_no"),
                    row.get("citation_report_nm"),
                    row.get("citation_basis"),
                ))

    if has_persisted_provenance:
        for year, item in by_year.items():
            citations = citations_by_year.get(year, set())
            if len(citations) == 1:
                receipt, report_nm, basis = next(iter(citations))
            else:
                receipt = report_nm = basis = None
            canonical_receipt = valid_annual_filing_receipt(
                receipt,
                year,
            )
            if (
                canonical_receipt
                and basis == "company_year_annual_filing_match"
            ):
                item["source"] = {
                    "corp_code": company,
                    "report_nm": report_nm,
                    "bsns_year": year,
                    "rcept_no": canonical_receipt,
                    "section_title": "재무제표",
                    "source_table": "financial_facts_compact",
                    "citation_basis": basis,
                }
            else:
                item["source"] = {
                    "corp_code": company,
                    "report_nm": "DART 연간 재무 데이터",
                    "bsns_year": year,
                    "rcept_no": None,
                    "section_title": "재무제표",
                    "source_table": "financial_facts_compact",
                    "citation_basis": basis or "uncitable",
                    "provenance_status": (
                        "compact_citation_unproven_or_conflicting"
                    ),
                    "provenance_gap": (
                        f"{year}년 compact 재무값의 일관된 저장 접수번호를 "
                        "확인하지 못했습니다."
                    ),
                }
    return [by_year[year] for year in sorted(by_year)]


def _normalized_excerpt(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _audit_matter_summary(company: str, start_year: int, end_year: int) -> dict:
    """Group audit-report matters by filing receipt, not by latest financial filing."""
    empty = {
        "unique_receipt_count": 0,
        "section_count": 0,
        "dedupe_basis": "parent_rcept_no + matter_type + normalized_excerpt",
        "groups": [],
    }
    if not company:
        return empty
    stmt = text("""
        SELECT rcept_no, bsns_year, matter_type, severity_hint, matter_text
        FROM audit_matter_items
        WHERE corp_code=:corp_code
          AND bsns_year BETWEEN :start_year AND :end_year
        ORDER BY bsns_year, rcept_no, matter_type, section_ordinal
    """)
    try:
        with _engine_module.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(stmt, {
                "corp_code": company,
                "start_year": int(start_year),
                "end_year": int(end_year),
            }).mappings()]
    except Exception:
        return empty
    grouped: dict[tuple[str | None, str, str], dict] = {}
    for row in rows:
        receipt = parent_rcept_no(str(row.get("rcept_no") or ""))
        excerpt = _normalized_excerpt(str(row.get("matter_text") or ""))
        key = (receipt, str(row.get("matter_type") or ""), excerpt)
        group = grouped.setdefault(key, {
            "year": row.get("bsns_year"),
            "matter_type": row.get("matter_type"),
            "severity": row.get("severity_hint"),
            "excerpt": str(row.get("matter_text") or "")[:500],
            "section_count": 0,
            "source": {
                "rcept_no": receipt,
                "url": dart_filing_url(receipt),
                "source_type": "audit_report",
            },
        })
        group["section_count"] += 1
    groups = list(grouped.values())
    return {
        "unique_receipt_count": len({
            group["source"]["rcept_no"] for group in groups
            if group["source"]["rcept_no"] is not None
        }),
        "section_count": len(rows),
        "dedupe_basis": "parent_rcept_no + matter_type + normalized_excerpt",
        "groups": groups,
    }


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
        with _engine_module.engine.connect() as conn:
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
    matter_summary = _audit_matter_summary(company, start_year, end_year)
    matter_flags = _audit_matter_flags(company, start_year, end_year)
    series = _financial_series(company, start_year, end_year, fs_div=fs_div)

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
    if any(row.get("severity_hint") in ("high", "warning") for row in matter_flags):
        signals.append({
            "signal": "audit_matter_present",
            "severity": "monitor",
            "count": sum(int(row.get("cnt") or 0) for row in matter_flags),
            "meaning": "감사보고서 강조사항/계속기업/기타사항 문단이 확인됩니다.",
        })

    verdict = (
        "monitor"
        if signals
        else "stable"
        if series
        else "insufficient_data"
    )
    quality_status = (
        "usable"
        if len(series) >= 3
        else "limited"
        if series or matter_summary["section_count"]
        else "missing"
    )
    limitations = [
        "This is a DART-based screening pack, not an investment recommendation.",
        "One-off gains/losses require note-level review when compact facts do not expose them separately.",
    ]
    if not series:
        limitations.insert(
            0,
            "요청 기간의 compact 연간 재무 실제값은 없지만 감사보고서 matter는 독립적으로 표시합니다.",
        )
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
        "audit_matter_summary": matter_summary,
        "data_quality": {
            "status": quality_status,
            "source": (
                "financial_facts_compact + audit_matter_items"
                if matter_summary["section_count"]
                else "financial_facts_compact"
            ),
            "year_count": len(series),
        },
        "limitations": limitations,
    }
