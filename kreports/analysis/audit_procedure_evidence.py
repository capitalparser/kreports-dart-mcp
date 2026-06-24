"""Evidence mapping for KAM audit procedures.

The functions in this module are deliberately read-only. They diagnose whether
cached audit-report KAM sections and parsed procedure rows can support MCP
answers, and they explain which disclosure materials should be checked for a
procedure.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

import kreports.db.engine as engine_module


_TOPIC_TO_LINKS: dict[str, list[dict[str, str]]] = {
    "revenue": [
        {"category": "audit_report_kam", "key": "revenue", "label": "KAM: 수익인식"},
        {"category": "financial_statement_account", "key": "revenue", "label": "재무제표: 매출액"},
        {"category": "accounting_note", "key": "revenue_policy", "label": "주석: 수익인식 회계정책"},
    ],
    "inventory": [
        {"category": "audit_report_kam", "key": "inventory", "label": "KAM: 재고자산"},
        {"category": "financial_statement_account", "key": "inventory", "label": "재무제표: 재고자산"},
        {"category": "accounting_note", "key": "inventory_policy", "label": "주석: 재고자산 평가정책"},
    ],
    "impairment": [
        {"category": "audit_report_kam", "key": "impairment", "label": "KAM: 손상검사"},
        {"category": "financial_statement_account", "key": "impairment", "label": "재무제표: 손상 관련 자산"},
        {"category": "accounting_note", "key": "impairment_assumption", "label": "주석: 회수가능액 및 주요 가정"},
    ],
    "fair_value": [
        {"category": "audit_report_kam", "key": "fair_value", "label": "KAM: 공정가치"},
        {"category": "financial_statement_account", "key": "fair_value", "label": "재무제표: 공정가치 측정 항목"},
        {"category": "accounting_note", "key": "fair_value_hierarchy", "label": "주석: 공정가치 서열체계"},
    ],
    "provision": [
        {"category": "audit_report_kam", "key": "provision", "label": "KAM: 충당부채/우발부채"},
        {"category": "financial_statement_account", "key": "provision", "label": "재무제표: 충당부채"},
        {"category": "accounting_note", "key": "contingency", "label": "주석: 우발부채 및 약정사항"},
    ],
    "consolidation": [
        {"category": "audit_report_kam", "key": "consolidation", "label": "KAM: 연결/종속기업"},
        {"category": "financial_statement_account", "key": "subsidiary_investment", "label": "재무제표: 종속기업 투자"},
        {"category": "accounting_note", "key": "consolidation_scope", "label": "주석: 연결범위"},
    ],
    "tax": [
        {"category": "audit_report_kam", "key": "tax", "label": "KAM: 법인세"},
        {"category": "financial_statement_account", "key": "deferred_tax", "label": "재무제표: 이연법인세"},
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
        links.extend(_TOPIC_TO_LINKS.get(topic, []))

    for event_key, keywords in _DISCLOSURE_EVENT_HINTS.items():
        if any(keyword in body for keyword in keywords):
            links.append({
                "category": "disclosure_event",
                "key": event_key,
                "label": f"수시공시 이벤트: {event_key}",
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
    company_filter, params = _resolve_company_filter(company)
    market_filter = "AND c.market=:market" if market else ""
    if market:
        params["market"] = market
    params["year"] = int(year)
    params["limit"] = max(1, min(int(limit), 500))

    engine = engine_module.engine
    with engine.connect() as conn:
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

        procedure_rows = [
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

    short_count = sum(1 for row in kam_rows if int(row.get("body_length") or 0) < 300)
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
        "kam_sections": len(kam_rows),
        "short_kam_sections": short_count,
        "procedure_items": len(procedure_rows),
        "procedure_receipts": len({row.get("rcept_no") for row in procedure_rows}),
    }
    required_gaps: list[str] = []
    if short_count:
        required_gaps.append("short_kam_body")
    if len(procedure_rows) == 0:
        required_gaps.append("audit_procedure_items")
    if not any(sample["linkages"] for sample in samples):
        required_gaps.append("procedure_evidence_linkages")

    if "short_kam_body" in required_gaps or "audit_procedure_items" in required_gaps:
        verdict = "fail"
    elif required_gaps:
        verdict = "conditional"
    else:
        verdict = "pass"

    return {
        "verdict": verdict,
        "year": int(year),
        "company": company,
        "market": market,
        "counts": counts,
        "rates": {
            "short_kam_rate": round(short_count * 100.0 / len(kam_rows), 1) if kam_rows else 0.0,
            "procedure_to_kam_rate": round(len(procedure_rows) * 100.0 / len(kam_rows), 1) if kam_rows else 0.0,
        },
        "required_gaps": required_gaps,
        "samples": samples,
        "data_quality": {
            "source": "report_sections.audit_report_kam + audit_procedure_items",
            "note": (
                "This diagnostic does not fetch new raw DART documents; it tests whether cached "
                "audit-report KAM bodies can support procedure-level answers."
            ),
        },
    }
