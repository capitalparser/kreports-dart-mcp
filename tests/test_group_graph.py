from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text

from kreports.analysis.group_graph import (
    ComponentMetric,
    GroupEntity,
    GroupGraph,
    GroupGraphUnavailable,
    GroupRelationship,
    QscResult,
    build_group_graph,
    classify_qsc,
    compute_component_share,
    normalize_entity_name,
)


def test_qsc_boundary_and_missing_contract():
    assert classify_qsc(9.9, 10.0).status == "qsc"
    assert classify_qsc(10.0, 2.0).status == "qsc"
    assert classify_qsc(9.9, 9.9).status == "not_qsc"
    assert classify_qsc(None, 12.0).status == "qsc"
    assert classify_qsc(None, 9.9).status == "undetermined"
    assert classify_qsc(None, None).status == "undetermined"
    for value in (float("nan"), float("inf"), -1.0, 101.0):
        with pytest.raises((TypeError, ValueError)):
            classify_qsc(value, None)
    with pytest.raises(ValueError):
        QscResult("qsc", ())
    with pytest.raises(ValueError):
        QscResult("undetermined", ("asset_share_pct>=10.0",))
    with pytest.raises(ValueError):
        QscResult(
            "qsc", ("not-a-valid-basis",), evidence_refs=("r1",),
        )
    with pytest.raises(ValueError):
        QscResult(
            "qsc", ("asset_share_pct>=10.0",),
            evidence_refs=tuple(f"r{i}" for i in range(9)),
        )


def test_component_share_requires_compatible_complete_evidence():
    assert compute_component_share(
        10, "KRW", "2025", "CFS", "before_elimination", "r1",
        100, "KRW", "2025", "CFS", "before_elimination", "r2",
    ) == (10.0, None)
    assert compute_component_share(
        10, "KRW", "2025", "CFS", "before_elimination", "r1",
        0, "KRW", "2025", "CFS", "before_elimination", "r2",
    )[1] == "denominator_not_positive"
    assert compute_component_share(
        10, "KRW", "2025", "CFS", "before_elimination", "r1",
        100, "USD", "2025", "CFS", "before_elimination", "r2",
    )[1] == "unit_mismatch"
    assert compute_component_share(
        10, "KRW", "2025", "CFS", "before_elimination", "",
        100, "KRW", "2025", "CFS", "before_elimination", "r2",
    )[1] == "incomplete_evidence_identity"


def test_names_normalize_only_identity_noise_and_keep_original():
    assert normalize_entity_name("  (주) 에이·비씨[주1] ") == "에이비씨"
    assert normalize_entity_name("ABC Co., Ltd.") == "abc"
    assert normalize_entity_name("Incubator") == "incubator"
    entity = GroupEntity(
        entity_key="e1", original_name="(주) 에이비씨[주1]",
        normalized_name="에이비씨", resolution_status="unresolved",
        resolution_reason="unlisted", listed_state="N", source_rcept_no="r1",
        source_table="SUB_CMPN", source_ordinal=1,
    )
    assert entity.original_name == "(주) 에이비씨[주1]"
    with pytest.raises(FrozenInstanceError):
        entity.original_name = "changed"  # type: ignore[misc]


def test_typed_contracts_reject_invalid_values_and_references():
    with pytest.raises(ValueError):
        QscResult("maybe", (), 10.0, ())
    edge = GroupRelationship(
        "r", "p", "missing", "subsidiary", None, 2025, "r1", "T", 0,
    )
    with pytest.raises(ValueError, match="unknown entity"):
        GroupGraph("Parent", 2025, (), (edge,))
    with pytest.raises(TypeError):
        ComponentMetric(
            "m", "r1", "e", "assets", float("nan"), "KRW", "r1", "T", 100,
            "KRW", "r2", "financials", "CFS", "2025", "before_elimination",
            None, "undetermined", (), (), 10.0, "invalid_amount",
        )
    for amount in (None, 999):
        with pytest.raises(ValueError, match="share"):
            ComponentMetric(
                "m", "r1", "e", "assets", amount, "KRW", "r1", "T", 100,
                "KRW", "r2", "financials", "CFS", "2025",
                "before_elimination", 12, "qsc",
                ("asset_share_pct>=10.0",), ("r1", "r2"), 10.0, "usable",
            )
    with pytest.raises(ValueError, match="source receipt"):
        ComponentMetric(
            "m", "r1", "e", "assets", 12, "KRW", "other", "T", 100,
            "KRW", "r2", "financials", "CFS", "2025",
            "before_elimination", 12, "qsc",
            ("asset_share_pct>=10.0",), ("other", "r2"), 10.0, "usable",
        )
    with pytest.raises(ValueError, match="evidence_refs"):
        ComponentMetric(
            "m", "r1", "e", "assets", 12, "KRW", "r1", "T", 100,
            "KRW", "r2", "financials", "CFS", "2025",
            "before_elimination", 12, "qsc",
            ("asset_share_pct>=10.0",), (), 10.0, "usable",
        )
    with pytest.raises(ValueError, match="quality_status"):
        ComponentMetric(
            metric_identity="m", source_rcept_no="r1", entity_key="e",
            metric_key="assets", amount=None, unit=None,
            numerator_source_rcept_no=None, numerator_source_table=None,
            denominator_amount=None, denominator_unit=None,
            denominator_source_rcept_no=None,
            denominator_source_table=None, fs_div=None, period=None,
            elimination_basis=None, share_pct=None,
            qsc_status="undetermined", qsc_basis=(),
            qsc_evidence_refs=(), qsc_threshold_pct=10,
            quality_status="invented",
        )


