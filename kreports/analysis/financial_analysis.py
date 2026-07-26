"""Financial snapshots, investor signals, and filing-event evidence."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd
from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.db.engine import get_session
from kreports.db.models import Disclosure
from kreports.analysis import queries as _queries

from kreports.analysis._shared import _as_float, _avg, _clean_dict, _dedupe_confirmed_facts, _df_to_records, _has_db_table, _pct, _ratio
from kreports.analysis.company_profile import _company_summary, _resolve_company_identifier, resolve_corp_code


def _annual_report_source(
    corp_code: str,
    subject: dict | None,
    year: int | None,
    *,
    section_title: str,
    source_table: str,
) -> dict:
    """Source descriptor for annual structured data, bound to the requested year.

    A financial fact may still be locally confirmed when the filing cache is
    incomplete, but it must never borrow a receipt number from a different
    fiscal year merely to make the fact look citable.
    """
    report_nm = None
    rcept_no = None
    params: dict[str, Any] = {"corp_code": corp_code}
    where = "corp_code=:corp_code AND report_nm LIKE '%사업보고서%'"
    if year:
        where += " AND report_nm LIKE :year_pattern"
        # Fiscal year ends need not be December.  DART annual report titles
        # carry the fiscal year as ``사업보고서 (YYYY.MM)``.
        params["year_pattern"] = f"%사업보고서 ({int(year)}.%"
    with _engine_module.engine.connect() as conn:
        row = conn.execute(
            text(f"""
                SELECT rcept_no, report_nm
                FROM disclosures
                WHERE {where}
                ORDER BY disc_date DESC, rcept_no DESC
                LIMIT 1
            """),
            params,
        ).mappings().first()
    if row:
        rcept_no = row.get("rcept_no")
        report_nm = row.get("report_nm")
    source = {
        "corp_code": corp_code,
        "corp_name": (subject or {}).get("corp_name") or corp_code,
        "report_nm": report_nm or "DART 연간 재무 데이터",
        "bsns_year": year,
        "rcept_no": rcept_no,
        "section_title": section_title,
        "source_table": source_table,
    }
    if year and not rcept_no:
        source.update({
            "provenance_status": "requested_annual_report_not_cached",
            "provenance_gap": (
                f"요청 사업연도 {int(year)}의 사업보고서 접수번호를 로컬 캐시에서 확인하지 못했습니다. "
                "다른 사업연도 공시를 대체 인용하지 않았습니다."
            ),
        })
    return source


def _investor_financial_evidence(result: dict, subject: dict | None, *, mode: str) -> dict:
    """Build confirmed facts and next checks for investor financial tools."""
    corp_code = str(result.get("company") or (subject or {}).get("corp_code") or "")
    start_year = result.get("start_year")
    end_year = result.get("end_year")
    source = _annual_report_source(
        corp_code,
        subject,
        int(end_year) if end_year else None,
        section_title="재무제표",
        source_table="financial_facts_compact",
    )
    facts: list[dict] = []
    analysis: list[dict] = []
    next_checks: list[str] = []

    if mode == "quality_of_earnings":
        metrics = result.get("metrics") or {}
        evidence_rows = result.get("evidence") or []
        latest = evidence_rows[-1] if evidence_rows else {}
        facts.append({
            "statement": (
                f"{start_year}~{end_year}년 {result.get('fs_div') or 'CFS'} 기준 "
                f"{metrics.get('years') or len(evidence_rows)}개년 이익의 질 지표가 계산되었습니다."
            ),
            "source": source,
            "excerpt": (
                f"최근연도 {latest.get('year') or end_year}: revenue={latest.get('revenue')}, "
                f"operating_cf={latest.get('operating_cf')}, cash_conversion={latest.get('cash_conversion')}"
            ),
        })
        analysis.append({
            "perspective": "investor",
            "statement": "이익의 질 점검은 보고이익이 영업현금흐름과 반복 가능한 영업성과로 뒷받침되는지 보는 1차 필터입니다.",
        })
        if result.get("signals"):
            analysis.append({
                "perspective": "investor",
                "statement": "warning 또는 monitor 신호가 있으면 손익 주석, 현금흐름표, 감사보고서 강조사항을 함께 확인해야 합니다.",
            })
        next_checks.extend([
            "주석에서 일회성 손익, 손상, 충당부채, 수익인식 판단이 있었는지 확인하세요.",
            "감사보고서 KAM과 강조사항/기타사항이 이익 품질 신호와 연결되는지 대조하세요.",
        ])
    elif mode == "dcf":
        assumptions = result.get("candidate_assumptions") or {}
        actuals = result.get("historical_actuals") or []
        facts.append({
            "statement": (
                f"{start_year}~{end_year}년 과거 실적에서 DCF 입력 후보가 산출되었습니다. "
                f"매출성장률 후보는 {((assumptions.get('revenue_growth') or {}).get('value'))}, "
                f"영업이익률 후보는 {((assumptions.get('operating_margin') or {}).get('value'))}입니다."
            ),
            "source": source,
            "excerpt": f"historical_actuals={len(actuals)}개년, basis=historical_median",
        })
        analysis.append({
            "perspective": "investor",
            "statement": "DCF 입력 후보는 valuation 결론이 아니라 모델 시작점입니다. 예측기간, 할인율, 터미널 성장률은 별도 판단이 필요합니다.",
        })
        next_checks.extend([
            "DCF 핵심 입력값인 WACC, 세율, CAPEX, 운전자본 변동, 터미널 성장률을 별도로 산정하세요.",
            "사업보고서 사업부문·수주·시장위험 문단을 이용해 과거 중앙값이 미래 추정에 적합한지 검토하세요.",
        ])

    return {"confirmed_facts": _dedupe_confirmed_facts(facts), "analysis": analysis, "next_checks": next_checks}


def _disclosure_event_evidence(result: dict) -> dict:
    facts: list[dict] = []
    for event in (result.get("events") or [])[:6]:
        facts.append({
            "statement": (
                f"{event.get('event_date')} {event.get('corp_name') or event.get('corp_code')}의 "
                f"{event.get('event_title')} 공시가 {event.get('event_type')} 이벤트로 분류되었습니다."
            ),
            "source": {
                "corp_code": event.get("corp_code"),
                "corp_name": event.get("corp_name") or event.get("corp_code"),
                "report_nm": event.get("source_report_nm") or event.get("event_title") or "공시",
                "rcept_no": event.get("rcept_no"),
                "section_title": "공시목록",
                "source_table": "disclosure_events",
            },
            "excerpt": event.get("event_title"),
        })
    analysis = [{
        "perspective": "investor",
        "statement": "수시공시는 로컬에는 목록과 분류를 선적재하고, 본문 검토는 사용자 DART API key로 온디맨드 확인하는 구조입니다.",
    }]
    next_checks = [
        "중요 이벤트는 접수번호 기준으로 fetch_disclosure_on_demand를 호출해 원문 본문을 확인하세요.",
        "자본조달, 전환사채, 최대주주 변경, 소송, 횡령·배임 이벤트는 희석·지배구조·현금흐름 리스크로 연결해 검토하세요.",
    ]
    return {"confirmed_facts": _dedupe_confirmed_facts(facts), "analysis": analysis, "next_checks": next_checks}


def _investor_signal_evidence(
    corp_code: str,
    subject: dict | None,
    rows: list[dict],
    risk_summary: dict,
    risk_verdict: str,
    events: list[dict],
) -> dict:
    facts: list[dict] = []
    latest = rows[-1] if rows else {}
    latest_year = latest.get("연도")
    if latest:
        facts.append({
            "statement": (
                f"{latest_year}년 연간 재무 스냅샷 기준 ROE={latest.get('ROE')}, "
                f"영업이익률={latest.get('영업이익률')}, FCF={latest.get('FCF')}가 확인됩니다."
            ),
            "source": _annual_report_source(
                corp_code,
                subject,
                int(latest_year) if latest_year else None,
                section_title="재무제표",
                source_table="financial_facts",
            ),
            "excerpt": f"latest_snapshot={latest}",
        })
    if risk_summary.get("has_data"):
        facts.append({
            "statement": f"회계/거버넌스 리스크 요약 데이터가 있으며 현재 verdict는 {risk_verdict}입니다.",
            "source": {
                "corp_code": corp_code,
                "corp_name": (subject or {}).get("corp_name") or corp_code,
                "source_table": "risk_summary_views",
                "section_title": "회계 리스크 요약",
            },
            "excerpt": str(risk_summary)[:300],
        })
    for event in events[:3]:
        facts.append({
            "statement": (
                f"{event.get('disc_date')} {event.get('report_nm')} 공시가 "
                f"{event.get('category')} 이벤트로 분류되었습니다."
            ),
            "source": {
                "corp_code": corp_code,
                "corp_name": (subject or {}).get("corp_name") or corp_code,
                "report_nm": event.get("report_nm") or "공시",
                "rcept_no": event.get("rcept_no"),
                "section_title": "공시목록",
                "source_table": "disclosures",
            },
            "excerpt": event.get("report_nm"),
        })
    analysis = [{
        "perspective": "investor",
        "statement": "투자자 신호는 재무 품질, 회계 리스크, 최근 공시 이벤트를 함께 보는 1차 스크리닝입니다. 개별 신호 하나만으로 투자 결론을 내리면 안 됩니다.",
    }]
    next_checks = [
        "품질 체크가 낮거나 리스크 verdict가 warning 이상이면 사업보고서 주석과 감사보고서 KAM/강조사항을 함께 확인하세요.",
        "최근 자본조달·CB·합병·소송 이벤트는 접수번호 기준 원문을 열어 조건과 재무효과를 확인하세요.",
    ]
    return {"confirmed_facts": _dedupe_confirmed_facts(facts), "analysis": analysis, "next_checks": next_checks}


_ANNUAL_FIELDS = [
    "연도", "분기", "구분",
    "매출액", "영업이익", "순이익", "자산총계", "부채총계", "자본총계", "영업CF",
    "영업이익률", "순이익률", "부채비율", "ROE", "ROA", "매출성장률",
    # 확장 (capital allocation)
    "CapEx", "재고자산", "매출채권", "매입채무", "매출원가",
    "이자비용", "현금성자산",
    "FCF", "FCF마진", "CapEx_OCF", "CFO_NI",
    "투하자본", "ROIC", "이자보상배율",
    "DIO", "DSO", "DPO", "CCC",
]


_COMPACT_FINANCIAL_FIELD_MAP = {
    "revenue": "매출액",
    "operating_profit": "영업이익",
    "profit_loss": "순이익",
    "assets": "자산총계",
    "liabilities": "부채총계",
    "equity": "자본총계",
    "operating_cash_flow": "영업CF",
}


def _financial_snapshot_from_compact(
    corp_code: str,
    fs_div: str,
    years: Optional[int],
) -> dict:
    fs_div_used = fs_div
    with _engine_module.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT bsns_year, fs_div, metric_key, amount
            FROM financial_facts_compact
            WHERE corp_code=:corp_code AND fs_div=:fs_div
            ORDER BY bsns_year, metric_key
        """), {"corp_code": corp_code, "fs_div": fs_div}).mappings().all()
        if not rows and fs_div == "CFS":
            rows = conn.execute(text("""
                SELECT bsns_year, fs_div, metric_key, amount
                FROM financial_facts_compact
                WHERE corp_code=:corp_code AND fs_div='OFS'
                ORDER BY bsns_year, metric_key
            """), {"corp_code": corp_code}).mappings().all()
            if rows:
                fs_div_used = "OFS"

    grouped: dict[int, dict[str, float | None]] = {}
    for row in rows:
        year = int(row["bsns_year"])
        metric = str(row["metric_key"])
        amount = row["amount"]
        grouped.setdefault(year, {})
        grouped[year][metric] = (float(amount) / 1e8) if amount is not None else None

    out_rows: list[dict] = []
    previous_revenue: float | None = None
    for year in sorted(grouped):
        metrics = grouped[year]
        item: dict[str, Any] = {"연도": year, "분기": 4, "구분": fs_div_used}
        for metric, field in _COMPACT_FINANCIAL_FIELD_MAP.items():
            item[field] = metrics.get(metric)
        capex_parts = [
            metrics.get("purchase_ppe"),
            metrics.get("purchase_intangible_assets"),
        ]
        capex_values = [abs(value) for value in capex_parts if value is not None]
        item["CapEx"] = sum(capex_values) if capex_values else None
        item["영업이익률"] = _pct(item.get("영업이익"), item.get("매출액"))
        item["순이익률"] = _pct(item.get("순이익"), item.get("매출액"))
        item["부채비율"] = _pct(item.get("부채총계"), item.get("자본총계"))
        item["ROE"] = _pct(item.get("순이익"), item.get("자본총계"))
        item["ROA"] = _pct(item.get("순이익"), item.get("자산총계"))
        item["매출성장률"] = _pct(
            (item.get("매출액") - previous_revenue) if previous_revenue not in (None, 0) and item.get("매출액") is not None else None,
            previous_revenue,
        )
        previous_revenue = item.get("매출액")
        item["FCF"] = (
            item["영업CF"] - item["CapEx"]
            if item.get("영업CF") is not None and item.get("CapEx") is not None
            else None
        )
        item["FCF마진"] = _pct(item.get("FCF"), item.get("매출액"))
        item["CapEx_OCF"] = _pct(item.get("CapEx"), item.get("영업CF"))
        item["CFO_NI"] = _ratio(item.get("영업CF"), item.get("순이익"))
        out_rows.append(item)

    if years is not None:
        out_rows = out_rows[-int(years):]

    out_df = pd.DataFrame(out_rows)
    selected_cols = [c for c in _ANNUAL_FIELDS if c in out_df.columns] if not out_df.empty else []
    return {
        "corp_code": corp_code,
        "fs_div": fs_div_used,
        "unit": "억원",
        "rows": _df_to_records(out_df[selected_cols]) if selected_cols else [],
        "row_count": len(out_rows),
        "data_quality": {
            "status": "usable" if out_rows else "missing",
            "source": "financial_facts_compact",
            "year_count": len(out_rows),
            "coverage_note": "Compact runtime DB uses annual core metrics; full account-level financial_facts are not bundled.",
        },
    }


