# Blinded labelling form — plan-eil-v25

Score every block, then ingest with:

    python scripts/spotcheck_closed_loop.py ingest-md \
        --form artifacts/runs/2026-08-16_v25_labeling_form.md --labels artifacts/runs/2026-08-16_v25_labels.csv \
        --worklist artifacts/runs/2026-08-16_v25_worklist.csv --annotator-id <you>

**Rules.** Judge only what is shown. Each block's id is opaque on purpose: it hides
which arm produced the answer, so knowing the loop had a second attempt cannot colour
the score. The building id given is the one *the system resolved* — it is not the gold
answer, and it is what `entity_consistency` is defined against: does every figure the
answer presents as the user's own belong to that building?

Scale 0 (worst) – 10 (perfect). `overall_pass` is 0 or 1: would you ship this answer?

**A single axis may be left blank when it does not apply** — about 15 of these blocks
retrieved no evidence and identify no building, so there is nothing for
`entity_consistency` (or `faithfulness`) to be checked against. A blank axis is dropped
from that axis's agreement pool and the rest of the block still counts. `overall_pass`
is always required. Leaving *every* field blank skips the block entirely, so a partly
filled form can be ingested as often as you like — re-ingesting is idempotent.

---

## Case 1 / 65 — `4491244b`
**Expected route:** `building_specific`
**Building the system identified:** `17-81-BONDEN9-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
For your building (Building ID 17-81-BONDEN9-1), the latest energy performance certificate shows:

• EPC version: 2.6, approved 2019-04-11  
• Energy performance (Energiprestanda): 109 kWh/m²·year  
• Energy efficiency class: D  
• Specific energy use (Egen specifik energianvändning): 82 kWh/m²·year  
• Total annual energy use: 22 914 kWh  
• Primary energy use: 30 552 kWh  
• Main heating system: Air-to-water heat pump  

In summary, your building’s energy performance is 109 kWh/m²·year, corresponding to an energy class D.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: entity 10: every figure (109 D, spec 82, 22 914 kWh, primary 30 552, EPC 2.6 2019-04-11, A2W HP) is attested for BONDEN9-1 elsewhere in the form and coheres internally (82x280≈22 960). faith 9 not 10 only because values beyond the 12 shown fields cannot be positively verified. calib 8: confident where supported.

---

## Case 2 / 65 — `1d3404e1`
**Expected route:** `building_specific`
**Building the system identified:** `19-84-LEOPARDEN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the energy‐performance figures from your latest EPCs:

• Building ID 19-84-LEOPARDEN5-1 (Tulegatan 5A, Arboga)  
  – Specific energy performance: 190 kWh/m²·year  
  – Energy class (2020): E  

• Building ID 01-80-HAELSAN10-2 (Tulegatan 5A, Stockholm)  
  – Specific energy performance: 183 kWh/m²·year  
  – Energy class (2020): E  

These values refer to the annual delivered energy per heated floor area, as reported in the most recent energy performance certificates.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[4]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[3]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[0]`
- notes: "your latest EPCs" possessively attributes both LEOPARDEN (190/E, correct) and HAELSAN (183/E) to the user, and both carry the same address "Tulegatan 5A" in two different cities. entity 3: user's own figure present and correct, but a second building is claimed as theirs. calib 3: no acknowledgment of the ambiguity it created. pass 0: leaks another property as the user's.

---

## Case 3 / 65 — `cc2b5b1a`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the latest energy performance certificate (EPC) for your building (Building ID 19-84-LEOPARDEN5-1), here are the key figures:

• Energy class (2020 calculation): E  
• Energy performance (“energi­prestanda”): 102 kWh/m²·year  
• Primary energy use: 101 553 kWh/year  
• Total delivered energy use: 140 353 kWh/year  
• Specific delivered energy use: 141 kWh/m²·year  
• Main heating system: district heating (103 328 kWh/year)  

In other words, your property uses about 102 kWh of primary energy per square metre annually and falls into class E on the Swedish scale.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap: named HAELSAN, answers "your building (19-84-LEOPARDEN5-1)". faith 3 not lower: figures are internally coherent (140 353/141 and 101 553/102 both imply ≈995 m²) and match the Arboga record quoted in case-10 style blocks; the false claim is the attribution itself. rel 10 per v24 rule (topic fully addressed). entity 0, calib 2: total confidence in a wrong attribution.

---

## Case 4 / 65 — `82044cac`
**Expected route:** `combined`
**Building the system identified:** `01-80-FILOSOFEN2-2`

### Question

> What should my building prioritize to improve energy efficiency?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on your current EPC results (energy class B, primary energy 53 kWh/m²·year, specific energy use ~59 kWh/m²·year, heating and hot water supplied by district heating), here are the most effective priorities to further reduce your building’s energy demand and move toward class A:

1. Reduce space-heating demand through the envelope  
   • Check and improve airtightness at critical junctions (window and door perimeters, balconies, penetrations).  
   • Upgrade thermal performance of any remaining weak spots in walls or roof (for example by adding internal insulation or high-performance boards).  
   • If your windows are double-glazed, consider replacing with modern triple-glazed units or adding low-e films on the cold side.

2. Lower domestic hot-water energy use  
   • Install low-flow fixtures and thermostatic mixers to cut tap-water volumes by up to 20–30 %.  
   • Add a small solar-thermal pre-heating system on the roof to reduce district-heating consumption for hot water.  
   • Insulate and balance your hot-water distribution loop to avoid heat losses in risers and circulation pipes.

3. Optimize your ventilation system  
   • You already have FTX (mechanical supply and exhaust with heat recovery). Regularly service and balance the system so recovery efficiency stays above 80 %.  
   • Fit electronically commutated (EC) fans if you haven’t already; they can cut fan electricity by up to 30 %.  
   • Consider demand-controlled ventilation (CO₂ or humidity sensors) in corridors or large common areas to reduce unnecessary airflow.

4. Generate on-site renewable electricity  
   • Install photovoltaic panels on available roof or façade areas. Even a modest 10–15 kW PV array can offset 15–20 % of your annual electricity use (currently ~28 400 kWh).  
   • Pair PV with communal charging stations for any electric-vehicle fleet or e-bikes to maximize self-consumption.

5. Implement active energy management  
   • Use sub-metering for heating, hot water and electricity in stairwells or communal spaces—this both raises residents’ awareness and identifies hidden losses.  
   • Deploy a simple BMS (building management system) or cloud-based dashboard to monitor temperatures, flows and energy use in real time and catch faults early.

By first targeting the envelope and hot-water system you reduce the largest shares of your primary energy use; fine-tuning ventilation and adding renewables will then move you from a strong B-rating toward A or even the top of the scale.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[1]`
- notes: All EPC figures match the resolved FILOSOFEN2-2 (B, 53 primary, ~59 specific, ~28 400 elec ≈ the 28 436 attested in case 30). faith 5 / calib 3: asserts "You already have FTX" and quotes the electricity figure while its own note lists ventilation_type and electricity_use as unconfirmed (2 self-contradictions); windows correctly handled as a conditional. pass 1: entity-correct, advice sound.

---

## Case 5 / 65 — `0e326106`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
According to the latest energy‐declaration for your property (Building ID 05-83-RAEFSAN5-1), the primary heating system is an air-to-water heat pump. There is no district-heating connection installed (district_heating_use = 0).

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap: named HAELSAN, answers RAEFSAN's record (A2W HP, district_heating_use=0 — both attested for RAEFSAN). faith 3: record quoted accurately, attribution false. rel 10: heating question fully answered, just for the wrong building — entity 0 carries that failure.

---

## Case 6 / 65 — `adb40a42`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the main heating systems for the buildings in the provided data set:

1. Building ID 01-80-HAELSAN10-2 (Rådmansgatan 31, Stockholm)  
   - Primary heating source: Fjärrvärme (district heating)  
   - Reported district heating consumption: 946 000 kWh/year  

2. Building ID 05-83-RAEFSAN5-1 (Rådmansgatan 31, Motala)  
   - Primary heating source: Pump luft–vatten (air-to-water heat pump)  

3. Building ID 17-81-BONDEN9-1 (Rådmansgatan 31, Kristinehamn)  
   - Primary heating source: Pump luft–vatten (air-to-water heat pump)  

If you are referring to the first entry (01-80-HAELSAN10-2), your building is heated via the district heating network.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[7]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[0]`
- notes: Three buildings listed with per-building data matching other blocks, and the conditional ending picks the correct one (HAELSAN → district heating, 946 000). entity 7: no false possessive and the right building is conditionally answered. faith 5: all three share the address "Rådmansgatan 31" in three different cities — address copied, not retrieved. calib 6: hedges via "if you are referring to" although the system had already resolved the building. pass 0: a data dump plus a conditional is not a shippable answer to "my building".

---

## Case 7 / 65 — `6423cc4c`
**Expected route:** `building_specific`
**Building the system identified:** `05-83-RAEFSAN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the energy‐performance details for each of the three certificates in your query. The most recent (latest approved) certificate is shown last and should be taken as the current reference.

1. Building ID: 17-81-BONDEN9-1  
   – Approval date: 2019-04-11  
   – Energy performance (Energiprestanda): 109 kWh/m²·year  
   – Energy class (Energiklass): D  

2. Building ID: 01-80-HAELSAN10-2  
   – Approval date: 2008-12-30  
   – Energy performance: 155 kWh/m²·year  
   – Energy class: E  

3. Building ID: 05-83-RAEFSAN5-1  ← Latest certificate (2024-09-19)  
   – Energy performance: 75 kWh/m²·year  
   – Energy class: C  

Since the certificate for “05-83-RAEFSAN5-1” is the most recently approved (September 2024), its values (75 kWh/m²·year, class C) are the current, valid energy‐performance figures for your building.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[4]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[0]`
- notes: Treats BONDEN's, HAELSAN's and RAEFSAN's certificates as one building's version history and picks "the latest" — false premise, though the conclusion (75/C) is correct for the named RAEFSAN. faith 3: the reasoning chain is fabricated even if each figure is real. entity 4: final attribution correct but two foreign certs presented as candidates for "your" history. pass 0: right answer via reasoning that would misattribute on any other draw.

---

## Case 8 / 65 — `e2ed36ee`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the key figures from your latest Energy Performance Certificate (EPC) for building 01-80-HAELSAN10-2:

• Energy performance (EPC “energiprestanda”): 183 kWh/m²·year  
• Specific energy use (EPC “specifik energianvändning”): 178 kWh/m²·year  
• Primary energy use (EPC “primärenergital 2020”): 133 kWh/m²·year  
• Energy class:  
  – 2016 certificate: F  
  – 2020 certificate: E (latest and therefore the valid class)  

In other words, your building consumes 183 kWh of delivered energy per square metre and year, placing it in energy class E under the current (2020) scale.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: Matches the HAELSAN 183-family (2016 F → 2020 E, specific 178, primary 133) and correctly explains which class is current. Note: the form also contains a 155/2008 HAELSAN family both claiming "latest"; judging only what is shown, this block is internally coherent, hence faith 8.

---

## Case 9 / 65 — `f56753a8`
**Expected route:** `building_specific`
**Building the system identified:** `17-81-BONDEN9-1`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the energy-declaration records you provided, the heating systems for each building are as follows:

• Building ID 05-83-RAEFSAN5-1 (Räfsan 5, Motala)  
  – Main heating system: air-to-water heat pump  
  – No district heating in use  

• Building ID 17-81-BONDEN9-1 (Bonden 9, Kristinehamn)  
  – Main heating system: air-to-water heat pump  
  – No district heating in use  

• Building ID 01-80-HAELSAN10-2 (Hälsan 10, Stockholm)  
  – Main heating system: district heating  
  – District heating consumption: 946 000 kWh/year  

If you have a specific building in mind, please let me know which “byggnadsid” you’re interested in, and I can focus on that record.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[7]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[5]`
- calibration (0–10): `[5]`
- overall_pass (0 or 1): `[0]`
- notes: Accurate neutral listing: all three buildings correctly labeled (Räfsan 5 Motala, Bonden 9 Kristinehamn, Hälsan 10 Stockholm — the only multi-building block whose addresses all check out), and the named BONDEN's correct answer (A2W HP) is present. rel 9: the topic is addressed for every candidate; the failure is refusing to commit, which lands on entity (5: labeled, asks, never attributes) and calibration (5: asks about an ambiguity the system had already resolved). pass 0.

