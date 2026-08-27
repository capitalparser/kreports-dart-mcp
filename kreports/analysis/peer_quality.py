"""Statistically honest peer workflows with reproducible cohort identity."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

import kreports.db.engine as _engine_module
from kreports.analysis.peer import confidence_band
from kreports.analysis.peer_benchmarks import (
    _METRIC_SQL,
    _METRIC_UNIT,
    _fetch_metric_values,
    _quantile,
    select_peer_group,
)
from kreports.analysis.peer_criteria import PeerCriteriaProfile
from kreports.db.engine import get_session
from kreports.db.models import DatasetManifest


_DEFAULT_FINANCIAL_METRICS = [
    "영업이익률",
    "순이익률",
    "부채비율",
    "ROE",
    "ROA",
    "자기자본비율",
    "매출성장률",
    "Beneish_M",
]
_MAX_STATISTICAL_PEERS = 5_000
_MIN_STATISTICAL_N = 5


def _hash_payload(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _latest_dataset_identity() -> dict[str, Any]:
    try:
        with get_session() as session:
            row = (
                session.query(DatasetManifest)
                .order_by(
                    DatasetManifest.generated_at.desc(),
                    DatasetManifest.manifest_id.desc(),
                )
                .first()
            )
            if row is None:
                return {
                    "dataset_version": "unknown",
                    "schema_version": "unknown",
                }
            return {
                "manifest_id": row.manifest_id,
                "dataset_version": row.dataset_version,
                "schema_version": row.schema_version,
                "generated_at": (
                    row.generated_at.isoformat()
                    if row.generated_at is not None
                    else None
                ),
            }
    except Exception:
        # Older fixture/runtime databases may not contain dataset_manifest.
        return {
            "dataset_version": "unknown",
            "schema_version": "unknown",
        }


def resolve_statistical_peer_population(
    company: str,
    *,
    year: int | None = None,
    peer_criteria: (
        PeerCriteriaProfile | dict | list[str] | None
    ) = None,
    peer_limit: int = 50,
    fs_strategy: str = "auto",
    prefix_len_start: int = 3,
    size_bucket_decade: float | None = None,
    exclude_other_sectors: bool = True,
) -> dict[str, Any]:
    """Resolve a complete statistical cohort plus a bounded display page.

    ``peer_limit`` controls only the rows returned to a chatbot. Statistics use
    every eligible peer returned by the bounded Korean listed-company universe.
    """
    full_group = select_peer_group(
        company=company,
        criteria=peer_criteria,
        peer_limit=_MAX_STATISTICAL_PEERS,
        fs_strategy=fs_strategy,
        prefix_len_start=prefix_len_start,
        size_bucket_decade=size_bucket_decade,
        exclude_other_sectors=exclude_other_sectors,
        year=year,
    )
    if "error" in full_group:
        return full_group

    full_peers = [
        dict(peer)
        for peer in (full_group.get("peers") or [])
        if isinstance(peer, dict) and peer.get("corp_code")
    ]
    eligible_count = int(
        full_group.get("peer_count", len(full_peers))
        or 0
    )
    statistical_codes = [
        str(peer["corp_code"])
        for peer in full_peers
    ]
    statistical_universe_truncated = (
        eligible_count > len(statistical_codes)
    )

    display_group = deepcopy(full_group)
    display_peers = full_peers[:peer_limit]
    display_group["peers"] = display_peers
    display_group["returned_peer_count"] = len(display_peers)
    display_group["statistical_member_count"] = len(
        statistical_codes
    )
    display_group["presentation_truncated"] = (
        eligible_count > len(display_peers)
    )
    display_group["statistical_universe_truncated"] = (
        statistical_universe_truncated
    )

    policy = display_group.get("selection_policy") or {}
    subject = display_group.get("subject") or {}
    dataset = _latest_dataset_identity()
    criteria_applied = (
        policy.get("criteria_applied")
        or policy.get("criteria_requested")
        or peer_criteria
        or {}
    )
    criteria_hash = _hash_payload(criteria_applied)
    member_codes_hash = _hash_payload(statistical_codes)
    cohort_payload = {
        "subject_corp_code": subject.get("corp_code"),
        "requested_year": policy.get("requested_year"),
        "resolved_year": policy.get("resolved_year"),
        "fs_div": policy.get("fs_div_used"),
        "criteria_hash": criteria_hash,
        "member_codes_hash": member_codes_hash,
        "dataset_version": dataset["dataset_version"],
        "schema_version": dataset["schema_version"],
    }
    cohort_snapshot = {
        **cohort_payload,
        "cohort_id": f"sha256:{_hash_payload(cohort_payload)}",
        "eligible_count": eligible_count,
        "statistical_member_count": len(statistical_codes),
        "returned_member_count": len(display_peers),
        "presentation_truncated": (
            eligible_count > len(display_peers)
        ),
        "statistical_universe_truncated": (
            statistical_universe_truncated
        ),
        "criteria_applied": criteria_applied,
        "dataset_manifest_id": dataset.get("manifest_id"),
        "dataset_generated_at": dataset.get("generated_at"),
    }

    return {
        "peer_group": display_group,
        "statistical_peer_codes": statistical_codes,
        "eligible_count": eligible_count,
        "statistical_member_count": len(statistical_codes),
        "returned_member_count": len(display_peers),
        "statistical_universe_truncated": (
            statistical_universe_truncated
        ),
        "cohort_snapshot": cohort_snapshot,
    }


def _midrank_percentile(
    subject_value: float | None,
    values: list[float],
) -> tuple[float | None, int]:
    if subject_value is None or not values:
        return None, 0
    below = sum(
        1 for value in values
        if value < subject_value
    )
    ties = sum(
        1 for value in values
        if value == subject_value
    )
    percentile = round(
        100.0 * (below + 0.5 * ties) / len(values),
        1,
    )
    return percentile, ties


def compare_custom_peer_financials(
    company: str,
    *,
    year: int | None = None,
    metrics: list[str] | None = None,
    years_back: int = 5,
    peer_criteria: (
        PeerCriteriaProfile | dict | list[str] | None
    ) = None,
    peer_limit: int = 50,
    fs_strategy: str = "auto",
    prefix_len_start: int = 3,
    size_bucket_decade: float | None = None,
    exclude_other_sectors: bool = True,
) -> dict[str, Any]:
    """Compare metrics over the full resolved cohort, not the display page."""
    selected_metrics = list(
        metrics or _DEFAULT_FINANCIAL_METRICS
    )
    invalid = [
        metric
        for metric in selected_metrics
        if metric not in _METRIC_SQL
    ]
    if invalid:
        return {
            "error": f"지원하지 않는 metric: {invalid}",
            "allowed": sorted(_METRIC_SQL),
        }
    if not 1 <= years_back <= 10:
        return {
            "error": "years_back must be between 1 and 10"
        }

    population = resolve_statistical_peer_population(
        company,
        year=year,
        peer_criteria=peer_criteria,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        prefix_len_start=prefix_len_start,
        size_bucket_decade=size_bucket_decade,
        exclude_other_sectors=exclude_other_sectors,
    )
    if "error" in population:
        return population

    peer_group = population["peer_group"]
    subject = peer_group.get("subject") or {}
    policy = peer_group.get("selection_policy") or {}
    corp_code = subject.get("corp_code")
    fs_div = policy.get("fs_div_used") or "CFS"
    resolved_year = policy.get("resolved_year")
    if not corp_code or resolved_year is None:
        return {
            "subject": subject,
            "peer_group": peer_group,
            "cohort_snapshot": population["cohort_snapshot"],
            "years": [],
            "metrics": selected_metrics,
            "results": {},
            "data_quality": {
                "status": "missing",
                "limitations": [
                    "resolved_peer_year_unavailable"
                ],
            },
        }

    latest_year = int(resolved_year)
    years = list(
        range(
            latest_year - years_back + 1,
            latest_year + 1,
        )
    )
    peer_codes = population["statistical_peer_codes"]
    statistical_n = len(peer_codes)
    results: dict[int, dict[str, dict[str, Any]]] = {}
    total_cells = len(years) * len(selected_metrics)
    comparable_cells = 0
    sufficient_cells = 0

    with _engine_module.engine.connect() as conn:
        for current_year in years:
            year_result: dict[str, dict[str, Any]] = {}
            for metric in selected_metrics:
                peer_rows = _fetch_metric_values(
                    conn,
                    peer_codes,
                    _METRIC_SQL[metric],
                    current_year,
                    fs_div,
                )
                values = sorted(
                    float(row[3])
                    for row in peer_rows
                    if row[3] is not None
                )
                subject_rows = _fetch_metric_values(
                    conn,
                    [str(corp_code)],
                    _METRIC_SQL[metric],
                    current_year,
                    fs_div,
                )
                subject_value = (
                    float(subject_rows[0][3])
                    if (
                        subject_rows
                        and subject_rows[0][3] is not None
                    )
                    else None
                )
                n = len(values)
                midrank, ties = _midrank_percentile(
                    subject_value,
                    values,
                )
                if subject_value is not None and n:
                    comparable_cells += 1
                if (
                    subject_value is not None
                    and n >= _MIN_STATISTICAL_N
                ):
                    sufficient_cells += 1
                confidence = (
                    "subject_unavailable"
                    if subject_value is None
                    else "sufficient_n"
                    if n >= _MIN_STATISTICAL_N
                    else "insufficient_n"
                )
                year_result[metric] = {
                    "p25": (
                        round(_quantile(values, 0.25), 2)
                        if n >= _MIN_STATISTICAL_N
                        else None
                    ),
                    "p50": (
                        round(_quantile(values, 0.50), 2)
                        if n
                        else None
                    ),
                    "p75": (
                        round(_quantile(values, 0.75), 2)
                        if n >= _MIN_STATISTICAL_N
                        else None
                    ),
                    "n": n,
                    "statistical_population_n": statistical_n,
                    "unavailable_count": max(
                        0,
                        statistical_n - n,
                    ),
                    "coverage_pct": (
                        round(100.0 * n / statistical_n, 1)
                        if statistical_n
                        else 0.0
                    ),
                    "subject_value": (
                        round(subject_value, 2)
                        if subject_value is not None
                        else None
                    ),
                    # Official percentile is suppressed for n<5.
                    "percentile": (
                        midrank
                        if n >= _MIN_STATISTICAL_N
                        else None
                    ),
                    "midrank_percentile": midrank,
                    "tie_count": ties,
                    "percentile_method": "midrank",
                    "confidence": confidence,
                    "unit": _METRIC_UNIT.get(metric),
                }
            results[current_year] = year_result

    comparable_ratio = (
        comparable_cells / total_cells
        if total_cells
        else 0.0
    )
    sufficient_ratio = (
        sufficient_cells / total_cells
        if total_cells
        else 0.0
    )
    limitations: list[str] = []
    if population["statistical_universe_truncated"]:
        limitations.append(
            "statistical_universe_exceeded_internal_safety_bound"
        )
    if statistical_n < _MIN_STATISTICAL_N:
        limitations.append(
            "statistical_peer_count_below_5"
        )
    if sufficient_ratio < 0.8:
        limitations.append(
            "fewer_than_80_percent_of_metric_year_cells_have_n_at_least_5"
        )
    if comparable_ratio < 1.0:
        limitations.append(
            "some_subject_or_peer_metric_year_values_are_unavailable"
        )
    if population["returned_member_count"] < statistical_n:
        limitations.append(
            "chatbot_peer_table_is_truncated_but_statistics_use_full_cohort"
        )

    status = (
        "usable"
        if (
            sufficient_ratio >= 0.8
            and not population[
                "statistical_universe_truncated"
            ]
        )
        else "limited"
        if comparable_cells
        else "missing"
    )
    quality = {
        "status": status,
        "dataset_version": population[
            "cohort_snapshot"
        ]["dataset_version"],
        "schema_version": population[
            "cohort_snapshot"
        ]["schema_version"],
        "covered_years": years,
        "limitations": limitations,
        "total_metric_year_cells": total_cells,
        "comparable_metric_year_cells": comparable_cells,
        "sufficient_metric_year_cells": sufficient_cells,
        "comparable_cell_pct": round(
            100.0 * comparable_ratio,
            1,
        ),
        "sufficient_cell_pct": round(
            100.0 * sufficient_ratio,
            1,
        ),
        "statistical_member_count": statistical_n,
        "returned_member_count": population[
            "returned_member_count"
        ],
        "statistical_denominator_independent_of_peer_limit": True,
        "interpretation": (
            "모든 분위수와 백분위는 화면에 반환된 행이 아니라 "
            "동일 기준으로 확정된 전체 통계 cohort를 사용합니다."
        ),
    }

    return {
        "subject": subject,
        "year": year,
        "resolved_year": latest_year,
        "fs_div": fs_div,
        "fs_div_used": fs_div,
        "sector_group": (
            policy.get("sector_group")
            or peer_group.get("sector_group")
        ),
        "matched_prefix_len": policy.get(
            "matched_prefix_len"
        ),
        "confidence": confidence_band(statistical_n),
        "n_peers": statistical_n,
        "peer_count": statistical_n,
        "returned_peer_count": population[
            "returned_member_count"
        ],
        "presentation_truncated": (
            population["returned_member_count"]
            < statistical_n
        ),
        "peer_group": peer_group,
        "cohort_snapshot": population[
            "cohort_snapshot"
        ],
        "years": years,
        "metrics": selected_metrics,
        "results": results,
        "data_quality": quality,
        "next_checks": [
            "중요 판단 전 cohort_id와 dataset_version을 기록해 동일 모집단인지 확인하세요.",
            "n<5인 지표·연도는 공식 백분위를 제시하지 않으므로 원자료를 추가 확보하세요.",
        ],
    }
