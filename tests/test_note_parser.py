from kreports.processor.note_parser import extract_note_disclosures


def test_extract_note_disclosures_preserves_metadata_and_tables():
    xml = """
    <DOCUMENT>
      <TITLE>재무제표 주석</TITLE>
      <TITLE>1. 일반사항</TITLE>
      <P>회사 개요</P>
      <TITLE>2. 수익</TITLE>
      <P>회사는 고객과의 계약에서 재화와 용역의 통제가 이전될 때 수익을 인식합니다.</P>
      <TABLE>
        <TR><TH>구분</TH><TH>금액</TH></TR>
        <TR><TD>제품매출</TD><TD>100</TD></TR>
      </TABLE>
      <TITLE>3. 리스</TITLE>
      <P>사용권자산과 리스부채를 인식합니다.</P>
    </DOCUMENT>
    """

    notes = extract_note_disclosures(
        xml,
        corp_code="00126380",
        rcept_no="20250312000000",
        bsns_year=2024,
        reprt_code="11011",
        fs_div="CFS",
        source_file="dart001.xml",
    )

    revenue = next(note for note in notes if note["note_key"] == "revenue_recognition")
    assert revenue["corp_code"] == "00126380"
    assert revenue["rcept_no"] == "20250312000000"
    assert revenue["bsns_year"] == 2024
    assert revenue["reprt_code"] == "11011"
    assert revenue["fs_div"] == "CFS"
    assert revenue["title"] == "2. 수익"
    assert revenue["source_route"] == "document_xml"
    assert revenue["source_file"] == "dart001.xml"
    assert revenue["span"]["start"] < revenue["span"]["end"]
    assert "제품매출" in revenue["tables"][0]["raw_xml"]
    assert "사용권자산" not in revenue["text_excerpt"]


def test_extract_note_disclosures_keeps_child_titles_inside_broad_note():
    xml = """
    <DOCUMENT>
      <TITLE>10. 금융상품</TITLE>
      <P>회사는 금융자산과 금융부채를 보유하고 있습니다.</P>
      <TITLE>(1) 신용위험</TITLE>
      <P>기대신용손실을 측정합니다.</P>
      <TITLE>(2) 유동성위험</TITLE>
      <P>만기분석을 공시합니다.</P>
      <TITLE>11. 특수관계자</TITLE>
      <P>최대주주와 거래가 있습니다.</P>
    </DOCUMENT>
    """

    notes = extract_note_disclosures(xml)

    instruments = next(note for note in notes if note["note_key"] == "financial_instruments")
    assert "기대신용손실" in instruments["text_excerpt"]
    assert "만기분석" in instruments["text_excerpt"]
    assert "최대주주" not in instruments["text_excerpt"]


def test_extract_note_disclosures_treats_note_prefix_headings_as_peer_notes():
    xml = """
    <DOCUMENT>
      <TITLE>재무제표 주석</TITLE>
      <TITLE>주석 10 금융상품</TITLE>
      <P>회사는 금융자산과 금융부채를 보유하고 있습니다.</P>
      <TITLE>(1) 신용위험</TITLE>
      <P>기대신용손실을 측정합니다.</P>
      <TITLE>(2) 유동성위험</TITLE>
      <P>만기분석을 공시합니다.</P>
      <TITLE>주석 11 특수관계자</TITLE>
      <P>최대주주와 거래가 있습니다.</P>
    </DOCUMENT>
    """

    notes = extract_note_disclosures(xml)

    instruments = next(note for note in notes if note["note_key"] == "financial_instruments")
    assert instruments["title"] == "주석 10 금융상품"
    assert "기대신용손실" in instruments["text_excerpt"]
    assert "만기분석" in instruments["text_excerpt"]
    assert "최대주주" not in instruments["text_excerpt"]


def test_extract_note_disclosures_does_not_cut_note_at_note_reference_title():
    xml = """
    <DOCUMENT>
      <TITLE>주석 10 금융상품</TITLE>
      <P>회사는 금융자산과 금융부채를 보유하고 있습니다.</P>
      <TITLE>주석 18을 참고</TITLE>
      <P>이 문장은 참조성 제목으로 분리되었지만 같은 주석 본문입니다.</P>
      <TITLE>(1) 신용위험</TITLE>
      <P>기대신용손실을 측정합니다.</P>
      <TITLE>주석 11 특수관계자</TITLE>
      <P>최대주주와 거래가 있습니다.</P>
    </DOCUMENT>
    """

    notes = extract_note_disclosures(xml)

    instruments = next(note for note in notes if note["note_key"] == "financial_instruments")
    assert "같은 주석 본문" in instruments["text_excerpt"]
    assert "기대신용손실" in instruments["text_excerpt"]
    assert "최대주주" not in instruments["text_excerpt"]
