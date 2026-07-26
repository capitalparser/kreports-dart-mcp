"""
Peer group 해석 공통 모듈.

감사 관점에서 동종업종 비교를 일관되게 수행하기 위한 함수들을 모은다.

핵심 규칙:
- Adaptive ladder: KSIC 3자리 → n<5면 2자리로 fallback
- Sector mutual exclusion: 금융(64~66) / 지주(6420) / 부동산(68) / 일반
- Size bucket opt-in: 자산총계 log10 기준 ±decade
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from sqlalchemy import text

from kreports.db.engine import engine


class SectorGroup(str, Enum):
    FINANCIAL = "financial"      # KSIC 64, 65, 66
    HOLDING = "holding"          # KSIC 64201(일반지주회사). 64202+ 미정의 코드는 FINANCIAL로 처리.
    REAL_ESTATE = "real_estate"  # KSIC 68
    GENERAL = "general"          # 그 외 제조·서비스·도소매 등
    UNKNOWN = "unknown"          # induty_code 없음/이상


def classify_sector(induty_code: Optional[str]) -> SectorGroup:
    """KSIC induty_code를 감사 비교 단위의 sector group으로 분류.

    분류 규칙:
    - 지주회사 우선 매칭: KSIC `64201` (일반지주회사) 또는 4자리 입력 `6420`
    - 금융: 2자리가 64, 65, 66
    - 부동산: 2자리가 68
    - 그 외: GENERAL
    - induty_code 없음/비숫자: UNKNOWN
    """
    if not induty_code or not isinstance(induty_code, str):
        return SectorGroup.UNKNOWN
    code = induty_code.strip()
    if not code or not code[:2].isdigit():
        return SectorGroup.UNKNOWN
    # 지주 명시 매칭: KSIC 64201(일반지주회사)만 HOLDING.
    # "6420" 4자리는 카테고리 명목값으로 HOLDING. 64202+ 미정의 642xx는 FINANCIAL.
    if code == "6420" or code.startswith("64201"):
        return SectorGroup.HOLDING
    p2 = code[:2]
    if p2 in {"64", "65", "66"}:
        return SectorGroup.FINANCIAL
    if p2 == "68":
        return SectorGroup.REAL_ESTATE
    return SectorGroup.GENERAL


# 임계값 — 통계 신뢰도
_N_HIGH = 20
_N_MEDIUM = 10
_N_LOW = 5


def resolve_fs_div_for_company(
    corp_code: str,
    year: int | None,
    fs_strategy: str = "auto",
    *,
    read_engine=None,
) -> str:
    strategy = (fs_strategy or "auto").upper()
    if strategy in {"CFS", "OFS"}:
        return strategy
    if strategy != "AUTO":
        return "CFS"
    active_engine = read_engine or engine
    with active_engine.connect() as conn:
        if year is None:
            row = conn.execute(
                text("SELECT MAX(year) FROM financials WHERE corp_code=:cc AND quarter=4"),
                {"cc": corp_code},
            ).first()
            year = row[0] if row and row[0] else None
        if year is None:
            return "CFS"
        cfs = conn.execute(
            text("SELECT 1 FROM financials WHERE corp_code=:cc AND year=:y AND quarter=4 AND fs_div='CFS' LIMIT 1"),
            {"cc": corp_code, "y": year},
        ).first()
        if cfs:
            return "CFS"
        ofs = conn.execute(
            text("SELECT 1 FROM financials WHERE corp_code=:cc AND year=:y AND quarter=4 AND fs_div='OFS' LIMIT 1"),
            {"cc": corp_code, "y": year},
        ).first()
        return "OFS" if ofs else "CFS"


def confidence_band(n: int) -> str:
    """peer 수에 따른 통계 신뢰도 라벨."""
    if n >= _N_HIGH:
        return "high"
    if n >= _N_MEDIUM:
        return "medium"
    if n >= _N_LOW:
        return "low"
    return "insufficient"


@dataclass(frozen=True)
class PeerResolution:
    """resolve_peers의 응답 컨테이너."""
    peer_corp_codes: list[str]
    matched_prefix_len: int
    sector_group: SectorGroup
    n_peers: int
    excluded_categories: list[str] = field(
        default_factory=lambda: ["financial", "holding", "real_estate"]
    )
    size_bucket_applied: Optional[float] = None
    resolved_year: Optional[int] = None  # peer 풀이 산정된 실제 Q4 연도 (subject 기준)
    note: str = ""

    @property
    def confidence(self) -> str:
        return confidence_band(self.n_peers)


def resolve_peers(
    corp_code: str,
    prefix_len_start: int = 3,
    min_n: int = 5,
    exclude_other_sectors: bool = True,
    size_bucket_decade: Optional[float] = None,
    fs_div: str = "CFS",
    year: Optional[int] = None,
    read_engine=None,
) -> PeerResolution:
    """동종업종 비교를 위한 peer corp_code 목록을 해석한다.

    Adaptive ladder: prefix_len_start(기본 3자리)로 매칭 → peer 수가 min_n 미만이면
    2자리로 fallback. subject 본인은 항상 제외. exclude_other_sectors=True인 경우
    subject와 다른 sector group(금융/지주/부동산/일반)은 제외한다.

    size_bucket_decade가 주어지면 subject의 total_assets 대비 log10 거리가 해당 값
    이하인 peer만 남긴다 (예: 1.0 → ±1 decade).

    year=None이면 subject가 보유한 가장 최근 Q4/fs_div 연도를 사용한다.
    """
    active_engine = read_engine or engine
    with active_engine.connect() as conn:
        subject_row = conn.execute(
            text("SELECT induty_code FROM companies WHERE corp_code = :cc"),
            {"cc": corp_code},
        ).first()

        if subject_row is None or not subject_row[0]:
            return PeerResolution(
                peer_corp_codes=[],
                matched_prefix_len=prefix_len_start,
                sector_group=SectorGroup.UNKNOWN,
                n_peers=0,
                note="subject corp_code 미등록 또는 induty_code 없음",
            )

        subject_induty = subject_row[0]
        subject_sector = classify_sector(subject_induty)

        # year 1회 해석 (ladder 양쪽 rung에서 동일하게 사용)
        resolved_year = year
        if resolved_year is None:
            year_row = conn.execute(
                text(
                    "SELECT MAX(year) FROM financials "
                    "WHERE quarter=4 AND fs_div=:fs AND corp_code=:cc"
                ),
                {"fs": fs_div, "cc": corp_code},
            ).first()
            resolved_year = year_row[0] if year_row and year_row[0] else None

        peers: list[str] = []
        matched_plen = prefix_len_start
        for plen in (prefix_len_start, 2):
            peers = _query_peers(
                conn=conn,
                subject_induty=subject_induty,
                subject_corp_code=corp_code,
                subject_sector=subject_sector,
                prefix_len=plen,
                exclude_other_sectors=exclude_other_sectors,
                size_bucket_decade=size_bucket_decade,
                fs_div=fs_div,
                year=resolved_year,
            )
            matched_plen = plen
            if len(peers) >= min_n:
                break

    excluded = (
        [s.value for s in SectorGroup
         if s != subject_sector and s != SectorGroup.UNKNOWN]
        if exclude_other_sectors else []
    )

    return PeerResolution(
        peer_corp_codes=peers,
        matched_prefix_len=matched_plen,
        sector_group=subject_sector,
        n_peers=len(peers),
        excluded_categories=excluded,
        size_bucket_applied=size_bucket_decade,
        resolved_year=resolved_year,
        note=_build_note(
            matched_plen,
            len(peers),
            size_bucket_decade,
            subject_sector=subject_sector,
            prefix_len_start=prefix_len_start,
        ),
    )


def _query_peers(
    *,
    conn,
    subject_induty: str,
    subject_corp_code: str,
    subject_sector: SectorGroup,
    prefix_len: int,
    exclude_other_sectors: bool,
    size_bucket_decade: Optional[float],
    fs_div: str,
    year: Optional[int],
) -> list[str]:
    prefix = subject_induty[:prefix_len]

    if year is None:
        return []

    subject_assets = None
    if size_bucket_decade is not None:
        ta_row = conn.execute(
            text(
                "SELECT total_assets FROM financials "
                "WHERE corp_code=:cc AND year=:y AND quarter=4 AND fs_div=:fs"
            ),
            {"cc": subject_corp_code, "y": year, "fs": fs_div},
        ).first()
        subject_assets = ta_row[0] if ta_row and ta_row[0] else None

    rows = conn.execute(
        text(
            "SELECT DISTINCT c.corp_code, c.induty_code, f.total_assets "
            "FROM companies c "
            "JOIN financials f ON f.corp_code = c.corp_code "
            "WHERE substr(c.induty_code,1,:plen) = :prefix "
            "  AND c.corp_code != :subject_cc "
            "  AND f.year = :year AND f.quarter = 4 AND f.fs_div = :fs"
        ),
        {
            "plen": prefix_len,
            "prefix": prefix,
            "subject_cc": subject_corp_code,
            "year": year,
            "fs": fs_div,
        },
    ).all()

    out: list[str] = []
    for cc, induty, ta in rows:
        if exclude_other_sectors:
            if classify_sector(induty) != subject_sector:
                continue
        if size_bucket_decade is not None:
            if not (subject_assets and subject_assets > 0 and ta and ta > 0):
                continue  # 음수/0 자산은 size_bucket 비교 불가
            if abs(math.log10(ta) - math.log10(subject_assets)) > size_bucket_decade:
                continue
        out.append(cc)
    return out


def _build_note(
    matched_plen: int,
    n: int,
    size_bucket: Optional[float],
    *,
    subject_sector: SectorGroup = SectorGroup.UNKNOWN,
    prefix_len_start: int = 3,
) -> str:
    # n_peers는 의도적으로 노출하지 않음. 외부 호출자(api.py)가 NULL 필터 후 n을
    # 별도로 갖고 있어 두 개의 count가 동시에 보이면 혼란을 준다. 여기는 resolution
    # 속성(sector·prefix·fallback·size_bucket·warning)만 기록한다.
    parts = [
        f"sector={subject_sector.value}",
        f"KSIC prefix_len={matched_plen} 매칭",
    ]
    if prefix_len_start > matched_plen:
        parts.append(f"⚠ p{prefix_len_start}→p{matched_plen} fallback")
    if size_bucket is not None:
        parts.append(f"size_bucket=±{size_bucket} decade")
    if n < _N_LOW:
        parts.append("⚠ peer 수가 부족합니다 (n<5 → P25/P75 신뢰도 낮음)")
    return " · ".join(parts)
