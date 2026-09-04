# KReports 2021–2025 annual source archive

이 폴더는 DART 사업보고서와 감사보고서의 재처리 가능한 원문 corpus다. 공개 MCP는
이 Drive를 요청마다 읽지 않으며, 여기의 원문 증가는 자동 배포를 뜻하지 않는다.

## 탐색 순서

1. `00_PIPELINE/README.md` — 로컬 worker 설치·실행·재개 방법
2. `01_SKILL.md` — 원문 수집과 품질 경계
3. `00_PIPELINE/ASSIGNMENTS.md` — 담당 연도/shard 확인
4. `2021` … `2025` — 해당 사업연도의 issuer/receipt/report-kind별 raw·parsed·manifest

`objects/`는 기존 hash-layout에서 이미 적재된 legacy object다. 삭제하지 않으며,
새 source archive writes는 `<year>/<corp_code>/<receipt>/<report_kind>/` 형태로
들어간다. 기존 object migration은 별도 mapping을 남긴 뒤 진행한다.

## 안전 경계

- DART API key, Google Drive OAuth token/rclone config, local spool과 state는 Drive에
  저장하거나 공유하지 않는다.
- worker는 자신의 담당 범위만 실행하며, 중단 후 같은 local state directory로 재개한다.
- source archive와 candidate DB/MCP release는 별도 단계다. 이 폴더의 파일을 수정해
  runtime DB를 바꾸지 않는다.
