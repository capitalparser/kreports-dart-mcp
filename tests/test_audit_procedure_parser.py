from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from kreports.db.engine import get_session
from kreports.db.models import AuditProcedureItem, KamItem
from kreports.processor.audit_procedure_parser import (
    MAX_PROCEDURE_STEPS,
    extract_procedure_steps,
    replace_procedure_steps_for_kam,
)
from kreports.processor.kam_parser import ParsedKamItem


def _kam_item(response: str, *, quality_status: str = "full_body") -> ParsedKamItem:
    body = f"수익인식\n감사에서 다루어진 방법\n{response}"
    return ParsedKamItem(
        ordinal=2,
        title="수익인식",
        normalized_topic="revenue",
        reason_text="기간귀속 판단에 유의적인 위험이 있습니다.",
        audit_response_text=response,
        related_note_references=["주석 2"],
        full_body=body,
        full_body_hash="a" * 40,
        full_body_length=len(body),
        quality_status=quality_status,
    )


@pytest.mark.parametrize(
    ("text_value", "expected"),
    [
        ("주요 계약서를 검사하였습니다.", "inspection"),
        ("담당자에게 질문하였습니다.", "inquiry"),
        ("재고실사를 입회하였습니다.", "observation"),
        ("거래처에 외부조회서를 발송하였습니다.", "confirmation"),
        ("감가상각비를 재계산하였습니다.", "recalculation"),
        ("통제의 수행을 재수행하였습니다.", "reperformance"),
        ("월별 매출 추세에 분석적 절차를 수행하였습니다.", "analytical_procedure"),
        ("기말 전후 매출의 기간귀속 테스트를 수행하였습니다.", "cutoff_test"),
        ("현금흐름 할인모형을 평가하였습니다.", "valuation_model_test"),
        ("매출 통제의 운영효과성을 테스트하였습니다.", "controls_test"),
        ("IT 일반통제를 테스트하였습니다.", "it_control_test"),
        ("매출 거래 표본을 추출하여 검사하였습니다.", "sampling"),
        ("가치평가 전문가를 활용하였습니다.", "specialist_involvement"),
    ],
)
def test_extract_procedure_steps_classifies_required_methods(text_value, expected):
    steps = extract_procedure_steps(_kam_item(text_value))

    assert [step.method for step in steps] == [expected]


def test_extract_procedure_steps_splits_actions_and_has_stable_identity():
    item = _kam_item(
        "주요 계약서를 검사하였습니다.\n"
        "관련 통제의 수행을 재수행하였습니다.\n"
        "기말 전후 매출의 기간귀속 테스트를 수행하였습니다."
    )

    first = extract_procedure_steps(item)
    second = extract_procedure_steps(item)

    assert [step.method for step in first] == [
        "inspection",
        "reperformance",
        "cutoff_test",
    ]
    assert [step.procedure_hash for step in first] == [
        step.procedure_hash for step in second
    ]
    assert first[0].procedure_text.startswith("주요 계약서를 검사")
    assert "revenue" in first[2].linked_metric_keys
    assert [step.source_start for step in first] == sorted(
        step.source_start for step in first
    )


def test_extract_procedure_steps_splits_conjoined_distinct_actions():
    steps = extract_procedure_steps(
        _kam_item(
            "주요 계약서를 검사하고, 매출 통제의 운영효과성을 "
            "테스트하였습니다."
        )
    )

    assert [step.method for step in steps] == ["inspection", "controls_test"]


def test_extract_procedure_steps_rejects_generic_auditor_responsibility():
    item = _kam_item(
        "감사인은 전문가적 판단을 적용하고 감사 전반에 걸쳐 전문가적 "
        "의구심을 유지할 책임이 있습니다."
    )

    assert extract_procedure_steps(item) == []


