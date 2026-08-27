import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from kreports.cli.main import app as cli_app
from kreports.mcp.http_server import create_app, run_http


def test_remote_http_requires_token_by_default():
    with pytest.raises(RuntimeError, match="KREPORTS_MCP_TOKEN is required"):
        run_http(host="127.0.0.1", port=9)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "203.0.113.9"])
def test_unauthenticated_http_rejects_non_loopback_host_before_startup(host, monkeypatch):
    """Catches an authless MCP endpoint accidentally becoming internet-reachable."""
    import uvicorn

    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda *_args, **_kwargs: pytest.fail("uvicorn must not start"),
    )
    with pytest.raises(RuntimeError, match="loopback host"):
        run_http(host=host, port=9, allow_unauthenticated=True)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_create_app_allows_explicit_unauthenticated_loopback_only(host):
    """Catches a local tunnel mode that no longer works on a loopback listener."""
    app = create_app(token=None, host=host, allow_unauthenticated=True)

    with TestClient(app) as client:
        response = client.get("/mcp")

    assert response.status_code != 401


def test_create_app_rejects_implicit_unauthenticated_mode():
    """Catches app-factory callers bypassing the explicit local-only escape hatch."""
    with pytest.raises(RuntimeError, match="KREPORTS_MCP_TOKEN is required"):
        create_app()


def test_explicit_public_mode_allows_nonloopback_without_bearer():
    """Public read-only deployments must opt in without sharing a token."""
    app = create_app(host="0.0.0.0", public=True)

    with TestClient(app) as client:
        response = client.get("/mcp")

    assert response.status_code != 401


def test_public_mode_rejects_a_bearer_token_configuration():
    """A public endpoint must not accidentally retain an unpublished secret."""
    with pytest.raises(RuntimeError, match="public mode cannot use"):
        create_app(host="0.0.0.0", token="secret", public=True)


def test_serve_http_help_exposes_the_public_mode():
    """The explicit public deployment switch must be discoverable from the CLI."""
    result = CliRunner().invoke(cli_app, ["serve-http", "--help"])

    assert result.exit_code == 0, result.exception
    assert "--public" in result.output


def test_remote_http_health_and_bearer_guard():
    app = create_app(token="secret")

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"ok": True}

        unauthorized = client.get("/mcp")
        assert unauthorized.status_code == 401
        assert unauthorized.headers["www-authenticate"] == "Bearer"


def test_readyz_requires_bearer_for_release_details(monkeypatch):
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
        anonymous = client.get("/readyz")
        authorized = client.get(
            "/readyz",
            headers={"Authorization": "Bearer secret"},
        )

    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"] == "Bearer"
    assert authorized.status_code == 200
    assert authorized.json()["degraded_features"] == ["audit_procedure"]


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
        ready = client.get("/readyz", headers={"Authorization": "Bearer secret"})

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
        ready = client.get("/readyz", headers={"Authorization": "Bearer secret"})

    assert ready.status_code == 503
    assert ready.json()["required_failures"] == ["stale_backfill_run"]


def test_readyz_returns_stable_503_when_gate_raises(monkeypatch):
    from kreports.mcp import http_server

    monkeypatch.setattr(http_server, "evaluate_release_gate", lambda _profile: (_ for _ in ()).throw(OSError("db unavailable")))
    app = create_app(token="secret")

    with TestClient(app) as client:
        ready = client.get("/readyz", headers={"Authorization": "Bearer secret"})

    assert ready.status_code == 503
    assert ready.json()["schema_version"] == "unknown"
    assert ready.json()["required_failures"] == ["runtime_db_unavailable"]
