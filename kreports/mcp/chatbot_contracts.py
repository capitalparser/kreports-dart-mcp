"""Chatbot-native presentation contracts for high-value MCP workflows."""
from __future__ import annotations

import html
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kreports.mcp.visual_contracts import build_visualization_pack


_MAX_TABLE_ROWS = 40
_MAX_ANSWER_CHARS = 20_000
_SUPPORTED_TOOLS = {
    "select_peer_group",
    "compare_to_industry_multi",
    "search_dataset",
    "compare_peer_accounting_notes",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatbotMetricV1(_StrictModel):
    label: str = Field(min_length=1, max_length=120)
    value: Any
    unit: str | None = Field(None, max_length=40)


class ChatbotColumnV1(_StrictModel):
    key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_]+$",
    )
    label: str = Field(min_length=1, max_length=120)
    unit: str | None = Field(None, max_length=40)


class ChatbotTableV1(_StrictModel):
    id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_]+$",
    )
    title: str = Field(min_length=1, max_length=200)
    columns: list[ChatbotColumnV1] = Field(
        min_length=1,
        max_length=20,
    )
    rows: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=_MAX_TABLE_ROWS,
    )
    note: str | None = Field(None, max_length=500)

    @field_validator("rows")
    @classmethod
    def rows_use_declared_columns(
        cls,
        rows: list[dict[str, Any]],
        info,
    ) -> list[dict[str, Any]]:
        columns = info.data.get("columns") or []
        declared = {column.key for column in columns}
        return [
            {
                key: value
                for key, value in row.items()
                if key in declared
            }
            for row in rows
            if isinstance(row, dict)
        ]


class ChatbotCitationV1(_StrictModel):
    label: str = Field(min_length=1, max_length=300)
    rcept_no: str = Field(
        min_length=14,
        max_length=14,
        pattern=r"^[0-9]{14}$",
    )
    url: str = Field(min_length=1, max_length=500)


class ChatbotViewV1(_StrictModel):
    version: str = "1.0"
    tool_name: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    subject: str = Field(min_length=1, max_length=200)
    status: str = Field(
        pattern=r"^(usable|limited|missing|error)$"
    )
    summary: str = Field(min_length=1, max_length=2_000)
    metrics: list[ChatbotMetricV1] = Field(
        default_factory=list,
        max_length=12,
    )
    tables: list[ChatbotTableV1] = Field(
        default_factory=list,
        max_length=8,
    )
    citations: list[ChatbotCitationV1] = Field(
        default_factory=list,
        max_length=32,
    )
    warnings: list[str] = Field(
        default_factory=list,
        max_length=24,
    )
    next_actions: list[str] = Field(
        default_factory=list,
        max_length=12,
    )
    raw_text_default_collapsed: bool = True
    initially_visible_rows: int = Field(8, ge=1, le=20)


def _status(result: dict[str, Any]) -> str:
    quality = result.get("data_quality")
    if isinstance(quality, dict):
        value = str(quality.get("status") or "")
        if value in {"usable", "limited", "missing", "error"}:
            return value
    return "error" if "error" in result else "limited"


def _subject(result: dict[str, Any]) -> str:
    subject = result.get("subject")
    if isinstance(subject, dict):
        return str(
            subject.get("corp_name")
            or subject.get("stock_code")
            or subject.get("corp_code")
            or "대상 회사"
        )
    query = result.get("query")
    if isinstance(query, dict):
        return str(
            query.get("company")
            or query.get("keyword")
            or query.get("market")
            or "대상 조건"
        )
    return "대상 조건"


def _safe_text(
    value: Any,
    *,
    limit: int = 1_200,
) -> str:
    text_value = " ".join(
        str(value if value is not None else "-").split()
    )
    return text_value[:limit]


def _markdown_cell(value: Any) -> str:
    text_value = html.escape(
        _safe_text(value, limit=1_000),
        quote=False,
    )
    return (
        text_value
        .replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", "<br/>")
    )


def _dart_url(rcept_no: str) -> str:
    return (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
        f"{rcept_no}"
    )


