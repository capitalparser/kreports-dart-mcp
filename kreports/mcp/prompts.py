"""Stable professional MCP prompt registry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
)

from kreports.mcp.workflows import WORKFLOW_SPECS


class PromptRequestError(ValueError):
    """Bounded prompt lookup or argument error."""


_COMMON_SAFETY = """
모든 결과에서 DART 원문 링크와 접수번호를 유지한다. 확인된 사실, 분석,
한계를 분리하고 데이터가 없으면 missing, 실행 실패이면 error라고 명시한다.
리스크 신호를 감사의견 결론이나 투자 추천으로 바꾸지 않는다. QSC, 구성감사인,
회계정책 또는 정책 차이의 근거를 날조(fabricate)하지 않는다. 원 공시 근거가
없는 판단은 한계와 후속 확인사항으로 남긴다.
""".strip()

_WORKFLOW_TEXT = {
    "investor_first_pass": (
        "투자자 1차 검토 워크플로를 실행하여 재무 스냅샷, 이익의 질, "
        "최근 공시 이벤트, 회계·감사 리스크 신호를 검토한다."
    ),
    "audit_acceptance_review": (
        "감사 수임·유지 검토 워크플로를 실행하여 감사인/감사의견 이력, "
        "보고서 matter, 감사보수와 독립성, peer 리스크 및 증거 공백을 검토한다."
    ),
    "group_audit_scope": (
        "그룹감사 범위 워크플로를 실행하여 실체 그래프, 소유지분, 자산·매출 "
        "기여도, QSC 근거와 구성감사인 증거의 가용성을 검토한다."
    ),
    "accounting_policy_peer_review": (
        "회계정책 peer 검토 워크플로를 실행하여 정책 본문, 변경 이력, "
        "peer 차이와 KAM 연계를 검토한다."
    ),
    "semantic_peer_context_review": (
        "사업·감사·주석·공시의 로컬 DART 증빙과 하나의 설명 가능한 peer cohort를 "
        "결합하고, caller-supplied IR·web/news를 출처별로 분리하는 읽기 전용 워크플로를 실행한다."
    ),
}

_SEMANTIC_PEER_CONTEXT_GUIDANCE = """
호스트 통합 환경에서는 `semantic_peer_context_review` adapter를 사용한다. 이 adapter는
`get_semantic_company_context`, customizable `peer_criteria` cohort, 그리고
`compare_peer_accounting_notes`를 하나의 read-only 요청으로 조합하며 cohort를 한 번만
선택한다. 일반 MCP 도구를 각각 호출해 새 cohort를 재계산한 결과를 동일 peer 비교로
표시하지 않는다.

출처 우선순위는 **DART → company IR → web/news → LLM** 이다. DART는 `confirmed facts`,
company IR는 `management claims`, web/news는 `external context`, LLM 산출물은 `analysis`로
분리한다. 모든 analysis와 counterpoint에는 source_id를 인용한다. IR·web/news는
caller-supplied evidence만 받으며 이 workflow는 외부 검색, API 호출, 백필, DB 쓰기를 하지 않는다.

`selection_policy.fs_div_used`와 정확한 사업연도를 재무·주석에 공통 적용한다. 선택된
CFS/OFS 주석이 캐시에 없으면 `fs_div_selection`의 명시적 fallback 상태와 locator를 유지하며,
fallback을 동일 기준 증거로 숨기지 않는다. `unavailable`은 로컬 캐시 부재, `summary_only`는
원문이 외부화·절단되었음을 뜻하므로 원 공시 부재나 완전한 원문으로 해석하지 않는다.
""".strip()


@dataclass(frozen=True)
class PromptSpec:
    name: str
    description: str

    def to_mcp(self) -> Prompt:
        return Prompt(
            name=self.name,
            description=self.description,
            arguments=[
                PromptArgument(
                    name="company",
                    description="corp_code, stock code, or exact company name",
                    required=True,
                ),
                PromptArgument(
                    name="year",
                    description="Exact business year (2000-2100)",
                    required=True,
                ),
            ],
        )


def list_prompts() -> list[PromptSpec]:
    return [
        PromptSpec(name=name, description=description)
        for name, description in _WORKFLOW_TEXT.items()
    ]


def mcp_prompts() -> list[Prompt]:
    return [spec.to_mcp() for spec in list_prompts()]


def _validated_arguments(
    arguments: Mapping[str, str] | None,
) -> tuple[str, int]:
    values = dict(arguments or {})
    if set(values) - {"company", "year"}:
        raise PromptRequestError("invalid_argument")
    company = str(values.get("company") or "").strip()
    raw_year = str(values.get("year") or "").strip()
    if not company or len(company) > 120:
        raise PromptRequestError("invalid_argument:company")
    if not raw_year.isdigit() or not 2000 <= int(raw_year) <= 2100:
        raise PromptRequestError("invalid_argument:year")
    return company, int(raw_year)


def get_prompt(
    name: str,
    arguments: Mapping[str, str] | None = None,
) -> GetPromptResult:
    description = _WORKFLOW_TEXT.get(str(name))
    if description is None:
        raise PromptRequestError("unknown_prompt")
    company, year = _validated_arguments(arguments)
    if name == "semantic_peer_context_review":
        workflow_instruction = _SEMANTIC_PEER_CONTEXT_GUIDANCE
    else:
        workflow_instruction = (
            "호출 순서(각 specialist는 한 번만 호출): "
            f"{', '.join(WORKFLOW_SPECS[name])}"
        )
    text = (
        f"{description}\n\n대상 회사: {company}\n사업연도: {year}\n\n"
        f"{workflow_instruction}\n\n{_COMMON_SAFETY}"
    )
    return GetPromptResult(
        description=description,
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=text),
            )
        ],
    )
