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
2. **감사보고서:** 사업보고서 `document.xml` ZIP 안의 명시적 감사보고서 XML
   member를 먼저 찾는다. 없으면 같은 회사·사업연도의 별도 감사보고서 공시
   receipt를 찾고, 그 receipt의 `document.xml` ZIP/XML을 수집한다. 두 경로 모두
   `source_type='audit_report'`, receipt, container SHA-256, XML member name으로
   사업보고서와 같은 형식에 보전한다.
3. **일반 구조 패키지:** 각 원문 hash에 묶어 heading, block, table/cell,
   caption, footnote와 미해석 노드를 JSON으로 저장한다. 이것은 현 시점의
   기능별 추출 결과가 아니라, 이후 parser/LLM 기능을 재실행할 수 있는
   재사용 가능한 구조 근거다.

XML은 표·섹션·각주 위치를 보존하는 **감사보고서의 required parse path**다.
viewer/PDF는 XML 후보의 부재를 조사할 때 참고할 수 있는 보조 증거일 뿐,
`audit_report` 원문이나 구조 파싱 완료를 대체하지 않는다. 원문 byte와 parse
JSON은 content-addressed SHA-256 object로 압축 보관되며, 같은 byte는 중복
업로드하지 않는다.

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

3. Drive 대상 경로의 archive-root 사용량·보존 정책과 local spool의 여유 공간을
   먼저 확인한다. `rclone size`는 archive root에 실제로 저장된 압축 object와
   manifest/event 크기를 보여주는 usage/object-accounting 수치이며, Drive의
   **남은 quota를 검사하지 않는다**. remote가 지원하면 `rclone about`으로
   계정 quota를 확인하고, apply 전에는 Google Drive UI의 available quota와도
   교차 확인한다. 원문 uncompressed byte 합계, compressed object byte 합계,
   object count, manifest/event overhead를 campaign별로 함께 기록한다.
   SHA-256 dedup 때문에 원문 합계와 Drive 사용량은 같지 않을 수 있다.

   ```bash
   rclone size '<drive-remote-name>:<archive-root>' --json
   rclone about '<drive-remote-name>:' --json
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
   # 기존 API/Drive 파이프라인의 operator-owned 설정 파일을 그대로 재사용한다.
   # 이 파일에는 OAuth credentials가 있으므로 Git/Drive/spool 밖에 두고:
   export RAW_STORAGE_RCLONE_CONFIG="$HOME/.config/rclone/rclone.conf"
   chmod 600 "$RAW_STORAGE_RCLONE_CONFIG"
   # The named rclone Drive remote must contain a dedicated client_id. Configure
   # it with `rclone config` (preferred), or use a backend override rclone
   # actually consumes, for example:
   # export RCLONE_CONFIG_KREPORTS_DRIVE_CLIENT_ID='<dedicated-rclone-client-id>'
   # export RCLONE_DRIVE_CLIENT_ID='<dedicated-rclone-client-id>'
   # Conservative defaults; keep burst at 1 unless a reviewed quota change allows it.
   export RAW_STORAGE_RCLONE_TPSLIMIT=0.5
   export RAW_STORAGE_RCLONE_TPSLIMIT_BURST=1
   export RAW_STORAGE_DRIVE_RATE_LIMIT_RETRIES=2
   export RAW_STORAGE_DRIVE_RATE_LIMIT_COOLDOWN_SECONDS=60
   export RAW_STORAGE_DRIVE_RATE_LIMIT_MAX_COOLDOWN_SECONDS=900
   ```

   실제 `DART_API_KEY` 값은 이 문서나 Git에 기록하지 않는다. `--apply`는
   collector mode, raw-backfill opt-in, named `type=drive` remote가 모두
   갖춰지지 않으면 실패해야 한다. source-archive `--apply`는 기본적으로
   named rclone remote의 `client_id` 또는 rclone이 소비하는 위 override가
   확인되지 않으면 시작하지 않는다. `RAW_STORAGE_DRIVE_CLIENT_ID` 같은
   애플리케이션 전용 marker는 증명으로 인정하지 않는다. 공유 OAuth client를
   사용하는 진단용 예외는 `KREPORTS_DRIVE_ALLOW_SHARED_CLIENT_DIAGNOSTIC=1`을
   명시적으로 설정한 일회성 점검에만 허용하며, 일반 백필에는 사용하지 않는다.

   `RAW_STORAGE_RCLONE_CONFIG`를 지정하면 KReports는 기존 파이프라인의
   rclone remote와 OAuth credentials를 새로 복사하지 않고 그 파일을 모든
   `rclone` 호출에 `--config`로 고정한다. 파일은 collector 사용자 소유의 일반
   파일이어야 하고 group/other 권한이 없어야 한다(`chmod 600`). 경로나 파일
   내용은 manifest와 로그에 넣지 않는다. 이 방식은 기존 Google Cloud
   프로젝트와 인증 파이프라인을 재사용하는 것이며, 별도 프로젝트를 새로 만들
   필요는 없다. 단, 선택한 remote에 실제 전용 `client_id`가 있어야 한다는
   source-archive apply gate는 그대로 유지된다.

   `rclone` 명령에는 기본적으로 `--tpslimit 0.5 --tpslimit-burst 1`이 붙는다.
   값은 위 환경변수로 조정할 수 있지만 TPS는 0.1~2.0, burst는 1~4 범위로
   제한된다. Google Drive의 분당 query quota는 rclone 한 명령보다 넓은
   계정·프로젝트 단위로 계산될 수 있으므로, 이 값은 안전한 전송 상한이지
   정확한 API query 수의 보증이 아니다. 단일 collector의 writer lease는 그
   collector의 local spool만 보호한다. 여러 사람의 로컬 worker에는 공유 lock이
   아니므로, 동시 실행은 중앙 assignment가 서로 겹치지 않는 연도/shard를
   배정하고 각 worker가 별도 Drive OAuth client와 DART key를 쓰는 경우에만
   허용한다. 같은 content-addressed object는 `--ignore-existing`으로 합류하지만,
   같은 assignment를 두 번 주거나 같은 OAuth client의 quota를 합산하는 운영은
   금지한다. rate-limit cooldown의 not-before 시각은 현재 프로세스 메모리에만 있다.
   프로세스를 재시작하면 cooldown 자체는 복원되지 않으므로, quota stop 뒤에는
   운영자가 충분히 기다린 뒤 재개해야 한다. outbox/checkpoint 보존은 유지된다.

