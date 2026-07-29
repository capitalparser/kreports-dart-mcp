from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from kreports.collector import on_demand
from kreports.collector.on_demand import fetch_disclosure_on_demand
from kreports.db.engine import get_session
from kreports.db.models import BackfillRun, Company, SourceDocument
from kreports.runtime import raw_persistence_allowed, require_runtime_write, runtime_write_allowed


def _source_document_count() -> int:
    with get_session() as session:
        return int(session.query(SourceDocument).count())


def test_readonly_on_demand_fetch_is_ephemeral(temp_engine, monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.setattr(on_demand, "_fetch_document_xml_with_user_key", lambda *_: "<DOC>body</DOC>")
    monkeypatch.setattr(
        on_demand,
        "_persist_source_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not persist in readonly mode")),
    )

    result = fetch_disclosure_on_demand(
        rcept_no="20250711000001",
        user_dart_api_key="caller-key",
        corp_code="00126380",
        year=2025,
        cache_policy="refresh",
    )

    assert result["persisted"] is False
    assert result["cache_policy_applied"] == "refresh_ephemeral"
    assert result["body_excerpt"] == "body"
    assert _source_document_count() == 0
    assert "caller-key" not in str(result)


def test_default_runtime_denies_writes_and_raw_persistence(monkeypatch):
    monkeypatch.delenv("KREPORTS_RUNTIME_MODE", raising=False)
    monkeypatch.delenv("KREPORTS_ENABLE_RAW_BACKFILL", raising=False)

    assert runtime_write_allowed("test") is False
    assert raw_persistence_allowed() is False
    with pytest.raises(RuntimeError, match="requires collector mode"):
        require_runtime_write("test")


