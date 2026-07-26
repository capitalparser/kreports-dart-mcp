"""Deterministic parser for full-body key audit matters."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from html import unescape
import re


PARSER_VERSION = "v1"
MAX_INPUT_CHARS = 2_000_000

_BLOCK_TAG_RE = re.compile(
    r"</?(?:TITLE|P|TD|TH|TR|TABLE|BR)\b[^>]*>",
    flags=re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]{0,1000}>")
_NUMBERED_TITLE_RE = re.compile(
    r"^\s*(?:\(?\d{1,2}\)?[.)]|[가-하][.)]|[IVX]{1,5}[.)])\s*(.+?)\s*$",
    flags=re.IGNORECASE,
)
_NOTE_RE = re.compile(
    r"(?:관련\s*(?:재무제표\s*)?)?(?:주석|note)\s*[제]?\s*(\d+(?:[.-]\d+)?)",
    flags=re.IGNORECASE,
)

_KAM_HEADINGS = ("핵심감사사항", "keyauditmatters")
_REASON_HEADINGS = (
    "핵심감사사항으로선정한이유",
    "핵심감사사항으로결정한이유",
    "whythematterwasdeterminedtobeakeyauditmatter",
    "whythematterwasconsideredtobeoneofthemostsignificantmattersintheaudit",
    "whythematterwasconsideredtobeoneofmostsignificanceintheaudit",
    "whythematterwasconsideredsignificant",
)
_RESPONSE_HEADINGS = (
    "감사에서다루어진방법",
    "핵심감사사항이감사에서다루어진방법",
    "감사인이수행한주요절차",
    "감사인의대응",
    "howthematterwasaddressedintheaudit",
    "auditresponse",
)
_TRAILING_HEADINGS = (
    "재무제표감사에대한감사인의책임",
    "감사인의책임",
    "재무제표에대한경영진",
    "경영진과지배기구의책임",
    "첨부재무제표",
    "별첨재무제표",
    "auditor'sresponsibilitiesfortheauditof thefinancialstatements".replace(" ", ""),
    "auditorresponsibilitiesfortheauditof thefinancialstatements".replace(" ", ""),
)


@dataclass(frozen=True)
class ParsedKamItem:
    ordinal: int
    title: str
    normalized_topic: str | None
    reason_text: str | None
    audit_response_text: str | None
    related_note_references: list[str]
    full_body: str
    full_body_hash: str
    full_body_length: int
    quality_status: str = "full_body"
    parser_version: str = PARSER_VERSION


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _plain_lines(full_text: str) -> list[str]:
    bounded = (full_text or "")[:MAX_INPUT_CHARS]
    text = _BLOCK_TAG_RE.sub("\n", bounded)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    text = re.sub(r"&cr;|&#13;", "\n", text, flags=re.IGNORECASE)
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", raw_line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return lines


def _matches_heading(line: str, headings: tuple[str, ...]) -> bool:
    compact = _compact(line).strip(":-–—()[]")
    return any(compact == heading or compact.startswith(f"{heading}:") for heading in headings)


def _trim_to_kam(lines: list[str]) -> list[str]:
    start = 0
    for index, line in enumerate(lines):
        if _matches_heading(line, _KAM_HEADINGS):
            start = index + 1
            break
    end = len(lines)
    for index in range(start, len(lines)):
        if _matches_heading(lines[index], _TRAILING_HEADINGS):
            end = index
            break
    return lines[start:end]


def _normalize_title(title: str) -> str:
    title = title.strip(" :-–—")
    title_parts = title.split()
    if (
        len(title_parts) > 1
        and all(len(part) == 1 for part in title_parts)
        and all(re.fullmatch(r"[가-힣]", part) for part in title_parts)
    ):
        return "".join(title_parts)
    return re.sub(r"\s+", " ", title).strip()


def _numbered_title(
    lines: list[str],
    index: int,
    *,
    in_response: bool,
) -> tuple[str, int] | None:
    match = _NUMBERED_TITLE_RE.match(lines[index])
    if not match:
        return None
    first = _normalize_title(match.group(1))
    if not first or first.endswith((".", "。")):
        return None
    parts = [first]
    for candidate_index, candidate in enumerate(
        lines[index + 1:index + 5],
        start=index + 1,
    ):
        if _matches_heading(candidate, _REASON_HEADINGS):
            return _normalize_title(" ".join(parts)), candidate_index
        if (
            _NUMBERED_TITLE_RE.match(candidate)
            or _matches_heading(
                candidate,
                _KAM_HEADINGS
                + _RESPONSE_HEADINGS
                + _TRAILING_HEADINGS,
            )
        ):
            return None
        normalized_candidate = _normalize_title(candidate)
        if _compact(normalized_candidate) == _compact(parts[-1]):
            continue
        if in_response:
            return None
        if len(parts) > 1 or len(candidate) > 200:
            return None
        parts.append(normalized_candidate)
    return None


def _title_starts(lines: list[str]) -> list[tuple[int, str]]:
    starts: list[tuple[int, str]] = []
    covered_until = -1
    in_response = False
    for index, line in enumerate(lines):
        if _matches_heading(line, _REASON_HEADINGS):
            in_response = False
        elif _matches_heading(line, _RESPONSE_HEADINGS):
            in_response = True
        numbered = _numbered_title(lines, index, in_response=in_response)
        title = numbered[0] if numbered is not None else None
        if numbered is not None:
            covered_until = max(covered_until, numbered[1] - 1)
        if title is None and (
            index > covered_until
            and index + 1 < len(lines)
            and _matches_heading(lines[index + 1], _REASON_HEADINGS)
            and len(line) <= 200
            and not _matches_heading(
                line,
                _KAM_HEADINGS
                + _REASON_HEADINGS
                + _RESPONSE_HEADINGS
                + _TRAILING_HEADINGS,
            )
        ):
            title = _normalize_title(line)
        if title:
            starts.append((index, title))
    return starts


def _field_range(lines: list[str], headings: tuple[str, ...], end_headings: tuple[str, ...]) -> str | None:
    start: int | None = None
    for index, line in enumerate(lines):
        if _matches_heading(line, headings):
            start = index + 1
            break
    if start is None:
        return None
    end = len(lines)
    for index in range(start, len(lines)):
        if _matches_heading(lines[index], end_headings):
            end = index
            break
    value = "\n".join(lines[start:end]).strip()
    return value or None


def _topic(title: str) -> str | None:
    compact = _compact(title)
    for topic, words in (
        ("revenue", ("수익", "매출", "revenue")),
        ("inventory", ("재고", "inventory")),
        ("impairment", ("손상", "impairment")),
        ("valuation", ("평가", "valuation", "공정가치")),
        ("provision", ("충당", "provision")),
    ):
        if any(word in compact for word in words):
            return topic
    return None


def _notes(value: str) -> list[str]:
    notes: list[str] = []
    for match in _NOTE_RE.finditer(value):
        note = f"주석 {match.group(1)}"
        if note not in notes:
            notes.append(note)
    for match in re.finditer(
        r"(?:관련재무제표)?주석(?:제)?(\d+(?:[.-]\d+)?)",
        _compact(value),
    ):
        note = f"주석 {match.group(1)}"
        if note not in notes:
            notes.append(note)
    return notes


def kam_detail_heading_status(full_text: str) -> tuple[bool, bool]:
    """Return whether explicit reason and audit-response headings are present."""
    lines = _plain_lines(full_text)
    return (
        any(_matches_heading(line, _REASON_HEADINGS) for line in lines),
        any(_matches_heading(line, _RESPONSE_HEADINGS) for line in lines),
    )


def extract_kam_items(full_text: str) -> list[ParsedKamItem]:
    """Extract complete, matter-level KAMs from a cached filing body.

    The parser only returns matters that contain both a selection-reason and an
    audit-response heading. Short summaries are classified by the rebuild
    layer and never expanded here.
    """
    lines = _trim_to_kam(_plain_lines(full_text))
    starts = _title_starts(lines)
    items: list[ParsedKamItem] = []
    for item_index, (start, title) in enumerate(starts):
        end = starts[item_index + 1][0] if item_index + 1 < len(starts) else len(lines)
        matter_lines = lines[start:end]
        reason = _field_range(matter_lines, _REASON_HEADINGS, _RESPONSE_HEADINGS)
        response = _field_range(matter_lines, _RESPONSE_HEADINGS, ())
        if not reason or not response:
            continue
        body = "\n".join(matter_lines).strip()
        items.append(
            ParsedKamItem(
                ordinal=len(items) + 1,
                title=title,
                normalized_topic=_topic(title),
                reason_text=reason,
                audit_response_text=response,
                related_note_references=_notes(body),
                full_body=body,
                full_body_hash=sha1(body.encode("utf-8")).hexdigest(),
                full_body_length=len(body),
            )
        )
    return items
