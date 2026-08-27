# CONTEXT.md — kreports_dart_mcp

## Project Profile

- 개요: DART 공시를 수집, 파싱하고 MCP/CLI/API 형태로 제공하는 한국 공시 데이터 도구 프로젝트입니다.
- 목적: 감사, 재무분석, 공시 검토 업무에서 DART 자료 접근과 구조화 비용을 줄이고 다른 에이전트가 재사용할 수 있는 인터페이스를 제공합니다.

## Domain Vocabulary

- **Filing**: A disclosure submitted to Korea's DART system and identified by
  its receipt number. It may include structured facts, attachments, and raw
  source content. A filing date is not proof of a company's listing date.
- **Business report**: DART 사업보고서 제출물 중 사업의 내용, 매출·매입
  구조, 주요 제품, 위험, 연구개발, 주요 계약, 지배구조 및 그 밖의 법정
  공시 본문을 뜻합니다. KReports에서 사업보고서는 재무제표 주석의 기본
  원천이 아닙니다.
- **Audit report package**: DART에 제출된 감사보고서 본문만을 뜻하지
  않습니다. 독립된 감사인의 감사보고서, 해당 제출물에 첨부된 감사대상
  재무제표, 현금흐름표·자본변동표, 재무제표 주석, 유의적인 회계정책,
  중요한 회계추정과 판단 및 관련 부속자료 전체를 하나의 감사보고서
  패키지로 봅니다.
- **Financial-statement notes**: 재무제표 주석, 유의적인 회계정책 및
  중요한 추정·판단은 감사보고서 패키지를 기본적이고 권위 있는 원천으로
  사용합니다. 사업보고서에서 가져온 동일·유사 문구는 감사보고서 패키지가
  확보되지 않은 경우에만 명시적인 대체자료로 사용할 수 있으며, 기본
  원천 또는 감사보고서 근거로 표시해서는 안 됩니다.
- **Company**: A DART-registered reporting entity identified by `corp_code`;
  stock code, market, and industry metadata are optional and current-state
  metadata is not historical listing evidence.
- **Investor signal**: A reproducible observation derived from filing or
  financial facts to support analysis. It is not investment advice.
- **Audit signal**: Extracted audit evidence or clearly labeled interpretation
  useful to accounting professionals. Evidence and risk interpretation remain
  separate.
- **Source filing**: The authoritative DART submission or attachment behind a
  structured fact or signal. Derived outputs retain receipt-level traceability.
- **Hosted mode**: A maintained, read-only deployment backed by a prepared
  dataset artifact and no required server-side DART API key.
- **Self-hosted mode**: A user-operated deployment running the same read-only
  semantics or explicitly enabled collector workflows with the operator's own
  credentials and storage.

Hosted and self-hosted modes share domain semantics. Only collector mode may
mutate the database or persist raw filing content.

## Professional Response Semantics

Professional MCP responses separate source-backed facts and audit or investor
interpretation. Their public data-quality status is one of `usable`, `limited`,
`missing`, or `error`; cache absence does not establish that a source filing is
absent.

## Report Source Precedence

KReports의 기본 원천 우선순위는 다음과 같습니다.

1. 감사의견, 핵심감사사항, 감사절차, 감사대상 재무제표, 재무제표 주석,
   회계정책 및 중요한 추정·판단은 감사보고서 패키지를 우선합니다.
2. 사업의 내용, 제품·서비스, 매출·매입 구조, 연구개발, 위험, 주요 계약,
   지배구조 및 법정 공시 본문은 사업보고서를 우선합니다.
3. 수시공시와 정정공시는 해당 DART 접수번호의 원 공시를 우선합니다.
4. 기본 원천을 확보하지 못해 다른 제출물이나 요약 캐시를 사용하면
   `fallback` 또는 `partial` 상태와 실제 접수번호를 보존하고 사용자에게
   자료의 한계를 알려야 합니다.
5. 같은 내용이 두 제출물에 존재해도 보고서 종류, 접수번호, 사업연도,
   연결·별도 기준을 유지하며 서로 다른 원천을 조용히 합치지 않습니다.

기존 코드나 데이터에 사업보고서 기반 재무제표 주석이 남아 있을 수
있습니다. 이는 이관 대상인 레거시 경로이며, 새 기능은 해당 경로를
재무제표 주석의 기본 원천으로 확대해서는 안 됩니다.

## Current Scope

collector/backfill 실행 근거, HTTP MCP 안정화, 캐시/패키지 사용 경로를 Harness 상태판에 남겨야 합니다. 운영 확인은 배포 완료가 아니라 반복 실행 근거가 있다는 의미입니다.

## Product Direction

- MCP 응답은 사용자-facing 단계에서 JSON dump가 아니라 한국어 문장, 단락, 근거 목록 형태여야 합니다. 구조화 dict/JSON은 내부 API, 테스트, UI 렌더링용으로 유지하되 MCP 사용자는 verdict-first 서술형 답변을 받아야 합니다.
- 공개 MCP endpoint의 기본 데이터는 cache-first/read-only입니다.
- 온디맨드 수시공시 조회는 예외적으로 허용할 수 있으나, 서버 보유 `DART_API_KEY`를 쓰지 않고 사용자별 OpenDART API key를 입력받아 1회성 fetch/cache에 사용해야 합니다.
- 사용자 제공 API key는 저장, 로그 출력, 응답 반영을 금지합니다.

## Historical Listing-Period Evidence

`company_listing_periods` stores a normalized listing CSV, never mislabels it
as a raw official KRX snapshot, and binds every row to both `corp_code` and
`stock_code`. It retains the origin URL, raw-receipt checksum/retrieval time,
durable receipt locator and byte length, normalized-payload checksum/locator/
byte length, and an explicitly supported transformation version. A missing,
altered, or unreadable raw or normalized artifact is reported as unavailable
and cannot produce verified eligibility. Its `as_of` field makes the
observation time explicit. `unknown` and `conflict` are retained in release
metadata and never shrink the current KOSPI/KOSDAQ denominator. A future
eligibility policy must be separately approved before it can use verified
full-year periods to change that denominator.
