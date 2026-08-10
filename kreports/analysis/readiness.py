from __future__ import annotations

import re

from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.analysis.filing_provenance import valid_annual_filing_receipt

CORE_MARKETS = ("KOSPI", "KOSDAQ")
DEFAULT_YEARS_BACK = 5
CORE_COVERAGE_THRESHOLD = 95.0
COMPLETENESS_THRESHOLD = 95.0
FEATURE_COVERAGE_THRESHOLD = 50.0
INVESTOR_CORE_METRICS = (
    "revenue",
    "profit_loss",
    "operating_cash_flow",
    "assets",
    "liabilities",
    "equity",
)


def pct(numerator: int | float | None, denominator: int | float | None) -> float:
    return round(100.0 * float(numerator or 0) / float(denominator or 0), 1) if denominator else 0.0


_KAM_REASON_PATTERNS = (
    "핵심감사사항으로 결정",
    "핵심 감사사항으로 결정",
    "핵심감사사항으로 선정한 이유",
    "중요한 왜곡표시위험",
    "유의적인 위험",
    "추정의 불확실성",
    "경영진의 판단",
)

_KAM_PROCEDURE_PATTERNS = (
    "감사절차",
    "감사에서 다루어진 방법",
    "수행하였습니다",
    "문서검사",
    "내부통제",
    "재계산",
    "대사",
)

_NO_KAM_PATTERNS = (
    "보고해야 할 핵심감사사항이 없",
    "보고할 핵심감사사항이 없",
    "핵심감사사항이 없다고 결정",
)


def _evidence_kam_body(normalized_text: str | None) -> str:
    """Return only the KAM evidence block, excluding later generic sections."""
    text_value = normalized_text or ""
    start = text_value.find("report_section/kam")
    if start < 0:
        return ""
    tail = text_value[start:]
    next_section = tail.find("\n## report_section/", len("report_section/kam"))
    if next_section > 0:
        tail = tail[:next_section]
    return tail.strip()


def _contains_any(text_value: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text_value for pattern in patterns)


def _policy_change_readiness_coverage(
    conn,
    *,
    params: dict[str, object],
    market_filter: str,
    has_note_chapters: bool,
) -> dict[str, int]:
    """Count only comparable chapter pairs proven to the latest annual filing."""
    empty = {
        "policy_change_chapters": 0,
        "policy_change_companies": 0,
        "policy_change_comparable_companies": 0,
        "policy_change_excluded_unproven": 0,
        "policy_change_excluded_not_comparable": 0,
        "policy_change_excluded_missing_requested_year": 0,
    }
    if not has_note_chapters:
        return empty
    notes = conn.execute(
        text(
            "SELECT anc.corp_code, anc.bsns_year, anc.fs_div, anc.rcept_no, "
            "anc.note_no, anc.section_type "
            "FROM accounting_note_chapters anc "
            "JOIN companies c ON c.corp_code=anc.corp_code "
            "WHERE anc.bsns_year<=:year "
            "AND anc.note_no IN ('2', '3', '4') "
            "AND anc.section_type IN ('basis', 'policy', 'estimate_judgment')"
            + market_filter
        ),
        params,
    ).mappings().all()
    if not notes:
        return empty
    disclosures = conn.execute(
        text(
            "SELECT d.corp_code, d.rcept_no, d.disc_date, d.report_nm "
            "FROM disclosures d JOIN companies c ON c.corp_code=d.corp_code "
            "WHERE d.report_nm LIKE '%사업보고서 (%'" + market_filter
        ),
        params,
    ).mappings().all()
    latest: dict[tuple[str, int], str] = {}
    requested_scopes = {
        (str(row["corp_code"]), int(row["bsns_year"])) for row in notes
    }
    latest_annual_candidate: dict[tuple[str, int], tuple[str, str]] = {}
    for disclosure in disclosures:
        corp_code = str(disclosure["corp_code"])
        report_name = str(disclosure.get("report_nm") or "")
        raw_receipt = str(disclosure.get("rcept_no") or "")
        disclosure_date = str(disclosure.get("disc_date") or "")[:10].replace("-", "")
        for scope in requested_scopes:
            if scope[0] != corp_code or f"사업보고서 ({scope[1]}." not in report_name:
                continue
            candidate = (disclosure_date, raw_receipt)
            if candidate > latest_annual_candidate.get(scope, ("", "")):
                latest_annual_candidate[scope] = candidate
    for scope, (disclosure_date, raw_receipt) in latest_annual_candidate.items():
        receipt = valid_annual_filing_receipt(raw_receipt, scope[1])
        if receipt is None or receipt != raw_receipt or receipt[:8] != disclosure_date:
            continue
        latest[scope] = receipt

    proven_years_by_key: dict[tuple[str, str, str, str], set[int]] = {}
    proven_rows: list[tuple[str, str, str, str, int]] = []
    unproven = 0
    current_year_chapters = 0
    for row in notes:
        corp_code = str(row["corp_code"])
        bsns_year = int(row["bsns_year"])
        raw_receipt = str(row.get("rcept_no") or "")
        receipt = valid_annual_filing_receipt(raw_receipt, bsns_year)
        if (
            receipt is None
            or receipt != raw_receipt
            or latest.get((corp_code, bsns_year)) != receipt
        ):
            unproven += 1
            continue
        if bsns_year == int(params["year"]):
            current_year_chapters += 1
        key = (
            corp_code,
            str(row["fs_div"]),
            str(row["note_no"]),
            str(row["section_type"]),
        )
        proven_years_by_key.setdefault(key, set()).add(bsns_year)
        proven_rows.append((*key, bsns_year))
    historically_comparable_keys = {
        key for key, years in proven_years_by_key.items() if len(years) >= 2
    }
    requested_year = int(params["year"])
    comparable_keys = {
        key
        for key in historically_comparable_keys
        if requested_year in proven_years_by_key[key]
    }
    comparable_companies = {key[0] for key in comparable_keys}
    not_comparable = sum(
        (corp_code, fs_div, note_no, section_type) not in comparable_keys
        for corp_code, fs_div, note_no, section_type, _year in proven_rows
    )
    return {
        "policy_change_chapters": current_year_chapters,
        "policy_change_companies": len(comparable_companies),
        "policy_change_comparable_companies": len(comparable_companies),
        "policy_change_excluded_unproven": unproven,
        "policy_change_excluded_not_comparable": not_comparable,
        "policy_change_excluded_missing_requested_year": len(
            historically_comparable_keys - comparable_keys
        ),
    }


def required_years(year: int = 2025, years_back: int = DEFAULT_YEARS_BACK) -> list[int]:
    return list(range(int(year) - int(years_back) + 1, int(year) + 1))


def _empty_year_market_row(market: str, listed: int) -> dict:
    return {
        "market": market,
        "listed": int(listed or 0),
        "financial_any": 0,
        "financial_cfs": 0,
        "financial_ofs": 0,
        "business_report": 0,
        "audit_report": 0,
        "audit_fee": 0,
        "auditor": 0,
        "policy": 0,
    }