def get_financial_snapshot(
    company: str,
    fs_div: str = "CFS",
    years: Optional[int] = None,
    annual_only: bool = True,
) -> dict:
    """
    연도별 핵심 재무지표 + 자본배분 지표. 단위: 억원.

    Args:
        company: corp_code / stock_code / 회사명
        fs_div: CFS(연결) / OFS(별도). CFS 없으면 OFS 자동 폴백.
        years: 최근 N개 연도만 반환 (None=전체)
        annual_only: True면 Q4(연간)만, False면 분기 포함.

    Returns:
        {
          "corp_code": str,
          "fs_div": str,
          "unit": "억원",
          "rows": [{"연도", "분기", "매출액", ..., "CCC"}, ...],
          "row_count": int,
        }
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "fs_div": fs_div,
            "unit": "억원",
            "rows": [],
            "row_count": 0,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    compact_available = _has_db_table("financial_facts_compact")
    if not _has_db_table("financial_facts") and compact_available:
        return _financial_snapshot_from_compact(corp_code, fs_div, years)

    df = _queries.get_financials_extended(corp_code, fs_div=fs_div)
    if df.empty and compact_available:
        compact = _financial_snapshot_from_compact(corp_code, fs_div, years)
        if compact["row_count"]:
            return compact
    if df.empty and fs_div == "CFS":
        df = _queries.get_financials_extended(corp_code, fs_div="OFS")
        fs_div = "OFS"
    if df.empty and compact_available:
        compact = _financial_snapshot_from_compact(corp_code, fs_div, years)
        if compact["row_count"]:
            return compact

    if df.empty:
        return {
            "corp_code": corp_code,
            "fs_div": fs_div,
            "unit": "억원",
            "rows": [],
            "row_count": 0,
        }

    if annual_only:
        df = df[df["분기"] == 4]
    if years is not None and not df.empty:
        df = df.sort_values("연도", ascending=False).head(years)

    cols = [c for c in _ANNUAL_FIELDS if c in df.columns]
    rows = _df_to_records(df[cols].sort_values(["연도", "분기"]))

    return {
        "corp_code": corp_code,
        "fs_div": fs_div,
        "unit": "억원",
        "rows": rows,
        "row_count": len(rows),
    }


_INVESTOR_EVENT_PRESETS = {
    "treasury_buy": {
        "label": "자기주식",
        "keywords": ["자기주식", "자사주"],
        "stance": "potentially_positive",
    },
    "capital_raise": {
        "label": "유상증자",
        "keywords": ["유상증자", "증자"],
        "stance": "dilution_watch",
    },
    "convertible_bond": {
        "label": "CB/BW/EB",
        "keywords": ["전환사채", "신주인수권부사채", "교환사채", "CB", "BW", "EB"],
        "stance": "dilution_watch",
    },
    "merger_split": {
        "label": "합병/분할",
        "keywords": ["합병", "분할"],
        "stance": "structure_change",
    },
    "major_contract": {
        "label": "대규모 계약",
        "keywords": ["단일판매", "공급계약", "수주"],
        "stance": "potentially_positive",
    },
    "litigation": {
        "label": "소송/분쟁",
        "keywords": ["소송", "분쟁", "중재"],
        "stance": "risk_watch",
    },
    "amendment": {
        "label": "정정공시",
        "keywords": ["정정"],
        "stance": "risk_watch",
    },
}


def _classify_disclosure(report_nm: str) -> dict | None:
    normalized = report_nm or ""
    for key, preset in _INVESTOR_EVENT_PRESETS.items():
        if any(keyword in normalized for keyword in preset["keywords"]):
            return {
                "category": key,
                "label": preset["label"],
                "stance": preset["stance"],
            }
    return None


def _recent_investor_events(corp_code: str, window_days: int, limit: int) -> tuple[list[dict], dict]:
    since = date.today() - timedelta(days=window_days)
    with get_session() as session:
        disclosure_rows = (
            session.query(Disclosure)
            .filter(Disclosure.corp_code == corp_code)
            .filter(Disclosure.disc_date >= since)
            .order_by(Disclosure.disc_date.desc())
            .limit(max(limit * 5, limit))
            .all()
        )
        rows = [
            {
                "disc_date": str(row.disc_date),
                "rcept_no": row.rcept_no,
                "report_nm": row.report_nm,
                "flr_nm": row.flr_nm,
            }
            for row in disclosure_rows
        ]

    events: list[dict] = []
    counts = {key: 0 for key in _INVESTOR_EVENT_PRESETS}
    for row in rows:
        classified = _classify_disclosure(row["report_nm"])
        if classified is None:
            continue
        counts[classified["category"]] += 1
        if len(events) < limit:
            events.append({
                "disc_date": row["disc_date"],
                "rcept_no": row["rcept_no"],
                "report_nm": row["report_nm"],
                "flr_nm": row["flr_nm"],
                **classified,
            })
    return events, counts


def _risk_score_from_summary(summary: dict) -> tuple[int, str, list[dict]]:
    weights = {
        "non_clean_opinion_count": 25,
        "equity_negative_count": 25,
        "going_concern_count": 20,
        "beneish_alert_count": 15,
        "op_cf_divergence_count": 10,
        "high_accrual_count": 10,
        "amendment_heavy_count": 10,
        "auditor_change_count": 5,
        "nas_risk_count": 5,
    }
    factors = []
    score = 0
    for key, weight in weights.items():
        count = int(summary.get(key) or 0)
        if count <= 0:
            continue
        penalty = min(weight * count, weight * 2)
        score += penalty
        factors.append({"name": key, "count": count, "penalty": penalty})
    score = min(score, 100)
    if score >= 70:
        verdict = "red_flag"
    elif score >= 40:
        verdict = "warning"
    elif score >= 20:
        verdict = "watch"
    else:
        verdict = "clean"
    return score, verdict, factors


def get_investor_signals(
    company: str,
    years: int = 5,
    window_days: int = 365,
    event_limit: int = 20,
) -> dict:
    """
    투자자 관점의 품질·리스크·최근 공시 이벤트 요약.

    DART 원문을 실시간으로 다시 긁기보다, 수집된 kreports DB 위에서
    반복 투자 점검에 바로 쓸 수 있는 신호를 만든다.
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "has_data": False,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    subject = _company_summary(corp_code)
    financial = get_financial_snapshot(corp_code, years=years, annual_only=True)
    rows = financial.get("rows", [])
    latest = rows[-1] if rows else {}

    avg_roe = _avg([_as_float(row.get("ROE")) for row in rows])
    avg_op_margin = _avg([_as_float(row.get("영업이익률")) for row in rows])
    avg_revenue_growth = _avg([_as_float(row.get("매출성장률")) for row in rows])
    latest_debt_ratio = _as_float(latest.get("부채비율"))
    latest_fcf = _as_float(latest.get("FCF"))
    latest_cfo_ni = _as_float(latest.get("CFO_NI"))

    quality_checks = {
        "positive_avg_roe": avg_roe is not None and avg_roe >= 10,
        "positive_avg_op_margin": avg_op_margin is not None and avg_op_margin > 0,
        "positive_revenue_growth": avg_revenue_growth is not None and avg_revenue_growth > 0,
        "debt_ratio_under_100": latest_debt_ratio is not None and latest_debt_ratio <= 100,
        "positive_latest_fcf": latest_fcf is not None and latest_fcf > 0,
        "cfo_covers_net_income": latest_cfo_ni is not None and latest_cfo_ni >= 0.8,
    }
    passed = sum(1 for passed in quality_checks.values() if passed)

    risk_summary = _queries.get_risk_summary(corp_code)
    risk_score, risk_verdict, risk_factors = _risk_score_from_summary(risk_summary)
    events, event_counts = _recent_investor_events(corp_code, window_days, event_limit)

    takeaways = []
    if passed >= 4:
        takeaways.append("quality_profile_supportive")
    elif rows:
        takeaways.append("quality_profile_mixed")
    else:
        takeaways.append("financial_data_missing")
    if risk_verdict in {"warning", "red_flag"}:
        takeaways.append("accounting_or_governance_risk_needs_review")
    if event_counts.get("capital_raise", 0) or event_counts.get("convertible_bond", 0):
        takeaways.append("dilution_events_present")
    if event_counts.get("treasury_buy", 0):
        takeaways.append("shareholder_return_event_present")

    result = {
        "corp_code": corp_code,
        "subject": subject,
        "has_data": bool(rows or events or risk_summary.get("has_data")),
        "unit": "억원",
        "years": years,
        "window_days": window_days,
        "quality_snapshot": {
            "avg_roe": avg_roe,
            "avg_operating_margin": avg_op_margin,
            "avg_revenue_growth": avg_revenue_growth,
            "latest_debt_ratio": latest_debt_ratio,
            "latest_fcf": latest_fcf,
            "latest_cfo_ni": latest_cfo_ni,
            "checks": quality_checks,
            "passed_checks": passed,
            "total_checks": len(quality_checks),
            "latest_year": latest.get("연도"),
        },
        "accounting_risk": {
            "score": risk_score,
            "verdict": risk_verdict,
            "factors": risk_factors,
            "raw_summary": _clean_dict(risk_summary),
        },
        "recent_events": events,
        "event_counts": event_counts,
        "takeaways": takeaways,
        "limitations": [
            "최근 공시 이벤트는 수집된 disclosures 테이블의 제목 기반 분류입니다.",
            "내부자 지분 매매 원자료는 아직 별도 수집하지 않으므로 insider_signal은 포함하지 않습니다.",
            "투자 판단 전 원 공시와 최신 수집 시각을 확인하세요.",
        ],
    }
    result.update(_investor_signal_evidence(
        corp_code,
        subject,
        rows,
        risk_summary,
        risk_verdict,
        events,
    ))
    return _clean_dict(result)


