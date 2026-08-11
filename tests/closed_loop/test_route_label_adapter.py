import pytest
from src.evaluation.closed_loop.route_label_adapter import map_route, route_match, agent_match

def _s(**kw) -> dict:
    d = {"top_route": "building", "clarification_fired": False,
         "request_address_fired": False, "specialists_invoked": ["generic_sql_agent"],
         "vector_chunks_retrieved": []}
    d.update(kw); return d

def test_map_generic(): assert map_route(_s(top_route="generic")) == "generic"
def test_map_conversational(): assert map_route(_s(top_route="conversational")) == "conversational"
def test_map_building_specific(): assert map_route(_s()) == "building_specific"
def test_map_combined():
    assert map_route(_s(specialists_invoked=["generic_sql_agent","vector_db_agent"],
                        vector_chunks_retrieved=[{"chunk_id":"c1"}])) == "combined"
def test_map_combined_no_chunks():
    assert map_route(_s(specialists_invoked=["generic_sql_agent","vector_db_agent"],
                        vector_chunks_retrieved=[])) == "building_specific"
def test_map_clarification_fired(): assert map_route(_s(clarification_fired=True)) == "clarification"
def test_map_request_address_fired(): assert map_route(_s(request_address_fired=True)) == "clarification"
def test_map_no_sql_specialist():
    assert map_route(_s(specialists_invoked=["vector_db_agent"])) == "clarification"
def test_over_retrieval_building_specific():
    assert map_route(_s(specialists_invoked=["generic_sql_agent","specialized_sql_agent","vector_db_agent"],
                        vector_chunks_retrieved=[])) == "building_specific"
def test_route_match_generic():
    assert route_match(_s(top_route="generic"), "generic") is True
    assert route_match(_s(top_route="generic"), "building_specific") is False
def test_route_match_under_retrieval():
    assert route_match(_s(), "combined") is False
def test_agent_match_generic():
    assert agent_match(_s(top_route="generic"), "GenericAgent") is True
    assert agent_match(_s(top_route="generic"), "BuildingAgent") is False
def test_agent_match_building(): assert agent_match(_s(), "BuildingAgent") is True
def test_agent_match_absent(): assert agent_match(_s(), None) is True
