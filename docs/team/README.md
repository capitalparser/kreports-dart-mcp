# KReports 팀 오리엔테이션 및 협업 문서

## 목적

이 폴더는 개발 경험이 많지 않은 회계사 팀원이 KReports를 이해하고,
2026년 9월 16일 제출까지 직접 기능을 설계·구현·검증할 수 있도록 만든
공통 안내서입니다.

이 문서에서 기술 용어는 가능한 한 그대로 사용하되, 처음 등장할 때
괄호 안에 쉬운 설명과 예시를 붙입니다. 개발자와 협업할 때 필요한 용어를
피하지 않으면서도 의미를 이해할 수 있게 하는 것이 목적입니다.

## 먼저 읽을 순서

1. [`01_ORIENTATION_GUIDE.md`](01_ORIENTATION_GUIDE.md)
   - 오리엔테이션의 진행 순서, 목표, 실습 내용
2. [`02_PROJECT_ARCHITECTURE_AND_DATA_FLOW.md`](02_PROJECT_ARCHITECTURE_AND_DATA_FLOW.md)
   - KReports의 전체 구조, 데이터베이스, 감사보고서 패키지, E2E 흐름
3. [`03_GITHUB_WORKFLOW.md`](03_GITHUB_WORKFLOW.md)
   - `ye`, `ei`, `kj` 브랜치, Worktree, Commit, PR, 리뷰와 반려 처리
4. [`04_MCP_FUNCTION_GUIDE.md`](04_MCP_FUNCTION_GUIDE.md)
   - MCP가 무엇인지, KReports의 34개 기능이 각각 무엇을 하는지
5. [`05_WORK_ASSIGNMENT_AND_ROADMAP.md`](05_WORK_ASSIGNMENT_AND_ROADMAP.md)
   - 담당자별 업무와 9월 16일까지의 일정
6. [`06_SUBMISSION_PLAN.md`](06_SUBMISSION_PLAN.md)
   - 90초 영상과 아이디어 상세 설명서 구성
7. [`GLOSSARY.md`](GLOSSARY.md)
   - 자주 쓰는 기술·협업 용어 사전

## 반드시 함께 읽을 기준 문서

- [`../report-source-contract.md`](../report-source-contract.md)
  - 사업보고서와 감사보고서 패키지의 정의 및 재무제표 주석 원천 기준
- [`../../CONTEXT.md`](../../CONTEXT.md)
  - 프로젝트 전체에서 사용하는 도메인 용어와 원천 우선순위
- [`../../AGENTS.md`](../../AGENTS.md)
  - AI를 이용한 개발 시 지켜야 할 구조·검증 원칙
- [`../../kreports/AGENTS.md`](../../kreports/AGENTS.md)
  - KReports 기능 구현 시 감사보고서 패키지와 원천을 다루는 세부 규칙

## 현재 브랜치와 역할

| 브랜치 | 기본 역할 | 공용 DB 권한 |
|---|---|---|
| `kj` | 제품·데이터베이스·MCP·출시 및 공통 문서 | 읽기·쓰기 관리자 |
| `ye` | 사업보고서 본문 및 사업·재무 분석 | 읽기 전용 |
| `ei` | 감사보고서 패키지, 재무제표·주석·KAM 분석 | 읽기 전용 |

브랜치(Branch, 기본 코드와 분리된 개인 작업 흐름)는 사람의 영구 소유물이
아니라 변경사항을 안전하게 검토하기 위한 Git의 작업 단위입니다. 이번
제출 전까지는 각 담당자의 주된 업무 흐름을 단순화하기 위해 위 세 개의
개인 브랜치를 사용합니다.

## 공동 원칙

1. 공용 데이터베이스는 `kj`가 관리합니다.
2. `ye`와 `ei`는 공용 데이터베이스를 읽기 전용으로 사용합니다.
3. 데이터 오류는 공용 DB를 손으로 수정하지 않고 코드·원천·테스트를
   고쳐 재생성합니다.
4. 모든 변경은 개인 브랜치에서 Commit(변경 기록)을 만들고 PR(Pull
   Request, 검토 후 `main`에 합치기 위한 요청)로 제출합니다.
5. PR이 반려되더라도 개인 Worktree(브랜치가 실제 파일로 펼쳐진 작업
   폴더)를 원복하지 않습니다. 같은 브랜치에서 수정 Commit을 추가합니다.
6. 재무제표와 주석의 기본 원천은 감사보고서 패키지입니다.
7. 기능 완료는 코드 작성이 아니라 실제 원문 대조·자동 테스트·챗봇
   시연까지 포함합니다.