@pytest.mark.parametrize(
    "text_value",
    [
        "감사인은 재무제표에 대한 감사를 수행할 책임이 있습니다.",
        "감사절차에는 질문, 검사, 관찰 및 확인이 포함될 수 있습니다.",
        "계약서 검토",
        "내부통제 확인",
        "We are responsible for performing the audit in accordance with standards.",
    ],
)
def test_extract_procedure_steps_rejects_boilerplate_and_noun_only_phrases(
    text_value,
):
    assert extract_procedure_steps(_kam_item(text_value)) == []


def test_extract_procedure_steps_rejects_planning_responsibility_and_action_list():
    text_value = (
        "감사인은 중요한 왜곡표시위험을 식별하고 평가하며, "
        "이에 대응하는 감사절차를 설계하고 수행합니다.\n"
        "감사계획에는 표본, 검사 및 확인 항목을 포함하였습니다."
    )

    assert extract_procedure_steps(_kam_item(text_value)) == []


def test_extract_procedure_steps_rejects_generic_bulleted_procedure_lead_in():
    item = _kam_item(
        "핵심감사사항에 대응하기 위하여 우리는 다음을 포함한 감사절차를 "
        "수행하였습니다.\n"
        "- 주요 계약서를 검사하였습니다."
    )

    steps = extract_procedure_steps(item)

    assert [step.procedure_text for step in steps] == ["주요 계약서를 검사하였습니다."]


def test_extract_procedure_steps_keeps_explicit_bullet_understanding_step():
    item = _kam_item(
        "ㆍ수출매출 정책의 검토 및 프로세스와 내부통제의 이해\n"
        "ㆍ수출매출 기간귀속 내부통제의 설계 및 운영의 효과성 평가"
    )

    steps = extract_procedure_steps(item)

    assert [step.procedure_text for step in steps] == [
        "수출매출 정책의 검토 및 프로세스와 내부통제의 이해",
        "수출매출 기간귀속 내부통제의 설계 및 운영의 효과성 평가",
    ]


def test_extract_procedure_steps_keeps_explicit_bullet_specialist_use_step():
    steps = extract_procedure_steps(
        _kam_item("ㆍ감사인 내부의 가치평가 전문가를 활용")
    )

    assert [step.procedure_text for step in steps] == [
        "감사인 내부의 가치평가 전문가를 활용"
    ]
    assert [step.method for step in steps] == ["specialist_involvement"]


@pytest.mark.parametrize(
    ("text_value", "expected_texts", "expected_methods"),
    [
        (
            "우리가 수행한 주요 감사절차는 다음과 같습니다.ㆍ 계약서 검토"
            "ㆍ 통제 테스트ㆍ 분석적 절차 수행",
            ["계약서 검토", "통제 테스트", "분석적 절차 수행"],
            ["inspection", "controls_test", "analytical_procedure"],
        ),
        (
            "우리가 수행한 주요 감사절차는 다음과 같습니다.① 계약서 검토"
            "② 외부조회 확인",
            ["계약서 검토", "외부조회 확인"],
            ["inspection", "confirmation"],
        ),
        (
            "우리가 수행한 주요 감사절차는 다음과 같습니다.- 계약서 검토"
            " - 외부조회 확인",
            ["계약서 검토", "외부조회 확인"],
            ["inspection", "confirmation"],
        ),
        (
            "우리가 수행한 주요 감사절차는 다음과 같습니다.－ 계약서 검토"
            " － 외부조회 확인",
            ["계약서 검토", "외부조회 확인"],
            ["inspection", "confirmation"],
        ),
    ],
)
def test_extract_procedure_steps_preserves_inline_explicit_list_provenance(
    text_value,
    expected_texts,
    expected_methods,
):
    """Catch inline list markers being collapsed before noun-ending steps are parsed."""
    steps = extract_procedure_steps(_kam_item(text_value))

    assert [step.procedure_text for step in steps] == expected_texts
    assert [step.method for step in steps] == expected_methods


