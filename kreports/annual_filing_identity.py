"""Pure annual-report identity predicates shared by provenance contracts."""
from __future__ import annotations


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
