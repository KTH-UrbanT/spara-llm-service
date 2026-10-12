from email.mime.multipart import MIMEMultipart
from email import message_from_string
from email.policy import default
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


def test_handoff_generation_receives_identity_evidence_without_recipient_prose(monkeypatch):
    module = fresh_import('src.services.expert_handoff_email')
    captured = []
    class Summarizer:
        def __init__(self, **kwargs):
            pass
        def generate_response(self, prompt, **kwargs):
            captured.append(prompt)
            return 'Sammanfattning av det verifierade underlaget.'
    monkeypatch.setitem(sys.modules, 'src.agents.openai_agent', types.SimpleNamespace(OpenAIResponseAgent=Summarizer))
    building_id = '01-80-SKYTTEN2-2'
    metadata = {
        'byggnadsid': building_id, 'address': 'Artemisgatan 17',
        'building_identity_check': {'status': 'passed', 'matched_building_id': building_id},
        'address_identity_evidence': {'selected_building_id': building_id, 'address_lookups': [{'searched_address': 'Artemisgatan 17', 'returned_building_ids': [building_id]}]},
        'retrieved_facts': {'byggnadsid': building_id, 'energy_class': 'G'},
    }
    module.generate_expert_handoff_summary('thread', [{'role': 'user', 'content': 'Visa en sammanfattning.'}], metadata)
    assert 'address_identity_evidence' in captured[0]
    assert 'building_identity_check' in captured[0]
    assert 'recorded_facts' in captured[0]
    assert 'advisor@example.com' not in captured[0]
    assert '"sharing_scope": "full_conversation"' in captured[0]


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
    monkeypatch.setattr(module, "generate_expert_handoff_summary", lambda *args: "Heating case summary")
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
    assert build.call_args.kwargs["summary_override"] == "Heating case summary"


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


def test_handoff_summary_is_filtered_before_preview_and_used_without_changes(monkeypatch):
    class SummaryAgent:
        def __init__(self, prompt_path):
            pass

        def generate_response(self, prompt, message_list, *, language=None):
            assert language == "sv"
            return "Se [påhittad rapport](https://invented.example/report.pdf)."
    monkeypatch.setitem(sys.modules, "src.agents.openai_agent", types.SimpleNamespace(OpenAIResponseAgent=SummaryAgent))
    module = fresh_import("src.services.expert_handoff_email")
    messages = [{"role": "user", "content": "Jag vill prata med en rådgivare"}]
    preview = module.prepare_expert_handoff("checked-handoff", messages, {})
    assert "invented.example" not in preview["summary"]
    assert "utelämnat länkar" in preview["summary"]
    metadata = {"expert_handoff_preview": preview}
    assert module.expert_handoff_preview_matches("checked-handoff", messages, metadata)
    email = module.build_expert_handoff_email("checked-handoff", messages, metadata, summary_override=preview["summary"])
    body = email.get_payload()[0].get_payload(decode=True).decode("utf-8")
    assert preview["summary"] in body


@pytest.mark.parametrize("model_response", ["  Error: unavailable", "No content returned", "No content returned from the model."])
def test_invalid_model_summary_uses_copyable_fallback(monkeypatch, model_response):
    module = fresh_import("src.services.expert_handoff_email")
    class EmptySummaryAgent:
        def __init__(self, prompt_path):
            pass

        def generate_response(self, prompt, message_list, *, language=None):
            assert language == "en"
            return model_response
    monkeypatch.setitem(sys.modules, "src.agents.openai_agent", types.SimpleNamespace(OpenAIResponseAgent=EmptySummaryAgent))
    summary = module.generate_expert_handoff_summary(
        "thread-fallback", [{"role": "user", "content": "Residents report cold flats"}], {},
    )
    assert "Summary to copy" in summary
    assert "Residents report cold flats" in summary
    assert model_response not in summary


def test_preview_explicitly_defaults_to_full_conversation(service):
    messages, metadata = confirmed_case(service)
    assert metadata["expert_handoff_preview"]["sharing_scope"] == "full_conversation"
    assert service.expert_handoff_preview_matches("thread-1", messages, metadata)


