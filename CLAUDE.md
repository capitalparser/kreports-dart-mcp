# kreports-dart CLAUDE.md

## Project Identity

- 이 디렉터리는 상위 `LLM_Wiki`의 감사 문서 작성 작업과 별개로 운영한다.
- 현재 목표는 `DART(OpenDART) 기반 기업공시/재무 데이터 수집 및 분석 기능을 MCP로 노출하는 제품형 코드베이스`를 발전시키는 것이다.
- 이 저장소는 단순 API 래퍼가 아니라 `MCP server + Python package + CLI + FastAPI + Streamlit dashboard`를 함께 포함한다.
- 따라서 새 작업은 가능하면 기존 구조를 확장하는 방식으로 수행하고, 별도 프로토타입을 중복 생성하지 않는다.

## Primary Goal

- 최우선 산출물은 `kreports/mcp`를 중심으로 한 안정적인 MCP 인터페이스다.
- DART 원천 데이터 수집, 파싱, 정규화, 분석 로직은 MCP 품질을 뒷받침하는 하위 계층으로 취급한다.
- CLI, API, 대시보드는 MCP와 동일한 도메인 로직을 재사용해야 하며, 기능이 갈라지지 않도록 유지한다.

## In Scope

- `kreports/mcp`: MCP server, tool 정의, tool 입출력 계약, Claude/Desktop 연동
- `kreports/collector`: DART 수집 로직, 동기화, 스케줄링, 재시도
- `kreports/processor`: XBRL/XML/공시문서 파싱 및 정규화
- `kreports/analysis`, `kreports/judge`: 분석 API, 지표 계산, 위험신호 로직
- `kreports/db`: SQLite 기반 모델/쿼리 계층
- `kreports/cli`, `api`, `dashboard`: 동일한 도메인 로직을 노출하는 인터페이스 계층
- `tests`: 단위 테스트, 회귀 테스트, MCP 계약 검증
- `README.md`, 예시 설정 파일, 운영 문서

## Out Of Scope

- 상위 폴더의 감사 보고서, 이슈 메모, judgment journal, precedent 축적 체계
- K-IFRS HTML 보고서 테마 및 감사 산출물 포맷 규칙
- 근거 문서 자동작성 자체를 이 저장소의 1차 목표로 두는 작업
- DART와 무관한 범용 MCP 실험 코드를 이 디렉터리에 추가하는 작업

## Source Of Truth

1. DART/OpenDART 응답 원문 및 공식 필드 정의
2. 현재 저장소의 테스트가 고정한 동작 계약
3. 공개 API 스키마와 MCP tool 스키마
4. README 및 예시 설정

- 과거 임시 스크립트나 수동 메모보다 실제 코드와 테스트를 우선한다.
- 스키마를 바꾸는 경우, 호출자 영향 범위를 먼저 확인하고 테스트/문서를 같이 갱신한다.

## Working Rules

- 새 MCP 기능은 가능하면 `kreports/mcp/tools.py`와 관련 분석 계층을 확장해 구현한다.
- 동일한 계산 로직을 MCP, CLI, API, dashboard에 각각 복제하지 않는다.
- DART 호출 한도와 응답 지연을 고려해 불필요한 실호출을 피한다.
- 테스트에서는 실제 DART API 호출보다 mock, fixture, 샘플 응답을 우선한다.
- `DART_API_KEY`는 `.env`나 사용자 환경변수로만 취급하고, 코드/문서/테스트에 하드코딩하지 않는다.
- `.env`, 로그 파일, `kreports.db` 같은 로컬 산출물은 기능 구현 대상이 아닌 한 불필요하게 수정하지 않는다.
- 수집기나 파서 변경 시에는 다운스트림 분석/MCP 출력 형식이 어떻게 바뀌는지 함께 점검한다.
- 한국어 공시 데이터가 원문이므로, 문자열 정규화와 인코딩 처리를 변경할 때는 회귀 위험을 기본 가정한다.

## Code Change Priorities

1. 정확한 데이터 해석
2. 안정적인 MCP/API 계약
3. 재사용 가능한 도메인 로직
4. 테스트 가능성
5. 운영 편의성

- 보기 좋은 우회 구현보다 계약이 분명한 구조를 우선한다.
- 기능 추가보다 기존 tool의 의미를 깨지 않는 변경을 우선 검토한다.

## Validation

- 작은 로직 수정이라도 관련 `pytest` 테스트를 우선 실행한다.
- MCP 변경 시 적어도 tool 목록, 입력 스키마, JSON 직렬화 가능 여부를 확인한다.
- API 변경 시 FastAPI 스키마 또는 응답 모델 영향 여부를 확인한다.
- 문서화가 필요한 사용자 가시적 변경은 `README.md` 또는 예시 설정 파일까지 반영한다.

## Common Commands

```powershell
pytest
pytest tests/test_dart_mcp.py
python -m kreports.mcp
python -m kreports.cli.main --help
uvicorn api.main:app --reload
streamlit run dashboard/app.py
```

## Collaboration Notes For Agents

- 이 디렉터리에서는 상위 감사 프로젝트 문맥을 자동으로 끌고 오지 않는다.
- `DART API를 MCP로 어떻게 안정적으로 노출할지`를 중심 질문으로 삼는다.
- 새 파일을 만들기 전에 기존 `kreports`, `api`, `dashboard`, `tests` 구조에서 수용 가능한 위치를 먼저 찾는다.
- 문서 초안, TODO, 실험 코드를 남길 때도 최종적으로는 제품 구조에 편입될 수 있는 형태를 선호한다.