def _citation(
    rcept_no: Any,
    label: Any,
) -> ChatbotCitationV1 | None:
    receipt = str(rcept_no or "")
    if len(receipt) != 14 or not receipt.isdigit():
        return None
    return ChatbotCitationV1(
        label=_safe_text(label, limit=300),
        rcept_no=receipt,
        url=_dart_url(receipt),
    )


def _collect_citations(
    result: dict[str, Any],
) -> list[ChatbotCitationV1]:
    citations: list[ChatbotCitationV1] = []
    seen: set[str] = set()

    def add(receipt: Any, label: Any) -> None:
        item = _citation(receipt, label)
        if item is None or item.rcept_no in seen:
            return
        seen.add(item.rcept_no)
        citations.append(item)

    for fact in result.get("confirmed_facts") or []:
        if not isinstance(fact, dict):
            continue
        source = fact.get("source")
        if not isinstance(source, dict):
            continue
        add(
            source.get("rcept_no"),
            source.get("section_title")
            or fact.get("statement")
            or "DART 공시",
        )

    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        for record in company.get("records") or []:
            if isinstance(record, dict):
                add(
                    record.get("rcept_no"),
                    (
                        f"{company.get('corp_name') or company.get('corp_code')} · "
                        f"{record.get('note_title') or record.get('note_no') or '주석'}"
                    ),
                )

    for topic in result.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        for row in topic.get("rows") or []:
            if not isinstance(row, dict):
                continue
            company = row.get("company") or {}
            add(
                row.get("rcept_no"),
                (
                    f"{company.get('corp_name') or company.get('corp_code') or '회사'} · "
                    f"{row.get('note_title') or topic.get('topic') or '주석'}"
                ),
            )

    return citations[:32]


def _quality_warnings(
    result: dict[str, Any],
) -> list[str]:
    quality = result.get("data_quality")
    warnings: list[str] = []
    if isinstance(quality, dict):
        warnings.extend(
            str(item)
            for item in quality.get("limitations") or []
            if item
        )
        interpretation = quality.get("interpretation")
        if interpretation:
            warnings.append(str(interpretation))
    warnings.extend(
        str(item)
        for item in result.get("limitations") or []
        if item
    )
    warnings.extend(
        str(item)
        for item in result.get("warnings") or []
        if item
    )
    return list(dict.fromkeys(warnings))[:24]


def _next_actions(
    result: dict[str, Any],
    defaults: list[str],
) -> list[str]:
    values = [
        str(item)
        for item in result.get("next_checks") or []
        if item
    ]
    values.extend(defaults)
    return list(dict.fromkeys(values))[:12]


