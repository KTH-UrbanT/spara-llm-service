from tests.support import fresh_import, stub_module
from pathlib import Path
import pytest


class StubRouterAgent:
    next_classification = "generic"
    last_call = None

    def wants_draft_report(self, message):
        return "report" in message.lower() or "briefing" in message.lower()

    def wants_expert_handoff(self, message):
        lowered = message.lower()
        return "expert" in lowered or "ekr" in lowered

    def is_confirmation(self, message):
        normalized = message.strip().lower()
        return normalized == "yes"

    def is_rejection(self, message):
        normalized = message.strip().lower()
        return normalized == "no"

    def classify_question(self, message, previous_classification, history=None):
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
            "route": "combined",
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
    next_response = "generic answer"

    def handle_generic_input(self, last_message, messages):
        type(self).last_call = (last_message, messages)
        return type(self).next_response


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
    stub_module(
        "src.services.expert_handoff_email",
        handoff_response_language=lambda messages, metadata: "en",
        send_expert_handoff_email=lambda thread_id, messages, metadata: {"status": "submitted"},
        prepare_expert_handoff=lambda thread_id, messages, metadata: {
            "enabled": True, "recipient": "advisor@example.com", "cc": None,
            "request_id": "preview-1", "summary": "Summary to copy",
        },
        expert_handoff_preview_matches=lambda thread_id, messages, metadata: bool(metadata.get("expert_handoff_preview")),
        copyable_expert_handoff_summary=lambda messages, metadata: "Summary to copy",
    )
    return fresh_import("src.pipeline.agent_router")


def test_classification_prompt_requires_brf_name_sanity_check():
    prompt = Path("src/prompts/classification_prompt.txt").read_text(encoding="utf-8")

    assert "a concrete BRF name must look like an actual proper name after BRF" in prompt
    assert "BRF with high heating bills" in prompt
    assert "BRF do we have" in prompt
    assert "The word “BRF” alone is not a building identifier" in prompt


def test_classification_prompt_keeps_public_brf_and_stockholmshem_questions_generic():
    prompt = Path("src/prompts/classification_prompt.txt").read_text(encoding="utf-8")

    assert "How many BRFs are in Stockholm" in prompt
    assert "How many buildings does StockholmsHem have" in prompt
    assert "Public/statistical/entity questions about BRFs" in prompt


def test_routes_generic_requests():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubGenericAgent.next_response = "generic answer"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "give me general energy tips"}],
        last_message="give me general energy tips",
        metadata={"kept": True},
        thread_id="thread-1",
    )

    assert response["content"] == "generic answer"
    assert response["classification"] == "generic"
    assert response["agent_answered"] == "generic"
    assert response["route"] == "generic"
    assert response["role"] == "assistant"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"kept": True}
    assert StubGenericAgent.last_call == (
        "give me general energy tips",
        [{"role": "user", "content": "give me general energy tips"}],
    )


@pytest.mark.parametrize("question", [
    "Vad är en BRF?", "Vad betyder BRF?", "Vad står BRF för?",
    "Hur många BRFer finns i Stockholm?", "Hur manga BRF finns i Stockholm?",
    "brf har fjarrvarme o det blir dyrt", "Vår BRF har fjärrvärme och det blir dyrt.",
    "Vi är en BRF med höga värmekostnader. Hur kan vi minska dem?",
    "Hur kan vi spara energi?", "Ge mig allmänna energiråd.",
    "Vad kan vi göra i vår BRF för att spara energi?",
])
def test_swedish_general_questions_do_not_start_building_lookup(question):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "Allmänna råd på svenska."
    response, metadata = module.AgentRouter().route_message(
        [{"role": "user", "content": question}], question, {}, "swedish-general",
    )
    assert response["classification"] == "generic"
    assert response["content"] == "Allmänna råd på svenska."
    assert StubBuildingAgent.last_call is None
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}


@pytest.mark.parametrize("question", [
    "BRF Sjöstaden 1 har fjärrvärme. Hur kan vi minska våra värmekostnader?",
    "Vilken energiklass har vår byggnad?",
    "Ge mig information om Ringvägen 10, Stockholm.",
])
def test_swedish_building_requests_still_use_building_flow(question):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubBuildingAgent.last_call = None
    response, _ = module.AgentRouter().route_message(
        [{"role": "user", "content": question}], question, {}, "swedish-building",
    )
    assert response["classification"] == "building_specific"
    assert StubBuildingAgent.last_call is not None


def test_definition_can_interrupt_address_clarification():
    module = import_agent_router_module()
    StubBuildingAgent.last_call = None
    question = "Vad är en BRF?"
    messages = [
        {"role": "assistant", "content": "I found multiple building matches. Which municipality or postcode?"},
        {"role": "user", "content": question},
    ]
    response, _ = module.AgentRouter().route_message(messages, question, {
        "address": "Ringvägen 10",
        "clarification": {"needed": True, "reason": "ambiguous_address"},
        "building_identity_check": {"status": "ambiguous"},
    }, "general-interrupt")
    assert response["classification"] == "generic"
    assert StubBuildingAgent.last_call is None


def test_failed_lookup_stays_out_of_subsequent_advisory_context():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    question = "Hur kan vi spara energi?"
    messages = [
        {"role": "user", "content": "BRF Okänd"},
        {"role": "assistant", "content": "Lookup failed", "classification": "building_specific"},
        {"role": "user", "content": question},
    ]
    original_metadata = {
        "brf_name": "Okänd", "brf_resolution": {"status": "not_found"},
        "clarification": {"needed": True, "reason": "brf_not_found"},
        "pending_building_advice_request": {"question": "old question"},
        "context": {"parsed_intent": "SQL database"}, "kept": True,
    }
    router = module.AgentRouter()
    response, metadata = router.route_message(messages, question, original_metadata, "failed-loop")
    assert response["classification"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"kept": True, "routing_context_start": 2}
    assert StubGenericAgent.last_call[1] == messages
    assert "brf_name" in original_metadata

    messages += [response, {"role": "user", "content": "Hur kan vi minska våra värmekostnader?"}]
    response, metadata = router.route_message(messages, messages[-1]["content"], metadata, "failed-loop")
    assert response["classification"] == "generic"
    assert StubBuildingAgent.last_call is None
    assert StubGenericAgent.last_call[1] == messages


@pytest.mark.parametrize("reply", ["BRF Sjöstaden 1", "Ringvägen 10, Stockholm", "01-80-SKYTTEN2-2"])
def test_failed_lookup_accepts_replacement_identifier(reply):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    response, _ = module.AgentRouter().route_message(
        [{"role": "user", "content": reply}], reply,
        {"brf_resolution": {"status": "not_found"}, "brf_name": "Okänd"}, "replace-identity",
    )
    assert response["classification"] == "building_specific"


