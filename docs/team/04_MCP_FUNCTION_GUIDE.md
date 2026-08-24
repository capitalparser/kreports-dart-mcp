# KReports MCP 기능 상세 설명

## 1. MCP란 무엇인가

MCP(Model Context Protocol, 챗봇이 외부 데이터와 기능을 정해진 방식으로
찾고 호출할 수 있게 하는 연결 규격)는 챗봇과 KReports 사이의 약속입니다.

일반 챗봇이 사용자의 문장을 보고 임의로 데이터베이스를 뒤지는 대신,
KReports는 이름·입력항목·결과형식이 정해진 Tool(도구, 하나의 검색 또는
분석 기능)을 공개합니다.

예:

```text
사용자 질문
“삼성전자의 최근 3개년 재무상태와 감사인 변경 여부를 알려줘.”

챗봇의 내부 흐름
1. search_company로 정확한 회사 확인
2. get_financial_snapshot으로 재무정보 조회
3. get_audit_history로 감사인·감사의견 조회
4. 결과와 원문 출처를 사용자 언어로 정리
```

## 2. MCP가 하지 않는 일

- MCP 자체가 회계 판단을 새로 만드는 것은 아닙니다.
- MCP는 데이터베이스를 직접 수정하지 않습니다.
- Tool이 존재한다고 모든 회사·연도의 데이터가 확보됐다는 뜻은 아닙니다.
- HTTP 연결이 성공했다고 분석 결과가 정확하다는 뜻은 아닙니다.
- 챗봇의 자연스러운 문장이 원문 근거를 대신하지 않습니다.

KReports MCP는 기본적으로 읽기 전용(Read-only, 조회는 가능하지만 공용
데이터를 수정하지 않는 상태)입니다.

## 3. MCP 연결 방식

### 3.1 로컬 stdio 방식

stdio(Standard Input/Output, 로컬 프로그램끼리 표준 입력과 출력으로
통신하는 방식)는 VS Code, Cursor, Claude Desktop 또는 Claude Code가
KReports 프로그램을 직접 실행하는 방식입니다.

장점:

- 로컬 테스트가 쉬움
- 별도 외부 서버 없이 실행 가능

### 3.2 원격 HTTP 방식

HTTP MCP는 사내 챗봇이나 원격 클라이언트가 서버 주소의 `/mcp` 경로로
KReports를 호출하는 방식입니다.

장점:

- 여러 사용자가 같은 읽기 전용 데이터와 기능 사용
- 중앙에서 버전과 접근권한 관리 가능

운영 서버에는 수집용 DART API Key를 넣지 않는 것이 원칙입니다.

### 3.3 MCP v2 사이드카

MCP v2 Sidecar(기존 MCP 서버와 별도로 시험 운영하는 새 프로토콜용 서버)는
사용자 선택 질문, 대화 상태와 다음 5개 보기 같은 기능을 확장합니다.
기본 MVP 서버와 별도 환경에서 검증합니다.

비개발자 팀원이 처음부터 MCP 버전 차이를 이해할 필요는 없습니다. 중요한
점은 두 서버 모두 같은 KReports 분석 결과를 사용해야 한다는 것입니다.

## 4. 공통 사용자 결과 원칙

MCP Tool의 내부 결과는 구조화 데이터일 수 있지만, 사용자에게는 다음
순서로 보여줍니다.

1. 직접 답변
2. 필요한 핵심 수치·문구
3. 짧은 표
4. DART 원문 링크
5. 결론에 영향을 주는 자료 한계
6. 다음 확인사항

회사 목록은 기본적으로 5개씩 보여줍니다. `다음 5개`를 요청하면 이미
저장된 같은 모집단과 순서를 사용해야 하며 Peer를 다시 선정하면 안 됩니다.

## 5. 현재 34개 Tool 개요

### A. 회사 식별·통합 검색

