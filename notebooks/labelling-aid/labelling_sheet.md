# Primary labelling sheet - `2026-05-14_v2`

**40 outputs, shuffled (seed=42, same order `label_outputs.py` would show).** Arm identity is hidden - you only see the `qid` and the response. Cross-reference [decision_aid.md](decision_aid.md) for gold facts + cache anchors + watchlists per qid.

## How to fill this in

Each output has a **Scores** block at the bottom:
```
G: 
C: 
NF: 
P: 
TAGS: 
NOTES: 
```

Fill values after the `: `. The parser is strict on this format.

| Field | Type | Notes |
|---|---|---|
| `G`  | int 0-4 | groundedness |
| `C`  | int 0-4 | completeness |
| `NF` | int 0-4 | numeric_fidelity |
| `CS` | int 0-4 | constraint_satisfaction |
| `P`  | int 0/1 | overall_pass |
| `TAGS` | comma-separated | one or more of: `unsupported_claim`, `numeric_mismatch`, `constraint_violation`, `partial_answer`, `overconfident_uncertainty`, `irrelevant_extras`. Leave blank if none. |
| `NOTES` | free text | one line |

**VS Code tip:** open this file, then use **multi-cursor** (`Cmd+Opt+↓` to add cursors below) on the empty space after `G:` across all sections - type all 40 G values in one pass, then move to C, etc. Should take you ~15-20 min total instead of ~90 min in the terminal.

**Blinding note:** the arm (A1/A2) is intentionally hidden. Score each output on its own merits without trying to guess which arm produced it.

**When done:** tell me, and I will parse this back to `human_labels.csv` in the proper 12-column format and run the analysis.

---

## 01 · `e2d2913c`  ·  Q010  ·  *simple_address / easy*

**Question:** What year was the building at Förridargränd 11 built?

**Gold expected_facts:**
  - the building at Förridargränd 11 was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchors:**
  - byggnadsid=`01-80-AVSKILDHETEN6-1`
  - **nybyggar=1957** (real year built)
  - **atemp=117** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`F`
  - forsta=`1701` = **Jan 2017** (YYMM, NOT a year)
  - sista=`1712` = **Dec 2017** (YYMM, NOT a year)

**Response:**

> The energy performance certificate (EPC) for Förridargränd 11 shows an “egennybyggar” value of 1957. In this context, that indicates the building was constructed in 1957.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly identifies the construction year as 1957 from the nybyggar field and answers the question directly without introducing unsupported claims.
```

---

## 02 · `a27fbacd`  ·  Q004  ·  *simple_address / medium*

**Question:** How many floors does the building at Förridargränd 5 have?

**Gold expected_facts:**
  - the building has 2 floors above ground

**Gold expected_numeric:** `{'floors_above_ground': 2}`

**Cache anchors:**
  - byggnadsid=`01-80-AVSKILDHETEN3-1`
  - **nybyggar=1957** (real year built)
  - **atemp=117** m²
  - plan=2
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`E`
  - forsta=`1901` = **Jan 2019** (YYMM, NOT a year)
  - sista=`1912` = **Dec 2019** (YYMM, NOT a year)

**Response:**

> According to the building’s energy performance certificate for Förridargränd 5:
> 
> • Number of above-ground floors (epc_egenantalplan): 2  
> • Number of basement (cellar) levels (epc_egenantalkallarplan): 1  
> 
> So, it is a two-storey building above ground with one additional cellar level.

**Scores:**
```
G: 3
C: 4
NF: 4
CS: 3
P: 1
TAGS: irrelevant_extras
NOTES: Correctly answers that the building has 2 above-ground floors, but introduces an unsupported claim about 1 basement level not present in the provided evidence.
```

---

## 03 · `8014f921`  ·  Q011  ·  *edge_case / hard*

**Question:** What construction material (brick, wood, concrete) is the building at Loviselundsvägen 81 made of?

**Gold expected_facts:**
  - indicate that construction material is not available in the EPC data
  - do not fabricate a material claim

**Gold must_avoid:**
  - fabricated 'brick' or 'wood' or 'concrete' claim
  - guessing based on construction year

**Cache anchors:**
  - byggnadsid=`01-80-BERBERISEN7-1`
  - **nybyggar=1947** (real year built)
  - **atemp=270** m²
  - plan=2
  - byggnadstyp=`Friliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`C`
  - forsta=`1702` = **Feb 2017** (YYMM, NOT a year)
  - sista=`1801` = **Jan 2018** (YYMM, NOT a year)

**Response:**

> The energy-performance data we have for Loviselundsvägen 81 (EPC ID 01-80-BERBERISEN7-1) is very detailed on energy use, heated floor area, year of construction (1947), number of floors (2 above ground, 1 basement), energy class (C), main heating system (ground-source heat pump), etc., but it does not record the primary load-bearing or external wall material. In other words, the EPC record does not tell us whether the house is of brick, wood or concrete.  
> 
> If you need the official construction material you will have to consult:  
> • The national building register at Boverket/Lantmäteriet (byggnadsinformationsdatabasen)  
> • The original building permit or drawings held by Stockholm municipality  
> • A site inspection or archival survey report that specifically records structural details.

**Scores:**
```
G: 3
C: 4
NF: 4
CS: 4
P: 1
TAGS: irrelevant_extras
NOTES: Correctly states that the EPC data does not contain construction material information and avoids fabrication, but includes unsupported extra details about basement levels and heating systems.
```

---

## 04 · `f5a1b740`  ·  Q017  ·  *edge_case / medium*