@pytest.mark.parametrize("reason", ["building_not_found", "building_not_found_by_id", "brf_lookup_failed"])
def test_failed_building_lookup_does_not_block_new_general_question(reason):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "General energy advice"
    question = "Hur kan vi spara energi?"
    metadata = {
        "address": "Okändgatan 10", "address_from_user": "Okändgatan 10",
        "building_identity_check": {"status": "missing"},
        "clarification": {"needed": True, "reason": reason},
        "pending_building_advice_request": {"question": "old request"},
        "kept": True,
    }
    response, cleaned = module.AgentRouter().route_message([
        {"role": "user", "content": "Okändgatan 10"},
        {"role": "assistant", "content": "Not found", "classification": "building_specific"},
        {"role": "user", "content": question},
    ], question, metadata, "failed-building")
    assert response["classification"] == "generic"
    assert cleaned["kept"] is True
    assert "clarification" not in cleaned
    assert "pending_building_advice_request" not in cleaned
    assert "address" not in cleaned
    assert StubBuildingAgent.last_call is None


def test_swedish_portfolio_analysis_is_not_overridden_by_public_brf_rule():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "cluster"
    question = "Hur många BRFer finns i vårt dataset?"
    response, _ = module.AgentRouter().route_message(
        [{"role": "user", "content": question}], question, {}, "portfolio",
    )
    assert response["classification"] == "cluster"


def test_lookup_cleanup_preserves_verified_building_and_unrelated_metadata():
    module = import_agent_router_module()
    metadata = {
        "address": "Ringvägen 10", "byggnadsid": "01-80-SKYTTEN2-2",
        "building_identity_check": {"status": "passed", "matched_building_id": "01-80-SKYTTEN2-2"},
        "brf_name": "Okänd", "brf_resolution": {"status": "lookup_error"},
        "clarification": {"reason": "brf_lookup_failed"}, "kept": True,
    }
    cleaned = module._clear_failed_lookup_state(metadata)
    assert cleaned["address"] == metadata["address"]
    assert cleaned["byggnadsid"] == metadata["byggnadsid"]
    assert cleaned["building_identity_check"] == metadata["building_identity_check"]
    assert cleaned["kept"] is True
    assert "brf_name" not in cleaned
    assert "clarification" not in cleaned


@pytest.mark.parametrize("question, route", [
    ("Create an energy report", "generic"),
    ("Prepare a board briefing", "draft_energy_report"),
])
def test_report_after_failed_lookup_retains_full_transcript(question, route):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = route
    messages = [
        {"role": "user", "content": "Earlier advisory discussion"},
        {"role": "assistant", "content": "Failed BRF lookup", "classification": "building_specific"},
        {"role": "user", "content": question},
    ]
    module.AgentRouter().route_message(
        messages, question, {"brf_resolution": {"status": "not_found"}}, "report-history",
    )
    assert StubDraftReportService.last_call[1] == messages


def test_fast_conversational_greeting_skips_conversational_agent():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubConversationalAgent.last_call = None
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "hello"}],
        last_message="hello",
        metadata={"kept": True},
        thread_id="thread-fast",
    )

    assert response["classification"] == "conversational"
    assert response["agent_answered"] == "fast_conversational"
    assert "energy advice" in response["content"]
    assert "If you want building-specific advice" in response["content"]
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"kept": True}
    assert StubConversationalAgent.last_call is None


def test_swedish_greeting_is_answered_in_swedish_without_a_model_call():
    module = import_agent_router_module()
    StubConversationalAgent.last_call = None
    StubRouterAgent.last_call = None
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "Hej!"}],
        last_message="Hej!", metadata={"kept": True}, thread_id="thread-swedish",
    )

    assert response["content"].startswith("Hej!")
    assert "allmänna energiråd" in response["content"]
    assert response["classification"] == "conversational"
    assert StubConversationalAgent.last_call is None
    assert StubRouterAgent.last_call is None
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"kept": True}


@pytest.mark.parametrize("reply", ["111 38", "Brunnsgatan 9", "01-80-KAAKENHUSEN40-1", "12-85-SIBBARP34:12-"])
def test_building_selection_reply_routes_to_building_with_location_already_known(reply):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "conversational"
    StubRouterAgent.last_call = None
    StubBuildingAgent.last_call = None
    router = module.AgentRouter()
    metadata = {
        "address": "Kungsgatan 10", "address_location_hint": "Stockholm",
        "clarification": {"needed": True, "reason": "ambiguous_address"},
        "building_identity_check": {"status": "ambiguous"},
    }
    response, _ = router.route_message(
        messages=[{"role": "user", "content": reply}], last_message=reply,
        metadata=metadata, thread_id="thread-candidate-selection",
    )
    assert response["classification"] == "building_specific"
    assert StubBuildingAgent.last_call[0] == reply
    assert StubRouterAgent.last_call is None


def test_address_disambiguation_followup_routes_to_building_agent_before_classifier():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "conversational"
    StubRouterAgent.last_call = None
    StubBuildingAgent.last_call = None
    StubConversationalAgent.last_call = None
    router = module.AgentRouter()

    pending_metadata = {
        "address": "Ringvägen 10",
        "address_from_user": "Ringvägen 10",
        "clarification": {
            "needed": True,
            "reason": "ambiguous_address",
            "question_asked": "Please provide the city, postcode, municipality, BRF name, or exact building ID.",
            "resolved": False,
        },
        "building_identity_check": {
            "status": "ambiguous",
        },
    }

    response, metadata = router.route_message(
        messages=[
            {
                "role": "assistant",
                "content": "I found more than one possible building match for that address.",
                "classification": "building_specific",
            },
            {"role": "user", "content": "i live in Huddinge"},
        ],
        last_message="i live in Huddinge",
        metadata=pending_metadata,
        thread_id="thread-address-disambiguation",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["role"] == "assistant"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call == (
        "i live in Huddinge",
        [
            {
                "role": "assistant",
                "content": "I found more than one possible building match for that address.",
                "classification": "building_specific",
            },
            {"role": "user", "content": "i live in Huddinge"},
        ],
        {**pending_metadata, "response_language": "en"},
        "thread-address-disambiguation",
    )
    assert StubRouterAgent.last_call is None
    assert StubConversationalAgent.last_call is None


def test_address_disambiguation_followup_routes_with_pending_address_only():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "conversational"
    StubRouterAgent.last_call = None
    StubBuildingAgent.last_call = None
    StubConversationalAgent.last_call = None
    router = module.AgentRouter()

    pending_metadata = {
        "pending_ambiguous_address": "Sveavägen 17",
        "clarification": {
            "needed": True,
            "reason": "ambiguous_address",
            "question_asked": "Please provide the city, postcode, municipality, BRF name, or exact building ID.",
            "resolved": False,
        },
        "building_identity_check": {
            "status": "ambiguous",
        },
    }

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "i live in Örebro"}],
        last_message="i live in Örebro",
        metadata=pending_metadata,
        thread_id="thread-pending-address-disambiguation",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["role"] == "assistant"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call == (
        "i live in Örebro",
        [{"role": "user", "content": "i live in Örebro"}],
        {**pending_metadata, "response_language": "en"},
        "thread-pending-address-disambiguation",
    )
    assert StubRouterAgent.last_call is None
    assert StubConversationalAgent.last_call is None


