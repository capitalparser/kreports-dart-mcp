from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest

from kreports.db.models import Company, CompanyYearListingMembership, Disclosure


@dataclass(frozen=True)
class _Object:
    storage_uri: str
    sha256: str
    byte_length: int


class _Archive:
    def __init__(self, *, storage_uri_prefix: str = "drive:objects/") -> None:
        self.calls: list[dict[str, object]] = []
        self.storage_uri_prefix = storage_uri_prefix

    def archive_bytes(self, *, data: bytes, extension: str, metadata: dict[str, str]):
        digest = hashlib.sha256(data).hexdigest()
        result = _Object(f"{self.storage_uri_prefix}{len(self.calls)}", digest, len(data))
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
        lambda _receipt: _BusinessAssets({
            "main.xml": b"<DOCUMENT><P>business</P></DOCUMENT>",
            "감사보고서.xml": b"<DOCUMENT><P>audit</P></DOCUMENT>",
        }),
    )


@pytest.fixture(autouse=True)
def _no_live_separate_audit_discovery(monkeypatch):
    """Campaign tests must opt into a concrete separate-filing response."""
    import kreports.maintenance.source_archive_campaign as campaign

    monkeypatch.setattr(campaign, "fetch_disclosure_list", lambda *_args, **_kwargs: [])


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


