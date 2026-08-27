"""Plain-language, link-rich presentation for the internal KReports chatbot.

This module deliberately sits after domain computation. It does not change
facts, peer selection, statistics, or source evidence. It only controls what a
business user sees first: a direct answer, five-company pages, human-readable
labels, and links to the underlying DART filing whenever a receipt number is
available.
"""
from __future__ import annotations

import html
import re
from typing import Any

from kreports.mcp.chatbot_contracts import (
    ChatbotColumnV1,
    ChatbotMetricV1,
    ChatbotTableV1,
    ChatbotViewV1,
    build_chatbot_visualization_pack,
)
from kreports.mcp.visual_contracts import build_visualization_pack


COMPANY_PAGE_SIZE = 5
_MAX_ANSWER_CHARS = 16_000
_DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

_TOPIC_LABELS = {
    "revenue": "수익인식",
    "leases": "리스",
    "financial_instruments": "금융상품",
    "related_parties": "특수관계자",
    "provisions_contingencies": "충당부채·우발사항",
    "impairment": "손상",
    "subsidiaries": "종속기업",
    "subsequent_events": "후속사건",
    "accounting_policies": "회계정책",
}

_REASON_LABELS = {
    "same_ksic_prefix": "같은 업종",
    "same_sector_group": "같은 산업군",
    "asset_size_bucket": "회사 규모가 유사",
    "audit_fee_available": "비교자료 확보",
    "explicit_custom_code": "사용자가 직접 선택",
    "explicit_included_corp_code": "사용자가 직접 포함",
    "financial_data": "재무자료 확보",
}

_SEARCH_MODE_LABELS = {
    "exact": "입력한 문구 그대로",
    "normalized": "띄어쓰기와 기호 차이까지 포함",
    "synonym": "유사한 표현까지 포함",
}

_AVAILABILITY_LABELS = {
    "available": "원문 확인 가능",
    "summary_only": "요약 내용만 확인됨",
    "unavailable": "현재 확보된 자료 없음",
}

_FS_SELECTION_LABELS = {
    "exact": "요청 기준과 일치",
    "fallback_requested_fs_div_unavailable": "다른 재무제표 기준 사용",
    "fallback_no_cohort_fs_div": "확보된 재무제표 기준 사용",
    "unavailable_no_cached_note": "현재 확보된 자료 없음",
}

_CONFIDENCE_LABELS = {
    "high": "높음",
    "medium": "보통",
    "low": "낮음",
    "insufficient": "비교기업 부족",
    "sufficient_n": "비교 가능",
    "insufficient_n": "비교기업 부족",
    "subject_unavailable": "대상회사 수치 미확보",
}

_INTERNAL_MARKERS = (
    "answer_pack",
    "_meta",
    "local_kreports_db",
    "schema_version",
    "dataset_version",
    "cohort_id",
    "member_codes_hash",
    "criteria_hash",
    "mid-rank",
    "midrank",
    "coverage",
    "summary_only",
    "unavailable",
    "different_normalized_text",
    "fallback_with_warning",
    "selection_score",
    "include_reasons",
)


def _safe_text(value: Any, *, limit: int = 1_200) -> str:
    text = " ".join(str(value if value is not None else "-").split())
    return text[:limit]


def _markdown_cell(value: Any) -> str:
    text = str(value if value is not None else "-")
    if re.fullmatch(
        r"\[[^\]\n]{1,200}\]\(https://dart\.fss\.or\.kr/[^)\n]{1,500}\)",
        text,
    ):
        return text
    escaped = html.escape(_safe_text(text, limit=1_000), quote=False)
    return escaped.replace("\\", "\\\\").replace("|", "\\|")


def _fs_div_label(value: Any) -> str:
    normalized = str(value or "").upper()
    if normalized == "CFS":
        return "연결"
    if normalized == "OFS":
        return "별도"
    return "미확정"


def _confidence_label(value: Any) -> str:
    return _CONFIDENCE_LABELS.get(str(value or ""), "확인 필요")


def _topic_label(value: Any) -> str:
    return _TOPIC_LABELS.get(str(value or ""), _safe_text(value, limit=80))


