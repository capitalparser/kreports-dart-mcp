"""
사업보고서 본문 섹션 파서.

DART 사업보고서 ZIP 내 본문 XML에서 핵심 경영 정보 섹션을 추출한다.
회계정책(policy_parser.py)이 주석 XML을 파싱하는 것과 달리,
이 모듈은 본문(사업의 개요, 사업의 내용, 위험관리 등)을 파싱한다.
"""
import re
from html import unescape
from typing import Optional

# ---------------------------------------------------------------------------
# 추출 대상 섹션 — section_key → TITLE 키워드(우선순위 순)
# ---------------------------------------------------------------------------

SECTION_KEYWORDS: dict[str, list[str]] = {
    "business_overview": [
        "사업의 개요",
        "사업개요",
        "회사의 개요",
        "회사 개요",
        "회사의 현황",
    ],
    "business_description": [
        "사업의 내용",
        "사업내용",
        "주요 제품 및 서비스",
        "주요 제품",
        "주요제품",
        "제품 및 서비스",
        "매출 및 수주상황",
        "매출 및 수주 상황",
        "매출에 관한 사항",
        "수주상황",
        "영업의 개황",
        "주요 사업 내용",
    ],
    "risk_management": [
        "위험관리 및 파생상품",
        "시장위험과 위험관리",
        "시장위험과위험관리",
        "시장위험 및 위험관리",
        "위험관리",
        "재무위험관리",
        "사업위험",
        "파생상품 및 풋백옵션",
        "파생상품",
    ],
    "management_plan": [
        "경영진의 경영계획",
        "경영진단 및 분석의견",
        "이사의 경영진단",
        "향후 추진계획",
        "향후 추진 계획",
        "경영방침",
        "중장기 전략",
        "전략 및 전망",
    ],
    "rd_activities": [
        "연구개발활동",
        "연구개발 활동",
        "연구 및 개발활동",
        "연구개발비용",
        "연구개발비",
        "연구개발 실적",
        "연구개발 담당조직",
        "기술개발",
    ],
    "key_contracts": [
        "경영상의 주요계약",
        "경영상의 주요 계약",
        "주요 계약",
        "주요계약",
        "중요한 계약",
        "주요한 계약",
        "라이선스",
        "핵심 계약",
    ],
}

# 섹션 한글 레이블
SECTION_LABELS: dict[str, str] = {
    "business_overview": "사업의 개요",
    "business_description": "사업의 내용",
    "risk_management": "위험관리",
    "management_plan": "경영진 경영계획",
    "rd_activities": "연구개발활동",
    "key_contracts": "주요 계약",
}

