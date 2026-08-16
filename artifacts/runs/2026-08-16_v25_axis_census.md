# Axis census — plan-eil-v25 Step 3

Degeneracy flag: ≥ 98 % of *scored* values on one point. Sub-floor = score < 4 (the axis fired).

## Live late-checkpoint judgements (loop arms) — n = 446

| axis | scored | null | mean | var | % ceiling | % sub-floor | modal (share) | degenerate |
|---|---|---|---|---|---|---|---|---|
| `faithfulness` | 196 | 250 | 9.61 | 1.88 | 86.2 % | 2.6 % | 10 (86.2 %) | no |
| `answer_relevance` | 446 | 0 | 9.96 | 0.06 | 97.1 % | 0.0 % | 10 (97.1 %) | no |
| `entity_consistency` | 184 | 262 | 8.39 | 12.40 | 82.1 % | 17.4 % | 10 (82.1 %) | no |
| `calibration` | 446 | 0 | 9.41 | 0.70 | 55.6 % | 0.0 % | 10 (55.6 %) | no |

## Measurement judge on the control arm (`A_open`) — n = 264

| axis | scored | null | mean | var | % ceiling | % sub-floor | modal (share) | degenerate |
|---|---|---|---|---|---|---|---|---|
| `faithfulness` | 81 | 120 | 9.75 | 0.83 | 90.1 % | 0.0 % | 10 (90.1 %) | no |
| `answer_relevance` | 201 | 0 | 9.93 | 0.29 | 96.0 % | 0.5 % | 10 (96.0 %) | no |
| `entity_consistency` | 75 | 126 | 8.08 | 13.73 | 77.3 % | 20.0 % | 10 (77.3 %) | no |
| `calibration` | 201 | 0 | 9.54 | 0.36 | 59.2 % | 0.0 % | 10 (59.2 %) | no |

## All judgements pooled — n = 710

| axis | scored | null | mean | var | % ceiling | % sub-floor | modal (share) | degenerate |
|---|---|---|---|---|---|---|---|---|
| `faithfulness` | 277 | 370 | 9.65 | 1.58 | 87.4 % | 1.8 % | 10 (87.4 %) | no |
| `answer_relevance` | 647 | 0 | 9.95 | 0.13 | 96.8 % | 0.2 % | 10 (96.8 %) | no |
| `entity_consistency` | 259 | 388 | 8.30 | 12.80 | 80.7 % | 18.1 % | 10 (80.7 %) | no |
| `calibration` | 647 | 0 | 9.45 | 0.60 | 56.7 % | 0.0 % | 10 (56.7 %) | no |

## Histograms (all judgements pooled)

- `faithfulness`: 1×1, 2×3, 3×1, 4×1, 6×2, 7×4, 8×7, 9×16, 10×242 — null ×370
- `answer_relevance`: 3×1, 7×2, 8×1, 9×17, 10×626 — null ×0
- `entity_consistency`: 0×22, 1×15, 2×9, 3×1, 5×1, 9×2, 10×209 — null ×388
- `calibration`: 4×3, 5×1, 7×2, 8×53, 9×221, 10×367 — null ×0
