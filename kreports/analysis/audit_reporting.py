"""Audit history, policies, report matters, KAMs, and procedures."""
from __future__ import annotations

import json
import re
from typing import Optional

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module
from kreports.analysis import queries as _queries
from kreports.analysis.audit_procedure_evidence import (
    ProcedureDatabaseUnavailable,
    classify_audit_procedure_linkages,
    procedure_database_preflight,
    procedure_read_connection,
    procedure_read_engine,
)

from kreports.analysis._shared import _clean_dict, _dedupe_confirmed_facts, _df_to_records, _display_text, _has_db_column, _has_db_table
from kreports.analysis.company_profile import (
    get_company_summary,
    get_industry_name,
    resolve_company_identifier,
    resolve_corp_code,
)
from kreports.analysis.search_adapter import (
    group_company_records,
)


_AUDIT_FEE_TYPED_COLUMNS = (
    "contract_fee_m",
    "contract_hours",
    "actual_fee_m",
    "actual_hours",
    "source_class",
    "source_rcept_no",
    "source_period",
    "availability_status",
    "quality_status",
    "compatibility_basis",
    "conflict_status",
    "source_observations_json",
)


def _valid_audit_fee_observation(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("corp_code"), str):
        return False
    if not isinstance(value.get("bsns_year"), int):
        return False
    if not isinstance(value.get("source_class"), str) or not value["source_class"]:
        return False
    for field_name in (
        "contract_fee_m",
        "contract_hours",
        "actual_fee_m",
        "actual_hours",
    ):
        field_value = value.get(field_name)
        if field_value is not None and (
            not isinstance(field_value, int) or isinstance(field_value, bool)
        ):
            return False
    for field_name in ("availability_status", "quality_status"):
        field_value = value.get(field_name)
        if field_value is not None and not isinstance(field_value, str):
            return False
    eligibility = value.get("source_eligibility")
    if eligibility is not None and eligibility not in {
        "eligible",
        "not_eligible",
        "unknown",
    }:
        return False
    if value.get("raw_values") is not None and not isinstance(
        value["raw_values"],
        dict,
    ):
        return False
    if value.get("limitations") is not None and not isinstance(
        value["limitations"],
        list,
    ):
        return False
    return True


def _audit_fee_observation_conflicts(observations: list[dict]) -> list[dict]:
    conflicts: list[dict] = []
    for metric in ("actual_fee_m", "actual_hours"):
        claims = []
        for item in observations:
            if (
                item.get(metric) is None
                or item.get("quality_status") in {"error", "missing"}
            ):
                continue
            try:
                claims.append((int(item[metric]), item))
            except (TypeError, ValueError):
                continue
        for index, (left, left_source) in enumerate(claims):
            for right, right_source in claims[index + 1 :]:
                absolute = abs(left - right)
                percentage = absolute / max(abs(left), abs(right), 1)
                if percentage <= 0.05:
                    continue
                conflicts.append(
                    {
                        "metric": metric,
                        "left_value": left,
                        "right_value": right,
                        "absolute_variance": absolute,
                        "percentage_variance": round(percentage, 6),
                        "denominator": "max(abs(left), abs(right), 1)",
                        "left_source": left_source.get("source_class"),
                        "right_source": right_source.get("source_class"),
                        "left_rcept_no": left_source.get("source_rcept_no"),
                        "right_rcept_no": right_source.get("source_rcept_no"),
                    }
                )
    return sorted(
        conflicts,
        key=lambda item: (
            item["metric"],
            str(item["left_source"]),
            str(item["right_source"]),
        ),
    )


def _audit_fee_availability_from_engine(corp_code: str, year: int, active_engine) -> dict:
    try:
        with active_engine.connect() as connection:
            table_columns = {
                str(row["name"])
                for row in connection.execute(
                    text("PRAGMA table_info(audit_fees)")
                ).mappings()
            }
            if not table_columns:
                return {
                    "corp_code": corp_code,
                    "year": year,
                    "availability_status": "schema_unavailable",
                    "source_eligibility": "unknown",
                    "quality_status": "missing",
                    "selected": {
                        "audit_fee_m": None,
                        "audit_hours": None,
                        "basis": "unavailable",
                    },
                    "limitations": ["audit_fees table is unavailable"],
                    "source_observations": [],
                    "conflicts": [],
                }
            selected_columns = [
                name
                for name in (
                    "audit_fee_m",
                    "audit_hours",
                    "non_audit_fee_m",
                    "non_audit_hours",
                    "nas_ratio",
                    "auditor_nm",
                    *_AUDIT_FEE_TYPED_COLUMNS,
                )
                if name in table_columns
            ]
            row = connection.execute(
                text(
                    "SELECT "
                    + ", ".join(selected_columns)
                    + " FROM audit_fees "
                    "WHERE corp_code=:corp_code AND bsns_year=:year LIMIT 1"
                ),
                {"corp_code": corp_code, "year": year},
            ).mappings().first()
            has_fetch_log = connection.execute(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='fetch_log'"
                )
            ).first()
            fetch_row = (
                connection.execute(
                    text(
                        "SELECT status, error_msg FROM fetch_log "
                        "WHERE corp_code=:corp_code AND year=:year "
                        "AND task_type IN ('audit_fee', 'audit_fees') "
                        "ORDER BY fetched_at DESC, id DESC LIMIT 1"
                    ),
                    {"corp_code": corp_code, "year": year},
                ).mappings().first()
                if has_fetch_log
                else None
            )
    except Exception as exc:
        return {
            "corp_code": corp_code,
            "year": year,
            "availability_status": "schema_unavailable",
            "source_eligibility": "unknown",
            "quality_status": "error",
            "selected": {
                "audit_fee_m": None,
                "audit_hours": None,
                "basis": "unavailable",
            },
            "limitations": [f"read-only availability query failed: {type(exc).__name__}"],
            "source_observations": [],
            "conflicts": [],
        }

    if row is None:
        fetch_status = str(fetch_row["status"]) if fetch_row else ""
        availability = (
            "transport_error"
            if fetch_status == "error"
            else "missing"
            if fetch_status == "no_data"
            else "missing"
        )
        return {
            "corp_code": corp_code,
            "year": year,
            "availability_status": availability,
            "source_eligibility": "unknown",
            "quality_status": "error" if fetch_status == "error" else "missing",
            "selected": {
                "audit_fee_m": None,
                "audit_hours": None,
                "basis": "unavailable",
            },
            "contract": {"fee_m": None, "hours": None},
            "actual": {"fee_m": None, "hours": None},
            "source": {},
            "source_observations": [],
            "conflicts": [],
            "limitations": [
                str(fetch_row["error_msg"])
                if fetch_row and fetch_row["error_msg"]
                else "No typed audit fee observation is cached"
            ],
        }

    record = dict(row)
    typed_schema = all(name in table_columns for name in _AUDIT_FEE_TYPED_COLUMNS)
    provenance_raw = record.get("source_observations_json")
    provenance_error = False
    try:
        observations = json.loads(provenance_raw or "[]")
        if not isinstance(observations, list):
            provenance_error = bool(provenance_raw)
            observations = []
    except (TypeError, ValueError):
        provenance_error = bool(provenance_raw)
        observations = []
    raw_observations = observations
    observations = [
        item
        for item in raw_observations
        if _valid_audit_fee_observation(item)
    ][:20]
    if len(observations) != len(raw_observations):
        provenance_error = True
    conflicts = _audit_fee_observation_conflicts(observations)
    basis = record.get("compatibility_basis") or "legacy_inferred"
    audit_fee = record.get("audit_fee_m")
    audit_hours = record.get("audit_hours")
    eligibility_values = {
        str(item.get("source_eligibility"))
        for item in observations
        if item.get("source_eligibility")
        in {"eligible", "not_eligible", "unknown"}
    }
    if "eligible" in eligibility_values:
        source_eligibility = "eligible"
    elif eligibility_values == {"not_eligible"}:
        source_eligibility = "not_eligible"
    elif audit_fee is not None or audit_hours is not None:
        source_eligibility = "eligible"
    else:
        source_eligibility = "unknown"
    availability = record.get("availability_status")
    if not availability:
        availability = (
            "available"
            if audit_fee is not None and audit_hours is not None
            else "partial"
            if audit_fee is not None or audit_hours is not None
            else "missing"
        )
    quality = record.get("quality_status") or (
        "verified" if availability == "available" else "partial"
    )
    limitations: list[str] = []
    for observation in observations:
        for limitation in observation.get("limitations") or []:
            text_value = str(limitation).strip()
            if text_value and text_value not in limitations:
                limitations.append(text_value)
            if len(limitations) >= 10:
                break
        if len(limitations) >= 10:
            break
    if not typed_schema:
        limitations.append(
            "Pre-20260711_07 row: compatibility basis inferred from legacy columns"
        )
    if availability == "conflict":
        limitations.append(
            "Source observations disagree by more than 5%; verify the filing"
        )
    if record.get("nas_ratio") is not None and basis not in {
        "actual",
        "legacy_inferred",
    }:
        limitations.append(
            "NAS ratio omitted from interpretation because fee bases may be incompatible"
        )
    if provenance_error:
        availability = "parse_error"
        quality = "error"
        limitations.append(
            "Stored audit fee source provenance is malformed and could not be verified"
        )
    if fetch_row and str(fetch_row["status"]) == "error":
        availability = "transport_error"
        quality = "error"
        limitations.append(
            str(fetch_row["error_msg"])
            if fetch_row["error_msg"]
            else "Latest audit-fee source attempt failed"
        )
    return {
        "corp_code": corp_code,
        "year": year,
        "availability_status": availability,
        "source_eligibility": source_eligibility,
        "quality_status": quality,
        "selected": {
            "audit_fee_m": audit_fee,
            "audit_hours": audit_hours,
            "basis": basis,
            "nas_ratio": (
                record.get("nas_ratio")
                if basis in {"actual", "legacy_inferred"}
                else None
            ),
        },
        "contract": {
            "fee_m": record.get("contract_fee_m"),
            "hours": record.get("contract_hours"),
        },
        "actual": {
            "fee_m": record.get("actual_fee_m"),
            "hours": record.get("actual_hours"),
        },
        "source": {
            "class": record.get("source_class"),
            "rcept_no": record.get("source_rcept_no"),
            "period": record.get("source_period"),
            "auditor_nm": record.get("auditor_nm"),
        },
        "source_observations": observations,
        "conflict_status": record.get("conflict_status") or (
            "conflict" if conflicts else "none"
        ),
        "conflicts": conflicts,
        "limitations": limitations,
    }


