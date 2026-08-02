"""Deterministic, fail-closed release evidence for a runtime SQLite artifact."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext
from collections.abc import Iterable
import hashlib
import json
import math
import os
from pathlib import Path
from importlib import resources
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ARTIFACT_VERSION = "1.0"
TOOL_CONTRACT_VERSION = "1.0"
FROZEN_TOOL_COUNT = 34
FROZEN_TOOL_WIRE_SHA256 = (
    "d02a3a78fc06506f5fa71b359d638eea447d5a4fcee41c88dfab6f16caa3b6a8"
)
APPROVED_GOLDEN_CONTRACT_SHA256 = (
    "c6552ed45c0fb5032d45e337e2e75fae92b0bda6854b4b0aa97ebff11b0dd617"
)
_CONTRACT_RUNNER_MARKER = "KREPORTS_RELEASE_CONTRACT="
_RUNTIME_DIGEST_CACHE: dict[tuple[Any, ...], str] = {}
_RUNTIME_DIGEST_LOCK = threading.Lock()
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_COUNT = 10**12
_MAX_TEXT_LENGTH = 10_000
MAX_MANIFEST_BYTES = 2_000_000
REQUIRED_TABLES = (
    "companies",
    "disclosures",
    "financials",
    "financial_facts_compact",
    "report_sections",
    "evidence_documents",
    "backfill_runs",
    "company_year_quality",
    "schema_migrations",
    "dataset_manifest",
    "source_documents",
)
REQUIRED_INDEXES = (
    "idx_company_year_quality_year_market",
    "uq_backfill_runs_active_lease",
    "idx_kam_item_corp_year",
    "idx_kam_item_quality_year",
    "idx_kam_item_receipt",
    "idx_audit_procedure_kam_item",
    "idx_audit_procedure_method_year",
    "idx_audit_fee_availability_year",
    "idx_group_entity_parent_year",
    "idx_group_entity_resolved_year",
    "idx_group_relationship_parent_year",
    "idx_group_relationship_nodes",
    "idx_group_metric_parent_year",
    "idx_group_metric_entity_kind",
    "idx_group_metric_qsc_year",
)
REQUIRED_INDEX_SPECS = {
    "idx_company_year_quality_year_market": (
        "company_year_quality", ("bsns_year", "market"), False, None
    ),
    "uq_backfill_runs_active_lease": (
        "backfill_runs", ("lease_key",), True, "where status = 'running'"
    ),
    "idx_kam_item_corp_year": (
        "kam_items", ("corp_code", "bsns_year"), False, None
    ),
    "idx_kam_item_quality_year": (
        "kam_items", ("bsns_year", "quality_status"), False, None
    ),
    "idx_kam_item_receipt": (
        "kam_items", ("rcept_no", "source_type"), False, None
    ),
    "idx_audit_procedure_kam_item": (
        "audit_procedure_items", ("kam_item_id",), False, None
    ),
    "idx_audit_procedure_method_year": (
        "audit_procedure_items", ("method", "bsns_year"), False, None
    ),
    "idx_audit_fee_availability_year": (
        "audit_fees", ("bsns_year", "availability_status"), False, None
    ),
    "idx_group_entity_parent_year": (
        "group_entities", ("parent_corp_code", "effective_year"), False, None
    ),
    "idx_group_entity_resolved_year": (
        "group_entities", ("resolved_corp_code", "effective_year"), False, None
    ),
    "idx_group_relationship_parent_year": (
        "group_relationships",
        ("parent_corp_code", "effective_year"),
        False,
        None,
    ),
    "idx_group_relationship_nodes": (
        "group_relationships",
        ("parent_entity_key", "child_entity_key"),
        False,
        None,
    ),
    "idx_group_metric_parent_year": (
        "group_component_metrics",
        ("parent_corp_code", "effective_year"),
        False,
        None,
    ),
    "idx_group_metric_entity_kind": (
        "group_component_metrics", ("entity_key", "metric_key"), False, None
    ),
    "idx_group_metric_qsc_year": (
        "group_component_metrics", ("effective_year", "qsc_status"), False, None
    ),
}


class ReleaseArtifactError(RuntimeError):
    """A stable fail-closed error while building immutable release proof."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DatabaseEvidence(_StrictModel):
    file_name: StrictStr = Field(min_length=1, max_length=255)
    byte_count: StrictInt = Field(ge=0, le=_MAX_COUNT)
    sha256: StrictStr = Field(pattern=_SHA256_PATTERN)

    @field_validator("file_name")
    @classmethod
    def _file_name_only(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("database file_name must not contain a path")
        return value


class SchemaEvidence(_StrictModel):
    version: StrictStr = Field(min_length=1, max_length=80)
    required_tables: list[StrictStr]
    required_indexes: list[StrictStr]


class DatasetEvidence(_StrictModel):
    version: StrictStr = Field(min_length=1, max_length=80)
    manifest_state: dict[StrictStr, Any]


class ToolContractEvidence(_StrictModel):
    version: StrictStr
    tool_count: StrictInt = Field(ge=0, le=FROZEN_TOOL_COUNT)
    wire_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)


class ReleaseGateEvidence(_StrictModel):
    profile: StrictStr
    passed: StrictBool
    blockers: list[StrictStr]
    degraded_features: list[StrictStr]
    coverage_year: StrictInt | None
    feature_coverage: dict[StrictStr, Any]
    feature_grades: dict[StrictStr, Any]

    @model_validator(mode="after")
    def _failed_gate_has_blockers(self) -> "ReleaseGateEvidence":
        if self.passed and self.blockers:
            raise ValueError("a passing release gate cannot contain blockers")
        if not self.passed and not self.blockers:
            raise ValueError("a failed release gate must contain named blockers")
        return self


class AllToolContractEvidence(_StrictModel):
    passed: StrictBool
    checks: StrictInt = Field(ge=0, le=FROZEN_TOOL_COUNT)


class ContractEvidence(_StrictModel):
    all_tools: AllToolContractEvidence
    golden_contract_sha256: StrictStr = Field(pattern=_SHA256_PATTERN)
    golden_contract_passed: StrictBool = True