| Tool | 쉬운 설명 | 질문 예시 |
|---|---|---|
| `search_company` | 회사명·종목코드로 정확한 DART 회사를 찾음 | “삼성전자 회사코드 찾아줘” |
| `get_business_overview` | 사업보고서의 사업 내용·위험·R&D·계약 등을 요약 | “이 회사는 무엇으로 돈을 버나?” |
| `get_semantic_company_context` | 한 회사·연도의 사업보고서·감사보고서 패키지·공시·재무 근거를 하나의 문맥으로 조합 | “2025년 회사 전체 공시 근거를 묶어줘” |
| `search_dataset` | 여러 내부 데이터 영역을 공통 조건으로 검색 | “2025년 수익인식 KAM 회사 찾아줘” |
| `fetch_disclosure_on_demand` | 사용자가 제공한 DART Key로 특정 미수집 공시를 1회 조회 | “이 접수번호 원문을 지금 확인해줘” |

### B. 재무·투자 분석

| Tool | 쉬운 설명 | 질문 예시 |
|---|---|---|
| `get_financial_snapshot` | 최근 연도의 매출·이익·자산·부채·현금흐름과 주요 비율을 조회 | “최근 3년 재무 추이를 보여줘” |
| `score_going_concern` | 손실·자본잠식·부채·현금흐름·감사의견으로 계속기업 위험을 점검 | “계속기업 위험요인이 있나?” |
| `detect_restatement` | 다음 연도 보고서의 전기금액과 이전 보고서 금액을 비교해 재작성 후보를 찾음 | “과거 재무수치가 바뀐 적 있나?” |
| `get_investor_signals` | 수익성·현금흐름·회계위험·공시사건을 한 번에 요약 | “투자 전 1차 위험신호를 알려줘” |
| `get_quality_of_earnings_pack` | 이익과 현금흐름의 질, 발생액과 지속가능성을 묶어 점검 | “이익의 질이 괜찮은가?” |
| `get_dcf_input_candidates` | DCF에 사용할 수 있는 실제 재무수치 후보와 자료 한계를 제공 | “DCF 입력값 후보를 보여줘” |
| `build_dcf_model_pack` | 명시한 가정으로 DCF 계산 묶음을 작성 | “WACC 8%, 성장률 2%로 계산해줘” |
| `search_disclosure_events` | 증자·CB·자기주식·계약·소송·정정 등 공시사건 검색 | “최근 희석 가능성이 있는 공시는?” |

### C. 비교회사와 업종 분석

| Tool | 쉬운 설명 | 질문 예시 |
|---|---|---|
| `compare_to_industry` | 한 재무지표를 동종업계와 비교 | “영업이익률이 업종에서 어느 수준인가?” |
| `select_peer_group` | 업종·규모·자료확보 기준으로 비교회사를 선정하고 이유를 제공 | “왜 이 회사들이 Peer인가?” |
| `compare_to_industry_multi` | 여러 연도·여러 재무지표를 한 번에 업종과 비교 | “5년간 수익성·부채·성장률을 비교해줘” |
| `compare_peer_audit_fees` | 감사보수·감사시간·비감사보수를 Peer와 비교 | “감사시간이 동종사보다 적은가?” |
| `compare_peer_risk_profile` | 현금흐름·발생액·정정공시 등 위험신호를 Peer와 비교 | “회계위험이 Peer보다 높은가?” |
| `compare_peer_accounting_policies` | 같은 회계정책 주제를 Peer 회사 간 비교 | “수익인식 정책을 Peer와 비교해줘” |
| `compare_peer_accounting_notes` | 재무제표 주석 원문을 회사별로 나란히 비교 | “리스 주석 원문을 5개사 비교해줘” |
| `compare_peer_kam_topics` | Peer 회사의 KAM 주제와 감사대응 문구를 비교 | “동종업계 매출 KAM은 어떤가?” |
| `compare_peer_audit_report_matters` | 강조·기타·계속기업·의견근거 문단을 Peer와 비교 | “Peer 중 강조사항이 있는 회사는?” |
| `compare_peer_audit_procedures` | KAM의 감사절차 유형을 Peer와 비교 | “매출 KAM에서 어떤 감사절차가 흔한가?” |
| `get_industry_audit_landscape` | 업종의 감사인·감사의견·보수·KAM 등 감사환경을 종합 | “이 업종 감사시장 구조를 보여줘” |

