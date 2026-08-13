# Closed-loop analysis: 2026-08-13_r1

## Case-pass rate

| Arm | N | Pass | Pass rate | 1st-attempt rate |
|---|---|---|---|---|
| A_open | 88 | 49 | 0.557 | 0.557 |
| A_late_only | 88 | 50 | 0.568 | 0.566 |
| A_full | 88 | 52 | 0.591 | 0.593 |

## McNemar paired tests (Holm-corrected)

**A_open vs A_full**: n_discordant=7, n(A_full better)=5, p=0.4531, ns
**A_late_only vs A_full**: n_discordant=10, n(A_full better)=6, p=0.7539, ns

## Stratified pass rate

### A_open
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 14 | 0.412 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 33 | 0.868 |
### A_late_only
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 16 | 0.471 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 32 | 0.842 |
### A_full
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 13 | 0.382 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 37 | 0.974 |

## Per-check pass rate

| Check | A_open | A_late_only | A_full |
|---|---|---|---|
| route_match | 0.625 | 0.625 | 0.670 |
| agent_match | 0.932 | 0.932 | 0.977 |
| building_id_match | 0.818 | 0.818 | 0.818 |
| field_coverage_pass | 0.795 | 0.795 | 0.795 |
| must_include_pass | 0.773 | 0.784 | 0.773 |
| must_not_include_pass | 1.000 | 1.000 | 1.000 |
| semantic_judge_pass | 0.784 | 0.784 | 0.830 |

## Hint effectiveness

**A_late_only**: 5 retries, 5 changed (100.0%)
**A_full**: 7 retries, 6 changed (85.7%)

## Retry, latency & cost

| Arm | retried | terminated-no-pass | router-disallowed | mean ms | p95 ms | p99 ms | tokens | cost-exceeded |
|---|---|---|---|---|---|---|---|---|
| A_open | 0 | 0 | 0 | 10757 | 23963 | 29179 | 231155 | 0 |
| A_late_only | 5 | 0 | 0 | 16504 | 51647 | 111040 | 534972 | 13 |
| A_full | 7 | 0 | 0 | 18388 | 53395 | 59145 | 582389 | 14 |