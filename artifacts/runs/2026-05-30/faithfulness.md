# Faithfulness drill-down — `artifacts/runs/2026-05-30` / `A_open`

Total rows: 88  ·  Rows with non-fail-open judge verdicts: 64

## Per `expected_route`

| route | n | faith mean | answer_rel mean | q_cov mean | calib mean | composite mean |
|---|---:|---:|---:|---:|---:|---:|
| building_specific | 34 | 9.78 | 10 | 10 | 8.94 | 9.68 |
| clarification | 5 | 7.33 | 9.67 | 10 | 9 | 9.0 |
| combined | 11 | 4.82 | 9.18 | 10 | 7.09 | 7.77 |
| generic | 38 | 1.53 | 9.44 | 10 | 5 | 6.49 |

## Per source-prefix within `expected_route`

| route | source | n | faith mean | composite mean |
|---|---|---:|---:|---:|
| building_specific | BRF_FACT | 2 | None | n/a |
| building_specific | BRF_ODEN | 28 | 9.78 | 9.68 |
| building_specific | DEMO_EXTRA | 4 | None | n/a |
| clarification | BRF_ODEN | 2 | 10 | 9.75 |
| clarification | CLAR | 1 | 2 | 7.5 |
| clarification | DEMO_EXTRA | 2 | None | n/a |
| combined | BRF_ODEN | 8 | 6 | 8.22 |
| combined | COMB | 1 | 1 | 6.25 |
| combined | DEMO_EXTRA | 2 | 2 | 6.75 |
| generic | DEMO_EXTRA | 6 | 1.8 | 6.7 |
| generic | EKR | 30 | 1.52 | 6.42 |
| generic | GEN | 2 | 1 | 6.88 |

## Worst 5 faithfulness cases (verbatim)

### `GEN_002` — faithfulness = 0
- **expected_route**: generic
- **final_route_taken**: generic
- **final_answer**: An energy declaration (energideklaration) gives a housing association’s board or property owner a standardized, independent overview of the building’s current energy performance and concrete improvement opportunities.  In practice it will report:

• Energy performance class (often A–G in Sweden) and specific energy use (kWh/m² Atemp and total annual consumption broken down into heating, hot water and common-area electricity)  
• The heated floor area (Atemp), number of above-ground floors and stairwells, and building type (e.g. detached house, apartment block)  
• A description of the main tec
- **sql_fields_used** (first 8): []
- **judge axes**: {'faithfulness': 0, 'answer_relevance': 9, 'question_coverage': 10, 'calibration': 6}
- **judge composite**: 6.25

### `EKR_GEN_002` — faithfulness = 0
- **expected_route**: generic
- **final_route_taken**: generic
- **final_answer**: Here are several practical measures you can take to get the most out of direct-acting electric radiators—both in terms of comfort and cost-efficiency:

1. Install programmable or smart thermostats  
   • Set lower temperatures (e.g. 18–19 °C) during sleeping hours or when the home is unoccupied, and raise them only shortly before you return.  
   • Some models can learn your daily routine or be controlled remotely via smartphone.

2. Zone your heating  
   • Divide your home into heating zones (e.g. living area, bedrooms, bathroom) and control each radiator independently.  
   • Only heat the 
- **sql_fields_used** (first 8): []
- **judge axes**: {'faithfulness': 0, 'answer_relevance': 9, 'question_coverage': 10, 'calibration': 8}
- **judge composite**: 6.75

### `EKR_GEN_012` — faithfulness = 0
- **expected_route**: generic
- **final_route_taken**: generic
- **final_answer**: Optimizing natural ventilation involves both a careful architectural layout and occupant-controlled strategies. Below are key approaches you can employ:

1. Building Orientation and Form  
   • Orient the long façades toward prevailing winds to capture breezes.  
   • Use a narrow floor plan or “slender” building form so that wind can penetrate more easily from one side to the other.  
   • Avoid large obstructions (e.g., high walls or dense landscaping) immediately upwind of windows or vents.

2. Placement and Size of Openings  
   • Provide high and low openings on opposite sides of a room o
- **sql_fields_used** (first 8): []
- **judge axes**: {'faithfulness': 0, 'answer_relevance': 10, 'question_coverage': 10, 'calibration': 3}
- **judge composite**: 5.75

### `EKR_GEN_014` — faithfulness = 0
- **expected_route**: generic
- **final_route_taken**: generic
- **final_answer**: If you’re thinking about adding solar panels to a building, here are the main factors you’ll want to understand before you invest:

1. Roof suitability and structural capacity  
   • Orientation & tilt – South-facing roofs at a 25–35° pitch capture the most sun in Sweden.  
   • Shading – Even small shadows from trees or neighboring structures can cut output significantly.  
   • Age and condition – A roof in poor shape may need repair or replacement before panels go on.  
   • Load bearing – Panels, rails and snow loads add weight; a structural engineer can verify your roof’s capacity.

2. So
- **sql_fields_used** (first 8): []
- **judge axes**: {'faithfulness': 0, 'answer_relevance': 10, 'question_coverage': 10, 'calibration': 8}
- **judge composite**: 7.0

### `EKR_GEN_023` — faithfulness = 0
- **expected_route**: generic
- **final_route_taken**: generic
- **final_answer**: In practice, the distinction between “normal” (or “standard”) EV charging and “fast” charging is based almost entirely on the power level (kW) and the type of current (AC vs. DC) delivered to the vehicle:

1. Normal (AC) Charging  
   – Power: up to about 22 kW  
   – Voltage/Current: Alternating Current (usually single-phase up to 7 kW, three-phase up to 22 kW)  
   – Typical use cases: home wall-boxes (3–11 kW), workplace chargers, public “mode 3” AC stations  
   – On-board charger in the EV limits the intake: higher-power AC stations simply shorten charge time up to the vehicle’s AC intake
- **sql_fields_used** (first 8): []
- **judge axes**: {'faithfulness': 0, 'answer_relevance': 10, 'question_coverage': 10, 'calibration': 5}
- **judge composite**: 6.25