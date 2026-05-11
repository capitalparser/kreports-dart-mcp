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
