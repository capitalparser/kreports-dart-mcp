"""Company resolution, profile, business overview, and cached dataset search."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module
from kreports.db.engine import get_session
from kreports.db.models import Company
from kreports.analysis import queries as _queries

from kreports.analysis._shared import _clean_dict, _display_text




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










# Stable internal interfaces consumed by other analysis domains.
resolve_company_identifier = _resolve_company_identifier
get_company_summary = _company_summary
get_industry_name = _get_industry_name


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
        total_chars += sec.get("length") or len(body)

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
