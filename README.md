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

#### Option A: Hosted service (no API key needed)

Connect to the pre-built database. No DART key. No data collection. Just add the MCP endpoint.

**Claude Code:**
```bash
claude mcp add kreports -- uvx --from kreports kreports-mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "kreports": {
      "command": "uvx",
      "args": ["--from", "kreports", "kreports-mcp"]
    }
  }
}
```

> Hosted endpoint coming soon. Follow the repo for the release.

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
```

### MCP Tools (32)

KReports exposes 32 catalog-bound MCP tools. The tools are grouped around the
maintenance questions that usually force analysts and auditors back into DART:

| Area | Representative tools | What it returns |
|------|----------------------|-----------------|
| Company lookup | `search_company` | Corp code, market, stock code, name disambiguation |
| Investor first pass | `get_investor_signals`, `get_quality_of_earnings_pack`, `get_dcf_input_candidates` | Quality checks, accounting risk, disclosure events, DCF input candidates |
| Financials and peer benchmarking | `get_financial_snapshot`, `compare_to_industry`, `compare_to_industry_multi`, `select_peer_group` | Multi-year financial facts, KSIC peer percentiles, peer group selection |
| Disclosure monitoring | `search_disclosure_events`, `fetch_disclosure_on_demand`, `search_dataset` | Indexed event search plus optional user-keyed live DART fetches |
| Audit risk | `score_going_concern`, `detect_restatement`, `build_audit_acceptance_pack`, `estimate_audit_hours_proxy` | Going-concern score, restatement candidates, acceptance risk pack, audit-hour proxy |
| Auditor and group audit | `get_audit_history`, `get_subsidiary_auditors`, `get_industry_audit_landscape`, `compare_peer_audit_fees` | Auditor tenure, opinion history, group auditor matrix, audit fee/NAS peer view |
| Audit report evidence | `get_audit_report_sections`, `search_audit_report_matters`, `search_audit_procedures`, `get_kam_lifecycle` | Audit report sections, KAM matters, audit procedures, year-to-year KAM lifecycle |
| Accounting policies | `get_accounting_policy`, `compare_peer_accounting_policies`, `get_accounting_policy_changes` | K-IFRS policy notes, peer policy comparison, policy change candidates |

All tools accept company name, 6-digit stock code, or 8-digit DART corp_code interchangeably.

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

### Remote HTTP MCP (claude.ai web)

```bash
kreports serve-http --port 8765 --token your_bearer_token
# then expose via ngrok, Fly.io, or any HTTPS host
```

Add `https://your-host/mcp` as a custom connector in claude.ai Settings → Integrations.

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
contract drift, or any current named release blocker. `/readyz` uses the same
`public_runtime` predicate.

Investor functions are ready only when `investor_core` passes the manifest
gate. Auditor functions remain conditional where accounting-policy,
audit-procedure, or group-audit grades are degraded. Code/test success does not
override a blocked live-data gate.

DCF packs keep four boundaries visible: source filing actuals, explicit
assumptions, Decimal model mechanics, and analyst judgment. The model does not
turn missing inputs into inferred actuals.

### Architecture

```
kreports/
├── mcp/         MCP stdio + HTTP servers (32 tools)
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

#### 방법 A: 호스팅 서비스 (API 키 불필요)

사전 구축된 데이터베이스에 연결합니다. DART 키도, 데이터 수집도 필요 없습니다.

**Claude Code:**
```bash
claude mcp add kreports -- uvx --from kreports kreports-mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "kreports": {
      "command": "uvx",
      "args": ["--from", "kreports", "kreports-mcp"]
    }
  }
}
```

> 호스팅 엔드포인트 준비 중. 레포를 팔로우하세요.

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
```

### MCP 도구 (32개)

KReports는 catalog에 고정된 MCP 도구 32개를 제공합니다. 투자자와 감사인이
DART에서 반복적으로 확인하던 질문을 기준으로 묶었습니다.

| 영역 | 대표 도구 | 반환 |
|------|-----------|------|
| 회사 검색 | `search_company` | corp_code, 시장, 종목코드, 동명이인 후보 |
| 투자자 1차 점검 | `get_investor_signals`, `get_quality_of_earnings_pack`, `get_dcf_input_candidates` | 퀄리티 체크, 회계 리스크, 공시 이벤트, DCF 입력 후보 |
| 재무·피어 비교 | `get_financial_snapshot`, `compare_to_industry`, `compare_to_industry_multi`, `select_peer_group` | 다개년 재무 fact, KSIC 피어 분위수, 피어그룹 |
| 공시 모니터링 | `search_disclosure_events`, `fetch_disclosure_on_demand`, `search_dataset` | 공시 이벤트 검색, 사용자 API 키 기반 실시간 DART 조회 |
| 감사 위험 | `score_going_concern`, `detect_restatement`, `build_audit_acceptance_pack`, `estimate_audit_hours_proxy` | 계속기업 점수, 전기재작성 후보, 감사수임 위험 pack, 감사시간 proxy |
| 감사인·그룹감사 | `get_audit_history`, `get_subsidiary_auditors`, `get_industry_audit_landscape`, `compare_peer_audit_fees` | 감사인 연속연수, 의견 이력, 그룹 감사인 매트릭스, 보수/NAS 피어 비교 |
| 감사보고서 근거 | `get_audit_report_sections`, `search_audit_report_matters`, `search_audit_procedures`, `get_kam_lifecycle` | 감사보고서 본문, KAM, 감사절차, KAM 연도별 변화 |
| 회계정책 | `get_accounting_policy`, `compare_peer_accounting_policies`, `get_accounting_policy_changes` | K-IFRS 주석, 피어 정책 비교, 정책 변경 후보 |

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

### 원격 HTTP MCP (claude.ai 웹)

```bash
kreports serve-http --port 8765 --token 발급받은_토큰
# ngrok 또는 클라우드에 HTTPS로 노출 후
# claude.ai → Settings → Integrations → URL 등록
```

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
blocker가 있으면 non-zero로 종료합니다. `/readyz`도 동일한
`public_runtime` 의미를 사용합니다.

투자자 기능은 `investor_core` gate가 통과한 데이터에서만 ready입니다.
회계정책·감사절차·그룹감사 등 감사인 기능은 artifact의 개별 등급에 따라
conditional일 수 있습니다. DCF는 공시 실제값, 명시적 가정, 모델 계산,
분석가 판단을 서로 섞지 않습니다.

### 아키텍처

```
kreports/
├── mcp/         MCP stdio + HTTP 서버 (32개 도구)
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
