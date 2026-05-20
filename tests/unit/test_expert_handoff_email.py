from email.mime.multipart import MIMEMultipart
from unittest.mock import patch

from tests.support import fresh_import


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


def test_send_expert_handoff_email_includes_user_cc_in_smtp_recipients():
    module = fresh_import("src.services.expert_handoff_email")

    class FakeSMTP:
        recipients = None

        def __init__(self, server, port):
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
        assert module.send_expert_handoff_email(
            thread_id="user@example.com:thread-2",
            messages=[{"role": "user", "content": "I need help"}],
            metadata={},
        )

    assert "user@example.com" in FakeSMTP.recipients
