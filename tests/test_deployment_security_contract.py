from pathlib import Path
from urllib.error import HTTPError


def test_docker_build_installs_the_locked_api_environment():
    """The release image must use the committed uv.lock, not unconstrained pip."""
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert "uv.lock" in dockerfile
    assert "uv sync --frozen --no-dev --extra api" in dockerfile
    assert 'pip install --no-cache-dir ".[api]"' not in dockerfile


def test_container_healthcheck_uses_authenticated_readiness_without_token_argv(monkeypatch):
    """A failed release gate must make the container unhealthy without exposing its token."""
    from kreports import deployment_healthcheck

    captured = {}

    def unavailable(ready_request, **_kwargs):
        captured["authorization"] = ready_request.get_header("Authorization")
        raise HTTPError("http://127.0.0.1:8765/readyz", 503, "blocked", {}, None)

    monkeypatch.setenv("KREPORTS_MCP_TOKEN", "secret")
    monkeypatch.setattr(
        deployment_healthcheck.request,
        "urlopen",
        unavailable,
    )

    assert deployment_healthcheck.main() == 1
    assert captured == {"authorization": "Bearer secret"}
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "python -m kreports.deployment_healthcheck" in dockerfile
    assert "KREPORTS_MCP_TOKEN" not in dockerfile
    compose = Path("docker-compose.deploy.yml").read_text(encoding="utf-8")
    assert 'test: ["CMD", "python", "-m", "kreports.deployment_healthcheck"]' in compose


def test_lightsail_healthcheck_allows_release_artifact_readiness_latency(monkeypatch):
    """A verified multi-gigabyte runtime may need longer than a probe default."""
    from kreports import deployment_healthcheck

    captured = {}

    class ReadyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def ready(ready_request, **kwargs):
        captured["authorization"] = ready_request.get_header("Authorization")
        captured["timeout"] = kwargs["timeout"]
        return ReadyResponse()

    monkeypatch.setenv("KREPORTS_MCP_TOKEN", "secret")
    monkeypatch.setattr(deployment_healthcheck.request, "urlopen", ready)

    assert deployment_healthcheck.main() == 0
    assert captured == {"authorization": "Bearer secret", "timeout": 20}
    assert "timeout: 25s" in Path("deploy/lightsail/compose.yaml").read_text(
        encoding="utf-8"
    )


def test_public_healthcheck_needs_no_bearer_token(monkeypatch):
    """Public MCP mode keeps the release probe internal without a shared key."""
    from kreports import deployment_healthcheck

    captured = {}

    class ReadyResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def ready(ready_request, **_kwargs):
        captured["authorization"] = ready_request.get_header("Authorization")
        return ReadyResponse()

    monkeypatch.delenv("KREPORTS_MCP_TOKEN", raising=False)
    monkeypatch.setattr(deployment_healthcheck.request, "urlopen", ready)

    assert deployment_healthcheck.main() == 0
    assert captured == {"authorization": None}


def test_lightsail_readonly_runtime_puts_settings_data_on_tmpfs():
    """Settings import must not attempt to create /root/.local on a readonly root."""
    compose = Path("deploy/lightsail/compose.yaml").read_text(encoding="utf-8")

    assert "read_only: true" in compose
    assert "XDG_DATA_HOME: /tmp/xdg" in compose
    assert "- /tmp" in compose


def test_lightsail_deployment_explicitly_enables_public_mcp_without_a_token():
    """The hosted public endpoint is opt-in and never loads a shared bearer."""
    compose = Path("deploy/lightsail/compose.yaml").read_text(
        encoding="utf-8"
    )
    env_example = Path(
        "deploy/lightsail/production.env.example"
    ).read_text(encoding="utf-8")

    assert "- --public" in compose
    assert "KREPORTS_MCP_TOKEN" not in compose
    assert "KREPORTS_MCP_TOKEN" not in env_example


def test_lightsail_caddy_only_proxies_public_mcp_and_liveness():
    """Detailed readiness remains a container-only release gate."""
    caddyfile = Path("deploy/lightsail/conf/Caddyfile").read_text(
        encoding="utf-8"
    )

    assert "handle /mcp" in caddyfile
    assert "handle /healthz" in caddyfile
    assert 'respond "Not Found" 404' in caddyfile


def test_default_db_path_honors_xdg_data_home_on_linux(monkeypatch, tmp_path):
    """Readonly containers can place Settings' fallback directory on tmpfs."""
    from kreports import config

    xdg_data_home = tmp_path / "xdg"
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data_home))

    assert config._resolve_default_db_url() == (
        f"sqlite:///{xdg_data_home / 'kreports' / 'kreports.db'}"
    )
