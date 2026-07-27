from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


GOLDEN_PATH = Path(__file__).parent / "golden" / "companies.yaml"
EXPECTED_IDS = {
    "samsung_five_year_investor",
    "sk_hynix_group_qsc",
    "daewon_five_year_dcf",
    "modified_opinion",
    "multiple_kam",
    "incomplete_company",
}


def _load_cases() -> list[dict]:
    payload = json.loads(GOLDEN_PATH.read_text())
    assert payload["contract_version"] == "1.0"
    return payload["cases"]


def _fingerprint(path: Path) -> tuple:
    values = []
    for candidate in (
        path,
        path.with_name(f"{path.name}-wal"),
        path.with_name(f"{path.name}-shm"),
    ):
        if not candidate.exists():
            values.append((candidate.name, None))
            continue
        stat = candidate.stat()
        values.append(
            (
                candidate.name,
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                hashlib.sha256(candidate.read_bytes()).hexdigest(),
            )
        )
    return tuple(values)


def test_six_declarative_golden_cases_cover_stable_semantic_boundaries():
    cases = _load_cases()
    by_id = {case["id"]: case for case in cases}

    assert set(by_id) == EXPECTED_IDS
    assert by_id["samsung_five_year_investor"]["years"] == 5
    assert by_id["daewon_five_year_dcf"]["years"] == 5
    assert "qsc" in by_id["sk_hynix_group_qsc"]["packs"]
    assert "opinion" in by_id["modified_opinion"]["required_shapes"]
    assert "items" in by_id["multiple_kam"]["required_shapes"]
    assert (
        "missing is never promoted to usable"
        in by_id["incomplete_company"]["stable_semantics"]
    )
    for case in cases:
        assert case["required_shapes"]
        assert case["stable_semantics"]
        assert case["provenance"] in {
            "dart_or_explicit_source_access_limitation",
            "explicit_source_access_limitation",
        }
        assert all("amount" not in field for field in case["required_shapes"])


def _seed_dispatch_fixture(db_path: Path) -> None:
    from kreports.db.models import (
        Auditor,
        Base,
        BusinessAffiliateAuditor,
        Company,
        Financial,
        FinancialFactCompact,
        ReportSection,
    )

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session.begin() as session:
        companies = [
            Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI", induty_code="264"),
            Company(corp_code="00164779", stock_code="000660", corp_name="SK하이닉스", market="KOSPI", induty_code="261"),
            Company(corp_code="00111855", stock_code="003220", corp_name="대원제약", market="KOSPI", induty_code="212"),
            Company(corp_code="90000001", stock_code="900001", corp_name="수정의견픽스처", market="KOSPI", induty_code="264"),
            Company(corp_code="90000002", stock_code="900002", corp_name="복수KAM픽스처", market="KOSPI", induty_code="264"),
            Company(corp_code="90000003", stock_code="900003", corp_name="불완전기업픽스처", market="KOSPI", induty_code="264"),
            Company(corp_code="90000004", stock_code="900004", corp_name="OFS폴백픽스처", market="KOSPI", induty_code="264"),
            Company(corp_code="80000001", stock_code="800001", corp_name="삼성피어A", market="KOSPI", induty_code="264"),
            Company(corp_code="80000002", stock_code="800002", corp_name="삼성피어B", market="KOSPI", induty_code="264"),
        ]
        session.add_all(companies)
        for corp_code in ("00126380", "00164779", "00111855", "80000001", "80000002"):
            for year in range(2021, 2026):
                session.add(
                    Financial(
                        corp_code=corp_code,
                        year=year,
                        quarter=4,
                        fs_div="CFS",
                        revenue=100_000_000_000 + year,
                        operating_profit=10_000_000_000,
                        net_income=8_000_000_000,
                        total_assets=200_000_000_000,
                        total_debt=80_000_000_000,
                        total_equity=120_000_000_000,
                        operating_cf=12_000_000_000,
                        account_map_confidence=1.0,
                    )
                )
        session.add(
            Financial(
                corp_code="90000004",
                year=2025,
                quarter=4,
                fs_div="OFS",
                revenue=10_000_000_000,
                operating_profit=1_000_000_000,
                net_income=800_000_000,
                total_assets=20_000_000_000,
                total_debt=8_000_000_000,
                total_equity=12_000_000_000,
                operating_cf=1_200_000_000,
                account_map_confidence=1.0,
            )
        )
        for metric_key in (
            "revenue",
            "operating_profit",
            "depreciation_amortization",
            "purchase_ppe",
            "purchase_intangible_assets",
            "trade_receivables",
            "inventories",
            "trade_payables",
            "cash_and_equivalents",
            "interest_bearing_debt",
        ):
            session.add(
                FinancialFactCompact(
                    corp_code="00111855",
                    bsns_year=2025,
                    fs_div="CFS",
                    metric_key=metric_key,
                    metric_name=metric_key,
                    amount=100_000_000,
                    source_account_id=f"fixture_{metric_key}",
                    source_account_nm=metric_key,
                )
            )
        session.add(
            Auditor(
                corp_code="90000001",
                bsns_year=2025,
                fs_div="CFS",
                auditor_nm="검증회계법인",
                audit_opinion="한정",
                rcept_no="20260331000001",
                consecutive_years=1,
            )
        )
        for ordinal, topic in enumerate(("수익인식", "재고자산 평가")):
            session.add(
                ReportSection(
                    rcept_no="20260331000002",
                    corp_code="90000002",
                    bsns_year=2025,
                    source_type="audit_report",
                    section_key="kam",
                    section_title=topic,
                    body_text=(
                        f"{topic}은 유의적인 위험으로 핵심감사사항으로 "
                        "결정하였고 감사절차를 수행했습니다."
                    ),
                    body_hash=f"kam-{ordinal}",
                    body_length=30,
                    ordinal=ordinal,
                )
            )
        for ordinal, name in enumerate(("SK자회사A", "SK자회사B")):
            session.add(
                BusinessAffiliateAuditor(
                    parent_corp_code="00164779",
                    parent_rcept_no="20260331000003",
                    bsns_year=2025,
                    name=name,
                    relation="subsidiary",
                    ownership_pct=80.0,
                    listed_yn="N",
                    business="반도체",
                    assets=str(30_000 - ordinal),
                    auditor_nm="검증회계법인",
                    audit_opinion="적정",
                    auditor_year=2025,
                    ordinal=ordinal,
                )
            )
    engine.dispose()


