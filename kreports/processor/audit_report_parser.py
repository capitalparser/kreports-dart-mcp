"""Audit report body section parser.

The footing project already established the useful abstraction: treat DART
filings as ordered sections and tables, not as one undifferentiated blob. This
module applies that idea to audit reports and extracts the paragraphs that are
useful for auditor-facing MCP tools.
"""
from __future__ import annotations

import re

SECTION_KEYWORDS: dict[str, list[str]] = {
    "audit_opinion": [
        "감사의견",
        "감사인의 의견",
    ],
    "kam": [
        "핵심감사사항",
        "핵심 감사사항",
        "Key Audit Matters",
        "KAM",
    ],
    "emphasis": [
        "강조사항",
        "강조 사항",
    ],
    "other_matter": [
        "기타사항",
        "기타 사항",
    ],
    "going_concern": [
        "계속기업 관련 중요한 불확실성",
        "계속기업 관련 불확실성",
        "계속기업전제와 관련된 중요한 불확실성",
    ],
    "management_responsibility": [
        "재무제표에 대한 경영진",
        "경영진과 지배기구의 책임",
        "경영진의 책임",
    ],
    "auditor_responsibility": [
        "재무제표감사에 대한 감사인의 책임",
        "감사인의 책임",
        "감사인의 감사책임",
    ],
    "basis_for_opinion": [
        "감사의견근거",
        "감사의견 근거",
        "의견근거",
        "의견 근거",
    ],
}

SECTION_LABELS: dict[str, str] = {
    "audit_opinion": "감사의견",
    "kam": "핵심감사사항",
    "emphasis": "강조사항",
    "other_matter": "기타사항",
    "going_concern": "계속기업",
    "management_responsibility": "경영진의 책임",
    "auditor_responsibility": "감사인의 책임",
    "basis_for_opinion": "감사의견 근거",
}

