from datetime import datetime, timezone
import json

from kreports.db.engine import get_session
from kreports.db.migrations import MIGRATIONS, apply_schema_migrations
from kreports.db.models import Company, CompanyYearQuality, DatasetManifest
from typer.testing import CliRunner


def _seed_valid_manifest(temp_engine, *, year: int = 2025) -> None:
    with temp_engine.begin() as connection:
        apply_schema_migrations(connection)
    with get_session() as session:
        session.add(
            DatasetManifest(
                manifest_id="release-v1",
                schema_version=MIGRATIONS[-1].revision,
                dataset_version="release-v1",
                generated_at=datetime.now(timezone.utc),
                year_from=year,
                year_to=year,
                company_count=0,
                disclosure_count=0,
                evidence_document_count=0,
                quality_snapshot_json="{}",
            )
        )


def _seed_quality_row(
    *,
    corp_code: str,
    grade: str,
    market: str = "KOSPI",
    stock_code: str | None = "000001",
    policy_status: str = "full_body",
    procedure_status: str = "available",
    kam_status: str = "full_body",
) -> None:
    with get_session() as session:
        session.add(
            Company(
                corp_code=corp_code,
                stock_code=stock_code,
                corp_name=f"회사-{corp_code}",
                market=market,
            )
        )
        session.add(
            CompanyYearQuality(
                corp_code=corp_code,
                bsns_year=2025,
                market=market,
                financial_core_status="available",
                auditor_status="available",
                audit_fee_status="available",
                policy_status=policy_status,
                kam_status=kam_status,
                audit_procedure_status=procedure_status,
                group_audit_status="missing",
                investor_grade=grade,
                auditor_grade="A",
                group_audit_grade="D",
                blockers_json="[]",
                quality_version="v1",
                updated_at=datetime.now(timezone.utc),
            )
        )


