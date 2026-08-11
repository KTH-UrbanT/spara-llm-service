# Closed-loop analysis: 2026-05-29

## Case-pass rate

| Arm | N | Pass | Pass rate | 1st-attempt rate |
|---|---|---|---|---|
| A_open | 88 | 49 | 0.557 | 0.557 |
| A_late_only | 88 | 47 | 0.534 | 0.534 |
| A_full | 88 | 47 | 0.534 | 0.534 |

## McNemar paired tests (Holm-corrected)

**A_open vs A_full**: n_discordant=2, n(A_full better)=0, p=0.5000, ns
**A_late_only vs A_full**: n_discordant=0, n(A_full better)=0, p=1.0000, ns

## Stratified pass rate

### A_open
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 13 | 0.382 |
| clarification | 5 | 3 | 0.600 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 33 | 0.868 |
### A_late_only
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 12 | 0.353 |
| clarification | 5 | 3 | 0.600 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 32 | 0.842 |
### A_full
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 12 | 0.353 |
| clarification | 5 | 3 | 0.600 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 32 | 0.842 |

## Per-check pass rate

| Check | A_open | A_late_only | A_full |
|---|---|---|---|
| route_match | 0.602 | 0.602 | 0.602 |
| agent_match | 0.932 | 0.932 | 0.932 |
| building_id_match | 0.773 | 0.966 | 0.966 |
| field_coverage_pass | 0.750 | 0.750 | 0.750 |
| must_include_pass | 0.761 | 0.773 | 0.795 |
| must_not_include_pass | 1.000 | 1.000 | 1.000 |
| semantic_judge_pass | 1.000 | 1.000 | 1.000 |

## Hint effectiveness

**A_late_only**: 0 retries, 0 changed (0.0%)
**A_full**: 81 retries, 81 changed (100.0%)

## Retry, latency & cost

| Arm | retried | terminated-no-pass | router-disallowed | mean ms | p95 ms | p99 ms | tokens | cost-exceeded |
|---|---|---|---|---|---|---|---|---|
| A_open | 0 | 0 | 0 | 13925 | 33998 | 48623 | 190863 | 0 |
| A_late_only | 0 | 0 | 0 | 7681 | 20270 | 25513 | 211548 | 0 |
| A_full | 0 | 0 | 0 | 7387 | 14707 | 26886 | 212180 | 0 |