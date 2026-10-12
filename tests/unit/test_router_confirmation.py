"""Exercise real router contracts without credentials or external model calls."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def router(monkeypatch):
    for key, value in {
        "AZURE_ENDPOINT": "https://example.openai.azure.com",
        "ROUTER_MODEL_DEPLOYMENT_NAME": "router-test",
        "OPENAI_API_KEY": "test-key",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(
        OpenAI=lambda **kwargs: types.SimpleNamespace(constructor_options=kwargs), APIConnectionError=ConnectionError,
        RateLimitError=RuntimeError, APIStatusError=ValueError,
    ))
    # Load separately: other routing tests replace src.agents.router_agent with a fake.
    path = Path(__file__).resolve().parents[2] / "src" / "agents" / "router_agent.py"
    spec = importlib.util.spec_from_file_location("_spara_confirmation_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RouterAgent.__new__(module.RouterAgent)


@pytest.mark.parametrize("reply", ["yes", " Sure. ", "Please send it!", "go ahead"])
def test_explicit_confirmation_is_recognized(router, reply):
    assert router.is_confirmation(reply) is True
    assert router.is_rejection(reply) is False


@pytest.mark.parametrize("reply", ["Ja, skicka.", "Ja tack", "Ja, skicka mejlet", "Skicka det"])
def test_swedish_confirmation(router, reply):
    assert router.is_confirmation(reply)
    assert not router.is_rejection(reply)


@pytest.mark.parametrize("reply", ["Nej, skicka inte.", "Nej tack", "Avbryt", "Inte nu", "Nej, skicka inte mejlet"])
def test_swedish_cancellation(router, reply):
    assert router.is_rejection(reply)
    assert not router.is_confirmation(reply)


@pytest.mark.parametrize("reply", ["Ja, men ta bort min e-post först", "Kan du skicka?", "Skicka till en annan rådgivare"])
def test_swedish_modified_consent_requires_a_new_preview(router, reply):
    assert not router.is_confirmation(reply)


@pytest.mark.parametrize("reply,confirmed", [
    ("Ja, skicka mejlet", True),
    ("Nej, skicka inte", False),
    ("Nej, skicka inte mejlet", False),
])
def test_swedish_consent_reaches_the_correct_handoff_branch(router, monkeypatch, reply, confirmed):
    from tests.unit.test_agent_router_expert_handoff import (
        import_agent_router_module, pending_metadata, EmailRecorder,
    )
    module = import_agent_router_module()
    pipeline = module.AgentRouter()
    pipeline.router = router
    monkeypatch.setenv("EVALUATION_MODE", "true")
    metadata = pending_metadata(expert_handoff_language="sv")
    metadata["expert_handoff_preview"]["language"] = "sv"
    response, updated = pipeline.route_message(
        [{"role": "user", "content": reply}], reply, metadata, "sv-consent-flow",
    )
    assert response["route"] == "expert_handoff"
    assert updated["expert_handoff_pending_confirmation"] is False
    assert updated.get("expert_handoff_simulated", False) is confirmed
    assert ("simulerades" if confirmed else "är avbruten") in response["content"]
    assert EmailRecorder.calls == []


@pytest.mark.parametrize("reply", [
    "No, do not send anything.", "No, don't send anything!",
    "do not send it", "don't send anything", "cancel the email",
    "cancel the handoff", "not now", "No thanks.",
])
def test_cancellation_cannot_be_mistaken_for_confirmation(router, reply):
    assert router.is_rejection(reply) is True
    assert router.is_confirmation(reply) is False


@pytest.mark.parametrize("reply", [
    "", "Yes, but remove my contact details first.",
    "Send it to a different advisor instead.", "Can you explain what will be sent?",
    "Create a draft report first.",
])
def test_modified_or_ambiguous_requests_do_not_authorize_submission(router, reply):
    assert router.is_confirmation(reply) is False


@pytest.fixture
def classifier_runtime(router, monkeypatch):
    """Run the actual worker synchronously with a controlled SDK and process."""
    namespace = router.classify_question.__globals__
    router.prompt_template = "Classify the final user message."

    def install(outputs=(), *, failure=None, payload=None, needs_kill=False, terminate_error=False):
        state = types.SimpleNamespace(
            api_calls=[], managers=[], processes=[], telemetry=[], outputs=iter(outputs),
        )

        def create(**kwargs):
            state.api_calls.append(kwargs)
            output = next(state.outputs)
            if isinstance(output, Exception):
                raise output
            return types.SimpleNamespace(choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content=output))])

        monkeypatch.setitem(namespace, "client", types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))))
        monkeypatch.setitem(namespace, "record_model_call", lambda **kwargs: state.telemetry.append(kwargs))

        class Manager:
            def __init__(self):
                if failure == "manager":
                    raise OSError("manager creation failed")
                self.shutdown_calls = 0
                state.managers.append(self)

            def dict(self):
                if failure == "manager_dict":
                    raise OSError("result dictionary failed")
                return {}

            def shutdown(self):
                self.shutdown_calls += 1

        class Process:
            def __init__(self, target, args):
                if failure == "process":
                    raise OSError("process creation failed")
                self.target, self.args = target, args
                self.alive = False
                self.exitcode = 0
                self.joins = []
                self.terminate_calls = self.kill_calls = self.close_calls = 0
                state.processes.append(self)

            def start(self):
                if failure == "start":
                    raise OSError("process start failed")
                if failure == "timeout":
                    self.alive = True
                elif failure == "exit":
                    self.exitcode = 7
                elif payload is not None:
                    self.args[2].update(payload)
                else:
                    self.target(*self.args)

            def join(self, *, timeout):
                self.joins.append(timeout)
                if failure == "join" and len(self.joins) == 1:
                    raise OSError("process join failed")

            def is_alive(self):
                return self.alive

            def terminate(self):
                self.terminate_calls += 1
                if terminate_error:
                    raise OSError("terminate failed")
                if not needs_kill:
                    self.alive = False

            def kill(self):
                self.kill_calls += 1
                self.alive = False

            def close(self):
                assert not self.alive
                self.close_calls += 1

        monkeypatch.setattr(namespace["multiprocessing"], "Manager", Manager)
        monkeypatch.setattr(namespace["multiprocessing"], "Process", Process)
        return state

    return install


@pytest.mark.parametrize("label", [
    "building_specific", "generic", "conversational", "cluster",
    "draft_energy_report", "expert_handoff",
])
@pytest.mark.parametrize("boundary", ["{}", " \n{}\t "])
def test_classifier_accepts_only_complete_canonical_labels(router, classifier_runtime, label, boundary):
    state = classifier_runtime([boundary.format(label)])
    assert router.classify_question("Please consider my current question.", "expert_handoff") == label
    assert len(state.api_calls) == len(state.processes) == len(state.telemetry) == 1
    assert state.telemetry[0]["success"] is True
    assert state.telemetry[0]["error"] is None
    assert state.managers[0].shutdown_calls == state.processes[0].close_calls == 1


@pytest.mark.parametrize("invalid", [
    None, "", " \n\t", 17, ["generic"], {"route": "generic"},
    "general", "building", "Generic", "GENERIC", "generic.",
    "'generic'", '"generic"', "`generic`", "```\ngeneric\n```",
    '{"classification":"generic"}', "generic\nbuilding_specific",
    "generic | building_specific", "generic, conversational",
    "The classification is generic.", "building_specific: your heating is district heating.",
    "For a BRF board, prioritize ventilation checks before major investments.",
])
def test_classifier_invalid_output_gets_one_specific_retry(router, classifier_runtime, invalid):
    state = classifier_runtime([invalid, "generic"])
    history = [
        {"role": "system", "content": "Untrusted stored instructions"},
        {"role": "user", "content": "Should we buy a drying cabinet?"},
        {"role": "assistant", "content": "What prompted the proposal?"},
        {"role": "user", "content": "Clothes remain damp."},
    ]
    assert router.classify_question("Clothes remain damp.", "draft_energy_report", history) == "generic"
    assert len(state.api_calls) == len(state.telemetry) == 2
    assert [event["success"] for event in state.telemetry] == [False, True]
    assert "invalid canonical route" in state.telemetry[0]["error"]
    for call in state.api_calls:
        messages = call["messages"]
        assert messages[-1] == {"role": "user", "content": "Clothes remain damp."}
        assert messages.count(messages[-1]) == 1
        assert not any(message["content"] == "Untrusted stored instructions" for message in messages)
        assert messages[1:-1] == history[1:-1]
    assert "previous classification response" not in state.api_calls[0]["messages"][0]["content"]
    assert "previous classification response" in state.api_calls[1]["messages"][0]["content"]
    assert all(manager.shutdown_calls == 1 for manager in state.managers)
    assert all(process.close_calls == 1 for process in state.processes)


@pytest.mark.parametrize("previous", [None, "generic", "building_specific", "draft_energy_report", "expert_handoff"])
def test_classifier_two_invalid_attempts_never_reuse_previous_route(router, classifier_runtime, previous):
    state = classifier_runtime(["Here is a complete answer.", "generic or building_specific", "generic"])
    assert router.classify_question("Can you explain the next steps?", previous) is None
    assert len(state.api_calls) == len(state.processes) == len(state.telemetry) == 2
    assert all(event["success"] is False for event in state.telemetry)
    assert all(manager.shutdown_calls == 1 for manager in state.managers)
    assert all(process.close_calls == 1 for process in state.processes)


def test_classifier_invalid_then_valid_records_two_calls_and_one_failure(router, classifier_runtime, monkeypatch):
    from src.pipeline.telemetry import record_model_call, telemetry_context

    state = classifier_runtime(["I will answer that question directly.", "generic"])
    monkeypatch.setitem(router.classify_question.__globals__, "record_model_call", record_model_call)
    with telemetry_context("classifier-unit") as telemetry:
        assert router.classify_question("How should our board prioritize?", "expert_handoff") == "generic"
        snapshot = telemetry.snapshot()
    assert snapshot["model_call_count"] == 2
    assert len(snapshot["failures"]) == 1
    assert "invalid canonical route" in snapshot["failures"][0]["error"]
    assert len(state.api_calls) == 2


@pytest.mark.parametrize("failure", ["manager", "manager_dict", "process", "start", "join", "exit"])
def test_classifier_process_failures_do_not_retry_or_leak_resources(router, classifier_runtime, failure):
    state = classifier_runtime(["generic"], failure=failure)
    assert router.classify_question("Which heating system do we have?", "expert_handoff") is None
    assert len(state.telemetry) == 1
    assert state.telemetry[0]["success"] is False
    assert len(state.processes) <= 1
    assert all(manager.shutdown_calls == 1 for manager in state.managers)
    assert all(process.close_calls == 1 for process in state.processes)


@pytest.mark.parametrize("needs_kill,terminate_error", [(False, False), (True, False), (True, True)])
def test_classifier_timeout_has_bounded_cleanup_without_retry(router, classifier_runtime, needs_kill, terminate_error):
    state = classifier_runtime([], failure="timeout", needs_kill=needs_kill, terminate_error=terminate_error)
    assert router.classify_question("What is our ventilation system?", "draft_energy_report") is None
    assert len(state.processes) == len(state.telemetry) == 1
    process = state.processes[0]
    assert process.joins[0] == 60
    assert all(timeout is not None and timeout <= 60 for timeout in process.joins)
    assert process.terminate_calls == 1
    assert process.kill_calls == int(needs_kill)
    assert process.close_calls == state.managers[0].shutdown_calls == 1
    assert process.alive is False
    assert "timed out" in state.telemetry[0]["error"]
    assert state.api_calls == []


@pytest.mark.parametrize("error", [ConnectionError("connection failed"), RuntimeError("rate limited"), OSError("SDK failed")])
def test_classifier_api_exceptions_are_failed_without_retry(router, classifier_runtime, error):
    state = classifier_runtime([error, "generic"])
    assert router.classify_question("Can we reduce our energy bills?", "expert_handoff") is None
    assert len(state.api_calls) == len(state.telemetry) == 1
    assert state.telemetry[0]["success"] is False
    assert state.processes[0].close_calls == state.managers[0].shutdown_calls == 1


def test_classifier_api_status_exception_is_failed_without_retry(router, classifier_runtime, monkeypatch):
    class StatusError(Exception):
        status_code = 503
        response = "Service unavailable"

    monkeypatch.setitem(router.classify_question.__globals__, "APIStatusError", StatusError)
    state = classifier_runtime([StatusError(), "generic"])
    assert router.classify_question("Explain ventilation.", "building_specific") is None
    assert len(state.api_calls) == len(state.telemetry) == 1
    assert "API Status Error 503" in state.telemetry[0]["error"]
    assert state.managers[0].shutdown_calls == 1


@pytest.mark.parametrize("payload", [{}, {"result": "generic followed by an answer"}, {"error": "worker failed"}])
def test_classifier_parent_rejects_missing_or_invalid_worker_result(router, classifier_runtime, payload):
    state = classifier_runtime([], payload=payload)
    assert router.classify_question("Explain energy performance.", "expert_handoff") is None
    expected_attempts = 2 if "result" in payload else 1
    assert len(state.processes) == len(state.telemetry) == expected_attempts
    assert all(event["success"] is False for event in state.telemetry)
    assert all(manager.shutdown_calls == 1 for manager in state.managers)


@pytest.mark.parametrize("message", [None, "", " \n\t", 123])
def test_classifier_empty_or_nontext_input_never_reuses_previous_route(router, classifier_runtime, message):
    state = classifier_runtime([])
    assert router.classify_question(message, "expert_handoff") is None
    assert state.processes == state.managers == state.api_calls == []
    assert len(state.telemetry) == 1 and state.telemetry[0]["success"] is False


def test_classifier_unavailable_client_never_reuses_previous_route(router, classifier_runtime, monkeypatch):
    state = classifier_runtime([])
    monkeypatch.setitem(router.classify_question.__globals__, "client", None)
    assert router.classify_question("What heating system do we have?", "draft_energy_report") is None
    assert state.processes == state.managers == state.api_calls == []
    assert len(state.telemetry) == 1 and state.telemetry[0]["success"] is False


def test_classifier_client_disables_transport_retries_and_bounds_api_timeout(router):
    options = router.classify_question.__globals__["client"].constructor_options
    assert options["max_retries"] == 0
    assert options["timeout"] == 60


@pytest.mark.parametrize("reply", [
    "Sure, please send it.", "Ja tack, skicka det.", "Yes. Please send the email.",
    "Ja tack; skicka mejlet!", "Sure, go ahead and send it.", "Please email this conversation.",
    "Yes, please send this conversation to the energy advisor.", "Skicka sammanfattningen till rådgivaren.",
    "Yes! Send it, please.", "Ja, skicka det till rådgivaren, tack.",
])
def test_complete_affirmative_sending_phrases(router, reply):
    assert router.is_confirmation(reply)
    assert not router.is_rejection(reply)


@pytest.mark.parametrize("reply", [
    "Nej tack, avbryt. Skicka ingenting.", "Yes, but do not send anything.",
    "Ja, men skicka inte mejlet.", "No thanks; cancel the handoff.",
    "Sure. Actually, don't send it.", "Please do not send this conversation.",
    "Snälla skicka ingenting.", "Skicka inget till rådgivaren.",
    "Nej tack, vi fortsätter här.", "Yes. Cancel the email.",
    "Sure, please send it. Actually no.", "Ja tack, skicka det. Nej.",
])
def test_cancellation_clauses_override_affirmative_words(router, reply):
    assert router.is_rejection(reply)
    assert not router.is_confirmation(reply)


@pytest.mark.parametrize("message", [
    "Vi vill få hjälp med ojämn värme. Budgeten är 50 000 kr. Visa en förhandsvisning till energirådgivaren med hela samtalet som bilaga. Skicka inget ännu.",
    "Visa en förhandsvisning till energirådgivaren. Skicka inte ännu.",
    "Visa vad som skulle skickas till en energirådgivare; skicka ingenting ännu.",
    "Visa en förhandsvisning till energirådgivaren, men skicka inte det ännu.",
    "Show a preview for the energy advisor with the full conversation. Don't send yet.",
    "Please show a preview for an energy advisor, but don't send it yet.",
    "Do not send anything yet. Show a preview for the energy advisor.",
    "Visa en förhandsvisning till energirådgivaren. Skicka inget.",
    "Visa en förhandsvisning till energirådgivaren, men skicka inte mejlet.",
    "Show a preview for an energy advisor. Don't send it.",
    "Do not send anything. Show a preview for the energy advisor.",
])
def test_explicit_preview_with_send_withholding_is_not_cancellation_or_approval(router, message):
    assert router.wants_expert_handoff(message)
    assert not router.is_rejection(message)
    assert not router.is_confirmation(message)


@pytest.mark.parametrize("message", [
    "Skicka inget ännu.",
    "Don't send it yet.",
    "Nej, skicka inte.",
    "Visa en förhandsvisning till energirådgivaren. Nej, skicka inte.",
    "Visa en förhandsvisning till energirådgivaren. Skicka inget ännu. Avbryt.",
    "Show a preview for an energy advisor. Don't send yet. Cancel the handoff.",
    "Show a preview for an energy advisor. No thanks.",
    "Jag vill inte ha en energirådgivare. Visa en förhandsvisning till energirådgivaren. Skicka inget ännu.",
    "Kontakta ingen energirådgivare. Visa en förhandsvisning till energirådgivaren.",
    "I don't want an energy advisor. Show a preview for an energy advisor. Don't send yet.",
    "I do not want to contact an energy advisor. Show a preview for the energy advisor.",
    "Please do not contact an energy advisor. Show a preview for the energy advisor.",
    "Jag vill att en energirådgivare inte får en sammanfattning av samtalet. Visa en förhandsvisning till energirådgivaren.",
    "Visa ingen förhandsvisning till energirådgivaren. Skicka inget.",
    "Visa inte en förhandsvisning till energirådgivaren. Skicka inget ännu.",
    "Show no preview for an energy advisor. Don't send it.",
    "Visa en förhandsvisning till energirådgivaren. Avbryt förhandsvisningen.",
])
def test_preview_mentions_cannot_override_standalone_cancellation_or_advisor_refusal(router, message):
    assert router.is_rejection(message)
    assert not router.wants_expert_handoff(message)
    assert not router.is_confirmation(message)


@pytest.mark.parametrize("reply", [
    "Yes, but remove my contact details first.", "Ja, men ta bort mina kontaktuppgifter först.",
    "Yes, only if my email address is excluded.", "Sure, send it to another advisor instead.",
    "Please send it tomorrow.", "Yes, can you explain what will be sent?",
    "Ja tack, kan du ändra mottagaren?", "Yes. Please remove my name first.",
    "The report says yes, please send it.", "What happens if I say yes?",
])
def test_conditions_questions_and_reported_words_are_not_consent(router, reply):
    assert not router.is_confirmation(reply)


def _actual_handoff_pipeline(router, monkeypatch, *, language="en"):
    from tests.unit.test_agent_router_expert_handoff import import_agent_router_module
    module = import_agent_router_module()
    pipeline = module.AgentRouter()
    pipeline.router = router
    router.classify_question = lambda *args, **kwargs: "generic"
    monkeypatch.setenv("EVALUATION_MODE", "true")
    monkeypatch.setattr(module, "handoff_response_language", lambda *args: language)
    monkeypatch.setattr(module, "prepare_expert_handoff", lambda *args: {
        "enabled": True, "recipient": "advisor@example.com", "cc": None,
        "request_id": "current-preview", "summary": "Summary to copy", "language": language,
    })
    return module, pipeline


@pytest.mark.parametrize("language,initial_message,confirmation", [
    ("en", "Can I talk to an EKR expert about this building?", "Sure, please send it."),
    ("sv", "Jag vill att en energirådgivare får en sammanfattning av samtalet. Visa vad som skulle skickas.", "Ja tack, skicka det."),
    ("sv", "Visa en sammanfattning som kan skickas till en energirådgivare, med mottagaren tydligt angiven.", "Ja tack, skicka det."),
])
def test_exact_saved_handoff_requests_preview_then_simulate(router, monkeypatch, language, initial_message, confirmation):
    from tests.unit.test_agent_router_expert_handoff import EmailRecorder
    _, pipeline = _actual_handoff_pipeline(router, monkeypatch, language=language)
    messages = [{"role": "user", "content": initial_message}]
    preview, metadata = pipeline.route_message(messages, initial_message, {}, "actual-saved-handoff")
    assert preview["route"] == "expert_handoff"
    assert "advisor@example.com" in preview["content"]
    assert metadata["expert_handoff_status"] == "pending_confirmation"
    assert not metadata["expert_handoff_sent"]
    assert EmailRecorder.calls == []
    messages.extend([preview, {"role": "user", "content": confirmation}])
    response, metadata = pipeline.route_message(messages, confirmation, metadata, "actual-saved-handoff")
    assert response["route"] == "expert_handoff"
    assert metadata["expert_handoff_status"] == "simulated"
    assert metadata["expert_handoff_simulated"] is True
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert not metadata["expert_handoff_sent"]
    assert EmailRecorder.calls == []


@pytest.mark.parametrize("reply", [
    "Nej tack, avbryt. Skicka ingenting.", "No, do not send anything.",
    "Yes. Cancel the handoff.", "Ja, men skicka inte mejlet.",
])
def test_real_cancellation_reaches_explicit_cancelled_state(router, monkeypatch, reply):
    from tests.unit.test_agent_router_expert_handoff import pending_metadata, EmailRecorder
    _, pipeline = _actual_handoff_pipeline(router, monkeypatch)
    response, updated = pipeline.route_message(
        [{"role": "user", "content": reply}], reply, pending_metadata(), "actual-cancellation",
    )
    assert response["route"] == "expert_handoff"
    assert updated["expert_handoff_status"] == "cancelled"
    assert updated["expert_handoff_cancelled"] is True
    assert updated["expert_handoff_pending_confirmation"] is False
    assert updated["expert_handoff_confirmed"] is False
    assert updated["expert_handoff_simulated"] is False
    assert EmailRecorder.calls == []


@pytest.mark.parametrize("language,changed", [
    ("sv", "Ja, men ta bort mina kontaktuppgifter först."),
    ("en", "Yes, but remove my contact details first."),
    ("en", "Send it to a different advisor instead."),
    ("en", "Yes. Send it without my email address."),
    ("sv", "Ja, skicka det utan mina kontaktuppgifter."),
])
def test_changed_terms_invalidate_preview_and_require_new_separate_consent(router, monkeypatch, language, changed):
    from tests.unit.test_agent_router_expert_handoff import pending_metadata, EmailRecorder
    _, pipeline = _actual_handoff_pipeline(router, monkeypatch, language=language)
    metadata = pending_metadata(expert_handoff_language=language)
    metadata["expert_handoff_preview"]["language"] = language
    messages = [{"role": "user", "content": changed}]
    response, updated = pipeline.route_message(messages, changed, metadata, "changed-preview")
    assert updated["expert_handoff_status"] == "changes_requested"
    assert updated["expert_handoff_preview"] is None
    assert updated["expert_handoff_confirmed"] is False
    assert not updated["expert_handoff_sent"]
    assert ("kan inte" if language == "sv" else "cannot") in response["content"]
    assert EmailRecorder.calls == []
    reply = "Ja tack, skicka det." if language == "sv" else "Sure, please send it."
    messages.extend([response, {"role": "user", "content": reply}])
    refreshed, updated = pipeline.route_message(messages, reply, updated, "changed-preview")
    assert updated["expert_handoff_status"] == "pending_confirmation"
    assert updated["expert_handoff_simulated"] is False
    assert ("inte tillämpats" if language == "sv" else "not been applied") in refreshed["content"]
    assert EmailRecorder.calls == []
    cancel = "Avbryt." if language == "sv" else "Cancel."
    messages.extend([refreshed, {"role": "user", "content": cancel}])
    _, updated = pipeline.route_message(messages, cancel, updated, "changed-preview")
    assert updated["expert_handoff_status"] == "cancelled"
    assert EmailRecorder.calls == []


@pytest.mark.parametrize("message,scope", [
    ("Bara sammanfattningen.", "summary_only"),
    ("Ja, men skicka endast sammanfattningen.", "summary_only"),
    ("Kan du skicka utan hela chatten?", "summary_only"),
    ("Skicka inte hela samtalet, bara sammanfattningen.", "summary_only"),
    ("Only the summary.", "summary_only"),
    ("Please send just a summary instead.", "summary_only"),
    ("Do not send the full chat, only the summary.", "summary_only"),
    ("Send the summary without the full conversation.", "summary_only"),
    ("Don't share the full conversation.", "summary_only"),
    ("Dela inte hela samtalet.", "summary_only"),
    ("Hela samtalet.", "full_conversation"),
    ("Please share the entire conversation.", "full_conversation"),
    ("Inte bara sammanfattningen; hela samtalet.", "full_conversation"),
    ("Not only the summary, include the full chat.", "full_conversation"),
])
def test_explicit_sharing_scope_requires_preview_not_consent(router, message, scope):
    assert router.requested_handoff_scope(message) == scope
    assert not router.is_confirmation(message)
    assert not router.is_rejection(message)
    assert not router.wants_handoff_preview_change(message)


@pytest.mark.parametrize("message", [
    "Inte bara sammanfattningen.",
    "Only the summary and the full conversation.",
    "Bara sammanfattningen och hela samtalet.",
    "What would the full conversation attachment contain?",
    "Vad innehåller hela samtalsbilagan?",
    "Please write a report from the full conversation.",
    "Create a board report and include the full conversation.",
    "Sammanfatta hela samtalet.",
    "Yes, only if the summary removes my email address.",
    "The report says only the summary should be sent.",
])
def test_ambiguous_descriptive_or_conditional_scope_is_not_consent(router, message):
    assert router.requested_handoff_scope(message) is None
    assert not router.is_confirmation(message)


@pytest.mark.parametrize("message", [
    "Only the summary. Cancel the handoff.",
    "Bara sammanfattningen. Avbryt.",
    "Nej, skicka inte hela chatten, bara sammanfattningen.",
    "Do not send anything, only the summary.",
    "Skicka inget, bara sammanfattningen.",
    "Don't share anything, only the summary.",
    "Dela ingenting, bara sammanfattningen.",
])
def test_scope_words_cannot_override_cancellation(router, message):
    assert router.is_rejection(message)
    assert not router.is_confirmation(message)


@pytest.mark.parametrize("message", [
    "Only the summary, without my email address.",
    "Ja, bara sammanfattningen men ta bort mina kontaktuppgifter.",
    "Send only the summary to a different advisor.",
    "Yes, send only the summary to other@example.com.",
    "Skicka bara sammanfattningen, inga personuppgifter.",
])
def test_scope_change_does_not_apply_unsupported_redaction_or_recipient(router, message):
    assert router.requested_handoff_scope(message) == "summary_only"
    assert router.wants_handoff_preview_change(message)
    assert not router.is_confirmation(message)


@pytest.mark.parametrize("language,initial", [
    ("sv", "Skicka bara sammanfattningen till en energirådgivare."),
    ("en", "Please send only the summary to an energy advisor."),
    ("sv", "Skicka inte hela samtalet, bara sammanfattningen till rådgivaren."),
    ("en", "Do not send the full chat, only the summary to the energy advisor."),
])
def test_initial_summary_scope_is_persisted_and_disclosed_before_separate_approval(router, monkeypatch, language, initial):
    from tests.unit.test_agent_router_expert_handoff import EmailRecorder
    _, pipeline = _actual_handoff_pipeline(router, monkeypatch, language=language)
    messages = [{"role": "user", "content": initial}]
    response, metadata = pipeline.route_message(messages, initial, {}, "initial-summary-scope")
    assert response["route"] == "expert_handoff"
    assert metadata["expert_handoff_scope"] == "summary_only"
    assert metadata["expert_handoff_preview"]["sharing_scope"] == "summary_only"
    assert metadata["expert_handoff_status"] == "pending_confirmation"
    assert ("endast den exakta sammanfattningen" if language == "sv" else "Only the exact summary") in response["content"]
    assert not metadata["expert_handoff_confirmed"]
    assert EmailRecorder.calls == []
    approval = "Ja, skicka." if language == "sv" else "Yes, send it."
    messages.extend([response, {"role": "user", "content": approval}])
    _, metadata = pipeline.route_message(messages, approval, metadata, "initial-summary-scope")
    assert metadata["expert_handoff_status"] == "simulated"
    assert EmailRecorder.calls == []


@pytest.mark.parametrize("old_scope,new_scope,scope_message", [
    ("full_conversation", "summary_only", "Ja, men bara sammanfattningen."),
    ("full_conversation", "summary_only", "Do not send the full chat, only the summary."),
    ("summary_only", "summary_only", "Yes, only the summary."),
    ("summary_only", "full_conversation", "Please share the full conversation."),
])
def test_pending_scope_request_refreshes_source_preview_and_cannot_send(router, monkeypatch, old_scope, new_scope, scope_message):
    from tests.unit.test_agent_router_expert_handoff import EmailRecorder
    module, pipeline = _actual_handoff_pipeline(router, monkeypatch)
    source = [{"role": "user", "content": "I need an expert."}]
    preview = {
        "enabled": True, "recipient": "advisor@example.com", "cc": "user@example.com",
        "request_id": "old-scope-preview", "summary": "Summary to copy", "language": "en",
        "sharing_scope": old_scope, "message_count": len(source),
    }
    metadata = {
        "expert_handoff_pending_confirmation": True,
        "expert_handoff_scope": old_scope, "expert_handoff_preview": preview,
        "expert_handoff_confirmed": True,
    }
    calls = []

    def prepare(thread_id, messages, context):
        calls.append((messages, context))
        return {**preview, "request_id": "new-scope-preview", "sharing_scope": context["expert_handoff_scope"]}

    monkeypatch.setattr(module, "prepare_expert_handoff", prepare)
    messages = source + [{"role": "assistant", "content": "Preview"}, {"role": "user", "content": scope_message}]
    response, updated = pipeline.route_message(messages, scope_message, metadata, "scope-change-sequence")
    assert response["route"] == "expert_handoff"
    assert updated["expert_handoff_scope"] == new_scope
    assert updated["expert_handoff_status"] == "pending_confirmation"
    assert not updated["expert_handoff_confirmed"]
    assert updated["expert_handoff_preview"]["request_id"] == "new-scope-preview"
    assert calls[0][0] == source
    assert calls[0][1]["expert_handoff_preview"] == preview
    assert "separate reply" in response["content"]
    assert EmailRecorder.calls == []
    messages.extend([response, {"role": "user", "content": "Yes, send it."}])
    _, updated = pipeline.route_message(messages, "Yes, send it.", updated, "scope-change-sequence")
    assert updated["expert_handoff_status"] == "simulated"
    assert EmailRecorder.calls == []


def test_scope_change_does_not_hide_an_intervening_substantive_user_turn(router, monkeypatch):
    from tests.unit.test_agent_router_expert_handoff import pending_metadata
    module, pipeline = _actual_handoff_pipeline(router, monkeypatch)
    captured = []
    monkeypatch.setattr(module, "prepare_expert_handoff", lambda thread_id, messages, metadata: (
        captured.append(messages) or {"enabled": True, "recipient": "advisor@example.com", "summary": "Summary", "language": "en"}
    ))
    metadata = pending_metadata(expert_handoff_scope="full_conversation")
    metadata["expert_handoff_preview"]["message_count"] = 1
    messages = [
        {"role": "user", "content": "I need an expert."},
        {"role": "assistant", "content": "Preview"},
        {"role": "user", "content": "The budget is now 20000 SEK."},
        {"role": "user", "content": "Only the summary."},
    ]
    pipeline.route_message(messages, "Only the summary.", metadata, "scope-with-new-details")
    assert captured == [messages]


@pytest.mark.parametrize("message", [
    "Skicka bara sammanfattningen. Budgeten är nu 20 000 kr.",
    "Send only the summary. Our heating is district heating, not a heat pump.",
    "Skicka bara sammanfattningen. Rättelse: vi har fjärrvärme.",
])
def test_scope_with_new_facts_regenerates_from_full_source(router, monkeypatch, message):
    from tests.unit.test_agent_router_expert_handoff import pending_metadata, EmailRecorder
    module, pipeline = _actual_handoff_pipeline(router, monkeypatch)
    captured = []
    monkeypatch.setattr(module, "prepare_expert_handoff", lambda thread_id, messages, metadata: (
        captured.append(messages) or {"enabled": True, "recipient": "advisor@example.com", "summary": "Updated summary", "language": "en"}
    ))
    metadata = pending_metadata(expert_handoff_scope="full_conversation")
    metadata["expert_handoff_preview"]["message_count"] = 1
    messages = [
        {"role": "user", "content": "I need an expert."},
        {"role": "assistant", "content": "Preview"},
        {"role": "user", "content": message},
    ]
    assert router.requested_handoff_scope(message) == "summary_only"
    assert not router.is_handoff_scope_only_request(message)
    _, updated = pipeline.route_message(messages, message, metadata, "scope-with-fact-correction")
    assert captured == [messages]
    assert updated["expert_handoff_status"] == "pending_confirmation"
    assert not updated["expert_handoff_confirmed"]
    assert EmailRecorder.calls == []


@pytest.mark.parametrize("message", [
    "Yes, send only the summary to other@example.com.",
    "Skicka bara sammanfattningen, inga personuppgifter.",
])
def test_mixed_scope_recipient_or_redaction_is_explicitly_guarded(router, monkeypatch, message):
    from tests.unit.test_agent_router_expert_handoff import pending_metadata, EmailRecorder
    module, pipeline = _actual_handoff_pipeline(router, monkeypatch)
    monkeypatch.setattr(module, "prepare_expert_handoff", lambda *args: pytest.fail("Unsupported changes must not prepare an unchanged scope preview"))
    response, updated = pipeline.route_message(
        [{"role": "user", "content": message}], message,
        pending_metadata(expert_handoff_scope="full_conversation"), "mixed-scope-change",
    )
    assert updated["expert_handoff_status"] == "changes_requested"
    assert updated["expert_handoff_preview"] is None
    assert updated["expert_handoff_scope"] == "summary_only"
    assert updated["expert_handoff_requested_changes"] == message
    assert "cannot" in response["content"]
    assert EmailRecorder.calls == []


@pytest.mark.parametrize("language,message", [
    ("en", "Please send only the summary to other@example.com."),
    ("en", "Send only the summary to an energy advisor at other@example.com."),
    ("sv", "Skicka bara sammanfattningen till annan@example.com."),
    ("sv", "Skicka bara sammanfattningen till rådgivaren, inga personuppgifter."),
    ("en", "Send only the summary to the energy advisor without my email address."),
])
def test_initial_handoff_discloses_unapplied_recipient_and_redaction_conditions(router, monkeypatch, language, message):
    from tests.unit.test_agent_router_expert_handoff import EmailRecorder
    _, pipeline = _actual_handoff_pipeline(router, monkeypatch, language=language)
    response, metadata = pipeline.route_message(
        [{"role": "user", "content": message}], message, {}, "initial-guarded-handoff",
    )
    assert response["route"] == "expert_handoff"
    assert metadata["expert_handoff_requested_changes"] == message
    assert metadata["expert_handoff_scope"] == "summary_only"
    assert metadata["expert_handoff_status"] == "pending_confirmation"
    assert ("inte tillämpats" if language == "sv" else "not been applied") in response["content"]
    assert "advisor@example.com" in response["content"]
    assert not metadata["expert_handoff_confirmed"]
    assert EmailRecorder.calls == []
