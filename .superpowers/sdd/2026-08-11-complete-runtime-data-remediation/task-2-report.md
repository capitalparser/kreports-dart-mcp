# Task 2 — audit-procedure runtime data remediation

## Status

**INCOMPLETE — parser-corrected cache/external-source recovery completed; a compliant historical-membership quality-gap DART runner is still required before live source recovery.** No prior database candidate was replaced or deleted. No secret value is present in this report.

## Code and RED/GREEN evidence

- Base: `77510e9`; implementation commits: `e8d2c138507f4c53a29054768e941909e5f4861d` (`fix: recover hash-verified GCS KAM raw documents`) and `12dd601edd867012099b5ffad42cd3f3bf4d69c2` (`fix: recover normalized external KAM bodies`).
- RED: `uv run pytest tests/test_kam_parser.py::test_rebuild_recovers_hash_verified_gcs_raw -q` failed before the change: a verified `gs://` source was classified as `missing`.
- GREEN: the same test passed after the change. Full focused suite: `uv run pytest tests/test_kam_parser.py -q` => **211 passed**, 59 pre-existing SQLAlchemy UTC deprecation warnings.
- Change: `_recover_kam_items()` now invokes `RawDocumentStore.read(storage_uri, expected_hash=doc_hash)` for `gs://` as it already does for file storage. `RawDocumentStore.read()` downloads, decompresses, and SHA-1 verifies; missing GCS dependency, ADC/auth, object, decompression, or hash failures remain explicit `source_documents.raw_body:read_error:<Exception>` limitations and do not claim `full_body`.

## Candidate and safety preflight

- Verified source, not mutated: `/private/tmp/kreports-investor-r5.KVjjJj/kreports-rehearsal-quality-refresh.db`.
- Recoverable APFS clone used for every write: `/private/tmp/kreports-investor-r5.KVjjJj/kreports-rehearsal-audit-procedure.db`.
- Clone/source initially both 3,516,485,632 bytes; source inode/mtime remained unchanged during candidate work.
- `uv sync --extra gcs` installed the optional GCS client in the local environment. No lockfile change was made.
- Redacted config preflight passed with `KREPORTS_RUNTIME_MODE=collector`, `KREPORTS_ENABLE_RAW_BACKFILL=1`, `RAW_STORAGE_BACKEND=gcs`, `RAW_STORAGE_BUCKET=kreports-raw-documents-gen-lang-client-0171998581`, `RAW_STORAGE_PREFIX=dart/raw`, `RAW_STORAGE_KEEP_INLINE=false`, and configured DART key true.
- One `gs://` candidate download completed with `doc_hash` verification (`body_chars=225040`); ADC was usable. `scripts/raw_backfill_guard.sh` and the 10 GiB disk preflight passed. `scripts/probe_dart_api.py` reported `DART API available (20260810)`.

## Diagnosis and cache recovery results

- Historical verified 2025 KOSPI/KOSDAQ denominator: **2,674** (`company_year_listing_memberships`, not the current company master).
- Candidate had 4,357 KAM sections; 4,354 short (99.9%). Before recovery: KAM items 38 `full_body`, 4,295 `summary_only`, 170 `missing`; procedures 44.
- Candidate contained **71** distinct 2025 externalized `gs://` audit-report source receipts across **27** companies (the supplied earlier 253/99 snapshot does not match this candidate and was not treated as current fact).
- Initial 71/71 bounded receipt batches exposed a parser defect, not a storage failure: a 225,040-character hash-verified GCS XML contained both KAM and procedure markers, but the section extractor truncated its KAM body to 33 characters because generic `재무제표` attachment trimming matched ordinary KAM prose. The normalized KAM boundary now retains the body until the actual auditor-responsibility heading, and a conservative collapsed parser is attempted only after normal parsing fails.
- After the corrected 71/71 rerun: KAM items changed from **38 to 65 full_body** (+27) and from 4,295 to **4,268 summary_only** (-27); missing remains 170. The candidate then measured 98 procedure items / 67 procedure receipts. The 27 affected company-year quality rows were rebuilt (`companies_evaluated=27`, `rows_written=27`).

## Canonical measurements and gates

- `audit-procedure-evidence-map --year 2025 --json` after corrected recovery: fail; 4,357 KAM sections, 4,354 short, 65 full-body KAM items / 64 receipts, 43 full-body receipts with procedures, 98 procedure items / 67 procedure receipts, **procedure coverage 67.2%** (required gap: `procedure_coverage_below_80`). Quality gaps remain 4,268 summary-only and 170 missing.
- `quality-release-gate --profile public_runtime --json`: exit 0 / pass, with audit-procedure degraded.
- `quality-release-gate --profile auditor_full --json`: exit 1 / fail; includes `audit_procedure_coverage` (also accounting-policy and materiality blockers).
- Actual `search_audit_procedures(company='005930'|'000660', year=2025, limit=3)` calls returned successfully but zero result companies for those samples; no fabricated procedure answer was produced.

## Remaining cohort and safe resume point

