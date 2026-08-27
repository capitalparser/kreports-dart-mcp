# Customized peer-selection transparency

KReports must not present a peer list as an opaque AI recommendation. When a
user customizes the comparison criteria, the answer must show the exact criteria
actually applied and the companies produced by those criteria in the same
response.

## Product contract

```text
user criteria
→ validated criteria
→ resolved applied criteria
→ full eligible population
→ explicit display order
→ first five companies with criteria evidence
→ downstream analysis over the same population
```

The user-facing result answers five questions:

1. Which criteria did I request?
2. Which criteria were actually applied?
3. How many companies qualified?
4. Why is each displayed company included?
5. Why are these five shown before the remaining companies?

## Example

> **The customized criteria matched 23 companies.** The system applied 2024
> consolidated financial statements, a three-digit KSIC match, revenue between
> 0.32 and 3.16 times the subject, and available accounting-note evidence. The
> first five companies are shown according to the declared ordering below.

### 적용한 비교 기준

| 기준 | 실제 적용 내용 |
|---|---|
| 기준 출처 | 사용자가 지정한 기준 |
| 기준연도 | 2024년 |
| 재무제표 기준 | 연결재무제표 |
| 업종 범위 | 한국표준산업분류 앞 3자리 일치 |
| 회사 규모 | 기준회사 매출의 0.32배~3.16배 범위 |
| 필요 비교자료 | 재무제표 주석 원문 중 최소 100% 확보 |
| 선정 방식 | 선택한 기준 적합도가 높은 순으로 정렬 |
| 적합도 가중치 | 업종 40% · 회사 규모 40% · 자료 확보 20% |

### 선정된 회사 1~5

| 회사 | 기준 충족 근거 | 매출 | 총자산 |
|---|---|---:|---:|
| A사 | 업종 기준 충족 · 매출 규모 조건 충족 · 요청한 비교자료 100% 확보 | … | … |
| B사 | 업종 기준 충족 · 매출 규모 조건 충족 · 요청한 비교자료 100% 확보 | … | … |

The visible explanation is generated from the same `selection_policy` and peer
rows used by the analytical result. It is not generated from a second selector
or an LLM reconstruction.

## Requested versus applied

The system keeps requested and applied values separately.

Examples:

- requested KSIC three digits, applied two digits after an allowed fallback;
- requested year 2024, resolved year 2023 because comparable prepared data was
  unavailable for 2024;
- requested `auto`, applied 연결재무제표;
- requested employee-size comparison, currently unsupported due to unavailable
  evidence; or
- requested revenue-size comparison without a tolerance, therefore not applied
  as a filter.

The response must label each criterion as `applied`, `informational`,
`not_applied`, or `unsupported`. It must not silently drop unsupported input.

## Membership versus ordering

Peer membership and display order are different decisions.

- Membership is determined by the applied industry, size, availability,
  inclusion, exclusion, and minimum-coverage rules.
- Ordering determines which eligible company appears first.

Current non-ranked peer output is ordered by total assets. It must therefore be
labeled:

> 선택한 조건을 충족한 회사 중 총자산이 큰 순

It must not be labeled `가장 유사한 순` or `관련성 높은 순`.

A ranked profile may be labeled as a criteria-fit ranking only when the canonical
selector emits a deterministic score from the declared dimensions. Even then,
that score does not establish full business-model similarity. A size tolerance
is a range filter unless a separate continuous size-distance component is
implemented.

## Canonical implementation

- Criteria validation: `kreports.analysis.peer_criteria.PeerCriteriaProfile`
- Membership and ordering: the existing canonical peer selector
- Full statistical population: `kreports.analysis.peer_quality`
- Pure explanation projection:
  `kreports.analysis.peer_selection_explanation`
- Chatbot presentation:
  `kreports.mcp.chatbot_peer_transparency`
- Five-company page packaging:
  `kreports.mcp.chatbot_company_pagination`

The explanation projection:

- performs no database query;
- does not change peer membership;
- does not resort companies;
- does not recalculate selection scores; and
- attaches the same structured explanation to the top-level result and nested
  peer group when applicable.

## Structured response

```json
{
  "selection_explanation": {
    "criteria_origin": "user_customized",
    "criteria_sentence": "...",
    "applied_criteria": [],
    "population": {
      "eligible_company_count": 23,
      "returned_company_count": 5
    },
    "ordering": {
      "label": "...",
      "is_relevance_ranking": false
    },
    "company_explanations": [],
    "limitations": []
  }
}
```

The web demo may render this as a criteria card or table above the first five
companies. Plain Markdown must remain complete without the custom UI.

## Pagination and conversation state

The criteria table is auxiliary content, not a company page. The first chat
answer shows:

```text
criteria table
+ company page 1 (companies 1~5)
```

`다음 5개` reuses the stored population and ordering. A criteria change rebuilds
the population and invalidates all dependent benchmark, note-comparison,
result-reference, and page-token state.

## Validation

Run in Codex without dispatching GitHub Actions:

```bash
python -m compileall -q \
  kreports/analysis/peer_selection_explanation.py \
  kreports/mcp/chatbot_peer_transparency.py \
  kreports/mcp/chatbot_company_pagination.py \
  kreports/mcp/chatbot_integration.py

pytest -q \
  tests/test_peer_criteria_transparency.py \
  tests/test_peer_criteria.py \
  tests/test_peer_statistical_quality.py \
  tests/test_user_first_chatbot_answers.py \
  tests/test_chatbot_presentation.py \
  tests/test_mcp_answer_pack.py \
  tests/test_mcp_contracts.py \
  tests/test_all_tools_contract.py
```

The PR remains Draft until Codex records the validated commit SHA, exact
commands, pass/fail/skipped counts, fixes, and tests not run.