def audit_fee_availability(corp_code: str, year: int) -> dict:
    """Return source-aware audit fee/hour coverage without collecting or writing."""
    try:
        with procedure_read_engine({"audit_fees"}) as read_engine:
            return _audit_fee_availability_from_engine(
                corp_code,
                year,
                read_engine,
            )
    except ProcedureDatabaseUnavailable as exc:
        return {
            "corp_code": corp_code,
            "year": year,
            "availability_status": "schema_unavailable",
            "source_eligibility": "unknown",
            "quality_status": "missing",
            "selected": {
                "audit_fee_m": None,
                "audit_hours": None,
                "basis": "unavailable",
            },
            "contract": {"fee_m": None, "hours": None},
            "actual": {"fee_m": None, "hours": None},
            "source": {},
            "source_observations": [],
            "conflicts": [],
            "limitations": [str(exc)],
        }


def audit_fee_availability_trend(
    corp_code: str,
    end_year: int,
    *,
    periods: int = 5,
) -> dict:
    """Return an explicit annual availability window with null gaps."""
    bounded_periods = min(max(int(periods), 1), 10)
    rows = [
        audit_fee_availability(corp_code, year)
        for year in range(end_year - bounded_periods + 1, end_year + 1)
    ]
    return {
        "corp_code": corp_code,
        "year_from": end_year - bounded_periods + 1,
        "year_to": end_year,
        "periods": [
            {
                "year": row["year"],
                "availability_status": row["availability_status"],
                "source_eligibility": row.get(
                    "source_eligibility",
                    "unknown",
                ),
                "quality_status": row["quality_status"],
                "selected_fee_m": row.get("selected", {}).get("audit_fee_m"),
                "selected_hours": row.get("selected", {}).get("audit_hours"),
                "metric_basis": row.get("selected", {}).get("basis"),
                "actual_fee_m": row.get("actual", {}).get("fee_m"),
                "actual_hours": row.get("actual", {}).get("hours"),
                "contract_fee_m": row.get("contract", {}).get("fee_m"),
                "contract_hours": row.get("contract", {}).get("hours"),
            }
            for row in rows
        ],
        "limitation": (
            "Unavailable periods remain null and are excluded from "
            "source-available coverage denominators."
        ),
    }


def _audit_section_source(
    subject: dict | None,
    record: dict,
    *,
    default_section_title: str,
    source_table: str,
) -> dict:
    return {
        "corp_code": record.get("corp_code") or (subject or {}).get("corp_code"),
        "corp_name": record.get("corp_name") or (subject or {}).get("corp_name") or record.get("corp_code"),
        "report_nm": "감사보고서" if record.get("source_type") == "audit_report" or source_table != "report_sections.business_report" else "사업보고서",
        "bsns_year": record.get("bsns_year") or record.get("year"),
        "rcept_no": record.get("rcept_no"),
        "section_title": record.get("section_title") or default_section_title,
        "section_key": record.get("section_key"),
        "source_table": source_table,
    }


def _audit_report_sections_evidence(result: dict) -> dict:
    subject = result.get("subject") if isinstance(result.get("subject"), dict) else None
    facts: list[dict] = []
    for section in (result.get("sections") or [])[:4]:
        title = section.get("section_title") or section.get("section_key") or "감사보고서 섹션"
        excerpt = str(section.get("body_excerpt") or "").strip()[:260]
        if not excerpt:
            continue
        facts.append({
            "statement": f"{result.get('year')}년 감사보고서 {title} 본문에서 다음 내용이 확인됩니다: {excerpt}",
            "source": _audit_section_source(
                subject,
                section,
                default_section_title=title,
                source_table=result.get("data_quality", {}).get("source") or "report_sections",
            ),
            "excerpt": excerpt,
        })
    analysis = [{
        "perspective": "auditor",
        "statement": "감사보고서 본문 섹션은 감사위험 식별, KAM 선정 이유, 감사절차 대응을 확인하는 1차 증거입니다.",
    }]
    next_checks = [
        "KAM 본문에서는 선정 이유와 수행한 감사절차가 모두 추출되었는지 확인하세요.",
        "사업보고서 주석의 회계정책·추정 문단과 감사보고서 KAM 대응절차를 대조하세요.",
    ]
    return {"confirmed_facts": _dedupe_confirmed_facts(facts), "analysis": analysis, "next_checks": next_checks}


