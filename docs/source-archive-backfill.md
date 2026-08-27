# Drive-first annual source archive backfill

이 문서는 KReports 관리자가 2021–2025년 등 다년 사업보고서와 감사보고서
원문을 보전하고, 나중의 새 추출 기능에도 다시 사용할 수 있게 하는 **로컬
수집기 전용** 운영 절차다. 공개 MCP 서비스의 운영 절차나 현재 데이터
커버리지 보고서가 아니다. 이 가이드 자체는 어떤 연도·회사 원문이 이미
백필되었다고 주장하지 않는다.

## 먼저 지켜야 할 경계

```text
로컬 수집기 -> 검증된 Google Drive 원문 아카이브 -> 별도 후보 DB/릴리스 검증 -> 읽기 전용 MCP artifact
```

- **public MCP queries do not call Google Drive.** 공개 MCP는 별도로 검증·배포한
  읽기 전용 SQLite artifact와 그 release manifest만 읽는다.
- Google Drive는 원문과 일반 구조(parse) 패키지를 보전하는 장기 보관소다.
  **Do not mount SQLite on Google Drive.** Drive를 SQLite 파일시스템·실시간
  질의 DB·공개 MCP 캐시로 쓰지 않는다.
- 수집·백필은 개인 로컬 컴퓨터의 collector mode에서만 한다. Lightsail 또는
  공개 MCP 컨테이너에는 DART/Drive credential, 수집 쓰기 권한, spool을 둬서는
  안 된다.
- 이 작업은 후보 DB나 운영 artifact를 변경하지 않는다. 아카이브를 바탕으로
  후보 DB를 새로 만들고 검증해 release promotion을 하는 일은 별도 승인
  단계다.

## 무엇을 원문으로 남기는가

원문 보전의 기준은 당장 필요한 지표가 아니라, 나중의 새 추출기·새 질문이
근거를 다시 읽을 수 있는가이다. 따라서 한 회사·사업연도에 사업보고서와
선정된 감사보고서 package를 모두 수집한다.

1. **사업보고서:** DART가 ZIP을 주면 original ZIP/container을 먼저 보전하고,
   그 안의 XML member를 container SHA-256, Drive URI, member name과 함께
   연결한다. DART가 ZIP 대신 XML을 직접 주면 그 원본 응답 container를 XML
   media type으로 별도 표기하며 ZIP으로 가장하지 않는다.
2. **감사보고서:** DART viewer에서 받은 HTML을 우선 원문으로 남긴다. viewer가
   비어 있거나 PDF가 공식 fallback인 경우에는 official PDF bytes를 원문으로
   남긴다.
3. **일반 구조 패키지:** 각 원문 hash에 묶어 heading, block, table/cell,
   caption, footnote와 미해석 노드를 JSON으로 저장한다. 이것은 현 시점의
   기능별 추출 결과가 아니라, 이후 parser/LLM 기능을 재실행할 수 있는
   재사용 가능한 구조 근거다.

HTML/XML은 표·섹션·각주 위치를 보존하는 **primary parse path**다. PDF는
원본 byte의 감사 가능한 fallback이며, PDF가 존재한다는 이유만으로 HTML/XML
원문을 대체하거나 구조 파싱이 완료되었다고 판단하지 않는다. 원문 byte와
parse JSON은 content-addressed SHA-256 object로 압축 보관되며, 같은 byte는
중복 업로드하지 않는다.

## 사전 준비: 로컬 collector와 Drive remote

다음은 관리자의 로컬 컴퓨터에서만 수행한다. credential은 repository, README,
shell history, 화면 공유에 넣지 않는다.

1. `rclone`을 설치하고, Google Drive 유형의 named remote를 대화형으로
   설정한다. 예시는 remote 이름일 뿐이며 실제 OAuth token을 명령·문서에
   넣지 않는다.

   ```bash
   rclone config
   rclone config redacted <drive-remote-name>
   rclone lsd '<drive-remote-name>:'
   ```

   redacted config 결과에서 `type = drive`를 확인한다. 공개 MCP 서버에
   remote 설정을 복사하지 않는다.

2. bounded local spool(한 번에 처리 중인 압축 object만 두는 임시 작업 공간)을
   별도 경로에 만든다. spool에는 원문/압축본을 장기 적재하지 않고, Drive
   read-back hash 검증이 끝난 뒤에만 해당 임시 파일을 지운다. 검증 실패 시
   파일을 보존해 원인을 조사하며, 재시도 전에 삭제하지 않는다.

   ```bash
   mkdir -p "$HOME/.cache/kreports/source-archive-spool"
   chmod 700 "$HOME/.cache/kreports/source-archive-spool"
   ```

