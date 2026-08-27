# KReports GitHub 협업 방식

## 1. 먼저 결론

KReports에서는 담당자별 장기 Branch가 아니라 **작업단위 Branch**를
사용합니다.

```text
GitHub Issue로 하나의 업무 정의
→ 최신 main에서 그 업무만을 위한 Branch 생성
→ 담당자가 해당 Branch의 Worktree에서 작업
→ 작은 단위로 Commit
→ GitHub에 Push
→ main을 대상으로 Draft PR 생성
→ Reviewer가 검토
→ 수정 요청이면 같은 Branch에서 수정 Commit 추가
→ 승인되면 main에 Merge
→ 완료된 Branch와 Worktree 정리
→ 다음 Issue는 최신 main에서 새 Branch로 시작
```

가장 중요한 원칙은 다음입니다.

> 하나의 Issue = 하나의 작업 Branch = 하나의 PR

`kj`, `ye`, `ei`는 사람과 역할을 나타내는 표기이며, 신규 작업용 장기
Branch 이름이 아닙니다.

---

## 2. 왜 사람별 Branch를 사용하지 않는가

사람별 장기 Branch를 사용하면 한 사람의 서로 다른 업무가 계속 쌓입니다.

예를 들어 `ei` Branch에 다음 작업이 함께 들어갈 수 있습니다.

```text
감사보고서 패키지 원천 변경
KAM 표시 개선
감사절차 검색 개선
상태보고서 수정
영상 화면 준비
```

이 상태에서는 다음 문제가 발생합니다.

- 하나의 PR에 여러 업무가 섞입니다.
- 일부 기능만 승인하거나 Merge하기 어렵습니다.
- 첫 번째 업무는 완료됐지만 다른 업무 때문에 전체 PR이 막힐 수 있습니다.
- 이전 변경이 다음 PR에 다시 포함될 수 있습니다.
- `main`과 오래 떨어져 있어 충돌 가능성이 커집니다.

작업단위 Branch는 한 가지 목적만 갖습니다.

예:

```text
feat/10-audit-package-note-source
```

이 Branch는 다음 한 가지 업무만 포함합니다.

> 재무제표 주석과 회계정책의 기본 원천을 감사보고서 패키지로 변경한다.

---

## 3. 주요 용어

### Repository

Repository 또는 Repo(리포지토리, 프로젝트의 코드·문서·변경 이력을 보관하는
GitHub 공간)는 `capitalparser/kreports-core`입니다.

### Issue

Issue(이슈, 해야 할 업무와 완료 조건을 기록하는 GitHub 작업표)는 개발을
시작하기 전에 먼저 만듭니다.

Issue에는 최소한 다음이 있어야 합니다.

- 해결하려는 사용자 문제
- 기대 결과
- 사용할 보고서와 데이터 원천
- 현재 E2E 흐름
- 변경할 범위와 변경하지 않을 범위
- 완료 조건
- 자동 테스트와 실제 DART 검증 방법
- Primary Assignee와 Reviewer

### Assignee

Assignee(어싸이니, 해당 Issue 또는 PR의 주 작업 책임자)는 실제 작업을
진행하고 결과를 설명할 사람입니다.

한 Issue에는 여러 참여자가 있을 수 있지만 Primary Assignee는 원칙적으로 한
명입니다. 다른 사람은 Reviewer, Pair Work 참여자 또는 선행 작업 담당자로
참여합니다.

### Branch

Branch(브랜치, `main`과 분리해 특정 업무의 변경 이력을 쌓는 작업선)는
사람이 아니라 **업무**를 나타냅니다.

```text
main
feat/10-audit-package-note-source
feat/11-business-report-analysis
fix/15-wrong-dart-link
docs/16-submission-report
```

### Worktree

Worktree(워크트리, 특정 Branch의 파일을 실제 폴더로 열어 편집하는
작업공간)는 Branch 그 자체가 아니라 Branch를 펼쳐 둔 폴더입니다.

브랜치는 작업단위로 만들지만 Worktree 폴더에는 담당자와 업무를 함께 표시할
수 있습니다.

```text
worktrees/
├── kj/
│   └── 18-release-db-baseline/
├── ye/
│   └── 11-business-report-analysis/
└── ei/
    └── 10-audit-package-note-source/
```

### Commit

Commit(커밋, 특정 시점의 변경내용과 설명을 저장한 기록)은 하나의 작은
논리적 변경을 담습니다.

좋은 예:

```text
docs: explain task-scoped branch workflow
fix: prefer audit package notes over business-report fallback
test: add multi-KAM audit report fixture
```

### Push

