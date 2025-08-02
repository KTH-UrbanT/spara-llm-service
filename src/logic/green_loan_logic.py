from typing import Dict, Any

def suggest_green_loans(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Suggests green loans based on building data and estimated cost.
    """
    cost = state.get("estimated_cost", {})
    building_info = state.get("building_data", {})

    # TODO: Implement actual green loan suggestion logic
    loan_options = [
        {"type": "GreenLoan", "rate": 1.5, "term": "15 years"},
        {"type": "Renovation Credit", "rate": 2.1, "term": "10 years"}
    ]

    state["green_loan_options"] = loan_options
    return state
