# 계정과목명 별칭 매핑 테이블
# DART API 응답의 account_nm은 기업마다 표기가 다르다.
# 각 표준 항목에 대해 알려진 별칭을 등록한다.

ACCOUNT_ALIASES: dict[str, list[str]] = {
    "revenue": [
        "매출액", "영업수익", "수익(매출액)", "매출", "순매출액",
        "영업수익(매출액)", "수입금액", "매출수익", "영업수입",
        "수익", "영업매출액",
    ],
    "operating_profit": [
        "영업이익", "영업이익(손실)", "영업손익", "영업이익(영업손실)",
    ],
    "net_income": [
        "당기순이익", "당기순이익(손실)", "분기순이익", "반기순이익",
        "당기순손익", "지배기업 소유주 귀속 당기순이익",
        "당기순이익(당기순손실)", "연결당기순이익",
    ],
    "total_assets": [
        "자산총계", "자산 총계",
    ],
    "total_debt": [
        "부채총계", "부채 총계",
    ],
    "total_equity": [
        "자본총계", "자본 총계", "지배기업 소유주지분",
        "자본합계",
    ],
}

# 역방향 인덱스: account_nm → 표준 항목명
_REVERSE: dict[str, str] = {
    alias.strip(): key
    for key, aliases in ACCOUNT_ALIASES.items()
    for alias in aliases
}


def normalize_account(account_nm: str) -> str | None:
    """
    DART account_nm을 표준 항목명으로 변환한다.
    매핑되지 않으면 None 반환.
    """
    return _REVERSE.get(account_nm.strip())
