"""Handlers for public audit-effort input preparation."""
from __future__ import annotations

from kreports.analysis.audit_effort_inputs import prepare_standard_audit_hours_inputs
from kreports.mcp.dispatch import resolve_company
from kreports.mcp.input_models import PrepareStandardAuditHoursInputsInput


def handle_prepare_standard_audit_hours_inputs(
    args: PrepareStandardAuditHoursInputsInput,
) -> dict:
    return prepare_standard_audit_hours_inputs(
        resolve_company(args.company),
        year=args.year,
        fs_strategy=args.fs_strategy,
    )
