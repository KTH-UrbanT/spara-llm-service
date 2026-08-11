# Stage 3 — closed-loop κ labelling (blinded)

**Instructions.** For each of the 15 cases below, score the model's *final answer* against the question and the retrieved evidence. The judge's verdict is intentionally hidden so your scoring is blinded.

**Scoring scale (per axis, integer 0–10):**
- **faithfulness** — Is every factual claim in the answer supported by the retrieved evidence? 10 = every claim grounded in the evidence; 0 = answer is unsupported / hallucinated.
- **answer_relevance** — Does the answer address what was actually asked? 10 = directly answers; 0 = off-topic.
- **question_coverage** — Does the answer cover all parts of a multi-part question (or just the main ask if single-part)? 10 = complete; 0 = misses the question entirely.
- **calibration** — Is the answer appropriately hedged where uncertain (no overconfident claims; admits gaps)? 10 = well-calibrated; 0 = confidently wrong.

**overall_pass** — Your binary judgement (0 / 1): would you accept this answer as a satisfactory response to the question? Use your overall sense, not a strict formula on the axes.

**notes** — optional free-text reasoning.

Fill the bracketed `[ ]` placeholders inline. Total time: ~30 min. When done, upload this file back and Claude will compute Cohen's κ + bootstrap CIs.

---

## Case 1 / 15 — `EKR_GEN_006`

**Expected route:** `generic`

### Question

> What does energy-efficiency improvement mean?

### Model's final answer

```
Energy-efficiency improvement refers to any measure or upgrade that reduces the amount of energy required to perform the same function—whether that function is heating, cooling, lighting or running equipment—while maintaining or improving comfort and performance. In the context of buildings, common energy-efficiency improvements include:

• Upgrading the building envelope  
  – Adding or enhancing insulation in walls, roofs and floors  
  – Replacing single-glazed windows with double- or triple-glazed units  
  – Sealing air leaks around doors, windows and penetrations  

• Optimizing heating, ventilation and air-conditioning (HVAC) systems  
  – Installing high-efficiency boilers, furnaces or heat pumps  
  – Upgrading controls and thermostats for better temperature regulation  
  – Balancing ductwork and improving airflow  

• Installing efficient lighting and appliances  
  – Switching to LED or other low-energy lighting technologies  
  – Choosing Energy Star or equivalent certifie
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[10]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[10]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 2 / 15 — `EKR_GEN_019`

**Expected route:** `generic`

### Question

> How does my electricity use affect my electricity price?

### Model's final answer

```
To give building-specific advice safely, I need the full building address.
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[2]`
- answer_relevance (0–10): `[5]`
- question_coverage (0–10): `[1]`
- calibration (0–10): `[7]`
- overall_pass (0 or 1): `[0]`
- notes: it should provide some insight and advices anyway. the question is generic

---

## Case 3 / 15 — `DEMO_EXTRA_GEN_001`

**Expected route:** `generic`

### Question

> How can we reduce standby electricity use in shared spaces?

### Model's final answer

```
Here are several well-established, general best practices for cutting standby (vampire) loads in shared or common areas such as lounges, kitchens, meeting rooms and corridors:

1. Centralized Power-Off Controls  
   • Install master switches or lockable disconnects in utility cupboards or next to entrance doors so that all non-essential equipment (coffee machines, AV gear, chargers, etc.) can be powered down at end-of-day.  
   • Use smart power strips with a “master” outlet: when the primary device (e.g. a computer) is turned off, all downstream sockets lose power.

2. Occupancy and Schedule-Based Automation  
   • Fit motion or presence sensors that automatically cut power or switch off non-critical lighting and equipment after a period of inactivity.  
   • Use your building management system (BMS) or dedicated timers to enforce “office hours” shut-off, ensuring devices don’t remain in standby overnight or at weekends.

3. Equipment Selection and Default Settings  
   • When replaci
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[10]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[10]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 4 / 15 — `EKR_GEN_005`

