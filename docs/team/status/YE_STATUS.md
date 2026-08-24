---
workstream: YE
owner_role: 사업보고서 본문·사업/재무/Peer 분석 및 부팀장
status: 구조 확인
current_issue: null
active_branch: null
active_pr: null
base_branch: main
updated_at: 2026-08-24
submission_deadline: 2026-09-16
---

# YE 업무 상태

## 1. 담당 결과

사용자가 회사의 사업모델, 제품·매출구조, 주요 위험·계약과 최근 재무성과를
사업보고서 근거와 검증된 재무자료를 통해 빠르게 이해하도록 기능을
고도화합니다.

## 2. 원천 범위

### 기본 원천

- 사업의 내용
- 제품과 서비스
- 매출·매입 구조
- 연구개발
- 위험
- 주요 계약
- 사업상 종속기업 정보
- 관련 수시공시

### 주의

- 재무제표 주석과 회계정책의 기본 원천은 감사보고서 패키지입니다.
- 사업보고서에 있는 유사 주석을 기본 재무제표 주석으로 사용하지 않습니다.
- 재무분석은 감사대상 재무제표 또는 검증된 구조화 재무자료를 사용합니다.

## 3. 담당 사용자 기능

### YE-1 회사와 사업 이해

> “A사는 무엇으로 돈을 벌고 주요 제품·지역·위험·계약은 무엇인가?”

### YE-2 재무·현금흐름·Peer 분석

> “A사의 최근 3개년 재무·현금흐름과 동종업계 위치를 알려줘.”

### 공동 매출 시나리오

사업보고서의 매출구조·고객·계약·위험을 EI의 감사보고서 패키지
수익인식 주석·KAM·감사절차와 연결합니다.

## 4. 현재 작업단위

- Issue:
- 작업 Branch: `feat/<issue-number>-<summary>`
- Draft PR:
- 교차 Reviewer:
- 이번 PR에서 제외한 범위:

신규 업무에 `ye`라는 사람별 장기 Branch를 사용하지 않습니다. 한 Issue를
위한 작업 Branch를 최신 `main`에서 만들고, Merge 후 Branch와 Worktree를
정리합니다.

## 5. 초기 업무

- [ ] 오리엔테이션 문서 읽기
- [ ] 자신에게 Assign된 첫 Issue 확인
- [ ] 최신 `main`에서 작업단위 Branch 생성
- [ ] 작업 Branch용 Worktree 확인
- [ ] 공용 DB 읽기 연결 확인
- [ ] 대표 회사 3곳 선정
- [ ] 사업보고서 E2E 흐름 작성
- [ ] 관련 파일·DB 표·MCP Tool·Test 목록 작성
- [ ] 실제 원문과 현재 결과 대조
- [ ] Reuse Map 작성
- [ ] 첫 개선 Draft PR 개설

## 6. 상태 업데이트 항목

### 이번 사용자 목적

### 확인한 데이터 흐름

```text
사업보고서 본문
→ Parser
→ DB
→ Analysis
→ MCP
→ 챗봇·원문 링크
```

### 확인한 기존 구조와 Reuse Map

- 기준 코드:
- DB 표:
- MCP Tool·Handler:
- Test:

### 직접 수행한 작업

### AI에게 요청한 내용

### AI 제안 중 수정·거절한 내용과 이유

### 자동 Test 결과

- Head SHA:
- 실행 명령:
- Passed / Failed / Error / Skipped:

### 실제 DART 원문 대조

| 회사 | 연도 | 접수번호 | 확인 항목 | 결과 |
|---|---:|---|---|---|
| | | | | |

### MCP·챗봇 확인

### EI 기능 교차 Review

### 이해하지 못한 부분

### Blocker와 KJ 결정 필요사항

### 다음 작업

## 7. 완료 판단

- [ ] 사업보고서 본문과 재무 입력의 원천을 구분해 설명할 수 있음
- [ ] 자동 Test와 실제 DART 대조를 완료함
- [ ] 공용 DB를 직접 수정하지 않음
- [ ] Peer 기준과 사용자 답변을 검토함
- [ ] EI 기능의 회계·업무 의미를 교차 Review함
- [ ] PR Review 의견을 같은 작업 Branch에서 반영함
- [ ] Merge 후 완료 Branch와 Worktree를 정리함