_TITLE_RE = re.compile(r'<TITLE[^>]*>([^<]{1,300})</TITLE>', re.IGNORECASE)
_VIEWER_ANCHOR_TITLE_RE = re.compile(
    r"<P\b[^>]*class\s*=\s*['\"]section-\d+['\"][^>]*>\s*"
    r"<A\b[^>]*>(.*?)</A>\s*</P>",
    re.IGNORECASE | re.DOTALL,
)
_HEADING_TITLE_RE = re.compile(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", re.IGNORECASE | re.DOTALL)

# 본문 XML 파일 식별 키워드 — 주석/감사보고서가 아닌 본문
_MAIN_BODY_KEYWORDS = [
    "사업의 개요",
    "사업개요",
    "사업의 내용",
    "사업내용",
    "주요 제품 및 서비스",
    "매출 및 수주상황",
]
_EXCLUDE_KEYWORDS = ["주석", "감사보고서", "내부회계관리"]
_MAIN_BOUNDARY_KEYWORDS = [
    "재무에 관한 사항",
    "이사의 경영진단",
    "회계감사인의 감사의견",
    "회계감사인",
    "주주에 관한 사항",
    "임원 및 직원",
    "계열회사",
    "타법인출자",
    "그 밖에 투자자",
]
_UNICODE_ROMAN = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"


def _xml_to_text(xml_content: str) -> str:
    """XML 태그를 제거하고 순수 텍스트를 반환한다."""
    text = re.sub(r'<[^>]+>', ' ', xml_content)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _xml_to_html(xml_content: str) -> str:
    """DART XML을 읽기 좋은 HTML로 변환한다."""
    html = xml_content

    # 테이블 관련 태그 변환
    html = re.sub(
        r'<TABLE[^>]*>',
        '<table style="width:100%;border-collapse:collapse;font-size:0.82rem;margin:0.5rem 0;">',
        html, flags=re.IGNORECASE,
    )
    html = re.sub(r'</TABLE>', '</table>', html, flags=re.IGNORECASE)
    html = re.sub(r'<TR[^>]*>', '<tr>', html, flags=re.IGNORECASE)
    html = re.sub(r'</TR>', '</tr>', html, flags=re.IGNORECASE)
    html = re.sub(
        r'<T[DH][^>]*>',
        '<td style="padding:0.3rem 0.5rem;border:1px solid #D1D8F0;vertical-align:top;">',
        html, flags=re.IGNORECASE,
    )
    html = re.sub(r'</T[DH]>', '</td>', html, flags=re.IGNORECASE)

    # 문단 변환
    html = re.sub(
        r'<P[^>]*>',
        '<p style="margin:0.3rem 0;line-height:1.6;">',
        html, flags=re.IGNORECASE,
    )
    html = re.sub(r'</P>', '</p>', html, flags=re.IGNORECASE)

    # TITLE 태그 → 볼드 제목
    html = re.sub(
        r'<TITLE[^>]*>(.*?)</TITLE>',
        r'<div style="font-weight:700;font-size:0.95rem;margin:0.8rem 0 0.3rem;">\1</div>',
        html, flags=re.IGNORECASE,
    )

    # 나머지 알 수 없는 태그 제거 (table/tr/td/p/div/span/br 유지)
    html = re.sub(r'</?(?!table|tr|td|th|p|div|span|br|a)[a-zA-Z][^>]*>', '', html)

    return html


def _clean_title(title_text: str) -> str:
    """TITLE 텍스트의 공백과 장식 문자를 정규화한다."""
    text = re.sub(r'<[^>]+>', ' ', title_text or '')
    text = unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r'\s+', ' ', text).strip()
    return text.strip("-ㆍ·. ")


def _iter_title_matches(xml_content: str):
    """Yield XML TITLE and DART viewer HTML heading-like nodes in document order."""
    for pattern in (_TITLE_RE, _VIEWER_ANCHOR_TITLE_RE, _HEADING_TITLE_RE):
        for match in pattern.finditer(xml_content or ""):
            yield match.start(), match.end(), _clean_title(match.group(1))


def _title_level(title_text: str) -> int:
    """
    DART TITLE의 대략적인 목차 깊이를 추정한다.

    값이 작을수록 상위 제목이다. 제목 표기가 없는 경우 99를 반환해
    기존의 "다음 TITLE까지" 동작과 호환되도록 한다.
    """
    text = _clean_title(title_text)
    if not text:
        return 99
    if re.match(rf'^[{_UNICODE_ROMAN}]+\s*[.\)]', text):
        return 1
    if re.match(r'^[IVXLC]+\s*[.\)]', text, re.IGNORECASE):
        return 1
    if re.match(r'^제\s*\d+\s*[장절편]', text):
        return 1
    if re.match(r'^\d+\s*[.\)]', text):
        return 2
    if re.match(r'^\(\s*\d+\s*\)', text):
        return 3
    if re.match(r'^[가-힣]\s*[.\)]', text):
        return 3
    return 99


def _is_broad_section_title(section_key: str, title_text: str) -> bool:
    """하위 TITLE 전체를 포괄해야 하는 상위 성격의 제목인지 판단한다."""
    title = _clean_title(title_text)
    broad_keywords = {
        "business_description": ("사업의 내용", "사업내용"),
        "risk_management": ("위험관리", "시장위험과 위험관리", "시장위험 및 위험관리"),
        "management_plan": ("경영진의 경영계획", "경영진단 및 분석의견", "이사의 경영진단"),
        "rd_activities": ("연구개발활동", "연구개발 활동", "연구 및 개발활동"),
    }
    return any(kw in title for kw in broad_keywords.get(section_key, ()))


