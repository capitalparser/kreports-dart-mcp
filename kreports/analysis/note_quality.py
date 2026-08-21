"""Quality annotation for peer accounting-note comparisons."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal


FsBasisPolicy = Literal[
    "fallback_with_warning",
    "strict",
]


def _strictly_exclude_fallback_row(
    row: dict[str, Any],
) -> None:
    selection = row.get("fs_div_selection")
    status = (
        str(selection.get("status") or "")
        if isinstance(selection, dict)
        else ""
    )
    if not status.startswith("fallback_"):
        return
    row["excluded_evidence"] = {
        "availability": row.get("availability"),
        "source_locator": row.get("source_locator"),
        "rcept_no": row.get("rcept_no"),
        "note_no": row.get("note_no"),
        "note_title": row.get("note_title"),
        "fs_div": row.get("fs_div"),
    }
    row.update({
        "availability": "unavailable",
        "source_locator": None,
        "source_document_id": None,
        "rcept_no": None,
        "comparison_text": None,
        "comparison_text_hash": None,
        "value_or_excerpt": None,
        "comparison_note": "excluded_by_strict_fs_basis",
        "fs_basis_excluded": True,
    })


def _topic_differences(
    topic: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return []
    subject = rows[0]
    subject_hash = subject.get("comparison_text_hash")
    subject_length = subject.get("comparison_text_length")
    differences: list[dict[str, Any]] = []
    for peer in rows[1:]:
        peer_hash = peer.get("comparison_text_hash")
        if subject_hash is None or peer_hash is None:
            continue
        if (
            subject_hash,
            subject_length,
        ) == (
            peer_hash,
            peer.get("comparison_text_length"),
        ):
            continue
        differences.append({
            "topic": topic,
            "subject_corp_code": (
                subject.get("company") or {}
            ).get("corp_code"),
            "peer_corp_code": (
                peer.get("company") or {}
            ).get("corp_code"),
            "status": "different_normalized_text",
            "subject_source_locator": subject.get(
                "source_locator"
            ),
            "peer_source_locator": peer.get(
                "source_locator"
            ),
        })
    return differences


def annotate_note_comparison_quality(
    result: dict[str, Any],
    *,
    fs_basis_policy: FsBasisPolicy = (
        "fallback_with_warning"
    ),
) -> dict[str, Any]:
    """Apply FS-basis policy and recompute coverage/differences."""
    if not isinstance(result, dict) or "error" in result:
        return result
    if fs_basis_policy not in {
        "fallback_with_warning",
        "strict",
    }:
        return {
            "error": "invalid fs_basis_policy",
            "allowed": [
                "fallback_with_warning",
                "strict",
            ],
        }

    enriched = deepcopy(result)
    topics = [
        topic
        for topic in (enriched.get("topics") or [])
        if isinstance(topic, dict)
    ]
    if not topics:
        enriched["fs_basis_policy"] = fs_basis_policy
        enriched["data_quality"] = {
            "status": "missing",
            "limitations": [
                "note_comparison_topics_unavailable"
            ],
        }
        return enriched

    fallback_count = 0
    all_differences: list[dict[str, Any]] = []
    coverage_topics: list[dict[str, Any]] = []
    total_cells = 0
    available_cells = 0
    subject_available_topics = 0

    for topic_result in topics:
        rows = [
            row
            for row in (topic_result.get("rows") or [])
            if isinstance(row, dict)
        ]
        for row in rows:
            selection = row.get("fs_div_selection")
            selection_status = (
                str(selection.get("status") or "")
                if isinstance(selection, dict)
                else ""
            )
            if selection_status.startswith("fallback_"):
                fallback_count += 1
                if fs_basis_policy == "strict":
                    _strictly_exclude_fallback_row(row)

        topic = str(topic_result.get("topic") or "unknown")
        differences = _topic_differences(topic, rows)
        topic_result["differences"] = differences
        all_differences.extend(differences)

        counts = {
            status: sum(
                row.get("availability") == status
                for row in rows
            )
            for status in (
                "available",
                "summary_only",
                "unavailable",
            )
        }
        topic_result["coverage"] = (
            counts["available"]
            + counts["summary_only"]
        )
        topic_result["coverage_by_status"] = counts
        total_cells += len(rows)
        available_cells += topic_result["coverage"]
        if (
            rows
            and rows[0].get("availability")
            != "unavailable"
        ):
            subject_available_topics += 1
        coverage_topics.append({
            "topic": topic,
            "coverage": counts,
            "cells": [
                {
                    "corp_code": (
                        row.get("company") or {}
                    ).get("corp_code"),
                    "availability": row.get("availability"),
                    "source_locator": row.get(
                        "source_locator"
                    ),
                    "rcept_no": row.get("rcept_no"),
                    "full_text_hash": row.get(
                        "full_text_hash"
                    ),
                    "fs_div_selection": row.get(
                        "fs_div_selection"
                    ),
                    "fs_basis_excluded": row.get(
                        "fs_basis_excluded",
                        False,
                    ),
                }
                for row in rows
            ],
        })

    enriched["differences"] = all_differences
    coverage_matrix = dict(
        enriched.get("coverage_matrix") or {}
    )
    coverage_matrix["topics"] = coverage_topics
    enriched["coverage_matrix"] = coverage_matrix
    enriched["fs_basis_policy"] = fs_basis_policy
    enriched["difference_count"] = len(
        all_differences
    )

    coverage_ratio = (
        available_cells / total_cells
        if total_cells
        else 0.0
    )
    subject_ratio = (
        subject_available_topics / len(topics)
        if topics
        else 0.0
    )
    limitations = list(
        (enriched.get("data_quality") or {}).get(
            "limitations"
        )
        or []
    )
    if fallback_count:
        limitations.append(
            f"fs_basis_fallback_rows:{fallback_count}"
        )
    if (
        fs_basis_policy == "strict"
        and fallback_count
    ):
        limitations.append(
            "fallback_rows_excluded_by_strict_fs_basis"
        )
    if coverage_ratio < 0.8:
        limitations.append(
            "note_cell_coverage_below_80_percent"
        )
    if subject_ratio < 1.0:
        limitations.append(
            "subject_note_topic_coverage_incomplete"
        )

    status = (
        "usable"
        if coverage_ratio >= 0.8 and subject_ratio == 1.0
        else "limited"
        if available_cells
        else "missing"
    )
    existing_quality = (
        enriched.get("data_quality")
        if isinstance(
            enriched.get("data_quality"),
            dict,
        )
        else {}
    )
    enriched["data_quality"] = {
        **existing_quality,
        "status": status,
        "limitations": list(dict.fromkeys(
            limitations
        )),
        "topic_count": len(topics),
        "total_cells": total_cells,
        "available_cells": available_cells,
        "coverage_pct": round(
            100.0 * coverage_ratio,
            1,
        ),
        "subject_topic_coverage_pct": round(
            100.0 * subject_ratio,
            1,
        ),
        "fs_basis_policy": fs_basis_policy,
        "interpretation": (
            "차이는 정규화 원문 hash 비교이며 회계처리 판단을 의미하지 않습니다. "
            "strict 모드에서는 cohort 재무제표 기준과 다른 주석을 비교에서 제외합니다."
        ),
    }
    enriched["next_checks"] = list(dict.fromkeys([
        *(enriched.get("next_checks") or []),
        "차이로 표시된 항목은 주석 제목·접수번호·원문 문맥을 함께 검토하세요.",
        "summary_only 행은 full_text_uri 또는 원 공시에서 전체 문구를 확인하세요.",
    ]))
    return enriched