Push(푸시, 로컬 Branch의 Commit을 GitHub 원격 Branch에 올리는 작업)를 해야
다른 사람이 PR에서 변경사항을 확인할 수 있습니다.

### Pull Request

PR 또는 Pull Request(풀 리퀘스트, 작업 Branch의 변경을 `main`에 합쳐도
되는지 검토받는 요청)는 다음을 보여줍니다.

- 어떤 Issue를 해결하는지
- 어떤 파일이 바뀌었는지
- 어떤 Commit이 포함됐는지
- 데이터와 보고서 원천이 무엇인지
- 어떤 Test를 실행했는지
- 실제 DART 원문과 비교했는지
- Reviewer가 어떤 의견을 남겼는지

### Review

Review(리뷰, 다른 사람이 변경 내용을 확인하고 승인·질문·수정 요청을 하는
절차)는 단순한 오탈자 확인이 아닙니다.

KReports Review에서는 다음을 봅니다.

- 회계·업무 의미가 맞는지
- 사업보고서와 감사보고서 패키지를 혼동하지 않았는지
- 기존 코드와 규칙을 중복 구현하지 않았는지
- 공용 DB를 직접 수정하지 않았는지
- 원문, 접수번호, 연결·별도와 연도가 일치하는지
- 자동 Test와 실제 DART 검증이 있는지
- 사용자 답변이 과도하게 단정하지 않는지

### Request Changes

Request Changes(수정 요청, Reviewer가 현재 상태로는 승인할 수 없다고 표시하는
것)는 작업 전체를 폐기한다는 뜻이 아닙니다.

같은 Branch와 Worktree에서 수정하고 새 Commit을 Push하면 기존 PR이 자동으로
업데이트됩니다.

### Merge

Merge(병합, 승인된 PR 변경을 `main`에 반영하는 작업)가 완료되어야 공식
프로젝트 기준에 포함됩니다.

---

## 4. Branch 이름 규칙

형식은 다음과 같습니다.

```text
<작업종류>/<Issue번호>-<짧은-업무명>
```

| 접두어 | 의미 | 예시 |
|---|---|---|
| `feat` | 새로운 기능 또는 기능 고도화 | `feat/10-audit-package-note-source` |
| `fix` | 잘못된 동작 수정 | `fix/15-wrong-dart-link` |
| `docs` | 문서 중심 변경 | `docs/16-submission-report` |
| `test` | Test·Fixture·검증 중심 | `test/17-kam-golden-cases` |
| `chore` | DB 검증·배포·환경 등 공통 작업 | `chore/18-release-db-baseline` |
| `refactor` | 기능 의미를 유지한 코드 구조 정리 | `refactor/19-note-source-selector` |

좋은 이름:

```text
feat/10-audit-package-note-source
```

좋지 않은 이름:

```text
ei
new-feature
final-version
fix-everything
```

---

## 5. 현재 `kj`, `ye`, `ei` Branch 처리

`kj`, `ye`, `ei` Branch는 작업단위 원칙을 확정하기 전에 만든 전환용
Branch입니다.

- 이미 열려 있는 `kj` Draft PR은 그 PR 범위까지만 완료할 수 있습니다.
- 신규 업무는 `kj`, `ye`, `ei` Branch에서 시작하지 않습니다.
- 기존 전환 PR이 Merge된 후 사람별 Branch는 신규 개발선으로 사용하지 않고
  정리합니다.
- 사람과 역할은 Issue Assignee, PR Reviewer, 상태보고서와 Worktree 폴더에서
  관리합니다.

---

## 6. 작업 시작 절차

### 6.1 Issue 확인

먼저 자신에게 Assign된 Issue를 읽습니다.

다음이 불명확하면 코드를 바로 수정하지 않습니다.

- 사용자에게 어떤 결과를 제공하려는가
- 기본 원천 보고서는 무엇인가
- 어떤 기존 코드를 재사용해야 하는가
- 공용 DB에 어떤 영향이 있는가
- 무엇을 완료로 판단하는가

### 6.2 최신 `main` 확인

작업 Branch는 최신 승인된 `main`에서 만듭니다.

```bash
git fetch origin
git switch main
git pull --ff-only origin main
```

비개발자 팀원은 현재 Worktree와 미저장 변경사항을 먼저 확인하고, 명령이
불확실하면 KJ에게 확인합니다.

### 6.3 작업 Branch 생성

예: Issue #10

```bash
git switch -c feat/10-audit-package-note-source
```

### 6.4 Worktree를 사용하는 경우

예시 구조:

```bash
git worktree add ../worktrees/ei/10-audit-package-note-source \
  feat/10-audit-package-note-source
```

실제 명령은 로컬 Branch 생성 여부와 폴더 상태에 따라 달라질 수 있으므로,
처음 설정할 때 KJ가 확인합니다.

