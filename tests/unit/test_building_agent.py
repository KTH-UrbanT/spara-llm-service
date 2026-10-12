from tests.support import fresh_import, stub_module
from src.services.source_link_registry import guard_response_links, verified_link_context
import pytest


class FakeGraph:
    result = None
    error = None
    received_state = None

    def invoke(self, state):
        type(self).received_state = state
        if type(self).error is not None:
            raise type(self).error
        return type(self).result


class SessionStoreRecorder:
    calls = []

    @classmethod
    def update(cls, thread_id, state):
        cls.calls.append((thread_id, state))


class SessionStoreLoader:
    state = {}


class SourceLinkResolver:
    calls = []
    result = []

    @classmethod
    def resolve(cls, source_keys):
        cls.calls.append(list(source_keys))
        return cls.result


class DirectMultiAddressResolver:
    result = None
    calls = []

    @classmethod
    def resolve(cls, last_message, metadata):
        cls.calls.append((last_message, metadata))
        return cls.result


def import_building_agent_module():
    SessionStoreRecorder.calls = []
    SessionStoreLoader.state = {}
    FakeGraph.received_state = None
    SourceLinkResolver.calls = []
    SourceLinkResolver.result = []
    DirectMultiAddressResolver.calls = []
    DirectMultiAddressResolver.result = None
    stub_module(
        "src.agents.building_flow_graph",
        build_building_flow_graph=lambda: FakeGraph(),
        resolve_multi_address_identity_message=DirectMultiAddressResolver.resolve,
    )
    stub_module(
        "src.redis.redis_session_store",
        get_session_state=lambda thread_id: SessionStoreLoader.state,
        update_session_state=SessionStoreRecorder.update,
    )
    stub_module(
        "src.services.source_link_registry",
        resolve_source_links=SourceLinkResolver.resolve,
        guard_response_links=guard_response_links,
        verified_link_context=verified_link_context,
    )
    return fresh_import("src.agents.building_agent")


def test_new_lookup_context_does_not_restore_abandoned_snapshot():
    module = import_building_agent_module()
    FakeGraph.error = None
    FakeGraph.result = {"final_response": "new building answer", "metadata": {"address": "Ringvägen 10"}}
    SessionStoreLoader.state = [{
        "metadata": {"brf_name": "Okänd", "brf_resolution": {"status": "not_found"}},
        "context": {"parsed_intent": "SQL database"},
    }]
    agent = module.BuildingAgent()
    response, metadata = agent.handle_building_query(
        "Ringvägen 10", [{"role": "user", "content": "Ringvägen 10"}],
        {"routing_context_start": 2, "address": "Ringvägen 10"}, "after-reset",
    )
    assert FakeGraph.received_state["session_state"] == {}
    assert "brf_name" not in DirectMultiAddressResolver.calls[-1][1]
    assert metadata["routing_context_start"] == 2
    assert SessionStoreRecorder.calls[-1][1]["metadata"]["routing_context_start"] == 2

    SessionStoreLoader.state = SessionStoreRecorder.calls[-1][1]
    FakeGraph.result = {"final_response": "follow-up answer", "metadata": {"address": "Ringvägen 10"}}
    agent.handle_building_query(
        "Visa energiklassen", [{"role": "user", "content": "Visa energiklassen"}],
        metadata, "after-reset",
    )
    assert FakeGraph.received_state["session_state"]["metadata"]["address"] == "Ringvägen 10"


def test_handle_building_query_returns_composed_response_and_updates_session():
    module = import_building_agent_module()
    FakeGraph.result = {
        "final_response": "Use insulation and heat recovery.",
        "context": {
            "parsed_intent": "SQL database ; vector database",
            "intent_list": ["SQL database", "vector database"],
        },
        "metadata": {"debug": {"agent_answered": "hybrid"}},
        "done_generic_sql": True,
        "done_specialized_sql": False,
        "done_vector": True,
    }
    FakeGraph.error = None

    agent = module.BuildingAgent()
    response, metadata = agent.handle_building_query(
        last_message="How do I improve this building?",
        messages=[{"role": "user", "content": "How do I improve this building?"}],
        metadata={"address": ["Main Street 1"]},
        thread_id="thread-1",
        _session_state={"step": "existing"},
    )

    assert FakeGraph.received_state == {
        "thread_id": "thread-1",
        "messages": [{"role": "user", "content": "How do I improve this building?"}],
        "metadata": {"address": ["Main Street 1"]},
        "last_message": "How do I improve this building?",
        "session_state": {"step": "existing"},
    }
    assert SessionStoreRecorder.calls == [("thread-1", FakeGraph.result)]
    assert response == {
        "content": "Use insulation and heat recovery.",
        "classification": "building_specific",
        "route": "combined",
        "parsed_intent": "SQL database ; vector database",
        "intent_list": ["SQL database", "vector database"],
        "agent_answered": ["ODEN API", "Documents stored in Vector database used"],
    }
    assert metadata == {
        "debug": {"agent_answered": "hybrid"},
        "context": {
            "parsed_intent": "SQL database ; vector database",
            "intent_list": ["SQL database", "vector database"],
        },
    }


