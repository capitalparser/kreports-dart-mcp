import json
from datetime import date, datetime

import pytest

from kreports.analysis.api import (
    build_audit_acceptance_pack,
    compare_peer_accounting_policies,
    compare_peer_audit_fees,
    compare_peer_audit_procedures,
    compare_peer_audit_report_matters,
    compare_peer_kam_topics,
    compare_peer_risk_profile,
    estimate_audit_hours_proxy,
    get_audit_report_sections,
    search_dataset,
    search_audit_procedures,
    search_audit_report_matters,
)
from kreports.db.models import (
    AccountingNoteChapter,
    AuditFee,
    AuditProcedureItem,
    Company,
    Disclosure,
    EvidenceDocument,
    Financial,
    KamItem,
    SourceDocument,
)
from kreports.db.engine import get_session
from kreports.mcp.tools import call_tool


def test_prepare_standard_audit_hours_inputs_mcp_dispatch(temp_engine):
    """The public handler preserves the non-calculation audit-effort contract."""
    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"))

    out = json.loads(call_tool("prepare_standard_audit_hours_inputs", {"company": "005930"}))

    assert out["_meta"]["tool"] == "prepare_standard_audit_hours_inputs"
    assert out["standard_audit_hours_assessment"] == "not_assessed"


def test_compare_peer_audit_fees_real_db_shape():
    out = compare_peer_audit_fees("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["year"] == 2025
    assert out["peer_count"] > 0
    assert "audit_fee_m" in out["subject_metrics"]
    assert "audit_fee_to_assets_bps" in out["benchmarks"]


def test_compare_peer_audit_fees_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_audit_fees", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_audit_fees"
    assert out["peer_count"] > 0


def test_compare_peer_audit_fees_returns_three_year_subject_scale_history(
    temp_engine,
):
    """Catch regressions that hide company scale behind one-year fee/hour values."""
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="subject", corp_name="대상회사", market="KOSPI"),
            Company(corp_code="peer", corp_name="비교회사", market="KOSPI"),
        ])
        session.add_all([
            Financial(
                corp_code="subject",
                year=2024,
                quarter=4,
                fs_div="CFS",
                total_assets=4_000_000_000_000,
                revenue=2_000_000_000_000,
                source="acntall",
            ),
            Financial(
                corp_code="subject",
                year=2023,
                quarter=4,
                fs_div="CFS",
                total_assets=3_000_000_000_000,
                revenue=1_500_000_000_000,
                source="acntall",
            ),
            Financial(
                corp_code="peer",
                year=2024,
                quarter=4,
                fs_div="CFS",
                total_assets=1_000_000_000_000,
                revenue=500_000_000_000,
                source="acntall",
            ),
        ])
        session.add_all([
            AuditFee(
                corp_code="subject",
                bsns_year=2024,
                auditor_nm="감사법인A",
                audit_fee_m=800,
                audit_hours=8_000,
                source_rcept_no="20250318000123",
                source_class="business_report",
                source_period="2024",
                compatibility_basis="actual",
                availability_status="available",
            ),
            AuditFee(
                corp_code="subject",
                bsns_year=2023,
                auditor_nm="감사법인A",
                audit_fee_m=600,
                audit_hours=6_000,
                source_rcept_no="20240318000456",
                source_class="business_report",
                source_period="2023",
                compatibility_basis="actual",
                availability_status="available",
            ),
            AuditFee(
                corp_code="peer",
                bsns_year=2024,
                audit_fee_m=200,
                audit_hours=2_000,
                compatibility_basis="actual",
                availability_status="available",
            ),
        ])

    out = compare_peer_audit_fees(
        "subject",
        year=2024,
        _peer_group={
            "subject": {"corp_code": "subject", "corp_name": "대상회사"},
            "selection_policy": {"fs_div_used": "CFS"},
            "peers": [{"corp_code": "peer"}],
        },
    )

    assert [row["year"] for row in out["subject_scale_history"]] == [
        2024,
        2023,
        2022,
    ]
    current, prior, missing = out["subject_scale_history"]
    assert current == {
        "year": 2024,
        "fs_div": "CFS",
        "total_assets": 4_000_000_000_000,
        "total_assets_100m": 40_000.0,
        "revenue": 2_000_000_000_000,
        "revenue_100m": 20_000.0,
        "financial_source": "acntall",
        "auditor_nm": "감사법인A",
        "audit_fee_m": 800,
        "audit_hours": 8_000,
        "metric_basis": "actual",
        "audit_source_rcept_no": "20250318000123",
        "audit_source_class": "business_report",
        "audit_source_period": "2024",
        "audit_hours_per_trillion_assets": 2_000.0,
        "audit_hours_per_trillion_revenue": 4_000.0,
        "audit_fee_m_per_trillion_assets": 200.0,
        "audit_fee_m_per_trillion_revenue": 400.0,
        "audit_fee_per_hour_m": 0.1,
        "missing_fields": [],
    }
    assert prior["year"] == 2023
    assert prior["total_assets_100m"] == 30_000.0
    assert prior["revenue_100m"] == 15_000.0
    assert missing == {
        "year": 2022,
        "fs_div": "CFS",
        "missing_fields": [
            "total_assets",
            "revenue",
            "audit_fee_m",
            "audit_hours",
        ],
        "missing_fields_label": "총자산, 매출액, 감사보수, 감사시간",
    }
    assert out["data_quality"]["subject_scale_history"] == {
        "status": "limited",
        "requested_years": [2024, 2023, 2022],
        "covered_years": [2024, 2023],
        "complete_years": [2024, 2023],
        "missing_by_year": {
            "2022": [
                "total_assets",
                "revenue",
                "audit_fee_m",
                "audit_hours",
            ],
        },
    }


