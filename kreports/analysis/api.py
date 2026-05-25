"""
dart_analyst.api — MCP 친화적 분석 API.

dashboard.db의 DataFrame 중심 함수들을 dict/list[dict] API로 변환한다.
JSON 직렬화 안전성을 보장한다 (NaN → None, numpy dtype → python).
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd
from sqlalchemy import bindparam, text

from kreports.db.engine import get_session, engine as _engine
from kreports.db.models import BusinessAffiliateAuditor, Company, Disclosure, EvidenceDocument, ReportSection

# dashboard.db는 streamlit optional import를 지원하므로 headless에서도 사용 가능
from kreports.analysis import queries as _queries
from kreports.analysis.peer import (
    PeerResolution,
    SectorGroup,
    classify_sector,
    confidence_band,
    resolve_fs_div_for_company,
    resolve_peers,
)
from kreports.storage.raw_documents import RawDocumentStore


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _clean_value(v: Any) -> Any:
    """
    JSON 직렬화 안전한 값으로 정리.
    - NaN/NaT/pd.NA → None
    - numpy scalar → python scalar
    - pd.Timestamp → ISO string
    """
    if v is None:
        return None
    # pd.NA / NaT
    if v is pd.NA:
        return None
    # pd.Timestamp
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    # float NaN/Inf
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    # numpy scalar
    if hasattr(v, "item"):
        try:
            v2 = v.item()
            if isinstance(v2, float) and (math.isnan(v2) or math.isinf(v2)):
                return None
            return v2
        except (AttributeError, ValueError):
            pass
    return v


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → list[dict], 모든 값을 JSON-safe하게 정리."""
    if df is None or df.empty:
        return []
    records = df.to_dict(orient="records")
    return [{k: _clean_value(v) for k, v in row.items()} for row in records]


def _clean_dict(d: dict) -> dict:
    """dict 값을 JSON-safe하게 정리 (중첩 리스트 포함)."""
    out: dict = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = [_clean_dict(x) if isinstance(x, dict) else _clean_value(x) for x in v]
        elif isinstance(v, dict):
            out[k] = _clean_dict(v)
        else:
            out[k] = _clean_value(v)
    return out


def _display_text(value: str | None) -> str:
    text_value = value or ""
    text_value = text_value.replace("&cr;", "\n").replace("&#13;", "\n")
    text_value = text_value.replace("&nbsp;", " ").replace("&#160;", " ")
    text_value = text_value.replace("\r", "\n")
    return text_value


def _load_source_document_excerpt(row: dict, *, keyword: str | None, limit: int = 1200) -> tuple[bool, str]:
    """Return keyword match and excerpt without requiring inline raw_content.

    For source_documents, raw XML/HTML may have been cleared from SQLite and
    moved to compressed storage. This helper reads at most the bounded result
    candidates selected by metadata filters.
    """
    report_nm = row.get("report_nm") or ""
    inline = row.pop("_inline_body", "") or ""
    body = inline
    if not body and row.get("storage_uri"):
        try:
            body = RawDocumentStore().read(row["storage_uri"], expected_hash=row.get("doc_hash"))
        except Exception as exc:
            row["source_load_error"] = str(exc)
            body = ""

    haystack = f"{report_nm}\n{body}"
    if keyword and keyword not in haystack:
        return False, ""
    return True, _display_text(body)[:limit]


# ---------------------------------------------------------------------------
# 기업 조회 / 식별
# ---------------------------------------------------------------------------

def search_company(query: str, limit: int = 30) -> list[dict]:
    """
    회사명 또는 종목코드로 DB 검색. 상장사만 반환.

    Returns:
        [{"corp_code", "corp_name", "stock_code", "market"}]
    """
    return _queries.search_companies(query, limit=limit)


def get_company(stock_code: str) -> Optional[dict]:
    """
    종목코드로 기업 상세 조회.

    Returns:
        {"corp_code", "corp_name", "stock_code", "market", "induty_code", "sector"} or None
    """
    return _queries.get_company(stock_code)


def resolve_corp_code(identifier: str) -> Optional[str]:
    """
    identifier가 8자리 corp_code면 그대로, 6자리 종목코드면 corp_code 변환,
    그 외 회사명이면 search 후 첫 결과의 corp_code 반환.
    빈 문자열·None은 None 반환.
    """
    if identifier is None:
        return None
    s = identifier.strip()
    if not s:
        return None
    if len(s) == 8 and s.isdigit():
        with get_session() as session:
            exists = session.query(Company.corp_code).filter_by(corp_code=s).first()
            return s if exists else None
    if len(s) == 6 and s.isdigit():
        company = _queries.get_company(s)
        return company["corp_code"] if company else None
    # 회사명 fallback
    hits = _queries.search_companies(s, limit=1)
    return hits[0]["corp_code"] if hits else None


def _resolve_company_identifier(identifier: str) -> Optional[str]:
    """
    public API에서 corp_code / stock_code / 회사명을 모두 허용하기 위한 helper.
    """
    return resolve_corp_code(identifier)


def _company_summary(corp_code: str) -> Optional[dict]:
    with _engine.connect() as conn:
        row = conn.execute(
            text("SELECT corp_code, stock_code, corp_name, market, induty_code FROM companies WHERE corp_code=:cc"),
            {"cc": corp_code},
        ).mappings().first()
    return dict(row) if row else None


def _has_db_column(table_name: str, column_name: str) -> bool:
    try:
        with _engine.connect() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
        return any(row.get("name") == column_name for row in rows)
    except Exception:
        return False


def _cached_years_for_sections(corp_code: str, source_type: str, section_key: str | None = None) -> list[int]:
    where = "corp_code=:corp_code AND source_type=:source_type"
    params: dict[str, Any] = {"corp_code": corp_code, "source_type": source_type}
    if section_key:
        where += " AND section_key=:section_key"
        params["section_key"] = section_key
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT DISTINCT bsns_year
                FROM report_sections
                WHERE {where}
                ORDER BY bsns_year DESC
                """
            ),
            params,
        ).scalars().all()
    return [int(y) for y in rows]


def _cache_quality_status(*, subject_count: int, peer_total: int = 0, peer_covered: int = 0) -> str:
    if subject_count <= 0 and peer_covered <= 0:
        return "missing"
    if peer_total and peer_covered / peer_total < 0.5:
        return "limited"
    if subject_count <= 0:
        return "subject_missing"
    return "usable"


def _kam_hint_coverage(rows: list[dict]) -> dict:
    kam_rows = [row for row in rows if row.get("section_key") == "kam"]
    with_reason = sum(
        1 for row in kam_rows
        if (row.get("kam_analysis") or {}).get("has_reason_hint")
    )
    with_procedure = sum(
        1 for row in kam_rows
        if (row.get("kam_analysis") or {}).get("has_procedure_hint")
        or bool(row.get("related_audit_procedures"))
    )
    total = len(kam_rows)
    return {
        "kam_body_count": total,
        "reason": {
            "with_reason_hint": with_reason,
            "coverage_pct": round(with_reason * 100.0 / total, 1) if total else 0.0,
        },
        "procedure": {
            "with_procedure_hint": with_procedure,
            "coverage_pct": round(with_procedure * 100.0 / total, 1) if total else 0.0,
        },
    }


def _attach_related_audit_procedures(rows: list[dict], *, corp_code: str, year: int) -> None:
    kam_rcept_nos = sorted({
        str(row.get("rcept_no"))
        for row in rows
        if row.get("section_key") == "kam" and row.get("rcept_no")
    })
    if not kam_rcept_nos:
        return
    stmt = text(
        """
        SELECT rcept_no, dcm_no, kam_topic, procedure_type, procedure_text,
               procedure_length, section_ordinal, procedure_ordinal
        FROM audit_procedure_items
        WHERE corp_code=:corp_code
          AND bsns_year=:year
          AND rcept_no IN :rcept_nos
        ORDER BY rcept_no, section_ordinal, procedure_ordinal
        """
    ).bindparams(bindparam("rcept_nos", expanding=True))
    with _engine.connect() as conn:
        procedure_rows = [dict(r) for r in conn.execute(
            stmt,
            {"corp_code": corp_code, "year": year, "rcept_nos": kam_rcept_nos},
        ).mappings().all()]
        fallback_rows = [dict(r) for r in conn.execute(
            text(
                """
                SELECT rcept_no, dcm_no, kam_topic, procedure_type, procedure_text,
                       procedure_length, section_ordinal, procedure_ordinal
                FROM audit_procedure_items
                WHERE corp_code=:corp_code
                  AND bsns_year=:year
                ORDER BY source_type, rcept_no, section_ordinal, procedure_ordinal
                LIMIT 10
                """
            ),
            {"corp_code": corp_code, "year": year},
        ).mappings().all()]

    grouped: dict[str, list[dict]] = {}
    for item in procedure_rows:
        text_value = _display_text(item.pop("procedure_text") or "")
        item["procedure_excerpt"] = text_value[:900]
        grouped.setdefault(str(item.get("rcept_no")), []).append(item)
    for item in fallback_rows:
        text_value = _display_text(item.pop("procedure_text") or "")
        item["procedure_excerpt"] = text_value[:900]

    for row in rows:
        if row.get("section_key") != "kam":
            continue
        procedures = grouped.get(str(row.get("rcept_no")), [])
        source = "audit_procedure_items"
        if not procedures:
            procedures = fallback_rows
            source = "audit_procedure_items_company_year"
        if not procedures:
            continue
        row["related_audit_procedures"] = procedures[:10]
        row["related_audit_procedure_count"] = len(procedures)
        row["related_audit_procedure_source"] = source
        analysis = row.setdefault("kam_analysis", {})
        if not analysis.get("has_procedure_hint"):
            analysis["has_procedure_hint"] = True
            analysis["procedure_excerpt"] = procedures[0].get("procedure_excerpt") or ""
            analysis["procedure_keywords"] = sorted({
                str(item.get("procedure_type"))
                for item in procedures
                if item.get("procedure_type")
            })


# ---------------------------------------------------------------------------
# 재무 스냅샷 (연도별 핵심 지표 + 자본배분)
# ---------------------------------------------------------------------------

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

    df = _queries.get_financials_extended(corp_code, fs_div=fs_div)
    if df.empty and fs_div == "CFS":
        df = _queries.get_financials_extended(corp_code, fs_div="OFS")
        fs_div = "OFS"

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


# ---------------------------------------------------------------------------
# 투자자 신호 요약
# ---------------------------------------------------------------------------

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


def _as_float(value: Any) -> float | None:
    value = _clean_value(value)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _avg(values: list[float | None]) -> float | None:
    cleaned = [v for v in values if v is not None]
    return statistics.fmean(cleaned) if cleaned else None


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

    return {
        "corp_code": corp_code,
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


# ---------------------------------------------------------------------------
# 계속기업 스코어
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 소급 재작성 감지
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# 회계정책 항목
# ---------------------------------------------------------------------------

def get_accounting_policy(
    company: str,
    bsns_year: int,
    fs_div: str = "CFS",
) -> Optional[dict]:
    """
    사업보고서 주석에서 회계정책 항목 추출.

    Returns:
        {
          "corp_code", "bsns_year", "fs_div",
          "items": {item_key: {"heading", "body"}},
          "item_count": int,
        } or None (수집된 사업보고서 없음)
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "items": {},
            "item_count": 0,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    data = _queries.get_cached_accounting_policy(corp_code, bsns_year, fs_div=fs_div)
    if data is None:
        from kreports.runtime import readonly_cache_miss

        return {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "items": {},
            "item_count": 0,
            "note": readonly_cache_miss("accounting_policy", corp_code, bsns_year),
        }
    result = {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "fs_div": fs_div,
        "items": data.get("items", {}),
        "item_count": len(data.get("items", {})),
    }
    return _clean_dict(result)


# ---------------------------------------------------------------------------
# 감사인 이력
# ---------------------------------------------------------------------------

def get_audit_history(company: str) -> dict:
    """
    연도별 감사인·의견·연속연수 이력.

    Returns:
        {
          "corp_code",
          "history": [
            {"회계연도", "구분", "감사인", "감사의견", "교체여부", "연속연수"},
            ...
          ],
          "count": int,
        }
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "history": [],
            "count": 0,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    df = _queries.get_auditors(corp_code)
    records = _df_to_records(df)
    return {
        "corp_code": corp_code,
        "history": records,
        "count": len(records),
    }


# ---------------------------------------------------------------------------
# 종속·관계회사 감사인 매트릭스
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 업종 벤치마킹
# ---------------------------------------------------------------------------

# SQL expression for each supported metric. NULLIF로 0 나누기 방어.
# 분자/분모가 NULL인 행은 HAVING 절에서 제외.
_METRIC_SQL = {
    "영업이익률": "100.0 * f.operating_profit / NULLIF(f.revenue, 0)",
    "순이익률":   "100.0 * f.net_income / NULLIF(f.revenue, 0)",
    "부채비율":   "100.0 * f.total_debt / NULLIF(f.total_equity, 0)",
    "ROE":        "100.0 * f.net_income / NULLIF(f.total_equity, 0)",
    "ROA":        "100.0 * f.net_income / NULLIF(f.total_assets, 0)",
    # v0.2 신규
    "자기자본비율": "100.0 * f.total_equity / NULLIF(f.total_assets, 0)",
    "매출성장률":   "f.revenue_yoy * 100.0",
    "Beneish_M":    "f.beneish_m_score",
}

_METRIC_UNIT = {
    "영업이익률": "%",
    "순이익률": "%",
    "부채비율": "%",
    "ROE": "%",
    "ROA": "%",
    "자기자본비율": "%",
    "매출성장률": "%",
    "Beneish_M": "score",
}


def _get_industry_name(prefix: str) -> str:
    """KSIC 2자리 prefix → 한글 업종명."""
    from kreports.processor.sector_policy_map import KSIC_NAMES
    return KSIC_NAMES.get(prefix, f"업종 {prefix}")

# 희소성 경고 임계값 (최소 peer 수)
_MIN_PEERS_FOR_STATS = 3


def _quantile(values: list[float], q: float) -> Optional[float]:
    """정렬된 리스트에서 quantile 계산. n=0이면 None."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    # statistics.quantiles(n=4)는 P25/P50/P75를 리스트로 반환 (n>=2 필요)
    # 수동 구현: linear interpolation
    s = sorted(values)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _fetch_metric_values(
    conn,
    corp_codes: list[str],
    metric_expr: str,
    year: int,
    fs_div: str,
) -> list[tuple[str, str, Optional[str], Optional[float]]]:
    """
    주어진 corp_code 리스트에 대해 metric 값 + 회사명 + induty_code 를 일괄 조회.

    반환: (corp_code, corp_name, induty_code, value) 튜플. value가 NULL인 행은 제외.
    """
    if not corp_codes:
        return []
    stmt = text(
        f"SELECT f.corp_code, c.corp_name, c.induty_code, ({metric_expr}) AS v "
        "FROM financials f JOIN companies c ON c.corp_code = f.corp_code "
        "WHERE f.corp_code IN :ccs "
        "  AND f.year = :y AND f.quarter = 4 AND f.fs_div = :fs"
    ).bindparams(bindparam("ccs", expanding=True))
    rows = conn.execute(
        stmt,
        {"ccs": list(corp_codes), "y": year, "fs": fs_div},
    ).all()
    return [
        (r[0], r[1], r[2], r[3])
        for r in rows
        if r[3] is not None
    ]


