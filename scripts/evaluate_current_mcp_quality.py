#!/usr/bin/env python3
"""Evaluate current read-only MCP tool quality from the local DB only."""
from __future__ import annotations

import json
from typing import Any

from kreports.mcp.tools import call_tool


DEFAULT_COMPANIES = ["005930", "005380", "035720", "247540", "900290"]


def _call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return json.loads(call_tool(name, args))


def _quality(status: str | None, count: int | None = None) -> str:
    if status in {"missing", "cache_missing", "not_persisted", "subject_missing"}:
        return "FAIL"
    if status in {"limited", "subject_only"}:
        return "LIMITED"
    if count is not None and count <= 0:
        return "LIMITED"
    return "PASS"


def _row(company: str, year: int) -> dict[str, Any]:
    fin = _call("get_financial_snapshot", {"company": company})
    peer = _call("select_peer_group", {"company": company, "peer_limit": 10})
    fee = _call("compare_peer_audit_fees", {"company": company, "year": year})
    policy = _call("compare_peer_accounting_policies", {"company": company, "year": year})
    kam = _call("get_audit_report_sections", {"company": company, "year": year, "section_key": "kam"})
    overview = _call("get_business_overview", {"company": company, "year": year})
    pack = _call("build_audit_acceptance_pack", {"company": company, "year": year})

    policy_q = policy.get("data_quality") or {}
    kam_q = kam.get("data_quality") or {}
    overview_q = overview.get("data_quality") or {}
    pack_q = pack.get("data_quality") or {}
    return {
        "company": company,
        "year": year,
        "financial_rows": len(fin.get("rows") or []),
        "financial_quality": _quality(None, len(fin.get("rows") or [])),
        "peer_count": peer.get("peer_count"),
        "audit_fee_peer_count": fee.get("peer_count"),
        "policy_status": policy_q.get("status"),
        "policy_subject_items": policy.get("subject_policy_count"),
        "policy_peer_coverage_pct": policy_q.get("peer_coverage_pct"),
        "kam_status": kam_q.get("status"),
        "kam_section_count": kam.get("section_count"),
        "kam_available_years": kam_q.get("available_audit_report_years"),
        "kam_latest_available_year": kam_q.get("latest_available_year"),
        "kam_alternative_sections": kam_q.get("alternative_section_count"),
        "kam_reason_cov": (kam_q.get("kam_reason_coverage") or {}).get("coverage_pct"),
        "kam_procedure_cov": (kam_q.get("kam_procedure_coverage") or {}).get("coverage_pct"),
        "business_overview_status": overview_q.get("status"),
        "business_overview_sections": overview.get("section_count"),
        "business_overview_available_years": overview_q.get("available_business_report_years"),
        "acceptance_policy_status": (pack_q.get("policy_cache") or {}).get("status"),
        "acceptance_kam_status": (pack_q.get("kam_body") or {}).get("status"),
    }


def main() -> None:
    print("company\tyear\tfinancial\tpeer\taudit_fee_peer\tpolicy\tpolicy_items\tpolicy_peer_cov\tkam\tkam_sections\tkam_years\tkam_latest_year\tkam_alt_sections\tkam_reason_cov\tkam_procedure_cov\tbusiness_overview\tbusiness_sections\tbusiness_years\tacceptance_policy\tacceptance_kam")
    for year in (2024, 2025):
        for company in DEFAULT_COMPANIES:
            row = _row(company, year)
            print(
                "\t".join(
                    str(row[key])
                    for key in [
                        "company",
                        "year",
                        "financial_rows",
                        "peer_count",
                        "audit_fee_peer_count",
                        "policy_status",
                        "policy_subject_items",
                        "policy_peer_coverage_pct",
                        "kam_status",
                        "kam_section_count",
                        "kam_available_years",
                        "kam_latest_available_year",
                        "kam_alternative_sections",
                        "kam_reason_cov",
                        "kam_procedure_cov",
                        "business_overview_status",
                        "business_overview_sections",
                        "business_overview_available_years",
                        "acceptance_policy_status",
                        "acceptance_kam_status",
                    ]
                )
            )


if __name__ == "__main__":
    main()