def test_multilevel_paths_cycles_orphans_and_row_order_are_deterministic():
    rows = [
        {"parent": "Parent", "child": "Middle", "ownership_pct": 100.0, "source_rcept_no": "r1"},
        {"parent": "Middle", "child": "Leaf", "ownership_pct": 80.0, "source_rcept_no": "r1"},
    ]
    graph = GroupGraph.from_rows("Parent", rows)
    assert graph.path_to("Leaf") == ("Parent", "Middle", "Leaf")
    assert GroupGraph.from_rows("Parent", list(reversed(rows))) == graph

    cycle = GroupGraph.from_rows("Parent", rows + [
        {"parent": "Leaf", "child": "Parent", "source_rcept_no": "r1"},
        {"parent": "Unknown", "child": "Leaf", "source_rcept_no": "r1"},
    ])
    assert "cycle_detected" in cycle.limitations
    assert "orphan_edge" in cycle.limitations


def test_from_rows_dedupes_exact_claims_and_limits_normalized_self_edges():
    duplicate = {
        "parent": "Parent", "child": "Child",
        "source_rcept_no": "r1", "source_table": "SUB",
        "source_ordinal": 1,
    }
    graph = GroupGraph.from_rows(
        "Parent",
        [
            duplicate,
            dict(duplicate),
            {
                "parent": "ACME Co., Ltd.", "child": "ACME",
                "source_rcept_no": "r1", "source_table": "SUB",
                "source_ordinal": 2,
            },
        ],
    )
    assert len(graph.relationships) == 1
    assert "duplicate_relationship_claim" in graph.limitations
    assert "normalized_self_edge" in graph.limitations


def test_repeated_unresolved_names_do_not_collapse():
    graph = GroupGraph.from_rows("Parent", [
        {"parent": "Parent", "child": "Same", "source_rcept_no": "r1", "source_ordinal": 1},
        {"parent": "Parent", "child": "Same", "source_rcept_no": "r2", "source_ordinal": 1},
    ])
    assert len([entity for entity in graph.entities if entity.original_name == "Same"]) == 2


def test_migration_08_schema_and_prior_checksums(temp_engine):
    from kreports.db.migrations import MIGRATIONS, _checksum, apply_schema_migrations

    prior = [_checksum(item) for item in MIGRATIONS[:7]]
    with temp_engine.begin() as conn:
        apply_schema_migrations(conn)
        assert apply_schema_migrations(conn) == []
    assert MIGRATIONS[-1].revision == "20260711_08_group_audit_graph"
    assert prior == [_checksum(item) for item in MIGRATIONS[:7]]
    tables = set(inspect(temp_engine).get_table_names())
    assert {"group_entities", "group_relationships", "group_component_metrics"} <= tables
    assert inspect(temp_engine).get_indexes("group_entities")
    metric_columns = {
        column["name"]
        for column in inspect(temp_engine).get_columns(
            "group_component_metrics"
        )
    }
    assert {
        "source_rcept_no", "qsc_evidence_refs_json",
    } <= metric_columns
    metric_uniques = inspect(temp_engine).get_unique_constraints(
        "group_component_metrics"
    )
    assert any(
        {
            "parent_corp_code", "effective_year",
            "source_rcept_no", "metric_identity",
        } == set(constraint["column_names"])
        for constraint in metric_uniques
    )
    entity_columns = {
        column["name"]
        for column in inspect(temp_engine).get_columns("group_entities")
    }
    assert "component_auditor_fs_div" in entity_columns


def _seed_graph(conn):
    conn.execute(text("""
        INSERT INTO group_entities
        (parent_corp_code,effective_year,entity_key,original_name,normalized_name,
         resolution_status,resolution_reason,listed_state,source_rcept_no,
         source_table,source_ordinal,fetched_at)
        VALUES
        ('00000001',2025,'p','Parent','parent','resolved','corp_code','Y','r1','SUB',0,CURRENT_TIMESTAMP),
        ('00000001',2025,'c','Child','child','resolved','corp_code','Y','r1','SUB',1,CURRENT_TIMESTAMP)
    """))
    conn.execute(text("""
        INSERT INTO group_relationships
        (parent_corp_code,effective_year,relationship_key,parent_entity_key,
         child_entity_key,relation_type,ownership_pct,source_rcept_no,
         source_table,source_ordinal,fetched_at)
        VALUES ('00000001',2025,'rel','p','c','subsidiary',80,'r1','SUB',1,CURRENT_TIMESTAMP)
    """))
    conn.execute(text("""
        INSERT INTO group_component_metrics
        (parent_corp_code,effective_year,metric_identity,source_rcept_no,
         entity_key,metric_key,
         amount,unit,numerator_source_rcept_no,numerator_source_table,
         denominator_amount,denominator_unit,denominator_source_rcept_no,
         denominator_source_table,fs_div,period,elimination_basis,share_pct,
         qsc_status,qsc_basis,qsc_evidence_refs_json,qsc_threshold_pct,
         quality_status,fetched_at)
        VALUES ('00000001',2025,'m1','r1','c','assets',10,'KRW','r1','SUB',
        100,'KRW','r2','financials','CFS','2025','before_elimination',10,
        'qsc','asset_share_pct>=10.0','["r1","r2"]',10,'usable',CURRENT_TIMESTAMP)
    """))


def test_build_group_graph_exact_year_is_bulk_and_read_only(temp_engine):
    with temp_engine.begin() as conn:
        _seed_graph(conn)
    count = 0

    def before(*_args):
        nonlocal count
        count += 1

    event.listen(temp_engine, "before_cursor_execute", before)
    graph = build_group_graph("00000001", 2025)
    event.remove(temp_engine, "before_cursor_execute", before)
    assert graph.year == 2025
    assert graph.path_to("Child") == ("Parent", "Child")
    assert graph.metrics[0].qsc_status == "qsc"
    assert count <= 8


