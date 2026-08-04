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
    assert "KREPORTS_ENABLE_RAW_BACKFILL=0" in collector
    assert "RAW_BACKFILL_ENABLED" not in collector
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


def test_deployment_guide_never_treats_the_public_runtime_db_as_collector_state():
    """Catches guidance that mutates a mounted public DB or skips release promotion."""
    guide = (REPO_ROOT / "docs" / "deploy-http-mcp.md").read_text()

    assert "visible without redeploy" not in guide
    assert "separate writable maintainer DB" in guide
    assert "never writes the mounted public runtime DB" in guide
    assert "atomic deployment" in guide
    assert "docker compose -f docker-compose.deploy.yml up -d --force-recreate" in guide
    compact_flow = guide.split("Compact runtime artifact flow", 1)[1].split(
        "The build command", 1
    )[0]
    assert compact_flow.index("kreports export-runtime-db") < compact_flow.index(
        "kreports build-release-manifest"
    ) < compact_flow.index("kreports verify-release-artifact")
    assert compact_flow.index("kreports verify-release-artifact") < compact_flow.index(
        "kreports upload-runtime-db-artifact"
    )


def test_private_collector_guide_documents_exact_raw_backfill_opt_in():
    """Catches a misspelled or fail-open raw-backfill environment contract."""
    guide = (REPO_ROOT / "docs" / "deploy-http-mcp.md").read_text()

    assert "KREPORTS_ENABLE_RAW_BACKFILL=0" in guide
    assert "Only the exact value `1` opts in" in guide
    assert "RAW_BACKFILL_ENABLED" not in guide


def test_host_release_verification_never_uses_the_container_data_path():
    """Catches host commands that point at a container-only /data path."""
    guide = (REPO_ROOT / "docs" / "deploy-http-mcp.md").read_text()

    assert "kreports verify-release-artifact --db ./kreports.db" in guide
    assert "kreports verify-release-artifact --db /data/kreports.db" not in guide


def test_compose_render_uses_only_the_placeholder_template_and_is_not_persisted():
    """Catches interpolation or persistence of a live bearer token during config review."""
    guide = (REPO_ROOT / "docs" / "deploy-http-mcp.md").read_text()
    safe_config = (
        "docker compose --env-file deploy/public-mcp.env.example "
        "-f docker-compose.deploy.yml config"
    )

    assert safe_config in guide
    assert "config >" not in guide
    assert guide.index(safe_config) < guide.index("export KREPORTS_MCP_TOKEN")
