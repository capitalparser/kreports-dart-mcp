from kreports.processor.audit_parser import (
    is_valid_auditor_name,
    normalize_auditor_name,
    parse_auditor_from_doc_xml,
)


def test_invalid_auditor_placeholders_are_not_valid_names():
    assert not is_valid_auditor_name("-")
    assert not is_valid_auditor_name("해당사항 없음")
    assert not is_valid_auditor_name("")
    assert is_valid_auditor_name("한영회계법인")
    assert normalize_auditor_name("EY한영") == "한영회계법인"


def test_new_aud_opn_skips_blank_cfs_and_keeps_valid_ofs():
    xml = """
    <TABLE-GROUP ACLASS="AUD_OPN">
      <TABLE>
        <TR>
          <TE ACODE="OPN_AUR1_C">-</TE>
          <TE ACODE="OPN_AUR1_A">한영회계법인</TE>
          <TE AUNIT="OPN_CMT1_C" AUNITVALUE="0"></TE>
          <TE AUNIT="OPN_CMT1_A" AUNITVALUE="1"></TE>
        </TR>
      </TABLE>
    </TABLE-GROUP>
    """

    rows = parse_auditor_from_doc_xml(xml)

    assert rows == [
        {
            "fs_div": "OFS",
            "auditor_nm": "한영회계법인",
            "audit_opinion": "적정",
        }
    ]


def test_old_aud_opn_skips_placeholder_auditor():
    xml = """
    <TABLE-GROUP ACLASS="AUD_OPN">
      <TABLE>
        <TR>
          <TE ACODE="OPN_AUR1">해당사항 없음</TE>
          <TE ACODE="OPN_CMT1">적정의견</TE>
        </TR>
      </TABLE>
    </TABLE-GROUP>
    """

    assert parse_auditor_from_doc_xml(xml) == []
