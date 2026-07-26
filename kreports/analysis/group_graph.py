"""Immutable, receipt-bound group-audit graph read model."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import re
from typing import Any, Iterable

from sqlalchemy import create_engine, inspect, text

import kreports.db.engine as _engine_module


QSC_THRESHOLD_PCT = 10.0
_QSC_STATUSES = frozenset({"qsc", "not_qsc", "undetermined"})
_RESOLUTION_STATUSES = frozenset({"resolved", "unresolved", "ambiguous"})
_RESOLUTION_REASONS = frozenset({
    "corp_code", "explicit_corp_code", "unique_exact_normalized_name",
    "unlisted", "ambiguous_exact_normalized_name",
    "unmatched_exact_normalized_name", "parent_corp_code",
    "parent_not_registered", "synthetic_parent", "orphan_parent",
    "ambiguous_parent_name", "unresolved",
})
_METRIC_KEYS = frozenset({"assets", "revenue"})
_ELIMINATION_BASES = frozenset({
    "before_elimination", "after_elimination", "not_disclosed",
})


class GroupGraphUnavailable(RuntimeError):
    """Canonical graph cannot be read safely from the configured database."""


def _required_text(name: str, value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{name} must be a non-empty string")


def _optional_text(name: str, value: Any) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise TypeError(f"{name} must be a non-empty string or None")


def _finite(name: str, value: Any, *, optional: bool = True) -> None:
    if value is None and optional:
        return
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise TypeError(f"{name} must be a finite number")


def _tuple_of_text(name: str, value: Any) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    if any(not isinstance(item, str) or not item for item in value):
        raise TypeError(f"{name} entries must be non-empty strings")


@dataclass(frozen=True)
class QscResult:
    status: str
    basis: tuple[str, ...]
    threshold_pct: float = QSC_THRESHOLD_PCT
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in _QSC_STATUSES:
            raise ValueError("invalid QSC status")
        _tuple_of_text("basis", self.basis)
        _tuple_of_text("evidence_refs", self.evidence_refs)
        _finite("threshold_pct", self.threshold_pct, optional=False)
        if float(self.threshold_pct) != QSC_THRESHOLD_PCT:
            raise ValueError("QSC threshold must be 10.0")
        if self.status == "not_qsc" and self.basis:
            raise ValueError("not_qsc cannot have a threshold-crossing basis")


def _validated_share(name: str, value: Any) -> float | None:
    if value is None:
        return None
    _finite(name, value, optional=False)
    result = float(value)
    if not 0 <= result <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
    return result


def classify_qsc(
    asset_share_pct: float | None,
    revenue_share_pct: float | None,
    *,
    evidence_refs: Iterable[str] = (),
) -> QscResult:
    """Apply the approved OR rule without treating missing evidence as zero."""
    asset = _validated_share("asset_share_pct", asset_share_pct)
    revenue = _validated_share("revenue_share_pct", revenue_share_pct)
    basis = tuple(
        label
        for label, share in (
            ("asset_share_pct>=10.0", asset),
            ("revenue_share_pct>=10.0", revenue),
        )
        if share is not None and share >= QSC_THRESHOLD_PCT
    )
    refs = tuple(sorted(set(evidence_refs)))
    if basis:
        return QscResult("qsc", basis, evidence_refs=refs)
    if asset is not None and revenue is not None:
        return QscResult("not_qsc", (), evidence_refs=refs)
    return QscResult("undetermined", (), evidence_refs=refs)


@dataclass(frozen=True)
class GroupEntity:
    entity_key: str
    original_name: str
    normalized_name: str
    resolution_status: str
    resolution_reason: str
    listed_state: str | None
    source_rcept_no: str
    source_table: str
    source_ordinal: int
    resolved_corp_code: str | None = None
    stock_code: str | None = None
    market: str | None = None
    component_auditor_name: str | None = None
    component_auditor_year: int | None = None
    component_auditor_rcept_no: str | None = None
    auditor_gap_reason: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "entity_key", "original_name", "normalized_name",
            "resolution_reason", "source_rcept_no", "source_table",
        ):
            _required_text(name, getattr(self, name))
        if self.resolution_status not in _RESOLUTION_STATUSES:
            raise ValueError("invalid resolution_status")
        if self.resolution_reason not in _RESOLUTION_REASONS:
            raise ValueError("invalid resolution_reason")
        if not isinstance(self.source_ordinal, int) or isinstance(self.source_ordinal, bool):
            raise TypeError("source_ordinal must be an integer")
        for name in (
            "listed_state", "resolved_corp_code", "stock_code", "market",
            "component_auditor_name", "component_auditor_rcept_no",
            "auditor_gap_reason",
        ):
            _optional_text(name, getattr(self, name))
        if self.component_auditor_year is not None and (
            not isinstance(self.component_auditor_year, int)
            or isinstance(self.component_auditor_year, bool)
        ):
            raise TypeError("component_auditor_year must be an integer or None")
        if self.resolution_status != "resolved" and self.resolved_corp_code:
            raise ValueError("unresolved entities cannot carry a corp code")


@dataclass(frozen=True)
class GroupRelationship:
    relationship_key: str
    parent_entity_key: str
    child_entity_key: str
    relation_type: str
    ownership_pct: float | None
    effective_year: int
    source_rcept_no: str
    source_table: str
    source_ordinal: int

    def __post_init__(self) -> None:
        for name in (
            "relationship_key", "parent_entity_key", "child_entity_key",
            "relation_type", "source_rcept_no", "source_table",
        ):
            _required_text(name, getattr(self, name))
        if self.parent_entity_key == self.child_entity_key:
            raise ValueError("relationship cannot be a self edge")
        _finite("ownership_pct", self.ownership_pct)
        if self.ownership_pct is not None and not 0 <= float(self.ownership_pct) <= 100:
            raise ValueError("ownership_pct must be between 0 and 100")
        for name in ("effective_year", "source_ordinal"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an integer")


@dataclass(frozen=True)
class ComponentMetric:
    metric_identity: str
    entity_key: str
    metric_key: str
    amount: float | None
    unit: str | None
    numerator_source_rcept_no: str | None
    numerator_source_table: str | None
    denominator_amount: float | None
    denominator_unit: str | None
    denominator_source_rcept_no: str | None
    denominator_source_table: str | None
    fs_div: str | None
    period: str | None
    elimination_basis: str | None
    share_pct: float | None
    qsc_status: str
    qsc_basis: tuple[str, ...]
    qsc_threshold_pct: float
    quality_status: str
    gap_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("metric_identity", "entity_key", "quality_status"):
            _required_text(name, getattr(self, name))
        if self.metric_key not in _METRIC_KEYS:
            raise ValueError("metric_key must be assets or revenue")
        for name in ("amount", "denominator_amount", "share_pct", "qsc_threshold_pct"):
            _finite(name, getattr(self, name), optional=name != "qsc_threshold_pct")
        if self.share_pct is not None and not 0 <= float(self.share_pct) <= 100:
            raise ValueError("share_pct must be between 0 and 100")
        if self.qsc_status not in _QSC_STATUSES:
            raise ValueError("invalid qsc_status")
        _tuple_of_text("qsc_basis", self.qsc_basis)
        if self.qsc_status == "qsc" and not self.qsc_basis:
            raise ValueError("qsc requires a threshold-crossing basis")
        if self.qsc_status != "qsc" and self.qsc_basis:
            raise ValueError("only qsc may carry a threshold-crossing basis")
        if float(self.qsc_threshold_pct) != QSC_THRESHOLD_PCT:
            raise ValueError("QSC threshold must be 10.0")
        if self.fs_div not in {None, "CFS", "OFS"}:
            raise ValueError("fs_div must be CFS, OFS, or None")
        if self.elimination_basis not in {None, *_ELIMINATION_BASES}:
            raise ValueError("invalid elimination_basis")
        for name in (
            "unit", "numerator_source_rcept_no", "numerator_source_table",
            "denominator_unit", "denominator_source_rcept_no",
            "denominator_source_table", "period", "gap_reason",
        ):
            _optional_text(name, getattr(self, name))


def compute_component_share(
    amount: float | None,
    unit: str | None,
    period: str | None,
    fs_div: str | None,
    elimination_basis: str | None,
    numerator_rcept_no: str | None,
    denominator_amount: float | None,
    denominator_unit: str | None,
    denominator_period: str | None,
    denominator_fs_div: str | None,
    denominator_elimination_basis: str | None,
    denominator_rcept_no: str | None,
) -> tuple[float | None, str | None]:
    """Return a share only for comparable, receipt-identified evidence."""
    try:
        _finite("amount", amount, optional=False)
        _finite("denominator_amount", denominator_amount, optional=False)
    except TypeError:
        return None, "non_finite_or_missing_amount"
    if not numerator_rcept_no or not denominator_rcept_no:
        return None, "incomplete_evidence_identity"
    if not unit or not denominator_unit or unit != denominator_unit:
        return None, "unit_mismatch"
    if not period or not denominator_period or period != denominator_period:
        return None, "period_mismatch"
    if not fs_div or not denominator_fs_div or fs_div != denominator_fs_div:
        return None, "fs_div_mismatch"
    if (
        not elimination_basis
        or not denominator_elimination_basis
        or elimination_basis != denominator_elimination_basis
    ):
        return None, "elimination_basis_mismatch"
    if elimination_basis == "not_disclosed":
        return None, "elimination_basis_not_disclosed"
    denominator = float(denominator_amount)
    if denominator <= 0:
        return None, "denominator_not_positive"
    share = float(amount) / denominator * 100
    if not math.isfinite(share) or not 0 <= share <= 100:
        return None, "invalid_share"
    return round(share, 6), None


_FOOTNOTE = re.compile(r"(?:\[\s*주\s*\d+\s*\]|\(\s*주\s*\d+(?:\s*,\s*\d+)*\s*\))")
_KOREAN_LEGAL_FORM = re.compile(
    r"(?:주식회사|유한회사|유한책임회사|\(\s*주\s*\)|㈜)",
    re.IGNORECASE,
)
_ENGLISH_LEGAL_SUFFIX = re.compile(
    r"(?:\bco\.?\s*,?\s*)?(?:ltd\.?|limited|corporation|corp\.?|inc\.?|llc)\s*$",
    re.IGNORECASE,
)


def normalize_entity_name(value: Any) -> str:
    """Normalize disclosed identity noise without retaining a display rewrite."""
    normalized = _FOOTNOTE.sub("", str(value or "").strip().lower())
    normalized = _KOREAN_LEGAL_FORM.sub("", normalized)
    normalized = _ENGLISH_LEGAL_SUFFIX.sub("", normalized)
    return re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)


def _opaque_key(*parts: Any, prefix: str = "e") -> str:
    payload = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode()).hexdigest()[:20]}"


@dataclass(frozen=True)
class GroupGraph:
    parent_name: str
    year: int | None
    entities: tuple[GroupEntity, ...]
    relationships: tuple[GroupRelationship, ...]
    metrics: tuple[ComponentMetric, ...] = ()
    limitations: tuple[str, ...] = ()
    truncated: bool = False

    def __post_init__(self) -> None:
        _required_text("parent_name", self.parent_name)
        if self.year is not None and (
            not isinstance(self.year, int) or isinstance(self.year, bool)
        ):
            raise TypeError("year must be an integer or None")
        for name, kind in (
            ("entities", GroupEntity),
            ("relationships", GroupRelationship),
            ("metrics", ComponentMetric),
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple) or any(not isinstance(x, kind) for x in value):
                raise TypeError(f"{name} must be a tuple of {kind.__name__}")
        _tuple_of_text("limitations", self.limitations)
        keys = [entity.entity_key for entity in self.entities]
        if len(keys) != len(set(keys)):
            raise ValueError("entity keys must be unique")
        known = set(keys)
        for edge in self.relationships:
            if edge.parent_entity_key not in known or edge.child_entity_key not in known:
                raise ValueError("relationship references an unknown entity")
            if self.year is not None and edge.effective_year != self.year:
                raise ValueError("relationship year does not match graph year")
        if any(metric.entity_key not in known for metric in self.metrics):
            raise ValueError("metric references an unknown entity")

    @classmethod
    def from_rows(cls, parent_name: str, rows: list[dict]) -> "GroupGraph":
        """Build deterministic paths from direct-parent rows, never guessed edges."""
        source_sorted_rows = sorted(
            (dict(row) for row in rows),
            key=lambda row: (
                str(row.get("source_rcept_no") or "synthetic"),
                int(row.get("source_ordinal") or 0),
                str(row.get("parent") or ""),
                str(row.get("child") or ""),
            ),
        )
        # Source order is not a graph order. Resolve all currently reachable
        # direct-parent rows in deterministic waves, then retain disconnected
        # or cyclic rows as explicit orphan evidence.
        pending = list(source_sorted_rows)
        sorted_rows: list[dict] = []
        reachable_names = {normalize_entity_name(parent_name)}
        while pending:
            ready = [
                row for row in pending
                if normalize_entity_name(row.get("parent")) in reachable_names
            ]
            if not ready:
                sorted_rows.extend(pending)
                break
            for row in ready:
                pending.remove(row)
                sorted_rows.append(row)
                reachable_names.add(normalize_entity_name(row.get("child")))
        year_values = {int(row["effective_year"]) for row in sorted_rows if row.get("effective_year") is not None}
        year = next(iter(year_values)) if len(year_values) == 1 else None
        limitations: set[str] = set()
        if len(year_values) > 1:
            limitations.add("mixed_effective_years")
        root_key = _opaque_key("root", parent_name)
        entities: dict[str, GroupEntity] = {
            root_key: GroupEntity(
                root_key, parent_name, normalize_entity_name(parent_name),
                "unresolved", "synthetic_parent", None, "synthetic", "root", 0,
            )
        }
        name_keys: dict[str, list[str]] = {normalize_entity_name(parent_name): [root_key]}
        edges: list[GroupRelationship] = []
        signatures: dict[tuple[str, str], tuple[Any, ...]] = {}
        for position, row in enumerate(sorted_rows):
            parent = str(row.get("parent") or "").strip()
            child = str(row.get("child") or "").strip()
            if not parent or not child:
                limitations.add("malformed_relationship")
                continue
            receipt = str(row.get("source_rcept_no") or "synthetic")
            table = str(row.get("source_table") or "rows")
            ordinal = int(row.get("source_ordinal", position))
            parent_norm = normalize_entity_name(parent)
            child_norm = normalize_entity_name(child)
            candidates = name_keys.get(parent_norm, [])
            if parent_norm == normalize_entity_name(parent_name):
                parent_key = root_key
            elif len(candidates) == 1:
                parent_key = candidates[0]
            else:
                parent_key = _opaque_key(receipt, table, ordinal, "parent", parent)
                if parent_key not in entities:
                    entities[parent_key] = GroupEntity(
                        parent_key, parent, parent_norm, "unresolved",
                        "orphan_parent" if not candidates else "ambiguous_parent_name",
                        None, receipt, table, ordinal,
                    )
                    name_keys.setdefault(parent_norm, []).append(parent_key)
                limitations.add("orphan_edge" if not candidates else "multiple_parent_ambiguity")
            child_key = str(row.get("child_entity_key") or "")
            if not child_key:
                child_key = (
                    root_key
                    if child_norm == normalize_entity_name(parent_name)
                    else _opaque_key(receipt, table, ordinal, "child", child)
                )
            if child_key not in entities:
                resolved_code = row.get("resolved_corp_code")
                resolution_status = "resolved" if resolved_code else str(
                    row.get("resolution_status") or "unresolved"
                )
                entities[child_key] = GroupEntity(
                    child_key, child, child_norm, resolution_status,
                    str(row.get("resolution_reason") or "unresolved"),
                    row.get("listed_state"), receipt, table, ordinal,
                    resolved_corp_code=resolved_code,
                )
                name_keys.setdefault(child_norm, []).append(child_key)
            signature_key = (parent_key, child_key)
            signature = (row.get("ownership_pct"), row.get("relation_type") or "subsidiary")
            if signature_key in signatures and signatures[signature_key] != signature:
                limitations.add("contradictory_duplicate_edge")
                continue
            signatures[signature_key] = signature
            effective_year = int(row.get("effective_year") or year or 0)
            edges.append(GroupRelationship(
                _opaque_key(receipt, table, ordinal, parent_key, child_key, prefix="r"),
                parent_key, child_key,
                str(row.get("relation_type") or "subsidiary"),
                row.get("ownership_pct"), effective_year, receipt, table, ordinal,
            ))
        _graph_limitations(edges, limitations)
        return cls(
            parent_name, year,
            tuple(sorted(entities.values(), key=lambda item: item.entity_key)),
            tuple(sorted(edges, key=lambda item: item.relationship_key)),
            limitations=tuple(sorted(limitations)),
        )

    def path_to(self, name: str) -> tuple[str, ...]:
        targets = [
            entity.entity_key for entity in self.entities
            if entity.original_name == name
        ]
        roots = sorted(
            set(entity.entity_key for entity in self.entities)
            - {edge.child_entity_key for edge in self.relationships}
        )
        by_parent: dict[str, list[str]] = {}
        for edge in self.relationships:
            by_parent.setdefault(edge.parent_entity_key, []).append(edge.child_entity_key)
        names = {entity.entity_key: entity.original_name for entity in self.entities}
        queue = [(root, (root,)) for root in roots]
        while queue:
            node, path = queue.pop(0)
            if node in targets:
                return tuple(names[key] for key in path)
            for child in sorted(by_parent.get(node, [])):
                if child not in path:
                    queue.append((child, (*path, child)))
        return ()


def _graph_limitations(
    edges: list[GroupRelationship], limitations: set[str],
) -> None:
    parents: dict[str, set[str]] = {}
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        parents.setdefault(edge.child_entity_key, set()).add(edge.parent_entity_key)
        adjacency.setdefault(edge.parent_entity_key, set()).add(edge.child_entity_key)
    if any(len(values) > 1 for values in parents.values()):
        limitations.add("multiple_parent_ambiguity")
    nodes = set(adjacency) | set(parents)
    roots = nodes - set(parents)
    if len(roots) > 1:
        limitations.add("multiple_roots")

    def visits(node: str, active: set[str], done: set[str]) -> bool:
        if node in active:
            return True
        if node in done:
            return False
        active.add(node)
        found = any(visits(child, active, done) for child in adjacency.get(node, ()))
        active.remove(node)
        done.add(node)
        return found

    done: set[str] = set()
    if any(visits(node, set(), done) for node in sorted(adjacency)):
        limitations.add("cycle_detected")


_REQUIRED_COLUMNS = {
    "group_entities": {
        "parent_corp_code", "effective_year", "entity_key", "original_name",
        "normalized_name", "resolution_status", "resolution_reason",
        "source_rcept_no", "source_table", "source_ordinal",
    },
    "group_relationships": {
        "parent_corp_code", "effective_year", "relationship_key",
        "parent_entity_key", "child_entity_key", "relation_type",
        "source_rcept_no", "source_table", "source_ordinal",
    },
    "group_component_metrics": {
        "parent_corp_code", "effective_year", "metric_identity", "entity_key",
        "metric_key", "qsc_status", "qsc_basis", "qsc_threshold_pct",
        "quality_status",
    },
}


@contextmanager
def _group_read_engine():
    source_engine = _engine_module.engine
    if source_engine.dialect.name == "sqlite":
        database = source_engine.url.database
        if database not in {None, "", ":memory:"}:
            path = Path(str(database)).expanduser().resolve()
            if not path.is_file():
                raise GroupGraphUnavailable("runtime_db_unavailable")
            wal = Path(f"{path}-wal")
            if wal.exists() and wal.stat().st_size > 0:
                raise GroupGraphUnavailable("runtime_db_unavailable:uncheckpointed_wal")
            readonly = create_engine(
                f"sqlite:///file:{path.as_posix()}?mode=ro&immutable=1&uri=true",
                connect_args={"check_same_thread": False},
            )
            try:
                _validate_schema(readonly)
                yield readonly
            finally:
                readonly.dispose()
            return
    _validate_schema(source_engine)
    yield source_engine


def _validate_schema(read_engine) -> None:
    try:
        inspector = inspect(read_engine)
        tables = set(inspector.get_table_names())
        missing = sorted(set(_REQUIRED_COLUMNS) - tables)
        if missing:
            raise GroupGraphUnavailable(f"missing_schema:{','.join(missing)}")
        for table_name, required in _REQUIRED_COLUMNS.items():
            columns = {str(item["name"]) for item in inspector.get_columns(table_name)}
            missing_columns = sorted(required - columns)
            if missing_columns:
                raise GroupGraphUnavailable(
                    f"missing_columns:{table_name}:{','.join(missing_columns)}"
                )
    except GroupGraphUnavailable:
        raise
    except Exception as exc:
        raise GroupGraphUnavailable(
            f"runtime_db_unavailable:{type(exc).__name__}"
        ) from exc


def _entity_from_row(row: dict[str, Any], *, requested_year: int) -> GroupEntity:
    auditor_year = row.get("component_auditor_year")
    auditor_name = row.get("component_auditor_name")
    auditor_receipt = row.get("component_auditor_rcept_no")
    auditor_gap = row.get("auditor_gap_reason")
    if auditor_name and auditor_year != requested_year:
        auditor_name = None
        auditor_receipt = None
        auditor_year = None
        auditor_gap = "component_auditor_year_mismatch"
    return GroupEntity(
        entity_key=str(row["entity_key"]),
        original_name=str(row["original_name"]),
        normalized_name=str(row["normalized_name"]),
        resolution_status=str(row["resolution_status"]),
        resolution_reason=str(row["resolution_reason"]),
        listed_state=row.get("listed_state"),
        source_rcept_no=str(row["source_rcept_no"]),
        source_table=str(row["source_table"]),
        source_ordinal=int(row["source_ordinal"]),
        resolved_corp_code=row.get("resolved_corp_code"),
        stock_code=row.get("stock_code"),
        market=row.get("market"),
        component_auditor_name=auditor_name,
        component_auditor_year=auditor_year,
        component_auditor_rcept_no=auditor_receipt,
        auditor_gap_reason=auditor_gap,
    )


def build_group_graph(parent_corp_code: str, year: int) -> GroupGraph:
    """Read one exact-year canonical graph using three bounded bulk queries."""
    _required_text("parent_corp_code", parent_corp_code)
    if not isinstance(year, int) or isinstance(year, bool):
        raise TypeError("year must be an integer")
    with _group_read_engine() as read_engine:
        with read_engine.connect() as conn:
            entity_rows = conn.execute(text("""
                SELECT * FROM group_entities
                WHERE parent_corp_code=:corp_code AND effective_year=:year
                ORDER BY source_rcept_no DESC, source_table, source_ordinal, entity_key
            """), {"corp_code": parent_corp_code, "year": year}).mappings().all()
            relationship_rows = conn.execute(text("""
                SELECT * FROM group_relationships
                WHERE parent_corp_code=:corp_code AND effective_year=:year
                ORDER BY source_rcept_no DESC, source_table, source_ordinal, relationship_key
            """), {"corp_code": parent_corp_code, "year": year}).mappings().all()
            metric_rows = conn.execute(text("""
                SELECT * FROM group_component_metrics
                WHERE parent_corp_code=:corp_code AND effective_year=:year
                ORDER BY metric_identity
            """), {"corp_code": parent_corp_code, "year": year}).mappings().all()
    if not entity_rows:
        return GroupGraph(parent_corp_code, year, (), (), limitations=("canonical_graph_missing",))
    receipts = sorted(
        {str(row["source_rcept_no"]) for row in entity_rows},
        reverse=True,
    )
    selected_receipt = receipts[0]
    entity_rows = [
        row for row in entity_rows
        if str(row["source_rcept_no"]) == selected_receipt
    ]
    relationship_rows = [
        row for row in relationship_rows
        if str(row["source_rcept_no"]) == selected_receipt
    ]
    metric_rows = [
        row for row in metric_rows
        if (
            str(row["metric_identity"]).startswith(f"{selected_receipt}:")
            or str(row["numerator_source_rcept_no"] or "") == selected_receipt
        )
    ]
    limitations: set[str] = set()
    if len(receipts) > 1:
        limitations.add("multiple_receipts_available")
    entity_by_key: dict[str, GroupEntity] = {}
    for row in entity_rows:
        entity = _entity_from_row(dict(row), requested_year=year)
        existing = entity_by_key.get(entity.entity_key)
        if existing is not None and existing != entity:
            limitations.add("duplicate_entity_claim")
            continue
        entity_by_key[entity.entity_key] = entity
    entities = tuple(entity_by_key.values())
    relationship_candidates = tuple(
        GroupRelationship(
            str(row["relationship_key"]), str(row["parent_entity_key"]),
            str(row["child_entity_key"]), str(row["relation_type"]),
            row["ownership_pct"], int(row["effective_year"]),
            str(row["source_rcept_no"]), str(row["source_table"]),
            int(row["source_ordinal"]),
        )
        for row in relationship_rows
    )
    metrics = tuple(
        ComponentMetric(
            str(row["metric_identity"]), str(row["entity_key"]),
            str(row["metric_key"]), row["amount"], row["unit"],
            row["numerator_source_rcept_no"], row["numerator_source_table"],
            row["denominator_amount"], row["denominator_unit"],
            row["denominator_source_rcept_no"], row["denominator_source_table"],
            row["fs_div"], row["period"], row["elimination_basis"],
            row["share_pct"], str(row["qsc_status"]),
            tuple(filter(None, str(row["qsc_basis"] or "").split("|"))),
            float(row["qsc_threshold_pct"]), str(row["quality_status"]),
            row["gap_reason"],
        )
        for row in metric_rows
    )
    known_entity_keys = {entity.entity_key for entity in entities}
    relationships_list: list[GroupRelationship] = []
    edge_claims: dict[tuple[str, str], tuple[str, float | None]] = {}
    for relationship in relationship_candidates:
        if (
            relationship.parent_entity_key not in known_entity_keys
            or relationship.child_entity_key not in known_entity_keys
        ):
            limitations.add("orphan_edge")
            continue
        key = (
            relationship.parent_entity_key,
            relationship.child_entity_key,
        )
        claim = (relationship.relation_type, relationship.ownership_pct)
        if key in edge_claims:
            if edge_claims[key] != claim:
                limitations.add("contradictory_duplicate_edge")
            else:
                limitations.add("duplicate_relationship_claim")
            continue
        edge_claims[key] = claim
        relationships_list.append(relationship)
    relationships = tuple(relationships_list)
    _graph_limitations(list(relationships), limitations)
    parent_candidates = [
        entity for entity in entities
        if entity.resolved_corp_code == parent_corp_code
    ]
    parent_name = (
        parent_candidates[0].original_name
        if parent_candidates else parent_corp_code
    )
    return GroupGraph(
        parent_name, year, entities, relationships, metrics,
        limitations=tuple(sorted(limitations)),
    )
