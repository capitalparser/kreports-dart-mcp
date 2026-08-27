from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date

import pytest

from kreports.db.models import Company, CompanyYearListingMembership, Disclosure


@dataclass(frozen=True)
class _Object:
    storage_uri: str
    sha256: str
    byte_length: int


class _Archive:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def archive_bytes(self, *, data: bytes, extension: str, metadata: dict[str, str]):
        digest = hashlib.sha256(data).hexdigest()
        result = _Object(f"drive:objects/{len(self.calls)}", digest, len(data))
        self.calls.append({
            "data": data, "extension": extension, "metadata": dict(metadata), "object": result,
        })
        return result


class _BusinessAssets(dict[str, bytes]):
    """Test double matching the fetcher's ZIP-member plus raw-container contract."""

    def __init__(self, assets: dict[str, bytes], *, container_bytes: bytes | None = None) -> None:
        super().__init__(assets)
        self.container_bytes = container_bytes or b"PK\x03\x04test-original-document-zip"
        self.container_content_type = "application/zip"
        self.is_zip = True


def _business_assets(raw: bytes = b"<DOCUMENT><P>x</P></DOCUMENT>") -> _BusinessAssets:
    return _BusinessAssets({"main.xml": raw})


def _membership(corp_code: str, year: int, market: str) -> CompanyYearListingMembership:
    normalized_checksum = hashlib.sha256(f"{corp_code}:{year}:{market}".encode()).hexdigest()
    return CompanyYearListingMembership(
        corp_code=corp_code, stock_code=corp_code[-6:], bsns_year=year, market=market,
        status="verified", evidence_basis="year_end_listing_receipt", as_of=date(year, 12, 31),
        manifest_checksum="a" * 64, manifest_storage_uri=f"drive:listing/{year}/{market}",
        manifest_size_bytes=1, manifest_raw_receipt_count=1, normalized_checksum=normalized_checksum,
        normalized_storage_uri=f"drive:listing/{year}/{market}/normalized", normalized_size_bytes=1,
        transformation_version="listing-v1", source_row_no=int(corp_code[-2:]) + 1,
    )


def _seed_annual_disclosures(temp_engine) -> None:
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=temp_engine)() as session:
        session.add_all([
            Company(corp_code="00126380", corp_name="적격회사"),
            Company(corp_code="00126381", corp_name="현재회사지만과거부적격"),
        ])
        for corp_code in ("00126380", "00126381"):
            for year in range(2021, 2026):
                session.add(Disclosure(
                    rcept_no=f"{year + 1}0331{corp_code[-6:]}", corp_code=corp_code,
                    corp_name="테스트", disc_date=date(year + 1, 3, 31), disc_type="A",
                    report_nm=f"사업보고서 ({year}.12)",
                ))
        session.add(Disclosure(
            rcept_no="20250401126380", corp_code="00126380", corp_name="적격회사",
            disc_date=date(2025, 4, 1), disc_type="A", report_nm="[기재정정]사업보고서 (2024.12)",
        ))
        for year in range(2021, 2026):
            session.add(_membership("00126380", year, "KOSPI"))
            # Each requested year needs both market receipts; the KOSDAQ receipt
            # is deliberately not a current-company row.
            session.add(_membership(f"9{year:07d}", year, "KOSDAQ"))
        session.add(_membership("00126381", 2024, "KOSPI"))
        session.commit()


def _plan(temp_engine, years=range(2021, 2026)):
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import build_source_archive_plan

    with sessionmaker(bind=temp_engine)() as session:
        return build_source_archive_plan(session, years=years)


def _install_complete_family(monkeypatch, campaign):
    monkeypatch.setattr(
        campaign, "fetch_document_zip_asset_bytes",
        lambda _receipt: _business_assets(b"<DOCUMENT><P>business</P></DOCUMENT>"),
    )
    monkeypatch.setattr(campaign, "fetch_dart_main_html", lambda _receipt: "attachments")
    monkeypatch.setattr(campaign, "select_primary_audit_report_attachments", lambda _html: [{
        "rcept_no": "20250401126380", "dcm_no": "99", "title": "감사보고서",
    }])
    monkeypatch.setattr(campaign, "fetch_viewer_bytes", lambda *_args: b"<DOCUMENT><P>audit</P></DOCUMENT>")
    monkeypatch.setattr(campaign, "fetch_audit_report_pdf", lambda *_args: pytest.fail("viewer success must not fetch PDF"))


