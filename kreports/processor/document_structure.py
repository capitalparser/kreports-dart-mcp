"""Deterministic, loss-aware structural parsing for DART source documents.

This module deliberately records document structure only.  Feature-specific
classification remains a projection from this archived representation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from lxml import etree, html


__all__ = ["StructuredDocument", "parse_document_structure"]


PARSER_VERSION = "document-structure-v1"

_WRAPPER_TAGS = {
    "document",
    "html",
    "body",
    "head",
    "tbody",
    "thead",
    "tfoot",
    "tr",
    "colgroup",
    "col",
    "section",
    "article",
}
_IGNORED_TAGS = {"script", "style", "meta", "link", "noscript", "br", "hr"}
_BLOCK_TAGS = {"p", "div", "li", "dd", "dt", "blockquote", "pre"}


@dataclass(frozen=True)
class StructuredDocument:
    """A source-hash-bound, generic structural representation of one asset."""

    parser_version: str
    source_sha256: str
    nodes: tuple[dict[str, Any], ...]
    table_cells: tuple[dict[str, Any], ...]
    unparsed_nodes: tuple[dict[str, Any], ...]
    structural_status: str
    source_receipt: str | None = None
    source_uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic JSON-ready archive payload."""
        return {
            "parser_version": self.parser_version,
            "source_sha256": self.source_sha256,
            "source_receipt": self.source_receipt,
            "source_uri": self.source_uri,
            "nodes": list(self.nodes),
            "table_cells": list(self.table_cells),
            "unparsed_nodes": list(self.unparsed_nodes),
            "structural_status": self.structural_status,
        }


def parse_document_structure(
    content: bytes,
    *,
    content_type: str,
    source_sha256: str,
    source_receipt: str | None = None,
    source_uri: str | None = None,
) -> StructuredDocument:
    """Emit generic visible structure from one HTML or XML source asset.

    Optional receipt/URI values are carried only when the caller knows the raw
    source identity.  They are mandatory later when the parse package is
    archived, so this parser never manufactures a DART receipt from a hash.
    """
    _validate_source_sha256(content, source_sha256)
    root, tree, parse_error = _parse_root(content, content_type)
    if parse_error:
        return StructuredDocument(
            parser_version=PARSER_VERSION,
            source_sha256=source_sha256,
            nodes=(),
            table_cells=(),
            unparsed_nodes=(
                {
                    "node_path": "/",
                    "reason": f"parse_error:{parse_error}",
                    "text": "",
                    "document_order": 0,
                },
            ),
            structural_status="requires_review",
            source_receipt=_clean_optional(source_receipt),
            source_uri=_clean_optional(source_uri),
        )

    nodes: list[dict[str, Any]] = []
    table_cells: list[dict[str, Any]] = []
    unparsed_nodes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    current_section: str | None = None
    latest_caption: str | None = None
    table_nodes: list[dict[str, Any]] = []
    footnote_nodes: list[dict[str, Any]] = []
    table_coordinates: dict[str, dict[str, tuple[int, int, int, int]]] = {}

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        if not _is_visible(element):
            parent = element.getparent()
            if parent is not None and isinstance(parent.tag, str):
                _record_direct_text(
                    element.tail,
                    host=parent,
                    node_path=f"{tree.getpath(element)}/tail()",
                    nodes=nodes,
                    unparsed_nodes=unparsed_nodes,
                    events=events,
                    nearest_section=current_section,
                )
            continue
        tag = _local_name(element.tag)
        node_path = tree.getpath(element)

        _record_direct_text(
            element.text,
            host=element,
            node_path=f"{node_path}/text()[1]",
            nodes=nodes,
            unparsed_nodes=unparsed_nodes,
            events=events,
            nearest_section=current_section,
        )

        if tag == "table":
            table_coordinates[node_path] = _table_cell_coordinates(element, tree)
            node = _node(
                kind="table",
                node_path=node_path,
                text="",
                nearest_section=current_section,
                caption=_direct_table_caption(element) or latest_caption,
                footnotes=[],
            )
            _append_event(nodes, events, node)
            table_nodes.append(node)
        elif tag in {"td", "th"}:
            text = _visible_text(element)
            if not text:
                pass
            else:
                table = _nearest_ancestor(element, "table")
                if table is None:
                    _append_event(
                        unparsed_nodes,
                        events,
                        _unparsed(node_path, "cell_without_table", text),
                    )
                else:
                    table_path = tree.getpath(table)
                    row, column, rowspan, colspan = table_coordinates[table_path][node_path]
                    cell = _node(
                        kind="cell",
                        node_path=node_path,
                        text=text,
                        table_path=table_path,
                        row=row,
                        column=column,
                        rowspan=rowspan,
                        colspan=colspan,
                    )
                    _append_event(nodes, events, cell)
                    table_cells.append(cell)
        elif _is_heading(tag, element):
            text = _visible_text(element)
            if text:
                current_section = text
                _append_event(
                    nodes,
                    events,
                    _node(kind="heading", node_path=node_path, text=text),
                )
        elif _is_caption(tag, element):
            text = _visible_text(element)
            if text:
                latest_caption = text
                _append_event(
                    nodes,
                    events,
                    _node(kind="caption", node_path=node_path, text=text),
                )
        elif _is_footnote(tag, element):
            text = _visible_text(element)
            if text:
                node = _node(kind="footnote", node_path=node_path, text=text)
                _append_event(nodes, events, node)
                footnote_nodes.append(node)
        elif tag in _BLOCK_TAGS:
            text = _visible_text(element)
            if (
                text
                and not _has_semantic_child(element)
                and not _has_cell_ancestor(element)
            ):
                _append_event(
                    nodes,
                    events,
                    _node(kind="block", node_path=node_path, text=text),
                )

        parent = element.getparent()
        if parent is not None and isinstance(parent.tag, str):
            _record_direct_text(
                element.tail,
                host=parent,
                node_path=f"{node_path}/tail()",
                nodes=nodes,
                unparsed_nodes=unparsed_nodes,
                events=events,
                nearest_section=current_section,
            )

    for document_order, event in enumerate(events):
        event["document_order"] = document_order
    _attach_nearest_footnotes(table_nodes, footnote_nodes)
    return StructuredDocument(
        parser_version=PARSER_VERSION,
        source_sha256=source_sha256,
        nodes=tuple(nodes),
        table_cells=tuple(table_cells),
        unparsed_nodes=tuple(unparsed_nodes),
        structural_status="requires_review" if unparsed_nodes else "complete",
        source_receipt=_clean_optional(source_receipt),
        source_uri=_clean_optional(source_uri),
    )