def _all_issuer_fixture_plan(temp_engine):
    """Return the Task 1 three-cohort fixture as a v3 plan."""
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import build_source_archive_plan

    with sessionmaker(bind=temp_engine)() as session:
        session.add_all([
            Company(corp_code="00000001", corp_name="코스피"),
            Company(corp_code="00000002", corp_name="코스닥"),
            Company(corp_code="00000003", corp_name="외부연차"),
            Disclosure(
                rcept_no="20250331000001", corp_code="00000001", corp_name="코스피",
                disc_date=date(2025, 3, 31), disc_type="A", report_nm="사업보고서 (2024.12)",
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
        return build_source_archive_plan(session, [2024], universe_mode="all_annual_issuers")


def test_all_issuer_preflight_reports_actual_cohort_and_historic_status_counts(temp_engine, monkeypatch):
    from typer.testing import CliRunner
    import kreports.cli.main as cli

    plan = _all_issuer_fixture_plan(temp_engine)
    requested_modes: list[str] = []

    def source_plan(_db_path, *, years, shard_count, universe_mode, excluded_pairs=frozenset()):
        assert years == [2024]
        assert shard_count == 64
        assert excluded_pairs == frozenset()
        requested_modes.append(universe_mode)
        return plan

    monkeypatch.setattr(cli, "_source_archive_plan_from_database", source_plan)

    result = CliRunner().invoke(
        cli.app,
        [
            "source-archive-preflight", "--db", "candidate.db", "--year", "2024",
            "--universe", "all-annual-issuers",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert requested_modes == ["all_annual_issuers"]
    assert payload["universe_mode"] == "all_annual_issuers"
    assert payload["cohort_counts"]["annual_report_issuer_outside_verified_markets"] == 1
    assert payload["cohort_target_counts"] == {
        "annual_report_issuer_outside_verified_markets": 1,
        "verified_kosdaq": 1,
        "verified_kospi": 1,
    }
    assert payload["cohort_discovered_counts"] == {
        "annual_report_issuer_outside_verified_markets": 1,
        "verified_kospi": 1,
    }
    assert payload["cohort_gap_counts"] == {"verified_kosdaq": 1}
    assert payload["historical_status_counts"] == {
        "KOSDAQ": 1,
        "KOSPI": 1,
        "unclassified": 1,
    }


def test_v3_apply_rejects_v2_target_state_before_archive_or_fetch(temp_engine, tmp_path):
    from kreports.maintenance.source_archive_campaign import (
        SourceArchiveCampaignError,
        run_source_archive_shard,
    )

    plan = _all_issuer_fixture_plan(temp_engine).with_state_dir(tmp_path / "v3-campaign")
    target = next(item for item in plan.targets if item.source_status == "discovered")
    plan.state_dir.mkdir()
    v2_target = {
        "schema": "source-archive-campaign.v2",
        "years": list(plan.years),
        "shard_count": plan.shard_count,
        "target_digest": "v2-state-cannot-resume-v3",
        "target_count": 0,
        "targets": [],
    }
    (plan.state_dir / "TARGET.json").write_text(json.dumps(v2_target), encoding="utf-8")
    archive = _Archive()

    with pytest.raises(SourceArchiveCampaignError, match="schema|universe|target"):
        run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=1)

    assert archive.calls == []


def test_v3_run_binds_scope_to_report_document_and_event_manifests(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    plan = _all_issuer_fixture_plan(temp_engine).with_state_dir(tmp_path / "v3-campaign")
    target = next(
        item for item in plan.targets
        if item.universe_cohort == "annual_report_issuer_outside_verified_markets"
    )
    _install_complete_family(monkeypatch, campaign)
    archive = _Archive()

    report = campaign.run_source_archive_shard(
        plan, target.shard, archive, apply=True, max_dart_calls=3
    )

    payload = report.to_dict()
    assert payload["schema"] == "source-archive-campaign.v3"
    assert payload["universe_mode"] == "all_annual_issuers"
    assert payload["cohort_counts"]["annual_report_issuer_outside_verified_markets"] == 1
    document = next(
        json.loads(call["data"])
        for call in archive.calls
        if call["metadata"]["archive_version"] == "source-archive-document-manifest-v1"
        and b'"corp_code":"00000003"' in call["data"]
    )
    event = next(
        json.loads(call["data"])
        for call in archive.calls
        if call["metadata"]["archive_version"] == "source-archive-campaign-manifest-v1"
        and b'"corp_code":"00000003"' in call["data"]
    )
    for manifest in (document, event):
        assert manifest["universe_cohort"] == "annual_report_issuer_outside_verified_markets"
        assert manifest["historical_listing_status"] == "unclassified"
        assert manifest["historical_listing_basis"] == "no_verified_kospi_kosdaq_membership"


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
    archive = _Archive(storage_uri_prefix="drive:containers/" + "x" * 120)

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
    assert len(raw_call["metadata"]["container_storage_uri"].encode("utf-8")) > 124
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


def test_audit_xml_member_inside_business_document_zip_completes_audit_family(temp_engine, tmp_path, monkeypatch):
    """A genuine audit XML member must outrank the viewer/PDF attachment path."""
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    business_xml = b"<DOCUMENT><DOCUMENT-NAME>\xec\x82\xac\xec\x97\x85\xeb\xb3\xb4\xea\xb3\xa0\xec\x84\x9c</DOCUMENT-NAME></DOCUMENT>"
    audit_xml = (
        b"<DOCUMENT><DOCUMENT-NAME>\xea\xb0\x90\xec\x82\xac\xeb\xb3\xb4\xea\xb3\xa0\xec\x84\x9c</DOCUMENT-NAME>"
        b"<P>\xea\xb0\x90\xec\x82\xac\xec\x9d\x98\xea\xb2\xac</P></DOCUMENT>"
    )
    monkeypatch.setattr(
        campaign,
        "fetch_document_zip_asset_bytes",
        lambda _receipt: _BusinessAssets({"main.xml": business_xml, "audit.xml": audit_xml}),
    )

    archive = _Archive()
    campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=2)
    audit_raw = next(
        call for call in archive.calls
        if call["metadata"].get("report_kind") == "audit_report"
        and call["metadata"].get("archive_version") == "raw-source-v1"
    )
    assert audit_raw["data"] == audit_xml
    assert audit_raw["metadata"]["source_receipt"] == target.source_receipt
    assert audit_raw["metadata"]["source_locator"] == "audit.xml"


def test_separate_audit_filing_xml_is_archived_when_business_zip_has_no_audit_xml(temp_engine, tmp_path, monkeypatch):
    """An unlisted issuer's separately filed audit XML must use document.xml, not PDF."""
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    audit_receipt = "20250315000000"
    business_xml = b"<DOCUMENT><DOCUMENT-NAME>\xec\x82\xac\xec\x97\x85\xeb\xb3\xb4\xea\xb3\xa0\xec\x84\x9c</DOCUMENT-NAME></DOCUMENT>"
    audit_xml = (
        b"<DOCUMENT><DOCUMENT-NAME>\xea\xb0\x90\xec\x82\xac\xeb\xb3\xb4\xea\xb3\xa0\xec\x84\x9c</DOCUMENT-NAME>"
        b"<P>\xea\xb0\x90\xec\x82\xac\xec\x9d\x98\xea\xb2\xac</P></DOCUMENT>"
    )
    monkeypatch.setattr(
        campaign,
        "fetch_document_zip_asset_bytes",
        lambda receipt: _BusinessAssets(
            {"business.xml": business_xml} if receipt == target.source_receipt else {"audit.xml": audit_xml}
        ),
    )
    monkeypatch.setattr(campaign, "fetch_disclosure_list", lambda *_args, **_kwargs: [{
        "rcept_no": audit_receipt,
        "report_nm": "감사보고서 (2024.12)",
        "rcept_dt": "20250315",
    }], raising=False)

    archive = _Archive()
    campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=3)

    audit_raw = next(
        call for call in archive.calls
        if call["metadata"].get("report_kind") == "audit_report"
        and call["metadata"].get("archive_version") == "raw-source-v1"
    )
    assert audit_raw["data"] == audit_xml
    assert audit_raw["metadata"]["source_receipt"] == audit_receipt
    assert audit_raw["metadata"]["source_locator"] == "audit.xml"


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

    report = campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=1)

    assert report.status == "partial"
    assert any(item["status"] == "partial_source" and item["report_kind"] == "audit_report" for item in report.outcomes)
    assert not (plan.state_dir / f"shard-{target.shard:02d}" / "COMMITTED.json").exists()


def test_pdf_attachment_does_not_count_as_an_xml_audit_package(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", lambda _: _business_assets())

    archive = _Archive()
    report = campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=4)

    assert report.status == "partial"
    assert any(
        item["report_kind"] == "audit_report"
        and item["status"] == "partial_source"
        and item["error"] == "audit_xml_unavailable"
        for item in report.outcomes
    )
    assert not any(
        call["metadata"].get("report_kind") == "audit_report"
        for call in archive.calls
    )


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


def test_existing_frozen_target_reuses_verified_drive_identity_without_rearchiving(temp_engine, tmp_path, monkeypatch):
    """A resumed shard must not re-upload/readback the 5-year denominator."""
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    first_archive = _Archive()
    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", lambda _: _business_assets())

    campaign.run_source_archive_shard(plan, target.shard, first_archive, apply=True, max_dart_calls=2)

    # A fresh worker has no in-memory Drive cache.  The local frozen manifest
    # is the durable record of the already verified content-addressed object.
    resumed_archive = _Archive()
    report = campaign.run_source_archive_shard(
        plan, target.shard, resumed_archive, apply=True, max_dart_calls=2
    )

    assert report.outcomes
    assert not any(
        call["metadata"]["archive_version"] == "source-archive-target-manifest-v1"
        for call in resumed_archive.calls
    )


def test_resume_reuses_completed_business_family_after_budget_stop(temp_engine, tmp_path, monkeypatch):
    """A family checkpoint avoids its DART and Drive work on the next run."""
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2021]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380" and item.bsns_year == 2021)
    business_calls: list[str] = []

    def fetch_business(receipt):
        business_calls.append(receipt)
        fetcher._record_request_attempt("document.xml")
        return _business_assets()

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", fetch_business)
    monkeypatch.setattr(
        campaign, "fetch_disclosure_list",
        lambda *_args, **_kwargs: (fetcher._record_request_attempt("list.json") or []),
    )

    first = campaign.run_source_archive_shard(
        plan, target.shard, _Archive(), apply=True, max_dart_calls=1
    )

    assert first.status == "partial"
    assert any(
        item["status"] == "family_complete" and item["report_kind"] == "business_report"
        for item in first.outcomes
    )
    assert not any(item["company_year_terminal"] for item in first.outcomes)

    # A legacy business-family checkpoint is re-read once to inspect embedded
    # audit XML under the upgraded resolver, without re-archiving its members.
    resumed_archive = _Archive()
    second = campaign.run_source_archive_shard(
        plan, target.shard, resumed_archive, apply=True, max_dart_calls=1
    )

    assert second.status == "partial"
    assert business_calls == [target.source_receipt, target.source_receipt]
    assert any(
        item["status"] == "family_reused" and item["report_kind"] == "business_report"
        for item in second.outcomes
    )
    assert not any(call["metadata"]["archive_version"] == "raw-source-v1" for call in resumed_archive.calls)


def test_resume_reuses_verified_assets_even_when_parser_requires_review(temp_engine, tmp_path, monkeypatch):
    """A parser-quality gap must not redownload already verified raw XML."""
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    assets = {
        "complete.xml": b"<DOCUMENT><P>complete</P></DOCUMENT>",
        "partial.xml": b"<DOCUMENT>",
    }
    business_calls = 0

    def fetch_business(_receipt):
        nonlocal business_calls
        business_calls += 1
        return _BusinessAssets(assets)

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", fetch_business)

    campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=1)
    resumed_archive = _Archive()
    second = campaign.run_source_archive_shard(
        plan, target.shard, resumed_archive, apply=True, max_dart_calls=1
    )

    assert second.status == "partial"
    assert business_calls == 2
    reused = [
        item for item in second.outcomes
        if item["status"] == "asset_reused" and item["report_kind"] == "business_report"
    ]
    assert [item["source_locator"] for item in reused] == ["complete.xml", "partial.xml"]
    raw_calls = [
        call for call in resumed_archive.calls
        if call["metadata"]["archive_version"] == "raw-source-v1"
    ]
    assert raw_calls == []
    assert not any(
        call["metadata"]["archive_version"] == "raw-document-zip-container-v1"
        for call in resumed_archive.calls
    )


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

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", business)
    monkeypatch.setattr(
        campaign, "fetch_disclosure_list",
        lambda *_args, **_kwargs: (fetcher._record_request_attempt("list.json") or []),
    )

    report = campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=1)

    assert report.status == "partial"
    assert any(item["status"] == "dart_budget_exhausted" for item in report.outcomes)
    assert report.stop_reason == "api_budget_exhausted"
    assert {(item["corp_code"], item["bsns_year"]) for item in report.outcomes} == {
        (target.corp_code, target.bsns_year),
    }
    assert not any(item["company_year_terminal"] for item in report.outcomes)
    assert not (plan.state_dir / f"shard-{target.shard:02d}" / "COMMITTED.json").exists()