def _format_amount(value: Any) -> str:
    if value in {None, ""}:
        return "미확보"
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return _safe_text(value, limit=80)
    absolute = abs(amount)
    if absolute >= 1_000_000_000_000:
        return f"{amount / 1_000_000_000_000:,.1f}조원"
    if absolute >= 100_000_000:
        return f"{amount / 100_000_000:,.0f}억원"
    if absolute >= 1_000_000:
        return f"{amount / 1_000_000:,.0f}백만원"
    return f"{amount:,.0f}원"


def _format_number(value: Any, unit: Any = None) -> str:
    if value in {None, ""}:
        return "미확보"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _safe_text(value, limit=80)
    normalized_unit = str(unit or "")
    if normalized_unit == "%":
        return f"{number:,.1f}%"
    if normalized_unit in {"KRW", "원"}:
        return _format_amount(number)
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _position_text(percentile: Any) -> str:
    if percentile is None:
        return "비교자료 부족"
    try:
        value = max(0.0, min(100.0, float(percentile)))
    except (TypeError, ValueError):
        return "확인 필요"
    if value >= 80:
        return f"높은 편 · 상위 약 {max(1, round(100 - value))}%"
    if value >= 60:
        return "다소 높은 편"
    if value >= 40:
        return "중간 수준"
    if value >= 20:
        return "다소 낮은 편"
    return f"낮은 편 · 하위 약 {max(1, round(value))}%"


def _reason_label(reason: Any) -> str:
    raw = str(reason or "")
    if raw in _REASON_LABELS:
        return _REASON_LABELS[raw]
    if raw.startswith("sector_group:"):
        return "같은 산업군"
    if raw.startswith("missing_feature:"):
        return "필요 자료 미확보"
    if raw.startswith("size_metric_"):
        return "회사 규모 기준"
    if raw.startswith("same_ksic"):
        return "같은 업종"
    return "선정 기준 충족"


def _receipt_link(rcept_no: Any, label: str = "공시 보기") -> str | None:
    receipt = str(rcept_no or "")
    if len(receipt) != 14 or not receipt.isdigit():
        return None
    return f"[{label}]({_DART_URL}{receipt})"


def _humanize_warning(value: Any) -> str | None:
    raw = _safe_text(value, limit=600)
    if not raw:
        return None
    if raw in {
        "chatbot_peer_table_is_truncated",
        "result_count_is_bounded_by_limit",
    }:
        return None
    if raw == "statistical_peer_count_below_5":
        return "비교 가능한 기업이 5개 미만이어서 상대 위치 해석이 제한적입니다."
    if raw == "statistical_universe_exceeded_internal_safety_bound":
        return "비교 대상이 매우 많아 일부 기업만 분석에 포함됐습니다."
    if raw == "fewer_than_80_percent_of_metric_year_cells_have_n_at_least_5":
        return "일부 연도·지표는 비교 가능한 기업 수가 충분하지 않습니다."
    if raw == "some_subject_or_peer_metric_year_values_are_unavailable":
        return "일부 수치는 현재 확보된 자료에서 확인되지 않습니다."
    if raw == "chatbot_peer_table_is_truncated_but_statistics_use_full_cohort":
        return "화면에는 일부 기업만 표시하지만 상대 위치 계산에는 전체 비교기업을 사용했습니다."
    if raw == "cache_miss_is_not_disclosure_absence":
        return "검색 결과가 없더라도 원 공시에 해당 내용이 없다고 단정할 수는 없습니다."
    if raw.startswith("fs_basis_fallback_rows:"):
        return "요청한 재무제표 기준이 없어 다른 기준을 사용한 항목이 있습니다."
    if raw == "fallback_rows_excluded_by_strict_fs_basis":
        return "요청한 재무제표 기준과 다른 자료는 비교에서 제외했습니다."
    if raw == "note_cell_coverage_below_80_percent":
        return "일부 회사의 주석 자료가 확보되지 않아 비교 범위가 제한적입니다."
    if raw == "subject_note_topic_coverage_incomplete":
        return "대상회사의 일부 주석 주제가 현재 자료에서 확인되지 않습니다."
    if raw == "note_comparison_topics_unavailable":
        return "비교할 주석 자료를 현재 확보하지 못했습니다."
    if "cached" in raw.lower() and "absence" in raw.lower():
        return "현재 확보된 자료의 검색 결과이며 원 공시 전체의 부재를 의미하지 않습니다."
    if any(marker.lower() in raw.lower() for marker in _INTERNAL_MARKERS):
        return "일부 자료의 범위가 제한되어 결과를 해석할 때 주의가 필요합니다."
    if re.fullmatch(r"[a-z0-9_:./-]+", raw):
        return "일부 자료의 범위가 제한되어 결과를 해석할 때 주의가 필요합니다."
    return raw


