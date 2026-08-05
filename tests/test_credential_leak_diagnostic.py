import json
import sqlite3

import pytest
from typer.testing import CliRunner

from kreports.cli.main import app
from kreports.maintenance.credential_leak_diagnostic import (
    diagnose_credential_leaks,
)
from kreports.security import redact_sensitive_text


@pytest.mark.parametrize(
    "credential_text, secret",
    [
        ("upstream rejected Bearer bearer-secret", "bearer-secret"),
        ("request failed at https://alice:password-secret@example.invalid/api", "password-secret"),
        ("request failed at https://example.invalid/api?crtfc_key%3Dencoded-secret", "encoded-secret"),
    ],
)
def test_redact_sensitive_text_removes_realistic_credential_forms(credential_text, secret):
    """Catches redaction that misses bearer, URL-userinfo, or encoded query secrets."""
    redacted = redact_sensitive_text(credential_text)

    assert redacted == "external error details redacted"
    assert secret not in redacted


def test_credential_leak_diagnostic_reports_locations_without_returning_secret(tmp_path):
    """Catches a cleanup diagnostic that leaks the very credential it finds."""
    db_path = tmp_path / "audit.db"
    secret = "server-secret"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE fetch_log (error_msg TEXT)")
        connection.execute(
            "INSERT INTO fetch_log(error_msg) VALUES (?)",
            (f"https://example.invalid/api?crtfc_key={secret}",),
        )
    before = db_path.stat()

    report = diagnose_credential_leaks(db_path)

    assert report == {
        "schema": "credential_leak_diagnostic_v1",
        "finding_count": 1,
        "findings": [{"table": "fetch_log", "column": "error_msg", "rowid": 1}],
        "findings_omitted_count": 0,
    }
    assert secret not in json.dumps(report)
    after = db_path.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )


@pytest.mark.parametrize(
    "credential_text",
    [
        "upstream rejected Bearer bearer-secret",
        "https://alice:password-secret@example.invalid/api",
        "https://example.invalid/api?crtfc_key%3Dencoded-secret",
    ],
)
def test_credential_leak_diagnostic_finds_realistic_credential_forms(tmp_path, credential_text):
    """Catches a read-only diagnostic silently skipping non-equals credential formats."""
    db_path = tmp_path / "audit.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE fetch_log (error_msg TEXT)")
        connection.execute(
            "INSERT INTO fetch_log(error_msg) VALUES (?)",
            (credential_text,),
        )

    report = diagnose_credential_leaks(db_path)

    assert report["finding_count"] == 1
    assert report["findings"] == [{"table": "fetch_log", "column": "error_msg", "rowid": 1}]
    assert "bearer-secret" not in json.dumps(report)
    assert "password-secret" not in json.dumps(report)
    assert "encoded-secret" not in json.dumps(report)


def test_credential_leak_diagnostic_cli_emits_safe_json(tmp_path):
    """Catches a diagnostic CLI that emits matching credential text."""
    db_path = tmp_path / "audit.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE fetch_log (error_msg TEXT)")
        connection.execute(
            "INSERT INTO fetch_log(error_msg) VALUES (?)",
            ("https://example.invalid/api?crtfc_key=server-secret",),
        )

    result = CliRunner().invoke(app, ["diagnose-credential-leaks", "--db", str(db_path)])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["finding_count"] == 1
    assert "server-secret" not in result.output
