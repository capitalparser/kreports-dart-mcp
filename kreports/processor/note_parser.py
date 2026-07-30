"""DART financial statement note disclosure parser."""

import re
from typing import Any

from kreports.processor.report_section_parser import _TITLE_RE, _title_level, _xml_to_text

NOTE_KEYWORDS: dict[str, list[str]] = {
    "revenue_recognition": [
        "수익", "매출", "고객과의 계약", "수행의무", "거래가격", "수익인식", "수익 인식",
    ],
    "lease": [
        "리스", "사용권자산", "리스부채", "K-IFRS 제1116호", "IFRS 16",
    ],
    "financial_instruments": [
        "금융상품", "금융자산", "금융부채", "공정가치", "신용위험", "유동성위험",
        "기대신용손실", "대손충당금",
    ],
    "related_parties": [
        "특수관계자", "관계자거래", "주요 경영진", "최대주주",
    ],
    "commitments_contingencies": [
        "우발부채", "우발자산", "약정사항", "소송", "지급보증", "담보",
    ],
    "provisions": [
        "충당부채", "복구충당", "제품보증", "손실충당",
    ],
    "impairment": [
        "손상", "손상차손", "회수가능액", "현금창출단위",
    ],
    "subsidiaries_associates": [
        "종속기업", "관계기업", "공동기업", "연결대상",
    ],
    "subsequent_events": [
        "보고기간후 사건", "후속사건", "재무제표 발행승인",
    ],
    "going_concern": [
        "계속기업", "자본잠식", "유동성", "중요한 불확실성",
    ],
}

_TABLE_RE = re.compile(r"<TABLE\b[^>]*>.*?</TABLE>", re.IGNORECASE | re.DOTALL)
_NOTE_TITLE_RE = re.compile(r"^\s*(?:주석\s*)?(\d{1,3})[.)]?\s+(.+?)\s*$")
_TOC_LEADER_RE = re.compile(r"(?:\.{3,}|-{3,})\s*\d+\s*$")
_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\(\s*\d{1,3}(?:,\d{3})+\s*\)")
_KNOWN_SHORT_NOTE_TITLES = {
    "일반사항",
    "일반 사항",
    "회사의 개요",
    "회사 개요",
    "개요",
    "중요한 회계정책",
    "중요한회계정책",
    "수익",
    "매출",
    "비용",
    "법인세",
    "주당이익",
    "배당",
    "리스",
    "금융상품",
    "특수관계자",
    "우발부채",
    "우발자산",
    "약정사항",
    "종속기업",
    "관계기업",
    "보고기간후 사건",
    "후속사건",
}
_REFERENCE_PARTICLES = (
    "을", "를", "은", "는", "에", "에서", "와", "과", "의", "로", "으로",
)


def extract_note_disclosures(
    xml_content: str,
    *,
    corp_code: str = "",
    rcept_no: str = "",
    bsns_year: int | None = None,
    reprt_code: str = "",
    fs_div: str = "",
    source_file: str = "",
) -> list[dict[str, Any]]:
    """
    DART document XML에서 주요 주석 disclosure 후보를 추출한다.

    TITLE 경계와 제목 깊이를 이용해 하위 제목을 포함하되 다음 peer note에서 멈춘다.
    """
    title_positions = [
        (m.start(), m.end(), m.group(1).strip(), _note_title_level(m.group(1).strip()))
        for m in _TITLE_RE.finditer(xml_content)
    ]
    if not title_positions:
        return []

    results: list[dict[str, Any]] = []
    emitted: set[str] = set()

    for idx, (start, _, title, level) in enumerate(title_positions):
        note_key = _match_note_key(title)
        title_matched = note_key is not None
        end = _note_end_index(title_positions, idx, len(xml_content))
        note_xml = xml_content[start:end]
        note_text = _xml_to_text(note_xml)

        if note_key is None:
            note_key = _match_note_key(note_text)
            title_matched = False
        if note_key is None or note_key in emitted:
            continue

        tables = [
            {"raw_xml": m.group(0), "text": _xml_to_text(m.group(0))}
            for m in _TABLE_RE.finditer(note_xml)
        ]
        excerpt = note_text[:1000]
        if len(note_text) > 1000:
            excerpt += "..."

        confidence = 0.9 if title_matched else 0.7
        if level == 99:
            confidence -= 0.1

        results.append({
            "corp_code": corp_code,
            "rcept_no": rcept_no,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
            "note_key": note_key,
            "title": title,
            "source_route": "document_xml",
            "source_file": source_file,
            "span": {"start": start, "end": end},
            "confidence": round(confidence, 2),
            "text_excerpt": excerpt,
            "tables": tables,
            "warnings": [],
        })
        emitted.add(note_key)

    return results


def _match_note_key(text: str) -> str | None:
    for note_key, keywords in NOTE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return note_key
    return None


def _note_title_level(title_text: str) -> int:
    """
    Infer TITLE depth for note disclosures.

    DART filings often render note headings as either "10. 금융상품" or
    "주석 10 금융상품".  The shared section parser already handles the first
    form, but not the second.  Treat only title-like "주석 N 제목" strings as
    peer note headings, while leaving references such as "주석 18을 참고" as
    ordinary low-confidence titles.
    """
    if _is_note_heading_title(title_text):
        return 2
    return _title_level(title_text)


def _is_note_heading_title(title_text: str) -> bool:
    title = re.sub(r"\s+", " ", title_text or "").strip()
    if not title or _TOC_LEADER_RE.search(title):
        return False
    if _AMOUNT_RE.search(title):
        return False
    if len(title) > 90:
        return False

    match = _NOTE_TITLE_RE.match(title)
    if not match:
        return False

    rest = (match.group(2) or "").strip()
    if not rest:
        return False
    if rest[:1] in {"-", "–", "—", "(", ")", "[", "]"}:
        return False

    first_token = rest.split()[0]
    if any(first_token.endswith(particle) for particle in _REFERENCE_PARTICLES):
        return False
    if rest in _KNOWN_SHORT_NOTE_TITLES:
        return True

    hangul = sum(1 for c in rest if "가" <= c <= "힣")
    return hangul >= 2


def _note_end_index(
    title_positions: list[tuple[int, int, str, int]],
    matched_idx: int,
    xml_len: int,
) -> int:
    start, _, _, level = title_positions[matched_idx]
    max_end = min(xml_len, start + 120_000)

    if level == 99:
        if matched_idx + 1 < len(title_positions):
            return min(title_positions[matched_idx + 1][0], max_end)
        return max_end

    for next_start, _, _, next_level in title_positions[matched_idx + 1:]:
        if next_start >= max_end:
            break
        if next_level <= level:
            return next_start

    return max_end
