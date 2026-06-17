"""
kreports.mcp._handlers — 9개 MCP 도구의 순수 핸들러 함수.

각 핸들러는:
- Pydantic 검증된 풀어쓴 시그니처 (FastMCP가 자동으로 inputSchema 생성)
- `kreports.analysis.api`에 위임
- 응답 dict에 `_attach_meta`로 표준 메타데이터 부착
- `KReportsToolResponse`로 감싸 structured content + outputSchema 지원

`TOOL_METADATA` 테이블이 도구 description / annotations / handler 단일 진실 원천이다.
이 테이블을 `server.py`(FastMCP 등록)와 `tools.py`(backward-compat ALL_TOOLS) 양쪽이 읽는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from mcp.types import ToolAnnotations
from pydantic import Field
from sqlalchemy import func

from kreports.analysis.api import (
    compare_to_industry as _api_compare_to_industry,
    detect_restatement as _api_detect_restatement,
    get_accounting_policy as _api_get_accounting_policy,
    get_audit_history as _api_get_audit_history,
    get_business_overview as _api_get_business_overview,
    get_financial_snapshot as _api_get_financial_snapshot,
    get_subsidiary_auditors as _api_get_subsidiary_auditors,
    resolve_corp_code,
    score_going_concern as _api_score_going_concern,
    search_company as _api_search_company,
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
from kreports.mcp.schemas import (
    COMPARE_METRICS,
    FsDiv,
    KReportsToolResponse,
)


_FS_DIVS = {"CFS", "OFS"}


# ---------------------------------------------------------------------------
# 메타데이터 부착
# ---------------------------------------------------------------------------

def _to_iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _extract_result_corp_code(result: dict) -> Optional[str]:
    corp_code = result.get("corp_code")
    if isinstance(corp_code, str) and corp_code:
        return corp_code
    subject = result.get("subject")
    if isinstance(subject, dict):
        corp_code = subject.get("corp_code")
        if isinstance(corp_code, str) and corp_code:
            return corp_code
    return None


def _company_meta(corp_code: str) -> Optional[dict]:
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
        freshness = {}
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


def _wrap(name: str, payload: Any) -> KReportsToolResponse:
    """도메인 결과를 `_attach_meta` 부착 후 표준 컨테이너로 감싼다."""
    enriched = _attach_meta(name, payload) if isinstance(payload, dict) else payload
    if not isinstance(enriched, dict):
        enriched = {"value": enriched, "_meta": {"tool": name}}
    return KReportsToolResponse.model_validate(enriched)


# ---------------------------------------------------------------------------
# 식별자 해석
# ---------------------------------------------------------------------------

def _format_candidates(candidates: list[dict]) -> str:
    labels = []
    for row in candidates[:5]:
        labels.append(
            f"{row.get('corp_name')}({row.get('stock_code') or '-'}, "
            f"{row.get('corp_code')})"
        )
    suffix = "" if len(candidates) <= 5 else f" 외 {len(candidates) - 5}건"
    return ", ".join(labels) + suffix


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

    hits = _api_search_company(raw, limit=10)
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
# 9개 핸들러 (FastMCP 시그니처)
# ---------------------------------------------------------------------------

def search_company(
    query: Annotated[
        str,
        Field(
            description="회사명 부분일치 또는 정확한 종목코드 (예: '삼성전자', '005930')",
            min_length=1,
        ),
    ],
    limit: Annotated[
        int,
        Field(description="반환 최대 개수 (기본 20)", ge=1, le=100),
    ] = 20,
) -> KReportsToolResponse:
    """
    회사명 또는 종목코드로 DART 등록 상장사를 검색한다.
    corp_code(8자리), 종목코드, 시장(KOSPI/KOSDAQ/KONEX)을 반환한다.
    다른 도구 호출 전 corp_code 확보용으로 먼저 사용한다.
    """
    results = _api_search_company(query, limit=limit)
    return _wrap("search_company", {"query": query, "count": len(results), "results": results})


def get_financial_snapshot(
    company: Annotated[
        str,
        Field(description="corp_code(8자리) / 종목코드(6자리) / 회사명", min_length=1),
    ],
    fs_div: Annotated[
        FsDiv,
        Field(description="CFS=연결재무제표(기본), OFS=별도재무제표"),
    ] = "CFS",
    years: Optional[Annotated[int, Field(description="최근 N개년", ge=1, le=20)]] = None,
) -> KReportsToolResponse:
    """
    연도별 핵심 재무지표 + 자본배분 지표 스냅샷. 단위: 억원.
    매출·영업이익·순이익·자산/부채/자본 + FCF, FCF마진, ROIC, CCC, DIO/DSO/DPO,
    이자보상배율 등 자본배분·운전자본 지표 포함.
    CFS(연결) 우선, 없으면 OFS(별도) 자동 폴백. 재무 트렌드/동종업종 비교 시 먼저 호출.
    """
    corp_code = _resolve_or_error(company)
    payload = _api_get_financial_snapshot(
        corp_code, fs_div=fs_div, years=years, annual_only=True,
    )
    return _wrap("get_financial_snapshot", payload)


def score_going_concern(
    company: Annotated[str, Field(description="corp_code / 종목코드 / 회사명", min_length=1)],
) -> KReportsToolResponse:
    """
    6인자 계속기업 위험 스코어카드. 100점 만점 감점방식.
    자본잠식(-30), 2년연속 영업손실(-20), 부채비율>200%(-15), 이자보상배율<1.0(-15),
    최근 영업CF<0(-10), 최근 비적정 감사의견(-10).
    등급: 안정(80+) / 주의(60-79) / 경고(40-59) / 위험(<40).
    부실위험 스크리닝, 거래처 신용평가, 상장폐지 모니터링에 사용.
    """
    corp_code = _resolve_or_error(company)
    return _wrap("score_going_concern", _api_score_going_concern(corp_code))


def detect_restatement(
    company: Annotated[str, Field(description="corp_code / 종목코드 / 회사명", min_length=1)],
    threshold_pct: Annotated[
        float,
        Field(description="변동률 임계값(%). 기본 1.0%", ge=0.1),
    ] = 1.0,
    top_n: Annotated[
        int,
        Field(description="반환 최대 항목 수 (절대변동률 내림차순)", ge=1, le=100),
    ] = 10,
) -> KReportsToolResponse:
    """
    사업보고서 간 소급 재작성(prior period error) 감지.
    N년 보고서 당기금액과 N+1년 보고서 전기금액을 계정별로 비교하여
    threshold_pct 이상 차이나는 항목을 반환.
    회계정책 변경·오류수정·연결범위 변경 식별에 사용. 감사 리스크 평가의 강력한 리드 지표.
    """
    corp_code = _resolve_or_error(company)
    payload = _api_detect_restatement(corp_code, threshold_pct=threshold_pct, top_n=top_n)
    return _wrap("detect_restatement", payload)


def get_accounting_policy(
    company: Annotated[str, Field(description="corp_code / 종목코드 / 회사명", min_length=1)],
    bsns_year: Annotated[
        int,
        Field(description="사업연도 (예: 2024는 2024 재무 → 2025년 3월 제출 사업보고서)", ge=2000, le=2100),
    ],
    fs_div: FsDiv = "CFS",
) -> KReportsToolResponse:
    """
    특정 연도 사업보고서 주석에서 회계정책 항목 추출.
    수익인식·재고자산·유형자산 감가상각·리스·금융상품·충당부채·법인세 등
    약 15개 표준 item_key별 heading + 본문을 반환.
    업종별 중요 회계처리 대비·정책 변경 추적에 사용.
    """
    corp_code = _resolve_or_error(company)
    payload = _api_get_accounting_policy(corp_code, bsns_year, fs_div=fs_div)
    if payload is None:
        payload = {
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "fs_div": fs_div,
            "items": {},
            "item_count": 0,
            "note": "해당 연도 사업보고서가 수집되지 않았거나 주석이 파싱되지 않음.",
        }
    return _wrap("get_accounting_policy", payload)


def get_audit_history(
    company: Annotated[str, Field(description="corp_code / 종목코드 / 회사명", min_length=1)],
) -> KReportsToolResponse:
    """
    연도별 감사인·감사의견·연속 감사연수 이력.
    CFS(연결)과 OFS(별도)을 구분하여 반환. 감사인 교체 여부, 비적정 의견 발생 여부,
    동일 감사인의 연속 감사연수(주기적 지정 감사제 추적)를 식별할 수 있다.
    감사 품질 평가·거버넌스 디스카운트 분석에 사용.
    """
    corp_code = _resolve_or_error(company)
    return _wrap("get_audit_history", _api_get_audit_history(corp_code))


def get_subsidiary_auditors(
    company: Annotated[str, Field(description="모회사 corp_code / 종목코드 / 회사명", min_length=1)],
    limit: Annotated[
        int,
        Field(description="반환 최대 종속회사 수. 기본 100. 전체 원하면 500+", ge=1, le=1000),
    ] = 100,
    only_with_auditor: Annotated[
        bool,
        Field(description="True면 감사인 정보가 있는 종속회사만 반환"),
    ] = False,
    slim: Annotated[
        bool,
        Field(
            description=(
                "True면 핵심 필드와 연결 총자산/총매출 대비 자산·매출 기여도만 반환. "
                "False면 사업 설명, 원천 자산 문자열 등 전체 필드."
            )
        ),
    ] = True,
) -> KReportsToolResponse:
    """
    최근 사업보고서 기준 종속·관계회사별 감사인 매트릭스.
    연결 총자산·총매출 대비 각 실체의 중요도와 감사인 이원화 여부를 한 번에 파악.
    대형 그룹은 400개 이상이므로 기본 slim=True + limit=100 + only_with_auditor=False.
    truncated=true면 total 참고 후 limit 증가시켜 재호출. 그룹 감사 리스크 평가에 사용.
    """
    corp_code = _resolve_or_error(company)
    payload = _api_get_subsidiary_auditors(
        corp_code, limit=limit, only_with_auditor=only_with_auditor, slim=slim,
    )
    return _wrap("get_subsidiary_auditors", payload)


def compare_to_industry(
    company: Annotated[
        Optional[str],
        Field(description="기준 회사 (corp_code / 종목코드 / 회사명). induty_code 자동 조회."),
    ] = None,
    induty_code: Annotated[
        Optional[str],
        Field(description="company 대신 직접 KSIC 코드 지정. company 있으면 무시."),
    ] = None,
    metric: COMPARE_METRICS = "영업이익률",
    year: Annotated[
        Optional[int],
        Field(description="사업연도 (Q4 기준). 생략 시 업종 내 최신 연도.", ge=2000, le=2100),
    ] = None,
    fs_div: FsDiv = "CFS",
    prefix_len: Annotated[
        int,
        Field(description="induty_code 매칭 자릿수. 2=대분류(권장), 3=중분류, 5=세분류", ge=1, le=5),
    ] = 2,
    include_peers: bool = True,
    peer_limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> KReportsToolResponse:
    """
    동종업종(KSIC induty_code prefix 매칭) 내 재무지표 분포와 특정 회사의 위치 비교.
    metric: 영업이익률, 순이익률, 부채비율, ROE, ROA, 자기자본비율, 매출성장률, Beneish_M.
    응답: P25/P50/P75 quantile + peers + subject(입력 회사) 값과 percentile.
    n<3이면 P25/P75는 null이며 희소성 note 포함.
    업종 내 상대 포지션, 경쟁사 우열, 밸류에이션 peer 선정에 사용.
    """
    resolved = _resolve_or_error(company) if company else None
    payload = _api_compare_to_industry(
        company=resolved,
        induty_code=induty_code,
        metric=metric,
        year=year,
        fs_div=fs_div,
        prefix_len=prefix_len,
        include_peers=include_peers,
        peer_limit=peer_limit,
    )
    return _wrap("compare_to_industry", payload)


def get_business_overview(
    company: Annotated[str, Field(description="corp_code / 종목코드 / 회사명", min_length=1)],
    bsns_year: Annotated[
        Optional[int],
        Field(description="사업연도. 생략 시 최신 사업보고서.", ge=2000, le=2100),
    ] = None,
) -> KReportsToolResponse:
    """
    사업보고서 핵심 경영 정보 텍스트 + 업종 특화 인사이트 반환.
    사업개요, 사업내용, 위험관리, 경영계획, R&D, 주요계약 6개 섹션 본문 텍스트 +
    KSIC 업종 분류 기반 rule-based 인사이트(R&D 비율, 위험 분포, 업종 키워드).
    감사 계획 수립(ISA 315 사업 이해), 투자 분석(비즈니스 모델 파악)에 활용.
    """
    corp_code = _resolve_or_error(company)
    return _wrap("get_business_overview", _api_get_business_overview(corp_code, bsns_year=bsns_year))


# ---------------------------------------------------------------------------
# 단일 진실 원천 — TOOL_METADATA
# ---------------------------------------------------------------------------

# 모든 9개 도구는 read-only DB 쿼리이므로 동일 annotation 적용.
_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


TOOL_METADATA: dict[str, dict[str, Any]] = {
    fn.__name__: {
        "handler": fn,
        "description": (fn.__doc__ or "").strip(),
        "annotations": _READ_ONLY,
    }
    for fn in [
        search_company,
        get_financial_snapshot,
        score_going_concern,
        detect_restatement,
        get_accounting_policy,
        get_audit_history,
        get_subsidiary_auditors,
        compare_to_industry,
        get_business_overview,
    ]
}