def _peer_group_view(
    tool_name: str,
    result: dict[str, Any],
) -> ChatbotViewV1:
    subject = _subject(result)
    policy = result.get("selection_policy") or {}
    snapshot = result.get("cohort_snapshot") or {}
    peer_count = int(
        result.get("statistical_member_count")
        or result.get("peer_count")
        or 0
    )
    returned = int(
        result.get("returned_peer_count")
        or len(result.get("peers") or [])
    )
    rows = []
    for peer in (result.get("peers") or [])[:_MAX_TABLE_ROWS]:
        if not isinstance(peer, dict):
            continue
        reasons = peer.get("include_reasons") or []
        rows.append({
            "company": peer.get("corp_name"),
            "stock_code": peer.get("stock_code"),
            "market": peer.get("market"),
            "ksic": peer.get("induty_code"),
            "total_assets": peer.get("total_assets"),
            "revenue": peer.get("revenue"),
            "selection_score": peer.get("selection_score"),
            "include_reasons": ", ".join(
                str(reason)
                for reason in reasons
            ),
        })
    summary = (
        f"{subject}의 동종업종 통계 모집단은 {peer_count}개이며, "
        f"챗봇 표에는 {returned}개를 표시합니다. "
        f"기준연도는 {policy.get('resolved_year') or '미확정'}, "
        f"재무제표 기준은 {policy.get('fs_div_used') or '미확정'}, "
        f"선정 신뢰도는 {result.get('confidence') or '미확정'}입니다."
    )
    cohort_id = snapshot.get("cohort_id")
    metrics = [
        ChatbotMetricV1(
            label="통계 모집단",
            value=peer_count,
            unit="개",
        ),
        ChatbotMetricV1(
            label="표시 기업",
            value=returned,
            unit="개",
        ),
        ChatbotMetricV1(
            label="선정 신뢰도",
            value=result.get("confidence") or "-",
        ),
        ChatbotMetricV1(
            label="기준연도",
            value=policy.get("resolved_year") or "-",
        ),
    ]
    if cohort_id:
        metrics.append(
            ChatbotMetricV1(
                label="Cohort ID",
                value=str(cohort_id)[-16:],
            )
        )
    return ChatbotViewV1(
        tool_name=tool_name,
        title=f"{subject} 동종업종 선정 결과",
        subject=subject,
        status=_status(result),
        summary=summary,
        metrics=metrics,
        tables=[ChatbotTableV1(
            id="peer_members",
            title="선정된 동종기업",
            columns=[
                ChatbotColumnV1(
                    key="company",
                    label="회사",
                ),
                ChatbotColumnV1(
                    key="stock_code",
                    label="종목코드",
                ),
                ChatbotColumnV1(
                    key="market",
                    label="시장",
                ),
                ChatbotColumnV1(
                    key="ksic",
                    label="KSIC",
                ),
                ChatbotColumnV1(
                    key="total_assets",
                    label="총자산",
                    unit="원",
                ),
                ChatbotColumnV1(
                    key="revenue",
                    label="매출",
                    unit="원",
                ),
                ChatbotColumnV1(
                    key="selection_score",
                    label="선정점수",
                ),
                ChatbotColumnV1(
                    key="include_reasons",
                    label="포함 사유",
                ),
            ],
            rows=rows,
            note=(
                "표시 행은 제한될 수 있으나 통계는 전체 확정 cohort를 사용합니다."
            ),
        )],
        citations=_collect_citations(result),
        warnings=_quality_warnings(result),
        next_actions=_next_actions(
            result,
            [
                "Cohort ID와 기준연도를 후속 분석 결과와 대조하세요.",
                "강제 포함 기업은 포함 사유와 경제적 유사성을 별도로 검토하세요.",
            ],
        ),
    )