def _audit_matters_evidence(result: dict) -> dict:
    facts: list[dict] = []
    for company in (result.get("companies") or [])[:4]:
        subject = {
            "corp_code": company.get("corp_code"),
            "corp_name": company.get("corp_name"),
        }
        for section in (company.get("sections") or [])[:2]:
            title = section.get("section_title") or section.get("section_key") or "감사보고서 matter"
            excerpt = str(section.get("body_excerpt") or "").strip()[:260]
            facts.append({
                "statement": (
                    f"{company.get('corp_name') or company.get('corp_code')} {section.get('bsns_year')}년 "
                    f"감사보고서에서 {title} 문단이 확인됩니다."
                    + (f" 주요 내용: {excerpt}" if excerpt else "")
                ),
                "source": _audit_section_source(
                    subject,
                    {**section, "corp_code": company.get("corp_code"), "corp_name": company.get("corp_name"), "source_type": "audit_report"},
                    default_section_title=title,
                    source_table=result.get("data_quality", {}).get("source") or "audit_matter_items",
                ),
                "excerpt": excerpt,
            })
            if len(facts) >= 6:
                break
        if len(facts) >= 6:
            break
    analysis = [{
        "perspective": "auditor",
        "statement": "강조사항·기타사항·계속기업 문단은 감사의견 자체와 별도로 수임위험, 계속기업, 후속사건, 범위제한 가능성을 점검하는 근거입니다.",
    }]
    next_checks = [
        "해당 문단이 감사의견 변형, 강조사항, 기타사항, 계속기업 관련 중요한 불확실성 중 무엇인지 원문 기준으로 확인하세요.",
        "동종업종 내 반복적으로 나타나는 matter인지 peer 검색 결과와 비교하세요.",
    ]
    return {"confirmed_facts": _dedupe_confirmed_facts(facts), "analysis": analysis, "next_checks": next_checks}


def _audit_procedures_evidence(result: dict) -> dict:
    facts: list[dict] = []
    for company in (result.get("companies") or [])[:4]:
        subject = {
            "corp_code": company.get("corp_code"),
            "corp_name": company.get("corp_name"),
        }
        for record in (company.get("records") or [])[:2]:
            excerpt = str(record.get("procedure_excerpt") or "").strip()[:260]
            facts.append({
                "statement": (
                    f"{company.get('corp_name') or company.get('corp_code')} {record.get('year')}년 KAM 감사절차에서 "
                    f"{record.get('procedure_type') or 'procedure'} 유형 절차가 확인됩니다."
                    + (f" 절차 내용: {excerpt}" if excerpt else "")
                ),
                "source": _audit_section_source(
                    subject,
                    {**record, "corp_code": company.get("corp_code"), "corp_name": company.get("corp_name")},
                    default_section_title="KAM 감사절차",
                    source_table=result.get("data_quality", {}).get("source") or "audit_procedure_items",
                ),
                "excerpt": excerpt,
            })
            if len(facts) >= 6:
                break
        if len(facts) >= 6:
            break
    analysis = [{
        "perspective": "auditor",
        "statement": "감사절차 유형은 KAM 위험요인에 대한 감사인의 대응 방식이 충분히 구체적인지, peer 대비 절차 밀도가 낮지 않은지 비교하는 데 사용됩니다.",
    }]
    next_checks = [
        "절차 문구가 단순 확인인지, 통제테스트·실증절차·전문가 활용·추정 검토 등으로 충분히 구분되는지 확인하세요.",
        "동일 KAM topic에서 peer 감사절차 유형 분포와 비교하세요.",
    ]
    return {"confirmed_facts": _dedupe_confirmed_facts(facts), "analysis": analysis, "next_checks": next_checks}


_cached_years_for_sections = _queries.get_cached_report_section_years


_EVIDENCE_REPORT_SECTION_RE = re.compile(
    r"^##\s+report_section/(?P<section_key>[A-Za-z0-9_]+):\s*(?P<section_title>.*?)\s*$",
    re.MULTILINE,
)


def _iter_evidence_report_sections(normalized_text: str | None) -> list[dict]:
    """Parse MD-like evidence_documents headings into report-section rows."""
    text_value = _display_text(normalized_text)
    matches = list(_EVIDENCE_REPORT_SECTION_RE.finditer(text_value))
    rows: list[dict] = []
    for idx, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text_value)
        body = text_value[body_start:body_end].strip()
        section_key = match.group("section_key").strip()
        section_title = match.group("section_title").strip()
        rows.append({
            "section_key": section_key,
            "section_title": section_title,
            "body_text": body,
            "body_length": len(body),
            "ordinal": idx,
        })
    return rows


def _evidence_report_section_rows(
    *,
    corp_codes: list[str],
    year: int,
    source_types: list[str],
    section_keys: list[str] | None = None,
    limit: int = 500,
) -> list[dict]:
    if not corp_codes:
        return []
    stmt = text(
        """
        SELECT ed.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
               ed.bsns_year, ed.rcept_no, ed.dcm_no, ed.source_type,
               ed.normalized_text, ed.text_length, ed.generated_at
        FROM evidence_documents ed
        JOIN companies c ON c.corp_code=ed.corp_code
        WHERE ed.corp_code IN :corp_codes
          AND ed.bsns_year=:year
          AND ed.source_type IN :source_types
        ORDER BY ed.bsns_year DESC, c.market, c.corp_name, ed.source_type
        LIMIT :doc_limit
        """
    ).bindparams(
        bindparam("corp_codes", expanding=True),
        bindparam("source_types", expanding=True),
    )
    wanted = set(section_keys or [])
    out: list[dict] = []
    with _engine_module.engine.connect() as conn:
        docs = [dict(r) for r in conn.execute(
            stmt,
            {
                "corp_codes": corp_codes,
                "year": int(year),
                "source_types": source_types,
                "doc_limit": max(limit, len(corp_codes) * 2),
            },
        ).mappings().all()]
    for doc in docs:
        for section in _iter_evidence_report_sections(doc.pop("normalized_text", "")):
            if wanted and section["section_key"] not in wanted:
                continue
            body = section["body_text"]
            out.append({
                "corp_code": doc["corp_code"],
                "stock_code": doc.get("stock_code"),
                "corp_name": doc.get("corp_name"),
                "market": doc.get("market"),
                "induty_code": doc.get("induty_code"),
                "bsns_year": doc["bsns_year"],
                "rcept_no": doc["rcept_no"],
                "dcm_no": doc.get("dcm_no"),
                "source_type": doc["source_type"],
                "section_key": section["section_key"],
                "section_title": section["section_title"],
                "body_text": body,
                "body_length": len(body),
                "ordinal": section["ordinal"],
                "evidence_source": "evidence_documents",
            })
            if len(out) >= limit:
                return out
    return out


def _evidence_years_for_sections(corp_code: str, source_type: str, section_key: str | None = None) -> list[int]:
    with _engine_module.engine.connect() as conn:
        docs = [dict(r) for r in conn.execute(
            text(
                """
                SELECT bsns_year, normalized_text
                FROM evidence_documents
                WHERE corp_code=:corp_code AND source_type=:source_type
                ORDER BY bsns_year DESC
                """
            ),
            {"corp_code": corp_code, "source_type": source_type},
        ).mappings().all()]
    years: list[int] = []
    for doc in docs:
        sections = _iter_evidence_report_sections(doc.get("normalized_text"))
        if section_key and not any(section["section_key"] == section_key for section in sections):
            continue
        year_value = int(doc["bsns_year"])
        if year_value not in years:
            years.append(year_value)
    return years


def _cache_quality_status(*, subject_count: int, peer_total: int = 0, peer_covered: int = 0) -> str:
    if subject_count <= 0 and peer_covered <= 0:
        return "missing"
    if peer_total and peer_covered / peer_total < 0.5:
        return "limited"
    if subject_count <= 0:
        return "subject_missing"
    return "usable"