def _parse_root(
    content: bytes, content_type: str
) -> tuple[etree._Element, etree._ElementTree, str | None]:
    normalized_content_type = content_type.lower()
    try:
        if "html" in normalized_content_type:
            root = html.fromstring(content)
        elif "xml" in normalized_content_type:
            root = etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False))
        else:
            raise ValueError(f"unsupported_content_type:{content_type}")
    except (ValueError, etree.ParserError, etree.XMLSyntaxError) as exc:
        placeholder = etree.Element("unparsed")
        return placeholder, etree.ElementTree(placeholder), str(exc).splitlines()[0]
    return root, etree.ElementTree(root), None


def _validate_source_sha256(content: bytes, source_sha256: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if source_sha256 != actual:
        raise ValueError("source_sha256 must match the supplied source bytes")


def _node(*, kind: str, node_path: str, text: str, **context: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "node_path": node_path,
        "text": text,
        **context,
    }


def _unparsed(node_path: str, reason: str, text: str) -> dict[str, str]:
    return {"node_path": node_path, "reason": reason, "text": text}


def _append_event(
    collection: list[dict[str, Any]], events: list[dict[str, Any]], event: dict[str, Any]
) -> None:
    collection.append(event)
    events.append(event)


def _record_direct_text(
    value: str | None,
    *,
    host: etree._Element,
    node_path: str,
    nodes: list[dict[str, Any]],
    unparsed_nodes: list[dict[str, Any]],
    events: list[dict[str, Any]],
    nearest_section: str | None,
) -> None:
    """Account for text and tails which are outside a child element's node."""
    text = " ".join((value or "").split())
    if (
        not text
        or not _is_visible(host)
        or _direct_text_is_owned_by_emitted_node(host)
    ):
        return
    tag = _local_name(host.tag)
    if tag in _WRAPPER_TAGS:
        _append_event(
            nodes,
            events,
            _node(
                kind="block",
                node_path=node_path,
                text=text,
                nearest_section=nearest_section,
                source_fragment="direct_text",
            ),
        )
        return
    _append_event(
        unparsed_nodes,
        events,
        _unparsed(node_path, f"unsupported_parent_text:{host.tag}", text),
    )


def _direct_text_is_owned_by_emitted_node(host: etree._Element) -> bool:
    """Whether a surrounding node already retains this text segment verbatim."""
    current: etree._Element | None = host
    while current is not None:
        if not isinstance(current.tag, str):
            current = current.getparent()
            continue
        tag = _local_name(current.tag)
        if tag in {"td", "th"}:
            return True
        if _is_heading(tag, current) or _is_caption(tag, current) or _is_footnote(tag, current):
            return True
        if tag in _BLOCK_TAGS and not _has_semantic_child(current):
            return True
        current = current.getparent()
    return False


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _visible_text(element: etree._Element) -> str:
    return " ".join("".join(element.itertext()).split())


def _is_visible(element: etree._Element) -> bool:
    current: etree._Element | None = element
    while current is not None:
        tag = _local_name(current.tag)
        if tag in _IGNORED_TAGS or tag == "head":
            return False
        if current.get("hidden") is not None:
            return False
        if (current.get("aria-hidden") or "").lower() == "true":
            return False
        if "display:none" in (current.get("style") or "").replace(" ", "").lower():
            return False
        current = current.getparent()
    return True


def _is_heading(tag: str, element: etree._Element) -> bool:
    return tag == "title" or bool(re.fullmatch(r"h[1-6]", tag))


def _is_caption(tag: str, element: etree._Element) -> bool:
    classes = (element.get("class") or "").lower().split()
    return tag == "caption" or "caption" in classes


def _is_footnote(tag: str, element: etree._Element) -> bool:
    classes = (element.get("class") or "").lower().split()
    identifier = (element.get("id") or "").lower()
    return tag == "footnote" or "footnote" in classes or "footnote" in identifier


def _has_semantic_child(element: etree._Element) -> bool:
    for child in element.iterdescendants():
        if not isinstance(child.tag, str):
            continue
        tag = _local_name(child.tag)
        if tag in {"table", "td", "th", "caption", "footnote"} or _is_heading(tag, child):
            return True
        if tag in _BLOCK_TAGS and _visible_text(child):
            return True
    return False


def _nearest_ancestor(element: etree._Element, expected_tag: str) -> etree._Element | None:
    parent = element.getparent()
    while parent is not None:
        if isinstance(parent.tag, str) and _local_name(parent.tag) == expected_tag:
            return parent
        parent = parent.getparent()
    return None


def _has_cell_ancestor(element: etree._Element) -> bool:
    return (
        _nearest_ancestor(element, "td") is not None
        or _nearest_ancestor(element, "th") is not None
    )


def _direct_table_caption(table: etree._Element) -> str | None:
    for child in table:
        if not isinstance(child.tag, str) or not _is_visible(child):
            continue
        if _is_caption(_local_name(child.tag), child):
            text = _visible_text(child)
            if text:
                return text
    return None


def _table_cell_coordinates(
    table: etree._Element, tree: etree._ElementTree
) -> dict[str, tuple[int, int, int, int]]:
    coordinates: dict[str, tuple[int, int, int, int]] = {}
    active_spans: dict[int, int] = {}
    rows = [element for element in table.iter() if _local_name(element.tag) == "tr"]
    for row_index, row in enumerate(rows):
        occupied = {column for column, remaining in active_spans.items() if remaining > 0}
        for column in occupied:
            active_spans[column] -= 1
        cell_elements = [
            child
            for child in row
            if isinstance(child.tag, str) and _local_name(child.tag) in {"td", "th"}
        ]
        column = 0
        for cell in cell_elements:
            while column in occupied:
                column += 1
            rowspan = _positive_int(cell.get("rowspan"), default=1)
            colspan = _positive_int(cell.get("colspan"), default=1)
            coordinates[tree.getpath(cell)] = (row_index, column, rowspan, colspan)
            for span_column in range(column, column + colspan):
                if rowspan > 1:
                    active_spans[span_column] = rowspan - 1
            column += colspan
    return coordinates


def _positive_int(value: str | None, *, default: int) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _attach_nearest_footnotes(
    table_nodes: list[dict[str, Any]], footnote_nodes: list[dict[str, Any]]
) -> None:
    for table in table_nodes:
        if not footnote_nodes:
            continue
        nearest = min(
            footnote_nodes,
            key=lambda footnote: abs(
                footnote["document_order"] - table["document_order"]
            ),
        )
        table["footnotes"] = [nearest["text"]]


def _clean_optional(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None