def test_address_disambiguation_followup_routes_from_recent_assistant_prompt_without_metadata():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "conversational"
    StubRouterAgent.last_call = None
    StubBuildingAgent.last_call = None
    StubConversationalAgent.last_call = None
    router = module.AgentRouter()

    messages = [
        {
            "role": "user",
            "content": "I live at Exempelgatan 10. What is the building's energy performance?",
        },
        {
            "role": "assistant",
            "content": (
                "I found several possible building records for that address. "
                "Please provide the city, postcode, municipality, BRF name, or exact building ID."
            ),
            "classification": "building_specific",
        },
        {"role": "user", "content": "i live in Täby"},
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="i live in Täby",
        metadata={},
        thread_id="thread-address-disambiguation-no-metadata",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call == (
        "i live in Täby",
        messages,
        {"response_language": "en"},
        "thread-address-disambiguation-no-metadata",
    )
    assert StubRouterAgent.last_call is None
    assert StubConversationalAgent.last_call is None


def test_fast_multi_address_policy_question_skips_classifier_and_building_agent():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubRouterAgent.last_call = None
    StubBuildingAgent.last_call = None
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "Our property has multiple addresses. Which one do you use?",
            }
        ],
        last_message="Our property has multiple addresses. Which one do you use?",
        metadata={"kept": True},
        thread_id="thread-multi-address-policy",
    )

    assert response["classification"] == "building_specific"
    assert response["agent_answered"] == "fast_multi_address_clarification"
    assert response["route"] == "clarification"
    assert "building ID" in response["content"]
    assert "actual addresses" in response["content"]
    assert metadata["kept"] is True
    assert metadata["clarification"]["reason"] == "missing_multi_address_values"
    assert StubRouterAgent.last_call is None
    assert StubBuildingAgent.last_call is None


@pytest.mark.parametrize("route", ["generic", "building_specific", "combined", "conversational", "cluster"])
def test_every_public_answer_route_filters_generated_and_payload_links(monkeypatch, route):
    module = import_agent_router_module()
    from tests.unit.test_source_link_registry import registry
    monkeypatch.setattr(module, "resolve_source_links", registry.resolve_source_links)
    router = module.AgentRouter()
    original = {"role": "assistant", "route": route,
                "content": "Läs [påhittat dokument](https://invented.example/guide.pdf).",
                "sources": [{"filename": "unknown.pdf", "name": "Unknown source",
                             "link": "https://invented.example/guide.pdf", "link_status": "verified"}]}
    monkeypatch.setattr(router, "_route_message", lambda *args: (original, {"response_language": "sv"}))
    answer, metadata = router.route_message([], "Fråga", {}, "link-contract")
    assert "invented.example" not in answer["content"]
    assert "utelämnat länkar" in answer["content"]
    assert answer["sources"][0]["link"] == ""
    assert metadata["response_language"] == "sv"
    assert "invented.example" in original["content"]


def test_swedish_multi_address_policy_is_localized():
    module = import_agent_router_module()
    router = module.AgentRouter()
    question = "Vår fastighet har flera adresser. Vilken använder du?"
    response, metadata = router.route_message(
        messages=[{"role": "user", "content": question}], last_message=question,
        metadata={}, thread_id="sv-address-policy",
    )
    assert response["agent_answered"] == "fast_multi_address_clarification"
    assert "byggnadsid" in response["content"]
    assert "Ange de aktuella adresserna" in response["content"]
    assert metadata["response_language"] == "sv"


def test_multi_address_with_actual_addresses_still_goes_to_building_agent():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "general"
    StubRouterAgent.last_call = None
    StubBuildingAgent.last_call = None
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
            }
        ],
        last_message="Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
        metadata={},
        thread_id="thread-multi-address-values",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call is not None
    assert StubRouterAgent.last_call is None


