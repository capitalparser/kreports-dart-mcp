"""Strict typed arguments for the 34 public MCP tools."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from typing import Annotated, Literal

from kreports.analysis.dcf_model import (
    MIN_DECIMAL_ADJUSTED,
    dcf_decimal_fits_serialization,
)
from kreports.analysis.peer_criteria import PeerCriteriaProfile
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


FsDiv = Literal["CFS", "OFS"]
FsStrategy = Literal["CFS", "OFS", "auto"]
Metric = Literal[
    "영업이익률",
    "순이익률",
    "부채비율",
    "ROE",
    "ROA",
    "자기자본비율",
    "매출성장률",
    "Beneish_M",
]
Year = Annotated[int, Field(ge=2000, le=2100)]
PeerSelector = Annotated[str, Field(min_length=1, max_length=100)]
SemanticContextTopic = Literal[
    "business_overview",
    "major_shareholders_board",
    "risks",
    "raw_materials",
    "facilities",
    "contracts",
    "accounting_policies",
    "kam",
    "audit_opinion",
    "subsequent_events",
]
NoteTopic = Literal[
    "revenue",
    "leases",
    "financial_instruments",
    "related_parties",
    "provisions_contingencies",
    "impairment",
    "subsidiaries",
    "subsequent_events",
    "accounting_policies",
]


class ToolInput(BaseModel):
    """The only accepted base configuration for public tool arguments."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def trim_string_arguments(cls, value):
        if not isinstance(value, dict):
            return value
        return {
            key: (
                item.strip()
                if isinstance(item, str)
                else SecretStr(item.get_secret_value().strip())
                if isinstance(item, SecretStr)
                else item
            )
            for key, item in value.items()
        }

    @model_validator(mode="after")
    def reject_blank_required_strings(self):
        blank_fields = [
            name
            for name, field in type(self).model_fields.items()
            if field.is_required()
            and isinstance(getattr(self, name), str)
            and not getattr(self, name)
        ]
        if blank_fields:
            raise ValueError(
                f"{', '.join(blank_fields)} 값은 비어 있을 수 없습니다."
            )
        return self


class SearchCompanyInput(ToolInput):
    query: str = Field(
        description="회사명 부분일치 또는 정확한 종목코드 (예: '삼성전자', '005930')"
    )
    limit: int = Field(20, ge=1, le=100, description="반환 최대 개수 (기본 20)")


class GetFinancialSnapshotInput(ToolInput):
    company: str = Field(description="corp_code(8자리) / 종목코드(6자리) / 회사명 중 아무거나")
    fs_div: FsDiv = Field("CFS", description="CFS=연결재무제표 (기본), OFS=별도재무제표")
    years: Annotated[int, Field(ge=1, le=20, description="최근 N개 연도만. 생략 시 전체 수집 연도 반환.")] | None = None


class ScoreGoingConcernInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")


class DetectRestatementInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")
    threshold_pct: float = Field(1.0, ge=0.1, description="변동률 임계값 (%). 기본 1.0%")
    top_n: int = Field(10, ge=1, le=100, description="반환 최대 항목 수 (절대변동률 기준 내림차순)")


class GetAccountingPolicyInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")
    bsns_year: Year = Field(description="사업연도 (예: 2024는 2024 재무 → 2025년 3월 제출 사업보고서)")
    fs_div: FsDiv = "CFS"


class GetAuditHistoryInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")


class GetSubsidiaryAuditorsInput(ToolInput):
    company: str = Field(description="모회사의 corp_code / 종목코드 / 회사명")
    limit: int | None = Field(100, ge=1, le=1000, description="반환 최대 종속회사 수. 기본 100. 전체를 원하면 500+ 설정.")
    only_with_auditor: bool = Field(False, description="True면 감사인 정보가 있는 종속회사만 반환.")
    slim: bool = Field(
        True,
        description=(
            "True면 핵심 필드만 반환 (name, relation, ownership_pct, listed_yn, "
            "asset_amount_m, asset_share_pct, revenue_amount_m, revenue_share_pct, "
            "is_qsc, qsc_status, qsc_basis, corp_code, stock_code, market, auditor). "
            "False면 business 설명·assets 등 전체 필드."
        ),
    )


