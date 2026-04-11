# KReports

**Korean Financial Intelligence MCP**

> DART를 매년 수작업으로 뒤지던 Big4 감사인이 만든 재무 인텔리전스 MCP 서버.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![MCP](https://img.shields.io/badge/MCP-compatible-purple.svg)](https://modelcontextprotocol.io)

---

## What is KReports?

KReports는 한국 금융감독원 [DART](https://dart.fss.or.kr) 공시 데이터를 기반으로 감사인과 투자자를 위한 재무 분석 도구를 제공합니다.

- **MCP 서버**: Claude Desktop / Claude Code에 연결하여 자연어로 재무 분석
- **CLI**: 터미널에서 데이터 수집 + 분석
- **대시보드**: Streamlit 기반 시각적 분석 (선택 사항)
- **Python API**: `import kreports`로 프로그래밍 방식 사용

### 핵심 기능

| 기능 | 감사인 | 투자자 | 설명 |
|------|:------:|:------:|------|
| 재무 스냅샷 | O | O | 매출·영업이익·FCF·ROIC·CCC 연도별 추세 |
| 업종 벤치마킹 | O | O | KSIC 업종 내 P25/P50/P75 분포 + 박스플롯 |
| 계속기업 스코어 | O | O | 6인자 100점 감점 스코어카드 |
| 소급 재작성 감지 | O | - | 사업보고서 간 전기 금액 변동 자동 탐지 |
| 회계정책 추출 | O | - | 15개 표준 item_key별 주석 발췌 + 연도별 변화 추적 |
| 감사인 이력 | O | O | 교체·의견·연속연수 타임라인 |
| 종속회사 감사인 | O | - | 연결그룹 감사인 매트릭스 |
| Beneish M-Score | O | O | 이익 조작 가능성 지표 |

---

## Quick Start

### 1. 설치

```bash
pip install kreports
```

또는 소스에서:

```bash
git clone https://github.com/capitalparser/kreports.git
cd kreports
pip install -e .
```

### 2. DART API 키 설정

[DART OpenAPI](https://opendart.fss.or.kr) 에서 무료 API 키를 발급받으세요 (1분).

```bash
echo "DART_API_KEY=your_api_key_here" > .env
```

### 3. 초기 데이터 수집

```bash
# DB 초기화 + 상장사 목록 동기화
kreports init
kreports sync-companies
kreports enrich-market

# 핵심 기업 재무데이터 수집 (~20분, 350사 Q4)
kreports collect-seed --size small
```

### 4. 사용

**MCP 서버 (Claude Desktop / Claude Code)**:
```bash
kreports serve
```

**Python API**:
```python
import kreports

# Samsung 재무 스냅샷
snap = kreports.get_financial_snapshot("005930", years=3)
print(snap["rows"])

# 계속기업 위험 스코어
gc = kreports.score_going_concern("005930")
print(f"Score: {gc['score']}/100 ({gc['grade']})")

# 업종 벤치마킹
bench = kreports.get_industry_aggregates("264", metric="영업이익률")
print(f"P50: {bench['quantiles']['p50']}%")
```

**대시보드** (선택):
```bash
pip install kreports[dashboard]
streamlit run dashboard/app.py
```

---

## Claude Desktop 연결

`~/.claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kreports": {
      "command": "kreports",
      "args": ["serve"],
      "env": {
        "DART_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

연결 후 Claude에게 이렇게 말하세요:

> "삼성전자 최근 3년 재무 스냅샷 보여줘"
> "SK하이닉스 계속기업 위험 스코어 확인해줘"
> "삼성전자와 동종업종 영업이익률 비교해줘"

---

## MCP 도구 (8개)

| 도구 | 입력 | 설명 |
|------|------|------|
| `search_company` | 회사명 / 종목코드 | DART 등록 상장사 검색 |
| `get_financial_snapshot` | company, years | 연도별 핵심 재무 + 자본배분 지표 |
| `score_going_concern` | company | 6인자 계속기업 스코어카드 |
| `detect_restatement` | company, threshold | 소급 재작성 감지 |
| `get_accounting_policy` | company, year | 회계정책 15개 항목 추출 |
| `get_audit_history` | company | 감사인·의견·연속연수 이력 |
| `get_subsidiary_auditors` | company | 종속회사 감사인 매트릭스 |
| `compare_to_industry` | company, metric | 동종업종 벤치마킹 (P25/P50/P75) |

---

## 대시보드 스크린샷

### 재무 요약
![Financial Summary](docs/images/financial_summary.png)

### 업종 벤치마킹
![Industry Benchmark](docs/images/industry_benchmark.png)

### 위험 신호 (Going Concern Scorecard)
![Risk Signals](docs/images/risk_signals.png)

### 감사인 이력
![Auditor History](docs/images/auditor_history.png)

### 회계정책 현황
![Accounting Policy](docs/images/accounting_policy.png)

---

## CLI 명령어

```
kreports init                 # DB 초기화
kreports serve                # MCP stdio 서버 실행
kreports sync-companies       # 상장사 목록 동기화
kreports enrich-market        # 시장구분·업종코드 보완
kreports collect-seed         # 핵심 기업 재무 자동 수집
kreports collect <종목코드>    # 단일 종목 수집
kreports collect-all          # 전체 상장사 배치 수집
kreports collect-disclosures  # 공시 목록 수집
kreports collect-auditors     # 감사인 이력 수집
kreports collect-audit-fees   # 감사보수 수집
kreports collect-policies     # 회계정책 영속화
kreports compute-flags        # 판단 플래그 재계산
kreports show <종목코드>       # 재무지표 조회
```

---

## 아키텍처

```
kreports/                   # pip 패키지
├── analysis/               # 공개 API (dict 반환, JSON-safe)
│   ├── api.py              # 10개 분석 함수
│   └── queries.py          # DB 쿼리 레이어 (Streamlit 무의존)
├── mcp/                    # MCP stdio 서버 (8개 도구)
├── cli/                    # Typer CLI (17개 명령)
├── db/                     # SQLAlchemy 모델 (8개 테이블)
├── collector/              # DART API 수집기 (9개 모듈)
├── processor/              # XBRL/XML 파서 (8개 모듈)
└── judge/                  # 위험 플래그 엔진 (Beneish, Going Concern)
```

**DB 테이블**: Company, Financial, FinancialFact, Disclosure, Auditor, AuditFee, AccountingPolicyItem, FetchLog

---

## 분석 지표

### 계속기업 스코어카드 (100점 감점)

| 인자 | 감점 | 기준 |
|------|------|------|
| 자본잠식 | -30 | 자본총계 < 0 |
| 2년 연속 영업손실 | -20 | 최근 2년 영업이익 < 0 |
| 부채비율 > 200% | -15 | 부채/자본 > 200% |
| 이자보상배율 < 1.0 | -15 | 영업이익/이자비용 < 1 |
| 영업CF 음수 | -10 | 최근 연도 영업활동현금흐름 < 0 |
| 비적정 감사의견 | -10 | 한정/부적정/의견거절 |

등급: 안정(80+) / 주의(60-79) / 경고(40-59) / 위험(<40)

### 업종 벤치마킹 지표 (8개)

영업이익률, 순이익률, ROE, ROA, 부채비율, 자기자본비율, 매출성장률, Beneish M-Score

KSIC 업종코드 2자리(대분류) 또는 3자리(중분류)로 peer 그룹 자동 매칭.

---

## 데이터 수집 전략

```
kreports collect-seed --size small     # KOSPI200+KOSDAQ150, ~20분
kreports collect-seed --size medium    # KOSPI 전체, ~50분
kreports collect-seed --size full      # 전체 상장사, ~11시간
```

- **Q4 우선**: 벤치마킹은 연간(Q4) 데이터만 사용. `--annual-only` 기본 활성화.
- **업종 분산**: KSIC 2자리 기준 라운드 로빈 선택. 모든 업종에 최소 1개 기업 보장.
- **중복 방지**: 이미 수집된 기업-연도-분기는 자동 건너뜀.
- **DART API 제한**: 일 10,000건. `collect-seed small`은 ~1,050건 사용.

---

## 요구 사항

- Python 3.11+
- DART OpenAPI 키 ([무료 발급](https://opendart.fss.or.kr))

---

## 라이선스

Apache License 2.0. 자유롭게 사용, 수정, 배포 가능.

---

## 기여

이슈 리포트와 풀 리퀘스트를 환영합니다.

---

## 만든 사람

**capitalparser** — Big4 회계법인 7년차 공인회계사. 감사 현장에서 매년 반복하던 DART 수작업을 자동화하기 위해 만들었습니다.
