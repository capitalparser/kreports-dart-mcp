from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import Session

from kreports.db.models import (
    Company,
    CompanyYearQuality,
    EvidenceDocument,
    Financial,
    ReportSection,
    SourceDocument,
)
from kreports.mcp.resources import (
    ResourceRequestError,
    list_resource_templates,
    list_resources,
    read_resource,
)


def test_required_resources_and_templates_are_enumerated():
    assert {resource.uri for resource in list_resources()} == {
        "kreports://dataset/readiness"
    }
    assert {resource.uri_template for resource in list_resource_templates()} == {
        "kreports://company/{corp_code}/{year}",
        "kreports://evidence/{rcept_no}",
    }


def test_stdio_and_http_share_registered_resource_and_prompt_handlers():
    from mcp.types import (
        GetPromptRequest,
        ListPromptsRequest,
        ListResourcesRequest,
        ListResourceTemplatesRequest,
        ReadResourceRequest,
    )

    from kreports.mcp.http_server import server as http_server
    from kreports.mcp.server import server as stdio_server

    assert http_server is stdio_server
    assert {
        ListResourcesRequest,
        ListResourceTemplatesRequest,
        ReadResourceRequest,
        ListPromptsRequest,
        GetPromptRequest,
    }.issubset(stdio_server.request_handlers)


@pytest.mark.parametrize(
    "uri",
    [
        "http://company/00126380/2025",
        "kreports://company/../../etc/passwd",
        "kreports://company/0012638/2025",
        "kreports://company/00126380/1999",
        "kreports://evidence/2025010100000",
        "kreports://evidence/20250101000000?path=/tmp/private",
        "kreports://unknown/value",
    ],
)
def test_resource_uri_parser_rejects_malformed_or_unknown_uris(uri):
    with pytest.raises(ResourceRequestError) as caught:
        read_resource(uri)
    assert len(str(caught.value)) <= 200
    assert "/tmp/private" not in str(caught.value)


def test_dataset_readiness_does_not_claim_release_without_manifest(monkeypatch):
    from kreports.mcp import resources

    monkeypatch.setattr(
        resources,
        "evaluate_release_gate",
        lambda _profile: {
            "ok": False,
            "profile": "public_runtime",
            "schema_version": "unknown",
            "dataset_version": "unknown",
            "required_failures": ["release_manifest_unavailable"],
            "degraded_features": ["accounting_policy"],
            "coverage_year": 2025,
            "coverage": {
                "investor_core": {
                    "numerator": 90,
                    "denominator": 100,
                    "coverage_pct": 90.0,
                    "threshold_pct": 95.0,
                }
            },
            "denominators": {"investor_core": 100},
            "excluded_populations": {"investor_core": {"not_listed": 3}},
            "tool_count": 31,
        },
    )

    payload = read_resource("kreports://dataset/readiness")

    assert payload["release_ready"] is False
    assert payload["manifest_available"] is False
    assert payload["schema_version"] == "unknown"
    assert payload["required_failures"] == ["release_manifest_unavailable"]
    assert payload["denominators"] == {"investor_core": 100}
    assert payload["excluded_populations"]["investor_core"]["not_listed"] == 3


def test_company_resource_distinguishes_missing_cache_from_filing_absence(temp_engine):
    with Session(temp_engine) as session:
        session.add(Company(corp_code="00126380", corp_name="표본회사"))
        session.commit()

    payload = read_resource("kreports://company/00126380/2025")

    assert payload["company"]["corp_code"] == "00126380"
    assert payload["cache_status"] == "missing"
    assert payload["filing_status"] == "not_determined"
    assert payload["data_quality"]["status"] == "missing"
    assert payload["errors"] == []


