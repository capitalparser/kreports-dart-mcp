import io
import json
import zipfile
from datetime import date

from kreports.collector import on_demand
from kreports.db.models import Disclosure, SourceDocument
from kreports.mcp.tools import call_tool


def _zip_bytes(name: str, content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, content.encode("utf-8"))
    return buf.getvalue()


def test_on_demand_requires_user_key_without_mentioning_server_key(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "server-key-that-must-not-be-used")

    out = json.loads(call_tool("fetch_disclosure_on_demand", {"rcept_no": "20250101000001"}))

    assert out["error"]
    assert "사용자 DART API key" in out["answer"]
    assert "server-key" not in json.dumps(out, ensure_ascii=False)


def test_on_demand_fetch_uses_user_key_and_caches_document(temp_engine, monkeypatch):
    from kreports.db.engine import get_session

    captured = {}

    class FakeResponse:
        content = _zip_bytes(
            "20250101000001.xml",
            "<DOCUMENT><DOCUMENT-NAME>주요사항보고서</DOCUMENT-NAME><TITLE>주요사항보고서</TITLE><P>신규 시설투자 결정</P></DOCUMENT>",
        )
        headers = {"content-type": "application/zip"}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, params, timeout):
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