## campaign 생성과 실행 순서

### Drive 탐색 구조

Drive archive root의 첫 화면에는 `00_README.md`, `01_SKILL.md`, `00_PIPELINE/`를
두고, 원문은 `2021`부터 `2025`까지 연도 폴더 아래에 둔다. 새 asset 경로는 다음과
같다.

```text
<year>/<corp_code>/<receipt_no>/<business_report|audit_report>/<raw|container|parsed|manifest>/<sha256>.<extension>.gz
```

hash는 파일명으로 남겨 immutable identity를 유지한다. 기존 `objects/sha256/`는
이미 기록된 URI의 legacy object 영역이므로 migration mapping 없이 이름을 바꾸거나
삭제하지 않는다.

### 공유 Drive worker 운영

Drive의 `pipeline/`에는 검증된 worker bundle과 실행 문서를, archive root에는
원문·parse·manifest object를 둔다. worker는 bundle을 **로컬에 내려 받아** 실행한다.
Drive에서 Python 환경이나 secret을 직접 실행하지 않는다. 각 worker는 다음을
독립적으로 보유한다.

- 자기 DART API key와 자기 rclone OAuth config (공용 secret 전달 금지)
- 고유 `KREPORTS_SOURCE_ARCHIVE_STATE_DIR`
- 중앙 assignment에서 받은 비중복 `KREPORTS_SOURCE_ARCHIVE_YEARS` 또는 shard set

모든 worker는 같은 frozen candidate DB revision, archive prefix, `SHARD_COUNT=64`,
그리고 pipeline bundle SHA를 사용해야 한다. work assignment는 한 사람에게
`2021`처럼 연도 전체를 주거나, 더 세분화가 필요하면 **연도 × shard** 단위를
주되 절대 겹치지 않게 한다. 각각의 local `outcomes.jsonl`은 checkpoint이며,
Drive의 immutable object가 공통 원문 저장소다. 중앙 담당자는 bundle 버전과
assignment 표를 Drive `pipeline/`에 갱신하고, 나중의 candidate DB 생성 전에는
각 worker의 outcome/Drive manifest를 한 번에 집계한다.