def _validate_bounded_json(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("manifest values exceed maximum nesting")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > _MAX_COUNT:
            raise ValueError("manifest integer is out of bounds")
        return
    if isinstance(value, float):
        if not math.isfinite(value) or abs(value) > _MAX_COUNT:
            raise ValueError("manifest number must be finite and bounded")
        return
    if isinstance(value, (datetime, str)):
        value = value.isoformat() if isinstance(value, datetime) else value
        if len(value) > _MAX_TEXT_LENGTH:
            raise ValueError("manifest string is too long")
        return
    if isinstance(value, list):
        if len(value) > 10_000:
            raise ValueError("manifest list is too long")
        for item in value:
            _validate_bounded_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 10_000:
            raise ValueError("manifest object is too large")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 255:
                raise ValueError("manifest object key is invalid")
            _validate_bounded_json(item, depth=depth + 1)
        return
    raise ValueError(f"unsupported manifest value: {type(value).__name__}")


class ReleaseManifest(_StrictModel):
    artifact_version: StrictStr
    generated_at: datetime
    database: DatabaseEvidence
    schema_evidence: SchemaEvidence = Field(alias="schema")
    dataset: DatasetEvidence
    tool_contract: ToolContractEvidence
    release_gate: ReleaseGateEvidence
    inline_raw_count: StrictInt = Field(ge=0, le=_MAX_COUNT)
    contracts: ContractEvidence

    @model_validator(mode="after")
    def _supported_contracts_and_bounded_values(self) -> "ReleaseManifest":
        if self.artifact_version != ARTIFACT_VERSION:
            raise ValueError("unsupported release artifact version")
        if self.tool_contract.version != TOOL_CONTRACT_VERSION:
            raise ValueError("unsupported tool contract version")
        _validate_bounded_json(self.model_dump(mode="python", by_alias=True))
        return self


class VerificationDiagnostic(_StrictModel):
    failure: StrictStr
    owner: StrictStr
    action: StrictStr


class VerificationResult(_StrictModel):
    ok: StrictBool
    failures: list[StrictStr]
    diagnostics: list[VerificationDiagnostic] = Field(default_factory=list)


_VERIFICATION_DIAGNOSTIC_OVERRIDES: dict[str, tuple[str, str]] = {
    "tool_contract_evidence_mismatch": (
        "dataset_release_maintainer",
        "rebuild the release manifest from the current approved 34-tool catalog",
    ),
    "tool_contract_drift": (
        "dataset_release_maintainer",
        "rebuild the release manifest from the current approved 34-tool catalog",
    ),
    "contracts_evidence_mismatch": (
        "dataset_release_maintainer",
        "rerun the all-tool contract and rebuild the release manifest after it passes",
    ),
    "all_tool_contract_failed": (
        "mcp_contract_maintainer",
        "repair the failing catalog tool contract before rebuilding release proof",
    ),
}


def _verification_diagnostics(
    failures: Iterable[str],
) -> list[VerificationDiagnostic]:
    """Attach owner/action context while preserving each fail-closed code."""
    from kreports.quality.release_gate import describe_release_blockers

    diagnostics: list[VerificationDiagnostic] = []
    for failure in sorted(set(str(item) for item in failures)):
        owner, action = _VERIFICATION_DIAGNOSTIC_OVERRIDES.get(
            failure,
            ("", ""),
        )
        if not owner:
            blocker = failure.removeprefix("release_gate_blocked:")
            guidance = describe_release_blockers([blocker])[0]
            owner = guidance["owner"]
            action = guidance["action"]
        diagnostics.append({
            "failure": failure,
            "owner": owner,
            "action": action,
        })
    return diagnostics


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_wire_sha256() -> str:
    from kreports.mcp.dispatch import list_mcp_tools

    snapshot = [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": tool.inputSchema,
            "annotations": (
                tool.annotations.model_dump(mode="json", exclude_none=False)
                if tool.annotations
                else None
            ),
        }
        for tool in list_mcp_tools()
    ]
    payload = (
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _valid_tool_arguments(name: str, model: type[BaseModel]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field_name, field in model.model_fields.items():
        if not field.is_required():
            continue
        if field_name in {"year", "bsns_year", "base_year"}:
            values[field_name] = 2025
        elif field_name == "dataset":
            values[field_name] = "financials"
        else:
            values[field_name] = "005930"
    if name == "get_industry_audit_landscape":
        values["induty_code"] = "264"
    if name in {
        "compare_to_industry",
        "search_audit_report_matters",
        "search_audit_procedures",
        "search_disclosure_events",
    }:
        values["company"] = "005930"
    if name == "fetch_disclosure_on_demand":
        values["rcept_no"] = "20250101000001"
        values["cache_policy"] = "refresh"
    if name == "build_dcf_model_pack":
        values.update(
            revenue_growth=0.03,
            operating_margin=0.1,
            tax_rate=0.22,
            da_to_revenue=0.03,
            capex_to_revenue=0.04,
            nwc_to_revenue=0.1,
            wacc=0.09,
            terminal_growth=0.02,
        )
    return values


def _run_catalog_dispatch_contract(
    db_path: str | os.PathLike[str] | None = None,
) -> bool:
    """Execute dispatch checks in the current process.

    Explicit-DB callers run this helper only in the isolated runner process.
    """
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.contracts import AnswerEnvelopeV1
    from kreports.mcp.dispatch import dispatch_tool

    context = (
        _bound_explicit_runtime(_safe_existing_db_path(db_path))
        if db_path is not None
        else nullcontext()
    )
    try:
        with context:
            for name, spec in TOOL_CATALOG.items():
                arguments = (
                    _valid_tool_arguments(name, spec.input_model)
                    if db_path is not None
                    else {"__release_contract_unknown__": True}
                )
                envelope = dispatch_tool(name, arguments)
                expected_status = (
                    {"usable", "limited", "missing"}
                    if db_path is not None
                    else {"error"}
                )
                valid_envelope = (
                    isinstance(envelope, AnswerEnvelopeV1)
                    and envelope.tool_name == name
                    and envelope.answer.strip()
                    and envelope.data_quality.status in expected_status
                )
                if (
                    db_path is not None
                    and name == "fetch_disclosure_on_demand"
                    and isinstance(envelope, AnswerEnvelopeV1)
                    and envelope.data_quality.status == "error"
                    and "user_dart_api_key is required"
                    in envelope.data_quality.limitations
                ):
                    valid_envelope = True
                if not valid_envelope:
                    return False
    except Exception:
        return False
    return True


def _isolated_catalog_dispatch_contract(db_path: Path) -> bool:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "kreports.release_contract_runner",
                str(db_path),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(_CONTRACT_RUNNER_MARKER):
            return line.removeprefix(_CONTRACT_RUNNER_MARKER) == "1"
    return False


def run_all_tool_contract(
    db_path: str | os.PathLike[str] | None = None,
) -> dict[str, bool | int]:
    """Recompute static wire checks and catalog dispatches without DB leakage."""
    from kreports.mcp.catalog import TOOL_CATALOG

    passed = (
        len(TOOL_CATALOG) == FROZEN_TOOL_COUNT
        and all(
            spec.input_model.model_config.get("extra") == "forbid"
            for spec in TOOL_CATALOG.values()
        )
        and _tool_wire_sha256() == FROZEN_TOOL_WIRE_SHA256
    )
    dispatch_passed = (
        _run_catalog_dispatch_contract()
        if db_path is None
        else _isolated_catalog_dispatch_contract(
            _safe_existing_db_path(db_path)
        )
    )
    passed = passed and dispatch_passed
    return {"passed": passed, "checks": len(TOOL_CATALOG)}


def _safe_existing_db_path(db_path: str | os.PathLike[str]) -> Path:
    raw = Path(db_path).expanduser()
    if raw.is_symlink():
        raise ValueError("database path must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError("database path must be an existing file") from exc
    if not resolved.is_file():
        raise ValueError("database path must be an existing file")
    if resolved.stat().st_nlink != 1:
        raise ValueError("database path must not be a hardlink")
    return resolved


def _safe_manifest_path(
    db_path: Path,
    manifest_path: str | os.PathLike[str] | None,
) -> Path:
    candidate = (
        db_path.with_suffix(db_path.suffix + ".release.json")
        if manifest_path is None
        else Path(manifest_path).expanduser()
    )
    if candidate.is_symlink():
        raise ValueError("manifest path must not be a symlink")
    parent = candidate.parent.resolve(strict=True)
    resolved = parent / candidate.name
    if resolved == db_path:
        raise ValueError("manifest path must not overwrite the database")
    if resolved.exists() and os.path.samefile(resolved, db_path):
        raise ValueError("manifest path must not alias the database")
    if resolved.suffix != ".json":
        raise ValueError("manifest path must end in .json")
    return resolved


def _open_immutable_sqlite(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{db_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


@contextmanager
def _explicit_session_scope(db_path: Path):
    sqlalchemy_engine = create_engine(
        "sqlite+pysqlite://",
        creator=lambda: _open_immutable_sqlite(db_path),
    )
    session_factory = sessionmaker(
        bind=sqlalchemy_engine,
        autocommit=False,
        autoflush=False,
    )
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        sqlalchemy_engine.dispose()


@contextmanager
def _bound_explicit_runtime(db_path: Path):
    """Temporarily bind legacy handlers to the explicit immutable DB."""
    import kreports.db.engine as engine_module

    explicit_engine = create_engine(
        "sqlite+pysqlite://",
        creator=lambda: _open_immutable_sqlite(db_path),
    )
    explicit_sessions = sessionmaker(
        bind=explicit_engine,
        autocommit=False,
        autoflush=False,
    )
    old_engine = engine_module.engine
    old_sessions = engine_module.SessionLocal
    module_engines: list[tuple[Any, Any]] = []
    engine_module.engine = explicit_engine
    engine_module.SessionLocal = explicit_sessions
    try:
        for module_name in (
            "kreports.analysis.disclosure_events",
            "kreports.analysis.peer",
            "kreports.analysis.readiness",
            "kreports.analysis.search_adapter",
        ):
            module = __import__(module_name, fromlist=["engine"])
            if hasattr(module, "engine"):
                module_engines.append((module, module.engine))
                module.engine = explicit_engine
        yield
    finally:
        for module, previous in module_engines:
            module.engine = previous
        engine_module.engine = old_engine
        engine_module.SessionLocal = old_sessions
        explicit_engine.dispose()


def _table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(
            f'PRAGMA table_info("{table_name}")'
        )
    }


def _dataset_manifest_state(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> tuple[str, dict[str, Any]]:
    if "dataset_manifest" not in table_names:
        return "unknown", {"status": "missing"}
    columns = (
        "manifest_id",
        "schema_version",
        "dataset_version",
        "generated_at",
        "year_from",
        "year_to",
        "company_count",
        "disclosure_count",
        "evidence_document_count",
        "quality_snapshot_json",
    )
    available = _table_columns(connection, "dataset_manifest")
    selected = [column for column in columns if column in available]
    row = connection.execute(
        "SELECT " + ", ".join(selected) + " FROM dataset_manifest "
        "ORDER BY generated_at DESC, manifest_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "unknown", {"status": "missing"}
    state = {key: row[key] for key in row.keys()}
    raw_quality = state.get("quality_snapshot_json")
    if isinstance(raw_quality, str):
        try:
            parsed_quality = json.loads(raw_quality)
        except json.JSONDecodeError:
            state["quality_snapshot"] = {"status": "malformed"}
        else:
            state["quality_snapshot"] = _safe_quality_snapshot(
                parsed_quality
            )
        del state["quality_snapshot_json"]
    return str(state.get("dataset_version") or "unknown"), state


def _safe_quality_snapshot(value: Any) -> dict[str, Any]:
    """Expose only the fixed, non-secret dataset quality contract."""
    if not isinstance(value, dict):
        return {"status": "malformed"}
    result: dict[str, Any] = {}
    digest = value.get("content_digest")
    if (
        isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    ):
        result["content_digest"] = digest
    coverage_year = value.get("coverage_year")
    if coverage_year is None or (
        isinstance(coverage_year, int)
        and not isinstance(coverage_year, bool)
        and 1900 <= coverage_year <= 2100
    ):
        result["coverage_year"] = coverage_year
    for key in ("coverage_year_row_count", "row_count"):
        count = value.get(key)
        if (
            isinstance(count, int)
            and not isinstance(count, bool)
            and 0 <= count <= _MAX_COUNT
        ):
            result[key] = count
    quality_version = value.get("quality_version")
    if (
        isinstance(quality_version, str)
        and re.fullmatch(r"v[0-9]{1,4}", quality_version)
    ):
        result["quality_version"] = quality_version
    if set(result) != {
        "content_digest",
        "coverage_year",
        "coverage_year_row_count",
        "quality_version",
        "row_count",
    }:
        return {"status": "malformed"}
    return result


def _schema_version(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> str:
    if "schema_migrations" not in table_names:
        return "unknown"
    row = connection.execute(
        "SELECT revision FROM schema_migrations "
        "ORDER BY revision DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row and row[0] else "unknown"


def _inline_raw_count(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> int:
    if "source_documents" not in table_names:
        return 0
    if "raw_content" not in _table_columns(connection, "source_documents"):
        return 0
    columns = _table_columns(connection, "source_documents")
    identity_columns = (
        "rcept_no",
        "corp_code",
        "bsns_year",
        "source_type",
    )
    optional = [
        name
        for name in (
            "content_type",
            "report_nm",
            "doc_hash",
            *identity_columns,
        )
        if name in columns
    ]
    rows = connection.execute(
        "SELECT raw_content"
        + "".join(f", {name}" for name in optional)
        + " FROM source_documents "
        "WHERE raw_content IS NOT NULL AND length(raw_content) > 0"
    )
    count = 0
    for raw_row in rows:
        row = dict(raw_row)
        body = str(row["raw_content"])
        expected_body = _expected_derived_body(
            connection,
            table_names,
            row,
        )
        trusted_derived = (
            row.get("content_type") == "derived_report_sections"
            and row.get("report_nm") == "derived from report_sections"
            and expected_body is not None
            and body == expected_body
            and row.get("doc_hash")
            == hashlib.sha1(body.encode()).hexdigest()
        )
        if not trusted_derived:
            count += 1
    return count


def _expected_derived_body(
    connection: sqlite3.Connection,
    table_names: set[str],
    source_row: dict[str, Any],
) -> str | None:
    required = {
        "rcept_no",
        "corp_code",
        "bsns_year",
        "source_type",
    }
    if (
        "report_sections" not in table_names
        or not required.issubset(source_row)
        or not {
            "rcept_no",
            "corp_code",
            "bsns_year",
            "source_type",
            "section_key",
            "section_title",
            "body_text",
            "ordinal",
        }.issubset(_table_columns(connection, "report_sections"))
    ):
        return None
    sections = connection.execute(
        "SELECT rcept_no, source_type, section_key, section_title, "
        "body_text, ordinal FROM report_sections "
        "WHERE rcept_no=? AND corp_code=? AND bsns_year=? AND source_type=? "
        "ORDER BY ordinal, section_key",
        (
            source_row["rcept_no"],
            source_row["corp_code"],
            source_row["bsns_year"],
            source_row["source_type"],
        ),
    ).fetchall()
    if not sections:
        return None
    parts = [
        "DERIVED FROM report_sections",
        (
            "This is not the original DART filing body. It is a legacy "
            "evidence bundle reconstructed from cached extracted sections."
        ),
        "",
    ]
    included = 0
    for section in sections:
        body = str(section["body_text"] or "").strip()
        if not body:
            continue
        title = (
            section["section_title"]
            or section["section_key"]
            or "section"
        )
        parts.extend(
            [
                f"## {section['section_key']} | {title}",
                (
                    f"rcept_no={section['rcept_no']} "
                    f"source_type={section['source_type']} "
                    f"ordinal={section['ordinal']}"
                ),
                body,
                "",
            ]
        )
        included += 1
    return "\n".join(parts).strip() if included else None


def _feature_grades(
    connection: sqlite3.Connection,
    table_names: set[str],
    *,
    coverage_year: int | None = None,
) -> dict[str, dict[str, int]]:
    if "company_year_quality" not in table_names:
        return {}
    columns = _table_columns(connection, "company_year_quality")
    result: dict[str, dict[str, int]] = {}
    for public_name, column in (
        ("investor_core", "investor_grade"),
        ("auditor_full", "auditor_grade"),
        ("group_audit", "group_audit_grade"),
    ):
        if column not in columns:
            continue
        where = " WHERE bsns_year=?" if coverage_year is not None else ""
        params = (coverage_year,) if coverage_year is not None else ()
        query = (
            f'SELECT "{column}", COUNT(*) FROM company_year_quality'
            + where
            + f' GROUP BY "{column}" ORDER BY "{column}"'
        )
        rows = connection.execute(
            query,
            params,
        ).fetchall()
        result[public_name] = {
            str(row[0]): int(row[1])
            for row in rows
            if row[0] is not None
        }
    return result


def _index_contract_blockers(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> list[str]:
    blockers: list[str] = []
    for name, (table, columns, unique, where) in REQUIRED_INDEX_SPECS.items():
        if table not in table_names:
            continue
        rows = {
            str(row["name"]): row
            for row in connection.execute(f'PRAGMA index_list("{table}")')
        }
        row = rows.get(name)
        if row is None:
            blockers.append(f"missing_required_index:{name}")
            continue
        actual_columns = tuple(
            str(item["name"])
            for item in connection.execute(f'PRAGMA index_info("{name}")')
        )
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='index' AND name=?",
            (name,),
        ).fetchone()
        sql = " ".join(str(sql_row[0] or "").lower().split()) if sql_row else ""
        if (
            actual_columns != columns
            or bool(row["unique"]) is not unique
            or (where is not None and where not in sql)
            or (where is None and " where " in f" {sql} ")
        ):
            blockers.append(f"invalid_required_index:{name}")
    return blockers


def _duplicate_key_blockers(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> list[str]:
    contracts = {
        "companies": ("corp_code",),
        "company_year_quality": ("corp_code", "bsns_year"),
        "financial_facts_compact": (
            "corp_code",
            "bsns_year",
            "fs_div",
            "metric_key",
        ),
    }
    blockers: list[str] = []
    for table_name, columns in contracts.items():
        if table_name not in table_names:
            continue
        if not set(columns).issubset(
            _table_columns(connection, table_name)
        ):
            continue
        group = ", ".join(f'"{column}"' for column in columns)
        duplicate = connection.execute(
            f'SELECT 1 FROM "{table_name}" GROUP BY {group} '
            "HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate is not None:
            blockers.append(f"duplicate_key:{table_name}")
    return blockers


def golden_contract_result() -> dict[str, Any]:
    try:
        body = (
            resources.files("kreports")
            .joinpath("data/golden_companies.json")
            .read_bytes()
        )
        payload = json.loads(body)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"passed": False, "sha256": "0" * 64, "cases": 0}
    digest = hashlib.sha256(body).hexdigest()
    cases = payload.get("cases") if isinstance(payload, dict) else None
    expected_ids = {
        "samsung_five_year_investor",
        "sk_hynix_group_qsc",
        "daewon_five_year_dcf",
        "modified_opinion",
        "multiple_kam",
        "incomplete_company",
    }
    valid = (
        payload.get("contract_version") == "1.0"
        and isinstance(cases, list)
        and {case.get("id") for case in cases if isinstance(case, dict)}
        == expected_ids
        and all(
            isinstance(case.get("required_shapes"), list)
            and case["required_shapes"]
            and isinstance(case.get("stable_semantics"), list)
            and case["stable_semantics"]
            for case in cases
        )
    )
    return {
        "passed": valid and digest == APPROVED_GOLDEN_CONTRACT_SHA256,
        "sha256": digest,
        "cases": len(cases) if isinstance(cases, list) else 0,
    }


def _list_size(result: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, int):
            return value
    return 0


def _has_public_provenance(envelope: Any) -> bool:
    for reference in envelope.evidence:
        if reference.rcept_no or "dart.fss.or.kr" in reference.source_url:
            return True
    explicit_source_gaps = (
        "로컬 캐시에 확인 가능한 데이터가 없습니다",
        "공개적으로 해석 가능한 근거 링크를 확인하지 못했습니다",
        "요청 사업연도",
        "원 공시 부재를 뜻하지 않습니다",
    )
    return (
        envelope.data_quality.status in {"limited", "missing"}
        and any(
            marker in limitation
            for limitation in envelope.data_quality.limitations
            for marker in explicit_source_gaps
        )
    )


def _find_ofs_only_company(database: Path) -> str | None:
    """Find a real row set that must exercise the public CFS-to-OFS fallback."""
    with _open_immutable_sqlite(database) as connection:
        row = connection.execute(
            """
            SELECT COALESCE(NULLIF(c.stock_code, ''), c.corp_code)
            FROM companies AS c
            WHERE (
                EXISTS (
                    SELECT 1 FROM financials AS f
                    WHERE f.corp_code = c.corp_code AND f.fs_div = 'OFS'
                )
                OR EXISTS (
                    SELECT 1 FROM financial_facts_compact AS f
                    WHERE f.corp_code = c.corp_code AND f.fs_div = 'OFS'
                )
            )
            AND NOT EXISTS (
                SELECT 1 FROM financials AS f
                WHERE f.corp_code = c.corp_code AND f.fs_div = 'CFS'
            )
            AND NOT EXISTS (
                SELECT 1 FROM financial_facts_compact AS f
                WHERE f.corp_code = c.corp_code AND f.fs_div = 'CFS'
            )
            ORDER BY c.corp_code
            LIMIT 1
            """
        ).fetchone()
    return str(row[0]) if row and row[0] else None


def _share_matches(
    amount: Any,
    denominator: Any,
    reported_share: Any,
) -> bool:
    if amount is None or denominator in (None, 0):
        return reported_share is None
    try:
        expected = round(float(amount) / float(denominator) * 100, 1)
        actual = float(reported_share)
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        return False
    return math.isfinite(actual) and actual == expected


def _qsc_denominator_identity(result: dict[str, Any]) -> bool:
    criterion = result.get("qsc_criterion") or {}
    consolidated = result.get("consolidated_totals") or {}
    entities = result.get("subsidiaries") or []
    asset_denominator = consolidated.get("assets_amount_m")
    revenue_denominator = consolidated.get("revenue_amount_m")
    if (
        criterion.get("threshold_pct") != 10.0
        or "asset_share_pct" not in str(criterion.get("basis"))
        or "revenue_share_pct" not in str(criterion.get("basis"))
        or asset_denominator in (None, 0)
        or revenue_denominator in (None, 0)
        or not entities
    ):
        return False
    checked_asset = 0
    for entity in entities:
        asset_amount = entity.get("asset_amount_m")
        revenue_amount = entity.get("revenue_amount_m")
        asset_share = entity.get("asset_share_pct")
        revenue_share = entity.get("revenue_share_pct")
        if asset_amount is not None:
            checked_asset += 1
        if not _share_matches(
            asset_amount,
            asset_denominator,
            asset_share,
        ) or not _share_matches(
            revenue_amount,
            revenue_denominator,
            revenue_share,
        ):
            return False
        available = [
            value for value in (asset_share, revenue_share)
            if value is not None
        ]
        expected_status = (
            "qsc"
            if any(float(value) >= 10.0 for value in available)
            else "not_qsc"
            if len(available) == 2
            else "undetermined"
        )
        if entity.get("qsc_status") != expected_status:
            return False
        expected_is_qsc = {
            "qsc": True,
            "not_qsc": False,
            "undetermined": None,
        }[expected_status]
        if entity.get("is_qsc") is not expected_is_qsc:
            return False
    return checked_asset > 0


def execute_golden_contracts(
    db_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Execute all six approved golden cases through real dispatch handlers."""
    from kreports.mcp.dispatch import dispatch_tool, legacy_result

    database = _safe_existing_db_path(db_path)
    details: dict[str, dict[str, Any]] = {}
    passed = True
    with _bound_explicit_runtime(database):
        def invoke(name: str, arguments: dict[str, Any]):
            nonlocal passed
            envelope = dispatch_tool(name, arguments)
            raw = legacy_result(name, arguments)
            if envelope.data_quality.status == "error":
                passed = False
            return envelope, raw

        samsung, samsung_raw = invoke(
            "get_financial_snapshot", {"company": "005930", "years": 5}
        )
        samsung_peer, _ = invoke(
            "select_peer_group", {"company": "005930"}
        )
        samsung_investor, _ = invoke(
            "get_investor_signals", {"company": "005930"}
        )
        samsung_rows = samsung_raw.get("rows") or []
        fallback_company = _find_ofs_only_company(database)
        fallback_raw: dict[str, Any] = {}
        if fallback_company is not None:
            _, fallback_raw = invoke(
                "get_financial_snapshot",
                {"company": fallback_company, "years": 1},
            )
        fallback_rows = fallback_raw.get("rows") or []
        samsung_provenance = {
            "financial_snapshot": _has_public_provenance(samsung),
            "peer_group": _has_public_provenance(samsung_peer),
            "investor_signals": _has_public_provenance(samsung_investor),
        }
        details["samsung_five_year_investor"] = {
            "covered_years": max(
                len(samsung.data_quality.covered_years),
                _list_size(
                    samsung_raw,
                    "years",
                    "financials",
                    "history",
                    "rows",
                    "row_count",
                ),
            ),
            "cfs_preferred": (
                samsung_raw.get("fs_div") == "CFS"
                and bool(samsung_rows)
                and all(row.get("구분") == "CFS" for row in samsung_rows)
            ),
            "ofs_fallback_explicit": (
                fallback_raw.get("fs_div") == "OFS"
                and bool(fallback_rows)
                and all(row.get("구분") == "OFS" for row in fallback_rows)
            ),
            "provenance_by_pack": samsung_provenance,
            "provenance_or_limitation": all(
                samsung_provenance.values()
            ),
        }

        invoke("get_investor_signals", {"company": "000660"})
        group_envelope, group_raw = invoke(
            "get_subsidiary_auditors", {"company": "000660"}
        )
        group_entities = group_raw.get("subsidiaries") or []
        details["sk_hynix_group_qsc"] = {
            "entity_count": _list_size(
                group_raw, "entities", "subsidiaries", "rows", "total"
            ),
            "relationship_count": max(len(group_entities), 0),
            "qsc_denominator_identity": _qsc_denominator_identity(
                group_raw
            ),
            "provenance_or_limitation": _has_public_provenance(
                group_envelope
            ),
        }

        dcf_candidates, _ = invoke(
            "get_dcf_input_candidates", {"company": "003220"}
        )
        dcf_envelope, dcf_raw = invoke(
            "build_dcf_model_pack",
            {
                "company": "003220",
                "base_year": 2025,
                "revenue_growth": 0.03,
                "operating_margin": 0.1,
                "tax_rate": 0.22,
                "da_to_revenue": 0.03,
                "capex_to_revenue": 0.04,
                "nwc_to_revenue": 0.1,
                "wacc": 0.09,
                "terminal_growth": 0.02,
            },
        )
        details["daewon_five_year_dcf"] = {
            "actuals_assumptions_separate": (
                ("source_actuals" in dcf_raw or "actuals" in dcf_raw)
                and "assumptions" in dcf_raw
                and (
                    dcf_raw.get("source_actuals", dcf_raw.get("actuals"))
                    is not dcf_raw["assumptions"]
                )
            ),
            "five_year_mechanics": (
                len(dcf_raw.get("projections") or []) == 5
                and all(
                    projection.get("formula")
                    for projection in dcf_raw.get("projections") or []
                )
            ),
            "actuals_source_bound": (
                bool(dcf_raw.get("actuals"))
                and all(
                    actual.get("fs_div") == "CFS"
                    and actual.get("source_account_id")
                    and actual.get("source_table")
                    for actual in dcf_raw.get("actuals") or []
                )
            ),
            "judgment_limitations": bool(
                dcf_envelope.data_quality.limitations
            ),
            "provenance_or_limitation": all(
                _has_public_provenance(envelope)
                for envelope in (dcf_candidates, dcf_envelope)
            ),
        }

        opinion_envelope, opinion_raw = invoke(
            "get_audit_history", {"company": "900001"}
        )
        opinion_history = opinion_raw.get("history") or []
        details["modified_opinion"] = {
            "modified_opinion_preserved": "한정" in json.dumps(
                opinion_raw, ensure_ascii=False
            ),
            "receipt_preserved": (
                bool(opinion_history)
                and opinion_history[0].get("접수번호")
                == "20260331000001"
            ),
            "provenance_or_limitation": _has_public_provenance(
                opinion_envelope
            ),
        }

        kam_envelope, kam_raw = invoke(
            "get_audit_report_sections",
            {"company": "900002", "year": 2025, "section_key": "kam"},
        )
        kam_sections = kam_raw.get("sections") or []
        details["multiple_kam"] = {
            "kam_count": _list_size(kam_raw, "sections", "items", "rows"),
            "receipt_ordinal_identity": (
                {section.get("rcept_no") for section in kam_sections}
                == {"20260331000002"}
                and sorted(
                    section.get("ordinal") for section in kam_sections
                )
                == [0, 1]
            ),
            "reason_and_procedure_shapes": all(
                (section.get("kam_analysis") or {}).get(
                    "has_reason_hint"
                )
                and (section.get("kam_analysis") or {}).get(
                    "has_procedure_hint"
                )
                for section in kam_sections
            ),
            "provenance_or_limitation": _has_public_provenance(
                kam_envelope
            ),
        }

        incomplete, _ = invoke(
            "get_investor_signals", {"company": "900003"}
        )
        details["incomplete_company"] = {
            "quality": incomplete.data_quality.status,
            "missing_fields_shape": isinstance(
                incomplete.data_quality.missing_fields,
                list,
            ),
            "explicit_limitations": bool(
                incomplete.data_quality.limitations
            ),
        }
    semantic_passed = (
        details["samsung_five_year_investor"]["covered_years"] >= 5
        and details["samsung_five_year_investor"]["cfs_preferred"]
        and details["samsung_five_year_investor"]["ofs_fallback_explicit"]
        and details["samsung_five_year_investor"][
            "provenance_or_limitation"
        ]
        and details["sk_hynix_group_qsc"]["entity_count"] >= 2
        and details["sk_hynix_group_qsc"]["relationship_count"] >= 2
        and details["sk_hynix_group_qsc"]["qsc_denominator_identity"]
        and details["sk_hynix_group_qsc"]["provenance_or_limitation"]
        and details["daewon_five_year_dcf"]["actuals_assumptions_separate"]
        and details["daewon_five_year_dcf"]["five_year_mechanics"]
        and details["daewon_five_year_dcf"]["actuals_source_bound"]
        and details["daewon_five_year_dcf"]["judgment_limitations"]
        and details["daewon_five_year_dcf"][
            "provenance_or_limitation"
        ]
        and details["modified_opinion"]["modified_opinion_preserved"]
        and details["modified_opinion"]["receipt_preserved"]
        and details["modified_opinion"]["provenance_or_limitation"]
        and details["multiple_kam"]["kam_count"] >= 2
        and details["multiple_kam"]["receipt_ordinal_identity"]
        and details["multiple_kam"]["reason_and_procedure_shapes"]
        and details["multiple_kam"]["provenance_or_limitation"]
        and details["incomplete_company"]["quality"] in {"limited", "missing"}
        and details["incomplete_company"]["missing_fields_shape"]
        and details["incomplete_company"]["explicit_limitations"]
    )
    return {"passed": passed and semantic_passed, "cases": details}


def release_gate_is_ready(report: dict[str, Any]) -> bool:
    """The one public-runtime readiness predicate shared with HTTP readiness."""
    return (
        report.get("ok") is True
        and not list(report.get("required_failures") or [])
    )


def _collect_current_evidence(db_path: Path, profile: str) -> dict[str, Any]:
    """Collect every proof field from the explicit immutable SQLite file."""
    from kreports.quality.release_gate import evaluate_release_gate

    database = _safe_existing_db_path(db_path)
    with _open_immutable_sqlite(database) as connection:
        table_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        dataset_version, dataset_state = _dataset_manifest_state(
            connection,
            table_names,
        )
        schema_version = _schema_version(connection, table_names)
        inline_raw_count = _inline_raw_count(connection, table_names)
        duplicate_blockers = _duplicate_key_blockers(
            connection,
            table_names,
        )
        index_blockers = _index_contract_blockers(
            connection,
            table_names,
        )

    def session_scope():
        return _explicit_session_scope(database)

    gate_report = evaluate_release_gate(
        profile,
        session_scope=session_scope,
        include_legacy_diagnostics=False,
    )
    blockers = list(gate_report.get("required_failures") or [])
    blockers.extend(
        f"missing_required_table:{name}"
        for name in REQUIRED_TABLES
        if name not in table_names
    )
    blockers.extend(index_blockers)
    blockers.extend(duplicate_blockers)
    if inline_raw_count > 0:
        blockers.append("inline_raw_bodies_present")

    coverage_year = gate_report.get("coverage_year")
    with _open_immutable_sqlite(database) as connection:
        grades = _feature_grades(
            connection,
            table_names,
            coverage_year=coverage_year,
        )

    all_tools = run_all_tool_contract(database)
    if not all_tools["passed"]:
        blockers.append("all_tool_contract_failed")
    golden = golden_contract_result()
    if not golden["passed"]:
        blockers.append("golden_contract_invalid")
    blockers = sorted(set(blockers))
    ready = release_gate_is_ready(
        {
            "ok": gate_report.get("ok") is True and not blockers,
            "required_failures": blockers,
        }
    )
    return {
        "artifact_version": ARTIFACT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": {
            "file_name": database.name,
            "byte_count": database.stat().st_size,
            "sha256": _sha256_file(database),
        },
        "schema": {
            "version": schema_version,
            "required_tables": list(REQUIRED_TABLES),
            "required_indexes": list(REQUIRED_INDEXES),
        },
        "dataset": {
            "version": dataset_version,
            "manifest_state": dataset_state,
        },
        "tool_contract": {
            "version": TOOL_CONTRACT_VERSION,
            "tool_count": int(all_tools["checks"]),
            "wire_sha256": _tool_wire_sha256(),
        },
        "release_gate": {
            "profile": profile,
            "passed": ready,
            "blockers": blockers,
            "degraded_features": sorted(
                set(gate_report.get("degraded_features") or [])
            ),
            "coverage_year": coverage_year,
            "feature_coverage": gate_report.get("coverage") or {},
            "feature_grades": grades,
        },
        "inline_raw_count": inline_raw_count,
        "contracts": {
            "all_tools": all_tools,
            "golden_contract_sha256": golden["sha256"],
            "golden_contract_passed": golden["passed"],
        },
    }


def default_runtime_db_path() -> Path:
    """Resolve the configured runtime DB only when it is a local SQLite file."""
    from kreports.config import settings

    prefix = "sqlite:///"
    if not settings.db_url.startswith(prefix):
        raise ValueError(
            "release artifacts require an explicit local SQLite --db path"
        )
    return Path(settings.db_url.removeprefix(prefix)).expanduser()


def evaluate_artifact_readiness(
    db_path: str | os.PathLike[str],
    profile: str = "public_runtime",
) -> dict[str, Any]:
    """Read deployment proof cheaply while checking runtime drift fail-closed."""
    database = _safe_existing_db_path(db_path)
    runtime_failures: list[str] = []
    fingerprint: tuple[Any, ...] | None = None
    try:
        fingerprint = _require_runtime_quiescent_db(database)
    except ReleaseArtifactError as exc:
        runtime_failures.append(str(exc))
    source = _safe_manifest_path(database, None)
    try:
        stored = _read_release_manifest(source)
    except _DuplicateManifestKey as exc:
        return _unavailable_artifact_readiness(
            profile,
            f"duplicate_manifest_key:{exc.key}",
        )
    except ReleaseArtifactError as exc:
        return _unavailable_artifact_readiness(profile, str(exc))
    except FileNotFoundError:
        return _unavailable_artifact_readiness(
            profile,
            "release_artifact_missing",
        )
    except (OSError, ValueError):
        return _unavailable_artifact_readiness(
            profile,
            "invalid_release_manifest",
        )

    if stored.release_gate.profile != profile:
        runtime_failures.append("release_artifact_profile_mismatch")
    if stored.database.file_name != database.name:
        runtime_failures.append("database_filename_mismatch")
    if stored.database.byte_count != database.stat().st_size:
        runtime_failures.append("database_size_mismatch")
    if fingerprint is not None:
        current_digest = _cached_runtime_db_digest(
            database,
            fingerprint,
        )
        if stored.database.sha256 != current_digest:
            runtime_failures.append("database_sha256_mismatch")
        try:
            if _require_runtime_quiescent_db(database) != fingerprint:
                runtime_failures.append(
                    "database_changed_during_readiness_check"
                )
        except ReleaseArtifactError as exc:
            runtime_failures.append(str(exc))
    if (
        stored.tool_contract.tool_count != FROZEN_TOOL_COUNT
        or stored.tool_contract.wire_sha256 != _tool_wire_sha256()
    ):
        runtime_failures.append("tool_contract_drift")
    golden = golden_contract_result()
    if (
        not golden["passed"]
        or stored.contracts.golden_contract_sha256 != golden["sha256"]
        or not stored.contracts.golden_contract_passed
    ):
        runtime_failures.append("golden_contract_drift")
    if (
        not stored.contracts.all_tools.passed
        or stored.contracts.all_tools.checks != FROZEN_TOOL_COUNT
    ):
        runtime_failures.append("all_tool_contract_failed")

    gate = stored.release_gate
    required_failures = sorted(
        set(gate.blockers) | set(runtime_failures)
    )
    return {
        "ok": gate.passed and not required_failures,
        "profile": gate.profile,
        "schema_version": stored.schema_evidence.version,
        "dataset_version": stored.dataset.version,
        "required_failures": required_failures,
        "degraded_features": gate.degraded_features,
        "tool_count": stored.tool_contract.tool_count,
        "coverage_year": gate.coverage_year,
        "coverage": gate.feature_coverage,
        "feature_grades": gate.feature_grades,
    }


def _cached_runtime_db_digest(
    database: Path,
    fingerprint: tuple[Any, ...],
) -> str:
    identity = (str(database), *fingerprint[:5])
    with _RUNTIME_DIGEST_LOCK:
        cached = _RUNTIME_DIGEST_CACHE.get(identity)
        if cached is not None:
            return cached
        digest = _sha256_file(database)
        if len(_RUNTIME_DIGEST_CACHE) >= 8:
            oldest = next(iter(_RUNTIME_DIGEST_CACHE))
            del _RUNTIME_DIGEST_CACHE[oldest]
        _RUNTIME_DIGEST_CACHE[identity] = digest
        return digest


def _unavailable_artifact_readiness(
    profile: str,
    failure: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "profile": profile,
        "schema_version": "unknown",
        "dataset_version": "unknown",
        "required_failures": [failure],
        "degraded_features": [],
        "tool_count": FROZEN_TOOL_COUNT,
        "coverage_year": None,
        "coverage": {},
        "feature_grades": {},
    }


def _proof_fingerprint(db_path: Path) -> tuple[Any, ...]:
    stat = db_path.stat()
    sidecars: list[tuple[str, int, int, str]] = []
    for suffix in ("-wal", "-shm"):
        path = db_path.with_name(f"{db_path.name}{suffix}")
        if not path.exists():
            sidecars.append((suffix, 0, 0, ""))
            continue
        sidecar_stat = path.stat()
        digest = _sha256_file(path) if sidecar_stat.st_size else ""
        sidecars.append(
            (
                suffix,
                sidecar_stat.st_size,
                sidecar_stat.st_mtime_ns,
                digest,
            )
        )
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        tuple(sidecars),
    )


def _require_quiescent_db(db_path: Path) -> tuple[Any, ...]:
    fingerprint = _proof_fingerprint(db_path)
    for suffix, size, _mtime, _digest in fingerprint[-1]:
        if suffix == "-wal" and size > 0:
            raise ReleaseArtifactError("nonempty_wal")
    return fingerprint


def _require_runtime_quiescent_db(db_path: Path) -> tuple[Any, ...]:
    """Fingerprint readiness inputs without reading sidecar contents."""
    stat = db_path.stat()
    sidecars: list[tuple[str, int, int, int]] = []
    for suffix in ("-wal", "-shm"):
        path = db_path.with_name(f"{db_path.name}{suffix}")
        if not path.exists():
            sidecars.append((suffix, 0, 0, 0))
            continue
        sidecar_stat = path.stat()
        if suffix == "-wal" and sidecar_stat.st_size > 0:
            raise ReleaseArtifactError("nonempty_wal")
        sidecars.append(
            (
                suffix,
                sidecar_stat.st_size,
                sidecar_stat.st_mtime_ns,
                sidecar_stat.st_ctime_ns,
            )
        )
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
        tuple(sidecars),
    )


def _atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    pre_replace: Any = None,
) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode()
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if pre_replace is not None:
            pre_replace()
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


class _DuplicateManifestKey(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _reject_duplicate_pairs(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateManifestKey(key)
        result[key] = value
    return result


def _read_release_manifest(path: Path) -> ReleaseManifest:
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ReleaseArtifactError("manifest_too_large")
    payload = json.loads(
        path.read_text(),
        object_pairs_hook=_reject_duplicate_pairs,
    )
    return ReleaseManifest.model_validate(payload)


def build_release_manifest(
    db_path: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str] | None = None,
    *,
    profile: str = "public_runtime",
) -> Path:
    """Build evidence even when the live gate is blocked."""
    database = _safe_existing_db_path(db_path)
    output = _safe_manifest_path(database, manifest_path)
    before = _require_quiescent_db(database)
    payload = _collect_current_evidence(database, profile)
    payload["database"] = {
        "file_name": database.name,
        "byte_count": database.stat().st_size,
        "sha256": _sha256_file(database),
    }
    manifest = ReleaseManifest.model_validate(payload)

    def ensure_stable() -> None:
        if _require_quiescent_db(database) != before:
            raise ReleaseArtifactError("database_changed_during_proof")

    _atomic_write_json(
        output,
        manifest.model_dump(mode="json", by_alias=True),
        pre_replace=ensure_stable,
    )
    return output


def verify_release_artifact(
    db_path: str | os.PathLike[str],
    manifest_path: str | os.PathLike[str] | None = None,
    *,
    profile: str = "public_runtime",
) -> VerificationResult:
    """Recompute evidence and reject stored-current drift or blockers."""
    database = _safe_existing_db_path(db_path)
    source = _safe_manifest_path(database, manifest_path)
    try:
        before = _require_quiescent_db(database)
    except ReleaseArtifactError as exc:
        return VerificationResult(ok=False, failures=[str(exc)])
    try:
        stored = _read_release_manifest(source)
    except _DuplicateManifestKey as exc:
        return VerificationResult(
            ok=False,
            failures=[f"duplicate_manifest_key:{exc.key}"],
        )
    except ReleaseArtifactError as exc:
        return VerificationResult(ok=False, failures=[str(exc)])
    except (OSError, ValueError) as exc:
        return VerificationResult(
            ok=False,
            failures=[f"invalid_release_manifest:{type(exc).__name__}"],
        )

    failures: list[str] = []
    current_size = database.stat().st_size
    current_digest = _sha256_file(database)
    if stored.database.file_name != database.name:
        failures.append("database_filename_mismatch")
    if stored.database.byte_count != current_size:
        failures.append("database_size_mismatch")
    if stored.database.sha256 != current_digest:
        failures.append("database_sha256_mismatch")

    try:
        current_payload = _collect_current_evidence(database, profile)
        current_payload["database"] = {
            "file_name": database.name,
            "byte_count": current_size,
            "sha256": current_digest,
        }
        current = ReleaseManifest.model_validate(current_payload)
    except Exception as exc:
        failures.append(f"current_evidence_unavailable:{type(exc).__name__}")
    else:
        if current.release_gate.blockers:
            failures.extend(
                f"release_gate_blocked:{blocker}"
                for blocker in current.release_gate.blockers
            )
        stored_data = stored.model_dump(
            mode="json",
            by_alias=True,
            exclude={"generated_at"},
        )
        current_data = current.model_dump(
            mode="json",
            by_alias=True,
            exclude={"generated_at"},
        )
        for field in (
            "schema",
            "dataset",
            "tool_contract",
            "release_gate",
            "inline_raw_count",
            "contracts",
        ):
            if stored_data[field] != current_data[field]:
                failures.append(f"{field}_evidence_mismatch")
    try:
        if _require_quiescent_db(database) != before:
            failures.append("database_changed_during_verification")
    except ReleaseArtifactError as exc:
        failures.append(str(exc))
    return VerificationResult(
        ok=not failures,
        failures=sorted(set(failures)),
        diagnostics=_verification_diagnostics(failures),
    )