def test_each_golden_case_executes_fixture_backed_tools_and_asserts_semantics(
    tmp_path,
):
    from kreports.release_artifact import execute_golden_contracts

    db_path = tmp_path / "golden.db"
    _seed_dispatch_fixture(db_path)

    result = execute_golden_contracts(db_path)

    assert result["passed"] is True
    assert set(result["cases"]) == EXPECTED_IDS
    assert result["cases"]["samsung_five_year_investor"]["covered_years"] == 5
    assert result["cases"]["samsung_five_year_investor"]["cfs_preferred"]
    assert result["cases"]["samsung_five_year_investor"][
        "ofs_fallback_explicit"
    ]
    assert result["cases"]["samsung_five_year_investor"][
        "provenance_or_limitation"
    ]
    assert all(
        result["cases"]["samsung_five_year_investor"][
            "provenance_by_pack"
        ].values()
    )
    assert result["cases"]["sk_hynix_group_qsc"]["entity_count"] >= 2
    assert result["cases"]["sk_hynix_group_qsc"]["relationship_count"] >= 2
    assert result["cases"]["sk_hynix_group_qsc"][
        "qsc_denominator_identity"
    ]
    assert result["cases"]["daewon_five_year_dcf"]["actuals_assumptions_separate"]
    assert result["cases"]["daewon_five_year_dcf"]["five_year_mechanics"]
    assert result["cases"]["daewon_five_year_dcf"]["actuals_source_bound"]
    assert result["cases"]["daewon_five_year_dcf"][
        "judgment_limitations"
    ]
    assert result["cases"]["modified_opinion"]["modified_opinion_preserved"]
    assert result["cases"]["modified_opinion"]["receipt_preserved"]
    assert result["cases"]["modified_opinion"][
        "provenance_or_limitation"
    ]
    assert result["cases"]["multiple_kam"]["kam_count"] >= 2
    assert result["cases"]["multiple_kam"]["receipt_ordinal_identity"]
    assert result["cases"]["multiple_kam"]["reason_and_procedure_shapes"]
    assert result["cases"]["incomplete_company"]["quality"] in {
        "limited",
        "missing",
    }
    assert result["cases"]["incomplete_company"]["missing_fields_shape"]
    assert result["cases"]["incomplete_company"]["explicit_limitations"]