def _section_end_index(
    title_positions: list[tuple[int, int, str, int]],
    matched_idx: int,
    xml_len: int,
    section_key: str,
) -> int:
    """현재 TITLE이 대표하는 섹션의 끝 위치를 계산한다."""
    sec_start, _, title_text, level = title_positions[matched_idx]
    max_end = min(xml_len, sec_start + 100_000)

    if level == 99 and not _is_broad_section_title(section_key, title_text):
        if matched_idx + 1 < len(title_positions):
            return min(title_positions[matched_idx + 1][0], max_end)
        return max_end

    current_title = _clean_title(title_text)
    for start, _, next_title, next_level in title_positions[matched_idx + 1:]:
        if start >= max_end:
            break
        if _clean_title(next_title) == current_title:
            continue
        if any(kw in next_title for kw in _MAIN_BOUNDARY_KEYWORDS):
            return start
        if level != 99 and next_level <= level:
            return start

    return max_end


def select_main_body_file(all_files: dict[str, str]) -> Optional[str]:
    """
    사업보고서 ZIP 내 XML 파일들 중 본문 파일을 선택한다.
    주석·감사보고서가 아닌, 사업 내용을 담는 파일을 반환한다.
    """
    candidates: list[tuple[str, str]] = []

    for fname, content in all_files.items():
        # 제외 대상 확인
        titles = _TITLE_RE.findall(content[:5000])
        title_text = ' '.join(titles)
        if any(kw in title_text for kw in _EXCLUDE_KEYWORDS):
            continue

        # 본문 키워드 확인
        if any(kw in title_text for kw in _MAIN_BODY_KEYWORDS):
            candidates.append((fname, content))

    if not candidates:
        # 폴백: 가장 큰 비-주석 파일 선택
        for fname, content in sorted(all_files.items(), key=lambda x: len(x[1]), reverse=True):
            titles = _TITLE_RE.findall(content[:5000])
            title_text = ' '.join(titles)
            if not any(kw in title_text for kw in _EXCLUDE_KEYWORDS):
                return content
        return None

    # 가장 많은 본문 키워드를 포함하는 파일 선택
    best = max(candidates, key=lambda x: sum(1 for kw in _MAIN_BODY_KEYWORDS if kw in x[1][:5000]))
    return best[1]


def extract_report_sections(xml_content: str) -> dict[str, dict]:
    """
    사업보고서 본문 XML에서 핵심 섹션을 추출한다.

    Returns:
        {section_key: {"title": str, "body_text": str, "body_html": str, "length": int}}
    """
    result: dict[str, dict] = {}

    # 모든 TITLE 위치 인덱싱
    title_positions: list[tuple[int, int, str, int]] = []
    for start, end, title_text in _iter_title_matches(xml_content):
        if title_text:
            title_positions.append((start, end, title_text, _title_level(title_text)))
    title_positions.sort(key=lambda item: item[0])

    if not title_positions:
        return result

    for section_key, keywords in SECTION_KEYWORDS.items():
        matched_idx: Optional[int] = None

        for kw in keywords:
            for i, (start, end, title_text, level) in enumerate(title_positions):
                if kw in title_text:
                    matched_idx = i
                    break
            if matched_idx is not None:
                break

        if matched_idx is None:
            continue

        # 섹션 범위: 현재 TITLE ~ 다음 같은/상위 TITLE.
        # 상위 섹션 바로 다음 하위 제목에서 본문이 잘리는 문제를 방지한다.
        sec_start = title_positions[matched_idx][0]
        sec_end = _section_end_index(title_positions, matched_idx, len(xml_content), section_key)

        section_xml = xml_content[sec_start:sec_end]
        title_text = title_positions[matched_idx][2]
        body_text = _xml_to_text(section_xml)
        body_html = _xml_to_html(section_xml)

        result[section_key] = {
            "title": title_text,
            "body_text": body_text,
            "body_html": body_html,
            "length": len(body_text),
        }

    return result
