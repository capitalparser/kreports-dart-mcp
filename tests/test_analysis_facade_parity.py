"""Behavior and import contracts for the domain-decomposed analysis facade."""
from __future__ import annotations

import inspect
import json
import re
import subprocess
import sys
import ast

import pytest

from kreports.analysis import api
from kreports.analysis import (
    audit_reporting,
    company_profile,
    financial_analysis,
    group_audit,
    peer_benchmarks,
)


DOMAIN_EXPORTS = {
    company_profile: (
        "search_company",
        "get_company",
        "resolve_corp_code",
        "get_business_overview",
    ),
    financial_analysis: (
        "get_financial_snapshot",
        "get_investor_signals",
        "score_going_concern",
        "detect_restatement",
        "get_quality_of_earnings_pack",
        "get_dcf_input_candidates",
        "build_dcf_model_pack",
        "search_disclosure_events",
    ),
    audit_reporting: (
        "get_accounting_policy",
        "get_audit_history",
        "get_audit_report_sections",
        "search_audit_report_matters",
        "search_audit_procedures",
        "get_kam_lifecycle",
        "get_accounting_policy_changes",
    ),
    peer_benchmarks: (
        "get_industry_aggregates",
        "compare_to_industry",
        "compare_to_industry_multi",
        "select_peer_group",
        "compare_peer_audit_fees",
        "compare_peer_risk_profile",
        "compare_peer_accounting_policies",
        "compare_peer_kam_topics",
        "compare_peer_audit_report_matters",
        "compare_peer_audit_procedures",
        "estimate_audit_hours_proxy",
        "build_audit_acceptance_pack",
        "get_industry_audit_landscape",
    ),
    group_audit: ("get_subsidiary_auditors",),
}


@pytest.mark.parametrize(
    ("domain", "export"),
    [
        (domain, export)
        for domain, exports in DOMAIN_EXPORTS.items()
        for export in exports
    ],
)
def test_facade_reexports_exact_domain_object(domain, export):
    assert getattr(api, export) is getattr(domain, export)


def test_facade_is_small_and_contains_no_domain_sql():
    source = inspect.getsource(api)
    assert len(source.splitlines()) < 500
    assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", source, re.IGNORECASE)
    assert "sqlalchemy" not in source


def test_facade_public_surface_matches_base_manifest_with_exact_identity():
    manifest_path = (
        __import__("pathlib").Path(__file__).parent
        / "fixtures"
        / "analysis_api_public_symbols.json"
    )
    manifest = json.loads(manifest_path.read_text())
    expected = manifest["symbols"]

    assert set(api.__all__) == set(expected)
    for symbol, module_name in expected.items():
        owner = __import__(module_name, fromlist=[symbol])
        assert getattr(api, symbol) is getattr(owner, symbol), symbol


@pytest.mark.parametrize(
    "module_name",
    [
        "kreports.analysis.company_profile",
        "kreports.analysis.financial_analysis",
        "kreports.analysis.audit_reporting",
        "kreports.analysis.peer_benchmarks",
        "kreports.analysis.group_audit",
        "kreports.analysis.search_adapter",
        "kreports.analysis.api",
        "kreports.mcp.catalog",
        "kreports.mcp.server",
        "kreports.mcp.tools",
    ],
)
def test_analysis_and_mcp_modules_import_in_fresh_interpreter(module_name):
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import importlib; importlib.import_module({module_name!r})",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**dict(__import__("os").environ), "PYTHONPATH": "."},
    )
    assert completed.returncode == 0, completed.stderr


def test_domain_modules_do_not_import_private_names_from_other_domains():
    domain_modules = {
        "company_profile",
        "financial_analysis",
        "audit_reporting",
        "peer_benchmarks",
        "group_audit",
    }
    analysis_dir = __import__("pathlib").Path(api.__file__).parent

    violations = []
    for domain in sorted(domain_modules):
        tree = ast.parse((analysis_dir / f"{domain}.py").read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            imported_domain = node.module.removeprefix("kreports.analysis.")
            if imported_domain not in domain_modules or imported_domain == domain:
                continue
            for imported in node.names:
                if imported.name.startswith("_"):
                    violations.append(
                        f"{domain} imports {imported.name} from {imported_domain}"
                    )
    assert violations == []


def test_broad_dataset_search_has_an_explicit_read_adapter_owner():
    from kreports.analysis import search_adapter

    assert api.search_dataset is search_adapter.search_dataset


def test_domain_outputs_match_base_commit_golden(temp_engine):
    from tests.fixtures.analysis_facade_golden_seed import (
        collect_analysis_results,
        seed_analysis_database,
    )

    seed_analysis_database()
    golden_path = (
        __import__("pathlib").Path(__file__).parent
        / "fixtures"
        / "analysis_facade_base_0920b57.json"
    )
    expected = json.loads(golden_path.read_text())

    assert collect_analysis_results(api) == expected
