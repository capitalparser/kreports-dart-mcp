"""End-to-end retained-clone rehearsal contract using a small legacy SQLite DB."""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIN_FREE_BYTES = 10 * 1024**3


def _sha256_file(path: Path) -> str:
    from kreports.maintenance.rehearsal_safety import sha256_file

    return sha256_file(path)


@pytest.fixture
def legacy_kam_source(tmp_path: Path) -> Path:
    """Create revision-04 source evidence with legacy fee/procedure schemas."""
    from sqlalchemy import create_engine

    from kreports.db.migrations import MIGRATIONS, _checksum
    from kreports.db.models import Base

    source = tmp_path / "source" / "legacy-kam.db"
    source.parent.mkdir()
    engine = create_engine(f"sqlite:///{source}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            DROP TABLE kam_items;
            DROP TABLE audit_procedure_items;
            DROP TABLE audit_fees;
            DROP TABLE audit_fee_observations;
            DROP TABLE financial_facts_compact;
            DROP TABLE company_year_quality;
            DROP TABLE group_component_metrics;
            DROP TABLE group_relationships;
            DROP TABLE group_entities;
            CREATE TABLE audit_procedure_items (
              id INTEGER PRIMARY KEY,
              rcept_no TEXT NOT NULL,
              dcm_no TEXT,
              corp_code TEXT NOT NULL,
              bsns_year INTEGER NOT NULL,
              source_type TEXT NOT NULL,
              kam_topic TEXT,
              procedure_type TEXT NOT NULL,
              procedure_text TEXT NOT NULL,
              procedure_hash TEXT,
              procedure_length INTEGER,
              section_ordinal INTEGER NOT NULL DEFAULT 0,
              procedure_ordinal INTEGER NOT NULL DEFAULT 0,
              fetched_at TEXT NOT NULL,
              CONSTRAINT uq_audit_procedure_item
                UNIQUE (rcept_no, source_type, section_ordinal, procedure_ordinal)
            );
            CREATE TABLE audit_fees (
              id INTEGER PRIMARY KEY,
              corp_code TEXT NOT NULL,
              bsns_year INTEGER NOT NULL,
              auditor_nm TEXT,
              audit_fee_m INTEGER,
              audit_hours INTEGER,
              non_audit_fee_m INTEGER,
              non_audit_hours INTEGER,
              nas_ratio REAL,
              independence_risk_flag INTEGER,
              fetched_at TEXT NOT NULL
            );
            CREATE TABLE financial_facts_compact (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              fs_div VARCHAR(3) NOT NULL,
              metric_key VARCHAR(50) NOT NULL,
              metric_name VARCHAR(200) NOT NULL,
              amount BIGINT,
              source_account_id VARCHAR(200),
              source_account_nm VARCHAR(300),
              fetched_at DATETIME NOT NULL,
              CONSTRAINT uq_financial_facts_compact
                UNIQUE (corp_code, bsns_year, fs_div, metric_key)
            );
            CREATE INDEX idx_fin_compact_corp_year
              ON financial_facts_compact (corp_code, bsns_year);
            CREATE INDEX idx_fin_compact_metric
              ON financial_facts_compact (metric_key);
            CREATE TABLE company_year_quality (
              corp_code VARCHAR(8) NOT NULL,
              bsns_year SMALLINT NOT NULL,
              market VARCHAR(10),
              financial_core_status VARCHAR(24) NOT NULL,
              auditor_status VARCHAR(24) NOT NULL,
              audit_fee_status VARCHAR(24) NOT NULL,
              policy_status VARCHAR(24) NOT NULL,
              kam_status VARCHAR(24) NOT NULL,
              audit_procedure_status VARCHAR(24) NOT NULL,
              group_audit_status VARCHAR(24) NOT NULL,
              investor_grade VARCHAR(1) NOT NULL,
              auditor_grade VARCHAR(1) NOT NULL,
              group_audit_grade VARCHAR(1) NOT NULL,
              blockers_json TEXT NOT NULL DEFAULT '[]',
              quality_version VARCHAR(20) NOT NULL DEFAULT 'v1',
              updated_at DATETIME NOT NULL,
              PRIMARY KEY (corp_code, bsns_year)
            );
            CREATE INDEX idx_company_year_quality_year_market
              ON company_year_quality (bsns_year, market);
            CREATE TABLE IF NOT EXISTS schema_migrations (
              revision TEXT PRIMARY KEY,
              checksum TEXT NOT NULL,
              description TEXT NOT NULL,
              applied_at TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
            [
                (
                    migration.revision,
                    _checksum(migration),
                    migration.description,
                    "2026-07-29T00:00:00Z",
                )
                for migration in MIGRATIONS[:4]
            ],
        )
        connection.execute(
            "INSERT INTO companies (corp_code, stock_code, corp_name, market, "
            "updated_at) VALUES (?, ?, ?, ?, ?)",
            ("00126380", "005930", "삼성전자", "KOSPI", "2026-03-10 00:00:00"),
        )
        connection.execute(
            """
            INSERT INTO source_documents (
              rcept_no, dcm_no, corp_code, bsns_year, source_type, report_nm,
              content_type, raw_content, doc_hash, storage_status, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "20260310000001",
                "1",
                "00126380",
                2025,
                "audit_report",
                "감사보고서",
                "xml",
                (
                    "핵심감사사항\n수익인식\n핵심감사사항으로 선정한 이유\n"
                    "추정\n감사에서 다루어진 방법\n계약서를 검사하였습니다."
                ),
                "a" * 40,
                "inline",
                "2026-03-10 00:00:00",
            ),
        )
        connection.execute(
            "INSERT INTO audit_fees (corp_code, bsns_year, auditor_nm, "
            "audit_fee_m, audit_hours, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("00126380", 2025, "삼일회계법인", 100, 1000, "2026-03-10 00:00:00"),
        )
        connection.execute(
            """
            INSERT INTO disclosures (
              rcept_no, corp_code, corp_name, disc_date, disc_type,
              report_nm, flr_nm, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "20260310000001",
                "00126380",
                "삼성전자",
                "2026-03-10",
                "A",
                "사업보고서 (2025.12)",
                "삼성전자",
                "2026-03-10 00:00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO financial_facts (
              corp_code, bsns_year, reprt_code, fs_div, sj_div,
              account_id, account_nm, ord, thstrm_amount, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "00126380",
                2025,
                "11011",
                "CFS",
                "IS",
                "ifrs-full_Revenue",
                "매출액",
                1,
                100_000_000,
                "2026-03-10 00:00:00",
            ),
        )
        audit_fee_json = json.dumps(
            [{
                "corp_code": "00126380",
                "bsns_year": 2025,
                "source_class": "cached_business_report",
                "contract_fee_m": None,
                "contract_hours": None,
                "actual_fee_m": 100,
                "actual_hours": 1_000,
                "auditor_nm": "삼일회계법인",
                "source_rcept_no": "20260310000002",
                "source_period": "2025",
                "availability_status": "available",
                "quality_status": "verified",
                "displayed_unit": "백만원",
                "raw_values": {"fee": "100", "hours": "1000"},
                "source_status": "local_fixture",
                "source_message": None,
                "source_eligibility": "eligible",
                "limitations": [],
                "parser_version": "v1",
            }],
            ensure_ascii=False,
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO source_documents (
              rcept_no, dcm_no, corp_code, bsns_year, source_type, report_nm,
              content_type, raw_content, doc_hash, storage_status, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "20260310000002",
                "2",
                "00126380",
                2025,
                "business_report",
                "unpromoted audit-fee JSON fixture input",
                "json",
                audit_fee_json,
                "b" * 40,
                "inline",
                "2026-03-10 00:00:00",
            ),
        )
    return source


@pytest.fixture
def apfs_rehearsal_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Keep the 10 GiB production floor while simulating only disk capacity."""
    from kreports.maintenance import rehearsal_safety

    rehearsal_dir = tmp_path / "rehearsal"
    rehearsal_dir.mkdir()
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(
            2 * MIN_FREE_BYTES,
            MIN_FREE_BYTES,
            MIN_FREE_BYTES,
        ),
    )
    return rehearsal_dir


@pytest.fixture
def file_backed_rehearsal_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Exercise real workers on a copied test DB without invoking APFS clonefile."""
    from kreports.maintenance import rehearsal_safety

    rehearsal_dir = tmp_path / "file-backed-rehearsal"
    rehearsal_dir.mkdir()
    monkeypatch.setattr(
        rehearsal_safety.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(
            2 * MIN_FREE_BYTES,
            MIN_FREE_BYTES,
            MIN_FREE_BYTES,
        ),
    )

    def preflight(
        source_db: Path,
        target_dir: Path,
        *,
        repository_root: Path,
        min_free_bytes: int,
    ) -> rehearsal_safety.SourcePreflight:
        assert repository_root == PROJECT_ROOT
        source = rehearsal_safety.inspect_source_database(source_db)
        assert target_dir == rehearsal_dir
        assert min_free_bytes == MIN_FREE_BYTES
        return rehearsal_safety.SourcePreflight(
            source=source,
            rehearsal_dir=target_dir.resolve(strict=True),
            free_bytes=MIN_FREE_BYTES,
            filesystem_type="apfs",
        )

    def copy_clone(
        preflight_result: rehearsal_safety.SourcePreflight,
    ) -> rehearsal_safety.FileIdentity:
        target = preflight_result.rehearsal_dir / "kreports-rehearsal.db"
        shutil.copyfile(preflight_result.source.path, target)
        metadata = target.stat()
        return rehearsal_safety.FileIdentity(
            path=target.resolve(),
            size=metadata.st_size,
            inode=metadata.st_ino,
            device=metadata.st_dev,
            mtime_ns=metadata.st_mtime_ns,
            sha256=_sha256_file(target),
        )

    monkeypatch.setattr(rehearsal_safety, "preflight_rehearsal", preflight)
    monkeypatch.setattr(rehearsal_safety, "create_apfs_clone", copy_clone)
    return rehearsal_dir


def test_file_backed_db_evidence_rehearsal_reaches_revision_11(
    legacy_kam_source: Path,
    file_backed_rehearsal_dir: Path,
) -> None:
    from kreports.db.migrations import MIGRATIONS
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source_before = _sha256_file(legacy_kam_source)
    report = run_kam_schema_backfill_rehearsal(
        source_db=legacy_kam_source,
        rehearsal_dir=file_backed_rehearsal_dir,
        repository_root=PROJECT_ROOT,
        python_executable=Path(sys.executable),
        include_db_evidence=True,
    )

    assert report["status"] in {
        "complete",
        "mcp_schema_closed",
        "data_quality_limited",
    }
    assert report["live_sha256_unchanged"] is True
    assert report["idempotency"]["semantic_sha256_equal"] is True
    assert report["mcp"]["tool_count"] == 18
    # A genuine revision-04 audit_fees table predates the revision-07
    # compatibility JSON column. Nonzero normalized-claim coverage belongs to
    # the revision-08 worker test; this run proves the empty phase is stable.
    assert report["idempotency"]["integrity"][
        "audit_fee_observations"
    ]["current_count"] == 0
    assert _sha256_file(legacy_kam_source) == source_before
    clone_path = Path(report["clone_path"])
    with sqlite3.connect(clone_path) as clone_connection:
        clone_revisions = [
            row[0]
            for row in clone_connection.execute(
                "SELECT revision FROM schema_migrations ORDER BY rowid"
            )
        ]
    assert clone_revisions[-1] == MIGRATIONS[-1].revision
    assert clone_revisions == [migration.revision for migration in MIGRATIONS]


# Break caught: the orchestrator can claim schema closure after a real legacy
# migration while skipping an MCP gate, changing the source, or losing the clone.
@pytest.mark.skipif(sys.platform != "darwin", reason="APFS clonefile required")
def test_real_rehearsal_migrates_rebuilds_and_preserves_source(
    legacy_kam_source: Path,
    apfs_rehearsal_dir: Path,
) -> None:
    from kreports.db.migrations import MIGRATIONS
    from kreports.maintenance.kam_backfill_rehearsal import (
        run_kam_schema_backfill_rehearsal,
    )

    source_before = _sha256_file(legacy_kam_source)
    with sqlite3.connect(legacy_kam_source) as source_connection:
        source_tables = {
            row[0]
            for row in source_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "kam_items",
            "group_entities",
            "group_relationships",
            "group_component_metrics",
        }.isdisjoint(source_tables)
        source_indexes = {
            row[0]
            for row in source_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert {
            "idx_kam_item_corp_year",
            "idx_kam_item_quality_year",
            "idx_kam_item_receipt",
            "idx_audit_procedure_kam_item",
            "idx_audit_procedure_method_year",
            "idx_audit_fee_availability_year",
            "idx_group_entity_parent_year",
            "idx_group_relationship_parent_year",
            "idx_group_metric_parent_year",
        }.isdisjoint(source_indexes)
        assert "kam_item_id" not in {
            row[1]
            for row in source_connection.execute(
                "PRAGMA table_info(audit_procedure_items)"
            )
        }
        assert "availability_status" not in {
            row[1]
            for row in source_connection.execute("PRAGMA table_info(audit_fees)")
        }
    report = run_kam_schema_backfill_rehearsal(
        source_db=legacy_kam_source,
        rehearsal_dir=apfs_rehearsal_dir,
        repository_root=PROJECT_ROOT,
        python_executable=Path(sys.executable),
    )

    assert report["status"] in {
        "mcp_schema_closed",
        "data_quality_limited",
        "complete",
    }
    assert report["mcp"]["tool_count"] == 18
    assert report["mcp"]["schema_error_closed"] is True
    assert report["idempotency"]["semantic_sha256_equal"] is True
    assert set(report["idempotency"]) == {
        "semantic_sha256",
        "semantic_sha256_equal",
        "integrity",
    }
    assert _sha256_file(legacy_kam_source) == source_before
    clone_path = Path(report["clone"]["path"])
    assert clone_path.is_file()
    with sqlite3.connect(clone_path) as clone_connection:
        clone_tables = {
            row[0]
            for row in clone_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "kam_items",
            "group_entities",
            "group_relationships",
            "group_component_metrics",
        } <= clone_tables
        recorded_revisions = {
            row[0]
            for row in clone_connection.execute(
                "SELECT revision FROM schema_migrations"
            )
        }
        assert recorded_revisions == {item.revision for item in MIGRATIONS}
        assert [item.revision for item in MIGRATIONS[4:8]] == [
            "20260711_05_kam_items",
            "20260711_06_audit_procedure_linkage",
            "20260711_07_audit_fee_availability",
            "20260711_08_group_audit_graph",
        ]
        assert {row[1] for row in clone_connection.execute(
            "PRAGMA table_info(group_component_metrics)"
        )} >= {
            "parent_corp_code",
            "effective_year",
            "metric_key",
            "source_rcept_no",
            "qsc_status",
        }