class CompareToIndustryInput(ToolInput):
    company: str | None = Field(
        None,
        description="기준 회사 (corp_code / 종목코드 / 회사명). induty_code를 자동 조회하고, 응답에 subject 필드로 포함.",
    )
    induty_code: str | None = Field(None, description="company 대신 직접 KSIC 코드를 지정. company가 있으면 무시.")
    metric: Metric = "영업이익률"
    year: Year | None = Field(None, description="사업연도 (Q4 기준). 생략 시 해당 업종 내 최신 연도.")
    fs_div: FsDiv = "CFS"
    prefix_len: int = Field(
        2,
        ge=1,
        le=5,
        description="induty_code 앞 몇 자리로 매칭할지. 2=KSIC 대분류(권장), 3=중분류, 5=세분류(정확).",
    )
    include_peers: bool = True
    peer_limit: int = Field(50, ge=1, le=500)


class GetBusinessOverviewInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")
    bsns_year: Year | None = Field(None, description="사업연도. 생략 시 최신 사업보고서.")
    include_semantic_context: bool = Field(
        False,
        description="선택. 기존 사업개요에 읽기 전용 회사·연도 증빙 버킷을 추가한다.",
    )
    semantic_topics: list[SemanticContextTopic] | None = Field(None, max_length=10)
    note_topics: list[NoteTopic] | None = Field(None, max_length=9)


class GetSemanticCompanyContextInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")
    year: Year = Field(description="대상 사업연도")
    topics: list[SemanticContextTopic] | None = Field(
        None,
        max_length=10,
        description=(
            "선택한 의미 증빙 주제만 반환. 생략하면 사업·감사·주석·공시·재무의 "
            "로컬 캐시 버킷을 모두 반환한다."
        ),
    )
    note_topics: list[NoteTopic] | None = Field(
        None,
        max_length=9,
        description=(
            "회계주석 비교용 주제. topics의 사업·감사 증빙 필터와 독립적으로 "
            "적용되며, 둘을 함께 지정해도 각 버킷은 별도로 필터링한다."
        ),
    )

    @field_validator("topics")
    @classmethod
    def unique_topics(cls, value: list[SemanticContextTopic] | None):
        if value is None:
            return value
        return list(dict.fromkeys(value))

    @field_validator("note_topics")
    @classmethod
    def unique_note_topics(cls, value: list[NoteTopic] | None):
        if value is None:
            return value
        return list(dict.fromkeys(value))


class GetInvestorSignalsInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")
    years: int = Field(5, ge=1, le=10, description="퀄리티 체크에 사용할 최근 N개 연도. 기본 5.")
    window_days: int = Field(365, ge=1, le=3650, description="최근 공시 이벤트 검색 기간. 기본 365일.")
    event_limit: int = Field(20, ge=1, le=100, description="반환할 최근 이벤트 최대 개수. 기본 20.")


class SelectPeerGroupInput(ToolInput):
    company: str
    criteria: list[str] | PeerCriteriaProfile | None = Field(
        None,
        description=(
            "기존 문자열 기준 목록 또는 strict/adaptive/ranked 동종업종 프로필. "
            "프로필은 KSIC·sector·규모·포함/제외 기업·증빙 충족률을 명시한다."
        ),
    )
    peer_criteria: PeerCriteriaProfile | None = Field(
        None,
        description="criteria의 명시적 별칭. criteria와 동시에 지정할 수 없다.",
    )
    peer_limit: int = Field(30, ge=1, le=200)
    fs_strategy: FsStrategy = "auto"
    prefix_len_start: int = Field(3, ge=2, le=5)
    size_bucket_decade: float | None = Field(None, ge=0.5, le=3.0)
    exclude_other_sectors: bool = True

    @model_validator(mode="after")
    def reject_duplicate_peer_criteria(self):
        if self.criteria is not None and self.peer_criteria is not None:
            raise ValueError("criteria와 peer_criteria는 동시에 지정할 수 없습니다.")
        return self