def _peer_benchmark_view(
    tool_name: str,
    result: dict[str, Any],
) -> ChatbotViewV1:
    subject = _subject(result)
    quality = result.get("data_quality") or {}
    rows = []
    for year in sorted(
        (result.get("results") or {}).keys(),
        key=lambda value: int(value),
    ):
        metrics = (result.get("results") or {}).get(year) or {}
        for metric, values in metrics.items():
            if not isinstance(values, dict):
                continue
            rows.append({
                "year": int(year),
                "metric": metric,
                "subject_value": values.get("subject_value"),
                "unit": values.get("unit"),
                "percentile": values.get("percentile"),
                "midrank_percentile": values.get(
                    "midrank_percentile"
                ),
                "p25": values.get("p25"),
                "p50": values.get("p50"),
                "p75": values.get("p75"),
                "n": values.get("n"),
                "coverage_pct": values.get("coverage_pct"),
                "confidence": values.get("confidence"),
            })
    statistical_n = int(
        result.get("peer_count")
        or result.get("n_peers")
        or 0
    )
    returned = int(
        result.get("returned_peer_count") or 0
    )
    summary = (
        f"{subject}의 {len(result.get('years') or [])}개 연도 × "
        f"{len(result.get('metrics') or [])}개 지표를 "
        f"통계 peer {statistical_n}개로 비교했습니다. "
        f"공식 백분위는 지표·연도별 n이 5 이상일 때만 제시합니다."
    )
    return ChatbotViewV1(
        tool_name=tool_name,
        title=f"{subject} 동종업종 다년도 벤치마크",
        subject=subject,
        status=_status(result),
        summary=summary,
        metrics=[
            ChatbotMetricV1(
                label="통계 Peer",
                value=statistical_n,
                unit="개",
            ),
            ChatbotMetricV1(
                label="표시 Peer",
                value=returned,
                unit="개",
            ),
            ChatbotMetricV1(
                label="충분한 표본 Cell",
                value=quality.get(
                    "sufficient_cell_pct",
                    0,
                ),
                unit="%",
            ),
            ChatbotMetricV1(
                label="기준연도",
                value=result.get("resolved_year") or "-",
            ),
        ],
        tables=[ChatbotTableV1(
            id="peer_benchmark",
            title="연도별 지표 비교",
            columns=[
                ChatbotColumnV1(
                    key="year",
                    label="연도",
                ),
                ChatbotColumnV1(
                    key="metric",
                    label="지표",
                ),
                ChatbotColumnV1(
                    key="subject_value",
                    label="대상회사 값",
                ),
                ChatbotColumnV1(
                    key="unit",
                    label="단위",
                ),
                ChatbotColumnV1(
                    key="percentile",
                    label="공식 백분위",
                    unit="%",
                ),
                ChatbotColumnV1(
                    key="midrank_percentile",
                    label="Mid-rank",
                    unit="%",
                ),
                ChatbotColumnV1(
                    key="p25",
                    label="P25",
                ),
                ChatbotColumnV1(
                    key="p50",
                    label="P50",
                ),
                ChatbotColumnV1(
                    key="p75",
                    label="P75",
                ),
                ChatbotColumnV1(
                    key="n",
                    label="표본",
                    unit="개",
                ),
                ChatbotColumnV1(
                    key="coverage_pct",
                    label="Coverage",
                    unit="%",
                ),
                ChatbotColumnV1(
                    key="confidence",
                    label="신뢰도",
                ),
            ],
            rows=rows[:_MAX_TABLE_ROWS],
            note=(
                "공식 백분위가 비어 있으면 n<5 또는 대상회사 값 부재입니다."
            ),
        )],
        citations=_collect_citations(result),
        warnings=_quality_warnings(result),
        next_actions=_next_actions(
            result,
            [
                "백분위가 비어 있는 Cell은 n과 coverage를 먼저 확인하세요.",
                "통계 결론이 아니라 상대 위치 screening으로 사용하세요.",
            ],
        ),
    )


