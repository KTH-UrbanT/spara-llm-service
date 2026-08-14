# Pre-registered prediction check

Replicates: 3 — artifacts/runs/2026-08-13_v21_r1, artifacts/runs/2026-08-13_v21_r2, artifacts/runs/2026-08-13_v21_r3

## Arm rates across replicates

| arm | pass n=88 (mean ± SD) | pass n=77 (mean ± SD) | per replicate (n=77) |
|---|---|---|---|
| A_open | 0.557 ± 0.011 | 0.636 ± 0.013 | 50/77, 49/77, 48/77 |
| A_late_only | 0.568 ± 0.023 | 0.649 ± 0.026 | 52/77, 50/77, 48/77 |
| A_full | 0.625 ± 0.000 | 0.714 ± 0.000 | 55/77, 55/77, 55/77 |

## Evidence and short-circuit summary (per replicate)

| replicate | arm | evidence absent | clarification | request_address | rewound |
|---|---|---:|---:|---:|---:|
| 2026-08-13_v21_r1 | A_open | 61/88 | 14 | 7 | 0 |
| 2026-08-13_v21_r1 | A_late_only | 61/88 | 14 | 7 | 0 |
| 2026-08-13_v21_r1 | A_full | 61/88 | 14 | 3 | 4 |
| 2026-08-13_v21_r2 | A_open | 61/88 | 14 | 7 | 0 |
| 2026-08-13_v21_r2 | A_late_only | 61/88 | 14 | 7 | 3 |
| 2026-08-13_v21_r2 | A_full | 61/88 | 14 | 4 | 6 |
| 2026-08-13_v21_r3 | A_open | 61/88 | 14 | 7 | 0 |
| 2026-08-13_v21_r3 | A_late_only | 61/88 | 14 | 7 | 6 |
| 2026-08-13_v21_r3 | A_full | 61/88 | 14 | 4 | 6 |

## Checkpoint firing rates

| replicate | arm | attempts | early fired | early→rewind | late fired | late→rewind | judge pass/fail | attribution on failures |
|---|---|---:|---:|---:|---:|---:|---|---|
| 2026-08-13_v21_r1 | A_open | 0 | 0 | 0 | 0 | 0 | 0/0 | — |
| 2026-08-13_v21_r1 | A_late_only | 67 | 0 | 0 | 67 | 0 | 67/0 | — |
| 2026-08-13_v21_r1 | A_full | 163 | 92 | 4 | 71 | 0 | 71/0 | — |
| 2026-08-13_v21_r2 | A_open | 0 | 0 | 0 | 0 | 0 | 0/0 | — |
| 2026-08-13_v21_r2 | A_late_only | 70 | 0 | 0 | 70 | 3 | 67/3 | {'summarizer': 3} |
| 2026-08-13_v21_r2 | A_full | 164 | 93 | 5 | 71 | 1 | 70/1 | {'summarizer': 1} |
| 2026-08-13_v21_r3 | A_open | 0 | 0 | 0 | 0 | 0 | 0/0 | — |
| 2026-08-13_v21_r3 | A_late_only | 73 | 0 | 0 | 73 | 6 | 66/7 | {'summarizer': 7} |
| 2026-08-13_v21_r3 | A_full | 164 | 93 | 5 | 71 | 1 | 70/1 | {'summarizer': 1} |

## Capped-rate sensitivity (Fix 2 disclosure)

Fix 2 removed the cost/wall vetoes from `case_pass` because they applied to the treatment arms only. This is what the rate would have been had they been kept — the confound, quantified.

| replicate | arm | uncapped | capped | cost_exceeded | wall_exceeded |
|---|---|---|---|---:|---:|
| 2026-08-13_v21_r1 | A_open | 50/88 = 0.568 | 50/88 = 0.568 | 0 | 0 |
| 2026-08-13_v21_r1 | A_late_only | 52/88 = 0.591 | 44/88 = 0.500 | 12 | 0 |
| 2026-08-13_v21_r1 | A_full | 55/88 = 0.625 | 47/88 = 0.534 | 13 | 0 |
| 2026-08-13_v21_r2 | A_open | 49/88 = 0.557 | 49/88 = 0.557 | 0 | 0 |
| 2026-08-13_v21_r2 | A_late_only | 50/88 = 0.568 | 43/88 = 0.489 | 13 | 0 |
| 2026-08-13_v21_r2 | A_full | 55/88 = 0.625 | 49/88 = 0.557 | 16 | 0 |
| 2026-08-13_v21_r3 | A_open | 48/88 = 0.545 | 48/88 = 0.545 | 0 | 0 |
| 2026-08-13_v21_r3 | A_late_only | 48/88 = 0.545 | 42/88 = 0.477 | 15 | 0 |
| 2026-08-13_v21_r3 | A_full | 55/88 = 0.625 | 46/88 = 0.523 | 18 | 0 |

