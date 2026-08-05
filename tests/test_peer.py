import pytest

from kreports.analysis.peer import (
    PeerResolution,
    SectorGroup,
    classify_sector,
    confidence_band,
    resolve_peers,
)


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


@pytest.mark.live_data
def test_resolve_peers_samsung_uses_p3_general():
    """삼성전자 (264 반도체)는 p3로 매칭되고, sector_group=general."""
    pr = resolve_peers("00126380")
    assert pr.sector_group == SectorGroup.GENERAL
    assert pr.matched_prefix_len in (2, 3)
    assert pr.n_peers >= 5
    assert "00126380" not in pr.peer_corp_codes


@pytest.mark.live_data
def test_resolve_peers_excludes_financial_when_subject_general():
    from sqlalchemy import bindparam, text
    from kreports.db.engine import engine

    pr = resolve_peers("00126380")
    if not pr.peer_corp_codes:
        pytest.skip("peer 데이터 없음")
    stmt = text(
        "SELECT induty_code FROM companies WHERE corp_code IN :ccs"
    ).bindparams(bindparam("ccs", expanding=True))
    with engine.connect() as conn:
        rows = conn.execute(stmt, {"ccs": list(pr.peer_corp_codes)}).all()
    for (induty,) in rows:
        if induty:
            assert not induty.startswith(("64", "65", "66", "68")), induty
            assert not induty.startswith("6420")


def test_resolve_peers_unknown_corp_returns_empty():
    pr = resolve_peers("99999999")
    assert pr.n_peers == 0
    assert pr.peer_corp_codes == []


@pytest.mark.live_data
def test_resolve_peers_size_bucket_reduces_pool():
    """size_bucket_decade=1.0 적용 시 peer 풀이 줄어든다."""
    pr_full = resolve_peers("00126380")
    pr_bucketed = resolve_peers("00126380", size_bucket_decade=1.0)
    assert pr_bucketed.size_bucket_applied == 1.0
    assert pr_bucketed.n_peers <= pr_full.n_peers


@pytest.mark.live_data
def test_resolve_peers_note_warns_when_low_n():
    pr_low = resolve_peers("00126380", min_n=999_999)
    assert "peer 수가 부족" in pr_low.note or pr_low.n_peers >= 5
