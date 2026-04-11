"""
policy_collector — 사업보고서 주석 회계정책 영속화.

dashboard.db.get_accounting_policy로 item_key별 heading/body를 추출한 뒤
AccountingPolicyItem 테이블에 upsert한다.

사용:
    from kreports.collector.policy_collector import collect_policies_for_company
    result = collect_policies_for_company(corp_code, bsns_year, fs_div="CFS")
    # → {"items_stored": 15, "items_changed": 3, "rcept_no": "..."}
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kreports.db.engine import get_session
from kreports.db.models import AccountingPolicyItem

logger = logging.getLogger(__name__)


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def collect_policies_for_company(
    corp_code: str,
    bsns_year: int,
    fs_div: str = "CFS",
) -> dict:
    """
    특정 기업·사업연도의 회계정책 item들을 수집·영속화한다.
    기존 레코드의 body_hash와 비교하여 변경된 항목을 카운트한다.

    Returns:
        {
          "corp_code", "bsns_year", "fs_div", "rcept_no",
          "items_stored": int,
          "items_new": int,
          "items_changed": int,
          "items_unchanged": int,
          "error": str | None,
        }
    """
    from kreports.analysis import queries as _q

    result = {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "fs_div": fs_div,
        "rcept_no": None,
        "items_stored": 0,
        "items_new": 0,
        "items_changed": 0,
        "items_unchanged": 0,
        "error": None,
    }

    try:
        policy_data = _q.get_accounting_policy(
            corp_code, bsns_year, fs_div=fs_div
        )
    except Exception as e:
        result["error"] = f"추출 실패: {type(e).__name__}: {e}"
        return result

    if policy_data is None:
        result["error"] = "사업보고서 또는 주석 파일을 찾지 못했습니다."
        return result

    rcept_no = policy_data.get("rcept_no")
    items = policy_data.get("items") or {}
    if not rcept_no or not items:
        result["error"] = "추출된 item이 없습니다."
        result["rcept_no"] = rcept_no
        return result

    result["rcept_no"] = rcept_no

    # 기존 레코드 조회 (corp+year+fs_div) → {item_key: body_hash}
    with get_session() as session:
        existing_rows = (
            session.query(
                AccountingPolicyItem.item_key,
                AccountingPolicyItem.body_hash,
            )
            .filter_by(
                corp_code=corp_code, bsns_year=bsns_year, fs_div=fs_div
            )
            .all()
        )
    existing_hash_by_key = {r[0]: r[1] for r in existing_rows}

    now = datetime.utcnow()
    upsert_rows: list[dict] = []

    for item_key, item in items.items():
        if not isinstance(item, dict):
            continue
        body = (item.get("body") or "").strip()
        heading = (item.get("heading") or "").strip() or None
        if not body:
            continue
        body_hash = _sha1(body)

        prior_hash = existing_hash_by_key.get(item_key)
        if prior_hash is None:
            result["items_new"] += 1
        elif prior_hash != body_hash:
            result["items_changed"] += 1
        else:
            result["items_unchanged"] += 1

        upsert_rows.append({
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "rcept_no": rcept_no,
            "item_key": item_key,
            "heading": heading[:500] if heading else None,
            "body": body,
            "body_hash": body_hash,
            "body_length": len(body),
            "fetched_at": now,
        })

    if not upsert_rows:
        result["error"] = "유효한 body가 있는 item이 없습니다."
        return result

    # Bulk upsert
    with get_session() as session:
        stmt = sqlite_insert(AccountingPolicyItem).values(upsert_rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["corp_code", "bsns_year", "fs_div", "item_key"],
            set_={
                "rcept_no": stmt.excluded.rcept_no,
                "heading": stmt.excluded.heading,
                "body": stmt.excluded.body,
                "body_hash": stmt.excluded.body_hash,
                "body_length": stmt.excluded.body_length,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        session.execute(stmt)

    result["items_stored"] = len(upsert_rows)
    logger.info(
        "policy upsert %s/%d %s: stored=%d new=%d changed=%d unchanged=%d",
        corp_code, bsns_year, fs_div,
        result["items_stored"], result["items_new"],
        result["items_changed"], result["items_unchanged"],
    )
    return result


def collect_policies_batch(
    targets: list[tuple[str, int, str]],
    progress_callback=None,
) -> dict:
    """
    여러 (corp_code, bsns_year, fs_div) 튜플을 batch 처리.

    Returns:
        {
          "total": int,
          "ok": int,
          "failed": int,
          "items_total": int,
          "items_new": int,
          "items_changed": int,
          "errors": [{"corp_code", "bsns_year", "fs_div", "error"}, ...],
        }
    """
    agg = {
        "total": len(targets),
        "ok": 0,
        "failed": 0,
        "items_total": 0,
        "items_new": 0,
        "items_changed": 0,
        "errors": [],
    }

    for idx, (corp_code, bsns_year, fs_div) in enumerate(targets, 1):
        if progress_callback:
            progress_callback(idx, agg["total"], corp_code, bsns_year, fs_div)

        r = collect_policies_for_company(corp_code, bsns_year, fs_div)
        if r.get("error"):
            agg["failed"] += 1
            agg["errors"].append({
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "fs_div": fs_div,
                "error": r["error"],
            })
        else:
            agg["ok"] += 1
            agg["items_total"] += r["items_stored"]
            agg["items_new"] += r["items_new"]
            agg["items_changed"] += r["items_changed"]

    return agg
