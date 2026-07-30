# Exact Annual-Receipt Provenance Remediation

## Scope

- Annual-filing proof now accepts only an unmodified 14-digit stored receipt
  whose date equals the recorded disclosure date.
- A later malformed annual disclosure blocks that company/year source instead
  of falling back to an older filing.
- The same rule applies to compact citation anchors, QoE provenance,
  materiality release coverage, policy-change sources, and policy readiness.

## Regressions

- suffix, prefix, and whitespace receipt contamination;
- latest-contaminated disclosure with an older otherwise valid filing;
- QoE source-list non-leakage;
- policy-change answer-pack non-leakage; and
- two-year policy-readiness false usability.

## Verification

- `uv run --extra dev pytest tests/test_filing_provenance.py tests/test_qoe_multiyear_provenance.py tests/test_policy_changes.py tests/test_db_schema_contract_review.py tests/test_quality_release_gate.py -q` — 82 passed
- `uv run --extra dev pytest tests/test_financial_compact_provenance.py tests/test_financial_timeseries.py tests/test_materiality_benchmark.py tests/test_peer_note_presentation_comparison.py tests/test_mcp_answer_pack.py tests/test_mcp_contracts.py tests/test_api_evidence_packs.py -q` — 129 passed
- `uv run --extra dev ruff check ...` and `git diff --check` — passed

The repository-wide suite was also started. It stops on an unrelated,
unseeded `tests/test_audit_landscape.py::test_audit_landscape_samsung_basic_shape`
(`sqlite3.OperationalError: no such table: companies`) after 212 preceding
tests passed; it does not exercise this remediation path.
