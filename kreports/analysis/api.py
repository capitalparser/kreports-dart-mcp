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
from kreports.db.models import Company, Disclosure

# dashboard.db는 streamlit optional import를 지원하므로 headless에서도 사용 가능
from kreports.analysis import queries as _queries
from kreports.analysis.peer import (
    PeerResolution,
    SectorGroup,
    classify_sector,
    confidence_band,
    resolve_peers,
)


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

    data = _queries.get_accounting_policy(corp_code, bsns_year, fs_div=fs_div)
    if data is None:
        return None
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
        # subject의 metric 값/induty_code도 필요하면 별도 fetch)
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
                cc, cn, ic, v = subj_rows[0]
                subject_value: Optional[float] = (
                    round(float(v), 2) if v is not None else None
                )
                subject_induty: Optional[str] = ic
            else:
                subject_value = None
                subject_induty = None
        else:
            subject_value = None
            subject_induty = None
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
        subject_induty = None

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
            "found_in_peers": subject_value is not None,
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

    # 섹션 추출 — dashboard.db에만 있는 함수 (queries.py에 미포함)
    import os as _os
    _os.environ.setdefault("DART_HEADLESS", "1")
    from dashboard.db import get_business_report_package as _get_brp
    package = _get_brp(corp_code, bsns_year)
    raw = (package or {}).get("sections")

    if not raw or not isinstance(raw, dict):
        return {
            "corp_code": corp_code, "corp_name": corp_name,
            "induty_code": induty_code, "industry_name": industry_name,
            "bsns_year": bsns_year,
            "sections": {},
            "insights": [],
            "total_chars": 0,
            "note": f"{bsns_year}년 사업보고서 본문을 추출할 수 없습니다.",
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
        "report_meta": (package or {}).get("meta", {}),
        "sections": sections_clean,
        "insights": insights,
        "audit_focus": (package or {}).get("audit_focus", []),
        "investment_focus": (package or {}).get("investment_focus", []),
        "risk_distribution": (package or {}).get("risk_distribution"),
        "total_chars": total_chars,
        "section_count": len(sections_clean),
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

    data = _queries.get_subsidiaries_with_auditors(corp_code)
    if not data:
        return {
            "corp_code": corp_code,
            "parent_rcept_no": None,
            "bsns_year": None,
            "subsidiaries": [],
            "count": 0,
            "total": 0,
            "truncated": False,
        }

    # dashboard.db 반환 구조: {rcept_no, bsns_year, items: [...], parse_errors: [...]}
    items = data.get("items", []) if isinstance(data, dict) else list(data)
    total = len(items)

    # 필터: 감사인 있는 항목 우선 정렬
    def _has_auditor(x: dict) -> int:
        a = x.get("auditor") if isinstance(x, dict) else None
        return 0 if a else 1  # auditor 있는 것이 먼저

    items_sorted = sorted(items, key=_has_auditor)

    if only_with_auditor:
        items_sorted = [x for x in items_sorted if x.get("auditor")]

    # Limit 적용
    truncated = False
    if limit is not None and len(items_sorted) > limit:
        items_sorted = items_sorted[:limit]
        truncated = True

    # Slim: 핵심 필드만
    if slim:
        items_sorted = [
            {k: x.get(k) for k in _SUBSIDIARY_SLIM_FIELDS}
            for x in items_sorted
        ]

    result = {
        "corp_code": corp_code,
        "parent_rcept_no": data.get("rcept_no") if isinstance(data, dict) else None,
        "bsns_year": data.get("bsns_year") if isinstance(data, dict) else None,
        "subsidiaries": items_sorted,
        "count": len(items_sorted),
        "total": total,
        "truncated": truncated,
    }
    return _clean_dict(result)
