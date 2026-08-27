# All-issuer source archive rollout — 2026-08-28

## Verified identity and preconditions

- Implementation HEAD: `1092ffb87daccf034cd853a68136c9dd47fefa3c`; it includes
  `1596990`.
- The supplied compact-v2 catalog SHA-256 is
  `c3910d32f8fe504b30e116e1a3b8d26921dd1725013fcf5805133f41c2c12050`.
- No active non-read-only handle to the catalog was observed.
- The v3 local state root and Drive prefix
  `KReports Data Lake/source-archive-v3-all-annual-issuers` were fresh.
- The configured `vault:` remote was confirmed as a Drive backend. Drive
  quota read-only check: total `5,497,558,138,880` bytes, used
  `34,599,565,163`, free `5,461,211,200,579`.
- The bounded spool directory was not present before apply. It was not created
  manually because apply did not start.

## No-write v3 planning evidence

The all-annual-issuers preflight and target preview both froze the same v3
digest: `b6fa82bada5e185faabfb74176a54cfbb61c1753b7e125639bf34a3a290a0211`.
The target set contains 14,813 company-years:

| Cohort | Targets | Discovered | Gap |
| --- | ---: | ---: | ---: |
| `annual_report_issuer_outside_verified_markets` | 2,176 | 2,176 | 0 |
| `verified_kosdaq` | 8,453 | 8,171 | 282 |
| `verified_kospi` | 4,184 | 4,079 | 105 |
| **Total** | **14,813** | **14,426** | **387** |

The 2,176 outside-market targets remain historical listing status
`unclassified`; absence of verified KOSPI/KOSDAQ evidence is not a conclusion
that an issuer was unlisted.

The lowest shard containing an outside-market target was shard `0`. Its
no-network dry-run contained 276 targets (49 outside-market, 161 KOSDAQ, and
66 KOSPI), including 273 discovered targets and 3 metadata gaps. The dry-run
used zero DART calls and performed no Drive or local raw-file write.

## Apply and verification result

The single bounded apply (`--max-dart-calls 100`) was **not started**. The
collector environment did not contain a DART API key, so authentication could
not be proven. The source-archive safety contract requires stopping before the
first DART request when authentication is unavailable.

- DART budget: `100` authorized; `0` used.
- Drive objects/bytes created: `0` / `0`.
- Source statuses produced by apply: none.
- `source-archive-verify`: not run because no frozen apply target manifest was
  created.
- No database, candidate artifact, runtime artifact, Lightsail deployment,
  promotion, push, v2 state, or existing spool was modified.

## Limitations and next gate

This is measured v3 planning and dry-run evidence, not raw archive coverage.
The missing DART credential is the blocking concern. Historical unlisted
classification, candidate database construction, and runtime promotion remain
outside this result and require separate evidence and approval.
