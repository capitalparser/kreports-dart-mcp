"""Investor quality-of-earnings diagnostics from DART-derived facts."""
from __future__ import annotations

from decimal import Decimal
import math
from statistics import pstdev

from sqlalchemy import bindparam, text

from kreports.analysis.evidence import dart_filing_url, parent_rcept_no
from kreports.analysis.filing_provenance import (
    annual_filing_sources,
    valid_annual_filing_receipt,
)
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


_QOE_PROVENANCE_FIELDS = (
    "amount",
    "unit",
    "period_type",
    "citation_rcept_no",
    "citation_report_nm",
    "citation_basis",
    "quality_status",
    "source_account_id",
    "source_table",
)
_QOE_REQUIRED_METRICS = {
    "revenue",
    "operating_profit",
    "profit_loss",
    "operating_cash_flow",
}
_QOE_DURATION_METRICS = _QOE_REQUIRED_METRICS


def _finite_numeric(value: object) -> bool:
    if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _has_recorded_unit(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _qoe_unproven_source(
    company: str,
    year: int,
    *,
    status: str,
    limitation: str,
) -> dict:
    """Describe a non-citable annual observation without borrowing a filing."""
    return {
        "corp_code": company,
        "report_nm": "DART 연간 재무 데이터",
        "bsns_year": year,
        "rcept_no": None,
        "section_title": "재무제표",
        "source_table": "financial_facts_compact",
        "provenance_status": status,
        "provenance_gap": limitation,
    }


def _qoe_provenance_series(
    company: str,
    start_year: int,
    end_year: int,
    *,
    fs_div: str,
) -> tuple[list[dict], list[dict]]:
    """Admit QoE rows only when every used annual metric has one exact filing.

    This path is intentionally stricter than the historical-series helper used
    by DCF.  A QoE multi-year conclusion must not turn a locally stored value
    or a plausible-looking receipt into a company/year filing citation.
    """
    raw_rows: list[dict] = []
    with _engine_module.engine.connect() as conn:
        compact_columns = {
            row["name"]
            for row in conn.execute(
                text("PRAGMA table_info(financial_facts_compact)")
            ).mappings()
        }
        requested_columns = {"bsns_year", "metric_key", *_QOE_PROVENANCE_FIELDS}
        if not requested_columns.issubset(compact_columns):
            identity_columns = {"corp_code", "bsns_year", "fs_div", "metric_key"}
            if not identity_columns.issubset(compact_columns):
                return [], []
            legacy_rows = conn.execute(text("""
                SELECT DISTINCT bsns_year, metric_key
                FROM financial_facts_compact
                WHERE corp_code=:corp_code
                  AND fs_div=:fs_div
                  AND bsns_year BETWEEN :start_year AND :end_year
                  AND metric_key IN :metric_keys
                ORDER BY bsns_year, metric_key
            """).bindparams(bindparam("metric_keys", expanding=True)), {
                "corp_code": company,
                "fs_div": fs_div,
                "start_year": int(start_year),
                "end_year": int(end_year),
                "metric_keys": tuple(sorted(_QOE_REQUIRED_METRICS)),
            }).mappings()
            legacy_metrics_by_year: dict[int, list[str]] = {}
            for row in legacy_rows:
                legacy_metrics_by_year.setdefault(
                    int(row["bsns_year"]), []
                ).append(str(row["metric_key"]))
            observations: list[dict] = []
            for year, metric_keys in legacy_metrics_by_year.items():
                limitation = (
                    f"{year}년 compact 재무행은 증빙 열이 없어 금액과 QoE "
                    "결론을 공개하지 않았습니다."
                )
                observations.append({
                    "year": year,
                    "available_metric_keys": metric_keys,
                    "units": {},
                    "source": _qoe_unproven_source(
                        company,
                        year,
                        status="compact_provenance_columns_missing",
                        limitation=limitation,
                    ),
                    "provenance_status": "compact_provenance_columns_missing",
                    "limitation": limitation,
                })
            return [], observations
        stmt = text("""
            SELECT bsns_year, metric_key, amount, unit, period_type,
                   citation_rcept_no, citation_report_nm, citation_basis,
                   quality_status, source_account_id, source_table
            FROM financial_facts_compact
            WHERE corp_code=:corp_code
              AND fs_div=:fs_div
              AND bsns_year BETWEEN :start_year AND :end_year
              AND metric_key IN :metric_keys
            ORDER BY bsns_year, metric_key, citation_rcept_no,
                     source_account_id, source_table
        """).bindparams(bindparam("metric_keys", expanding=True))
        raw_rows = [dict(row) for row in conn.execute(stmt, {
            "corp_code": company,
            "fs_div": fs_div,
            "start_year": int(start_year),
            "end_year": int(end_year),
            "metric_keys": tuple(sorted(_QOE_REQUIRED_METRICS)),
        }).mappings()]

    rows_by_year: dict[int, dict[str, list[dict]]] = {}
    for row in raw_rows:
        try:
            year = int(row["bsns_year"])
        except (TypeError, ValueError):
            continue
        metric_key = str(row.get("metric_key") or "")
        if metric_key:
            rows_by_year.setdefault(year, {}).setdefault(metric_key, []).append(row)

    annual_sources = annual_filing_sources(
        company,
        sorted(rows_by_year),
        source_table="financial_facts_compact",
        fs_div=fs_div,
    )
    admitted: list[dict] = []
    observations: list[dict] = []
    for year in sorted(rows_by_year):
        metric_groups = rows_by_year[year]
        source = annual_sources.get(year)
        safe_values: dict[str, object] = {}
        units: dict[str, object] = {}
        statuses: list[str] = []
        missing_metrics = sorted(
            _QOE_REQUIRED_METRICS.difference(metric_groups)
        )
        if missing_metrics:
            statuses.append("compact_required_metrics_missing")
        for metric_key in sorted(metric_groups):
            candidates = metric_groups[metric_key]
            identities = {
                tuple(candidate.get(field) for field in _QOE_PROVENANCE_FIELDS)
                for candidate in candidates
            }
            if len(identities) != 1:
                statuses.append("conflicting_compact_series_rows")
                safe_values[metric_output_key(metric_key)] = None
                units[metric_output_key(metric_key)] = None
                continue
            row = candidates[0]
            output_key = metric_output_key(metric_key)
            amount = row.get("amount")
            safe_values[output_key] = amount if _finite_numeric(amount) else None
            units[output_key] = row.get("unit")
            raw_receipt = str(row.get("citation_rcept_no") or "").strip()
            canonical_receipt = valid_annual_filing_receipt(raw_receipt, year)
            if source is None:
                statuses.append("requested_annual_report_not_cached")
            elif row.get("quality_status") != "usable":
                statuses.append("compact_quality_not_usable")
            elif not _finite_numeric(amount):
                statuses.append("compact_amount_not_finite_numeric")
            elif not _has_recorded_unit(row.get("unit")):
                statuses.append("compact_unit_missing")
            elif (
                metric_key in _QOE_DURATION_METRICS
                and row.get("period_type") != "duration"
            ):
                statuses.append("compact_period_not_duration")
            elif row.get("citation_basis") != "company_year_annual_filing_match":
                statuses.append("compact_citation_basis_unproven")
            elif (
                raw_receipt != canonical_receipt
                or canonical_receipt != source.get("rcept_no")
            ):
                statuses.append("compact_citation_not_exact_annual_filing")

        for numerator_key, denominator_key in (
            ("operating_profit", "revenue"),
            ("operating_cash_flow", "profit_loss"),
        ):
            numerator_unit = units.get(metric_output_key(numerator_key))
            denominator_unit = units.get(metric_output_key(denominator_key))
            if (
                _has_recorded_unit(numerator_unit)
                and _has_recorded_unit(denominator_unit)
                and numerator_unit != denominator_unit
            ):
                statuses.append("compact_ratio_unit_mismatch")

        if statuses:
            status = sorted(set(statuses))[0]
            limitation = (
                f"{year}년 QoE 재무값은 {status} 상태여서 동일 회사·사업연도 "
                "사업보고서 근거의 다년 결론에 사용하지 않았습니다."
            )
            observation_source = _qoe_unproven_source(
                company, year, status=status, limitation=limitation,
            )
        else:
            status = "proven_company_year_annual_filing"
            observation_source = {
                **source,
                "citation_basis": "company_year_annual_filing_match",
            }
            admitted.append({
                "bsns_year": year,
                **safe_values,
                "source": dict(observation_source),
                "units": dict(units),
            })
            limitation = None
        observations.append({
            "year": year,
            **safe_values,
            "units": units,
            "source": observation_source,
            "provenance_status": status,
            **({"missing_metrics": missing_metrics} if missing_metrics else {}),
            **({"limitation": limitation} if limitation else {}),
        })
    return admitted, observations


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
    series, financial_observations = _qoe_provenance_series(
        company, start_year, end_year, fs_div=fs_div,
    )
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
            "source": dict(row.get("source") or _qoe_unproven_source(
                company,
                int(row["bsns_year"]),
                status="compact_citation_unproven_or_conflicting",
                limitation=(
                    f"{int(row['bsns_year'])}년 QoE 재무값의 사업보고서 "
                    "접수번호를 확인하지 못했습니다."
                ),
            )),
            "units": dict(row.get("units") or {}),
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
    provenance_limitations = [
        str(row["limitation"])
        for row in financial_observations
        if row.get("limitation")
    ]
    quality_status = (
        "usable"
        if len(series) >= 3 and not provenance_limitations
        else "limited"
        if financial_observations or matter_summary["section_count"]
        else "missing"
    )
    limitations = [
        "DART 기반 스크리닝 자료이며 투자 권고가 아닙니다.",
        "구조화 재무값에서 일회성 손익이 분리되지 않으면 관련 주석을 추가 검토해야 합니다.",
    ]
    if not series:
        limitations.insert(
            0,
            "요청 기간에 QoE 결론에 사용할 증빙 완료 재무연도는 없지만 연도별 관찰과 감사보고서 matter는 독립적으로 표시합니다.",
        )
    limitations.extend(
        limitation for limitation in provenance_limitations
        if limitation not in limitations
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
        "financial_observations": financial_observations,
        "financial_sources": [
            dict(row["source"])
            for row in financial_observations
            if row.get("provenance_status") == "proven_company_year_annual_filing"
        ],
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
            "limitations": provenance_limitations,
        },
        "limitations": limitations,
    }