def test_handle_building_query_fast_resolves_two_address_identity_before_graph():
    module = import_building_agent_module()
    DirectMultiAddressResolver.result = {
        "final_response": (
            "Building ID: 01-80-SKYTTEN2-2\n\n"
            "Artemisgatan 17 and Nimrodsgatan 7 resolve to the same building record."
        ),
        "metadata": {
            "byggnadsid": "01-80-SKYTTEN2-2",
            "address": "Artemisgatan 17",
            "alternate_addresses": ["Nimrodsgatan 7"],
            "clarification": {"needed": False},
        },
        "multi_address_resolution_complete": True,
    }

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
        messages=[
            {
                "role": "user",
                "content": "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
            }
        ],
        metadata={},
        thread_id="thread-two-addresses",
    )

    assert FakeGraph.received_state is None
    assert DirectMultiAddressResolver.calls == [
        (
            "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
            {},
        )
    ]
    assert SessionStoreRecorder.calls == [("thread-two-addresses", DirectMultiAddressResolver.result)]
    assert response["route"] == "building_specific"
    assert response["content"].startswith("Building ID: 01-80-SKYTTEN2-2")
    assert response["agent_answered"] == ["ODEN API"]
    assert metadata["byggnadsid"] == "01-80-SKYTTEN2-2"


def test_f08_address_explanation_uses_saved_identity_without_rechecking_addresses():
    module = import_building_agent_module()
    building_id = "01-80-TAPETEN2-1"
    stored_metadata = {
        "byggnadsid": building_id,
        "building_id": building_id,
        "address": "Ringvägen 10",
        "requested_address": "Ringvägen 10",
        "epc_record_address": "Hornsgatan 104",
        "same_building_multiple_addresses": True,
        "building_identity_check": {"status": "passed", "matched_building_id": building_id},
        "retrieved_facts": {"byggnadsid": building_id, "energy_performance": 142},
    }
    FakeGraph.error = None
    FakeGraph.result = {
        "final_response": "Hornsgatan 104 är adressen i energideklarationen för samma byggnadsid.",
        "metadata": stored_metadata,
    }
    question = "Varför visas Hornsgatan 104 när jag frågade om Ringvägen 10?"
    response, metadata = module.BuildingAgent().handle_building_query(
        question, [{"role": "user", "content": question}],
        {"response_language": "sv"}, "verified-alias-followup",
        _session_state={"metadata": stored_metadata},
    )
    assert DirectMultiAddressResolver.calls == []
    assert FakeGraph.received_state["last_message"] == question
    assert FakeGraph.received_state["metadata"]["requested_address"] == "Ringvägen 10"
    assert FakeGraph.received_state["metadata"]["epc_record_address"] == "Hornsgatan 104"
    assert FakeGraph.received_state["metadata"]["byggnadsid"] == building_id
    assert response["route"] == "building_specific"
    assert metadata["byggnadsid"] == building_id


