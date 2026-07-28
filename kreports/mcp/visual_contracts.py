"""Strict, portable visualization contracts with canonical table fallbacks."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import hashlib
import html
import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kreports.analysis.evidence import evidence_reference_fields
from kreports.mcp.auditor_public import public_kam_lifecycle_events


PACK_VERSION = "visualization_pack.v1"
MAX_TEXT = 2_000
MAX_ROWS = 200
MAX_COLUMNS = 32
MAX_PAYLOAD_BYTES = 128_000
_ID = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
_CONTROLS = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]")
_URL_SCHEME = re.compile(r"(?i)\b(https?|javascript|data):")
_PUBLIC_SOURCE_LABELS = {
    "accounting_note_chapters": "회계정책 주석 캐시",
    "audit_matter_items": "감사보고서 항목 캐시",
    "audit_procedure_items": "감사절차 항목 캐시",
    "evidence_documents": "공시 근거 캐시",
    "financial_facts_compact": "재무 공시 캐시",
    "local_subsidiary_auditor_matrix": "연결실체 감사인 캐시",
    "report_sections": "보고서 섹션 캐시",
    "report_sections.audit_report": "감사보고서 캐시",
    "source_documents": "원문 문서 캐시",
}


def _strict_model_config() -> ConfigDict:
    return ConfigDict(
        extra="forbid",
        strict=True,
        populate_by_name=True,
        revalidate_instances="always",
    )


def _bounded_text(value: str, *, field_name: str = "text") -> str:
    if not value or len(value) > MAX_TEXT or _CONTROLS.search(value):
        raise ValueError(f"{field_name} is invalid or exceeds bounds")
    return value


def _validate_identifier(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ValueError("identifier must be canonical and bounded")
    return value


def _bounded_title(value: str) -> str:
    if len(value) > 256:
        raise ValueError("title exceeds bounds")
    return _bounded_text(value, field_name="title")


def _validate_json_value(value: Any, *, depth: int = 0) -> None:
    if depth > 4:
        raise ValueError("row value nesting exceeds bounds")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > 10**100:
            raise ValueError("integer exceeds bounds")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("row values must be finite")
        return
    if isinstance(value, str):
        _bounded_text(value, field_name="cell value")
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ValueError("row list exceeds bounds")
        for item in value:
            _validate_json_value(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("row object exceeds bounds")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("row object keys must be strings")
            _bounded_text(key, field_name="row object key")
            _validate_json_value(item, depth=depth + 1)
        return
    raise TypeError("row values must be JSON-safe")


def _contains_fact(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, list):
        return any(_contains_fact(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_fact(item) for item in value.values())
    return False


def _is_quantitative_value(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float, Decimal)):
        return not isinstance(value, float) or math.isfinite(value)
    if not isinstance(value, str) or len(value) > 128:
        return False
    if not re.fullmatch(
        r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?",
        value,
        re.ASCII,
    ):
        return False
    try:
        return Decimal(value).is_finite()
    except Exception:
        return False


class ColumnSpecV1(BaseModel):
    model_config = _strict_model_config()

    key: str = Field(alias="field")
    label: str
    unit: str | None = None

    _id = field_validator("key")(_validate_identifier)
    _label = field_validator("label")(_bounded_text)

    @field_validator("unit")
    @classmethod
    def _unit(cls, value: str | None) -> str | None:
        return _bounded_text(value, field_name="unit") if value is not None else None


class TableSpecV1(BaseModel):
    model_config = _strict_model_config()

    id: str
    title: str
    columns: list[ColumnSpecV1] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[dict[str, Any]] = Field(max_length=MAX_ROWS)
    status: Literal["usable", "limited", "missing", "error"] = "usable"
    note: str | None = None

    _id = field_validator("id")(_validate_identifier)
    _title = field_validator("title")(_bounded_title)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _bounded_text(value, field_name="note") if value is not None else None

    @model_validator(mode="after")
    def _integrity(self) -> TableSpecV1:
        keys = [column.key for column in self.columns]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate column key")
        allowed = set(keys)
        for row in self.rows:
            unknown = set(row) - allowed
            if unknown:
                raise ValueError(f"row contains unknown column: {sorted(unknown)[0]}")
            for value in row.values():
                _validate_json_value(value)
            if not any(_contains_fact(value) for value in row.values()):
                raise ValueError("table row must contain a declared fact")
        if self.status == "usable" and not self.rows:
            raise ValueError("usable table must contain at least one row")
        if self.status == "missing" and self.rows:
            raise ValueError("missing table cannot carry fact rows")
        return self


class ChannelSpecV1(BaseModel):
    model_config = _strict_model_config()

    field: str | None = None
    fields: list[str] | None = Field(default=None, min_length=1, max_length=16)

    @model_validator(mode="after")
    def _one_shape(self) -> ChannelSpecV1:
        if (self.field is None) == (self.fields is None):
            raise ValueError("encoding requires exactly one of field or fields")
        values = [self.field] if self.field is not None else self.fields or []
        for value in values:
            _validate_identifier(value)
        if len(values) != len(set(values)):
            raise ValueError("duplicate encoding field")
        return self


class EncodingSpecV1(BaseModel):
    model_config = _strict_model_config()

    x: ChannelSpecV1 | None = None
    y: ChannelSpecV1 | None = None
    color: ChannelSpecV1 | None = None
    series: ChannelSpecV1 | None = None
    band: ChannelSpecV1 | None = None


class ChartSpecV1(BaseModel):
    model_config = _strict_model_config()

    id: str
    type: Literal["line", "bar", "waterfall", "heatmap"]
    title: str
    data_ref: str
    encodings: EncodingSpecV1
    note: str | None = None

    _id = field_validator("id", "data_ref")(_validate_identifier)
    _title = field_validator("title")(_bounded_title)

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _bounded_text(value, field_name="note") if value is not None else None


class DiagramSpecV1(BaseModel):
    model_config = _strict_model_config()

    id: str
    type: Literal["mermaid"] = "mermaid"
    title: str
    table_ref: str
    definition: str = Field(max_length=20_000)
    row_refs: list[str] = Field(min_length=1, max_length=MAX_ROWS)
    note: str | None = None

    _id = field_validator("id", "table_ref")(_validate_identifier)
    _title = field_validator("title")(_bounded_title)

    @field_validator("definition")
    @classmethod
    def _definition(cls, value: str) -> str:
        if (
            not value.startswith("flowchart ")
            or "\x00" in value
            or re.search(
                r"(?i)(?:%%\{|https?://|javascript:|data:|"
                r"\bclick\b|\bclassdef\b|\blinkstyle\b|\bstyle\b|"
                r"<(?:script|style|svg|iframe)|\bon\w+\s*=)",
                value,
            )
        ):
            raise ValueError("diagram definition must be bounded Mermaid")
        return value

    @field_validator("row_refs")
    @classmethod
    def _row_refs(cls, values: list[str]) -> list[str]:
        for value in values:
            _bounded_text(value, field_name="row reference")
        if len(values) != len(set(values)):
            raise ValueError("duplicate diagram row reference")
        return values

    @field_validator("note")
    @classmethod
    def _note(cls, value: str | None) -> str | None:
        return _bounded_text(value, field_name="note") if value is not None else None


class TimelineSpecV1(BaseModel):
    model_config = _strict_model_config()

    id: str
    title: str
    table_ref: str

    _id = field_validator("id", "table_ref")(_validate_identifier)
    _title = field_validator("title")(_bounded_title)


class SummarySpecV1(BaseModel):
    model_config = _strict_model_config()

    title: str
    status: str
    subject: str
    domain_status: str | None = None

    _title = field_validator("title")(_bounded_title)
    _text = field_validator("status", "subject")(_bounded_text)

    @field_validator("domain_status")
    @classmethod
    def _domain_status(cls, value: str | None) -> str | None:
        return _bounded_text(value) if value is not None else None


class SourceSpecV1(BaseModel):
    model_config = _strict_model_config()

    label: str
    rcept_no: str | None = None
    url: str | None = None

    _label = field_validator("label")(_bounded_text)

    @field_validator("rcept_no")
    @classmethod
    def _receipt(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9]{14}", value, re.ASCII):
            raise ValueError("source receipt is not canonical")
        return value

    @model_validator(mode="after")
    def _canonical_url(self) -> SourceSpecV1:
        if self.url is not None:
            reference = evidence_reference_fields({
                "source_label": self.label,
                "source_url": self.url,
                "rcept_no": self.rcept_no,
            })
            if (
                reference is None
                or reference.get("source_url") != self.url
                or reference.get("rcept_no") != self.rcept_no
            ):
                raise ValueError("source URL is not canonical")
        return self


class VisualDataQualityV1(BaseModel):
    model_config = _strict_model_config()

    status: Literal["usable", "limited", "missing", "error"]
    source: str | None = None
    grade: Literal["A", "B", "C", "D"] | None = None
    dataset_version: str | None = None
    schema_version: str | None = None
    covered_years: list[int] = Field(default_factory=list, max_length=32)
    missing_fields: list[str] = Field(default_factory=list, max_length=64)
    limitations: list[str] = Field(default_factory=list, max_length=64)
    section_statuses: dict[str, dict[str, Any]] = Field(default_factory=dict)
    coverage_note: str | None = None
    interpretation: str | None = None

    @field_validator(
        "source",
        "dataset_version",
        "schema_version",
        "coverage_note",
        "interpretation",
    )
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        return _bounded_text(value) if value is not None else None

    @field_validator("missing_fields", "limitations")
    @classmethod
    def _string_list(cls, values: list[str]) -> list[str]:
        for value in values:
            _bounded_text(value)
        return values


class VisualizationPackV1(BaseModel):
    model_config = _strict_model_config()

    kind: Literal["answer_pack"] = "answer_pack"
    version: Literal["visualization_pack.v1"] = PACK_VERSION
    tool_name: Literal["build_dcf_model_pack"] | None = None
    summary: SummarySpecV1 = Field(default_factory=lambda: SummarySpecV1(
        title="시각화",
        status="usable",
        subject="대상 조건",
    ))
    tables: list[TableSpecV1] = Field(max_length=16)
    charts: list[ChartSpecV1] = Field(default_factory=list, max_length=16)
    diagrams: list[DiagramSpecV1] = Field(default_factory=list, max_length=8)
    timelines: list[TimelineSpecV1] = Field(default_factory=list, max_length=8)
    sources: list[SourceSpecV1] = Field(default_factory=list, max_length=32)
    data_quality: VisualDataQualityV1 | None = None
    status: Literal["usable", "limited", "missing", "error"]
    limitations: list[str] = Field(default_factory=list, max_length=64)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    resource_uri: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _default_coherent_summary(cls, value: Any) -> Any:
        if isinstance(value, dict) and "summary" not in value:
            normalized = dict(value)
            status = str(normalized.get("status") or "usable")
            normalized["summary"] = {
                "title": "시각화",
                "status": status,
                "subject": "대상 조건",
            }
            return normalized
        return value

    @field_validator("limitations", "warnings")
    @classmethod
    def _bounded_list(cls, values: list[str]) -> list[str]:
        for value in values:
            _bounded_text(value)
        return values

    @field_validator("resource_uri")
    @classmethod
    def _resource_uri(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(
            r"kreports://visualization/[0-9a-f]{64}",
            value,
            re.ASCII,
        ):
            raise ValueError("visualization resource URI is not canonical")
        return value

    @model_validator(mode="after")
    def _integrity(self) -> VisualizationPackV1:
        table_ids = [table.id for table in self.tables]
        if len(table_ids) != len(set(table_ids)):
            raise ValueError("duplicate table id")
        chart_ids = [chart.id for chart in self.charts]
        if len(chart_ids) != len(set(chart_ids)):
            raise ValueError("duplicate chart id")
        diagram_ids = [diagram.id for diagram in self.diagrams]
        if len(diagram_ids) != len(set(diagram_ids)):
            raise ValueError("duplicate diagram id")
        tables = {table.id: table for table in self.tables}
        for chart in self.charts:
            table = tables.get(chart.data_ref)
            if table is None:
                raise ValueError(f"chart references unknown table: {chart.data_ref}")
            declared = {column.key for column in table.columns}
            columns = {column.key: column for column in table.columns}
            encodings = chart.encodings.model_dump(exclude_none=True)
            for channel_name, channel in encodings.items():
                fields = [channel["field"]] if channel.get("field") else channel["fields"]
                if not set(fields).issubset(declared):
                    raise ValueError("chart encoding references undeclared column")
                is_quantitative = (
                    channel_name in {"y", "band"}
                    or (
                        channel_name == "color"
                        and chart.type == "heatmap"
                    )
                )
                if not is_quantitative:
                    continue
                field_has_numeric = {
                    field: any(
                        _is_quantitative_value(row.get(field))
                        for row in table.rows
                    )
                    for field in fields
                }
                field_has_categorical = {
                    field: any(
                        _contains_fact(row.get(field))
                        and not _is_quantitative_value(row.get(field))
                        for row in table.rows
                    )
                    for field in fields
                }
                has_declared_unit = any(
                    columns[field].unit is not None
                    for field in fields
                )
                has_row_unit = (
                    "unit" in declared
                    and any(
                        row.get("unit") not in {None, ""}
                        for row in table.rows
                    )
                )
                intended_quantitative = (
                    any(field_has_numeric.values())
                    or has_declared_unit
                    or (
                        has_row_unit
                        and not any(field_has_categorical.values())
                    )
                )
                if not intended_quantitative:
                    continue
                if not all(field_has_numeric.values()):
                    raise ValueError(
                        "chart quantitative channel has no numeric facts"
                    )
                numeric_fields = list(fields)
                static_units = {
                    columns[field].unit
                    for field in numeric_fields
                }
                if len(static_units) > 1:
                    raise ValueError(
                        "chart quantitative channel has mixed units"
                    )
                applicable_rows = [
                    row
                    for row in table.rows
                    if any(
                        _is_quantitative_value(row.get(field))
                        for field in numeric_fields
                    )
                ]
                row_units = {
                    str(row.get("unit"))
                    for row in applicable_rows
                    if row.get("unit") not in {None, ""}
                }
                missing_row_unit = any(
                    row.get("unit") in {None, ""}
                    for row in applicable_rows
                )
                static_unit = next(iter(static_units))
                if static_unit is not None:
                    if (
                        "unit" in declared
                        and channel_name != "color"
                        and row_units
                        and row_units != {static_unit}
                    ):
                        raise ValueError(
                            "chart row units contradict static units"
                        )
                    continue
                if "unit" not in declared:
                    raise ValueError(
                        "chart quantitative channel requires a unit"
                    )
                if len(row_units) != 1 or missing_row_unit:
                    raise ValueError(
                        "chart quantitative channel has mixed row units"
                    )
                color = encodings.get("color") or {}
                if color.get("field") != "unit":
                    raise ValueError(
                        "chart row unit grouping must be visible"
                    )
        for diagram in self.diagrams:
            table = tables.get(diagram.table_ref)
            if table is None:
                raise ValueError(f"diagram references unknown table: {diagram.table_ref}")
            allowed_refs = {
                str(row.get("row_id", index))
                for index, row in enumerate(table.rows)
            }
            if not set(diagram.row_refs).issubset(allowed_refs):
                raise ValueError("diagram references a row outside its canonical table")
            if set(diagram.row_refs) != allowed_refs:
                raise ValueError("diagram must bind every canonical table row")
            if diagram.definition != _canonical_diagram_definition(table):
                raise ValueError("diagram must be generated from its canonical table")
        for timeline in self.timelines:
            table = tables.get(timeline.table_ref)
            if table is None:
                raise ValueError("timeline references unknown table")
            if table.status in {"missing", "error"} or not table.rows:
                raise ValueError("timeline cannot reference unavailable data")
        if self.summary.status != self.status:
            raise ValueError("summary status must equal pack status")
        if (
            self.data_quality is not None
            and self.data_quality.status != self.status
        ):
            raise ValueError("data-quality status must equal pack status")
        if self.status == "usable" and any(
            table.status != "usable" for table in self.tables
        ):
            raise ValueError("table status contradicts usable pack status")
        if (
            self.status == "missing"
            and self.tool_name == "build_dcf_model_pack"
        ):
            allowed_dcf_tables = {
                "dcf_actuals",
                "dcf_assumptions",
                "dcf_missing_accounts",
            }
            if (
                any(
                    table.id not in allowed_dcf_tables
                    for table in self.tables
                )
                or self.charts
                or self.diagrams
                or self.timelines
                or self.sources
            ):
                raise ValueError(
                    "missing DCF remediation pack contains non-remediation facts"
                )
        elif self.status == "missing" and any(
            table.rows for table in self.tables
        ):
            raise ValueError("missing table cannot carry fact rows")
        for chart in self.charts:
            table = tables[chart.data_ref]
            if table.status in {"missing", "error"} or not table.rows:
                raise ValueError("chart cannot reference unavailable data")
        for diagram in self.diagrams:
            table = tables[diagram.table_ref]
            if table.status in {"missing", "error"} or not table.rows:
                raise ValueError("diagram cannot reference unavailable data")
        if self.status != "usable" and not self.limitations:
            raise ValueError("non-usable pack requires an explicit limitation")
        if self.status == "usable" and not any(table.rows for table in self.tables):
            raise ValueError("usable pack must contain canonical table facts")
        payload = self.model_dump(mode="json", exclude={"resource_uri"})
        size = len(json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode())
        if size > MAX_PAYLOAD_BYTES:
            raise ValueError("visualization payload exceeds bounds")
        digest = hashlib.sha256(json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest()
        canonical_resource_uri = f"kreports://visualization/{digest}"
        if (
            self.resource_uri is not None
            and self.resource_uri != canonical_resource_uri
        ):
            raise ValueError("resource URI does not match the content digest")
        self.resource_uri = canonical_resource_uri
        return self


def validate_visualization_pack(
    pack: VisualizationPackV1 | dict[str, Any],
) -> VisualizationPackV1:
    """Fresh-validate a detached payload at every trust boundary."""
    payload = (
        pack.model_dump(
            mode="json",
            exclude_none=False,
            round_trip=True,
        )
        if isinstance(pack, VisualizationPackV1)
        else deepcopy(pack)
    )
    return VisualizationPackV1.model_validate(payload)


def _canonical_diagram_definition(table: TableSpecV1) -> str:
    lines = ["flowchart TD"]
    node_ids = {
        str(row.get("row_id", index)): (
            "P" if index == 0 else f"N{index}"
        )
        for index, row in enumerate(table.rows)
    }
    for index, row in enumerate(table.rows):
        label = next(
            (
                row.get(key)
                for key in ("name", "corp_name", "entity_name", "label")
                if row.get(key) is not None and row.get(key) != ""
            ),
            next(
                (
                    value for key, value in row.items()
                    if key != "row_id" and value is not None and value != ""
                ),
                "미확보",
            ),
        )
        node_id = "P" if index == 0 else f"N{index}"
        rendered_label = _mermaid_text(label)
        if index == 0 and row.get("year") not in {None, ""}:
            rendered_label += (
                f"<br/>{_mermaid_text(row['year'])}년 연결실체"
            )
        elif index and row.get("qsc_status") not in {None, ""}:
            rendered_label += (
                "<br/>"
                + _mermaid_text(
                    {
                        "qsc": "QSC",
                        "not_qsc": "비QSC",
                        "undetermined": "미판정",
                    }.get(
                        str(row["qsc_status"]),
                        str(row["qsc_status"]),
                    )
                )
            )
        lines.append(f'  {node_id}["{rendered_label}"]')
        if index:
            relation = _safe_text(row.get("relation") or "-")
            ownership = row.get("ownership_pct")
            edge_label = relation
            if ownership is not None and ownership != "":
                rendered_ownership = (
                    f"{ownership:g}"
                    if isinstance(ownership, (int, float))
                    else str(ownership)
                )
                edge_label += f" / 지분율 {rendered_ownership}%"
            parent_node = node_ids.get(
                str(row.get("parent_row_id") or ""),
                "P",
            )
            lines.append(
                f'  {parent_node} -->|"{_mermaid_text(edge_label)}"| N{index}'
            )
    return "\n".join(lines)


def _safe_text(value: Any, *, fallback: str = "-") -> str:
    text = str(value if value is not None else fallback)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROLS.sub("\ufffd", text).strip() or fallback
    text = _URL_SCHEME.sub(lambda match: f"{match.group(1)}_", text)
    return text[:MAX_TEXT]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return max(min(value, 10**100), -(10**100))
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return _safe_text(format(value, "f"))
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in value[:64]]
    if isinstance(value, dict):
        return {
            _safe_text(key)[:80]: _safe_value(item, depth=depth + 1)
            for key, item in list(value.items())[:32]
        }
    return _safe_text(value)


def _infer_unit(label: str, key: str) -> str | None:
    match = re.search(r"\(([^()]{1,30})\)", label)
    if match:
        return match.group(1)
    if key in {
        "wacc",
        "terminal_growth",
        "tax_rate",
        "discount_factor",
        "final_year_discount_factor",
    }:
        return "ratio"
    if key.endswith("_pct") or key == "percentile":
        return "%"
    return None


def _canonical_table(
    raw: dict[str, Any],
    *,
    pack_status: str,
    allow_missing_rows: bool = False,
) -> dict[str, Any]:
    columns = []
    seen: set[str] = set()
    for raw_column in (raw.get("columns") or [])[:MAX_COLUMNS]:
        if not isinstance(raw_column, dict):
            continue
        key = str(raw_column.get("key") or raw_column.get("field") or "")
        if not _ID.fullmatch(key) or key in seen:
            continue
        seen.add(key)
        label = _safe_text(raw_column.get("label") or key)
        columns.append({
            "key": key,
            "label": label,
            "unit": _safe_text(raw_column["unit"])
            if raw_column.get("unit") is not None
            else _infer_unit(label, key),
        })
    if not columns:
        columns = [{"key": "status", "label": "상태", "unit": None}]
        seen = {"status"}
    rows = []
    for raw_row in (raw.get("rows") or [])[:MAX_ROWS]:
        if not isinstance(raw_row, dict):
            continue
        row = {
            key: _safe_value(raw_row.get(key))
            for key in seen
            if key in raw_row
        }
        if row and any(_contains_fact(value) for value in row.values()):
            rows.append(row)
    status = (
        "limited"
        if rows and pack_status == "missing" and allow_missing_rows
        else pack_status
        if rows
        else "missing"
    )
    note = raw.get("note")
    if not rows and not note:
        note = "확인 가능한 데이터가 없습니다."
    return {
        "id": raw.get("id"),
        "title": _safe_text(raw.get("title") or raw.get("id")),
        "columns": columns,
        "rows": rows,
        "status": status,
        **({"note": _safe_text(note)} if note else {}),
    }


def _from_legacy_pack(raw: dict[str, Any]) -> VisualizationPackV1:
    raw_quality = raw.get("data_quality")
    quality = raw_quality if isinstance(raw_quality, dict) else {}
    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    quality_status = str(quality.get("status") or "")
    top_status = str(raw.get("status") or "")
    summary_status = str(summary.get("status") or "")
    valid_statuses = {"usable", "limited", "missing", "error"}
    status = next(
        (
            candidate
            for candidate in (quality_status, top_status, summary_status)
            if candidate in valid_statuses
        ),
        "limited",
    )
    domain_status = next(
        (
            candidate
            for candidate in (summary_status, top_status)
            if candidate and candidate not in valid_statuses
        ),
        None,
    )
    missing_dcf_remediation = (
        status == "missing"
        and raw.get("tool_name") == "build_dcf_model_pack"
    )
    limitations = [
        _safe_text(item)
        for item in [
            *(raw.get("limitations") or []),
            *(quality.get("limitations") or []),
        ][:64]
        if item
    ]
    warnings = [_safe_text(item) for item in (raw.get("warnings") or [])[:64] if item]
    tables = [
        _canonical_table(
            table,
            pack_status=status,
            allow_missing_rows=missing_dcf_remediation,
        )
        for table in (raw.get("tables") or [])[:16]
        if isinstance(table, dict) and _ID.fullmatch(str(table.get("id") or ""))
    ]
    table_ids = {table["id"] for table in tables}
    charts = []
    for raw_chart in (raw.get("charts") or [])[:16]:
        if not isinstance(raw_chart, dict):
            continue
        data_ref = str(raw_chart.get("data_ref") or "")
        raw_rows = raw_chart.get("rows")
        if data_ref not in table_ids and isinstance(raw_rows, list):
            keys = []
            for row in raw_rows:
                if isinstance(row, dict):
                    for key in row:
                        if _ID.fullmatch(str(key)) and key not in keys:
                            keys.append(key)
            if keys and _ID.fullmatch(data_ref):
                table = _canonical_table(
                    {
                        "id": data_ref,
                        "title": raw_chart.get("title") or data_ref,
                        "columns": [
                            {"field": key, "label": key}
                            for key in keys
                        ],
                        "rows": raw_rows,
                    },
                    pack_status=status,
                    allow_missing_rows=missing_dcf_remediation,
                )
                tables.append(table)
                table_ids.add(data_ref)
        table = next((item for item in tables if item["id"] == data_ref), None)
        if table is None:
            continue
        declared = {column["key"] for column in table["columns"]}
        encodings = {}
        for channel_name, raw_channel in (raw_chart.get("encodings") or {}).items():
            if channel_name not in {"x", "y", "color", "series", "band"}:
                continue
            if not isinstance(raw_channel, dict):
                continue
            if raw_channel.get("field") in declared:
                encodings[channel_name] = {"field": raw_channel["field"]}
            elif isinstance(raw_channel.get("fields"), list):
                fields = [field for field in raw_channel["fields"] if field in declared][:16]
                if fields:
                    encodings[channel_name] = {"fields": fields}
        chart_type = str(raw_chart.get("type") or "")
        if chart_type == "band":
            chart_type = "bar"
        if chart_type not in {"line", "bar", "waterfall", "heatmap"}:
            continue
        chart = {
            "id": raw_chart.get("id"),
            "type": chart_type,
            "title": _safe_text(raw_chart.get("title") or raw_chart.get("id")),
            "data_ref": data_ref,
            "encodings": encodings,
        }
        if raw_chart.get("note"):
            chart["note"] = _safe_text(raw_chart["note"])
        charts.append(chart)
    diagrams = []
    first_table = tables[0]["id"] if tables else None
    for raw_diagram in (raw.get("diagrams") or [])[:8]:
        if not isinstance(raw_diagram, dict) or not first_table:
            continue
        table_ref = str(raw_diagram.get("table_ref") or first_table)
        if table_ref not in table_ids:
            continue
        source_table = next(item for item in tables if item["id"] == table_ref)
        if not source_table["rows"] or source_table["status"] in {"missing", "error"}:
            continue
        if not raw_diagram.get("table_ref"):
            fact_id = f"{str(raw_diagram.get('id') or 'group')}_facts"
            fact_id = fact_id[:64]
            fact_rows = [{
                "row_id": "0",
                "name": _safe_text(summary.get("subject") or "대상 회사"),
                "relation": "root",
            }]
            for index, row in enumerate(source_table["rows"], start=1):
                fact_rows.append({
                    "row_id": str(index),
                    "name": _safe_value(
                        row.get("name")
                        or row.get("corp_name")
                        or f"실체 {index}"
                    ),
                    "relation": _safe_value(row.get("relation") or "-"),
                })
            source_table = {
                "id": fact_id,
                "title": _safe_text(raw_diagram.get("title") or "그룹 구조 근거"),
                "columns": [
                    {"key": "row_id", "label": "행 ID", "unit": None},
                    {"key": "name", "label": "회사", "unit": None},
                    {"key": "relation", "label": "관계", "unit": None},
                ],
                "rows": fact_rows,
                "status": status,
            }
            tables.append(source_table)
            table_ids.add(fact_id)
            table_ref = fact_id
        table_model = TableSpecV1.model_validate(source_table)
        row_refs = [
            str(row.get("row_id", index))
            for index, row in enumerate(table_model.rows)
        ]
        diagrams.append({
            "id": raw_diagram.get("id"),
            "type": "mermaid",
            "title": _safe_text(raw_diagram.get("title") or raw_diagram.get("id")),
            "table_ref": table_ref,
            "definition": _canonical_diagram_definition(table_model),
            "row_refs": row_refs,
        })
    timelines = []
    for raw_timeline in (raw.get("timelines") or [])[:8]:
        if not isinstance(raw_timeline, dict):
            continue
        table_ref = str(raw_timeline.get("table_ref") or "disclosure_events")
        table = next(
            (item for item in tables if item["id"] == table_ref),
            None,
        )
        if (
            table is not None
            and table["rows"]
            and table["status"] not in {"missing", "error"}
        ):
            timelines.append({
                "id": raw_timeline.get("id"),
                "title": _safe_text(raw_timeline.get("title") or raw_timeline.get("id")),
                "table_ref": table_ref,
            })
    if (
        not tables
        or (
            not any(table["rows"] for table in tables)
            and not missing_dcf_remediation
        )
    ):
        tables = [{
            "id": "availability",
            "title": "데이터 가용성",
            "columns": [{"key": "status", "label": "상태", "unit": None}],
            "rows": [],
            "status": "missing",
            "note": "확인 가능한 데이터가 없습니다.",
        }]
        # A supplied canonical non-usable status describes the response even
        # when its availability table has no fact rows.  Only an otherwise
        # usable empty response becomes missing.
        if status == "usable":
            status = "missing"
        limitations.append("로컬 캐시에 확인 가능한 데이터가 없습니다.")
        charts = []
        diagrams = []
        timelines = []
    elif status == "usable" and any(
        table["status"] != "usable" for table in tables
    ):
        status = "limited"
        for table in tables:
            if table["status"] == "usable":
                table["status"] = "limited"
        limitations.append("일부 시각화 표의 데이터가 완전하지 않습니다.")
    charts = [
        chart for chart in charts
        if (
            (table := next(
                (item for item in tables if item["id"] == chart["data_ref"]),
                None,
            )) is not None
            and table["rows"]
            and table["status"] not in {"missing", "error"}
        )
    ]
    if status != "usable" and not limitations:
        limitations.append("데이터가 제한되어 시각적 결론을 제공하지 않습니다.")
    sources = []
    for source in (raw.get("sources") or [])[:32]:
        if not isinstance(source, dict):
            continue
        reference = evidence_reference_fields({
            "source_label": source.get("label"),
            "source_url": source.get("url"),
            "rcept_no": source.get("rcept_no"),
        })
        if not reference:
            continue
        sources.append({
            "label": _safe_text(reference.get("source_label") or "공개 출처"),
            "rcept_no": reference.get("rcept_no"),
            "url": reference.get("source_url"),
        })
    quality_keys = {
        "status",
        "source",
        "grade",
        "dataset_version",
        "schema_version",
        "covered_years",
        "missing_fields",
        "limitations",
        "section_statuses",
        "coverage_note",
        "interpretation",
    }
    safe_quality = {
        # Contracts already validate typed section statuses.  They are copied
        # verbatim so the visualization resource remains traceable to the
        # response envelope rather than silently rewriting source references.
        key: (value if key == "section_statuses" else _safe_value(value))
        for key, value in quality.items()
        if key in quality_keys
    }
    safe_quality["status"] = status
    return VisualizationPackV1.model_validate({
        "tool_name": (
            "build_dcf_model_pack"
            if missing_dcf_remediation
            else None
        ),
        "summary": {
            "title": _safe_text(summary.get("title") or "시각화"),
            "status": _safe_text(status),
            "subject": _safe_text(summary.get("subject") or "대상 조건"),
            **(
                {"domain_status": _safe_text(domain_status)}
                if domain_status
                else {}
            ),
        },
        "tables": tables,
        "charts": charts,
        "diagrams": diagrams,
        "timelines": timelines,
        "sources": sources,
        "data_quality": safe_quality,
        "status": status,
        "limitations": list(dict.fromkeys(limitations)),
        "warnings": list(dict.fromkeys(warnings)),
    })


def _raw_family_pack(result: dict[str, Any]) -> dict[str, Any]:
    family = str(result.get("_visual_family") or "")
    status = str(result.get("_visual_status") or "usable")
    limitations = list(result.get("limitations") or [])
    title = _safe_text(result.get("title") or family.replace("_", " ") or "시각화")
    pack: dict[str, Any] = {
        "summary": {"title": title, "status": status, "subject": _safe_text(result.get("entity_name") or "대상 조건")},
        "tables": [],
        "charts": [],
        "diagrams": [],
        "timelines": [],
        "data_quality": {"status": status},
        "limitations": limitations,
    }
    if family == "financial_trend":
        rows = result.get("historical_actuals") or []
        pack["tables"].append({
            "id": "historical_actuals",
            "title": "5개년 재무 추이",
            "columns": [
                {"field": "year", "label": "연도"},
                {"field": "revenue", "label": "매출", "unit": "백만원"},
            ],
            "rows": rows,
        })
        pack["charts"].append({
            "id": "financial_trend", "type": "line", "title": "재무 추이",
            "data_ref": "historical_actuals",
            "encodings": {"x": {"field": "year"}, "y": {"field": "revenue"}},
        })
    elif family == "dcf":
        projections = result.get("projections") or []
        sensitivity = result.get("sensitivity") or []
        pack["tables"].extend([
            {
                "id": "dcf_projections", "title": "DCF 예측",
                "columns": [
                    {"field": "year", "label": "연도"},
                    {"field": "revenue", "label": "매출", "unit": "KRW"},
                    {"field": "ufcf", "label": "UFCF", "unit": "KRW"},
                ], "rows": projections,
            },
            {
                "id": "dcf_sensitivity", "title": "DCF 민감도",
                "columns": [
                    {"field": "wacc", "label": "WACC", "unit": "ratio"},
                    {
                        "field": "terminal_growth",
                        "label": "영구성장률",
                        "unit": "ratio",
                    },
                    {
                        "field": "enterprise_value",
                        "label": "기업가치",
                        "unit": "KRW",
                    },
                    {"field": "status", "label": "상태"},
                ], "rows": sensitivity,
            },
        ])
        pack["charts"].extend([
            {
                "id": "dcf_projection", "type": "line", "title": "DCF 예측",
                "data_ref": "dcf_projections",
                "encodings": {"x": {"field": "year"}, "y": {"field": "ufcf"}},
            },
            {
                "id": "dcf_sensitivity_matrix", "type": "heatmap", "title": "DCF 민감도",
                "data_ref": "dcf_sensitivity",
                "encodings": {
                    "x": {"field": "wacc"},
                    "y": {"field": "terminal_growth"},
                    "color": {"field": "enterprise_value"},
                },
            },
        ])
    elif family == "peer_distribution":
        rows = []
        for year, metrics in (result.get("results") or {}).items():
            for metric, values in metrics.items():
                rows.append({"year": int(year), "metric": metric, **values})
        pack["tables"].append({
            "id": "peer_metric_matrix", "title": "Peer 분포",
            "columns": [
                {"field": "year", "label": "연도"},
                {"field": "metric", "label": "지표"},
                {"field": "subject_value", "label": "대상회사 값"},
                {"field": "p25", "label": "Peer P25 값"},
                {"field": "p50", "label": "Peer 중앙값 P50"},
                {"field": "p75", "label": "Peer P75 값"},
                {
                    "field": "percentile",
                    "label": "대상회사 백분위",
                    "unit": "%",
                },
                {"field": "n", "label": "Peer 표본 수", "unit": "개"},
                {"field": "unit", "label": "값 단위"},
            ], "rows": rows,
        })
        peer_units = {
            str(row.get("unit"))
            for row in rows
            if row.get("unit") not in {None, ""}
        }
        has_numeric_subject = any(
            _is_quantitative_value(row.get("subject_value"))
            for row in rows
        )
        missing_units = any(
            row.get("unit") in {None, ""}
            for row in rows
        )
        if (
            rows
            and has_numeric_subject
            and len(peer_units) == 1
            and not missing_units
        ):
            unit = next(iter(peer_units))
            pack["charts"].append({
                "id": "peer_distribution",
                "type": "bar",
                "title": f"Peer 분포 ({unit})",
                "data_ref": "peer_metric_matrix",
                "encodings": {
                    "x": {"field": "metric"},
                    "y": {"field": "subject_value"},
                    "color": {"field": "unit"},
                },
            })
        elif rows:
            limitation = (
                "peer_chart_suppressed:no_numeric_facts"
                if not has_numeric_subject
                else "peer_chart_suppressed:missing_units"
                if missing_units
                else "peer_chart_suppressed:mixed_units:"
                + ",".join(sorted(peer_units))
            )
            if limitation not in pack["limitations"]:
                pack["limitations"].append(limitation)
    elif family == "group_graph":
        entity_name = _safe_text(result.get("entity_name") or "대상 회사")
        entities = [
            row for row in (result.get("entities") or [])
            if (
                isinstance(row, dict)
                and any(
                    _contains_fact(row.get(key))
                    for key in ("name", "relation", "ownership_pct")
                )
            )
        ][: MAX_ROWS - 1]
        (
            visible_entities,
            unresolved_count,
            omitted_count,
        ) = _hierarchy_closed_group_rows(entities, limit=8)
        if unresolved_count:
            if status == "usable":
                status = "limited"
                pack["summary"]["status"] = status
                pack["data_quality"]["status"] = status
            limitation = f"unresolved_parent_entities:{unresolved_count}"
            if limitation not in pack["limitations"]:
                pack["limitations"].append(limitation)
        entity_row_ids = {
            str(row.get("entity_key")): str(row.get("entity_key"))
            for row in visible_entities
            if row.get("entity_key") not in {None, ""}
        }
        rows = []
        if entities:
            rows.append({
                "row_id": "root",
                "parent_row_id": None,
                "name": entity_name,
                "relation": "root",
                "ownership_pct": None,
            })
        for index, row in enumerate(visible_entities, start=1):
            row_id = str(row.get("entity_key") or index)
            parent_key = str(row.get("parent_entity_key") or "")
            rows.append({
                "row_id": row_id,
                "parent_row_id": (
                    entity_row_ids.get(parent_key, "root")
                    if not row.get("parent_is_root")
                    else "root"
                ),
                "name": row.get("name"),
                "relation": row.get("relation"),
                "ownership_pct": row.get("ownership_pct"),
            })
        if omitted_count > 0:
            rows.append({
                "row_id": "omitted",
                "parent_row_id": "root",
                "name": f"{omitted_count}개 노드는 가독성을 위해 생략",
                "relation": "omitted",
                "ownership_pct": None,
            })
        pack["tables"].append({
            "id": "group_entities", "title": "그룹 실체",
            "columns": [
                {"field": "row_id", "label": "행 ID"},
                {"field": "parent_row_id", "label": "상위 행 ID"},
                {"field": "name", "label": "회사"},
                {"field": "relation", "label": "관계"},
                {"field": "ownership_pct", "label": "지분율", "unit": "%"},
            ], "rows": rows,
        })
        definition = _group_definition(
            entity_name,
            rows,
        )
        pack["diagrams"].append({
            "id": "group_structure", "type": "mermaid", "title": "그룹 구조",
            "table_ref": "group_entities", "definition": definition,
        })
    elif family == "audit_fee":
        rows = result.get("history") or []
        pack["tables"].append({
            "id": "audit_fee_trend", "title": "감사보수 추이",
            "columns": [
                {"field": "year", "label": "연도"},
                {"field": "audit_fee_m", "label": "감사보수", "unit": "백만원"},
                {"field": "audit_hours", "label": "감사시간", "unit": "시간"},
            ], "rows": rows,
        })
        pack["charts"].append({
            "id": "audit_fee_trend_chart", "type": "line", "title": "감사보수 추이",
            "data_ref": "audit_fee_trend",
            "encodings": {"x": {"field": "year"}, "y": {"field": "audit_fee_m"}},
        })
    elif family == "kam_lifecycle":
        rows = public_kam_lifecycle_events(result.get("events"))
        pack["tables"].append({
            "id": "kam_lifecycle", "title": "KAM 생애주기",
            "columns": [
                {"field": "year", "label": "연도"},
                {"field": "topic", "label": "주제"},
                {"field": "status", "label": "상태"},
            ], "rows": rows,
        })
        pack["charts"].append({
            "id": "kam_lifecycle_chart", "type": "bar", "title": "KAM 생애주기",
            "data_ref": "kam_lifecycle",
            "encodings": {"x": {"field": "year"}, "y": {"field": "status"}},
        })
    elif family == "disclosure_timeline":
        rows = result.get("events") or []
        pack["tables"].append({
            "id": "disclosure_events", "title": "공시 이벤트",
            "columns": [
                {"field": "event_date", "label": "일자"},
                {"field": "corp_name", "label": "회사"},
                {"field": "event_type", "label": "이벤트 유형"},
                {"field": "event_title", "label": "공시명"},
                {"field": "rcept_no", "label": "접수번호"},
            ], "rows": rows,
        })
        pack["timelines"].append({
            "id": "disclosure_timeline", "title": "공시 이벤트 타임라인",
            "table_ref": "disclosure_events",
        })
    return pack


def _hierarchy_closed_group_rows(
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], int, int]:
    resolved: list[dict[str, Any]] = []
    resolved_keys: set[str] = set()
    pending = list(rows)
    while pending:
        progressed = False
        for row in list(pending):
            parent_key = str(row.get("parent_entity_key") or "")
            parent_is_root = (
                row.get("parent_is_root") is True
                or not parent_key
            )
            if (
                parent_key
                and not parent_is_root
                and parent_key not in resolved_keys
            ):
                continue
            pending.remove(row)
            resolved.append(row)
            if row.get("entity_key") not in {None, ""}:
                resolved_keys.add(str(row["entity_key"]))
            progressed = True
        if not progressed:
            break
    visible = resolved[:limit]
    return visible, len(pending), len(resolved) - len(visible)


def build_visualization_pack(result: dict[str, Any]) -> VisualizationPackV1:
    """Build and validate a portable visualization pack without database I/O."""
    if not isinstance(result, dict):
        raise TypeError("result must be a dict")
    if result.get("kind") == "answer_pack" or "tables" in result:
        pack = _from_legacy_pack(result)
    else:
        pack = _from_legacy_pack(_raw_family_pack(result))
    from kreports.mcp.resources import publish_visualization_resource

    publish_visualization_resource(pack)
    return pack


def _mermaid_text(value: Any) -> str:
    text = _safe_text(value)
    escaped = (
        text.replace("&", "&amp;")
        .replace("\\", "&#92;")
        .replace("|", "/")
        .replace('"', '\\"')
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
        .replace("`", "'")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
    escaped = re.sub(
        r"(?i)\b(click|style|classdef|linkstyle)\b",
        lambda match: f"{match.group(1)[0]}&#8203;{match.group(1)[1:]}",
        escaped,
    )
    return re.sub(
        r"(?i)\b(on\w+)(\s*=)",
        lambda match: (
            f"{match.group(1)[0]}&#8203;{match.group(1)[1:]}"
            f"{match.group(2)}"
        ),
        escaped,
    )


def _group_definition(entity_name: str, rows: list[dict[str, Any]]) -> str:
    lines = ["flowchart TD", f'  P["{_mermaid_text(entity_name)}"]']
    for index, row in enumerate(rows[:32], start=1):
        name = _mermaid_text(row.get("name") or "미확보")
        relation = _mermaid_text(row.get("relation") or "-")
        lines.append(f'  P -->|"{relation}"| N{index}["{name}"]')
    if not rows:
        lines.append('  P -->|"캐시 없음"| N0["연결/투자 실체 미확보"]')
    return "\n".join(lines)


def build_group_diagram(entity_name: str) -> DiagramSpecV1:
    """Build a safe group root diagram tied to the canonical group table."""
    table = TableSpecV1(
        id="group_entities",
        title="그룹 실체",
        columns=[
            {"field": "row_id", "label": "행 ID"},
            {"field": "name", "label": "회사"},
            {"field": "relation", "label": "관계"},
        ],
        rows=[{
            "row_id": "0",
            "name": _safe_text(entity_name),
            "relation": "root",
        }],
    )
    return DiagramSpecV1(
        id="group_structure",
        title="그룹 구조",
        table_ref="group_entities",
        definition=_canonical_diagram_definition(table),
        row_refs=["0"],
    )


def _markdown_cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    text = _safe_text(value)
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _public_source_label(value: str) -> str:
    return _PUBLIC_SOURCE_LABELS.get(value, "로컬 공시 캐시")


def _presentation_cell(column_key: str, value: Any) -> Any:
    if column_key == "source_table" and isinstance(value, str):
        return _public_source_label(value)
    return value


def _column_heading(column: ColumnSpecV1) -> str:
    if not column.unit:
        return column.label
    suffix = f"({column.unit})"
    if column.label.rstrip().endswith(suffix):
        return column.label
    return f"{column.label} ({column.unit})"


def render_visualization_markdown(
    pack: VisualizationPackV1,
    *,
    mermaid: bool,
) -> str:
    """Render canonical tables, optionally followed by safe Mermaid diagrams."""
    validated = validate_visualization_pack(pack)
    lines: list[str] = [
        f"시각화 데이터 상태: {_markdown_cell(validated.status)}",
    ]
    if validated.data_quality and validated.data_quality.source:
        lines.append(
            "데이터 출처: "
            f"{_markdown_cell(_public_source_label(validated.data_quality.source))}"
        )
    lines.append("")
    for table in validated.tables:
        lines.extend([f"### {_markdown_cell(table.title)}", ""])
        headers = [_column_heading(column) for column in table.columns]
        lines.append("| " + " | ".join(_markdown_cell(item) for item in headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in table.rows:
            lines.append("| " + " | ".join(
                _markdown_cell(_presentation_cell(
                    column.key,
                    row.get(column.key, "-"),
                ))
                for column in table.columns
            ) + " |")
        if not table.rows:
            lines.append("| " + " | ".join(
                _markdown_cell(table.note or "데이터 미확보")
                if index == 0 else "-"
                for index, _ in enumerate(headers)
            ) + " |")
        lines.append("")
    if mermaid:
        for diagram in validated.diagrams:
            lines.extend([
                f"### {_markdown_cell(diagram.title)}",
                "",
                "```mermaid",
                diagram.definition,
                "```",
                "",
            ])
    if validated.limitations:
        lines.extend(["데이터 한계:", *[
            f"- {_markdown_cell(item)}" for item in validated.limitations
        ]])
    if validated.warnings:
        lines.extend(["경고:", *[
            f"- {_markdown_cell(item)}" for item in validated.warnings
        ]])
    if validated.sources:
        lines.extend(["출처:", *[
            "- "
            + _markdown_cell(source.label)
            + (
                f" / {_markdown_cell(source.rcept_no)}"
                if source.rcept_no
                else ""
            )
            for source in validated.sources
        ]])
    return "\n".join(lines).strip()


def render_visualization_html(pack: VisualizationPackV1) -> str:
    """Render a bounded, self-contained HTML table resource from validated facts."""
    validated = validate_visualization_pack(pack)
    pieces = [
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">",
        "<meta name=\"referrer\" content=\"no-referrer\">",
        "<meta http-equiv=\"Content-Security-Policy\" "
        "content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'\">",
        "<style>body{font-family:system-ui,sans-serif;margin:1rem;color:#17202a}"
        "table{border-collapse:collapse;width:100%;margin-bottom:1.5rem}"
        "th,td{border:1px solid #ccd1d1;padding:.4rem;text-align:left}"
        "th{background:#f4f6f7}.note{color:#566573}</style></head><body>",
        f"<h1>{html.escape(validated.summary.title)}</h1>",
        f"<p>시각화 데이터 상태: {html.escape(validated.status)}</p>",
    ]
    if validated.data_quality and validated.data_quality.source:
        pieces.append(
            "<p>데이터 출처: "
            f"{html.escape(_public_source_label(validated.data_quality.source))}</p>"
        )
    for table in validated.tables:
        pieces.append(f"<section><h2>{html.escape(table.title)}</h2><table><thead><tr>")
        for column in table.columns:
            label = _column_heading(column)
            pieces.append(f"<th>{html.escape(label)}</th>")
        pieces.append("</tr></thead><tbody>")
        rows = table.rows or [{table.columns[0].key: table.note or "데이터 미확보"}]
        for row in rows:
            pieces.append("<tr>")
            for column in table.columns:
                value = _presentation_cell(
                    column.key,
                    row.get(column.key, "-"),
                )
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                pieces.append(f"<td>{html.escape(_safe_text(value))}</td>")
            pieces.append("</tr>")
        pieces.append("</tbody></table></section>")
    if validated.sources:
        pieces.append("<section><h2>출처</h2><ul>")
        for source in validated.sources:
            reference = f"{source.label} {source.rcept_no or ''}".strip()
            pieces.append(f"<li>{html.escape(reference)}</li>")
        pieces.append("</ul></section>")
    if validated.limitations:
        pieces.append("<section class=\"note\"><h2>데이터 한계</h2><ul>")
        for limitation in validated.limitations:
            pieces.append(f"<li>{html.escape(limitation)}</li>")
        pieces.append("</ul></section>")
    if validated.warnings:
        pieces.append("<section class=\"note\"><h2>경고</h2><ul>")
        for warning in validated.warnings:
            pieces.append(f"<li>{html.escape(warning)}</li>")
        pieces.append("</ul></section>")
    pieces.append("</body></html>")
    rendered = "".join(pieces)
    if len(rendered.encode()) > 200_000:
        raise ValueError("visualization HTML exceeds bounds")
    return rendered