def _humanize_warnings(values: list[Any]) -> list[str]:
    warnings: list[str] = []
    for value in values:
        warning = _humanize_warning(value)
        if warning and warning not in warnings:
            warnings.append(warning)
    return warnings[:8]


def _pagination(result: dict[str, Any], *, row_count: int) -> dict[str, Any]:
    raw = result.get("pagination")
    pagination = raw if isinstance(raw, dict) else {}
    query = result.get("query")
    query = query if isinstance(query, dict) else {}
    offset = int(pagination.get("offset", query.get("offset", 0)) or 0)
    total = int(
        pagination.get("total_peer_count")
        or result.get("statistical_member_count")
        or result.get("matched_company_count")
        or result.get("total_companies")
        or row_count
        or 0
    )
    page_size = COMPANY_PAGE_SIZE
    returned = min(row_count, page_size)
    has_more = bool(
        pagination.get("has_more")
        if "has_more" in pagination
        else offset + returned < total
    )
    return {
        "offset": offset,
        "page_size": page_size,
        "total": total,
        "returned": returned,
        "start": offset + 1 if returned else 0,
        "end": offset + returned,
        "has_more": has_more,
        "next_offset": offset + returned if has_more else None,
        "previous_offset": max(0, offset - page_size) if offset else None,
    }


