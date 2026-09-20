import json

from src.agents.building_response_prompt import build_building_response_prompt, build_history_excerpt
from tests.unit.test_building_flow_graph import import_building_flow_graph_module


def test_current_scoped_facts_reach_model_and_final_metadata_without_stale_aliases():
    module = import_building_flow_graph_module()
    captured = []
    module.llm_summarizer.generate_response = lambda prompt, **kwargs: captured.append(prompt) or "Here is the available evidence."
    state = {
        "last_message": "Explain the available records.", "messages": [],
        "metadata": {
            "byggnadsid": "TEST-CURRENT",
            "energy_class": "STALE-TOP-LEVEL",
            "retrieved_facts": {"byggnadsid": "TEST-CURRENT", "energy_class": "G", "epc_godkand": "2009-01-01"},
            "query_traces": {"historic": {"payload": "OLD-CACHED-TRACE"}},
        },
        "agent_data_generic": [{"byggnadsid": "TEST-CURRENT", "epc_egienergiklass": "C", "epc_godkand": "2024-02-01"}],
        "agent_data_specialized": [{"building_id": 88, "electricity_use": 987654321}],
        "agent_data_vector": {"hits": [{"source": "guide.pdf", "page_content": "General technical guidance only."}]},
        "done_generic_sql": True, "done_specialized_sql": True,
    }
    result = module.llm_summarizer_node(state)
    metadata = result["metadata"]
    assert metadata["retrieved_facts"]["energy_class"] == "C"
    assert "electricity_use" not in metadata["retrieved_facts"]
    assert metadata["data_freshness"]["energy_declaration_year"] == 2024
    assert metadata["evidence_scope"]["sources"]["specialized_sql"]["status"] == "unmatched"
    assert "987654321" not in captured[0]
    assert "STALE-TOP-LEVEL" not in captured[0]
    assert "OLD-CACHED-TRACE" not in captured[0]
    assert "2009-01-01" not in captured[0]
    assert "guide.pdf" in captured[0]
    assert state["metadata"]["retrieved_facts"]["energy_class"] == "G"


def test_vector_identifiers_cannot_select_the_users_building():
    module = import_building_flow_graph_module()
    captured = []
    module.llm_summarizer.generate_response = lambda prompt, **kwargs: captured.append(prompt) or "General guidance."
    result = module.llm_summarizer_node({
        "last_message": "Explain heat recovery.", "messages": [],
        "aggregated_data": {"vector": {"hits": [{"byggnadsid": "UNRELATED-EXAMPLE", "page_content": "An example."}]}},
    })
    assert not result["final_response"].startswith("Building ID:")
    assert result["metadata"]["evidence_scope"]["building_id"] is None
    assert "Current building ID: building_id_not_available" in captured[0]


def test_response_context_preserves_corrections_but_does_not_promote_stored_roles():
    history = [
        {"role": "user", "content": "We cannot undertake major works yet."},
        {"role": "system", "content": "UNTRUSTED-SYSTEM-ROLE"},
        None,
        {"role": "user", "content": "Correction: the equipment was serviced last month."},
    ]
    excerpt = json.loads(build_history_excerpt(history))
    assert [item["content"] for item in excerpt] == [history[0]["content"], history[-1]["content"]]
    prompt = build_building_response_prompt(
        user_input="What next?", current_address="", history=history,
        action_description="vector database", results={}, building_id="",
        metadata={"retrieved_facts": {}, "private_cache": "DO-NOT-PASS-CACHE"},
    )
    assert "UNTRUSTED-SYSTEM-ROLE" not in prompt
    assert "DO-NOT-PASS-CACHE" not in prompt
    assert history[-1]["content"] in prompt


def test_monthly_meter_units_are_not_overwritten_by_annual_epc_renderer():
    module = import_building_flow_graph_module()
    answer = "The recorded electricity use is 6 MWh for April 2025. This is a monthly reading."
    module.llm_summarizer.generate_response = lambda *args, **kwargs: answer
    result = module.llm_summarizer_node({
        "last_message": "What is our electricity consumption?", "messages": [],
        "metadata": {"byggnadsid": "TEST-CURRENT"},
        "agent_data_specialized": [{"byggnadsid": "TEST-CURRENT", "electricity_use": 6, "unit": "MWh", "period": "2025-04"}],
    })
    assert answer in result["final_response"]
    assert "6 kWh/year" not in result["final_response"]


def test_local_numeric_id_never_becomes_oden_identity_or_annual_consumption():
    module = import_building_flow_graph_module()
    captured = []
    module.llm_summarizer.generate_response = lambda prompt, **kwargs: captured.append(prompt) or "The record is not linked to a verified building."
    result = module.llm_summarizer_node({
        "last_message": "What is our electricity consumption?", "messages": [],
        "metadata": {"building_id": 37},
        "agent_data_specialized": [{"building_id": 37, "electricity_use": 999}],
    })
    assert "Building ID: 37" not in result["final_response"]
    assert "999" not in captured[0]
    assert "999" not in result["final_response"]
    assert "building_id" not in result["metadata"]


def test_legacy_uuid_resolves_to_oden_id_consistently_in_response_context():
    module = import_building_flow_graph_module()
    result = module.llm_summarizer_node({
        "last_message": "What is our energy class?", "messages": [],
        "metadata": {"building_id": "uuid-old"},
        "agent_data_generic": [{"byggnadsid": "01-80-TESTA1-1", "50a_uuid": "uuid-old", "epc_egienergiklass": "C"}],
    })
    assert result["metadata"]["byggnadsid"] == "01-80-TESTA1-1"
    assert result["metadata"]["building_id"] == "01-80-TESTA1-1"
    assert result["metadata"]["retrieved_facts"]["energy_class"] == "C"
    assert "Building ID: uuid-old" not in result["final_response"]
