from tests.support import fresh_import, stub_module


class StubRouterAgent:
    next_classification = "generic"
    last_call = None

    def classify_question(self, message, previous_classification):
        type(self).last_call = (message, previous_classification)
        return type(self).next_classification


class StubBuildingAgent:
    last_call = None

    def handle_building_query(self, last_message, messages, metadata, thread_id):
        type(self).last_call = (last_message, messages, metadata, thread_id)
        return {
            "content": "building answer",
            "agent_answered": "building",
            "parsed_intent": "retrofit",
        }, {"address": ["street 1"]}


class StubClusterAgent:
    last_call = None

    def handle_cluster_query(self, last_message, messages, metadata, thread_id):
        type(self).last_call = (last_message, messages, metadata, thread_id)
        return {
            "content": "cluster answer",
            "agent_answered": "cluster",
        }, {"simulation_results": ["done"]}


class StubGenericAgent:
    last_call = None

    def handle_generic_input(self, last_message, messages):
        type(self).last_call = (last_message, messages)
        return "generic answer"


class StubConversationalAgent:
    last_call = None

    def handle_conversational_input(self, last_message, messages):
        type(self).last_call = (last_message, messages)
        return "conversation answer"


class StubDraftReportService:
    last_call = None

    @staticmethod
    def generate_draft_report_response(thread_id, messages, metadata):
        StubDraftReportService.last_call = (thread_id, messages, metadata)
        return {
            "role": "assistant",
            "content": "report answer",
            "classification": "draft_energy_report",
            "agent_answered": "draft_energy_report",
            "downloadable_report": {"report_id": "rep-1"},
        }


def import_agent_router_module():
    stub_module("src.agents.router_agent", RouterAgent=StubRouterAgent)
    stub_module("src.agents.building_agent", BuildingAgent=StubBuildingAgent)
    stub_module("src.agents.cluster_agent", ClusterAgent=StubClusterAgent)
    stub_module("src.agents.generic_agent", GenericAgent=StubGenericAgent)
    stub_module("src.agents.aggregator_agent", AggregatorAgent=object)
    stub_module("src.agents.conversationalist_agent", ConversationalAgent=StubConversationalAgent)
    stub_module(
        "src.services.draft_report_service",
        generate_draft_report_response=StubDraftReportService.generate_draft_report_response,
    )
    return fresh_import("src.pipeline.agent_router")


def test_routes_generic_requests():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "hello"}],
        last_message="hello",
        metadata={"kept": True},
        thread_id="thread-1",
    )

    assert response["content"] == "generic answer"
    assert response["classification"] == "generic"
    assert response["agent_answered"] == "generic"
    assert response["role"] == "assistant"
    assert metadata == {"kept": True}
    assert StubGenericAgent.last_call == ("hello", [{"role": "user", "content": "hello"}])


def test_asks_for_confirmation_when_switching_from_building_to_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {"role": "assistant", "classification": "building_specific"},
            {"role": "user", "content": "and more broadly?"},
        ],
        last_message="and more broadly?",
        metadata={"existing": 1},
        thread_id="thread-2",
    )

    assert response["content"] == "Would you like building-specific advice or generic advice?"
    assert response["classification"] == "generic"
    assert response["agent_answered"] == "uncertain"
    assert metadata == {"existing": 1}


def test_routes_building_specific_requests():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "building question"}],
        last_message="building question",
        metadata={"address": []},
        thread_id="thread-3",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["agent_answered"] == "building"
    assert response["role"] == "assistant"
    assert metadata == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call[3] == "thread-3"


def test_routes_cluster_requests():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "cluster"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "cluster question"}],
        last_message="cluster question",
        metadata={},
        thread_id="thread-4",
    )

    assert response["content"] == "cluster answer"
    assert response["classification"] == "cluster"
    assert response["agent_answered"] == "cluster"
    assert response["role"] == "assistant"
    assert metadata == {"simulation_results": ["done"]}


def test_routes_conversational_requests():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "conversational"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "small talk"}],
        last_message="small talk",
        metadata={"session": "x"},
        thread_id="thread-5",
    )

    assert response["content"] == "conversation answer"
    assert response["classification"] == "conversational"
    assert response["agent_answered"] == "conversationalist"
    assert response["role"] == "assistant"
    assert metadata == {"session": "x"}


def test_routes_draft_report_requests():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "draft_energy_report"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "prepare a draft report"}],
        last_message="prepare a draft report",
        metadata={"address": "Main Street 1"},
        thread_id="thread-7",
    )

    assert response["content"] == "report answer"
    assert response["classification"] == "draft_energy_report"
    assert response["agent_answered"] == "draft_energy_report"
    assert response["downloadable_report"] == {"report_id": "rep-1"}
    assert metadata == {"address": "Main Street 1"}
    assert StubDraftReportService.last_call == (
        "thread-7",
        [{"role": "user", "content": "prepare a draft report"}],
        {"address": "Main Street 1"},
    )


def test_unknown_classification_falls_back_cleanly():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "mystery"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "??"}],
        last_message="??",
        metadata={},
        thread_id="thread-6",
    )

    assert response == {
        "role": "assistant",
        "content": "Unknown classification: mystery",
        "classification": "mystery",
        "agent_answered": "unknown",
    }
    assert metadata == {}
