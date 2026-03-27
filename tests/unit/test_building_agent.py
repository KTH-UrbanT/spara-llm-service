from tests.support import fresh_import, stub_module


class FakeGraph:
    result = None
    error = None
    received_state = None

    def invoke(self, state):
        type(self).received_state = state
        if type(self).error is not None:
            raise type(self).error
        return type(self).result


class SessionStoreRecorder:
    calls = []

    @classmethod
    def update(cls, thread_id, state):
        cls.calls.append((thread_id, state))


class SessionStoreLoader:
    state = {}


def import_building_agent_module():
    SessionStoreRecorder.calls = []
    FakeGraph.received_state = None
    stub_module("src.agents.building_flow_graph", build_building_flow_graph=lambda: FakeGraph())
    stub_module(
        "src.redis.redis_session_store",
        get_session_state=lambda thread_id: SessionStoreLoader.state,
        update_session_state=SessionStoreRecorder.update,
    )
    return fresh_import("src.agents.building_agent")


def test_handle_building_query_returns_composed_response_and_updates_session():
    module = import_building_agent_module()
    FakeGraph.result = {
        "final_response": "Use insulation and heat recovery.",
        "context": {
            "parsed_intent": "SQL database ; vector database",
            "intent_list": ["SQL database", "vector database"],
        },
        "metadata": {"debug": {"agent_answered": "hybrid"}},
        "done_generic_sql": True,
        "done_specialized_sql": False,
        "done_vector": True,
    }
    FakeGraph.error = None

    agent = module.BuildingAgent()
    response, metadata = agent.handle_building_query(
        last_message="How do I improve this building?",
        messages=[{"role": "user", "content": "How do I improve this building?"}],
        metadata={"address": ["Main Street 1"]},
        thread_id="thread-1",
        _session_state={"step": "existing"},
    )

    assert FakeGraph.received_state == {
        "thread_id": "thread-1",
        "messages": [{"role": "user", "content": "How do I improve this building?"}],
        "metadata": {"address": ["Main Street 1"]},
        "last_message": "How do I improve this building?",
        "session_state": {"step": "existing"},
    }
    assert SessionStoreRecorder.calls == [("thread-1", FakeGraph.result)]
    assert response == {
        "content": "Use insulation and heat recovery.",
        "classification": "building_specific",
        "parsed_intent": "SQL database ; vector database",
        "intent_list": ["SQL database", "vector database"],
        "agent_answered": ["ODEN API", "Documents stored in Vector database used"],
    }
    assert metadata == {"debug": {"agent_answered": "hybrid"}}


def test_handle_building_query_falls_back_across_response_fields():
    module = import_building_agent_module()
    FakeGraph.result = {
        "response": "Fallback response",
        "context": {},
        "metadata": {},
        "done_generic_sql": False,
        "done_specialized_sql": True,
        "done_vector": False,
    }
    FakeGraph.error = None

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="question",
        messages=[],
        metadata={},
        thread_id="thread-2",
    )

    assert response["content"] == "Fallback response"
    assert response["agent_answered"] == ["Hammarby dataset used"]
    assert metadata == {}


def test_handle_building_query_loads_session_state_when_not_provided():
    module = import_building_agent_module()
    SessionStoreLoader.state = {"metadata": {"byggnadsid": "01-80-FILOSOFEN2-3"}}
    FakeGraph.result = {
        "final_response": "Building ID: 01-80-FILOSOFEN2-3\n\nResponse",
        "context": {},
        "metadata": {"byggnadsid": "01-80-FILOSOFEN2-3"},
        "done_generic_sql": False,
        "done_specialized_sql": False,
        "done_vector": False,
    }
    FakeGraph.error = None

    module.BuildingAgent().handle_building_query(
        last_message="question",
        messages=[],
        metadata={},
        thread_id="thread-from-store",
    )

    assert FakeGraph.received_state["session_state"] == {"metadata": {"byggnadsid": "01-80-FILOSOFEN2-3"}}


def test_handle_building_query_returns_error_payload_when_graph_fails():
    module = import_building_agent_module()
    FakeGraph.result = None
    FakeGraph.error = RuntimeError("graph exploded")

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="question",
        messages=[],
        metadata={"x": 1},
        thread_id="thread-3",
    )

    assert response["content"] == "An error occurred while handling your building-related request: graph exploded"
    assert response["classification"] == "building_specific"
    assert response["parsed_intent"] is None
    assert response["agent_answered"] == []
    assert response["vector_sources"] == []
    assert metadata == {"x": 1}

