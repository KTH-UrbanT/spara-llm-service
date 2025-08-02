from typing import Dict, Any

def simulation_needed(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Decides whether a simulation is needed based on intent and data quality.
    """
    intent = state.get("parsed_intent", "")

    # TODO: Implement actual logic to decide if simulation is needed
    should_simulate = "simulation" in intent.lower()

    state["run_simulation"] = should_simulate
    return state
