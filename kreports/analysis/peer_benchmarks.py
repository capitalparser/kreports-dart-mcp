"""Industry and peer selection, comparisons, and engagement benchmarks."""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Optional

from sqlalchemy import bindparam, text

import kreports.db.engine as _engine_module
from kreports.db.engine import get_session
from kreports.db.models import Company
from kreports.analysis.peer import (
    PeerCohort,
    PeerResolution,
    classify_sector,
    cohort_to_peer_group,
    confidence_band,
    resolve_fs_div_for_company,
    resolve_peers,
)
from kreports.analysis.peer_criteria import PeerCriteriaProfile, coerce_peer_criteria

from kreports.analysis._shared import _clean_dict, _display_text, _has_db_column
from kreports.analysis.audit_procedure_evidence import (
    procedure_database_preflight,
    procedure_read_engine,
)
from kreports.analysis.company_profile import get_industry_name, resolve_corp_code
from kreports.analysis.audit_reporting import (
    AUDIT_MATTER_KEYS,
    KAM_TOPIC_KEYWORDS,
    cache_quality_status,
    cached_years_for_sections,
    classify_audit_matter,
    evidence_report_section_rows,
    evidence_years_for_sections,
    full_body_kam_procedure_rows,
    kam_hint_coverage,
    topic_hits,
)
from kreports.analysis.evidence import dart_filing_url
from kreports.analysis.filing_provenance import (
    canonical_annual_filing_source_receipt,
    valid_annual_filing_receipt,
)


_METRIC_SQL = {
    "영업이익률": "100.0 * f.operating_profit / NULLIF(f.revenue, 0)",
    "순이익률":   "100.0 * f.net_income / NULLIF(f.revenue, 0)",
    "부채비율":   "100.0 * f.total_debt / NULLIF(f.total_equity, 0)",
    "ROE":        "100.0 * f.net_income / NULLIF(f.total_equity, 0)",
    "ROA":        "100.0 * f.net_income / NULLIF(f.total_assets, 0)",
    # v0.2 신규
    "자기자본비율": "100.0 * f.total_equity / NULLIF(f.total_assets, 0)",
    "매출성장률":   "f.revenue_yoy * 100.0",
    "Beneish_M":    "f.beneish_m_score",
}


_METRIC_UNIT = {
    "영업이익률": "%",
    "순이익률": "%",
    "부채비율": "%",
    "ROE": "%",
    "ROA": "%",
    "자기자본비율": "%",
    "매출성장률": "%",
    "Beneish_M": "score",
}


_MIN_PEERS_FOR_STATS = 3


@dataclass(frozen=True)
class ResolvedPeerSubject:
    """Canonical corp identity resolved by a stricter public boundary."""

    corp_code: str

    def __post_init__(self) -> None:
        if len(self.corp_code) != 8 or not self.corp_code.isdigit():
            raise ValueError("resolved peer subject must contain an 8-digit corp_code")


def _quantile(values: list[float], q: float) -> Optional[float]:
    """정렬된 리스트에서 quantile 계산. n=0이면 None."""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    # statistics.quantiles(n=4)는 P25/P50/P75를 리스트로 반환 (n>=2 필요)
    # 수동 구현: linear interpolation
    s = sorted(values)
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def _fetch_metric_values(
    conn,
    corp_codes: list[str],
    metric_expr: str,
    year: int,
    fs_div: str,
) -> list[tuple[str, str, Optional[str], Optional[float]]]:
    """
    주어진 corp_code 리스트에 대해 metric 값 + 회사명 + induty_code 를 일괄 조회.

    반환: (corp_code, corp_name, induty_code, value) 튜플. value가 NULL인 행은 제외.
    """
    if not corp_codes:
        return []
    stmt = text(
        f"SELECT f.corp_code, c.corp_name, c.induty_code, ({metric_expr}) AS v "
        "FROM financials f JOIN companies c ON c.corp_code = f.corp_code "
        "WHERE f.corp_code IN :ccs "
        "  AND f.year = :y AND f.quarter = 4 AND f.fs_div = :fs"
    ).bindparams(bindparam("ccs", expanding=True))
    rows = conn.execute(
        stmt,
        {"ccs": list(corp_codes), "y": year, "fs": fs_div},
    ).all()
    return [
        (r[0], r[1], r[2], r[3])
        for r in rows
        if r[3] is not None
    ]


def get_industry_aggregates(
    induty_code: str,
    metric: str = "영업이익률",
    year: Optional[int] = None,
    fs_div: str = "CFS",
    prefix_len: int = 2,
    include_peers: bool = True,
    peer_limit: int = 50,
    subject_corp_code: str | None = None,
    subject_name: str | None = None,
) -> dict:
    """
    같은 업종(induty_code prefix 매칭) 내 기업들의 metric 분포.

    동일 prefix를 공유하는 기업 중 연간 Q4 재무데이터가 있는 기업을 수집하여
    P25/P50/P75 quantile과 min/max/mean을 계산한다.

    subject_corp_code가 제공되면 peer set 해석을 `peer.resolve_peers`에 위임하여
    adaptive ladder(3자리→2자리 fallback) 및 sector mutual exclusion을 적용한다.

    Args:
        induty_code: 기준 기업의 induty_code (예: Samsung "264")
        metric: 집계 지표. 지원: 영업이익률·순이익률·부채비율·ROE·ROA.
        year: 사업연도 (예: 2024). None이면 해당 induty에서 데이터가 있는 가장 최근 연도.
        fs_div: CFS/OFS
        prefix_len: induty_code 앞에서 몇 자리로 매칭할지 (resolve_peers의
            prefix_len_start로 전달). 기본 2.
        include_peers: True면 peer 기업 리스트 반환. False면 통계만.
        peer_limit: peer 리스트 최대 개수.
        subject_corp_code: 특정 회사의 위치를 전체 peer set 기준으로 계산할 때 사용.
        subject_name: subject_corp_code의 표시명.

    Returns:
        {
          "induty_code", "match_prefix", "prefix_len",
          "metric", "unit",
          "year", "fs_div",
          "n",  # 통계에 포함된 기업 수
          "quantiles": {"p25", "p50", "p75", "min", "max", "mean"} or None,
          "peers": [{"corp_code", "corp_name", "induty_code", "value"}, ...],
          "sector_group", "confidence", "excluded_categories",
          "size_bucket_applied", "matched_prefix_len",
          "note": str,
        }
    """
    if metric not in _METRIC_SQL:
        return {
            "error": f"지원하지 않는 metric: {metric}. "
                     f"지원: {list(_METRIC_SQL.keys())}",
        }

    if induty_code is None or not str(induty_code).strip():
        return {"error": "induty_code가 비어 있습니다."}

    induty_code = str(induty_code).strip()
    if prefix_len < 1 or prefix_len > 5:
        return {"error": "prefix_len은 1~5 사이여야 합니다."}

    metric_expr = _METRIC_SQL[metric]

    # ------------------------------------------------------------------
    # Peer set 해석
    # ------------------------------------------------------------------
    # subject_corp_code가 있으면 peer.resolve_peers에 위임(어댑티브 ladder + sector
    # mutual exclusion). 없으면 induty_code prefix-only 경로(legacy).
    #
    # 두 경로 모두 동일한 meta 키(sector_group/confidence/excluded_categories/
    # size_bucket_applied/matched_prefix_len)를 반환해야 한다.
    peer_resolution: Optional[PeerResolution] = None
    sector_group_val: str = classify_sector(induty_code).value
    excluded_categories: list[str] = []
    size_bucket_applied: Optional[float] = None
    resolution_note: str = ""

    if subject_corp_code:
        peer_resolution = resolve_peers(
            corp_code=subject_corp_code,
            prefix_len_start=prefix_len,
            min_n=_MIN_PEERS_FOR_STATS,
            exclude_other_sectors=True,
            size_bucket_decade=None,
            fs_div=fs_div,
            year=year,
        )
        matched_prefix_len = peer_resolution.matched_prefix_len
        match_prefix = induty_code[:matched_prefix_len]
        sector_group_val = peer_resolution.sector_group.value
        excluded_categories = list(peer_resolution.excluded_categories)
        size_bucket_applied = peer_resolution.size_bucket_applied
        resolution_note = peer_resolution.note
        # subject-기준 연도를 그대로 사용 (industry-wide MAX와 발산할 수 있는 늦은 제출
        # 케이스 방지). year 미지정 시 resolve_peers가 산정한 결과를 신뢰한다.
        if year is None and peer_resolution.resolved_year is not None:
            year = peer_resolution.resolved_year
    else:
        # legacy prefix-only 경로: subject 없음 (induty_code 직접 지정)
        matched_prefix_len = prefix_len
        match_prefix = induty_code[:prefix_len]

    # ------------------------------------------------------------------
    # 연도 결정: 미지정 시 가장 최근 Q4 연도
    # ------------------------------------------------------------------
    if year is None:
        with _engine_module.engine.connect() as conn:
            latest = conn.execute(
                text("""
                    SELECT MAX(f.year)
                    FROM financials f
                    JOIN companies c ON f.corp_code = c.corp_code
                    WHERE substr(c.induty_code, 1, :plen) = :prefix
                      AND f.fs_div = :fs_div
                      AND f.quarter = 4
                """),
                {"plen": matched_prefix_len, "prefix": match_prefix, "fs_div": fs_div},
            ).scalar()
        if latest is None:
            industry_name = (
                get_industry_name(match_prefix) if matched_prefix_len == 2 else match_prefix
            )
            return {
                "induty_code": induty_code,
                "match_prefix": match_prefix,
                "industry_name": industry_name,
                "prefix_len": matched_prefix_len,
                "matched_prefix_len": matched_prefix_len,
                "metric": metric,
                "unit": _METRIC_UNIT.get(metric),
                "year": None,
                "fs_div": fs_div,
                "n": 0,
                "quantiles": None,
                "peers": [],
                "sector_group": sector_group_val,
                "confidence": confidence_band(0),
                "excluded_categories": excluded_categories,
                "size_bucket_applied": size_bucket_applied,
                "note": (
                    f"업종 prefix '{match_prefix}' 내에서 {fs_div} Q4 재무데이터를 가진 "
                    f"기업이 없습니다. 더 많은 기업을 수집하거나 prefix_len을 낮추세요."
                ),
            }
        year = int(latest)

    # ------------------------------------------------------------------
    # Peer metric 값 수집
    # ------------------------------------------------------------------
    if peer_resolution is not None:
        # resolve_peers가 이미 sector mutual exclusion 적용한 corp_code 목록을 줌.
        # metric 값은 _fetch_metric_values로 일괄 조회.
        with _engine_module.engine.connect() as conn:
            fetched = _fetch_metric_values(
                conn,
                peer_resolution.peer_corp_codes,
                metric_expr,
                year,
                fs_div,
            )
        # 기존 응답 shape과 동일하게 정렬 (value desc)
        fetched_sorted = sorted(
            fetched,
            key=lambda r: r[3] if r[3] is not None else float("-inf"),
            reverse=True,
        )
        peers_all = [
            {
                "corp_code": cc,
                "corp_name": cn,
                "induty_code": ic,
                "value": round(float(v), 2) if v is not None else None,
            }
            for cc, cn, ic, v in fetched_sorted
        ]

        # subject 본인 metric 값도 별도 조회 (resolve_peers는 본인을 제외하므로,
        # subject의 metric 값이 필요하면 별도 fetch).
        if subject_corp_code:
            with _engine_module.engine.connect() as conn:
                subj_rows = _fetch_metric_values(
                    conn,
                    [subject_corp_code],
                    metric_expr,
                    year,
                    fs_div,
                )
            if subj_rows:
                _cc, _cn, _ic, v = subj_rows[0]
                subject_value: Optional[float] = (
                    round(float(v), 2) if v is not None else None
                )
            else:
                subject_value = None
        else:
            subject_value = None
    else:
        # ---- legacy prefix-only 경로 ----
        sql = text(f"""
            SELECT
              c.corp_code,
              c.corp_name,
              c.induty_code,
              ({metric_expr}) AS value
            FROM financials f
            JOIN companies c ON f.corp_code = c.corp_code
            WHERE substr(c.induty_code, 1, :plen) = :prefix
              AND f.fs_div = :fs_div
              AND f.year = :year
              AND f.quarter = 4
              AND ({metric_expr}) IS NOT NULL
            ORDER BY value DESC
        """)
        with _engine_module.engine.connect() as conn:
            rows = conn.execute(
                sql,
                {
                    "plen": matched_prefix_len,
                    "prefix": match_prefix,
                    "fs_div": fs_div,
                    "year": year,
                },
            ).fetchall()

        peers_all = [
            {
                "corp_code": r[0],
                "corp_name": r[1],
                "induty_code": r[2],
                "value": round(float(r[3]), 2) if r[3] is not None else None,
            }
            for r in rows
            if r[3] is not None
        ]
        subject_value = None

    values = [p["value"] for p in peers_all if p["value"] is not None]
    n = len(values)

    quantiles: Optional[dict] = None
    if n >= 1:
        quantiles = {
            "p25": round(_quantile(values, 0.25), 2) if n >= _MIN_PEERS_FOR_STATS else None,
            "p50": round(_quantile(values, 0.50), 2),
            "p75": round(_quantile(values, 0.75), 2) if n >= _MIN_PEERS_FOR_STATS else None,
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "mean": round(statistics.fmean(values), 2),
        }

    # 업종명
    industry_name = (
        get_industry_name(match_prefix) if matched_prefix_len == 2 else match_prefix
    )

    # 업종 내 전체 기업 수 vs 수집된 기업 수 (커버리지)
    with _engine_module.engine.connect() as conn:
        total_in_industry = conn.execute(
            text("""
                SELECT COUNT(*) FROM companies
                WHERE substr(induty_code, 1, :plen) = :prefix
                  AND stock_code IS NOT NULL
            """),
            {"plen": matched_prefix_len, "prefix": match_prefix},
        ).scalar() or 0

    coverage_pct = round(100.0 * n / total_in_industry, 1) if total_in_industry > 0 else 0

    if n == 0:
        note = (
            f"업종 '{industry_name}' (prefix {match_prefix}) 내 {year or '?'}년 {fs_div} Q4 데이터에서 "
            f"{metric} 계산 가능한 기업이 없습니다. "
            f"`kreports collect-seed`로 데이터를 수집하세요."
        )
    elif n < _MIN_PEERS_FOR_STATS:
        note = (
            f"peer {n}개 / 전체 {total_in_industry}개 ({coverage_pct}% 커버리지). "
            f"P25/P75 계산 생략. `kreports collect-seed`로 추가 수집 권장."
        )
    else:
        note = f"peer {n}개 / 전체 {total_in_industry}개 ({coverage_pct}%). fs_div={fs_div}, year={year}."

    # resolve_peers에서 받은 note(sector + fallback 마커)를 prefix로 합성.
    # 기존 희소성 경고/커버리지 메시지는 보존한다.
    if resolution_note:
        note = f"{resolution_note} · {note}"

    peers_out = peers_all[:peer_limit] if include_peers else []

    subject = None
    if subject_corp_code:
        # subject는 resolve_peers에서 제외되므로 peers_all에는 없음.
        # subject_value를 별도 조회 결과로 사용한다 (peer_resolution 경로).
        # legacy 경로(peer_resolution=None)에서는 subject_corp_code가 None이라
        # 이 분기에 진입하지 않는다.
        subject_peer_in_returned = any(
            p["corp_code"] == subject_corp_code for p in peers_out
        )
        subject = {
            "corp_code": subject_corp_code,
            "corp_name": subject_name,
            "value": subject_value,
            "subject_has_metric": subject_value is not None,
            "found_in_returned_peers": subject_peer_in_returned,
        }
        if subject_value is not None and n >= 1:
            # subject를 포함한 전체 분포 기준 percentile
            all_values = sorted(values + [subject_value])
            rank = sum(1 for v in all_values if v < subject_value)
            denom = max(len(all_values) - 1, 1)
            subject["percentile"] = round(100.0 * rank / denom, 1)
        else:
            subject["percentile"] = None

    result = {
        "induty_code": induty_code,
        "match_prefix": match_prefix,
        "industry_name": industry_name,
        "prefix_len": matched_prefix_len,
        "matched_prefix_len": matched_prefix_len,
        "requested_prefix_len": prefix_len,
        "metric": metric,
        "unit": _METRIC_UNIT.get(metric),
        "year": year,
        "fs_div": fs_div,
        "n": n,
        "total_in_industry": total_in_industry,
        "coverage_pct": coverage_pct,
        "quantiles": quantiles,
        "peers": peers_out,
        "peer_limit": peer_limit,
        "truncated": include_peers and len(peers_all) > peer_limit,
        "sector_group": sector_group_val,
        "confidence": confidence_band(n),
        "excluded_categories": excluded_categories,
        "size_bucket_applied": size_bucket_applied,
        "note": note,
    }
    if subject is not None:
        result["subject"] = subject
    return result


def compare_to_industry(
    company: str | None = None,
    induty_code: str | None = None,
    metric: str = "영업이익률",
    year: Optional[int] = None,
    fs_div: str = "CFS",
    prefix_len: int = 2,
    include_peers: bool = True,
    peer_limit: int = 50,
) -> dict:
    """
    회사 또는 업종코드를 기준으로 동종업종 내 상대 위치를 반환한다.

    Args:
        company: corp_code / stock_code / 회사명
        induty_code: 회사 대신 직접 KSIC 코드 지정
        metric: 비교 지표
        year: 사업연도 (Q4 기준)
        fs_div: CFS / OFS
        prefix_len: induty_code prefix 길이
        include_peers: peer 리스트 포함 여부
        peer_limit: peer 리스트 최대 개수

    Returns:
        get_industry_aggregates 반환값 + subject 정보
    """
    if company:
        corp_code = resolve_corp_code(company)
        if corp_code is None:
            return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

        with get_session() as session:
            row = session.query(Company).filter_by(corp_code=corp_code).first()
            if row is None:
                return {"error": f"corp_code '{corp_code}'를 찾을 수 없습니다."}
            if not row.induty_code:
                return {
                    "error": (
                        f"'{row.corp_name}'에 induty_code가 없습니다. "
                        "kreports enrich-market으로 업종코드를 보완하세요."
                    )
                }
            resolved_induty = row.induty_code
            subject_name = row.corp_name
            subject_corp_code = corp_code
    elif induty_code:
        resolved_induty = str(induty_code).strip()
        subject_name = None
        subject_corp_code = None
    else:
        return {"error": "company 또는 induty_code 중 하나를 제공해야 합니다."}

    result = get_industry_aggregates(
        induty_code=resolved_induty,
        metric=metric,
        year=year,
        fs_div=fs_div,
        prefix_len=prefix_len,
        include_peers=include_peers,
        peer_limit=peer_limit,
        subject_corp_code=subject_corp_code,
        subject_name=subject_name,
    )

    if "error" in result:
        return result

    return result


