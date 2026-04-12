"""
dart_mcp.tools — MCP 도구 정의.

각 도구는 세 부분으로 구성된다:
  1. schema — JSON Schema로 입력 스펙 정의 (Claude가 이해)
  2. handler — 실제 호출 함수 (dart_analyst에 위임)
  3. description — Claude가 도구 선택에 쓰는 자연어 설명

도구 목록 (7개):
  search_company           회사명/종목코드 → corp_code 탐색
  get_financial_snapshot   연도별 재무요약 + 자본배분 지표
  score_going_concern      6인자 계속기업 스코어카드
  detect_restatement       소급 재작성 감지
  get_accounting_policy    회계정책 주석 추출
  get_audit_history        감사인·의견 이력
  get_subsidiary_auditors  종속/관계회사 감사인 매트릭스
"""
from __future__ import annotations

import json
from typing import Any, Callable

from mcp.types import Tool

import kreports


# ---------------------------------------------------------------------------
# 입력 헬퍼: corp_code / stock_code / 회사명 세 가지 모두 지원
# ---------------------------------------------------------------------------

def _resolve_or_error(identifier: str) -> str:
    """식별자를 corp_code로 변환. 실패 시 ValueError."""
    cc = kreports.resolve_corp_code(identifier)
    if cc is None:
        raise ValueError(
            f"'{identifier}'에 해당하는 기업을 찾을 수 없습니다. "
            "corp_code(8자리), 종목코드(6자리) 또는 정확한 회사명을 입력하세요."
        )
    return cc


# ---------------------------------------------------------------------------
# 1. search_company
# ---------------------------------------------------------------------------

def _handle_search_company(args: dict) -> dict:
    query = args.get("query", "")
    limit = int(args.get("limit", 20))
    results = kreports.search_company(query, limit=limit)
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
    corp_code = _resolve_or_error(args["company"])
    fs_div = args.get("fs_div", "CFS")
    years = args.get("years")
    return kreports.get_financial_snapshot(
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
    corp_code = _resolve_or_error(args["company"])
    return kreports.score_going_concern(corp_code)


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
    corp_code = _resolve_or_error(args["company"])
    threshold = float(args.get("threshold_pct", 1.0))
    top_n = int(args.get("top_n", 10))
    return kreports.detect_restatement(
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
    corp_code = _resolve_or_error(args["company"])
    bsns_year = int(args["bsns_year"])
    fs_div = args.get("fs_div", "CFS")
    result = kreports.get_accounting_policy(corp_code, bsns_year, fs_div=fs_div)
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
    corp_code = _resolve_or_error(args["company"])
    return kreports.get_audit_history(corp_code)


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
    corp_code = _resolve_or_error(args["company"])
    limit = args.get("limit", 100)
    if limit is not None:
        limit = int(limit)
    return kreports.get_subsidiary_auditors(
        corp_code,
        limit=limit,
        only_with_auditor=bool(args.get("only_with_auditor", False)),
        slim=bool(args.get("slim", True)),
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
    company_arg = args.get("company")
    induty_code_arg = args.get("induty_code")

    # 기업 기준 호출: company → get_company → induty_code
    if company_arg:
        cc = _resolve_or_error(company_arg)
        # get_company는 stock_code를 요구 → Company 테이블 직접 조회
        from kreports.db.engine import get_session
        from kreports.db.models import Company

        with get_session() as session:
            row = session.query(Company).filter_by(corp_code=cc).first()
            if row is None:
                raise ValueError(f"corp_code '{cc}'를 찾을 수 없습니다.")
            if not row.induty_code:
                raise ValueError(
                    f"'{row.corp_name}'에 induty_code가 없습니다. "
                    "dart enrich-market으로 업종코드를 보완하세요."
                )
            resolved_induty = row.induty_code
            subject_name = row.corp_name
            subject_corp_code = cc
    elif induty_code_arg:
        resolved_induty = str(induty_code_arg).strip()
        subject_name = None
        subject_corp_code = None
    else:
        raise ValueError("company 또는 induty_code 중 하나를 제공해야 합니다.")

    result = kreports.get_industry_aggregates(
        induty_code=resolved_induty,
        metric=args.get("metric", "영업이익률"),
        year=args.get("year"),
        fs_div=args.get("fs_div", "CFS"),
        prefix_len=int(args.get("prefix_len", 2)),
        include_peers=bool(args.get("include_peers", True)),
        peer_limit=int(args.get("peer_limit", 50)),
    )

    # 기업 기준 호출 시 subject 표시 + 자신의 값과 percentile 계산
    if subject_corp_code and "peers" in result:
        subject = next(
            (p for p in result["peers"] if p["corp_code"] == subject_corp_code),
            None,
        )
        result["subject"] = {
            "corp_code": subject_corp_code,
            "corp_name": subject_name,
            "value": subject["value"] if subject else None,
            "found_in_peers": subject is not None,
        }
        # 백분위: subject 값이 해당 업종 내 몇 번째인지
        if subject and result.get("n", 0) > 0:
            all_values = sorted(
                [p["value"] for p in result["peers"] if p["value"] is not None]
            )
            rank = sum(1 for v in all_values if v < subject["value"])
            result["subject"]["percentile"] = round(
                100.0 * rank / max(len(all_values) - 1, 1), 1
            ) if len(all_values) > 1 else None

    return result


TOOL_COMPARE_TO_INDUSTRY = Tool(
    name="compare_to_industry",
    description=(
        "동종업종(KSIC induty_code prefix 매칭) 내 재무지표 분포와 특정 회사의 위치 비교. "
        "지원 metric: 영업이익률, 순이익률, 부채비율, ROE, ROA. "
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
                "enum": ["영업이익률", "순이익률", "부채비율", "ROE", "ROA"],
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
    corp_code = _resolve_or_error(args["company"])
    bsns_year = args.get("bsns_year")
    if bsns_year is not None:
        bsns_year = int(bsns_year)
    return kreports.get_business_overview(corp_code, bsns_year=bsns_year)


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
            ensure_ascii=False,
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
    return json.dumps(result, ensure_ascii=False, default=str)