def _note_search_view(
    tool_name: str,
    result: dict[str, Any],
) -> ChatbotViewV1:
    query = result.get("query") or {}
    keyword = str(query.get("keyword") or "주석 검색")
    rows = []
    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        for record in company.get("records") or []:
            if not isinstance(record, dict):
                continue
            rows.append({
                "company": (
                    company.get("corp_name")
                    or company.get("corp_code")
                ),
                "year": record.get("year"),
                "fs_div": record.get("fs_div"),
                "note_title": (
                    record.get("note_title")
                    or record.get("note_no")
                ),
                "matched_term": record.get("matched_term"),
                "match_type": record.get("match_type"),
                "excerpt": record.get("body_excerpt"),
                "rcept_no": record.get("rcept_no"),
            })
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
    returned = int(
        result.get("returned_company_count")
        or len(result.get("companies") or [])
    )
    summary = (
        f"'{keyword}' 관련 주석이 현재 캐시에서 회사 "
        f"{total_companies}개, 레코드 {total_records}건 확인됐습니다. "
        f"현재 페이지에는 회사 {returned}개를 표시합니다. "
        f"검색 방식은 {query.get('search_mode') or 'exact'}입니다."
    )
    return ChatbotViewV1(
        tool_name=tool_name,
        title=f"'{keyword}' 주석 공시회사 검색",
        subject=keyword,
        status=_status(result),
        summary=summary,
        metrics=[
            ChatbotMetricV1(
                label="일치 회사",
                value=total_companies,
                unit="개",
            ),
            ChatbotMetricV1(
                label="일치 레코드",
                value=total_records,
                unit="건",
            ),
            ChatbotMetricV1(
                label="현재 페이지",
                value=returned,
                unit="개사",
            ),
            ChatbotMetricV1(
                label="검색 방식",
                value=query.get("search_mode") or "exact",
            ),
        ],
        tables=[ChatbotTableV1(
            id="note_search_results",
            title="공시회사와 일치 근거",
            columns=[
                ChatbotColumnV1(
                    key="company",
                    label="회사",
                ),
                ChatbotColumnV1(
                    key="year",
                    label="연도",
                ),
                ChatbotColumnV1(
                    key="fs_div",
                    label="재무제표",
                ),
                ChatbotColumnV1(
                    key="note_title",
                    label="주석",
                ),
                ChatbotColumnV1(
                    key="matched_term",
                    label="일치어",
                ),
                ChatbotColumnV1(
                    key="match_type",
                    label="검색유형",
                ),
                ChatbotColumnV1(
                    key="excerpt",
                    label="근거 문구",
                ),
                ChatbotColumnV1(
                    key="rcept_no",
                    label="접수번호",
                ),
            ],
            rows=rows[:_MAX_TABLE_ROWS],
            note=(
                "근거 문구는 검색어 주변의 제한된 excerpt이며 원 공시 전체 문맥이 아닙니다."
            ),
        )],
        citations=_collect_citations(result),
        warnings=_quality_warnings(result),
        next_actions=_next_actions(
            result,
            [
                "접수번호 링크에서 해당 주석의 전체 문맥을 확인하세요.",
                "동의어 검색은 matched_term과 원문 excerpt를 함께 검토하세요.",
            ],
        ),
    )


def _note_comparison_view(
    tool_name: str,
    result: dict[str, Any],
) -> ChatbotViewV1:
    subject = _subject(result)
    quality = result.get("data_quality") or {}
    rows = []
    for topic in result.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        for row in topic.get("rows") or []:
            if not isinstance(row, dict):
                continue
            company = row.get("company") or {}
            selection = row.get("fs_div_selection") or {}
            rows.append({
                "topic": topic.get("topic"),
                "company": (
                    company.get("corp_name")
                    or company.get("corp_code")
                ),
                "availability": row.get("availability"),
                "fs_div": row.get("fs_div"),
                "fs_basis_status": (
                    selection.get("status")
                    if isinstance(selection, dict)
                    else None
                ),
                "note_title": row.get("note_title"),
                "excerpt": (
                    row.get("comparison_text")
                    or row.get("value_or_excerpt")
                ),
                "rcept_no": row.get("rcept_no"),
            })
    topic_count = int(
        quality.get("topic_count")
        or len(result.get("topics") or [])
    )
    company_count = len(
        (result.get("coverage_matrix") or {}).get(
            "companies"
        )
        or []
    )
    difference_count = int(
        result.get("difference_count")
        or len(result.get("differences") or [])
    )
    coverage = quality.get("coverage_pct", 0)
    summary = (
        f"{subject}와 peer의 주석 {topic_count}개 주제를 "
        f"회사 {company_count}개 기준으로 비교했습니다. "
        f"가용 coverage는 {coverage}%, 정규화 원문 차이는 "
        f"{difference_count}건입니다. 차이는 회계처리 판단이 아닙니다."
    )
    return ChatbotViewV1(
        tool_name=tool_name,
        title=f"{subject} 동종기업 주석 비교",
        subject=subject,
        status=_status(result),
        summary=summary,
        metrics=[
            ChatbotMetricV1(
                label="비교 주제",
                value=topic_count,
                unit="개",
            ),
            ChatbotMetricV1(
                label="비교 회사",
                value=company_count,
                unit="개",
            ),
            ChatbotMetricV1(
                label="Coverage",
                value=coverage,
                unit="%",
            ),
            ChatbotMetricV1(
                label="원문 차이",
                value=difference_count,
                unit="건",
            ),
            ChatbotMetricV1(
                label="FS 정책",
                value=result.get(
                    "fs_basis_policy",
                    "fallback_with_warning",
                ),
            ),
        ],
        tables=[ChatbotTableV1(
            id="note_comparison",
            title="주제별 회사 주석",
            columns=[
                ChatbotColumnV1(
                    key="topic",
                    label="주제",
                ),
                ChatbotColumnV1(
                    key="company",
                    label="회사",
                ),
                ChatbotColumnV1(
                    key="availability",
                    label="가용상태",
                ),
                ChatbotColumnV1(
                    key="fs_div",
                    label="재무제표",
                ),
                ChatbotColumnV1(
                    key="fs_basis_status",
                    label="FS 선택",
                ),
                ChatbotColumnV1(
                    key="note_title",
                    label="주석 제목",
                ),
                ChatbotColumnV1(
                    key="excerpt",
                    label="비교 문구",
                ),
                ChatbotColumnV1(
                    key="rcept_no",
                    label="접수번호",
                ),
            ],
            rows=rows[:_MAX_TABLE_ROWS],
            note=(
                "사내 챗봇 UI에서는 비교 문구를 기본 접힘 상태로 표시하고 "
                "회사·주제·가용상태를 먼저 보여주는 것이 권장됩니다."
            ),
        )],
        citations=_collect_citations(result),
        warnings=_quality_warnings(result),
        next_actions=_next_actions(
            result,
            [
                "different_normalized_text 항목은 원 공시 문구를 나란히 검토하세요.",
                "summary_only 또는 unavailable 행은 전체 원문 가용성을 추가 확인하세요.",
            ],
        ),
        raw_text_default_collapsed=True,
    )


