from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy.orm import Session

from kreports.db.models import AccountingNoteChapter, Company


def _seed_long_note(
    temp_engine,
    *,
    external: bool = False,
) -> tuple[str, str]:
    from kreports.analysis.note_evidence import build_note_ref

    paragraphs = [
        (
            "회사는 Alpha SPC의 프로젝트금융 대출약정과 관련하여 "
            "자금보충약정을 제공하고 있습니다. 상환재원이 부족한 경우 "
            "부족액을 보충하며 약정 한도는 3,000억원이고 만기는 2032년입니다."
        ),
        (
            "당기말 현재 실행된 자금보충액은 없으며 관련 지급보증과 담보 "
            "제공 현황은 다음과 같습니다."
        ),
        "기타 약정 설명 " + ("가" * 8_500),
        "추가 공시 설명 " + ("나" * 8_500),
    ]
    full_text = "\n\n".join(paragraphs)
    cached = (
        full_text[:1_000]
        if external
        else full_text
    )
    with Session(temp_engine) as session:
        session.add(
            Company(
                corp_code="00000001",
                stock_code="000001",
                corp_name="Alpha",
                market="KOSPI",
                induty_code="35110",
            )
        )
        row = AccountingNoteChapter(
            corp_code="00000001",
            bsns_year=2024,
            fs_div="CFS",
            rcept_no="20250318000001",
            source_type="business_report",
            note_no="31",
            note_title="자금보충약정",
            section_type="other_note",
            body=cached,
            body_length=len(cached),
            full_text_uri=(
                "file:///private/note-31.txt.gz"
                if external
                else None
            ),
            full_text_hash=(
                hashlib.sha1(full_text.encode()).hexdigest()
                if external
                else None
            ),
            full_text_length=len(full_text),
            full_text_storage_status=(
                "externalized" if external else None
            ),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return build_note_ref(row), full_text


def test_note_resource_templates_are_advertised_with_existing_resources():
    from kreports.mcp.resources import list_resource_templates

    templates = {
        descriptor.uri_template
        for descriptor in list_resource_templates()
    }

    assert {
        "kreports://company/{corp_code}/{year}",
        "kreports://evidence/{rcept_no}",
        "kreports://note/{note_ref}",
        "kreports://note/{note_ref}/paragraph",
        "kreports://note/{note_ref}/page/{page}",
        "kreports://visualization/{digest}",
    }.issubset(templates)


def test_note_summary_paragraph_and_full_pages_share_one_reference(
    temp_engine,
):
    from kreports.mcp.resources import read_resource

    note_ref, full_text = _seed_long_note(temp_engine)

    summary = read_resource(f"kreports://note/{note_ref}")
    paragraph = read_resource(
        f"kreports://note/{note_ref}/paragraph"
    )
    first = read_resource(
        f"kreports://note/{note_ref}/page/1"
    )
    second = read_resource(
        f"kreports://note/{note_ref}/page/2"
    )
    last = read_resource(
        f"kreports://note/{note_ref}/page/3"
    )

    assert summary["note_ref"] == note_ref
    assert paragraph["note_ref"] == note_ref
    assert first["note_ref"] == note_ref
    assert summary["company"]["corp_name"] == "Alpha"
    assert summary["fs_div"] == "CFS"
    assert summary["disclosure_profile"]["level"] == "detailed"
    assert "자금보충약정" in paragraph["text"]
    assert paragraph["view"] == "paragraph"
    assert first["view"] == "page"
    assert first["page"]["number"] == 1
    assert first["page"]["count"] == 3
    assert first["page"]["next_uri"].endswith("/page/2")
    assert second["page"]["previous_uri"].endswith("/page/1")
    assert second["page"]["next_uri"].endswith("/page/3")
    assert last["page"]["has_next"] is False
    assert last["page"]["end_character"] == len(full_text)
    assert summary["source_url"].endswith("20250318000001")
    assert "full_text_uri" not in json.dumps(summary)
    assert "/private/" not in json.dumps(summary)


def test_external_full_note_is_read_only_when_resource_is_opened(
    temp_engine,
    monkeypatch,
):
    import kreports.analysis.note_evidence as note_evidence
    from kreports.mcp.resources import read_resource

    note_ref, full_text = _seed_long_note(
        temp_engine,
        external=True,
    )
    calls: list[tuple[str, str | None]] = []

    class FakeBlobStore:
        def read(self, uri, *, expected_hash=None):
            calls.append((uri, expected_hash))
            assert uri == "file:///private/note-31.txt.gz"
            return full_text

    monkeypatch.setattr(
        note_evidence,
        "EvidenceBlobStore",
        FakeBlobStore,
    )

    first = read_resource(
        f"kreports://note/{note_ref}/page/1"
    )

    assert calls
    assert first["text_status"]["source_basis"] == (
        "external_full_text"
    )
    assert first["text_status"]["completeness"] == "complete"
    assert first["page"]["count"] == 3
    assert "자금보충약정" in first["text"]


def test_note_resource_rejects_malformed_stale_and_out_of_range_references(
    temp_engine,
):
    from kreports.mcp.resources import (
        ResourceRequestError,
        read_resource,
    )

    note_ref, _full_text = _seed_long_note(temp_engine)

    for uri in (
        "kreports://note/not-a-note-ref",
        f"kreports://note/{note_ref}/paragraph/",
        f"kreports://note/{note_ref}/page/0",
        f"kreports://note/{note_ref}/page/0001",
        f"kreports://note/{note_ref}?path=/tmp/private",
    ):
        with pytest.raises(ResourceRequestError):
            read_resource(uri)

    with pytest.raises(
        ResourceRequestError,
        match="out_of_range",
    ):
        read_resource(
            f"kreports://note/{note_ref}/page/99"
        )
