"""Building electricity requests must retain the ODEN building profile."""

import copy

import pytest

from tests.unit.test_building_flow_graph import (
    DummyParseIntentAgent,
    import_building_flow_graph_module,
)


BUILDING_ID = "01-80-TESTHUSET1-1"
ADDRESS = "Testgatan 12"


def _route(module, question, intents, metadata=None):
    DummyParseIntentAgent.result = {
        "context": {"intent_list": intents, "parsed_intent": " ; ".join(intents)}
    }
    state = module.understand_context_node({
        "last_message": question,
        "messages": [{"role": "user", "content": question}],
        "metadata": copy.deepcopy(metadata or {}),
        "session_state": {},
    })
    gate = module.route_after_ambiguity(state)
    if gate == "maintain_history":
        state.update(module.maintain_history_node(state) or {})
    return state, gate


@pytest.mark.parametrize("question, intents", [
    ("What measures should we investigate for solar?", ["vector database"]),
    ("Only the shared meter, for last year.", ["Specialized SQL database"]),
    ("Can you compare those readings?", ["Specialized SQL database", "vector database"]),
    ("Compare the readings.", ["query specific database"]),
])
def test_electricity_routes_keep_oden_profile_and_wait_for_it(question, intents):
    module = import_building_flow_graph_module()
    state, gate = _route(module, question, intents, {
        "byggnadsid": BUILDING_ID, "address": ADDRESS,
    })

    assert gate == "maintain_history"
    nodes = module.decision_router(state)
    assert "generic_sql_agent" in nodes
    assert "specialized_sql_agent" in nodes
    assert state["context"]["intent_list"].count("SQL database") == 1
    assert state["context"]["parsed_intent"] == " ; ".join(state["context"]["intent_list"])

    state.update({"done_specialized_sql": True, "done_vector": True})
    state.update(module.wait_for_replies_node(state))
    assert "done_generic_sql" in state["waiting_missing_flags"]
    assert module.route_after_wait(state) == "await_more"
    state["done_generic_sql"] = True
    state.update(module.wait_for_replies_node(state))
    assert module.route_after_wait(state) == "aggregator"


def test_profile_intent_is_not_duplicated_and_specialized_is_preserved():
    module = import_building_flow_graph_module()
    state, gate = _route(module, "Compare those readings.", [
        "SQL database", "Specialized SQL database", "vector database",
    ], {"byggnadsid": BUILDING_ID})
    state.update(module.maintain_history_node(state) or {})
    assert gate == "maintain_history"
    assert state["context"]["intent_list"] == [
        "SQL database", "Specialized SQL database", "vector database",
    ]


def test_ordinary_generic_explanation_does_not_add_building_lookup():
    module = import_building_flow_graph_module()
    state, gate = _route(module, "What is photovoltaic electricity?", ["vector database"])
    assert gate == "maintain_history"
    assert module.decision_router(state) == ["vector_db_agent"]


def test_solar_summary_receives_latest_oden_record_alongside_specialized_data():
    module = import_building_flow_graph_module()
    state, gate = _route(module, "What measures should we investigate for solar?", ["vector database"], {
        "byggnadsid": BUILDING_ID, "address": ADDRESS,
        "selected_brf_building_id": BUILDING_ID,
    })
    assert gate == "maintain_history"
    calls = []
    old_record = {
        "byggnadsid": BUILDING_ID, "epc_idadr": ADDRESS,
        "epc_godkand": "2012-02-01", "epc_egienergiklass2020_calc": "F",
        "epc_egienergiprestanda": 190, "epc_el_calc": 99000,
    }
    latest_record = {
        "byggnadsid": BUILDING_ID, "epc_idadr": ADDRESS,
        "epc_godkand": "2024-02-01", "epc_egienergiklass2020_calc": "C",
        "epc_egienergiprestanda": 82, "epc_el_calc": 64000,
        "epc_egifjarrvarme": None, "epc_egifjarrvarmeuppv": 120000,
        "epc_egifjarrvarmevv": 35000,
    }

    def lookup(operation, **kwargs):
        calls.append((operation, kwargs))
        return {"ok": True, "data": [old_record, latest_record]}

    module.sql_mapper_layer.execute = lookup
    module._address_list_raw = [ADDRESS]
    module.specialized_layer.route = lambda value: ("answer_query", {})
    module.specialized_layer.execute = lambda *args: {"ok": True, "data": []}
    generated_prompts = []

    def summarize(prompt, message_list=None, *, language=None):
        generated_prompts.append(prompt)
        return "Compare the expected solar production with the shared meter's load profile."

    module.llm_summarizer.generate_response = summarize
    nodes = module.decision_router(state)
    # Model the graph's shallow merge of concurrent branch outputs: every
    # branch sees the same input, so specialized metadata may be applied last.
    branch_updates = [getattr(module, node + "_node")(copy.deepcopy(state)) for node in nodes]
    for update in branch_updates:
        state.update(update)
    state.update(module.aggregator_node(state))
    result = module.llm_summarizer_node(state)

    assert calls == [("buildings_by_building_id", {"building_id": BUILDING_ID})]
    facts = result["metadata"]["retrieved_facts"]
    assert facts["calculated_energy_class"] == "C"
    assert "energy_class" not in facts
    assert facts["energy_performance"] == 82
    assert facts["electricity_use"] == 64000
    assert result["metadata"]["data_freshness"]["energy_declaration_year"] == 2024
    assert "district_heating_use" not in result["metadata"]["uncertainty"]["missing_facts"]
    assert "energy_class" in result["metadata"]["uncertainty"]["missing_facts"]
    assert "electricity_use" not in result["metadata"]["uncertainty"]["missing_facts"]
    assert "Some building details may be outdated" not in result["final_response"]
    assert "I do not have confirmed data for:" not in result["final_response"]
    assert "2024-02-01" in generated_prompts[0]
    assert "2012-02-01" not in generated_prompts[0]
