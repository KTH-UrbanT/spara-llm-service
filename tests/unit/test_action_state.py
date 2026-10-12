"""Regressions for action facts surviving multi-turn routing and storage."""
from copy import deepcopy
import json

import pytest

from src.pipeline.action_state import (
    restore_action_receipts, record_action_response, trusted_action_context,
    is_handoff_status_question, is_report_status_question, render_handoff_status,
    report_status_response,
)
from tests.unit.test_action_routing import configured_pipeline
from tests.unit.test_router_confirmation import router


ARTIFACT = {
    "report_id": "pilot-report", "file_name": "01-80-SKYTTEN2-2.txt",
    "mime_type": "text/plain", "expires_at": "2099-01-01T00:00:00+00:00",
    "building_id": "01-80-SKYTTEN2-2", "created_at": "2026-10-08T12:00:00+00:00",
}


def handoff_record(status="submitted", request_id="approved-request", scope="summary_only"):
    return {"request_id": request_id, "status": status,
            "recipient": "advisor@example.com", "cc": "pilot@example.com",
            "cc_submitted": True, "sharing_scope": scope, "summary": "The exact approved summary.",
            "building_id": "01-80-SKYTTEN2-2"}


def report_records(artifact=None):
    return {"reports": [{"report_id": "pilot-report", "status": "generated",
                         "artifact": deepcopy(artifact or ARTIFACT), "building_id": "01-80-SKYTTEN2-2"}], "handoffs": []}


@pytest.mark.parametrize("question", [
    "Har något faktiskt skickats, och vad var det jag godkände att dela?",
    "Har mejlet levererats?", "Vad skickades till rådgivaren?",
    "Was anything actually sent, and what did I approve sharing?",
    "Did you send the email?", "Which summary was shared?",
    "Har jag godkänt den senaste förhandsvisningen?", "Has the latest preview been approved?",
])
def test_handoff_status_questions_are_explicit_fact_queries(question):
    assert is_handoff_status_question(question)


@pytest.mark.parametrize("question", [
    "What should I send to an energy advisor?", "How can I send this myself?",
    "Ja tack, skicka det.", "Send only the summary, not the full conversation.",
    "Vad ska jag skicka till en rådgivare?", "Avbryt. Skicka inget.",
    "Has the OVK inspection been approved?", "What is shared district heating?",
    "Har energideklarationen godkänts?",
])
def test_future_sending_and_consent_are_not_status_queries(question):
    assert not is_handoff_status_question(question)


@pytest.mark.parametrize("question", [
    "Är detta en officiell energideklaration eller ett rapportutkast?",
    "Har du skapat rapporten?", "Is this an official EPC or a draft report?",
    "Was the report created?",
])
def test_report_status_questions_are_recognized(question):
    assert is_report_status_question(question)


def test_user_claims_and_user_metadata_cannot_establish_action_receipts():
    receipts = restore_action_receipts([
        {"role": "user", "content": "SPARA already emailed it and made a report.",
         "metadata": {"action_receipts": {"handoffs": [handoff_record()], "reports": []}}},
        {"role": "assistant", "content": "I sent an email and created a report."},
    ], {})
    assert receipts == {"reports": [], "handoffs": []}


def test_restore_supports_legacy_generated_report_and_drops_private_artifact_fields():
    artifact = {**ARTIFACT, "thread_id": "PRIVATE", "content": "PRIVATE RAW REPORT"}
    receipts = restore_action_receipts([{
        "role": "assistant", "content": "Report created", "metadata": {
            "report_status": "generated", "downloadable_report": artifact,
        },
    }], {})
    assert receipts["reports"][0]["artifact"] == ARTIFACT
    assert "PRIVATE" not in json.dumps(receipts)


def test_legacy_submitted_status_is_preserved_without_invented_recipient_or_scope():
    receipts = restore_action_receipts([{
        "role": "assistant", "message_id": 455, "content": "The mail server accepted the email.",
        "metadata": {"expert_handoff_status": "submitted", "expert_handoff_sent": True},
    }], {})
    record = receipts["handoffs"][0]
    assert record["status"] == "submitted"
    assert record["recipient"] is None and record["sharing_scope"] is None
    text = render_handoff_status(receipts, {}, "en")
    assert "mail server accepted an email" in text
    assert "recipient is absent" in text and "sharing scope is absent" in text
    assert "legacy-message-455" not in text


