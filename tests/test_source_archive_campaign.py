from __future__ import annotations

import json
from datetime import date

import pytest

from kreports.db.models import Company, Disclosure


class _Archive:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def archive_bytes(self, *, data: bytes, extension: str, metadata: dict[str, str]):
        del extension
        assert metadata["source_receipt"]
        assert metadata["source_uri"]
        self.payloads.append(data)
        return {"sha256": "a" * 64, "byte_length": len(data)}


def _seed_annual_disclosures(temp_engine) -> None:
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=temp_engine)()
    try:
        session.add_all(
            [
                Company(corp_code="00126380", corp_name="테스트"),
                Company(corp_code="00126381", corp_name="테스트2"),
            ]
        )
        for corp_code in ("00126380", "00126381"):
            for year in range(2021, 2026):
                session.add(Disclosure(
                    rcept_no=f"{year + 1}0331{corp_code[-6:]}",
                    corp_code=corp_code,
                    corp_name="테스트",
                    disc_date=date(year + 1, 3, 31),
                    disc_type="A",
                    report_nm=f"사업보고서 ({year}.12)",
                ))
        # The correction is the canonical latest annual identity for 2024.
        session.add(Disclosure(
            rcept_no="20250401126380",
            corp_code="00126380",
            corp_name="테스트",
            disc_date=date(2025, 4, 1),
            disc_type="A",
            report_nm="[기재정정]사업보고서 (2024.12)",
        ))
        session.commit()
    finally:
        session.close()


def test_plan_uses_canonical_latest_anchor_and_stable_company_shards(temp_engine):
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import build_source_archive_plan

    _seed_annual_disclosures(temp_engine)
    with sessionmaker(bind=temp_engine)() as session:
        plan = build_source_archive_plan(session, years=range(2021, 2026))

    corrected = next(
        target
        for target in plan.targets
        if target.corp_code == "00126380" and target.bsns_year == 2024
    )
    assert corrected.source_receipt == "20250401126380"
    assert len({target.shard for target in plan.targets if target.corp_code == "00126380"}) == 1
    assert plan.shard_count == 64
    assert plan.target_manifest["target_digest"]


def test_dry_run_makes_no_fetch_or_archive_writes(temp_engine, tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    with sessionmaker(bind=temp_engine)() as session:
        plan = campaign.build_source_archive_plan(session, years=range(2021, 2026))

    monkeypatch.setattr(
        campaign,
        "fetch_document_zip_files",
        lambda _receipt: pytest.fail("dry run must not fetch DART sources"),
    )
    plan = plan.with_state_dir(tmp_path / "campaign")
    report = campaign.run_source_archive_shard(plan, plan.targets[0].shard, _Archive(), apply=False)

    assert report.apply is False
    assert report.outcomes == ()
    assert not plan.state_dir.exists()


def test_partial_source_never_emits_committed_marker(temp_engine, tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    with sessionmaker(bind=temp_engine)() as session:
        plan = campaign.build_source_archive_plan(session, years=[2024])
    plan = plan.with_state_dir(tmp_path / "campaign")
    shard = plan.targets[0].shard
    monkeypatch.setattr(campaign, "fetch_document_zip_files", lambda _receipt: {})

    report = campaign.run_source_archive_shard(plan, shard, _Archive(), apply=True)

    assert report.status == "partial"
    assert not (plan.state_dir / f"shard-{shard:02d}" / "COMMITTED.json").exists()
    rows = [
        json.loads(line)
        for line in (plan.state_dir / f"shard-{shard:02d}" / "outcomes.jsonl").read_text().splitlines()
    ]
    assert any(row["status"] == "partial_source" for row in rows)


def test_one_asset_at_a_time_archives_raw_and_generic_parse(temp_engine, tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    with sessionmaker(bind=temp_engine)() as session:
        plan = campaign.build_source_archive_plan(session, years=[2024])
    plan = plan.with_state_dir(tmp_path / "campaign")
    shard = plan.targets[0].shard
    monkeypatch.setattr(campaign, "fetch_document_zip_files", lambda _receipt: {
        "main.xml": "<DOCUMENT><P>사업보고서 본문</P></DOCUMENT>",
        "audit.xml": "<DOCUMENT><P>감사보고서 첨부</P></DOCUMENT>",
    })
    archive = _Archive()

    report = campaign.run_source_archive_shard(plan, shard, archive, apply=True)

    assert report.status == "complete"
    # Each raw asset plus its source-bound generic structure is immutable evidence.
    assert len(archive.payloads) == 4
    assert {outcome["status"] for outcome in report.outcomes} >= {
        "discovered", "archived_verified", "generically_parsed", "structurally_complete"
    }
    assert (plan.state_dir / f"shard-{shard:02d}" / "COMMITTED.json").is_file()


def test_apply_fails_closed_before_any_state_write_in_readonly_runtime(temp_engine, tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    with sessionmaker(bind=temp_engine)() as session:
        plan = campaign.build_source_archive_plan(session, years=[2024])
    monkeypatch.setenv("KREPORTS_RUNTIME_MODE", "readonly")

    with pytest.raises(RuntimeError, match="requires collector mode"):
        campaign.run_source_archive_shard(
            plan.with_state_dir(tmp_path / "campaign"), plan.targets[0].shard, _Archive(), apply=True
        )

    assert not (tmp_path / "campaign").exists()


def test_plan_rejects_configured_runtime_database(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import (
        SourceArchiveCampaignError,
        build_source_archive_plan,
    )

    database = tmp_path / "runtime.db"
    engine = create_engine(f"sqlite:///{database}")
    from kreports.db.models import Base

    Base.metadata.create_all(engine)
    monkeypatch.setattr("kreports.config.settings.db_url", f"sqlite:///{database}")
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(SourceArchiveCampaignError, match="non-runtime collector"):
            build_source_archive_plan(session, years=[2024])


def test_cli_exposes_explicit_source_archive_commands():
    from typer.testing import CliRunner
    from kreports.cli.main import app

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "source-archive-preflight",
        "source-archive-plan",
        "source-archive-run",
        "source-archive-verify",
    ):
        assert command in result.output