The remaining inadequate cohort is the verified 2,674-company 2025 KOSPI/KOSDAQ population minus the companies with full-body KAM evidence; the current candidate still has 4,268 summary-only and 170 missing KAM items. A full `rebuild_kam_items(year=2025)` run was stopped after it attempted to materialize the entire 4,357-target corpus at once; candidate WAL was checkpointed. It must be resumed through a **persisted, historical-membership-scoped quality-gap selector**, not current `companies.market` and not existing `--missing-only` (which skips truncated KAM rows).

Do not run live DART collection until that selector records a bounded `backfill_runs` lease/checkpoint and selects every verified inadequate receipt deterministically. The existing `collect-audit-report-sections --missing-only` SQL uses current company-master membership and treats any stored section as complete, so it is not safe for this cohort.

Safe environment (DART value is deliberately loaded only in-process and never printed):

```bash
set -a
source /Users/kjun/vault/01_Projects/kreports_dart_mcp/.env
set +a
export DB_URL='sqlite:////private/tmp/kreports-investor-r5.KVjjJj/kreports-rehearsal-audit-procedure.db'
export KREPORTS_RUNTIME_MODE=collector KREPORTS_ENABLE_RAW_BACKFILL=1
export RAW_STORAGE_BACKEND=gcs RAW_STORAGE_BUCKET=kreports-raw-documents-gen-lang-client-0171998581
export RAW_STORAGE_PREFIX=dart/raw RAW_STORAGE_KEEP_INLINE=false
```

After the selector exists, resume with one bounded deterministic batch, persist its `backfill_runs` success/failure/API/storage counts and next receipt cursor, then repeat through the cohort. Only after the full candidate quality ledger is rebuilt should a release manifest be rebuilt and verified.

## 2026-08-11 — historical-membership procedure-recovery selector (append-only update)

### Implementation and test evidence

- Selector/CLI implementation commits, in order: `7f19aa0` (new recovery selector), `0abbbd0` (checkpoint counters), `5764e33` (attachment receipt rebuilding), `744799e` (failed-run prefix resume), `290a751` (canonical own-company root and derive-before-cursor), and `1842742` (standalone audit-report roots).
- `collect-audit-procedure-recovery --year 2025 --market KOSPI|KOSDAQ --limit N` is collector/raw-backfill gated and stores an exact-scope `BackfillRun` lease. Scope is `company_year_listing_memberships.bsns_year`, `market`, and `status='verified'`; current `companies.market` is only display metadata.
- A target is inadequate unless the canonical root or one of its attachment receipts has both a full-body `audit_report` KAM and a nonblank `audit_report` procedure. The derived rebuild (KAM, procedures, then company-year quality) completes before its receipt cursor is checkpointed. Failed fetches and failed derived rebuilding therefore retry the same root.
- The canonical selector chooses one latest own-company target-year root per verified company, rejects subsidiary/prior-business-year labels, and treats `감사보고서제출` plus target-year standalone `감사보고서 (YYYY.MM)`/`연결감사보고서 (YYYY.MM)` labels as candidates. `SELECTOR_VERSION=3` deliberately makes the previous v2 cursor ineligible after this cohort change.
- RED: `uv run pytest tests/test_audit_procedure_recovery.py::test_selector_includes_target_year_standalone_audit_report_roots -q` failed with no selected root for the standalone labels. GREEN: the same test passed after the label rule. Focused suite: `uv run pytest tests/test_audit_procedure_recovery.py tests/test_audit_report_sections.py tests/test_backfill_runs.py -q` => **90 passed** (223 SQLAlchemy deprecation warnings). `uv run ruff check kreports/collector/audit_procedure_recovery.py tests/test_audit_procedure_recovery.py` => pass.

### Read-only canonical cohort measurement

- On the recoverable candidate DB, selector v3 measured KOSPI **825 canonical / 805 inadequate** and KOSDAQ **1,776 canonical / 1,769 inadequate**: **2,601** canonical roots and **2,574** inadequate roots total. This reconciles to the verified 2,674-company population with 73 verified companies that have no accepted own-company audit root; it is not a current-master denominator.
- The 1,776 KOSDAQ canonical total includes corp `01207761` (프로브잇), whose accepted latest standalone root is `20260609000510` (`연결감사보고서 (2025.12)`).

### Bounded live v3 evidence

- Safety preflight recorded above remained in force: candidate-only APFS clone, 10 GiB disk guard, collector/raw-backfill mode, GCS external raw storage, and successful DART availability probe. No secret was printed.
- Live invocation: `collect-audit-procedure-recovery --year 2025 --market KOSPI --limit 2` against `/private/tmp/kreports-investor-r5.KVjjJj/kreports-rehearsal-audit-procedure.db`.
- Backfill run **407** (selector v3) succeeded: processed/success/failed = **2/2/0**, DART receipt fetches = **2**, sections = **28**, storage backend = `gcs`, `exhausted=false`, cursor start = null, next cursor = `(00101628, 20260309801123)`. It recovered canonical roots `20260318800754` (경농) and `20260309801123` (경방). This proves a durable first v3 prefix; the second-v3-batch behavior is covered by the focused lease/resume tests and should be independently reviewed before continuing the full 2,574-root route.
- Historical v1 runs 402--405 are retained as diagnostics only, not canonical completion evidence: run 402 had the then-known cumulative-counter bug (reported 6 for a 3-root batch), and run 405 included four subsidiary/off-scope roots before canonicalization. Runs 403 (3/3) and 404 (10/10) have corrected batch counters, but remain v1 scope.

