from email.mime.multipart import MIMEMultipart
from unittest.mock import patch
import sys
import types
import pytest

from tests.support import fresh_import


@pytest.fixture(autouse=True)
def isolate_external_services(monkeypatch):
    """Unit tests must not read a developer's .env or reach Redis/SMTP."""
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))
    monkeypatch.setitem(sys.modules, "redis", types.SimpleNamespace(StrictRedis=lambda *args, **kwargs: MemoryStore()))
    for key, value in {
        "EXPERT_EMAIL_SMTP_SERVER": "smtp.example.com",
        "EXPERT_EMAIL_SMTP_PORT": "587",
        "EXPERT_EMAIL_ADDRESS": "service@example.com",
        "EXPERT_EMAIL_ACCOUNT": "service@example.com",
        "EXPERT_EMAIL_PASSWORD": "test-only",
        "EXPERT_EMAIL_RECIPIENT": "advisor@example.com",
        "REDIS_PORT": "6379",
    }.items():
        monkeypatch.setenv(key, value)

    def unexpected_smtp(*args, **kwargs):
        pytest.fail("A handoff unit test attempted to use an unstubbed SMTP connection")

    monkeypatch.setattr("smtplib.SMTP", unexpected_smtp)


def test_build_expert_handoff_transcript_keeps_json_shape_and_appends_metadata():
    module = fresh_import("src.services.expert_handoff_email")
    transcript = module.build_expert_handoff_transcript(
        thread_id="thread-1",
        messages=[
            {
                "role": "user",
                "timestamp": 1,
                "added_to_database": 0,
                "content": "I need help",
                "classification": "expert_handoff",
            },
            {
                "role": "assistant",
                "timestamp": 2,
                "added_to_database": 0,
                "content": "I can help",
                "agent_answered": "expert_handoff",
            },
        ],
        metadata={"address": ["Main Street 1"], "building_information": [{"name": "A"}]},
    )

    assert "SPARA expert handoff transcript" in transcript
    assert '"role": "user"' in transcript
    assert '"classification": "expert_handoff"' in transcript
    assert '"agent_answered": "expert_handoff"' in transcript
    assert '"address"' in transcript
    assert '"Main Street 1"' in transcript
    assert "added_to_database" not in transcript
    assert '"timestamp"' not in transcript


def test_build_expert_handoff_email_contains_summary_and_attachment():
    module = fresh_import("src.services.expert_handoff_email")
    with patch.object(
        module,
        "generate_expert_handoff_summary",
        return_value=(
            "Reason for handoff:\n"
            "- The user requested expert support.\n\n"
            "Why human follow-up is needed:\n"
            "- The chatbot could not complete the case."
        ),
    ):
        message = module.build_expert_handoff_email(
        thread_id="thread-1",
        messages=[
            {"role": "user", "timestamp": 1, "added_to_database": 0, "content": "I need help"},
            {"role": "assistant", "timestamp": 2, "added_to_database": 0, "content": "I can help"},
        ],
        metadata={
            "address": ["Main Street 1"],
            "building_information": [{"name": "A"}],
            "user_email": "User@Example.com",
        },
        )

    assert isinstance(message, MIMEMultipart)
    assert "thread-1" in message["Subject"]
    assert message["Cc"] == "user@example.com"
    assert len(message.get_payload()) == 2

    body_part = message.get_payload()[0]
    attachment_part = message.get_payload()[1]

    body_text = body_part.get_payload(decode=True).decode("utf-8")
    attachment_text = attachment_part.get_payload(decode=True).decode("utf-8")

    assert "Reason for handoff" in body_text
    assert "The full conversation transcript is attached as a text file." in body_text
    assert attachment_part.get_filename() == "spara_handoff_thread-1.txt"
    assert '"role": "user"' in attachment_text
    assert '"Main Street 1"' in attachment_text
    assert "added_to_database" not in attachment_text
    assert '"timestamp"' not in attachment_text


