import json
from datetime import datetime

from kreports.db.models import AccountingNoteChapter, Company
from kreports.mcp.dispatch import dispatch_tool
from kreports.mcp.tools import call_tool


def _seed_note(
    *,
    rcept_no: str = "20250312000001",
    body: str,
) -> None:
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(
            corp_code="00000001",
            stock_code="000001",
            corp_name="주석테스트",
            market="KOSPI",
        ))
        session.add(AccountingNoteChapter(
            corp_code="00000001",
            bsns_year=2024,
            fs_div="CFS",
            rcept_no=rcept_no,
            source_type="business_report",
            note_no="2",
            note_title="중요한 회계정책",
            section_type="policy",
            body=body,
            body_hash="fixture-hash",
            body_length=len(body),
            fetched_at=datetime(2025, 3, 12),
        ))


def _search(keyword: str) -> dict:
    return json.loads(call_tool(
        "search_dataset",
        {
            "dataset": "accounting_note_chapters",
            "company": "000001",
            "year": 2024,
            "fs_div": "CFS",
            "keyword": keyword,
            "limit": 5,
        },
    ))


def test_note_search_public_path_surfaces_keyword_evidence_and_coherent_status(
    temp_engine,
):
    body = (
        "재무제표 작성기준 " + ("앞부분 " * 260)
        + "2.8 재고자산 재고자산의 단위원가는 평균법으로 결정합니다. "
        "재고자산은 취득원가와 순실현가능가치 중 낮은 금액으로 측정합니다."
    )
    _seed_note(body=body)

    out = _search("재고자산")
    envelope = dispatch_tool(
        "search_dataset",
        {
            "dataset": "accounting_note_chapters",
            "company": "000001",
            "year": 2024,
            "fs_div": "CFS",
            "keyword": "재고자산",
            "limit": 5,
        },
    )

    assert out["data_quality"]["status"] == "usable"
    assert envelope.verdict == "usable"
    assert out["answer_pack"]["status"] == "usable"
    assert out["answer_pack"]["summary"]["status"] == "usable"
    assert "평균법" in out["answer"]
    assert "순실현가능가치" in out["answer"]
    assert "20250312000001" in out["answer"]
    assert (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250312000001"
        in out["answer"]
    )
    evidence_table = next(
        table
        for table in out["answer_pack"]["tables"]
        if table["id"] == "accounting_note_evidence"
    )
    assert evidence_table["rows"]
    assert "재고자산" in evidence_table["rows"][0]["matched_excerpt"]


def test_note_search_missing_means_cache_absence_not_filing_absence(temp_engine):
    _seed_note(body="중요한 회계정책에는 재고자산 측정 정책만 포함합니다.")

    out = _search("우발")

    assert out["data_quality"]["status"] == "missing"
    assert out["answer_pack"]["status"] == "missing"
    assert "로컬 캐시" in out["answer"]
    assert "원 공시 부재를 뜻하지 않습니다" in out["answer"]
    assert "우발사항이 없습니다" not in out["answer"]
    assert "우발사항이 공시되지 않았습니다" not in out["answer"]


def test_note_search_with_uncitable_match_is_not_usable(temp_engine):
    _seed_note(
        rcept_no="invalid-receipt",
        body="2.8 재고자산은 평균법과 순실현가능가치 기준을 적용합니다.",
    )

    out = _search("재고자산")
    envelope = dispatch_tool(
        "search_dataset",
        {
            "dataset": "accounting_note_chapters",
            "company": "000001",
            "year": 2024,
            "fs_div": "CFS",
            "keyword": "재고자산",
            "limit": 5,
        },
    )

    assert out["data_quality"]["status"] == "limited"
    assert envelope.verdict == "limited"
    assert out["answer_pack"]["status"] == "limited"
    assert "연결 가능한 공시" in out["answer"]
