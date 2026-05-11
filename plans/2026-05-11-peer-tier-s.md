# Peer 비교 Tier S 구현 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** kreports_dart_mcp에 감사인 관점의 peer 비교 도구 2개(`compare_to_industry_multi`, `get_industry_audit_landscape`)를 추가하고, 모든 peer 해석을 일관되게 처리하는 `kreports/analysis/peer.py` 공통 모듈을 도입한다.

**Architecture:** Peer 해석 로직을 `peer.py`로 분리해 (a) Adaptive prefix ladder(p3 → n<5면 p2), (b) Sector mutual exclusion(금융 64–66 / 지주 6420 / 부동산 68 / 일반), (c) Size bucket opt-in을 단일 함수에서 처리한다. 기존 `compare_to_industry`도 이 함수를 사용하도록 리팩터하고, 신규 도구 두 개는 동일 peer set 위에서 다지표·다년도 집계, 감사 시장 분석을 수행한다.

**Tech Stack:** Python 3.12, SQLAlchemy 2 (ORM + raw text() queries), SQLite (kreports.db), pytest, MCP tool schema(JSON), 기존 코드 패턴(`kreports/analysis/api.py`, `kreports/mcp/tools.py`).

---

## File Structure

| 파일 | 역할 | 작업 |
|---|---|---|
| `kreports/analysis/peer.py` | 공통 peer 해석 (sector 분류, ladder, size bucket) | 신규 |
| `kreports/analysis/api.py` | `get_industry_aggregates`를 peer.py 사용으로 리팩터, `compare_to_industry_multi` / `get_industry_audit_landscape` 추가 | 수정 |
| `kreports/mcp/schemas.py` | 신규 tool 2개의 JSON Schema 추가 | 수정 |
| `kreports/mcp/tools.py` | 신규 tool 2개 handler 등록, schemas 연결 | 수정 |
| `tests/test_peer.py` | `peer.py` 유닛 테스트 (sector 분류, ladder, size bucket) | 신규 |
| `tests/test_compare_industry_multi.py` | `compare_to_industry_multi` 통합 테스트 | 신규 |
| `tests/test_audit_landscape.py` | `get_industry_audit_landscape` 통합 테스트 | 신규 |

**Design rules:**
- `peer.py`는 SQL은 직접 작성하되 ORM 모델(`Company`, `Financial`)은 import해 type-safe 키를 사용한다.
- `compare_to_industry`(기존)는 *backward compatible*해야 한다 — 호출자 signature·응답 키 유지, 내부적으로만 peer.py 사용.
- 신규 응답 메타에 항상 `matched_prefix_len`, `n_peers`, `sector_group`, `excluded_categories`, `size_bucket_applied`, `confidence`를 노출한다.
- 통합 테스트는 mock 없이 실제 `kreports.db`를 read-only로 사용한다 (이미 collect-all 진행 중이지만 SQLite는 동시 read 허용).

---

## Korean Output Convention

모든 MCP 응답 필드명·errpr 메시지·문서 한국어 유지(`CLAUDE.md §3.1`). 코드 주석은 한국어/영어 혼용 허용. 테스트 함수명·docstring은 영어.

---

## Task 1: peer.py — 모듈 골격 + `classify_sector`

**Files:**
- Create: `kreports/analysis/peer.py`
- Create: `tests/test_peer.py`

- [ ] **Step 1: Write failing test for classify_sector**

```python
# tests/test_peer.py
from kreports.analysis.peer import classify_sector, SectorGroup


def test_classify_sector_financial():
    assert classify_sector("64205") == SectorGroup.FINANCIAL
    assert classify_sector("65110") == SectorGroup.FINANCIAL
    assert classify_sector("66111") == SectorGroup.FINANCIAL


def test_classify_sector_holding():
    assert classify_sector("64201") == SectorGroup.HOLDING  # 일반지주
    assert classify_sector("6420") == SectorGroup.HOLDING   # 4자리 입력도


def test_classify_sector_real_estate():
    assert classify_sector("68111") == SectorGroup.REAL_ESTATE
    assert classify_sector("68") == SectorGroup.REAL_ESTATE


def test_classify_sector_general():
    assert classify_sector("26411") == SectorGroup.GENERAL  # 반도체
    assert classify_sector("20111") == SectorGroup.GENERAL  # 화학
    assert classify_sector("29100") == SectorGroup.GENERAL  # 기계


def test_classify_sector_empty_or_invalid():
    assert classify_sector(None) == SectorGroup.UNKNOWN
    assert classify_sector("") == SectorGroup.UNKNOWN
    assert classify_sector("abc") == SectorGroup.UNKNOWN
```

