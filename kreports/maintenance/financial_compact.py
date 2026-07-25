from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import get_session
from kreports.semantic.metrics import FINANCIAL_SUMMARY_FIELD_METRICS, compact_metric_definitions, metric_definition


METRIC_MAP = {
    account_id: (definition.key, definition.label_ko)
    for definition in compact_metric_definitions()
    for account_id in definition.source_account_ids
}
SUMMARY_METRIC_MAP = {
    source_field: (metric_key, metric_definition(metric_key).label_ko)
    for source_field, metric_key in FINANCIAL_SUMMARY_FIELD_METRICS.items()
}

_COMPACT_METRICS = {definition.key: definition for definition in compact_metric_definitions()}


def _compact_rows(rows: list[dict]) -> list[dict]:
    """Resolve registered source groups into one deterministic compact row each."""
    candidates: dict[tuple[str, int, str, str], dict[str, list[dict]]] = {}
    for row in rows:
        metric_key, _ = METRIC_MAP[row["account_id"]]
        key = (row["corp_code"], int(row["bsns_year"]), row["fs_div"], metric_key)
        candidates.setdefault(key, {}).setdefault(row["account_id"], []).append(dict(row))

    compact_rows: list[dict] = []
    for (corp_code, bsns_year, fs_div, metric_key), by_account in candidates.items():
        definition = _COMPACT_METRICS[metric_key]
        selected: list[dict] = []
        for account_group in definition.source_account_groups:
            selected = [
                by_account[account_id][0]
                for account_id in account_group
                if by_account.get(account_id) and by_account[account_id][0]["thstrm_amount"] is not None
            ]
            if selected:
                break
        if not selected:
            continue

        if definition.aggregation == "sum":
            amount = sum(row["thstrm_amount"] for row in selected)
            source_account_id = "+".join(row["account_id"] for row in selected)
            source_account_nm = " + ".join(row["account_nm"] for row in selected)
        else:
            source = selected[0]
            amount = source["thstrm_amount"]
            source_account_id = source["account_id"]
            source_account_nm = source["account_nm"]
        compact_rows.append({
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "metric_key": metric_key,
            "metric_name": definition.label_ko,
            "amount": amount,
            "source_account_id": source_account_id,
            "source_account_nm": source_account_nm,
        })
    return compact_rows


def rebuild_financial_facts_compact(*, year_from: int | None = None, year_to: int | None = None) -> dict:
    params: dict[str, object] = {}
    account_placeholders = []
    for idx, account_id in enumerate(METRIC_MAP):
        key = f"account_id_{idx}"
        account_placeholders.append(f":{key}")
        params[key] = account_id

    where = ["reprt_code='11011'", f"account_id IN ({', '.join(account_placeholders)})"]
    if year_from is not None:
        where.append("bsns_year >= :year_from")
        params["year_from"] = int(year_from)
    if year_to is not None:
        where.append("bsns_year <= :year_to")
        params["year_to"] = int(year_to)

    sql = text(f"""
        SELECT corp_code, bsns_year, fs_div, account_id, account_nm, thstrm_amount
        FROM financial_facts
        WHERE {" AND ".join(where)}
    """)
    inserted_or_updated = 0
    summary_inserted_or_updated = 0
    with get_session() as session:
        rows = session.execute(sql, params).mappings().all()
        for row in _compact_rows(rows):
            session.execute(text("""
                INSERT INTO financial_facts_compact
                (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
                 source_account_id, source_account_nm, fetched_at)
                VALUES
                (:corp_code, :bsns_year, :fs_div, :metric_key, :metric_name, :amount,
                 :source_account_id, :source_account_nm, CURRENT_TIMESTAMP)
                ON CONFLICT(corp_code, bsns_year, fs_div, metric_key) DO UPDATE SET
                  amount=excluded.amount,
                  source_account_id=excluded.source_account_id,
                  source_account_nm=excluded.source_account_nm,
                  fetched_at=excluded.fetched_at
            """), {
                **row,
            })
            inserted_or_updated += 1

        summary_where = ["quarter=4"]
        summary_params: dict[str, object] = {}
        if year_from is not None:
            summary_where.append("year >= :year_from")
            summary_params["year_from"] = int(year_from)
        if year_to is not None:
            summary_where.append("year <= :year_to")
            summary_params["year_to"] = int(year_to)

        summary_rows = session.execute(text(f"""
            SELECT corp_code, year AS bsns_year, fs_div,
                   revenue, operating_profit, net_income,
                   total_assets, total_debt, total_equity, operating_cf
            FROM financials
            WHERE {" AND ".join(summary_where)}
        """), summary_params).mappings().all()
        for row in summary_rows:
            for source_field, (metric_key, metric_name) in SUMMARY_METRIC_MAP.items():
                amount = row[source_field]
                if amount is None:
                    continue
                result = session.execute(text("""
                    INSERT INTO financial_facts_compact
                    (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
                     source_account_id, source_account_nm, fetched_at)
                    VALUES
                    (:corp_code, :bsns_year, :fs_div, :metric_key, :metric_name, :amount,
                     :source_account_id, :source_account_nm, CURRENT_TIMESTAMP)
                    ON CONFLICT(corp_code, bsns_year, fs_div, metric_key) DO UPDATE SET
                      amount=excluded.amount,
                      source_account_id=excluded.source_account_id,
                      source_account_nm=excluded.source_account_nm,
                      fetched_at=excluded.fetched_at
                    WHERE financial_facts_compact.amount IS NULL
                       OR financial_facts_compact.source_account_id LIKE 'financials.%'
                """), {
                    "corp_code": row["corp_code"],
                    "bsns_year": row["bsns_year"],
                    "fs_div": row["fs_div"],
                    "metric_key": metric_key,
                    "metric_name": metric_name,
                    "amount": amount,
                    "source_account_id": f"financials.{source_field}",
                    "source_account_nm": metric_name,
                })
                summary_inserted_or_updated += int(result.rowcount or 0)
        session.commit()
    return {
        "source_rows": len(rows),
        "inserted_or_updated": inserted_or_updated,
        "summary_source_rows": len(summary_rows),
        "summary_inserted_or_updated": summary_inserted_or_updated,
        "total_inserted_or_updated": inserted_or_updated + summary_inserted_or_updated,
    }