**Question:** What is the heated area of the building at Loviselundsvägen?

**Gold expected_facts:**
  - indicate that the address is incomplete (no street number)
  - either ask for the full address or return multiple matches if found

**Gold must_avoid:**
  - silently picking one of several matches and reporting its Atemp as the answer

**Cache:** *(empty - edge case)*

**Response:**

> I’m sorry, but I don’t have any EPC data for the building at Loviselundsvägen in the information provided. To determine the heated floor area (Atemp), please share the building’s EPC record or its unique identifier (byggnadsid, EPC-idadr or UUID), and I’ll be happy to look up the heated area for you.

**Scores:**
```
G: 4
C: 1
NF: 4
CS: 4
P: 1
TAGS: partial_answer
NOTES: Correctly avoids fabricating or selecting a building without a full address, but does not explicitly explain that the address is incomplete or ambiguous.
```

---

## 05 · `0a76bdef`  ·  Q006  ·  *simple_address / medium*

**Question:** What is the building category (epc_egenbyggnadskat) of Bruntegatan 5, and what year was it built?

**Gold expected_facts:**
  - the building at Bruntegatan 5 is in the category En- och tvåbostadshus
  - the building was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchors:**
  - byggnadsid=`01-80-BLOMPRAKTEN4-`
  - **nybyggar=1957** (real year built)
  - **atemp=115** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0802` = **Feb 2008** (YYMM, NOT a year)
  - sista=`0901` = **Jan 2009** (YYMM, NOT a year)

**Response:**

> The energy performance certificate for Bruntegatan 5 lists:
> 
> • Building category (epc_egenbyggnadskat): “En- och tvåbostadshus” (one- and two-dwelling house)  
> • Year of construction (epc_egennybyggar): 1957

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly reports both the building category and construction year directly from the EPC fields without adding unsupported information.
```

---

## 06 · `1022642b`  ·  Q005  ·  *simple_address / easy*

**Question:** What is the building type (epc_egenbyggnadstyp) of the building at Friherregatan 138?

**Gold expected_facts:**
  - the building at Friherregatan 138 is of type Gavel

**Cache anchors:**
  - byggnadsid=`01-80-BLOMLISTEN1-1`
  - **nybyggar=1957** (real year built)
  - **atemp=113** m²
  - byggnadstyp=`Gavel`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`G`
  - forsta=`2001` = **Jan 2020** (YYMM, NOT a year)
  - sista=`2012` = **Dec 2020** (YYMM, NOT a year)

**Response:**

> The energy performance certificate (EPC) for Friherregatan 138 classifies the building under the “En- och tvåbostadshus” category with an own building type (epc_egenbyggnadstyp) of “Gavel.”

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly identifies the building type as Gavel and remains fully consistent with the EPC cache data.
```

---

## 07 · `4486760d`  ·  Q011  ·  *edge_case / hard*

**Question:** What construction material (brick, wood, concrete) is the building at Loviselundsvägen 81 made of?

**Gold expected_facts:**
  - indicate that construction material is not available in the EPC data
  - do not fabricate a material claim

**Gold must_avoid:**
  - fabricated 'brick' or 'wood' or 'concrete' claim
  - guessing based on construction year

**Cache anchors:**
  - byggnadsid=`01-80-BERBERISEN7-1`
  - **nybyggar=1947** (real year built)
  - **atemp=270** m²
  - plan=2
  - byggnadstyp=`Friliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`C`
  - forsta=`1702` = **Feb 2017** (YYMM, NOT a year)
  - sista=`1801` = **Jan 2018** (YYMM, NOT a year)

**Response:**

> I’m sorry, but the provided energy‐performance certificate (EPC) data for Loviselundsvägen 81 does not include any field describing the building’s primary construction material (brick, wood or concrete). The EPC focuses on energy use, thermal characteristics and technical installations, but does not record façade or structural material.  
> 
> To find the construction material you would need to consult one of the following sources:  
> • The original building permit or architectural drawings (from the local municipality)  
> • The detailed property description in Lantmäteriet’s building register  
> • A more comprehensive building survey or structural report  
> 
> Without one of those documents, we cannot determine the material from the EPC data alone.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly states that construction material is not available in the EPC data and avoids fabricating or inferring a material type.
```

---

## 08 · `44434f9d`  ·  Q012  ·  *edge_case / hard*

**Question:** When was the building at Astrakangatan 180 last renovated?

**Gold expected_facts:**
  - indicate that renovation history is not available in the EPC data
  - do not fabricate a renovation date

**Gold must_avoid:**
  - fabricated renovation year
  - treating epc_egennybyggar (construction year) as a renovation year

**Cache anchors:**
  - byggnadsid=`01-80-AVKOPPLINGEN4-1`
  - **nybyggar=1957** (real year built)
  - **atemp=108** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0606` = **Jun 2006** (YYMM, NOT a year)
  - sista=`0705` = **May 2007** (YYMM, NOT a year)

**Response:**

> According to the latest Energy Performance Certificate (EPC) for Astrakangatan 180, the building was most recently renovated in 2008 (EPC field epc_egisistaarmanad).

**Scores:**
```
G: 1
C: 1
NF: 0
CS: 0
P: 0
TAGS: unsupported_claim, numeric_mismatch
NOTES: The response fabricates a renovation year (2008) that is not present in the cache or allowed EPC fields. This directly violates the instruction to avoid inferring renovation history and incorrectly treats a non-provided field as evidence.
```

---

## 09 · `79b8b47d`  ·  Q005  ·  *simple_address / easy*

