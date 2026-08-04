"""Reader-safe labels for the structured release readiness contract."""
from __future__ import annotations

from kreports.quality.release_gate import describe_release_blockers


_BLOCKER_LABELS = {
    "investor_core_3y_coverage": "3년 핵심 투자 데이터 커버리지 부족",
    "investor_core_coverage": "핵심 투자 데이터 커버리지 부족",
    "release_manifest_unavailable": "배포 매니페스트 미확인",
    "schema_migration_contract_mismatch": "데이터베이스 스키마 계약 불일치",
    "unexpected_tool_count": "공개 도구 목록 불일치",
    "runtime_not_readonly": "읽기 전용 실행 환경 미확인",
}
_BLOCKER_ACTIONS_KO = {
    "backfill and validate three-year investor-core coverage before release": (
        "배포 전에 3년 핵심 투자 분석용 기업·연도 데이터의 수집과 검증이 필요합니다."
    ),
    "backfill and validate investor-core company-year coverage before release": (
        "배포 전에 핵심 투자 분석용 기업·연도 데이터의 수집과 검증이 필요합니다."
    ),
    "write a validated dataset manifest from the prepared runtime DB": (
        "준비된 실행 DB에서 검증된 데이터셋 매니페스트를 생성해야 합니다."
    ),
    "migrate the release DB to the approved schema revision before release": (
        "배포 전에 승인된 스키마 개정으로 데이터베이스를 이전해야 합니다."
    ),
    "restore the approved 34-tool catalog before release": (
        "배포 전에 승인된 34개 도구 목록을 복원해야 합니다."
    ),
    "run release verification with KREPORTS_RUNTIME_MODE=readonly": (
        "읽기 전용 실행 환경에서 배포 검증을 다시 수행해야 합니다."
    ),
    "create the required index in a prepared release DB before release": (
        "준비된 배포 DB에 필수 인덱스를 생성한 뒤 다시 검증해야 합니다."
    ),
}
_DEGRADED_FEATURE_LABELS = {
    "investor_timeseries_5y": "5년 투자자 재무 시계열 제공 범위가 제한됩니다.",
    "accounting_policy": "회계정책 분석 범위가 제한됩니다.",
    "audit_procedure": "감사절차 분석 범위가 제한됩니다.",
    "materiality_benchmark": "중요성 기준 분석 범위가 제한됩니다.",
    "auditor_feature_readiness": "감사 기능의 준비 상태를 확인할 수 없습니다.",
    "audit_report_sections": "감사보고서 항목의 제공 범위가 제한됩니다.",
}
_UNKNOWN_BLOCKER = "배포 준비를 확인할 수 없는 항목이 있습니다. 담당자가 배포 점검 결과를 검토해야 합니다."
_UNKNOWN_DEGRADED_FEATURE = "기능 제공 범위를 확인할 수 없는 항목이 있습니다. 결과 해석 시 해당 기능의 가용 범위를 별도로 확인하세요."


def public_release_ready_label(value: bool) -> str:
    return "준비됨" if value else "준비되지 않음"


def public_manifest_available_label(value: bool) -> str:
    return "확인됨" if value else "확인되지 않음"


def public_release_blocker_text(value: object) -> str:
    """Describe known release blockers from gate guidance without exposing codes."""
    code = str(value)
    label = _BLOCKER_LABELS.get(code)
    if code.startswith("missing_required_index:"):
        label = "필수 데이터베이스 인덱스 누락"
    if label is None:
        return _UNKNOWN_BLOCKER

    guidance = describe_release_blockers([code])
    action = guidance[0]["action"] if guidance else ""
    explanation = _BLOCKER_ACTIONS_KO.get(action)
    if explanation is None:
        return _UNKNOWN_BLOCKER
    return f"{label}: {explanation}"


def public_degraded_feature_text(value: object) -> str:
    """Describe feature limitations without exposing an internal feature code."""
    return _DEGRADED_FEATURE_LABELS.get(
        str(value),
        _UNKNOWN_DEGRADED_FEATURE,
    )
