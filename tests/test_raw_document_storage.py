from sqlalchemy import inspect


from pathlib import Path

from kreports.storage.raw_documents import RawDocumentStore


def test_source_documents_has_raw_storage_columns(temp_engine):
    inspector = inspect(temp_engine)
    columns = {column["name"] for column in inspector.get_columns("source_documents")}

    assert "storage_uri" in columns
    assert "content_length" in columns
    assert "compressed_length" in columns
    assert "storage_status" in columns


def test_raw_document_store_writes_gzip_and_verifies_hash(tmp_path):
    store = RawDocumentStore(base_dir=tmp_path)
    content = "<DOCUMENT><TITLE>감사보고서</TITLE><P>핵심감사사항</P></DOCUMENT>"

    saved = store.write(
        corp_code="00126380",
        bsns_year=2025,
        source_type="audit_report",
        rcept_no="20260331000001_00760_xml",
        content_type="xml",
        content=content,
    )

    assert saved.storage_uri.startswith("file://")
    assert saved.content_length == len(content.encode("utf-8"))
    assert saved.compressed_length > 0
    assert Path(saved.path).exists()
    assert store.read(saved.storage_uri, expected_hash=saved.doc_hash) == content
