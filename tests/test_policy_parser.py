from kreports.processor.policy_parser import extract_policy_section


def test_policy_section_keeps_child_titles_until_next_peer_note():
    xml = """
    <DOCUMENT>
      <TITLE>1. 일반사항</TITLE>
      <P>회사 개요</P>
      <TITLE>2. 중요한 회계정책</TITLE>
      <P>아래 정책은 연결재무제표에 적용됩니다.</P>
      <TITLE>(1) 수익인식</TITLE>
      <P>고객과의 계약에서 수행의무를 식별합니다.</P>
      <TITLE>(2) 리스</TITLE>
      <P>사용권자산과 리스부채를 인식합니다.</P>
      <TITLE>3. 중요한 회계추정 및 가정</TITLE>
      <P>이 문장은 정책 섹션에 포함되면 안 됩니다.</P>
    </DOCUMENT>
    """

    section = extract_policy_section(xml)

    assert section is not None
    assert "수행의무" in section
    assert "사용권자산" in section
    assert "포함되면 안 됩니다" not in section
