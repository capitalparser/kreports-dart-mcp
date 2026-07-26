"""Evidence mapping for KAM audit procedures.

The functions in this module are deliberately read-only. They diagnose whether
cached audit-report KAM sections and parsed procedure rows can support MCP
answers, and they explain which disclosure materials should be checked for a
procedure.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, inspect, text

import kreports.db.engine as engine_module
from kreports.processor.audit_procedure_parser import ParsedProcedureStep


@dataclass(frozen=True)
class EvidenceLink:
    category: str
    key: str
    label: str
    matching_phrase: str
    confidence_basis: str


class ProcedureDatabaseUnavailable(RuntimeError):
    """Procedure read surface cannot safely inspect the runtime database."""


def _validate_procedure_schema(
    read_engine,
    required_tables: set[str] | None = None,
) -> None:
    required = (
        {
            "companies",
            "kam_items",
            "audit_procedure_items",
        }
        if required_tables is None
        else required_tables
    )
    try:
        inspector = inspect(read_engine)
        tables = set(inspector.get_table_names())
    except Exception as exc:
        raise ProcedureDatabaseUnavailable(
            f"runtime_db_unavailable:{type(exc).__name__}"
        ) from exc
    missing = sorted(required - tables)
    if missing:
        raise ProcedureDatabaseUnavailable(
            f"missing_schema:{','.join(missing)}"
        )
    if "audit_procedure_items" not in required:
        return
    try:
        columns = {
            str(column["name"])
            for column in inspector.get_columns("audit_procedure_items")
        }
    except Exception as exc:
        raise ProcedureDatabaseUnavailable(
            f"runtime_db_unavailable:{type(exc).__name__}"
        ) from exc
    required_columns = {
        "kam_item_id",
        "method",
        "assertion_hints_json",
        "linked_metric_keys_json",
        "linked_note_keys_json",
        "linked_event_keys_json",
        "parser_version",
        "quality_status",
    }
    missing_columns = sorted(required_columns - columns)
    if missing_columns:
        raise ProcedureDatabaseUnavailable(
            f"missing_columns:{','.join(missing_columns)}"
        )


@contextmanager
def procedure_read_engine(required_tables: set[str] | None = None):
    """Yield a schema-checked engine without creating SQLite sidecars."""
    source_engine = engine_module.engine
    if source_engine.dialect.name == "sqlite":
        database = source_engine.url.database
        if database not in {None, "", ":memory:"}:
            database_path = Path(str(database)).expanduser().resolve()
            if not database_path.is_file():
                raise ProcedureDatabaseUnavailable(
                    "runtime_db_unavailable"
                )
            wal_path = Path(f"{database_path}-wal")
            if wal_path.exists() and wal_path.stat().st_size > 0:
                raise ProcedureDatabaseUnavailable(
                    "runtime_db_unavailable:uncheckpointed_wal"
                )
            readonly_engine = create_engine(
                (
                    f"sqlite:///file:{database_path.as_posix()}"
                    "?mode=ro&immutable=1&uri=true"
                ),
                connect_args={"check_same_thread": False},
            )
            try:
                _validate_procedure_schema(
                    readonly_engine,
                    required_tables,
                )
                yield readonly_engine
            finally:
                readonly_engine.dispose()
            return
    _validate_procedure_schema(source_engine, required_tables)
    yield source_engine


@contextmanager
def procedure_read_connection(required_tables: set[str] | None = None):
    """Yield one actual read connection using the safe procedure engine."""
    with procedure_read_engine(required_tables) as read_engine:
        with read_engine.connect() as connection:
            yield connection


def procedure_database_preflight(
    required_tables: set[str] | None = None,
) -> tuple[bool, str | None]:
    """Check procedure schema without creating a missing SQLite database."""
    try:
        with procedure_read_engine(required_tables):
            pass
    except ProcedureDatabaseUnavailable as exc:
        return False, str(exc)
    return True, None


def link_procedure_evidence(
    step: ParsedProcedureStep,
    semantic_registry: Any,
) -> list[EvidenceLink]:
    body = step.procedure_text or ""
    metric_map: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("revenue", ("매출", "수익", "계약서", "revenue")),
        ("trade_receivables", ("매출채권", "수취채권", "receivable")),
        ("inventories", ("재고", "inventory")),
        ("cash_and_equivalents", ("현금및현금성", "현금성자산")),
        ("interest_bearing_debt", ("차입금", "사채", "이자부부채")),
        ("tax_expense", ("법인세", "tax")),
        ("assets", ("자산", "손상", "공정가치")),
        ("liabilities", ("부채", "충당부채")),
    )
    note_map: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("revenue_policy", ("매출", "수익", "계약서")),
        ("inventory_policy", ("재고",)),
        ("impairment_assumption", ("손상", "할인율", "현금흐름")),
        ("fair_value_hierarchy", ("공정가치", "가치평가")),
        ("contingency", ("충당부채", "우발", "소송")),
        ("income_tax", ("법인세",)),
    )
    event_map: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
        (key, keywords) for key, keywords in _DISCLOSURE_EVENT_HINTS.items()
    )

    links: list[EvidenceLink] = []
    for key, phrases in metric_map:
        phrase = next((value for value in phrases if value.lower() in body.lower()), None)
        if phrase is None or key not in semantic_registry:
            continue
        definition = semantic_registry[key]
        links.append(
            EvidenceLink(
                category="metric",
                key=key,
                label=definition.label_ko,
                matching_phrase=phrase,
                confidence_basis="explicit_keyword_registry_match",
            )
        )
    for key, phrases in note_map:
        phrase = next((value for value in phrases if value.lower() in body.lower()), None)
        if phrase is not None:
            links.append(
                EvidenceLink(
                    category="note",
                    key=key,
                    label=f"회계주석: {key}",
                    matching_phrase=phrase,
                    confidence_basis="explicit_keyword_map_match",
                )
            )
    for key, phrases in event_map:
        phrase = next((value for value in phrases if value.lower() in body.lower()), None)
        if phrase is not None:
            links.append(
                EvidenceLink(
                    category="event",
                    key=key,
                    label=f"공시 이벤트: {key}",
                    matching_phrase=phrase,
                    confidence_basis="explicit_keyword_map_match",
                )
            )
    return links


_TOPIC_TO_LINKS: dict[str, list[dict[str, str]]] = {
    "revenue": [
        {"category": "audit_report_kam", "key": "revenue", "label": "KAM: 수익인식"},
        {"category": "financial_statement_account", "key": "revenue", "label": "재무제표: 매출액"},
        {"category": "accounting_note", "key": "revenue_policy", "label": "주석: 수익인식 회계정책"},
    ],
    "inventory": [
        {"category": "audit_report_kam", "key": "inventory", "label": "KAM: 재고자산"},
        {"category": "financial_statement_account", "key": "inventories", "label": "재무제표: 재고자산"},
        {"category": "accounting_note", "key": "inventory_policy", "label": "주석: 재고자산 평가정책"},
    ],
    "impairment": [
        {"category": "audit_report_kam", "key": "impairment", "label": "KAM: 손상검사"},
        {"category": "financial_statement_account", "key": "assets", "label": "재무제표: 손상 관련 자산"},
        {"category": "accounting_note", "key": "impairment_assumption", "label": "주석: 회수가능액 및 주요 가정"},
    ],
    "fair_value": [
        {"category": "audit_report_kam", "key": "fair_value", "label": "KAM: 공정가치"},
        {"category": "financial_statement_account", "key": "assets", "label": "재무제표: 공정가치 측정 항목"},
        {"category": "accounting_note", "key": "fair_value_hierarchy", "label": "주석: 공정가치 서열체계"},
    ],
    "provision": [
        {"category": "audit_report_kam", "key": "provision", "label": "KAM: 충당부채/우발부채"},
        {"category": "financial_statement_account", "key": "liabilities", "label": "재무제표: 충당부채"},
        {"category": "accounting_note", "key": "contingency", "label": "주석: 우발부채 및 약정사항"},
    ],
    "consolidation": [
        {"category": "audit_report_kam", "key": "consolidation", "label": "KAM: 연결/종속기업"},
        {"category": "financial_statement_account", "key": "assets", "label": "재무제표: 종속기업 투자"},
        {"category": "accounting_note", "key": "consolidation_scope", "label": "주석: 연결범위"},
    ],
    "tax": [
        {"category": "audit_report_kam", "key": "tax", "label": "KAM: 법인세"},
        {"category": "financial_statement_account", "key": "tax_expense", "label": "재무제표: 법인세"},
        {"category": "accounting_note", "key": "income_tax", "label": "주석: 법인세"},
    ],
}

_TEXT_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "revenue": ("매출", "수익", "기간귀속", "계약서", "수행의무"),
    "inventory": ("재고", "순실현가능가치", "평가충당"),
    "impairment": ("손상", "회수가능", "현금창출단위", "할인율", "미래현금흐름"),
    "fair_value": ("공정가치", "가치평가", "평가기법", "외부평가기관"),
    "provision": ("충당부채", "우발", "소송", "복구충당"),
    "consolidation": ("연결", "종속기업", "사업결합", "지배력"),
    "tax": ("법인세", "이연법인세", "세무조사"),
}

_DISCLOSURE_EVENT_HINTS: dict[str, tuple[str, ...]] = {
    "auditor_change": ("감사인", "교체", "지정감사"),
    "capital_market_event": ("유상증자", "전환사채", "신주인수권", "사채"),
    "business_combination": ("합병", "분할", "양수", "양도", "사업결합"),
    "litigation": ("소송", "중재", "분쟁"),
}


def _dedupe_links(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (row["category"], row["key"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def classify_audit_procedure_linkages(text_value: str, kam_topic: str | None = None) -> list[dict[str, str]]:
    """Map a procedure sentence to the evidence layers a user should inspect."""
    body = text_value or ""
    topics: list[str] = []
    if kam_topic:
        topics.append(kam_topic)
    for topic, keywords in _TEXT_TOPIC_KEYWORDS.items():
        if any(keyword in body for keyword in keywords):
            topics.append(topic)

    links: list[dict[str, str]] = []
    for topic in topics:
        for row in _TOPIC_TO_LINKS.get(topic, []):
            keywords = _TEXT_TOPIC_KEYWORDS.get(topic, ())
            matching_phrase = next(
                (keyword for keyword in keywords if keyword in body),
                f"kam_topic:{topic}",
            )
            links.append(
                {
                    **row,
                    "matching_phrase": matching_phrase,
                    "confidence_basis": (
                        "explicit_keyword_map_match"
                        if not matching_phrase.startswith("kam_topic:")
                        else "source_kam_topic_match"
                    ),
                }
            )

    for event_key, keywords in _DISCLOSURE_EVENT_HINTS.items():
        if any(keyword in body for keyword in keywords):
            links.append({
                "category": "disclosure_event",
                "key": event_key,
                "label": f"수시공시 이벤트: {event_key}",
                "matching_phrase": next(
                    keyword for keyword in keywords if keyword in body
                ),
                "confidence_basis": "explicit_keyword_map_match",
            })
    return _dedupe_links(links)


def _resolve_company_filter(company: str | None) -> tuple[str, dict[str, Any]]:
    if not company:
        return "", {}
    return (
        "AND (c.corp_code=:company OR c.stock_code=:company OR c.corp_name LIKE :company_like)",
        {"company": company, "company_like": f"%{company}%"},
    )


def build_audit_procedure_evidence_map(
    *,
    year: int,
    company: str | None = None,
    market: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Return a quality map for procedure extraction and evidence linkage.

    This is a diagnostic surface. It does not fetch DART and it does not write
    rows back to SQLite.
    """
    available, unavailable_reason = procedure_database_preflight(
        {
            "companies",
            "kam_items",
            "audit_procedure_items",
            "report_sections",
        }
    )
    if not available:
        return {
            "verdict": "unavailable",
            "database_status": "unavailable",
            "database_reason": unavailable_reason,
            "year": int(year),
            "company": company,
            "market": market,
            "counts": {},
            "sample": {},
            "rates": {},
            "quality_gaps": {},
            "required_gaps": ["procedure_database_unavailable"],
            "missing_procedure_kams": [],
            "samples": [],
            "data_quality": {
                "status": "unavailable",
                "source": "runtime_db",
                "note": unavailable_reason,
            },
        }

    company_filter, params = _resolve_company_filter(company)
    market_filter = "AND c.market=:market" if market else ""
    if market:
        params["market"] = market
    params["year"] = int(year)
    params["limit"] = max(1, min(int(limit), 500))

    with procedure_read_connection(
        {
            "companies",
            "kam_items",
            "audit_procedure_items",
            "report_sections",
        }
    ) as conn:
        structured_kam_summary = dict(
            conn.execute(
                text(f"""
                    SELECT
                        COUNT(*) AS kam_items,
                        COALESCE(SUM(
                            CASE WHEN ki.quality_status='full_body' THEN 1 ELSE 0 END
                        ), 0) AS full_body_kam_items,
                        COALESCE(SUM(
                            CASE
                                WHEN ki.quality_status='full_body'
                                 AND EXISTS (
                                    SELECT 1
                                    FROM audit_procedure_items api
                                    WHERE api.kam_item_id=ki.id
                                 )
                                THEN 1 ELSE 0
                            END
                        ), 0) AS full_body_kam_items_with_procedures,
                        COUNT(DISTINCT CASE
                            WHEN ki.quality_status='full_body'
                            THEN ki.rcept_no || '|' || ki.source_type
                        END) AS full_body_kam_receipts,
                        COUNT(DISTINCT CASE
                            WHEN ki.quality_status='full_body'
                             AND EXISTS (
                                SELECT 1
                                FROM audit_procedure_items api
                                WHERE api.kam_item_id=ki.id
                             )
                            THEN ki.rcept_no || '|' || ki.source_type
                        END) AS full_body_kam_receipts_with_procedures,
                        COUNT(DISTINCT CASE
                            WHEN ki.quality_status='summary_only'
                            THEN ki.rcept_no || '|' || ki.source_type
                        END) AS summary_only,
                        COUNT(DISTINCT CASE
                            WHEN ki.quality_status='missing'
                            THEN ki.rcept_no || '|' || ki.source_type
                        END) AS missing,
                        COUNT(DISTINCT CASE
                            WHEN ki.quality_status='error'
                            THEN ki.rcept_no || '|' || ki.source_type
                        END) AS error
                    FROM kam_items ki
                    JOIN companies c ON c.corp_code=ki.corp_code
                    WHERE ki.bsns_year=:year
                      {company_filter}
                      {market_filter}
                """),
                params,
            ).mappings().first()
            or {}
        )
        missing_procedure_kams = [
            dict(row)
            for row in conn.execute(
                text(f"""
                    SELECT ki.id AS kam_item_id, ki.corp_code, c.stock_code,
                           c.corp_name, c.market, c.induty_code,
                           ki.bsns_year AS year, ki.rcept_no, ki.dcm_no,
                           ki.title, ki.normalized_topic, ki.quality_status
                    FROM kam_items ki
                    JOIN companies c ON c.corp_code=ki.corp_code
                    WHERE ki.bsns_year=:year
                      AND ki.quality_status='full_body'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM audit_procedure_items api
                          WHERE api.kam_item_id=ki.id
                      )
                      {company_filter}
                      {market_filter}
                    ORDER BY c.market, c.corp_name, ki.rcept_no, ki.ordinal
                    LIMIT :limit
                """),
                params,
            ).mappings().all()
        ]
        kam_summary = dict(
            conn.execute(
                text(f"""
                    SELECT
                        COUNT(*) AS kam_sections,
                        COALESCE(SUM(
                            CASE
                                WHEN COALESCE(rs.body_length, LENGTH(rs.body_text)) < 300 THEN 1
                                ELSE 0
                            END
                        ), 0) AS short_kam_sections
                    FROM report_sections rs
                    JOIN companies c ON c.corp_code=rs.corp_code
                    WHERE rs.bsns_year=:year
                      AND rs.source_type='audit_report'
                      AND rs.section_key='kam'
                      {company_filter}
                      {market_filter}
                """),
                params,
            ).mappings().first()
            or {}
        )
        procedure_summary = dict(
            conn.execute(
                text(f"""
                    SELECT
                        COUNT(*) AS procedure_items,
                        COUNT(DISTINCT api.rcept_no) AS procedure_receipts
                    FROM audit_procedure_items api
                    JOIN companies c ON c.corp_code=api.corp_code
                    WHERE api.bsns_year=:year
                      {company_filter}
                      {market_filter}
                """),
                params,
            ).mappings().first()
            or {}
        )
        kam_rows = [
            dict(r)
            for r in conn.execute(
                text(f"""
                    SELECT rs.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                           rs.bsns_year, rs.rcept_no, rs.dcm_no, rs.section_title,
                           rs.body_text, rs.body_length, rs.ordinal
                    FROM report_sections rs
                    JOIN companies c ON c.corp_code=rs.corp_code
                    WHERE rs.bsns_year=:year
                      AND rs.source_type='audit_report'
                      AND rs.section_key='kam'
                      {company_filter}
                      {market_filter}
                    ORDER BY rs.body_length ASC, c.market, c.corp_name
                    LIMIT :limit
                """),
                params,
            ).mappings().all()
        ]
        sample_receipts = [str(row.get("rcept_no")) for row in kam_rows if row.get("rcept_no")]
        if sample_receipts:
            procedure_stmt = text("""
                SELECT api.corp_code, api.rcept_no, api.dcm_no, api.kam_topic,
                       api.procedure_type, api.procedure_text, api.procedure_length
                FROM audit_procedure_items api
                WHERE api.bsns_year=:year
                  AND api.rcept_no IN :sample_receipts
                ORDER BY api.corp_code, api.rcept_no, api.procedure_ordinal
            """).bindparams(bindparam("sample_receipts", expanding=True))
            procedure_rows = [
                dict(r)
                for r in conn.execute(
                    procedure_stmt,
                    {"year": params["year"], "sample_receipts": sample_receipts},
                ).mappings().all()
            ]
        else:
            procedure_rows = []
        linkage_probe_rows = [
            dict(r)
            for r in conn.execute(
                text(f"""
                    SELECT api.corp_code, api.rcept_no, api.dcm_no, api.kam_topic,
                           api.procedure_type, api.procedure_text, api.procedure_length
                    FROM audit_procedure_items api
                    JOIN companies c ON c.corp_code=api.corp_code
                    WHERE api.bsns_year=:year
                      {company_filter}
                      {market_filter}
                    ORDER BY api.corp_code, api.rcept_no, api.procedure_ordinal
                    LIMIT :limit
                """),
                params,
            ).mappings().all()
        ]

    procedures_by_receipt: dict[str, list[dict[str, Any]]] = {}
    for row in procedure_rows:
        procedures_by_receipt.setdefault(str(row.get("rcept_no")), []).append(row)

    total_kam_sections = int(kam_summary.get("kam_sections") or 0)
    short_count = int(kam_summary.get("short_kam_sections") or 0)
    procedure_item_count = int(procedure_summary.get("procedure_items") or 0)
    procedure_receipt_count = int(procedure_summary.get("procedure_receipts") or 0)
    linked_probe_count = sum(
        1
        for row in linkage_probe_rows
        if classify_audit_procedure_linkages(
            str(row.get("procedure_text") or ""),
            kam_topic=row.get("kam_topic"),
        )
    )
    samples: list[dict[str, Any]] = []
    for row in kam_rows:
        procedures = procedures_by_receipt.get(str(row.get("rcept_no")), [])
        text_basis = " ".join(str(p.get("procedure_text") or "") for p in procedures)
        text_basis = text_basis or str(row.get("body_text") or "")
        kam_topic = procedures[0].get("kam_topic") if procedures else None
        samples.append({
            "corp_code": row.get("corp_code"),
            "stock_code": row.get("stock_code"),
            "corp_name": row.get("corp_name"),
            "market": row.get("market"),
            "year": row.get("bsns_year"),
            "rcept_no": row.get("rcept_no"),
            "dcm_no": row.get("dcm_no"),
            "section_title": row.get("section_title"),
            "body_length": row.get("body_length"),
            "procedure_count": len(procedures),
            "body_head": str(row.get("body_text") or "")[:180],
            "linkages": classify_audit_procedure_linkages(text_basis, kam_topic=kam_topic),
        })

    counts = {
        "kam_sections": total_kam_sections,
        "short_kam_sections": short_count,
        "procedure_items": procedure_item_count,
        "procedure_receipts": procedure_receipt_count,
        "linked_procedure_items_sampled": linked_probe_count,
        "full_body_kam_items": int(
            structured_kam_summary.get("full_body_kam_items") or 0
        ),
        "full_body_kam_items_with_procedures": int(
            structured_kam_summary.get(
                "full_body_kam_items_with_procedures"
            )
            or 0
        ),
        "full_body_kam_receipts": int(
            structured_kam_summary.get("full_body_kam_receipts") or 0
        ),
        "full_body_kam_receipts_with_procedures": int(
            structured_kam_summary.get(
                "full_body_kam_receipts_with_procedures"
            )
            or 0
        ),
    }
    sample = {
        "kam_sections": len(kam_rows),
        "procedure_items_for_sample_receipts": len(procedure_rows),
        "procedure_items_for_linkage_probe": len(linkage_probe_rows),
    }
    required_gaps: list[str] = []
    structured_count = int(structured_kam_summary.get("kam_items") or 0)
    if structured_count == 0:
        if short_count:
            required_gaps.append("short_kam_body")
        if procedure_item_count == 0:
            required_gaps.append("audit_procedure_items")
    if procedure_item_count > 0 and linked_probe_count == 0:
        required_gaps.append("procedure_evidence_linkages")

    full_body_receipts = counts["full_body_kam_receipts"]
    receipts_with_procedures = counts[
        "full_body_kam_receipts_with_procedures"
    ]
    coverage_rate = (
        round(receipts_with_procedures * 100.0 / full_body_receipts, 1)
        if full_body_receipts
        else 0.0
    )
    if full_body_receipts == 0:
        required_gaps.append("no_eligible_full_body_receipts")
    elif full_body_receipts and coverage_rate < 80.0:
        required_gaps.append("procedure_coverage_below_80")

    if required_gaps:
        verdict = "fail"
    else:
        verdict = "pass"

    return {
        "verdict": verdict,
        "database_status": "available",
        "year": int(year),
        "company": company,
        "market": market,
        "counts": counts,
        "sample": sample,
        "rates": {
            "short_kam_rate": round(short_count * 100.0 / total_kam_sections, 1) if total_kam_sections else 0.0,
            "procedure_receipt_to_kam_rate": (
                round(procedure_receipt_count * 100.0 / total_kam_sections, 1) if total_kam_sections else 0.0
            ),
            "procedure_linkage_sample_rate": (
                round(linked_probe_count * 100.0 / len(linkage_probe_rows), 1) if linkage_probe_rows else 0.0
            ),
            "procedure_coverage": (
                coverage_rate
            ),
        },
        "quality_gaps": {
            "summary_only": int(structured_kam_summary.get("summary_only") or 0),
            "missing": int(structured_kam_summary.get("missing") or 0),
            "error": int(structured_kam_summary.get("error") or 0),
        },
        "required_gaps": required_gaps,
        "missing_procedure_kams": missing_procedure_kams,
        "samples": samples,
        "data_quality": {
            "source": "report_sections.audit_report_kam + audit_procedure_items",
            "note": (
                "This diagnostic does not fetch new raw DART documents; it tests whether cached "
                "full-body audit-report KAM receipts can support procedure-level answers. "
                "Summary-only, missing, and error receipts are separate quality gaps."
            ),
        },
    }
