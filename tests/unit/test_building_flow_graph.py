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


def test_llm_summarizer_marks_same_building_multiple_addresses():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "Show me the data",
            "messages": [{"role": "user", "content": "Show me the data"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {
                "address": "Artemisgatan 17",
                "address_from_user": "Artemisgatan 17",
                "building_identity_check": {
                    "status": "passed",
                    "matched_building_id": "01-80-SKYTTEN2-2",
                    "matched_address": "Artemisgatan 13",
                    "ambiguous": False,
                },
            },
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-SKYTTEN2-2",
                        "epc_idadr": "Artemisgatan 13",
                        "epc_egienergiklass2020_calc": "F",
                        "epc_egiprimarenergital2020_calc": 145,
                        "epc_egienergiprestanda": 201,
                    }
                ]
            },
            "done_generic_sql": True,
        }
    )

    metadata = result["metadata"]
    assert metadata["same_building_multiple_addresses"] is True
    assert metadata["requested_address"] == "Artemisgatan 17"
    assert metadata["epc_record_address"] == "Artemisgatan 13"
    assert metadata["retrieved_facts"]["address"] == "Artemisgatan 17"
    assert metadata["retrieved_facts"]["epc_idadr"] == "Artemisgatan 13"


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


def test_understand_context_recovers_address_from_initial_i_live_in_turn():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": True,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "so help me with this. I live in professorsslingan 10",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hi there! How can I help you today?"},
                {"role": "user", "content": "so help me with this. I live in professorsslingan 10"},
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["context"]["address"] == "professorsslingan 10"
    assert result["context"]["parsed_intent"] == "SQL database"
    assert result["metadata"]["address"] == "professorsslingan 10"
    assert result["metadata"]["address_from_user"] == "professorsslingan 10"


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
    assert result["context"]["advice_type"] == "ecm"
    assert result["context"]["answer_focus"] == "How do I improve my energy efficiency?"
    assert result["metadata"]["pending_building_advice_request"]["type"] == "ecm"


def test_understand_context_restores_pending_ecm_request_for_brf_identity_followup():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    pending = {
        "question": "We are a BRF in Stockholm. How can we reduce our heating costs?",
        "type": "ecm",
        "context": {
            "parsed_intent": "SQL database ; vector database",
            "intent_list": ["SQL database", "vector database"],
            "effective_query": "reduce heating costs energy conservation measures ECM",
            "answer_focus": "We are a BRF in Stockholm. How can we reduce our heating costs?",
            "advice_type": "ecm",
        },
    }

    result = module.understand_context_node(
        {
            "last_message": "ohh, sorry. We are brf Sjöstaden 2",
            "messages": [
                {
                    "role": "user",
                    "content": "We are a BRF in Stockholm. How can we reduce our heating costs?",
                },
                {
                    "role": "assistant",
                    "content": "Please share the full street address so I can use the correct building.",
                },
                {"role": "user", "content": "ohh, sorry. We are brf Sjöstaden 2"},
            ],
            "metadata": {},
            "session_state": {"metadata": {"pending_building_advice_request": pending}},
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert result["context"]["answer_focus"] == pending["question"]
    assert result["context"]["advice_type"] == "ecm"
    assert "energy conservation measures" in result["context"]["effective_query"]


def test_understand_context_promotes_ecm_followup_with_prior_building_context():
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
            "last_message": "so, what are the relevant ecms",
            "messages": [
                {"role": "user", "content": "what are the EPC metrics for Öregrundsgatan 9"},
                {
                    "role": "assistant",
                    "content": "Building ID: 01-80-LISSABON2-2\n\nEnergy class: F",
                },
                {"role": "user", "content": "so, what are the relevant ecms"},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Öregrundsgatan 9",
                    "address_from_user": "Öregrundsgatan 9",
                    "byggnadsid": "01-80-LISSABON2-2",
                    "retrieved_facts": {
                        "energy_class": "F",
                        "energy_performance": 199,
                        "heating_system": "district heating",
                    },
                }
            },
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert result["context"]["ambiguous"] is False
    assert result["metadata"]["address"] == "Öregrundsgatan 9"
    assert "district heating" in result["context"]["effective_query"]
    assert "energy class: F" in result["context"]["effective_query"]


def test_understand_context_keeps_general_ecm_question_vector_only():
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
            "last_message": "what are ECMs in general?",
            "messages": [{"role": "user", "content": "what are ECMs in general?"}],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Öregrundsgatan 9",
                    "byggnadsid": "01-80-LISSABON2-2",
                }
            },
        }
    )

    assert result["context"]["parsed_intent"] == "vector database"
    assert result["context"]["intent_list"] == ["vector database"]
    assert "effective_query" not in result["context"]


def test_understand_context_promotes_ventilation_type_overview_with_building_context_to_hybrid():
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
            "last_message": "what are the other types of ventilation systems?",
            "messages": [
                {"role": "user", "content": "what is my ventilation system?"},
                {
                    "role": "assistant",
                    "classification": "building_specific",
                    "content": "Building ID: 18-80-SKRIKAN4-1\nYour building's ventilation type is Självdrag.",
                },
                {"role": "user", "content": "what are the other types of ventilation systems?"},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Sveavägen 17",
                    "address_from_user": "Sveavägen 17",
                    "byggnadsid": "18-80-SKRIKAN4-1",
                    "retrieved_facts": {
                        "ventilation_type": "Självdrag",
                    },
                }
            },
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert "current ventilation type: Självdrag" in result["context"]["effective_query"]
    assert "ventilation types" in result["context"]["effective_query"]
    assert result["metadata"]["address"] == "Sveavägen 17"


def test_understand_context_promotes_heating_kinds_overview_with_building_context_to_hybrid():
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
            "last_message": "what are the other kinds of heating systems available for heating builsinga?",
            "messages": [
                {"role": "user", "content": "what is my heating system?"},
                {
                    "role": "assistant",
                    "classification": "building_specific",
                    "content": "Building ID: 18-80-SKRIKAN4-1\nYour building's heating system is district heating.",
                },
                {"role": "user", "content": "what are the other kinds of heating systems available for heating builsinga?"},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Sveavägen 17",
                    "address_from_user": "Sveavägen 17",
                    "byggnadsid": "18-80-SKRIKAN4-1",
                    "retrieved_facts": {
                        "heating_system": "district heating",
                    },
                }
            },
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert "current heating system: district heating" in result["context"]["effective_query"]
    assert "heating systems" in result["context"]["effective_query"]
    assert result["metadata"]["address"] == "Sveavägen 17"


def test_understand_context_promotes_building_concept_definition_followup_to_hybrid():
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
            "last_message": "what does FTX mean?",
            "messages": [
                {"role": "user", "content": "what is my ventilation system?"},
                {
                    "role": "assistant",
                    "classification": "building_specific",
                    "content": "Building ID: 01-80-FILOSOFEN2-3\nYour building's ventilation type is FTX.",
                },
                {"role": "user", "content": "what does FTX mean?"},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Professorsslingan 51",
                    "byggnadsid": "01-80-FILOSOFEN2-3",
                    "retrieved_facts": {
                        "ventilation_type": "FTX",
                    },
                }
            },
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert "current ventilation type: FTX" in result["context"]["effective_query"]


def test_understand_context_promotes_generic_quality_followup_to_hybrid_with_building_context():
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
            "last_message": "is that good?",
            "messages": [
                {"role": "user", "content": "what is my energy class?"},
                {
                    "role": "assistant",
                    "classification": "building_specific",
                    "content": "Building ID: 01-80-FILOSOFEN2-3\nYour building's energy class is B.",
                },
                {"role": "user", "content": "is that good?"},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Professorsslingan 51",
                    "byggnadsid": "01-80-FILOSOFEN2-3",
                    "retrieved_facts": {
                        "energy_class": "B",
                        "energy_performance": 63,
                    },
                }
            },
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert "current energy class: B" in result["context"]["effective_query"]
    assert "current energy performance: 63" in result["context"]["effective_query"]