def test_compare_peer_audit_fees_never_mixes_typed_actual_and_contract(
    temp_engine,
):
    from kreports.db.engine import get_session

    codes = ("subject", "actual", "contract")
    with get_session() as session:
        session.add_all(
            [
                Company(corp_code=code, corp_name=code, market="KOSPI")
                for code in codes
            ]
        )
        session.add_all(
            [
                Financial(
                    corp_code=code,
                    year=2024,
                    quarter=4,
                    fs_div="CFS",
                    total_assets=1_000_000_000,
                )
                for code in codes
            ]
        )
        session.add_all(
            [
                AuditFee(
                    corp_code="subject",
                    bsns_year=2024,
                    audit_fee_m=200,
                    audit_hours=2000,
                    actual_fee_m=200,
                    actual_hours=2000,
                    compatibility_basis="actual",
                    availability_status="available",
                ),
                AuditFee(
                    corp_code="actual",
                    bsns_year=2024,
                    audit_fee_m=100,
                    audit_hours=1000,
                    actual_fee_m=100,
                    actual_hours=1000,
                    compatibility_basis="actual",
                    availability_status="available",
                ),
                AuditFee(
                    corp_code="contract",
                    bsns_year=2024,
                    audit_fee_m=1000,
                    audit_hours=10000,
                    contract_fee_m=1000,
                    contract_hours=10000,
                    compatibility_basis="contract",
                    availability_status="available",
                ),
            ]
        )

    out = compare_peer_audit_fees(
        "subject",
        year=2024,
        _peer_group={
            "subject": {"corp_code": "subject", "corp_name": "subject"},
            "selection_policy": {"fs_div_used": "CFS"},
            "peers": [
                {"corp_code": "actual"},
                {"corp_code": "contract"},
            ],
        },
    )

    # Compatibility keys remain stable for existing clients; typed actual and
    # contract populations stay separate for basis-sensitive review.
    assert {
        "audit_fee_m",
        "audit_hours",
        "audit_fee_to_assets_bps",
        "audit_fee_per_hour_m",
        "nas_ratio",
    } <= set(out["benchmarks"])
    assert out["benchmarks"]["actual_fee_m"]["p50"] == 100
    assert out["benchmarks"]["contract_fee_m"]["p50"] == 1000
    assert out["data_quality"]["basis_populations"]["actual"]["valid_fee_n"] == 1
    assert out["data_quality"]["basis_populations"]["contract"]["valid_fee_n"] == 1


