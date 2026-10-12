"""Exercise handoff consent through real routing and MIME generation, offline."""
from copy import deepcopy
from email import message_from_string
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from tests.support import fresh_import


THREAD_ID = "pilot@example.com:INTERNAL-SESSION-DO-NOT-SHARE"
RAW_SENTINEL = "RAW-TRANSCRIPT-DO-NOT-SHARE"
METADATA_SENTINEL = "METADATA-DO-NOT-SHARE"
SUMMARY = "Case summary: investigate uneven heating before major investment."


class MemoryStore:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value


def _load_source(name, relative_path):
    path = Path(__file__).resolve().parents[2] / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def handoff_flow(monkeypatch):
    """No real model client, developer environment, Redis or SMTP is available."""
    for key, value in {
        "AZURE_ENDPOINT": "https://example.openai.azure.com",
        "ROUTER_MODEL_DEPLOYMENT_NAME": "offline-router",
        "OPENAI_API_KEY": "offline-only",
        "EXPERT_EMAIL_ENABLED": "true",
        "EXPERT_EMAIL_SMTP_SERVER": "smtp.example.com",
        "EXPERT_EMAIL_SMTP_PORT": "587",
        "EXPERT_EMAIL_ADDRESS": "service@example.com",
        "EXPERT_EMAIL_ACCOUNT": "service@example.com",
        "EXPERT_EMAIL_PASSWORD": "offline-only",
        "EXPERT_EMAIL_RECIPIENT": "advisor@example.com",
        "REDIS_PORT": "6379",
        "EVALUATION_MODE": "false",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(
        load_dotenv=lambda *args, **kwargs: None,
    ))
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(
        OpenAI=lambda **kwargs: object(), APIConnectionError=ConnectionError,
        RateLimitError=RuntimeError, APIStatusError=ValueError,
    ))
    store = MemoryStore()
    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(
        StrictRedis=lambda *args, **kwargs: store,
    ))
    summary_calls = []

    class OfflineSummarizer:
        def __init__(self, **kwargs):
            pass

        def generate_response(self, prompt, **kwargs):
            summary_calls.append((prompt, kwargs))
            return SUMMARY

    monkeypatch.setitem(sys.modules, "src.agents.openai_agent", types.SimpleNamespace(
        OpenAIResponseAgent=OfflineSummarizer,
    ))
    real_router = _load_source("_handoff_scope_router", "src/agents/router_agent.py")
    service = _load_source("_handoff_scope_service", "src/services/expert_handoff_email.py")
    monkeypatch.setitem(sys.modules, "src.agents.router_agent", real_router)
    monkeypatch.setitem(sys.modules, "src.services.expert_handoff_email", service)

    class UnusedAgent:
        def handle_building_query(self, *args, **kwargs):
            pytest.fail("Handoff flow entered building lookup")

        def handle_generic_input(self, *args, **kwargs):
            pytest.fail("Handoff flow entered general generation")

        def handle_cluster_query(self, *args, **kwargs):
            pytest.fail("Handoff flow entered cluster lookup")

        def handle_conversational_input(self, *args, **kwargs):
            pytest.fail("Handoff flow entered conversational generation")

    for module_name, class_name in (
        ("src.agents.building_agent", "BuildingAgent"),
        ("src.agents.cluster_agent", "ClusterAgent"),
        ("src.agents.generic_agent", "GenericAgent"),
        ("src.agents.aggregator_agent", "AggregatorAgent"),
        ("src.agents.conversationalist_agent", "ConversationalAgent"),
    ):
        monkeypatch.setitem(sys.modules, module_name, types.SimpleNamespace(**{class_name: UnusedAgent}))

    def unused_report(*args, **kwargs):
        pytest.fail("Handoff flow entered report generation")

    monkeypatch.setitem(sys.modules, "src.services.draft_report_service", types.SimpleNamespace(
        generate_draft_report_response=unused_report,
    ))
    # Track the original module so monkeypatch restores other tests' import state.
    monkeypatch.setitem(sys.modules, "src.pipeline.agent_router", types.ModuleType("src.pipeline.agent_router"))
    pipeline_module = fresh_import("src.pipeline.agent_router")
    pipeline = pipeline_module.AgentRouter()
    monkeypatch.setattr(pipeline.router, "classify_question", lambda *args, **kwargs: pytest.fail(
        "Explicit handoff flow must not need model classification"
    ))
    smtp_calls = []

    class RecordingSMTP:
        def __init__(self, server, port, timeout):
            assert server == "smtp.example.com"

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, account, password):
            assert account == "service@example.com"

        def sendmail(self, sender, recipients, content):
            smtp_calls.append((sender, recipients, content))
            return {}

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(service.smtplib, "SMTP", RecordingSMTP)
    return types.SimpleNamespace(
        pipeline=pipeline, service=service, summary_calls=summary_calls,
        smtp_calls=smtp_calls, store=store,
    )


