from collections.abc import Callable
from typing import Any

PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]

_SECTION_LABELS = {
    "peer_group": "Peer 그룹",
    "audit_effort": "감사 노력",
    "financial_risk": "재무 위험",
    "audit_history": "감사인 이력",
    "accounting_policy": "회계정책",
    "kam": "핵심감사사항",
    "audit_report_matters": "감사보고서 사항",
}
_METRIC_LABELS = {
    "receivables_to_revenue": "매출채권/매출",
    "inventory_to_revenue": "재고자산/매출",
    "op_cf_to_operating_profit": "영업현금흐름/영업이익",
    "accrual_ratio": "발생액 비율",
    "beneish_m_score": "Beneish M-score",
}
_PEER_REASON_LABELS = {
    "same_ksic_prefix": "동일 업종 분류",
    "asset_size_bucket": "자산규모 구간",
    "audit_fee_available": "감사보수 확인",
}
_COVERAGE_LABELS = {
    "selection_basis": "선정기준",
    "included_peers": "포함 Peer 수",
    "requested_years": "요청연도 수",
    "complete_years": "완전연도 수",
    "cited_years": "인용연도 수",
    "row_count": "입력행 수",
    "subject_metric_count": "대상 지표 수",
    "peer_metric_count": "Peer 지표 수",
    "current_year_rows": "당기 행 수",
    "prior_year_rows": "전기 행 수",
    "subject_policy_count": "대상 회계정책 수",
    "filing_source": "공시 출처",
    "current_filing_source": "당기 공시 출처",
    "semantic_complete": "의미 완결",
    "current_audit_report_source": "당기 감사보고서 출처",
    "classification_complete": "분류 완결",
}


def _public_coverage(coverage: dict[str, Any]) -> str:
    return ", ".join(
        f"{_COVERAGE_LABELS.get(key, '기타 coverage')}={value}"
        for key, value in coverage.items()
    ) or "-"


def _public_metric_rows(rows: object) -> list[dict[str, Any]]:
    public_rows = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        public_rows.append({
            **row,
            "metric": _METRIC_LABELS.get(
                str(row.get("metric") or ""),
                "기타 재무 위험지표",
            ),
        })
    return public_rows


def _public_peer_rows(
    rows: object,
    *,
    peer_count: object,
) -> list[dict[str, Any]]:
    public_rows = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        reasons = []
        for reason in row.get("include_reasons") or []:
            if not isinstance(reason, str):
                continue
            if reason.startswith("sector_group:"):
                reasons.append("동일 섹터")
            elif reason in _PEER_REASON_LABELS:
                reasons.append(_PEER_REASON_LABELS[reason])
        public_rows.append({
            "corp_name": row.get("corp_name"),
            "stock_code": row.get("stock_code"),
            "market": row.get("market"),
            "induty_code": row.get("induty_code"),
            "total_assets": row.get("total_assets"),
            "revenue": row.get("revenue"),
            "audit_fee_m": row.get("audit_fee_m"),
            "audit_hours": row.get("audit_hours"),
            "peer_n": peer_count,
            "selection_basis": ", ".join(dict.fromkeys(reasons))
            or "공개 Peer 선정 기준 충족",
        })
    return public_rows


def _acceptance_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    quality = (
        result.get("data_quality")
        if isinstance(result.get("data_quality"), dict)
        else {}
    )
    sections = (
        quality.get("section_statuses")
        if isinstance(quality.get("section_statuses"), dict)
        else {}
    )
    rows = []
    for section_key in _SECTION_LABELS:
        section = (
            sections.get(section_key)
            if isinstance(sections.get(section_key), dict)
            else {}
        )
        sources = (
            section.get("sources")
            if isinstance(section.get("sources"), list)
            else []
        )
        receipt = next(
            (
                source.get("rcept_no")
                for source in sources
                if isinstance(source, dict) and source.get("rcept_no")
            ),
            None,
        )
        coverage = (
            section.get("coverage")
            if isinstance(section.get("coverage"), dict)
            else {}
        )
        facts = "공시 근거 확인" if sources else "공시 근거 추가 확인 필요"
        rows.append({
            "review_area": _SECTION_LABELS[section_key],
            "status": section.get("status") or "limited",
            "confirmed_facts": facts,
            "coverage": _public_coverage(coverage),
            "rcept_no": receipt or "-",
            "next_check": (
                "공시 근거와 최소 coverage를 추가 확인하세요."
                if section.get("blockers")
                else "추가 확인 없음"
            ),
        })
    return rows


def _history_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _base_pack, _subject_label, _table

    pack = _base_pack(f"{_subject_label(result)} 감사인 이력", result)
    rows = [row for row in (result.get("history") or []) if isinstance(row, dict)]
    if rows:
        pack["tables"].append(_table(
            "audit_history",
            "감사인 이력",
            [
                ("year", "연도"), ("fs_div", "FS"), ("auditor_nm", "감사인"),
                ("audit_opinion", "감사의견"), ("auditor_changed", "변경 여부"),
                ("consecutive_years", "연속감사연수"), ("rcept_no", "접수번호"),
            ],
            rows,
        ))
    return pack


