import httpx

from kreports.collector.audit_fee_sources import normalize_endpoint_result
from kreports.collector import fetcher
from kreports.security import redact_external_error


def test_redact_external_error_removes_query_credentials_from_http_status_error():
    """Catches storage or logging of a DART URL containing crtfc_key."""
    request = httpx.Request(
        "GET",
        "https://opendart.fss.or.kr/api/adtServcCnclsSttus.json?"
        "crtfc_key=server-secret&corp_code=00126380",
    )
    response = httpx.Response(401, request=request)
    with_error = None
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        with_error = exc

    assert with_error is not None
    rendered = redact_external_error(with_error)

    assert rendered == "external HTTP request failed (status=401)"
    assert "server-secret" not in rendered
    assert "crtfc_key" not in rendered


def test_audit_fee_observation_redacts_external_url_before_persistence_or_public_rendering():
    """Catches a DART exception URL reaching an audit-fee observation."""
    observation = normalize_endpoint_result(
        corp_code="00126380",
        year=2025,
        status="ERR",
        rows=[],
        message=(
            "Client error for url "
            "https://opendart.fss.or.kr/api/adtServcCnclsSttus.json?"
            "crtfc_key=server-secret&corp_code=00126380"
        ),
    )

    assert observation.source_message == "external error details redacted"
    assert observation.limitations == ("external error details redacted",)


def test_fetch_audit_fee_does_not_return_or_log_dart_query_credentials(monkeypatch, caplog):
    """Catches the raw httpx exception being passed into the audit-fee pipeline."""
    request = httpx.Request(
        "GET",
        "https://opendart.fss.or.kr/api/adtServcCnclsSttus.json?"
        "crtfc_key=server-secret&corp_code=00126380",
    )
    response = httpx.Response(401, request=request)

    class FailingClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            response.raise_for_status()

    monkeypatch.setattr(fetcher.settings, "dart_api_key", "configured-key")
    monkeypatch.setattr(fetcher, "_get_client", lambda: FailingClient())

    result = fetcher.fetch_audit_fee("00126380", 2025)

    assert result == {
        "status": "ERR",
        "message": "external HTTP request failed (status=401)",
    }
    assert "server-secret" not in caplog.text
    assert "crtfc_key" not in caplog.text