@pytest.mark.parametrize("scope", ["summary_only", "full_conversation"])
def test_approved_scope_and_summary_are_bound_to_preview_digest(service, scope):
    messages = [{"role": "user", "content": "Help with heating"}]
    metadata = {"expert_handoff_scope": scope}
    preview = service.prepare_expert_handoff("thread-scope", messages, metadata)
    metadata["expert_handoff_preview"] = preview
    metadata["expert_handoff_confirmed"] = True
    assert preview["sharing_scope"] == scope
    assert service.expert_handoff_preview_matches("thread-scope", messages, metadata)
    preview["summary"] = "A summary the user never reviewed"
    assert service.send_expert_handoff_email("thread-scope", messages, metadata)["error"] == "confirmation_required"
    assert RecordingSMTP.calls == []


@pytest.mark.parametrize("field,value", [
    ("sharing_scope", "summary_only"),
    ("request_id", "a-new-request-without-new-consent"),
    ("language", "sv"),
    ("summary", ""),
    ("summary", None),
    ("message_count", True),
    ("expires_at", float("inf")),
    ("expires_at", float("nan")),
])
def test_edited_or_malformed_preview_cannot_be_sent(service, field, value):
    messages, metadata = confirmed_case(service)
    metadata["expert_handoff_preview"][field] = value
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["error"] == "confirmation_required"
    assert RecordingSMTP.calls == []


@pytest.mark.parametrize("missing", ["sharing_scope", "summary", "source_context_keys", "payload_digest"])
def test_legacy_incomplete_preview_requires_new_preview(service, missing):
    messages, metadata = confirmed_case(service)
    del metadata["expert_handoff_preview"][missing]
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["error"] == "confirmation_required"
    assert RecordingSMTP.calls == []


def test_changed_current_sharing_scope_requires_new_preview_and_consent(service):
    messages, metadata = confirmed_case(service)
    metadata["expert_handoff_scope"] = "summary_only"
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["error"] == "confirmation_required"
    assert RecordingSMTP.calls == []


def test_preview_is_bound_to_originating_session(service):
    messages, metadata = confirmed_case(service)
    assert service.send_expert_handoff_email("different-thread", messages, metadata)["error"] == "confirmation_required"
    assert RecordingSMTP.calls == []


@pytest.mark.parametrize("field", ["retrieved_facts", "building_identity_check", "address_identity_evidence"])
def test_summary_source_evidence_mutation_requires_new_preview(service, field):
    messages = [{"role": "user", "content": "Help with heating"}]
    metadata = {"building_id": "BUILDING-1", field: {"evidence": "original"}}
    metadata["expert_handoff_preview"] = service.prepare_expert_handoff("thread-evidence", messages, metadata)
    metadata["expert_handoff_confirmed"] = True
    metadata[field]["evidence"] = "changed"
    assert service.send_expert_handoff_email("thread-evidence", messages, metadata)["error"] == "confirmation_required"
    assert RecordingSMTP.calls == []


def test_summary_only_sends_exact_approved_body_without_transcript_or_internal_id(service, monkeypatch):
    thread_id = "user@example.com:private-internal-session"
    messages = [
        {"role": "user", "content": "raw-user-message-marker"},
        {"role": "assistant", "content": "raw-assistant-message-marker"},
    ]
    metadata = {
        "expert_handoff_scope": "summary_only", "constraints": {"private": "raw-metadata-marker"},
    }
    preview = service.prepare_expert_handoff(thread_id, messages, metadata)
    metadata.update(expert_handoff_preview=preview, expert_handoff_confirmed=True)
    monkeypatch.setattr(service, "generate_expert_handoff_summary", lambda *args: pytest.fail("approved summary must not be regenerated"))
    monkeypatch.setattr(service, "build_expert_handoff_transcript", lambda *args: pytest.fail("summary-only sending must not build a transcript"))
    assert service.send_expert_handoff_email(thread_id, messages, metadata)["status"] == "submitted"
    outgoing = message_from_string(RecordingSMTP.calls[0][2], policy=default)
    assert outgoing["To"] == "advisor@example.com"
    assert outgoing["Cc"] == "user@example.com"
    assert thread_id not in str(outgoing["Subject"])
    assert "private-internal-session" not in str(outgoing["Subject"])
    assert len(outgoing.get_payload()) == 1
    assert list(outgoing.iter_attachments()) == []
    body = outgoing.get_body(preferencelist=("plain",)).get_content()
    assert preview["summary"] in body
    assert "no conversation attachment" in body or "ingen samtalsbilaga" in body
    for private in ("raw-user-message-marker", "raw-assistant-message-marker", "raw-metadata-marker", thread_id):
        assert private not in body


