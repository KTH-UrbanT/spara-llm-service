from src.simulations.saved_co2 import calculate_saved_co2

class SimulationAgent: 
    def __init__(self):
        pass
    
    def run(self , state) : 
        print('ran till here simulation')
        return calculate_saved_co2(    baseline_kwh=20000,
    baseline_source="oil",
    improved_kwh=18000,
    improved_source="electricity",
    start_year=2025,
    end_year=2030)