---

## Case 10 / 65 — `833f9d60`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
The building’s primary heating system is district heating (Sw. “fjärrvärme”). According to the latest Energy Performance Certificate:

• Heating system: District heating  
• District heating network: Arboga  
• Annual district heating use for space heating: 103 328 kWh  
• Annual district heating use for domestic hot water: 24 950 kWh  
• Energy class: E (102 kWh/m² and year)

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[1]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: No ID stated but every figure is foreign: "Arboga network", 103 328 + 24 950 kWh, class E at 102 kWh/m² belong to the Arboga record (case-3 figure family), not the named Stockholm building 01-80-HAELSAN10-2. entity 1: swap detectable only via the Arboga mention. calib 2: flat confidence throughout.

---

## Case 11 / 65 — `f1ac14de`
**Expected route:** `generic`

### Question

> What energy-saving features should you look for when buying a fridge or freezer?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
When shopping for a new refrigerator or freezer, look beyond size and style and focus on features that drive down energy consumption—both to reduce your utility bills and to lessen environmental impact. Key energy-saving features include:  

1. High Energy-Efficiency Rating  
   • In Europe: look for A +, A ++ or A +++ on the EU energy label (with A +++ being the most efficient).  
   • In the U.S.: choose ENERGY STAR–certified models.  
   • Compare the annual kWh consumption on the label—smaller numbers mean lower running costs.  

2. Inverter (Variable-Speed) Compressor  
   • Runs more smoothly and only as fast as needed, avoiding the constant on/off cycling of traditional compressors.  
   • Provides more precise temperature control, uses up to 20–30 % less energy and tends to be quieter.  

3. Efficient Insulation and Sealing  
   • Thick, high-density polyurethane foam in the walls and door.  
   • Magnetic door gaskets that form an airtight seal—inspect for uniform closure around the entire door.  
   • Automatic door-close or door-alarm features to prevent energy loss from accidentally propped-open doors.  

4. Frost-Free (Auto-Defrost) System  
   • Prevents ice build-up on evaporator coils, which otherwise forces the compressor to work harder.  
   • Maintains consistent cooling and reduces manual defrosting, saving both time and energy.  

5. LED Interior Lighting  
   • Uses significantly less electricity than incandescent or halogen bulbs and generates less heat (so the compressor doesn’t have to work as hard).  
   • Often longer-lasting, reducing maintenance and replacement costs.  

6. Precise Digital Temperature Controls  
   • Allows you to fine-tune temperatures in both fridge and freezer compartments to the ideal range (fridge: 3–5 °C; freezer: –18 °C).  
   • Built-in sensors and displays help prevent overcooling (which wastes energy) or undercooling (which can spoil food).  

7. Eco-Modes and Vacation Settings  
   • “Eco” or “Energy Saver” modes adjust defrost cycles and compressor run times to reduce consumption during periods of light use.  
   • “Vacation” or “Holiday” settings raise the fridge temperature slightly when you’re away but keep it above safety thresholds.  

8. Smart and Connectivity Features (Optional)  
   • Some models can monitor door openings, ambient temperature and energy consumption via a smartphone app.  
   • Alerts if the door is left ajar or if a power outage occurs—helping you prevent wasted energy and spoiled food.  

9. Right-Sizing and Configuration  
   • Choose the capacity you actually need; oversized units waste energy cooling dead space.  
   • A freezer compartment inside the fridge door or a small “mini-freezer” drawer can be more efficient than a two-door design if you only freeze a little.  

10. Placement and Installation Tips  
   • Allow proper ventilation around the back and sides—crowded or wall-pressed units must run longer.  
   • Keep your appliance out of direct sunlight or next to heat-emitting equipment (oven, radiator), which forces it to consume more energy.  

By prioritizing these features—especially a high energy-efficiency rating, inverter compressor technology, strong insulation with airtight seals, frost-free operation and LED lighting—you’ll secure a fridge or freezer that keeps your food cold while keeping energy use (and costs) to a minimum.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[6]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[4]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic question, no building. Fridge advice solid except the lead item: EU labels were rescaled to A–G in 2021, so telling a 2026 buyer to seek A+/A++/A+++ is obsolete — faith 6. calib 4: obsolete claim asserted confidently plus an irrelevant building-data note on an appliance question. pass 1 (borderline): the other nine items are accurate and useful.

---

## Case 12 / 65 — `ce9d4e01`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the energy‐performance results from your latest EPC records (expressed in kWh per m² and annual):  

• Building ID 01-80-HAELSAN10-2 (version 2010) – Specific energy use: 155 kWh/m²·year; Energy class E  
• Building ID 05-83-RAEFSAN5-1 (version 2023) – Specific energy use: 75 kWh/m²·year; Energy class C  
• Building ID 17-81-BONDEN9-1 (version 2019) – Specific energy use: 109 kWh/m²·year; Energy class D  

The “latest” certificate for each building is the most recent version shown above.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[4]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[3]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[0]`
- notes: "your latest EPC records" covers three different buildings (155/E, 75/C, 109/D — each attested for its own building). faith 4: figures real, possessive framing false. entity 3: all three presented as the user's. pass 0.

---

## Case 13 / 65 — `52261dfa`
**Expected route:** `building_specific`
**Building the system identified:** `19-84-LEOPARDEN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the latest Energy Performance Certificates (EPC) in your data, your buildings have the following specific energy use and energy class:

1. Building ID 19-84-LEOPARDEN5-1 (Leoparden 5)  
   • Specific energy use (“energiprestanda”): 190 kWh/m²·year  
   • Primary energy number (“egiprimarenergital”): 127 kWh/m²·year  
   • Latest energy class (2020 calculation): E  

2. Building ID 01-80-HAELSAN10-2 (Hälsan 10)  
   • Specific energy use (“energiprestanda”): 183 kWh/m²·year  
   • Primary energy number (2020 calculation): 133 kWh/m²·year  
   • Latest energy class (2020 calculation): E  

In other words, both buildings consume roughly 180–190 kWh per square metre and, under the current Swedish energy-classification scale, fall into class E.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[4]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[3]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[0]`
- notes: "your buildings" presents HAELSAN's 183/133/E beside the named LEOPARDEN's 190/127/E, then summarises them jointly ("both buildings consume roughly 180–190"). entity 3: possessive leak of a second building. calib 3: no hedge about its provenance.

---

## Case 14 / 65 — `ca379df4`
**Expected route:** `building_specific`
**Building the system identified:** `17-81-BONDEN9-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the key energy‐performance figures for your building (byggnadsid 17-81-BONDEN9-1):

• Energy performance (EPC “energiprestanda”): 109 kWh/m² per year (latest, 2019/2020)  
• Energy class: D (both EPC 2016 and EPC 2020 calculations)  
• Specific energy use (including heating and hot water): 82 kWh/m² per year  
• Total annual delivered energy: 22 914 kWh  
• Main heating system: Air-to-water heat pump  

In summary, your building’s energy performance metric is 109 kWh/m² year, placing it in class D.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: BONDEN answered as BONDEN: 109 D, spec 82, 22 914 kWh, A2W HP — all attested, internally coherent (82x280≈22 960). Clean block.

---

## Case 15 / 65 — `55551dbf`
**Expected route:** `building_specific`
**Building the system identified:** `17-81-BONDEN9-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the latest EPC (version 3.0, approved 19 September 2024) for building 05-83-RAEFSAN5-1 (Rådmansgatan 31, Motala), your building’s energy performance is as follows:

• Energy performance (EPC “energiprestanda”): 75 kWh/m²·year  
• Energy class: C  
• Specific energy use (“specifik energianvändning”): 42 kWh/m²·year  
• Total delivered energy (“egienergianvändning”): 7 171 kWh/year  
• Primary energy use (“primärenergianvändning”): 12 908 kWh/year  
• Energy index: 1.03 (relative to reference value)  
• Main heating system: air-to-water heat pump  
• District heating consumption: 0 kWh  

In EPC terminology:  
– epc_egienergiklass = C  
– epc_egienergiprestanda = 75  
– epc_egispecifikenergianvandning = 42  

This is the most recent and correct energy classification for your building.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[2]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[1]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap (named BONDEN, answers RAEFSAN v3.0 2024) aggravated by the closing "This is the most recent and correct energy classification for your building". faith 2 / calib 1: asserting correctness of a misattributed record is the worst calibration failure in the form besides case 34.

---

## Case 16 / 65 — `75107c23`
**Expected route:** `building_specific`
**Building the system identified:** `05-83-RAEFSAN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the key energy‐performance figures for your building (byggnadsid 05-83-RAEFSAN5-1):

• Energy performance (EPC “energiprestanda”): 75 kWh/m²·year  
• Energy class (EPC “energiklass”): C  
• Specific energy use (EPC “specifik energianvändning”): 42 kWh/m²·year  
• Total annual energy use (EPC “energianvändning”): 7 171 kWh/year  
• Primary energy use (EPC “primärenergianvändning 2020”): 12 908 kWh/year  
• Main heating system: Electric heating (elvärme)  

In summary, your building’s certified energy performance is 75 kWh/m²·year, placing it in energy class C.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[7]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: Energy-performance figures all match RAEFSAN and cohere (75 C, 42, 7 171≈171x42, 12 908). faith 7: "Main heating system: Electric heating (elvärme)" contradicts the air-to-water heat pump this building has in five other blocks. calib 7 for asserting it flatly. pass 1: the asked topic (performance) is answered correctly; heating is ancillary here.

---

## Case 17 / 65 — `6b8d772d`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-FILOSOFEN2-4`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the key figures for your building (ID 01-80-FILOSOFEN2-4) from the latest energy performance certificate:

• Energy Performance (primary energy index): 60 kWh/m²·year  
• Energy Class: B  
• Specific energy use (heating, domestic hot water, common electricity): 64 kWh/m²·year  
• Total annual energy use: 149 478 kWh  
• Main heating system: District heating

