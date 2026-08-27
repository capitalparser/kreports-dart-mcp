import os
import re
import subprocess
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


def _documented_promotion_script() -> str:
    guide = (REPO_ROOT / "docs" / "deploy-http-mcp.md").read_text()
    section = guide.split("Compact runtime artifact flow", 1)[1].split(
        "The build command", 1
    )[0]
    blocks = re.findall(r"```bash\n(.*?)\n```", section, flags=re.DOTALL)
    promotion_blocks = [block for block in blocks if "export-runtime-db" in block]
    assert len(promotion_blocks) == 1
    return promotion_blocks[0]


def _write_promotion_fakes(fake_bin: Path) -> None:
    fake_bin.mkdir()
    kreports = fake_bin / "kreports"
    kreports.write_text(
        """#!/bin/sh
set -eu
command_name="$1"
shift
db_path=""
output_path=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --db) db_path="$2"; shift 2 ;;
    --output) output_path="$2"; shift 2 ;;
    *) shift ;;
  esac
done
case "$command_name" in
  export-runtime-db)
    mkdir -p "$(dirname "$output_path")"
    printf 'synthetic-db' > "$output_path"
    ;;
  build-release-manifest)
    [ "$(basename "$db_path")" = "kreports.db" ] || exit 41
    printf 'synthetic-manifest' > "${db_path}.release.json"
    ;;
  verify-release-artifact)
    [ "$(basename "$db_path")" = "kreports.db" ] || exit 42
    [ -f "${db_path}.release.json" ] || exit 43
    if [ "${FAIL_FINAL_VERIFY:-0}" = "1" ] && [ "$db_path" = "./kreports.db" ]; then
      exit 44
    fi
    ;;
esac
"""
    )
    kreports.chmod(0o755)

    docker = fake_bin / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
case " $* " in
  *" stop kreports-mcp "*) printf 'stopped' > "$TEST_STATE/service.stopped" ;;
  *" up -d --force-recreate "*) printf 'running' > "$TEST_STATE/service.running" ;;
esac
"""
    )
    docker.chmod(0o755)


def _run_documented_promotion(tmp_path: Path, *, fail_final_verify: bool = False):
    fake_bin = tmp_path / "fake-bin"
    state = tmp_path / "state"
    state.mkdir()
    _write_promotion_fakes(fake_bin)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TEST_STATE": str(state),
        "TMPDIR": str(tmp_path),
        "FAIL_FINAL_VERIFY": "1" if fail_final_verify else "0",
    }
    result = subprocess.run(
        ["bash"],
        input=_documented_promotion_script().replace(
            "<gcs-bucket-name>", "synthetic-bucket"
        ),
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, state


def test_public_compose_fails_closed_with_a_readonly_verified_artifact_pair():
    """Catches removal of auth, readonly mode, manifest proof, or loopback binding."""
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
    assert '"127.0.0.1:8765:8765"' in compose_text
    assert '"8765:8765"' not in compose_text
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
    assert "KREPORTS_MCP_ENABLE_CREDENTIAL_TOOLS" not in public

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

    assert "33 public tools" in guide
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


def test_documented_promotion_keeps_manifest_bound_to_final_database_basename(
    tmp_path,
):
    """Catches export or promotion under a basename other than kreports.db."""
    result, state = _run_documented_promotion(tmp_path)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "kreports.db").read_text() == "synthetic-db"
    assert (
        tmp_path / "kreports.db.release.json"
    ).read_text() == "synthetic-manifest"
    assert (state / "service.running").read_text() == "running"


def test_documented_promotion_leaves_service_stopped_when_final_verification_fails(
    tmp_path,
):
    """Catches a restart that is not gated by final-path artifact verification."""
    result, state = _run_documented_promotion(tmp_path, fail_final_verify=True)

    assert result.returncode == 44
    assert (state / "service.stopped").read_text() == "stopped"
    assert not (state / "service.running").exists()


def test_source_archive_guide_keeps_drive_and_public_runtime_separate():
    """The archival path must never become a Drive-mounted MCP database."""
    guide = (REPO_ROOT / "docs" / "source-archive-backfill.md").read_text()

    assert "Do not mount SQLite on Google Drive" in guide
    assert "--apply" in guide
    assert "public MCP queries do not call Google Drive" in guide
    assert "rclone about '<drive-remote-name>:' --json" in guide
    assert "does not perform a reliable remaining-DART-quota preflight" in guide
    assert "--max-dart-calls` is a local physical-request cap" in guide


def test_all_issuer_source_archive_guide_preserves_cohort_and_historic_status_boundaries():
    """Catches all-issuer operations that conflate archive inclusion with listing proof."""
    guide = (REPO_ROOT / "docs" / "source-archive-backfill.md").read_text()

    for phrase in (
        "--universe all-annual-issuers",
        "annual_report_issuer_outside_verified_markets",
        "unclassified",
        "not proof of unlisted",
        "not_krx_listed_verified",
        "unlisted_confirmed",
    ):
        assert phrase in guide

    assert "fresh v3 Drive prefix and local state directory" in guide
    assert "cohort counts and target digest" in guide
    assert "dated official KRX KOSPI/KOSDAQ/KONEX raw exports" in guide
    assert "normalization manifest" in guide
    assert "dated issuer-status source" in guide


def test_drive_archive_diagnostic_is_safe_pre_deadline_evidence():
    """The prior observation must not be presented as deadline-bound v3 readiness."""
    diagnostic = (
        REPO_ROOT / "docs" / "reports" / "2026-08-28-drive-archive-diagnostic.md"
    ).read_text()

    assert "/private/" not in diagnostic
    assert "pre-deadline" in diagnostic
    assert "not a time-bounded v3 readiness result" in diagnostic