_ALL_METRICS = [
    "영업이익률", "순이익률", "부채비율", "ROE", "ROA",
    "자기자본비율", "매출성장률", "Beneish_M",
]


def _with_typed_cohort_metadata(
    result: dict,
    cohort: PeerCohort | None,
) -> dict:
    if cohort is None:
        return result
    return {
        **result,
        "cohort_metadata": {
            "profile": cohort.profile,
            "requested_year": cohort.requested_year,
            "fs_div": cohort.fs_div,
            "total_candidates": cohort.total_candidates,
            "eligible_count": cohort.eligible_count,
            "selected_count": len(cohort.members),
            "exclusion_counts": dict(cohort.exclusion_counts),
            "denominator_metadata": dict(cohort.denominator_metadata),
            "limitations": list(cohort.limitations),
        },
    }


def compare_to_industry_multi(
    company: str,
    metrics: Optional[list[str]] = None,
    years_back: int = 5,
    fs_div: str = "CFS",
    fs_strategy: str = "CFS",
    prefix_len_start: int = 3,
    exclude_other_sectors: bool = True,
    size_bucket_decade: Optional[float] = None,
    _cohort: PeerCohort | None = None,
) -> dict:
    """다지표·다년도 동종업종 분포 + subject percentile.

    Peer 풀은 resolve_peers로 한 번만 산정한다 (subject 최신 Q4 연도 기준).
    그 peer 풀 위에서 metric × year matrix를 만든다.

    Args:
        company: corp_code / stock_code / 회사명
        metrics: 비교 지표 리스트. None이면 _ALL_METRICS 8개 사용.
        years_back: 최근 N개 연도 (기본 5).
        fs_div: CFS / OFS
        prefix_len_start: KSIC prefix 시작 길이 (resolve_peers로 전달).
        exclude_other_sectors: 금융/지주/부동산/일반 mutual exclusion 적용.
        size_bucket_decade: 자산총계 log10 거리 한도 (opt-in).

    Returns:
        {
          "subject": {"corp_code", "corp_name", "induty_code"},
          "sector_group", "matched_prefix_len", "n_peers", "confidence",
          "excluded_categories", "size_bucket_applied", "fs_div",
          "years": [int, ...],
          "metrics": [str, ...],
          "results": {year: {metric: {"p25", "p50", "p75", "n",
                                      "subject_value", "percentile", "unit"}}},
          "note": str,
        }
    """
    corp_code = (
        _cohort.subject_corp_code
        if _cohort is not None
        else resolve_corp_code(company)
    )
    if corp_code is None:
        return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

    if metrics is None:
        metrics = list(_ALL_METRICS)
    invalid = [m for m in metrics if m not in _METRIC_SQL]
    if invalid:
        return {
            "error": f"지원하지 않는 metric: {invalid}. 지원: {list(_METRIC_SQL.keys())}"
        }

    # subject 메타
    with _engine_module.engine.connect() as conn:
        subject_row = conn.execute(
            text("SELECT corp_name, induty_code FROM companies WHERE corp_code = :cc"),
            {"cc": corp_code},
        ).first()
    if subject_row is None:
        return {"error": f"corp_code '{corp_code}' 미등록"}
    subject_name, subject_induty = subject_row[0], subject_row[1]
    requested_fs_div = fs_div
    if _cohort is not None and _cohort.fs_div is not None:
        fs_div = _cohort.fs_div
    elif fs_strategy.lower() == "auto":
        fs_div = resolve_fs_div_for_company(corp_code, None, "auto")

    # Peer 풀은 한 번만 결정 (subject의 최신 Q4 연도 기준)
    if _cohort is not None:
        pr = PeerResolution(
            peer_corp_codes=[member.corp_code for member in _cohort.members],
            matched_prefix_len=prefix_len_start,
            sector_group=classify_sector(subject_induty),
            n_peers=len(_cohort.members),
            excluded_categories=[],
            size_bucket_applied=size_bucket_decade,
            resolved_year=(
                _cohort.requested_year
                if _cohort.fs_div is not None
                else None
            ),
            note="typed explainable peer cohort",
        )
    else:
        pr = resolve_peers(
            corp_code,
            prefix_len_start=prefix_len_start,
            min_n=5,
            exclude_other_sectors=exclude_other_sectors,
            size_bucket_decade=size_bucket_decade,
            fs_div=fs_div,
        )

    subject_meta = {
        "corp_code": corp_code,
        "corp_name": subject_name,
        "induty_code": subject_induty,
    }

    if pr.n_peers == 0:
        return _with_typed_cohort_metadata({
            "subject": subject_meta,
            "sector_group": pr.sector_group.value,
            "matched_prefix_len": pr.matched_prefix_len,
            "n_peers": 0,
            "confidence": pr.confidence,
            "excluded_categories": pr.excluded_categories,
            "size_bucket_applied": pr.size_bucket_applied,
            "fs_div": fs_div,
            "fs_strategy": fs_strategy,
            "requested_fs_div": requested_fs_div,
            "fs_div_used": fs_div,
            "years": [],
            "metrics": metrics,
            "results": {},
            "note": pr.note,
        }, _cohort)

    # 최신 연도: resolve_peers가 산정한 결과 우선, 없으면 (peers + subject)에서 MAX
    latest_year = pr.resolved_year
    if latest_year is None:
        with _engine_module.engine.connect() as conn:
            stmt = text(
                "SELECT MAX(year) FROM financials "
                "WHERE quarter = 4 AND fs_div = :fs AND corp_code IN :ccs"
            ).bindparams(bindparam("ccs", expanding=True))
            latest_row = conn.execute(
                stmt,
                {
                    "fs": fs_div,
                    "ccs": list(pr.peer_corp_codes) + [corp_code],
                },
            ).first()
        latest_year = latest_row[0] if latest_row and latest_row[0] else None

    if latest_year is None:
        return _with_typed_cohort_metadata({
            "subject": subject_meta,
            "sector_group": pr.sector_group.value,
            "matched_prefix_len": pr.matched_prefix_len,
            "n_peers": pr.n_peers,
            "confidence": pr.confidence,
            "excluded_categories": pr.excluded_categories,
            "size_bucket_applied": pr.size_bucket_applied,
            "fs_div": fs_div,
            "fs_strategy": fs_strategy,
            "requested_fs_div": requested_fs_div,
            "fs_div_used": fs_div,
            "years": [],
            "metrics": metrics,
            "results": {},
            "note": (pr.note + " · " if pr.note else "") + "최신 Q4 재무 데이터 없음",
        }, _cohort)

    years = list(range(int(latest_year) - years_back + 1, int(latest_year) + 1))

    results: dict[int, dict[str, dict]] = {}
    with _engine_module.engine.connect() as conn:
        for y in years:
            row_y: dict[str, dict] = {}
            for metric in metrics:
                expr = _METRIC_SQL[metric]
                peer_rows = _fetch_metric_values(
                    conn, pr.peer_corp_codes, expr, y, fs_div
                )
                vals = sorted(float(r[3]) for r in peer_rows if r[3] is not None)
                subj_rows = _fetch_metric_values(
                    conn, [corp_code], expr, y, fs_div
                )
                subj_val = (
                    float(subj_rows[0][3]) if subj_rows and subj_rows[0][3] is not None
                    else None
                )

                n = len(vals)
                p50 = round(_quantile(vals, 0.50), 2) if n >= 1 else None
                p25 = round(_quantile(vals, 0.25), 2) if n >= 5 else None
                p75 = round(_quantile(vals, 0.75), 2) if n >= 5 else None

                percentile = None
                if subj_val is not None and n >= 1:
                    below = sum(1 for v in vals if v < subj_val)
                    percentile = round(100.0 * below / n, 1)

                row_y[metric] = {
                    "p25": p25,
                    "p50": p50,
                    "p75": p75,
                    "n": n,
                    "subject_value": round(subj_val, 2) if subj_val is not None else None,
                    "percentile": percentile,
                    "unit": _METRIC_UNIT.get(metric),
                }
            results[y] = row_y

    result = {
        "subject": subject_meta,
        "sector_group": pr.sector_group.value,
        "matched_prefix_len": pr.matched_prefix_len,
        "n_peers": pr.n_peers,
        "confidence": pr.confidence,
        "excluded_categories": pr.excluded_categories,
        "size_bucket_applied": pr.size_bucket_applied,
        "fs_div": fs_div,
        "fs_strategy": fs_strategy,
        "requested_fs_div": requested_fs_div,
        "fs_div_used": fs_div,
        "years": years,
        "metrics": metrics,
        "results": results,
        "note": pr.note,
    }
    return _with_typed_cohort_metadata(result, _cohort)


