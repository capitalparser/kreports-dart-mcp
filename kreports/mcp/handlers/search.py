"""Dataset, peer-selection, industry, and on-demand retrieval handlers."""
from __future__ import annotations

import re

from kreports.analysis.peer_benchmarks import (
    ResolvedPeerSubject,
    compare_to_industry,
    get_industry_audit_landscape,
)
from kreports.analysis.investor_peer_evidence import (
    compare_to_industry_multi_with_evidence,
    select_peer_group_with_evidence,
)
from kreports.analysis.financial_analysis import _annual_report_source
from kreports.analysis.filing_provenance import canonical_annual_filing_source_binding
from kreports.analysis.search_adapter import search_dataset
from kreports.collector.on_demand import fetch_disclosure_on_demand
from kreports.mcp.dispatch import resolve_company
from kreports.mcp.input_models import (
    CompareToIndustryInput,
    CompareToIndustryMultiInput,
    FetchDisclosureOnDemandInput,
    GetIndustryAuditLandscapeInput,
    SearchDatasetInput,
    SelectPeerGroupInput,
)


def handle_compare_to_industry(args: CompareToIndustryInput) -> dict:
    return compare_to_industry(
        company=resolve_company(args.company) if args.company else None,
        induty_code=args.induty_code,
        metric=args.metric,
        year=args.year,
        fs_div=args.fs_div,
        prefix_len=args.prefix_len,
        include_peers=args.include_peers,
        peer_limit=args.peer_limit,
    )


def handle_compare_to_industry_multi(args: CompareToIndustryMultiInput) -> dict:
    resolved_subject = ResolvedPeerSubject(resolve_company(args.company))
    return compare_to_industry_multi_with_evidence(
        company=resolved_subject.corp_code,
        metrics=args.metrics,
        years_back=args.years_back,
        fs_div=args.fs_div,
        fs_strategy=args.fs_strategy,
        prefix_len_start=args.prefix_len_start,
        exclude_other_sectors=args.exclude_other_sectors,
        size_bucket_decade=args.size_bucket_decade,
        _resolved_subject=resolved_subject,
    )


def handle_select_peer_group(args: SelectPeerGroupInput) -> dict:
    result = select_peer_group_with_evidence(
        company=resolve_company(args.company),
        criteria=args.peer_criteria or args.criteria,
        peer_limit=args.peer_limit,
        fs_strategy=args.fs_strategy,
        prefix_len_start=args.prefix_len_start,
        size_bucket_decade=args.size_bucket_decade,
        exclude_other_sectors=args.exclude_other_sectors,
    )
    subject = result.get("subject") or {}
    policy = result.get("selection_policy") or {}
    corp_code = subject.get("corp_code")
    if corp_code:
        resolved_year = policy.get("resolved_year")
        result["confirmed_facts"] = [{
            "statement": (
                f"선정 정책에 따라 비교기업 "
                f"{result.get('returned_peer_count', len(result.get('peers') or []))}"
                "개를 구성했습니다."
            ),
            "source": _annual_report_source(
                str(corp_code),
                subject,
                int(resolved_year) if resolved_year else None,
                section_title="재무제표",
                source_table="peer_cohort",
            ),
            "excerpt": (
                f"resolved_year={resolved_year}, "
                f"fs_div={policy.get('fs_div_used')}"
            ),
        }]
    return result


def handle_search_dataset(args: SearchDatasetInput) -> dict:
    result = search_dataset(**args.model_dump())
    if args.dataset == "accounting_note_chapters":
        return _enrich_accounting_note_search(result)
    return _enrich_search_dataset_evidence(result)