def build_chatbot_view(
    tool_name: str,
    result: dict[str, Any],
) -> ChatbotViewV1 | None:
    """Build a bounded summary-first view for an internal chatbot."""
    if (
        tool_name not in _SUPPORTED_TOOLS
        or not isinstance(result, dict)
    ):
        return None
    if tool_name == "select_peer_group":
        return _peer_group_view(tool_name, result)
    if tool_name == "compare_to_industry_multi":
        return _peer_benchmark_view(
            tool_name,
            result,
        )
    if (
        tool_name == "search_dataset"
        and (
            (result.get("query") or {}).get("dataset")
            == "accounting_note_chapters"
        )
    ):
        return _note_search_view(tool_name, result)
    if tool_name == "compare_peer_accounting_notes":
        return _note_comparison_view(
            tool_name,
            result,
        )
    return None


def render_chatbot_markdown(
    view: ChatbotViewV1,
) -> str:
    """Render summary-first Markdown safe for a corporate chat surface."""
    status_labels = {
        "usable": "사용 가능",
        "limited": "제한적",
        "missing": "데이터 미확보",
        "error": "오류",
    }
    lines = [
        f"## {_markdown_cell(view.title)}",
        "",
        f"**데이터 상태:** {status_labels[view.status]}",
        "",
        "### 한눈에 보기",
        _safe_text(view.summary, limit=2_000),
    ]

    if view.metrics:
        lines.extend([
            "",
            "### 핵심 지표",
            "| 항목 | 값 |",
            "|---|---:|",
        ])
        for metric in view.metrics:
            value = _markdown_cell(metric.value)
            if metric.unit:
                value = f"{value} {_markdown_cell(metric.unit)}"
            lines.append(
                f"| {_markdown_cell(metric.label)} | {value} |"
            )

    for table in view.tables:
        lines.extend([
            "",
            f"### {_markdown_cell(table.title)}",
            "| "
            + " | ".join(
                _markdown_cell(column.label)
                for column in table.columns
            )
            + " |",
            "| "
            + " | ".join(
                "---"
                for _ in table.columns
            )
            + " |",
        ])
        for row in table.rows[:view.initially_visible_rows]:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(
                        row.get(column.key, "-")
                    )
                    for column in table.columns
                )
                + " |"
            )
        if not table.rows:
            lines.append(
                "| "
                + " | ".join(
                    "데이터 미확보"
                    if index == 0
                    else "-"
                    for index, _column in enumerate(
                        table.columns
                    )
                )
                + " |"
            )
        if len(table.rows) > view.initially_visible_rows:
            lines.append(
                f"\n표시 생략: {len(table.rows) - view.initially_visible_rows}행은 "
                "구조화 결과의 answer_pack에서 확인할 수 있습니다."
            )
        if table.note:
            lines.append(f"\n> {_safe_text(table.note, limit=500)}")

    if view.citations:
        lines.extend(["", "### 근거 공시"])
        for citation in view.citations[:8]:
            lines.append(
                f"- [{_markdown_cell(citation.label)}]"
                f"({citation.url}) — 접수번호 {citation.rcept_no}"
            )

    if view.warnings:
        lines.extend(["", "### 데이터 한계"])
        lines.extend(
            f"- {_safe_text(warning, limit=500)}"
            for warning in view.warnings[:8]
        )

    if view.next_actions:
        lines.extend(["", "### 다음 확인"])
        lines.extend(
            f"- {_safe_text(action, limit=500)}"
            for action in view.next_actions[:6]
        )

    if view.raw_text_default_collapsed:
        lines.extend([
            "",
            "> 긴 주석 원문과 반복 근거는 사내 챗봇 UI에서 기본 접힘 상태로 "
            "표시하고, 접수번호·회사·주제·데이터 상태를 먼저 노출하는 것이 권장됩니다.",
        ])

    return "\n".join(lines)[:_MAX_ANSWER_CHARS]


