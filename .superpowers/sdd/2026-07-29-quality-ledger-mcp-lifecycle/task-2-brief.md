# Task 2 Brief: Persist and Read Quality Freshness

## Objective

Persist the canonical evidence summary and its fingerprint in the existing
company-year rebuild transaction, expose verified freshness fields on reads,
and include the non-volatile fields in release/dataset content digests.

## Contract

- Rebuild status and grade algorithms remain unchanged.
- A rebuild writes summary JSON and fingerprint in the same transaction as the
  existing ledger fields.
- Unchanged inputs retain byte-identical summary JSON and the same
  fingerprint, regardless of `updated_at`.
- A changed evidence-derived status changes the fingerprint.
- Reads expose a parsed summary only when it is a JSON object and its
  recomputed fingerprint matches.
- Legacy blank fingerprints remain readable but return an explicit Korean
  freshness limitation and no summary.
- Malformed summaries and fingerprint mismatches fail closed without exposing
  unverified summary content.
- Dataset/release digests include fingerprint and summary, exclude
  `updated_at`, and canonicalize the summary object rather than JSON
  whitespace.

## TDD boundary

1. Add rebuild/read and digest tests before production edits.
2. Confirm failures on absent response fields and unchanged digest semantics.
3. Add the smallest persistence/read/digest implementation.
4. Run the plan's quality/release/dataset/resource regressions and Ruff.

Only fixture databases created by pytest are permitted.