def test_qsc_golden_identity_rejects_tampered_computed_share():
    from kreports.release_artifact import _qsc_denominator_identity

    result = {
        "qsc_criterion": {
            "threshold_pct": 10.0,
            "basis": (
                "asset_share_pct >= 10.0 OR "
                "revenue_share_pct >= 10.0"
            ),
        },
        "consolidated_totals": {
            "assets_amount_m": 1_000.0,
            "revenue_amount_m": 500.0,
        },
        "subsidiaries": [
            {
                "asset_amount_m": 120.0,
                "asset_share_pct": 12.0,
                "revenue_amount_m": 40.0,
                "revenue_share_pct": 8.0,
                "qsc_status": "qsc",
                "is_qsc": True,
            }
        ],
    }

    assert _qsc_denominator_identity(result) is True
    result["subsidiaries"][0]["asset_share_pct"] = 11.9
    assert _qsc_denominator_identity(result) is False


def test_golden_provenance_rejects_arbitrary_warning_or_limitation():
    from kreports.release_artifact import _has_public_provenance

    arbitrary = SimpleNamespace(
        evidence=[],
        data_quality=SimpleNamespace(
            status="limited",
            limitations=["model assumptions require review"],
        ),
        warnings=["generic warning"],
    )
    explicit_gap = SimpleNamespace(
        evidence=[],
        data_quality=SimpleNamespace(
            status="missing",
            limitations=["로컬 캐시에 확인 가능한 데이터가 없습니다."],
        ),
        warnings=[],
    )

    assert _has_public_provenance(arbitrary) is False
    assert _has_public_provenance(explicit_gap) is True


def test_missing_dcf_never_claims_source_actuals_were_found(tmp_path):
    from kreports.release_artifact import (
        _bound_explicit_runtime,
        _safe_existing_db_path,
    )
    from kreports.mcp.dispatch import dispatch_tool

    db_path = tmp_path / "golden.db"
    _seed_dispatch_fixture(db_path)
    with _bound_explicit_runtime(_safe_existing_db_path(db_path)):
        envelope = dispatch_tool(
            "build_dcf_model_pack",
            {
                "company": "900003",
                "base_year": 2025,
                "revenue_growth": 0.03,
                "operating_margin": 0.1,
                "tax_rate": 0.22,
                "da_to_revenue": 0.03,
                "capex_to_revenue": 0.04,
                "nwc_to_revenue": 0.1,
                "wacc": 0.09,
                "terminal_growth": 0.02,
            },
        )

    assert envelope.data_quality.status == "missing"
    assert envelope.confirmed_facts == []
    assert "source actuals를" not in envelope.answer


def test_live_regression_is_opt_in_by_default():
    if os.environ.get("KREPORTS_RUN_LIVE_DB_TESTS") == "1":
        pytest.skip("live regression is exercised by the opt-in test")
    pytest.skip(
        "set KREPORTS_RUN_LIVE_DB_TESTS=1 for immutable local DB regression"
    )


@pytest.mark.skipif(
    os.environ.get("KREPORTS_RUN_LIVE_DB_TESTS") != "1",
    reason="live DB regression is explicit opt-in",
)
def test_live_golden_company_shapes_are_read_immutably_without_dart_calls():
    from kreports.config import settings

    prefix = "sqlite:///"
    assert settings.db_url.startswith(prefix)
    db_path = Path(settings.db_url.removeprefix(prefix)).resolve(strict=True)
    before = _fingerprint(db_path)
    connection = sqlite3.connect(
        f"{db_path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT stock_code, corp_name FROM companies "
            "WHERE stock_code IN ('005930', '000660', '003220') "
            "ORDER BY stock_code"
        ).fetchall()
        assert {row[0] for row in rows} == {"005930", "000660", "003220"}
        assert connection.execute(
            "SELECT COUNT(*) FROM financials "
            "WHERE corp_code IN ("
            "SELECT corp_code FROM companies "
            "WHERE stock_code IN ('005930', '000660', '003220'))"
        ).fetchone()[0] > 0
        assert connection.execute(
            "SELECT COUNT(*) FROM company_year_quality "
            "WHERE corp_code IN ("
            "SELECT corp_code FROM companies "
            "WHERE stock_code IN ('005930', '000660', '003220'))"
        ).fetchone()[0] > 0
    finally:
        connection.close()
    assert _fingerprint(db_path) == before
