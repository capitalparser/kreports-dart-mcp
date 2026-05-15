from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import engine

CORE_MARKETS = ("KOSPI", "KOSDAQ")


def pct(numerator: int | float | None, denominator: int | float | None) -> float:
    return round(100.0 * float(numerator or 0) / float(denominator or 0), 1) if denominator else 0.0


def auditor_readiness_snapshot(year: int = 2025) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                WITH listed AS (
                  SELECT corp_code, market
                  FROM companies
                  WHERE stock_code IS NOT NULL
                    AND market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                ),
                fin_any AS (
                  SELECT DISTINCT corp_code FROM financials
                  WHERE year=:year AND quarter=4
                ),
                fin_cfs AS (
                  SELECT DISTINCT corp_code FROM financials
                  WHERE year=:year AND quarter=4 AND fs_div='CFS'
                ),
                fin_ofs AS (
                  SELECT DISTINCT corp_code FROM financials
                  WHERE year=:year AND quarter=4 AND fs_div='OFS'
                ),
                fee AS (
                  SELECT DISTINCT corp_code FROM audit_fees WHERE bsns_year=:year
                ),
                aud AS (
                  SELECT DISTINCT corp_code FROM auditors
                  WHERE bsns_year=:year AND fs_div='CFS'
                ),
                disc AS (
                  SELECT DISTINCT corp_code FROM disclosures
                  WHERE disc_date >= :recent_start
                ),
                pol AS (
                  SELECT DISTINCT corp_code FROM accounting_policy_items
                  WHERE bsns_year=:year AND fs_div='CFS'
                )
                SELECT l.market,
                       COUNT(*) listed,
                       SUM(CASE WHEN l.corp_code IN fin_any THEN 1 ELSE 0 END) financial_any_2025,
                       SUM(CASE WHEN l.corp_code IN fin_cfs THEN 1 ELSE 0 END) financial_cfs_2025,
                       SUM(CASE WHEN l.corp_code IN fin_ofs THEN 1 ELSE 0 END) financial_ofs_2025,
                       SUM(CASE WHEN l.corp_code IN fee THEN 1 ELSE 0 END) audit_fee_2025,
                       SUM(CASE WHEN l.corp_code IN aud THEN 1 ELSE 0 END) auditor_2025,
                       SUM(CASE WHEN l.corp_code IN disc THEN 1 ELSE 0 END) disclosure_recent,
                       SUM(CASE WHEN l.corp_code IN pol THEN 1 ELSE 0 END) policy_2025
                FROM listed l
                GROUP BY l.market
                ORDER BY l.market
                """
            ),
            {"year": year, "recent_start": f"{year}-01-01"},
        ).mappings().all()
        policy_corps = conn.execute(
            text("SELECT COUNT(DISTINCT corp_code) FROM accounting_policy_items")
        ).scalar() or 0
        auditor_2025_corps = conn.execute(
            text("SELECT COUNT(DISTINCT corp_code) FROM auditors WHERE bsns_year=:year"),
            {"year": year},
        ).scalar() or 0

    return {
        "year": year,
        "markets": {row["market"]: dict(row) for row in rows},
        "policy_corps": int(policy_corps),
        "auditor_2025_corps": int(auditor_2025_corps),
    }


def readiness_verdict(snapshot: dict) -> dict:
    required_gaps: list[str] = []
    recommended_gaps: list[str] = []
    for market in CORE_MARKETS:
        row = snapshot.get("markets", {}).get(market, {})
        listed = int(row.get("listed") or 0)
        if pct(row.get("financial_any_2025"), listed) < 95.0:
            required_gaps.append("financial_any_2025")
        if pct(row.get("audit_fee_2025"), listed) < 95.0:
            required_gaps.append("audit_fee_2025")
        if pct(row.get("disclosure_recent"), listed) < 95.0:
            required_gaps.append("disclosure_recent")

    if int(snapshot.get("policy_corps") or 0) < 100:
        recommended_gaps.append("accounting_policy")
    if int(snapshot.get("auditor_2025_corps") or 0) < 1000:
        recommended_gaps.append("auditor_history")

    verdict = "pass"
    if required_gaps:
        verdict = "fail"
    elif recommended_gaps:
        verdict = "conditional_pass"
    return {
        "verdict": verdict,
        "required_gaps": sorted(set(required_gaps)),
        "recommended_gaps": sorted(set(recommended_gaps)),
    }
