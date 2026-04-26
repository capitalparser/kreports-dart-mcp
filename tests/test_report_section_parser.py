from kreports.processor.report_section_parser import extract_report_sections


def test_business_description_keeps_child_titles_until_next_peer_main_section():
    xml = """
    <DOCUMENT>
      <TITLE>II. 사업의 내용</TITLE>
      <P>회사는 소프트웨어 서비스를 제공합니다.</P>
      <TITLE>1. 사업의 개요</TITLE>
      <P>구독형 플랫폼과 구축형 솔루션을 판매합니다.</P>
      <TITLE>2. 주요 제품 및 서비스</TITLE>
      <P>주요 제품은 AI 분석 서비스이며 수주잔고를 관리합니다.</P>
      <TITLE>III. 재무에 관한 사항</TITLE>
      <P>이 문장은 사업의 내용 섹션에 포함되면 안 됩니다.</P>
    </DOCUMENT>
    """

    sections = extract_report_sections(xml)

    assert "business_description" in sections
    body = sections["business_description"]["body_text"]
    assert "구독형 플랫폼" in body
    assert "AI 분석 서비스" in body
    assert "포함되면 안 됩니다" not in body


def test_section_title_variants_are_recognized():
    xml = """
    <DOCUMENT>
      <TITLE>Ⅰ. 회사의 개요</TITLE>
      <P>회사 개요 본문</P>
      <TITLE>Ⅱ. 사업의 내용</TITLE>
      <P>사업 본문</P>
      <TITLE>1. 주요 제품 및 서비스</TITLE>
      <P>제품과 서비스 본문</P>
      <TITLE>2. 시장위험과 위험관리</TITLE>
      <P>환율과 유동성 위험 본문</P>
      <TITLE>3. 연구개발비용</TITLE>
      <P>연구개발비 / 매출액 비율 7.2%</P>
      <TITLE>4. 경영상의 주요계약</TITLE>
      <P>계약상대방 ABC 공급계약</P>
      <TITLE>Ⅲ. 재무에 관한 사항</TITLE>
      <P>재무 본문</P>
    </DOCUMENT>
    """

    sections = extract_report_sections(xml)

    assert sections["business_description"]["title"] == "Ⅱ. 사업의 내용"
    assert sections["risk_management"]["title"] == "2. 시장위험과 위험관리"
    assert sections["rd_activities"]["title"] == "3. 연구개발비용"
    assert sections["key_contracts"]["title"] == "4. 경영상의 주요계약"
