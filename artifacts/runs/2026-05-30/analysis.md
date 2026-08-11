# Closed-loop analysis: 2026-05-30

## Case-pass rate

| Arm | N | Pass | Pass rate | 1st-attempt rate |
|---|---|---|---|---|
| A_open | 88 | 19 | 0.216 | 0.216 |
| A_late_only | 88 | 13 | 0.148 | 0.261 |
| A_full | 88 | 14 | 0.159 | 0.250 |

## McNemar paired tests (Holm-corrected)

**A_open vs A_full**: n_discordant=11, n(A_full better)=3, p=0.2266, ns
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
| building_specific | 34 | 9 | 0.265 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 2 | 0.053 |
### A_full
| Route | N | Pass | Rate |
|---|---|---|---|
| building_specific | 34 | 9 | 0.265 |
| clarification | 5 | 2 | 0.400 |
| combined | 11 | 0 | 0.000 |
| generic | 38 | 3 | 0.079 |

## Per-check pass rate

| Check | A_open | A_late_only | A_full |
|---|---|---|---|
| route_match | 0.625 | 0.625 | 0.648 |
| agent_match | 0.932 | 0.932 | 0.955 |
| building_id_match | 0.818 | 0.818 | 0.818 |
| field_coverage_pass | 0.795 | 0.795 | 0.795 |
| must_include_pass | 0.773 | 0.795 | 0.795 |
| must_not_include_pass | 1.000 | 1.000 | 1.000 |
| semantic_judge_pass | 0.580 | 0.568 | 0.580 |

## Hint effectiveness

**A_late_only**: 42 retries, 42 changed (100.0%)
**A_full**: 46 retries, 44 changed (95.7%)

## Retry, latency & cost

| Arm | retried | terminated-no-pass | router-disallowed | mean ms | p95 ms | p99 ms | tokens | cost-exceeded |
|---|---|---|---|---|---|---|---|---|
| A_open | 0 | 0 | 0 | 13422 | 29458 | 46896 | 231517 | 0 |
| A_late_only | 42 | 38 | 0 | 20304 | 52324 | 56727 | 652118 | 14 |
| A_full | 44 | 37 | 0 | 22033 | 53596 | 59042 | 694095 | 12 |