def auditor_readiness_snapshot(year: int = 2025, years_back: int = DEFAULT_YEARS_BACK) -> dict:
    years = required_years(year, years_back)
    start_year = years[0]
    with _engine_module.engine.connect() as conn:
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
                br AS (
                  SELECT DISTINCT corp_code FROM source_documents
                  WHERE bsns_year=:year
                    AND source_type='business_report'
                ),
                ar AS (
                  SELECT DISTINCT corp_code FROM source_documents
                  WHERE bsns_year=:year
                    AND source_type='audit_report'
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
                       SUM(CASE WHEN l.corp_code IN br THEN 1 ELSE 0 END) business_report_2025,
                       SUM(CASE WHEN l.corp_code IN ar THEN 1 ELSE 0 END) audit_report_2025,
                       SUM(CASE WHEN l.corp_code IN fee THEN 1 ELSE 0 END) audit_fee_2025,
                       SUM(CASE WHEN l.corp_code IN aud THEN 1 ELSE 0 END) auditor_2025,
                       SUM(CASE WHEN l.corp_code IN disc THEN 1 ELSE 0 END) disclosure_recent,
                       SUM(CASE WHEN l.corp_code IN pol THEN 1 ELSE 0 END) policy_2025
                FROM listed l
                GROUP BY l.market
                ORDER BY l.market
                """
            ),
            {
                "year": year,
                "recent_start": f"{year}-01-01",
            },
        ).mappings().all()
        policy_corps = conn.execute(
            text("SELECT COUNT(DISTINCT corp_code) FROM accounting_policy_items")
        ).scalar() or 0
        audit_fee_2025_corps = conn.execute(
            text("SELECT COUNT(DISTINCT corp_code) FROM audit_fees WHERE bsns_year=:year"),
            {"year": year},
        ).scalar() or 0
        listed_rows = conn.execute(
            text(
                """
                SELECT market, COUNT(*) listed
                FROM companies
                WHERE stock_code IS NOT NULL
                  AND market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                GROUP BY market
                """
            )
        ).mappings().all()
        fin_rows = conn.execute(
            text(
                """
                SELECT c.market, f.year,
                       COUNT(DISTINCT f.corp_code) financial_any,
                       COUNT(DISTINCT CASE WHEN f.fs_div='CFS' THEN f.corp_code END) financial_cfs,
                       COUNT(DISTINCT CASE WHEN f.fs_div='OFS' THEN f.corp_code END) financial_ofs
                FROM companies c
                JOIN financials f ON f.corp_code=c.corp_code
                WHERE c.stock_code IS NOT NULL
                  AND c.market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                  AND f.quarter=4
                  AND f.year BETWEEN :start_year AND :year
                GROUP BY c.market, f.year
                """
            ),
            {"start_year": start_year, "year": year},
        ).mappings().all()
        fee_rows = conn.execute(
            text(
                """
                SELECT c.market, af.bsns_year year,
                       COUNT(DISTINCT af.corp_code) audit_fee
                FROM companies c
                JOIN audit_fees af ON af.corp_code=c.corp_code
                WHERE c.stock_code IS NOT NULL
                  AND c.market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                  AND af.bsns_year BETWEEN :start_year AND :year
                GROUP BY c.market, af.bsns_year
                """
            ),
            {"start_year": start_year, "year": year},
        ).mappings().all()
        aud_rows = conn.execute(
            text(
                """
                SELECT c.market, a.bsns_year year,
                       COUNT(DISTINCT a.corp_code) auditor
                FROM companies c
                JOIN auditors a ON a.corp_code=c.corp_code
                WHERE c.stock_code IS NOT NULL
                  AND c.market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                  AND a.bsns_year BETWEEN :start_year AND :year
                GROUP BY c.market, a.bsns_year
                """
            ),
            {"start_year": start_year, "year": year},
        ).mappings().all()
        business_report_rows = conn.execute(
            text(
                """
                SELECT c.market, sd.bsns_year year,
                       COUNT(DISTINCT sd.corp_code) business_report
                FROM companies c
                JOIN source_documents sd ON sd.corp_code=c.corp_code
                WHERE c.stock_code IS NOT NULL
                  AND c.market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                  AND sd.source_type='business_report'
                  AND sd.bsns_year BETWEEN :start_year AND :year
                GROUP BY c.market, sd.bsns_year
                """
            ),
            {"start_year": start_year, "year": year},
        ).mappings().all()
        audit_report_rows = conn.execute(
            text(
                """
                SELECT c.market, sd.bsns_year year,
                       COUNT(DISTINCT sd.corp_code) audit_report
                FROM companies c
                JOIN source_documents sd ON sd.corp_code=c.corp_code
                WHERE c.stock_code IS NOT NULL
                  AND c.market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                  AND sd.source_type='audit_report'
                  AND sd.bsns_year BETWEEN :start_year AND :year
                GROUP BY c.market, sd.bsns_year
                """
            ),
            {"start_year": start_year, "year": year},
        ).mappings().all()
        pol_rows = conn.execute(
            text(
                """
                SELECT c.market, p.bsns_year year,
                       COUNT(DISTINCT p.corp_code) policy
                FROM companies c
                JOIN accounting_policy_items p ON p.corp_code=c.corp_code
                WHERE c.stock_code IS NOT NULL
                  AND c.market IN ('KOSPI', 'KOSDAQ', 'KONEX')
                  AND p.bsns_year BETWEEN :start_year AND :year
                  AND p.fs_div='CFS'
                GROUP BY c.market, p.bsns_year
                """
            ),
            {"start_year": start_year, "year": year},
        ).mappings().all()

    listed_by_market = {row["market"]: int(row["listed"] or 0) for row in listed_rows}
    yearly_markets = {
        y: {
            market: _empty_year_market_row(market, listed_by_market.get(market, 0))
            for market in listed_by_market
        }
        for y in years
    }
    for row in fin_rows:
        target = yearly_markets.setdefault(int(row["year"]), {}).setdefault(
            row["market"],
            _empty_year_market_row(row["market"], listed_by_market.get(row["market"], 0)),
        )
        target["financial_any"] = int(row["financial_any"] or 0)
        target["financial_cfs"] = int(row["financial_cfs"] or 0)
        target["financial_ofs"] = int(row["financial_ofs"] or 0)
    for row in fee_rows:
        target = yearly_markets.setdefault(int(row["year"]), {}).setdefault(
            row["market"],
            _empty_year_market_row(row["market"], listed_by_market.get(row["market"], 0)),
        )
        target["audit_fee"] = int(row["audit_fee"] or 0)
    for row in aud_rows:
        target = yearly_markets.setdefault(int(row["year"]), {}).setdefault(
            row["market"],
            _empty_year_market_row(row["market"], listed_by_market.get(row["market"], 0)),
        )
        target["auditor"] = int(row["auditor"] or 0)
    for row in business_report_rows:
        target = yearly_markets.setdefault(int(row["year"]), {}).setdefault(
            row["market"],
            _empty_year_market_row(row["market"], listed_by_market.get(row["market"], 0)),
        )
        target["business_report"] = int(row["business_report"] or 0)
    for row in audit_report_rows:
        target = yearly_markets.setdefault(int(row["year"]), {}).setdefault(
            row["market"],
            _empty_year_market_row(row["market"], listed_by_market.get(row["market"], 0)),
        )
        target["audit_report"] = int(row["audit_report"] or 0)
    for row in pol_rows:
        target = yearly_markets.setdefault(int(row["year"]), {}).setdefault(
            row["market"],
            _empty_year_market_row(row["market"], listed_by_market.get(row["market"], 0)),
        )
        target["policy"] = int(row["policy"] or 0)

    return {
        "year": year,
        "years_back": years_back,
        "required_years": years,
        "markets": {row["market"]: dict(row) for row in rows},
        "yearly_markets": yearly_markets,
        "policy_corps": int(policy_corps),
        "audit_fee_2025_corps": int(audit_fee_2025_corps),
    }


def readiness_verdict(snapshot: dict) -> dict:
    required_gaps: list[str] = []
    recommended_gaps: list[str] = []
    for market in CORE_MARKETS:
        row = snapshot.get("markets", {}).get(market, {})
        listed = int(row.get("listed") or 0)
        if pct(row.get("financial_any_2025"), listed) < 95.0:
            required_gaps.append("financial_any_2025")
        if pct(row.get("business_report_2025"), listed) < CORE_COVERAGE_THRESHOLD:
            required_gaps.append("business_report_2025")
        if pct(row.get("audit_report_2025"), listed) < CORE_COVERAGE_THRESHOLD:
            required_gaps.append("audit_report_2025")
        if pct(row.get("auditor_2025"), listed) < CORE_COVERAGE_THRESHOLD:
            required_gaps.append("auditor_2025")
        if pct(row.get("disclosure_recent"), listed) < 95.0:
            required_gaps.append("disclosure_recent")

    yearly_markets = snapshot.get("yearly_markets") or {}
    for y in snapshot.get("required_years") or []:
        rows_for_year = yearly_markets.get(y) or yearly_markets.get(str(y)) or {}
        for market in CORE_MARKETS:
            row = rows_for_year.get(market, {})
            listed = int(row.get("listed") or 0)
            if pct(row.get("financial_any"), listed) < CORE_COVERAGE_THRESHOLD:
                required_gaps.append(f"financial_any_{y}")
            if pct(row.get("business_report"), listed) < CORE_COVERAGE_THRESHOLD:
                required_gaps.append(f"business_report_{y}")
            if pct(row.get("audit_report"), listed) < CORE_COVERAGE_THRESHOLD:
                required_gaps.append(f"audit_report_{y}")
            if pct(row.get("auditor"), listed) < CORE_COVERAGE_THRESHOLD:
                required_gaps.append(f"auditor_{y}")

    if int(snapshot.get("policy_corps") or 0) < 100:
        recommended_gaps.append("accounting_policy")
    if int(snapshot.get("audit_fee_2025_corps") or 0) < 1000:
        recommended_gaps.append("audit_fee")

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


def _compact_year_ranges(years: list[int]) -> list[tuple[int, int]]:
    unique_years = sorted(set(int(y) for y in years))
    if not unique_years:
        return []
    ranges: list[tuple[int, int]] = []
    start = prev = unique_years[0]
    for year in unique_years[1:]:
        if year == prev + 1:
            prev = year
            continue
        ranges.append((start, prev))
        start = prev = year
    ranges.append((start, prev))
    return ranges


def backfill_plan(snapshot: dict) -> dict:
    financial_gap_years: list[int] = []
    disclosure_gap_years_by_market: dict[str, list[int]] = {m: [] for m in CORE_MARKETS}
    needs_auditors = False

    yearly_markets = snapshot.get("yearly_markets") or {}
    for y in snapshot.get("required_years") or []:
        rows_for_year = yearly_markets.get(y) or yearly_markets.get(str(y)) or {}
        for market in CORE_MARKETS:
            row = rows_for_year.get(market, {})
            listed = int(row.get("listed") or 0)
            if pct(row.get("financial_any"), listed) < CORE_COVERAGE_THRESHOLD:
                financial_gap_years.append(int(y))
            if (
                pct(row.get("business_report"), listed) < CORE_COVERAGE_THRESHOLD
                or pct(row.get("audit_report"), listed) < CORE_COVERAGE_THRESHOLD
            ):
                disclosure_gap_years_by_market[market].append(int(y))
                needs_auditors = True
            if pct(row.get("auditor"), listed) < CORE_COVERAGE_THRESHOLD:
                needs_auditors = True

    required_commands: list[str] = []
    for start, end in _compact_year_ranges(financial_gap_years):
        required_commands.append(
            f".venv/bin/kreports collect-all --year-from {start} --year-to {end}"
        )
    for market in CORE_MARKETS:
        for start, end in _compact_year_ranges(disclosure_gap_years_by_market[market]):
            required_commands.append(
                ".venv/bin/kreports collect-disclosures "
                f"--market {market} --start-date {start + 1}0101 --end-date {end + 1}1231"
            )
    if needs_auditors:
        required_commands.append(".venv/bin/kreports collect-auditors")

    recommended_commands: list[str] = []
    verdict = readiness_verdict(snapshot)
    year = int(snapshot.get("year") or max(snapshot.get("required_years") or [2025]))
    if "accounting_policy" in verdict["recommended_gaps"]:
        for market in CORE_MARKETS:
            recommended_commands.append(
                f".venv/bin/kreports collect-policies --market {market} --year {year} --limit 100"
            )
    if "audit_fee" in verdict["recommended_gaps"]:
        years = snapshot.get("required_years") or [year]
        for market in CORE_MARKETS:
            recommended_commands.append(
                ".venv/bin/kreports collect-audit-fees "
                f"--market {market} --year-from {min(years)} --year-to {max(years)}"
            )

    return {
        "required_commands": required_commands,
        "recommended_commands": recommended_commands,
        "note": "Run only on a maintainer machine with DART_API_KEY in the shell environment.",
    }


def dataset_completeness_snapshot(
    year: int = 2025,
    years_back: int = DEFAULT_YEARS_BACK,
    sample_size: int = 100,
) -> dict:
    """Strict MCP product-readiness view.

    This is intentionally stricter than auditor_readiness_snapshot. Readiness can
    say API jobs ran or broad tables exist; completeness asks whether a listed
    company can actually show the data the MCP promises.
    """
    years = required_years(year, years_back)
    start_year = years[0]
    with _engine_module.engine.connect() as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).all()
        }
        has_kam_table = any(
            name in table_names
            for name in ("report_sections", "kam_topics", "key_audit_matters", "audit_key_matters")
        )
        kam_rows = 0
        if "report_sections" in table_names:
            kam_rows = conn.execute(
                text(
                    "SELECT COUNT(*) FROM report_sections "
                    "WHERE source_type IN ('audit_report', 'business_report') AND section_key='kam'"
                )
            ).scalar() or 0
        elif "kam_topics" in table_names:
            kam_rows = conn.execute(text("SELECT COUNT(*) FROM kam_topics")).scalar() or 0
        elif "key_audit_matters" in table_names:
            kam_rows = conn.execute(text("SELECT COUNT(*) FROM key_audit_matters")).scalar() or 0
        elif "audit_key_matters" in table_names:
            kam_rows = conn.execute(text("SELECT COUNT(*) FROM audit_key_matters")).scalar() or 0

        rows = conn.execute(
            text(
                """
                WITH listed AS (
                  SELECT corp_code, stock_code, corp_name, market, induty_code
                  FROM companies
                  WHERE stock_code IS NOT NULL
                    AND market IN ('KOSPI', 'KOSDAQ')
                ),
                fin AS (
                  SELECT corp_code, COUNT(DISTINCT year) years
                  FROM financials
                  WHERE quarter=4 AND year BETWEEN :start_year AND :year
                  GROUP BY corp_code
                ),
                fee AS (
                  SELECT corp_code,
                         COUNT(DISTINCT bsns_year) years,
                         COUNT(DISTINCT CASE WHEN audit_fee_m IS NOT NULL THEN bsns_year END) fee_value_years,
                         COUNT(DISTINCT CASE WHEN audit_hours IS NOT NULL THEN bsns_year END) hour_value_years
                  FROM audit_fees
                  WHERE bsns_year BETWEEN :start_year AND :year
                  GROUP BY corp_code
                ),
                aud AS (
                  SELECT corp_code, COUNT(DISTINCT bsns_year) years
                  FROM auditors
                  WHERE bsns_year BETWEEN :start_year AND :year
                  GROUP BY corp_code
                ),
                pol AS (
                  SELECT corp_code, COUNT(DISTINCT item_key) items
                  FROM accounting_policy_items
                  WHERE bsns_year=:year
                  GROUP BY corp_code
                )
                SELECT l.corp_code, l.stock_code, l.corp_name, l.market, l.induty_code,
                       COALESCE(fin.years, 0) financial_years,
                       COALESCE(fee.years, 0) audit_fee_years,
                       COALESCE(fee.fee_value_years, 0) audit_fee_value_years,
                       COALESCE(fee.hour_value_years, 0) audit_hour_value_years,
                       COALESCE(aud.years, 0) auditor_years,
                       COALESCE(pol.items, 0) policy_items
                FROM listed l
                LEFT JOIN fin ON fin.corp_code=l.corp_code
                LEFT JOIN fee ON fee.corp_code=l.corp_code
                LEFT JOIN aud ON aud.corp_code=l.corp_code
                LEFT JOIN pol ON pol.corp_code=l.corp_code
                """
            ),
            {"start_year": start_year, "year": year},
        ).mappings().all()

    listed = len(rows)
    requirements = {
        "financial_5y": 0,
        "audit_fee_5y": 0,
        "audit_fee_value_5y": 0,
        "audit_hours_5y": 0,
        "auditor_5y": 0,
        "policy_current": 0,
        "core_without_policy": 0,
        "complete_company": 0,
    }

    incomplete_examples: list[dict] = []
    required_count = len(years)
    sample_rows = rows[: max(0, int(sample_size))]
    sample_complete = 0

    for idx, row in enumerate(rows):
        financial_ok = int(row["financial_years"] or 0) >= required_count
        fee_ok = int(row["audit_fee_years"] or 0) >= required_count
        fee_value_ok = int(row["audit_fee_value_years"] or 0) >= required_count
        hour_ok = int(row["audit_hour_value_years"] or 0) >= required_count
        auditor_ok = int(row["auditor_years"] or 0) >= required_count
        policy_ok = int(row["policy_items"] or 0) > 0
        core_ok = financial_ok and fee_ok and auditor_ok
        complete_ok = core_ok and fee_value_ok and hour_ok and policy_ok and has_kam_table and kam_rows > 0

        requirements["financial_5y"] += int(financial_ok)
        requirements["audit_fee_5y"] += int(fee_ok)
        requirements["audit_fee_value_5y"] += int(fee_value_ok)
        requirements["audit_hours_5y"] += int(hour_ok)
        requirements["auditor_5y"] += int(auditor_ok)
        requirements["policy_current"] += int(policy_ok)
        requirements["core_without_policy"] += int(core_ok)
        requirements["complete_company"] += int(complete_ok)

        if idx < len(sample_rows):
            sample_complete += int(complete_ok)

        if not complete_ok and len(incomplete_examples) < 20:
            missing = []
            if not financial_ok:
                missing.append("financial_5y")
            if not fee_ok:
                missing.append("audit_fee_5y")
            if not fee_value_ok:
                missing.append("audit_fee_value_5y")
            if not hour_ok:
                missing.append("audit_hours_5y")
            if not auditor_ok:
                missing.append("auditor_5y")
            if not policy_ok:
                missing.append("policy_current")
            if not has_kam_table or kam_rows == 0:
                missing.append("kam_body_topics")
            incomplete_examples.append(
                {
                    "stock_code": row["stock_code"],
                    "corp_name": row["corp_name"],
                    "market": row["market"],
                    "financial_years": int(row["financial_years"] or 0),
                    "audit_fee_years": int(row["audit_fee_years"] or 0),
                    "audit_fee_value_years": int(row["audit_fee_value_years"] or 0),
                    "audit_hour_value_years": int(row["audit_hour_value_years"] or 0),
                    "auditor_years": int(row["auditor_years"] or 0),
                    "policy_items": int(row["policy_items"] or 0),
                    "missing": missing,
                }
            )

    rates = {key: pct(value, listed) for key, value in requirements.items()}
    required_gaps = [
        key
        for key in (
            "financial_5y",
            "audit_fee_5y",
            "audit_fee_value_5y",
            "audit_hours_5y",
            "auditor_5y",
            "policy_current",
            "complete_company",
        )
        if rates[key] < COMPLETENESS_THRESHOLD
    ]
    if not has_kam_table or kam_rows == 0:
        required_gaps.append("kam_body_topics")

    return {
        "verdict": "pass" if not required_gaps else "fail",
        "year": year,
        "required_years": years,
        "threshold_pct": COMPLETENESS_THRESHOLD,
        "listed_companies": listed,
        "counts": requirements,
        "rates": rates,
        "sample_size": len(sample_rows),
        "sample_complete": sample_complete,
        "sample_complete_rate": pct(sample_complete, len(sample_rows)),
        "kam_body_topics": {
            "table_present": has_kam_table,
            "rows": int(kam_rows),
            "status": "persisted" if has_kam_table and kam_rows > 0 else "missing",
        },
        "required_gaps": sorted(set(required_gaps)),
        "incomplete_examples": incomplete_examples,
        "backfill_priorities": [
            "Persist accounting_policy_items for the full KOSPI/KOSDAQ target universe.",
            "Add a KAM/key_audit_matters table and parser; event-only KAM screening is not complete.",
            "Backfill auditors to 5-year coverage for listed companies.",
            "Backfill or classify missing audit_fee_m/audit_hours values by DART no_data vs parser gap.",
            "Fix financial 5-year denominator by listed-at-year/no_data status before claiming completeness.",
        ],
    }


def investor_dataset_readiness_snapshot(
    year: int = 2025,
    years_back: int = DEFAULT_YEARS_BACK,
    market: str | None = None,
) -> dict:
    """Investor-facing runtime readiness.

    Regular reports and financial facts are preload data. Ad-hoc disclosure
    bodies are not: the runtime DB only needs the disclosure list and title
    classification index. Source bodies are fetched through the user-keyed
    on-demand tool when a user asks to inspect one receipt in detail.
    """
    years = required_years(year, years_back)
    params: dict[str, object] = {
        "start_year": years[0],
        "year": int(year),
        "metric_count": len(INVESTOR_CORE_METRICS),
    }
    market_filter = ""
    if market:
        market_filter = " AND c.market=:market"
        params["market"] = market

    metric_placeholders = ", ".join(f":metric_{idx}" for idx, _ in enumerate(INVESTOR_CORE_METRICS))
    for idx, metric in enumerate(INVESTOR_CORE_METRICS):
        params[f"metric_{idx}"] = metric

    with _engine_module.engine.connect() as conn:
        listed = int(conn.execute(
            text(
                "SELECT COUNT(*) FROM companies c "
                "WHERE c.stock_code IS NOT NULL AND c.market IN ('KOSPI','KOSDAQ')"
                + market_filter
            ),
            params,
        ).scalar() or 0)
        yearly_rows = conn.execute(
            text(
                f"""
                WITH years(y) AS (
                  SELECT :start_year
                  UNION ALL
                  SELECT y + 1 FROM years WHERE y < :year
                ),
                listed AS (
                  SELECT c.corp_code
                  FROM companies c
                  WHERE c.stock_code IS NOT NULL
                    AND c.market IN ('KOSPI','KOSDAQ')
                    {market_filter}
                ),
                annual_report AS (
                  SELECT CAST(substr(d.report_nm, instr(d.report_nm, '(') + 1, 4) AS INTEGER) bsns_year,
                         d.corp_code
                  FROM disclosures d
                  JOIN listed l ON l.corp_code=d.corp_code
                  WHERE d.report_nm LIKE '%사업보고서 (%'
                    AND d.report_nm NOT LIKE '%제출기한연장%'
                    AND instr(d.report_nm, '(') > 0
                  GROUP BY bsns_year, d.corp_code
                ),
                fin_fs AS (
                  SELECT ffc.bsns_year, ffc.corp_code, ffc.fs_div,
                         COUNT(DISTINCT ffc.metric_key) metric_count
                  FROM financial_facts_compact ffc
                  JOIN listed l ON l.corp_code=ffc.corp_code
                  WHERE ffc.bsns_year BETWEEN :start_year AND :year
                    AND ffc.fs_div IN ('CFS', 'OFS')
                    AND ffc.metric_key IN ({metric_placeholders})
                  GROUP BY ffc.bsns_year, ffc.corp_code, ffc.fs_div
                ),
                fin_metric AS (
                  SELECT bsns_year, corp_code,
                         MAX(CASE WHEN metric_count >= :metric_count THEN 1 ELSE 0 END) has_core
                  FROM fin_fs
                  GROUP BY bsns_year, corp_code
                ),
                financial_eligible AS (
                  SELECT y.y year, l.corp_code
                  FROM years y
                  JOIN listed l
                  LEFT JOIN annual_report ar ON ar.bsns_year=y.y AND ar.corp_code=l.corp_code
                  LEFT JOIN fin_metric fm ON fm.bsns_year=y.y AND fm.corp_code=l.corp_code
                  WHERE ar.corp_code IS NOT NULL OR fm.corp_code IS NOT NULL
                  GROUP BY y.y, l.corp_code
                ),
                financial_eligible_yearly AS (
                  SELECT year, COUNT(DISTINCT corp_code) financial_eligible_companies
                  FROM financial_eligible
                  GROUP BY year
                ),
                disclosure_first AS (
                  SELECT d.corp_code,
                         MIN(CAST(strftime('%Y', d.disc_date) AS INTEGER)) first_filing_year
                  FROM disclosures d
                  JOIN listed l ON l.corp_code=d.corp_code
                  GROUP BY d.corp_code
                ),
                disclosure_eligible AS (
                  SELECT y.y year, l.corp_code
                  FROM years y
                  JOIN listed l
                  JOIN disclosure_first df ON df.corp_code=l.corp_code
                  WHERE df.first_filing_year <= y.y
                  GROUP BY y.y, l.corp_code
                ),
                disclosure_eligible_yearly AS (
                  SELECT year, COUNT(DISTINCT corp_code) disclosure_eligible_companies
                  FROM disclosure_eligible
                  GROUP BY year
                ),
                disc AS (
                  SELECT CAST(strftime('%Y', d.disc_date) AS INTEGER) filing_year,
                         d.corp_code
                  FROM disclosures d
                  JOIN listed l ON l.corp_code=d.corp_code
                  WHERE CAST(strftime('%Y', d.disc_date) AS INTEGER) BETWEEN :start_year AND :year
                  GROUP BY filing_year, d.corp_code
                ),
                events AS (
                  SELECT CAST(strftime('%Y', de.event_date) AS INTEGER) event_year,
                         de.corp_code,
                         COUNT(*) event_count
                  FROM disclosure_events de
                  JOIN listed l ON l.corp_code=de.corp_code
                  WHERE CAST(strftime('%Y', de.event_date) AS INTEGER) BETWEEN :start_year AND :year
                  GROUP BY event_year, de.corp_code
                ),
                event_yearly AS (
                  SELECT event_year,
                         COUNT(DISTINCT corp_code) event_companies,
                         SUM(event_count) event_rows
                  FROM events
                  GROUP BY event_year
                )
                SELECT y.y year,
                       :listed listed,
                       COALESCE(MAX(fey.financial_eligible_companies), 0) financial_eligible_companies,
                       COALESCE(MAX(dey.disclosure_eligible_companies), 0) disclosure_eligible_companies,
                       COUNT(DISTINCT CASE WHEN fm.has_core = 1 THEN fm.corp_code END) compact_core_companies,
                       COUNT(DISTINCT disc.corp_code) disclosure_list_companies,
                       COALESCE(MAX(ey.event_companies), 0) disclosure_event_companies,
                       COALESCE(MAX(ey.event_rows), 0) disclosure_event_rows
                FROM years y
                LEFT JOIN fin_metric fm ON fm.bsns_year=y.y
                LEFT JOIN financial_eligible_yearly fey ON fey.year=y.y
                LEFT JOIN disclosure_eligible_yearly dey ON dey.year=y.y
                LEFT JOIN disc ON disc.filing_year=y.y
                LEFT JOIN event_yearly ey ON ey.event_year=y.y
                GROUP BY y.y
                ORDER BY y.y
                """
            ),
            {**params, "listed": listed},
        ).mappings().all()
        on_demand_cached_bodies = int(conn.execute(
            text(
                "SELECT COUNT(*) FROM source_documents sd JOIN companies c ON c.corp_code=sd.corp_code "
                "WHERE sd.source_type='event_disclosure' AND sd.bsns_year BETWEEN :start_year AND :year"
                + market_filter
            ),
            params,
        ).scalar() or 0)

    rows = []
    required_gaps: list[str] = []
    recommended_gaps: list[str] = []
    for row in yearly_rows:
        row_dict = dict(row)
        financial_denominator = row_dict["financial_eligible_companies"]
        disclosure_denominator = row_dict["disclosure_eligible_companies"]
        row_dict["compact_core_coverage_pct"] = pct(row_dict["compact_core_companies"], financial_denominator)
        row_dict["disclosure_list_coverage_pct"] = pct(row_dict["disclosure_list_companies"], disclosure_denominator)
        row_dict["event_company_coverage_pct"] = pct(row_dict["disclosure_event_companies"], disclosure_denominator)
        rows.append(row_dict)
        if financial_denominator and row_dict["compact_core_coverage_pct"] < CORE_COVERAGE_THRESHOLD:
            required_gaps.append(f"financial_compact_core_{row_dict['year']}")
        if disclosure_denominator and row_dict["disclosure_list_coverage_pct"] < CORE_COVERAGE_THRESHOLD:
            required_gaps.append(f"disclosure_list_{row_dict['year']}")
        if row_dict["disclosure_event_rows"] == 0:
            recommended_gaps.append(f"disclosure_event_index_{row_dict['year']}")

    return {
        "verdict": "pass" if not required_gaps else "fail",
        "year": int(year),
        "years_back": int(years_back),
        "required_years": years,
        "market": market,
        "listed_companies": listed,
        "core_financial_metrics": list(INVESTOR_CORE_METRICS),
        "disclosure_body_storage_policy": "on_demand_user_key",
        "disclosure_body_required_for_runtime": False,
        "on_demand_cached_disclosure_bodies": on_demand_cached_bodies,
        "yearly": rows,
        "required_gaps": required_gaps,
        "recommended_gaps": recommended_gaps,
        "recommended_next": [
            "Backfill disclosures list metadata for years with disclosure_list gaps; do not preload all ad-hoc disclosure bodies.",
            "Run rebuild-disclosure-events for years with disclosure_event_index gaps.",
            "Use search_dataset with dataset=evidence_documents for locally indexed receipt evidence, then follow the DART source link when needed.",
        ],
    }


def auditor_feature_readiness_snapshot(year: int = 2025, market: str | None = None) -> dict:
    """Feature-level readiness for auditor-facing MCP tools."""
    params: dict[str, object] = {"year": year}
    market_filter = ""
    if market:
        market_filter = " AND c.market=:market"
        params["market"] = market
    with _engine_module.engine.connect() as conn:
        table_names = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all()
        }

        def scalar(sql: str, extra: dict | None = None) -> int:
            merged = dict(params)
            if extra:
                merged.update(extra)
            try:
                return int(conn.execute(text(sql), merged).scalar() or 0)
            except Exception:
                return 0

        listed = scalar(
            "SELECT COUNT(*) FROM companies c WHERE c.stock_code IS NOT NULL "
            "AND c.market IN ('KOSPI','KOSDAQ')" + market_filter
        )
        raw_document_condition = (
            "sd.content_type!='derived_report_sections' "
            "AND COALESCE(sd.storage_status, 'inline')!='derived_only' "
            "AND ((sd.raw_content IS NOT NULL AND sd.raw_content!='') "
            "OR (sd.storage_uri IS NOT NULL AND sd.storage_uri!=''))"
        )
        derived_document_condition = (
            "sd.content_type!='derived_report_sections' "
            "AND (COALESCE(sd.storage_status, '')='derived_only' "
            "OR ((sd.raw_content IS NULL OR sd.raw_content='') "
            "AND (sd.storage_uri IS NULL OR sd.storage_uri='')))"
        )
        raw_source_docs = (
            scalar(
                "SELECT COUNT(*) FROM source_documents sd JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {raw_document_condition} AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        raw_source_companies = (
            scalar(
                "SELECT COUNT(DISTINCT sd.corp_code) FROM source_documents sd "
                "JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {raw_document_condition} AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        raw_business_docs = (
            scalar(
                "SELECT COUNT(*) FROM source_documents sd JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {raw_document_condition} "
                "AND sd.source_type='business_report' AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        raw_business_companies = (
            scalar(
                "SELECT COUNT(DISTINCT sd.corp_code) FROM source_documents sd "
                "JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {raw_document_condition} "
                "AND sd.source_type='business_report' AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        raw_audit_docs = (
            scalar(
                "SELECT COUNT(*) FROM source_documents sd JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {raw_document_condition} "
                "AND sd.source_type='audit_report' AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        raw_audit_companies = (
            scalar(
                "SELECT COUNT(DISTINCT sd.corp_code) FROM source_documents sd "
                "JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {raw_document_condition} "
                "AND sd.source_type='audit_report' AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        derived_source_docs = (
            scalar(
                "SELECT COUNT(*) FROM source_documents sd JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {derived_document_condition} AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        derived_business_docs = (
            scalar(
                "SELECT COUNT(*) FROM source_documents sd JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {derived_document_condition} "
                "AND sd.source_type='business_report' AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        derived_audit_docs = (
            scalar(
                "SELECT COUNT(*) FROM source_documents sd JOIN companies c ON c.corp_code=sd.corp_code "
                f"WHERE {derived_document_condition} "
                "AND sd.source_type='audit_report' AND sd.bsns_year=:year" + market_filter
            )
            if "source_documents" in table_names
            else 0
        )
        kam_sections = (
            scalar(
                "SELECT COUNT(*) FROM report_sections rs JOIN companies c ON c.corp_code=rs.corp_code "
                "WHERE rs.section_key='kam' AND rs.bsns_year=:year" + market_filter
            )
            if "report_sections" in table_names
            else 0
        )
        kam_companies = (
            scalar(
                "SELECT COUNT(DISTINCT rs.corp_code) FROM report_sections rs "
                "JOIN companies c ON c.corp_code=rs.corp_code "
                "WHERE rs.section_key='kam' AND rs.bsns_year=:year" + market_filter
            )
            if "report_sections" in table_names
            else 0
        )
        kam_reason = (
            scalar(
                "SELECT COUNT(*) FROM report_sections rs JOIN companies c ON c.corp_code=rs.corp_code "
                "WHERE rs.section_key='kam' AND rs.bsns_year=:year "
                "AND (rs.body_text LIKE '%핵심감사사항으로 결정%' "
                "OR rs.body_text LIKE '%중요한 왜곡표시위험%' OR rs.body_text LIKE '%경영진의 판단%')"
                + market_filter
            )
            if "report_sections" in table_names
            else 0
        )
        kam_procedure = (
            scalar(
                "SELECT COUNT(*) FROM report_sections rs JOIN companies c ON c.corp_code=rs.corp_code "
                "WHERE rs.section_key='kam' AND rs.bsns_year=:year "
                "AND (rs.body_text LIKE '%감사절차%' OR rs.body_text LIKE '%감사에서 다루어진 방법%' "
                "OR rs.body_text LIKE '%수행하였습니다%')"
                + market_filter
            )
            if "report_sections" in table_names
            else 0
        )
        matter_sections = (
            scalar(
                "SELECT COUNT(*) FROM report_sections rs JOIN companies c ON c.corp_code=rs.corp_code "
                "WHERE rs.source_type='audit_report' "
                "AND rs.section_key IN ('emphasis','other_matter','going_concern') "
                "AND rs.bsns_year=:year" + market_filter
            )
            if "report_sections" in table_names
            else 0
        )
        source_basis = {
            "kam_sections": "report_sections",
            "audit_report_matters": "report_sections",
            "accounting_policy_changes": "accounting_note_chapters",
        }
        if kam_sections == 0 and "evidence_documents" in table_names:
            evidence_rows = conn.execute(
                text(
                    "SELECT ed.corp_code, ed.normalized_text "
                    "FROM evidence_documents ed "
                    "JOIN companies c ON c.corp_code=ed.corp_code "
                    "WHERE ed.source_type='audit_report' AND ed.bsns_year=:year "
                    "AND ed.normalized_text LIKE '%report_section/kam%'" + market_filter
                ),
                params,
            ).mappings().all()
            kam_companies_seen = set()
            kam_sections = 0
            kam_reason = 0
            kam_procedure = 0
            for row in evidence_rows:
                kam_body = _evidence_kam_body(row["normalized_text"])
                if not kam_body or _contains_any(kam_body, _NO_KAM_PATTERNS):
                    continue
                kam_sections += 1
                kam_companies_seen.add(row["corp_code"])
                if _contains_any(kam_body, _KAM_REASON_PATTERNS):
                    kam_reason += 1
                if _contains_any(kam_body, _KAM_PROCEDURE_PATTERNS):
                    kam_procedure += 1
            kam_companies = len(kam_companies_seen)
            source_basis["kam_sections"] = "evidence_documents"
        if matter_sections == 0 and "evidence_documents" in table_names:
            matter_sections = scalar(
                "SELECT COUNT(*) FROM evidence_documents ed "
                "JOIN companies c ON c.corp_code=ed.corp_code "
                "WHERE ed.source_type='audit_report' AND ed.bsns_year=:year "
                "AND (ed.normalized_text LIKE '%report_section/emphasis%' "
                "OR ed.normalized_text LIKE '%report_section/other_matter%' "
                "OR ed.normalized_text LIKE '%report_section/going_concern%')"
                + market_filter
            )
            source_basis["audit_report_matters"] = "evidence_documents"
        note_chapters = (
            scalar(
                "SELECT COUNT(*) FROM accounting_note_chapters anc JOIN companies c ON c.corp_code=anc.corp_code "
                "WHERE anc.bsns_year=:year" + market_filter
            )
            if "accounting_note_chapters" in table_names
            else 0
        )
        note_chapter_companies = (
            scalar(
                "SELECT COUNT(DISTINCT anc.corp_code) FROM accounting_note_chapters anc "
                "JOIN companies c ON c.corp_code=anc.corp_code "
                "WHERE anc.bsns_year=:year" + market_filter
            )
            if "accounting_note_chapters" in table_names
            else 0
        )
        policy_change_coverage = _policy_change_readiness_coverage(
            conn,
            params=params,
            market_filter=market_filter,
            has_note_chapters="accounting_note_chapters" in table_names,
        )
        policy_items = (
            scalar(
                "SELECT COUNT(*) FROM accounting_policy_items api JOIN companies c ON c.corp_code=api.corp_code "
                "WHERE api.bsns_year=:year" + market_filter
            )
            if "accounting_policy_items" in table_names
            else 0
        )
        policy_item_companies = (
            scalar(
                "SELECT COUNT(DISTINCT api.corp_code) FROM accounting_policy_items api "
                "JOIN companies c ON c.corp_code=api.corp_code "
                "WHERE api.bsns_year=:year" + market_filter
            )
            if "accounting_policy_items" in table_names
            else 0
        )
        procedure_items = (
            scalar(
                "SELECT COUNT(*) FROM audit_procedure_items api JOIN companies c ON c.corp_code=api.corp_code "
                "WHERE api.bsns_year=:year" + market_filter
            )
            if "audit_procedure_items" in table_names
            else 0
        )
        procedure_item_companies = (
            scalar(
                "SELECT COUNT(DISTINCT api.corp_code) FROM audit_procedure_items api "
                "JOIN companies c ON c.corp_code=api.corp_code "
                "WHERE api.bsns_year=:year" + market_filter
            )
            if "audit_procedure_items" in table_names
            else 0
        )

    def coverage_status(count: int, denominator: int, threshold: float = FEATURE_COVERAGE_THRESHOLD) -> str:
        if int(count or 0) <= 0:
            return "missing"
        if pct(count, denominator) < threshold:
            return "degraded"
        return "usable"

    feature_status = {
        "raw_source_documents": coverage_status(raw_source_companies, listed),
        "kam_sections": coverage_status(kam_companies, listed),
        "kam_reason_hints": "usable" if kam_reason > 0 and pct(kam_reason, kam_sections) >= FEATURE_COVERAGE_THRESHOLD else ("degraded" if kam_reason > 0 else "missing"),
        "kam_procedure_hints": "usable" if kam_procedure > 0 and pct(kam_procedure, kam_sections) >= FEATURE_COVERAGE_THRESHOLD else ("degraded" if kam_procedure > 0 else "missing"),
        "audit_report_matters": "usable" if matter_sections > 0 else "missing",
        "accounting_notes": coverage_status(note_chapter_companies, listed),
        "accounting_policy_changes": coverage_status(
            policy_change_coverage["policy_change_comparable_companies"],
            listed,
        ),
        "accounting_policy_items": coverage_status(policy_item_companies, listed),
        "audit_procedure_items": coverage_status(procedure_item_companies, listed),
    }
    missing = [key for key, status in feature_status.items() if status == "missing"]
    degraded = [key for key, status in feature_status.items() if status == "degraded"]
    return {
        "verdict": "pass" if not missing and not degraded else "conditional",
        "year": year,
        "market": market,
        "listed_companies": listed,
        "source_basis": source_basis,
        "counts": {
            "raw_source_documents": raw_source_docs,
            "raw_source_document_companies": raw_source_companies,
            "raw_business_documents": raw_business_docs,
            "raw_business_document_companies": raw_business_companies,
            "raw_audit_documents": raw_audit_docs,
            "raw_audit_document_companies": raw_audit_companies,
            "derived_source_document_placeholders": derived_source_docs,
            "derived_business_document_placeholders": derived_business_docs,
            "derived_audit_document_placeholders": derived_audit_docs,
            "kam_sections": kam_sections,
            "kam_companies": kam_companies,
            "kam_reason_hints": kam_reason,
            "kam_procedure_hints": kam_procedure,
            "audit_report_matters": matter_sections,
            "accounting_note_chapters": note_chapters,
            "accounting_note_chapter_companies": note_chapter_companies,
            "accounting_policy_change_chapters": policy_change_coverage["policy_change_chapters"],
            "accounting_policy_change_companies": policy_change_coverage["policy_change_companies"],
            "policy_change_comparable_companies": policy_change_coverage["policy_change_comparable_companies"],
            "policy_change_excluded_unproven": policy_change_coverage["policy_change_excluded_unproven"],
            "policy_change_excluded_not_comparable": policy_change_coverage["policy_change_excluded_not_comparable"],
            "policy_change_excluded_missing_requested_year": policy_change_coverage["policy_change_excluded_missing_requested_year"],
            "accounting_policy_items": policy_items,
            "accounting_policy_item_companies": policy_item_companies,
            "audit_procedure_items": procedure_items,
            "audit_procedure_item_companies": procedure_item_companies,
        },
        "rates": {
            "raw_source_company_coverage": pct(raw_source_companies, listed),
            "raw_business_company_coverage": pct(raw_business_companies, listed),
            "raw_audit_company_coverage": pct(raw_audit_companies, listed),
            "kam_company_coverage": pct(kam_companies, listed),
            "accounting_note_company_coverage": pct(note_chapter_companies, listed),
            "accounting_policy_change_company_coverage": pct(
                policy_change_coverage["policy_change_comparable_companies"],
                listed,
            ),
            "accounting_policy_company_coverage": pct(policy_item_companies, listed),
            "audit_procedure_company_coverage": pct(procedure_item_companies, listed),
            "kam_reason_to_kam": pct(kam_reason, kam_sections),
            "kam_procedure_to_kam": pct(kam_procedure, kam_sections),
        },
        "feature_status": feature_status,
        "missing_features": missing,
        "degraded_features": degraded,
        "recommended_next": [
            (
                "Collect or restore raw DART document bodies before parser repair; current source_documents are derived placeholders."
                if raw_source_docs == 0 and derived_source_docs > 0
                else "Run derived-first backfill: refresh extractors from cached/externalized raw documents before collecting more raw bodies."
            ),
            "Rebuild evidence_documents so MCP narrative search uses compact normalized evidence instead of source_documents raw XML.",
            "Backfill compact structured tables: financials, auditors, audit_fee, audit_hours, policy items, and audit procedures.",
            "Investigate parser gaps where KAM exists but reason/procedure hints are absent.",
        ],
    }


def audit_kam_quality_snapshot(
    *,
    year: int = 2025,
    market: str | None = None,
    min_body_length: int = 300,
    limit: int = 50,
) -> dict:
    """Quality view for audit-report KAM sections and repair targeting."""
    params: dict[str, object] = {
        "year": int(year),
        "min_body_length": int(min_body_length),
        "limit": int(limit),
    }
    market_filter = ""
    if market:
        market_filter = " AND c.market=:market"
        params["market"] = market

    reason_condition = (
        "rs.body_text LIKE '%핵심감사사항으로 결정%' "
        "OR rs.body_text LIKE '%핵심 감사사항으로 결정%' "
        "OR rs.body_text LIKE '%핵심감사사항으로 선정한 이유%' "
        "OR rs.body_text LIKE '%중요한 왜곡표시위험%' "
        "OR rs.body_text LIKE '%유의적인 위험%' "
        "OR rs.body_text LIKE '%추정의 불확실성%' "
        "OR rs.body_text LIKE '%경영진의 판단%'"
    )
    procedure_condition = (
        "rs.body_text LIKE '%감사절차%' "
        "OR rs.body_text LIKE '%감사에서 다루어진 방법%' "
        "OR rs.body_text LIKE '%수행하였습니다%' "
        "OR rs.body_text LIKE '%문서검사%' "
        "OR rs.body_text LIKE '%내부통제%' "
        "OR rs.body_text LIKE '%재계산%' "
        "OR rs.body_text LIKE '%대사%'"
    )
    no_kam_condition = (
        "rs.body_text LIKE '%보고해야 할 핵심감사사항이 없%' "
        "OR rs.body_text LIKE '%보고할 핵심감사사항이 없%' "
        "OR rs.body_text LIKE '%핵심감사사항이 없다고 결정%'"
    )

    with _engine_module.engine.connect() as conn:
        counts = conn.execute(
            text(
                f"""
                SELECT
                  SUM(CASE WHEN NOT ({no_kam_condition}) THEN 1 ELSE 0 END) AS kam_sections,
                  COUNT(DISTINCT CASE WHEN NOT ({no_kam_condition}) THEN rs.corp_code END) AS kam_companies,
                  SUM(CASE WHEN {no_kam_condition} THEN 1 ELSE 0 END) AS no_kam_sections,
                  SUM(CASE WHEN NOT ({no_kam_condition}) AND COALESCE(rs.body_length, LENGTH(rs.body_text)) < :min_body_length THEN 1 ELSE 0 END)
                    AS short_kam_sections,
                  SUM(CASE WHEN NOT ({no_kam_condition}) AND ({reason_condition}) THEN 1 ELSE 0 END) AS reason_hints,
                  SUM(CASE WHEN NOT ({no_kam_condition}) AND ({procedure_condition}) THEN 1 ELSE 0 END) AS procedure_hints,
                  SUM(CASE WHEN NOT ({no_kam_condition}) AND api.id IS NOT NULL THEN 1 ELSE 0 END) AS indexed_procedure_sections,
                  COUNT(DISTINCT CASE WHEN NOT ({no_kam_condition}) AND api.id IS NOT NULL THEN rs.corp_code END) AS indexed_procedure_companies
                FROM report_sections rs
                JOIN companies c ON c.corp_code=rs.corp_code
                LEFT JOIN audit_procedure_items api
                  ON api.corp_code=rs.corp_code
                 AND api.bsns_year=rs.bsns_year
                 AND api.rcept_no=rs.rcept_no
                WHERE rs.bsns_year=:year
                  AND rs.source_type='audit_report'
                  AND rs.section_key='kam'
                  {market_filter}
                """
            ),
            params,
        ).mappings().first() or {}

        candidates = conn.execute(
            text(
                f"""
                SELECT
                  rs.corp_code,
                  c.stock_code,
                  c.corp_name,
                  c.market,
                  c.induty_code,
                  rs.bsns_year,
                  rs.rcept_no,
                  rs.dcm_no,
                  rs.section_title,
                  COALESCE(rs.body_length, LENGTH(rs.body_text)) AS body_length,
                  CASE WHEN {reason_condition} THEN 1 ELSE 0 END AS has_reason_hint,
                  CASE WHEN {procedure_condition} THEN 1 ELSE 0 END AS has_procedure_hint,
                  COUNT(api.id) AS procedure_item_count,
                  substr(rs.body_text, 1, 260) AS body_head
                FROM report_sections rs
                JOIN companies c ON c.corp_code=rs.corp_code
                LEFT JOIN audit_procedure_items api
                  ON api.corp_code=rs.corp_code
                 AND api.bsns_year=rs.bsns_year
                 AND api.rcept_no=rs.rcept_no
                WHERE rs.bsns_year=:year
                  AND rs.source_type='audit_report'
                  AND rs.section_key='kam'
                  AND NOT ({no_kam_condition})
                  {market_filter}
                GROUP BY rs.id
                HAVING body_length < :min_body_length
                    OR has_reason_hint=0
                    OR has_procedure_hint=0
                    OR procedure_item_count=0
                ORDER BY
                  CASE WHEN body_length < :min_body_length THEN 0 ELSE 1 END,
                  has_reason_hint ASC,
                  has_procedure_hint ASC,
                  procedure_item_count ASC,
                  body_length ASC,
                  c.market,
                  c.corp_name
                LIMIT :limit
                """
            ),
            params,
        ).mappings().all()

        source_basis = "report_sections"
        if int(counts.get("kam_sections") or 0) == 0:
            source_basis = "evidence_documents"
            evidence_rows = conn.execute(
                text(
                    f"""
                    SELECT
                      ed.corp_code,
                      c.stock_code,
                      c.corp_name,
                      c.market,
                      c.induty_code,
                      ed.bsns_year,
                      ed.rcept_no,
                      ed.dcm_no,
                      ed.title AS section_title,
                      ed.normalized_text,
                      COUNT(api.id) AS procedure_item_count,
                      substr(ed.normalized_text, instr(ed.normalized_text, 'report_section/kam'), 260) AS body_head
                    FROM evidence_documents ed
                    JOIN companies c ON c.corp_code=ed.corp_code
                    LEFT JOIN audit_procedure_items api
                      ON api.corp_code=ed.corp_code
                     AND api.bsns_year=ed.bsns_year
                     AND (api.rcept_no=ed.rcept_no OR api.source_type=ed.source_type)
                    WHERE ed.bsns_year=:year
                      AND ed.source_type='audit_report'
                      AND ed.normalized_text LIKE '%report_section/kam%'
                      {market_filter}
                    GROUP BY ed.id
                    """
                ),
                params,
            ).mappings().all()
            candidate_rows = []
            kam_companies_seen = set()
            indexed_companies_seen = set()
            computed_counts = {
                "kam_sections": 0,
                "kam_companies": 0,
                "no_kam_sections": 0,
                "short_kam_sections": 0,
                "reason_hints": 0,
                "procedure_hints": 0,
                "indexed_procedure_sections": 0,
                "indexed_procedure_companies": 0,
            }
            for row in evidence_rows:
                kam_body = _evidence_kam_body(row["normalized_text"])
                if not kam_body:
                    continue
                is_no_kam = _contains_any(kam_body, _NO_KAM_PATTERNS)
                body_length = len(kam_body)
                has_reason = _contains_any(kam_body, _KAM_REASON_PATTERNS)
                has_procedure = _contains_any(kam_body, _KAM_PROCEDURE_PATTERNS)
                procedure_count = int(row["procedure_item_count"] or 0)
                if is_no_kam:
                    computed_counts["no_kam_sections"] += 1
                    continue
                computed_counts["kam_sections"] += 1
                kam_companies_seen.add(row["corp_code"])
                if body_length < int(min_body_length):
                    computed_counts["short_kam_sections"] += 1
                if has_reason:
                    computed_counts["reason_hints"] += 1
                if has_procedure:
                    computed_counts["procedure_hints"] += 1
                if procedure_count > 0:
                    computed_counts["indexed_procedure_sections"] += 1
                    indexed_companies_seen.add(row["corp_code"])
                if (
                    body_length < int(min_body_length)
                    or not has_reason
                    or not has_procedure
                    or procedure_count <= 0
                ):
                    candidate_rows.append({
                        **dict(row),
                        "body_length": body_length,
                        "has_reason_hint": 1 if has_reason else 0,
                        "has_procedure_hint": 1 if has_procedure else 0,
                        "body_head": kam_body[:260],
                    })
            computed_counts["kam_companies"] = len(kam_companies_seen)
            computed_counts["indexed_procedure_companies"] = len(indexed_companies_seen)
            counts = computed_counts
            candidates = sorted(
                candidate_rows,
                key=lambda row: (
                    0 if int(row["body_length"] or 0) < int(min_body_length) else 1,
                    int(row["has_reason_hint"] or 0),
                    int(row["has_procedure_hint"] or 0),
                    int(row["procedure_item_count"] or 0),
                    int(row["body_length"] or 0),
                    row["market"] or "",
                    row["corp_name"] or "",
                ),
            )[: int(limit)]

    kam_sections = int(counts.get("kam_sections") or 0)
    reason_hints = int(counts.get("reason_hints") or 0)
    procedure_hints = int(counts.get("procedure_hints") or 0)
    indexed_procedure_sections = int(counts.get("indexed_procedure_sections") or 0)
    repair_candidates = []
    for row in candidates:
        body_length = int(row["body_length"] or 0)
        has_reason = bool(row["has_reason_hint"])
        has_procedure = bool(row["has_procedure_hint"])
        procedure_count = int(row["procedure_item_count"] or 0)
        gap_reasons = []
        if body_length < int(min_body_length):
            gap_reasons.append("short_body")
        if not has_reason:
            gap_reasons.append("missing_reason_hint")
        if not has_procedure:
            gap_reasons.append("missing_procedure_hint")
        if procedure_count <= 0:
            gap_reasons.append("missing_indexed_procedures")
        repair_candidates.append({
            "source_basis": source_basis,
            "corp_code": row["corp_code"],
            "stock_code": row["stock_code"],
            "corp_name": row["corp_name"],
            "market": row["market"],
            "induty_code": row["induty_code"],
            "bsns_year": int(row["bsns_year"]),
            "rcept_no": row["rcept_no"],
            "dcm_no": row["dcm_no"],
            "section_title": row["section_title"],
            "body_length": body_length,
            "has_reason_hint": has_reason,
            "has_procedure_hint": has_procedure,
            "procedure_item_count": procedure_count,
            "gap_reasons": gap_reasons,
            "body_head": row["body_head"],
        })

    rates = {
        "reason_hint_coverage": pct(reason_hints, kam_sections),
        "procedure_hint_coverage": pct(procedure_hints, kam_sections),
        "indexed_procedure_coverage": pct(indexed_procedure_sections, kam_sections),
        "short_body_rate": pct(int(counts.get("short_kam_sections") or 0), kam_sections),
    }
    required_gaps = []
    if kam_sections <= 0:
        required_gaps.append("kam_sections")
    if rates["reason_hint_coverage"] < 50.0:
        required_gaps.append("kam_reason_hints")
    if rates["procedure_hint_coverage"] < 50.0:
        required_gaps.append("kam_procedure_hints")
    if rates["indexed_procedure_coverage"] < 50.0:
        required_gaps.append("audit_procedure_items")
    if rates["short_body_rate"] > 30.0:
        required_gaps.append("short_kam_body")

    return {
        "verdict": "pass" if not required_gaps else "fail",
        "year": int(year),
        "market": market,
        "min_body_length": int(min_body_length),
        "source_basis": source_basis,
        "counts": {
            "kam_sections": kam_sections,
            "kam_companies": int(counts.get("kam_companies") or 0),
            "no_kam_sections": int(counts.get("no_kam_sections") or 0),
            "short_kam_sections": int(counts.get("short_kam_sections") or 0),
            "reason_hints": reason_hints,
            "procedure_hints": procedure_hints,
            "indexed_procedure_sections": indexed_procedure_sections,
            "indexed_procedure_companies": int(counts.get("indexed_procedure_companies") or 0),
        },
        "rates": rates,
        "required_gaps": required_gaps,
        "repair_candidates": repair_candidates,
        "recommended_next": [
            "Reparse only repair_candidates from original DART audit-report attachments when API quota is available.",
            "Run index-audit-procedures after KAM section repair.",
            "Rebuild evidence_documents after report_sections and audit_procedure_items are repaired.",
        ],
    }


def _original_disclosure_rcept_no(rcept_no: str | None, dcm_no: str | None = None) -> str | None:
    """Return the 14-digit DART disclosure receipt from derived document ids."""
    for value in (rcept_no, dcm_no):
        match = re.search(r"\d{14}", value or "")
        if match:
            return match.group(0)
    return None


def kam_repair_targets_snapshot(
    *,
    year: int = 2025,
    market: str | None = None,
    min_body_length: int = 300,
    limit: int = 50,
    include_index_only: bool = False,
) -> dict:
    """Select KAM rows that need DART re-collection, not just local re-indexing."""
    quality = audit_kam_quality_snapshot(
        year=year,
        market=market,
        min_body_length=min_body_length,
        limit=limit,
    )
    repair_gaps = {"short_body", "missing_reason_hint", "missing_procedure_hint"}
    excluded_gap_reasons: set[str] = set()
    targets: list[dict] = []
    seen: set[str] = set()
    for candidate in quality.get("repair_candidates") or []:
        gap_reasons = set(candidate.get("gap_reasons") or [])
        needs_dart_repair = bool(gap_reasons & repair_gaps)
        if not include_index_only and not needs_dart_repair:
            excluded_gap_reasons.update(gap_reasons)
            continue
        source_rcept_no = _original_disclosure_rcept_no(
            candidate.get("rcept_no"),
            candidate.get("dcm_no"),
        )
        if not source_rcept_no:
            excluded_gap_reasons.add("unresolved_source_rcept_no")
            continue
        if source_rcept_no in seen:
            continue
        seen.add(source_rcept_no)
        targets.append({
            **candidate,
            "source_rcept_no": source_rcept_no,
            "repair_action": "collect_report_sections_for_disclosure",
        })

    return {
        "year": int(year),
        "market": market,
        "min_body_length": int(min_body_length),
        "include_index_only": bool(include_index_only),
        "quality_verdict": quality["verdict"],
        "source_basis": quality["source_basis"],
        "quality_counts": quality["counts"],
        "quality_rates": quality["rates"],
        "total_candidates": len(targets),
        "targets": targets,
        "excluded_gap_reasons": sorted(excluded_gap_reasons),
        "recommended_next": [
            "Run repair-kam-sections --execute only for targets listed here.",
            "Run index-audit-procedures after repair; index-only gaps do not require DART calls.",
            "Run rebuild-evidence-documents after procedure indexing.",
        ],
    }
