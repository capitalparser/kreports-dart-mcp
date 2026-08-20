from kreports.runtime import (
    is_readonly_mode,
    readonly_cache_miss,
    require_collector_mode,
    require_raw_backfill_mode,
)


def test_readonly_mode_defaults_to_true_for_mcp(monkeypatch):
    monkeypatch.delenv("KREPORTS_RUNTIME_MODE", raising=False)
    assert is_readonly_mode() is True


def test_collector_mode_can_be_enabled(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    assert is_readonly_mode() is False


def test_require_collector_mode_blocks_readonly(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    try:
        require_collector_mode("collect-policies")
    except RuntimeError as exc:
        assert "collect-policies requires collector mode" in str(exc)
    else:
        raise AssertionError("collector guard did not raise")


def test_require_raw_backfill_mode_blocks_without_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.delenv("KREPORTS_ENABLE_RAW_BACKFILL", raising=False)

    try:
        require_raw_backfill_mode(
            "collect-business-report-sections",
            raw_storage_backend="gcs",
            raw_storage_keep_inline=False,
        )
    except RuntimeError as exc:
        assert "KREPORTS_ENABLE_RAW_BACKFILL=1" in str(exc)
    else:
        raise AssertionError("raw backfill guard did not raise")


def test_require_raw_backfill_mode_blocks_inline_storage(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")

    try:
        require_raw_backfill_mode(
            "collect-business-report-sections",
            raw_storage_backend="inline",
            raw_storage_keep_inline=False,
        )
    except RuntimeError as exc:
        assert "RAW_STORAGE_BACKEND=file or gcs" in str(exc)
    else:
        raise AssertionError("inline raw storage guard did not raise")


def test_require_raw_backfill_mode_blocks_keep_inline(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")

    try:
        require_raw_backfill_mode(
            "collect-business-report-sections",
            raw_storage_backend="gcs",
            raw_storage_keep_inline=True,
        )
    except RuntimeError as exc:
        assert "RAW_STORAGE_KEEP_INLINE=false" in str(exc)
    else:
        raise AssertionError("keep-inline raw storage guard did not raise")


def test_require_raw_backfill_mode_allows_external_storage(monkeypatch):
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "collector")
    monkeypatch.setenv("KREPORTS_ENABLE_RAW_BACKFILL", "1")
    monkeypatch.setenv("RAW_STORAGE_BUCKET", "kreports-raw-documents")

    require_raw_backfill_mode(
        "collect-business-report-sections",
        raw_storage_backend="gcs",
        raw_storage_keep_inline=False,
    )


def test_readonly_cache_miss_message_does_not_request_dart_key():
    msg = readonly_cache_miss("accounting_policy", "00126380", 2025)
    assert "pre-built DB" in msg
    assert "DART_API_KEY" not in msg


def test_mcp_smoke_cli_works_without_dart_key(temp_engine, monkeypatch):
    from typer.testing import CliRunner

    from kreports.cli.main import app
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00126380",
                stock_code="005930",
                corp_name="삼성전자",
                market="KOSPI",
                induty_code="26410",
            ),
            Financial(
                corp_code="00126380",
                year=2025,
                quarter=4,
                fs_div="CFS",
                revenue=1000,
                operating_profit=100,
                net_income=80,
                total_assets=2000,
                total_debt=800,
                total_equity=1200,
                operating_cf=90,
            ),
        ])

    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")
    result = CliRunner().invoke(
        app,
        ["mcp-smoke", "--company", "005930"],
    )

    assert result.exit_code == 0, result.output + repr(result.exception)
    assert "RESULT: OK" in result.output
    assert "DART_API_KEY" not in result.output