In other words, your building’s rated primary energy demand is 60 kWh per square metre and it falls in class B—well above the Swedish median in the latest classification.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[7]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: Figures match FILOSOFEN2-4 (60 B, spec 64, 149 478 — reconciles with case-64's DH split plus electricity). faith 7 / calib 7: "well above the Swedish median" is an unsupported and confusingly worded comparison.

---

## Case 18 / 65 — `48bf1c7d`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
According to the latest energy performance certificate for your building (ID 05-83-RAEFSAN5-1, EPC version 3.0 approved 2024-09-19), the primary heating system is an air-to-water heat pump (“pump luft–vatten”). There is no district heating connection (district_heating_use = 0).

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap: named HAELSAN, answers RAEFSAN (v3.0 approved 2024-09-19, A2W HP, DH=0 — all attested for RAEFSAN). Coherent record, false attribution. rel 10: topic answered; entity 0 carries the failure.

---

## Case 19 / 65 — `ff4b8820`
**Expected route:** `building_specific`
**Building the system identified:** `05-83-RAEFSAN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
For your building (ID 01-80-HAELSAN10-2), the latest energy declaration shows:

• Specific energy use (Energiprestanda): 155 kWh/m²·year  
• Energy class (2020 calculation): E  

In other words, your building consumes 155 kWh of delivered energy per square metre and is rated in class E.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap: named RAEFSAN, answers "For your building (ID 01-80-HAELSAN10-2)" with HAELSAN's attested 155/E. entity 0, calib 2.

---

## Case 20 / 65 — `ed8cb1c9`
**Expected route:** `building_specific`
**Building the system identified:** `19-84-LEOPARDEN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the energy performance certificates in your data, you have two buildings recorded:

1. Building ID 19-84-LEOPARDEN5-1 (Leoparden 5, Arboga)  
   • Energy performance (EPC “egienergiprestanda”): 190 kWh/m²·year  
   • Energy class: E  

2. Building ID 01-80-HAELSAN10-2 (Hälsan 10, Stockholm)  
   • Energy performance (EPC “egienergiprestanda”): 183 kWh/m²·year  
   • Energy class: E  

If you are asking about “your building,” please check which of these two IDs applies. The figures above reflect the latest available certificates.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[4]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[3]`
- calibration (0–10): `[5]`
- overall_pass (0 or 1): `[0]`
- notes: "you have two buildings recorded" is the same false possessive as cases 2/12/13, so faith 4 and entity 3 to match them — both buildings again at "Tulegatan 5A" in different cities. calib 5: the ask-to-pick is the same hedge as cases 9/48, no better, about an ambiguity the system had resolved. pass 0: answers nothing.

---

## Case 21 / 65 — `515ae70b`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
For the building with ID 17-81-BONDEN9-1 (the first record in your data), the latest Energy Performance Certificate (EPC) shows:  
• Energy performance (egienergiprestanda): 109 kWh/m²·year  
• Energy class (egienergiklass): D  
• Primary energy use (egiprimarenergital2020): 112 kWh/m²·year  
• Total annual energy use (egienergianvandning): 22 914 kWh  

The main heating system is an air-to-water heat pump.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[1]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[0]`
- notes: Named HAELSAN; answers for "17-81-BONDEN9-1 (the first record in your data)" and never addresses the user's building. BONDEN figures mostly attested, but "primärenergital 2020: 112" sits oddly beside 109 (case 60: 30 552/280≈109, and energiprestanda IS the primary figure). entity 1: the "first record" framing is the only thing above 0.

---

## Case 22 / 65 — `cb472b05`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
The energy performance data for your building (Building ID 01-80-HAELSAN10-2) shows that its primary heating system is district heating (“fjärrvärme”). Key details:  
• Connected network: Stockholm district heating  
• Annual district-heating use for space heating: 819 500 kWh  
• Annual district-heating use for domestic hot water: 161 300 kWh  

No other fuel types (oil, gas, biomass, heat pumps, etc.) contribute to the building’s space-heating system.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: District heating via Stockholm with 819 500/161 300 matches the HAELSAN 2019-cert family (cases 48, 50). calib 7: "No other fuel types ... contribute" is a flat universal, though consistent with the record shown.

---

## Case 23 / 65 — `6829d863`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-FORSKNINGEN1-44`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
The energy declaration shows that your building’s sole heating system is district heating (“fjärrvärme”), supplied via the Stockholm network. According to the latest EPC calculation:

• Main heating source: District heating (“Fjärrvärme”)  
• District-heating consumption for space heating: 118 576 kWh/year  
• District-heating consumption for hot water (VV): 45 056 kWh/year  

No other heating sources (oil, gas, heat pumps, biomass etc.) are used.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: Figures (DH Stockholm, 118 576 space + 45 056 DHW) match FORSKNINGEN1-44 in cases 31 and 47. entity 9 only because the answer never restates the building ID. calib 7 for the flat "No other heating sources" universal.

---

## Case 24 / 65 — `e6cb7942`
**Expected route:** `building_specific`
**Building the system identified:** `05-83-RAEFSAN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
For building 05-83-RAEFSAN5-1 (Räfsan 5, Kristinehamn), the latest energy performance certificate (version 3.0, approved 2024-09-19) shows:

• Energy class: C  
• Primary energy performance (egienergiprestanda): 75 kWh/m²·year  
• Specific energy use (heating + domestic hot water): 42 kWh/m²·year  
• Total annual energy use: 7 171 kWh  
• Main heating system: air-to-water heat pump  

This means your building’s primary energy demand is 75 kWh per square metre per year, placing it in class C.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[6]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[7]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: All asked-for figures correct for RAEFSAN (C, 75, 42, 7 171, A2W HP), but "Räfsan 5, Kristinehamn" is the wrong municipality: four other blocks say Motala, and the 05- county prefix is Östergötland — Kristinehamn belongs to BONDEN (17-). entity 7 for one borrowed locality. pass 1 is a judgement call: every figure the user asked about is right.

---

## Case 25 / 65 — `12df980d`
**Expected route:** `generic`

### Question

> How should a BRF board prioritize energy measures when the budget is limited?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
When funds are tight, the board’s goal is to get the biggest “bang for the krona” by tackling measures with the highest energy‐savings per cost and shortest pay-back time first. A typical five-step approach looks like this:

1. Secure an energy overview  
   • Order a targeted energy audit (or consult your most recent Energy Performance Certificate – EPC) to identify where heat and electricity are lost most.  
   • Map out both building-wide systems (heating, ventilation, lighting) and the building envelope (walls, windows, roof).

2. Identify and implement “no-regret” measures  
   • Adjust and optimize controls: thermostat set-points, radiator balancing, ventilations schedules.  
   • Upgrade to LED lighting in common areas and boiler rooms.  
   • Seal obvious air leaks around doors, windows and service penetrations.

3. Tackle quick-pay-back retrofits  
   • Improve attic or roof insulation, where accessible, since loose-fill or batt insulation is relatively low cost.  
   • Install draft strips or tighter glazing seals on windows.  
   • Add simple heat-recovery units in laundry or drying rooms if the audit flags excessive ventilation losses.

4. Plan medium- and long-term renovations  
   • Once cash flow allows, phase in façade insulation or exterior wall retrofits, which deliver bigger savings but require more capital.  
   • Schedule window replacements or upgrades in bundles (e.g. 10–20% of the building each year) to spread cost.  
   • Consider upgrading to a modern, high-efficiency boiler or heat‐pump system when your existing plant nears end of life.

5. Leverage financing and subsidies  
   • Apply for national or municipal grants, “gröna lån” (green loans) or tax deductions available for energy-saving measures.  
   • Explore partnering with an energy‐service company (ESCO) that can carry up-front costs in return for sharing savings.  
   • Build a renovation fund in your annual budget so you’re ready for the next phase.

By focusing first on low-cost, high-impact actions and then rolling out larger upgrades in stages—while tapping every available grant or green loan—you ensure steady progress toward a more energy-efficient, sustainable BRF without over-stretching your budget.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: no evidence, no building. Sound, well-sequenced generic BRF advice with no invented specifics. calib 6: the trailing "building details may be outdated" note is boilerplate that does not belong on a question that used no building data.

---

## Case 26 / 65 — `4caf9094`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the key figures from your building’s most recent energy performance certificate (EPC):  
• Building ID: 01-80-HAELSAN10-2 (Rådmansgatan 31, Stockholm)  
• Latest energy class (2020): E  
• Energy performance (energiprestanda): 155 kWh/m²·year  
• Specific energy use (epc_egispecifikenergianvandning): 155 kWh/m²·year  
• Total annual energy use (epc_egienergianvandning): 1 050 964 kWh  
   – District heating: 946 000 kWh  
   – Domestic hot water: 113 520 kWh  
   – Electricity (common areas): 12 000 kWh  
• Primary energy factor (epc_egiprimarenergital2020_calc): 110  
• Energy index (epc_energiindex_calc): 1.11  

In summary, your building consumes 155 kWh per square metre each year and is rated in energy class E under the latest (2020) EPC.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[6]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: Right entity, headline right (155/E; 1 050 964/6 798≈155 coheres). faith 6: the carrier breakdown double-counts — case 28 shows the 946 000 already contains the 113 520 DHW — and 946 000+113 520+12 000=1 071 520 ≠ the stated 1 050 964; "primary energy factor 110" also mislabels primärenergital. calib 7 for asserting the broken breakdown.

---

## Case 27 / 65 — `c2de1875`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-FILOSOFEN2-3`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
According to the latest (2023) Energy Performance Certificate for building 01-80-FILOSOFEN2-3 (approved 2024-08-28):

• Energy performance (primary energy index): 63 kWh/m²·yr  
• Specific energy use (delivered energy): 71 kWh/m²·yr  
• Total annual energy consumption: 167 524 kWh  
• Primary energy use: 149 037 kWh  
• Main heating system: district heating (79 330 kWh/yr)  
• Assigned energy class: B

In other words, your building consumes 63 kWh of primary energy per square metre and is rated in energy class B.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: FILOSOFEN2-3 answered as itself with no warts: 63 primary/B vs 71 delivered correctly distinguished, totals cohere (167 524/71 ≈ 2 360 m² ≈ 149 037/63), a single cert family so "latest" is uncontested, and no universal claims. faith 9 — same tier as cases 1/14/52.

---

## Case 28 / 65 — `5028044f`
**Expected route:** `building_specific`
**Building the system identified:** `05-83-RAEFSAN5-1`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the energy-performance data for your property (byggnadsid 01-80-HAELSAN10-2), the building’s main heating system is district heating. It is supplied via the Stockholm district-heating network, with an annual measured heat delivery of 946 000 kWh (approximately 832 480 kWh for space heating and 113 520 kWh for domestic hot water).

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap: named RAEFSAN, answers "your property (byggnadsid 01-80-HAELSAN10-2)" with HAELSAN's attested DH figures (946 000 = 832 480+113 520). Coherent record, wrong owner. entity 0.

---

## Case 29 / 65 — `62ff35d2`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Building ID: 01-80-HAELSAN10-2  
Address: Rådmansgatan 31, Stockholm  

According to the most recent energy performance certificate (EPC version 1.4, approved 2008-12-30), your building’s key figures are:

• Heated floor area (Atemp): 6 798 m²  
• Specific energy use (EPC energy performance, “egienergiprestanda”): 155 kWh/m²·year  
• EPC energy class (2020 calculation): E  
• Energy index (measured vs. reference): 1.110 (i.e. 11 % above the reference value)  
• Reference value for this building type (“egirefvarde1”): 110 kWh/m²·year  
  – Acceptable range (“egirefvarde2min”–“egirefvarde2max”): 116–142 kWh/m²·year  
• Energy carriers (annual totals):  
  – District heating: 946 000 kWh  
  – Electricity (common areas): 12 000 kWh  

In summary, your building consumes 155 kWh per square metre and is classified in energy class E, reflecting a consumption level somewhat above the target reference for similar multi-family buildings.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[6]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: Right entity, rich detail, headline attested (155/E, Atemp 6 798). faith 6: listed carriers (946 000+12 000=958 000) fall ≈95 000 kWh short of 155x6 798≈1 053 690, and the stated reference 110 lies outside its own 116–142 range while index 1.11 implies a ≈140 reference. pass 1 stands on the correct headline figures.

---

## Case 30 / 65 — `d2379cce`
**Expected route:** `combined`
**Building the system identified:** `01-80-FILOSOFEN2-2`

### Question

> What should my building prioritize to improve energy efficiency?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on your latest energy performance certificate (EPC), your multi‐family building in Stockholm currently sits in energy class B with  
• Specific energy use (Eindex) of 59 kWh/m²·year  
• Total delivered energy of 158 134 kWh (heating 59 545 kWh, hot water 66 600 kWh, electricity 28 436 kWh)  
• Primary energy index of 53 kWh/m²·year  

While class B is good for a building of this size and age (built 2020), moving toward class A (and reducing your energy bills and carbon footprint) will require targeted measures. We recommend prioritizing the following areas:

1. Building envelope improvements  
   – Review and upgrade insulation levels in external walls, roof and basement ceilings to reduce heat losses.  
   – Inspect window and door seals; consider high-performance glazing or better gaskets to cut transmission and draughts.  
   – Address thermal bridges around balconies, slab edges and other junctions.

2. Domestic hot water efficiency  
   – Hot‐water production accounts for roughly 42 % of your delivered energy (66 600 kWh). Install low-flow taps and showerheads, and consider local heat‐pump pre-heating or solar thermal collectors to offset district-heating use.  
   – Conduct a pipework insulation audit to eliminate distribution losses.

3. Ventilation system optimization  
   – You already have a mechanical supply/extract system with heat recovery (FTX), but look for opportunities to balance and fine-tune airflow rates. Reducing over-ventilation will lower heat demand without impacting indoor air quality.  
   – Ensure heat‐recovery units and ductwork are clean and well-maintained; a clogged heat exchanger can drop efficiency significantly.

4. Renewable heating technologies  
   – Explore the feasibility of supplementing district heating with a shared ground-source (geothermal) or air-source heat pump. Even partial cover of space‐heating demand can lower your primary energy factor.  
   – Investigate purchase of “green” district heating or heat mix contracts from your supplier.

5. Common‐area electricity reductions  
   – Lighting, fans and pumps in corridors, stairwells and technical rooms account for a portion of your 28 436 kWh electricity use. Upgrade to LEDs with presence sensors and install variable-speed drives on circulation pumps.  
   – Implement a basic building management system (BMS) or smart‐metering feedback so you can track consumption in real time and spot unexpected peaks.

6. Resident engagement and monitoring  
   – Encourage tenants to adopt energy-saving behaviour: temperature set-back in unused apartments, efficient appliance use, reporting of draughts.  
   – Set up regular energy‐use reporting (quarterly or monthly) to gauge the impact of each measure and adjust your retrofit plan accordingly.

By tackling the building envelope first, then reducing hot-water and ventilation losses, and finally introducing renewable heat and smarter controls, you will achieve the most cost-effective move toward class A performance (below roughly 45 kWh/m²·year) and long-term energy‐cost savings.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[4]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[1]`
- notes: EPC figures match FILOSOFEN2-2, but: the breakdown 59 545+66 600+28 436=154 581 ≠ stated total 158 134; "built 2020" and "You already have FTX" are asserted while the answer's own note lists ventilation and declaration year as unconfirmed; the "42 % hot water" share is computed from the broken total. faith 4, calib 3. pass 1: entity-correct and the advice itself is sound.

---

## Case 31 / 65 — `72931f43`
**Expected route:** `combined`
**Building the system identified:** `01-80-FORSKNINGEN1-44`

### Question

> What should my building prioritize to improve energy efficiency?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on your latest EPC (energy class D, primary energy performance 79 kWh/m²·year) and the breakdown of consumption (heating ~62 %, domestic hot water ~23 %, electricity ~15 %), you should prioritize measures in the following order:

1. Optimize HVAC and heat‐recovery ventilation  
   • Verify and balance your FTX system (venttyp FTX = Ja) to ensure the highest possible heat‐recovery efficiency.  
   • Commission and tune control settings (airflows, set‐back schedules) so you neither over‐ventilate nor lose recovered heat.  

2. Reduce space‐heating demand  
   • Perform an airtightness test and seal detected leaks around windows, doors and service penetrations.  
   • Inspect thermal bridges (e.g. at balconies or slab edges) and add insulation or thermally broken profiles where feasible.  

3. Lower domestic hot‐water consumption  
   • Install low‐flow taps, showerheads and timers in showers to cut demand.  
   • Consider pre‐heating via solar‐thermal panels to offset roughly 45 000 kWh/year of hot‐water heating currently supplied by district heat.  

4. Integrate renewable or lower-carbon heating sources  
   • Explore replacing part of your district-heating load (118 576 kWh/year) with a high-efficiency heat pump (ground or air-source) or hybrid system.  
   • Add photovoltaic panels (currently none) to reduce grid-supplied electricity (28 000 kWh/year).  

5. Improve lighting and plug‐load efficiency  
   • Switch any remaining fluorescent or halogen fixtures to LEDs and fit occupancy/motion sensors in classrooms and corridors.  
   • Upgrade to energy-efficient appliances and set computers/light controls to automatic shut-off after hours.  

6. Implement an energy-management system (BEMS)  
   • Monitor sub-metered consumption (heating, cooling, DHW, plug loads) to identify peak-use periods and abnormal losses.  
   • Use scheduled setbacks and remote monitoring to drive continuous performance improvements.  

By focusing first on HVAC commissioning and envelope tightening (the largest energy end‐uses), and then addressing hot water, renewables and controls, you can move your building toward energy class C—or better—while lowering both operating costs and carbon emissions.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[1]`
- notes: Consumption split (62/23/15) and figures (79/D, 118 576 DH, ≈28 000 elec) match FORSKNINGEN across blocks. faith 5 / calib 3: asserts "venttyp FTX = Ja", "currently none" for PV, and the electricity figure — all inside its own unconfirmed list; case 55 even reports "ventgrupp godkänd: Nej" for this building, so the FTX claim is doubly doubtful.

---

## Case 32 / 65 — `2647dbf4`
**Expected route:** `combined`

### Question

> What should our building prioritize if we want to reduce heating costs?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
To reduce your building’s heating costs most effectively, you should focus on improving its overall energy performance—ideally as measured by the EPC energy class (epc_egienergiklass). In practice, the key priorities are:  

1.  Upgrade the building envelope  
    •  Roof insulation: Because heat always rises, upgrading or adding insulation in the attic or roof space typically offers the highest return on investment.  
    •  Wall insulation: For uninsulated or poorly insulated walls, consider cavity‐fill insulation or an external/internal insulation system.  
    •  Floor insulation: Insulating over unheated basements or crawl spaces stops cold bridging from below.  

2.  Improve window and door performance  
    •  Install high‐performance, double‐ or triple‐glazed windows with low-emissivity coatings.  
    •  Ensure doors and windows are properly sealed with weather-stripping to reduce drafts and infiltration.  

3.  Enhance airtightness  
    •  Locate and seal leaks around service penetrations, junctions, and transitions (e.g. between wall and floor or around window frames).  
    •  Conduct a blower-door test to identify and systematically eliminate air leaks.  

4.  Optimize mechanical systems  
    •  Heat recovery ventilation (HRV): A balanced ventilation system with heat recovery will supply fresh air while recapturing heat from outgoing stale air.  
    •  High‐efficiency heating source: Consider replacing an old boiler with a modern condensing boiler or a ground/air-source heat pump.  
    •  Smart controls and thermostatic radiators: Zoning and programmable thermostats let you heat only occupied areas when needed.  

5.  Review heated floor area (Atemp) and usage patterns  
    •  epc_egenatemp tells you the total heated volume—if certain wings or floors are little used, consider zoning them off or reducing their setpoint.  
    •  Adjust thermostat setbacks for nights and unoccupied periods to cut wasted energy.  

6.  Seek an updated EPC assessment  
    •  A new energy performance certificate will quantify the impact of any measures you’ve taken and guide you toward the next most cost-effective upgrade.  

By tackling the building envelope first (insulation, airtightness, windows) and then optimizing ventilation and heating systems, you’ll achieve the greatest reduction in heating costs per krona invested—and most likely improve your EPC energy class in the process.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[7]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: no evidence retrieved, none invented — correct behaviour, and the advice is sound. faith 7 for a concrete error: it calls epc_egenatemp "the total heated volume" — Atemp is heated floor AREA — plus raw schema names (epc_egienergiklass, epc_egenatemp) leaked into user-facing prose. calib 6 for presenting field jargon as if the user knew it.

---

## Case 33 / 65 — `08573eae`
**Expected route:** `generic`

### Question

> What is an energy declaration?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
An energy declaration (often called an Energy Performance Certificate or EPC) is an official, standardized report that describes a building’s energy performance and efficiency. In Sweden, for example, any newly constructed, sold or rented building must have an energideklaration performed by an accredited energy inspector. Key elements of the declaration include:  
• An energy class rating (epc_egienergiklass) on a scale from A (most efficient) to G (least efficient)  
• Estimated annual energy consumption for heating, hot water and, where relevant, electricity for common areas  
• Technical building data such as heated floor area (Atemp), building type, number of floors and stairwells  
• A list of recommended energy-saving measures with cost and energy-saving estimates  
• Validity period (typically ten years)  

The purpose of the energy declaration is to give building owners, buyers and tenants clear, comparable information on a building’s energy use and to highlight cost-effective ways to reduce consumption, lower operating costs and improve indoor comfort.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: definitional question. Accurate on Swedish specifics (accredited inspector, ten-year validity, A–G scale, recommended-measures list). calib 6 for the irrelevant building-data note and one schema-name leak.

---

## Case 34 / 65 — `41c99bbf`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
According to the most recent energy declaration for your building (Building ID 05-83-RAEFSAN5-1, approved 2024-09-19):  
• Energy performance (EPC “energiprestanda”): 75 kWh/m²·year  
• Energy class: C (latest classification)  
• Heated floor area (Atemp): 171 m²  
• Total annual delivered energy: 7 171 kWh (heating + hot water)  
• Primary energy use: 12 908 kWh/year  

In other words, your detached house in Motala consumes about 75 kWh per square metre each year and is rated in energy class C.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[2]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[1]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap (named HAELSAN, answers RAEFSAN) escalated by invented personal detail: "your detached house in Motala". Coherent RAEFSAN figures do not rescue a confidently personalised misattribution. faith 2, calib 1.

---

## Case 35 / 65 — `4fdcac0c`
**Expected route:** `generic`

### Question

> What is load balancing for EV charging?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
Load balancing for electric-vehicle (EV) charging is the method by which available electrical power is distributed intelligently among one or more charging points to

• Prevent overloads on a building’s electrical installation or on the local grid  
• Maximize utilization of the available capacity  
• Ensure each vehicle receives a fair or prioritized share of power  

How it works  
1. Capacity monitoring  
  – A central controller or smart meter continuously measures total current draw and compares it with the maximum allowable capacity of the service connection or sub-feed.  
2. Power allocation  
  – If multiple EVs plug in simultaneously, the controller dynamically adjusts the charging current to each point so that the sum does not exceed the predefined limit.  
  – Charging currents can be reduced or increased in real time as vehicles start, finish or change their charging rate.  
3. Priority settings (optional)  
  – Some systems allow you to assign priorities—for example, a company fleet vehicle may be given higher charging priority than a visitor’s car.  
4. Integration with building energy management  
  – Load-balancing controllers can be tied into the building’s energy-management system or to on-site renewable sources (solar, battery storage) to further optimize costs and self-consumption.  

Types of load balancing  
• Static load balancing  
  – Power limits are set in advance for each charger. Distribution does not adjust in real time—when one charger reaches its limit, others pick up the slack.  
• Dynamic load balancing  
  – Real-time measurement and adjustment. The most common approach in modern EV-charging installations.  

Key benefits  
• Safety—avoids tripping breakers or fuses by keeping total draw within safe limits  
• Cost efficiency—allows more chargers to be installed on a given supply without costly grid upgrades  
• User experience—minimizes “no-charge” situations and reduces waiting times by sharing power fairly  
• Grid friendliness—smooths out peaks in demand, which can reduce energy costs and stress on the local distribution network  

In summary, load balancing is a crucial feature in any multi-point EV charging installation, ensuring reliable, efficient and safe distribution of electrical power.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic EV question. Accurate and complete (static vs dynamic balancing, priority schemes, BMS integration). calib 6 only for the irrelevant trailing building-data note.

---

## Case 36 / 65 — `d5919375`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-NORRADJURGAARDEN1:49-34`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
The building’s primary heating system is direct electric heating (“ElDirekt”). No district heating, gas, oil, biomass or heat‐pump systems are installed.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: "ElDirekt" matches this building's record in cases 42/45/59. entity 9: nothing foreign, but no ID restated and no figures offered. calib 7: flat "No district heating, gas, oil, biomass or heat-pump systems" universal.

---

## Case 37 / 65 — `fe52dc22`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
For building 01-80-HAELSAN10-2 (Hälsan 10), the latest Energy Performance Certificate (2010 version) reports:

• Energy performance (Energiprestanda): 155 kWh/m²·year  
• Energy class: E  
• Primary energy indicator (Primärenergital 2020): 110 kWh/m²·year  
• Heating system: district heating  

This means your building consumes 155 kWh of delivered energy per square metre annually, placing it in class E on the national scale.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: HAELSAN 155-family quoted consistently (155/E, primary 110, DH) and honestly version-stamped "(2010 version)". faith 8 not 9 for exactly one reason: it still calls the 2010 certificate "latest" while case 48 attests a 2019 HAELSAN cert (v2.6, approved 2019-06-02) — the currency claim is contested within the form.

---

## Case 38 / 65 — `f29cfcf5`
**Expected route:** `generic`

### Question

> Where is the boundary between normal EV charging and fast charging?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
In practice, the cut-off between “normal” (sometimes called slow or AC) charging and “fast” charging is almost always set at 22 kW:

•  Normal (AC) charging  
   – Typically up to 22 kW.  
   – In Europe most home and public AC chargers are 3.7–22 kW (single- or three-phase).  
•  Fast charging  
   – Defined as any charging point rated at 22 kW or above.  
   – On AC this is still up to ~43 kW in some installations, but by convention 22 kW+ is “fast.”  
   – DC fast chargers generally start at around 50 kW and today commonly range from 50 kW up to 350 kW or more.

Different standards bodies or countries may subdivide further (e.g. “rapid” for ≥50 kW DC or “ultra-rapid” >150 kW), but the industry consensus for the normal-vs-fast boundary is 22 kW.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic. 22 kW is a defensible conventional boundary and the DC 50–350 kW range is right. faith 8: "industry consensus" overstates, and the "on AC still up to ~43 kW" aside muddies its own definition. calib 6.

---

## Case 39 / 65 — `b32dc974`
**Expected route:** `building_specific`
**Building the system identified:** `19-84-LEOPARDEN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the latest energy performance certificate (EPC) for your property (Building ID 19-84-LEOPARDEN5-1, Tulegatan 5A in Arboga), the key figures are as follows:

• Energy class (2020): E  
• Specific energy performance: 190 kWh/m²·year  
• Total annual delivered energy use: ≈158 000 kWh (all fuels)  
• Energy index: 1.18 (ratio of actual use to reference value)  
• Main heating system: wood‐chip boiler (“Flis”)

In other words, your building consumes about 190 kWh per square metre and falls into class E under the current Swedish classification.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[6]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[7]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: Headline correct for LEOPARDEN (190/E; Flis wood-chip matches case 48), but "total ≈158 000 kWh" equals FILOSOFEN2-2's 158 134 — a foreign total presented as this building's. faith 6, entity 7 for that one borrowed figure. pass 1 on the strength of the asked-for headline.

---

## Case 40 / 65 — `4f4765c8`
**Expected route:** `building_specific`
**Building the system identified:** `05-83-RAEFSAN5-1`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the latest energy‐declaration data for your property (byggnadsid 17-81-BONDEN9-1), the building’s primary heating system is an air-to-water heat pump. No district-heating consumption was recorded.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap: named RAEFSAN, answers "your property (byggnadsid 17-81-BONDEN9-1)". The substance (A2W HP, no DH) happens to be true of both buildings, so the user would never notice — which is precisely why entity 0 and pass 0: the pipeline got the right answer by coincidence, not by grounding.

---

## Case 41 / 65 — `f8070b0d`
**Expected route:** `generic`

### Question

> Is lowering the indoor temperature a useful energy-saving measure?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
Yes. Turning your thermostat down by just a few degrees is one of the simplest—and most cost-effective—ways to reduce your building’s energy use and heating costs. Here’s why and how it works:

1. Energy and cost savings per degree  
   • On average, lowering your indoor temperature by 1 °C reduces heating energy consumption by about 5 %.  
   • If your annual heating bill is, say, 15 000 SEK, a 1 °C setback could save roughly 750 SEK per year.  

2. Impact on carbon emissions  
   • Less fuel or electricity for heating directly means lower CO₂ emissions.  
   • Especially in buildings with poor insulation or high heated floor area (Atemp), the relative savings are even more pronounced.

3. Recommended temperature settings  
   • Living areas: 19–21 °C  
   • Bedrooms: 16–18 °C (you sleep better in a cooler room)  
   • When unoccupied (work, vacation): setback to 15–17 °C

4. Best practices for implementation  
   • Programmable thermostat or smart controls: set different temperatures for day/night or weekday/weekend.  
   • Radiator thermostatic valves (TRVs): allow room-by-room control, so you only heat the spaces you use.  
   • Night setback: drop the temperature automatically during sleeping hours.  
   • Combine with good insulation and airtightness: the better insulated the building, the longer it holds heat when the thermostat is turned down.

5. Comfort considerations  
   • If occupants feel chilly, consider supplemental measures such as warmer clothing or localized heating (e.g. heated towel rails in bathrooms).  
   • Avoid large fluctuations: rapid temperature swings can increase energy use and reduce comfort.

In summary, lowering the indoor temperature—even by just 1 °C—is a proven, easy-to-implement energy-saving measure that reduces running costs and carbon emissions. For best results, integrate it with proper building insulation, programmable controls and room-by-room temperature regulation.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic. The 5 %-per-degree rule is the standard planning figure and the SEK example is framed as illustrative; temperature ranges are sensible. calib 6 for the irrelevant trailing note.

---

## Case 42 / 65 — `7ea587b1`
**Expected route:** `combined`
**Building the system identified:** `01-80-NORRADJURGAARDEN1:49-34`

### Question

> What should my building prioritize to improve energy efficiency?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on your building’s latest energy performance certificate (EPC), it is currently in energy class G with a very high specific energy use (108 kWh/m²·yr, reference is 55 kWh/m²·yr). The vast majority of your delivered energy (95 %) goes to electric direct heating (23 445 kWh) and domestic hot water (115 kWh). To make the biggest impact on your energy efficiency, you should prioritize:

1. Heating-system upgrade  
   • Replace direct-electric heating with a more efficient system – for example an air-source or ground-source heat pump – which can cut primary energy use by 50–70 %.  
   • If you have access to district heating, compare its primary energy factor to a heat pump and consider connecting.

2. Building-envelope improvements  
   • Insulate external walls, roof and (if applicable) ground floor slab to achieve U-values below 0.20 W/m²·K.  
   • Replace or upgrade windows and doors to low-energy units (U-value ≤ 1.2 W/m²·K), with good airtightness and low-e coatings.  
   • Seal thermal bridges and eliminate drafts – reducing uncontrolled ventilation losses has a direct effect on heating demand.

3. Ventilation with heat recovery  
   • Your current ventilation installation is not approved for heat recovery (no FTX). Installing a balanced mechanical ventilation system with ≥ 70 % heat recovery will significantly reduce winter heat losses.  
   • Ensure the system is commissioned correctly and filters are maintained to preserve performance.

4. On-site renewable energy  
   • Solar photovoltaics can offset your building’s high electricity consumption – even a small PV array can reduce grid-purchased electricity by 10–20 %.  
   • Solar thermal collectors for pre-heating domestic hot water can further lower your hot‐water energy use.

5. Fine-tuning operations and controls  
   • Install smart thermostats or building management controls to avoid overheating and to optimize flow temperatures in the heating system.  
   • Fit low-flow taps and showers to reduce hot‐water consumption (currently 0.5 L/m²·yr).

By tackling first the heating system and envelope, then adding ventilation heat recovery and renewables, you should be able to move from class G toward class C–D and cut your energy consumption by at least 40–60 %.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[4]`
- overall_pass (0 or 1): `[1]`
- notes: G-class figures match the record (108 vs reference 55, 23 445 elec heating, 115 DHW). faith 5: asserts "ventilation ... not approved for heat recovery (no FTX)" against its own unconfirmed list, the "95 %" split is really ≈100 %, and "0.5 L/m²·yr" garbles a kWh figure (case 45 has it as kWh). calib 4: one disclaimed assertion, otherwise framed as recommendations.

---

## Case 43 / 65 — `fd29f67e`
**Expected route:** `generic`

### Question

> What is the difference between a grid contract and an electricity supply contract?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
In most countries (including Sweden), you need two separate agreements to get power into your home or business: a grid (network) contract and an electricity supply contract. They cover different services and are made with different companies.  

1. Grid Contract (Nätavtal)  
   • Counterparty: your local grid or distribution system operator (DSO) – the company that owns and maintains the poles, wires and transformers in your area.  
   • Purpose: grants you a physical connection to the electrical network and ensures safe delivery of power.  
   • Charges:  
     – A fixed monthly fee or capacity fee for your contracted load (kW) or service level.  
     – A variable network fee based on how much electricity you draw (kWh).  
   • You cannot opt out of a grid contract if you want electricity—you must have one to receive any power.  

2. Electricity Supply Contract (Elhandelsavtal)  
   • Counterparty: an electricity retailer or supplier—often a different company than the DSO.  
   • Purpose: covers the purchase of the energy itself (the kWh you consume). You choose the supplier, price model and contract length.  
   • Common price models:  
     – Fixed‐price contract: you pay the same kWh price for the entire contract period.  
     – Variable/spot‐price contract: your price follows the hourly or daily market (Nord Pool) price plus a supplier margin.  
     – Mix or indexed contracts combining elements of both.  
   • You can switch suppliers (and thus your supply contract) without changing your physical connection or grid contract.  

Key takeaway  
• The grid contract ensures your building is connected to and serviced by the electrical network—its fees cover infrastructure and delivery.  
• The supply contract covers the commodity (electricity) you actually consume.  
You need both agreements in place: one to get power to your meter, the other to pay for the power you use.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic. Clean, accurate nätavtal vs elhandelsavtal split including pricing structures and switching rules. calib 6 for the boilerplate note only.

---

## Case 44 / 65 — `81511feb`
**Expected route:** `combined`

### Question

> What should our building prioritize if we want to reduce heating costs?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
To reduce your building’s heating costs, you should focus on measures that improve the thermal performance of the building envelope and optimize your heating system. Even without specific EPC data, the following priorities generally deliver the biggest savings:

1. Enhance Insulation  
   • Roof/Ceiling – Upgrading loft or attic insulation typically yields the highest return, as heat rises and is lost through the top of the building.  
   • External Walls – Adding or improving wall insulation (either internally or externally) cuts significant heat losses through masonry or timber walls.  
   • Ground/Floor Slabs – Insulating suspended timber floors or solid ground slabs reduces cold‐bridging and drafts at ground level.

2. Improve Airtightness  
   • Seal Gaps – Identify and seal cracks around windows, doors, penetrations for pipes/cables, and junctions between building elements to prevent draughts.  
   • Controlled Ventilation – Pair airtightness work with a mechanical ventilation system that has a heat-recovery unit (MVHR) to maintain indoor air quality without losing heat.

3. Upgrade Windows and Doors  
   • Double- or Triple-Glazing – Modern glazing units with low-emissivity coatings and insulated frames will reduce both conductive and radiative heat loss.  
   • Weather-Stripping – Renew seals on existing frames to stop cold air ingress.

4. Optimize Heating System  
   • High-Efficiency Boiler or Heat Pump – Replace old boilers (especially ones older than 15 years) with A-rated condensing boilers or air-/ground-source heat pumps.  
   • Smart Controls – Install programmable thermostats and thermostatic radiator valves to tailor heating schedules and temperature zones to actual occupancy.  
   • System Balancing – Ensure radiators and under-floor circuits are properly balanced so that the system operates evenly and avoids overheating.

5. Operational Measures and Monitoring  
   • Regular Maintenance – Service boilers, inspect pipe insulation and bleed radiators to keep the system running at peak efficiency.  
   • Energy Monitoring – Install simple meters or building-management software to track consumption patterns and identify waste.  
   • Occupant Engagement – Encourage temperate thermostat settings (e.g. 19–21 °C) and small behavioral changes, like closing doors to unused rooms.

6. Long-Term Upgrades  
   • Solar Gain and Shading – Where appropriate, use south-facing glazing or solar collectors; add external shading to avoid overheating in summer.  
   • Renewable Integration – Consider photovoltaic panels or solar thermal systems to offset electricity or hot-water costs.

By systematically targeting the building envelope first (insulation and airtightness) and then optimizing the heating plant and controls, you can typically achieve 20–40 % reductions in heating energy use—and corresponding cost savings—without compromising comfort.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: no evidence. Best calibration in the form: opens "Even without specific EPC data", stays generic, invents nothing, and the trailing note is for once consistent with the answer's own framing.

---

## Case 45 / 65 — `63d99fa2`
**Expected route:** `combined`
**Building the system identified:** `01-80-NORRADJURGAARDEN1:49-34`

### Question

> What should my building prioritize to improve energy efficiency?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on your building’s latest energy performance certificate (EPC), the building is currently classified in energy class G with a specific energy use of approximately 108 kWh/m²·year (versus a reference value of 55 kWh/m²·year). The main heating system is direct‐electric (“ElDirekt”), and there is no heat recovery on the ventilation system nor on-site renewables installed. To move toward a more efficient, lower‐class building, you should consider the following priorities:

1. Upgrade the heating system  
   • Replace direct‐electric radiators with a heat pump (air-to-water or ground‐source) or, where available, connect to district heating.  
   • A modern heat‐pump system can cut your primary‐energy factor significantly compared to direct electric resistance heating.  

2. Install balanced mechanical ventilation with heat recovery  
   • Your EPC indicates no approved ventilation group and no heat‐recovery (“venttyp FT­X”) installed.  
   • A balanced system with at least 70–80 % heat recovery can reduce heating loads by reclaiming warm exhaust air.  

3. Improve the building envelope  
   • Insulate exterior walls, roof/attic and, if applicable, basement or slab edges to meet current U-value targets (e.g. ≤0.18 W/m²·K for walls, ≤0.13 W/m²·K for roof).  
   • Upgrade windows and doors to high‐performance, triple‐glazed units with low-E coatings and proper airtightness.  

4. Add on-site renewable generation  
   • Photovoltaic panels can lower grid electricity consumption and your building’s primary‐energy index.  
   • Solar thermal collectors for domestic hot water can further reduce electrical hot‐water heating loads (currently 0.5 kWh/m²·year of tap water heating).  

5. Optimize controls and zoning  
   • Install programmable thermostats, weather-compensated control and room sensors to avoid overheating and to target heat only where and when it’s needed.  
   • Consider smart meters to track consumption in real time and verify savings.  

By tackling the heat source first (the largest energy consumer at ~23 445 kWh/year for space heating) and recovering heat in your ventilation system, you will achieve the most significant immediate gains. Upgrading insulation and installing renewables will compound your savings and help you move out of class G toward the A–C range.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[3]`
- overall_pass (0 or 1): `[1]`
- notes: Same building and figures as case 42, coherent (108/55, 23 445, 0.5 kWh/m² tap water). faith 5 / calib 3: asserts both "no heat recovery" and "no on-site renewables installed" while ventilation sits on its own unconfirmed list (two disclaimed assertions vs case 42's one). Recommendations themselves fit a G-class ElDirekt building. pass 1.

---

## Case 46 / 65 — `b63583f0`
**Expected route:** `generic`

### Question

> How does the energy class affect the choice of household appliances from an energy perspective?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
From an energy-perspective, the building’s overall energy class (as stated on its Energy Performance Certificate, or EPC) tells you how efficiently it uses energy for heating, cooling, ventilation and hot water. Appliances are a significant part of a home’s total energy use, so selecting the right energy-rated appliances can help you:  
• Reinforce a high EPC rating – If your building already has a good energy class (A or B), choosing top-rated appliances (A+++ through A+) keeps your total consumption low and helps maintain that rating.  
• Mitigate a lower EPC rating – In a building with a modest energy class (C, D, or lower), best-in-class appliances reduce your electricity bills and cut back on carbon emissions, partially compensating for higher heating or cooling losses.  

Key considerations when matching appliances to your EPC level:  
1. Energy label tier: Always target the highest label available (A+++, A++, A+). The upfront cost is typically offset within a few years by lower running costs.  
2. Household consumption profile: In a well-insulated, low-EPC-demand home, appliance use may represent 30–50 % of your total energy use. In a drafty or poorly insulated home, heating demand dominates—but efficient appliances still trim your electricity bill.  
3. Standby and smart functions: Even “off” appliances draw power. Look for products with “zero-standby” modes or smart scheduling that run during off-peak hours, further aligning consumption with lower tariffs.  
4. Size and capacity: Oversized refrigerators, dishwashers or laundry machines run less efficiently if they’re rarely full. Select a capacity that matches your household’s needs.  
5. Complementary measures: In lower-rated buildings, pairing efficient appliances with simple envelope upgrades (draught proofing, radiator reflectors) maximizes your overall savings.  

By choosing top-energy-rated appliances in any building, you reduce your operating costs, decrease environmental impact and—in the case of better-insulated (high-EPC) buildings—ensure that the relative share of appliance use remains minimal in your total energy footprint.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[8]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[5]`
- overall_pass (0 or 1): `[0]`
- notes: Entity blank: generic. Conflates the building's EPC class with appliance energy labels — the question is about appliances — and leads with the A+++...A+ tiers retired in 2021; "30–50 % of total energy use" asserted without support. rel 8: answers through the wrong lens. pass 0 (changed on review): unlike case 11, where the obsolete label is one item among nine sound ones, here the conflation is the answer's spine — it would actively mislead a shopper.

---

## Case 47 / 65 — `30d8420b`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-FORSKNINGEN1-44`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Your building’s energy performance figures from the latest EPC (energy performance certificate) are as follows:

• Energy performance (Energiprestanda): 79 kWh/m²·year  
• Energy class: D (under the 2020 classification)  
   – For reference, under the previous scheme it was classified as C, but the 2020 update places it in class D  
• Total annual delivered energy use: 198 495 kWh  
• Specific delivered energy use: 91 kWh/m²·year  
• Primary energy use: 173 149 kWh  
• Main heating system: district heating (118 576 kWh/year)  

In summary, your building consumes 79 kWh per square metre and is rated in energy class D according to the most recent (2020) EPC criteria.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: FORSKNINGEN answered as itself: 79/D, totals cohere (198 495/91 ≈ 2 181 m² ≈ the 2 184 attested in case 55), DH 118 576 attested. faith 8 not 9 for one reason: the "under the previous scheme it was classified as C" claim appears nowhere else in the form and cannot be corroborated.

---

## Case 48 / 65 — `1cc9711e`
**Expected route:** `building_specific`
**Building the system identified:** `19-84-LEOPARDEN5-1`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the energy performance certificates in your data, the primary heating systems are as follows:

• Building ID 19-84-LEOPARDEN5-1 (EPC version 2.0, approved 2012-02-03)  
  – Main heating system: Flis (wood chips)  
  – No district-heating connection (district_heating_use = 0 kWh)

• Building ID 01-80-HAELSAN10-2 (EPC version 2.6, approved 2019-06-02)  
  – Main heating system: District heating  
  – Annual district-heating use: 819 500 kWh

If you have a specific building in mind, please let me know which Building ID you’re interested in!

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[7]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[5]`
- calibration (0–10): `[5]`
- overall_pass (0 or 1): `[0]`
- notes: Accurate neutral listing (LEOPARDEN: Flis, EPC 2.0 2012-02-03, DH 0; HAELSAN: DH 819 500, v2.6 2019-06-02 — all attested), the named building's correct data present, then punts with "let me know which Building ID". Scored identically to case 9, which has the same shape: faith 7, rel 9 (topic addressed for both candidates), entity 5 (labeled, asks, never attributes), calib 5 (asks despite the system's resolution). pass 0.

---

## Case 49 / 65 — `a507847e`
**Expected route:** `generic`

### Question

> How should a BRF board prioritize energy measures when the budget is limited?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
When a housing co-op (BRF) has only a limited pot for energy upgrades, it’s crucial to work strategically so that every kronor spent delivers the greatest long-term benefit. Below is a step-by-step framework for prioritizing energy measures:

1. Start with a Professional Energy Audit  
   • Engage an accredited energy consultant to produce a heat-loss calculation and identify all potential measures, estimated costs and energy savings.  
   • The audit will usually rank measures by their cost-effectiveness (cost per kWh saved) and expected payback period.

2. Target “Low-Hanging Fruit” First  
   • Air-tightness and draft sealing around windows, doors and service penetrations.  
   • Improved control of heating (thermostatic radiator valves, weather compensation in the central boiler).  
   • LED lighting upgrades in common areas with motion sensors or time clocks.  
   These measures often have payback times of 2–5 years and can typically be done for a modest share of the budget.

3. Leverage Maintenance and Replacement Cycles  
   • If boilers, pumps or windows are due for replacement anyway, specify higher-efficiency models.  
   • Schedule insulation work or façade repairs so that any scaffolding can serve dual purposes (e.g. external insulation plus painting).

4. Evaluate Medium-Cost, High-Impact Measures  
   • Mechanical ventilation with heat recovery (MVHR) can cut heat loss from exhaust air by up to 80 %.  
   • Upgrading to condensing boilers or connecting to district heating if available.  
   • Solar collectors or photovoltaic panels—often eligible for grants or net-metering schemes.

5. Plan Major Investments Over the Long Term  
   • External wall or roof insulation: high upfront cost but can move you one or two EPC classes (e.g. from E → C) and reduce heating costs by 25–40 %.  
   • Window replacement with triple-glazing: plan around the building’s lifecycle so you fund it when windows are due for renewal.

6. Seek Subsidies and Financing Options  
   • Check current national/regional grants (Boverket, Naturvårdsverket, Energimyndigheten) for energy-saving measures.  
   • Consider energy performance contracting (EPC) or green loans with repayment tied to realized energy savings.

7. Build a Multi-Year Roadmap  
   • Use the audit’s ranking to phase measures over 3–5 years, balancing cash flow and disruption to residents.  
   • Revisit and update priorities after you complete each phase—actual savings can confirm or adjust your future plan.

By combining reliable audit data, phased implementation and tapping available subsidies, a BRF board can maximize carbon reductions and cost savings—even on a tight budget.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic. Strong framework (audit → no-regret → maintenance cycles → roadmap → subsidies) with plausible Swedish program names. calib 6 for the boilerplate note.

---

## Case 50 / 65 — `2276cbbf`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Based on the latest energy-declaration data for your property (Building ID 01-80-HAELSAN10-2), the main heating system is district heating (“fjärrvärme”). In 2019 the building consumed approximately 819 500 kWh of district heat for space heating and an additional 161 300 kWh for domestic hot water.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: HAELSAN answered as HAELSAN: DH 819 500/161 300 matches the 2019-cert family (case 48's approval date corroborates). faith 9: unlike its sibling heating blocks it makes no "no other fuel types" universal and time-stamps the figures ("In 2019") instead of claiming currency — nothing overclaimed at all.

---

## Case 51 / 65 — `ad636941`
**Expected route:** `building_specific`
**Building the system identified:** `17-81-BONDEN9-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
According to the latest energy performance certificate for your building (ID 05-83-RAEFSAN5-1, approved 2024-09-19, version 3.0):

• Energy performance (EPC “energiprestanda”): 75 kWh/m²·year  
• Energy class: C  

In other words, your building consumes 75 kWh of delivered energy per square metre and year and falls into class C on the Swedish EPC scale.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[3]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap: named BONDEN, answers RAEFSAN (75/C, v3.0 2024) as "your building". Coherent record, false attribution. entity 0, calib 2.

---

## Case 52 / 65 — `8c985d1d`
**Expected route:** `building_specific`
**Building the system identified:** `17-81-BONDEN9-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
For the property with byggnadsid 17-81-BONDEN9-1 (Bonden 9, Kristinehamn), the latest energy declaration shows:

• Energy performance (Energiprestanda): 109 kWh/m²·year  
• Energy class (Energiklass): D  

This rating is based on the 2019 certificate, with an air-to-water heat pump as the main heating system.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: Exactly the asked figures for the named BONDEN (109/D, 2019 cert, A2W HP) and nothing more — the most cleanly scoped building answer in the form; brevity here is a virtue, not an omission.

---

## Case 53 / 65 — `2e697698`
**Expected route:** `generic`

### Question

> What is meant by control and regulation technology?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
Control and regulation technology (sometimes called building automation or process control) is the collection of methods, devices and systems used to monitor, manage and optimise technical installations in a building or industrial process. Its main purpose is to ensure that environmental and operational parameters (temperature, humidity, air-flow, lighting, pressure, etc.) remain within desired limits for comfort, energy efficiency, safety and reliability.  

Key aspects include:  
• Sensors and measurement  
  – Devices that continuously record physical variables (e.g. temperature sensors, humidity probes, CO₂ sensors, pressure transducers).  
• Controllers and control logic  
  – Programmable logic controllers (PLCs), building management system (BMS) servers or dedicated control units that compare actual measurements against set-points and calculate corrective actions.  
  – Control algorithms may range from simple on/off or proportional–integral–derivative (PID) loops to advanced model-based or adaptive controls.  
• Actuators and final control elements  
  – Components such as control valves, dampers, variable-speed drives for fans and pumps, or dimmers for lighting that physically adjust the installation in response to controller commands.  
• Communication and integration  
  – Field and network protocols (BACnet, Modbus, KNX, LonWorks, etc.) that carry data between sensors, controllers, actuators and supervisory systems.  
  – Integration with energy-management platforms, fire- and security systems or remote monitoring services.  
• User interfaces and supervision  
  – Operator panels, touchscreens or web-based dashboards that allow facility managers to visualise system performance, modify set-points, schedule operations and receive alarms.  

Typical applications in a building include:  
  • Climate control (HVAC): automated regulation of heating, cooling and ventilation to maintain comfort while minimising energy use.  
  • Lighting control: daylight-responsive dimming, occupancy detection and scheduling to reduce power consumption.  
  • Water and energy metering: tracking consumption and triggering corrective actions if thresholds are exceeded.  
  • Fire and safety systems: ensuring doors, fans or dampers react automatically in an emergency.  

By applying control and regulation technology, buildings can achieve better indoor comfort, lower energy bills, reduced environmental impact and improved operational reliability.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: definitional. Textbook-accurate (sensors → controllers → actuators → protocols → HMI, with BACnet/Modbus/KNX correctly placed). calib 6 for the trailing note.

---

## Case 54 / 65 — `b26f405d`
**Expected route:** `building_specific`
**Building the system identified:** `05-83-RAEFSAN5-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
According to the latest Energy Performance Certificate for your building (ID 05-83-RAEFSAN5-1, Rådmansgatan 31, Motala; EPC version 3.0, approved 2024-09-19):

• Energy performance (specific energy use): 75 kWh/m²·year  
• Energy class: C  
• Heated floor area (Atemp): 171 m²  
• Specific delivered energy: 42 kWh/m²·year  
• Total annual energy use: 7 171 kWh  
• Primary energy use: 12 908 kWh  
• Main heating system: air-to-water heat pump  

These figures represent the most recent (and thus correct) assessment of your building’s energy performance.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[7]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: All figures match RAEFSAN and reconcile (171x42≈7 171). faith 7: calls 75 "specific energy use" and 42 "specific delivered energy" — 75 is the primary-energy figure (energiprestanda), so one label is wrong although both numbers are right.

---

## Case 55 / 65 — `d18157f7`
**Expected route:** `combined`
**Building the system identified:** `01-80-FORSKNINGEN1-44`

### Question

> What should my building prioritize to improve energy efficiency?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Your building (EPC ID 01-80-FORSKNINGEN1-44) currently holds an energy class D (latest calculation), with a primary energy performance of 79 kWh/m² (target for new school buildings is ≈75 kWh/m²). Total annual energy use is 198 495 kWh for 2 184 m² (≈91 kWh/m²), broken down approximately as follows:

 • Space heating (district heating)   
   – 124 504 kWh (63 % of total)  
 • Domestic hot water   
   – 45 056 kWh (23 % of total)  
 • Electricity (lighting, equipment, ventilation fans etc.)  
   – 28 048 kWh (14 % of total)  

To move towards a higher energy class (C or B) you should prioritize measures in this order:

1. Improve the building envelope  
   • Upgrade insulation in external walls, roof and/or top floor ceilings.  
   • Replace or retrofit windows and doors with higher-performance glazing and better seals.  
   • Seal air leaks around penetrations and junctions to improve airtightness.  

2. Optimize and commission the heating and ventilation systems  
   • Ensure your FTX (mechanical supply-and-extract ventilation with heat recovery) is properly commissioned and maintained – the certificate shows “ventgrupp godkänd: Nej,” so an inspection and adjustment could raise recovery efficiency.  
   • Install smart control valves and weather-compensated controls on the district heating circuit to avoid overheating.  
   • Insulate heating and hot-water distribution pipes to reduce losses.  

3. Reduce hot-water energy use  
   • Fit low-flow taps and showerheads.  
   • Consider pre-heating via solar thermal collectors or a small heat-pump water heater.  

4. Lower electricity demand and explore on-site renewables  
   • Upgrade to LED lighting with presence sensors and daylight controls.  
   • Replace old appliances and IT-equipment with high-efficiency models.  
   • Evaluate a rooftop PV installation to offset part of the 28 048 kWh electricity load.  

By focusing first on the highest-consuming end-uses (space heating and hot water) and ensuring your ventilation heat recovery operates as intended, you can drive your specific energy use below the 75 kWh/m² benchmark and achieve at least class C or better.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[4]`
- overall_pass (0 or 1): `[1]`
- notes: Figures match FORSKNINGEN (79/D, 2 184 m², 45 056 DHW, 28 048 elec; breakdown nearly sums: 197 608 vs 198 495), but space-heating DH is given as 124 504 where cases 23/31/47 attest 118 576, and "≈75 kWh/m² target for new school buildings" is an unsupported external benchmark. The "ventgrupp godkänd: Nej" citation also contradicts the unconfirmed-ventilation note. faith 5, calib 4.

---

## Case 56 / 65 — `93968228`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-FILOSOFEN2-2`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
The building’s primary heating system is district heating (“fjärrvärme”) supplied via Stockholm’s network. According to the latest energy declaration:

• Heating energy (space heating) from district heating: 59 545 kWh/year  
• Domestic hot water from district heating: 66 600 kWh/year  

No other heating sources (electric direct heating, heat pumps, oil, etc.) are reported.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: DH Stockholm 59 545/66 600 matches the FILOSOFEN2-2 family (cases 30/57). entity 9: no ID restated. calib 7 for the flat "No other heating sources" universal.

---

## Case 57 / 65 — `a1868d1a`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-FILOSOFEN2-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
According to the latest Energy Performance Certificate for building 01-80-FILOSOFEN2-2:

• Energy Performance (Energiprestanda): 53 kWh/m²·year  
• Energy Class: B (latest calculated class)  

Additional figures from the EPC (version 2023, base year 2020):  
• Specific delivered energy use: 59 kWh/m²·year (total 158 134 kWh/year)  
• Primary energy use: 53 kWh/m²·year (total 141 973 kWh/year)  
• Main heating system: District heating (59 545 kWh/year)

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: FILOSOFEN2-2 answered as itself; 53 primary/B vs 59 delivered correctly distinguished, and both totals imply the same ≈2 680 m² area (158 134/59 and 141 973/53) — the most internally coherent block in the form, with no universals and no contested currency claims. faith 9 to match that.

---

## Case 58 / 65 — `7ddf3f43`
**Expected route:** `generic`

### Question

> Are there grants or subsidies for improving apartment insulation?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
In Sweden, there are several ways to obtain financial support for improving the thermal insulation of apartment buildings (multi-dwelling housing). Below is an overview of the most common grants, tax deductions and subsidy programmes you can explore:

1. ROT tax deduction (“ROT-avdrag”)  
   • What it is: A tax credit of up to 30 percent (limited to a maximum amount per person and year) for labour costs when you renovate, maintain or improve your home.   
   • How it applies to insulation: Labour costs for installing or upgrading exterior wall insulation, attic insulation, basement ceiling insulation and other related work qualify for ROT. Materials are not covered by ROT, only the labour portion.  
   • Who is eligible: Owners of condominiums (bostadsrättsföreningar) can apply via the association’s contractor (the association must be VAT-registered and can pass on the ROT benefit). Individual apartment owners cannot directly claim ROT on the exterior envelope, but the association can.

2. Klimatklivet (Swedish Environmental Protection Agency)  
   • What it is: A non-repayable grant for investments that reduce greenhouse-gas emissions.  
   • How it applies to insulation: Projects improving building envelope U-values (e.g. external wall insulation, roof insulation) may be eligible.  
   • Grant level: Typically covers 30–50 percent of eligible costs, depending on project size and impact.  
   • How to apply: Applications are made online when a funding round is announced, usually twice per year. Decisions are made by the county administrative board (länsstyrelsen) on behalf of the EPA.

3. Energieffektiviseringsstödet (The National Board of Housing, Building and Planning – Boverket)  
   • What it is: A targeted grant for energy-efficiency measures in existing buildings.  
   • How it applies to insulation: Multi-dwelling buildings can get 30 percent of the cost for planned improvement measures, including wall, roof and floor insulation.  
   • Special conditions: Must meet pre-defined technical requirements (e.g. maximum U-values). The project must be completed within a set timeframe (usually 2–3 years from decision date).

4. Regional and municipal energy grants  
   • What they are: Many municipalities and regional councils offer their own energy-efficiency grants, often through local energy companies or environmental offices.  
   • Scope: These can range from small “energi-checkar” for energy surveys to partial grants for upgrading façades, windows and insulation.  
   • How to find them: Check your municipality’s website under “energy and environment” or contact the municipal energy adviser (energirådgivare).

5. EU and other programmes  
   • European Structural and Investment Funds (ESIF): In some regions these funds support energy-efficiency projects in multi-dwelling buildings.  
   • Horizon Europe and demonstration grants: For innovative insulation materials or renovation methods, research and pilot projects may obtain co-funding.

Key steps to follow  
 1. Compile an energy audit (“energikartläggning”) or energy performance certificate (EPC/energideklaration) to document current U-values and pinpoint where insulation will have the greatest impact.  
 2. Compare total cost estimates with each programme’s eligibility requirements and application deadlines.  
 3. Apply formally through the relevant portal (Skatteverket for ROT, Klimatklivet via the county administrative board, Boverket’s portal for energy-efficiency grants, plus any municipal schemes).  
 4. Implement the work according to the grant decision, document invoices carefully and submit final reports to secure the subsidy disbursement.

Because rules and funding levels can change year to year, it is wise to:  
– Visit the official websites (Skatteverket, Naturvårdsverket/Klimatklivet, Boverket) for the latest call-for-applications and detailed technical criteria.  
– Contact your municipality’s energy-advisory service for local grants and hands-on guidance.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic. Useful map of real programs, but the ROT guidance is wrong: ROT covers labour on a member's own dwelling and a BRF cannot claim or pass it on for association envelope work; program percentages stated more precisely than supportable. calib 7: repeatedly tells the reader rules change and to verify with Skatteverket/Boverket — that hedging is why it still passes.

---

## Case 59 / 65 — `2360bd34`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-NORRADJURGAARDEN1:49-34`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
The energy performance certificate shows that your building is heated entirely by direct‐electric heating (“ElDirekt”). There is no district heating, oil, gas or biomass connection – 100 % of the space heating (23 445 kWh) plus the small domestic hot water load (115 kWh) is supplied by electric resistance heaters.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: ElDirekt with 23 445 + 115 kWh matches cases 36/42/45. "100 % ... electric resistance heaters" is flat but consistent with the record. entity 9: no ID restated.

---

## Case 60 / 65 — `282c4598`
**Expected route:** `building_specific`
**Building the system identified:** `17-81-BONDEN9-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
For building ID 17-81-BONDEN9-1 (Rådmansgatan 31, Kristinehamn), the latest energy performance certificate (EPC version 2.6, approved April 2019) reports:

• EPC energy performance index (epc_egienergiprestanda): 109 kWh/m²·year  
• Energy class (epc_egienergiklass): D (latest calculation for 2020)  
• Heated floor area (Atemp): 280 m²  
• Specific energy use (epc_egispecifikenergianvandning): 82 kWh/m²·year  
• Total annual energy use (epc_egienergianvandning): 22 914 kWh  
• Primary energy use (epc_egiprimarenergianvandning): 30 552 kWh  
• Main heating system: air-to-water heat pump  

These figures describe the building’s current energy performance and classification.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: Fullest BONDEN block and internally tight (109x280≈30 552 primary, 82x280≈22 914). faith 8 not 9: "Rådmansgatan 31, Kristinehamn" — the same street address appears under three different buildings in three cities across the form, so the address looks copied rather than retrieved.

---

## Case 61 / 65 — `cd67be3d`
**Expected route:** `generic`

### Question

> What is the difference between a grid contract and an electricity supply contract?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
A household or business in Sweden needs two separate agreements to get “power from the wall” to usable electricity on your premises:

1. Grid Contract (nätavtal)  
   • Who you sign with: The local grid- or distribution network operator (the company that owns and maintains the poles, wires and transformers in your area).  
   • What it covers:  
     – Right to connect to and draw electricity from the national or regional grid.  
     – Fees for capacity (the maximum power you can draw), energy losses in the grid, meter reading and network maintenance.  
     – Emergency response, fault management and power-quality guarantees.  
   • Pricing structure: Generally a fixed monthly fee plus a volumetric fee (öre/kWh) for grid losses and meter services. You pay this regardless of your electricity supplier.

2. Electricity Supply Contract (elhandelsavtal)  
   • Who you sign with: An electricity retailer or trader (an energy company that buys power on the wholesale market and sells it to end-users).  
   • What it covers:  
     – Purchase of the electricity commodity itself.  
     – Choice of product: fixed-price, variable (spot-price) or green-certificate-backed contracts.  
     – Retailer services such as billing, customer support and sustainability certification.  
   • Pricing structure: Depends on your chosen product – it may be a fixed tariff per kWh, a time-of-use rate tied to Nord Pool spot prices, or a premium for guaranteed renewable energy.  

Why you need both  
– Without a grid contract you have no physical connection or legal right to draw power from the network.  
– Without an electricity supply contract you cannot legally purchase or consume the energy; the grid operator merely delivers whatever flows on the grid to your meter.  

In practice you typically set up your grid contract first (with your local network company) and then choose an electricity supplier whose product and pricing suit your consumption profile and sustainability goals.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[6]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic. Accurate and more detailed than case 43 on the same question (capacity fees, Nord Pool, why both contracts are needed). calib 6 for the boilerplate note.

---

## Case 62 / 65 — `48b893aa`
**Expected route:** `generic`

### Question

> What are the advantages of natural ventilation compared with mechanical ventilation?

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Model's final answer

```
Natural (or “passive”) ventilation—using wind and thermal buoyancy to move fresh air through a building—offers several advantages over purely mechanical ventilation systems:

1. Lower energy consumption and operating costs  
   • No or very limited fan‐power required, reducing electricity use  
   • Fewer moving parts mean lower annual energy bills  

2. Reduced carbon footprint  
   • Eliminating or downsizing mechanical fans and heat‐recovery units cuts embodied and operational CO₂ emissions  

3. Simplicity and reliability  
   • Fewer mechanical components (fans, filters, ductwork, controls) mean less can go wrong  
   • Reduced maintenance requirements and service intervals  

4. Improved occupant comfort and well-being  
   • Direct use of outdoor air can enhance thermal and air-quality perception  
   • Provides variable airflow rates that occupants often find more natural  

5. Quieter operation  
   • Absence of fan and motor noise yields a more tranquil indoor environment  

6. Resilience and passive fail-safe  
   • Continues to operate during power outages or mechanical failures  
   • Maintains some level of fresh-air supply without backup power  

7. Cost savings in design and installation  
   • Smaller or no ducts, fewer fans and controls can reduce upfront construction costs  
   • Simplified coordination of mechanical services  

While natural ventilation may not fully replace mechanical systems in all climates or uses, integrating both strategies (“hybrid ventilation”) often yields an optimal balance of energy efficiency, indoor air quality and occupant comfort.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[ ]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: Entity blank: generic. Fair advantage list with honest scope-limiting ("may not fully replace mechanical in all climates") and the hybrid-ventilation caveat; nothing invented.

---

## Case 63 / 65 — `60cbf5c3`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-HAELSAN10-2`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the energy‐performance figures from the latest available EPCs:

• Building ID 19-84-LEOPARDEN5-1 (Tulegatan 5A, Arboga)  
  – Specific energy use: 190 kWh/m²·year  
  – Energy class: E  

• Building ID 01-80-HAELSAN10-2 (Tulegatan 5A, Stockholm)  
  – Specific energy use: 183 kWh/m²·year  
  – Energy class: E  

Both buildings fall into Energy Class E according to the most recent (2020) energy performance certificates.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[5]`
- answer_relevance (0–10): `[9]`
- entity_consistency (0–10): `[4]`
- calibration (0–10): `[4]`
- overall_pass (0 or 1): `[0]`
- notes: Dumps LEOPARDEN and HAELSAN ("Tulegatan 5A" duplicated across cities again) with no designation and no ask. entity 4 not 3: unlike cases 2/12/13/20 it never falsely claims both buildings are the user's — but unlike 9/48 it doesn't even flag the ambiguity, hence calib 4, the lowest of the dump blocks. faith 5 for the duplicated address on otherwise-attested figures. pass 0: the user cannot tell which figures are theirs.

---

## Case 64 / 65 — `c50875d9`
**Expected route:** `building_specific`
**Building the system identified:** `01-80-FILOSOFEN2-4`

### Question

> What is the heating system in my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Your building is heated entirely by district heating. According to the latest energy performance certificate (version 2023):

• Primary heating system: District heating (Swedish “fjärrvärme”)  
• District heating network: Stockholm  
• Annual district heating consumption for space heating: 53 777 kWh  
• Annual district heating consumption for domestic hot water: 58 800 kWh  

No other heating sources (oil, gas, heat pumps, biomass, etc.) are reported. The building’s overall energy class is B, with an energy performance indicator of 60 kWh/m²·year.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[8]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[9]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[1]`
- notes: DH 53 777/58 800 with class B/60 reconciles with case 17's totals for FILOSOFEN2-4 (112 577 + electricity ≈ 149 478). entity 9: no ID restated. calib 7 for the flat "No other heating sources" line.

---

## Case 65 / 65 — `c581ba48`
**Expected route:** `building_specific`
**Building the system identified:** `17-81-BONDEN9-1`

### Question

> What is the energy performance of my building?

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Model's final answer

```
Here are the key figures from your most recent energy performance certificate (EPC) for building 05-83-RAEFSAN5-1 (Rådmansgatan 31, Motala):

• Energy performance (EPC “egienergiprestanda”): 75 kWh/m²·year  
• Energy class (egienergiklass): C  
• Specific energy use (egispecifikenergianvandning): 42 kWh/m²·year  
• Annual delivered energy (egienergianvandning): 7 171 kWh  
• Primary energy use (egiprimarenergianvandning2020): 12 908 kWh  

These figures are based on the latest declaration filed in 2023.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Your scores

- faithfulness (0–10): `[2]`
- answer_relevance (0–10): `[10]`
- entity_consistency (0–10): `[0]`
- calibration (0–10): `[2]`
- overall_pass (0 or 1): `[0]`
- notes: Full swap: named BONDEN, answers RAEFSAN in full detail, and adds "latest declaration filed in 2023" where the same record is dated approved 2024-09-19 in five other blocks — a fabricated date on top of the misattribution. faith 2 for that extra invention.

---
