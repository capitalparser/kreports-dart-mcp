from __future__ import annotations


def _release_context(*, ready: bool = False) -> dict:
    return {
        "release_ready": ready,
        "manifest_available": False,
        "required_failures": ["release_manifest_unavailable"],
        "degraded_features": ["audit_report_sections"],
        "snapshot_version": "snapshot-2026-07-29",
    }


def _usable_business_result() -> dict:
    source = {
        "rcept_no": "20260310002820",
        "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260310002820",
    }
    return {
        "sections": {"business_overview": {"body_text": "공시된 사업 개요"}},
        "confirmed_facts": [{"statement": "공시 사실", "source": source}],
        "data_quality": {"status": "usable"},
    }


def test_release_context_fails_closed_and_bounds_readiness_payload(monkeypatch):
    from kreports.mcp import resources

    monkeypatch.setattr(resources, "_dataset_readiness", lambda: {
        "release_ready": True,
        "manifest_available": True,
        "required_failures": [f"failure_{index}" for index in range(12)],
        "degraded_features": [f"feature_{index}" for index in range(12)],
        "dataset_version": "snapshot-2026-07-29",
    })
    context = resources.release_context()

    assert context == {
        "release_ready": True,
        "manifest_available": True,
        "required_failures": [f"failure_{index}" for index in range(10)],
        "degraded_features": [f"feature_{index}" for index in range(10)],
        "snapshot_version": "snapshot-2026-07-29",
    }

    monkeypatch.setattr(
        resources,
        "_dataset_readiness",
        lambda: (_ for _ in ()).throw(RuntimeError("private db path")),
    )
    assert resources.release_context() == {
        "release_ready": False,
        "manifest_available": False,
        "required_failures": ["release_context_unavailable"],
        "degraded_features": [],
        "snapshot_version": None,
    }


def test_release_context_is_identical_in_answer_pack_and_resource_without_downgrading_question(
    monkeypatch,
):
    from kreports.mcp import dispatch
    from kreports.mcp.contracts import build_answer_envelope
    from kreports.mcp.resources import read_resource

    expected = _release_context(ready=False)
    monkeypatch.setattr(dispatch, "release_context", lambda: expected)

    enriched = dispatch._attach_meta("get_business_overview", _usable_business_result())
    envelope = build_answer_envelope("get_business_overview", enriched)
    pack = enriched["answer_pack"]
    resource = read_resource(pack["resource_uri"])

    assert enriched["data_quality"]["status"] == "usable"
    assert envelope.verdict == "usable"
    assert envelope.release_context.model_dump() == expected
    assert all("release_context" not in fact for fact in envelope.confirmed_facts)
    assert all("release_context" not in ref.model_dump() for ref in envelope.evidence)
    assert pack["release_context"] == expected
    assert "배포 준비 상태" in enriched["answer"]
    assert "snapshot-2026-07-29" in enriched["answer"]
    assert "release_manifest_unavailable" in enriched["answer"]
    assert "snapshot-2026-07-29" in resource["text"]
    assert "release_manifest_unavailable" in resource["text"]


def test_release_context_reaches_custom_professional_surface_pack(monkeypatch):
    from types import SimpleNamespace

    from kreports.mcp import answer_pack

    expected = _release_context(ready=False)
    monkeypatch.setattr(
        answer_pack,
        "build_answer_envelope",
        lambda *_args: SimpleNamespace(
            data_quality=SimpleNamespace(
                status="usable",
                model_dump=lambda: {"status": "usable"},
            ),
        ),
    )
    raw = {
        "subject": {"corp_code": "00126380", "corp_name": "테스트회사"},
        "rows": [{"year": 2025, "fs_div": "CFS", "input_status": "complete"}],
        "data_quality": {"status": "usable"},
        "_meta": {"release_context": expected},
    }

    pack = answer_pack.build_answer_pack("prepare_standard_audit_hours_inputs", raw)

    assert pack is not None
    assert pack["release_context"] == expected
