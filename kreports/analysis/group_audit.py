"""Group-audit component and subsidiary auditor analysis."""
from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module
from kreports.db.engine import get_session
from kreports.db.models import Auditor, BusinessAffiliateAuditor, Disclosure

from kreports.analysis._shared import _clean_dict, _has_db_column, _has_db_table, _pct
from kreports.analysis.company_profile import resolve_company_identifier
from kreports.analysis.group_graph import (
    GroupGraphUnavailable,
    build_group_graph,
    classify_qsc,
    latest_group_graph_year,
)


_SUBSIDIARY_SLIM_FIELDS = (
    "name", "relation", "ownership_pct", "listed_yn",
    "asset_amount_m", "asset_share_pct", "revenue_amount_m", "revenue_share_pct",
    "is_qsc", "qsc_status", "qsc_basis",
    "corp_code", "stock_code", "market", "auditor",
)


_QSC_THRESHOLD_PCT = 10.0


_QSC_CRITERION = {
    "threshold_pct": _QSC_THRESHOLD_PCT,
    "basis": "asset_share_pct >= 10.0 OR revenue_share_pct >= 10.0",
    "status_values": {
        "qsc": "총자산 또는 총매출 비중이 10% 이상",
        "not_qsc": "총자산과 총매출 비중이 모두 10% 미만",
        "undetermined": "총자산/총매출 비중 산출에 필요한 데이터 부족",
    },
}


def _classify_qsc(asset_share_pct: float | None, revenue_share_pct: float | None) -> dict:
    """Classify Quantitatively Significant Component using the configured group-audit threshold."""
    result = classify_qsc(asset_share_pct, revenue_share_pct)
    return {
        "is_qsc": True if result.status == "qsc" else False
        if result.status == "not_qsc" else None,
        "qsc_status": result.status,
        "qsc_basis": list(result.basis),
    }


def _canonical_graph_payload(corp_code: str, year: int | None) -> dict | None:
    if year is None:
        return None
    try:
        graph = build_group_graph(corp_code, year)
    except GroupGraphUnavailable:
        return None
    if not graph.entities:
        return None
    entities = {entity.entity_key: entity for entity in graph.entities}
    graph_root_keys = {
        entity.entity_key
        for entity in graph.entities
        if (
            entity.entity_key == f"parent:{corp_code}"
            or entity.resolved_corp_code == corp_code
        )
    }
    metrics: dict[str, dict[str, Any]] = {}
    for metric in graph.metrics:
        metrics.setdefault(metric.entity_key, {})[metric.metric_key] = metric
    rows = []
    for edge in graph.relationships:
        entity = entities[edge.child_entity_key]
        by_metric = metrics.get(entity.entity_key, {})
        asset = by_metric.get("assets")
        revenue = by_metric.get("revenue")
        qsc_status = (
            asset.qsc_status if asset is not None
            else revenue.qsc_status if revenue is not None
            else "undetermined"
        )
        qsc_basis = list(
            asset.qsc_basis
            if asset else revenue.qsc_basis if revenue else ()
        )
        asset_amount = asset.amount if asset else None
        revenue_amount = revenue.amount if revenue else None
        rows.append({
            "entity_key": entity.entity_key,
            "parent_entity_key": edge.parent_entity_key,
            "parent_is_root": edge.parent_entity_key in graph_root_keys,
            "name": entity.original_name,
            "relation": edge.relation_type,
            "ownership_pct": edge.ownership_pct,
            "listed_yn": entity.listed_state,
            "business": None,
            "assets": asset_amount,
            "asset_amount": asset_amount,
            "asset_amount_m": (
                float(asset_amount) / 1_000_000
                if asset_amount is not None else None
            ),
            "asset_share_pct": asset.share_pct if asset else None,
            "asset_amount_source": (
                "canonical_group_component_metrics"
                if asset_amount is not None else None
            ),
            "revenue_amount": revenue_amount,
            "revenue_amount_m": (
                float(revenue_amount) / 1_000_000
                if revenue_amount is not None else None
            ),
            "revenue_share_pct": revenue.share_pct if revenue else None,
            "revenue_amount_source": (
                "canonical_group_component_metrics"
                if revenue_amount is not None else None
            ),
            "revenue_gap_reason": revenue.gap_reason if revenue else None,
            "is_qsc": (
                True if qsc_status == "qsc"
                else False if qsc_status == "not_qsc"
                else None
            ),
            "qsc_status": qsc_status,
            "qsc_basis": qsc_basis,
            "corp_code": entity.resolved_corp_code,
            "stock_code": entity.stock_code,
            "market": entity.market,
            "auditor": {
                "auditor_nm": entity.component_auditor_name,
                "bsns_year": entity.component_auditor_year,
                "rcept_no": entity.component_auditor_rcept_no,
                "fs_div": entity.component_auditor_fs_div,
            } if entity.component_auditor_name else None,
            "auditor_gap_reason": entity.auditor_gap_reason,
            "matched_corp_name": (
                entity.original_name if entity.resolved_corp_code else None
            ),
            "source": edge.source_table,
            "source_rcept_no": edge.source_rcept_no,
            "source_table": edge.source_table,
        })
    return {
        "parent_name": graph.parent_name,
        "year": graph.year,
        "entities": rows,
        "limitations": list(graph.limitations),
        "truncated": graph.truncated,
    }