def test_extract_procedure_steps_preserves_standalone_bullet_provenance_for_next_clause():
    steps = extract_procedure_steps(
        _kam_item("ㆍ\n계약서 검토\nㆍ\n외부조회 확인")
    )

    assert [step.procedure_text for step in steps] == ["계약서 검토", "외부조회 확인"]
    assert [step.method for step in steps] == ["inspection", "confirmation"]


def test_extract_procedure_steps_preserves_standalone_korean_enumerators():
    steps = extract_procedure_steps(
        _kam_item(
            "우리가 수행한 주요 감사절차는 다음과 같습니다.\n"
            "가.\n"
            "보고기간말 전후 발생한 매출거래의 수익인식시점 비교\n"
            "나.\n"
            "보고기간말에 임박하여 발생한 유의적인 거래에 대한 분석\n"
            "다.\n"
            "매출거래의 인도조건에 따른 수익인식시기의 정확성을 검토"
        )
    )

    assert [step.procedure_text for step in steps] == [
        "보고기간말 전후 발생한 매출거래의 수익인식시점 비교",
        "보고기간말에 임박하여 발생한 유의적인 거래에 대한 분석",
        "매출거래의 인도조건에 따른 수익인식시기의 정확성을 검토",
    ]
    assert [step.method for step in steps] == [
        "cutoff_test",
        "analytical_procedure",
        "inspection",
    ]


def test_extract_procedure_steps_rejects_bare_korean_enumerators():
    steps = extract_procedure_steps(
        _kam_item(
            "우리가 수행한 주요 감사절차는 다음과 같습니다.\n가.\n나.\n다."
        )
    )

    assert steps == []


def test_extract_procedure_steps_rejects_non_enumerator_korean_label():
    steps = extract_procedure_steps(
        _kam_item(
            "우리가 수행한 주요 감사절차는 다음과 같습니다.\n"
            "예.\n보고기간말 전후 발생한 매출거래의 수익인식시점 비교"
        )
    )

    assert steps == []


def test_extract_procedure_steps_does_not_promote_korean_label_before_lead_in():
    steps = extract_procedure_steps(
        _kam_item(
            "가.\n"
            "보고기간말 전후 발생한 매출거래의 수익인식시점 비교\n"
            "우리가 수행한 주요 감사절차는 다음과 같습니다."
        )
    )

    assert steps == []


def test_extract_procedure_steps_rejects_korean_enumerator_boilerplate():
    steps = extract_procedure_steps(
        _kam_item(
            "우리가 수행한 주요 감사절차는 다음과 같습니다.\n"
            "가.\n감사인의 책임과 전문가적 판단에 대한 일반 설명"
        )
    )

    assert steps == []


def test_extract_procedure_steps_preserves_glued_hyphen_list_after_audit_lead_in():
    steps = extract_procedure_steps(
        _kam_item(
            "우리가 수행한 주요 감사절차는 다음과 같습니다- 계약서 검토"
            "- 외부조회 확인- 분석적 절차 수행"
        )
    )

    assert [step.procedure_text for step in steps] == [
        "계약서 검토",
        "외부조회 확인",
        "분석적 절차 수행",
    ]
    assert [step.method for step in steps] == [
        "inspection",
        "confirmation",
        "analytical_procedure",
    ]


def test_extract_procedure_steps_preserves_inline_numbered_list_after_audit_lead_in():
    steps = extract_procedure_steps(
        _kam_item(
            "우리가 수행한 주요 감사절차는 다음과 같습니다. 1) 계약서 검토 "
            "2) 외부조회 확인 3) 분석적 절차 수행"
        )
    )

    assert [step.procedure_text for step in steps] == [
        "계약서 검토",
        "외부조회 확인",
        "분석적 절차 수행",
    ]
    assert [step.method for step in steps] == [
        "inspection",
        "confirmation",
        "analytical_procedure",
    ]


def test_extract_procedure_steps_does_not_treat_numeric_prose_as_a_list_without_audit_lead_in():
    steps = extract_procedure_steps(
        _kam_item("투자지표는 1) 계약서 검토 2) 외부조회 확인으로 구성됩니다.")
    )

    assert steps == []