def _kam_hint_coverage(rows: list[dict]) -> dict:
    kam_rows = [row for row in rows if row.get("section_key") == "kam"]
    with_reason = sum(
        1 for row in kam_rows
        if (row.get("kam_analysis") or {}).get("has_reason_hint")
    )
    with_procedure = sum(
        1 for row in kam_rows
        if (row.get("kam_analysis") or {}).get("has_procedure_hint")
        or bool(row.get("related_audit_procedures"))
    )
    total = len(kam_rows)
    return {
        "kam_body_count": total,
        "reason": {
            "with_reason_hint": with_reason,
            "coverage_pct": round(with_reason * 100.0 / total, 1) if total else 0.0,
        },
        "procedure": {
            "with_procedure_hint": with_procedure,
            "coverage_pct": round(with_procedure * 100.0 / total, 1) if total else 0.0,
        },
    }


def _attach_related_audit_procedures(rows: list[dict], *, corp_code: str, year: int) -> None:
    kam_rcept_nos = sorted({
        str(row.get("rcept_no"))
        for row in rows
        if row.get("section_key") == "kam" and row.get("rcept_no")
    })
    if not kam_rcept_nos:
        return
    stmt = text(
        """
        SELECT rcept_no, dcm_no, kam_topic, procedure_type, procedure_text,
               procedure_length, section_ordinal, procedure_ordinal
        FROM audit_procedure_items
        WHERE corp_code=:corp_code
          AND bsns_year=:year
          AND rcept_no IN :rcept_nos
        ORDER BY rcept_no, section_ordinal, procedure_ordinal
        """
    ).bindparams(bindparam("rcept_nos", expanding=True))
    with _engine_module.engine.connect() as conn:
        procedure_rows = [dict(r) for r in conn.execute(
            stmt,
            {"corp_code": corp_code, "year": year, "rcept_nos": kam_rcept_nos},
        ).mappings().all()]
        fallback_rows = [dict(r) for r in conn.execute(
            text(
                """
                SELECT rcept_no, dcm_no, kam_topic, procedure_type, procedure_text,
                       procedure_length, section_ordinal, procedure_ordinal
                FROM audit_procedure_items
                WHERE corp_code=:corp_code
                  AND bsns_year=:year
                ORDER BY source_type, rcept_no, section_ordinal, procedure_ordinal
                LIMIT 10
                """
            ),
            {"corp_code": corp_code, "year": year},
        ).mappings().all()]

    grouped: dict[str, list[dict]] = {}
    for item in procedure_rows:
        text_value = _display_text(item.pop("procedure_text") or "")
        item["procedure_excerpt"] = text_value[:900]
        grouped.setdefault(str(item.get("rcept_no")), []).append(item)
    for item in fallback_rows:
        text_value = _display_text(item.pop("procedure_text") or "")
        item["procedure_excerpt"] = text_value[:900]

    for row in rows:
        if row.get("section_key") != "kam":
            continue
        procedures = grouped.get(str(row.get("rcept_no")), [])
        source = "audit_procedure_items"
        if not procedures:
            procedures = fallback_rows
            source = "audit_procedure_items_company_year"
        if not procedures:
            continue
        row["related_audit_procedures"] = procedures[:10]
        row["related_audit_procedure_count"] = len(procedures)
        row["related_audit_procedure_source"] = source
        analysis = row.setdefault("kam_analysis", {})
        if not analysis.get("has_procedure_hint"):
            analysis["has_procedure_hint"] = True
            analysis["procedure_excerpt"] = procedures[0].get("procedure_excerpt") or ""
            analysis["procedure_keywords"] = sorted({
                str(item.get("procedure_type"))
                for item in procedures
                if item.get("procedure_type")
            })


def get_accounting_policy(
    company: str,
    bsns_year: int,
    fs_div: str = "CFS",
) -> Optional[dict]:
    """
    사업보고서 주석에서 회계정책 항목 추출.

    Returns:
        {
          "corp_code", "bsns_year", "fs_div",
          "items": {item_key: {"heading", "body"}},
          "item_count": int,
        } or None (수집된 사업보고서 없음)
    """
    corp_code = resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "items": {},
            "item_count": 0,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    data = _queries.get_cached_accounting_policy(corp_code, bsns_year, fs_div=fs_div)
    if data is None:
        from kreports.runtime import readonly_cache_miss

        return {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "items": {},
            "item_count": 0,
            "note": readonly_cache_miss("accounting_policy", corp_code, bsns_year),
        }
    result = {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "fs_div": fs_div,
        "items": data.get("items", {}),
        "item_count": len(data.get("items", {})),
    }
    return _clean_dict(result)


def get_audit_history(company: str) -> dict:
    """
    연도별 감사인·의견·연속연수 이력.

    Returns:
        {
          "corp_code",
          "history": [
            {"회계연도", "구분", "감사인", "감사의견", "교체여부", "연속연수"},
            ...
          ],
          "count": int,
        }
    """
    corp_code = resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "history": [],
            "count": 0,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    df = _queries.get_auditors(corp_code)
    records = _df_to_records(df)
    return {
        "corp_code": corp_code,
        "history": records,
        "count": len(records),
    }


_KAM_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "revenue_recognition": ["수익", "매출", "진행기준", "총액", "순액"],
    "impairment": ["손상", "회수가능", "영업권", "현금창출단위"],
    "inventory": ["재고", "평가충당", "순실현가능"],
    "fair_value": ["공정가치", "금융상품", "파생"],
    "provisions": ["충당부채", "우발", "소송"],
    "development_cost": ["개발비", "무형자산"],
    "tax": ["법인세", "이연법인세"],
}


def _topic_hits(text_value: str | None) -> list[str]:
    text_value = text_value or ""
    hits = []
    for topic, keywords in _KAM_TOPIC_KEYWORDS.items():
        if any(keyword in text_value for keyword in keywords):
            hits.append(topic)
    return hits


