"""Map a pipeline-state snapshot to the dataset's route/agent taxonomy.

Called by the harness after a case runs. `expected_route` is the *minimum
sufficient route*: over-retrieval (extra specialists) does not break a match;
under-retrieval (missing the specialist the dataset expects) does.
"""
from __future__ import annotations

_SQL_SPECIALISTS = {"generic_sql_agent", "specialized_sql_agent"}


def map_route(snapshot: dict) -> str:
    """Classify what the pipeline actually did into one dataset route label.

    Reads observed behaviour, not the router's stated intent: a case that picked
    `building` but asked for an address instead of querying anything is labelled
    `clarification`, because that is what the user received.
    """
    top = snapshot.get("top_route", "")
    clar = snapshot.get("clarification_fired", False)
    req = snapshot.get("request_address_fired", False)
    specs = set(snapshot.get("specialists_invoked") or [])
    chunks = snapshot.get("vector_chunks_retrieved") or []
    # Non-building routes are terminal labels — no specialists run under them.
    if top == "generic":
        return "generic"
    if top == "conversational":
        return "conversational"
    if top == "building":
        # Two distinct ways to end up answering nothing, both labelled `clarification`:
        # the gate fired explicitly (asked the user for an address / a clarification), ...
        if clar or req:
            return "clarification"
        # ... or no SQL specialist ever ran, so no registry fact was retrieved. The user
        # got a non-answer either way, so the label must not depend on which path got there.
        if not (specs & _SQL_SPECIALISTS):
            return "clarification"
        # `combined` needs the vector agent to have actually returned something: invoking it
        # and retrieving zero chunks contributed no free-text evidence, so that is still a
        # plain SQL answer and must not be credited as the richer route.
        if "vector_db_agent" in specs and len(chunks) > 0:
            return "combined"
        return "building_specific"
    return "unknown"


def route_match(snapshot: dict, expected_route: str) -> bool:
    """Whether the observed route equals the dataset's expected route."""
    return map_route(snapshot) == expected_route


def agent_match(snapshot: dict, expected_agent: str | None) -> bool:
    """Whether the top-level agent matches; vacuously true when the case pins none.

    The `__never__` default makes an unrecognised `expected_agent` fail loudly rather
    than match whatever `top_route` happens to hold.
    """
    if expected_agent is None:
        return True
    mapping = {"GenericAgent": "generic", "BuildingAgent": "building",
               "ConversationalistAgent": "conversational"}
    return snapshot.get("top_route", "") == mapping.get(expected_agent, "__never__")