**Question:** What is the building type (epc_egenbyggnadstyp) of the building at Friherregatan 138?

**Gold expected_facts:**
  - the building at Friherregatan 138 is of type Gavel

**Cache anchors:**
  - byggnadsid=`01-80-BLOMLISTEN1-1`
  - **nybyggar=1957** (real year built)
  - **atemp=113** m²
  - byggnadstyp=`Gavel`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`G`
  - forsta=`2001` = **Jan 2020** (YYMM, NOT a year)
  - sista=`2012` = **Dec 2020** (YYMM, NOT a year)

**Response:**

> The Energy Performance Certificate (EPC) for the property at Friherregatan 138 lists the building type (epc_egenbyggnadstyp) as “Gavel.” This indicates it is an end-terrace unit within the broader category of single- or two-dwelling houses (epc_egenbyggnadskat = “En- och tvåbostadshus”).

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly extracts the building type “Gavel” directly from the cache anchors and provides a fully supported answer without introducing unsupported or fabricated information.
```

---

## 10 · `1ecd4e98`  ·  Q013  ·  *constraint_filtering / medium*

**Question:** As of January 2026, is the building at Förridargränd 5 less than 75 years old?

**Gold expected_facts:**
  - the building was built in 1957
  - as of January 2026 the building is 69 years old
  - answer yes, the building is less than 75 years old

**Gold expected_numeric:** `{'year_built': 1957}`

**Gold must_avoid:**
  - answering no
  - claiming the building is 75 years old or older
  - computing age relative to a year other than 2026

**Cache anchors:**
  - byggnadsid=`01-80-AVSKILDHETEN3-1`
  - **nybyggar=1957** (real year built)
  - **atemp=117** m²
  - plan=2
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`E`
  - forsta=`1901` = **Jan 2019** (YYMM, NOT a year)
  - sista=`1912` = **Dec 2019** (YYMM, NOT a year)

**Response:**

> According to the building’s Energy Performance Certificate (EPC), the first year of occupancy (epc_egiforstaarmanad) for the property at Förridargränd 5 is recorded as 1901. By January 2026 the building will therefore be approximately 125 years old - well in excess of 75 years.

**Scores:**
```
G: 0
C: 0
NF: 0
CS: 0
P: 0
TAGS: unsupported_claim, numeric_mismatch, constraint_violation
NOTES: Completely incorrect interpretation of EPC fields; uses forsta (1901) as if it were a construction year and fabricates an age (~125 years), contradicting the true construction year (1957) and all constraints about correct temporal reasoning.
```

---

## 11 · `c6f4403c`  ·  Q002  ·  *simple_address / easy*

**Question:** What year was the building at Astrakangatan 180 built?

**Gold expected_facts:**
  - the building at Astrakangatan 180 was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchors:**
  - byggnadsid=`01-80-AVKOPPLINGEN4-1`
  - **nybyggar=1957** (real year built)
  - **atemp=108** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0606` = **Jun 2006** (YYMM, NOT a year)
  - sista=`0705` = **May 2007** (YYMM, NOT a year)

**Response:**

> The energy-declaration records for Astrakangatan 180 both report the original construction year as 1957. In other words, this single-family house (mellanliggande småhus) in Hässelby was built in 1957.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly identifies the construction year as 1957, fully grounded in the cache anchor nybyggar=1957, with no unsupported claims or misinterpretation of unrelated fields.
```

---

## 12 · `602df544`  ·  Q009  ·  *simple_address / easy*

**Question:** What is the heated area (Atemp) of the building at Kurirgatan 11?

**Gold expected_facts:**
  - heated area (Atemp) is 94 m²

**Gold expected_numeric:** `{'atemp_m2': 94}`

**Cache anchors:**
  - byggnadsid=`01-80-BAECKSLINGAN12-1`
  - **nybyggar=1958** (real year built)
  - **atemp=94** m²
  - plan=2
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`1104` = **Apr 2011** (YYMM, NOT a year)
  - sista=`1203` = **Mar 2012** (YYMM, NOT a year)

**Response:**

> The Energy Performance Certificate for the building at Kurirgatan 11 reports a heated floor area (Atemp) of 94 m².

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly extracts Atemp = 94 m² directly from the cache anchors without adding or inferring any unsupported information.
```

---

## 13 · `3a4efa9f`  ·  Q004  ·  *simple_address / medium*

**Question:** How many floors does the building at Förridargränd 5 have?

**Gold expected_facts:**
  - the building has 2 floors above ground

**Gold expected_numeric:** `{'floors_above_ground': 2}`

**Cache anchors:**
  - byggnadsid=`01-80-AVSKILDHETEN3-1`
  - **nybyggar=1957** (real year built)
  - **atemp=117** m²
  - plan=2
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`E`
  - forsta=`1901` = **Jan 2019** (YYMM, NOT a year)
  - sista=`1912` = **Dec 2019** (YYMM, NOT a year)

**Response:**

> According to the latest energy performance certificate for Förridargränd 5 (Building ID 01-80-AVSKILDHETEN3-1):
> 
> • Number of above-ground floors (epc_egenantalplan): 2  
> • Number of basement levels (epc_egenantalkallarplan): 1  
> 
> In other words, the house has two main storeys over ground plus one cellar level.

**Scores:**
```
G: 3
C: 3
NF: 4
CS: 4
P: 1
TAGS: irrelevant_extras
NOTES: Correctly identifies 2 above-ground floors from plan=2, but adds unsupported basement/cellar level not present in cache anchors or gold data, introducing extra ungrounded information.
```

---

