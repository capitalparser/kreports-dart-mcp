from __future__ import annotations


def _row(
    corp_code: str,
    *,
    availability: str = "available",
    selection_status: str = "exact",
    text_hash: str = "hash-a",
) -> dict:
    return {
        "company": {
            "corp_code": corp_code,
            "corp_name": f"Company {corp_code}",
        },
        "availability": availability,
        "comparison_text": "note text",
        "comparison_text_length": 9,
        "comparison_text_hash": text_hash,
        "value_or_excerpt": "note text",
        "source_locator": f"accounting_note_chapters:{corp_code}",
        "source_document_id": 1,
        "rcept_no": f"20250318{int(corp_code):06d}",
        "note_no": "10",
        "note_title": "리스",
        "fs_div": (
            "CFS"
            if selection_status == "exact"
            else "OFS"
        ),
        "fs_div_selection": {
            "requested": "CFS",
            "used": (
                "CFS"
                if selection_status == "exact"
                else "OFS"
            ),
            "status": selection_status,
        },
    }


def test_strict_fs_policy_excludes_fallback_and_recomputes_quality():
    from kreports.analysis.note_quality import (
        annotate_note_comparison_quality,
    )

    raw = {
        "subject": {
            "corp_code": "00000001",
            "corp_name": "Subject",
        },
        "topics": [{
            "topic": "leases",
            "rows": [
                _row("00000001"),
                _row(
                    "00000002",
                    selection_status=(
                        "fallback_requested_fs_div_unavailable"
                    ),
                    text_hash="hash-b",
                ),
            ],
            "differences": [{
                "topic": "leases",
                "peer_corp_code": "00000002",
            }],
        }],
        "coverage_matrix": {
            "companies": [],
            "topics": [],
        },
        "differences": [{
            "topic": "leases",
            "peer_corp_code": "00000002",
        }],
    }

    result = annotate_note_comparison_quality(
        raw,
        fs_basis_policy="strict",
    )
    peer = result["topics"][0]["rows"][1]

    assert peer["availability"] == "unavailable"
    assert peer["fs_basis_excluded"] is True
    assert peer["excluded_evidence"]["rcept_no"]
    assert result["differences"] == []
    assert result["difference_count"] == 0
    assert result["data_quality"]["status"] == "limited"
    assert result["data_quality"]["coverage_pct"] == 50.0
    assert (
        "fallback_rows_excluded_by_strict_fs_basis"
        in result["data_quality"]["limitations"]
    )


def test_fallback_policy_preserves_row_and_warns():
    from kreports.analysis.note_quality import (
        annotate_note_comparison_quality,
    )

    raw = {
        "topics": [{
            "topic": "leases",
            "rows": [
                _row("00000001"),
                _row(
                    "00000002",
                    selection_status=(
                        "fallback_requested_fs_div_unavailable"
                    ),
                    text_hash="hash-b",
                ),
            ],
        }],
        "coverage_matrix": {
            "companies": [],
            "topics": [],
        },
    }
    result = annotate_note_comparison_quality(
        raw,
        fs_basis_policy="fallback_with_warning",
    )

    assert (
        result["topics"][0]["rows"][1]["availability"]
        == "available"
    )
    assert result["difference_count"] == 1
    assert any(
        item.startswith("fs_basis_fallback_rows:")
        for item in result["data_quality"]["limitations"]
    )