def test_recent_partial_source_does_not_starve_an_unattempted_company_year(
    temp_engine, tmp_path, monkeypatch
):
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2021, 2022]).with_state_dir(tmp_path / "campaign")
    targets = tuple(
        target for target in plan.targets_for_shard(
            next(item.shard for item in plan.targets if item.corp_code == "00126380")
        )
        if target.corp_code == "00126380"
    )
    recent, untouched = targets
    shard_dir = plan.state_dir / f"shard-{recent.shard:02d}"
    shard_dir.mkdir(parents=True)
    (shard_dir / "outcomes.jsonl").write_text(json.dumps({
        "target_digest": plan.target_digest,
        "recorded_at": datetime.now(UTC).isoformat(),
        "corp_code": recent.corp_code,
        "bsns_year": recent.bsns_year,
        "status": "partial_source",
        "company_year_terminal": True,
        "audit_xml_resolver_version": campaign.AUDIT_XML_RESOLVER_VERSION,
    }) + "\n")
    fetched: list[str] = []

    def fetch_business(receipt):
        fetched.append(receipt)
        fetcher._record_request_attempt("document.xml")
        return _business_assets()

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", fetch_business)
    monkeypatch.setattr(
        campaign, "fetch_disclosure_list",
        lambda *_args, **_kwargs: (fetcher._record_request_attempt("list.json") or []),
    )

    report = campaign.run_source_archive_shard(
        plan, recent.shard, _Archive(), apply=True, max_dart_calls=1,
        partial_retry_after_seconds=24 * 60 * 60,
    )

    assert fetched == [untouched.source_receipt]
    assert report.stop_reason == "api_budget_exhausted"
    assert report.deferred_retry_count == 1


