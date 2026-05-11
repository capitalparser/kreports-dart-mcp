# KReports

**Ask Korean filings like an analyst. KReports turns DART into investor-ready signals for Claude.**

[English](#english) | [한국어](#한국어)

[![PyPI](https://img.shields.io/pypi/v/kreports.svg)](https://pypi.org/project/kreports/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)

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

KReports connects [DART](https://dart.fss.or.kr) (Korea's SEC) to Claude via the [Model Context Protocol](https://modelcontextprotocol.io). It covers 3,900+ KOSPI/KOSDAQ/KONEX listed companies and converts filings into structured financial intelligence.

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

### MCP Tools (12)

| Tool | Best for | What it returns |
|------|----------|-----------------|
| `search_company` | Everyone | Corp code, market, stock code |
| `get_investor_signals` | Investors | Quality checks, accounting risk score, recent investor-relevant disclosure events |
| `get_financial_snapshot` | Investors / analysts | Revenue, OP, NI, FCF, ROIC, CCC by year |
| `compare_to_industry` | Investors / analysts | KSIC P25/P50/P75 vs. peers (single metric) |
| `compare_to_industry_multi` | Investors / analysts | Multi-metric × multi-year P25/P50/P75 matrix + subject percentile. Adaptive ladder (p3→p2) + sector mutual exclusion + opt-in size bucket |
| `get_business_overview` | Investors / auditors | Business report narrative (overview, risk, MD&A) |
| `score_going_concern` | Audit / credit risk | 6-factor 100-pt deduction scorecard + grade |
| `detect_restatement` | Audit / accounting risk | Prior period adjustments across annual filings |
| `get_accounting_policy` | Accounting / audit planning | 15 standard K-IFRS policy items from footnotes |
| `get_audit_history` | Audit / governance | Auditor, opinion, change flag, consecutive years |
| `get_subsidiary_auditors` | Group audit / governance | Group audit matrix across subsidiaries |
| `get_industry_audit_landscape` | Audit / governance | Industry audit market: auditor share (count + asset-weighted), Big4 share, non-qualified opinion rate (N-yr), avg tenure, subject's auditor |

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
kreports collect-all --year-from 2021 --year-to 2025   # all 3,900+ companies

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

### Data coverage

| Entity | Coverage |
|--------|---------|
| Listed companies | 3,900+ (KOSPI + KOSDAQ + KONEX) |
| Financial history | Up to 5 years |
| Auditor records | Opinion, firm, consecutive years |
| Audit fees | Audit + non-audit, NAS ratio |
| Industry benchmarks | KSIC 2/3-digit, 8 metrics |
| Accounting policies | 15 standard K-IFRS items |
| Investor signals | Quality checks, accounting/governance risk, disclosure event categories |

### Architecture

```
kreports/
├── mcp/         MCP stdio + HTTP servers (10 tools)
├── analysis/    Public Python API (11 functions, JSON-safe)
├── collector/   DART API collectors (9 modules)
├── processor/   XBRL/XML parsers
├── judge/       Risk flag engine (Beneish, Going Concern)
├── db/          SQLAlchemy models (8 tables, SQLite)
└── cli/         Typer CLI (17 commands)

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

KReports는 한국 금융감독원 [DART](https://dart.fss.or.kr) 공시 데이터를 [MCP 프로토콜](https://modelcontextprotocol.io)로 Claude에 연결합니다. KOSPI/KOSDAQ/KONEX 상장사 3,900여 개의 공시와 재무 데이터를 투자자가 질문하기 쉬운 인텔리전스로 제공합니다.

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

### MCP 도구 (10개)

| 도구 | 주 사용 관점 | 반환 |
|------|-------------|------|
| `search_company` | 공통 | corp_code, 시장, 종목코드 |
| `get_investor_signals` | 투자자 | 퀄리티 체크·회계 리스크 점수·투자자 관련 최근 공시 이벤트 |
| `get_financial_snapshot` | 투자자 / 애널리스트 | 연도별 매출·영업이익·FCF·ROIC·CCC |
| `compare_to_industry` | 투자자 / 애널리스트 | KSIC 업종 P25/P50/P75 비교 |
| `get_business_overview` | 투자자 / 감사인 | 사업보고서 핵심 섹션 (사업개요·위험·경영계획) |
| `score_going_concern` | 감사 / 신용위험 | 6인자 100점 감점 스코어카드 + 등급 |
| `detect_restatement` | 감사 / 회계위험 | 사업보고서 간 전기 금액 변동 감지 |
| `get_accounting_policy` | 회계 / 감사계획 | K-IFRS 표준 15개 항목 주석 발췌 |
| `get_audit_history` | 감사 / 지배구조 | 감사인·의견·교체·연속연수 이력 |
| `get_subsidiary_auditors` | 그룹감사 / 지배구조 | 연결그룹 종속회사 감사인 매트릭스 |

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

### 데이터 커버리지

| 대상 | 범위 |
|------|------|
| 상장사 | 3,900개+ (KOSPI + KOSDAQ + KONEX) |
| 재무 이력 | 최근 5개년 |
| 감사인 기록 | 감사의견, 감사법인, 연속연수 |
| 감사보수 | 감사보수 + 비감사보수, NAS ratio |
| 업종 벤치마킹 | KSIC 2/3자리, 8개 지표 |
| 회계정책 | K-IFRS 표준 15개 항목 |
| 투자자 신호 | 퀄리티 체크, 회계/거버넌스 리스크, 공시 이벤트 분류 |

### 아키텍처

```
kreports/
├── mcp/         MCP stdio + HTTP 서버 (10개 도구)
├── analysis/    Python 공개 API (11개 함수, JSON-safe)
├── collector/   DART API 수집기 (9개 모듈)
├── processor/   XBRL/XML 파서
├── judge/       위험 플래그 엔진 (Beneish, Going Concern)
├── db/          SQLAlchemy 모델 (8개 테이블, SQLite)
└── cli/         Typer CLI (17개 명령)

dashboard/       Streamlit 분석 대시보드 (선택, 9페이지)
```

### 요구 사항

- Python 3.11+
- [DART OpenAPI 키 (무료 발급)](https://opendart.fss.or.kr)

### 라이선스

Apache 2.0

### 만든 사람

**capitalparser** — Big4 회계법인 7년차 공인회계사. 감사 현장에서 매년 반복하던 DART 수작업을 없애기 위해 만들었습니다.
