from __future__ import annotations

from sqlalchemy import text

def get_financial_timeseries_quality(corp_code: str, *, year: int = 2025, years_back: int = 5) -> dict:
    from kreports.db.engine import engine

    years = list(range(int(year) - int(years_back) + 1, int(year) + 1))
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT year, fs_div, revenue, operating_profit, net_income,
                   total_assets, total_debt, total_equity, operating_cf
            FROM financials
            WHERE corp_code=:corp_code
              AND quarter=4
              AND year BETWEEN :start_year AND :year
            ORDER BY year, CASE WHEN fs_div='CFS' THEN 0 ELSE 1 END
            """
        ), {"corp_code": corp_code, "start_year": years[0], "year": year}).mappings().all()

    by_year = {}
    for row in rows:
        if row["year"] not in by_year:
            by_year[row["year"]] = dict(row)

    missing = [target_year for target_year in years if target_year not in by_year]
    fs_divs = {row["fs_div"] for row in by_year.values()}
    fs_div_used = "CFS" if fs_divs == {"CFS"} else "mixed" if fs_divs else None
    return {
        "verdict": "pass" if not missing and fs_div_used == "CFS" else "conditional",
        "corp_code": corp_code,
        "years": years,
        "fs_div_used": fs_div_used,
        "missing_years": missing,
        "rows": list(by_year.values()),
    }