def test_build_group_graph_query_count_is_constant_for_large_graphs(
    temp_engine,
):
    with temp_engine.begin() as conn:
        entities = [{
            "parent_corp_code": "00000001", "effective_year": 2025,
            "entity_key": "p" if i == 0 else f"c{i}",
            "original_name": "Parent" if i == 0 else f"Child {i}",
            "normalized_name": "parent" if i == 0 else f"child{i}",
            "resolved_corp_code": (
                "00000001" if i == 0 else f"{i:08d}"
            ),
            "resolution_status": "resolved", "resolution_reason": "corp_code",
            "source_rcept_no": "r1", "source_table": "SUB",
            "source_ordinal": i,
        } for i in range(1001)]
        conn.execute(text("""
            INSERT INTO group_entities
            (parent_corp_code,effective_year,entity_key,original_name,
             normalized_name,resolved_corp_code,resolution_status,
             resolution_reason,source_rcept_no,source_table,source_ordinal,
             fetched_at)
            VALUES
            (:parent_corp_code,:effective_year,:entity_key,:original_name,
             :normalized_name,:resolved_corp_code,:resolution_status,
             :resolution_reason,:source_rcept_no,:source_table,:source_ordinal,
             CURRENT_TIMESTAMP)
        """), entities)
        conn.execute(text("""
            INSERT INTO group_relationships
            (parent_corp_code,effective_year,relationship_key,
             parent_entity_key,child_entity_key,relation_type,ownership_pct,
             source_rcept_no,source_table,source_ordinal,fetched_at)
            VALUES
            ('00000001',2025,:relationship_key,'p',:child_entity_key,
             'subsidiary',80,'r1','SUB',:source_ordinal,CURRENT_TIMESTAMP)
        """), [
            {
                "relationship_key": f"rel{i}",
                "child_entity_key": f"c{i}",
                "source_ordinal": i,
            }
            for i in range(1, 1001)
        ])
    count = 0

    def before(*_args):
        nonlocal count
        count += 1

    event.listen(temp_engine, "before_cursor_execute", before)
    graph = build_group_graph("00000001", 2025)
    event.remove(temp_engine, "before_cursor_execute", before)
    assert len(graph.relationships) == 1000
    assert count <= 8


def test_duplicate_metric_claims_fail_closed_deterministically(temp_engine):
    with temp_engine.begin() as conn:
        _seed_graph(conn)
        conn.execute(text("""
            INSERT INTO group_component_metrics
            (parent_corp_code,effective_year,metric_identity,source_rcept_no,
             entity_key,metric_key,amount,unit,numerator_source_rcept_no,
             numerator_source_table,denominator_amount,denominator_unit,
             denominator_source_rcept_no,denominator_source_table,fs_div,
             period,elimination_basis,share_pct,qsc_status,qsc_basis,
             qsc_evidence_refs_json,qsc_threshold_pct,quality_status,fetched_at)
            VALUES
            ('00000001',2025,'m2','r1','c','assets',20,'KRW','r1','SUB',
             100,'KRW','r2','financials','CFS','2025','before_elimination',
             20,'qsc','asset_share_pct>=10.0','["r1","r2"]',10,'usable',
             CURRENT_TIMESTAMP)
        """))
    graph = build_group_graph("00000001", 2025)
    assert "contradictory_metric_claim" in graph.limitations
    assert not graph.metrics


def test_entity_metric_snapshot_rejects_cross_metric_qsc_disagreement(
    temp_engine,
):
    with temp_engine.begin() as conn:
        _seed_graph(conn)
        conn.execute(text("""
            INSERT INTO group_component_metrics
            (parent_corp_code,effective_year,metric_identity,source_rcept_no,
             entity_key,metric_key,amount,unit,numerator_source_rcept_no,
             numerator_source_table,denominator_amount,denominator_unit,
             denominator_source_rcept_no,denominator_source_table,fs_div,
             period,elimination_basis,share_pct,qsc_status,qsc_basis,
             qsc_evidence_refs_json,qsc_threshold_pct,quality_status,fetched_at)
            VALUES
            ('00000001',2025,'revenue','r1','c','revenue',5,'KRW','r1','SUB',
             100,'KRW','r2','financials','CFS','2025','before_elimination',
             5,'not_qsc','','["r1","r2"]',10,'usable',CURRENT_TIMESTAMP)
        """))
    with pytest.raises(
        GroupGraphUnavailable,
        match="invalid_canonical_graph",
    ):
        build_group_graph("00000001", 2025)


def test_missing_partial_and_nonempty_wal_fail_closed_without_file_creation(monkeypatch, tmp_path):
    import kreports.db.engine as engine_module

    missing = tmp_path / "missing.db"
    missing_engine = create_engine(f"sqlite:///{missing}")
    monkeypatch.setattr(engine_module, "engine", missing_engine)
    with pytest.raises(GroupGraphUnavailable, match="runtime_db_unavailable"):
        build_group_graph("00000001", 2025)
    assert not missing.exists()

    partial = tmp_path / "partial.db"
    partial_engine = create_engine(f"sqlite:///{partial}")
    with partial_engine.begin() as conn:
        conn.execute(text("CREATE TABLE group_entities (entity_key TEXT)"))
    monkeypatch.setattr(engine_module, "engine", partial_engine)
    with pytest.raises(GroupGraphUnavailable, match="missing_schema"):
        build_group_graph("00000001", 2025)

    wal = Path(f"{partial}-wal")
    wal.write_bytes(b"pending")
    with pytest.raises(GroupGraphUnavailable, match="uncheckpointed_wal"):
        build_group_graph("00000001", 2025)


