# CONTEXT.md — kreports_dart_mcp

## Project Profile
- 개요: DART 공시를 수집, 파싱하고 MCP/CLI/API 형태로 제공하는 한국 공시 데이터 도구 프로젝트입니다.
- 목적: 감사, 재무분석, 공시 검토 업무에서 DART 자료 접근과 구조화 비용을 줄이고 다른 에이전트가 재사용할 수 있는 인터페이스를 제공합니다.

## Current Scope

collector/backfill 실행 근거, HTTP MCP 안정화, 캐시/패키지 사용 경로를 Harness 상태판에 남겨야 합니다. 운영 확인은 배포 완료가 아니라 반복 실행 근거가 있다는 의미입니다.

## Product Direction

- MCP 응답은 사용자-facing 단계에서 JSON dump가 아니라 한국어 문장, 단락, 근거 목록 형태여야 합니다. 구조화 dict/JSON은 내부 API, 테스트, UI 렌더링용으로 유지하되 MCP 사용자는 verdict-first 서술형 답변을 받아야 합니다.
- 공개 MCP endpoint의 기본 데이터는 cache-first/read-only입니다.
- 온디맨드 수시공시 조회는 예외적으로 허용할 수 있으나, 서버 보유 `DART_API_KEY`를 쓰지 않고 사용자별 OpenDART API key를 입력받아 1회성 fetch/cache에 사용해야 합니다.
- 사용자 제공 API key는 저장, 로그 출력, 응답 반영을 금지합니다.