_DART_RECEIPT_NO = re.compile(r"^[0-9]{14}$", re.ASCII)
_MAX_NOTE_DISCLOSURE_COMPANY_MATRIX_ROWS = 200
_MAX_NOTE_CONFIRMED_FACTS = 20
_SOURCE_REQUIRED_SEARCH_DATASETS = {
    "source_documents",
    "report_sections",
    "accounting_policies",
    "evidence_documents",
    "disclosures",
    "audit_fees",
    "financials",
}
_NOTE_AUDIT_GUIDANCE = (
    (
        ("수익", "revenue"),
        "수익 인식 정책 문구는 수행의무, 거래가격 및 기간귀속 판단의 적용 일관성을 점검할 스크리닝 근거입니다. 이는 감사 결론이 아닙니다.",
    ),
    (
        ("재고", "inventory"),
        "재고자산 정책 문구는 평균법과 순실현가능가치 평가의 적용 일관성 및 기말 평가 추정을 점검할 스크리닝 근거입니다. 이는 감사 결론이 아닙니다.",
    ),
    (
        ("충당", "provision"),
        "충당부채 정책 문구는 의무 발생 여부와 최선추정액 산정 근거를 점검할 스크리닝 근거입니다. 이는 감사 결론이 아닙니다.",
    ),
    (
        ("추정", "estimate"),
        "회계추정 관련 문구는 주요 가정, 민감도 및 추정 변경의 근거를 점검할 스크리닝 근거입니다. 이는 감사 결론이 아닙니다.",
    ),
    (
        ("손상", "impairment"),
        "손상 관련 문구는 손상징후, 현금흐름 추정 및 할인율 가정을 점검할 스크리닝 근거입니다. 이는 감사 결론이 아닙니다.",
    ),
    (
        ("우발", "contingen"),
        "우발사항 관련 문구는 의무의 존재, 발생가능성 및 공시 충분성을 점검할 스크리닝 근거입니다. 이는 감사 결론이 아닙니다.",
    ),
)


def _enrich_search_dataset_evidence(result: dict) -> dict:
    """Bind generic search rows to filing receipts or limit the public claim."""
    enriched = dict(result)
    query = enriched.get("query") if isinstance(enriched.get("query"), dict) else {}
    dataset = str(query.get("dataset") or "")
    facts: list[dict] = []

    for company in enriched.get("companies") or []:
        if not isinstance(company, dict):
            continue
        corp_name = str(
            company.get("corp_name")
            or company.get("stock_code")
            or company.get("corp_code")
            or "대상 회사"
        )
        for record in company.get("records") or []:
            if not isinstance(record, dict):
                continue
            receipt = str(record.get("rcept_no") or "").strip()
            if not _DART_RECEIPT_NO.fullmatch(receipt):
                continue
            year = record.get("year")
            section_title = (
                record.get("section_title")
                or record.get("note_title")
                or record.get("report_nm")
                or dataset
            )
            facts.append({
                "statement": (
                    f"{corp_name}의 {year}년 {dataset} 조회 레코드를 "
                    "공시 접수번호와 연결했습니다."
                    if year is not None
                    else f"{corp_name}의 {dataset} 조회 레코드를 공시 접수번호와 연결했습니다."
                ),
                "source": {
                    "source_label": "DART 공시",
                    "source_url": (
                        "https://dart.fss.or.kr/dsaf001/main.do?"
                        f"rcpNo={receipt}"
                    ),
                    "rcept_no": receipt,
                    "section_title": str(section_title),
                },
                "excerpt": str(
                    record.get("body_excerpt")
                    or record.get("excerpt")
                    or ""
                )[:600] or None,
            })
            if len(facts) >= 20:
                break
        if len(facts) >= 20:
            break

    if facts:
        enriched["confirmed_facts"] = facts
        return enriched

    if (
        dataset in _SOURCE_REQUIRED_SEARCH_DATASETS
        and int(enriched.get("total_records") or 0) > 0
    ):
        raw_quality = (
            enriched.get("data_quality")
            if isinstance(enriched.get("data_quality"), dict)
            else {}
        )
        limitations = [
            str(item)
            for item in raw_quality.get("limitations") or []
            if str(item).strip()
        ]
        limitations.append(
            "조회 레코드의 DART 접수번호를 확인하지 못해 수치와 공시 원문을 "
            "직접 연결할 수 없습니다."
        )
        enriched["data_quality"] = {
            **raw_quality,
            "status": "limited",
            "limitations": list(dict.fromkeys(limitations)),
        }
    return enriched


