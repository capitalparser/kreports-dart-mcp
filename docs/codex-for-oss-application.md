# Codex for OSS application strategy

This document is a maintainer-facing strategy note for the OpenAI Codex for OSS
application. It should not shape the README directly. The README should sell the
product and project on its own merits; this document translates that substance
into an application narrative. It does not contain private IDs, email addresses,
or secrets.

## Repository

https://github.com/capitalparser/kreports-dart-mcp

## Role

Primary maintainer.

## Why this repository fits the program

The application should lead with KReports as an applied infrastructure project:
it turns Korean public-company filings into source-grounded MCP tools for
investor and audit workflows. The strongest angle is not "this repo needs help";
it is "this project brings an underserved, non-US disclosure system into the
agent/MCP ecosystem with real professional use cases."

The feature substance to emphasize:

- Document-first DART ingestion, not just endpoint wrapping.
- 31 read-oriented MCP tools across investor, auditor, peer comparison, and
  disclosure monitoring workflows.
- Source-grounded narrative answers with confirmed facts, analysis, next checks,
  and filing provenance.
- Audit-specific capabilities: KAM matters, audit procedures, accounting
  policies, auditor changes, audit fees, NAS ratio, group-audit perimeter, and
  going-concern signals.
- Investor-specific capabilities: financial quality, cash conversion,
  peer/industry benchmarks, DCF input candidates, disclosure-event monitoring,
  and quality-of-earnings packs.
- Public read-only MCP architecture that can serve users without each user
  needing a DART API key, while still allowing user-keyed on-demand fetches for
  live disclosure checks.

Short form draft, under 500 Korean characters:

> KReports는 한국 DART 공시를 MCP 도구와 Python 패키지로 바꾸는 Apache-2.0
> 오픈소스 프로젝트입니다. 단순 API 래퍼가 아니라 사업보고서·감사보고서
> 원문을 근거화해 재무 fact, 피어 벤치마킹, 감사인 이력, 감사보수, KAM,
> 감사절차, 회계정책, 공시 이벤트를 서술형 MCP 답변으로 제공합니다. 한국
> 상장사 공시를 agent/MCP 생태계에서 재사용 가능한 공시 인프라로 만드는
> 것이 목표입니다.

## API credit use plan

The API credit plan should be concrete and tied to open-source maintenance, not
general usage. The best framing is that credits reduce the maintenance cost of a
domain-heavy parser/MCP project where correctness requires many edge-case
reviews.

Use cases to list:

- Generate and review parser tests for DART XML/HTML edge cases.
- Improve Korean/English narrative renderers that distinguish confirmed facts
  from analysis.
- Triage issues and pull requests involving accounting, audit, and disclosure
  semantics.
- Build regression checks over compact fixture datasets.
- Review security and data-boundary risks around remote HTTP MCP, on-demand
  user-keyed DART fetches, GCS/raw-document storage, and provenance handling.
- Draft contributor docs and examples for MCP client builders.

Short form draft, under 500 Korean characters:

> API 크레딧은 DART XML/HTML 파서 회귀테스트, 근거와 분석을 분리하는
> 한국어/영어 MCP 응답 개선, 회계·감사 도메인 이슈/PR triage, compact
> fixture 검증, 원격 HTTP MCP·사용자 API 키 기반 온디맨드 조회·GCS 원문
> 저장의 보안 검토에 사용할 계획입니다. 목표는 공시 근거성과 유지관리
> 품질을 높이는 것입니다.

## Additional notes

KReports is still pre-1.0. The application should be transparent about that,
but not apologetic. The honest message is: the public runtime dataset is still
being completed, while the software surface and maintenance architecture are
already substantial.

- 31 read-oriented MCP tools.
- stdio and HTTP MCP servers.
- SQLite runtime DB with structured facts, normalized evidence documents, and
  raw-document storage metadata.
- DART limit-aware backfill runners.
- Tests covering parsers, evidence packs, MCP rendering, audit tools, investor
  tools, raw storage, HTTP MCP, and runtime exports.
- Public docs for MCP setup, deployment, automated backfill, disclosure DB
  completeness, and raw retention policy.

Recommended application posture:

- Be specific about Korea/DART as the underserved ecosystem.
- Emphasize professional-grade workflows: investor diligence and audit planning.
- Emphasize MCP as distribution, not a side feature.
- Avoid claiming the hosted dataset is complete until the 5-year compact runtime
  DB is actually finished.
- State that credits will be used for tests, docs, parser robustness, and
  source-grounded answer quality rather than private data processing.

Short form draft, under 500 Korean characters:

> 현재 pre-1.0이지만 31개 MCP 도구, stdio/HTTP 서버, SQLite runtime DB,
> evidence document layer, raw storage metadata, DART 한도 인식 백필러,
> 감사·투자자 관점 테스트를 갖추고 있습니다. 다음 단계는 5개년 compact
> runtime DB 완성, 공개 endpoint 안정화, contributor-friendly fixture와
> 보안 점검 체계를 강화하는 것입니다.