### Current gate and continuation

- Post-smoke `audit-procedure-evidence-map --year 2025 --json` is still **fail**: 4,420 KAM sections; 4,357 short; 106 full-body KAM items / 101 receipts; 161 procedure items / 102 receipts; 78 full-body KAM receipts with procedures; procedure coverage **77.2%**; 4,296 summary-only and 172 missing. Required remaining gate is `procedure_coverage_below_80`.
- Continue from run 407's exact v3 KOSPI cursor in bounded batches; start a separate v3 KOSDAQ lease at its null cursor. Do not reuse v1/v2 cursors or claim release readiness until the full inadequate route, quality rebuild, evidence map, and release-gate rerun are complete.

## 2026-08-11 — independent-review fix round: v4 business-report fallback and mutation guards

### Findings addressed

- Commit `1e2091d` (`fix: harden audit procedure recovery v4`) advances `SELECTOR_VERSION` to **4**. It adds target-business-year `사업보고서 (YYYY.MM)` roots as a fallback for non-calendar fiscal-year companies. A direct/standalone audit root always outranks a business-report fallback for the same historical company; among roots of the same class, the latest `(disc_date, rcept_no)` remains authoritative. Thus an ordinary later annual filing cannot replace a direct audit receipt.
- Business-report fallback is intentionally not gated on a pre-indexed audit attachment. `collect_report_sections_for_disclosure()` can retrieve the business ZIP/viewer and discover its audit attachments, so requiring a prior `source_documents` attachment would permanently omit recoverable roots. Target-year parsing rejects adjacent-year annual reports.
- The recovery CLI now permits only external **GCS** raw persistence with a nonempty bucket, caps `--limit` at **25**, and runs the existing Python 10 GiB `assert_free_space` guard against the active file-backed SQLite database's parent directory before it creates a lease or mutates data. It rejects a non-SQLite or in-memory target rather than checking an unrelated cwd filesystem.
- A derived rebuild exception now records the fetched receipt as attempted/error, records API receipt fetch count and bounded receipt-level error in the checkpoint, leaves `next_cursor` before that receipt, and returns a failed batch for the durable lease failure path. Retrying therefore refetches the failed root rather than silently skipping it.

### RED/GREEN and reconciliation evidence

- RED cases were observed for: omitted non-calendar business root, later-business-root displacement of direct audit evidence, attachment-discovery fallback, non-GCS storage acceptance, limit `26` acceptance, cwd-based disk guard, and derived rebuild failure without checkpoint counters. GREEN: `uv run pytest tests/test_audit_procedure_recovery.py -q` => **18 passed**; `uv run ruff check kreports/runtime.py kreports/collector/audit_procedure_recovery.py kreports/cli/main.py tests/test_audit_procedure_recovery.py` => pass.
- Broader focused suite after the change: `uv run pytest tests/test_audit_procedure_recovery.py tests/test_audit_report_sections.py tests/test_backfill_runs.py tests/test_readonly_mcp.py -q` => **107 passed**.
- Read-only v4 candidate reconciliation: KOSPI **841 canonical / 819 inadequate**, KOSDAQ **1,818 canonical / 1,811 inadequate**, total **2,659 canonical / 2,630 inadequate**. Compared with v3's 2,601 direct/standalone roots, v4 adds **58** valid target-year business-report fallbacks (16 KOSPI, 42 KOSDAQ); the verified 2,674-company denominator leaves **15** genuine no-root companies. Corp `00378363` / root `20250618000208` is included as required.

### One bounded live v4 smoke

- After all tests passed, exactly one candidate-only live invocation ran with collector mode, explicit raw-backfill opt-in, GCS bucket, and the actual candidate DB filesystem guard: `collect-audit-procedure-recovery --year 2025 --market KOSPI --limit 2`.
- Backfill run **408** succeeded: selector v4, processed/success/failed **2/2/0**, API receipt fetches **2**, sections **28**, storage `gcs`, null start cursor, next cursor `(00101628, 20260309801123)`, `exhausted=false`. The recovered roots were 경농 `20260318800754` and 경방 `20260309801123`.
- The full v4 inadequate cohort remains intentionally unrun pending independent review approval. Resume only with v4's exact KOSPI checkpoint (and a separate null-cursor v4 KOSDAQ lease); no v1--v3 cursor is valid for this cohort.

## 2026-08-11 — bounded parser QA: collapsed `다뤄진 방법` attachment repair

