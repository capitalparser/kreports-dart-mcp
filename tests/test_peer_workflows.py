from __future__ import annotations


def test_search_note_disclosing_companies_preserves_company_evidence(monkeypatch):
    from kreports.analysis import peer_workflows

    captured = {}

    def fake_search_dataset(**kwargs):
        captured.update(kwargs)
        return {
            "total_companies": 1,
            "total_records": 2,
            "companies": [
                {
                    "corp_code": "00000001",
                    "corp_name": "Example",
                    "records": [
                        {
                            "year": 2024,
                            "note_no": "32",
                            "note_title": "우발부채 및 약정사항",
                            "rcept_no": "20250318000001",
                            "body_excerpt": "자금보충약정 ...",
                        }
                    ],
                }
            ],
            "data_quality": {"status": "usable", "source": "accounting_note_chapters"},
        }

    monkeypatch.setattr(peer_workflows, "search_dataset", fake_search_dataset)

    out = peer_workflows.search_note_disclosing_companies(
        " 자금보충약정 ",
        year=2024,
        market="KOSPI",
        induty_prefix="35",
        fs_div="CFS",
    )

    assert captured["dataset"] == "accounting_note_chapters"
    assert captured["keyword"] == "자금보충약정"
    assert captured["year"] == 2024
    assert out["query"]["dataset"] == "accounting_note_chapters"
    assert out["total_companies"] == 1
    assert out["companies"][0]["records"][0]["rcept_no"] == "20250318000001"
    assert "cache_miss_is_not_disclosure_absence" in out["data_quality"]["limitations"]


def test_custom_peer_bundle_reuses_exact_peer_group(monkeypatch):
    from kreports.analysis import peer_workflows

    peer_group = {
        "subject": {"corp_code": "00000001", "corp_name": "Subject"},
        "selection_policy": {"fs_div_used": "CFS"},
        "peers": [{"corp_code": "00000002"}],
    }
    seen_groups = []

    monkeypatch.setattr(
        peer_workflows,
        "build_custom_peer_group",
        lambda *args, **kwargs: peer_group,
    )

    def child(**kwargs):
        seen_groups.append(kwargs.get("_peer_group"))
        return {"ok": True}

    monkeypatch.setattr(peer_workflows, "compare_peer_audit_fees", child)
    monkeypatch.setattr(peer_workflows, "compare_peer_risk_profile", child)
    monkeypatch.setattr(peer_workflows, "compare_peer_accounting_policies", child)
    monkeypatch.setattr(peer_workflows, "compare_peer_kam_topics", child)
    monkeypatch.setattr(peer_workflows, "compare_peer_audit_report_matters", child)
    monkeypatch.setattr(peer_workflows, "compare_peer_audit_procedures", child)
    monkeypatch.setattr(
        peer_workflows,
        "compare_peer_accounting_notes",
        lambda **kwargs: {"peer_criteria": kwargs.get("peer_criteria")},
    )

    out = peer_workflows.compare_custom_peer_bundle(
        "00000001",
        year=2024,
        peer_criteria={"mode": "strict", "prefix_len": 3},
    )

    assert len(seen_groups) == 6
    assert all(group is peer_group for group in seen_groups)
    assert out["accounting_notes"]["peer_criteria"] == {
        "mode": "strict",
        "industry_basis": "custom_codes",
        "included_corp_codes": ["00000002"],
    }
    assert out["peer_group"] is peer_group


def test_custom_financial_comparison_uses_resolved_cohort(monkeypatch):
    from kreports.analysis import peer_workflows

    peer_group = {
        "subject": {"corp_code": "00000001", "corp_name": "Subject"},
        "selection_policy": {"fs_div_used": "CFS", "resolved_year": 2024},
        "peers": [{"corp_code": "00000002"}, {"corp_code": "00000003"}],
    }
    monkeypatch.setattr(
        peer_workflows,
        "build_custom_peer_group",
        lambda *args, **kwargs: peer_group,
    )

    class FakeConnection:
        pass

    class FakeContext:
        def __enter__(self):
            return FakeConnection()

        def __exit__(self, *args):
            return False

    class FakeEngine:
        def connect(self):
            return FakeContext()

    monkeypatch.setattr(peer_workflows._engine_module, "engine", FakeEngine())

    def fake_fetch(conn, corp_codes, metric_expr, year, fs_div):
        if corp_codes == ["00000001"]:
            return [("00000001", "Subject", "26", 20.0)]
        return [
            ("00000002", "Peer A", "26", 10.0),
            ("00000003", "Peer B", "26", 30.0),
        ]

    monkeypatch.setattr(peer_workflows, "_fetch_metric_values", fake_fetch)

    out = peer_workflows.compare_custom_peer_financials(
        "00000001",
        year=2024,
        metrics=["영업이익률"],
        years_back=1,
        peer_criteria={"mode": "strict"},
    )

    metric = out["results"][2024]["영업이익률"]
    assert out["peer_group"] is peer_group
    assert metric["n"] == 2
    assert metric["p50"] == 20.0
    assert metric["subject_value"] == 20.0
    assert metric["percentile"] == 50.0


def test_blank_note_keyword_fails_closed():
    from kreports.analysis.peer_workflows import search_note_disclosing_companies

    out = search_note_disclosing_companies("   ")
    assert out["error"] == "keyword is required"
    assert out["total_companies"] == 0