### 6.5 동시에 진행하는 Branch 수

9월 16일 제출 전까지는 한 사람당 활성 작업 Branch를 원칙적으로 하나만
유지합니다.

다른 긴급 업무가 생기면:

- 현재 업무를 안전하게 Commit하고 중단하거나
- KJ가 두 번째 Worktree 필요성을 확인하고
- 서로 다른 Issue와 Branch로 분리합니다.

---

## 7. 작업 중 절차

### 7.1 기존 구조부터 확인

AI에게 코드를 바로 만들게 하지 않습니다.

먼저 다음을 찾습니다.

- 기존 기준 코드
- 관련 DB 표
- 관련 MCP Tool과 Handler
- 관련 Test
- 현재 E2E 데이터 흐름

이를 Reuse Map(재사용 지도, 새로 만들지 않고 기존에 사용할 코드·표·Test를
정리한 목록)으로 PR에 기록합니다.

### 7.2 작은 단위로 수정

좋은 예:

```text
1. 감사보고서 패키지 첨부 주석 선택 경로 확인
2. 감사보고서 우선 규칙 수정
3. 사업보고서 fallback 표시 추가
4. 자동 Test 추가
5. 실제 DART 원문 대조
6. MCP 답변 확인
```

좋지 않은 예:

```text
주석·KAM·Peer·DB Schema·MCP·영상 문서를 하나의 PR에서 모두 변경
```

### 7.3 변경사항 확인

```bash
git status
git diff
```

- `git status`: 어떤 파일이 바뀌었는지 확인
- `git diff`: 실제 코드와 문장이 어떻게 달라졌는지 확인

### 7.4 필요한 파일만 Stage

```bash
git add -- <확인한 파일 경로>
```

다음 명령은 사용하지 않습니다.

```bash
git add .
git add -A
git add --all
```

무관한 파일, 임시 DB 또는 비밀정보가 함께 Commit될 수 있기 때문입니다.

### 7.5 Commit과 Push

```bash
git commit -m "fix: prefer audit package note source"
git push -u origin feat/10-audit-package-note-source
```

첫 Push 후 Draft PR을 만듭니다.

Draft PR(초안 PR, 아직 작업 중이지만 진행 방향과 변경 범위를 Reviewer가 볼
수 있는 상태)은 작업 완료 후가 아니라 초기에도 만들 수 있습니다.

---

## 8. PR 작성 방식

PR에는 최소한 다음을 기록합니다.

1. 연결된 Issue
2. 해결하려는 사용자 문제
3. Primary Assignee와 Reviewer
4. 사용한 보고서와 데이터 원천
5. 회사·연도·접수번호·연결/별도
6. 기존 코드와 Reuse Map
7. 변경한 파일과 이유
8. 공용 DB 영향
9. 자동 Test 결과
10. 실제 DART 원문 대조
11. MCP·챗봇 확인
12. AI 제안 중 수정·거절한 내용
13. 알려진 한계와 Blocker
14. 문서·영상·제출자료 영향

단순히 `완료했습니다`라고 쓰는 것은 충분하지 않습니다.

---

## 9. Review 결과별 처리

### 9.1 승인

Reviewer가 필요한 검토를 완료하고 승인하면 KJ가 최종 Merge합니다.

승인은 다음을 의미합니다.

- 해당 Issue의 완료 조건을 충족함
- 변경 범위가 적절함
- 원천과 회계 의미가 맞음
- Test와 검증 증거가 충분함
- `main`에 반영해도 됨

### 9.2 수정 요청

수정 요청을 받으면 Worktree를 원복하지 않습니다.

```text
Review 의견 확인
→ 같은 Worktree에서 수정
→ Test 재실행
→ 새 Commit
→ 같은 Branch에 Push
→ 기존 PR 자동 업데이트
→ Review 의견에 답변
→ 재검토 요청
```

예:

```text
“주석의 기본 원천이 사업보고서로 남아 있음”
→ feat/10-audit-package-note-source에서 원천 선택 수정
→ 회귀 Test 추가
→ Commit·Push
→ 같은 PR 업데이트
```

### 9.3 PR 전체 폐기

다음 경우에만 PR을 닫고 Branch를 폐기할 수 있습니다.

- 사용자 요구 자체가 취소됨
- 접근방식이 근본적으로 잘못됨
- 다른 PR이 같은 문제를 해결함
- 보안·데이터 위험으로 변경을 사용할 수 없음

이 경우에도 담당자가 임의로 `git reset --hard` 또는 Force Push를 하지
않습니다. KJ와 함께 보존할 Commit과 Branch·Worktree 처리 방식을 결정합니다.