def _note_audit_implication(topic: str) -> str:
    normalized = topic.casefold()
    for hints, guidance in _NOTE_AUDIT_GUIDANCE:
        if any(hint.casefold() in normalized for hint in hints):
            return guidance
    return (
        "주석 문구는 해당 회계정책 또는 공시 판단의 적용 근거를 원 공시와 대조할 "
        "스크리닝 근거입니다. 이는 감사 결론이 아닙니다."
    )


def _note_next_checks(topic: str) -> list[str]:
    """Return conservative, topic-specific audit follow-up prompts."""
    normalized = topic.casefold()
    if any(hint in normalized for hint in ("수익", "revenue")):
        return [
            "수행의무, 통제이전 또는 기간귀속 판단이 계약 조건과 일치하는지 확인하세요.",
            "변동대가와 매출차감의 추정·제한 및 기간별 반영 근거를 확인하세요.",
            "해당 주석 전문과 관련 공시의 후속 변경사항을 검토하세요.",
        ]
    if any(hint in normalized for hint in ("재고", "inventory")):
        return [
            "재고 실사와 수량 확인 결과가 기말 잔액 및 이동 내역과 일치하는지 확인하세요.",
            "원가, 순실현가능가치 및 진부화 평가에 사용한 가정과 근거를 검토하세요.",
            "해당 주석 전문과 관련 공시의 후속 변경사항을 검토하세요.",
        ]
    if any(hint in normalized for hint in ("충당", "provision")):
        return [
            "현재의무 완전성과 미인식 의무의 존재 여부를 관련 계약 및 법률 자문과 대조하세요.",
            "과거 보증청구, 최선추정 또는 사후실적이 충당부채 측정 근거와 일치하는지 확인하세요.",
            "해당 주석 전문과 관련 공시의 후속 변경사항을 검토하세요.",
        ]
    return [
        "관련 잔액과 비교표시 금액을 원 공시와 대조하세요.",
        "주요 회계추정 입력과 근거를 검토하세요.",
        "해당 주석 전문과 관련 공시의 후속 변경사항을 검토하세요.",
    ]


def _note_passages(record: dict, *, keyword: str) -> list[str]:
    raw_passages = record.get("match_excerpts")
    if not isinstance(raw_passages, list) or not raw_passages:
        raw_passages = [record.get("body_excerpt")]

    passages: list[str] = []
    seen: set[str] = set()
    for raw_passage in raw_passages:
        passage = " ".join(str(raw_passage or "").split())
        if not passage or (keyword and keyword not in passage):
            continue
        if passage in seen:
            continue
        seen.add(passage)
        passages.append(passage)
    return passages


def _note_reference(record: dict) -> str:
    note_no = str(record.get("note_no") or "").strip()
    note_title = str(record.get("note_title") or "").strip()
    prefix = f"주석 {note_no}" if note_no else "주석"
    nested_title = re.match(r"^([0-9]+)(?:\s*[.)]\s*|\s+)(.+)$", note_title)
    if note_no and nested_title:
        return f"{prefix} · {nested_title.group(1)} {nested_title.group(2)}"
    return f"{prefix} {note_title}".strip()


