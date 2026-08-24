# KReports 비개발자용 용어 사전

## 사용 방법

문서와 회의에서는 기술 용어를 없애지 않습니다. 개발자·AI와 정확히
소통하려면 실제 용어를 알아야 하기 때문입니다. 대신 처음 등장할 때
다음처럼 설명합니다.

> PR(Pull Request, 변경사항을 검토받아 기본 코드에 합치기 위한 요청)

같은 문서에서 두 번째부터는 `PR`이라고만 써도 됩니다.

# A. 제품·업무 용어

| 용어 | 설명 | 예시 |
|---|---|---|
| POC | Proof of Concept. 아이디어가 기술적으로 가능한지 보여주는 시험 | 회사 한 곳의 KAM 검색 시연 |
| Prototype | 제품의 동작과 화면을 미리 구현한 시험 버전 | 챗봇 화면 예시 |
| MVP | Minimum Viable Product. 사용자가 반복 사용하며 가치를 확인할 수 있는 최소 완성 제품 | 여러 회사·자료부족 상황까지 처리하는 MCP |
| Use Case | 사용자가 제품을 쓰는 구체적인 목적 | 감사수임 검토 |
| Scenario | 사용자의 입력부터 결과까지 이어지는 사용 흐름 | 회사검색 → 주석 → KAM → 원문 |
| Workflow | 사람이 업무를 수행하는 단계의 연결 | DART 검색 → 원문 확인 → 조서 작성 |
| E2E | End to End. 입력부터 최종 결과까지 전체 과정 | 질문부터 챗봇 답변까지 |
| Acceptance Criteria | 업무를 완료했다고 판단하는 구체적인 조건 | 3개 회사 검증·테스트 통과·원문 링크 |
| Scope | 이번 작업에 포함할 범위 | 수익인식 주석과 KAM만 포함 |
| Out of Scope | 이번 작업에서 제외할 범위 | 전 상장사 데이터 완전수집 |
| Blocker | 다음 단계 진행을 막는 문제 | 감사보고서 첨부 주석 미수집 |
| Milestone | 일정상 중요한 중간 완료점 | 9월 10일 기능 동결 |
| Feature Freeze | 새 기능 추가를 멈추고 오류수정·출시 준비만 하는 시점 | 9월 10일 이후 |
| Release | 실제 사용 가능한 버전으로 확정하는 것 | 제출용 KReports 버전 |
| Release Candidate | 최종 검증 대상이 되는 출시 후보 | 영상 촬영에 사용할 코드·DB |

# B. 보고서·데이터 원천 용어

| 용어 | 설명 | 예시 |
|---|---|---|
| Filing | DART에 제출된 공시와 첨부자료 | 사업보고서 접수번호 14자리 |
| Business Report | 사업보고서. 사업·제품·위험·계약 등을 종합한 보고서 | 사업의 내용 |
| Audit Report Package | 감사보고서 제출물 전체. 감사보고서 본문 + 첨부 재무제표 + 주석 | 감사의견·재무제표·주석·KAM |
| Financial Statements | 재무상태표·손익계산서·현금흐름표·자본변동표 | 연결재무제표 |
| Financial-statement Notes | 재무제표 주석 | 수익인식·리스·금융상품 주석 |
| Accounting Policy | 거래와 잔액을 인식·측정·표시하는 회사의 정책 | 수익인식 정책 |
| Significant Estimate | 결과에 중요한 영향을 주는 회계추정 | 손상·충당부채 |
| KAM | Key Audit Matter. 감사에서 특히 중요했던 핵심감사사항 | 매출 인식 KAM |
| Audit Procedure | 감사인이 감사증거를 얻기 위해 수행한 절차 | 표본검사·외부조회·Cut-off 검사 |
| Audit Opinion | 감사인의 재무제표 의견 | 적정·한정·부적정·의견거절 |
| Emphasis of Matter | 재무제표에 적절히 표시됐지만 특별히 강조한 문단 | 중요한 불확실성 강조 |
| Going Concern | 회사가 예측 가능한 미래 동안 계속 영업할 수 있다는 전제 | 계속기업 불확실성 |
| CFS | Consolidated Financial Statements. 연결재무제표 | 지배기업과 종속기업 포함 |
| OFS | Separate/Individual Financial Statements. 별도재무제표 | 지배기업 개별 기준 |
| Receipt Number | DART 제출물을 식별하는 접수번호 | 14자리 번호 |
| Provenance | 결과가 어느 원문에서 왔는지 추적하는 정보 | 접수번호·보고서 종류·주석 위치 |
| Source Precedence | 같은 내용이 여러 문서에 있을 때 기본 원천을 정한 우선순위 | 주석은 감사보고서 패키지 우선 |
| Fallback | 기본 원천이 없을 때 제한적으로 사용하는 대체자료 | 사업보고서 기반 주석 |
| Partial | 전체 원문이 아니라 일부 문구만 확보된 상태 | 요약 캐시만 존재 |
| Coverage | 특정 회사·연도·기능의 자료가 확보된 범위 | 2024년 KOSPI KAM 80% |