def _canonical_result(
    corp_code: str,
    year: int,
    graph: dict,
    *,
    limit: int | None,
    only_with_auditor: bool,
    slim: bool,
) -> dict:
    all_items = list(graph["entities"])
    items = list(all_items)
    if only_with_auditor:
        items = [item for item in items if item.get("auditor")]
    truncated = False
    if limit is not None and len(items) > limit:
        items = items[:limit]
        truncated = True
    if slim:
        items = [
            {key: item.get(key) for key in _SUBSIDIARY_SLIM_FIELDS}
            for item in items
        ]
    first_receipt = next(
        (
            item.get("source_rcept_no")
            for item in all_items
            if item.get("source_rcept_no")
        ),
        None,
    )
    return _clean_dict({
        "corp_code": corp_code,
        "parent_rcept_no": first_receipt,
        "bsns_year": year,
        "qsc_criterion": _QSC_CRITERION,
        "subsidiaries": items,
        "count": len(items),
        "total": len(all_items),
        "truncated": truncated,
        "group_graph": graph,
        "data_quality": {
            "status": "usable",
            "source": "canonical_group_audit_graph",
            "canonical_graph": "available",
        },
    })


def _component_importance_sort_key(item: dict) -> tuple:
    known_shares = [
        share for share in (item.get("asset_share_pct"), item.get("revenue_share_pct"))
        if share is not None
    ]
    max_share = max(known_shares) if known_shares else -1
    return (
        0 if item.get("qsc_status") == "qsc" else 1,
        0 if item.get("auditor") else 1,
        -max_share,
    )


def _parse_report_amount_m(value: Any) -> float | None:
    """Parse DART report table amounts that are presented in KRW millions."""
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value or text_value in {"-", "—", "N/A", "n/a"}:
        return None
    negative = text_value.startswith("(") and text_value.endswith(")")
    normalized = (
        text_value.replace(",", "")
        .replace("백만원", "")
        .replace("원", "")
        .replace(" ", "")
        .replace("(", "")
        .replace(")", "")
    )
    match = re.search(r"-?\d+(?:\.\d+)?", normalized)
    if not match:
        return None
    try:
        amount = float(match.group(0))
    except ValueError:
        return None
    if negative:
        amount *= -1
    return round(amount, 3)


def _amount_to_m(amount: int | float | None) -> float | None:
    if amount is None:
        return None
    return round(float(amount) / 1_000_000, 3)


def _normalize_entity_name(value: Any) -> str:
    text_value = str(value or "").lower()
    text_value = re.sub(r"\(주\d+(?:,\s*\d+)*\)", "", text_value)
    text_value = text_value.replace("주식회사", "").replace("(주)", "").replace("㈜", "")
    return re.sub(r"[^0-9a-z가-힣]", "", text_value)