def test_compare_peer_audit_fees_excludes_uncorroborated_unit_anomalies_and_explains_missing_receipts(
    temp_engine,
):
    """Never repair a suspect unit by guessing a multiplier or invent a filing link."""
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import enrich_answer_response

    with get_session() as session:
        session.add_all([
            Company(corp_code="subject", corp_name="대상회사", market="KOSPI"),
            Company(corp_code="peer", corp_name="단위의심회사", market="KOSPI"),
            Company(corp_code="wrong", corp_name="다른회사", market="KOSPI"),
            Company(corp_code="wrongdate", corp_name="날짜오류회사", market="KOSPI"),
        ])
        session.add_all([
            Financial(
                corp_code=code,
                year=2024,
                quarter=4,
                fs_div="CFS",
                total_assets=1_000_000_000,
            )
            for code in ("subject", "peer", "wrong", "wrongdate")
        ])
        session.add_all([
            AuditFee(
                corp_code="subject",
                bsns_year=2024,
                audit_fee_m=100,
                audit_hours=1_000,
                actual_fee_m=100,
                actual_hours=1_000,
                compatibility_basis="actual",
                availability_status="available",
                source_rcept_no="20250318000123",
            ),
            AuditFee(
                corp_code="peer",
                bsns_year=2024,
                audit_fee_m=162_000,
                audit_hours=1_553,
                actual_fee_m=162_000,
                actual_hours=1_553,
                non_audit_fee_m=80_000_000,
                nas_ratio=493.8272,
                compatibility_basis="actual",
                availability_status="available",
                # The backfill failure shape has no receipt that could justify
                # either an inferred unit conversion or a confirmed fact.
                source_rcept_no=None,
            ),
            AuditFee(
                corp_code="wrong",
                bsns_year=2024,
                audit_fee_m=80,
                audit_hours=800,
                actual_fee_m=80,
                actual_hours=800,
                compatibility_basis="actual",
                availability_status="available",
                source_rcept_no="20250318000123",
            ),
            AuditFee(
                corp_code="wrongdate",
                bsns_year=2024,
                audit_fee_m=80,
                audit_hours=800,
                actual_fee_m=80,
                actual_hours=800,
                compatibility_basis="actual",
                availability_status="available",
                source_rcept_no="20250318000888",
            ),
            Disclosure(
                rcept_no="20250318000123",
                corp_code="subject",
                corp_name="대상회사",
                disc_date=date(2025, 3, 18),
                disc_type="A",
                report_nm="사업보고서 (2024.12)",
            ),
            Disclosure(
                rcept_no="20250318000888",
                corp_code="wrongdate",
                corp_name="날짜오류회사",
                disc_date=date(2025, 3, 17),
                disc_type="A",
                report_nm="사업보고서 (2024.12)",
            ),
        ])

    out = compare_peer_audit_fees(
        "subject",
        year=2024,
        _peer_group={
            "subject": {"corp_code": "subject", "corp_name": "대상회사"},
            "selection_policy": {"fs_div_used": "CFS"},
            "peers": [
                {"corp_code": "peer"},
                {"corp_code": "wrong"},
                {"corp_code": "wrongdate"},
            ],
        },
    )

    peer = out["peers"][0]
    assert peer["audit_fee_m"] is None
    assert peer["actual_fee_m"] is None
    assert peer["non_audit_fee_m"] is None
    assert peer["nas_ratio"] is None
    assert peer["unit_integrity_status"] == "excluded_suspect_unit"
    assert out["data_quality"]["unit_integrity"]["excluded_row_count"] == 1
    assert out["confirmed_facts"] == [{
        "statement": "2024년 대상회사 감사보수 100백만원, 감사시간 1,000시간 (actual 기준).",
        "source": {"rcept_no": "20250318000123"},
    }]

    enriched = enrich_answer_response("compare_peer_audit_fees", out)

    assert [source["rcept_no"] for source in enriched["answer_pack"]["sources"]] == [
        "20250318000123"
    ]
    assert "162000" not in enriched["answer"]
    assert "단위를 추정 변환하지 않고" in enriched["answer"]