**Expected route:** `generic`

### Question

> What is an energy declaration?

### Model's final answer

```
Below is a general description of an energy declaration (in Swedish: energideklaration). This overview is based on publicly available information from the Swedish Energy Agency and the EU’s Energy Performance of Buildings Directive (2010/31/EU). 

1. Purpose  
   • Ensure transparency about a building’s energy performance when it is constructed, sold or rented.  
   • Help owners and occupants identify cost-effective measures to reduce energy use and associated emissions.  

2. Legal Framework (Sweden)  
   • Regulated by the Act (2006:985) on Energy Declarations for Buildings.  
   • Implements the EU’s Energy Performance of Buildings Directive.  

3. Main Contents  
   • Building description: geometry, heated floor area (Atemp), number of floors and stairwells.  
   • Technical systems: heating, ventilation, cooling and lighting installations.  
   • Calculated energy use: expressed in kWh per square meter and year (kWh/m²·year).  
   • Energy performance class: graded on a scale (co
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[10]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[10]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 5 / 15 — `BRF_ODEN_06_TEKNIKRINGEN_78B_001_HEATING`

**Expected route:** `building_specific`

### Question

> What is the heating system in my building?

### Model's final answer

```
I’m sorry, but the data you’ve provided doesn’t include any details on your building’s heating system. In Sweden, the heating system type is normally recorded in the energy performance certificate (energideklaration), under the section “Värmesystem.” 

To find out what heating system your building uses, you can:

• Look up the building’s EPC (energideklaration) via Boverket’s or your municipality’s online register.  
• Check the “Värmesystem” field in the EPC document—it will specify whether you have district heating, a heat pump, biomass boiler, electric radiators, etc.  
• Provide the EPC record (or at least the “Värmesystem” entry) here, and I can help interpret it for you.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[9]`
- question_coverage (0–10): `[9]`
- calibration (0–10): `[9]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 6 / 15 — `BRF_ODEN_10_RADMANSGATAN_31_003_HEATING`

**Expected route:** `building_specific`

### Question

> What is the heating system in my building?

### Model's final answer

```
I’m sorry, but I don’t have any information on your building’s heating system—your request arrived without any building or EPC data. To answer, I would normally look for the relevant field in the Energy Performance Certificate (EPC), for example a “heating system” or “värme­system” entry. 

Please provide one of the following so I can help you further:
• Your building’s EPC record (including the heating system field)  
• The building ID (byggnadsid) or property number (fastighetsnummer) so I can look up the EPC details  

With that information, I can tell you exactly what heating system is installed.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[9]`
- question_coverage (0–10): `[9]`
- calibration (0–10): `[9]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 7 / 15 — `DEMO_EXTRA_BRF_002`

**Expected route:** `building_specific`

### Question

> What heating system does Teknikringen 37A in Stockholm have?

### Model's final answer

```
I’m sorry, but I don’t have any EPC or building‐system data for Teknikringen 37A in Stockholm in the information provided. If you can supply the building’s energy performance certificate details or other technical documentation, I’ll be happy to look up the heating system for you.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[9]`
- question_coverage (0–10): `[9]`
- calibration (0–10): `[9]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 8 / 15 — `BRF_ODEN_10_RADMANSGATAN_31_004_HEATING`

**Expected route:** `building_specific`

### Question

> What is the heating system in my building?

### Model's final answer

```
According to the latest energy performance data for your property (Building ID 17-81-BONDEN9-1), the building’s primary heating system is an air-to-water heat pump.  There is no district-heating connection (district_heating_use = 0).

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[8]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[9]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 9 / 15 — `BRF_ODEN_04_DROTTNING_KRISTINAS_VAG_43A_001_ADVICE`

**Expected route:** `combined`

### Question

> What should my building prioritize to improve energy efficiency?

### Model's final answer

```
Based on your latest energy performance certificate (EPC 2022) the building’s primary energy use is 79 kWh/m²·year (class D) and the total delivered energy is about 91 kWh/m²·year. Roughly 60 % of that is space heating (≈118 600 kWh), 23 % is domestic hot water (≈45 000 kWh) and the remaining 17 % is electricity for fans, pumps, lighting and other loads. To move towards class C and beyond, you should prioritize measures in the following order:

