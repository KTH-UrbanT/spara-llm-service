from tests.support import fresh_import, stub_module
import pytest


@pytest.fixture(autouse=True)
def no_evaluation_environment(monkeypatch):
    monkeypatch.delenv("EVALUATION_MODE", raising=False)


class FakeRouterAgent:
    def classify_question(self, message, previous_classification=None, history=None):
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
        return (
            "expert" in lowered
            or "ekr" in lowered
            or "human help" in lowered
            or "send the email" in lowered
            or "send email" in lowered
        )

    def is_confirmation(self, message):
        normalized = " ".join(message.strip().lower().strip(" \t\r\n.,!?").split())
        return normalized in {
            "yes",
            "yes please",
            "sure",
            "sure please",
            "sure thing",
            "ok",
            "okay",
            "okay please",
            "yep",
            "yeah",
            "absolutely",
            "please do",
            "go ahead",
            "send it",
            "send it please",
            "please send it",
            "send email",
            "please send email",
            "send the email",
            "please send the email",
            "yes send the email",
            "yes please send the email",
        }

    def is_rejection(self, message):
        normalized = message.strip().lower().strip(" \t\r\n.,!?")
        return normalized in {
            "no",
            "no thanks",
            "no thank you",
            "don't send",
            "do not send",
            "cancel",
            "not now",
            "never mind",
        }


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
        return {"status": "submitted", "cc_submitted": False}


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
    stub_module(
        "src.services.expert_handoff_email",
        handoff_response_language=lambda messages, metadata: "en",
        send_expert_handoff_email=EmailRecorder.send,
        prepare_expert_handoff=lambda thread_id, messages, metadata: {
            "enabled": True, "recipient": "advisor@example.com", "cc": None,
            "request_id": "preview-1", "summary": "Summary to copy",
        },
        expert_handoff_preview_matches=lambda thread_id, messages, metadata: bool(metadata.get("expert_handoff_preview")),
        copyable_expert_handoff_summary=lambda messages, metadata: "Summary to copy",
    )
    return fresh_import("src.pipeline.agent_router")


def pending_metadata(**extra):
    return {
        "expert_handoff_pending_confirmation": True,
        "expert_handoff_preview": {
            "request_id": "preview-1", "recipient": "advisor@example.com",
            "cc": None, "summary": "Summary to copy",
        },
        **extra,
    }


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
    assert "advisor@example.com" in response["content"]
    assert "full conversation" in response["content"]
    assert "contact information" in response["content"]
    assert "Summary to copy" in response["content"]
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
        pending_metadata(address=["Main Street 1"]),
        "thread-2",
    )

    assert response["classification"] == "expert_handoff"
    assert "mail server accepted" in response["content"]
    assert "Delivery to the inbox is not confirmed" in response["content"]
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert metadata["expert_handoff_requested"] is True
    assert len(EmailRecorder.calls) == 1
    assert EmailRecorder.calls[0][0] == "thread-2"


def test_route_message_accepts_sure_as_handoff_confirmation():
    module = import_agent_router_module()
    router = module.AgentRouter()

    response, metadata = router.route_message(
        [
            {"role": "user", "content": "Can I talk to an expert?"},
            {
                "role": "assistant",
                "content": "I can email this conversation and the available session details to an EKR expert. Do you want me to send it?",
            },
            {"role": "user", "content": "sure."},
        ],
        "sure.",
        pending_metadata(),
        "thread-sure",
    )

    assert response["classification"] == "expert_handoff"
    assert "mail server accepted" in response["content"]
    assert "Delivery to the inbox is not confirmed" in response["content"]
    assert metadata["expert_handoff_sent"] is True


def test_route_message_requires_new_preview_after_lost_pending_state():
    module = import_agent_router_module()
    router = module.AgentRouter()

    response, metadata = router.route_message(
        [
            {"role": "user", "content": "Can I talk to an expert?"},
            {
                "role": "assistant",
                "content": "I can email this conversation and the available session details to an EKR expert. Do you want me to send it?",
            },
            {"role": "user", "content": "sure"},
            {
                "role": "assistant",
                "content": "Got it! I'll send this conversation to an EKR expert for you. They'll follow up as needed.",
            },
            {"role": "user", "content": "please send the email."},
        ],
        "please send the email.",
        {},
        "thread-lost-pending",
    )

    assert response["classification"] == "expert_handoff"
    assert "Do you want me to send it?" in response["content"]
    assert metadata["expert_handoff_sent"] is False
    assert EmailRecorder.calls == []


def test_route_message_cancels_expert_handoff_on_rejection():
    module = import_agent_router_module()
    router = module.AgentRouter()

    response, metadata = router.route_message(
        [{"role": "user", "content": "no"}],
        "no",
        pending_metadata(),
        "thread-3",
    )

    assert response["classification"] == "expert_handoff"
    assert "will not send" in response["content"]
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert EmailRecorder.calls == []