def test_handle_building_query_passes_session_metadata_to_direct_multi_address_resolver():
    module = import_building_agent_module()
    DirectMultiAddressResolver.result = {
        "final_response": "Building ID: 01-80-HALVOEN1-3",
        "metadata": {
            "byggnadsid": "01-80-HALVOEN1-3",
            "clarification": {"needed": False},
        },
        "multi_address_resolution_complete": True,
    }

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="Our property has two addresses: Hammarby Allé 163 and Hammarby Allé 165. Which one do you use?",
        messages=[
            {
                "role": "user",
                "content": "Our property has two addresses: Hammarby Allé 163 and Hammarby Allé 165. Which one do you use?",
            }
        ],
        metadata={"request_id": "current-turn"},
        thread_id="thread-brf-context",
        _session_state={
            "metadata": {
                "brf_name": "Sjöstaden 1",
                "brf_resolution": {
                    "status": "resolved_by_user_selection",
                    "selected_building_id": "01-80-HALVOEN1-3",
                },
            }
        },
    )

    assert DirectMultiAddressResolver.calls == [
        (
            "Our property has two addresses: Hammarby Allé 163 and Hammarby Allé 165. Which one do you use?",
            {
                "brf_name": "Sjöstaden 1",
                "brf_resolution": {
                    "status": "resolved_by_user_selection",
                    "selected_building_id": "01-80-HALVOEN1-3",
                },
                "request_id": "current-turn",
                "selected_brf_building_id": "01-80-HALVOEN1-3",
                "byggnadsid": "01-80-HALVOEN1-3",
            },
        )
    ]
    assert FakeGraph.received_state is None
    assert response["content"] == "Building ID: 01-80-HALVOEN1-3"
    assert metadata["byggnadsid"] == "01-80-HALVOEN1-3"


def test_unified_addresses_continue_original_multipart_request_through_graph():
    module = import_building_agent_module()
    question = "Tulegatan 5A och Rådmansgatan 31 i Stockholm avser samma hus. Vilket värmesystem har huset och vilka åtgärder bör vi prioritera?"
    building_id = "01-80-HAELSAN10-2"
    parts = [
        {"question": "Vilket värmesystem har huset?", "kind": "fact", "sources": ["SQL database"]},
        {"question": "Vilka åtgärder bör vi prioritera?", "kind": "advice", "sources": ["vector database"]},
    ]
    original_context = {
        "parsed_intent": "SQL database ; vector database",
        "intent_list": ["SQL database", "vector database"],
        "request_parts": parts,
        "answer_focus": question,
    }
    resolved_metadata = {
        "address": "Tulegatan 5A",
        "alternate_addresses": ["Rådmansgatan 31"],
        "address_location_hint": "Stockholm",
        "byggnadsid": building_id,
        "selected_address_building_id": building_id,
        "request_parts": parts,
        "clarification": {"needed": False, "resolved": True},
    }
    DirectMultiAddressResolver.result = {
        "final_response": "Byggnadsid: " + building_id + ". Adresserna avser samma byggnad.",
        "metadata": resolved_metadata,
        "multi_address_resolution_complete": False,
    }
    FakeGraph.error = None
    FakeGraph.result = {
        "final_response": "Byggnaden har fjärrvärme. Kontrollera styrning och temperaturer först.",
        "metadata": dict(resolved_metadata),
        "context": original_context,
        "done_generic_sql": True,
        "done_vector": True,
    }
    messages = [{"role": "user", "content": question}]
    response, metadata = module.BuildingAgent().handle_building_query(
        question, messages, {"routing_context_start": 2}, "alias-facts-advice",
        _session_state={
            "metadata": {"routing_context_start": 2, "byggnadsid": "OLD-ID", "retrieved_facts": {"energy_class": "G"}},
            "selected_address_building_id": "OLD-ID",
            "building_id": "OLD-ID",
            "50a_uuid": "old-uuid",
            "agent_data": {"generic_sql": [{"byggnadsid": "OLD-ID"}]},
            "query_traces": {"generic_sql": {"returned_values_used": {"byggnadsid": "OLD-ID"}}},
            "context": original_context,
        },
    )
    received = FakeGraph.received_state
    assert received["last_message"] == question
    assert received["messages"] == messages
    assert received["multi_address_resolution_checked"] is True
    assert received["multi_address_resolution_complete"] is False
    assert received["metadata"]["selected_address_building_id"] == building_id
    assert received["metadata"]["request_parts"] == parts
    assert received["session_state"]["context"] == original_context
    assert received["session_state"]["metadata"] == received["metadata"]
    assert "retrieved_facts" not in received["session_state"]["metadata"]
    assert "selected_address_building_id" not in received["session_state"]
    assert "building_id" not in received["session_state"]
    assert "50a_uuid" not in received["session_state"]
    assert "agent_data" not in received["session_state"]
    assert "query_traces" not in received["session_state"]
    assert response["route"] == "combined"
    assert response["content"] == FakeGraph.result["final_response"]
    assert metadata["selected_address_building_id"] == building_id
    assert metadata["context"]["request_parts"] == parts
    assert SessionStoreRecorder.calls == [("alias-facts-advice", FakeGraph.result)]
    assert SessionStoreRecorder.calls[-1][1]["metadata"]["selected_address_building_id"] == building_id


