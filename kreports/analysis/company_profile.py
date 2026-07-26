"""Company resolution, profile, business overview, and cached dataset search."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module
from kreports.db.engine import get_session
from kreports.db.models import Company
from kreports.analysis import queries as _queries
from kreports.storage.raw_documents import RawDocumentStore

from kreports.analysis._shared import _clean_dict, _display_text, _has_db_column


def _load_source_document_excerpt(row: dict, *, keyword: str | None, limit: int = 1200) -> tuple[bool, str]:
    """Return keyword match and excerpt without requiring inline raw_content.

    For source_documents, raw XML/HTML may have been cleared from SQLite and
    moved to compressed storage. This helper reads at most the bounded result
    candidates selected by metadata filters.
    """
    report_nm = row.get("report_nm") or ""
    inline = row.pop("_inline_body", "") or ""
    body = inline
    if not body and row.get("storage_uri"):
        try:
            body = RawDocumentStore().read(row["storage_uri"], expected_hash=row.get("doc_hash"))
        except Exception as exc:
            row["source_load_error"] = str(exc)
            body = ""

    haystack = f"{report_nm}\n{body}"
    if keyword and keyword not in haystack:
        return False, ""
    return True, _display_text(body)[:limit]


def search_company(query: str, limit: int = 30) -> list[dict]:
    """
    회사명 또는 종목코드로 DB 검색. 상장사만 반환.

    Returns:
        [{"corp_code", "corp_name", "stock_code", "market"}]
    """
    return _queries.search_companies(query, limit=limit)


def get_company(stock_code: str) -> Optional[dict]:
    """
    종목코드로 기업 상세 조회.

    Returns:
        {"corp_code", "corp_name", "stock_code", "market", "induty_code", "sector"} or None
    """
    return _queries.get_company(stock_code)


def resolve_corp_code(identifier: str) -> Optional[str]:
    """
    identifier가 8자리 corp_code면 그대로, 6자리 종목코드면 corp_code 변환,
    그 외 회사명이면 search 후 첫 결과의 corp_code 반환.
    빈 문자열·None은 None 반환.
    """
    if identifier is None:
        return None
    s = identifier.strip()
    if not s:
        return None
    if len(s) == 8 and s.isdigit():
        with get_session() as session:
            exists = session.query(Company.corp_code).filter_by(corp_code=s).first()
            return s if exists else None
    if len(s) == 6 and s.isdigit():
        company = _queries.get_company(s)
        return company["corp_code"] if company else None
    # 회사명 fallback
    hits = _queries.search_companies(s, limit=1)
    return hits[0]["corp_code"] if hits else None


def _resolve_company_identifier(identifier: str) -> Optional[str]:
    """
    public API에서 corp_code / stock_code / 회사명을 모두 허용하기 위한 helper.
    """
    return resolve_corp_code(identifier)


def _company_summary(corp_code: str) -> Optional[dict]:
    with _engine_module.engine.connect() as conn:
        row = conn.execute(
            text("SELECT corp_code, stock_code, corp_name, market, induty_code FROM companies WHERE corp_code=:cc"),
            {"cc": corp_code},
        ).mappings().first()
    return dict(row) if row else None


def _get_industry_name(prefix: str) -> str:
    """KSIC 2자리 prefix → 한글 업종명."""
    from kreports.processor.sector_policy_map import KSIC_NAMES
    return KSIC_NAMES.get(prefix, f"업종 {prefix}")


_SEARCH_DATASETS = {
    "source_documents",
    "report_sections",
    "accounting_policies",
    "accounting_note_chapters",
    "evidence_documents",
    "disclosures",
    "audit_fees",
    "financials",
}


def _company_filters(
    *,
    company: str | None,
    market: str | None,
    induty_prefix: str | None,
    params: dict[str, object],
    alias: str = "c",
) -> tuple[list[str], dict | None]:
    filters: list[str] = []
    subject = None
    if company:
        corp_code = resolve_corp_code(company) or company
        subject = _company_summary(corp_code)
        if not subject:
            return ["__company_not_found__"], None
        filters.append(f"{alias}.corp_code=:corp_code")
        params["corp_code"] = corp_code
    if market:
        filters.append(f"{alias}.market=:market")
        params["market"] = market
    if induty_prefix:
        filters.append(f"{alias}.induty_code LIKE :induty_prefix")
        params["induty_prefix"] = f"{induty_prefix}%"
    return filters, subject


def _group_company_records(rows: list[dict], *, limit: int) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        cc = row.pop("corp_code")
        item = grouped.setdefault(cc, {
            "corp_code": cc,
            "stock_code": row.pop("stock_code", None),
            "corp_name": row.pop("corp_name", None),
            "market": row.pop("market", None),
            "induty_code": row.pop("induty_code", None),
            "records": [],
        })
        item["records"].append(row)
    companies = list(grouped.values())
    for item in companies:
        item["record_count"] = len(item["records"])
        item["records"] = item["records"][:10]
    companies.sort(key=lambda item: (-item["record_count"], item.get("market") or "", item.get("corp_name") or ""))
    return companies[:limit]


def search_dataset(
    *,
    dataset: str,
    company: str | None = None,
    year: int | None = None,
    market: str | None = None,
    induty_prefix: str | None = None,
    keyword: str | None = None,
    source_type: str | None = None,
    section_keys: list[str] | None = None,
    section_type: str | None = None,
    fs_div: str | None = None,
    quarter: int | None = None,
    limit: int = 50,
    include_excerpt: bool = True,
) -> dict:
    """Unified cache search over the main local dataset tables."""
    if dataset not in _SEARCH_DATASETS:
        return {"error": "invalid dataset", "dataset": dataset, "allowed": sorted(_SEARCH_DATASETS)}
    limit = max(1, min(int(limit), 500))
    params: dict[str, object] = {"row_limit": limit * 10}
    filters, subject = _company_filters(
        company=company,
        market=market,
        induty_prefix=induty_prefix,
        params=params,
    )
    if "__company_not_found__" in filters:
        return {"error": "company not found", "company": company}

    bind_expanding = []
    source = dataset
    interpretation = "Search reads local cached tables only. Empty result means no cached matching row."
    if dataset == "source_documents":
        where = ["1=1", *filters]
        if year is not None:
            where.append("sd.bsns_year=:year")
            params["year"] = int(year)
        if source_type:
            where.append("sd.source_type=:source_type")
            params["source_type"] = source_type
        if keyword:
            params["row_limit"] = max(params["row_limit"], limit * 50)
        sql = f"""
            SELECT sd.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   sd.bsns_year AS year, sd.rcept_no, sd.dcm_no, sd.source_type,
                   sd.report_nm, sd.content_type, sd.storage_uri, sd.doc_hash,
                   sd.content_length, sd.compressed_length, sd.storage_status,
                   CASE WHEN sd.content_type='derived_report_sections' THEN sd.raw_content ELSE '' END AS _inline_body,
                   CASE
                     WHEN sd.content_length IS NOT NULL THEN sd.content_length
                     ELSE LENGTH(sd.raw_content)
                   END AS body_length
            FROM source_documents sd
            JOIN companies c ON c.corp_code=sd.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY sd.bsns_year DESC, c.market, c.corp_name, sd.source_type
            LIMIT :row_limit
        """
        source = "source_documents"
        interpretation = (
            "Search reads source_documents cache. Rows with content_type=derived_report_sections "
            "are reconstructed legacy evidence bundles, not original DART filing bodies. "
            "Raw body keyword search is bounded to selected metadata candidates; use evidence_documents "
            "for broad text search."
        )
    elif dataset == "report_sections":
        where = ["1=1", *filters]
        if year is not None:
            where.append("rs.bsns_year=:year")
            params["year"] = int(year)
        if source_type:
            where.append("rs.source_type=:source_type")
            params["source_type"] = source_type
        if section_keys:
            where.append("rs.section_key IN :section_keys")
            params["section_keys"] = section_keys
            bind_expanding.append("section_keys")
        if keyword:
            where.append("(rs.section_title LIKE :kw OR rs.body_text LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        dcm_select = "rs.dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
        sql = f"""
            SELECT rs.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   rs.bsns_year AS year, rs.rcept_no, {dcm_select}, rs.source_type,
                   rs.section_key, rs.section_title, rs.body_text, rs.body_length
            FROM report_sections rs
            JOIN companies c ON c.corp_code=rs.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY rs.bsns_year DESC, c.market, c.corp_name, rs.section_key
            LIMIT :row_limit
        """
    elif dataset == "accounting_policies":
        where = ["1=1", *filters]
        if year is not None:
            where.append("api.bsns_year=:year")
            params["year"] = int(year)
        if fs_div:
            where.append("api.fs_div=:fs_div")
            params["fs_div"] = fs_div
        if keyword:
            where.append("(api.item_key LIKE :kw OR api.heading LIKE :kw OR api.body LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT api.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   api.bsns_year AS year, api.fs_div, api.rcept_no, api.item_key,
                   api.heading, api.body, api.body_length
            FROM accounting_policy_items api
            JOIN companies c ON c.corp_code=api.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY api.bsns_year DESC, c.market, c.corp_name, api.item_key
            LIMIT :row_limit
        """
        source = "accounting_policy_items"
    elif dataset == "accounting_note_chapters":
        where = ["1=1", *filters]
        if year is not None:
            where.append("anc.bsns_year=:year")
            params["year"] = int(year)
        if fs_div:
            where.append("anc.fs_div=:fs_div")
            params["fs_div"] = fs_div
        if source_type:
            where.append("anc.source_type=:source_type")
            params["source_type"] = source_type
        if section_type:
            where.append("anc.section_type=:section_type")
            params["section_type"] = section_type
        if keyword:
            where.append("(anc.note_title LIKE :kw OR anc.body LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT anc.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   anc.bsns_year AS year, anc.fs_div, anc.rcept_no, anc.dcm_no,
                   anc.source_type, anc.note_no, anc.note_title, anc.section_type,
                   anc.body, anc.body_length
            FROM accounting_note_chapters anc
            JOIN companies c ON c.corp_code=anc.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY anc.bsns_year DESC, c.market, c.corp_name, anc.note_no
            LIMIT :row_limit
        """
        source = "accounting_note_chapters"
    elif dataset == "evidence_documents":
        where = ["1=1", *filters]
        if year is not None:
            where.append("ed.bsns_year=:year")
            params["year"] = int(year)
        if source_type:
            where.append("ed.source_type=:source_type")
            params["source_type"] = source_type
        if keyword:
            where.append("(ed.title LIKE :kw OR ed.normalized_text LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT ed.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   ed.bsns_year AS year, ed.rcept_no, ed.dcm_no, ed.source_type,
                   ed.evidence_scope, ed.title, ed.normalized_text AS body,
                   ed.text_length AS body_length, ed.source_count, ed.generated_at,
                   ed.full_text_uri, ed.full_text_length, ed.full_text_storage_status
            FROM evidence_documents ed
            JOIN companies c ON c.corp_code=ed.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY ed.bsns_year DESC, c.market, c.corp_name, ed.source_type
            LIMIT :row_limit
        """
        source = "evidence_documents"
        interpretation = (
            "Search reads compact normalized evidence bundles derived from report_sections, "
            "accounting note chapters, accounting policies, and audit procedures. It is suitable "
            "for MCP narrative answers, while raw DART XML/HTML remains the source of record."
        )
    elif dataset == "disclosures":
        where = ["1=1", *filters]
        if year is not None:
            where.append("d.disc_date BETWEEN :start_date AND :end_date")
            params["start_date"] = f"{int(year)}-01-01"
            params["end_date"] = f"{int(year)}-12-31"
        if keyword:
            where.append("(d.report_nm LIKE :kw OR d.flr_nm LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT d.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   d.rcept_no, d.disc_date, d.disc_type, d.report_nm, d.flr_nm
            FROM disclosures d
            JOIN companies c ON c.corp_code=d.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY d.disc_date DESC, c.market, c.corp_name
            LIMIT :row_limit
        """
    elif dataset == "audit_fees":
        where = ["1=1", *filters]
        if year is not None:
            where.append("af.bsns_year=:year")
            params["year"] = int(year)
        if keyword:
            where.append("af.auditor_nm LIKE :kw")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT af.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   af.bsns_year AS year, af.auditor_nm, af.audit_fee_m,
                   af.audit_hours, af.non_audit_fee_m, af.nas_ratio,
                   af.independence_risk_flag
            FROM audit_fees af
            JOIN companies c ON c.corp_code=af.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY af.bsns_year DESC, c.market, c.corp_name
            LIMIT :row_limit
        """
    else:
        where = ["1=1", *filters]
        if year is not None:
            where.append("f.year=:year")
            params["year"] = int(year)
        if fs_div:
            where.append("f.fs_div=:fs_div")
            params["fs_div"] = fs_div
        if quarter is not None:
            where.append("f.quarter=:quarter")
            params["quarter"] = int(quarter)
        sql = f"""
            SELECT f.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   f.year, f.quarter, f.fs_div, f.revenue, f.operating_profit,
                   f.net_income, f.total_assets, f.total_debt, f.total_equity,
                   f.operating_cf, f.going_concern_flag, f.op_cf_divergence_flag,
                   f.beneish_m_score, f.source
            FROM financials f
            JOIN companies c ON c.corp_code=f.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY f.year DESC, f.quarter DESC, c.market, c.corp_name
            LIMIT :row_limit
        """

    stmt = text(sql)
    for key in bind_expanding:
        stmt = stmt.bindparams(bindparam(key, expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, params).mappings().all()]

    if dataset == "source_documents":
        filtered_rows = []
        for row in rows:
            matched, excerpt = _load_source_document_excerpt(row, keyword=keyword)
            if not matched:
                continue
            if include_excerpt:
                row["body_excerpt"] = excerpt
            filtered_rows.append(row)
            if len(filtered_rows) >= limit * 10:
                break
        rows = filtered_rows

    for row in rows:
        if dataset == "evidence_documents":
            row["full_text_available"] = bool(row.get("full_text_uri"))
            row["text_storage_status"] = row.pop("full_text_storage_status", None) or "inline_excerpt"
        if "body_text" in row:
            body = _display_text(row.pop("body_text") or "")
            if include_excerpt:
                row["body_excerpt"] = body[:1200]
        if "body" in row:
            body = _display_text(row.pop("body") or "")
            if include_excerpt:
                row["body_excerpt"] = body[:1200]

    companies = _group_company_records(rows, limit=limit)
    return _clean_dict({
        "query": {
            "dataset": dataset,
            "company": company,
            "year": year,
            "market": market,
            "induty_prefix": induty_prefix,
            "keyword": keyword,
            "source_type": source_type,
            "section_keys": section_keys,
            "section_type": section_type,
            "fs_div": fs_div,
            "quarter": quarter,
            "limit": limit,
            "include_excerpt": include_excerpt,
        },
        "subject": subject,
        "total_companies": len(companies),
        "total_records": sum(item["record_count"] for item in companies),
        "companies": companies,
        "data_quality": {
            "status": "usable" if companies else "missing",
            "source": source,
            "interpretation": interpretation,
        },
    })


def get_business_overview(
    company: str,
    bsns_year: Optional[int] = None,
) -> dict:
    """
    사업보고서 핵심 섹션 텍스트 + 업종 분류 + rule-based 인사이트 반환.
    Claude가 MCP로 호출하여 업종별 심층 분석에 활용.

    Args:
        company: corp_code / stock_code / 회사명
        bsns_year: 사업연도 (None이면 최신)

    Returns:
        {
          "corp_code", "corp_name", "induty_code", "industry_name",
          "bsns_year",
          "sections": {section_key: {"title", "body_text", "length"}},
          "insights": [{"title", "value", "detail", "audience", "risk_level"}],
          "total_chars": int,
        }
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

    # 기업 정보
    with get_session() as session:
        row = session.query(Company).filter_by(corp_code=corp_code).first()
        if row is None:
            return {"error": f"corp_code '{corp_code}'를 찾을 수 없습니다."}
        corp_name = row.corp_name
        induty_code = row.induty_code or ""

    # 업종명
    industry_name = _get_industry_name(induty_code[:2]) if induty_code else ""

    # 사업보고서 연도 결정
    if bsns_year is None:
        years = _queries.get_years_with_business_report(corp_code)
        if not years:
            return {
                "corp_code": corp_code, "corp_name": corp_name,
                "induty_code": induty_code, "industry_name": industry_name,
                "bsns_year": None,
                "sections": {},
                "insights": [],
                "total_chars": 0,
                "note": "수집된 사업보고서가 없습니다.",
            }
        bsns_year = years[0]  # 가장 최근

    def _business_report_source(section_key: str, section: dict) -> dict:
        rcept_no = section.get("rcept_no")
        report_nm = None
        if rcept_no:
            with _engine_module.engine.connect() as conn:
                disclosure_row = conn.execute(
                    text("SELECT report_nm FROM disclosures WHERE rcept_no=:rcept_no LIMIT 1"),
                    {"rcept_no": rcept_no},
                ).mappings().first()
            if disclosure_row:
                report_nm = disclosure_row["report_nm"]
        from kreports.analysis.evidence import dart_filing_url

        return {
            "corp_code": corp_code,
            "corp_name": corp_name,
            "report_nm": report_nm or "사업보고서",
            "bsns_year": bsns_year,
            "rcept_no": rcept_no,
            "section_key": section_key,
            "section_title": section.get("title") or section_key,
            "dart_url": dart_filing_url(rcept_no),
            "source_table": "report_sections",
        }

    def _business_overview_evidence(sections: dict) -> tuple[list[dict], list[dict], list[str]]:
        facts: list[dict] = []
        fact_specs = [
            ("business_overview", "사업 개요"),
            ("business_description", "사업 내용"),
            ("risk_management", "위험관리"),
            ("management_plan", "경영진단"),
        ]
        for section_key, label in fact_specs:
            section = sections.get(section_key)
            if not isinstance(section, dict):
                continue
            body = str(section.get("body_text") or "").strip()
            if not body:
                continue
            excerpt = body[:260].replace("\n", " ")
            facts.append({
                "statement": f"{label} 섹션에서 {excerpt}",
                "source": _business_report_source(section_key, section),
                "excerpt": excerpt,
            })
            if len(facts) >= 4:
                break

        analysis: list[dict] = []
        if "risk_management" in sections:
            analysis.append({
                "perspective": "auditor",
                "statement": "위험관리 섹션이 확인되므로 차입금, 유동성, 환위험, 파생상품 공시와 관련 계정의 완전성·평가 검토가 필요합니다.",
                "basis": ["위험관리 섹션"],
                "risk_level": "watch",
            })
        if "business_description" in sections or "business_overview" in sections:
            analysis.append({
                "perspective": "investor",
                "statement": "사업 포트폴리오와 주요 제품·서비스 기재는 성장성 판단의 출발점이지만, 투자 판단에는 수익성·현금흐름·재무구조 확인이 함께 필요합니다.",
                "basis": ["사업의 내용", "사업의 개요"],
                "risk_level": "watch",
            })
        next_checks = [
            "중요 문단은 공시 링크에서 원문 위치와 표 수치를 재확인하세요.",
            "감사인 관점 검토에는 감사보고서 KAM 본문과 감사절차를 함께 확인하세요.",
        ]
        return facts, analysis, next_checks

    section_keys = {
        "business_overview",
        "business_description",
        "risk_management",
        "management_plan",
        "rd_activities",
        "key_contracts",
    }
    with _engine_module.engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT rcept_no, section_key, section_title, body_text, body_length
                FROM report_sections
                WHERE corp_code=:corp_code
                  AND bsns_year=:bsns_year
                  AND source_type='business_report'
                  AND section_key IN :section_keys
                ORDER BY ordinal
                """
            ).bindparams(bindparam("section_keys", expanding=True)),
            {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "section_keys": list(section_keys),
            },
        ).mappings().all()

    raw = {
        row["section_key"]: {
            "rcept_no": row["rcept_no"],
            "title": row["section_title"],
            "body_text": _display_text(row["body_text"]),
            "length": row["body_length"],
        }
        for row in rows
    }

    if not raw or not isinstance(raw, dict):
        with _engine_module.engine.connect() as conn:
            full_text_row = conn.execute(
                text(
                    """
                    SELECT rcept_no, section_key, section_title, body_text, body_length
                    FROM report_sections
                    WHERE corp_code=:corp_code
                      AND bsns_year=:bsns_year
                      AND source_type='business_report'
                      AND section_key='full_text'
                    ORDER BY ordinal
                    LIMIT 1
                    """
                ),
                {
                    "corp_code": corp_code,
                    "bsns_year": bsns_year,
                },
            ).mappings().first()
        if full_text_row:
            body = _display_text(full_text_row["body_text"] or "")
            if len(body) > 3000:
                body = body[:3000] + "\n... (이하 생략)"
            raw = {
                "full_text": {
                    "rcept_no": full_text_row["rcept_no"],
                    "title": full_text_row["section_title"] or "사업보고서 본문",
                    "body_text": body,
                    "length": full_text_row["body_length"] or len(body),
                }
            }
            from kreports.analysis.business_insights import generate_business_insights
            confirmed_facts, analysis, next_checks = _business_overview_evidence(raw)

            return _clean_dict({
                "corp_code": corp_code,
                "corp_name": corp_name,
                "induty_code": induty_code,
                "industry_name": industry_name,
                "bsns_year": bsns_year,
                "report_meta": {},
                "sections": raw,
                "insights": generate_business_insights(raw, induty_code=induty_code),
                "audit_focus": [],
                "investment_focus": [],
                "risk_distribution": None,
                "total_chars": full_text_row["body_length"] or len(body),
                "section_count": 1,
                "available_sections": ["full_text"],
                "missing_sections": sorted(section_keys),
                "confirmed_facts": confirmed_facts,
                "analysis": analysis,
                "next_checks": next_checks,
                "data_quality": {
                    "status": "limited",
                    "source": "local_report_sections",
                    "requested_year": bsns_year,
                    "available_business_report_years": _queries.get_cached_report_section_years(corp_code, "business_report"),
                    "fallback_used": "full_text",
                    "missing_reason": (
                        "사업보고서 원문 본문은 캐시되어 있으나 business_overview/business_description 등 "
                        "경영정보 세부 섹션으로 아직 분리되지 않았습니다."
                    ),
                    "interpretation": (
                        "The response uses cached full_text as a limited fallback. "
                        "It supports reading and keyword screening, but section-specific analysis may be incomplete."
                    ),
                },
                "note": (
                    f"{bsns_year}년 사업보고서 세부 경영정보 섹션이 없어 full_text 캐시를 제한적으로 반환합니다."
                ),
            })
        return {
            "corp_code": corp_code, "corp_name": corp_name,
            "induty_code": induty_code, "industry_name": industry_name,
            "bsns_year": bsns_year,
            "sections": {},
            "insights": [],
            "total_chars": 0,
            "section_count": 0,
            "data_quality": {
                "status": "cache_missing",
                "source": "local_report_sections",
                "requested_year": bsns_year,
                "available_business_report_years": _queries.get_cached_report_section_years(corp_code, "business_report"),
                "missing_reason": "business_overview/business_description 등 경영정보 섹션이 아직 로컬 DB에 영속화되지 않았습니다.",
                "interpretation": (
                    "No rows means the local business-report section cache lacks the requested year. "
                    "It does not prove the filing lacks management discussion sections."
                ),
            },
            "note": (
                f"{bsns_year}년 사업보고서 경영정보 본문 캐시가 없습니다. "
                "외부 MCP 런타임에서는 DART API를 호출하지 않습니다."
            ),
        }

    # 텍스트만 반환 (HTML 제거 — MCP 응답 크기 절약)
    sections_clean = {}
    total_chars = 0
    for key, sec in raw.items():
        if not isinstance(sec, dict):
            continue
        body = sec.get("body_text", "")
        # MCP 응답 크기 제한: 각 섹션 최대 3000자
        if len(body) > 3000:
            body = body[:3000] + "\n... (이하 생략)"
        sections_clean[key] = {
            "title": sec.get("title", key),
            "body_text": body,
            "length": sec.get("length", len(body)),
        }
        total_chars += sec.get("length", 0)

    # 인사이트
    from kreports.analysis.business_insights import generate_business_insights
    insights = generate_business_insights(raw, induty_code=induty_code)
    confirmed_facts, analysis, next_checks = _business_overview_evidence(raw)

    return _clean_dict({
        "corp_code": corp_code,
        "corp_name": corp_name,
        "induty_code": induty_code,
        "industry_name": industry_name,
        "bsns_year": bsns_year,
        "report_meta": {},
        "sections": sections_clean,
        "insights": insights,
        "audit_focus": [],
        "investment_focus": [],
        "risk_distribution": None,
        "total_chars": total_chars,
        "section_count": len(sections_clean),
        "available_sections": sorted(sections_clean),
        "missing_sections": sorted(section_keys - set(sections_clean)),
        "confirmed_facts": confirmed_facts,
        "analysis": analysis,
        "next_checks": next_checks,
        "data_quality": {
            "status": "usable",
            "source": "local_report_sections",
            "requested_year": bsns_year,
            "available_business_report_years": _queries.get_cached_report_section_years(corp_code, "business_report"),
        },
    })
