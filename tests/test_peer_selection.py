import json

from kreports.analysis.api import select_peer_group
from kreports.analysis.peer import resolve_fs_div_for_company
from kreports.mcp.tools import call_tool


def test_resolve_fs_strategy_auto_prefers_cfs(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="A", induty_code="264"))
        session.add(Financial(corp_code="00000001", year=2025, quarter=4, fs_div="CFS", total_assets=100))

    assert resolve_fs_div_for_company("00000001", 2025, "auto") == "CFS"


def test_resolve_fs_strategy_auto_falls_back_to_ofs(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import Company, Financial

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="A", induty_code="264"))
        session.add(Financial(corp_code="00000001", year=2025, quarter=4, fs_div="OFS", total_assets=100))

    assert resolve_fs_div_for_company("00000001", 2025, "auto") == "OFS"


def test_select_peer_group_returns_reason_codes_for_real_db():
    out = select_peer_group("005930", criteria=["industry", "size"], peer_limit=10)
    assert out["subject"]["corp_code"] == "00126380"
    assert out["peer_count"] > 0
    first = out["peers"][0]
    assert "corp_code" in first
    assert "include_reasons" in first
    assert "same_ksic_prefix" in first["include_reasons"]
    assert "reason_components" in first
    assert "industry_match" in first["reason_components"]
    assert "kam_topic_overlap" in first["reason_components"]
    assert "selection_policy" in out


def test_select_peer_group_mcp_dispatch():
    out = json.loads(call_tool("select_peer_group", {"company": "005930", "peer_limit": 5}))
    assert out["peer_count"] > 0
    assert out["_meta"]["tool"] == "select_peer_group"


def test_select_peer_group_propagates_requested_year_to_cohort_resolution(temp_engine, monkeypatch):
    from kreports.analysis import api, peer_benchmarks
    from kreports.analysis.peer import PeerResolution, SectorGroup
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add(Company(corp_code="00000001", stock_code="000001", corp_name="A", induty_code="264"))

    seen = {}

    def fake_fs_div(corp_code, year, strategy):
        seen["fs_year"] = year
        return "CFS"

    def fake_resolve_peers(**kwargs):
        seen["peer_year"] = kwargs["year"]
        return PeerResolution([], 3, SectorGroup.GENERAL, 0, resolved_year=kwargs["year"])

    monkeypatch.setattr(peer_benchmarks, "resolve_fs_div_for_company", fake_fs_div)
    monkeypatch.setattr(peer_benchmarks, "resolve_peers", fake_resolve_peers)
    out = api.select_peer_group("00000001", year=2022)

    assert seen == {"fs_year": 2022, "peer_year": 2022}
    assert out["selection_policy"]["requested_year"] == 2022
    assert out["selection_policy"]["resolved_year"] == 2022
