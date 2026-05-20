# Disclosure DB Completeness Procedure

Goal: certify the `disclosures` table before judging downstream datasets.

Do not use current listed companies as the primary denominator. That denominator
mixes IPOs, delistings, mergers, SPACs, foreign issuers, and market transfers.
Use OpenDART `list.json` receipt numbers as the source ledger, then compare
those receipt numbers with local `disclosures.rcept_no`.

## Required Environment

Run only on the maintainer/collector machine:

```bash
export KREPORTS_RUNTIME_MODE=collector
export DART_API_KEY=<opendart-key>
```

Do not set `DART_API_KEY` on the public MCP endpoint.

## Annual Report Filing Windows

Business year to filing-date mapping:

- 2021 business year: `20220101` - `20221231`
- 2022 business year: `20230101` - `20231231`
- 2023 business year: `20240101` - `20241231`
- 2024 business year: `20250101` - `20251231`
- 2025 business year: `20260101` - `20261231`

## Audit Commands

Business reports:

```bash
kreports audit-disclosure-window \
  --start-date 20220101 \
  --end-date 20221231 \
  --disc-type A \
  --report-keyword 사업보고서 \
  --exclude-keyword 제출기한연장 \
  --exclude-keyword 해외증권
```

Audit reports:

```bash
kreports audit-disclosure-window \
  --start-date 20220101 \
  --end-date 20221231 \
  --disc-type F \
  --report-keyword 감사보고서
```

Use `--json` for machine-readable evidence and repeat for each filing year.

## Remediation

If the audit reports missing rows, persist only the missing disclosure-list rows:

```bash
kreports audit-disclosure-window \
  --start-date 20220101 \
  --end-date 20221231 \
  --disc-type A \
  --report-keyword 사업보고서 \
  --exclude-keyword 제출기한연장 \
  --exclude-keyword 해외증권 \
  --persist-missing
```

This does not fetch document bodies, financial statements, audit fees, or
policies. It only certifies the filing ledger.

## Pass Criteria

For each audited window:

- `errors = []`
- `missing_rows = 0`
- `coverage = 100.0%`

Only after this passes should downstream completeness be judged for:

- `report_documents`
- `source_documents`
- `financials` / `financial_facts`
- `auditors`
- `audit_fees`
- `accounting_policy_items`