def _profile_candidate_codes(
    conn,
    *,
    profile: PeerCriteriaProfile,
    resolution: PeerResolution,
    subject_corp_code: str,
    subject_sector: str,
    fs_div: str,
) -> list[str]:
    """Return the deterministic candidate universe for a peer profile.

    This is intentionally limited to company metadata and already-collected Q4
    financial rows.  It never materializes a cache or asks a collector for
    missing data.
    """
    if resolution.resolved_year is None:
        return []
    if profile.industry_basis == "custom_codes":
        return [code for code in profile.included_corp_codes if code != subject_corp_code]
    if profile.industry_basis == "sector_group":
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT c.corp_code
                FROM companies c
                JOIN financials f ON f.corp_code=c.corp_code
                WHERE c.corp_code != :subject_corp_code
                  AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs_div
                ORDER BY c.corp_code
                """
            ),
            {
                "subject_corp_code": subject_corp_code,
                "year": resolution.resolved_year,
                "fs_div": fs_div,
            },
        ).all()
        return [
            row[0]
            for row in rows
            if classify_sector(
                conn.execute(
                    text("SELECT induty_code FROM companies WHERE corp_code=:corp_code"),
                    {"corp_code": row[0]},
                ).scalar(),
            ).value == subject_sector
        ]
    candidates = list(resolution.peer_corp_codes)
    # An explicit inclusion is a transparent user override, not an implicit
    # similarity claim.  It remains subject to exclusion and feature gates.
    for code in profile.included_corp_codes:
        if code != subject_corp_code and code not in candidates:
            candidates.append(code)
    return sorted(candidates)


def _feature_coverage(
    conn,
    *,
    corp_code: str,
    year: int | None,
    fs_div: str,
    profile: PeerCriteriaProfile,
) -> tuple[float, list[str]]:
    """Assess only evidence that is already present in the local database."""
    required = list(profile.required_features)
    if profile.required_business_tags:
        required.append("business_tags")
    if not required:
        return 1.0, []
    if year is None:
        return 0.0, required
    checks = {
        "financials": (
            "SELECT 1 FROM financials WHERE corp_code=:corp_code AND year=:year "
            "AND quarter=4 AND fs_div=:fs_div LIMIT 1"
        ),
        "business_report": (
            "SELECT 1 FROM source_documents WHERE corp_code=:corp_code AND bsns_year=:year "
            "AND source_type='business_report' LIMIT 1"
        ),
        "audit_report": (
            "SELECT 1 FROM source_documents WHERE corp_code=:corp_code AND bsns_year=:year "
            "AND source_type='audit_report' LIMIT 1"
        ),
        "audit_fees": "SELECT 1 FROM audit_fees WHERE corp_code=:corp_code AND bsns_year=:year LIMIT 1",
        "notes": "SELECT 1 FROM accounting_note_chapters WHERE corp_code=:corp_code AND bsns_year=:year LIMIT 1",
        "kam": (
            "SELECT 1 FROM report_sections WHERE corp_code=:corp_code AND bsns_year=:year "
            "AND section_key='kam' LIMIT 1"
        ),
    }
    unavailable: list[str] = []
    for feature in required:
        if feature == "business_tags":
            # Semantic-tag extraction is a later adapter.  Do not claim a
            # narrative match before an evidence index exists.
            unavailable.append(feature)
            continue
        try:
            present = conn.execute(
                text(checks[feature]),
                {"corp_code": corp_code, "year": year, "fs_div": fs_div},
            ).first()
        except Exception:  # schema may be an older read-only runtime artifact
            present = None
        if not present:
            unavailable.append(feature)
    return (len(required) - len(unavailable)) / len(required), unavailable


def _apply_peer_profile(
    conn,
    *,
    profile: PeerCriteriaProfile,
    resolution: PeerResolution,
    subject_corp_code: str,
    subject_sector: str,
    fs_div: str,
) -> tuple[
    list[str],
    dict[str, list[str]],
    dict[str, float],
    dict[str, float],
    dict[str, list[str]],
]:
    """Filter and score peer candidates without changing source tables."""
    candidates = _profile_candidate_codes(
        conn,
        profile=profile,
        resolution=resolution,
        subject_corp_code=subject_corp_code,
        subject_sector=subject_sector,
        fs_div=fs_div,
    )
    candidate_industry_rows = conn.execute(
        text(
            "SELECT corp_code, induty_code FROM companies "
            "WHERE corp_code IN :corp_codes"
        ).bindparams(bindparam("corp_codes", expanding=True)),
        {"corp_codes": candidates},
    ).mappings().all() if candidates else []
    candidate_industry_codes = {
        str(row["corp_code"]): row["induty_code"]
        for row in candidate_industry_rows
    }
    selected: list[tuple[str, float]] = []
    excluded: dict[str, list[str]] = {}
    coverage: dict[str, float] = {}
    scores: dict[str, float] = {}
    inclusions: dict[str, list[str]] = {}
    automatic_candidates = set(resolution.peer_corp_codes)
    for code in candidates:
        reasons: list[str] = []
        if code not in candidate_industry_codes:
            excluded[code] = ["company_not_found"]
            continue
        induty_code = candidate_industry_codes[code]
        sector = classify_sector(induty_code).value
        if code in profile.excluded_corp_codes:
            reasons.append("excluded_by_user")
        if sector in profile.excluded_sector_groups:
            reasons.append(f"excluded_sector_group:{sector}")
        if profile.size_metric is not None and profile.size_log10_tolerance is not None:
            if profile.size_metric == "employees":
                # No employee-count evidence exists in the current runtime
                # schema.  Exclude rather than pretending an asset proxy is an
                # employee comparison.
                reasons.append("size_metric_unavailable:employees")
            else:
                metric = profile.size_metric
                subject_size = conn.execute(
                    text(
                        f"SELECT {metric} FROM financials WHERE corp_code=:corp_code "
                        "AND year=:year AND quarter=4 AND fs_div=:fs_div"
                    ),
                    {
                        "corp_code": subject_corp_code,
                        "year": resolution.resolved_year,
                        "fs_div": fs_div,
                    },
                ).scalar()
                peer_size = conn.execute(
                    text(
                        f"SELECT {metric} FROM financials WHERE corp_code=:corp_code "
                        "AND year=:year AND quarter=4 AND fs_div=:fs_div"
                    ),
                    {
                        "corp_code": code,
                        "year": resolution.resolved_year,
                        "fs_div": fs_div,
                    },
                ).scalar()
                if not (subject_size and subject_size > 0 and peer_size and peer_size > 0):
                    reasons.append(f"size_metric_unavailable:{metric}")
                elif abs(math.log10(float(peer_size)) - math.log10(float(subject_size))) > profile.size_log10_tolerance:
                    reasons.append(f"size_metric_outside_tolerance:{metric}")
        candidate_coverage, missing_features = _feature_coverage(
            conn,
            corp_code=code,
            year=resolution.resolved_year,
            fs_div=fs_div,
            profile=profile,
        )
        coverage[code] = candidate_coverage
        if candidate_coverage < profile.minimum_coverage:
            reasons.append("minimum_coverage_not_met")
        if profile.mode == "strict" and missing_features:
            reasons.extend(f"missing_feature:{feature}" for feature in missing_features)
        if reasons:
            excluded[code] = reasons
            continue
        if profile.industry_basis == "custom_codes":
            inclusions[code] = ["explicit_custom_code"]
        elif code in profile.included_corp_codes:
            inclusions[code] = ["explicit_included_corp_code"]
            if code in automatic_candidates:
                inclusions[code].append("same_ksic_prefix")
        elif profile.industry_basis == "sector_group":
            inclusions[code] = ["same_sector_group"]
        else:
            inclusions[code] = ["same_ksic_prefix"]
        if (
            sector == subject_sector
            and profile.industry_basis != "custom_codes"
        ):
            inclusions[code].append(f"sector_group:{subject_sector}")
        # The baseline universe proves industry/sector membership.  Ranking is
        # intentionally deterministic and only uses declared dimensions.
        score_components = {
            "industry": 1.0,
            "sector": 1.0 if sector == subject_sector else 0.0,
            "coverage": candidate_coverage,
            "business": 0.0 if profile.required_business_tags else 1.0,
            "size": 1.0,
        }
        if profile.weights:
            score = sum(profile.weights[key] * score_components[key] for key in profile.weights)
        else:
            score = candidate_coverage
        scores[code] = round(score, 6)
        selected.append((code, score))
    if profile.mode == "ranked":
        selected.sort(key=lambda item: (-item[1], item[0]))
    else:
        selected.sort(key=lambda item: item[0])
    return [code for code, _score in selected], excluded, coverage, scores, inclusions


def select_peer_group(
    company: str,
    criteria: Optional[list[str] | PeerCriteriaProfile | dict] = None,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    prefix_len_start: int = 3,
    size_bucket_decade: Optional[float] = None,
    exclude_other_sectors: bool = True,
    year: int | None = None,
    _read_engine=None,
    _cohort: PeerCohort | None = None,
    _resolved_subject: ResolvedPeerSubject | None = None,
) -> dict:
    if _cohort is not None:
        return cohort_to_peer_group(_cohort)
    profile, criteria_requested, legacy_criteria = coerce_peer_criteria(
        criteria,
        prefix_len_start=prefix_len_start,
        size_bucket_decade=size_bucket_decade,
        exclude_other_sectors=exclude_other_sectors,
    )
    active_engine = _read_engine or _engine_module.engine
    if _resolved_subject is not None:
        if company != _resolved_subject.corp_code:
            raise ValueError("resolved peer subject does not match company")
        corp_code = _resolved_subject.corp_code
    elif _read_engine is None:
        corp_code = resolve_corp_code(company)
    else:
        with active_engine.connect() as conn:
            corp_code = conn.execute(
                text(
                    """
                    SELECT corp_code
                    FROM companies
                    WHERE corp_code=:company
                       OR stock_code=:company
                       OR corp_name=:company
                       OR corp_name LIKE :company_like
                    ORDER BY
                        CASE
                            WHEN corp_code=:company THEN 0
                            WHEN stock_code=:company THEN 1
                            WHEN corp_name=:company THEN 2
                            ELSE 3
                        END,
                        corp_name
                    LIMIT 1
                    """
                ),
                {
                    "company": company,
                    "company_like": f"%{company}%",
                },
            ).scalar()
    if corp_code is None:
        return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

    with active_engine.connect() as conn:
        subject_row = conn.execute(
            text("SELECT corp_name, stock_code, market, induty_code FROM companies WHERE corp_code=:cc"),
            {"cc": corp_code},
        ).first()
    if subject_row is None:
        return {"error": f"corp_code '{corp_code}' 미등록"}

    if _read_engine is None:
        fs_div_used = resolve_fs_div_for_company(
            corp_code,
            year,
            fs_strategy,
        )
    else:
        fs_div_used = resolve_fs_div_for_company(
            corp_code,
            year,
            fs_strategy,
            read_engine=_read_engine,
        )
    effective_prefix_len = profile.prefix_len
    effective_fallback_prefix_len = (
        None if profile.mode == "strict" else profile.fallback_prefix_len
    )
    effective_size_bucket = profile.size_log10_tolerance if profile.size_metric == "total_assets" else None
    # The old boolean remains authoritative only for the legacy argument
    # shape.  A typed profile expresses sector exclusions explicitly; applying
    # the old implicit mutual-exclusion rule here would make a requested
    # profile impossible to reproduce from its returned policy.
    effective_exclude_other_sectors = (
        exclude_other_sectors if legacy_criteria else False
    )
    peer_kwargs = {
        "corp_code": corp_code,
        "prefix_len_start": effective_prefix_len,
        "fallback_prefix_len": effective_fallback_prefix_len,
        "min_n": 5,
        "exclude_other_sectors": effective_exclude_other_sectors,
        "size_bucket_decade": effective_size_bucket,
        "fs_div": fs_div_used,
        "year": year,
    }
    if _read_engine is not None:
        peer_kwargs["read_engine"] = _read_engine
    pr = resolve_peers(
        **peer_kwargs,
    )

    with active_engine.connect() as conn:
        (
            peer_codes,
            profile_exclusions,
            feature_coverage,
            peer_scores,
            peer_inclusions,
        ) = _apply_peer_profile(
            conn,
            profile=profile,
            resolution=pr,
            subject_corp_code=corp_code,
            subject_sector=pr.sector_group.value,
            fs_div=fs_div_used,
        )

    peers: list[dict] = []
    if peer_codes:
        stmt = text(
            """
            SELECT c.corp_code, c.stock_code, c.corp_name, c.market, c.induty_code,
                   f.total_assets, f.revenue,
                   af.audit_fee_m, af.audit_hours, af.nas_ratio
            FROM companies c
            LEFT JOIN financials f
              ON f.corp_code=c.corp_code
             AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
            LEFT JOIN audit_fees af
              ON af.corp_code=c.corp_code AND af.bsns_year=:year
            WHERE c.corp_code IN :ccs
            ORDER BY (f.total_assets IS NULL), f.total_assets DESC
            """
        ).bindparams(bindparam("ccs", expanding=True))
        with active_engine.connect() as conn:
            rows = conn.execute(
                stmt,
                {
                    "ccs": peer_codes,
                    "year": pr.resolved_year,
                    "fs": fs_div_used,
                },
            ).mappings().all()
        if profile.mode == "ranked":
            rows_by_corp_code = {row["corp_code"]: row for row in rows}
            rows = [
                rows_by_corp_code[code]
                for code in peer_codes[:peer_limit]
                if code in rows_by_corp_code
            ]
        else:
            rows = rows[:peer_limit]
        for row in rows:
            reasons = list(peer_inclusions.get(row["corp_code"], []))
            peer_induty_code = row["induty_code"]
            same_ksic_prefix = bool(
                subject_row[3]
                and peer_induty_code
                and subject_row[3][:pr.matched_prefix_len]
                == peer_induty_code[:pr.matched_prefix_len]
            )
            explicit_ksic_override = (
                profile.industry_basis == "ksic"
                and row["corp_code"] in profile.included_corp_codes
                and not same_ksic_prefix
            )
            if profile.industry_basis == "ksic":
                industry_matched = same_ksic_prefix
                industry_basis = (
                    "explicit_override"
                    if explicit_ksic_override
                    else "same_ksic_prefix"
                )
            elif profile.industry_basis == "sector_group":
                industry_matched = (
                    classify_sector(peer_induty_code).value
                    == pr.sector_group.value
                )
                industry_basis = "sector_group"
            else:
                industry_matched = row["corp_code"] in profile.included_corp_codes
                industry_basis = "custom_codes"
            if effective_size_bucket is not None:
                reasons.append("asset_size_bucket")
            if row["audit_fee_m"] is not None:
                reasons.append("audit_fee_available")
            reason_components = {
                "industry_match": {
                    "matched": industry_matched,
                    "basis": industry_basis,
                    "requested_basis": profile.industry_basis,
                    "override": explicit_ksic_override,
                    "matched_prefix_len": pr.matched_prefix_len,
                    "subject_induty_code": subject_row[3],
                    "peer_induty_code": peer_induty_code,
                },
                "sector_match": {
                    "matched": classify_sector(row["induty_code"]).value == pr.sector_group.value,
                    "basis": (
                        f"sector_group:{pr.sector_group.value}"
                        if classify_sector(row["induty_code"]).value == pr.sector_group.value
                        else "not_required"
                    ),
                },
                "size_bucket_match": {
                    "matched": bool(effective_size_bucket is not None),
                    "basis": "asset_size_bucket" if effective_size_bucket is not None else "not_requested",
                },
                "audit_evidence_available": {
                    "matched": row["audit_fee_m"] is not None,
                    "audit_fee_m": row["audit_fee_m"],
                    "audit_hours": row["audit_hours"],
                    "nas_ratio": row["nas_ratio"],
                },
                "business_text_overlap": {
                    "matched": None,
                    "basis": "not_indexed_for_peer_scoring",
                },
                "kam_topic_overlap": {
                    "matched": None,
                    "basis": "not_indexed_for_peer_scoring",
                },
                "audit_matter_overlap": {
                    "matched": None,
                    "basis": "not_indexed_for_peer_scoring",
                },
            }
            peers.append({
                **dict(row),
                "include_reasons": reasons,
                "reason_components": reason_components,
                "feature_coverage": feature_coverage.get(row["corp_code"], 0.0),
                "selection_score": peer_scores.get(row["corp_code"]),
            })

    selection_confidence = confidence_band(len(peer_codes))
    return {
        "subject": {
            "corp_code": corp_code,
            "stock_code": subject_row[1],
            "corp_name": subject_row[0],
            "market": subject_row[2],
            "induty_code": subject_row[3],
        },
        "selection_policy": {
            "criteria": criteria_requested or ["industry", "sector", "financial_data"],
            "criteria_requested": criteria_requested or ["industry", "sector", "financial_data"],
            "criteria_applied": profile.requested_policy(),
            "selection_mode": profile.mode,
            "legacy_criteria": legacy_criteria,
            "prefix_len_start": effective_prefix_len,
            "matched_prefix_len": pr.matched_prefix_len,
            "fallback_used": (
                effective_fallback_prefix_len is not None
                and pr.matched_prefix_len < effective_prefix_len
            ),
            "exclude_other_sectors": effective_exclude_other_sectors,
            "size_bucket_decade": effective_size_bucket,
            "fs_strategy": fs_strategy,
            "fs_div_used": fs_div_used,
            "requested_year": year,
            "resolved_year": pr.resolved_year,
            "reason_component_note": (
                "industry/sector/audit availability are populated now; business text, KAM topic, "
                "and audit matter overlap are exposed as nullable components until those indexes are fully backfilled."
            ),
            "inclusion_reasons": sorted({
                reason
                for reasons in peer_inclusions.values()
                for reason in reasons
            }),
            "exclusion_reasons": profile_exclusions,
            "coverage": {
                "minimum_required": profile.minimum_coverage,
                "by_peer": feature_coverage,
            },
            "confidence": selection_confidence,
        },
        "peer_count": len(peer_codes),
        "returned_peer_count": len(peers),
        "confidence": selection_confidence,
        "peers": peers,
        "excluded_categories": pr.excluded_categories,
        "note": pr.note,
    }


def _percentile(value: float | None, values: list[float]) -> float | None:
    if value is None or not values:
        return None
    below = sum(1 for v in values if v < value)
    return round(100.0 * below / len(values), 1)


def _metric_quantiles(values: list[float]) -> dict:
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    return {
        "n": n,
        "p25": round(_quantile(vals, 0.25), 2) if n >= 5 else None,
        "p50": round(_quantile(vals, 0.50), 2) if n else None,
        "p75": round(_quantile(vals, 0.75), 2) if n >= 5 else None,
    }


_SUBJECT_SCALE_FIELDS = (
    "total_assets",
    "revenue",
    "audit_fee_m",
    "audit_hours",
)
_SUBJECT_SCALE_FIELD_LABELS = {
    "total_assets": "총자산",
    "revenue": "매출액",
    "audit_fee_m": "감사보수",
    "audit_hours": "감사시간",
}

# The public comparison is labelled in KRW millions. A value that implies more
# than KRW 10m for one audit hour is not a safe comparable observation; it can
# indicate an upstream unit mismatch. Exclude it rather than guessing whether
# the raw source was KRW, KRW thousands, or KRW millions.
_MAX_AUDIT_FEE_M_PER_HOUR = 10.0
_MAX_NAS_RATIO = 100.0


def _positive_finite_number(value: object) -> float | None:
    """Return a public numeric value only when it is finite and positive."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _sanitize_audit_fee_row(row: dict) -> dict:
    """Fail closed on fee-unit anomalies without silently rescaling values."""
    sanitized = dict(row)
    issues: list[str] = []
    for fee_key, hours_key, asset_bps_key, per_hour_key in (
        ("audit_fee_m", "audit_hours", "fee_assets_bps", "fee_per_hour_m"),
        ("actual_fee_m", "actual_hours", "actual_fee_assets_bps", "actual_fee_per_hour_m"),
        ("contract_fee_m", "contract_hours", "contract_fee_assets_bps", "contract_fee_per_hour_m"),
    ):
        fee = _positive_finite_number(sanitized.get(fee_key))
        hours = _positive_finite_number(sanitized.get(hours_key))
        if fee is None or hours is None or fee / hours <= _MAX_AUDIT_FEE_M_PER_HOUR:
            continue
        sanitized[fee_key] = None
        sanitized[asset_bps_key] = None
        sanitized[per_hour_key] = None
        issues.append(f"{fee_key}:fee_per_hour_exceeds_max")

    audit_fee = _positive_finite_number(sanitized.get("audit_fee_m"))
    non_audit_fee = _positive_finite_number(sanitized.get("non_audit_fee_m"))
    if non_audit_fee is not None:
        if audit_fee is None:
            sanitized["non_audit_fee_m"] = None
            sanitized["nas_ratio"] = None
            issues.append("non_audit_fee_m:missing_trusted_audit_fee")
        elif non_audit_fee / audit_fee > _MAX_NAS_RATIO:
            sanitized["non_audit_fee_m"] = None
            sanitized["nas_ratio"] = None
            issues.append("non_audit_fee_m:nas_ratio_exceeds_max")
        else:
            sanitized["nas_ratio"] = round(non_audit_fee / audit_fee, 4)
    elif sanitized.get("nas_ratio") is not None:
        sanitized["nas_ratio"] = None

    if issues:
        sanitized["unit_integrity_status"] = "excluded_suspect_unit"
        sanitized["unit_integrity_issues"] = issues
    return sanitized


def _audit_fee_confirmed_facts(rows: list[dict], *, year: int) -> list[dict]:
    """Emit a fact only where this comparison row carries a filing receipt."""
    facts: list[dict] = []
    for row in rows:
        receipt = valid_annual_filing_receipt(
            row.get("audit_source_rcept_no"), year,
        )
        if receipt is None:
            continue
        fee = row.get("audit_fee_m")
        hours = row.get("audit_hours")
        if fee is None and hours is None:
            continue
        parts = []
        if fee is not None:
            parts.append(f"감사보수 {int(fee):,}백만원")
        if hours is not None:
            parts.append(f"감사시간 {int(hours):,}시간")
        company = row.get("corp_name") or row.get("corp_code") or "대상회사"
        basis = row.get("metric_basis") or "legacy"
        facts.append({
            "statement": f"{year}년 {company} {', '.join(parts)} ({basis} 기준).",
            "source": {"rcept_no": receipt},
        })
        if len(facts) == 8:
            break
    return facts


def _per_trillion(value: float | int | None, denominator: float | int | None) -> float | None:
    if value is None or denominator is None or denominator <= 0:
        return None
    return round(float(value) * 1_000_000_000_000.0 / float(denominator), 2)


def _subject_scale_history(
    corp_code: str,
    *,
    year: int,
    fs_div: str,
) -> tuple[list[dict], dict]:
    """Return three comparable annual scale/effort rows without mixing FS bases."""
    requested_years = [year, year - 1, year - 2]
    financial_stmt = text(
        """
        SELECT year, fs_div, total_assets, revenue, source
        FROM financials
        WHERE corp_code=:corp_code
          AND year IN :years
          AND quarter=4
          AND fs_div=:fs_div
        """
    ).bindparams(bindparam("years", expanding=True))
    source_rcept_expr = (
        "source_rcept_no"
        if _has_db_column("audit_fees", "source_rcept_no")
        else "NULL AS source_rcept_no"
    )
    source_class_expr = (
        "source_class"
        if _has_db_column("audit_fees", "source_class")
        else "NULL AS source_class"
    )
    source_period_expr = (
        "source_period"
        if _has_db_column("audit_fees", "source_period")
        else "NULL AS source_period"
    )
    metric_basis_expr = (
        "COALESCE(compatibility_basis, 'legacy_inferred') AS metric_basis"
        if _has_db_column("audit_fees", "compatibility_basis")
        else "'legacy_inferred' AS metric_basis"
    )
    audit_stmt = text(
        f"""
        SELECT bsns_year, auditor_nm, audit_fee_m, audit_hours,
               non_audit_fee_m, nas_ratio, {metric_basis_expr},
               {source_rcept_expr}, {source_class_expr}, {source_period_expr}
        FROM audit_fees
        WHERE corp_code=:corp_code
          AND bsns_year IN :years
        """
    ).bindparams(bindparam("years", expanding=True))

    with _engine_module.engine.connect() as conn:
        params = {
            "corp_code": corp_code,
            "years": requested_years,
            "fs_div": fs_div,
        }
        financial_rows = conn.execute(financial_stmt, params).mappings().all()
        audit_rows = conn.execute(audit_stmt, params).mappings().all()

    financial_by_year = {int(row["year"]): dict(row) for row in financial_rows}
    audit_by_year = {int(row["bsns_year"]): dict(row) for row in audit_rows}
    history: list[dict] = []
    covered_years: list[int] = []
    complete_years: list[int] = []
    missing_by_year: dict[str, list[str]] = {}

    for current_year in requested_years:
        financial = financial_by_year.get(current_year, {})
        audit = audit_by_year.get(current_year, {})
        total_assets = financial.get("total_assets")
        revenue = financial.get("revenue")
        audit_fee_m = audit.get("audit_fee_m")
        audit_hours = audit.get("audit_hours")
        missing_fields = [
            field
            for field, value in (
                ("total_assets", total_assets),
                ("revenue", revenue),
                ("audit_fee_m", audit_fee_m),
                ("audit_hours", audit_hours),
            )
            if value is None
        ]
        if len(missing_fields) < len(_SUBJECT_SCALE_FIELDS):
            covered_years.append(current_year)
        if not missing_fields:
            complete_years.append(current_year)
        else:
            missing_by_year[str(current_year)] = missing_fields

        history_row = {
            "year": current_year,
            "fs_div": fs_div,
            "total_assets": total_assets,
            "total_assets_100m": (
                round(float(total_assets) / 100_000_000.0, 1)
                if total_assets is not None
                else None
            ),
            "revenue": revenue,
            "revenue_100m": (
                round(float(revenue) / 100_000_000.0, 1)
                if revenue is not None
                else None
            ),
            "financial_source": financial.get("source"),
            "auditor_nm": audit.get("auditor_nm"),
            "audit_fee_m": audit_fee_m,
            "audit_hours": audit_hours,
            "non_audit_fee_m": audit.get("non_audit_fee_m"),
            "nas_ratio": audit.get("nas_ratio"),
            "metric_basis": audit.get("metric_basis"),
            "audit_source_rcept_no": audit.get("source_rcept_no"),
            "audit_source_class": audit.get("source_class"),
            "audit_source_period": audit.get("source_period"),
            "audit_hours_per_trillion_assets": _per_trillion(
                audit_hours,
                total_assets,
            ),
            "audit_hours_per_trillion_revenue": _per_trillion(
                audit_hours,
                revenue,
            ),
            "audit_fee_m_per_trillion_assets": _per_trillion(
                audit_fee_m,
                total_assets,
            ),
            "audit_fee_m_per_trillion_revenue": _per_trillion(
                audit_fee_m,
                revenue,
            ),
            "audit_fee_per_hour_m": (
                round(float(audit_fee_m) / float(audit_hours), 6)
                if audit_fee_m is not None and audit_hours
                else None
            ),
            "missing_fields": missing_fields,
            "missing_fields_label": (
                ", ".join(
                    _SUBJECT_SCALE_FIELD_LABELS[field]
                    for field in missing_fields
                )
                if missing_fields
                else None
            ),
        }
        history.append(_clean_dict({
            key: value
            for key, value in history_row.items()
            if value is not None
        }))

    status = (
        "usable"
        if len(complete_years) == len(requested_years)
        else "limited"
        if covered_years
        else "missing"
    )
    return history, {
        "status": status,
        "requested_years": requested_years,
        "covered_years": covered_years,
        "complete_years": complete_years,
        "missing_by_year": missing_by_year,
    }