def _conversation(language="en"):
    return [
        {"role": "user", "content": f"Our building has uneven heating. {RAW_SENTINEL}"},
        {"role": "assistant", "content": "Measure temperatures before selecting an investment."},
    ], {
        "response_language": language,
        "advisory_context": {"private_note": METADATA_SENTINEL},
        "building_id": "01-80-TAPETEN2-1",
        "building_identity_check": {
            "status": "passed", "matched_building_id": "01-80-TAPETEN2-1",
        },
        "retrieved_facts": {"byggnadsid": "01-80-TAPETEN2-1", "energy_class": "F"},
    }


def _turn(flow, messages, metadata, text):
    messages.append({"role": "user", "content": text})
    response, updated = flow.pipeline.route_message(messages, text, metadata, THREAD_ID)
    assert response["route"] == "expert_handoff"
    messages.append(response)
    return response, updated


def _mime_texts(flow):
    message = message_from_string(flow.smtp_calls[-1][2])
    texts = [
        part.get_payload(decode=True).decode("utf-8")
        for part in message.walk() if not part.is_multipart()
    ]
    return message, texts


@pytest.mark.parametrize("language,handoff_request,approval,status_question", [
    ("sv", "Skicka bara sammanfattningen till energirådgivaren.", "Ja, skicka", "Har något faktiskt skickats, och vad var det jag godkände att dela?"),
    ("en", "Send only the summary to the energy advisor.", "Yes, send it", "Was anything actually sent, and what did I approve sharing?"),
])
def test_submission_receipt_restores_and_status_never_classifies_or_resends(handoff_flow, language, handoff_request, approval, status_question):
    flow = handoff_flow
    messages, metadata = _conversation(language)
    _, metadata = _turn(flow, messages, metadata, handoff_request)
    submitted, metadata = _turn(flow, messages, metadata, approval)
    assert len(flow.smtp_calls) == 1
    assert submitted["metadata"]["action_receipts"]["handoffs"][-1]["status"] == "submitted"
    saved_message = {"role": "assistant", "content": submitted["content"], "metadata": deepcopy(submitted["metadata"])}
    restored_messages = [saved_message, {"role": "user", "content": status_question}]
    response, restored = flow.pipeline.route_message(restored_messages, status_question, {}, THREAD_ID)
    assert response["route"] == "action_status"
    assert restored["action_receipts"]["handoffs"][-1]["sharing_scope"] == "summary_only"
    assert "advisor@example.com" in response["content"] and "pilot@example.com" in response["content"]
    assert ("inkorg är inte bekräftad" if language == "sv" else "inbox is not confirmed") in response["content"]
    assert len(flow.smtp_calls) == 1 and len(flow.summary_calls) == 1


def test_status_question_preserves_pending_preview_and_does_not_count_as_approval(handoff_flow):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please send only the summary to an energy advisor.")
    original_preview = deepcopy(metadata["expert_handoff_preview"])
    question = "Has anything been sent?"
    messages.append({"role": "user", "content": question})
    status_reply, metadata = flow.pipeline.route_message(messages, question, metadata, THREAD_ID)
    messages.append(status_reply)
    assert metadata["expert_handoff_preview"] == original_preview
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert "has not been approved or sent" in status_reply["content"]
    assert flow.smtp_calls == []
    _, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_status"] == "submitted"
    assert len(flow.smtp_calls) == 1


def test_simulated_receipt_remains_simulated_after_restore(handoff_flow, monkeypatch):
    flow = handoff_flow
    monkeypatch.setenv("EVALUATION_MODE", "true")
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please send only the summary to an energy advisor.")
    simulated, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_status"] == "simulated"
    question = "Was the email sent?"
    response, restored = flow.pipeline.route_message([simulated, {"role": "user", "content": question}], question, {}, THREAD_ID)
    assert "simulated" in response["content"] and "No real email was sent" in response["content"]
    assert restored["action_receipts"]["handoffs"][-1]["status"] == "simulated"
    assert flow.smtp_calls == []


