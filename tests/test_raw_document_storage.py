from sqlalchemy import inspect


from pathlib import Path

from kreports.storage.raw_documents import RawDocumentStore
from kreports.storage.raw_documents import sha1_text


class _FakeGcsBlob:
    def __init__(self, bucket: "_FakeGcsBucket", name: str):
        self.bucket = bucket
        self.name = name

    def upload_from_string(self, data: bytes, content_type: str | None = None):
        self.bucket.objects[self.name] = {"data": data, "content_type": content_type}

    def download_as_bytes(self) -> bytes:
        if self.name not in self.bucket.objects:
            raise FileNotFoundError(self.name)
        return self.bucket.objects[self.name]["data"]


class _FakeGcsBucket:
    def __init__(self, objects: dict):
        self.objects = objects

    def blob(self, name: str) -> _FakeGcsBlob:
        return _FakeGcsBlob(self, name)


class _FakeGcsClient:
    def __init__(self):
        self.objects: dict[str, dict] = {}

    def bucket(self, _name: str) -> _FakeGcsBucket:
        return _FakeGcsBucket(self.objects)


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


def test_raw_document_store_writes_and_reads_gcs_uri():
    client = _FakeGcsClient()
    store = RawDocumentStore(
        backend="gcs",
        bucket="kreports-raw-documents",
        prefix="dart",
        gcs_client=client,
    )
    content = "<DOCUMENT><TITLE>감사보고서</TITLE><P>핵심감사사항</P></DOCUMENT>"

    saved = store.write(
        corp_code="00126380",
        bsns_year=2025,
        source_type="audit_report",
        rcept_no="20260331000001_00760_xml",
        content_type="xml",
        content=content,
    )

    assert saved.storage_uri == (
        "gs://kreports-raw-documents/dart/2025/audit_report/"
        "00126380/20260331000001_00760_xml.xml.gz"
    )
    assert saved.path == "dart/2025/audit_report/00126380/20260331000001_00760_xml.xml.gz"
    assert saved.content_length == len(content.encode("utf-8"))
    assert saved.compressed_length > 0
    assert store.read(saved.storage_uri, expected_hash=saved.doc_hash) == content


def test_raw_document_store_requires_bucket_for_gcs():
    store = RawDocumentStore(backend="gcs")

    try:
        store.write(
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            rcept_no="20260331000001",
            content_type="xml",
            content="<DOCUMENT/>",
        )
    except ValueError as exc:
        assert "bucket is required" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_raw_storage_config_status_reports_inline_db_growth(monkeypatch):
    import kreports.maintenance.raw_storage_migration as migration_module

    monkeypatch.setattr(migration_module.settings, "raw_storage_backend", "inline")
    monkeypatch.setattr(migration_module.settings, "raw_storage_bucket", "")
    monkeypatch.setattr(migration_module.settings, "raw_storage_prefix", "")
    monkeypatch.setattr(migration_module.settings, "raw_storage_keep_inline", False)

    out = migration_module.raw_storage_config_status()

    assert out["ready"] is True
    assert out["verdict"] == "inline_raw_will_grow_db"
    assert out["will_store_inline_raw_content"] is True
    assert out["will_write_storage_uri"] is False


def test_raw_storage_config_status_requires_gcs_bucket(monkeypatch):
    import kreports.maintenance.raw_storage_migration as migration_module

    monkeypatch.setattr(migration_module.settings, "raw_storage_backend", "gcs")
    monkeypatch.setattr(migration_module.settings, "raw_storage_bucket", "")
    monkeypatch.setattr(migration_module.settings, "raw_storage_prefix", "dart")
    monkeypatch.setattr(migration_module.settings, "raw_storage_keep_inline", False)

    out = migration_module.raw_storage_config_status()

    assert out["ready"] is False
    assert out["verdict"] == "gcs_bucket_missing"
    assert out["mode"] == "externalized"
    assert out["will_write_storage_uri"] is True