3. Drive 대상 경로의 여유 공간·보존 정책과 local spool의 여유 공간을 먼저
   확인한다. `rclone size`는 Drive에 실제로 저장된 압축 object와 manifest/event
   크기를 보여주는 점검용 수치다. 원문 uncompressed byte 합계, compressed
   object byte 합계, object count, manifest/event overhead를 campaign별로
   함께 기록한다. SHA-256 dedup 때문에 원문 합계와 Drive 사용량은 같지 않을
   수 있다.

   ```bash
   rclone size '<drive-remote-name>:<archive-root>' --json
   df -h "$HOME/.cache/kreports/source-archive-spool"
   ```

4. collector 환경은 로컬의 비밀 파일에서만 불러온다. 아래 이름은 값 없이
   보여 주는 계약 예시다.

   ```bash
   export KREPORTS_RUNTIME_MODE=collector
   export KREPORTS_ENABLE_RAW_BACKFILL=1
   export RAW_STORAGE_BACKEND=drive
   export RAW_STORAGE_DRIVE_REMOTE='<drive-remote-name>:'
   export RAW_STORAGE_PREFIX='<archive-root>'
   export RAW_STORAGE_SPOOL_DIR="$HOME/.cache/kreports/source-archive-spool"
   ```

   실제 `DART_API_KEY` 값은 이 문서나 Git에 기록하지 않는다. `--apply`는
   collector mode, raw-backfill opt-in, named `type=drive` remote가 모두
   갖춰지지 않으면 실패해야 한다.

## campaign 생성과 실행 순서

`--db`는 historical KOSPI/KOSDAQ membership evidence와 disclosure metadata를
가진 **읽기 전용 후보 DB**다. 활성 MCP DB, Lightsail mount, 혹은 그 복사본을
입력으로 쓰지 않는다. 대상 연도나 선정 규칙이 달라지면 기존 campaign을
바꾸지 말고 새 state directory를 만든다.

### 1. no-write preflight

먼저 대상의 historical membership, 연도별 시장 근거, anchor metadata를 읽기
전용으로 검사한다. 이 명령은 DART 요청, Drive 요청, Drive upload를 하지
않는다. `no_source_metadata`는 "공시가 없었다"는 결론이 아니라, 현재 anchor
metadata가 부족하다는 명시적 gap이다.

```bash
uv run kreports source-archive-preflight \
  --db /path/to/candidate.db \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

출력의 target digest, discovered count, `no_source_metadata` count를 campaign
기록에 남긴다. 이 결과는 Drive 용량·rclone 접근·DART quota를 확인한 결과가
아니다. 그것들은 위의 로컬 운영 사전 점검과 실제 apply guard에서 별도로
확인한다.

### 2. local target preview와 shard dry run

64개 shard가 기본값이며, 같은 `corp_code`는 모든 선택 연도에서 항상 같은
`sha256(corp_code) % 64` shard에 속한다. 따라서 한 shard를 중단·재개해도
회사 membership이 바뀌지 않는다.

```bash
CAMPAIGN_DIR="$HOME/.local/share/kreports/source-archive-2021-2025"

uv run kreports source-archive-plan \
  --db /path/to/candidate.db \
  --state-dir "$CAMPAIGN_DIR" \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025

uv run kreports source-archive-run \
  --db /path/to/candidate.db \
  --state-dir "$CAMPAIGN_DIR" \
  --shard 7 \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

`source-archive-plan`은 `TARGET.preview.json`만 local state directory에
작성한다. `source-archive-run`에서 `--apply`를 생략하면 완전한 dry run이며
DART/Drive 호출도 raw file 작성도 하지 않는다.

### 3. 명시적·유한한 apply

preflight와 dry run을 검토한 뒤에만 한 shard를 실행한다. `--apply`와 유한한
`--max-dart-calls`를 반드시 같이 지정한다. budget은 retry, attachment viewer,
PDF fallback을 포함한 실제 DART HTTP 시도마다 먼저 하나씩 소진된다.

