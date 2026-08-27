from __future__ import annotations

import hashlib
import json

import pytest

from kreports.processor.document_structure import parse_document_structure
from kreports.storage.source_archive import archive_structured_document


SOURCE = b"""\
<DOCUMENT>
  <TITLE>I. Business</TITLE>
  <P>First source paragraph.</P>
  <TITLE>1. Key products</TITLE>
  <P>Second source paragraph.</P>
  <P class="caption">Table 1. Key product sales</P>
  <TABLE>
    <TR><TH colspan="2">Category</TH></TR>
    <TR><TD>Revenue</TD><TD>100</TD></TR>
  </TABLE>
  <P class="footnote">Note 1) Unit: KRW millions</P>
  <CUSTOM>Visible source text with no supported structure</CUSTOM>
</DOCUMENT>
"""


class RecordingArchive:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def archive_bytes(self, **kwargs):
        self.calls.append(kwargs)
        return {"archived": True}


def test_generic_structure_preserves_document_order_and_table_context():
    """A generic archive must retain source structure without feature classification."""
    source_sha256 = hashlib.sha256(SOURCE).hexdigest()

    document = parse_document_structure(
        SOURCE,
        content_type="application/xml",
        source_sha256=source_sha256,
        source_receipt="20260828000001",
        source_uri="https://dart.example.test/20260828000001",
    )
    payload = document.to_dict()

    assert payload["source_sha256"] == source_sha256
    assert payload["structural_status"] == "requires_review"
    assert [node["kind"] for node in payload["nodes"]] == [
        "heading",
        "block",
        "heading",
        "block",
        "caption",
        "table",
        "cell",
        "cell",
        "cell",
        "footnote",
    ]
    assert [node["document_order"] for node in payload["nodes"]] == list(
        range(len(payload["nodes"]))
    )
    assert all(node["node_path"] for node in payload["nodes"])
    assert len({node["node_path"] for node in payload["nodes"]}) == len(payload["nodes"])

    table = next(node for node in payload["nodes"] if node["kind"] == "table")
    assert table["nearest_section"] == "1. Key products"
    assert table["caption"] == "Table 1. Key product sales"
    assert table["footnotes"] == ["Note 1) Unit: KRW millions"]
    assert [(cell["row"], cell["column"], cell["colspan"]) for cell in payload["table_cells"]] == [
        (0, 0, 2),
        (1, 0, 1),
        (1, 1, 1),
    ]
    assert payload["unparsed_nodes"] == [
        {
            "node_path": "/DOCUMENT/CUSTOM/text()[1]",
            "reason": "unsupported_parent_text:CUSTOM",
            "text": "Visible source text with no supported structure",
            "document_order": 10,
        }
    ]


def test_archived_structure_keeps_raw_source_provenance_and_parser_version():
    """A parse package is linked to the raw object rather than an invented fact."""
    source_sha256 = hashlib.sha256(SOURCE).hexdigest()
    document = parse_document_structure(
        SOURCE,
        content_type="application/xml",
        source_sha256=source_sha256,
        source_receipt="20260828000001",
        source_uri="https://dart.example.test/20260828000001",
    )
    archive = RecordingArchive()

    archived = archive_structured_document(archive, document)

    assert archived == {"archived": True}
    assert len(archive.calls) == 1
    call = archive.calls[0]
    assert call["extension"] == "json"
    assert call["metadata"] == {
        "source_receipt": "20260828000001",
        "source_uri": "https://dart.example.test/20260828000001",
        "archive_version": document.parser_version,
        "source_sha256": source_sha256,
        "parser_version": document.parser_version,
    }
    assert json.loads(call["data"].decode("utf-8")) == document.to_dict()


def test_archived_structure_rejects_missing_original_source_provenance():
    """A raw hash alone is not a substitute for the original DART source identity."""
    document = parse_document_structure(
        SOURCE,
        content_type="application/xml",
        source_sha256=hashlib.sha256(SOURCE).hexdigest(),
    )

    with pytest.raises(ValueError, match="source_receipt and source_uri"):
        archive_structured_document(RecordingArchive(), document)


def test_direct_text_and_unknown_element_tails_are_never_silently_dropped():
    """Wrapper text is emitted, while unknown-parent fragments remain reviewable."""
    wrapper_source = b"<html><body>before<p>inside</p>after</body></html>"
    wrapper_document = parse_document_structure(
        wrapper_source,
        content_type="text/html",
        source_sha256=hashlib.sha256(wrapper_source).hexdigest(),
    )

    assert wrapper_document.structural_status == "complete"
    assert [node["text"] for node in wrapper_document.nodes] == [
        "before",
        "inside",
        "after",
    ]
    assert [node["document_order"] for node in wrapper_document.nodes] == [0, 1, 2]

    unknown_source = b"<DOCUMENT><CUSTOM>prefix<P>kept</P>suffix</CUSTOM></DOCUMENT>"
    unknown_document = parse_document_structure(
        unknown_source,
        content_type="application/xml",
        source_sha256=hashlib.sha256(unknown_source).hexdigest(),
    )

    assert unknown_document.structural_status == "requires_review"
    assert [node["text"] for node in unknown_document.nodes] == ["kept"]
    assert [node["text"] for node in unknown_document.unparsed_nodes] == [
        "prefix",
        "suffix",
    ]
    assert all(node["node_path"] for node in unknown_document.unparsed_nodes)


