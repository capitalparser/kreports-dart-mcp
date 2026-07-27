"""Deterministic, fail-closed release evidence for a runtime SQLite artifact."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
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
FROZEN_TOOL_COUNT = 32
FROZEN_TOOL_WIRE_SHA256 = (
    "055f54993bf45f2e4a1388642871d09c1e2f45fc0b5fde1e83228bb910b38339"
)
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


class VerificationResult(_StrictModel):
    ok: StrictBool
    failures: list[StrictStr]


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


def run_all_tool_contract() -> dict[str, bool | int]:
    """Recompute the catalog-wide strict-input and frozen-wire contract."""
    from kreports.mcp.catalog import TOOL_CATALOG
    from kreports.mcp.contracts import AnswerEnvelopeV1
    from kreports.mcp.dispatch import dispatch_tool

    passed = (
        len(TOOL_CATALOG) == FROZEN_TOOL_COUNT
        and all(
            spec.input_model.model_config.get("extra") == "forbid"
            for spec in TOOL_CATALOG.values()
        )
        and _tool_wire_sha256() == FROZEN_TOOL_WIRE_SHA256
    )
    if passed:
        for name in TOOL_CATALOG:
            envelope = dispatch_tool(
                name,
                {"__release_contract_unknown__": True},
            )
            if not (
                isinstance(envelope, AnswerEnvelopeV1)
                and envelope.tool_name == name
                and envelope.answer.strip()
                and envelope.data_quality.status == "error"
                and "__release_contract_unknown__" in envelope.answer
            ):
                passed = False
                break
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
    row = connection.execute(
        "SELECT * FROM dataset_manifest "
        "ORDER BY generated_at DESC, manifest_id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "unknown", {"status": "missing"}
    state = {key: row[key] for key in row.keys()}
    raw_quality = state.get("quality_snapshot_json")
    if isinstance(raw_quality, str):
        try:
            state["quality_snapshot"] = json.loads(raw_quality)
        except json.JSONDecodeError:
            state["quality_snapshot"] = {"status": "malformed"}
        del state["quality_snapshot_json"]
    return str(state.get("dataset_version") or "unknown"), state


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
    derived_filter = (
        " AND COALESCE(content_type, '') != 'derived_report_sections'"
        if "content_type" in columns
        else ""
    )
    return int(
        connection.execute(
            "SELECT COUNT(*) FROM source_documents "
            "WHERE raw_content IS NOT NULL AND length(raw_content) > 0"
            + derived_filter
        ).fetchone()[0]
        or 0
    )


def _feature_grades(
    connection: sqlite3.Connection,
    table_names: set[str],
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
        rows = connection.execute(
            f'SELECT "{column}", COUNT(*) FROM company_year_quality '
            f'GROUP BY "{column}" ORDER BY "{column}"'
        ).fetchall()
        result[public_name] = {
            str(row[0]): int(row[1])
            for row in rows
            if row[0] is not None
        }
    return result


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


def _golden_contract_digest() -> tuple[str, bool]:
    path = Path(__file__).resolve().parents[1] / "tests" / "golden" / "companies.yaml"
    if not path.is_file() or path.is_symlink():
        return "0" * 64, False
    return _sha256_file(path), True


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
        index_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='index'"
            )
        }
        dataset_version, dataset_state = _dataset_manifest_state(
            connection,
            table_names,
        )
        schema_version = _schema_version(connection, table_names)
        inline_raw_count = _inline_raw_count(connection, table_names)
        grades = _feature_grades(connection, table_names)
        duplicate_blockers = _duplicate_key_blockers(
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
    blockers.extend(
        f"missing_required_index:{name}"
        for name in REQUIRED_INDEXES
        if name not in index_names
    )
    blockers.extend(duplicate_blockers)
    if inline_raw_count > 0:
        blockers.append("inline_raw_bodies_present")

    all_tools = run_all_tool_contract()
    if not all_tools["passed"]:
        blockers.append("all_tool_contract_failed")
    golden_digest, golden_available = _golden_contract_digest()
    if not golden_available:
        blockers.append("golden_contract_missing")
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
            "coverage_year": gate_report.get("coverage_year"),
            "feature_coverage": gate_report.get("coverage") or {},
            "feature_grades": grades,
        },
        "inline_raw_count": inline_raw_count,
        "contracts": {
            "all_tools": all_tools,
            "golden_contract_sha256": golden_digest,
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
    )
