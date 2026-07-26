from kreports.processor.audit_fee_table_parser import parse_audit_fee_table


def test_cached_report_table_parses_actual_contract_and_explicit_unit():
    body = """
    <html><body>
      <p>(단위: 천원, 시간)</p>
      <table>
        <tr>
          <th>사업연도</th><th>감사인</th>
          <th>계약보수</th><th>계약시간</th>
          <th>실제수행보수</th><th>실제수행시간</th>
        </tr>
        <tr>
          <td>2024</td><td>삼정회계법인</td>
          <td>1,100,000</td><td>11,000</td>
          <td>1,200,000</td><td>10,500</td>
        </tr>
      </table>
    </body></html>
    """

    rows = parse_audit_fee_table(
        body,
        corp_code="00126380",
        bsns_year=2024,
        rcept_no="20250318000001",
    )

    assert len(rows) == 1
    assert rows[0].contract_fee_m == 1100
    assert rows[0].actual_fee_m == 1200
    assert rows[0].contract_hours == 11000
    assert rows[0].actual_hours == 10500
    assert rows[0].auditor_nm == "삼정회계법인"
    assert rows[0].source_rcept_no == "20250318000001"
    assert rows[0].displayed_unit == "천원"


def test_cached_report_parser_rejects_unrelated_fee_table():
    body = """
    <table>
      <tr><th>구분</th><th>임원 보수</th></tr>
      <tr><td>대표이사</td><td>1,200</td></tr>
    </table>
    """

    assert parse_audit_fee_table(body, corp_code="001", bsns_year=2024) == []


def test_cached_report_parser_bounds_adversarial_input():
    body = "<table>" + "<tr><td>x</td></tr>" * 10_000 + "</table>"

    rows = parse_audit_fee_table(
        body,
        corp_code="001",
        bsns_year=2024,
        max_input_chars=2_000,
        max_rows=10,
    )

    assert rows == []


def test_cached_report_parser_preserves_multirow_contract_actual_headers():
    body = """
    <p>단위: 백만원, 시간</p>
    <table>
      <tr>
        <th rowspan="2">사업연도</th><th rowspan="2">감사인</th>
        <th colspan="2">계약내용</th><th colspan="2">실제수행내용</th>
      </tr>
      <tr><th>보수</th><th>시간</th><th>보수</th><th>시간</th></tr>
      <tr>
        <td>2024</td><td>한영회계법인</td>
        <td>800</td><td>8,000</td><td>900</td><td>8,500</td>
      </tr>
    </table>
    """

    rows = parse_audit_fee_table(body, corp_code="001", bsns_year=2024)

    assert rows[0].contract_fee_m == 800
    assert rows[0].contract_hours == 8000
    assert rows[0].actual_fee_m == 900
    assert rows[0].actual_hours == 8500
