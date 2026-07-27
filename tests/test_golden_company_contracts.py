from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest


GOLDEN_PATH = Path(__file__).parent / "golden" / "companies.yaml"
EXPECTED_IDS = {
    "samsung_five_year_investor",
    "sk_hynix_group_qsc",
    "daewon_five_year_dcf",
    "modified_opinion",
    "multiple_kam",
    "incomplete_company",
}


def _load_cases() -> list[dict]:
    payload = json.loads(GOLDEN_PATH.read_text())
    assert payload["contract_version"] == "1.0"
    return payload["cases"]


def _fingerprint(path: Path) -> tuple:
    values = []
    for candidate in (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    ):
        if not candidate.exists():
            values.append((candidate.name, None))
            continue
        stat = candidate.stat()
        values.append(
            (
                candidate.name,
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(candidate.read_bytes()).hexdigest(),
            )
        )
    return tuple(values)


def test_six_declarative_golden_cases_cover_stable_semantic_boundaries():
    cases = _load_cases()
    by_id = {case["id"]: case for case in cases}

    assert set(by_id) == EXPECTED_IDS
    assert by_id["samsung_five_year_investor"]["years"] == 5
    assert by_id["daewon_five_year_dcf"]["years"] == 5
    assert "qsc" in by_id["sk_hynix_group_qsc"]["packs"]
    assert "opinion" in by_id["modified_opinion"]["required_shapes"]
    assert "items" in by_id["multiple_kam"]["required_shapes"]
    assert (
        "missing is never promoted to usable"
        in by_id["incomplete_company"]["stable_semantics"]
    )
    for case in cases:
        assert case["required_shapes"]
        assert case["stable_semantics"]
        assert case["provenance"] in {
            "dart_or_explicit_source_access_limitation",
            "explicit_source_access_limitation",
        }
        assert all("amount" not in field for field in case["required_shapes"])


def test_live_regression_is_opt_in_by_default():
    if os.environ.get("KREPORTS_RUN_LIVE_DB_TESTS") == "1":
        pytest.skip("live regression is exercised by the opt-in test")
    pytest.skip(
        "set KREPORTS_RUN_LIVE_DB_TESTS=1 for immutable local DB regression"
    )


@pytest.mark.skipif(
    os.environ.get("KREPORTS_RUN_LIVE_DB_TESTS") != "1",
    reason="live DB regression is explicit opt-in",
)
def test_live_golden_company_shapes_are_read_immutably_without_dart_calls():
    from kreports.config import settings

    prefix = "sqlite:///"
    assert settings.db_url.startswith(prefix)
    db_path = Path(settings.db_url.removeprefix(prefix)).resolve(strict=True)
    before = _fingerprint(db_path)
    connection = sqlite3.connect(
        f"{db_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT stock_code, corp_name FROM companies "
            "WHERE stock_code IN ('005930', '000660', '003220') "
            "ORDER BY stock_code"
        ).fetchall()
        assert {row[0] for row in rows} == {"005930", "000660", "003220"}
        assert connection.execute(
            "SELECT COUNT(*) FROM financials "
            "WHERE corp_code IN ("
            "SELECT corp_code FROM companies "
            "WHERE stock_code IN ('005930', '000660', '003220'))"
        ).fetchone()[0] > 0
        assert connection.execute(
            "SELECT COUNT(*) FROM company_year_quality "
            "WHERE corp_code IN ("
            "SELECT corp_code FROM companies "
            "WHERE stock_code IN ('005930', '000660', '003220'))"
        ).fetchone()[0] > 0
    finally:
        connection.close()
    assert _fingerprint(db_path) == before
