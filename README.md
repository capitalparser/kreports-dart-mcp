# KReports

**DART → Claude. Korean financial intelligence as an MCP server.**

[English](#english) | [한국어](#한국어)

[![PyPI](https://img.shields.io/pypi/v/kreports.svg)](https://pypi.org/project/kreports/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)

---

<a id="english"></a>

## English

> Built by a Big4 auditor who spent too many hours manually combing through DART filings every audit season.

### The problem

Every audit season, the same ritual: open DART, search company, click through filings, copy numbers to Excel, repeat for 30 subsidiaries. 3 hours of lookup for one going concern assessment.

KReports eliminates that loop. Ask Claude. Get the answer.

### What it does

KReports connects [DART](https://dart.fss.or.kr) (Korea's SEC) to Claude via the [Model Context Protocol](https://modelcontextprotocol.io). Every listed Korean company — 3,900+ on KOSPI/KOSDAQ — available as structured financial intelligence.

**What web search can't do, KReports can:**

| Query | Web Search | KReports |
|-------|:----------:|:--------:|
| "Samsung Electronics revenue 2024" | ✓ (news summary) | ✓ (DART original) |
| "Compare all KOSDAQ biotech debt ratios" | ✗ | ✓ |
| "Has this company changed auditors in 5 years?" | ✗ | ✓ |
| "Going concern risk score — 6-factor K-IFRS audit standard" | ✗ | ✓ |
| "Detect prior period restatements vs. prior annual report" | ✗ | ✓ |
| "Subsidiary auditor matrix for the whole group" | ✗ | ✓ |
| "NAS ratio (non-audit fees / audit fees)" | ✗ | ✓ |
| "Industry P25/P50/P75 for operating margin (KSIC)" | ✗ | ✓ |

### One-line setup

No Python environment needed. `uvx` handles everything:

```bash
# Claude Code
claude mcp add kreports -e DART_API_KEY=your_key -- uvx --from kreports kreports-mcp
```

Or add to `~/Library/Application Support/Claude/claude_desktop_config.json` for Claude Desktop:

```json
{
  "mcpServers": {
    "kreports": {
      "command": "uvx",
      "args": ["--from", "kreports", "kreports-mcp"],
      "env": {
        "DART_API_KEY": "your_key_here"
      }
    }
  }
}
```

Get a free DART API key at [opendart.fss.or.kr](https://opendart.fss.or.kr).

### Then ask Claude

```
"SK Hynix going concern risk — 6-factor scorecard"
"Compare Samsung Electronics operating margin to semiconductor peers"
"Show auditor history for Kakao for the past 5 years"
"Has Celltrion restated any prior period figures?"
"Subsidiary auditor matrix for POSCO group"
"Beneish M-Score for this company — earnings manipulation risk"
```

### MCP Tools (9)

| Tool | Input | What it returns |
|------|-------|-----------------|
| `search_company` | name / stock code | Corp code, market, stock code |
| `get_financial_snapshot` | company, years | Revenue, OP, NI, FCF, ROIC, CCC by year |
| `score_going_concern` | company | 6-factor 100-pt deduction scorecard + grade |
| `detect_restatement` | company, threshold | Prior period adjustments across annual filings |
| `get_accounting_policy` | company, year | 15 standard K-IFRS policy items from footnotes |
| `get_audit_history` | company | Auditor, opinion, change flag, consecutive years |
| `get_subsidiary_auditors` | company | Group audit matrix across subsidiaries |
| `compare_to_industry` | company, metric | KSIC P25/P50/P75 vs. peers |
| `get_business_overview` | company, year | Business report narrative (overview, risk, MD&A) |

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

### Architecture

```
kreports/
├── mcp/         MCP stdio + HTTP servers (9 tools)
├── analysis/    Public Python API (10 functions, JSON-safe)
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

> 감사 시즌마다 DART를 수작업으로 뒤지던 Big4 감사인이 만들었습니다.

### 문제

감사 시즌마다 같은 반복: DART 열고, 회사 검색, 공시 클릭, 숫자 엑셀 복붙, 종속회사 30개 반복. 계속기업 검토 하나에 3시간.

KReports는 그 루틴을 없앱니다. Claude에게 물어보면 됩니다.

### 무엇을 하나

KReports는 한국 금융감독원 [DART](https://dart.fss.or.kr) 공시 데이터를 [MCP 프로토콜](https://modelcontextprotocol.io)로 Claude에 연결합니다. KOSPI/KOSDAQ 상장사 3,900여 개의 재무 데이터를 구조화된 인텔리전스로 제공합니다.

**웹 검색이 못 하는 것, KReports는 합니다:**

| 질문 | 웹 검색 | KReports |
|------|:-------:|:--------:|
| "삼성전자 2024년 매출" | ✓ (언론 요약) | ✓ (DART 원문) |
| "KOSDAQ 바이오 전체 부채비율 비교" | ✗ | ✓ |
| "최근 5년 감사인 교체 이력" | ✗ | ✓ |
| "계속기업 위험 — K-IFRS 6인자 스코어" | ✗ | ✓ |
| "전기 소급 재작성 감지" | ✗ | ✓ |
| "그룹 종속회사 감사인 매트릭스" | ✗ | ✓ |
| "비감사보수 비율 (NAS ratio)" | ✗ | ✓ |
| "동종업종 영업이익률 P25/P50/P75" | ✗ | ✓ |

### 한 줄 설치

Python 환경 설정 불필요. `uvx`가 모든 것을 처리합니다:

```bash
# Claude Code
claude mcp add kreports -e DART_API_KEY=your_key -- uvx --from kreports kreports-mcp
```

Claude Desktop은 `~/Library/Application Support/Claude/claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "kreports": {
      "command": "uvx",
      "args": ["--from", "kreports", "kreports-mcp"],
      "env": {
        "DART_API_KEY": "발급받은_키"
      }
    }
  }
}
```

DART API 키는 [opendart.fss.or.kr](https://opendart.fss.or.kr)에서 무료 발급.

### Claude에게 이렇게 물어보세요

```
"SK하이닉스 계속기업 위험 스코어 — 6인자 스코어카드로"
"삼성전자 영업이익률을 반도체 동종업종과 비교해줘"
"카카오 최근 5년 감사인 이력 보여줘"
"셀트리온 전기 소급 재작성 있어?"
"POSCO 그룹 종속회사 감사인 매트릭스"
"이 회사 Beneish M-Score — 이익 조작 가능성은?"
```

### MCP 도구 (9개)

| 도구 | 입력 | 반환 |
|------|------|------|
| `search_company` | 회사명 / 종목코드 | corp_code, 시장, 종목코드 |
| `get_financial_snapshot` | company, years | 연도별 매출·영업이익·FCF·ROIC·CCC |
| `score_going_concern` | company | 6인자 100점 감점 스코어카드 + 등급 |
| `detect_restatement` | company, threshold | 사업보고서 간 전기 금액 변동 감지 |
| `get_accounting_policy` | company, year | K-IFRS 표준 15개 항목 주석 발췌 |
| `get_audit_history` | company | 감사인·의견·교체·연속연수 이력 |
| `get_subsidiary_auditors` | company | 연결그룹 종속회사 감사인 매트릭스 |
| `compare_to_industry` | company, metric | KSIC 업종 P25/P50/P75 비교 |
| `get_business_overview` | company, year | 사업보고서 핵심 섹션 (사업개요·위험·경영계획) |

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

### 아키텍처

```
kreports/
├── mcp/         MCP stdio + HTTP 서버 (9개 도구)
├── analysis/    Python 공개 API (10개 함수, JSON-safe)
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
