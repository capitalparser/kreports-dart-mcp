"""
회계정책 주석 파서.

사업보고서 별첨 재무제표 XML에서 '중요한 회계정책' 섹션을 추출하고,
항목별로 분리하여 item_key 기준 dict로 반환한다.
"""
import re
from typing import Optional

# ---------------------------------------------------------------------------
# 회계정책 TITLE 검색 키워드
# ---------------------------------------------------------------------------

_POLICY_TITLE_KEYWORDS = [
    "중요한 회계정책",
    "유의적인 회계정책",
    "회계정책의 요약",
    "유의적 회계정책",
    "중요 회계정책",
    "회계정책",           # 가장 범용 — 마지막에 배치
]

# ---------------------------------------------------------------------------
# item_key → 매칭 키워드 목록
# ---------------------------------------------------------------------------

POLICY_KEYWORDS: dict[str, list[str]] = {
    "revenue_recognition": [
        "수익인식", "수익의 인식", "고객과의 계약", "수행의무", "거래가격",
        "매출인식", "수익 인식",
    ],
    "inventory_valuation": [
        "재고자산", "재고자산의 평가", "순실현가능가치", "저가법",
        "선입선출", "가중평균", "재고평가",
    ],
    "depreciation": [
        "유형자산", "감가상각", "내용연수", "잔존가치", "정액법", "정률법",
        "감가상각방법", "상각방법",
    ],
    "intangible_assets": [
        "무형자산", "개발비", "영업권", "산업재산권", "소프트웨어",
        "내부적으로 창출", "자본화", "연구비",
    ],
    "financial_instruments": [
        "금융상품", "금융자산", "금융부채", "공정가치", "상각후원가",
        "대손충당금", "신용손실", "기대신용손실", "ECL",
        "당기손익-공정가치", "기타포괄손익-공정가치", "FVTPL", "FVOCI",
    ],
    "lease": [
        "리스", "사용권자산", "리스부채", "운용리스", "금융리스",
        "IFRS 16", "K-IFRS 제1116호",
    ],
    "provisions": [
        "충당부채", "우발부채", "우발자산", "복구충당", "제품보증",
        "손실충당", "환경충당",
    ],
    "impairment": [
        "자산손상", "손상차손", "손상검사", "현금창출단위",
        "회수가능액", "사용가치", "처분부대원가",
    ],
    "consolidation": [
        "종속기업", "관계기업", "공동기업", "연결", "지배력",
        "비지배지분", "취득법", "사업결합",
    ],
    "employee_benefits": [
        "종업원급여", "퇴직급여", "확정급여", "확정기여", "보험수리",
        "순확정급여부채", "주식기준보상",
    ],
    "foreign_currency": [
        "외화환산", "외화거래", "환율", "기능통화", "표시통화",
        "환산차이", "외화환산손익",
    ],
    "construction_contract": [
        "건설계약", "진행기준", "투입법", "산출법", "공사손실충당",
        "진행률", "계약수익", "계약원가",
    ],
    "financial_instruments_hedge": [
        "헤지회계", "위험회피", "공정가치위험회피", "현금흐름위험회피",
        "파생상품", "헤지수단", "피헤지항목",
    ],
    "tax": [
        "법인세", "이연법인세", "당기법인세", "일시적차이",
        "미인식이연법인세", "세율",
    ],
    "insurance_contract": [
        "보험계약", "IFRS 17", "K-IFRS 제1117호", "보험부채",
        "계약서비스마진", "보험수익", "최선추정부채",
    ],
}

# ---------------------------------------------------------------------------
# 소제목 패턴 (항목 경계 분리용)
# ---------------------------------------------------------------------------

# (1), ①, 1), ㄱ), ①, 가. 등 번호/기호 소제목
_SUBHEADING_RE = re.compile(
    r'(?:^|\n)'                         # 줄 시작
    r'(?:'
    r'\(\d+\)'                          # (1)
    r'|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]'  # 원문자
    r'|\d+\)'                           # 1)
    r'|[가-힣]\.'                       # 가.
    r'|\d+\.'                           # 1.
    r')'
    r'\s*',
    re.MULTILINE,
)