class CompareToIndustryMultiInput(ToolInput):
    company: str = Field(description="기준 회사 (corp_code / 종목코드 / 회사명).")
    metrics: list[Metric] | None = Field(None, description="비교 지표 리스트. 생략 시 8개 전체.")
    years_back: int = Field(5, ge=1, le=10, description="최근 N개 연도. 기본 5.")
    fs_div: FsDiv = "CFS"
    fs_strategy: FsStrategy = Field("auto", description="auto면 CFS 우선, 없으면 OFS로 비교한다.")
    prefix_len_start: int = Field(
        3, ge=2, le=5, description="KSIC ladder 시작 자리 수. 기본 3 (소분류). n<5면 2자리로 자동 fallback."
    )
    exclude_other_sectors: bool = Field(
        True, description="True면 금융/지주/부동산/일반 sector group 간 분리. 기본 True."
    )
    size_bucket_decade: float | None = Field(
        None, ge=0.5, le=3.0, description="자산 log10 ±decade 필터 (예: 1.0=±10배). 생략 시 미적용."
    )


class ComparePeerAuditFeesInput(ToolInput):
    company: str
    year: Year = 2025
    peer_limit: int = Field(30, ge=1, le=200)
    fs_strategy: FsStrategy = "auto"
    size_bucket_decade: float | None = Field(None, ge=0.5, le=3.0)


class PrepareStandardAuditHoursInputsInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")
    year: Year = 2025
    fs_strategy: FsStrategy = Field(
        "auto", description="auto면 CFS 우선, 없으면 OFS 한 기준으로 최근 3개년을 준비한다.",
    )


class PrepareAuditMaterialityInputsInput(ToolInput):
    company: str = Field(description="corp_code / 종목코드 / 회사명")
    end_year: Year = 2025
    years_back: Literal[3, 5] = Field(5, description="3년 또는 5년 비교 범위")
    fs_strategy: FsStrategy = Field(
        "auto", description="auto면 CFS 우선, 없으면 OFS 한 기준으로 비교합니다.",
    )


class ComparePeerRiskProfileInput(ToolInput):
    company: str
    year: Year = 2025
    peer_limit: int = Field(30, ge=1, le=200)
    fs_strategy: FsStrategy = "auto"


