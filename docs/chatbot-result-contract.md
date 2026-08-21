# Internal chatbot result contract

KReports MCP responses are consumed by an internal chatbot. The presentation
contract must therefore optimize for a business user's question, not for the
shape of the underlying program output.

## Dual response surface

Every supported MCP response keeps two synchronized surfaces:

1. `answer`
   - starts with the direct answer in plain Korean;
   - shows only the most relevant figures or five-company page;
   - includes clickable DART links when a receipt number is available;
   - states only limitations that materially affect the answer;
   - never exposes implementation terminology.

2. `answer_pack`
   - validated structured content for cards, tables, charts, and source buttons;
   - may retain multiple five-company pages for UI Previous/Next controls;
   - keeps reproducibility metadata internally without showing it in the default
     answer.

The raw domain facts remain unchanged. Presentation failure must fall back to
the original evidence response rather than fail the tool call.

## Recommended corporate-chat layout

1. One- or two-sentence direct answer.
2. Three to five supporting figures.
3. Five companies per visible company page.
4. A short table using business-language labels.
5. Inline `공시 보기` links and receipt numbers when available.
6. No more than two material limitations in the default answer.
7. Plain-language follow-up actions.
8. Long note excerpts collapsed by default.

The default answer must not show tool names, database names, schemas, hashes,
internal codes, or UI implementation instructions.

## Supported user-facing views

### Comparison-company selection

- Explain how many companies were selected and why.
- Show five companies at a time.
- Translate selection reasons into phrases such as `같은 업종`, `회사 규모가
  유사`, `비교자료 확보`, and `사용자가 직접 선택`.
- Keep comparison-population identity and hashes in internal metadata only.

### Industry comparison

- Lead with the latest-year relative position of the most relevant metrics.
- Show company value, comparison-company median, relative position, comparison
  count, and data availability.
- Do not show mid-rank, cell, coverage, or other statistical implementation
  terminology in the default answer.
- Small-sample suppression remains enforced internally.

### Accounting-note company search

- State the number of companies where a related phrase was found.
- Show five companies at a time.
- Use natural search-scope phrases:
  - `입력한 문구 그대로`;
  - `띄어쓰기와 기호 차이까지 포함`;
  - `유사한 표현까지 포함`.
- Show a keyword-centered passage and a clickable filing link for every valid
  receipt number.

### Peer accounting-note comparison

- Summarize how many topics and companies were compared and how many wording
  differences were identified.
- Present five comparison companies at a time.
- Translate availability and financial-statement-basis states into plain Korean.
- Link each available company row to the relevant DART filing.
- Make clear that wording differences do not by themselves establish different
  accounting treatment.

## Five-company paging rules

- `answer` renders the first page only.
- `answer_pack` keeps up to eight pages of five companies each.
- Internal presentation metadata identifies page table IDs and row counts.
- A business user sees `다음 5개 회사` or `이전 5개 회사`, not offsets, row
  limits, truncation, or payload constraints.
- If fewer companies are loaded than exist in the complete population, the UI
  states this in plain language without presenting the loaded subset as the
  total population.

## Link rules

- A valid 14-digit receipt number maps to the canonical DART filing URL.
- The primary label is `공시 보기`.
- Do not fabricate a company link when no receipt number is available.
- Keep source links beside the relevant company or note and repeat the most
  important links in the source section.

## User-visible language rules

The default answer must not contain the following implementation concepts:

```text
answer_pack
_meta
local_kreports_db
schema or dataset version
cohort ID or hashes
selection_score or include_reasons
mid-rank or statistical cell
exact / normalized / synonym
summary_only / unavailable
different_normalized_text
fallback_with_warning
```

The structured contract may retain technical values when required for audit and
reproducibility, but the visible title, summary, table labels, warnings, and
follow-up prompts must use business language.

## Codex validation handoff

GitHub Actions remain manual-only. Run in Codex:

```bash
python -m pip install -e ".[dev,api]"
python scripts/refresh_mcp_wire_hash.py
python scripts/refresh_mcp_wire_hash.py --check
python -m compileall -q kreports/analysis kreports/mcp scripts

rm -rf .codex-validation
mkdir -p .codex-validation

DB_URL="sqlite:///./.codex-validation/kreports.db" \
KREPORTS_RUNTIME_MODE=collector \
kreports init

DB_URL="sqlite:///./.codex-validation/kreports.db" \
KREPORTS_RUNTIME_MODE=readonly \
pytest -q \
  tests/test_mcp_schema_closure.py \
  tests/test_peer_statistical_quality.py \
  tests/test_note_search_quality.py \
  tests/test_note_quality.py \
  tests/test_chatbot_presentation.py \
  tests/test_user_first_chatbot_answers.py \
  tests/test_mcp_catalog.py \
  tests/test_mcp_contracts.py \
  tests/test_all_tools_contract.py \
  tests/test_readonly_mcp.py
```

Record the validated commit SHA, Python version, exact commands, test counts,
and any unexecuted live-data checks in the Draft PR before recommending merge.
