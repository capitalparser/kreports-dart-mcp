from __future__ import annotations

from datetime import date

from kreports.annual_filing_identity import (
    annual_report_name_matches_business_year,
    audit_report_receipt_matches_business_year,
)


def test_audit_report_receipt_matches_business_year_infers_year_from_receipt_date():
    # No explicit year in the title -> inferred from disc_date: month <= 4 means
    # the prior calendar year's fiscal year (matches kreports.processor.audit_parser.parse_bsns_year).
    assert audit_report_receipt_matches_business_year(
        "감사보고서 (첨부:재무제표)", "20220331", 2021
    ) is True


def test_audit_report_receipt_matches_business_year_accepts_date_object():
    assert audit_report_receipt_matches_business_year(
        "감사보고서 (첨부:재무제표)", date(2022, 3, 31), 2021
    ) is True


def test_audit_report_receipt_matches_business_year_rejects_wrong_year():
    assert audit_report_receipt_matches_business_year(
        "감사보고서 (첨부:재무제표)", "20220331", 2022
    ) is False


def test_audit_report_receipt_matches_business_year_rejects_internal_control_report():
    assert audit_report_receipt_matches_business_year(
        "내부회계관리제도감사보고서", "20220331", 2021
    ) is False


def test_audit_report_receipt_matches_business_year_rejects_meta_audit_of_audit_report():
    assert audit_report_receipt_matches_business_year(
        "감사의감사보고서", "20220331", 2021
    ) is False


def test_audit_report_receipt_matches_business_year_rejects_business_report():
    assert audit_report_receipt_matches_business_year(
        "사업보고서 (2021.12)", "20220331", 2021
    ) is False


def test_annual_report_name_matches_business_year_still_works():
    # Regression guard: this predicate is untouched by this task.
    assert annual_report_name_matches_business_year("사업보고서 (2021.12)", 2021) is True