def test_legacy_partial_source_is_immediately_retried_after_audit_xml_resolver_upgrade(
    temp_engine, tmp_path, monkeypatch
):
    """A partial recorded before XML discovery must not wait out the old retry window."""
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    shard_dir = plan.state_dir / f"shard-{target.shard:02d}"
    shard_dir.mkdir(parents=True)
    (shard_dir / "outcomes.jsonl").write_text(json.dumps({
        "target_digest": plan.target_digest,
        "recorded_at": datetime.now(UTC).isoformat(),
        "corp_code": target.corp_code,
        "bsns_year": target.bsns_year,
        "status": "partial_source",
        "company_year_terminal": True,
    }) + "\n")
    fetched: list[str] = []

    def fetch_business(receipt):
        fetched.append(receipt)
        raise fetcher.DartRequestBudgetExceeded(1)

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", fetch_business)

    report = campaign.run_source_archive_shard(
        plan, target.shard, _Archive(), apply=True, max_dart_calls=1,
        partial_retry_after_seconds=24 * 60 * 60,
    )

    assert fetched == [target.source_receipt]
    assert report.stop_reason == "api_budget_exhausted"
    assert report.deferred_retry_count == 0


def test_partial_source_becomes_eligible_after_retry_interval(
    temp_engine, tmp_path, monkeypatch
):
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2021]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    shard_dir = plan.state_dir / f"shard-{target.shard:02d}"
    shard_dir.mkdir(parents=True)
    (shard_dir / "outcomes.jsonl").write_text(json.dumps({
        "target_digest": plan.target_digest,
        "recorded_at": (datetime.now(UTC) - timedelta(hours=25)).isoformat(),
        "corp_code": target.corp_code,
        "bsns_year": target.bsns_year,
        "status": "partial_source",
        "company_year_terminal": True,
    }) + "\n")
    fetched: list[str] = []

    def fetch_business(receipt):
        fetched.append(receipt)
        fetcher._record_request_attempt("document.xml")
        raise fetcher.DartRequestBudgetExceeded("document.xml")

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", fetch_business)

    report = campaign.run_source_archive_shard(
        plan, target.shard, _Archive(), apply=True, max_dart_calls=1,
        partial_retry_after_seconds=24 * 60 * 60,
    )

    assert fetched == [target.source_receipt]
    assert report.stop_reason == "api_budget_exhausted"
    assert report.deferred_retry_count == 0


def test_requires_review_assets_are_reused_without_redownloading_the_raw_xml(
    temp_engine, tmp_path, monkeypatch
):
    """A parser-quality gap stays partial but never redoes verified raw archival."""
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    assets = _BusinessAssets({"main.xml": b"<not xml"})
    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", lambda _receipt: assets)
    archive = _Archive()

    report = campaign.run_source_archive_shard(
        plan, target.shard, archive, apply=True, max_dart_calls=3
    )
    assert any(
        row["report_kind"] == "business_report"
        and row["status"] == "generically_parsed"
        and row["structural_status"] == "requires_review"
        for row in report.outcomes
    )

    resume_state = campaign._resume_state(
        plan.state_dir / f"shard-{target.shard:02d}" / "outcomes.jsonl",
        plan.target_digest,
    )
    archive.calls.clear()
    reused = campaign._business_family(
        target,
        archive,
        {},
        resume_state,
    )

    assert reused.complete is False
    assert archive.calls == []
    assert [row["status"] for row in reused.outcomes] == ["asset_reused"]


def test_provider_unavailable_stops_before_unattempted_targets_and_is_nonterminal(
    temp_engine, tmp_path, monkeypatch
):
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380" and item.bsns_year == 2021)

    def unavailable(_receipt):
        raise fetcher.DartTransportError("document.xml")

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", unavailable)

    report = campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=10)

    assert report.status == "partial"
    assert report.stop_reason == "dart_transport_failure"
    assert report.unattempted_target_count == 4
    assert {(item["corp_code"], item["bsns_year"]) for item in report.outcomes} == {
        (target.corp_code, target.bsns_year),
    }
    stop = next(item for item in report.outcomes if item["status"] == "dart_transport_failure")
    assert stop["company_year_terminal"] is False
    assert not any(item["company_year_terminal"] for item in report.outcomes)


