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
