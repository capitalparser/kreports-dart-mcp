"""
kreports.mcp.schemas — MCP 도구 입출력 모델.

각 도구의 입력은 Pydantic v2 BaseModel로 정의한다. JSON Schema는
`model_json_schema()`로 자동 생성되어 MCP 클라이언트의 inputSchema가 된다.

출력은 도메인별로 자유로운 dict 구조이므로 공통 컨테이너 `KReportsToolResponse`
(extra="allow", `_meta` alias 지원)로 감싸 structured content를 표준화한다.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# 도구 전반에서 쓰는 enum
FsDiv = Literal["CFS", "OFS"]
COMPARE_METRICS = Literal[
    "영업이익률",
    "순이익률",
    "부채비율",
    "ROE",
    "ROA",
    "자기자본비율",
    "매출성장률",
    "Beneish_M",
]

CompanyIdent = Annotated[
    str,
    Field(
        description="corp_code(8자리) / 종목코드(6자리) / 정확한 회사명 중 하나",
        min_length=1,
    ),
]
BsnsYear = Annotated[int, Field(ge=2000, le=2100, description="사업연도")]


# ---------------------------------------------------------------------------
# Input models (9개 도구)
# ---------------------------------------------------------------------------

class SearchCompanyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: Annotated[
        str,
        Field(min_length=1, description="회사명 부분일치 또는 정확한 종목코드"),
    ]
    limit: Annotated[int, Field(ge=1, le=100)] = 20


class GetFinancialSnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: CompanyIdent
    fs_div: FsDiv = "CFS"
    years: Optional[Annotated[int, Field(ge=1, le=20)]] = None


class ScoreGoingConcernInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: CompanyIdent


class DetectRestatementInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: CompanyIdent
    threshold_pct: Annotated[float, Field(ge=0.1)] = 1.0
    top_n: Annotated[int, Field(ge=1, le=100)] = 10


class GetAccountingPolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: CompanyIdent
    bsns_year: BsnsYear
    fs_div: FsDiv = "CFS"


class GetAuditHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: CompanyIdent


class GetSubsidiaryAuditorsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: CompanyIdent
    limit: Annotated[int, Field(ge=1, le=1000)] = 100
    only_with_auditor: bool = False
    slim: bool = Field(
        default=True,
        description=(
            "True면 핵심 필드와 연결 총자산/총매출 대비 자산·매출 기여도만 반환. "
            "False면 사업 설명, 원천 자산 문자열 등 상세 필드 포함."
        ),
    )


class CompareToIndustryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: Optional[CompanyIdent] = None
    induty_code: Optional[str] = Field(default=None, description="KSIC 코드. company가 있으면 무시.")
    metric: COMPARE_METRICS = "영업이익률"
    year: Optional[BsnsYear] = None
    fs_div: FsDiv = "CFS"
    prefix_len: Annotated[int, Field(ge=1, le=5)] = 2
    include_peers: bool = True
    peer_limit: Annotated[int, Field(ge=1, le=500)] = 50


class GetBusinessOverviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company: CompanyIdent
    bsns_year: Optional[BsnsYear] = None


# ---------------------------------------------------------------------------
# Output container — structured content 표준
# ---------------------------------------------------------------------------

class KReportsToolResponse(BaseModel):
    """
    모든 KReports MCP 도구가 반환하는 표준 컨테이너.

    실제 도메인 필드는 extra="allow"로 자유 통과시키고,
    `_meta` 키는 alias로 별도 보장한다.
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    meta: dict[str, Any] = Field(default_factory=dict, alias="_meta")


# 도구 이름 → 입력 모델 (call_tool shim에서 사용)
TOOL_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "search_company": SearchCompanyInput,
    "get_financial_snapshot": GetFinancialSnapshotInput,
    "score_going_concern": ScoreGoingConcernInput,
    "detect_restatement": DetectRestatementInput,
    "get_accounting_policy": GetAccountingPolicyInput,
    "get_audit_history": GetAuditHistoryInput,
    "get_subsidiary_auditors": GetSubsidiaryAuditorsInput,
    "compare_to_industry": CompareToIndustryInput,
    "get_business_overview": GetBusinessOverviewInput,
}