- Candidate-only diagnosis for attachment `20260323800743_11162971` (corp `00109514`) found one hash-verified GCS `audit_report` source (40-character document hash; no inline raw body). Its stored KAM section was 775 characters. Both ordinary and collapsed parsing failed closed as `incomplete_kam_structure`; the source read itself was not the failure.
- The sole missing grammar was the explicit common response heading `핵심감사사항이 감사에서 다뤄진 방법`. The parser recognized only the longer `다루어진` spelling, so it saw the reason heading but not a response frame even though the section contained a title, reason, and hyphenated procedure list.
- Commit `f13bf3f` adds only the compact `다뤄진` response-heading variants to the normal and collapsed heading registries. The collapsed splitter can consequently separate an inline heading from its following introduction; no title, reason, or procedure text is inferred without the explicit existing frame.
- RED: new synthetic test `test_parse_collapsed_audit_report_accepts_inline_dwaeojin_response_heading` failed as `error` before the change. GREEN: it produces one `full_body` KAM and six explicit synthetic procedure steps. Focused validation: `uv run pytest tests/test_kam_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_procedure_recovery.py -q` => **235 passed**; focused Ruff => pass.
- After tests, exactly one existing-data repair ran against the attachment itself: `rebuild_kam_items_for_receipts(year=2025, rcept_nos=['20260323800743_11162971'])`, then exact corp/year quality rebuild. This operation used existing GCS/report-section evidence only and made **no DART API call**. KAM transitioned **error / 0 chars / none / 0 procedures** to **full_body / 956 chars / source_documents.raw_body / 3 persisted procedures**. One quality row was rebuilt; it now records `kam_status=full_body` and `audit_procedure_status=available`.

## 2026-08-11 — bounded parser QA: response boundary and explicit bullet procedures

- Commit `1ee0014` (`fix: retain explicit audit procedure bullets`) adds two conservative parser boundaries: full `재무제표에 대한 경영진과 지배기구의 책임` (and consolidated equivalent) headings terminate a KAM response, and noun-ending procedure clauses are accepted only when their source line has an explicit bullet marker. Generic responsibility boilerplate remains rejected; non-bulleted noun lists remain rejected.
- A generic response lead-in matching `다음을 포함한 감사절차를 수행` is explicitly excluded even if it precedes bullets. This prevents the introduction itself becoming a seventh procedure item while retaining the following performed steps.
- Method normalization adds only the observed control-test phrasing `설계 및 운영의 효과성을 테스트`, producing `controls_test`; the remaining exact attachment methods are `inspection`, `other`, `analytical_procedure`, `inspection`, and `sampling`.
- RED/GREEN evidence: the management/governance section initially leaked into `audit_response_text`; bullet noun endings were initially absent; the generic lead-in initially indexed as a procedure; and the live-shaped control phrasing initially classified as `other`. Final focused regression command `uv run pytest tests/test_kam_parser.py tests/test_audit_procedure_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_procedure_recovery.py -q` passed **277** tests. Ruff and `git diff --check` passed.
- Candidate-only repair remained bounded to receipt `20260323800743_11162971`, corp `00109514`, year 2025. First, `rebuild_kam_items_for_receipts` read the existing GCS raw object only and rebuilt quality for that company/year. It changed the response from **956** to **634** characters, removed the management/governance heading, and grew procedures from **3** to **6**. Then `index_audit_procedures_from_sections(year=2025, rcept_nos=[...])` reconciled one persisted KAM identity with **6 rows written**, **0 failures**, and methods `controls_test`, `inspection`, `other`, `analytical_procedure`, `inspection`, `sampling`.
- No DART collection function or DART API call was used in either bounded operation; the first was existing-GCS-only and the second used persisted `kam_items.full_body` only. Company-year quality remains `kam_status=full_body` and `audit_procedure_status=available`.

## 2026-08-11 — full-body/no-procedure forensic pass: inline list recovery