def test_plan_uses_verified_year_memberships_and_canonical_latest_anchor(temp_engine):
    _seed_annual_disclosures(temp_engine)

    plan = _plan(temp_engine)

    assert len(plan.targets) == 11
    assert {(target.corp_code, target.bsns_year) for target in plan.targets} == {
        *( ("00126380", year) for year in range(2021, 2026) ),
        ("00126381", 2024),
        *( (f"9{year:07d}", year) for year in range(2021, 2026) ),
    }
    corrected = next(target for target in plan.targets if target.corp_code == "00126380" and target.bsns_year == 2024)
    assert corrected.source_receipt == "20250401126380"
    assert len({target.shard for target in plan.targets if target.corp_code == "00126380"}) == 1
    assert plan.shard_count == 64
    # The default remains the byte-compatible listed-v2 identity used by
    # already-frozen campaign manifests and their target digests.
    assert plan.target_manifest["schema"] == "source-archive-campaign.v2"
    assert "universe_mode" not in plan.target_manifest
    assert "universe_cohort" not in corrected.to_dict()
    legacy_digest_payload = {
        "schema": "source-archive-campaign.v2",
        "years": list(plan.years),
        "shard_count": plan.shard_count,
        "targets": [target.to_dict() for target in plan.targets],
    }
    assert plan.target_digest == hashlib.sha256(
        json.dumps(legacy_digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def test_all_annual_issuers_unions_verified_markets_with_outside_canonical_anchors(temp_engine):
    """Current company metadata cannot classify a historic outside-market issuer."""
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import build_source_archive_plan

    with sessionmaker(bind=temp_engine)() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="코스피", market="KOSPI"),
            Company(corp_code="00000002", corp_name="코스닥", market="KOSDAQ"),
            Company(corp_code="00000003", corp_name="외부연차", market=None),
            # This is deliberately a current-market-like hint only.  It has
            # neither dated membership nor an annual anchor, so it is excluded.
            Company(corp_code="00000004", corp_name="현재메타데이터", market="KOSPI"),
            Disclosure(
                rcept_no="20250331000001", corp_code="00000001", corp_name="코스피",
                disc_date=date(2025, 3, 31), disc_type="A", report_nm="[기재정정]사업보고서 (2024.12)",
            ),
            Disclosure(
                rcept_no="20250331000003", corp_code="00000003", corp_name="외부연차",
                disc_date=date(2025, 3, 31), disc_type="A", report_nm="사업보고서 (2024.12)",
            ),
            _membership("00000001", 2024, "KOSPI"),
            _membership("00000002", 2024, "KOSDAQ"),
        ])
        session.commit()

    with sessionmaker(bind=temp_engine)() as session:
        plan = build_source_archive_plan(session, [2024], universe_mode="all_annual_issuers")

    assert {(target.corp_code, target.bsns_year) for target in plan.targets} == {
        ("00000001", 2024), ("00000002", 2024), ("00000003", 2024),
    }
    assert len(plan.targets) == 3
    listed = {target.corp_code: target for target in plan.targets if target.corp_code != "00000003"}
    assert listed["00000001"].universe_cohort == "verified_kospi"
    assert listed["00000002"].universe_cohort == "verified_kosdaq"
    outside = next(target for target in plan.targets if target.corp_code == "00000003")
    assert outside.universe_cohort == "annual_report_issuer_outside_verified_markets"
    assert outside.historical_listing_status == "unclassified"
    assert outside.historical_listing_basis == "no_verified_kospi_kosdaq_membership"
    assert outside.market is None
    assert outside.shard == next(target.shard for target in plan.targets if target.corp_code == "00000003")
    assert "unlisted_confirmed" not in json.dumps(plan.target_manifest)
    assert plan.target_manifest["schema"] == "source-archive-campaign.v3"
    assert plan.target_manifest["universe_mode"] == "all_annual_issuers"
    assert plan.target_manifest["cohort_counts"] == {
        "annual_report_issuer_outside_verified_markets": 1,
        "verified_kosdaq": 1,
        "verified_kospi": 1,
    }


