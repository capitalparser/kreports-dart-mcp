import io
import json
import threading
import zipfile
from datetime import date

import pytest

from kreports.collector import on_demand
from kreports.db.models import Disclosure, SourceDocument
from kreports.mcp.tools import call_tool


def _zip_bytes(name: str, content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, content.encode("utf-8"))
    return buf.getvalue()


def test_on_demand_requires_user_key_without_mentioning_server_key(monkeypatch):
    import kreports.mcp.dispatch as dispatch

    monkeypatch.setenv("DART_API_KEY", "server-key-that-must-not-be-used")

    def unexpected_dependency(*args, **kwargs):
        del args, kwargs
        raise AssertionError("no-key preflight must not access a dependency")

    monkeypatch.setattr(on_demand, "_cached_source", unexpected_dependency)
    monkeypatch.setattr(on_demand, "_fetch_document_xml_with_user_key", unexpected_dependency)
    monkeypatch.setattr(dispatch, "release_context", unexpected_dependency)

    out = json.loads(call_tool("fetch_disclosure_on_demand", {"rcept_no": "20250101000001"}))

    assert out["error"]
    assert "사용자 DART API key" in out["answer"]
    for section in ("판정:", "업무 결론:", "데이터 한계:", "추가 확인사항:"):
        assert section in out["answer"]
    serialized = json.dumps(out, ensure_ascii=False)
    assert "server-key" not in serialized
    assert "DART_API_KEY" not in serialized
    for forbidden in (
        "배포 준비 상태",
        "missing_table:",
        "manifest_available",
        "snapshot_version",
        "required_failure",
        "degraded_feature",
        "schema_version",
        "_meta",
    ):
        assert forbidden not in serialized


def test_on_demand_fetch_uses_user_key_and_caches_document(temp_engine, monkeypatch):
    from kreports.db.engine import get_session

    captured = {}

    class FakeResponse:
        content = _zip_bytes(
            "20250101000001.xml",
            "<DOCUMENT><DOCUMENT-NAME>주요사항보고서</DOCUMENT-NAME><TITLE>주요사항보고서</TITLE><P>신규 시설투자 결정</P></DOCUMENT>",
        )
        headers = {"content-type": "application/zip"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield self.content

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def stream(self, method, url, params, timeout):
            assert method == "GET"
            captured["key"] = params["crtfc_key"]
            captured["rcept_no"] = params["rcept_no"]
            return FakeResponse()

    monkeypatch.setattr(on_demand.httpx, "Client", lambda timeout=60.0: FakeClient())
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "file")
    monkeypatch.setenv("RAW_STORAGE_KEEP_INLINE", "false")

    with get_session() as session:
        session.add(Disclosure(
            rcept_no="20250101000001",
            corp_code="00126380",
            corp_name="삼성전자",
            disc_date=date(2025, 1, 1),
            disc_type="B",
            report_nm="주요사항보고서",
        ))

    out = json.loads(call_tool(
        "fetch_disclosure_on_demand",
        {
            "rcept_no": "20250101000001",
            "user_dart_api_key": "user-key",
            "cache_policy": "refresh",
        },
    ))

    assert captured == {"key": "user-key", "rcept_no": "20250101000001"}
    assert out["data_quality"]["source"] == "user_keyed_dart_fetch"
    assert "user-key" not in json.dumps(out, ensure_ascii=False)
    assert out["answer"].startswith("판정:")

    with get_session() as session:
        cached = session.query(SourceDocument).filter_by(rcept_no="20250101000001").one()
        assert cached.corp_code == "00126380"
        assert cached.storage_status == "externalized"
        assert cached.raw_content == ""


def test_on_demand_transport_error_never_reflects_caller_key(temp_engine, monkeypatch, caplog):
    request = on_demand.httpx.Request(
        "GET",
        "https://opendart.fss.or.kr/api/document.xml?crtfc_key=caller-key&rcept_no=20250101000001",
    )
    monkeypatch.setattr(
        on_demand,
        "_fetch_document_xml_with_user_key",
        lambda *_: (_ for _ in ()).throw(on_demand.httpx.ConnectError("caller-key", request=request)),
    )

    out = on_demand.fetch_disclosure_on_demand(
        rcept_no="20250101000001",
        user_dart_api_key="caller-key",
        corp_code="00126380",
        year=2025,
        cache_policy="refresh",
    )

    assert out["data_quality"]["status"] == "error"
    assert "caller-key" not in str(out)
    assert "caller-key" not in caplog.text


def test_on_demand_cache_first_does_not_call_dart(temp_engine, monkeypatch):
    from kreports.db.engine import get_session

    def fail_client(*args, **kwargs):
        raise AssertionError("DART should not be called on cache hit")

    monkeypatch.setattr(on_demand.httpx, "Client", fail_client)
    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250101000001",
            corp_code="00126380",
            bsns_year=2025,
            source_type="event_disclosure",
            report_nm="주요사항보고서",
            content_type="xml",
            raw_content="<DOCUMENT><TITLE>주요사항보고서</TITLE><P>cached</P></DOCUMENT>",
            doc_hash="x",
        ))

    out = json.loads(call_tool(
        "fetch_disclosure_on_demand",
        {
            "rcept_no": "20250101000001",
            "user_dart_api_key": "user-key",
            "cache_policy": "cache_first",
        },
    ))

    assert out["data_quality"]["source"] == "source_documents_cache"
    assert out["cached"] is True