## 14 · `657b083c`  ·  Q019  ·  *constraint_filtering / medium*

**Question:** Is the building at Astrakangatan 180 a detached (Friliggande) house?

**Gold expected_facts:**
  - the building type (epc_egenbyggnadstyp) is Mellanliggande, not Friliggande
  - answer no, the building is not detached

**Gold must_avoid:**
  - answering yes
  - claiming the building type is Friliggande

**Cache anchors:**
  - byggnadsid=`01-80-AVKOPPLINGEN4-1`
  - **nybyggar=1957** (real year built)
  - **atemp=108** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0606` = **Jun 2006** (YYMM, NOT a year)
  - sista=`0705` = **May 2007** (YYMM, NOT a year)

**Response:**

> According to the most recent energy performance certificate (dated 2020-09-16) for Astrakangatan 180, the building’s “egenbyggnadstyp” is recorded as “Mellanliggande” under the category “En- och tvåbostadshus.”  In other words, it is a mid-terrace (row) house rather than a detached (“Friliggande”) house.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly identifies the building type as Mellanliggande from the cache anchors and correctly concludes that it is not Friliggande, without introducing unsupported or conflicting data.
```

---

## 15 · `e0f8783d`  ·  Q013  ·  *constraint_filtering / medium*

**Question:** As of January 2026, is the building at Förridargränd 5 less than 75 years old?

**Gold expected_facts:**
  - the building was built in 1957
  - as of January 2026 the building is 69 years old
  - answer yes, the building is less than 75 years old

**Gold expected_numeric:** `{'year_built': 1957}`

**Gold must_avoid:**
  - answering no
  - claiming the building is 75 years old or older
  - computing age relative to a year other than 2026

**Cache anchors:**
  - byggnadsid=`01-80-AVSKILDHETEN3-1`
  - **nybyggar=1957** (real year built)
  - **atemp=117** m²
  - plan=2
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`E`
  - forsta=`1901` = **Jan 2019** (YYMM, NOT a year)
  - sista=`1912` = **Dec 2019** (YYMM, NOT a year)

**Response:**

> According to the latest energy‐performance certificate, the building at Förridargränd 5 was constructed in 1957 (epc_egennybyggar = 1957). By January 2026 it will be 69 years old, which is still below the 75-year threshold. Its most recent recorded energy class is E.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly uses the provided construction year (1957) to compute age in 2026 (~69 years) and correctly concludes that the building is under 75 years old, fully aligned with constraints and cache anchors.
```

---

## 16 · `47c0327c`  ·  Q015  ·  *edge_case / easy*

**Question:** What is the heated area?

**Gold expected_facts:**
  - ask for an address or clarification rather than answering

**Gold must_avoid:**
  - fabricated heated area for an unspecified building
  - picking an arbitrary building

**Cache:** *(empty - edge case)*

**Response:**

> Can you please provide the building address?

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly requests clarification instead of fabricating or guessing a heated area for an unspecified building.
```

---

## 17 · `09d5356d`  ·  Q016  ·  *edge_case / hard*

**Question:** What is the average rent for the apartments in the building at Loviselundsvägen 81?

**Gold expected_facts:**
  - indicate that rent data is not available in the EPC registry
  - do not fabricate a numeric rent value
  - may optionally note that rent figures would come from a different data source than EPC

**Gold must_avoid:**
  - fabricated rent in SEK
  - treating rent as an EPC field
  - guessing a Stockholm-average rent and applying it to this building

**Cache:** *(empty - edge case)*

**Response:**

> I’m sorry, but I don’t have any rental-price records for the building at Loviselundsvägen 81. Without actual lease data from the property owner, housing association or municipal housing statistics, it isn’t possible to calculate an average rent per apartment.  
> 
> To obtain this information you may wish to:  
> • Contact the building’s landlord or managing housing association directly – they typically keep up-to-date rent rolls.  
> • Consult municipal or regional housing market reports (often published by your municipality or by the Swedish Association of Public Housing Companies, SABO).  
> • Check real-estate listing services or local rental platforms, which can give you current asking rents in that specific area.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly states that rent information is unavailable and avoids fabricating rental values or treating rent as an EPC field.
```

---

## 18 · `f8c129ef`  ·  Q007  ·  *simple_address / medium*

**Question:** What is the heated area of the building at Förridargränd 9?

**Gold expected_facts:**
  - heated area (Atemp) is 95 m²

**Gold expected_numeric:** `{'atemp_m2': 95}`

**Cache anchors:**
  - byggnadsid=`01-80-AVSKILDHETEN5-1`
  - **nybyggar=1957** (real year built)
  - **atemp=95** m²
  - plan=2
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`F`
  - forsta=`2004` = **Apr 2020** (YYMM, NOT a year)
  - sista=`2103` = **Mar 2021** (YYMM, NOT a year)

**Response:**

> The Energy Performance Certificate for Förridargränd 9 reports a heated floor area (Atemp) of 95 m².

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly reports the heated area as 95 m² directly from the EPC cache data without adding unsupported information.
```

---

## 19 · `007dfcac`  ·  Q014  ·  *edge_case / easy*

**Question:** What is the heated area of the building at Nonexistentgatan 999?

**Gold expected_facts:**
  - indicate that no building was found at the given address
  - do not fabricate any numeric heated area value

**Gold must_avoid:**
  - fabricated heated area number
  - treating the address as real

**Cache:** *(empty - edge case)*

**Response:**