def build_chatbot_visualization_pack(
    view: ChatbotViewV1,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Translate the view into the existing validated visualization contract."""
    tables = [
        {
            "id": table.id,
            "title": table.title,
            "columns": [
                {
                    "field": column.key,
                    "label": column.label,
                    **(
                        {"unit": column.unit}
                        if column.unit
                        else {}
                    ),
                }
                for column in table.columns
            ],
            "rows": table.rows,
            **(
                {"note": table.note}
                if table.note
                else {}
            ),
        }
        for table in view.tables
    ]
    charts: list[dict[str, Any]] = []
    if (
        view.tool_name == "compare_to_industry_multi"
        and tables
        and any(
            isinstance(row.get("percentile"), (int, float))
            for row in tables[0]["rows"]
        )
    ):
        charts.append({
            "id": "peer_percentile_heatmap",
            "type": "heatmap",
            "title": "연도·지표별 대상회사 백분위",
            "data_ref": "peer_benchmark",
            "encodings": {
                "x": {"field": "year"},
                "y": {"field": "metric"},
                "color": {"field": "percentile"},
            },
            "note": (
                "백분위는 mid-rank 방식이며 n<5인 Cell은 표시하지 않습니다."
            ),
        })

    raw_pack = {
        "kind": "answer_pack",
        "summary": {
            "title": view.title,
            "status": view.status,
            "subject": view.subject,
            "domain_status": view.status,
        },
        "tables": tables,
        "charts": charts,
        "diagrams": [],
        "timelines": [],
        "sources": [
            {
                "label": citation.label,
                "rcept_no": citation.rcept_no,
            }
            for citation in view.citations
        ],
        "data_quality": {
            "status": view.status,
            "source": (
                (result.get("data_quality") or {}).get(
                    "source"
                )
                or "local_kreports_db"
            ),
            "dataset_version": (
                (result.get("data_quality") or {}).get(
                    "dataset_version"
                )
            ),
            "schema_version": (
                (result.get("data_quality") or {}).get(
                    "schema_version"
                )
            ),
            "limitations": view.warnings,
            "interpretation": view.summary,
        },
        "status": view.status,
        "limitations": view.warnings,
        "warnings": [
            *view.warnings,
            (
                "chatbot_display:summary_first;"
                f"initial_rows={view.initially_visible_rows};"
                f"raw_text_collapsed={str(view.raw_text_default_collapsed).lower()}"
            ),
        ],
    }
    pack = build_visualization_pack(raw_pack)
    return pack.model_dump(mode="json")