def compare_peer_audit_fees(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    size_bucket_decade: Optional[float] = None,
    _peer_group: dict | None = None,
) -> dict:
    base = _peer_group if _peer_group is not None else select_peer_group(
        company=company,
        peer_limit=peer_limit,
        fs_strategy=fs_strategy,
        size_bucket_decade=size_bucket_decade,
        year=year,
    )
    if "error" in base:
        return base
    corp_code = base["subject"]["corp_code"]
    fs_div = base["selection_policy"]["fs_div_used"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes
    has_typed_fee = _has_db_column("audit_fees", "actual_fee_m")
    actual_fee_expr = "af.actual_fee_m" if has_typed_fee else "NULL"
    actual_hours_expr = "af.actual_hours" if has_typed_fee else "NULL"
    contract_fee_expr = "af.contract_fee_m" if has_typed_fee else "NULL"
    contract_hours_expr = "af.contract_hours" if has_typed_fee else "NULL"
    basis_expr = (
        "COALESCE(af.compatibility_basis, 'legacy_inferred')"
        if has_typed_fee
        else "'legacy_inferred'"
    )
    availability_expr = (
        "af.availability_status"
        if has_typed_fee
        else (
            "CASE WHEN af.audit_fee_m IS NOT NULL OR af.audit_hours IS NOT NULL "
            "THEN 'available' ELSE 'missing' END"
        )
    )
    source_rcept_expr = (
        "af.source_rcept_no"
        if _has_db_column("audit_fees", "source_rcept_no")
        else "NULL"
    )
    nas_expr = (
        "CASE WHEN COALESCE(af.compatibility_basis, 'legacy_inferred') "
        "IN ('actual', 'legacy_inferred') THEN af.nas_ratio END"
        if has_typed_fee
        else "af.nas_ratio"
    )

    stmt = text(
        f"""
        SELECT c.corp_code, c.corp_name, f.total_assets,
               af.audit_fee_m, af.audit_hours, af.non_audit_fee_m,
               {nas_expr} AS nas_ratio,
               {actual_fee_expr} AS actual_fee_m,
               {actual_hours_expr} AS actual_hours,
               {contract_fee_expr} AS contract_fee_m,
               {contract_hours_expr} AS contract_hours,
               {basis_expr} AS metric_basis,
               {availability_expr} AS availability_status,
               {source_rcept_expr} AS audit_source_rcept_no,
               CASE WHEN f.total_assets > 0 AND af.audit_fee_m IS NOT NULL
                    THEN 10000.0 * af.audit_fee_m * 1000000.0 / f.total_assets END AS fee_assets_bps,
               CASE WHEN af.audit_hours > 0 AND af.audit_fee_m IS NOT NULL
                    THEN 1.0 * af.audit_fee_m / af.audit_hours END AS fee_per_hour_m,
               CASE WHEN f.total_assets > 0 AND {actual_fee_expr} IS NOT NULL
                    THEN 10000.0 * {actual_fee_expr} * 1000000.0 / f.total_assets
                    END AS actual_fee_assets_bps,
               CASE WHEN {actual_hours_expr} > 0 AND {actual_fee_expr} IS NOT NULL
                    THEN 1.0 * {actual_fee_expr} / {actual_hours_expr}
                    END AS actual_fee_per_hour_m,
               CASE WHEN f.total_assets > 0 AND {contract_fee_expr} IS NOT NULL
                    THEN 10000.0 * {contract_fee_expr} * 1000000.0 / f.total_assets
                    END AS contract_fee_assets_bps,
               CASE WHEN {contract_hours_expr} > 0 AND {contract_fee_expr} IS NOT NULL
                    THEN 1.0 * {contract_fee_expr} / {contract_hours_expr}
                    END AS contract_fee_per_hour_m
        FROM companies c
        LEFT JOIN financials f
          ON f.corp_code=c.corp_code AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
        LEFT JOIN audit_fees af
          ON af.corp_code=c.corp_code AND af.bsns_year=:year
        WHERE c.corp_code IN :ccs
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = conn.execute(stmt, {"ccs": all_codes, "year": year, "fs": fs_div}).mappings().all()

    by_cc = {
        row["corp_code"]: _sanitize_audit_fee_row(dict(row))
        for row in rows
    }
    typed_evidence = has_typed_fee and any(
        row.get("actual_fee_m") is not None
        or row.get("actual_hours") is not None
        or row.get("contract_fee_m") is not None
        or row.get("contract_hours") is not None
        or row.get("availability_status") is not None
        for row in by_cc.values()
    )
    if not typed_evidence:
        for row in by_cc.values():
            for key in (
                "actual_fee_m",
                "actual_hours",
                "contract_fee_m",
                "contract_hours",
                "metric_basis",
                "availability_status",
                "actual_fee_assets_bps",
                "actual_fee_per_hour_m",
                "contract_fee_assets_bps",
                "contract_fee_per_hour_m",
            ):
                row.pop(key, None)
    subject_row = by_cc.get(corp_code, {})
    peer_rows = [by_cc[cc] for cc in peer_codes if cc in by_cc]
    if typed_evidence:
        metrics = {
            key: [row[key] for row in peer_rows if row.get(key) is not None]
            for key in (
                "actual_fee_m",
                "actual_hours",
                "actual_fee_assets_bps",
                "actual_fee_per_hour_m",
                "contract_fee_m",
                "contract_hours",
                "contract_fee_assets_bps",
                "contract_fee_per_hour_m",
                "nas_ratio",
            )
        }
    else:
        metrics = {
            "audit_fee_m": [
                r["audit_fee_m"]
                for r in peer_rows
                if r["audit_fee_m"] is not None
            ],
            "audit_hours": [
                r["audit_hours"]
                for r in peer_rows
                if r["audit_hours"] is not None
            ],
            "nas_ratio": [
                r["nas_ratio"]
                for r in peer_rows
                if r["nas_ratio"] is not None
            ],
            "audit_fee_to_assets_bps": [
                r["fee_assets_bps"]
                for r in peer_rows
                if r["fee_assets_bps"] is not None
            ],
            "audit_fee_per_hour_m": [
                r["fee_per_hour_m"]
                for r in peer_rows
                if r["fee_per_hour_m"] is not None
            ],
        }
    benchmarks = {k: _metric_quantiles([float(v) for v in vals]) for k, vals in metrics.items()}
    metric_coverage = {
        key: {
            "peer_count": len(peer_rows),
            "available_n": len(vals),
            "coverage_pct": round(100.0 * len(vals) / len(peer_rows), 1) if peer_rows else 0.0,
            "status": "usable" if len(vals) >= 5 else "limited" if vals else "missing",
        }
        for key, vals in metrics.items()
    }
    basis_populations = {}
    if typed_evidence:
        for basis, fee_key, hours_key in (
            ("actual", "actual_fee_m", "actual_hours"),
            ("contract", "contract_fee_m", "contract_hours"),
        ):
            fees = [
                float(row[fee_key])
                for row in peer_rows
                if row.get(fee_key) is not None
            ]
            hours = [
                float(row[hours_key])
                for row in peer_rows
                if row.get(hours_key) is not None
            ]
            basis_populations[basis] = {
                "basis": basis,
                "fee": _metric_quantiles(fees),
                "hours": _metric_quantiles(hours),
                "valid_fee_n": len(fees),
                "valid_hours_n": len(hours),
                "unavailable_fee_count": len(peer_rows) - len(fees),
                "unavailable_hours_count": len(peer_rows) - len(hours),
            }
    for key, vals in metrics.items():
        subj_key = {
            "audit_fee_to_assets_bps": "fee_assets_bps",
            "audit_fee_per_hour_m": "fee_per_hour_m",
        }.get(key, key)
        subj_val = subject_row.get(subj_key)
        benchmarks[key]["subject_percentile"] = _percentile(
            float(subj_val) if subj_val is not None else None,
            [float(v) for v in vals],
        )

    all_rows = [subject_row, *peer_rows]
    excluded_rows = [
        row for row in all_rows
        if row.get("unit_integrity_status") == "excluded_suspect_unit"
    ]
    citable_rows = [
        row for row in all_rows
        if valid_annual_filing_receipt(row.get("audit_source_rcept_no"), year)
        and (row.get("audit_fee_m") is not None or row.get("audit_hours") is not None)
    ]
    uncitable_value_rows = [
        row for row in all_rows
        if (row.get("audit_fee_m") is not None or row.get("audit_hours") is not None)
        and row not in citable_rows
    ]
    limitations = []
    if uncitable_value_rows:
        limitations.append(
            "감사보수·시간 값 중 일부에 원 공시 접수번호가 연결되지 않아 "
            "공시 확인 사실·출처로 제시하지 않았습니다."
        )
    if excluded_rows:
        limitations.append(
            "단위 또는 비감사보수 비율이 비정상적으로 보이는 행은 "
            "단위를 추정 변환하지 않고 비교·표시에서 제외했습니다."
        )
    limited_metrics = [
        key for key, info in metric_coverage.items()
        if info["status"] != "usable"
    ]
    data_quality = {
        "metric_coverage": metric_coverage,
        "limited_metrics": limited_metrics,
        "status": "limited" if limitations or limited_metrics else "usable",
        "limitations": limitations,
        "source_provenance": {
            "citable_row_count": len(citable_rows),
            "uncitable_value_row_count": len(uncitable_value_rows),
            "status": "usable" if not uncitable_value_rows else "limited",
        },
        "unit_integrity": {
            "status": "limited" if excluded_rows else "usable",
            "excluded_row_count": len(excluded_rows),
            "fee_per_hour_max_m": _MAX_AUDIT_FEE_M_PER_HOUR,
            "nas_ratio_max": _MAX_NAS_RATIO,
            "policy": "exclude_without_unit_conversion",
        },
    }
    if typed_evidence:
        data_quality["basis_populations"] = basis_populations
    subject_scale_history, subject_scale_quality = _subject_scale_history(
        corp_code,
        year=year,
        fs_div=fs_div,
    )
    data_quality["subject_scale_history"] = subject_scale_quality
    if subject_scale_quality["status"] != "usable":
        data_quality["status"] = "limited"
    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "fs_div_used": fs_div,
        "peer_count": len(peer_rows),
        "subject_metrics": subject_row,
        "subject_scale_history": subject_scale_history,
        "benchmarks": benchmarks,
        "data_quality": data_quality,
        "confirmed_facts": _audit_fee_confirmed_facts(
            [subject_row, *peer_rows], year=year,
        ),
        "peers": peer_rows[:peer_limit],
        "selection_policy": base["selection_policy"],
        "note": (
            (
                "Actual and contract peer populations are reported separately; "
                "legacy compatibility metrics retain their explicit metric_basis. "
                "DART audit fee evidence only; audit judgment not performed. "
                "Metrics with available_n < 5 are screening signals only."
            )
            if typed_evidence
            else (
                "DART audit fee contract/status data; audit judgment not performed. "
                "Metrics with available_n < 5 are screening signals only."
            )
        ),
    })


def compare_peer_risk_profile(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    _peer_group: dict | None = None,
) -> dict:
    base = _peer_group if _peer_group is not None else select_peer_group(
        company=company, peer_limit=peer_limit, fs_strategy=fs_strategy, year=year
    )
    if "error" in base:
        return base
    corp_code = base["subject"]["corp_code"]
    fs_div = base["selection_policy"]["fs_div_used"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes

    stmt = text(
        """
        SELECT f.corp_code, c.corp_name, f.revenue, f.total_assets,
               f.operating_profit, f.net_income, f.operating_cf,
               f.accrual_ratio, f.beneish_m_score,
               f.op_cf_divergence_flag, f.going_concern_flag
        FROM financials f
        JOIN companies c ON c.corp_code=f.corp_code
        WHERE f.corp_code IN :ccs AND f.year=:year AND f.quarter=4 AND f.fs_div=:fs
        """
    ).bindparams(bindparam("ccs", expanding=True))
    disc_stmt = text(
        """
        SELECT corp_code,
               SUM(CASE WHEN report_nm LIKE '%정정%' THEN 1 ELSE 0 END) restatement_like,
               SUM(CASE WHEN report_nm LIKE '%주요사항%' THEN 1 ELSE 0 END) major_event_like,
               COUNT(*) total_disclosures
        FROM disclosures
        WHERE corp_code IN :ccs
          AND disc_date >= :start_date
          AND disc_date <= :end_date
        GROUP BY corp_code
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, {"ccs": all_codes, "year": year, "fs": fs_div}).mappings().all()]
        disc_rows = conn.execute(
            disc_stmt,
            {"ccs": all_codes, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31"},
        ).mappings().all()

    disc_by_cc = {r["corp_code"]: dict(r) for r in disc_rows}
    by_cc = {r["corp_code"]: r for r in rows}
    subject_row = by_cc.get(corp_code, {})
    peer_rows = [by_cc[cc] for cc in peer_codes if cc in by_cc]

    def ratio(row: dict, numerator: str, denominator: str) -> float | None:
        n = row.get(numerator)
        d = row.get(denominator)
        if n is None or not d:
            return None
        return 100.0 * float(n) / float(d)

    derived = {
        "op_cf_to_operating_profit": [ratio(r, "operating_cf", "operating_profit") for r in peer_rows],
        "accrual_ratio": [r.get("accrual_ratio") for r in peer_rows],
        "beneish_m_score": [r.get("beneish_m_score") for r in peer_rows],
        "receivables_to_revenue": [],
        "inventory_to_revenue": [],
    }
    benchmarks = {
        k: _metric_quantiles([float(v) for v in vals if v is not None])
        for k, vals in derived.items()
    }
    metric_coverage = {
        k: {
            "peer_count": len(peer_rows),
            "available_n": benchmarks[k]["n"],
            "coverage_pct": round(100.0 * benchmarks[k]["n"] / len(peer_rows), 1) if peer_rows else 0.0,
            "status": "usable" if benchmarks[k]["n"] >= 5 else "limited" if benchmarks[k]["n"] else "missing",
        }
        for k in benchmarks
    }
    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "fs_div_used": fs_div,
        "peer_count": len(peer_rows),
        "subject_metrics": subject_row,
        "benchmarks": benchmarks,
        "data_quality": {
            "metric_coverage": metric_coverage,
            "unavailable_metrics": [
                key for key, info in metric_coverage.items()
                if info["status"] == "missing"
            ],
            "limited_metrics": [
                key for key, info in metric_coverage.items()
                if info["status"] == "limited"
            ],
        },
        "disclosure_event_counts": {
            "subject": disc_by_cc.get(corp_code, {}),
            "peers": {cc: disc_by_cc.get(cc, {}) for cc in peer_codes[:peer_limit]},
        },
        "selection_policy": base["selection_policy"],
        "note": "Risk profile is a DART-based signal pack, not audit risk assessment. Missing metrics must not be interpreted as low risk.",
    })


_POLICY_SELECTION_WEIGHTS = {
    "auditor": {"size": 0.45, "leverage": 0.30, "profitability": 0.15, "growth": 0.10},
    "investor": {"size": 0.40, "leverage": 0.05, "profitability": 0.35, "growth": 0.20},
    "balanced": {"size": 0.40, "leverage": 0.20, "profitability": 0.20, "growth": 0.20},
}
_POLICY_SELECTION_KEYS = frozenset({"size", "leverage", "profitability", "growth"})
_POLICY_EXCERPT_LIMIT = 400
_POLICY_KEYWORD_PER_COMPANY_LIMIT = 5
_POLICY_KEYWORD_TOTAL_LIMIT = 25
_POLICY_INVENTORY_ITEM_LIMIT = 20


