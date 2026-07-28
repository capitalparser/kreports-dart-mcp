"""Public three-year audit-effort input preparation contracts."""
from __future__ import annotations

from datetime import date

from sqlalchemy import event

from kreports.db.models import AuditFee, Company, Disclosure, Financial


def _seed_years(
    temp_engine,
    *,
    fs_div: str = "CFS",
    include_audit_receipts: bool = True,
    missing_oldest_audit: bool = False,
    include_ofs: bool = False,
    actual_and_contract: bool = False,
) -> None:
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자"))
        for index, year in enumerate((2025, 2024, 2023)):
            session.add(Financial(
                corp_code="00126380", year=year, quarter=4, fs_div=fs_div,
                total_assets=(1_000 - index * 100) * 100_000_000,
                revenue=(750 - index * 50) * 100_000_000,
            ))
            session.add(Disclosure(
                rcept_no=f"{year + 1}0318000001", corp_code="00126380", corp_name="삼성전자",
                disc_date=date(year + 1, 3, 18), disc_type="A",
                report_nm=f"사업보고서 ({year}.12)", flr_nm="삼성전자",
            ))
            fee = None if missing_oldest_audit and year == 2023 else 120 - index * 10
            hours = None if missing_oldest_audit and year == 2023 else 1_800 - index * 100
            session.add(AuditFee(
                corp_code="00126380", bsns_year=year, audit_fee_m=fee, audit_hours=hours,
                actual_fee_m=200 if actual_and_contract and year == 2025 else None,
                actual_hours=None if actual_and_contract and year == 2025 else None,
                contract_fee_m=100 if actual_and_contract and year == 2025 else None,
                contract_hours=1_000 if actual_and_contract and year == 2025 else None,
                source_rcept_no=(f"{year + 1}0318000002" if include_audit_receipts else None),
            ))
            if include_audit_receipts:
                session.add(Disclosure(
                    rcept_no=f"{year + 1}0318000002", corp_code="00126380", corp_name="삼성전자",
                    disc_date=date(year + 1, 3, 18), disc_type="A",
                    report_nm=f"사업보고서 ({year}.12)", flr_nm="삼성전자",
                ))
            if include_ofs:
                session.add(Financial(
                    corp_code="00126380", year=year, quarter=4, fs_div="OFS",
                    total_assets=(9_000 - index * 100) * 100_000_000,
                    revenue=(8_000 - index * 50) * 100_000_000,
                ))


def _prepare(*args, **kwargs):
    from kreports.analysis.audit_effort_inputs import prepare_standard_audit_hours_inputs

    return prepare_standard_audit_hours_inputs(*args, **kwargs)


def test_prepare_standard_audit_hours_inputs_returns_three_complete_cited_years(temp_engine):
    """Missing one of the public inputs or its filing must downgrade the row."""
    _seed_years(temp_engine)

    result = _prepare("00126380")

    assert result["requested_years"] == [2025, 2024, 2023]
    assert result["fs_div_used"] == "CFS"
    assert [row["input_status"] for row in result["rows"]] == ["usable"] * 3
    assert all(row["financial_source"]["rcept_no"] for row in result["rows"])
    assert all(row["audit_source"]["rcept_no"] for row in result["rows"])
    assert result["data_quality"]["status"] == "usable"
    assert result["subject_scale_history_quality"]["status"] == "usable"
    assert result["standard_audit_hours_assessment"] == "not_assessed"


def test_prepare_standard_audit_hours_inputs_preserves_missing_oldest_fee_and_hours(temp_engine):
    """An oldest-year gap must remain visible instead of being filled from newer data."""
    _seed_years(temp_engine, missing_oldest_audit=True)

    result = _prepare("00126380")
    oldest = result["rows"][-1]

    assert oldest["missing_fields"] == ["audit_fee_m", "audit_hours"]
    assert oldest["input_status"] == "limited"
    assert result["data_quality"]["status"] == "limited"
    assert {"2023.audit_fee_m", "2023.audit_hours"} <= set(result["data_quality"]["missing_fields"])
    assert result["standard_audit_hours_assessment"] == "not_assessed"


def test_prepare_standard_audit_hours_inputs_marks_uncitable_audit_rows_limited(temp_engine):
    """Structured audit values without a valid audit receipt are not source-backed inputs."""
    _seed_years(temp_engine, include_audit_receipts=False)

    result = _prepare("00126380")

    assert result["data_quality"]["status"] == "limited"
    assert all("uncitable_audit_source" in row["provenance_gaps"] for row in result["rows"])
    assert all(row["audit_source"] is None for row in result["rows"])
    assert result["standard_audit_hours_assessment"] == "not_assessed"


def test_prepare_standard_audit_hours_inputs_uses_one_financial_statement_basis(temp_engine):
    """Auto selection must not silently mix CFS and OFS across the three years."""
    _seed_years(temp_engine, include_ofs=True)

    result = _prepare("00126380", fs_strategy="auto")

    assert result["fs_div_used"] == "CFS"
    assert [row["fs_div"] for row in result["rows"]] == ["CFS", "CFS", "CFS"]
    assert [row["total_assets"] for row in result["rows"]] == [100_000_000_000, 90_000_000_000, 80_000_000_000]


def test_prepare_standard_audit_hours_inputs_never_combines_actual_fee_and_contract_hours(temp_engine):
    """Typed observations from different bases cannot become one apparent observation."""
    _seed_years(temp_engine, actual_and_contract=True)

    result = _prepare("00126380")
    current = result["rows"][0]

    assert current["audit_fee_m"] == 100
    assert current["audit_hours"] == 1_000
    assert current["hours_basis"] == "contract"


def test_prepare_standard_audit_hours_inputs_reports_cache_absence_without_claiming_filing_absence(temp_engine):
    """No local rows are a cache limitation, not evidence that the filing has no values."""
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="삼성전자"))

    result = _prepare("00126380")

    assert result["data_quality"]["status"] == "missing"
    assert "로컬 캐시에 없음" in " ".join(result["data_quality"]["limitations"])
    assert "공시에 없음" not in " ".join(result["data_quality"]["limitations"])
    assert result["standard_audit_hours_assessment"] == "not_assessed"


def test_prepare_standard_audit_hours_inputs_uses_at_most_five_bounded_queries(temp_engine):
    """Three-year preparation must not regress into one query per financial field."""
    _seed_years(temp_engine)
    statements = []

    def count_queries(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(temp_engine, "after_cursor_execute", count_queries)
    try:
        result = _prepare("00126380")
    finally:
        event.remove(temp_engine, "after_cursor_execute", count_queries)

    assert result["data_quality"]["status"] == "usable"
    assert len(statements) <= 5


def test_prepare_standard_audit_hours_inputs_public_surface_starts_with_non_calculation_conclusion(temp_engine):
    """The MCP result must lead with preparation, never an invented standard-hours value."""
    from kreports.mcp.dispatch import legacy_result
    from kreports.mcp.answer_pack import build_answer_pack

    _seed_years(temp_engine)

    raw = _prepare("00126380")
    assert build_answer_pack("prepare_standard_audit_hours_inputs", raw) is not None

    result = legacy_result("prepare_standard_audit_hours_inputs", {"company": "005930"})

    assert result["domain_verdict"] == "not_assessed"
    assert "표준감사시간 결론: 산정하지 않음" in result["answer"]
    table = result["answer_pack"]["tables"][0]
    assert [column["label"] for column in table["columns"]] == [
        "연도", "FS", "총자산(억원)", "매출(억원)", "감사보수(백만원)",
        "감사시간", "기준", "입력상태", "미확보 항목",
    ]
    assert len(table["rows"]) == 3
