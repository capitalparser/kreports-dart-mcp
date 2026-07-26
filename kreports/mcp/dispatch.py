"""Single validation, dispatch, metadata, and exception-normalization boundary."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mcp.types import Tool
from pydantic import ValidationError
from sqlalchemy import func

from kreports.analysis.api import resolve_corp_code, search_company
from kreports.db.engine import get_session
from kreports.db.models import (
    AccountingPolicyItem,
    AuditFee,
    Auditor,
    Company,
    Disclosure,
    Financial,
    FinancialFact,
)
from kreports.mcp.contracts import (
    AnswerEnvelopeV1,
    build_answer_envelope,
    enrich_answer_response,
)


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _extract_result_corp_code(result: dict[str, Any]) -> str | None:
    corp_code = result.get("corp_code")
    if isinstance(corp_code, str) and corp_code:
        return corp_code
    subject = result.get("subject")
    if isinstance(subject, dict):
        corp_code = subject.get("corp_code")
        if isinstance(corp_code, str) and corp_code:
            return corp_code
    return None


def _company_meta(corp_code: str) -> dict[str, Any] | None:
    with get_session() as session:
        row = session.query(Company).filter_by(corp_code=corp_code).first()
        if row is None:
            return None
        return {
            "corp_code": row.corp_code,
            "corp_name": row.corp_name,
            "stock_code": row.stock_code,
            "market": row.market,
            "induty_code": row.induty_code or row.sector,
        }


def _data_freshness(corp_code: str) -> dict[str, str | None]:
    table_map = {
        "financial": Financial,
        "financial_fact": FinancialFact,
        "disclosure": Disclosure,
        "auditor": Auditor,
        "audit_fee": AuditFee,
        "accounting_policy": AccountingPolicyItem,
    }
    with get_session() as session:
        freshness: dict[str, str | None] = {}
        for key, model in table_map.items():
            try:
                freshness[key] = _to_iso(
                    session.query(func.max(model.fetched_at))
                    .filter(model.corp_code == corp_code)
                    .scalar()
                )
            except Exception:
                freshness[key] = None
        return freshness


def _attach_meta(name: str, result: Any) -> Any:
    """Attach the single shared metadata and professional-response layer."""
    if not isinstance(result, dict):
        return result
    enriched = dict(result)
    meta = dict(enriched.get("_meta") or {})
    meta.update(
        {
            "tool": name,
            "source": "local_kreports_db",
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "limitations": [
                "MCP 응답은 로컬 kreports.db에 수집된 DART/OpenDART 기반 캐시입니다.",
                "중요 판단 전 data_freshness와 원 공시 접수번호를 확인하세요.",
            ],
        }
    )
    corp_code = _extract_result_corp_code(enriched)
    if corp_code:
        try:
            meta["company"] = _company_meta(corp_code)
            meta["data_freshness"] = _data_freshness(corp_code)
        except Exception as exc:
            meta["meta_error"] = f"{type(exc).__name__}: {exc}"
    if enriched.get("parent_rcept_no"):
        meta["source_rcept_no"] = enriched["parent_rcept_no"]
    if enriched.get("bsns_year") is not None:
        meta["bsns_year"] = enriched["bsns_year"]
    if name == "search_company":
        meta["result_count"] = enriched.get("count", 0)
    enriched["_meta"] = meta
    return enrich_answer_response(name, enriched)


def _format_candidates(candidates: list[dict[str, Any]]) -> str:
    labels = [
        f"{row.get('corp_name')}({row.get('stock_code') or '-'}, {row.get('corp_code')})"
        for row in candidates[:5]
    ]
    suffix = "" if len(candidates) <= 5 else f" 외 {len(candidates) - 5}건"
    return ", ".join(labels) + suffix


def resolve_company(identifier: str) -> str:
    """Resolve one company identifier; ambiguous names fail closed."""
    raw = "" if identifier is None else str(identifier).strip()
    if not raw:
        raise ValueError("회사 식별자(company)가 필요합니다.")
    if raw.isdigit() and len(raw) in (6, 8):
        corp_code = resolve_corp_code(raw)
        if corp_code is not None:
            return corp_code
        raise ValueError(
            f"'{raw}'에 해당하는 기업을 찾을 수 없습니다. "
            "corp_code(8자리), 종목코드(6자리) 또는 정확한 회사명을 입력하세요."
        )
    hits = search_company(raw, limit=10)
    exact = [row for row in hits if row.get("corp_name") == raw]
    if len(exact) == 1:
        return exact[0]["corp_code"]
    if len(hits) == 1:
        return hits[0]["corp_code"]
    if len(hits) > 1:
        raise ValueError(
            f"'{raw}' 회사명이 모호합니다. 종목코드나 corp_code로 다시 호출하세요. "
            f"후보: {_format_candidates(hits)}"
        )
    raise ValueError(
        f"'{raw}'에 해당하는 기업을 찾을 수 없습니다. "
        "corp_code(8자리), 종목코드(6자리) 또는 정확한 회사명을 입력하세요."
    )


def _bounded_validation_message(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors(include_url=False, include_context=False, include_input=False):
        location = ".".join(str(part) for part in error.get("loc", ())) or "arguments"
        parts.append(f"{location}: {error.get('msg', 'invalid value')}")
        if len(parts) == 5:
            break
    return ("입력값 검증 오류: " + "; ".join(parts))[:500]


def _error_envelope(name: str, message: str) -> AnswerEnvelopeV1:
    bounded = str(message).replace("\n", " ")[:500]
    return build_answer_envelope(name, {"error": bounded, "answer": bounded})


def _safe_exception_message(
    exc: Exception,
    arguments: dict[str, Any] | None,
) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if isinstance(arguments, dict):
        secret = arguments.get("user_dart_api_key")
        if secret is not None:
            raw_secret = (
                secret.get_secret_value()
                if hasattr(secret, "get_secret_value")
                else str(secret)
            )
            if raw_secret:
                message = message.replace(raw_secret, "[REDACTED]")
    return message


def _validated_result(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    from kreports.mcp.catalog import TOOL_CATALOG

    spec = TOOL_CATALOG.get(name)
    if spec is None:
        raise LookupError(f"Unknown tool: {name}")
    try:
        validated = spec.input_model.model_validate(arguments or {})
    except ValidationError as exc:
        raise ValueError(_bounded_validation_message(exc)) from None
    result = spec.handler(validated)
    if not isinstance(result, dict):
        result = {"value": result}
    return _attach_meta(name, result)


def dispatch_tool(name: str, arguments: dict[str, Any] | None) -> AnswerEnvelopeV1:
    """Validate once, invoke once, and always return the v1 answer envelope."""
    try:
        result = _validated_result(name, arguments)
        return build_answer_envelope(name, result)
    except (LookupError, ValueError) as exc:
        return _error_envelope(name, str(exc))
    except Exception as exc:
        return _error_envelope(
            name,
            f"Internal error: {_safe_exception_message(exc, arguments)}",
        )


def legacy_result(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility result retaining domain fields for existing Python callers."""
    try:
        return _validated_result(name, arguments)
    except LookupError:
        from kreports.mcp.catalog import TOOL_CATALOG

        return {"error": f"Unknown tool: {name}", "available": list(TOOL_CATALOG)}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {
            "error": f"Internal error: {_safe_exception_message(exc, arguments)}"
        }


def list_mcp_tools() -> list[Tool]:
    from kreports.mcp.catalog import TOOL_CATALOG

    return [spec.to_mcp_tool() for spec in TOOL_CATALOG.values()]