## McNemar (exact), n=88 and n=77

| replicate | contrast | n | discordant | b better | a better | p |
|---|---|---:|---:|---:|---:|---:|
| 2026-08-13_v21_r1 | A_open vs A_late_only | 88 | 2 | 2 | 0 | 0.5000 |
| 2026-08-13_v21_r1 | A_open vs A_late_only | 77 | 2 | 2 | 0 | 0.5000 |
| 2026-08-13_v21_r1 | A_open vs A_full | 88 | 5 | 5 | 0 | 0.0625 |
| 2026-08-13_v21_r1 | A_open vs A_full | 77 | 5 | 5 | 0 | 0.0625 |
| 2026-08-13_v21_r1 | A_late_only vs A_full | 88 | 3 | 3 | 0 | 0.2500 |
| 2026-08-13_v21_r1 | A_late_only vs A_full | 77 | 3 | 3 | 0 | 0.2500 |
| 2026-08-13_v21_r2 | A_open vs A_late_only | 88 | 1 | 1 | 0 | 1.0000 |
| 2026-08-13_v21_r2 | A_open vs A_late_only | 77 | 1 | 1 | 0 | 1.0000 |
| 2026-08-13_v21_r2 | A_open vs A_full | 88 | 8 | 7 | 1 | 0.0703 |
| 2026-08-13_v21_r2 | A_open vs A_full | 77 | 8 | 7 | 1 | 0.0703 |
| 2026-08-13_v21_r2 | A_late_only vs A_full | 88 | 7 | 6 | 1 | 0.1250 |
| 2026-08-13_v21_r2 | A_late_only vs A_full | 77 | 7 | 6 | 1 | 0.1250 |
| 2026-08-13_v21_r3 | A_open vs A_late_only | 88 | 4 | 2 | 2 | 1.0000 |
| 2026-08-13_v21_r3 | A_open vs A_late_only | 77 | 4 | 2 | 2 | 1.0000 |
| 2026-08-13_v21_r3 | A_open vs A_full | 88 | 9 | 8 | 1 | 0.0391 |
| 2026-08-13_v21_r3 | A_open vs A_full | 77 | 9 | 8 | 1 | 0.0391 |
| 2026-08-13_v21_r3 | A_late_only vs A_full | 88 | 7 | 7 | 0 | 0.0156 |
| 2026-08-13_v21_r3 | A_late_only vs A_full | 77 | 7 | 7 | 0 | 0.0156 |

## The five pre-registered predictions

| # | prediction | observed | verdict |
|---|---|---|---|
| 1 | `evidence_present == false` on ≥45 of 88 A_open | [61, 61, 61] | PASS |
| 2 | A_open `case_pass` in 30–38 of 77 | [50, 49, 48] | FAIL |
| 3 | late rewinds < 20 **and** top attribution ≠ summarizer | late_rewinds [0, 0, 3, 1, 6, 1], top_attribution [None, None, 'summarizer', 'summarizer', 'summarizer', 'summarizer'] | FAIL |
| 4 | Fix 5 fires ≥3 of 4, ≥2 convert, both guards hold | fired [4, 4, 4], converted [3, 4, 4], guards_held [2, 2, 2], tracked_fired [[], [], []] | PASS |
| 5 | A_full ≥ A_open in ≥2 of 3 replicates | 3 of 3 | PASS |

Per-replicate Fix 5 detail:

- `2026-08-13_v21_r1` fired=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019', 'DEMO_EXTRA_GEN_001'] converted=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019'] guards_held=['DEMO_EXTRA_CLAR_001', 'DEMO_EXTRA_CLAR_002'] tracked=[] forced_route=[]
- `2026-08-13_v21_r2` fired=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019', 'DEMO_EXTRA_GEN_001'] converted=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019', 'DEMO_EXTRA_GEN_001'] guards_held=['DEMO_EXTRA_CLAR_001', 'DEMO_EXTRA_CLAR_002'] tracked=[] forced_route=[]
- `2026-08-13_v21_r3` fired=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019', 'DEMO_EXTRA_GEN_001'] converted=['EKR_GEN_017', 'EKR_GEN_018', 'EKR_GEN_019', 'DEMO_EXTRA_GEN_001'] guards_held=['DEMO_EXTRA_CLAR_001', 'DEMO_EXTRA_CLAR_002'] tracked=[] forced_route=[]
