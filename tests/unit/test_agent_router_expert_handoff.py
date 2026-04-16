from tests.support import fresh_import, stub_module


class FakeRouterAgent:
    def classify_question(self, message, previous_classification=None):
        lowered = message.lower()
        if "report" in lowered:
            return "draft_energy_report"
        if "expert" in lowered or "ekr" in lowered or "human help" in lowered:
            return "expert_handoff"
        return "generic"

    def wants_draft_report(self, message):
        return "report" in message.lower()

    def wants_expert_handoff(self, message):
        lowered = message.lower()
        return "expert" in lowered or "ekr" in lowered or "human help" in lowered

    def is_confirmation(self, message):
        normalized = message.strip().lower()
        return any(
            normalized == phrase or normalized.startswith(f"{phrase} ")
            for phrase in {"yes", "yes please", "send it", "send the email", "please send the email"}
        )

    def is_rejection(self, message):
        normalized = message.strip().lower()
        return any(phrase in normalized for phrase in {"no", "cancel", "don't send"})


class FakeBuildingAgent:
    def handle_building_query(self, last_message, messages, metadata, thread_id):
        return {"content": "building reply", "agent_answered": "building"}, metadata


class FakeClusterAgent:
    def handle_cluster_query(self, last_message, messages, metadata, thread_id):
        return {"content": "cluster reply", "agent_answered": "cluster"}, metadata


class FakeGenericAgent:
    def handle_generic_input(self, last_message, messages):
        return "generic reply"


class FakeConversationalAgent:
    def handle_conversational_input(self, last_message, messages):
        return "conversational reply"


class EmailRecorder:
    calls = []
    should_raise = False

    @classmethod
    def send(cls, thread_id, messages, metadata):
        if cls.should_raise:
            raise RuntimeError("smtp failed")
        cls.calls.append((thread_id, messages, metadata))
        return True


def import_agent_router_module():
    EmailRecorder.calls = []
    EmailRecorder.should_raise = False
    stub_module("src.agents.router_agent", RouterAgent=FakeRouterAgent)
    stub_module("src.agents.building_agent", BuildingAgent=FakeBuildingAgent)
    stub_module("src.agents.cluster_agent", ClusterAgent=FakeClusterAgent)
    stub_module("src.agents.generic_agent", GenericAgent=FakeGenericAgent)
    stub_module("src.agents.aggregator_agent", AggregatorAgent=object)
    stub_module("src.agents.conversationalist_agent", ConversationalAgent=FakeConversationalAgent)
    stub_module(
        "src.services.draft_report_service",
        generate_draft_report_response=lambda thread_id, messages, metadata: {
            "role": "assistant",
            "content": "draft report reply",
            "classification": "draft_energy_report",
            "agent_answered": "draft_energy_report",
            "downloadable_report": {"report_id": f"rep-{thread_id}"},
        },
    )
    stub_module("src.services.expert_handoff_email", send_expert_handoff_email=EmailRecorder.send)
    return fresh_import("src.pipeline.agent_router")


def test_route_message_asks_for_expert_handoff_confirmation():
    module = import_agent_router_module()
    router = module.AgentRouter()

    response, metadata = router.route_message(
        [{"role": "user", "content": "I need an expert"}],
        "I need an expert",
        {},
        "thread-1",
    )

    assert response["classification"] == "expert_handoff"
    assert "Do you want me to send it?" in response["content"]
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert EmailRecorder.calls == []


def test_route_message_sends_email_after_confirmation():
    module = import_agent_router_module()
    router = module.AgentRouter()
    messages = [
        {"role": "user", "content": "This needs an expert"},
        {"role": "assistant", "content": "I can email an expert. Do you want me to send it?"},
        {"role": "user", "content": "yes"},
    ]

    response, metadata = router.route_message(
        messages,
        "yes",
        {"expert_handoff_pending_confirmation": True, "address": ["Main Street 1"]},
        "thread-2",
    )

    assert response["classification"] == "expert_handoff"
    assert "email was sent successfully" in response["content"]
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert metadata["expert_handoff_requested"] is True
    assert len(EmailRecorder.calls) == 1
    assert EmailRecorder.calls[0][0] == "thread-2"


def test_route_message_cancels_expert_handoff_on_rejection():
    module = import_agent_router_module()
    router = module.AgentRouter()

    response, metadata = router.route_message(
        [{"role": "user", "content": "no"}],
        "no",
        {"expert_handoff_pending_confirmation": True},
        "thread-3",
    )

    assert response["classification"] == "expert_handoff"
    assert "will not send" in response["content"]
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert EmailRecorder.calls == []


def test_route_message_reports_email_failure_to_user():
    module = import_agent_router_module()
    router = module.AgentRouter()
    EmailRecorder.should_raise = True

    response, metadata = router.route_message(
        [{"role": "user", "content": "yes"}],
        "yes",
        {"expert_handoff_pending_confirmation": True},
        "thread-4",
    )

    assert response["classification"] == "expert_handoff"
    assert "could not send the email" in response["content"]
    assert metadata["expert_handoff_sent"] is False
    assert "smtp failed" in metadata["expert_handoff_error"]


def test_route_message_accepts_longer_confirmation_phrase():
    module = import_agent_router_module()
    router = module.AgentRouter()

    response, metadata = router.route_message(
        [{"role": "user", "content": "yes please send the email"}],
        "yes please send the email",
        {"expert_handoff_pending_confirmation": True},
        "thread-5",
    )

    assert response["classification"] == "expert_handoff"
    assert "email was sent successfully" in response["content"]
    assert metadata["expert_handoff_sent"] is True


def test_route_message_does_not_mutate_input_metadata():
    module = import_agent_router_module()
    router = module.AgentRouter()
    original_metadata = {"address": ["Main Street 1"]}

    response, returned_metadata = router.route_message(
        [{"role": "user", "content": "I need an expert"}],
        "I need an expert",
        original_metadata,
        "thread-6",
    )

    assert response["classification"] == "expert_handoff"
    assert original_metadata == {"address": ["Main Street 1"]}
    assert returned_metadata is not original_metadata
    assert returned_metadata["expert_handoff_pending_confirmation"] is True


def test_pending_handoff_does_not_treat_report_request_as_confirmation():
    module = import_agent_router_module()
    router = module.AgentRouter()

    response, metadata = router.route_message(
        [{"role": "user", "content": "can you give me the draft energy report?"}],
        "can you give me the draft energy report?",
        {"expert_handoff_pending_confirmation": True, "address": ["Main Street 1"]},
        "thread-7",
    )

    assert response["classification"] == "draft_energy_report"
    assert response["content"] == "draft report reply"
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert EmailRecorder.calls == []
