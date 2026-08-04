import hashlib
import json
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from kreports.db.quality_snapshot import QUALITY_VERSION
from kreports.quality.company_year_fingerprint import (
    build_quality_evidence_summary,
    quality_input_fingerprint,
)
from tests.factories import (
    company_factory,
    disclosure_factory,
    evidence_document_factory,
)

_QUALITY_CONTENT_FIELDS = (
    "corp_code",
    "bsns_year",
    "market",
    "financial_core_status",
    "auditor_status",
    "audit_fee_status",
    "policy_status",
    "kam_status",
    "audit_procedure_status",
    "group_audit_status",
    "investor_grade",
    "auditor_grade",
    "group_audit_grade",
    "blockers_json",
    "quality_version",
    "input_fingerprint",
    "evidence_summary_json",
)


def _expected_quality_digest(rows: list[dict]) -> str:
    ordered = sorted(
        (
            {
                field: (
                    sorted(json.loads(row[field]))
                    if field == "blockers_json"
                    else json.loads(row[field])
                    if field == "evidence_summary_json"
                    else row[field]
                )
                for field in _QUALITY_CONTENT_FIELDS
            }
            for row in rows
        ),
        key=lambda row: (row["corp_code"], row["bsns_year"]),
    )
    payload = json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _financial_core_proof(bsns_year: int) -> dict:
    return {
        "window_start_year": bsns_year - 4,
        "window_end_year": bsns_year,
        "proven_years": [{
            "bsns_year": bsns_year,
            "fs_div": "CFS",
            "rcept_no": "20260318000001",
            "report_nm": f"사업보고서 ({bsns_year}.12)",
            "metric_digest": "a" * 64,
        }],
    }


