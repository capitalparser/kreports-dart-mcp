from kreports.analysis.evidence import dart_filing_url, parent_rcept_no, source_line


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