- [ ] **Step 2: Run test (expect failure: module missing)**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_peer.py -v`
Expected: `ModuleNotFoundError: No module named 'kreports.analysis.peer'`

- [ ] **Step 3: Create peer.py with SectorGroup + classify_sector**

```python
# kreports/analysis/peer.py
"""
Peer group 해석 공통 모듈.

감사 관점에서 동종업종 비교를 일관되게 수행하기 위한 함수들을 모은다.

핵심 규칙:
- Adaptive ladder: KSIC 3자리 → n<5면 2자리로 fallback
- Sector mutual exclusion: 금융(64~66) / 지주(6420) / 부동산(68) / 일반
- Size bucket opt-in: 자산총계 log10 기준 ±decade
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class SectorGroup(str, Enum):
    FINANCIAL = "financial"      # KSIC 64, 65, 66
    HOLDING = "holding"          # KSIC 6420 (일반지주회사)
    REAL_ESTATE = "real_estate"  # KSIC 68
    GENERAL = "general"          # 그 외 제조·서비스·도소매 등
    UNKNOWN = "unknown"          # induty_code 없음/이상


def classify_sector(induty_code: Optional[str]) -> SectorGroup:
    """KSIC induty_code를 감사 비교 단위의 sector group으로 분류."""
    if not induty_code or not isinstance(induty_code, str):
        return SectorGroup.UNKNOWN
    code = induty_code.strip()
    if not code or not code[:2].isdigit():
        return SectorGroup.UNKNOWN
    # 지주회사 우선 매칭 (KSIC 6420 — 4자리 정확 일치)
    if code.startswith("6420"):
        return SectorGroup.HOLDING
    p2 = code[:2]
    if p2 in {"64", "65", "66"}:
        return SectorGroup.FINANCIAL
    if p2 == "68":
        return SectorGroup.REAL_ESTATE
    return SectorGroup.GENERAL
```

- [ ] **Step 4: Run test to confirm passing**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_peer.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/kjun/vault/01_Projects/kreports_dart_mcp
git add kreports/analysis/peer.py tests/test_peer.py
git commit -m "feat(peer): add SectorGroup classifier for KSIC induty_code"
```

---

## Task 2: peer.py — `PeerResolution` dataclass + confidence band

**Files:**
- Modify: `kreports/analysis/peer.py`
- Modify: `tests/test_peer.py`

- [ ] **Step 1: Write failing test for confidence band**

```python
# tests/test_peer.py — append
from kreports.analysis.peer import PeerResolution, confidence_band


def test_confidence_band_thresholds():
    assert confidence_band(50) == "high"
    assert confidence_band(20) == "high"
    assert confidence_band(19) == "medium"
    assert confidence_band(10) == "medium"
    assert confidence_band(9) == "low"
    assert confidence_band(5) == "low"
    assert confidence_band(4) == "insufficient"
    assert confidence_band(0) == "insufficient"


def test_peer_resolution_dataclass_defaults():
    pr = PeerResolution(
        peer_corp_codes=["A", "B", "C"],
        matched_prefix_len=3,
        sector_group=SectorGroup.GENERAL,
        n_peers=3,
    )
    assert pr.confidence == "insufficient"  # n<5
    assert pr.size_bucket_applied is None
    assert pr.excluded_categories == ["financial", "holding", "real_estate"]
```

- [ ] **Step 2: Run test (expect failure)**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_peer.py::test_confidence_band_thresholds tests/test_peer.py::test_peer_resolution_dataclass_defaults -v`
Expected: ImportError

- [ ] **Step 3: Add PeerResolution + confidence_band**

```python
# kreports/analysis/peer.py — append below classify_sector

# 임계값 — 통계 신뢰도
_N_HIGH = 20
_N_MEDIUM = 10
_N_LOW = 5


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
    matched_prefix_len: int           # 실제 매칭 사용된 KSIC prefix 길이 (2 또는 3)
    sector_group: SectorGroup         # subject가 속한 sector
    n_peers: int                      # peer_corp_codes 길이와 동일
    excluded_categories: list[str] = field(  # default: 일반 비교 시 제외되는 다른 sector
        default_factory=lambda: ["financial", "holding", "real_estate"]
    )
    size_bucket_applied: Optional[float] = None  # None=미적용, float=±decade
    note: str = ""

    @property
    def confidence(self) -> str:
        return confidence_band(self.n_peers)
```

- [ ] **Step 4: Run test to confirm passing**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_peer.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/peer.py tests/test_peer.py
git commit -m "feat(peer): add PeerResolution dataclass and confidence_band"
```

---

## Task 3: peer.py — `resolve_peers` (ladder + sector exclusion)

**Files:**
- Modify: `kreports/analysis/peer.py`
- Modify: `tests/test_peer.py`

- [ ] **Step 1: Write failing integration test using real DB**

```python
# tests/test_peer.py — append
import pytest
from kreports.analysis.peer import resolve_peers, SectorGroup


def test_resolve_peers_samsung_uses_p3_general():
    """삼성전자 (264 반도체)는 p3로 매칭되고, sector_group=general."""
    pr = resolve_peers("00126380")  # 삼성전자 corp_code
    assert pr.sector_group == SectorGroup.GENERAL
    assert pr.matched_prefix_len in (2, 3)
    assert pr.n_peers >= 5  # 264는 충분한 peer 존재
    assert "00126380" not in pr.peer_corp_codes  # subject 자기 자신 제외


def test_resolve_peers_excludes_financial_when_subject_general():
    """일반 제조사 peer 풀에 금융업(64~66)이 섞이지 않는다."""
    from sqlalchemy import text
    from kreports.db.engine import engine

    pr = resolve_peers("00126380")
    if not pr.peer_corp_codes:
        pytest.skip("peer 데이터 없음")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT induty_code FROM companies "
                "WHERE corp_code IN :ccs"
            ).bindparams(ccs=tuple(pr.peer_corp_codes))
        ).all()
    for (induty,) in rows:
        if induty:
            assert not induty.startswith(("64", "65", "66", "68")), induty
            assert not induty.startswith("6420")


def test_resolve_peers_unknown_corp_returns_empty():
    pr = resolve_peers("99999999")
    assert pr.n_peers == 0
    assert pr.peer_corp_codes == []
```

- [ ] **Step 2: Run test (expect failure: resolve_peers not defined)**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_peer.py -v`
Expected: ImportError on resolve_peers

- [ ] **Step 3: Implement resolve_peers**

```python
# kreports/analysis/peer.py — append

from sqlalchemy import text
from kreports.db.engine import engine


def resolve_peers(
    corp_code: str,
    prefix_len_start: int = 3,
    min_n: int = 5,
    exclude_other_sectors: bool = True,
    size_bucket_decade: Optional[float] = None,
    fs_div: str = "CFS",
    year: Optional[int] = None,
) -> PeerResolution:
    """
    subject 회사의 동종업종 peer corp_code 리스트를 해석.

    Args:
        corp_code: subject 회사의 corp_code (8자리).
        prefix_len_start: KSIC ladder 시작 자리 수 (기본 3, fallback은 2).
        min_n: ladder fallback 결정 임계값 (peer<min_n이면 fallback).
        exclude_other_sectors: True면 subject와 다른 sector(financial/holding/real_estate/general)는 제외.
        size_bucket_decade: None=미적용. 1.0=자산총계 log10±1.0 decade(=±10배)만, 2.0=±100배.
        fs_div: peer 풀 산정 기준 재무 구분 (CFS/OFS).
        year: peer 풀 산정 기준 사업연도 (Q4). None=최신.

    Returns:
        PeerResolution. 데이터 없으면 n_peers=0의 빈 PeerResolution.
    """
    with engine.connect() as conn:
        subject_row = conn.execute(
            text(
                "SELECT induty_code FROM companies WHERE corp_code = :cc"
            ),
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

        # Adaptive ladder: prefix_len_start → 부족 시 2자리
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
                year=year,
            )
            if len(peers) >= min_n:
                matched_plen = plen
                break
        else:
            # 두 단계 모두 부족 — 마지막(2자리) 결과 반환
            matched_plen = 2

    excluded = (
        [s.value for s in SectorGroup
         if s != subject_sector and s not in (SectorGroup.UNKNOWN,)]
        if exclude_other_sectors else []
    )

    return PeerResolution(
        peer_corp_codes=peers,
        matched_prefix_len=matched_plen,
        sector_group=subject_sector,
        n_peers=len(peers),
        excluded_categories=excluded,
        size_bucket_applied=size_bucket_decade,
        note=_build_note(matched_plen, len(peers), size_bucket_decade),
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

    # 연도: 최신 Q4 보유 연도 (subject 기준)
    if year is None:
        year_row = conn.execute(
            text(
                "SELECT MAX(year) FROM financials "
                "WHERE quarter=4 AND fs_div=:fs AND corp_code=:cc"
            ),
            {"fs": fs_div, "cc": subject_corp_code},
        ).first()
        year = year_row[0] if year_row and year_row[0] else None

    if year is None:
        return []

    # Subject 자산총계 (size bucket용)
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

    # Peer 후보: 같은 prefix + financials Q4 보유 + subject 자기 제외
    rows = conn.execute(
        text(
            "SELECT DISTINCT c.corp_code, c.induty_code, f.total_assets "
            "FROM companies c "
            "JOIN financials f ON f.corp_code = c.corp_code "
            "WHERE substr(c.induty_code,1,:plen) = :prefix "
            "  AND c.corp_code != :subject_cc "
            "  AND f.year = :year AND f.quarter = 4 AND f.fs_div = :fs "
        ),
        {
            "plen": prefix_len,
            "prefix": prefix,
            "subject_cc": subject_corp_code,
            "year": year,
            "fs": fs_div,
        },
    ).all()

    import math
    out: list[str] = []
    for cc, induty, ta in rows:
        # Sector exclusion
        if exclude_other_sectors:
            if classify_sector(induty) != subject_sector:
                continue
        # Size bucket
        if size_bucket_decade is not None and subject_assets and ta and ta > 0:
            try:
                if abs(math.log10(ta) - math.log10(subject_assets)) > size_bucket_decade:
                    continue
            except ValueError:
                continue
        out.append(cc)
    return out


def _build_note(matched_plen: int, n: int, size_bucket: Optional[float]) -> str:
    parts = [f"KSIC prefix_len={matched_plen} 매칭", f"n_peers={n}"]
    if size_bucket is not None:
        parts.append(f"size_bucket=±{size_bucket} decade")
    if n < _N_LOW:
        parts.append("⚠ peer 수가 부족합니다 (n<5 → P25/P75 신뢰도 낮음)")
    return " · ".join(parts)
```

- [ ] **Step 4: Run integration tests**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_peer.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/peer.py tests/test_peer.py
git commit -m "feat(peer): implement resolve_peers with adaptive ladder + sector exclusion"
```

---

## Task 4: peer.py — size bucket integration test

**Files:**
- Modify: `tests/test_peer.py`

- [ ] **Step 1: Write test for size_bucket parameter**

```python
# tests/test_peer.py — append
def test_resolve_peers_size_bucket_reduces_pool():
    """size_bucket_decade=1.0 적용 시 peer 풀이 줄어든다."""
    pr_full = resolve_peers("00126380")
    pr_bucketed = resolve_peers("00126380", size_bucket_decade=1.0)
    # 삼성전자(자산 540조)와 ±10배 = 54조~5400조 안에 들어오는 회사만
    # 한국에 자산 54조+ 회사는 매우 적으므로 감소
    assert pr_bucketed.size_bucket_applied == 1.0
    assert pr_bucketed.n_peers <= pr_full.n_peers


def test_resolve_peers_note_warns_when_low_n():
    """n<5일 때 note에 경고가 포함된다."""
    # 작은 niche 업종을 강제 — corp_code가 알려지지 않은 경우라도
    # note 내용만 확인
    pr_low = resolve_peers("00126380", min_n=999_999)  # 강제로 ladder 끝까지 가게
    assert "peer 수가 부족" in pr_low.note or pr_low.n_peers >= 5
```

- [ ] **Step 2: Run test**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_peer.py -v`
Expected: 12 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_peer.py
git commit -m "test(peer): cover size_bucket and low-n warning paths"
```

---

## Task 5: api.py — `get_industry_aggregates` 리팩터 (backward compatible)

**Files:**
- Modify: `kreports/analysis/api.py:675-895` (`get_industry_aggregates`)
- Modify: `tests/` — 기존 compare_to_industry 회귀 테스트 확인

**Goal:** 기존 `get_industry_aggregates` 응답 키·동작 유지하되, peer 후보 산정 로직을 `peer.resolve_peers`로 교체. 기존 호출자(`compare_to_industry`)는 무변경.

- [ ] **Step 1: Write regression test for existing compare_to_industry response shape**

```python
# tests/test_compare_industry_multi.py (new file, but first add regression for old API)
from kreports.analysis.api import compare_to_industry


def test_compare_to_industry_samsung_legacy_shape():
    """기존 compare_to_industry 응답 키가 유지된다 (회귀)."""
    out = compare_to_industry(company="005930", metric="영업이익률")
    assert "induty_code" in out
    assert "match_prefix" in out
    assert "metric" in out
    assert "year" in out
    assert "n" in out
    assert "quantiles" in out
    assert "peers" in out
```

- [ ] **Step 2: Run test (should pass with current code as baseline)**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_compare_industry_multi.py -v`
Expected: PASS (현재 코드 기준 baseline 확보)

- [ ] **Step 3: Refactor get_industry_aggregates to use resolve_peers internally**

`kreports/analysis/api.py` 내부 `get_industry_aggregates`의 후보 수집부분(현 `_query_peers`-유사 로직)을 `resolve_peers`로 위임. 기존 응답 필드(`induty_code`, `match_prefix`, `prefix_len`, `metric`, `unit`, `year`, `fs_div`, `n`, `quantiles`, `peers`, `note`)는 그대로 유지. 신규 메타 필드(`sector_group`, `confidence`, `excluded_categories`, `size_bucket_applied`)는 추가만(기존 키 영향 없음).

핵심 변경 코드 스케치 (전체 함수는 250줄이라 patch는 실제 구현 시 작성):

```python
# pseudo-diff
- # (현행) prefix 매칭으로 직접 SQL → peer corp_code 모음
- match_prefix = induty_code[:prefix_len]
- conn.execute(text(... substr ...))
+ from kreports.analysis.peer import resolve_peers, classify_sector, SectorGroup
+ pr = resolve_peers(
+     subject_corp_code,
+     prefix_len_start=prefix_len,
+     min_n=5,
+     exclude_other_sectors=True,
+     fs_div=fs_div,
+     year=year,
+ )
+ peer_corp_codes = pr.peer_corp_codes
+ matched_prefix_len = pr.matched_prefix_len
+ # metric 값을 SQL로 한 번에 가져오기 (in 절)
+ values = _fetch_metric_values(conn, peer_corp_codes, metric, year, fs_div)
...
# 응답에 추가 메타
+ result["sector_group"] = pr.sector_group.value
+ result["confidence"] = pr.confidence
+ result["excluded_categories"] = pr.excluded_categories
+ result["size_bucket_applied"] = pr.size_bucket_applied
```

- [ ] **Step 4: Run regression + peer tests**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_peer.py tests/test_compare_industry_multi.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/api.py tests/test_compare_industry_multi.py
git commit -m "refactor(analysis): get_industry_aggregates uses peer.resolve_peers"
```

---

## Task 6: api.py — `compare_to_industry_multi` 추가

**Files:**
- Modify: `kreports/analysis/api.py` (새 함수 추가)
- Modify: `tests/test_compare_industry_multi.py`

**Goal:** 다지표(8개 전체 또는 사용자 지정) × 다년도(기본 5년) peer 분포 + subject 위치를 한 응답으로 반환.

- [ ] **Step 1: Write failing test**

```python
# tests/test_compare_industry_multi.py — append
from kreports.analysis.api import compare_to_industry_multi


def test_compare_multi_samsung_default_8metrics_5years():
    out = compare_to_industry_multi(company="005930")
    assert out["subject"]["corp_name"] == "삼성전자"
    assert out["sector_group"] == "general"
    assert "matched_prefix_len" in out
    assert "confidence" in out
    # results: { year: { metric: {p25,p50,p75,subject_value,percentile} } }
    assert isinstance(out["results"], dict)
    # 최소 1개 연도 이상 결과 존재
    years = list(out["results"].keys())
    assert len(years) >= 1
    metrics = out["results"][years[0]]
    assert "영업이익률" in metrics
    assert "ROE" in metrics
    inner = metrics["영업이익률"]
    assert "p50" in inner
    assert "subject_value" in inner


def test_compare_multi_explicit_metrics_and_years():
    out = compare_to_industry_multi(
        company="005930", metrics=["ROE", "ROA"], years_back=3
    )
    years = sorted(out["results"].keys())
    assert len(years) <= 3
    sample = out["results"][years[-1]]
    assert set(sample.keys()) == {"ROE", "ROA"}


def test_compare_multi_unknown_company_returns_error():
    out = compare_to_industry_multi(company="존재하지않는회사명12345")
    assert "error" in out
```

- [ ] **Step 2: Run test (expect failure: function missing)**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_compare_industry_multi.py -v`
Expected: ImportError on compare_to_industry_multi

- [ ] **Step 3: Implement compare_to_industry_multi**

```python
# kreports/analysis/api.py — append near compare_to_industry

_ALL_METRICS = [
    "영업이익률", "순이익률", "부채비율", "ROE", "ROA",
    "자기자본비율", "매출성장률", "Beneish_M",
]


def compare_to_industry_multi(
    company: str,
    metrics: Optional[list[str]] = None,
    years_back: int = 5,
    fs_div: str = "CFS",
    prefix_len_start: int = 3,
    exclude_other_sectors: bool = True,
    size_bucket_decade: Optional[float] = None,
) -> dict:
    """다지표·다년도 동종업종 분포 + subject percentile."""
    from kreports.analysis.peer import resolve_peers, SectorGroup
    from sqlalchemy import text

    corp_code = resolve_corp_code(company)
    if corp_code is None:
        return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}

    if metrics is None:
        metrics = list(_ALL_METRICS)
    invalid = [m for m in metrics if m not in _METRIC_SQL]
    if invalid:
        return {"error": f"지원하지 않는 metric: {invalid}. 지원: {_ALL_METRICS}"}

    with engine.connect() as conn:
        subject_row = conn.execute(
            text("SELECT corp_name, induty_code FROM companies WHERE corp_code=:cc"),
            {"cc": corp_code},
        ).first()
    if subject_row is None:
        return {"error": f"corp_code '{corp_code}' 미등록"}

    subject_name, subject_induty = subject_row

    # peer 풀은 *최신 연도 기준 한 번만* 결정 (5년치 모두 동일 peer set 사용)
    pr = resolve_peers(
        corp_code,
        prefix_len_start=prefix_len_start,
        min_n=5,
        exclude_other_sectors=exclude_other_sectors,
        size_bucket_decade=size_bucket_decade,
        fs_div=fs_div,
    )

    if not pr.peer_corp_codes:
        return {
            "subject": {
                "corp_code": corp_code,
                "corp_name": subject_name,
                "induty_code": subject_induty,
            },
            "sector_group": pr.sector_group.value,
            "matched_prefix_len": pr.matched_prefix_len,
            "n_peers": 0,
            "confidence": pr.confidence,
            "results": {},
            "note": pr.note,
        }

    # 최신 연도 확보 후 N년 슬라이딩
    with engine.connect() as conn:
        latest_row = conn.execute(
            text(
                "SELECT MAX(year) FROM financials "
                "WHERE quarter=4 AND fs_div=:fs AND corp_code IN :ccs"
            ).bindparams(ccs=tuple(pr.peer_corp_codes + [corp_code])),
            {"fs": fs_div},
        ).first()
        latest_year = latest_row[0] if latest_row else None
        if latest_year is None:
            return {
                "subject": {"corp_code": corp_code, "corp_name": subject_name},
                "results": {},
                "note": "최신 Q4 재무 데이터 없음",
            }
        years = list(range(latest_year - years_back + 1, latest_year + 1))

        results: dict[int, dict[str, dict]] = {}
        for y in years:
            results[y] = {}
            for metric in metrics:
                expr = _METRIC_SQL[metric]
                # peer values
                peer_vals = conn.execute(
                    text(
                        f"SELECT {expr} AS v FROM financials f "
                        "WHERE f.corp_code IN :ccs "
                        "  AND f.year=:y AND f.quarter=4 AND f.fs_div=:fs"
                    ).bindparams(ccs=tuple(pr.peer_corp_codes)),
                    {"y": y, "fs": fs_div},
                ).all()
                vals = [r[0] for r in peer_vals if r[0] is not None]
                # subject value
                subj_row = conn.execute(
                    text(
                        f"SELECT {expr} AS v FROM financials f "
                        "WHERE f.corp_code=:cc AND f.year=:y AND f.quarter=4 AND f.fs_div=:fs"
                    ),
                    {"cc": corp_code, "y": y, "fs": fs_div},
                ).first()
                subj_val = subj_row[0] if subj_row else None

                vals_sorted = sorted(vals)
                p25 = _quantile(vals_sorted, 0.25)
                p50 = _quantile(vals_sorted, 0.50)
                p75 = _quantile(vals_sorted, 0.75)
                percentile = None
                if subj_val is not None and vals_sorted:
                    below = sum(1 for v in vals_sorted if v < subj_val)
                    percentile = round(100.0 * below / len(vals_sorted), 1)
                results[y][metric] = {
                    "p25": p25, "p50": p50, "p75": p75,
                    "n": len(vals_sorted),
                    "subject_value": subj_val,
                    "percentile": percentile,
                    "unit": _METRIC_UNIT.get(metric),
                }

    return {
        "subject": {
            "corp_code": corp_code,
            "corp_name": subject_name,
            "induty_code": subject_induty,
        },
        "sector_group": pr.sector_group.value,
        "matched_prefix_len": pr.matched_prefix_len,
        "n_peers": pr.n_peers,
        "confidence": pr.confidence,
        "excluded_categories": pr.excluded_categories,
        "size_bucket_applied": pr.size_bucket_applied,
        "fs_div": fs_div,
        "years": years,
        "metrics": metrics,
        "results": results,
        "note": pr.note,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_compare_industry_multi.py -v`
Expected: 4 passed (test_compare_to_industry_samsung_legacy_shape + 3 new)

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/api.py tests/test_compare_industry_multi.py
git commit -m "feat(analysis): add compare_to_industry_multi (multi-metric, multi-year)"
```

---

## Task 7: api.py — `get_industry_audit_landscape`

**Files:**
- Modify: `kreports/analysis/api.py`
- Create: `tests/test_audit_landscape.py`

**Goal:** 업종 내 감사인 시장점유율(회사수·자산가중), 비적정 의견 발생율, 평균 tenure, Big4 점유율을 반환. `auditors` 테이블만으로 동작 (audit_fees 비어 있어도 OK).

- [ ] **Step 1: Write failing test**

```python
# tests/test_audit_landscape.py
import pytest
from kreports.analysis.api import get_industry_audit_landscape


def test_audit_landscape_samsung_basic_shape():
    out = get_industry_audit_landscape(company="005930")
    assert out["subject"]["corp_name"] == "삼성전자"
    assert "matched_prefix_len" in out
    assert "n_peers" in out
    assert "auditor_market_share" in out
    assert "big4_share" in out
    assert "non_qualified_opinion_rate" in out  # 비적정 발생율
    assert "avg_tenure" in out
    assert "subject_auditor" in out


def test_audit_landscape_returns_top_auditors_sorted():
    out = get_industry_audit_landscape(company="005930", top_n=5)
    shares = out["auditor_market_share"]
    if shares:
        counts = [s["company_count"] for s in shares]
        assert counts == sorted(counts, reverse=True)
        assert len(shares) <= 5


def test_audit_landscape_unknown_company():
    out = get_industry_audit_landscape(company="없는회사999")
    assert "error" in out


def test_audit_landscape_explicit_induty_code():
    out = get_industry_audit_landscape(induty_code="264")
    assert out.get("error") is None or "subject" in out
```

- [ ] **Step 2: Run test (expect failure)**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_audit_landscape.py -v`
Expected: ImportError on get_industry_audit_landscape

- [ ] **Step 3: Implement get_industry_audit_landscape**

```python
# kreports/analysis/api.py — append

_BIG4_KEYWORDS = ("삼일", "삼정", "한영", "안진", "PwC", "KPMG", "EY", "Deloitte")


def _is_big4(name: Optional[str]) -> bool:
    if not name:
        return False
    return any(k in name for k in _BIG4_KEYWORDS)


def get_industry_audit_landscape(
    company: Optional[str] = None,
    induty_code: Optional[str] = None,
    years_back: int = 5,
    fs_div: str = "CFS",
    prefix_len_start: int = 3,
    top_n: int = 10,
    exclude_other_sectors: bool = True,
) -> dict:
    """업종 내 감사 시장 분석: 점유율, 비적정 발생율, tenure, Big4 share."""
    from kreports.analysis.peer import resolve_peers, classify_sector, SectorGroup
    from sqlalchemy import text

    if company:
        corp_code = resolve_corp_code(company)
        if corp_code is None:
            return {"error": f"'{company}'에 해당하는 기업을 찾을 수 없습니다."}
    elif induty_code:
        # induty_code 기반: 해당 prefix 첫 회사를 subject 대용으로 사용
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT corp_code FROM companies "
                    "WHERE substr(induty_code,1,:plen)=:prefix LIMIT 1"
                ),
                {"plen": len(induty_code), "prefix": induty_code},
            ).first()
        if row is None:
            return {"error": f"induty_code '{induty_code}' 회사 없음"}
        corp_code = row[0]
    else:
        return {"error": "company 또는 induty_code 중 하나 필요"}

    pr = resolve_peers(
        corp_code,
        prefix_len_start=prefix_len_start,
        min_n=5,
        exclude_other_sectors=exclude_other_sectors,
        fs_div=fs_div,
    )

    with engine.connect() as conn:
        subject_row = conn.execute(
            text(
                "SELECT corp_name, induty_code FROM companies WHERE corp_code=:cc"
            ),
            {"cc": corp_code},
        ).first()
        subject_name = subject_row[0] if subject_row else None
        subject_induty = subject_row[1] if subject_row else None

        peer_set = list(pr.peer_corp_codes) + [corp_code]

        # 최신 연도 확보
        latest_row = conn.execute(
            text(
                "SELECT MAX(bsns_year) FROM auditors "
                "WHERE corp_code IN :ccs AND fs_div=:fs"
            ).bindparams(ccs=tuple(peer_set)),
            {"fs": fs_div},
        ).first()
        latest_year = latest_row[0] if latest_row else None

        if latest_year is None:
            return {
                "subject": {
                    "corp_code": corp_code,
                    "corp_name": subject_name,
                    "induty_code": subject_induty,
                },
                "sector_group": pr.sector_group.value,
                "matched_prefix_len": pr.matched_prefix_len,
                "n_peers": pr.n_peers,
                "confidence": pr.confidence,
                "auditor_market_share": [],
                "big4_share": None,
                "non_qualified_opinion_rate": None,
                "avg_tenure": None,
                "subject_auditor": None,
                "note": "auditors 데이터 부족 (collect-auditors 미실행 또는 데이터 없음)",
            }

        years = list(range(latest_year - years_back + 1, latest_year + 1))

        # 시장 점유율 — 최신 연도, 회사 수 기준
        share_rows = conn.execute(
            text(
                "SELECT auditor_nm, COUNT(DISTINCT corp_code) AS n "
                "FROM auditors "
                "WHERE corp_code IN :ccs AND bsns_year=:y AND fs_div=:fs "
                "GROUP BY auditor_nm ORDER BY n DESC LIMIT :topn"
            ).bindparams(ccs=tuple(peer_set)),
            {"y": latest_year, "fs": fs_div, "topn": top_n},
        ).all()

        # 자산가중 점유율 (latest year)
        weight_rows = conn.execute(
            text(
                "SELECT a.auditor_nm, COALESCE(SUM(f.total_assets),0) AS w "
                "FROM auditors a "
                "LEFT JOIN financials f "
                "  ON f.corp_code=a.corp_code AND f.year=a.bsns_year "
                "  AND f.quarter=4 AND f.fs_div=a.fs_div "
                "WHERE a.corp_code IN :ccs AND a.bsns_year=:y AND a.fs_div=:fs "
                "GROUP BY a.auditor_nm"
            ).bindparams(ccs=tuple(peer_set)),
            {"y": latest_year, "fs": fs_div},
        ).all()
        weight_map = {nm: w for nm, w in weight_rows}
        total_w = sum(weight_map.values()) or 1

        market_share = []
        for nm, n in share_rows:
            market_share.append({
                "auditor_nm": nm,
                "company_count": int(n),
                "company_share_pct": round(100.0 * n / len(peer_set), 1),
                "asset_weighted_share_pct": round(
                    100.0 * weight_map.get(nm, 0) / total_w, 1
                ),
                "is_big4": _is_big4(nm),
            })

        # Big4 share (latest year)
        big4_n = sum(
            int(n) for nm, n in conn.execute(
                text(
                    "SELECT auditor_nm, COUNT(DISTINCT corp_code) "
                    "FROM auditors "
                    "WHERE corp_code IN :ccs AND bsns_year=:y AND fs_div=:fs "
                    "GROUP BY auditor_nm"
                ).bindparams(ccs=tuple(peer_set)),
                {"y": latest_year, "fs": fs_div},
            ).all() if _is_big4(nm)
        )
        # 전체 분모: 최신연도 감사인 데이터 보유 회사 수
        total_with_audit = conn.execute(
            text(
                "SELECT COUNT(DISTINCT corp_code) FROM auditors "
                "WHERE corp_code IN :ccs AND bsns_year=:y AND fs_div=:fs"
            ).bindparams(ccs=tuple(peer_set)),
            {"y": latest_year, "fs": fs_div},
        ).scalar() or 0
        big4_share_pct = round(100.0 * big4_n / total_with_audit, 1) if total_with_audit else None

        # 비적정 의견 발생율 (5년 누적, 비적정 = audit_opinion != '적정' AND NOT NULL)
        opinion_rows = conn.execute(
            text(
                "SELECT audit_opinion, COUNT(*) AS n FROM auditors "
                "WHERE corp_code IN :ccs AND bsns_year BETWEEN :y0 AND :y1 "
                "  AND fs_div=:fs "
                "GROUP BY audit_opinion"
            ).bindparams(ccs=tuple(peer_set)),
            {"y0": years[0], "y1": years[-1], "fs": fs_div},
        ).all()
        total_op = sum(int(n) for _, n in opinion_rows)
        non_qual = sum(
            int(n) for op, n in opinion_rows
            if op and op.strip() != "적정"
        )
        non_qual_rate = round(100.0 * non_qual / total_op, 2) if total_op else None

        # 평균 tenure (latest year)
        tenure_row = conn.execute(
            text(
                "SELECT AVG(consecutive_years) FROM auditors "
                "WHERE corp_code IN :ccs AND bsns_year=:y AND fs_div=:fs "
                "  AND consecutive_years IS NOT NULL"
            ).bindparams(ccs=tuple(peer_set)),
            {"y": latest_year, "fs": fs_div},
        ).first()
        avg_tenure = round(float(tenure_row[0]), 2) if tenure_row and tenure_row[0] is not None else None

        # Subject 자신의 감사인
        subj_audit = conn.execute(
            text(
                "SELECT auditor_nm, audit_opinion, consecutive_years "
                "FROM auditors WHERE corp_code=:cc AND bsns_year=:y AND fs_div=:fs"
            ),
            {"cc": corp_code, "y": latest_year, "fs": fs_div},
        ).first()
        subject_auditor = None
        if subj_audit:
            subject_auditor = {
                "auditor_nm": subj_audit[0],
                "audit_opinion": subj_audit[1],
                "consecutive_years": subj_audit[2],
                "is_big4": _is_big4(subj_audit[0]),
            }

    return {
        "subject": {
            "corp_code": corp_code,
            "corp_name": subject_name,
            "induty_code": subject_induty,
        },
        "sector_group": pr.sector_group.value,
        "matched_prefix_len": pr.matched_prefix_len,
        "n_peers": pr.n_peers,
        "confidence": pr.confidence,
        "excluded_categories": pr.excluded_categories,
        "fs_div": fs_div,
        "latest_year": latest_year,
        "years_window": [years[0], years[-1]],
        "auditor_market_share": market_share,
        "big4_share_pct": big4_share_pct,
        "non_qualified_opinion_rate_pct": non_qual_rate,
        "avg_tenure_years": avg_tenure,
        "subject_auditor": subject_auditor,
        "note": pr.note,
    }
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_audit_landscape.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/api.py tests/test_audit_landscape.py
git commit -m "feat(analysis): add get_industry_audit_landscape"
```

---

## Task 8: MCP — 신규 도구 2개 등록

**Files:**
- Modify: `kreports/mcp/schemas.py`
- Modify: `kreports/mcp/tools.py`

- [ ] **Step 1: Write smoke test for MCP tool registration**

```python
# tests/test_mcp_tools_registration.py (신규 또는 기존에 append)
from kreports.mcp.tools import list_tools


def test_compare_to_industry_multi_registered():
    names = [t["name"] for t in list_tools()]
    assert "compare_to_industry_multi" in names


def test_get_industry_audit_landscape_registered():
    names = [t["name"] for t in list_tools()]
    assert "get_industry_audit_landscape" in names
```

(`list_tools`의 정확한 시그니처는 `kreports/mcp/tools.py:861` 근처 확인 후 일치시킴. 만약 `TOOL_HANDLERS` dict면 그 키 검사로 대체.)

- [ ] **Step 2: Run test (expect failure)**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/test_mcp_tools_registration.py -v`
Expected: AssertionError (도구 미등록)

- [ ] **Step 3: Add JSON schema + handler**

`kreports/mcp/schemas.py`에 두 개 JSON Schema 추가:

```python
# kreports/mcp/schemas.py — append

COMPARE_TO_INDUSTRY_MULTI_SCHEMA = {
    "type": "object",
    "required": ["company"],
    "properties": {
        "company": {"type": "string", "description": "corp_code / 종목코드 / 회사명"},
        "metrics": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["영업이익률","순이익률","부채비율","ROE","ROA",
                         "자기자본비율","매출성장률","Beneish_M"],
            },
            "description": "비교 지표 리스트. 생략 시 8개 전체.",
        },
        "years_back": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        "fs_div": {"type": "string", "enum": ["CFS","OFS"], "default": "CFS"},
        "prefix_len_start": {"type": "integer", "minimum": 2, "maximum": 5, "default": 3},
        "exclude_other_sectors": {"type": "boolean", "default": True},
        "size_bucket_decade": {
            "type": "number", "minimum": 0.5, "maximum": 3.0,
            "description": "자산 log10 ±decade 필터 (예: 1.0=±10배). 생략 시 미적용.",
        },
    },
}

GET_INDUSTRY_AUDIT_LANDSCAPE_SCHEMA = {
    "type": "object",
    "properties": {
        "company": {"type": "string"},
        "induty_code": {"type": "string"},
        "years_back": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        "fs_div": {"type": "string", "enum": ["CFS","OFS"], "default": "CFS"},
        "prefix_len_start": {"type": "integer", "minimum": 2, "maximum": 5, "default": 3},
        "top_n": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        "exclude_other_sectors": {"type": "boolean", "default": True},
    },
    "oneOf": [
        {"required": ["company"]},
        {"required": ["induty_code"]},
    ],
}
```

`kreports/mcp/tools.py`에 핸들러 추가 (기존 패턴 mirror):

```python
# kreports/mcp/tools.py — append near existing handlers

def _handle_compare_to_industry_multi(args: dict) -> dict:
    company = _require_string(args, "company")
    metrics = args.get("metrics")
    years_back = _optional_int(args, "years_back", default=5, min_v=1, max_v=10)
    fs_div = _optional_enum(args, "fs_div", choices=["CFS","OFS"], default="CFS")
    prefix_len_start = _optional_int(args, "prefix_len_start", default=3, min_v=2, max_v=5)
    exclude_other = _optional_bool(args, "exclude_other_sectors", default=True)
    size_bucket = _optional_float(args, "size_bucket_decade")
    from kreports.analysis.api import compare_to_industry_multi
    return compare_to_industry_multi(
        company=company,
        metrics=metrics,
        years_back=years_back,
        fs_div=fs_div,
        prefix_len_start=prefix_len_start,
        exclude_other_sectors=exclude_other,
        size_bucket_decade=size_bucket,
    )


_register_tool(
    name="compare_to_industry_multi",
    description=(
        "동종업종 내 다지표·다년도(기본 5년) 분포와 subject 회사 percentile을 "
        "한 번에 반환. peer 매칭은 adaptive ladder(p3→p2) + sector 분리 + size bucket(opt-in)."
    ),
    input_schema=COMPARE_TO_INDUSTRY_MULTI_SCHEMA,
    handler=_handle_compare_to_industry_multi,
)


def _handle_get_industry_audit_landscape(args: dict) -> dict:
    company = _optional_string(args, "company")
    induty_code = _optional_string(args, "induty_code")
    years_back = _optional_int(args, "years_back", default=5, min_v=1, max_v=10)
    fs_div = _optional_enum(args, "fs_div", choices=["CFS","OFS"], default="CFS")
    prefix_len_start = _optional_int(args, "prefix_len_start", default=3, min_v=2, max_v=5)
    top_n = _optional_int(args, "top_n", default=10, min_v=1, max_v=50)
    exclude_other = _optional_bool(args, "exclude_other_sectors", default=True)
    from kreports.analysis.api import get_industry_audit_landscape
    return get_industry_audit_landscape(
        company=company,
        induty_code=induty_code,
        years_back=years_back,
        fs_div=fs_div,
        prefix_len_start=prefix_len_start,
        top_n=top_n,
        exclude_other_sectors=exclude_other,
    )


_register_tool(
    name="get_industry_audit_landscape",
    description=(
        "업종 내 감사 시장 분석: 감사인 시장점유율(회사수·자산가중), Big4 점유율, "
        "비적정 의견 발생율(5년 누적), 평균 tenure, subject 회사의 감사인 정보."
    ),
    input_schema=GET_INDUSTRY_AUDIT_LANDSCAPE_SCHEMA,
    handler=_handle_get_industry_audit_landscape,
)
```

(주의: `_register_tool` 매크로/패턴은 기존 코드와 일치시킴. 현 코드에서 `_TOOLS = [...]` 또는 데코레이터 사용 중인지 `kreports/mcp/tools.py:347` 근처 확인 후 동일 형식으로.)

- [ ] **Step 4: Run all tests**

Run: `cd /Users/kjun/vault/01_Projects/kreports_dart_mcp && .venv/bin/pytest tests/ -v -k 'peer or compare_industry or audit_landscape or mcp_tools_registration'`
Expected: 모두 통과

- [ ] **Step 5: Commit**

```bash
git add kreports/mcp/schemas.py kreports/mcp/tools.py tests/test_mcp_tools_registration.py
git commit -m "feat(mcp): register compare_to_industry_multi and get_industry_audit_landscape"
```

---

## Task 9: 검증 — 실제 MCP 호출 end-to-end

**Files:** (검증 단계, 코드 변경 없음)

- [ ] **Step 1: kreports-mcp 재시작 (캐시된 binary는 재시작 필요)**

```bash
# 사용자가 직접 실행 (이 plan은 destructive action 회피)
# Claude Desktop / Claude Code를 재시작하거나 MCP 프로세스 kill
pkill -f kreports-mcp
```

- [ ] **Step 2: MCP에서 신규 도구 검증**

새 Claude 세션에서:
```
- compare_to_industry_multi(company="005930") 호출 → results 키에 연도별 metric 분포 + subject percentile 확인
- get_industry_audit_landscape(company="005930") 호출 → auditor_market_share 리스트 + big4_share_pct 숫자 확인
```

- [ ] **Step 3: dataset-health 갱신 확인**

`python -m kreports.cli.main dataset-health` 실행 후, audit 관련 신규 지표는 별도 갱신 불필요(읽기 전용 분석). 단 audit_fees / auditors coverage 낮으므로 응답에 `note`로 경고 잘 나오는지 시각 확인.

- [ ] **Step 4: README 또는 docs/ 갱신**

```bash
# docs/MCP_TOOLS.md (있다면) 또는 README에 신규 도구 2개 항목 추가
# 짧은 1~2줄 설명 + JSON 예시
```

- [ ] **Step 5: Final commit (문서 갱신)**

```bash
git add README.md docs/
git commit -m "docs: add compare_to_industry_multi and audit_landscape tool docs"
```

---

## Self-Review Notes

- **Spec 커버리지**: Tier S 합의 3개 결정사항 모두 반영 — adaptive ladder(Task 3), sector 분리(Task 1·3), size bucket opt-in(Task 3·4). 다지표 동시 비교(Task 6) + 감사 시장 분석(Task 7) 둘 다 별도 도구로 분리됨.
- **Placeholder 없음 확인**: 모든 Step에 실제 코드/명령. Task 5의 `get_industry_aggregates` 리팩터는 함수가 250줄이라 pseudo-diff로 표현했으나 실제 구현 시 해당 함수 내부의 peer 조회 블록만 교체하는 작업이라 명확.
- **Type 일관성**: `PeerResolution` dataclass 필드명이 Task 2 정의 → Task 3·5·6·7에서 일관 사용. `_METRIC_SQL`·`_METRIC_UNIT`은 기존 api.py 상수 재사용.
- **데이터 의존성**:
  - `compare_to_industry_multi`는 financials 기반 → 현재 1,220 corps coverage로도 동작
  - `get_industry_audit_landscape`는 auditors 기반 → 현재 95 rows라 응답이 sparse할 수 있지만 함수는 graceful degradation (note에 명시)
  - `collect-auditors` 완주 후 본격 활용 가능 — 도구는 미리 만들어두고 데이터가 채워지면 자동 활용

## Execution Handoff

이 plan은 9개 task로 분해되어 있고, 각 task는 독립 commit으로 닫힌다. T2 표준 작업이므로 다음 둘 중 택일:

**1. Subagent-Driven (권장)** — fresh subagent 1개 per task + task 간 리뷰
**2. Inline Execution** — 현재 세션에서 모두 처리, 중간 체크포인트 통과 요청

선택 후 실행 진입.
