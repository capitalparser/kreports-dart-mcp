import pytest
from starlette.testclient import TestClient

from kreports.mcp.http_server import create_app, run_http


def test_remote_http_requires_token_by_default():
    with pytest.raises(RuntimeError, match="KREPORTS_MCP_TOKEN is required"):
        run_http(host="127.0.0.1", port=9)


def test_remote_http_health_and_bearer_guard():
    app = create_app(token="secret")

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        unauthorized = client.get("/mcp")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"


def test_readyz_uses_release_gate_and_degraded_features_stay_ready(monkeypatch):
    from kreports.mcp import http_server

    monkeypatch.setattr(http_server, "evaluate_release_gate", lambda profile: {
        "ok": True,
        "profile": profile,
        "schema_version": "unknown",
        "dataset_version": "unknown",
        "required_failures": [],
        "degraded_features": ["audit_procedure"],
        "tool_count": 31,
    })
    app = create_app(token="secret")

    with TestClient(app) as client:
        ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json()["ok"] is True
    assert ready.json()["degraded_features"] == ["audit_procedure"]


def test_readyz_returns_503_only_for_required_failures(monkeypatch):
    from kreports.mcp import http_server

    monkeypatch.setattr(http_server, "evaluate_release_gate", lambda profile: {
        "ok": False,
        "profile": profile,
        "schema_version": "unknown",
        "dataset_version": "unknown",
        "required_failures": ["stale_backfill_run"],
        "degraded_features": [],
        "tool_count": 31,
    })
    app = create_app(token="secret")

    with TestClient(app) as client:
        ready = client.get("/readyz")

    assert ready.status_code == 503
    assert ready.json()["required_failures"] == ["stale_backfill_run"]


def test_readyz_returns_stable_503_when_gate_raises(monkeypatch):
    from kreports.mcp import http_server

    monkeypatch.setattr(http_server, "evaluate_release_gate", lambda _profile: (_ for _ in ()).throw(OSError("db unavailable")))
    app = create_app(token="secret")

    with TestClient(app) as client:
        ready = client.get("/readyz")

    assert ready.status_code == 503
    assert ready.json()["schema_version"] == "unknown"
    assert ready.json()["required_failures"] == ["runtime_db_unavailable"]
