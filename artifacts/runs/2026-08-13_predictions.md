# Pre-registered prediction check

Replicates: 3 — artifacts/runs/2026-08-13_r1, artifacts/runs/2026-08-13_r2, artifacts/runs/2026-08-13_r3

## Arm rates across replicates

| arm | pass n=88 (mean ± SD) | pass n=77 (mean ± SD) | per replicate (n=77) |
|---|---|---|---|
| A_open | 0.553 ± 0.017 | 0.632 ± 0.020 | 49/77, 47/77, 50/77 |
| A_late_only | 0.561 ± 0.024 | 0.641 ± 0.027 | 50/77, 47/77, 51/77 |
| A_full | 0.587 ± 0.017 | 0.671 ± 0.020 | 52/77, 50/77, 53/77 |

## Dual scoring (v22 R1): judge-inclusive vs deterministic-core

Deterministic-core drops `semantic_judge_pass` — the one `case_pass` term the treatment retries until it flips — and keeps the six gold checks.

| replicate | arm | judge-inclusive n=88 | deterministic-core n=88 |
|---|---|---|---|
| 2026-08-13_r1 | A_open | 49/88 = 0.557 | 49/88 = 0.557 |
| 2026-08-13_r1 | A_late_only | 50/88 = 0.568 | 50/88 = 0.568 |
| 2026-08-13_r1 | A_full | 52/88 = 0.591 | 52/88 = 0.591 |
| 2026-08-13_r2 | A_open | 47/88 = 0.534 | 48/88 = 0.545 |
| 2026-08-13_r2 | A_late_only | 47/88 = 0.534 | 47/88 = 0.534 |
| 2026-08-13_r2 | A_full | 50/88 = 0.568 | 50/88 = 0.568 |
| 2026-08-13_r3 | A_open | 50/88 = 0.568 | 51/88 = 0.580 |
| 2026-08-13_r3 | A_late_only | 51/88 = 0.580 | 51/88 = 0.580 |
| 2026-08-13_r3 | A_full | 53/88 = 0.602 | 53/88 = 0.602 |

## Evidence and short-circuit summary (per replicate)

| replicate | arm | evidence absent | clarification | request_address | rewound |
|---|---|---:|---:|---:|---:|
| 2026-08-13_r1 | A_open | 61/88 | 14 | 7 | 0 |
| 2026-08-13_r1 | A_late_only | 61/88 | 14 | 7 | 5 |
| 2026-08-13_r1 | A_full | 61/88 | 14 | 3 | 7 |
| 2026-08-13_r2 | A_open | 61/88 | 14 | 7 | 0 |
| 2026-08-13_r2 | A_late_only | 61/88 | 14 | 7 | 2 |
| 2026-08-13_r2 | A_full | 61/88 | 14 | 3 | 7 |
| 2026-08-13_r3 | A_open | 61/88 | 14 | 7 | 0 |
| 2026-08-13_r3 | A_late_only | 61/88 | 14 | 7 | 2 |
| 2026-08-13_r3 | A_full | 61/88 | 14 | 4 | 10 |

## Checkpoint firing rates

| replicate | arm | attempts | early fired | early→rewind | late fired | late→rewind | judge pass/fail | attribution on failures |
|---|---|---:|---:|---:|---:|---:|---|---|
| 2026-08-13_r1 | A_open | 0 | 0 | 0 | 0 | 0 | 0/0 | — |
| 2026-08-13_r1 | A_late_only | 72 | 0 | 0 | 72 | 5 | 67/5 | {'summarizer': 5} |
| 2026-08-13_r1 | A_full | 166 | 93 | 5 | 73 | 2 | 71/2 | {'summarizer': 2} |
| 2026-08-13_r2 | A_open | 0 | 0 | 0 | 0 | 0 | 0/0 | — |
| 2026-08-13_r2 | A_late_only | 69 | 0 | 0 | 69 | 2 | 66/3 | {'summarizer': 3} |
| 2026-08-13_r2 | A_full | 166 | 93 | 5 | 73 | 2 | 71/2 | {'summarizer': 2} |
| 2026-08-13_r3 | A_open | 0 | 0 | 0 | 0 | 0 | 0/0 | — |
| 2026-08-13_r3 | A_late_only | 69 | 0 | 0 | 69 | 2 | 67/2 | {'summarizer': 2} |
| 2026-08-13_r3 | A_full | 168 | 92 | 4 | 76 | 6 | 69/7 | {'summarizer': 7} |

## Capped-rate sensitivity (Fix 2 disclosure)