# C. 데이터베이스 용어

| 용어 | 설명 | 예시 |
|---|---|---|
| Database | 데이터를 표 형태로 저장하고 조회하는 자료함 | SQLite Runtime DB |
| Table | 같은 종류의 데이터를 행과 열로 저장한 단위 | `companies`, `kam_items` |
| Row | 표의 한 건 데이터 | A사 2025년 KAM 1건 |
| Column | 각 데이터 항목 | 회사코드·사업연도·본문 |
| Schema | 어떤 표와 항목이 존재하는지 정한 DB 구조 | `kam_items`의 필드 구성 |
| Migration | 기존 DB 구조를 새 구조로 변경하는 작업 | 새 인덱스·열 추가 |
| Query | DB에서 필요한 데이터를 찾는 명령 또는 코드 | 회사코드로 주석 조회 |
| SQL | 관계형 DB를 조회·수정하는 언어 | `SELECT ...` |
| Join | 공통 값을 기준으로 여러 표를 연결하는 것 | 회사코드·연도로 주석과 KAM 연결 |
| Index | 특정 조건의 검색속도를 높이는 DB 구조 | 회사코드·연도 인덱스 |
| Read-only | 조회할 수 있지만 저장·수정할 수 없는 상태 | 팀원 공용 DB 접근 |
| Runtime DB | 실제 서비스가 질문에 답할 때 읽는 DB | MCP 서버에 배포된 DB |
| Maintainer DB | 수집·재가공·이력까지 가진 관리용 DB | `kj` 관리 DB |
| Test DB | 개발 중 자유롭게 만들고 삭제하는 임시 DB | 테스트용 SQLite |
| Dataset Manifest | DB의 버전·범위·건수를 기록한 명세 | 회사 수·연도 범위 |
| Artifact | 배포·검증을 위해 고정한 파일 결과물 | Runtime DB와 Manifest |
| Hash | 파일내용이 같은지 확인하는 고유 요약값 | SHA-256 |
| Cache | 반복 조회를 줄이기 위해 저장한 결과 | 주석 검색 캐시 |
| Backfill | 과거 또는 누락 데이터를 다시 수집·생성하는 작업 | 2021~2025 감사보고서 재수집 |

# D. 프로그램 구조 용어