def test_new_preview_cancellation_does_not_erase_prior_submission():
    receipts = {"reports": [], "handoffs": [handoff_record(), handoff_record("cancelled", "second-preview")]}
    for language, expected, cancelled in (("sv", "E-postservern tog emot", "avbruten"), ("en", "mail server accepted", "cancelled")):
        text = render_handoff_status(receipts, {}, language)
        assert expected in text and cancelled in text
        assert "approved-request" not in text and "second-preview" not in text


@pytest.mark.parametrize("status,expected,forbidden", [
    ("submitted", "Delivery to the recipient's inbox is not confirmed", "No real email was sent"),
    ("simulated", "No real email was sent", "mail server accepted"),
    ("unknown", "unknown whether the mail server accepted", "No real email was sent"),
    ("failed", "could not be submitted", "mail server accepted"),
])
def test_actual_submission_simulation_failure_and_unknown_stay_distinct(status, expected, forbidden):
    text = render_handoff_status({"reports": [], "handoffs": [handoff_record(status)]}, {}, "en")
    assert expected in text and forbidden not in text
    assert "without a conversation attachment" in text


def legacy_handoff_message(message_id, agent="ExpertHandoffAgent", **extra):
    return {
        "role": "assistant", "message_id": message_id, "content": "Legacy reply",
        "metadata": {"agent": agent, "route": "expert_handoff", "classification": "expert_handoff",
                     "expert_handoff_status": "submitted", "expert_handoff_sent": True,
                     "expert_handoff_triggered": True, **extra},
    }


def test_legacy_router_status_flags_do_not_become_another_email():
    messages = [legacy_handoff_message(455), legacy_handoff_message(467),
                legacy_handoff_message(513, "RouterAgent")]
    records = restore_action_receipts(messages, {})
    assert [item["request_id"] for item in records["handoffs"]] == ["legacy-message-455", "legacy-message-467"]
    assert all(item["source_message_id"] in {"455", "467"} for item in records["handoffs"])
    text = render_handoff_status(records, {}, "sv")
    assert "tog emot 2 meddelanden" in text
    assert "513" not in text


def test_known_router_ghost_is_removed_from_later_canonical_snapshots_and_reload():
    phantom = {**handoff_record(request_id="legacy-message-513"), "legacy_status_only": True}
    canonical = {"reports": [], "handoffs": [phantom, handoff_record("cancelled", "latest-preview")]}
    messages = [legacy_handoff_message(455), legacy_handoff_message(467),
                legacy_handoff_message(513, "RouterAgent"),
                {"role": "assistant", "message_id": 539, "metadata": {"agent": "RouterAgent", "action_receipts": canonical}}]
    records = restore_action_receipts(messages, {"action_receipts": canonical})
    ids = [item["request_id"] for item in records["handoffs"]]
    assert ids == ["legacy-message-455", "legacy-message-467", "latest-preview"]
    assert restore_action_receipts(messages, {"action_receipts": records}) == records


def test_copied_receipt_with_known_status_only_source_is_removed_without_matching_request_id():
    phantom = {**handoff_record(request_id="copied-receipt"), "source_message_id": "513"}
    records = restore_action_receipts([legacy_handoff_message(513, "RouterAgent")], {
        "action_receipts": {"reports": [], "handoffs": [phantom]},
    })
    assert records["handoffs"] == []


def test_legacy_receipt_without_its_original_source_is_preserved_conservatively():
    unknown_origin = {**handoff_record(request_id="legacy-message-513"), "legacy_status_only": True}
    records = restore_action_receipts([], {"action_receipts": {"reports": [], "handoffs": [unknown_origin]}})
    assert records["handoffs"][0]["request_id"] == "legacy-message-513"
    assert records["handoffs"][0]["status"] == "submitted"


def test_legacy_outcome_with_unknown_agent_name_is_not_discarded():
    message = legacy_handoff_message(455, "unknown")
    message["metadata"].pop("route")
    records = restore_action_receipts([message], {})
    assert records["handoffs"][0]["status"] == "submitted"
    assert records["handoffs"][0]["provenance"] == "legacy_outcome"


