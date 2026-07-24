import pytest

from kreports.analysis.evidence import dart_filing_url, evidence_reference_fields, parent_rcept_no, source_line


def test_dart_filing_url_uses_plain_receipt_number():
    assert dart_filing_url("20260316001520") == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260316001520"


def test_parent_rcept_no_extracts_from_attached_document_id():
    assert parent_rcept_no("20260316001520_20260316001520_00761_xml") == "20260316001520"


def test_source_line_uses_parent_receipt_for_dart_link():
    source = {
        "corp_name": "SK이터닉스",
        "report_nm": "사업보고서 (2025.12)",
        "section_title": "II. 사업의 내용",
        "rcept_no": "20260316001520_20260316001520_00761_xml",
    }

    line = source_line(source)

    assert "출처: SK이터닉스 사업보고서 (2025.12), II. 사업의 내용, 접수번호 20260316001520" in line
    assert "공시 링크: https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260316001520" in line
    assert "첨부문서 식별자: 20260316001520_20260316001520_00761_xml" in line


@pytest.mark.parametrize("unsafe_url", [
    "javascript:alert(1)",
    "data:text/html,unsafe",
    "file:///etc/passwd",
    "ftp://example.com/source",
    "//example.com/protocol-relative",
    "https:///missing-host",
    "https://user:password@example.com/source",
    "http://localhost/source",
    "http://127.0.0.1/source",
    "http://[::1]/source",
    "http://127.1/source",
    "http://127.0.1/source",
    "http://2130706433/source",
    "http://0x7f000001/source",
    "http://0177.0.0.1/source",
    "http://192.168.1/source",
    "http://%31%32%37.0.0.1/source",
    "http://100.64.0.1/source",
    "http://224.0.0.1/source",
    "http://239.255.255.250/source",
    "http://[ff02::1]/source",
    "http://[ff05::2]/source",
])
def test_evidence_reference_rejects_unsafe_explicit_urls(unsafe_url):
    assert evidence_reference_fields({"source_url": unsafe_url}) is None


def test_evidence_reference_accepts_absolute_public_http_url():
    assert evidence_reference_fields({"source_url": "https://example.com/source"})["source_url"] == "https://example.com/source"


@pytest.mark.parametrize("public_url", [
    "https://8.8.8.8/path",
    "https://[2606:4700:4700::1111]/path",
])
def test_evidence_reference_accepts_global_ip_urls(public_url):
    assert evidence_reference_fields({"source_url": public_url})["source_url"] == public_url
