"""High-quality, cache-only company search over accounting-note chapters."""
from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module


SearchMode = Literal["exact", "normalized", "synonym"]

_NORMALIZATION_CHARS = (
    " ",
    "\t",
    "\n",
    "\r",
    "-",
    "_",
    "·",
    ".",
    ",",
    "(",
    ")",
    "/",
    ":",
    ";",
    "[",
    "]",
)
_BUILTIN_SYNONYM_GROUPS = (
    (
        "자금보충약정",
        "자금 보충 약정",
        "자금보충의무",
        "유동성보충약정",
        "자금지원약정",
    ),
    (
        "우발부채",
        "우발 채무",
        "충당부채 및 우발부채",
        "약정사항",
    ),
    (
        "리스",
        "사용권자산",
        "리스부채",
        "임차계약",
    ),
    (
        "손상",
        "손상차손",
        "회수가능액",
        "현금창출단위",
        "CGU",
    ),
    (
        "계속기업",
        "계속기업 불확실성",
        "계속기업 관련 중요한 불확실성",
    ),
)


def _compact_term(value: str) -> str:
    compact = str(value or "").casefold()
    for character in _NORMALIZATION_CHARS:
        compact = compact.replace(character, "")
    return compact


def _compact_with_positions(
    value: str,
) -> tuple[str, list[int]]:
    compact: list[str] = []
    positions: list[int] = []
    removable = set(_NORMALIZATION_CHARS)
    for index, character in enumerate(str(value or "")):
        if character in removable:
            continue
        compact.append(character.casefold())
        positions.append(index)
    return "".join(compact), positions