def test_understand_context_keeps_ventilation_type_overview_vector_only_without_building_context():
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
            "last_message": "what are the other types of ventilation systems?",
            "messages": [{"role": "user", "content": "what are the other types of ventilation systems?"}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["context"]["parsed_intent"] == "vector database"
    assert result["context"]["intent_list"] == ["vector database"]
    assert "effective_query" not in result["context"]


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


def test_understand_context_does_not_treat_year_comparison_as_address():
    module = import_building_flow_graph_module()
    message = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
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
            "last_message": message,
            "messages": [
                {
                    "role": "assistant",
                    "classification": "building_specific",
                    "content": "Building ID: 01-80-FYSIKERN1-1\nEnergy class 2019: G\nEnergy class 2020: F",
                },
                {"role": "user", "content": message},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Professorsslingan 10",
                    "address_from_user": "Professorsslingan 10",
                    "byggnadsid": "01-80-FYSIKERN1-1",
                }
            },
        }
    )

    assert module._extract_address_candidate_from_text(message) is None
    assert result["context"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address_from_user"] == "Professorsslingan 10"


def test_extract_address_candidate_strips_city_context_from_prose():
    module = import_building_flow_graph_module()

    address = module._extract_address_candidate_from_text(
        "For the building at Nimrodsgatan 7 in Stockholm, what is our energy performance?"
    )

    assert address == "Nimrodsgatan 7"
    assert (
        module._extract_address_location_hint_from_text(
            "For the building at Nimrodsgatan 7 in Stockholm, what is our energy performance?",
            address,
        )
        == "Stockholm"
    )


def test_extract_address_candidate_handles_city_context_for_non_suffix_street_name():
    module = import_building_flow_graph_module()

    message = "For Professorsslingan 51 in Stockholm, what is the energy class?"
    address = module._extract_address_candidate_from_text(message)

    assert address == "Professorsslingan 51"
    assert module._extract_address_location_hint_from_text(message, address) == "Stockholm"


def test_extract_address_location_hint_from_later_live_clause():
    module = import_building_flow_graph_module()

    message = (
        "i live in Sveavägen 17. What is the building's energy performance?\n"
        "i live in Örebro"
    )
    address = module._extract_address_candidate_from_text(message)

    assert address == "Sveavägen 17"
    assert module._extract_address_location_hint_from_text(message, address) == "Örebro"


def test_extract_address_location_hint_from_later_clause_without_address_preposition():
    module = import_building_flow_graph_module()

    message = (
        "i live Kungsgatan 10. What is my energy performance?\n"
        "i live in Växjö"
    )
    address = module._extract_address_candidate_from_text(message)

    assert address == "Kungsgatan 10"
    assert module._extract_address_location_hint_from_text(message, address) == "Växjö"


def test_extract_address_without_preposition_for_non_suffix_street_name():
    module = import_building_flow_graph_module()

    message = (
        "i live Professorsslingan 51. What is my energy performance?\n"
        "i live in Stockholm"
    )
    address = module._extract_address_candidate_from_text(message)

    assert address == "Professorsslingan 51"
    assert module._extract_address_location_hint_from_text(message, address) == "Stockholm"


def test_understand_context_recovers_address_and_location_hint_from_raw_message():
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
            "last_message": "For Ringvägen 10 in Täby, what is the energy class?",
            "messages": [{"role": "user", "content": "For Ringvägen 10 in Täby, what is the energy class?"}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Täby"
    assert result["context"]["address_location_hint"] == "Täby"


def test_understand_context_recovers_comma_city_pair_strictly():
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
            "last_message": "I live at Ringvägen 10, Huddinge. What is the building's energy performance?",
            "messages": [
                {
                    "role": "user",
                    "content": "I live at Ringvägen 10, Huddinge. What is the building's energy performance?",
                }
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Huddinge"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["address_location_hint"] == "Huddinge"


def test_understand_context_recovers_address_and_later_location_hint_from_raw_message():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }
    message = (
        "i live in Sveavägen 17. What is the building's energy performance?\n"
        "i live in Örebro"
    )

    result = module.understand_context_node(
        {
            "last_message": message,
            "messages": [{"role": "user", "content": message}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Sveavägen 17"
    assert result["metadata"]["address_location_hint"] == "Örebro"
    assert result["context"]["address_location_hint"] == "Örebro"


def test_understand_context_recovers_later_location_hint_without_address_preposition():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }
    message = (
        "i live Kungsgatan 10. What is my energy performance?\n"
        "i live in Växjö"
    )

    result = module.understand_context_node(
        {
            "last_message": message,
            "messages": [{"role": "user", "content": message}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Kungsgatan 10"
    assert result["metadata"]["address_location_hint"] == "Växjö"
    assert result["context"]["address_location_hint"] == "Växjö"


def test_understand_context_uses_location_only_followup_for_ambiguous_address():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": None,
            "intents": [],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "oh yes sure, I live in Katrineholm",
            "messages": [{"role": "user", "content": "oh yes sure, I live in Katrineholm"}],
            "metadata": {},
            "session_state": {
                "context": {
                    "parsed_intent": "SQL database",
                    "intent_list": ["SQL database"],
                },
                "metadata": {
                    "address": "Ringvägen 10",
                    "address_from_user": "Ringvägen 10",
                    "clarification": {
                        "needed": True,
                        "reason": "ambiguous_address",
                    },
                    "building_identity_check": {
                        "status": "ambiguous",
                    },
                },
            },
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Katrineholm"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["parsed_intent"] == "SQL database"


def test_understand_context_uses_pending_ambiguous_address_memory_key():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": None,
            "intents": [],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "i live in huddinge",
            "messages": [{"role": "user", "content": "i live in huddinge"}],
            "metadata": {
                "pending_ambiguous_address": "Ringvägen 10",
                "clarification": {
                    "needed": True,
                    "reason": "ambiguous_address",
                },
                "building_identity_check": {
                    "status": "ambiguous",
                },
            },
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "huddinge"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["address_location_hint"] == "huddinge"
    assert result["context"]["parsed_intent"] == "SQL database"


def test_understand_context_uses_first_location_followup_after_live_ambiguous_prompt():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": None,
            "intents": [],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "i live in Huddinge",
            "messages": [
                {
                    "role": "user",
                    "content": "I live at Ringvägen 10. What is the building's energy performance?",
                },
                {
                    "role": "assistant",
                    "content": (
                        "I found more than one possible building match for that address. "
                        "Please provide the city, postcode, municipality, BRF name, "
                        "or exact building ID so I use the correct building."
                    ),
                },
                {"role": "user", "content": "i live in Huddinge"},
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Huddinge"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["address_location_hint"] == "Huddinge"
    assert result["context"]["parsed_intent"] == "SQL database"


def test_understand_context_recovers_ambiguous_address_from_recent_messages_without_metadata():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": None,
            "intents": [],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "i live in Täby",
            "messages": [
                {
                    "role": "user",
                    "content": "I live at Exempelgatan 10. What is the building's energy performance?",
                },
                {
                    "role": "assistant",
                    "content": (
                        "I found several possible building records for that address. "
                        "Please provide the city, postcode, municipality, BRF name, or exact building ID."
                    ),
                },
                {"role": "user", "content": "i live in Täby"},
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Exempelgatan 10"
    assert result["metadata"]["address_location_hint"] == "Täby"
    assert result["context"]["address"] == "Exempelgatan 10"
    assert result["context"]["address_location_hint"] == "Täby"
    assert result["context"]["parsed_intent"] == "SQL database"


def test_understand_context_preserves_location_hint_on_later_building_followup():
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
            "last_message": "What is the building's energy performance?",
            "messages": [{"role": "user", "content": "What is the building's energy performance?"}],
            "metadata": {
                "address": "Ringvägen 10",
                "address_from_user": "Ringvägen 10",
                "address_location_hint": "Stockholm",
                "clarification": {
                    "needed": False,
                    "reason": None,
                    "resolved": True,
                },
            },
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Stockholm"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["address_location_hint"] == "Stockholm"


def test_understand_context_discards_bad_parser_address_and_uses_stored_address():
    module = import_building_flow_graph_module()
    message = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": message,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": message,
            "messages": [{"role": "user", "content": message}],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Professorsslingan 10",
                    "address_from_user": "Professorsslingan 10",
                }
            },
        }
    )

    assert result["context"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address"] == "Professorsslingan 10"


def test_understand_context_drops_polluted_metadata_address():
    module = import_building_flow_graph_module()
    bad_address = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
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
            "last_message": "show me the data",
            "messages": [{"role": "user", "content": "show me the data"}],
            "metadata": {
                "address": bad_address,
                "address_from_user": bad_address,
                "byggnadsid": "01-80-FYSIKERN1-1",
            },
            "session_state": {},
        }
    )

    assert result["context"].get("address") is None
    assert "address" not in result["metadata"]
    assert "address_from_user" not in result["metadata"]
    assert result["metadata"]["byggnadsid"] == "01-80-FYSIKERN1-1"


def test_understand_context_extracts_building_id_from_user_text():
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
            "last_message": "use this building 01-80-BRAEDGAARDEN9-2",
            "messages": [{"role": "user", "content": "use this building 01-80-BRAEDGAARDEN9-2"}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["byggnadsid"] == "01-80-BRAEDGAARDEN9-2"
    assert result["metadata"]["building_id_from_user"] == "01-80-BRAEDGAARDEN9-2"


def test_understand_context_prefers_valid_nested_address_over_polluted_address():
    module = import_building_flow_graph_module()
    bad_address = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
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
            "last_message": "show me the data",
            "messages": [{"role": "user", "content": "show me the data"}],
            "metadata": {
                "address": bad_address,
                "address_from_user": bad_address,
                "building_identity_check": {
                    "matched_address": "Professorsslingan 10",
                    "matched_building_id": "01-80-FYSIKERN1-1",
                },
            },
            "session_state": {},
        }
    )

    assert result["context"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address_from_user"] == "Professorsslingan 10"


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


def test_brf_resolution_asks_for_selection_when_brf_has_multiple_buildings():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden",
            "byggnadsid": "01-80-CIGARREN2-1",
            "fastighet": "Cigarren 2",
            "address": "Tegelviksgatan 71",
            "postnr": "11647",
            "postort": "Stockholm",
        },
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden",
            "byggnadsid": "01-80-CIGARREN2-2",
            "fastighet": "Cigarren 2",
            "address": "Tegelviksgatan 77",
            "postnr": "11647",
            "postort": "Stockholm",
        },
    ]

    result = module.brf_resolution_node(
        {
            "last_message": "We are BRF Sjöstaden, what ventilation system do we have?",
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {},
        }
    )

    metadata = result["metadata"]
    assert metadata["brf_name"] == "Sjöstaden"
    assert metadata["clarification"]["needed"] is True
    assert metadata["clarification"]["reason"] == "ambiguous_brf"
    assert "1. 01-80-CIGARREN2-1" in metadata["clarification"]["question_asked"]
    assert "2. 01-80-CIGARREN2-2" in metadata["clarification"]["question_asked"]
    assert metadata["pending_brf_resolution"]["original_question"].startswith("We are BRF")


def test_brf_resolution_uses_recent_brf_name_for_building_specific_followup():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden 1",
            "byggnadsid": "01-80-HALVOEN1-3",
            "fastighet": "Halvön 1",
            "address": "Hammarby Allé 163",
            "postnr": "12065",
            "postort": "Stockholm",
        },
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden 1",
            "byggnadsid": "01-80-HALVOEN1-3",
            "fastighet": "Halvön 1",
            "address": "Hammarby Allé 165",
            "postnr": "12065",
            "postort": "Stockholm",
        },
    ]

    result = module.brf_resolution_node(
        {
            "last_message": "yes, please give me building specific advice",
            "messages": [
                {
                    "role": "user",
                    "content": "I live in brf Sjöstaden 1. How can we improve our heating bills?",
                },
                {"role": "assistant", "classification": "generic", "content": "General advice..."},
                {"role": "user", "content": "yes, please give me building specific advice"},
            ],
            "context": {"parsed_intent": "SQL database ; vector database", "intent_list": ["SQL database", "vector database"]},
            "metadata": {},
        }
    )

    metadata = result["metadata"]
    assert metadata["brf_name"] == "Sjöstaden 1"
    assert metadata["byggnadsid"] == "01-80-HALVOEN1-3"
    assert metadata["brf_resolution"]["status"] == "resolved_unique_building"
    assert metadata["clarification"]["needed"] is False


