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
_TITLE_MARKER_RE = re.compile(
    r"^\s*(?:(?P<arabic>\(?\d{1,2}\)?)[.)]|"
    r"(?P<korean>[가-하])[.)]|"
    r"(?P<roman>[IVX]{1,5})[.)])\s*(?P<title>.+?)\s*$",
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
MAX_TITLE_BLOCK_LINES = 8
MAX_TITLE_BLOCK_CHARS = 800
_TITLE_CONNECTOR_ENDINGS = (
    "및",
    "과",
    "와",
    "의",
    "대한",
    "관련",
    "and",
    "of",
    "for",
    "regarding",
)
_KOREAN_TITLE_CONTINUATIONS = (
    "가치",
    "관련",
    "손상",
    "인식",
    "측정",
    "추정",
    "충당",
    "평가",
    "회계처리",
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


@dataclass(frozen=True)
class _TitleBoundary:
    start: int
    title: str


@dataclass(frozen=True)
class _MatterFrame:
    title_start: int
    title: str
    reason_heading: int
    response_heading: int


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


def _title_marker(line: str) -> tuple[str, str, str] | None:
    match = _TITLE_MARKER_RE.match(line)
    if not match:
        return None
    title = _normalize_title(match.group("title"))
    if not title or title.endswith((".", "。")):
        return None
    for family in ("arabic", "roman", "korean"):
        marker = match.group(family)
        if marker is None:
            continue
        identity = marker.strip("()").upper()
        return family, identity, title
    return None


def _is_title_continuation(previous: str, current: str) -> bool:
    """Return whether ``current`` is grammatically dependent on ``previous``."""
    previous = _normalize_title(previous)
    current = _normalize_title(current)
    previous_compact = _compact(previous)
    current_compact = _compact(current)
    if not previous or not current:
        return False
    if current_compact == previous_compact:
        return True
    if any(previous_compact.endswith(value) for value in _TITLE_CONNECTOR_ENDINGS):
        return True
    first_alpha = next((char for char in current if char.isalpha()), "")
    if first_alpha and first_alpha.isascii() and first_alpha.islower():
        return True
    return any(
        current_compact.startswith(value)
        for value in _KOREAN_TITLE_CONTINUATIONS
    )


def _title_parts(values: list[str]) -> str:
    parts: list[str] = []
    for value in values:
        normalized = _normalize_title(value)
        if not normalized:
            continue
        if parts and _compact(normalized) == _compact(parts[-1]):
            continue
        parts.append(normalized)
    return _normalize_title(" ".join(parts))


def _unnumbered_suffix_start(lines: list[str], lower: int, upper: int) -> int:
    """Find the bounded grammatical suffix immediately owned by a reason."""
    start = upper - 1
    while start > lower and _is_title_continuation(
        lines[start - 1],
        lines[start],
    ):
        start -= 1
    return start


def _discover_title_boundary(
    lines: list[str],
    *,
    lower: int,
    reason_index: int,
    response_owned: bool,
) -> _TitleBoundary | None:
    """Discover one reason-anchored title without segmenting any fields."""
    if reason_index <= lower:
        return None
    boundary_headings = (
        _KAM_HEADINGS
        + _REASON_HEADINGS
        + _RESPONSE_HEADINGS
        + _TRAILING_HEADINGS
    )
    scan_start = max(lower, reason_index - MAX_TITLE_BLOCK_LINES)
    for index in range(reason_index - 1, scan_start - 1, -1):
        if _matches_heading(lines[index], boundary_headings):
            scan_start = index + 1
            break
    if scan_start >= reason_index:
        return None

    marker_index: int | None = None
    marker: tuple[str, str, str] | None = None
    for index in range(reason_index - 1, scan_start - 1, -1):
        found = _title_marker(lines[index])
        if found is not None:
            marker_index = index
            marker = found
            break

    start: int
    title_values: list[str]
    if marker_index is None or marker is None:
        start = _unnumbered_suffix_start(lines, scan_start, reason_index)
        title_values = lines[start:reason_index]
    else:
        _, _, marked_title = marker
        suffix_start = marker_index + 1
        has_suffix = suffix_start < reason_index
        marked_wrap = has_suffix and _is_title_continuation(
            marked_title,
            lines[suffix_start],
        )
        if response_owned and has_suffix and not marked_wrap:
            # This marker already belongs to the accepted matter's response.
            # The unnumbered suffix immediately before the new reason owns the
            # next title; the procedure marker is never recycled as a title.
            start = _unnumbered_suffix_start(lines, suffix_start, reason_index)
            title_values = lines[start:reason_index]
        else:
            start = marker_index
            title_values = [marked_title, *lines[suffix_start:reason_index]]

    if sum(len(line) for line in lines[start:reason_index]) > MAX_TITLE_BLOCK_CHARS:
        return None
    title = _title_parts(title_values)
    if not title:
        return None
    return _TitleBoundary(
        start=start,
        title=title,
    )


def _discover_matter_frames(lines: list[str]) -> list[_MatterFrame]:
    """Phase 1: discover matter boundaries with a bounded heading state machine."""
    reason_indices = [
        index
        for index, line in enumerate(lines)
        if _matches_heading(line, _REASON_HEADINGS)
    ]
    frames: list[_MatterFrame] = []
    previous_response: int | None = None
    for anchor_position, reason_index in enumerate(reason_indices):
        next_reason = (
            reason_indices[anchor_position + 1]
            if anchor_position + 1 < len(reason_indices)
            else len(lines)
        )
        response_index = next(
            (
                index
                for index in range(reason_index + 1, next_reason)
                if _matches_heading(lines[index], _RESPONSE_HEADINGS)
            ),
            None,
        )
        if response_index is None:
            continue
        lower = previous_response + 1 if previous_response is not None else 0
        title = _discover_title_boundary(
            lines,
            lower=lower,
            reason_index=reason_index,
            response_owned=previous_response is not None,
        )
        if title is None:
            continue
        frames.append(
            _MatterFrame(
                title_start=title.start,
                title=title.title,
                reason_heading=reason_index,
                response_heading=response_index,
            )
        )
        previous_response = response_index
    return frames


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
    frames = _discover_matter_frames(lines)
    items: list[ParsedKamItem] = []
    for item_index, frame in enumerate(frames):
        end = (
            frames[item_index + 1].title_start
            if item_index + 1 < len(frames)
            else len(lines)
        )
        matter_lines = lines[frame.title_start:end]
        reason = "\n".join(
            lines[frame.reason_heading + 1:frame.response_heading]
        ).strip()
        response = "\n".join(lines[frame.response_heading + 1:end]).strip()
        if not reason or not response:
            continue
        body = "\n".join(matter_lines).strip()
        items.append(
            ParsedKamItem(
                ordinal=len(items) + 1,
                title=frame.title,
                normalized_topic=_topic(frame.title),
                reason_text=reason,
                audit_response_text=response,
                related_note_references=_notes(body),
                full_body=body,
                full_body_hash=sha1(body.encode("utf-8")).hexdigest(),
                full_body_length=len(body),
            )
        )
    return items