def test_partial_08_schema_missing_accessed_metric_column_fails_closed(
    monkeypatch,
    tmp_path,
):
    import kreports.db.engine as engine_module

    path = tmp_path / "partial08.db"
    partial = create_engine(f"sqlite:///{path}")
    with partial.begin() as conn:
        conn.execute(text("""
            CREATE TABLE group_entities (
              parent_corp_code TEXT, effective_year INTEGER, entity_key TEXT,
              original_name TEXT, normalized_name TEXT,
              resolution_status TEXT, resolution_reason TEXT,
              source_rcept_no TEXT, source_table TEXT, source_ordinal INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE group_relationships (
              parent_corp_code TEXT, effective_year INTEGER,
              relationship_key TEXT, parent_entity_key TEXT,
              child_entity_key TEXT, relation_type TEXT,
              source_rcept_no TEXT, source_table TEXT, source_ordinal INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE group_component_metrics (
              parent_corp_code TEXT, effective_year INTEGER,
              metric_identity TEXT, entity_key TEXT, metric_key TEXT,
              qsc_status TEXT, qsc_basis TEXT, qsc_threshold_pct FLOAT,
              quality_status TEXT
            )
        """))
    partial.dispose()
    monkeypatch.setattr(engine_module, "engine", create_engine(f"sqlite:///{path}"))
    with pytest.raises(GroupGraphUnavailable, match="missing_columns"):
        build_group_graph("00000001", 2025)


def test_authorized_persistence_is_receipt_idempotent_and_auditor_exact_year(
    temp_engine,
):
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import (
        Auditor,
        Company,
        FinancialFactCompact,
        GroupComponentMetricRecord,
        GroupEntityRecord,
    )

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001", stock_code="000001",
                corp_name="Parent", market="KOSPI",
            ),
            Company(
                corp_code="00000002", stock_code="000002",
                corp_name="Child Co., Ltd.", market="KOSPI",
            ),
            Auditor(
                corp_code="00000002", bsns_year=2024, fs_div="CFS",
                auditor_nm="prior auditor", rcept_no="old",
            ),
            FinancialFactCompact(
                corp_code="00000001", bsns_year=2025, fs_div="CFS",
                metric_key="assets", metric_name="assets", amount=1_000_000_000,
            ),
            FinancialFactCompact(
                corp_code="00000001", bsns_year=2025, fs_div="CFS",
                metric_key="revenue", metric_name="revenue", amount=500_000_000,
            ),
        ])
    meta = {
        "corp_code": "00000001", "bsns_year": 2025,
        "rcept_no": "20260301000001",
    }
    affiliates = [{
        "name": "Child", "original_name": "Child Co., Ltd.",
        "ownership_pct": 80.0, "listed_yn": "Y", "assets": "100",
        "revenue": "50", "relation": "subsidiary", "source": "SUB_CMPN",
        "asset_unit": "KRW_MILLION", "revenue_unit": "KRW_MILLION",
        "period": "2025", "fs_div": "CFS",
        "elimination_basis": "before_elimination",
        "denominator_elimination_basis": "before_elimination",
    }]
    first = _persist_group_audit_graph(meta, affiliates=affiliates)
    with get_session() as session:
        session.add(GroupComponentMetricRecord(
            parent_corp_code="00000001", effective_year=2025,
            metric_identity="opaque-identity", source_rcept_no=meta["rcept_no"],
            entity_key="corp:00000002", metric_key="assets",
            qsc_status="undetermined", qsc_basis="",
            qsc_evidence_refs_json="[]", qsc_threshold_pct=10,
            quality_status="partial",
        ))
    second = _persist_group_audit_graph(meta, affiliates=affiliates)
    assert first == second == 5
    with get_session() as session:
        assert session.query(GroupComponentMetricRecord).filter_by(
            source_rcept_no=meta["rcept_no"],
        ).count() == 2
    graph = build_group_graph("00000001", 2025)
    child = next(item for item in graph.entities if item.original_name.startswith("Child"))
    assert child.component_auditor_name is None
    assert child.auditor_gap_reason == "exact_year_component_auditor_missing"
    assert {metric.qsc_status for metric in graph.metrics} == {"undetermined"}
    assert {
        metric.gap_reason for metric in graph.metrics
    } == {"denominator_receipt_unavailable"}
    assert all(
        metric.denominator_source_rcept_no is None
        for metric in graph.metrics
    )
    assert all(metric.source_rcept_no == meta["rcept_no"] for metric in graph.metrics)

    other_meta = dict(meta, rcept_no="20260302000001")
    _persist_group_audit_graph(other_meta, affiliates=affiliates)
    with get_session() as session:
        assert session.query(GroupEntityRecord).count() == 4


def test_ambiguous_exact_name_and_unlisted_entities_stay_unresolved(temp_engine):
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Parent"),
            Company(corp_code="00000002", corp_name="Same Ltd."),
            Company(corp_code="00000003", corp_name="Same Inc."),
        ])
    _persist_group_audit_graph(
        {"corp_code": "00000001", "bsns_year": 2025, "rcept_no": "r1"},
        affiliates=[
            {
                "name": "Same", "original_name": "Same Ltd.",
                "listed_yn": "N", "relation": "subsidiary", "source": "SUB",
            },
            {
                "name": "Unknown", "original_name": "Unknown",
                "listed_yn": "Y", "relation": "subsidiary", "source": "SUB",
            },
        ],
    )
    graph = build_group_graph("00000001", 2025)
    reasons = {
        item.original_name: item.resolution_reason
        for item in graph.entities
    }
    assert reasons["Same Ltd."] == "unlisted"
    assert reasons["Unknown"] == "unmatched_exact_normalized_name"


