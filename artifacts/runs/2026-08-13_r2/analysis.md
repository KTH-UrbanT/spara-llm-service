# Closed-loop analysis: 2026-08-13_r2

## Case-pass rate

| Arm | N | Pass | Pass rate | 1st-attempt rate |
|---|---|---|---|---|
| A_open | 88 | 47 | 0.534 | 0.534 |
| A_late_only | 88 | 47 | 0.534 | 0.547 |
| A_full | 88 | 50 | 0.568 | 0.568 |

## McNemar paired tests (Holm-corrected)

**A_open vs A_full**: n_discordant=11, n(A_full better)=7, p=0.5488, ns
**A_late_only vs A_full**: n_discordant=7, n(A_full better)=5, p=0.4531, ns

## Stratified pass rate

### A_open
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 14 | 0.412 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 31 | 0.816 |
### A_late_only
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 13 | 0.382 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 32 | 0.842 |
### A_full
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 14 | 0.412 |
| clarification | 5 | 1 | 0.200 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 35 | 0.921 |

## Per-check pass rate

| Check | A_open | A_late_only | A_full |
|---|---|---|---|
| route_match | 0.625 | 0.625 | 0.648 |
| agent_match | 0.932 | 0.932 | 0.955 |
| building_id_match | 0.818 | 0.818 | 0.818 |
| field_coverage_pass | 0.795 | 0.795 | 0.795 |
| must_include_pass | 0.761 | 0.750 | 0.761 |
| must_not_include_pass | 1.000 | 1.000 | 1.000 |
| semantic_judge_pass | 0.773 | 0.773 | 0.818 |

## Hint effectiveness

**A_late_only**: 2 retries, 2 changed (100.0%)
**A_full**: 7 retries, 6 changed (85.7%)

## Retry, latency & cost

| Arm | retried | terminated-no-pass | router-disallowed | mean ms | p95 ms | p99 ms | tokens | cost-exceeded |
|---|---|---|---|---|---|---|---|---|
| A_open | 0 | 0 | 0 | 10860 | 25188 | 32048 | 229211 | 0 |
| A_late_only | 2 | 1 | 0 | 15547 | 51813 | 58978 | 499132 | 14 |
| A_full | 7 | 0 | 0 | 18332 | 55910 | 59290 | 572465 | 14 |