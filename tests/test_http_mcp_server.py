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
