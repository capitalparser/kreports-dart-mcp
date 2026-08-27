import io
import zipfile

import pytest

from kreports.collector import fetcher


class _FakeResponse:
    def __init__(self, content: bytes, content_type: str = "application/xml"):
        self.content = content
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"

    def raise_for_status(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_bytes(self):
        yield self.content


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, *_args, **_kwargs):
        return self.response


class _RecordingClient(_FakeClient):
    def __init__(self, response: _FakeResponse):
        super().__init__(response)
        self.calls: list[tuple[tuple, dict]] = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response

    def stream(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


def test_fetch_document_xml_accepts_raw_document_xml(monkeypatch):
    raw = """
    <DOCUMENT>
      <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
      <P>원문입니다.</P>
    </DOCUMENT>
    """.encode()
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(fetcher, "_get_client", lambda: _FakeClient(_FakeResponse(raw)))

    out = fetcher.fetch_document_xml("20250331000001")

    assert out is not None
    assert "사업보고서" in out


def test_fetch_document_zip_files_accepts_raw_document_xml(monkeypatch):
    raw = """
    <DOCUMENT>
      <DOCUMENT-NAME>사업보고서</DOCUMENT-NAME>
      <P>원문입니다.</P>
    </DOCUMENT>
    """.encode()
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(fetcher, "_get_client", lambda: _FakeClient(_FakeResponse(raw)))

    out = fetcher.fetch_document_zip_files("20250331000001")

    assert list(out) == ["20250331000001.xml"]
    assert "원문입니다" in out["20250331000001.xml"]


def test_fetch_document_zip_assets_retains_exact_response_container_and_members(monkeypatch):
    """The archive workflow needs both original ZIP evidence and parseable entries."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("main.xml", b"<DOCUMENT><P>\x80\xff</P></DOCUMENT>")
    original_zip = buffer.getvalue()
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(fetcher, "_get_client", lambda: _FakeClient(_FakeResponse(original_zip, "application/zip")))

    assets = fetcher.fetch_document_zip_asset_bytes("20250331000001")

    assert assets.container_bytes == original_zip
    assert assets["main.xml"] == b"<DOCUMENT><P>\x80\xff</P></DOCUMENT>"


def test_fetch_document_xml_raises_on_dart_limit_xml(monkeypatch):
    raw = b"<result><status>020</status><message>limit exceeded</message></result>"
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(fetcher, "_get_client", lambda: _FakeClient(_FakeResponse(raw)))

    with pytest.raises(fetcher.DartApiLimitExceeded):
        fetcher.fetch_document_xml("20250331000001")
    with pytest.raises(fetcher.DartApiLimitExceeded):
        fetcher.fetch_document_zip_files("20250331000001")


def test_fetch_document_xml_rejects_non_limit_dart_error_xml(monkeypatch, caplog):
    raw = b"<result><status>013</status><message>no data</message></result>"
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(fetcher, "_get_client", lambda: _FakeClient(_FakeResponse(raw)))

    assert fetcher.fetch_document_xml("20250331000001") is None
    assert fetcher.fetch_document_zip_files("20250331000001") == {}
    assert "status=013" in caplog.text
    assert "no data" in caplog.text


def test_fetch_document_zip_files_logs_non_limit_dart_error_xml(monkeypatch, caplog):
    raw = b"<result><status>999</status><message>other error</message></result>"
    monkeypatch.setattr(fetcher.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(fetcher, "_get_client", lambda: _FakeClient(_FakeResponse(raw)))

    assert fetcher.fetch_document_zip_files("20250331000001") == {}
    assert "status=999" in caplog.text
    assert "other error" in caplog.text


@pytest.mark.parametrize(
    ("content", "content_type"),
    (
        pytest.param(b"", "application/pdf", id="empty"),
        pytest.param(b"<html>not a PDF</html>", "application/pdf", id="non_pdf"),
        pytest.param(b"%PDF-1.7", "text/html", id="wrong_content_type"),
        pytest.param(
            b"%PDF-1.7" + b"x" * (20 * 1024 * 1024),
            "application/pdf",
            id="oversize",
        ),
    ),
)
def test_fetch_audit_report_pdf_rejects_empty_non_pdf_or_oversize_payloads(
    monkeypatch,
    content,
    content_type,
):
    """Catch an official PDF fallback accepting an unsafe or non-PDF response."""
    client = _RecordingClient(_FakeResponse(content, content_type=content_type))
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)

    assert fetcher.fetch_audit_report_pdf("20260428000679", "11351227") is None


def test_fetch_audit_report_pdf_uses_official_endpoint_and_safe_headers(monkeypatch):
    """Catch a PDF fallback that omits DART's browser/referrer request context."""
    client = _RecordingClient(_FakeResponse(b"%PDF-1.7\nbody", "application/pdf"))
    monkeypatch.setattr(fetcher, "_get_client", lambda: client)

    assert fetcher.fetch_audit_report_pdf("20260428000679", "11351227") == b"%PDF-1.7\nbody"
    assert client.calls
    args, kwargs = client.calls[0]
    assert args == ("GET", "https://dart.fss.or.kr/pdf/download/pdf.do")
    assert kwargs["params"] == {"rcp_no": "20260428000679", "dcm_no": "11351227"}
    assert kwargs["headers"]["Referer"].endswith("main.do?rcpNo=20260428000679")
    assert "Mozilla" in kwargs["headers"]["User-Agent"]


def test_request_budget_counts_each_viewer_retry_and_pdf_fallback_attempt(monkeypatch):
    """A campaign limit must cap physical HTTP attempts, not logical fetches."""
    attempts: list[str] = []

    class FlakyViewerThenPdf:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            attempts.append("viewer")
            raise RuntimeError("temporary viewer failure")

        def stream(self, *_args, **_kwargs):
            attempts.append("pdf")
            return _FakeResponse(b"%PDF-1.7\nbody", "application/pdf")

    monkeypatch.setattr(fetcher, "_get_client", FlakyViewerThenPdf)
    monkeypatch.setattr(fetcher.time, "sleep", lambda _seconds: None)

    with fetcher.request_budget(4) as budget:
        assert fetcher.fetch_viewer_bytes("20260428000679", "11351227") is None
        assert fetcher.fetch_audit_report_pdf("20260428000679", "11351227") == b"%PDF-1.7\nbody"

    assert attempts == ["viewer", "viewer", "viewer", "pdf"]
    assert budget.used_calls == 4

    attempts.clear()
    with fetcher.request_budget(3) as budget:
        assert fetcher.fetch_viewer_bytes("20260428000679", "11351227") is None
        with pytest.raises(fetcher.DartRequestBudgetExceeded):
            fetcher.fetch_audit_report_pdf("20260428000679", "11351227")
    assert attempts == ["viewer", "viewer", "viewer"]
    assert budget.used_calls == 3


def test_fetch_audit_report_pdf_stops_streaming_at_the_byte_cap(monkeypatch):
    """Catch a PDF fallback buffering bytes after its configured limit is crossed."""
    consumed: list[bytes] = []
    closed: list[bool] = []

    class Response:
        headers = {"content-type": "application/pdf"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            closed.append(True)
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            for chunk in (b"%PDF", b"-123", b"must-not-be-read"):
                consumed.append(chunk)
                yield chunk

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            assert method == "GET"
            assert url.endswith("/pdf/download/pdf.do")
            return Response()

    monkeypatch.setattr(fetcher, "MAX_AUDIT_REPORT_PDF_BYTES", 7)
    monkeypatch.setattr(fetcher, "_get_client", lambda: Client())

    assert fetcher.fetch_audit_report_pdf("20260428000679", "11351227") is None
    assert consumed == [b"%PDF", b"-123"]
    assert closed == [True]


def test_fetch_audit_report_pdf_rejects_declared_oversize_before_reading(monkeypatch):
    """A Content-Length cap must close the stream without consuming its body."""
    closed: list[bool] = []

    class Response:
        headers = {
            "content-type": "application/pdf",
            "content-length": str(8),
        }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            closed.append(True)
            return None

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            raise AssertionError("oversize body must not be read")

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(fetcher, "MAX_AUDIT_REPORT_PDF_BYTES", 7)
    monkeypatch.setattr(fetcher, "_get_client", lambda: Client())

    assert fetcher.fetch_audit_report_pdf("20260428000679", "11351227") is None
    assert closed == [True]