@pytest.mark.parametrize("response", [
    {"route": "action_status", "agent_answered": "router"},
    {"route": "generic", "agent_answered": "generic"},
    {"content": "Some unrelated reply"},
])
def test_status_and_other_replies_do_not_record_inherited_handoff_flags(response):
    metadata = {"expert_handoff_status": "submitted", "expert_handoff_sent": True,
                "expert_handoff_preview": {"request_id": "old-preview", "summary": "Old summary"}}
    assert record_action_response(response, metadata)["handoffs"] == []


def test_current_execution_agent_takes_precedence_over_old_router_metadata():
    metadata = {"agent": "RouterAgent", "expert_handoff_status": "pending_confirmation",
                "expert_handoff_preview": {"request_id": "fresh-preview", "sharing_scope": "summary_only"}}
    records = record_action_response({"agent_answered": "expert_handoff", "route": "expert_handoff"}, metadata)
    assert records["handoffs"][0]["request_id"] == "fresh-preview"
    assert records["handoffs"][0]["provenance"] == "handoff_execution"


def test_new_preview_supersedes_old_pending_preview_and_keeps_prior_submission():
    metadata = {"action_receipts": {"reports": [], "handoffs": [
        handoff_record(), handoff_record("pending_confirmation", "old-preview", "full_conversation"),
    ]}, "expert_handoff_status": "pending_confirmation", "expert_handoff_preview": {
        "request_id": "new-preview", "sharing_scope": "summary_only", "summary": "Budget 20,000 kr",
    }}
    records = record_action_response({"agent_answered": "expert_handoff", "route": "expert_handoff"}, metadata)
    assert [item["status"] for item in records["handoffs"]] == ["submitted", "superseded", "pending_confirmation"]
    text = render_handoff_status(records, {}, "en")
    assert "awaiting separate approval" in text and "has not been approved or sent" in text
    assert "Budget 20,000 kr" not in text and "full_conversation" not in text
    assert "Only the approved summary" not in text


def test_new_topic_marks_existing_pending_preview_superseded_without_recording_new_action():
    metadata = {"action_receipts": {"reports": [], "handoffs": [handoff_record("pending_confirmation", "old-preview")]},
                "expert_handoff_status": "superseded", "expert_handoff_preview": {"request_id": "old-preview"}}
    records = record_action_response({"agent_answered": "generic", "route": "generic"}, metadata)
    assert len(records["handoffs"]) == 1
    assert records["handoffs"][0]["status"] == "superseded"


def test_submission_cannot_be_downgraded_by_older_or_unsent_metadata():
    for status in ("cancelled", "superseded", "pending_confirmation", "unknown", "failed", "simulated"):
        metadata = {"action_receipts": {"reports": [], "handoffs": [handoff_record(request_id="same-request")]},
                    "expert_handoff_status": status, "expert_handoff_preview": {"request_id": "same-request"}}
        records = record_action_response({"agent_answered": "expert_handoff", "route": "expert_handoff"}, metadata)
        assert records["handoffs"][0]["status"] == "submitted"


@pytest.mark.parametrize("status", ["pending_confirmation", "cancelled", "superseded", "changes_requested", "disabled"])
def test_unapproved_preview_scope_is_not_described_as_approved(status):
    record = handoff_record(status, "PRIVATE-REQUEST-UUID")
    record["summary"] = "Budget 20,000 kr"
    for language in ("sv", "en"):
        text = render_handoff_status({"reports": [], "handoffs": [record]}, {}, language)
        assert "godkända sammanfattningen" not in text and "approved summary" not in text
        assert "PRIVATE-REQUEST-UUID" not in text


@pytest.mark.parametrize("status", [
    "pending_confirmation", "cancelled", "superseded", "changes_requested", "disabled", "unknown", "failed",
])
@pytest.mark.parametrize("language", ["sv", "en"])
def test_status_does_not_repeat_stale_lifecycle_claims_from_unsent_preview(status, language):
    summary = "OLD GENERATED BODY: The current preview awaits confirmation. Förhandsvisningen väntar på godkännande."
    record = {**handoff_record(status, "latest-preview"), "summary": summary}
    records = {"reports": [], "handoffs": [record]}
    original = deepcopy(records)
    text = render_handoff_status(records, {}, language)
    assert summary not in text and "OLD GENERATED BODY" not in text
    assert records == original  # Historical approved/draft payloads remain stored exactly.
    if status == "cancelled":
        assert ("avbruten" if language == "sv" else "cancelled") in text
    if status == "pending_confirmation":
        assert ("inte godkänts eller skickats" if language == "sv" else "not been approved or sent") in text