def test_extract_procedure_steps_ignores_numeric_prose_before_audit_lead_in():
    steps = extract_procedure_steps(
        _kam_item(
            "투자지표는 1) 계약서 검토 2) 외부조회 확인으로 구성됩니다.\n"
            "주요 감사절차는 다음과 같습니다. - 자산을 검토하였습니다."
        )
    )

    assert [step.procedure_text for step in steps] == ["자산을 검토하였습니다."]


@pytest.mark.parametrize(
    "text_value",
    [
        "ㆍ\n감사기준에 따라 감사를 수행합니다.",
        "매출-매입 차이를 검토하였습니다-추가 분석을 수행하였습니다.",
    ],
)
def test_extract_procedure_steps_does_not_extend_list_provenance_to_responsibility_or_generic_minus_prose(
    text_value,
):
    steps = extract_procedure_steps(_kam_item(text_value))

    if "감사기준" in text_value:
        assert steps == []
    else:
        assert [step.procedure_text for step in steps] == [text_value]


@pytest.mark.parametrize(
    "text_value",
    [
        "감사인의 책임 - 계약서 검토",
        "감사인의 책임 - 계약서 검토 - 외부조회 확인",
        "감사인은 전문가적 판단을 유지할 책임이 있습니다ㆍ 계약서 검토",
        "감사기준에 따라 감사를 수행합니다 ① 계약서 검토",
    ],
)
def test_extract_procedure_steps_does_not_bypass_responsibility_rejection_via_inline_marker(
    text_value,
):
    """Catch artificial inline splitting that turns responsibility prose into a procedure list."""
    assert extract_procedure_steps(_kam_item(text_value)) == []


@pytest.mark.parametrize(
    "text_value",
    [
        "보고기간 전ㆍ후 거래를 검토하였습니다.",
        "내ㆍ외부 거래를 검토하였습니다.",
        "현ㆍ전기 잔액을 비교하였습니다.",
        "손익ㆍ공정가치 가정을 평가하였습니다.",
        "매출·매입 거래를 검토하였습니다.",
        "감사인은 전문가적 판단·의구심을 유지할 책임이 있습니다.",
    ],
)
def test_extract_procedure_steps_does_not_treat_lexical_middle_dots_as_list_provenance(
    text_value,
):
    """Catch a splitter that turns prose middle dots into unsupported procedure bullets."""
    steps = extract_procedure_steps(_kam_item(text_value))

    if "책임" in text_value:
        assert steps == []
    else:
        assert [step.procedure_text for step in steps] == [text_value]


def test_extract_procedure_steps_does_not_treat_fullwidth_hyphen_prose_as_bullets():
    steps = extract_procedure_steps(
        _kam_item("매출－매입 거래 차이를 검토하였습니다.")
    )

    assert [step.procedure_text for step in steps] == [
        "매출－매입 거래 차이를 검토하였습니다.",
    ]


def test_extract_procedure_steps_keeps_fullwidth_hyphen_prose_after_audit_lead_in():
    steps = extract_procedure_steps(
        _kam_item(
            "우리가 수행한 주요 감사절차는 다음과 같습니다. "
            "매출－매입 거래 차이를 검토하였습니다."
        )
    )

    assert [step.procedure_text for step in steps] == [
        "매출－매입 거래 차이를 검토하였습니다.",
    ]


@pytest.mark.parametrize(
    ("text_value", "expected_method"),
    [
        ("감사계획에 따라 주요 계약서를 검사하였습니다.", "inspection"),
        (
            "감사 계획의 일환으로 거래처 외부조회를 실시하였습니다.",
            "confirmation",
        ),
        (
            "감사절차에는 포함된 계약서 검사를 실제로 수행하였습니다.",
            "inspection",
        ),
        (
            "감사절차는 다음을 포함하였으며, 거래처에 확인서를 "
            "발송하였습니다.",
            "confirmation",
        ),
    ],
)
def test_extract_procedure_steps_keeps_performed_actions_in_planning_context(
    text_value,
    expected_method,
):
    steps = extract_procedure_steps(_kam_item(text_value))

    assert [step.method for step in steps] == [expected_method]