def test_next_handoff_summary_keeps_submission_history_outside_the_case_summary(handoff_flow):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please send only the summary to an energy advisor.")
    _, metadata = _turn(flow, messages, metadata, "Yes, send it")
    _, metadata = _turn(flow, messages, metadata, "Please forward this conversation to an energy advisor.")
    prompt, _ = flow.summary_calls[-1]
    assert "Verified application action records" not in prompt
    assert '"status": "submitted"' not in prompt
    assert "The mail server accepted" not in prompt
    assert any(record["status"] == "submitted" for record in metadata["action_receipts"]["handoffs"])
    assert len(flow.smtp_calls) == 1


@pytest.mark.parametrize("language,utterance,approval", [
    ("sv", "Kan du skicka bara sammanfattningen till energirådgivaren?", "Ja, skicka"),
    ("en", "Please send only the summary to the energy advisor.", "Yes, send it"),
])
def test_initial_summary_only_requires_preview_and_shares_only_approved_summary(handoff_flow, language, utterance, approval):
    flow = handoff_flow
    messages, metadata = _conversation(language)
    preview_response, metadata = _turn(flow, messages, metadata, utterance)
    assert metadata["expert_handoff_scope"] == "summary_only"
    assert metadata["expert_handoff_preview"]["sharing_scope"] == "summary_only"
    assert SUMMARY in preview_response["content"]
    assert "advisor@example.com" in preview_response["content"]
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert flow.smtp_calls == []
    assert len(flow.summary_calls) == 1

    _, metadata = _turn(flow, messages, metadata, approval)
    assert metadata["expert_handoff_status"] == "submitted"
    assert len(flow.smtp_calls) == 1
    assert len(flow.summary_calls) == 1
    message, texts = _mime_texts(flow)
    assert len(texts) == 1
    assert SUMMARY in texts[0]
    assert message.get("To") == "advisor@example.com"
    assert all(part.get_content_disposition() != "attachment" for part in message.walk())
    assert THREAD_ID not in message["Subject"]
    for sentinel in (RAW_SENTINEL, METADATA_SENTINEL, "INTERNAL-SESSION-DO-NOT-SHARE"):
        assert all(sentinel not in text for text in texts)


@pytest.mark.parametrize("change", [
    "Ja, men skicka bara sammanfattningen.",
    "Send only the summary, not the full conversation.",
])
def test_full_to_summary_scope_change_reuses_summary_and_requires_separate_approval(handoff_flow, change):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please forward this conversation to an energy advisor.")
    old_preview = deepcopy(metadata["expert_handoff_preview"])
    assert old_preview["sharing_scope"] == "full_conversation"

    _, metadata = _turn(flow, messages, metadata, change)
    preview = metadata["expert_handoff_preview"]
    assert preview["sharing_scope"] == "summary_only"
    assert preview["request_id"] != old_preview["request_id"]
    assert preview["message_count"] == old_preview["message_count"]
    assert preview["summary"] == old_preview["summary"] == SUMMARY
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert flow.smtp_calls == []
    assert len(flow.summary_calls) == 1

    _, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_status"] == "submitted"
    assert len(flow.smtp_calls) == 1
    _, texts = _mime_texts(flow)
    assert len(texts) == 1
    assert RAW_SENTINEL not in texts[0]


def test_repeating_same_scope_is_not_consent_and_cancel_submits_nothing(handoff_flow):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please send only the summary to an energy advisor.")
    _, metadata = _turn(flow, messages, metadata, "Yes, send only the summary.")
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert flow.smtp_calls == []
    assert len(flow.summary_calls) == 1

    _, metadata = _turn(flow, messages, metadata, "No, do not send")
    assert metadata["expert_handoff_status"] == "cancelled"
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert flow.smtp_calls == []


