from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError


def _local_context() -> dict:
    return {
        "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
        "year": 2024,
        "availability": {
            "business_report": "available",
            "audit_report": "unavailable",
            "notes": "unavailable",
            "evidence_documents": "unavailable",
            "disclosures": "unavailable",
            "financials": "unavailable",
        },
        "business_report": [
            {
                "source_locator": "report_sections:2",
                "section_title": "사업의 내용",
                "section_key": "business_overview",
                "excerpt": "두 번째 DART 근거",
                "full_text_hash": "b" * 40,
                "rcept_no": "20250302000001",
                "source_document_id": 2,
                "source_type": "business_report",
                "provenance_status": "proven_annual_filing",
                "canonical_source_binding": True,
            },
            {
                "source_locator": "report_sections:1",
                "section_title": "위험관리",
                "section_key": "risks",
                "excerpt": "첫 번째 DART 근거",
                "full_text_hash": "a" * 40,
                "rcept_no": "20250301000001",
                "source_document_id": 1,
                "source_type": "business_report",
                "provenance_status": "proven_annual_filing",
                "canonical_source_binding": True,
            },
        ],
        "audit_report": [],
        "notes": [],
        "evidence_documents": [],
        "disclosures": [],
        "financials": [],
    }


def _seed_local_context_sources():
    from kreports.db.engine import get_session
    from kreports.db.models import Disclosure, SourceDocument

    with get_session() as session:
        session.add_all([
            SourceDocument(
                rcept_no="20250301000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서 (2024.12)",
                raw_content="<xml/>", doc_hash="a" * 40,
            ),
            SourceDocument(
                rcept_no="20250302000001", corp_code="00000001", bsns_year=2024,
                source_type="business_report", report_nm="사업보고서 (2024.12)",
                raw_content="<xml/>", doc_hash="b" * 40,
            ),
            Disclosure(
                rcept_no="20250301000001", corp_code="00000001", corp_name="Context Corp",
                disc_date=date(2025, 3, 1), disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
            Disclosure(
                rcept_no="20250302000001", corp_code="00000001", corp_name="Context Corp",
                disc_date=date(2025, 3, 2), disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
        ])


def test_context_pack_separates_sources_orders_deterministically_and_dedupes(temp_engine):
    from kreports.analysis.context_pack import build_context_pack

    _seed_local_context_sources()

    pack = build_context_pack(
        _local_context(),
        peer_note_comparison={"subject": {"corp_code": "00000001"}, "topics": []},
        company_ir=[
            {
                "source_class": "company_ir",
                "source_id": "ir-b",
                "title": "IR deck",
                "excerpt": "매출 성장은 10% 목표",
                "url": "https://example.com/ir/deck",
                "claim_key": "revenue_growth",
            },
            {
                "source_class": "company_ir",
                "source_id": "ir-a",
                "title": "IR deck copy",
                "excerpt": "매출 성장은 10% 목표",
                "url": "https://example.com/ir/deck",
                "claim_key": "revenue_growth",
            },
        ],
        web_news=[
            {
                "source_class": "web_news",
                "source_id": "news-2",
                "title": "News",
                "excerpt": "외부 보도",
                "checksum": "news-hash",
            },
            {
                "source_class": "web_news",
                "source_id": "news-1",
                "title": "News duplicate",
                "excerpt": "외부 보도",
                "checksum": "news-hash",
            },
        ],
    )

    assert pack.schema_version == "context_pack.v1"
    assert pack.source_precedence == ["dart_filing", "company_ir", "web_news", "llm_analysis"]
    assert [item.source_id for item in pack.dart_filing] == ["report_sections:1", "report_sections:2"]
    assert [item.source_id for item in pack.company_ir] == ["ir-a"]
    assert [item.source_id for item in pack.web_news] == ["news-1"]
    assert pack.peer_note_comparison.source_class == "dart_filing"
    assert pack.llm_analysis == []
    assert {item.evidence_type for item in pack.missing_evidence} >= {"audit_report", "notes"}
    assert any(item.claim_key == "revenue_growth" for item in pack.conflicts) is False


def test_context_pack_rejects_unlabelled_or_wrongly_classed_external_claims():
    from kreports.analysis.context_pack import build_context_pack

    with pytest.raises(ValidationError):
        build_context_pack(
            _local_context(),
            company_ir=[{"source_id": "ir-1", "excerpt": "unlabelled claim"}],
        )


def test_context_pack_keeps_unbound_cached_excerpt_out_of_dart_filing_sources():
    from kreports.analysis.context_pack import build_context_pack

    context = _local_context()
    context["business_report"].append({
        "source_locator": "report_sections:unbound",
        "section_key": "risks",
        "excerpt": "캐시에는 있으나 연간 사업보고서 원천 결합이 입증되지 않은 텍스트",
        "rcept_no": "20250303000001",
        "availability": "summary_only",
    })

    pack = build_context_pack(context)

    assert "report_sections:unbound" not in [item.source_id for item in pack.dart_filing]


def test_context_pack_does_not_trust_caller_supplied_canonical_binding_flags():
    from kreports.analysis.context_pack import build_context_pack

    pack = build_context_pack(_local_context())

    assert pack.dart_filing == []
    with pytest.raises(ValueError, match="company_ir"):
        build_context_pack(
            _local_context(),
            company_ir=[
                {
                    "source_class": "web_news",
                    "source_id": "wrong-bucket",
                    "excerpt": "wrong bucket",
                }
            ],
        )


@pytest.mark.parametrize("missing_field", ["source_document_id", "source_type"])
def test_context_pack_requires_explicit_source_identity(temp_engine, missing_field):
    from kreports.analysis.context_pack import build_context_pack

    _seed_local_context_sources()
    context = _local_context()
    context["business_report"][0].pop(missing_field)

    pack = build_context_pack(context)

    assert "report_sections:2" not in [item.source_id for item in pack.dart_filing]


def test_context_pack_bounds_caller_supplied_external_evidence():
    from kreports.analysis.context_pack import build_context_pack

    with pytest.raises(ValueError, match="maximum 50"):
        build_context_pack(
            _local_context(),
            web_news=[
                {
                    "source_class": "web_news",
                    "source_id": f"news-{index}",
                    "excerpt": "bounded external context",
                }
                for index in range(51)
            ],
        )


def test_mcp_context_pack_applies_a_documented_total_output_budget(temp_engine):
    import json

    from kreports.analysis.context_pack import (
        MAX_MCP_CONTEXT_PACK_BYTES,
        build_mcp_context_pack,
    )

    _seed_local_context_sources()

    result = build_mcp_context_pack({
        "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
        "year": 2024,
        "business_report": [
            {
                "source_locator": f"report_sections:{index}",
                "section_key": "risks",
                "excerpt": "x" * 4_000,
                "full_text_hash": f"{index:040d}",
                "rcept_no": "20250301000001",
                "source_document_id": 1,
                "source_type": "business_report",
                "provenance_status": "proven_annual_filing",
                "canonical_source_binding": True,
            }
            for index in range(80)
        ],
    })

    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= MAX_MCP_CONTEXT_PACK_BYTES
    assert result["truncation"] == {
        "applied": True,
        "max_output_bytes": MAX_MCP_CONTEXT_PACK_BYTES,
        "reason": "context_pack_output_budget",
    }
    assert result["dart_filing"]


def test_mcp_context_pack_emergency_budget_retains_compact_provenance(temp_engine):
    import json

    from kreports.analysis.context_pack import (
        MAX_MCP_CONTEXT_PACK_BYTES,
        build_mcp_context_pack,
    )

    _seed_local_context_sources()

    huge_peer_payload = [[["x" * 4_000 for _ in range(20)] for _ in range(20)] for _ in range(20)]
    result = build_mcp_context_pack(
        {
            "subject": {"corp_code": "00000001", "corp_name": "Context Corp"},
            "year": 2024,
            "business_report": [
                {
                    "source_locator": "report_sections:1",
                    "section_key": "risks",
                    "excerpt": "DART excerpt",
                    "full_text_hash": "a" * 40,
                    "availability": "summary_only",
                    "rcept_no": "20250301000001",
                    "source_document_id": 1,
                    "source_type": "business_report",
                    "provenance_status": "proven_annual_filing",
                    "canonical_source_binding": True,
                    "fs_div_selection": {
                        "requested": "OFS",
                        "used": "CFS",
                        "status": "fallback_requested_fs_div_unavailable",
                        "huge_nested_metadata": huge_peer_payload,
                    },
                }
            ],
        },
        peer_note_comparison={"nested": huge_peer_payload},
        company_ir=[
            {"source_class": "company_ir", "source_id": "ir-1", "excerpt": "IR"}
        ],
        web_news=[
            {"source_class": "web_news", "source_id": "news-1", "excerpt": "News"}
        ],
    )

    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) <= MAX_MCP_CONTEXT_PACK_BYTES
    assert result["truncation"]["applied"] is True
    dart = result["dart_filing"][0]
    assert dart["source_class"] == "dart_filing"
    assert dart["source_id"] == "report_sections:1"
    assert dart["metadata"]["availability"] == "summary_only"
    assert dart["metadata"]["rcept_no"] == "20250301000001"
    assert dart["metadata"]["fs_div_selection"] == {
        "requested": "OFS",
        "used": "CFS",
        "status": "fallback_requested_fs_div_unavailable",
    }
    assert dart["metadata"]["source_locator"] == "report_sections:1"
    assert [item["source_id"] for item in result["company_ir"]] == ["ir-1"]
    assert [item["source_id"] for item in result["web_news"]] == ["news-1"]


def test_context_pack_rejects_cross_bucket_duplicate_source_ids_before_llm_citation():
    from kreports.analysis.context_pack import build_context_pack

    with pytest.raises(ValueError, match="duplicate source_id"):
        build_context_pack(
            _local_context(),
            company_ir=[
                {
                    "source_class": "company_ir",
                    "source_id": "shared-source",
                    "excerpt": "IR self-description",
                }
            ],
            web_news=[
                {
                    "source_class": "web_news",
                    "source_id": "shared-source",
                    "excerpt": "News coverage",
                }
            ],
            llm_analysis=[
                {
                    "statement": "This citation would otherwise be ambiguous.",
                    "source_ids": ["shared-source"],
                }
            ],
        )