def test_extract_procedure_steps_preserves_independent_chained_actions():
    steps = extract_procedure_steps(
        _kam_item("경영진에게 질문하여 계약서를 검사하였습니다.")
    )

    assert [step.method for step in steps] == ["inquiry", "inspection"]


def test_extract_procedure_steps_preserves_specialist_and_model_validation():
    steps = extract_procedure_steps(
        _kam_item("전문가를 활용하여 가치평가 모델을 검증하였습니다.")
    )

    assert [step.method for step in steps] == [
        "specialist_involvement",
        "valuation_model_test",
    ]


def test_extract_procedure_steps_keeps_unknown_action_as_other():
    steps = extract_procedure_steps(
        _kam_item("해당 매출 자료를 대사하여 차이를 조사하였습니다.")
    )

    assert len(steps) == 1
    assert steps[0].method == "other"


def test_extract_procedure_steps_keeps_unknown_comparison_action_as_other():
    steps = extract_procedure_steps(
        _kam_item("당기 수치를 전기 및 예산과 비교하였습니다.")
    )

    assert [step.method for step in steps] == ["other"]


def test_extract_procedure_steps_splits_compound_action_lists():
    steps = extract_procedure_steps(
        _kam_item(
            "계약서를 검사하였으며 거래처에 외부조회서를 발송하였습니다.\n"
            "계약서 검사, 외부조회 및 기간귀속 테스트를 수행하였습니다."
        )
    )

    assert [step.method for step in steps] == [
        "inspection",
        "confirmation",
        "inspection",
        "confirmation",
        "cutoff_test",
    ]


def test_extract_procedure_steps_requires_full_body_quality():
    assert extract_procedure_steps(
        replace(_kam_item("계약서를 검사하였습니다."), quality_status="summary_only")
    ) == []


def test_extract_procedure_steps_bounds_adversarial_input_and_deduplicates():
    repeated = "계약서를 검사하였습니다."
    item = _kam_item("\n".join([repeated] * (MAX_PROCEDURE_STEPS * 4)))

    steps = extract_procedure_steps(item)

    assert len(steps) == 1
    assert steps[0].procedure_text == repeated


def test_replace_procedure_steps_is_idempotent_and_scoped(temp_engine):
    with get_session() as session:
        first = KamItem(
            rcept_no="20260301000001_100",
            dcm_no="100",
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            ordinal=1,
            title="수익인식",
            normalized_topic="revenue",
            reason_text="기간귀속 위험",
            audit_response_text="계약서를 검사하였습니다.\n기간귀속 테스트를 수행하였습니다.",
            related_note_references_json='["주석 2"]',
            full_body_hash="1" * 40,
            full_body_length=200,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime(2026, 3, 1),
        )
        second = KamItem(
            rcept_no="20260301000002_100",
            dcm_no="100",
            corp_code="00164779",
            bsns_year=2025,
            source_type="audit_report",
            ordinal=1,
            title="재고자산",
            normalized_topic="inventory",
            reason_text="평가 위험",
            audit_response_text="재고실사를 입회하였습니다.",
            related_note_references_json="[]",
            full_body_hash="2" * 40,
            full_body_length=200,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime(2026, 3, 1),
        )
        session.add_all([first, second])
        session.flush()
        first_id = first.id
        second_id = second.id

    assert replace_procedure_steps_for_kam(first_id) == 2
    assert replace_procedure_steps_for_kam(second_id) == 1
    with get_session() as session:
        stable_ids = [
            row.id
            for row in session.query(AuditProcedureItem)
            .filter(AuditProcedureItem.kam_item_id == first_id)
            .order_by(AuditProcedureItem.procedure_ordinal)
        ]

    assert replace_procedure_steps_for_kam(first_id) == 2
    with get_session() as session:
        assert [
            row.id
            for row in session.query(AuditProcedureItem)
            .filter(AuditProcedureItem.kam_item_id == first_id)
            .order_by(AuditProcedureItem.procedure_ordinal)
        ] == stable_ids
        assert (
            session.query(AuditProcedureItem)
            .filter(AuditProcedureItem.kam_item_id == second_id)
            .count()
            == 1
        )


