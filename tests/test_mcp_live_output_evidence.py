"""Regression contracts derived from immutable live MCP chatbot rehearsal."""
from __future__ import annotations

def test_audit_fee_dataset_rows_without_receipts_are_limited_but_still_tabular(
    monkeypatch,
):
    """Catches uncited audit-fee rows being labelled usable or hidden from the table."""
    from kreports.mcp.handlers import search as search_handler
    from kreports.mcp.dispatch import dispatch_tool

    monkeypatch.setattr(
        search_handler,
        "search_dataset",
        lambda **_kwargs: {
            "query": {
                "dataset": "audit_fees",
                "company": "005930",
                "year": 2024,
            },
            "subject": {
                "corp_code": "00126380",
                "stock_code": "005930",
                "corp_name": "삼성전자",
            },
            "companies": [{
                "corp_code": "00126380",
                "stock_code": "005930",
                "corp_name": "삼성전자",
                "records": [{
                    "year": 2024,
                    "auditor_nm": "삼정회계법인",
                    "audit_fee_m": 7800,
                    "audit_hours": 76830,
                }],
                "record_count": 1,
            }],
            "total_companies": 1,
            "total_records": 1,
            "data_quality": {
                "status": "usable",
                "source": "audit_fees",
                "limitations": [],
            },
        },
    )

    out = dispatch_tool(
        "search_dataset",
        {
            "dataset": "audit_fees",
            "company": "005930",
            "year": 2024,
            "limit": 20,
        },
    ).model_dump(mode="json")

    assert out["verdict"] == "limited"
    assert out["data_quality"]["status"] == "limited"
    assert any(
        "접수번호" in limitation
        for limitation in out["data_quality"]["limitations"]
    )
    table = next(
        table
        for table in out["answer_pack"]["tables"]
        if table["id"] == "search_results"
    )
    assert table["rows"] == [{
        "corp_name": "삼성전자",
        "year": 2024,
        "auditor_nm": "삼정회계법인",
        "audit_fee_m": 7800,
        "audit_hours": 76830,
        "rcept_no": None,
    }]
    assert out["answer_pack"]["sources"] == []
    assert "감사보수(백만원)" in out["answer"]


def test_policy_change_candidates_expose_receipt_evidence_and_rows(monkeypatch):
    """Catches cited policy-change candidates collapsing into an empty availability pack."""
    from kreports.mcp.handlers import auditor as auditor_handler
    from kreports.mcp.dispatch import dispatch_tool

    monkeypatch.setattr(
        auditor_handler,
        "resolve_company",
        lambda _company: "00126380",
    )
    monkeypatch.setattr(
        auditor_handler,
        "get_accounting_policy_changes",
        lambda **_kwargs: {
            "subject": {
                "corp_code": "00126380",
                "stock_code": "005930",
                "corp_name": "삼성전자",
            },
            "start_year": 2022,
            "end_year": 2024,
            "fs_div": "CFS",
            "change_count": 1,
            "changes": [{
                "year": 2024,
                "fs_div": "CFS",
                "rcept_no": "20250311001085",
                "note_no": "2",
                "note_title": "재무제표 작성기준",
                "section_type": "basis",
                "change_type": "changed",
                "similarity_to_previous": 0.5401,
                "body_excerpt": "회사는 주요 제ㆍ개정 기준서를 신규 적용하였습니다.",
            }],
            "changed_items": [{
                "year": 2024,
                "fs_div": "CFS",
                "rcept_no": "20250311001085",
                "note_no": "2",
                "note_title": "재무제표 작성기준",
                "section_type": "basis",
                "change_type": "changed",
                "similarity_to_previous": 0.5401,
                "body_excerpt": "회사는 주요 제ㆍ개정 기준서를 신규 적용하였습니다.",
            }],
            "data_quality": {
                "status": "usable",
                "source": "accounting_note_chapters",
                "limitations": [
                    "텍스트 차이 후보이며 회계정책 변경 결론이 아닙니다.",
                ],
            },
        },
    )

    out = dispatch_tool(
        "get_accounting_policy_changes",
        {
            "company": "005930",
            "start_year": 2022,
            "end_year": 2024,
            "fs_div": "CFS",
        },
    ).model_dump(mode="json")

    assert out["verdict"] == "usable"
    assert [item["rcept_no"] for item in out["evidence"]] == [
        "20250311001085",
    ]
    table = next(
        table
        for table in out["answer_pack"]["tables"]
        if table["id"] == "accounting_policy_changes"
    )
    assert table["rows"] == [{
        "year": 2024,
        "fs_div": "CFS",
        "note_no": "2",
        "note_title": "재무제표 작성기준",
        "section_type": "basis",
        "change_type": "changed",
        "similarity_to_previous": 0.5401,
        "rcept_no": "20250311001085",
    }]
    assert out["answer_pack"]["sources"][0]["rcept_no"] == "20250311001085"
    assert "20250311001085" in out["answer"]
    assert "회계정책 텍스트 변경 후보" in out["answer"]


def test_audit_hours_proxy_carries_child_receipts_into_public_pack(monkeypatch):
    """Catches cited child inputs being reduced to source-free proxy metrics."""
    from kreports.analysis import peer_benchmarks
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.contracts import build_answer_envelope

    monkeypatch.setattr(
        peer_benchmarks,
        "select_peer_group",
        lambda **_kwargs: {
            "subject": {
                "corp_code": "00126380",
                "corp_name": "삼성전자",
            },
            "peers": [{"corp_code": "00000001"}],
            "selection_policy": {"fs_div_used": "CFS"},
        },
    )
    monkeypatch.setattr(
        peer_benchmarks,
        "compare_peer_audit_fees",
        lambda **_kwargs: {
            "subject": {
                "corp_code": "00126380",
                "corp_name": "삼성전자",
            },
            "peer_count": 1,
            "subject_metrics": {
                "audit_fee_m": 7800,
                "audit_hours": 76830,
                "total_assets": 514531948000000,
            },
            "subject_scale_history": [{
                "year": 2024,
                "audit_source_rcept_no": "20250311001085",
                "financial_source": {
                    "rcept_no": "20250311001085",
                    "section_title": "연결재무제표",
                },
            }],
            "benchmarks": {
                "audit_hours": {"subject_percentile": 80},
            },
            "data_quality": {"status": "limited"},
            "selection_policy": {"fs_div_used": "CFS"},
        },
    )
    monkeypatch.setattr(
        peer_benchmarks,
        "compare_peer_risk_profile",
        lambda **_kwargs: {
            "subject_metrics": {
                "beneish_m_score": -2.9,
                "op_cf_divergence_flag": 0,
                "going_concern_flag": 0,
            },
            "data_quality": {"status": "usable"},
        },
    )

    result = peer_benchmarks.estimate_audit_hours_proxy(
        "00126380",
        year=2024,
        peer_limit=1,
    )
    envelope = build_answer_envelope(
        "estimate_audit_hours_proxy",
        result,
    )
    pack = build_answer_pack(
        "estimate_audit_hours_proxy",
        result,
    )

    assert [item.rcept_no for item in envelope.evidence] == [
        "20250311001085",
    ]
    assert pack is not None
    assert [item["rcept_no"] for item in pack["sources"]] == [
        "20250311001085",
    ]
    table = next(
        item for item in pack["tables"]
        if item["id"] == "audit_hours_proxy_inputs"
    )
    assert table["rows"][0]["audit_source_rcept_no"] == "20250311001085"
    assert (
        table["rows"][0]["financial_source_rcept_no"]
        == "20250311001085"
    )