### D. 감사보고서 패키지·감사근거 분석

| Tool | 쉬운 설명 | 질문 예시 |
|---|---|---|
| `get_accounting_policy` | 감사보고서 패키지의 주석에서 특정 회계정책 원문을 조회 | “수익인식 정책 원문을 보여줘” |
| `get_audit_history` | 연도별 감사인·감사의견·감사인 변경 이력을 조회 | “최근 5년 감사인이 바뀌었나?” |
| `get_subsidiary_auditors` | 종속·관계회사별 감사인과 중요도를 보여줌 | “주요 자회사 감사인은 누구인가?” |
| `search_audit_report_matters` | 강조사항·기타사항·계속기업 문단을 회사·업종별 검색 | “계속기업 문단이 있는 회사 찾아줘” |
| `search_audit_procedures` | KAM에서 추출한 감사절차를 주제·방법·키워드로 검색 | “매출 cutoff 관련 감사절차를 찾아줘” |
| `get_kam_lifecycle` | 한 회사의 KAM 주제가 여러 해 동안 어떻게 변했는지 추적 | “최근 5년 KAM 변화는?” |
| `get_accounting_policy_changes` | 회계정책·추정·판단 문구의 연도별 변화를 비교 | “수익인식 정책이 바뀌었나?” |
| `get_audit_report_sections` | 감사보고서의 의견·KAM·강조·기타 문단 원문을 조회 | “감사보고서 주요 문단을 보여줘” |
| `estimate_audit_hours_proxy` | 공개된 회사·감사 자료로 필요한 감사시간 규모를 참고 추정 | “감사시간 규모를 1차로 추정해줘” |
| `build_audit_acceptance_pack` | 수임·유지 검토에 필요한 재무·감사·공시 위험 근거를 묶음 | “감사수임 검토팩을 만들어줘” |

## 6. 각 Tool의 상세 설명

### 6.1 `search_company`

**목적:** 사용자의 회사 표현을 정확한 DART 회사로 바꿉니다.

입력 예:

- 회사명
- 6자리 종목코드
- 8자리 DART 회사코드

결과 예:

- 회사명
- 종목코드
- DART 회사코드
- 시장
- 동명이인 후보

다른 회사 단위 Tool을 부르기 전에 먼저 사용합니다.

### 6.2 `get_financial_snapshot`

**목적:** 회사의 최근 재무상태와 성과를 빠르게 확인합니다.

주요 결과:

- 매출
- 영업이익
- 순이익
- 자산·부채·자본
- 영업현금흐름
- 잉여현금흐름
- 수익성·운전자본 지표
- 연결/별도 기준

주의:

- 연도와 단위를 확인해야 합니다.
- 연결이 없을 때 별도를 사용한 경우 이를 표시해야 합니다.
- 재무제표의 기본 원천과 접수번호를 추적해야 합니다.

### 6.3 `score_going_concern`

**목적:** 계속기업 위험을 자동 결론이 아니라 1차 확인표로 제공합니다.

확인 요소:

- 자본잠식
- 연속 영업손실
- 높은 부채비율
- 낮은 이자보상능력
- 음의 영업현금흐름
- 비적정 감사의견

주의:

- 점수는 감사인의 계속기업 판단을 대체하지 않습니다.
- 감사보고서 패키지의 계속기업 문단을 함께 확인해야 합니다.

### 6.4 `detect_restatement`

**목적:** 동일한 전기 수치가 다음 보고서에서 달라졌는지 찾습니다.

예:

```text
2024년 보고서의 2024년 매출
vs
2025년 보고서에 비교표시된 2024년 매출
```

차이가 있다고 모두 오류수정은 아닙니다. 연결범위 변경, 표시변경,
회계정책 변경 등 원인을 원문에서 확인해야 합니다.

### 6.5 `get_accounting_policy`

**목적:** 수익인식·리스·재고·유형자산·금융상품 등의 회계정책 원문을
조회합니다.

기본 원천:

> 감사보고서 패키지에 첨부된 재무제표 주석

