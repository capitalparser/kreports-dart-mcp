"""Canonical accounting-note evidence service.

This module is the single owner for note references, full-text recovery,
related-paragraph extraction, and conservative disclosure-depth assessment.
Search, comparison, MCP resources, and chatbot presentation must consume this
service rather than reimplementing note-text rules in transport-specific code.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import re
from typing import Any, Iterable, Iterator, Literal, Sequence

from sqlalchemy.orm import Session

from kreports.db.engine import get_session
from kreports.db.models import AccountingNoteChapter, Company
from kreports.storage.evidence_blobs import EvidenceBlobStore


NOTE_EVIDENCE_VERSION = "note_evidence.v1"
NOTE_REF_TOKEN_PATTERN = r"n1-[0-9]+-[0-9a-f]{20}"
NOTE_REF_RE = re.compile(rf"^{NOTE_REF_TOKEN_PATTERN}$", re.ASCII)
NOTE_SUMMARY_URI_TEMPLATE = "kreports://note/{note_ref}"
NOTE_PARAGRAPH_URI_TEMPLATE = "kreports://note/{note_ref}/paragraph"
NOTE_PAGE_URI_TEMPLATE = "kreports://note/{note_ref}/page/{page}"
NOTE_PAGE_CHARACTERS = 8_000
MAX_RELATED_PARAGRAPHS = 3
MAX_RELATED_PARAGRAPH_CHARACTERS = 4_000
MAX_RELATED_TEXT_CHARACTERS = 6_000
MAX_PROFILED_SEARCH_COMPANIES = 40
MAX_PROFILED_COMPARISON_COMPANIES = 6  # subject + first five peers


class NoteReferenceError(ValueError):
    """Malformed, stale, or unavailable deterministic note reference."""


@dataclass(frozen=True)
class DisclosureDimension:
    key: str
    label: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class DisclosureTopic:
    key: str
    label: str
    keywords: tuple[str, ...]
    trigger_patterns: tuple[str, ...]
    dimensions: tuple[DisclosureDimension, ...]


_GENERIC_DIMENSIONS = (
    DisclosureDimension(
        "counterparty",
        "상대방·대상",
        (
            r"상대방|거래상대|계약상대|관계기업|종속기업|공동기업|특수목적|SPC|차주|대주단|금융기관|고객|공급자",
        ),
    ),
    DisclosureDimension(
        "amount",
        "금액·한도",
        (
            r"(?:한도|금액|잔액|총액|약정액|보증액|대출액|원금)[^\n]{0,50}(?:원|천원|백만원|억원|조원)",
            r"(?:[0-9][0-9,]*(?:\.[0-9]+)?)[ ]*(?:원|천원|백만원|억원|조원)",
        ),
    ),
    DisclosureDimension(
        "condition",
        "발생·적용 조건",
        (
            r"(?:경우|조건|요건|발생시|발생 시|미달|부족|초과|위반|불이행|충족)",
        ),
    ),
    DisclosureDimension(
        "period",
        "기간·만기",
        (
            r"(?:기간|만기|종료일|개시일|약정기간|계약기간|20[0-9]{2}년|까지)",
        ),
    ),
    DisclosureDimension(
        "current_exposure",
        "당기말 현황",
        (
            r"(?:당기말|보고기간말|현재|실행액|이행액|잔액|미실행|발생하지 않았|사용액|노출액)",
        ),
    ),
)


def _dimension(key: str, label: str, *patterns: str) -> DisclosureDimension:
    return DisclosureDimension(key=key, label=label, patterns=tuple(patterns))


_DISCLOSURE_TOPICS: tuple[DisclosureTopic, ...] = (
    DisclosureTopic(
        key="funding_support",
        label="자금보충·유동성 지원 약정",
        keywords=(
            "자금보충약정",
            "자금 보충 약정",
            "자금보충의무",
            "유동성보충약정",
            "자금지원약정",
            "자금지원 의무",
        ),
        trigger_patterns=(
            r"자금\s*보충|유동성\s*보충|자금\s*지원|부족액\s*보충",
        ),
        dimensions=(
            _dimension(
                "counterparty",
                "약정 상대방·대상",
                r"관계기업|종속기업|공동기업|SPC|특수목적|차주|대주단|금융기관|사업시행자|프로젝트회사|상대방",
            ),
            _dimension(
                "underlying_arrangement",
                "기초 거래·사업",
                r"대출약정|PF|프로젝트금융|사업비|원리금|채무|운영자금|상환재원|금융약정",
            ),
            _dimension(
                "amount_limit",
                "한도·대상 금액",
                r"(?:한도|금액|약정액|대출액|원금|보충액)[^\n]{0,50}(?:원|천원|백만원|억원|조원)",
                r"[0-9][0-9,]*(?:\.[0-9]+)?[ ]*(?:원|천원|백만원|억원|조원)",
            ),
            _dimension(
                "trigger",
                "의무 발생 조건",
                r"부족|미달|상환재원|채무불이행|기한이익|조건|경우|발생시|발생 시",
            ),
            _dimension(
                "term",
                "약정 기간·만기",
                r"약정기간|계약기간|기간|만기|종료|20[0-9]{2}년|까지",
            ),
            _dimension(
                "support_method",
                "보충·지원 방법",
                r"출자|대여|지급|이행|보증|지원|보충|후순위|자본금",
            ),
            _dimension(
                "current_exposure",
                "당기말 실행·노출 현황",
                r"당기말|보고기간말|현재|실행|이행|잔액|노출|사용액|미실행|발생하지 않았",
            ),
            _dimension(
                "security_relation",
                "담보·보증과의 관계",
                r"담보|지급보증|연대보증|근질권|질권|채무보증",
            ),
        ),
    ),
    DisclosureTopic(
        key="leases",
        label="리스",
        keywords=("리스", "사용권자산", "리스부채", "임차계약"),
        trigger_patterns=(r"리스|사용권자산|리스부채|임차계약",),
        dimensions=(
            _dimension("scope", "적용 대상·범위", r"사용권자산|기초자산|부동산|차량|장비|임차"),
            _dimension("term", "리스기간 판단", r"리스기간|연장선택권|해지선택권|계약기간"),
            _dimension("discount_rate", "할인율", r"증분차입이자율|할인율|가중평균.*이자율|[0-9]+(?:\.[0-9]+)?%"),
            _dimension("options", "연장·해지 선택권", r"연장선택권|해지선택권|선택권 행사"),
            _dimension("exemptions", "단기·소액 면제", r"단기리스|소액자산|인식면제"),
            _dimension("variable_payments", "변동리스료", r"변동리스료|지수|요율"),
            _dimension("maturity", "만기분석", r"만기분석|1년 이내|1년 초과|5년 초과"),
        ),
    ),
    DisclosureTopic(
        key="impairment",
        label="손상",
        keywords=("손상", "손상차손", "회수가능액", "현금창출단위", "CGU"),
        trigger_patterns=(r"손상|회수가능액|현금창출단위|CGU",),
        dimensions=(
            _dimension("tested_asset", "손상검사 대상", r"영업권|유형자산|무형자산|관계기업|현금창출단위|CGU"),
            _dimension("valuation_method", "회수가능액 산정 방식", r"사용가치|공정가치.*처분부대원가|회수가능액"),
            _dimension("discount_rate", "할인율", r"할인율|세전할인율|가중평균자본비용|WACC|[0-9]+(?:\.[0-9]+)?%"),
            _dimension("growth_rate", "성장률", r"성장률|영구성장률|매출성장"),
            _dimension("assumptions", "주요 가정", r"주요 가정|예산|사업계획|현금흐름 추정"),
            _dimension("sensitivity", "민감도", r"민감도|변동하는 경우|합리적으로 가능한"),
            _dimension("loss_amount", "당기 손상차손", r"손상차손[^\n]{0,60}(?:원|천원|백만원|억원|조원)|손상차손을 인식"),
        ),
    ),
    DisclosureTopic(
        key="provisions_contingencies",
        label="충당부채·우발사항",
        keywords=("충당부채", "우발부채", "우발채무", "약정사항", "소송"),
        trigger_patterns=(r"충당부채|우발부채|우발채무|약정사항|소송",),
        dimensions=(
            _dimension("obligation", "의무의 성격", r"법적의무|의제의무|보증|복구의무|소송|계약상 의무"),
            _dimension("event", "관련 사건·상대방", r"소송|분쟁|계약|고객|정부|거래상대|상대방"),
            _dimension("amount", "예상 금액", r"(?:충당부채|예상액|청구액|소송가액|보증한도)[^\n]{0,60}(?:원|천원|백만원|억원|조원)"),
            _dimension("probability", "발생 가능성", r"가능성이 높|가능성이 낮|개연성|발생가능성|합리적으로 가능"),
            _dimension("timing", "예상 시기", r"예상 시기|지급시기|기간|만기|20[0-9]{2}년"),
            _dimension("uncertainty", "불확실성", r"불확실|추정|결과를 예측|금액을 신뢰성 있게"),
            _dimension("reimbursement", "보상 가능성", r"보험|보상|구상권|제3자로부터"),
            _dimension("movement", "당기 변동", r"기초|전입|사용|환입|기말"),
        ),
    ),
    DisclosureTopic(
        key="revenue",
        label="수익인식",
        keywords=("수익인식", "수행의무", "거래가격", "변동대가", "계약자산", "계약부채"),
        trigger_patterns=(r"수익인식|수행의무|거래가격|변동대가|계약자산|계약부채",),
        dimensions=(
            _dimension("performance_obligation", "수행의무", r"수행의무|재화나 용역"),
            _dimension("timing", "인식 시점", r"한 시점|기간에 걸쳐|통제가 이전|인도 시점"),
            _dimension("variable_consideration", "변동대가", r"변동대가|환불|리베이트|성과보너스"),
            _dimension("principal_agent", "본인·대리인", r"본인|대리인|총액|순액"),
            _dimension("allocation", "거래가격 배분", r"거래가격|개별판매가격|배분"),
            _dimension("contract_balances", "계약자산·계약부채", r"계약자산|계약부채|수취채권"),
        ),
    ),
    DisclosureTopic(
        key="related_parties",
        label="특수관계자",
        keywords=("특수관계자", "관계기업", "주요 경영진", "지급보증"),
        trigger_patterns=(r"특수관계자|주요 경영진|관계기업|지급보증",),
        dimensions=(
            _dimension("relationship", "관계", r"지배기업|종속기업|관계기업|공동기업|주요 경영진"),
            _dimension("transaction_type", "거래 유형", r"매출|매입|대여|차입|용역|배당|자산의 취득|자산의 처분"),
            _dimension("amount", "거래 금액", r"(?:매출|매입|대여|차입|채권|채무)[^\n]{0,60}(?:원|천원|백만원|억원|조원)"),
            _dimension("balance", "기말 잔액", r"채권|채무|대여금|차입금|미수금|미지급금|기말"),
            _dimension("terms", "거래 조건", r"이자율|상환조건|거래조건|정상가격|무담보"),
            _dimension("guarantees", "보증·담보", r"지급보증|담보|채무보증"),
        ),
    ),
    DisclosureTopic(
        key="financial_instruments",
        label="금융상품",
        keywords=("금융상품", "공정가치", "기대신용손실", "신용위험", "유동성위험"),
        trigger_patterns=(r"금융상품|공정가치|기대신용손실|신용위험|유동성위험",),
        dimensions=(
            _dimension("classification", "분류·측정", r"상각후원가|기타포괄손익.*공정가치|당기손익.*공정가치"),
            _dimension("fair_value", "공정가치 수준", r"수준 1|수준 2|수준 3|공정가치 서열체계"),
            _dimension("credit_risk", "신용위험·기대신용손실", r"기대신용손실|신용위험|손실충당금|연체"),
            _dimension("liquidity_risk", "유동성위험", r"유동성위험|계약상 만기|현금흐름"),
            _dimension("market_risk", "시장위험", r"환위험|이자율위험|가격위험|시장위험"),
            _dimension("valuation", "가치평가기법", r"가치평가기법|할인현금흐름|관측가능한 투입변수|비관측"),
        ),
    ),
    DisclosureTopic(
        key="subsidiaries",
        label="종속기업·연결범위",
        keywords=("종속기업", "연결범위", "지배력", "지분율"),
        trigger_patterns=(r"종속기업|연결범위|지배력|지분율",),
        dimensions=(
            _dimension("entity", "대상 기업", r"회사명|법인명|종속기업|소재지"),
            _dimension("ownership", "지분율", r"지분율|소유지분|의결권|[0-9]+(?:\.[0-9]+)?%"),
            _dimension("control_basis", "지배력 판단 근거", r"지배력|의결권|사실상 지배|변동이익"),
            _dimension("changes", "연결범위 변동", r"신규 편입|연결 제외|취득|처분|청산"),
            _dimension("restrictions", "자금이전 제한", r"배당 제한|자금이전|제약|규제"),
        ),
    ),
    DisclosureTopic(
        key="subsequent_events",
        label="후속사건",
        keywords=("후속사건", "보고기간 후 사건", "재무제표 승인일"),
        trigger_patterns=(r"후속사건|보고기간 후 사건|재무제표 승인일",),
        dimensions=(
            _dimension("event", "사건의 내용", r"취득|처분|증자|차입|합병|분할|소송|화재|계약"),
            _dimension("date", "발생일", r"20[0-9]{2}년[ ]*[0-9]{1,2}월|발생일|결정일|계약일"),
            _dimension("financial_effect", "재무적 영향", r"재무적 영향|예상 금액|손익|자산|부채|금액"),
            _dimension("adjustment", "수정·비수정 여부", r"수정후속사건|비수정후속사건|수정사항|조정하지 않았"),
        ),
    ),
    DisclosureTopic(
        key="accounting_policies",
        label="회계정책·중요한 판단",
        keywords=("회계정책", "측정기준", "중요한 판단", "추정 불확실성"),
        trigger_patterns=(r"회계정책|측정기준|중요한 판단|추정 불확실성",),
        dimensions=(
            _dimension("measurement", "측정 기준", r"원가|공정가치|상각후원가|현재가치|측정"),
            _dimension("recognition", "인식 기준", r"인식|제거|분류|표시"),
            _dimension("judgment", "중요한 판단", r"중요한 판단|경영진의 판단|판단을 적용"),
            _dimension("estimates", "추정 불확실성", r"추정 불확실성|가정|추정치|민감도"),
            _dimension("changes", "정책 변경", r"회계정책 변경|소급 적용|전진 적용|변경의 영향"),
        ),
    ),
)

_TOPIC_BY_KEY = {topic.key: topic for topic in _DISCLOSURE_TOPICS}
_TOPIC_ALIASES = {
    "lease": "leases",
    "leases": "leases",
    "impairment": "impairment",
    "provisions": "provisions_contingencies",
    "provisions_contingencies": "provisions_contingencies",
    "revenue": "revenue",
    "financial_instruments": "financial_instruments",
    "related_parties": "related_parties",
    "subsidiaries": "subsidiaries",
    "subsequent_events": "subsequent_events",
    "accounting_policies": "accounting_policies",
    "funding_support": "funding_support",
}


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    with get_session() as managed:
        yield managed


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _normalized_space(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", "\n").split())


def _compact(value: Any) -> str:
    return re.sub(r"[\s\-_.·,()/;:\[\]]+", "", str(value or "").casefold())


def _identity_payload(row: Any) -> str:
    body = str(_value(row, "body", "") or "")
    body_hash = str(
        _value(row, "body_hash")
        or hashlib.sha1(body.encode("utf-8")).hexdigest()
    )
    return "|".join(
        str(part or "")
        for part in (
            _value(row, "id"),
            _value(row, "corp_code"),
            _value(row, "bsns_year"),
            _value(row, "fs_div"),
            _value(row, "rcept_no"),
            _value(row, "note_no"),
            _value(row, "section_type"),
            body_hash,
            _value(row, "full_text_hash"),
        )
    )


def build_note_ref(row: Any) -> str:
    row_id = _value(row, "id")
    if row_id is None:
        raise NoteReferenceError("note_row_id_unavailable")
    digest = hashlib.sha256(
        _identity_payload(row).encode("utf-8")
    ).hexdigest()[:20]
    return f"n1-{int(row_id)}-{digest}"


def note_resource_uris(note_ref: str) -> dict[str, str]:
    if not NOTE_REF_RE.fullmatch(str(note_ref or "")):
        raise NoteReferenceError("invalid_note_reference")
    return {
        "summary": NOTE_SUMMARY_URI_TEMPLATE.format(note_ref=note_ref),
        "paragraph": NOTE_PARAGRAPH_URI_TEMPLATE.format(note_ref=note_ref),
        "full_page": NOTE_PAGE_URI_TEMPLATE.format(note_ref=note_ref, page=1),
    }


def resolve_note_ref(
    note_ref: str,
    *,
    session: Session | None = None,
) -> AccountingNoteChapter:
    match = NOTE_REF_RE.fullmatch(str(note_ref or ""))
    if match is None:
        raise NoteReferenceError("invalid_note_reference")
    row_id = int(note_ref.split("-", 2)[1])
    with _session_scope(session) as active:
        row = active.get(AccountingNoteChapter, row_id)
        if row is None:
            raise NoteReferenceError("note_reference_not_found")
        if build_note_ref(row) != note_ref:
            raise NoteReferenceError("note_reference_stale")
        return row


@dataclass(frozen=True)
class NoteText:
    text: str
    source_basis: Literal[
        "external_full_text",
        "cached_note",
        "cached_excerpt",
        "missing",
    ]
    completeness: Literal["complete", "partial", "missing"]
    expected_length: int | None
    errors: tuple[str, ...]


def load_note_text(
    row: AccountingNoteChapter,
    *,
    include_external: bool,
) -> NoteText:
    body = str(row.body or "")
    expected_length = (
        int(row.full_text_length)
        if row.full_text_length is not None
        else None
    )
    storage_status = str(row.full_text_storage_status or "").lower()
    errors: list[str] = []

    if include_external and row.full_text_uri:
        try:
            external = EvidenceBlobStore().read(
                row.full_text_uri,
                expected_hash=row.full_text_hash,
            )
        except Exception:
            errors.append("external_note_text_read_failed")
        else:
            if external.strip():
                return NoteText(
                    text=external,
                    source_basis="external_full_text",
                    completeness="complete",
                    expected_length=expected_length or len(external),
                    errors=tuple(errors),
                )
            errors.append("external_note_text_blank")

    partial = bool(
        row.full_text_uri
        or storage_status in {"externalized", "truncated", "compressed"}
        or (
            expected_length is not None
            and expected_length > len(body)
        )
    )
    if body:
        return NoteText(
            text=body,
            source_basis=(
                "cached_excerpt" if partial else "cached_note"
            ),
            completeness="partial" if partial else "complete",
            expected_length=expected_length or len(body),
            errors=tuple(errors),
        )
    return NoteText(
        text="",
        source_basis="missing",
        completeness="missing",
        expected_length=expected_length,
        errors=tuple(errors),
    )


def _topic_score(topic: DisclosureTopic, text: str) -> int:
    return sum(
        3 for pattern in topic.trigger_patterns
        if re.search(pattern, text, flags=re.IGNORECASE)
    ) + sum(
        1 for keyword in topic.keywords
        if _compact(keyword) and _compact(keyword) in _compact(text)
    )


def resolve_disclosure_topic(
    *,
    title: str,
    text: str,
    topic_hint: str | None = None,
    query_terms: Sequence[str] | None = None,
) -> DisclosureTopic:
    hint = _TOPIC_ALIASES.get(str(topic_hint or ""))
    combined = f"{title}\n{text}"
    query_text = " ".join(str(term) for term in (query_terms or []))
    funding = _TOPIC_BY_KEY["funding_support"]
    if _topic_score(funding, query_text) > 0:
        return funding
    if hint in _TOPIC_BY_KEY:
        return _TOPIC_BY_KEY[hint]
    scored = [
        (_topic_score(topic, combined), topic)
        for topic in _DISCLOSURE_TOPICS
    ]
    score, selected = max(scored, key=lambda item: item[0])
    if score > 0:
        return selected
    return DisclosureTopic(
        key="general_note",
        label="관련 주석",
        keywords=tuple(str(term) for term in (query_terms or []) if term),
        trigger_patterns=(),
        dimensions=_GENERIC_DIMENSIONS,
    )


def _context_excerpt(
    text: str,
    start: int,
    end: int,
    *,
    before: int = 160,
    after: int = 260,
) -> str:
    left = max(0, start - before)
    right = min(len(text), end + after)
    excerpt = _normalized_space(text[left:right])
    if left:
        excerpt = "… " + excerpt
    if right < len(text):
        excerpt += " …"
    return excerpt


def _dimension_evidence(
    text: str,
    dimension: DisclosureDimension,
) -> dict[str, Any]:
    for pattern in dimension.patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match is not None:
            return {
                "key": dimension.key,
                "label": dimension.label,
                "status": "confirmed",
                "evidence": _context_excerpt(
                    text,
                    match.start(),
                    match.end(),
                ),
            }
    return {
        "key": dimension.key,
        "label": dimension.label,
        "status": "not_observed",
        "evidence": None,
    }


def _split_large_block(block: str, *, limit: int = 4_500) -> list[str]:
    if len(block) <= limit:
        return [block]
    sentences = re.split(r"(?<=[.!?다])\s+|\n", block)
    parts: list[str] = []
    current: list[str] = []
    length = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if current and length + len(sentence) + 1 > limit:
            parts.append(" ".join(current))
            current = []
            length = 0
        current.append(sentence)
        length += len(sentence) + 1
    if current:
        parts.append(" ".join(current))
    return parts or [block[:limit]]


def split_note_paragraphs(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [
        block.strip()
        for block in re.split(r"\n[ \t]*\n+", normalized)
        if block.strip()
    ]
    if not blocks and normalized.strip():
        blocks = [normalized.strip()]
    paragraphs: list[str] = []
    for block in blocks:
        paragraphs.extend(_split_large_block(block))
    return paragraphs


def related_paragraphs(
    text: str,
    *,
    topic: DisclosureTopic,
    query_terms: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    paragraphs = split_note_paragraphs(text)
    literal_terms = [
        str(term).strip()
        for term in [*(query_terms or []), *topic.keywords]
        if str(term).strip()
    ]
    compact_terms = [
        _compact(term) for term in literal_terms if _compact(term)
    ]
    scored: list[tuple[int, int, list[str], str]] = []
    for index, paragraph in enumerate(paragraphs):
        compact_paragraph = _compact(paragraph)
        matched_terms = [
            term for term, compact_term in zip(literal_terms, compact_terms)
            if compact_term in compact_paragraph
        ]
        dimension_hits = sum(
            1
            for dimension in topic.dimensions
            if any(
                re.search(pattern, paragraph, flags=re.IGNORECASE)
                for pattern in dimension.patterns
            )
        )
        score = 5 * len(matched_terms) + dimension_hits
        if re.search(r"[0-9][0-9,]*(?:\.[0-9]+)?[ ]*(?:%|원|천원|백만원|억원|조원)", paragraph):
            score += 1
        if score:
            scored.append((score, index, matched_terms, paragraph))
    selected = sorted(
        sorted(scored, key=lambda item: (-item[0], item[1]))[
            :MAX_RELATED_PARAGRAPHS
        ],
        key=lambda item: item[1],
    )
    output: list[dict[str, Any]] = []
    for _score, index, matched_terms, paragraph in selected:
        truncated = len(paragraph) > MAX_RELATED_PARAGRAPH_CHARACTERS
        text_value = paragraph[:MAX_RELATED_PARAGRAPH_CHARACTERS]
        if truncated:
            text_value += " …"
        output.append({
            "paragraph_index": index,
            "text": text_value,
            "text_length": len(paragraph),
            "truncated": truncated,
            "matched_terms": list(dict.fromkeys(matched_terms))[:10],
        })
    return output


def assess_disclosure_depth(
    *,
    title: str,
    text: NoteText,
    topic_hint: str | None = None,
    query_terms: Sequence[str] | None = None,
    matched_term: str | None = None,
    query_keyword: str | None = None,
) -> dict[str, Any]:
    topic = resolve_disclosure_topic(
        title=title,
        text=text.text,
        topic_hint=topic_hint,
        query_terms=query_terms,
    )
    dimensions = [
        _dimension_evidence(text.text, dimension)
        for dimension in topic.dimensions
    ]
    observed = [
        item for item in dimensions
        if item["status"] == "confirmed"
    ]
    total = len(dimensions)
    ratio = len(observed) / total if total else 0.0

    if text.completeness == "missing" or not observed:
        level = "indeterminate"
    elif ratio >= 0.65:
        level = "detailed"
    elif ratio >= 0.35:
        level = "moderate"
    else:
        level = "brief"

    base_labels = {
        "detailed": "구체적",
        "moderate": "보통",
        "brief": "간략",
        "indeterminate": "판단 불가",
    }
    level_label = base_labels[level]
    if text.completeness == "partial" and level != "indeterminate":
        level_label += " 내용 확인(전체 주석 확인 필요)"

    compact_query = _compact(query_keyword)
    compact_match = _compact(matched_term)
    if compact_match and compact_query and compact_match == compact_query:
        expression_type = "direct"
        expression_label = "직접 표현"
    elif compact_match:
        expression_type = "related"
        expression_label = "유사 표현"
    elif any(_compact(keyword) in _compact(text.text) for keyword in topic.keywords):
        expression_type = "topic_context"
        expression_label = "관련 주제 표현"
    else:
        expression_type = "not_observed"
        expression_label = "표현 판단 불가"

    confidence = (
        "high"
        if text.completeness == "complete" and topic.key != "general_note"
        else "medium"
        if text.completeness == "complete"
        else "low"
    )
    if level == "indeterminate":
        confidence = "low"

    if level == "indeterminate":
        interpretation = (
            "현재 확보된 주석 본문만으로 공시의 구체성을 판단하기 어렵습니다."
        )
    else:
        interpretation = (
            f"현재 본문에서 {total}개 확인 항목 중 {len(observed)}개가 확인됩니다. "
            "이는 문구에 포함된 정보요소를 기준으로 한 비교이며 법적 효력이나 "
            "회계처리 적정성 판단은 아닙니다."
        )
        if text.completeness == "partial":
            interpretation += (
                " 전체 원문이 아닌 일부 본문 기준이므로 미확인 항목을 공시 누락으로 "
                "단정할 수 없습니다."
            )

    return {
        "version": NOTE_EVIDENCE_VERSION,
        "topic": topic.key,
        "topic_label": topic.label,
        "expression_type": expression_type,
        "expression_label": expression_label,
        "level": level,
        "level_label": level_label,
        "assessment_confidence": confidence,
        "assessment_scope": (
            "full_note"
            if text.completeness == "complete"
            else "cached_excerpt"
            if text.completeness == "partial"
            else "unavailable"
        ),
        "observed_dimension_count": len(observed),
        "total_dimension_count": total,
        "observed_ratio_pct": round(100.0 * ratio, 1),
        "observed_items": [item["label"] for item in observed],
        "not_observed_items": [
            item["label"] for item in dimensions
            if item["status"] != "confirmed"
        ],
        "dimensions": dimensions,
        "interpretation": interpretation,
    }


def build_note_evidence(
    row: AccountingNoteChapter,
    *,
    topic_hint: str | None = None,
    query_terms: Sequence[str] | None = None,
    matched_term: str | None = None,
    query_keyword: str | None = None,
    include_external: bool = False,
) -> dict[str, Any]:
    note_ref = build_note_ref(row)
    uris = note_resource_uris(note_ref)
    note_text = load_note_text(
        row,
        include_external=include_external,
    )
    profile = assess_disclosure_depth(
        title=str(row.note_title or ""),
        text=note_text,
        topic_hint=topic_hint,
        query_terms=query_terms,
        matched_term=matched_term,
        query_keyword=query_keyword,
    )
    topic = _TOPIC_BY_KEY.get(profile["topic"])
    if topic is None:
        topic = resolve_disclosure_topic(
            title=str(row.note_title or ""),
            text=note_text.text,
            topic_hint=topic_hint,
            query_terms=query_terms,
        )
    paragraphs = related_paragraphs(
        note_text.text,
        topic=topic,
        query_terms=query_terms,
    )
    related_text = "\n\n".join(
        paragraph["text"] for paragraph in paragraphs
    )[:MAX_RELATED_TEXT_CHARACTERS]
    source_url = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
        f"{row.rcept_no}"
        if re.fullmatch(r"[0-9]{14}", str(row.rcept_no or ""), re.ASCII)
        else None
    )
    return {
        "version": NOTE_EVIDENCE_VERSION,
        "note_ref": note_ref,
        "resources": uris,
        "company": {
            "corp_code": row.corp_code,
        },
        "year": row.bsns_year,
        "fs_div": row.fs_div,
        "rcept_no": row.rcept_no,
        "source_url": source_url,
        "note_no": row.note_no,
        "note_title": row.note_title,
        "section_type": row.section_type,
        "text": {
            "source_basis": note_text.source_basis,
            "completeness": note_text.completeness,
            "returned_length": len(note_text.text),
            "expected_length": note_text.expected_length,
            "errors": list(note_text.errors),
        },
        "related_paragraphs": paragraphs,
        "related_text": related_text,
        "disclosure_profile": profile,
    }


def _chunks(values: Sequence[int], size: int = 500) -> Iterator[list[int]]:
    for index in range(0, len(values), size):
        yield list(values[index:index + size])


def _load_rows(
    note_ids: Iterable[int],
    *,
    session: Session,
) -> dict[int, AccountingNoteChapter]:
    ids = sorted({int(value) for value in note_ids if value is not None})
    rows: dict[int, AccountingNoteChapter] = {}
    for chunk in _chunks(ids):
        for row in (
            session.query(AccountingNoteChapter)
            .filter(AccountingNoteChapter.id.in_(chunk))
            .all()
        ):
            rows[int(row.id)] = row
    return rows


def _source_locator_id(value: Any) -> int | None:
    match = re.fullmatch(
        r"accounting_note_chapters:([0-9]+)",
        str(value or ""),
        re.ASCII,
    )
    return int(match.group(1)) if match else None


def _compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    profile = evidence["disclosure_profile"]
    return {
        "note_ref": evidence["note_ref"],
        "note_resource_uri": evidence["resources"]["summary"],
        "paragraph_resource_uri": evidence["resources"]["paragraph"],
        "full_note_resource_uri": evidence["resources"]["full_page"],
        "text_completeness": evidence["text"]["completeness"],
        "text_source_basis": evidence["text"]["source_basis"],
        "related_paragraph": evidence["related_text"][:1_600],
        "disclosure_level": profile["level"],
        "disclosure_level_label": profile["level_label"],
        "expression_type": profile["expression_type"],
        "expression_label": profile["expression_label"],
        "observed_disclosure_items": profile["observed_items"][:8],
        "disclosure_assessment_scope": profile["assessment_scope"],
        "disclosure_assessment_confidence": profile[
            "assessment_confidence"
        ],
    }


def enrich_note_search_result(
    result: dict[str, Any],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    if not isinstance(result, dict) or "error" in result:
        return result
    query = result.get("query") or {}
    query_keyword = str(query.get("keyword") or "")
    query_terms = [
        str(value)
        for value in (
            query.get("expanded_terms")
            or [query_keyword]
        )
        if value
    ]
    note_ids: list[int] = []
    for company in result.get("companies") or []:
        if not isinstance(company, dict):
            continue
        for record in company.get("records") or []:
            if isinstance(record, dict) and record.get("id") is not None:
                note_ids.append(int(record["id"]))

    with _session_scope(session) as active:
        rows = _load_rows(note_ids, session=active)
        profiled_companies = 0
        profiled_records = 0
        partial_records = 0
        for company in result.get("companies") or []:
            if not isinstance(company, dict):
                continue
            company_profiled = False
            for record_index, record in enumerate(company.get("records") or []):
                if not isinstance(record, dict):
                    continue
                row = rows.get(int(record.get("id") or 0))
                if row is None:
                    continue
                evidence = build_note_evidence(
                    row,
                    query_terms=query_terms,
                    matched_term=str(record.get("matched_term") or ""),
                    query_keyword=query_keyword,
                    include_external=False,
                )
                record.update({
                    "note_ref": evidence["note_ref"],
                    "note_resource_uri": evidence["resources"]["summary"],
                    "paragraph_resource_uri": evidence["resources"]["paragraph"],
                    "full_note_resource_uri": evidence["resources"]["full_page"],
                })
                if (
                    profiled_companies < MAX_PROFILED_SEARCH_COMPANIES
                    and record_index == 0
                ):
                    record.update(_compact_evidence(evidence))
                    profiled_records += 1
                    company_profiled = True
                    if evidence["text"]["completeness"] != "complete":
                        partial_records += 1
            if company_profiled:
                profiled_companies += 1

    enriched = dict(result)
    enriched["note_evidence"] = {
        "version": NOTE_EVIDENCE_VERSION,
        "profiled_company_count": profiled_companies,
        "profiled_record_count": profiled_records,
        "partial_text_record_count": partial_records,
        "full_note_loaded_lazily": True,
    }
    quality = dict(enriched.get("data_quality") or {})
    limitations = list(quality.get("limitations") or [])
    if partial_records:
        limitations.append(
            "some_disclosure_depth_assessments_use_cached_excerpt"
        )
    quality["limitations"] = list(dict.fromkeys(limitations))
    enriched["data_quality"] = quality
    return enriched


def enrich_note_comparison_result(
    result: dict[str, Any],
    *,
    session: Session | None = None,
) -> dict[str, Any]:
    if not isinstance(result, dict) or "error" in result:
        return result
    note_ids: list[int] = []
    company_order: list[str] = []
    subject_code = str((result.get("subject") or {}).get("corp_code") or "")
    if subject_code:
        company_order.append(subject_code)
    for topic in result.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        for row in topic.get("rows") or []:
            if not isinstance(row, dict):
                continue
            note_id = _source_locator_id(row.get("source_locator"))
            if note_id is not None:
                note_ids.append(note_id)
            code = str((row.get("company") or {}).get("corp_code") or "")
            if code and code not in company_order:
                company_order.append(code)
    profiled_codes = set(company_order[:MAX_PROFILED_COMPARISON_COMPANIES])

    summaries: dict[str, dict[str, Any]] = {}
    profiled_rows = 0
    partial_rows = 0
    with _session_scope(session) as active:
        rows = _load_rows(note_ids, session=active)
        for topic in result.get("topics") or []:
            if not isinstance(topic, dict):
                continue
            topic_key = str(topic.get("topic") or "")
            for comparison_row in topic.get("rows") or []:
                if not isinstance(comparison_row, dict):
                    continue
                company = comparison_row.get("company") or {}
                code = str(company.get("corp_code") or "")
                note_id = _source_locator_id(
                    comparison_row.get("source_locator")
                )
                row = rows.get(note_id or 0)
                if row is None:
                    continue
                note_ref = build_note_ref(row)
                uris = note_resource_uris(note_ref)
                comparison_row.update({
                    "note_ref": note_ref,
                    "note_resource_uri": uris["summary"],
                    "paragraph_resource_uri": uris["paragraph"],
                    "full_note_resource_uri": uris["full_page"],
                })
                if code not in profiled_codes:
                    continue
                evidence = build_note_evidence(
                    row,
                    topic_hint=topic_key,
                    include_external=False,
                )
                comparison_row.update(_compact_evidence(evidence))
                profiled_rows += 1
                if evidence["text"]["completeness"] != "complete":
                    partial_rows += 1
                profile = evidence["disclosure_profile"]
                summary = summaries.setdefault(code, {
                    "corp_code": code,
                    "corp_name": company.get("corp_name") or code,
                    "topic_count": 0,
                    "detailed_topics": [],
                    "moderate_topics": [],
                    "brief_topics": [],
                    "indeterminate_topics": [],
                    "observed_items": [],
                    "note_actions": [],
                })
                summary["topic_count"] += 1
                summary[f"{profile['level']}_topics"].append(
                    profile["topic_label"]
                )
                for label in profile["observed_items"]:
                    if label not in summary["observed_items"]:
                        summary["observed_items"].append(label)
                summary["note_actions"].append({
                    "topic": profile["topic_label"],
                    "note_ref": evidence["note_ref"],
                    "paragraph_resource_uri": evidence["resources"]["paragraph"],
                    "full_note_resource_uri": evidence["resources"]["full_page"],
                    "rcept_no": evidence["rcept_no"],
                    "source_url": evidence["source_url"],
                })

    for summary in summaries.values():
        levels = (
            [("구체적", len(summary["detailed_topics"])),
             ("보통", len(summary["moderate_topics"])),
             ("간략", len(summary["brief_topics"])),
             ("판단 불가", len(summary["indeterminate_topics"]))]
        )
        summary["overall_level_label"] = max(
            levels,
            key=lambda item: item[1],
        )[0] if summary["topic_count"] else "판단 불가"
        summary["observed_items"] = summary["observed_items"][:10]
        summary["note_actions"] = summary["note_actions"][:10]

    enriched = dict(result)
    enriched["disclosure_depth_by_company"] = list(summaries.values())
    enriched["note_evidence"] = {
        "version": NOTE_EVIDENCE_VERSION,
        "profiled_company_count": len(summaries),
        "profiled_row_count": profiled_rows,
        "partial_text_row_count": partial_rows,
        "full_note_loaded_lazily": True,
    }
    quality = dict(enriched.get("data_quality") or {})
    limitations = list(quality.get("limitations") or [])
    if partial_rows:
        limitations.append(
            "some_disclosure_depth_assessments_use_cached_excerpt"
        )
    quality["limitations"] = list(dict.fromkeys(limitations))
    enriched["data_quality"] = quality
    return enriched


def _page_count(text: str) -> int:
    return max(1, math.ceil(len(text) / NOTE_PAGE_CHARACTERS))


def read_note_resource(
    note_ref: str,
    *,
    view: Literal["summary", "paragraph", "page"] = "summary",
    page: int = 1,
    session: Session | None = None,
) -> dict[str, Any]:
    with _session_scope(session) as active:
        row = resolve_note_ref(note_ref, session=active)
        company = active.get(Company, row.corp_code)
        evidence = build_note_evidence(
            row,
            include_external=True,
        )

    note_text = load_note_text(row, include_external=True)
    pages = _page_count(note_text.text)
    page = max(1, int(page))
    if page > pages:
        raise NoteReferenceError("note_page_out_of_range")
    start = (page - 1) * NOTE_PAGE_CHARACTERS
    end = min(len(note_text.text), start + NOTE_PAGE_CHARACTERS)
    page_text = note_text.text[start:end]
    if start:
        page_text = "… " + page_text
    if end < len(note_text.text):
        page_text += " …"

    uris = evidence["resources"]
    payload: dict[str, Any] = {
        "resource_version": NOTE_EVIDENCE_VERSION,
        "view": view,
        "note_ref": note_ref,
        "company": {
            "corp_code": row.corp_code,
            "corp_name": (
                company.corp_name if company is not None else None
            ),
            "stock_code": (
                company.stock_code if company is not None else None
            ),
        },
        "year": row.bsns_year,
        "fs_div": row.fs_div,
        "note_no": row.note_no,
        "note_title": row.note_title,
        "section_type": row.section_type,
        "rcept_no": row.rcept_no,
        "source_url": evidence["source_url"],
        "text_status": evidence["text"],
        "disclosure_profile": evidence["disclosure_profile"],
        "resources": uris,
        "page": {
            "number": page,
            "count": pages,
            "page_size_characters": NOTE_PAGE_CHARACTERS,
            "start_character": start,
            "end_character": end,
            "returned_length": len(page_text),
            "has_previous": page > 1,
            "has_next": page < pages,
            "previous_uri": (
                NOTE_PAGE_URI_TEMPLATE.format(
                    note_ref=note_ref,
                    page=page - 1,
                )
                if page > 1
                else None
            ),
            "next_uri": (
                NOTE_PAGE_URI_TEMPLATE.format(
                    note_ref=note_ref,
                    page=page + 1,
                )
                if page < pages
                else None
            ),
        },
        "data_quality": {
            "status": (
                "usable"
                if note_text.completeness == "complete"
                and not note_text.errors
                else "limited"
                if note_text.text
                else "missing"
            ),
            "limitations": [
                *note_text.errors,
                *(
                    [
                        "현재 확보된 주석 본문은 전체 원문보다 짧을 수 있으므로 "
                        "미확인 항목을 공시 누락으로 단정할 수 없습니다."
                    ]
                    if note_text.completeness == "partial"
                    else []
                ),
            ],
        },
    }
    if view == "summary":
        payload["related_paragraphs"] = evidence[
            "related_paragraphs"
        ]
        payload["text_preview"] = page_text[:2_000]
    elif view == "paragraph":
        payload["related_paragraphs"] = evidence[
            "related_paragraphs"
        ]
        payload["text"] = evidence["related_text"]
    elif view == "page":
        payload["text"] = page_text
    else:  # pragma: no cover - Literal and resource parser constrain this
        raise NoteReferenceError("invalid_note_resource_view")
    return payload


__all__ = [
    "NOTE_EVIDENCE_VERSION",
    "NOTE_PAGE_URI_TEMPLATE",
    "NOTE_PARAGRAPH_URI_TEMPLATE",
    "NOTE_REF_RE",
    "NOTE_REF_TOKEN_PATTERN",
    "NOTE_SUMMARY_URI_TEMPLATE",
    "NoteReferenceError",
    "assess_disclosure_depth",
    "build_note_evidence",
    "build_note_ref",
    "enrich_note_comparison_result",
    "enrich_note_search_result",
    "load_note_text",
    "note_resource_uris",
    "read_note_resource",
    "related_paragraphs",
    "resolve_note_ref",
    "split_note_paragraphs",
]