def test_compare_peer_risk_profile_shape():
    out = compare_peer_risk_profile("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "receivables_to_revenue" in out["benchmarks"]
    assert "disclosure_event_counts" in out


def test_compare_peer_risk_profile_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_risk_profile", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_risk_profile"
    assert out["peer_count"] > 0


def test_compare_peer_accounting_policies_shape():
    out = compare_peer_accounting_policies("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "subject_policy_count" in out
    assert "peer_item_coverage" in out
    assert "coverage_note" in out


def test_compare_peer_accounting_policies_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_accounting_policies", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_accounting_policies"
    assert "peer_item_coverage" in out


def test_compare_peer_kam_topics_shape():
    out = compare_peer_kam_topics("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "audit_report_events" in out
    assert "kam_topics" in out
    assert out["limitations"]


def test_compare_peer_kam_topics_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_kam_topics", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "compare_peer_kam_topics"
    assert "audit_report_events" in out


def test_compare_peer_audit_report_matters_shape():
    out = compare_peer_audit_report_matters("005930", year=2024, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "matter_counts" in out
    assert "other_matter" in out["matter_counts"]
    assert "emphasis" in out["matter_counts"]
    assert "going_concern" in out["matter_counts"]
    assert "subject_matters" in out


def test_peer_matter_counts_keep_boilerplate_as_evidence_without_acceptance_signal(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import ReportSection

    with get_session() as session:
        session.add(Company(corp_code="subject", stock_code="000001", corp_name="대상", market="KOSPI"))
        session.add(Company(corp_code="peer", stock_code="000002", corp_name="피어", market="KOSPI"))
        session.add(ReportSection(
            rcept_no="20260311000005",
            corp_code="subject",
            bsns_year=2025,
            source_type="audit_report",
            section_key="other_matter",
            section_title="기타사항",
            body_text="감사인의 책임과 지배기구와의 커뮤니케이션 사항을 설명합니다.",
            body_hash="matter-boilerplate",
            body_length=30,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = compare_peer_audit_report_matters(
        "subject",
        year=2025,
        _peer_group={
            "subject": {"corp_code": "subject", "corp_name": "대상"},
            "peers": [{"corp_code": "peer", "corp_name": "피어"}],
            "selection_policy": {"requested_year": 2025},
        },
    )

    assert out["matter_counts"]["other_matter"]["subject_count"] == 1
    assert out["matter_counts"]["other_matter"]["subject_signal_count"] == 0
    assert out["subject_matters"][0]["acceptance_signal"] is False
    assert "data_quality" in out


def test_compare_peer_audit_report_matters_mcp_dispatch():
    out = json.loads(call_tool("compare_peer_audit_report_matters", {"company": "005930", "year": 2024}))
    assert out["_meta"]["tool"] == "compare_peer_audit_report_matters"
    assert "matter_counts" in out


def test_search_audit_report_matters_company_question_shape():
    out = search_audit_report_matters(company="005930", year=2024, section_keys=["other_matter"], limit=20)
    assert out["query"]["company"] == "005930"
    assert out["query"]["year"] == 2024
    assert out["data_quality"]["source"] in {"audit_matter_items", "report_sections.audit_report"}
    assert "companies" in out
    assert out["total_companies"] >= 0
    if out["companies"]:
        first = out["companies"][0]
        assert first["corp_code"] == "00126380"
        assert "matter_counts" in first
        assert "sections" in first


def test_search_audit_report_matters_industry_question_mcp_dispatch():
    out = json.loads(call_tool(
        "search_audit_report_matters",
        {
            "year": 2024,
            "market": "KOSPI",
            "induty_prefix": "26",
            "section_keys": ["emphasis", "other_matter"],
            "limit": 10,
        },
    ))
    assert out["_meta"]["tool"] == "search_audit_report_matters"
    assert out["query"]["year"] == 2024
    assert out["query"]["induty_prefix"] == "26"
    assert "companies" in out
    assert out["total_companies"] >= 0


def test_search_audit_report_matters_adds_topic_and_severity_hints(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import ReportSection

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="강조테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(ReportSection(
            rcept_no="20250311000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            section_key="going_concern",
            section_title="계속기업 관련 중요한 불확실성",
            body_text="유동부채가 유동자산을 초과하여 계속기업 존속능력에 중요한 불확실성이 존재합니다.",
            body_hash="x",
            body_length=50,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = search_audit_report_matters(company="000001", year=2024, section_keys=["going_concern"], limit=5)

    section = out["companies"][0]["sections"][0]
    assert section["severity_hint"] == "high"
    assert "going_concern" in section["topic_tags"]


def test_get_audit_report_sections_falls_back_to_evidence_documents(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="근거KAM테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000001",
            dcm_no="D001",
            evidence_scope="auditor_view",
            title="2025 audit_report evidence",
            normalized_text=(
                "# Evidence document\n"
                "## report_section/audit_opinion: 감사의견\n"
                "적정의견입니다.\n"
                "## report_section/kam: 핵심감사사항\n"
                "수익인식은 핵심감사사항입니다. 핵심감사사항으로 선정한 이유는 거래조건 판단이 중요하기 때문입니다.\n"
                "우리는 매출 거래의 근거 문서검사와 기간귀속 테스트를 수행하였습니다.\n"
            ),
            text_hash="x",
            text_length=180,
            source_count=1,
            generated_at=datetime.utcnow(),
        ))

    out = get_audit_report_sections("000001", year=2025, section_key="kam")

    assert out["section_count"] == 1
    assert out["data_quality"]["source"] == "evidence_documents"
    assert out["sections"][0]["section_key"] == "kam"
    assert "수익인식" in out["sections"][0]["body_excerpt"]
    assert out["sections"][0]["kam_analysis"]["has_procedure_hint"] is True


def test_search_audit_report_matters_falls_back_to_evidence_documents(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="근거강조테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(EvidenceDocument(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000002",
            dcm_no="D002",
            evidence_scope="auditor_view",
            title="2025 audit_report evidence",
            normalized_text=(
                "# Evidence document\n"
                "## report_section/emphasis: 강조사항\n"
                "계속기업 존속능력에 중요한 불확실성이 존재합니다.\n"
            ),
            text_hash="x",
            text_length=90,
            source_count=1,
            generated_at=datetime.utcnow(),
        ))

    out = search_audit_report_matters(company="000001", year=2025, section_keys=["emphasis"], limit=5)

    assert out["data_quality"]["source"] == "evidence_documents"
    assert out["total_sections"] == 1
    section = out["companies"][0]["sections"][0]
    assert section["section_key"] == "emphasis"
    assert section["severity_hint"] == "high"


def test_search_audit_procedures_falls_back_to_full_body_kam_only(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="근거절차테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(KamItem(
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260311000003",
            dcm_no="D003",
            ordinal=1,
            title="수익인식",
            normalized_topic="revenue",
            reason_text="수익인식은 핵심감사사항입니다.",
            audit_response_text=(
                "매출 관련 내부통제의 운영효과성을 테스트하였습니다."
            ),
            related_note_references_json="[]",
            full_body_hash="x" * 40,
            full_body_length=500,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime.utcnow(),
        ))

    out = search_audit_procedures(company="000001", year=2025, keyword="내부통제", limit=5)

    assert out["data_quality"]["source"] == "kam_items.full_body"
    assert out["total_procedures"] == 1
    record = out["companies"][0]["records"][0]
    assert record["procedure_type"] == "internal_control"
    assert "내부통제" in record["procedure_excerpt"]


def test_search_dataset_report_sections_shape():
    out = search_dataset(
        dataset="report_sections",
        company="005930",
        year=2024,
        source_type="audit_report",
        section_keys=["kam", "other_matter"],
        limit=10,
    )
    assert out["query"]["dataset"] == "report_sections"
    assert out["data_quality"]["source"] == "report_sections"
    assert "companies" in out
    if out["companies"]:
        assert out["companies"][0]["corp_code"] == "00126380"
        assert "records" in out["companies"][0]


def test_search_dataset_policy_and_structured_mcp_dispatch():
    policy = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "accounting_policies",
            "company": "005930",
            "year": 2025,
            "keyword": "수익",
            "limit": 5,
        },
    ))
    assert policy["_meta"]["tool"] == "search_dataset"
    assert policy["query"]["dataset"] == "accounting_policies"
    assert "companies" in policy

    fees = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "audit_fees",
            "year": 2025,
            "market": "KOSPI",
            "limit": 5,
        },
    ))
    assert fees["_meta"]["tool"] == "search_dataset"
    assert fees["query"]["dataset"] == "audit_fees"
    assert "companies" in fees