> I’m sorry, but I can’t find any energy-performance (EPC) data for “Nonexistentgatan 999.” Therefore, the heated floor area (Atemp) is not available. If you have a building ID, property number or EPC address ID, please share it and I can try again.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly indicates that no EPC/building data could be found for the provided address and avoids fabricating a heated area value.
```

---

## 20 · `396a1388`  ·  Q003  ·  *simple_address / easy*

**Question:** What is the heated area (Atemp) of the building at Friherregatan 111?

**Gold expected_facts:**
  - heated area (Atemp) is 132 m²

**Gold expected_numeric:** `{'atemp_m2': 132}`

**Cache anchors:**
  - byggnadsid=`01-80-KLAETTEN14-1`
  - **nybyggar=1960** (real year built)
  - **atemp=132** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0712` = **Dec 2007** (YYMM, NOT a year)
  - sista=`0811` = **Nov 2008** (YYMM, NOT a year)

**Response:**

> The Energy Performance Certificate for the property at Friherregatan 111 reports a heated floor area (Atemp) of 132 m².

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly reports the heated area (Atemp) as 132 m² directly from the cache anchors without adding unsupported information.
```

---

## 21 · `5f56902a`  ·  Q001  ·  *simple_address / easy*

**Question:** What is the heated area (Atemp) of the building at Loviselundsvägen 81?

**Gold expected_facts:**
  - heated area (Atemp) is 270 m²
  - the building is at Loviselundsvägen 81

**Gold expected_numeric:** `{'atemp_m2': 270}`

**Cache anchors:**
  - byggnadsid=`01-80-BERBERISEN7-1`
  - **nybyggar=1947** (real year built)
  - **atemp=270** m²
  - plan=2
  - byggnadstyp=`Friliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`C`
  - forsta=`1702` = **Feb 2017** (YYMM, NOT a year)
  - sista=`1801` = **Jan 2018** (YYMM, NOT a year)

**Response:**

> The energy performance certificate for the building at Loviselundsvägen 81 (Building ID 01-80-BERBERISEN7-1) reports a total heated floor area (Atemp) of 270 m².

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly reports the heated area (Atemp) as 270 m² for Loviselundsvägen 81, fully consistent with the provided cache anchors.
```

---

## 22 · `1f18eb2e`  ·  Q012  ·  *edge_case / hard*

**Question:** When was the building at Astrakangatan 180 last renovated?

**Gold expected_facts:**
  - indicate that renovation history is not available in the EPC data
  - do not fabricate a renovation date

**Gold must_avoid:**
  - fabricated renovation year
  - treating epc_egennybyggar (construction year) as a renovation year

**Cache anchors:**
  - byggnadsid=`01-80-AVKOPPLINGEN4-1`
  - **nybyggar=1957** (real year built)
  - **atemp=108** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0606` = **Jun 2006** (YYMM, NOT a year)
  - sista=`0705` = **May 2007** (YYMM, NOT a year)

**Response:**

> I have reviewed the EPC records for Astrakangatan 180 (BBR building ID 01-80-AVKOPPLINGEN4-1). Neither the 2010 nor the 2020 energy performance certificates include an explicit “last renovation” date. They do show:
> 
> • EPC 1.4 (approved 2009-02-10) – measurement period June 2006 to May 2007  
> • EPC 2.8 (approved 2020-09-16) – measurement period September 2019 to August 2020  
> 
> However, those periods refer to the energy‐use monitoring interval, not building renovation. No renovation date is recorded in the available EPC metadata.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: irrelevant_extras
NOTES: Correctly states that renovation history is not available in the EPC data and explicitly avoids confusing monitoring periods with renovation dates, but introduces unsupported extra metadata about EPC versions and approval dates.
```

---

## 23 · `7ebbebc9`  ·  Q018  ·  *constraint_filtering / medium*

**Question:** Was the building at Loviselundsvägen 81 built before 1970?

**Gold expected_facts:**
  - the building was built in 1947
  - answer yes, the building was built before 1970

**Gold expected_numeric:** `{'year_built': 1947}`

**Gold must_avoid:**
  - answering no without justification
  - claiming the building was built after 1970

**Cache anchors:**
  - byggnadsid=`01-80-BERBERISEN7-1`
  - **nybyggar=1947** (real year built)
  - **atemp=270** m²
  - plan=2
  - byggnadstyp=`Friliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`C`
  - forsta=`1702` = **Feb 2017** (YYMM, NOT a year)
  - sista=`1801` = **Jan 2018** (YYMM, NOT a year)

**Response:**

> According to the latest energy performance certificate for Loviselundsvägen 81 (Building ID 01-80-BERBERISEN7-1), the building’s “egennybyggar” (year of original construction) is listed as 1947. Therefore, the building was indeed erected well before 1970. Its most recent declared energy class is C.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly identifies the construction year as 1947 and correctly concludes that the building was built before 1970, fully consistent with the cache anchors.
```

---

## 24 · `61daad44`  ·  Q010  ·  *simple_address / easy*

**Question:** What year was the building at Förridargränd 11 built?

**Gold expected_facts:**
  - the building at Förridargränd 11 was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchors:**
  - byggnadsid=`01-80-AVSKILDHETEN6-1`
  - **nybyggar=1957** (real year built)
  - **atemp=117** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`F`
  - forsta=`1701` = **Jan 2017** (YYMM, NOT a year)
  - sista=`1712` = **Dec 2017** (YYMM, NOT a year)

**Response:**

> The building at Förridargränd 11 was constructed in 1957.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly states that the building was constructed in 1957, fully aligned with the cache anchor nybyggar=1957 and without introducing unsupported information.
```

---

