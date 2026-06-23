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
    "dart_OperatingIncomeLoss": ("operating_profit", "영업손익"),
    "ifrs-full_IncomeTaxExpenseContinuingOperations": ("tax_expense", "법인세비용"),
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": ("operating_cash_flow", "영업활동현금흐름"),
    "ifrs-full_CashFlowsFromUsedInInvestingActivities": ("investing_cash_flow", "투자활동현금흐름"),
    "ifrs-full_CashFlowsFromUsedInFinancingActivities": ("financing_cash_flow", "재무활동현금흐름"),
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": ("purchase_ppe", "유형자산 취득"),
    "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities": ("purchase_intangible_assets", "무형자산 취득"),
}

SUMMARY_METRIC_MAP = {
    "revenue": ("revenue", "매출액"),
    "operating_profit": ("operating_profit", "영업손익"),
    "net_income": ("profit_loss", "당기순손익"),
    "total_assets": ("assets", "자산총계"),
    "total_debt": ("liabilities", "부채총계"),
    "total_equity": ("equity", "자본총계"),
    "operating_cf": ("operating_cash_flow", "영업활동현금흐름"),
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
    summary_inserted_or_updated = 0
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