def test_search_dataset_accounting_note_chapters(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="정책테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(AccountingNoteChapter(
            corp_code="00000001",
            bsns_year=2024,
            fs_div="CFS",
            rcept_no="20250311000001",
            source_type="business_report",
            note_no="3",
            note_title="중요한 회계정책",
            section_type="policy",
            body="수익은 고객과의 계약에서 수행의무가 이행될 때 인식한다.",
            body_hash="x",
            body_length=34,
            fetched_at=datetime.utcnow(),
        ))

    out = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "accounting_note_chapters",
            "company": "000001",
            "year": 2024,
            "section_type": "policy",
            "keyword": "수익",
            "limit": 5,
        },
    ))

    assert out["_meta"]["tool"] == "search_dataset"
    assert out["query"]["dataset"] == "accounting_note_chapters"
    assert out["query"]["section_type"] == "policy"
    assert out["data_quality"]["source"] == "accounting_note_chapters"
    assert out["companies"][0]["corp_code"] == "00000001"
    record = out["companies"][0]["records"][0]
    assert record["note_no"] == "3"
    assert record["note_title"] == "중요한 회계정책"
    assert record["section_type"] == "policy"
    assert "수익" in record["body_excerpt"]


def test_search_dataset_source_documents_marks_derived_evidence(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="근거테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(SourceDocument(
            rcept_no="20250311000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            report_nm="derived from report_sections",
            content_type="derived_report_sections",
            raw_content="DERIVED FROM report_sections\n## kam | 핵심감사사항\n수익인식 관련 핵심감사사항입니다.",
            doc_hash="x",
            fetched_at=datetime.utcnow(),
        ))

    out = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "source_documents",
            "company": "000001",
            "year": 2024,
            "source_type": "audit_report",
            "keyword": "수익인식",
            "limit": 5,
        },
    ))

    assert out["query"]["dataset"] == "source_documents"
    assert out["data_quality"]["source"] == "source_documents"
    assert "derived" in out["data_quality"]["interpretation"]
    record = out["companies"][0]["records"][0]
    assert record["content_type"] == "derived_report_sections"
    assert "수익인식" in record["body_excerpt"]


