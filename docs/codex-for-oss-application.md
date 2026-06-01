# Codex for OSS application notes

This document is a maintainer-facing summary for the OpenAI Codex for OSS
application form. It does not contain private IDs, email addresses, or secrets.

## Repository

https://github.com/capitalparser/kreports-dart-mcp

## Role

Primary maintainer.

## Why this repository fits the program

KReports is an Apache-2.0 MCP server and Python package that turns Korea's DART
filings into source-grounded investor and audit intelligence. It covers company
lookup, financial facts, peer benchmarking, audit history, audit fees, KAM and
audit-procedure search, accounting policy extraction, disclosure event search,
and HTTP/stdio MCP deployment. The project matters because Korean public
company disclosure is high-value but hard to use programmatically, especially
for non-Korean MCP/LLM users.

Short form draft, under 500 Korean characters:

> KReports는 한국 DART 공시를 MCP 도구와 Python 패키지로 바꾸는 Apache-2.0
> 오픈소스 프로젝트입니다. 재무 fact, 피어 벤치마킹, 감사인 이력, 감사보수,
> 핵심감사사항/KAM, 감사절차, 회계정책, 공시 이벤트를 근거 기반 답변으로
> 제공합니다. 한국 상장사 공시는 가치가 크지만 API·문서·언어 장벽이 높아,
> MCP/LLM 생태계에서 재사용 가능한 공시 인프라 역할을 할 수 있습니다.

## API credit use plan

Credits would be used for maintainer workflows, not for storing private user
data:

- Generate and review parser tests for DART XML/HTML edge cases.
- Improve source-grounded Korean/English narrative renderers for MCP answers.
- Triage issues and pull requests involving accounting, audit, and disclosure
  semantics.
- Build regression checks over compact fixture datasets.
- Improve security review around remote HTTP MCP, on-demand user-keyed DART
  fetches, and provenance handling.

Short form draft, under 500 Korean characters:

> API 크레딧은 DART XML/HTML 파서 회귀테스트 생성, MCP 응답의 근거 기반
> 한국어/영어 서술 품질 개선, 이슈/PR triage, compact fixture 기반 검증,
> 원격 HTTP MCP와 사용자 API 키 기반 온디맨드 조회의 보안 검토에 사용할
> 계획입니다. 목적은 기능 추가 속도보다 공시 근거·데이터 품질·유지관리
> 자동화 수준을 높이는 것입니다.

## Additional notes

KReports is still pre-1.0, but it has a substantial implemented surface:

- 31 read-oriented MCP tools.
- stdio and HTTP MCP servers.
- SQLite runtime DB with structured facts, normalized evidence documents, and
  raw-document storage metadata.
- DART limit-aware backfill runners.
- Tests covering parsers, evidence packs, MCP rendering, audit tools, investor
  tools, raw storage, HTTP MCP, and runtime exports.
- Public docs for MCP setup, deployment, automated backfill, disclosure DB
  completeness, and raw retention policy.

Short form draft, under 500 Korean characters:

> 현재 pre-1.0이지만 31개 MCP 도구, stdio/HTTP 서버, SQLite runtime DB,
> evidence document layer, raw storage metadata, DART 한도 인식 백필러,
> 감사·투자자 관점 테스트를 갖추고 있습니다. 다음 단계는 5개년 compact
> runtime DB 완성, 공개 endpoint 안정화, contributor-friendly fixture와
> 보안 점검 체계를 강화하는 것입니다.