def test_full_conversation_still_sends_source_and_disclosed_cc(service):
    messages, metadata = confirmed_case(service)
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "submitted"
    outgoing = message_from_string(RecordingSMTP.calls[0][2], policy=default)
    attachments = list(outgoing.iter_attachments())
    assert "thread-1" in str(outgoing["Subject"])
    assert outgoing["Cc"] == "user@example.com"
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "spara_handoff_thread-1.txt"
    transcript = attachments[0].get_content()
    assert messages[0]["content"] in transcript
    assert "BUILDING-1" in transcript


@pytest.mark.parametrize("scope", ["summary_only", "full_conversation"])
def test_scope_preview_reuses_authenticated_summary_and_requires_new_confirmation(service, monkeypatch, scope):
    messages, metadata = confirmed_case(service)
    old_preview = metadata["expert_handoff_preview"]
    metadata["expert_handoff_scope"] = scope
    monkeypatch.setattr(service, "generate_expert_handoff_summary", lambda *args: pytest.fail("unchanged source must reuse approved summary"))
    new_preview = service.prepare_expert_handoff("thread-1", messages, metadata)
    assert new_preview["summary"] == old_preview["summary"]
    assert new_preview["request_id"] != old_preview["request_id"]
    assert new_preview["payload_digest"] != old_preview["payload_digest"]
    assert new_preview["sharing_scope"] == scope
    metadata.update(expert_handoff_preview=new_preview, expert_handoff_confirmed=False)
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["error"] == "confirmation_required"
    metadata["expert_handoff_confirmed"] = True
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["status"] == "submitted"
    assert service.send_expert_handoff_email("thread-1", messages, metadata)["duplicate"] is True
    assert len(RecordingSMTP.calls) == 1


@pytest.mark.parametrize("mutation", ["summary", "source"])
def test_scope_change_cannot_reuse_tampered_or_stale_summary(service, monkeypatch, mutation):
    messages, metadata = confirmed_case(service)
    if mutation == "summary":
        metadata["expert_handoff_preview"]["summary"] = "Tampered summary"
    else:
        messages[0]["content"] = "A changed source request"
    metadata["expert_handoff_scope"] = "summary_only"
    generated = []
    monkeypatch.setattr(service, "generate_expert_handoff_summary", lambda *args: generated.append(args) or "New preview summary")
    prepared = service.prepare_expert_handoff("thread-1", messages, metadata)
    assert generated
    assert prepared["summary"] == "New preview summary"


def test_summary_only_fallback_is_disclosed_text_not_anonymized_text(service, monkeypatch):
    # Summary-only controls attachments; it cannot promise to redact text the
    # deterministic fallback visibly includes and the user explicitly approves.
    monkeypatch.setattr(service, "generate_expert_handoff_summary", lambda thread, messages, metadata: service.copyable_expert_handoff_summary(messages, metadata))
    messages = [{"role": "user", "content": "My name is Resident Example and I need heating advice"}]
    metadata = {"expert_handoff_scope": "summary_only"}
    preview = service.prepare_expert_handoff("fallback-thread", messages, metadata)
    assert messages[0]["content"] in preview["summary"]
    metadata.update(expert_handoff_preview=preview, expert_handoff_confirmed=True)
    assert service.send_expert_handoff_email("fallback-thread", messages, metadata)["status"] == "submitted"
    outgoing = message_from_string(RecordingSMTP.calls[0][2], policy=default)
    assert list(outgoing.iter_attachments()) == []
    assert preview["summary"] in outgoing.get_body(preferencelist=("plain",)).get_content()