class ComparePeerAccountingPoliciesInput(ToolInput):
    company: str
    year: Year = 2025
    peer_limit: int = Field(30, ge=1, le=200)
    fs_div: FsDiv = "CFS"
    fs_strategy: FsStrategy = "auto"
    item_key: str | None = Field(None, max_length=50, description="선택. 표준 회계정책 item_key")
    keyword: str | None = Field(None, max_length=100, description="선택. 제목/본문 주제 검색어")
    selection_profile: Literal["auditor", "investor", "balanced"] = "balanced"
    peer_weights: dict[str, float] | None = Field(
        None, description="선택. size/leverage/profitability/growth 전체 가중치(0~1); 생략 키는 0"
    )
    size_bucket_decade: float | None = Field(None, ge=0.1, le=3.0)
    include_peers: list[PeerSelector] = Field(default_factory=list, max_length=50)
    exclude_peers: list[PeerSelector] = Field(default_factory=list, max_length=50)
    include_note_comparison: bool = Field(
        False,
        description="선택. 같은 사업연도 peer별 회계주석 원문 발췌·출처를 추가한다.",
    )
    include_note_disclosure_matrix: bool = Field(
        False,
        description="선택. topic별 회사 주석 로컬 확인 매트릭스를 추가한다. 대상회사 포함 최대 200개이며, 원문 미확보는 공시 부재로 판단하지 않는다.",
    )
    note_topics: list[NoteTopic] | None = Field(None, max_length=9)
    peer_offset: int = Field(0, ge=0)
    page_size: int | None = Field(None, ge=1, le=200)
    peer_criteria: PeerCriteriaProfile | list[str] | None = Field(
        None,
        description="선택. 주석 side-by-side 비교에 적용할 동종기업 기준.",
    )

    @field_validator("peer_weights")
    @classmethod
    def validate_peer_weights(cls, value):
        if value is None:
            return value
        supported = {"size", "leverage", "profitability", "growth"}
        unknown = sorted(set(value) - supported)
        if unknown:
            raise ValueError(f"지원하지 않는 peer weight: {', '.join(unknown)}")
        if not value or any(
            not isinstance(weight, (int, float))
            or isinstance(weight, bool)
            or not math.isfinite(float(weight))
            or not 0 <= weight <= 1
            for weight in value.values()
        ):
            raise ValueError("peer_weights는 0~1 범위의 하나 이상의 값이어야 합니다.")
        if sum(value.values()) <= 0:
            raise ValueError("peer_weights 합계는 0보다 커야 합니다.")
        return value

    @field_validator("include_peers", "exclude_peers")
    @classmethod
    def normalize_peer_selectors(cls, value):
        normalized = []
        for selector in value:
            clean = selector.strip()
            if not clean:
                raise ValueError("peer selector는 비어 있을 수 없습니다.")
            normalized.append(clean)
        return normalized

    @model_validator(mode="after")
    def validate_peer_overrides(self):
        included = set(self.include_peers)
        overlap = sorted(included & set(self.exclude_peers))
        if overlap:
            raise ValueError(f"include_peers와 exclude_peers가 중복됩니다: {', '.join(overlap)}")
        if self.company in included or self.company in set(self.exclude_peers):
            raise ValueError("대상회사는 include_peers 또는 exclude_peers에 넣을 수 없습니다.")
        return self

    @field_validator("note_topics")
    @classmethod
    def unique_note_topics(cls, value):
        return list(dict.fromkeys(value)) if value is not None else value


class ComparePeerAccountingNotesInput(ToolInput):
    company: str
    year: Year = 2025
    topics: list[NoteTopic] | None = Field(None, max_length=9)
    peer_limit: int = Field(30, ge=1, le=200)
    peer_offset: int = Field(0, ge=0, description="동종기업 비교 페이지의 0-기반 offset")
    page_size: int | None = Field(None, ge=1, le=200, description="선택. 생략하면 peer_limit을 페이지 크기로 사용한다.")
    fs_strategy: FsStrategy = "auto"
    peer_criteria: PeerCriteriaProfile | list[str] | None = Field(
        None,
        description="선택한 동종업종 기준. 생략하면 기존 adaptive peer 정책을 사용한다.",
    )

    @field_validator("topics")
    @classmethod
    def unique_note_topics(cls, value):
        return list(dict.fromkeys(value)) if value is not None else value


class ComparePeerKamTopicsInput(ComparePeerRiskProfileInput):
    pass


class ComparePeerAuditReportMattersInput(ComparePeerRiskProfileInput):
    pass


class SearchDatasetInput(ToolInput):
    dataset: Literal[
        "source_documents",
        "report_sections",
        "accounting_policies",
        "accounting_note_chapters",
        "evidence_documents",
        "disclosures",
        "audit_fees",
        "financials",
    ]
    company: str | None = Field(None, description="선택. corp_code/stock_code/company name")
    year: Year | None = None
    market: str | None = Field(None, description="선택. KOSPI/KOSDAQ/KONEX")
    induty_prefix: str | None = Field(None, description="선택. KSIC/업종코드 prefix 예: 26")
    keyword: str | None = Field(None, description="선택. 본문/제목/감사인명 등 데이터셋별 텍스트 검색어")
    source_type: Literal["audit_report", "business_report"] | None = Field(
        None, description="report_sections 검색 시 선택"
    )
    section_keys: list[str] | None = Field(None, description="report_sections 검색 시 선택. 예: kam, emphasis, other_matter")
    section_type: Literal["basis", "policy", "estimate_judgment", "other_note"] | None = Field(
        None, description="accounting_note_chapters 검색 시 선택"
    )
    fs_div: FsDiv | None = Field(None, description="재무/회계정책 검색 시 선택")
    quarter: int | None = Field(None, ge=1, le=4, description="financials 검색 시 선택")
    limit: int = Field(50, ge=1, le=500)
    include_excerpt: bool = True


