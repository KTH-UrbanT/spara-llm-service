"""Specialist nodes must emit the instrumentation `map_route` and field coverage read."""
import os, pytest
from unittest.mock import MagicMock, patch

@pytest.fixture(autouse=True)
def env_vars():
    """Fake Azure/deployment env so the graph module imports without real credentials."""
    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "test", "AZURE_ENDPOINT": "https://x.openai.azure.com/",
        "OPENAI_API_VERSION": "2023-05-15",
        "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME": "gpt-4o",
        "GENERIC_MODEL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "ROUTER_MODEL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "CONVERSATIONAL_MODEL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "BUILDING_MODEL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "SPECIALIZED_SQL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "EVALUATOR_MODE": "off",
    }): yield

def test_generic_sql_emits_fields(monkeypatch):
    """generic_sql must report invoked_* and the fields it used.

    Field coverage and the `clarification` route label are both computed from these,
    so a silent instrumentation gap would read as a system failure.
    """
    import src.agents.building_flow_graph as m
    mock_result = {
        "ok": True,
        "data": [{"byggnadsid": "B1", "epc_egenatemp": 114, "epc_egennybyggar": 1956}],
        "message": "ok",
        "trace": {"query_type": "generic_sql", "execution_status": "success",
                  "rows_returned": 1, "match_strategy": "eq",
                  "returned_values_used": {"byggnadsid": "B1"}},
    }
    monkeypatch.setattr(m.sql_mapper_layer, "execute", MagicMock(return_value=mock_result))
    result = m.generic_sql_agent_node({
        "last_message": "q", "metadata": {"address": "T 1"},
        "messages": [], "aggregated_data_cached": False})
    assert result.get("invoked_generic_sql") is True
    assert len(result.get("sql_fields_generic") or []) > 0  # byggnadsid, epc_egenatemp, ...

def test_vector_emits_chunks(monkeypatch):
    """The vector agent must report the chunks it retrieved — `combined` depends on it."""
    import src.agents.building_flow_graph as m
    monkeypatch.setattr(m.vector_database, "query", MagicMock(return_value=[
        {"score": 0.9, "page_content": "E advice.", "metadata": {"source": "d.pdf"}}]))
    result = m.vector_db_agent_node({
        "last_message": "q", "context": {"effective_query": "heating"},
        "messages": [], "metadata": {}, "aggregated_data_cached": False})
    assert result.get("invoked_vector") is True
    assert len(result.get("vector_chunks_retrieved") or []) == 1

def test_clarification_sets_flag(monkeypatch):
    """The clarification gate must set its flag; `map_route` reads it."""
    import src.agents.building_flow_graph as m
    assert m.clarification_node({"metadata": {}, "last_message": "help"}).get("clarification_fired") is True

def test_request_address_sets_flag(monkeypatch):
    """The address gate must set its flag; `map_route` reads it."""
    import src.agents.building_flow_graph as m
    assert m.request_address_node({"metadata": {}, "last_message": "heating"}).get("request_address_fired") is True