def _peer_group_view(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    subject = view.subject
    peers = [row for row in result.get("peers") or [] if isinstance(row, dict)]
    page = _pagination(result, row_count=len(peers))
    policy = result.get("selection_policy") or {}
    year = policy.get("resolved_year") or policy.get("requested_year")
    fs_label = _fs_div_label(policy.get("fs_div_used"))
    total = int(result.get("statistical_member_count") or result.get("peer_count") or len(peers))
    confidence = _confidence_label(result.get("confidence"))

    rows: list[dict[str, Any]] = []
    for peer in peers[:COMPANY_PAGE_SIZE]:
        reasons = []
        for reason in peer.get("include_reasons") or []:
            label = _reason_label(reason)
            if label not in reasons:
                reasons.append(label)
        source_link = _receipt_link(
            peer.get("rcept_no") or peer.get("source_rcept_no"),
        )
        rows.append({
            "company": peer.get("corp_name") or peer.get("corp_code"),
            "stock_code": peer.get("stock_code") or "-",
            "market": peer.get("market") or "-",
            "total_assets": _format_amount(peer.get("total_assets")),
            "revenue": _format_amount(peer.get("revenue")),
            "reason": ", ".join(reasons[:3]) or "선정 기준 충족",
            "source": source_link or "-",
        })

    if total:
        summary = (
            f"{year or '최근'}년 {fs_label}재무제표 기준으로 {subject}와 업종·규모가 "
            f"유사한 상장사 {total}개를 선정했습니다. "
            f"아래에는 {page['start']}~{page['end']}번째 회사를 보여드립니다."
        )
    else:
        summary = "현재 확보된 자료에서는 비교하기 적절한 상장사를 찾지 못했습니다."

    next_actions = []
    if page["has_more"]:
        next_actions.append("다음 5개 비교회사를 보여줘.")
    if page["previous_offset"] is not None:
        next_actions.append("이전 5개 비교회사를 보여줘.")
    next_actions.extend([
        "비교회사 선정 기준을 자세히 보여줘.",
        "선정에서 제외된 회사와 이유를 보여줘.",
    ])

    return view.model_copy(update={
        "title": f"{subject} 비교회사",
        "summary": summary,
        "metrics": [
            ChatbotMetricV1(label="전체 비교회사", value=total, unit="개"),
            ChatbotMetricV1(label="현재 표시", value=len(rows), unit="개"),
            ChatbotMetricV1(label="기준연도", value=year or "미확정"),
            ChatbotMetricV1(label="재무제표 기준", value=fs_label),
            ChatbotMetricV1(label="비교 신뢰도", value=confidence),
        ],
        "tables": [ChatbotTableV1(
            id="peer_members",
            title=f"비교회사 {page['start']}~{page['end']}",
            columns=[
                ChatbotColumnV1(key="company", label="회사"),
                ChatbotColumnV1(key="stock_code", label="종목코드"),
                ChatbotColumnV1(key="market", label="시장"),
                ChatbotColumnV1(key="total_assets", label="총자산"),
                ChatbotColumnV1(key="revenue", label="매출"),
                ChatbotColumnV1(key="reason", label="선정 이유"),
                ChatbotColumnV1(key="source", label="관련 공시"),
            ],
            rows=rows,
            note=(
                f"전체 {total}개 중 5개씩 보여드립니다. "
                "상대 위치 계산에는 화면에 보이지 않는 비교회사도 함께 사용합니다."
            ),
        )],
        "warnings": _humanize_warnings(list(view.warnings)),
        "next_actions": next_actions,
        "initially_visible_rows": COMPANY_PAGE_SIZE,
        "raw_text_default_collapsed": True,
    })


def _benchmark_view(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    subject = view.subject
    all_results = result.get("results") or {}
    years = sorted((int(value) for value in all_results), reverse=True)
    latest_year = years[0] if years else result.get("resolved_year")
    latest = all_results.get(latest_year) or all_results.get(str(latest_year)) or {}

    highlights: list[str] = []
    for metric, values in latest.items():
        if not isinstance(values, dict) or values.get("percentile") is None:
            continue
        highlights.append(
            f"{metric}은 {_position_text(values.get('percentile'))}"
        )
        if len(highlights) == 3:
            break
    if highlights:
        summary = f"{latest_year}년 기준 " + ", ".join(highlights) + "입니다."
    else:
        summary = "현재 자료만으로는 동종기업 대비 상대 위치를 충분히 계산하기 어렵습니다."

    rows: list[dict[str, Any]] = []
    for year in sorted((int(value) for value in all_results), reverse=True):
        metrics = all_results.get(year) or all_results.get(str(year)) or {}
        for metric, values in metrics.items():
            if not isinstance(values, dict):
                continue
            unit = values.get("unit")
            rows.append({
                "year": year,
                "metric": metric,
                "subject_value": _format_number(values.get("subject_value"), unit),
                "peer_median": _format_number(values.get("p50"), unit),
                "position": _position_text(values.get("percentile")),
                "peer_count": values.get("n") or 0,
                "data_rate": (
                    f"{float(values.get('coverage_pct')):,.0f}%"
                    if values.get("coverage_pct") is not None
                    else "미확보"
                ),
            })

    quality = result.get("data_quality") or {}
    total_peers = int(result.get("peer_count") or result.get("n_peers") or 0)
    sufficient = quality.get("sufficient_cell_pct")
    return view.model_copy(update={
        "title": f"{subject} 동종기업 대비 실적",
        "summary": summary,
        "metrics": [
            ChatbotMetricV1(label="비교기업", value=total_peers, unit="개"),
            ChatbotMetricV1(label="기준연도", value=latest_year or "미확정"),
            ChatbotMetricV1(
                label="자료가 충분한 비교항목",
                value=(f"{float(sufficient):,.0f}%" if sufficient is not None else "확인 필요"),
            ),
        ],
        "tables": [ChatbotTableV1(
            id="peer_benchmark",
            title="주요 지표의 상대 위치",
            columns=[
                ChatbotColumnV1(key="year", label="연도"),
                ChatbotColumnV1(key="metric", label="지표"),
                ChatbotColumnV1(key="subject_value", label="회사 값"),
                ChatbotColumnV1(key="peer_median", label="동종기업 중앙값"),
                ChatbotColumnV1(key="position", label="상대 위치"),
                ChatbotColumnV1(key="peer_count", label="비교기업 수"),
                ChatbotColumnV1(key="data_rate", label="자료 확보율"),
            ],
            rows=rows,
            note="수치의 높고 낮음은 상대 위치를 뜻하며, 그 자체로 우수·부진 판단을 의미하지 않습니다.",
        )],
        "warnings": _humanize_warnings(list(view.warnings)),
        "next_actions": [
            "비교회사 5개를 보여줘.",
            "가장 차이가 큰 지표를 설명해줘.",
            "연도별 변화가 큰 지표를 설명해줘.",
        ],
        "initially_visible_rows": 8,
    })


def _note_search_view(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    query = result.get("query") or {}
    keyword = str(query.get("keyword") or "검색 문구")
    companies = [row for row in result.get("companies") or [] if isinstance(row, dict)]
    page = _pagination(result, row_count=len(companies))
    total_companies = int(
        result.get("matched_company_count")
        or result.get("total_companies")
        or 0
    )
    total_records = int(
        result.get("matched_record_count")
        or result.get("total_records")
        or 0
    )
    mode = _SEARCH_MODE_LABELS.get(
        str(query.get("search_mode") or "exact"),
        "입력한 문구 그대로",
    )

    rows: list[dict[str, Any]] = []
    for company in companies[:COMPANY_PAGE_SIZE]:
        records = [row for row in company.get("records") or [] if isinstance(row, dict)]
        record = records[0] if records else {}
        rows.append({
            "company": company.get("corp_name") or company.get("corp_code"),
            "year": record.get("year") or "-",
            "fs_div": _fs_div_label(record.get("fs_div")),
            "note_title": record.get("note_title") or record.get("note_no") or "-",
            "matched_term": record.get("matched_term") or keyword,
            "excerpt": record.get("body_excerpt") or "관련 문구 미확보",
            "source": _receipt_link(record.get("rcept_no")) or "-",
        })

    year_text = f"{query.get('year')}년 " if query.get("year") else ""
    summary = (
        f"현재 확보된 {year_text}사업보고서에서 '{keyword}' 관련 문구가 확인된 회사는 "
        f"{total_companies}개입니다. {mode} 검색했으며, 아래에는 "
        f"{page['start']}~{page['end']}번째 회사를 보여드립니다."
    )
    next_actions = []
    if page["has_more"]:
        next_actions.append("다음 5개 회사를 보여줘.")
    if page["previous_offset"] is not None:
        next_actions.append("이전 5개 회사를 보여줘.")
    next_actions.extend([
        "특정 회사의 해당 주석 전체 문구를 보여줘.",
        "같은 업종 회사만 다시 찾아줘.",
    ])

    return view.model_copy(update={
        "title": f"'{keyword}' 관련 공시회사",
        "summary": summary,
        "metrics": [
            ChatbotMetricV1(label="관련 회사", value=total_companies, unit="개"),
            ChatbotMetricV1(label="관련 주석", value=total_records, unit="건"),
            ChatbotMetricV1(label="현재 표시", value=len(rows), unit="개"),
            ChatbotMetricV1(label="검색 범위", value=mode),
        ],
        "tables": [ChatbotTableV1(
            id="note_search_results",
            title=f"관련 회사 {page['start']}~{page['end']}",
            columns=[
                ChatbotColumnV1(key="company", label="회사"),
                ChatbotColumnV1(key="year", label="연도"),
                ChatbotColumnV1(key="fs_div", label="재무제표"),
                ChatbotColumnV1(key="note_title", label="주석"),
                ChatbotColumnV1(key="matched_term", label="확인된 표현"),
                ChatbotColumnV1(key="excerpt", label="관련 문구"),
                ChatbotColumnV1(key="source", label="원 공시"),
            ],
            rows=rows,
            note="회사별 대표 일치 문구 1건을 보여드립니다. 링크에서 주석 전체 문맥을 확인할 수 있습니다.",
        )],
        "warnings": _humanize_warnings(list(view.warnings)),
        "next_actions": next_actions,
        "initially_visible_rows": COMPANY_PAGE_SIZE,
        "raw_text_default_collapsed": True,
    })


def _note_comparison_view(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    subject = view.subject
    topics = [row for row in result.get("topics") or [] if isinstance(row, dict)]
    differences = [row for row in result.get("differences") or [] if isinstance(row, dict)]
    subject_code = str((result.get("subject") or {}).get("corp_code") or "")

    company_map: dict[str, dict[str, Any]] = {}
    for topic in topics:
        topic_key = str(topic.get("topic") or "")
        for row in topic.get("rows") or []:
            if not isinstance(row, dict):
                continue
            company = row.get("company") or {}
            code = str(company.get("corp_code") or "")
            if not code or code == subject_code:
                continue
            item = company_map.setdefault(code, {
                "company": company.get("corp_name") or code,
                "available_topics": 0,
                "different_topics": [],
                "basis_notes": [],
                "rcept_no": None,
            })
            availability = str(row.get("availability") or "")
            if availability in {"available", "summary_only"}:
                item["available_topics"] += 1
            selection = row.get("fs_div_selection") or {}
            status = str(selection.get("status") or "") if isinstance(selection, dict) else ""
            basis_label = _FS_SELECTION_LABELS.get(status)
            if basis_label and basis_label not in item["basis_notes"]:
                item["basis_notes"].append(basis_label)
            if row.get("rcept_no") and not item["rcept_no"]:
                item["rcept_no"] = row.get("rcept_no")
        for difference in differences:
            if str(difference.get("topic") or "") != topic_key:
                continue
            code = str(difference.get("peer_corp_code") or "")
            if code in company_map:
                label = _topic_label(topic_key)
                if label not in company_map[code]["different_topics"]:
                    company_map[code]["different_topics"].append(label)

    pagination = result.get("pagination") or {}
    page = _pagination(result, row_count=len(company_map))
    if isinstance(pagination, dict):
        page["offset"] = int(pagination.get("offset") or 0)
        page["total"] = int(pagination.get("total_peer_count") or len(company_map))
        page["has_more"] = bool(pagination.get("has_more"))
        page["next_offset"] = pagination.get("next_page_token")
        page["start"] = page["offset"] + 1 if company_map else 0
        page["end"] = page["offset"] + min(len(company_map), COMPANY_PAGE_SIZE)

    rows: list[dict[str, Any]] = []
    for item in list(company_map.values())[:COMPANY_PAGE_SIZE]:
        different = item["different_topics"]
        rows.append({
            "company": item["company"],
            "different_topics": ", ".join(different) if different else "뚜렷한 문구 차이 없음",
            "available_topics": f"{item['available_topics']}개 주제 확인",
            "basis": ", ".join(item["basis_notes"]) or "확인 필요",
            "source": _receipt_link(item["rcept_no"]) or "-",
        })

    quality = result.get("data_quality") or {}
    topic_count = int(quality.get("topic_count") or len(topics))
    difference_count = int(result.get("difference_count") or len(differences))
    coverage = quality.get("coverage_pct")
    coverage_text = f"{float(coverage):,.0f}%" if coverage is not None else "확인 필요"
    summary = (
        f"{subject}와 동종기업의 주석 {topic_count}개 주제를 비교한 결과, "
        f"기준회사와 문구가 다른 항목이 {difference_count}건 확인됐습니다. "
        "문구 차이만으로 회계처리가 다르다고 단정할 수는 없습니다."
    )

    next_actions = []
    if page["has_more"]:
        next_actions.append("다음 5개 비교회사의 주석을 보여줘.")
    if page["offset"]:
        next_actions.append("이전 5개 비교회사의 주석을 보여줘.")
    next_actions.extend([
        "문구 차이가 큰 주제만 자세히 보여줘.",
        "각 회사의 원문을 나란히 보여줘.",
    ])

    return view.model_copy(update={
        "title": f"{subject} 동종기업 주석 비교",
        "summary": summary,
        "metrics": [
            ChatbotMetricV1(label="비교 주제", value=topic_count, unit="개"),
            ChatbotMetricV1(label="비교 회사", value=page["total"], unit="개"),
            ChatbotMetricV1(label="자료 확인률", value=coverage_text),
            ChatbotMetricV1(label="문구가 다른 항목", value=difference_count, unit="건"),
        ],
        "tables": [ChatbotTableV1(
            id="note_comparison",
            title=f"비교회사 {page['start']}~{page['end']}",
            columns=[
                ChatbotColumnV1(key="company", label="회사"),
                ChatbotColumnV1(key="different_topics", label="문구가 다른 주제"),
                ChatbotColumnV1(key="available_topics", label="확인된 범위"),
                ChatbotColumnV1(key="basis", label="재무제표 기준"),
                ChatbotColumnV1(key="source", label="원 공시"),
            ],
            rows=rows,
            note="회사별 핵심 차이만 먼저 보여드립니다. 원 공시 링크에서 전체 문구를 확인할 수 있습니다.",
        )],
        "warnings": _humanize_warnings(list(view.warnings)),
        "next_actions": next_actions,
        "initially_visible_rows": COMPANY_PAGE_SIZE,
        "raw_text_default_collapsed": True,
    })


def polish_chatbot_view(
    tool_name: str,
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> ChatbotViewV1:
    """Replace implementation-centric labels with a direct business answer."""
    if tool_name == "select_peer_group":
        return _peer_group_view(view, result)
    if tool_name == "compare_to_industry_multi":
        return _benchmark_view(view, result)
    if (
        tool_name == "search_dataset"
        and (result.get("query") or {}).get("dataset")
        == "accounting_note_chapters"
    ):
        return _note_search_view(view, result)
    if tool_name == "compare_peer_accounting_notes":
        return _note_comparison_view(view, result)
    return view.model_copy(update={
        "warnings": _humanize_warnings(list(view.warnings)),
    })


def render_user_markdown(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> str:
    """Render only what the business user asked for, with sources inline."""
    lines = [
        f"## {_markdown_cell(view.title)}",
        "",
        f"**{_safe_text(view.summary, limit=2_000)}**",
    ]

    if view.status in {"limited", "missing", "error"}:
        notice = {
            "limited": "일부 자료가 부족해 결과를 제한적으로 해석해야 합니다.",
            "missing": "현재 확보된 자료만으로는 답을 확인하기 어렵습니다.",
            "error": "요청을 처리하는 과정에서 문제가 발생했습니다.",
        }[view.status]
        lines.extend(["", f"> {notice}"])

    if view.metrics:
        lines.extend(["", "### 핵심 결과", "| 항목 | 결과 |", "|---|---:|"])
        for metric in view.metrics[:6]:
            value = _markdown_cell(metric.value)
            if metric.unit:
                value = f"{value} {_markdown_cell(metric.unit)}"
            lines.append(f"| {_markdown_cell(metric.label)} | {value} |")

    for table in view.tables:
        lines.extend([
            "",
            f"### {_markdown_cell(table.title)}",
            "| " + " | ".join(_markdown_cell(column.label) for column in table.columns) + " |",
            "| " + " | ".join("---" for _ in table.columns) + " |",
        ])
        visible_rows = table.rows[: view.initially_visible_rows]
        for row in visible_rows:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(row.get(column.key, "-"))
                    for column in table.columns
                )
                + " |"
            )
        if not visible_rows:
            lines.append(
                "| "
                + " | ".join(
                    "현재 확인된 결과 없음" if index == 0 else "-"
                    for index, _column in enumerate(table.columns)
                )
                + " |"
            )
        if table.note:
            lines.extend(["", f"> {_safe_text(table.note, limit=500)}"])

    if view.citations:
        lines.extend(["", "### 근거 공시"])
        for citation in view.citations[:5]:
            lines.append(
                f"- [{_markdown_cell(citation.label)}]({citation.url})"
                f" — 접수번호 {citation.rcept_no}"
            )

    if view.warnings:
        lines.extend(["", "### 확인할 점"])
        lines.extend(
            f"- {_safe_text(warning, limit=500)}"
            for warning in _humanize_warnings(list(view.warnings))[:3]
        )

    if view.next_actions:
        lines.extend(["", "### 이어서 볼 수 있는 내용"])
        lines.extend(
            f"- {_safe_text(action, limit=300)}"
            for action in view.next_actions[:4]
        )

    return "\n".join(lines)[:_MAX_ANSWER_CHARS]


def build_user_visualization_pack(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Keep the structured UI pack synchronized with the plain-language view."""
    pack = build_chatbot_visualization_pack(view, result)
    pack.pop("resource_uri", None)
    pack["warnings"] = _humanize_warnings(list(pack.get("warnings") or []))
    pack["limitations"] = _humanize_warnings(list(pack.get("limitations") or []))
    quality = pack.get("data_quality")
    if isinstance(quality, dict):
        quality["limitations"] = _humanize_warnings(
            list(quality.get("limitations") or [])
        )
    rebuilt = build_visualization_pack(pack)
    return rebuilt.model_dump(mode="json")


def presentation_metadata(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Return internal UI hints; these are never rendered as user-facing prose."""
    row_count = max((len(table.rows) for table in view.tables), default=0)
    page = _pagination(result, row_count=row_count)
    return {
        "summary_first": True,
        "company_page_size": COMPANY_PAGE_SIZE,
        "initially_visible_rows": view.initially_visible_rows,
        "raw_text_default_collapsed": view.raw_text_default_collapsed,
        "source_links_inline": True,
        "pagination": page,
    }
