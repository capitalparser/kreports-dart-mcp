"""Explicit read-only adapter for bounded searches across cached datasets."""
from __future__ import annotations

import re

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module
from kreports.analysis._shared import _clean_dict, _display_text, _has_db_column
from kreports.analysis.company_profile import get_company_summary, resolve_corp_code
from kreports.storage.raw_documents import RawDocumentStore


_ACCOUNTING_NOTE_TOPIC_HINTS = (
    (
        ("수익", "매출", "revenue"),
        (
            ("고객과의 계약", 5), ("수행의무", 5), ("통제", 4), ("재화", 4),
            ("용역", 4), ("변동대가", 5), ("매출장려", 5), ("인식", 1),
        ),
    ),
    (
        ("재고", "inventory"),
        (
            ("순실현가능가치", 5), ("평균법", 4), ("선입선출", 4), ("평가손실", 3),
            ("매출원가", 2), ("원가", 1),
        ),
    ),
    (
        ("충당", "provision"),
        (
            ("충당부채", 5), ("현재의무", 5), ("과거사건", 4), ("자원의 유출", 4),
            ("최선의 추정", 4), ("할인", 1),
        ),
    ),
    (
        ("추정", "estimate"),
        (("불확실성", 4), ("민감도", 4), ("가정", 2), ("판단", 2), ("추정", 1)),
    ),
    (
        ("손상", "impairment"),
        (
            ("회수가능액", 5), ("현금창출단위", 5), ("사용가치", 4), ("손상차손", 4),
            ("공정가치", 2),
        ),
    ),
    (
        ("우발", "contingenc"),
        (
            ("우발부채", 5), ("우발자산", 5), ("현재의무", 4), ("소송", 4),
            ("가능성", 1), ("공시", 1),
        ),
    ),
)


def _accounting_note_topic_hints(keyword: str) -> tuple[tuple[str, int], ...]:
    normalized_keyword = keyword.lower()
    for triggers, hints in _ACCOUNTING_NOTE_TOPIC_HINTS:
        if any(trigger in normalized_keyword for trigger in triggers):
            return hints
    return ()


def _keyword_centered_excerpts(
    body: str,
    keyword: str,
    *,
    limit: int = 3,
    context_chars: int = 320,
) -> list[str]:
    """Return normalized, de-duplicated windows centered on literal matches."""
    normalized_body = re.sub(r"\s+", " ", _display_text(body)).strip()
    normalized_keyword = re.sub(r"\s+", " ", _display_text(keyword)).strip()
    if not normalized_body or not normalized_keyword or limit <= 0:
        return []

    topic_hints = _accounting_note_topic_hints(normalized_keyword)
    candidates: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    match_start = normalized_body.find(normalized_keyword)
    while match_start != -1:
        match_end = match_start + len(normalized_keyword)
        window_start = max(0, match_start - context_chars)
        window_end = min(len(normalized_body), match_end + context_chars)

        left_boundary = max(
            (normalized_body.rfind(mark, window_start, match_start) for mark in ".!?;:。！？；："),
            default=-1,
        )
        if left_boundary >= window_start:
            window_start = left_boundary + 1
        right_boundaries = [
            normalized_body.find(mark, match_end, window_end)
            for mark in ".!?;:。！？；："
        ]
        right_boundary = min((index for index in right_boundaries if index >= 0), default=-1)
        if right_boundary >= 0:
            window_end = right_boundary + 1

        excerpt = normalized_body[window_start:window_end].strip()
        if excerpt and excerpt not in seen:
            seen.add(excerpt)
            topic_score = sum(weight for hint, weight in topic_hints if hint in excerpt)
            candidates.append((topic_score, match_start, excerpt))
        match_start = normalized_body.find(normalized_keyword, match_end)
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
    return [excerpt for _, _, excerpt in candidates[:limit]]


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