def test_budget_exhaustion_before_separate_audit_xml_discovery_is_explicit(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.collector import fetcher

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    def business(_receipt):
        fetcher._record_request_attempt("document.xml")
        return _business_assets()

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", business)
    monkeypatch.setattr(
        campaign, "fetch_disclosure_list",
        lambda *_args, **_kwargs: (fetcher._record_request_attempt("list.json") or []),
    )

    report = campaign.run_source_archive_shard(plan, target.shard, _Archive(), apply=True, max_dart_calls=1)

    assert any(item["status"] == "dart_budget_exhausted" and item["report_kind"] == "audit_report" for item in report.outcomes)


def test_drive_rate_limit_stops_shard_without_marking_later_targets_or_committing(
    temp_engine, tmp_path, monkeypatch
):
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.storage.drive_archive import DriveArchiveRateLimitError

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380" and item.bsns_year == 2021)
    fetched: list[str] = []

    class _RateLimitedArchive(_Archive):
        def archive_bytes(self, *, data, extension, metadata):
            if metadata.get("archive_version") == "raw-source-v1":
                raise DriveArchiveRateLimitError(
                    "quota exhausted; diagnostic=HTTP 429: Too Many Requests",
                    operation="copyto",
                    attempts=3,
                    cooldown_seconds=240,
                    diagnostic="HTTP 429: Too Many Requests",
                )
            return super().archive_bytes(data=data, extension=extension, metadata=metadata)

    def fetch_business(receipt):
        fetched.append(receipt)
        return _business_assets()

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", fetch_business)
    report = campaign.run_source_archive_shard(
        plan, target.shard, _RateLimitedArchive(), apply=True, max_dart_calls=10
    )

    assert report.status == "partial"
    assert report.stop_reason == "drive_quota_exhausted"
    assert report.unattempted_target_count == len(plan.targets_for_shard(target.shard)) - 1
    assert fetched == [target.source_receipt]
    assert not (plan.state_dir / f"shard-{target.shard:02d}" / "COMMITTED.json").exists()


@pytest.mark.parametrize("failure_name", [
    "DriveArchiveCommandTimeoutError",
    "DriveArchiveCommandError",
])
def test_drive_readback_failure_stops_shard_as_resumable_transport_failure(
    temp_engine, tmp_path, monkeypatch, failure_name
):
    """A temporary Drive readback stall must not crash the continuous runner."""
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.storage import drive_archive

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380" and item.bsns_year == 2021)

    class _TimeoutArchive(_Archive):
        def archive_bytes(self, *, data, extension, metadata):
            if metadata.get("archive_version") == "raw-source-v1":
                failure = getattr(drive_archive, failure_name)
                raise failure("Drive archive cat command failed temporarily.")
            return super().archive_bytes(data=data, extension=extension, metadata=metadata)

    monkeypatch.setattr(campaign, "fetch_document_zip_asset_bytes", lambda _receipt: _business_assets())

    report = campaign.run_source_archive_shard(
        plan, target.shard, _TimeoutArchive(), apply=True, max_dart_calls=10
    )

    assert report.status == "partial"
    assert report.stop_reason == "drive_transport_failure"
    assert report.unattempted_target_count == len(plan.targets_for_shard(target.shard)) - 1
    stop = next(item for item in report.outcomes if item["status"] == "drive_transport_failure")
    assert stop["company_year_terminal"] is False
    assert not (plan.state_dir / f"shard-{target.shard:02d}" / "COMMITTED.json").exists()


def test_campaign_archives_one_event_bundle_per_company_year(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    _install_complete_family(monkeypatch, campaign)
    archive = _Archive()

    campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=3)

    event_calls = [
        call for call in archive.calls
        if call["metadata"]["archive_version"] == "source-archive-campaign-manifest-v1"
    ]
    assert len(event_calls) == 1
    event = json.loads(event_calls[0]["data"])
    assert event["corp_code"] == target.corp_code
    assert event["bsns_year"] == target.bsns_year
    assert len(event["outcomes"]) >= 1


def test_failed_event_archive_remains_in_local_outbox_for_resume(
    temp_engine, tmp_path, monkeypatch
):
    import kreports.maintenance.source_archive_campaign as campaign
    from kreports.storage.drive_archive import DriveArchiveRateLimitError

    _seed_annual_disclosures(temp_engine)
    plan = _plan(temp_engine, [2024]).with_state_dir(tmp_path / "campaign")
    target = next(item for item in plan.targets if item.corp_code == "00126380")
    _install_complete_family(monkeypatch, campaign)

    class _EventRateLimitedArchive(_Archive):
        def archive_bytes(self, *, data, extension, metadata):
            if metadata.get("archive_version") == "source-archive-campaign-manifest-v1":
                raise DriveArchiveRateLimitError(
                    "quota exhausted; diagnostic=HTTP 429: Too Many Requests",
                    operation="copyto",
                    attempts=3,
                    cooldown_seconds=240,
                    diagnostic="HTTP 429: Too Many Requests",
                )
            return super().archive_bytes(data=data, extension=extension, metadata=metadata)

    archive = _EventRateLimitedArchive()
    report = campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=3)

    assert report.stop_reason == "drive_quota_exhausted"
    assert report.drive_metrics["pending_event_bundles"] == 1
    assert list((plan.state_dir / f"shard-{target.shard:02d}" / "outbox").glob("*.json"))
    assert not (plan.state_dir / f"shard-{target.shard:02d}" / "COMMITTED.json").exists()


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
    for command in (
        "source-archive-preflight", "source-archive-plan", "source-archive-run",
        "source-archive-auto-run", "source-archive-verify",
    ):
        assert command in result.output