class FetchDisclosureOnDemandInput(ToolInput):
    rcept_no: str = Field(description="DART 접수번호")
    user_dart_api_key: SecretStr | None = Field(
        None,
        repr=False,
        description="요청 사용자의 OpenDART API key. 저장/로그/응답 노출 금지.",
        json_schema_extra={"writeOnly": True},
    )
    cache_policy: Literal["cache_first", "refresh"] = "cache_first"
    corp_code: str | None = Field(None, description="선택. disclosures 메타가 없을 때 캐시 메타데이터로 사용.")
    year: Annotated[int, Field(ge=1900, le=2100, description="선택. disclosures 메타가 없을 때 캐시 메타데이터로 사용.")] | None = None


class SearchAuditReportMattersInput(ToolInput):
    company: str | None = Field(None, description="선택. corp_code/stock_code/company name")
    year: Year | None = None
    market: str | None = Field(None, description="선택. KOSPI/KOSDAQ/KONEX")
    induty_prefix: str | None = Field(None, description="선택. KSIC/업종코드 prefix 예: 26")
    section_keys: list[Literal["other_matter", "emphasis", "going_concern", "basis_for_opinion"]] = [
        "other_matter", "emphasis", "going_concern"
    ]
    limit: int = Field(50, ge=1, le=500)
    include_excerpt: bool = True


class SearchAuditProceduresInput(ToolInput):
    company: str | None = Field(None, description="선택. corp_code/stock_code/company name")
    year: Year | None = None
    market: str | None = Field(None, description="선택. KOSPI/KOSDAQ/KONEX")
    induty_prefix: str | None = Field(None, description="선택. KSIC/업종코드 prefix 예: 26")
    kam_topic: str | None = Field(None, description="예: revenue, inventory, impairment")
    procedure_type: Literal[
        "analytics",
        "cutoff",
        "estimation_assumption",
        "external_confirmation",
        "internal_control",
        "other",
        "substantive_test",
        "valuation_specialist",
    ] | None = None
    keyword: str | None = None
    limit: int = Field(50, ge=1, le=500)
    include_excerpt: bool = True


class ComparePeerAuditProceduresInput(ComparePeerRiskProfileInput):
    pass


class GetKamLifecycleInput(ToolInput):
    company: str
    start_year: Year = 2021
    end_year: Year = 2025


class GetAccountingPolicyChangesInput(GetKamLifecycleInput):
    fs_div: FsDiv | None = None


class GetQualityOfEarningsPackInput(GetKamLifecycleInput):
    fs_div: FsDiv = "CFS"


class GetDcfInputCandidatesInput(GetQualityOfEarningsPackInput):
    pass