- Read-only candidate analysis began with **182** full-body 2025 audit KAM identities and **45** identities without a persisted procedure. The prior parser could already recover 9 from the now-supported hyphen bullets, rejected 3 responsibility/boilerplate cases, and left 33 action-bearing responses unparsed.
- The actual missing pattern was not absent evidence: explicit procedure lists had been flattened inline using Korean list marker `ㆍ`, circled numerals (`①` etc.), or a lead-in immediately followed by `-`. The existing splitter treated each flattened response as prose, so noun-ending steps such as review/test/confirmation were neither isolated nor given bullet provenance. Two residual groups use collapsed prose without a safely separable list boundary and remain fail-closed.
- Commit `dff6eff` (`fix: parse inline audit procedure lists`) splits only explicit inline list boundaries: `ㆍ` except lexical `전ㆍ후`, circled numerals, sentence-to-list boundaries, and whitespace-delimited list bullets. It strips those markers for stored text and carries their provenance into the existing noun-ending acceptance rule. A bare lexical middle dot requires boundary/spacing and is not treated as a list marker.
- RED/GREEN tests cover inline `ㆍ`, circled-number, and immediate-lead-in hyphen lists; they also prove `전ㆍ후`, `매출·매입`, and auditor responsibility prose are not reclassified as list procedures. Focused validation: `uv run pytest tests/test_kam_parser.py tests/test_audit_procedure_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_procedure_recovery.py -q` => **283 passed**; Ruff and `git diff --check` passed.
- After commit, a second read-only parser simulation found **42/45** missing identities recoverable; the remaining three are `20251120000578_20251120000578_00760_xml`, `20260331904714_11216278`, and `20260331904714_11216279`. Their parser output remains zero, so no low-confidence procedure was persisted.
- Candidate mutation was exact and cache-only: the runner dynamically selected those **42** recoverable identities (**40** receipt keys, **26** companies), called `index_audit_procedures_from_sections(year=2025, rcept_nos=...)` against persisted `kam_items.full_body`, then rebuilt company-year quality for only those 26 companies. Reindex result: **44** reconciled identities, **217** rows written, **0** failures/errors. It did not read GCS and made **no DART API call**.
- Direct identity measurement changed full-body procedure support from **137/182** to **179/182** (98.4%) and procedure rows from 256 to 471 at the scoped snapshot. The post-run `audit-procedure-evidence-map --year 2025 --json` independently passed with **179/182** full-body KAM identities covered, `procedure_coverage=98.2%`, and no required gaps. The difference is receipt-based coverage/command accounting rather than an inferred claim of full-cohort readiness.

### Inline-marker boundary hardening (review follow-up)

- Review correctly identified that the first `ㆍ` rule was too broad: excluding only `전ㆍ후` could still split lexical no-space middle dots such as `내ㆍ외부`, `현ㆍ전기`, and `손익ㆍ공정가치`.
- Commit `dbdf161` (`fix: restrict inline procedure bullet boundaries`) now recognizes `ㆍ` only at response start, after whitespace/sentence punctuation, or when followed by whitespace. Circled numbers and the already constrained sentence/whitespace list boundaries remain unchanged. This retains genuine compact list markers after a list lead-in while not treating arbitrary Korean compounds as bullets.
- RED: the three lexical examples above each lost their left-side token under the over-broad splitter. GREEN: all preserve their complete procedure text. The inline-list positives and responsibility-prose/ordinary-middle-dot negatives remain green. Focused parser/indexer/recovery validation: **286 passed**; Ruff and `git diff --check` passed.
- The original forensic scope was then reconstructed explicitly: **43** unique receipt keys covering the original **42** recoverable plus **3** residual full-body/no-procedure identities. Exact persisted-cache-only reindex reconciled **47** identities, wrote **215** rows, and failed **0**; the related **28** company/year quality rows were rebuilt. No GCS read, DART collection function, or DART API call occurred.
- Before/after this hardening reindex, full-body support remained **179/182** and the direct audit-procedure row count remained 494, confirming that the tighter boundary did not discard the recovered evidence. Final `audit-procedure-evidence-map --year 2025 --json` passed with **179/182** full-body KAM identities supported, `procedure_coverage=98.2%`, no required gaps, and command-reported 533 procedure items.

### Remaining-three recovery and responsibility-boundary re-review fix

- Independent read-only review corrected the earlier residual classification: all three contained explicit procedures. DH오토리드 receipts `20260331904714_11216278` and `20260331904714_11216279` use a standalone `ㆍ` line followed immediately by noun-ending procedure text; `20251120000578_20251120000578_00760_xml` has a recognized audit-procedure lead-in followed by glued `-` list separators. They were parser provenance gaps, not insufficient evidence.
- Commit `d84b95c` (`fix: retain fragmented audit procedure lists`) carries a standalone marker's provenance only to the immediately following non-empty clause and splits a glued `-` list only after the explicit audit-procedure lead-in. Generic minus prose remains unsplit.
- Review also found that fragmenting a responsibility sentence could bypass fail-closed filtering. The final parser therefore rejects an entire response if it contains any existing responsibility boilerplate marker, before any artificial inline split. RED cases include responsibility followed by one and multiple `-` bullets, `ㆍ`, and a circled numeral; all remain excluded. This intentionally conservative source-level boundary prevents later fragments from reviving a rejected response.
- RED/GREEN validation for standalone marker, glued list, generic-minus, single/multiple responsibility fragments, lexical middle-dot, and prior parser cases passed: `uv run pytest tests/test_kam_parser.py tests/test_audit_procedure_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_procedure_recovery.py -q` => **294 passed**; Ruff and `git diff --check` passed.
- Exactly the three reviewed receipts were then reconciled from persisted `kam_items.full_body`: **3** identities, **12** procedure rows, **0** failures/errors, and exactly **2** company/year quality rows rebuilt. Resulting rows per receipt were 3, 6, and 3; no GCS read, DART collection function, or DART API call occurred.
- Post-repair direct full-body support is **182/182**. `audit-procedure-evidence-map --year 2025 --json` now passes with `procedure_coverage=100.0%`, no required gaps, and 545 command-reported procedure items. Independent re-review remains required before Task 2 closure.

