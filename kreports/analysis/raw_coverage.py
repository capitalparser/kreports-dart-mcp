"""Coverage queries for raw annual-report source documents."""
from __future__ import annotations

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module


VALID_RCEPT_SQL = """
length(d.rcept_no)=14
AND d.rcept_no GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
AND substr(d.rcept_no,1,4)=strftime('%Y', d.disc_date)
"""


def raw_annual_report_coverage(
    *,
    start_filing_year: int = 2022,
    end_filing_year: int = 2026,
    markets: list[str] | None = None,
) -> dict:
    """Return latest annual-report raw-document coverage by filing year/market."""
    markets = markets or ["KOSPI", "KOSDAQ"]
    stmt = text(f"""
    WITH ranked AS (
      SELECT d.rcept_no, d.corp_code, d.disc_date, co.market,
             ROW_NUMBER() OVER (
               PARTITION BY d.corp_code, substr(d.disc_date,1,4)
               ORDER BY d.disc_date DESC, d.rcept_no DESC
             ) AS rn
      FROM disclosures d
      JOIN companies co ON co.corp_code=d.corp_code
      WHERE co.stock_code IS NOT NULL
        AND co.market IN :markets
        AND d.report_nm LIKE '%사업보고서%'
        AND d.report_nm NOT LIKE '%제출기한연장%'
        AND d.report_nm NOT LIKE '%해외증권%'
        AND CAST(substr(d.disc_date,1,4) AS INTEGER) BETWEEN :start_year AND :end_year
        AND ({VALID_RCEPT_SQL})
    )
    SELECT CAST(substr(r.disc_date,1,4) AS INTEGER) AS filing_year,
           r.market,
           COUNT(*) AS latest_reports,
           SUM(CASE WHEN sd.id IS NOT NULL THEN 1 ELSE 0 END) AS raw_externalized,
           SUM(CASE WHEN sd.id IS NULL THEN 1 ELSE 0 END) AS raw_missing
    FROM ranked r
    LEFT JOIN source_documents sd
      ON sd.rcept_no=r.rcept_no
     AND sd.source_type='business_report'
     AND sd.content_type!='derived_report_sections'
     AND sd.storage_status='externalized'
    WHERE r.rn=1
    GROUP BY 1,2
    ORDER BY 1,2
    """).bindparams(bindparam("markets", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                stmt,
                {
                    "markets": markets,
                    "start_year": int(start_filing_year),
                    "end_year": int(end_filing_year),
                },
            ).mappings()
        ]
    totals = {
        "latest_reports": sum(int(row["latest_reports"] or 0) for row in rows),
        "raw_externalized": sum(int(row["raw_externalized"] or 0) for row in rows),
        "raw_missing": sum(int(row["raw_missing"] or 0) for row in rows),
    }
    totals["coverage_pct"] = (
        round(100.0 * totals["raw_externalized"] / totals["latest_reports"], 2)
        if totals["latest_reports"]
        else 100.0
    )
    return {
        "start_filing_year": int(start_filing_year),
        "end_filing_year": int(end_filing_year),
        "markets": markets,
        "totals": totals,
        "rows": rows,
        "status": "complete" if totals["raw_missing"] == 0 else "in_progress",
    }