def _risk_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _base_pack, _subject_label, _table

    pack = _base_pack(f"{_subject_label(result)} 재무 위험 Peer 비교", result)
    rows = _public_metric_rows(result.get("metric_rows"))
    if rows:
        pack["tables"].append(_table(
            "peer_risk_metrics",
            "Peer 위험지표 분포",
            [
                ("metric", "지표"), ("peer_n", "Peer n"), ("p25", "P25"),
                ("p50", "P50"), ("p75", "P75"), ("subject_value", "대상 값"),
                ("limitation", "한계"),
            ],
            rows,
        ))
    return pack


def _acceptance_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _base_pack, _subject_label, _table

    pack = _base_pack(f"{_subject_label(result)} 감사 검토 근거", result)
    rows = _acceptance_rows(result)
    pack["tables"].append(_table(
        "audit_acceptance_evidence",
        "수임·유지 검토 영역",
        [
            ("review_area", "검토영역"), ("status", "상태"),
            ("confirmed_facts", "확인 사실"), ("coverage", "값/coverage"),
            ("rcept_no", "접수번호"), ("next_check", "필수 후속 확인"),
        ],
        rows,
    ))
    peer_group = (
        result.get("peer_group")
        if isinstance(result.get("peer_group"), dict)
        else {}
    )
    displayed_peers = (
        peer_group.get("selected_peers")
        if isinstance(peer_group.get("selected_peers"), list)
        else peer_group.get("sample_peers")
    )
    peer_rows = _public_peer_rows(
        displayed_peers,
        peer_count=len(displayed_peers) if isinstance(displayed_peers, list) else 0,
    )
    if peer_rows:
        pack["tables"].append(_table(
            "audit_acceptance_peer_group",
            "선정 Peer 그룹",
            [
                ("corp_name", "회사"),
                ("stock_code", "종목코드"),
                ("market", "시장"),
                ("induty_code", "업종코드"),
                ("total_assets", "총자산"),
                ("revenue", "매출"),
                ("audit_fee_m", "감사보수(백만원)"),
                ("audit_hours", "감사시간"),
                ("peer_n", "전체 Peer n"),
                ("selection_basis", "선정근거"),
            ],
            peer_rows,
        ))
    risk_summary = (
        result.get("risk_summary")
        if isinstance(result.get("risk_summary"), dict)
        else {}
    )
    metric_rows = _public_metric_rows(risk_summary.get("metric_rows"))
    if metric_rows:
        pack["tables"].append(_table(
            "audit_acceptance_risk_metrics",
            "재무 위험지표 Peer 분포",
            [
                ("metric", "지표"),
                ("peer_n", "Peer n"),
                ("p25", "P25"),
                ("p50", "P50"),
                ("p75", "P75"),
                ("subject_value", "대상 값"),
                ("limitation", "한계"),
            ],
            metric_rows,
        ))
    return pack


def _history_detail(result: dict[str, Any]) -> str:
    rows = [row for row in (result.get("history") or []) if isinstance(row, dict)]
    lines = ["감사인 이력:"]
    for row in rows[:10]:
        changed = "변경" if row.get("auditor_changed") else "유지"
        lines.append(
            f"- {row.get('year')}년 {row.get('fs_div')}: {row.get('auditor_nm')} / "
            f"의견 {row.get('audit_opinion')} / {changed} / 연속 {row.get('consecutive_years')}년 / 접수번호 {row.get('rcept_no') or '-'}"
        )
    return "\n".join(lines)


def _risk_detail(result: dict[str, Any]) -> str:
    rows = _public_metric_rows(result.get("metric_rows"))
    lines = ["Peer 위험지표:"]
    for row in rows[:8]:
        lines.append(
            f"- {row.get('metric')}: 대상 {row.get('subject_value')}, Peer n={row.get('peer_n')}, "
            f"P25/P50/P75={row.get('p25')}/{row.get('p50')}/{row.get('p75')}"
        )
    return "\n".join(lines)


def _acceptance_detail(result: dict[str, Any]) -> str:
    lines = [
        "검토 근거 매트릭스:",
        "",
        "| 검토영역 | 상태 | 확인 사실 | 값/coverage | 접수번호 | 필수 후속 확인 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in _acceptance_rows(result):
        lines.append(
            f"| {row['review_area']} | {row['status']} | "
            f"{row['confirmed_facts']} | {row['coverage']} | "
            f"{row['rcept_no']} | {row['next_check']} |"
        )
    for signal in (result.get("acceptance_signals") or [])[:5]:
        if isinstance(signal, dict) and signal.get("label"):
            lines.append(f"- 관찰사항: {signal['label']}")
    return "\n".join(lines)


PACK_BUILDERS: dict[str, PackBuilder] = {
    "get_audit_history": _history_pack,
    "compare_peer_risk_profile": _risk_pack,
    "build_audit_acceptance_pack": _acceptance_pack,
}
DETAIL_RENDERERS: dict[str, DetailRenderer] = {
    "get_audit_history": _history_detail,
    "compare_peer_risk_profile": _risk_detail,
    "build_audit_acceptance_pack": _acceptance_detail,
}
