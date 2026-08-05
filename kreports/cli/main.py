import logging
import os
import json
import platform
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

# CLI는 headless 환경
os.environ.setdefault("KREPORTS_HEADLESS", "1")
os.environ.setdefault("DART_HEADLESS", "1")  # backward compat

import typer
from tabulate import tabulate

from kreports.config import settings
from kreports.db.models import (
    Company, Financial, Disclosure, Auditor, AuditFee, FetchLog,
    AccountingPolicyItem,
)

app = typer.Typer(
    name="kreports",
    help="KReports - Korean Financial Intelligence CLI",
    no_args_is_help=True,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


GOLDEN_STOCK_CODES = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "005380",  # 현대자동차
    "035420",  # NAVER
    "051910",  # LG화학
    "068270",  # 셀트리온
    "035720",  # 카카오
    "012330",  # 현대모비스
    "105560",  # KB금융
    "207940",  # 삼성바이오로직스
]

_ACTIVE_BACKFILL_LEASES = {}


def init_db() -> None:
    """Load mutable database initialization only for commands that need it."""
    from kreports.db.engine import init_db as initialize_database

    initialize_database()


def get_session():
    """Load the configured session factory only for commands that need it."""
    from kreports.db.engine import get_session as configured_session

    return configured_session()


@app.command("normalize-krx-listing")
def normalize_krx_listing_cmd(
    raw_path: Path = typer.Option(..., "--raw-path", help="Local KIND HTML-XLS receipt"),
    db_path: Path = typer.Option(..., "--db-path", help="Explicit read-only SQLite snapshot"),
    output_path: Path = typer.Option(..., "--output-path", help="New UTF-8 CSV output path"),
    as_of: str = typer.Option(..., "--as-of", help="Snapshot date, YYYY-MM-DD"),
    raw_source_uri: str = typer.Option(..., "--raw-source-uri", help="Official KRX HTTPS source URI"),
    raw_source_retrieved_at: str = typer.Option(
        ..., "--raw-source-retrieved-at", help="Timezone-aware ISO-8601 receipt timestamp"
    ),
) -> None:
    """Normalize a local KIND receipt without network access or database writes."""
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        canonical_raw_source_provenance,
        normalize_krx_listing_path,
        read_current_core_companies,
        write_normalized_listing_csv,
    )

    try:
        as_of_date = date.fromisoformat(as_of)
        provenance = canonical_raw_source_provenance(
            raw_source_uri,
            raw_source_retrieved_at,
            as_of=as_of_date,
        )
        resolved_raw_path = raw_path.expanduser().resolve(strict=True)
        if not resolved_raw_path.is_file():
            raise KrxListingNormalizationError("raw KIND path must be a readable file")
        companies = read_current_core_companies(db_path)
        result = normalize_krx_listing_path(resolved_raw_path, companies, as_of=as_of_date)
        write_normalized_listing_csv(output_path, result.csv_bytes)
    except (KrxListingNormalizationError, OSError, ValueError) as exc:
        typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        raise typer.Exit(code=2) from exc
    summary = {
        **result.summary,
        **provenance,
        "raw_source_storage_uri": resolved_raw_path.as_uri(),
        "output_path": str(output_path),
    }
    typer.echo(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


@app.command("backfill-audit-fee-observations")
def backfill_audit_fee_observations_cmd(
    year_from: Optional[int] = typer.Option(None, "--year-from"),
    year_to: Optional[int] = typer.Option(None, "--year-to"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Explicitly promote local audit-fee provenance JSON into claim history."""
    init_db()
    from kreports.maintenance.audit_fee_observation_backfill import (
        backfill_audit_fee_observations,
    )

    result = backfill_audit_fee_observations(
        year_from=year_from,
        year_to=year_to,
        dry_run=dry_run,
    )
    typer.echo(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


@app.command("renormalize-audit-fee-observations")
def renormalize_audit_fee_observations_cmd(
    year_from: Optional[int] = typer.Option(None, "--year-from"),
    year_to: Optional[int] = typer.Option(None, "--year-to"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Explicit local-only repair of v1 DS002 amount-unit projections."""
    init_db()
    from kreports.maintenance.audit_fee_observation_backfill import (
        renormalize_audit_fee_observations,
    )

    result = renormalize_audit_fee_observations(
        year_from=year_from,
        year_to=year_to,
        dry_run=dry_run,
    )
    typer.echo(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _run_clone_rehearsal_cli(
    *,
    source_db: Path,
    rehearsal_dir: Path,
    python_executable: Path,
    include_db_evidence: bool,
) -> None:
    for path, expected, reject_symlink in (
        (source_db, "file", True),
        (rehearsal_dir, "directory", True),
        (python_executable, "file", False),
    ):
        valid_kind = path.is_file() if expected == "file" else path.is_dir()
        if (
            not path.is_absolute()
            or (reject_symlink and path.is_symlink())
            or not valid_kind
        ):
            raise typer.BadParameter(
                f"{expected} path must be absolute and existing",
            )
    source_db = source_db.resolve(strict=True)
    rehearsal_dir = rehearsal_dir.resolve(strict=True)
    python_executable = python_executable.absolute()

    # Deliberately lazy: normal CLI startup must not bind the rehearsal
    # safety module, runtime database, or APFS-specific implementation.
    from kreports.maintenance.kam_backfill_rehearsal import (
        RehearsalRunError,
        format_live_sha256_unchanged,
        run_kam_schema_backfill_rehearsal,
    )

    try:
        arguments: dict[str, object] = {
            "source_db": source_db,
            "rehearsal_dir": rehearsal_dir,
            "repository_root": Path(__file__).resolve().parents[2],
            "python_executable": python_executable,
        }
        if include_db_evidence:
            arguments["include_db_evidence"] = True
        report = run_kam_schema_backfill_rehearsal(
            **arguments,
        )
    except RehearsalRunError as exc:
        report = {
            "status": "preflight_blocked",
            "report_path": str(exc.report_path or ""),
            "markdown_report_path": "",
            "clone_path": "",
            "live_sha256_unchanged": None,
        }

    status = str(report.get("status") or "preflight_blocked")
    clone_path = str(report.get("clone_path") or "")
    typer.echo(f"status={status}")
    typer.echo(f"json_report={report.get('report_path') or ''}")
    typer.echo(
        f"markdown_report={report.get('markdown_report_path') or ''}",
    )
    typer.echo(f"clone={clone_path}")
    typer.echo(f"clone_retained={str(bool(clone_path)).lower()}")
    typer.echo(
        "live_sha256_unchanged="
        f"{format_live_sha256_unchanged(report.get('live_sha256_unchanged'))}",
    )
    if status not in {
        "complete",
        "mcp_schema_closed",
        "data_quality_limited",
    }:
        raise typer.Exit(2)


@app.command("rehearse-kam-schema-backfill")
def rehearse_kam_schema_backfill_cmd(
    source_db: Annotated[Path, typer.Option("--source-db")],
    rehearsal_dir: Annotated[Path, typer.Option("--rehearsal-dir")],
    python_executable: Annotated[
        Path,
        typer.Option("--python-executable"),
    ] = Path(sys.executable),
) -> None:
    """Run a retained-clone KAM migration/backfill rehearsal."""
    _run_clone_rehearsal_cli(
        source_db=source_db,
        rehearsal_dir=rehearsal_dir,
        python_executable=python_executable,
        include_db_evidence=False,
    )


@app.command("rehearse-db-evidence-hardening")
def rehearse_db_evidence_hardening_cmd(
    source_db: Annotated[Path, typer.Option("--source-db")],
    rehearsal_dir: Annotated[Path, typer.Option("--rehearsal-dir")],
    python_executable: Annotated[
        Path,
        typer.Option("--python-executable"),
    ] = Path(sys.executable),
) -> None:
    """Rehearse all local database evidence rebuilds on a retained clone."""
    _run_clone_rehearsal_cli(
        source_db=source_db,
        rehearsal_dir=rehearsal_dir,
        python_executable=python_executable,
        include_db_evidence=True,
    )


@contextmanager
def _backfill_run_guard(
    *,
    task_type: str,
    year: int | None,
    market: str | None,
    params: dict[str, object],
    force: bool = False,
):
    """Record a batch run and block concurrent duplicate backfills."""
    from kreports.maintenance.backfill_runs import (
        BackfillAlreadyRunning,
        BackfillLease,
        classify_backfill_error,
    )

    init_db()
    try:
        lease = BackfillLease.start(
            task_type=task_type,
            year=year,
            market=market,
            params=params,
            force=force,
        )
    except BackfillAlreadyRunning as exc:
        typer.echo(f"오류: {exc}", err=True)
        raise typer.Exit(2) from exc

    _ACTIVE_BACKFILL_LEASES[lease.id] = lease

    try:
        yield lease.id
    except BaseException as exc:
        active_lease = _ACTIVE_BACKFILL_LEASES.pop(lease.id, None)
        if active_lease is not None:
            active_lease.fail(classify_backfill_error(exc), str(exc))
        raise
    else:
        active_lease = _ACTIVE_BACKFILL_LEASES.get(lease.id)
        if active_lease is not None:
            active_lease.succeed({})
            _ACTIVE_BACKFILL_LEASES.pop(lease.id, None)


def _finish_backfill_run(run_id: int, result: dict[str, object]) -> None:
    lease = _ACTIVE_BACKFILL_LEASES.get(run_id)
    if lease is None:
        raise RuntimeError(f"backfill run {run_id} is not owned by this process")
    lease.succeed(result)
    _ACTIVE_BACKFILL_LEASES.pop(run_id, None)


@app.command("repair-stale-backfills")
def repair_stale_backfills_cmd(
    timeout_seconds: int = typer.Option(3600, min=1),
    json_output: bool = typer.Option(False, "--json"),
):
    """Mark timed-out runs stale only when their owner process is dead."""
    from kreports.maintenance import backfill_runs
    from kreports.runtime import require_collector_mode

    require_collector_mode("repair-stale-backfills")
    init_db()
    result = backfill_runs.repair_stale_backfills(
        datetime.now(timezone.utc),
        timeout_seconds,
    )
    if json_output:
        typer.echo(
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    typer.echo(
        f"stale backfills repaired: {result['repaired_count']} "
        f"(ids={result['repaired_ids']})"
    )


@app.command("backfill-status")
def backfill_status_cmd(
    json_output: bool = typer.Option(False, "--json"),
    limit: int = typer.Option(50, min=1, max=500),
):
    """Show bounded, newest-first durable backfill state."""
    from kreports.maintenance.backfill_runs import list_backfill_status

    result = list_backfill_status(limit)
    if json_output:
        typer.echo(
            json.dumps(
                result,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    rows = [
        [
            row["id"],
            row["task_type"],
            row["year"],
            row["market"],
            row["status"],
            row["attempted"],
            row["saved"],
            row["no_data"],
            row["errors"],
            row["heartbeat_at"],
        ]
        for row in result["runs"]
    ]
    typer.echo(
        tabulate(
            rows,
            headers=[
                "id",
                "task_type",
                "year",
                "market",
                "status",
                "attempted",
                "saved",
                "no_data",
                "errors",
                "heartbeat_at",
            ],
            tablefmt="github",
        )
    )


@app.command("orchestrate-complete-backfill")
def orchestrate_complete_backfill_cmd(
    year_from: int = typer.Option(2021),
    year_to: int = typer.Option(2025),
    disclosure_end_year: int = typer.Option(2026),
    force: bool = typer.Option(False, "--force"),
):
    """Run the complete workflow with durable Python-owned progress."""
    from kreports.collector.scheduler import orchestrate_complete_backfill

    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)
    with _backfill_run_guard(
        task_type="complete_dataset",
        year=year_from,
        market="LISTED",
        params={
            "year_from": year_from,
            "year_to": year_to,
            "disclosure_end_year": disclosure_end_year,
            "raw_backfill": os.getenv("KREPORTS_ENABLE_RAW_BACKFILL", "0"),
        },
        force=force,
    ) as run_id:
        result = orchestrate_complete_backfill(
            _ACTIVE_BACKFILL_LEASES[run_id],
            year_from=year_from,
            year_to=year_to,
            disclosure_end_year=disclosure_end_year,
        )
        _finish_backfill_run(run_id, result)
    typer.echo(
        "complete backfill finished - "
        f"attempted: {result['attempted']}, "
        f"saved: {result['saved']}, errors: {result['errors']}"
    )


def _parse_stock_codes(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    values = []
    for token in raw.replace("\n", ",").split(","):
        stock_code = token.strip()
        if stock_code:
            values.append(stock_code)
    return values


def _resolve_companies_by_stock(stock_codes: list[str]) -> list[dict]:
    if not stock_codes:
        return []

    with get_session() as session:
        rows = (
            session.query(
                Company.stock_code,
                Company.corp_code,
                Company.corp_name,
                Company.market,
                Company.induty_code,
            )
            .filter(Company.stock_code.in_(stock_codes))
            .all()
        )

    by_stock = {
        row.stock_code: {
            "stock_code": row.stock_code,
            "corp_code": row.corp_code,
            "corp_name": row.corp_name,
            "market": row.market,
            "induty_code": row.induty_code,
        }
        for row in rows
    }
    missing = [code for code in stock_codes if code not in by_stock]
    if missing:
        typer.echo(f"오류: DB에 없는 종목코드가 있습니다: {', '.join(missing)}", err=True)
        raise typer.Exit(1)

    return [by_stock[code] for code in stock_codes]


def _dataset_health_snapshot() -> dict:
    from sqlalchemy import func

    with get_session() as session:
        total_companies = session.query(Company).filter(Company.stock_code.isnot(None)).count()
        companies_with_market = (
            session.query(Company)
            .filter(Company.stock_code.isnot(None), Company.market.isnot(None))
            .count()
        )
        companies_with_induty = (
            session.query(Company)
            .filter(Company.stock_code.isnot(None), Company.induty_code.isnot(None))
            .count()
        )
        financial_company_count = session.query(func.count(func.distinct(Financial.corp_code))).scalar() or 0
        disclosure_company_count = session.query(func.count(func.distinct(Disclosure.corp_code))).scalar() or 0
        auditor_company_count = session.query(func.count(func.distinct(Auditor.corp_code))).scalar() or 0
        audit_fee_company_count = session.query(func.count(func.distinct(AuditFee.corp_code))).scalar() or 0
        policy_company_count = session.query(func.count(func.distinct(AccountingPolicyItem.corp_code))).scalar() or 0
        auditors_orphan_rows = (
            session.query(func.count(Auditor.id))
            .outerjoin(Company, Auditor.corp_code == Company.corp_code)
            .filter(Company.corp_code.is_(None))
            .scalar()
            or 0
        )
        latest_financial = session.query(func.max(Financial.fetched_at)).scalar()
        latest_disclosure = session.query(func.max(Disclosure.fetched_at)).scalar()
        latest_auditor = session.query(func.max(Auditor.fetched_at)).scalar()
        latest_audit_fee = session.query(func.max(AuditFee.fetched_at)).scalar()
        latest_policy = session.query(func.max(AccountingPolicyItem.fetched_at)).scalar()

    golden_rows = []
    for company in _resolve_companies_by_stock(GOLDEN_STOCK_CODES):
        with get_session() as session:
            golden_rows.append({
                "stock_code": company["stock_code"],
                "corp_name": company["corp_name"],
                "market": company["market"] or "-",
                "induty_code": company["induty_code"] or "-",
                "financial_rows": session.query(Financial).filter_by(corp_code=company["corp_code"]).count(),
                "disclosure_rows": session.query(Disclosure).filter_by(corp_code=company["corp_code"]).count(),
                "auditor_rows": session.query(Auditor).filter_by(corp_code=company["corp_code"]).count(),
                "audit_fee_rows": session.query(AuditFee).filter_by(corp_code=company["corp_code"]).count(),
                "policy_rows": session.query(AccountingPolicyItem).filter_by(corp_code=company["corp_code"]).count(),
            })

    return {
        "total_companies": total_companies,
        "companies_with_market": companies_with_market,
        "companies_with_induty": companies_with_induty,
        "financial_company_count": financial_company_count,
        "disclosure_company_count": disclosure_company_count,
        "auditor_company_count": auditor_company_count,
        "audit_fee_company_count": audit_fee_company_count,
        "policy_company_count": policy_company_count,
        "auditors_orphan_rows": auditors_orphan_rows,
        "latest_financial": latest_financial,
        "latest_disclosure": latest_disclosure,
        "latest_auditor": latest_auditor,
        "latest_audit_fee": latest_audit_fee,
        "latest_policy": latest_policy,
        "golden_rows": golden_rows,
    }


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init():
    """DB 테이블 생성 및 마이그레이션."""
    init_db()
    typer.echo("DB 초기화 완료.")


@app.command()
def serve():
    """MCP stdio 서버를 실행한다. Claude Desktop / Claude Code에 연결."""
    from kreports.mcp.server import main as mcp_main
    mcp_main()


@app.command("serve-http")
def serve_http(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(8765, "--port", help="Bind port"),
    path: str = typer.Option("/mcp", "--path", help="MCP HTTP path"),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help="Bearer token. Defaults to KREPORTS_MCP_TOKEN when omitted.",
    ),
    allow_unauthenticated: bool = typer.Option(
        False,
        "--allow-unauthenticated",
        help="Allow requests without bearer authentication.",
    ),
    stateless: bool = typer.Option(False, "--stateless", help="Use stateless Streamable HTTP sessions."),
    json_response: bool = typer.Option(False, "--json-response", help="Return JSON responses instead of SSE streams."),
    allowed_hosts: Optional[str] = typer.Option(
        None,
        "--allowed-hosts",
        help="Comma-separated Host allowlist for DNS rebinding protection.",
    ),
    allowed_origins: Optional[str] = typer.Option(
        None,
        "--allowed-origins",
        help="Comma-separated Origin allowlist for DNS rebinding protection.",
    ),
):
    """MCP Streamable HTTP 서버를 실행한다. Claude Web remote connector용."""
    from kreports.mcp.http_server import run_http

    run_http(
        host=host,
        port=port,
        path=path,
        token=token,
        allow_unauthenticated=allow_unauthenticated,
        stateless=stateless,
        json_response=json_response,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mcp_launcher_path() -> Path:
    suffix = ".cmd" if os.name == "nt" else ".sh"
    path = _project_root() / "scripts" / f"kreports-mcp{suffix}"
    if path.exists():
        return path
    return _project_root() / "scripts" / "kreports-mcp.cmd"


def _json_print(data: dict) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command("mcp-config")
def mcp_config(
    target: str = typer.Option(
        "vscode",
        "--target",
        "-t",
        help="vscode / cursor / claude / code-cli",
    ),
):
    """IDE/CLI MCP 클라이언트에 붙일 설정 JSON을 출력한다."""
    root = _project_root()
    launcher = _mcp_launcher_path()
    env_file = root / ".env"
    target_norm = target.lower().strip()

    if target_norm == "vscode":
        _json_print({
            "servers": {
                "kreports": {
                    "type": "stdio",
                    "command": str(launcher),
                    "envFile": str(env_file),
                }
            }
        })
        return

    if target_norm in ("cursor", "claude", "claude-desktop"):
        _json_print({
            "mcpServers": {
                "kreports": {
                    "command": str(launcher),
                }
            }
        })
        return

    if target_norm in ("code-cli", "vscode-cli"):
        payload = {
            "name": "kreports",
            "type": "stdio",
            "command": str(launcher),
            "envFile": str(env_file),
        }
        typer.echo(f"code --add-mcp {json.dumps(payload, ensure_ascii=False)}")
        return

    raise typer.BadParameter("target은 vscode / cursor / claude / code-cli 중 하나여야 합니다.")


@app.command("mcp-doctor")
def mcp_doctor(
    json_output: bool = typer.Option(False, "--json", help="점검 결과를 JSON으로 출력"),
):
    """MCP 실행 환경을 점검한다. IDE 연결 전 빠른 smoke check 용도."""
    from kreports.mcp.tools import ALL_TOOLS

    root = _project_root()
    launcher = _mcp_launcher_path()
    env_file = root / ".env"
    python_exe = shutil.which(os.environ.get("KREPORTS_PYTHON") or "python")

    checks = {
        "project_root": str(root),
        "python": python_exe or "",
        "python_version": platform.python_version(),
        "launcher": str(launcher),
        "launcher_exists": launcher.exists(),
        "env_file": str(env_file),
        "env_file_exists": env_file.exists(),
        "dart_api_key_present": bool(settings.dart_api_key or os.environ.get("DART_API_KEY")),
        "tool_count": len(ALL_TOOLS),
        "tools": [tool.name for tool in ALL_TOOLS],
    }
    checks["collection_ready"] = bool(checks["dart_api_key_present"])

    try:
        from kreports.analysis.api import search_company
        sample = search_company("삼성전자", limit=1)
        checks["analysis_import_ok"] = True
        checks["db_query_ok"] = True
        checks["sample_company_found"] = bool(sample)
    except Exception as exc:
        checks["analysis_import_ok"] = False
        checks["db_query_ok"] = False
        checks["db_error"] = str(exc)

    checks["ok"] = bool(
        checks["python"]
        and checks["launcher_exists"]
        and checks.get("analysis_import_ok")
        and checks.get("db_query_ok")
        and checks.get("sample_company_found")
    )

    if json_output:
        _json_print(checks)
        return

    typer.echo("KReports MCP doctor")
    typer.echo(f"- project_root: {checks['project_root']}")
    typer.echo(f"- python: {checks['python'] or 'NOT FOUND'} ({checks['python_version']})")
    typer.echo(f"- launcher: {checks['launcher']} ({'ok' if checks['launcher_exists'] else 'missing'})")
    typer.echo(f"- .env: {checks['env_file']} ({'ok' if checks['env_file_exists'] else 'missing; optional for read-only MCP'})")
    typer.echo(f"- DART_API_KEY: {'ok; collection ready' if checks['collection_ready'] else 'missing; read-only MCP ok'}")
    typer.echo(f"- tools: {checks['tool_count']} ({', '.join(checks['tools'])})")
    typer.echo(f"- db query: {'ok' if checks.get('db_query_ok') else checks.get('db_error', 'failed')}")
    typer.echo(f"- sample company: {'ok' if checks.get('sample_company_found') else 'missing'}")
    typer.echo(f"RESULT: {'OK' if checks['ok'] else 'CHECK REQUIRED'}")


@app.command("mcp-smoke")
def mcp_smoke_cmd(
    company: str = typer.Option("005930", "--company", help="스모크 테스트 기준 회사"),
):
    """DART key 없이 read-only MCP 주요 도구를 호출한다."""
    from kreports.mcp.tools import call_tool

    calls = [
        ("search_company", {"query": company, "limit": 3}),
        ("get_financial_snapshot", {"company": company}),
        ("select_peer_group", {"company": company, "peer_limit": 5}),
        ("compare_to_industry_multi", {"company": company, "years_back": 2, "fs_strategy": "auto"}),
        ("compare_peer_audit_fees", {"company": company, "year": 2025}),
        ("compare_peer_risk_profile", {"company": company, "year": 2025}),
        ("compare_peer_accounting_policies", {"company": company, "year": 2025}),
        ("compare_peer_kam_topics", {"company": company, "year": 2025}),
        ("get_audit_report_sections", {"company": company, "year": 2025, "section_key": "kam"}),
        ("estimate_audit_hours_proxy", {"company": company, "year": 2025}),
        ("build_audit_acceptance_pack", {"company": company, "year": 2025}),
        ("get_accounting_policy", {"company": company, "bsns_year": 2025}),
    ]
    failures = []
    for name, args in calls:
        out = json.loads(call_tool(name, args))
        if "error" in out and "pre-built DB" not in str(out.get("error")):
            failures.append(f"{name}: {out['error']}")
        typer.echo(f"- {name}: {'FAIL' if any(f.startswith(name + ':') for f in failures) else 'OK'}")
    if failures:
        typer.echo("RESULT: CHECK REQUIRED")
        for item in failures:
            typer.echo(item)
        raise typer.Exit(1)
    typer.echo("RESULT: OK")


# ---------------------------------------------------------------------------
# collect-seed — 업종 벤치마킹용 핵심 기업 자동 수집
# ---------------------------------------------------------------------------

@app.command("collect-seed")
def collect_seed_cmd(
    size: str = typer.Option("small", help="small (350사 ~20분) / medium (950사 ~50분) / full (전체 ~11시간)"),
    year_from: Optional[int] = typer.Option(None, "--year-from", help="수집 시작 연도 (기본: 최근 3년)"),
    year_to: Optional[int] = typer.Option(None, "--year-to", help="수집 종료 연도 (기본: 올해)"),
    annual_only: bool = typer.Option(True, "--annual-only/--all-quarters", help="Q4만 수집 (벤치마킹 용도)"),
):
    """
    업종 벤치마킹용 핵심 기업 재무데이터를 수집한다.

    Q4(연간) 우선 수집으로 동종업종 비교를 빠르게 활성화한다.
    이미 수집된 데이터는 건너뛴다 (중복 수집 없음).

    예시:
      kreports collect-seed                      # KOSPI200+KOSDAQ150, 최근3년 Q4
      kreports collect-seed --size medium         # KOSPI 전체
      kreports collect-seed --all-quarters        # Q1~Q4 모두
      kreports collect-seed --year-from 2020      # 2020~올해
    """
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정. .env 파일에 DART_API_KEY를 설정하세요.", err=True)
        raise typer.Exit(1)

    from kreports.collector.seed_collector import collect_seed, SEED_SIZES

    cfg = SEED_SIZES.get(size, SEED_SIZES["small"])
    q_label = "Q4만" if annual_only else "Q1~Q4"
    typer.echo(f"seed 수집 시작: {cfg['desc']} · {q_label}")

    def _progress(done, total, name, year, q):
        if done % 50 == 0 or done == total or done == 1:
            typer.echo(f"\r  [{done:,}/{total:,}] {name} {year}Q{q}", nl=False)

    result = collect_seed(
        size=size,
        year_from=year_from,
        year_to=year_to,
        annual_only=annual_only,
        progress_callback=_progress,
    )
    typer.echo(
        f"\n완료 - {result['companies']:,}사 대상 | "
        f"수집 {result['collected']:,} | 건너뜀 {result['skipped']:,} | "
        f"오류 {result['error']:,} | 업종 커버 {result['industries_covered']}개"
    )


# ---------------------------------------------------------------------------
# sync-companies
# ---------------------------------------------------------------------------

@app.command("sync-companies")
def sync_companies(
    market: Optional[str] = typer.Option(
        None, help="수집 시장 필터 (KOSPI,KOSDAQ 또는 생략하면 전체 상장사)",
    ),
):
    """DART 기업코드 목록을 동기화한다."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    market_filter = [m.strip() for m in market.split(",")] if market else ["KOSPI", "KOSDAQ"]
    typer.echo(f"기업 동기화 시작 (시장: {market_filter})")

    from kreports.collector.corp_sync import sync_companies as _sync
    count = _sync(market_filter=market_filter)
    typer.echo(f"동기화 완료: {count:,}개사")


# ---------------------------------------------------------------------------
# enrich-market
# ---------------------------------------------------------------------------

@app.command("enrich-market")
def enrich_market_cmd(
    stocks: Optional[str] = typer.Option(
        None,
        "--stocks",
        help="쉼표 구분 종목코드. 지정 시 해당 기업만 보완.",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="최대 처리 건수. 미지정 시 전체 대상 처리.",
    ),
    request_delay: Optional[float] = typer.Option(
        None,
        "--request-delay",
        help="API 요청 간 대기 초. 미지정 시 settings.request_delay 사용.",
    ),
):
    """market 또는 induty_code가 NULL인 상장사의 시장구분·업종코드를 DART API로 보완한다."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    selected_stocks = _parse_stock_codes(stocks)
    corp_codes = None

    if selected_stocks:
        companies = _resolve_companies_by_stock(selected_stocks)
        corp_codes = [c["corp_code"] for c in companies]
        typer.echo(
            f"시장·업종 보완 시작: 지정 종목 {len(companies)}개 "
            f"({', '.join(c['stock_code'] for c in companies)})"
        )
    else:
        from sqlalchemy import or_

        with get_session() as session:
            null_count = (
                session.query(Company)
                .filter(Company.stock_code.isnot(None))
                .filter(or_(Company.market.is_(None), Company.induty_code.is_(None)))
                .count()
            )

        if null_count == 0:
            typer.echo("보완 대상이 없습니다 (market/induty_code 모두 채워짐).")
            raise typer.Exit(0)

        expected = limit or null_count
        delay = settings.request_delay if request_delay is None else request_delay
        typer.echo(
            f"시장·업종 보완 시작: {expected:,}개사 대상 "
            f"(request_delay={delay:.2f}s)"
        )

    from sqlalchemy import or_

    from kreports.collector.corp_sync import enrich_market as _enrich

    def _progress(done, total, corp_code):
        if done == 1 or done % 25 == 0 or done == total:
            typer.echo(f"\r[{done}/{total}] {corp_code}", nl=False)

    result = _enrich(
        progress_callback=_progress,
        corp_codes=corp_codes,
        limit=limit,
        request_delay=request_delay,
    )
    typer.echo(
        f"\n완료 - 처리 {result['total']:,}개사 | "
        f"업데이트 {result['updated']:,} | 기타 {result['skipped']:,} | "
        f"오류 {result['error']:,} | induty 채움 {result['induty_filled']:,}"
    )


# ---------------------------------------------------------------------------
# dataset-health
# ---------------------------------------------------------------------------

@app.command("dataset-health")
def dataset_health_cmd():
    """실사용 데이터셋 준비 상태를 요약해 출력한다."""
    snapshot = _dataset_health_snapshot()

    total = snapshot["total_companies"] or 1
    market_rate = snapshot["companies_with_market"] / total * 100
    induty_rate = snapshot["companies_with_induty"] / total * 100

    typer.echo("데이터셋 건강도")
    typer.echo(f"  상장사 마스터: {snapshot['total_companies']:,}개사")
    typer.echo(f"  market 채움:   {snapshot['companies_with_market']:,} ({market_rate:.1f}%)")
    typer.echo(f"  induty 채움:   {snapshot['companies_with_induty']:,} ({induty_rate:.1f}%)")
    typer.echo(f"  재무 커버:     {snapshot['financial_company_count']:,}개사")
    typer.echo(f"  공시 커버:     {snapshot['disclosure_company_count']:,}개사")
    typer.echo(f"  감사인 커버:   {snapshot['auditor_company_count']:,}개사")
    typer.echo(f"  감사보수 커버: {snapshot['audit_fee_company_count']:,}개사")
    typer.echo(f"  정책 커버:     {snapshot['policy_company_count']:,}개사")
    typer.echo(f"  감사인 orphan: {snapshot['auditors_orphan_rows']:,}건")

    typer.echo("\n최근 적재 시각")
    typer.echo(f"  financials: {snapshot['latest_financial'] or '-'}")
    typer.echo(f"  disclosures: {snapshot['latest_disclosure'] or '-'}")
    typer.echo(f"  auditors: {snapshot['latest_auditor'] or '-'}")
    typer.echo(f"  audit_fees: {snapshot['latest_audit_fee'] or '-'}")
    typer.echo(f"  policy_items: {snapshot['latest_policy'] or '-'}")

    golden_table = [
        [
            row["stock_code"],
            row["corp_name"],
            row["market"],
            row["induty_code"],
            row["financial_rows"],
            row["disclosure_rows"],
            row["auditor_rows"],
            row["audit_fee_rows"],
            row["policy_rows"],
        ]
        for row in snapshot["golden_rows"]
    ]
    typer.echo("\n골든셋 커버리지")
    typer.echo(
        tabulate(
            golden_table,
            headers=[
                "종목코드", "회사", "시장", "업종", "재무", "공시", "감사인", "감사보수", "정책",
            ],
            tablefmt="github",
        )
    )


@app.command("dataset-auditor-readiness")
def dataset_auditor_readiness_cmd(
    year: int = typer.Option(2025, "--year", help="기준 사업연도"),
    years_back: int = typer.Option(5, "--years-back", help="직전 N개년 데이터셋 커버리지 기준"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """감사인 peer/MCP 배포용 데이터셋 readiness를 점검한다."""
    from kreports.analysis.readiness import backfill_plan, auditor_readiness_snapshot, pct, readiness_verdict

    snapshot = auditor_readiness_snapshot(year, years_back=years_back)
    verdict = readiness_verdict(snapshot)
    plan = backfill_plan(snapshot)
    payload = {**snapshot, **verdict, "backfill_plan": plan}
    if json_output:
        _json_print(payload)
        return

    typer.echo(f"Auditor dataset readiness: {verdict['verdict']}")
    typer.echo(f"required_years: {', '.join(str(y) for y in snapshot['required_years'])}")
    for market, row in snapshot["markets"].items():
        listed = int(row["listed"] or 0)
        typer.echo(
            f"- {market}: financial(any) {row['financial_any_2025']}/{listed} "
            f"({pct(row['financial_any_2025'], listed)}%), "
            f"CFS {row['financial_cfs_2025']}/{listed} "
            f"({pct(row['financial_cfs_2025'], listed)}%), "
            f"business_report {row.get('business_report_2025', 0)}/{listed} "
            f"({pct(row.get('business_report_2025'), listed)}%), "
            f"audit_report {row.get('audit_report_2025', 0)}/{listed} "
            f"({pct(row.get('audit_report_2025'), listed)}%), "
            f"auditor {row.get('auditor_2025', 0)}/{listed} "
            f"({pct(row.get('auditor_2025'), listed)}%), "
            f"disclosure {row['disclosure_recent']}/{listed} "
            f"({pct(row['disclosure_recent'], listed)}%)"
        )
    typer.echo("5-year core coverage:")
    for y in snapshot["required_years"]:
        rows_for_year = snapshot["yearly_markets"].get(y, {})
        for market in ("KOSPI", "KOSDAQ"):
            row = rows_for_year.get(market, {})
            listed = int(row.get("listed") or 0)
            typer.echo(
                f"- {y} {market}: financial(any) {row.get('financial_any', 0)}/{listed} "
                f"({pct(row.get('financial_any'), listed)}%), "
                f"business_report {row.get('business_report', 0)}/{listed} "
                f"({pct(row.get('business_report'), listed)}%), "
                f"audit_report {row.get('audit_report', 0)}/{listed} "
                f"({pct(row.get('audit_report'), listed)}%), "
                f"auditor {row.get('auditor', 0)}/{listed} "
                f"({pct(row.get('auditor'), listed)}%)"
            )
    typer.echo(f"required_gaps: {', '.join(verdict['required_gaps']) or '-'}")
    typer.echo(f"recommended_gaps: {', '.join(verdict['recommended_gaps']) or '-'}")
    if plan["required_commands"]:
        typer.echo("required_backfill_commands:")
        for cmd in plan["required_commands"]:
            typer.echo(f"  {cmd}")
    if plan["recommended_commands"]:
        typer.echo("recommended_backfill_commands:")
        for cmd in plan["recommended_commands"]:
            typer.echo(f"  {cmd}")


@app.command("investor-dataset-readiness")
def investor_dataset_readiness_cmd(
    year: int = typer.Option(2025, "--year", help="기준 연도"),
    years_back: int = typer.Option(5, "--years-back", help="직전 N개년 데이터셋 커버리지 기준"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """투자자 관점 runtime DB readiness를 점검한다. 수시공시 원문은 온디맨드 대상이다."""
    from kreports.analysis.readiness import investor_dataset_readiness_snapshot

    snapshot = investor_dataset_readiness_snapshot(year=year, years_back=years_back, market=market)
    if json_output:
        _json_print(snapshot)
        return

    typer.echo(f"Investor dataset readiness: {snapshot['verdict']}")
    typer.echo(f"required_years: {', '.join(str(y) for y in snapshot['required_years'])}")
    typer.echo(f"listed_companies: {snapshot['listed_companies']:,}")
    typer.echo(
        "disclosure_body_policy: "
        f"{snapshot['disclosure_body_storage_policy']} "
        f"(required_for_runtime={snapshot['disclosure_body_required_for_runtime']})"
    )
    for row in snapshot["yearly"]:
        typer.echo(
            f"- {row['year']}: compact_core "
            f"{row['compact_core_companies']}/{row['financial_eligible_companies']} "
            f"({row['compact_core_coverage_pct']}%), "
            f"disclosure_list "
            f"{row['disclosure_list_companies']}/{row['disclosure_eligible_companies']} "
            f"({row['disclosure_list_coverage_pct']}%), "
            f"event_index rows {row['disclosure_event_rows']}"
        )
    typer.echo(f"required_gaps: {', '.join(snapshot['required_gaps']) or '-'}")
    typer.echo(f"recommended_gaps: {', '.join(snapshot['recommended_gaps']) or '-'}")


@app.command("dataset-completeness")
def dataset_completeness_cmd(
    year: int = typer.Option(2025, "--year", help="기준 사업연도"),
    years_back: int = typer.Option(5, "--years-back", help="직전 N개년 완전성 기준"),
    sample_size: int = typer.Option(100, "--sample-size", help="표본 완전성 점검 회사 수"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """MCP 배포용 strict 데이터셋 완전성 게이트."""
    from kreports.analysis.readiness import dataset_completeness_snapshot, pct

    snapshot = dataset_completeness_snapshot(
        year=year,
        years_back=years_back,
        sample_size=sample_size,
    )
    if json_output:
        _json_print(snapshot)
        return

    listed = int(snapshot["listed_companies"] or 0)
    typer.echo(f"Dataset completeness: {snapshot['verdict']}")
    typer.echo(f"required_years: {', '.join(str(y) for y in snapshot['required_years'])}")
    typer.echo(f"threshold: {snapshot['threshold_pct']}%")
    typer.echo(f"listed_companies: {listed:,}")

    counts = snapshot["counts"]
    for key in (
        "financial_5y",
        "audit_fee_5y",
        "audit_fee_value_5y",
        "audit_hours_5y",
        "auditor_5y",
        "policy_current",
        "core_without_policy",
        "complete_company",
    ):
        typer.echo(f"- {key}: {counts[key]:,}/{listed:,} ({pct(counts[key], listed)}%)")

    typer.echo(
        "sample_complete: "
        f"{snapshot['sample_complete']}/{snapshot['sample_size']} "
        f"({snapshot['sample_complete_rate']}%)"
    )
    kam = snapshot["kam_body_topics"]
    typer.echo(f"kam_body_topics: {kam['status']} (table_present={kam['table_present']}, rows={kam['rows']})")
    typer.echo(f"required_gaps: {', '.join(snapshot['required_gaps']) or '-'}")

    if snapshot["incomplete_examples"]:
        typer.echo("incomplete_examples:")
        for row in snapshot["incomplete_examples"][:10]:
            typer.echo(
                f"  {row['stock_code']} {row['corp_name']} "
                f"fin={row['financial_years']} fee={row['audit_fee_years']} "
                f"fee_value={row['audit_fee_value_years']} hours={row['audit_hour_value_years']} "
                f"auditor={row['auditor_years']} policy={row['policy_items']} "
                f"missing={','.join(row['missing'])}"
            )


@app.command("auditor-feature-readiness")
def auditor_feature_readiness_cmd(
    year: int = typer.Option(2025, "--year", help="기준 사업연도"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """현재 DB가 감사인 관점 MCP 기능을 실제로 뒷받침하는지 점검한다."""
    from kreports.analysis.readiness import auditor_feature_readiness_snapshot

    snapshot = auditor_feature_readiness_snapshot(year=year, market=market)
    if json_output:
        _json_print(snapshot)
        return

    typer.echo(f"Auditor feature readiness: {snapshot['verdict']}")
    typer.echo(f"year: {snapshot['year']} | market: {snapshot['market'] or '-'}")
    typer.echo(f"listed_companies: {snapshot['listed_companies']:,}")
    typer.echo("counts:")
    for key, value in snapshot["counts"].items():
        typer.echo(f"- {key}: {value:,}")
    typer.echo("rates:")
    for key, value in snapshot["rates"].items():
        typer.echo(f"- {key}: {value}%")
    typer.echo("feature_status:")
    for key, value in snapshot["feature_status"].items():
        typer.echo(f"- {key}: {value}")
    typer.echo(f"missing_features: {', '.join(snapshot['missing_features']) or '-'}")
    typer.echo("recommended_next:")
    for item in snapshot["recommended_next"]:
        typer.echo(f"- {item}")


@app.command("audit-kam-quality")
def audit_kam_quality_cmd(
    year: int = typer.Option(2025, "--year", help="기준 사업연도"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ"),
    min_body_length: int = typer.Option(300, "--min-body-length", help="짧은 KAM 본문 판정 기준"),
    limit: int = typer.Option(50, "--limit", help="repair candidate 출력 수"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """KAM 본문 품질과 재파싱 후보를 점검한다."""
    from kreports.analysis.readiness import audit_kam_quality_snapshot

    snapshot = audit_kam_quality_snapshot(
        year=year,
        market=market,
        min_body_length=min_body_length,
        limit=limit,
    )
    if json_output:
        _json_print(snapshot)
        return

    typer.echo(f"Audit KAM quality: {snapshot['verdict']}")
    typer.echo(
        f"year: {snapshot['year']} | market: {snapshot['market'] or '-'} "
        f"| min_body_length: {snapshot['min_body_length']}"
    )
    typer.echo("counts:")
    for key, value in snapshot["counts"].items():
        typer.echo(f"- {key}: {value:,}")
    typer.echo("rates:")
    for key, value in snapshot["rates"].items():
        typer.echo(f"- {key}: {value}%")
    typer.echo(f"required_gaps: {', '.join(snapshot['required_gaps']) or '-'}")
    if snapshot["repair_candidates"]:
        typer.echo("repair_candidates:")
        for row in snapshot["repair_candidates"]:
            typer.echo(
                f"  {row['stock_code'] or row['corp_code']} {row['corp_name']} "
                f"rcept_no={row['rcept_no']} dcm_no={row.get('dcm_no') or '-'} "
                f"len={row['body_length']} gaps={','.join(row['gap_reasons'])}"
            )
    typer.echo("recommended_next:")
    for item in snapshot["recommended_next"]:
        typer.echo(f"- {item}")


@app.command("audit-procedure-evidence-map")
def audit_procedure_evidence_map_cmd(
    year: int = typer.Option(2025, "--year", help="기준 사업연도"),
    company: Optional[str] = typer.Option(None, "--company", help="corp_code, stock_code, or 회사명"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ"),
    limit: int = typer.Option(100, "--limit", help="샘플 최대 행 수"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """감사절차가 어떤 공시/주석/계정 근거와 연결되는지 진단한다."""
    from kreports.analysis.audit_procedure_evidence import build_audit_procedure_evidence_map

    snapshot = build_audit_procedure_evidence_map(
        year=year,
        company=company,
        market=market,
        limit=limit,
    )
    if json_output:
        _json_print(snapshot)
        return

    typer.echo(f"Audit procedure evidence map: {snapshot['verdict']}")
    typer.echo(
        f"year: {snapshot['year']} | company: {snapshot['company'] or '-'} | "
        f"market: {snapshot['market'] or '-'}"
    )
    typer.echo("counts:")
    for key, value in snapshot["counts"].items():
        typer.echo(f"- {key}: {value}")
    typer.echo("rates:")
    for key, value in snapshot["rates"].items():
        typer.echo(f"- {key}: {value}%")
    typer.echo(f"required_gaps: {', '.join(snapshot['required_gaps']) or '-'}")
    for row in snapshot["samples"][:10]:
        linkage_labels = ", ".join(link["label"] for link in row["linkages"][:4]) or "-"
        typer.echo(
            f"  {row['stock_code'] or row['corp_code']} {row['corp_name']} "
            f"len={row['body_length']} procedures={row['procedure_count']} links={linkage_labels}"
        )


@app.command("repair-kam-sections")
def repair_kam_sections_cmd(
    year: int = typer.Option(2025, "--year", help="기준 사업연도"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ/KONEX"),
    min_body_length: int = typer.Option(300, "--min-body-length", help="짧은 KAM 본문 판정 기준"),
    limit: int = typer.Option(50, "--limit", help="최대 repair 대상 수"),
    include_index_only: bool = typer.Option(False, "--include-index-only", help="로컬 색인만 필요한 후보도 DART 재수집 대상에 포함"),
    execute: bool = typer.Option(False, "--execute", help="dry-run이 아니라 실제 DART 재수집 실행"),
    force: bool = typer.Option(False, "--force", help="동일 백필 running 기록이 있어도 강제 실행"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """KAM 품질 진단 후보 중 원문 재파싱이 필요한 공시만 다시 수집한다."""
    from kreports.collector.report_document_collector import repair_kam_sections
    from kreports.runtime import require_collector_mode

    if execute:
        require_collector_mode("repair-kam-sections")
        if not settings.dart_api_key:
            typer.echo("오류: DART_API_KEY 미설정", err=True)
            raise typer.Exit(1)

    init_db()

    def _progress(idx, total, corp_name, rcept_no):
        if idx == 1 or idx % 10 == 0 or idx == total:
            typer.echo(f"  [{idx}/{total}] {corp_name} {rcept_no}")

    params = {
        "year": year,
        "market": market,
        "min_body_length": min_body_length,
        "limit": limit,
        "include_index_only": include_index_only,
        "execute": execute,
    }
    if execute:
        with _backfill_run_guard(
            task_type="kam_sections_repair",
            year=year,
            market=market,
            params=params,
            force=force,
        ) as run_id:
            result = repair_kam_sections(
                year=year,
                market=market,
                min_body_length=min_body_length,
                limit=limit,
                include_index_only=include_index_only,
                dry_run=False,
                progress_callback=_progress,
            )
            _finish_backfill_run(run_id, result)
    else:
        result = repair_kam_sections(
            year=year,
            market=market,
            min_body_length=min_body_length,
            limit=limit,
            include_index_only=include_index_only,
            dry_run=True,
            progress_callback=None,
        )

    if json_output:
        _json_print(result)
        return

    mode = "EXECUTE" if execute else "DRY-RUN"
    typer.echo(f"KAM repair {mode}: total {result['total']:,}")
    typer.echo(
        f"quality_rates: reason={result['quality_rates']['reason_hint_coverage']}% "
        f"procedure={result['quality_rates']['procedure_hint_coverage']}% "
        f"indexed={result['quality_rates']['indexed_procedure_coverage']}%"
    )
    if execute:
        typer.echo(
            f"완료 - 성공 {result['ok']:,} | 실패 {result['failed']:,} | "
            f"sections {result['sections']:,}"
        )
    if result["excluded_gap_reasons"]:
        typer.echo(f"excluded_gap_reasons: {', '.join(result['excluded_gap_reasons'])}")
    if result["targets"]:
        typer.echo("targets:")
        for row in result["targets"][:20]:
            typer.echo(
                f"  {row['stock_code'] or row['corp_code']} {row['corp_name']} "
                f"source_rcept_no={row['source_rcept_no']} "
                f"gaps={','.join(row['gap_reasons'])}"
            )
    if result["errors"]:
        typer.echo("실패 샘플:")
        for row in result["errors"][:10]:
            typer.echo(f"  {row['rcept_no']} {row['corp_name']}: {row['error']}")


# ---------------------------------------------------------------------------
# collect-golden
# ---------------------------------------------------------------------------

@app.command("collect-golden")
def collect_golden_cmd(
    year_from: Optional[int] = typer.Option(
        None, "--year-from", help="수집 시작 연도. 기본은 직전 2개 연도."
    ),
    year_to: Optional[int] = typer.Option(
        None, "--year-to", help="수집 종료 연도. 기본은 직전 연도."
    ),
    stocks: Optional[str] = typer.Option(
        None, "--stocks", help="쉼표 구분 종목코드. 기본은 내장 골든셋."
    ),
    enrich_master: bool = typer.Option(
        True, "--enrich-master/--skip-enrich-master", help="수집 전 market/induty_code 보완"
    ),
    include_disclosures: bool = typer.Option(
        True, "--disclosures/--no-disclosures", help="공시 목록 수집"
    ),
    include_auditors: bool = typer.Option(
        True, "--auditors/--no-auditors", help="감사인 이력 수집"
    ),
    include_audit_fees: bool = typer.Option(
        True, "--audit-fees/--no-audit-fees", help="감사보수 수집"
    ),
    include_policies: bool = typer.Option(
        True, "--policies/--no-policies", help="회계정책 item 수집"
    ),
):
    """MCP smoke test와 데모에 필요한 골든셋 기업 데이터를 우선 적재한다."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    current_year = date.today().year
    y_to = year_to or (current_year - 1)
    y_from = year_from or (y_to - 1)
    selected_stocks = _parse_stock_codes(stocks) or GOLDEN_STOCK_CODES
    companies = _resolve_companies_by_stock(selected_stocks)
    corp_codes = [c["corp_code"] for c in companies]
    start_date = f"{y_from}0101"
    end_date = date.today().strftime("%Y%m%d")

    typer.echo(
        f"골든셋 수집 시작: {len(companies)}개사 · "
        f"{y_from}~{y_to}년 · "
        f"{', '.join(c['stock_code'] for c in companies)}"
    )

    if enrich_master:
        from kreports.collector.corp_sync import enrich_market as _enrich

        typer.echo("  master 보완 중...")
        _enrich(corp_codes=corp_codes)

    from kreports.collector.fin_collector import collect_financial_range
    from kreports.collector.disc_collector import collect_disclosures
    from kreports.collector.audit_collector import collect_auditors
    from kreports.collector.audit_fee_collector import collect_audit_fees
    from kreports.collector.policy_collector import collect_policies_batch

    totals = {
        "financial_success": 0,
        "financial_no_data": 0,
        "financial_error": 0,
        "disclosures_saved": 0,
        "disclosures_error": 0,
        "auditors_saved": 0,
        "audit_fees_saved": 0,
        "audit_fees_error": 0,
        "policy_ok": 0,
        "policy_failed": 0,
        "policy_items_total": 0,
    }

    for idx, company in enumerate(companies, 1):
        stock_code = company["stock_code"]
        corp_code = company["corp_code"]
        corp_name = company["corp_name"]
        typer.echo(f"[{idx}/{len(companies)}] {corp_name} ({stock_code})")

        financial_result = collect_financial_range(stock_code, year_from=y_from, year_to=y_to)
        totals["financial_success"] += financial_result["success"]
        totals["financial_no_data"] += financial_result["no_data"]
        totals["financial_error"] += financial_result["error"]

        if include_disclosures:
            disclosure_result = collect_disclosures(
                corp_code, start_date=start_date, end_date=end_date
            )
            totals["disclosures_saved"] += disclosure_result["saved"]
            totals["disclosures_error"] += disclosure_result["error"]

        if include_auditors:
            auditor_result = collect_auditors(
                corp_code, start_date=start_date, end_date=end_date
            )
            totals["auditors_saved"] += auditor_result["saved"]

        if include_audit_fees:
            fee_result = collect_audit_fees(corp_code, year_from=y_from, year_to=y_to)
            totals["audit_fees_saved"] += fee_result["saved"]
            totals["audit_fees_error"] += fee_result["error"]

        if include_policies:
            policy_targets = [(corp_code, year, "CFS") for year in range(y_from, y_to + 1)]
            policy_result = collect_policies_batch(policy_targets)
            totals["policy_ok"] += policy_result["ok"]
            totals["policy_failed"] += policy_result["failed"]
            totals["policy_items_total"] += policy_result["items_total"]

    typer.echo(
        "\n완료 - "
        f"financial success {totals['financial_success']}, "
        f"no_data {totals['financial_no_data']}, "
        f"error {totals['financial_error']} | "
        f"disclosures {totals['disclosures_saved']} (error {totals['disclosures_error']}) | "
        f"auditors {totals['auditors_saved']} | "
        f"audit_fees {totals['audit_fees_saved']} (error {totals['audit_fees_error']}) | "
        f"policies ok {totals['policy_ok']} failed {totals['policy_failed']} "
        f"items {totals['policy_items_total']}"
    )


# ---------------------------------------------------------------------------
# collect-policies — 사업보고서 주석 회계정책 영속화
# ---------------------------------------------------------------------------

def _select_policy_targets(
    *,
    year: int,
    fs_div: str,
    market: str | None,
    limit: int | None,
    missing_only: bool,
) -> list[tuple[str, int, str]]:
    from sqlalchemy import text

    stmt = (
        "SELECT c.corp_code FROM companies c "
        "WHERE c.stock_code IS NOT NULL "
    )
    params: dict[str, object] = {}
    if market:
        stmt += "AND c.market = :market "
        params["market"] = market
    if missing_only:
        stmt += (
            "AND NOT EXISTS ("
            "SELECT 1 FROM accounting_policy_items p "
            "WHERE p.corp_code=c.corp_code AND p.bsns_year=:year AND p.fs_div=:fs_div"
            ") "
        )
        params["year"] = year
        params["fs_div"] = fs_div
    stmt += "ORDER BY c.market, c.corp_code "
    if limit:
        stmt += "LIMIT :limit"
        params["limit"] = limit
    with get_session() as session:
        rows = session.execute(text(stmt), params).all()
    return [(row[0], year, fs_div) for row in rows]


@app.command("collect-policies")
def collect_policies_cmd(
    stock: Optional[str] = typer.Argument(
        None, help="종목코드 (예: 005930). 생략 시 --all 필요."
    ),
    year: Optional[int] = typer.Option(
        None, "--year", help="사업연도. 미지정 시 해당 기업 사업보고서가 있는 전체 연도."
    ),
    fs_div: str = typer.Option("CFS", "--fs-div", help="CFS/OFS"),
    all_corps: bool = typer.Option(
        False, "--all", help="AccountingPolicyItem에 이미 있는 기업 전체 재수집."
    ),
    market: Optional[str] = typer.Option(None, "--market", help="KOSPI/KOSDAQ/KONEX 대상 일괄 수집"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 회사 수"),
    missing_only: bool = typer.Option(True, "--missing-only/--include-existing", help="이미 캐시된 정책 제외"),
    force: bool = typer.Option(False, "--force", help="동일 백필 running 기록이 있어도 강제 실행"),
):
    """
    사업보고서 주석에서 회계정책 item들을 파싱하여 DB에 영속화한다.

    예시:
      dart collect-policies 005930              # 삼성 전체 연도
      dart collect-policies 005930 --year 2024  # 삼성 2024 사업연도만
      dart collect-policies 005930 --fs-div OFS # 별도
    """
    from kreports.runtime import require_collector_mode

    require_collector_mode("collect-policies")
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    if not stock and not all_corps and not market:
        typer.echo("종목코드 또는 --all/--market 플래그 필요", err=True)
        raise typer.Exit(1)

    from kreports.collector.policy_collector import (
        collect_policies_batch,
    )

    # 대상 수집: (corp_code, bsns_year, fs_div) 튜플 리스트
    targets: list[tuple[str, int, str]] = []

    if stock:
        with get_session() as session:
            row = session.query(Company).filter_by(stock_code=stock).first()
            if row is None:
                typer.echo(f"오류: 종목코드 '{stock}' DB 미등록", err=True)
                raise typer.Exit(1)
            corp_code = row.corp_code
            corp_name = row.corp_name

            # 사업보고서 연도 목록 조회 (Disclosure에서)
            if year is not None:
                years_to_process = [year]
            else:
                report_years = (
                    session.query(Disclosure.disc_date)
                    .filter_by(corp_code=corp_code)
                    .filter(Disclosure.report_nm.like("%사업보고서%"))
                    .order_by(Disclosure.disc_date.desc())
                    .all()
                )
                years_to_process = sorted({r[0].year - 1 for r in report_years})

        for y in years_to_process:
            targets.append((corp_code, y, fs_div))
        typer.echo(
            f"수집 시작: {corp_name} ({corp_code}) · "
            f"{len(targets)}개 (사업연도={','.join(str(t[1]) for t in targets)}) · fs_div={fs_div}"
        )

    if market:
        if year is None:
            typer.echo("--market 사용 시 --year 필요", err=True)
            raise typer.Exit(1)
        targets.extend(
            _select_policy_targets(
                year=year,
                fs_div=fs_div,
                market=market,
                limit=limit,
                missing_only=missing_only,
            )
        )
        typer.echo(f"정책 수집 대상: market={market} year={year} fs_div={fs_div} targets={len(targets)}")

    if not targets:
        typer.echo("대상 없음. 종료.")
        raise typer.Exit(0)

    def _progress(idx, total, cc, yy, fd):
        if idx == 1 or idx % 5 == 0 or idx == total:
            typer.echo(f"  [{idx}/{total}] {cc} {yy} {fd}")

    guard_market = market if market else ("SINGLE" if stock else "ALL")
    guard_year = year if year is not None else min(t[1] for t in targets)
    with _backfill_run_guard(
        task_type="policy_items",
        year=guard_year,
        market=guard_market,
        params={
            "stock": stock,
            "year": year,
            "fs_div": fs_div,
            "all_corps": all_corps,
            "market": market,
            "limit": limit,
            "missing_only": missing_only,
        },
        force=force,
    ) as run_id:
        agg = collect_policies_batch(targets, progress_callback=_progress)
        _finish_backfill_run(run_id, agg)

    typer.echo(
        f"\n완료 - 처리 {agg['total']} | 성공 {agg['ok']} | 실패 {agg['failed']} | "
        f"items {agg['items_total']} (신규 {agg['items_new']} · 변경 {agg['items_changed']})"
    )

    if agg["errors"]:
        typer.echo("\n실패 목록:")
        for e in agg["errors"][:10]:
            typer.echo(f"  {e['corp_code']} {e['bsns_year']} {e['fs_div']}: {e['error']}")
        if len(agg["errors"]) > 10:
            typer.echo(f"  ... 외 {len(agg['errors']) - 10}건")


# ---------------------------------------------------------------------------
# collect (단일 종목)
# ---------------------------------------------------------------------------

@app.command("collect")
def collect(
    stock: str = typer.Argument(..., help="종목코드 (예: 005930)"),
    year_from: Optional[int] = typer.Option(None, help="수집 시작 연도"),
    year_to: Optional[int] = typer.Option(None, help="수집 종료 연도"),
    force: bool = typer.Option(False, "--force", help="이미 수집된 분기도 재수집"),
):
    """단일 종목 재무데이터를 수집한다."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    current_year = date.today().year
    y_to = year_to or current_year
    y_from = year_from or (y_to - settings.collect_years + 1)

    typer.echo(f"수집 시작: {stock} ({y_from}~{y_to}년)")
    from kreports.collector.fin_collector import collect_financial_range
    result = collect_financial_range(stock, year_from=y_from, year_to=y_to, force=force)
    typer.echo(
        "완료 - "
        f"성공: {result['success']}, "
        f"데이터없음: {result['no_data']}, "
        f"건너뜀: {result.get('skipped', 0)}, "
        f"오류: {result['error']}"
    )


# ---------------------------------------------------------------------------
# collect-all (전체 배치)
# ---------------------------------------------------------------------------

@app.command("collect-all")
def collect_all(
    year_from: Optional[int] = typer.Option(None, help="수집 시작 연도"),
    year_to: Optional[int] = typer.Option(None, help="수집 종료 연도"),
    market: Optional[str] = typer.Option(None, help="시장 필터: KOSPI/KOSDAQ/KONEX. 생략 시 상장 시장 전체."),
    force: bool = typer.Option(False, "--force", help="동일 백필 running 기록을 무시하고 이미 수집된 분기도 재수집"),
):
    """전체 상장사 재무데이터를 배치 수집한다."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    from kreports.collector.scheduler import run_resumable_financial_backfill

    def _progress(done, total, corp_name):
        typer.echo(f"\r[{done}/{total}] {corp_name}", nl=False)

    typer.echo("전체 재무 배치 수집 시작...")
    with _backfill_run_guard(
        task_type="financials",
        year=year_from,
        market=(market or "LISTED").upper(),
        params={"year_from": year_from, "year_to": year_to, "market": market},
        force=force,
    ) as run_id:
        progress = run_resumable_financial_backfill(
            _ACTIVE_BACKFILL_LEASES[run_id],
            year_from=year_from,
            year_to=year_to,
            market=market,
            progress_callback=_progress,
            force=force,
        )
        result = {
            "success": progress["saved"],
            "no_data": progress["no_data"],
            "error": progress["errors"],
            "skipped": progress["skipped"],
            "attempted": progress["attempted"],
        }
        _finish_backfill_run(run_id, result)
    typer.echo(
        "\n완료 - "
        f"성공: {result['success']:,}, "
        f"데이터없음: {result['no_data']:,}, "
        f"건너뜀: {result.get('skipped', 0):,}, "
        f"오류: {result['error']:,}"
    )


# ---------------------------------------------------------------------------
# collect-disclosures (공시 수집)
# ---------------------------------------------------------------------------

@app.command("collect-disclosures")
def collect_disclosures_cmd(
    stock: Optional[str] = typer.Option(None, help="단일 종목코드. 생략 시 전체 수집."),
    start_date: Optional[str] = typer.Option(None, help="수집 시작일 YYYYMMDD"),
    end_date: Optional[str] = typer.Option(None, help="수집 종료일 YYYYMMDD"),
    market: Optional[str] = typer.Option(None, help="전체 수집 시 시장 필터: KOSPI/KOSDAQ/KONEX"),
    force: bool = typer.Option(False, "--force", help="동일 백필 running 기록이 있어도 강제 실행"),
):
    """공시 목록을 수집한다."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    if stock:
        from kreports.collector.corp_sync import get_corp_code
        from kreports.collector.disc_collector import collect_disclosures
        corp_code = get_corp_code(stock)
        if not corp_code:
            typer.echo(f"오류: {stock} 종목을 찾을 수 없습니다.", err=True)
            raise typer.Exit(1)
        with _backfill_run_guard(
            task_type="disclosures",
            year=None,
            market="SINGLE",
            params={"stock": stock, "start_date": start_date, "end_date": end_date},
            force=force,
        ) as run_id:
            result = collect_disclosures(corp_code, start_date=start_date, end_date=end_date)
            _finish_backfill_run(run_id, result)
        typer.echo(f"완료 - 저장: {result['saved']}, 스킵: {result['skipped']}")
    else:
        from kreports.collector.disc_collector import collect_all_disclosures

        def _progress(done, total, corp_name):
            typer.echo(f"\r[{done}/{total}] {corp_name}", nl=False)

        typer.echo("전체 공시 배치 수집 시작...")
        with _backfill_run_guard(
            task_type="disclosures",
            year=None,
            market=market,
            params={"start_date": start_date, "end_date": end_date, "market": market},
            force=force,
        ) as run_id:
            result = collect_all_disclosures(
                start_date=start_date,
                end_date=end_date,
                market=market,
                progress_callback=_progress,
            )
            _finish_backfill_run(run_id, result)
        typer.echo(f"\n완료 - 저장: {result['saved']:,}, 스킵: {result['skipped']:,}")


@app.command("audit-disclosure-window")
def audit_disclosure_window_cmd(
    start_date: str = typer.Option(..., "--start-date", help="감사 시작일 YYYYMMDD"),
    end_date: str = typer.Option(..., "--end-date", help="감사 종료일 YYYYMMDD"),
    disc_type: str = typer.Option("", "--disc-type", help="DART pblntf_ty 필터. 예: A=정기공시, F=외부감사관련"),
    report_keyword: Optional[str] = typer.Option(None, "--report-keyword", help="report_nm 포함 키워드"),
    exclude_keyword: list[str] = typer.Option([], "--exclude-keyword", help="제외할 report_nm 키워드. 반복 가능"),
    chunk_days: int = typer.Option(31, "--chunk-days", help="DART 조회 구간 일수"),
    persist_missing: bool = typer.Option(False, "--persist-missing", help="DART에는 있으나 로컬에 없는 공시목록 row를 저장"),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """DART list.json 원장 기준으로 로컬 disclosures 누락을 대조한다."""
    from kreports.runtime import require_collector_mode
    from kreports.collector.disc_collector import audit_disclosure_window

    require_collector_mode("audit-disclosure-window")
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    init_db()

    def _progress(idx, total, start, end):
        typer.echo(f"  [{idx}/{total}] {start}-{end}")

    result = audit_disclosure_window(
        start_date=start_date,
        end_date=end_date,
        disc_type=disc_type,
        report_keyword=report_keyword,
        exclude_keywords=list(exclude_keyword),
        chunk_days=chunk_days,
        persist_missing=persist_missing,
        progress_callback=None if json_output else _progress,
    )
    if json_output:
        _json_print(result)
        return

    typer.echo(f"Disclosure window audit: {result['verdict']}")
    typer.echo(f"range: {start_date}-{end_date} | chunks: {result['chunks']}")
    typer.echo(f"disc_type: {disc_type or '-'} | report_keyword: {report_keyword or '-'}")
    typer.echo(
        f"DART target rows: {result['target_rows']:,} | "
        f"local rows: {result['local_rows']:,} | "
        f"missing: {result['missing_rows']:,} | "
        f"coverage: {result['coverage_pct']}%"
    )
    if persist_missing:
        typer.echo(f"saved_missing: {result['saved_missing']:,}")
    if result["errors"]:
        typer.echo("errors:")
        for row in result["errors"][:10]:
            typer.echo(f"  {row['start_date']}-{row['end_date']}: {row['error']}")
    if result["missing_samples"]:
        typer.echo("missing_samples:")
        for row in result["missing_samples"][:20]:
            typer.echo(
                f"  {row['disc_date']} {row['rcept_no']} "
                f"{row['corp_name']} {row['report_nm']}"
            )


@app.command("raw-annual-report-coverage")
def raw_annual_report_coverage_cmd(
    start_filing_year: int = typer.Option(2022, "--start-filing-year"),
    end_filing_year: int = typer.Option(2026, "--end-filing-year"),
    market: list[str] = typer.Option(["KOSPI", "KOSDAQ"], "--market"),
):
    """5개년 최신 사업보고서 원문 GCS 적재율을 요약한다."""
    from kreports.analysis.raw_coverage import raw_annual_report_coverage

    _json_print(raw_annual_report_coverage(
        start_filing_year=start_filing_year,
        end_filing_year=end_filing_year,
        markets=list(market),
    ))


# ---------------------------------------------------------------------------
# collect-auditors (감사인 이력 수집)
# ---------------------------------------------------------------------------

@app.command("collect-auditors")
def collect_auditors_cmd(
    stock: Optional[str] = typer.Option(None, help="단일 종목코드. 생략 시 전체 수집."),
    force: bool = typer.Option(False, "--force", help="동일 백필 running 기록이 있어도 강제 실행"),
):
    """감사인 이력을 수집하고 플래그를 계산한다."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    if stock:
        from kreports.collector.corp_sync import get_corp_code
        from kreports.collector.audit_collector import collect_auditors
        from kreports.judge.auditor_flags import compute_auditor_flags
        corp_code = get_corp_code(stock)
        if not corp_code:
            typer.echo(f"오류: {stock} 종목을 찾을 수 없습니다.", err=True)
            raise typer.Exit(1)
        with _backfill_run_guard(
            task_type="auditors",
            year=None,
            market="SINGLE",
            params={"stock": stock},
            force=force,
        ) as run_id:
            result = collect_auditors(corp_code)
            compute_auditor_flags(corp_code)
            _finish_backfill_run(run_id, result)
        typer.echo(f"완료 - 저장: {result['saved']}, 스킵: {result['skipped']}")
    else:
        from kreports.collector.audit_collector import collect_all_auditors
        from kreports.judge.auditor_flags import compute_all_auditor_flags

        def _progress(done, total, corp_name):
            typer.echo(f"\r[{done}/{total}] {corp_name}", nl=False)

        typer.echo("전체 감사인 배치 수집 시작...")
        with _backfill_run_guard(
            task_type="auditors",
            year=None,
            market="ALL",
            params={"stock": stock},
            force=force,
        ) as run_id:
            result = collect_all_auditors(progress_callback=_progress)
            typer.echo("\n플래그 계산 중...")
            compute_all_auditor_flags()
            _finish_backfill_run(run_id, result)
        typer.echo(f"완료 - 저장: {result['saved']:,}, 스킵: {result['skipped']:,}")


# ---------------------------------------------------------------------------
# collect-audit-report-sections (감사보고서 본문 섹션 수집)
# ---------------------------------------------------------------------------

@app.command("collect-audit-report-sections")
def collect_audit_report_sections_cmd(
    year: int = typer.Option(2025, "--year", help="감사대상 사업연도"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ/KONEX"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 공시 수"),
    missing_only: bool = typer.Option(True, "--missing-only/--include-existing", help="이미 섹션 저장된 공시 제외"),
    force: bool = typer.Option(False, "--force", help="동일 백필 running 기록이 있어도 강제 실행"),
):
    """감사보고서 document.xml 본문에서 KAM/의견/강조사항 등 섹션을 저장한다."""
    from kreports.runtime import require_collector_mode, require_raw_backfill_mode
    from kreports.collector.report_document_collector import collect_audit_report_sections

    try:
        require_collector_mode("collect-audit-report-sections")
        require_raw_backfill_mode(
            "collect-audit-report-sections",
            raw_storage_backend=settings.raw_storage_backend,
            raw_storage_keep_inline=settings.raw_storage_keep_inline,
        )
    except RuntimeError as exc:
        typer.echo(f"오류: {exc}", err=True)
        raise typer.Exit(2)
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    init_db()

    def _progress(idx, total, corp_name, rcept_no):
        if idx == 1 or idx % 10 == 0 or idx == total:
            typer.echo(f"  [{idx}/{total}] {corp_name} {rcept_no}")

    with _backfill_run_guard(
        task_type="audit_report_sections",
        year=year,
        market=market,
        params={"year": year, "market": market, "limit": limit, "missing_only": missing_only},
        force=force,
    ) as run_id:
        result = collect_audit_report_sections(
            year=year,
            market=market,
            limit=limit,
            missing_only=missing_only,
            progress_callback=_progress,
        )
        _finish_backfill_run(run_id, result)
    typer.echo(
        f"완료 - 처리 {result['total']:,} | 성공 {result['ok']:,} | "
        f"실패 {result['failed']:,} | sections {result['sections']:,}"
    )
    if result["errors"]:
        typer.echo("실패 샘플:")
        for row in result["errors"][:10]:
            typer.echo(f"  {row['rcept_no']} {row['corp_name']}: {row['error']}")


@app.command("collect-business-report-sections")
def collect_business_report_sections_cmd(
    year: int = typer.Option(2025, "--year", help="사업연도"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ/KONEX"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 공시 수"),
    missing_only: bool = typer.Option(True, "--missing-only/--include-existing", help="이미 섹션 저장된 공시 제외"),
    force: bool = typer.Option(False, "--force", help="동일 백필 running 기록이 있어도 강제 실행"),
):
    """사업보고서 document.xml 안의 감사보고서/KAM 관련 섹션을 저장한다."""
    from kreports.runtime import require_collector_mode, require_raw_backfill_mode
    from kreports.collector.report_document_collector import collect_business_report_sections

    try:
        require_collector_mode("collect-business-report-sections")
        require_raw_backfill_mode(
            "collect-business-report-sections",
            raw_storage_backend=settings.raw_storage_backend,
            raw_storage_keep_inline=settings.raw_storage_keep_inline,
        )
    except RuntimeError as exc:
        typer.echo(f"오류: {exc}", err=True)
        raise typer.Exit(2)
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    init_db()

    def _progress(idx, total, corp_name, rcept_no):
        if idx == 1 or idx % 10 == 0 or idx == total:
            typer.echo(f"  [{idx}/{total}] {corp_name} {rcept_no}")

    with _backfill_run_guard(
        task_type="business_report_sections",
        year=year,
        market=market,
        params={"year": year, "market": market, "limit": limit, "missing_only": missing_only},
        force=force,
    ) as run_id:
        result = collect_business_report_sections(
            year=year,
            market=market,
            limit=limit,
            missing_only=missing_only,
            progress_callback=_progress,
        )
        _finish_backfill_run(run_id, result)
    typer.echo(
        f"완료 - 처리 {result['total']:,} | 성공 {result['ok']:,} | "
        f"실패 {result['failed']:,} | sections {result['sections']:,}"
    )
    if result["errors"]:
        typer.echo("실패 샘플:")
        for row in result["errors"][:10]:
            typer.echo(f"  {row['rcept_no']} {row['corp_name']}: {row['error']}")


@app.command("run-document-extractors")
def run_document_extractors_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    source_type: Optional[str] = typer.Option(None, "--source-type", help="business_report/audit_report"),
    extractor: str = typer.Option("all", "--extractor", help="all/sections/auditors/subsidiaries/note_chapters"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 문서 수"),
):
    """source_documents 원문 캐시에서 extractor를 재실행한다. DART API를 호출하지 않는다."""
    from kreports.collector.report_document_collector import run_document_extractors

    init_db()

    def _progress(idx, total, corp_code, yy, src_type, rcept_no):
        if idx == 1 or idx % 25 == 0 or idx == total:
            typer.echo(f"  [{idx}/{total}] {corp_code} {yy} {src_type} {rcept_no}")

    result = run_document_extractors(
        year=year,
        source_type=source_type,
        extractor=extractor,
        limit=limit,
        progress_callback=_progress,
    )
    typer.echo(
        f"완료 - 처리 {result['total']:,} | 성공 {result['ok']:,} | "
        f"skip {result.get('skipped', 0):,} | 실패 {result['failed']:,} | "
        f"rows_written {result['rows_written']:,}"
    )
    if result["errors"]:
        typer.echo("실패 샘플:")
        for row in result["errors"][:10]:
            typer.echo(f"  {row.get('rcept_no')}: {row.get('error')}")


@app.command("index-note-sources")
def index_note_sources_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    source_type: Optional[str] = typer.Option(None, "--source-type", help="business_report/audit_report"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 검사 문서 수"),
):
    """기존 원문 캐시를 읽기 전용으로 주석 후보/커버리지만 점검한다."""
    from kreports.processor.note_source_index import build_note_source_index

    _json_print(build_note_source_index(
        year=year,
        source_type=source_type,
        limit=limit,
        include_chapters=False,
    ))


@app.command("inventory-note-sources")
def inventory_note_sources_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    source_type: Optional[str] = typer.Option(None, "--source-type", help="business_report/audit_report"),
    company_offset: int = typer.Option(0, "--company-offset", min=0, help="회사-연도-출처 그룹 offset"),
    company_limit: int = typer.Option(500, "--company-limit", min=1, max=1000, help="반환할 회사-연도-출처 그룹 수"),
):
    """모든 회사 원문 메타데이터의 read-only 주석 backfill 계획을 생성한다."""
    from kreports.processor.note_source_index import build_note_source_inventory

    _json_print(build_note_source_inventory(
        year=year,
        source_type=source_type,
        company_offset=company_offset,
        company_limit=company_limit,
    ))


@app.command("audit-extraction-gaps")
def audit_extraction_gaps_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    source_type: Optional[str] = typer.Option(None, "--source-type", help="business_report/audit_report"),
    company_offset: int = typer.Option(0, "--company-offset", min=0, help="영수증 단위 표본 offset"),
    company_limit: int = typer.Option(200, "--company-limit", min=1, max=1000, help="반환할 영수증 단위 표본 수"),
):
    """원문/파생 테이블 간 추출 공백을 읽기 전용으로 감사한다."""
    from kreports.analysis.extraction_gap_audit import build_extraction_gap_audit

    _json_print(build_extraction_gap_audit(
        year=year,
        source_type=source_type,
        company_offset=company_offset,
        company_limit=company_limit,
    ))


@app.command("index-audit-procedures")
def index_audit_procedures_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 KAM 섹션 수"),
):
    """이미 저장된 KAM 본문에서 감사절차 인덱스를 생성한다."""
    from kreports.collector.report_document_collector import index_audit_procedures_from_sections

    init_db()

    def _progress(idx, total, corp_code, yy, rcept_no):
        if idx == 1 or idx % 100 == 0 or idx == total:
            typer.echo(f"  [{idx}/{total}] {corp_code} {yy} {rcept_no}")

    result = index_audit_procedures_from_sections(
        year=year,
        limit=limit,
        progress_callback=_progress,
    )
    typer.echo(
        f"완료 - 처리 {result['total']:,} | 성공 {result['ok']:,} | "
        f"실패 {result['failed']:,} | rows_written {result['rows_written']:,}"
    )
    if result["errors"]:
        typer.echo("실패 샘플:")
        for row in result["errors"][:10]:
            typer.echo(f"  {row.get('rcept_no')}: {row.get('error')}")


@app.command("rebuild-audit-matter-items")
def rebuild_audit_matter_items_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 섹션 수"),
):
    """감사보고서 강조사항/기타사항/계속기업 문단을 검색용 정형 테이블로 재생성한다."""
    from kreports.collector.audit_matter_indexer import rebuild_audit_matter_items

    _json_print(rebuild_audit_matter_items(year=year, limit=limit))


@app.command("rebuild-kam-items")
def rebuild_kam_items_cmd(
    year: int = typer.Option(..., "--year", help="대상 사업연도"),
    market: Optional[str] = typer.Option(
        None,
        "--market",
        help="시장 필터: KOSPI/KOSDAQ/KONEX",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="품질 분포만 계산하고 kam_items에 쓰지 않음",
    ),
):
    """로컬 원문 증거에서 접수번호별 KAM matter를 재구성한다."""
    from kreports.collector.report_document_collector import rebuild_kam_items

    if not dry_run:
        init_db()
    result = rebuild_kam_items(year=year, market=market, dry_run=dry_run)
    typer.echo(
        f"KAM rebuild {'dry-run' if dry_run else 'complete'}: "
        f"database_status={result['database_status']} "
        f"receipts_total={result['total']} "
        f"receipt_full_body={result['receipt_counts']['full_body']} "
        f"receipt_summary_only={result['receipt_counts']['summary_only']} "
        f"receipt_missing={result['receipt_counts']['missing']} "
        f"receipt_error={result['receipt_counts']['error']} "
        f"matter_items_total={result['items_total']} "
        f"item_full_body={result['item_counts']['full_body']} "
        f"item_summary_only={result['item_counts']['summary_only']} "
        f"item_missing={result['item_counts']['missing']} "
        f"item_error={result['item_counts']['error']} "
        f"rows_written={result['rows_written']}"
    )
    for receipt in result["receipts"]:
        if receipt["quality_status"] not in {"missing", "error"}:
            continue
        typer.echo(
            f"- {receipt['rcept_no']} {receipt['quality_status']} "
            f"source={receipt['source_basis']} "
            f"limitations={';'.join(receipt['limitations']) or '-'}"
        )


@app.command("rebuild-disclosure-events")
def rebuild_disclosure_events_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 공시 제출연도"),
    market: Optional[str] = typer.Option(None, "--market", help="시장 필터: KOSPI/KOSDAQ/KONEX"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 공시 수"),
):
    """수시공시 제목을 투자자/감사인 이벤트 레이어로 인덱싱한다."""
    from kreports.collector.disclosure_event_indexer import rebuild_disclosure_events

    _json_print(rebuild_disclosure_events(year=year, market=market, limit=limit))


@app.command("migrate-raw-documents-to-storage")
def migrate_raw_documents_to_storage_cmd(
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 문서 수"),
    clear_inline: bool = typer.Option(False, "--clear-inline", help="검증 후 DB raw_content를 비움"),
    backend: str = typer.Option("file", "--backend", help="저장 backend: file/gcs"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="GCS bucket 이름. backend=gcs일 때 필요"),
    prefix: str = typer.Option("", "--prefix", help="GCS object prefix"),
):
    """source_documents.raw_content를 압축 raw store로 이전한다."""
    from kreports.maintenance.raw_storage_migration import migrate_raw_documents_to_storage

    init_db()
    result = migrate_raw_documents_to_storage(
        limit=limit,
        clear_inline=clear_inline,
        backend=backend,
        bucket=bucket,
        prefix=prefix,
    )
    _json_print(result)


@app.command("verify-raw-storage")
def verify_raw_storage_cmd(
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 검증 문서 수"),
):
    """외부화된 원문을 읽고 hash/length를 검증한다."""
    from kreports.maintenance.raw_storage_migration import verify_raw_storage

    result = verify_raw_storage(limit=limit)
    _json_print(result)


@app.command("clear-externalized-raw-content")
def clear_externalized_raw_content_cmd(
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 문서 수"),
):
    """외부 gzip 검증이 끝난 문서의 DB inline raw_content를 비운다."""
    from kreports.maintenance.raw_storage_migration import clear_externalized_inline_content

    result = clear_externalized_inline_content(limit=limit)
    _json_print(result)


@app.command("clear-cold-derived-raw-content")
def clear_cold_derived_raw_content_cmd(
    year_to: int = typer.Option(..., "--year-to", help="이 사업연도 이하의 cold raw만 처리"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 문서 수"),
    apply: bool = typer.Option(False, "--apply", help="dry-run이 아니라 실제 raw_content를 비움"),
):
    """파생데이터가 존재하는 과거 원문을 derived_only 상태로 비운다."""
    from kreports.maintenance.raw_storage_migration import clear_cold_derived_inline_content

    result = clear_cold_derived_inline_content(
        year_to=year_to,
        limit=limit,
        dry_run=not apply,
    )
    _json_print(result)


@app.command("raw-storage-readiness")
def raw_storage_readiness_cmd():
    """source_documents 원문 외부화 상태를 요약한다."""
    from kreports.maintenance.raw_storage_migration import raw_storage_readiness

    init_db()
    _json_print(raw_storage_readiness())


@app.command("raw-storage-config")
def raw_storage_config_cmd():
    """collector가 신규 원문을 DB inline 또는 외부 저장소 중 어디에 저장할지 보여준다."""
    from kreports.maintenance.raw_storage_migration import raw_storage_config_status

    _json_print(raw_storage_config_status())


@app.command("raw-storage-smoke")
def raw_storage_smoke_cmd(
    backend: str = typer.Option("file", "--backend", help="저장 backend: file/gcs"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="GCS bucket 이름. backend=gcs일 때 필요"),
    prefix: str = typer.Option("", "--prefix", help="GCS object prefix"),
):
    """원문 저장소에 작은 문서 1건을 쓰고 다시 읽어 hash/roundtrip을 검증한다."""
    from kreports.maintenance.raw_storage_migration import raw_storage_smoke

    result = raw_storage_smoke(backend=backend, bucket=bucket, prefix=prefix)
    _json_print(result)


@app.command("externalize-long-evidence-text")
def externalize_long_evidence_text_cmd(
    table_name: str = typer.Option(..., "--table", help="accounting_note_chapters/evidence_documents/report_sections"),
    excerpt_chars: int = typer.Option(2000, "--excerpt-chars", help="DB에 남길 짧은 본문 길이"),
    min_text_chars: int = typer.Option(4000, "--min-text-chars", help="외부화할 최소 본문 길이"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 행 수"),
    backend: str = typer.Option("file", "--backend", help="file/gcs"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="GCS bucket"),
    prefix: str = typer.Option("evidence/full-text", "--prefix", help="blob prefix"),
):
    """긴 파생 evidence 본문을 GCS/file로 옮기고 DB에는 excerpt와 manifest만 남긴다."""
    from kreports.maintenance.evidence_blob_migration import externalize_long_evidence_text

    init_db()
    result = externalize_long_evidence_text(
        table_name=table_name,
        excerpt_chars=excerpt_chars,
        min_text_chars=min_text_chars,
        limit=limit,
        backend=backend,
        bucket=bucket,
        prefix=prefix,
    )
    _json_print(result)


@app.command("rebuild-financial-facts-compact")
def rebuild_financial_facts_compact_cmd(
    year_from: Optional[int] = typer.Option(None, "--year-from"),
    year_to: Optional[int] = typer.Option(None, "--year-to"),
):
    """5개년 runtime DB용 핵심 재무 metric 테이블을 재생성한다."""
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    init_db()
    result = rebuild_financial_facts_compact(year_from=year_from, year_to=year_to)
    _json_print(result)


@app.command("rebuild-company-year-quality")
def rebuild_company_year_quality_cmd(
    year_from: int = typer.Option(..., "--year-from", help="시작 사업연도"),
    year_to: int = typer.Option(..., "--year-to", help="종료 사업연도"),
    market: Optional[str] = typer.Option(
        None,
        "--market",
        help="KOSPI/KOSDAQ 등 대상 시장",
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """기존 파생 데이터로 회사-연도별 기능 품질 원장을 재생성한다."""
    from kreports.quality.company_year import rebuild_company_year_quality

    init_db()
    result = rebuild_company_year_quality(
        year_from=year_from,
        year_to=year_to,
        market=market,
    )
    if json_output:
        _json_print(result)
        return
    typer.echo(
        "Company-year quality rebuild: "
        f"{result['year_from']}-{result['year_to']} "
        f"market={result['market'] or 'ALL'}"
    )
    typer.echo(f"Companies evaluated: {result['companies_evaluated']}")
    typer.echo(f"Rows written: {result['rows_written']}")
    typer.echo(f"Quality version: {result['quality_version']}")


@app.command("quality-release-gate")
def quality_release_gate_cmd(
    profile: str = typer.Option(
        "public_runtime",
        "--profile",
        help="public_runtime 또는 auditor_full",
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """준비된 데이터셋의 배포 게이트를 읽기 전용으로 평가한다."""
    from kreports.quality.release_gate import evaluate_release_gate

    report = evaluate_release_gate(profile)
    if json_output:
        _json_print(report)
    else:
        typer.echo(
            f"Release gate: {'PASS' if report['ok'] else 'FAIL'} "
            f"(profile={report['profile']})"
        )
        typer.echo(f"Schema version: {report['schema_version']}")
        typer.echo(f"Dataset version: {report['dataset_version']}")
        typer.echo(f"Tool count: {report['tool_count']}")
        typer.echo(
            "Required failures: "
            + (", ".join(report["required_failures"]) or "none")
        )
        typer.echo(
            "Degraded features: "
            + (", ".join(report["degraded_features"]) or "none")
        )
        typer.echo("Coverage and exact denominators:")
        for feature, values in sorted(report["coverage"].items()):
            typer.echo(
                f"- {feature}: {values['numerator']}/"
                f"{values['denominator']} "
                f"({values['coverage_pct']}%, "
                f"threshold {values['threshold_pct']}%)"
            )
        coverage_metadata = report.get("coverage_metadata") or {}
        if coverage_metadata:
            typer.echo("Coverage policy:")
            for feature, values in sorted(coverage_metadata.items()):
                typer.echo(
                    f"- {feature}: "
                    + json.dumps(
                        values,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
        typer.echo("Excluded populations:")
        for feature, values in sorted(
            report["excluded_populations"].items()
        ):
            formatted = ", ".join(
                f"{key}={value}" for key, value in sorted(values.items())
            )
            typer.echo(f"- {feature}: {formatted or 'none'}")
    if not report["ok"]:
        raise typer.Exit(1)


@app.command("plan-investor-core-backfill")
def plan_investor_core_backfill_cmd(
    db_path: Path = typer.Option(..., "--db", help="읽기 전용으로 검사할 SQLite DB 경로"),
    coverage_year: Optional[int] = typer.Option(
        None,
        "--coverage-year",
        help="기준 사업연도 (기본: quality 원장의 최신 연도)",
    ),
    threshold_pct: float = typer.Option(
        95.0,
        "--threshold-pct",
        help="목표 커버리지 비율 (0 초과, 100 이하)",
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
) -> None:
    """투자자 core 3년 게이트 부족분을 네트워크 없이 계획한다."""
    from kreports.maintenance.investor_core_backfill_plan import (
        plan_investor_core_backfill,
    )

    try:
        plan = plan_investor_core_backfill(
            db_path,
            coverage_year=coverage_year,
            threshold_pct=threshold_pct,
        )
    except (ValueError, sqlite3.Error) as exc:
        if json_output:
            typer.echo(
                json.dumps(
                    {"error": "investor_core_backfill_plan_unavailable"},
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        else:
            typer.echo("investor-core backfill plan unavailable", err=True)
        raise typer.Exit(2) from exc
    if json_output:
        typer.echo(
            json.dumps(
                plan,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    typer.echo(
        "Investor-core backfill preflight: "
        f"{plan['numerator']}/{plan['denominator']} "
        f"(target {plan['target_numerator']}, shortfall {plan['shortfall']})"
    )
    typer.echo(
        "Selected: "
        f"{plan['selected_candidate_count']} companies, "
        f"{plan['selected_successful_company_year_request_count']} "
        "successful company-year requests"
    )
    typer.echo(
        "Source readiness: "
        f"{plan['selected_source_ready_count']} ready, "
        f"{plan['selected_needing_disclosure_metadata_count']} need disclosure metadata"
    )
    typer.echo(
        "Annual filing anchors: "
        f"{plan['selected_valid_annual_anchor_company_count']} companies/"
        f"{plan['selected_valid_annual_anchor_year_count']} years valid, "
        f"{plan['selected_invalid_annual_anchor_company_count']} companies/"
        f"{plan['selected_invalid_annual_anchor_year_count']} years invalid, "
        f"{plan['selected_true_missing_disclosure_metadata_company_count']} companies/"
        f"{plan['selected_true_missing_disclosure_metadata_year_count']} years true-missing"
    )
    if plan["unfillable_shortfall"]:
        typer.echo(f"Unfillable shortfall: {plan['unfillable_shortfall']}")


_INVESTOR_CORE_RUNNER_ERROR_MESSAGES = {
    "database_unavailable": "database is unavailable or not a regular SQLite file",
    "database_symlink_rejected": "database path must not contain symlinks",
    "database_binding_mismatch": "--db does not match the configured process database",
    "expected_db_sha256_required": "--expected-db-sha256 is required with --execute",
    "invalid_expected_db_sha256": "expected database SHA-256 is invalid",
    "expected_db_sha256_mismatch": "database SHA-256 does not match expected value",
    "database_changed_before_execution": "database changed after preflight",
    "max_api_calls_required": "--max-api-calls must be positive with --execute",
    "dart_api_key_required": "DART API key is required with --execute",
    "collector_mode_required": "collector runtime mode is required with --execute",
    "non_source_ready_execution_rejected": "execute mode accepts source-ready targets only",
    "invalid_planner_output": "investor-core planner output is invalid",
    "duplicate_planner_target": "investor-core planner contains duplicate targets",
    "free_space_probe_failed": "free-space probe failed",
    "relevant_row_count_failed": "relevant database rows could not be counted",
    "insufficient_free_space": "free-space reserve is below the 10 GiB minimum",
    "api_budget_exhausted": "DART request budget exhausted",
    "dart_auth_failure": "DART authentication failed",
    "dart_quota_failure": "DART API quota or limit failure",
    "dart_transport_failure": "DART transport or HTTP failure",
    "collector_failure": "bounded collector failed",
    "evidence_collection_failed": "post-run evidence could not be collected",
}


@app.command("run-investor-core-backfill")
def run_investor_core_backfill_cmd(
    db_path: Path = typer.Option(..., "--db", help="bounded runner SQLite DB path"),
    execute: bool = typer.Option(False, "--execute", help="perform the bounded collector session"),
    expected_db_sha256: Optional[str] = typer.Option(
        None,
        "--expected-db-sha256",
        help="expected SHA-256 for the exact SQLite file (required with --execute)",
    ),
    max_api_calls: Optional[int] = typer.Option(
        None,
        "--max-api-calls",
        help="positive maximum actual DART HTTP attempts (required with --execute)",
    ),
    coverage_year: Optional[int] = typer.Option(None, "--coverage-year"),
    threshold_pct: float = typer.Option(95.0, "--threshold-pct"),
    source_ready_only: bool = typer.Option(
        True,
        "--source-ready-only/--include-non-source-ready",
        help="execute only planner candidates with source_ready=true",
    ),
) -> None:
    """Run or dry-run the fail-closed bounded investor-core backfill."""
    from kreports.maintenance.investor_core_backfill_runner import (
        InvestorCoreBackfillError,
        run_investor_core_backfill,
    )

    try:
        report = run_investor_core_backfill(
            db_path,
            expected_db_sha256=expected_db_sha256,
            execute=execute,
            max_api_calls=max_api_calls,
            coverage_year=coverage_year,
            threshold_pct=threshold_pct,
            source_ready_only=source_ready_only,
        )
    except InvestorCoreBackfillError as exc:
        message = _INVESTOR_CORE_RUNNER_ERROR_MESSAGES.get(
            exc.code,
            "investor-core backfill runner rejected the request",
        )
        typer.echo(
            json.dumps(
                {"error": {"code": exc.code, "message": message}},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise typer.Exit(2) from exc
    except (ValueError, sqlite3.Error) as exc:
        typer.echo(
            json.dumps(
                {
                    "error": {
                        "code": "investor_core_backfill_unavailable",
                        "message": "investor-core backfill runner unavailable",
                    }
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(
            json.dumps(
                {
                    "error": {
                        "code": "investor_core_backfill_failed",
                        "message": "investor-core backfill runner failed",
                    }
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise typer.Exit(2) from exc

    _json_print(report)
    if not report.get("completed", False):
        raise typer.Exit(3)


@app.command("build-release-manifest")
def build_release_manifest_cmd(
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Runtime SQLite DB (기본: configured local DB)",
    ),
    manifest_path: Optional[Path] = typer.Option(
        None,
        "--manifest",
        help="Release manifest JSON (기본: DB 옆 *.release.json)",
    ),
    profile: str = typer.Option(
        "public_runtime",
        "--profile",
        help="public_runtime 또는 auditor_full",
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """현재 DB 증거를 원자적으로 기록한다. Gate 실패도 blocker로 보존한다."""
    from kreports import release_artifact

    try:
        selected_db = db_path or release_artifact.default_runtime_db_path()
        output = release_artifact.build_release_manifest(
            selected_db,
            manifest_path,
            profile=profile,
        )
        manifest = release_artifact.ReleaseManifest.model_validate_json(
            output.read_text()
        )
    except (
        OSError,
        ValueError,
        release_artifact.ReleaseArtifactError,
    ) as exc:
        typer.echo(
            f"release manifest build rejected: {exc}",
            err=True,
        )
        raise typer.Exit(2) from exc

    result = {
        "artifact": str(output),
        "ready": manifest.release_gate.passed,
        "blockers": manifest.release_gate.blockers,
    }
    if json_output:
        _json_print(result)
    else:
        typer.echo(f"Release manifest: {output}")
        typer.echo(f"Ready: {str(result['ready']).lower()}")
        typer.echo(
            "Blockers: " + (", ".join(result["blockers"]) or "none")
        )


@app.command("verify-release-artifact")
def verify_release_artifact_cmd(
    db_path: Optional[Path] = typer.Option(
        None,
        "--db",
        help="Runtime SQLite DB (기본: configured local DB)",
    ),
    manifest_path: Optional[Path] = typer.Option(
        None,
        "--manifest",
        help="Release manifest JSON (기본: DB 옆 *.release.json)",
    ),
    profile: str = typer.Option(
        "public_runtime",
        "--profile",
        help="public_runtime 또는 auditor_full",
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 출력"),
):
    """저장된 값을 신뢰하지 않고 현재 DB와 모든 release 증거를 재검증한다."""
    from kreports import release_artifact

    try:
        selected_db = db_path or release_artifact.default_runtime_db_path()
        result = release_artifact.verify_release_artifact(
            selected_db,
            manifest_path,
            profile=profile,
        )
    except (
        OSError,
        ValueError,
        release_artifact.ReleaseArtifactError,
    ) as exc:
        typer.echo(
            f"release artifact verification rejected: {exc}",
            err=True,
        )
        raise typer.Exit(2) from exc

    payload = result.model_dump(mode="json")
    if json_output:
        _json_print(payload)
    else:
        typer.echo(
            f"Release artifact verification: "
            f"{'PASS' if result.ok else 'FAIL'}"
        )
        typer.echo(
            "Failures: " + (", ".join(result.failures) or "none")
        )
    if not result.ok:
        raise typer.Exit(1)


@app.command("export-runtime-db")
def export_runtime_db_cmd(
    output_path: Path = typer.Option(..., "--output", help="exported SQLite DB path"),
    year_from: int = typer.Option(..., "--year-from"),
    year_to: int = typer.Option(..., "--year-to"),
    profile: str = typer.Option("compact", "--profile"),
    vacuum: bool = typer.Option(True, "--vacuum/--no-vacuum", help="VACUUM exported DB after copy"),
):
    """배포용 compact runtime DB를 생성한다."""
    from kreports.maintenance.runtime_export import export_runtime_db

    init_db()
    result = export_runtime_db(
        output_path=output_path,
        year_from=year_from,
        year_to=year_to,
        profile=profile,
        vacuum=vacuum,
    )
    _json_print(result)


@app.command("upload-runtime-db-artifact")
def upload_runtime_db_artifact_cmd(
    db_path: Path = typer.Option(..., "--db", help="runtime DB path"),
    bucket: str = typer.Option(..., "--bucket", help="GCS bucket"),
    prefix: str = typer.Option("runtime-db", "--prefix"),
    profile: str = typer.Option("compact", "--profile"),
    year_from: int = typer.Option(..., "--year-from"),
    year_to: int = typer.Option(..., "--year-to"),
):
    """배포용 runtime DB artifact와 manifest를 GCS에 업로드한다."""
    from kreports.maintenance.runtime_export import upload_runtime_db_artifact

    result = upload_runtime_db_artifact(
        db_path=db_path,
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        year_from=year_from,
        year_to=year_to,
    )
    _json_print(result)


@app.command("rebuild-evidence-documents")
def rebuild_evidence_documents_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    year_from: Optional[int] = typer.Option(None, "--year-from", help="시작 사업연도"),
    year_to: Optional[int] = typer.Option(None, "--year-to", help="종료 사업연도"),
    corp_code: Optional[str] = typer.Option(None, "--corp-code", help="대상 DART corp_code"),
    source_type: Optional[str] = typer.Option(None, "--source-type", help="business_report/audit_report"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 문서 수"),
    max_text_chars: int = typer.Option(12000, "--max-text-chars", help="문서별 evidence text 최대 문자 수"),
):
    """파생 evidence 테이블에서 MCP 검색용 경량 문서 캐시를 재생성한다."""
    from kreports.maintenance.evidence_documents import rebuild_evidence_documents

    init_db()
    result = rebuild_evidence_documents(
        year=year,
        year_from=year_from,
        year_to=year_to,
        corp_code=corp_code,
        source_type=source_type,
        limit=limit,
        max_text_chars=max_text_chars,
    )
    _json_print(result)


@app.command("trim-evidence-documents")
def trim_evidence_documents_cmd(
    year_from: Optional[int] = typer.Option(None, "--year-from", help="이 연도 이전 evidence 삭제"),
    year_to: Optional[int] = typer.Option(None, "--year-to", help="이 연도 이후 evidence 삭제"),
    max_text_chars: int = typer.Option(12000, "--max-text-chars", help="문서별 evidence text 최대 문자 수"),
):
    """MCP 검색용 evidence document를 최근연도/길이 기준으로 슬림화한다."""
    from kreports.maintenance.evidence_documents import trim_evidence_documents

    init_db()
    result = trim_evidence_documents(
        year_from=year_from,
        year_to=year_to,
        max_text_chars=max_text_chars,
    )
    _json_print(result)


@app.command("evidence-document-readiness")
def evidence_document_readiness_cmd():
    """MCP 검색용 경량 evidence document 적재 상태를 요약한다."""
    from kreports.maintenance.evidence_documents import evidence_document_readiness

    init_db()
    _json_print(evidence_document_readiness())


@app.command("restore-report-sections-from-evidence")
def restore_report_sections_from_evidence_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    year_from: Optional[int] = typer.Option(None, "--year-from", help="시작 사업연도"),
    year_to: Optional[int] = typer.Option(None, "--year-to", help="종료 사업연도"),
    source_type: Optional[str] = typer.Option(None, "--source-type", help="business_report/audit_report"),
    corp_code: Optional[str] = typer.Option(None, "--corp-code", help="대상 DART corp_code"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 evidence 문서 수"),
):
    """evidence_documents의 report_section 블록을 report_sections로 복원한다."""
    from kreports.maintenance.evidence_documents import restore_report_sections_from_evidence

    init_db()
    result = restore_report_sections_from_evidence(
        year=year,
        year_from=year_from,
        year_to=year_to,
        source_type=source_type,
        corp_code=corp_code,
        limit=limit,
    )
    _json_print(result)


@app.command("hydrate-source-documents-from-sections")
def hydrate_source_documents_from_sections_cmd(
    year: Optional[int] = typer.Option(None, "--year", help="대상 사업연도"),
    source_type: Optional[str] = typer.Option(None, "--source-type", help="business_report/audit_report"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 문서 수"),
):
    """기존 report_sections를 derived source_documents로 묶는다. 원문 캐시로 간주하지 않는다."""
    from kreports.collector.report_document_collector import hydrate_source_documents_from_report_sections

    init_db()

    def _progress(idx, total, corp_code, yy, src_type, rcept_no):
        if idx == 1 or idx % 100 == 0 or idx == total:
            typer.echo(f"  [{idx}/{total}] {corp_code} {yy} {src_type} {rcept_no}")

    result = hydrate_source_documents_from_report_sections(
        year=year,
        source_type=source_type,
        limit=limit,
        progress_callback=_progress,
    )
    typer.echo(
        f"완료 - 처리 {result['total']:,} | 생성 {result['created']:,} | "
        f"갱신 {result['updated']:,} | 원문보존 skip {result['skipped_raw']:,}"
    )
    if result["errors"]:
        typer.echo("실패 샘플:")
        for row in result["errors"][:10]:
            typer.echo(f"  {row.get('rcept_no')}: {row.get('error')}")


# ---------------------------------------------------------------------------
# collect-audit-fees (DS002 감사보수 수집)
# ---------------------------------------------------------------------------

@app.command("collect-audit-fees")
def collect_audit_fees_cmd(
    stock: Optional[str] = typer.Option(None, help="단일 종목코드. 생략 시 전체 수집."),
    year_from: Optional[int] = typer.Option(None, help="수집 시작 연도"),
    year_to: Optional[int] = typer.Option(None, help="수집 종료 연도"),
    market: Optional[str] = typer.Option(None, help="전체 수집 시 시장 필터: KOSPI/KOSDAQ/KONEX"),
    force: bool = typer.Option(False, "--force", help="동일 백필 running 기록이 있어도 강제 실행"),
):
    """DS002 감사보수/비감사보수를 수집하고 NAS ratio를 계산한다."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    if stock:
        from kreports.collector.corp_sync import get_corp_code
        from kreports.collector.audit_fee_collector import collect_audit_fees
        corp_code = get_corp_code(stock)
        if not corp_code:
            typer.echo(f"오류: {stock} 종목을 찾을 수 없습니다.", err=True)
            raise typer.Exit(1)
        with _backfill_run_guard(
            task_type="audit_fees",
            year=year_from,
            market="SINGLE",
            params={"stock": stock, "year_from": year_from, "year_to": year_to},
            force=force,
        ) as run_id:
            result = collect_audit_fees(corp_code, year_from, year_to)
            _finish_backfill_run(run_id, result)
        typer.echo(f"완료 - 저장: {result['saved']}, 데이터없음: {result['no_data']}, 오류: {result['error']}")
    else:
        from kreports.collector.audit_fee_collector import collect_all_audit_fees

        def _progress(done, total, corp_name):
            if done % 50 == 0 or done == total:
                typer.echo(f"\r[{done}/{total}] {corp_name}", nl=False)

        typer.echo("전체 감사보수 배치 수집 시작...")
        with _backfill_run_guard(
            task_type="audit_fees",
            year=year_from,
            market=market,
            params={"year_from": year_from, "year_to": year_to, "market": market},
            force=force,
        ) as run_id:
            result = collect_all_audit_fees(year_from, year_to, market=market, progress_callback=_progress)
            _finish_backfill_run(run_id, result)
        typer.echo(
            f"\n완료 - 저장: {result['saved']:,}, "
            f"데이터없음: {result['no_data']:,}, 오류: {result['error']:,}"
        )


# ---------------------------------------------------------------------------
# compute-flags (판단 플래그 재계산)
# ---------------------------------------------------------------------------

@app.command("compute-flags")
def compute_flags_cmd(
    stock: Optional[str] = typer.Option(None, help="단일 종목코드. 생략 시 전체."),
):
    """재무 판단 플래그(CFS/OFS 괴리, 트렌드, CF, Beneish)를 재계산한다."""
    from kreports.judge.flags import (
        compute_all_gap_flags, compute_all_trend_cf_flags,
    )
    from kreports.judge.beneish import compute_all_beneish

    def _run_all(corp_code: str) -> dict:
        gap_n = compute_all_gap_flags(corp_code)
        trend_n = compute_all_trend_cf_flags(corp_code)
        beneish_n = compute_all_beneish(corp_code)
        return {"gap": gap_n, "trend": trend_n, "beneish": beneish_n}

    if stock:
        from kreports.collector.corp_sync import get_corp_code
        corp_code = get_corp_code(stock)
        if not corp_code:
            typer.echo(f"오류: {stock} 종목을 찾을 수 없습니다.", err=True)
            raise typer.Exit(1)
        r = _run_all(corp_code)
        typer.echo(
            f"완료 - gap: {r['gap']}기간, trend+CF: {r['trend']}기간, "
            f"Beneish: {r['beneish']}연도"
        )
    else:
        with get_session() as session:
            corp_codes = [
                r[0] for r in
                session.query(Company.corp_code).filter(Company.stock_code.isnot(None)).all()
            ]
        totals = {"gap": 0, "trend": 0, "beneish": 0}
        for idx, cc in enumerate(corp_codes, 1):
            if idx % 100 == 0:
                typer.echo(f"\r[{idx}/{len(corp_codes)}]", nl=False)
            r = _run_all(cc)
            for k in totals:
                totals[k] += r[k]
        typer.echo(
            f"\n완료 - gap: {totals['gap']:,}기간, "
            f"trend+CF: {totals['trend']:,}기간, "
            f"Beneish: {totals['beneish']:,}연도"
        )


# ---------------------------------------------------------------------------
# show (재무 조회)
# ---------------------------------------------------------------------------

@app.command("show")
def show(
    stock: str = typer.Argument(..., help="종목코드 (예: 005930)"),
    year_from: Optional[int] = typer.Option(None, help="조회 시작 연도"),
    year_to: Optional[int] = typer.Option(None, help="조회 종료 연도"),
    unit: str = typer.Option("억원", help="금액 단위 (억원 / 십억원 / 원)"),
):
    """수집된 재무지표를 테이블로 출력한다."""
    with get_session() as session:
        company = session.query(Company).filter_by(stock_code=stock).first()
        if not company:
            typer.echo(f"오류: {stock} 종목을 찾을 수 없습니다.", err=True)
            raise typer.Exit(1)
        corp_code = company.corp_code
        corp_name = company.corp_name
        market = company.market

        q = session.query(Financial).filter_by(corp_code=corp_code)
        if year_from:
            q = q.filter(Financial.year >= year_from)
        if year_to:
            q = q.filter(Financial.year <= year_to)

        rows = [
            {
                "year": r.year, "quarter": r.quarter, "fs_div": r.fs_div,
                "revenue": r.revenue, "operating_profit": r.operating_profit,
                "net_income": r.net_income, "total_debt": r.total_debt,
                "total_equity": r.total_equity,
                "map_conf": r.account_map_confidence,
                "gap_flag": r.cfs_ofs_gap_flag,
            }
            for r in q.order_by(Financial.year, Financial.quarter).all()
        ]

    if not rows:
        typer.echo("수집된 데이터가 없습니다. collect 명령을 먼저 실행하세요.")
        raise typer.Exit(0)

    divisor, unit_label = _get_divisor(unit)
    headers = [
        "연도", "분기", "구분",
        f"매출액({unit_label})", f"영업이익({unit_label})", f"순이익({unit_label})",
        "부채비율(%)", "매핑률", "CFS/OFS괴리",
    ]
    table = []
    for r in rows:
        debt_ratio = None
        if r["total_debt"] is not None and r["total_equity"]:
            debt_ratio = round(r["total_debt"] / r["total_equity"] * 100, 1)
        gap = "O" if r["gap_flag"] else ("-" if r["gap_flag"] is None else "")
        conf = f"{r['map_conf']:.0%}" if r["map_conf"] is not None else "-"
        table.append([
            r["year"], f"Q{r['quarter']}", r["fs_div"],
            _fmt(r["revenue"], divisor), _fmt(r["operating_profit"], divisor),
            _fmt(r["net_income"], divisor),
            f"{debt_ratio:.1f}" if debt_ratio is not None else "-",
            conf, gap,
        ])

    typer.echo(f"\n종목: {corp_name} ({stock}) | 시장: {market}")
    typer.echo(tabulate(table, headers=headers, tablefmt="github", numalign="right"))


# ---------------------------------------------------------------------------
# show-disclosures
# ---------------------------------------------------------------------------

@app.command("show-disclosures")
def show_disclosures(
    stock: str = typer.Argument(..., help="종목코드"),
    limit: int = typer.Option(20, help="출력 건수"),
):
    """수집된 공시 목록을 출력한다."""
    with get_session() as session:
        company = session.query(Company).filter_by(stock_code=stock).first()
        if not company:
            typer.echo(f"오류: {stock} 종목을 찾을 수 없습니다.", err=True)
            raise typer.Exit(1)
        corp_name = company.corp_name

        rows = (
            session.query(Disclosure)
            .filter_by(corp_code=company.corp_code)
            .order_by(Disclosure.disc_date.desc())
            .limit(limit)
            .all()
        )
        rows = [(r.disc_date, r.disc_type, r.report_nm[:60], r.flr_nm or "-") for r in rows]

    if not rows:
        typer.echo("수집된 공시가 없습니다. collect-disclosures 먼저 실행하세요.")
        raise typer.Exit(0)

    headers = ["공시일", "유형", "공시명", "제출인"]
    typer.echo(f"\n종목: {corp_name} ({stock})")
    typer.echo(tabulate(rows, headers=headers, tablefmt="github"))


# ---------------------------------------------------------------------------
# show-auditors
# ---------------------------------------------------------------------------

@app.command("show-auditors")
def show_auditors(
    stock: str = typer.Argument(..., help="종목코드"),
):
    """감사인 이력을 출력한다."""
    with get_session() as session:
        company = session.query(Company).filter_by(stock_code=stock).first()
        if not company:
            typer.echo(f"오류: {stock} 종목을 찾을 수 없습니다.", err=True)
            raise typer.Exit(1)
        corp_name = company.corp_name

        rows = (
            session.query(Auditor)
            .filter_by(corp_code=company.corp_code)
            .order_by(Auditor.fs_div, Auditor.bsns_year)
            .all()
        )
        data = [
            (
                r.bsns_year, r.fs_div, r.auditor_nm,
                r.audit_opinion or "-",
                "교체" if r.is_auditor_changed else ("최초" if r.is_auditor_changed is None else "유지"),
                r.consecutive_years or "-",
            )
            for r in rows
        ]

    if not data:
        typer.echo("감사인 이력이 없습니다. collect-auditors 먼저 실행하세요.")
        raise typer.Exit(0)

    headers = ["회계연도", "구분", "감사인", "감사의견", "교체여부", "연속연수"]
    typer.echo(f"\n종목: {corp_name} ({stock})")
    typer.echo(tabulate(data, headers=headers, tablefmt="github"))


# ---------------------------------------------------------------------------
# schedule-start (일별 증분 수집 스케줄러)
# ---------------------------------------------------------------------------

@app.command("schedule-start")
def schedule_start():
    """일별 증분 수집 스케줄러를 포그라운드로 실행한다. Ctrl+C로 종료."""
    if not settings.dart_api_key:
        typer.echo("오류: DART_API_KEY 미설정", err=True)
        raise typer.Exit(1)

    import time
    import signal
    from kreports.collector.scheduler import start_scheduler, list_jobs

    sched = start_scheduler()
    typer.echo("스케줄러 실행 중...")
    for job in list_jobs(sched):
        typer.echo(f"  [{job['id']}] {job['name']} - 다음 실행: {job['next_run']}")
    typer.echo("Ctrl+C로 종료하세요.")

    def _shutdown(signum, frame):
        typer.echo("\n스케줄러 종료 중...")
        sched.shutdown()
        raise typer.Exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while True:
            time.sleep(60)
    except (SystemExit, KeyboardInterrupt):
        pass


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status():
    """수집 현황을 출력한다."""
    from sqlalchemy import func
    with get_session() as session:
        total_companies = session.query(Company).filter(Company.stock_code.isnot(None)).count()
        total_financials = session.query(Financial).count()
        total_disclosures = session.query(Disclosure).count()
        total_auditors = session.query(Auditor).count()
        total_audit_fees = session.query(AuditFee).count()
        log_summary = list(
            session.query(FetchLog.task_type, FetchLog.status, func.count(FetchLog.id))
            .group_by(FetchLog.task_type, FetchLog.status)
            .all()
        )

    typer.echo(f"등록 기업:     {total_companies:,}개사")
    typer.echo(f"재무 레코드:   {total_financials:,}건")
    typer.echo(f"공시 레코드:   {total_disclosures:,}건")
    typer.echo(f"감사인 레코드: {total_auditors:,}건")
    typer.echo(f"감사보수 레코드: {total_audit_fees:,}건")
    if log_summary:
        typer.echo("\n수집 이력:")
        for task_type, stat, cnt in log_summary:
            typer.echo(f"  [{task_type}] {stat}: {cnt:,}건")


@app.command("dataset-audit")
def dataset_audit(
    top: int = typer.Option(10, "--top", help="중복 fetch 시도 상위 표시 건수"),
    fail_on_duplicates: bool = typer.Option(False, "--fail-on-duplicates", help="데이터 unique key 중복 발견 시 exit 1"),
):
    """데이터셋 중복 행과 중복 백필 시도를 분리해 점검한다."""
    from sqlalchemy import text

    init_db()
    duplicate_checks = {
        "disclosures.rcept_no": """
            SELECT count(*) FROM (
              SELECT rcept_no FROM disclosures
              GROUP BY rcept_no HAVING count(*) > 1
            ) x
        """,
        "financials.uq_financial": """
            SELECT count(*) FROM (
              SELECT corp_code, year, quarter, fs_div FROM financials
              GROUP BY corp_code, year, quarter, fs_div HAVING count(*) > 1
            ) x
        """,
        "financial_facts.unique": """
            SELECT count(*) FROM (
              SELECT corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id
              FROM financial_facts
              GROUP BY corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id
              HAVING count(*) > 1
            ) x
        """,
        "auditors.uq_auditor": """
            SELECT count(*) FROM (
              SELECT corp_code, bsns_year, fs_div FROM auditors
              GROUP BY corp_code, bsns_year, fs_div HAVING count(*) > 1
            ) x
        """,
        "audit_fees.uq_audit_fee": """
            SELECT count(*) FROM (
              SELECT corp_code, bsns_year FROM audit_fees
              GROUP BY corp_code, bsns_year HAVING count(*) > 1
            ) x
        """,
        "accounting_policy_items.unique": """
            SELECT count(*) FROM (
              SELECT corp_code, bsns_year, fs_div, item_key FROM accounting_policy_items
              GROUP BY corp_code, bsns_year, fs_div, item_key HAVING count(*) > 1
            ) x
        """,
        "report_documents.uq_report_document": """
            SELECT count(*) FROM (
              SELECT rcept_no, source_type FROM report_documents
              GROUP BY rcept_no, source_type HAVING count(*) > 1
            ) x
        """,
        "report_sections.uq_report_section": """
            SELECT count(*) FROM (
              SELECT rcept_no, source_type, section_key, ordinal FROM report_sections
              GROUP BY rcept_no, source_type, section_key, ordinal HAVING count(*) > 1
            ) x
        """,
        "source_documents.uq_source_document": """
            SELECT count(*) FROM (
              SELECT rcept_no, source_type FROM source_documents
              GROUP BY rcept_no, source_type HAVING count(*) > 1
            ) x
        """,
        "subsidiary_auditor_matrix.uq": """
            SELECT count(*) FROM (
              SELECT parent_rcept_no, name FROM subsidiary_auditor_matrix
              GROUP BY parent_rcept_no, name HAVING count(*) > 1
            ) x
        """,
    }
    count_tables = [
        "companies",
        "disclosures",
        "financials",
        "financial_facts",
        "auditors",
        "audit_fees",
        "accounting_policy_items",
        "report_documents",
        "report_sections",
        "source_documents",
        "extraction_runs",
        "subsidiary_auditor_matrix",
        "fetch_log",
        "backfill_runs",
    ]

    with get_session() as session:
        duplicate_rows = []
        duplicate_total = 0
        for name, query in duplicate_checks.items():
            count = int(session.execute(text(query)).scalar() or 0)
            duplicate_total += count
            duplicate_rows.append([name, count])

        count_rows = [
            [table, int(session.execute(text(f"SELECT count(*) FROM {table}")).scalar() or 0)]
            for table in count_tables
        ]
        fetch_status_rows = session.execute(text("""
            SELECT task_type, status, count(*) AS count
            FROM fetch_log
            GROUP BY task_type, status
            ORDER BY task_type, status
        """)).all()
        repeated_fetch_rows = session.execute(text("""
            SELECT task_type, corp_code, year, quarter, status, count(*) AS attempts
            FROM fetch_log
            GROUP BY task_type, corp_code, year, quarter, status
            HAVING count(*) > 1
            ORDER BY attempts DESC, task_type, corp_code
            LIMIT :top
        """), {"top": int(top)}).all()
        running_backfills = session.execute(text("""
            SELECT id, task_type, year, market, pid, started_at
            FROM backfill_runs
            WHERE status='running'
            ORDER BY started_at DESC
            LIMIT :top
        """), {"top": int(top)}).all()
        recent_backfills = session.execute(text("""
            SELECT id, task_type, year, market, status, started_at, finished_at
            FROM backfill_runs
            ORDER BY started_at DESC
            LIMIT :top
        """), {"top": int(top)}).all()

    typer.echo("DATASET DUPLICATE ROWS")
    typer.echo(tabulate(duplicate_rows, headers=["unique key", "duplicate groups"], tablefmt="github"))
    typer.echo("")
    typer.echo("TABLE COUNTS")
    typer.echo(tabulate(count_rows, headers=["table", "rows"], tablefmt="github"))
    typer.echo("")
    typer.echo("FETCH LOG STATUS")
    typer.echo(tabulate(fetch_status_rows, headers=["task_type", "status", "rows"], tablefmt="github"))
    typer.echo("")
    typer.echo("REPEATED FETCH ATTEMPTS")
    if repeated_fetch_rows:
        typer.echo(tabulate(
            repeated_fetch_rows,
            headers=["task_type", "corp_code", "year", "quarter", "status", "attempts"],
            tablefmt="github",
        ))
    else:
        typer.echo("없음")
    typer.echo("")
    typer.echo("RUNNING BACKFILLS")
    if running_backfills:
        typer.echo(tabulate(
            running_backfills,
            headers=["id", "task_type", "year", "market", "pid", "started_at"],
            tablefmt="github",
        ))
    else:
        typer.echo("없음")
    typer.echo("")
    typer.echo("RECENT BACKFILLS")
    if recent_backfills:
        typer.echo(tabulate(
            recent_backfills,
            headers=["id", "task_type", "year", "market", "status", "started_at", "finished_at"],
            tablefmt="github",
        ))
    else:
        typer.echo("없음")

    if fail_on_duplicates and duplicate_total:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt(value, divisor):
    if value is None:
        return "-"
    return f"{value // divisor:,}"


def _get_divisor(unit):
    if unit == "원":
        return 1, "원"
    if unit == "십억원":
        return 1_000_000_000, "십억원"
    return 100_000_000, "억원"


if __name__ == "__main__":
    app()