def test_brf_resolution_does_not_treat_location_phrase_as_brf_name():
    module = import_building_flow_graph_module()

    assert (
        module._extract_brf_name_from_text(
            "We are a BRF in Stockholm. How can we reduce our heating costs?"
        )
        is None
    )
    assert (
        module._extract_brf_name_from_text(
            "We are a BRF with very high heating bills in winter. What can we do?"
        )
        is None
    )
    assert module._extract_brf_name_from_text("How many BRF do we have?") is None


def test_brf_resolution_clears_failed_lookup_for_new_public_question():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: (_ for _ in ()).throw(
        AssertionError("stale BRF lookup should not be retried")
    )

    result = module.brf_resolution_node(
        {
            "last_message": "what is BRF",
            "messages": [
                {"role": "user", "content": "How many BRF do we have?"},
                {"role": "assistant", "content": "I could not find building addresses for BRF do we have."},
                {"role": "user", "content": "what is BRF"},
            ],
            "context": {"parsed_intent": "vector database", "intent_list": ["vector database"]},
            "metadata": {
                "brf_name": "do we have",
                "brf_resolution": {"status": "not_found", "brf_name": "do we have"},
                "clarification": {
                    "needed": True,
                    "reason": "brf_not_found",
                    "question_asked": "I could not find building addresses for BRF do we have.",
                    "resolved": False,
                },
            },
        }
    )

    metadata = result["metadata"]
    assert "brf_name" not in metadata
    assert "brf_resolution" not in metadata
    assert "clarification" not in metadata


def test_brf_resolution_selects_pending_building_and_restores_original_question():
    module = import_building_flow_graph_module()
    pending = {
        "brf_name": "Sjöstaden",
        "original_question": "We are BRF Sjöstaden, what ventilation system do we have?",
        "original_context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
        "options": [
            {
                "choice": "1",
                "byggnadsid": "01-80-CIGARREN2-1",
                "addresses": ["Tegelviksgatan 71, 11647 Stockholm"],
            },
            {
                "choice": "2",
                "byggnadsid": "01-80-CIGARREN2-2",
                "addresses": ["Tegelviksgatan 77, 11647 Stockholm"],
            },
        ],
        "question": "Which building?",
    }

    result = module.brf_resolution_node(
        {
            "last_message": "2",
            "context": {"parsed_intent": "", "intent_list": []},
            "metadata": {"pending_brf_resolution": pending},
        }
    )

    assert result["last_message"] == pending["original_question"]
    assert result["context"]["intent_list"] == ["SQL database"]
    assert result["metadata"]["byggnadsid"] == "01-80-CIGARREN2-2"
    assert result["metadata"]["selected_brf_lookup_address"] == "Tegelviksgatan 77"
    assert result["metadata"]["clarification"]["needed"] is False
    assert "pending_brf_resolution" not in result["metadata"]


def test_brf_resolution_carries_pending_ecm_question_into_brf_selection():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden 2",
            "byggnadsid": "01-80-BRAEDGAARDEN9-2",
            "address": "Aktergatan 11",
            "postnr": "12066",
            "postort": "Stockholm",
        },
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden 2",
            "byggnadsid": "01-80-BRAEDGAARDEN9-1",
            "address": "Lugnets Allé 22",
            "postnr": "12066",
            "postort": "Stockholm",
        },
    ]

    result = module.brf_resolution_node(
        {
            "last_message": "ohh, sorry. We are brf Sjöstaden 2",
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
                "answer_focus": "We are a BRF in Stockholm. How can we reduce our heating costs?",
                "advice_type": "ecm",
            },
            "metadata": {},
        }
    )

    pending = result["metadata"]["pending_brf_resolution"]
    assert pending["original_question"] == "We are a BRF in Stockholm. How can we reduce our heating costs?"
    assert pending["original_context"]["advice_type"] == "ecm"


def test_brf_resolution_selects_pending_building_from_building_id_reply():
    module = import_building_flow_graph_module()
    pending = {
        "brf_name": "Sjöstaden 2",
        "original_question": "I live in BRF Sjöstaden 2. What ventilation system do we have?",
        "original_context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
        "options": [
            {
                "choice": "1",
                "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                "addresses": ["Aktergatan 11, 12066 Stockholm", "Aktergatan 13, 12066 Stockholm"],
            },
            {
                "choice": "2",
                "byggnadsid": "01-80-BRAEDGAARDEN9-1",
                "addresses": ["Lugnets Allé 22, 12066 Stockholm"],
            },
        ],
        "question": "Which building?",
    }

    result = module.brf_resolution_node(
        {
            "last_message": "use this building 01-80-BRAEDGAARDEN9-2",
            "context": {"parsed_intent": "", "intent_list": []},
            "metadata": {"pending_brf_resolution": pending},
        }
    )

    assert result["last_message"] == pending["original_question"]
    assert result["metadata"]["byggnadsid"] == "01-80-BRAEDGAARDEN9-2"
    assert result["metadata"]["selected_brf_lookup_address"] == "Aktergatan 11"
    assert result["metadata"]["clarification"]["needed"] is False
    assert "pending_brf_resolution" not in result["metadata"]


