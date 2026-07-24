from datetime import datetime, timedelta, timezone

import pytest

from kreports.collector import on_demand
from kreports.collector.on_demand import fetch_disclosure_on_demand
from kreports.db.engine import get_session
from kreports.db.models import BackfillRun, SourceDocument
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
    monkeypatch.setenv("RAW_STORAGE_KEEP_INLINE", "false")

    assert runtime_write_allowed("test") is True
    assert raw_persistence_allowed() is True


def test_release_gate_reports_stale_running_backfill_without_repairing_it(temp_engine, monkeypatch):
    from kreports.quality import release_gate

    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.setattr(release_gate, "investor_dataset_readiness_snapshot", lambda: {"required_gaps": []})
    monkeypatch.setattr(release_gate, "auditor_feature_readiness_snapshot", lambda: {"feature_status": {}})
    with get_session() as session:
        session.add(BackfillRun(
            task_type="financials",
            status="running",
            started_at=datetime.now(timezone.utc) - timedelta(hours=2),
        ))

    report = release_gate.evaluate_release_gate("public_runtime")

    assert report["profile"] == "public_runtime"
    assert "stale_backfill_run" in report["required_failures"]
    assert report["tool_count"] == 31


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