def test_invalid_or_name_conflicting_explicit_corp_code_never_falls_back(
    temp_engine,
):
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Parent"),
            Company(corp_code="00000002", corp_name="Real Child"),
            Company(corp_code="00000003", corp_name="Canonical Name"),
            Company(corp_code="00000004", corp_name="Fallback"),
            Company(corp_code="BAD", corp_name="Malformed"),
        ])
    _persist_group_audit_graph(
        {"corp_code": "00000001", "bsns_year": 2025, "rcept_no": "r1"},
        affiliates=[
            {
                "name": "Real Child", "original_name": "Real Child",
                "corp_code": "99999999", "source": "SUB",
            },
            {
                "name": "Different", "original_name": "Different",
                "corp_code": "00000003", "source": "SUB",
            },
            {
                "name": "Fallback", "original_name": "Fallback",
                "corp_code": "BAD", "source": "SUB",
            },
            {
                "name": "Real Child", "original_name": "Real Child",
                "corp_code": "00000002", "listed_yn": "N", "source": "SUB",
            },
        ],
    )
    entities = [
        item for item in build_group_graph("00000001", 2025).entities
        if not item.entity_key.startswith("parent:")
    ]
    by_original_and_ordinal = {
        (item.original_name, item.source_ordinal): item for item in entities
    }
    assert by_original_and_ordinal[("Real Child", 0)].resolved_corp_code == (
        "00000002"
    )
    assert by_original_and_ordinal[("Real Child", 0)].resolution_reason == (
        "unique_exact_normalized_name"
    )
    assert by_original_and_ordinal[("Different", 1)].resolved_corp_code == (
        "00000003"
    )
    assert by_original_and_ordinal[("Different", 1)].resolution_reason == (
        "explicit_corp_code_name_conflict"
    )
    assert by_original_and_ordinal[("Fallback", 2)].resolved_corp_code == (
        "00000004"
    )
    assert by_original_and_ordinal[("Real Child", 3)].resolution_reason == (
        "unlisted"
    )
    assert by_original_and_ordinal[("Real Child", 3)].resolved_corp_code is None


def test_group_persistence_rejects_parent_self_component_and_invalid_parent(
    temp_engine,
):
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add(Company(corp_code="00000001", corp_name="Parent"))
    assert _persist_group_audit_graph(
        {"corp_code": "BAD", "bsns_year": 2025, "rcept_no": "bad"},
        affiliates=[{"original_name": "Child", "source": "SUB"}],
    ) == 0
    _persist_group_audit_graph(
        {"corp_code": "00000001", "bsns_year": 2025, "rcept_no": "r1"},
        affiliates=[
            {
                "original_name": "Parent", "corp_code": "00000001",
                "parent_name": "Parent", "source": "SUB",
            },
            {
                "original_name": "Parent", "corp_code": "99999999",
                "parent_name": "Parent", "source": "SUB",
            },
        ],
    )
    graph = build_group_graph("00000001", 2025)
    assert {entity.resolution_reason for entity in graph.entities} == {
        "parent_corp_code", "self_entity_claim",
    }
    assert "self_entity_claim" in graph.limitations
    assert "isolated_entity" in graph.limitations
    assert graph.relationships == ()
    assert graph.metrics == ()
    for invalid in ("BAD", "１２３４５６７８"):
        with pytest.raises(ValueError, match="8 ASCII digits"):
            build_group_graph(invalid, 2025)


def test_group_writer_fails_closed_before_writes_on_partial_schema(temp_engine):
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import Company, GroupEntityRecord

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Parent"),
            GroupEntityRecord(
                parent_corp_code="00000001", effective_year=2025,
                entity_key="sentinel", original_name="Sentinel",
                normalized_name="sentinel", resolution_status="unresolved",
                resolution_reason="unresolved", source_rcept_no="r1",
                source_table="SUB", source_ordinal=0,
            ),
        ])
    with temp_engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE group_component_metrics "
            "DROP COLUMN qsc_evidence_refs_json"
        ))
    assert _persist_group_audit_graph(
        {"corp_code": "00000001", "bsns_year": 2025, "rcept_no": "r1"},
        affiliates=[{"original_name": "Child", "source": "SUB"}],
    ) == 0
    with get_session() as session:
        assert session.query(GroupEntityRecord.entity_key).all() == [
            ("sentinel",),
        ]