def test_brf_resolution_ignores_stale_pending_resolution_after_building_selected():
    module = import_building_flow_graph_module()

    result = module.brf_resolution_node(
        {
            "last_message": "when was this building built?",
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {
                "byggnadsid": "01-80-HEDVIG15-1",
                "selected_brf_building_id": "01-80-HEDVIG15-1",
                "pending_brf_resolution": {
                    "brf_name": "Solgläntan 1",
                    "question": "Which building should I use?",
                    "options": [
                        {
                            "choice": "1",
                            "byggnadsid": "01-80-HEDVIG15-1",
                            "addresses": ["Bennebolsgatan 34, 16350 Spånga"],
                        }
                    ],
                },
                "brf_resolution": {
                    "status": "resolved_by_user_selection",
                    "selected_building_id": "01-80-HEDVIG15-1",
                },
            },
        }
    )

    assert result["metadata"]["byggnadsid"] == "01-80-HEDVIG15-1"
    assert "pending_brf_resolution" not in result["metadata"]
    assert "clarification" not in result["metadata"]
    assert "last_message" not in result


def test_brf_resolution_uses_unique_building_id_directly():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden",
            "byggnadsid": "01-80-CIGARREN2-1",
            "address": "Tegelviksgatan 71",
            "postnr": "11647",
            "postort": "Stockholm",
        },
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden",
            "byggnadsid": "01-80-CIGARREN2-1",
            "address": "Tengdahlsgatan 40",
            "postnr": "11647",
            "postort": "Stockholm",
        },
    ]

    result = module.brf_resolution_node(
        {
            "last_message": "I live in BRF Sjöstaden, what is my energy rating?",
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {},
        }
    )

    assert result["metadata"]["byggnadsid"] == "01-80-CIGARREN2-1"
    assert result["metadata"]["brf_resolution"]["status"] == "resolved_unique_building"
    assert result["metadata"]["clarification"]["needed"] is False


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
            {
                "byggnadsid": "01-80-SKYTTEN2-2",
                "50a_uuid": "uuid-1",
                "address": "Artemisgatan 17",
                "energy_class": "D",
            },
            {
                "byggnadsid": "01-80-SKYTTEN2-2",
                "50a_uuid": "uuid-1",
                "address": "Artemisgatan 17",
                "heating_system": "district heating",
            },
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


def test_generic_sql_agent_allows_multiple_addresses_for_explicit_building_id():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"byggnadsid": "01-80-CIGARREN2-1", "address": "Tegelviksgatan 71", "energy_class": "F"},
            {"byggnadsid": "01-80-CIGARREN2-1", "address": "Tengdahlsgatan 40", "energy_class": "F"},
        ],
        "trace": {"query_type": "buildings_by_single_filter", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"byggnadsid": "01-80-CIGARREN2-1"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["building_identity_check"]["multiple_addresses_same_explicit_building"] is True
    assert len(result["agent_data_generic"]) == 2


def test_generic_sql_agent_prefers_building_id_over_ambiguous_address_and_exposes_construction_year():
    module = import_building_flow_graph_module()
    calls = []
    selected_building_id = "24-82-BURTRAESKS-GAMMELBYN71:4-1"

    def fake_execute(op, **kwargs):
        calls.append((op, kwargs))
        if op == "buildings_by_building_id":
            return {
                "ok": True,
                "data": [
                    {
                        "byggnadsid": selected_building_id,
                        "epc_idadr": "Ringvägen 10",
                        "epc_idpostnr": "93732",
                        "epc_idpostort": "Burträsk",
                        "epc_idkommun": "Skellefteå",
                        "epc_egennybyggar": 1966,
                        "epc_godkand": "2020-12-23",
                        "epc_egiversion": "2020",
                    }
                ],
                "trace": {"query_type": "buildings_by_building_id", "execution_status": "success"},
            }
        raise AssertionError(f"unexpected operation: {op}")

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "building_id_from_user": selected_building_id,
            },
            "parallel": {},
        }
    )

    assert calls == [("buildings_by_building_id", {"building_id": selected_building_id.upper()})]
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == selected_building_id.upper()
    assert result["agent_data_generic"][0]["byggnadsid"] == selected_building_id
    assert result["agent_data_generic"][0]["construction_year"] == 1966
    assert result["metadata"]["generic_sql_trace"]["returned_values_used"]["construction_year"] == 1966


def test_generic_sql_agent_does_not_use_stale_system_building_id_over_location_hint():
    module = import_building_flow_graph_module()
    calls = []

    def fake_execute(op, **kwargs):
        calls.append((op, kwargs))
        if op == "building_by_address":
            return {
                "ok": True,
                "data": [
                    {
                        "byggnadsid": "01-60-BOKBINDAREN6-1",
                        "epc_idadr": "Ringvägen 10",
                        "epc_idpostort": "Täby",
                        "epc_idkommun": "Täby",
                        "epc_idpostnr": "18770",
                        "epc_godkand": "2019-10-31",
                        "epc_egienergiprestanda": 173,
                    },
                    {
                        "byggnadsid": "01-26-SPACKELN6-1",
                        "epc_idadr": "Ringvägen 10",
                        "epc_idpostort": "Huddinge",
                        "epc_idkommun": "Huddinge",
                        "epc_idpostnr": "14131",
                        "epc_godkand": "2018-04-27",
                        "epc_egienergiprestanda": 86,
                    },
                ],
                "trace": {"query_type": "building_by_address", "execution_status": "success"},
            }
        raise AssertionError(f"unexpected operation: {op}")

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Huddinge",
                "byggnadsid": "01-60-BOKBINDAREN6-1",
            },
            "parallel": {},
        }
    )

    assert calls == [("building_by_address", {"address": "Ringvägen 10"})]
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-26-SPACKELN6-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Huddinge"


