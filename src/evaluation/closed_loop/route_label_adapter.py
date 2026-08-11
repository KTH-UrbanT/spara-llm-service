"""Map a pipeline-state snapshot to the dataset's route/agent taxonomy.

Called by the harness after a case runs. `expected_route` is the *minimum
sufficient route*: over-retrieval (extra specialists) does not break a match;
under-retrieval (missing the specialist the dataset expects) does.
"""
from __future__ import annotations

_SQL_SPECIALISTS = {"generic_sql_agent", "specialized_sql_agent"}


def map_route(snapshot: dict) -> str:
    top = snapshot.get("top_route", "")
    clar = snapshot.get("clarification_fired", False)
    req = snapshot.get("request_address_fired", False)
    specs = set(snapshot.get("specialists_invoked") or [])
    chunks = snapshot.get("vector_chunks_retrieved") or []
    if top == "generic":
        return "generic"
    if top == "conversational":
        return "conversational"
    if top == "building":
        if clar or req:
            return "clarification"
        if not (specs & _SQL_SPECIALISTS):
            return "clarification"
        if "vector_db_agent" in specs and len(chunks) > 0:
            return "combined"
        return "building_specific"
    return "unknown"


def route_match(snapshot: dict, expected_route: str) -> bool:
    return map_route(snapshot) == expected_route


def agent_match(snapshot: dict, expected_agent: str | None) -> bool:
    if expected_agent is None:
        return True
    mapping = {"GenericAgent": "generic", "BuildingAgent": "building",
               "ConversationalistAgent": "conversational"}
    return snapshot.get("top_route", "") == mapping.get(expected_agent, "__never__")
