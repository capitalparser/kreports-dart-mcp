# SDD ledger — plan: final integration of DB provenance hardening and semantic peer context

- Integration base: fadbc702a6c5ee1a8bf0e4170ab25c6a71b89a6f
- Semantic owner head: 9b0ac2af209899ee92b91c9491a1c17c229db77c
- Baseline: 130 passed in 20.75s
- Live immutable QA: database unchanged; release blocked by stale schema/artifact; chatbot blocker-token UX finding open
- Task 1 merge: `fadbc702..78c8bc1`; independent review found three Important issues.
- Task 1 fix round 1: `78c8bc1..8f76d7d`; schema refs and cohort reuse addressed, provenance still open.
- Task 1 fix round 2: `8f76d7d..7ddd2f8`; provenance revalidation improved, exact binding still open.
- Task 1 fix round 3: `7ddd2f8..966fc71`; report-name and explicit source identity added, strict ID type still open.
- Task 1 fix round 4: `966fc71..b861d09`; strict positive built-in integer source ID added; independent Sol review PASS with no Critical/Important findings.
- Task 1 complete at `b861d09c5a6bfb58e068ad3c54516c4227f1d794`.
- Task 2 public release-context UX: `8f03508`; independent Sol review PASS; integrated focused suite 103 passed before later combined QA.
- Deployment preparation: three reviewed commits integrated through `2330a4a`; two fix rounds; final independent Sol review PASS; integrated deployment/HTTP suite 14 passed.
- Integrated regression hardening: `5cfc720..b27fcc6`; strict company resolution and exact 9-query contract retained; canonical semantic fixtures/fail-closed tests; independent Sol review PASS.
- Combined focused QA at `b27fcc6`: 237 passed; Ruff and diff checks passed.
- Non-real-data suite: 2199 passed, 4 skipped, 1 environment failure solely because APFS clone safety correctly rejected free space below 10 GiB.
- Readonly live-data suite on immutable owner DB: 215 passed, 1 skipped; SHA-256, size, inode, mtime, and zero-byte WAL unchanged.
- Retained-clone rehearsal remains blocked before clone creation by non-empty stale SHM and free space below the 10 GiB floor.
