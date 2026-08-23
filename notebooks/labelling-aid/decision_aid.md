# Primary labelling decision aid - `2026-05-14_v2`

Per-question reference material. **Open this alongside the labelling UI**, look up the question by `qid` (the labeller sees both `output_id` and `qid` - only `arm` is hidden).

Contents of each section:
- **Question + category + difficulty**
- **Gold facts / numerics / must_avoid** (what a perfect answer would and would not contain)
- **Cache anchors** - the key EPC fields and their values, with `epc_egiforstaarmanad`/`epc_egisistaarmanad` already YYMM-decoded so you can tell at a glance whether a date in an answer is correctly interpreted
- **Watchlist** - specific phrases or numbers that, if present in the response, indicate a trouble pattern
- **Calibration cue** - a one-line nudge on how to score this question shape

This file contains **NO arm identification and NO scores**. Your labels are still yours. The intent is to spare you the cache-JSON archaeology, not to anchor your judgment.

---

## Q001 - `simple_address` / *easy*

**Question:** What is the heated area (Atemp) of the building at Loviselundsvägen 81?

**`hint_address`:** `Loviselundsvägen 81`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - heated area (Atemp) is 270 m²
  - the building is at Loviselundsvägen 81

**Gold expected_numeric:** `{'atemp_m2': 270}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-BERBERISEN7-1` | building identifier |
| `epc_egennybyggar` | **1947** | actual construction year |
| `epc_egenatemp` | **270** | heated area Atemp (m²) |
| `epc_egenantalplan` | 2 | above-ground floors |
| `epc_egenbyggnadstyp` | `Friliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `C` | energy class |
| `epc_egiforstaarmanad` | **1702** = **Feb 2017** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **1801** = **Jan 2018** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Any numeric Atemp value that is NOT 270 m² → mismatch (drop NF or G)
  - Multi-bullet dump of unsolicited fields → `irrelevant_extras` tag

**Calibration cue:** Simple lookup question. If the answer states the correct Atemp and stays on topic, 4/4/4/4 with no tags is the right call.

---

## Q002 - `simple_address` / *easy*

**Question:** What year was the building at Astrakangatan 180 built?

**`hint_address`:** `Astrakangatan 180`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building at Astrakangatan 180 was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-AVKOPPLINGEN4-1` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **108** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egiforstaarmanad` | **0606** = **Jun 2006** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **0705** = **May 2007** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Any construction year that is NOT 1957 → mismatch
  - Answer that quotes `epc_egiforstaarmanad` or its decoded YYMM as "year built" → **hallucination**, drop G
  - Energy class / floors / type dumped without being asked → `irrelevant_extras`

**Calibration cue:** Year question. Correct value is in `epc_egennybyggar`. The YYMM fields are a trap; if the answer quotes them as years, score honestly.

---

## Q003 - `simple_address` / *easy*

**Question:** What is the heated area (Atemp) of the building at Friherregatan 111?

**`hint_address`:** `Friherregatan 111`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - heated area (Atemp) is 132 m²

**Gold expected_numeric:** `{'atemp_m2': 132}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-KLAETTEN14-1` | building identifier |
| `epc_egennybyggar` | **1960** | actual construction year |
| `epc_egenatemp` | **132** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egiforstaarmanad` | **0712** = **Dec 2007** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **0811** = **Nov 2008** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Any numeric Atemp value that is NOT 132 m² → mismatch (drop NF or G)
  - Multi-bullet dump of unsolicited fields → `irrelevant_extras` tag

**Calibration cue:** Simple lookup question. If the answer states the correct Atemp and stays on topic, 4/4/4/4 with no tags is the right call.

---

## Q004 - `simple_address` / *medium*

**Question:** How many floors does the building at Förridargränd 5 have?

**`hint_address`:** `Förridargränd 5`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building has 2 floors above ground

**Gold expected_numeric:** `{'floors_above_ground': 2}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-AVSKILDHETEN3-1` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **117** | heated area Atemp (m²) |
| `epc_egenantalplan` | 2 | above-ground floors |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `E` | energy class |
| `epc_egiforstaarmanad` | **1901** = **Jan 2019** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **1912** = **Dec 2019** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Floors above ground = 2; any other number is a mismatch

**Calibration cue:** Floors lookup. 4/4/4/4 if correct.

---

