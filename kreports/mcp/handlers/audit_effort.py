"""Handlers for public audit-effort input preparation."""
from __future__ import annotations

from kreports.analysis.audit_effort_inputs import prepare_standard_audit_hours_inputs
from kreports.analysis.materiality_benchmark import prepare_audit_materiality_inputs
from kreports.mcp.dispatch import resolve_company
from kreports.mcp.input_models import PrepareStandardAuditHoursInputsInput
from kreports.mcp.input_models import PrepareAuditMaterialityInputsInput


def handle_prepare_standard_audit_hours_inputs(
    args: PrepareStandardAuditHoursInputsInput,
) -> dict:
    return prepare_standard_audit_hours_inputs(
        resolve_company(args.company),
        year=args.year,
        fs_strategy=args.fs_strategy,
    )


def handle_prepare_audit_materiality_inputs(
    args: PrepareAuditMaterialityInputsInput,
) -> dict:
    return prepare_audit_materiality_inputs(
        resolve_company(args.company),
        end_year=args.end_year,
        years_back=args.years_back,
        fs_strategy=args.fs_strategy,
    )