```bash
uv run kreports source-archive-run \
  --db /path/to/candidate.db \
  --state-dir "$CAMPAIGN_DIR" \
  --shard 7 \
  --apply --max-dart-calls 100 \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

첫 `--apply`는 DART 요청보다 먼저 complete target list를 Drive에 immutable
object로 보관하고, 그 URI/SHA-256을 `TARGET.json`에 결속한다. 이것이 campaign의
frozen denominator다. 실행 중인 campaign에서 `TARGET.json`을 교체하거나
대상을 추가·삭제하지 않는다.

## 보전·검증·재개 상태

각 asset은 다음 순서로 처리한다.

```text
download raw bytes -> SHA-256 -> immutable Drive upload -> Drive read-back verification
-> generic parse -> parse package/document manifest archive -> local outcome checkpoint
```

동일 원문 SHA-256이 이미 Drive에 있으면 먼저 원격 압축 object를 읽어
decompress·byte length·SHA-256을 확인한 뒤 재사용한다. 새 object도 upload 뒤
동일 검증을 통과하기 전에는 성공으로 기록하지 않는다. document manifest와
append-only campaign event는 Drive에 남고, local `outcomes.jsonl`은 재개를 위한
cache/checkpoint다.

campaign outcome과 asset document manifest에는 아래 상태가 남는다.

| 상태 | 뜻과 다음 행동 |
|---|---|
| `discovered` | annual anchor를 찾음. 아직 asset 수집 전이다. |
| `archived_verified` | 원문 byte의 Drive read-back 검증까지 완료했다. |
| `generically_parsed` | source hash에 묶인 일반 구조 JSON을 보전했다. |
| `structurally_complete` | 사업보고서와 primary 감사보고서 package가 모두 완전 구조 상태다. |
| `requires_review` / `partial_source` | `requires_review`는 asset document manifest의 parser 검토 상태이고, 그 결과 company-year outcome은 `partial_source`다. 완료로 취급하지 않고 재시도 또는 검토한다. |
| `dart_budget_exhausted` | 이번 실행의 호출 상한에 도달했다. 다음 유한 budget 실행에서 재개한다. |
| `fetch_failed` / `asset_failed` | 원문 수신 또는 archive 검증이 실패했다. 오류와 raw evidence를 점검 후 재개한다. |
| `no_source_metadata` | frozen target에 source anchor metadata가 없다. missing disclosure로 추정하지 않는다. |

shard의 모든 company-year가 `structurally_complete`일 때만
`shard-XX/COMMITTED.json`이 생긴다. marker는 frozen target digest와 outcomes
checksum을 묶는다. partial shard에는 marker가 없으며, 다음 동일 shard 실행은
이미 complete인 company-year만 건너뛰고 미완료 항목을 재개한다. local outcome
cache와 marker의 결속이 완료된 shard에서 변조되면 verify/run은 실패해야 한다.

진행 점검은 외부 호출 없이 할 수 있다.

```bash
uv run kreports source-archive-verify \
  --state-dir "$CAMPAIGN_DIR" --shard 7
```

## campaign 뒤의 DB와 릴리스

Drive archive는 배포 DB가 아니다. source archive가 늘어나도 현재 공개 MCP의
조회 결과는 변하지 않는다. 다음 두 단계는 서로 분리하고, 각각 대상·rollback·
검증 증거를 검토한 뒤 별도 승인을 받는다.

1. archive 원문/일반 구조 패키지를 사용해 **새 writable candidate DB**를
   생성·추출·coverage 검증한다. 기존 공개 artifact를 직접 수정하지 않는다.
2. candidate DB의 release manifest와 artifact 검증을 통과한 경우에만 immutable
   runtime artifact를 만들어 Lightsail의 읽기 전용 deployment pair로 promote한다.

공개 runtime 운영과 promotion의 상세 절차는
[HTTP MCP deployment guide](deploy-http-mcp.md)를 따른다. code test, Drive
object 존재, 한 shard의 `COMMITTED.json` 중 어느 하나만으로 전체 5개년 data
coverage나 production release readiness가 증명되지는 않는다.

## 운영 기록 최소 항목

각 campaign마다 다음을 함께 기록하면 재현·감사가 가능하다.

- years, shard count, target digest, candidate DB identity와 membership evidence;
- Drive archive root, object count, compressed/original byte accounting,
  dedup 설명과 용량 점검 시각;
- parser version, raw/parse object URI·SHA-256, DART receipt와 source locator;
- shard별 DART call budget/used count, terminal status, error/retry 사유;
- candidate build와 release promotion의 별도 승인·검증 결과.

이 기록은 raw filing 자체를 Git에 커밋하라는 뜻이 아니다. Git에는 코드와
안전한 manifest/reference만 두고, 대용량 원문은 검증된 외부 archive에 둔다.