def test_summary_only_approval_survives_persisted_transport_metadata(handoff_flow):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please send only the summary to an energy advisor.")
    for index, message in enumerate(messages):
        message.update({
            "message_id": index + 1, "timestamp": 1000 + index,
            "metadata": {"in_reply_to_message_id": index, "transport": "restored"},
            "version": 1,
        })
    metadata.update({
        "route": "expert_handoff", "classification": "expert_handoff",
        "expert_handoff_status": "pending_confirmation",
        "telemetry": {"transport": "rehydrated"},
    })

    _, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_status"] == "submitted"
    assert len(flow.smtp_calls) == 1
    assert len(flow.summary_calls) == 1
    _, texts = _mime_texts(flow)
    assert len(texts) == 1
    assert "rehydrated" not in texts[0]


@pytest.mark.parametrize("tampering", [
    "summary", "preview_scope", "current_scope", "facts", "context", "transcript", "expired",
])
def test_changed_or_expired_preview_refreshes_without_submitting(handoff_flow, tampering):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please send only the summary to an energy advisor.")
    old_request = metadata["expert_handoff_preview"]["request_id"]
    metadata = deepcopy(metadata)
    if tampering == "summary":
        metadata["expert_handoff_preview"]["summary"] = "Altered after review"
    elif tampering == "preview_scope":
        metadata["expert_handoff_preview"]["sharing_scope"] = "full_conversation"
    elif tampering == "current_scope":
        metadata["expert_handoff_scope"] = "full_conversation"
    elif tampering == "facts":
        metadata["retrieved_facts"]["energy_class"] = "A"
    elif tampering == "context":
        metadata["advisory_context"]["private_note"] = "Changed source context"
    elif tampering == "transcript":
        messages[0]["content"] = "Changed conversation after review"
    elif tampering == "expired":
        metadata["expert_handoff_preview"]["expires_at"] = 0

    _, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert metadata["expert_handoff_preview"]["request_id"] != old_request
    assert flow.smtp_calls == []


def test_full_conversation_mode_preserves_disclosed_attachment_and_duplicate_retry_does_not_resend(handoff_flow):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please forward this conversation to an energy advisor.")
    assert metadata["expert_handoff_preview"]["sharing_scope"] == "full_conversation"
    pending_metadata = deepcopy(metadata)
    retry_messages = deepcopy(messages) + [{"role": "user", "content": "Yes, send it"}]
    _, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_status"] == "submitted"
    message, texts = _mime_texts(flow)
    assert len(texts) == 2
    assert SUMMARY in texts[0]
    attachment = next(part for part in message.walk() if part.get_content_disposition() == "attachment")
    attachment_text = attachment.get_payload(decode=True).decode("utf-8")
    assert RAW_SENTINEL in attachment_text
    assert METADATA_SENTINEL in attachment_text
    assert THREAD_ID in message["Subject"]

    retry_response, retry_metadata = flow.pipeline.route_message(
        retry_messages, "Yes, send it", pending_metadata, THREAD_ID,
    )
    assert retry_metadata["expert_handoff_status"] == "submitted"
    assert "duplicate" in retry_response["content"].lower() or "already submitted" in retry_response["content"].lower()
    assert len(flow.smtp_calls) == 1
    assert len(flow.summary_calls) == 1


def test_summary_only_duplicate_approval_preserves_scope_without_resending(handoff_flow):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please send only the summary to an energy advisor.")
    pending_metadata = deepcopy(metadata)
    retry_messages = deepcopy(messages) + [{"role": "user", "content": "Yes, send it"}]
    _, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_status"] == "submitted"

    _, retry_metadata = flow.pipeline.route_message(
        retry_messages, "Yes, send it", pending_metadata, THREAD_ID,
    )
    assert retry_metadata["expert_handoff_status"] == "submitted"
    assert retry_metadata["expert_handoff_scope"] == "summary_only"
    assert len(flow.smtp_calls) == 1
    assert len(flow.summary_calls) == 1
    _, texts = _mime_texts(flow)
    assert len(texts) == 1
    assert RAW_SENTINEL not in texts[0]


@pytest.mark.parametrize("correction", [
    "Skicka bara sammanfattningen. Rättelse: budgeten är 20 000 kr.",
    "Send only the summary. Our heating system is district heating, not a heat pump.",
])
def test_scope_change_with_substantive_correction_regenerates_from_complete_history(handoff_flow, correction):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please forward this conversation to an energy advisor.")
    old_preview = deepcopy(metadata["expert_handoff_preview"])
    assert len(flow.summary_calls) == 1

    _, metadata = _turn(flow, messages, metadata, correction)
    preview = metadata["expert_handoff_preview"]
    assert metadata["expert_handoff_scope"] == "summary_only"
    assert preview["sharing_scope"] == "summary_only"
    assert preview["message_count"] > old_preview["message_count"]
    assert preview["request_id"] != old_preview["request_id"]
    assert len(flow.summary_calls) == 2
    assert correction.split(".", 1)[1].strip() in flow.summary_calls[-1][0]
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert flow.smtp_calls == []

    _, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_status"] == "submitted"
    assert len(flow.summary_calls) == 2
    assert len(flow.smtp_calls) == 1
    _, texts = _mime_texts(flow)
    assert len(texts) == 1