@pytest.mark.parametrize("question", [
    "Tulegatan 5A och Rådmansgatan 31 i Stockholm verkar avse samma hus. Kan du kontrollera det och säga vilket värmesystem som är registrerat?",
    "Our property has two addresses: Teknikringen 37A and Teknikringen 39A. Which one do you use?",
    "Teknikringen 37A och Teknikringen 39A verkar avse samma hus. Kan du kontrollera det?",
])
def test_same_house_and_non_suffix_address_values_bypass_missing_address_shortcut(question):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubRouterAgent.last_call = None
    StubBuildingAgent.last_call = None
    messages = [{"role": "user", "content": question}]

    assert module._address_value_count(question) == 2
    assert module._has_explicit_site_identifier(question) is True
    assert module._has_multi_address_values_question(question) is True
    assert module._fast_multi_address_clarification(question) is None
    response, _ = module.AgentRouter().route_message(
        messages, question, {}, "same-house-address-values",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["agent_answered"] == "building"
    assert StubBuildingAgent.last_call[:2] == (question, messages)
    assert StubRouterAgent.last_call is None


@pytest.mark.parametrize("question", [
    "What heating system does Teknikringen 37A have?",
    "Give me information about Norra Djurgården 10",
])
def test_validated_non_suffix_address_counts_as_explicit_site_identifier(question):
    module = import_agent_router_module()
    assert module._address_value_count(question) == 1
    assert module._has_explicit_site_identifier(question) is True


@pytest.mark.parametrize("question", [
    "heating cost 1200",
    "Atemp 6798 m²",
    "energy performance 100",
    "primary energy number 155",
    "What does Atemp 6798 m² mean in general?",
    "How do energy performance 100 and heating cost 1200 differ in general?",
])
def test_generic_metric_values_do_not_count_as_street_addresses(question):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubGenericAgent.next_response = "generic metric explanation"
    StubBuildingAgent.last_call = None

    assert module._address_value_count(question) == 0
    assert module._has_explicit_site_identifier(question) is False
    assert module._has_multi_address_values_question(question) is False
    response, _ = module.AgentRouter().route_message(
        [{"role": "user", "content": question}], question, {}, "generic-metric-values",
    )

    assert response["classification"] == "generic"
    assert response["content"] == "generic metric explanation"
    assert StubBuildingAgent.last_call is None


@pytest.mark.parametrize("reply", [
    "01-80-NORRADJURGAARDEN1:49-34",
    "Teknikringen 37A, Stockholm",
    "option 2",
    "2",
])
def test_pending_different_buildings_accepts_id_address_and_option_replies_before_classifier(reply):
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "conversational"
    StubRouterAgent.last_call = None
    StubBuildingAgent.last_call = None
    pending = {
        "address": "Teknikringen 37A",
        "address_references": ["Teknikringen 37A", "Teknikringen 39A"],
        "clarification": {"needed": True, "reason": "multi_address_different_buildings"},
        "building_identity_check": {"status": "ambiguous"},
        "pending_address_query": {
            "user_message": "Which heating systems are recorded for these addresses?",
            "context": {"parsed_intent": "SQL database"},
        },
        "multi_address_resolution": {
            "status": "different_buildings",
            "building_ids": ["01-80-NORRADJURGAARDEN1:49-34", "01-80-NORRADJURGAARDEN1:49-35"],
        },
    }
    messages = [{"role": "user", "content": reply}]
    response, _ = module.AgentRouter().route_message(
        messages, reply, pending, "different-building-selection",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert StubBuildingAgent.last_call[:2] == (reply, messages)
    assert StubBuildingAgent.last_call[2]["pending_address_query"] == pending["pending_address_query"]
    assert StubBuildingAgent.last_call[2]["multi_address_resolution"] == pending["multi_address_resolution"]
    assert StubRouterAgent.last_call is None


def test_normalizes_general_classifier_label_to_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "general"
    StubGenericAgent.next_response = "generic answer"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "general tips for apartment buildings"}],
        last_message="general tips for apartment buildings",
        metadata={"kept": True},
        thread_id="thread-general",
    )

    assert response["content"] == "generic answer"
    assert response["classification"] == "generic"
    assert response["agent_answered"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"kept": True}


def test_building_data_request_overrides_generic_classifier_label():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "general"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "I'd like to know the energy performance of my building.",
            }
        ],
        last_message="I'd like to know the energy performance of my building.",
        metadata={},
        thread_id="thread-building-override",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["agent_answered"] == "building"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_broad_brf_heating_cost_advice_overrides_building_classifier_to_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "general heating cost advice"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "We are a BRF in Stockholm. How can we reduce our heating costs?",
            }
        ],
        last_message="We are a BRF in Stockholm. How can we reduce our heating costs?",
        metadata={},
        thread_id="thread-broad-brf-heating-costs",
    )

    assert response["content"] == "general heating cost advice"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}
    assert StubBuildingAgent.last_call is None


def test_brf_with_heating_bills_without_name_stays_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "generic BRF heating bill advice"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "We are a BRF with very high heating bills in winter. What can we do?",
            }
        ],
        last_message="We are a BRF with very high heating bills in winter. What can we do?",
        metadata={},
        thread_id="thread-broad-brf-winter-bills",
    )

    assert response["content"] == "generic BRF heating bill advice"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}
    assert StubBuildingAgent.last_call is None


def test_public_brf_count_question_stays_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "There are about 5,700 BRFs in Stockholm municipality."
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "How many BRFs are in Stockholm?"}],
        last_message="How many BRFs are in Stockholm?",
        metadata={},
        thread_id="thread-public-brf-count",
    )

    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}
    assert StubBuildingAgent.last_call is None


def test_public_stockholmshem_building_count_stays_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "generic StockholmsHem count answer"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "How many buildings StockholmsHem have?"}],
        last_message="How many buildings StockholmsHem have?",
        metadata={},
        thread_id="thread-public-stockholmshem-count",
    )

    assert response["content"] == "generic StockholmsHem count answer"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}
    assert StubBuildingAgent.last_call is None


def test_failed_brf_lookup_state_is_cleared_for_public_brf_question():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "BRF means bostadsrättsförening."
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {"role": "user", "content": "How many BRF do we have?"},
            {
                "role": "assistant",
                "classification": "building_specific",
                "content": "I could not find building addresses for BRF do we have.",
            },
            {"role": "user", "content": "what is BRF"},
        ],
        last_message="what is BRF",
        metadata={
            "brf_name": "do we have",
            "brf_resolution": {"status": "not_found", "brf_name": "do we have"},
            "clarification": {
                "needed": True,
                "reason": "brf_not_found",
                "question_asked": "I could not find building addresses for BRF do we have.",
                "resolved": False,
            },
        },
        thread_id="thread-stale-brf-loop",
    )

    assert response["content"] == "BRF means bostadsrättsförening."
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert "brf_name" not in metadata
    assert "brf_resolution" not in metadata
    assert "clarification" not in metadata
    assert StubBuildingAgent.last_call is None


def test_concrete_brf_heating_cost_advice_routes_to_building_resolution():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "I live in brf Sjöstaden 1. How can we improve our heating bills?",
            }
        ],
        last_message="I live in brf Sjöstaden 1. How can we improve our heating bills?",
        metadata={},
        thread_id="thread-concrete-brf-heating-costs",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_building_specific_advice_followup_uses_recent_brf_context():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    messages = [
        {
            "role": "user",
            "content": "I live in brf Sjöstaden 1. How can we improve our heating bills?",
        },
        {
            "role": "assistant",
            "classification": "generic",
            "content": "Here are general measures. If you want building-specific advice, share the BRF name, street address, or building ID.",
        },
        {
            "role": "user",
            "content": "yes, please give me building specific advice",
        },
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="yes, please give me building specific advice",
        metadata={},
        thread_id="thread-recent-brf-building-specific-followup",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_our_energy_class_still_requires_building_specific_flow():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "What is our energy class?"}],
        last_message="What is our energy class?",
        metadata={},
        thread_id="thread-our-energy-class",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_brf_name_building_data_request_overrides_generic_classifier_label():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "general"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "We are BRF Sjöstaden, what ventilation system do we have?",
            }
        ],
        last_message="We are BRF Sjöstaden, what ventilation system do we have?",
        metadata={},
        thread_id="thread-brf-override",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["agent_answered"] == "building"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_brf_energy_advice_with_concrete_brf_routes_to_building_resolution_not_handoff():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "expert_handoff"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "I represent brf friskytten. Can you help me with energy advice",
            }
        ],
        last_message="I represent brf friskytten. Can you help me with energy advice",
        metadata={},
        thread_id="thread-brf-energy-advice",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["agent_answered"] == "building"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_explicit_expert_handoff_wins_even_when_brf_is_mentioned():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "I represent BRF Friskytten. Can you send this to an expert?",
            }
        ],
        last_message="I represent BRF Friskytten. Can you send this to an expert?",
        metadata={},
        thread_id="thread-brf-explicit-expert",
    )

    assert response["classification"] == "expert_handoff"
    assert response["route"] == "expert_handoff"
    assert metadata["expert_handoff_pending_confirmation"] is True