def test_public_runtime_accepts_exact_95_percent_with_exact_denominator(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_valid_manifest(temp_engine)
    for index in range(20):
        _seed_quality_row(
            corp_code=f"{index + 1:08d}",
            grade="A" if index < 19 else "D",
            stock_code=f"{index + 1:06d}",
        )
    _seed_quality_row(
        corp_code="90000001",
        grade="D",
        stock_code=None,
    )
    _seed_quality_row(
        corp_code="90000002",
        grade="D",
        market="KONEX",
        stock_code="900002",
    )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert report["ok"] is True
    assert report["denominators"]["investor_core"] == 20
    assert report["coverage"]["investor_core"] == {
        "numerator": 19,
        "denominator": 20,
        "coverage_pct": 95.0,
        "threshold_pct": 95.0,
    }
    assert report["excluded_populations"]["investor_core"] == {
        "not_listed": 1,
        "outside_core_markets": 1,
    }
    assert "investor_core_coverage" not in report["required_failures"]


def test_auditor_full_promotes_optional_policy_and_procedure_gaps(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_valid_manifest(temp_engine)
    for index in range(20):
        _seed_quality_row(
            corp_code=f"{index + 1:08d}",
            grade="A",
            stock_code=f"{index + 1:06d}",
            policy_status="missing" if index < 2 else "full_body",
            procedure_status="missing" if index < 2 else "available",
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    public = evaluate_release_gate("public_runtime")
    auditor = evaluate_release_gate("auditor_full")

    assert public["ok"] is True
    assert public["required_failures"] == []
    assert public["degraded_features"] == [
        "accounting_policy",
        "audit_procedure",
    ]
    assert auditor["ok"] is False
    assert auditor["required_failures"] == [
        "accounting_policy_coverage",
        "audit_procedure_coverage",
    ]


def test_explicit_no_kam_is_excluded_from_procedure_denominator(
    temp_engine,
    monkeypatch,
):
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_valid_manifest(temp_engine)
    for index in range(20):
        no_kam = index >= 18
        _seed_quality_row(
            corp_code=f"{index + 1:08d}",
            grade="A",
            stock_code=f"{index + 1:06d}",
            procedure_status=(
                "not_applicable" if no_kam else "available"
            ),
            kam_status="explicit_no_kam" if no_kam else "full_body",
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("auditor_full")

    assert report["ok"] is True
    assert report["coverage"]["audit_procedure"] == {
        "numerator": 18,
        "denominator": 18,
        "coverage_pct": 100.0,
        "threshold_pct": 95.0,
    }
    assert report["excluded_populations"]["audit_procedure"][
        "explicit_no_kam"
    ] == 2


def test_invalid_manifest_fails_closed(temp_engine, monkeypatch):
    from kreports.quality.release_gate import evaluate_release_gate

    with temp_engine.begin() as connection:
        apply_schema_migrations(connection)
    with get_session() as session:
        session.add(
            DatasetManifest(
                manifest_id="manifest-id",
                schema_version=MIGRATIONS[-1].revision,
                dataset_version="different-version",
                generated_at=datetime.now(timezone.utc),
                year_from=2025,
                year_to=2025,
                company_count=0,
                disclosure_count=0,
                evidence_document_count=0,
                quality_snapshot_json="{}",
            )
        )
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    report = evaluate_release_gate("public_runtime")

    assert report["schema_version"] == "unknown"
    assert report["dataset_version"] == "unknown"
    assert "release_manifest_unavailable" in report["required_failures"]


def test_release_gate_is_read_only_and_does_not_require_dart_key(
    temp_engine,
    monkeypatch,
):
    from sqlalchemy import event

    import kreports.db.engine as engine_module
    from kreports.quality.release_gate import evaluate_release_gate

    _seed_valid_manifest(temp_engine)
    _seed_quality_row(corp_code="00126380", grade="A", stock_code="005930")
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.setattr(
        engine_module,
        "init_db",
        lambda: (_ for _ in ()).throw(
            AssertionError("release gate must not initialize schema")
        ),
    )
    statements: list[str] = []

    def capture_sql(
        _conn,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        statements.append(str(statement))

    event.listen(temp_engine, "before_cursor_execute", capture_sql)
    try:
        report = evaluate_release_gate("public_runtime")
    finally:
        event.remove(temp_engine, "before_cursor_execute", capture_sql)

    assert report["ok"] is True
    assert statements
    assert all(
        statement.lstrip().upper().startswith(("SELECT", "PRAGMA"))
        for statement in statements
    )


def test_quality_release_gate_cli_supports_json_and_human_output(monkeypatch):
    from kreports.cli.main import app
    from kreports.quality import release_gate

    report = {
        "ok": False,
        "profile": "auditor_full",
        "schema_version": MIGRATIONS[-1].revision,
        "dataset_version": "release-v1",
        "required_failures": ["audit_procedure_coverage"],
        "degraded_features": ["audit_procedure"],
        "tool_count": 31,
        "coverage_year": 2025,
        "coverage": {
            "audit_procedure": {
                "numerator": 18,
                "denominator": 20,
                "coverage_pct": 90.0,
                "threshold_pct": 95.0,
            }
        },
        "denominators": {"audit_procedure": 20},
        "excluded_populations": {
            "audit_procedure": {
                "not_listed": 2,
                "outside_core_markets": 1,
                "explicit_no_kam": 3,
            }
        },
    }
    monkeypatch.setattr(release_gate, "evaluate_release_gate", lambda _profile: report)
    runner = CliRunner()

    json_result = runner.invoke(
        app,
        ["quality-release-gate", "--profile", "auditor_full", "--json"],
    )
    human_result = runner.invoke(
        app,
        ["quality-release-gate", "--profile", "auditor_full"],
    )

    assert json_result.exit_code == 1
    assert json.loads(json_result.stdout) == report
    assert human_result.exit_code == 1
    assert "Required failures: audit_procedure_coverage" in human_result.stdout
    assert "Degraded features: audit_procedure" in human_result.stdout
    assert "audit_procedure: 18/20 (90.0%, threshold 95.0%)" in human_result.stdout
    assert (
        "audit_procedure: explicit_no_kam=3, not_listed=2, "
        "outside_core_markets=1"
    ) in human_result.stdout


def test_rebuild_company_year_quality_cli_supports_json_and_human(
    monkeypatch,
):
    from kreports.cli import main as cli_main
    from kreports.quality import company_year as company_year_module

    result = {
        "year_from": 2024,
        "year_to": 2025,
        "market": "KOSPI",
        "companies_evaluated": 2,
        "rows_written": 4,
        "quality_version": "v1",
    }
    monkeypatch.setattr(cli_main, "init_db", lambda: None)
    monkeypatch.setattr(
        company_year_module,
        "rebuild_company_year_quality",
        lambda **_kwargs: result,
    )
    runner = CliRunner()

    json_result = runner.invoke(
        cli_main.app,
        [
            "rebuild-company-year-quality",
            "--year-from",
            "2024",
            "--year-to",
            "2025",
            "--market",
            "KOSPI",
            "--json",
        ],
    )
    human_result = runner.invoke(
        cli_main.app,
        [
            "rebuild-company-year-quality",
            "--year-from",
            "2024",
            "--year-to",
            "2025",
            "--market",
            "KOSPI",
        ],
    )

    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout) == result
    assert human_result.exit_code == 0
    assert "Rows written: 4" in human_result.stdout
    assert "market=KOSPI" in human_result.stdout