_TITLE_RE = re.compile(r"<TITLE[^>]*>(.*?)</TITLE>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_TITLE_OR_P_RE = re.compile(r"</?(?:TITLE|P|TR|TABLE)[^>]*>", re.IGNORECASE)

_AUDIT_REPORT_TRAILING_MARKERS = (
    "(첨부)재무제표",
    "(첨부) 재무제표",
    "(첨부)재 무 제 표",
    "(첨부) 재 무 제 표",
    "첨부된 재무제표",
    "별첨 재무제표",
    "재 무 제 표",
    "연 결 재 무 제 표",
)

_SIGNATURE_MARKERS = (
    "이 감사보고서의 근거가 된 감사를 실시한 업무수행이사는",
    "이 감사보고서는 감사보고서일",
    "대 표 이 사",
    "대표이사",
)

_SECTION_SPECIFIC_TRAILING_MARKERS: dict[str, tuple[str, ...]] = {
    "other_matter": _AUDIT_REPORT_TRAILING_MARKERS + _SIGNATURE_MARKERS,
    "emphasis": _AUDIT_REPORT_TRAILING_MARKERS,
    "going_concern": _AUDIT_REPORT_TRAILING_MARKERS,
    "basis_for_opinion": _AUDIT_REPORT_TRAILING_MARKERS,
    "audit_opinion": _AUDIT_REPORT_TRAILING_MARKERS,
    "kam": _AUDIT_REPORT_TRAILING_MARKERS,
}


def _clean(value: str) -> str:
    text = _TAG_RE.sub(" ", value or "")
    text = re.sub(r"&cr;|&#13;", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def xml_to_text(xml_content: str) -> str:
    """Convert DART XML-ish content to readable plain text."""
    text = _TITLE_OR_P_RE.sub("\n", xml_content or "")
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"&cr;|&#13;", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = _WS_RE.sub(" ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _title_positions(xml_content: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for m in _TITLE_RE.finditer(xml_content or ""):
        title = _clean(m.group(1))
        if title:
            out.append((m.start(), m.end(), title))
    return out


def _matches(title: str, keywords: list[str]) -> bool:
    compact = re.sub(r"\s+", "", title).lower()
    for kw in keywords:
        key = re.sub(r"\s+", "", kw).lower()
        if key and key in compact:
            return True
    return False


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _find_compact_marker(text: str, marker: str) -> int:
    """Find a marker while ignoring whitespace inserted by DART HTML/XML."""
    compact_marker = _compact(marker)
    if not compact_marker:
        return -1
    pattern = re.compile(r"\s*".join(re.escape(ch) for ch in compact_marker))
    match = pattern.search(text or "")
    return match.start() if match else -1


def _trim_section_body(section_key: str, body: str) -> str:
    """Trim obvious non-audit-report appendices accidentally captured as a section."""
    if not body:
        return body
    end = len(body)
    for marker in _SECTION_SPECIFIC_TRAILING_MARKERS.get(section_key, ()):
        pos = _find_compact_marker(body, marker)
        if pos > 0:
            end = min(end, pos)
    trimmed = body[:end].strip()
    return trimmed or body.strip()


def _section_end(titles: list[tuple[int, int, str]], idx: int, xml_len: int) -> int:
    if idx + 1 < len(titles):
        return titles[idx + 1][0]
    return xml_len


def extract_audit_report_sections(xml_content: str) -> dict[str, dict]:
    """Extract auditor-useful sections from an audit report document.xml body."""
    titles = _title_positions(xml_content)
    result: dict[str, dict] = {}
    full_text = xml_to_text(xml_content)
    if not titles:
        body = full_text
        if body:
            result["full_text"] = {
                "title": "감사보고서 본문",
                "body_text": body,
                "length": len(body),
            }
        result.update(_extract_by_text_headings(full_text))
        return result

    for section_key, keywords in SECTION_KEYWORDS.items():
        match_idx = None
        for idx, (_, _, title) in enumerate(titles):
            if _matches(title, keywords):
                match_idx = idx
                break
        if match_idx is None:
            continue

        start = titles[match_idx][0]
        end = _section_end(titles, match_idx, len(xml_content))
        section_xml = xml_content[start:end]
        body = _trim_section_body(section_key, xml_to_text(section_xml))
        if not body:
            continue
        result[section_key] = {
            "title": titles[match_idx][2],
            "body_text": body,
            "length": len(body),
        }

    # Many DART audit reports put the report's real headings in paragraph text,
    # while TITLE only contains "독립된 감사인의 감사보고서" and attachments.
    for key, section in _extract_by_text_headings(full_text).items():
        result.setdefault(key, section)

    return result


def _extract_by_text_headings(text: str) -> dict[str, dict]:
    if not text:
        return {}
    markers: list[tuple[int, str, str]] = []
    for section_key, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            if len(kw) < 3:
                continue
            pos = _find_heading_candidate(text, section_key, kw)
            if pos >= 0:
                markers.append((pos, section_key, kw))
                break
    if not markers:
        return {}
    markers.sort(key=lambda item: item[0])

    result: dict[str, dict] = {}
    for idx, (start, section_key, heading) in enumerate(markers):
        if section_key in result:
            continue
        end = len(text)
        for next_start, _, _ in markers[idx + 1:]:
            if next_start > start:
                end = next_start
                break
        body = _trim_section_body(section_key, text[start:end].strip())
        if len(body) < len(heading) + 10:
            continue
        result[section_key] = {
            "title": heading,
            "body_text": body,
            "length": len(body),
        }
    return result


def _find_heading_candidate(text: str, section_key: str, keyword: str) -> int:
    """Find section headings while avoiding keyword mentions inside prose."""
    compact_keyword = re.sub(r"\s+", "", keyword)
    pattern = re.compile(re.escape(keyword))
    for match in pattern.finditer(text):
        pos = match.start()
        prefix = text[max(0, pos - 20):pos]
        suffix = text[pos:pos + 40]
        compact_suffix = re.sub(r"\s+", "", suffix)
        if section_key == "kam" and compact_suffix.startswith(f"{compact_keyword}으로결정"):
            continue
        if pos == 0 or "\n" in prefix or len(prefix.strip()) <= 3:
            return pos
        if section_key != "kam":
            return pos
    return -1


_KAM_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "revenue": ["수익", "매출", "공사수익", "진행률"],
    "inventory": ["재고", "재고자산", "평가충당"],
    "impairment": ["손상", "손상검사", "회수가능"],
    "fair_value": ["공정가치", "가치평가", "평가기법"],
    "provision": ["충당부채", "우발", "소송"],
    "going_concern": ["계속기업"],
    "consolidation": ["연결", "종속기업", "사업결합"],
    "tax": ["법인세", "이연법인세"],
}


def classify_kam_topics(text: str) -> list[str]:
    hits: list[str] = []
    for topic, keywords in _KAM_TOPIC_KEYWORDS.items():
        if any(kw in (text or "") for kw in keywords):
            hits.append(topic)
    return hits


_KAM_REASON_HINTS = (
    "핵심감사사항으로 결정",
    "핵심 감사사항으로 결정",
    "중요한 왜곡표시위험",
    "유의적인 위험",
    "추정의 불확실성",
    "경영진의 판단",
    "중요한 추정",
)

_KAM_PROCEDURE_HINTS = (
    "감사절차",
    "주요 감사절차",
    "감사에서 다루어진 방법",
    "다음의 절차",
    "수행하였습니다",
    "검토",
    "평가",
    "테스트",
    "재계산",
    "대사",
)

_PROCEDURE_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "internal_control": ("내부통제", "통제", "프로세스", "승인"),
    "substantive_test": ("문서검사", "표본", "대사", "확인", "검사", "입증"),
    "estimation_assumption": ("추정", "가정", "민감도", "회수가능", "평가"),
    "external_confirmation": ("조회", "외부조회", "확인서", "채권조회"),
    "valuation_specialist": ("전문가", "평가법인", "감정", "외부평가기관"),
    "analytics": ("분석적", "추세", "비교분석", "분석"),
    "cutoff": ("cutoff", "기간귀속", "마감", "발생사실"),
}


def _sentence_windows(text: str) -> list[str]:
    cleaned = xml_to_text(text)
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)\s+|\n+", cleaned)
    return [part.strip() for part in parts if part and part.strip()]