## 25 · `11bd5455`  ·  Q020  ·  *constraint_filtering / medium*

**Question:** Is the heated area of the building at Friherregatan 132 larger than 200 m²?

**Gold expected_facts:**
  - heated area (Atemp) is 152 m²
  - answer no, 152 m² is not larger than 200 m²

**Gold expected_numeric:** `{'atemp_m2': 152}`

**Gold must_avoid:**
  - answering yes
  - claiming heated area exceeds 200 m²

**Cache anchors:**
  - byggnadsid=`01-80-BLOMLISTEN4-1`
  - **nybyggar=1958** (real year built)
  - **atemp=152** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`E`
  - forsta=`2108` = **Aug 2021** (YYMM, NOT a year)
  - sista=`2207` = **Jul 2022** (YYMM, NOT a year)

**Response:**

> The Energy Performance Certificate (EPC) for Friherregatan 132 reports a total heated floor area (Atemp) of 152 m². Since 152 m² is below 200 m², the building’s heated area is not larger than 200 m².

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS:
NOTES: Correctly reports Atemp as 152 m² and correctly concludes that it is not larger than 200 m², fully consistent with the cache anchors and task constraints.
```

---

## 26 · `10e71127`  ·  Q017  ·  *edge_case / medium*

**Question:** What is the heated area of the building at Loviselundsvägen?

**Gold expected_facts:**
  - indicate that the address is incomplete (no street number)
  - either ask for the full address or return multiple matches if found

**Gold must_avoid:**
  - silently picking one of several matches and reporting its Atemp as the answer

**Cache:** *(empty - edge case)*

**Response:**

> I’m afraid I don’t have the energy-declaration (EPC) data for the property at Loviselundsvägen, so I cannot tell you its heated floor area (Atemp). If you can supply the EPC record or at least the value of the “epc_egenatemp” field for that building, I’ll be able to give you the exact heated area in square meters.

**Scores:**
```
G: 4  
C: 2  
NF: 4  
CS: 4  
P: 0  
TAGS: partial_answer  
NOTES: Response correctly refuses due to missing EPC data but fails to address the key ambiguity requirement (incomplete address / multiple possible matches as specified in gold expected facts).
```

---

## 27 · `c1d3642b`  ·  Q020  ·  *constraint_filtering / medium*

**Question:** Is the heated area of the building at Friherregatan 132 larger than 200 m²?

**Gold expected_facts:**
  - heated area (Atemp) is 152 m²
  - answer no, 152 m² is not larger than 200 m²

**Gold expected_numeric:** `{'atemp_m2': 152}`

**Gold must_avoid:**
  - answering yes
  - claiming heated area exceeds 200 m²

**Cache anchors:**
  - byggnadsid=`01-80-BLOMLISTEN4-1`
  - **nybyggar=1958** (real year built)
  - **atemp=152** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`E`
  - forsta=`2108` = **Aug 2021** (YYMM, NOT a year)
  - sista=`2207` = **Jul 2022** (YYMM, NOT a year)

**Response:**

> The energy performance certificate (EPC) for Friherregatan 132 reports a heated floor area (Atemp) of 152 m². Since 152 m² is below 200 m², the building’s heated area is not larger than 200 m².

**Scores:**
```
G: 4  
C: 4  
NF: 4  
CS: 4  
P: 1  
TAGS:  
NOTES: Answer correctly uses Atemp = 152 m² from gold data and correctly concludes it is not larger than 200 m²; fully grounded, complete, numerically consistent, and satisfies the comparison constraint.
```

---

## 28 · `d9834c76`  ·  Q014  ·  *edge_case / easy*

**Question:** What is the heated area of the building at Nonexistentgatan 999?

**Gold expected_facts:**
  - indicate that no building was found at the given address
  - do not fabricate any numeric heated area value

**Gold must_avoid:**
  - fabricated heated area number
  - treating the address as real

**Cache:** *(empty - edge case)*

**Response:**

> I’m afraid I can’t find any energy-performance data (including the heated floor area, “Atemp”) for a building at Nonexistentgatan 999. In our schema the heated area would be given by the epc_egenatemp field (in m²), but no such entry is available for that address. If you can supply the building’s EPC record or its unique building ID, I can try again.

**Scores:**
```
G: 4  
C: 4  
NF: 4  
CS: 4  
P: 1  
TAGS:  
NOTES: Correctly identifies absence of building data and avoids fabricating any Atemp value. Response is fully grounded in the “no entry exists” condition implied by the query; avoids hallucination and follows constraints.
```

---

## 29 · `9cb87eeb`  ·  Q003  ·  *simple_address / easy*

**Question:** What is the heated area (Atemp) of the building at Friherregatan 111?

**Gold expected_facts:**
  - heated area (Atemp) is 132 m²

**Gold expected_numeric:** `{'atemp_m2': 132}`

**Cache anchors:**
  - byggnadsid=`01-80-KLAETTEN14-1`
  - **nybyggar=1960** (real year built)
  - **atemp=132** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0712` = **Dec 2007** (YYMM, NOT a year)
  - sista=`0811` = **Nov 2008** (YYMM, NOT a year)

**Response:**

> The Energy Performance Certificate for Friherregatan 111 reports:
> 
> • EPC Atemp (heated floor area): 132 m²
> 
> - this is the total heated area as defined in the certificate.

**Scores:**
```
G: 4  
C: 4  
NF: 4  
CS: 4  
P: 1  
TAGS:  
NOTES: Response exactly matches gold Atemp value (132 m²), correctly answers the question, and presents no contradictions or extra constraints violations. Fully grounded and complete.
```