def test_quality_a_requires_complete_persisted_qsc_evidence(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import (
        GroupComponentMetricRecord,
        GroupEntityRecord,
        GroupRelationshipRecord,
    )
    from kreports.quality.company_year import _group_audit_status_and_grade

    with get_session() as session:
        session.add_all([
            GroupEntityRecord(
                parent_corp_code="00000001", effective_year=2025,
                entity_key=key, original_name=name, normalized_name=name.lower(),
                resolution_status="resolved", resolution_reason="corp_code",
                source_rcept_no="r1", source_table="SUB", source_ordinal=ordinal,
            )
            for ordinal, (key, name) in enumerate((("p", "Parent"), ("c", "Child")))
        ])
        session.add(GroupRelationshipRecord(
            parent_corp_code="00000001", effective_year=2025,
            relationship_key="rel", parent_entity_key="p", child_entity_key="c",
            relation_type="subsidiary", ownership_pct=80,
            source_rcept_no="r1", source_table="SUB", source_ordinal=1,
        ))
        for metric_key, amount, denominator in (
            ("assets", 10, 100), ("revenue", 5, 100),
        ):
            session.add(GroupComponentMetricRecord(
                parent_corp_code="00000001", effective_year=2025,
                metric_identity=f"r1:SUB:1:{metric_key}", entity_key="c",
                source_rcept_no="r1",
                metric_key=metric_key, amount=amount, unit="KRW",
                numerator_source_rcept_no="r1", numerator_source_table="SUB",
                denominator_amount=denominator, denominator_unit="KRW",
                denominator_source_rcept_no="fin-r1",
                denominator_source_table="financial_facts",
                fs_div="CFS", period="2025",
                elimination_basis="before_elimination", share_pct=amount,
                qsc_status="qsc", qsc_basis="asset_share_pct>=10.0",
                qsc_evidence_refs_json='["fin-r1","r1"]',
                qsc_threshold_pct=10, quality_status="usable",
            ))
    assert _group_audit_status_and_grade("00000001", 2025) == ("available", "A")
    with get_session() as session:
        session.add(GroupEntityRecord(
            parent_corp_code="00000001", effective_year=2025,
            entity_key="isolated", original_name="Isolated",
            normalized_name="isolated", resolution_status="unresolved",
            resolution_reason="unresolved", source_rcept_no="r1",
            source_table="SUB", source_ordinal=9,
        ))
    assert _group_audit_status_and_grade("00000001", 2025) == ("partial", "D")


def test_quality_a_never_combines_relationships_and_metrics_across_receipts(
    temp_engine,
):
    from kreports.db.engine import get_session
    from kreports.db.models import (
        GroupComponentMetricRecord,
        GroupEntityRecord,
        GroupRelationshipRecord,
    )
    from kreports.quality.company_year import _group_audit_status_and_grade

    with get_session() as session:
        session.add_all([
            GroupEntityRecord(
                parent_corp_code="00000001", effective_year=2025,
                entity_key=key, original_name=key, normalized_name=key,
                resolution_status="resolved", resolution_reason="corp_code",
                source_rcept_no="new", source_table="SUB", source_ordinal=i,
            )
            for i, key in enumerate(("p", "c"))
        ])
        session.add(GroupRelationshipRecord(
            parent_corp_code="00000001", effective_year=2025,
            relationship_key="new-rel", parent_entity_key="p",
            child_entity_key="c", relation_type="subsidiary",
            ownership_pct=80, source_rcept_no="new",
            source_table="SUB", source_ordinal=1,
        ))
        for metric_key in ("assets", "revenue"):
            session.add(GroupComponentMetricRecord(
                parent_corp_code="00000001", effective_year=2025,
                metric_identity=f"old:SUB:1:{metric_key}", entity_key="c",
                source_rcept_no="old",
                metric_key=metric_key, amount=10, unit="KRW",
                numerator_source_rcept_no="old", numerator_source_table="SUB",
                denominator_amount=100, denominator_unit="KRW",
                denominator_source_rcept_no="fin-old",
                denominator_source_table="financials", fs_div="CFS",
                period="2025", elimination_basis="before_elimination",
                share_pct=10, qsc_status="qsc",
                    qsc_basis="asset_share_pct>=10.0",
                    qsc_evidence_refs_json='["fin-old","old"]',
                qsc_threshold_pct=10, quality_status="usable",
            ))
    assert _group_audit_status_and_grade("00000001", 2025) == ("partial", "D")


def test_quality_a_accepts_qsc_from_one_complete_crossing_share(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import (
        GroupComponentMetricRecord,
        GroupEntityRecord,
        GroupRelationshipRecord,
    )
    from kreports.quality.company_year import _group_audit_status_and_grade

    with get_session() as session:
        for ordinal, key in enumerate(("p", "c")):
            session.add(GroupEntityRecord(
                parent_corp_code="00000001", effective_year=2025,
                entity_key=key, original_name=key, normalized_name=key,
                resolution_status="resolved", resolution_reason="corp_code",
                source_rcept_no="r1", source_table="SUB",
                source_ordinal=ordinal,
            ))
        session.add(GroupRelationshipRecord(
            parent_corp_code="00000001", effective_year=2025,
            relationship_key="rel", parent_entity_key="p",
            child_entity_key="c", relation_type="subsidiary",
            ownership_pct=80, source_rcept_no="r1",
            source_table="SUB", source_ordinal=1,
        ))
        session.add(GroupComponentMetricRecord(
            parent_corp_code="00000001", effective_year=2025,
            metric_identity="asset", source_rcept_no="r1",
            entity_key="c", metric_key="assets", amount=12, unit="KRW",
            numerator_source_rcept_no="r1", numerator_source_table="SUB",
            denominator_amount=100, denominator_unit="KRW",
            denominator_source_rcept_no="fin-r1",
            denominator_source_table="financials", fs_div="CFS",
            period="2025", elimination_basis="before_elimination",
            share_pct=12, qsc_status="qsc",
            qsc_basis="asset_share_pct>=10.0",
            qsc_evidence_refs_json='["fin-r1","r1"]',
            qsc_threshold_pct=10, quality_status="usable",
        ))
    assert _group_audit_status_and_grade("00000001", 2025) == ("available", "A")


@pytest.mark.parametrize("amount", [None, 999.0])
def test_quality_a_rejects_missing_or_inconsistent_metric_numerator(
    temp_engine,
    amount,
):
    from kreports.db.engine import get_session
    from kreports.db.models import (
        GroupComponentMetricRecord,
        GroupEntityRecord,
        GroupRelationshipRecord,
    )
    from kreports.quality.company_year import _group_audit_status_and_grade

    with get_session() as session:
        for ordinal, key in enumerate(("p", "c")):
            session.add(GroupEntityRecord(
                parent_corp_code="00000001", effective_year=2025,
                entity_key=key, original_name=key, normalized_name=key,
                resolution_status="resolved", resolution_reason="corp_code",
                source_rcept_no="r1", source_table="SUB",
                source_ordinal=ordinal,
            ))
        session.add(GroupRelationshipRecord(
            parent_corp_code="00000001", effective_year=2025,
            relationship_key="rel", parent_entity_key="p",
            child_entity_key="c", relation_type="subsidiary",
            ownership_pct=80, source_rcept_no="r1",
            source_table="SUB", source_ordinal=1,
        ))
        session.add(GroupComponentMetricRecord(
            parent_corp_code="00000001", effective_year=2025,
            metric_identity="asset", source_rcept_no="r1",
            entity_key="c", metric_key="assets", amount=amount, unit="KRW",
            numerator_source_rcept_no="r1", numerator_source_table="SUB",
            denominator_amount=100, denominator_unit="KRW",
            denominator_source_rcept_no="fin-r1",
            denominator_source_table="financials", fs_div="CFS",
            period="2025", elimination_basis="before_elimination",
            share_pct=12, qsc_status="qsc",
            qsc_basis="asset_share_pct>=10.0",
            qsc_evidence_refs_json='["fin-r1","r1"]',
            qsc_threshold_pct=10, quality_status="usable",
        ))
    assert _group_audit_status_and_grade(
        "00000001", 2025,
    ) == ("partial", "D")


def test_component_auditor_conflict_is_withheld_order_independently(temp_engine):
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import Auditor, Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Parent"),
            Company(corp_code="00000002", corp_name="Child"),
            Auditor(
                corp_code="00000002", bsns_year=2025, fs_div="CFS",
                auditor_nm="Auditor A", rcept_no="a",
            ),
            Auditor(
                corp_code="00000002", bsns_year=2025, fs_div="OFS",
                auditor_nm="Auditor B", rcept_no="b",
            ),
        ])
    _persist_group_audit_graph(
        {"corp_code": "00000001", "bsns_year": 2025, "rcept_no": "r1"},
        affiliates=[{
            "name": "Child", "original_name": "Child", "source": "SUB",
        }],
    )
    child = next(
        item for item in build_group_graph("00000001", 2025).entities
        if item.original_name == "Child"
    )
    assert child.component_auditor_name is None
    assert child.component_auditor_fs_div is None
    assert child.auditor_gap_reason == "component_auditor_conflict"
    with get_session() as session:
        ofs = session.query(Auditor).filter_by(
            corp_code="00000002", bsns_year=2025, fs_div="OFS",
        ).one()
        ofs.auditor_nm = "Auditor A"
    _persist_group_audit_graph(
        {"corp_code": "00000001", "bsns_year": 2025, "rcept_no": "r2"},
        affiliates=[{
            "name": "Child", "original_name": "Child", "source": "SUB",
        }],
    )
    corrected = next(
        item for item in build_group_graph("00000001", 2025).entities
        if item.original_name == "Child"
    )
    assert corrected.component_auditor_name == "Auditor A"
    assert corrected.component_auditor_fs_div == "CFS"
    assert corrected.component_auditor_rcept_no == "a"