def _quality_values(
    corp_code: str,
    bsns_year: int,
    *,
    investor_grade: str = "A",
    quality_version: str = QUALITY_VERSION,
    blockers_json: str = "[]",
    input_fingerprint: str | None = None,
    evidence_summary_json: str | None = None,
) -> dict:
    values = {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "market": "KOSPI",
        "financial_core_status": "available",
        "auditor_status": "available",
        "audit_fee_status": "available",
        "policy_status": "full_body",
        "kam_status": "full_body",
        "audit_procedure_status": "available",
        "group_audit_status": "missing",
        "investor_grade": investor_grade,
        "auditor_grade": "A",
        "group_audit_grade": "D",
        "blockers_json": blockers_json,
        "quality_version": quality_version,
    }
    if input_fingerprint is None or evidence_summary_json is None:
        try:
            blockers = json.loads(blockers_json)
        except json.JSONDecodeError:
            blockers = []
        if not isinstance(blockers, list) or not all(
            isinstance(blocker, str) for blocker in blockers
        ):
            blockers = []
        summary = build_quality_evidence_summary(
            statuses={
                "financial_core": values["financial_core_status"],
                "auditor": values["auditor_status"],
                "audit_fee": values["audit_fee_status"],
                "policy": values["policy_status"],
                "kam": values["kam_status"],
                "audit_procedure": values["audit_procedure_status"],
                "group_audit": values["group_audit_status"],
            },
            grades={
                "investor_core": values["investor_grade"],
                "auditor_full": values["auditor_grade"],
                "group_audit": values["group_audit_grade"],
            },
            blockers=blockers,
            quality_version=quality_version,
            financial_core_proof=(
                _financial_core_proof(bsns_year)
                if quality_version == QUALITY_VERSION
                else None
            ),
        )
        if input_fingerprint is None:
            input_fingerprint = quality_input_fingerprint(summary)
        if evidence_summary_json is None:
            evidence_summary_json = json.dumps(
                summary,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
    values["input_fingerprint"] = input_fingerprint
    values["evidence_summary_json"] = evidence_summary_json
    return values


def _apply_contract(temp_engine) -> None:
    from kreports.db.migrations import apply_schema_migrations

    with temp_engine.begin() as conn:
        apply_schema_migrations(conn)


def test_dataset_manifest_records_schema_version_counts_and_year_range(temp_engine):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.migrations import MIGRATIONS
    from kreports.db.models import DatasetManifest

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add_all(
            [
                company_factory(),
                company_factory(corp_code="00164779", corp_name="한국전력"),
                disclosure_factory(
                    rcept_no="20220318000001",
                    disc_date=date(2022, 3, 18),
                ),
                disclosure_factory(
                    rcept_no="20250318000001",
                    disc_date=date(2025, 3, 18),
                ),
                evidence_document_factory(
                    bsns_year=2021,
                    rcept_no="20220318000001",
                ),
                evidence_document_factory(
                    bsns_year=2024,
                    rcept_no="20250318000001",
                ),
            ]
        )

    result = write_dataset_manifest("compact-2025.07.25", notes="release candidate")

    assert result == {
        "manifest_id": "compact-2025.07.25",
        "schema_version": MIGRATIONS[-1].revision,
        "dataset_version": "compact-2025.07.25",
        "generated_at": result["generated_at"],
        "year_from": 2021,
        "year_to": 2024,
        "company_count": 2,
        "disclosure_count": 2,
        "evidence_document_count": 2,
        "quality_snapshot_json": result["quality_snapshot_json"],
        "notes": "release candidate",
    }

    with get_session() as session:
        stored = session.scalars(select(DatasetManifest)).one()
        stored_values = {
            "manifest_id": stored.manifest_id,
            "schema_version": stored.schema_version,
            "company_count": stored.company_count,
            "disclosure_count": stored.disclosure_count,
            "evidence_document_count": stored.evidence_document_count,
            "year_from": stored.year_from,
            "year_to": stored.year_to,
            "quality_snapshot_json": stored.quality_snapshot_json,
        }

    assert stored_values["manifest_id"] == "compact-2025.07.25"
    assert stored_values["schema_version"] == MIGRATIONS[-1].revision
    assert stored_values["company_count"] == 2
    assert stored_values["disclosure_count"] == 2
    assert stored_values["evidence_document_count"] == 2
    assert (stored_values["year_from"], stored_values["year_to"]) == (2021, 2024)
    assert json.loads(stored_values["quality_snapshot_json"]) == {
        "content_digest": _expected_quality_digest([]),
        "coverage_year": None,
        "coverage_year_row_count": 0,
        "quality_version": QUALITY_VERSION,
        "row_count": 0,
    }


def test_dataset_manifest_allows_empty_dataset_with_unknown_year_range(temp_engine):
    from kreports.db.engine import write_dataset_manifest

    _apply_contract(temp_engine)

    result = write_dataset_manifest("empty-v1")

    assert result["year_from"] is None
    assert result["year_to"] is None
    assert result["company_count"] == 0
    assert result["disclosure_count"] == 0
    assert result["evidence_document_count"] == 0


def test_dataset_manifest_includes_compact_only_financial_years(temp_engine):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import FinancialFactCompact

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add_all(
            [
                FinancialFactCompact(
                    corp_code="00126380",
                    bsns_year=2020,
                    fs_div="CFS",
                    metric_key="revenue",
                    metric_name="매출액",
                    amount=100,
                    fetched_at=datetime.now(UTC),
                ),
                FinancialFactCompact(
                    corp_code="00126380",
                    bsns_year=2025,
                    fs_div="CFS",
                    metric_key="assets",
                    metric_name="자산총계",
                    amount=200,
                    fetched_at=datetime.now(UTC),
                ),
            ]
        )

    result = write_dataset_manifest("compact-only-v1")

    assert (result["year_from"], result["year_to"]) == (2020, 2025)


def test_dataset_manifest_rejects_whitespace_only_version(temp_engine):
    from kreports.db.engine import write_dataset_manifest

    _apply_contract(temp_engine)

    with pytest.raises(ValueError, match="dataset_version"):
        write_dataset_manifest(" \t ")


def test_dataset_manifest_normalizes_version_before_identity_and_duplicate_check(
    temp_engine,
):
    from kreports.db.engine import write_dataset_manifest

    _apply_contract(temp_engine)

    result = write_dataset_manifest("  compact-v1  ")

    assert result["manifest_id"] == "compact-v1"
    assert result["dataset_version"] == "compact-v1"
    with pytest.raises(ValueError, match="already exists"):
        write_dataset_manifest("compact-v1")


def test_dataset_manifest_rejects_duplicate_manifest_id_without_overwrite(temp_engine):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import DatasetManifest

    _apply_contract(temp_engine)
    first = write_dataset_manifest("compact-v1", notes="first")

    with pytest.raises(ValueError, match="already exists"):
        write_dataset_manifest("compact-v1", notes="replacement")

    with get_session() as session:
        stored = session.get(DatasetManifest, "compact-v1")
        stored_values = (
            stored.notes if stored is not None else None,
            stored.generated_at.isoformat() if stored is not None else None,
        )

    assert stored is not None
    assert stored_values[0] == "first"
    assert first["generated_at"].startswith(stored_values[1])


def test_dataset_manifest_writer_is_rejected_in_readonly_mode(
    temp_engine,
    monkeypatch,
):
    from kreports.db.engine import write_dataset_manifest

    _apply_contract(temp_engine)
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        write_dataset_manifest("readonly-v1")


def test_manifest_uses_business_year_and_snapshots_quality_ledger(
    temp_engine,
):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality, Disclosure

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add(company_factory())
        session.add(
            Disclosure(
                rcept_no="20260318000001",
                corp_code="00126380",
                corp_name="삼성전자",
                disc_date=date(2026, 3, 18),
                disc_type="A",
                report_nm="사업보고서 (2025.12)",
            )
        )
        session.add(
            CompanyYearQuality(
                **_quality_values("00126380", 2025),
                updated_at=datetime.now(UTC),
            )
        )

    result = write_dataset_manifest("quality-v1")

    assert (result["year_from"], result["year_to"]) == (2025, 2025)
    assert json.loads(result["quality_snapshot_json"]) == {
        "content_digest": _expected_quality_digest(
            [_quality_values("00126380", 2025)]
        ),
        "coverage_year": 2025,
        "coverage_year_row_count": 1,
        "quality_version": QUALITY_VERSION,
        "row_count": 1,
    }


def test_manifest_quality_digest_is_stable_across_row_and_timestamp_order(
    temp_engine,
):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality

    _apply_contract(temp_engine)
    first_values = _quality_values("00000001", 2024)
    second_values = _quality_values(
        "00000002",
        2025,
        investor_grade="B",
    )
    with get_session() as session:
        session.add_all(
            [
                CompanyYearQuality(
                    **second_values,
                    updated_at=datetime(2026, 1, 2, tzinfo=UTC),
                ),
                CompanyYearQuality(
                    **first_values,
                    updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ]
        )

    first = json.loads(
        write_dataset_manifest("quality-order-first")[
            "quality_snapshot_json"
        ]
    )
    with get_session() as session:
        session.query(CompanyYearQuality).delete()
        session.add_all(
            [
                CompanyYearQuality(
                    **first_values,
                    updated_at=datetime(2030, 1, 1, tzinfo=UTC),
                ),
                CompanyYearQuality(
                    **second_values,
                    updated_at=datetime(2030, 1, 2, tzinfo=UTC),
                ),
            ]
        )
    second = json.loads(
        write_dataset_manifest("quality-order-second")[
            "quality_snapshot_json"
        ]
    )

    assert first.get("content_digest") == _expected_quality_digest(
        [first_values, second_values]
    )
    assert second.get("content_digest") == first["content_digest"]


def test_dataset_manifest_rejects_unsupported_quality_version(temp_engine):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                **_quality_values(
                    "00126380",
                    2025,
                    quality_version="unsupported-v1",
                ),
                updated_at=datetime.now(UTC),
            )
        )

    with pytest.raises(
        RuntimeError,
        match="supported quality version",
    ):
        write_dataset_manifest("unsupported-quality-v2")


