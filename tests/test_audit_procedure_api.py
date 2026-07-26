from datetime import datetime

from sqlalchemy import create_engine, text

from kreports.analysis.api import search_audit_procedures
from kreports.analysis.peer_benchmarks import compare_peer_audit_procedures
from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, Company, KamItem


def test_search_audit_procedures_returns_linkages_and_source_note(temp_engine):
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
                induty_code="264",
            )
        )
        session.add(
            AuditProcedureItem(
                corp_code="00126380",
                bsns_year=2025,
                rcept_no="20260301000001_100",
                dcm_no="100",
                source_type="audit_report",
                section_ordinal=1,
                kam_topic="revenue",
                procedure_type="substantive_test",
                procedure_text="매출 계약서 문서검사와 기간귀속 테스트를 수행하였습니다.",
                procedure_hash="p1",
                procedure_length=32,
                procedure_ordinal=1,
                fetched_at=datetime(2026, 3, 1),
            )
        )

    result = search_audit_procedures(company="005930", year=2025, limit=5)

    record = result["companies"][0]["records"][0]
    assert record["linkages"][0]["category"] == "audit_report_kam"
    assert result["data_quality"]["source"] == "audit_procedure_items"
    assert "audit-report KAM" in result["data_quality"]["interpretation"]


def test_search_audit_procedures_exposes_structured_linkage_contract(temp_engine):
    with get_session() as session:
        session.add(
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
                induty_code="264",
            )
        )
        kam = KamItem(
            rcept_no="20260301000001_100",
            dcm_no="100",
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            ordinal=1,
            title="수익인식",
            normalized_topic="revenue",
            reason_text="기간귀속 위험",
            audit_response_text="기말 전후 매출의 기간귀속 테스트를 수행하였습니다.",
            related_note_references_json='["주석 2"]',
            full_body_hash="a" * 40,
            full_body_length=500,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime(2026, 3, 1),
        )
        session.add(kam)
        session.flush()
        kam_id = kam.id
        session.add(
            AuditProcedureItem(
                kam_item_id=kam.id,
                corp_code="00126380",
                bsns_year=2025,
                rcept_no=kam.rcept_no,
                dcm_no="100",
                source_type="audit_report",
                section_ordinal=1,
                kam_topic="revenue",
                method="cutoff_test",
                procedure_type="cutoff",
                procedure_text="기말 전후 매출의 기간귀속 테스트를 수행하였습니다.",
                procedure_hash="b" * 40,
                procedure_length=30,
                procedure_ordinal=1,
                assertion_hints_json='["cutoff", "occurrence"]',
                linked_metric_keys_json='["revenue"]',
                linked_note_keys_json='["revenue_policy"]',
                linked_event_keys_json="[]",
                parser_version="audit_procedure.v1",
                quality_status="full_body",
                fetched_at=datetime(2026, 3, 1),
            )
        )

    result = search_audit_procedures(
        year=2025,
        induty_prefix="264",
        procedure_type="cutoff",
        limit=5,
    )
    method_result = search_audit_procedures(
        year=2025,
        induty_prefix="264",
        method="cutoff_test",
        limit=5,
    )

    record = result["companies"][0]["records"][0]
    assert record["method"] == "cutoff_test"
    assert record["procedure_text"].startswith("기말 전후")
    assert record["assertion_hints"] == ["cutoff", "occurrence"]
    assert record["linked_metric_keys"] == ["revenue"]
    assert record["linked_note_keys"] == ["revenue_policy"]
    assert record["source_kam"]["id"] == kam_id
    assert record["parser_version"] == "audit_procedure.v1"
    assert "navigation aid" in result["data_quality"]["interpretation"]
    assert method_result["total_procedures"] == 1
    assert method_result["query"]["method"] == "cutoff_test"


def test_peer_procedure_coverage_uses_full_body_kams_only(temp_engine):
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000001",
                    stock_code="000001",
                    corp_name="대상",
                    market="KOSPI",
                    induty_code="264",
                ),
                Company(
                    corp_code="00000002",
                    stock_code="000002",
                    corp_name="피어",
                    market="KOSPI",
                    induty_code="264",
                ),
            ]
        )
        seeded: list[KamItem] = []
        for ordinal, (corp_code, status) in enumerate(
            [
                ("00000001", "full_body"),
                ("00000002", "full_body"),
                ("00000002", "summary_only"),
                ("00000002", "error"),
            ],
            start=1,
        ):
            item = KamItem(
                rcept_no=f"2026030100000{ordinal}_100",
                corp_code=corp_code,
                bsns_year=2025,
                source_type="audit_report",
                ordinal=ordinal,
                title="수익인식",
                normalized_topic="revenue",
                reason_text="위험",
                audit_response_text=(
                    "계약서를 검사하였습니다."
                    if status == "full_body"
                    else None
                ),
                related_note_references_json="[]",
                full_body_hash=str(ordinal) * 40,
                full_body_length=500 if status == "full_body" else 20,
                source_basis="source_documents.full_body",
                parser_version="kam.v1",
                quality_status=status,
                fetched_at=datetime(2026, 3, 1),
            )
            session.add(item)
            session.flush()
            seeded.append(item)
        session.add(
            AuditProcedureItem(
                kam_item_id=seeded[0].id,
                rcept_no=seeded[0].rcept_no,
                corp_code="00000001",
                bsns_year=2025,
                source_type="audit_report",
                kam_topic="revenue",
                method="inspection",
                procedure_type="substantive_test",
                procedure_text="계약서를 검사하였습니다.",
                procedure_hash="f" * 40,
                section_ordinal=seeded[0].ordinal,
                procedure_ordinal=1,
                parser_version="audit_procedure.v1",
                quality_status="full_body",
            )
        )

    result = compare_peer_audit_procedures(
        "000001",
        year=2025,
        _peer_group={
            "subject": {
                "corp_code": "00000001",
                "stock_code": "000001",
                "corp_name": "대상",
            },
            "peers": [
                {
                    "corp_code": "00000002",
                    "stock_code": "000002",
                    "corp_name": "피어",
                }
            ],
            "selection_policy": {"profile": "kam_procedure"},
        },
    )

    assert result["coverage"]["denominator_full_body_kam_receipts"] == 2
    assert result["coverage"]["full_body_kam_receipts_with_procedures"] == 1
    assert result["coverage"]["rate"] == 50.0
    assert result["coverage"]["quality_gaps"] == {
        "summary_only": 1,
        "missing": 0,
        "error": 1,
    }
    assert result["subject_method_counts"] == {"inspection": 1}