---

## 10. Merge 후 처리

PR이 Merge되어도 로컬 Worktree가 자동으로 최신 `main`이 되지는 않습니다.

작업 완료 후 다음을 수행합니다.

1. 미저장 변경사항 확인
2. PR Merge 확인
3. 완료된 Worktree 제거
4. 로컬·원격 작업 Branch 정리
5. 최신 `main` 가져오기
6. 다음 Issue에서 새 Branch 생성

작업 Branch는 일회성입니다.

> 완료된 Branch를 다음 기능에 계속 사용하지 않습니다.

Merge 후 결함이 발견되면 기존 Branch를 다시 살리는 대신 새 Issue와 새
Branch를 만듭니다.

```text
fix/25-audit-package-cfs-ofs-mismatch
```

---

## 11. 여러 사람이 같은 기능에 참여하는 경우

하나의 Branch에는 Primary Assignee를 한 명 둡니다.

예:

```text
Branch: feat/20-revenue-recognition-e2e
Primary Assignee: EI
회계 Review: YE
DB·MCP Review: KJ
```

다른 사람은 다음 방식으로 참여합니다.

- PR Comment와 Review
- Pair Work(두 사람이 함께 보되 한 사람이 최종 코드를 정리하는 방식)
- 실제 DART 표본 검증 결과 제공
- 별도 선행 Issue와 Branch

비개발자 협업 중 여러 사람이 같은 Branch에 동시에 직접 Push하는 것은
가능하면 피합니다.

---

## 12. 작업 간 의존성이 있는 경우

예:

```text
Issue #10 감사보고서 패키지 원천 선택
→ Issue #20 수익인식 E2E 기능이 이를 사용
```

권장 순서:

1. 선행 Issue #10을 먼저 Merge
2. 후속 Branch를 최신 `main`에서 생성
3. 후속 기능 구현

기다릴 수 없는 경우에는 Draft PR에 의존성을 명시하되, 장기간 여러 Branch를
겹쳐 쌓는 Stacked PR은 비개발자 팀의 기본 방식으로 사용하지 않습니다.

---

## 13. 공용 데이터베이스 관련 GitHub 주의사항

- YE와 EI는 공용 DB를 읽기 전용으로 사용합니다.
- 공용 DB 오류를 발견해도 행을 직접 수정하지 않습니다.
- Parser·수집·분석 로직을 수정하고 Test를 추가한 뒤 PR을 제출합니다.
- Migration, Backfill, 데이터 재생성, Runtime DB Export는 KJ가 별도 승인 후
  수행합니다.
- DB 파일, WAL, SHM, Raw Filing과 Cache를 Commit하지 않습니다.
- 개발·Test에는 임시 DB와 Fixture를 사용합니다.

---

## 14. 절대 임의로 하지 않는 작업

명시적인 승인 없이 다음을 실행하지 않습니다.

- `main` 직접 Commit 또는 Push
- `git reset --hard`
- Force Push
- 다른 사람 Branch Rebase
- 공유 Branch 삭제
- Commit History Rewrite
- 공용 DB Migration·Backfill·수동 행 수정
- GitHub Actions 자동 Trigger 추가 또는 임의 실행
- API Key, Token, `.env`, DB, Raw Filing Commit

---

## 15. 전체 예시

### Issue

```text
#10 감사보고서 패키지를 재무제표 주석의 기본 원천으로 변경
```

### Branch

```text
feat/10-audit-package-note-source
```

### Worktree

```text
worktrees/ei/10-audit-package-note-source
```

### 작업

```text
현재 Source 흐름 조사
→ 감사보고서 첨부 선택 규칙 수정
→ 사업보고서 fallback 명시
→ Fixture Test
→ 실제 DART 표본 대조
→ MCP 답변 확인
```

### PR

```text
feat: use audit report package as canonical note source
```

### Review

```text
YE: 회계정책·주석 의미 검토
KJ: DB·Source Contract·MCP 영향 검토
```

### 수정 요청

```text
같은 Branch와 Worktree에서 수정 Commit 추가
```

### Merge 후

```text
Branch와 Worktree 정리
→ 최신 main
→ 다음 Issue의 새 Branch
```

---

## 16. 핵심 문장

> Branch는 내 개인 공간이 아니라, 하나의 업무를 검토받기 위해 잠시 만드는
> 변경 묶음입니다.

> PR에서 수정 요청을 받으면 원복하는 것이 아니라, 같은 작업 Branch에서
> 수정하고 다시 검증합니다.

> Merge가 완료되면 해당 Branch의 역할은 끝나며, 다음 업무는 최신 `main`에서
> 새로 시작합니다.