def get_audit_report_sections(
    company: str,
    year: int = 2025,
    section_key: str | None = None,
    source_type: str = "audit_report",
    limit: int = 20,
) -> dict:
    """Return cached audit-report body sections for a company/year."""
    corp_code = resolve_corp_code(company) or company
    comp = get_company_summary(corp_code)
    if not comp:
        return {"error": "company not found", "company": company}

    if source_type not in {"audit_report", "business_report", "all"}:
        return {"error": "source_type must be audit_report, business_report, or all", "source_type": source_type}
    source_filter = ("audit_report", "business_report") if source_type == "all" else (source_type,)
    dcm_select = "dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
    stmt = text(
        f"""
        SELECT rcept_no, {dcm_select}, bsns_year, source_type, section_key, section_title,
               body_text, body_length, fetched_at
        FROM report_sections
        WHERE corp_code=:corp_code
          AND bsns_year=:year
          AND source_type IN :source_types
          AND (:section_key IS NULL OR section_key=:section_key)
        ORDER BY section_key, ordinal
        LIMIT :limit
        """
    ).bindparams(bindparam("source_types", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            stmt,
            {
                "corp_code": corp_code,
                "year": year,
                "section_key": section_key,
                "source_types": list(source_filter),
                "limit": int(limit),
            },
        ).mappings().all()]
    row_source = "report_sections"
    if not rows:
        rows = _evidence_report_section_rows(
            corp_codes=[corp_code],
            year=year,
            source_types=list(source_filter),
            section_keys=[section_key] if section_key else None,
            limit=int(limit),
        )
        if rows:
            row_source = "evidence_documents"

    alternative_rows: list[dict] = []
    alternative_year: int | None = None
    if not rows:
        years = _cached_years_for_sections(
            corp_code,
            "audit_report" if source_type == "audit_report" else "business_report",
            section_key,
        ) if source_type != "all" else sorted(set(
            _cached_years_for_sections(corp_code, "audit_report", section_key)
            + _cached_years_for_sections(corp_code, "business_report", section_key)
        ), reverse=True)
        evidence_years = _evidence_years_for_sections(
            corp_code,
            "audit_report" if source_type == "audit_report" else "business_report",
            section_key,
        ) if source_type != "all" else sorted(set(
            _evidence_years_for_sections(corp_code, "audit_report", section_key)
            + _evidence_years_for_sections(corp_code, "business_report", section_key)
        ), reverse=True)
        years = sorted(set(years + evidence_years), reverse=True)
        alternative_year = years[0] if years else None
        if alternative_year is not None:
            with _engine_module.engine.connect() as conn:
                alternative_rows = [dict(r) for r in conn.execute(
                    stmt,
                    {
                        "corp_code": corp_code,
                        "year": alternative_year,
                        "section_key": section_key,
                        "source_types": list(source_filter),
                        "limit": min(int(limit), 5),
                    },
                ).mappings().all()]
            if not alternative_rows:
                alternative_rows = _evidence_report_section_rows(
                    corp_codes=[corp_code],
                    year=alternative_year,
                    source_types=list(source_filter),
                    section_keys=[section_key] if section_key else None,
                    limit=min(int(limit), 5),
                )

    for row in rows:
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:2000]
        if row.get("section_key") == "kam":
            from kreports.processor.audit_report_parser import summarize_kam_body
            row["kam_analysis"] = summarize_kam_body(body)
        row.pop("body_text", None)
    _attach_related_audit_procedures(rows, corp_code=corp_code, year=year)
    for row in alternative_rows:
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:1200]
        if row.get("section_key") == "kam":
            from kreports.processor.audit_report_parser import summarize_kam_body
            row["kam_analysis"] = summarize_kam_body(body)
        row.pop("body_text", None)
    if alternative_year is not None:
        _attach_related_audit_procedures(alternative_rows, corp_code=corp_code, year=alternative_year)
    if rows:
        coverage_note = (
            "Cached audit_report report_sections."
            if source_type == "audit_report"
            else "Cached report_sections."
        )
        if row_source == "evidence_documents":
            coverage_note = "Compact evidence_documents fallback parsed from normalized report evidence."
    else:
        coverage_note = (
            "No cached sections. Run collect-audit-report-sections for detailed audit reports; "
            "business_report is summary coverage only."
        )
    kam_hint_coverage = _kam_hint_coverage(rows)
    section_quality = {
        "status": "usable" if rows else "missing",
        "source": row_source,
        "requested_year": year,
        "requested_source_type": source_type,
        "requested_section_key": section_key,
        "section_count": len(rows),
        "kam_reason_coverage": kam_hint_coverage["reason"],
        "kam_procedure_coverage": kam_hint_coverage["procedure"],
        "available_audit_report_years": sorted(set(
            _cached_years_for_sections(corp_code, "audit_report", section_key)
            + _evidence_years_for_sections(corp_code, "audit_report", section_key)
        ), reverse=True),
        "available_business_report_years": sorted(set(
            _cached_years_for_sections(corp_code, "business_report", section_key)
            + _evidence_years_for_sections(corp_code, "business_report", section_key)
        ), reverse=True),
        "latest_available_year": alternative_year if not rows else year,
        "alternative_section_count": len(alternative_rows),
        "interpretation": (
            "No rows means the local cache lacks the requested section/year. "
            "It does not prove the filing lacks that audit report section."
        ),
    }
    result = {
        "subject": comp,
        "year": year,
        "section_key": section_key,
        "source_type": source_type,
        "section_count": len(rows),
        "sections": rows,
        "alternative_sections": alternative_rows,
        "data_quality": section_quality,
        "coverage_note": coverage_note,
    }
    result.update(_audit_report_sections_evidence(result))
    return _clean_dict(result)


_AUDIT_MATTER_KEYS = ("other_matter", "emphasis", "going_concern", "basis_for_opinion")


_AUDIT_MATTER_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "going_concern": ("계속기업", "존속능력", "유동부채", "유동성"),
    "covid": ("COVID-19", "코로나", "코로나바이러스"),
    "subsequent_event": ("보고기간후", "보고기간 후", "후속사건", "작성기준일 이후"),
    "restatement": ("재작성", "재작성", "정정", "재분류", "수정"),
    "litigation": ("소송", "분쟁", "우발부채"),
    "scope_limitation": ("범위제한", "충분하고 적합한 감사증거", "의견거절"),
    "uncertainty": ("불확실성", "추정", "중요한 불확실성"),
}


def _classify_audit_matter(text_value: str, section_key: str | None = None) -> dict:
    body = text_value or ""
    topics = [
        topic
        for topic, keywords in _AUDIT_MATTER_TOPIC_KEYWORDS.items()
        if any(keyword in body for keyword in keywords)
    ]
    if section_key == "going_concern" or "going_concern" in topics:
        severity = "high"
    elif section_key == "emphasis" or any(topic in topics for topic in ("scope_limitation", "uncertainty")):
        severity = "warning"
    else:
        severity = "info"
    return {"topic_tags": topics, "severity_hint": severity}


