"""
kreports.analysis.queries — DART 공시 데이터 쿼리 함수.

Streamlit 의존성 없는 순수 쿼리 레이어.
MCP 서버, CLI, 배치 작업에서 직접 사용 가능.
dashboard/db.py는 이 모듈을 import하여 @st.cache_data 래핑만 추가한다.
"""
import pandas as pd
from kreports.db.engine import get_session
from kreports.db.models import (
    AccountingPolicyItem,
    Company,
    Financial,
    FinancialFact,
    Disclosure,
    Auditor,
    AuditFee,
)

_UNIT = 1e8  # 억원


def search_companies(query: str, limit: int = 30) -> list[dict]:
    with get_session() as session:
        q = session.query(Company).filter(Company.stock_code.isnot(None))
        if query:
            q = q.filter(
                (Company.corp_name.ilike(f"%{query}%")) |
                (Company.stock_code == query.strip())
            )
        rows = q.order_by(Company.market, Company.corp_name).limit(limit).all()
        return [
            {"corp_code": r.corp_code, "corp_name": r.corp_name,
             "stock_code": r.stock_code, "market": r.market or "-"}
            for r in rows
        ]


def get_company(stock_code: str) -> dict | None:
    with get_session() as session:
        r = session.query(Company).filter_by(stock_code=stock_code).first()
        if r is None:
            return None
        # induty_code: 신 컬럼 우선, 없으면 legacy sector 값 fallback (마이그레이션 전 레코드 대응)
        induty_code = r.induty_code or r.sector
        return {"corp_code": r.corp_code, "corp_name": r.corp_name,
                "stock_code": r.stock_code, "market": r.market or "-",
                "induty_code": induty_code,
                "sector": r.sector}  # deprecated: 하위 호환용


def _query_financials_raw(corp_code: str, fs_div: str) -> list:
    with get_session() as session:
        return [
            {
                "연도": r.year, "분기": r.quarter, "구분": r.fs_div,
                "매출액": r.revenue, "영업이익": r.operating_profit,
                "순이익": r.net_income, "자산총계": r.total_assets,
                "부채총계": r.total_debt, "자본총계": r.total_equity,
                "매핑신뢰도": r.account_map_confidence,
                "CFS_OFS괴리": r.cfs_ofs_gap_flag,
                "발생액비율": r.accrual_ratio,
                "영업CF": r.operating_cf,
                "영업순익괴리": r.op_net_divergence_flag,
                "자본잠식": r.equity_negative_flag,
                "계속기업우려": r.going_concern_flag,
                "매출성장률YoY": r.revenue_yoy,
                "매출급변": r.revenue_vol_flag,
                "정정공시수": r.amendment_count_annual,
                "영업CF괴리": r.op_cf_divergence_flag,
                "Beneish_M": r.beneish_m_score,
                "Beneish_flag": r.beneish_flag,
            }
            for r in session.query(Financial)
            .filter_by(corp_code=corp_code, fs_div=fs_div)
            .order_by(Financial.year, Financial.quarter)
            .all()
        ]