def test_direct_resolution_without_completion_flag_remains_terminal():
    module = import_building_agent_module()
    DirectMultiAddressResolver.result = {
        "final_response": "Building ID: 01-80-HAELSAN10-2",
        "metadata": {"byggnadsid": "01-80-HAELSAN10-2", "clarification": {"needed": False}},
    }
    response, _ = module.BuildingAgent().handle_building_query(
        "Do these two addresses identify the same building?", [], {}, "legacy-alias-result",
    )
    assert FakeGraph.received_state is None
    assert response["content"] == DirectMultiAddressResolver.result["final_response"]


def test_ambiguous_multi_address_parses_original_request_before_terminal_choices():
    module = import_building_agent_module()
    question = "Compare heating for Ringvägen 10 in Stockholm and Kungsgatan 10 in Växjö, and explain what we should prioritize."
    choices = "I found several buildings. Which building ID should I use?"
    original_context = {
        "parsed_intent": "SQL database ; vector database",
        "intent_list": ["SQL database", "vector database"],
        "request_parts": [
            {"question": "Compare heating for the two buildings.", "kind": "comparison", "sources": ["SQL database"]},
            {"question": "Explain what we should prioritize.", "kind": "advice", "sources": ["vector database"]},
        ],
        "answer_focus": question,
    }
    pending_metadata = {
        "address_references": [{"address": "Ringvägen 10", "city": "Stockholm"}, {"address": "Kungsgatan 10", "city": "Växjö"}],
        "pending_address_query": {"user_message": question, "context": {}},
        "building_candidate_matches": [{"byggnadsid": "01-80-CANDIDATE-1"}, {"byggnadsid": "07-80-CANDIDATE-2"}],
        "clarification": {"needed": True, "reason": "ambiguous_address", "question_asked": choices},
    }
    DirectMultiAddressResolver.result = {
        "final_response": choices,
        "metadata": pending_metadata,
        "multi_address_resolution_complete": True,
    }
    completed_metadata = {
        **pending_metadata,
        "pending_address_query": {"user_message": question, "context": original_context},
    }
    FakeGraph.error = None
    FakeGraph.result = {"final_response": choices, "metadata": completed_metadata, "context": original_context}
    messages = [{"role": "user", "content": question}]
    response, metadata = module.BuildingAgent().handle_building_query(question, messages, {}, "multi-pending-context")
    received = FakeGraph.received_state
    assert received["last_message"] == question
    assert received["messages"] == messages
    assert received["metadata"] == pending_metadata
    assert received["session_state"]["metadata"] == pending_metadata
    assert received["multi_address_resolution_checked"] is True
    assert received["multi_address_resolution_complete"] is True
    assert received["final_response"] == choices
    assert response["route"] == "clarification"
    assert response["content"] == choices
    assert metadata["pending_address_query"]["context"]["request_parts"] == original_context["request_parts"]
    assert metadata["context"]["intent_list"] == ["SQL database", "vector database"]
    assert SessionStoreRecorder.calls == [("multi-pending-context", FakeGraph.result)]


def test_selected_address_identity_is_carried_from_session_and_hidden_when_unresolved():
    module = import_building_agent_module()
    selected = "01-80-HAELSAN10-2"
    metadata = module._direct_resolution_metadata({}, {"selected_address_building_id": selected})
    assert metadata["selected_address_building_id"] == selected
    assert "selected_address_building_id" not in module._metadata_without_unresolved_selection(metadata)