def _policy_peer_code_map(selectors: list[str]) -> dict[str, dict]:
    """Resolve explicit overrides exactly; ambiguity and unknown selectors fail closed."""
    if not selectors:
        return {}
    values = [str(value).strip() for value in selectors if str(value).strip()]
    if not values:
        return {}
    with _engine_module.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT corp_code, stock_code, corp_name, market, induty_code
            FROM companies
            WHERE corp_code IN :values OR stock_code IN :values OR corp_name IN :values
            ORDER BY corp_code
        """).bindparams(bindparam("values", expanding=True)), {"values": values}).mappings().all()
    by_selector: dict[str, dict] = {}
    for selector in values:
        matches = [dict(row) for row in rows if selector in {
            str(row.get("corp_code") or ""), str(row.get("stock_code") or ""), str(row.get("corp_name") or ""),
        }]
        if len(matches) != 1:
            raise ValueError(f"명시적 peer selector를 정확히 해석할 수 없습니다: {selector}")
        by_selector[selector] = matches[0]
    return by_selector


def _policy_similarity(subject: float | None, candidate: float | None, *, logarithmic: bool = False) -> float | None:
    if subject is None or candidate is None:
        return None
    try:
        subject_value, candidate_value = float(subject), float(candidate)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(subject_value) or not math.isfinite(candidate_value):
        return None
    if logarithmic:
        if subject_value <= 0 or candidate_value <= 0:
            return None
        distance = abs(math.log10(candidate_value / subject_value))
    else:
        distance = abs(candidate_value - subject_value)
    return round(1.0 / (1.0 + distance), 4)


_POLICY_FINANCIAL_FIELDS = (
    "revenue", "total_assets", "total_debt", "total_equity",
    "operating_profit", "revenue_yoy",
)


def _finite_policy_number(value: object) -> object | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return value if math.isfinite(float(value)) else None
    except (TypeError, ValueError):
        return None


def _resolve_policy_financial_rows(
    rows: list[dict],
) -> tuple[dict[str, dict], set[str], set[str]]:
    """Fail closed on conflicting legacy duplicate financial rows.

    The current table has a uniqueness constraint, but retained legacy SQLite
    artifacts may not. Exact duplicate values are harmlessly de-duplicated;
    differing values receive no score rather than an arbitrary latest-row pick.
    """
    grouped: dict[str, list[dict]] = {}
    nonfinite_codes: set[str] = set()
    for raw_row in rows:
        row = dict(raw_row)
        corp_code = str(row["corp_code"])
        normalized = {}
        for field in _POLICY_FINANCIAL_FIELDS:
            value = _finite_policy_number(row.get(field))
            if row.get(field) is not None and value is None:
                nonfinite_codes.add(corp_code)
            normalized[field] = value
        row.update(normalized)
        grouped.setdefault(corp_code, []).append(row)

    resolved: dict[str, dict] = {}
    conflicts: set[str] = set()
    for corp_code, candidates in grouped.items():
        signatures = {
            tuple(candidate.get(field) for field in _POLICY_FINANCIAL_FIELDS)
            for candidate in candidates
        }
        if len(signatures) != 1:
            conflicts.add(corp_code)
            continue
        resolved[corp_code] = candidates[0]
    return resolved, conflicts, nonfinite_codes


def _policy_selection_financials(
    corp_codes: list[str], *, year: int, fs_div: str,
) -> tuple[dict[str, dict], set[str], set[str]]:
    if not corp_codes:
        return {}, set(), set()
    stmt = text("""
        SELECT id, corp_code, revenue, total_assets, total_debt, total_equity,
               operating_profit, revenue_yoy
        FROM financials
        WHERE corp_code IN :ccs AND year=:year AND quarter=4 AND fs_div=:fs_div
        ORDER BY corp_code, id DESC
    """).bindparams(bindparam("ccs", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = conn.execute(
            stmt, {"ccs": corp_codes, "year": year, "fs_div": fs_div}
        ).mappings().all()
    return _resolve_policy_financial_rows([dict(row) for row in rows])


def _policy_ratio(numerator: object, denominator: object) -> float | None:
    top = _finite_policy_number(numerator)
    bottom = _finite_policy_number(denominator)
    if top is None or bottom is None or float(bottom) == 0:
        return None
    value = float(top) / float(bottom)
    return value if math.isfinite(value) else None


def _visible_policy_ratio(value: object) -> float | None:
    """Bound a public screening ratio without changing scoring precision."""
    finite_value = _finite_policy_number(value)
    return round(float(finite_value), 4) if finite_value is not None else None


def _policy_components(subject: dict, candidate: dict) -> tuple[dict[str, float | None], dict[str, float | None]]:
    subject_assets, candidate_assets = subject.get("total_assets"), candidate.get("total_assets")
    subject_revenue, candidate_revenue = subject.get("revenue"), candidate.get("revenue")
    size_parts = [
        _policy_similarity(subject_assets, candidate_assets, logarithmic=True),
        _policy_similarity(subject_revenue, candidate_revenue, logarithmic=True),
    ]
    size_values = [value for value in size_parts if value is not None]
    values = {
        "size": round(sum(size_values) / len(size_values), 4) if size_values else None,
        "leverage": _policy_similarity(
            _policy_ratio(subject.get("total_debt"), subject.get("total_equity")),
            _policy_ratio(candidate.get("total_debt"), candidate.get("total_equity")),
        ),
        "profitability": _policy_similarity(
            _policy_ratio(subject.get("operating_profit"), subject.get("revenue")),
            _policy_ratio(candidate.get("operating_profit"), candidate.get("revenue")),
        ),
        "growth": _policy_similarity(subject.get("revenue_yoy"), candidate.get("revenue_yoy")),
    }
    metrics = {
        "revenue": _finite_policy_number(candidate.get("revenue")),
        "total_assets": _finite_policy_number(candidate.get("total_assets")),
        "leverage": _visible_policy_ratio(
            _policy_ratio(candidate.get("total_debt"), candidate.get("total_equity")),
        ),
        "profitability": _visible_policy_ratio(
            _policy_ratio(candidate.get("operating_profit"), candidate.get("revenue")),
        ),
        "growth": _visible_policy_ratio(candidate.get("revenue_yoy")),
    }
    return values, metrics


def _policy_annual_sources(
    corp_codes: list[str], *, year: int,
) -> dict[str, dict[str, object]]:
    """Return re-verified exact source-document bindings for policy rows."""
    if not corp_codes:
        return {}
    stmt = text("""
        SELECT sd.id AS source_document_id, sd.corp_code, sd.rcept_no,
               sd.source_type, d.disc_date, d.report_nm
        FROM source_documents sd
        JOIN disclosures d ON d.rcept_no=sd.rcept_no AND d.corp_code=sd.corp_code
        WHERE sd.corp_code IN :ccs
          AND sd.bsns_year=:year
          AND sd.source_type='business_report'
          AND d.report_nm LIKE :annual
        ORDER BY sd.corp_code, d.disc_date DESC, sd.rcept_no DESC
    """).bindparams(bindparam("ccs", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = conn.execute(
            stmt, {"ccs": corp_codes, "year": year, "annual": f"%사업보고서 ({year}.%"},
        ).mappings().all()
    latest: dict[str, dict[str, object]] = {}
    for row in rows:
        corp_code = str(row["corp_code"])
        if corp_code in latest:
            continue
        receipt = canonical_annual_filing_source_receipt(
            corp_code=corp_code,
            bsns_year=year,
            rcept_no=row.get("rcept_no"),
            source_document_id=row.get("source_document_id"),
            source_type=row.get("source_type"),
        )
        if receipt:
            latest[corp_code] = {
                "rcept_no": receipt,
                "source_document_id": row["source_document_id"],
                "source_type": row["source_type"],
            }
    return latest


def _policy_row_provenance(
    row: dict, annual_sources: dict[str, dict[str, object]], *, year: int,
) -> tuple[str | None, str, dict[str, object] | None]:
    raw_receipt = row.get("rcept_no")
    receipt = valid_annual_filing_receipt(raw_receipt, year)
    source_binding = annual_sources.get(str(row.get("corp_code") or ""))
    proven = (
        isinstance(raw_receipt, str) and raw_receipt == receipt
        and bool(receipt)
        and source_binding is not None
        and source_binding.get("rcept_no") == receipt
    )
    if proven:
        return receipt, "proven_annual_filing", source_binding
    if not isinstance(raw_receipt, str) or raw_receipt != receipt:
        return None, "invalid_receipt", None
    return None, "unproven_annual_filing", None


def compare_peer_accounting_policies(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_div: str = "CFS",
    fs_strategy: str = "auto",
    item_key: str | None = None,
    keyword: str | None = None,
    selection_profile: str = "balanced",
    peer_weights: dict[str, float] | None = None,
    size_bucket_decade: float | None = None,
    include_peers: list[str] | None = None,
    exclude_peers: list[str] | None = None,
    peer_criteria: PeerCriteriaProfile | list[str] | dict | None = None,
    _peer_group: dict | None = None,
    _return_note_comparison_peer_group: bool = False,
) -> dict:
    """Compare cached accounting policy item coverage across selected peers.

    This is intentionally cache-only. It does not fetch DART documents at MCP
    runtime, so external users do not need a DART API key.
    """
    if not 1 <= peer_limit <= 200:
        raise ValueError("peer_limit은 1~200 범위여야 합니다.")
    if selection_profile not in _POLICY_SELECTION_WEIGHTS:
        raise ValueError("selection_profile은 auditor/investor/balanced 중 하나여야 합니다.")
    include_peers, exclude_peers = include_peers or [], exclude_peers or []
    for selector in [*include_peers, *exclude_peers]:
        if not isinstance(selector, str) or not selector.strip() or len(selector.strip()) > 100:
            raise ValueError("peer selector는 1~100자 비어 있지 않은 문자열이어야 합니다.")
    if set(include_peers) & set(exclude_peers):
        raise ValueError("include_peers와 exclude_peers는 중복될 수 없습니다.")
    if peer_weights is not None and (
        not peer_weights or set(peer_weights) - _POLICY_SELECTION_KEYS
        or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1 for value in peer_weights.values())
        or sum(peer_weights.values()) <= 0
    ):
        raise ValueError("peer_weights는 size/leverage/profitability/growth의 0~1 가중치여야 합니다.")
    if size_bucket_decade is not None and not 0.1 <= size_bucket_decade <= 3.0:
        raise ValueError("size_bucket_decade는 0.1~3.0 범위여야 합니다.")
    custom_selection = bool(
        include_peers or exclude_peers or peer_weights or size_bucket_decade is not None
        or selection_profile != "balanced" or peer_criteria is not None
    )
    presentation_mode = custom_selection or item_key is not None or keyword is not None
    base = _peer_group if _peer_group is not None else select_peer_group(
        company=company, peer_limit=200 if custom_selection else peer_limit,
        fs_strategy=fs_strategy, year=year, size_bucket_decade=size_bucket_decade,
        criteria=peer_criteria,
    )
    if "error" in base:
        return base

    corp_code = base["subject"]["corp_code"]
    included_map = _policy_peer_code_map(include_peers)
    excluded_map = _policy_peer_code_map(exclude_peers)
    if corp_code in {row["corp_code"] for row in [*included_map.values(), *excluded_map.values()]}:
        raise ValueError("대상회사는 peer override로 지정할 수 없습니다.")
    included_codes = {row["corp_code"] for row in included_map.values()}
    excluded_codes = {row["corp_code"] for row in excluded_map.values()}
    if included_codes & excluded_codes:
        raise ValueError("해석된 include/exclude peer가 중복됩니다.")
    if len(included_codes) > peer_limit:
        raise ValueError("include_peers 수는 peer_limit을 초과할 수 없습니다.")

    candidate_by_code = {row["corp_code"]: dict(row) for row in base["peers"]}
    for row in included_map.values():
        candidate_by_code.setdefault(row["corp_code"], dict(row))
    candidate_codes = sorted(candidate_by_code)
    selection_year = int(base["selection_policy"].get("resolved_year") or year)
    financial_rows, conflicting_financial_codes, nonfinite_financial_codes = _policy_selection_financials(
        [corp_code, *candidate_codes], year=selection_year, fs_div=base["selection_policy"]["fs_div_used"]
    )
    weights = (
        {key: float((peer_weights or {}).get(key, 0.0)) for key in _POLICY_SELECTION_KEYS}
        if peer_weights is not None
        else dict(_POLICY_SELECTION_WEIGHTS[selection_profile])
    )
    subject_financials = financial_rows.get(corp_code, {})
    peer_selection: list[dict] = []
    ranked_candidates: list[tuple[float, str]] = []
    for candidate_code in candidate_codes:
        candidate = candidate_by_code[candidate_code]
        components, metric_values = _policy_components(subject_financials, financial_rows.get(candidate_code, {}))
        available_weights = {key: weights[key] for key, value in components.items() if value is not None}
        weight_total = sum(available_weights.values())
        algorithmic_score = (
            round(sum(components[key] * weight for key, weight in available_weights.items()) / weight_total, 4)
            if weight_total else None
        )
        component_contributions = {
            key: round(components[key] * weights[key] / weight_total, 4)
            if components[key] is not None and weight_total else None
            for key in _POLICY_SELECTION_KEYS
        }
        limitations = [f"missing_financial_dimensions:{key}" for key, value in components.items() if value is None]
        if candidate_code in conflicting_financial_codes:
            limitations.append("conflicting_financial_rows_no_score")
        if candidate_code in nonfinite_financial_codes:
            limitations.append("nonfinite_financial_values_unavailable")
        direct = candidate_code in included_codes
        excluded = candidate_code in excluded_codes
        if excluded:
            status, reason = "excluded", "user_exclude_override"
        elif direct:
            status, reason = "included", "direct_include_override"
        else:
            status, reason = "candidate", "industry_sector_candidate"
            ranked_candidates.append((algorithmic_score if algorithmic_score is not None else -1.0, candidate_code))
        peer_selection.append({
            "corp_code": candidate_code,
            "corp_name": candidate.get("corp_name"),
            "market": candidate.get("market"),
            "induty_code": candidate.get("induty_code"),
            "selection_status": status,
            "selection_reason": reason,
            "algorithmic_score": None if direct else algorithmic_score,
            "score_components": components,
            "component_contributions": component_contributions,
            "weights": weights,
            "financial_values": metric_values,
            "financial_similarity_status": (
                "internal_cached_screening_input_not_receipt_proven"
            ),
            "data_year": selection_year,
            "fs_div": base["selection_policy"]["fs_div_used"],
            "limitations": limitations,
        })
    # Explicit inclusions are visible overrides and occupy peer_limit first;
    # the remaining slots use deterministic score then company-code ordering.
    direct_codes = sorted(included_codes - excluded_codes)
    ranked_codes = [code for _score, code in sorted(ranked_candidates, key=lambda pair: (-pair[0], pair[1]))]
    peer_codes = (direct_codes + [code for code in ranked_codes if code not in direct_codes])[:peer_limit]
    if not presentation_mode:
        # Exact legacy defaults retain the pre-extension peer ordering and
        # response shape; richer presentation fields are opt-in.
        peer_codes = [row["corp_code"] for row in base["peers"]]
    for row in peer_selection:
        if row["selection_status"] == "candidate":
            if row["corp_code"] in peer_codes:
                row["selection_status"] = "included"
                row["selection_reason"] = (
                    "algorithmic_financial_similarity"
                    if row["algorithmic_score"] is not None
                    else "industry_sector_fallback_no_financial_score"
                )
            else:
                row["selection_status"] = "excluded"
                row["selection_reason"] = "outside_peer_limit"
    for candidate in excluded_map.values():
        if candidate["corp_code"] not in candidate_by_code:
            peer_selection.append({
                "corp_code": candidate["corp_code"], "corp_name": candidate.get("corp_name"),
                "market": candidate.get("market"), "induty_code": candidate.get("induty_code"),
                "selection_status": "excluded", "selection_reason": "user_exclude_override",
                "algorithmic_score": None, "score_components": {},
                "component_contributions": {key: None for key in _POLICY_SELECTION_KEYS}, "weights": weights,
                "financial_values": {},
                "financial_similarity_status": "not_scored_explicitly_excluded",
                "data_year": selection_year,
                "fs_div": base["selection_policy"]["fs_div_used"],
                "limitations": ["explicitly_excluded_before_candidate_scoring"],
            })
    peer_selection.sort(key=lambda row: (row["selection_status"] != "included", row["corp_code"]))
    all_codes = [corp_code] + peer_codes
    stmt = text(
        """
        SELECT p.corp_code, c.corp_name, p.rcept_no, p.item_key, p.heading, p.body,
               p.body_length, p.body_hash
        FROM accounting_policy_items p
        JOIN companies c ON c.corp_code = p.corp_code
        WHERE p.corp_code IN :ccs
          AND p.bsns_year = :year
          AND p.fs_div = :fs
        ORDER BY p.corp_code, p.item_key
        """
    ).bindparams(bindparam("ccs", expanding=True))

    with _engine_module.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            stmt, {"ccs": all_codes, "year": year, "fs": fs_div}
        ).mappings().all()]

    by_corp: dict[str, list[dict]] = {}
    for row in rows:
        by_corp.setdefault(row["corp_code"], []).append(row)

    subject_items = {
        row["item_key"]: {
            "heading": row["heading"],
            "body_length": row["body_length"],
            "body_hash": row["body_hash"],
        }
        for row in by_corp.get(corp_code, [])
    }

    item_keys = sorted({row["item_key"] for row in rows})
    peer_item_coverage = {}
    for key in item_keys:
        covered = [
            cc for cc in peer_codes
            if any(row["item_key"] == key for row in by_corp.get(cc, []))
        ]
        peer_item_coverage[key] = {
            "peer_count": len(peer_codes),
            "covered_peers": len(covered),
            "coverage_pct": round(100.0 * len(covered) / len(peer_codes), 1) if peer_codes else 0.0,
            "subject_has_item": key in subject_items,
        }

    peer_summaries = []
    for cc in peer_codes:
        items = by_corp.get(cc, [])
        if not items:
            continue
        peer_summaries.append({
            "corp_code": cc,
            "corp_name": items[0]["corp_name"],
            "item_count": len(items),
            "item_keys": sorted(row["item_key"] for row in items),
        })

    peer_coverage_pct = round(100.0 * len(peer_summaries) / len(peer_codes), 1) if peer_codes else 0.0
    topic_requested = item_key is not None or keyword is not None
    selected_topic = {"item_key": item_key, "keyword": keyword}
    matching_rows = [
        row for row in rows
        if topic_requested
        and (item_key is None or row["item_key"] == item_key)
        and (keyword is None or keyword.lower() in str(row.get("heading") or "").lower()
             or keyword.lower() in str(row.get("body") or "").lower())
    ]
    matched_count_by_company = {code: 0 for code in all_codes}
    for row in matching_rows:
        matched_count_by_company[row["corp_code"]] = matched_count_by_company.get(row["corp_code"], 0) + 1
    selected_rows: list[dict] = []
    returned_per_company = {code: 0 for code in all_codes}
    for row in matching_rows:
        if keyword is not None and (
            len(selected_rows) >= _POLICY_KEYWORD_TOTAL_LIMIT
            or returned_per_company[row["corp_code"]] >= _POLICY_KEYWORD_PER_COMPANY_LIMIT
        ):
            continue
        selected_rows.append(row)
        returned_per_company[row["corp_code"]] = returned_per_company.get(row["corp_code"], 0) + 1
    presentation_truncation = {
        "requested": topic_requested,
        "total_matches": len(matching_rows),
        "returned_matches": len(selected_rows),
        "per_company_limit": _POLICY_KEYWORD_PER_COMPANY_LIMIT if keyword is not None else None,
        "total_limit": _POLICY_KEYWORD_TOTAL_LIMIT if keyword is not None else None,
        "truncated": len(selected_rows) < len(matching_rows),
    }
    annual_sources = _policy_annual_sources(all_codes, year=year)
    matched_provenance_by_company = {code: [] for code in all_codes}
    for row in matching_rows:
        _receipt, status, _source_binding = _policy_row_provenance(
            row,
            annual_sources,
            year=year,
        )
        matched_provenance_by_company.setdefault(row["corp_code"], []).append(status)
    note_presentations: list[dict] = []
    rows_by_company = {code: [] for code in all_codes}
    for row in selected_rows:
        rows_by_company.setdefault(row["corp_code"], []).append(row)
        receipt, provenance_status, source_binding = _policy_row_provenance(
            row, annual_sources, year=year,
        )
        body = str(row.get("body") or "")
        note_presentations.append(_clean_dict({
            "corp_code": row["corp_code"], "corp_name": row["corp_name"], "item_key": row["item_key"],
            "heading": row.get("heading"), "body_excerpt": body[:_POLICY_EXCERPT_LIMIT],
            "body_length": row.get("body_length") if row.get("body_length") is not None else len(body),
            "body_hash": row.get("body_hash"), "data_year": year, "fs_div": fs_div,
            "provenance_status": provenance_status,
            "rcept_no": receipt if provenance_status == "proven_annual_filing" else None,
            "source_url": dart_filing_url(receipt) if provenance_status == "proven_annual_filing" else None,
            "source_document_id": (
                source_binding.get("source_document_id")
                if source_binding is not None
                else None
            ),
            "source_type": (
                source_binding.get("source_type")
                if source_binding is not None
                else None
            ),
            "canonical_source_binding": (
                provenance_status == "proven_annual_filing"
            ),
        }))
    if item_key is not None:
        presentation_codes = {row["corp_code"] for row in note_presentations}
        names = {corp_code: base["subject"].get("corp_name")}
        names.update({row["corp_code"]: row.get("corp_name") for row in peer_selection})
        for code in all_codes:
            if code not in presentation_codes:
                note_presentations.append({
                    "corp_code": code, "corp_name": names.get(code),
                    "item_key": item_key, "heading": None, "body_excerpt": None,
                    "data_year": year, "fs_div": fs_div,
                    "provenance_status": "cache_missing_not_filing_absence",
                })
    note_presentations.sort(key=lambda row: (all_codes.index(row["corp_code"]), row["item_key"], str(row.get("heading") or "")))
    topic_coverage = []
    if topic_requested:
        names = {corp_code: base["subject"].get("corp_name")}
        names.update({row["corp_code"]: row.get("corp_name") for row in peer_selection})
        for code in all_codes:
            topic_coverage.append({
                "corp_code": code, "corp_name": names.get(code),
                "status": "topic_cached" if matched_count_by_company.get(code) else "cache_missing_not_filing_absence",
                "matched_item_count": matched_count_by_company.get(code, 0),
                "returned_item_count": returned_per_company.get(code, 0),
            })
    topic_inventory = []
    if presentation_mode and not topic_requested:
        for code in all_codes:
            items = by_corp.get(code, [])
            keys = sorted(row["item_key"] for row in items)
            topic_inventory.append({
                "corp_code": code,
                "corp_name": base["subject"].get("corp_name") if code == corp_code else next(
                    (row.get("corp_name") for row in peer_selection if row["corp_code"] == code), None,
                ),
                "cached_item_count": len(keys),
                "item_keys": keys[:_POLICY_INVENTORY_ITEM_LIMIT],
                "item_keys_truncated": len(keys) > _POLICY_INVENTORY_ITEM_LIMIT,
            })
    subject_topic_available = bool(matched_count_by_company.get(corp_code)) if topic_requested else bool(subject_items)
    data_quality = {
        "status": cache_quality_status(
            subject_count=len(subject_items),
            peer_total=len(peer_codes),
            peer_covered=len(peer_summaries),
        ),
        "source": "accounting_policy_items",
        "requested_year": year,
        "subject_policy_count": len(subject_items),
        "peer_count": len(peer_codes),
        "peers_with_policy": len(peer_summaries),
        "peer_coverage_pct": peer_coverage_pct,
        "interpretation": (
            "Coverage measures local cache availability. Missing items must not be interpreted "
            "as absence of accounting policy disclosure."
        ),
    }
    limitations = list(data_quality.get("limitations") or [])
    if presentation_mode and not topic_requested:
        data_quality["status"] = "limited"
        limitations.append("topic_selector_required")
    elif not subject_topic_available:
        data_quality["status"] = "limited"
        limitations.append("subject_topic_cache_missing_not_filing_absence")
    elif topic_requested and "proven_annual_filing" not in matched_provenance_by_company.get(corp_code, []):
        data_quality["status"] = "limited"
        limitations.append("subject_topic_receipt_not_proven_against_latest_annual_filing")
    if topic_requested:
        peer_topic_quality = {
            "final_peer_count": len(peer_codes),
            "cache_missing_count": 0,
            "unproven_receipt_count": 0,
            "proven_count": 0,
        }
        for peer_code in peer_codes:
            if not matched_count_by_company.get(peer_code):
                peer_topic_quality["cache_missing_count"] += 1
            elif "proven_annual_filing" in matched_provenance_by_company.get(peer_code, []):
                peer_topic_quality["proven_count"] += 1
            else:
                peer_topic_quality["unproven_receipt_count"] += 1
        data_quality["peer_topic_quality"] = peer_topic_quality
        if peer_topic_quality["cache_missing_count"]:
            data_quality["status"] = "limited"
            limitations.append("peer_topic_cache_missing_not_filing_absence")
        if peer_topic_quality["unproven_receipt_count"]:
            data_quality["status"] = "limited"
            limitations.append("peer_topic_receipt_not_proven")
    if limitations:
        data_quality["limitations"] = sorted(set(limitations))

    result = {
        "subject": base["subject"],
        "year": year,
        "fs_div": fs_div,
        "subject_policy_count": len(subject_items),
        "subject_items": subject_items,
        "peer_count": len(peer_codes),
        "peers_with_policy": len(peer_summaries),
        "peer_item_coverage": peer_item_coverage,
        "peer_summaries": peer_summaries[:peer_limit],
        "selection_policy": {
            **base["selection_policy"],
            "selection_profile": selection_profile,
            "weights": weights,
            "weight_provenance": "custom" if peer_weights else "profile_default",
            "override_provenance": "custom" if include_peers or exclude_peers else "default",
            "candidate_universe": "adaptive KSIC prefix and sector filters only; market is display-only and business text is unindexed",
            "preselection_criteria": {
                "candidate_universe": "adaptive KSIC prefix and sector filters only; market is display-only and business text is unindexed",
                "industry_sector_market_context": {
                    "matched_prefix_len": base["selection_policy"].get("matched_prefix_len"),
                    "exclude_other_sectors": base["selection_policy"].get("exclude_other_sectors"),
                    "subject_market": base["subject"].get("market"),
                    "business_context": "not indexed for peer scoring; no business-overlap score is fabricated",
                    "market_context": "reported for each candidate; market is not a default exclusion rule",
                },
                "financial_similarity": {
                    "components": ["size", "leverage", "profitability", "growth"],
                    "size_basis": "mean of available positive cached revenue and total_assets similarities; one available dimension is sufficient",
                    "missing_value_policy": "missing components receive no score or contribution; no value is fabricated",
                    "weighting_status": "internal screening heuristic; not an auditing, accounting, or external-standard methodology",
                    "source_provenance": "internal cached financials screening inputs only; no receipt-proven filing provenance",
                },
                "supported_customization": {
                    "selection_profile": ["auditor", "investor", "balanced"],
                    "peer_weights": sorted(_POLICY_SELECTION_KEYS),
                    "size_bucket_decade_range": [0.1, 3.0],
                    "include_exclude": "exact corp_code, stock_code, or company name",
                },
                "unsupported_customization": ["business-text threshold", "market-cap threshold", "unreliable financial field scoring"],
            },
        },
        "data_quality": data_quality,
        "coverage_note": (
            "Accounting policy comparison uses cached accounting_policy_items only; "
            "low coverage means dataset refresh is required, not that peers lack policy disclosures."
        ),
    }
    if presentation_mode:
        result.update({
            "candidate_universe": {
                "candidate_count": len(candidate_by_code),
                "algorithmic_candidate_count": len(ranked_candidates),
                "final_peer_count": len(peer_codes),
                "description": "Candidate universe is separate from final peers; see peer_selection for every returned candidate/override status.",
            },
            "peer_selection": peer_selection,
            "selected_topic": selected_topic,
            "note_presentations": note_presentations,
            "topic_coverage": topic_coverage,
            "topic_inventory": topic_inventory,
            "presentation_truncation": presentation_truncation,
            "methodology": {
            "selection_profile": selection_profile,
            "candidate_universe": "adaptive KSIC prefix and sector candidate universe only; market is display-only and business text is unindexed. Direct includes are overrides, not algorithmic matches",
            "financial_similarity": "cached financials are internal screening inputs with no receipt-proven filing provenance. Size averages available positive revenue/total-assets similarities; leverage, profitability, and growth are scored only when both sides have reliable fields.",
            "weighting_status": "auditor/investor/balanced weights are internal screening heuristics, not auditing or accounting standards.",
            "comparison_limitations": "Heading, placement and text differences are screening signals only; they are not an accounting treatment conclusion.",
            "source_rule": "DART links appear only when the cached exact 14-digit receipt matches the latest same-company, same-year annual filing.",
            },
        })
    else:
        result["selection_policy"] = base["selection_policy"]
    if _return_note_comparison_peer_group:
        result["_note_comparison_peer_group"] = {
            "subject": dict(base["subject"]),
            "peers": [dict(candidate_by_code[code]) for code in peer_codes],
            "peer_count": len(peer_codes),
            "confidence": base.get("confidence"),
            "selection_policy": dict(result["selection_policy"]),
        }
    return _clean_dict(result)


def compare_peer_kam_topics(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    _peer_group: dict | None = None,
) -> dict:
    """Audit-report/KAM signal view from cached DART disclosures and sections."""
    base = _peer_group if _peer_group is not None else select_peer_group(
        company=company, peer_limit=peer_limit, fs_strategy=fs_strategy, year=year
    )
    if "error" in base:
        return base

    corp_code = base["subject"]["corp_code"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes
    stmt = text(
        """
        SELECT d.corp_code, c.corp_name, d.rcept_no, d.disc_date, d.report_nm
        FROM disclosures d
        JOIN companies c ON c.corp_code = d.corp_code
        WHERE d.corp_code IN :ccs
          AND d.disc_date >= :start_date
          AND d.disc_date <= :end_date
          AND d.report_nm LIKE '%감사보고서%'
        ORDER BY d.corp_code, d.disc_date DESC
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine_module.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            stmt,
            {"ccs": all_codes, "start_date": f"{year + 1}-01-01", "end_date": f"{year + 1}-12-31"},
        ).mappings().all()]

    events_by_corp: dict[str, list[dict]] = {}
    topic_counts = {topic: 0 for topic in KAM_TOPIC_KEYWORDS}
    restated = delayed = 0
    for row in rows:
        row_topics = topic_hits(row["report_nm"])
        for topic in row_topics:
            topic_counts[topic] += 1
        name = row["report_nm"] or ""
        if "정정" in name:
            restated += 1
        if "지연" in name:
            delayed += 1
        row["topic_hints"] = row_topics
        events_by_corp.setdefault(row["corp_code"], []).append(row)

    subject_events = events_by_corp.get(corp_code, [])
    peer_events = {
        cc: events_by_corp.get(cc, [])
        for cc in peer_codes
        if events_by_corp.get(cc)
    }

    dcm_select = "rs.dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
    section_stmt = text(
        f"""
        SELECT rs.corp_code, c.corp_name, rs.rcept_no, {dcm_select}, rs.source_type, rs.section_key,
               rs.section_title, rs.body_text, rs.body_length
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE rs.corp_code IN :ccs
          AND rs.bsns_year=:year
          AND rs.source_type='audit_report'
          AND rs.section_key IN ('kam', 'audit_opinion', 'emphasis', 'going_concern')
        ORDER BY rs.corp_code, rs.section_key, rs.ordinal
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine_module.engine.connect() as conn:
        section_rows = [dict(r) for r in conn.execute(
            section_stmt,
            {"ccs": all_codes, "year": year},
        ).mappings().all()]
    section_source = "report_sections.audit_report"
    audit_report_sections_source = "audit_report_sections"
    if not section_rows:
        section_rows = evidence_report_section_rows(
            corp_codes=all_codes,
            year=year,
            source_types=["audit_report"],
            section_keys=["kam", "audit_opinion", "emphasis", "going_concern"],
            limit=max(500, len(all_codes) * 8),
        )
        if section_rows:
            section_source = "evidence_documents.audit_report"
            audit_report_sections_source = "evidence_documents.audit_report"

    from kreports.processor.audit_report_parser import classify_kam_topics, summarize_kam_body

    sections_by_corp: dict[str, list[dict]] = {}
    body_topic_counts: dict[str, int] = {}
    for row in section_rows:
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:1200]
        row.pop("body_text", None)
        kam_analysis = summarize_kam_body(body) if row.get("section_key") == "kam" else None
        if kam_analysis:
            row["kam_analysis"] = kam_analysis
        topics = (kam_analysis or {}).get("topics") or (
            classify_kam_topics(body) if row.get("section_key") == "kam" else []
        )
        row["topic_hints"] = topics
        for topic in topics:
            body_topic_counts[topic] = body_topic_counts.get(topic, 0) + 1
        sections_by_corp.setdefault(row["corp_code"], []).append(row)

    summary_stmt = text(
        f"""
        SELECT rs.corp_code, c.corp_name, rs.rcept_no, {dcm_select}, rs.source_type, rs.section_key,
               rs.section_title, rs.body_text, rs.body_length
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE rs.corp_code IN :ccs
          AND rs.bsns_year=:year
          AND rs.source_type='business_report'
          AND rs.section_key='kam'
        ORDER BY rs.corp_code, rs.ordinal
        """
    ).bindparams(bindparam("ccs", expanding=True))
    with _engine_module.engine.connect() as conn:
        summary_rows = [dict(r) for r in conn.execute(
            summary_stmt,
            {"ccs": all_codes, "year": year},
        ).mappings().all()]
    for row in summary_rows:
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:1200]
        row.pop("body_text", None)

    business_summary_by_corp: dict[str, list[dict]] = {}
    for row in summary_rows:
        business_summary_by_corp.setdefault(row["corp_code"], []).append(row)

    subject_sections = sections_by_corp.get(corp_code, [])
    peer_sections = {
        cc: sections_by_corp.get(cc, [])
        for cc in peer_codes
        if sections_by_corp.get(cc)
    }
    kam_body_rows = [r for r in section_rows if r.get("section_key") == "kam"]
    kam_coverage = kam_hint_coverage(
        [row for company_rows in sections_by_corp.values() for row in company_rows]
    )
    has_body = bool(kam_body_rows)
    limitations = (
        ["KAM paragraphs are based on cached audit_report body sections, not business-report summary tables."]
        if has_body else [
            "Detailed audit-report KAM paragraphs are not yet persisted for this peer set.",
            "Business-report KAM summary, when present, is exposed separately and is not treated as the primary KAM body.",
            "Topic hints are based on cached audit-report disclosure titles only.",
            "Use as KAM coverage/event screening, not KAM determination.",
        ]
    )
    if has_body and not peer_sections:
        limitations.append(
            "Peer audit-report KAM body coverage is currently zero for the selected peer group; do not infer peer topic absence."
        )
    elif has_body and len(peer_sections) < max(1, len(peer_codes) // 2):
        limitations.append(
            "Peer audit-report KAM body coverage is limited for the selected peer group; compare topics cautiously."
        )

    data_quality = {
        "status": cache_quality_status(
            subject_count=len([r for r in subject_sections if r.get("section_key") == "kam"]),
            peer_total=len(peer_codes),
            peer_covered=len(peer_sections),
        ),
        "source": section_source,
        "requested_year": year,
        "subject_kam_body_count": len([r for r in subject_sections if r.get("section_key") == "kam"]),
        "peer_companies_with_sections": len(peer_sections),
        "peer_count": len(peer_codes),
        "total_audit_report_sections": len(section_rows),
        "total_kam_body_count": len(kam_body_rows),
        "kam_reason_coverage": kam_coverage["reason"],
        "kam_procedure_coverage": kam_coverage["procedure"],
        "business_report_summary_sections": len(summary_rows),
        "available_subject_kam_years": sorted(set(
            cached_years_for_sections(corp_code, "audit_report", "kam")
            + evidence_years_for_sections(corp_code, "audit_report", "kam")
        ), reverse=True),
        "coverage_note": (
            "KAM body comparison uses cached audit_report report_sections, with evidence_documents fallback. "
            "Disclosure events are not a substitute for KAM body/topic coverage."
        ),
    }

    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "peer_count": len(peer_codes),
        "audit_report_events": {
            "subject_count": len(subject_events),
            "peer_companies_with_events": len(peer_events),
            "total_events": len(rows),
            "restatement_like_events": restated,
            "delayed_submission_like_events": delayed,
        },
        "subject_events": subject_events[:10],
        "peer_event_samples": {
            cc: events[:3] for cc, events in list(peer_events.items())[:peer_limit]
        },
        "kam_topics": body_topic_counts if has_body else {
            topic: count for topic, count in topic_counts.items() if count > 0
        },
        "audit_report_sections": {
            "subject_section_count": len(subject_sections),
            "peer_companies_with_sections": len(peer_sections),
            "total_sections": len(section_rows),
            "kam_body_count": len(kam_body_rows),
            "kam_reason_coverage": kam_coverage["reason"],
            "kam_procedure_coverage": kam_coverage["procedure"],
            "source": audit_report_sections_source if has_body else "disclosure_events_only",
        },
        "data_quality": data_quality,
        "business_report_kam_summary": {
            "subject_summary_count": len(business_summary_by_corp.get(corp_code, [])),
            "peer_companies_with_summary": len([
                cc for cc in peer_codes if business_summary_by_corp.get(cc)
            ]),
            "total_summary_sections": len(summary_rows),
            "note": "사업보고서 KAM은 요약표/요약 문단으로만 취급하며, 상세 판단근거와 감사절차의 기준 소스는 audit_report입니다.",
        },
        "subject_business_report_kam_summary": business_summary_by_corp.get(corp_code, [])[:5],
        "subject_sections": subject_sections[:10],
        "peer_section_samples": {
            cc: sections[:3] for cc, sections in list(peer_sections.items())[:peer_limit]
        },
        "selection_policy": base["selection_policy"],
        "limitations": limitations,
    })


def compare_peer_audit_report_matters(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    _peer_group: dict | None = None,
) -> dict:
    """Compare non-KAM audit report matters across the selected peer group.

    Emphasis of matter, other matter, going concern, and basis-for-opinion
    paragraphs are not audit opinions by themselves. They are useful screening
    evidence for acceptance/continuance and peer disclosure comparison.
    """
    base = _peer_group if _peer_group is not None else select_peer_group(
        company=company, peer_limit=peer_limit, fs_strategy=fs_strategy, year=year
    )
    if "error" in base:
        return base

    corp_code = base["subject"]["corp_code"]
    peer_codes = [p["corp_code"] for p in base["peers"]]
    all_codes = [corp_code] + peer_codes
    dcm_select = "rs.dcm_no" if _has_db_column("report_sections", "dcm_no") else "NULL AS dcm_no"
    stmt = text(
        f"""
        SELECT rs.corp_code, c.corp_name, rs.rcept_no, {dcm_select}, rs.source_type,
               rs.section_key, rs.section_title, rs.body_text, rs.body_length, rs.ordinal
        FROM report_sections rs
        JOIN companies c ON c.corp_code=rs.corp_code
        WHERE rs.corp_code IN :ccs
          AND rs.bsns_year=:year
          AND rs.source_type='audit_report'
          AND rs.section_key IN :section_keys
        ORDER BY rs.corp_code, rs.section_key, rs.ordinal
        """
    ).bindparams(
        bindparam("ccs", expanding=True),
        bindparam("section_keys", expanding=True),
    )
    with _engine_module.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(
            stmt,
            {"ccs": all_codes, "year": year, "section_keys": list(AUDIT_MATTER_KEYS)},
        ).mappings().all()]
    row_source = "report_sections.audit_report"
    if not rows:
        rows = evidence_report_section_rows(
            corp_codes=all_codes,
            year=year,
            source_types=["audit_report"],
            section_keys=list(AUDIT_MATTER_KEYS),
            limit=max(500, len(all_codes) * 8),
        )
        if rows:
            row_source = "evidence_documents"

    counts = {
        key: {
            "subject_count": 0,
            "subject_signal_count": 0,
            "peer_companies_with_section": 0,
            "total_sections": 0,
        }
        for key in AUDIT_MATTER_KEYS
    }
    by_corp: dict[str, list[dict]] = {}
    peer_corp_by_key: dict[str, set[str]] = {key: set() for key in AUDIT_MATTER_KEYS}
    for row in rows:
        key = row["section_key"]
        body = _display_text(row.get("body_text"))
        row["body_excerpt"] = body[:1200]
        row.update(classify_audit_matter(body, key))
        row.pop("body_text", None)
        counts[key]["total_sections"] += 1
        if row["corp_code"] == corp_code:
            counts[key]["subject_count"] += 1
            if row.get("acceptance_signal") is True:
                counts[key]["subject_signal_count"] += 1
        elif row["corp_code"] in peer_codes:
            peer_corp_by_key[key].add(row["corp_code"])
        by_corp.setdefault(row["corp_code"], []).append(row)

    for key in AUDIT_MATTER_KEYS:
        counts[key]["peer_companies_with_section"] = len(peer_corp_by_key[key])
        counts[key]["peer_coverage_pct"] = (
            round(100.0 * len(peer_corp_by_key[key]) / len(peer_codes), 1)
            if peer_codes else 0.0
        )

    subject_matters = by_corp.get(corp_code, [])
    peer_sections_by_corp = {
        cc: by_corp.get(cc, [])
        for cc in peer_codes
        if by_corp.get(cc)
    }
    subject_count = len(subject_matters)
    peer_covered = len(peer_sections_by_corp)
    data_quality = {
        "status": cache_quality_status(
            subject_count=subject_count,
            peer_total=len(peer_codes),
            peer_covered=peer_covered,
        ),
        "source": row_source,
        "requested_year": year,
        "section_keys": list(AUDIT_MATTER_KEYS),
        "subject_section_count": subject_count,
        "peer_companies_with_sections": peer_covered,
        "peer_count": len(peer_codes),
        "total_sections": len(rows),
        "available_subject_years": sorted(set(
            year
            for key in AUDIT_MATTER_KEYS
            for year in cached_years_for_sections(corp_code, "audit_report", key)
        ), reverse=True),
        "interpretation": (
            "Emphasis/other-matter/going-concern paragraphs are audit-report "
            "screening evidence. Absence in cache does not prove absence in the filing."
        ),
    }

    return _clean_dict({
        "subject": base["subject"],
        "year": year,
        "peer_count": len(peer_codes),
        "matter_counts": counts,
        "subject_matters": subject_matters[:20],
        "peer_matter_samples": {
            cc: sections[:5] for cc, sections in list(peer_sections_by_corp.items())[:peer_limit]
        },
        "data_quality": data_quality,
        "selection_policy": base["selection_policy"],
        "limitations": [
            "These paragraphs are screening evidence, not audit conclusions.",
            "Going-concern emphasis in audit reports should be read together with the opinion and basis-for-opinion sections.",
            "Peer comparisons depend on current local report_sections coverage.",
        ],
    })


def compare_peer_audit_procedures(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    _peer_group: dict | None = None,
) -> dict:
    """Compare KAM audit procedure patterns for a subject and its peer group."""
    procedure_tables = {
        "companies",
        "kam_items",
        "audit_procedure_items",
    }

    def unavailable_result(reason: str, group: dict | None) -> dict:
        return {
            "subject": (
                (group or {}).get("subject")
                or {"corp_code": company}
            ),
            "year": year,
            "peer_count": len((group or {}).get("peers", [])),
            "companies_with_procedures": 0,
            "subject_procedure_type_counts": {},
            "peer_procedure_type_counts": {},
            "subject_method_counts": {},
            "peer_method_counts": {},
            "peer_kam_topic_counts": {},
            "coverage": {
                "denominator_full_body_kam_receipts": 0,
                "full_body_kam_receipts_with_procedures": 0,
                "rate": 0.0,
                "quality_gaps": {},
            },
            "data_quality": {
                "status": "unavailable",
                "source": "runtime_db",
                "coverage_note": reason,
            },
        }

    if _peer_group is not None:
        base = _peer_group
    else:
        selection_tables = {"companies", "financials", "audit_fees"}
        available, unavailable_reason = procedure_database_preflight(
            selection_tables
        )
        if not available:
            return unavailable_result(
                str(unavailable_reason),
                None,
            )
        with procedure_read_engine(selection_tables) as read_engine:
            base = select_peer_group(
                company=company,
                peer_limit=peer_limit,
                fs_strategy=fs_strategy,
                year=year,
                _read_engine=read_engine,
            )
    if "error" in base:
        return base
    available, unavailable_reason = procedure_database_preflight(
        procedure_tables
    )
    if not available:
        return unavailable_result(str(unavailable_reason), base)
    required_tables = procedure_tables
    subject = base["subject"]
    peer_codes = [subject["corp_code"]] + [peer["corp_code"] for peer in base.get("peers", [])]
    if not peer_codes:
        return {"error": "no peers"}
    stmt = text("""
        SELECT api.corp_code, c.corp_name, api.kam_topic, api.method,
               api.procedure_type,
               COUNT(*) AS cnt
        FROM audit_procedure_items api
        JOIN companies c ON c.corp_code=api.corp_code
        WHERE api.bsns_year=:year AND api.corp_code IN :corp_codes
        GROUP BY api.corp_code, c.corp_name, api.kam_topic, api.method,
                 api.procedure_type
        ORDER BY c.corp_name, api.kam_topic, api.method, api.procedure_type
    """).bindparams(bindparam("corp_codes", expanding=True))
    coverage_stmt = text("""
        SELECT
            COUNT(*) AS kam_items,
            COALESCE(SUM(
                CASE WHEN ki.quality_status='full_body' THEN 1 ELSE 0 END
            ), 0) AS full_body_kams,
            COALESCE(SUM(
                CASE
                    WHEN ki.quality_status='full_body'
                     AND EXISTS (
                        SELECT 1
                        FROM audit_procedure_items api
                        WHERE api.kam_item_id=ki.id
                     )
                    THEN 1 ELSE 0
                END
            ), 0) AS full_body_kams_with_procedures,
            COUNT(DISTINCT CASE
                WHEN ki.quality_status='full_body'
                THEN ki.rcept_no || '|' || ki.source_type
            END) AS full_body_kam_receipts,
            COUNT(DISTINCT CASE
                WHEN ki.quality_status='full_body'
                 AND EXISTS (
                    SELECT 1
                    FROM audit_procedure_items api
                    WHERE api.kam_item_id=ki.id
                 )
                THEN ki.rcept_no || '|' || ki.source_type
            END) AS full_body_kam_receipts_with_procedures,
            COUNT(DISTINCT CASE
                WHEN ki.quality_status='summary_only'
                THEN ki.rcept_no || '|' || ki.source_type
            END) AS summary_only,
            COUNT(DISTINCT CASE
                WHEN ki.quality_status='missing'
                THEN ki.rcept_no || '|' || ki.source_type
            END) AS missing,
            COUNT(DISTINCT CASE
                WHEN ki.quality_status='error'
                THEN ki.rcept_no || '|' || ki.source_type
            END) AS error
        FROM kam_items ki
        WHERE ki.bsns_year=:year AND ki.corp_code IN :corp_codes
    """).bindparams(bindparam("corp_codes", expanding=True))
    with procedure_read_engine(required_tables) as read_engine:
        with read_engine.connect() as conn:
            query_params = {"year": year, "corp_codes": peer_codes}
            rows = [
                dict(r)
                for r in conn.execute(stmt, query_params).mappings().all()
            ]
            coverage_row = dict(
                conn.execute(
                    coverage_stmt,
                    query_params,
                ).mappings().one()
            )
    row_source = "audit_procedure_items"
    if not rows:
        with procedure_read_engine(required_tables) as read_engine:
            with read_engine.connect() as conn:
                evidence_rows = full_body_kam_procedure_rows(
                    corp_codes=peer_codes,
                    year=year,
                    limit=max(500, len(peer_codes) * 10),
                    _connection=conn,
                )
        aggregated: dict[tuple[str, str, str, str, str | None], int] = {}
        names = {row["corp_code"]: row.get("corp_name") for row in evidence_rows}
        for row in evidence_rows:
            key = (
                row["corp_code"],
                row.get("corp_name") or names.get(row["corp_code"]) or "",
                row.get("kam_topic") or "unknown",
                row.get("procedure_type") or "other",
                row.get("method"),
            )
            aggregated[key] = aggregated.get(key, 0) + 1
        rows = [
            {
                "corp_code": cc,
                "corp_name": name,
                "kam_topic": topic,
                "procedure_type": ptype,
                "method": method,
                "cnt": cnt,
            }
            for (cc, name, topic, ptype, method), cnt in aggregated.items()
        ]
        if rows:
            row_source = "kam_items.full_body"

    subject_counts: dict[str, int] = {}
    peer_type_counts: dict[str, int] = {}
    peer_topic_counts: dict[str, int] = {}
    subject_method_counts: dict[str, int] = {}
    peer_method_counts: dict[str, int] = {}
    companies_with_procedures: set[str] = set()
    for row in rows:
        companies_with_procedures.add(row["corp_code"])
        key = row["procedure_type"] or "other"
        topic = row["kam_topic"] or "unknown"
        if row["corp_code"] == subject["corp_code"]:
            subject_counts[key] = subject_counts.get(key, 0) + int(row["cnt"] or 0)
            if row.get("method"):
                method = str(row["method"])
                subject_method_counts[method] = (
                    subject_method_counts.get(method, 0)
                    + int(row["cnt"] or 0)
                )
        else:
            peer_type_counts[key] = peer_type_counts.get(key, 0) + int(row["cnt"] or 0)
            peer_topic_counts[topic] = peer_topic_counts.get(topic, 0) + int(row["cnt"] or 0)
            if row.get("method"):
                method = str(row["method"])
                peer_method_counts[method] = (
                    peer_method_counts.get(method, 0)
                    + int(row["cnt"] or 0)
                )

    full_body_receipts = int(
        coverage_row.get("full_body_kam_receipts") or 0
    )
    receipt_with_procedures = int(
        coverage_row.get("full_body_kam_receipts_with_procedures") or 0
    )

    return _clean_dict({
        "subject": subject,
        "year": year,
        "peer_count": len(base.get("peers", [])),
        "companies_with_procedures": len(companies_with_procedures),
        "subject_procedure_type_counts": subject_counts,
        "peer_procedure_type_counts": peer_type_counts,
        "subject_method_counts": subject_method_counts,
        "peer_method_counts": peer_method_counts,
        "peer_kam_topic_counts": peer_topic_counts,
        "coverage": {
            "denominator_full_body_kam_receipts": full_body_receipts,
            "full_body_kam_receipts_with_procedures": (
                receipt_with_procedures
            ),
            "rate": (
                round(
                    receipt_with_procedures * 100.0 / full_body_receipts,
                    1,
                )
                if full_body_receipts
                else 0.0
            ),
            "quality_gaps": {
                "summary_only": int(
                    coverage_row.get("summary_only") or 0
                ),
                "missing": int(coverage_row.get("missing") or 0),
                "error": int(coverage_row.get("error") or 0),
            },
        },
        "selection_policy": base.get("selection_policy"),
        "data_quality": {
            "status": "usable" if rows else "missing",
            "source": row_source,
            "coverage_note": (
                "The coverage denominator is full_body KAM receipts only. "
                "Summary-only, missing, and error KAMs are separate gaps."
            ),
        },
    })


def estimate_audit_hours_proxy(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
    _peer_group: dict | None = None,
) -> dict:
    """Estimate public-data audit complexity proxy for planning discussion."""
    peer_group = _peer_group if _peer_group is not None else select_peer_group(
        company=company, peer_limit=peer_limit, fs_strategy=fs_strategy, year=year
    )
    if "error" in peer_group:
        return peer_group
    fee_pack = compare_peer_audit_fees(
        company=company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy,
        _peer_group=peer_group,
    )
    if "error" in fee_pack:
        return fee_pack
    risk_pack = compare_peer_risk_profile(
        company=company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy,
        _peer_group=peer_group,
    )

    subject_metrics = fee_pack.get("subject_metrics") or {}
    risk_metrics = risk_pack.get("subject_metrics") or {}
    benchmarks = fee_pack.get("benchmarks") or {}
    scale_row = next(
        (
            row
            for row in fee_pack.get("subject_scale_history") or []
            if isinstance(row, dict) and row.get("year") == year
        ),
        {},
    )
    audit_receipt = str(
        scale_row.get("audit_source_rcept_no") or ""
    ).strip()
    financial_source = (
        scale_row.get("financial_source")
        if isinstance(scale_row.get("financial_source"), dict)
        else {}
    )
    financial_receipt = str(
        financial_source.get("rcept_no")
        or scale_row.get("financial_source_rcept_no")
        or ""
    ).strip()

    input_sources: list[dict] = []
    for receipt, section_title in (
        (audit_receipt, "감사보수 및 감사시간"),
        (financial_receipt, "재무제표"),
    ):
        if not (len(receipt) == 14 and receipt.isdigit()):
            continue
        input_sources.append({
            "source_label": "DART 사업보고서",
            "source_url": (
                "https://dart.fss.or.kr/dsaf001/main.do?"
                f"rcpNo={receipt}"
            ),
            "rcept_no": receipt,
            "section_title": section_title,
        })
    input_sources = list({
        source["rcept_no"]: source
        for source in input_sources
    }.values())

    drivers = []
    score = 0

    total_assets = subject_metrics.get("total_assets")
    if total_assets:
        score += 20
        drivers.append({
            "driver": "size",
            "signal": "total_assets_available",
            "points": 20,
            "score_after": score,
        })

    audit_hours = subject_metrics.get("audit_hours")
    if audit_hours:
        pctl = (benchmarks.get("audit_hours") or {}).get("subject_percentile")
        if pctl is not None and pctl >= 75:
            points = 25
            level = "high_vs_peers"
        elif pctl is not None and pctl <= 25:
            points = 5
            level = "low_vs_peers"
        else:
            points = 15
            level = "mid_vs_peers"
        score += points
        drivers.append({
            "driver": "audit_hours",
            "signal": level,
            "points": points,
            "score_after": score,
        })

    if risk_metrics.get("op_cf_divergence_flag"):
        score += 15
        drivers.append({
            "driver": "cashflow_divergence",
            "signal": "flagged",
            "points": 15,
            "score_after": score,
        })
    if risk_metrics.get("going_concern_flag"):
        score += 20
        drivers.append({
            "driver": "going_concern",
            "signal": "flagged",
            "points": 20,
            "score_after": score,
        })
    if risk_metrics.get("beneish_m_score") is not None and risk_metrics.get("beneish_m_score") > -1.78:
        score += 10
        drivers.append({
            "driver": "beneish_m_score",
            "signal": "above_screening_threshold",
            "points": 10,
            "score_after": score,
        })

    complexity_score = min(score, 100)
    if complexity_score >= 70:
        band = "high"
    elif complexity_score >= 40:
        band = "medium"
    else:
        band = "low"

    limitations = [
        "표준감사시간 산정 결과가 아니라 공개자료 기반 감사복잡도 프록시입니다.",
        "감사계획 논의와 동종기업 비교를 위한 스크리닝 자료로만 사용해야 합니다.",
    ]
    if not audit_receipt:
        limitations.append(
            "감사보수·감사시간 입력의 공시 접수번호를 확인하지 못했습니다."
        )
    if not financial_receipt:
        limitations.append(
            "총자산 입력의 공시 접수번호를 확인하지 못했습니다."
        )

    return _clean_dict({
        "subject": fee_pack["subject"],
        "year": year,
        "peer_count": fee_pack.get("peer_count"),
        "complexity_score": complexity_score,
        "complexity_band": band,
        "drivers": drivers,
        "peer_benchmarks": {
            "audit_hours": benchmarks.get("audit_hours"),
            "audit_fee_to_assets_bps": benchmarks.get("audit_fee_to_assets_bps"),
            "audit_fee_per_hour_m": benchmarks.get("audit_fee_per_hour_m"),
        },
        "data_quality": {
            "audit_fee_metrics": fee_pack.get("data_quality"),
            "risk_metrics": risk_pack.get("data_quality"),
        },
        "subject_metrics": {
            "audit_hours": audit_hours,
            "audit_fee_m": subject_metrics.get("audit_fee_m"),
            "total_assets": total_assets,
            "audit_source_rcept_no": (
                audit_receipt
                if len(audit_receipt) == 14 and audit_receipt.isdigit()
                else None
            ),
            "financial_source_rcept_no": (
                financial_receipt
                if len(financial_receipt) == 14
                and financial_receipt.isdigit()
                else None
            ),
            "op_cf_divergence_flag": risk_metrics.get("op_cf_divergence_flag"),
            "going_concern_flag": risk_metrics.get("going_concern_flag"),
            "beneish_m_score": risk_metrics.get("beneish_m_score"),
        },
        "confirmed_facts": (
            [{
                "statement": (
                    f"{year}년 공개 감사보수·감사시간 및 재무 규모 입력을 "
                    "공시 접수번호와 연결했습니다."
                ),
                "sources": input_sources,
            }]
            if input_sources
            else []
        ),
        "selection_policy": fee_pack.get("selection_policy"),
        "limitations": limitations,
    })


def build_audit_acceptance_pack(
    company: str,
    year: int = 2025,
    peer_limit: int = 30,
    fs_strategy: str = "auto",
) -> dict:
    """Build a compact DART evidence pack for acceptance/continuance screening."""
    peer_group = select_peer_group(
        company=company, peer_limit=peer_limit, fs_strategy=fs_strategy, year=year
    )
    if "error" in peer_group:
        return peer_group
    fee_pack = compare_peer_audit_fees(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy, _peer_group=peer_group)
    risk_pack = compare_peer_risk_profile(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy, _peer_group=peer_group)
    hours_pack = estimate_audit_hours_proxy(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy, _peer_group=peer_group)
    policy_pack = compare_peer_accounting_policies(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy, _peer_group=peer_group)
    kam_pack = compare_peer_kam_topics(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy, _peer_group=peer_group)
    matter_pack = compare_peer_audit_report_matters(company, year=year, peer_limit=peer_limit, fs_strategy=fs_strategy, _peer_group=peer_group)

    acceptance_signals = []
    subject_fee = fee_pack.get("subject_metrics") or {}
    if subject_fee.get("nas_ratio") is not None and subject_fee.get("nas_ratio") > 1.0:
        acceptance_signals.append({
            "area": "independence",
            "severity": "review",
            "signal": "non_audit_fee_exceeds_audit_fee",
        })
    risk_subject = risk_pack.get("subject_metrics") or {}
    if risk_subject.get("going_concern_flag"):
        acceptance_signals.append({
            "area": "going_concern",
            "severity": "review",
            "signal": "loss_based_going_concern_flag",
        })
    if risk_subject.get("op_cf_divergence_flag"):
        acceptance_signals.append({
            "area": "cashflow_quality",
            "severity": "review",
            "signal": "positive_operating_profit_negative_operating_cashflow",
        })
    if hours_pack.get("complexity_band") == "high":
        acceptance_signals.append({
            "area": "audit_effort",
            "severity": "review",
            "signal": "high_public_data_complexity_proxy",
        })
    matter_counts = matter_pack.get("matter_counts") or {}
    if (matter_counts.get("emphasis") or {}).get("subject_count"):
        acceptance_signals.append({
            "area": "audit_report_matters",
            "severity": "review",
            "signal": "audit_report_emphasis_paragraph_present",
        })
    if (matter_counts.get("going_concern") or {}).get("subject_count"):
        acceptance_signals.append({
            "area": "going_concern",
            "severity": "review",
            "signal": "audit_report_going_concern_paragraph_present",
        })
    if (matter_counts.get("other_matter") or {}).get("subject_count"):
        acceptance_signals.append({
            "area": "audit_report_matters",
            "severity": "info",
            "signal": "audit_report_other_matter_paragraph_present",
        })

    policy_peer_count = policy_pack.get("peer_count") or 0
    peers_with_policy = policy_pack.get("peers_with_policy") or 0
    policy_coverage_pct = (
        round(100.0 * peers_with_policy / policy_peer_count, 1)
        if policy_peer_count else 0.0
    )
    kam_events = (kam_pack.get("audit_report_events") or {}).get("total_events") or 0
    kam_section_quality = kam_pack.get("audit_report_sections") or {}
    kam_body_count = kam_section_quality.get("kam_body_count") or 0
    kam_reason_coverage = kam_section_quality.get("kam_reason_coverage") or {}
    kam_procedure_coverage = kam_section_quality.get("kam_procedure_coverage") or {}
    data_quality = {
        "subject_scale_history": (
            fee_pack.get("data_quality") or {}
        ).get("subject_scale_history") or {
            "status": "missing",
            "requested_years": [year, year - 1, year - 2],
            "covered_years": [],
            "complete_years": [],
            "missing_by_year": {},
        },
        "policy_cache": {
            "subject_policy_count": policy_pack.get("subject_policy_count"),
            "peers_with_policy": peers_with_policy,
            "peer_count": policy_peer_count,
            "peer_policy_coverage_pct": policy_coverage_pct,
            "status": (policy_pack.get("data_quality") or {}).get(
                "status",
                "limited" if policy_coverage_pct < 50.0 else "usable",
            ),
            "coverage_note": policy_pack.get("coverage_note"),
        },
        "audit_report_events": {
            "total_events": kam_events,
            "status": "limited" if kam_events == 0 else "usable",
        },
        "kam_body": {
            "kam_body_count": kam_body_count,
            "peer_companies_with_sections": kam_section_quality.get("peer_companies_with_sections"),
            "subject_section_count": kam_section_quality.get("subject_section_count"),
            "kam_reason_coverage": kam_reason_coverage,
            "kam_procedure_coverage": kam_procedure_coverage,
            "source": kam_section_quality.get("source"),
            "available_subject_kam_years": (kam_pack.get("data_quality") or {}).get("available_subject_kam_years"),
            "status": (
                "not_persisted"
                if not kam_body_count
                else "subject_missing"
                if not (kam_section_quality.get("subject_section_count") or 0)
                else "subject_only"
                if not (kam_section_quality.get("peer_companies_with_sections") or 0)
                else "usable"
            ),
        },
        "audit_report_matters": {
            "status": (matter_pack.get("data_quality") or {}).get("status"),
            "subject_section_count": (matter_pack.get("data_quality") or {}).get("subject_section_count"),
            "peer_companies_with_sections": (matter_pack.get("data_quality") or {}).get("peer_companies_with_sections"),
            "matter_counts": matter_counts,
        },
    }

    if data_quality["policy_cache"]["status"] == "limited":
        acceptance_signals.append({
            "area": "data_coverage",
            "severity": "info",
            "signal": "low_peer_accounting_policy_cache_coverage",
        })
    if data_quality["kam_body"]["status"] == "not_persisted":
        acceptance_signals.append({
            "area": "data_coverage",
            "severity": "info",
            "signal": "kam_body_not_persisted",
        })
    elif data_quality["kam_body"]["status"] == "subject_only":
        acceptance_signals.append({
            "area": "data_coverage",
            "severity": "info",
            "signal": "peer_kam_body_coverage_missing",
        })
    elif data_quality["kam_body"]["status"] == "subject_missing":
        acceptance_signals.append({
            "area": "data_coverage",
            "severity": "info",
            "signal": "subject_kam_body_missing_for_requested_year",
        })

    recommended_review_areas = sorted({
        item["area"] for item in acceptance_signals
    } | {
        "peer_group_basis",
        "audit_fee_and_hours",
        "accounting_policy_coverage",
        "audit_report_events",
        "audit_report_matters",
    })

    return _clean_dict({
        "subject": peer_group["subject"],
        "year": year,
        "scope": "external_dart_evidence_pack",
        "acceptance_signals": acceptance_signals,
        "data_quality": data_quality,
        "recommended_review_areas": recommended_review_areas,
        "peer_group": {
            "peer_count": peer_group.get("peer_count"),
            "confidence": peer_group.get("confidence"),
            "selection_policy": peer_group.get("selection_policy"),
            "sample_peers": peer_group.get("peers", [])[:10],
        },
        "audit_fee_summary": {
            "subject_metrics": subject_fee,
            "benchmarks": fee_pack.get("benchmarks"),
        },
        "subject_scale_history": fee_pack.get("subject_scale_history") or [],
        "risk_summary": {
            "subject_metrics": risk_subject,
            "benchmarks": risk_pack.get("benchmarks"),
            "disclosure_event_counts": risk_pack.get("disclosure_event_counts"),
        },
        "audit_hours_proxy": {
            "complexity_score": hours_pack.get("complexity_score"),
            "complexity_band": hours_pack.get("complexity_band"),
            "drivers": hours_pack.get("drivers"),
        },
        "policy_summary": {
            "subject_policy_count": policy_pack.get("subject_policy_count"),
            "peers_with_policy": policy_pack.get("peers_with_policy"),
            "coverage_note": policy_pack.get("coverage_note"),
        },
        "kam_summary": {
            "audit_report_events": kam_pack.get("audit_report_events"),
            "kam_topics": kam_pack.get("kam_topics"),
            "audit_report_sections": kam_pack.get("audit_report_sections"),
            "subject_sections": (kam_pack.get("subject_sections") or [])[:3],
            "subject_business_report_kam_summary": (
                kam_pack.get("subject_business_report_kam_summary") or []
            )[:3],
            "limitations": kam_pack.get("limitations"),
        },
        "audit_report_matter_summary": {
            "matter_counts": matter_counts,
            "subject_matters": (matter_pack.get("subject_matters") or [])[:5],
            "data_quality": matter_pack.get("data_quality"),
            "limitations": matter_pack.get("limitations"),
        },
        "limitations": [
            "This pack supports acceptance/continuance screening only.",
            "It does not replace firm methodology, independence checks, client inquiry, or workpaper judgment.",
        ],
    })


_BIG4_KEYWORDS = ("삼일", "삼정", "한영", "안진", "PwC", "KPMG", "EY", "Deloitte")


def _is_big4(name: Optional[str]) -> bool:
    """auditor_nm 내 Big4 키워드 포함 여부."""
    if not name:
        return False
    return any(k in name for k in _BIG4_KEYWORDS)


def _empty_audit_landscape(
    subject_meta: dict,
    pr: PeerResolution,
    fs_div: str,
    note: str,
) -> dict:
    """auditors 데이터 부족 시 graceful degradation shape."""
    return {
        "subject": subject_meta,
        "sector_group": pr.sector_group.value,
        "matched_prefix_len": pr.matched_prefix_len,
        "n_peers": pr.n_peers,
        "confidence": pr.confidence,
        "excluded_categories": pr.excluded_categories,
        "fs_div": fs_div,
        "latest_year": None,
        "years_window": None,
        "auditor_market_share": [],
        "big4_share_pct": None,
        "non_qualified_opinion_rate_pct": None,
        "avg_tenure_years": None,
        "subject_auditor": None,
        "note": note,
    }


def get_industry_audit_landscape(
    company: Optional[str] = None,
    induty_code: Optional[str] = None,
    years_back: int = 5,
    fs_div: str = "CFS",
    prefix_len_start: int = 3,
    top_n: int = 10,
    exclude_other_sectors: bool = True,
) -> dict:
    """업종 내 감사 시장 분석.

    Returns: subject 정보 + 감사인 시장점유율(회사수·자산가중) + Big4 share +
    비적정 의견 발생율(5년 누적) + 평균 tenure + subject 본인 감사인.

    Auditors 테이블 데이터가 부족하면 latest_year=None과 함께 자료 부족 note.
    """
    # 1. Subject corp_code 해석
    if company:
        corp_code = resolve_corp_code(company)
        if corp_code is None:
            return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}
    elif induty_code:
        prefix = str(induty_code).strip()
        if not prefix:
            return {"error": "induty_code가 비어 있습니다."}
        with _engine_module.engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT corp_code FROM companies "
                    "WHERE substr(induty_code, 1, :plen) = :prefix "
                    "  AND stock_code IS NOT NULL "
                    "ORDER BY corp_code LIMIT 1"
                ),
                {"plen": len(prefix), "prefix": prefix},
            ).first()
        if row is None:
            return {"error": f"induty_code prefix '{prefix}'에 해당하는 기업이 없습니다."}
        corp_code = row[0]
    else:
        return {"error": "company 또는 induty_code 중 하나 필요"}

    # 2. Subject 메타
    with _engine_module.engine.connect() as conn:
        subj_row = conn.execute(
            text("SELECT corp_name, induty_code FROM companies WHERE corp_code = :cc"),
            {"cc": corp_code},
        ).first()
    if subj_row is None:
        return {"error": f"corp_code '{corp_code}' 미등록"}
    subject_meta = {
        "corp_code": corp_code,
        "corp_name": subj_row[0],
        "induty_code": subj_row[1],
    }

    # 3. Peer 풀
    pr = resolve_peers(
        corp_code=corp_code,
        prefix_len_start=prefix_len_start,
        min_n=5,
        exclude_other_sectors=exclude_other_sectors,
        size_bucket_decade=None,
        fs_div=fs_div,
    )

    # 4. peer_set = peer + subject
    peer_set = list(pr.peer_corp_codes) + [corp_code]

    # 5. latest_year (auditors)
    with _engine_module.engine.connect() as conn:
        stmt = text(
            "SELECT MAX(bsns_year) FROM auditors "
            "WHERE corp_code IN :ccs AND fs_div = :fs"
        ).bindparams(bindparam("ccs", expanding=True))
        latest = conn.execute(stmt, {"ccs": peer_set, "fs": fs_div}).scalar()

    if latest is None:
        return _empty_audit_landscape(
            subject_meta,
            pr,
            fs_div,
            note=(
                (pr.note + " · " if pr.note else "")
                + "auditors 데이터 부족 (collect-auditors 미실행 또는 데이터 없음)"
            ),
        )
    latest_year = int(latest)

    # 6. years window
    y1 = latest_year
    y0 = latest_year - years_back + 1

    # 7. Auditor market share (latest year)
    with _engine_module.engine.connect() as conn:
        # 7a. 회사수 기반 share + asset_weighted share (한 쿼리)
        share_stmt = text(
            """
            SELECT a.auditor_nm,
                   COUNT(DISTINCT a.corp_code) AS company_count,
                   COALESCE(SUM(f.total_assets), 0) AS asset_sum
            FROM auditors a
            LEFT JOIN financials f
              ON f.corp_code = a.corp_code
             AND f.year = a.bsns_year
             AND f.fs_div = a.fs_div
             AND f.quarter = 4
            WHERE a.corp_code IN :ccs
              AND a.bsns_year = :y
              AND a.fs_div = :fs
            GROUP BY a.auditor_nm
            ORDER BY company_count DESC
            LIMIT :topn
            """
        ).bindparams(bindparam("ccs", expanding=True))
        share_rows = conn.execute(
            share_stmt,
            {"ccs": peer_set, "y": latest_year, "fs": fs_div, "topn": top_n},
        ).all()

        # 7b. 전체 합산 (share_pct 계산 분모)
        total_stmt = text(
            """
            SELECT COUNT(DISTINCT a.corp_code) AS company_total,
                   COALESCE(SUM(f.total_assets), 0) AS asset_total
            FROM auditors a
            LEFT JOIN financials f
              ON f.corp_code = a.corp_code
             AND f.year = a.bsns_year
             AND f.fs_div = a.fs_div
             AND f.quarter = 4
            WHERE a.corp_code IN :ccs
              AND a.bsns_year = :y
              AND a.fs_div = :fs
            """
        ).bindparams(bindparam("ccs", expanding=True))
        total_row = conn.execute(
            total_stmt,
            {"ccs": peer_set, "y": latest_year, "fs": fs_div},
        ).first()
    company_total = int(total_row[0]) if total_row and total_row[0] else 0
    asset_total = float(total_row[1]) if total_row and total_row[1] else 0.0

    auditor_market_share = []
    for nm, cnt, asum in share_rows:
        cnt_i = int(cnt or 0)
        asum_f = float(asum or 0)
        comp_share = (
            round(100.0 * cnt_i / company_total, 2) if company_total > 0 else None
        )
        asset_share = (
            round(100.0 * asum_f / asset_total, 2) if asset_total > 0 else None
        )
        auditor_market_share.append(
            {
                "auditor_nm": nm,
                "company_count": cnt_i,
                "company_share_pct": comp_share,
                "asset_weighted_share_pct": asset_share,
                "is_big4": _is_big4(nm),
            }
        )

    # 8. Big4 share (latest year)
    with _engine_module.engine.connect() as conn:
        big4_stmt = text(
            "SELECT auditor_nm, COUNT(DISTINCT corp_code) FROM auditors "
            "WHERE corp_code IN :ccs AND bsns_year = :y AND fs_div = :fs "
            "GROUP BY auditor_nm"
        ).bindparams(bindparam("ccs", expanding=True))
        all_rows = conn.execute(
            big4_stmt, {"ccs": peer_set, "y": latest_year, "fs": fs_div}
        ).all()
    big4_corps = sum(int(c or 0) for nm, c in all_rows if _is_big4(nm))
    any_corps = sum(int(c or 0) for _, c in all_rows)
    big4_share_pct = (
        round(100.0 * big4_corps / any_corps, 2) if any_corps > 0 else None
    )

    # 9. Non-qualified opinion rate (years window)
    with _engine_module.engine.connect() as conn:
        op_stmt = text(
            "SELECT audit_opinion, COUNT(*) FROM auditors "
            "WHERE corp_code IN :ccs "
            "  AND bsns_year BETWEEN :y0 AND :y1 "
            "  AND fs_div = :fs "
            "GROUP BY audit_opinion"
        ).bindparams(bindparam("ccs", expanding=True))
        op_rows = conn.execute(
            op_stmt,
            {"ccs": peer_set, "y0": y0, "y1": y1, "fs": fs_div},
        ).all()
    total_op = sum(int(c or 0) for _, c in op_rows)
    non_qual = sum(
        int(c or 0)
        for op, c in op_rows
        if op is not None and str(op).strip() != "적정"
    )
    non_qualified_opinion_rate_pct = (
        round(100.0 * non_qual / total_op, 2) if total_op > 0 else None
    )

    # 10. Average tenure (latest year)
    with _engine_module.engine.connect() as conn:
        ten_stmt = text(
            "SELECT AVG(consecutive_years) FROM auditors "
            "WHERE corp_code IN :ccs "
            "  AND bsns_year = :y AND fs_div = :fs "
            "  AND consecutive_years IS NOT NULL"
        ).bindparams(bindparam("ccs", expanding=True))
        avg_ten = conn.execute(
            ten_stmt, {"ccs": peer_set, "y": latest_year, "fs": fs_div}
        ).scalar()
    avg_tenure_years = round(float(avg_ten), 2) if avg_ten is not None else None

    # 11. Subject auditor (latest year)
    with _engine_module.engine.connect() as conn:
        subj_aud = conn.execute(
            text(
                "SELECT auditor_nm, audit_opinion, consecutive_years "
                "FROM auditors "
                "WHERE corp_code = :cc AND bsns_year = :y AND fs_div = :fs"
            ),
            {"cc": corp_code, "y": latest_year, "fs": fs_div},
        ).first()
    if subj_aud is not None:
        subject_auditor = {
            "auditor_nm": subj_aud[0],
            "audit_opinion": subj_aud[1],
            "consecutive_years": (
                int(subj_aud[2]) if subj_aud[2] is not None else None
            ),
            "is_big4": _is_big4(subj_aud[0]),
        }
    else:
        subject_auditor = None

    return {
        "subject": subject_meta,
        "sector_group": pr.sector_group.value,
        "matched_prefix_len": pr.matched_prefix_len,
        "n_peers": pr.n_peers,
        "confidence": pr.confidence,
        "excluded_categories": pr.excluded_categories,
        "fs_div": fs_div,
        "latest_year": latest_year,
        "years_window": [y0, y1],
        "auditor_market_share": auditor_market_share,
        "big4_share_pct": big4_share_pct,
        "non_qualified_opinion_rate_pct": non_qualified_opinion_rate_pct,
        "avg_tenure_years": avg_tenure_years,
        "subject_auditor": subject_auditor,
        "note": pr.note,
    }