# 볼드/강조 소제목 태그 — HTML 변환 후 기준
_BOLD_HEADING_RE = re.compile(
    r'<span[^>]*>([^<]{3,60})</span>',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def extract_policy_section(note_content: str) -> Optional[str]:
    """
    DART 사업보고서 주석 XML에서 '중요한 회계정책' TITLE 블록을 추출한다.

    `get_notes_for_account()`의 TITLE 검색 로직을 재사용.
    회계정책 TITLE 발견 지점부터 다음 번호 주석 TITLE까지를 반환한다.

    Args:
        note_content: 주석 섹션 원문 XML (get_notes_for_account의 note_section 수준)

    Returns:
        회계정책 섹션 XML 문자열 또는 None
    """
    _title_re = re.compile(r'<TITLE[^>]*>([^<]{1,200})</TITLE>')

    # 정책 TITLE 검색 — 우선순위 순
    policy_title_pos: Optional[int] = None
    for kw in _POLICY_TITLE_KEYWORDS:
        for m in _title_re.finditer(note_content):
            if kw in m.group(1):
                policy_title_pos = m.start()
                break
        if policy_title_pos is not None:
            break

    if policy_title_pos is None:
        return None

    # 다음 번호 주석 TITLE (숫자 시작 or "3. xxx", "4. xxx" 패턴) 검색
    # 회계정책 이후 첫 번째 TITLE을 블록 끝으로 사용
    _next_title_re = re.compile(r'<TITLE[^>]*>[^<]{1,200}</TITLE>')
    search_from = policy_title_pos + 100  # 정책 TITLE 자체 제외

    next_title = _next_title_re.search(note_content, search_from)

    # 회계정책 블록은 최대 80,000자 (대기업 주석 고려)
    if next_title:
        section_end = min(next_title.start(), policy_title_pos + 80_000)
    else:
        section_end = min(len(note_content), policy_title_pos + 80_000)

    return note_content[policy_title_pos:section_end]


def extract_policy_items(policy_text: str) -> dict[str, str]:
    """
    회계정책 섹션 텍스트(XML 또는 HTML)에서 item_key별 발췌문을 추출한다.

    소제목 패턴((1), ①, 볼드 태그)을 기준으로 항목을 분리한 후,
    POLICY_KEYWORDS 매칭으로 item_key를 식별한다.

    Args:
        policy_text: extract_policy_section() 반환값 (XML or _xml_to_html() 후 HTML)

    Returns:
        {item_key: 발췌 텍스트(최대 800자)} — 매칭된 항목만 포함
    """
    # XML 태그 제거 후 텍스트 분석 (TITLE 태그 등 제거)
    plain = re.sub(r'<[^>]+>', ' ', policy_text)
    plain = re.sub(r'[ \t]{2,}', ' ', plain)
    plain = re.sub(r'\n{3,}', '\n\n', plain)

    # 소제목 경계로 분할
    segments = _split_by_subheadings(plain)

    result: dict[str, str] = {}
    for seg in segments:
        seg_stripped = seg.strip()
        if len(seg_stripped) < 20:
            continue
        matched_key = _match_item_key(seg_stripped)
        if matched_key and matched_key not in result:
            # 발췌 최대 600자
            excerpt = seg_stripped[:600]
            if len(seg_stripped) > 600:
                excerpt += "…"
            result[matched_key] = excerpt

    return result


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _split_by_subheadings(text: str) -> list[str]:
    """소제목 경계로 텍스트를 분할한다."""
    # TITLE 태그 텍스트 위치 수집
    positions = [0]
    for m in _SUBHEADING_RE.finditer(text):
        pos = m.start()
        if pos > 0:
            positions.append(pos)

    # 위치가 너무 적으면 전체를 하나로
    if len(positions) <= 1:
        return [text]

    segments = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        segments.append(text[start:end])
    return segments


def _match_item_key(text: str) -> Optional[str]:
    """텍스트에서 POLICY_KEYWORDS 매칭으로 item_key를 반환한다."""
    text_lower = text  # 한국어는 대소문자 무관
    for key, keywords in POLICY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return key
    return None