class BuildDcfModelPackInput(ToolInput):
    company: str = Field(
        min_length=1,
        max_length=200,
        description="corp_code / 종목코드 / 정확한 회사명",
    )
    base_year: Year
    fs_div: FsDiv = "CFS"
    forecast_years: int = Field(5, ge=1, le=10)
    revenue_growth: float | None = Field(None, gt=-1, le=10)
    operating_margin: float | None = Field(None, ge=-10, le=10)
    tax_rate: float | None = Field(None, ge=0, le=1)
    da_to_revenue: float | None = Field(None, ge=0, le=10)
    capex_to_revenue: float | None = Field(None, ge=0, le=10)
    nwc_to_revenue: float | None = Field(None, ge=-10, le=10)
    wacc: float | None = Field(None, gt=0, le=1)
    terminal_growth: float | None = Field(None, gt=-1, le=1)
    normalized_revenue: float | None = Field(None, gt=0, le=1e24)
    normalized_operating_profit: float | None = Field(
        None,
        ge=-1e24,
        le=1e24,
    )
    normalization_reason: str | None = Field(None, max_length=1000)

    @field_validator(
        "base_year",
        "forecast_years",
        "revenue_growth",
        "operating_margin",
        "tax_rate",
        "da_to_revenue",
        "capex_to_revenue",
        "nwc_to_revenue",
        "wacc",
        "terminal_growth",
        "normalized_revenue",
        "normalized_operating_profit",
        mode="before",
    )
    @classmethod
    def reject_bool_and_nonfinite(cls, value, info):
        if value is None:
            return value
        if isinstance(value, bool):
            raise ValueError(f"{info.field_name} 값은 boolean일 수 없습니다.")
        try:
            converted = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                f"{info.field_name} 값은 유한한 숫자여야 합니다."
            ) from exc
        if not converted.is_finite():
            raise ValueError(
                f"{info.field_name} 값은 유한한 숫자여야 합니다."
            )
        if (
            not dcf_decimal_fits_serialization(converted)
            or (
                converted != 0
                and converted.adjusted() < MIN_DECIMAL_ADJUSTED
            )
        ):
            raise ValueError(
                f"{info.field_name} 값은 지원 정밀도 범위여야 합니다."
            )
        return value

    @model_validator(mode="after")
    def validate_terminal_and_normalization(self):
        if (
            self.wacc is not None
            and self.terminal_growth is not None
            and self.terminal_growth >= self.wacc
        ):
            raise ValueError("terminal_growth는 wacc보다 작아야 합니다.")
        if (
            self.normalized_revenue is not None
            or self.normalized_operating_profit is not None
        ) and not str(self.normalization_reason or "").strip():
            raise ValueError("normalization_reason이 필요합니다.")
        return self


class SearchDisclosureEventsInput(ToolInput):
    company: str | None = None
    start_date: str | None = Field(None, description="YYYY-MM-DD")
    end_date: str | None = Field(None, description="YYYY-MM-DD")
    event_types: list[str] | None = Field(
        None,
        description="capital_raise/litigation/control_change/fraud/major_contract/asset_deal/audit_related",
    )
    market: str | None = None
    limit: int = Field(50, ge=1, le=500)


class GetAuditReportSectionsInput(ToolInput):
    company: str
    year: Year = 2025
    section_key: Literal[
        "audit_opinion",
        "basis_for_opinion",
        "kam",
        "emphasis",
        "other_matter",
        "going_concern",
        "management_responsibility",
        "auditor_responsibility",
    ] | None = None
    source_type: Literal["audit_report", "business_report", "all"] = Field(
        "audit_report",
        description="audit_report=상세 독립감사보고서 본문, business_report=사업보고서 요약 섹션",
    )
    limit: int = Field(20, ge=1, le=100)


class EstimateAuditHoursProxyInput(ComparePeerRiskProfileInput):
    pass


class BuildAuditAcceptancePackInput(ComparePeerRiskProfileInput):
    pass


class GetIndustryAuditLandscapeInput(ToolInput):
    company: str | None = Field(None, description="기준 회사 (corp_code / 종목코드 / 회사명).")
    induty_code: str | None = Field(None, description="company 대신 직접 KSIC 코드를 지정. company가 있으면 무시.")
    years_back: int = Field(5, ge=1, le=10)
    fs_div: FsDiv = "CFS"
    prefix_len_start: int = Field(3, ge=2, le=5)
    top_n: int = Field(10, ge=1, le=50, description="auditor_market_share 최대 항목 수.")
    exclude_other_sectors: bool = True

    @model_validator(mode="after")
    def require_company_or_industry(self) -> "GetIndustryAuditLandscapeInput":
        if not self.company and not self.induty_code:
            raise ValueError("company 또는 induty_code 중 하나 필요")
        return self