### Run 414 follow-up: inline numbered procedures and omitted-reason risk title

- Candidate read-only inspection found three full-body/no-procedure identities with explicit inline numeric lists: 대한해운 receipts `20260320801358_11158385` and `20260320801358_11158386`, plus 대현 `20260319801185_11149999`. Each uses `1) ... 2) ...` after the explicit `주요 감사절차는 다음과 같습니다` lead-in; the prior parser normalized numeric labels but did not split or preserve their list provenance.
- Commit `60fc8a9` (`fix: recover numbered and risk-only audit procedures`) recognizes numeric markers only when that explicit audit-procedure list lead-in is present. A non-audit numeric prose fixture remains unparsed, preventing numeric labels elsewhere from becoming procedure provenance.
- The same inspection found 디와이 receipt `20260317800947_11133285` as an error despite an explicit KAM introduction, separate title `DY AUTO INDIA Pvt.의 현금창출단위 손상평가`, long reason narrative, explicit response heading, and six bullet procedures. Root cause: omitted-reason recovery required title evidence score 3; this valid foreign-entity title scores 1 (`손상`) although it ends in the strong risk-title phrase `손상평가`.
- The parser now admits only a bounded omitted-reason title alternative: score at least 1 plus one of `손상평가`, `손상검사`, `공정가치측정`, `매수가격배분`, or `회수가능성`, while retaining the existing explicit KAM/response and long reason checks. RED synthetic parsing failed as `incomplete_kam_structure`; GREEN yields the exact risk title and its explicit steps.
- Validation: targeted RED/GREEN tests followed by `uv run pytest tests/test_kam_parser.py tests/test_audit_procedure_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_procedure_recovery.py -q` => **297 passed**; Ruff and `git diff --check` passed.
- Exact candidate repair used persisted evidence only. The three numeric receipts were indexed from `kam_items.full_body`; DY was parsed from its stored `report_sections.kam` body and persisted with `source_basis=report_sections.structured_body`, then all four receipts were exact-indexed. Result: **4** receipts / **3** companies; DY parse complete with one persisted KAM; reindex **4/4** identities, **19** procedure rows, **0** failures/errors; quality rebuilt for three companies. No GCS read, DART collection, or DART API call occurred.
- Target outcomes: 대한해운 4 and 4 procedures, 대현 5, and DY error/0 to full-body length 877 with 6 procedures. The contemporaneous candidate denominator was 240 full-body / 237 supported before this exact repair and 241/241 after it. Final `audit-procedure-evidence-map --year 2025 --json` passed at `procedure_coverage=100.0%`, no required gaps, and 884 command-reported procedure items.

### Positional numeric-list provenance re-review fix

- Independent review found a medium-severity scope error in `60fc8a9`: the presence of an audit-procedure lead-in anywhere in a response enabled numeric splitting before that lead-in. The exact regression has numeric prose (`1) 계약서 검토 2) 외부조회 확인`) before a later `주요 감사절차는 다음과 같습니다` sentence and must retain only the later real procedure.
- Commit `ccd4d14` (`fix: scope numeric procedure lists to lead-in`) separates base boundaries from supplementary numeric/glued-list boundaries, records the lead-in match end, and adds the latter boundaries only at or after that position. Thus prior numeric prose cannot gain list provenance merely because a later audit lead-in exists.
- RED returned the pre-lead `계약서 검토` as a procedure. GREEN retains only `자산을 검토하였습니다.`; existing numeric-list positive and no-lead negative remain covered. Focused parser/indexer/recovery suite: **298 passed**; Ruff and `git diff --check` passed.
- Collector run415 completed before candidate mutation. Read-only post-run scan found **266** full-body KAM identities, **0** error KAMs, **0** full-body identities without procedures, and **0** stored responses with a numeric/glued list marker before a lead-in. No broad reparse was therefore needed.
- Exact persisted-cache reindex of the three numeric-list receipts only (대한해운 two, 대현 one) reconciled **3** identities, wrote **13** rows, failed **0**, and rebuilt quality for the two affected companies. Their method/row shapes remained 4, 4, and 5 procedures. No GCS read, DART collection function, or DART API call occurred.
- Final `audit-procedure-evidence-map --year 2025 --json` passed with **266/266** full-body identities supported, `procedure_coverage=100.0%`, no required gaps, and 1,020 command-reported procedure items. Independent re-review remains required before Task 2 closure.

### Run 417 follow-up: same-line KAM heading swallowed by emphasis

