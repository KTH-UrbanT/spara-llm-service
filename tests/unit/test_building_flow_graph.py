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