def _note_disclosure_company_matrix(enriched: dict, query: dict) -> dict:
    """Expose one bounded local-cache match row per company.

    This is an inverse lookup over the existing note search.  It deliberately
    separates a canonical annual-filing match from a cache-only match.
    """
    companies: list[dict] = []
    for company in enriched.get("companies") or []:
        if not isinstance(company, dict):
            continue
        records = [
            item for item in company.get("records") or []
            if isinstance(item, dict)
        ]
        if not records:
            continue
        corp_code = str(company.get("corp_code") or "")
        canonical_record = next(
            (item for item in records if item.get("canonical_source_binding")),
            None,
        )
        companies.append({
            "corp_code": corp_code,
            "corp_name": company.get("corp_name"),
            "market": company.get("market"),
            "induty_code": company.get("induty_code"),
            "year": query.get("year"),
            "match_status": (
                "verified_annual_filing_match"
                if canonical_record is not None else "unverified_cache_match"
            ),
            "record_count": int(company.get("record_count") or len(records)),
            "canonical_rcept_no": (
                canonical_record.get("rcept_no") if canonical_record is not None else None
            ),
            "canonical_note_title": (
                canonical_record.get("note_title") if canonical_record is not None else None
            ),
        })
        if len(companies) >= _MAX_NOTE_DISCLOSURE_COMPANY_MATRIX_ROWS:
            break
    return {
        "scope": {
            "keyword": query.get("keyword"),
            "year": query.get("year"),
            "market": query.get("market"),
            "induty_prefix": query.get("induty_prefix"),
        },
        "configured_limit": query.get("limit"),
        "returned_company_count": len(companies),
        "is_exhaustive": False,
        "limitations": [
            "캐시 일치는 규제상 공시 완전성 또는 공시 부재 결론이 아니며, 원 공시 주석 전문 확인이 필요합니다.",
            "반환 회사 수는 응답 경계를 위해 최대 200개이며, 검색 결과 전체를 대표하지 않습니다.",
        ],
        "companies": companies,
    }


