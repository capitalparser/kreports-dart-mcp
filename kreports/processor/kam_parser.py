"""Deterministic parser for full-body key audit matters."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha1
from html import unescape
from html.parser import HTMLParser
import re


PARSER_VERSION = "v1"
MAX_INPUT_CHARS = 2_000_000

_TITLE_MARKER_RE = re.compile(
    r"^\s*(?:(?P<arabic>\(?\d{1,2}\)?)[.)]|"
    r"(?P<korean>[가-하])[.)]|"
    r"(?P<roman>[IVX]{1,5})[.)])\s*(?P<title>.+?)\s*$",
    flags=re.IGNORECASE,
)
_CDATA_DECL_RE = re.compile(r"<!\[CDATA", re.IGNORECASE)
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
_INITIAL_MARKER_IDENTITIES = {
    "arabic": "1",
    "roman": "I",
    "korean": "가",
}
_TITLE_TOPIC_TERMS = (
    "매출",
    "수익",
    "재고",
    "영업권",
    "자산",
    "부채",
    "충당",
    "공정가치",
    "금융상품",
    "파생상품",
    "법인세",
    "계속기업",
    "연결범위",
    "종속기업",
    "매출채권",
    "회수가능",
    "리스",
    "revenue",
    "inventory",
    "goodwill",
    "asset",
    "liability",
    "provision",
    "fairvalue",
    "financialinstrument",
    "derivative",
    "incometax",
    "goingconcern",
    "performanceobligation",
    "lease",
    "classification",
)
_TITLE_RISK_TERMS = (
    "손상",
    "인식",
    "측정",
    "추정",
    "평가",
    "회계처리",
    "impairment",
    "recognition",
    "measurement",
    "estimate",
    "valuation",
    "accounting",
)
_AUDIT_PROCEDURE_TERMS = (
    "검사",
    "검토",
    "확인",
    "테스트",
    "재계산",
    "재수행",
    "조회",
    "수행",
    "대사",
    "inspect",
    "test",
    "review",
    "confirm",
    "recalculate",
    "reperform",
    "inquire",
    "perform",
    "reconcile",
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
class StructuredLine:
    text: str
    origin: str
    block_id: int
    blank_before: bool = False
    tag_path: tuple[str, ...] = ()
    is_title_container: bool = False
    is_table_header: bool = False
    is_table_cell: bool = False
    is_explicit_heading: bool = False


@dataclass(frozen=True)
class KamParseOutcome:
    items: list[ParsedKamItem]
    status: str
    limitations: list[str]


@dataclass(frozen=True)
class _TitleBoundary:
    start: int
    title: str
    marker: tuple[str, str] | None
    has_explicit_structure: bool


@dataclass
class _MatterFrame:
    title_start: int
    title: str
    heading_frames: list[_HeadingFrame]
    marker: tuple[str, str] | None
    has_explicit_title_structure: bool


@dataclass(frozen=True)
class _HeadingFrame:
    reason_heading: int
    reason_separators: tuple[int, ...]
    response_heading: int
    response_separators: tuple[int, ...]
    end: int


@dataclass
class _TagFrame:
    tag: str
    explicit_heading: bool
    emitted_content: bool = False


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _input_structure_limitations(full_text: str) -> list[str]:
    value = full_text or ""
    bounded = value[:MAX_INPUT_CHARS]
    limitations: list[str] = []
    if len(value) > MAX_INPUT_CHARS:
        limitations.append("input_truncated")

    position = 0
    while True:
        match = _CDATA_DECL_RE.search(bounded, position)
        if match is None:
            break
        payload_start = match.end()
        if (
            payload_start >= len(bounded)
            or bounded[payload_start] != "["
        ):
            limitations.append("malformed_cdata")
            break
        end = bounded.find("]]>", payload_start + 1)
        if end < 0:
            limitations.append("malformed_cdata")
            break
        position = end + len("]]>")
    return limitations


def _structured_lines(full_text: str) -> list[StructuredLine]:
    bounded = (full_text or "")[:MAX_INPUT_CHARS]
    lines: list[StructuredLine] = []
    tag_stack: list[_TagFrame] = []
    buffer: list[str] = []
    block_id = 0
    blank_before = False

    def current_origin() -> str:
        for frame in reversed(tag_stack):
            if frame.tag == "title":
                return "title"
            if frame.tag in {"td", "th"}:
                return "table_cell"
            if frame.tag == "p":
                return "paragraph"
        return "plain"

    def flush() -> None:
        nonlocal block_id, blank_before
        value = re.sub(r"[ \t\r\f\v]+", " ", "".join(buffer)).strip()
        buffer.clear()
        if not value:
            blank_before = True
            return
        tag_path = tuple(frame.tag for frame in tag_stack)
        lines.append(
            StructuredLine(
                text=value,
                origin=current_origin(),
                block_id=block_id,
                blank_before=blank_before,
                tag_path=tag_path,
                is_title_container="title" in tag_path,
                is_table_header="th" in tag_path,
                is_table_cell=any(
                    tag in {"td", "th"}
                    for tag in tag_path
                ),
                is_explicit_heading=any(
                    frame.explicit_heading
                    for frame in tag_stack
                ),
            )
        )
        for frame in tag_stack:
            frame.emitted_content = True
        block_id += 1
        blank_before = False

    def explicit_heading(
        tag: str,
        parsed_attributes: list[tuple[str, str | None]],
    ) -> bool:
        if tag in {"title", "th"}:
            return True
        if tag != "td":
            return False
        role_values = [
            value or ""
            for name, value in parsed_attributes
            if name.lower() == "role"
        ]
        # Generic table cells are strong title evidence only when the source
        # declares the exact semantic role. Class/id names are presentation
        # details and substring token inference creates false title evidence
        # for values such as ``not-title`` and ``data-title-value``.
        return (
            len(role_values) == 1
            and role_values[0].strip().lower() == "heading"
        )

    def clear_strong_ancestry() -> None:
        strong_index = next(
            (
                index
                for index, frame in enumerate(tag_stack)
                if frame.explicit_heading
            ),
            None,
        )
        if strong_index is not None:
            del tag_stack[strong_index:]

    def close_open_structure(
        incompatible: set[str],
        *,
        after_tag: str | None = None,
    ) -> None:
        lower = 0
        if after_tag is not None:
            boundary = next(
                (
                    index
                    for index in range(len(tag_stack) - 1, -1, -1)
                    if tag_stack[index].tag == after_tag
                ),
                None,
            )
            if boundary is not None:
                lower = boundary + 1
        incompatible_index = next(
            (
                index
                for index in range(lower, len(tag_stack))
                if tag_stack[index].tag in incompatible
            ),
            None,
        )
        if incompatible_index is not None:
            del tag_stack[incompatible_index:]

    def prepare_opening(tag: str) -> None:
        if tag == "p":
            close_open_structure({"p"})

            strong_with_content = next(
                (
                    index
                    for index, frame in enumerate(tag_stack)
                    if frame.explicit_heading and frame.emitted_content
                ),
                None,
            )
            if strong_with_content is not None:
                del tag_stack[strong_with_content:]
            return

        if tag == "title":
            close_open_structure({"title", "p"})
            return

        if tag == "tr":
            # A new row/cell cannot inherit title semantics from a malformed
            # container left open in a previous structural block.
            clear_strong_ancestry()
            close_open_structure(
                {"tr", "td", "th", "title", "p"},
                after_tag="table",
            )
            return

        if tag in {"td", "th"}:
            clear_strong_ancestry()
            close_open_structure(
                {"td", "th", "title", "p"},
                after_tag="tr",
            )
            return

        if tag == "table":
            clear_strong_ancestry()

    structural_tags = {"title", "p", "td", "th", "tr", "table"}
    block_tags = structural_tags | {"br"}

    def append_text(value: str) -> None:
        value = unescape(value)
        value = re.sub(r"&cr;|&#13;", "\n", value, flags=re.IGNORECASE)
        pieces = value.splitlines(keepends=True)
        for piece in pieces:
            buffer.append(piece.rstrip("\r\n"))
            if piece.endswith(("\n", "\r")):
                flush()

    class StructureParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=False)

        @staticmethod
        def handle_self_closing(tag: str) -> None:
            tag = tag.lower()
            if tag == "br":
                flush()
                return
            if tag not in structural_tags:
                return
            flush()
            prepare_opening(tag)
            # A no-content event cannot own heading evidence. It terminates
            # any prior strong scope instead of silently carrying that scope
            # into the next text block.
            clear_strong_ancestry()

        def handle_starttag(
            self,
            tag: str,
            attrs: list[tuple[str, str | None]],
        ) -> None:
            tag = tag.lower()
            raw_tag = self.get_starttag_text() or ""
            if re.search(r"/\s*>$", raw_tag):
                self.handle_self_closing(tag)
                return
            if tag not in block_tags:
                tag_stack.append(
                    _TagFrame(tag=tag, explicit_heading=False)
                )
                return
            flush()
            if tag == "br":
                return
            prepare_opening(tag)
            tag_stack.append(
                _TagFrame(
                    tag=tag,
                    explicit_heading=explicit_heading(tag, attrs),
                )
            )

        def handle_startendtag(
            self,
            tag: str,
            attrs: list[tuple[str, str | None]],
        ) -> None:
            del attrs
            self.handle_self_closing(tag)

        def handle_endtag(self, tag: str) -> None:
            tag = tag.lower()
            matching_index = next(
                (
                    index
                    for index in range(len(tag_stack) - 1, -1, -1)
                    if tag_stack[index].tag == tag
                ),
                None,
            )
            if tag in block_tags or matching_index is None:
                flush()
            if matching_index is not None:
                del tag_stack[matching_index:]
            else:
                # Any stray close makes an active strong ancestry unreliable,
                # including ignored presentation tags such as DIV and SPAN.
                clear_strong_ancestry()

        def handle_data(self, data: str) -> None:
            append_text(data)

        def handle_entityref(self, name: str) -> None:
            append_text(f"&{name};")

        def handle_charref(self, name: str) -> None:
            append_text(f"&#{name};")

        def unknown_decl(self, data: str) -> None:
            if data[:len("CDATA[")].lower() == "cdata[":
                append_text(data[len("CDATA["):])

    parser = StructureParser()
    parser.feed(bounded)
    parser.close()
    flush()
    return lines


def _plain_lines(full_text: str) -> list[str]:
    return [line.text for line in _structured_lines(full_text)]


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


def _trim_structured_to_kam(
    lines: list[StructuredLine],
) -> list[StructuredLine]:
    start = 0
    for index, line in enumerate(lines):
        if _matches_heading(line.text, _KAM_HEADINGS):
            start = index + 1
            break
    end = len(lines)
    for index in range(start, len(lines)):
        if _matches_heading(lines[index].text, _TRAILING_HEADINGS):
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


def _title_evidence_score(value: str) -> int:
    compact = _compact(value)
    topic = any(term in compact for term in _TITLE_TOPIC_TERMS)
    risk = any(term in compact for term in _TITLE_RISK_TERMS)
    return (2 if topic else 0) + (1 if risk else 0)


def _procedure_evidence_score(value: str) -> int:
    compact = _compact(value)
    return sum(term in compact for term in _AUDIT_PROCEDURE_TERMS)


def _has_clear_title_evidence(value: str) -> bool:
    return (
        _title_evidence_score(value) >= 2
        and _procedure_evidence_score(value) == 0
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


def _is_distinct_marker_transition(
    candidate: tuple[str, str] | None,
    current: tuple[str, str] | None,
) -> bool:
    if candidate is None:
        return False
    family, identity = candidate
    if current is not None and family == current[0]:
        return identity != current[1]
    return identity != _INITIAL_MARKER_IDENTITIES[family]


def _discover_title_boundary(
    lines: list[str],
    *,
    lower: int,
    reason_index: int,
    response_owned: bool,
    current_marker: tuple[str, str] | None,
    structured_lines: list[StructuredLine] | None = None,
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
    selected_marker: tuple[str, str] | None = None
    if marker_index is None or marker is None:
        start = _unnumbered_suffix_start(lines, scan_start, reason_index)
        title_values = lines[start:reason_index]
    else:
        family, identity, marked_title = marker
        suffix_start = marker_index + 1
        has_suffix = suffix_start < reason_index
        candidate_marker = (family, identity)
        same_marker = current_marker == candidate_marker
        initial_without_current = (
            current_marker is None
            and identity == _INITIAL_MARKER_IDENTITIES[family]
        )
        title_supported_transition = (
            _has_clear_title_evidence(marked_title)
            and not same_marker
            and not initial_without_current
        )
        is_distinct_matter_marker = (
            not response_owned
            or _is_distinct_marker_transition(candidate_marker, current_marker)
            or title_supported_transition
        )
        marked_wrap = (
            has_suffix
            and is_distinct_matter_marker
            and _is_title_continuation(marked_title, lines[suffix_start])
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
            selected_marker = (family, identity)

    if sum(len(line) for line in lines[start:reason_index]) > MAX_TITLE_BLOCK_CHARS:
        return None
    title = _title_parts(title_values)
    if not title:
        return None
    return _TitleBoundary(
        start=start,
        title=title,
        marker=selected_marker,
        has_explicit_structure=(
            structured_lines is not None
            and any(
                line.is_explicit_heading
                for line in structured_lines[start:reason_index]
            )
        ),
    )


def _discover_heading_frames(lines: list[str]) -> list[_HeadingFrame]:
    frames: list[_HeadingFrame] = []
    reason_heading: int | None = None
    reason_separators: list[int] = []
    response_heading: int | None = None
    response_separators: list[int] = []
    state = "seeking_reason"

    def close_frame(end: int) -> None:
        if reason_heading is None or response_heading is None:
            return
        frames.append(
            _HeadingFrame(
                reason_heading=reason_heading,
                reason_separators=tuple(reason_separators),
                response_heading=response_heading,
                response_separators=tuple(response_separators),
                end=end,
            )
        )

    for index, line in enumerate(lines):
        if _matches_heading(line, _REASON_HEADINGS):
            if state == "reading_reason":
                reason_separators.append(index)
            else:
                if state == "reading_response":
                    close_frame(index)
                reason_heading = index
                reason_separators = []
                response_heading = None
                response_separators = []
                state = "reading_reason"
            continue
        if _matches_heading(line, _RESPONSE_HEADINGS):
            if state == "reading_reason" and reason_heading is not None:
                response_heading = index
                state = "reading_response"
            elif state == "reading_response":
                response_separators.append(index)
    if state == "reading_response":
        close_frame(len(lines))
    return frames


def _last_response_heading(frame: _HeadingFrame) -> int:
    if frame.response_separators:
        return frame.response_separators[-1]
    return frame.response_heading


def _heading_semantic_class(line: str) -> str | None:
    if _matches_heading(line, _REASON_HEADINGS):
        return "reason"
    if _matches_heading(line, _RESPONSE_HEADINGS):
        return "response"
    return None


def _is_semantically_equivalent_heading_pair(
    lines: list[str],
    previous: _HeadingFrame,
    current: _HeadingFrame,
) -> bool:
    return (
        _heading_semantic_class(lines[previous.reason_heading])
        == _heading_semantic_class(lines[current.reason_heading])
        == "reason"
        and _heading_semantic_class(lines[previous.response_heading])
        == _heading_semantic_class(lines[current.response_heading])
        == "response"
    )


def _has_incomplete_explicit_matter(
    lines: list[str],
    structured_lines: list[StructuredLine],
) -> bool:
    heading_frames = _discover_heading_frames(lines)
    owned_reason_headings = {
        index
        for frame in heading_frames
        for index in (frame.reason_heading, *frame.reason_separators)
    }
    response_boundaries = sorted(
        _last_response_heading(frame)
        for frame in heading_frames
    )
    for reason_index, line in enumerate(lines):
        if (
            reason_index in owned_reason_headings
            or not _matches_heading(line, _REASON_HEADINGS)
        ):
            continue
        prior_response = max(
            (
                index
                for index in response_boundaries
                if index < reason_index
            ),
            default=-1,
        )
        candidate = _discover_title_boundary(
            lines,
            lower=prior_response + 1,
            reason_index=reason_index,
            response_owned=prior_response >= 0,
            current_marker=None,
            structured_lines=structured_lines,
        )
        if candidate is not None and candidate.has_explicit_structure:
            return True

    if not response_boundaries:
        return False
    for index in range(response_boundaries[-1] + 1, len(lines)):
        line = structured_lines[index]
        if not line.is_explicit_heading:
            continue
        if (
            _title_marker(line.text) is not None
            or _has_clear_title_evidence(line.text)
        ):
            return True
    return False


def _discover_matter_frames(
    lines: list[str],
    structured_lines: list[StructuredLine] | None = None,
) -> list[_MatterFrame]:
    """Phase 1: discover matter boundaries with a bounded heading state machine."""
    heading_frames = _discover_heading_frames(lines)
    frames: list[_MatterFrame] = []
    previous_response: int | None = None
    for heading_frame in heading_frames:
        lower = previous_response + 1 if previous_response is not None else 0
        title = _discover_title_boundary(
            lines,
            lower=lower,
            reason_index=heading_frame.reason_heading,
            response_owned=previous_response is not None,
            current_marker=frames[-1].marker if frames else None,
            structured_lines=structured_lines,
        )
        if title is None:
            continue
        if (
            frames
            and _is_semantically_equivalent_heading_pair(
                lines,
                frames[-1].heading_frames[-1],
                heading_frame,
            )
            and not title.has_explicit_structure
            and not _has_clear_title_evidence(title.title)
        ):
            frames[-1].heading_frames.append(heading_frame)
            previous_response = _last_response_heading(heading_frame)
            continue
        frames.append(
            _MatterFrame(
                title_start=title.start,
                title=title.title,
                heading_frames=[heading_frame],
                marker=title.marker,
                has_explicit_title_structure=title.has_explicit_structure,
            )
        )
        previous_response = _last_response_heading(heading_frame)
    return frames


def _has_ambiguous_plain_boundary(
    lines: list[str],
    structured_lines: list[StructuredLine],
) -> bool:
    heading_frames = _discover_heading_frames(lines)
    for position in range(1, len(heading_frames)):
        previous = heading_frames[position - 1]
        current = heading_frames[position]
        lower = _last_response_heading(previous) + 1
        if current.reason_heading <= lower:
            continue
        candidate_title = _discover_title_boundary(
            lines,
            lower=lower,
            reason_index=current.reason_heading,
            response_owned=True,
            current_marker=None,
            structured_lines=structured_lines,
        )
        if (
            candidate_title is not None
            and candidate_title.has_explicit_structure
        ):
            continue
        candidate_line = structured_lines[current.reason_heading - 1]
        if candidate_line.is_table_cell:
            return True
        marker_positions = [
            index
            for index in range(lower, current.reason_heading)
            if _title_marker(lines[index]) is not None
        ]
        if marker_positions and current.reason_heading - marker_positions[-1] > 1:
            continue
        candidate = candidate_line.text
        title_score = _title_evidence_score(candidate)
        procedure_score = _procedure_evidence_score(candidate)
        same_literal_pair = (
            _compact(lines[previous.reason_heading])
            == _compact(lines[current.reason_heading])
            and _compact(lines[previous.response_heading])
            == _compact(lines[current.response_heading])
        )
        if not same_literal_pair and not (
            title_score > 0 and procedure_score > 0
        ):
            continue
        if procedure_score > 0 and title_score == 0:
            continue
        return True
    return False


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


def _candidate_items_from_frames(
    lines: list[str],
    frames: list[_MatterFrame],
) -> list[ParsedKamItem | None]:
    items: list[ParsedKamItem | None] = []
    for item_index, frame in enumerate(frames):
        end = (
            frames[item_index + 1].title_start
            if item_index + 1 < len(frames)
            else len(lines)
        )
        matter_lines = lines[frame.title_start:end]
        reason_lines: list[str] = []
        response_lines: list[str] = []
        for heading_frame in frame.heading_frames:
            reason_lines.extend(
                line
                for index, line in enumerate(
                    lines[
                        heading_frame.reason_heading + 1:
                        heading_frame.response_heading
                    ],
                    start=heading_frame.reason_heading + 1,
                )
                if index not in heading_frame.reason_separators
            )
            response_end = min(heading_frame.end, end)
            response_lines.extend(
                line
                for index, line in enumerate(
                    lines[heading_frame.response_heading + 1:response_end],
                    start=heading_frame.response_heading + 1,
                )
                if index not in heading_frame.response_separators
            )
        reason = "\n".join(reason_lines).strip()
        response = "\n".join(response_lines).strip()
        if not reason or not response:
            items.append(None)
            continue
        body = "\n".join(matter_lines).strip()
        items.append(
            ParsedKamItem(
                ordinal=item_index + 1,
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


def _deduplicate_items(
    items: list[ParsedKamItem],
) -> list[ParsedKamItem]:
    deduplicated: list[ParsedKamItem] = []
    previous_signature: tuple[object, ...] | None = None
    for item in items:
        signature = (
            _compact(item.title),
            item.reason_text,
            item.audit_response_text,
            tuple(item.related_note_references),
            item.full_body_hash,
        )
        if signature == previous_signature:
            continue
        deduplicated.append(
            replace(item, ordinal=len(deduplicated) + 1)
        )
        previous_signature = signature
    return deduplicated


def parse_kam_items(full_text: str) -> KamParseOutcome:
    """Return KAM items with an explicit completeness/ambiguity outcome."""
    try:
        input_limitations = _input_structure_limitations(full_text)
        if input_limitations:
            return KamParseOutcome(
                items=[],
                status="error",
                limitations=input_limitations,
            )
        structured_lines = _trim_structured_to_kam(
            _structured_lines(full_text)
        )
        lines = [line.text for line in structured_lines]
        if not lines:
            return KamParseOutcome(
                items=[],
                status="no_kam",
                limitations=[],
            )
        if _has_ambiguous_plain_boundary(lines, structured_lines):
            return KamParseOutcome(
                items=[],
                status="ambiguous",
                limitations=["ambiguous_boundary"],
            )
        if _has_incomplete_explicit_matter(lines, structured_lines):
            return KamParseOutcome(
                items=[],
                status="error",
                limitations=["incomplete_kam_structure"],
            )
        frames = _discover_matter_frames(lines, structured_lines)
        candidate_items = _candidate_items_from_frames(lines, frames)
        explicit_frame_count = sum(
            frame.has_explicit_title_structure
            for frame in frames
        )
        valid_explicit_frame_count = sum(
            frame.has_explicit_title_structure and item is not None
            for frame, item in zip(frames, candidate_items, strict=True)
        )
        if valid_explicit_frame_count != explicit_frame_count:
            return KamParseOutcome(
                items=[],
                status="error",
                limitations=["incomplete_kam_structure"],
            )
        items = _deduplicate_items(
            [item for item in candidate_items if item is not None]
        )
        if items:
            return KamParseOutcome(
                items=items,
                status="complete",
                limitations=[],
            )
        has_reason = any(
            _matches_heading(line, _REASON_HEADINGS)
            for line in lines
        )
        has_response = any(
            _matches_heading(line, _RESPONSE_HEADINGS)
            for line in lines
        )
        return KamParseOutcome(
            items=[],
            status="error" if has_reason or has_response else "no_kam",
            limitations=(
                ["incomplete_kam_structure"]
                if has_reason or has_response
                else []
            ),
        )
    except Exception as exc:
        return KamParseOutcome(
            items=[],
            status="error",
            limitations=[f"parser_error:{type(exc).__name__}"],
        )


def extract_kam_items(full_text: str) -> list[ParsedKamItem]:
    """Extract complete, matter-level KAMs from a cached filing body.

    Ambiguous or incomplete bodies fail closed as an empty list. Call
    :func:`parse_kam_items` when the parse status and limitations are needed.
    """
    return parse_kam_items(full_text).items
