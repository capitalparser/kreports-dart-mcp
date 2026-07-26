"""Bounded parser for audit-fee tables in already-cached business reports."""
from __future__ import annotations

import re
from lxml import html

from kreports.collector.audit_fee_sources import (
    AuditFeeObservation,
    normalize_fee_m,
    normalize_hours,
)


_AUDIT_MARKERS = (
    "감사보수",
    "감사 보수",
    "감사시간",
    "감사 시간",
    "회계감사",
)
_ACTUAL_MARKERS = ("실제", "수행", "집행")
_CONTRACT_MARKERS = ("계약", "예정")
_FEE_MARKERS = ("보수", "금액", "fee")
_HOUR_MARKERS = ("시간", "hour")
_AUDITOR_MARKERS = ("감사인", "회계법인", "auditor")
_YEAR_MARKERS = ("사업연도", "연도", "기수", "period")


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _unit_for_text(text_value: str) -> str | None:
    cleaned = _clean(text_value).lower()
    labeled = re.search(
        r"(?:단위|unit)\s*[:：]\s*(백만원|천원|억원|원|million\s*krw|krw\s*million)",
        cleaned,
        flags=re.IGNORECASE,
    )
    if labeled:
        unit = labeled.group(1).replace(" ", "")
        return "백만원" if unit in {"millionkrw", "krwmillion"} else unit
    parenthetical = re.search(
        r"[\(\[]\s*(백만원|천원|억원|원|million\s*krw|krw\s*million)\s*[\)\]]",
        cleaned,
        flags=re.IGNORECASE,
    )
    if parenthetical:
        unit = parenthetical.group(1).replace(" ", "")
        return "백만원" if unit in {"millionkrw", "krwmillion"} else unit
    if cleaned.strip() in {"million krw", "krw million"}:
        return "백만원"
    return None


def _is_standalone_unit_label(text_value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*\(?\s*(?:단위|unit)\s*[:：]\s*"
            r"(?:백만원|천원|억원|원|million\s*krw|krw\s*million)"
            r"(?:\s*,?\s*시간)?\s*\)?\s*",
            _clean(text_value),
            flags=re.IGNORECASE,
        )
    )


def _has_audit_fee_semantics(text_value: str) -> bool:
    compact = text_value.lower().replace(" ", "")
    explicit = any(marker.replace(" ", "") in compact for marker in _AUDIT_MARKERS)
    structured = (
        any(marker in compact for marker in _AUDITOR_MARKERS)
        and any(marker in compact for marker in (*_ACTUAL_MARKERS, *_CONTRACT_MARKERS))
        and any(marker in compact for marker in _FEE_MARKERS)
    )
    return explicit or structured


def _table_grid(table, max_rows: int) -> list[list[str]]:
    """Expand bounded rowspan/colspan cells into a deterministic text grid."""
    grid: list[list[str]] = []
    active: dict[int, tuple[str, int]] = {}
    for row_node in table.xpath(".//tr")[: max_rows + 4]:
        row: dict[int, str] = {
            column: value
            for column, (value, remaining) in active.items()
            if remaining > 0
        }
        active = {
            column: (value, remaining - 1)
            for column, (value, remaining) in active.items()
            if remaining > 1
        }
        column = 0
        for cell in row_node.xpath("./th|./td"):
            while column in row:
                column += 1
            value = _clean(cell.text_content())
            try:
                colspan = max(1, min(int(cell.get("colspan") or 1), 20))
                rowspan = max(1, min(int(cell.get("rowspan") or 1), 20))
            except ValueError:
                colspan = rowspan = 1
            for offset in range(colspan):
                target = column + offset
                row[target] = value
                if rowspan > 1:
                    active[target] = (value, rowspan - 1)
            column += colspan
        if row:
            width = min(max(row) + 1, 50)
            grid.append([row.get(index, "") for index in range(width)])
    return grid


def _column_kind(header: str) -> str | None:
    lowered = header.lower().replace(" ", "")
    if any(marker.replace(" ", "") in lowered for marker in _AUDITOR_MARKERS):
        return "auditor"
    if any(marker.replace(" ", "") in lowered for marker in _YEAR_MARKERS):
        return "year"
    is_fee = any(marker in lowered for marker in _FEE_MARKERS)
    is_hours = any(marker in lowered for marker in _HOUR_MARKERS)
    is_actual = any(marker in lowered for marker in _ACTUAL_MARKERS)
    is_contract = any(marker in lowered for marker in _CONTRACT_MARKERS)
    if is_fee and is_actual:
        return "actual_fee"
    if is_hours and is_actual:
        return "actual_hours"
    if is_fee and is_contract:
        return "contract_fee"
    if is_hours and is_contract:
        return "contract_hours"
    # Some filings use a two-row header whose first row is lost after HTML
    # normalization. Treat unqualified fee/hour as contract, never as actual.
    if is_fee:
        return "contract_fee"
    if is_hours:
        return "contract_hours"
    return None


def _candidate_table_text(table) -> str:
    return _clean(table.text_content())


def _extract_year(value: str, fallback: int) -> int:
    match = re.search(r"(20\d{2})", value)
    return int(match.group(1)) if match else fallback