def _dedupe_terms(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        key = _compact_term(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def expanded_search_terms(
    keyword: str,
    *,
    search_mode: SearchMode,
    synonyms: list[str] | None = None,
) -> list[str]:
    normalized = str(keyword or "").strip()
    if not normalized:
        return []
    if search_mode != "synonym":
        return [normalized]

    query_key = _compact_term(normalized)
    values = [normalized, *(synonyms or [])]
    for group in _BUILTIN_SYNONYM_GROUPS:
        group_keys = {_compact_term(item) for item in group}
        if query_key in group_keys:
            values.extend(group)
    return _dedupe_terms(values)


def _compact_sql(expression: str) -> str:
    compact = f"lower(coalesce({expression}, ''))"
    compact = f"replace({compact}, char(9), '')"
    compact = f"replace({compact}, char(10), '')"
    compact = f"replace({compact}, char(13), '')"
    for character in (
        " ",
        "-",
        "_",
        "·",
        ".",
        ",",
        "(",
        ")",
        "/",
        ":",
        ";",
        "[",
        "]",
    ):
        escaped = character.replace("'", "''")
        compact = f"replace({compact}, '{escaped}', '')"
    return compact


def _match_predicate(
    terms: list[str],
    *,
    search_mode: SearchMode,
    params: dict[str, Any],
) -> str:
    predicates: list[str] = []
    for index, term in enumerate(terms):
        key = f"search_term_{index}"
        if search_mode == "exact":
            params[key] = term.casefold()
            predicates.append(
                "("
                f"instr(lower(coalesce(anc.note_title, '')), :{key}) > 0 "
                "OR "
                f"instr(lower(coalesce(anc.body, '')), :{key}) > 0"
                ")"
            )
        else:
            params[key] = _compact_term(term)
            predicates.append(
                "("
                f"instr({_compact_sql('anc.note_title')}, :{key}) > 0 "
                "OR "
                f"instr({_compact_sql('anc.body')}, :{key}) > 0"
                ")"
            )
    return "(" + " OR ".join(predicates) + ")"


def _find_match(
    value: str,
    terms: list[str],
    *,
    search_mode: SearchMode,
) -> tuple[int, str, int] | None:
    text_value = str(value or "")
    if search_mode == "exact":
        folded = text_value.casefold()
        best: tuple[int, str, int] | None = None
        for term in terms:
            needle = term.casefold()
            index = folded.find(needle)
            if index < 0:
                continue
            count = folded.count(needle)
            candidate = (index, term, count)
            if best is None or index < best[0]:
                best = candidate
        return best

    compact, positions = _compact_with_positions(text_value)
    best = None
    for term in terms:
        needle = _compact_term(term)
        compact_index = compact.find(needle)
        if compact_index < 0:
            continue
        source_index = (
            positions[compact_index]
            if compact_index < len(positions)
            else 0
        )
        count = compact.count(needle)
        candidate = (source_index, term, count)
        if best is None or source_index < best[0]:
            best = candidate
    return best


def _bounded_excerpt(
    body: str,
    *,
    match_index: int,
    before: int = 320,
    after: int = 880,
) -> tuple[str, int]:
    source = str(body or "")
    start = max(0, match_index - before)
    end = min(len(source), match_index + after)
    excerpt = " ".join(source[start:end].split())
    if start:
        excerpt = "… " + excerpt
    if end < len(source):
        excerpt += " …"
    return excerpt, start


def _record_match(
    row: dict[str, Any],
    terms: list[str],
    *,
    search_mode: SearchMode,
    include_excerpt: bool,
) -> dict[str, Any]:
    title = str(row.get("note_title") or "")
    body = str(row.pop("body", "") or "")
    title_match = _find_match(
        title,
        terms,
        search_mode=search_mode,
    )
    body_match = _find_match(
        body,
        terms,
        search_mode=search_mode,
    )

    chosen_field = "body" if body_match is not None else "note_title"
    chosen = body_match or title_match
    if chosen is None:
        # SQL and Python normalization are designed to match; fail closed if an
        # older SQLite collation behaves differently.
        row["match_status"] = "sql_python_match_mismatch"
        return row

    match_index, matched_term, _field_count = chosen
    total_count = 0
    for value in (title, body):
        found = _find_match(
            value,
            terms,
            search_mode=search_mode,
        )
        if found is not None:
            total_count += found[2]

    if include_excerpt:
        if chosen_field == "body":
            excerpt, excerpt_start = _bounded_excerpt(
                body,
                match_index=match_index,
            )
        else:
            excerpt_start = 0
            body_excerpt = " ".join(body[:900].split())
            excerpt = (
                f"{title} — {body_excerpt}"
                if body_excerpt
                else title
            )
        row["body_excerpt"] = excerpt
        row["excerpt_start"] = excerpt_start

    row.update({
        "matched_field": chosen_field,
        "matched_term": matched_term,
        "match_type": search_mode,
        "match_count": total_count,
        "source_url": (
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
            f"{row.get('rcept_no')}"
        ),
    })
    return row


def search_note_disclosing_companies(
    keyword: str,
    *,
    year: int | None = None,
    market: str | None = None,
    induty_prefix: str | None = None,
    fs_div: str | None = None,
    section_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    include_excerpt: bool = True,
    search_mode: SearchMode = "exact",
    synonyms: list[str] | None = None,
) -> dict[str, Any]:
    """Find companies with matching cached notes and truthful pagination."""
    normalized_keyword = str(keyword or "").strip()
    if not normalized_keyword:
        return {
            "error": "keyword is required",
            "companies": [],
            "total_companies": 0,
            "total_records": 0,
        }
    if search_mode not in {"exact", "normalized", "synonym"}:
        return {
            "error": "invalid search_mode",
            "allowed": ["exact", "normalized", "synonym"],
        }

    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    terms = expanded_search_terms(
        normalized_keyword,
        search_mode=search_mode,
        synonyms=synonyms,
    )
    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
    }
    where = ["1=1"]
    if year is not None:
        where.append("anc.bsns_year=:year")
        params["year"] = int(year)
    if market:
        where.append("c.market=:market")
        params["market"] = market
    if induty_prefix:
        where.append("c.induty_code LIKE :induty_prefix")
        params["induty_prefix"] = f"{induty_prefix}%"
    if fs_div:
        where.append("anc.fs_div=:fs_div")
        params["fs_div"] = fs_div
    if section_type:
        where.append("anc.section_type=:section_type")
        params["section_type"] = section_type
    where.append(
        _match_predicate(
            terms,
            search_mode=search_mode,
            params=params,
        )
    )
    where_sql = " AND ".join(where)
    from_sql = (
        " FROM accounting_note_chapters anc "
        "JOIN companies c ON c.corp_code=anc.corp_code "
    )

    count_sql = (
        "SELECT COUNT(*) AS matched_records, "
        "COUNT(DISTINCT anc.corp_code) AS matched_companies"
        + from_sql
        + f"WHERE {where_sql}"
    )
    company_sql = (
        "SELECT anc.corp_code, c.stock_code, c.corp_name, "
        "c.market, c.induty_code, COUNT(*) AS record_count"
        + from_sql
        + f"WHERE {where_sql} "
        "GROUP BY anc.corp_code, c.stock_code, c.corp_name, "
        "c.market, c.induty_code "
        "ORDER BY record_count DESC, c.market, c.corp_name, "
        "anc.corp_code LIMIT :limit OFFSET :offset"
    )

    with _engine_module.engine.connect() as conn:
        totals = conn.execute(
            text(count_sql),
            params,
        ).mappings().first()
        company_rows = [
            dict(row)
            for row in conn.execute(
                text(company_sql),
                params,
            ).mappings().all()
        ]

        selected_codes = [
            str(row["corp_code"])
            for row in company_rows
        ]
        records: list[dict[str, Any]] = []
        if selected_codes:
            record_from_sql = (
                from_sql
                + "LEFT JOIN source_documents sd "
                "ON sd.rcept_no=anc.rcept_no "
                "AND sd.source_type=anc.source_type "
                "AND sd.corp_code=anc.corp_code "
                "AND sd.bsns_year=anc.bsns_year "
                "LEFT JOIN disclosures d "
                "ON d.rcept_no=anc.rcept_no "
                "AND d.corp_code=anc.corp_code "
            )
            record_params = {
                **params,
                "selected_codes": selected_codes,
            }
            record_stmt = text(
                "SELECT anc.id, anc.corp_code, anc.bsns_year AS year, "
                "anc.fs_div, anc.rcept_no, anc.dcm_no, anc.source_type, "
                "anc.note_no, anc.note_title, anc.section_type, "
                "anc.body, anc.body_length, anc.full_text_uri, "
                "anc.full_text_hash, anc.full_text_length, "
                "anc.full_text_storage_status, "
                "sd.id AS source_document_id, "
                "sd.rcept_no AS source_document_rcept_no, "
                "sd.corp_code AS source_document_corp_code, "
                "sd.bsns_year AS source_document_bsns_year, "
                "sd.report_nm AS source_document_report_nm, "
                "d.rcept_no AS disclosure_rcept_no, "
                "d.corp_code AS disclosure_corp_code, "
                "d.disc_date AS disclosure_disc_date, "
                "d.report_nm AS disclosure_report_nm"
                + record_from_sql
                + f"WHERE {where_sql} "
                "AND anc.corp_code IN :selected_codes "
                "ORDER BY anc.corp_code, anc.bsns_year DESC, "
                "anc.fs_div, anc.note_no, anc.id"
            ).bindparams(
                bindparam("selected_codes", expanding=True)
            )
            records = [
                dict(row)
                for row in conn.execute(
                    record_stmt,
                    record_params,
                ).mappings().all()
            ]

    matched_records = int(
        (totals or {}).get("matched_records") or 0
    )
    matched_companies = int(
        (totals or {}).get("matched_companies") or 0
    )
    by_code: dict[str, dict[str, Any]] = {}
    for row in company_rows:
        code = str(row["corp_code"])
        by_code[code] = {
            **row,
            "records": [],
        }

    for raw_record in records:
        code = str(raw_record["corp_code"])
        company = by_code.get(code)
        if company is None:
            continue
        record = _record_match(
            dict(raw_record),
            terms,
            search_mode=search_mode,
            include_excerpt=include_excerpt,
        )
        if len(company["records"]) < 10:
            company["records"].append(record)

    companies = list(by_code.values())
    returned_record_count = sum(
        len(company["records"])
        for company in companies
    )
    next_offset = (
        offset + len(companies)
        if offset + len(companies) < matched_companies
        else None
    )
    confirmed_facts = []
    for company in companies:
        for record in company["records"][:2]:
            if len(confirmed_facts) >= 6:
                break
            confirmed_facts.append({
                "statement": (
                    f"{company.get('corp_name') or code}의 "
                    f"{record.get('year')}년 "
                    f"'{record.get('note_title') or record.get('note_no')}' "
                    f"주석에서 '{record.get('matched_term')}' 관련 문구가 "
                    "로컬 캐시에 확인됩니다."
                ),
                "source": {
                    "rcept_no": record.get("rcept_no"),
                    "section_title": record.get("note_title"),
                    "source_table": "accounting_note_chapters",
                },
                "excerpt": record.get("body_excerpt"),
            })

    limitations = [
        "cache_miss_is_not_disclosure_absence",
        "search_reads_cached_accounting_note_chapters_only",
        "each_company_returns_at_most_10_matching_records",
    ]
    if next_offset is not None:
        limitations.append("company_results_are_paginated")
    if search_mode != "exact":
        limitations.append(
            f"match_expansion_applied:{search_mode}"
        )

    return {
        "query": {
            "dataset": "accounting_note_chapters",
            "keyword": normalized_keyword,
            "expanded_terms": terms,
            "year": year,
            "market": market,
            "induty_prefix": induty_prefix,
            "fs_div": fs_div,
            "section_type": section_type,
            "limit": limit,
            "offset": offset,
            "include_excerpt": include_excerpt,
            "search_mode": search_mode,
        },
        # Backward-compatible totals now mean the true full match totals.
        "total_companies": matched_companies,
        "total_records": matched_records,
        "matched_company_count": matched_companies,
        "matched_record_count": matched_records,
        "returned_company_count": len(companies),
        "returned_record_count": returned_record_count,
        "offset": offset,
        "next_offset": next_offset,
        "has_more": next_offset is not None,
        "truncated": next_offset is not None,
        "companies": companies,
        "confirmed_facts": confirmed_facts,
        "data_quality": {
            "status": (
                "usable"
                if matched_companies
                else "missing"
            ),
            "source": "accounting_note_chapters",
            "limitations": limitations,
            "search_scope": "cached_accounting_note_chapters",
            "interpretation": (
                "검색 건수는 전체 캐시 일치 건수를 별도로 계산하고, "
                "회사 페이지와 키워드 중심 excerpt를 반환합니다."
            ),
        },
        "next_checks": [
            "중요한 일치 건은 접수번호 링크에서 원 공시 문맥을 확인하세요.",
            "normalized 또는 synonym 검색 결과는 matched_term과 excerpt를 함께 검토하세요.",
        ],
    }