def test_run_source_archive_shard_skips_business_family_when_only_audit_report_required(
    temp_engine, tmp_path, monkeypatch,
):
    import kreports.maintenance.source_archive_campaign as campaign

    monkeypatch.setattr(
        campaign, "fetch_document_zip_asset_bytes",
        lambda _receipt: _BusinessAssets({"감사보고서.xml": b"<DOCUMENT><P>audit</P></DOCUMENT>"}),
    )
    target = campaign.SourceArchiveTarget(
        corp_code="00000099", bsns_year=2023, market=None, shard=0,
        source_receipt="20240101000099", report_nm="감사보고서",
        source_uri="https://opendart.fss.or.kr/api/document.xml?rcept_no=20240101000099",
        source_status="discovered", required_report_kinds=("audit_report",),
        universe_cohort="audit_report_only_no_business_report",
        historical_listing_status="unclassified",
        historical_listing_basis="audit_report_receipt_without_business_report",
    )
    plan = campaign.SourceArchivePlan(
        years=(2023,), shard_count=1, targets=(target,),
        target_digest="test-digest-audit-only", universe_mode="audit_report_only",
    ).with_state_dir(tmp_path / "audit-only-campaign")
    archive = _Archive()

    report = campaign.run_source_archive_shard(plan, 0, archive, apply=True, max_dart_calls=3)

    payload = report.to_dict()
    assert payload["status"] == "complete"
    report_kinds = {outcome["report_kind"] for outcome in payload["outcomes"]}
    assert "business_report" not in report_kinds
    assert "audit_report" in report_kinds
    terminal = next(
        outcome for outcome in payload["outcomes"]
        if outcome["report_kind"] == "company_year" and outcome["company_year_terminal"]
    )
    assert terminal["status"] == "structurally_complete"


def test_separate_audit_receipts_excludes_internal_control_report(monkeypatch):
    import kreports.maintenance.source_archive_campaign as campaign

    def fake_fetch(_corp_code, _start, _end, disc_type=""):
        return [
            {"report_nm": "내부회계관리제도감사보고서", "rcept_no": "20220101000001", "rcept_dt": "20220331"},
            {"report_nm": "감사보고서 (첨부:재무제표)", "rcept_no": "20220101000002", "rcept_dt": "20220331"},
        ]

    monkeypatch.setattr(campaign, "fetch_disclosure_list", fake_fetch)
    target = campaign.SourceArchiveTarget(
        corp_code="00000001", bsns_year=2021, market="KOSPI", shard=0,
        source_receipt="x", report_nm="사업보고서 (2021.12)", source_uri="x",
        source_status="discovered",
    )

    receipts = campaign._separate_audit_receipts(target)

    assert receipts == ("20220101000002",)


def test_audit_report_only_plan_finds_business_gap_and_audit_only_targets_and_respects_exclusions(
    temp_engine,
):
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import build_source_archive_plan

    with sessionmaker(bind=temp_engine)() as session:
        session.add_all([
            Company(corp_code="00200001", corp_name="정상사업보고서법인"),
            Company(corp_code="00200002", corp_name="감사보고서전용법인"),
            Company(corp_code="00200003", corp_name="제외대상법인"),
        ])
        for year in (2021, 2022, 2023):
            session.add(_membership("00299999", year, "KOSPI"))
            session.add(_membership("00299998", year, "KOSDAQ"))
        session.add(Disclosure(
            rcept_no="20240331200001", corp_code="00200001", corp_name="정상사업보고서법인",
            disc_date=date(2024, 3, 31), disc_type="A", report_nm="사업보고서 (2023.12)",
        ))
        session.add(Disclosure(
            rcept_no="20220331200002", corp_code="00200002", corp_name="감사보고서전용법인",
            disc_date=date(2022, 3, 31), disc_type="F", report_nm="감사보고서",
        ))
        session.add(Disclosure(
            rcept_no="20240331200003", corp_code="00200003", corp_name="제외대상법인",
            disc_date=date(2024, 3, 31), disc_type="A", report_nm="사업보고서 (2023.12)",
        ))
        session.commit()

    excluded = frozenset({("00200003", 2023)})
    with sessionmaker(bind=temp_engine)() as session:
        plan = build_source_archive_plan(
            session, years=[2021, 2022, 2023], universe_mode="audit_report_only",
            excluded_pairs=excluded,
        )

    by_pair = {(t.corp_code, t.bsns_year): t for t in plan.targets}

    assert ("00200001", 2023) in by_pair
    gap_fill_target = by_pair[("00200001", 2023)]
    assert gap_fill_target.required_report_kinds == ("business_report", "audit_report")
    assert gap_fill_target.universe_cohort == "annual_report_issuer_outside_verified_markets"

    assert ("00200002", 2021) in by_pair
    audit_only_target = by_pair[("00200002", 2021)]
    assert audit_only_target.required_report_kinds == ("audit_report",)
    assert audit_only_target.source_receipt == "20220331200002"
    assert audit_only_target.universe_cohort == "audit_report_only_no_business_report"

    assert ("00200003", 2023) not in by_pair
    assert ("00299999", 2021) not in by_pair  # verified KOSPI/KOSDAQ rows never become targets here

    assert plan.campaign_counts["cohort_counts"] == {
        "annual_report_issuer_outside_verified_markets": 1,
        "audit_report_only_no_business_report": 1,
    }
    assert plan.target_manifest["universe_mode"] == "audit_report_only"
    assert "cohort_counts" in plan.target_manifest


