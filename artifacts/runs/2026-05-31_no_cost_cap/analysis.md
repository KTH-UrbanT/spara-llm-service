# Closed-loop analysis: 2026-05-31_no_cost_cap

## Case-pass rate

| Arm | N | Pass | Pass rate | 1st-attempt rate |
|---|---|---|---|---|
| A_open | 88 | 19 | 0.216 | 0.216 |
| A_late_only | 88 | 15 | 0.170 | 0.319 |
| A_full | 88 | 16 | 0.182 | 0.349 |

## McNemar paired tests (Holm-corrected)

**A_open vs A_full**: n_discordant=7, n(A_full better)=2, p=0.4531, ns
**A_late_only vs A_full**: n_discordant=5, n(A_full better)=3, p=1.0000, ns

## Stratified pass rate

### A_open
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 14 | 0.412 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 3 | 0.079 |
### A_late_only
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 13 | 0.382 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 0 | 0.000 |
### A_full
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 13 | 0.382 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 1 | 0.026 |

## Per-check pass rate

| Check | A_open | A_late_only | A_full |
|---|---|---|---|
| route_match | 0.625 | 0.625 | 0.648 |
| agent_match | 0.932 | 0.932 | 0.955 |
| building_id_match | 0.818 | 0.818 | 0.818 |
| field_coverage_pass | 0.795 | 0.795 | 0.795 |
| must_include_pass | 0.773 | 0.818 | 0.795 |
| must_not_include_pass | 1.000 | 1.000 | 1.000 |
| semantic_judge_pass | 0.580 | 0.534 | 0.534 |

## Hint effectiveness

**A_late_only**: 41 retries, 41 changed (100.0%)
**A_full**: 49 retries, 48 changed (98.0%)

## Retry, latency & cost

| Arm | retried | terminated-no-pass | router-disallowed | mean ms | p95 ms | p99 ms | tokens | cost-exceeded |
|---|---|---|---|---|---|---|---|---|
| A_open | 0 | 0 | 0 | 13422 | 29458 | 46896 | 231517 | 0 |
| A_late_only | 41 | 41 | 1 | 23037 | 56792 | 115790 | 632865 | 0 |
| A_full | 45 | 41 | 0 | 25590 | 55323 | 61904 | 725501 | 0 |