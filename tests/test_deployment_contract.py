from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _environment_entries(compose_text: str) -> set[str]:
    """Return the public service environment keys from the Compose mapping."""
    entries: set[str] = set()
    in_environment = False
    for line in compose_text.splitlines():
        if line == "    environment:":
            in_environment = True
            continue
        if in_environment and line.startswith("    ") and not line.startswith("      "):
            break
        if in_environment and line.startswith("      ") and ":" in line:
            entries.add(line.strip().split(":", 1)[0])
    return entries


def test_public_compose_fails_closed_with_a_readonly_verified_artifact_pair():
    """Catches removal of public auth, readonly mode, or manifest mount proof."""
    compose_text = (REPO_ROOT / "docker-compose.deploy.yml").read_text()

    assert _environment_entries(compose_text) == {
        "KREPORTS_RUNTIME_MODE",
        "DB_URL",
        "KREPORTS_MCP_TOKEN",
    }
    assert "KREPORTS_RUNTIME_MODE: readonly" in compose_text
    assert "KREPORTS_MCP_TOKEN: ${KREPORTS_MCP_TOKEN:?set KREPORTS_MCP_TOKEN}" in compose_text
    assert "./kreports.db:/data/kreports.db:ro" in compose_text
    assert (
        "./kreports.db.release.json:/data/kreports.db.release.json:ro"
        in compose_text
    )
    assert "DART_API_KEY" not in compose_text
    assert "RAW_STORAGE_" not in compose_text


def test_role_separated_templates_never_offer_collector_credentials_to_public_mcp():
    """Catches a public template that can leak or activate collector capability."""
    public = (REPO_ROOT / "deploy" / "public-mcp.env.example").read_text()
    collector = (REPO_ROOT / "deploy" / "private-collector.env.example").read_text()

    assert "KREPORTS_RUNTIME_MODE=readonly" in public
    assert "KREPORTS_MCP_TOKEN=" in public
    assert "DB_URL=sqlite:////data/kreports.db" in public
    assert "DART_API_KEY" not in public
    assert "RAW_STORAGE_" not in public

    assert "KREPORTS_RUNTIME_MODE=collector" in collector
    assert "DART_API_KEY=replace_with_opendart_key" in collector
    assert "DB_URL=sqlite:////path/to/writable/kreports.db" in collector
    assert "RAW_STORAGE_BACKEND=" in collector
    assert "RAW_STORAGE_BUCKET=" in collector
    assert "RAW_BACKFILL_ENABLED=false" in collector
    assert "KREPORTS_MCP_TOKEN" not in collector


def test_deployment_guide_preserves_ephemeral_fetch_and_evidence_readiness_boundaries():
    """Catches documentation that promises cache persistence or calls HTTP healthy data-ready."""
    guide = (REPO_ROOT / "docs" / "deploy-http-mcp.md").read_text()

    assert "34 public tools" in guide
    assert "inseparable deployment pair" in guide
    assert "ephemeral" in guide
    assert "does not persist or cache" in guide
    assert "Code-test success" in guide
    assert "HTTP liveness" in guide
    assert "Release readiness" in guide
    assert "Live-data coverage" in guide
    assert "docker compose -f docker-compose.deploy.yml config" in guide
    assert "kreports verify-release-artifact --db /data/kreports.db" in guide