def test_dataset_manifest_rejects_tampered_financial_core_proof_digest(
    temp_engine,
):
    """A proof-only receipt or amount digest edit cannot preserve a v2 snapshot."""
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                **_quality_values("00126380", 2025),
                updated_at=datetime.now(UTC),
            )
        )

    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        summary = json.loads(row.evidence_summary_json)
        summary["financial_core_proof"]["proven_years"][0][
            "metric_digest"
        ] = "b" * 64
        row.evidence_summary_json = json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    with pytest.raises(ValueError, match="input_fingerprint"):
        write_dataset_manifest("tampered-core-proof")


def test_dataset_manifest_rejects_v2_quality_row_without_core_proof(
    temp_engine,
):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality

    _apply_contract(temp_engine)
    values = _quality_values(
        "00126380",
        2025,
        quality_version="v1",
    )
    values["quality_version"] = QUALITY_VERSION
    summary = json.loads(values["evidence_summary_json"])
    summary["quality_version"] = QUALITY_VERSION
    values["evidence_summary_json"] = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                **values,
                updated_at=datetime.now(UTC),
            )
        )

    with pytest.raises(ValueError, match="financial_core_proof"):
        write_dataset_manifest("missing-core-proof")


def test_manifest_digest_normalizes_blocker_order_but_detects_content_change(
    temp_engine,
):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                **_quality_values(
                    "00126380",
                    2025,
                    blockers_json='["kam_error", "policy_error"]',
                ),
                updated_at=datetime.now(UTC),
            )
        )

    first = json.loads(
        write_dataset_manifest("blockers-first")[
            "quality_snapshot_json"
        ]
    )
    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        row.blockers_json = '["policy_error", "kam_error"]'
    reordered = json.loads(
        write_dataset_manifest("blockers-reordered")[
            "quality_snapshot_json"
        ]
    )
    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        row.blockers_json = '["policy_error", "audit_fee_error"]'
        summary = json.loads(row.evidence_summary_json)
        summary["blockers"] = ["audit_fee_error", "policy_error"]
        row.evidence_summary_json = json.dumps(summary)
        row.input_fingerprint = quality_input_fingerprint(summary)
    changed = json.loads(
        write_dataset_manifest("blockers-changed")[
            "quality_snapshot_json"
        ]
    )

    assert reordered["content_digest"] == first["content_digest"]
    assert changed["content_digest"] != first["content_digest"]