## Q005 - `simple_address` / *easy*

**Question:** What is the building type (epc_egenbyggnadstyp) of the building at Friherregatan 138?

**`hint_address`:** `Friherregatan 138`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building at Friherregatan 138 is of type Gavel

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-BLOMLISTEN1-1` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **113** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Gavel` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `G` | energy class |
| `epc_egiforstaarmanad` | **2001** = **Jan 2020** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **2012** = **Dec 2020** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Type/category claim that does NOT match the cache value → mismatch (drop G)
  - For Q006: BOTH category AND year required for completeness; missing either → C=3

**Calibration cue:** Two-fact or string-field question. Watch for hedging vs concrete claim.

---

## Q006 - `simple_address` / *medium*

**Question:** What is the building category (epc_egenbyggnadskat) of Bruntegatan 5, and what year was it built?

**`hint_address`:** `Bruntegatan 5`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building at Bruntegatan 5 is in the category En- och tvåbostadshus
  - the building was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-BLOMPRAKTEN4-` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **115** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egiforstaarmanad` | **0802** = **Feb 2008** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **0901** = **Jan 2009** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Type/category claim that does NOT match the cache value → mismatch (drop G)
  - For Q006: BOTH category AND year required for completeness; missing either → C=3

**Calibration cue:** Two-fact or string-field question. Watch for hedging vs concrete claim.

---

## Q007 - `simple_address` / *medium*

**Question:** What is the heated area of the building at Förridargränd 9?

**`hint_address`:** `Förridargränd 9`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - heated area (Atemp) is 95 m²

**Gold expected_numeric:** `{'atemp_m2': 95}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-AVSKILDHETEN5-1` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **95** | heated area Atemp (m²) |
| `epc_egenantalplan` | 2 | above-ground floors |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `F` | energy class |
| `epc_egiforstaarmanad` | **2004** = **Apr 2020** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **2103** = **Mar 2021** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Any numeric Atemp value that is NOT 95 m² → mismatch (drop NF or G)
  - Multi-bullet dump of unsolicited fields → `irrelevant_extras` tag

**Calibration cue:** Simple lookup question. If the answer states the correct Atemp and stays on topic, 4/4/4/4 with no tags is the right call.

---

## Q008 - `simple_address` / *easy*

**Question:** When was the building at Kurirgatan 16 built?

**`hint_address`:** `Kurirgatan 16`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-BAECKSLINGAN3-0` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **119** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egiforstaarmanad` | **0905** = **May 2009** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **1004** = **Apr 2010** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Any construction year that is NOT 1957 → mismatch
  - Answer that quotes `epc_egiforstaarmanad` or its decoded YYMM as "year built" → **hallucination**, drop G
  - Energy class / floors / type dumped without being asked → `irrelevant_extras`

**Calibration cue:** Year question. Correct value is in `epc_egennybyggar`. The YYMM fields are a trap; if the answer quotes them as years, score honestly.

---

## Q009 - `simple_address` / *easy*

**Question:** What is the heated area (Atemp) of the building at Kurirgatan 11?

**`hint_address`:** `Kurirgatan 11`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - heated area (Atemp) is 94 m²

**Gold expected_numeric:** `{'atemp_m2': 94}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-BAECKSLINGAN12-1` | building identifier |
| `epc_egennybyggar` | **1958** | actual construction year |
| `epc_egenatemp` | **94** | heated area Atemp (m²) |
| `epc_egenantalplan` | 2 | above-ground floors |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egiforstaarmanad` | **1104** = **Apr 2011** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **1203** = **Mar 2012** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Any numeric Atemp value that is NOT 94 m² → mismatch (drop NF or G)
  - Multi-bullet dump of unsolicited fields → `irrelevant_extras` tag

**Calibration cue:** Simple lookup question. If the answer states the correct Atemp and stays on topic, 4/4/4/4 with no tags is the right call.

---

## Q010 - `simple_address` / *easy*

**Question:** What year was the building at Förridargränd 11 built?

**`hint_address`:** `Förridargränd 11`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building at Förridargränd 11 was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-AVSKILDHETEN6-1` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **117** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `F` | energy class |
| `epc_egiforstaarmanad` | **1701** = **Jan 2017** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **1712** = **Dec 2017** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Any construction year that is NOT 1957 → mismatch
  - Answer that quotes `epc_egiforstaarmanad` or its decoded YYMM as "year built" → **hallucination**, drop G
  - Energy class / floors / type dumped without being asked → `irrelevant_extras`

