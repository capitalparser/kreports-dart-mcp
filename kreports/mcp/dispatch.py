"""Single validation, dispatch, metadata, and exception-normalization boundary."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from mcp.types import Tool
from pydantic import BaseModel, SecretStr, ValidationError
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
from kreports.mcp.resources import release_context

_MAX_TOOL_NAME_LENGTH = 120


class ArgumentValidationError(ValueError):
    """Bounded public argument error, distinct from handler ValueError."""


class HandlerExecutionError(Exception):
    """Carries a handler failure and its validated secret without rendering it."""

    def __init__(
        self,
        original: Exception,
        validated_secret: str | None,
        public_context: dict[str, str],
    ) -> None:
        super().__init__(type(original).__name__)
        self.original = original
        self.validated_secret = validated_secret
        self.public_context = public_context


def _bounded_tool_name(name: object) -> str:
    return str(name)[:_MAX_TOOL_NAME_LENGTH]


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
            "release_context": release_context(),
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
    envelope = build_answer_envelope(name, {"error": bounded, "answer": bounded})
    # Input validation messages are generated from the public schema, unlike
    # handler errors that the peer/DCF quarantine must suppress.
    return envelope.model_copy(update={"answer": bounded})


_HANDLER_FAILURE_MESSAGE = (
    "로컬 캐시 스키마 또는 준비된 데이터에 접근할 수 없습니다. "
    "이는 원 공시 부재를 뜻하지 않습니다. "
    "민감한 내부 오류 정보는 [REDACTED] 처리되었습니다."
)

_HANDLER_FAILURE_CONTEXT = {
    "search_dataset": "요청한 공시 근거 조회를 완료하지 못했습니다.",
    "compare_peer_kam_topics": "핵심감사사항(KAM) 동종업종 비교를 완료하지 못했습니다.",
    "build_audit_acceptance_pack": "수임·계속감사 검토용 근거 구성을 완료하지 못했습니다.",
    "search_audit_report_matters": "감사보고서 핵심사항 조회를 완료하지 못했습니다.",
    "get_audit_report_sections": "감사보고서 KAM·감사절차 근거 조회를 완료하지 못했습니다.",
}


def _handler_failure_context(
    name: str,
    public_context: dict[str, str] | None = None,
) -> str:
    context = _HANDLER_FAILURE_CONTEXT.get(
        name,
        "요청한 공시 근거 검토를 완료하지 못했습니다.",
    )
    company = (public_context or {}).get("company")
    if company:
        return f"{context} 요청 회사: {company}."
    return context


def _handler_failure_result(
    name: str,
    *,
    public_context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a standalone bounded error without using the enrichment path."""
    next_check = "로컬 캐시 스키마와 준비된 데이터 artifact를 확인한 뒤 다시 배포하세요."
    quality = {
        "status": "error",
        "grade": None,
        "dataset_version": "unknown",
        "schema_version": "unknown",
        "covered_years": [],
        "missing_fields": [],
        "limitations": [_HANDLER_FAILURE_MESSAGE],
        "section_statuses": {},
    }
    release = {
        "release_ready": False,
        "manifest_available": False,
        "required_failures": ["release_context_unavailable"],
        "degraded_features": [],
        "snapshot_version": None,
    }
    answer = "\n".join([
        "판정:",
        "- error",
        "",
        "업무 결론:",
        f"- {_handler_failure_context(name, public_context)}",
        "",
        "확인된 내용:",
        "- 현재 오류 상태에서는 검증 가능한 근거를 제시하지 않습니다.",
        "",
        "데이터 한계:",
        f"- {_HANDLER_FAILURE_MESSAGE}",
        "",
        "추가 확인사항:",
        f"- {next_check}",
    ])
    pack = {
        "kind": "answer_pack",
        "version": "answer_pack.v1",
        "summary": {"title": "데이터 가용성", "status": "error", "subject": "대상 조건"},
        "tables": [{
            "id": "availability",
            "title": "데이터 가용성",
            "columns": [{"field": "status", "label": "상태"}],
            "rows": [{"status": "error"}],
            "status": "error",
        }],
        "charts": [],
        "diagrams": [],
        "timelines": [],
        "sources": [],
        "data_quality": quality,
        "limitations": [_HANDLER_FAILURE_MESSAGE],
        "release_context": release,
    }
    return {
        "error": _HANDLER_FAILURE_MESSAGE,
        "answer": answer,
        "domain_verdict": None,
        "confirmed_facts": [],
        "analysis": [],
        "evidence": [],
        "data_quality": quality,
        "release_context": release,
        "warnings": [_HANDLER_FAILURE_MESSAGE],
        "next_checks": [next_check],
        "answer_pack": pack,
    }