`--db`는 historical KOSPI/KOSDAQ membership evidence와 disclosure metadata를
가진 **읽기 전용 후보 DB**다. 활성 MCP DB, Lightsail mount, 혹은 그 복사본을
입력으로 쓰지 않는다. 대상 연도나 선정 규칙이 달라지면 기존 campaign을
바꾸지 말고 새 state directory를 만든다.

## all-issuer v3 universe: archive inclusion and historic status are separate

기본 `listed` universe는 검증된 해당 연도 KOSPI/KOSDAQ membership만 대상으로
하는 기존 v2 절차다. 모든 annual-report issuer를 원문 보전 분모에 넣을 때는
명시적으로 `--universe all-annual-issuers`를 사용한다. 이 v3 universe는 기존
verified listed pair를 모두 유지하고, canonical annual-report anchor가 있으나
검증된 KOSPI/KOSDAQ membership이 없는 issuer를
`annual_report_issuer_outside_verified_markets` cohort로 추가한다.

이 outside cohort의 historic status는 `unclassified`다. KOSPI/KOSDAQ membership이
없다는 것은 `not proof of unlisted`다. KONEX, 상장 이전·이전상장·상장폐지,
누락된 historical snapshot 또는 비상장 등 여러 가능성이 남아 있다. 따라서
archive inclusion은 현재 원문 보전 대상이라는 뜻일 뿐 historic listing conclusion이
아니다.

all-issuer apply는 기존 v2 campaign과 절대 섞지 않는다. 새 campaign마다 fresh v3 Drive prefix and local state directory를 만들고, v2 `TARGET.json`, event,
checkpoint, Drive target manifest를 복사·변경·resume 대상으로 쓰지 않는다. v3
target manifest는 `universe_mode`, cohort, historic-status basis와 frozen digest를
함께 묶는다. 기존 raw object를 재사용할 수 있는 경우도 SHA-256 read-back
verification으로 byte-identical임을 확인한 경우에 한한다.

현재 사용할 수 있는 bounded archive status와 이후의 historic-status promotion은
다음처럼 분리한다.

| 상태/근거 | 현재 할 수 있는 일 | historic claim |
|---|---|---|
| `annual_report_issuer_outside_verified_markets` + `unclassified` | canonical annual-report anchor를 v3 raw archive에 포함 | KOSPI/KOSDAQ 부재만으로 unlisted라고 말하지 않음 |
| dated official KRX KOSPI/KOSDAQ/KONEX raw exports와 year-specific normalization manifest | issuer-year를 all-market evidence와 대조 | `KONEX_verified` 또는 `not_krx_listed_verified` 가능 |
| dated issuer-status source | 위 evidence에 추가하여 issuer status를 확인 | 이때만 `unlisted_confirmed` 가능 |

`not_krx_listed_verified` 전에는 dated official KRX KOSPI/KOSDAQ/KONEX raw exports와
각 raw object의 source URI, retrieval/date, SHA-256, transformation version을 남긴
normalization manifest가 필요하다. `unlisted_confirmed`에는 그보다 한 단계 더
강한 dated issuer-status source가 필요하다. 이 분류 근거는 additive metadata이며,
이미 frozen된 v3 target을 제거·대체하거나 raw archive coverage를 완전하다고
주장하는 근거가 아니다.

### 1. no-write preflight

먼저 대상의 historical membership, 연도별 시장 근거, anchor metadata를 읽기
전용으로 검사한다. 이 명령은 DART 요청, Drive 요청, Drive upload를 하지
않는다. `no_source_metadata`는 "공시가 없었다"는 결론이 아니라, 현재 anchor
metadata가 부족하다는 명시적 gap이다.

```bash
uv run kreports source-archive-preflight \
  --db /path/to/candidate.db \
  --universe all-annual-issuers \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

all-issuer preflight의 `universe_mode`, cohort counts and target digest,
discovered count, `no_source_metadata` count를 campaign 기록에 남긴다. cohort별
대상·발견·gap count가 기대한 v3 분모와 맞지 않으면 plan 또는 apply로 진행하지
않는다. 이 결과는 Drive 용량·rclone 접근을 확인한 결과가 아니며,
**does not perform a reliable remaining-DART-quota preflight**. DART의 남은
quota/rate 상태는 조회 가능한 신뢰성 있는 사전검사로 가정하지 않는다. 실제
반응은 첫 bounded apply shard에서 발견·기록한다. Drive target manifest는 첫
DART 요청보다 먼저 생성될 수 있으므로, 그 shard를 시작하기 전 archive-root
quota를 별도로 확인해야 한다.

### 2. local target preview와 shard dry run

64개 shard가 기본값이며, 같은 `corp_code`는 모든 선택 연도에서 항상 같은
`sha256(corp_code) % 64` shard에 속한다. 따라서 한 shard를 중단·재개해도
회사 membership이 바뀌지 않는다. 아래 all-issuer v3 예시는 v2와 구별되는 Drive
prefix와 local state root를 사용한다. 이 예시의 placeholder는 실제 credential이나
private path를 뜻하지 않는다.

```bash
CAMPAIGN_DIR="$HOME/.local/share/kreports/source-archive-2021-2025-all-issuers-v3"
export RAW_STORAGE_PREFIX='<archive-root>/source-archive-v3-all-annual-issuers'

