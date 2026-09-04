# KReports source-archive worker pipeline

이 폴더는 2021–2025 DART 사업·감사보고서 원문을 공동 적재하는 worker의 단일
참조점이다. **원문 저장소와 공개 MCP DB는 별개**다. 여기의 작업은 Drive 원문
object를 늘릴 뿐, MCP runtime DB를 바꾸거나 배포하지 않는다.

## 폴더 사용법

1. `worker-bundles/`에서 중앙 담당자가 지정한 SHA-256 bundle을 내려받아 로컬에
   푼다. `checksums/`의 checksum과 일치해야 한다.
2. `ENV.template`을 자기 컴퓨터의 private `.env.collector`로 복사하고, 예시 값을
   채운다. 이 private 파일·DART API key·rclone OAuth config는 Drive에 올리지
   않는다.
3. `ASSIGNMENTS.md`에서 받은 한 개의 비중복 연도 또는 연도×shard 범위만 설정한다.
   자동 supervisor는 지정된 연도를 앞에서부터 모두 채운 뒤 다음 연도로 이동한다.
4. bundle 폴더에서 `uv sync --frozen` 후 `scripts/source_archive_auto_backfill.sh`를
   실행한다. 중단되어도 자신의 local state directory와 spool을 보존하고 같은 설정으로
   재실행한다.

## 현재 배포본

- Bundle: `kreports-source-archive-worker-98b7021db545-snapshot-20260901T081243Z.tar.gz`
- SHA-256: `29b63dc8c18e6aed3ac18c172096b8be03a59fd5379a91747f476f1bbe809776`
- Base commit: `98b7021db545e970ed143f0421bb3007b7303c68`
- 상태: **작업용 source snapshot**. 원문 backfill 실행용이며, candidate DB 또는 MCP
  release artifact가 아니다. 정식 worker release는 reviewed clean commit으로 같은
  폴더에 별도 immutable bundle을 추가한다.

## 필수 의존성

- Python 3.11 이상과 `uv`
- `rclone` (Google Drive remote는 `type = drive`이며 본인 OAuth client를 사용)
- OpenDART API key (본인 키; 다른 worker와 공유하지 않음)
- 중앙에서 제공한 읽기 전용 candidate DB snapshot
- 이 Drive folder에 object를 생성할 수 있는 권한

`rclone config`에서 만든 config는 owner-only로 제한한다.

```bash
chmod 600 ~/.config/kreports/rclone.conf
rclone config show <your-remote>
```

## 공통 Drive와 각자 로컬의 경계

| 공유 Drive | 각 worker 로컬 |
| --- | --- |
| `objects/`의 content-addressed raw XML/ZIP/parse/manifest | private `.env.collector`, DART key, rclone OAuth config |
| 이 `pipeline/` 문서와 worker bundle | `state-dir`, spool, logs, outcomes checkpoint |
| assignment와 bundle checksum | 실행 중인 Python/uv environment |

성공한 raw object는 SHA-256 path로 저장한다. 빠른 backfill에서는
`RAW_STORAGE_VERIFY_READBACK_ON_SUCCESS=0`으로 즉시 원격 readback을 생략할 수
있다. copy 오류·timeout은 성공으로 처리하지 않으며 local spool을 남겨 재개한다.
candidate/release DB artifact는 이 설정과 무관하게 strict readback을 유지한다.

## 동시 작업 규칙

- 한 assignment는 한 worker만 맡는다. 다른 사람이 같은 연도×shard를 시작하지
  않는다.
- worker마다 DART API key, Drive OAuth client, `STATE_DIR`를 분리한다.
- 공유 object path의 `--ignore-existing`은 같은 bytes의 재전송을 무해하게 만들 뿐,
  assignment 충돌을 해결하는 분산 lock은 아니다.
- quota stop 또는 `drive_transport_failure`가 나면 새 worker를 추가하지 말고, 해당
  worker가 60초 후 같은 checkpoint에서 재개한다.

## 중앙 담당자만 하는 일

- `ASSIGNMENTS.md`에 bundle SHA와 worker 소유 범위를 기록한다.
- 새 worker bundle은 immutable 이름과 checksum으로만 추가한다. 기존 bundle을
  덮어쓰지 않는다.
- source archive completion을 candidate DB/release completion으로 간주하지 않는다.
  worker outcome과 Drive manifest를 집계한 별도 candidate build·release gate가
  필요하다.

관련 상세 계약은 bundle의 `docs/source-archive-backfill.md`를 따른다.
