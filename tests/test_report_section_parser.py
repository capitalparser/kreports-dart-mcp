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


def test_viewer_html_section_anchor_titles_are_recognized():
    html = """
    <HTML>
      <BODY>
        <h1>II. 사업의 내용</h1>
        <P class='section-1'><A name='toc1'>II. 사업의 내용</A></P>
        <P class='section-2'><A name='toc2'>1. 사업의 개요</A></P>
        <P>신재생에너지 개발과 ESS 사업을 영위합니다.</P>
        <P class='section-2'><A name='toc3'>2. 주요 제품 및 서비스</A></P>
        <P>태양광, 풍력, 연료전지, ESS 서비스를 제공합니다.</P>
        <P class='section-2'><A name='toc4'>5. 위험관리 및 파생거래</A></P>
        <P>시장위험, 신용위험 및 유동성위험을 관리합니다.</P>
        <P class='section-2'><A name='toc5'>6. 주요계약 및 연구개발활동</A></P>
        <P>주요 약정 현황과 연구개발 담당조직을 설명합니다.</P>
        <h1>IV. 이사의 경영진단 및 분석의견</h1>
        <P class='section-1'><A name='toc6'>IV. 이사의 경영진단 및 분석의견</A></P>
        <P>매출액과 영업이익 등 경영성과를 분석합니다.</P>
      </BODY>
    </HTML>
    """

    sections = extract_report_sections(html)

    assert sections["business_description"]["title"] == "II. 사업의 내용"
    assert "태양광" in sections["business_description"]["body_text"]
    assert "시장위험" in sections["business_description"]["body_text"]
    assert sections["business_overview"]["title"] == "1. 사업의 개요"
    assert sections["risk_management"]["title"] == "5. 위험관리 및 파생거래"
    assert sections["key_contracts"]["title"] == "6. 주요계약 및 연구개발활동"
    assert sections["rd_activities"]["title"] == "6. 주요계약 및 연구개발활동"
    assert sections["management_plan"]["title"] == "IV. 이사의 경영진단 및 분석의견"
    assert "경영성과" in sections["management_plan"]["body_text"]