def test_canonical_only_graph_is_exposed_without_legacy_matrix(temp_engine):
    from kreports.analysis.group_audit import get_subsidiary_auditors
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    with get_session() as session:
        session.add_all([
            Company(
                corp_code="00000001", stock_code="000001",
                corp_name="Parent",
            ),
            Company(corp_code="00000002", corp_name="Child"),
        ])
    _persist_group_audit_graph(
        {"corp_code": "00000001", "bsns_year": 2025, "rcept_no": "r1"},
        affiliates=[{
            "name": "Child", "original_name": "Child", "source": "SUB",
        }],
    )
    result = get_subsidiary_auditors("000001", slim=False)
    assert result["bsns_year"] == 2025
    assert result["group_graph"]["year"] == 2025
    assert result["subsidiaries"][0]["name"] == "Child"
    assert result["group_graph"]["entities"][0]["parent_is_root"] is True
    assert result["data_quality"]["source"] == "canonical_group_audit_graph"


def test_newer_canonical_year_is_not_suppressed_by_stale_legacy_matrix(
    temp_engine,
):
    from kreports.analysis.group_audit import get_subsidiary_auditors
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import BusinessAffiliateAuditor, Company

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Parent"),
            Company(corp_code="00000002", corp_name="Child"),
            BusinessAffiliateAuditor(
                parent_corp_code="00000001",
                parent_rcept_no="20250301000001",
                bsns_year=2024, name="Stale Child",
                corp_code="00000002", source="SUB", ordinal=0,
            ),
        ])
    _persist_group_audit_graph(
        {
            "corp_code": "00000001", "bsns_year": 2025,
            "rcept_no": "20260301000001",
        },
        affiliates=[{
            "name": "Child", "original_name": "Child", "source": "SUB",
        }],
    )
    result = get_subsidiary_auditors("00000001", slim=False)
    assert result["bsns_year"] == 2025
    assert result["group_graph"]["year"] == 2025
    assert result["subsidiaries"][0]["name"] == "Child"
    assert result["data_quality"]["source"] == "canonical_group_audit_graph"


def test_legacy_auditor_is_exact_year_and_conflicts_fail_closed(temp_engine):
    from kreports.analysis.group_audit import get_subsidiary_auditors
    from kreports.db.engine import get_session
    from kreports.db.models import (
        Auditor,
        BusinessAffiliateAuditor,
        Company,
    )

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Parent"),
            Company(corp_code="00000002", corp_name="Child"),
            BusinessAffiliateAuditor(
                parent_corp_code="00000001",
                parent_rcept_no="20250301000001",
                bsns_year=2024, name="Child", corp_code="00000002",
                auditor_nm="Old Auditor", auditor_year=2023,
                auditor_fs_div="CFS", source="SUB", ordinal=0,
            ),
        ])
    result = get_subsidiary_auditors("00000001", slim=False)
    assert result["subsidiaries"][0]["auditor"] is None
    assert result["subsidiaries"][0]["auditor_gap_reason"] == (
        "component_auditor_year_mismatch"
    )

    with get_session() as session:
        matrix = session.query(BusinessAffiliateAuditor).one()
        matrix.auditor_nm = "Auditor A"
        matrix.auditor_year = 2024
        session.add_all([
            Auditor(
                corp_code="00000002", bsns_year=2024, fs_div="CFS",
                auditor_nm="Auditor A", rcept_no="a",
            ),
            Auditor(
                corp_code="00000002", bsns_year=2024, fs_div="OFS",
                auditor_nm="Auditor B", rcept_no="b",
            ),
        ])
    conflicted = get_subsidiary_auditors("00000001", slim=False)
    assert conflicted["subsidiaries"][0]["auditor"] is None
    assert conflicted["subsidiaries"][0]["auditor_gap_reason"] == (
        "component_auditor_conflict"
    )