def test_summary_context_uses_current_scope_and_excludes_operational_history(monkeypatch):
    module = fresh_import("src.services.expert_handoff_email")
    captured = []

    class SummaryAgent:
        def __init__(self, **kwargs):
            pass

        def generate_response(self, prompt, **kwargs):
            captured.append(prompt)
            return "Användarens huvudsakliga behov:\nOjämn värme. Budgeten är nu 20 000 kr."

    monkeypatch.setitem(sys.modules, "src.agents.openai_agent", types.SimpleNamespace(OpenAIResponseAgent=SummaryAgent))
    messages = [
        {"role": "user", "content": "Vi har ojämn värme. Budgeten är 50 000 kr."},
        {"role": "assistant", "agent_answered": "expert_handoff", "content": "obsolete-preview-marker Budgeten är 50 000 kr."},
        {"role": "assistant", "content": "obsolete-status-marker", "metadata": {"route": "action_status"}},
        {"role": "assistant", "content": "Mät temperaturen i de kallaste lägenheterna."},
        {"role": "user", "content": "Skicka bara sammanfattningen. Rättelse: budgeten är nu 20 000 kr."},
    ]
    metadata = {
        "expert_handoff_scope": "summary_only",
        "expert_handoff_preview": {"sharing_scope": "full_conversation", "summary": "obsolete-internal-preview-marker"},
        "action_receipts": {"handoffs": [{"status": "submitted", "request_id": "obsolete-receipt-marker"}]},
    }
    summary = module.generate_expert_handoff_summary("private-thread-marker", messages, metadata)
    prompt = captured[0]
    assert '"sharing_scope": "summary_only"' in prompt
    assert "20 000 kr" in prompt
    assert "Mät temperaturen" in prompt
    for removed in ("obsolete-preview-marker", "obsolete-status-marker", "obsolete-internal-preview-marker", "obsolete-receipt-marker", "private-thread-marker"):
        assert removed not in prompt
    assert "20 000 kr" in summary


@pytest.mark.parametrize("scope,operational_claim", [
    ("full_conversation", "Endast sammanfattningen delas; ingen samtalsbilaga skickas."),
    ("summary_only", "Appen är fortfarande inställd på hela samtalet och behöver justeras."),
    ("summary_only", "Användaren har godkänt att detta skickas."),
    ("summary_only", "Mottagare: advisor@example.com."),
    ("summary_only", "The full conversation is attached and the user approved sending."),
    ("full_conversation", "Only the summary will be shared."),
    ("summary_only", "En rapport har skapats och levererats."),
    ("summary_only", "Sammanfattningen delas med rådgivaren."),
    ("summary_only", "The user approved this request."),
    ("summary_only", "Approved summary: permission has already been given."),
    ("summary_only", "Approved summary: uneven heating."),
    ("summary_only", "Godkänd sammanfattning: ojämn värme."),
    ("summary_only", "An attachment will accompany this message."),
    ("summary_only", "En bilaga följer med meddelandet."),
])
def test_generated_summary_cannot_restate_sharing_consent_or_execution(monkeypatch, scope, operational_claim):
    module = fresh_import("src.services.expert_handoff_email")

    class SummaryAgent:
        def __init__(self, **kwargs):
            pass

        def generate_response(self, *args, **kwargs):
            return (
                "Användarens huvudsakliga behov:\nOjämn värme. Budgeten är nu 20 000 kr.\n"
                + operational_claim
                + "\nRelevant information om byggnaden eller ärendet:\n"
                "Godkänd energideklaration från 2019-11-03. Byggnadsid: 01-80-SKYTTEN2-2."
            )

    monkeypatch.setitem(sys.modules, "src.agents.openai_agent", types.SimpleNamespace(OpenAIResponseAgent=SummaryAgent))
    summary = module.generate_expert_handoff_summary(
        "thread", [{"role": "user", "content": "Vi har ojämn värme."}], {"expert_handoff_scope": scope},
    )
    assert operational_claim not in summary
    assert "Ojämn värme" in summary
    assert "20 000 kr" in summary
    assert "Godkänd energideklaration från 2019-11-03" in summary
    assert "01-80-SKYTTEN2-2" in summary


