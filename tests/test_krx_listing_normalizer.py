"""Read-only normalization of KIND's EUC-KR HTML-XLS listing export."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner


def _kind_xls(rows: list[tuple[str, str, str, str]]) -> bytes:
    """A small faithful HTML-XLS shape emitted by KIND, encoded as EUC-KR."""
    body = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        "<html><head><meta charset='euc-kr'></head><body><table>"
        "<tr><th>회사명</th><th>종목코드</th><th>시장구분</th><th>상장일</th></tr>"
        f"{body}</table></body></html>"
    ).encode("euc-kr")


def _companies() -> list[dict[str, str]]:
    return [
        {"corp_code": "1", "stock_code": "1", "market": "KOSPI"},
        {"corp_code": "00000002", "stock_code": "000002", "market": "KOSDAQ"},
        {"corp_code": "00000003", "stock_code": "000003", "market": "KOSPI"},
    ]


def test_normalizes_realistic_euc_kr_html_xls_with_complete_current_core_population():
    from kreports.maintenance.krx_listing_normalizer import normalize_krx_listing_bytes

    raw = _kind_xls([
        ("가나다", "000001", "유가", "2001.01.02"),
        ("라마바", "000002", "코스닥", "2002/02/03"),
        ("사아자", "000004", "코넥스", "2003-03-04"),
    ])
    result = normalize_krx_listing_bytes(raw, _companies(), as_of=date(2026, 8, 5))

    assert result.csv_bytes == (
        b"corp_code,stock_code,market,listed_from,listed_to,status\n"
        b"00000001,000001,KOSPI,2001-01-02,,verified\n"
        b"00000002,000002,KOSDAQ,2002-02-03,,verified\n"
        b"00000003,000003,KOSPI,,,unknown\n"
    )
    assert result.summary == {
        "raw_checksum": hashlib.sha256(raw).hexdigest(),
        "normalized_checksum": hashlib.sha256(result.csv_bytes).hexdigest(),
        "row_count": 3,
        "status_counts": {"conflict": 0, "unknown": 1, "verified": 2},
        "transformation_version": "krx-listing-normalize-v1",
        "as_of": "2026-08-05",
        "unmatched_krx_stock_codes": ["000004"],
    }


def test_exact_duplicates_dedupe_but_conflicting_duplicates_and_market_mismatch_fail_closed():
    from kreports.maintenance.krx_listing_normalizer import normalize_krx_listing_bytes

    raw = _kind_xls([
        ("가나다", "000001", "유가", "2001-01-02"),
        ("가나다", "000001", "유가", "2001-01-02"),
        ("라마바", "000002", "코스닥", "2002-02-03"),
        ("라마바", "000002", "코스닥", "2002-02-04"),
        ("사아자", "000003", "코스닥", "2003-03-04"),
    ])
    rows = normalize_krx_listing_bytes(raw, _companies(), as_of=date(2026, 8, 5)).rows

    assert rows == [
        {"corp_code": "00000001", "stock_code": "000001", "market": "KOSPI", "listed_from": "2001-01-02", "listed_to": "", "status": "verified"},
        {"corp_code": "00000002", "stock_code": "000002", "market": "KOSDAQ", "listed_from": "", "listed_to": "", "status": "conflict"},
        {"corp_code": "00000003", "stock_code": "000003", "market": "KOSPI", "listed_from": "", "listed_to": "", "status": "conflict"},
    ]


def test_rejects_unreadable_or_malformed_kind_input_and_future_listing_date():
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        normalize_krx_listing_bytes,
    )

    with pytest.raises(KrxListingNormalizationError, match="KIND HTML-XLS must be EUC-KR"):
        normalize_krx_listing_bytes(b"\xff\xfe", _companies(), as_of=date(2026, 8, 5))
    with pytest.raises(KrxListingNormalizationError, match="required KIND columns"):
        normalize_krx_listing_bytes(
            "<table><tr><th>회사명</th></tr><tr><td>가</td></tr></table>".encode("euc-kr"),
            _companies(),
            as_of=date(2026, 8, 5),
        )
    with pytest.raises(KrxListingNormalizationError, match="listed_from is after as_of"):
        normalize_krx_listing_bytes(
            _kind_xls([("가나다", "000001", "유가", "2026-08-06")]),
            _companies(),
            as_of=date(2026, 8, 5),
        )


def test_normalization_bytes_are_deterministic_for_input_and_company_order():
    from kreports.maintenance.krx_listing_normalizer import normalize_krx_listing_bytes

    raw = _kind_xls([
        ("라마바", "000002", "코스닥", "2002-02-03"),
        ("가나다", "000001", "유가", "2001-01-02"),
    ])
    first = normalize_krx_listing_bytes(raw, _companies(), as_of=date(2026, 8, 5))
    second = normalize_krx_listing_bytes(raw, list(reversed(_companies())), as_of=date(2026, 8, 5))

    assert first.csv_bytes == second.csv_bytes
    assert first.summary == second.summary


def test_reads_explicit_sqlite_snapshot_without_mutating_it(tmp_path: Path):
    from kreports.maintenance.krx_listing_normalizer import read_current_core_companies

    database = tmp_path / "companies.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE companies (corp_code TEXT, stock_code TEXT, market TEXT)")
    connection.executemany(
        "INSERT INTO companies VALUES (?, ?, ?)",
        [("00000002", "2", "KOSDAQ"), ("00000001", "1", "KOSPI"), ("00000009", "9", "KONEX")],
    )
    connection.commit()
    connection.close()
    before = database.read_bytes()

    assert read_current_core_companies(database) == [
        {"corp_code": "00000001", "stock_code": "000001", "market": "KOSPI"},
        {"corp_code": "00000002", "stock_code": "000002", "market": "KOSDAQ"},
    ]
    assert database.read_bytes() == before


def test_safe_output_writer_rejects_preexisting_file_and_leaves_no_partial_output(tmp_path: Path, monkeypatch):
    from kreports.maintenance import krx_listing_normalizer as normalizer

    output = tmp_path / "normalized.csv"
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError, match="output path already exists"):
        normalizer.write_normalized_listing_csv(output, b"replacement")
    assert output.read_bytes() == b"existing"

    failed_output = tmp_path / "failed.csv"
    monkeypatch.setattr(normalizer.os, "link", lambda *_args: (_ for _ in ()).throw(OSError("link failed")))
    with pytest.raises(OSError, match="link failed"):
        normalizer.write_normalized_listing_csv(failed_output, b"complete payload")
    assert not failed_output.exists()
    assert list(tmp_path.glob(".failed.csv.*.tmp")) == []


def test_cli_normalizes_from_explicit_raw_and_db_paths_and_prints_json(tmp_path: Path):
    from kreports.cli.main import app

    database = tmp_path / "companies.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE companies (corp_code TEXT, stock_code TEXT, market TEXT)")
    connection.execute("INSERT INTO companies VALUES ('00000001', '000001', 'KOSPI')")
    connection.commit()
    connection.close()
    raw_path = tmp_path / "kind.xls"
    raw_path.write_bytes(_kind_xls([("가나다", "000001", "유가", "2001-01-02")]))
    output = tmp_path / "normalized.csv"

    result = CliRunner().invoke(app, [
        "normalize-krx-listing", "--raw-path", str(raw_path), "--db-path", str(database),
        "--output-path", str(output), "--as-of", "2026-08-05",
    ])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["row_count"] == 1
    assert summary["output_path"] == str(output)
    assert output.read_text(encoding="utf-8").endswith("00000001,000001,KOSPI,2001-01-02,,verified\n")