def test_raw_storage_smoke_roundtrips_file_backend(tmp_path, monkeypatch):
    import kreports.maintenance.raw_storage_migration as migration_module

    monkeypatch.setattr(
        migration_module,
        "RawDocumentStore",
        lambda **kwargs: RawDocumentStore(base_dir=tmp_path, **kwargs),
    )

    out = migration_module.raw_storage_smoke(backend="file", content="<DOCUMENT>smoke</DOCUMENT>")

    assert out["ok"] is True
    assert out["backend"] == "file"
    assert out["storage_uri"].startswith("file://")
    assert out["content_length"] == len("<DOCUMENT>smoke</DOCUMENT>".encode("utf-8"))
    assert out["compressed_length"] > 0


def test_migrate_raw_documents_to_storage_preserves_hash(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.raw_storage_migration as migration_module
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument

    monkeypatch.setattr(
        migration_module,
        "RawDocumentStore",
        lambda **kwargs: RawDocumentStore(base_dir=tmp_path, **kwargs),
    )
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


def test_migrate_raw_documents_to_gcs_storage(temp_engine, monkeypatch):
    import kreports.maintenance.raw_storage_migration as migration_module
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument

    client = _FakeGcsClient()
    monkeypatch.setattr(
        migration_module,
        "RawDocumentStore",
        lambda **kwargs: RawDocumentStore(gcs_client=client, **kwargs),
    )
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

    result = migration_module.migrate_raw_documents_to_storage(
        limit=10,
        clear_inline=True,
        backend="gcs",
        bucket="kreports-raw-documents",
        prefix="dart",
    )

    assert result["migrated"] == 1
    with get_session() as session:
        doc = session.query(SourceDocument).one()
        assert doc.storage_uri.startswith("gs://kreports-raw-documents/dart/")
        assert doc.storage_status == "externalized"
        assert doc.raw_content == ""
        assert doc.content_length > 0
        assert doc.compressed_length > 0


def test_raw_storage_readiness_distinguishes_extractable_raw_from_placeholders(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument
    from kreports.maintenance.raw_storage_migration import raw_storage_readiness

    with get_session() as session:
        session.add_all([
            SourceDocument(
                rcept_no="20250331000001",
                corp_code="00000001",
                bsns_year=2024,
                source_type="business_report",
                report_nm="사업보고서",
                content_type="xml",
                raw_content="<DOCUMENT>원문</DOCUMENT>",
                doc_hash="raw",
                storage_status="inline",
            ),
            SourceDocument(
                rcept_no="20250331000002",
                corp_code="00000001",
                bsns_year=2024,
                source_type="audit_report",
                report_nm="감사보고서",
                content_type="xml",
                raw_content="",
                doc_hash="derived",
                storage_status="derived_only",
            ),
        ])

    out = raw_storage_readiness()

    assert out["total"] == 2
    assert out["raw_extractable"] == 1
    assert out["raw_business_extractable"] == 1
    assert out["raw_audit_extractable"] == 0
    assert out["derived_placeholders"] == 1
    assert out["derived_audit_placeholders"] == 1
    assert out["parser_repair_ready"] is True


def test_raw_storage_readiness_marks_parser_repair_blocked_without_raw(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument
    from kreports.maintenance.raw_storage_migration import raw_storage_readiness

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000002",
            corp_code="00000001",
            bsns_year=2024,
            source_type="audit_report",
            report_nm="감사보고서",
            content_type="xml",
            raw_content="",
            doc_hash="derived",
            storage_status="derived_only",
        ))

    out = raw_storage_readiness()

    assert out["raw_extractable"] == 0
    assert out["derived_placeholders"] == 1
    assert out["parser_repair_ready"] is False
    assert "no raw documents" in out["status_note"]


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


def test_clear_externalized_inline_content_verifies_before_clear(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.raw_storage_migration as migration_module
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument

    store = RawDocumentStore(base_dir=tmp_path)
    monkeypatch.setattr(migration_module, "RawDocumentStore", lambda: store)
    raw_content = "<DOCUMENT><P>검증 후 삭제</P></DOCUMENT>"
    saved = store.write(
        corp_code="00000001",
        bsns_year=2024,
        source_type="business_report",
        rcept_no="20250331000001",
        content_type="xml",
        content=raw_content,
    )

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content=raw_content,
            doc_hash=saved.doc_hash,
            storage_uri=saved.storage_uri,
            content_length=saved.content_length,
            compressed_length=saved.compressed_length,
            storage_status="externalized",
        ))

    out = migration_module.clear_externalized_inline_content(limit=10)

    assert out["checked"] == 1
    assert out["cleared"] == 1
    assert out["failed"] == 0
    assert out["cleared_bytes"] == len(raw_content.encode("utf-8"))
    with get_session() as session:
        doc = session.query(SourceDocument).one()
        assert doc.raw_content == ""
        assert doc.storage_status == "externalized"


