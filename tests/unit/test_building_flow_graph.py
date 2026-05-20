from tests.support import fresh_import, stub_module


class DummyStateGraph:
    def __init__(self, *args, **kwargs):
        pass


class DummyParseIntentAgent:
    result = {"context": {}}

    def __call__(self, state):
        return type(self).result


class DummySQLMapperLayer:
    def execute(self, *args, **kwargs):
        return {"ok": True, "data": []}


class DummyVectorClientConfig:
    pass


class DummyVectorClient:
    def __init__(self, *args, **kwargs):
        self.hits = []

    def query(self, question: str):
        return list(self.hits)


class DummyOpenAIResponseAgent:
    def generate_response(self, prompt, message_list=None):
        return "Energy class is a rating of a building's energy performance."


class DummySpecializedSQLLayer:
    pass


def import_building_flow_graph_module():
    graph_module = stub_module("langgraph.graph", StateGraph=DummyStateGraph, START="START", END="END")
    stub_module("langgraph", graph=graph_module)
    stub_module("src.agents.parse_intent_agent", ParseIntentAgent=DummyParseIntentAgent)
    stub_module("src.agents.generic_sql_layer", SQL_Mapper_Layer=DummySQLMapperLayer)
    stub_module(
        "src.database.vector_client",
        VectorClient=DummyVectorClient,
        VectorClientConfig=DummyVectorClientConfig,
    )
    stub_module("src.agents.openai_agent", OpenAIResponseAgent=DummyOpenAIResponseAgent)
    stub_module("src.agents.specialized_sql_layer", SpecializedSQLLayer=DummySpecializedSQLLayer)
    stub_module("src.database.hammarby_data", query_address=lambda: [])
    return fresh_import("src.agents.building_flow_graph")


def test_llm_summarizer_accepts_session_state_history_list():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "what is energy class?",
            "messages": [{"role": "user", "content": "what is energy class?"}],
            "context": {"parsed_intent": "vector database", "intent_list": []},
            "session_state": [{"metadata": {"byggnadsid": "01-80-FILOSOFEN2-3"}}],
            "aggregated": "Vector hits:\nEnergy classes run from A to G.",
            "done_vector": True,
        }
    )

    assert result["final_response"].startswith("Building ID: 01-80-FILOSOFEN2-3")
    assert result["session_state"]["metadata"]["byggnadsid"] == "01-80-FILOSOFEN2-3"
    assert result["session_state"]["last_intent_list"] == []


def test_vector_db_agent_persists_structured_hits_for_aggregation():
    module = import_building_flow_graph_module()
    module.vector_database.hits = [
        {
            "page_content": "Energy classes run from A to G.",
            "source": "boverket.pdf",
        }
    ]

    result = module.vector_db_agent_node(
        {
            "last_message": "what is energy class?",
            "context": {},
        }
    )

    assert result["done_vector"] is True
    assert result["agent_outputs_vector"] == ["Vector hits:\nEnergy classes run from A to G."]
    assert result["agent_data_vector"] == {
        "hits": [
            {
                "page_content": "Energy classes run from A to G.",
                "source": "boverket.pdf",
            }
        ],
        "sources": ["boverket.pdf"],
        "snippets": ["Energy classes run from A to G."],
    }


def test_understand_context_restores_prior_intent_for_address_only_follow_up():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "",
            "intents": [],
            "address": None,
            "ambigious": True,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "i live in Professorsslingan 51",
            "messages": [
                {"role": "user", "content": "What is the energy class of my building?"},
                {"role": "assistant", "content": "Can you please provide the building address?"},
                {"role": "user", "content": "i live in Professorsslingan 51"},
            ],
            "metadata": {},
            "session_state": {
                "context": {
                    "parsed_intent": "SQL database",
                    "intent_list": ["SQL database"],
                },
                "metadata": {},
            },
        }
    )

    assert result["context"]["address"] == "Professorsslingan 51"
    assert result["context"]["parsed_intent"] == "SQL database"
    assert result["context"]["intent_list"] == ["SQL database"]
    assert result["metadata"]["address"] == "Professorsslingan 51"
    assert result["metadata"]["address_from_user"] == "Professorsslingan 51"


def test_understand_context_cleans_conversational_address_follow_up():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "",
            "intents": [],
            "address": "sure, i live in Artemisgatan 17",
            "ambigious": True,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "sure, i live in Artemisgatan 17.",
            "messages": [
                {"role": "user", "content": "How do I improve my energy efficiency?"},
                {
                    "role": "assistant",
                    "content": "To give building-specific advice safely, I need the full building address.",
                },
                {"role": "user", "content": "sure, i live in Artemisgatan 17."},
            ],
            "metadata": {},
            "session_state": {
                "context": {
                    "parsed_intent": "SQL database ; vector database",
                    "intent_list": ["SQL database", "vector database"],
                },
                "metadata": {},
            },
        }
    )

    assert result["context"]["address"] == "Artemisgatan 17"
    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert result["metadata"]["address"] == "Artemisgatan 17"