def test_replace_procedure_steps_does_not_infer_summary_only_rows(temp_engine):
    with get_session() as session:
        item = KamItem(
            rcept_no="20260301000003_100",
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            ordinal=1,
            title="수익인식",
            normalized_topic="revenue",
            reason_text=None,
            audit_response_text=None,
            related_note_references_json="[]",
            full_body_hash="3" * 40,
            full_body_length=20,
            source_basis="report_sections.derived_summary",
            parser_version="kam.v1",
            quality_status="summary_only",
            fetched_at=datetime(2026, 3, 1),
        )
        session.add(item)
        session.flush()
        item_id = item.id

    assert replace_procedure_steps_for_kam(item_id) == 0
    with get_session() as session:
        assert session.query(AuditProcedureItem).count() == 0


def test_replace_procedure_steps_cleans_exact_rows_after_quality_downgrade(
    temp_engine,
):
    with get_session() as session:
        item = KamItem(
            rcept_no="20260301000004_100",
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            ordinal=1,
            title="수익인식",
            normalized_topic="revenue",
            reason_text="위험",
            audit_response_text="계약서를 검사하였습니다.",
            related_note_references_json="[]",
            full_body_hash="4" * 40,
            full_body_length=500,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime(2026, 3, 1),
        )
        session.add(item)
        session.flush()
        item_id = item.id

    assert replace_procedure_steps_for_kam(item_id) == 1
    with get_session() as session:
        session.get(KamItem, item_id).quality_status = "summary_only"

    assert replace_procedure_steps_for_kam(item_id) == 0
    with get_session() as session:
        assert (
            session.query(AuditProcedureItem)
            .filter(AuditProcedureItem.kam_item_id == item_id)
            .count()
            == 0
        )


def test_replace_procedure_steps_cleans_deleted_and_replaced_kam_rows(
    temp_engine,
):
    with get_session() as session:
        old = KamItem(
            rcept_no="20260301000005_100",
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            ordinal=1,
            title="수익인식",
            normalized_topic="revenue",
            reason_text="위험",
            audit_response_text="계약서를 검사하였습니다.",
            related_note_references_json="[]",
            full_body_hash="5" * 40,
            full_body_length=500,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime(2026, 3, 1),
        )
        session.add(old)
        session.flush()
        old_id = old.id
    assert replace_procedure_steps_for_kam(old_id) == 1

    with get_session() as session:
        replacement = KamItem(
            rcept_no="20260301000005_100",
            corp_code="00126380",
            bsns_year=2025,
            source_type="audit_report",
            ordinal=1,
            title="수익인식",
            normalized_topic="revenue",
            reason_text="위험 변경",
            audit_response_text="기간귀속 테스트를 수행하였습니다.",
            related_note_references_json="[]",
            full_body_hash="6" * 40,
            full_body_length=510,
            source_basis="source_documents.full_body",
            parser_version="kam.v1",
            quality_status="full_body",
            fetched_at=datetime(2026, 3, 2),
        )
        session.add(replacement)
        session.flush()
        replacement_id = replacement.id

    assert replace_procedure_steps_for_kam(replacement_id) == 1
    with get_session() as session:
        assert (
            session.query(AuditProcedureItem)
            .filter(AuditProcedureItem.kam_item_id == old_id)
            .count()
            == 0
        )
        session.delete(session.get(KamItem, replacement_id))

    assert replace_procedure_steps_for_kam(replacement_id) == 0
    with get_session() as session:
        assert session.query(AuditProcedureItem).count() == 0
