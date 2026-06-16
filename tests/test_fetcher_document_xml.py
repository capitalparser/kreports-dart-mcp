import pytest

from kreports.collector import fetcher


class _FakeResponse:
    def __init__(self, content: bytes, content_type: str = "application/xml"):
        self.content = content
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, *_args, **_kwargs):
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
