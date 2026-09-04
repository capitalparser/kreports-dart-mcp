# Skill: annual source-archive worker

목적은 다년 사업보고서·감사보고서의 원문 XML/ZIP과 generic parse package를
손실 없이 Drive에 보존하는 것이다. 기능별 DB 추출은 그 다음 단계이며, 새 기능이
생겨도 원문을 다시 다운로드하지 않고 archived parse에서 재처리한다.

## 입력과 산출물

- 입력: 중앙에서 지정한 읽기 전용 candidate DB, 담당 연도/shard, 본인 DART API key
- 원문: DART `document.xml` 원 응답 container와 선택된 business/audit XML member
- 산출물: raw, parsed structure, document manifest
- 경로: `years/<fiscal_year>/<corp_code>/<receipt>/<business_report|audit_report>/<role>/`

## 실행 규칙

1. `00_PIPELINE/ASSIGNMENTS.md`에서 중복 없는 범위를 확인한다.
2. `00_PIPELINE/ENV.template`을 private local env로 복사해 본인 API/OAuth만 입력한다.
3. local spool에 한 asset만 유지하고 SHA-256을 계산한다.
4. 성공한 raw copy는 content hash filename으로 immutable하게 저장한다. 고속 backfill은
   즉시 remote readback을 생략할 수 있지만 copy 실패·timeout이면 spool을 보존한다.
5. 같은 raw bytes를 parse하고 parse package와 document manifest를 업로드한다.
6. candidate DB build/release는 실행하지 않는다.

## 금지 사항

- 다른 worker의 연도/shard를 중복 실행하지 않는다.
- Drive의 raw XML을 편집·덮어쓰기·삭제하지 않는다.
- DART key/rclone OAuth config를 Drive, Git, manifest, log에 올리지 않는다.
- `partial_source` 또는 parser `requires_review`를 원문 부재나 완료로 바꾸지 않는다.
