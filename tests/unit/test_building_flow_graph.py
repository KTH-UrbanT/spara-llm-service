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
            "metadata": "boverket.pdf",
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
                "metadata": "boverket.pdf",
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
