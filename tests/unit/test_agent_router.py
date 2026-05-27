from tests.support import fresh_import, stub_module


class StubRouterAgent:
    next_classification = "generic"
    last_call = None

    def wants_draft_report(self, message):
        return "report" in message.lower()

    def wants_expert_handoff(self, message):
        lowered = message.lower()
        return "expert" in lowered or "ekr" in lowered

    def is_confirmation(self, message):
        normalized = message.strip().lower()
        return normalized == "yes"

    def is_rejection(self, message):
        normalized = message.strip().lower()
        return normalized == "no"

    def classify_question(self, message, previous_classification):
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
        send_expert_handoff_email=lambda thread_id, messages, metadata: True,
    )
    return fresh_import("src.pipeline.agent_router")


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
    assert metadata == {"kept": True}
    assert StubGenericAgent.last_call == (
        "give me general energy tips",
        [{"role": "user", "content": "give me general energy tips"}],
    )


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
    assert metadata == {"kept": True}
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
    assert metadata == {"address": ["street 1"]}
    assert StubBuildingAgent.last_call is not None
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
    assert metadata == {"kept": True}


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
    assert metadata == {"address": ["street 1"]}


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
    assert metadata == {"address": ["street 1"]}


def test_brf_energy_advice_overrides_expert_handoff_classifier_label():
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
    assert metadata == {"address": ["street 1"]}


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


def test_brf_measure_recommendation_overrides_generic_classifier_label():
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
    assert metadata == {"address": ["street 1"]}


def test_recommend_measures_followup_after_wrong_handoff_prompt_routes_to_building_agent():
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
    assert metadata == {"address": ["street 1"]}
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
    assert metadata == {"address": ["street 1"]}


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
    assert metadata == {"address": ["street 1"]}


def test_personal_energy_efficiency_request_overrides_generic_classifier_label():
    module = import_agent_router_module()
    StubRouterAgent.next_classification = "generic"
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

    assert response["content"] == "building answer"
    assert response["classification"] == "building_specific"
    assert response["route"] == "combined"
    assert metadata == {"address": ["street 1"]}


def test_routes_generic_requests_with_structured_sources():
    module = import_agent_router_module()
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
    assert response["sources"] == [
        {
            "name": "BRF Energieffektiv 2015",
            "filename": "brfenergieffektiv_2015.pdf",
            "link": "https://example.com/brfenergieffektiv_2015.pdf",
        }
    ]
    assert metadata == {"kept": True}


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
    assert metadata == {"existing": 1}
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
    assert metadata == {"address": ["street 1"]}
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
    assert metadata == {"address": ["street 1"]}
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
    assert metadata == {"address": ["street 1"]}
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
    assert metadata == {"address": "Artemisgatan 17"}


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
    assert metadata == {"address": ["street 1"]}
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
    assert metadata == {}


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
    assert metadata == {"address": ["street 1"]}
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
    assert metadata == {"simulation_results": ["done"]}


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
    assert metadata == {"session": "x"}


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
    assert metadata == {"address": "Main Street 1"}
    assert StubDraftReportService.last_call == (
        "thread-7",
        [{"role": "user", "content": "prepare a draft report"}],
        {"address": "Main Street 1"},
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
        "content": "Unknown classification: mystery",
        "classification": "mystery",
        "agent_answered": "unknown",
        "route": "generic",
    }
    assert metadata == {}


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