def _enrich_accounting_note_search(result: dict) -> dict:
    """Turn cached note passages into fail-closed, filing-backed MCP evidence."""
    enriched = dict(result)
    query = enriched.get("query") if isinstance(enriched.get("query"), dict) else {}
    keyword = str(query.get("keyword") or "").strip()
    topic = keyword or "회계주석"
    facts: list[dict] = []
    matched_row_count = 0
    canonical_matched_row_count = 0
    seen_passages: set[tuple[str, str, str, str]] = set()

    for company in enriched.get("companies") or []:
        if not isinstance(company, dict):
            continue
        corp_code = str(company.get("corp_code") or "")
        corp_name = company.get("corp_name") or corp_code or "대상 회사"
        for record in company.get("records") or []:
            if not isinstance(record, dict):
                continue
            matched_row_count += 1
            cached_receipt = record.get("rcept_no")
            receipt = canonical_annual_filing_source_binding(
                record,
                corp_code=corp_code,
                bsns_year=record.get("year") or query.get("year"),
            )
            record["cached_rcept_no"] = cached_receipt
            record["rcept_no"] = receipt
            record["canonical_source_binding"] = receipt is not None
            record["provenance_status"] = (
                "proven_annual_filing" if receipt else "unproven_source_binding"
            )
            if receipt is None:
                continue
            canonical_matched_row_count += 1
            note_reference = _note_reference(record)
            raw_receipt = str(receipt or "").strip()
            for passage in _note_passages(record, keyword=keyword):
                passage_key = (corp_code, raw_receipt, note_reference, passage)
                if passage_key in seen_passages:
                    continue
                seen_passages.add(passage_key)
                facts.append({
                    "statement": f"{note_reference}: {passage}",
                    "excerpt": passage,
                    "topic": topic,
                    "year": record.get("year") or query.get("year"),
                    "fs_div": record.get("fs_div") or query.get("fs_div"),
                    "note_reference": note_reference,
                    "source": {
                        "corp_code": corp_code,
                        "corp_name": corp_name,
                        "rcept_no": receipt,
                        "report_nm": "사업보고서",
                        "source_table": "accounting_note_chapters",
                        "section_title": note_reference,
                    },
                })

    confirmed_facts_omitted_count = max(0, len(facts) - _MAX_NOTE_CONFIRMED_FACTS)
    if confirmed_facts_omitted_count:
        facts = facts[:_MAX_NOTE_CONFIRMED_FACTS]

    unverified_cache_match_count = matched_row_count - canonical_matched_row_count
    if not matched_row_count:
        status = "missing"
        coverage_note = "로컬 캐시에 일치하는 회계주석 근거가 없습니다."
    elif facts and unverified_cache_match_count == 0:
        status = "usable"
        coverage_note = (
            f"반환된 일치 주석 행 {canonical_matched_row_count}건 모두 canonical 연간 "
            "DART source binding과 14자리 접수번호를 함께 확인했습니다."
        )
    else:
        status = "limited"
        coverage_note = (
            f"일치 주석 행 {matched_row_count}건 중 canonical 연간 source binding 확인 "
            f"{canonical_matched_row_count}건, 미검증 캐시 일치 "
            f"{unverified_cache_match_count}건입니다. 미검증 행은 14자리 접수번호가 "
            "있어도 사용자용 근거 또는 링크로 승격하지 않습니다."
        )

    data_quality = dict(enriched.get("data_quality") or {})
    limitations = [
        str(item)
        for item in data_quality.get("limitations") or []
        if str(item).strip()
    ]
    if confirmed_facts_omitted_count:
        limitations.append(
            f"confirmed_facts_output_truncated:{confirmed_facts_omitted_count}"
        )
    data_quality.update({
        "status": status,
        "source": "accounting_note_chapters",
        "coverage_note": coverage_note,
        "limitations": list(dict.fromkeys(limitations)),
        "interpretation": (
            "반환된 주석 발췌문은 로컬 캐시 기반의 스크리닝 근거이며, "
            "중요 판단 전 원 공시 주석 전문을 확인해야 합니다."
        ),
    })
    enriched["confirmed_facts"] = facts
    enriched["confirmed_facts_truncation"] = {
        "applied": bool(confirmed_facts_omitted_count),
        "max_rows": _MAX_NOTE_CONFIRMED_FACTS,
        "omitted_count": confirmed_facts_omitted_count,
    }
    enriched["analysis"] = [{
        "statement": _note_audit_implication(topic),
        "perspective": "auditor",
        "basis": "반환된 회계주석 발췌문과 요청 키워드",
    }] if facts else []
    enriched["next_checks"] = (
        [
            "원 공시의 해당 주석 전문을 직접 확인하세요.",
            "필요하면 최신 수집본으로 로컬 캐시를 보완한 뒤 다시 조회하세요.",
        ]
        if status == "missing"
        else _note_next_checks(topic)
    )
    enriched["data_quality"] = data_quality
    enriched["note_disclosure_company_matrix"] = _note_disclosure_company_matrix(
        enriched, query,
    )
    return enriched


def handle_fetch_disclosure_on_demand(args: FetchDisclosureOnDemandInput) -> dict:
    # Secret is unwrapped only at this ephemeral external-fetch boundary.
    user_key = (
        args.user_dart_api_key.get_secret_value()
        if args.user_dart_api_key is not None
        else None
    )
    return fetch_disclosure_on_demand(
        rcept_no=args.rcept_no,
        user_dart_api_key=user_key,
        cache_policy=args.cache_policy,
        corp_code=args.corp_code,
        year=args.year,
    )


def handle_get_industry_audit_landscape(
    args: GetIndustryAuditLandscapeInput,
) -> dict:
    return get_industry_audit_landscape(
        company=resolve_company(args.company) if args.company else None,
        induty_code=args.induty_code,
        years_back=args.years_back,
        fs_div=args.fs_div,
        prefix_len_start=args.prefix_len_start,
        top_n=args.top_n,
        exclude_other_sectors=args.exclude_other_sectors,
    )