uv run kreports source-archive-plan \
  --db /path/to/candidate.db \
  --universe all-annual-issuers \
  --state-dir "$CAMPAIGN_DIR" \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025

uv run kreports source-archive-run \
  --db /path/to/candidate.db \
  --universe all-annual-issuers \
  --state-dir "$CAMPAIGN_DIR" \
  --shard 7 \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

`source-archive-plan`은 `TARGET.preview.json`만 local state directory에
작성한다. `source-archive-run`에서 `--apply`를 생략하면 완전한 dry run이며
DART/Drive 호출도 raw file 작성도 하지 않는다.

### 3. 명시적·유한한 apply

preflight와 dry run을 검토한 뒤에만 한 shard를 실행한다. `--apply`와 유한한
`--max-dart-calls`를 반드시 같이 지정한다. `--max-dart-calls` is a local physical-request cap; DART 계정의 남은 quota를 측정하거나 예약하지 않는다.
budget은 retry, 별도 감사보고서 receipt 탐색, `document.xml` 수집을 포함한 실제
DART HTTP 시도마다 먼저 하나씩 소진된다.

기본 Drive archive command deadline은 **default 60 seconds**다. 성공한
no-write preflight 뒤 reviewed v3 shard 0의 target freeze를 재개할 때만
`RAW_STORAGE_COMMAND_TIMEOUT_SECONDS=180`을 한 번 명시할 수 있다. 이 값은
1~**maximum 300 seconds** 범위의 collector 전용 설정이며, 각 rclone content
command의 deadline은 계속 유한하다. 이는 **not a retry, DART-budget, or shard authorization**이며, 다른 shard 실행·추가 DART 호출·production promotion을
허용하지 않는다.

```bash
export RAW_STORAGE_COMMAND_TIMEOUT_SECONDS=180

uv run kreports source-archive-run \
  --db /path/to/candidate.db \
  --universe all-annual-issuers \
  --state-dir "$CAMPAIGN_DIR" \
  --shard 0 \
  --apply --max-dart-calls 100 \
  --year 2021 --year 2022 --year 2023 --year 2024 --year 2025
```

첫 `--apply`는 DART 요청보다 먼저 complete target list를 Drive에 immutable
object로 보관하고, 그 URI/SHA-256을 `TARGET.json`에 결속한다. 이것이 campaign의
frozen denominator다. 실행 중인 campaign에서 `TARGET.json`을 교체하거나
대상을 추가·삭제하지 않는다.

Drive 403 응답 중 `rateLimitExceeded`, `userRateLimitExceeded`,
`rate_limit_exceeded`와 HTTP 429는 권한 오류나 404 missing과 구분한다. rate
limit이면 첫 cooldown을 최소 60초로 두고 제한된 truncated-exponential retry를
수행한다. 재시도가 소진되면 현재 shard는 `drive_quota_exhausted`라는
non-terminal stop으로 즉시 끝나며 `COMMITTED.json`을 만들지 않는다. 현재
company-year의 local outcome과 spool은 보존되고, 이후 target은 시도하지 않는다.
`rclone copyto` 또는 선택된 `rclone cat` readback의 timeout/일시 command 오류도
프로세스 오류로 종료하지 않고 `drive_transport_failure`로 기록한다. 이 경우 supervisor는
**60 seconds** 뒤 같은 checkpoint를 재개한다. checksum 불일치·권한 오류·확정
404는 이 transport 재시도 분류에 포함하지 않는다.