def test_fallback_keeps_budget_correction_without_control_requests_or_action_history(service):
    messages = [
        {"role": "user", "content": "Vi har ojämn värme. Budgeten är 50 000 kr."},
        {"role": "user", "content": "Skicka bara sammanfattningen och budgeten är nu 20 000 kr."},
        {"role": "assistant", "content": "The email was sent. obsolete-status-marker"},
        {"role": "user", "content": "Nej, skicka inte."},
    ]
    summary = service.copyable_expert_handoff_summary(messages, {
        "building_id": "BUILDING-1", "expert_handoff_scope": "summary_only",
        "action_receipts": {"handoffs": [{"status": "submitted", "request_id": "obsolete-receipt-marker"}]},
    })
    assert "ojämn värme" in summary
    assert "20 000 kr" in summary
    assert "BUILDING-1" in summary
    for control_text in ("Skicka bara", "Nej, skicka", "obsolete-status-marker", "obsolete-receipt-marker", "SMTP"):
        assert control_text not in summary


def test_operational_only_model_output_uses_case_fallback(monkeypatch):
    module = fresh_import("src.services.expert_handoff_email")

    class SummaryAgent:
        def __init__(self, **kwargs):
            pass

        def generate_response(self, *args, **kwargs):
            return "The user approved sharing. Only the summary is sent."

    monkeypatch.setitem(sys.modules, "src.agents.openai_agent", types.SimpleNamespace(OpenAIResponseAgent=SummaryAgent))
    summary = module.generate_expert_handoff_summary(
        "thread", [{"role": "user", "content": "Residents report uneven heating. Budget is 20 000 kr."}], {},
    )
    assert "Residents report uneven heating" in summary
    assert "20 000 kr" in summary
    assert "approved" not in summary


def test_scope_change_regenerates_contaminated_legacy_summary(service, monkeypatch):
    messages = [{"role": "user", "content": "Help with uneven heating"}]
    metadata = {"expert_handoff_scope": "full_conversation"}
    monkeypatch.setattr(service, "generate_expert_handoff_summary", lambda *args: "Only the summary is shared.")
    metadata["expert_handoff_preview"] = service.prepare_expert_handoff("thread", messages, metadata)
    assert service.expert_handoff_preview_matches("thread", messages, metadata)
    metadata["expert_handoff_scope"] = "summary_only"
    generated = []
    monkeypatch.setattr(service, "generate_expert_handoff_summary", lambda *args: generated.append(args) or "Uneven heating; budget 20 000 kr.")
    preview = service.prepare_expert_handoff("thread", messages, metadata)
    assert generated
    assert preview["summary"] == "Uneven heating; budget 20 000 kr."
    assert preview["sharing_scope"] == "summary_only"


def test_shared_technical_system_is_not_removed_from_case_projection(service):
    projected = service._case_summary_messages([
        {"role": "user", "content": "We have a shared heating system. The laundry room is shared by 20 flats."},
        {"role": "assistant", "content": "Check heating distribution before replacing equipment."},
    ])
    assert len(projected) == 2
    assert "shared heating system" in projected[0]["content"]
    assert "shared by 20 flats" in projected[0]["content"]
    assert "Check heating distribution" in projected[1]["content"]


def test_approved_energy_declaration_is_preserved_as_technical_evidence(service):
    case_text = "Energy declaration approved on 2019-11-03. Declared class G; calculated class F."
    assert service._case_summary_text(case_text) == case_text


def test_summary_reduced_to_empty_sections_uses_case_fallback(monkeypatch):
    module = fresh_import("src.services.expert_handoff_email")

    class SummaryAgent:
        def __init__(self, **kwargs):
            pass

        def generate_response(self, *args, **kwargs):
            return "Användarens huvudsakliga behov:\nEndast sammanfattningen delas.\nVad rådgivaren behöver hjälpa till med:\nAnvändaren har godkänt delning."

    monkeypatch.setitem(sys.modules, "src.agents.openai_agent", types.SimpleNamespace(OpenAIResponseAgent=SummaryAgent))
    summary = module.generate_expert_handoff_summary(
        "thread", [{"role": "user", "content": "Vi har ojämn värme. Budgeten är nu 20 000 kr."}], {},
    )
    assert "Sammanfattning att kopiera" in summary
    assert "ojämn värme" in summary
    assert "20 000 kr" in summary
