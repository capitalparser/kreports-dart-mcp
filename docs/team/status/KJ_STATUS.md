---
workstream: KJ
owner_role: 제품·데이터베이스·MCP·출시 총괄
status: 구조 확인
current_issue: null
active_branch: kj
active_pr: 9
base_branch: main
updated_at: 2026-08-24
submission_deadline: 2026-09-16
---

# KJ 업무 상태

## 1. 담당 결과

하나의 검증된 데이터베이스와 코드 버전에서 사업보고서 기능과 감사보고서
패키지 기능이 MCP를 통해 연결되고, 제출 영상과 Word 설명서가 동일한
MVP를 설명하도록 합니다.

## 2. 현재 확인된 사실

- 공용 DB는 KJ가 관리하고 YE·EI는 전체 승인 데이터를 읽기 전용으로 사용
- 재무제표 주석·회계정책의 기본 원천은 감사보고서 패키지로 정의
- 기존 사업보고서 기반 주석은 현황 조사와 이관 필요
- 팀 오리엔테이션·GitHub·MCP·로드맵 문서를 Draft PR #9에서 정리 중
- 신규 업무는 사람별 장기 Branch가 아니라 Issue별 작업단위 Branch 사용
- 기존 `kj` Branch는 이미 열린 PR #9의 전환 작업까지만 사용 후 정리
- 기존 `ye`, `ei` Branch는 신규 개발선으로 사용하지 않음

## 3. 현재 E2E 흐름

```text
DART 원문·첨부
→ 수집·보고서 패키지 구분
→ Parser
→ 관리 DB
→ Runtime DB
→ Analysis
→ MCP
→ 챗봇·원문 링크
```

## 4. 현재 작업단위

- Issue: 공통 협업·원천·오리엔테이션 문서 정비
- 작업 Branch: `kj` — 전환용 예외, PR #9까지만 사용
- Draft PR: #9
- 다음 신규 업무 Branch 예: `chore/<issue>-release-db-baseline`

## 5. 완료한 문서·지침 작업

- [x] 감사보고서 패키지 Source Contract 문서화
- [x] 오리엔테이션 문서 작성
- [x] GitHub 협업 문서 작성
- [x] MCP 기능 안내 작성
- [x] 제출 로드맵 작성
- [x] 공용 DB 변경 통제 규칙을 `AGENTS.md`에 반영
- [x] 작업단위 Branch·PR 규칙을 `AGENTS.md`에 반영
- [x] 비개발자 Handoff·설명 책임을 `AGENTS.md`에 반영
- [x] 실제 DART 표본 검증 규칙을 `kreports/AGENTS.md`에 반영
- [x] `docs/AGENTS.md`와 `kreports/db/AGENTS.md` 추가
- [x] Issue·PR Template을 작업단위 Branch 방식으로 수정
- [x] YE·EI·KJ 상태 문서를 작업단위 방식으로 수정

## 6. 남은 P0 업무

- [ ] 현행 주석 데이터 원천 Inventory 작성
- [ ] 팀원용 읽기 전용 DB 연결
- [ ] 대표 검증회사·연도 확정
- [ ] 감사보고서 첨부 재무제표·주석 수집 경로 확인
- [ ] Issue #10을 구현 가능한 하위 작업으로 분해
- [ ] MCP 통합 대표 Scenario 고정
- [ ] Release Candidate와 데이터 품질 검증
- [ ] PR #9 Review·수정·Merge
- [ ] 전환용 사람별 Branch 정리

## 7. AI 활용 기록

### 요청한 내용

- 프로젝트의 기존 지침·MCP·데이터 구조 확인
- 비개발자용 협업·오리엔테이션 문서 작성
- 감사보고서 패키지 기준의 원천 계약 반영
- Word 가이드에서 Agent 지침화할 내용을 선별

### 직접 판단한 내용

- 주석의 기본 원천을 감사보고서 패키지로 변경
- 공용 DB 쓰기 권한을 KJ로 제한
- 보고서 원천 책임과 사용자 기능 책임을 함께 사용
- 사람별 장기 Branch 대신 작업단위 Branch 사용
- PR 수정 요청 시 Worktree를 원복하지 않고 수정 Commit 추가
- 영상·Word·화면을 동일 Release Candidate에 묶음

## 8. 검증

- 현재 변경은 지침·Template·팀 문서 중심
- 코드·데이터·MCP 실행 Test는 수행하지 않음
- PR #9의 변경 파일 범위와 Diff를 검토해야 함
- 문서에 코드 구현 완료나 Live Data 검증 완료를 주장하지 않음

## 9. Blocker·결정 필요사항

- 기존 데이터 중 사업보고서 기반 주석의 규모와 품질 미확인
- 감사보고서 첨부 재무제표·주석 원문 수집 경로의 현재 구현상태 확인 필요
- 팀원 로컬 개발환경과 공용 읽기 전용 DB 전달방법 확정 필요
- GitHub `main` Ruleset과 CODEOWNERS 적용 여부 결정 필요

## 10. 다음 작업

1. PR #9 Diff 및 문서간 일관성 최종 점검
2. Issue #10의 작업단위 분해와 Assignee·Reviewer 지정
3. 읽기 전용 DB 접근 안내와 Data Dictionary 준비
4. 오리엔테이션 진행
5. 최신 `main`에서 첫 작업단위 Branch 생성