def test_build_expert_handoff_email_reads_recipient_from_environment(monkeypatch):
    monkeypatch.setenv("EXPERT_EMAIL_RECIPIENT", "advisor@example.com")
    module = fresh_import("src.services.expert_handoff_email")

    message = module.build_expert_handoff_email(
        thread_id="thread-1",
        messages=[{"role": "user", "content": "I need help"}],
        metadata={},
        summary_override="Summary",
    )

    assert message["To"] == "advisor@example.com"


def test_send_expert_handoff_email_includes_user_cc_in_smtp_recipients(service):
    module = service

    class FakeSMTP:
        recipients = None

        def __init__(self, server, port, timeout):
            self.server = server
            self.port = port

        def ehlo(self):
            return None

        def starttls(self):
            return None

        def login(self, account, password):
            return None

        def sendmail(self, from_address, recipients, raw_message):
            FakeSMTP.recipients = recipients

        def quit(self):
            return None

    with patch.object(module.smtplib, "SMTP", FakeSMTP):
        messages = [{"role": "user", "content": "I need help"}]
        metadata = {"expert_handoff_confirmed": True}
        metadata["expert_handoff_preview"] = module.prepare_expert_handoff("user@example.com:thread-2", messages, metadata)
        result = module.send_expert_handoff_email("user@example.com:thread-2", messages, metadata)
        assert result["status"] == "submitted"

    assert "user@example.com" in FakeSMTP.recipients


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


class RecordingSMTP:
    calls = []
    refused = {}
    send_error = None
    quit_error = None

    def __init__(self, server, port, timeout):
        self.timeout = timeout

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, account, password):
        pass

    def sendmail(self, sender, recipients, content):
        self.calls.append((sender, recipients, content))
        if self.send_error:
            raise self.send_error
        return self.refused

    def quit(self):
        if self.quit_error:
            raise self.quit_error

    def close(self):
        pass


@pytest.fixture
def service(monkeypatch):
    module = fresh_import("src.services.expert_handoff_email")
    monkeypatch.setenv("EXPERT_EMAIL_ENABLED", "true")
    for key, value in {
        "SMTP_SERVER": "smtp.example.com", "EMAIL_ACCOUNT": "service@example.com",
        "EMAIL_ADDRESS": "service@example.com", "EMAIL_PASSWORD": "test-only",
        "EMAIL_RECIPIENT": "advisor@example.com",
    }.items():
        monkeypatch.setattr(module, key, value)
    monkeypatch.setattr(module, "_handoff_store", MemoryStore())
    monkeypatch.setattr(module, "generate_expert_handoff_summary", lambda *args: "Previewed summary")
    RecordingSMTP.calls = []
    RecordingSMTP.refused = {}
    RecordingSMTP.send_error = None
    RecordingSMTP.quit_error = None
    monkeypatch.setattr(module.smtplib, "SMTP", RecordingSMTP)
    return module


def confirmed_case(service):
    messages = [{"role": "user", "content": "I need advice about uneven temperatures"}]
    metadata = {"building_id": "BUILDING-1", "user_email": "user@example.com"}
    metadata["expert_handoff_preview"] = service.prepare_expert_handoff("thread-1", messages, metadata)
    metadata["expert_handoff_confirmed"] = True
    return messages, metadata


def test_confirmation_and_preview_are_required_by_sender(service):
    result = service.send_expert_handoff_email("thread-1", [], {})
    assert result["status"] == "failed"
    assert RecordingSMTP.calls == []


def test_recipient_changes_invalidate_consent(service, monkeypatch):
    messages, metadata = confirmed_case(service)
    monkeypatch.setattr(service, "EMAIL_RECIPIENT", "different@example.com")
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "failed"
    assert RecordingSMTP.calls == []


def test_expired_preview_cannot_be_sent(service):
    messages, metadata = confirmed_case(service)
    metadata["expert_handoff_preview"]["expires_at"] = 0
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "failed"
    assert RecordingSMTP.calls == []


def test_changed_building_context_requires_new_preview(service):
    messages, metadata = confirmed_case(service)
    metadata["building_id"] = "BUILDING-2"
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "failed"
    assert RecordingSMTP.calls == []


