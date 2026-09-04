"""Pure annual-report identity predicates shared by provenance contracts."""
from __future__ import annotations

from kreports.processor.audit_parser import parse_bsns_year

_AUDIT_REPORT_EXCLUDED_FRAGMENTS = ("내부회계", "감사의감사보고서", "내부감시장치")


def annual_report_name_matches_business_year(
    report_nm: object,
    bsns_year: object,
) -> bool:
    """Match the DART annual-report marker used by compact citation anchors.

    DART may prefix a valid annual report with a correction label such as
    ``[기재정정]``.  The anchor contract therefore looks for the embedded annual
    marker instead of requiring it at the beginning of the report name.
    """
    try:
        normalized_year = int(bsns_year)
    except (TypeError, ValueError):
        return False
    return f"사업보고서 ({normalized_year}." in str(report_nm or "").strip()


def audit_report_receipt_matches_business_year(
    report_nm: object,
    disc_date: object,
    bsns_year: object,
) -> bool:
    """Match a primary audit-report disclosure to its fiscal (business) year.

    Excludes internal-control and meta "audit of the audit report" filings,
    the same way the pre-existing ``_separate_audit_receipts`` discovery did.
    ``disc_date`` accepts either a ``YYYYMMDD`` string (DART's raw ``rcept_dt``
    field) or a date-like object with ``.strftime`` (a local ``Disclosure.disc_date``
    column value) so this one predicate serves both a live-API caller and a
    local-DB caller.
    """
    compact_name = "".join(str(report_nm or "").split())
    if "감사보고서" not in compact_name:
        return False
    if any(fragment in compact_name for fragment in _AUDIT_REPORT_EXCLUDED_FRAGMENTS):
        return False
    try:
        normalized_year = int(bsns_year)
    except (TypeError, ValueError):
        return False
    if hasattr(disc_date, "strftime"):
        rcept_dt = disc_date.strftime("%Y%m%d")
    else:
        rcept_dt = str(disc_date or "")
    return parse_bsns_year(str(report_nm or ""), rcept_dt) == normalized_year
