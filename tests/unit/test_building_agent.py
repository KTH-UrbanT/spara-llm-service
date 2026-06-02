from tests.support import fresh_import, stub_module


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
    )
    return fresh_import("src.agents.building_agent")


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
        last_message="question",
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