**Calibration cue:** Year question. Correct value is in `epc_egennybyggar`. The YYMM fields are a trap; if the answer quotes them as years, score honestly.

---

## Q011 - `edge_case` / *hard*

**Question:** What construction material (brick, wood, concrete) is the building at Loviselundsvägen 81 made of?

**`hint_address`:** `Loviselundsvägen 81`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - indicate that construction material is not available in the EPC data
  - do not fabricate a material claim

**Gold expected_constraints:**
  - `must not state a specific construction material as fact`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - fabricated 'brick' or 'wood' or 'concrete' claim
  - guessing based on construction year

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-BERBERISEN7-1` | building identifier |
| `epc_egennybyggar` | **1947** | actual construction year |
| `epc_egenatemp` | **270** | heated area Atemp (m²) |
| `epc_egenantalplan` | 2 | above-ground floors |
| `epc_egenbyggnadstyp` | `Friliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `C` | energy class |
| `epc_egiforstaarmanad` | **1702** = **Feb 2017** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **1801** = **Jan 2018** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Any specific material (brick / wood / concrete) claimed → **fabrication**, drop G + add `unsupported_claim`
  - Answer that simply notes the field is absent → correct refusal, 4/4/4/4

**Calibration cue:** `missing_data` edge case: no field for material exists. Correct = refuse. Specific material = hallucination.

---

## Q012 - `edge_case` / *hard*

**Question:** When was the building at Astrakangatan 180 last renovated?

**`hint_address`:** `Astrakangatan 180`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - indicate that renovation history is not available in the EPC data
  - do not fabricate a renovation date

**Gold expected_constraints:**
  - `must not state a specific renovation year as fact`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - fabricated renovation year
  - treating epc_egennybyggar (construction year) as a renovation year

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-AVKOPPLINGEN4-1` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **108** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egiforstaarmanad` | **0606** = **Jun 2006** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **0705** = **May 2007** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - "renovated in 2008" or any single renovation year → **YYMM hallucination** (epc_egisistaarmanad=2008 means Aug 2020 of measurement period, NOT a year)
  - Mention of "approved YYYY-MM-DD" without that exact date being in the response's shown evidence → check if it's fabricated
  - Discussion of "EPC measurement period" or "energy monitoring interval" → grounded, OK to mention

**Calibration cue:** CRITICAL CASE: this is the YYMM trap. A correct refusal explains there's no renovation field. Any specific renovation year = fabrication.

---

## Q013 - `constraint_filtering` / *medium*

**Question:** As of January 2026, is the building at Förridargränd 5 less than 75 years old?

**`hint_address`:** `Förridargränd 5`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building was built in 1957
  - as of January 2026 the building is 69 years old
  - answer yes, the building is less than 75 years old

**Gold expected_numeric:** `{'year_built': 1957}`

