# Accounting-note source evidence and drill-down

KReports users usually need to know what a company actually wrote, not how the
system would rewrite or score that wording. The note workflow therefore follows
a source-first contract:

1. confirm whether the requested expression is present in prepared evidence;
2. preserve the expression actually used by the company;
3. show a bounded excerpt from the original note;
4. state whether the excerpt comes from complete or partial stored note text; and
5. provide the related paragraph, complete note, and original DART filing.

This contract is shared by note search, peer-note comparison, MCP resources, and
the internal chatbot.

## Canonical ownership

`kreports.analysis.note_evidence` remains the single owner for:

- deterministic `note_ref` generation and validation;
- external full-text recovery with hash verification;
- cached-text completeness classification;
- related-paragraph extraction;
- optional topic/facet detection; and
- note resource URIs.

`kreports.analysis.note_source_projection` is a lightweight application
projection. It reuses the canonical reference and completeness functions and
attaches only the excerpt, scope, source link, and lazy resource actions needed
for the ordinary chatbot answer. It is not a second note-evidence engine.

Search and comparison handlers enrich their existing results through this
projection. MCP resources and chatbot presentation consume the same evidence.
They do not maintain separate reference, recovery, paragraph-selection, or
source-link rules.

## Source-first default answer

The initial chatbot response remains small and evidence-led:

- one direct answer;
- five companies;
- the expression actually found;
- one representative original-text excerpt per company;
- `전체 주석에서 발췌`,
  `일부 저장 문구에서 발췌 · 전체 주석 확인 필요`, or another
  user-friendly scope label; and
- a canonical DART filing link when available.

The default answer must not present:

- `구체적`, `보통`, or `간략` disclosure grades;
- a completeness score or percentage;
- a standardized sentence that replaces the company's wording;
- a list of supposed mandatory disclosure requirements; or
- a conclusion that an unobserved item was omitted from the filing.

The source excerpt may normalize whitespace and line breaks for readability, but
must not paraphrase, merge, or rewrite the company's disclosure. If the excerpt
is shortened for the first answer, the visible text must end with an ellipsis.
`전체 주석에서 발췌` means that the stored note body is complete; it does not
mean that the first table cell displays the complete note.

### Example

> 현재 확보된 2024년 사업보고서에서 ‘자금보충약정’ 관련 표현이 확인된
> 회사는 37개입니다. 아래에는 회사가 실제 공시에서 사용한 표현과 원문
> 발췌를 보여드립니다.

| 회사 | 실제 사용 표현 | 실제 공시 문구 | 원문 확인 범위 | 원 공시 |
|---|---|---|---|---|
| A사 | 자금보충약정 | “회사는 ○○SPC의 대출약정과 관련하여 원리금 상환재원이 부족한 경우 부족액을 보충할 의무를 부담하고 있습니다…” | 전체 주석에서 발췌 | 공시 보기 |
| B사 | 자금보충의무 | “회사는 관계기업의 사업비 부족액에 대한 자금보충의무를 부담하고 있습니다…” | 일부 저장 문구에서 발췌 · 전체 주석 확인 필요 | 공시 보기 |

## Optional structured comparison

Topic and information-element detection is an optional navigation aid, not a
universal disclosure standard. It may be used only when the user asks a focused
question such as:

> 이 회사들이 금액, 의무 발생 조건, 약정기간까지 공시했는지 원문으로
> 비교해줘.

The answer must return the exact supporting source span for each requested
facet.

| 회사 | 금액 관련 원문 | 발생 조건 관련 원문 | 기간 관련 원문 |
|---|---|---|---|
| A사 | “3,000억원 대출약정” | “상환재원이 부족한 경우” | “2032년까지” |
| B사 | 현재 확인된 문구 없음 | “사업비가 부족한 경우” | 현재 확인된 문구 없음 |

Rules:

- every detected facet must carry an exact source excerpt;
- `현재 확인된 문구 없음` is not the same as `공시하지 않음`;
- partial cached text must be qualified with `전체 주석 확인 필요`;
- no overall disclosure score is calculated by default; and
- structured facets never replace the original paragraph or filing link.

## Allowed and prohibited transformation

### Allowed

- normalize whitespace and line breaks;
- remove unsafe HTML or control characters;
- select the paragraph around the matched expression;
- mark omitted beginning or ending text with an ellipsis;
- preserve table rows and columns when possible;
- highlight the matched expression; and
- attach the receipt number and DART link.

### Prohibited in the default answer

- rewrite the note in chatbot language;
- combine multiple sentences into a new assertion;
- replace company-specific wording with a standard sentence;
- score disclosure quality without an explicit normative basis;
- rank companies by disclosure quality; and
- describe an unobserved item as omitted when only partial evidence is present.

## Deterministic note reference

A note reference has the form:

```text
n1-<row-id>-<20-character-content-digest>
```

The digest binds the reference to the note identity and current content. A
modified or replaced row makes the old reference stale instead of silently
returning different evidence.

The reference is not a credential and contains no API key, filing body, or local
path.

## MCP resources

### Note summary

```text
kreports://note/{note_ref}
```

Returns note identity, source link, text completeness, related paragraphs, a
preview, and resource links. Internal optional facet metadata may be included in
structured data, but is not rendered as a default user-facing grade.

### Related paragraphs

```text
kreports://note/{note_ref}/paragraph
```

Returns the paragraphs most relevant to the matched expression or requested
topic. The text remains source wording with only bounded whitespace cleanup.

### Complete note pages

```text
kreports://note/{note_ref}/page/{page}
```

Returns the complete available note in bounded 8,000-character pages. Each page
includes previous/next URIs, returned character range, page count, completeness
status, and the original DART link.

If an external full-text blob is available, it is read lazily and hash-checked.
If only cached partial text is available, the resource says so explicitly.

## Model-context and performance rules

The following stay outside the routine model context:

- complete note text;
- all note pages;
- all matching paragraphs;
- large tables embedded in the note; and
- raw filing documents.

The web chatbot receives application-only actions for:

- `관련 문단`;
- `주석 전체`; and
- `원 공시`.

Opening a resource does not rerun note search, peer selection, or statistical
analysis. The ordinary source-first projection performs no optional facet
assessment and reads no external full-text blob. External full text is loaded
only when the user opens a resource.

Batch loading is used for note rows referenced by one result. The projection
must not issue one database query per company or topic.

## Integration rules

```text
accounting_note_chapters
→ note_evidence canonical service
→ note_source_projection
→ existing search/comparison handlers
→ source-first chatbot view
→ application-only resource actions
→ MCP note resources
```

No renderer or transport may reimplement note reference generation, full-text
recovery, or paragraph selection. Optional facet extraction must remain tied to
the same note reference and exact source spans.

## Validation

Run in the default MCP 1.x environment:

```bash
python -m compileall -q \
  kreports/analysis/note_evidence.py \
  kreports/analysis/note_source_projection.py \
  kreports/mcp/resources.py \
  kreports/mcp/chatbot_note_depth.py

pytest -q \
  tests/test_note_evidence_depth.py \
  tests/test_note_resource_contract.py \
  tests/test_note_depth_chatbot.py \
  tests/test_note_search_quality.py \
  tests/test_note_quality.py
```

Regression tests must verify that the default answer contains actual source
wording, exposes the excerpt scope, omits disclosure grades, skips optional
facet assessment, keeps resource IDs out of visible prose, and does not convert
missing evidence into an omission claim. GitHub Actions remain manual-only.
