"""Evidence-preserving adapters for investor peer decision surfaces."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
from typing import Any, Literal

from sqlalchemy import bindparam, text

from kreports.analysis import peer_benchmarks
import kreports.db.engine as _engine_module


CheckStatus = Literal["pass", "fail", "unknown"]


def evaluate_investor_check(
    *,
    name: str,
    value: float | None,
    predicate: Callable[[float], bool],
    meaning: str,
) -> dict[str, Any]:
    """Evaluate an investor check without treating an absent input as failure."""
    status: CheckStatus = "unknown" if value is None else (
        "pass" if predicate(value) else "fail"
    )
    return {"name": name, "value": value, "status": status, "meaning": meaning}


def _cohort_digest(
    identifiers: list[str],
    *,
    year: int | None,
    fs_div: str | None,
    selection_policy: dict[str, Any] | None,
) -> str:
    """Return a stable opaque cohort fingerprint; callers never render identifiers."""
    payload = {
        "identifiers": sorted(str(value) for value in identifiers),
        "year": year,
        "fs_div": fs_div,
        "selection_policy": selection_policy or {},
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def select_peer_group_with_evidence(**kwargs: Any) -> dict[str, Any]:
    """Enrich the legacy selector without changing its stable computation path."""
    result = deepcopy(peer_benchmarks.select_peer_group(**kwargs))
    if result.get("error"):
        return result
    policy = result.get("selection_policy") or {}
    peers = result.get("peers") or []
    identifiers = [str(peer.get("corp_code")) for peer in peers if peer.get("corp_code")]
    cohort_n = int(result.get("peer_count") or 0)
    identity_complete = cohort_n > 0 and len(identifiers) == cohort_n
    identity_status = (
        "empty" if cohort_n == 0
        else "complete" if identity_complete
        else "incomplete"
    )
    subject = result.get("subject") or {}
    digest = (
        _cohort_digest(
            identifiers,
            year=policy.get("resolved_year"),
            fs_div=policy.get("fs_div_used"),
            selection_policy=policy,
        )
        if identity_complete else None
    )
    result["peer_selection"] = [
        {
            "company_name": peer.get("corp_name"),
            "ksic": peer.get("induty_code"),
            "scale": peer.get("total_assets"),
            "include_reason": ", ".join(peer.get("include_reasons") or []) or "선정 근거 미확보",
        }
        for peer in peers
    ]
    result["cohort_provenance"] = {
        "cohort_digest": digest,
        "cohort_n": cohort_n,
        "identifier_count": len(identifiers),
        "identity_status": identity_status,
        "digest_status": "available" if identity_complete else "withheld",
        "year": policy.get("resolved_year"),
        "fs_div": policy.get("fs_div_used"),
        "selection_policy": policy,
        "subject_company": subject.get("corp_name"),
    }
    if cohort_n > 0 and not identity_complete:
        result["data_quality"] = {
            "status": "limited",
            "source": "peer_benchmarks",
            "limitations": [
                "cohort_identity_incomplete: 선택된 전체 cohort 식별자를 확보하지 못해 digest를 생성하지 않았습니다."
            ],
        }
    return result


def compare_to_industry_multi_with_evidence(**kwargs: Any) -> dict[str, Any]:
    """Build a constant-query peer matrix from one legacy cohort selection."""
    metrics = list(kwargs.get("metrics") or peer_benchmarks._ALL_METRICS)
    invalid = [metric for metric in metrics if metric not in peer_benchmarks._METRIC_SQL]
    if invalid:
        return {
            "error": (
                f"지원하지 않는 metric: {invalid}. "
                f"지원: {list(peer_benchmarks._METRIC_SQL.keys())}"
            )
        }
    selected = select_peer_group_with_evidence(
        company=kwargs["company"],
        peer_limit=200,
        fs_strategy=kwargs.get("fs_strategy") or "CFS",
        prefix_len_start=kwargs.get("prefix_len_start", 3),
        size_bucket_decade=kwargs.get("size_bucket_decade"),
        exclude_other_sectors=kwargs.get("exclude_other_sectors", True),
    )
    if selected.get("error"):
        return selected

    subject = selected.get("subject") or {}
    policy = selected.get("selection_policy") or {}
    fs_div = policy.get("fs_div_used")
    latest_year = policy.get("resolved_year")
    peer_count = int(selected.get("peer_count") or 0)
    identifiers = [
        str(peer.get("corp_code"))
        for peer in (selected.get("peers") or [])
        if peer.get("corp_code")
    ]
    identity_complete = peer_count > 0 and len(identifiers) == peer_count
    identity_status = (
        "empty" if peer_count == 0
        else "complete" if identity_complete
        else "incomplete"
    )
    years = (
        list(range(int(latest_year) - int(kwargs.get("years_back", 5)) + 1, int(latest_year) + 1))
        if latest_year is not None else []
    )

    batch_rows: list[dict[str, Any]] = []
    if years and (identifiers or subject.get("corp_code")):
        projections = [
            f"({peer_benchmarks._METRIC_SQL[metric]}) AS metric_{index}"
            for index, metric in enumerate(metrics)
        ]
        stmt = text(
            "SELECT f.corp_code, f.year, " + ", ".join(projections)
            + " FROM financials f WHERE f.corp_code IN :corp_codes"
            + " AND f.year IN :years AND f.quarter=4 AND f.fs_div=:fs_div"
        ).bindparams(
            bindparam("corp_codes", expanding=True),
            bindparam("years", expanding=True),
        )
        with _engine_module.engine.connect() as conn:
            batch_rows = [
                dict(row)
                for row in conn.execute(stmt, {
                    "corp_codes": [subject.get("corp_code"), *identifiers],
                    "years": years,
                    "fs_div": fs_div,
                }).mappings()
            ]

    indexed = {
        (str(row["corp_code"]), int(row["year"])): row
        for row in batch_rows
    }
    results: dict[int, dict[str, dict[str, Any]]] = {}
    for year in years:
        year_metrics: dict[str, dict[str, Any]] = {}
        digest = (
            _cohort_digest(
                identifiers, year=year, fs_div=fs_div,
                selection_policy=policy,
            )
            if identity_complete else None
        )
        for index, metric in enumerate(metrics):
            field = f"metric_{index}"
            values = [
                float(indexed[(corp_code, year)][field])
                for corp_code in identifiers
                if indexed.get((corp_code, year), {}).get(field) is not None
            ]
            subject_value = indexed.get((str(subject.get("corp_code")), year), {}).get(field)
            subject_value = float(subject_value) if subject_value is not None else None
            metric_n = len(values)
            below = (
                sum(value < subject_value for value in values)
                if subject_value is not None else 0
            )
            year_metrics[metric] = {
                "p25": round(peer_benchmarks._quantile(values, 0.25), 2) if metric_n >= 5 else None,
                "p50": round(peer_benchmarks._quantile(values, 0.50), 2) if metric_n else None,
                "p75": round(peer_benchmarks._quantile(values, 0.75), 2) if metric_n >= 5 else None,
                "n": metric_n,
                "metric_n": metric_n,
                "cohort_n": peer_count,
                "missing_n": max(peer_count - metric_n, 0),
                "cohort_digest": digest,
                "subject_value": round(subject_value, 2) if subject_value is not None else None,
                "percentile": round(100.0 * below / metric_n, 1) if subject_value is not None and metric_n else None,
                "unit": peer_benchmarks._METRIC_UNIT.get(metric),
            }
        results[year] = year_metrics

    limitations = []
    if peer_count:
        limitations.append(
            "Peer 개별 사업보고서 접수번호는 집계 결과에 보존되지 않아 cohort provenance는 정책·표본수·digest로 제한됩니다."
        )
    if peer_count > 0 and not identity_complete:
        limitations.append(
            "cohort_identity_incomplete: 선택된 전체 cohort 식별자를 확보하지 못해 digest를 생성하지 않았습니다."
        )
    result = {
        "subject": subject,
        "sector_group": peer_benchmarks.classify_sector(subject.get("induty_code")).value,
        "matched_prefix_len": policy.get("matched_prefix_len"),
        "n_peers": peer_count,
        "confidence": selected.get("confidence"),
        "excluded_categories": selected.get("excluded_categories") or [],
        "size_bucket_applied": policy.get("size_bucket_decade"),
        "fs_div": fs_div,
        "fs_strategy": kwargs.get("fs_strategy") or "CFS",
        "requested_fs_div": kwargs.get("fs_div", "CFS"),
        "fs_div_used": fs_div,
        "years": years,
        "metrics": metrics,
        "results": results,
        "note": selected.get("note"),
        "data_quality": {
        "status": "limited" if peer_count else "missing",
        "source": "peer_benchmarks",
        "limitations": limitations,
        },
        "cohort_provenance": {
            "cohort_n": peer_count,
            "identifier_count": len(identifiers),
            "identity_status": identity_status,
            "digest_status": "available" if identity_complete else "withheld",
            "fs_div": fs_div,
            "selection_policy": policy,
            "subject_company": subject.get("corp_name"),
        },
    }
    return result