def test_plan_fails_closed_without_verified_historical_memberships(temp_engine):
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import SourceArchiveCampaignError, build_source_archive_plan

    with sessionmaker(bind=temp_engine)() as session:
        session.add(Company(corp_code="00126380", corp_name="현재회사"))
    with sessionmaker(bind=temp_engine)() as session:
        with pytest.raises(SourceArchiveCampaignError, match="historical listing membership"):
            build_source_archive_plan(session, years=[2024])


def test_plan_rejects_unsupported_source_universe(temp_engine):
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import SourceArchiveCampaignError, build_source_archive_plan

    _seed_annual_disclosures(temp_engine)
    with sessionmaker(bind=temp_engine)() as session:
        with pytest.raises(SourceArchiveCampaignError, match="universe_mode"):
            build_source_archive_plan(session, years=[2024], universe_mode="all_issuers")


def test_dry_run_makes_no_fetch_or_archive_writes(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine).with_state_dir(tmp_path / "campaign")
    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", lambda _: pytest.fail("dry run fetched DART"))

    report = campaign.run_source_archive_shard(plan, plan.targets[0].shard, _Archive(), apply=False)

    assert report.status == "dry_run"
    assert report.outcomes == ()
    assert not plan.state_dir.exists()


def test_raw_archive_keeps_original_non_utf8_bytes_and_drive_lineage_manifest(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    raw = b"<DOCUMENT><P>\x80\xff</P></DOCUMENT>"
    original_zip = b"PK\x03\x04original-document-zip\x80\xff"
    monkeypatch.setattr(
        campaign, "fetch_document_zip_asset_bytes",
        lambda _receipt: _BusinessAssets({"main.xml": raw}, container_bytes=original_zip),
    )
    monkeypatch.setattr(campaign, "fetch_dart_main_html", lambda _receipt: None)
    archive = _Archive()

    report = campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=2)

    assert report.status == "partial"
    raw_call = next(call for call in archive.calls if call["metadata"]["archive_version"] == "raw-source-v1")
    assert raw_call["data"] == raw
    assert raw_call["object"].sha256 == hashlib.sha256(raw).hexdigest()
    container_call = next(
        call for call in archive.calls
        if call["metadata"]["archive_version"] == "raw-document-zip-container-v1"
    )
    assert container_call["data"] == original_zip
    assert container_call["object"].sha256 == hashlib.sha256(original_zip).hexdigest()
    assert container_call["extension"] == "zip"
    assert container_call["metadata"]["container_content_type"] == "application/zip"
    assert container_call["metadata"]["container_is_zip"] == "true"
    assert raw_call["metadata"]["container_sha256"] == container_call["object"].sha256
    assert raw_call["metadata"]["container_storage_uri"] == container_call["object"].storage_uri
    assert raw_call["metadata"]["container_member_name"] == "main.xml"
    document_manifest = next(
        json.loads(call["data"])
        for call in archive.calls
        if call["metadata"]["archive_version"] == "source-archive-document-manifest-v1"
    )
    assert document_manifest["corp_code"] == target.corp_code
    assert document_manifest["report_kind"] == "business_report"
    assert document_manifest["source_receipt"] == target.source_receipt
    assert document_manifest["source_locator"] == "main.xml"
    assert document_manifest["raw"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert document_manifest["raw_container"]["sha256"] == hashlib.sha256(original_zip).hexdigest()
    assert document_manifest["raw_container"]["storage_uri"] == container_call["object"].storage_uri
    assert document_manifest["container_member_name"] == "main.xml"


def test_direct_raw_xml_response_is_not_archived_as_a_zip_container(temp_engine, tmp_path, monkeypatch):
    """A direct XML DART response must retain its own media/provenance representation."""
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    raw_xml = b"<DOCUMENT><P>direct response</P></DOCUMENT>"
    response = type("Response", (), {
        "content": raw_xml,
        "encoding": "utf-8",
        "headers": {"content-type": "application/xml"},
        "raise_for_status": lambda self: None,
    })()

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(fetcher.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(fetcher, "_get_client", Client)
    assets = fetcher.fetch_document_zip_asset_bytes(target.source_receipt)
    assert assets.is_zip is False
    assert assets.container_content_type == "application/xml"
    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", lambda _receipt: assets)
    monkeypatch.setattr(campaign, "fetch_dart_main_html", lambda _receipt: None)
    archive = _Archive()

    campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=2)

    container = next(
        call for call in archive.calls
        if call["metadata"]["archive_version"] == "raw-document-response-container-v1"
    )
    assert container["extension"] == "xml"
    assert container["data"] == raw_xml
    assert container["metadata"]["container_content_type"] == "application/xml"
    assert container["metadata"]["container_is_zip"] == "false"
    raw_member = next(call for call in archive.calls if call["metadata"]["archive_version"] == "raw-source-v1")
    assert raw_member["metadata"]["container_content_type"] == "application/xml"
    document = next(
        json.loads(call["data"])
        for call in archive.calls
        if call["metadata"]["archive_version"] == "source-archive-document-manifest-v1"
    )
    assert document["content_type"] == "xml"
    assert document["raw_container"]["content_type"] == "application/xml"
    assert document["raw_container"]["is_zip"] is False


def test_business_only_is_partial_and_never_committed(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", lambda _: _business_assets())
    monkeypatch.setattr(campaign, "fetch_dart_main_html", lambda _: "no audit attachment")
    monkeypatch.setattr(campaign, "select_primary_audit_report_attachments", lambda _: [])

    report = campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=2)

    assert report.status == "partial"
    assert any(item["status"] == "partial_source" and item["report_kind"] == "audit_report" for item in report.outcomes)
    assert not (plan.state_dir / f"shard-{target.shard:02d}" / "COMMITTED.json").exists()


def test_audit_attachment_pdf_fallback_is_explicit_partial_boundary(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", lambda _: _business_assets())
    monkeypatch.setattr(campaign, "fetch_dart_main_html", lambda _: "attachments")
    monkeypatch.setattr(campaign, "select_primary_audit_report_attachments", lambda _: [{
        "rcept_no": target.source_receipt, "dcm_no": "77", "title": "감사보고서",
    }])
    monkeypatch.setattr(campaign, "fetch_viewer_bytes", lambda *_args: None)
    monkeypatch.setattr(campaign, "fetch_audit_report_pdf", lambda *_args: b"%PDF-1.7\noriginal\n%%EOF")

    archive = _Archive()
    report = campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=4)

    assert report.status == "partial"
    audit = next(item for item in report.outcomes if item.get("report_kind") == "audit_report" and item["status"] == "archived_verified")
    assert audit["content_type"] == "pdf"
    assert audit["source_locator"] == "dcm:77"
    assert audit["source_uri"].endswith("?rcp_no=20250401126380&dcm_no=77")
    raw = next(call for call in archive.calls if call["metadata"]["archive_version"] == "raw-source-v1" and call["metadata"]["report_kind"] == "audit_report")
    assert raw["metadata"]["source_uri"].endswith("?rcp_no=20250401126380&dcm_no=77")
    parsed = next(
        call for call in archive.calls
        if call["metadata"]["archive_version"] == "document-structure-v1"
        and call["metadata"]["source_uri"].endswith("?rcp_no=20250401126380&dcm_no=77")
    )
    assert parsed["metadata"]["source_uri"].endswith("?rcp_no=20250401126380&dcm_no=77")
    document = next(
        json.loads(call["data"])
        for call in archive.calls
        if call["metadata"]["archive_version"] == "source-archive-document-manifest-v1"
        and b'"report_kind":"audit_report"' in call["data"]
    )
    assert document["source_uri"].endswith("?rcp_no=20250401126380&dcm_no=77")


def test_full_frozen_target_is_archived_before_source_fetch_and_referenced_by_drive_events(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    archive = _Archive()

    def fetch_business(_receipt):
        assert any(call["metadata"]["archive_version"] == "source-archive-target-manifest-v1" for call in archive.calls)
        return _business_assets()

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", fetch_business)
    monkeypatch.setattr(campaign, "fetch_dart_main_html", lambda _: None)

    campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=2)

    target_call = next(call for call in archive.calls if call["metadata"]["archive_version"] == "source-archive-target-manifest-v1")
    target_payload = json.loads(target_call["data"])
    assert target_payload["target_digest"] == plan.target_digest
    assert target_payload["targets"] == plan.target_manifest["targets"]
    local_target = json.loads((plan.state_dir / "TARGET.json").read_text())
    assert local_target["drive_target_manifest"]["storage_uri"] == target_call["object"].storage_uri
    event = next(
        json.loads(call["data"])
        for call in archive.calls
        if call["metadata"]["archive_version"] == "source-archive-campaign-manifest-v1"
    )
    assert event["drive_target_manifest"]["sha256"] == target_call["object"].sha256


def test_apply_fails_closed_when_existing_target_lacks_drive_identity(temp_engine, tmp_path):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    plan.state_dir.mkdir()
    (plan.state_dir / "TARGET.json").write_text(
        json.dumps(plan.target_manifest), encoding="utf-8"
    )
    archive = _Archive()

    with pytest.raises(campaign.SourceArchiveCampaignError, match="immutable Drive target manifest identity"):
        campaign.run_source_archive_shard(
            plan, target.shard, archive, apply=True, max_dart_calls=1
        )
    assert archive.calls == []


def test_budget_exhaustion_is_resumable_and_never_commits(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    def business(_receipt):
        fetcher._record_request_attempt("document.xml")
        return _business_assets()

    def audit_main(_receipt):
        fetcher._record_request_attempt("dsaf001/main.do")
        return "attachments"

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", business)
    monkeypatch.setattr(campaign, "fetch_dart_main_html", audit_main)

    report = campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=1)

    assert report.status == "partial"
    assert any(item["status"] == "dart_budget_exhausted" for item in report.outcomes)
    assert not (plan.state_dir / f"shard-{target.shard:02d}" / "COMMITTED.json").exists()


def test_budget_exhaustion_before_audit_viewer_is_explicit(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    def business(_receipt):
        fetcher._record_request_attempt("document.xml")
        return _business_assets()

    def audit_main(_receipt):
        fetcher._record_request_attempt("dsaf001/main.do")
        return "attachments"

    def viewer(*_args):
        fetcher._record_request_attempt("report/viewer.do")
        return b"<DOCUMENT><P>audit</P></DOCUMENT>"

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", business)
    monkeypatch.setattr(campaign, "fetch_dart_main_html", audit_main)
    monkeypatch.setattr(campaign, "select_primary_audit_report_attachments", lambda _: [{
        "rcept_no": target.source_receipt, "dcm_no": "77", "title": "감사보고서",
    }])
    monkeypatch.setattr(campaign, "fetch_viewer_bytes", viewer)

    report = campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=2)

    assert any(item["status"] == "dart_budget_exhausted" and item["report_kind"] == "audit_report" for item in report.outcomes)


def test_committed_marker_and_verify_fail_closed_when_outcomes_are_tampered(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    _install_complete_family(monkeypatch, campaign)
    report = campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=3)
    assert report.status == "complete"
    outcomes = plan.state_dir / f"shard-{target.shard:02d}" / "outcomes.jsonl"
    outcomes.write_text(outcomes.read_text() + "tampered\n", encoding="utf-8")

    with pytest.raises(campaign.SourceArchiveCampaignError, match="outcomes checksum"):
        campaign.verify_source_archive_campaign(plan.state_dir, shard=target.shard)
    with pytest.raises(campaign.SourceArchiveCampaignError, match="outcomes checksum"):
        campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=3)


@pytest.mark.parametrize("configured_as_alias", [True, False])
def test_plan_rejects_runtime_db_uri_alias_and_symlink(tmp_path, monkeypatch, configured_as_alias):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from kreports.db.models import Base
    from kreports.maintenance.source_archive_campaign import SourceArchiveCampaignError, build_source_archive_plan

    database = tmp_path / "runtime.db"
    alias = tmp_path / "runtime-alias.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    alias.symlink_to(database)
    configured = f"sqlite:///file:{database}?mode=ro&uri=true" if configured_as_alias else f"sqlite:///{alias}"
    monkeypatch.setattr("kreports.config.settings.db_url", configured)
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(SourceArchiveCampaignError, match="non-runtime collector"):
            build_source_archive_plan(session, years=[2024])


def test_cli_exposes_explicit_source_archive_commands():
    from typer.testing import CliRunner
    from kreports.cli.main import app

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("source-archive-preflight", "source-archive-plan", "source-archive-run", "source-archive-verify"):
        assert command in result.output
