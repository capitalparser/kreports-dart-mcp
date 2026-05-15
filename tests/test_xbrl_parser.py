import io
import zipfile

from kreports.processor.xbrl_parser import parse_xbrl_zip


def _zip_with_files(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_xbrl_parser_keeps_instant_balance_sheet_amount_and_korean_label():
    xbrl = """<?xml version="1.0" encoding="UTF-8"?>
    <xbrli:xbrl
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
      <xbrli:context id="CFS_I_2024">
        <xbrli:entity>
          <xbrli:identifier scheme="dart">00126380</xbrli:identifier>
          <xbrli:segment>
            <xbrldi:explicitMember dimension="dart:FinancialStatementAxis">dart:ConsolidatedMember</xbrldi:explicitMember>
          </xbrli:segment>
        </xbrli:entity>
        <xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period>
      </xbrli:context>
      <xbrli:unit id="KRW"><xbrli:measure>iso4217:KRW</xbrli:measure></xbrli:unit>
      <ifrs-full:Assets contextRef="CFS_I_2024" unitRef="KRW" decimals="0">571164152000000</ifrs-full:Assets>
    </xbrli:xbrl>
    """
    labels = """<?xml version="1.0" encoding="UTF-8"?>
    <link:linkbase
      xmlns:link="http://www.xbrl.org/2003/linkbase"
      xmlns:xlink="http://www.w3.org/1999/xlink"
      xmlns:xml="http://www.w3.org/XML/1998/namespace">
      <link:label xlink:type="resource" xlink:label="label_ifrs-full_Assets" xml:lang="ko">자산총계</link:label>
    </link:linkbase>
    """

    facts = parse_xbrl_zip(
        _zip_with_files({"sample_CFS.xbrl": xbrl, "sample_label.xml": labels}),
        corp_code="00126380",
        bsns_year=2024,
        reprt_code="11011",
        fs_div="CFS",
    )

    assert facts == [
        {
            "corp_code": "00126380",
            "bsns_year": 2024,
            "reprt_code": "11011",
            "fs_div": "CFS",
            "sj_div": "BS",
            "account_id": "ifrs-full_Assets",
            "account_nm": "자산총계",
            "ord": None,
            "thstrm_amount": 571_164_152_000_000,
            "frmtrm_amount": None,
            "bfefrmtrm_amount": None,
            "thstrm_add_amount": None,
        }
    ]


def test_xbrl_parser_skips_non_krw_numeric_units():
    xbrl = """<?xml version="1.0" encoding="UTF-8"?>
    <xbrli:xbrl
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
      <xbrli:context id="D_2024">
        <xbrli:entity><xbrli:identifier scheme="dart">00126380</xbrli:identifier></xbrli:entity>
        <xbrli:period>
          <xbrli:startDate>2024-01-01</xbrli:startDate>
          <xbrli:endDate>2024-12-31</xbrli:endDate>
        </xbrli:period>
      </xbrli:context>
      <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
      <ifrs-full:Revenue contextRef="D_2024" unitRef="USD" decimals="0">1000</ifrs-full:Revenue>
    </xbrli:xbrl>
    """

    facts = parse_xbrl_zip(
        _zip_with_files({"sample.xbrl": xbrl}),
        corp_code="00126380",
        bsns_year=2024,
        reprt_code="11011",
        fs_div="CFS",
    )

    assert facts == []