def test_generic_sql_agent_uses_latest_epc_for_same_building_address_alias():
    module = import_building_flow_graph_module()

    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-80-EXAMPLE77-1",
                "epc_idadr": "Firstgatan 20",
                "epc_idpostort": "Stockholm",
                "epc_idkommun": "Stockholm",
                "epc_idpostnr": "11157",
                "epc_godkand": "2008-11-25",
                "epc_egienergiprestanda": 226,
            },
            {
                "byggnadsid": "01-80-EXAMPLE77-1",
                "epc_idadr": "Secondtorget 2",
                "epc_idpostort": "Stockholm",
                "epc_idkommun": "Stockholm",
                "epc_idpostnr": "11157",
                "epc_godkand": "2019-11-20",
                "epc_egienergiprestanda": 169,
            },
            {
                "byggnadsid": "01-60-OTHER37-1",
                "epc_idadr": "Firstgatan 20",
                "epc_idpostort": "Täby",
                "epc_idkommun": "Täby",
                "epc_idpostnr": "18762",
                "epc_godkand": "2024-01-01",
                "epc_egienergiprestanda": 300,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Firstgatan 20",
                "address_location_hint": "Stockholm",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-80-EXAMPLE77-1"
    assert result["agent_data_generic"][0]["epc_idadr"] == "Secondtorget 2"
    assert result["agent_data_generic"][0]["epc_egienergiprestanda"] == 169


def test_generic_sql_agent_allows_multiple_addresses_for_same_building_id_without_explicit_id():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"byggnadsid": "01-80-CIGARREN2-1", "address": "Tegelviksgatan 71", "energy_class": "F"},
            {"byggnadsid": "01-80-CIGARREN2-1", "address": "Tengdahlsgatan 40", "energy_class": "F"},
        ],
        "trace": {"query_type": "buildings_by_single_filter", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"epc_idadr": "Tegelviksgatan 71"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    identity = result["metadata"]["building_identity_check"]
    assert identity["status"] == "passed"
    assert identity["ambiguous"] is False
    assert identity["multiple_addresses_same_building_id"] is True
    assert result["metadata"]["byggnadsid"] == "01-80-CIGARREN2-1"


def test_extracts_multiple_addresses_from_identity_question():
    module = import_building_flow_graph_module()

    addresses = module._extract_address_candidates_from_text(
        "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?"
    )

    assert addresses == ["Artemisgatan 17", "Nimrodsgatan 7"]


def test_extracts_multiword_addresses_without_leading_conjunction():
    module = import_building_flow_graph_module()

    addresses = module._extract_address_candidates_from_text(
        "Our property has two addresses: Hammarby Allé 163 and Hammarby Allé 165. Which one do you use?"
    )

    assert addresses == ["Hammarby Allé 163", "Hammarby Allé 165"]


def test_extracts_semicolon_separated_address_list_with_postcodes():
    module = import_building_flow_graph_module()

    addresses = module._extract_address_candidates_from_text(
        "Aktergatan 11, 12066 Stockholm; Aktergatan 13, 12066 Stockholm; "
        "Aktergatan 5, 12066 Stockholm; Aktergatan 7, 12066 Stockholm; "
        "Aktergatan 9, 12066 Stockholm; Hammarby Allé 173, 12066 Stockholm"
    )

    assert addresses == [
        "Aktergatan 11",
        "Aktergatan 13",
        "Aktergatan 5",
        "Aktergatan 7",
        "Aktergatan 9",
        "Hammarby Allé 173",
    ]


def test_semicolon_separated_address_list_triggers_multi_address_resolution():
    module = import_building_flow_graph_module()

    text = (
        "Aktergatan 11, 12066 Stockholm; Aktergatan 13, 12066 Stockholm; "
        "Aktergatan 5, 12066 Stockholm; Aktergatan 7, 12066 Stockholm; "
        "Aktergatan 9, 12066 Stockholm; Hammarby Allé 173, 12066 Stockholm"
    )

    assert module._looks_like_multi_address_identity_question(
        text,
        module._extract_address_candidates_from_text(text),
    )


def test_multi_address_resolution_uses_shared_building_id_for_alias_addresses():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        assert op == "building_by_address"
        return {
            "ok": True,
            "data": [
                {
                    "byggnadsid": "01-80-SKYTTEN2-2",
                    "address": kwargs["address"],
                    "energy_class": "F",
                }
            ],
            "trace": {"query_type": "building_by_address", "execution_status": "success"},
        }

    module.sql_mapper_layer.execute = fake_execute

    result = module.multi_address_resolution_node(
        {
            "last_message": "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
            "metadata": {},
        }
    )

    assert result["multi_address_resolution_complete"] is True
    assert result["metadata"]["same_building_multiple_addresses"] is True
    assert result["metadata"]["byggnadsid"] == "01-80-SKYTTEN2-2"
    assert result["metadata"]["address"] == "Artemisgatan 17"
    assert result["metadata"]["alternate_addresses"] == ["Nimrodsgatan 7"]
    assert "Building ID: 01-80-SKYTTEN2-2" in result["final_response"]
    assert "resolve to the same building record" in result["final_response"]
    assert "building ID as the source of truth" in result["final_response"]
    assert "latest available EPC/building record" in result["final_response"]


def test_multi_address_resolution_uses_brf_address_context_when_direct_lookup_misses():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        return {
            "ok": False,
            "data": [],
            "message": "not found",
            "trace": {"query_type": op, "execution_status": "not_found"},
        }

    module.sql_mapper_layer.execute = fake_execute
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "orgnr": "7696062509",
            "brf_name": "Bostadsrättsföreningen Sjöstaden 1",
            "byggnadsid": "01-80-HALVOEN1-3",
            "fastighet": "Halvön 1",
            "address": "Hammarby Allé 163",
            "postnr": "12065",
            "postort": "Stockholm",
        },
        {
            "orgnr": "7696062509",
            "brf_name": "Bostadsrättsföreningen Sjöstaden 1",
            "byggnadsid": "01-80-HALVOEN1-3",
            "fastighet": "Halvön 1",
            "address": "Hammarby Allé 165",
            "postnr": "12065",
            "postort": "Stockholm",
        },
    ]

    result = module.resolve_multi_address_identity_message(
        "Our property has two addresses: Hammarby Allé 163 and Hammarby Allé 165. Which one do you use?",
        {"brf_name": "Sjöstaden 1"},
    )

    assert result["multi_address_resolution_complete"] is True
    assert result["metadata"]["same_building_multiple_addresses"] is True
    assert result["metadata"]["byggnadsid"] == "01-80-HALVOEN1-3"
    assert result["metadata"]["address"] == "Hammarby Allé 163"
    assert result["metadata"]["alternate_addresses"] == ["Hammarby Allé 165"]
    assert result["metadata"]["address_candidates"] == ["Hammarby Allé 163", "Hammarby Allé 165"]
    assert "Building ID: 01-80-HALVOEN1-3" in result["final_response"]


def test_multi_address_resolution_asks_when_addresses_map_to_different_buildings():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        assert op == "building_by_address"
        address = kwargs["address"]
        building_id = "01-80-SKYTTEN2-2" if address == "Artemisgatan 17" else "01-80-NIMROD1-1"
        return {
            "ok": True,
            "data": [{"byggnadsid": building_id, "address": address}],
            "trace": {"query_type": "building_by_address", "execution_status": "success"},
        }

    module.sql_mapper_layer.execute = fake_execute

    result = module.multi_address_resolution_node(
        {
            "last_message": "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
            "metadata": {},
        }
    )

    assert result["multi_address_resolution_complete"] is True
    assert result["metadata"]["clarification"]["needed"] is True
    assert result["metadata"]["clarification"]["reason"] == "multi_address_different_buildings"
    assert "I found different building IDs" in result["final_response"]
    assert "Which building ID should I use?" in result["final_response"]


def test_multi_address_resolution_returns_clarification_when_lookup_errors():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        raise TimeoutError("lookup timeout")

    module.sql_mapper_layer.execute = fake_execute

    result = module.resolve_multi_address_identity_message(
        "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
        {},
    )

    assert result["multi_address_resolution_complete"] is True
    assert result["metadata"]["clarification"]["needed"] is True
    assert result["metadata"]["clarification"]["reason"] == "multi_address_no_match"
    assert "I could not finish the address lookup" in result["final_response"]
    assert "lookup timed out" in result["final_response"]
    assert "Processing timed out" not in result["final_response"]


def test_multi_address_resolution_ignores_stale_candidates_on_single_address_followup():
    module = import_building_flow_graph_module()

    result = module.resolve_multi_address_identity_message(
        "use this address, Artemisgatan 17",
        {
            "address_candidates": ["Artemisgatan 17", "Nimrodsgatan 7"],
            "multi_address_resolution": {
                "status": "no_match",
                "candidates": [
                    {"address": "Artemisgatan 17", "ok": False},
                    {"address": "Nimrodsgatan 7", "ok": False},
                ],
            },
            "clarification": {
                "needed": True,
                "reason": "multi_address_no_match",
            },
        },
    )

    assert result is None


def test_multi_address_lookup_uses_short_timeout_and_restores_default(monkeypatch):
    module = import_building_flow_graph_module()

    class FakeSQL:
        timeout = 15

    seen_timeouts = []
    module.sql_mapper_layer.sql = FakeSQL()

    def fake_execute(op, **kwargs):
        seen_timeouts.append(module.sql_mapper_layer.sql.timeout)
        return {
            "ok": True,
            "data": [{"byggnadsid": "01-80-SKYTTEN2-2", "address": kwargs["address"]}],
        }

    monkeypatch.setenv("ODEN_MULTI_ADDRESS_TIMEOUT", "3")
    module.sql_mapper_layer.execute = fake_execute

    result = module._resolve_single_address_candidate("Artemisgatan 17")

    assert result["building_id"] == "01-80-SKYTTEN2-2"
    assert seen_timeouts == [3]
    assert module.sql_mapper_layer.sql.timeout == 15


def test_multi_address_lookup_default_timeout_allows_slow_oden_address_responses(monkeypatch):
    module = import_building_flow_graph_module()

    class FakeSQL:
        timeout = 15

    seen_timeouts = []
    module.sql_mapper_layer.sql = FakeSQL()

    def fake_execute(op, **kwargs):
        seen_timeouts.append(module.sql_mapper_layer.sql.timeout)
        return {
            "ok": True,
            "data": [{"byggnadsid": "01-80-HALVOEN1-3", "address": kwargs["address"]}],
        }

    monkeypatch.delenv("ODEN_MULTI_ADDRESS_TIMEOUT", raising=False)
    module.sql_mapper_layer.execute = fake_execute

    result = module._resolve_single_address_candidate("Lugnets Allé 25")

    assert result["building_id"] == "01-80-HALVOEN1-3"
    assert seen_timeouts == [10]
    assert module.sql_mapper_layer.sql.timeout == 15


