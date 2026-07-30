"""Read-only, evidence-bounded preparation for audit materiality inputs.

This module prepares transparent benchmark observations.  It deliberately does
not choose a materiality benchmark, rate, or audit conclusion.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from statistics import median
from typing import Any, Iterable

from sqlalchemy import bindparam, inspect, text

import kreports.db.engine as _engine_module
from kreports.analysis.evidence import parent_rcept_no
from kreports.analysis.filing_provenance import (
    annual_filing_sources,
    valid_annual_filing_receipt,
)


METHODOLOGY_VERSION = "kreports-materiality-methodology-2026.07"
_CANONICAL_METRICS = ("profit_before_tax", "revenue", "assets", "equity")
_METRIC_LABELS = {
    "profit_before_tax": "법인세차감전순이익",
    "revenue": "매출액",
    "assets": "자산총계",
    "equity": "자본총계",
}
_RATES = {
    "profit_before_tax": (Decimal("0.03"), Decimal("0.05"), Decimal("0.10")),
    "revenue": (Decimal("0.005"), Decimal("0.0075"), Decimal("0.010")),
    "assets": (Decimal("0.005"), Decimal("0.0075"), Decimal("0.010")),
    "equity": (Decimal("0.010"), Decimal("0.015"), Decimal("0.020")),
}
_RATE_REFERENCES = {
    # ISA 320 A8's 5% PBT example applies only to that one illustrated rate.
    # Every other rate is a transparent KReports internal-methodology input.
    "profit_before_tax": {
        "lower": ("materiality_candidate_ranges_v1",),
        "central": ("isa_320_a8_pbt_illustration",),
        "upper": ("materiality_candidate_ranges_v1",),
    },
    "revenue": {rate: ("materiality_candidate_ranges_v1",) for rate in ("lower", "central", "upper")},
    "assets": {rate: ("materiality_candidate_ranges_v1",) for rate in ("lower", "central", "upper")},
    "equity": {rate: ("materiality_candidate_ranges_v1",) for rate in ("lower", "central", "upper")},
}
_VOLATILITY_RULE = {
    "reference_id": "materiality_stability_registry_v1",
    "low_cv_max": Decimal("0.15"),
    "moderate_cv_max": Decimal("0.50"),
    "high_relative_year_over_year_change": Decimal("0.50"),
    "rule": "CV and maximum relative year-over-year change are descriptive internal thresholds, not ISA thresholds.",
}
_ANNUAL_FILING_CITATION_BASIS = "company_year_annual_filing_match"
_SERIES_VALUE_AND_PROVENANCE_FIELDS = (
    "amount",
    "unit",
    "period_type",
    "quality_status",
    "citation_rcept_no",
    "citation_report_nm",
    "citation_basis",
)


def methodology_references() -> list[dict[str, Any]]:
    """Return versioned sources and clearly scoped KReports methodology rules."""
    return [
        {
            "reference_id": "isa_320_a4_a6_volatility",
            "authority_level": "authoritative_standard",
            "issuer": "AUASB",
            "jurisdiction": "Australia",
            "standard_code": "ASA 320 (conforms with ISA 320)",
            "document_title": "ASA 320 Materiality in Planning and Performing an Audit",
            "paragraphs": "A4-A6",
            "effective_from": "2009-12-15",
            "official_url": "https://standards.auasb.gov.au/asa-320-dec-2015",
            "application_note_ko": "AUASB ASA 320(ISA 320 준거)의 상대적 변동성, 예외적 변동 및 정규화 이익 검토 근거이며 KReports 변동성 구간을 정하지 않습니다.",
            "registry_version_date": "2026-07-31",
        },
        {
            "reference_id": "isa_320_a8_pbt_illustration",
            "authority_level": "standard_illustration",
            "issuer": "AUASB",
            "jurisdiction": "Australia",
            "standard_code": "ASA 320 (conforms with ISA 320)",
            "document_title": "ASA 320 Materiality in Planning and Performing an Audit",
            "paragraphs": "A8",
            "effective_from": "2009-12-15",
            "official_url": "https://standards.auasb.gov.au/asa-320-dec-2015",
            "application_note_ko": "AUASB ASA 320(ISA 320 준거)의 계속영업 법인세차감전이익 5% 예시는 설명용이며 고정 의무 비율이 아닙니다.",
            "registry_version_date": "2026-07-31",
        },
        {
            "reference_id": "isa_320_a8_revenue_illustration",
            "authority_level": "standard_illustration",
            "issuer": "AUASB",
            "jurisdiction": "Australia",
            "standard_code": "ASA 320 (conforms with ISA 320)",
            "document_title": "ASA 320 Materiality in Planning and Performing an Audit",
            "paragraphs": "A8",
            "effective_from": "2009-12-15",
            "official_url": "https://standards.auasb.gov.au/asa-320-dec-2015",
            "application_note_ko": "AUASB ASA 320(ISA 320 준거)의 비영리 조직 수익 또는 비용 1% 예시는 설명용이며 일반화된 고정 비율이 아닙니다.",
            "registry_version_date": "2026-07-31",
        },
        {
            "reference_id": "materiality_candidate_ranges_v1",
            "authority_level": "internal_methodology",
            "issuer": "KReports",
            "jurisdiction": "Korea",
            "standard_code": "KREPORTS-MAT-RANGE-1",
            "document_title": "Materiality candidate range registry",
            "paragraphs": "candidate ranges",
            "effective_from": "2026-07-31",
            "source_locator": "docs/data-contract.md#audit-materiality-preparation",
            "methodology_version": METHODOLOGY_VERSION,
            "application_note_ko": "후보 범위는 KReports 내부 방법론의 투명한 계산 입력이며 감사기준서상 의무 비율이 아닙니다.",
            "registry_version_date": "2026-07-31",
        },
        {
            "reference_id": "materiality_stability_registry_v1",
            "authority_level": "internal_methodology",
            "issuer": "KReports",
            "jurisdiction": "Korea",
            "standard_code": "KREPORTS-MAT-STABILITY-1",
            "document_title": "Materiality benchmark stability registry",
            "paragraphs": "transparent observations",
            "effective_from": "2026-07-31",
            "source_locator": "docs/data-contract.md#audit-materiality-preparation",
            "methodology_version": METHODOLOGY_VERSION,
            "application_note_ko": "3개년 미만은 안정성 결론을 내리지 않으며, CV 및 상대 전년대비 변동이 큰 계열은 단독 기준 후보에서 제외합니다.",
            "registry_version_date": "2026-07-31",
        },
    ]


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return value if value.is_finite() else None


def _source(row: dict[str, Any], *, operand_metric: str | None = None) -> dict[str, Any] | None:
    receipt = row.get("citation_rcept_no")
    if not receipt:
        return None
    source = {
        "source_label": "DART 사업보고서 재무사실",
        "rcept_no": str(receipt),
        "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        "report_nm": row.get("citation_report_nm"),
        "provenance_status": "receipt_proven",
    }
    if operand_metric:
        source["operand_metric"] = operand_metric
    return source


def _observation(metric: str, year: int, row: dict[str, Any] | None, *, basis: str, sources: list[dict[str, Any]] | None = None, limitations: list[str] | None = None, rejected_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if row is None:
        return {
            "year": year,
            "amount": None,
            "basis": "limited" if limitations else "missing",
            "sources": [],
            "limitations": list(limitations or ["cache_missing_not_filing_absence"]),
            "rejected_rows": list(rejected_rows or []),
        }
    value = _decimal(row.get("amount"))
    source = _source(row)
    valid = (
        value is not None
        and row.get("unit") == "KRW"
        and row.get("quality_status") == "usable"
        and source is not None
    )
    result = {
        "year": year,
        "amount": value if valid else None,
        "basis": basis if valid else "limited",
        "sources": sources if sources is not None else ([source] if source else []),
        "source": source,
        "unit": row.get("unit"),
        "period_type": row.get("period_type"),
        "limitations": list(limitations or []),
    }
    if value is None:
        result["limitations"].append("non_numeric_amount")
    if row.get("unit") != "KRW":
        result["limitations"].append("missing_or_incompatible_unit")
    if row.get("quality_status") != "usable":
        result["limitations"].append("limited_source_quality")
    if source is None:
        result["limitations"].append("filing_provenance_unproven")
    return result


def _series_value_and_provenance_identity(row: dict[str, Any]) -> tuple[Any, ...]:
    """Return the fields that must agree before one compact series is chosen."""
    return tuple(row.get(field) for field in _SERIES_VALUE_AND_PROVENANCE_FIELDS)


def _rejected_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep bounded provenance diagnostics without exposing rejected money."""
    return {
        "metric_key": row.get("metric_key"),
        "bsns_year": row.get("bsns_year"),
        "fs_div": row.get("fs_div"),
        "citation_rcept_no": row.get("citation_rcept_no"),
        "citation_basis": row.get("citation_basis"),
    }


