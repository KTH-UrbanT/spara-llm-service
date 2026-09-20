import json
from types import SimpleNamespace

import pytest

from tests.support import fresh_import
from tests.unit.test_building_flow_graph import import_building_flow_graph_module


class FakeClient:
    def __init__(self, payload, *, finish_reason="stop", refusal=None, error=None):
        self.payload, self.finish_reason, self.refusal, self.error = payload, finish_reason, refusal, error
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason=self.finish_reason, message=SimpleNamespace(content=content, refusal=self.refusal)
        )])


def agent_for(payload, **kwargs):
    module = fresh_import("src.agents.clarification_agent")
    client = FakeClient(payload, **kwargs)
    return module.ClarificationAgent(client=client, deployment="gpt-5.6-sol"), client


def test_question_uses_context_with_strict_schema_without_private_metadata():
    agent, client = agent_for({"focus": "timing", "question": "When did you first notice the change?"})
    result = agent.generate(state={
        "last_message": "Only on weekdays now.",
        "messages": [
            {"role": "user", "content": "The common room is uncomfortable and we cannot afford new equipment."},
            {"role": "assistant", "content": "When do you notice this?"},
            {"role": "user", "content": "Only on weekdays now."},
        ],
        "metadata": {"api_key": "DO-NOT-FORWARD", "query_traces": {"password": "DO-NOT-FORWARD"}},
    }, reason="incomplete_question", fallback="Fallback?")
    assert result["status"] == "generated"
    call = client.calls[0]
    assert call["model"] == "gpt-5.6-sol"
    assert call["response_format"]["json_schema"]["strict"] is True
    context = json.loads(call["messages"][1]["content"])
    assert "cannot afford" in context["history"][0]["content"]
    assert "DO-NOT-FORWARD" not in json.dumps(call)


@pytest.mark.parametrize("payload", [
    {"focus": "objective", "question": "Your building is inefficient. What would you like to change?"},
    {"focus": "objective", "question": "Would a guaranteed saving of 25% help?"},
    {"focus": "objective", "question": "Would you like to use SolarVendor?"},
    {"focus": "objective", "question": "What is your email address?"},
    {"focus": "building_identity", "question": "What is your street address?"},
    {"focus": "objective", "question": "What do you need? When did it start?"},
    {"focus": "objective", "question": "Could you check https://example.org?"},
    {"focus": "objective", "question": "How can I help?", "building_id": "invented"},
    "not json",
])
def test_invalid_or_assertive_output_falls_back(payload):
    agent, _ = agent_for(payload)
    result = agent.generate(state={}, reason="incomplete_question", fallback="What would you like help understanding?")
    assert result == {"question": "What would you like help understanding?", "status": "fallback"}


@pytest.mark.parametrize("kwargs", [
    {"finish_reason": "length"}, {"refusal": "refused"}, {"error": TimeoutError()},
])
def test_api_failure_or_partial_output_has_no_user_visible_exception(kwargs):
    agent, _ = agent_for({"focus": "building_identity", "question": "Which building is this for?"}, **kwargs)
    result = agent.generate(state={}, reason="missing_address", fallback="Which building is this for?")
    assert result["status"] == "fallback"
    assert result["question"] == "Which building is this for?"


def test_identity_gate_cannot_be_replaced_with_diagnostic_question():
    agent, _ = agent_for({"focus": "objective", "question": "What outcome matters most?"})
    result = agent.generate(state={}, reason="missing_address", fallback="Which building is this for?")
    assert result["status"] == "fallback"


def test_location_gate_requires_location_question():
    agent, _ = agent_for({"focus": "building_location", "question": "What would you like to improve?"})
    result = agent.generate(state={}, reason="ambiguous_address", fallback="Which city is the building in?")
    assert result["status"] == "fallback"


def test_selection_choices_and_lookup_outcome_are_preserved():
    agent, _ = agent_for({"focus": "building_selection", "question": "Which of these buildings is the report for?"})
    fallback = "I found more than one building for this BRF. Which building should I use?\n\n1. ID-A - Testgatan 1\n2. ID-B - Testgatan 2\n\nReply with the number or building ID."
    result = agent.generate(state={}, reason="ambiguous_brf", fallback=fallback)
    assert result["status"] == "generated"
    assert result["question"].startswith("I found more than one building for this BRF. Which of these")
    assert result["question"].partition("\n")[2] == fallback.partition("\n")[2]
    assert result["question"].count("?") == 1


def test_lookup_failure_is_not_presented_as_absent_record():
    agent, _ = agent_for({"focus": "building_identity", "question": "Could you share the full street address?"})
    result = agent.generate(state={}, reason="brf_lookup_failed", fallback="I could not look up that BRF right now. Please share its address.")
    assert result["question"].startswith("I could not look up that BRF right now.")
    assert "not found" not in result["question"]


def test_repeated_question_is_rejected():
    question = "What would you like to achieve?"
    agent, _ = agent_for({"focus": "objective", "question": question})
    result = agent.generate(state={"messages": [{"role": "assistant", "content": question}, {"role": "user", "content": "I already said better comfort."}]}, reason="incomplete_question", fallback="What is still unclear?")
    assert result["status"] == "fallback"


def test_only_matched_recorded_facts_are_forwarded():
    agent, client = agent_for({"focus": "missing_evidence", "question": "What measurements are available?"})
    state = {"metadata": {"byggnadsid": "ID-A", "building_identity_check": {"status": "passed"},
                          "retrieved_facts": {"byggnadsid": "ID-B", "heating_system": "unrelated"}}}
    agent.generate(state=state, reason="missing_building_data", fallback="What information is available?")
    assert json.loads(client.calls[0]["messages"][1]["content"])["recorded_facts"] == {}
    state["metadata"]["retrieved_facts"]["byggnadsid"] = "ID-A"
    agent.generate(state=state, reason="missing_building_data", fallback="What information is available?")
    assert json.loads(client.calls[1]["messages"][1]["content"])["recorded_facts"] == {"heating_system": "unrelated"}


def test_missing_configuration_uses_fallback_without_api_call(monkeypatch):
    monkeypatch.delenv("CLARIFICATION_MODEL_DEPLOYMENT_NAME", raising=False)
    module = fresh_import("src.agents.clarification_agent")
    result = module.ClarificationAgent().generate(state={}, reason="missing_address", fallback="Which building?")
    assert result == {"question": "Which building?", "status": "disabled"}


@pytest.mark.parametrize("node,reason", [("request_address_node", "missing_address"), ("clarification_node", "incomplete_question")])
def test_graph_uses_generated_question_and_keeps_pending_state(node, reason):
    module = import_building_flow_graph_module()
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return {"question": "Which information should we clarify?", "status": "generated", "focus": "scope"}
    module.generate_clarification = generate
    state = {"last_message": "Can you help with this?", "messages": [], "metadata": {"pending_building_advice": {"question": "original request"}}}
    result = getattr(module, node)(state)
    assert calls[0]["reason"] == reason
    assert result["final_response"] == "Which information should we clarify?"
    clarification = result["metadata"]["clarification"]
    assert clarification["question_asked"] == result["final_response"]
    assert clarification["needed"] is True and clarification["resolved"] is False
    assert clarification["generation_status"] == "generated"
    assert result["metadata"]["pending_building_advice"] == {"question": "original request"}