@pytest.mark.parametrize("changed_terms", [
    "Skicka bara sammanfattningen till annan@example.com.",
    "Send only the summary to board@example.com.",
    "Skicka bara sammanfattningen, men ta bort mitt namn först.",
    "Send only the summary without my email address.",
])
def test_scope_request_with_unsupported_recipient_or_redaction_invalidates_consent(handoff_flow, changed_terms):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please forward this conversation to an energy advisor.")
    _, metadata = _turn(flow, messages, metadata, changed_terms)
    assert metadata["expert_handoff_status"] == "changes_requested"
    assert metadata["expert_handoff_requested_changes"] == changed_terms
    assert metadata["expert_handoff_preview"] is None
    assert metadata["expert_handoff_confirmed"] is False
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert len(flow.summary_calls) == 1
    assert flow.smtp_calls == []

    response, metadata = _turn(flow, messages, metadata, "Yes, send it")
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert metadata["expert_handoff_preview"]["recipient"] == "advisor@example.com"
    assert "not been applied" in response["content"] or "inte tillämpats" in response["content"]
    assert flow.smtp_calls == []


@pytest.mark.parametrize("negated_full", [
    "Do not share the full conversation.",
    "Dela inte hela samtalet.",
    "Dela inte hela samtalet, bara sammanfattningen.",
    "Please share only the summary; do not share the full conversation.",
])
def test_negated_full_sharing_selects_summary_preview_without_submission(handoff_flow, negated_full):
    flow = handoff_flow
    messages, metadata = _conversation()
    _, metadata = _turn(flow, messages, metadata, "Please forward this conversation to an energy advisor.")
    assert flow.pipeline.router.requested_handoff_scope(negated_full) == "summary_only"

    _, metadata = _turn(flow, messages, metadata, negated_full)
    assert metadata["expert_handoff_scope"] == "summary_only"
    assert metadata["expert_handoff_preview"]["sharing_scope"] == "summary_only"
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert len(flow.summary_calls) == 1
    assert flow.smtp_calls == []


