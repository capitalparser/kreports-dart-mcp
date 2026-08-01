"""Validation contracts for public MCP input models."""

import pytest
from pydantic import ValidationError


def test_fetch_disclosure_rejects_non_dart_receipt_number():
    from kreports.mcp.input_models import FetchDisclosureOnDemandInput

    with pytest.raises(ValidationError, match="14자리"):
        FetchDisclosureOnDemandInput(rcept_no="2025-invalid")


def test_search_disclosure_events_rejects_invalid_date_range_values():
    from kreports.mcp.input_models import SearchDisclosureEventsInput

    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        SearchDisclosureEventsInput(
            start_date="2025-13-01",
            end_date="2025-01-01",
        )


def test_search_disclosure_events_rejects_reverse_date_range():
    from kreports.mcp.input_models import SearchDisclosureEventsInput

    with pytest.raises(ValidationError, match="늦을 수 없습니다"):
        SearchDisclosureEventsInput(
            start_date="2025-12-31",
            end_date="2025-01-01",
        )
