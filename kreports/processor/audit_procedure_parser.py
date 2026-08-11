"""Structured procedure extraction from reconstructed KAM items."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json
import re

from sqlalchemy import and_, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, KamItem
from kreports.processor.kam_parser import ParsedKamItem
from kreports.runtime import require_runtime_write


PARSER_VERSION = "audit_procedure.v1"
MAX_INPUT_CHARS = 120_000
MAX_PROCEDURE_STEPS = 64
ASSERTION_VOCABULARY = (
    "existence",
    "occurrence",
    "completeness",
    "accuracy",
    "valuation",
    "rights_obligations",
    "cutoff",
    "presentation",
)

_METHOD_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("specialist_involvement", ("전문가", "specialist")),
    ("it_control_test", ("it 일반통제", "정보기술 일반통제", "itgc")),
    (
        "controls_test",
        (
            "통제의 운영효과성",
            "통제 운영효과성",
            "통제의 설계 및 운영의 효과성",
            "설계 및 운영의 효과성을 테스트",
            "통제 테스트",
            "test of control",
        ),
    ),
    ("cutoff_test", ("기간귀속", "cut-off", "cutoff", "컷오프")),
    (
        "valuation_model_test",
        (
            "할인모형",
            "가치평가모형",
            "가치평가 모델",
            "평가모형",
            "valuation model",
            "모델 검증",
        ),
    ),
    ("analytical_procedure", ("분석적 절차", "추세 분석", "analytical procedure")),
    ("sampling", ("표본", "샘플", "sample")),
    ("reperformance", ("재수행", "reperform")),
    ("recalculation", ("재계산", "recalculate")),
    ("confirmation", ("외부조회", "조회서", "확인서", "confirmation")),
    ("observation", ("입회", "관찰", "observe")),
    ("inquiry", ("질문", "문의", "inquir")),
    (
        "inspection",
        ("검사", "문서검토", "계약서 검토", "증빙 검토", "inspect"),
    ),
)
PROCEDURE_METHODS = tuple(name for name, _patterns in _METHOD_PATTERNS) + (
    "other",
)

_ACTION_SIGNALS = (
    "검사",
    "검토",
    "질문",
    "문의",
    "입회",
    "관찰",
    "조회",
    "발송",
    "재계산",
    "재수행",
    "분석",
    "테스트",
    "평가",
    "검증",
    "추출",
    "활용",
    "대사",
    "조사",
    "확인",
    "비교",
    "수행",
    "inspect",
    "inquir",
    "observ",
    "confirm",
    "recalcul",
    "reperform",
    "analys",
    "test",
    "evaluate",
    "sample",
)
_BULLET_NOUN_ACTION_ENDINGS = (
    "검사",
    "검토",
    "테스트",
    "대사",
    "재수행",
    "재계산",
    "확인",
    "조회",
    "관찰",
    "입회",
    "질문",
    "문의",
    "분석",
    "수행",
    "평가",
    "검증",
    "비교",
)

_RESPONSIBILITY_BOILERPLATE = (
    "감사인의 책임",
    "감사인은 전문가적 판단",
    "전문가적 의구심을 유지할 책임",
    "합리적인 확신을 얻도록",
    "감사기준에 따라 감사를 수행",
    "our responsibility",
    "professional skepticism",
    "책임이 있습니다",
    "포함될 수 있습니다",
    "responsible for",
    "in accordance with",
    "왜곡표시위험을 식별",
    "감사절차를 설계",
)
_GENERIC_PROCEDURE_LEAD_IN = re.compile(
    r"(?:다음(?:을)? 포함한|다음의)\s*감사절차(?:를|는)?\s*"
    r"수행(?:하였|했|합니다|하였다)"
)
_AUDIT_PROCEDURE_LIST_LEAD_IN = re.compile(
    r"(?:주요(?:한)?\s*)?감사절차\s*(?:는|를)?\s*"
    r"다음(?:과\s*)?같(?:습니다|다|은)"
)

_ASSERTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "existence": ("실재", "실사", "잔액 확인"),
    "occurrence": ("발생", "계약서", "거래 검사", "매출"),
    "completeness": ("완전성", "누락", "모집단"),
    "accuracy": ("정확성", "재계산", "대사", "계산"),
    "valuation": ("평가", "공정가치", "손상", "할인율", "모형"),
    "rights_obligations": ("권리", "의무", "소유권"),
    "cutoff": ("기간귀속", "기말 전후", "cut-off", "cutoff", "컷오프"),
    "presentation": ("표시", "공시", "분류"),
}

_LEGACY_PROCEDURE_TYPE = {
    "confirmation": "external_confirmation",
    "analytical_procedure": "analytics",
    "cutoff_test": "cutoff",
    "valuation_model_test": "estimation_assumption",
    "controls_test": "internal_control",
    "it_control_test": "internal_control",
    "specialist_involvement": "valuation_specialist",
    "other": "other",
}


@dataclass(frozen=True)
class ParsedProcedureStep:
    ordinal: int
    procedure_text: str
    method: str
    assertion_hints: tuple[str, ...]
    source_start: int
    source_end: int
    source_kam_ordinal: int
    source_kam_hash: str
    procedure_hash: str
    source_kam_topic: str | None = None
    linked_metric_keys: tuple[str, ...] = ()
    linked_note_keys: tuple[str, ...] = ()
    linked_event_keys: tuple[str, ...] = ()
    parser_version: str = PARSER_VERSION
    quality_status: str = "full_body"


def legacy_procedure_type(method: str) -> str:
    return _LEGACY_PROCEDURE_TYPE.get(method, "substantive_test")


def _normalize_clause(value: str) -> str:
    value = re.sub(
        r"^\s*(?:(?:[-•·▪◦ㆍ])\s*|[①-⑳]\s*|\(?\d{1,3}\)?[.)]\s*)",
        "",
        value,
    )
    return re.sub(r"\s+", " ", value).strip()


def _candidate_clauses(source: str) -> list[tuple[str, int, int, bool, bool]]:
    bounded = (source or "")[:MAX_INPUT_CHARS]
    if any(marker in bounded.lower() for marker in _RESPONSIBILITY_BOILERPLATE):
        return []
    boundaries = [
        r"\r?\n+",
        r"[;；]+",
        r"(?<=[.!?。])\s+",
        r"(?<=[.!?。])(?=\s*[-•·▪◦ㆍ①-⑳])",
        r"(?:(?<![\s\S])(?=ㆍ)|(?<=[\s.!?。])(?=ㆍ)|(?=ㆍ\s+))",
        r"(?=[①-⑳])",
        r"(?<=\s)(?=[•·▪◦]\s+)",
        r"(?<=\s)(?=-\s+)",
    ]
    if _AUDIT_PROCEDURE_LIST_LEAD_IN.search(bounded):
        boundaries.append(r"(?<=\S)(?=-\s*\S)")
    pattern = re.compile("(?:" + "|".join(boundaries) + ")")
    clauses: list[tuple[str, int, int, bool, bool]] = []

    def append_raw(
        raw: str,
        absolute_start: int,
        *,
        pending_bullet_context: bool = False,
    ) -> bool:
        if re.fullmatch(r"\s*(?:(?:[-•·▪◦ㆍ])\s*|[①-⑳]\s*)", raw):
            return True
        lowered_raw = raw.lower()
        if any(marker in lowered_raw for marker in _RESPONSIBILITY_BOILERPLATE):
            return False
        bullet_context = bool(
            re.match(r"^\s*(?:(?:[-•·▪◦ㆍ])\s*|[①-⑳]\s*)", raw)
        ) or pending_bullet_context
        conjunction = re.compile(
            r"((?:검사|검토|질문|문의|입회|관찰|조회|재계산|재수행|"
            r"분석|테스트|평가|추출|활용|대사|조사|확인)"
            r"(하여|하고|한\s+뒤|하였으며|했으며)),?\s+"
        )

        def append_piece(
            piece: str,
            piece_start: int,
            piece_end: int,
            *,
            performed_context: bool = False,
        ) -> None:
            parts = [
                value.strip()
                for value in re.split(r"\s*(?:,|및)\s*", piece)
                if value.strip()
            ]
            recognized = sum(_method(value) != "other" for value in parts)
            is_action_list = (
                len(parts) >= 2
                and recognized >= 2
                and bool(
                    re.search(
                        r"(?:수행|실시|검사|확인|테스트|평가)"
                        r"(?:하였습니다|했습니다|하였다|했다)"
                        r"[.!?。]?$",
                        piece,
                    )
                )
            )
            if not is_action_list:
                normalized = _normalize_clause(piece)
                if normalized:
                    clauses.append(
                        (
                            normalized,
                            piece_start,
                            piece_end,
                            performed_context,
                            bullet_context,
                        )
                    )
                return
            cursor = 0
            for part in parts:
                local = piece.find(part, cursor)
                cursor = local + len(part)
                clauses.append(
                    (
                        _normalize_clause(part),
                        piece_start + local,
                        piece_start + local + len(part),
                        True,
                        bullet_context,
                    )
                )

        local_start = 0
        for boundary in conjunction.finditer(raw):
            local_end = boundary.end(1)
            piece = raw[local_start:local_end]
            left_method = _method(piece)
            right_method = _method(raw[boundary.end():])
            if (
                boundary.group(2) == "하여"
                and (
                    left_method in {"other", "sampling"}
                    or right_method == "other"
                )
            ):
                continue
            append_piece(
                piece,
                absolute_start + local_start,
                absolute_start + local_end,
                performed_context=True,
            )
            local_start = boundary.end()
        piece = raw[local_start:]
        append_piece(
            piece,
            absolute_start + local_start,
            absolute_start + len(raw),
        )
        return False

    start = 0
    pending_bullet_context = False
    for match in pattern.finditer(bounded):
        pending_bullet_context = append_raw(
            bounded[start:match.start()],
            start,
            pending_bullet_context=pending_bullet_context,
        )
        start = match.end()
    append_raw(
        bounded[start:],
        start,
        pending_bullet_context=pending_bullet_context,
    )
    return clauses


def _is_action_clause(
    clause: str,
    *,
    compound_context: bool = False,
    bullet_context: bool = False,
) -> bool:
    lowered = clause.lower()
    if any(marker in lowered for marker in _RESPONSIBILITY_BOILERPLATE):
        return False
    if _GENERIC_PROCEDURE_LEAD_IN.search(clause):
        return False
    if not any(signal in lowered for signal in _ACTION_SIGNALS):
        return False
    if bullet_context:
        noun_ending = re.sub(r"[.!?。]+$", "", lowered).strip()
        if any(noun_ending.endswith(ending) for ending in _BULLET_NOUN_ACTION_ENDINGS):
            return True
    if compound_context:
        return _method(clause) != "other" or bool(
            re.search(r"(?:하였|했|합니다|하였다)", clause)
        )
    return bool(
        re.search(
            r"(?:(?:검사|검토|질문|문의|입회|관찰|조회|재계산|재수행|"
            r"분석|테스트|평가|검증|추출|활용|대사|조사|확인|비교|발송|"
            r"수행|실시)"
            r"(?:하였습니다|했습니다|하였다|했다|하였으며|했으며|"
            r"하고|한\s+뒤|하여|합니다)|"
            r"inspect(?:ed)?|inquir(?:ed)?|observ(?:ed)?|confirm(?:ed)?|"
            r"test(?:ed)?|evaluat(?:ed)?|compar(?:ed)?)",
            lowered,
        )
    )


def _method(clause: str) -> str:
    lowered = clause.lower()
    for method, patterns in _METHOD_PATTERNS:
        if any(pattern in lowered for pattern in patterns):
            return method
    return "other"


def _assertion_hints(clause: str) -> tuple[str, ...]:
    lowered = clause.lower()
    return tuple(
        assertion
        for assertion in ASSERTION_VOCABULARY
        if any(keyword in lowered for keyword in _ASSERTION_KEYWORDS[assertion])
    )


def _stable_hash(kam_item: ParsedKamItem, ordinal: int, clause: str) -> str:
    payload = "\x1f".join(
        (
            kam_item.full_body_hash,
            str(kam_item.ordinal),
            str(ordinal),
            clause,
        )
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def extract_procedure_steps(kam_item: ParsedKamItem) -> list[ParsedProcedureStep]:
    if kam_item.quality_status != "full_body":
        return []
    source = kam_item.audit_response_text or ""
    if not source.strip():
        return []

    steps: list[ParsedProcedureStep] = []
    seen_text: set[str] = set()
    for (
        clause,
        source_start,
        source_end,
        compound_context,
        bullet_context,
    ) in _candidate_clauses(source):
        normalized_key = re.sub(r"\s+", "", clause).lower()
        if normalized_key in seen_text or not _is_action_clause(
            clause,
            compound_context=compound_context,
            bullet_context=bullet_context,
        ):
            continue
        seen_text.add(normalized_key)
        ordinal = len(steps) + 1
        step = ParsedProcedureStep(
            ordinal=ordinal,
            procedure_text=clause,
            method=_method(clause),
            assertion_hints=_assertion_hints(clause),
            source_start=source_start,
            source_end=source_end,
            source_kam_ordinal=kam_item.ordinal,
            source_kam_hash=kam_item.full_body_hash,
            source_kam_topic=kam_item.normalized_topic,
            procedure_hash=_stable_hash(kam_item, ordinal, clause),
        )
        from kreports.analysis.audit_procedure_evidence import (
            link_procedure_evidence,
        )
        from kreports.semantic.metrics import METRICS

        links = link_procedure_evidence(step, METRICS)
        step = replace(
            step,
            linked_metric_keys=tuple(
                link.key for link in links if link.category == "metric"
            ),
            linked_note_keys=tuple(
                link.key for link in links if link.category == "note"
            ),
            linked_event_keys=tuple(
                link.key for link in links if link.category == "event"
            ),
        )
        steps.append(step)
        if len(steps) >= MAX_PROCEDURE_STEPS:
            break
    return steps


def _procedure_identity_filter(
    *,
    rcept_no: str,
    source_type: str,
    section_ordinal: int,
):
    return and_(
        AuditProcedureItem.rcept_no == rcept_no,
        AuditProcedureItem.source_type == source_type,
        AuditProcedureItem.section_ordinal == int(section_ordinal),
    )


def reconcile_procedure_steps_for_identity(
    *,
    rcept_no: str,
    source_type: str,
    section_ordinal: int,
) -> int:
    """Reconcile one logical KAM identity, including downgrade/deletion."""
    require_runtime_write("reconcile structured audit procedure steps")
    with get_session() as session:
        stored = (
            session.query(KamItem)
            .filter(
                KamItem.rcept_no == rcept_no,
                KamItem.source_type == source_type,
                KamItem.ordinal == int(section_ordinal),
            )
            .order_by(KamItem.fetched_at.desc(), KamItem.id.desc())
            .first()
        )
        if stored is None:
            (
                session.query(AuditProcedureItem)
                .filter(
                    _procedure_identity_filter(
                        rcept_no=rcept_no,
                        source_type=source_type,
                        section_ordinal=section_ordinal,
                    )
                )
                .delete(synchronize_session=False)
            )
            return 0
        stored_id = int(stored.id)
    return replace_procedure_steps_for_kam(stored_id)


def replace_procedure_steps_for_kam(kam_item_id: int) -> int:
    require_runtime_write("persist structured audit procedure steps")
    with get_session() as session:
        stored = session.get(KamItem, int(kam_item_id))
        if stored is None:
            (
                session.query(AuditProcedureItem)
                .filter(AuditProcedureItem.kam_item_id == int(kam_item_id))
                .delete(synchronize_session=False)
            )
            return 0
        identity_filter = _procedure_identity_filter(
            rcept_no=stored.rcept_no,
            source_type=stored.source_type,
            section_ordinal=stored.ordinal,
        )
        stale_sibling_ids = [
            int(row[0])
            for row in (
                session.query(KamItem.id)
                .filter(
                    KamItem.rcept_no == stored.rcept_no,
                    KamItem.source_type == stored.source_type,
                    KamItem.ordinal == stored.ordinal,
                    KamItem.id != stored.id,
                )
                .all()
            )
        ]
        if stale_sibling_ids:
            (
                session.query(AuditProcedureItem)
                .filter(AuditProcedureItem.kam_item_id.in_(stale_sibling_ids))
                .delete(synchronize_session=False)
            )
        parsed = ParsedKamItem(
            ordinal=stored.ordinal,
            title=stored.title or "",
            normalized_topic=stored.normalized_topic,
            reason_text=stored.reason_text,
            audit_response_text=stored.audit_response_text,
            related_note_references=json.loads(
                stored.related_note_references_json or "[]"
            ),
            full_body="\n".join(
                part
                for part in (
                    stored.title,
                    stored.reason_text,
                    stored.audit_response_text,
                )
                if part
            ),
            full_body_hash=stored.full_body_hash,
            full_body_length=stored.full_body_length,
            quality_status=stored.quality_status,
            parser_version=stored.parser_version,
        )
        steps = extract_procedure_steps(parsed)
        if stored.quality_status != "full_body":
            (
                session.query(AuditProcedureItem)
                .filter(identity_filter)
                .delete(synchronize_session=False)
            )
            return 0

        now = stored.fetched_at or datetime.utcnow()
        rows = [
            {
                "kam_item_id": stored.id,
                "rcept_no": stored.rcept_no,
                "dcm_no": stored.dcm_no,
                "corp_code": stored.corp_code,
                "bsns_year": stored.bsns_year,
                "source_type": stored.source_type,
                "kam_topic": stored.normalized_topic,
                "method": step.method,
                "procedure_type": legacy_procedure_type(step.method),
                "procedure_text": step.procedure_text,
                "procedure_hash": step.procedure_hash,
                "procedure_length": len(step.procedure_text),
                "assertion_hints_json": json.dumps(
                    step.assertion_hints,
                    ensure_ascii=False,
                ),
                "linked_metric_keys_json": json.dumps(
                    step.linked_metric_keys,
                    ensure_ascii=False,
                ),
                "linked_note_keys_json": json.dumps(
                    step.linked_note_keys,
                    ensure_ascii=False,
                ),
                "linked_event_keys_json": json.dumps(
                    step.linked_event_keys,
                    ensure_ascii=False,
                ),
                "parser_version": step.parser_version,
                "quality_status": step.quality_status,
                "section_ordinal": stored.ordinal,
                "procedure_ordinal": step.ordinal,
                "fetched_at": now,
            }
            for step in steps
        ]
        if rows:
            stmt = sqlite_insert(AuditProcedureItem).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    "rcept_no",
                    "source_type",
                    "section_ordinal",
                    "procedure_ordinal",
                ],
                set_={
                    "kam_item_id": stmt.excluded.kam_item_id,
                    "dcm_no": stmt.excluded.dcm_no,
                    "corp_code": stmt.excluded.corp_code,
                    "bsns_year": stmt.excluded.bsns_year,
                    "kam_topic": stmt.excluded.kam_topic,
                    "method": stmt.excluded.method,
                    "procedure_type": stmt.excluded.procedure_type,
                    "procedure_text": stmt.excluded.procedure_text,
                    "procedure_hash": stmt.excluded.procedure_hash,
                    "procedure_length": stmt.excluded.procedure_length,
                    "assertion_hints_json": stmt.excluded.assertion_hints_json,
                    "linked_metric_keys_json": (
                        stmt.excluded.linked_metric_keys_json
                    ),
                    "linked_note_keys_json": stmt.excluded.linked_note_keys_json,
                    "linked_event_keys_json": (
                        stmt.excluded.linked_event_keys_json
                    ),
                    "parser_version": stmt.excluded.parser_version,
                    "quality_status": stmt.excluded.quality_status,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            session.execute(stmt)
            current = or_(
                *[
                    and_(
                        AuditProcedureItem.procedure_ordinal
                        == row["procedure_ordinal"],
                        AuditProcedureItem.procedure_hash
                        == row["procedure_hash"],
                    )
                    for row in rows
                ]
            )
            (
                session.query(AuditProcedureItem)
                .filter(
                    identity_filter,
                    ~current,
                )
                .delete(synchronize_session=False)
            )
        else:
            (
                session.query(AuditProcedureItem)
                .filter(identity_filter)
                .delete(synchronize_session=False)
            )
        return len(rows)