def _admission_limitations(
    row: dict[str, Any],
    *,
    annual_sources: dict[int, dict[str, Any]] | None,
) -> list[str]:
    """Require a compact citation to prove, then match, annual-filing identity."""
    receipt_raw = row.get("citation_rcept_no")
    receipt = valid_annual_filing_receipt(receipt_raw, row.get("bsns_year"))
    limitations: list[str] = []
    if receipt is None or str(receipt_raw) != receipt:
        limitations.append("invalid_citation_receipt")
    if row.get("citation_basis") != _ANNUAL_FILING_CITATION_BASIS:
        limitations.append("citation_basis_not_company_year_annual_filing_match")
    if annual_sources is not None:
        source = annual_sources.get(int(row["bsns_year"]))
        if (
            source is None
            or source.get("fs_div") != row.get("fs_div")
            or source.get("rcept_no") != receipt
        ):
            limitations.append("annual_filing_receipt_mismatch")
    return limitations


def _indexed_rows(
    rows: Iterable[dict[str, Any]],
    *,
    years: list[int],
    fs_div: str,
    annual_sources: dict[int, dict[str, Any]] | None,
) -> tuple[dict[tuple[str, int], dict[str, Any]], dict[tuple[str, int], dict[str, Any]]]:
    """Resolve compact duplicates before any direct or derived observation selection."""
    grouped: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for raw in rows:
        metric = raw.get("metric_key")
        year = raw.get("bsns_year")
        if metric and year in years and raw.get("fs_div") == fs_div:
            grouped.setdefault((str(metric), int(year)), []).append(dict(raw))

    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    rejected: dict[tuple[str, int], dict[str, Any]] = {}
    for identity, candidates in grouped.items():
        if len({_series_value_and_provenance_identity(row) for row in candidates}) > 1:
            rejected[identity] = {
                "limitations": ["conflicting_compact_series_rows"],
                "rows": [_rejected_row(row) for row in candidates],
            }
            continue
        row = candidates[0]
        limitations = _admission_limitations(row, annual_sources=annual_sources)
        if limitations:
            rejected[identity] = {
                "limitations": limitations,
                "rows": [_rejected_row(row)],
            }
            continue
        indexed[identity] = row
    return indexed, rejected


