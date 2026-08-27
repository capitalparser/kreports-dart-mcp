"""Archive generic source-document structure beside immutable raw evidence."""

from __future__ import annotations

import json

from kreports.processor.document_structure import StructuredDocument
from kreports.storage.drive_archive import ArchivedObject, DriveArchive


__all__ = ["archive_structured_document"]


def archive_structured_document(
    archive: DriveArchive, document: StructuredDocument
) -> ArchivedObject:
    """Serialize a source-bound parse package through the verified Drive adapter."""
    if not document.source_receipt or not document.source_uri:
        raise ValueError(
            "archived structure requires source_receipt and source_uri from the original source"
        )
    payload = json.dumps(
        document.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return archive.archive_bytes(
        data=payload,
        extension="json",
        metadata={
            "source_receipt": document.source_receipt,
            "source_uri": document.source_uri,
            "archive_version": document.parser_version,
            "source_sha256": document.source_sha256,
            "parser_version": document.parser_version,
        },
    )
