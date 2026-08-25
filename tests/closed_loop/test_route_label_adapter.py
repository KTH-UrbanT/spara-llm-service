"""Route/agent label mapping: the pivot `route_match` — and every headline number — rests on."""
import pytest
from src.evaluation.closed_loop.route_label_adapter import map_route, route_match, agent_match

def _s(**kw) -> dict:
    """A default building-route snapshot; keyword args override individual fields."""
    d = {"top_route": "building", "clarification_fired": False,
         "request_address_fired": False, "specialists_invoked": ["generic_sql_agent"],
         "vector_chunks_retrieved": []}
    d.update(kw); return d

def test_map_generic():
    """Non-building routes pass through as their own label."""
    assert map_route(_s(top_route="generic")) == "generic"

def test_map_conversational():
    """Non-building routes pass through as their own label."""
    assert map_route(_s(top_route="conversational")) == "conversational"

def test_map_building_specific():
    """A building route that ran a SQL specialist and retrieved no chunks is SQL-only."""
    assert map_route(_s()) == "building_specific"

def test_map_combined():
    """`combined` requires the vector agent AND chunks actually returned."""
    assert map_route(_s(specialists_invoked=["generic_sql_agent","vector_db_agent"],
                        vector_chunks_retrieved=[{"chunk_id":"c1"}])) == "combined"

def test_map_combined_no_chunks():
    """Invoking the vector agent but retrieving nothing contributes no evidence.

    It must stay `building_specific` — otherwise a dead vector channel would be credited
    as the richer route on every case that merely attempted it.
    """
    assert map_route(_s(specialists_invoked=["generic_sql_agent","vector_db_agent"],
                        vector_chunks_retrieved=[])) == "building_specific"

def test_map_clarification_fired():
    """An explicit clarification gate means the user got no answer."""
    assert map_route(_s(clarification_fired=True)) == "clarification"

def test_map_request_address_fired():
    """Bouncing back for an address is a non-answer, same as a clarification."""
    assert map_route(_s(request_address_fired=True)) == "clarification"

def test_map_no_sql_specialist():
    """No SQL specialist ran, so no registry fact was retrieved: also `clarification`.

    The label must not depend on WHICH path produced the non-answer.
    """
    assert map_route(_s(specialists_invoked=["vector_db_agent"])) == "clarification"

def test_over_retrieval_building_specific():
    """Over-retrieval does not change the label — only chunks actually returned do."""
    assert map_route(_s(specialists_invoked=["generic_sql_agent","specialized_sql_agent","vector_db_agent"],
                        vector_chunks_retrieved=[])) == "building_specific"

def test_route_match_generic():
    """`route_match` is exact equality against the dataset's expected route."""
    assert route_match(_s(top_route="generic"), "generic") is True
    assert route_match(_s(top_route="generic"), "building_specific") is False

def test_route_match_under_retrieval():
    """Under-retrieval breaks the match: expected `combined`, got SQL only."""
    assert route_match(_s(), "combined") is False

def test_agent_match_generic():
    """The dataset's agent names map onto top-level routes."""
    assert agent_match(_s(top_route="generic"), "GenericAgent") is True
    assert agent_match(_s(top_route="generic"), "BuildingAgent") is False

def test_agent_match_building():
    """The dataset's agent names map onto top-level routes."""
    assert agent_match(_s(), "BuildingAgent") is True

def test_agent_match_absent():
    """A case that pins no expected agent matches vacuously."""
    assert agent_match(_s(), None) is True