def _parse_candidate(
    table,
    *,
    corp_code: str,
    bsns_year: int,
    rcept_no: str | None,
    outer_unit: str | None,
    max_rows: int,
) -> list[AuditFeeObservation]:
    raw_rows = _table_grid(table, max_rows)
    if len(raw_rows) < 2:
        return [
            AuditFeeObservation(
                corp_code=corp_code,
                bsns_year=bsns_year,
                source_class="cached_business_report",
                source_rcept_no=rcept_no,
                availability_status="parse_error",
                quality_status="error",
                limitations=("candidate audit-fee table has no data row",),
            )
        ]

    header_rows = raw_rows[: min(3, len(raw_rows) - 1)]
    width = max(len(row) for row in header_rows)
    header = [
        " ".join(
            dict.fromkeys(
                row[column]
                for row in header_rows
                if column < len(row) and row[column]
            )
        )
        for column in range(width)
    ]
    header_depth = next(
        (
            depth
            for depth in range(1, len(header_rows) + 1)
            if any(
                _column_kind(
                    " ".join(
                        dict.fromkeys(
                            row[column]
                            for row in header_rows[:depth]
                            if column < len(row) and row[column]
                        )
                    )
                )
                in {"actual_fee", "contract_fee"}
                for column in range(width)
            )
        ),
        len(header_rows),
    )
    kinds = [_column_kind(cell) for cell in header]
    if not any(kind in {"actual_fee", "contract_fee"} for kind in kinds):
        return [
            AuditFeeObservation(
                corp_code=corp_code,
                bsns_year=bsns_year,
                source_class="cached_business_report",
                source_rcept_no=rcept_no,
                availability_status="parse_error",
                quality_status="error",
                limitations=("candidate table does not identify audit fee columns",),
            )
        ]

    unit = _unit_for_text(" ".join(header)) or outer_unit
    if unit is None:
        return [
            AuditFeeObservation(
                corp_code=corp_code,
                bsns_year=bsns_year,
                source_class="cached_business_report",
                source_rcept_no=rcept_no,
                availability_status="parse_error",
                quality_status="error",
                limitations=("audit fee displayed unit is missing",),
            )
        ]

    observations: list[AuditFeeObservation] = []
    seen: set[tuple[object, ...]] = set()
    for row in raw_rows[header_depth : header_depth + max_rows]:
        if len(row) < len(header):
            continue
        values = {kind: row[index] for index, kind in enumerate(kinds) if kind}
        contract_fee = normalize_fee_m(values.get("contract_fee"), unit)
        actual_fee = normalize_fee_m(values.get("actual_fee"), unit)
        contract_hours = normalize_hours(values.get("contract_hours"))
        actual_hours = normalize_hours(values.get("actual_hours"))
        if not any(
            value is not None
            for value in (contract_fee, actual_fee, contract_hours, actual_hours)
        ):
            continue
        year = _extract_year(values.get("year", ""), bsns_year)
        key = (
            year,
            values.get("auditor"),
            contract_fee,
            contract_hours,
            actual_fee,
            actual_hours,
        )
        if key in seen:
            continue
        seen.add(key)
        populated = sum(
            value is not None
            for value in (contract_fee, contract_hours, actual_fee, actual_hours)
        )
        observations.append(
            AuditFeeObservation(
                corp_code=corp_code,
                bsns_year=year,
                source_class="cached_business_report",
                contract_fee_m=contract_fee,
                contract_hours=contract_hours,
                actual_fee_m=actual_fee,
                actual_hours=actual_hours,
                auditor_nm=values.get("auditor") or None,
                source_rcept_no=rcept_no,
                source_period=values.get("year") or str(year),
                availability_status="available" if populated == 4 else "partial",
                quality_status="verified",
                displayed_unit=unit,
                raw_values={
                    key: values.get(key)
                    for key in (
                        "contract_fee",
                        "contract_hours",
                        "actual_fee",
                        "actual_hours",
                    )
                },
                limitations=() if populated == 4 else ("cached report fields are incomplete",),
            )
        )
    return observations


def parse_audit_fee_table(
    source_text: str | bytes,
    *,
    corp_code: str = "",
    bsns_year: int,
    rcept_no: str | None = None,
    max_input_chars: int = 2_000_000,
    max_tables: int = 100,
    max_rows: int = 200,
) -> list[AuditFeeObservation]:
    """Parse audit fee observations from bounded cached HTML/XML/text.

    This function is deliberately network-free. Unrelated fee tables are
    ignored; recognized but malformed audit-fee tables yield a typed parse
    error observation instead of invented numeric values.
    """
    if isinstance(source_text, bytes):
        bounded = source_text[:max_input_chars].decode("utf-8", errors="replace")
    else:
        bounded = str(source_text or "")[:max_input_chars]
    if not bounded or not _has_audit_fee_semantics(bounded):
        return []
    try:
        root = html.fromstring(bounded)
    except (ValueError, TypeError):
        return [
            AuditFeeObservation(
                corp_code=corp_code,
                bsns_year=bsns_year,
                source_class="cached_business_report",
                source_rcept_no=rcept_no,
                availability_status="parse_error",
                quality_status="error",
                limitations=("cached business report markup is malformed",),
            )
        ]

    output: list[AuditFeeObservation] = []
    tables = ([root] if str(root.tag).lower() == "table" else []) + root.xpath(
        ".//table"
    )
    for table in tables[:max_tables]:
        table_text = _candidate_table_text(table)
        if not _has_audit_fee_semantics(table_text):
            continue
        preceding = table.xpath("./preceding::*[self::p or self::div][1]")
        local_unit = _unit_for_text(table_text)
        if (
            local_unit is None
            and preceding
            and _is_standalone_unit_label(preceding[-1].text_content())
        ):
            local_unit = _unit_for_text(preceding[-1].text_content())
        output.extend(
            _parse_candidate(
                table,
                corp_code=corp_code,
                bsns_year=bsns_year,
                rcept_no=rcept_no,
                outer_unit=local_unit,
                max_rows=max_rows,
            )
        )
    return output[:max_rows]
