"""Read-only normalization of KIND's EUC-KR HTML-XLS listing export."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from typer.testing import CliRunner


def _kind_xls(
    rows: list[tuple[str, str, str, str]],
    *,
    meta_tags: str = "<meta charset='euc-kr'>",
    encoding: str = "euc-kr",
) -> bytes:
    """A small faithful HTML-XLS shape emitted by KIND, encoded as EUC-KR."""
    body = "".join(
        "<tr>" + "".join(f"<td>{value}</td>" for value in row) + "</tr>"
        for row in rows
    )
    return (
        f"<html><head>{meta_tags}</head><body><table>"
        "<tr><th>회사명</th><th>종목코드</th><th>시장구분</th><th>상장일</th></tr>"
        f"{body}</table></body></html>"
    ).encode(encoding)


def _company_snapshot_checksum() -> str:
    return hashlib.sha256(
        b"00000001,000001,KOSPI\n"
        b"00000002,000002,KOSDAQ\n"
        b"00000003,000003,KOSPI\n"
    ).hexdigest()


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
        "raw_size_bytes": len(raw),
        "normalized_checksum": hashlib.sha256(result.csv_bytes).hexdigest(),
        "normalized_size_bytes": len(result.csv_bytes),
        "row_count": 3,
        "status_counts": {"conflict": 0, "unknown": 1, "verified": 2},
        "transformation_version": "krx-listing-normalize-v1",
        "as_of": "2026-08-05",
        "source_row_count": 3,
        "source_unique_row_count": 3,
        "exact_duplicate_row_count": 0,
        "exact_duplicate_group_count": 0,
        "conflicting_source_stock_code_count": 0,
        "current_company_count": 3,
        "current_company_snapshot_checksum": _company_snapshot_checksum(),
        "unmatched_krx_stock_codes": ["000004"],
        "unmatched_krx_stock_code_count": 1,
    }


def test_exact_duplicates_dedupe_but_conflicting_duplicates_and_market_mismatch_fail_closed():
    from kreports.maintenance.krx_listing_normalizer import normalize_krx_listing_bytes

    raw = _kind_xls([
        ("가나다", "000001", "유가", "2001-01-02"),
        ("가나다", "000001", "유가", "2001-01-02"),
        ("라마바", "000002", "코스닥", "2002-02-03"),
        ("다른법인", "000002", "코스닥", "2002-02-03"),
        ("사아자", "000003", "코스닥", "2003-03-04"),
    ])
    result = normalize_krx_listing_bytes(raw, _companies(), as_of=date(2026, 8, 5))
    rows = result.rows

    assert rows == [
        {"corp_code": "00000001", "stock_code": "000001", "market": "KOSPI", "listed_from": "2001-01-02", "listed_to": "", "status": "verified"},
        {"corp_code": "00000002", "stock_code": "000002", "market": "KOSDAQ", "listed_from": "", "listed_to": "", "status": "conflict"},
        {"corp_code": "00000003", "stock_code": "000003", "market": "KOSPI", "listed_from": "", "listed_to": "", "status": "conflict"},
    ]
    assert {key: result.summary[key] for key in (
        "source_row_count", "source_unique_row_count", "exact_duplicate_row_count",
        "exact_duplicate_group_count", "conflicting_source_stock_code_count",
    )} == {
        "source_row_count": 5,
        "source_unique_row_count": 4,
        "exact_duplicate_row_count": 1,
        "exact_duplicate_group_count": 1,
        "conflicting_source_stock_code_count": 1,
    }


def test_alphanumeric_six_character_stock_codes_are_canonicalized_and_preserved():
    from kreports.maintenance.krx_listing_normalizer import normalize_krx_listing_bytes

    result = normalize_krx_listing_bytes(
        _kind_xls([("알파", "0010v0", "유가", "2024-01-02")]),
        [{"corp_code": "00000010", "stock_code": "0010V0", "market": "KOSPI"}],
        as_of=date(2026, 8, 5),
    )

    assert result.rows == [{
        "corp_code": "00000010", "stock_code": "0010V0", "market": "KOSPI",
        "listed_from": "2024-01-02", "listed_to": "", "status": "verified",
    }]


def test_company_name_is_required_and_blank_or_name_conflicting_source_rows_fail_closed():
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        normalize_krx_listing_bytes,
    )

    with pytest.raises(KrxListingNormalizationError, match="company_name is blank"):
        normalize_krx_listing_bytes(
            _kind_xls([("", "000001", "유가", "2001-01-02")]),
            _companies(),
            as_of=date(2026, 8, 5),
        )
    with pytest.raises(KrxListingNormalizationError, match="required KIND columns"):
        normalize_krx_listing_bytes(
            "<html><head><meta charset='euc-kr'></head><body><table>"
            "<tr><th>종목코드</th><th>시장구분</th><th>상장일</th></tr>"
            "<tr><td>000001</td><td>유가</td><td>2001-01-02</td></tr>"
            "</table></body></html>".encode("euc-kr"),
            _companies(),
            as_of=date(2026, 8, 5),
        )


def test_duplicate_nonblank_company_master_stock_code_is_rejected_before_output():
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        normalize_krx_listing_bytes,
    )

    with pytest.raises(KrxListingNormalizationError, match="duplicate stock_code in company records"):
        normalize_krx_listing_bytes(
            _kind_xls([("가나다", "0010V0", "유가", "2024-01-02")]),
            [
                {"corp_code": "00000010", "stock_code": "0010V0", "market": "KOSPI"},
                {"corp_code": "00000011", "stock_code": "0010V0", "market": "KOSPI"},
            ],
            as_of=date(2026, 8, 5),
        )


def test_rejects_unreadable_or_malformed_kind_input_and_future_listing_date():
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        normalize_krx_listing_bytes,
    )

    with pytest.raises(KrxListingNormalizationError, match="KIND HTML-XLS must be raw EUC-KR"):
        normalize_krx_listing_bytes(b"\xff\xfe", _companies(), as_of=date(2026, 8, 5))
    with pytest.raises(KrxListingNormalizationError, match="required KIND columns"):
        normalize_krx_listing_bytes(
            "<html><head><meta charset='euc-kr'></head><body><table>"
            "<tr><th>회사명</th></tr><tr><td>가</td></tr>"
            "</table></body></html>".encode("euc-kr"),
            _companies(),
            as_of=date(2026, 8, 5),
        )
    with pytest.raises(KrxListingNormalizationError, match="listed_from is after as_of"):
        normalize_krx_listing_bytes(
            _kind_xls([("가나다", "000001", "유가", "2026-08-06")]),
            _companies(),
            as_of=date(2026, 8, 5),
        )


def test_requires_a_single_euc_kr_meta_charset_and_rejects_bom_or_ascii_spoofing():
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        normalize_krx_listing_bytes,
    )

    base_rows = [("가나다", "000001", "유가", "2001-01-02")]
    invalid_meta_cases = [
        _kind_xls(base_rows, meta_tags=""),
        _kind_xls(base_rows, meta_tags="<meta charset='utf-8'>"),
        _kind_xls(base_rows, meta_tags="<meta charset='euc-kr'><meta charset='utf-8'>"),
    ]
    for raw in invalid_meta_cases:
        with pytest.raises(KrxListingNormalizationError, match="EUC-KR meta charset"):
            normalize_krx_listing_bytes(raw, _companies(), as_of=date(2026, 8, 5))
    document = _kind_xls(base_rows).decode("euc-kr")
    for raw in (
        b"\xef\xbb\xbf" + document.encode("utf-8"),
        document.encode("utf-16"),
        b"<html><head><meta charset='euc-kr'></head><body>ASCII only</body></html>",
    ):
        with pytest.raises(KrxListingNormalizationError, match="raw EUC-KR|ASCII-only"):
            normalize_krx_listing_bytes(raw, _companies(), as_of=date(2026, 8, 5))


@pytest.mark.parametrize("replacement", [
    "<td colspan='2'>가나다</td><td>000001</td><td>유가</td><td>2001-01-02</td>",
    "<td>가나다<td>중첩</td></td><td>000001</td><td>유가</td><td>2001-01-02</td>",
    "<td><table><tr><td>가나다</td></tr></table></td><td>000001</td><td>유가</td><td>2001-01-02</td>",
    "<td>가나다<tr><td>중첩</td></tr></td><td>000001</td><td>유가</td><td>2001-01-02</td>",
])
def test_rejects_ambiguous_table_layouts(replacement: str):
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        normalize_krx_listing_bytes,
    )

    raw = _kind_xls([("가나다", "000001", "유가", "2001-01-02")]).decode("euc-kr")
    raw = raw.replace("<td>가나다</td><td>000001</td><td>유가</td><td>2001-01-02</td>", replacement).encode("euc-kr")
    with pytest.raises(KrxListingNormalizationError, match="unsupported table structure"):
        normalize_krx_listing_bytes(raw, _companies(), as_of=date(2026, 8, 5))


def test_rejects_row_shape_and_prohibited_character_content():
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        normalize_krx_listing_bytes,
    )

    short_row = _kind_xls([("가나다", "000001", "유가", "2001-01-02")]).replace(
        b"<td>2001-01-02</td>", b"",
    )
    long_row = _kind_xls([("가나다", "000001", "유가", "2001-01-02")]).replace(
        b"</tr></table>", b"<td>extra</td></tr></table>", 1,
    )
    prohibited = _kind_xls([("가나다", "000001", "유가", "2001-01-02")]).replace(
        b"</body>", b"\x01</body>", 1,
    )
    zero_width_entity = _kind_xls([("가나다&#x200B;", "000001", "유가", "2001-01-02")])
    for raw, error in (
        (short_row, "does not match header length"),
        (long_row, "does not match header length"),
        (prohibited, "prohibited control"),
        (zero_width_entity, "prohibited zero-width"),
    ):
        with pytest.raises(KrxListingNormalizationError, match=error):
            normalize_krx_listing_bytes(raw, _companies(), as_of=date(2026, 8, 5))


def test_enforces_raw_row_cell_and_text_size_bounds_while_accepting_2802_krx_sized_rows():
    from kreports.maintenance import krx_listing_normalizer as normalizer

    with pytest.raises(normalizer.KrxListingNormalizationError, match="raw KIND payload exceeds maximum size"):
        normalizer.normalize_krx_listing_bytes(
            b"x" * (normalizer.MAX_RAW_SIZE_BYTES + 1), _companies(), as_of=date(2026, 8, 5),
        )
    oversized_name = "가" * (normalizer.MAX_CELL_TEXT_CHARS + 1)
    with pytest.raises(normalizer.KrxListingNormalizationError, match="cell text exceeds maximum length"):
        normalizer.normalize_krx_listing_bytes(
            _kind_xls([(oversized_name, "000001", "유가", "2001-01-02")]),
            _companies(),
            as_of=date(2026, 8, 5),
        )
    with pytest.raises(normalizer.KrxListingNormalizationError, match="cell text exceeds maximum length"):
        normalizer.normalize_krx_listing_bytes(
            _kind_xls([("가" + "<br>" * normalizer.MAX_CELL_TEXT_CHARS, "000001", "유가", "2001-01-02")]),
            _companies(),
            as_of=date(2026, 8, 5),
        )
    cells = "<td>가</td>" + "".join("<td>x</td>" for _ in range(normalizer.MAX_CELLS_PER_ROW))
    too_many_cells = (
        "<html><head><meta charset='euc-kr'></head><body><table><tr>"
        f"{cells}</tr></table></body></html>"
    ).encode("euc-kr")
    with pytest.raises(normalizer.KrxListingNormalizationError, match="row exceeds maximum cell count"):
        normalizer.normalize_krx_listing_bytes(too_many_cells, _companies(), as_of=date(2026, 8, 5))

    krx_sized_rows = [("가나다", f"{number:06d}", "유가", "2001-01-02") for number in range(1, 2803)]
    result = normalizer.normalize_krx_listing_bytes(
        _kind_xls(krx_sized_rows),
        [{"corp_code": "00000001", "stock_code": "000001", "market": "KOSPI"}],
        as_of=date(2026, 8, 5),
    )
    assert result.summary["source_row_count"] == 2802
    assert result.rows[0]["status"] == "verified"


def test_enforces_table_row_bound(monkeypatch):
    from kreports.maintenance import krx_listing_normalizer as normalizer

    monkeypatch.setattr(normalizer, "MAX_TABLE_ROWS", 2)
    with pytest.raises(normalizer.KrxListingNormalizationError, match="table exceeds maximum row count"):
        normalizer.normalize_krx_listing_bytes(
            _kind_xls([
                ("가나다", "000001", "유가", "2001-01-02"),
                ("라마바", "000002", "코스닥", "2002-02-03"),
            ]),
            _companies(),
            as_of=date(2026, 8, 5),
        )


def test_source_row_order_does_not_change_csv_checksum_or_duplicate_summary():
    from kreports.maintenance.krx_listing_normalizer import normalize_krx_listing_bytes

    rows = [
        ("가나다", "000001", "유가", "2001-01-02"),
        ("가나다", "000001", "유가", "2001-01-02"),
        ("라마바", "000002", "코스닥", "2002-02-03"),
        ("다른법인", "000002", "코스닥", "2002-02-03"),
    ]
    first = normalize_krx_listing_bytes(_kind_xls(rows), _companies(), as_of=date(2026, 8, 5))
    second = normalize_krx_listing_bytes(_kind_xls(list(reversed(rows))), _companies(), as_of=date(2026, 8, 5))

    assert first.csv_bytes == second.csv_bytes
    assert first.summary["normalized_checksum"] == second.summary["normalized_checksum"]
    for key in (
        "source_row_count", "source_unique_row_count", "exact_duplicate_row_count",
        "exact_duplicate_group_count", "conflicting_source_stock_code_count",
    ):
        assert first.summary[key] == second.summary[key]


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


def test_reads_explicit_sqlite_snapshot_without_mutating_it_and_fails_closed_for_bad_stock_codes(tmp_path: Path):
    from kreports.maintenance.krx_listing_normalizer import (
        KrxListingNormalizationError,
        read_current_core_companies,
    )

    database = tmp_path / "companies.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE companies (corp_code TEXT, stock_code TEXT, market TEXT)")
    connection.executemany(
        "INSERT INTO companies VALUES (?, ?, ?)",
        [
            ("00000002", "2", "KOSDAQ"),
            ("00000001", "1", "KOSPI"),
            ("00000003", None, "KOSPI"),
            ("00000009", "9", "KONEX"),
        ],
    )
    connection.commit()
    connection.close()
    before = database.read_bytes()

    assert read_current_core_companies(database) == [
        {"corp_code": "00000001", "stock_code": "000001", "market": "KOSPI"},
        {"corp_code": "00000002", "stock_code": "000002", "market": "KOSDAQ"},
    ]
    assert database.read_bytes() == before

    invalid_database = tmp_path / "invalid-companies.sqlite"
    invalid_connection = sqlite3.connect(invalid_database)
    invalid_connection.execute("CREATE TABLE companies (corp_code TEXT, stock_code TEXT, market TEXT)")
    invalid_connection.execute("INSERT INTO companies VALUES ('00000004', '', 'KOSPI')")
    invalid_connection.commit()
    invalid_connection.close()
    with pytest.raises(KrxListingNormalizationError, match="stock_code must be a 6-character uppercase alphanumeric code"):
        read_current_core_companies(invalid_database)


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
    connection.execute("INSERT INTO companies VALUES ('00000001', '0010V0', 'KOSPI')")
    connection.commit()
    connection.close()
    raw_path = tmp_path / "kind.xls"
    raw_path.write_bytes(_kind_xls([("가나다", "0010V0", "유가", "2001-01-02")]))
    output = tmp_path / "normalized.csv"

    result = CliRunner().invoke(app, [
        "normalize-krx-listing", "--raw-path", str(raw_path), "--db-path", str(database),
        "--output-path", str(output), "--as-of", "2026-08-05",
    ])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["row_count"] == 1
    assert summary["output_path"] == str(output)
    assert output.read_text(encoding="utf-8").endswith("00000001,0010V0,KOSPI,2001-01-02,,verified\n")
