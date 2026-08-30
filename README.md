# KReports

**Ask Korean filings like an analyst. KReports turns DART into investor-ready signals for Claude.**

[English](#english) | [한국어](#한국어)

[![PyPI](https://img.shields.io/pypi/v/kreports.svg)](https://pypi.org/project/kreports/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)

<p align="center">
  <img src="docs/images/kreports-demo.gif" alt="KReports MCP demo showing a plain-language Korean filing question flowing into source-grounded investor and auditor answers" width="840">
</p>

<p align="center">
  <sub>Ask once in Claude. KReports turns Korean DART filings into source-grounded paragraphs, tables, diagrams, and next checks for investor and auditor workflows.</sub>
</p>

---

<a id="english"></a>

## English

> Built by a Big4 auditor for investors who want to read Korean filings without living inside DART.

### Why KReports

Korean listed companies already tell you a lot in DART filings: revenue, cash flow, auditor changes, restatements, subsidiary structures, audit fees, business risks, and shareholder events.

The problem is not lack of data. The problem is that the data is buried across filings, tables, footnotes, and company codes.

KReports turns that raw disclosure pile into questions you can ask Claude:

- "Is this company financially healthy, or just optically cheap?"
- "Did anything suspicious show up in accounting, cash flow, auditor history, or restatements?"
- "How does this company compare with peers in the same Korean industry?"
- "What recent disclosure events should I read before buying or holding?"

### What it does

KReports connects [DART](https://dart.fss.or.kr) (Korea's SEC) to Claude via the [Model Context Protocol](https://modelcontextprotocol.io). It converts the companies and filing years proven by the selected runtime release manifest into structured financial intelligence; coverage is an artifact property, not a timeless product claim.

### What makes it different

KReports is not a thin wrapper around the DART API. It is a filing intelligence
layer built around the questions analysts actually ask after opening an annual
report.

- **Document-first evidence**: stores and normalizes business reports, audit
  reports, KAM sections, accounting policy notes, and disclosure events so MCP
  answers can point back to source filings.
- **Investor lens**: combines financial quality, cash conversion, peer
  benchmarking, accounting risk, disclosure events, and DCF input candidates.
- **Auditor lens**: tracks going-concern signals, auditor changes, audit fees,
  non-audit service ratios, group-audit perimeter, KAM topics, audit procedures,
  and accounting policy changes.
- **Peer-group comparison**: selects comparable companies from Korean industry
  codes and compares margins, leverage, cash flow, audit fees, KAM topics, and
  policy patterns.
- **Runtime-ready MCP**: separates private collection jobs from read-only MCP
  serving, so users can query a compact runtime dataset without needing a DART
  API key.
- **Source-grounded answers**: returns prose-oriented MCP responses with
  confirmed facts, analysis, next checks, and filing provenance instead of only
  raw JSON dumps.
- **Semantic filing context**: combines business-report sections, audit-report
  evidence, disclosures, financial facts, and accounting-note topics into one
  company/year context so an LLM can answer across document types.
- **Original-note comparison**: compares accounting-note topics across a
  user-selected peer group and preserves the source excerpt, locator, source
  status, and formatting metadata. Missing or unverified originals remain
  explicitly marked rather than silently summarized.
- **Custom peer cohorts**: peer selection can use industry-prefix rules,
  explicit company lists, or adaptive criteria. The cohort and rule used for a
  comparison are returned with the result for reproducibility.
- **Fail-closed coverage**: release artifacts expose which report types,
  sections, note topics, and raw sources are actually available. A successful
  code/test run does not imply that every filing or original note is present.

### Public/private boundary

The public repository documents the MCP contract, supported workflows, data
provenance model, and reproducible client examples. Collection credentials,
raw-document storage, enrichment jobs, parser internals, and release data are
maintained in the separate private `capitalparser/kreports-core` repository.
The private repository is now the canonical home for the core pipeline; this
public branch is the compatibility/public-documentation snapshot while the
extraction is completed. The public package must not be treated as a guarantee
that all companies or all original note text are available in a given release;
use the release artifact and per-source status for that determination.

### Two workflows

KReports serves two audiences from the same DART source: investors who need fast judgment signals, and audit/accounting professionals who need evidence and risk coverage.

#### Investors

You do not need to know accounting standards to start. Ask in plain language and use KReports as a pre-buy or portfolio checkup.

| Investor question | What KReports checks |
|-------|-------|
| "Is Samsung Electronics still a high-quality business?" | ROE, operating margin, revenue growth, debt ratio, free cash flow, cash conversion |
| "What should I worry about before buying Kakao?" | Restatements, amendments, Beneish M-Score, auditor changes, non-clean opinions, cash-flow gaps |
| "Is this stock strong compared with peers?" | KSIC industry P25/P50/P75 and peer list |
| "Did recent filings contain shareholder-friendly or dilution events?" | Treasury stock, capital raise, CB/BW/EB, merger/split, large contract, litigation, amendments |
| "Can I trust the numbers?" | DART original data, accounting policy footnotes, audit opinions, audit fees, subsidiary auditor matrix |

The new `get_investor_signals` tool gives one compact first-pass read: quality profile, accounting/governance risk, recent investor-relevant disclosure events, and plain takeaways.

#### Audit and accounting professionals

KReports also preserves the audit lens it was born from. It helps turn scattered DART filings into risk leads that can be traced back to source filings.

| Professional question | What KReports checks |
|-------|-------|
| "Is there a going-concern issue I should not miss?" | Capital impairment, two-year operating losses, high debt, weak interest coverage, negative operating cash flow, non-clean opinion |
| "Did prior-year numbers move after the next annual report?" | Prior-period restatement candidates across annual filings |
| "Did the auditor change, and how long has the current auditor served?" | Auditor, opinion, change flag, consecutive years |
| "Is independence worth reviewing?" | Audit fee, non-audit fee, NAS ratio |
| "What does the group audit perimeter look like?" | Subsidiary and affiliate auditor matrix |
| "Which accounting policies matter for this company?" | Standard K-IFRS policy footnote extraction |

### Setup

Two modes — pick one.

---

#### Option A: Hosted remote MCP (no DART key needed)

The production service is a **read-only Streamable HTTP MCP endpoint**:

```text
https://mcp.dartmcp.com/mcp
```

It serves the verified compact release artifact, not the writable collector
database. It exposes 33 public read-only tools; no DART API key, raw filing
backfill, or collector credentials are present on the public service.

##### 1. Public, read-only access — no token or OAuth

This production endpoint is intentionally a **public, no-auth, read-only MCP
service**. Enter its URL only; do not add an `Authorization` header, API key,
OAuth client, or DART key. It is not a web page, so opening `/mcp` directly in
a browser is not a useful test.

- `/mcp` is the public Streamable HTTP MCP endpoint.
- `/healthz` is public liveness and returns only `{"ok": true}`.
- `/readyz` is deliberately not published by the reverse proxy; it is an
  in-container release gate for the operator.

The process has a read-only filesystem, mounts only the compact SQLite artifact
and its matching manifest, and rejects collector and DART credentials. The
proxy accepts MCP and liveness paths only and caps a request body at 256 KB.
This is public data access, not a user account system: do not send personal,
confidential, or write instructions to it.

##### 2. Any remote MCP client

Use the client's **remote / Streamable HTTP MCP** configuration and enter:

```json
{
  "url": "https://mcp.dartmcp.com/mcp"
}
```

First test with a read-only prompt such as: `삼성전자 2025년 투자자 신호를
근거와 한계까지 요약해줘`. A working client discovers the tool list and may
ask for tool-use permission; it must never receive a DART API key.

##### 3. Web chatbots: direct connection, not an API integration

| Client | What to enter | Who pays for the model |
|---|---|---|
| Claude.ai / Claude remote connector | The MCP URL above; select **no authentication** if prompted. | The user's Claude plan/account. |
| ChatGPT web | The MCP URL above; select **no authentication** if prompted. | The user's ChatGPT plan/account. |
| Codex, Cursor, Claude Desktop, or another MCP client | The MCP URL above, with no headers. | The user's own client/account. |

No KReports OAuth, shared token, Stripe checkout, or OpenAI API key is needed
for this direct mode. Connecting this MCP in ChatGPT is **not** a server-side
Responses API call made by KReports, so it does not make the MCP operator pay
for the user's model requests. Building a separate custom chatbot with the
OpenAI API is optional and would be a separate, billable application.

##### ChatGPT web setup

1. In ChatGPT web, enable **Developer mode** if your plan/workspace exposes it.
2. Open **Settings or Workspace settings → Apps → Create**.
3. Enter `https://mcp.dartmcp.com/mcp` as the remote MCP endpoint and choose
   **no authentication** when that choice is shown.
4. Select **Scan Tools**, then **Create**. Test from a new chat using a
   read-only question.
5. A workspace admin/owner can publish the scanned app for its members. When
   KReports changes its tool schema, refresh and review the actions before
   enabling those changes for the workspace.

ChatGPT availability and menus vary by plan. The current official guide says
that Pro users can connect read/fetch MCPs in Developer mode, while full MCP
app support is rolling out for Business and Enterprise/Edu workspaces. See the
[official ChatGPT Developer mode guide](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt).

OAuth is only an optional future feature if this public service later needs
per-user access control, revocation, paid entitlements, or private data. It is
not a prerequisite for the public read-only endpoint.

##### 4. Local Claude Desktop / Claude Code is a different mode

The following starts a **local stdio** MCP process; it does not connect to the
hosted production endpoint and needs a locally configured runtime DB:

```bash
claude mcp add kreports -- uvx --from kreports kreports-mcp
```

Use it for local development. For the hosted service, use the remote endpoint
above; it has no client credential.

##### 5. Operator-only smoke checks

```bash
curl -fsS https://mcp.dartmcp.com/healthz
curl -sS -o /dev/null -w '%{http_code}\n' https://mcp.dartmcp.com/readyz
```

The first command returns liveness. The second must return `404` from the public
proxy: detailed release readiness is intentionally available only to the
container healthcheck. A public MCP request must never be accepted as a DB
release-readiness proof; use the mounted artifact verification and container
health status for that decision.

---

#### Option B: Self-hosted (bring your own data)

Build and own your local database. Requires a free DART API key.

```bash
pip install kreports
echo "DART_API_KEY=your_key" > .env

kreports init
kreports sync-companies
kreports collect-seed --size small   # ~350 companies, ~20 min

kreports serve
```

Get a free DART API key at [opendart.fss.or.kr](https://opendart.fss.or.kr).

### Then ask Claude

```
"Samsung Electronics investor signal summary — quality, accounting risk, recent disclosure events"
"What recent disclosure events should I read before buying Kakao?"
"Compare Samsung Electronics operating margin to semiconductor peers"
"SK Hynix going concern risk — 6-factor scorecard"
"Show auditor history for Kakao for the past 5 years"
"Has Celltrion restated any prior period figures?"
"Subsidiary auditor matrix for POSCO group"
"Beneish M-Score for this company — earnings manipulation risk"
"Compare lease-note policy, right-of-use assets, and lease liabilities across Samsung and its selected peers; show original excerpts and source status"
```

### MCP Tools (33 public)

KReports exposes 33 credential-free, read-only MCP tools. One additional
`fetch_disclosure_on_demand` tool remains available only to explicitly opted-in
self-hosted operators. The public tools are grouped around the
maintenance questions that usually force analysts and auditors back into DART:

| Area | Representative tools | What it returns |
|------|----------------------|-----------------|
| Company lookup | `search_company` | Corp code, market, stock code, name disambiguation |
| Investor first pass | `get_investor_signals`, `get_quality_of_earnings_pack`, `get_dcf_input_candidates` | Quality checks, accounting risk, disclosure events, DCF input candidates |
| Financials and peer benchmarking | `get_financial_snapshot`, `compare_to_industry`, `compare_to_industry_multi`, `select_peer_group` | Multi-year financial facts, KSIC peer percentiles, peer group selection |
| Disclosure monitoring | `search_disclosure_events`, `search_dataset` | Indexed event and evidence-document search from the verified local release DB |
| Audit risk | `score_going_concern`, `detect_restatement`, `build_audit_acceptance_pack`, `estimate_audit_hours_proxy` | Going-concern score, restatement candidates, acceptance risk pack, audit-hour proxy |
| Auditor and group audit | `get_audit_history`, `get_subsidiary_auditors`, `get_industry_audit_landscape`, `compare_peer_audit_fees` | Auditor tenure, opinion history, group auditor matrix, audit fee/NAS peer view |
| Audit report evidence | `get_audit_report_sections`, `search_audit_report_matters`, `search_audit_procedures`, `get_kam_lifecycle` | Audit report sections, KAM matters, audit procedures, year-to-year KAM lifecycle |
| Accounting policies | `get_accounting_policy`, `compare_peer_accounting_policies`, `get_accounting_policy_changes` | K-IFRS policy notes, peer policy comparison, policy change candidates |
| Semantic filing and peer context | `get_business_overview`, `compare_peer_accounting_policies`, `select_peer_group` | Optional local DART evidence buckets, explainable cohort selection, side-by-side note excerpts and provenance |

All tools accept company name, 6-digit stock code, or 8-digit DART corp_code interchangeably.

### Professional response contract

Professional MCP answers always begin with `판정:`. This is the canonical
availability status (`usable`, `limited`, `missing`, or `error`), not an audit,
investment, approval, or valuation conclusion. A domain verdict may state a
bounded result such as DCF `산출 불가`; it never overrides the availability
status.

Read responses in this order: the Korean chatbot `answer`, then the complete
structured `answer_pack`, then its detailed visualization resource. The pack
retains rows and provenance that the concise answer may summarize.

`prepare_standard_audit_hours_inputs` prepares three years of public inputs;
its `not_assessed` boundary means it does not calculate a standard audit-hour
conclusion. Similarly, DCF candidates are inputs for review, whereas valuation
readiness decides whether a value can be calculated. Missing cache data is not
evidence that the underlying filing does not exist. A question can therefore be
usable while a deployment release remains unready; release context is separate.

### For Python developers

```bash
pip install kreports
```

```python
import kreports

# Financial snapshot
snap = kreports.get_financial_snapshot("005930", years=3)

# Going concern score
gc = kreports.score_going_concern("005930")
print(f"Score: {gc['score']}/100 ({gc['grade']})")

# Industry benchmark
bench = kreports.compare_to_industry("005930", metric="영업이익률")

# Investor signal summary
signals = kreports.get_investor_signals("005930")
```

### Full local setup (self-hosted)

```bash
pip install kreports

# Set API key
echo "DART_API_KEY=your_key" > .env

# Initialize and collect
kreports init
kreports sync-companies
kreports enrich-market
kreports collect-seed --size small   # ~350 companies, ~20 min
# or
kreports collect-all --year-from 2021 --year-to 2025   # configured company universe

# Start MCP server
kreports serve
```

### Remote HTTP MCP

For the production endpoint and public-client boundary, see
[Hosted remote MCP](#option-a-hosted-remote-mcp-no-dart-key-needed). Do not
expose a local `serve-http` process to the public internet with
`--allow-unauthenticated`; that flag is only for short-lived loopback testing.

### Going Concern Scorecard

100-point deduction system (K-IFRS audit standard):

| Factor | Deduction | Threshold |
|--------|----------:|-----------|
| Capital impairment | −30 | Total equity < 0 |
| 2-year consecutive operating loss | −20 | OP < 0 for 2 years |
| Debt ratio > 200% | −15 | Debt / Equity > 200% |
| Interest coverage < 1.0 | −15 | OP / Interest expense < 1 |
| Negative operating cash flow | −10 | Operating CF < 0 |
| Non-clean audit opinion | −10 | Qualified / Adverse / Disclaimer |

Grades: **Stable** (80+) / **Caution** (60–79) / **Warning** (40–59) / **Danger** (<40)

### Release evidence and data coverage

Coverage and readiness are read from the JSON release artifact next to the
runtime SQLite DB. Build evidence even when a gate is blocked, then verify it
against the exact DB that will be deployed:

```bash
kreports build-release-manifest --db artifacts/kreports-runtime.db
kreports verify-release-artifact --db artifacts/kreports-runtime.db
```

Build exits successfully after writing a valid proof whose
`release_gate.passed` value may be false. Verify returns non-zero for DB drift,
contract drift, or any current named release blocker. The 33-tool public smoke
executes handlers in an isolated process without credentials or network writes.
`/readyz` reads the pre-verified
artifact and performs cheap WAL/file/static-contract drift checks; it does not
rehash the full DB or rerun all tools for every health probe.

Investor functions are ready only when `investor_core` passes the manifest
gate. Auditor functions remain conditional where accounting-policy,
audit-procedure, or group-audit grades are degraded. Code/test success does not
override a blocked live-data gate.

DCF packs keep four boundaries visible: source filing actuals, explicit
assumptions, Decimal model mechanics, and analyst judgment. The model does not
turn missing inputs into inferred actuals.

### Maintainer source-archive operations

The five-year source archive is a local collector workflow, not a public MCP
feature or a statement that all source reports have already been collected.
It preserves original DART containers/members and generic structure packages in
Google Drive, then keeps candidate-DB and runtime-artifact promotion as
separate approval gates. See [Drive-first annual source archive backfill](docs/source-archive-backfill.md)
for the bounded-spool, dry-run, 64-shard resume, Drive accounting, and
release-boundary procedure. The all-issuer v3 option,
`--universe all-annual-issuers`, uses a fresh Drive/state root and includes
`annual_report_issuer_outside_verified_markets` as `unclassified`; missing
KOSPI/KOSDAQ evidence is not proof of unlisted. The guide defines the required
KRX and dated issuer-status evidence before any historic-status promotion.
Public MCP queries never call Google Drive.

비활성 후보 DB와 과거 릴리스 DB는 별도 검증 절차로 Google Drive에 보관할 수
있습니다. Drive는 불변 보관소이고, MCP의 현재 읽기 전용 DB는 계속 로컬에서만
동작합니다. 검증 후 유예기간·여유공간 기준을 충족할 때만 로컬 사본을 정리하는
운영 절차는 [Maintainer DB archive lifecycle](docs/database-archive-lifecycle.md)에
정리했습니다.

Inactive candidate and historic release databases have a separate, verified
Google Drive lifecycle: Drive is an immutable archive, while the active MCP DB
remains local and read-only. The maintainer can schedule only the safe
archive-then-grace-then-capacity-prune workflow described in
[Maintainer DB archive lifecycle](docs/database-archive-lifecycle.md).

### Architecture

```
kreports/
├── mcp/         MCP stdio + HTTP servers (33 public tools)
├── analysis/    Public Python API and evidence-grounded answer layer
├── collector/   DART API collectors and document-first backfill runners
├── processor/   XBRL/XML parsers
├── judge/       Risk flag engine (Beneish, Going Concern)
├── db/          SQLAlchemy models for runtime facts, evidence, and provenance
└── cli/         Typer CLI for collection, readiness checks, and MCP serving

dashboard/       Optional Streamlit UI (9 pages)
```

### CLI reference

```
kreports init                  Initialize DB
kreports sync-companies        Sync DART company registry
kreports enrich-market         Fill market + KSIC codes
kreports collect-seed          Collect core companies (small/medium/full)
kreports collect <ticker>      Collect single company
kreports collect-all           Batch collect all listed companies
kreports collect-auditors      Collect auditor history
kreports collect-audit-fees    Collect audit / non-audit fees
kreports collect-policies      Persist accounting policy footnotes
kreports compute-flags         Recompute Beneish + going concern flags
kreports serve                 Start MCP stdio server
kreports serve-http            Start MCP HTTP server
kreports mcp-doctor            Smoke-check MCP environment
kreports mcp-config            Print IDE config JSON
```

### Requirements

- Python 3.11+
- [Free DART OpenAPI key](https://opendart.fss.or.kr)

### License

Apache 2.0

### Author

**capitalparser** — Big4 CPA, 7 years in external audit. Built to replace the annual DART manual labor that every audit team dreads.

---

<a id="한국어"></a>

## 한국어

> DART 원문은 믿고 싶지만, 수백 쪽 사업보고서를 매번 직접 뒤질 수는 없는 투자자를 위해 만들었습니다.

### 왜 필요한가

한국 상장사는 이미 DART에 많은 힌트를 남깁니다. 매출, 현금흐름, 감사의견, 감사인 교체, 정정공시, 전기 재작성, 종속회사, 비감사보수, 사업위험, 자사주, 증자, 전환사채까지 전부 공시 안에 있습니다.

문제는 데이터가 없는 게 아니라, 너무 흩어져 있다는 점입니다. 공시 제목을 찾고, 보고서를 열고, 표를 보고, 주석을 읽고, 과거 보고서와 비교하는 일은 투자자가 매번 하기 어렵습니다.

KReports는 그 일을 Claude가 바로 물어볼 수 있는 형태로 바꿉니다.

- "이 회사 싸 보이는데, 숫자는 건강한가?"
- "최근 회계나 지배구조에서 이상 신호가 있었나?"
- "동종업계 안에서 이익률과 부채비율이 어느 정도 위치인가?"
- "매수 전에 꼭 봐야 할 최근 공시는 무엇인가?"

### 무엇을 하나

KReports는 한국 금융감독원 [DART](https://dart.fss.or.kr) 공시 데이터를 [MCP 프로토콜](https://modelcontextprotocol.io)로 Claude에 연결합니다. 선택한 runtime release manifest가 증명하는 기업·연도 범위의 공시와 재무 데이터를 투자자가 질문하기 쉬운 인텔리전스로 제공합니다.

### 무엇이 다른가

KReports는 DART API를 얇게 감싼 래퍼가 아닙니다. 사업보고서와
감사보고서를 실제로 열어본 뒤 이어지는 질문을 기준으로 만든 공시
인텔리전스 레이어입니다.

- **문서 우선 근거화**: 사업보고서, 감사보고서, 핵심감사사항, 회계정책
  주석, 공시 이벤트를 원문 근거와 함께 정규화하여 MCP 답변이 출처를
  따라갈 수 있게 합니다.
- **투자자 관점**: 재무 퀄리티, 현금전환, 피어 벤치마킹, 회계 리스크,
  공시 이벤트, DCF 입력 후보를 한 번에 연결합니다.
- **감사인 관점**: 계속기업 징후, 감사인 교체, 감사보수, 비감사보수
  비율, 그룹감사 범위, KAM 주제, 감사절차, 회계정책 변화를 추적합니다.
- **피어그룹 비교**: 한국 업종코드 기반으로 비교회사를 선별하고,
  이익률·부채비율·현금흐름·감사보수·KAM·회계정책을 비교합니다.
- **런타임 MCP 구조**: 비공개 수집 작업과 읽기 전용 MCP serving을
  분리하여, 사용자는 DART API 키 없이 compact runtime dataset을 조회할
  수 있습니다.
- **근거 기반 서술형 응답**: 단순 JSON 나열이 아니라 확인된 사실,
  분석, 다음 확인사항, 공시 출처가 포함된 문장형 응답을 지향합니다.
- **의미 기반 공시 맥락**: 사업보고서의 사업개요·위험·원재료·설비,
  감사보고서, 공시사항, 재무 fact, 회계 주석 topic을 회사·연도 단위로
  묶어 LLM이 문서 종류를 넘나들며 답할 수 있게 합니다.
- **주석 원문 비교**: 리스 등 특정 주석 사례를 지정하거나 동종기업군을
  선택해 비교할 수 있습니다. 원문 excerpt, 위치 정보, 서식 메타데이터,
  출처 상태를 함께 보여주며, 원문 미확보·미검증은 요약으로 위장하지 않고
  명시합니다.
- **동종기업 기준 커스터마이징**: 업종코드 prefix, 명시적 기업 목록,
  adaptive 규칙을 조합할 수 있고, 결과에 실제 선정군과 기준을 남겨 같은
  질문을 재현할 수 있습니다.
- **fail-closed 커버리지**: 릴리스 artifact에서 보고서 종류·섹션·주석
  topic·원문 소스별 실제 가용 여부를 확인합니다. 테스트가 통과했다는
  사실만으로 모든 기업의 모든 원문이 적재됐다고 간주하지 않습니다.

### 공개 저장소와 private core의 경계

공개 저장소에는 MCP 계약, 지원 기능, provenance 모델, 재현 가능한 사용
예제를 공개합니다. 수집 credential, 원문 저장소, enrichment/backfill 작업,
파서 내부 구현, 릴리스 데이터는 별도 private `capitalparser/kreports-core`
저장소를 canonical home으로 삼습니다. 현재 공개 브랜치는 추출이 완료될
때까지의 호환·문서화 snapshot입니다. 따라서 공개 패키지는 인터페이스와
사용법을 보여주지만, 특정 릴리스의 모든 기업·모든 주석 원문 제공을
보장하지 않습니다. 실제 조회 가능 여부는 릴리스 artifact와 source별
상태를 기준으로 판단해야 합니다.

### 두 가지 관점

KReports는 같은 DART 원천 데이터를 두 가지 관점으로 씁니다. 투자자는 빠르게 판단 신호를 보고, 감사/회계 실무자는 근거와 리스크 커버리지를 봅니다.

#### 투자자 관점

회계나 개발을 몰라도 이렇게 물어보면 됩니다. 매수 전 점검이나 보유종목 정기 체크에 맞춰져 있습니다.

| 질문 | KReports가 보는 것 |
|------|------|
| "삼성전자는 아직 좋은 회사야?" | ROE, 영업이익률, 매출성장, 부채비율, FCF, 현금흐름 |
| "카카오 사기 전에 위험한 신호 있어?" | 정정공시, 전기 재작성, Beneish M-Score, 감사인 교체, 감사의견 |
| "이 종목은 동종업계에서 어느 정도야?" | KSIC 업종 기준 P25/P50/P75, 피어 목록 |
| "최근 주주에게 좋은 공시나 희석 위험 있었어?" | 자기주식, 유상증자, CB/BW/EB, 합병/분할, 대규모 계약, 소송 |
| "사업보고서에서 핵심만 뽑아줘" | 사업개요, 위험요소, 경영계획, R&D, 주요계약 |

`get_investor_signals`는 이 모든 것을 한 번에 훑는 첫 화면입니다. 퀄리티 체크, 회계/거버넌스 리스크 점수, 최근 투자자 관련 공시 이벤트, 핵심 takeaways를 한 번에 돌려줍니다.

#### 감사/회계 실무 관점

KReports는 감사 현장에서 출발한 도구입니다. 흩어진 DART 공시를 감사 리스크 단서와 원천 근거로 정리합니다.

| 질문 | KReports가 보는 것 |
|------|------|
| "계속기업 이슈를 놓치고 있지 않나?" | 자본잠식, 2년 연속 영업손실, 과도한 부채, 이자보상배율, 영업CF, 비적정 의견 |
| "전기 숫자가 다음 사업보고서에서 바뀌었나?" | 사업보고서 간 소급 재작성 후보 |
| "감사인이 바뀌었고, 몇 년째 감사 중인가?" | 감사인, 감사의견, 교체 여부, 연속 감사연수 |
| "독립성 검토가 필요한가?" | 감사보수, 비감사보수, NAS ratio |
| "그룹 감사 범위는 어떻게 생겼나?" | 종속회사/관계회사 감사인 매트릭스 |
| "이 회사의 중요한 회계정책은 무엇인가?" | K-IFRS 표준 항목별 주석 본문 |

### 설치

두 가지 방법 중 선택하세요.

---

#### 방법 A: 호스팅 원격 MCP (API 키 불필요)

운영 서비스는 **읽기 전용 Streamable HTTP MCP**입니다.

```text
https://mcp.dartmcp.com/mcp
```

이 주소는 쓰기 가능한 수집 DB가 아니라 검증된 compact runtime artifact만
제공합니다. 공개 읽기 도구 33개만 노출하며, DART API 키·원문 백필·수집기
자격증명은 서비스에 넣지 않습니다.

##### 1. 공개 읽기 전용 접근 — 토큰·OAuth 불필요

이 운영 endpoint는 의도적으로 **공개·무인증·읽기 전용 MCP 서비스**입니다.
URL만 입력하세요. `Authorization` header, API 키, OAuth client, DART API 키를
추가하지 않습니다. `/mcp`는 웹 페이지가 아니므로 브라우저로 직접 여는 것은
유효한 연결 테스트가 아닙니다.

- `/mcp`는 공개 Streamable HTTP MCP endpoint입니다.
- `/healthz`는 `{"ok": true}`만 반환하는 공개 liveness 경로입니다.
- `/readyz`는 reverse proxy로 공개하지 않으며, 운영자 컨테이너 내부 release
  gate입니다.

프로세스는 읽기 전용 파일시스템에서 compact SQLite artifact와 일치하는 manifest만
mount하며, 수집기·DART 자격증명을 거부합니다. proxy는 MCP와 liveness 경로만
받고 request body를 256 KB로 제한합니다. 이는 사용자 계정 시스템이 아니라
공개 데이터 접근입니다. 개인정보·기밀정보·쓰기 지시는 보내지 마세요.

##### 2. 모든 원격 MCP 클라이언트

클라이언트의 **remote / Streamable HTTP MCP** 설정에 다음만 입력하세요.

```json
{
  "url": "https://mcp.dartmcp.com/mcp"
}
```

첫 질문은 `삼성전자 2025년 투자자 신호를 근거와 한계까지 요약해줘`처럼 읽기
전용으로 시작하세요. 정상 클라이언트는 도구 목록을 발견하고 도구 사용 권한을
물을 수 있습니다. 어떤 경우에도 DART API 키를 MCP 클라이언트에 넣지 않습니다.

##### 3. 웹 챗봇 직접 연결 — API 연동이 아닙니다

| 클라이언트 | 입력할 항목 | 모델 비용 부담 |
|---|---|---|
| Claude.ai / Claude 원격 커넥터 | 위 MCP URL, 선택지가 나오면 **인증 없음** | 사용자 Claude 요금제/계정 |
| ChatGPT web | 위 MCP URL, 선택지가 나오면 **인증 없음** | 사용자 ChatGPT 요금제/계정 |
| Codex·Cursor·Claude Desktop 등 MCP 클라이언트 | 위 MCP URL, header 없음 | 각 사용자 클라이언트/계정 |

이 직접 연결에는 KReports OAuth, 공유 토큰, Stripe 결제, OpenAI API 키가
필요하지 않습니다. 특히 ChatGPT에서 MCP를 연결하는 것은 KReports가 서버에서
Responses API를 호출하는 것이 아니므로, 운영자가 사용자의 모델 요청 API 비용을
부담하지 않습니다. 별도 OpenAI API 기반 챗봇을 만들면 그때만 별도의 과금
애플리케이션이 됩니다.

##### ChatGPT web 설정

1. ChatGPT web에서 요금제/워크스페이스가 제공하면 **Developer mode**를 켭니다.
2. **Settings 또는 Workspace settings → Apps → Create**로 이동합니다.
3. 원격 MCP endpoint에 `https://mcp.dartmcp.com/mcp`를 입력하고, 선택지가
   나오면 **인증 없음**을 선택합니다.
4. **Scan Tools** 후 **Create**를 누르고, 새 채팅에서 읽기 전용 질문으로
   확인합니다.
5. workspace admin/owner는 스캔한 app을 구성원에게 publish할 수 있습니다.
   KReports의 tool schema가 바뀌면 action을 refresh·검토한 후 활성화합니다.

ChatGPT 메뉴와 사용 가능 여부는 요금제에 따라 달라집니다. 현재 공식 안내에
따르면 Pro 사용자는 Developer mode에서 read/fetch MCP를 연결할 수 있고, 전체
MCP app 지원은 Business 및 Enterprise/Edu workspace에 순차 제공 중입니다.
[ChatGPT Developer mode 공식 안내](https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt)를 참고하세요.

OAuth는 나중에 사용자별 접근 제어, 취소, 유료 entitlement, 비공개 데이터를
도입할 때의 선택 기능입니다. 공개 읽기 전용 endpoint의 선행조건이 아닙니다.

##### 4. 로컬 Claude Desktop / Claude Code는 별도 방식입니다

다음은 호스팅 endpoint에 접속하는 설정이 아니라 **로컬 stdio** MCP 프로세스를
시작합니다. 로컬 runtime DB가 필요합니다.

```bash
claude mcp add kreports -- uvx --from kreports kreports-mcp
```

로컬 개발에는 이 방식을 쓰고, 호스팅 서비스는 위 원격 endpoint에 자격증명 없이
연결하세요.

##### 5. 운영자 전용 점검

```bash
curl -fsS https://mcp.dartmcp.com/healthz
curl -sS -o /dev/null -w '%{http_code}\n' https://mcp.dartmcp.com/readyz
```

첫 명령은 liveness를 확인합니다. 두 번째는 공개 proxy에서 `404`여야 합니다.
상세 release readiness는 의도적으로 컨테이너 healthcheck에만 열려 있습니다.
공개 MCP 요청 성공을 DB release readiness 증거로 사용하면 안 되며, mount한
artifact 검증과 컨테이너 health 상태로 판정합니다.

---

#### 방법 B: 직접 구축 (데이터를 직접 소유)

로컬 데이터베이스를 직접 구축합니다. 무료 DART API 키가 필요합니다.

```bash
pip install kreports
echo "DART_API_KEY=your_key" > .env

kreports init
kreports sync-companies
kreports collect-seed --size small   # ~350개사, ~20분

kreports serve
```

DART API 키는 [opendart.fss.or.kr](https://opendart.fss.or.kr)에서 무료 발급.

### Claude에게 이렇게 물어보세요

```
"삼성전자 투자자 신호 요약 — 퀄리티, 회계 리스크, 최근 공시 이벤트"
"카카오 사기 전에 최근 공시 이벤트 중 봐야 할 것 정리해줘"
"삼성전자 영업이익률을 반도체 동종업종과 비교해줘"
"SK하이닉스 계속기업 위험 스코어 — 6인자 스코어카드로"
"카카오 최근 5년 감사인 이력 보여줘"
"셀트리온 전기 소급 재작성 있어?"
"POSCO 그룹 종속회사 감사인 매트릭스"
"이 회사 Beneish M-Score — 이익 조작 가능성은?"
"삼성전자와 동종기업의 리스 주석(정책·사용권자산·리스부채)을 원문과 출처 상태까지 비교해줘"
```

### MCP 도구 (공개 33개)

KReports는 자격증명 없이 읽기 전용으로 동작하는 공개 MCP 도구 33개를
제공합니다. `fetch_disclosure_on_demand` 1개는 명시적으로 활성화한 셀프호스트
운영자에게만 제공됩니다. 공개 도구는 투자자와 감사인이 DART에서 반복적으로
확인하던 질문을 기준으로 묶었습니다.

| 영역 | 대표 도구 | 반환 |
|------|-----------|------|
| 회사 검색 | `search_company` | corp_code, 시장, 종목코드, 동명이인 후보 |
| 투자자 1차 점검 | `get_investor_signals`, `get_quality_of_earnings_pack`, `get_dcf_input_candidates` | 퀄리티 체크, 회계 리스크, 공시 이벤트, DCF 입력 후보 |
| 재무·피어 비교 | `get_financial_snapshot`, `compare_to_industry`, `compare_to_industry_multi`, `select_peer_group` | 다개년 재무 fact, KSIC 피어 분위수, 피어그룹 |
| 공시 모니터링 | `search_disclosure_events`, `search_dataset` | 검증된 로컬 릴리스 DB의 공시 이벤트·근거 문서 검색 |
| 감사 위험 | `score_going_concern`, `detect_restatement`, `build_audit_acceptance_pack`, `estimate_audit_hours_proxy` | 계속기업 점수, 전기재작성 후보, 감사수임 위험 pack, 감사시간 proxy |
| 감사인·그룹감사 | `get_audit_history`, `get_subsidiary_auditors`, `get_industry_audit_landscape`, `compare_peer_audit_fees` | 감사인 연속연수, 의견 이력, 그룹 감사인 매트릭스, 보수/NAS 피어 비교 |
| 감사보고서 근거 | `get_audit_report_sections`, `search_audit_report_matters`, `search_audit_procedures`, `get_kam_lifecycle` | 감사보고서 본문, KAM, 감사절차, KAM 연도별 변화 |
| 회계정책 | `get_accounting_policy`, `compare_peer_accounting_policies`, `get_accounting_policy_changes` | K-IFRS 주석, 피어 정책 비교, 정책 변경 후보 |
| 의미 공시·피어 맥락 | `get_business_overview`, `compare_peer_accounting_policies`, `select_peer_group` | 선택형 로컬 DART 증빙 버킷, 설명 가능한 피어 선정, 주석 원문·출처 비교 |

회사명, 종목코드(6자리), corp_code(8자리) 중 아무거나 입력 가능합니다.

### Python 개발자용

```bash
pip install kreports
```

```python
import kreports

snap = kreports.get_financial_snapshot("005930", years=3)
gc = kreports.score_going_concern("005930")
print(f"점수: {gc['score']}/100 ({gc['grade']})")
signals = kreports.get_investor_signals("005930")
```

### 로컬 직접 구축 (셀프호스트)

```bash
pip install kreports
echo "DART_API_KEY=your_key" > .env

kreports init
kreports sync-companies
kreports enrich-market
kreports collect-seed --size small   # ~350개사, ~20분
kreports serve
```

### 원격 HTTP MCP

운영 endpoint와 공개 클라이언트 경계는 [호스팅 원격 MCP](#방법-a-호스팅-원격-mcp-api-키-불필요)를
참고하세요. `serve-http` 로컬 프로세스를 `--allow-unauthenticated`로 외부 인터넷에
노출하면 안 됩니다. 이 옵션은 짧은 loopback 테스트 전용입니다.

### 계속기업 스코어카드

K-IFRS 감사기준 기반 100점 감점 방식:

| 인자 | 감점 | 기준 |
|------|-----:|------|
| 자본잠식 | −30 | 자본총계 < 0 |
| 2년 연속 영업손실 | −20 | 영업이익 < 0 (2년 연속) |
| 부채비율 > 200% | −15 | 부채 / 자본 > 200% |
| 이자보상배율 < 1.0 | −15 | 영업이익 / 이자비용 < 1 |
| 영업 CF 음수 | −10 | 영업활동현금흐름 < 0 |
| 비적정 감사의견 | −10 | 한정 / 부적정 / 의견거절 |

등급: **안정** (80+) / **주의** (60–79) / **경고** (40–59) / **위험** (<40)

### 릴리스 증거와 데이터 커버리지

연도·시장·기능 커버리지는 배포할 SQLite DB 옆의 JSON release artifact에서
확인합니다. 문서의 고정 숫자는 현재 배포 범위를 증명하지 않습니다.

```bash
kreports build-release-manifest --db artifacts/kreports-runtime.db
kreports verify-release-artifact --db artifacts/kreports-runtime.db
```

build는 blocker가 있어도 `release_gate.passed=false`와 정확한 blocker를
기록하고 0으로 종료합니다. verify는 현재 DB의 해시·스키마·인덱스·raw
count·catalog·golden contract·release gate를 다시 계산하며 drift나
blocker가 있으면 non-zero로 종료합니다. 공개 33개 도구 smoke는 격리
프로세스에서 자격증명과 네트워크 쓰기 없이 실제 handler를 실행합니다.
`/readyz`는 사전 검증된 artifact와 WAL·파일·정적 계약 drift를 빠르게
확인합니다. 전체 DB 해시는 파일 identity가 바뀔 때만 다시 계산하고,
공개 33개 도구는 health probe에서 다시 실행하지 않습니다.

투자자 기능은 `investor_core` gate가 통과한 데이터에서만 ready입니다.
회계정책·감사절차·그룹감사 등 감사인 기능은 artifact의 개별 등급에 따라
conditional일 수 있습니다. DCF는 공시 실제값, 명시적 가정, 모델 계산,
분석가 판단을 서로 섞지 않습니다.

### 관리자의 원문 아카이브 운영

5개년 원문 아카이브는 공개 MCP 기능이나 모든 원문 적재 완료 주장과는 별개의
로컬 collector 작업입니다. DART original container/member와 일반 구조 패키지를
Google Drive에 보전하고, 후보 DB 생성 및 runtime artifact promotion은 각각
별도 승인 단계로 둡니다. bounded spool, dry run, 64-shard 재개, Drive 용량
정산, 릴리스 경계는 [Drive-first annual source archive backfill](docs/source-archive-backfill.md)
가이드에서 확인합니다. all-issuer v3는 새 Drive/state root와
`--universe all-annual-issuers`를 사용하며,
`annual_report_issuer_outside_verified_markets`를 `unclassified`로 보전합니다.
KOSPI/KOSDAQ 근거가 없다는 사실은 비상장 증명이 아니며, 역사적 상태의 승격은
가이드가 정한 KRX 및 dated issuer-status 근거 없이는 할 수 없습니다. 공개 MCP
질의는 Google Drive를 호출하지 않습니다.

### 아키텍처

```
kreports/
├── mcp/         MCP stdio + HTTP 서버 (공개 33개 도구)
├── analysis/    Python 공개 API와 근거 기반 응답 레이어
├── collector/   DART API 수집기와 문서 우선 백필 러너
├── processor/   XBRL/XML 파서
├── judge/       위험 플래그 엔진 (Beneish, Going Concern)
├── db/          런타임 fact, evidence, provenance용 SQLAlchemy 모델
└── cli/         수집, readiness 점검, MCP serving용 Typer CLI

dashboard/       Streamlit 분석 대시보드 (선택, 9페이지)
```

### 요구 사항

- Python 3.11+
- [DART OpenAPI 키 (무료 발급)](https://opendart.fss.or.kr)

### 라이선스

Apache 2.0

### 만든 사람

**capitalparser** — Big4 회계법인 7년차 공인회계사. 감사 현장에서 매년 반복하던 DART 수작업을 없애기 위해 만들었습니다.
