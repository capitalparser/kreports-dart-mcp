"""Immutable single source of truth for all public MCP tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mcp.types import Tool
from pydantic import BaseModel

from kreports.mcp import input_models as models
from kreports.mcp.handlers import HANDLERS


def _legacy_compatible_schema(model: type[BaseModel], name: str) -> dict:
    """Generate the typed schema while retaining the established MCP wire shape."""
    schema = model.model_json_schema()
    schema.pop("title", None)
    schema.pop("$defs", None)
    schema.pop("additionalProperties", None)

    def clean(node: object) -> None:
        if isinstance(node, dict):
            node.pop("title", None)
            if node.get("default") is None:
                node.pop("default", None)
            any_of = node.get("anyOf")
            if isinstance(any_of, list):
                non_null = [
                    item
                    for item in any_of
                    if not (isinstance(item, dict) and item.get("type") == "null")
                ]
                if len(non_null) == 1 and isinstance(non_null[0], dict):
                    replacement = dict(non_null[0])
                    node.pop("anyOf", None)
                    node.update(replacement)
            node.pop("format", None)
            node.pop("writeOnly", None)
            for value in list(node.values()):
                clean(value)
        elif isinstance(node, list):
            for value in node:
                clean(value)

    clean(schema)
    if name == "get_industry_audit_landscape":
        schema["oneOf"] = [
            {"required": ["company"]},
            {"required": ["induty_code"]},
        ]
    return schema


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[BaseModel], dict]
    read_only: bool
    professional: bool

    def to_mcp_tool(self) -> Tool:
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema=_legacy_compatible_schema(self.input_model, self.name),
        )


_DESCRIPTIONS = {
    "search_company": "회사명 또는 종목코드로 DART 등록 상장사를 검색한다. corp_code(8자리 DART 식별자), 종목코드, 시장(KOSPI/KOSDAQ/KONEX)을 반환한다. 다른 도구를 호출하기 전에 corp_code를 확보할 때 먼저 사용한다.",
    "get_financial_snapshot": "연도별 핵심 재무지표와 자본배분 지표 스냅샷. 억원 단위. 매출·영업이익·순이익·자산/부채/자본 + FCF, FCF마진, ROIC, CCC, DIO/DSO/DPO, 이자보상배율 등 자본배분·운전자본 지표 포함. CFS(연결) 우선, 없으면 OFS(별도) 자동 폴백. 재무 트렌드 분석·동종업종 비교 시 먼저 호출한다.",
    "score_going_concern": "6인자 계속기업 위험 스코어카드. 100점 만점 감점방식. 자본잠식(-30), 2년연속 영업손실(-20), 부채비율>200%(-15), 이자보상배율<1.0(-15), 최근 영업CF<0(-10), 최근 비적정 감사의견(-10). 등급: 안정(80+) / 주의(60-79) / 경고(40-59) / 위험(<40). 부실위험 스크리닝, 거래처 신용평가, 상장폐지 모니터링에 사용.",
    "detect_restatement": "사업보고서 간 소급 재작성(prior period error) 감지. N년 사업보고서의 당기금액과 N+1년 사업보고서의 전기금액을 계정별로 비교하여 threshold_pct 이상 차이나는 항목을 반환. 회계정책 변경·오류수정·연결범위 변경 식별에 사용. 감사 리스크 평가 시 과거 재작성 이력은 강력한 리드 지표.",
    "get_accounting_policy": "특정 연도 사업보고서 주석에서 회계정책 항목 추출. 수익인식·재고자산·유형자산 감가상각·리스·금융상품·충당부채·법인세 등 약 15개 표준 item_key별로 heading과 본문을 반환한다. 업종별 중요 회계처리 대비·정책 변경 추적에 사용. bsns_year는 연간 사업보고서가 제출된 연도(보통 전년도 재무).'",
    "get_audit_history": "연도별 감사인·감사의견·연속 감사연수 이력. CFS(연결)과 OFS(별도)을 구분하여 반환. 감사인 교체 여부, 비적정 의견 발생 여부, 동일 감사인의 연속 감사연수(주기적 지정 감사제 추적)를 식별할 수 있다. 감사 품질 평가·거버넌스 디스카운트 분석에 사용.",
    "get_subsidiary_auditors": "최근 사업보고서 기준 종속·관계회사별 감사인 매트릭스. 연결 총자산·총매출 대비 각 실체의 자산/매출 기여도와 지분율을 함께 보여준다. QSC는 연결 총자산 또는 연결 총매출 대비 10% 이상인 실체로 판정한다. 연결그룹 내 감사인 이원화 여부, 주요 종속회사 감사 품질, 해외 자회사 감사인 정보를 한 번에 파악할 수 있다. 대형 그룹(삼성전자 등)은 400개 이상이므로 기본값은 QSC 우선 + 상위 100개 + 핵심 필드만 반환한다. truncated=true면 total 참고 후 limit 증가시켜 재호출. 그룹 감사 리스크 평가·PCAOB 이슈 추적에 사용.",
    "compare_to_industry": "동종업종(KSIC induty_code prefix 매칭) 내 재무지표 분포와 특정 회사의 위치 비교. 지원 metric: 영업이익률, 순이익률, 부채비율, ROE, ROA, 자기자본비율, 매출성장률, Beneish_M. 기본 prefix_len=2 (KSIC 대분류). 응답: P25/P50/P75 quantile + peers 리스트 + subject(입력 회사) 값과 percentile. n<3이면 P25/P75는 null이며 희소성 note를 포함한다. 업종 내 상대 포지션, 경쟁사 대비 우열, 밸류에이션 peer 선정에 사용.",
    "get_business_overview": "사업보고서 핵심 경영 정보 텍스트 + 업종 특화 인사이트 반환. 사업개요, 사업내용, 위험관리, 경영계획, R&D, 주요계약 6개 섹션의 본문 텍스트와 KSIC 업종 분류 기반 rule-based 인사이트(R&D 비율, 위험 분포, 업종 키워드)를 포함. Claude가 이 텍스트를 받아 업종 맥락에 맞는 심층 분석을 수행할 수 있다. 감사 계획 수립(ISA 315 사업 이해), 투자 분석(비즈니스 모델 파악)에 활용.",
    "get_semantic_company_context": "한 회사·사업연도의 로컬 DART 캐시를 사업보고서, 감사보고서, 주석, 공시, 재무 증빙 버킷으로 조합한다. 원문 외부화·절단 상태와 source locator를 보존하며, unavailable은 로컬 캐시 부재이지 원 공시 부재가 아니다. 읽기 전용이며 DART API 호출이나 백필을 수행하지 않는다.",
    "get_investor_signals": "투자자 관점의 품질·회계리스크·최근 공시 이벤트 요약. 버핏식 퀄리티 체크(ROE, 영업이익률, 매출성장, 부채비율, FCF, CFO/NI), 회계/거버넌스 리스크 점수(Beneish, 정정공시, 감사의견, 감사인 교체, NAS 등), 최근 자기주식·유상증자·CB/BW/EB·합병/분할·대규모 계약·소송 공시 제목 신호를 묶어 반환한다. 보유종목 정기 점검, 신규 종목 1차 스크리닝, 리스크 플래그 확인에 사용.",
    "select_peer_group": "감사인 관점 peer group 선정 근거팩. KSIC 업종, sector 분리, 자산규모 bucket, 재무데이터/감사보수 coverage를 기준으로 peer 목록과 include_reasons를 반환한다.",
    "compare_to_industry_multi": "동종업종 내 다지표·다년도(기본 5년) 분포와 subject 회사 percentile을 한 번에 반환. peer 매칭은 adaptive ladder(p3→p2) + sector mutual exclusion(금융/지주/부동산/일반) + 옵션 size bucket(자산 log10 ±decade). 응답은 {year: {metric: {p25, p50, p75, n, subject_value, percentile, unit}}} 형태의 matrix와 meta(matched_prefix_len, confidence, sector_group 등)를 포함. 단일 회사의 업종 내 상대 포지션을 다지표·다년도로 한 번에 파악할 때 사용.",
    "compare_peer_audit_fees": "감사보수와 감사시간을 peer group 기준으로 벤치마크한다. 감사보수, 감사시간, 비감사보수 비율, 자산 대비 보수, 시간당 보수의 분위수와 subject percentile을 반환한다.",
    "compare_peer_risk_profile": "감사인 관점 재무 위험 신호팩. peer group 기준 현금흐름/발생액/Beneish 신호와 정정·주요사항 공시 카운트를 반환하며 감사 리스크 판단 자체는 수행하지 않는다.",
    "compare_peer_accounting_policies": "감사인 관점 회계정책 peer 비교. local DB에 캐시된 accounting_policy_items만 사용해 subject item 보유 현황과 peer item_key coverage를 반환한다. DART key 없이 동작하며, coverage가 낮으면 dataset refresh 필요성을 명시한다.",
    "compare_peer_accounting_notes": "같은 사업연도의 회계 주석을 기준회사와 peer별로 나란히 비교한다. 수익, 리스, 금융상품, 특수관계자, 충당·우발, 손상, 종속기업, 후속사건, 회계정책 topic별 원문 excerpt와 source locator·외부화 상태를 반환한다. 로컬 캐시 부재는 unavailable로 명시하며, 공시 부재로 추론하지 않는다.",
    "compare_peer_kam_topics": "동종업종 감사보고서/KAM screening. 로컬 DB에 영속화된 독립감사보고서 본문 섹션이 있으면 KAM 본문 topic hint, 핵심감사사항 선정 이유 hint, 감사절차/대응 절차 excerpt를 우선 반환하고, 사업보고서 KAM은 요약 정보로만 별도 표시한다. 본문 coverage가 부족하면 감사보고서 제출·정정·지연 공시 기반 screening으로 graceful degradation한다.",
    "compare_peer_audit_report_matters": "감사보고서의 기타사항, 강조사항, 계속기업 관련 문단, 감사의견 근거 문단을 peer group 기준으로 비교한다. 수임/유지 검토와 감사보고서 이슈 screening용 evidence pack이며 감사의견 판단을 대체하지 않는다.",
    "search_dataset": "주요 로컬 캐시 데이터셋을 회사, 연도, 시장, 업종, 키워드 기준으로 공통 검색한다. 감사보고서 섹션(KAM/강조/기타/계속기업), 회계정책, 주석 2/3/4번 챕터, 공시목록, 감사보수·시간, 재무요약을 동일한 응답 구조로 반환하여 '어느 회사/업종/연도에 해당 이슈가 있는가' 유형의 질문에 사용한다.",
    "fetch_disclosure_on_demand": "사용자가 제공한 OpenDART API key로 특정 접수번호의 수시공시 원문을 온디맨드 조회하고 읽기 전용 런타임에서는 요청 범위의 요약만 반환한다. collector의 명시적 외부 원문 저장 정책에서만 source_documents에 캐시하며, 공개 MCP 서버의 DART_API_KEY는 사용하지 않는다. 사용자 key는 저장하거나 응답에 노출하지 않는다.",
    "search_audit_report_matters": "감사보고서 기타사항·강조사항·계속기업 문단을 회사, 특정연도, 시장, 업종코드 prefix 기준으로 검색한다. '특정 회사에 강조/기타사항이 있어?'와 '특정 연도/업종에서 강조사항 있는 회사가 어디야?' 유형의 질문에 회사별 count와 본문 excerpt를 정렬해 반환한다.",
    "search_audit_procedures": "KAM 본문에서 분리한 감사절차 항목을 회사, 연도, 시장, 업종, KAM topic, 절차 유형, 키워드로 검색한다. '수익인식 KAM에서 어떤 감사절차를 했나', '동종업종에서 내부통제 테스트가 언급된 회사는?' 같은 질문에 사용한다.",
    "compare_peer_audit_procedures": "기준 회사와 peer group의 KAM 감사절차 유형을 비교한다. 내부통제, 입증절차, 추정/가정 평가, 외부조회, 전문가 활용 등 절차 유형별 분포를 보여준다.",
    "get_kam_lifecycle": "특정 회사의 5개년 KAM 주제 변화, 반복 여부, 문구 변화, 선정 이유/감사절차 hint를 반환한다. 감사위험 변화, 수임/유지 검토, peer 비교 전 사전 진단에 사용한다.",
    "get_accounting_policy_changes": "사업보고서 주석 2/3/4의 회계정책·추정판단 문구 변화를 5개년으로 비교한다. 정책 변경 후보, 추정 불확실성 변화, 전년 대비 문구 변경을 검토할 때 사용한다.",
    "get_quality_of_earnings_pack": "투자자 관점에서 5개년 이익의 질을 점검한다. 순이익-영업현금흐름 전환율, 영업마진 변동성, 음의 영업현금흐름, 감사보고서 matter 신호를 함께 반환한다.",
    "get_dcf_input_candidates": "5개년 재무 실제값에서 DCF 입력 후보를 산출한다. 매출성장률, 영업마진, 현금전환율 등 관측값 기반 후보와 WACC/세율/CAPEX 등 별도 판단이 필요한 누락 입력을 분리한다.",
    "search_disclosure_events": "유상증자, 전환사채, 소송, 최대주주 변경, 횡령배임, 주요 계약, 자산거래, 감사관련 공시 이벤트를 회사/기간/시장/이벤트 유형으로 검색한다. 원문 확인은 fetch_disclosure_on_demand와 연결한다.",
    "get_audit_report_sections": "로컬 DB에 저장된 감사보고서 본문 섹션 조회. collect-audit-report-sections로 영속화한 감사의견, 핵심감사사항/KAM, 강조사항, 계속기업, 감사인의 책임 문단을 반환한다. KAM에는 topic, 선정 이유 hint, 감사절차 hint를 함께 반환한다.",
    "estimate_audit_hours_proxy": "표준감사시간 산정 전단계의 public-data 감사난이도 proxy. 자산규모, 감사시간 peer percentile, 현금흐름 괴리, 계속기업/Beneish signal을 종합해 complexity score를 제공한다. 표준감사시간 결론이나 법정 산정값은 아니다.",
    "build_audit_acceptance_pack": "수임/유지 검토용 DART 외부근거 pack. peer 선정, 감사보수·감사시간, 재무 risk signal, 회계정책 cache coverage, 감사보고서 event를 한 번에 묶어 반환한다. 독립성 확인·수임승인·감사판단을 대체하지 않는다.",
    "get_industry_audit_landscape": "업종 내 감사 시장 분석: 감사인 시장점유율(회사수·자산가중 top_n), Big4 점유율, 비적정 의견 발생율(years_back 누적), 평균 tenure(consecutive_years), subject 회사의 감사인 정보. peer는 adaptive ladder + sector 분리 적용. 감사 리스크 평가, 동종업종 감사 시장 이해, 거버넌스 디스카운트 분석에 사용. auditors 데이터가 부족하면 latest_year=None과 함께 graceful note.",
    "build_dcf_model_pack": "정확한 사업연도와 CFS/OFS의 로컬 공시 실제값, 명시적 정규화와 분석가 가정을 분리해 Decimal 기반 DCF 검토 모델을 만든다. UFCF·터미널가치·기업가치·순부채 브리지와 5x5 민감도를 제공하며 누락 입력은 자동 보충하지 않는다. 투자 권유, 공정성 의견, 승인된 예측 또는 감사 결론이 아니다.",
}


_INPUT_MODELS = {
    "search_company": models.SearchCompanyInput,
    "get_financial_snapshot": models.GetFinancialSnapshotInput,
    "score_going_concern": models.ScoreGoingConcernInput,
    "detect_restatement": models.DetectRestatementInput,
    "get_accounting_policy": models.GetAccountingPolicyInput,
    "get_audit_history": models.GetAuditHistoryInput,
    "get_subsidiary_auditors": models.GetSubsidiaryAuditorsInput,
    "compare_to_industry": models.CompareToIndustryInput,
    "get_business_overview": models.GetBusinessOverviewInput,
    "get_semantic_company_context": models.GetSemanticCompanyContextInput,
    "get_investor_signals": models.GetInvestorSignalsInput,
    "select_peer_group": models.SelectPeerGroupInput,
    "compare_to_industry_multi": models.CompareToIndustryMultiInput,
    "compare_peer_audit_fees": models.ComparePeerAuditFeesInput,
    "compare_peer_risk_profile": models.ComparePeerRiskProfileInput,
    "compare_peer_accounting_policies": models.ComparePeerAccountingPoliciesInput,
    "compare_peer_accounting_notes": models.ComparePeerAccountingNotesInput,
    "compare_peer_kam_topics": models.ComparePeerKamTopicsInput,
    "compare_peer_audit_report_matters": models.ComparePeerAuditReportMattersInput,
    "search_dataset": models.SearchDatasetInput,
    "fetch_disclosure_on_demand": models.FetchDisclosureOnDemandInput,
    "search_audit_report_matters": models.SearchAuditReportMattersInput,
    "search_audit_procedures": models.SearchAuditProceduresInput,
    "compare_peer_audit_procedures": models.ComparePeerAuditProceduresInput,
    "get_kam_lifecycle": models.GetKamLifecycleInput,
    "get_accounting_policy_changes": models.GetAccountingPolicyChangesInput,
    "get_quality_of_earnings_pack": models.GetQualityOfEarningsPackInput,
    "get_dcf_input_candidates": models.GetDcfInputCandidatesInput,
    "search_disclosure_events": models.SearchDisclosureEventsInput,
    "get_audit_report_sections": models.GetAuditReportSectionsInput,
    "estimate_audit_hours_proxy": models.EstimateAuditHoursProxyInput,
    "build_audit_acceptance_pack": models.BuildAuditAcceptancePackInput,
    "get_industry_audit_landscape": models.GetIndustryAuditLandscapeInput,
    "build_dcf_model_pack": models.BuildDcfModelPackInput,
}


TOOL_CATALOG: dict[str, ToolSpec] = {
    name: ToolSpec(
        name=name,
        description=_DESCRIPTIONS[name],
        input_model=input_model,
        handler=HANDLERS[name],
        read_only=name != "fetch_disclosure_on_demand",
        professional=True,
    )
    for name, input_model in _INPUT_MODELS.items()
}
