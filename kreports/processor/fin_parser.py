import logging
from kreports.processor.account_map import normalize_account
from kreports.semantic.metrics import financial_summary_account_map

logger = logging.getLogger(__name__)

# OpenDART financial amount strings are parsed and persisted without scaling.
# FinancialFact and Financial integer amount columns therefore store KRW.
FINANCIAL_AMOUNT_STORAGE_UNIT = "KRW"

# DART reprt_code → 분기 번호
REPRT_TO_QUARTER = {
    "11013": 1,  # 1분기보고서
    "11012": 2,  # 반기보고서
    "11014": 3,  # 3분기보고서
    "11011": 4,  # 사업보고서
}

# XBRL element ID → 요약 필드는 semantic registry에서 파생한다.
_XBRL_TO_SUMMARY: dict[str, str] = financial_summary_account_map()

# 요약 필드가 속하는 재무제표 종류
_SUMMARY_SJ = {
    "revenue": {"IS", "CIS"},
    "operating_profit": {"IS", "CIS"},
    "net_income": {"IS", "CIS"},
    "total_assets": {"BS"},
    "total_debt": {"BS"},
    "total_equity": {"BS"},
}


def _parse_amount(raw: str | None) -> int | None:
    """쉼표 제거 후 int 변환. 빈 문자열이나 '-'는 None."""
    if not raw or raw.strip() in ("", "-", "－"):
        return None
    try:
        return int(raw.replace(",", "").replace(" ", ""))
    except ValueError:
        return None


def _safe_int(val) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 상장사용: 전체 계정과목 파싱 (FinancialFact 저장용)
# ---------------------------------------------------------------------------

def parse_all_accounts(
    api_response: dict,
    corp_code: str,
    year: int,
    reprt_code: str,
    fs_div: str,
) -> list[dict] | None:
    """
    fnlttSinglAcntAll 응답에서 모든 계정과목 행을 XBRL 구조로 반환한다.
    상장사 FinancialFact 테이블 저장용.

    account_id가 없는 행은 '_nm_{account_nm}' 형태의 합성 ID를 사용한다.

    Returns:
        list[dict] 또는 None (데이터 없음)
    """
    if api_response.get("status") != "000":
        return None

    rows = api_response.get("list", [])
    if not rows:
        return None

    results = []
    seen_ids: set[str] = set()   # (sj_div, account_id) 중복 방지

    for row in rows:
        account_nm = (row.get("account_nm") or "").strip()
        if not account_nm:
            continue

        raw_id = (row.get("account_id") or "").strip()
        sj_div = (row.get("sj_div") or "").strip()
        # account_id 없으면 합성 키 생성
        account_id = raw_id if raw_id else f"_nm_{account_nm}"

        # 동일 (sj_div, account_id) 중복 시 첫 번째만 저장
        dedup_key = (sj_div, account_id)
        if dedup_key in seen_ids:
            continue
        seen_ids.add(dedup_key)

        results.append({
            "corp_code": corp_code,
            "bsns_year": year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
            "sj_div": sj_div,
            "account_id": account_id,
            "account_nm": account_nm,
            "ord": _safe_int(row.get("ord")),
            "thstrm_amount": _parse_amount(row.get("thstrm_amount")),
            "frmtrm_amount": _parse_amount(row.get("frmtrm_amount")),
            "bfefrmtrm_amount": _parse_amount(row.get("bfefrmtrm_amount")),
            "thstrm_add_amount": _parse_amount(row.get("thstrm_add_amount")),
        })

    return results if results else None


def compute_summary_from_facts(
    facts: list[dict],
    corp_code: str,
    year: int,
    reprt_code: str,
    fs_div: str,
) -> dict:
    """
    parse_all_accounts() 결과에서 Financial(6개 요약) 값을 계산한다.
    XBRL element ID 우선, 없으면 계정명 매핑 폴백.
    """
    quarter = REPRT_TO_QUARTER.get(reprt_code, 0)
    result: dict[str, int | None] = {
        "revenue": None,
        "operating_profit": None,
        "net_income": None,
        "total_assets": None,
        "total_debt": None,
        "total_equity": None,
    }
    filled: set[str] = set()

    # 1차: XBRL element ID로 매핑
    for fact in facts:
        aid = fact.get("account_id", "")
        sj = fact.get("sj_div", "")
        field = _XBRL_TO_SUMMARY.get(aid)
        if not field or field in filled:
            continue
        # 재무제표 종류 일치 확인
        if sj and _SUMMARY_SJ.get(field) and sj not in _SUMMARY_SJ[field]:
            continue
        if fact.get("thstrm_amount") is not None:
            result[field] = fact["thstrm_amount"]
            filled.add(field)

    # 2차: account_nm 폴백 (비표준 element를 쓰는 기업 대응)
    if len(filled) < 6:
        BS_KEYS = {"total_assets", "total_debt", "total_equity"}
        IS_KEYS = {"revenue", "operating_profit", "net_income"}
        for fact in facts:
            nm = fact.get("account_nm", "")
            sj = fact.get("sj_div", "")
            field = normalize_account(nm)
            if not field or field in filled:
                continue
            if field in BS_KEYS and sj not in ("BS", ""):
                continue
            if field in IS_KEYS and sj not in ("IS", "CIS", ""):
                continue
            if fact.get("thstrm_amount") is not None:
                result[field] = fact["thstrm_amount"]
                filled.add(field)

    core_fields = set(result.keys())
    confidence = round(len(filled & core_fields) / len(core_fields), 3)

    return {
        "corp_code": corp_code,
        "year": year,
        "quarter": quarter,
        "fs_div": fs_div,
        **result,
        "account_map_confidence": confidence,
        "cfs_ofs_gap_flag": None,
        "accrual_ratio": None,
        "op_cf_divergence_flag": None,
    }