def search_audit_report_matters(
    *,
    company: str | None = None,
    year: int | None = None,
    market: str | None = None,
    induty_prefix: str | None = None,
    section_keys: list[str] | None = None,
    limit: int = 50,
    include_excerpt: bool = True,
) -> dict:
    """Search audit-report matters by company/year/industry filters.

    This backs questions like:
    - "Does company X have emphasis/other matter paragraphs?"
    - "Which companies in industry Y had emphasis/other matters in year Z?"
    """
    allowed_keys = set(_AUDIT_MATTER_KEYS)
    keys = section_keys or ["other_matter", "emphasis", "going_concern"]
    invalid = [key for key in keys if key not in allowed_keys]
    if invalid:
        return {
            "error": "invalid section_keys",
            "invalid": invalid,
            "allowed": sorted(allowed_keys),
        }
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    corp_code = None
    company_summary = None
    if company:
        corp_code = resolve_corp_code(company) or company
        company_summary = get_company_summary(corp_code)
        if not company_summary:
            return {"error": "company not found", "company": company}

    where = [
        "rs.source_type='audit_report'",
        "rs.section_key IN :section_keys",
    ]
    params: dict[str, object] = {"section_keys": keys}
    if corp_code:
        where.append("rs.corp_code=:corp_code")
        params["corp_code"] = corp_code
    if year is not None:
        where.append("rs.bsns_year=:year")
        params["year"] = int(year)
    if market:
        where.append("c.market=:market")
        params["market"] = market
    if induty_prefix:
        where.append("c.induty_code LIKE :induty_prefix")
        params["induty_prefix"] = f"{induty_prefix}%"

    params["row_limit"] = int(limit) * 10
    rows: list[dict] = []
    row_source = "audit_matter_items"
    if _has_db_table("audit_matter_items"):
        matter_where = [
            condition.replace("rs.section_key", "ami.matter_type").replace("rs.", "ami.")
            for condition in where
            if condition != "rs.source_type='audit_report'"
        ]
        matter_sql = text(
            f"""
            SELECT ami.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   ami.bsns_year, ami.rcept_no, ami.dcm_no, ami.matter_type AS section_key,
                   ami.matter_title AS section_title, ami.matter_text AS body_text,
                   ami.matter_length AS body_length, ami.section_ordinal AS ordinal,
                   ami.topic_tags, ami.severity_hint
            FROM audit_matter_items ami
            JOIN companies c ON c.corp_code=ami.corp_code
            WHERE {" AND ".join(matter_where)}
            ORDER BY ami.bsns_year DESC, c.market, c.induty_code, c.corp_name, ami.matter_type, ami.section_ordinal
            LIMIT :row_limit
            """
        ).bindparams(bindparam("section_keys", expanding=True))
        with _engine_module.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(matter_sql, params).mappings().all()]

    if not rows:
        dcm_select = "rs.dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
        sql = text(
            f"""
            SELECT rs.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   rs.bsns_year, rs.rcept_no, {dcm_select}, rs.section_key,
                   rs.section_title, rs.body_text, rs.body_length, rs.ordinal
            FROM report_sections rs
            JOIN companies c ON c.corp_code=rs.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY rs.bsns_year DESC, c.market, c.induty_code, c.corp_name, rs.section_key, rs.ordinal
            LIMIT :row_limit
            """
        ).bindparams(bindparam("section_keys", expanding=True))

        with _engine_module.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).mappings().all()]
        row_source = "report_sections.audit_report"
    if not rows:
        company_where = ["1=1"]
        company_params: dict[str, object] = {}
        if corp_code:
            company_where.append("corp_code=:corp_code")
            company_params["corp_code"] = corp_code
        if market:
            company_where.append("market=:market")
            company_params["market"] = market
        if induty_prefix:
            company_where.append("induty_code LIKE :induty_prefix")
            company_params["induty_prefix"] = f"{induty_prefix}%"
        with _engine_module.engine.connect() as conn:
            corp_codes = [str(r) for r in conn.execute(
                text(
                    f"""
                    SELECT corp_code
                    FROM companies
                    WHERE {" AND ".join(company_where)}
                    ORDER BY market, induty_code, corp_name
                    LIMIT :corp_limit
                    """
                ),
                {**company_params, "corp_limit": max(int(limit) * 20, 100)},
            ).scalars().all()]
        if year is not None:
            rows = _evidence_report_section_rows(
                corp_codes=corp_codes,
                year=int(year),
                source_types=["audit_report"],
                section_keys=keys,
                limit=int(limit) * 10,
            )
            if rows:
                row_source = "evidence_documents"

    companies: dict[str, dict] = {}
    for row in rows:
        cc = row["corp_code"]
        item = companies.setdefault(cc, {
            "corp_code": cc,
            "stock_code": row.get("stock_code"),
            "corp_name": row.get("corp_name"),
            "market": row.get("market"),
            "induty_code": row.get("induty_code"),
            "industry_name": get_industry_name((row.get("induty_code") or "")[:2]) if row.get("induty_code") else "",
            "years": [],
            "matter_counts": {key: 0 for key in keys},
            "sections": [],
        })
        if row["bsns_year"] not in item["years"]:
            item["years"].append(row["bsns_year"])
        item["matter_counts"][row["section_key"]] = item["matter_counts"].get(row["section_key"], 0) + 1
        section = {
            "bsns_year": row["bsns_year"],
            "rcept_no": row["rcept_no"],
            "dcm_no": row.get("dcm_no"),
            "section_key": row["section_key"],
            "section_title": row.get("section_title"),
            "body_length": row.get("body_length"),
        }
        if include_excerpt:
            body = _display_text(row.get("body_text"))
            section["body_excerpt"] = body[:1200]
            section.update(_classify_audit_matter(body, row["section_key"]))
        item["sections"].append(section)

    company_rows = list(companies.values())
    for item in company_rows:
        item["years"] = sorted([int(y) for y in item["years"]], reverse=True)
        item["total_sections"] = sum(item["matter_counts"].values())
        item["sections"] = item["sections"][:10]
    company_rows.sort(
        key=lambda item: (
            -item["total_sections"],
            item.get("market") or "",
            item.get("corp_name") or "",
        )
    )
    company_rows = company_rows[:limit]

    result = {
        "query": {
            "company": company,
            "year": year,
            "market": market,
            "induty_prefix": induty_prefix,
            "section_keys": keys,
            "limit": limit,
            "include_excerpt": include_excerpt,
        },
        "subject": company_summary,
        "total_companies": len(company_rows),
        "total_sections": sum(item["total_sections"] for item in company_rows),
        "companies": company_rows,
        "data_quality": {
            "status": "usable" if company_rows else "missing",
            "source": row_source,
            "interpretation": (
                "Results are local cached audit-report sections. Empty results mean no cached matching section, "
                "not proof that the filing has no such matter."
            ),
        },
    }
    result.update(_audit_matters_evidence(result))
    return _clean_dict(result)


def get_kam_lifecycle(company: str, start_year: int = 2021, end_year: int = 2025) -> dict:
    """Return 5-year KAM lifecycle for a resolved company identifier."""
    from kreports.analysis.kam_lifecycle import kam_lifecycle_for_company

    corp_code = resolve_corp_code(company) or company
    subject = get_company_summary(corp_code)
    if not subject:
        return {"error": "company not found", "company": company}
    result = kam_lifecycle_for_company(corp_code, start_year=start_year, end_year=end_year)
    result["subject"] = subject
    return _clean_dict(result)


def get_accounting_policy_changes(
    company: str,
    start_year: int = 2021,
    end_year: int = 2025,
    fs_div: str | None = None,
) -> dict:
    """Return note 2/3/4 accounting policy and estimate text-change hints."""
    from kreports.analysis.policy_changes import accounting_policy_changes

    corp_code = resolve_corp_code(company) or company
    subject = get_company_summary(corp_code)
    if not subject:
        return {"error": "company not found", "company": company}
    result = accounting_policy_changes(
        corp_code,
        start_year=start_year,
        end_year=end_year,
        fs_div=fs_div,
    )
    result["subject"] = subject
    return _clean_dict(result)


_AUDIT_PROCEDURE_TYPES = {
    "internal_control",
    "substantive_test",
    "estimation_assumption",
    "external_confirmation",
    "valuation_specialist",
    "analytics",
    "cutoff",
    "other",
}


_AUDIT_PROCEDURE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "internal_control": ("내부통제", "통제", "통제활동"),
    "substantive_test": ("문서검사", "표본", "입증", "증빙", "거래검사"),
    "estimation_assumption": ("추정", "가정", "민감도", "회수가능", "평가"),
    "external_confirmation": ("외부조회", "조회", "확인서"),
    "valuation_specialist": ("전문가", "가치평가", "평가기관"),
    "analytics": ("분석적", "추세", "비교분석"),
    "cutoff": ("기간귀속", "cut-off", "컷오프"),
}


def _classify_audit_procedure_type(text_value: str) -> str:
    body = text_value or ""
    for procedure_type, keywords in _AUDIT_PROCEDURE_KEYWORDS.items():
        if any(keyword in body for keyword in keywords):
            return procedure_type
    return "other"


def _procedure_excerpt_from_kam(body: str) -> str:
    text_value = _display_text(body)
    candidates = [
        item.strip()
        for item in re.split(r"\n+|(?<=\.)\s+|(?<=!)\s+|(?<=\?)\s+|(?<=。)\s+|(?<=다\.)\s+", text_value)
        if item.strip()
    ]
    for candidate in candidates:
        if any(keyword in candidate for keywords in _AUDIT_PROCEDURE_KEYWORDS.values() for keyword in keywords):
            return candidate.strip()
    if "수행" in text_value or "검토" in text_value:
        return text_value[:900].strip()
    return ""