def test_cached_candidate_explanation_skips_intent_parser_without_consuming_pending_request():
    module = import_building_agent_module()
    original = "Which heating system is registered for Tulegatan 5A and Rådmansgatan 31?"
    question = "Vilka byggnadsid hittade du för respektive adress?"
    pending = {
        "response_language": "sv",
        "pending_address_query": {"user_message": original, "context": {"answer_focus": original}},
        "building_candidate_matches": [{"byggnadsid": "01-80-HAELSAN10-1"}, {"byggnadsid": "01-80-HAELSAN10-2"}],
        "clarification": {"needed": True, "reason": "ambiguous_address"},
    }
    DirectMultiAddressResolver.result = {
        "final_response": "Tulegatan 5A: 01-80-HAELSAN10-2; Rådmansgatan 31: 01-80-HAELSAN10-1, 01-80-HAELSAN10-2",
        "metadata": pending, "multi_address_resolution_complete": True, "cached_candidate_answer": True,
    }
    FakeGraph.error = AssertionError("A candidate explanation must not run the intent graph")
    try:
        response, metadata = module.BuildingAgent().handle_building_query(
            question, [{"role": "user", "content": original}, {"role": "user", "content": question}],
            pending, "candidate-explanation",
        )
    finally:
        FakeGraph.error = None
    assert FakeGraph.received_state is None
    assert response["route"] == "clarification"
    assert "01-80-HAELSAN10-2" in response["content"]
    assert metadata["pending_address_query"] == pending["pending_address_query"]
    assert metadata["clarification"]["needed"] is True
    assert SessionStoreRecorder.calls[-1][1]["metadata"]["pending_address_query"]["user_message"] == original


def test_public_metadata_retains_selected_address_identity_only_when_resolved():
    import_building_agent_module()
    metadata_module = fresh_import("src.pipeline.evaluation_metadata")
    selected = "01-80-HAELSAN10-2"
    metadata = {"selected_address_building_id": selected, "byggnadsid": selected}
    answer = {"content": "The building uses district heating.", "classification": "building_specific", "route": "building_specific"}
    payload = metadata_module.build_message_metadata(answer, metadata)
    assert payload["selected_address_building_id"] == selected
    answer = {"content": "Which building ID should I use?", "classification": "building_specific", "route": "clarification"}
    payload = metadata_module.build_message_metadata(answer, {**metadata, "clarification": {"needed": True, "reason": "ambiguous_address"}})
    assert "selected_address_building_id" not in payload


def test_handle_building_query_falls_back_across_response_fields():
    module = import_building_agent_module()
    FakeGraph.result = {
        "response": "Fallback response",
        "context": {},
        "metadata": {},
        "done_generic_sql": False,
        "done_specialized_sql": True,
        "done_vector": False,
    }
    FakeGraph.error = None

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="question",
        messages=[],
        metadata={},
        thread_id="thread-2",
    )

    assert response["content"] == "Fallback response"
    assert response["route"] == "building_specific"
    assert response["agent_answered"] == ["Hammarby dataset used"]
    assert metadata == {}


def test_handle_building_query_marks_full_address_prompt_as_clarification():
    module = import_building_agent_module()
    FakeGraph.result = {
        "final_response": "To give building-specific advice safely, I need the full building address.",
        "context": {"parsed_intent": "SQL database ; vector database"},
        "metadata": {},
    }
    FakeGraph.error = None

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="How do I improve my energy efficiency?",
        messages=[],
        metadata={},
        thread_id="thread-address",
    )

    assert response["route"] == "clarification"
    assert metadata["clarification"]["reason"] == "missing_address"


@pytest.mark.parametrize("done_vector, expected_route", [(False, "building_specific"), (True, "combined")])
def test_verified_selected_identity_survives_explanation_of_prior_address_ambiguity(done_vector, expected_route):
    module = import_building_agent_module()
    building_id = "01-80-HAELSAN10-2"
    # This paragraph is from the actual post-selection SV_05 live answer. It
    # explains prior ambiguity and triggers the legacy text-only route check.
    answer = (
        "Rådmansgatan 31 ger dock även träff på ett annat byggnadsid, så den adressen ensam är inte entydig. "
        "Ditt val klargör vilken byggnad frågan gäller. Mitt tidigare besked om att adresserna avsåg olika byggnader var missvisande.\n\n"
        "Registrerat värmesystem är fjärrvärme, både för uppvärmning och varmvatten."
    )
    assert module.ADDRESS_DISAMBIGUATION_REQUEST_RE.search(answer)
    facts = {"byggnadsid": building_id, "heating_system": "district heating"}
    FakeGraph.error = None
    FakeGraph.result = {
        "final_response": answer,
        "context": {"parsed_intent": "SQL database"},
        "metadata": {
            "byggnadsid": building_id,
            "selected_address_building_id": building_id,
            "address": "Tulegatan 5A",
            "clarification": {"needed": False, "resolved": True},
            "building_identity_check": {"status": "passed", "matched_building_id": building_id},
            "retrieved_facts": facts,
        },
        "done_generic_sql": True,
        "done_vector": done_vector,
    }
    response, metadata = module.BuildingAgent().handle_building_query(
        building_id, [{"role": "user", "content": building_id}], {}, "sv05-selected-identity",
    )

    assert response["route"] == expected_route
    assert metadata["clarification"]["needed"] is False
    assert metadata["byggnadsid"] == building_id
    assert metadata["selected_address_building_id"] == building_id
    assert metadata["retrieved_facts"] == facts
    metadata_module = fresh_import("src.pipeline.evaluation_metadata")
    public_metadata = metadata_module.build_message_metadata(response, metadata)
    assert public_metadata["route"] == expected_route
    assert public_metadata["needs_clarification"] is False
    assert public_metadata["building_id"] == building_id
    assert public_metadata["selected_address_building_id"] == building_id
    assert public_metadata["retrieved_facts"] == facts


