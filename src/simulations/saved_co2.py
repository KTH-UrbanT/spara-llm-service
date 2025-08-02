import pandas as pd
from typing import Dict, List


# Static emission factors in kg CO₂ per kWh
EMISSION_FACTORS = {
    "electricity": 0.1,  # low-carbon grid (Sweden)
    "district_heating": 0.08,
    "oil": 0.27,
    "natural_gas": 0.25
}


def simulate_energy_use(start_year: int, end_year: int, annual_kwh: float) -> pd.Series:
    """Returns energy use per year in kWh"""
    years = list(range(start_year, end_year + 1))
    return pd.Series([annual_kwh] * len(years), index=years, name="energy_use_kwh")


def calculate_emissions(energy_use: pd.Series, source: str) -> pd.Series:
    """Returns CO₂ emissions per year in kg based on energy source"""
    factor = EMISSION_FACTORS.get(source.lower())
    if factor is None:
        raise ValueError(f"Unknown energy source: {source}")
    return energy_use * factor


def calculate_saved_co2(
    baseline_kwh: float,
    baseline_source: str,
    improved_kwh: float,
    improved_source: str,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """Calculates yearly and total CO₂ savings between two scenarios"""
    
    # Energy use per year
    baseline_energy = simulate_energy_use(start_year, end_year, baseline_kwh)
    improved_energy = simulate_energy_use(start_year, end_year, improved_kwh)
    
    # Emissions
    baseline_emissions = calculate_emissions(baseline_energy, baseline_source)
    improved_emissions = calculate_emissions(improved_energy, improved_source)

    # Savings
    saved = baseline_emissions - improved_emissions

    result = pd.DataFrame({
        "baseline_kgCO2": baseline_emissions,
        "improved_kgCO2": improved_emissions,
        "saved_kgCO2": saved
    })

    result["cumulative_saved_kgCO2"] = result["saved_kgCO2"].cumsum()
    return result


# 🧪 Example usage:
# if __name__ == "__main__":
#     df = calculate_saved_co2(
#         baseline_kwh=20000,
#         baseline_source="oil",
#         improved_kwh=18000,
#         improved_source="electricity",
#         start_year=2025,
#         end_year=2030
#     )

#     print(df)
#     print(f"\nTotal CO₂ saved: {df['saved_kgCO2'].sum():,.2f} kg")
