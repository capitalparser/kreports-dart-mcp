"""
dart_mcp.tools — MCP 도구 정의.

각 도구는 세 부분으로 구성된다:
  1. schema — JSON Schema로 입력 스펙 정의 (Claude가 이해)
  2. handler — 실제 호출 함수 (dart_analyst에 위임)
  3. description — Claude가 도구 선택에 쓰는 자연어 설명

도구 목록 (12개):
  search_company                회사명/종목코드 → corp_code 탐색
  get_financial_snapshot        연도별 재무요약 + 자본배분 지표
  score_going_concern           6인자 계속기업 스코어카드
  detect_restatement            소급 재작성 감지
  get_accounting_policy         회계정책 주석 추출
  get_audit_history             감사인·의견 이력
  get_subsidiary_auditors       종속/관계회사 감사인 매트릭스
  compare_to_industry           업종 벤치마킹 (단일 지표·단일 연도)
  get_business_overview         사업보고서 핵심 섹션
  get_investor_signals          투자자 품질·리스크·공시 이벤트 요약
  compare_to_industry_multi     업종 벤치마킹 (다지표·다년도 matrix)
  get_industry_audit_landscape  업종 내 감사인 시장 분석
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from mcp.types import Tool
from sqlalchemy import func

from kreports.analysis.api import (
    compare_peer_audit_fees,
    compare_peer_risk_profile,
    compare_to_industry,
    compare_to_industry_multi,
    detect_restatement,
    get_accounting_policy,
    get_audit_history,
    get_business_overview,
    get_financial_snapshot,
    get_industry_audit_landscape,
    get_investor_signals,
    get_subsidiary_auditors,
    resolve_corp_code,
    select_peer_group,
    score_going_concern,
    search_company,
)
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


_FS_DIVS = {"CFS", "OFS"}
_FS_STRATEGIES = {"CFS", "OFS", "auto"}
_COMPARE_METRICS = [
    "영업이익률",
    "순이익률",
    "부채비율",
    "ROE",
    "ROA",
    "자기자본비율",
    "매출성장률",
    "Beneish_M",
]


# ---------------------------------------------------------------------------
# 입력 검증 / 응답 메타데이터
# ---------------------------------------------------------------------------

def _require_string(args: dict, name: str) -> str:
    value = args.get(name)
    if value is None:
        raise ValueError(f"'{name}' 값이 필요합니다.")
    value = str(value).strip()
    if not value:
        raise ValueError(f"'{name}' 값은 비어 있을 수 없습니다.")
    return value


def _optional_string(args: dict, name: str) -> str | None:
    if name not in args or args.get(name) is None:
        return None
    value = str(args.get(name)).strip()
    return value or None


def _optional_int(
    args: dict,
    name: str,
    default: int | None = None,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
    allow_none: bool = False,
) -> int | None:
    if name not in args:
        return default
    value = args.get(name)
    if value is None:
        if allow_none:
            return None
        return default
    if isinstance(value, bool):
        raise ValueError(f"'{name}' 값은 정수여야 합니다.")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"'{name}' 값은 정수여야 합니다.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{name}' 값은 정수여야 합니다.") from exc
    if min_value is not None and parsed < min_value:
        raise ValueError(f"'{name}' 값은 {min_value} 이상이어야 합니다.")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"'{name}' 값은 {max_value} 이하이어야 합니다.")
    return parsed


def _required_int(
    args: dict,
    name: str,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    if name not in args or args.get(name) is None:
        raise ValueError(f"'{name}' 값이 필요합니다.")
    parsed = _optional_int(
        args,
        name,
        min_value=min_value,
        max_value=max_value,
    )
    assert parsed is not None
    return parsed


def _optional_float(
    args: dict,
    name: str,
    default: float,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float:
    if name not in args or args.get(name) is None:
        return default
    value = args.get(name)
    if isinstance(value, bool):
        raise ValueError(f"'{name}' 값은 숫자여야 합니다.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{name}' 값은 숫자여야 합니다.") from exc
    if min_value is not None and parsed < min_value:
        raise ValueError(f"'{name}' 값은 {min_value} 이상이어야 합니다.")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"'{name}' 값은 {max_value} 이하이어야 합니다.")
    return parsed


def _optional_bool(args: dict, name: str, default: bool) -> bool:
    if name not in args or args.get(name) is None:
        return default
    value = args.get(name)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"'{name}' 값은 boolean이어야 합니다.")


def _optional_enum(
    args: dict,
    name: str,
    allowed: set[str] | list[str],
    default: str,
) -> str:
    value = args.get(name, default)
    if value is None:
        value = default
    value = str(value).strip()
    if value not in allowed:
        raise ValueError(f"'{name}' 값은 {', '.join(allowed)} 중 하나여야 합니다.")
    return value


def _format_candidates(candidates: list[dict]) -> str:
    labels = []
    for row in candidates[:5]:
        labels.append(
            f"{row.get('corp_name')}({row.get('stock_code') or '-'}, "
            f"{row.get('corp_code')})"
        )
    suffix = "" if len(candidates) <= 5 else f" 외 {len(candidates) - 5}건"
    return ", ".join(labels) + suffix


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _extract_result_corp_code(result: dict) -> str | None:
    corp_code = result.get("corp_code")
    if isinstance(corp_code, str) and corp_code:
        return corp_code
    subject = result.get("subject")
    if isinstance(subject, dict):
        corp_code = subject.get("corp_code")
        if isinstance(corp_code, str) and corp_code:
            return corp_code
    return None


def _company_meta(corp_code: str) -> dict | None:
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


def _data_freshness(corp_code: str) -> dict:
    table_map = {
        "financial": Financial,
        "financial_fact": FinancialFact,
        "disclosure": Disclosure,
        "auditor": Auditor,
        "audit_fee": AuditFee,
        "accounting_policy": AccountingPolicyItem,
    }
    with get_session() as session:
        return {
            key: _to_iso(
                session.query(func.max(model.fetched_at))
                .filter(model.corp_code == corp_code)
                .scalar()
            )
            for key, model in table_map.items()
        }


def _attach_meta(name: str, result: Any) -> Any:
    if not isinstance(result, dict):
        return result

    enriched = dict(result)
    meta = dict(enriched.get("_meta") or {})
    meta.update({
        "tool": name,
        "source": "local_kreports_db",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "MCP 응답은 로컬 kreports.db에 수집된 DART/OpenDART 기반 캐시입니다.",
            "중요 판단 전 data_freshness와 원 공시 접수번호를 확인하세요.",
        ],
    })

    corp_code = _extract_result_corp_code(enriched)
    if corp_code:
        try:
            meta["company"] = _company_meta(corp_code)
            meta["data_freshness"] = _data_freshness(corp_code)
        except Exception as exc:
            meta["meta_error"] = f"{type(exc).__name__}: {exc}"

    if enriched.get("parent_rcept_no"):
        meta["source_rcept_no"] = enriched.get("parent_rcept_no")
    if enriched.get("bsns_year") is not None:
        meta["bsns_year"] = enriched.get("bsns_year")
    if name == "search_company":
        meta["result_count"] = enriched.get("count", 0)

    enriched["_meta"] = meta
    return enriched


# ---------------------------------------------------------------------------
# 입력 헬퍼: corp_code / stock_code / 회사명 세 가지 모두 지원
# ---------------------------------------------------------------------------

def _resolve_or_error(identifier: str) -> str:
    """식별자를 corp_code로 변환. 회사명 다건 매칭은 명시적으로 거절한다."""
    raw = "" if identifier is None else str(identifier).strip()
    if not raw:
        raise ValueError("회사 식별자(company)가 필요합니다.")

    if raw.isdigit() and len(raw) in (6, 8):
        cc = resolve_corp_code(raw)
        if cc is not None:
            return cc
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


# ---------------------------------------------------------------------------
# 1. search_company
# ---------------------------------------------------------------------------

def _handle_search_company(args: dict) -> dict:
    query = _require_string(args, "query")
    limit = _optional_int(args, "limit", 20, min_value=1, max_value=100)
    results = search_company(query, limit=limit)
    return {
        "query": query,
        "count": len(results),
        "results": results,
    }


TOOL_SEARCH_COMPANY = Tool(
    name="search_company",
    description=(
        "회사명 또는 종목코드로 DART 등록 상장사를 검색한다. "
        "corp_code(8자리 DART 식별자), 종목코드, 시장(KOSPI/KOSDAQ/KONEX)을 반환한다. "
        "다른 도구를 호출하기 전에 corp_code를 확보할 때 먼저 사용한다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "회사명 부분일치 또는 정확한 종목코드 (예: '삼성전자', '005930')",
            },
            "limit": {
                "type": "integer",
                "description": "반환 최대 개수 (기본 20)",
                "default": 20,
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": ["query"],
    },
)


# ---------------------------------------------------------------------------
# 2. get_financial_snapshot
# ---------------------------------------------------------------------------

def _handle_get_financial_snapshot(args: dict) -> dict:
    corp_code = _resolve_or_error(_require_string(args, "company"))
    fs_div = _optional_enum(args, "fs_div", _FS_DIVS, "CFS")
    years = _optional_int(args, "years", None, min_value=1, max_value=20)
    return get_financial_snapshot(
        corp_code,
        fs_div=fs_div,
        years=years,
        annual_only=True,
    )


TOOL_GET_FINANCIAL_SNAPSHOT = Tool(
    name="get_financial_snapshot",
    description=(
        "연도별 핵심 재무지표와 자본배분 지표 스냅샷. 억원 단위. "
        "매출·영업이익·순이익·자산/부채/자본 + FCF, FCF마진, ROIC, CCC, DIO/DSO/DPO, "
        "이자보상배율 등 자본배분·운전자본 지표 포함. CFS(연결) 우선, 없으면 OFS(별도) 자동 폴백. "
        "재무 트렌드 분석·동종업종 비교 시 먼저 호출한다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "corp_code(8자리) / 종목코드(6자리) / 회사명 중 아무거나",
            },
            "fs_div": {
                "type": "string",
                "enum": ["CFS", "OFS"],
                "description": "CFS=연결재무제표 (기본), OFS=별도재무제표",
                "default": "CFS",
            },
            "years": {
                "type": "integer",
                "description": "최근 N개 연도만. 생략 시 전체 수집 연도 반환.",
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["company"],
    },
)


# ---------------------------------------------------------------------------
# 3. score_going_concern
# ---------------------------------------------------------------------------

def _handle_score_going_concern(args: dict) -> dict:
    corp_code = _resolve_or_error(_require_string(args, "company"))
    return score_going_concern(corp_code)


TOOL_SCORE_GOING_CONCERN = Tool(
    name="score_going_concern",
    description=(
        "6인자 계속기업 위험 스코어카드. 100점 만점 감점방식. "
        "자본잠식(-30), 2년연속 영업손실(-20), 부채비율>200%(-15), 이자보상배율<1.0(-15), "
        "최근 영업CF<0(-10), 최근 비적정 감사의견(-10). "
        "등급: 안정(80+) / 주의(60-79) / 경고(40-59) / 위험(<40). "
        "부실위험 스크리닝, 거래처 신용평가, 상장폐지 모니터링에 사용."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "corp_code / 종목코드 / 회사명",
            },
        },
        "required": ["company"],
    },
)


# ---------------------------------------------------------------------------
# 4. detect_restatement
# ---------------------------------------------------------------------------

def _handle_detect_restatement(args: dict) -> dict:
    corp_code = _resolve_or_error(_require_string(args, "company"))
    threshold = _optional_float(args, "threshold_pct", 1.0, min_value=0.1)
    top_n = _optional_int(args, "top_n", 10, min_value=1, max_value=100)
    return detect_restatement(
        corp_code, threshold_pct=threshold, top_n=top_n
    )


TOOL_DETECT_RESTATEMENT = Tool(
    name="detect_restatement",
    description=(
        "사업보고서 간 소급 재작성(prior period error) 감지. "
        "N년 사업보고서의 당기금액과 N+1년 사업보고서의 전기금액을 계정별로 비교하여 "
        "threshold_pct 이상 차이나는 항목을 반환. "
        "회계정책 변경·오류수정·연결범위 변경 식별에 사용. "
        "감사 리스크 평가 시 과거 재작성 이력은 강력한 리드 지표."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "corp_code / 종목코드 / 회사명",
            },
            "threshold_pct": {
                "type": "number",
                "description": "변동률 임계값 (%). 기본 1.0%",
                "default": 1.0,
                "minimum": 0.1,
            },
            "top_n": {
                "type": "integer",
                "description": "반환 최대 항목 수 (절대변동률 기준 내림차순)",
                "default": 10,
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": ["company"],
    },
)


# ---------------------------------------------------------------------------
# 5. get_accounting_policy
# ---------------------------------------------------------------------------

def _handle_get_accounting_policy(args: dict) -> dict | None:
    corp_code = _resolve_or_error(_require_string(args, "company"))
    bsns_year = _required_int(args, "bsns_year", min_value=2000, max_value=2100)
    fs_div = _optional_enum(args, "fs_div", _FS_DIVS, "CFS")
    result = get_accounting_policy(corp_code, bsns_year, fs_div=fs_div)
    if result is None:
        return {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "items": {},
            "item_count": 0,
            "note": "해당 연도 사업보고서가 수집되지 않았거나 주석이 파싱되지 않음.",
        }
    return result


TOOL_GET_ACCOUNTING_POLICY = Tool(
    name="get_accounting_policy",
    description=(
        "특정 연도 사업보고서 주석에서 회계정책 항목 추출. "
        "수익인식·재고자산·유형자산 감가상각·리스·금융상품·충당부채·법인세 등 "
        "약 15개 표준 item_key별로 heading과 본문을 반환한다. "
        "업종별 중요 회계처리 대비·정책 변경 추적에 사용. "
        "bsns_year는 연간 사업보고서가 제출된 연도(보통 전년도 재무).'"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "corp_code / 종목코드 / 회사명",
            },
            "bsns_year": {
                "type": "integer",
                "description": "사업연도 (예: 2024는 2024 재무 → 2025년 3월 제출 사업보고서)",
                "minimum": 2000,
                "maximum": 2100,
            },
            "fs_div": {
                "type": "string",
                "enum": ["CFS", "OFS"],
                "default": "CFS",
            },
        },
        "required": ["company", "bsns_year"],
    },
)


# ---------------------------------------------------------------------------
# 6. get_audit_history
# ---------------------------------------------------------------------------

def _handle_get_audit_history(args: dict) -> dict:
    corp_code = _resolve_or_error(_require_string(args, "company"))
    return get_audit_history(corp_code)


TOOL_GET_AUDIT_HISTORY = Tool(
    name="get_audit_history",
    description=(
        "연도별 감사인·감사의견·연속 감사연수 이력. "
        "CFS(연결)과 OFS(별도)을 구분하여 반환. 감사인 교체 여부, 비적정 의견 발생 여부, "
        "동일 감사인의 연속 감사연수(주기적 지정 감사제 추적)를 식별할 수 있다. "
        "감사 품질 평가·거버넌스 디스카운트 분석에 사용."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "corp_code / 종목코드 / 회사명",
            },
        },
        "required": ["company"],
    },
)


# ---------------------------------------------------------------------------
# 7. get_subsidiary_auditors
# ---------------------------------------------------------------------------

def _handle_get_subsidiary_auditors(args: dict) -> dict:
    corp_code = _resolve_or_error(_require_string(args, "company"))
    limit = _optional_int(
        args,
        "limit",
        100,
        min_value=1,
        max_value=1000,
        allow_none=True,
    )
    return get_subsidiary_auditors(
        corp_code,
        limit=limit,
        only_with_auditor=_optional_bool(args, "only_with_auditor", False),
        slim=_optional_bool(args, "slim", True),
    )


TOOL_GET_SUBSIDIARY_AUDITORS = Tool(
    name="get_subsidiary_auditors",
    description=(
        "최근 사업보고서 기준 종속·관계회사별 감사인 매트릭스. "
        "연결그룹 내 감사인 이원화 여부, 주요 종속회사 감사 품질, 해외 자회사 감사인 정보를 "
        "한 번에 파악할 수 있다. 대형 그룹(삼성전자 등)은 400개 이상이므로 "
        "기본값은 감사인 있는 항목 우선 + 상위 100개 + 핵심 필드만 반환한다. "
        "truncated=true면 total 참고 후 limit 증가시켜 재호출. "
        "그룹 감사 리스크 평가·PCAOB 이슈 추적에 사용."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "모회사의 corp_code / 종목코드 / 회사명",
            },
            "limit": {
                "type": "integer",
                "description": "반환 최대 종속회사 수. 기본 100. 전체를 원하면 500+ 설정.",
                "default": 100,
                "minimum": 1,
                "maximum": 1000,
            },
            "only_with_auditor": {
                "type": "boolean",
                "description": "True면 감사인 정보가 있는 종속회사만 반환.",
                "default": False,
            },
            "slim": {
                "type": "boolean",
                "description": "True면 핵심 8개 필드만 반환 (name, relation, ownership_pct, "
                               "listed_yn, corp_code, stock_code, market, auditor). "
                               "False면 business 설명·assets 등 전체 필드.",
                "default": True,
            },
        },
        "required": ["company"],
    },
)


# ---------------------------------------------------------------------------
# 8. compare_to_industry
# ---------------------------------------------------------------------------

def _handle_compare_to_industry(args: dict) -> dict:
    company = _optional_string(args, "company")
    if company:
        company = _resolve_or_error(company)
    result = compare_to_industry(
        company=company,
        induty_code=_optional_string(args, "induty_code"),
        metric=_optional_enum(args, "metric", _COMPARE_METRICS, "영업이익률"),
        year=_optional_int(args, "year", None, min_value=2000, max_value=2100),
        fs_div=_optional_enum(args, "fs_div", _FS_DIVS, "CFS"),
        prefix_len=_optional_int(args, "prefix_len", 2, min_value=1, max_value=5),
        include_peers=_optional_bool(args, "include_peers", True),
        peer_limit=_optional_int(args, "peer_limit", 50, min_value=1, max_value=500),
    )
    return result


TOOL_COMPARE_TO_INDUSTRY = Tool(
    name="compare_to_industry",
    description=(
        "동종업종(KSIC induty_code prefix 매칭) 내 재무지표 분포와 특정 회사의 위치 비교. "
        "지원 metric: 영업이익률, 순이익률, 부채비율, ROE, ROA, 자기자본비율, 매출성장률, Beneish_M. "
        "기본 prefix_len=2 (KSIC 대분류). 응답: P25/P50/P75 quantile + peers 리스트 + "
        "subject(입력 회사) 값과 percentile. "
        "n<3이면 P25/P75는 null이며 희소성 note를 포함한다. "
        "업종 내 상대 포지션, 경쟁사 대비 우열, 밸류에이션 peer 선정에 사용."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "기준 회사 (corp_code / 종목코드 / 회사명). "
                               "induty_code를 자동 조회하고, 응답에 subject 필드로 포함.",
            },
            "induty_code": {
                "type": "string",
                "description": "company 대신 직접 KSIC 코드를 지정. "
                               "company가 있으면 무시.",
            },
            "metric": {
                "type": "string",
                "enum": _COMPARE_METRICS,
                "default": "영업이익률",
            },
            "year": {
                "type": "integer",
                "description": "사업연도 (Q4 기준). 생략 시 해당 업종 내 최신 연도.",
                "minimum": 2000,
                "maximum": 2100,
            },
            "fs_div": {
                "type": "string",
                "enum": ["CFS", "OFS"],
                "default": "CFS",
            },
            "prefix_len": {
                "type": "integer",
                "description": "induty_code 앞 몇 자리로 매칭할지. "
                               "2=KSIC 대분류(권장), 3=중분류, 5=세분류(정확).",
                "default": 2,
                "minimum": 1,
                "maximum": 5,
            },
            "include_peers": {
                "type": "boolean",
                "default": True,
            },
            "peer_limit": {
                "type": "integer",
                "default": 50,
                "minimum": 1,
                "maximum": 500,
            },
        },
    },
)


# ---------------------------------------------------------------------------
# 9. get_business_overview
# ---------------------------------------------------------------------------

def _handle_get_business_overview(args: dict) -> dict:
    corp_code = _resolve_or_error(_require_string(args, "company"))
    bsns_year = _optional_int(args, "bsns_year", None, min_value=2000, max_value=2100)
    return get_business_overview(corp_code, bsns_year=bsns_year)


TOOL_GET_BUSINESS_OVERVIEW = Tool(
    name="get_business_overview",
    description=(
        "사업보고서 핵심 경영 정보 텍스트 + 업종 특화 인사이트 반환. "
        "사업개요, 사업내용, 위험관리, 경영계획, R&D, 주요계약 6개 섹션의 본문 텍스트와 "
        "KSIC 업종 분류 기반 rule-based 인사이트(R&D 비율, 위험 분포, 업종 키워드)를 포함. "
        "Claude가 이 텍스트를 받아 업종 맥락에 맞는 심층 분석을 수행할 수 있다. "
        "감사 계획 수립(ISA 315 사업 이해), 투자 분석(비즈니스 모델 파악)에 활용."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "corp_code / 종목코드 / 회사명",
            },
            "bsns_year": {
                "type": "integer",
                "description": "사업연도. 생략 시 최신 사업보고서.",
                "minimum": 2000,
                "maximum": 2100,
            },
        },
        "required": ["company"],
    },
)


# ---------------------------------------------------------------------------
# 10. get_investor_signals
# ---------------------------------------------------------------------------

def _handle_get_investor_signals(args: dict) -> dict:
    corp_code = _resolve_or_error(_require_string(args, "company"))
    years = _optional_int(args, "years", 5, min_value=1, max_value=10)
    window_days = _optional_int(args, "window_days", 365, min_value=1, max_value=3650)
    event_limit = _optional_int(args, "event_limit", 20, min_value=1, max_value=100)
    return get_investor_signals(
        corp_code,
        years=years or 5,
        window_days=window_days or 365,
        event_limit=event_limit or 20,
    )


TOOL_GET_INVESTOR_SIGNALS = Tool(
    name="get_investor_signals",
    description=(
        "투자자 관점의 품질·회계리스크·최근 공시 이벤트 요약. "
        "버핏식 퀄리티 체크(ROE, 영업이익률, 매출성장, 부채비율, FCF, CFO/NI), "
        "회계/거버넌스 리스크 점수(Beneish, 정정공시, 감사의견, 감사인 교체, NAS 등), "
        "최근 자기주식·유상증자·CB/BW/EB·합병/분할·대규모 계약·소송 공시 제목 신호를 묶어 반환한다. "
        "보유종목 정기 점검, 신규 종목 1차 스크리닝, 리스크 플래그 확인에 사용."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "corp_code / 종목코드 / 회사명",
            },
            "years": {
                "type": "integer",
                "description": "퀄리티 체크에 사용할 최근 N개 연도. 기본 5.",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
            "window_days": {
                "type": "integer",
                "description": "최근 공시 이벤트 검색 기간. 기본 365일.",
                "default": 365,
                "minimum": 1,
                "maximum": 3650,
            },
            "event_limit": {
                "type": "integer",
                "description": "반환할 최근 이벤트 최대 개수. 기본 20.",
                "default": 20,
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": ["company"],
    },
)


# ---------------------------------------------------------------------------
# 11. compare_to_industry_multi
# ---------------------------------------------------------------------------

_ALL_MULTI_METRICS = [
    "영업이익률",
    "순이익률",
    "부채비율",
    "ROE",
    "ROA",
    "자기자본비율",
    "매출성장률",
    "Beneish_M",
]


def _optional_float_or_none(
    args: dict,
    name: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float | None:
    """size_bucket_decade처럼 default가 None인 optional float용 헬퍼."""
    if name not in args or args.get(name) is None:
        return None
    value = args.get(name)
    if isinstance(value, bool):
        raise ValueError(f"'{name}' 값은 숫자여야 합니다.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"'{name}' 값은 숫자여야 합니다.") from exc
    if min_value is not None and parsed < min_value:
        raise ValueError(f"'{name}' 값은 {min_value} 이상이어야 합니다.")
    if max_value is not None and parsed > max_value:
        raise ValueError(f"'{name}' 값은 {max_value} 이하이어야 합니다.")
    return parsed


def _handle_compare_to_industry_multi(args: dict) -> dict:
    company = _resolve_or_error(_require_string(args, "company"))
    metrics_raw = args.get("metrics")
    if metrics_raw is None:
        metrics: list[str] | None = None
    elif not isinstance(metrics_raw, list):
        return {"error": "metrics는 array여야 합니다."}
    else:
        invalid = [m for m in metrics_raw if m not in _ALL_MULTI_METRICS]
        if invalid:
            return {
                "error": f"지원하지 않는 metric: {invalid}. 지원: {_ALL_MULTI_METRICS}"
            }
        metrics = list(metrics_raw)
    years_back = _optional_int(args, "years_back", 5, min_value=1, max_value=10) or 5
    prefix_len_start = (
        _optional_int(args, "prefix_len_start", 3, min_value=2, max_value=5) or 3
    )
    return compare_to_industry_multi(
        company=company,
        metrics=metrics,
        years_back=years_back,
        fs_div=_optional_enum(args, "fs_div", _FS_DIVS, "CFS"),
        fs_strategy=_optional_enum(args, "fs_strategy", _FS_STRATEGIES, "auto"),
        prefix_len_start=prefix_len_start,
        exclude_other_sectors=_optional_bool(args, "exclude_other_sectors", True),
        size_bucket_decade=_optional_float_or_none(
            args, "size_bucket_decade", min_value=0.5, max_value=3.0
        ),
    )


TOOL_COMPARE_TO_INDUSTRY_MULTI = Tool(
    name="compare_to_industry_multi",
    description=(
        "동종업종 내 다지표·다년도(기본 5년) 분포와 subject 회사 percentile을 한 번에 반환. "
        "peer 매칭은 adaptive ladder(p3→p2) + sector mutual exclusion(금융/지주/부동산/일반) + "
        "옵션 size bucket(자산 log10 ±decade). 응답은 {year: {metric: {p25, p50, p75, n, "
        "subject_value, percentile, unit}}} 형태의 matrix와 meta(matched_prefix_len, "
        "confidence, sector_group 등)를 포함. 단일 회사의 업종 내 상대 포지션을 다지표·다년도로 "
        "한 번에 파악할 때 사용."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "기준 회사 (corp_code / 종목코드 / 회사명).",
            },
            "metrics": {
                "type": "array",
                "items": {"type": "string", "enum": _ALL_MULTI_METRICS},
                "description": "비교 지표 리스트. 생략 시 8개 전체.",
            },
            "years_back": {
                "type": "integer",
                "description": "최근 N개 연도. 기본 5.",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
            "fs_div": {
                "type": "string",
                "enum": ["CFS", "OFS"],
                "default": "CFS",
            },
            "fs_strategy": {
                "type": "string",
                "enum": ["CFS", "OFS", "auto"],
                "default": "auto",
                "description": "auto면 CFS 우선, 없으면 OFS로 비교한다.",
            },
            "prefix_len_start": {
                "type": "integer",
                "description": "KSIC ladder 시작 자리 수. 기본 3 (소분류). n<5면 2자리로 자동 fallback.",
                "default": 3,
                "minimum": 2,
                "maximum": 5,
            },
            "exclude_other_sectors": {
                "type": "boolean",
                "description": "True면 금융/지주/부동산/일반 sector group 간 분리. 기본 True.",
                "default": True,
            },
            "size_bucket_decade": {
                "type": "number",
                "description": "자산 log10 ±decade 필터 (예: 1.0=±10배). 생략 시 미적용.",
                "minimum": 0.5,
                "maximum": 3.0,
            },
        },
        "required": ["company"],
    },
)


def _handle_select_peer_group(args: dict) -> dict:
    company = _resolve_or_error(_require_string(args, "company"))
    criteria = args.get("criteria")
    if criteria is not None and not isinstance(criteria, list):
        return {"error": "criteria는 array여야 합니다."}
    return select_peer_group(
        company=company,
        criteria=criteria,
        peer_limit=_optional_int(args, "peer_limit", 30, min_value=1, max_value=200) or 30,
        fs_strategy=_optional_enum(args, "fs_strategy", _FS_STRATEGIES, "auto"),
        prefix_len_start=_optional_int(args, "prefix_len_start", 3, min_value=2, max_value=5) or 3,
        size_bucket_decade=_optional_float_or_none(args, "size_bucket_decade", min_value=0.5, max_value=3.0),
        exclude_other_sectors=_optional_bool(args, "exclude_other_sectors", True),
    )


TOOL_SELECT_PEER_GROUP = Tool(
    name="select_peer_group",
    description=(
        "감사인 관점 peer group 선정 근거팩. KSIC 업종, sector 분리, 자산규모 bucket, "
        "재무데이터/감사보수 coverage를 기준으로 peer 목록과 include_reasons를 반환한다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {"type": "string"},
            "criteria": {"type": "array", "items": {"type": "string"}},
            "peer_limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
            "fs_strategy": {"type": "string", "enum": ["CFS", "OFS", "auto"], "default": "auto"},
            "prefix_len_start": {"type": "integer", "default": 3, "minimum": 2, "maximum": 5},
            "size_bucket_decade": {"type": "number", "minimum": 0.5, "maximum": 3.0},
            "exclude_other_sectors": {"type": "boolean", "default": True},
        },
        "required": ["company"],
    },
)


def _handle_compare_peer_audit_fees(args: dict) -> dict:
    company = _resolve_or_error(_require_string(args, "company"))
    return compare_peer_audit_fees(
        company=company,
        year=_optional_int(args, "year", 2025, min_value=2000, max_value=2100) or 2025,
        peer_limit=_optional_int(args, "peer_limit", 30, min_value=1, max_value=200) or 30,
        fs_strategy=_optional_enum(args, "fs_strategy", _FS_STRATEGIES, "auto"),
        size_bucket_decade=_optional_float_or_none(args, "size_bucket_decade", min_value=0.5, max_value=3.0),
    )


TOOL_COMPARE_PEER_AUDIT_FEES = Tool(
    name="compare_peer_audit_fees",
    description=(
        "감사보수와 감사시간을 peer group 기준으로 벤치마크한다. 감사보수, 감사시간, "
        "비감사보수 비율, 자산 대비 보수, 시간당 보수의 분위수와 subject percentile을 반환한다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {"type": "string"},
            "year": {"type": "integer", "default": 2025, "minimum": 2000, "maximum": 2100},
            "peer_limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
            "fs_strategy": {"type": "string", "enum": ["CFS", "OFS", "auto"], "default": "auto"},
            "size_bucket_decade": {"type": "number", "minimum": 0.5, "maximum": 3.0},
        },
        "required": ["company"],
    },
)


def _handle_compare_peer_risk_profile(args: dict) -> dict:
    company = _resolve_or_error(_require_string(args, "company"))
    return compare_peer_risk_profile(
        company=company,
        year=_optional_int(args, "year", 2025, min_value=2000, max_value=2100) or 2025,
        peer_limit=_optional_int(args, "peer_limit", 30, min_value=1, max_value=200) or 30,
        fs_strategy=_optional_enum(args, "fs_strategy", _FS_STRATEGIES, "auto"),
    )


TOOL_COMPARE_PEER_RISK_PROFILE = Tool(
    name="compare_peer_risk_profile",
    description=(
        "감사인 관점 재무 위험 신호팩. peer group 기준 현금흐름/발생액/Beneish 신호와 "
        "정정·주요사항 공시 카운트를 반환하며 감사 리스크 판단 자체는 수행하지 않는다."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {"type": "string"},
            "year": {"type": "integer", "default": 2025, "minimum": 2000, "maximum": 2100},
            "peer_limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
            "fs_strategy": {"type": "string", "enum": ["CFS", "OFS", "auto"], "default": "auto"},
        },
        "required": ["company"],
    },
)


# ---------------------------------------------------------------------------
# 12. get_industry_audit_landscape
# ---------------------------------------------------------------------------

def _handle_get_industry_audit_landscape(args: dict) -> dict:
    company = _optional_string(args, "company")
    induty_code = _optional_string(args, "induty_code")
    if not company and not induty_code:
        return {"error": "company 또는 induty_code 중 하나 필요"}
    if company:
        company = _resolve_or_error(company)
    years_back = _optional_int(args, "years_back", 5, min_value=1, max_value=10) or 5
    prefix_len_start = (
        _optional_int(args, "prefix_len_start", 3, min_value=2, max_value=5) or 3
    )
    top_n = _optional_int(args, "top_n", 10, min_value=1, max_value=50) or 10
    return get_industry_audit_landscape(
        company=company,
        induty_code=induty_code,
        years_back=years_back,
        fs_div=_optional_enum(args, "fs_div", _FS_DIVS, "CFS"),
        prefix_len_start=prefix_len_start,
        top_n=top_n,
        exclude_other_sectors=_optional_bool(args, "exclude_other_sectors", True),
    )


TOOL_GET_INDUSTRY_AUDIT_LANDSCAPE = Tool(
    name="get_industry_audit_landscape",
    description=(
        "업종 내 감사 시장 분석: 감사인 시장점유율(회사수·자산가중 top_n), Big4 점유율, "
        "비적정 의견 발생율(years_back 누적), 평균 tenure(consecutive_years), "
        "subject 회사의 감사인 정보. peer는 adaptive ladder + sector 분리 적용. "
        "감사 리스크 평가, 동종업종 감사 시장 이해, 거버넌스 디스카운트 분석에 사용. "
        "auditors 데이터가 부족하면 latest_year=None과 함께 graceful note."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "company": {
                "type": "string",
                "description": "기준 회사 (corp_code / 종목코드 / 회사명).",
            },
            "induty_code": {
                "type": "string",
                "description": "company 대신 직접 KSIC 코드를 지정. company가 있으면 무시.",
            },
            "years_back": {
                "type": "integer",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
            "fs_div": {
                "type": "string",
                "enum": ["CFS", "OFS"],
                "default": "CFS",
            },
            "prefix_len_start": {
                "type": "integer",
                "default": 3,
                "minimum": 2,
                "maximum": 5,
            },
            "top_n": {
                "type": "integer",
                "description": "auditor_market_share 최대 항목 수.",
                "default": 10,
                "minimum": 1,
                "maximum": 50,
            },
            "exclude_other_sectors": {
                "type": "boolean",
                "default": True,
            },
        },
        "oneOf": [
            {"required": ["company"]},
            {"required": ["induty_code"]},
        ],
    },
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_TOOLS: list[Tool] = [
    TOOL_SEARCH_COMPANY,
    TOOL_GET_FINANCIAL_SNAPSHOT,
    TOOL_SCORE_GOING_CONCERN,
    TOOL_DETECT_RESTATEMENT,
    TOOL_GET_ACCOUNTING_POLICY,
    TOOL_GET_AUDIT_HISTORY,
    TOOL_GET_SUBSIDIARY_AUDITORS,
    TOOL_COMPARE_TO_INDUSTRY,
    TOOL_GET_BUSINESS_OVERVIEW,
    TOOL_GET_INVESTOR_SIGNALS,
    TOOL_SELECT_PEER_GROUP,
    TOOL_COMPARE_TO_INDUSTRY_MULTI,
    TOOL_COMPARE_PEER_AUDIT_FEES,
    TOOL_COMPARE_PEER_RISK_PROFILE,
    TOOL_GET_INDUSTRY_AUDIT_LANDSCAPE,
]

HANDLERS: dict[str, Callable[[dict], Any]] = {
    "search_company": _handle_search_company,
    "get_financial_snapshot": _handle_get_financial_snapshot,
    "score_going_concern": _handle_score_going_concern,
    "detect_restatement": _handle_detect_restatement,
    "get_accounting_policy": _handle_get_accounting_policy,
    "get_audit_history": _handle_get_audit_history,
    "get_subsidiary_auditors": _handle_get_subsidiary_auditors,
    "compare_to_industry": _handle_compare_to_industry,
    "get_business_overview": _handle_get_business_overview,
    "get_investor_signals": _handle_get_investor_signals,
    "select_peer_group": _handle_select_peer_group,
    "compare_to_industry_multi": _handle_compare_to_industry_multi,
    "compare_peer_audit_fees": _handle_compare_peer_audit_fees,
    "compare_peer_risk_profile": _handle_compare_peer_risk_profile,
    "get_industry_audit_landscape": _handle_get_industry_audit_landscape,
}


def call_tool(name: str, arguments: dict) -> str:
    """
    도구 이름과 arguments를 받아 handler 실행, JSON 문자열로 반환.
    ValueError → 사용자 오류 메시지로 포장.
    """
    handler = HANDLERS.get(name)
    if handler is None:
        return json.dumps(
            {"error": f"Unknown tool: {name}", "available": list(HANDLERS.keys())},
            ensure_ascii=True,
        )
    try:
        result = handler(arguments or {})
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"error": f"Internal error: {type(e).__name__}: {e}"},
            ensure_ascii=False,
        )
    result = _attach_meta(name, result)
    return json.dumps(result, ensure_ascii=False, default=str)