def score_going_concern(company: str) -> dict:
    """
    6인자 계속기업 위험 스코어카드 (100점 감점방식).

    Returns:
        {
          "corp_code", "score" (0-100), "grade" (안정/주의/경고/위험),
          "risk" (ok/warn/bad),
          "factors": [{"name", "hit", "penalty", "detail"}],
          "has_data": bool,
        }
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "score": 0,
            "grade": "-",
            "risk": "ok",
            "factors": [],
            "has_data": False,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    result = _queries.get_going_concern_score(corp_code)
    result["corp_code"] = corp_code
    return _clean_dict(result)


def detect_restatement(
    company: str,
    threshold_pct: float = 1.0,
    top_n: int = 10,
) -> dict:
    """
    사업보고서 간 소급 재작성 Top N. threshold_pct 이상 차이만 반환.

    Returns:
        {
          "corp_code", "threshold_pct", "top_n",
          "restatements": [
            {"기준연도", "재무표", "계정명", "원본값", "재작성값", "차이", "변동률"},
            ...
          ],
          "count": int,
        }
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "threshold_pct": threshold_pct,
            "top_n": top_n,
            "restatements": [],
            "count": 0,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    df = _queries.get_restatement_delta(
        corp_code, threshold_pct=threshold_pct, top_n=top_n
    )
    records = _df_to_records(df)
    return {
        "corp_code": corp_code,
        "threshold_pct": threshold_pct,
        "top_n": top_n,
        "restatements": records,
        "count": len(records),
    }


