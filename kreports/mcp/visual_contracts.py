"""Strict, portable visualization contracts with canonical table fallbacks."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import html
import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PACK_VERSION = "visualization_pack.v1"
MAX_TEXT = 2_000
MAX_ROWS = 200
MAX_COLUMNS = 32
MAX_PAYLOAD_BYTES = 128_000
_ID = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
_CONTROLS = re.compile(r"[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]")
_URL_SCHEME = re.compile(r"(?i)\b(https?|javascript|data):")


def _strict_model_config() -> ConfigDict:
    return ConfigDict(extra="forbid", strict=True, populate_by_name=True)


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
        if self.status == "usable" and not self.rows:
            raise ValueError("usable table must contain at least one row")
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
    row_refs: list[str] = Field(default_factory=list, max_length=MAX_ROWS)
    note: str | None = None

    _id = field_validator("id", "table_ref")(_validate_identifier)
    _title = field_validator("title")(_bounded_title)

    @field_validator("definition")
    @classmethod
    def _definition(cls, value: str) -> str:
        if not value.startswith("flowchart ") or "\x00" in value:
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
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_ROWS)

    _id = field_validator("id", "table_ref")(_validate_identifier)
    _title = field_validator("title")(_bounded_title)

    @field_validator("events")
    @classmethod
    def _events(cls, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for value in values:
            _validate_json_value(value)
        return values


class SummarySpecV1(BaseModel):
    model_config = _strict_model_config()

    title: str
    status: str
    subject: str

    _title = field_validator("title")(_bounded_title)
    _text = field_validator("status", "subject")(_bounded_text)


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
            expected = (
                "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
                f"{self.rcept_no}"
            )
            if self.rcept_no is None or self.url != expected:
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
            for channel in chart.encodings.model_dump(exclude_none=True).values():
                fields = [channel["field"]] if channel.get("field") else channel["fields"]
                if not set(fields).issubset(declared):
                    raise ValueError("chart encoding references undeclared column")
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
        for timeline in self.timelines:
            if timeline.table_ref not in tables:
                raise ValueError("timeline references unknown table")
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
    if key.endswith("_pct") or key in {"wacc", "terminal_growth", "percentile"}:
        return "%"
    return None


def _canonical_table(raw: dict[str, Any], *, pack_status: str) -> dict[str, Any]:
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
        rows.append({
            key: _safe_value(raw_row.get(key))
            for key in seen
            if key in raw_row
        })
    status = pack_status if rows else "missing"
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
    raw_status = str(
        raw.get("status")
        or (raw.get("summary") or {}).get("status")
        or quality.get("status")
        or "usable"
    )
    status = raw_status if raw_status in {"usable", "limited", "missing", "error"} else "limited"
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
        _canonical_table(table, pack_status=status)
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
                table = _canonical_table({
                    "id": data_ref,
                    "title": raw_chart.get("title") or data_ref,
                    "columns": [{"field": key, "label": key} for key in keys],
                    "rows": raw_rows,
                }, pack_status=status)
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
        definition = str(raw_diagram.get("definition") or "")
        if not definition.startswith("flowchart "):
            continue
        diagrams.append({
            "id": raw_diagram.get("id"),
            "type": "mermaid",
            "title": _safe_text(raw_diagram.get("title") or raw_diagram.get("id")),
            "table_ref": table_ref,
            "definition": definition[:20_000],
            "row_refs": [],
        })
    timelines = []
    for raw_timeline in (raw.get("timelines") or [])[:8]:
        if not isinstance(raw_timeline, dict):
            continue
        table_ref = str(raw_timeline.get("table_ref") or "disclosure_events")
        if table_ref in table_ids:
            timelines.append({
                "id": raw_timeline.get("id"),
                "title": _safe_text(raw_timeline.get("title") or raw_timeline.get("id")),
                "table_ref": table_ref,
                "events": _safe_value(raw_timeline.get("events") or []),
            })
    if not tables:
        tables = [{
            "id": "availability",
            "title": "데이터 가용성",
            "columns": [{"key": "status", "label": "상태", "unit": None}],
            "rows": [],
            "status": "missing",
            "note": "확인 가능한 데이터가 없습니다.",
        }]
        status = "missing"
        limitations.append("로컬 캐시에 확인 가능한 데이터가 없습니다.")
    if status != "usable" and not limitations:
        limitations.append("데이터가 제한되어 시각적 결론을 제공하지 않습니다.")
    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    sources = []
    for source in (raw.get("sources") or [])[:32]:
        if not isinstance(source, dict):
            continue
        receipt = str(source.get("rcept_no") or "")
        if not re.fullmatch(r"[0-9]{14}", receipt, re.ASCII):
            continue
        sources.append({
            "label": _safe_text(source.get("label") or "DART 공시"),
            "rcept_no": receipt,
            "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
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
        "coverage_note",
        "interpretation",
    }
    safe_quality = {
        key: _safe_value(value)
        for key, value in quality.items()
        if key in quality_keys
    }
    safe_quality["status"] = status
    return VisualizationPackV1.model_validate({
        "summary": {
            "title": _safe_text(summary.get("title") or "시각화"),
            "status": _safe_text(status),
            "subject": _safe_text(summary.get("subject") or "대상 조건"),
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
                    {"field": "revenue", "label": "매출", "unit": "원"},
                    {"field": "ufcf", "label": "UFCF", "unit": "원"},
                ], "rows": projections,
            },
            {
                "id": "dcf_sensitivity", "title": "DCF 민감도",
                "columns": [
                    {"field": "wacc", "label": "WACC", "unit": "%"},
                    {"field": "terminal_growth", "label": "영구성장률", "unit": "%"},
                    {"field": "enterprise_value", "label": "기업가치", "unit": "원"},
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
                {"field": "subject_value", "label": "대상회사"},
                {"field": "p25", "label": "P25"},
                {"field": "p50", "label": "P50"},
                {"field": "p75", "label": "P75"},
                {"field": "percentile", "label": "백분위", "unit": "%"},
                {"field": "n", "label": "Peer 수"},
            ], "rows": rows,
        })
        pack["charts"].append({
            "id": "peer_distribution", "type": "bar", "title": "Peer 분포",
            "data_ref": "peer_metric_matrix",
            "encodings": {"x": {"field": "metric"}, "y": {"field": "subject_value"}},
        })
    elif family == "group_graph":
        rows = result.get("entities") or []
        pack["tables"].append({
            "id": "group_entities", "title": "그룹 실체",
            "columns": [
                {"field": "name", "label": "회사"},
                {"field": "relation", "label": "관계"},
                {"field": "ownership_pct", "label": "지분율", "unit": "%"},
            ], "rows": rows,
        })
        definition = _group_definition(
            _safe_text(result.get("entity_name") or "대상 회사"),
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
        rows = result.get("events") or []
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


def build_visualization_pack(result: dict[str, Any]) -> VisualizationPackV1:
    """Build and validate a portable visualization pack without database I/O."""
    if not isinstance(result, dict):
        raise TypeError("result must be a dict")
    if result.get("kind") == "answer_pack" or "tables" in result:
        return _from_legacy_pack(result)
    return _from_legacy_pack(_raw_family_pack(result))


def _mermaid_text(value: Any) -> str:
    text = _safe_text(value)
    escaped = (
        text.replace("\\", "&#92;")
        .replace("|", "/")
        .replace('"', '\\"')
        .replace("[", "(")
        .replace("]", ")")
        .replace("{", "(")
        .replace("}", ")")
        .replace("`", "'")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )
    return re.sub(
        r"(?i)\b(click|style|classdef|linkstyle)\b",
        lambda match: f"{match.group(1)[0]}&#8203;{match.group(1)[1:]}",
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
    return DiagramSpecV1(
        id="group_structure",
        title="그룹 구조",
        table_ref="group_entities",
        definition=_group_definition(entity_name, []),
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


def render_visualization_markdown(
    pack: VisualizationPackV1,
    *,
    mermaid: bool,
) -> str:
    """Render canonical tables, optionally followed by safe Mermaid diagrams."""
    validated = VisualizationPackV1.model_validate(pack)
    lines: list[str] = []
    for table in validated.tables:
        lines.extend([f"### {_markdown_cell(table.title)}", ""])
        headers = [
            f"{column.label}{f' ({column.unit})' if column.unit else ''}"
            for column in table.columns
        ]
        lines.append("| " + " | ".join(_markdown_cell(item) for item in headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in table.rows:
            lines.append("| " + " | ".join(
                _markdown_cell(row.get(column.key, "-"))
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
    return "\n".join(lines).strip()


def render_visualization_html(pack: VisualizationPackV1) -> str:
    """Render a bounded, self-contained HTML table resource from validated facts."""
    validated = VisualizationPackV1.model_validate(pack)
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
    ]
    for table in validated.tables:
        pieces.append(f"<section><h2>{html.escape(table.title)}</h2><table><thead><tr>")
        for column in table.columns:
            label = column.label + (f" ({column.unit})" if column.unit else "")
            pieces.append(f"<th>{html.escape(label)}</th>")
        pieces.append("</tr></thead><tbody>")
        rows = table.rows or [{table.columns[0].key: table.note or "데이터 미확보"}]
        for row in rows:
            pieces.append("<tr>")
            for column in table.columns:
                value = row.get(column.key, "-")
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
    pieces.append("</body></html>")
    rendered = "".join(pieces)
    if len(rendered.encode()) > 200_000:
        raise ValueError("visualization HTML exceeds bounds")
    return rendered