def test_on_demand_cache_first_reads_externalized_raw_document(temp_engine, tmp_path, monkeypatch):
    from kreports.db.engine import get_session
    from kreports.storage.raw_documents import RawDocumentStore, sha1_text

    def fail_client(*args, **kwargs):
        raise AssertionError("DART should not be called on cache hit")

    store = RawDocumentStore(base_dir=tmp_path)
    monkeypatch.setattr(on_demand, "RawDocumentStore", lambda: store)
    monkeypatch.setattr(on_demand.httpx, "Client", fail_client)
    raw_content = "<DOCUMENT><TITLE>주요사항보고서</TITLE><P>external cached</P></DOCUMENT>"
    saved = store.write(
        corp_code="00126380",
        bsns_year=2025,
        source_type="event_disclosure",
        rcept_no="20250101000002",
        content_type="xml",
        content=raw_content,
    )
    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250101000002",
            corp_code="00126380",
            bsns_year=2025,
            source_type="event_disclosure",
            report_nm="주요사항보고서",
            content_type="xml",
            raw_content="",
            doc_hash=sha1_text(raw_content),
            storage_uri=saved.storage_uri,
            content_length=saved.content_length,
            compressed_length=saved.compressed_length,
            storage_status="externalized",
        ))

    out = json.loads(call_tool(
        "fetch_disclosure_on_demand",
        {
            "rcept_no": "20250101000002",
            "user_dart_api_key": "user-key",
            "cache_policy": "cache_first",
        },
    ))

    assert out["data_quality"]["source"] == "source_documents_cache"
    assert out["cached"] is True
    assert out["body_length"] == len(raw_content)


def test_on_demand_fetch_fails_closed_when_process_slots_are_busy(monkeypatch):
    """Catches a busy user-keyed fetch still reaching the DART network."""
    semaphore = threading.BoundedSemaphore(1)
    assert semaphore.acquire(blocking=False)
    monkeypatch.setattr(on_demand, "_ON_DEMAND_FETCH_SEMAPHORE", semaphore)
    monkeypatch.setattr(on_demand, "_cached_source", lambda _rcept_no: None)
    monkeypatch.setattr(on_demand, "_disclosure_meta", lambda *_args, **_kwargs: {
        "corp_code": "00126380",
        "bsns_year": 2025,
        "report_nm": "주요사항보고서",
    })
    monkeypatch.setattr(
        on_demand,
        "_fetch_document_xml_with_user_key",
        lambda *_args: (_ for _ in ()).throw(AssertionError("busy fetch must not call DART")),
    )

    try:
        out = on_demand.fetch_disclosure_on_demand(
            rcept_no="20250101000001",
            user_dart_api_key="user-key",
            cache_policy="refresh",
        )
    finally:
        semaphore.release()

    assert out["error"] == "on-demand fetch is busy"
    assert out["data_quality"] == {
        "status": "error",
        "source": "user_keyed_dart_fetch",
        "limitations": ["On-demand fetch capacity is temporarily unavailable."],
    }


def test_on_demand_rejects_response_larger_than_download_limit(monkeypatch):
    """Catches a full unbounded document response being accumulated in memory."""
    monkeypatch.setattr(on_demand, "MAX_DOCUMENT_RESPONSE_BYTES", 8)

    class StreamingResponse:
        headers = {}

        def iter_bytes(self):
            yield b"1234"
            yield b"56789"

    with pytest.raises(on_demand.OnDemandPayloadLimitError, match="response exceeds"):
        on_demand._read_limited_response(StreamingResponse())


def test_on_demand_rejects_zip_with_too_many_members(monkeypatch):
    """Catches ZIP member-count bombs before any member is decompressed."""
    monkeypatch.setattr(on_demand, "MAX_DOCUMENT_ZIP_MEMBERS", 1)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("20250101000001.xml", b"<DOCUMENT/>")
        zf.writestr("other.xml", b"<DOCUMENT/>")
    payload = buffer.getvalue()

    with pytest.raises(on_demand.OnDemandPayloadLimitError, match="too many members"):
        on_demand._document_xml_from_payload(payload, "20250101000001")


def test_on_demand_rejects_zip_member_with_excessive_compression_ratio(monkeypatch):
    """Catches a highly compressed XML member before it is read into memory."""
    monkeypatch.setattr(on_demand, "MAX_DOCUMENT_ZIP_COMPRESSION_RATIO", 2)
    payload = _zip_bytes("20250101000001.xml", "A" * 20_000)

    with pytest.raises(on_demand.OnDemandPayloadLimitError, match="compression ratio"):
        on_demand._document_xml_from_payload(payload, "20250101000001")