def test_cell_descendant_blocks_are_owned_by_the_cell_without_global_duplicates():
    """Nested formatting inside a table cell must not create a second block record."""
    source = b"""\
<DOCUMENT><TABLE>
  <TR><TD rowspan="2"><P>Cell A</P></TD><TD colspan="2"><P>Cell B</P></TD></TR>
  <TR><TD>Cell C</TD><TD>Cell D</TD></TR>
</TABLE></DOCUMENT>
"""
    document = parse_document_structure(
        source,
        content_type="application/xml",
        source_sha256=hashlib.sha256(source).hexdigest(),
    )

    assert [node["kind"] for node in document.nodes] == [
        "table",
        "cell",
        "cell",
        "cell",
        "cell",
    ]
    assert [(cell["row"], cell["column"], cell["rowspan"], cell["colspan"]) for cell in document.table_cells] == [
        (0, 0, 2, 1),
        (0, 1, 1, 2),
        (1, 1, 1, 1),
        (1, 2, 1, 1),
    ]


def test_hidden_ancestor_content_is_not_emitted_as_visible_structure():
    """Visibility checks must apply to an element's full ancestor chain."""
    source = b"""\
<html><body>
  <div aria-hidden="true"><p>hidden aria text</p></div>
  <div style="display: none"><p>hidden style text</p></div>
  <p>visible sibling</p>
</body></html>
"""
    document = parse_document_structure(
        source,
        content_type="text/html",
        source_sha256=hashlib.sha256(source).hexdigest(),
    )

    assert document.structural_status == "complete"
    assert [node["text"] for node in document.nodes] == ["visible sibling"]
    assert document.unparsed_nodes == ()


def test_tails_after_hidden_and_ignored_elements_remain_visible_source_text():
    """A skipped element's tail belongs to its visible parent, not the skipped body."""
    source = b"""\
<html><body><div hidden>secret</div>after hidden
<script>ignored script</script>after script
<style>.ignored { display: none }</style>after style
<p>shown</p></body></html>
"""
    document = parse_document_structure(
        source,
        content_type="text/html",
        source_sha256=hashlib.sha256(source).hexdigest(),
    )

    assert document.structural_status == "complete"
    assert [node["text"] for node in document.nodes] == [
        "after hidden",
        "after script",
        "after style",
        "shown",
    ]
    assert "secret" not in str(document.to_dict())
    assert "ignored script" not in str(document.to_dict())


def test_table_caption_uses_direct_caption_before_falling_back_to_external_caption():
    """A native table caption must stay attached to its owning table."""
    direct_source = b"""\
<DOCUMENT><TABLE><CAPTION>Native table caption</CAPTION>
<TR><TD>native cell</TD></TR></TABLE></DOCUMENT>
"""
    direct_document = parse_document_structure(
        direct_source,
        content_type="application/xml",
        source_sha256=hashlib.sha256(direct_source).hexdigest(),
    )
    direct_table = next(node for node in direct_document.nodes if node["kind"] == "table")

    external_source = b"""\
<DOCUMENT><P class="caption">External caption</P><TABLE>
<TR><TD>external cell</TD></TR></TABLE></DOCUMENT>
"""
    external_document = parse_document_structure(
        external_source,
        content_type="application/xml",
        source_sha256=hashlib.sha256(external_source).hexdigest(),
    )
    external_table = next(node for node in external_document.nodes if node["kind"] == "table")

    assert direct_table["caption"] == "Native table caption"
    assert external_table["caption"] == "External caption"


@pytest.mark.parametrize(
    "caption_markup",
    [
        b"<CAPTION hidden>secret caption</CAPTION>",
        b'<CAPTION style="display:none">secret caption</CAPTION>',
    ],
)
def test_hidden_direct_table_caption_is_not_exposed(caption_markup: bytes):
    """A hidden native caption is neither table context nor a visible node."""
    source = b"<DOCUMENT><TABLE>" + caption_markup + b"<TR><TD>cell</TD></TR></TABLE></DOCUMENT>"
    document = parse_document_structure(
        source,
        content_type="text/html",
        source_sha256=hashlib.sha256(source).hexdigest(),
    )
    table = next(node for node in document.nodes if node["kind"] == "table")

    assert document.structural_status == "complete"
    assert table["caption"] is None
    assert "secret caption" not in str(document.to_dict())


def test_visible_direct_caption_class_binds_its_owning_table():
    """DART-style paragraph captions are direct table context too."""
    source = b"""\
<DOCUMENT><TABLE><P class="caption">Direct paragraph caption</P>
<TR><TD>cell</TD></TR></TABLE></DOCUMENT>
"""
    document = parse_document_structure(
        source,
        content_type="application/xml",
        source_sha256=hashlib.sha256(source).hexdigest(),
    )
    table = next(node for node in document.nodes if node["kind"] == "table")

    assert table["caption"] == "Direct paragraph caption"