1. Improve the thermal envelope  
   • Upgrade or add insulation to exterior walls, roof and basements to reduce heat losses.  
   • Replace or retrofit windows and doors with low-U-value glazing and ensure good airtightness around frames.  
   • Seal thermal bridges (e.g. at balconies and joints) and close infiltration paths.

2. Optimize the space-heating system  
   • Fine-tune your district-heating control strategy: lower flow temperatures, install weather-compensated controls and thermostatic radiator valves.  
   • Balance and commission t
```

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[10]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[9]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 10 / 15 — `COMB_001`

**Expected route:** `combined`

### Question

> What should our building prioritize if we want to reduce heating costs?

### Model's final answer

```
Since we don’t have specific energy-performance data for your building (for example the current EPC energy class, heated floor area or construction details), the following priorities are general recommendations for most building types. You can tailor them later once you have your EPC report or a professional audit.

1. Improve the thermal envelope  
   • Roof insulation – heat rises, so an under-rafter or attic upgrade often yields large savings.  
   • Wall insulation – cavity-wall or external/internal insulation can cut transmission losses by 30–50 %.  
   • Floor insulation – especially for uninsulated ground floors or suspended timber floors.

2. Increase airtightness and seal leaks  
   • Locate and seal gaps around windows, doors, service penetrations and junctions.  
   • Install weather-stripping on exterior doors and airtight membranes in critical areas.

3. Upgrade windows and exterior doors  
   • High-performance double or triple glazing with low-e coatings and insulated fr
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[10]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[9]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 11 / 15 — `BRF_ODEN_02_PROFESSORSSLINGAN_51_001_ADVICE`

**Expected route:** `combined`

### Question

> What should my building prioritize to improve energy efficiency?

### Model's final answer

```
Based on your building’s energy performance certificate (EPC) and the breakdown of annual energy use, the largest energy consumers are space heating and domestic hot water. To move from an energy class B toward class A (or better), you should concentrate on measures that reduce those two end-uses most effectively.  

1. Envelope improvements  
   • Insulation upgrades – add or improve insulation in the roof, exterior walls and, if possible, in basement or ground-floor slabs.  
   • Window and door replacement or refurbishment – install high-performance glazing and ensure airtight seals around frames.  
   • Air-tightness – carry out a blower-door test and seal leakage paths (around penetrations, service ducts, façade-junctions).  

2. Ventilation optimisation  
   • Heat‐recovery performance – your building already has an FTX system (mechanical supply and exhaust with heat recovery), but you can increase its efficiency by cleaning and upgrading the heat exchanger cores, adjusting fan c
```

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[9]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[10]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 12 / 15 — `BRF_ODEN_09_PROFESSORSSLINGAN_53_001_ADVICE`

**Expected route:** `combined`

### Question

> What should my building prioritize to improve energy efficiency?

### Model's final answer

```
Based on your building’s latest Energy Performance Certificate (EPC 2023), the single largest end‐use today is domestic hot water (DHW), closely followed by space heating. Your building already performs at an above‐average level (Energy Class B, 60 kWh/m²·year versus the reference of 85 kWh/m²·year), but to push towards Class A and further reduce primary energy you should prioritize:

1. Domestic hot water efficiency  
   • Install low‐flow taps and showerheads to reduce hot water demand.  
   • Introduce a solar thermal system or heat‐pump–assisted DHW preheating.  
   • Recover heat from shower drains (drain‐water heat recovery).  

2. Building envelope improvements  
   • Upgrade or add insulation in the roof and exterior walls to reduce transmission losses.  
   • Replace or refurbish windows and ensure airtight seals around frames.  
   • Insulate basement walls or slab to cut heat loss to the ground.  

3. Ventilation and heat-recovery optimization  
   • Verify that your FTX sys
```

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[10]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[10]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 13 / 15 — `BRF_ODEN_10_RADMANSGATAN_31_001_AMBIGUOUS`