company-year별 campaign event는 여러 개의 Drive event upload 대신 하나의
`outbox/*.json` bundle로 먼저 local에 기록한다. 원문 source archive의 일반 성공
경로는 SHA-256 content-addressed path의 `rclone copyto --ignore-existing` 성공만
확인하고 (`RAW_STORAGE_VERIFY_READBACK_ON_SUCCESS=0`), 즉시 readback은 생략할 수
있다. copy 실패·timeout 때는 spool을 삭제하지 않는다. 별도
`source-archive-verify`/감사 작업은 필요한 범위에 strict readback을 수행한다. DB
candidate/release artifact는 이 설정과 무관하게 항상 strict readback 검증을 유지한다.
이후에만 outbox 파일을 삭제한다. 프로세스가 중단되면 다음 실행이 DART
호출 전에 pending bundle을 먼저 flush하므로, source archive와 event checkpoint의
순서를 잃지 않는다.

### 4. 전 shard 자동 재개

수동 `source-archive-run`은 한 shard를 점검할 때 사용한다. 장기 백필은
`source-archive-auto-run`이 **연도 우선**으로 동작한다. 선택한 첫 연도의 64개
shard를 모두 순회한 뒤 다음 연도로 넘어가며, 각 물리 배치를 계속 유한한
`--max-dart-calls 100`으로 실행한다. 이 로컬 100호출 경계는 DART의 일일 한도
응답이 아니다. `api_budget_exhausted`이면 **30 seconds** 뒤 같은 shard에서 새
100호출 배치를 시작한다.

DART가 실제 quota/limit 응답(`dart_quota_failure`, 예: status `020`)을 반환한
경우에는 날짜나 자정 초기화를 추측하지 않는다. **15 minutes**를 기다린 뒤 같은
미완료 target의 다음 실제 요청으로 이용 가능 여부를 다시 확인한다. 인증 실패는
자동 재시도하지 않고 `AUTH_BLOCKED`를 만들며, credential을 고친 관리자가 해당
파일을 제거해야 재개된다.

한 번도 시도하지 않은 company-year를 먼저 처리한다. 회사의 shard 번호는 연도
사이에서 고정되므로 중단 후에도 동일한 회사·연도 대상과 checkpoint를 재사용한다.
terminal
`partial_source`는 **24 hours**가 지난 뒤에만 재시도하여 같은 결손 보고서가 새
대상을 가로막지 않게 한다. 로컬 budget/transport stop은 non-terminal이므로 이
24시간 대기 대상이 아니며 다음 배치에서 즉시 이어진다. 다만 audit XML resolver
version이 없는 이전 `partial_source`는 새 resolver가 사업보고서 내부 XML과 별도
감사공시 XML을 한 번 다시 판별하도록 즉시 재시도한다. 새 version으로 기록된
partial은 다시 24시간 cadence를 적용한다.

```bash
# repository 밖의 owner-only 파일이며 둘 다 chmod 600으로 유지한다.
export KREPORTS_SOURCE_ARCHIVE_COLLECTOR_ENV=/path/to/.env.collector
export KREPORTS_SOURCE_ARCHIVE_DRIVE_ENV=/path/to/.env.drive

scripts/source_archive_auto_backfill.sh
```

macOS 로그인·재부팅 후 자동 시작하고 비정상 종료를 재기동하려면, 위 두 env
파일의 기본 위치가 repository root일 때 다음을 실행한다. job은 15분 간격의
안전망도 갖지만 정상 상태에서는 하나의 장기 실행 프로세스만 유지한다. 로컬
PID lock과 Drive writer lease가 중복 writer를 막는다.

```bash
scripts/install_launchd_source_archive.sh
launchctl print gui/$(id -u)/com.kjun.kreports-source-archive
tail -f logs/source-archive-auto.out.log logs/source-archive-auto.err.log
```

### 5. 여러 DART API key의 순차 전환

동시에 여러 worker를 실행하지 않는다. 기존 `DART_API_KEY`를 첫 키로 유지하고,
추가로 사용 권한이 있는 키만 owner-only 파일에 한 줄씩 넣는다. `export`, 변수명,
따옴표, 쉼표는 쓰지 않는다. 빈 줄과 `#` 주석은 무시한다.

```bash
export DART_API_KEYS_FILE="$HOME/.config/kreports/dart-api-keys"
install -m 600 /dev/null "$DART_API_KEYS_FILE"

# 편집기로 열어 한 줄에 키 하나를 입력한다.
# 첫 줄: 두 번째 키
# 둘째 줄: 세 번째 키
```

