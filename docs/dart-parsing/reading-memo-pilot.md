# DART Reading Memo Pilot

This pilot checks whether KReports can support an AI reading workflow beyond parsing:

1. identify numeric gaps from financial statements,
2. connect those gaps to note/policy evidence,
3. separate explained, weakly explained, and unresolved risks.

The pilot used local `kreports.db` cached data for four companies with both financial history and accounting policy items.

## Sample

| Company | Stock code | Segment | Financial coverage | Policy coverage |
| --- | --- | --- | --- | --- |
| 삼성전자 | 005930 | Large manufacturing / electronics | 2021-2025 CFS Q4 | 11 policy keys |
| SK하이닉스 | 000660 | Semiconductor / capex-heavy | 2021-2025 CFS Q4 | 12 policy keys |
| NAVER | 035420 | Platform / service revenue | 2021-2025 CFS Q4 | 12 policy keys |
| 셀트리온 | 068270 | Bio / development cost | 2021-2025 CFS Q4 | 13 policy keys |

Kakao, Hyundai Engineering & Construction, and KB Financial were not used in this first pass because their cached accounting policy coverage was empty or weaker in the local DB.

## Pilot Rules

The first-pass reader used simple deterministic thresholds:

- Revenue YoY absolute change >= 20%
- Operating profit YoY absolute change >= 40%
- Operating cash flow / operating profit < 0.8
- Accrual ratio > 0.2
- Debt / equity > 2.0
- Beneish M-Score > -1.78

Each gap was mapped to a candidate note family. This intentionally tests whether the current evidence layer can support the claim.

## Results

| Company | Numeric gaps | Covered by cached policy | Unresolved | Reading verdict |
| --- | ---: | ---: | ---: | --- |
| 삼성전자 | 0 | 0 | 0 | Pass: stable first-pass read |
| SK하이닉스 | 2 | 2 | 0 | Conditional pass: revenue/profit jump links to revenue policy, but needs revenue breakdown note for stronger analysis |
| NAVER | 0 | 0 | 0 | Pass: stable first-pass read; useful auditor-change context exists |
| 셀트리온 | 3 | 3 | 0 | Conditional: gaps detected, but cash/accrual evidence is weak when only accounting policy excerpts are used |

## Company Notes

### 삼성전자

2025 CFS first-pass metrics:

- Revenue YoY: 10.9%
- Operating profit YoY: 33.2%
- Operating cash flow / operating profit: 1.96
- Debt / equity: 0.30
- Accrual ratio: -0.8872
- Beneish M-Score: -2.7437

No threshold gap was triggered. The cached policy layer includes revenue recognition, inventory, leases, financial instruments, and other core policies, but impairment and provisions were missing from the priority set.

Verdict: the reading memo can safely produce a "no obvious numeric gap" first-pass memo, with caveat that note-table extraction is still needed for deeper inspection.

### SK하이닉스

2025 CFS first-pass metrics:

- Revenue YoY: 46.8%
- Operating profit YoY: 101.2%
- Operating cash flow / operating profit: 1.13
- Debt / equity: 0.46
- Accrual ratio: -0.2427
- Beneish M-Score: -2.2123

Triggered gaps:

- Revenue jump
- Operating profit jump

Cached evidence:

- Revenue recognition policy: `2.19 고객과의 계약에서 생기는 수익`
- Excerpt identifies control transfer, customer acceptance, returns, and estimated return liabilities.

Verdict: the structure works. The memo can detect a semiconductor upcycle-style jump and route it to revenue policy. However, the AI should not stop at policy text. It should request revenue breakdown, segment, inventory, and customer/order notes to explain whether the jump is price, volume, mix, or cycle-driven.

### NAVER

2025 CFS first-pass metrics:

- Revenue YoY: 12.1%
- Operating profit YoY: 11.6%
- Net income YoY: -5.9%
- Operating cash flow / operating profit: 1.40
- Debt / equity: 0.42
- Accrual ratio: -0.7022
- Beneish M-Score: -2.6208

No threshold gap was triggered. Auditor history shows changes in the local DB: 삼일 -> 한영, then 한영 -> 삼정.

Verdict: stable first-pass read. This is a good control case for the memo template because the AI should avoid inventing risk when numeric gaps are not present.

### 셀트리온

2025 CFS first-pass metrics:

- Revenue YoY: 17.0%
- Operating profit YoY: 137.5%
- Operating cash flow / operating profit: 0.55
- Debt / equity: 0.29
- Accrual ratio: 0.3737
- Beneish M-Score: -1.91

Triggered gaps:

- Operating profit jump
- Operating cash flow / operating profit weak
- Accrual ratio high

Cached evidence:

- Revenue recognition policy includes variable consideration and a 178,829 million KRW unbilled asset reference.
- Financial instruments policy was available, but the matched excerpt was a generic K-IFRS amendment discussion, not a strong explanation for cash/accrual gap.

Verdict: the structure works for detection, but current evidence routing is too shallow. For bio/pharma, cash/accrual gaps need actual note-table extraction for trade receivables, contract assets, inventories, development costs, impairment, and variable consideration. Accounting policy alone is not enough.

## Interpretation

The proposed AI reading structure is viable, but only if the evidence layer is split into two tiers:

1. **Policy evidence**: explains the accounting model.
2. **Measurement evidence**: explains current-period numbers, balances, movements, and table disclosures.

The pilot showed that policy evidence is enough to route the AI to the right accounting topic, but not always enough to support a risk conclusion.

## Required Next Parser Work

- Add note-family extraction beyond policy items:
  - revenue note
  - trade receivables / expected credit loss
  - inventories
  - contract assets and variable consideration
  - development costs / intangible assets
  - impairment
  - financial risk tables
- Store note excerpts with source title, span, table raw XML, and confidence.
- Let the reading memo classify evidence quality:
  - `strong`: numeric note table or direct movement disclosure
  - `medium`: relevant accounting policy plus related amount
  - `weak`: generic policy text only
  - `missing`: no matching evidence
- Add a reading memo regression fixture for:
  - SK하이닉스 2025 revenue/profit jump
  - 셀트리온 2025 cash/accrual gap

## Verdict

Conditional pass.

The AI can already produce a useful first-pass reading memo from financial facts plus cached policy items. To make the memo audit-grade or investor-grade, KReports needs structured note disclosure extraction for measurement tables, not just accounting policy paragraphs.