def get_industry_aggregates(
    induty_code: str,
    metric: str = "영업이익률",
    year: Optional[int] = None,
    fs_div: str = "CFS",
    prefix_len: int = 2,
    include_peers: bool = True,
    peer_limit: int = 50,
    subject_corp_code: str | None = None,
    subject_name: str | None = None,
) -> dict:
    """
    같은 업종(induty_code prefix 매칭) 내 기업들의 metric 분포.

    동일 prefix를 공유하는 기업 중 연간 Q4 재무데이터가 있는 기업을 수집하여
    P25/P50/P75 quantile과 min/max/mean을 계산한다.

    subject_corp_code가 제공되면 peer set 해석을 `peer.resolve_peers`에 위임하여
    adaptive ladder(3자리→2자리 fallback) 및 sector mutual exclusion을 적용한다.

    Args:
        induty_code: 기준 기업의 induty_code (예: Samsung "264")
        metric: 집계 지표. 지원: 영업이익률·순이익률·부채비율·ROE·ROA.
        year: 사업연도 (예: 2024). None이면 해당 induty에서 데이터가 있는 가장 최근 연도.
        fs_div: CFS/OFS
        prefix_len: induty_code 앞에서 몇 자리로 매칭할지 (resolve_peers의
            prefix_len_start로 전달). 기본 2.
        include_peers: True면 peer 기업 리스트 반환. False면 통계만.
        peer_limit: peer 리스트 최대 개수.
        subject_corp_code: 특정 회사의 위치를 전체 peer set 기준으로 계산할 때 사용.
        subject_name: subject_corp_code의 표시명.

    Returns:
        {
          "induty_code", "match_prefix", "prefix_len",
          "metric", "unit",
          "year", "fs_div",
          "n",  # 통계에 포함된 기업 수
          "quantiles": {"p25", "p50", "p75", "min", "max", "mean"} or None,
          "peers": [{"corp_code", "corp_name", "induty_code", "value"}, ...],
          "sector_group", "confidence", "excluded_categories",
          "size_bucket_applied", "matched_prefix_len",
          "note": str,
        }
    """
    if metric not in _METRIC_SQL:
        return {
            "error": f"지원하지 않는 metric: {metric}. "
                     f"지원: {list(_METRIC_SQL.keys())}",
        }

    if induty_code is None or not str(induty_code).strip():
        return {"error": "induty_code가 비어 있습니다."}

    induty_code = str(induty_code).strip()
    if prefix_len < 1 or prefix_len > 5:
        return {"error": "prefix_len은 1~5 사이여야 합니다."}

    metric_expr = _METRIC_SQL[metric]

    # ------------------------------------------------------------------
    # Peer set 해석
    # ------------------------------------------------------------------
    # subject_corp_code가 있으면 peer.resolve_peers에 위임(어댑티브 ladder + sector
    # mutual exclusion). 없으면 induty_code prefix-only 경로(legacy).
    #
    # 두 경로 모두 동일한 meta 키(sector_group/confidence/excluded_categories/
    # size_bucket_applied/matched_prefix_len)를 반환해야 한다.
    peer_resolution: Optional[PeerResolution] = None
    sector_group_val: str = classify_sector(induty_code).value
    excluded_categories: list[str] = []
    size_bucket_applied: Optional[float] = None
    resolution_note: str = ""

    if subject_corp_code:
        peer_resolution = resolve_peers(
            corp_code=subject_corp_code,
            prefix_len_start=prefix_len,
            min_n=_MIN_PEERS_FOR_STATS,
            exclude_other_sectors=True,
            size_bucket_decade=None,
            fs_div=fs_div,
            year=year,
        )
        matched_prefix_len = peer_resolution.matched_prefix_len
        match_prefix = induty_code[:matched_prefix_len]
        sector_group_val = peer_resolution.sector_group.value
        excluded_categories = list(peer_resolution.excluded_categories)
        size_bucket_applied = peer_resolution.size_bucket_applied
        resolution_note = peer_resolution.note
        # subject-기준 연도를 그대로 사용 (industry-wide MAX와 발산할 수 있는 늦은 제출
        # 케이스 방지). year 미지정 시 resolve_peers가 산정한 결과를 신뢰한다.
        if year is None and peer_resolution.resolved_year is not None:
            year = peer_resolution.resolved_year
    else:
        # legacy prefix-only 경로: subject 없음 (induty_code 직접 지정)
        matched_prefix_len = prefix_len
        match_prefix = induty_code[:prefix_len]

    # ------------------------------------------------------------------
    # 연도 결정: 미지정 시 가장 최근 Q4 연도
    # ------------------------------------------------------------------
    if year is None:
        with _engine.connect() as conn:
            latest = conn.execute(
                text(f"""
                    SELECT MAX(f.year)
                    FROM financials f
                    JOIN companies c ON f.corp_code = c.corp_code
                    WHERE substr(c.induty_code, 1, :plen) = :prefix
                      AND f.fs_div = :fs_div
                      AND f.quarter = 4
                """),
                {"plen": matched_prefix_len, "prefix": match_prefix, "fs_div": fs_div},
            ).scalar()
        if latest is None:
            industry_name = (
                _get_industry_name(match_prefix) if matched_prefix_len == 2 else match_prefix
            )
            return {
                "induty_code": induty_code,
                "match_prefix": match_prefix,
                "industry_name": industry_name,
                "prefix_len": matched_prefix_len,
                "matched_prefix_len": matched_prefix_len,
                "metric": metric,
                "unit": _METRIC_UNIT.get(metric),
                "year": None,
                "fs_div": fs_div,
                "n": 0,
                "quantiles": None,
                "peers": [],
                "sector_group": sector_group_val,
                "confidence": confidence_band(0),
                "excluded_categories": excluded_categories,
                "size_bucket_applied": size_bucket_applied,
                "note": (
                    f"업종 prefix '{match_prefix}' 내에서 {fs_div} Q4 재무데이터를 가진 "
                    f"기업이 없습니다. 더 많은 기업을 수집하거나 prefix_len을 낮추세요."
                ),
            }
        year = int(latest)

    # ------------------------------------------------------------------
    # Peer metric 값 수집
    # ------------------------------------------------------------------
    if peer_resolution is not None:
        # resolve_peers가 이미 sector mutual exclusion 적용한 corp_code 목록을 줌.
        # metric 값은 _fetch_metric_values로 일괄 조회.
        with _engine.connect() as conn:
            fetched = _fetch_metric_values(
                conn,
                peer_resolution.peer_corp_codes,
                metric_expr,
                year,
                fs_div,
            )
        # 기존 응답 shape과 동일하게 정렬 (value desc)
        fetched_sorted = sorted(
            fetched,
            key=lambda r: r[3] if r[3] is not None else float("-inf"),
            reverse=True,
        )
        peers_all = [
            {
                "corp_code": cc,
                "corp_name": cn,
                "induty_code": ic,
                "value": round(float(v), 2) if v is not None else None,
            }
            for cc, cn, ic, v in fetched_sorted
        ]

        # subject 본인 metric 값도 별도 조회 (resolve_peers는 본인을 제외하므로,
        # subject의 metric 값이 필요하면 별도 fetch).
        if subject_corp_code:
            with _engine.connect() as conn:
                subj_rows = _fetch_metric_values(
                    conn,
                    [subject_corp_code],
                    metric_expr,
                    year,
                    fs_div,
                )
            if subj_rows:
                _cc, _cn, _ic, v = subj_rows[0]
                subject_value: Optional[float] = (
                    round(float(v), 2) if v is not None else None
                )
            else:
                subject_value = None
        else:
            subject_value = None
    else:
        # ---- legacy prefix-only 경로 ----
        sql = text(f"""
            SELECT
              c.corp_code,
              c.corp_name,
              c.induty_code,
              ({metric_expr}) AS value
            FROM financials f
            JOIN companies c ON f.corp_code = c.corp_code
            WHERE substr(c.induty_code, 1, :plen) = :prefix
              AND f.fs_div = :fs_div
              AND f.year = :year
              AND f.quarter = 4
              AND ({metric_expr}) IS NOT NULL
            ORDER BY value DESC
        """)
        with _engine.connect() as conn:
            rows = conn.execute(
                sql,
                {
                    "plen": matched_prefix_len,
                    "prefix": match_prefix,
                    "fs_div": fs_div,
                    "year": year,
                },
            ).fetchall()

        peers_all = [
            {
                "corp_code": r[0],
                "corp_name": r[1],
                "induty_code": r[2],
                "value": round(float(r[3]), 2) if r[3] is not None else None,
            }
            for r in rows
            if r[3] is not None
        ]
        subject_value = None

    values = [p["value"] for p in peers_all if p["value"] is not None]
    n = len(values)

    quantiles: Optional[dict] = None
    if n >= 1:
        quantiles = {
            "p25": round(_quantile(values, 0.25), 2) if n >= _MIN_PEERS_FOR_STATS else None,
            "p50": round(_quantile(values, 0.50), 2),
            "p75": round(_quantile(values, 0.75), 2) if n >= _MIN_PEERS_FOR_STATS else None,
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "mean": round(statistics.fmean(values), 2),
        }

    # 업종명
    industry_name = (
        _get_industry_name(match_prefix) if matched_prefix_len == 2 else match_prefix
    )

    # 업종 내 전체 기업 수 vs 수집된 기업 수 (커버리지)
    with _engine.connect() as conn:
        total_in_industry = conn.execute(
            text("""
                SELECT COUNT(*) FROM companies
                WHERE substr(induty_code, 1, :plen) = :prefix
                  AND stock_code IS NOT NULL
            """),
            {"plen": matched_prefix_len, "prefix": match_prefix},
        ).scalar() or 0

    coverage_pct = round(100.0 * n / total_in_industry, 1) if total_in_industry > 0 else 0

    if n == 0:
        note = (
            f"업종 '{industry_name}' (prefix {match_prefix}) 내 {year or '?'}년 {fs_div} Q4 데이터에서 "
            f"{metric} 계산 가능한 기업이 없습니다. "
            f"`kreports collect-seed`로 데이터를 수집하세요."
        )
    elif n < _MIN_PEERS_FOR_STATS:
        note = (
            f"peer {n}개 / 전체 {total_in_industry}개 ({coverage_pct}% 커버리지). "
            f"P25/P75 계산 생략. `kreports collect-seed`로 추가 수집 권장."
        )
    else:
        note = f"peer {n}개 / 전체 {total_in_industry}개 ({coverage_pct}%). fs_div={fs_div}, year={year}."

    # resolve_peers에서 받은 note(sector + fallback 마커)를 prefix로 합성.
    # 기존 희소성 경고/커버리지 메시지는 보존한다.
    if resolution_note:
        note = f"{resolution_note} · {note}"

    peers_out = peers_all[:peer_limit] if include_peers else []

    subject = None
    if subject_corp_code:
        # subject는 resolve_peers에서 제외되므로 peers_all에는 없음.
        # subject_value를 별도 조회 결과로 사용한다 (peer_resolution 경로).
        # legacy 경로(peer_resolution=None)에서는 subject_corp_code가 None이라
        # 이 분기에 진입하지 않는다.
        subject_peer_in_returned = any(
            p["corp_code"] == subject_corp_code for p in peers_out
        )
        subject = {
            "corp_code": subject_corp_code,
            "corp_name": subject_name,
            "value": subject_value,
            "subject_has_metric": subject_value is not None,
            "found_in_returned_peers": subject_peer_in_returned,
        }
        if subject_value is not None and n >= 1:
            # subject를 포함한 전체 분포 기준 percentile
            all_values = sorted(values + [subject_value])
            rank = sum(1 for v in all_values if v < subject_value)
            denom = max(len(all_values) - 1, 1)
            subject["percentile"] = round(100.0 * rank / denom, 1)
        else:
            subject["percentile"] = None

    result = {
        "induty_code": induty_code,
        "match_prefix": match_prefix,
        "industry_name": industry_name,
        "prefix_len": matched_prefix_len,
        "matched_prefix_len": matched_prefix_len,
        "requested_prefix_len": prefix_len,
        "metric": metric,
        "unit": _METRIC_UNIT.get(metric),
        "year": year,
        "fs_div": fs_div,
        "n": n,
        "total_in_industry": total_in_industry,
        "coverage_pct": coverage_pct,
        "quantiles": quantiles,
        "peers": peers_out,
        "peer_limit": peer_limit,
        "truncated": include_peers and len(peers_all) > peer_limit,
        "sector_group": sector_group_val,
        "confidence": confidence_band(n),
        "excluded_categories": excluded_categories,
        "size_bucket_applied": size_bucket_applied,
        "note": note,
    }
    if subject is not None:
        result["subject"] = subject
    return result


# ---------------------------------------------------------------------------
# 업종 비교 (회사 기준 wrapper)
# ---------------------------------------------------------------------------

def compare_to_industry(
    company: str | None = None,
    induty_code: str | None = None,
    metric: str = "영업이익률",
    year: Optional[int] = None,
    fs_div: str = "CFS",
    prefix_len: int = 2,
    include_peers: bool = True,
    peer_limit: int = 50,
) -> dict:
    """
    회사 또는 업종코드를 기준으로 동종업종 내 상대 위치를 반환한다.

    Args:
        company: corp_code / stock_code / 회사명
        induty_code: 회사 대신 직접 KSIC 코드 지정
        metric: 비교 지표
        year: 사업연도 (Q4 기준)
        fs_div: CFS / OFS
        prefix_len: induty_code prefix 길이
        include_peers: peer 리스트 포함 여부
        peer_limit: peer 리스트 최대 개수

    Returns:
        get_industry_aggregates 반환값 + subject 정보
    """
    if company:
        corp_code = resolve_corp_code(company)
        if corp_code is None:
            return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

        with get_session() as session:
            row = session.query(Company).filter_by(corp_code=corp_code).first()
            if row is None:
                return {"error": f"corp_code '{corp_code}'를 찾을 수 없습니다."}
            if not row.induty_code:
                return {
                    "error": (
                        f"'{row.corp_name}'에 induty_code가 없습니다. "
                        "kreports enrich-market으로 업종코드를 보완하세요."
                    )
                }
            resolved_induty = row.induty_code
            subject_name = row.corp_name
            subject_corp_code = corp_code
    elif induty_code:
        resolved_induty = str(induty_code).strip()
        subject_name = None
        subject_corp_code = None
    else:
        return {"error": "company 또는 induty_code 중 하나를 제공해야 합니다."}

    result = get_industry_aggregates(
        induty_code=resolved_induty,
        metric=metric,
        year=year,
        fs_div=fs_div,
        prefix_len=prefix_len,
        include_peers=include_peers,
        peer_limit=peer_limit,
        subject_corp_code=subject_corp_code,
        subject_name=subject_name,
    )

    if "error" in result:
        return result

    return result


# ---------------------------------------------------------------------------
# 업종 비교 — 다지표·다년도 (compare_to_industry_multi)
# ---------------------------------------------------------------------------

_ALL_METRICS = [
    "영업이익률", "순이익률", "부채비율", "ROE", "ROA",
    "자기자본비율", "매출성장률", "Beneish_M",
]