worker는 한 번에 키 하나만 요청에 주입한다. DART가
`dart_quota_failure`/status `020`을 반환하면 같은 shard와 checkpoint를 유지한 채
다음 키로 즉시 전환한다. 모든 키가 제한된 경우에만 15분을 기다린 뒤 새 probe
cycle을 시작한다. 한 키의 인증 실패는 그 키만 격리하고 다음 키로 넘어가며,
모든 키가 인증 실패한 경우에만 `AUTH_BLOCKED`로 중단한다.

키 파일은 매 quota/auth 전환 시 다시 읽기 때문에 실행 중 키를 추가해도 다음
전환부터 반영된다. local campaign의 `dart-api-key-rotation.json`에는 키 원문이
아니라 SHA-256 식별자, 제한/격리 상태, 전환 시각만 기록한다. 키 원문은 log,
outcome, Drive manifest, 후보 DB 또는 공개 MCP에 기록하지 않는다.

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
| `archived` | SHA-256 content-addressed Drive copy가 성공했다. 일반 백필은 즉시 readback을 생략할 수 있으며, 엄격 readback은 별도 audit/verify에서 수행한다. |
| `generically_parsed` | source hash에 묶인 일반 구조 JSON을 보전했다. |
| `structurally_complete` | 사업보고서와 primary 감사보고서 package가 모두 완전 구조 상태다. |
| `family_complete` / `family_reused` | 해당 report family의 모든 발견 asset을 검증·보전했거나, 같은 frozen checkpoint에서 재사용했다. 다음 재개에서 family DART 조회와 Drive 재보전을 건너뛴다. |
| `asset_reused` | 이전 실행에서 raw·generic parse·document manifest까지 검증된 asset을 재사용했다. 부분 family 재개에서도 이 asset은 DART/Drive 작업을 반복하지 않는다. |
| `requires_review` / `partial_source` | `requires_review`는 asset document manifest의 parser 검토 상태이고, 그 결과 company-year outcome은 `partial_source`다. 완료로 취급하지 않고 재시도 또는 검토한다. |
| `dart_budget_exhausted` | 이번 실행의 호출 상한에 도달했다. 다음 유한 budget 실행에서 재개한다. |
| `dart_transport_failure` / `dart_auth_failure` / `dart_quota_failure` | DART provider가 bounded stop을 반환했다. 현재 미완료 target만 non-terminal checkpoint로 남기고 이후 target은 시도하지 않는다. |
| `drive_quota_exhausted` | Drive 403 rate-limit 또는 429의 bounded retry가 소진됐다. 현재 shard를 즉시 중단하고 pending outbox/spool을 보존한 채 다음 실행에서 재개한다. |
| `drive_transport_failure` | Drive copy/readback timeout 또는 일시 command 실패다. 현재 checkpoint와 outbox를 보존하고 supervisor가 60초 뒤 재개한다. |
| `fetch_failed` / `asset_failed` | 원문 수신 또는 archive 검증이 실패했다. 오류와 raw evidence를 점검 후 재개한다. |
| `no_source_metadata` | frozen target에 source anchor metadata가 없다. missing disclosure로 추정하지 않는다. |

shard의 모든 company-year가 `structurally_complete`일 때만
`shard-XX/COMMITTED.json`이 생긴다. marker는 frozen target digest와 outcomes
checksum을 묶는다. partial shard에는 marker가 없으며, 다음 동일 shard 실행은
이미 complete인 company-year를 건너뛰고, family·asset 단위로 검증된 prefix를
재사용하면서 미완료 항목만 재개한다. DART budget/provider bounded stop이
발생하면 현재 target의 non-terminal stop outcome만 남기고 이후 target을
시도하지 않으므로, 미시도 target을 실패 또는 누락으로 기록하지 않는다. local
outcome cache와 marker의 결속이 완료된 shard에서 변조되면 verify/run은 실패해야
한다.

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
- Drive command attempts, rate-limit events, retry attempts, cooldown wait,
  `commands_by_operation`, `dedicated_client_configured`, pending event bundle
  count. 이 값은 rclone command metric이며 Google API query 수와 동일하지 않다;
- candidate build와 release promotion의 별도 승인·검증 결과.

이 기록은 raw filing 자체를 Git에 커밋하라는 뜻이 아니다. Git에는 코드와
안전한 manifest/reference만 두고, 대용량 원문은 검증된 외부 archive에 둔다.