def test_generic_sql_agent_uses_brf_selected_address_fallback_for_building_id():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        if op == "buildings_by_building_id":
            return {
                "ok": False,
                "data": [],
                "trace": {"query_type": "buildings_by_building_id", "execution_status": "not_found"},
            }
        if op == "building_by_address":
            assert kwargs["address"] == "Aktergatan 11"
            return {
                "ok": True,
                "data": [
                    {
                        "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                        "address": "Aktergatan 11",
                        "epc_venttypftx": "Ja",
                    }
                ],
                "trace": {"query_type": "building_by_address", "execution_status": "success"},
            }
        raise AssertionError(op)

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                "selected_brf_lookup_address": "Aktergatan 11",
                "selected_brf_addresses": ["Aktergatan 11, 12066 Stockholm"],
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["generic_sql_trace"]["match_strategy"] == "brf_selected_address_fallback"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-80-BRAEDGAARDEN9-2"


def test_generic_sql_agent_accepts_explicit_building_id_despite_stale_ambiguity():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        assert op == "buildings_by_building_id"
        assert kwargs["building_id"] == "01-80-BRAEDGAARDEN9-2"
        return {
            "ok": True,
            "data": [
                {
                    "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                    "epc_idadr": "Aktergatan 11",
                    "energy_class": "C",
                }
            ],
            "trace": {"query_type": "buildings_by_building_id", "execution_status": "success"},
        }

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                "building_id_from_user": "01-80-BRAEDGAARDEN9-2",
                "selected_brf_building_id": "01-80-BRAEDGAARDEN9-2",
                "context": {"ambiguous": True},
                "clarification": {
                    "needed": True,
                    "reason": "ambiguous_brf",
                },
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    identity = result["metadata"]["building_identity_check"]
    assert identity["status"] == "passed"
    assert identity["ambiguous"] is False
    assert identity["matched_building_id"] == "01-80-BRAEDGAARDEN9-2"
    assert identity["explicit_building_id_accepted"] is True
    assert result["metadata"]["clarification"]["needed"] is False
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-80-BRAEDGAARDEN9-2"


def test_generic_sql_agent_success_clears_stale_clarification_metadata():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "50a_uuid": "uuid-1",
                "address": "Artemisgatan 17",
                "energy_class": "D",
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Artemisgatan 17",
                "clarification": {
                    "needed": True,
                    "reason": "ambiguous_brf",
                    "question_asked": "Could you clarify?",
                    "resolved": False,
                },
            },
            "parallel": {},
        }
    )

    clarification = result["metadata"]["clarification"]
    assert clarification["needed"] is False
    assert clarification["resolved"] is True
    assert clarification["reason"] is None


def test_generic_sql_agent_selects_latest_epc_version_for_same_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-80-LISSABON2-2",
                "50a_uuid": "same-building",
                "address": "Öregrundsgatan 9",
                "epc_egiversion": "2010",
                "epc_egienergiklass2020_calc": "G",
            },
            {
                "byggnadsid": "01-80-LISSABON2-2",
                "50a_uuid": "same-building",
                "address": "Öregrundsgatan 9",
                "epc_egiversion": "2020",
                "epc_egienergiklass2020_calc": "F",
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Öregrundsgatan 9"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert len(result["agent_data_generic"]) == 1
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-80-LISSABON2-2"
    assert result["agent_data_generic"][0]["epc_egiversion"] == "2020"
    assert result["agent_data_generic"][0]["energy_class"] == "F"
    assert result["metadata"]["generic_sql_trace"]["returned_values_used"]["byggnadsid"] == "01-80-LISSABON2-2"


def test_generic_sql_agent_uses_building_id_and_latest_epc_for_same_building_aliases():
    module = import_building_flow_graph_module()
    calls = []

    def fake_execute(op, **kwargs):
        calls.append((op, kwargs))
        assert op == "buildings_by_building_id"
        assert kwargs["building_id"] == "01-80-SKYTTEN2-2"
        return {
            "ok": True,
            "data": [
                {
                    "byggnadsid": "01-80-SKYTTEN2-2",
                    "address": "Artemisgatan 17",
                    "epc_egiversion": "2009",
                    "epc_egienergiklass2020_calc": "G",
                },
                {
                    "byggnadsid": "01-80-SKYTTEN2-2",
                    "address": "Nimrodsgatan 7",
                    "epc_egiversion": "2019",
                    "epc_egienergiklass2020_calc": "F",
                },
            ],
            "trace": {"query_type": "buildings_by_building_id", "execution_status": "success"},
        }

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Artemisgatan 17",
                "address_from_user": "Artemisgatan 17",
                "alternate_addresses": ["Nimrodsgatan 7"],
                "byggnadsid": "01-80-SKYTTEN2-2",
                "same_building_multiple_addresses": True,
            },
            "parallel": {},
        }
    )

    assert calls == [("buildings_by_building_id", {"building_id": "01-80-SKYTTEN2-2"})]
    assert result.get("identity_gate_blocked") is not True
    assert len(result["agent_data_generic"]) == 1
    assert result["agent_data_generic"][0]["address"] == "Nimrodsgatan 7"
    assert result["agent_data_generic"][0]["epc_egiversion"] == "2019"
    assert result["agent_data_generic"][0]["energy_class"] == "F"


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


def test_generic_sql_agent_blocks_specific_address_when_rows_have_different_building_ids_without_addresses():
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

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"
    assert "Ringvägen 10, Huddinge" in result["metadata"]["clarification"]["question_asked"]