- Candidate-only diagnosis of `20260319801468_11151139` (삼익THK) found a persisted 95,391-character `report_sections.full_text` and no persisted `kam` section. The prior `kam_items` placeholder was `error / none / 0` with no procedures. The full text itself contains a complete KAM and explicit procedures; no DART collection was needed.
- Root cause: `_find_heading_candidate()` rejected a valid same-line heading because the preceding emphasis sentence and the KAM introduction were flattened together. The observed boundary is sentence-terminal prose followed by `핵심감사사항 핵심감사사항은 우리의 전문가적 판단...`. The parser had additionally not registered the observed response heading `핵심감사사항에 대응하기 위한 우리의 감사절차는 다음을 포함하고 있습니다.`
- The section extractor now accepts only that doubled KAM-introduction signature immediately after a sentence terminal, while continuing to reject prose forms such as `핵심감사사항으로 결정...` and child response headings. When a historical receipt has no structured KAM section, its persisted `full_text` is conservatively re-extracted and parsed before any raw-store read; only a complete normal/collapsed KAM is returned, otherwise existing source/evidence `missing` and `error` behavior is unchanged.
- RED/GREEN tests cover the actual same-line emphasis-to-KAM transition, explicit procedure extraction, response-heading variant, no external raw read, and the existing prose rejection. Focused validation: `uv run pytest tests/test_audit_report_sections.py tests/test_kam_parser.py tests/test_audit_procedure_indexer.py -q` => **259 passed**; focused Ruff passed.
- Exact candidate rebuild used `rebuild_kam_items_for_receipts(year=2025, rcept_nos=['20260319801468_11151139'])` with `DART_API_KEY` and Google credentials unset and `RawDocumentStore.read` replaced by an assertion guard. The guard recorded **0 raw-store reads**. Result: one `full_body` KAM, source basis `report_sections.full_text`, body length **565**, and **3** persisted procedures (inspection, other, other). No DART API or GCS call occurred.
- Post-repair readonly `audit-procedure-evidence-map --year 2025 --json` passed with **319/319** full-body KAM identities supported, `procedure_coverage=100.0%`, and no required gaps.

### Run 420 follow-up: intro-tail title with omitted reason heading

- Candidate-only diagnosis of `20260312801564_11115897` (아남전자) found a locally cached 642-character `report_sections.kam` body. It contains an explicit KAM intro, title `오디오제품매출 수익인식 기간귀속의 적정성` concatenated after `별도의 의견을 제공하지는 않습니다.`, a 281-character inline reason beginning `회사는`, the explicit response heading, and three hyphen procedures. Both ordinary and collapsed parsing previously returned `incomplete_kam_structure`; the persisted placeholder was `error / none / 0`.
- The omitted-reason recovery now recognizes the bounded title boundary only after a known KAM-intro ending and before an inline `회사(는)` reason. It requires a title of at most 80 characters, no sentence terminal, and existing title-evidence score at least 3; the reason still requires at least 50 compact characters and the existing single explicit response-frame checks. A body that begins its reason directly with `회사는` remains `error` and cannot invent a title.
- RED/GREEN tests cover this title/reason boundary, three explicit procedures, and the direct-prose negative. Focused validation: `uv run pytest tests/test_kam_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_report_sections.py -q` => **261 passed**; focused Ruff and `git diff --check` passed.
- Exact persisted-section reparse used the cached `report_sections.kam` only, invoked `parse_collapsed_kam_items`, and persisted through `_persist_rebuilt_kam_items` with source basis `report_sections.structured_body`. `DART_API_KEY` and Google credentials were unset; an assertion guard recorded **0** `RawDocumentStore.read` calls. The receipt changed to one `full_body` KAM (body length **519**) and **3** procedures (inspection, other, sampling). No DART API or GCS call occurred.
- Post-repair readonly `audit-procedure-evidence-map --year 2025 --json` passed with **401/401** full-body KAM identities supported, `procedure_coverage=100.0%`, and no required gaps.

### Run 420 independent-review follow-up: choose the validated intro ending

- Independent review found that the initial intro-tail recovery used the latest matching intro ending. A later reason sentence can itself end `핵심감사사항으로 결정하였습니다.` or `핵심감사사항으로 식별하였습니다.`, which must not displace the actual pre-title `별도의 의견을 제공하지는 않습니다.` ending.
- `_compact_span_ends()` now exposes every whitespace-insensitive occurrence. Omitted-reason recovery evaluates those endings in source order and chooses only the first whose immediate tail contains a title no longer than 80 characters, title-evidence score at least 3, no sentence terminal, and then inline `회사(는)` reason. The existing sufficient-reason and explicit-response checks remain unchanged.
- RED: both later-ending variants produced `error` with the old latest-ending branch; direct `회사(는)` prose remained `error`. GREEN: both recover the exact audio-revenue title and three procedures, while the prose negative remains fail-closed. Focused validation: `uv run pytest tests/test_kam_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_report_sections.py -q` => **262 passed**; Ruff and `git diff --check` passed.
- Per active collector coordination, this correction is code/test-only until the current batch completes. No candidate DB reparse, raw-store access, DART API, or GCS call was performed in this follow-up.

### Run 422 follow-up: fullwidth procedure bullets and numbered multiple matters