def compare_to_industry_multi(
    company: str,
    metrics: Optional[list[str]] = None,
    years_back: int = 5,
    fs_div: str = "CFS",
    fs_strategy: str = "CFS",
    prefix_len_start: int = 3,
    exclude_other_sectors: bool = True,
    size_bucket_decade: Optional[float] = None,
) -> dict:
    """다지표·다년도 동종업종 분포 + subject percentile.

    Peer 풀은 resolve_peers로 한 번만 산정한다 (subject 최신 Q4 연도 기준).
    그 peer 풀 위에서 metric × year matrix를 만든다.

    Args:
        company: corp_code / stock_code / 회사명
        metrics: 비교 지표 리스트. None이면 _ALL_METRICS 8개 사용.
        years_back: 최근 N개 연도 (기본 5).
        fs_div: CFS / OFS
        prefix_len_start: KSIC prefix 시작 길이 (resolve_peers로 전달).
        exclude_other_sectors: 금융/지주/부동산/일반 mutual exclusion 적용.
        size_bucket_decade: 자산총계 log10 거리 한도 (opt-in).

    Returns:
        {
          "subject": {"corp_code", "corp_name", "induty_code"},
          "sector_group", "matched_prefix_len", "n_peers", "confidence",
          "excluded_categories", "size_bucket_applied", "fs_div",
          "years": [int, ...],
          "metrics": [str, ...],
          "results": {year: {metric: {"p25", "p50", "p75", "n",
                                      "subject_value", "percentile", "unit"}}},
          "note": str,
        }
    """
    corp_code = resolve_corp_code(company)
    if corp_code is None:
        return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

    if metrics is None:
        metrics = list(_ALL_METRICS)
    invalid = [m for m in metrics if m not in _METRIC_SQL]
    if invalid:
        return {
            "error": f"지원하지 않는 metric: {invalid}. 지원: {list(_METRIC_SQL.keys())}"
        }

    # subject 메타
    with _engine.connect() as conn:
        subject_row = conn.execute(
            text("SELECT corp_name, induty_code FROM companies WHERE corp_code = :cc"),
            {"cc": corp_code},
        ).first()
    if subject_row is None:
        return {"error": f"corp_code '{corp_code}' 미등록"}
    subject_name, subject_induty = subject_row[0], subject_row[1]
    requested_fs_div = fs_div
    if fs_strategy.lower() == "auto":
        fs_div = resolve_fs_div_for_company(corp_code, None, "auto")

    # Peer 풀은 한 번만 결정 (subject의 최신 Q4 연도 기준)
    pr = resolve_peers(
        corp_code,
        prefix_len_start=prefix_len_start,
        min_n=5,
        exclude_other_sectors=exclude_other_sectors,
        size_bucket_decade=size_bucket_decade,
        fs_div=fs_div,
    )

    subject_meta = {
        "corp_code": corp_code,
        "corp_name": subject_name,
        "induty_code": subject_induty,
    }

    if pr.n_peers == 0:
        return {
            "subject": subject_meta,
            "sector_group": pr.sector_group.value,
            "matched_prefix_len": pr.matched_prefix_len,
            "n_peers": 0,
            "confidence": pr.confidence,
            "excluded_categories": pr.excluded_categories,
            "size_bucket_applied": pr.size_bucket_applied,
            "fs_div": fs_div,
            "fs_strategy": fs_strategy,
            "requested_fs_div": requested_fs_div,
            "fs_div_used": fs_div,
            "years": [],
            "metrics": metrics,
            "results": {},
            "note": pr.note,
        }

    # 최신 연도: resolve_peers가 산정한 결과 우선, 없으면 (peers + subject)에서 MAX
    latest_year = pr.resolved_year
    if latest_year is None:
        with _engine.connect() as conn:
            stmt = text(
                "SELECT MAX(year) FROM financials "
                "WHERE quarter = 4 AND fs_div = :fs AND corp_code IN :ccs"
            ).bindparams(bindparam("ccs", expanding=True))
            latest_row = conn.execute(
                stmt,
                {
                    "fs": fs_div,
                    "ccs": list(pr.peer_corp_codes) + [corp_code],
                },
            ).first()
        latest_year = latest_row[0] if latest_row and latest_row[0] else None

    if latest_year is None:
        return {
            "subject": subject_meta,
            "sector_group": pr.sector_group.value,
            "matched_prefix_len": pr.matched_prefix_len,
            "n_peers": pr.n_peers,
            "confidence": pr.confidence,
            "excluded_categories": pr.excluded_categories,
            "size_bucket_applied": pr.size_bucket_applied,
            "fs_div": fs_div,
            "fs_strategy": fs_strategy,
            "requested_fs_div": requested_fs_div,
            "fs_div_used": fs_div,
            "years": [],
            "metrics": metrics,
            "results": {},
            "note": (pr.note + " · " if pr.note else "") + "최신 Q4 재무 데이터 없음",
        }

    years = list(range(int(latest_year) - years_back + 1, int(latest_year) + 1))

    results: dict[int, dict[str, dict]] = {}
    with _engine.connect() as conn:
        for y in years:
            row_y: dict[str, dict] = {}
            for metric in metrics:
                expr = _METRIC_SQL[metric]
                peer_rows = _fetch_metric_values(
                    conn, pr.peer_corp_codes, expr, y, fs_div
                )
                vals = sorted(float(r[3]) for r in peer_rows if r[3] is not None)
                subj_rows = _fetch_metric_values(
                    conn, [corp_code], expr, y, fs_div
                )
                subj_val = (
                    float(subj_rows[0][3]) if subj_rows and subj_rows[0][3] is not None
                    else None
                )

                n = len(vals)
                p50 = round(_quantile(vals, 0.50), 2) if n >= 1 else None
                p25 = round(_quantile(vals, 0.25), 2) if n >= 5 else None
                p75 = round(_quantile(vals, 0.75), 2) if n >= 5 else None

                percentile = None
                if subj_val is not None and n >= 1:
                    below = sum(1 for v in vals if v < subj_val)
                    percentile = round(100.0 * below / n, 1)

                row_y[metric] = {
                    "p25": p25,
                    "p50": p50,
                    "p75": p75,
                    "n": n,
                    "subject_value": round(subj_val, 2) if subj_val is not None else None,
                    "percentile": percentile,
                    "unit": _METRIC_UNIT.get(metric),
                }
            results[y] = row_y

    return {
        "subject": subject_meta,
        "sector_group": pr.sector_group.value,
        "matched_prefix_len": pr.matched_prefix_len,
        "n_peers": pr.n_peers,
        "confidence": pr.confidence,
        "excluded_categories": pr.excluded_categories,
        "size_bucket_applied": pr.size_bucket_applied,
        "fs_div": fs_div,
        "fs_strategy": fs_strategy,
        "requested_fs_div": requested_fs_div,
        "fs_div_used": fs_div,
        "years": years,
        "metrics": metrics,
        "results": results,
        "note": pr.note,
    }


def select_peer_group(
    company: str,
    criteria: Optional[list[str]] = None,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    prefix_len_start: int = 3,
    size_bucket_decade: Optional[float] = None,
    exclude_other_sectors: bool = True,
) -> dict:
    criteria = criteria or ["industry", "sector", "financial_data"]
    corp_code = resolve_corp_code(company)
    if corp_code is None:
        return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

    with _engine.connect() as conn:
        subject_row = conn.execute(
            text("SELECT corp_name, stock_code, market, induty_code FROM companies WHERE corp_code=:cc"),
            {"cc": corp_code},
        ).first()
    if subject_row is None:
        return {"error": f"corp_code '{corp_code}' 미등록"}

    fs_div_used = resolve_fs_div_for_company(corp_code, None, fs_strategy)
    pr = resolve_peers(
        corp_code=corp_code,
        prefix_len_start=prefix_len_start,
        min_n=5,
        exclude_other_sectors=exclude_other_sectors,
        size_bucket_decade=size_bucket_decade,
        fs_div=fs_div_used,
    )

    peers: list[dict] = []
    if pr.peer_corp_codes:
        stmt = text(
            """
            SELECT c.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   f.total_assets, f.revenue,
                   af.audit_fee_m, af.audit_hours, af.nas_ratio
            FROM companies c
            LEFT JOIN financials f
              ON f.corp_code=c.corp_code
             AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
            LEFT JOIN audit_fees af
              ON af.corp_code=c.corp_code AND af.bsns_year=:year
            WHERE c.corp_code IN :ccs
            ORDER BY (f.total_assets IS NULL), f.total_assets DESC
            LIMIT :limit
            """
        ).bindparams(bindparam("ccs", expanding=True))
        with _engine.connect() as conn:
            rows = conn.execute(
                stmt,
                {
                    "ccs": pr.peer_corp_codes,
                    "year": pr.resolved_year,
                    "fs": fs_div_used,
                    "limit": peer_limit,
                },
            ).mappings().all()
        for row in rows:
            reasons = ["same_ksic_prefix", f"sector_group:{pr.sector_group.value}"]
            if size_bucket_decade is not None:
                reasons.append("asset_size_bucket")
            if row["audit_fee_m"] is not None:
                reasons.append("audit_fee_available")
            peers.append({**dict(row), "include_reasons": reasons})

    return {
        "subject": {
            "corp_code": corp_code,
            "stock_code": subject_row[1],
            "corp_name": subject_row[0],
            "market": subject_row[2],
            "induty_code": subject_row[3],
        },
        "selection_policy": {
            "criteria": criteria,
            "prefix_len_start": prefix_len_start,
            "matched_prefix_len": pr.matched_prefix_len,
            "exclude_other_sectors": exclude_other_sectors,
            "size_bucket_decade": size_bucket_decade,
            "fs_strategy": fs_strategy,
            "fs_div_used": fs_div_used,
            "resolved_year": pr.resolved_year,
        },
        "peer_count": pr.n_peers,
        "returned_peer_count": len(peers),
        "confidence": pr.confidence,
        "peers": peers,
        "excluded_categories": pr.excluded_categories,
        "note": pr.note,
    }


def _percentile(value: float | None, values: list[float]) -> float | None:
    if value is None or not values:
        return None
    below = sum(1 for v in values if v < value)
    return round(100.0 * below / len(values), 1)


def _metric_quantiles(values: list[float]) -> dict:
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    return {
        "n": n,
        "p25": round(_quantile(vals, 0.25), 2) if n >= 5 else None,
        "p50": round(_quantile(vals, 0.50), 2) if n else None,
        "p75": round(_quantile(vals, 0.75), 2) if n >= 5 else None,
    }


def compare_peer_audit_fees(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    size_bucket_decade: Optional[float] = None,
) -> dict:
    base = select_peer_group(
        company=company,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        size_bucket_decade=size_bucket_decade,
    )
    if "error" in base:
        return base
    corp_code = base["subject"]["corp_code"]
    fs_div = base["selection_policy"]["fs_div_used"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes

    stmt = text(
        """
        SELECT c.corp_code, c.corp_name, f.total_assets,
               af.audit_fee_m, af.audit_hours, af.non_audit_fee_m, af.nas_ratio,
               CASE WHEN f.total_assets > 0 AND af.audit_fee_m IS NOT NULL
                    THEN 10000.0 * af.audit_fee_m * 1000000.0 / f.total_assets END AS fee_assets_bps,
               CASE WHEN af.audit_hours > 0 AND af.audit_fee_m IS NOT NULL
                    THEN 1.0 * af.audit_fee_m / af.audit_hours END AS fee_per_hour_m
        FROM companies c
        LEFT JOIN financials f
          ON f.corp_code=c.corp_code AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
        LEFT JOIN audit_fees af
          ON af.corp_code=c.corp_code AND af.bsns_year=:year
        WHERE c.corp_code IN :ccs
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine.connect() as conn:
        rows = conn.execute(stmt, {"ccs": all_codes, "year": year, "fs": fs_div}).mappings().all()

    by_cc = {row["corp_code"]: dict(row) for row in rows}
    subject_row = by_cc.get(corp_code, {})
    peer_rows = [by_cc[cc] for cc in peer_codes if cc in by_cc]
    metrics = {
        "audit_fee_m": [r["audit_fee_m"] for r in peer_rows if r["audit_fee_m"] is not None],
        "audit_hours": [r["audit_hours"] for r in peer_rows if r["audit_hours"] is not None],
        "nas_ratio": [r["nas_ratio"] for r in peer_rows if r["nas_ratio"] is not None],
        "audit_fee_to_assets_bps": [r["fee_assets_bps"] for r in peer_rows if r["fee_assets_bps"] is not None],
        "audit_fee_per_hour_m": [r["fee_per_hour_m"] for r in peer_rows if r["fee_per_hour_m"] is not None],
    }
    benchmarks = {k: _metric_quantiles([float(v) for v in vals]) for k, vals in metrics.items()}
    metric_coverage = {
        key: {
            "peer_count": len(peer_rows),
            "available_n": len(vals),
            "coverage_pct": round(100.0 * len(vals) / len(peer_rows), 1) if peer_rows else 0.0,
            "status": "usable" if len(vals) >= 5 else "limited" if vals else "missing",
        }
        for key, vals in metrics.items()
    }
    for key, vals in metrics.items():
        subj_key = {
            "audit_fee_to_assets_bps": "fee_assets_bps",
            "audit_fee_per_hour_m": "fee_per_hour_m",
        }.get(key, key)
        subj_val = subject_row.get(subj_key)
        benchmarks[key]["subject_percentile"] = _percentile(
            float(subj_val) if subj_val is not None else None,
            [float(v) for v in vals],
        )

    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "fs_div_used": fs_div,
        "peer_count": len(peer_rows),
        "subject_metrics": subject_row,
        "benchmarks": benchmarks,
        "data_quality": {
            "metric_coverage": metric_coverage,
            "limited_metrics": [
                key for key, info in metric_coverage.items()
                if info["status"] != "usable"
            ],
        },
        "peers": peer_rows[:peer_limit],
        "selection_policy": base["selection_policy"],
        "note": (
            "DART audit fee contract/status data; audit judgment not performed. "
            "Metrics with available_n < 5 are screening signals only."
        ),
    })


def compare_peer_risk_profile(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict:
    base = select_peer_group(company=company, peer_limit=peer_limit, fs_strategy=fs_strategy)
    if "error" in base:
        return base
    corp_code = base["subject"]["corp_code"]
    fs_div = base["selection_policy"]["fs_div_used"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes

    stmt = text(
        """
        SELECT f.corp_code, c.corp_name, f.revenue, f.total_assets,
               f.operating_profit, f.net_income, f.operating_cf,
               f.accrual_ratio, f.beneish_m_score,
               f.op_cf_divergence_flag, f.going_concern_flag
        FROM financials f
        JOIN companies c ON c.corp_code=f.corp_code
        WHERE f.corp_code IN :ccs AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
        """
    ).bindparams(bindparam("ccs", expanding=True))
    disc_stmt = text(
        """
        SELECT corp_code,
               SUM(CASE WHEN report_nm LIKE '%정정%' THEN 1 ELSE 0 END) restatement_like,
               SUM(CASE WHEN report_nm LIKE '%주요사항%' THEN 1 ELSE 0 END) major_event_like,
               COUNT(*) total_disclosures
        FROM disclosures
        WHERE corp_code IN :ccs
          AND disc_date >= :start_date
          AND disc_date <= :end_date
        GROUP BY corp_code
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, {"ccs": all_codes, "year": year, "fs": fs_div}).mappings().all()]
        disc_rows = conn.execute(
            disc_stmt,
            {"ccs": all_codes, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31"},
        ).mappings().all()

    disc_by_cc = {r["corp_code"]: dict(r) for r in disc_rows}
    by_cc = {r["corp_code"]: r for r in rows}
    subject_row = by_cc.get(corp_code, {})
    peer_rows = [by_cc[cc] for cc in peer_codes if cc in by_cc]

    def ratio(row: dict, numerator: str, denominator: str) -> float | None:
        n = row.get(numerator)
        d = row.get(denominator)
        if n is None or not d:
            return None
        return 100.0 * float(n) / float(d)

    derived = {
        "op_cf_to_operating_profit": [ratio(r, "operating_cf", "operating_profit") for r in peer_rows],
        "accrual_ratio": [r.get("accrual_ratio") for r in peer_rows],
        "beneish_m_score": [r.get("beneish_m_score") for r in peer_rows],
        "receivables_to_revenue": [],
        "inventory_to_revenue": [],
    }
    benchmarks = {
        k: _metric_quantiles([float(v) for v in vals if v is not None])
        for k, vals in derived.items()
    }
    metric_coverage = {
        k: {
            "peer_count": len(peer_rows),
            "available_n": benchmarks[k]["n"],
            "coverage_pct": round(100.0 * benchmarks[k]["n"] / len(peer_rows), 1) if peer_rows else 0.0,
            "status": "usable" if benchmarks[k]["n"] >= 5 else "limited" if benchmarks[k]["n"] else "missing",
        }
        for k in benchmarks
    }
    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "fs_div_used": fs_div,
        "peer_count": len(peer_rows),
        "subject_metrics": subject_row,
        "benchmarks": benchmarks,
        "data_quality": {
            "metric_coverage": metric_coverage,
            "unavailable_metrics": [
                key for key, info in metric_coverage.items()
                if info["status"] == "missing"
            ],
            "limited_metrics": [
                key for key, info in metric_coverage.items()
                if info["status"] == "limited"
            ],
        },
        "disclosure_event_counts": {
            "subject": disc_by_cc.get(corp_code, {}),
            "peers": {cc: disc_by_cc.get(cc, {}) for cc in peer_codes[:peer_limit]},
        },
        "selection_policy": base["selection_policy"],
        "note": "Risk profile is a DART-based signal pack, not audit risk assessment. Missing metrics must not be interpreted as low risk.",
    })