Fix 2 removed the cost/wall vetoes from `case_pass` because they applied to the treatment arms only. This is what the rate would have been had they been kept — the confound, quantified.

| replicate | arm | uncapped | capped | cost_exceeded | wall_exceeded |
|---|---|---|---|---:|---:|
| 2026-08-13_r1 | A_open | 49/88 = 0.557 | 49/88 = 0.557 | 0 | 0 |
| 2026-08-13_r1 | A_late_only | 50/88 = 0.568 | 43/88 = 0.489 | 13 | 0 |
| 2026-08-13_r1 | A_full | 52/88 = 0.591 | 48/88 = 0.545 | 14 | 0 |
| 2026-08-13_r2 | A_open | 47/88 = 0.534 | 47/88 = 0.534 | 0 | 0 |
| 2026-08-13_r2 | A_late_only | 47/88 = 0.534 | 43/88 = 0.489 | 14 | 0 |
| 2026-08-13_r2 | A_full | 50/88 = 0.568 | 44/88 = 0.500 | 14 | 0 |
| 2026-08-13_r3 | A_open | 50/88 = 0.568 | 50/88 = 0.568 | 0 | 0 |
| 2026-08-13_r3 | A_late_only | 51/88 = 0.580 | 43/88 = 0.489 | 14 | 0 |
| 2026-08-13_r3 | A_full | 53/88 = 0.602 | 47/88 = 0.534 | 15 | 0 |

## McNemar (exact), n=88 and n=77

| replicate | contrast | n | discordant | b better | a better | p |
|---|---|---:|---:|---:|---:|---:|
| 2026-08-13_r1 | A_open vs A_late_only | 88 | 3 | 2 | 1 | 1.0000 |
| 2026-08-13_r1 | A_open vs A_late_only | 77 | 3 | 2 | 1 | 1.0000 |
| 2026-08-13_r1 | A_open vs A_late_only | 88 det | 3 | 2 | 1 | 1.0000 |
| 2026-08-13_r1 | A_open vs A_full | 88 | 7 | 5 | 2 | 0.4531 |
| 2026-08-13_r1 | A_open vs A_full | 77 | 7 | 5 | 2 | 0.4531 |
| 2026-08-13_r1 | A_open vs A_full | 88 det | 7 | 5 | 2 | 0.4531 |
| 2026-08-13_r1 | A_late_only vs A_full | 88 | 10 | 6 | 4 | 0.7539 |
| 2026-08-13_r1 | A_late_only vs A_full | 77 | 10 | 6 | 4 | 0.7539 |
| 2026-08-13_r1 | A_late_only vs A_full | 88 det | 10 | 6 | 4 | 0.7539 |
| 2026-08-13_r2 | A_open vs A_late_only | 88 | 4 | 2 | 2 | 1.0000 |
| 2026-08-13_r2 | A_open vs A_late_only | 77 | 4 | 2 | 2 | 1.0000 |
| 2026-08-13_r2 | A_open vs A_late_only | 88 det | 3 | 1 | 2 | 1.0000 |
| 2026-08-13_r2 | A_open vs A_full | 88 | 11 | 7 | 4 | 0.5488 |
| 2026-08-13_r2 | A_open vs A_full | 77 | 11 | 7 | 4 | 0.5488 |
| 2026-08-13_r2 | A_open vs A_full | 88 det | 10 | 6 | 4 | 0.7539 |
| 2026-08-13_r2 | A_late_only vs A_full | 88 | 7 | 5 | 2 | 0.4531 |
| 2026-08-13_r2 | A_late_only vs A_full | 77 | 7 | 5 | 2 | 0.4531 |
| 2026-08-13_r2 | A_late_only vs A_full | 88 det | 7 | 5 | 2 | 0.4531 |
| 2026-08-13_r3 | A_open vs A_late_only | 88 | 1 | 1 | 0 | 1.0000 |
| 2026-08-13_r3 | A_open vs A_late_only | 77 | 1 | 1 | 0 | 1.0000 |
| 2026-08-13_r3 | A_open vs A_late_only | 88 det | 0 | 0 | 0 | 1.0000 |
| 2026-08-13_r3 | A_open vs A_full | 88 | 5 | 4 | 1 | 0.3750 |
| 2026-08-13_r3 | A_open vs A_full | 77 | 5 | 4 | 1 | 0.3750 |
| 2026-08-13_r3 | A_open vs A_full | 88 det | 4 | 3 | 1 | 0.6250 |
| 2026-08-13_r3 | A_late_only vs A_full | 88 | 4 | 3 | 1 | 0.6250 |
| 2026-08-13_r3 | A_late_only vs A_full | 77 | 4 | 3 | 1 | 0.6250 |
| 2026-08-13_r3 | A_late_only vs A_full | 88 det | 4 | 3 | 1 | 0.6250 |