중요:

현재 데이터에 사업보고서에서 추출한 레거시 정책이 남아 있을 수 있습니다.
Tool 결과에서 실제 보고서 종류와 접수번호를 반드시 확인하고, 감사보고서
패키지가 없을 때만 대체자료임을 표시해야 합니다.

### 6.6 `get_audit_history`

**목적:** 연도별 감사인, 감사의견, 감사인 변경과 연속 감사연수를 확인합니다.

확인사항:

- 연결·별도 구분
- 감사의견 원문
- 감사인명 표준화
- 접수번호

### 6.7 `get_subsidiary_auditors`

**목적:** 그룹 내 주요 구성회사의 감사인을 확인합니다.

주요 결과:

- 회사명
- 관계
- 지분율
- 자산·매출 기여도
- 감사인
- 감사의견
- 중요 구성회사 여부

대형 그룹은 결과가 많으므로 중요회사와 상위 회사부터 표시합니다.

### 6.8 `compare_to_industry`

**목적:** 한 지표의 업종 내 위치를 봅니다.

예:

- 영업이익률
- ROE
- 부채비율
- 매출성장률

회사 수가 적으면 분위수 해석이 불안정할 수 있으므로 표본 수를 함께 봅니다.

### 6.9 `get_business_overview`

**목적:** 사업보고서 본문을 이용해 회사의 사업과 위험을 이해합니다.

기본 원천:

- 사업의 내용
- 제품·서비스
- 위험관리
- 연구개발
- 주요 계약

재무제표 주석의 기본 원천으로 사용하지 않습니다.

### 6.10 `get_semantic_company_context`

**목적:** 한 회사·한 연도의 여러 공시 근거를 서로 구분된 상태로 묶습니다.

포함 가능한 근거:

- 사업보고서 본문
- 감사보고서 패키지
- 재무제표·주석
- 수시공시
- 재무정보

서로 다른 원천을 한 문장처럼 섞지 않고 Source ID와 접수번호를 유지해야
합니다.

### 6.11 `get_investor_signals`

**목적:** 수익성·현금흐름·회계위험·최근 공시사건을 1차로 묶어 봅니다.

투자권유가 아니라 추가 확인사항을 찾는 기능입니다.

### 6.12 `select_peer_group`

**목적:** 비교회사 선정 기준과 실제 적용 결과를 투명하게 보여줍니다.

확인할 기준:

- 사업연도
- 연결·별도
- 업종 범위
- 회사 규모
- 필요한 자료 확보 여부
- 직접 포함·제외 회사
- 실제 표시 순서

“가장 유사한 회사”라고 표현하려면 실제로 어떤 기준으로 유사도를 계산했는지
근거가 있어야 합니다.

### 6.13 `compare_to_industry_multi`

**목적:** 여러 연도와 여러 지표를 한 번에 업종과 비교합니다.

시계열(시간의 흐름에 따른 변화)과 상대위치를 함께 볼 때 사용합니다.

### 6.14 `compare_peer_audit_fees`

**목적:** 감사보수와 감사시간을 Peer와 비교합니다.

확인사항:

- 계약과 실제 구분
- 감사·비감사보수 구분
- 단위
- 비교연도
- 자료 확보 상태

### 6.15 `compare_peer_risk_profile`

**목적:** 발생액·현금흐름·정정공시 등의 위험신호를 Peer와 비교합니다.

위험신호가 높다고 오류가 확정되는 것은 아닙니다.

### 6.16 `compare_peer_accounting_policies`

**목적:** 같은 회계정책 주제의 실제 문구를 Peer 회사와 비교합니다.

기본 원천은 각 회사의 감사보고서 패키지 주석입니다. 사업보고서 기반 문구가
섞이면 원천 상태를 분리해 표시해야 합니다.

### 6.17 `compare_peer_accounting_notes`

**목적:** 특정 주석을 회사별로 나란히 비교합니다.

예:

- 수익
- 리스
- 금융상품
- 특수관계자
- 충당부채·우발사항
- 손상
- 종속기업
- 후속사건

