"""Shared JSON, evidence, and schema-normalization primitives for analysis domains."""
from __future__ import annotations

import math
import re
import statistics
from typing import Any

import pandas as pd
from sqlalchemy import text

import kreports.db.engine as _engine_module
from kreports.analysis.evidence import parent_rcept_no

def _clean_value(v: Any) -> Any:
    """
    JSON 직렬화 안전한 값으로 정리.
    - NaN/NaT/pd.NA → None
    - numpy scalar → python scalar
    - pd.Timestamp → ISO string
    """
    if v is None:
        return None
    # pd.NA / NaT
    if v is pd.NA:
        return None
    # pd.Timestamp
    if isinstance(v, pd.Timestamp):
        return v.isoformat()
    # float NaN/Inf
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    # numpy scalar
    if hasattr(v, "item"):
        try:
            v2 = v.item()
            if isinstance(v2, float) and (math.isnan(v2) or math.isinf(v2)):
                return None
            return v2
        except (AttributeError, ValueError):
            pass
    return v


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → list[dict], 모든 값을 JSON-safe하게 정리."""
    if df is None or df.empty:
        return []
    records = df.to_dict(orient="records")
    return [{k: _clean_value(v) for k, v in row.items()} for row in records]


def _clean_dict(d: dict) -> dict:
    """dict 값을 JSON-safe하게 정리 (중첩 리스트 포함)."""
    out: dict = {}
    for k, v in d.items():
        if isinstance(v, list):
            out[k] = [_clean_dict(x) if isinstance(x, dict) else _clean_value(x) for x in v]
        elif isinstance(v, dict):
            out[k] = _clean_dict(v)
        else:
            out[k] = _clean_value(v)
    return out


def _display_text(value: str | None) -> str:
    text_value = value or ""
    text_value = text_value.replace("&cr;", "\n").replace("&#13;", "\n")
    text_value = text_value.replace("&nbsp;", " ").replace("&#160;", " ")
    text_value = text_value.replace("\r", "\n")
    return text_value


def _dedupe_confirmed_facts(facts: list[dict]) -> list[dict]:
    """Collapse repeated evidence facts that differ only by attachment/viewer id."""
    seen: set[tuple[str, ...]] = set()
    deduped: list[dict] = []
    for fact in facts:
        source = fact.get("source") if isinstance(fact, dict) else {}
        source = source if isinstance(source, dict) else {}
        raw_rcept = source.get("rcept_no")
        receipt_key = parent_rcept_no(str(raw_rcept)) or str(raw_rcept or "")
        excerpt = re.sub(r"\s+", " ", str(fact.get("excerpt") or fact.get("statement") or "")).strip()[:160]
        key = (
            str(source.get("corp_code") or source.get("corp_name") or ""),
            str(source.get("bsns_year") or ""),
            receipt_key,
            str(source.get("section_title") or source.get("section_key") or ""),
            excerpt,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(fact)
    return deduped


def _has_db_column(table_name: str, column_name: str) -> bool:
    try:
        with _engine_module.engine.connect() as conn:
            rows = conn.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
        return any(row.get("name") == column_name for row in rows)
    except Exception:
        return False


def _has_db_table(table_name: str) -> bool:
    try:
        with _engine_module.engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"),
                {"name": table_name},
            ).first()
        return row is not None
    except Exception:
        return False


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator * 100, 1)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator, 2)


def _as_float(value: Any) -> float | None:
    value = _clean_value(value)
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _avg(values: list[float | None]) -> float | None:
    cleaned = [v for v in values if v is not None]
    return statistics.fmean(cleaned) if cleaned else None