## The five pre-registered predictions

| # | prediction | observed | verdict |
|---|---|---|---|
| 1 | `evidence_present == false` on ≥45 of 88 A_open | [61, 61, 61] | PASS |
| 2 | A_open `case_pass` in 30–38 of 77 | [49, 47, 50] | FAIL |
| 3 | late rewinds < 20 **and** top attribution ≠ summarizer | late_rewinds [5, 2, 2, 2, 2, 6], top_attribution ['summarizer', 'summarizer', 'summarizer', 'summarizer', 'summarizer', 'summarizer'] | FAIL |
| 4 | Fix 5 fires ≥3 of 4, ≥2 convert, both guards hold | fired [4, 4, 4], converted [3, 3, 3], guards_held [2, 1, 2], tracked_fired [['EKR_GEN_030'], [], []] | FAIL |
| 5 | A_full ≥ A_open in ≥2 of 3 replicates | 3 of 3 | PASS |

Per-replicate Fix 5 detail:

- `2026-08-13_r1` fired=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019', 'DEMO_EXTRA_GEN_001'] converted=['EKR_GEN_017', 'EKR_GEN_018', 'DEMO_EXTRA_GEN_001'] guards_held=['DEMO_EXTRA_CLAR_001', 'DEMO_EXTRA_CLAR_002'] tracked=['EKR_GEN_030'] forced_route=[]
- `2026-08-13_r2` fired=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019', 'DEMO_EXTRA_GEN_001'] converted=['EKR_GEN_017', 'EKR_GEN_018', 'DEMO_EXTRA_GEN_001'] guards_held=['DEMO_EXTRA_CLAR_002'] tracked=[] forced_route=[]
- `2026-08-13_r3` fired=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019', 'DEMO_EXTRA_GEN_001'] converted=['EKR_GEN_017', 'EKR_GEN_018', 'DEMO_EXTRA_GEN_001'] guards_held=['DEMO_EXTRA_CLAR_001', 'DEMO_EXTRA_CLAR_002'] tracked=[] forced_route=[]

## Consensus McNemar (pre-registered §4), both scorings

| contrast | scoring | gained | lost | p |
|---|---|---|---|---|
| A_open vs A_late_only | judge-inclusive | 1: BRF_ODEN_10_RADMANSGATAN_31_004_HEATING | 1: DEMO_EXTRA_GEN_006 | 1.0000 |
| A_open vs A_full | judge-inclusive | 4: BRF_ODEN_10_RADMANSGATAN_31_001_PERFORMANCE, DEMO_EXTRA_GEN_001, EKR_GEN_017, EKR_GEN_018 | 1: BRF_ODEN_10_RADMANSGATAN_31_001_HEATING | 0.3750 |
| A_late_only vs A_full | judge-inclusive | 5: BRF_ODEN_10_RADMANSGATAN_31_001_PERFORMANCE, DEMO_EXTRA_GEN_001, DEMO_EXTRA_GEN_006, EKR_GEN_017, EKR_GEN_018 | 2: BRF_ODEN_10_RADMANSGATAN_31_001_HEATING, BRF_ODEN_10_RADMANSGATAN_31_004_HEATING | 0.4531 |
| A_open vs A_late_only | deterministic-core | 1: BRF_ODEN_10_RADMANSGATAN_31_004_HEATING | 1: DEMO_EXTRA_GEN_006 | 1.0000 |
| A_open vs A_full | deterministic-core | 4: BRF_ODEN_10_RADMANSGATAN_31_001_PERFORMANCE, DEMO_EXTRA_GEN_001, EKR_GEN_017, EKR_GEN_018 | 1: BRF_ODEN_10_RADMANSGATAN_31_001_HEATING | 0.3750 |
| A_late_only vs A_full | deterministic-core | 5: BRF_ODEN_10_RADMANSGATAN_31_001_PERFORMANCE, DEMO_EXTRA_GEN_001, DEMO_EXTRA_GEN_006, EKR_GEN_017, EKR_GEN_018 | 2: BRF_ODEN_10_RADMANSGATAN_31_001_HEATING, BRF_ODEN_10_RADMANSGATAN_31_004_HEATING | 0.4531 |