_FINANCIAL_FILTER_COLUMNS = {
    "revenue": "revenue",
    "operating_profit": "operating_profit",
    "net_income": "net_income",
    "total_assets": "total_assets",
    "total_debt": "total_debt",
    "total_equity": "total_equity",
    "operating_cf": "operating_cf",
    "beneish_m_score": "beneish_m_score",
}


def build_company_filters(
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
        subject = get_company_summary(corp_code)
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


def group_company_records(rows: list[dict], *, limit: int) -> list[dict]:
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
    financial_metric: str | None = None,
    financial_min: float | None = None,
    financial_max: float | None = None,
    financial_year: int | None = None,
    financial_fs_div: str = "CFS",
    limit: int = 50,
    include_excerpt: bool = True,
) -> dict:
    """Unified cache search over the main local dataset tables."""
    if dataset not in _SEARCH_DATASETS:
        return {"error": "invalid dataset", "dataset": dataset, "allowed": sorted(_SEARCH_DATASETS)}
    limit = max(1, min(int(limit), 500))
    params: dict[str, object] = {"row_limit": limit * 10}
    filters, subject = build_company_filters(
        company=company,
        market=market,
        induty_prefix=induty_prefix,
        params=params,
    )
    if "__company_not_found__" in filters:
        return {"error": "company not found", "company": company}

    if financial_metric is not None:
        metric_column = _FINANCIAL_FILTER_COLUMNS.get(str(financial_metric))
        if metric_column is None:
            return {"error": "invalid financial metric", "financial_metric": financial_metric}
        resolved_financial_year = financial_year if financial_year is not None else year
        if resolved_financial_year is None:
            return {"error": "financial_year required when financial_metric is set"}
        filters.append(
            "EXISTS (SELECT 1 FROM financials ff "
            "WHERE ff.corp_code=c.corp_code AND ff.year=:financial_year "
            "AND ff.fs_div=:financial_fs_div "
            f"AND ff.{metric_column} IS NOT NULL"
            + (" AND ff." + metric_column + ">=:financial_min" if financial_min is not None else "")
            + (" AND ff." + metric_column + "<=:financial_max" if financial_max is not None else "")
            + ")"
        )
        params["financial_year"] = int(resolved_financial_year)
        params["financial_fs_div"] = financial_fs_div
        if financial_min is not None:
            params["financial_min"] = float(financial_min)
        if financial_max is not None:
            params["financial_max"] = float(financial_max)

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
                   anc.body, anc.body_length,
                   sd.id AS source_document_id,
                   sd.rcept_no AS source_document_rcept_no,
                   sd.corp_code AS source_document_corp_code,
                   sd.bsns_year AS source_document_bsns_year,
                   sd.report_nm AS source_document_report_nm,
                   d.rcept_no AS disclosure_rcept_no,
                   d.corp_code AS disclosure_corp_code,
                   d.disc_date AS disclosure_disc_date,
                   d.report_nm AS disclosure_report_nm
            FROM accounting_note_chapters anc
            JOIN companies c ON c.corp_code=anc.corp_code
            LEFT JOIN source_documents sd
              ON sd.rcept_no=anc.rcept_no AND sd.source_type=anc.source_type
             AND sd.corp_code=anc.corp_code AND sd.bsns_year=anc.bsns_year
            LEFT JOIN disclosures d
              ON d.rcept_no=anc.rcept_no AND d.corp_code=anc.corp_code
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
                if dataset == "accounting_note_chapters" and keyword:
                    excerpts = _keyword_centered_excerpts(body, keyword)
                    row["match_excerpts"] = excerpts
                    row["body_excerpt"] = excerpts[0] if excerpts else ""
                else:
                    row["body_excerpt"] = body[:1200]

    companies = group_company_records(rows, limit=limit)
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
            "financial_metric": financial_metric,
            "financial_min": financial_min,
            "financial_max": financial_max,
            "financial_year": financial_year,
            "financial_fs_div": financial_fs_div,
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