def test_search_dataset_source_documents_reads_externalized_excerpt(temp_engine, tmp_path, monkeypatch):
    import kreports.analysis.search_adapter as search_adapter_module
    from kreports.db.engine import get_session
    from kreports.storage.raw_documents import RawDocumentStore, sha1_text

    store = RawDocumentStore(base_dir=tmp_path)
    monkeypatch.setattr(search_adapter_module, "RawDocumentStore", lambda: store)
    raw_content = "<DOCUMENT><P>외부 gzip 원문에서 수익인식 문단을 읽습니다.</P></DOCUMENT>"
    saved = store.write(
        corp_code="00000001",
        bsns_year=2024,
        source_type="audit_report",
        rcept_no="20250311000002",
        content_type="xml",
        content=raw_content,
    )
    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="외부원문테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(SourceDocument(
            rcept_no="20250311000002",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            report_nm="감사보고서",
            content_type="xml",
            raw_content="",
            doc_hash=sha1_text(raw_content),
            storage_uri=saved.storage_uri,
            content_length=saved.content_length,
            compressed_length=saved.compressed_length,
            storage_status="externalized",
            fetched_at=datetime.utcnow(),
        ))

    out = json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "source_documents",
            "company": "000001",
            "year": 2024,
            "source_type": "audit_report",
            "keyword": "수익인식",
            "limit": 5,
        },
    ))

    assert out["data_quality"]["source"] == "source_documents"
    record = out["companies"][0]["records"][0]
    assert record["storage_status"] == "externalized"
    assert "수익인식" in record["body_excerpt"]