def test_clear_externalized_inline_content_keeps_inline_on_verify_failure(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument
    from kreports.maintenance.raw_storage_migration import clear_externalized_inline_content

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content="<DOCUMENT>보존</DOCUMENT>",
            doc_hash="abc",
            storage_uri="file:///missing/file.xml.gz",
            storage_status="externalized",
        ))

    out = clear_externalized_inline_content(limit=10)

    assert out["checked"] == 1
    assert out["cleared"] == 0
    assert out["failed"] == 1
    with get_session() as session:
        doc = session.query(SourceDocument).one()
        assert doc.raw_content == "<DOCUMENT>보존</DOCUMENT>"


def test_clear_cold_derived_inline_content_requires_derived_evidence(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import ReportSection, SourceDocument
    from kreports.maintenance.raw_storage_migration import clear_cold_derived_inline_content

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20230331000001",
            corp_code="00000001",
            bsns_year=2022,
            source_type="audit_report",
            report_nm="감사보고서",
            content_type="xml",
            raw_content="<DOCUMENT>파생 있음</DOCUMENT>",
            doc_hash="x",
            storage_status="inline",
        ))
        session.add(SourceDocument(
            rcept_no="20230331000002",
            corp_code="00000002",
            bsns_year=2022,
            source_type="audit_report",
            report_nm="감사보고서",
            content_type="xml",
            raw_content="<DOCUMENT>파생 없음</DOCUMENT>",
            doc_hash="y",
            storage_status="inline",
        ))
        session.add(ReportSection(
            rcept_no="20230331000001",
            corp_code="00000001",
            bsns_year=2022,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="파생된 KAM",
            ordinal=0,
        ))

    dry = clear_cold_derived_inline_content(year_to=2023, limit=10, dry_run=True)
    assert dry["checked"] == 1
    assert dry["dry_run"] is True

    out = clear_cold_derived_inline_content(year_to=2023, limit=10, dry_run=False)
    assert out["checked"] == 1
    assert out["cleared"] == 1

    with get_session() as session:
        cleared = session.query(SourceDocument).filter_by(rcept_no="20230331000001").one()
        preserved = session.query(SourceDocument).filter_by(rcept_no="20230331000002").one()
        assert cleared.raw_content == ""
        assert cleared.storage_status == "derived_only"
        assert preserved.raw_content == "<DOCUMENT>파생 없음</DOCUMENT>"


def test_run_document_extractors_skips_empty_source_without_deleting_sections(temp_engine):
    from kreports.collector.report_document_collector import run_document_extractors
    from kreports.db.engine import get_session
    from kreports.db.models import ReportSection, SourceDocument

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20260331000001",
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            report_nm="감사보고서",
            content_type="xml",
            raw_content="",
            doc_hash="empty",
            storage_status="derived_only",
        ))
        session.add(ReportSection(
            rcept_no="20260331000001",
            corp_code="00000001",
            bsns_year=2025,
            source_type="audit_report",
            section_key="kam",
            section_title="핵심감사사항",
            body_text="기존 KAM 섹션은 보존되어야 합니다.",
            ordinal=0,
        ))

    out = run_document_extractors(year=2025, source_type="audit_report")

    assert out["total"] == 1
    assert out["skipped"] == 1
    assert out["failed"] == 0
    with get_session() as session:
        section = session.query(ReportSection).one()
        assert section.body_text == "기존 KAM 섹션은 보존되어야 합니다."
