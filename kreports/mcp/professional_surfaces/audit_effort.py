from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

PackBuilder = Callable[[dict[str, Any]], dict[str, Any] | None]
DetailRenderer = Callable[[dict[str, Any]], str]


def _is_numeric_measure(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)).is_finite()
    except (InvalidOperation, ValueError):
        return False


def _public_sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep top-level and per-year audit-effort receipts in the public pack."""
    from kreports.mcp.answer_pack import _collect_sources

    sources = _collect_sources(result)
    seen = {str(source.get("rcept_no") or source.get("url")) for source in sources}
    for row in result.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for field in ("financial_source", "audit_source"):
            source = row.get(field)
            if not isinstance(source, dict):
                continue
            for candidate in _collect_sources({
                "confirmed_facts": [{"source": source}],
            }):
                key = str(candidate.get("rcept_no") or candidate.get("url"))
                if key not in seen:
                    seen.add(key)
                    sources.append(candidate)
    for reference in result.get("methodology_references") or []:
        if not isinstance(reference, dict):
            continue
        url = reference.get("official_url")
        if not url:
            continue
        candidate = {
            "label": f"{reference.get('issuer') or 'KReports'}: {reference.get('document_title') or reference.get('reference_id')}",
            "url": url,
        }
        key = str(candidate["url"])
        if key not in seen:
            seen.add(key)
            sources.append(candidate)
    return sources


def _base_pack(title: str, result: dict[str, Any]) -> dict[str, Any]:
    quality = result.get("data_quality") or {}
    subject = result.get("subject") or {}
    return {
        "kind": "answer_pack",
        "version": "answer_pack.v1",
        "summary": {
            "title": title,
            "status": quality.get("status") or "limited",
            "subject": subject.get("corp_name") or subject.get("corp_code") or "대상 회사",
        },
        "tables": [],
        "charts": [],
        "diagrams": [],
        "timelines": [],
        "sources": _public_sources(result),
        "data_quality": quality,
    }


def _subject_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "year": row.get("year"),
        "fs": row.get("fs_div"),
        "total_assets_100m": row.get("total_assets_100m"),
        "revenue_100m": row.get("revenue_100m"),
        "audit_fee_m": row.get("audit_fee_m"),
        "audit_hours": row.get("audit_hours"),
        "hours_basis": row.get("hours_basis"),
        "input_status": row.get("input_status"),
        "missing_fields": ", ".join(row.get("missing_fields") or []) or "-",
    } for row in result.get("rows") or [] if isinstance(row, dict)]


def _build_prepare_inputs_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack("표준감사시간 입력 준비", result)
    rows = _subject_rows(result)
    if rows:
        pack["tables"].append({
            "id": "standard_audit_hours_inputs",
            "title": "최근 3개년 공개자료 입력",
            "columns": [
                {"field": "year", "label": "연도"},
                {"field": "fs", "label": "FS"},
                {"field": "total_assets_100m", "label": "총자산(억원)"},
                {"field": "revenue_100m", "label": "매출(억원)"},
                {"field": "audit_fee_m", "label": "감사보수(백만원)"},
                {"field": "audit_hours", "label": "감사시간"},
                {"field": "hours_basis", "label": "기준"},
                {"field": "input_status", "label": "입력상태"},
                {"field": "missing_fields", "label": "미확보 항목"},
            ],
            "rows": rows,
            "status": (result.get("data_quality") or {}).get("status", "limited"),
        })
    return pack


def _build_fee_comparison_pack(result: dict[str, Any]) -> dict[str, Any]:
    from kreports.mcp.answer_pack import _append_subject_scale_history

    pack = _base_pack("감사보수·감사시간 비교", result)
    _append_subject_scale_history(pack, result)
    rows = []
    subject = result.get("subject_metrics")
    if isinstance(subject, dict):
        rows.append({"role": "subject", **subject})
    for peer in result.get("peers") or []:
        if isinstance(peer, dict):
            rows.append({"role": "peer", **peer})
    if rows:
        pack["tables"].append({
            "id": "peer_audit_fee_benchmark",
            "title": "대상회사 후 peer 감사보수 비교",
            "columns": [
                {"field": "role", "label": "구분"},
                {"field": "corp_name", "label": "회사"},
                {
                    "field": "audit_fee_m",
                    "label": "감사보수(백만원)",
                    "unit": "백만원",
                },
                {"field": "audit_hours", "label": "감사시간", "unit": "시간"},
                {
                    "field": "non_audit_fee_m",
                    "label": "비감사보수(백만원)",
                    "unit": "백만원",
                },
                {"field": "nas_ratio", "label": "비감사보수 비율", "unit": "ratio"},
            ],
            "rows": [
                {
                    key: row.get(key)
                    for key in (
                        "role",
                        "corp_name",
                        "audit_fee_m",
                        "audit_hours",
                        "non_audit_fee_m",
                        "nas_ratio",
                    )
                }
                for row in rows
            ],
            "status": (result.get("data_quality") or {}).get("status", "limited"),
        })
        if any(_is_numeric_measure(row.get("audit_fee_m")) for row in rows):
            pack["charts"].append({
                "id": "audit_fee_peer_chart",
                "type": "bar",
                "title": "감사보수 Peer 분포",
                "data_ref": "peer_audit_fee_benchmark",
                "encodings": {
                    "x": {"field": "corp_name"},
                    "y": {"field": "audit_fee_m"},
                    "series": {"field": "role"},
                },
            })
    return pack


def _build_hours_proxy_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack("감사시간 proxy", result)
    metrics = result.get("subject_metrics")
    if isinstance(metrics, dict):
        pack["tables"].append({
            "id": "audit_hours_proxy_inputs",
            "title": "감사시간 공개자료 proxy",
            "columns": [
                {"field": "audit_fee_m", "label": "감사보수(백만원)"},
                {"field": "audit_hours", "label": "감사시간"},
                {"field": "total_assets", "label": "총자산"},
                {
                    "field": "audit_source_rcept_no",
                    "label": "감사보수·시간 접수번호",
                },
                {
                    "field": "financial_source_rcept_no",
                    "label": "재무제표 접수번호",
                },
            ],
            "rows": [{
                key: metrics.get(key)
                for key in (
                    "audit_fee_m",
                    "audit_hours",
                    "total_assets",
                    "audit_source_rcept_no",
                    "financial_source_rcept_no",
                )
            }],
            "status": (result.get("data_quality") or {}).get("status", "limited"),
        })
    return pack


def _build_materiality_pack(result: dict[str, Any]) -> dict[str, Any]:
    pack = _base_pack("감사 중요성 기준 후보 준비", result)
    series_rows = []
    for benchmark, observations in (result.get("benchmark_series") or {}).items():
        if not isinstance(observations, list):
            continue
        for observation in observations:
            if not isinstance(observation, dict):
                continue
            sources = observation.get("sources") or []
            source_receipts = ", ".join(
                str(source.get("rcept_no"))
                for source in sources if isinstance(source, dict) and source.get("rcept_no")
            ) or "-"
            source_roles = ", ".join(
                str(source.get("operand_metric"))
                for source in sources if isinstance(source, dict) and source.get("operand_metric")
            ) or "-"
            rejected_rows = [
                {
                    field: rejected.get(field)
                    for field in (
                        "metric_key", "bsns_year", "fs_div",
                        "citation_rcept_no", "citation_basis",
                        "source_account_id", "source_table",
                    )
                }
                for rejected in observation.get("rejected_rows") or []
                if isinstance(rejected, dict)
            ]
            series_rows.append({
                "year": observation.get("year"), "benchmark": benchmark,
                "amount": observation.get("amount"), "unit": observation.get("unit"),
                "basis": observation.get("basis"),
                "limitations": ", ".join(observation.get("limitations") or []) or "-",
                "source_receipt": source_receipts, "source_roles": source_roles,
                "rejected_rows": rejected_rows,
            })
    pack["tables"].append({
        "id": "materiality_benchmark_series", "title": "연도별 중요성 기준 관찰값",
        "columns": [{"field": key, "label": label} for key, label in (
            ("year", "연도"), ("benchmark", "기준"), ("amount", "금액"),
            ("unit", "단위"), ("basis", "산출 근거"), ("limitations", "한계"),
            ("source_receipt", "출처 접수번호"), ("source_roles", "파생 피연산자"),
            ("rejected_rows", "제외된 행 출처 진단"),
        )], "rows": series_rows,
        "status": (result.get("data_quality") or {}).get("status", "limited"),
    })
    stability = result.get("benchmark_stability") or {}
    stability_rows = []
    for key, item in stability.items():
        if not isinstance(item, dict):
            continue
        stability_rows.append({
            "benchmark": key,
            "usable_year_count": item.get("usable_year_count"),
            "requested_year_count": item.get("requested_year_count"),
            "mean": item.get("mean"), "median": item.get("median"),
            "sample_standard_deviation": item.get("sample_standard_deviation"),
            "coefficient_of_variation": item.get("coefficient_of_variation"),
            "maximum_absolute_year_over_year_change": item.get("maximum_absolute_year_over_year_change"),
            "maximum_relative_year_over_year_change": item.get("maximum_relative_year_over_year_change"),
            "anomaly_flags": ", ".join(item.get("anomaly_flags") or []) or "-",
            "role": item.get("role"), "stability": item.get("stability"),
            "volatility_classification": item.get("volatility_classification"),
        })
    pack["tables"].append({
        "id": "materiality_benchmark_stability", "title": "중요성 기준별 변동성 관찰",
        "columns": [{"field": key, "label": label} for key, label in (
            ("benchmark", "기준"), ("usable_year_count", "사용 가능 연도"),
            ("requested_year_count", "요청 연도"), ("mean", "평균"), ("median", "중앙값"),
            ("sample_standard_deviation", "표본 표준편차"), ("coefficient_of_variation", "변동계수"),
            ("maximum_absolute_year_over_year_change", "최대 전년대비 변동"),
            ("maximum_relative_year_over_year_change", "최대 상대 전년대비 변동"),
            ("volatility_classification", "변동성 분류"), ("anomaly_flags", "이상 징후"),
            ("role", "활용 역할"), ("stability", "관찰 상태"),
        )], "rows": stability_rows, "status": (result.get("data_quality") or {}).get("status", "limited"),
    })
    candidate_rows = result.get("materiality_candidates") or []
    pack["tables"].append({
        "id": "materiality_candidates", "title": "중요성 후보 범위 (감사인 미선택)",
        "columns": [{"field": key, "label": label} for key, label in (
            ("benchmark_label_ko", "기준"), ("selected_source_amount", "기준금액"),
            ("selected_year_basis", "연도"), ("lower_rate", "하한 비율"),
            ("central_rate", "중심 비율"), ("upper_rate", "상한 비율"),
            ("lower_candidate_amount", "하한 후보금액"), ("central_candidate_amount", "중심 후보금액"),
            ("upper_candidate_amount", "상한 후보금액"), ("suitability_role", "활용 역할"),
            ("rate_reference_ids", "비율별 방법론 근거"), ("conclusion_status", "결론 상태"),
        )], "rows": candidate_rows, "status": (result.get("data_quality") or {}).get("status", "limited"),
        "note": (
            "비교 가능한 단위·접수번호 출처가 없거나 변동성 역할이 제외되어 후보 금액을 표시하지 않았습니다."
            if not candidate_rows else None
        ),
    })
    references = [
        {
            **reference,
            "source_location": (
                str(reference["official_url"])
                .removeprefix("https://")
                .removeprefix("http://")
                if reference.get("official_url")
                else reference.get("source_locator") or "-"
            ),
        }
        for reference in result.get("methodology_references") or []
        if isinstance(reference, dict)
    ]
    pack["tables"].append({
        "id": "materiality_methodology_references", "title": "방법론 및 기준 근거",
        "columns": [{"field": key, "label": label} for key, label in (
            ("reference_id", "근거 ID"), ("authority_level", "근거 구분"),
            ("issuer", "발행자"), ("source_location", "출처 위치"),
            ("standard_code", "기준 코드"), ("paragraphs", "문단"),
            ("document_title", "문서"), ("application_note_ko", "적용 설명"),
        )], "rows": references, "status": "usable",
    })
    return pack


def _render_prepare_inputs(result: dict[str, Any]) -> str:
    lines = ["최근 3개년 공개자료 입력:"]
    for row in _subject_rows(result):
        lines.append(
            f"- {row['year']} | {row['fs']} | 자산 {row['total_assets_100m'] or '미확보'}억원 "
            f"| 매출 {row['revenue_100m'] or '미확보'}억원 | 보수 {row['audit_fee_m'] or '미확보'}백만원 "
            f"| 시간 {row['audit_hours'] or '미확보'} | {row['input_status']}"
        )
    return "\n".join(lines)


def _render_fee_comparison(result: dict[str, Any]) -> str:
    """Explain local comparison rows without promoting uncited values to facts."""
    year = result.get("year") or "요청"
    peer_count = result.get("peer_count") or 0
    quality = result.get("data_quality") or {}
    provenance = quality.get("source_provenance") or {}
    integrity = quality.get("unit_integrity") or {}
    citable = provenance.get("citable_row_count") or 0
    uncitable = provenance.get("uncitable_value_row_count") or 0
    excluded = integrity.get("excluded_row_count") or 0
    lines = [
        "감사보수·감사시간 Peer 비교:",
        f"- {year}년 대상회사와 peer {peer_count}개사의 로컬 캐시 관찰값을 비교 표에 정리했습니다.",
    ]
    if citable:
        lines.append(f"- 원 공시 접수번호가 직접 연결된 비교 행: {citable}건입니다.")
    if uncitable:
        lines.append(
            f"- 접수번호가 연결되지 않은 감사보수·시간 관찰값: {uncitable}건입니다. "
            "이 값들은 표의 로컬 캐시 관찰값일 뿐, 공시 확인 사실이나 출처로 승격하지 않았습니다."
        )
    if excluded:
        lines.append(
            f"- 단위·비율 이상 징후 {excluded}건은 단위를 추정 변환하지 않고 비교·표시에서 제외했습니다."
        )
    return "\n".join(lines)


def _render_materiality_inputs(result: dict[str, Any]) -> str:
    subject = (result.get("subject") or {}).get("corp_name") or "대상 회사"
    candidates = result.get("materiality_candidates") or []
    first = (
        f"중요성 기준 후보 준비: {subject}의 공시 재무계열 변동성과 후보 금액을 정리했습니다."
        if candidates else
        f"중요성 기준 후보 준비: {subject}의 변동성 관찰만 정리했으며 후보 금액을 표시하지 않았습니다."
    )
    availability = (
        f"- 표시 후보: {len(candidates)}건 (모든 후보는 방법론 근거를 포함)"
        if candidates else
        "- 후보 금액 보류: 비교 가능한 단위·접수번호 출처 또는 안정성 조건이 충족되지 않았습니다."
    )
    return "\n".join([
        first,
        "감사 중요성 기준·비율은 아직 선택하거나 승인하지 않았습니다.",
        availability,
        "- ISA 320 예시는 고정 의무 비율이 아니며, 변동성 관찰값은 KReports 내부 방법론으로 투명하게 표시합니다.",
    ])


PACK_BUILDERS: dict[str, PackBuilder] = {
    "prepare_standard_audit_hours_inputs": _build_prepare_inputs_pack,
    "compare_peer_audit_fees": _build_fee_comparison_pack,
    "estimate_audit_hours_proxy": _build_hours_proxy_pack,
    "prepare_audit_materiality_inputs": _build_materiality_pack,
}
DETAIL_RENDERERS: dict[str, DetailRenderer] = {
    "prepare_standard_audit_hours_inputs": _render_prepare_inputs,
    "compare_peer_audit_fees": _render_fee_comparison,
    "prepare_audit_materiality_inputs": _render_materiality_inputs,
}
CONCLUSION_OVERRIDES = {
    "prepare_standard_audit_hours_inputs": "표준감사시간 결론: 산정하지 않음",
}
