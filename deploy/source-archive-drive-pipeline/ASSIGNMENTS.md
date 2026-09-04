# Worker assignments

Update this table before starting a worker. “Assigned” is a reservation, not
proof of completion. Do not overwrite another worker's local checkpoint.

| Worker | Year(s) | Shard range | Bundle SHA-256 | Candidate DB SHA-256 | Status | Started (KST) | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| unassigned | 2021 | all 0–63 | pending | pending | open | — | assign one owner |
| unassigned | 2022 | all 0–63 | pending | pending | open | — | assign one owner |
| unassigned | 2023 | all 0–63 | pending | pending | open | — | assign one owner |
| unassigned | 2024 | all 0–63 | pending | pending | open | — | assign one owner |
| unassigned | 2025 | all 0–63 | pending | pending | open | — | assign one owner |

For more workers, split a single year into disjoint shard ranges, for example
`2023 / 0–15`, `2023 / 16–31`, and so on. All rows must use the same candidate
DB SHA and worker bundle SHA. A worker marks `complete` only after the
coordinator has collected its local outcome summary; it never means the MCP
runtime has been updated.
