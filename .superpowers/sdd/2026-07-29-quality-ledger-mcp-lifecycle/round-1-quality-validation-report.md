# Round 1 quality freshness validation report

## Review finding

The review finding was valid. A legacy blank fingerprint and `{}` summary, or an
arbitrary JSON object with a matching raw SHA-256, could previously participate
in a manifest digest without proving that the summary described the persisted
quality row.

## RED

Added tests for:

- extra top-level summary fields;
- invalid status and grade domains;
- blocker count and length bounds;
- quality-version bounds;
- a self-consistent hash over a noncanonical summary;
- summary-to-row grade mismatch; and
- legacy blank freshness in the dataset-manifest path.

Before the implementation, the new focused tests produced 11 failures: eight in
the company-year validation cases and three in the manifest freshness cases.

## GREEN

The implementation now:

- validates an exact, bounded canonical summary schema;
- validates each status and grade against the supported domain;
- validates sorted, unique, bounded blockers and a bounded version;
- recomputes the fingerprint from canonical JSON;
- requires the summary statuses, grades, blockers, and version to equal the
  persisted row;
- rejects invalid rows before manifest content-digest generation; and
- returns an empty evidence summary plus a specific freshness limitation from
  the company-year read path.

Legacy fixtures were upgraded to valid canonical summaries. Tests that
intentionally mutate a row now distinguish a coherent semantic change from an
unverified mismatch.

## Verification

Focused test command:

```text
uv run pytest tests/test_company_year_quality.py tests/test_dataset_manifest.py tests/test_quality_release_gate.py tests/test_mcp_resources.py -q
```

Result: `132 passed`.

No full suite, default/live database, network, remote, or sidecar was used.