@pytest.mark.parametrize("identity_check", [None, {}, {"status": "missing"}, {"status": "ambiguous"}])
def test_false_clarification_without_verified_identity_keeps_legacy_address_request_fallback(identity_check):
    module = import_building_agent_module()
    metadata = {"clarification": {"needed": False}}
    if identity_check is not None:
        metadata["building_identity_check"] = identity_check
    state = {"metadata": metadata, "done_generic_sql": True, "done_vector": True}
    assert module.BuildingAgent._derive_route(state, "Please provide the full street address.") == "clarification"


def test_explicit_clarification_needed_wins_even_with_passed_identity_and_completed_retrieval():
    module = import_building_agent_module()
    state = {
        "metadata": {
            "clarification": {"needed": True},
            "building_identity_check": {"status": "passed"},
        },
        "done_generic_sql": True,
        "done_vector": True,
    }
    assert module.BuildingAgent._derive_route(state, "The selected building uses district heating.") == "clarification"


def test_handle_building_query_preserves_ambiguous_address_clarification_reason():
    module = import_building_agent_module()
    question = (
        "I found more than one possible building match for that address. "
        "Please provide the city, postcode, municipality, BRF name, or exact building ID so I use the correct building."
    )
    FakeGraph.result = {
        "final_response": question,
        "context": {"parsed_intent": "SQL database", "ambiguous": False},
        "metadata": {
            "address": "Ringvägen 10",
            "clarification": {
                "needed": True,
                "reason": "ambiguous_address",
                "question_asked": question,
                "resolved": False,
                "resolved_after_turns": None,
            },
            "building_identity_check": {"status": "ambiguous"},
        },
    }
    FakeGraph.error = None

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="I live at Ringvägen 10. What is the building's energy performance?",
        messages=[],
        metadata={},
        thread_id="thread-ambiguous-address",
    )

    assert response["route"] == "clarification"
    assert metadata["clarification"]["reason"] == "ambiguous_address"
    assert metadata["clarification"]["question_asked"] == question
    assert metadata["building_identity_check"]["status"] == "ambiguous"


def test_handle_building_query_does_not_promote_candidate_id_on_clarification():
    module = import_building_agent_module()
    question = "Please provide the city, postcode, municipality, BRF name, or exact building ID."
    FakeGraph.result = {
        "final_response": question,
        "context": {"parsed_intent": "SQL database"},
        "metadata": {
            "address": "Examplegatan 10",
            "clarification": {
                "needed": True,
                "reason": "ambiguous_address",
                "question_asked": question,
                "resolved": False,
            },
            "building_identity_check": {
                "status": "ambiguous",
                "candidate_building_ids": ["01-60-CANDIDATE-1", "18-80-CANDIDATE-2"],
            },
            "building_candidate_matches": [
                {
                    "byggnadsid": "01-60-CANDIDATE-1",
                    "epc_idadr": "Examplegatan 10",
                    "epc_idkommun": "Täby",
                },
                {
                    "byggnadsid": "18-80-CANDIDATE-2",
                    "epc_idadr": "Examplegatan 10",
                    "epc_idkommun": "Örebro",
                },
            ],
        },
    }
    FakeGraph.error = None

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="i live in Örebro",
        messages=[],
        metadata={},
        thread_id="thread-ambiguous-candidates",
    )

    assert response["route"] == "clarification"
    assert "byggnadsid" not in metadata
    assert "building_id" not in metadata
    assert "retrieved_facts" not in metadata
    assert metadata["building_identity_check"]["candidate_building_ids"] == [
        "01-60-CANDIDATE-1",
        "18-80-CANDIDATE-2",
    ]


