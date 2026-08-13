from datetime import date

# report_nm 키워드 기반 공시유형 분류
# DART list API 응답 항목에는 pblntf_ty가 포함되지 않아 report_nm으로 추론한다.
_TYPE_RULES: list[tuple[str, list[str]]] = [
    ("A", ["사업보고서", "분기보고서", "반기보고서", "소액공모법인결산서류"]),
    ("F", ["감사보고서", "내부회계관리제도"]),
    ("B", ["주요사항보고서"]),
    ("C", ["증권신고서", "투자설명서", "소액공모", "모집공고"]),
    ("D", ["임원ㆍ주요주주특정증권", "주식등의대량보유", "최대주주등소유주식", "자기주식"]),
    ("H", ["유동화", "자산유동화"]),
    ("J", ["공정공시"]),
    ("I", ["조회공시", "풍문또는보도"]),
    ("G", ["집합투자기구", "펀드"]),
]


def classify_disc_type(report_nm: str) -> str:
    """report_nm 키워드로 공시유형(A~J)을 분류한다. 해당 없으면 'E'(기타)."""
    for disc_type, keywords in _TYPE_RULES:
        if any(kw in report_nm for kw in keywords):
            return disc_type
    return "E"


def parse_disclosure(raw: dict) -> dict | None:
    """
    DART 공시 목록 API 응답 항목을 정규화된 dict로 변환한다.

    Note: DART list API 응답 항목에 pblntf_ty가 없으므로 report_nm 기반으로 분류한다.

    Returns:
        {"rcept_no", "corp_code", "corp_name", "disc_date", "disc_type", "report_nm", "flr_nm"}
        rcept_no 또는 corp_code가 없으면 None
    """
    rcept_no = raw.get("rcept_no", "").strip()
    corp_code = raw.get("corp_code", "").strip()
    if not rcept_no or not corp_code:
        return None

    raw_date = raw.get("rcept_dt", "")
    try:
        disc_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:8]))
    except (ValueError, IndexError):
        disc_date = None

    report_nm = raw.get("report_nm", "").strip()
    return {
        "rcept_no": rcept_no,
        "corp_code": corp_code,
        "corp_name": raw.get("corp_name", "").strip(),
        "disc_date": disc_date,
        "disc_type": classify_disc_type(report_nm),
        "report_nm": report_nm,
        "flr_nm": raw.get("flr_nm", "").strip() or None,
    }
