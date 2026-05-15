# Plan — fnlttSinglAcnt summary fallback (KOSDAQ 재무 갭 메우기)

작성: 2026-05-11
저자: K (with Claude Opus 4.7)
Tier: 2 (3개 이상 파일, 설계결정 ADR 가치)

## 문제

`fetch_log.task_type='financial'` 기준 전체 79,340건 중 `status='no_data'` 52,797건 (66%). 시장별 재무 커버리지:

| 시장 | 회사 수 | 재무 보유 | 커버리지 |
|---|---|---|---|
| KOSPI | 838 | 835 | 99.6% |
| KOSDAQ | 1,817 | 479 | **26.4%** |
| KONEX | 110 | 0 | 0% |

대표 케이스: 로보티즈 (KOSDAQ, 108490, induty 29299) — 감사인 데이터 2021~2025 정상 수집, 재무 데이터 0건.

## 가설 (Root Cause)

`kreports/collector/fetcher.py:131`은 **`fnlttSinglAcntAll.json` 단일 엔드포인트만** 호출. 이 엔드포인트는 XBRL 풀 계정 데이터를 반환하며 KOSPI 대형주 위주로 커버. KOSDAQ 소형주는 XBRL 의무 면제이거나 간소화 보고로 인해 status=013(데이터 없음) 반환 → fin_collector.py:77~79가 `no_data` 기록 후 종료.

DART는 동시에 `fnlttSinglAcnt.json` (주요계정 요약, ~13개 라인)을 제공하며, 이쪽은 K-IFRS 적용 모든 상장사를 커버. 폴백 체인이 없는 것이 핵심 결함.

## 스코프

In:
- 수집기에 `fnlttSinglAcnt` 폴백 추가 (CFS → OFS → 요약 CFS → 요약 OFS)
- `Financial` 테이블에 `source` 컬럼 (`'acntall'` | `'acnt'`) 추가하여 데이터 출처 추적
- 요약 엔드포인트 응답 파서 신설 (account_nm 기반)
- 테스트 (fixture + parser + collector 폴백)
- 마이그레이션 자동 적용

Out (이번 변경 아님):
- `financial_facts` 갭 보완 (요약 엔드포인트는 그래뉼러 계정 없음. financial_facts는 acntall 전용 유지)
- Beneish M-Score 등 요약-기반 계산 보강 (요약 엔드포인트는 영업CF 없음. flag 계산은 정상 동작하되 None 다수 허용)
- KONEX 0% 갭 (KONEX는 DART 정기보고서 의무 자체가 다름)
- 기존 no_data 재시도 자동화 — 백필은 사용자가 명시적으로 호출

## 데이터 출처 태깅 (Why source column)

`source='acntall'`: 기존 동작. financial_facts + Financial 양쪽 모두 채움.  
`source='acnt'`: 신규 폴백 경로. Financial만 채움. revenue/operating_profit/net_income/total_assets/total_debt/total_equity 6개 외 모든 Judge 컬럼은 부분 None 허용. financial_facts 없음.

다운스트림(`get_financial_snapshot`, `score_going_concern`, `compare_to_industry*`)은 `source`를 응답 메타에 포함해야 함 — 이는 후속 변경 (이번 plan 밖). 일단 컬럼만 추가하고 도구는 다음 PR에서 활용.

## 계약 (DART API 응답 shape)

`fnlttSinglAcnt.json` 정상 응답:
```json
{
  "status": "000",
  "list": [
    {
      "rcept_no": "20250318000123",
      "reprt_code": "11011",
      "bsns_year": "2024",
      "corp_code": "00946030",
      "sj_div": "BS|IS|CIS|CF|SCE",
      "sj_nm": "재무상태표|손익계산서|...",
      "account_nm": "자산총계",
      "thstrm_nm": "제 26 기",
      "thstrm_dt": "2024.12.31 현재",
      "thstrm_amount": "123,456,789",
      "frmtrm_nm": "...", "frmtrm_amount": "...",
      "bfefrmtrm_nm": "...", "bfefrmtrm_amount": "...",
      "ord": "9",
      "currency": "KRW"
    }
  ]
}
```

`fnlttSinglAcntAll`와 차이:
- `account_id` 없음 (XBRL element ID 매핑 불가)
- 행수 적음 (BS 9개 + IS/CIS 4개 ≒ 13행)
- 표준 account_nm: 유동자산/비유동자산/자산총계/유동부채/비유동부채/부채총계/자본금/이익잉여금/자본총계/매출액/영업이익/법인세차감전순이익/당기순이익

## 폴백 체인

```
[CFS, OFS] × [acntall, acnt] = 4단계
  ├─ acntall CFS → status=000 → financial_facts + Financial 저장, source='acntall'
  ├─ acntall OFS (CFS 실패 시) → 위와 동일 (fs_div='OFS')
  ├─ acnt CFS (acntall 양쪽 실패 시) → Financial만 저장, source='acnt'
  └─ acnt OFS (acnt CFS 실패 시) → 위와 동일 (fs_div='OFS')
실패 시 → no_data
```

## 변경 파일

| 파일 | 내용 |
|---|---|
| kreports/db/models.py | Financial.source 컬럼 추가 |
| kreports/db/engine.py | _migrate_existing_tables에 source 컬럼 등록 |
| kreports/collector/fetcher.py | fetch_financial_summary() 추가 |
| kreports/processor/fin_parser.py | parse_summary_response() 추가 |
| kreports/collector/fin_collector.py | 폴백 체인 + source 태깅 |
| tests/conftest.py | dart_response_acnt_summary fixture 추가 |
| tests/test_fin_parser.py | TestParseSummaryResponse 클래스 추가 |
| tests/test_fin_collector_fallback.py | 신규: 폴백 체인 mock 테스트 |

## 검증

1. 단위 테스트: pytest 전체 통과 + 신규 테스트 ≥6건 통과
2. 마이그레이션: 기존 DB에서 `source` 컬럼 추가되는지 확인
3. 회귀: 기존 acntall 경로 동작 변화 없음 (source='acntall' 자동 태깅만 추가)
4. 검증 (DART 쿼터 리셋 후, 사용자가 수행):
   - `python -m kreports.cli.main fin 108490 --year-from 2024 --year-to 2024`
   - DB에서 `SELECT * FROM financials WHERE corp_code='00946030'` 행 존재 + `source='acnt'` 확인

## 백필 (별도 단계)

본 plan은 코드 변경까지. 기존 `no_data` 행에 대한 backfill은 별도 명령으로 분리:
- 사용자가 쿼터 리셋 후 수동 호출
- `python -m kreports.cli.main fin --retry-no-data` 같은 옵션은 후속. 우선은 단일 종목 재실행으로 검증.

## 리스크

- DART 쿼터 (status=020): 본 변경은 호출 수를 늘림 (acntall 실패 시 acnt 추가 1~2회). KOSDAQ 1,338개 backfill 시 ~5,000회 추가 호출 예상. 일일 쿼터 (기본 10,000회) 내 1~2일 소요.
- 요약 엔드포인트 데이터 정확도: account_nm 기반 매칭은 normalize_account() 의존. 비표준 명칭 사용 기업에서 일부 필드 None 가능 — 회귀가 아닌 신규 path이므로 허용.

## 비 목표 (이번에 안 함)

- MCP 도구 응답 스키마 변경
- get_financial_snapshot 등이 source 사용하는 변경 — 후속 plan에서 처리
- 백필 자동화