def test_group_persistence_resolves_direct_parents_independent_of_row_order(
    temp_engine,
):
    from kreports.collector.report_document_collector import (
        _persist_group_audit_graph,
    )
    from kreports.db.engine import get_session
    from kreports.db.models import Company, GroupRelationshipRecord

    with get_session() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="Parent"),
            Company(corp_code="00000002", corp_name="Middle"),
            Company(corp_code="00000003", corp_name="Leaf"),
        ])
    rows = [
        {
            "name": "Middle", "original_name": "Middle",
            "parent_name": "Parent", "relation": "subsidiary", "source": "SUB",
        },
        {
            "name": "Leaf", "original_name": "Leaf",
            "parent_name": "Middle", "relation": "subsidiary", "source": "SUB",
        },
    ]
    for receipt, claims in (("r1", rows), ("r2", list(reversed(rows)))):
        _persist_group_audit_graph(
            {
                "corp_code": "00000001", "bsns_year": 2025,
                "rcept_no": receipt,
            },
            affiliates=claims,
        )
    with get_session() as session:
        leaf_parents = {
            receipt: parent
            for receipt, parent in (
                session.query(
                    GroupRelationshipRecord.source_rcept_no,
                    GroupRelationshipRecord.parent_entity_key,
                )
                .filter(GroupRelationshipRecord.child_entity_key == "corp:00000003")
                .all()
            )
        }
    assert leaf_parents == {
        "r1": "corp:00000002",
        "r2": "corp:00000002",
    }


def test_parser_preserves_same_name_claims_with_distinct_direct_parents():
    from kreports.processor.subsidiary_parser import extract_subsidiaries

    content = """
    <TABLE-GROUP ACLASS="SUB_CMPN"><TBODY>
      <TR><TD ACODE="CRP_NM">Same Ltd.</TD><TD ACODE="PARENT_NM">Hold A</TD></TR>
      <TR><TD ACODE="CRP_NM">Same Ltd.</TD><TD ACODE="PARENT_NM">Hold B</TD></TR>
    </TBODY></TABLE-GROUP>
    """
    rows = extract_subsidiaries(content)
    assert [(row["name"], row["parent_name"]) for row in rows] == [
        ("Same Ltd.", "Hold A"), ("Same Ltd.", "Hold B"),
    ]


def test_answer_pack_keeps_all_graph_rows_but_limits_mermaid_nodes():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    rows = [
        {
            "entity_key": f"e{i}", "parent_entity_key": "p",
            "name": f'Child | {i} "quoted"', "relation": "subsidiary",
            "ownership_pct": 80, "qsc_status": "undetermined",
            "source_rcept_no": f"r{i}",
        }
        for i in range(12)
    ]
    result = {
        "corp_code": "00000001", "bsns_year": 2025,
        "subsidiaries": [], "count": 12, "total": 12,
        "group_graph": {
            "parent_name": "Parent", "year": 2025,
            "entities": rows, "limitations": [], "truncated": False,
        },
        "data_quality": {"status": "usable"},
    }
    pack = build_answer_pack("get_subsidiary_auditors", result)
    assert len(pack["tables"][0]["rows"]) == 12
    assert "graph_nodes_omitted:4" in pack["warnings"]
    assert "4개 노드는 가독성을 위해 생략" in pack["diagrams"][0]["definition"]
    rendered = render_answer("get_subsidiary_auditors", result)
    assert "Child \\| 11" in rendered
    assert "표에는 반환 행 전체" in rendered


def test_node_limit_never_rewires_child_of_omitted_parent_to_root():
    from kreports.mcp.answer_pack import build_answer_pack

    rows = [
        {
            "entity_key": "leaf", "parent_entity_key": "middle",
            "name": "Leaf", "relation": "subsidiary",
            "source_rcept_no": "r",
        },
        *[
            {
                "entity_key": f"root-child-{i}", "parent_entity_key": "root",
                "name": f"Root child {i}", "relation": "subsidiary",
                "source_rcept_no": "r",
            }
            for i in range(8)
        ],
        {
            "entity_key": "middle", "parent_entity_key": "root",
            "name": "Middle", "relation": "subsidiary",
            "source_rcept_no": "r",
        },
    ]
    result = {
        "bsns_year": 2025, "subsidiaries": [],
        "group_graph": {"entities": rows, "truncated": False},
        "data_quality": {"status": "usable"},
    }
    definition = build_answer_pack(
        "get_subsidiary_auditors", result,
    )["diagrams"][0]["definition"]
    leaf_lines = [line for line in definition.splitlines() if "Leaf" in line]
    assert not leaf_lines or all(
        not line.strip().startswith("P -->") for line in leaf_lines
    )


def test_mermaid_omits_child_whose_explicit_parent_gap_is_not_rendered():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    rows = [{
        "entity_key": "child",
        "parent_entity_key": "parent-gap:r:table:0",
        "parent_is_root": False,
        "name": "Child",
        "relation": "subsidiary",
        "source_rcept_no": "r",
    }]
    result = {
        "bsns_year": 2025,
        "subsidiaries": [],
        "group_graph": {"entities": rows, "truncated": False},
        "data_quality": {"status": "usable"},
    }
    pack = build_answer_pack("get_subsidiary_auditors", result)
    definition = pack["diagrams"][0]["definition"]
    assert "Child" not in definition
    assert "graph_nodes_omitted:1" in pack["warnings"]
    rendered = render_answer("get_subsidiary_auditors", result)
    assert "P -->" not in rendered
    assert "1개 노드를 생략" in rendered


def test_graph_rendering_escapes_hostile_html_and_diagram_metacharacters():
    from kreports.mcp.answer_pack import build_answer_pack
    from kreports.mcp.renderers import render_answer

    hostile = '\"><img src=x onerror=alert(1)> & [X] | {Y}\nnext'
    row = {
        "entity_key": "child", "parent_entity_key": "root",
        "parent_is_root": True, "name": hostile, "relation": hostile,
        "source_rcept_no": "r",
    }
    result = {
        "subject": {"corp_name": hostile},
        "bsns_year": 2025, "subsidiaries": [],
        "group_graph": {"entities": [row], "truncated": False},
        "data_quality": {"status": "usable"},
    }
    definition = build_answer_pack(
        "get_subsidiary_auditors", result,
    )["diagrams"][0]["definition"]
    rendered = render_answer("get_subsidiary_auditors", result)
    for output in (definition, rendered):
        assert "<img" not in output
        assert "&lt;img" in output
        assert "&amp;" in output
    assert "&#91;X&#93;" in definition
    assert "<br/>next" in definition
    assert "\\|" in rendered
