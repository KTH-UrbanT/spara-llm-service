# `must_include` audit (v22 R2) — triage list, no labels touched

58 tokens over 44 cases.

## Case-level triage (worst token class per case)

| class | cases |
|---|---|
| version-slice | 1: BRF_ODEN_01_TULEGATAN_5A_001_PERFORMANCE |
| record-choice | 4: BRF_ODEN_01_TULEGATAN_5A_002_PERFORMANCE, BRF_ODEN_10_RADMANSGATAN_31_001_PERFORMANCE, BRF_ODEN_10_RADMANSGATAN_31_002_PERFORMANCE, BRF_ODEN_10_RADMANSGATAN_31_004_PERFORMANCE |
| indirect | 1: BRF_ODEN_07_TEKNIKRINGEN_37A_001_HEATING |
| single-char | 9: BRF_ODEN_02_PROFESSORSSLINGAN_51_001_PERFORMANCE, BRF_ODEN_03_TEKNIKRINGEN_10B_001_PERFORMANCE, BRF_ODEN_04_DROTTNING_KRISTINAS_VAG_43A_001_PERFORMANCE, BRF_ODEN_05_ARTEMISGATAN_17_001_PERFORMANCE, BRF_ODEN_06_TEKNIKRINGEN_78B_001_PERFORMANCE, BRF_ODEN_07_TEKNIKRINGEN_37A_001_PERFORMANCE, BRF_ODEN_08_PROFESSORSSLINGAN_49_001_PERFORMANCE, BRF_ODEN_09_PROFESSORSSLINGAN_53_001_PERFORMANCE, BRF_ODEN_10_RADMANSGATAN_31_003_PERFORMANCE |
| no-evidence | 20: BRF_ODEN_03_TEKNIKRINGEN_10B_001_HEATING, BRF_ODEN_05_ARTEMISGATAN_17_001_HEATING, BRF_ODEN_06_TEKNIKRINGEN_78B_001_HEATING, BRF_ODEN_10_RADMANSGATAN_31_003_HEATING, CLAR_001, COMB_001, DEMO_EXTRA_BRF_001, DEMO_EXTRA_BRF_002, DEMO_EXTRA_BRF_003, DEMO_EXTRA_BRF_004, DEMO_EXTRA_CLAR_001, DEMO_EXTRA_CLAR_002, DEMO_EXTRA_GEN_001, DEMO_EXTRA_GEN_002, DEMO_EXTRA_GEN_003, DEMO_EXTRA_GEN_004, DEMO_EXTRA_GEN_005, DEMO_EXTRA_GEN_006, GEN_001, GEN_002 |
| ok | 9: BRF_ODEN_01_TULEGATAN_5A_001_HEATING, BRF_ODEN_01_TULEGATAN_5A_002_HEATING, BRF_ODEN_02_PROFESSORSSLINGAN_51_001_HEATING, BRF_ODEN_04_DROTTNING_KRISTINAS_VAG_43A_001_HEATING, BRF_ODEN_08_PROFESSORSSLINGAN_49_001_HEATING, BRF_ODEN_09_PROFESSORSSLINGAN_53_001_HEATING, BRF_ODEN_10_RADMANSGATAN_31_001_HEATING, BRF_ODEN_10_RADMANSGATAN_31_002_HEATING, BRF_ODEN_10_RADMANSGATAN_31_004_HEATING |

## Every flagged token (`ok` omitted)

| case | token | class | field_coverage_pass | detail |
|---|---|---|---|---|
| GEN_001 | `heating` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| GEN_002 | `energy` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| COMB_001 | `heating` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| CLAR_001 | `address` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_01_TULEGATAN_5A_001_PERFORMANCE | `E` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_01_TULEGATAN_5A_001_PERFORMANCE | `155` | version-slice | True | absent from own retrieval (matches only inside a longer number); present in sibling BRF_ODEN_10_RADMANSGATAN_31_004_HEATING for the same building — different EPC slice |
| BRF_ODEN_01_TULEGATAN_5A_002_PERFORMANCE | `E` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_01_TULEGATAN_5A_002_PERFORMANCE | `102` | record-choice | True | present, but 2 EPC versions retrieved: ['102', '183'] — which one the answer cites is a lottery |
| BRF_ODEN_02_PROFESSORSSLINGAN_51_001_PERFORMANCE | `B` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_03_TEKNIKRINGEN_10B_001_HEATING | `district heating` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_03_TEKNIKRINGEN_10B_001_PERFORMANCE | `B` | single-char | False | satisfied by any sentence under the substring check |
| BRF_ODEN_03_TEKNIKRINGEN_10B_001_PERFORMANCE | `54` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_04_DROTTNING_KRISTINAS_VAG_43A_001_PERFORMANCE | `D` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_05_ARTEMISGATAN_17_001_HEATING | `district heating` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_05_ARTEMISGATAN_17_001_PERFORMANCE | `F` | single-char | False | satisfied by any sentence under the substring check |
| BRF_ODEN_05_ARTEMISGATAN_17_001_PERFORMANCE | `220` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_06_TEKNIKRINGEN_78B_001_HEATING | `district heating` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_06_TEKNIKRINGEN_78B_001_PERFORMANCE | `E` | single-char | False | satisfied by any sentence under the substring check |
| BRF_ODEN_06_TEKNIKRINGEN_78B_001_PERFORMANCE | `154` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_07_TEKNIKRINGEN_37A_001_HEATING | `electric` | indirect | True | absent from evidence at string level, but the answer passed the token in the reference replicate — translated/derived; label OK |
| BRF_ODEN_07_TEKNIKRINGEN_37A_001_PERFORMANCE | `G` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_08_PROFESSORSSLINGAN_49_001_PERFORMANCE | `B` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_09_PROFESSORSSLINGAN_53_001_PERFORMANCE | `B` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_10_RADMANSGATAN_31_001_PERFORMANCE | `C` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_10_RADMANSGATAN_31_001_PERFORMANCE | `75` | record-choice | True | present, but 3 EPC versions retrieved: ['109', '155', '75'] — which one the answer cites is a lottery |
| BRF_ODEN_10_RADMANSGATAN_31_002_PERFORMANCE | `D` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_10_RADMANSGATAN_31_002_PERFORMANCE | `109` | record-choice | True | present, but 3 EPC versions retrieved: ['109', '155', '75'] — which one the answer cites is a lottery |
| BRF_ODEN_10_RADMANSGATAN_31_003_HEATING | `district heating` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_10_RADMANSGATAN_31_003_PERFORMANCE | `E` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_10_RADMANSGATAN_31_003_PERFORMANCE | `183` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| BRF_ODEN_10_RADMANSGATAN_31_004_PERFORMANCE | `E` | single-char | True | satisfied by any sentence under the substring check |
| BRF_ODEN_10_RADMANSGATAN_31_004_PERFORMANCE | `155` | record-choice | True | present, but 3 EPC versions retrieved: ['109', '155', '75'] — which one the answer cites is a lottery |
| DEMO_EXTRA_GEN_001 | `electricity` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_GEN_002 | `roof` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_GEN_003 | `hot water` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_GEN_004 | `ventilation` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_GEN_005 | `temperature` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_GEN_006 | `prioritize` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_CLAR_001 | `address` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_CLAR_002 | `address` | no-evidence | True | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_BRF_001 | `220` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_BRF_002 | `electric` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_BRF_003 | `60` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
| DEMO_EXTRA_BRF_004 | `heat pump` | no-evidence | False | no cached evidence for this case (registry/vector stratum) |