전체 주석과 일부 문구를 구분하고, 원문을 찾지 못한 것을 공시하지 않은
것으로 단정하지 않습니다.

### 6.18 `compare_peer_kam_topics`

**목적:** Peer의 KAM 주제·선정 이유·감사대응을 비교합니다.

사업보고서의 KAM 요약이 아니라 감사보고서 패키지 본문을 우선합니다.

### 6.19 `compare_peer_audit_report_matters`

**목적:** 강조사항·기타사항·계속기업·감사의견 근거를 Peer와 비교합니다.

이는 수임·유지 검토의 근거자료이며 최종 감사판단을 자동화하지 않습니다.

### 6.20 `search_dataset`

**목적:** 여러 데이터 영역에서 회사·연도·업종·키워드 조건으로 검색합니다.

먼저 어떤 데이터 영역을 검색했는지 확인해야 합니다. 검색 결과가 없다는
사실은 원 공시에 내용이 없다는 뜻이 아닐 수 있습니다.

### 6.21 `fetch_disclosure_on_demand`

**목적:** 로컬에 없는 특정 수시공시를 사용자의 DART Key로 요청 범위에서
조회합니다.

보안 원칙:

- 사용자 Key 저장 금지
- 로그 출력 금지
- 응답 노출 금지
- 서버 수집용 Key 사용 금지
- 기능별 Cache 정책 명시

### 6.22 `search_audit_report_matters`

**목적:** 감사보고서 패키지의 강조·기타·계속기업 문단을 검색합니다.

회사 하나를 찾는 질문과 업종 전체에서 회사를 찾는 질문 모두에 사용합니다.

### 6.23 `search_audit_procedures`

**목적:** KAM 감사대응 문단에서 구체적인 감사절차를 검색합니다.

예:

- 내부통제 테스트
- 표본 거래 검사
- 외부조회
- 결산일 전후 Cut-off 검사
- 추정치와 가정 평가
- 전문가 활용

절차 유형은 검색을 돕는 분류이며 원문 문구를 함께 보여줘야 합니다.

### 6.24 `compare_peer_audit_procedures`

**목적:** 같은 KAM 주제에서 Peer 회사의 감사절차 유형과 문구를 비교합니다.

### 6.25 `get_kam_lifecycle`

**목적:** 한 회사의 KAM이 반복·신규·삭제·변경됐는지 연도별로 봅니다.

KAM 제목뿐 아니라 선정 이유와 감사절차 문구 변화를 함께 확인합니다.

### 6.26 `get_accounting_policy_changes`

**목적:** 회계정책·추정·판단의 원문 변화를 여러 해에 걸쳐 비교합니다.

문구가 달라졌다고 정책 변경이 확정되는 것은 아니며, 기준서 변경·작성방식
변경·원문 확보 범위를 함께 확인해야 합니다.

### 6.27 `get_quality_of_earnings_pack`

**목적:** 이익과 현금흐름의 관계를 한 번에 점검합니다.

예:

- CFO/순이익
- 발생액
- 매출채권·재고 변화
- 영업이익과 현금흐름 괴리
- 일회성 가능성

### 6.28 `get_dcf_input_candidates`

**목적:** DCF 입력에 사용할 수 있는 실제 공시 수치 후보를 제공하고 무엇이
가정인지 구분합니다.

실제 수치와 분석가 가정을 섞지 않습니다.

### 6.29 `search_disclosure_events`

**목적:** 최근 투자·감사 관련 공시사건을 찾습니다.

예:

- 정정
- 자기주식
- 유상증자
- CB·BW·EB
- 합병·분할
- 대규모 계약
- 소송

공시 제목 신호이므로 필요하면 원문을 추가 확인합니다.

### 6.30 `get_audit_report_sections`

**목적:** 감사보고서의 주요 문단 원문을 구조적으로 조회합니다.

감사보고서 패키지의 첨부 재무제표·주석까지 모두 자동 반환한다는 의미가
아니며, 주석은 별도 note/policy 기능과 연결해 조회합니다.

### 6.31 `estimate_audit_hours_proxy`