def test_brf_measure_recommendation_with_concrete_brf_routes_to_building_resolution():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "We are BRF Friskytten. Can you recommend measures?",
            }
        ],
        last_message="We are BRF Friskytten. Can you recommend measures?",
        metadata={},
        thread_id="thread-brf-recommend-measures",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_recommend_measures_followup_after_wrong_handoff_prompt_uses_prior_brf_context():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "expert_handoff"
    StubBuildingAgent.last_call = None
    router = module.AgentRouter()

    messages = [
        {
            "role": "user",
            "content": "I represent brf friskytten. Can you help me with energy advice",
        },
        {
            "role": "assistant",
            "content": "I can email this conversation and the available session details to an EKR expert. Do you want me to send it?",
            "classification": "expert_handoff",
        },
        {
            "role": "user",
            "content": "No, I mean can you recommend measures",
        },
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="No, I mean can you recommend measures",
        metadata={"expert_handoff_pending_confirmation": True},
        thread_id="thread-recommend-measures-after-handoff",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call is not None
    assert StubBuildingAgent.last_call[2]["expert_handoff_pending_confirmation"] is False


def test_our_energy_audit_request_overrides_expert_handoff_classifier_label():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "expert_handoff"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "Our bed would like to have an energy audit",
            }
        ],
        last_message="Our bed would like to have an energy audit",
        metadata={},
        thread_id="thread-energy-audit",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_building_id_message_overrides_generic_classifier_label():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "general"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "use this building 01-80-BRAEDGAARDEN9-2",
            }
        ],
        last_message="use this building 01-80-BRAEDGAARDEN9-2",
        metadata={},
        thread_id="thread-building-id",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["agent_answered"] == "building"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_building_id_with_property_colon_overrides_generic_classifier_label():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "use building 24-82-BURTRAESKS-GAMMELBYN71:4-1",
            }
        ],
        last_message="use building 24-82-BURTRAESKS-GAMMELBYN71:4-1",
        metadata={},
        thread_id="thread-building-id-colon",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}


def test_personal_energy_efficiency_request_without_building_identity_stays_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubGenericAgent.next_response = "general efficiency answer"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "user",
                "content": "How do I improve my energy efficiency?",
            }
        ],
        last_message="How do I improve my energy efficiency?",
        metadata={},
        thread_id="thread-personal-energy",
    )

    assert response["content"] == "general efficiency answer"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}


def test_generic_heating_bills_constraints_do_not_force_building_identity():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "generic district heating constraints answer"
    router = module.AgentRouter()

    messages = [
        {"role": "user", "content": "We have high heating bills."},
        {
            "role": "assistant",
            "classification": "generic",
            "content": "Start with meter checks, heating curve review, and radiator balancing.",
        },
        {
            "role": "user",
            "content": "The building is from the 60s and has district heating.",
        },
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="The building is from the 60s and has district heating.",
        metadata={},
        thread_id="thread-generic-heating-constraints",
    )

    assert response["content"] == "generic district heating constraints answer"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}
    assert StubBuildingAgent.last_call is None


def test_generic_heating_bills_next_step_followup_stays_generic_without_resolved_building():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "generic first step answer"
    router = module.AgentRouter()

    messages = [
        {"role": "user", "content": "We have high heating bills."},
        {
            "role": "assistant",
            "classification": "generic",
            "content": "Start with a general diagnostic sequence.",
        },
        {"role": "user", "content": "The building is from the 60s and has district heating."},
        {
            "role": "assistant",
            "classification": "generic",
            "content": "That points toward controls, balancing, and substation checks.",
        },
        {"role": "user", "content": "What should we do first?"},
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="What should we do first?",
        metadata={},
        thread_id="thread-generic-heating-next-step",
    )

    assert response["content"] == "generic first step answer"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}
    assert StubBuildingAgent.last_call is None


def test_generic_geothermal_constraint_followup_stays_generic_without_resolved_building():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    StubGenericAgent.next_response = "generic geothermal feasibility answer"
    router = module.AgentRouter()

    messages = [
        {"role": "user", "content": "Can we install geothermal?"},
        {
            "role": "assistant",
            "classification": "generic",
            "content": "Feasibility depends on ground conditions, space, permits, and heat demand.",
        },
        {
            "role": "user",
            "content": "We're a multi-family BRF, and the courtyard is small.",
        },
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="We're a multi-family BRF, and the courtyard is small.",
        metadata={},
        thread_id="thread-generic-geothermal-constraints",
    )

    assert response["content"] == "generic geothermal feasibility answer"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}
    assert StubBuildingAgent.last_call is None


def test_routes_generic_requests_with_structured_sources(monkeypatch):
    module = import_agent_router_module()
    from tests.unit.test_source_link_registry import registry
    monkeypatch.setattr(module, "resolve_source_links", registry.resolve_source_links)
    StubRouterAgent.next_classification = "generic"
    StubGenericAgent.next_response = {
        "content": "generic answer",
        "sources": [
            {
                "name": "BRF Energieffektiv 2015",
                "filename": "brfenergieffektiv_2015.pdf",
                "link": "https://example.com/brfenergieffektiv_2015.pdf",
            }
        ],
    }
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "general insulation tips"}],
        last_message="general insulation tips",
        metadata={"kept": True},
        thread_id="thread-1b",
    )

    assert response["content"] == "generic answer"
    assert response["classification"] == "generic"
    assert response["agent_answered"] == "generic"
    assert response["route"] == "generic"
    assert response["role"] == "assistant"
    assert len(response["sources"]) == 1
    assert response["sources"][0]["name"] == "BRF Energieffektiv 2015"
    assert response["sources"][0]["filename"] == "brfenergieffektiv_2015.pdf"
    assert response["sources"][0]["link"] == "https://eef.se/wp-content/uploads/2018/01/brfenergieffektiv_2015.pdf"
    assert response["sources"][0]["link_status"] == "verified"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"kept": True}