| 용어 | 설명 | 예시 |
|---|---|---|
| Frontend | 사용자가 직접 보는 화면 | 사내 챗봇 UI |
| Backend | 화면 뒤에서 데이터·분석·서버를 처리하는 전체 시스템 | DB·MCP·분석 코드 |
| Collector | 외부 원천에서 데이터를 수집하는 코드 | DART 원문 다운로드 |
| Parser | 원문을 표·제목·문단으로 나누는 코드 | KAM 제목·절차 분리 |
| Processor | 원문을 정규화·변환하는 처리 계층 | XML을 구조화 데이터로 변환 |
| Analysis Layer | 저장된 데이터를 조회·비교·계산하는 코드 | Peer 비교 |
| Renderer | 구조화 결과를 글·표로 표현하는 코드 | MCP Markdown 답변 |
| Handler | Tool 요청을 받아 실제 기능을 실행하는 연결 코드 | 감사이력 Handler |
| Module | 한 책임을 가진 코드 파일 또는 폴더 | `kreports/analysis/` |
| Function | 입력을 받아 정해진 처리를 하고 결과를 반환하는 코드 단위 | 회사검색 함수 |
| API | 프로그램이 다른 프로그램의 기능을 호출하는 규격 | OpenDART API |
| MCP | 챗봇이 외부 Tool·Resource를 호출하는 표준 규격 | KReports MCP |
| Tool | MCP가 공개한 하나의 기능 | `search_company` |
| Resource | Tool 결과보다 큰 원문을 필요할 때 열어보는 자료 | 전체 주석 페이지 |
| stdio | 로컬 프로그램끼리 표준입출력으로 통신하는 방식 | Claude Desktop 로컬 MCP |
| HTTP | 네트워크 주소를 통해 서버와 통신하는 방식 | `/mcp` endpoint |
| Endpoint | 서버가 요청을 받는 주소 | `https://host/mcp` |
| Token | 서버 접근을 확인하는 비밀 문자열 | Bearer Token |
| Environment Variable | 코드 밖에서 설정하는 실행환경 값 | `DB_URL` |
| Dependency | 프로그램 실행에 필요한 외부 라이브러리 | SQLAlchemy·MCP SDK |
| Version | 기능·형식의 특정 상태를 식별하는 번호 | MCP 1.x·2.x |

# E. Git·GitHub 용어

| 용어 | 설명 | 예시 |
|---|---|---|
| Git | 파일의 변경 이력을 관리하는 도구 | Commit·Branch 관리 |
| GitHub | Git Repository를 공유·리뷰하는 서비스 | `capitalparser/kreports-core` |
| Repository | 코드·문서·변경이력을 보관하는 프로젝트 공간 | `kreports-core` |
| Clone | 원격 Repository를 로컬에 내려받는 것 | 최초 프로젝트 다운로드 |
| Branch | 기본 코드와 분리된 변경 흐름 | `ye`, `ei`, `kj` |
| Worktree | 특정 Branch가 파일 폴더로 펼쳐진 작업공간 | `kreports-ei/` |
| Working Tree | 현재 폴더의 파일 상태 | 수정됐지만 Commit 전인 파일 |
| Commit | 변경사항과 설명을 저장한 기록 | `fix: ...` |
| SHA | Commit을 식별하는 고유 문자열 | `4119b250...` |
| Push | 로컬 Commit을 GitHub 브랜치로 올리는 것 | `git push origin ei` |
| Pull | 원격 변경을 로컬로 가져와 반영하는 것 | 최신 브랜치 반영 |
| Fetch | 원격 변경 정보를 가져오되 현재 파일에는 바로 합치지 않는 것 | `git fetch origin` |
| PR | Pull Request. 변경을 검토해 main에 합치기 위한 요청 | `ei → main` |
| Draft PR | 아직 작업 중임을 표시한 PR | 중간 진행 공유 |
| Review | PR 변경을 확인하고 의견·승인·수정요청을 남기는 것 | Request changes |
| Approve | PR 병합에 동의하는 리뷰 상태 | `kj` 최종 승인 |
| Request Changes | 수정이 필요하다는 리뷰 상태 | 원천 오류 수정 요청 |
| Merge | PR 변경을 main에 반영하는 것 | 승인 후 병합 |
| Squash Merge | 여러 Commit을 하나로 합쳐 병합하는 방식 | PR 이력 정리 |
| Rebase | 브랜치의 시작기준을 최신 Commit으로 다시 놓는 작업 | 고급 동기화 방식 |
| Reset | 브랜치 위치와 파일 상태를 특정 Commit으로 되돌리는 작업 | 잘못 쓰면 변경 유실 가능 |
| Conflict | 같은 부분을 다르게 수정해 자동 병합이 어려운 상태 | 공통 문서 동시 수정 |
| Diff | 두 상태 사이의 실제 변경내용 | 추가·삭제된 코드 |
| Issue | 해야 할 일·버그·완료조건을 기록하는 작업표 | 주석 원천 이관 Issue |
| Main | 승인된 공식 기준 브랜치 | `main` |