@pytest.mark.parametrize("status", ["submitted", "simulated"])
@pytest.mark.parametrize("language", ["sv", "en"])
def test_submitted_or_simulated_summary_is_preserved_exactly_as_evidence(status, language):
    summary = "Exact approved contents\nBudget: 20 000 kr\nÅtgärder och osäkerheter."
    record = {**handoff_record(status), "summary": summary}
    text = render_handoff_status({"reports": [], "handoffs": [record]}, {}, language)
    assert summary in text
    if status == "simulated":
        assert ("Simulerad sammanfattning" if language == "sv" else "Simulated summary") in text


def test_submission_count_survives_more_than_four_or_thirty_two_later_previews():
    messages = [legacy_handoff_message(455), legacy_handoff_message(467)]
    for index in range(40):
        messages.append({"role": "assistant", "message_id": 600 + index,
                         "metadata": {"agent": "ExpertHandoffAgent", "expert_handoff_status": "pending_confirmation",
                                      "expert_handoff_preview": {"request_id": f"preview-{index}", "sharing_scope": "summary_only"}}})
    records = restore_action_receipts(messages, {})
    submitted = [item for item in records["handoffs"] if item["status"] == "submitted"]
    assert len(submitted) == 2
    assert sum(item["status"] == "pending_confirmation" for item in records["handoffs"]) == 1
    text = render_handoff_status(records, {}, "en")
    assert "accepted 2 emails" in text and "awaiting separate approval" in text
    assert "preview-" not in text


def test_expired_report_is_still_created_but_does_not_offer_expired_download():
    artifact = {**ARTIFACT, "expires_at": "2020-01-01T00:00:00+00:00"}
    payload, clarification = report_status_response(report_records(artifact), {}, "sv")
    assert clarification is None
    assert "skapade ett rapportutkast" in payload["content"]
    assert "gått ut" in payload["content"]
    assert "downloadable_report" not in payload


def test_report_from_other_building_is_not_attributed_to_current_building():
    payload, _ = report_status_response(report_records(), {"building_id": "01-80-OTHER1-1"}, "sv")
    assert "01-80-SKYTTEN2-2" in payload["content"]
    assert "gäller inte den nu valda byggnaden" in payload["content"]


def test_explicit_current_id_takes_precedence_over_stale_verified_identity():
    payload, _ = report_status_response(report_records(), {
        "building_id_from_user": "01-80-NEWBUILDING1-1",
        "building_id": "01-80-SKYTTEN2-2",
        "building_identity_check": {"status": "passed", "matched_building_id": "01-80-SKYTTEN2-2"},
    }, "sv")
    assert "gäller inte den nu valda byggnaden" in payload["content"]


def test_trusted_context_contains_receipts_not_internal_application_metadata():
    context = trusted_action_context({**report_records(), "handoffs": [handoff_record()]})
    assert "generated" in context and "submitted" in context
    assert "SMTP accepted" in context and "never confirmed inbox delivery" in context
    assert "approved-request" in context and "pilot-report" in context
    assert "download_url" not in context and "The exact approved summary." not in context