def test_generic_sql_agent_blocks_common_address_across_multiple_building_ids():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "03-31-LILLARAMSJOE2:13-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Morgongåva",
                "epc_idkommun": "Heby",
                "epc_idpostnr": "74450",
                "epc_godkand": "2021-08-19",
                "epc_egienergiprestanda": 143,
            },
            {
                "byggnadsid": "01-60-BOKBINDAREN6-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Täby",
                "epc_idkommun": "Täby",
                "epc_idpostnr": "18770",
                "epc_godkand": "2020-12-01",
                "epc_egennybyggar": 1975,
            },
            {
                "byggnadsid": "24-82-BURTRAESKS-GAMMELBYN71:4-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Burträsk",
                "epc_idkommun": "Skellefteå",
                "epc_idpostnr": "93732",
                "epc_godkand": "2020-12-23",
                "epc_egennybyggar": 1966,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Ringvägen 10"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"
    assert "Ringvägen 10, Huddinge" in result["metadata"]["clarification"]["question_asked"]
    assert result["metadata"]["pending_ambiguous_address"] == "Ringvägen 10"
    assert "143 kWh" not in result.get("final_response", "")


def test_generic_sql_agent_uses_location_hint_for_common_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-60-BOKBINDAREN6-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Täby",
                "epc_idkommun": "Täby",
                "epc_idpostnr": "18770",
                "epc_egiversion": "2020",
            },
            {
                "byggnadsid": "24-82-BURTRAESKS-GAMMELBYN71:4-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Burträsk",
                "epc_idkommun": "Skellefteå",
                "epc_idpostnr": "93732",
                "epc_egiversion": "2020",
            },
            {
                "byggnadsid": "01-15-GARNS-EKSKOGEN1:82-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Brottby",
                "epc_idkommun": "Vallentuna",
                "epc_idpostnr": "18697",
                "epc_godkand": "2024-11-15",
                "epc_egienergiprestanda": 221,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Täby",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-60-BOKBINDAREN6-1"
    assert result["metadata"]["generic_sql_trace"]["match_strategy"] == "exact_address_location"


def test_generic_sql_agent_uses_huddinge_hint_before_krokom_row():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "23-09-HISSMOBOELE2:140-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Krokom",
                "epc_idkommun": "Krokom",
                "epc_idpostnr": "83532",
                "epc_godkand": "2020-08-10",
                "epc_egienergiprestanda": 101,
            },
            {
                "byggnadsid": "01-26-SPACKELN6-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Huddinge",
                "epc_idkommun": "Huddinge",
                "epc_idpostnr": "14131",
                "epc_godkand": "2018-04-27",
                "epc_egienergiprestanda": 86,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Huddinge",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-26-SPACKELN6-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Huddinge"


def test_generic_sql_agent_uses_location_hint_before_latest_epc_for_common_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-83-LOEVSAANGAREN5-1",
                "epc_idadr": "Sveavägen 17",
                "epc_idpostort": "Sundbyberg",
                "epc_idkommun": "Sundbyberg",
                "epc_idpostnr": "17270",
                "epc_godkand": "2024-01-01",
                "epc_egienergiprestanda": 93,
            },
            {
                "byggnadsid": "18-80-SKRIKAN4-1",
                "epc_idadr": "Sveavägen 17",
                "epc_idpostort": "Örebro",
                "epc_idkommun": "Örebro",
                "epc_idpostnr": "70214",
                "epc_godkand": "2009-01-07",
                "epc_egienergiprestanda": 172,
            },
            {
                "byggnadsid": "18-80-SKRIKAN4-1",
                "epc_idadr": "Sveavägen 17",
                "epc_idpostort": "Örebro",
                "epc_idkommun": "Örebro",
                "epc_idpostnr": "70214",
                "epc_godkand": "2021-07-08",
                "epc_egienergiprestanda": 106,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Sveavägen 17",
                "address_location_hint": "Örebro",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "18-80-SKRIKAN4-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Örebro"
    assert result["agent_data_generic"][0]["epc_godkand"] == "2021-07-08"


def test_generic_sql_agent_uses_location_hint_for_kungsgatan_vaxjo():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "12-66-MALTESHOLM20-1",
                "epc_idadr": "Kungsgatan 10",
                "epc_idpostort": "Hörby",
                "epc_idkommun": "Hörby",
                "epc_idpostnr": "24231",
                "epc_godkand": "2017-06-05",
                "epc_egienergiprestanda": 157,
            },
            {
                "byggnadsid": "07-80-GUNNARGROEPE10-1",
                "epc_idadr": "Kungsgatan 10",
                "epc_idpostort": "Växjö",
                "epc_idkommun": "Växjö",
                "epc_idpostnr": "35233",
                "epc_godkand": "2019-12-19",
                "epc_egienergiprestanda": 104,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Kungsgatan 10",
                "address_location_hint": "Växjö",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "07-80-GUNNARGROEPE10-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Växjö"


def test_generic_sql_agent_blocks_when_city_still_has_multiple_building_ids():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "07-80-GUNNARGROEPE10-1",
                "epc_idadr": "Kungsgatan 10",
                "epc_idpostort": "Växjö",
                "epc_idkommun": "Växjö",
                "epc_idfastbet": "Gunnar Gröpe 10",
                "epc_godkand": "2019-12-19",
                "epc_egienergiprestanda": 104,
            },
            {
                "byggnadsid": "07-80-GUNNARGROEPE9-1",
                "epc_idadr": "Kungsgatan 10",
                "epc_idpostort": "Växjö",
                "epc_idkommun": "Växjö",
                "epc_idfastbet": "Gunnar Gröpe 9",
                "epc_godkand": "2009-11-12",
                "epc_egienergiprestanda": 147,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Kungsgatan 10",
                "address_location_hint": "Växjö",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["building_identity_check"]["candidate_building_ids"] == [
        "07-80-GUNNARGROEPE10-1",
        "07-80-GUNNARGROEPE9-1",
    ]
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"
    assert result.get("agent_data_generic") in (None, [])


def test_generic_sql_agent_uses_municipality_hint_for_common_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "04-83-HILLERSTA1:73-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Julita",
                "epc_idkommun": "Katrineholm",
                "epc_idpostnr": "64360",
                "epc_godkand": "2014-10-15",
                "epc_egienergiprestanda": 104,
            },
            {
                "byggnadsid": "01-88-HALLSTA4:30-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Hallstavik",
                "epc_idkommun": "Norrtälje",
                "epc_idpostnr": "76340",
                "epc_godkand": "2017-01-02",
                "epc_egienergiprestanda": 25,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Katrineholm",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "04-83-HILLERSTA1:73-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Katrineholm"


def test_generic_sql_agent_prefers_post_town_over_municipality_hint_for_common_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "06-80-MAANSARP1:167-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Taberg",
                "epc_idkommun": "Jönköping",
                "epc_idpostnr": "56241",
                "epc_godkand": "2013-02-26",
                "epc_egienergiprestanda": 153,
            },
            {
                "byggnadsid": "06-80-AASKAADAREN12-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Jönköping",
                "epc_idkommun": "Jönköping",
                "epc_idpostnr": "55454",
                "epc_godkand": "2018-02-14",
                "epc_egienergiprestanda": 68,
            },
            {
                "byggnadsid": "01-15-GARNS-EKSKOGEN1:82-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Brottby",
                "epc_idkommun": "Vallentuna",
                "epc_idpostnr": "18697",
                "epc_godkand": "2024-11-15",
                "epc_egienergiprestanda": 221,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Jönköping",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "06-80-AASKAADAREN12-1"
    assert result["agent_data_generic"][0]["epc_idpostort"] == "Jönköping"
    assert result["agent_data_generic"][0]["epc_egienergiprestanda"] == 68


def test_generic_sql_agent_blocks_when_location_hint_matches_no_returned_location():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-26-SPACKELN6-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Huddinge",
                "epc_idkommun": "Huddinge",
                "epc_idpostnr": "14131",
                "epc_godkand": "2018-05-08",
                "epc_egienergiprestanda": 86,
            },
            {
                "byggnadsid": "01-88-HALLSTA4:30-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Hallstavik",
                "epc_idkommun": "Norrtälje",
                "epc_idpostnr": "76340",
                "epc_godkand": "2017-01-02",
                "epc_egienergiprestanda": 25,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Sollentuna",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["building_identity_check"]["location_hint_no_match"] is True
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"
    assert result["metadata"]["generic_sql_trace"]["match_strategy"] == "address_location_no_match"
    assert result["metadata"]["generic_sql_trace"]["returned_values_used"] == {}
    assert result.get("agent_data_generic") in (None, [])


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


def test_generic_sql_agent_keeps_district_heating_breakdown_separate():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "07-80-GUNNARGROEPE10-1",
                "epc_idadr": "Examplegatan 10",
                "epc_idpostort": "Växjö",
                "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                "epc_egifjarrvarmeuppv": 1169500,
                "epc_egifjarrvarmevv": 33300,
            }
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Examplegatan 10", "address_location_hint": "Växjö"},
            "parallel": {},
        }
    )

    row = result["agent_data_generic"][0]
    assert row["heating_system"] == "district heating"
    assert "district_heating_use" not in row
    assert row["district_heating_space_heating"] == 1169500
    assert row["district_heating_domestic_hot_water"] == 33300


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


def test_llm_summarizer_uses_structured_fallback_on_rate_limit():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Error: Rate limit exceeded. Please retry shortly."
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is my energy performance?",
            "messages": [{"role": "user", "content": "what is my energy performance?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Ringvägen 10"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-26-SPACKELN6-1",
                        "epc_idadr": "Ringvägen 10",
                        "epc_egienergiklass2020_calc": "E",
                        "epc_egienergiprestanda": 86,
                        "epc_egispecifikenergianvandning_calc": 86,
                        "epc_egiprimarenergital2020_calc": 155,
                        "epc_godkand": "2018-05-08",
                    }
                ]
            },
        }
    )

    assert "Error: Rate limit exceeded" not in result["final_response"]
    assert "Building ID: 01-26-SPACKELN6-1" in result["final_response"]
    assert "Your building's declared energy performance is 86 kWh/m2-year." in result["final_response"]
    assert "Energy performance is the building's annual energy use per square meter of heated area." in result["final_response"]
    assert "Primary energy number: 155 kWh/m2-year" not in result["final_response"]


def test_llm_summarizer_fallback_labels_district_heating_breakdown():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Error: Rate limit exceeded. Please retry shortly."
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is the district heating breakdown?",
            "messages": [{"role": "user", "content": "what is the district heating breakdown?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Examplegatan 10"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "07-80-GUNNARGROEPE10-1",
                        "epc_idadr": "Examplegatan 10",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                        "epc_egifjarrvarmeuppv": 1169500,
                        "epc_egifjarrvarmevv": 33300,
                    }
                ]
            },
        }
    )

    assert "Total district heating use" not in result["final_response"]
    assert "District heating use: 1169500" not in result["final_response"]
    assert "Your building's district heating for space heating is 1169500 kWh/year." in result["final_response"]
    assert "Your building's district heating for domestic hot water is 33300 kWh/year." in result["final_response"]