- 이화산업 cached KAM identities `37727` and `37728` were already `full_body`, but each response used four U+FF0D fullwidth hyphens (`－`) as inline list markers and had zero persisted procedures. The procedure splitter now recognizes U+FF0D only in the same explicit-bullet positions as ASCII hyphen; `매출－매입` prose remains a single non-list clause.
- 이수화학 receipt `20260319801149_11150068` had one persisted 1,034-character KAM section with two explicit Arabic-marked matters and two explicit response headings, but no reason headings. The new multiple-matter path is separate from the unmarked fallback: it requires consecutive `(1)..(N)` markers, an equal number of response frames, a bounded clear-evidence title for every marker, and sufficient reason text per matter. It recovered `종속기업 투자주식에 대한 손상 평가` and `매출의 수익인식 기간귀속`; unmarked prose retains the single-response fail-closed rules.
- RED/GREEN coverage includes U+FF0D inline bullet recovery and lexical negative, two numbered omitted-reason matters with one inline and one next-line reason, and the existing unmarked negatives. Focused validation: `uv run pytest tests/test_audit_procedure_parser.py tests/test_kam_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_report_sections.py -q` => **323 passed**; Ruff and `git diff --check` passed.
- Exact candidate mutation was persisted-only and assertion-guarded: no `RawDocumentStore.read`, DART, or GCS call occurred. Reconciled 이화산업 receipts `20260318800640_11136783` and `20260318800640_11136784` from cached `kam_items.full_body`, each writing **4** procedures. Reparsed 이수화학 from cached `report_sections.kam`, replacing `error / none / 0` with two `full_body / report_sections.structured_body` KAM rows: lengths **569** and **342**, with **5** and **3** procedures.
- Post-repair readonly `audit-procedure-evidence-map --year 2025 --json` passed with **457/457** full-body KAM identities supported, `procedure_coverage=100.0%`, and no required gaps.

### Run 422 independent-review follow-up: fullwidth hyphen lexical boundary

- Review found that adding U+FF0D to the post-lead-in glued-list rule also split lexical `매출－매입` and dropped the left token when an otherwise valid audit-procedure lead-in preceded it.
- The supplementary rule now preserves ASCII `-` glued-list handling, but accepts U+FF0D there only when the marker is followed by whitespace. Existing sentence/line explicit-bullet boundaries remain available for U+FF0D lists. Thus `...다음과 같습니다.－ 계약서 검토 － 외부조회 확인` remains a list, while `...다음과 같습니다. 매출－매입 거래 차이를 검토하였습니다.` remains one procedure clause.
- RED reproduced the exact prefix loss; GREEN covers the lead-in lexical negative, pre-lead lexical negative, U+FF0D explicit list positive, and ASCII glued-list positive. Focused validation: `uv run pytest tests/test_audit_procedure_parser.py tests/test_kam_parser.py tests/test_audit_procedure_indexer.py tests/test_audit_report_sections.py -q` => **324 passed**; Ruff and `git diff --check` passed.
- Per active collector coordination, this is code/test-only. No candidate reparse, raw-store read, DART API, or GCS call occurred.

### Run 423 follow-up: bounded collapsed KAM grammar repairs (code/test/read-only only)

- Three locally cached KAM grammar forms were diagnosed read-only against `/private/tmp/kreports-investor-r5.KVjjJj/kreports-rehearsal-audit-procedure.db`; the candidate database was not mutated and no DART or GCS operation was called.
- NICE consolidated receipt `20260323800995_11164269` used consecutive `(1)`/`(2)` matter markers with bullet-prefixed reason headings. The marker was treated as insufficient plain title structure, allowing equivalent heading pairs to merge. A marker is now structural only with this narrow source evidence (separator-only padding or the bullet-prefixed reason heading), so ordinary numbered procedures remain ambiguity-protected.
- Hanon receipt `20260318800701_11137043` starts `영업권 손상평가 연결재무제표 주석 15...` without a reason heading. Recovery now accepts only a bounded risk-title followed immediately by an inline financial-statement note and retains the note as the reason; generic reason prose still cannot invent a title.
- Hwaseung receipt `20260320801006_11157085` embeds `우리가 수행한 주요 감사절차는 다음과 같습니다.` after its first reason sentence and begins both inline reasons with `연결실체는`. The collapsed splitter recognizes that exact sentence, the numbered omitted-reason recovery recognizes `연결실체는`, and its relaxed title ending remains limited to consecutive markers with one explicit response per matter. A generic procedure lead-in following an already explicit response heading is kept as response prose, not a second response frame.
- RED: three minimized parser fixtures failed (NICE collapsed to one item; Hanon/Hwaseung were errors). GREEN: all three recover their expected 2/1/2 KAM counts and explicit procedure lists. Full focused parser/indexer/recovery validation passed **312 tests**; Ruff and `git diff --check` passed.
- Read-only six-receipt probe results: NICE consolidated `20260323800995_11164269` complete/2 (4,4 procedures); Hanon `20260318800701_11137043` complete/1 (7); Hwaseung `20260320801006_11157085` complete/2 (3,3); prior Lotte Holdings `20260316802369_11129286` complete/1 (9) and `20260316802369_11129287` complete/1 (7); NICE standalone `20260323800995_11164268` complete/1 (5). Every result had empty limitations.
