from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import get_session


METRIC_MAP = {
    "ifrs-full_Assets": ("assets", "자산총계"),
    "ifrs-full_Liabilities": ("liabilities", "부채총계"),
    "ifrs-full_Equity": ("equity", "자본총계"),
    "ifrs-full_Revenue": ("revenue", "매출액"),
    "ifrs-full_ProfitLoss": ("profit_loss", "당기순손익"),
    "ifrs-full_OperatingProfitLoss": ("operating_profit", "영업손익"),
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": ("operating_cash_flow", "영업활동현금흐름"),
    "ifrs-full_CashFlowsFromUsedInInvestingActivities": ("investing_cash_flow", "투자활동현금흐름"),
    "ifrs-full_CashFlowsFromUsedInFinancingActivities": ("financing_cash_flow", "재무활동현금흐름"),
}


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
    with get_session() as session:
        rows = session.execute(sql, params).mappings().all()
        for row in rows:
            metric_key, metric_name = METRIC_MAP[row["account_id"]]
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
                "corp_code": row["corp_code"],
                "bsns_year": row["bsns_year"],
                "fs_div": row["fs_div"],
                "metric_key": metric_key,
                "metric_name": metric_name,
                "amount": row["thstrm_amount"],
                "source_account_id": row["account_id"],
                "source_account_nm": row["account_nm"],
            })
            inserted_or_updated += 1
        session.commit()
    return {"source_rows": len(rows), "inserted_or_updated": inserted_or_updated}