# F. 테스트·품질 용어

| 용어 | 설명 | 예시 |
|---|---|---|
| Test | 기능이 예상대로 작동하는지 자동 확인하는 코드 | 주석 원천 우선 테스트 |
| Unit Test | 작은 함수나 규칙 하나를 확인하는 테스트 | 보고서 선택 함수 |
| Integration Test | 여러 모듈이 연결되는지 확인하는 테스트 | Parser → DB → Analysis |
| E2E Test | 사용자 입력부터 최종 결과까지 확인하는 테스트 | 챗봇 질문 → DART 링크 |
| Regression Test | 변경 후 기존 기능이 깨지지 않았는지 확인하는 테스트 | 기존 34 Tool 계약 |
| Smoke Test | 주요 기능이 최소한 시작·실행되는지 빠르게 확인 | MCP 서버 실행 |
| Fixture | 반복 테스트용 작은 샘플 원문·데이터 | KAM 두 건 XML |
| Mock | 실제 외부 시스템 대신 정해진 값을 반환하는 시험 대체물 | DART API 응답 대체 |
| Assertion | 테스트에서 반드시 맞아야 한다고 선언한 조건 | 접수번호 일치 |
| Validation | 코드 실행뿐 아니라 업무 결과가 맞는지 검증 | 원문 대조 |
| Data Quality | 데이터가 완전·정확·일관된 정도 | 주석 확보 상태 |
| Release Gate | 출시해도 되는지 판정하는 필수 조건 | Manifest·Tool Smoke 통과 |
| Fail-closed | 불확실할 때 성공으로 처리하지 않고 제한·실패로 막는 방식 | 원문 미확보를 공시없음으로 단정하지 않음 |
| Golden Test | 중요한 입력과 기대 결과를 고정한 기준 테스트 | 대표 회사 응답 형식 |

# G. AI 빌딩 용어

| 용어 | 설명 | 예시 |
|---|---|---|
| Prompt | AI에게 주는 지시와 맥락 | 기존 코드를 먼저 조사하라는 요청 |
| Context | AI가 판단할 때 참고하는 정보 | 관련 파일·Issue·원천 규칙 |
| Agent | 목표를 받아 도구·코드·검색을 사용해 여러 단계를 수행하는 AI | Codex 작업 Agent |
| AI Coding | AI를 이용해 코드·테스트·문서를 작성하는 방식 | Codex·Claude Code |
| Hallucination | AI가 근거 없이 사실·코드·결과를 만들어내는 현상 | 없는 Tool을 있다고 설명 |
| Source of Truth | 여러 구현 중 최종 기준이 되는 하나의 정보·코드 | 감사보고서 원천 선택 서비스 |
| Canonical Owner | 특정 규칙을 유일하게 책임지는 코드 또는 담당자 | Peer 선정 함수 |
| Duplicate Logic | 같은 규칙을 여러 곳에 따로 구현한 상태 | Handler와 Renderer가 각자 Peer 계산 |
| Refactor | 기능은 유지하면서 코드 구조를 개선하는 작업 | 중복 Parser 통합 |
| Technical Debt | 빠른 구현 때문에 이후 수정비용이 늘어난 상태 | 사업보고서 기반 레거시 주석 |
| Prompt Harness | 반복 작업에서 AI가 지켜야 할 입력·검증 구조 | AGENTS.md 지침 |

# H. 상태·진행 용어

| 상태 | 의미 |
|---|---|
| 구조 확인 | 기존 코드·데이터·원천을 이해하는 중 |
| 설계 완료 | 변경 범위·완료조건·검증방법이 정해짐 |
| 구현 중 | 코드·테스트·문서를 변경하는 중 |
| 테스트 중 | 자동 테스트와 실제 원문을 확인하는 중 |
| 리뷰 요청 | 다른 사람이 검토할 수 있는 상태 |
| 검증 완료 | 코드·원문·MCP·답변 확인 완료 |
| 진행 차단 | 데이터·결정·권한 문제로 진행이 막힘 |
| Released | 승인된 코드와 DB가 실제 사용 버전으로 확정됨 |