def test_raw_persistence_requires_external_non_inline_storage(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BACKEND", "gcs")
    monkeypatch.setenv("RAW_STORAGE_BUCKET", "kreports-raw-documents")
    monkeypatch.setenv("RAW_STORAGE_KEEP_INLINE", "false")

    assert runtime_write_allowed("test") is True
    assert raw_persistence_allowed() is True


def test_readonly_orm_write_is_rejected_before_commit(temp_engine, monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        with get_session() as session:
            session.add(Company(corp_code="00126380", corp_name="테스트"))

    with get_session() as session:
        assert session.query(Company).count() == 0


def test_readonly_text_insert_is_rejected_without_mutating_db(temp_engine, monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    with pytest.raises(RuntimeError, match="requires collector mode"):
        with get_session() as session:
            session.execute(text("INSERT INTO companies (corp_code, corp_name) VALUES ('00126380', '테스트')"))

    with get_session() as session:
        assert session.query(Company).count() == 0


@pytest.mark.parametrize("statement", [
    "/* maintainer note */ INSERT INTO companies (corp_code, corp_name) VALUES ('00126380', '테스트')",
    "-- maintainer note\nUPDATE companies SET corp_name='변경' WHERE corp_code='00126380'",
    "WITH target AS (SELECT corp_code FROM companies WHERE corp_code='00126380') UPDATE companies SET corp_name='변경' WHERE corp_code IN (SELECT corp_code FROM target)",
])
def test_readonly_commented_text_dml_is_rejected(temp_engine, monkeypatch, statement):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="원본"))
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        with get_session() as session:
            session.execute(text(statement))

    with get_session() as session:
        assert session.query(Company).one().corp_name == "원본"


def test_readonly_select_and_with_select_remain_allowed(temp_engine, monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with get_session() as session:
        assert session.execute(text("SELECT count(*) FROM companies")).scalar() == 0
        assert session.execute(text("WITH sample AS (SELECT 1 AS value) SELECT value FROM sample")).scalar() == 1


@pytest.mark.parametrize("bulk_method", ["bulk_save_objects", "bulk_insert_mappings"])
def test_readonly_bulk_inserts_are_rejected(temp_engine, monkeypatch, bulk_method):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        with get_session() as session:
            if bulk_method == "bulk_save_objects":
                session.bulk_save_objects([Company(corp_code="00126380", corp_name="테스트")])
            else:
                session.bulk_insert_mappings(Company, [{"corp_code": "00126380", "corp_name": "테스트"}])

    with get_session() as session:
        assert session.query(Company).count() == 0


def test_readonly_bulk_update_is_rejected_without_changing_row(temp_engine, monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    with get_session() as session:
        row = Company(corp_code="00126380", corp_name="원본")
        session.add(row)
        session.flush()
        corp_code = row.corp_code
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        with get_session() as session:
            session.bulk_update_mappings(Company, [{"corp_code": corp_code, "corp_name": "변경"}])

    with get_session() as session:
        assert session.query(Company).one().corp_name == "원본"


def test_collector_bulk_persistence_is_allowed(temp_engine, monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    with get_session() as session:
        session.bulk_insert_mappings(Company, [{"corp_code": "00126380", "corp_name": "테스트"}])

    with get_session() as session:
        assert session.query(Company).count() == 1


def test_collector_session_writes_are_allowed(temp_engine, monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    with get_session() as session:
        session.add(Company(corp_code="00126380", corp_name="테스트"))

    with get_session() as session:
        assert session.query(Company).count() == 1


def test_raw_document_store_write_is_rejected_in_readonly_mode(tmp_path, monkeypatch):
    from kreports.storage.raw_documents import RawDocumentStore

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    store = RawDocumentStore(base_dir=tmp_path)

    with pytest.raises(RuntimeError, match="raw persistence"):
        store.write(
            corp_code="00126380", bsns_year=2025, source_type="audit_report",
            rcept_no="20250711000001", content_type="xml", content="<DOC>body</DOC>",
        )

    assert list(tmp_path.rglob("*")) == []


def test_evidence_blob_store_write_is_rejected_in_readonly_mode(tmp_path, monkeypatch):
    from kreports.storage.evidence_blobs import EvidenceBlobStore

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    store = EvidenceBlobStore(base_dir=tmp_path)

    with pytest.raises(RuntimeError, match="requires collector mode"):
        store.write(table_name="evidence_documents", row_id=1, corp_code="00126380", bsns_year=2025, content="body")

    assert list(tmp_path.rglob("*")) == []


def test_init_db_is_rejected_in_readonly_before_creating_tables(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect
    import kreports.db.engine as engine_module

    isolated = create_engine(f"sqlite:///{tmp_path / 'readonly.db'}")
    monkeypatch.setattr(engine_module, "engine", isolated)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        engine_module.init_db()

    assert inspect(isolated).get_table_names() == []


def test_release_gate_reports_stale_running_backfill_without_repairing_it(temp_engine, monkeypatch):
    from kreports.quality import release_gate

    monkeypatch.setattr(release_gate, "investor_dataset_readiness_snapshot", lambda: {"required_gaps": []})
    monkeypatch.setattr(release_gate, "auditor_feature_readiness_snapshot", lambda: {"feature_status": {}})
    with get_session() as session:
        session.add(BackfillRun(
            task_type="financials",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ))
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = release_gate.evaluate_release_gate("public_runtime")

    assert report["profile"] == "public_runtime"
    assert "stale_backfill_run" in report["required_failures"]
    assert report["tool_count"] == 33


def test_release_gate_fails_closed_without_manifest_contract(temp_engine, monkeypatch):
    from kreports.quality import release_gate

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.setattr(release_gate, "investor_dataset_readiness_snapshot", lambda: {"required_gaps": []})
    monkeypatch.setattr(release_gate, "auditor_feature_readiness_snapshot", lambda: {"feature_status": {}})

    report = release_gate.evaluate_release_gate("public_runtime")

    assert report["ok"] is False
    assert report["schema_version"] == "unknown"
    assert "release_manifest_unavailable" in report["required_failures"]


def test_release_gate_makes_investor_core_gap_required(temp_engine, monkeypatch):
    from kreports.quality import release_gate

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.setattr(release_gate, "investor_dataset_readiness_snapshot", lambda: {"required_gaps": ["financial_compact_core_2025"]})
    monkeypatch.setattr(release_gate, "auditor_feature_readiness_snapshot", lambda: {"feature_status": {}})

    report = release_gate.evaluate_release_gate("public_runtime")

    assert "investor_core_coverage" in report["required_failures"]


def test_empty_manifest_tables_do_not_make_release_manifest_available(temp_engine, monkeypatch):
    from kreports.quality import release_gate

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.setattr(release_gate, "investor_dataset_readiness_snapshot", lambda: {"required_gaps": []})
    monkeypatch.setattr(release_gate, "auditor_feature_readiness_snapshot", lambda: {"feature_status": {}})

    report = release_gate.evaluate_release_gate("public_runtime")

    assert "release_manifest_unavailable" in report["required_failures"]


def test_written_manifest_is_visible_to_readonly_release_gate(
    temp_engine,
    monkeypatch,
):
    from kreports.db.engine import write_dataset_manifest
    from kreports.db.migrations import MIGRATIONS, apply_schema_migrations
    from kreports.quality import release_gate

    with temp_engine.begin() as conn:
        apply_schema_migrations(conn)
    write_dataset_manifest("compact-release-v1")

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.setattr(
        release_gate,
        "investor_dataset_readiness_snapshot",
        lambda: {"required_gaps": []},
    )
    monkeypatch.setattr(
        release_gate,
        "auditor_feature_readiness_snapshot",
        lambda: {"feature_status": {}},
    )

    report = release_gate.evaluate_release_gate("public_runtime")

    assert "release_manifest_unavailable" not in report["required_failures"]
    assert report["schema_version"] == MIGRATIONS[-1].revision
    assert report["dataset_version"] == "compact-release-v1"


def test_release_gate_returns_stable_failure_when_db_inspection_raises(monkeypatch):
    from kreports.quality import release_gate

    monkeypatch.setattr(release_gate, "_runtime_schema_state", lambda: (_ for _ in ()).throw(OSError("db unavailable")))

    report = release_gate.evaluate_release_gate("public_runtime")

    assert report["ok"] is False
    assert report["schema_version"] == "unknown"
    assert report["dataset_version"] == "unknown"
    assert "runtime_db_unavailable" in report["required_failures"]