def test_handle_building_query_includes_vector_sources_from_merged_agent_data():
    module = import_building_agent_module()
    SourceLinkResolver.result = [
        {
            "name": "BRF Energieffektiv 2015",
            "filename": "brfenergieffektiv_2015.pdf",
            "link": "https://example.com/brfenergieffektiv_2015.pdf",
        }
    ]
    FakeGraph.result = {
        "final_response": "Energy class information",
        "context": {},
        "metadata": {},
        "done_generic_sql": False,
        "done_specialized_sql": False,
        "done_vector": True,
        "agent_data": {
            "vector": {
                "sources": [r"C:\docs\brfenergieffektiv_2015.pdf"],
            }
        },
    }
    FakeGraph.error = None

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="What is energy class?",
        messages=[{"role": "user", "content": "What is energy class?"}],
        metadata={},
        thread_id="thread-vector",
    )

    assert response["sources"] == SourceLinkResolver.result
    assert response["route"] == "building_specific"
    assert SourceLinkResolver.calls == [[r"C:\docs\brfenergieffektiv_2015.pdf"]]
    assert metadata == {
        "agent_data": {
            "vector": {
                "sources": [r"C:\docs\brfenergieffektiv_2015.pdf"],
            }
        }
    }


def test_handle_building_query_loads_session_state_when_not_provided():
    module = import_building_agent_module()
    SessionStoreLoader.state = {"metadata": {"byggnadsid": "01-80-FILOSOFEN2-3"}}
    FakeGraph.result = {
        "final_response": "Building ID: 01-80-FILOSOFEN2-3\n\nResponse",
        "context": {},
        "metadata": {"byggnadsid": "01-80-FILOSOFEN2-3"},
        "done_generic_sql": False,
        "done_specialized_sql": False,
        "done_vector": False,
    }
    FakeGraph.error = None

    module.BuildingAgent().handle_building_query(
        last_message="question",
        messages=[],
        metadata={},
        thread_id="thread-from-store",
    )

    assert FakeGraph.received_state["session_state"] == {"metadata": {"byggnadsid": "01-80-FILOSOFEN2-3"}}


def test_handle_building_query_returns_error_payload_when_graph_fails():
    module = import_building_agent_module()
    FakeGraph.result = None
    FakeGraph.error = RuntimeError("graph exploded")

    response, metadata = module.BuildingAgent().handle_building_query(
        last_message="What is the energy performance?",
        messages=[],
        metadata={"x": 1},
        thread_id="thread-3",
    )

    assert response["content"] == "An error occurred while handling your building-related request: graph exploded"
    assert response["classification"] == "building_specific"
    assert response["parsed_intent"] is None
    assert response["agent_answered"] == []
    assert response["vector_sources"] == []
    assert metadata == {"x": 1}


def test_building_failure_is_readable_in_swedish():
    module = import_building_agent_module()
    FakeGraph.error = RuntimeError("graph exploded")
    try:
        response, _ = module.BuildingAgent().handle_building_query(
            "Vilken energiklass har vi?", [], {"response_language": "sv"}, "sv-error",
        )
        assert "Försök igen" in response["content"]
        assert "graph exploded" not in response["content"]
    finally:
        FakeGraph.error = None


@pytest.mark.parametrize("clarification", [None, {}, {"resolved": True}, {"needed": False}])
def test_verified_long_advice_and_diagnostic_question_keep_building_scope(clarification):
    module = import_building_agent_module()
    selected = "01-80-NORRADJURGAARDEN1:49-25"
    answer = ("Review the building heating, ventilation and controls first. " * 12
              + "Which BRF operating schedule applies, and have the building systems been changed?")
    facts = {"byggnadsid": selected, "energy_class": "C", "heating_system": "district heating"}
    metadata = {"byggnadsid": selected, "retrieved_facts": facts, "building_identity_check": {"status": "passed", "matched_building_id": selected}}
    if clarification is not None:
        metadata["clarification"] = clarification
    FakeGraph.error = None
    FakeGraph.result = {"final_response": answer, "metadata": metadata, "done_generic_sql": True, "done_vector": True}
    response, returned = module.BuildingAgent().handle_building_query("What should my building prioritize?", [], {}, "verified-advice")
    assert response["route"] == "combined"
    assert returned["clarification"]["needed"] is False
    assert returned["retrieved_facts"] == facts
    assert SessionStoreRecorder.calls[-1][1]["metadata"] == returned


