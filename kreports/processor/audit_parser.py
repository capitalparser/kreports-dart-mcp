"""
감사보고서 공시에서 감사인 정보를 추출·정규화한다.
"""
import re

# 감사보고서 판별 키워드
_AUDIT_KEYWORDS = ("감사보고서", "감사인의 감사보고서", "내부회계관리제도 감사보고서")

# 사업보고서 판별 키워드 (상장사 연간 보고서)
_ANNUAL_KEYWORDS = ("사업보고서",)

# 감사의견 키워드
_OPINION_KEYWORDS = ("적정", "한정", "부적정", "의견거절")

# 감사인명 표준화 매핑 (알려진 별칭 → 표준명)
_AUDITOR_ALIASES: dict[str, str] = {
    "삼일": "삼일회계법인",
    "삼일피더블유씨": "삼일회계법인",
    "pwc삼일": "삼일회계법인",
    "삼정": "삼정회계법인",
    "삼정kpmg": "삼정회계법인",
    "kpmg삼정": "삼정회계법인",
    "안진": "안진회계법인",
    "딜로이트안진": "안진회계법인",
    "한영": "한영회계법인",
    "ey한영": "한영회계법인",
    "대주": "대주회계법인",
    "신한": "신한회계법인",
    "태성": "태성회계법인",
    "삼화": "삼화회계법인",
    "이촌": "이촌회계법인",
    "세일": "세일회계법인",
}


def is_audit_report(report_nm: str) -> bool:
    """공시 제목이 감사보고서인지 판별한다."""
    return any(kw in report_nm for kw in _AUDIT_KEYWORDS)


def is_annual_report(report_nm: str) -> bool:
    """공시 제목이 사업보고서인지 판별한다 (상장사 연간보고서)."""
    return any(kw in report_nm for kw in _ANNUAL_KEYWORDS)


def normalize_auditor_name(raw_nm: str) -> str:
    """감사인명 별칭을 표준명으로 변환한다."""
    # DART XML 커스텀 엔티티 및 공백 제거
    cleaned = re.sub(r'&[a-z]+;|\s+', '', raw_nm).strip()
    key = cleaned.lower()
    return _AUDITOR_ALIASES.get(key, cleaned)


def parse_bsns_year(report_nm: str, rcept_dt: str) -> int | None:
    """
    감사보고서 제목 또는 접수일에서 회계연도를 추정한다.

    패턴:
      - "2024년도 감사보고서" → 2024
      - "2024.12 감사보고서" → 2024
      - 제목에서 추출 불가 → 접수월이 1~4월이면 year-1, 나머지는 year
    """
    # 제목에서 연도 추출
    m = re.search(r"(20\d{2})[년\.]", report_nm)
    if m:
        return int(m.group(1))

    # 접수일 기반 추정
    try:
        year = int(rcept_dt[:4])
        month = int(rcept_dt[4:6])
        return year - 1 if month <= 4 else year
    except (ValueError, IndexError):
        return None


def parse_fs_div(report_nm: str) -> str:
    """제목에서 연결/별도 구분을 판별한다. 기본 OFS."""
    return "CFS" if "연결" in report_nm else "OFS"


# ---------------------------------------------------------------------------
# 사업보고서 document.xml 파싱 (ACLASS="AUD_OPN" 테이블 기반)
# ---------------------------------------------------------------------------

# AUD_OPN 테이블 전체 추출
_AUD_OPN_TABLE_RE = re.compile(
    r'ACLASS="AUD_OPN".*?</TABLE-GROUP>', re.DOTALL
)

# 신형식: OPN_AUR1_C (CFS), OPN_AUR1_A (OFS) — 2024 이후
_AUD_NM_NEW_RE = re.compile(r'ACODE="OPN_AUR1_([AC])"[^>]*>([^<\n]+)</TE>')
# 신형식 감사의견 (AUNITVALUE)
_AUD_OPINION_NEW_RE = re.compile(r'AUNIT="OPN_CMT1_([AC])"[^>]*AUNITVALUE="(\d)"')

# 구형식: OPN_AUR1 (단일 통합 보고서) — 2023 이전
_AUD_NM_OLD_RE = re.compile(r'ACODE="OPN_AUR\d+"[^>]*>([^<\n]+)</TE>')
_AUD_OPINION_OLD_RE = re.compile(r'ACODE="OPN_CMT\d+"[^>]*>([^<\n]+)</TE>')

_UNITVALUE_TO_OPINION = {"1": "적정", "2": "한정", "3": "부적정", "4": "의견거절"}
_OPINION_CLEAN_RE = re.compile(r'적정의견|한정의견|부적정의견|의견거절|적정|한정|부적정')
_OPINION_NORMALIZE = {
    "적정의견": "적정", "한정의견": "한정", "부적정의견": "부적정",
    "적정": "적정", "한정": "한정", "부적정": "부적정", "의견거절": "의견거절",
}


def parse_auditor_from_doc_xml(content: str) -> list[dict]:
    """
    사업보고서 document.xml 원문에서 감사인 정보를 추출한다.
    ACLASS="AUD_OPN" 테이블의 ACODE 속성을 파싱한다.

    신형식 (2024~): OPN_AUR1_A (OFS) / OPN_AUR1_C (CFS) + AUNITVALUE 기반 의견
    구형식 (~2023): OPN_AUR1 (통합) + OPN_CMT1 텍스트 기반 의견

    Returns:
        [{"fs_div": "CFS"|"OFS", "auditor_nm": str, "audit_opinion": str|None}, ...]
    """
    results: list[dict] = []

    # AUD_OPN 테이블 섹션 추출
    m_table = _AUD_OPN_TABLE_RE.search(content)
    if not m_table:
        return results
    table = m_table.group(0)

    # ── 신형식 파싱 (_A/_C suffix 있는 경우) ─────────────────────────────
    auditors_new: dict[str, str] = {}
    for m in _AUD_NM_NEW_RE.finditer(table):
        suffix, nm = m.group(1), m.group(2).strip()
        fs_div = "CFS" if suffix == "C" else "OFS"
        if nm and fs_div not in auditors_new:
            auditors_new[fs_div] = normalize_auditor_name(nm)

    if auditors_new:
        opinions: dict[str, str | None] = {}
        for m in _AUD_OPINION_NEW_RE.finditer(table):
            suffix, val = m.group(1), m.group(2)
            fs_div = "CFS" if suffix == "C" else "OFS"
            opinions[fs_div] = _UNITVALUE_TO_OPINION.get(val)
        for fs_div, auditor_nm in auditors_new.items():
            results.append({
                "fs_div": fs_div,
                "auditor_nm": auditor_nm,
                "audit_opinion": opinions.get(fs_div),
            })
        return results

    # ── 구형식 파싱 (suffix 없는 단일 감사인) ────────────────────────────
    nm_matches = list(_AUD_NM_OLD_RE.finditer(table))
    if not nm_matches:
        return results

    auditor_nm = normalize_auditor_name(nm_matches[0].group(1).strip())
    opinion: str | None = None
    op_matches = list(_AUD_OPINION_OLD_RE.finditer(table))
    if op_matches:
        raw_op = op_matches[0].group(1).strip()
        m_op = _OPINION_CLEAN_RE.match(raw_op)
        if m_op:
            opinion = _OPINION_NORMALIZE.get(m_op.group(0))

    # 구형식은 연결/별도 구분 없이 CFS로 저장 (사업보고서 = 연결 포함)
    results.append({"fs_div": "CFS", "auditor_nm": auditor_nm, "audit_opinion": opinion})
    return results