**Expected route:** `clarification`

### Question

> What is the heating system in my building? My address is Rådmansgatan 31.

### Model's final answer

```
Rådmansgatan 31 turns out to be the address of three distinct buildings in different municipalities (Motala, Kristinehamn and Stockholm), each with its own energy declaration. Here’s a summary:

1. Motala (Building ID 05-83-RAEFSAN5-1)  
   – Energy declaration approved September 2024 (version 2023)  
   – Main heating system: air-to-water heat pump  
   – No district heating connection  

2. Kristinehamn (Building ID 17-81-BONDEN9-1)  
   – Energy declaration approved April 2019 (version 2019)  
   – Main heating system: air-to-water heat pump  
   – No district heating  

3. Stockholm (Building ID 01-80-HAELSAN10-2)  
   – Energy declaration approved December 2008 (version 1.4)  
   – Main heating system: district heating (fjärrvärme)  

If you refer to the most recent declaration (Motala, 2023), your building is heated by an air-to-water heat pump.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not
```

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[5]`
- question_coverage (0–10): `[7]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 14 / 15 — `CLAR_001`

**Expected route:** `clarification`

### Question

> What should our building prioritize?

### Model's final answer

```
To give you a targeted recommendation, we need some basic information about your building—most importantly its location or unique identifier. Please provide one of the following:

• The street address (including postal code and city)  
• The national Building ID (byggnadsid) or Property Number (fastighetsnummer)  
• The EPC energy class (epc_egienergiklass) if you have it  
• Any other details you know (building type, heated area, age, etc.)  

With that information we can determine whether your building should, for example, focus on improving its energy efficiency, upgrading ventilation, enhancing accessibility or fire safety, or prioritizing other sustainability measures.

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: energy_class, energy_performance, heating_system, ventilation_type.
```

### Retrieved evidence (summary)

```
(no evidence retrieved)
```

### Your scores

- faithfulness (0–10): `[10]`
- answer_relevance (0–10): `[10]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[10]`
- overall_pass (0 or 1): `[1]`
- notes: 

---

## Case 15 / 15 — `BRF_ODEN_01_TULEGATAN_5A_001_AMBIGUOUS`

**Expected route:** `clarification`

### Question

> What is the heating system in my building? My address is Tulegatan 5a.

### Model's final answer

```
Based on the most recent energy performance certificate for Tulegatan 5A (Building ID 01-80-HAELSAN10-2, approved June 2019), your building’s primary heating system is district heating (fjärrvärme) supplied by the Stockholm network.  
• Annual district heating use for space heating: approx. 819 500 kWh  
• Annual district heating use for domestic hot water: approx. 161 300 kWh  

(Note: An older 2012 certificate for a different property at the same street address reports wood‐chip heating in Arboga, but the 2019 Stockholm EPC is the latest and applicable record.)

Note: Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is. I do not have confirmed data for: ventilation_type, electricity_use, energy_declaration_year, renovation_information.
```

### Retrieved evidence (summary)

```
SQL fields used (n=214): 01a_fnr, 50a_deso, 50a_husnridmerge, 50a_uuid, byggnadsid, district_heating_use, energy_class, energy_performance, epc_antalbyggdek_calc, epc_antalgamladek_calc, epc_egenantalbolgh, epc_egenantalkallarplan
```

### Your scores

- faithfulness (0–10): `[9]`
- answer_relevance (0–10): `[9]`
- question_coverage (0–10): `[10]`
- calibration (0–10): `[8]`
- overall_pass (0 or 1): `[1]`
- notes: 

---