---

## 30 · `03738e37`  ·  Q001  ·  *simple_address / easy*

**Question:** What is the heated area (Atemp) of the building at Loviselundsvägen 81?

**Gold expected_facts:**
  - heated area (Atemp) is 270 m²
  - the building is at Loviselundsvägen 81

**Gold expected_numeric:** `{'atemp_m2': 270}`

**Cache anchors:**
  - byggnadsid=`01-80-BERBERISEN7-1`
  - **nybyggar=1947** (real year built)
  - **atemp=270** m²
  - plan=2
  - byggnadstyp=`Friliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`C`
  - forsta=`1702` = **Feb 2017** (YYMM, NOT a year)
  - sista=`1801` = **Jan 2018** (YYMM, NOT a year)

**Response:**

> The Energy Performance Certificate for Loviselundsvägen 81 reports the heated floor area (Atemp) as 270 m². This figure comes directly from the EPC (“epc_egenatemp”) and represents the total measured, heated area of the building.

**Scores:**
```
G: 4  
C: 4  
NF: 4  
CS: 4  
P: 1  
TAGS:  
NOTES: Answer is fully consistent with gold data: correctly identifies Atemp = 270 m² and directly answers the question without omission or hallucination. No constraint violations or unsupported claims.
```

---

## 31 · `b6e958d0`  ·  Q019  ·  *constraint_filtering / medium*

**Question:** Is the building at Astrakangatan 180 a detached (Friliggande) house?

**Gold expected_facts:**
  - the building type (epc_egenbyggnadstyp) is Mellanliggande, not Friliggande
  - answer no, the building is not detached

**Gold must_avoid:**
  - answering yes
  - claiming the building type is Friliggande

**Cache anchors:**
  - byggnadsid=`01-80-AVKOPPLINGEN4-1`
  - **nybyggar=1957** (real year built)
  - **atemp=108** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0606` = **Jun 2006** (YYMM, NOT a year)
  - sista=`0705` = **May 2007** (YYMM, NOT a year)

**Response:**

> The energy performance certificates for Astrakangatan 180 (Building ID 01-80-AVKOPPLINGEN4-1) consistently classify it as an “En- och tvåbostadshus” of type “Mellanliggande” (mid-terraced house). In other words, it is not a friliggande (detached) house but an intermediate/row-house unit.

**Scores:**
```
G: 4  
C: 4  
NF: 4  
CS: 4  
P: 1  
TAGS:  
NOTES: Correctly uses cached building type “Mellanliggande” and correctly concludes it is not detached (Friliggande). No contradictions, no missing constraints, and all claims are supported by the provided ground truth.
```

---

## 32 · `ba654d47`  ·  Q008  ·  *simple_address / easy*

**Question:** When was the building at Kurirgatan 16 built?

**Gold expected_facts:**
  - the building was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchors:**
  - byggnadsid=`01-80-BAECKSLINGAN3-0`
  - **nybyggar=1957** (real year built)
  - **atemp=119** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0905` = **May 2009** (YYMM, NOT a year)
  - sista=`1004` = **Apr 2010** (YYMM, NOT a year)

**Response:**

> According to the building’s energy performance certificate (EPC) data for Kurirgatan 16, the property was constructed in 1957.

**Scores:**
```
G: 4  
C: 4  
NF: 4  
CS: 4  
P: 1  
TAGS:  
NOTES: Response directly matches gold expected fact (nybyggar = 1957 → built in 1957). No extra claims, no contradictions, and fully grounded in the provided cache.
```

---

## 33 · `e934a59a`  ·  Q006  ·  *simple_address / medium*

**Question:** What is the building category (epc_egenbyggnadskat) of Bruntegatan 5, and what year was it built?

**Gold expected_facts:**
  - the building at Bruntegatan 5 is in the category En- och tvåbostadshus
  - the building was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchors:**
  - byggnadsid=`01-80-BLOMPRAKTEN4-`
  - **nybyggar=1957** (real year built)
  - **atemp=115** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0802` = **Feb 2008** (YYMM, NOT a year)
  - sista=`0901` = **Jan 2009** (YYMM, NOT a year)

**Response:**

> The energy‐performance certificate for Bruntegatan 5 shows that it is classified as an “En- och tvåbostadshus” (a single- and two-dwelling house) and that the building was originally constructed in 1957.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: 
NOTES: response matches gold expectation
```

---

## 34 · `6448c166`  ·  Q007  ·  *simple_address / medium*

**Question:** What is the heated area of the building at Förridargränd 9?

**Gold expected_facts:**
  - heated area (Atemp) is 95 m²

**Gold expected_numeric:** `{'atemp_m2': 95}`

**Cache anchors:**
  - byggnadsid=`01-80-AVSKILDHETEN5-1`
  - **nybyggar=1957** (real year built)
  - **atemp=95** m²
  - plan=2
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`F`
  - forsta=`2004` = **Apr 2020** (YYMM, NOT a year)
  - sista=`2103` = **Mar 2021** (YYMM, NOT a year)

**Response:**

> The Energy Performance Certificate for the building at Förridargränd 9 reports a heated floor area (Atemp) of 95 m².

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: 
NOTES: ok with gold expectations
```

---

## 35 · `fff43c34`  ·  Q009  ·  *simple_address / easy*

**Question:** What is the heated area (Atemp) of the building at Kurirgatan 11?

**Gold expected_facts:**
  - heated area (Atemp) is 94 m²

**Gold expected_numeric:** `{'atemp_m2': 94}`

