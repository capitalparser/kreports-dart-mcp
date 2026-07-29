# Task 2 Report: Persist and Read Quality Freshness

## Result

- The existing company-year rebuild writes canonical summary JSON and its
  SHA-256 in the same per-row transaction as statuses, grades, blockers, and
  `updated_at`.
- Reads expose `input_fingerprint`, `evidence_summary`, and
  `freshness_limitations` additively.
- Legacy blank fingerprints remain readable and explicitly freshness-limited.
- Malformed/non-object summaries and hash mismatches return an empty summary
  plus one bounded limitation instead of claiming freshness.
- Dataset and release digests now include the fingerprint and canonical parsed
  summary object while continuing to exclude `updated_at`.

## RED

Command:

```bash
uv run pytest \
  tests/test_company_year_quality.py \
  tests/test_quality_release_gate.py \
  tests/test_dataset_manifest.py \
  -q
```

Result: `9 failed, 67 passed`.

- Four read/rebuild failures showed the additive freshness keys were absent.
- Three digest failures showed fingerprint/summary semantics were ignored.
- Two invalid-summary cases showed non-object/malformed JSON was accepted by
  the snapshot digest.

## GREEN

Commands:

```bash
uv run pytest \
  tests/test_company_year_quality.py \
  tests/test_quality_release_gate.py \
  tests/test_dataset_manifest.py \
  tests/test_mcp_resources.py \
  -q

uv run ruff check \
  kreports/quality/company_year_fingerprint.py \
  kreports/quality/company_year.py \
  kreports/db/quality_snapshot.py \
  tests/test_company_year_quality.py \
  tests/test_quality_release_gate.py

git diff --check
```

Results:

- `121 passed`, `2690 warnings`, exit status `0`.
- Ruff: `All checks passed!`
- `git diff --check`: passed.

## Self-review

- Status and grade functions are untouched; the maps are constructed only
  after all existing computations complete.
- Summary blockers are the same blockers persisted in `blockers_json`, with
  canonical deduplication/sorting.
- A second unchanged rebuild retains the fingerprint and byte-identical
  summary JSON even though `updated_at` is recomputed.
- Adding a real `Auditor` fixture changes `auditor_status` and the fingerprint.
- Read validation never returns unverified parsed content.
- Snapshot tests separately prove JSON whitespace/key order stability,
  fingerprint sensitivity, summary-content sensitivity, and malformed object
  rejection.
- All tests use the isolated pytest engine; no live database, sidecar, DART,
  network, or remote Git operation occurred.
- Ruff-safe import and `UTC` alias changes in already modified lint-target
  files are mechanical and behavior-preserving.