def compare_peer_accounting_policies(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_div: str = "CFS",
    fs_strategy: str = "auto",
) -> dict:
    """Compare cached accounting policy item coverage across selected peers.

    This is intentionally cache-only. It does not fetch DART documents at MCP
    runtime, so external users do not need a DART API key.
    """
    base = select_peer_group(company=company, peer_limit=peer_limit, fs_strategy=fs_strategy)
    if "error" in base:
        return base

    corp_code = base["subject"]["corp_code"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes
    stmt = text(
        """
        SELECT p.corp_code, c.corp_name, p.item_key, p.heading, p.body_length, p.body_hash
        FROM accounting_policy_items p
        JOIN companies c ON c.corp_code = p.corp_code
        WHERE p.corp_code IN :ccs
          AND p.bsns_year = :year
          AND p.fs_div = :fs
        ORDER BY p.corp_code, p.item_key
        """
    ).bindparams(bindparam("ccs", expanding=True))

    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            stmt, {"ccs": all_codes, "year": year, "fs": fs_div}
        ).mappings().all()]

    by_corp: dict[str, list[dict]] = {}
    for row in rows:
        by_corp.setdefault(row["corp_code"], []).append(row)

    subject_items = {
        row["item_key"]: {
            "heading": row["heading"],
            "body_length": row["body_length"],
            "body_hash": row["body_hash"],
        }
        for row in by_corp.get(corp_code, [])
    }

    item_keys = sorted({row["item_key"] for row in rows})
    peer_item_coverage = {}
    for key in item_keys:
        covered = [
            cc for cc in peer_codes
            if any(row["item_key"] == key for row in by_corp.get(cc, []))
        ]
        peer_item_coverage[key] = {
            "peer_count": len(peer_codes),
            "covered_peers": len(covered),
            "coverage_pct": round(100.0 * len(covered) / len(peer_codes), 1) if peer_codes else 0.0,
            "subject_has_item": key in subject_items,
        }

    peer_summaries = []
    for cc in peer_codes:
        items = by_corp.get(cc, [])
        if not items:
            continue
        peer_summaries.append({
            "corp_code": cc,
            "corp_name": items[0]["corp_name"],
            "item_count": len(items),
            "item_keys": sorted(row["item_key"] for row in items),
        })

    peer_coverage_pct = round(100.0 * len(peer_summaries) / len(peer_codes), 1) if peer_codes else 0.0
    data_quality = {
        "status": _cache_quality_status(
            subject_count=len(subject_items),
            peer_total=len(peer_codes),
            peer_covered=len(peer_summaries),
        ),
        "source": "accounting_policy_items",
        "requested_year": year,
        "subject_policy_count": len(subject_items),
        "peer_count": len(peer_codes),
        "peers_with_policy": len(peer_summaries),
        "peer_coverage_pct": peer_coverage_pct,
        "interpretation": (
            "Coverage measures local cache availability. Missing items must not be interpreted "
            "as absence of accounting policy disclosure."
        ),
    }

    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "fs_div": fs_div,
        "subject_policy_count": len(subject_items),
        "subject_items": subject_items,
        "peer_count": len(peer_codes),
        "peers_with_policy": len(peer_summaries),
        "peer_item_coverage": peer_item_coverage,
        "peer_summaries": peer_summaries[:peer_limit],
        "selection_policy": base["selection_policy"],
        "data_quality": data_quality,
        "coverage_note": (
            "Accounting policy comparison uses cached accounting_policy_items only; "
            "low coverage means dataset refresh is required, not that peers lack policy disclosures."
        ),
    })


_KAM_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "revenue_recognition": ["수익", "매출", "진행기준", "총액", "순액"],
    "impairment": ["손상", "회수가능", "영업권", "현금창출단위"],
    "inventory": ["재고", "평가충당", "순실현가능"],
    "fair_value": ["공정가치", "금융상품", "파생"],
    "provisions": ["충당부채", "우발", "소송"],
    "development_cost": ["개발비", "무형자산"],
    "tax": ["법인세", "이연법인세"],
}


def _topic_hits(text_value: str | None) -> list[str]:
    text_value = text_value or ""
    hits = []
    for topic, keywords in _KAM_TOPIC_KEYWORDS.items():
        if any(keyword in text_value for keyword in keywords):
            hits.append(topic)
    return hits