def test_llm_summarizer_replaces_overbroad_profile_for_direct_electricity_question():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Building ID: 01-84-FURIREN2-1\n\n"
        "Energy class: E\n"
        "Declared energy performance: 136 kWh/m2-year\n"
        "Specific energy use: 129 kWh/m2-year\n"
        "Primary energy number: 103 kWh/m2-year\n"
        "Energy declaration year: 2019\n"
        "Construction year: 1963\n"
        "Heating system: district heating\n"
        "Ventilation: FTX\n"
        "Electricity consumption: 121667 kWh/year"
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is total electricity consumption of my building?",
            "messages": [{"role": "user", "content": "what is total electricity consumption of my building?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Armégatan 32A"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-84-FURIREN2-1",
                        "address": "Armégatan 32A",
                        "epc_el_calc": 121667,
                        "epc_egienergiklass2020_calc": "E",
                        "epc_egienergiprestanda": 136,
                        "epc_egispecifikenergianvandning_calc": 129,
                        "epc_egiprimarenergital2020_calc": 103,
                    }
                ]
            },
        }
    )

    assert "Your building's total electricity consumption is 121667 kWh/year." in result["final_response"]
    assert "This is annual electricity use in the building record." in result["final_response"]
    assert "Energy class: E" not in result["final_response"]
    assert "Ventilation: FTX" not in result["final_response"]
    assert "renovation_information" not in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == "direct_fact_response_was_too_broad"


def test_targeted_fact_response_explains_energy_class_for_beginners():
    module = import_building_flow_graph_module()

    response = module._targeted_building_fact_response(
        user_input="what is my building's energy class?",
        current_address="Armégatan 32A",
        building_id="01-84-FURIREN2-1",
        facts={"energy_class": "E"},
    )

    assert "Your building's energy class is E." in response
    assert "Energy classes usually run from A to G" in response
    assert "class E means there is likely room to improve" in response


def test_llm_summarizer_translates_oden_ventilation_fields_for_beginners():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Energy class is a rating of a building's energy performance."
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is my ventilation system?",
            "messages": [{"role": "user", "content": "what is my ventilation system?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Professorsslingan 51"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-FILOSOFEN2-3",
                        "epc_idadr": "Professorsslingan 51",
                        "epc_venttypf": "Nej",
                        "epc_venttypft": "Nej",
                        "epc_venttypftx": "Ja",
                        "epc_venttypsjalvdrag": "Nej",
                    }
                ]
            },
        }
    )

    assert "Your building's ventilation type is FTX." in result["final_response"]
    assert "FTX ventilation means mechanical supply and exhaust ventilation with heat recovery" in result["final_response"]
    assert "Ventilation matters because it affects air quality" in result["final_response"]
    assert "epc_venttypftx" not in result["final_response"]
    assert "Energy class" not in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == "direct_fact_response_targeted"


def test_targeted_fact_response_does_not_hijack_ventilation_type_overview_question():
    module = import_building_flow_graph_module()

    response = module._targeted_building_fact_response(
        user_input="what are the other types of ventilation systems?",
        current_address="Sveavägen 17",
        building_id="18-80-SKRIKAN4-1",
        facts={"ventilation_type": "Självdrag"},
    )

    assert response is None


def test_targeted_fact_response_does_not_hijack_heating_kinds_overview_question():
    module = import_building_flow_graph_module()

    response = module._targeted_building_fact_response(
        user_input="what are the other kinds of heating systems available for heating builsinga?",
        current_address="Sveavägen 17",
        building_id="18-80-SKRIKAN4-1",
        facts={"heating_system": "district heating"},
    )

    assert response is None


def test_llm_summarizer_translates_oden_heating_fields_for_beginners():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Energy class is a rating of a building's energy performance."
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is my heating system?",
            "messages": [{"role": "user", "content": "what is my heating system?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Professorsslingan 51"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-FILOSOFEN2-3",
                        "epc_idadr": "Professorsslingan 51",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                    }
                ]
            },
        }
    )

    assert "Your building's heating system is district heating." in result["final_response"]
    assert "District heating means heat is produced centrally" in result["final_response"]
    assert "epc_huvudsakliguppvarmning_calc" not in result["final_response"]
    assert "Energy class" not in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == "direct_fact_response_targeted"


def test_llm_summarizer_replaces_profile_dump_for_heating_systems_overview():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Sveavägen 17\n\n"
        "Building ID: 18-80-SKRIKAN4-1\n\n"
        "Energy class: E\n"
        "Declared energy performance: 106 kWh/m2-year\n"
        "Specific energy use: 145 kWh/m2-year\n"
        "Primary energy number: 106 kWh/m2-year\n"
        "Energy declaration year: 2021\n"
        "Construction year: 1938\n"
        "Heating system: district heating\n"
        "Ventilation: Självdrag\n"
        "Electricity consumption: 1951 kWh/year\n"
        "District heating for space heating: 45029 kWh/year\n"
        "District heating for domestic hot water: 11800 kWh/year"
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what are the other kinds of heating systems available for heating builsinga?",
            "messages": [{"role": "user", "content": "what are the other kinds of heating systems available for heating builsinga?"}],
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
            },
            "metadata": {"address": "Sveavägen 17"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "18-80-SKRIKAN4-1",
                        "address": "Sveavägen 17",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                        "epc_egienergiklass2020_calc": "E",
                        "epc_egienergiprestanda": 106,
                        "epc_egispecifikenergianvandning_calc": 145,
                        "epc_egiprimarenergital2020_calc": 106,
                    }
                ]
            },
        }
    )

    assert "Your building's heating system is district heating." in result["final_response"]
    assert "Other common heating systems for buildings include:" in result["final_response"]
    assert "Heat pumps:" in result["final_response"]
    assert "Energy class: E" not in result["final_response"]
    assert "Ventilation: Självdrag" not in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == "concept_overview_response_was_too_broad"


def test_llm_summarizer_gives_focused_ftx_explanation_for_ventilation_measures():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "what are efficiency measures for ventilation system?",
            "messages": [{"role": "user", "content": "what are efficiency measures for ventilation system?"}],
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
            },
            "metadata": {"address": "Armégatan 32A"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-84-FURIREN2-1",
                        "address": "Armégatan 32A",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                        "epc_venttypftx": "Ja",
                        "epc_egienergiklass2020_calc": "E",
                        "epc_egienergiprestanda": 136,
                    }
                ]
            },
        }
    )

    assert "FTX ventilation means mechanical supply and exhaust ventilation with heat recovery" in result["final_response"]
    assert "Commission the FTX system" in result["final_response"]
    assert "Energy class:" not in result["final_response"]
    assert "Electricity consumption:" not in result["final_response"]
    assert "renovation_information" not in result["final_response"]


def test_llm_summarizer_treats_epc_ventilation_electricity_and_date_as_confirmed():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "what are the ECMs?",
            "messages": [{"role": "user", "content": "what are the ECMs?"}],
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
            },
            "metadata": {"address": "Öregrundsgatan 9"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-LISSABON2-2",
                        "address": "Öregrundsgatan 9",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                        "epc_egienergiklass2020_calc": "F",
                        "epc_egispecifikenergianvandning_calc": 199,
                        "epc_egifjarrvarme": 665500,
                        "epc_venttypft": "Ja",
                        "epc_venttypftx": "Nej",
                        "epc_el_calc": 132000,
                        "epc_godkand": "2009-11-23",
                    }
                ]
            },
        }
    )

    facts = result["metadata"]["retrieved_facts"]
    uncertainty = result["metadata"]["uncertainty"]
    assert facts["ventilation_type"] == "FT"
    assert facts["electricity_use"] == 132000
    assert facts["energy_declaration_year"] == 2009
    assert result["metadata"]["data_freshness"]["energy_declaration_year"] == 2009
    assert "ventilation_type" in uncertainty["confirmed_facts"]
    assert "electricity_use" in uncertainty["confirmed_facts"]
    assert "energy_declaration_year" in uncertainty["confirmed_facts"]
    assert "ventilation_type" not in uncertainty["missing_facts"]
    assert "electricity_use" not in uncertainty["missing_facts"]
    assert "energy_declaration_year" not in uncertainty["missing_facts"]
    assert uncertainty["missing_facts"] == ["renovation_information"]
    assert result["metadata"]["response_fallback"]["reason"] == "ecm_response_missing_recommendations"
    assert result["metadata"]["building_match"]["building_id"] == "01-80-LISSABON2-2"
    assert result["metadata"]["retrieved_facts"]["address"] == "Öregrundsgatan 9"
    assert "Energy Conservation Measures (ECMs)" in result["final_response"]
    assert "1. Energy conservation / reduce demand and waste" in result["final_response"]
    assert "2. Energy efficiency / improve equipment and building systems" in result["final_response"]
    assert "3. Energy management measures / controls, monitoring, and routines" in result["final_response"]
    assert "4. Renewable energy / add supply after demand is reduced" in result["final_response"]