def test_procedure_read_surfaces_do_not_create_missing_sqlite_file(
    tmp_path,
    monkeypatch,
):
    import kreports.db.engine as engine_module
    from kreports.analysis.audit_procedure_evidence import (
        build_audit_procedure_evidence_map,
    )

    db_path = tmp_path / "missing-procedure.db"
    missing_engine = create_engine(f"sqlite:///{db_path}")
    monkeypatch.setattr(engine_module, "engine", missing_engine)

    evidence = build_audit_procedure_evidence_map(year=2025)
    search = search_audit_procedures(year=2025)
    peer = compare_peer_audit_procedures(
        "000001",
        year=2025,
        _peer_group={
            "subject": {"corp_code": "00000001"},
            "peers": [],
            "selection_policy": {},
        },
    )

    assert evidence["database_status"] == "unavailable"
    assert search["data_quality"]["status"] == "unavailable"
    assert peer["data_quality"]["status"] == "unavailable"
    assert not db_path.exists()


def test_procedure_read_surfaces_report_unmigrated_schema_without_crash(
    tmp_path,
    monkeypatch,
):
    import kreports.db.engine as engine_module
    from kreports.analysis.audit_procedure_evidence import (
        build_audit_procedure_evidence_map,
    )

    db_path = tmp_path / "legacy-procedure.db"
    legacy_engine = create_engine(f"sqlite:///{db_path}")
    with legacy_engine.begin() as conn:
        conn.execute(text("CREATE TABLE companies (corp_code VARCHAR(8))"))
        conn.execute(
            text(
                "CREATE TABLE kam_items ("
                "id INTEGER PRIMARY KEY, corp_code VARCHAR(8), "
                "bsns_year INTEGER, rcept_no VARCHAR(80), "
                "source_type VARCHAR(30), quality_status VARCHAR(20))"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE audit_procedure_items ("
                "id INTEGER PRIMARY KEY, corp_code VARCHAR(8), "
                "bsns_year INTEGER, rcept_no VARCHAR(80), "
                "source_type VARCHAR(30), procedure_type VARCHAR(50), "
                "procedure_text TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE report_sections ("
                "id INTEGER PRIMARY KEY, corp_code VARCHAR(8), "
                "bsns_year INTEGER, rcept_no VARCHAR(80), "
                "source_type VARCHAR(30))"
            )
        )
    monkeypatch.setattr(engine_module, "engine", legacy_engine)

    evidence = build_audit_procedure_evidence_map(year=2025)
    search = search_audit_procedures(year=2025)
    peer = compare_peer_audit_procedures(
        "000001",
        year=2025,
        _peer_group={
            "subject": {"corp_code": "00000001"},
            "peers": [],
            "selection_policy": {},
        },
    )

    assert evidence["database_status"] == "unavailable"
    assert evidence["database_reason"].startswith("missing_columns:")
    assert search["data_quality"]["status"] == "unavailable"
    assert peer["data_quality"]["status"] == "unavailable"


def test_peer_full_body_fallback_preserves_procedure_method(temp_engine):
    with get_session() as session:
        session.add_all(
            [
                Company(
                    corp_code="00000001",
                    stock_code="000001",
                    corp_name="대상",
                    market="KOSPI",
                ),
                Company(
                    corp_code="00000002",
                    stock_code="000002",
                    corp_name="피어",
                    market="KOSPI",
                ),
            ]
        )
        for corp_code, receipt, response in (
            ("00000001", "F1", "계약서를 검사하였습니다."),
            ("00000002", "F2", "거래처에 외부조회서를 발송하였습니다."),
        ):
            session.add(
                KamItem(
                    rcept_no=receipt,
                    corp_code=corp_code,
                    bsns_year=2025,
                    source_type="audit_report",
                    ordinal=1,
                    title="수익인식",
                    normalized_topic="revenue",
                    reason_text="위험",
                    audit_response_text=response,
                    related_note_references_json="[]",
                    full_body_hash=receipt.ljust(40, "0"),
                    full_body_length=500,
                    source_basis="source_documents.full_body",
                    parser_version="kam.v1",
                    quality_status="full_body",
                    fetched_at=datetime(2026, 3, 1),
                )
            )

    result = compare_peer_audit_procedures(
        "000001",
        year=2025,
        _peer_group={
            "subject": {"corp_code": "00000001", "corp_name": "대상"},
            "peers": [{"corp_code": "00000002", "corp_name": "피어"}],
            "selection_policy": {},
        },
    )

    assert result["data_quality"]["source"] == "kam_items.full_body"
    assert result["subject_method_counts"] == {"inspection": 1}
    assert result["peer_method_counts"] == {"confirmation": 1}