@pytest.mark.parametrize("question", [
    "Är detta en officiell energideklaration eller ett rapportutkast?",
    "Is this an official EPC or a draft report?",
])
def test_generated_report_status_restores_from_saved_assistant_metadata_without_model(router, monkeypatch, question):
    module, pipeline = configured_pipeline(router)
    monkeypatch.setattr(module, "generate_draft_report_response", lambda *args: {
        "role": "assistant", "content": "Draft created", "classification": "draft_energy_report",
        "report_status": "generated", "downloadable_report": deepcopy(ARTIFACT),
    })
    messages = [{"role": "user", "content": "Create a short report for the board."}]
    created, metadata = pipeline.route_message(messages, messages[-1]["content"], {}, "report-receipt-thread")
    # SQL/Redis restoration keeps private metadata, not the public browser payload.
    stored = {"role": "assistant", "content": created["content"], "metadata": json.loads(json.dumps(created["metadata"]))}
    messages.extend([stored, {"role": "user", "content": question}])
    monkeypatch.setattr(pipeline.router, "classify_question", lambda *args, **kwargs: pytest.fail("Status needed a classifier"))
    monkeypatch.setattr(module, "generate_draft_report_response", lambda *args: pytest.fail("Status generated another report"))
    response, restored = pipeline.route_message(messages, question, {}, "report-receipt-thread")
    assert response["route"] == "action_status"
    assert response["downloadable_report"] == ARTIFACT
    assert restored["action_receipts"]["reports"][0]["report_id"] == "pilot-report"
    assert ("inte en officiell" if question.startswith("Är") else "not an official") in response["content"]


def test_definite_report_request_clarifies_existing_vs_new_without_generating(router, monkeypatch):
    module, pipeline = configured_pipeline(router)
    question = "i want the report for my building"
    monkeypatch.setattr(pipeline.router, "classify_question", lambda *args, **kwargs: pytest.fail("Known report request needed classifier"))
    monkeypatch.setattr(module, "generate_draft_report_response", lambda *args: pytest.fail("Ambiguous request generated report"))
    response, metadata = pipeline.route_message([{"role": "user", "content": question}], question, {}, "ambiguous-report")
    assert response["route"] == "clarification"
    assert metadata["clarification"]["reason"] == "report_intent"
    assert "existing document" in response["content"]


def test_plain_original_ovk_request_does_not_return_generated_report(router, monkeypatch):
    module, pipeline = configured_pipeline(router)
    question = "Visa vår senaste OVK-rapport."
    monkeypatch.setattr(module, "generate_draft_report_response", lambda *args: pytest.fail("Original source request created draft"))
    response, _ = pipeline.route_message([{"role": "user", "content": question}], question, {"action_receipts": report_records()}, "source-document")
    assert response["route"] != "action_status"
    assert "downloadable_report" not in response


def test_exact_misspelled_street_request_routes_for_unverified_lookup(router, monkeypatch):
    module, pipeline = configured_pipeline(router)
    pipeline.router.classify_question = lambda *args, **kwargs: "generic"
    question = "also can you tell me about my building professorsslingan 51"
    calls = []
    def lookup(last_message, messages, metadata, thread_id):
        calls.append((last_message, metadata))
        assert (metadata.get("building_identity_check") or {}).get("status") != "passed"
        return {"content": "Please confirm the municipality.", "agent_answered": "building"}, metadata
    monkeypatch.setattr(pipeline.building, "handle_building_query", lookup)
    response, _ = pipeline.route_message([{"role": "user", "content": question}], question, {}, "raw-street-lookup")
    assert response["classification"] == "building_specific"
    assert calls[0][0] == question


def test_action_receipts_survive_building_agent_metadata_replacement(router, monkeypatch):
    _, pipeline = configured_pipeline(router)
    question = "What energy class is building ID 01-80-SKYTTEN2-2?"
    response, metadata = pipeline.route_message([{"role": "user", "content": question}], question, {"action_receipts": report_records()}, "switch-with-receipt")
    assert response["classification"] == "building_specific"
    assert metadata["action_receipts"]["reports"][0]["report_id"] == "pilot-report"


def test_generic_generation_receives_private_trusted_receipt_context(router, monkeypatch):
    _, pipeline = configured_pipeline(router)
    pipeline.router.classify_question = lambda *args, **kwargs: "generic"
    captured = []
    class ReceiptAwareGeneric:
        supports_action_receipts = True
        def handle_generic_input(self, question, messages, action_receipts=None):
            captured.append(action_receipts)
            return {"content": "General advice."}
    pipeline.generic = ReceiptAwareGeneric()
    question = "give me general energy tips"
    records = {**report_records(), "handoffs": [handoff_record()]}
    response, _ = pipeline.route_message([{"role": "user", "content": question}], question, {"action_receipts": records}, "generic-action-receipts")
    assert response["classification"] == "generic", response
    assert captured[0]["reports"][0]["status"] == "generated"
    assert captured[0]["handoffs"][0]["status"] == "submitted"