def _evidence_audit_procedure_rows(
    *,
    corp_codes: list[str],
    year: int,
    keyword: str | None = None,
    kam_topic: str | None = None,
    procedure_type: str | None = None,
    limit: int = 500,
) -> list[dict]:
    from kreports.processor.audit_report_parser import classify_kam_topics

    section_rows = _evidence_report_section_rows(
        corp_codes=corp_codes,
        year=year,
        source_types=["audit_report"],
        section_keys=["kam"],
        limit=limit,
    )
    out: list[dict] = []
    for row in section_rows:
        body = _display_text(row.get("body_text"))
        excerpt = _procedure_excerpt_from_kam(body)
        if not excerpt:
            continue
        topics = classify_kam_topics(body) or _topic_hits(body)
        topic = topics[0] if topics else "unknown"
        ptype = _classify_audit_procedure_type(excerpt)
        if kam_topic and topic != kam_topic:
            continue
        if procedure_type and ptype != procedure_type:
            continue
        if keyword and keyword not in excerpt and keyword not in body:
            continue
        out.append({
            "corp_code": row["corp_code"],
            "stock_code": row.get("stock_code"),
            "corp_name": row.get("corp_name"),
            "market": row.get("market"),
            "induty_code": row.get("induty_code"),
            "year": row["bsns_year"],
            "rcept_no": row["rcept_no"],
            "dcm_no": row.get("dcm_no"),
            "source_type": "audit_report",
            "kam_topic": topic,
            "procedure_type": ptype,
            "procedure_text": excerpt,
            "procedure_length": len(excerpt),
            "section_ordinal": row.get("ordinal", 0),
            "procedure_ordinal": 0,
        })
        if len(out) >= limit:
            break
    return out


def _full_body_kam_procedure_rows(
    *,
    corp_codes: list[str],
    year: int,
    keyword: str | None = None,
    kam_topic: str | None = None,
    procedure_type: str | None = None,
    method: str | None = None,
    limit: int = 500,
    _connection=None,
) -> list[dict]:
    """Parse only reconstructed full-body KAMs without persisting rows."""
    if not corp_codes:
        return []
    if _connection is None:
        with procedure_read_connection() as connection:
            return _full_body_kam_procedure_rows(
                corp_codes=corp_codes,
                year=year,
                keyword=keyword,
                kam_topic=kam_topic,
                procedure_type=procedure_type,
                method=method,
                limit=limit,
                _connection=connection,
            )
    from kreports.processor.audit_procedure_parser import (
        extract_procedure_steps,
        legacy_procedure_type,
    )
    from kreports.processor.kam_parser import ParsedKamItem

    stmt = text("""
        SELECT ki.id AS kam_item_id, ki.corp_code, c.stock_code, c.corp_name,
               c.market, c.induty_code, ki.bsns_year, ki.rcept_no, ki.dcm_no,
               ki.source_type, ki.ordinal, ki.title, ki.normalized_topic,
               ki.reason_text, ki.audit_response_text,
               ki.related_note_references_json, ki.full_body_hash,
               ki.full_body_length, ki.parser_version AS kam_parser_version,
               ki.quality_status
        FROM kam_items ki
        JOIN companies c ON c.corp_code=ki.corp_code
        WHERE ki.bsns_year=:year
          AND ki.quality_status='full_body'
          AND ki.corp_code IN :corp_codes
        ORDER BY c.market, c.corp_name, ki.rcept_no, ki.ordinal
    """).bindparams(bindparam("corp_codes", expanding=True))
    kam_rows = [
        dict(row)
        for row in _connection.execute(
            stmt,
            {"year": int(year), "corp_codes": corp_codes},
        ).mappings().all()
    ]

    out: list[dict] = []
    for row in kam_rows:
        response = str(row.get("audit_response_text") or "")
        full_body = "\n".join(
            value
            for value in (
                str(row.get("title") or ""),
                str(row.get("reason_text") or ""),
                response,
            )
            if value
        )
        item = ParsedKamItem(
            ordinal=int(row.get("ordinal") or 0),
            title=str(row.get("title") or ""),
            normalized_topic=row.get("normalized_topic"),
            reason_text=row.get("reason_text"),
            audit_response_text=response or None,
            related_note_references=[],
            full_body=full_body,
            full_body_hash=str(row.get("full_body_hash") or ""),
            full_body_length=int(row.get("full_body_length") or len(full_body)),
            quality_status="full_body",
            parser_version=str(row.get("kam_parser_version") or "v1"),
        )
        for step in extract_procedure_steps(item):
            legacy_type = legacy_procedure_type(step.method)
            if kam_topic and row.get("normalized_topic") != kam_topic:
                continue
            if procedure_type and legacy_type != procedure_type:
                continue
            if method and step.method != method:
                continue
            if keyword and keyword not in step.procedure_text:
                continue
            out.append(
                {
                    "corp_code": row["corp_code"],
                    "stock_code": row.get("stock_code"),
                    "corp_name": row.get("corp_name"),
                    "market": row.get("market"),
                    "induty_code": row.get("induty_code"),
                    "year": row["bsns_year"],
                    "rcept_no": row["rcept_no"],
                    "dcm_no": row.get("dcm_no"),
                    "source_type": row.get("source_type"),
                    "kam_topic": row.get("normalized_topic"),
                    "method": step.method,
                    "procedure_type": legacy_type,
                    "procedure_text": step.procedure_text,
                    "procedure_length": len(step.procedure_text),
                    "section_ordinal": row.get("ordinal", 0),
                    "procedure_ordinal": step.ordinal,
                    "assertion_hints_json": json.dumps(
                        step.assertion_hints,
                        ensure_ascii=False,
                    ),
                    "linked_metric_keys_json": json.dumps(
                        step.linked_metric_keys,
                        ensure_ascii=False,
                    ),
                    "linked_note_keys_json": json.dumps(
                        step.linked_note_keys,
                        ensure_ascii=False,
                    ),
                    "linked_event_keys_json": json.dumps(
                        step.linked_event_keys,
                        ensure_ascii=False,
                    ),
                    "parser_version": step.parser_version,
                    "quality_status": step.quality_status,
                    "kam_item_id": row["kam_item_id"],
                    "source_kam_title": row.get("title"),
                    "source_kam_hash": row.get("full_body_hash"),
                    "source_kam_quality_status": "full_body",
                }
            )
            if len(out) >= limit:
                return out
    return out


