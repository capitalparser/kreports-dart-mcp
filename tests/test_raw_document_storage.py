from sqlalchemy import inspect


from pathlib import Path

from kreports.storage.raw_documents import RawDocumentStore
from kreports.storage.raw_documents import sha1_text


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


def test_migrate_raw_documents_to_storage_preserves_hash(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.raw_storage_migration as migration_module
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument

    monkeypatch.setattr(migration_module, "RawDocumentStore", lambda: RawDocumentStore(base_dir=tmp_path))
    raw_content = "<DOCUMENT><P>원문</P></DOCUMENT>"

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content=raw_content,
            doc_hash=sha1_text(raw_content),
            storage_status="inline",
        ))

    result = migration_module.migrate_raw_documents_to_storage(limit=10, clear_inline=True)

    assert result["migrated"] == 1
    with get_session() as session:
        doc = session.query(SourceDocument).one()
        assert doc.storage_uri.startswith("file://")
        assert doc.storage_status == "externalized"
        assert doc.raw_content == ""
        assert doc.content_length > 0
        assert doc.compressed_length > 0


def test_verify_raw_storage_detects_missing_file(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument
    from kreports.maintenance.raw_storage_migration import verify_raw_storage

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content="",
            doc_hash="abc",
            storage_uri="file:///missing/file.xml.gz",
            storage_status="externalized",
        ))

    out = verify_raw_storage(limit=10)

    assert out["checked"] == 1
    assert out["failed"] == 1
    assert "missing" in out["errors"][0]["error"]
