# Closed-loop analysis: 2026-08-13_r3

## Case-pass rate

| Arm | N | Pass | Pass rate | 1st-attempt rate |
|---|---|---|---|---|
| A_open | 88 | 50 | 0.568 | 0.568 |
| A_late_only | 88 | 51 | 0.580 | 0.581 |
| A_full | 88 | 53 | 0.602 | 0.603 |

## McNemar paired tests (Holm-corrected)

**A_open vs A_full**: n_discordant=5, n(A_full better)=4, p=0.3750, ns
**A_late_only vs A_full**: n_discordant=4, n(A_full better)=3, p=0.6250, ns

## Stratified pass rate

### A_open
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 16 | 0.471 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 32 | 0.842 |
### A_late_only
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 16 | 0.471 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 33 | 0.868 |
### A_full
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 15 | 0.441 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 36 | 0.947 |

## Per-check pass rate

| Check | A_open | A_late_only | A_full |
|---|---|---|---|
| route_match | 0.625 | 0.625 | 0.659 |
| agent_match | 0.932 | 0.932 | 0.966 |
| building_id_match | 0.818 | 0.818 | 0.818 |
| field_coverage_pass | 0.795 | 0.795 | 0.795 |
| must_include_pass | 0.795 | 0.795 | 0.795 |
| must_not_include_pass | 1.000 | 1.000 | 1.000 |
| semantic_judge_pass | 0.727 | 0.784 | 0.807 |

## Hint effectiveness

**A_late_only**: 2 retries, 2 changed (100.0%)
**A_full**: 10 retries, 9 changed (90.0%)

## Retry, latency & cost

| Arm | retried | terminated-no-pass | router-disallowed | mean ms | p95 ms | p99 ms | tokens | cost-exceeded |
|---|---|---|---|---|---|---|---|---|
| A_open | 0 | 0 | 0 | 10855 | 25409 | 29152 | 229522 | 0 |
| A_late_only | 2 | 0 | 0 | 15338 | 54204 | 58769 | 503155 | 14 |
| A_full | 10 | 1 | 0 | 18738 | 54785 | 61236 | 589220 | 15 |