# ---------------------------------------------------------------------------
# 비상장사용: 6개 요약 계정만 파싱 (기존 로직 유지)
# ---------------------------------------------------------------------------

def parse_financials(
    api_response: dict,
    corp_code: str,
    year: int,
    reprt_code: str,
    fs_div: str,
) -> dict | None:
    """
    fnlttSinglAcntAll 응답에서 핵심 6개 재무지표를 추출한다.
    비상장사(감사보고서만 있는 기업) 대상.

    Returns:
        {corp_code, year, quarter, fs_div, revenue, ...} 또는 None
    """
    if api_response.get("status") != "000":
        return None

    rows = api_response.get("list", [])
    if not rows:
        return None

    # parse_all_accounts → compute_summary_from_facts 경로로 처리
    all_facts = parse_all_accounts(api_response, corp_code, year, reprt_code, fs_div)
    if not all_facts:
        return None

    summary = compute_summary_from_facts(all_facts, corp_code, year, reprt_code, fs_div)
    if all(summary.get(k) is None for k in
           ["revenue", "operating_profit", "net_income", "total_assets", "total_debt", "total_equity"]):
        return None

    return summary


# ---------------------------------------------------------------------------
# fnlttSinglAcnt 폴백: 주요계정 요약 응답 파싱
# ---------------------------------------------------------------------------

_CORE_FIELDS = ("revenue", "operating_profit", "net_income",
                "total_assets", "total_debt", "total_equity")
_BS_FIELDS = {"total_assets", "total_debt", "total_equity"}
_IS_FIELDS = {"revenue", "operating_profit", "net_income"}


def parse_summary_response(
    api_response: dict,
    corp_code: str,
    year: int,
    reprt_code: str,
    fs_div: str,
) -> dict | None:
    """
    fnlttSinglAcnt (주요계정) 응답에서 핵심 6개 재무지표를 추출한다.

    fnlttSinglAcntAll와 달리 account_id 없음 → account_nm 별칭 매칭만 사용.
    sj_div로 BS/IS/CIS 구분하여 동명 계정 오매칭(예: '자본총계' IS행)을 방지.

    Returns:
        {corp_code, year, quarter, fs_div, revenue, ...,
         account_map_confidence, source='acnt'} 또는 None
    """
    if api_response.get("status") != "000":
        return None

    rows = api_response.get("list", [])
    if not rows:
        return None

    quarter = REPRT_TO_QUARTER.get(reprt_code, 0)
    result: dict = {field: None for field in _CORE_FIELDS}
    filled: set[str] = set()

    for row in rows:
        nm = (row.get("account_nm") or "").strip()
        sj = (row.get("sj_div") or "").strip()
        field = normalize_account(nm)
        if not field or field in filled:
            continue
        # sj_div 일치 강제 (acnt 응답은 sj_div 비어있지 않음)
        if field in _BS_FIELDS and sj not in ("BS", ""):
            continue
        if field in _IS_FIELDS and sj not in ("IS", "CIS", ""):
            continue
        amount = _parse_amount(row.get("thstrm_amount"))
        if amount is not None:
            result[field] = amount
            filled.add(field)

    if not filled:
        return None

    confidence = round(len(filled) / len(_CORE_FIELDS), 3)

    return {
        "corp_code": corp_code,
        "year": year,
        "quarter": quarter,
        "fs_div": fs_div,
        **result,
        "account_map_confidence": confidence,
        "cfs_ofs_gap_flag": None,
        "accrual_ratio": None,
        "op_cf_divergence_flag": None,
        "source": "acnt",
    }