def test_changed_cc_requires_new_preview(service):
    messages, metadata = confirmed_case(service)
    metadata["user_email"] = "different@example.com"
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "failed"
    assert RecordingSMTP.calls == []


def test_edited_previewed_conversation_requires_new_preview(service):
    messages, metadata = confirmed_case(service)
    messages[0]["content"] = "Replace the original request with a different concern"
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "failed"
    assert RecordingSMTP.calls == []


def test_disabled_configuration_after_preview_prevents_submission(service, monkeypatch):
    messages, metadata = confirmed_case(service)
    monkeypatch.setenv("EXPERT_EMAIL_ENABLED", "false")
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "failed"
    assert RecordingSMTP.calls == []


def test_inflight_duplicate_is_uncertain_without_a_second_submission(service, monkeypatch):
    messages, metadata = confirmed_case(service)
    duplicates = []

    def send_with_concurrent_retry(smtp, sender, recipients, content):
        RecordingSMTP.calls.append((sender, recipients, content))
        duplicates.append(service.send_expert_handoff_email("thread-1", messages, metadata))
        return {}

    monkeypatch.setattr(RecordingSMTP, "sendmail", send_with_concurrent_retry)
    result = service.send_expert_handoff_email("thread-1", messages, metadata)
    assert result["status"] == "submitted"
    assert len(duplicates) == 1
    # The service retains the claim state; AgentRouter exposes it as unknown.
    assert duplicates[0]["status"] == "sending"
    assert duplicates[0]["duplicate"] is True
    assert len(RecordingSMTP.calls) == 1


def test_lost_submission_result_does_not_release_duplicate_guard(service, monkeypatch):
    messages, metadata = confirmed_case(service)

    def unavailable(*args, **kwargs):
        raise ConnectionError("result storage unavailable after SMTP acceptance")

    monkeypatch.setattr(service._handoff_store, "setex", unavailable)
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "submitted"
    retry = service.send_expert_handoff_email("thread-1", messages, metadata)
    assert retry["status"] == "sending"
    assert retry["duplicate"] is True
    assert len(RecordingSMTP.calls) == 1


def test_queue_telemetry_updates_do_not_invalidate_preview(service):
    messages, metadata = confirmed_case(service)
    metadata.update(last_user_message="yes", telemetry={"latency_ms": 10})
    assert service.expert_handoff_preview_matches("thread-1", messages, metadata)


def test_persistence_ids_and_versions_do_not_invalidate_preview(service):
    messages, metadata = confirmed_case(service)
    messages[0].update(message_id=12, version=2, timestamp=123, added_to_database=1)
    assert service.expert_handoff_preview_matches("thread-1", messages, metadata)


def test_public_metadata_rehydration_does_not_invalidate_preview(service):
    messages = [{"role": "user", "content": "Please contact an advisor"}]
    metadata = {"aggregated_data": {"oden": {"byggnadsid": "BUILDING-1", "energy_class": "D"}}}
    metadata["expert_handoff_preview"] = service.prepare_expert_handoff("thread-1", messages, metadata)
    metadata.update({
        "route": "expert_handoff", "agent": "expert_handoff", "classification": "expert_handoff",
        "expert_handoff_triggered": True, "evaluation_mode": False, "report_generation_triggered": False,
        "building_id": "BUILDING-1", "retrieved_facts": {"byggnadsid": "BUILDING-1", "energy_class": "D"},
        "building_match": {"building_id": "BUILDING-1", "match_confidence": "high"},
        "clarification": {"needed": False}, "model_config": {"router": "test-model"},
    })
    assert service.expert_handoff_preview_matches("thread-1", messages, metadata)


def test_repeat_submission_is_deduplicated_across_service_calls(service):
    messages, metadata = confirmed_case(service)
    first = service.send_expert_handoff_email("thread-1", messages, metadata)
    second = service.send_expert_handoff_email("thread-1", messages, metadata)
    assert first["status"] == second["status"] == "submitted"
    assert second["duplicate"] is True
    assert len(RecordingSMTP.calls) == 1


