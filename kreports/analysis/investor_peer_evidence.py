"""Evidence-preserving adapters for investor peer decision surfaces."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
import hashlib
import json
from typing import Any, Literal

from kreports.analysis import peer_benchmarks


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
    subject = result.get("subject") or {}
    digest = _cohort_digest(
        identifiers,
        year=policy.get("resolved_year"),
        fs_div=policy.get("fs_div_used"),
        selection_policy=policy,
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
        "cohort_n": len(peers),
        "year": policy.get("resolved_year"),
        "fs_div": policy.get("fs_div_used"),
        "selection_policy": policy,
        "subject_company": subject.get("corp_name"),
    }
    return result


def compare_to_industry_multi_with_evidence(**kwargs: Any) -> dict[str, Any]:
    """Enrich legacy multi-year peer metrics with denominator and provenance facts."""
    result = deepcopy(peer_benchmarks.compare_to_industry_multi(**kwargs))
    if result.get("error"):
        return result
    subject = result.get("subject") or {}
    comparison_years = [int(year) for year in (result.get("years") or [])]
    selected = peer_benchmarks.select_peer_group(
        company=kwargs["company"],
        peer_limit=max(int(result.get("n_peers") or 0), 30),
        fs_strategy=kwargs.get("fs_strategy") or result.get("fs_strategy") or "CFS",
        prefix_len_start=kwargs.get("prefix_len_start", 3),
        size_bucket_decade=kwargs.get("size_bucket_decade"),
        exclude_other_sectors=kwargs.get("exclude_other_sectors", True),
        year=max(comparison_years) if comparison_years else None,
    )
    selection_policy = selected.get("selection_policy") or {}
    identifiers = [
        str(peer.get("corp_code"))
        for peer in (selected.get("peers") or [])
        if peer.get("corp_code")
    ]
    policy = selection_policy or {
        "matched_prefix_len": result.get("matched_prefix_len"),
        "sector_group": result.get("sector_group"),
        "fs_strategy": result.get("fs_strategy"),
        "size_bucket_applied": result.get("size_bucket_applied"),
    }
    cohort_n = int(result.get("n_peers") or 0)
    fs_div = result.get("fs_div_used") or result.get("fs_div")
    for year, metrics in (result.get("results") or {}).items():
        digest = _cohort_digest(
            identifiers,
            year=int(year),
            fs_div=fs_div,
            selection_policy=policy,
        )
        for values in (metrics or {}).values():
            if not isinstance(values, dict):
                continue
            metric_n = int(values.get("n") or 0)
            values["metric_n"] = metric_n
            values["cohort_n"] = cohort_n
            values["missing_n"] = max(cohort_n - metric_n, 0)
            values["cohort_digest"] = digest

    limitations = list((result.get("data_quality") or {}).get("limitations") or [])
    if cohort_n:
        limitations.append(
            "Peer 개별 사업보고서 접수번호는 집계 결과에 보존되지 않아 cohort provenance는 정책·표본수·digest로 제한됩니다."
        )
    result["data_quality"] = {
        **(result.get("data_quality") or {}),
        "status": "limited" if cohort_n else "missing",
        "source": "peer_benchmarks",
        "limitations": limitations,
    }
    result["cohort_provenance"] = {
        "cohort_n": cohort_n,
        "fs_div": fs_div,
        "selection_policy": policy,
        "subject_company": subject.get("corp_name"),
    }
    return result