def compare_peer_kam_topics(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict:
    """Audit-report/KAM signal view from cached DART disclosures and sections."""
    base = select_peer_group(company=company, peer_limit=peer_limit, fs_strategy=fs_strategy)
    if "error" in base:
        return base

    corp_code = base["subject"]["corp_code"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes
    stmt = text(
        """
        SELECT d.corp_code, c.corp_name, d.rcept_no, d.disc_date, d.report_nm
        FROM disclosures d
        JOIN companies c ON c.corp_code = d.corp_code
        WHERE d.corp_code IN :ccs
          AND d.disc_date >= :start_date
          AND d.disc_date <= :end_date
          AND d.report_nm LIKE '%감사보고서%'
        ORDER BY d.corp_code, d.disc_date DESC
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            stmt,
            {"ccs": all_codes, "start_date": f"{year + 1}-01-01", "end_date": f"{year + 1}-12-31"},
        ).mappings().all()]

    events_by_corp: dict[str, list[dict]] = {}
    topic_counts = {topic: 0 for topic in _KAM_TOPIC_KEYWORDS}
    restated = delayed = 0
    for row in rows:
        row_topics = _topic_hits(row["report_nm"])
        for topic in row_topics:
            topic_counts[topic] += 1
        name = row["report_nm"] or ""
        if "정정" in name:
            restated += 1
        if "지연" in name:
            delayed += 1
        row["topic_hints"] = row_topics
        events_by_corp.setdefault(row["corp_code"], []).append(row)

    subject_events = events_by_corp.get(corp_code, [])
    peer_events = {
        cc: events_by_corp.get(cc, [])
        for cc in peer_codes
        if events_by_corp.get(cc)
    }

    dcm_select = "rs.dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
    section_stmt = text(
        f"""
        SELECT rs.corp_code, c.corp_name, rs.rcept_no, {dcm_select}, rs.source_type, rs.section_key,
               rs.section_title, rs.body_text, rs.body_length
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE rs.corp_code IN :ccs
          AND rs.bsns_year=:year
          AND rs.source_type='audit_report'
          AND rs.section_key IN ('kam', 'audit_opinion', 'emphasis', 'going_concern')
        ORDER BY rs.corp_code, rs.section_key, rs.ordinal
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine.connect() as conn:
        section_rows = [dict(r) for r in conn.execute(
            section_stmt,
            {"ccs": all_codes, "year": year},
        ).mappings().all()]

    from kreports.processor.audit_report_parser import classify_kam_topics, summarize_kam_body

    sections_by_corp: dict[str, list[dict]] = {}
    body_topic_counts: dict[str, int] = {}
    for row in section_rows:
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:1200]
        row.pop("body_text", None)
        kam_analysis = summarize_kam_body(body) if row.get("section_key") == "kam" else None
        if kam_analysis:
            row["kam_analysis"] = kam_analysis
        topics = (kam_analysis or {}).get("topics") or (
            classify_kam_topics(body) if row.get("section_key") == "kam" else []
        )
        row["topic_hints"] = topics
        for topic in topics:
            body_topic_counts[topic] = body_topic_counts.get(topic, 0) + 1
        sections_by_corp.setdefault(row["corp_code"], []).append(row)

    summary_stmt = text(
        f"""
        SELECT rs.corp_code, c.corp_name, rs.rcept_no, {dcm_select}, rs.source_type, rs.section_key,
               rs.section_title, rs.body_text, rs.body_length
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE rs.corp_code IN :ccs
          AND rs.bsns_year=:year
          AND rs.source_type='business_report'
          AND rs.section_key='kam'
        ORDER BY rs.corp_code, rs.ordinal
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine.connect() as conn:
        summary_rows = [dict(r) for r in conn.execute(
            summary_stmt,
            {"ccs": all_codes, "year": year},
        ).mappings().all()]
    for row in summary_rows:
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:1200]
        row.pop("body_text", None)

    business_summary_by_corp: dict[str, list[dict]] = {}
    for row in summary_rows:
        business_summary_by_corp.setdefault(row["corp_code"], []).append(row)

    subject_sections = sections_by_corp.get(corp_code, [])
    peer_sections = {
        cc: sections_by_corp.get(cc, [])
        for cc in peer_codes
        if sections_by_corp.get(cc)
    }
    kam_body_rows = [r for r in section_rows if r.get("section_key") == "kam"]
    kam_hint_coverage = _kam_hint_coverage([r for rows_for_corp in sections_by_corp.values() for r in rows_for_corp])
    has_body = bool(kam_body_rows)
    limitations = (
        ["KAM paragraphs are based on cached audit_report body sections, not business-report summary tables."]
        if has_body else [
            "Detailed audit-report KAM paragraphs are not yet persisted for this peer set.",
            "Business-report KAM summary, when present, is exposed separately and is not treated as the primary KAM body.",
            "Topic hints are based on cached audit-report disclosure titles only.",
            "Use as KAM coverage/event screening, not KAM determination.",
        ]
    )
    if has_body and not peer_sections:
        limitations.append(
            "Peer audit-report KAM body coverage is currently zero for the selected peer group; do not infer peer topic absence."
        )
    elif has_body and len(peer_sections) < max(1, len(peer_codes) // 2):
        limitations.append(
            "Peer audit-report KAM body coverage is limited for the selected peer group; compare topics cautiously."
        )

    data_quality = {
        "status": _cache_quality_status(
            subject_count=len([r for r in subject_sections if r.get("section_key") == "kam"]),
            peer_total=len(peer_codes),
            peer_covered=len(peer_sections),
        ),
        "source": "report_sections.audit_report",
        "requested_year": year,
        "subject_kam_body_count": len([r for r in subject_sections if r.get("section_key") == "kam"]),
        "peer_companies_with_sections": len(peer_sections),
        "peer_count": len(peer_codes),
        "total_audit_report_sections": len(section_rows),
        "total_kam_body_count": len(kam_body_rows),
        "kam_reason_coverage": kam_hint_coverage["reason"],
        "kam_procedure_coverage": kam_hint_coverage["procedure"],
        "business_report_summary_sections": len(summary_rows),
        "available_subject_kam_years": _cached_years_for_sections(corp_code, "audit_report", "kam"),
        "coverage_note": (
            "KAM body comparison uses cached audit_report report_sections. "
            "Disclosure events are not a substitute for KAM body/topic coverage."
        ),
    }

    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "peer_count": len(peer_codes),
        "audit_report_events": {
            "subject_count": len(subject_events),
            "peer_companies_with_events": len(peer_events),
            "total_events": len(rows),
            "restatement_like_events": restated,
            "delayed_submission_like_events": delayed,
        },
        "subject_events": subject_events[:10],
        "peer_event_samples": {
            cc: events[:3] for cc, events in list(peer_events.items())[:peer_limit]
        },
        "kam_topics": body_topic_counts if has_body else {
            topic: count for topic, count in topic_counts.items() if count > 0
        },
        "audit_report_sections": {
            "subject_section_count": len(subject_sections),
            "peer_companies_with_sections": len(peer_sections),
            "total_sections": len(section_rows),
            "kam_body_count": len(kam_body_rows),
            "kam_reason_coverage": kam_hint_coverage["reason"],
            "kam_procedure_coverage": kam_hint_coverage["procedure"],
            "source": "audit_report_sections" if has_body else "disclosure_events_only",
        },
        "data_quality": data_quality,
        "business_report_kam_summary": {
            "subject_summary_count": len(business_summary_by_corp.get(corp_code, [])),
            "peer_companies_with_summary": len([
                cc for cc in peer_codes if business_summary_by_corp.get(cc)
            ]),
            "total_summary_sections": len(summary_rows),
            "note": "사업보고서 KAM은 요약표/요약 문단으로만 취급하며, 상세 판단근거와 감사절차의 기준 소스는 audit_report입니다.",
        },
        "subject_business_report_kam_summary": business_summary_by_corp.get(corp_code, [])[:5],
        "subject_sections": subject_sections[:10],
        "peer_section_samples": {
            cc: sections[:3] for cc, sections in list(peer_sections.items())[:peer_limit]
        },
        "selection_policy": base["selection_policy"],
        "limitations": limitations,
    })


def get_audit_report_sections(
    company: str,
    year: int = 2025,
    section_key: str | None = None,
    source_type: str = "audit_report",
    limit: int = 20,
) -> dict:
    """Return cached audit-report body sections for a company/year."""
    corp_code = resolve_corp_code(company) or company
    comp = _company_summary(corp_code)
    if not comp:
        return {"error": "company not found", "company": company}

    if source_type not in {"audit_report", "business_report", "all"}:
        return {"error": "source_type must be audit_report, business_report, or all", "source_type": source_type}
    source_filter = ("audit_report", "business_report") if source_type == "all" else (source_type,)
    dcm_select = "dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
    stmt = text(
        f"""
        SELECT rcept_no, {dcm_select}, bsns_year, source_type, section_key, section_title,
               body_text, body_length, fetched_at
        FROM report_sections
        WHERE corp_code=:corp_code
          AND bsns_year=:year
          AND source_type IN :source_types
          AND (:section_key IS NULL OR section_key=:section_key)
        ORDER BY section_key, ordinal
        LIMIT :limit
        """
    ).bindparams(bindparam("source_types", expanding=True))
    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            stmt,
            {
                "corp_code": corp_code,
                "year": year,
                "section_key": section_key,
                "source_types": list(source_filter),
                "limit": int(limit),
            },
        ).mappings().all()]

    alternative_rows: list[dict] = []
    alternative_year: int | None = None
    if not rows:
        years = _cached_years_for_sections(
            corp_code,
            "audit_report" if source_type == "audit_report" else "business_report",
            section_key,
        ) if source_type != "all" else sorted(set(
            _cached_years_for_sections(corp_code, "audit_report", section_key)
            + _cached_years_for_sections(corp_code, "business_report", section_key)
        ), reverse=True)
        alternative_year = years[0] if years else None
        if alternative_year is not None:
            with _engine.connect() as conn:
                alternative_rows = [dict(r) for r in conn.execute(
                    stmt,
                    {
                        "corp_code": corp_code,
                        "year": alternative_year,
                        "section_key": section_key,
                        "source_types": list(source_filter),
                        "limit": min(int(limit), 5),
                    },
                ).mappings().all()]

    for row in rows:
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:2000]
        if row.get("section_key") == "kam":
            from kreports.processor.audit_report_parser import summarize_kam_body
            row["kam_analysis"] = summarize_kam_body(body)
        row.pop("body_text", None)
    _attach_related_audit_procedures(rows, corp_code=corp_code, year=year)
    for row in alternative_rows:
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:1200]
        if row.get("section_key") == "kam":
            from kreports.processor.audit_report_parser import summarize_kam_body
            row["kam_analysis"] = summarize_kam_body(body)
        row.pop("body_text", None)
    if alternative_year is not None:
        _attach_related_audit_procedures(alternative_rows, corp_code=corp_code, year=alternative_year)
    if rows:
        coverage_note = (
            "Cached audit_report report_sections."
            if source_type == "audit_report"
            else "Cached report_sections."
        )
    else:
        coverage_note = (
            "No cached sections. Run collect-audit-report-sections for detailed audit reports; "
            "business_report is summary coverage only."
        )
    kam_hint_coverage = _kam_hint_coverage(rows)
    section_quality = {
        "status": "usable" if rows else "missing",
        "source": "report_sections",
        "requested_year": year,
        "requested_source_type": source_type,
        "requested_section_key": section_key,
        "section_count": len(rows),
        "kam_reason_coverage": kam_hint_coverage["reason"],
        "kam_procedure_coverage": kam_hint_coverage["procedure"],
        "available_audit_report_years": _cached_years_for_sections(corp_code, "audit_report", section_key),
        "available_business_report_years": _cached_years_for_sections(corp_code, "business_report", section_key),
        "latest_available_year": alternative_year if not rows else year,
        "alternative_section_count": len(alternative_rows),
        "interpretation": (
            "No rows means the local cache lacks the requested section/year. "
            "It does not prove the filing lacks that audit report section."
        ),
    }
    return _clean_dict({
        "subject": comp,
        "year": year,
        "section_key": section_key,
        "source_type": source_type,
        "section_count": len(rows),
        "sections": rows,
        "alternative_sections": alternative_rows,
        "data_quality": section_quality,
        "coverage_note": coverage_note,
    })


_AUDIT_MATTER_KEYS = ("other_matter", "emphasis", "going_concern", "basis_for_opinion")

_AUDIT_MATTER_TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "going_concern": ("계속기업", "존속능력", "유동부채", "유동성"),
    "covid": ("COVID-19", "코로나", "코로나바이러스"),
    "subsequent_event": ("보고기간후", "보고기간 후", "후속사건", "작성기준일 이후"),
    "restatement": ("재작성", "재작성", "정정", "재분류", "수정"),
    "litigation": ("소송", "분쟁", "우발부채"),
    "scope_limitation": ("범위제한", "충분하고 적합한 감사증거", "의견거절"),
    "uncertainty": ("불확실성", "추정", "중요한 불확실성"),
}


def _classify_audit_matter(text_value: str, section_key: str | None = None) -> dict:
    body = text_value or ""
    topics = [
        topic
        for topic, keywords in _AUDIT_MATTER_TOPIC_KEYWORDS.items()
        if any(keyword in body for keyword in keywords)
    ]
    if section_key == "going_concern" or "going_concern" in topics:
        severity = "high"
    elif section_key == "emphasis" or any(topic in topics for topic in ("scope_limitation", "uncertainty")):
        severity = "warning"
    else:
        severity = "info"
    return {"topic_tags": topics, "severity_hint": severity}


def compare_peer_audit_report_matters(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict:
    """Compare non-KAM audit report matters across the selected peer group.

    Emphasis of matter, other matter, going concern, and basis-for-opinion
    paragraphs are not audit opinions by themselves. They are useful screening
    evidence for acceptance/continuance and peer disclosure comparison.
    """
    base = select_peer_group(company=company, peer_limit=peer_limit, fs_strategy=fs_strategy)
    if "error" in base:
        return base

    corp_code = base["subject"]["corp_code"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes
    dcm_select = "rs.dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
    stmt = text(
        f"""
        SELECT rs.corp_code, c.corp_name, rs.rcept_no, {dcm_select}, rs.source_type,
               rs.section_key, rs.section_title, rs.body_text, rs.body_length, rs.ordinal
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE rs.corp_code IN :ccs
          AND rs.bsns_year=:year
          AND rs.source_type='audit_report'
          AND rs.section_key IN :section_keys
        ORDER BY rs.corp_code, rs.section_key, rs.ordinal
        """
    ).bindparams(
        bindparam("ccs", expanding=True),
        bindparam("section_keys", expanding=True),
    )
    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            stmt,
            {"ccs": all_codes, "year": year, "section_keys": list(_AUDIT_MATTER_KEYS)},
        ).mappings().all()]

    counts = {
        key: {
            "subject_count": 0,
            "peer_companies_with_section": 0,
            "total_sections": 0,
        }
        for key in _AUDIT_MATTER_KEYS
    }
    by_corp: dict[str, list[dict]] = {}
    peer_corp_by_key: dict[str, set[str]] = {key: set() for key in _AUDIT_MATTER_KEYS}
    for row in rows:
        key = row["section_key"]
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:1200]
        row.update(_classify_audit_matter(body, key))
        row.pop("body_text", None)
        counts[key]["total_sections"] += 1
        if row["corp_code"] == corp_code:
            counts[key]["subject_count"] += 1
        elif row["corp_code"] in peer_codes:
            peer_corp_by_key[key].add(row["corp_code"])
        by_corp.setdefault(row["corp_code"], []).append(row)

    for key in _AUDIT_MATTER_KEYS:
        counts[key]["peer_companies_with_section"] = len(peer_corp_by_key[key])
        counts[key]["peer_coverage_pct"] = (
            round(100.0 * len(peer_corp_by_key[key]) / len(peer_codes), 1)
            if peer_codes else 0.0
        )

    subject_matters = by_corp.get(corp_code, [])
    peer_sections_by_corp = {
        cc: by_corp.get(cc, [])
        for cc in peer_codes
        if by_corp.get(cc)
    }
    subject_count = len(subject_matters)
    peer_covered = len(peer_sections_by_corp)
    data_quality = {
        "status": _cache_quality_status(
            subject_count=subject_count,
            peer_total=len(peer_codes),
            peer_covered=peer_covered,
        ),
        "source": "report_sections.audit_report",
        "requested_year": year,
        "section_keys": list(_AUDIT_MATTER_KEYS),
        "subject_section_count": subject_count,
        "peer_companies_with_sections": peer_covered,
        "peer_count": len(peer_codes),
        "total_sections": len(rows),
        "available_subject_years": sorted(set(
            year
            for key in _AUDIT_MATTER_KEYS
            for year in _cached_years_for_sections(corp_code, "audit_report", key)
        ), reverse=True),
        "interpretation": (
            "Emphasis/other-matter/going-concern paragraphs are audit-report "
            "screening evidence. Absence in cache does not prove absence in the filing."
        ),
    }

    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "peer_count": len(peer_codes),
        "matter_counts": counts,
        "subject_matters": subject_matters[:20],
        "peer_matter_samples": {
            cc: sections[:5] for cc, sections in list(peer_sections_by_corp.items())[:peer_limit]
        },
        "data_quality": data_quality,
        "selection_policy": base["selection_policy"],
        "limitations": [
            "These paragraphs are screening evidence, not audit conclusions.",
            "Going-concern emphasis in audit reports should be read together with the opinion and basis-for-opinion sections.",
            "Peer comparisons depend on current local report_sections coverage.",
        ],
    })


def search_audit_report_matters(
    *,
    company: str | None = None,
    year: int | None = None,
    market: str | None = None,
    induty_prefix: str | None = None,
    section_keys: list[str] | None = None,
    limit: int = 50,
    include_excerpt: bool = True,
) -> dict:
    """Search audit-report matters by company/year/industry filters.

    This backs questions like:
    - "Does company X have emphasis/other matter paragraphs?"
    - "Which companies in industry Y had emphasis/other matters in year Z?"
    """
    allowed_keys = set(_AUDIT_MATTER_KEYS)
    keys = section_keys or ["other_matter", "emphasis", "going_concern"]
    invalid = [key for key in keys if key not in allowed_keys]
    if invalid:
        return {
            "error": "invalid section_keys",
            "invalid": invalid,
            "allowed": sorted(allowed_keys),
        }
    if limit < 1:
        limit = 1
    if limit > 500:
        limit = 500

    corp_code = None
    company_summary = None
    if company:
        corp_code = resolve_corp_code(company) or company
        company_summary = _company_summary(corp_code)
        if not company_summary:
            return {"error": "company not found", "company": company}

    where = [
        "rs.source_type='audit_report'",
        "rs.section_key IN :section_keys",
    ]
    params: dict[str, object] = {"section_keys": keys}
    if corp_code:
        where.append("rs.corp_code=:corp_code")
        params["corp_code"] = corp_code
    if year is not None:
        where.append("rs.bsns_year=:year")
        params["year"] = int(year)
    if market:
        where.append("c.market=:market")
        params["market"] = market
    if induty_prefix:
        where.append("c.induty_code LIKE :induty_prefix")
        params["induty_prefix"] = f"{induty_prefix}%"

    dcm_select = "rs.dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
    sql = text(
        f"""
        SELECT rs.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
               rs.bsns_year, rs.rcept_no, {dcm_select}, rs.section_key,
               rs.section_title, rs.body_text, rs.body_length, rs.ordinal
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE {" AND ".join(where)}
        ORDER BY rs.bsns_year DESC, c.market, c.induty_code, c.corp_name, rs.section_key, rs.ordinal
        LIMIT :row_limit
        """
    ).bindparams(bindparam("section_keys", expanding=True))
    params["row_limit"] = int(limit) * 10

    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).mappings().all()]

    companies: dict[str, dict] = {}
    for row in rows:
        cc = row["corp_code"]
        item = companies.setdefault(cc, {
            "corp_code": cc,
            "stock_code": row.get("stock_code"),
            "corp_name": row.get("corp_name"),
            "market": row.get("market"),
            "induty_code": row.get("induty_code"),
            "industry_name": _get_industry_name((row.get("induty_code") or "")[:2]) if row.get("induty_code") else "",
            "years": [],
            "matter_counts": {key: 0 for key in keys},
            "sections": [],
        })
        if row["bsns_year"] not in item["years"]:
            item["years"].append(row["bsns_year"])
        item["matter_counts"][row["section_key"]] = item["matter_counts"].get(row["section_key"], 0) + 1
        section = {
            "bsns_year": row["bsns_year"],
            "rcept_no": row["rcept_no"],
            "dcm_no": row.get("dcm_no"),
            "section_key": row["section_key"],
            "section_title": row.get("section_title"),
            "body_length": row.get("body_length"),
        }
        if include_excerpt:
            body = _display_text(row.get("body_text"))
            section["body_excerpt"] = body[:1200]
            section.update(_classify_audit_matter(body, row["section_key"]))
        item["sections"].append(section)

    company_rows = list(companies.values())
    for item in company_rows:
        item["years"] = sorted([int(y) for y in item["years"]], reverse=True)
        item["total_sections"] = sum(item["matter_counts"].values())
        item["sections"] = item["sections"][:10]
    company_rows.sort(
        key=lambda item: (
            -item["total_sections"],
            item.get("market") or "",
            item.get("corp_name") or "",
        )
    )
    company_rows = company_rows[:limit]

    return _clean_dict({
        "query": {
            "company": company,
            "year": year,
            "market": market,
            "induty_prefix": induty_prefix,
            "section_keys": keys,
            "limit": limit,
            "include_excerpt": include_excerpt,
        },
        "subject": company_summary,
        "total_companies": len(company_rows),
        "total_sections": sum(item["total_sections"] for item in company_rows),
        "companies": company_rows,
        "data_quality": {
            "status": "usable" if company_rows else "missing",
            "source": "report_sections.audit_report",
            "interpretation": (
                "Results are local cached audit-report sections. Empty results mean no cached matching section, "
                "not proof that the filing has no such matter."
            ),
        },
    })


_AUDIT_PROCEDURE_TYPES = {
    "internal_control",
    "substantive_test",
    "estimation_assumption",
    "external_confirmation",
    "valuation_specialist",
    "analytics",
    "cutoff",
    "other",
}


def search_audit_procedures(
    *,
    company: str | None = None,
    year: int | None = None,
    market: str | None = None,
    induty_prefix: str | None = None,
    kam_topic: str | None = None,
    procedure_type: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    include_excerpt: bool = True,
) -> dict:
    """Search KAM audit procedures by company/year/industry/topic filters."""
    if procedure_type and procedure_type not in _AUDIT_PROCEDURE_TYPES:
        return {"error": "invalid procedure_type", "allowed": sorted(_AUDIT_PROCEDURE_TYPES)}
    limit = max(1, min(int(limit), 500))
    params: dict[str, object] = {"row_limit": limit * 10}
    filters, subject = _company_filters(
        company=company,
        market=market,
        induty_prefix=induty_prefix,
        params=params,
    )
    if "__company_not_found__" in filters:
        return {"error": "company not found", "company": company}
    where = ["1=1", *filters]
    if year is not None:
        where.append("api.bsns_year=:year")
        params["year"] = int(year)
    if kam_topic:
        where.append("api.kam_topic=:kam_topic")
        params["kam_topic"] = kam_topic
    if procedure_type:
        where.append("api.procedure_type=:procedure_type")
        params["procedure_type"] = procedure_type
    if keyword:
        where.append("api.procedure_text LIKE :kw")
        params["kw"] = f"%{keyword}%"

    sql = text(f"""
        SELECT api.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
               api.bsns_year AS year, api.rcept_no, api.dcm_no, api.source_type,
               api.kam_topic, api.procedure_type, api.procedure_text,
               api.procedure_length, api.section_ordinal, api.procedure_ordinal
        FROM audit_procedure_items api
        JOIN companies c ON c.corp_code=api.corp_code
        WHERE {" AND ".join(where)}
        ORDER BY api.bsns_year DESC, c.market, c.corp_name, api.kam_topic, api.procedure_type
        LIMIT :row_limit
    """)
    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).mappings().all()]

    for row in rows:
        text_value = _display_text(row.pop("procedure_text") or "")
        if include_excerpt:
            row["procedure_excerpt"] = text_value[:900]
    companies = _group_company_records(rows, limit=limit)
    type_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for company_row in companies:
        for record in company_row["records"]:
            type_key = record.get("procedure_type") or "unknown"
            topic_key = record.get("kam_topic") or "unknown"
            type_counts[type_key] = type_counts.get(type_key, 0) + 1
            topic_counts[topic_key] = topic_counts.get(topic_key, 0) + 1
    return _clean_dict({
        "query": {
            "company": company,
            "year": year,
            "market": market,
            "induty_prefix": induty_prefix,
            "kam_topic": kam_topic,
            "procedure_type": procedure_type,
            "keyword": keyword,
            "limit": limit,
            "include_excerpt": include_excerpt,
        },
        "subject": subject,
        "total_companies": len(companies),
        "total_procedures": sum(item["record_count"] for item in companies),
        "procedure_type_counts": type_counts,
        "kam_topic_counts": topic_counts,
        "companies": companies,
        "data_quality": {
            "status": "usable" if companies else "missing",
            "source": "audit_procedure_items",
            "interpretation": (
                "Procedure items are parsed hints from cached KAM audit-response paragraphs. "
                "They support comparison and search, but do not replace reading the full audit report."
            ),
        },
    })


def compare_peer_audit_procedures(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict:
    """Compare KAM audit procedure patterns for a subject and its peer group."""
    base = select_peer_group(company=company, peer_limit=peer_limit, fs_strategy=fs_strategy)
    if "error" in base:
        return base
    subject = base["subject"]
    peer_codes = [subject["corp_code"]] + [peer["corp_code"] for peer in base.get("peers", [])]
    if not peer_codes:
        return {"error": "no peers"}
    stmt = text("""
        SELECT api.corp_code, c.corp_name, api.kam_topic, api.procedure_type,
               COUNT(*) AS cnt
        FROM audit_procedure_items api
        JOIN companies c ON c.corp_code=api.corp_code
        WHERE api.bsns_year=:year AND api.corp_code IN :corp_codes
        GROUP BY api.corp_code, c.corp_name, api.kam_topic, api.procedure_type
        ORDER BY c.corp_name, api.kam_topic, api.procedure_type
    """).bindparams(bindparam("corp_codes", expanding=True))
    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, {"year": year, "corp_codes": peer_codes}).mappings().all()]

    subject_counts: dict[str, int] = {}
    peer_type_counts: dict[str, int] = {}
    peer_topic_counts: dict[str, int] = {}
    companies_with_procedures: set[str] = set()
    for row in rows:
        companies_with_procedures.add(row["corp_code"])
        key = row["procedure_type"] or "other"
        topic = row["kam_topic"] or "unknown"
        if row["corp_code"] == subject["corp_code"]:
            subject_counts[key] = subject_counts.get(key, 0) + int(row["cnt"] or 0)
        else:
            peer_type_counts[key] = peer_type_counts.get(key, 0) + int(row["cnt"] or 0)
            peer_topic_counts[topic] = peer_topic_counts.get(topic, 0) + int(row["cnt"] or 0)

    return _clean_dict({
        "subject": subject,
        "year": year,
        "peer_count": len(base.get("peers", [])),
        "companies_with_procedures": len(companies_with_procedures),
        "subject_procedure_type_counts": subject_counts,
        "peer_procedure_type_counts": peer_type_counts,
        "peer_kam_topic_counts": peer_topic_counts,
        "selection_policy": base.get("selection_policy"),
        "data_quality": {
            "status": "usable" if rows else "missing",
            "source": "audit_procedure_items",
            "coverage_note": "Parsed from cached KAM sections; coverage increases as source_documents and extractors backfill.",
        },
    })


_SEARCH_DATASETS = {
    "source_documents",
    "report_sections",
    "accounting_policies",
    "accounting_note_chapters",
    "evidence_documents",
    "disclosures",
    "audit_fees",
    "financials",
}


def _company_filters(
    *,
    company: str | None,
    market: str | None,
    induty_prefix: str | None,
    params: dict[str, object],
    alias: str = "c",
) -> tuple[list[str], dict | None]:
    filters: list[str] = []
    subject = None
    if company:
        corp_code = resolve_corp_code(company) or company
        subject = _company_summary(corp_code)
        if not subject:
            return ["__company_not_found__"], None
        filters.append(f"{alias}.corp_code=:corp_code")
        params["corp_code"] = corp_code
    if market:
        filters.append(f"{alias}.market=:market")
        params["market"] = market
    if induty_prefix:
        filters.append(f"{alias}.induty_code LIKE :induty_prefix")
        params["induty_prefix"] = f"{induty_prefix}%"
    return filters, subject


def _group_company_records(rows: list[dict], *, limit: int) -> list[dict]:
    grouped: dict[str, dict] = {}
    for row in rows:
        cc = row.pop("corp_code")
        item = grouped.setdefault(cc, {
            "corp_code": cc,
            "stock_code": row.pop("stock_code", None),
            "corp_name": row.pop("corp_name", None),
            "market": row.pop("market", None),
            "induty_code": row.pop("induty_code", None),
            "records": [],
        })
        item["records"].append(row)
    companies = list(grouped.values())
    for item in companies:
        item["record_count"] = len(item["records"])
        item["records"] = item["records"][:10]
    companies.sort(key=lambda item: (-item["record_count"], item.get("market") or "", item.get("corp_name") or ""))
    return companies[:limit]


def search_dataset(
    *,
    dataset: str,
    company: str | None = None,
    year: int | None = None,
    market: str | None = None,
    induty_prefix: str | None = None,
    keyword: str | None = None,
    source_type: str | None = None,
    section_keys: list[str] | None = None,
    section_type: str | None = None,
    fs_div: str | None = None,
    quarter: int | None = None,
    limit: int = 50,
    include_excerpt: bool = True,
) -> dict:
    """Unified cache search over the main local dataset tables."""
    if dataset not in _SEARCH_DATASETS:
        return {"error": "invalid dataset", "dataset": dataset, "allowed": sorted(_SEARCH_DATASETS)}
    limit = max(1, min(int(limit), 500))
    params: dict[str, object] = {"row_limit": limit * 10}
    filters, subject = _company_filters(
        company=company,
        market=market,
        induty_prefix=induty_prefix,
        params=params,
    )
    if "__company_not_found__" in filters:
        return {"error": "company not found", "company": company}

    bind_expanding = []
    source = dataset
    interpretation = "Search reads local cached tables only. Empty result means no cached matching row."
    if dataset == "source_documents":
        where = ["1=1", *filters]
        if year is not None:
            where.append("sd.bsns_year=:year")
            params["year"] = int(year)
        if source_type:
            where.append("sd.source_type=:source_type")
            params["source_type"] = source_type
        if keyword:
            params["row_limit"] = max(params["row_limit"], limit * 50)
        sql = f"""
            SELECT sd.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   sd.bsns_year AS year, sd.rcept_no, sd.dcm_no, sd.source_type,
                   sd.report_nm, sd.content_type, sd.storage_uri, sd.doc_hash,
                   sd.content_length, sd.compressed_length, sd.storage_status,
                   CASE WHEN sd.content_type='derived_report_sections' THEN sd.raw_content ELSE '' END AS _inline_body,
                   CASE
                     WHEN sd.content_length IS NOT NULL THEN sd.content_length
                     ELSE LENGTH(sd.raw_content)
                   END AS body_length
            FROM source_documents sd
            JOIN companies c ON c.corp_code=sd.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY sd.bsns_year DESC, c.market, c.corp_name, sd.source_type
            LIMIT :row_limit
        """
        source = "source_documents"
        interpretation = (
            "Search reads source_documents cache. Rows with content_type=derived_report_sections "
            "are reconstructed legacy evidence bundles, not original DART filing bodies. "
            "Raw body keyword search is bounded to selected metadata candidates; use evidence_documents "
            "for broad text search."
        )
    elif dataset == "report_sections":
        where = ["1=1", *filters]
        if year is not None:
            where.append("rs.bsns_year=:year")
            params["year"] = int(year)
        if source_type:
            where.append("rs.source_type=:source_type")
            params["source_type"] = source_type
        if section_keys:
            where.append("rs.section_key IN :section_keys")
            params["section_keys"] = section_keys
            bind_expanding.append("section_keys")
        if keyword:
            where.append("(rs.section_title LIKE :kw OR rs.body_text LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        dcm_select = "rs.dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
        sql = f"""
            SELECT rs.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   rs.bsns_year AS year, rs.rcept_no, {dcm_select}, rs.source_type,
                   rs.section_key, rs.section_title, rs.body_text, rs.body_length
            FROM report_sections rs
            JOIN companies c ON c.corp_code=rs.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY rs.bsns_year DESC, c.market, c.corp_name, rs.section_key
            LIMIT :row_limit
        """
    elif dataset == "accounting_policies":
        where = ["1=1", *filters]
        if year is not None:
            where.append("api.bsns_year=:year")
            params["year"] = int(year)
        if fs_div:
            where.append("api.fs_div=:fs_div")
            params["fs_div"] = fs_div
        if keyword:
            where.append("(api.item_key LIKE :kw OR api.heading LIKE :kw OR api.body LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT api.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   api.bsns_year AS year, api.fs_div, api.rcept_no, api.item_key,
                   api.heading, api.body, api.body_length
            FROM accounting_policy_items api
            JOIN companies c ON c.corp_code=api.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY api.bsns_year DESC, c.market, c.corp_name, api.item_key
            LIMIT :row_limit
        """
        source = "accounting_policy_items"
    elif dataset == "accounting_note_chapters":
        where = ["1=1", *filters]
        if year is not None:
            where.append("anc.bsns_year=:year")
            params["year"] = int(year)
        if fs_div:
            where.append("anc.fs_div=:fs_div")
            params["fs_div"] = fs_div
        if source_type:
            where.append("anc.source_type=:source_type")
            params["source_type"] = source_type
        if section_type:
            where.append("anc.section_type=:section_type")
            params["section_type"] = section_type
        if keyword:
            where.append("(anc.note_title LIKE :kw OR anc.body LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT anc.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   anc.bsns_year AS year, anc.fs_div, anc.rcept_no, anc.dcm_no,
                   anc.source_type, anc.note_no, anc.note_title, anc.section_type,
                   anc.body, anc.body_length
            FROM accounting_note_chapters anc
            JOIN companies c ON c.corp_code=anc.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY anc.bsns_year DESC, c.market, c.corp_name, anc.note_no
            LIMIT :row_limit
        """
        source = "accounting_note_chapters"
    elif dataset == "evidence_documents":
        where = ["1=1", *filters]
        if year is not None:
            where.append("ed.bsns_year=:year")
            params["year"] = int(year)
        if source_type:
            where.append("ed.source_type=:source_type")
            params["source_type"] = source_type
        if keyword:
            where.append("(ed.title LIKE :kw OR ed.normalized_text LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT ed.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   ed.bsns_year AS year, ed.rcept_no, ed.dcm_no, ed.source_type,
                   ed.evidence_scope, ed.title, ed.normalized_text AS body,
                   ed.text_length AS body_length, ed.source_count, ed.generated_at
            FROM evidence_documents ed
            JOIN companies c ON c.corp_code=ed.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY ed.bsns_year DESC, c.market, c.corp_name, ed.source_type
            LIMIT :row_limit
        """
        source = "evidence_documents"
        interpretation = (
            "Search reads compact normalized evidence bundles derived from report_sections, "
            "accounting note chapters, accounting policies, and audit procedures. It is suitable "
            "for MCP narrative answers, while raw DART XML/HTML remains the source of record."
        )
    elif dataset == "disclosures":
        where = ["1=1", *filters]
        if year is not None:
            where.append("d.disc_date BETWEEN :start_date AND :end_date")
            params["start_date"] = f"{int(year)}-01-01"
            params["end_date"] = f"{int(year)}-12-31"
        if keyword:
            where.append("(d.report_nm LIKE :kw OR d.flr_nm LIKE :kw)")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT d.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   d.rcept_no, d.disc_date, d.disc_type, d.report_nm, d.flr_nm
            FROM disclosures d
            JOIN companies c ON c.corp_code=d.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY d.disc_date DESC, c.market, c.corp_name
            LIMIT :row_limit
        """
    elif dataset == "audit_fees":
        where = ["1=1", *filters]
        if year is not None:
            where.append("af.bsns_year=:year")
            params["year"] = int(year)
        if keyword:
            where.append("af.auditor_nm LIKE :kw")
            params["kw"] = f"%{keyword}%"
        sql = f"""
            SELECT af.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   af.bsns_year AS year, af.auditor_nm, af.audit_fee_m,
                   af.audit_hours, af.non_audit_fee_m, af.nas_ratio,
                   af.independence_risk_flag
            FROM audit_fees af
            JOIN companies c ON c.corp_code=af.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY af.bsns_year DESC, c.market, c.corp_name
            LIMIT :row_limit
        """
    else:
        where = ["1=1", *filters]
        if year is not None:
            where.append("f.year=:year")
            params["year"] = int(year)
        if fs_div:
            where.append("f.fs_div=:fs_div")
            params["fs_div"] = fs_div
        if quarter is not None:
            where.append("f.quarter=:quarter")
            params["quarter"] = int(quarter)
        sql = f"""
            SELECT f.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   f.year, f.quarter, f.fs_div, f.revenue, f.operating_profit,
                   f.net_income, f.total_assets, f.total_debt, f.total_equity,
                   f.operating_cf, f.going_concern_flag, f.op_cf_divergence_flag,
                   f.beneish_m_score, f.source
            FROM financials f
            JOIN companies c ON c.corp_code=f.corp_code
            WHERE {" AND ".join(where)}
            ORDER BY f.year DESC, f.quarter DESC, c.market, c.corp_name
            LIMIT :row_limit
        """

    stmt = text(sql)
    for key in bind_expanding:
        stmt = stmt.bindparams(bindparam(key, expanding=True))
    with _engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, params).mappings().all()]

    if dataset == "source_documents":
        filtered_rows = []
        for row in rows:
            matched, excerpt = _load_source_document_excerpt(row, keyword=keyword)
            if not matched:
                continue
            if include_excerpt:
                row["body_excerpt"] = excerpt
            filtered_rows.append(row)
            if len(filtered_rows) >= limit * 10:
                break
        rows = filtered_rows

    for row in rows:
        if "body_text" in row:
            body = _display_text(row.pop("body_text") or "")
            if include_excerpt:
                row["body_excerpt"] = body[:1200]
        if "body" in row:
            body = _display_text(row.pop("body") or "")
            if include_excerpt:
                row["body_excerpt"] = body[:1200]

    companies = _group_company_records(rows, limit=limit)
    return _clean_dict({
        "query": {
            "dataset": dataset,
            "company": company,
            "year": year,
            "market": market,
            "induty_prefix": induty_prefix,
            "keyword": keyword,
            "source_type": source_type,
            "section_keys": section_keys,
            "section_type": section_type,
            "fs_div": fs_div,
            "quarter": quarter,
            "limit": limit,
            "include_excerpt": include_excerpt,
        },
        "subject": subject,
        "total_companies": len(companies),
        "total_records": sum(item["record_count"] for item in companies),
        "companies": companies,
        "data_quality": {
            "status": "usable" if companies else "missing",
            "source": source,
            "interpretation": interpretation,
        },
    })


def estimate_audit_hours_proxy(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict:
    """Estimate public-data audit complexity proxy for planning discussion."""
    fee_pack = compare_peer_audit_fees(
        company=company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy
    )
    if "error" in fee_pack:
        return fee_pack
    risk_pack = compare_peer_risk_profile(
        company=company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy
    )

    subject_metrics = fee_pack.get("subject_metrics") or {}
    risk_metrics = risk_pack.get("subject_metrics") or {}
    benchmarks = fee_pack.get("benchmarks") or {}

    drivers = []
    score = 0

    total_assets = subject_metrics.get("total_assets")
    if total_assets:
        score += 20
        drivers.append({
            "driver": "size",
            "signal": "total_assets_available",
            "points": 20,
            "score_after": score,
        })

    audit_hours = subject_metrics.get("audit_hours")
    if audit_hours:
        pctl = (benchmarks.get("audit_hours") or {}).get("subject_percentile")
        if pctl is not None and pctl >= 75:
            points = 25
            level = "high_vs_peers"
        elif pctl is not None and pctl <= 25:
            points = 5
            level = "low_vs_peers"
        else:
            points = 15
            level = "mid_vs_peers"
        score += points
        drivers.append({
            "driver": "audit_hours",
            "signal": level,
            "points": points,
            "score_after": score,
        })

    if risk_metrics.get("op_cf_divergence_flag"):
        score += 15
        drivers.append({
            "driver": "cashflow_divergence",
            "signal": "flagged",
            "points": 15,
            "score_after": score,
        })
    if risk_metrics.get("going_concern_flag"):
        score += 20
        drivers.append({
            "driver": "going_concern",
            "signal": "flagged",
            "points": 20,
            "score_after": score,
        })
    if risk_metrics.get("beneish_m_score") is not None and risk_metrics.get("beneish_m_score") > -1.78:
        score += 10
        drivers.append({
            "driver": "beneish_m_score",
            "signal": "above_screening_threshold",
            "points": 10,
            "score_after": score,
        })

    complexity_score = min(score, 100)
    if complexity_score >= 70:
        band = "high"
    elif complexity_score >= 40:
        band = "medium"
    else:
        band = "low"

    return _clean_dict({
        "subject": fee_pack["subject"],
        "year": year,
        "peer_count": fee_pack.get("peer_count"),
        "complexity_score": complexity_score,
        "complexity_band": band,
        "drivers": drivers,
        "peer_benchmarks": {
            "audit_hours": benchmarks.get("audit_hours"),
            "audit_fee_to_assets_bps": benchmarks.get("audit_fee_to_assets_bps"),
            "audit_fee_per_hour_m": benchmarks.get("audit_fee_per_hour_m"),
        },
        "data_quality": {
            "audit_fee_metrics": fee_pack.get("data_quality"),
            "risk_metrics": risk_pack.get("data_quality"),
        },
        "subject_metrics": {
            "audit_hours": audit_hours,
            "audit_fee_m": subject_metrics.get("audit_fee_m"),
            "total_assets": total_assets,
            "op_cf_divergence_flag": risk_metrics.get("op_cf_divergence_flag"),
            "going_concern_flag": risk_metrics.get("going_concern_flag"),
            "beneish_m_score": risk_metrics.get("beneish_m_score"),
        },
        "selection_policy": fee_pack.get("selection_policy"),
        "limitations": [
            "This is not a standard audit hour calculation.",
            "It is a public DART data proxy for planning discussion and peer comparison.",
        ],
    })


def build_audit_acceptance_pack(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict:
    """Build a compact DART evidence pack for acceptance/continuance screening."""
    peer_group = select_peer_group(company=company, peer_limit=peer_limit, fs_strategy=fs_strategy)
    if "error" in peer_group:
        return peer_group
    fee_pack = compare_peer_audit_fees(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy)
    risk_pack = compare_peer_risk_profile(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy)
    hours_pack = estimate_audit_hours_proxy(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy)
    policy_pack = compare_peer_accounting_policies(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy)
    kam_pack = compare_peer_kam_topics(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy)
    matter_pack = compare_peer_audit_report_matters(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy)

    acceptance_signals = []
    subject_fee = fee_pack.get("subject_metrics") or {}
    if subject_fee.get("nas_ratio") is not None and subject_fee.get("nas_ratio") > 1.0:
        acceptance_signals.append({
            "area": "independence",
            "severity": "review",
            "signal": "non_audit_fee_exceeds_audit_fee",
        })
    risk_subject = risk_pack.get("subject_metrics") or {}
    if risk_subject.get("going_concern_flag"):
        acceptance_signals.append({
            "area": "going_concern",
            "severity": "review",
            "signal": "loss_based_going_concern_flag",
        })
    if risk_subject.get("op_cf_divergence_flag"):
        acceptance_signals.append({
            "area": "cashflow_quality",
            "severity": "review",
            "signal": "positive_operating_profit_negative_operating_cashflow",
        })
    if hours_pack.get("complexity_band") == "high":
        acceptance_signals.append({
            "area": "audit_effort",
            "severity": "review",
            "signal": "high_public_data_complexity_proxy",
        })
    matter_counts = matter_pack.get("matter_counts") or {}
    if (matter_counts.get("emphasis") or {}).get("subject_count"):
        acceptance_signals.append({
            "area": "audit_report_matters",
            "severity": "review",
            "signal": "audit_report_emphasis_paragraph_present",
        })
    if (matter_counts.get("going_concern") or {}).get("subject_count"):
        acceptance_signals.append({
            "area": "going_concern",
            "severity": "review",
            "signal": "audit_report_going_concern_paragraph_present",
        })
    if (matter_counts.get("other_matter") or {}).get("subject_count"):
        acceptance_signals.append({
            "area": "audit_report_matters",
            "severity": "info",
            "signal": "audit_report_other_matter_paragraph_present",
        })

    policy_peer_count = policy_pack.get("peer_count") or 0
    peers_with_policy = policy_pack.get("peers_with_policy") or 0
    policy_coverage_pct = (
        round(100.0 * peers_with_policy / policy_peer_count, 1)
        if policy_peer_count else 0.0
    )
    kam_events = (kam_pack.get("audit_report_events") or {}).get("total_events") or 0
    kam_section_quality = kam_pack.get("audit_report_sections") or {}
    kam_body_count = kam_section_quality.get("kam_body_count") or 0
    kam_reason_coverage = kam_section_quality.get("kam_reason_coverage") or {}
    kam_procedure_coverage = kam_section_quality.get("kam_procedure_coverage") or {}
    data_quality = {
        "policy_cache": {
            "subject_policy_count": policy_pack.get("subject_policy_count"),
            "peers_with_policy": peers_with_policy,
            "peer_count": policy_peer_count,
            "peer_policy_coverage_pct": policy_coverage_pct,
            "status": (policy_pack.get("data_quality") or {}).get(
                "status",
                "limited" if policy_coverage_pct < 50.0 else "usable",
            ),
            "coverage_note": policy_pack.get("coverage_note"),
        },
        "audit_report_events": {
            "total_events": kam_events,
            "status": "limited" if kam_events == 0 else "usable",
        },
        "kam_body": {
            "kam_body_count": kam_body_count,
            "peer_companies_with_sections": kam_section_quality.get("peer_companies_with_sections"),
            "subject_section_count": kam_section_quality.get("subject_section_count"),
            "kam_reason_coverage": kam_reason_coverage,
            "kam_procedure_coverage": kam_procedure_coverage,
            "source": kam_section_quality.get("source"),
            "available_subject_kam_years": (kam_pack.get("data_quality") or {}).get("available_subject_kam_years"),
            "status": (
                "not_persisted"
                if not kam_body_count
                else "subject_missing"
                if not (kam_section_quality.get("subject_section_count") or 0)
                else "subject_only"
                if not (kam_section_quality.get("peer_companies_with_sections") or 0)
                else "usable"
            ),
        },
        "audit_report_matters": {
            "status": (matter_pack.get("data_quality") or {}).get("status"),
            "subject_section_count": (matter_pack.get("data_quality") or {}).get("subject_section_count"),
            "peer_companies_with_sections": (matter_pack.get("data_quality") or {}).get("peer_companies_with_sections"),
            "matter_counts": matter_counts,
        },
    }

    if data_quality["policy_cache"]["status"] == "limited":
        acceptance_signals.append({
            "area": "data_coverage",
            "severity": "info",
            "signal": "low_peer_accounting_policy_cache_coverage",
        })
    if data_quality["kam_body"]["status"] == "not_persisted":
        acceptance_signals.append({
            "area": "data_coverage",
            "severity": "info",
            "signal": "kam_body_not_persisted",
        })
    elif data_quality["kam_body"]["status"] == "subject_only":
        acceptance_signals.append({
            "area": "data_coverage",
            "severity": "info",
            "signal": "peer_kam_body_coverage_missing",
        })
    elif data_quality["kam_body"]["status"] == "subject_missing":
        acceptance_signals.append({
            "area": "data_coverage",
            "severity": "info",
            "signal": "subject_kam_body_missing_for_requested_year",
        })

    recommended_review_areas = sorted({
        item["area"] for item in acceptance_signals
    } | {
        "peer_group_basis",
        "audit_fee_and_hours",
        "accounting_policy_coverage",
        "audit_report_events",
        "audit_report_matters",
    })

    return _clean_dict({
        "subject": peer_group["subject"],
        "year": year,
        "scope": "external_dart_evidence_pack",
        "acceptance_signals": acceptance_signals,
        "data_quality": data_quality,
        "recommended_review_areas": recommended_review_areas,
        "peer_group": {
            "peer_count": peer_group.get("peer_count"),
            "confidence": peer_group.get("confidence"),
            "selection_policy": peer_group.get("selection_policy"),
            "sample_peers": peer_group.get("peers", [])[:10],
        },
        "audit_fee_summary": {
            "subject_metrics": subject_fee,
            "benchmarks": fee_pack.get("benchmarks"),
        },
        "risk_summary": {
            "subject_metrics": risk_subject,
            "benchmarks": risk_pack.get("benchmarks"),
            "disclosure_event_counts": risk_pack.get("disclosure_event_counts"),
        },
        "audit_hours_proxy": {
            "complexity_score": hours_pack.get("complexity_score"),
            "complexity_band": hours_pack.get("complexity_band"),
            "drivers": hours_pack.get("drivers"),
        },
        "policy_summary": {
            "subject_policy_count": policy_pack.get("subject_policy_count"),
            "peers_with_policy": policy_pack.get("peers_with_policy"),
            "coverage_note": policy_pack.get("coverage_note"),
        },
        "kam_summary": {
            "audit_report_events": kam_pack.get("audit_report_events"),
            "kam_topics": kam_pack.get("kam_topics"),
            "audit_report_sections": kam_pack.get("audit_report_sections"),
            "subject_sections": (kam_pack.get("subject_sections") or [])[:3],
            "subject_business_report_kam_summary": (
                kam_pack.get("subject_business_report_kam_summary") or []
            )[:3],
            "limitations": kam_pack.get("limitations"),
        },
        "audit_report_matter_summary": {
            "matter_counts": matter_counts,
            "subject_matters": (matter_pack.get("subject_matters") or [])[:5],
            "data_quality": matter_pack.get("data_quality"),
            "limitations": matter_pack.get("limitations"),
        },
        "limitations": [
            "This pack supports acceptance/continuance screening only.",
            "It does not replace firm methodology, independence checks, client inquiry, or workpaper judgment.",
        ],
    })


# ---------------------------------------------------------------------------
# 업종 감사 시장 분석 (get_industry_audit_landscape)
# ---------------------------------------------------------------------------

_BIG4_KEYWORDS = ("삼일", "삼정", "한영", "안진", "PwC", "KPMG", "EY", "Deloitte")


def _is_big4(name: Optional[str]) -> bool:
    """auditor_nm 내 Big4 키워드 포함 여부."""
    if not name:
        return False
    return any(k in name for k in _BIG4_KEYWORDS)


def _empty_audit_landscape(
    subject_meta: dict,
    pr: PeerResolution,
    fs_div: str,
    note: str,
) -> dict:
    """auditors 데이터 부족 시 graceful degradation shape."""
    return {
        "subject": subject_meta,
        "sector_group": pr.sector_group.value,
        "matched_prefix_len": pr.matched_prefix_len,
        "n_peers": pr.n_peers,
        "confidence": pr.confidence,
        "excluded_categories": pr.excluded_categories,
        "fs_div": fs_div,
        "latest_year": None,
        "years_window": None,
        "auditor_market_share": [],
        "big4_share_pct": None,
        "non_qualified_opinion_rate_pct": None,
        "avg_tenure_years": None,
        "subject_auditor": None,
        "note": note,
    }


def get_industry_audit_landscape(
    company: Optional[str] = None,
    induty_code: Optional[str] = None,
    years_back: int = 5,
    fs_div: str = "CFS",
    prefix_len_start: int = 3,
    top_n: int = 10,
    exclude_other_sectors: bool = True,
) -> dict:
    """업종 내 감사 시장 분석.

    Returns: subject 정보 + 감사인 시장점유율(회사수·자산가중) + Big4 share +
    비적정 의견 발생율(5년 누적) + 평균 tenure + subject 본인 감사인.

    Auditors 테이블 데이터가 부족하면 latest_year=None과 함께 자료 부족 note.
    """
    # 1. Subject corp_code 해석
    if company:
        corp_code = resolve_corp_code(company)
        if corp_code is None:
            return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}
    elif induty_code:
        prefix = str(induty_code).strip()
        if not prefix:
            return {"error": "induty_code가 비어 있습니다."}
        with _engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT corp_code FROM companies "
                    "WHERE substr(induty_code, 1, :plen) = :prefix "
                    "  AND stock_code IS NOT NULL "
                    "ORDER BY corp_code LIMIT 1"
                ),
                {"plen": len(prefix), "prefix": prefix},
            ).first()
        if row is None:
            return {"error": f"induty_code prefix '{prefix}'에 해당하는 기업이 없습니다."}
        corp_code = row[0]
    else:
        return {"error": "company 또는 induty_code 중 하나 필요"}

    # 2. Subject 메타
    with _engine.connect() as conn:
        subj_row = conn.execute(
            text("SELECT corp_name, induty_code FROM companies WHERE corp_code = :cc"),
            {"cc": corp_code},
        ).first()
    if subj_row is None:
        return {"error": f"corp_code '{corp_code}' 미등록"}
    subject_meta = {
        "corp_code": corp_code,
        "corp_name": subj_row[0],
        "induty_code": subj_row[1],
    }

    # 3. Peer 풀
    pr = resolve_peers(
        corp_code=corp_code,
        prefix_len_start=prefix_len_start,
        min_n=5,
        exclude_other_sectors=exclude_other_sectors,
        size_bucket_decade=None,
        fs_div=fs_div,
    )

    # 4. peer_set = peer + subject
    peer_set = list(pr.peer_corp_codes) + [corp_code]

    # 5. latest_year (auditors)
    with _engine.connect() as conn:
        stmt = text(
            "SELECT MAX(bsns_year) FROM auditors "
            "WHERE corp_code IN :ccs AND fs_div = :fs"
        ).bindparams(bindparam("ccs", expanding=True))
        latest = conn.execute(stmt, {"ccs": peer_set, "fs": fs_div}).scalar()

    if latest is None:
        return _empty_audit_landscape(
            subject_meta,
            pr,
            fs_div,
            note=(
                (pr.note + " · " if pr.note else "")
                + "auditors 데이터 부족 (collect-auditors 미실행 또는 데이터 없음)"
            ),
        )
    latest_year = int(latest)

    # 6. years window
    y1 = latest_year
    y0 = latest_year - years_back + 1

    # 7. Auditor market share (latest year)
    with _engine.connect() as conn:
        # 7a. 회사수 기반 share + asset_weighted share (한 쿼리)
        share_stmt = text(
            """
            SELECT a.auditor_nm,
                   COUNT(DISTINCT a.corp_code) AS company_count,
                   COALESCE(SUM(f.total_assets), 0) AS asset_sum
            FROM auditors a
            LEFT JOIN financials f
              ON f.corp_code = a.corp_code
             AND f.year = a.bsns_year
             AND f.fs_div = a.fs_div
             AND f.quarter = 4
            WHERE a.corp_code IN :ccs
              AND a.bsns_year = :y
              AND a.fs_div = :fs
            GROUP BY a.auditor_nm
            ORDER BY company_count DESC
            LIMIT :topn
            """
        ).bindparams(bindparam("ccs", expanding=True))
        share_rows = conn.execute(
            share_stmt,
            {"ccs": peer_set, "y": latest_year, "fs": fs_div, "topn": top_n},
        ).all()

        # 7b. 전체 합산 (share_pct 계산 분모)
        total_stmt = text(
            """
            SELECT COUNT(DISTINCT a.corp_code) AS company_total,
                   COALESCE(SUM(f.total_assets), 0) AS asset_total
            FROM auditors a
            LEFT JOIN financials f
              ON f.corp_code = a.corp_code
             AND f.year = a.bsns_year
             AND f.fs_div = a.fs_div
             AND f.quarter = 4
            WHERE a.corp_code IN :ccs
              AND a.bsns_year = :y
              AND a.fs_div = :fs
            """
        ).bindparams(bindparam("ccs", expanding=True))
        total_row = conn.execute(
            total_stmt,
            {"ccs": peer_set, "y": latest_year, "fs": fs_div},
        ).first()
    company_total = int(total_row[0]) if total_row and total_row[0] else 0
    asset_total = float(total_row[1]) if total_row and total_row[1] else 0.0

    auditor_market_share = []
    for nm, cnt, asum in share_rows:
        cnt_i = int(cnt or 0)
        asum_f = float(asum or 0)
        comp_share = (
            round(100.0 * cnt_i / company_total, 2) if company_total > 0 else None
        )
        asset_share = (
            round(100.0 * asum_f / asset_total, 2) if asset_total > 0 else None
        )
        auditor_market_share.append(
            {
                "auditor_nm": nm,
                "company_count": cnt_i,
                "company_share_pct": comp_share,
                "asset_weighted_share_pct": asset_share,
                "is_big4": _is_big4(nm),
            }
        )

    # 8. Big4 share (latest year)
    with _engine.connect() as conn:
        big4_stmt = text(
            "SELECT auditor_nm, COUNT(DISTINCT corp_code) FROM auditors "
            "WHERE corp_code IN :ccs AND bsns_year = :y AND fs_div = :fs "
            "GROUP BY auditor_nm"
        ).bindparams(bindparam("ccs", expanding=True))
        all_rows = conn.execute(
            big4_stmt, {"ccs": peer_set, "y": latest_year, "fs": fs_div}
        ).all()
    big4_corps = sum(int(c or 0) for nm, c in all_rows if _is_big4(nm))
    any_corps = sum(int(c or 0) for _, c in all_rows)
    big4_share_pct = (
        round(100.0 * big4_corps / any_corps, 2) if any_corps > 0 else None
    )

    # 9. Non-qualified opinion rate (years window)
    with _engine.connect() as conn:
        op_stmt = text(
            "SELECT audit_opinion, COUNT(*) FROM auditors "
            "WHERE corp_code IN :ccs "
            "  AND bsns_year BETWEEN :y0 AND :y1 "
            "  AND fs_div = :fs "
            "GROUP BY audit_opinion"
        ).bindparams(bindparam("ccs", expanding=True))
        op_rows = conn.execute(
            op_stmt,
            {"ccs": peer_set, "y0": y0, "y1": y1, "fs": fs_div},
        ).all()
    total_op = sum(int(c or 0) for _, c in op_rows)
    non_qual = sum(
        int(c or 0)
        for op, c in op_rows
        if op is not None and str(op).strip() != "적정"
    )
    non_qualified_opinion_rate_pct = (
        round(100.0 * non_qual / total_op, 2) if total_op > 0 else None
    )

    # 10. Average tenure (latest year)
    with _engine.connect() as conn:
        ten_stmt = text(
            "SELECT AVG(consecutive_years) FROM auditors "
            "WHERE corp_code IN :ccs "
            "  AND bsns_year = :y AND fs_div = :fs "
            "  AND consecutive_years IS NOT NULL"
        ).bindparams(bindparam("ccs", expanding=True))
        avg_ten = conn.execute(
            ten_stmt, {"ccs": peer_set, "y": latest_year, "fs": fs_div}
        ).scalar()
    avg_tenure_years = round(float(avg_ten), 2) if avg_ten is not None else None

    # 11. Subject auditor (latest year)
    with _engine.connect() as conn:
        subj_aud = conn.execute(
            text(
                "SELECT auditor_nm, audit_opinion, consecutive_years "
                "FROM auditors "
                "WHERE corp_code = :cc AND bsns_year = :y AND fs_div = :fs"
            ),
            {"cc": corp_code, "y": latest_year, "fs": fs_div},
        ).first()
    if subj_aud is not None:
        subject_auditor = {
            "auditor_nm": subj_aud[0],
            "audit_opinion": subj_aud[1],
            "consecutive_years": (
                int(subj_aud[2]) if subj_aud[2] is not None else None
            ),
            "is_big4": _is_big4(subj_aud[0]),
        }
    else:
        subject_auditor = None

    return {
        "subject": subject_meta,
        "sector_group": pr.sector_group.value,
        "matched_prefix_len": pr.matched_prefix_len,
        "n_peers": pr.n_peers,
        "confidence": pr.confidence,
        "excluded_categories": pr.excluded_categories,
        "fs_div": fs_div,
        "latest_year": latest_year,
        "years_window": [y0, y1],
        "auditor_market_share": auditor_market_share,
        "big4_share_pct": big4_share_pct,
        "non_qualified_opinion_rate_pct": non_qualified_opinion_rate_pct,
        "avg_tenure_years": avg_tenure_years,
        "subject_auditor": subject_auditor,
        "note": pr.note,
    }


# ---------------------------------------------------------------------------
# 사업개요 (MCP용 — Claude가 업종 맥락 분석에 활용)
# ---------------------------------------------------------------------------

def get_business_overview(
    company: str,
    bsns_year: Optional[int] = None,
) -> dict:
    """
    사업보고서 핵심 섹션 텍스트 + 업종 분류 + rule-based 인사이트 반환.
    Claude가 MCP로 호출하여 업종별 심층 분석에 활용.

    Args:
        company: corp_code / stock_code / 회사명
        bsns_year: 사업연도 (None이면 최신)

    Returns:
        {
          "corp_code", "corp_name", "induty_code", "industry_name",
          "bsns_year",
          "sections": {section_key: {"title", "body_text", "length"}},
          "insights": [{"title", "value", "detail", "audience", "risk_level"}],
          "total_chars": int,
        }
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

    # 기업 정보
    with get_session() as session:
        row = session.query(Company).filter_by(corp_code=corp_code).first()
        if row is None:
            return {"error": f"corp_code '{corp_code}'를 찾을 수 없습니다."}
        corp_name = row.corp_name
        stock_code = row.stock_code
        induty_code = row.induty_code or ""

    # 업종명
    industry_name = _get_industry_name(induty_code[:2]) if induty_code else ""

    # 사업보고서 연도 결정
    if bsns_year is None:
        years = _queries.get_years_with_business_report(corp_code)
        if not years:
            return {
                "corp_code": corp_code, "corp_name": corp_name,
                "induty_code": induty_code, "industry_name": industry_name,
                "bsns_year": None,
                "sections": {},
                "insights": [],
                "total_chars": 0,
                "note": "수집된 사업보고서가 없습니다.",
            }
        bsns_year = years[0]  # 가장 최근

    section_keys = {
        "business_overview",
        "business_description",
        "risk_management",
        "management_plan",
        "rd_activities",
        "key_contracts",
    }
    with _engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT section_key, section_title, body_text, body_length
                FROM report_sections
                WHERE corp_code=:corp_code
                  AND bsns_year=:bsns_year
                  AND source_type='business_report'
                  AND section_key IN :section_keys
                ORDER BY ordinal
                """
            ).bindparams(bindparam("section_keys", expanding=True)),
            {
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "section_keys": list(section_keys),
            },
        ).mappings().all()

    raw = {
        row["section_key"]: {
            "title": row["section_title"],
            "body_text": _display_text(row["body_text"]),
            "length": row["body_length"],
        }
        for row in rows
    }

    if not raw or not isinstance(raw, dict):
        return {
            "corp_code": corp_code, "corp_name": corp_name,
            "induty_code": induty_code, "industry_name": industry_name,
            "bsns_year": bsns_year,
            "sections": {},
            "insights": [],
            "total_chars": 0,
            "section_count": 0,
            "data_quality": {
                "status": "cache_missing",
                "source": "local_report_sections",
                "requested_year": bsns_year,
                "available_business_report_years": _cached_years_for_sections(corp_code, "business_report"),
                "missing_reason": "business_overview/business_description 등 경영정보 섹션이 아직 로컬 DB에 영속화되지 않았습니다.",
                "interpretation": (
                    "No rows means the local business-report section cache lacks the requested year. "
                    "It does not prove the filing lacks management discussion sections."
                ),
            },
            "note": (
                f"{bsns_year}년 사업보고서 경영정보 본문 캐시가 없습니다. "
                "외부 MCP 런타임에서는 DART API를 호출하지 않습니다."
            ),
        }

    # 텍스트만 반환 (HTML 제거 — MCP 응답 크기 절약)
    sections_clean = {}
    total_chars = 0
    for key, sec in raw.items():
        if not isinstance(sec, dict):
            continue
        body = sec.get("body_text", "")
        # MCP 응답 크기 제한: 각 섹션 최대 3000자
        if len(body) > 3000:
            body = body[:3000] + "\n... (이하 생략)"
        sections_clean[key] = {
            "title": sec.get("title", key),
            "body_text": body,
            "length": sec.get("length", len(body)),
        }
        total_chars += sec.get("length", 0)

    # 인사이트
    from kreports.analysis.business_insights import generate_business_insights
    insights = generate_business_insights(raw, induty_code=induty_code)

    return _clean_dict({
        "corp_code": corp_code,
        "corp_name": corp_name,
        "induty_code": induty_code,
        "industry_name": industry_name,
        "bsns_year": bsns_year,
        "report_meta": {},
        "sections": sections_clean,
        "insights": insights,
        "audit_focus": [],
        "investment_focus": [],
        "risk_distribution": None,
        "total_chars": total_chars,
        "section_count": len(sections_clean),
        "data_quality": {
            "status": "usable",
            "source": "local_report_sections",
            "requested_year": bsns_year,
            "available_business_report_years": _cached_years_for_sections(corp_code, "business_report"),
        },
    })


_SUBSIDIARY_SLIM_FIELDS = (
    "name", "relation", "ownership_pct", "listed_yn",
    "corp_code", "stock_code", "market", "auditor",
)


def get_subsidiary_auditors(
    company: str,
    limit: Optional[int] = 100,
    only_with_auditor: bool = False,
    slim: bool = True,
) -> dict:
    """
    최근 사업보고서 기준 종속/관계회사별 감사인 정보.

    대형 그룹(삼성전자 등)은 종속회사가 400개 이상이라 MCP 응답이 수십KB로 커질 수 있다.
    기본값은 감사인 있는 항목 우선 + 상위 100개 + 핵심 필드만 (slim 모드).

    Args:
        company: corp_code / stock_code / 회사명
        limit: 반환 최대 종속회사 수. None이면 전체.
        only_with_auditor: True면 감사인 있는 항목만.
        slim: True면 핵심 8개 필드만 반환 (name, relation, ownership_pct, listed_yn,
              corp_code, stock_code, market, auditor). False면 전체 필드.

    Returns:
        {
          "corp_code", "parent_rcept_no", "bsns_year",
          "subsidiaries": [...],
          "count": int,             # 반환된 개수
          "total": int,              # DB에 있는 전체 개수
          "truncated": bool,
        }
    """
    corp_code = _resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "parent_rcept_no": None,
            "bsns_year": None,
            "subsidiaries": [],
            "count": 0,
            "total": 0,
            "truncated": False,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    cached_rows = []
    latest_year = None
    if _has_db_column("subsidiary_auditor_matrix", "parent_corp_code"):
        with get_session() as session:
            cached_orm_rows = (
                session.query(BusinessAffiliateAuditor)
                .filter_by(parent_corp_code=corp_code)
                .order_by(BusinessAffiliateAuditor.bsns_year.desc(), BusinessAffiliateAuditor.ordinal.asc())
                .all()
            )
            latest_year = cached_orm_rows[0].bsns_year if cached_orm_rows else None
            if latest_year is not None:
                cached_orm_rows = [row for row in cached_orm_rows if row.bsns_year == latest_year]
            cached_rows = [
                {
                    "parent_rcept_no": row.parent_rcept_no,
                    "bsns_year": row.bsns_year,
                    "name": row.name,
                    "relation": row.relation,
                    "ownership_pct": row.ownership_pct,
                    "listed_yn": row.listed_yn,
                    "business": row.business,
                    "assets": row.assets,
                    "source": row.source,
                    "corp_code": row.corp_code,
                    "stock_code": row.stock_code,
                    "market": row.market,
                    "auditor_nm": row.auditor_nm,
                    "audit_opinion": row.audit_opinion,
                    "auditor_fs_div": row.auditor_fs_div,
                    "auditor_year": row.auditor_year,
                }
                for row in cached_orm_rows
            ]
    with get_session() as session:
        row = (
            session.query(Disclosure.rcept_no, Disclosure.disc_date, Disclosure.report_nm)
            .filter_by(corp_code=corp_code)
            .filter(Disclosure.report_nm.like("%사업보고서%"))
            .order_by(Disclosure.disc_date.desc())
            .first()
        )
    if cached_rows:
        items = []
        for cached in cached_rows:
            auditor = None
            if cached["auditor_nm"]:
                auditor = {
                    "auditor_nm": cached["auditor_nm"],
                    "bsns_year": cached["auditor_year"],
                    "audit_opinion": cached["audit_opinion"],
                }
            items.append({
                "name": cached["name"],
                "relation": cached["relation"],
                "ownership_pct": cached["ownership_pct"],
                "listed_yn": cached["listed_yn"],
                "business": cached["business"],
                "assets": cached["assets"],
                "source": cached["source"],
                "corp_code": cached["corp_code"],
                "stock_code": cached["stock_code"],
                "market": cached["market"],
                "auditor": auditor,
            })

        total = len(items)
        items_sorted = sorted(items, key=lambda x: 0 if x.get("auditor") else 1)
        if only_with_auditor:
            items_sorted = [x for x in items_sorted if x.get("auditor")]
        truncated = False
        if limit is not None and len(items_sorted) > limit:
            items_sorted = items_sorted[:limit]
            truncated = True
        if slim:
            items_sorted = [
                {k: x.get(k) for k in _SUBSIDIARY_SLIM_FIELDS}
                for x in items_sorted
            ]

        return _clean_dict({
            "corp_code": corp_code,
            "parent_rcept_no": cached_rows[0]["parent_rcept_no"],
            "bsns_year": latest_year,
            "subsidiaries": items_sorted,
            "count": len(items_sorted),
            "total": total,
            "truncated": truncated,
            "data_quality": {
                "status": "usable",
                "source": "local_subsidiary_auditor_matrix",
            },
        })
    if row is None:
        return {
            "corp_code": corp_code,
            "parent_rcept_no": None,
            "bsns_year": None,
            "subsidiaries": [],
            "count": 0,
            "total": 0,
            "truncated": False,
            "data_quality": {
                "status": "cache_missing",
                "source": "local_subsidiary_auditor_matrix",
            },
            "note": "DB에 사업보고서 공시가 없습니다.",
        }
    disc_date_str = str(row.disc_date)
    try:
        bsns_year = int(disc_date_str[:4]) - 1
    except Exception:
        bsns_year = None

    return {
        "corp_code": corp_code,
        "parent_rcept_no": row.rcept_no,
        "bsns_year": bsns_year,
        "subsidiaries": [],
        "count": 0,
        "total": 0,
        "truncated": False,
        "parse_errors": [],
            "data_quality": {
                "status": "cache_missing",
                "source": "local_subsidiary_auditor_matrix",
                "missing_reason": "종속회사/관계회사 감사인 매트릭스는 아직 별도 캐시 테이블로 영속화되지 않았습니다.",
            },
        "note": "외부 MCP 런타임에서는 DART API를 호출하지 않습니다. 이 기능은 캐시 테이블 추가 전까지 데이터 없음으로 반환합니다.",
    }
