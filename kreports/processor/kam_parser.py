"""Deterministic parser for full-body key audit matters."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha1
from html import escape, unescape
from html.parser import HTMLParser
import re


PARSER_VERSION = "v1"
MAX_INPUT_CHARS = 2_000_000
_PARSER_TITLE_TAG = "kam-title"
_PARSER_SUPPRESSED_RAW_TAG = "kam-parser-suppressed-raw-v1"
_TITLE_TAG_RE = re.compile(r"<(?P<close>/?)title(?=[\s/>])", re.IGNORECASE)
_TAG_NAME_RE = re.compile(
    r"<(?P<close>/)?(?P<tag>[A-Za-z][A-Za-z0-9:_-]*)(?=[\s/>])"
)
_SELF_CLOSING_TAG_RE = re.compile(r"/\s*>$")
_RAW_TEXT_CLOSE_RES = {
    "script": re.compile(r"</script\s*>", re.IGNORECASE),
    "style": re.compile(r"</style\s*>", re.IGNORECASE),
}

_TITLE_MARKER_RE = re.compile(
    r"^\s*(?:(?P<arabic>\(?\d{1,2}\)?)[.)]|"
    r"(?P<korean>[가-하])\s*[.)]|"
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
    "핵심감사사항으로선정된이유",
    "핵심감사사항으로결정된이유",
    "핵심감사항으로결정한이유",
    "whythematterwasdeterminedtobeakeyauditmatter",
    "whythematterwasconsideredtobeoneofthemostsignificantmattersintheaudit",
    "whythematterwasconsideredtobeoneofmostsignificanceintheaudit",
    "whythematterwasconsideredsignificant",
)
_RESPONSE_HEADINGS = (
    "감사에서다루어진방법",
    "핵심감사사항이감사에서다루어진방법",
    "감사에서다뤄진방법",
    "핵심감사사항이감사에서다뤄진방법",
    "핵심감사사항에대응하기위한우리의감사절차는다음을포함하고있습니다.",
    "감사인이수행한주요절차",
    "이와관련하여우리가수행한주요감사절차는다음과같습니다.",
    "이와관련하여우리가수행한주요한감사절차는다음과같습니다.",
    "이와관련하여우리가부문감사인을참여시켜수행한주요감사절차는다음과같습니다.",
    "우리가수행한주요감사절차는다음과같습니다.",
    "감사인의대응",
    "howthematterwasaddressedintheaudit",
    "auditresponse",
)
_GENERIC_MAJOR_PROCEDURE_HEADING = (
    "우리가수행한주요감사절차는다음과같습니다.",
)
_TRAILING_HEADINGS = (
    "재무제표감사에대한감사인의책임",
    "연결재무제표감사에대한감사인의책임",
    "감사인의책임",
    "재무제표에대한경영진",
    "연결재무제표에대한경영진",
    "재무제표에대한경영진과지배기구의책임",
    "연결재무제표에대한경영진과지배기구의책임",
    "경영진과지배기구의책임",
    "기타사항",
    "강조사항",
    "첨부재무제표",
    "별첨재무제표",
    "auditor'sresponsibilitiesfortheauditof thefinancialstatements".replace(" ", ""),
    "auditorresponsibilitiesfortheauditof thefinancialstatements".replace(" ", ""),
)
_COLLAPSED_HEADING_LABELS = (
    ("핵심감사사항으로선정한이유", "핵심감사사항으로 선정한 이유"),
    ("핵심감사사항으로결정한이유", "핵심감사사항으로 결정한 이유"),
    ("핵심감사사항으로선정된이유", "핵심감사사항으로 선정된 이유"),
    ("핵심감사사항으로결정된이유", "핵심감사사항으로 결정된 이유"),
    ("핵심감사항으로결정한이유", "핵심감사사항으로 결정한 이유"),
    (
        "핵심감사사항이감사에서다루어진방법",
        "핵심감사사항이 감사에서 다루어진 방법",
    ),
    (
        "핵심감사사항이감사에서다뤄진방법",
        "핵심감사사항이 감사에서 다뤄진 방법",
    ),
    (
        "핵심감사사항에대응하기위한우리의감사절차는다음을포함하고있습니다.",
        "핵심감사사항에 대응하기 위한 우리의 감사절차는 다음을 포함하고 있습니다.",
    ),
    (
        "이와관련하여우리가수행한주요한감사절차는다음과같습니다.",
        "이와 관련하여 우리가 수행한 주요한 감사절차는 다음과 같습니다.",
    ),
    (
        "이와관련하여우리가수행한주요감사절차는다음과같습니다.",
        "이와 관련하여 우리가 수행한 주요 감사절차는 다음과 같습니다.",
    ),
    (
        "이와관련하여우리가부문감사인을참여시켜수행한주요감사절차는다음과같습니다.",
        "이와 관련하여 우리가 부문감사인을 참여시켜 수행한 주요 감사절차는 다음과 같습니다.",
    ),
    (
        "우리가수행한주요감사절차는다음과같습니다.",
        "우리가 수행한 주요 감사절차는 다음과 같습니다.",
    ),
    ("감사에서다루어진방법", "감사에서 다루어진 방법"),
    ("감사에서다뤄진방법", "감사에서 다뤄진 방법"),
    (
        "연결재무제표감사에대한감사인의책임",
        "연결재무제표감사에 대한 감사인의 책임",
    ),
    (
        "재무제표감사에대한감사인의책임",
        "재무제표감사에 대한 감사인의 책임",
    ),
    (
        "연결재무제표에대한경영진과지배기구의책임",
        "연결재무제표에 대한 경영진과 지배기구의 책임",
    ),
    (
        "재무제표에대한경영진과지배기구의책임",
        "재무제표에 대한 경영진과 지배기구의 책임",
    ),
    ("기타사항", "기타사항"),
    ("강조사항", "강조사항"),
    ("핵심감사사항", "핵심감사사항"),
)
_COLLAPSED_HEADING_PATTERN = re.compile(
    "|".join(
        (
            f"(?P<heading_{index}>"
            + r"\s*".join(re.escape(character) for character in compact)
            + ")"
        )
        for index, (compact, _label) in enumerate(
            sorted(
                _COLLAPSED_HEADING_LABELS,
                key=lambda value: len(value[0]),
                reverse=True,
            )
        )
    ),
    flags=re.IGNORECASE,
)
_COLLAPSED_HEADING_ORDER = tuple(
    sorted(
        _COLLAPSED_HEADING_LABELS,
        key=lambda value: len(value[0]),
        reverse=True,
    )
)
_COLLAPSED_INTRO_ENDINGS = (
    "별도의의견을제공하지는않습니다.",
    "별도의의견을제공하지않습니다.",
    "핵심감사사항으로결정하였습니다.",
    "핵심감사사항으로식별하였습니다.",
)
_COLLAPSED_TITLE_ENDING_RE = re.compile(
    r"(?:적정성|회수가능성|발생사실|실재성|손상평가|손상검사|"
    r"공정가치측정|매수가격배분|평가|검사|인식|측정|배분|표시)"
)
_OMITTED_REASON_RISK_TITLE_ENDING_RE = re.compile(
    r"(?:손상평가|손상검사|공정가치측정|매수가격배분|회수가능성)\s*$"
)
_COLLAPSED_TITLE_MARKER_START_RE = re.compile(
    r"(?:^|\s)(?:\(?\d{1,2}\)?[.)]|[가-하]\s*[.)]|[IVX]{1,5}[.)])\s*",
    flags=re.IGNORECASE,
)
_COLLAPSED_FIELD_MARKER_RE = re.compile(
    r"^\s*(?:\(?\d{1,2}\)?|[가-하]\s*[.)]|[IVX]{1,5}[.)])\s*$",
    flags=re.IGNORECASE,
)
_COLLAPSED_REASON_SUBJECT_RE = re.compile(
    r"\s+(?=(?:연결)?회사는\s|당사는\s|그룹은\s)"
)
_COLLAPSED_REASON_SUBJECT_START_RE = re.compile(
    r"(?:(?:연결)?회사는|당사는|그룹은)\s"
)
_INTRO_TAIL_RISK_TITLE_RE = re.compile(
    r"(?:손상\s*평가|손상\s*검사|공정가치\s*측정|매수가격\s*배분|회수\s*가능성)"
)
_INTRO_TAIL_YEAR_CUE_RE = re.compile(r"^20\d{2}년(?:\s|$)")
_NUMBERED_OMITTED_REASON_SUBJECT_RE = re.compile(
    r"\s+(?=(?:(?:연결)?회사는|연결실체는|당사는|그룹은|매출은)\s)"
)
_NUMBERED_OMITTED_REASON_TITLE_ENDING_RE = re.compile(
    r"(?:손상|평가|검사|인식|측정|회수가능성)\s*$"
)
_OMITTED_REASON_TITLE_NOTE_RE = re.compile(
    r"^(?P<title>.+?(?:손상평가|손상검사|공정가치측정|매수가격배분|회수가능성))\s+"
    r"(?P<reason>(?:연결)?재무제표\s*(?:에\s*대한\s*)?주석\s*[제]?\s*\d+.*)$"
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
    "사업결합",
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
    is_empty_explicit_heading: bool = False


@dataclass(frozen=True)
class _StructureParseResult:
    lines: list[StructuredLine]
    limitations: list[str]


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
class _BoundaryClassification:
    start: int
    classification: str


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
    limitations: list[str] = []
    if len(value) > MAX_INPUT_CHARS:
        limitations.append("input_truncated")
    return limitations


def _tag_end(value: str, start: int) -> int | None:
    """Return the exclusive end of a tag without treating quoted ``>`` as syntax."""
    quote: str | None = None
    cursor = start + 1
    value_length = len(value)
    while cursor < value_length:
        character = value[cursor]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in {"\"", "'"}:
            quote = character
        elif character == ">":
            return cursor + 1
        cursor += 1
    return None


def _doctype_end(value: str, start: int) -> int | None:
    """Return the exclusive end of a DOCTYPE, respecting quotes and subsets."""
    quote: str | None = None
    subset_depth = 0
    cursor = start + len("<!doctype")
    value_length = len(value)
    while cursor < value_length:
        character = value[cursor]
        if quote is not None:
            if character == quote:
                quote = None
        elif value.startswith("<!--", cursor):
            comment_end = value.find("-->", cursor + 4)
            if comment_end < 0:
                return None
            cursor = comment_end + 3
            continue
        elif value.startswith("<?", cursor):
            pi_end = value.find("?>", cursor + 2)
            if pi_end < 0:
                return None
            cursor = pi_end + 2
            continue
        elif character in {"\"", "'"}:
            quote = character
        elif character == "[":
            subset_depth += 1
        elif character == "]" and subset_depth:
            subset_depth -= 1
        elif character == ">" and subset_depth == 0:
            return cursor + 1
        cursor += 1
    return None


def _htmlparser_safe_markup(value: str) -> tuple[str, list[str]]:
    """Preserve DART pseudo-HTML boundaries before feeding ``HTMLParser``.

    ``HTMLParser`` treats HTML ``TITLE`` as a CDATA element, although DART
    documents frequently use it as a structural wrapper containing nested
    pseudo-HTML.  The adapter changes only that token name and converts valid
    XML-style CDATA payloads to literal text.  It also suppresses script and
    style raw text from structured evidence extraction; the original document
    remains available to the caller outside this intermediate markup.
    """
    limitations: list[str] = []
    parts: list[str] = []
    cursor = 0
    value_length = len(value)
    while cursor < value_length:
        start = value.find("<", cursor)
        if start < 0:
            parts.append(value[cursor:value_length])
            break
        parts.append(value[cursor:start])
        if value.startswith("<!--", start):
            end = value.find("-->", start + 4)
            if end < 0:
                parts.append(value[start:value_length])
                break
            parts.append(value[start:end + 3])
            cursor = end + 3
            continue
        if value.startswith("<?", start):
            end = value.find("?>", start + 2)
            if end < 0:
                parts.append(value[start:value_length])
                break
            parts.append(value[start:end + 2])
            cursor = end + 2
            continue
        if value[start:start + 9].lower() == "<!doctype":
            end = _doctype_end(value, start)
            if end is None:
                limitations.append("malformed_doctype")
                parts.append(f"<{_PARSER_SUPPRESSED_RAW_TAG}>")
                parts.append(f"</{_PARSER_SUPPRESSED_RAW_TAG}>")
                cursor = value_length
                break
            parts.append(f"<{_PARSER_SUPPRESSED_RAW_TAG}>")
            parts.append(f"</{_PARSER_SUPPRESSED_RAW_TAG}>")
            cursor = end
            continue
        tag_match = _TAG_NAME_RE.match(value, start)
        tag_end = _tag_end(value, start) if tag_match else None
        if tag_match and tag_end is not None:
            tag = tag_match.group("tag").lower()
            is_closing = tag_match.group("close") is not None
            raw_tag = value[start:tag_end]
            if (
                tag in _RAW_TEXT_CLOSE_RES
                and not is_closing
                and not _SELF_CLOSING_TAG_RE.search(raw_tag)
            ):
                raw_close = _RAW_TEXT_CLOSE_RES[tag].search(value, tag_end)
                if raw_close is None:
                    parts.append(f"<{_PARSER_SUPPRESSED_RAW_TAG}>")
                    parts.append(f"</{_PARSER_SUPPRESSED_RAW_TAG}>")
                    cursor = value_length
                    break
                parts.append(f"<{_PARSER_SUPPRESSED_RAW_TAG}>")
                parts.append(f"</{_PARSER_SUPPRESSED_RAW_TAG}>")
                cursor = raw_close.end()
                continue
            parts.append(raw_tag)
            cursor = tag_end
            continue
        if value[start:start + 9].lower() == "<![cdata[":
            end = value.find("]]>", start + 9)
            if end < 0:
                limitations.append("malformed_cdata")
                parts.append(escape(value[start:value_length]))
                cursor = value_length
                break
            parts.append(escape(value[start + 9:end]))
            cursor = end + 3
            continue
        if value[start:start + 8].lower() == "<![cdata":
            limitations.append("malformed_cdata")
        parts.append("<")
        cursor = start + 1
    normalized = _TITLE_TAG_RE.sub(
        lambda match: f"<{match.group('close')}{_PARSER_TITLE_TAG}",
        "".join(parts),
    )
    return normalized, list(dict.fromkeys(limitations))


def _structured_lines(full_text: str) -> _StructureParseResult:
    bounded = (full_text or "")[:MAX_INPUT_CHARS]
    bounded, markup_limitations = _htmlparser_safe_markup(bounded)
    lines: list[StructuredLine] = []
    limitations: list[str] = list(markup_limitations)
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

    def emit_empty_explicit_boundary(frame_index: int) -> None:
        nonlocal block_id, blank_before
        frame = tag_stack[frame_index]
        if not frame.explicit_heading or frame.emitted_content:
            return
        tag_path = tuple(
            candidate.tag
            for candidate in tag_stack[:frame_index + 1]
        )
        lines.append(
            StructuredLine(
                text="",
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
                is_explicit_heading=True,
                is_empty_explicit_heading=True,
            )
        )
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
            if tag == _PARSER_TITLE_TAG:
                tag = "title"
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
            if tag.lower() == _PARSER_TITLE_TAG:
                tag = "title"
            self.handle_self_closing(tag)

        def handle_endtag(self, tag: str) -> None:
            tag = tag.lower()
            if tag == _PARSER_TITLE_TAG:
                tag = "title"
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
                emit_empty_explicit_boundary(matching_index)
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

        def handle_comment(self, data: str) -> None:
            del data

        def handle_pi(self, data: str) -> None:
            del data

        def unknown_decl(self, data: str) -> None:
            if data[:len("CDATA")].lower() != "cdata":
                return
            if data[:len("CDATA[")].lower() == "cdata[":
                append_text(data[len("CDATA["):])
                return
            if "malformed_cdata" not in limitations:
                limitations.append("malformed_cdata")

    parser = StructureParser()
    parser.feed(bounded)
    unconsumed = parser.rawdata
    parser_context = parser.cdata_elem
    parser.close()
    if (
        parser_context is None
        and re.match(r"<!\[cdata", unconsumed, flags=re.IGNORECASE)
        and "malformed_cdata" not in limitations
    ):
        limitations.append("malformed_cdata")
    flush()
    return _StructureParseResult(
        lines=lines,
        limitations=limitations,
    )


def _plain_lines(full_text: str) -> list[str]:
    return [
        line.text
        for line in _structured_lines(full_text).lines
    ]


def _matches_heading(line: str, headings: tuple[str, ...]) -> bool:
    compact = _compact(line).strip(":-–—()[]").lstrip("-·•ㆍ")
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


def _has_separator_title_evidence(value: str) -> bool:
    """Accept a separator-marked title only with matter, not procedure, evidence."""
    return _has_clear_title_evidence(value) or (
        _title_evidence_score(value) >= 1
        and _OMITTED_REASON_RISK_TITLE_ENDING_RE.search(value) is not None
    )


def _has_collapsed_title_ending(value: str) -> bool:
    normalized = _normalize_title(value)
    match = _COLLAPSED_TITLE_ENDING_RE.search(normalized)
    return match is not None and match.end() == len(normalized)


def _intro_tail_risk_title_candidate(value: str) -> tuple[str, str] | None:
    for risk_match in _INTRO_TAIL_RISK_TITLE_RE.finditer(value):
        title = value[:risk_match.end()].strip()
        reason = value[risk_match.end():].strip()
        if (
            len(title) <= 80
            and not title.endswith((".", "。"))
            and _title_evidence_score(title) >= 1
            and _INTRO_TAIL_YEAR_CUE_RE.match(reason) is not None
        ):
            return title, reason
    return None


def _ends_with_collapsed_intro(value: str) -> bool:
    return any(
        position == len(value.rstrip())
        for ending in _COLLAPSED_INTRO_ENDINGS
        for position in _compact_span_ends(value, ending)
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
    marker_reason_separator = False
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
        separator_padding = (
            has_suffix
            and all(
                re.fullmatch(r"\s*[-–—]+\s*", line) is not None
                for line in lines[suffix_start:reason_index]
            )
        )
        marker_reason_separator = lines[reason_index].lstrip().startswith(
            ("-", "–", "—")
        )
        marker_separator_transition = (
            (separator_padding or marker_reason_separator)
            and _has_separator_title_evidence(marked_title)
        )
        if (
            response_owned
            and has_suffix
            and not marked_wrap
            and not (
                is_distinct_matter_marker
                and marker_separator_transition
            )
        ):
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
        if structured_lines is not None:
            empty_explicit_positions = [
                index
                for index in range(scan_start, reason_index)
                if structured_lines[index].is_empty_explicit_heading
            ]
            if empty_explicit_positions:
                return _TitleBoundary(
                    start=empty_explicit_positions[-1],
                    title="",
                    marker=None,
                    has_explicit_structure=True,
                )
        return None
    return _TitleBoundary(
        start=start,
        title=title,
        marker=selected_marker,
        has_explicit_structure=(
            (
                selected_marker is not None
                and marker_separator_transition
            )
            or (
                structured_lines is not None
                and any(
                    line.is_explicit_heading
                    for line in structured_lines[start:reason_index]
                )
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
            preceding = lines[lower:heading_frame.reason_heading]
            marked_tail = [
                (index, _title_marker(line))
                for index, line in enumerate(preceding)
                if _title_marker(line) is not None
            ]
            final_marker = marked_tail[-1] if marked_tail else None
            if (
                frames
                and _is_semantically_equivalent_heading_pair(
                    lines,
                    frames[-1].heading_frames[-1],
                    heading_frame,
                )
                and final_marker is not None
                and final_marker[1] is not None
                and not _has_separator_title_evidence(
                    final_marker[1][2]
                )
                and all(
                    re.fullmatch(r"\s*[-–—]+\s*", line) is not None
                    for line in preceding[final_marker[0] + 1:]
                )
            ):
                frames[-1].heading_frames.append(heading_frame)
                previous_response = _last_response_heading(heading_frame)
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


def _ambiguous_plain_boundary_starts(
    lines: list[str],
    structured_lines: list[StructuredLine],
) -> set[int]:
    ambiguous_starts: set[int] = set()
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
            ambiguous_starts.add(
                candidate_title.start
                if candidate_title is not None
                else current.reason_heading - 1
            )
            continue
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
        ambiguous_starts.add(
            candidate_title.start
            if candidate_title is not None
            else current.reason_heading - 1
        )
    return ambiguous_starts


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
        if not frame.title or not reason or not response:
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


def _classify_matter_boundaries(
    lines: list[str],
    structured_lines: list[StructuredLine],
    frames: list[_MatterFrame],
    candidate_items: list[ParsedKamItem | None],
    ambiguous_starts: set[int],
) -> list[_BoundaryClassification]:
    classifications: list[_BoundaryClassification] = []
    for index, (frame, item) in enumerate(
        zip(frames, candidate_items, strict=True)
    ):
        if item is not None:
            classification = (
                "ambiguous"
                if frame.title_start in ambiguous_starts
                else "valid"
            )
        elif (
            frame.title_start in ambiguous_starts
            and structured_lines[frame.title_start].is_table_cell
            and not structured_lines[frame.title_start].is_explicit_heading
        ):
            classification = "ambiguous"
        else:
            next_start = (
                frames[index + 1].title_start
                if index + 1 < len(frames)
                else None
            )
            split_is_ambiguous = next_start in ambiguous_starts
            complete_without_split = (
                _candidate_items_from_frames(lines, [frame])[0] is not None
            )
            classification = (
                "artifact"
                if (
                    split_is_ambiguous
                    and complete_without_split
                )
                else "incomplete"
            )
        classifications.append(
            _BoundaryClassification(
                start=frame.title_start,
                classification=classification,
            )
        )
    return classifications


def parse_kam_items(full_text: str) -> KamParseOutcome:
    """Return KAM items with an explicit completeness/ambiguity outcome."""
    try:
        input_limitations = _input_structure_limitations(full_text)
        structure = _structured_lines(full_text)
        structure_limitations = list(
            dict.fromkeys(
                [
                    *input_limitations,
                    *structure.limitations,
                ]
            )
        )
        if structure_limitations:
            return KamParseOutcome(
                items=[],
                status="error",
                limitations=structure_limitations,
            )
        structured_lines = _trim_structured_to_kam(
            structure.lines
        )
        lines = [line.text for line in structured_lines]
        if not lines:
            return KamParseOutcome(
                items=[],
                status="no_kam",
                limitations=[],
            )
        if _has_incomplete_explicit_matter(lines, structured_lines):
            return KamParseOutcome(
                items=[],
                status="error",
                limitations=["incomplete_kam_structure"],
            )
        frames = _discover_matter_frames(lines, structured_lines)
        candidate_items = _candidate_items_from_frames(lines, frames)
        ambiguous_starts = _ambiguous_plain_boundary_starts(
            lines,
            structured_lines,
        )
        boundary_classifications = _classify_matter_boundaries(
            lines,
            structured_lines,
            frames,
            candidate_items,
            ambiguous_starts,
        )
        if any(
            boundary.classification == "incomplete"
            for boundary in boundary_classifications
        ):
            return KamParseOutcome(
                items=[],
                status="error",
                limitations=["incomplete_kam_structure"],
            )
        if ambiguous_starts or any(
            boundary.classification == "artifact"
            for boundary in boundary_classifications
        ):
            return KamParseOutcome(
                items=[],
                status="ambiguous",
                limitations=["ambiguous_boundary"],
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


def _compact_span_ends(value: str, compact_needle: str) -> list[int]:
    """Return every source offset ending a whitespace-insensitive marker."""
    compact_chars: list[str] = []
    source_indexes: list[int] = []
    for index, character in enumerate(value):
        if character.isspace():
            continue
        compact_chars.append(character.lower())
        source_indexes.append(index)
    compact_value = "".join(compact_chars)
    start = 0
    ends: list[int] = []
    while True:
        position = compact_value.find(compact_needle.lower(), start)
        if position < 0:
            return ends
        final = position + len(compact_needle) - 1
        if final < len(source_indexes):
            ends.append(source_indexes[final] + 1)
        start = position + 1


def _compact_span_end(value: str, compact_needle: str) -> int | None:
    ends = _compact_span_ends(value, compact_needle)
    return ends[-1] if ends else None


def _split_collapsed_headings(value: str) -> list[str]:
    parts: list[str] = []
    cursor = 0
    for match in _COLLAPSED_HEADING_PATTERN.finditer(value):
        heading_index = int((match.lastgroup or "").removeprefix("heading_"))
        compact, label = _COLLAPSED_HEADING_ORDER[heading_index]
        if compact == "핵심감사사항":
            suffix = value[match.end():].lstrip()
            if suffix.startswith(
                ("은", "는", "이", "가", "을", "를", "으로", "에")
            ):
                continue
        prefix = value[cursor:match.start()].strip()
        if prefix:
            parts.append(prefix)
        parts.append(label)
        cursor = match.end()
    suffix = value[cursor:].strip()
    if suffix:
        parts.append(suffix)
    return parts


def _refine_collapsed_marked_title(value: str) -> str:
    if len(value) <= 120:
        return value
    cue = len(value)
    for phrase in (
        "관련된 회계정책",
        "관련 내용은",
        "재무제표에 대한 주석",
        "연결재무제표에 대한 주석",
    ):
        position = value.find(phrase, 5)
        if position >= 0:
            cue = min(cue, position)
    if cue == len(value):
        return value
    prefix = value[:cue]
    endings = list(_COLLAPSED_TITLE_ENDING_RE.finditer(prefix))
    if not endings:
        return value
    return prefix[:endings[-1].end()].strip()


def _collapsed_title_candidate(value: str) -> tuple[str, str] | None:
    markers = list(_COLLAPSED_TITLE_MARKER_START_RE.finditer(value))
    for marker in reversed(markers):
        prefix = value[:marker.start()].strip()
        title = _refine_collapsed_marked_title(
            value[marker.start():].strip()
        )
        parsed_marker = _title_marker(title)
        if parsed_marker is not None and len(title) <= MAX_TITLE_BLOCK_CHARS:
            return prefix, title

    cut = -1
    for ending in _COLLAPSED_INTRO_ENDINGS:
        position = _compact_span_end(value, ending)
        if position is not None:
            cut = max(cut, position)
    if cut >= 0:
        prefix = value[:cut].strip()
        title = value[cut:].strip()
        if (
            title
            and len(title) <= 200
            and _has_clear_title_evidence(title)
        ):
            return prefix, title
        return None
    if len(value) <= 200 and _has_clear_title_evidence(value):
        return "", value
    return None


def _drop_embedded_collapsed_sections(
    lines: list[str],
    *,
    kam_start: int,
) -> None:
    """Remove an unrelated exact-heading span inside a complete KAM frame."""
    response_indexes = [
        index
        for index in range(kam_start + 1, len(lines))
        if _matches_heading(lines[index], _RESPONSE_HEADINGS)
    ]
    for response_index in reversed(response_indexes):
        reason_index = next(
            (
                index
                for index in range(response_index - 1, kam_start, -1)
                if _matches_heading(lines[index], _REASON_HEADINGS)
            ),
            None,
        )
        if reason_index is None:
            continue
        unrelated_index = next(
            (
                index
                for index in range(reason_index + 1, response_index)
                if _matches_heading(
                    lines[index],
                    ("강조사항", "기타사항"),
                )
            ),
            None,
        )
        if unrelated_index is not None:
            del lines[unrelated_index:response_index]


def _has_embedded_collapsed_section(full_text: str) -> bool:
    lines = [
        part
        for line in _structured_lines(full_text).lines
        for part in _split_collapsed_headings(line.text)
    ]
    for response_index, line in enumerate(lines):
        if not _matches_heading(line, _RESPONSE_HEADINGS):
            continue
        reason_index = next(
            (
                index
                for index in range(response_index - 1, -1, -1)
                if _matches_heading(lines[index], _REASON_HEADINGS)
            ),
            None,
        )
        if reason_index is not None and any(
            _matches_heading(
                lines[index],
                ("강조사항", "기타사항"),
            )
            for index in range(reason_index + 1, response_index)
        ):
            return True
    return False


def _inject_numbered_omitted_collapsed_reason_headings(
    lines: list[str],
    *,
    kam_start: int,
    kam_end: int,
    response_indexes: list[int],
) -> bool:
    """Recover explicit numbered matters with one response frame each.

    This is deliberately separate from the unmarked single-matter fallback:
    every matter must have a consecutive Arabic marker, a short title with
    clear evidence, an inline reason, and its own explicit response heading.
    """
    if len(response_indexes) < 2:
        return False

    matters: list[tuple[int, str, str, str | None]] = []
    lower = kam_start
    for response_index in response_indexes:
        candidates: list[tuple[int, str, str, str]] = []
        for index in range(lower + 1, response_index):
            marker = _TITLE_MARKER_RE.match(lines[index])
            if marker is None or marker.group("arabic") is None:
                continue
            identity = marker.group("arabic").strip("()")
            title_and_reason = _normalize_title(marker.group("title"))
            subject = _NUMBERED_OMITTED_REASON_SUBJECT_RE.search(
                title_and_reason
            )
            if subject is None:
                title = title_and_reason
                reason_parts = lines[index + 1:response_index]
                inline_reason = None
            else:
                title = _normalize_title(title_and_reason[:subject.start()])
                inline_reason = title_and_reason[subject.end():].strip()
                reason_parts = [inline_reason, *lines[index + 1:response_index]]
            if (
                len(title) > 80
                or title.endswith((".", "。"))
                or (
                    not _has_clear_title_evidence(title)
                    and _NUMBERED_OMITTED_REASON_TITLE_ENDING_RE.search(title)
                    is None
                )
                or len(_compact(" ".join(reason_parts))) < 50
                or any(
                    _matches_heading(
                        line,
                        _KAM_HEADINGS
                        + _REASON_HEADINGS
                        + _RESPONSE_HEADINGS
                        + _TRAILING_HEADINGS,
                    )
                    for line in reason_parts
                )
            ):
                continue
            candidates.append((index, identity, title, inline_reason))
        if len(candidates) != 1:
            return False
        matters.append(candidates[0])
        lower = response_index

    if [identity for _index, identity, _title, _reason in matters] != [
        str(number) for number in range(1, len(matters) + 1)
    ]:
        return False
    for index, _identity, title, inline_reason in reversed(matters):
        replacement = [
            f"<TITLE>{escape(title)}</TITLE>",
            "핵심감사사항으로 결정된 이유",
        ]
        if inline_reason:
            replacement.append(inline_reason)
        lines[index:index + 1] = replacement
    return True


def _inject_omitted_collapsed_reason_heading(
    lines: list[str],
    *,
    kam_start: int,
    kam_end: int,
) -> None:
    """Recover a single matter only when an explicit short title survives."""
    if any(
        _matches_heading(lines[index], _REASON_HEADINGS)
        for index in range(kam_start + 1, kam_end)
    ):
        return
    response_indexes = [
        index
        for index in range(kam_start + 1, kam_end)
        if _matches_heading(lines[index], _RESPONSE_HEADINGS)
        and not (
            _matches_heading(
                lines[index], _GENERIC_MAJOR_PROCEDURE_HEADING
            )
            and any(
                _matches_heading(lines[prior], _RESPONSE_HEADINGS)
                for prior in range(kam_start + 1, index)
            )
        )
    ]
    if len(response_indexes) > 1:
        _inject_numbered_omitted_collapsed_reason_headings(
            lines,
            kam_start=kam_start,
            kam_end=kam_end,
            response_indexes=response_indexes,
        )
        return
    if len(response_indexes) != 1:
        return
    response_index = response_indexes[0]
    candidate_index = None
    candidate_value = None
    candidate_inline_reason = None
    for index in range(kam_start + 1, response_index):
        inline_reason = None
        candidate = None
        subject_led_standalone_title = False
        note_title = _OMITTED_REASON_TITLE_NOTE_RE.match(lines[index])
        if note_title is not None:
            candidate = ("", note_title.group("title"))
            inline_reason = note_title.group("reason")
        intro_cuts = sorted(
            {
                position
                for ending in _COLLAPSED_INTRO_ENDINGS
                for position in _compact_span_ends(lines[index], ending)
            }
        )
        for intro_cut in intro_cuts if candidate is None else ():
            intro_tail = lines[index][intro_cut:].strip()
            risk_candidate = _intro_tail_risk_title_candidate(intro_tail)
            if risk_candidate is not None:
                candidate = (lines[index][:intro_cut].strip(), risk_candidate[0])
                inline_reason = risk_candidate[1]
                break
            subject = _COLLAPSED_REASON_SUBJECT_RE.search(intro_tail)
            if subject is None:
                continue
            inline_title = intro_tail[:subject.start()].strip()
            if (
                len(inline_title) > 80
                or inline_title.endswith((".", "。"))
                or _title_evidence_score(inline_title) < 3
            ):
                continue
            candidate = (lines[index][:intro_cut].strip(), inline_title)
            inline_reason = intro_tail[subject.end():].strip()
            break
        if candidate is None:
            subject = _COLLAPSED_REASON_SUBJECT_RE.search(lines[index])
            if subject is not None:
                inline_title = lines[index][:subject.start()].strip()
                candidate = (
                    ("", inline_title)
                    if (
                        len(inline_title) <= 80
                        and not inline_title.endswith((".", "。"))
                        and _title_evidence_score(inline_title) >= 3
                    )
                    else None
                )
                if candidate is not None:
                    inline_reason = lines[index][subject.end():].strip()
            else:
                candidate = None
        if (
            candidate is None
            and index > kam_start + 1
            and _ends_with_collapsed_intro(lines[index - 1])
        ):
            risk_candidate = _intro_tail_risk_title_candidate(lines[index])
            if risk_candidate is not None:
                candidate = ("", risk_candidate[0])
                inline_reason = risk_candidate[1]
        if candidate is None:
            candidate = _collapsed_title_candidate(lines[index])
        if (
            candidate is None
            and len(lines[index]) <= 80
            and not lines[index].endswith((".", "。"))
            and _has_collapsed_title_ending(lines[index])
            and index + 1 < response_index
            and _COLLAPSED_REASON_SUBJECT_START_RE.match(
                lines[index + 1]
            )
            is not None
        ):
            candidate = ("", lines[index])
            subject_led_standalone_title = True
        if candidate is None:
            cut = -1
            for ending in _COLLAPSED_INTRO_ENDINGS:
                position = _compact_span_end(lines[index], ending)
                if position is not None:
                    cut = max(cut, position)
            if cut >= 0:
                candidate = (
                    lines[index][:cut].strip(),
                    lines[index][cut:].strip(),
                )
        if (
            candidate is None
            and len(lines[index]) <= 80
            and not lines[index].endswith((".", "。"))
            and (
                _title_evidence_score(lines[index]) >= 3
                or (
                    _title_evidence_score(lines[index]) >= 1
                    and _OMITTED_REASON_RISK_TITLE_ENDING_RE.search(
                        _normalize_title(lines[index]),
                    )
                    is not None
                )
            )
        ):
            candidate = ("", lines[index])
        if candidate is None:
            continue
        prefix, title = candidate
        normalized_title = _normalize_title(title)
        if (
            len(normalized_title) > 80
            or normalized_title.endswith((".", "。"))
            or (
                _title_evidence_score(normalized_title) < 3
                and not (
                    _title_evidence_score(normalized_title) >= 1
                    and _OMITTED_REASON_RISK_TITLE_ENDING_RE.search(
                        normalized_title,
                    )
                    is not None
                )
                and not subject_led_standalone_title
            )
        ):
            continue
        if (
            not prefix
            and inline_reason is None
            and _compact(lines[index]) != _compact(title)
        ):
            continue
        candidate_index = index
        candidate_value = (prefix, title)
        candidate_inline_reason = inline_reason
        break
    if candidate_index is None or candidate_value is None:
        return
    reason_parts = [
        *([candidate_inline_reason] if candidate_inline_reason else []),
        *lines[candidate_index + 1:response_index],
    ]
    if (
        not reason_parts
        or len(_compact(" ".join(reason_parts))) < 50
        or any(
            _matches_heading(
                line,
                _KAM_HEADINGS
                + _REASON_HEADINGS
                + _RESPONSE_HEADINGS
                + _TRAILING_HEADINGS,
            )
            for line in reason_parts
        )
    ):
        return
    prefix, title = candidate_value
    replacement = []
    if prefix:
        replacement.append(prefix)
    replacement.extend(
        (
            f"<TITLE>{escape(title)}</TITLE>",
            "핵심감사사항으로 결정된 이유",
        )
    )
    if candidate_inline_reason:
        replacement.append(candidate_inline_reason)
    lines[candidate_index:candidate_index + 1] = replacement


def _collapsed_kam_markup(full_text: str) -> str | None:
    structure = _structured_lines(full_text)
    if structure.limitations:
        return None
    lines = [
        part
        for line in structure.lines
        for part in _split_collapsed_headings(line.text)
    ]
    try:
        kam_start = next(
            index
            for index, line in enumerate(lines)
            if _matches_heading(line, _KAM_HEADINGS)
        )
    except StopIteration:
        return None

    _drop_embedded_collapsed_sections(lines, kam_start=kam_start)
    kam_end = len(lines)
    for index in range(kam_start + 1, len(lines)):
        if _matches_heading(lines[index], _TRAILING_HEADINGS):
            kam_end = index
            break
    _inject_omitted_collapsed_reason_heading(
        lines,
        kam_start=kam_start,
        kam_end=kam_end,
    )
    kam_end = len(lines)
    for index in range(kam_start + 1, len(lines)):
        if _matches_heading(lines[index], _TRAILING_HEADINGS):
            kam_end = index
            break
    reason_indexes = [
        index
        for index in range(kam_start + 1, kam_end)
        if _matches_heading(lines[index], _REASON_HEADINGS)
    ]
    for reason_index in reversed(reason_indexes):
        if reason_index <= kam_start + 1:
            continue
        title_index = reason_index - 1
        if (
            _COLLAPSED_FIELD_MARKER_RE.fullmatch(lines[title_index])
            and title_index > kam_start + 1
        ):
            title_index -= 1
        if lines[title_index].startswith("<TITLE>"):
            continue
        candidate = _collapsed_title_candidate(lines[title_index])
        if (
            candidate is None
            and len(lines[title_index]) <= 80
            and not lines[title_index].endswith((".", "。"))
            and (
                _title_evidence_score(lines[title_index]) >= 3
                or _COLLAPSED_TITLE_ENDING_RE.search(
                    lines[title_index],
                )
                is not None
            )
        ):
            candidate = ("", lines[title_index])
        if candidate is None:
            continue
        prefix, title = candidate
        replacement = []
        if prefix:
            replacement.append(prefix)
        replacement.append(f"<TITLE>{escape(title)}</TITLE>")
        lines[title_index:reason_index] = replacement
    return "\n".join(lines)


def _normalize_collapsed_result_title(value: str) -> str:
    title = _normalize_title(value)
    cut = -1
    for ending in _COLLAPSED_INTRO_ENDINGS:
        position = _compact_span_end(title, ending)
        if position is not None:
            cut = max(cut, position)
    if cut >= 0:
        title = title[cut:].strip()
    title = re.sub(
        r"^\s*(?:\(?\d{1,2}\)?[.)]|[가-하]\s*[.)]|[IVX]{1,5}[.)])\s*",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(
        r"\s*(?:\(?\d{1,2}\)?|[가-하]\s*[.)])\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return _normalize_title(title)


def _validated_collapsed_items(
    items: list[ParsedKamItem],
) -> list[ParsedKamItem] | None:
    validated: list[ParsedKamItem] = []
    for item in items:
        title = _normalize_collapsed_result_title(item.title)
        compact = _compact(title)
        if (
            not title
            or len(title) > 160
            or "핵심감사사항은" in compact
            or "별도의의견을제공하지" in compact
            or _matches_heading(title, _KAM_HEADINGS)
            or _matches_heading(title, _REASON_HEADINGS)
            or _matches_heading(title, _RESPONSE_HEADINGS)
            or _matches_heading(title, _TRAILING_HEADINGS)
        ):
            return None
        validated.append(
            replace(
                item,
                title=title,
                parser_version=f"{PARSER_VERSION}-collapsed",
            )
        )
    return validated


def parse_collapsed_kam_items(full_text: str) -> KamParseOutcome:
    """Parse audit-report text whose source block boundaries were flattened.

    This recovery path only promotes a result when the collapsed text still
    contains an explicit KAM heading, a reason/response pair, and a supported
    title boundary. All other cases retain an error or ambiguity outcome.
    """
    ordinary = parse_kam_items(full_text)
    if (
        ordinary.status == "complete"
        and not _has_embedded_collapsed_section(full_text)
    ):
        validated = _validated_collapsed_items(ordinary.items)
        if validated is not None:
            return KamParseOutcome(
                items=validated,
                status="complete",
                limitations=[],
            )
        ordinary = KamParseOutcome(
            items=[],
            status="error",
            limitations=["invalid_collapsed_kam_title"],
        )
    recovered_text = _collapsed_kam_markup(full_text)
    if recovered_text is None:
        return ordinary
    recovered = parse_kam_items(recovered_text)
    if recovered.status != "complete":
        if recovered.status == "no_kam":
            return KamParseOutcome(
                items=[],
                status="error",
                limitations=["incomplete_collapsed_kam_structure"],
            )
        return recovered
    validated = _validated_collapsed_items(recovered.items)
    if validated is None:
        return KamParseOutcome(
            items=[],
            status="error",
            limitations=["invalid_collapsed_kam_title"],
        )
    return KamParseOutcome(
        items=validated,
        status="complete",
        limitations=[],
    )


def extract_kam_items(full_text: str) -> list[ParsedKamItem]:
    """Extract complete, matter-level KAMs from a cached filing body.

    Ambiguous or incomplete bodies fail closed as an empty list. Call
    :func:`parse_kam_items` when the parse status and limitations are needed.
    """
    return parse_kam_items(full_text).items