def search_audit_procedures(
    *,
    company: str | None = None,
    year: int | None = None,
    market: str | None = None,
    induty_prefix: str | None = None,
    kam_topic: str | None = None,
    procedure_type: str | None = None,
    method: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    include_excerpt: bool = True,
) -> dict:
    """Search KAM audit procedures by company/year/industry/topic filters."""
    from kreports.processor.audit_procedure_parser import PROCEDURE_METHODS

    if procedure_type and procedure_type not in _AUDIT_PROCEDURE_TYPES:
        return {"error": "invalid procedure_type", "allowed": sorted(_AUDIT_PROCEDURE_TYPES)}
    allowed_methods = set(PROCEDURE_METHODS)
    if method and method not in allowed_methods:
        return {"error": "invalid method", "allowed": sorted(allowed_methods)}
    available, unavailable_reason = procedure_database_preflight()
    if not available:
        return {
            "query": {
                "company": company,
                "year": year,
                "market": market,
                "induty_prefix": induty_prefix,
                "kam_topic": kam_topic,
                "procedure_type": procedure_type,
                "method": method,
                "keyword": keyword,
                "limit": limit,
                "include_excerpt": include_excerpt,
            },
            "total_companies": 0,
            "total_procedures": 0,
            "companies": [],
            "data_quality": {
                "status": "unavailable",
                "source": "runtime_db",
                "interpretation": unavailable_reason,
            },
        }
    limit = max(1, min(int(limit), 500))
    params: dict[str, object] = {"row_limit": limit * 10}
    filters: list[str] = []
    subject = None
    if company:
        with procedure_read_connection() as conn:
            subject_row = conn.execute(
                text(
                    """
                    SELECT corp_code, stock_code, corp_name, market,
                           induty_code
                    FROM companies
                    WHERE corp_code=:company
                       OR stock_code=:company
                       OR corp_name=:company
                       OR corp_name LIKE :company_like
                    ORDER BY
                        CASE
                            WHEN corp_code=:company THEN 0
                            WHEN stock_code=:company THEN 1
                            WHEN corp_name=:company THEN 2
                            ELSE 3
                        END,
                        corp_name
                    LIMIT 1
                    """
                ),
                {
                    "company": company,
                    "company_like": f"%{company}%",
                },
            ).mappings().first()
        if subject_row is None:
            return {"error": "company not found", "company": company}
        subject = dict(subject_row)
        filters.append("c.corp_code=:corp_code")
        params["corp_code"] = subject["corp_code"]
    if market:
        filters.append("c.market=:market")
        params["market"] = market
    if induty_prefix:
        filters.append("c.induty_code LIKE :induty_prefix")
        params["induty_prefix"] = f"{induty_prefix}%"
    where = ["1=1", *filters]
    if year is not None:
        where.append("api.bsns_year=:year")
        params["year"] = int(year)
    if kam_topic:
        where.append("api.kam_topic=:kam_topic")
        params["kam_topic"] = kam_topic
    if procedure_type:
        where.append("api.procedure_type=:procedure_type")
        params["procedure_type"] = procedure_type
    if method:
        where.append("api.method=:method")
        params["method"] = method
    if keyword:
        where.append("api.procedure_text LIKE :kw")
        params["kw"] = f"%{keyword}%"

    sql = text(f"""
        SELECT api.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
               api.bsns_year AS year, api.rcept_no, api.dcm_no, api.source_type,
               api.kam_topic, api.method, api.procedure_type,
               api.procedure_text, api.procedure_length, api.section_ordinal,
               api.procedure_ordinal, api.assertion_hints_json,
               api.linked_metric_keys_json, api.linked_note_keys_json,
               api.linked_event_keys_json, api.parser_version,
               api.quality_status, api.kam_item_id,
               ki.title AS source_kam_title,
               ki.full_body_hash AS source_kam_hash,
               ki.quality_status AS source_kam_quality_status
        FROM audit_procedure_items api
        JOIN companies c ON c.corp_code=api.corp_code
        LEFT JOIN kam_items ki ON ki.id=api.kam_item_id
        WHERE {" AND ".join(where)}
        ORDER BY api.bsns_year DESC, c.market, c.corp_name, api.kam_topic, api.procedure_type
        LIMIT :row_limit
    """)
    with procedure_read_connection() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).mappings().all()]
    row_source = "audit_procedure_items"
    if not rows and year is not None:
        company_where = ["1=1"]
        company_params: dict[str, object] = {}
        if company:
            company_where.append("corp_code=:corp_code")
            company_params["corp_code"] = subject["corp_code"]
        if market:
            company_where.append("market=:market")
            company_params["market"] = market
        if induty_prefix:
            company_where.append("induty_code LIKE :induty_prefix")
            company_params["induty_prefix"] = f"{induty_prefix}%"
        with procedure_read_connection() as conn:
            corp_codes = [str(r) for r in conn.execute(
                text(
                    f"""
                    SELECT corp_code
                    FROM companies
                    WHERE {" AND ".join(company_where)}
                    ORDER BY market, induty_code, corp_name
                    LIMIT :corp_limit
                    """
                ),
                {**company_params, "corp_limit": max(limit * 20, 100)},
            ).scalars().all()]
            rows = _full_body_kam_procedure_rows(
                corp_codes=corp_codes,
                year=int(year),
                keyword=keyword,
                kam_topic=kam_topic,
                procedure_type=procedure_type,
                method=method,
                limit=limit * 10,
                _connection=conn,
            )
        if rows:
            row_source = "kam_items.full_body"

    for row in rows:
        text_value = _display_text(row.pop("procedure_text") or "")
        row["procedure_text"] = text_value
        if include_excerpt:
            row["procedure_excerpt"] = text_value[:900]
        for source_key, output_key in (
            ("assertion_hints_json", "assertion_hints"),
            ("linked_metric_keys_json", "linked_metric_keys"),
            ("linked_note_keys_json", "linked_note_keys"),
            ("linked_event_keys_json", "linked_event_keys"),
        ):
            raw = row.pop(source_key, None)
            try:
                row[output_key] = list(json.loads(raw)) if raw else []
            except (TypeError, ValueError):
                row[output_key] = []
        kam_item_id = row.pop("kam_item_id", None)
        source_kam_title = row.pop("source_kam_title", None)
        source_kam_hash = row.pop("source_kam_hash", None)
        source_kam_quality = row.pop("source_kam_quality_status", None)
        if kam_item_id is not None:
            row["source_kam"] = {
                "id": kam_item_id,
                "title": source_kam_title,
                "full_body_hash": source_kam_hash,
                "quality_status": source_kam_quality,
                "rcept_no": row.get("rcept_no"),
                "dcm_no": row.get("dcm_no"),
            }
        row["linkages"] = classify_audit_procedure_linkages(
            text_value,
            kam_topic=row.get("kam_topic"),
        )
    companies = group_company_records(rows, limit=limit)
    type_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for company_row in companies:
        for record in company_row["records"]:
            type_key = record.get("procedure_type") or "unknown"
            topic_key = record.get("kam_topic") or "unknown"
            type_counts[type_key] = type_counts.get(type_key, 0) + 1
            topic_counts[topic_key] = topic_counts.get(topic_key, 0) + 1
    result = {
        "query": {
            "company": company,
            "year": year,
            "market": market,
            "induty_prefix": induty_prefix,
            "kam_topic": kam_topic,
            "procedure_type": procedure_type,
            "method": method,
            "keyword": keyword,
            "limit": limit,
            "include_excerpt": include_excerpt,
        },
        "subject": subject,
        "total_companies": len(companies),
        "total_procedures": sum(item["record_count"] for item in companies),
        "procedure_type_counts": type_counts,
        "kam_topic_counts": topic_counts,
        "companies": companies,
        "data_quality": {
            "status": "usable" if companies else "missing",
            "source": row_source,
            "interpretation": (
                "Procedure items are parsed hints from cached audit-report KAM response paragraphs. "
                "The linkages explain which audit-report KAM, financial-statement account, "
                "accounting note, or disclosure-event evidence should be checked with the procedure. "
                "Each linkage is a navigation aid, not evidence that the audit procedure was "
                "sufficient or appropriately performed."
            ),
        },
    }
    result.update(_audit_procedures_evidence(result))
    return _clean_dict(result)


# Stable audit-internal interfaces consumed by peer benchmarking.
AUDIT_MATTER_KEYS = _AUDIT_MATTER_KEYS
KAM_TOPIC_KEYWORDS = _KAM_TOPIC_KEYWORDS
cache_quality_status = _cache_quality_status
cached_years_for_sections = _cached_years_for_sections
classify_audit_matter = _classify_audit_matter
evidence_audit_procedure_rows = _evidence_audit_procedure_rows
full_body_kam_procedure_rows = _full_body_kam_procedure_rows
evidence_report_section_rows = _evidence_report_section_rows
evidence_years_for_sections = _evidence_years_for_sections
kam_hint_coverage = _kam_hint_coverage
topic_hits = _topic_hits
