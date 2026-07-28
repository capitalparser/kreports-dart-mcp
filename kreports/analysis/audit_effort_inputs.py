"""Source-grounded public inputs for three-year audit-effort review.

This module deliberately prepares observations only.  It never derives a
standard-audit-hours result or a statutory calculation.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import inspect, text

import kreports.db.engine as _engine_module
from kreports.analysis.evidence import parent_rcept_no
from kreports.analysis.filing_provenance import annual_filing_sources


OPTIONAL_AUDIT_COLUMNS = (
    "actual_fee_m",
    "actual_hours",
    "contract_fee_m",
    "contract_hours",
    "compatibility_basis",
    "source_rcept_no",
    "source_class",
    "source_period",
)
_REQUIRED_INPUT_FIELDS = ("total_assets", "revenue", "audit_fee_m", "audit_hours")


def _source_for_receipt(
    row: dict[str, Any], *, section_title: str, source_table: str,
) -> dict[str, Any] | None:
    receipt = parent_rcept_no(row.get("rcept_no"))
    if not receipt:
        return None
    return {
        "corp_code": row["corp_code"],
        "corp_name": row.get("corp_name") or row["corp_code"],
        "report_nm": row.get("report_nm"),
        "bsns_year": row.get("bsns_year"),
        "rcept_no": receipt,
        "section_title": section_title,
        "source_table": source_table,
        **({"fs_div": row["fs_div"]} if row.get("fs_div") else {}),
    }


def _select_audit_observation(row: dict[str, Any]) -> tuple[int | None, int | None, str]:
    """Return one complete observation basis; never splice typed sources."""
    for basis, fee_key, hours_key in (
        ("actual", "actual_fee_m", "actual_hours"),
        ("contract", "contract_fee_m", "contract_hours"),
    ):
        if row.get(fee_key) is not None and row.get(hours_key) is not None:
            return row[fee_key], row[hours_key], basis
    if row.get("audit_fee_m") is not None and row.get("audit_hours") is not None:
        return row["audit_fee_m"], row["audit_hours"], "legacy_inferred"
    return None, None, "missing"


def _quality_status(rows: list[dict[str, Any]]) -> str:
    statuses = {row["input_status"] for row in rows}
    if statuses == {"missing"}:
        return "missing"
    if "limited" in statuses or "missing" in statuses:
        return "limited"
    return "usable"


def _audit_source_index(
    conn,
    *,
    corp_code: str,
    years: list[int],
    audit_rows: list[dict[str, Any]],
) -> dict[tuple[int, str], dict[str, Any]]:
    """Resolve only the exact subject annual-report receipts used by audit rows."""
    requested_receipts: list[tuple[int, str]] = []
    for row in audit_rows:
        candidate_year = row.get("bsns_year")
        receipt = parent_rcept_no(row.get("source_rcept_no"))
        if candidate_year in years and receipt:
            pair = (int(candidate_year), receipt)
            if pair not in requested_receipts:
                requested_receipts.append(pair)
    if not requested_receipts:
        return {}

    params: dict[str, Any] = {"corp_code": corp_code}
    requested_values = []
    for index, (candidate_year, receipt) in enumerate(requested_receipts):
        params[f"bsns_year_{index}"] = candidate_year
        params[f"rcept_no_{index}"] = receipt
        params[f"annual_year_pattern_{index}"] = (
            f"%사업보고서 ({candidate_year}.%"
        )
        params[f"receipt_pattern_{index}"] = f"%{receipt}%"
        requested_values.append(
            f"(:bsns_year_{index}, :rcept_no_{index}, "
            f":annual_year_pattern_{index}, :receipt_pattern_{index})"
        )
    rows = conn.execute(text(f"""
        WITH requested_receipts(
            bsns_year, requested_rcept_no, annual_year_pattern, receipt_pattern
        ) AS (
            VALUES {", ".join(requested_values)}
        ),
        ranked_sources AS (
            SELECT requested.bsns_year, requested.requested_rcept_no,
                   d.rcept_no, d.corp_code, d.corp_name, d.report_nm,
                   ROW_NUMBER() OVER (
                       PARTITION BY requested.bsns_year, requested.requested_rcept_no
                       ORDER BY d.disc_date DESC, d.rcept_no DESC
                   ) AS source_rank
            FROM requested_receipts AS requested
            JOIN disclosures AS d
              ON d.corp_code=:corp_code
             AND d.report_nm LIKE requested.annual_year_pattern
             AND d.rcept_no LIKE requested.receipt_pattern
        )
        SELECT bsns_year, requested_rcept_no, rcept_no,
               corp_code, corp_name, report_nm
        FROM ranked_sources
        WHERE source_rank=1
    """), params).mappings().all()

    sources: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        receipt = parent_rcept_no(row.get("rcept_no"))
        if not receipt:
            continue
        candidate_year = int(row["bsns_year"])
        source = _source_for_receipt(
            dict(row),
            section_title="감사보수·감사시간",
            source_table="audit_fees",
        )
        if source:
            sources[(candidate_year, row["requested_rcept_no"])] = source
    return sources


def prepare_standard_audit_hours_inputs(
    company: str,
    *,
    year: int = 2025,
    fs_strategy: str = "auto",
) -> dict[str, Any]:
    """Prepare three-year public inputs without calculating standard hours."""
    if fs_strategy not in {"auto", "CFS", "OFS"}:
        raise ValueError("fs_strategy must be one of auto, CFS, OFS")
    years = [int(year) - offset for offset in range(3)]
    audit_columns = {
        column["name"]
        for column in inspect(_engine_module.engine).get_columns("audit_fees")
    }
    selected_optional = [name for name in OPTIONAL_AUDIT_COLUMNS if name in audit_columns]
    audit_fields = ["bsns_year", "audit_fee_m", "audit_hours", *selected_optional]
    fs_filter = "f.fs_div IN ('CFS', 'OFS')" if fs_strategy == "auto" else "f.fs_div=:fs_div"
    params: dict[str, Any] = {"corp_code": company, "years": years}
    if fs_strategy != "auto":
        params["fs_div"] = fs_strategy

    # The financial query also returns subject identity, avoiding a separate
    # company lookup in the normal populated path.
    financial_sql = text(f"""
        SELECT f.year, f.fs_div, f.total_assets, f.revenue,
               c.corp_code, c.corp_name, c.stock_code, c.market, c.induty_code
        FROM financials AS f
        LEFT JOIN companies AS c ON c.corp_code=f.corp_code
        WHERE f.corp_code=:corp_code AND f.quarter=4 AND f.year IN :years
          AND {fs_filter}
        ORDER BY f.year DESC, CASE f.fs_div WHEN 'CFS' THEN 0 ELSE 1 END
    """).bindparams(__import__("sqlalchemy").bindparam("years", expanding=True))
    audit_sql = text(f"""
        SELECT {', '.join(audit_fields)}
        FROM audit_fees
        WHERE corp_code=:corp_code AND bsns_year IN :years
    """).bindparams(__import__("sqlalchemy").bindparam("years", expanding=True))
    with _engine_module.engine.connect() as conn:
        financials = [dict(row) for row in conn.execute(financial_sql, params).mappings()]
        audits = [dict(row) for row in conn.execute(audit_sql, params).mappings()]
        audit_sources = (
            _audit_source_index(
                conn,
                corp_code=company,
                years=years,
                audit_rows=audits,
            )
            if any(parent_rcept_no(row.get("source_rcept_no")) for row in audits)
            else {}
        )
        if not financials:
            subject_row = conn.execute(text("""
                SELECT corp_code, corp_name, stock_code, market, induty_code
                FROM companies WHERE corp_code=:corp_code LIMIT 1
            """), {"corp_code": company}).mappings().first()
        else:
            subject_row = None

    coverage_by_fs = {
        candidate_fs: len({
            row["year"]
            for row in financials
            if row["fs_div"] == candidate_fs
        })
        for candidate_fs in ("CFS", "OFS")
    }
    fs_div_used = (
        fs_strategy if fs_strategy != "auto"
        else max(("CFS", "OFS"), key=lambda item: (coverage_by_fs[item], item == "CFS"))
    )
    finance_by_year = {
        row["year"]: row for row in financials if row["fs_div"] == fs_div_used
    }
    audit_by_year = {row["bsns_year"]: row for row in audits}
    annual_by_year = annual_filing_sources(
        company,
        years,
        source_table="financials",
        fs_div=fs_div_used,
    )
    subject_source = financials[0] if financials else dict(subject_row or {})
    subject = {
        "corp_code": company,
        "corp_name": subject_source.get("corp_name") or company,
        "stock_code": subject_source.get("stock_code"),
        "market": subject_source.get("market"),
        "induty_code": subject_source.get("induty_code"),
    }
    rows: list[dict[str, Any]] = []
    for candidate_year in years:
        financial = finance_by_year.get(candidate_year) or {}
        audit = audit_by_year.get(candidate_year) or {}
        audit_fee_m, audit_hours, hours_basis = _select_audit_observation(audit)
        financial_source = None
        if financial:
            financial_source = annual_by_year.get(candidate_year)
        audit_receipt = parent_rcept_no(audit.get("source_rcept_no"))
        audit_source = (
            audit_sources.get((candidate_year, audit_receipt))
            if audit_receipt
            else None
        )
        missing_fields = [
            field for field, value in (
                ("total_assets", financial.get("total_assets")),
                ("revenue", financial.get("revenue")),
                ("audit_fee_m", audit_fee_m),
                ("audit_hours", audit_hours),
            ) if value is None
        ]
        provenance_gaps = []
        if financial and financial_source is None:
            provenance_gaps.append("uncitable_financial_source")
        if audit and (audit_fee_m is not None or audit_hours is not None) and audit_source is None:
            provenance_gaps.append("uncitable_audit_source")
        if len(missing_fields) == len(_REQUIRED_INPUT_FIELDS):
            input_status = "missing"
        elif missing_fields or provenance_gaps:
            input_status = "limited"
        else:
            input_status = "usable"
        rows.append({
            "year": candidate_year,
            "fs_div": fs_div_used,
            "total_assets": financial.get("total_assets"),
            "revenue": financial.get("revenue"),
            "total_assets_100m": (
                financial["total_assets"] / 100_000_000 if financial.get("total_assets") is not None else None
            ),
            "revenue_100m": financial["revenue"] / 100_000_000 if financial.get("revenue") is not None else None,
            "audit_fee_m": audit_fee_m,
            "audit_hours": audit_hours,
            "hours_basis": hours_basis,
            "financial_source": financial_source,
            "audit_source": audit_source,
            "input_status": input_status,
            "missing_fields": missing_fields,
            "provenance_gaps": provenance_gaps,
        })
    status = _quality_status(rows)
    missing = [f"{row['year']}.{field}" for row in rows for field in row["missing_fields"]]
    limitations = []
    if status == "missing":
        limitations.append("요청 3개년 입력값이 로컬 캐시에 없음; 원 공시에 값이 없다는 뜻은 아닙니다.")
    if any("uncitable_financial_source" in row["provenance_gaps"] for row in rows):
        limitations.append("일부 재무 입력값은 동일 회사·사업연도 사업보고서 접수번호를 확인하지 못했습니다.")
    if any("uncitable_audit_source" in row["provenance_gaps"] for row in rows):
        limitations.append("일부 감사보수·감사시간 입력값은 유효한 감사 출처 접수번호를 확인하지 못했습니다.")
    return {
        "subject": subject,
        "requested_years": years,
        "fs_div_used": fs_div_used,
        "standard_audit_hours_assessment": "not_assessed",
        "domain_verdict": "not_assessed",
        "rows": rows,
        "subject_scale_history": rows,
        "subject_scale_history_quality": {
            "status": status,
            "covered_years": [row["year"] for row in rows if row["input_status"] != "missing"],
            "complete_years": [row["year"] for row in rows if row["input_status"] == "usable"],
            "missing_fields": missing,
        },
        "data_quality": {
            "status": status,
            "covered_years": [row["year"] for row in rows if row["input_status"] != "missing"],
            "complete_years": [row["year"] for row in rows if row["input_status"] == "usable"],
            "missing_fields": missing,
            "limitations": limitations,
        },
        "confirmed_facts": [
            {"statement": f"{row['year']}년 감사시간 입력 기준: {row['hours_basis']}", "source": row["financial_source"]}
            for row in rows if row["financial_source"]
        ],
        "analysis": [{
            "statement": "표준감사시간 결론은 산정하지 않았으며, 공개자료 입력과 출처 상태만 정리했습니다.",
            "perspective": "auditor",
        }],
        "next_checks": ["표준감사시간 산정 또는 법정 산정값은 별도 기준과 전문가 검토로 확인하세요."],
    }