def test_routes_generic_requests_even_after_building_specific_turn():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubGenericAgent.next_response = "generic answer"
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

    assert response["content"] == "generic answer"
    assert response["classification"] == "generic"
    assert response["agent_answered"] == "generic"
    assert response["route"] == "generic"
    assert response["role"] == "assistant"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"existing": 1}
    assert StubGenericAgent.last_call == (
        "and more broadly?",
        [
            {"role": "assistant", "classification": "building_specific"},
            {"role": "user", "content": "and more broadly?"},
        ],
    )


def test_relevant_ecm_followup_after_building_context_routes_to_building_flow():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    messages = [
        {"role": "user", "content": "what are the relevant epc metrics for Öregrundsgatan 9"},
        {
            "role": "assistant",
            "classification": "building_specific",
            "content": "Building ID: 01-80-LISSABON2-2\n\nEnergy class: F",
        },
        {"role": "user", "content": "so, what are the relevant ecms"},
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="so, what are the relevant ecms",
        metadata={},
        thread_id="thread-ecm-followup",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call[0] == "so, what are the relevant ecms"


def test_building_info_followup_after_building_context_routes_to_building_flow():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    messages = [
        {"role": "user", "content": "Do we have district heating? I live in professorsslingan 10"},
        {
            "role": "assistant",
            "classification": "building_specific",
            "content": "Building ID: 01-80-FYSIKERN1-1\n\nDistrict heating.",
            "metadata": {
                "address": "professorsslingan 10",
                "byggnadsid": "01-80-FYSIKERN1-1",
            },
        },
        {"role": "user", "content": "give me information about building"},
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="give me information about building",
        metadata={"address": "professorsslingan 10"},
        thread_id="thread-building-info-followup",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call[0] == "give me information about building"


def test_contentful_followup_after_building_context_routes_to_building_flow():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    messages = [
        {"role": "user", "content": "I live in Artemisgatan 17. What is the energy class?"},
        {
            "role": "assistant",
            "classification": "building_specific",
            "content": "Building ID: 01-80-SKYTTEN2-2\n\nEnergy class: F.",
            "metadata": {
                "address": "Artemisgatan 17",
                "byggnadsid": "01-80-SKYTTEN2-2",
            },
        },
        {"role": "user", "content": "please explain more"},
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="please explain more",
        metadata={"address": "Artemisgatan 17"},
        thread_id="thread-contentful-followup",
    )

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call[0] == "please explain more"


def test_small_talk_after_building_context_stays_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubGenericAgent.next_response = "you are welcome"
    router = module.AgentRouter()

    messages = [
        {
            "role": "assistant",
            "classification": "building_specific",
            "content": "Building ID: 01-80-SKYTTEN2-2\n\nEnergy class: F.",
            "metadata": {
                "address": "Artemisgatan 17",
                "byggnadsid": "01-80-SKYTTEN2-2",
            },
        },
        {"role": "user", "content": "thanks"},
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="thanks",
        metadata={"address": "Artemisgatan 17"},
        thread_id="thread-small-talk-after-building",
    )

    assert response["content"] == "you are welcome"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": "Artemisgatan 17"}


def test_data_explanation_followup_after_building_context_routes_to_building_flow():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    message = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
    messages = [
        {"role": "user", "content": "What is the energy class for professorsslingan 10?"},
        {
            "role": "assistant",
            "classification": "building_specific",
            "content": "Building ID: 01-80-FYSIKERN1-1\nEnergy class 2019: G\nEnergy class 2020: F",
            "metadata": {
                "address": "professorsslingan 10",
                "byggnadsid": "01-80-FYSIKERN1-1",
            },
        },
        {"role": "user", "content": message},
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message=message,
        metadata={"address": "professorsslingan 10"},
        thread_id="thread-building-data-followup",
    )

    assert module._looks_like_address(message) is False
    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call[0] == message


def test_general_ecm_followup_after_building_context_stays_generic():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    StubGenericAgent.next_response = "generic ecm answer"
    router = module.AgentRouter()

    messages = [
        {"role": "assistant", "classification": "building_specific", "content": "Building ID: 01-80-LISSABON2-2"},
        {"role": "user", "content": "what are ECMs in general?"},
    ]

    response, metadata = router.route_message(
        messages=messages,
        last_message="what are ECMs in general?",
        metadata={},
        thread_id="thread-general-ecm",
    )

    assert response["content"] == "generic ecm answer"
    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {}


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
    assert response["route"] == "combined"
    assert response["role"] == "assistant"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": ["street 1"]}
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
    assert response["route"] == "combined"
    assert response["role"] == "assistant"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"simulation_results": ["done"]}


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
    assert response["route"] == "generic"
    assert response["role"] == "assistant"
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"session": "x"}


def test_routes_draft_report_requests():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
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
    assert response["route"] == "report_generation"
    assert response["downloadable_report"] == {"report_id": "rep-1"}
    assert {key: value for key, value in metadata.items() if key != "response_language"} == {"address": "Main Street 1"}
    assert StubDraftReportService.last_call == (
        "thread-7",
        [{"role": "user", "content": "prepare a draft report"}],
        {"address": "Main Street 1", "response_language": "en"},
    )


def test_report_requests_clear_stale_expert_handoff_confirmation():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "expert_handoff"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "can you give me the energy report?"}],
        last_message="can you give me the energy report?",
        metadata={"expert_handoff_pending_confirmation": True, "address": "Main Street 1"},
        thread_id="thread-8",
    )

    assert response["classification"] == "draft_energy_report"
    assert response["route"] == "report_generation"
    assert metadata["expert_handoff_pending_confirmation"] is False


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
        "content": "Jag kunde inte avgöra hur frågan ska hanteras. Kan du formulera om den?",
        "classification": "conversational",
        "agent_answered": "router",
        "route": "clarification",
    }
    assert metadata["clarification"]["needed"] is True
    assert metadata["clarification"]["reason"] == "classification_failed"


def test_out_of_scope_questions_are_handled_safely():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[{"role": "user", "content": "Can our BRF legally force residents to pay?"}],
        last_message="Can our BRF legally force residents to pay?",
        metadata={},
        thread_id="thread-9",
    )

    assert response["classification"] == "out_of_scope"
    assert response["route"] == "out_of_scope"
    assert metadata["out_of_scope"] is True
    assert metadata["out_of_scope_type"] == "legal_advice"