def test_company_resource_returns_manifest_quality_facts_and_dart_links(
    temp_engine,
):
    with Session(temp_engine) as session:
        session.add(Company(corp_code="00126380", corp_name="표본회사"))
        session.add(
            Financial(
                corp_code="00126380",
                year=2025,
                quarter=4,
                fs_div="CFS",
                revenue=100,
                operating_profit=10,
                net_income=8,
                total_assets=200,
                total_debt=80,
                total_equity=120,
            )
        )
        session.add(
            CompanyYearQuality(
                corp_code="00126380",
                bsns_year=2025,
                market="KOSPI",
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status="summary_only",
                kam_status="available",
                audit_procedure_status="available",
                group_audit_status="missing",
                investor_grade="A",
                auditor_grade="B",
                group_audit_grade="D",
                blockers_json='["group_audit"]',
                quality_version="v1",
            )
        )
        session.add(
            EvidenceDocument(
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                rcept_no="20250312000001",
                normalized_text="evidence",
            )
        )
        session.commit()

    payload = read_resource("kreports://company/00126380/2025")

    assert payload["cache_status"] == "available"
    assert payload["quality"]["investor_grade"] == "A"
    assert payload["structured_facts"][0]["revenue"] == 100
    assert payload["evidence"][0] == {
        "rcept_no": "20250312000001",
        "source_url": (
            "https://dart.fss.or.kr/dsaf001/main.do?"
            "rcpNo=20250312000001"
        ),
    }
    assert "id" not in payload
    assert "id" not in payload["company"]
    assert all("id" not in fact for fact in payload["structured_facts"])


def test_evidence_resource_prefers_raw_external_and_bounds_text(
    temp_engine, monkeypatch
):
    from kreports.mcp import resources

    class FakeRawStore:
        def read(self, uri, *, expected_hash=None):
            assert uri == "file:///private/raw.xml.gz"
            return "R" * 25_000

    monkeypatch.setattr(resources, "RawDocumentStore", FakeRawStore)
    with Session(temp_engine) as session:
        session.add(
            SourceDocument(
                rcept_no="20250312000001",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content="",
                doc_hash="hash",
                storage_uri="file:///private/raw.xml.gz",
                storage_status="externalized",
            )
        )
        session.add(
            EvidenceDocument(
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                rcept_no="20250312000001",
                normalized_text="normalized fallback",
            )
        )
        session.commit()

    payload = read_resource("kreports://evidence/20250312000001")

    assert payload["source_basis"] == "raw_external"
    assert len(payload["text"]) == 20_000
    assert payload["truncated"] is True
    assert "storage_uri" not in payload
    assert "/private/" not in json.dumps(payload)


def test_evidence_resource_falls_back_to_normalized_then_derived(temp_engine):
    receipt = "20250312000002"
    with Session(temp_engine) as session:
        session.add(
            EvidenceDocument(
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                rcept_no=receipt,
                normalized_text="normalized evidence",
            )
        )
        session.add(
            ReportSection(
                rcept_no=receipt,
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                body_text="derived section",
            )
        )
        session.commit()

    normalized = read_resource(f"kreports://evidence/{receipt}")
    assert normalized["source_basis"] == "normalized_evidence"
    assert normalized["text"] == "normalized evidence"

    with Session(temp_engine) as session:
        session.query(EvidenceDocument).delete()
        session.commit()

    derived = read_resource(f"kreports://evidence/{receipt}")
    assert derived["source_basis"] == "derived_summary"
    assert derived["text"] == "derived section"


def test_evidence_storage_failure_is_error_not_missing(temp_engine, monkeypatch):
    from kreports.mcp import resources

    class BrokenRawStore:
        def read(self, _uri, *, expected_hash=None):
            raise OSError("sensitive/internal/path")

    monkeypatch.setattr(resources, "RawDocumentStore", BrokenRawStore)
    with Session(temp_engine) as session:
        session.add(
            SourceDocument(
                rcept_no="20250312000003",
                corp_code="00126380",
                bsns_year=2025,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content="",
                doc_hash="hash",
                storage_uri="file:///private/raw.xml.gz",
                storage_status="externalized",
            )
        )
        session.commit()

    payload = read_resource("kreports://evidence/20250312000003")

    assert payload["data_quality"]["status"] == "error"
    assert payload["source_basis"] is None
    assert "sensitive" not in json.dumps(payload)