def build_benchmark_series(
    rows: Iterable[dict[str, Any]],
    *,
    years: list[int],
    fs_div: str,
    annual_sources: dict[int, dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build source-backed facts and the narrowly allowed PBT derivation."""
    indexed, rejected = _indexed_rows(
        rows,
        years=years,
        fs_div=fs_div,
        annual_sources=annual_sources,
    )
    result: dict[str, list[dict[str, Any]]] = {metric: [] for metric in _CANONICAL_METRICS}
    for year in years:
        for metric in ("revenue", "assets", "equity"):
            rejection = rejected.get((metric, year))
            result[metric].append(_observation(
                metric,
                year,
                indexed.get((metric, year)),
                basis="direct_annual_fact",
                limitations=rejection.get("limitations") if rejection else None,
                rejected_rows=rejection.get("rows") if rejection else None,
            ))
        direct = indexed.get(("profit_before_tax", year))
        direct_rejection = rejected.get(("profit_before_tax", year))
        direct_observation = _observation(
            "profit_before_tax",
            year,
            direct,
            basis="direct_annual_fact",
            limitations=direct_rejection.get("limitations") if direct_rejection else None,
            rejected_rows=direct_rejection.get("rows") if direct_rejection else None,
        )
        if direct_observation["amount"] is not None:
            result["profit_before_tax"].append(direct_observation)
            continue
        profit = indexed.get(("profit_loss", year))
        tax = indexed.get(("tax_expense", year))
        direct_profit = _observation("profit_loss", year, profit, basis="direct_annual_fact")
        direct_tax = _observation("tax_expense", year, tax, basis="direct_annual_fact")
        operands_usable = (
            direct_profit["amount"] is not None
            and direct_tax["amount"] is not None
            and profit is not None and tax is not None
        )
        profit_receipt = parent_rcept_no(profit.get("citation_rcept_no")) if profit else None
        tax_receipt = parent_rcept_no(tax.get("citation_rcept_no")) if tax else None
        compatible = (
            operands_usable
            and profit.get("fs_div") == tax.get("fs_div") == fs_div
            and profit.get("period_type") == tax.get("period_type") == "duration"
            and profit.get("unit") == tax.get("unit") == "KRW"
            and profit_receipt
            and profit_receipt == tax_receipt
        )
        if compatible:
            sources = [
                _source(profit, operand_metric="profit_loss"),
                _source(tax, operand_metric="tax_expense"),
            ]
            sources = [source for source in sources if source is not None]
            result["profit_before_tax"].append({
                "year": year,
                "amount": direct_profit["amount"] + direct_tax["amount"],
                "basis": "derived_profit_loss_plus_tax_expense",
                "formula": "profit_loss + tax_expense",
                "sources": sources,
                "source": sources[0] if sources else None,
                "unit": "KRW",
                "period_type": "duration",
                "limitations": (
                    ["direct_pbt_unusable_used_compatible_derivation"]
                    + (direct_rejection.get("limitations") if direct_rejection else [])
                ) if direct is not None or direct_rejection else [],
                "rejected_rows": direct_rejection.get("rows") if direct_rejection else [],
            })
        else:
            limitations = ["incompatible_operands"]
            for operand in ("profit_loss", "tax_expense"):
                operand_rejection = rejected.get((operand, year))
                if operand_rejection:
                    limitations.extend(operand_rejection["limitations"])
            if operands_usable and profit_receipt and tax_receipt and profit_receipt != tax_receipt:
                limitations.append("incompatible_filing_provenance")
            if direct is not None:
                limitations.append("direct_pbt_unusable")
            result["profit_before_tax"].append({
                "year": year, "amount": None, "basis": "limited", "sources": [],
                "limitations": list(dict.fromkeys(limitations)), "formula": "profit_loss + tax_expense",
                "rejected_rows": [
                    rejected_row
                    for operand in ("profit_loss", "tax_expense")
                    for rejected_row in (rejected.get((operand, year), {}).get("rows") or [])
                ],
            })
    return result


def observe_stability(observations: list[dict[str, Any]], *, requested_years: list[int]) -> dict[str, Any]:
    """Expose calculations rather than treating a local threshold as ISA law."""
    usable = [item for item in observations if _decimal(item.get("amount")) is not None]
    values = [_decimal(item["amount"]) for item in usable]
    assert all(value is not None for value in values)
    values = [value for value in values if value is not None]
    missing = [year for year in requested_years if year not in {item.get("year") for item in usable}]
    if len(values) < 3:
        return {
            "stability": "insufficient", "usable_year_count": len(values),
            "requested_year_count": len(requested_years), "raw_annual_values": usable,
            "missing_years": missing, "anomaly_flags": [], "role": "not_assessed",
            "volatility_classification": "insufficient", "volatility_rule": _VOLATILITY_RULE,
        }
    with localcontext() as ctx:
        ctx.prec = 28
        mean = sum(values) / Decimal(len(values))
        variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
        sample_stddev = variance.sqrt()
        med = Decimal(str(median(values)))
        mad = Decimal(str(median([abs(value - med) for value in values])))
        yoy = [abs(values[index] - values[index + 1]) for index in range(len(values) - 1)]
        relative_yoy = [
            abs(left - right) / abs(right) if right != 0 else None
            for left, right in zip(values, values[1:])
        ]
        changes = sum(1 for left, right in zip(values, values[1:]) if (left < 0) != (right < 0))
        discontinuity = any(
            min(abs(left), abs(right)) > 0 and max(abs(left), abs(right)) / min(abs(left), abs(right)) >= 5
            for left, right in zip(values, values[1:])
        )
        cv = sample_stddev / abs(mean) if mean != 0 else None
        max_relative_yoy = max((item for item in relative_yoy if item is not None), default=None)
        if changes or discontinuity or cv is None or cv > _VOLATILITY_RULE["moderate_cv_max"] or (max_relative_yoy is not None and max_relative_yoy > _VOLATILITY_RULE["high_relative_year_over_year_change"]):
            volatility = "high"
        elif cv <= _VOLATILITY_RULE["low_cv_max"] and (max_relative_yoy is None or max_relative_yoy <= _VOLATILITY_RULE["low_cv_max"]):
            volatility = "low"
        else:
            volatility = "moderate"
        flags = ["material_discontinuity"] if discontinuity else []
        role = "avoid_as_sole_basis" if volatility == "high" or flags else "primary_candidate" if volatility == "low" else "cross_check"
        return {
            "stability": "observed", "usable_year_count": len(values),
            "requested_year_count": len(requested_years), "raw_annual_values": usable,
            "mean": mean, "median": med, "sample_standard_deviation": sample_stddev,
            "coefficient_of_variation": cv,
            "median_absolute_deviation_ratio": mad / abs(med) if med != 0 else None,
            "minimum": min(values), "maximum": max(values),
            "maximum_absolute_year_over_year_change": max(yoy) if yoy else None,
            "maximum_relative_year_over_year_change": max_relative_yoy,
            "profit_loss_sign_changes": changes, "missing_years": missing,
            "anomaly_flags": flags,
            "volatility_classification": volatility, "volatility_rule": _VOLATILITY_RULE,
            "role": role,
        }


def materiality_candidates(stability: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Create Decimal-only method candidates that retain every rule reference."""
    candidates = []
    refs = {item["reference_id"]: item for item in methodology_references()}
    for metric, observation in stability.items():
        amount = _decimal(observation.get("selected_amount"))
        if (
            amount is None or metric not in _RATES
            or observation.get("stability") != "observed"
            or observation.get("role") not in {"primary_candidate", "cross_check"}
        ):
            continue
        lower, central, upper = _RATES[metric]
        rate_reference_ids = {
            rate: list(reference_ids)
            for rate, reference_ids in _RATE_REFERENCES[metric].items()
        }
        reference_ids = list(dict.fromkeys([
            reference_id for reference_id in ("isa_320_a8_pbt_illustration", "materiality_candidate_ranges_v1")
            if any(reference_id in values for values in rate_reference_ids.values())
        ]))
        candidates.append({
            "benchmark_key": metric, "benchmark_label_ko": _METRIC_LABELS[metric],
            "selected_source_amount": amount, "selected_year_basis": observation.get("selected_year"),
            "lower_rate": lower, "central_rate": central, "upper_rate": upper,
            "lower_candidate_amount": amount * lower,
            "central_candidate_amount": amount * central,
            "upper_candidate_amount": amount * upper,
            "stability": observation.get("stability"), "suitability_role": observation.get("role"),
            "reference_ids": reference_ids,
            "rate_reference_ids": rate_reference_ids,
            "authority_levels": [refs[key]["authority_level"] for key in reference_ids],
            "conclusion_status": "not_assessed",
        })
    return candidates


def prepare_audit_materiality_inputs(company: str, *, end_year: int = 2025, years_back: int = 5, fs_strategy: str = "auto") -> dict[str, Any]:
    """Prepare read-only materiality benchmarks from cited compact annual facts."""
    if years_back not in {3, 5}:
        raise ValueError("years_back must be 3 or 5")
    if fs_strategy not in {"CFS", "OFS", "auto"}:
        raise ValueError("fs_strategy must be CFS, OFS, or auto")
    years = [end_year - offset for offset in range(years_back)]
    with _engine_module.engine.connect() as conn:
        inspector = inspect(conn)
        compact_columns = (
            {column["name"] for column in inspector.get_columns("financial_facts_compact")}
            if inspector.has_table("financial_facts_compact")
            else set()
        )
        has_disclosures = inspector.has_table("disclosures")
        optional_fields = (
            "unit", "period_type", "citation_rcept_no", "citation_report_nm",
            "citation_basis", "quality_status",
        )
        optional_select = ",\n                   ".join(
            field if field in compact_columns else f"NULL AS {field}"
            for field in optional_fields
        )
        company_row = (
            conn.execute(text("SELECT corp_code, corp_name, stock_code, market, induty_code FROM companies WHERE corp_code=:corp_code"), {"corp_code": company}).mappings().first()
            if inspector.has_table("companies") else None
        )
        rows = ([dict(row) for row in conn.execute(text("""
            SELECT corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
                   """ + optional_select + """
            FROM financial_facts_compact
            WHERE corp_code=:corp_code AND bsns_year IN :years
              AND metric_key IN ('profit_before_tax', 'profit_loss', 'tax_expense', 'revenue', 'assets', 'equity')
        """).bindparams(bindparam("years", expanding=True)), {"corp_code": company, "years": years}).mappings()]
        if compact_columns else [])
    if fs_strategy == "auto":
        fs_div = max(("CFS", "OFS"), key=lambda item: sum(1 for row in rows if row.get("fs_div") == item))
    else:
        fs_div = fs_strategy
    annual_sources = (
        annual_filing_sources(
            company,
            years,
            source_table="financial_facts_compact",
            fs_div=fs_div,
        )
        if compact_columns and "citation_basis" in compact_columns and has_disclosures
        else {}
    )
    series = build_benchmark_series(
        rows,
        years=years,
        fs_div=fs_div,
        annual_sources=annual_sources,
    )
    stability: dict[str, dict[str, Any]] = {}
    for metric, observations in series.items():
        item = observe_stability(observations, requested_years=years)
        usable = [row for row in observations if row.get("amount") is not None]
        if usable:
            item["selected_amount"] = usable[0]["amount"]
            item["selected_year"] = usable[0]["year"]
        stability[metric] = item
    candidates = materiality_candidates(stability)
    source_count = sum(len(row.get("sources") or []) for values in series.values() for row in values)
    # The methodology and requested-year observation table remain inspectable
    # even when the local compact cache has no usable facts, so expose this as
    # a bounded limited preparation rather than an opaque missing/error pack.
    status = "usable" if source_count and all(value["usable_year_count"] >= 3 for value in stability.values()) else "limited"
    limitations = []
    if not rows:
        limitations.append("로컬 캐시에 필요한 재무사실이 없습니다. 원 공시에 값이 없다는 뜻은 아닙니다.")
    if any(value["stability"] == "insufficient" for value in stability.values()):
        limitations.append("일부 기준은 비교 가능한 3개년이 부족하여 안정성 결론을 내리지 않았습니다.")
    if rows and not source_count:
        limitations.append("로컬 재무 캐시에 금액 행은 있으나 단위 또는 접수번호 출처가 없어 중요성 후보 금액으로 사용하지 않았습니다.")
    return {
        "assessment_status": "not_assessed", "domain_verdict": "not_assessed",
        "methodology_version": METHODOLOGY_VERSION,
        "subject": dict(company_row or {"corp_code": company, "corp_name": company}),
        "period": {"end_year": end_year, "years_back": years_back, "requested_years": years, "fs_div_used": fs_div},
        "benchmark_series": series, "benchmark_stability": stability,
        "materiality_candidates": candidates, "methodology_references": methodology_references(),
        "confirmed_facts": [
            {"statement": f"{_METRIC_LABELS[metric]} {row['year']}년 공시 수치를 확인했습니다.", "source": row.get("source"), "sources": row.get("sources") or []}
            for metric, values in series.items() for row in values if row.get("amount") is not None and row.get("source")
        ],
        "analysis": [{"statement": "후보 범위는 감사 결론이 아니며, 감사인이 기준과 비율을 명시적으로 선택·승인하기 전까지 not_assessed입니다.", "perspective": "auditor", "basis": "materiality_stability_registry_v1"}],
        "next_checks": ["감사인이 사업 특성, 정상화 필요성, 중요성 기준 및 비율을 선택하고 승인하세요."],
        "data_quality": {"status": status, "covered_years": [year for year in years if any(row.get("amount") is not None for values in series.values() for row in values if row["year"] == year)], "missing_fields": [], "limitations": limitations},
    }
