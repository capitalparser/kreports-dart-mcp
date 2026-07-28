"""Public semantic guards for audit-report evidence surfaces."""
from __future__ import annotations

from datetime import datetime

from kreports.db.models import Company, ReportSection


def _add_company(session, corp_code: str = "00000001") -> None:
    stock_code = "000001" if corp_code != "peer" else "000002"
    session.add(Company(
        corp_code=corp_code,
        stock_code=stock_code,
        corp_name="의미검증회사",
        market="KOSPI",
        induty_code="264",
    ))


def test_section_guidance_is_specific_to_opinion_basis_and_kam():
    from kreports.analysis.audit_reporting import audit_section_guidance

    opinion_analysis, opinion_checks = audit_section_guidance("audit_opinion")
    basis_analysis, basis_checks = audit_section_guidance("basis_for_opinion")
    kam_analysis, kam_checks = audit_section_guidance("kam")

    assert opinion_analysis != kam_analysis
    assert opinion_checks != kam_checks
    assert basis_analysis != kam_analysis
    assert basis_checks != kam_checks
    assert "KAM" not in " ".join(
        [item["statement"] for item in opinion_analysis] + opinion_checks,
    )
    assert "재분류하지 마세요" in " ".join(
        [item["statement"] for item in basis_analysis] + basis_checks,
    )


def test_opinion_basis_text_without_parsed_conclusion_stays_limited(temp_engine):
    from kreports.analysis.audit_reporting import get_audit_report_sections
    from kreports.db.engine import get_session

    with get_session() as session:
        _add_company(session)
        session.add(ReportSection(
            rcept_no="20260311000001",
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            section_key="audit_opinion",
            section_title="감사의견",
            body_text="감사의견의 근거 문단에서 감사기준 준수와 독립성을 설명합니다.",
            body_hash="opinion-basis-only",
            body_length=34,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = get_audit_report_sections("000001", year=2025, section_key="audit_opinion")

    assert out["data_quality"]["status"] == "limited"
    assert out["data_quality"]["opinion_conclusion_coverage"]["available"] == 0


def test_kam_without_topic_reason_or_procedure_is_limited_but_retains_receipt_and_excerpt(temp_engine):
    from kreports.analysis.audit_reporting import get_audit_report_sections
    from kreports.db.engine import get_session

    with get_session() as session:
        _add_company(session)
        session.add(ReportSection(
            rcept_no="20260311000002",
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="감사인과 지배기구에 커뮤니케이션한 사항입니다.",
            body_hash="kam-generic",
            body_length=25,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    out = get_audit_report_sections("000001", year=2025, section_key="kam")

    assert out["data_quality"]["status"] == "limited"
    assert out["data_quality"]["semantic_complete"] is False
    for field in ("topic_coverage", "reason_coverage", "procedure_coverage"):
        assert out["data_quality"][field] == {
            "available": 0,
            "total": 1,
            "status": "limited",
        }
    fact = out["confirmed_facts"][0]
    assert fact["source"]["rcept_no"] == "20260311000002"
    assert fact["excerpt"] == "감사인과 지배기구에 커뮤니케이션한 사항입니다."


def test_kam_semantic_complete_requires_every_current_item_and_receipt_linked_source(temp_engine):
    from kreports.analysis.audit_reporting import get_audit_report_sections
    from kreports.db.engine import get_session

    with get_session() as session:
        _add_company(session)
        session.add_all([
            ReportSection(
                rcept_no="20260311000003",
                corp_code="00000001",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text="수익인식은 핵심감사사항입니다. 선정 이유는 거래조건 판단입니다. 문서검사를 수행하였습니다.",
                body_hash="kam-complete",
                body_length=52,
                ordinal=0,
                fetched_at=datetime.utcnow(),
            ),
            ReportSection(
                rcept_no="",
                corp_code="00000001",
                bsns_year=2025,
                source_type="audit_report",
                section_key="kam",
                section_title="핵심감사사항",
                body_text="재고평가는 핵심감사사항입니다. 선정 이유는 추정의 불확실성입니다. 표본검사를 수행하였습니다.",
                body_hash="kam-uncited",
                body_length=52,
                ordinal=1,
                fetched_at=datetime.utcnow(),
            ),
        ])

    out = get_audit_report_sections("000001", year=2025, section_key="kam")

    assert out["data_quality"]["semantic_complete"] is False
    assert out["data_quality"]["source_coverage"] == {
        "available": 1,
        "total": 2,
        "status": "limited",
    }


def test_basis_and_boilerplate_do_not_create_other_matter_acceptance_signals():
    from kreports.analysis.audit_reporting import classify_audit_matter

    boilerplate = "감사인의 책임과 지배기구와의 커뮤니케이션 사항을 설명합니다."

    assert classify_audit_matter(boilerplate, "other_matter")["acceptance_signal"] is False
    assert classify_audit_matter("감사기준 준수 및 독립성 진술입니다.", "basis_for_opinion")["acceptance_signal"] is False


def test_peer_kam_answer_and_pack_share_limited_semantic_status(temp_engine):
    from kreports.analysis.peer_benchmarks import compare_peer_kam_topics
    from kreports.db.engine import get_session
    from kreports.mcp.contracts import enrich_answer_response

    with get_session() as session:
        _add_company(session, "subject")
        _add_company(session, "peer")
        session.add(ReportSection(
            rcept_no="20260311000004",
            corp_code="subject",
            bsns_year=2025,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="감사인과 지배기구에 커뮤니케이션한 사항입니다.",
            body_hash="peer-kam-generic",
            body_length=25,
            ordinal=0,
            fetched_at=datetime.utcnow(),
        ))

    raw = compare_peer_kam_topics(
        "subject",
        year=2025,
        _peer_group={
            "subject": {"corp_code": "subject", "corp_name": "의미검증회사"},
            "peers": [{"corp_code": "peer", "corp_name": "peer"}],
            "selection_policy": {"requested_year": 2025},
        },
    )
    public = enrich_answer_response("compare_peer_kam_topics", raw)

    assert raw["data_quality"]["status"] == "limited"
    assert public["quality_status"] == "limited"
    assert public["answer_pack"]["data_quality"]["status"] == "limited"


def test_kam_lifecycle_rows_without_semantic_coverage_stay_limited(monkeypatch):
    from kreports.analysis import audit_reporting
    from kreports.analysis import kam_lifecycle

    monkeypatch.setattr(audit_reporting, "resolve_corp_code", lambda _: "001")
    monkeypatch.setattr(audit_reporting, "get_company_summary", lambda _: {"corp_code": "001", "corp_name": "A"})
    monkeypatch.setattr(kam_lifecycle, "kam_lifecycle_for_company", lambda *args, **kwargs: {
        "events": [{
            "year": 2025,
            "topic": "unknown",
            "has_reason_hint": False,
            "has_procedure_hint": False,
            "rcept_no": "20260311000006",
            "body_excerpt": "감사인과 지배기구에 커뮤니케이션한 사항입니다.",
        }],
        "data_quality": {"status": "usable", "source": "report_sections.audit_report"},
    })

    out = audit_reporting.get_kam_lifecycle("001", start_year=2021, end_year=2025)

    assert out["data_quality"]["status"] == "limited"
    assert out["data_quality"]["timeline_status"] == "usable"
    assert out["data_quality"]["semantic_complete"] is False
    assert out["data_quality"]["topic_coverage"]["available"] == 0
