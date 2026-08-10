"""Search indexed DART disclosure events."""
from __future__ import annotations

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module


def search_disclosure_events(
    *,
    company: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    event_types: list[str] | None = None,
    market: str | None = None,
    limit: int = 50,
) -> dict:
    where = ["1=1"]
    params: dict[str, object] = {"row_limit": max(1, min(int(limit), 500))}
    if company:
        where.append("de.corp_code=:corp_code")
        params["corp_code"] = company
    if start_date:
        where.append("date(de.event_date) >= :start_date")
        params["start_date"] = start_date
    if end_date:
        where.append("date(de.event_date) <= :end_date")
        params["end_date"] = end_date
    if event_types:
        where.append("de.event_type IN :event_types")
        params["event_types"] = event_types
    if market:
        where.append("c.market=:market")
        params["market"] = market
    stmt = text(f"""
        SELECT de.rcept_no, de.corp_code, c.stock_code, c.corp_name, c.market,
               c.induty_code, de.event_date, de.event_type, de.event_title,
               de.severity_hint, de.source_report_nm
        FROM disclosure_events de
        JOIN companies c ON c.corp_code=de.corp_code
        WHERE {" AND ".join(where)}
        ORDER BY de.event_date DESC, de.severity_hint, c.corp_name
        LIMIT :row_limit
    """)
    if event_types:
        stmt = stmt.bindparams(bindparam("event_types", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, params).mappings()]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["event_type"]] = counts.get(row["event_type"], 0) + 1
    return {
        "query": {
            "company": company,
            "start_date": start_date,
            "end_date": end_date,
            "event_types": event_types,
            "market": market,
            "limit": limit,
        },
        "total_events": len(rows),
        "event_type_counts": counts,
        "events": rows,
        "data_quality": {
            "status": "usable" if rows else "missing",
            "source": "disclosure_events",
            "storage_policy": "list_only_preload_body_on_demand",
            "interpretation": (
                "Events are classified from cached DART disclosure-list metadata, not preloaded source bodies. "
                "Use search_dataset with dataset=evidence_documents for locally indexed source-body evidence, then follow the DART source link when needed."
            ),
        },
    }