@pytest.mark.parametrize("reason", ["diagnostic_question", "diagnostic_followup", "missing_measurements", "missing_operational_data"])
def test_confirmed_building_diagnostic_clarification_does_not_clear_identity(reason):
    module = import_building_agent_module()
    selected = "01-80-FILOSOFEN2-2"
    question = "Have the systems changed, and which rooms are warm?"
    FakeGraph.error = None
    FakeGraph.result = {"final_response": question, "metadata": {
        "byggnadsid": selected, "retrieved_facts": {"byggnadsid": selected, "energy_class": "B"},
        "building_identity_check": {"status": "passed", "matched_building_id": selected},
        "clarification": {"needed": True, "reason": reason, "question_asked": question},
    }, "done_generic_sql": True}
    response, metadata = module.BuildingAgent().handle_building_query("What should we do first?", [], {}, "diagnostic")
    assert response["route"] == "building_specific"
    assert metadata["clarification"]["needed"] is False
    assert metadata["diagnostic_question"] == question
    assert metadata["byggnadsid"] == selected
    assert metadata["retrieved_facts"]["energy_class"] == "B"
    assert SessionStoreRecorder.calls[-1][1]["metadata"] == metadata


def test_long_unverified_advice_is_not_an_identity_request_from_prose_alone():
    module = import_building_agent_module()
    answer = "The building needs a review before choosing which BRF equipment to replace in this city. " * 15
    assert module.BuildingAgent._derive_route({"metadata": {}, "done_vector": True}, answer) == "building_specific"


def test_unresolved_identity_clears_returned_and_persisted_candidate_facts():
    module = import_building_agent_module()
    selected = "01-80-OLD1-1"
    stale = {"byggnadsid": selected, "energy_class": "G"}
    FakeGraph.error = None
    FakeGraph.result = {"final_response": "Which building ID should I use?", "metadata": {
        "byggnadsid": selected, "retrieved_facts": stale,
        "building_identity_check": {"status": "passed", "matched_building_id": selected},
        "clarification": {"needed": True, "reason": "ambiguous_address"},
        "request_parts": [{"question": "What is the energy class?", "kind": "fact", "sources": ["SQL database"]}],
    }, "byggnadsid": selected, "aggregated_data": {"generic_sql": stale}, "agent_data_generic": [stale]}
    response, metadata = module.BuildingAgent().handle_building_query("Ringvägen 10", [{"role": "user", "content": "Ringvägen 10"}], {}, "ambiguous-state")
    assert response["route"] == "clarification"
    assert metadata["building_identity_check"]["status"] == "ambiguous"
    assert "matched_building_id" not in metadata["building_identity_check"]
    assert "retrieved_facts" not in metadata
    persisted = SessionStoreRecorder.calls[-1][1]
    assert persisted["metadata"] == metadata
    assert "byggnadsid" not in persisted
    assert "aggregated_data" not in persisted
    assert "agent_data_generic" not in persisted
    assert metadata["request_parts"][0]["question"] == "What is the energy class?"


def test_building_selection_reports_real_elapsed_clarification_turns():
    module = import_building_agent_module()
    FakeGraph.error = None
    FakeGraph.result = {"final_response": "Which city?", "metadata": {"clarification": {"needed": True, "reason": "ambiguous_address"}}}
    messages = [{"role": "user", "content": "Ringvägen 10"}]
    _, pending = module.BuildingAgent().handle_building_query("Ringvägen 10", messages, {}, "count-selection")
    assert pending["clarification_started_user_turn"] == 1
    messages += [{"role": "assistant", "content": "Which city?"}, {"role": "user", "content": "Stockholm"}]
    selected = "01-80-TAPETEN2-1"
    FakeGraph.result = {"final_response": "District heating.", "metadata": {
        "byggnadsid": selected, "building_identity_check": {"status": "passed", "matched_building_id": selected},
        "clarification": {"needed": False, "resolved": True, "resolved_after_turns": 0},
    }, "done_generic_sql": True}
    _, resolved = module.BuildingAgent().handle_building_query("Stockholm", messages, pending, "count-selection", _session_state={"metadata": pending})
    assert resolved["clarification"]["resolved_after_turns"] == 1
    assert resolved["clarification_resolved_user_turn"] == 2
    assert SessionStoreRecorder.calls[-1][1]["metadata"] == resolved

