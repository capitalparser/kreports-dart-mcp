from pathlib import Path

from sqlalchemy import text
from sqlalchemy import inspect
import pytest

from kreports.storage.evidence_blobs import EvidenceBlobStore, sha1_text


@pytest.fixture(autouse=True)
def _allow_intentional_evidence_blob_writes(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")


def test_evidence_blob_store_writes_and_reads_file_backend(tmp_path):
    store = EvidenceBlobStore(base_dir=tmp_path)
    content = "핵심감사사항 본문\n감사절차 본문"

    saved = store.write(
        table_name="evidence_documents",
        row_id=123,
        corp_code="00126380",
        bsns_year=2024,
        content=content,
    )

    assert saved.storage_uri.startswith("file://")
    assert saved.text_hash == sha1_text(content)
    assert saved.content_length == len(content.encode("utf-8"))
    assert saved.compressed_length > 0
    assert Path(saved.path).exists()
    assert store.read(saved.storage_uri, expected_hash=saved.text_hash) == content


def test_long_text_tables_have_evidence_blob_manifest_columns(temp_engine):
    inspector = inspect(temp_engine)
    for table in ["accounting_note_chapters", "evidence_documents", "report_sections"]:
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "full_text_uri" in columns
        assert "full_text_hash" in columns
        assert "full_text_length" in columns
        assert "full_text_compressed_length" in columns
        assert "full_text_storage_status" in columns


def test_externalize_long_evidence_text_preserves_excerpt_and_manifest(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.evidence_blob_migration as migration_module
    from kreports.db.engine import get_session

    monkeypatch.setattr(
        migration_module,
        "EvidenceBlobStore",
        lambda **kwargs: EvidenceBlobStore(base_dir=tmp_path, **kwargs),
    )
    long_body = "A" * 5000
    with get_session() as session:
        session.execute(text("""
            INSERT INTO report_sections
            (rcept_no, corp_code, bsns_year, source_type, section_key, section_title,
             body_text, body_hash, body_length, ordinal, fetched_at)
            VALUES
            ('20250331000001', '00126380', 2024, 'audit_report', 'kam', 'KAM',
             :body, 'oldhash', :length, 1, CURRENT_TIMESTAMP)
        """), {"body": long_body, "length": len(long_body)})
        session.commit()

    out = migration_module.externalize_long_evidence_text(
        table_name="report_sections",
        text_column="body_text",
        excerpt_chars=1000,
        min_text_chars=2000,
        limit=10,
        backend="file",
    )

    assert out["externalized"] == 1
    with get_session() as session:
        row = session.execute(text("""
            SELECT body_text, full_text_uri, full_text_hash, full_text_length,
                   full_text_compressed_length, full_text_storage_status
            FROM report_sections
            WHERE rcept_no='20250331000001'
        """)).mappings().one()
    assert len(row["body_text"]) == 1000
    assert row["full_text_uri"].startswith("file://")
    assert row["full_text_length"] == 5000
    assert row["full_text_compressed_length"] > 0
    assert row["full_text_storage_status"] == "externalized"