def get_quality_of_earnings_pack(
    company: str,
    start_year: int = 2021,
    end_year: int = 2025,
    fs_div: str = "CFS",
) -> dict:
    """Return investor-oriented quality-of-earnings diagnostics."""
    from kreports.analysis.investor_quality import quality_of_earnings_pack

    corp_code = resolve_corp_code(company) or company
    subject = _company_summary(corp_code)
    if not subject:
        return {"error": "company not found", "company": company}
    result = quality_of_earnings_pack(
        corp_code,
        start_year=start_year,
        end_year=end_year,
        fs_div=fs_div,
    )
    result["subject"] = subject
    result.update(_investor_financial_evidence(result, subject, mode="quality_of_earnings"))
    return _clean_dict(result)


def get_dcf_input_candidates(
    company: str,
    start_year: int = 2021,
    end_year: int = 2025,
    fs_div: str = "CFS",
) -> dict:
    """Return evidence-backed DCF input candidates."""
    from kreports.analysis.dcf_inputs import dcf_input_candidates

    corp_code = resolve_corp_code(company) or company
    subject = _company_summary(corp_code)
    if not subject:
        return {"error": "company not found", "company": company}
    result = dcf_input_candidates(
        corp_code,
        start_year=start_year,
        end_year=end_year,
        fs_div=fs_div,
    )
    result["subject"] = subject
    result.update(_investor_financial_evidence(result, subject, mode="dcf"))
    return _clean_dict(result)


def search_disclosure_events(
    *,
    company: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    event_types: list[str] | None = None,
    market: str | None = None,
    limit: int = 50,
) -> dict:
    """Search indexed event disclosures by company, period, event type, and market."""
    from kreports.analysis.disclosure_events import search_disclosure_events as _search_events

    corp_code = None
    subject = None
    if company:
        corp_code = resolve_corp_code(company) or company
        subject = _company_summary(corp_code)
        if not subject:
            return {"error": "company not found", "company": company}
    result = _search_events(
        company=corp_code,
        start_date=start_date,
        end_date=end_date,
        event_types=event_types,
        market=market,
        limit=limit,
    )
    result["subject"] = subject
    result.update(_disclosure_event_evidence(result))
    return _clean_dict(result)