def _handler_failure_envelope(
    name: str,
    *,
    public_context: dict[str, str] | None = None,
) -> AnswerEnvelopeV1:
    result = _handler_failure_result(name, public_context=public_context)
    return AnswerEnvelopeV1(
        tool_name=name,
        verdict="error",
        domain_verdict=None,
        answer=result["answer"],
        confirmed_facts=[],
        analysis=[],
        evidence=[],
        data_quality=result["data_quality"],
        release_context=result["release_context"],
        warnings=result["warnings"],
        next_checks=result["next_checks"],
        answer_pack=result["answer_pack"],
    )


def _safe_exception_message(
    exc: Exception,
    arguments: dict[str, Any] | None,
    *,
    validated_secret: str | None = None,
) -> str:
    message = f"{type(exc).__name__}: {exc}"
    secrets: list[str] = []
    if isinstance(arguments, dict):
        secret = arguments.get("user_dart_api_key")
        if secret is not None:
            raw_secret = (
                secret.get_secret_value()
                if hasattr(secret, "get_secret_value")
                else str(secret)
            )
            if raw_secret:
                secrets.extend((raw_secret, raw_secret.strip()))
    if validated_secret:
        secrets.append(validated_secret)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if not secret:
            continue
        if len(secret) >= 4:
            message = message.replace(secret, "[REDACTED]")
        else:
            message = re.sub(
                rf"(?<!\w){re.escape(secret)}(?!\w)",
                "[REDACTED]",
                message,
            )
    return message


def _validated_secret(arguments: BaseModel) -> str | None:
    secret = getattr(arguments, "user_dart_api_key", None)
    if isinstance(secret, SecretStr):
        return secret.get_secret_value()
    return None


def _validated_public_context(arguments: BaseModel) -> dict[str, str]:
    company = getattr(arguments, "company", None)
    if not isinstance(company, str):
        return {}
    company = company.strip()
    if not company or len(company) > 120:
        return {}
    if not re.fullmatch(r"[0-9A-Za-z가-힣 .()&_-]+", company):
        return {}
    return {"company": company}


def _invoke_handler(
    name: str,
    arguments: dict[str, Any] | None,
    *,
    capture_failure: bool,
) -> dict[str, Any]:
    from kreports.mcp.catalog import TOOL_CATALOG

    spec = TOOL_CATALOG.get(name)
    if spec is None:
        raise LookupError(f"Unknown tool: {_bounded_tool_name(name)}")
    try:
        validated = spec.input_model.model_validate(arguments or {})
    except ValidationError as exc:
        raise ArgumentValidationError(_bounded_validation_message(exc)) from None
    try:
        result = spec.handler(validated)
    except Exception as exc:
        if capture_failure:
            raise HandlerExecutionError(
                exc,
                _validated_secret(validated),
                _validated_public_context(validated),
            ) from None
        raise
    if not isinstance(result, dict):
        result = {"value": result}
    return result


def raw_result(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Validate once and return the unmodified domain handler result."""
    return _invoke_handler(name, arguments, capture_failure=False)


def _enriched_result(
    name: str,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    return _attach_meta(
        name,
        _invoke_handler(name, arguments, capture_failure=True),
    )


def dispatch_tool(name: str, arguments: dict[str, Any] | None) -> AnswerEnvelopeV1:
    """Validate once, invoke once, and always return the v1 answer envelope."""
    public_name = _bounded_tool_name(name)
    try:
        result = _enriched_result(name, arguments)
        envelope = build_answer_envelope(public_name, result)
        return envelope.model_copy(update={"answer_pack": result.get("answer_pack")})
    except (LookupError, ArgumentValidationError) as exc:
        return _error_envelope(public_name, str(exc))
    except HandlerExecutionError as exc:
        return _handler_failure_envelope(
            public_name,
            public_context=exc.public_context,
        )
    except Exception:
        return _handler_failure_envelope(public_name)


def legacy_result(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility result retaining domain fields for existing Python callers."""
    try:
        return _enriched_result(name, arguments)
    except LookupError:
        from kreports.mcp.catalog import TOOL_CATALOG

        return {
            "error": f"Unknown tool: {_bounded_tool_name(name)}",
            "available": list(TOOL_CATALOG),
        }
    except ArgumentValidationError as exc:
        return {"error": str(exc)}
    except HandlerExecutionError as exc:
        return _handler_failure_result(name, public_context=exc.public_context)
    except Exception:
        return _handler_failure_result(name)


def list_mcp_tools() -> list[Tool]:
    from kreports.mcp.catalog import TOOL_CATALOG

    return [spec.to_mcp_tool() for spec in TOOL_CATALOG.values()]