def _excerpt_for_hints(text: str, hints: tuple[str, ...], *, limit: int = 700) -> str:
    sentences = _sentence_windows(text)
    selected: list[str] = []
    for idx, sentence in enumerate(sentences):
        if any(hint in sentence for hint in hints):
            window = sentences[max(0, idx - 1): idx + 2]
            for item in window:
                if item not in selected:
                    selected.append(item)
            if len(" ".join(selected)) >= limit:
                break
    excerpt = " ".join(selected).strip()
    return excerpt[:limit]


def summarize_kam_body(text: str) -> dict:
    """Return structured hints from a KAM body.

    The output is intentionally a hint pack, not a legal/audit conclusion.
    Korean audit reports are not consistently templated, so the parser surfaces
    evidence excerpts and booleans instead of asserting exact semantic fields.
    """
    body = xml_to_text(text)
    reason_excerpt = _excerpt_for_hints(body, _KAM_REASON_HINTS)
    procedure_excerpt = _excerpt_for_hints(body, _KAM_PROCEDURE_HINTS)
    reason_hits = [hint for hint in _KAM_REASON_HINTS if hint in body]
    procedure_hits = [hint for hint in _KAM_PROCEDURE_HINTS if hint in body]
    return {
        "topics": classify_kam_topics(body),
        "has_reason_hint": bool(reason_excerpt or reason_hits),
        "has_procedure_hint": bool(procedure_excerpt or procedure_hits),
        "reason_excerpt": reason_excerpt,
        "procedure_excerpt": procedure_excerpt,
        "reason_keywords": reason_hits,
        "procedure_keywords": procedure_hits,
    }


def classify_audit_procedure_type(text: str) -> str:
    body = text or ""
    for procedure_type, keywords in _PROCEDURE_TYPE_KEYWORDS.items():
        if any(keyword in body for keyword in keywords):
            return procedure_type
    return "other"


def _procedure_zone(text: str) -> str:
    body = xml_to_text(text)
    candidates = [
        "핵심감사사항이 감사에서 다루어진 방법",
        "감사에서 다루어진 방법",
        "주요 감사절차",
        "감사절차",
    ]
    positions = [body.find(marker) for marker in candidates if body.find(marker) >= 0]
    if not positions:
        return body
    return body[min(positions):]


def extract_audit_procedure_items(kam_body: str) -> list[dict]:
    """Split a KAM body into procedure-level evidence items."""
    zone = _procedure_zone(kam_body)
    if not zone:
        return []
    pieces = re.split(r"\n+|(?:^|\s)[·•\-]\s+|(?:^|\s)\(\d+\)\s*", zone)
    items: list[dict] = []
    seen: set[str] = set()
    for piece in pieces:
        text = re.sub(r"\s+", " ", piece or "").strip(" ;·•-")
        if len(text) < 12:
            continue
        if text in {
            "핵심감사사항이 감사에서 다루어진 방법",
            "감사에서 다루어진 방법",
            "주요 감사절차",
            "감사절차",
        }:
            continue
        if not any(hint in text for hint in _KAM_PROCEDURE_HINTS + ("내부통제", "문서검사", "재계산", "대사", "조회")):
            continue
        if text in seen:
            continue
        seen.add(text)
        items.append({
            "procedure_text": text[:1200],
            "procedure_type": classify_audit_procedure_type(text),
        })
    return items