def test_only_previewed_conversation_and_summary_are_sent(service, monkeypatch):
    messages, metadata = confirmed_case(service)
    messages.append({"role": "user", "content": "yes-confirmation-not-in-preview"})
    monkeypatch.setattr(service, "generate_expert_handoff_summary", lambda *args: pytest.fail("must not regenerate consented summary"))
    with patch.object(service, "build_expert_handoff_email", wraps=service.build_expert_handoff_email) as build:
        assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "submitted"
    assert len(build.call_args.args[1]) == 1
    assert build.call_args.kwargs["summary_override"] == "Previewed summary"


def test_refused_advisor_is_failure_even_if_cc_accepted(service):
    messages, metadata = confirmed_case(service)
    RecordingSMTP.refused = {"advisor@example.com": (550, b"rejected")}
    result = service.send_expert_handoff_email("thread-1", messages, metadata)
    assert result["status"] == "failed"
    assert result["cc_submitted"] is True


def test_refused_cc_does_not_claim_user_copy_was_sent(service):
    messages, metadata = confirmed_case(service)
    RecordingSMTP.refused = {"user@example.com": (550, b"rejected")}
    result = service.send_expert_handoff_email("thread-1", messages, metadata)
    assert result["status"] == "submitted"
    assert result["cc_submitted"] is False


def test_quit_failure_does_not_undo_smtp_acceptance(service):
    messages, metadata = confirmed_case(service)
    RecordingSMTP.quit_error = OSError("connection already closed")
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "submitted"


def test_ambiguous_send_failure_is_not_automatically_retried(service):
    messages, metadata = confirmed_case(service)
    RecordingSMTP.send_error = TimeoutError("connection lost after DATA")
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "unknown"
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "unknown"
    assert len(RecordingSMTP.calls) == 1


def test_missing_deduplication_store_prevents_sending(service, monkeypatch):
    messages, metadata = confirmed_case(service)
    def unavailable(*args, **kwargs):
        raise ConnectionError("redis unavailable")
    monkeypatch.setattr(service._handoff_store, "set", unavailable)
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "failed"
    assert RecordingSMTP.calls == []


def test_disabled_email_still_provides_copyable_context(service, monkeypatch):
    monkeypatch.setenv("EXPERT_EMAIL_ENABLED", "false")
    prepared = service.prepare_expert_handoff(
        "thread-1", [{"role": "user", "content": "Some flats are cold"}],
        {"building_id": "BUILDING-1"},
    )
    assert prepared["enabled"] is False
    assert "Some flats are cold" in prepared["summary"]
    assert "BUILDING-1" in prepared["summary"]


def test_transcript_does_not_embed_internal_preview_or_runtime_diagnostics(service):
    text = service.build_expert_handoff_transcript("thread-1", [], {
        "building_id": "BUILDING-1", "expert_handoff_preview": {"summary": "private preview"},
        "last_user_message": "yes", "telemetry": {"latency_ms": 10},
    })
    assert "BUILDING-1" in text
    assert "expert_handoff_preview" not in text
    assert "telemetry" not in text


@pytest.mark.parametrize("model_response", ["  Error: unavailable", "No content returned", "No content returned from the model."])
def test_invalid_model_summary_uses_copyable_fallback(monkeypatch, model_response):
    module = fresh_import("src.services.expert_handoff_email")
    class EmptySummaryAgent:
        def __init__(self, prompt_path):
            pass

        def generate_response(self, prompt, message_list):
            return model_response
    monkeypatch.setitem(sys.modules, "src.agents.openai_agent", types.SimpleNamespace(OpenAIResponseAgent=EmptySummaryAgent))
    summary = module.generate_expert_handoff_summary(
        "thread-fallback", [{"role": "user", "content": "Residents report cold flats"}], {},
    )
    assert "Summary to copy" in summary
    assert "Residents report cold flats" in summary
    assert model_response not in summary