**Cache anchors:**
  - byggnadsid=`01-80-BAECKSLINGAN12-1`
  - **nybyggar=1958** (real year built)
  - **atemp=94** m²
  - plan=2
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`1104` = **Apr 2011** (YYMM, NOT a year)
  - sista=`1203` = **Mar 2012** (YYMM, NOT a year)

**Response:**

> The energy performance certificate (EPC) for the building at Kurirgatan 11 (BBR building ID 01-80-BAECKSLINGAN12-1) reports a heated floor area (Atemp) of 94 m². This value comes from the EPC’s own Atemp field (epc_egenatemp), which is the area used in the energy declaration.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: 
NOTES: very detailed
```

---

## 36 · `f2b507a9`  ·  Q015  ·  *edge_case / easy*

**Question:** What is the heated area?

**Gold expected_facts:**
  - ask for an address or clarification rather than answering

**Gold must_avoid:**
  - fabricated heated area for an unspecified building
  - picking an arbitrary building

**Cache:** *(empty - edge case)*

**Response:**

> Can you please provide the building address?

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: 
NOTES: correct question, concise.
```

---

## 37 · `a63dd82f`  ·  Q016  ·  *edge_case / hard*

**Question:** What is the average rent for the apartments in the building at Loviselundsvägen 81?

**Gold expected_facts:**
  - indicate that rent data is not available in the EPC registry
  - do not fabricate a numeric rent value
  - may optionally note that rent figures would come from a different data source than EPC

**Gold must_avoid:**
  - fabricated rent in SEK
  - treating rent as an EPC field
  - guessing a Stockholm-average rent and applying it to this building

**Cache:** *(empty - edge case)*

**Response:**

> I’m sorry, but I don’t have any rent‐level data for the apartments at Loviselundsvägen 81 in my current records. To obtain the average rent you could:
> 
> • Contact the landlord or property manager for Loviselundsvägen 81 directly – they will have the official figures.  
> • Check public listings on sites such as Hemnet, Booli or Blocket Bostad and calculate an average based on the advertised rents for that address.  
> • Reach out to the Hyresgästföreningen or the local Hyresnämnd (Rent Tribunal) – they maintain statistics on rent levels and can often provide guidance on current market rents in a specific building or area.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: 
NOTES: 
```

---

## 38 · `59c48dba`  ·  Q018  ·  *constraint_filtering / medium*

**Question:** Was the building at Loviselundsvägen 81 built before 1970?

**Gold expected_facts:**
  - the building was built in 1947
  - answer yes, the building was built before 1970

**Gold expected_numeric:** `{'year_built': 1947}`

**Gold must_avoid:**
  - answering no without justification
  - claiming the building was built after 1970

**Cache anchors:**
  - byggnadsid=`01-80-BERBERISEN7-1`
  - **nybyggar=1947** (real year built)
  - **atemp=270** m²
  - plan=2
  - byggnadstyp=`Friliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - klass=`C`
  - forsta=`1702` = **Feb 2017** (YYMM, NOT a year)
  - sista=`1801` = **Jan 2018** (YYMM, NOT a year)

**Response:**

> Yes. According to the building’s energy‐declaration data, Loviselundsvägen 81 was originally constructed in 1947 – well before 1970.  Here are a few key facts from the latest EPC:
> 
> • Building ID (byggnadsid): 01-80-BERBERISEN7-1  
> • Address: Loviselundsvägen 81, Hässelby (Stockholm)  
> • Year of construction (epc_egennybyggar): 1947  
> • Building type (epc_egenbyggnadstyp): Friliggande småhus (detached house)  
> • Heated dwelling area (epc_egenatempbostad): 100 m²  
> • Above-ground floors (epc_egenantalplan): 2  
> • Basement levels (epc_egenantalkallarplan): 1  
> • Latest certified energy class (epc_egienergiklass2020_calc): C  
> 
> All of this confirms that the house predates 1970.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: 
NOTES: 
```

---

## 39 · `9eaaa8d7`  ·  Q002  ·  *simple_address / easy*

**Question:** What year was the building at Astrakangatan 180 built?

**Gold expected_facts:**
  - the building at Astrakangatan 180 was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchors:**
  - byggnadsid=`01-80-AVKOPPLINGEN4-1`
  - **nybyggar=1957** (real year built)
  - **atemp=108** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0606` = **Jun 2006** (YYMM, NOT a year)
  - sista=`0705` = **May 2007** (YYMM, NOT a year)

**Response:**

> According to the energy performance certificate for Astrakangatan 180 (Avkopplingen 4), the building was constructed in 1957 (as indicated by the “egennybyggar” field in the EPC data).

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: 
NOTES: 
```

---

## 40 · `9608c70f`  ·  Q008  ·  *simple_address / easy*

**Question:** When was the building at Kurirgatan 16 built?

**Gold expected_facts:**
  - the building was built in 1957

**Gold expected_numeric:** `{'year_built': 1957}`

**Cache anchors:**
  - byggnadsid=`01-80-BAECKSLINGAN3-0`
  - **nybyggar=1957** (real year built)
  - **atemp=119** m²
  - byggnadstyp=`Mellanliggande`
  - byggnadskat=`En- och tvåbostadshus`
  - forsta=`0905` = **May 2009** (YYMM, NOT a year)
  - sista=`1004` = **Apr 2010** (YYMM, NOT a year)

**Response:**

> The property at Kurirgatan 16 (Bäckslingan 3) was originally constructed in 1957.

**Scores:**
```
G: 4
C: 4
NF: 4
CS: 4
P: 1
TAGS: 
NOTES: 
```

---