def test_search_audit_procedures_mcp_dispatch(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="절차테스트",
            market="KOSPI",
            induty_code="264",
        ))
        session.add(AuditProcedureItem(
            rcept_no="20250311000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            kam_topic="revenue",
            procedure_type="internal_control",
            procedure_text="매출차감 처리 관련 내부통제 이해 및 평가를 수행하였습니다.",
            procedure_hash="x",
            procedure_length=35,
            section_ordinal=0,
            procedure_ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = json.loads(call_tool(
        "search_audit_procedures",
        {
            "company": "000001",
            "year": 2024,
            "kam_topic": "revenue",
            "procedure_type": "internal_control",
            "keyword": "내부통제",
            "limit": 5,
        },
    ))

    assert out["_meta"]["tool"] == "search_audit_procedures"
    assert out["total_procedures"] == 1
    assert out["procedure_type_counts"]["internal_control"] == 1
    assert "내부통제" in out["companies"][0]["records"][0]["procedure_excerpt"]


def test_compare_peer_audit_procedures_shape(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", stock_code="000001", corp_name="대상", market="KOSPI", induty_code="264"),
            Company(corp_code="00000002", stock_code="000002", corp_name="피어", market="KOSPI", induty_code="264"),
        ])
        for cc in ("00000001", "00000002"):
            session.add(Financial(
                corp_code=cc,
                year=2024,
                quarter=4,
                fs_div="CFS",
                revenue=100,
                operating_profit=10,
                net_income=8,
                total_assets=1000,
                total_debt=400,
                total_equity=600,
            ))
            session.add(AuditProcedureItem(
                rcept_no=f"20250331{cc[-6:]}",
                corp_code=cc,
                bsns_year=2024,
                source_type="audit_report",
                kam_topic="revenue",
                procedure_type="substantive_test",
                procedure_text="매출 거래 근거 문서검사를 수행하였습니다.",
                procedure_hash=cc,
                procedure_length=24,
                section_ordinal=0,
                procedure_ordinal=0,
                fetched_at=datetime.utcnow(),
            ))

    out = compare_peer_audit_procedures("000001", year=2024, peer_limit=5)

    assert out["subject"]["corp_code"] == "00000001"
    assert out["data_quality"]["source"] == "audit_procedure_items"
    assert out["subject_procedure_type_counts"]["substantive_test"] == 1
    assert out["peer_procedure_type_counts"]["substantive_test"] == 1


def test_estimate_audit_hours_proxy_shape():
    out = estimate_audit_hours_proxy("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["peer_count"] > 0
    assert "complexity_score" in out
    assert "drivers" in out
    assert "peer_benchmarks" in out
    assert all("score_after" in d for d in out["drivers"])


def test_estimate_audit_hours_proxy_mcp_dispatch():
    out = json.loads(call_tool("estimate_audit_hours_proxy", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "estimate_audit_hours_proxy"
    assert "complexity_score" in out


def test_build_audit_acceptance_pack_shape():
    out = build_audit_acceptance_pack("005930", year=2025, peer_limit=20)
    assert out["subject"]["corp_code"] == "00126380"
    assert "acceptance_signals" in out
    assert "data_quality" in out
    assert "kam_reason_coverage" in out["data_quality"]["kam_body"]
    assert "kam_procedure_coverage" in out["data_quality"]["kam_body"]
    assert "recommended_review_areas" in out
    assert out["scope"] == "external_dart_evidence_pack"
    assert "audit_report_sections" in out["kam_summary"]
    assert "subject_sections" in out["kam_summary"]
    assert "audit_report_matter_summary" in out
    assert "audit_report_matters" in out["data_quality"]


def test_build_audit_acceptance_pack_mcp_dispatch():
    out = json.loads(call_tool("build_audit_acceptance_pack", {"company": "005930", "year": 2025}))
    assert out["_meta"]["tool"] == "build_audit_acceptance_pack"
    assert "acceptance_signals" in out


def test_acceptance_pack_selects_one_requested_year_cohort_and_reuses_it(monkeypatch):
    from kreports.analysis import peer_benchmarks

    cohort = {
        "subject": {"corp_code": "001", "corp_name": "A"},
        "peers": [{"corp_code": "002"}],
        "peer_count": 1,
        "confidence": "insufficient",
        "selection_policy": {"requested_year": 2022, "resolved_year": 2022, "fs_div_used": "CFS"},
    }
    selected_years = []
    reused = []
    monkeypatch.setattr(peer_benchmarks, "select_peer_group", lambda **kwargs: (selected_years.append(kwargs["year"]) or cohort))

    def child_result(name, result):
        def child(*args, **kwargs):
            reused.append((name, kwargs.get("_peer_group")))
            return result
        return child

    scale_history = [{
        "year": 2022,
        "fs_div": "CFS",
        "total_assets_100m": 1234.0,
        "revenue_100m": 567.0,
        "audit_fee_m": 100,
        "audit_hours": 1_000,
        "missing_fields": [],
    }]
    monkeypatch.setattr(peer_benchmarks, "compare_peer_audit_fees", child_result("fee", {
        "subject_metrics": {},
        "subject_scale_history": scale_history,
        "benchmarks": {},
        "data_quality": {
            "subject_scale_history": {
                "status": "limited",
                "requested_years": [2022, 2021, 2020],
                "covered_years": [2022],
                "complete_years": [2022],
                "missing_by_year": {},
            },
        },
    }))
    monkeypatch.setattr(peer_benchmarks, "compare_peer_risk_profile", child_result("risk", {"subject_metrics": {}, "benchmarks": {}}))
    monkeypatch.setattr(peer_benchmarks, "estimate_audit_hours_proxy", child_result("hours", {"complexity_band": "low", "drivers": []}))
    monkeypatch.setattr(peer_benchmarks, "compare_peer_accounting_policies", child_result("policy", {"peer_count": 1, "peers_with_policy": 1, "data_quality": {"status": "usable"}}))
    monkeypatch.setattr(peer_benchmarks, "compare_peer_kam_topics", child_result("kam", {"audit_report_events": {}, "audit_report_sections": {}, "data_quality": {}}))
    monkeypatch.setattr(peer_benchmarks, "compare_peer_audit_report_matters", child_result("matter", {"matter_counts": {}, "data_quality": {}}))

    out = peer_benchmarks.build_audit_acceptance_pack("001", year=2022)

    assert selected_years == [2022]
    assert {name for name, _ in reused} == {"fee", "risk", "hours", "policy", "kam", "matter"}
    assert all(group is cohort for _, group in reused)
    assert out["peer_group"]["selection_policy"]["requested_year"] == 2022
    assert out["peer_group"]["selection_policy"]["resolved_year"] == 2022
    assert out["subject_scale_history"] == scale_history
    assert out["data_quality"]["subject_scale_history"]["status"] == "limited"


def test_audit_hours_proxy_selects_one_requested_year_cohort(monkeypatch):
    from kreports.analysis import peer_benchmarks

    cohort = {
        "subject": {"corp_code": "001", "corp_name": "A"},
        "peers": [{"corp_code": "002"}],
        "peer_count": 1,
        "selection_policy": {"requested_year": 2022, "resolved_year": 2022, "fs_div_used": "CFS"},
    }
    selected = []
    passed = []
    monkeypatch.setattr(peer_benchmarks, "select_peer_group", lambda **kwargs: (selected.append(kwargs["year"]) or cohort))

    def fees(*args, **kwargs):
        passed.append(kwargs["_peer_group"])
        return {"subject": cohort["subject"], "peer_count": 1, "subject_metrics": {}, "benchmarks": {}, "data_quality": {}}

    def risk(*args, **kwargs):
        passed.append(kwargs["_peer_group"])
        return {"subject_metrics": {}, "data_quality": {}}

    monkeypatch.setattr(peer_benchmarks, "compare_peer_audit_fees", fees)
    monkeypatch.setattr(peer_benchmarks, "compare_peer_risk_profile", risk)
    peer_benchmarks.estimate_audit_hours_proxy("001", year=2022)

    assert selected == [2022]
    assert passed == [cohort, cohort]


@pytest.mark.parametrize("comparator_name", [
    "compare_peer_audit_fees",
    "compare_peer_risk_profile",
    "compare_peer_accounting_policies",
    "compare_peer_kam_topics",
    "compare_peer_audit_report_matters",
    "compare_peer_audit_procedures",
])
def test_peer_comparators_select_the_requested_year_when_no_cohort_is_supplied(monkeypatch, comparator_name):
    """All peer comparisons must resolve their own cohort at the data year, never latest."""
    from contextlib import nullcontext

    from kreports.analysis import peer_benchmarks

    selected_years = []

    def no_cohort(**kwargs):
        selected_years.append(kwargs["year"])
        return {"error": "stop after selection"}

    monkeypatch.setattr(peer_benchmarks, "select_peer_group", no_cohort)
    if comparator_name == "compare_peer_audit_procedures":
        monkeypatch.setattr(
            peer_benchmarks,
            "procedure_database_preflight",
            lambda _tables: (True, None),
        )
        monkeypatch.setattr(
            peer_benchmarks,
            "procedure_read_engine",
            lambda _tables: nullcontext(None),
        )
    result = getattr(peer_benchmarks, comparator_name)("001", year=2022)

    assert result == {"error": "stop after selection"}
    assert selected_years == [2022]


def test_auditor_handler_routes_peer_risk_through_decision_wrapper(monkeypatch):
    from kreports.mcp.handlers import auditor

    captured = {}
    monkeypatch.setattr(auditor, "resolve_company", lambda value: f"resolved:{value}")
    monkeypatch.setattr(auditor, "compare_peer_risk_profile", lambda **kwargs: captured.setdefault("value", kwargs) or kwargs)

    result = auditor.handle_compare_peer_risk_profile(type("Args", (), {
        "company": "005930", "year": 2025, "peer_limit": 5, "fs_strategy": "auto",
    })())

    assert captured["value"]["company"] == "resolved:005930"
    assert result == captured["value"]