def test_contact_details_update_does_not_use_building_context_or_claim_update():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "building_specific"
    StubBuildingAgent.last_call = None
    router = module.AgentRouter()

    response, metadata = router.route_message(
        messages=[
            {
                "role": "assistant",
                "classification": "building_specific",
                "content": "Building ID: 01-80-HEDVIG15-1",
                "metadata": {
                    "address": "Bennebolsgatan 34",
                    "byggnadsid": "01-80-HEDVIG15-1",
                },
            },
            {
                "role": "user",
                "content": "We changed board members--can you update our contact details?",
            },
        ],
        last_message="We changed board members--can you update our contact details?",
        metadata={"address": "Bennebolsgatan 34", "byggnadsid": "01-80-HEDVIG15-1"},
        thread_id="thread-contact-update",
    )

    assert response["classification"] == "out_of_scope"
    assert response["route"] == "out_of_scope"
    assert response["agent_answered"] == "boundary"
    assert "cannot update external registers" in response["content"]
    assert "cannot perform the update" in response["content"]
    assert "Building ID" not in response["content"]
    assert "Once you send these details" not in response["content"]
    assert metadata["out_of_scope"] is True
    assert metadata["out_of_scope_type"] == "external_contact_or_register_update"
    assert StubBuildingAgent.last_call is None


@pytest.mark.parametrize("value,expected", [
    ("building_specific", "building_specific"),
    ("generic", "generic"),
    ("conversational", "conversational"),
    ("cluster", "cluster"),
    ("draft_energy_report", "draft_energy_report"),
    ("expert_handoff", "expert_handoff"),
    (" \ngeneric\t", "generic"),
    ("GENERIC", "generic"),
    ("general", "generic"),
    ("building", "building_specific"),
    ("building-specific", "building_specific"),
    ("draft_report", "draft_energy_report"),
])
def test_classifier_boundary_accepts_only_complete_supported_labels(value, expected):
    module = import_agent_router_module()
    assert module._normalize_classification(value) == expected


INVALID_CLASSIFIER_OUTPUTS = [
    None, "", " ", "mystery", "generic.", "generic,",
    "This question is generic", "classification: generic", "generic because this is advice",
    "generic\nbuilding_specific", "generic | conversational", "generic generic",
    "draft_energy_report, generic", "expert_handoff or generic",
    "I recommend expert_handoff", "draft_energy_report because it would help",
    '"generic"', "`generic`", "```generic```", '{"label":"generic"}',
    ["generic"], {"classification": "generic"}, b"generic", 0, True,
]


@pytest.mark.parametrize("value", INVALID_CLASSIFIER_OUTPUTS)
def test_classifier_boundary_rejects_prose_multiple_labels_and_non_strings(value):
    module = import_agent_router_module()
    assert module._normalize_classification(value) is None


@pytest.mark.parametrize("invalid_output", INVALID_CLASSIFIER_OUTPUTS)
def test_invalid_classifier_output_cannot_create_report_or_handoff(monkeypatch, invalid_output):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, "next_classification", invalid_output)
    monkeypatch.setattr(StubGenericAgent, "next_response", "Insulation slows heat loss.")
    monkeypatch.setattr(StubBuildingAgent, "last_call", None)
    monkeypatch.setattr(StubClusterAgent, "last_call", None)
    monkeypatch.setattr(StubDraftReportService, "last_call", None)

    def unexpected_action(*args, **kwargs):
        pytest.fail("Invalid classifier output initiated a report or handoff")

    monkeypatch.setattr(module, "generate_draft_report_response", unexpected_action)
    monkeypatch.setattr(module, "prepare_expert_handoff", unexpected_action)
    monkeypatch.setattr(module, "send_expert_handoff_email", unexpected_action)
    question = "In general, how does insulation reduce energy use?"
    response, metadata = module.AgentRouter().route_message(
        [{"role": "user", "content": question}], question, {}, "invalid-output-no-action",
    )

    assert response["classification"] == "generic"
    assert response["route"] == "generic"
    assert response["content"] == "Insulation slows heat loss."
    assert not response.get("downloadable_report")
    assert not metadata.get("expert_handoff_pending_confirmation")
    assert StubBuildingAgent.last_call is None
    assert StubClusterAgent.last_call is None
    assert StubDraftReportService.last_call is None


@pytest.mark.parametrize("previous_label", [
    "building_specific", "draft_energy_report", "expert_handoff", "cluster",
    "generic", "expert_handoff because an advisor is needed", None,
])
def test_invalid_classification_cannot_reuse_previous_route_for_new_general_topic(monkeypatch, previous_label):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, "next_classification", None)
    monkeypatch.setattr(StubRouterAgent, "last_call", None)
    monkeypatch.setattr(StubGenericAgent, "next_response", "General solar guidance.")
    monkeypatch.setattr(StubBuildingAgent, "last_call", None)
    monkeypatch.setattr(StubClusterAgent, "last_call", None)

    def unexpected_action(*args, **kwargs):
        pytest.fail("A previous route was reused after the user changed topic")

    monkeypatch.setattr(module, "generate_draft_report_response", unexpected_action)
    monkeypatch.setattr(module, "prepare_expert_handoff", unexpected_action)
    monkeypatch.setattr(module, "send_expert_handoff_email", unexpected_action)
    question = "New topic: in general, what should a BRF consider before installing solar panels?"
    messages = [
        {"role": "user", "content": "Please consider my earlier request."},
        {"role": "assistant", "classification": previous_label, "content": "Earlier answer."},
        {"role": "user", "content": question},
    ]
    response, _ = module.AgentRouter().route_message(
        messages, question, {"address": "Tulegatan 5A", "byggnadsid": "01-80-HAELSAN10-2"},
        "invalid-classification-new-topic",
    )

    assert StubRouterAgent.last_call == (question, previous_label)
    assert response["classification"] == "generic"
    assert response["content"] == "General solar guidance."
    assert StubBuildingAgent.last_call is None
    assert StubClusterAgent.last_call is None


