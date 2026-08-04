# CONTEXT.md — kreports_dart_mcp

## Project Profile

- 개요: DART 공시를 수집, 파싱하고 MCP/CLI/API 형태로 제공하는 한국 공시 데이터 도구 프로젝트입니다.
- 목적: 감사, 재무분석, 공시 검토 업무에서 DART 자료 접근과 구조화 비용을 줄이고 다른 에이전트가 재사용할 수 있는 인터페이스를 제공합니다.

## Domain Vocabulary

- **Filing**: A disclosure submitted to Korea's DART system and identified by
  its receipt number. It may include structured facts, attachments, and raw
  source content. A filing date is not proof of a company's listing date.
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