def test_pending_rejection_is_handled_before_any_confirmation(monkeypatch):
    module = import_agent_router_module()
    pipeline = module.AgentRouter()
    monkeypatch.setattr(pipeline.router, "is_confirmation", lambda message: True)
    monkeypatch.setattr(pipeline.router, "is_rejection", lambda message: True)
    response, metadata = pipeline.route_message(
        [{"role": "user", "content": "Yes, but do not send."}], "Yes, but do not send.",
        pending_metadata(), "reject-before-confirm",
    )
    assert response["route"] == "expert_handoff"
    assert metadata["expert_handoff_status"] == "cancelled"
    assert metadata["expert_handoff_cancelled"] is True
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert metadata["expert_handoff_confirmed"] is False
    assert EmailRecorder.calls == []


def test_route_message_reports_email_failure_to_user():
    module = import_agent_router_module()
    router = module.AgentRouter()
    EmailRecorder.should_raise = True

    response, metadata = router.route_message(
        [{"role": "user", "content": "yes"}],
        "yes",
        pending_metadata(),
        "thread-4",
    )

    assert response["classification"] == "expert_handoff"
    assert "could not confirm" in response["content"]
    assert "Summary to copy" in response["content"]
    assert metadata["expert_handoff_sent"] is False
    assert "RuntimeError" in metadata["expert_handoff_error"]


def test_route_message_accepts_longer_confirmation_phrase():
    module = import_agent_router_module()
    router = module.AgentRouter()

    response, metadata = router.route_message(
        [{"role": "user", "content": "yes please send the email"}],
        "yes please send the email",
        pending_metadata(),
        "thread-5",
    )

    assert response["classification"] == "expert_handoff"
    assert "mail server accepted" in response["content"]
    assert "Delivery to the inbox is not confirmed" in response["content"]
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
        pending_metadata(address=["Main Street 1"]),
        "thread-7",
    )

    assert response["classification"] == "draft_energy_report"
    assert response["content"] == "draft report reply"
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert EmailRecorder.calls == []


def test_pending_flag_without_preview_does_not_authorize_sending():
    module = import_agent_router_module()
    response, metadata = module.AgentRouter().route_message(
        [{"role": "user", "content": "yes"}], "yes",
        {"expert_handoff_pending_confirmation": True}, "thread-no-preview",
    )
    assert "Do you want me to send it?" in response["content"]
    assert EmailRecorder.calls == []


def test_disabled_handoff_offers_summary_without_confirmation_or_send(monkeypatch):
    module = import_agent_router_module()
    monkeypatch.setattr(module, "prepare_expert_handoff", lambda *args: {
        "enabled": False, "summary": "Summary to copy",
    })
    response, metadata = module.AgentRouter().route_message(
        [{"role": "user", "content": "I need an expert"}], "I need an expert", {}, "thread-disabled",
    )
    assert "not enabled or fully configured" in response["content"]
    assert "Summary to copy" in response["content"]
    assert "Do you want me to send it?" not in response["content"]
    assert metadata["expert_handoff_status"] == "disabled"
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert EmailRecorder.calls == []


def test_evaluation_confirmation_never_sends_email(monkeypatch):
    module = import_agent_router_module()
    monkeypatch.setenv("EVALUATION_MODE", "true")
    response, metadata = module.AgentRouter().route_message(
        [{"role": "user", "content": "yes"}], "yes", pending_metadata(), "thread-evaluation",
    )
    assert "No real email was sent" in response["content"]
    assert metadata["expert_handoff_status"] == "simulated"
    assert EmailRecorder.calls == []


def test_repeated_confirmation_after_success_does_not_send_again():
    module = import_agent_router_module()
    router = module.AgentRouter()
    response, metadata = router.route_message(
        [{"role": "user", "content": "yes"}], "yes", pending_metadata(), "thread-repeat",
    )
    router.route_message(
        [response, {"role": "user", "content": "yes"}], "yes", metadata, "thread-repeat",
    )
    assert len(EmailRecorder.calls) == 1


def test_fast_conversation_clears_pending_consent():
    module = import_agent_router_module()
    router = module.AgentRouter()
    response, metadata = router.route_message(
        [{"role": "user", "content": "hello"}], "hello", pending_metadata(), "thread-topic",
    )
    assert metadata["expert_handoff_pending_confirmation"] is False
    router.route_message([response, {"role": "user", "content": "yes"}], "yes", metadata, "thread-topic")
    assert EmailRecorder.calls == []


@pytest.mark.parametrize("status", ["failed", "unknown", "sending"])
def test_submission_failure_provides_copyable_summary(monkeypatch, status):
    module = import_agent_router_module()
    monkeypatch.setattr(module, "send_expert_handoff_email", lambda *args: {"status": status})
    response, metadata = module.AgentRouter().route_message(
        [{"role": "user", "content": "yes"}], "yes", pending_metadata(), "thread-failure",
    )
    assert "Summary to copy" in response["content"]
    assert "municipality" in response["content"]
    assert metadata["expert_handoff_sent"] is False
    if status in {"unknown", "sending"}:
        assert metadata["expert_handoff_status"] == "unknown"
        assert "not automatically resend" in response["content"]
    else:
        assert metadata["expert_handoff_status"] == "failed"