def test_build_source_archive_plan_rejects_excluded_pairs_outside_audit_report_only(temp_engine):
    from sqlalchemy.orm import sessionmaker
    from kreports.maintenance.source_archive_campaign import (
        SourceArchiveCampaignError,
        build_source_archive_plan,
    )

    with sessionmaker(bind=temp_engine)() as session:
        for year in (2021,):
            session.add(_membership("00299999", year, "KOSPI"))
            session.add(_membership("00299998", year, "KOSDAQ"))
        session.commit()

    with sessionmaker(bind=temp_engine)() as session:
        with pytest.raises(SourceArchiveCampaignError, match="excluded_pairs"):
            build_source_archive_plan(
                session, years=[2021], universe_mode="all_annual_issuers",
                excluded_pairs=frozenset({("00299999", 2021)}),
            )


def test_audit_report_only_report_and_verify_use_v3_schema(temp_engine, tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    import kreports.maintenance.source_archive_campaign as campaign

    with sessionmaker(bind=temp_engine)() as session:
        session.add(Company(corp_code="00200002", corp_name="감사보고서전용법인"))
        session.add(Disclosure(
            rcept_no="20220331200002", corp_code="00200002", corp_name="감사보고서전용법인",
            disc_date=date(2022, 3, 31), disc_type="F", report_nm="감사보고서",
        ))
        for year in (2021,):
            session.add(_membership("00299999", year, "KOSPI"))
            session.add(_membership("00299998", year, "KOSDAQ"))
        session.commit()

    with sessionmaker(bind=temp_engine)() as session:
        plan = campaign.build_source_archive_plan(
            session, years=[2021], universe_mode="audit_report_only",
        ).with_state_dir(tmp_path / "audit-only-campaign")

    assert len(plan.targets) == 1
    _install_complete_family(monkeypatch, campaign)
    archive = _Archive()
    target = plan.targets[0]

    report = campaign.run_source_archive_shard(plan, target.shard, archive, apply=True, max_dart_calls=3)
    payload = report.to_dict()
    assert payload["schema"] == campaign.ALL_ISSUER_CAMPAIGN_SCHEMA
    assert payload["universe_mode"] == "audit_report_only"

    campaign.write_source_archive_plan_preview(plan, plan.state_dir)
    verify_result = campaign.verify_source_archive_campaign(plan.state_dir, shard=target.shard)
    assert verify_result["universe_mode"] == "audit_report_only"


def test_source_archive_preflight_cli_accepts_audit_report_only_universe_and_exclude_manifest(
    temp_engine, tmp_path,
):
    from sqlalchemy.orm import sessionmaker
    from typer.testing import CliRunner
    import kreports.cli.main as cli

    with sessionmaker(bind=temp_engine)() as session:
        session.add_all([
            Company(corp_code="00200001", corp_name="정상사업보고서법인"),
        ])
        for year in (2021,):
            session.add(_membership("00299999", year, "KOSPI"))
            session.add(_membership("00299998", year, "KOSDAQ"))
        session.add(Disclosure(
            rcept_no="20220331200001", corp_code="00200001", corp_name="정상사업보고서법인",
            disc_date=date(2022, 3, 31), disc_type="A", report_nm="사업보고서 (2021.12)",
        ))
        session.commit()

    # A non-empty manifest (even with a non-matching pair) keeps excluded_pairs
    # truthy so `_source_archive_plan_from_database`'s missing-manifest warning
    # (fired whenever excluded_pairs is empty) doesn't prepend stderr text to
    # this CLI's stdout JSON payload.
    exclude_manifest = tmp_path / "exclude.json"
    exclude_manifest.write_text(
        json.dumps({"targets": [{"corp_code": "00999999", "bsns_year": 1900}]}),
        encoding="utf-8",
    )

    # settings.db_url stays at its test-config default (unrelated to db_path) so
    # `_source_archive_plan_from_database`'s "must not equal the runtime DB" guard
    # does not fire against our own candidate file.
    db_path = tmp_path / "candidate.db"
    from sqlalchemy import create_engine
    file_engine = create_engine(f"sqlite:///{db_path}")
    from kreports.db.models import Base
    Base.metadata.create_all(bind=file_engine)
    with sessionmaker(bind=temp_engine)() as source_session, sessionmaker(bind=file_engine)() as dest_session:
        for table in Base.metadata.sorted_tables:
            rows = source_session.execute(table.select()).mappings().all()
            if rows:
                dest_session.execute(table.insert(), [dict(row) for row in rows])
        dest_session.commit()

    result = CliRunner().invoke(cli.app, [
        "source-archive-preflight", "--db", str(db_path), "--year", "2021",
        "--universe", "audit-report-only", "--exclude-manifest", str(exclude_manifest),
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["target_count"] == 1


def test_source_archive_plan_from_database_warns_when_audit_report_only_has_no_exclusions(
    temp_engine, tmp_path,
):
    from sqlalchemy.orm import sessionmaker
    import kreports.cli.main as cli

    with sessionmaker(bind=temp_engine)() as session:
        for year in (2021,):
            session.add(_membership("00299999", year, "KOSPI"))
            session.add(_membership("00299998", year, "KOSDAQ"))
        session.commit()

    db_path = tmp_path / "candidate.db"
    from sqlalchemy import create_engine
    file_engine = create_engine(f"sqlite:///{db_path}")
    from kreports.db.models import Base
    Base.metadata.create_all(bind=file_engine)
    with sessionmaker(bind=temp_engine)() as source_session, sessionmaker(bind=file_engine)() as dest_session:
        for table in Base.metadata.sorted_tables:
            rows = source_session.execute(table.select()).mappings().all()
            if rows:
                dest_session.execute(table.insert(), [dict(row) for row in rows])
        dest_session.commit()

    from typer.testing import CliRunner
    result = CliRunner().invoke(cli.app, [
        "source-archive-preflight", "--db", str(db_path), "--year", "2021",
        "--universe", "audit-report-only",
    ])

    assert result.exit_code == 0, result.output
    assert "경고" in result.output or "exclude-manifest" in result.output


def test_source_archive_discover_gaps_cli_reports_counts_and_upserts_companies(
    temp_engine, monkeypatch,
):
    from typer.testing import CliRunner
    import kreports.cli.main as cli
    from kreports.collector import disc_collector
    from kreports.db.engine import get_session
    from kreports.db.models import Company

    monkeypatch.setattr(cli.settings, "dart_api_key", "test-key")

    def fake_audit_disclosure_window(
        *, start_date, end_date, disc_type, report_keyword=None,
        exclude_keywords=None, persist_missing=False, **_kwargs,
    ):
        if disc_type == "F":
            if persist_missing:
                with get_session() as session:
                    session.add(Disclosure(
                        rcept_no="20220331900001", corp_code="00900001", corp_name="신규비상장법인",
                        disc_date=date(2022, 3, 31), disc_type="F", report_nm="감사보고서",
                    ))
            return {
                "target_rows": 1, "missing_rows": 1,
                "saved_missing": 1 if persist_missing else 0, "missing_samples": [],
                "verdict": "pass", "errors": [],
            }
        return {
            "target_rows": 0, "missing_rows": 0, "saved_missing": 0, "missing_samples": [],
            "verdict": "pass", "errors": [],
        }

    monkeypatch.setattr(disc_collector, "audit_disclosure_window", fake_audit_disclosure_window)

    result = CliRunner().invoke(cli.app, ["source-archive-discover-gaps", "--apply"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["audit_only"]["saved_missing"] == 1
    assert payload["audit_only"]["verdict"] == "pass"
    assert payload["new_companies_upserted"] == 1
    with get_session() as session:
        row = session.get(Company, "00900001")
        assert row is not None
        assert row.corp_name == "신규비상장법인"


def test_source_archive_discover_gaps_cli_dry_run_does_not_upsert(temp_engine, monkeypatch):
    from typer.testing import CliRunner
    import kreports.cli.main as cli
    from kreports.collector import disc_collector

    monkeypatch.setattr(cli.settings, "dart_api_key", "test-key")
    monkeypatch.setattr(
        disc_collector, "audit_disclosure_window",
        lambda **_kwargs: {
            "target_rows": 5, "missing_rows": 5, "saved_missing": 0, "missing_samples": [],
            "verdict": "pass", "errors": [],
        },
    )

    result = CliRunner().invoke(cli.app, ["source-archive-discover-gaps"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["apply"] is False
    assert payload["new_companies_upserted"] == 0


def test_source_archive_discover_gaps_cli_exits_nonzero_when_a_sweep_fails(temp_engine, monkeypatch):
    from typer.testing import CliRunner
    import kreports.cli.main as cli
    from kreports.collector import disc_collector

    monkeypatch.setattr(cli.settings, "dart_api_key", "test-key")

    def fake_audit_disclosure_window(*, disc_type, **_kwargs):
        if disc_type == "F":
            return {
                "target_rows": 0, "missing_rows": 0, "saved_missing": 0,
                "missing_samples": [], "verdict": "fail",
                "errors": [{"start_date": "20210101", "end_date": "20210131", "error": "boom"}],
            }
        return {
            "target_rows": 0, "missing_rows": 0, "saved_missing": 0,
            "missing_samples": [], "verdict": "pass", "errors": [],
        }

    monkeypatch.setattr(disc_collector, "audit_disclosure_window", fake_audit_disclosure_window)

    result = CliRunner().invoke(cli.app, ["source-archive-discover-gaps"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["audit_only"]["verdict"] == "fail"
    assert payload["audit_only"]["error_count"] == 1


def test_source_archive_discover_gaps_cli_succeeds_when_gaps_found_with_no_errors(temp_engine, monkeypatch):
    from typer.testing import CliRunner
    import kreports.cli.main as cli
    from kreports.collector import disc_collector

    monkeypatch.setattr(cli.settings, "dart_api_key", "test-key")

    def fake_audit_disclosure_window(**_kwargs):
        # Mirrors the real audit_disclosure_window's own verdict formula:
        # verdict is "fail" whenever missing_rows > 0, even with zero errors.
        # The CLI command must gate its exit code on errors, not verdict.
        return {
            "target_rows": 5, "missing_rows": 3, "saved_missing": 0,
            "missing_samples": [], "verdict": "fail", "errors": [],
        }

    monkeypatch.setattr(disc_collector, "audit_disclosure_window", fake_audit_disclosure_window)

    result = CliRunner().invoke(cli.app, ["source-archive-discover-gaps"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["audit_only"]["verdict"] == "fail"
    assert payload["audit_only"]["error_count"] == 0