def test_saved_six_turn_release_script_reconciles_history_and_never_sends(handoff_flow):
    flow = handoff_flow
    messages, metadata = _conversation("sv")
    genuine = [{
        "request_id": f"legacy-message-{message_id}", "status": "submitted",
        "legacy_status_only": True, "building_id": None,
    } for message_id in (455, 467)]
    for message_id in (455, 467):
        messages.append({
            "role": "assistant", "message_id": message_id,
            "content": "E-postservern tog emot meddelandet. Leverans till inkorgen är inte bekräftad.",
            "metadata": {
                "agent": "ExpertHandoffAgent", "route": "expert_handoff",
                "classification": "expert_handoff", "expert_handoff_status": "submitted",
                "expert_handoff_sent": True, "expert_handoff_triggered": True,
            },
        })
    # SQL persisted a router status reply with inherited legacy execution flags.
    # Later turns copied its phantom receipt into the canonical collection.
    messages.append({
        "role": "assistant", "message_id": 513,
        "content": "E-postservern tog emot ett meddelande.",
        "metadata": {
            "agent": "RouterAgent", "route": "expert_handoff",
            "classification": "expert_handoff", "expert_handoff_status": "submitted",
            "expert_handoff_sent": True, "expert_handoff_triggered": True,
            "action_receipts": {"reports": [], "handoffs": deepcopy(genuine)},
        },
    })
    metadata["action_receipts"] = {"reports": [], "handoffs": [
        *deepcopy(genuine), {
            "request_id": "legacy-message-513", "status": "submitted",
            "legacy_status_only": True, "building_id": metadata["building_id"],
        },
    ]}

    def persisted_turn(text, current_metadata):
        user_id = 1000 + len(messages) * 2
        messages.append({"role": "user", "message_id": user_id, "content": text})
        response, updated = flow.pipeline.route_message(messages, text, current_metadata, THREAD_ID)
        agent = "RouterAgent" if response.get("agent_answered") == "router" else "ExpertHandoffAgent"
        messages.append({
            **deepcopy(response), "message_id": user_id + 1,
            "metadata": {
                **deepcopy(updated), "agent": agent,
                # The actual persisted transport normalized this route too.
                "route": "expert_handoff", "in_reply_to_message_id": user_id,
            },
        })
        return response, updated

    first_request = (
        "Vi vill få hjälp med ojämn värme. Budgeten är 50 000 kr. "
        "Visa en förhandsvisning till energirådgivaren med hela samtalet som bilaga. "
        "Skicka inget ännu."
    )
    response, metadata = persisted_turn(first_request, metadata)
    assert response["route"] == "expert_handoff"
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    first_preview = metadata["expert_handoff_preview"]["request_id"]
    assert metadata["expert_handoff_preview"]["sharing_scope"] == "full_conversation"

    correction = "Skicka bara sammanfattningen. Rättelse: budgeten är nu 20 000 kr."
    response, metadata = persisted_turn(correction, metadata)
    assert response["route"] == "expert_handoff"
    revised_preview = metadata["expert_handoff_preview"]["request_id"]
    assert revised_preview != first_preview
    assert metadata["expert_handoff_preview"]["sharing_scope"] == "summary_only"
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert "Rättelse: budgeten är nu 20 000 kr." in flow.summary_calls[-1][0]

    response, metadata = persisted_turn(
        "Har något faktiskt skickats, och har jag godkänt den senaste förhandsvisningen?", metadata,
    )
    assert response["route"] == "action_status"
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_preview"]["request_id"] == revised_preview
    assert metadata["expert_handoff_confirmed"] is False

    response, metadata = persisted_turn("Nej, skicka inte.", metadata)
    assert response["route"] == "expert_handoff"
    assert metadata["expert_handoff_status"] == "cancelled"
    assert metadata["expert_handoff_pending_confirmation"] is False

    status_question = (
        "Hur många e-postmeddelanden har faktiskt skickats i detta samtal, "
        "och vad hände med den senaste förhandsvisningen?"
    )
    response, metadata = persisted_turn(status_question, metadata)
    assert response["route"] == "action_status"
    assert "avbruten" in response["content"]
    assert "godkända sammanfattningen" not in response["content"]
    expected_receipts = deepcopy(metadata["action_receipts"])
    submitted = [r for r in expected_receipts["handoffs"] if r["status"] == "submitted"]
    assert {r["request_id"] for r in submitted} == {"legacy-message-455", "legacy-message-467"}
    assert "legacy-message-513" not in {r["request_id"] for r in expected_receipts["handoffs"]}
    assert next(r for r in expected_receipts["handoffs"] if r["request_id"] == first_preview)["status"] == "superseded"
    assert next(r for r in expected_receipts["handoffs"] if r["request_id"] == revised_preview)["status"] == "cancelled"
    assert first_preview not in response["content"] and revised_preview not in response["content"]

    # Rehydrate from persisted message metadata, including copied old receipts.
    messages[:] = json.loads(json.dumps(messages))
    response, restored = persisted_turn(status_question, {})
    assert response["route"] == "action_status"
    assert restored["action_receipts"] == expected_receipts
    assert flow.smtp_calls == []
    assert len(flow.summary_calls) == 2


def test_preview_without_send_permission_replaces_pending_preview_without_cancelling(handoff_flow):
    flow = handoff_flow
    messages, metadata = _conversation("sv")
    _, metadata = _turn(flow, messages, metadata, "Visa vad som skulle skickas till en energirådgivare.")
    old_request = metadata["expert_handoff_preview"]["request_id"]
    _, metadata = _turn(
        flow, messages, metadata,
        "Visa en förhandsvisning till energirådgivaren med hela samtalet som bilaga. Skicka inget ännu.",
    )
    assert metadata["expert_handoff_pending_confirmation"] is True
    assert metadata["expert_handoff_confirmed"] is False
    assert metadata["expert_handoff_preview"]["request_id"] != old_request
    assert metadata["expert_handoff_preview"]["sharing_scope"] == "full_conversation"
    assert flow.smtp_calls == []
