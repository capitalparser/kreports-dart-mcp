"""
Beneish M-Score 계산 (연간 Q4 전용).

M = -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI
         + 0.892*SGI  + 0.115*DEPI - 0.172*SGAI
         + 4.679*TATA - 0.327*LVGI

M > -1.78 → 이익 조작 가능성 (Beneish 1999)

모든 인덱스는 FinancialFact의 thstrm(당기) / frmtrm(전기) 금액을 사용.
TATA만 Financial.operating_cf(compute_cf_flags 이후 설정)를 활용.
"""
import logging
from kreports.db.engine import get_session
from kreports.db.models import Financial, FinancialFact

logger = logging.getLogger(__name__)

BENEISH_THRESHOLD = -1.78
_REPRT_CODE_ANNUAL = "11011"

# XBRL 계정 ID 우선순위 (앞에서부터 첫 매칭 사용)
_XBRL_IDS: dict[str, list[str]] = {
    "revenue": [
        "ifrs-full_Revenue",
        "dart_Revenue",
        "ifrs-full_RevenueFromContractsWithCustomers",
        "dart_TotalRevenue",
    ],
    "cogs": [
        "ifrs-full_CostOfSales",
        "dart_CostOfSales",
    ],
    "gross_profit": [
        "ifrs-full_GrossProfit",
        "dart_GrossProfit",
    ],
    "receivables": [
        "ifrs-full_TradeAndOtherCurrentReceivables",
        "ifrs-full_CurrentTradeReceivables",
        "dart_TradeAndOtherReceivables",
        "ifrs-full_TradeReceivables",
    ],
    "total_assets": [
        "ifrs-full_Assets",
        "dart_Assets",
    ],
    "current_assets": [
        "ifrs-full_CurrentAssets",
        "dart_CurrentAssets",
    ],
    "ppe": [
        "ifrs-full_PropertyPlantAndEquipment",
        "dart_PropertyPlantAndEquipment",
    ],
    "depreciation": [
        "ifrs-full_DepreciationAndAmortisationExpense",
        "dart_DepreciationAndAmortization",
        "ifrs-full_DepreciationAmortisationAndImpairmentLoss",
    ],
    "sga": [
        "dart_SellingExpensesAndAdministrativeExpenses",
        "ifrs-full_SellingGeneralAndAdministrativeExpense",
        "dart_SellingExpenses",
        "dart_GeneralAndAdministrativeExpenses",
    ],
    "lt_debt": [
        "ifrs-full_NoncurrentBorrowings",
        "dart_LongTermBorrowings",
        "ifrs-full_NoncurrentPortionOfLongtermBorrowings",
    ],
    "current_liabilities": [
        "ifrs-full_CurrentLiabilities",
        "dart_CurrentLiabilities",
    ],
}

# account_nm 폴백 (정확 일치)
_ACCOUNT_NMS: dict[str, list[str]] = {
    "revenue": ["매출액", "영업수익", "수익(매출액)", "매출"],
    "cogs": ["매출원가", "영업비용"],
    "gross_profit": ["매출총이익", "영업총이익"],
    "receivables": ["매출채권", "매출채권 및 기타채권", "매출채권 및 기타유동채권"],
    "total_assets": ["자산총계"],
    "current_assets": ["유동자산"],
    "ppe": ["유형자산"],
    "depreciation": ["감가상각비", "감가상각비 및 무형자산상각비"],
    "sga": ["판매비와관리비", "판매비 및 관리비"],
    "lt_debt": ["장기차입금", "비유동성 차입금"],
    "current_liabilities": ["유동부채"],
}