def _load_company_names(corp_codes: list[str]) -> dict[str, str]:
    unique_codes = sorted({code for code in corp_codes if code})
    if not unique_codes:
        return {}
    stmt = text("""
        SELECT corp_code, corp_name
        FROM companies
        WHERE corp_code IN :corp_codes
    """).bindparams(bindparam("corp_codes", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = conn.execute(stmt, {"corp_codes": unique_codes}).mappings().all()
    return {str(row["corp_code"]): str(row["corp_name"]) for row in rows}


def _load_year_end_financial_metrics(corp_codes: list[str], bsns_year: int | None) -> dict[str, dict]:
    """Load annual CFS/OFS asset and revenue metrics for parent/affiliate companies."""
    if not corp_codes or bsns_year is None:
        return {}
    unique_codes = sorted({code for code in corp_codes if code})
    if not unique_codes:
        return {}

    metrics: dict[str, dict[str, dict[str, Any]]] = {}
    if _has_db_table("financial_facts_compact"):
        stmt = text("""
            SELECT corp_code, bsns_year, fs_div, metric_key, amount
            FROM financial_facts_compact
            WHERE corp_code IN :corp_codes
              AND bsns_year=:bsns_year
              AND fs_div IN ('CFS', 'OFS')
              AND metric_key IN ('assets', 'revenue')
        """).bindparams(bindparam("corp_codes", expanding=True))
        with _engine_module.engine.connect() as conn:
            rows = conn.execute(stmt, {"corp_codes": unique_codes, "bsns_year": bsns_year}).mappings().all()
        for row in rows:
            code = str(row["corp_code"])
            fs_div = str(row["fs_div"])
            slot = metrics.setdefault(code, {}).setdefault(fs_div, {"fs_div": fs_div, "source": "financial_facts_compact"})
            if row["metric_key"] == "assets":
                slot["assets_amount"] = row["amount"]
            elif row["metric_key"] == "revenue":
                slot["revenue_amount"] = row["amount"]

    missing_codes = [code for code in unique_codes if code not in metrics]
    if missing_codes and _has_db_table("financials"):
        stmt = text("""
            SELECT corp_code, fs_div, revenue, total_assets
            FROM financials
            WHERE corp_code IN :corp_codes
              AND year=:bsns_year
              AND quarter=4
              AND fs_div IN ('CFS', 'OFS')
        """).bindparams(bindparam("corp_codes", expanding=True))
        with _engine_module.engine.connect() as conn:
            rows = conn.execute(stmt, {"corp_codes": missing_codes, "bsns_year": bsns_year}).mappings().all()
        for row in rows:
            code = str(row["corp_code"])
            fs_div = str(row["fs_div"])
            metrics.setdefault(code, {})[fs_div] = {
                "fs_div": fs_div,
                "source": "financials",
                "assets_amount": row["total_assets"],
                "revenue_amount": row["revenue"],
            }

    selected: dict[str, dict] = {}
    for code, by_fs in metrics.items():
        data = by_fs.get("CFS") or by_fs.get("OFS")
        if not data:
            continue
        data = dict(data)
        data.setdefault("assets_amount", None)
        data.setdefault("revenue_amount", None)
        data["assets_amount_m"] = _amount_to_m(data.get("assets_amount"))
        data["revenue_amount_m"] = _amount_to_m(data.get("revenue_amount"))
        selected[code] = data
    return selected


def get_subsidiary_auditors(
    company: str,
    limit: Optional[int] = 100,
    only_with_auditor: bool = False,
    slim: bool = True,
) -> dict:
    """
    최근 사업보고서 기준 종속/관계회사별 감사인 정보.

    대형 그룹(삼성전자 등)은 종속회사가 400개 이상이라 MCP 응답이 수십KB로 커질 수 있다.
    기본값은 QSC 우선 + 상위 100개 + 핵심 필드만 (slim 모드).

    Args:
        company: corp_code / stock_code / 회사명
        limit: 반환 최대 종속회사 수. None이면 전체.
        only_with_auditor: True면 감사인 있는 항목만.
        slim: True면 핵심 8개 필드만 반환 (name, relation, ownership_pct, listed_yn,
              corp_code, stock_code, market, auditor). False면 전체 필드.

    Returns:
        {
          "corp_code", "parent_rcept_no", "bsns_year",
          "subsidiaries": [...],
          "count": int,             # 반환된 개수
          "total": int,              # DB에 있는 전체 개수
          "truncated": bool,
        }
    """
    corp_code = resolve_company_identifier(company)
    if corp_code is None:
        return {
            "corp_code": None,
            "parent_rcept_no": None,
            "bsns_year": None,
            "qsc_criterion": _QSC_CRITERION,
            "subsidiaries": [],
            "count": 0,
            "total": 0,
            "truncated": False,
            "error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다.",
        }

    cached_rows = []
    latest_year = None
    if _has_db_column("subsidiary_auditor_matrix", "parent_corp_code"):
        with get_session() as session:
            cached_orm_rows = (
                session.query(BusinessAffiliateAuditor)
                .filter_by(parent_corp_code=corp_code)
                .order_by(
                    BusinessAffiliateAuditor.bsns_year.desc(),
                    BusinessAffiliateAuditor.parent_rcept_no.desc(),
                    BusinessAffiliateAuditor.ordinal.asc(),
                )
                .all()
            )
            latest_year = cached_orm_rows[0].bsns_year if cached_orm_rows else None
            latest_receipt = (
                cached_orm_rows[0].parent_rcept_no
                if cached_orm_rows else None
            )
            if latest_year is not None and latest_receipt is not None:
                cached_orm_rows = [
                    row for row in cached_orm_rows
                    if (
                        row.bsns_year == latest_year
                        and row.parent_rcept_no == latest_receipt
                    )
                ]
            cached_rows = [
                {
                    "parent_rcept_no": row.parent_rcept_no,
                    "bsns_year": row.bsns_year,
                    "name": row.name,
                    "relation": row.relation,
                    "ownership_pct": row.ownership_pct,
                    "listed_yn": row.listed_yn,
                    "business": row.business,
                    "assets": row.assets,
                    "source": row.source,
                    "corp_code": row.corp_code,
                    "stock_code": row.stock_code,
                    "market": row.market,
                    "auditor_nm": row.auditor_nm,
                    "audit_opinion": row.audit_opinion,
                    "auditor_fs_div": row.auditor_fs_div,
                    "auditor_year": row.auditor_year,
                }
                for row in cached_orm_rows
            ]
    try:
        canonical_year = latest_group_graph_year(corp_code)
    except GroupGraphUnavailable:
        canonical_year = None
    canonical_graph = _canonical_graph_payload(corp_code, canonical_year)
    if (
        canonical_year is not None
        and canonical_graph is not None
        and (latest_year is None or canonical_year > latest_year)
    ):
        return _canonical_result(
            corp_code,
            canonical_year,
            canonical_graph,
            limit=limit,
            only_with_auditor=only_with_auditor,
            slim=slim,
        )
    legacy_auditor_conflicts: set[str] = set()
    legacy_current_auditor_names: dict[str, str] = {}
    if cached_rows and latest_year is not None:
        affiliate_codes = {
            str(item.get("corp_code") or "")
            for item in cached_rows
            if item.get("corp_code")
        }
        if affiliate_codes:
            with get_session() as session:
                exact_claims = (
                    session.query(Auditor.corp_code, Auditor.auditor_nm)
                    .filter(
                        Auditor.bsns_year == latest_year,
                        Auditor.corp_code.in_(affiliate_codes),
                    )
                    .all()
                )
            names_by_code: dict[str, set[str]] = {}
            for claim_code, auditor_name in exact_claims:
                if auditor_name:
                    names_by_code.setdefault(claim_code, set()).add(
                        str(auditor_name).strip()
                    )
            legacy_auditor_conflicts = {
                claim_code for claim_code, names in names_by_code.items()
                if len(names) > 1
            }
            legacy_current_auditor_names = {
                claim_code: next(iter(names))
                for claim_code, names in names_by_code.items()
                if len(names) == 1
            }
    with get_session() as session:
        row = (
            session.query(Disclosure.rcept_no, Disclosure.disc_date, Disclosure.report_nm)
            .filter_by(corp_code=corp_code)
            .filter(Disclosure.report_nm.like("%사업보고서%"))
            .order_by(Disclosure.disc_date.desc())
            .first()
        )
    if cached_rows:
        affiliate_corp_codes = [row.get("corp_code") for row in cached_rows if row.get("corp_code")]
        financial_metrics = _load_year_end_financial_metrics(
            [corp_code] + affiliate_corp_codes,
            latest_year,
        )
        matched_company_names = _load_company_names(affiliate_corp_codes)
        consolidated_totals = financial_metrics.get(corp_code, {})
        consolidated_assets_m = consolidated_totals.get("assets_amount_m")
        consolidated_revenue_m = consolidated_totals.get("revenue_amount_m")
        items = []
        for cached in cached_rows:
            auditor = None
            auditor_gap_reason = None
            if cached.get("corp_code") in legacy_auditor_conflicts:
                auditor_gap_reason = "component_auditor_conflict"
            elif (
                cached["auditor_nm"]
                and cached.get("corp_code") in legacy_current_auditor_names
                and str(cached["auditor_nm"]).strip()
                != legacy_current_auditor_names[cached["corp_code"]]
            ):
                auditor_gap_reason = "component_auditor_correction_mismatch"
            elif (
                cached["auditor_nm"]
                and cached["auditor_year"] != latest_year
            ):
                auditor_gap_reason = "component_auditor_year_mismatch"
            elif cached["auditor_nm"]:
                auditor = {
                    "auditor_nm": cached["auditor_nm"],
                    "bsns_year": cached["auditor_year"],
                    "audit_opinion": cached["audit_opinion"],
                }
            asset_amount_m = _parse_report_amount_m(cached.get("assets"))
            affiliate_corp_code = cached.get("corp_code") or ""
            matched_corp_name = matched_company_names.get(affiliate_corp_code)
            exact_name_match = (
                bool(matched_corp_name)
                and _normalize_entity_name(cached.get("name")) == _normalize_entity_name(matched_corp_name)
            )
            if auditor and not exact_name_match:
                auditor = None
                auditor_gap_reason = "matched_company_name_mismatch"
            revenue_metrics = financial_metrics.get(affiliate_corp_code, {}) if exact_name_match else {}
            revenue_amount = revenue_metrics.get("revenue_amount")
            revenue_amount_m = revenue_metrics.get("revenue_amount_m")
            revenue_gap_reason = None
            if revenue_amount_m is None:
                revenue_gap_reason = (
                    "matched_company_name_mismatch"
                    if affiliate_corp_code and matched_corp_name and not exact_name_match
                    else "entity_revenue_not_cached"
                )
            asset_share_pct = _pct(asset_amount_m, consolidated_assets_m)
            revenue_share_pct = _pct(revenue_amount_m, consolidated_revenue_m)
            qsc_classification = _classify_qsc(asset_share_pct, revenue_share_pct)
            items.append({
                "name": cached["name"],
                "relation": cached["relation"],
                "ownership_pct": cached["ownership_pct"],
                "listed_yn": cached["listed_yn"],
                "business": cached["business"],
                "assets": cached["assets"],
                "asset_amount": int(round(asset_amount_m * 1_000_000)) if asset_amount_m is not None else None,
                "asset_amount_m": asset_amount_m,
                "asset_share_pct": asset_share_pct,
                "asset_amount_source": "business_report_affiliate_table" if asset_amount_m is not None else None,
                "revenue_amount": revenue_amount,
                "revenue_amount_m": revenue_amount_m,
                "revenue_share_pct": revenue_share_pct,
                "revenue_amount_source": "matched_company_financials" if revenue_amount_m is not None else None,
                "revenue_gap_reason": revenue_gap_reason,
                **qsc_classification,
                "auditor_gap_reason": auditor_gap_reason,
                "matched_corp_name": matched_corp_name,
                "source": cached["source"],
                "corp_code": cached["corp_code"],
                "stock_code": cached["stock_code"],
                "market": cached["market"],
                "auditor": auditor,
            })

        total = len(items)
        items_sorted = sorted(items, key=_component_importance_sort_key)
        if only_with_auditor:
            items_sorted = [x for x in items_sorted if x.get("auditor")]
        truncated = False
        if limit is not None and len(items_sorted) > limit:
            items_sorted = items_sorted[:limit]
            truncated = True
        if slim:
            items_sorted = [
                {k: x.get(k) for k in _SUBSIDIARY_SLIM_FIELDS}
                for x in items_sorted
            ]
        coverage = {
            "total_entities": total,
            "entity_assets_with_amount": sum(1 for x in items if x.get("asset_amount_m") is not None),
            "entity_revenue_with_amount": sum(1 for x in items if x.get("revenue_amount_m") is not None),
            "asset_share_calculated": sum(1 for x in items if x.get("asset_share_pct") is not None),
            "revenue_share_calculated": sum(1 for x in items if x.get("revenue_share_pct") is not None),
            "entity_auditor_with_exact_match": sum(1 for x in items if x.get("auditor") is not None),
            "auditor_hidden_name_mismatch": sum(
                1 for x in items if x.get("auditor_gap_reason") == "matched_company_name_mismatch"
            ),
            "qsc_count": sum(1 for x in items if x.get("is_qsc") is True),
            "qsc_classified": sum(1 for x in items if x.get("qsc_status") in {"qsc", "not_qsc"}),
            "qsc_undetermined": sum(1 for x in items if x.get("qsc_status") == "undetermined"),
            "consolidated_assets_available": consolidated_assets_m is not None,
            "consolidated_revenue_available": consolidated_revenue_m is not None,
        }
        coverage_note = (
            "개별 실체 자산은 사업보고서 종속회사/타법인출자 표의 백만원 단위 금액을 사용합니다. "
            "개별 실체 매출은 해당 표에 없는 경우가 많아, corp_code가 매칭되고 연간 재무정보가 "
            "있더라도 회사명이 정확히 일치하는 경우에만 matched_company_financials 기준으로 보조 산출합니다. "
            "감사인 정보도 회사명 정확매칭이 확인된 경우에만 표시합니다."
        )

        result = _clean_dict({
            "corp_code": corp_code,
            "parent_rcept_no": cached_rows[0]["parent_rcept_no"],
            "bsns_year": latest_year,
            "consolidated_totals": {
                "fs_div": consolidated_totals.get("fs_div"),
                "assets_amount": consolidated_totals.get("assets_amount"),
                "assets_amount_m": consolidated_totals.get("assets_amount_m"),
                "revenue_amount": consolidated_totals.get("revenue_amount"),
                "revenue_amount_m": consolidated_totals.get("revenue_amount_m"),
                "source": consolidated_totals.get("source"),
            },
            "qsc_criterion": _QSC_CRITERION,
            "subsidiaries": items_sorted,
            "count": len(items_sorted),
            "total": total,
            "truncated": truncated,
            "data_quality": {
                "status": "usable",
                "source": "local_subsidiary_auditor_matrix",
                "coverage": coverage,
                "coverage_note": coverage_note,
            },
        })
        same_year_graph = (
            canonical_graph if canonical_year == latest_year else None
        )
        if same_year_graph is not None:
            result["group_graph"] = same_year_graph
            result["data_quality"]["canonical_graph"] = "available"
        return result
    if canonical_year is not None and canonical_graph is not None:
        return _canonical_result(
            corp_code,
            canonical_year,
            canonical_graph,
            limit=limit,
            only_with_auditor=only_with_auditor,
            slim=slim,
        )
    if row is None:
        return {
            "corp_code": corp_code,
            "parent_rcept_no": None,
            "bsns_year": None,
            "qsc_criterion": _QSC_CRITERION,
            "subsidiaries": [],
            "count": 0,
            "total": 0,
            "truncated": False,
            "data_quality": {
                "status": "cache_missing",
                "source": "local_subsidiary_auditor_matrix",
            },
            "note": "DB에 사업보고서 공시가 없습니다.",
        }
    disc_date_str = str(row.disc_date)
    try:
        bsns_year = int(disc_date_str[:4]) - 1
    except Exception:
        bsns_year = None

    return {
        "corp_code": corp_code,
        "parent_rcept_no": row.rcept_no,
        "bsns_year": bsns_year,
        "qsc_criterion": _QSC_CRITERION,
        "subsidiaries": [],
        "count": 0,
        "total": 0,
        "truncated": False,
        "parse_errors": [],
            "data_quality": {
                "status": "cache_missing",
                "source": "local_subsidiary_auditor_matrix",
                "missing_reason": "종속회사/관계회사 감사인 매트릭스는 아직 별도 캐시 테이블로 영속화되지 않았습니다.",
            },
        "note": "외부 MCP 런타임에서는 DART API를 호출하지 않습니다. 이 기능은 캐시 테이블 추가 전까지 데이터 없음으로 반환합니다.",
    }