**목적:** 공개정보로 감사시간 규모를 참고 추정합니다.

실제 투입시간·보수 견적 또는 인력계획을 확정하는 기능은 아닙니다.

### 6.32 `build_audit_acceptance_pack`

**목적:** 감사수임·유지 검토에 필요한 위험 근거를 묶습니다.

예:

- 재무위험
- 감사의견과 감사인 변경
- KAM·강조사항
- 정정·소송·자본조달
- 그룹구조
- 추가 확인사항

### 6.33 `get_industry_audit_landscape`

**목적:** 업종 전체의 감사 환경을 봅니다.

예:

- 감사인 분포
- 감사의견
- 감사보수와 시간
- KAM 주제
- 자료 커버리지

### 6.34 `build_dcf_model_pack`

**목적:** 실제 공시 수치와 사용자가 정한 가정을 분리해 DCF를 계산합니다.

구성:

1. 실제 재무수치
2. 명시적 가정
3. 계산 과정
4. 기업가치에서 주주가치로의 연결
5. 한계와 민감도

## 7. 대표 업무 시나리오와 Tool 조합

### 시나리오 1 — 회사와 사업 이해

```text
search_company
→ get_business_overview
→ get_financial_snapshot
→ get_investor_signals
```

### 시나리오 2 — 수익인식과 감사근거

```text
search_company
→ get_accounting_policy
→ compare_peer_accounting_notes 또는 주석 검색
→ get_audit_report_sections
→ search_audit_procedures
→ search_disclosure_events
```

기본 원천은 감사보고서 패키지의 재무제표 주석과 KAM입니다. 사업보고서는
매출구조·제품·계약의 사업 맥락을 제공합니다.

### 시나리오 3 — 감사수임 검토

```text
search_company
→ build_audit_acceptance_pack
→ get_audit_history
→ get_kam_lifecycle
→ get_subsidiary_auditors
→ search_disclosure_events
```

### 시나리오 4 — Peer 비교

```text
search_company
→ select_peer_group
→ compare_to_industry_multi
→ compare_peer_risk_profile
→ compare_peer_accounting_notes
→ compare_peer_kam_topics
```

## 8. 팀별 주요 Tool

### `ye` 우선 Tool

- `get_business_overview`
- `get_financial_snapshot`
- `get_investor_signals`
- `compare_to_industry`
- `compare_to_industry_multi`
- `select_peer_group`
- `search_disclosure_events`
- `get_quality_of_earnings_pack`

### `ei` 우선 Tool

- `get_accounting_policy`
- `get_audit_history`
- `get_audit_report_sections`
- `search_audit_report_matters`
- `search_audit_procedures`
- `get_kam_lifecycle`
- `get_accounting_policy_changes`
- `compare_peer_accounting_notes`
- `compare_peer_kam_topics`
- `compare_peer_audit_procedures`

### `kj` 우선 Tool·운영

- 전체 34개 Tool Catalog
- MCP stdio·HTTP 서버
- `get_semantic_company_context`
- `search_dataset`
- `fetch_disclosure_on_demand`
- 대화 상태·5개 단위 페이지
- 읽기 전용 DB와 Release 검증
- Tool 입력·출력 계약

## 9. Tool 검증 체크리스트

- [ ] 회사가 정확히 식별되는가
- [ ] 사업연도와 연결·별도가 맞는가
- [ ] 기본 원천 보고서가 맞는가
- [ ] 주석은 감사보고서 패키지를 우선하는가
- [ ] 접수번호와 DART 링크가 실제 사용한 원문과 같은가
- [ ] 일부 원문과 전체 원문을 구분하는가
- [ ] 검색 결과 없음과 공시 부재를 구분하는가
- [ ] 사용자 답변에 내부 코드·DB 용어가 노출되지 않는가
- [ ] 표와 서술형 답변이 같은 사실을 말하는가
- [ ] 자동 테스트와 실제 원문 대조를 완료했는가
- [ ] 다음 5개가 동일한 Peer 모집단을 사용하는가
- [ ] Tool 성공과 데이터 준비 완료를 혼동하지 않는가