**Gold expected_constraints:**
  - `(2026 - 1957) < 75`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - answering no
  - claiming the building is 75 years old or older
  - computing age relative to a year other than 2026

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-AVSKILDHETEN3-1` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **117** | heated area Atemp (m²) |
| `epc_egenantalplan` | 2 | above-ground floors |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `E` | energy class |
| `epc_egiforstaarmanad` | **1901** = **Jan 2019** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **1912** = **Dec 2019** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Answer that quotes 1957 → CORRECT (epc_egennybyggar)
  - **Answer that quotes 1901 or "approximately 125 years old"** → ⚠️ **YYMM hallucination** (reads epc_egiforstaarmanad=1901 as a year; it's actually Jan 2019)
  - Answer "no" + claims building older than 75 → wrong

**Calibration cue:** CRITICAL CASE: the agent should read 1957 from `epc_egennybyggar`. If it quotes 1901 or claims ~125 years old, that's reading the YYMM field as a year - drop G aggressively (≤2) and tag `unsupported_claim` + flip overall_pass to 0.

---

## Q014 - `edge_case` / *easy*

**Question:** What is the heated area of the building at Nonexistentgatan 999?

**`hint_address`:** `Nonexistentgatan 999`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - indicate that no building was found at the given address
  - do not fabricate any numeric heated area value

**Gold expected_constraints:**
  - `must not return any numeric Atemp`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - fabricated heated area number
  - treating the address as real

**Watchlist:**
  - Any numeric Atemp value at all → **fabrication** (no record exists for Nonexistentgatan 999)
  - Refusal that says "no record found" → correct, 4/4/4/4

**Calibration cue:** `unknown_address` edge case: there is no building. Correct = refuse. Any number = fabrication.

---

## Q015 - `edge_case` / *easy*

**Question:** What is the heated area?

**`hint_address`:** `None`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - ask for an address or clarification rather than answering

**Gold expected_constraints:**
  - `must not return any numeric Atemp without an address being specified`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - fabricated heated area for an unspecified building
  - picking an arbitrary building

**Watchlist:**
  - "please provide the address" → correct short-circuit
  - Any building-specific data → impossible (no address was given)

**Calibration cue:** Short-circuits before the summarizer. A1 and A2 produce the same response by construction; these contribute zero to the paired delta.

---

## Q016 - `edge_case` / *hard*

**Question:** What is the average rent for the apartments in the building at Loviselundsvägen 81?

**`hint_address`:** `Loviselundsvägen 81`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - indicate that rent data is not available in the EPC registry
  - do not fabricate a numeric rent value
  - may optionally note that rent figures would come from a different data source than EPC

**Gold expected_constraints:**
  - `must not return a numeric rent figure`
  - `must not invent rent in SEK/m² or SEK/month`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - fabricated rent in SEK
  - treating rent as an EPC field
  - guessing a Stockholm-average rent and applying it to this building

**Watchlist:**
  - Any numeric rent figure (SEK/month, SEK/m²) → **fabrication**
  - Refusal that explains rent isn't in EPC → correct
  - Refusal that just asks for an address → incomplete (the address IS provided)

**Calibration cue:** Out-of-scope question with a valid address. Correct = retrieve EPC + recognise rent is not represented + refuse cleanly with helpful pointers.

---

## Q017 - `edge_case` / *medium*

**Question:** What is the heated area of the building at Loviselundsvägen?

**`hint_address`:** `Loviselundsvägen`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - indicate that the address is incomplete (no street number)
  - either ask for the full address or return multiple matches if found

**Gold expected_constraints:**
  - `must not return a single Atemp value without disambiguating the building`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - silently picking one of several matches and reporting its Atemp as the answer

**Watchlist:**
  - Any specific Atemp value for "Loviselundsvägen" → fabrication (street name alone is ambiguous)
  - Asks for full address or admits ambiguity → correct refusal

**Calibration cue:** `missing_data` edge case: street alone returns 0 rows from ODEN tiered fallback. Correct = ask for full address.

---

## Q018 - `constraint_filtering` / *medium*

**Question:** Was the building at Loviselundsvägen 81 built before 1970?

**`hint_address`:** `Loviselundsvägen 81`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building was built in 1947
  - answer yes, the building was built before 1970

**Gold expected_numeric:** `{'year_built': 1947}`

**Gold expected_constraints:**
  - `year_built < 1970`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - answering no without justification
  - claiming the building was built after 1970

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-BERBERISEN7-1` | building identifier |
| `epc_egennybyggar` | **1947** | actual construction year |
| `epc_egenatemp` | **270** | heated area Atemp (m²) |
| `epc_egenantalplan` | 2 | above-ground floors |
| `epc_egenbyggnadstyp` | `Friliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `C` | energy class |
| `epc_egiforstaarmanad` | **1702** = **Feb 2017** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **1801** = **Jan 2018** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Answer = "yes, built 1947" → correct
  - Answer = "no" or year ≠ 1947 → wrong
  - Multi-bullet dump of Atemp + floors + class etc. → `irrelevant_extras`

**Calibration cue:** Constraint check on a clean field. 4/4/4/4 if correctly answered "yes"; tag for any volunteered facts.

---

## Q019 - `constraint_filtering` / *medium*

**Question:** Is the building at Astrakangatan 180 a detached (Friliggande) house?

**`hint_address`:** `Astrakangatan 180`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - the building type (epc_egenbyggnadstyp) is Mellanliggande, not Friliggande
  - answer no, the building is not detached

**Gold expected_constraints:**
  - `byggnadstyp == 'Friliggande' must evaluate to false`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - answering yes
  - claiming the building type is Friliggande

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-AVKOPPLINGEN4-1` | building identifier |
| `epc_egennybyggar` | **1957** | actual construction year |
| `epc_egenatemp` | **108** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egiforstaarmanad` | **0606** = **Jun 2006** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **0705** = **May 2007** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Answer = "no, the building is Mellanliggande, not Friliggande" → correct
  - Answer = "yes" or claims Friliggande → wrong

**Calibration cue:** Type comparison. The cache has `epc_egenbyggnadstyp=Mellanliggande`. Correct answer is `no`.

---

## Q020 - `constraint_filtering` / *medium*

**Question:** Is the heated area of the building at Friherregatan 132 larger than 200 m²?

**`hint_address`:** `Friherregatan 132`

**Gold expected_facts** (a good answer SHOULD say each of these, or equivalent):
  - heated area (Atemp) is 152 m²
  - answer no, 152 m² is not larger than 200 m²

**Gold expected_numeric:** `{'atemp_m2': 152}`

**Gold expected_constraints:**
  - `atemp_m2 > 200 must evaluate to false`

**Gold `must_avoid`** (a good answer MUST NOT do any of these):
  - answering yes
  - claiming heated area exceeds 200 m²

**Cache anchor fields** (what the agent saw):

| Field | Value | Notes |
|---|---|---|
| `byggnadsid` | `01-80-BLOMLISTEN4-1` | building identifier |
| `epc_egennybyggar` | **1958** | actual construction year |
| `epc_egenatemp` | **152** | heated area Atemp (m²) |
| `epc_egenbyggnadstyp` | `Mellanliggande` | building type |
| `epc_egenbyggnadskat` | `En- och tvåbostadshus` | building category |
| `epc_egienergiklass` | `E` | energy class |
| `epc_egiforstaarmanad` | **2108** = **Aug 2021** (YYMM) | EPC measurement-period START - **NOT a construction year** |
| `epc_egisistaarmanad` | **2207** = **Jul 2022** (YYMM) | EPC measurement-period END - **NOT a renovation year** |

**Watchlist:**
  - Answer = "no, 152 m² is not > 200" → correct (cache atemp=152)
  - Any other Atemp number → mismatch

**Calibration cue:** Numeric threshold. 4/4/4/4 if correctly answered "no" with 152 m² as the cache value.

---

## General reminders

### When to drop a score below 4
- **groundedness < 4**: a claim in the answer is NOT supported by the cache shown to you. Use `unsupported_claim` tag.
- **completeness < 4**: the answer fails to address a part of the question (or refuses without explaining why).
- **numeric_fidelity < 4**: a specific number in the answer disagrees with the cache. Use `numeric_mismatch` tag.
- **constraint_satisfaction < 4**: a `must_avoid` is violated, or `expected_constraints` is broken.

### When to apply a failure tag *without* dropping a score
- `irrelevant_extras`: the answer is correct AND fully grounded, but volunteered information you did not ask for. Tag without penalising axes (your rule from the smoke rehearsal: "minimal extras → no tag; substantial dump → tag").
- `overconfident_uncertainty`: the answer hedges where it shouldn't (or vice versa).
- `partial_answer`: question had two parts; only one was addressed.

### What makes a "perfect 4/4/4/4 with overall_pass=1"
Every claim in the answer is in the cache, every part of the question is addressed, no unsolicited dumps, no fabricated numbers. About 12-15 of the 40 outputs should land here on a healthy distribution.

### What makes a 0 overall_pass
A claim that a domain expert would call **wrong** (not just suboptimal). Examples: claiming a renovation year that doesn't exist; reading a YYMM as a year; fabricating an Atemp for a nonexistent address; misidentifying the building type.

### A note on Q015
Q015 has no address. Both arms produce the identical "Can you please provide the building address?" response by construction. If you score both at exactly the same level, the paired delta on this question is 0 - that is mechanically correct and not a labelling failure.

### A note on Q012 + Q013 (the case studies)
These are the two questions where the YYMM-vs-year trap matters most. **Look at every date in the answer and ask: "is this a 4-digit year, or is it a YYMM that the agent misread?"**
  - `1909`, `2008`, `0606`, `0905` etc. as "years" → ALWAYS WRONG (they're YYMM codes).
  - The actual construction year for every building in this dataset is in `epc_egennybyggar` (1947, 1957, 1958, or 1960).