def compute_beneish(corp_code: str, year: int, fs_div: str) -> None:
    """
    연간(Q4) FinancialFact 데이터로 Beneish M-Score를 계산하고 Financial에 저장한다.
    compute_cf_flags() 이후에 호출해야 operating_cf가 설정된 상태다.
    """
    with get_session() as session:
        facts = session.query(FinancialFact).filter_by(
            corp_code=corp_code,
            bsns_year=year,
            reprt_code=_REPRT_CODE_ANNUAL,
            fs_div=fs_div,
        ).all()

        if not facts:
            return

        # 인덱스 구성: account_id → fact, account_nm → fact (첫 매칭)
        by_id: dict[str, FinancialFact] = {}
        by_nm: dict[str, FinancialFact] = {}
        for f in facts:
            if f.account_id not in by_id:
                by_id[f.account_id] = f
            nm = f.account_nm.strip()
            if nm not in by_nm:
                by_nm[nm] = f

        def _get(key: str) -> tuple[int | None, int | None]:
            """(당기, 전기) 반환. account_id → account_nm 순 우선순위."""
            for aid in _XBRL_IDS.get(key, []):
                if aid in by_id:
                    f = by_id[aid]
                    return f.thstrm_amount, f.frmtrm_amount
            for nm in _ACCOUNT_NMS.get(key, []):
                if nm in by_nm:
                    f = by_nm[nm]
                    return f.thstrm_amount, f.frmtrm_amount
            return None, None

        fin = session.query(Financial).filter_by(
            corp_code=corp_code, year=year, quarter=4, fs_div=fs_div,
        ).first()
        if fin is None:
            return

        # ── 컴포넌트 추출 ──────────────────────────────────────────────────
        rev_t, rev_p = _get("revenue")
        cogs_t, cogs_p = _get("cogs")
        gp_t, gp_p = _get("gross_profit")
        recv_t, recv_p = _get("receivables")
        ta_t, ta_p = _get("total_assets")
        ca_t, ca_p = _get("current_assets")
        ppe_t, ppe_p = _get("ppe")
        depr_t, depr_p = _get("depreciation")
        sga_t, sga_p = _get("sga")
        ltd_t, ltd_p = _get("lt_debt")
        cl_t, cl_p = _get("current_liabilities")

        # Financial 요약값으로 보완
        if rev_t is None:
            rev_t = fin.revenue
        if ta_t is None:
            ta_t = fin.total_assets

        # gross_profit: 직접 없으면 revenue - cogs로 계산
        if gp_t is None and rev_t is not None and cogs_t is not None:
            gp_t = rev_t - cogs_t
        if gp_p is None and rev_p is not None and cogs_p is not None:
            gp_p = rev_p - cogs_p

        operating_cf = fin.operating_cf
        net_income_t = fin.net_income

        # ── 각 인덱스 계산 ──────────────────────────────────────────────────
        dsri = gmi = aqi = sgi = depi = sgai = lvgi = tata = None

        try:
            # DSRI: (Recv_t/Rev_t) / (Recv_p/Rev_p)
            if recv_t and rev_t and recv_p and rev_p:
                dsri = round((recv_t / rev_t) / (recv_p / rev_p), 4)

            # GMI: GrossMargin_p / GrossMargin_t  (>1 = 악화)
            if gp_t is not None and rev_t and gp_p is not None and rev_p:
                gm_t = gp_t / rev_t
                if gm_t != 0:
                    gmi = round((gp_p / rev_p) / gm_t, 4)

            # AQI: (1 - (PPE_t+CA_t)/TA_t) / (1 - (PPE_p+CA_p)/TA_p)
            if all(v is not None for v in [ca_t, ppe_t, ta_t, ca_p, ppe_p, ta_p]):
                aq_t = 1 - (ppe_t + ca_t) / ta_t
                aq_p = 1 - (ppe_p + ca_p) / ta_p
                if aq_p != 0:
                    aqi = round(aq_t / aq_p, 4)

            # SGI: Rev_t / Rev_p
            if rev_t and rev_p:
                sgi = round(rev_t / rev_p, 4)

            # DEPI: (Depr_p/(PPE_p+Depr_p)) / (Depr_t/(PPE_t+Depr_t))
            if all(v is not None and v > 0 for v in [depr_t, ppe_t, depr_p, ppe_p]):
                rate_t = depr_t / (ppe_t + depr_t)
                rate_p = depr_p / (ppe_p + depr_p)
                if rate_t != 0:
                    depi = round(rate_p / rate_t, 4)

            # SGAI: (SGA_t/Rev_t) / (SGA_p/Rev_p)
            if sga_t is not None and rev_t and sga_p is not None and rev_p:
                if sga_p / rev_p != 0:
                    sgai = round((sga_t / rev_t) / (sga_p / rev_p), 4)

            # LVGI: ((LTD_t+CL_t)/TA_t) / ((LTD_p+CL_p)/TA_p)
            if all(v is not None for v in [ltd_t, cl_t, ta_t, ltd_p, cl_p, ta_p]):
                lev_p = (ltd_p + cl_p) / ta_p
                if lev_p != 0:
                    lvgi = round(((ltd_t + cl_t) / ta_t) / lev_p, 4)

            # TATA: (NetIncome_t - CFO_t) / TA_t
            if net_income_t is not None and operating_cf is not None and ta_t:
                tata = round((net_income_t - operating_cf) / ta_t, 4)

        except (ZeroDivisionError, TypeError):
            pass

        # ── M-Score 계산 ──────────────────────────────────────────────────
        # 최소 5개 컴포넌트 필요 (단, TATA가 None이면 계산 생략)
        coeffs = {
            "dsri": (dsri, 0.920),
            "gmi":  (gmi,  0.528),
            "aqi":  (aqi,  0.404),
            "sgi":  (sgi,  0.892),
            "depi": (depi, 0.115),
            "sgai": (sgai, -0.172),
            "tata": (tata, 4.679),
            "lvgi": (lvgi, -0.327),
        }
        available = {k: (v, c) for k, (v, c) in coeffs.items() if v is not None}

        m_score = None
        beneish_flag_val = None
        if len(available) >= 5:
            m_score = round(-4.84 + sum(v * c for v, c in available.values()), 4)
            beneish_flag_val = bool(m_score > BENEISH_THRESHOLD)

        # ── Financial 저장 ──────────────────────────────────────────────────
        fin.beneish_dsri = dsri
        fin.beneish_gmi = gmi
        fin.beneish_aqi = aqi
        fin.beneish_sgi = sgi
        fin.beneish_depi = depi
        fin.beneish_sgai = sgai
        fin.beneish_lvgi = lvgi
        fin.beneish_tata = tata
        fin.beneish_m_score = m_score
        fin.beneish_flag = beneish_flag_val

        logger.debug(
            "Beneish [%s %s Q4 %s]: M=%.3f (%d/8 컴포넌트) flag=%s",
            corp_code, year, fs_div,
            m_score if m_score is not None else float("nan"),
            len(available),
            beneish_flag_val,
        )


def compute_all_beneish(corp_code: str) -> int:
    """corp_code 전체 연도의 Beneish M-Score를 일괄 계산한다."""
    with get_session() as session:
        years_divs = (
            session.query(Financial.year, Financial.fs_div)
            .filter_by(corp_code=corp_code, quarter=4)
            .distinct()
            .all()
        )

    for year, fs_div in years_divs:
        compute_beneish(corp_code, year, fs_div)

    return len(years_divs)
