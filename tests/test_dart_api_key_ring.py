from __future__ import annotations

import json

import pytest


def test_quota_rotation_reloads_new_key_without_persisting_secret(tmp_path):
    from kreports.collector.dart_api_key_ring import DartApiKeyRing

    key_file = tmp_path / "dart-api-keys"
    key_file.write_text("", encoding="utf-8")
    key_file.chmod(0o600)
    state_file = tmp_path / "rotation.json"
    ring = DartApiKeyRing.from_values(
        primary_key="primary-secret", key_file=key_file, state_file=state_file
    )
    key_file.write_text("second-secret\n", encoding="utf-8")

    assert ring.advance_after_quota() is True
    assert ring.current_key == "second-secret"
    persisted = state_file.read_text(encoding="utf-8")
    assert "primary-secret" not in persisted
    assert "second-secret" not in persisted
    assert json.loads(persisted)["current_key_id"] == ring.current_key_id


def test_restart_resumes_the_last_selected_key(tmp_path):
    from kreports.collector.dart_api_key_ring import DartApiKeyRing

    key_file = tmp_path / "dart-api-keys"
    key_file.write_text("second-secret\n", encoding="utf-8")
    key_file.chmod(0o600)
    state_file = tmp_path / "rotation.json"
    first = DartApiKeyRing.from_values(
        primary_key="primary-secret", key_file=key_file, state_file=state_file
    )
    assert first.advance_after_quota() is True

    resumed = DartApiKeyRing.from_values(
        primary_key="primary-secret", key_file=key_file, state_file=state_file
    )

    assert resumed.current_key == "second-secret"


def test_key_file_must_be_owner_only(tmp_path):
    from kreports.collector.dart_api_key_ring import DartApiKeyRing

    key_file = tmp_path / "dart-api-keys"
    key_file.write_text("second-secret\n", encoding="utf-8")
    key_file.chmod(0o644)

    with pytest.raises(ValueError, match="owner-only"):
        DartApiKeyRing.from_values(
            primary_key="primary-secret",
            key_file=key_file,
            state_file=tmp_path / "rotation.json",
        )


def test_scoped_key_overrides_settings_without_mutating_global(monkeypatch):
    from kreports.collector import fetcher

    monkeypatch.setattr(fetcher.settings, "dart_api_key", "primary-secret")

    with fetcher.dart_api_key_scope("second-secret"):
        assert fetcher._dart_api_key() == "second-secret"

    assert fetcher._dart_api_key() == "primary-secret"