def get_financials(corp_code: str, fs_div: str = "CFS") -> pd.DataFrame:
    """CFS 우선, 없으면 OFS 폴백."""
    data = _query_financials_raw(corp_code, fs_div)
    if not data and fs_div == "CFS":
        data = _query_financials_raw(corp_code, "OFS")

    df = pd.DataFrame(data)
    if df.empty:
        return df

    # 억원 단위 변환
    for col in ["매출액", "영업이익", "순이익", "자산총계", "부채총계", "자본총계", "영업CF"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce") / _UNIT

    # 파생 지표
    df["영업이익률"] = (df["영업이익"] / df["매출액"] * 100).round(1)
    df["순이익률"] = (df["순이익"] / df["매출액"] * 100).round(1)
    df["부채비율"] = (df["부채총계"] / df["자본총계"] * 100).round(1)
    df["ROE"] = (df["순이익"] / df["자본총계"] * 100).round(1)
    df["ROA"] = (df["순이익"] / df["자산총계"] * 100).round(1)
    df["기간"] = df.apply(lambda r: f"{int(r['연도'])}Q{int(r['분기'])}", axis=1)

    # YoY 매출 성장률 (Q4 기준)
    annual = df[df["분기"] == 4][["연도", "매출액"]].copy()
    annual["매출성장률"] = annual["매출액"].pct_change() * 100
    df = df.merge(annual[["연도", "매출성장률"]], on="연도", how="left")

    return df


def get_financials_both(corp_code: str) -> pd.DataFrame:
    rows = []
    for div in ["CFS", "OFS"]:
        rows.extend(_query_financials_raw(corp_code, div))
    df = pd.DataFrame(rows)
    if not df.empty:
        for col in ["매출액", "영업이익", "순이익", "자산총계", "부채총계", "자본총계"]:
            df[col] = pd.to_numeric(df[col], errors="coerce") / _UNIT
    return df


# ---------------------------------------------------------------------------
# 확장 재무 (자본배분 · 현금흐름 품질 · 운전자본 사이클)
# ---------------------------------------------------------------------------

# 계정명 키워드 — FinancialFact.account_nm LIKE 매칭용.
# XBRL element ID는 기업별로 표기가 달라 한글 계정명을 기준으로 탐색한다.
# 각 항목은 (포함되어야 할 토큰, 제외할 토큰) 튜플 리스트: 위에서부터 우선순위.
_FACT_PATTERNS: dict[str, list[tuple[list[str], list[str]]]] = {
    # 유형자산 취득 (CapEx) — CF에서 투자활동 유출
    "capex": [
        (["유형자산의 취득"], []),
        (["유형자산 취득"], []),
        (["유형자산의 증가"], []),
    ],
    # 재고자산 (BS)
    "inventory": [
        (["재고자산"], ["평가", "변동", "손실", "환입"]),
    ],
    # 매출채권 (BS)
    "receivables": [
        (["매출채권"], ["손실", "대손", "변동", "평가"]),
    ],
    # 매입채무 (BS)
    "payables": [
        (["매입채무"], ["변동"]),
    ],
    # 매출원가 (IS)
    "cogs": [
        (["매출원가"], []),
    ],
    # 이자비용 (IS) — 이자보상배율 계산용
    "interest_expense": [
        (["이자비용"], []),
        (["금융비용"], []),
    ],
    # 현금및현금성자산 (BS) — ROIC 투하자본 계산용
    "cash": [
        (["현금및현금성자산"], []),
        (["현금 및 현금성자산"], []),
    ],
}


def _extract_fact_value(facts_df: pd.DataFrame, pattern_key: str) -> float | None:
    """FinancialFact DataFrame에서 pattern_key에 해당하는 계정의 당기 금액을 반환.

    pattern 리스트를 순서대로 시도한다. 동일 연도 · 동일 sj_div 내에서 가장 먼저
    매칭되는 값을 사용하며, 매칭 실패 시 None.
    """
    if facts_df.empty:
        return None
    patterns = _FACT_PATTERNS.get(pattern_key)
    if not patterns:
        return None
    for include_tokens, exclude_tokens in patterns:
        mask = pd.Series(True, index=facts_df.index)
        for tok in include_tokens:
            mask &= facts_df["계정명"].str.contains(tok, na=False, regex=False)
        for tok in exclude_tokens:
            mask &= ~facts_df["계정명"].str.contains(tok, na=False, regex=False)
        hits = facts_df[mask]
        if not hits.empty:
            val = pd.to_numeric(hits.iloc[0]["당기"], errors="coerce")
            if pd.notna(val):
                return float(val)
    return None


def get_financials_extended(corp_code: str, fs_div: str = "CFS") -> pd.DataFrame:
    """
    기본 재무 요약(get_financials)에 FinancialFact 기반 확장 컬럼을 덧붙인다.

    추가 컬럼 (연간 Q4 기준, 억원 단위):
      CapEx, 재고자산, 매출채권, 매입채무, 매출원가, 이자비용, 현금성자산,
      FCF(=영업CF-CapEx), FCF마진(=FCF/매출), CapEx/OCF, CFO/NI,
      투하자본(=자산총계-현금성자산), ROIC(=영업이익*(1-0.22)/투하자본),
      이자보상배율(=영업이익/이자비용),
      DIO(=365*재고/매출원가), DSO(=365*매출채권/매출), DPO(=365*매입채무/매출원가),
      CCC(=DIO+DSO-DPO).

    XBRL 계정 매핑 실패 시 해당 컬럼은 NaN. 호출자 측에서 isna() 체크 필요.
    """
    df = get_financials(corp_code, fs_div)
    if df.empty:
        return df

    # Q4(연간)만 확장 — 분기 보고서는 FinancialFact의 thstrm_amount가 누적이 아닐 수 있어 생략
    ext_cols = [
        "CapEx", "재고자산", "매출채권", "매입채무", "매출원가",
        "이자비용", "현금성자산",
        "FCF", "FCF마진", "CapEx_OCF", "CFO_NI",
        "투하자본", "ROIC", "이자보상배율",
        "DIO", "DSO", "DPO", "CCC",
    ]
    for c in ext_cols:
        df[c] = pd.NA

    # 연간 Q4 행의 (연도) 목록
    annual_years = sorted(df[df["분기"] == 4]["연도"].dropna().astype(int).unique().tolist())

    # FinancialFact 전체 조회 (연간 reprt_code=11011) — 한 번만 쿼리
    with get_session() as session:
        q = (
            session.query(FinancialFact)
            .filter_by(corp_code=corp_code, fs_div=fs_div, reprt_code="11011")
            .filter(FinancialFact.bsns_year.in_(annual_years))
        )
        rows = q.all()
        fact_data = [
            {
                "연도": r.bsns_year,
                "재무표": r.sj_div,
                "계정명": r.account_nm,
                "당기": r.thstrm_amount,
            }
            for r in rows
        ]
    if not fact_data and fs_div == "CFS":
        # CFS 없으면 OFS 재시도
        with get_session() as session:
            q = (
                session.query(FinancialFact)
                .filter_by(corp_code=corp_code, fs_div="OFS", reprt_code="11011")
                .filter(FinancialFact.bsns_year.in_(annual_years))
            )
            rows = q.all()
            fact_data = [
                {
                    "연도": r.bsns_year,
                    "재무표": r.sj_div,
                    "계정명": r.account_nm,
                    "당기": r.thstrm_amount,
                }
                for r in rows
            ]

    facts_all = pd.DataFrame(fact_data)

    # 연도별로 파생 지표 계산
    for year in annual_years:
        row_mask = (df["분기"] == 4) & (df["연도"] == year)
        if not row_mask.any():
            continue
        if facts_all.empty:
            continue
        year_facts = facts_all[facts_all["연도"] == year]
        if year_facts.empty:
            continue

        bs = year_facts[year_facts["재무표"] == "BS"]
        is_ = year_facts[year_facts["재무표"] == "IS"]
        cf = year_facts[year_facts["재무표"] == "CF"]

        capex_raw = _extract_fact_value(cf, "capex")
        inv_raw = _extract_fact_value(bs, "inventory")
        ar_raw = _extract_fact_value(bs, "receivables")
        ap_raw = _extract_fact_value(bs, "payables")
        cogs_raw = _extract_fact_value(is_, "cogs")
        int_exp_raw = _extract_fact_value(is_, "interest_expense")
        cash_raw = _extract_fact_value(bs, "cash")

        # CapEx는 투자활동 유출이므로 절댓값으로 저장 (양수)
        capex = abs(capex_raw) / _UNIT if capex_raw is not None else None
        inv = inv_raw / _UNIT if inv_raw is not None else None
        ar = ar_raw / _UNIT if ar_raw is not None else None
        ap = ap_raw / _UNIT if ap_raw is not None else None
        cogs = cogs_raw / _UNIT if cogs_raw is not None else None
        int_exp = int_exp_raw / _UNIT if int_exp_raw is not None else None
        cash = cash_raw / _UNIT if cash_raw is not None else None

        row = df[row_mask].iloc[0]
        rev = row.get("매출액")
        op = row.get("영업이익")
        ni = row.get("순이익")
        ocf = row.get("영업CF")
        assets = row.get("자산총계")

        # 파생 지표
        fcf = None
        if pd.notna(ocf) and capex is not None:
            fcf = ocf - capex

        fcf_margin = None
        if fcf is not None and pd.notna(rev) and rev:
            fcf_margin = round(fcf / rev * 100, 1)

        capex_ocf = None
        if capex is not None and pd.notna(ocf) and ocf:
            capex_ocf = round(capex / ocf * 100, 1)

        cfo_ni = None
        if pd.notna(ocf) and pd.notna(ni) and ni:
            cfo_ni = round(ocf / ni, 2)

        invested_cap = None
        roic = None
        if pd.notna(assets):
            invested_cap = assets - (cash or 0)
            if invested_cap:
                # EBIT * (1-t) / 투하자본. 영업이익을 EBIT 근사로 사용, 법인세율 22% 가정
                roic = round(op * 0.78 / invested_cap * 100, 1) if pd.notna(op) else None

        int_coverage = None
        if pd.notna(op) and int_exp is not None and int_exp:
            int_coverage = round(op / int_exp, 1)

        dio = round(365 * inv / cogs, 0) if (inv is not None and cogs) else None
        dso = round(365 * ar / rev, 0) if (ar is not None and pd.notna(rev) and rev) else None
        dpo = round(365 * ap / cogs, 0) if (ap is not None and cogs) else None
        ccc = None
        if dio is not None and dso is not None and dpo is not None:
            ccc = dio + dso - dpo

        # 값 기록
        idx = df.index[row_mask][0]
        df.at[idx, "CapEx"] = capex
        df.at[idx, "재고자산"] = inv
        df.at[idx, "매출채권"] = ar
        df.at[idx, "매입채무"] = ap
        df.at[idx, "매출원가"] = cogs
        df.at[idx, "이자비용"] = int_exp
        df.at[idx, "현금성자산"] = cash
        df.at[idx, "FCF"] = fcf
        df.at[idx, "FCF마진"] = fcf_margin
        df.at[idx, "CapEx_OCF"] = capex_ocf
        df.at[idx, "CFO_NI"] = cfo_ni
        df.at[idx, "투하자본"] = invested_cap
        df.at[idx, "ROIC"] = roic
        df.at[idx, "이자보상배율"] = int_coverage
        df.at[idx, "DIO"] = dio
        df.at[idx, "DSO"] = dso
        df.at[idx, "DPO"] = dpo
        df.at[idx, "CCC"] = ccc

    return df


def get_disclosures(corp_code: str, limit: int = 300) -> pd.DataFrame:
    with get_session() as session:
        rows = (
            session.query(Disclosure)
            .filter_by(corp_code=corp_code)
            .order_by(Disclosure.disc_date.desc())
            .limit(limit)
            .all()
        )
        data = [
            {"공시일": str(r.disc_date), "유형": r.disc_type,
             "공시명": r.report_nm, "제출인": r.flr_nm or "-",
             "접수번호": r.rcept_no}
            for r in rows
        ]
    return pd.DataFrame(data)


def get_auditors(corp_code: str) -> pd.DataFrame:
    from kreports.processor.audit_parser import is_valid_auditor_name

    with get_session() as session:
        rows = (
            session.query(Auditor)
            .filter_by(corp_code=corp_code)
            .order_by(Auditor.fs_div, Auditor.bsns_year)
            .all()
        )
        data = [
            {
                "회계연도": r.bsns_year, "구분": r.fs_div,
                "감사인": r.auditor_nm,
                "감사의견": r.audit_opinion or "-",
                "접수번호": r.rcept_no,
                "교체여부": (
                    "최초" if r.is_auditor_changed is None
                    else ("교체" if r.is_auditor_changed else "유지")
                ),
                "연속연수": r.consecutive_years or 1,
            }
            for r in rows
            if is_valid_auditor_name(r.auditor_nm)
        ]
    df = pd.DataFrame(data)
    if df.empty:
        return df

    df["_fs_rank"] = df["구분"].map({"CFS": 0, "OFS": 1}).fillna(9)
    df = (
        df.sort_values(["회계연도", "_fs_rank"])
        .groupby("회계연도", as_index=False)
        .first()
        .sort_values("회계연도")
        .drop(columns=["_fs_rank"])
        .reset_index(drop=True)
    )

    prev_auditor = None
    tenure = 0
    for idx, row in df.iterrows():
        auditor = row["감사인"]
        if prev_auditor is None:
            df.at[idx, "교체여부"] = "최초"
            tenure = 1
        elif auditor == prev_auditor:
            df.at[idx, "교체여부"] = "유지"
            tenure += 1
        else:
            df.at[idx, "교체여부"] = "교체"
            tenure = 1
        df.at[idx, "연속연수"] = tenure
        prev_auditor = auditor

    return df


def get_companies_by_corp_codes(corp_codes: list[str]) -> dict[str, dict]:
    """corp_code 리스트로 기업 정보 일괄 조회. {corp_code: {corp_name, stock_code}} 반환."""
    if not corp_codes:
        return {}
    with get_session() as session:
        rows = (
            session.query(Company)
            .filter(Company.corp_code.in_(corp_codes))
            .all()
        )
        return {
            r.corp_code: {"corp_name": r.corp_name, "stock_code": r.stock_code, "market": r.market}
            for r in rows
        }


def get_auditors_for_corp_codes(corp_codes: list[str]) -> pd.DataFrame:
    """
    복수 기업의 감사인 이력을 일괄 조회.
    각 기업의 최신 연도 감사인 정보만 반환 (연도별 상세는 미포함).
    """
    if not corp_codes:
        return pd.DataFrame()
    from kreports.processor.audit_parser import is_valid_auditor_name

    with get_session() as session:
        rows = (
            session.query(Auditor)
            .filter(Auditor.corp_code.in_(corp_codes))
            .order_by(Auditor.corp_code, Auditor.bsns_year.desc())
            .all()
        )
        data = [
            {
                "corp_code": r.corp_code,
                "회계연도": r.bsns_year,
                "fs_div": r.fs_div,
                "감사인": r.auditor_nm,
                "감사의견": r.audit_opinion or "-",
                "교체여부": (
                    "최초" if r.is_auditor_changed is None
                    else ("교체" if r.is_auditor_changed else "유지")
                ),
                "연속연수": r.consecutive_years or 1,
            }
            for r in rows
            if is_valid_auditor_name(r.auditor_nm)
        ]
    return pd.DataFrame(data)


def get_financial_facts(
    corp_code: str,
    bsns_year: int | None = None,
    reprt_code: str | None = None,
    fs_div: str = "CFS",
    sj_div: str | None = None,
) -> pd.DataFrame:
    """
    FinancialFact 테이블 조회 (상장사 전체 계정).

    Args:
        corp_code: 기업코드
        bsns_year: 특정 연도 필터 (None = 전체)
        reprt_code: 11013/11012/11014/11011 (None = 전체)
        fs_div: CFS/OFS
        sj_div: BS/IS/CIS/CF/SCE (None = 전체)

    Returns:
        DataFrame with columns: 연도, 보고서, 구분, 재무표, account_id, 계정명, 순서, 당기, 전기, 전전기
    """
    with get_session() as session:
        q = (
            session.query(FinancialFact)
            .filter_by(corp_code=corp_code, fs_div=fs_div)
        )
        if bsns_year is not None:
            q = q.filter(FinancialFact.bsns_year == bsns_year)
        if reprt_code is not None:
            q = q.filter(FinancialFact.reprt_code == reprt_code)
        if sj_div is not None:
            q = q.filter(FinancialFact.sj_div == sj_div)

        _REPRT_LABEL = {"11013": "Q1", "11012": "Q2", "11014": "Q3", "11011": "연간"}
        data = [
            {
                "연도": r.bsns_year,
                "보고서": _REPRT_LABEL.get(r.reprt_code, r.reprt_code),
                "구분": r.fs_div,
                "재무표": r.sj_div,
                "account_id": r.account_id,
                "계정명": r.account_nm,
                "순서": r.ord,
                "당기": r.thstrm_amount,
                "전기": r.frmtrm_amount,
                "전전기": r.bfefrmtrm_amount,
                "당분기": r.thstrm_add_amount,
            }
            for r in q.order_by(
                FinancialFact.bsns_year.desc(),
                FinancialFact.reprt_code,
                FinancialFact.sj_div,
                FinancialFact.ord,
            ).all()
        ]
    return pd.DataFrame(data)


def has_financial_facts(corp_code: str) -> bool:
    """FinancialFact 데이터 존재 여부."""
    with get_session() as session:
        return session.query(FinancialFact).filter_by(corp_code=corp_code).count() > 0


def get_financial_facts_years(corp_code: str) -> list[int]:
    """수집된 연도 목록 반환."""
    with get_session() as session:
        rows = (
            session.query(FinancialFact.bsns_year)
            .filter_by(corp_code=corp_code)
            .distinct()
            .order_by(FinancialFact.bsns_year.desc())
            .all()
        )
    return [r[0] for r in rows]


def get_years_with_business_report(corp_code: str) -> list[int]:
    """
    사업보고서 공시가 실제 수집된 사업연도 목록 반환 (최신순).
    공시일의 연도에서 -1을 적용한다 (사업연도 N의 사업보고서는 N+1년 3월경 공시).
    """
    with get_session() as session:
        rows = (
            session.query(Disclosure.disc_date)
            .filter_by(corp_code=corp_code)
            .filter(Disclosure.report_nm.like("%사업보고서%"))
            .order_by(Disclosure.disc_date.desc())
            .all()
        )
    years: set[int] = set()
    for (d,) in rows:
        try:
            y = int(str(d)[:4]) - 1
            years.add(y)
        except (ValueError, TypeError):
            continue
    return sorted(years, reverse=True)


def get_risk_summary(corp_code: str) -> dict:
    """위험 신호 요약. 항상 모든 키를 반환한다."""
    result = {
        "cfs_ofs_gap_count": 0,
        "low_map_conf_count": 0,
        "auditor_change_count": 0,
        "non_clean_opinion_count": 0,
        "equity_negative_count": 0,
        "going_concern_count": 0,
        "op_net_divergence_count": 0,
        "beneish_alert_count": 0,
        "latest_beneish_m": None,
        "nas_risk_count": 0,
        # 추가 플래그 (계산됐지만 표시 안 됐던 것)
        "revenue_vol_count": 0,         # |매출성장률| >= 30%
        "op_cf_divergence_count": 0,    # 영업이익>0 & 영업CF<0
        "high_accrual_count": 0,        # |발생액비율| > 1.0
        "amendment_heavy_count": 0,     # 연간 정정공시 >= 3건
        "has_data": False,
    }

    df = get_financials_both(corp_code)
    if not df.empty:
        result["has_data"] = True

        # 최근 2개 사업연도(8분기)로 제한 — 오래된 이력이 현재 위험으로 오해되는 것 방지
        if "연도" in df.columns and len(df) > 0:
            recent_years = sorted(df["연도"].dropna().unique())[-2:]
            df = df[df["연도"].isin(recent_years)].copy()

        def _flag_count(col: str) -> int:
            if col not in df.columns:
                return 0
            return int(df[col].apply(lambda x: bool(x) if x is not None else False).sum())

        if "CFS_OFS괴리" in df.columns:
            result["cfs_ofs_gap_count"] = _flag_count("CFS_OFS괴리")
        if "매핑신뢰도" in df.columns:
            result["low_map_conf_count"] = int(
                (pd.to_numeric(df["매핑신뢰도"], errors="coerce").fillna(1.0) < 0.8).sum()
            )
        result["equity_negative_count"] = _flag_count("자본잠식")
        result["going_concern_count"] = _flag_count("계속기업우려")
        result["op_net_divergence_count"] = _flag_count("영업순익괴리")
        result["beneish_alert_count"] = _flag_count("Beneish_flag")
        result["revenue_vol_count"] = _flag_count("매출급변")
        result["op_cf_divergence_count"] = _flag_count("영업CF괴리")
        if "발생액비율" in df.columns:
            result["high_accrual_count"] = int(
                pd.to_numeric(df["발생액비율"], errors="coerce").abs().fillna(0).gt(1.0).sum()
            )
        if "정정공시수" in df.columns:
            # 연도별 최대값(Q4 기준) >= 3
            cfs_q4 = df[(df["구분"] == "CFS") & (df["분기"] == 4)] if "구분" in df.columns else df[df["분기"] == 4]
            result["amendment_heavy_count"] = int(
                pd.to_numeric(cfs_q4["정정공시수"], errors="coerce").fillna(0).ge(3).sum()
            )
        if "Beneish_M" in df.columns:
            # 가장 최근 연간(Q4) Beneish M-Score
            cfs_q4 = df[(df["구분"] == "CFS") & (df["분기"] == 4)].copy() \
                if "구분" in df.columns else df[df["분기"] == 4].copy()
            m_vals = pd.to_numeric(cfs_q4["Beneish_M"], errors="coerce").dropna()
            if not m_vals.empty:
                result["latest_beneish_m"] = float(m_vals.iloc[-1])

    fee_df = get_audit_fees(corp_code)
    if not fee_df.empty:
        result["nas_risk_count"] = int(
            fee_df["독립성위험"].apply(lambda x: x is True).sum()
        )

    aud = get_auditors(corp_code)
    if not aud.empty:
        result["auditor_change_count"] = int((aud["교체여부"] == "교체").sum())
        result["non_clean_opinion_count"] = int(
            (~aud["감사의견"].isin(["-", "적정"])).sum()
        )

    return result


def get_restatement_delta(corp_code: str, threshold_pct: float = 1.0, top_n: int = 10) -> pd.DataFrame:
    """
    소급 재작성 감지.

    동일 `account_id`에 대해 (1) N+1년 사업보고서의 `frmtrm_amount`(전기 값)와
    (2) N년 사업보고서의 `thstrm_amount`(당기 값)를 비교한다.
    두 값은 동일 기간(N년 연말 잔액)을 가리켜야 하므로 원칙적으로 일치해야 한다.
    차이가 `threshold_pct` 이상이면 소급 재작성 후보로 반환.

    Args:
      corp_code: 기업코드
      threshold_pct: 차이 임계값 (%). 기본 1.0%.
      top_n: 반환 최대 항목 수.

    Returns:
      DataFrame 컬럼: 기준연도, 재무표, 계정명, 원본값(억원), 재작성값(억원), 차이(억원), 변동률(%)
      값이 없으면 빈 DataFrame.
    """
    with get_session() as session:
        # 연간 보고서(11011) CFS 우선 조회.
        # SCE(자본변동표)는 동일 계정명이 여러 맥락에서 반복되어 비교가 부정확하므로 제외.
        rows = (
            session.query(FinancialFact)
            .filter_by(corp_code=corp_code, fs_div="CFS", reprt_code="11011")
            .filter(FinancialFact.sj_div.in_(["BS", "IS", "CIS", "CF"]))
            .order_by(FinancialFact.bsns_year.desc())
            .all()
        )
        if not rows:
            rows = (
                session.query(FinancialFact)
                .filter_by(corp_code=corp_code, fs_div="OFS", reprt_code="11011")
                .filter(FinancialFact.sj_div.in_(["BS", "IS", "CIS", "CF"]))
                .order_by(FinancialFact.bsns_year.desc())
                .all()
            )
        fact_rows = [
            {
                "연도": r.bsns_year,
                "재무표": r.sj_div,
                "account_id": r.account_id,
                "계정명": r.account_nm,
                "당기": r.thstrm_amount,
                "전기": r.frmtrm_amount,
            }
            for r in rows
        ]

    if not fact_rows:
        return pd.DataFrame()

    facts = pd.DataFrame(fact_rows)
    years = sorted(facts["연도"].unique().tolist())
    if len(years) < 2:
        return pd.DataFrame()

    deltas: list[dict] = []
    for i in range(len(years) - 1):
        base_year = years[i]      # N
        next_year = years[i + 1]  # N+1
        base = facts[facts["연도"] == base_year][["재무표", "account_id", "계정명", "당기"]].rename(
            columns={"당기": "원본값"}
        )
        nxt = facts[facts["연도"] == next_year][["재무표", "account_id", "전기"]].rename(
            columns={"전기": "재작성값"}
        )
        merged = base.merge(nxt, on=["재무표", "account_id"], how="inner")
        if merged.empty:
            continue
        merged["원본값"] = pd.to_numeric(merged["원본값"], errors="coerce")
        merged["재작성값"] = pd.to_numeric(merged["재작성값"], errors="coerce")
        merged = merged.dropna(subset=["원본값", "재작성값"])
        # 원본값 0 또는 NaN이면 변동률 계산 불가
        merged = merged[merged["원본값"].abs() > 0]
        if merged.empty:
            continue
        merged["차이"] = merged["재작성값"] - merged["원본값"]
        merged["변동률"] = (merged["차이"] / merged["원본값"].abs() * 100).round(2)
        merged["기준연도"] = base_year
        merged = merged[merged["변동률"].abs() >= threshold_pct]
        if merged.empty:
            continue
        deltas.append(merged)

    if not deltas:
        return pd.DataFrame()

    out = pd.concat(deltas, ignore_index=True)
    # 억원 변환
    for col in ["원본값", "재작성값", "차이"]:
        out[col] = (out[col] / _UNIT).round(1)
    # 절대 변동률 기준 정렬
    out["abs_변동률"] = out["변동률"].abs()
    out = out.sort_values("abs_변동률", ascending=False).drop(columns=["abs_변동률", "account_id"])
    out = out[["기준연도", "재무표", "계정명", "원본값", "재작성값", "차이", "변동률"]]
    return out.head(top_n).reset_index(drop=True)


def get_going_concern_score(corp_code: str) -> dict:
    """
    6인자 기반 계속기업 스코어카드. 100점 만점에서 감점 방식.

    감점 체계 (최대 -100):
      자본잠식 (자본총계 < 0)            -30
      2년 연속 영업손실                  -20
      부채비율 > 200%                   -15
      이자보상배율 < 1.0                 -15
      최근 연도 영업CF < 0               -10
      최근 연도 감사의견 비적정           -10

    등급:
      >= 80 안정 (risk=ok)
      60-79  주의 (risk=warn)
      40-59  경고 (risk=warn)
      < 40   위험 (risk=bad)

    Returns:
      {
        "score": int,           # 0-100
        "grade": str,           # 안정/주의/경고/위험
        "risk": str,            # ok/warn/bad (kpi_card용)
        "factors": list[dict],  # [{"name", "hit", "penalty", "detail"}]
        "has_data": bool,
      }
    """
    factors: list[dict] = []
    score = 100

    df = get_financials_extended(corp_code, "CFS")
    if df.empty:
        df = get_financials_extended(corp_code, "OFS")

    if df.empty:
        return {
            "score": 0,
            "grade": "-",
            "risk": "ok",
            "factors": [],
            "has_data": False,
        }

    annual = df[df["분기"] == 4].sort_values("연도").copy()
    if annual.empty:
        annual = df.sort_values(["연도", "분기"]).copy()

    latest = annual.iloc[-1] if not annual.empty else None
    prev = annual.iloc[-2] if len(annual) >= 2 else None

    # 1. 자본잠식 (-30)
    equity = latest.get("자본총계") if latest is not None else None
    if pd.notna(equity) and equity < 0:
        score -= 30
        factors.append({
            "name": "자본잠식",
            "hit": True,
            "penalty": 30,
            "detail": f"자본총계 {equity:,.0f}억원",
        })
    else:
        factors.append({
            "name": "자본잠식",
            "hit": False,
            "penalty": 0,
            "detail": f"자본총계 {equity:,.0f}억원" if pd.notna(equity) else "데이터 없음",
        })

    # 2. 2년 연속 영업손실 (-20)
    op_loss_2yr = False
    op_detail = "-"
    if latest is not None and prev is not None:
        op_latest = latest.get("영업이익")
        op_prev = prev.get("영업이익")
        if pd.notna(op_latest) and pd.notna(op_prev) and op_latest < 0 and op_prev < 0:
            op_loss_2yr = True
            op_detail = f"전년 {op_prev:,.0f} → 당기 {op_latest:,.0f}억원"
        else:
            op_detail = f"당기 영업이익 {op_latest:,.0f}억원" if pd.notna(op_latest) else "데이터 없음"
    if op_loss_2yr:
        score -= 20
    factors.append({
        "name": "2년 연속 영업손실",
        "hit": op_loss_2yr,
        "penalty": 20 if op_loss_2yr else 0,
        "detail": op_detail,
    })

    # 3. 부채비율 > 200% (-15)
    debt_ratio = latest.get("부채비율") if latest is not None else None
    debt_hit = pd.notna(debt_ratio) and debt_ratio > 200
    if debt_hit:
        score -= 15
    factors.append({
        "name": "부채비율 > 200%",
        "hit": bool(debt_hit),
        "penalty": 15 if debt_hit else 0,
        "detail": f"부채비율 {debt_ratio:.0f}%" if pd.notna(debt_ratio) else "데이터 없음",
    })

    # 4. 이자보상배율 < 1.0 (-15)
    int_cov = latest.get("이자보상배율") if latest is not None else None
    int_hit = pd.notna(int_cov) and int_cov < 1.0
    if int_hit:
        score -= 15
    factors.append({
        "name": "이자보상배율 < 1.0",
        "hit": bool(int_hit),
        "penalty": 15 if int_hit else 0,
        "detail": f"이자보상배율 {int_cov:.1f}x" if pd.notna(int_cov) else "이자비용 미확인",
    })

    # 5. 영업CF < 0 (-10)
    ocf = latest.get("영업CF") if latest is not None else None
    ocf_hit = pd.notna(ocf) and ocf < 0
    if ocf_hit:
        score -= 10
    factors.append({
        "name": "최근 연도 영업CF 음수",
        "hit": bool(ocf_hit),
        "penalty": 10 if ocf_hit else 0,
        "detail": f"영업CF {ocf:,.0f}억원" if pd.notna(ocf) else "데이터 없음",
    })

    # 6. 최근 연도 감사의견 비적정 (-10)
    aud_df = get_auditors(corp_code)
    opinion_hit = False
    opinion_detail = "감사인 이력 없음"
    if not aud_df.empty:
        # 최신 연도, CFS 우선
        cfs_aud = aud_df[aud_df["구분"] == "CFS"].sort_values("회계연도")
        target_aud = cfs_aud if not cfs_aud.empty else aud_df.sort_values("회계연도")
        latest_aud = target_aud.iloc[-1]
        opinion = latest_aud.get("감사의견", "-")
        opinion_detail = f"{latest_aud.get('회계연도')}년 감사의견: {opinion}"
        if opinion not in ("-", "적정"):
            opinion_hit = True
    if opinion_hit:
        score -= 10
    factors.append({
        "name": "최근 감사의견 비적정",
        "hit": opinion_hit,
        "penalty": 10 if opinion_hit else 0,
        "detail": opinion_detail,
    })

    # 등급 산정
    score = max(score, 0)
    if score >= 80:
        grade = "안정"
        risk = "ok"
    elif score >= 60:
        grade = "주의"
        risk = "warn"
    elif score >= 40:
        grade = "경고"
        risk = "warn"
    else:
        grade = "위험"
        risk = "bad"

    return {
        "score": score,
        "grade": grade,
        "risk": risk,
        "factors": factors,
        "has_data": True,
    }


def get_audit_fees(corp_code: str) -> pd.DataFrame:
    """
    DS002 수집 데이터에서 감사보수 이력을 반환한다.
    collect-audit-fees 완료 후 사용 가능. 테이블 미생성 시 빈 DataFrame 반환.
    """
    try:
        with get_session() as session:
            rows = (
                session.query(AuditFee)
                .filter_by(corp_code=corp_code)
                .order_by(AuditFee.bsns_year)
                .all()
            )
        data = [
            {
                "회계연도": r.bsns_year,
                "감사인": r.auditor_nm or "-",
                "감사보수(백만원)": r.audit_fee_m,
                "감사시간": r.audit_hours,
                "비감사보수(백만원)": r.non_audit_fee_m,
                "비감사시간": r.non_audit_hours,
                "NAS_ratio": r.nas_ratio,
                "독립성위험": r.independence_risk_flag,
            }
            for r in rows
        ]
        return pd.DataFrame(data)
    except Exception:
        return pd.DataFrame()


def get_audit_fee_history(corp_code: str) -> pd.DataFrame:
    """사업보고서 document.xml에서 감사용역 체결현황(AUD_SIGK) 파싱."""
    import re
    import time

    # 연간 사업보고서 rcept_no 목록 가져오기 (최대 6개년)
    with get_session() as session:
        rows = (
            session.query(Disclosure.rcept_no, Disclosure.disc_date, Disclosure.report_nm)
            .filter_by(corp_code=corp_code)
            .filter(Disclosure.report_nm.like("%사업보고서%"))
            .order_by(Disclosure.disc_date.desc())
            .limit(6)
            .all()
        )
        disclosures = [(r.rcept_no, str(r.disc_date), r.report_nm) for r in rows]

    if not disclosures:
        return pd.DataFrame()

    from kreports.collector.fetcher import fetch_document_xml

    records = []
    for rcept_no, disc_date, report_nm in disclosures:
        content = fetch_document_xml(rcept_no)
        if not content:
            continue

        # AUD_SIGK 테이블 추출
        m = re.search(r'ACLASS="AUD_SIGK"', content)
        if not m:
            continue

        table_end = content.find('</TABLE-GROUP>', m.start())
        if table_end == -1:
            continue
        table = content[m.start():table_end + len('</TABLE-GROUP>')]

        # 행별 파싱: 사업연도, 감사인, 보수(금액)
        rows_data = re.findall(
            r'<TR[^>]*>.*?</TR>',
            table,
            re.DOTALL
        )
        for row_html in rows_data:
            # 텍스트 추출
            cells = re.findall(r'<T[EDHU][^>]*>([^<]*(?:<P[^>]*>[^<]*</P>[^<]*)*)</T[EDHU]>', row_html)
            clean = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
            clean = [c for c in clean if c and c not in ('N', 'Y')]

            # 사업연도 + 감사인 + 보수 패턴 감지
            year_cell = next((c for c in clean if '기' in c and ('당기' in c or '전기' in c or '전전기' in c)), None)
            auditor_cell = next((c for c in clean if '회계법인' in c or '감사법인' in c), None)
            fee_cells = [c for c in clean if re.match(r'^[\d,]+$', c.replace(',', '').strip())]

            if year_cell and auditor_cell:
                fee = fee_cells[0] if fee_cells else '-'
                records.append({
                    '사업연도': year_cell,
                    '감사인': auditor_cell,
                    '보수(백만원)': fee,
                    '접수번호': rcept_no,
                })

        time.sleep(0.3)

    return pd.DataFrame(records).drop_duplicates(subset=['사업연도', '감사인']) if records else pd.DataFrame()


def _fetch_note_files_cached(rcept_no: str) -> dict:
    """document.xml ZIP의 별첨 재무제표 파일들을 캐시 (1시간)."""
    from kreports.collector.fetcher import fetch_document_zip_files
    return fetch_document_zip_files(rcept_no)


# 회계정책 소제목 매칭용 키워드 — 구체적 표현 위주로 재구성.
# dart_platform.processor.policy_parser.POLICY_KEYWORDS 는 TITLE 기반 폴백용으로 유지하고,
# 여기서는 <P> 세그먼트 헤드 매칭용으로 더 엄격한 키워드를 사용한다.
_POLICY_HEAD_KEYWORDS: dict[str, list[str]] = {
    "revenue_recognition": ["수익인식", "수익의 인식", "매출인식", "수익 인식", "수행의무", "변동대가"],
    "inventory_valuation": ["재고자산", "저가법", "선입선출", "평균법", "총평균법"],
    "depreciation": ["감가상각", "유형자산"],
    "intangible_assets": ["무형자산", "개발비", "영업권"],
    "financial_instruments": ["금융상품", "금융자산의 분류", "금융부채", "금융자산"],
    "lease": ["리스", "사용권자산", "IFRS 16", "K-IFRS 제1116호"],
    "provisions": ["충당부채", "복구충당", "제품보증", "환경충당"],
    "impairment": ["자산손상", "손상차손", "손상검사", "현금창출단위", "회수가능액"],
    "consolidation": ["관계기업", "공동기업", "연결범위", "연결대상", "종속기업의 변동"],
    "employee_benefits": ["종업원급여", "퇴직급여", "확정급여", "확정기여", "주식기준보상"],
    "foreign_currency": ["외화환산", "외화거래", "기능통화", "표시통화"],
    "construction_contract": ["건설계약", "공사계약", "진행률", "공사손실", "공사수익", "진행기준"],
    "financial_instruments_hedge": ["헤지회계", "위험회피회계", "파생상품"],
    "tax": ["법인세", "이연법인세", "당기법인세"],
    "insurance_contract": ["IFRS 17", "K-IFRS 제1117호", "보험부채", "보험수익"],
}


def _extract_policy_items_from_xml(policy_xml: str) -> dict[str, dict]:
    """
    회계정책 XML 블록에서 <P> 태그 단위로 소제목을 인식하여 item_key별 발췌문 반환.

    DART 사업보고서의 회계정책은 대부분 `<P>2-N ...</P>`, `<P>2-N-N ...</P>`,
    `<P>1) ...</P>` 패턴으로 위계가 매겨져 있다. 이를 세그먼트 경계로 사용한다.

    매칭: 세그먼트 헤드(첫 120자, 소제목 포함)에 키워드가 포함된 경우.
    - 챕터 헤더(예: "2. 중요한 회계정책")는 스킵
    - 매우 일반적 헤드(예: "2-1 작성기준")는 스킵

    Returns:
        {
          item_key: {
            "heading": "2-2-11 유형자산",   # 소제목 (첫 <P>)
            "body": "유형자산은 원가로...",   # 본문 (나머지 <P> 조인)
          },
          ...
        }
    """
    import re as _re

    # <P> 텍스트 추출 (태그 제거)
    p_blocks: list[str] = []
    for m in _re.finditer(r'<P[^>]*>(.*?)</P>', policy_xml, _re.DOTALL | _re.IGNORECASE):
        inner = m.group(1)
        plain = _re.sub(r'<[^>]+>', ' ', inner)
        plain = _re.sub(r'\s+', ' ', plain).strip()
        if plain:
            p_blocks.append(plain)

    if not p_blocks:
        return {}

    # 소제목 감지 패턴 — 시작 부분이 다음과 같은 번호 표기
    # [.-] : "2-8" (하이픈)과 "2.8" (점) 모두 매칭 (삼성전자 등)
    sub_pattern = _re.compile(
        r'^\s*(?:\d+(?:[.\-]\d+)+|\(\d+\)|\d+\)|[가-힣]\.)\s*',
    )
    # 챕터 헤더 감지: "N. 제목" 형태 (점 뒤에 숫자 아닌 문자 → 1단계 헤더)
    chapter_header_re = _re.compile(r'^\s*\d+\.\s*[^\d]')

    # 세그먼트 빌드: 소제목 P를 별도 저장, 이후 비-소제목 P들을 body로 누적
    # segments = [(head_p, [body_p, ...]), ...]
    segments: list[tuple[str, list[str]]] = []
    cur_head: str | None = None
    cur_body: list[str] = []
    for p in p_blocks:
        if sub_pattern.match(p):
            if cur_head is not None:
                segments.append((cur_head, cur_body))
            cur_head = p
            cur_body = []
        else:
            if cur_head is None:
                # 선두에 소제목이 없으면 이 P를 pseudo-head로 사용
                cur_head = p
            else:
                cur_body.append(p)
    if cur_head is not None:
        segments.append((cur_head, cur_body))

    # 스킵할 헤드 키워드 — 이 표현으로 시작하는 세그먼트는 일반 설명이라 매칭 제외
    skip_head_markers = (
        "연결재무제표의 작성기준", "작성기준", "회계기준의 적용",
        "제정ㆍ공표되었으나", "제정·공표되었으나", "시행되지 않은",
        "중요한 회계정책", "중요한 회계적 판단",
    )

    # 헤드 소제목 부분에서 번호 표기 떼어낸 실제 제목만 추출하기 위한 패턴
    # (순전히 표시 가독성 용도)
    _heading_prefix_re = _re.compile(
        r'^\s*(?:\d+(?:[.\-]\d+)+|\(\d+\)|\d+\))\s*'
    )

    matched: dict[str, dict] = {}

    def _excerpt(text: str, limit: int = 600) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "…"

    for head_p, body_ps in segments:
        seg_full = head_p if not body_ps else (head_p + " " + " ".join(body_ps))
        if len(seg_full) < 40:
            continue
        head_120 = seg_full[:120]
        if chapter_header_re.match(head_120):
            continue
        if any(sk in head_120 for sk in skip_head_markers):
            continue
        for key, keywords in _POLICY_HEAD_KEYWORDS.items():
            if key in matched:
                continue
            if any(kw in head_120 for kw in keywords):
                heading, head_body_remainder = _split_head_and_body(
                    head_p, keywords,
                )
                body_parts = []
                if head_body_remainder:
                    body_parts.append(head_body_remainder)
                body_parts.extend(body_ps)
                body_text = " ".join(body_parts).strip()

                matched[key] = {
                    "heading": heading,
                    "body": _excerpt(body_text, 600),
                }
                break
    return matched


def _split_head_and_body(head_p: str, item_keywords: list[str]) -> tuple[str, str]:
    """
    소제목과 본문이 연속된 단일 <P> 문자열을 (heading, body_remainder)로 분리한다.

    DART 사업보고서는 종종 `<P>2-2-11 유형자산유형자산은 원가로...</P>` 처럼
    번호 + 소제목 + 본문이 한 P에 붙어있다. 다음 전략을 순서대로 시도:

      1) 번호 접두 제거 후 "같은 prefix가 뒤에 또 나타나는" 반복 탐지 (유형자산 반복 케이스)
      2) 매칭된 item_keywords 중 rest가 시작하는 키워드로 자르기 (퇴직급여·충당부채 케이스)
      3) 첫 공백 기준 fallback

    Returns:
        (heading, body_remainder) — body_remainder는 heading 이후 잔여 텍스트
    """
    import re as _re
    _prefix_re = _re.compile(r'^\s*(?:\d+(?:-\d+)+|\(\d+\)|\d+\))\s*')

    m = _prefix_re.match(head_p)
    if not m:
        # 번호 없는 경우: 첫 30자 정도를 heading으로
        short = head_p[:40].strip()
        return short, head_p[len(short):].strip()

    prefix = m.group(0).strip()
    rest = head_p[m.end():].lstrip()

    # 전략 1: 키워드 기반 — rest가 알려진 키워드로 시작하면 그 끝까지
    # "퇴직급여연결실체는 확정..." → "퇴직급여" 까지
    for kw in sorted(item_keywords, key=len, reverse=True):
        if rest.startswith(kw):
            return f"{prefix} {kw}", rest[len(kw):].lstrip()

    # 전략 2: 반복 탐지 (4자 이상 prefix가 30자 이내에 재출현)
    # "관계기업 및 공동기업관계기업은..." → "관계기업" 재출현 → heading = "관계기업 및 공동기업"
    for cut in range(12, 3, -1):
        token = rest[:cut]
        if not token or token.isspace():
            continue
        pos = rest.find(token, cut)
        if 0 < pos <= 30:
            heading_core = rest[:pos].rstrip(' ·')
            body_rem = rest[pos:].strip()
            return f"{prefix} {heading_core}", body_rem

    # 전략 3: 키워드가 rest 시작 근처(0~4자 오프셋)에 있으면 그 지점까지
    # "개별취득하는 무형자산 ..." → "무형자산" at 7 → "개별취득하는 무형자산"
    for kw in sorted(item_keywords, key=len, reverse=True):
        idx = rest.find(kw)
        if 0 <= idx <= 10:
            end = idx + len(kw)
            return f"{prefix} {rest[:end].strip()}", rest[end:].lstrip()

    # 전략 4: 첫 공백 + 다음 한자 단어 (예: "발전소 건설..." → "발전소 건설")
    m2 = _re.match(r'([가-힣A-Za-z]+(?:\s+[가-힣A-Za-z]+)?)', rest)
    if m2:
        cand = m2.group(1)
        if 2 <= len(cand) <= 20:
            return f"{prefix} {cand}", rest[len(cand):].lstrip()

    # 최후: 첫 15자
    return f"{prefix} {rest[:15].strip()}", rest[15:].lstrip()


def _select_note_file(all_files: dict[str, str], fs_div: str) -> str | None:
    """
    ZIP 내 파일 중 '주석' TITLE이 있는 파일을 선택한다.

    선택 전략:
      1) <TITLE>주석</TITLE> 또는 유사 TITLE이 본문에 있는 파일만 후보
      2) 그 중 fs_div에 해당하는 파일 (CFS=연결, OFS=별도/첨부) 우선
      3) 일치하는 것이 없으면 폴백 파일 반환

    작은 코스닥 상장사는 연결재무제표가 없이 감사보고서 파일(_00760)에만
    주석이 포함되는 경우가 많다. 이 경우도 OFS 폴백으로 잡힌다.
    """
    import re as _re
    _note_title_re = _re.compile(
        r'<TITLE[^>]*>\s*(?:주석|주&nbsp;석)\s*</TITLE>',
    )

    candidates: list[tuple[str, str]] = []  # (fname, content)
    for fname, content in all_files.items():
        if _note_title_re.search(content):
            candidates.append((fname, content))

    if not candidates:
        return None

    # CFS/OFS 분류 시도 — 전체 내용에서 키워드 탐색
    cfs_markers = ("연 결 재 무 제 표", "연결재무제표")
    ofs_markers = ("(첨부)재 무 제 표", "(첨부)재무제표", "별 도 재 무 제 표", "별도재무제표")

    cfs_files: list[str] = []
    ofs_files: list[str] = []
    for fname, content in candidates:
        # DOCUMENT-NAME 태그도 참고
        doc_name_m = _re.search(r'<DOCUMENT-NAME[^>]*>([^<]+)</DOCUMENT-NAME>', content)
        doc_name = doc_name_m.group(1) if doc_name_m else ""

        is_cfs = any(kw in content for kw in cfs_markers)
        is_ofs = any(kw in content for kw in ofs_markers)

        if is_cfs and not is_ofs:
            cfs_files.append(content)
        elif is_ofs and not is_cfs:
            ofs_files.append(content)
        elif is_cfs and is_ofs:
            # 두 키워드 모두 포함 — 둘 다 후보에 추가
            cfs_files.append(content)
            ofs_files.append(content)
        else:
            # 키워드 없음 — 감사보고서(00760)는 일반적으로 OFS
            if "00760" in fname or "감사보고서" in doc_name:
                ofs_files.append(content)
            else:
                # 본문(사업보고서)이면 주 파일이므로 둘 다 폴백
                cfs_files.append(content)
                ofs_files.append(content)

    # fs_div에 맞춰 선택
    if fs_div == "CFS":
        if cfs_files:
            return cfs_files[0]
        if ofs_files:
            return ofs_files[0]
    else:
        if ofs_files:
            return ofs_files[0]
        if cfs_files:
            return cfs_files[0]
    return candidates[0][1]


def _note_chapters(note_section: str) -> list[tuple[str, int, int]]:
    """
    주석 섹션을 <P>N. 제목...</P> 번호 헤딩 기반으로 챕터로 분할한다.

    DART 사업보고서 XML은 TITLE 태그가 없고 모든 주석 제목이
    `<P>숫자. 제목</P>` 형태로 들어있는 경우가 많다.
    이런 구조에서 챕터 경계를 잡기 위한 공통 헬퍼.

    Returns:
        [(heading_label, start_offset, end_offset), ...]
        heading_label 예: "2. 중요한 회계정책"
    """
    import re as _re
    # <P> 시작 직후에 "숫자. " 으로 시작하는 패턴
    pattern = _re.compile(
        r'<P[^>]*>\s*(\d{1,2})\s*[\.．]\s*([^<\n]{1,120})',
        _re.DOTALL | _re.IGNORECASE,
    )
    hits = []
    for m in pattern.finditer(note_section):
        num = int(m.group(1))
        title = m.group(2).strip()
        hits.append((num, title, m.start()))

    if not hits:
        return []

    # 번호가 단조 증가하는 구간만 유지 (표 안의 숫자 오탐 방지).
    # 다음 번호는 prev_num보다 커야 하고, 10 이내의 건너뜀을 허용한다.
    # (소규모 상장사는 일부 번호가 누락되는 경우가 흔함: 예 1→2→6→7→13)
    valid: list[tuple[int, str, int]] = []
    for h in hits:
        if not valid:
            valid.append(h)
            continue
        prev_num = valid[-1][0]
        if prev_num < h[0] <= prev_num + 10:
            valid.append(h)
        # 역전·동일·10 초과는 오탐으로 간주하여 스킵
    if not valid:
        return []

    chapters: list[tuple[str, int, int]] = []
    for i, (num, title, pos) in enumerate(valid):
        end = valid[i + 1][2] if i + 1 < len(valid) else len(note_section)
        label = f"{num}. {title}"
        chapters.append((label, pos, end))
    return chapters


def _plain_text_from_xml(xml_snippet: str) -> str:
    import re as _re

    text_parts: list[str] = []
    for m in _re.finditer(r'<P[^>]*>(.*?)</P>', xml_snippet, _re.DOTALL | _re.IGNORECASE):
        inner = m.group(1)
        plain = _re.sub(r'<[^>]+>', ' ', inner)
        plain = _re.sub(r'\s+', ' ', plain).strip()
        if plain:
            text_parts.append(plain)
    if text_parts:
        return " ".join(text_parts)
    plain = _re.sub(r'<[^>]+>', ' ', xml_snippet)
    return _re.sub(r'\s+', ' ', plain).strip()


def _classify_accounting_note_chapter(note_no: str, title: str) -> str | None:
    normalized = " ".join((title or "").split())
    if any(keyword in normalized for keyword in (
        "회계추정", "회계적 추정", "중요한 판단", "유의적인 판단",
        "추정 및 판단", "판단 및 추정",
    )):
        return "estimate_judgment"
    if any(keyword in normalized for keyword in (
        "중요한 회계정책", "유의적 회계정책", "유의적인 회계정책",
        "회계처리방침", "회계정책",
    )):
        return "policy"
    if any(keyword in normalized for keyword in (
        "작성기준", "재무제표의 작성", "연결재무제표의 작성",
        "준거 회계기준",
    )):
        return "basis"
    return None


def extract_accounting_note_chapters(note_section: str) -> list[dict]:
    """Extract basis, policy, and estimate/judgment chapters from note XML."""
    chapters = _note_chapters(note_section)
    extracted: list[dict] = []
    for label, start, end in chapters:
        note_no, _, note_title = label.partition(".")
        note_no = note_no.strip()
        note_title = note_title.strip()
        section_type = _classify_accounting_note_chapter(note_no, note_title)
        if section_type is None:
            continue
        xml_block = note_section[start:end]
        body = _plain_text_from_xml(xml_block)
        if not body:
            continue
        extracted.append({
            "note_no": note_no,
            "note_title": note_title,
            "section_type": section_type,
            "body": body,
            "body_length": len(body),
        })
    return extracted


def _xml_to_html(xml_snippet: str) -> str:
    """DART XML 주석 섹션을 렌더링 가능한 HTML로 변환한다. 테이블 구조를 보존."""
    import re

    s = xml_snippet

    # TABLE 계열 태그 → 스타일 있는 HTML
    s = re.sub(
        r'<TABLE(?:\s[^>]*)?>',
        '<table style="border-collapse:collapse;width:100%;font-size:0.8rem;margin:0.8rem 0;">',
        s, flags=re.IGNORECASE,
    )
    s = re.sub(r'</TABLE>', '</table>', s, flags=re.IGNORECASE)
    s = re.sub(r'<TR(?:\s[^>]*)?>',
               '<tr>', s, flags=re.IGNORECASE)
    s = re.sub(r'</TR>', '</tr>', s, flags=re.IGNORECASE)
    s = re.sub(
        r'<TH(?:\s[^>]*)?>',
        '<th style="border:1px solid #D1D8F0;padding:5px 8px;background:#1E49E2;'
        'color:white;text-align:left;font-size:0.75rem;white-space:nowrap;">',
        s, flags=re.IGNORECASE,
    )
    s = re.sub(r'</TH>', '</th>', s, flags=re.IGNORECASE)
    s = re.sub(
        r'<TD(?:\s[^>]*)?>',
        '<td style="border:1px solid #E5E7EB;padding:4px 8px;color:#1A1A2E;'
        'background:white;vertical-align:top;">',
        s, flags=re.IGNORECASE,
    )
    s = re.sub(r'</TD>', '</td>', s, flags=re.IGNORECASE)

    # P / SPAN / B / STRONG → 인라인 유지
    s = re.sub(r'<P(?:\s[^>]*)?>',
               '<p style="margin:4px 0;font-size:0.82rem;color:#1A1A2E;">', s, flags=re.IGNORECASE)
    s = re.sub(r'</P>', '</p>', s, flags=re.IGNORECASE)
    s = re.sub(r'<BR\s*/?>', '<br>', s, flags=re.IGNORECASE)
    s = re.sub(r'<(?:SPAN|B|STRONG)(?:\s[^>]*)?>',
               '<span style="color:#1A1A2E;">', s, flags=re.IGNORECASE)
    s = re.sub(r'</(?:SPAN|B|STRONG)>', '</span>', s, flags=re.IGNORECASE)

    # 나머지 XML 태그 제거 (DART 전용 래퍼 등)
    _KEEP = frozenset(['table', '/table', 'tr', '/tr', 'td', '/td', 'th', '/th',
                       'p', '/p', 'br', 'span', '/span'])

    def _strip_unknown(m: re.Match) -> str:
        inner = m.group(1).lower().split()[0] if m.group(1).strip() else ''
        return m.group(0) if inner in _KEEP else ' '

    s = re.sub(r'<(/?\w[^>]*)>', _strip_unknown, s)

    # 공백 정리
    s = re.sub(r'[ \t]{2,}', ' ', s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


def get_notes_for_account(corp_code: str, bsns_year: int, account_nm: str, fs_div: str = "CFS") -> str | None:
    """
    사업보고서 별첨 재무제표 XML의 주석 섹션에서 account_nm 관련 주석을 검색한다.
    테이블 구조를 보존한 HTML을 반환한다.

    Returns: HTML 문자열 or None
    """
    import re

    with get_session() as session:
        # 사업연도 N의 사업보고서는 N+1년 1월~6월에 공시됨 (정정공시는 그 이후도 가능)
        row = (
            session.query(Disclosure.rcept_no)
            .filter_by(corp_code=corp_code)
            .filter(Disclosure.report_nm.like("%사업보고서%"))
            .filter(Disclosure.disc_date >= f"{bsns_year + 1}-01-01")
            .filter(Disclosure.disc_date <= f"{bsns_year + 1}-12-31")
            .order_by(Disclosure.disc_date.desc())
            .first()
        )
        if row is None:
            return None
        rcept_no = row.rcept_no

    all_files = _fetch_note_files_cached(rcept_no)
    if not all_files:
        return None

    target_content = _select_note_file(all_files, fs_div)
    if not target_content:
        return None

    # 주석 섹션 추출
    m_note = re.search(r'<TITLE[^>]*>주석</TITLE>', target_content)
    if not m_note:
        return None
    note_start = m_note.start()

    next_major = re.search(
        r'<TITLE[^>]*>(?:연결 내부|내부회계|외부감사 실시|독립된)',
        target_content[note_start + 100:]
    )
    note_end = note_start + 100 + next_major.start() if next_major else len(target_content)
    note_section = target_content[note_start:note_end]

    if account_nm not in note_section:
        return None

    # 챕터 분할: <P>N. 제목</P> 기반 (TITLE 태그 부재 시 대응)
    chapters = _note_chapters(note_section)

    # 폴백: 챕터가 없으면 TITLE 기반으로 블록 추정
    if not chapters:
        _title_re = re.compile(r'<TITLE[^>]*>[^<]{1,120}</TITLE>')
        tps = [(m.start(), m.end()) for m in _title_re.finditer(note_section)]
        if tps:
            chapters = []
            for i, (ts, te) in enumerate(tps):
                cend = tps[i + 1][0] if i + 1 < len(tps) else len(note_section)
                label = re.sub(r'<[^>]+>', '', note_section[ts:te]).strip()
                chapters.append((label, ts, cend))
        else:
            # 최후 수단: 전체를 하나의 블록으로
            chapters = [("전체", 0, len(note_section))]

    seen_starts: set[int] = set()
    title_hit: list[tuple[str, int, int]] = []
    body_hit: list[tuple[str, int, int]] = []

    # 일반 설명 챕터 (회계정책·회계 판단·작성 기준) — 특정 계정 조회 시 노이즈라 제외
    _GENERAL_KW = (
        "회계정책", "회계 판단", "회계적 판단", "추정 및 가정", "작성기준",
        "회계기준", "일반사항", "시행되지 않은",
    )

    def _is_general(label: str) -> bool:
        return any(kw in label for kw in _GENERAL_KW)

    # 1순위: 챕터 제목에 account_nm 포함 (일반 설명은 제외)
    for label, cs, ce in chapters:
        if _is_general(label):
            continue
        if account_nm in label:
            if cs not in seen_starts:
                seen_starts.add(cs)
                title_hit.append((label, cs, ce))

    # 2순위: 챕터 본문에 account_nm 포함 (일반 설명 챕터는 제외)
    for label, cs, ce in chapters:
        if cs in seen_starts:
            continue
        if _is_general(label):
            continue
        snippet = note_section[cs:ce]
        if account_nm in snippet:
            seen_starts.add(cs)
            body_hit.append((label, cs, ce))

    # 본문 hit은 최대 3개로 제한 (너무 많은 챕터가 걸리지 않도록)
    ordered = title_hit + body_hit[:3]

    if not ordered:
        return None

    parts = []
    for label, cs, ce in ordered:
        snippet = note_section[cs:ce]
        # 챕터 크기가 너무 큰 경우(50KB 초과) 일부만 사용
        if len(snippet) > 50_000:
            snippet = snippet[:50_000] + '<P>[주석 내용이 길어 일부만 표시합니다]</P>'
        html = _xml_to_html(snippet)
        if len(html) > 100:
            # 챕터 라벨을 상단에 표시
            header = (
                f'<div style="font-size:0.85rem;color:#1E49E2;font-weight:700;'
                f'margin:0.4rem 0 0.6rem 0;">{label}</div>'
            )
            parts.append(header + html)

    if not parts:
        return None

    return '<hr style="border:none;border-top:1px solid #D1D8F0;margin:1rem 0;">'.join(parts)


def get_accounting_policy(corp_code: str, bsns_year: int, fs_div: str = "CFS") -> dict | None:
    """
    사업보고서 별첨 재무제표 XML에서 '중요한 회계정책' 섹션을 추출하고 정형화한다.

    Returns:
        {
            "raw_html": str,          # 전체 회계정책 섹션 HTML
            "items": dict[str, str],  # {item_key: 발췌 텍스트}
            "rcept_no": str,
        }
        또는 None (데이터 없음)
    """
    import re
    from kreports.processor.policy_parser import extract_policy_section, extract_policy_items

    # 사업보고서 rcept_no 조회 — 사업연도 N의 사업보고서는 N+1년 1~12월에 공시
    with get_session() as session:
        row = (
            session.query(Disclosure.rcept_no)
            .filter_by(corp_code=corp_code)
            .filter(Disclosure.report_nm.like("%사업보고서%"))
            .filter(Disclosure.disc_date >= f"{bsns_year + 1}-01-01")
            .filter(Disclosure.disc_date <= f"{bsns_year + 1}-12-31")
            .order_by(Disclosure.disc_date.desc())
            .first()
        )
        if row is None:
            return None
        rcept_no = row.rcept_no

    all_files = _fetch_note_files_cached(rcept_no)
    if not all_files:
        return None

    target_content = _select_note_file(all_files, fs_div)
    if not target_content:
        return None

    # 주석 섹션 추출
    m_note = re.search(r'<TITLE[^>]*>주석</TITLE>', target_content)
    if not m_note:
        return None
    note_start = m_note.start()

    next_major = re.search(
        r'<TITLE[^>]*>(?:연결 내부|내부회계|외부감사 실시|독립된)',
        target_content[note_start + 100:]
    )
    note_end = note_start + 100 + next_major.start() if next_major else len(target_content)
    note_section = target_content[note_start:note_end]

    # 전략 1: TITLE 기반 (기존 policy_parser) — 일부 기업은 TITLE 사용
    policy_xml = extract_policy_section(note_section)

    # 전략 2: <P> 번호 헤딩 기반 챕터 매칭 — 대부분의 DART 보고서
    if not policy_xml:
        chapters = _note_chapters(note_section)
        # 우선순위: 구체적 키워드 → 포괄적 키워드
        # 소규모 상장사는 '2. 재무제표 작성기준' 하나에 회계정책을 통합해 기재하기도 함
        policy_keywords = (
            "중요한 회계정책", "유의적 회계정책", "유의적인 회계정책",
            "작성기준 및 중요한 회계정책", "재무제표 작성기준",
            "연결재무제표 작성기준",
            # 일부 기업(삼성전자 등)은 "회계처리방침" 표현 사용
            "중요한 회계처리방침", "유의적인 회계처리방침", "회계처리방침",
            "회계정책",
        )
        matched: tuple[str, int, int] | None = None
        for kw in policy_keywords:
            for label, cs, ce in chapters:
                if kw in label:
                    matched = (label, cs, ce)
                    break
            if matched:
                break
        if matched is None:
            return None
        _, cs, ce = matched
        policy_xml = note_section[cs:ce]

    if not policy_xml:
        return None

    raw_html = _xml_to_html(policy_xml)
    # <P> 기반 세그먼트 매칭을 우선 시도 (대부분의 DART 보고서)
    items = _extract_policy_items_from_xml(policy_xml)
    # 폴백 1: TITLE 기반 소제목(기존 policy_parser)
    if not items:
        items = extract_policy_items(policy_xml)

    # 폴백 2: 토픽-챕터 매칭
    # 삼성전자·현대차 등 대기업은 회계정책을 별도 단일 챕터가 아니라
    # 각 주제 챕터(10. 유형자산, 11. 무형자산 등)에 분산 기재한다.
    # 단일 정책 챕터에서 3개 미만이 잡히면 전체 note_section의 챕터를
    # 토픽별로 매칭하여 보강 추출한다.
    if len(items) < 3:
        topic_items = _extract_topic_chapter_items(note_section)
        # 기존 items에 토픽 items 보강 (기존 키 우선)
        for k, v in topic_items.items():
            if k not in items:
                items[k] = v

    chapters = extract_accounting_note_chapters(note_section)

    return {
        "raw_html": raw_html,
        "items": items,
        "chapters": chapters,
        "rcept_no": rcept_no,
    }


def get_cached_accounting_policy(corp_code: str, bsns_year: int, fs_div: str = "CFS") -> dict | None:
    with get_session() as session:
        rows = (
            session.query(AccountingPolicyItem)
            .filter_by(corp_code=corp_code, bsns_year=bsns_year, fs_div=fs_div)
            .order_by(AccountingPolicyItem.item_key.asc())
            .all()
        )
        if not rows:
            return None
        return {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "rcept_no": rows[0].rcept_no,
            "items": {
                row.item_key: {
                    "heading": row.heading,
                    "body": row.body,
                    "body_length": row.body_length,
                    "body_hash": row.body_hash,
                }
                for row in rows
            }
        }


def get_cached_accounting_policy(corp_code: str, bsns_year: int, fs_div: str = "CFS") -> dict | None:
    with get_session() as session:
        rows = (
            session.query(AccountingPolicyItem)
            .filter_by(corp_code=corp_code, bsns_year=bsns_year, fs_div=fs_div)
            .order_by(AccountingPolicyItem.item_key.asc())
            .all()
        )
        if not rows:
            return None
        return {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "rcept_no": rows[0].rcept_no,
            "items": {
                row.item_key: {
                    "heading": row.heading,
                    "body": row.body,
                    "body_length": row.body_length,
                    "body_hash": row.body_hash,
                }
                for row in rows
            }
        }


# 토픽 chapter label → item_key 매핑. 포함 검사(in) 기준.
# 구체적인 키워드가 먼저 와야 일반 키워드 매칭을 덮어쓰지 않음.
_TOPIC_CHAPTER_MAP: list[tuple[tuple[str, ...], str]] = [
    (("재고자산",), "inventory_valuation"),
    (("유형자산",), "depreciation"),
    (("무형자산", "영업권", "개발비"), "intangible_assets"),
    (("사용권자산", "리스"), "lease"),
    (("금융상품", "금융자산", "금융부채"), "financial_instruments"),
    (("헤지", "위험회피", "파생"), "financial_instruments_hedge"),
    (("손상", "회수가능액", "현금창출단위"), "impairment"),
    (("관계기업", "공동기업", "연결"), "consolidation"),
    (("수익", "매출"), "revenue_recognition"),
    (("퇴직급여", "종업원급여", "확정급여"), "employee_benefits"),
    (("주식기준보상",), "share_based_payment"),
    (("충당부채",), "provisions"),
    (("법인세", "이연법인세"), "tax"),
    (("외화",), "foreign_currency"),
    (("건설계약", "공사계약"), "construction_contract"),
    (("보험", "IFRS 17"), "insurance_contract"),
]


def _extract_topic_chapter_items(note_section: str) -> dict[str, dict]:
    """
    note_section의 모든 챕터를 순회하며 토픽 키워드가 label에 포함된 챕터를
    item으로 추출한다. 하나의 item_key에 매칭되는 챕터가 여러 개면 첫 번째만 사용.
    """
    import re as _re

    chapters = _note_chapters(note_section)
    if not chapters:
        return {}

    items: dict[str, dict] = {}
    for label, cs, ce in chapters:
        # 챕터 크기가 너무 작으면 실제 정책이 없을 가능성 (라벨만 있는 경우)
        body_len = ce - cs
        if body_len < 300:
            continue

        # 챕터 전체 XML에서 text 추출 (body)
        xml_block = note_section[cs:ce]
        # <P> 태그 내용 모두 합침
        p_texts: list[str] = []
        for m in _re.finditer(r'<P[^>]*>(.*?)</P>', xml_block, _re.DOTALL | _re.IGNORECASE):
            inner = m.group(1)
            plain = _re.sub(r'<[^>]+>', ' ', inner)
            plain = _re.sub(r'\s+', ' ', plain).strip()
            if plain:
                p_texts.append(plain)
        if not p_texts:
            continue
        body_text = " ".join(p_texts)
        # 너무 짧으면 스킵 (정책 설명 수준이 아님)
        if len(body_text) < 200:
            continue

        # label 매칭
        for keywords, item_key in _TOPIC_CHAPTER_MAP:
            if item_key in items:
                continue  # 이미 매칭됨
            for kw in keywords:
                if kw in label:
                    items[item_key] = {
                        "heading": label.strip(),
                        "body": body_text[:2000] + ("…" if len(body_text) > 2000 else ""),
                    }
                    break

    return items


# ---------------------------------------------------------------------------
# 종속회사·지분법 회사 감사인 조회
# ---------------------------------------------------------------------------

def _normalize_corp_name(name: str) -> str:
    """DART 사업보고서 회사명을 companies 테이블 매칭용으로 정규화."""
    import re
    s = name
    # 법인형태 표기 제거
    s = re.sub(
        r'(\(주\)|㈜|주식회사|유한회사|유한책임회사|합자회사|합명회사'
        r'|Co\.,?\s*Ltd\.?|Co\.,?\s*Inc\.?|Inc\.?|Ltd\.?|Corp\.?|LLC\.?)',
        '',
        s,
        flags=re.IGNORECASE,
    )
    # 특수문자·공백 제거 후 소문자
    s = re.sub(r'[\s\-_\.\,\(\)\[\]]+', '', s)
    return s.lower()


def _all_dart_corps_normalized() -> dict[str, dict]:
    """
    DART corpCode.xml 전체 로드 → 정규화 이름 → {corp_code, corp_name, stock_code} 매핑.
    비상장 포함. 24시간 캐시.
    """
    from kreports.collector.fetcher import fetch_corp_code_xml
    corps = fetch_corp_code_xml()
    out: dict[str, dict] = {}
    for c in corps:
        key = _normalize_corp_name(c.get("corp_name", ""))
        if key and key not in out:
            out[key] = {
                "corp_code": c["corp_code"],
                "corp_name": c["corp_name"],
                "stock_code": c.get("stock_code"),
                "market": c.get("market"),
            }
    return out


def get_companies_by_names(names: list[str]) -> dict[str, dict]:
    """
    회사명 리스트로 DART 전체 기업 목록 조회 (비상장 포함).
    반환: {원본_이름: {corp_code, corp_name, stock_code, market}}
    """
    if not names:
        return {}

    all_corps = _all_dart_corps_normalized()
    result: dict[str, dict] = {}
    unmatched: list[str] = []

    # 1단계: 정규화 exact match
    for name in names:
        key = _normalize_corp_name(name)
        if key in all_corps:
            result[name] = all_corps[key]
        else:
            unmatched.append(name)

    # 2단계: 부분 일치 폴백
    for name in unmatched:
        key = _normalize_corp_name(name)
        if len(key) < 3:
            continue
        for norm_db, info in all_corps.items():
            if key in norm_db or norm_db in key:
                result[name] = info
                break

    return result


def get_subsidiaries_with_auditors(corp_code: str) -> dict:
    """
    최신 사업보고서에서 종속·지분법 회사 목록을 파싱하고
    각 회사의 감사인 정보를 DB에서 조회하여 병합한다.

    반환:
    {
        "rcept_no": str,
        "bsns_year": int | None,
        "items": [...],
        "parse_errors": [str],
    }
    """
    from kreports.collector.fetcher import fetch_document_xml
    from kreports.processor.subsidiary_parser import extract_affiliates_from_report

    result: dict = {"rcept_no": "", "bsns_year": None, "items": [], "parse_errors": []}

    # 1. 최신 사업보고서 rcept_no 조회
    with get_session() as session:
        row = (
            session.query(Disclosure.rcept_no, Disclosure.disc_date, Disclosure.report_nm)
            .filter_by(corp_code=corp_code)
            .filter(Disclosure.report_nm.like("%사업보고서%"))
            .order_by(Disclosure.disc_date.desc())
            .first()
        )
        if row is None:
            result["parse_errors"].append("DB에 사업보고서 공시가 없습니다.")
            return result
        rcept_no = row.rcept_no
        disc_date_str = str(row.disc_date)
        result["rcept_no"] = rcept_no
        try:
            result["bsns_year"] = int(disc_date_str[:4]) - 1
        except Exception:
            pass

    # 2. document.xml 가져오기
    content = fetch_document_xml(rcept_no)
    if not content:
        result["parse_errors"].append(f"document.xml 수신 실패 (rcept_no={rcept_no})")
        return result

    # 3. 파싱
    raw_items = extract_affiliates_from_report(content)
    if not raw_items:
        result["parse_errors"].append(
            "사업보고서에서 종속회사/타법인출자 테이블을 찾지 못했습니다. "
            "ACLASS 코드 또는 TITLE 키워드가 다를 수 있습니다."
        )
        return result

    # 4. 회사명으로 DB 매칭
    names = [it["name"] for it in raw_items]
    name_to_corp = get_companies_by_names(names)

    # 5. 감사인 일괄 조회
    matched_corp_codes = [v["corp_code"] for v in name_to_corp.values()]
    auditors_df = get_auditors_for_corp_codes(matched_corp_codes) if matched_corp_codes else pd.DataFrame()

    # corp_code → 최신 감사인 (CFS 우선)
    latest_map: dict[str, dict] = {}
    if not auditors_df.empty:
        cfs = auditors_df[auditors_df["fs_div"] == "CFS"]
        by_corp_cfs = (
            cfs.sort_values("회계연도", ascending=False)
            .groupby("corp_code")
            .first()
            .to_dict("index")
        )
        ofs = auditors_df[
            (auditors_df["fs_div"] == "OFS") &
            (~auditors_df["corp_code"].isin(by_corp_cfs))
        ]
        by_corp_ofs = (
            ofs.sort_values("회계연도", ascending=False)
            .groupby("corp_code")
            .first()
            .to_dict("index")
        )
        latest_map = {**by_corp_cfs, **by_corp_ofs}

    # 6. 병합
    merged: list[dict] = []
    for it in raw_items:
        corp_info = name_to_corp.get(it["name"])
        cc = corp_info["corp_code"] if corp_info else None
        aud = latest_map.get(cc) if cc else None
        # listed_yn: 'Y'=상장, 'N'=비상장, ''=미상
        listed_yn = it.get("listed_yn", "")
        # stock_code가 있으면 상장사, 없어도 corp_code 있으면 비상장이지만 DART 등록사
        stock_code = corp_info["stock_code"] if corp_info else None
        merged.append({
            "name": it["name"],
            "relation": it["relation"],
            "ownership_pct": it.get("ownership_pct"),
            "business": it.get("business", ""),
            "assets": it.get("assets", ""),
            "source": it.get("source", ""),
            "listed_yn": listed_yn,
            "corp_code": cc,
            "stock_code": stock_code,
            "market": corp_info["market"] if corp_info else None,
            "auditor": {
                "name": aud["감사인"],
                "year": aud["회계연도"],
                "opinion": aud["감사의견"],
            } if aud else None,
        })

    result["items"] = merged
    return result


def get_cached_report_section_years(
    corp_code: str,
    source_type: str,
    section_key: str | None = None,
) -> list[int]:
    """Return cached report-section years through a narrow query adapter."""
    from sqlalchemy import text

    from kreports.db.engine import engine

    where = "corp_code=:corp_code AND source_type=:source_type"
    params: dict[str, object] = {
        "corp_code": corp_code,
        "source_type": source_type,
    }
    if section_key:
        where += " AND section_key=:section_key"
        params["section_key"] = section_key
    with engine.connect() as conn:
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
    return [int(year) for year in rows]
