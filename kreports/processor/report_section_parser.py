"""
사업보고서 본문 섹션 파서.

DART 사업보고서 ZIP 내 본문 XML에서 핵심 경영 정보 섹션을 추출한다.
회계정책(policy_parser.py)이 주석 XML을 파싱하는 것과 달리,
이 모듈은 본문(사업의 개요, 사업의 내용, 위험관리 등)을 파싱한다.
"""
import re
from typing import Optional

# ---------------------------------------------------------------------------
# 추출 대상 섹션 — section_key → TITLE 키워드(우선순위 순)
# ---------------------------------------------------------------------------

SECTION_KEYWORDS: dict[str, list[str]] = {
    "business_overview": [
        "사업의 개요",
        "사업개요",
        "회사의 개요",
    ],
    "business_description": [
        "사업의 내용",
        "사업내용",
        "주요 제품 및 서비스",
        "주요 사업 내용",
    ],
    "risk_management": [
        "위험관리 및 파생상품",
        "위험관리",
        "사업위험",
        "파생상품 및 풋백옵션",
    ],
    "management_plan": [
        "경영진의 경영계획",
        "향후 추진계획",
        "경영방침",
        "중장기 전략",
    ],
    "rd_activities": [
        "연구개발활동",
        "연구개발 활동",
        "연구 및 개발활동",
        "기술개발",
    ],
    "key_contracts": [
        "주요 계약",
        "주요계약",
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

# 본문 XML 파일 식별 키워드 — 주석/감사보고서가 아닌 본문
_MAIN_BODY_KEYWORDS = ["사업의 개요", "사업개요", "사업의 내용", "사업내용"]
_EXCLUDE_KEYWORDS = ["주석", "감사보고서", "내부회계관리"]


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
    title_positions: list[tuple[int, int, str]] = []
    for m in _TITLE_RE.finditer(xml_content):
        title_positions.append((m.start(), m.end(), m.group(1).strip()))

    if not title_positions:
        return result

    for section_key, keywords in SECTION_KEYWORDS.items():
        matched_idx: Optional[int] = None

        for kw in keywords:
            for i, (start, end, title_text) in enumerate(title_positions):
                if kw in title_text:
                    matched_idx = i
                    break
            if matched_idx is not None:
                break

        if matched_idx is None:
            continue

        # 섹션 범위: 현재 TITLE ~ 다음 TITLE
        sec_start = title_positions[matched_idx][0]
        if matched_idx + 1 < len(title_positions):
            sec_end = title_positions[matched_idx + 1][0]
        else:
            sec_end = min(len(xml_content), sec_start + 100_000)

        # 최대 100,000자 제한
        sec_end = min(sec_end, sec_start + 100_000)

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