def test_understand_context_promotes_personal_energy_advice_to_hybrid_intent():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "vector database",
            "intents": ["vector database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "How do I improve my energy efficiency?",
            "messages": [
                {"role": "user", "content": "How do I improve my energy efficiency?"},
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert result["context"]["ambiguous"] is False


def test_understand_context_keeps_stored_address_when_new_turn_omits_it():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "What is the energy class of my building?",
            "messages": [
                {"role": "user", "content": "What is the energy class of my building?"},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Professorsslingan 51",
                    "address_from_user": "Professorsslingan 51",
                }
            },
        }
    )

    assert result["context"]["intent_list"] == ["SQL database"]
    assert result["metadata"]["address"] == "Professorsslingan 51"
    assert result["metadata"]["address_from_user"] == "Professorsslingan 51"


def test_understand_context_promotes_nested_building_match_address():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "what is the heating system in my building?",
            "messages": [
                {"role": "user", "content": "what is the heating system in my building?"},
            ],
            "metadata": {
                "building_id": "01-80-SKYTTEN2-2",
                "building_match": {
                    "input_address": "Artemisgatan 17",
                    "matched_address": "Artemisgatan 17",
                },
            },
            "session_state": {},
        }
    )

    assert result["context"]["address"] == "Artemisgatan 17"
    assert result["metadata"]["address"] == "Artemisgatan 17"
    assert result["metadata"]["address_from_user"] == "Artemisgatan 17"


def test_generic_sql_agent_blocks_ambiguous_building_matches():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"50a_uuid": "uuid-1", "epc_idadr": "Main Street 1"},
            {"50a_uuid": "uuid-2", "epc_idadr": "Main Street 2"},
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Main Street"},
            "parallel": {},
        }
    )

    assert result["identity_gate_blocked"] is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"


def test_generic_sql_agent_allows_multiple_records_for_same_exact_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"50a_uuid": "uuid-1", "address": "Artemisgatan 17", "energy_class": "D"},
            {"50a_uuid": "uuid-2", "address": "Artemisgatan 17", "heating_system": "district heating"},
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["building_identity_check"]["multiple_records_same_address"] is True
    assert result["agent_data_generic"][0]["address"] == "Artemisgatan 17"


def test_generic_sql_agent_prefers_exact_address_over_related_variants():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"50a_uuid": "uuid-1", "address": "Artemisgatan 17", "energy_class": "D"},
            {"50a_uuid": "uuid-2", "address": "Artemisgatan 17A", "energy_class": "C"},
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["generic_sql_trace"]["match_strategy"] == "exact_address"
    assert len(result["agent_data_generic"]) == 1
    assert result["agent_data_generic"][0]["address"] == "Artemisgatan 17"


def test_generic_sql_agent_allows_specific_address_even_without_row_address_fields():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"50a_uuid": "uuid-1", "energy_class": "D"},
            {"50a_uuid": "uuid-2", "heating_system": "district heating"},
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["building_identity_check"]["accepted_multiple_records_for_specific_address"] is True


def test_generic_sql_agent_exposes_epc_heating_system_alias():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-80-SKYTTEN2-2",
                "50a_uuid": "5174853a-d8c1-4b87-8a34-d05c054e443d",
                "01a_fnr": "010113926",
                "address": "Artemisgatan 17",
                "epc_idadr": "Artemisgatan 17",
                "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                "epc_egifjarrvarme": 455130,
            }
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["heating_system"] == "district heating"
    assert result["agent_data_generic"][0]["district_heating_use"] == 455130


def test_generic_sql_missing_rows_does_not_block_hybrid_vector_advice():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": False,
        "data": [],
        "message": "No buildings matched address 'Artemisgatan 17'.",
        "trace": {"query_type": "building_by_address", "execution_status": "not_found"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["clarification"]["reason"] == "building_not_found"
    assert result["done_generic_sql"] is True


def test_llm_summarizer_adds_freshness_and_uncertainty_metadata():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "what should our building prioritize?",
            "messages": [{"role": "user", "content": "what should our building prioritize?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Main Street 1"},
            "aggregated_data": {
                "generic_sql": {
                    "building_id": "uuid-1",
                    "energy_declaration_year": 2016,
                    "heating_system": "district heating",
                }
            },
        }
    )

    assert result["metadata"]["data_freshness"]["freshness_status"] == "old"
    assert result["metadata"]["uncertainty"]["confidence"] in {"medium", "low", "high"}
    assert "Note:" in result["final_response"]


def test_llm_summarizer_treats_epc_heating_as_confirmed_fact():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "what is the heating system in my building?",
            "messages": [{"role": "user", "content": "what is the heating system in my building?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Artemisgatan 17"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-SKYTTEN2-2",
                        "address": "Artemisgatan 17",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                    }
                ]
            },
        }
    )

    facts = result["metadata"]["retrieved_facts"]
    uncertainty = result["metadata"]["uncertainty"]
    assert facts["heating_system"] == "district heating"
    assert "heating_system" in uncertainty["confirmed_facts"]
    assert "heating_system" not in uncertainty["missing_facts"]
