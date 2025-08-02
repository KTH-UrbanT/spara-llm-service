from langgraph.graph import StateGraph, END
from typing import TypedDict, Optional, Dict, Any

# Agents

# Logic modules
from src.logic.address_match_check import check_address_match
from src.logic.sim_flag_check import simulation_needed
from src.logic.cost_estimation_logic import estimate_cost
from src.logic.green_loan_logic import suggest_green_loans

# Initialize agents and clients
from src.agents.instances import (
    parse_intent_agent,
    sql_client,
    vector_query_agent,
    simulation_agent,
    openai_response_agent,
)

# --- State Definition ---
class BuildingState(TypedDict, total=False):
    messages: list[Dict[str, str]]
    metadata: list[Dict[str, Any]]
    thread_id: str

    parsed_intent: str
    address: Optional[str]
    building_info: Optional[dict]
    address_mismatch: Optional[bool]

    sql_result: Optional[dict]
    localized_sql_result: Optional[dict]
    vector_result: Optional[str]

    run_simulation: Optional[bool]
    simulation_result: Optional[Dict[str, Any]]
    estimated_cost: Optional[float]
    green_loan_options: Optional[list]

    response: Optional[str]

# --- SQL Query Nodes ---
def sql_query_node(state: BuildingState) -> BuildingState:
    address = state['metadata'].get("address")
    if not address:
        address = _extract_or_fallback_address(state)
        state["address"] = address

    building_response = sql_client.building_by_address(address)
    if building_response.status_code == 200:
        state["sql_result"] = building_response.json()
    else:
        state["sql_result"] = {}
    return state

def localized_sql_query_node(state: BuildingState) -> BuildingState:
    address = state.get("address")
    if not address:
        address = _extract_or_fallback_address(state)
        state["address"] = address

    building_response = sql_client.building_by_address(address)
    if building_response.status_code == 200:
        state["localized_sql_result"] = building_response.json()
    else:
        state["localized_sql_result"] = {}
    return state

# --- Address Fallback ---
def _extract_or_fallback_address(state: BuildingState) -> str:
    # Check metadata
    for meta in state.get("metadata", []):
        if meta.get("address"):
            return meta["address"]
    # Fallback to username-thread ID lookup
    return sql_client.get_address_by_username(state.get("thread_id", ""))

# --- Vector DB Query ---
def vector_query_node(state: BuildingState) -> BuildingState:
    return vector_query_agent.query(state)

# --- Simulation Logic ---
def simulation_run_node(state: BuildingState) -> BuildingState:
    return simulation_agent.run(state)

# --- Final response generator ---
def generate_response_node(state: BuildingState) -> BuildingState:
    return openai_response_agent.generate(state)

# --- Graph Builder ---
def build_building_flow_graph():
    graph = StateGraph(BuildingState)

    # --- Nodes ---
    graph.add_node("ParseIntent", parse_intent_agent)
#    graph.add_node("CheckAddress", check_address_match)
    graph.add_node("SQLQuery", sql_query_node)
    graph.add_node("LocalizedSQLQuery", localized_sql_query_node)
    graph.add_node("VectorQuery", vector_query_node)
    graph.add_node("SimNeeded", simulation_needed)
    graph.add_node("RunSimulation", simulation_run_node)
    graph.add_node("EstimateCost", estimate_cost)
    graph.add_node("SuggestGreenLoans", suggest_green_loans)
    graph.add_node("GenerateResponse", generate_response_node)

    # --- Flow ---
    graph.set_entry_point("ParseIntent")
    # graph.add_edge("ParseIntent", "CheckAddress")

    # graph.add_conditional_edges(
    #     "CheckAddress",
    #     lambda state: state.get("parsed_intent", "unknown"),
    #     {
    #         "simulation": "SimNeeded",
    #         "query_generic": "SQLQuery",
    #         "query_local": "LocalizedSQLQuery",
    #         "vector": "VectorQuery",
    #         "unknown": "GenerateResponse"
    #     }
    # )

    graph.add_conditional_edges(
        "SimNeeded",
        lambda state: state.get("run_simulation", False),
        {
            True: "RunSimulation",
            False: "GenerateResponse"
        }
    )

    graph.add_edge("RunSimulation", "EstimateCost")
    graph.add_edge("EstimateCost", "SuggestGreenLoans")
    graph.add_edge("SuggestGreenLoans", "GenerateResponse")

    graph.add_edge("SQLQuery", "GenerateResponse")
    graph.add_edge("LocalizedSQLQuery", "GenerateResponse")
    graph.add_edge("VectorQuery", "GenerateResponse")

    graph.set_finish_point("GenerateResponse")

    return graph.compile()