def test_manifest_digest_canonicalizes_evidence_summary_json(temp_engine):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                **_quality_values("00126380", 2025),
                updated_at=datetime.now(UTC),
            )
        )

    first = json.loads(
        write_dataset_manifest("summary-first")[
            "quality_snapshot_json"
        ]
    )
    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        summary = json.loads(row.evidence_summary_json)
        row.evidence_summary_json = json.dumps(summary, indent=2)
    reordered = json.loads(
        write_dataset_manifest("summary-reordered")[
            "quality_snapshot_json"
        ]
    )
    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        row.input_fingerprint = "b" * 64
    with pytest.raises(ValueError, match="quality freshness"):
        write_dataset_manifest("fingerprint-changed")
    with get_session() as session:
        row = session.get(CompanyYearQuality, ("00126380", 2025))
        assert row is not None
        summary = json.loads(row.evidence_summary_json)
        summary["grades"]["investor_core"] = "B"
        row.investor_grade = "B"
        row.input_fingerprint = quality_input_fingerprint(summary)
        row.evidence_summary_json = json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    changed = json.loads(
        write_dataset_manifest("summary-changed")[
            "quality_snapshot_json"
        ]
    )

    assert reordered["content_digest"] == first["content_digest"]
    assert changed["content_digest"] != first["content_digest"]


@pytest.mark.parametrize(
    "evidence_summary_json",
    [
        "{not-json",
        "[]",
    ],
)
def test_dataset_manifest_rejects_invalid_evidence_summary_object(
    temp_engine,
    evidence_summary_json,
):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                **_quality_values(
                    "00126380",
                    2025,
                    evidence_summary_json=evidence_summary_json,
                ),
                updated_at=datetime.now(UTC),
            )
        )

    with pytest.raises(
        ValueError,
        match="evidence_summary_json must be a JSON object",
    ):
        write_dataset_manifest("invalid-evidence-summary")


@pytest.mark.parametrize(
    "case",
    [
        "legacy_blank",
        "extra_local_path",
        "row_grade_mismatch",
    ],
)
def test_dataset_manifest_rejects_unverified_quality_freshness(
    temp_engine,
    case,
):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality
    from kreports.quality.company_year_fingerprint import (
        build_quality_evidence_summary,
        quality_input_fingerprint,
    )

    _apply_contract(temp_engine)
    values = _quality_values("00126380", 2025)
    if case == "legacy_blank":
        values["input_fingerprint"] = ""
        values["evidence_summary_json"] = "{}"
    else:
        summary = build_quality_evidence_summary(
            statuses={
                "financial_core": "available",
                "auditor": "available",
                "audit_fee": "available",
                "policy": "full_body",
                "kam": "full_body",
                "audit_procedure": "available",
                "group_audit": "missing",
            },
            grades={
                "investor_core": (
                    "B" if case == "row_grade_mismatch" else "A"
                ),
                "auditor_full": "A",
                "group_audit": "D",
            },
            blockers=(),
            quality_version=QUALITY_VERSION,
            financial_core_proof=_financial_core_proof(2025),
        )
        if case == "extra_local_path":
            summary["local_path"] = "/private/tmp/quality.db"
            payload = json.dumps(
                summary,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            fingerprint = hashlib.sha256(
                payload.encode("utf-8")
            ).hexdigest()
        else:
            fingerprint = quality_input_fingerprint(summary)
        values["input_fingerprint"] = fingerprint
        values["evidence_summary_json"] = json.dumps(summary)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                **values,
                updated_at=datetime.now(UTC),
            )
        )

    with pytest.raises(
        ValueError,
        match="quality freshness",
    ):
        write_dataset_manifest(f"invalid-freshness-{case}")


@pytest.mark.parametrize(
    "blockers_json",
    [
        "{not-json",
        '{"kam_error": true}',
        '[1, "kam_error"]',
    ],
)
def test_dataset_manifest_rejects_invalid_blocker_array(
    temp_engine,
    blockers_json,
):
    from kreports.db.engine import get_session, write_dataset_manifest
    from kreports.db.models import CompanyYearQuality

    _apply_contract(temp_engine)
    with get_session() as session:
        session.add(
            CompanyYearQuality(
                **_quality_values(
                    "00126380",
                    2025,
                    blockers_json=blockers_json,
                ),
                updated_at=datetime.now(UTC),
            )
        )

    with pytest.raises(
        ValueError,
        match="blockers_json must be a JSON array of strings",
    ):
        write_dataset_manifest("invalid-blockers")