@pytest.mark.parametrize("previous_label", [
    "building_specific", "draft_energy_report", "expert_handoff", "cluster", "mystery",
])
@pytest.mark.parametrize("question", ["??", "", "  "])
def test_invalid_classification_with_unclear_current_input_requests_clarification(monkeypatch, previous_label, question):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, "next_classification", None)
    monkeypatch.setattr(StubRouterAgent, "last_call", None)

    def unexpected_dispatch(*args, **kwargs):
        pytest.fail("Unclear input inherited an earlier classification")

    monkeypatch.setattr(StubBuildingAgent, "handle_building_query", unexpected_dispatch)
    monkeypatch.setattr(StubGenericAgent, "handle_generic_input", unexpected_dispatch)
    monkeypatch.setattr(StubClusterAgent, "handle_cluster_query", unexpected_dispatch)
    monkeypatch.setattr(module, "generate_draft_report_response", unexpected_dispatch)
    monkeypatch.setattr(module, "prepare_expert_handoff", unexpected_dispatch)
    monkeypatch.setattr(module, "send_expert_handoff_email", unexpected_dispatch)
    messages = [
        {"role": "user", "content": "Earlier request."},
        {"role": "assistant", "classification": previous_label, "content": "Earlier answer."},
        {"role": "user", "content": question},
    ]
    response, metadata = module.AgentRouter().route_message(
        messages, question, {}, "invalid-classification-clarification",
    )

    assert response["classification"] == "conversational"
    assert response["agent_answered"] == "router"
    assert response["route"] == "clarification"
    assert metadata["clarification"]["needed"] is True
    assert metadata["clarification"]["reason"] == "classification_failed"


@pytest.mark.parametrize("reply", ["yes", "no", "okay", "tack"])
@pytest.mark.parametrize("previous_label", ["building_specific", "draft_energy_report", "expert_handoff", "cluster"])
def test_invalid_classifier_neutral_reply_does_not_repeat_previous_action(monkeypatch, reply, previous_label):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, "next_classification", None)
    monkeypatch.setattr(StubConversationalAgent, "last_call", None)

    def unexpected_action(*args, **kwargs):
        pytest.fail("An acknowledgment repeated the previous building route or action")

    monkeypatch.setattr(StubBuildingAgent, "handle_building_query", unexpected_action)
    monkeypatch.setattr(StubClusterAgent, "handle_cluster_query", unexpected_action)
    monkeypatch.setattr(module, "generate_draft_report_response", unexpected_action)
    monkeypatch.setattr(module, "prepare_expert_handoff", unexpected_action)
    monkeypatch.setattr(module, "send_expert_handoff_email", unexpected_action)
    messages = [
        {"role": "user", "content": "An earlier request."},
        {"role": "assistant", "classification": previous_label, "content": "Earlier answer."},
        {"role": "user", "content": reply},
    ]
    response, metadata = module.AgentRouter().route_message(
        messages, reply, {}, "neutral-reply-invalid-classifier",
    )

    assert response["classification"] == "conversational"
    assert response["route"] == "generic"
    assert not response.get("downloadable_report")
    assert not metadata.get("expert_handoff_pending_confirmation")


@pytest.mark.parametrize("next_label,question,expected_agent", [
    ("generic", "Give me general energy tips.", "generic"),
    (None, "In general, how does insulation reduce energy use?", "generic"),
    (None, "hello", "fast_conversational"),
])
def test_successful_turn_clears_prior_classifier_clarification(monkeypatch, next_label, question, expected_agent):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, "next_classification", None)
    monkeypatch.setattr(StubGenericAgent, "next_response", "Useful general energy guidance.")
    router = module.AgentRouter()
    messages = [{"role": "user", "content": "??"}]
    first_response, first_metadata = router.route_message(
        messages, "??", {"kept": "session-value"}, "classifier-clarification-followup",
    )
    assert first_metadata["clarification"]["needed"] is True
    assert first_metadata["clarification"]["reason"] == "classification_failed"
    monkeypatch.setattr(StubRouterAgent, "next_classification", next_label)
    messages.extend([first_response, {"role": "user", "content": question}])

    response, metadata = router.route_message(
        messages, question, first_metadata, "classifier-clarification-followup",
    )

    assert response["agent_answered"] == expected_agent
    assert response["route"] == "generic"
    assert not metadata.get("clarification", {}).get("needed")
    assert metadata.get("clarification", {}).get("reason") != "classification_failed"
    assert metadata["kept"] == "session-value"
    # Routing owns a copy and must not rewrite the previous turn's evidence.
    assert first_metadata["clarification"]["needed"] is True
    assert first_metadata["clarification"]["reason"] == "classification_failed"


def test_repeated_invalid_turn_creates_fresh_classifier_clarification(monkeypatch):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, "next_classification", None)
    original = {
        "kept": "session-value",
        "clarification": {
            "needed": True, "reason": "classification_failed", "question_asked": "Old question",
            "resolved": True, "resolved_after_turns": 9, "stale_marker": "remove",
        },
    }
    response, metadata = module.AgentRouter().route_message(
        [{"role": "user", "content": "??"}], "??", original, "repeated-invalid-classifier",
    )

    assert response["route"] == "clarification"
    assert metadata["clarification"] == {
        "needed": True, "reason": "classification_failed", "question_asked": response["content"],
        "resolved": False, "resolved_after_turns": None,
    }
    assert metadata["kept"] == "session-value"
    assert original["clarification"]["question_asked"] == "Old question"
    assert original["clarification"]["stale_marker"] == "remove"


@pytest.mark.parametrize("reason", ["missing_address", "ambiguous_address"])
def test_classifier_recovery_preserves_genuine_address_clarification(monkeypatch, reason):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, "next_classification", None)
    monkeypatch.setattr(StubGenericAgent, "next_response", "General insulation guidance.")
    clarification = {
        "needed": True, "reason": reason, "question_asked": "Which building do you mean?",
        "resolved": False, "address_marker": "keep",
    }
    question = "In general, how does insulation reduce energy use?"
    response, metadata = module.AgentRouter().route_message(
        [{"role": "user", "content": question}], question,
        {"clarification": clarification, "address": "Ringvägen 10"},
        "preserve-address-clarification",
    )

    assert response["classification"] == "generic"
    assert metadata["clarification"] == clarification


def test_invalid_classifier_unrelated_prefaced_topic_cannot_use_selected_building(monkeypatch):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, "next_classification", None)

    def unexpected_building(*args, **kwargs):
        pytest.fail("An unrelated topic inherited the old selected building")

    monkeypatch.setattr(StubBuildingAgent, "handle_building_query", unexpected_building)
    question = "Also, what are your opening hours?"
    messages = [
        {"role": "user", "content": "What energy class does Tulegatan 5A have?"},
        {"role": "assistant", "classification": "building_specific",
         "content": "Selected building ID: 01-80-HAELSAN10-2."},
        {"role": "user", "content": question},
    ]
    response, metadata = module.AgentRouter().route_message(
        messages, question, {"address": "Tulegatan 5A", "byggnadsid": "01-80-HAELSAN10-2"},
        "prefaced-unrelated-topic",
    )

    assert response["classification"] == "conversational"
    assert response["route"] == "clarification"
    assert metadata["clarification"]["reason"] == "classification_failed"
