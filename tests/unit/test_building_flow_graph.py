import copy
import json

import pytest

from tests.support import fresh_import, stub_module


class DummyStateGraph:
    def __init__(self, *args, **kwargs):
        pass


class DummyParseIntentAgent:
    result = {"context": {}}

    def __call__(self, state):
        return type(self).result


class DummySQLMapperLayer:
    def execute(self, *args, **kwargs):
        return {"ok": True, "data": []}


@pytest.mark.parametrize('fragment', ['en budget på 50', 'inte 50', 'budget 20000', 'temperature 20'])
def test_financial_and_measurement_fragments_never_become_graph_addresses(fragment):
    module = import_building_flow_graph_module()
    assert module._extract_address_candidate_from_text(fragment) is None


@pytest.mark.parametrize("message", [
    "Byt till 00-00-PILOTFINNSEJ999-0. Vilken energiklass har den?",
    "Gå tillbaka till 01-80-KAAKENHUSEN40-1 och visa energiklassen.",
    "Byt till 00-00", "Gå tillbaka till 01-80",
])
def test_building_id_switch_commands_never_become_street_addresses(message):
    module = import_building_flow_graph_module()
    assert module._extract_address_candidate_from_text(message) is None
    assert module.normalize_address_candidate(message, allow_four_digit_number=True) is None


@pytest.mark.parametrize("reply, expected", [
    ("Jag menade Stockholm.", "Stockholm"),
    ("Ja, Stockholm.", "Stockholm"),
    ("Jag bor i Stockholm.", "Stockholm"),
    ("Jag menar kommunen Stockholm.", "Stockholm"),
    ("I meant Stockholm.", "Stockholm"),
    ("Rättelse: Stockholm.", "Stockholm"),
    ("Jakobsberg", "Jakobsberg"),
])
def test_municipality_corrections_preserve_complete_words(reply, expected):
    module = import_building_flow_graph_module()
    assert module._extract_location_only_hint_from_text(reply) == expected


@pytest.mark.parametrize("message, existing", [
    ("Sammanfatta rapportutkastet.", True),
    ("Ladda ner rapportutkastet.", True),
    ("Sammanfatta energirapportutkastet.", True),
    ("Sammanfatta rapportutkastet till ett nytt styrelseunderlag.", False),
    ("Sammanfatta rapportutkastet, inte till ett nytt styrelseunderlag.", True),
])
def test_existing_draft_reading_is_distinct_from_creating_new_output(message, existing):
    module = import_building_flow_graph_module()
    assert module.requests_existing_document(message) is existing


def test_final_round_id_switch_unknown_and_return_replace_pending_request(monkeypatch):
    module = import_building_flow_graph_module()
    known_id = "01-80-KAAKENHUSEN40-1"
    unknown_id = "00-00-PILOTFINNSEJ999-0"
    calls = []

    def execute(query, **kwargs):
        calls.append((query, kwargs))
        assert query == "buildings_by_building_id"
        building_id = kwargs["building_id"]
        return {"ok": building_id == known_id,
                "data": [{"byggnadsid": known_id, "epc_egienergiklass2020_calc": "B"}] if building_id == known_id else [],
                "trace": {"query_type": query, "building_id": building_id,
                          "execution_status": "success" if building_id == known_id else "not_found"}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    old_metadata = {"byggnadsid": known_id, "building_id": known_id, "building_id_from_user": known_id,
                    "address": "Brunnsgatan 9", "retrieved_facts": {"building_id": known_id, "energy_class": "B"},
                    "building_identity_check": {"status": "passed", "matched_building_id": known_id}}
    unknown_question = f"Byt till {unknown_id}. Vilken energiklass har den?"
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {
        "address": "Byt till 00-00", "intents": ["SQL database"], "answer_focus": unknown_question,
    }})
    state = module.understand_context_node({
        "last_message": unknown_question, "messages": [], "metadata": copy.deepcopy(old_metadata),
        "session_state": {"metadata": copy.deepcopy(old_metadata), "context": {"address": "Brunnsgatan 9"}},
    })
    assert state["metadata"]["building_id_from_user"] == unknown_id
    assert "address" not in state["metadata"]
    assert "address" not in state["context"]
    assert "retrieved_facts" not in state["metadata"]
    missing = module.generic_sql_agent_node(state)
    assert missing["metadata"]["clarification"]["reason"] == "building_not_found_by_id"
    pending = module.clarification_node({**state, **missing})
    assert pending["metadata"]["pending_building_request"]["question"] == unknown_question
    return_question = f"Gå tillbaka till {known_id} och visa energiklassen."
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {
        "address": "Gå tillbaka till 01-80", "intents": ["SQL database"], "answer_focus": unknown_question,
        "request_parts": [{"question": unknown_question, "kind": "fact", "sources": ["SQL database"]}],
    }})
    returned = module.understand_context_node({
        "last_message": return_question, "messages": [], "metadata": pending["metadata"],
        "session_state": {"metadata": pending["metadata"], "context": state["context"]},
    })
    assert returned["metadata"]["building_id_from_user"] == known_id
    for stale in ("address", "retrieved_facts", "clarification", "pending_building_request"):
        assert stale not in returned["metadata"]
    assert returned["context"]["answer_focus"] == return_question
    assert returned["context"]["effective_query"] == return_question
    assert returned["context"]["request_parts"] == [{"question": return_question, "kind": "fact", "sources": ["SQL database"]}]
    result = module.generic_sql_agent_node(returned)
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == known_id
    assert result["agent_data_generic"][0]["byggnadsid"] == known_id
    assert calls == [("buildings_by_building_id", {"building_id": unknown_id}),
                     ("buildings_by_building_id", {"building_id": known_id})]


def test_replacement_bare_id_rebinds_failed_request_without_old_identifier(monkeypatch):
    module = import_building_flow_graph_module()
    unknown_id = "00-00-PILOTFINNSEJ999-0"
    selected_id = "01-80-KAAKENHUSEN40-1"
    question = f"Vilken energiklass har byggnadsid {unknown_id}?"
    context = {"parsed_intent": "SQL database", "intent_list": ["SQL database"],
               "answer_focus": question, "effective_query": question,
               "request_parts": [{"question": question, "kind": "fact", "sources": ["SQL database"]}]}
    metadata = {"building_id_from_user": unknown_id, "byggnadsid": unknown_id,
                "clarification": {"needed": True, "reason": "building_not_found_by_id"},
                "pending_building_request": {"question": question, "context": context}}
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {"intents": ["SQL database"]}})
    result = module.understand_context_node({
        "last_message": selected_id, "messages": [], "metadata": metadata,
        "session_state": {"metadata": metadata, "context": context},
    })
    expected = question.replace(unknown_id, selected_id)
    assert result["metadata"]["building_id_from_user"] == selected_id
    assert result["context"]["answer_focus"] == expected
    assert result["context"]["effective_query"] == expected
    assert result["context"]["request_parts"][0]["question"] == expected
    assert unknown_id not in json.dumps(result["metadata"]["pending_building_request"])


@pytest.mark.parametrize("ok", [True, False])
def test_unknown_user_id_is_not_replaced_by_an_old_address_lookup(monkeypatch, ok):
    module = import_building_flow_graph_module()
    unknown_id = "00-00-PILOTFINNSEJ999-0"
    calls = []

    def execute(query, **kwargs):
        calls.append((query, kwargs))
        return {"ok": ok, "data": [], "trace": {"query_type": query, "execution_status": "not_found", "building_id": unknown_id}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    result = module.generic_sql_agent_node({
        "last_message": f"Vilken energiklass har {unknown_id}?",
        "metadata": {"byggnadsid": unknown_id, "building_id_from_user": unknown_id, "address": "Ringvägen 10"},
        "context": {"intent_list": ["SQL database"]},
    })
    assert calls == [("buildings_by_building_id", {"building_id": unknown_id})]
    assert result["metadata"]["clarification"]["reason"] == "building_not_found_by_id"
    assert result["identity_gate_blocked"] is True


@pytest.mark.parametrize('status,expected_reason', [
    ('not_found', 'building_not_found_by_id'), ('error', 'building_lookup_failed'),
])
def test_id_lookup_failure_is_not_described_as_an_address_failure(monkeypatch, status, expected_reason):
    module = import_building_flow_graph_module()
    building_id = '00-00-PILOTFINNSEJ999-0'
    monkeypatch.setattr(module.sql_mapper_layer, 'execute', lambda *a, **k: {
        'ok': False, 'data': [], 'trace': {'query_type': 'buildings_by_building_id', 'execution_status': status, 'building_id': building_id},
    })
    result = module.generic_sql_agent_node({
        'last_message': f'Jag väljer byggnadsid {building_id}',
        'metadata': {'address': 'Ringvägen 10', 'byggnadsid': building_id, 'building_id_from_user': building_id},
        'context': {'intent_list': ['SQL database']},
    })
    assert result['metadata']['clarification']['reason'] == expected_reason


class DummyVectorClientConfig:
    pass


class DummyVectorClient:
    def __init__(self, *args, **kwargs):
        self.hits = []

    def query(self, question: str):
        return list(self.hits)


class DummyOpenAIResponseAgent:
    def generate_response(self, prompt, message_list=None, *, language=None):
        return "Energy class is a rating of a building's energy performance."


@pytest.mark.parametrize(("question", "fact_question", "advice_question", "model_answer"), [
    ("Which heating system do we have, and what does that mean for priorities?", "Which heating system do we have?", "What does that mean for priorities?", "You have district heating. For priorities, check control settings and reported uneven temperatures before ranking investments."),
    ("What is our energy performance, and is that good or bad?", "What is our energy performance?", "Is that good or bad?", "The recorded performance is 201 kWh/m2-year. The declared class G indicates poor performance in that declaration; it does not prove current operating performance."),
])
def test_multipart_interpretation_survives_fact_renderer(question, fact_question, advice_question, model_answer):
    module = import_building_flow_graph_module()
    prompts = []
    module.llm_summarizer.generate_response = lambda prompt, **kwargs: prompts.append(prompt) or model_answer
    parts = [{"question": fact_question, "kind": "fact", "sources": ["SQL database"]},
             {"question": advice_question, "kind": "interpretation", "sources": ["vector database"]}]
    result = module.llm_summarizer_node({"last_message": question, "messages": [],
        "context": {"request_parts": parts, "intent_list": ["SQL database", "vector database"]},
        "metadata": {"building_id_from_user": "01-80-SKYTTEN2-2"},
        "agent_data_generic": [{"byggnadsid": "01-80-SKYTTEN2-2", "epc_egienergiklass": "G", "epc_egienergiprestanda": 201, "epc_huvudsakliguppvarmning_calc": "Fjarrvarme", "epc_godkand": "2018-01-01"}]})
    assert model_answer in result["final_response"]
    assert advice_question in prompts[0]
    assert "direct_fact_response_targeted" not in str(result["metadata"].get("response_fallback"))


@pytest.mark.parametrize("reply", ["Ringvägen 10", "01-80-TAPETEN2-1"])
def test_identity_clarification_preserves_all_request_parts_and_retrieval_query(monkeypatch, reply):
    module = import_building_flow_graph_module()
    question = "Which heating system do we have, and what should we prioritize?"
    parts = [{"question": "Which heating system do we have?", "kind": "fact", "sources": ["SQL database"]},
             {"question": "What should we prioritize?", "kind": "advice", "sources": ["vector database"]}]
    context = {"request_parts": parts, "intent_list": ["SQL database", "vector database"], "parsed_intent": "SQL database ; vector database", "answer_focus": question, "effective_query": question}
    pending = module.request_address_node({"last_message": question, "context": context, "messages": [], "metadata": {"response_language": "en"}})
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {"intents": ["SQL database"], "address": None, "ambigious": False}})
    state = module.understand_context_node({"last_message": reply, "messages": [], "metadata": pending["metadata"], "session_state": {"context": context, "metadata": pending["metadata"]}})
    assert state["context"]["request_parts"] == parts
    assert state["context"]["answer_focus"] == question
    assert state["context"]["intent_list"] == ["SQL database", "vector database"]
    queries = []
    module.vector_database.query = lambda query: queries.append(query) or []
    module.vector_db_agent_node(state)
    assert queries == [question]


def test_multipart_generation_failure_lists_unanswered_parts():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: None
    parts = [{"question": "What is our energy class?", "kind": "fact", "sources": ["SQL database"]}, {"question": "What should we prioritize?", "kind": "advice", "sources": ["vector database"]}]
    result = module.llm_summarizer_node({"last_message": "What is our energy class and what should we prioritize?", "context": {"request_parts": parts}, "metadata": {"building_id_from_user": "01-80-SKYTTEN2-2"}, "agent_data_generic": [{"byggnadsid": "01-80-SKYTTEN2-2", "epc_egienergiklass": "G"}]})
    assert "What should we prioritize?" in result["final_response"]
    assert "remains unanswered" in result["final_response"]
    assert result["metadata"]["unanswered_request_parts"] == [{"question": "What should we prioritize?", "kind": "advice"}]


class DummySpecializedSQLLayer:
    pass


def import_building_flow_graph_module():
    graph_module = stub_module("langgraph.graph", StateGraph=DummyStateGraph, START="START", END="END")
    stub_module("langgraph", graph=graph_module)
    stub_module("src.agents.parse_intent_agent", ParseIntentAgent=DummyParseIntentAgent)
    stub_module("src.agents.clarification_agent", generate_clarification=lambda **kwargs: {"question": kwargs["fallback"], "status": "disabled"})
    stub_module("src.agents.generic_sql_layer", SQL_Mapper_Layer=DummySQLMapperLayer)
    stub_module(
        "src.database.vector_client",
        VectorClient=DummyVectorClient,
        VectorClientConfig=DummyVectorClientConfig,
    )
    stub_module("src.agents.openai_agent", OpenAIResponseAgent=DummyOpenAIResponseAgent)
    stub_module("src.agents.specialized_sql_layer", SpecializedSQLLayer=DummySpecializedSQLLayer)
    stub_module("src.database.hammarby_data", query_address=lambda: [])
    return fresh_import("src.agents.building_flow_graph")


@pytest.fixture
def address_selection_flow(monkeypatch):
    module = import_building_flow_graph_module()
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {"address": None, "intents": [], "ambigious": False}})
    # Relevant fields from the supplied ODEN responses, including EPC versions
    # and buildings whose recorded address differs from the search address.
    rows = [
        {"byggnadsid": "01-80-KAAKENHUSEN39-1", "epc_idadr": "Brunnsgatan 9",
         "epc_idkommun": "Stockholm", "epc_idpostnr": "11138", "epc_egiversion": "2010"},
        {"byggnadsid": "01-80-KAAKENHUSEN40-1", "epc_idadr": "Brunnsgatan 9",
         "epc_idkommun": "Stockholm", "epc_idpostnr": "11138", "epc_egiversion": "2019"},
        {"byggnadsid": "01-80-KAAKENHUSEN40-1", "epc_idadr": "Brunnsgatan 9",
         "epc_idkommun": "Stockholm", "epc_idpostnr": "11138", "epc_egiversion": "2023"},
        {"byggnadsid": "07-80-GUNNARGROEPE10-1", "epc_idadr": "Kungsgatan 10",
         "epc_idkommun": "Växjö", "epc_idpostnr": "35233", "epc_egiversion": "2019"},
        {"byggnadsid": "07-80-GUNNARGROEPE9-1", "epc_idadr": "Nygatan 11",
         "epc_idkommun": "Växjö", "epc_idpostnr": "35233", "epc_egiversion": "2010"},
    ]
    ring_rows = [{"byggnadsid": "01-80-TAPETEN2-1", "epc_idadr": "Hornsgatan 104",
                  "epc_idkommun": "Stockholm", "epc_idpostnr": "11726", "epc_egiversion": "2015"}]
    calls = []

    def execute(query, **kwargs):
        calls.append((query, kwargs))
        if query == "buildings_by_building_id":
            data = [row for row in rows + ring_rows if row["byggnadsid"] == kwargs["building_id"]]
        elif kwargs.get("address") == "Ringvägen 10":
            data = ring_rows
        else:
            data = rows
        return {"ok": True, "data": copy.deepcopy(data), "trace": {}}

    module.sql_mapper_layer.execute = execute
    previous = {"metadata": {}, "context": {}}

    def advance(message):
        state = {
            "last_message": message, "messages": [{"role": "user", "content": message}],
            "metadata": copy.deepcopy(previous["metadata"]), "session_state": copy.deepcopy(previous),
            "parallel": {},
        }
        understood = module.understand_context_node(state)
        if understood.get("address_candidate_clarification_pending"):
            # Mirror the graph: unresolved selection replies go directly to
            # clarification, without another address/API lookup.
            lookup = {**module.clarification_node(understood), "identity_gate_blocked": True}
        else:
            lookup = module.generic_sql_agent_node(understood)
        previous.update({"metadata": copy.deepcopy(lookup["metadata"]), "context": copy.deepcopy(understood["context"])})
        return {**understood, **lookup}

    return module, advance, calls


def test_candidate_selection_shared_address_requires_id_and_retains_question(address_selection_flow):
    module, advance, calls = address_selection_flow
    original = "give me information about Kungsgatan 10"
    result = advance(original)
    assert "Which city or municipality" in result["metadata"]["clarification"]["question_asked"]
    result = advance("Stockholm")
    result = advance("111 38")
    question = module.clarification_node(result)["final_response"]
    for building_id in ("01-80-KAAKENHUSEN39-1", "01-80-KAAKENHUSEN40-1"):
        assert question.count(building_id) == 1
    assert "Brunnsgatan 9" in question
    result = advance("Brunnsgatan 9")
    assert result["identity_gate_blocked"] is True
    assert result["metadata"]["building_identity_check"]["matched_building_id"] is None
    result = advance("01-80-kaakenhusen40-1")
    assert result.get("identity_gate_blocked") is not True
    assert calls[-1] == ("buildings_by_building_id", {"building_id": "01-80-KAAKENHUSEN40-1"})
    assert result["context"]["answer_focus"] == original
    assert len(result["agent_data_generic"]) == 1
    assert result["agent_data_generic"][0]["epc_egiversion"] == "2023"
    assert "building_candidate_matches" not in result["metadata"]
    result = advance("what is energy class?")
    assert calls[-1][1]["building_id"] == "01-80-KAAKENHUSEN40-1"


def test_final_round_municipality_correction_reuses_all_candidates(address_selection_flow):
    module, advance, calls = address_selection_flow
    original = "Vilken energiklass har byggnaden på Kungsgatan 10?"
    initial = advance(original)
    assert "flera kommuner" in initial["metadata"]["clarification"]["question_asked"]
    initial_call_count = len(calls)
    unmatched = advance("Jönköping.")
    assert unmatched["metadata"]["generic_sql_trace"]["location_hint_no_match"] is True
    corrected = advance("Jag menade Stockholm.")
    assert len(calls) == initial_call_count
    assert corrected["metadata"]["address_location_hint"] == "Stockholm"
    assert corrected["metadata"]["address"] == "Kungsgatan 10"
    assert corrected["context"]["answer_focus"] == original
    assert "location_hint_no_match" not in corrected["metadata"]["generic_sql_trace"]
    assert {row["byggnadsid"] for row in corrected["metadata"]["building_candidate_matches"]} == {
        "01-80-KAAKENHUSEN39-1", "01-80-KAAKENHUSEN40-1",
    }
    question = corrected["metadata"]["clarification"]["question_asked"]
    for value in ("01-80-KAAKENHUSEN39-1", "01-80-KAAKENHUSEN40-1", "Brunnsgatan 9", "11138"):
        assert value in question
    selected = advance("01-80-KAAKENHUSEN40-1.")
    assert calls[-1] == ("buildings_by_building_id", {"building_id": "01-80-KAAKENHUSEN40-1"})
    assert selected["metadata"]["building_identity_check"]["matched_building_id"] == "01-80-KAAKENHUSEN40-1"
    assert selected["context"]["answer_focus"] == original


def test_verified_address_explanation_keeps_original_lookup_and_identity(monkeypatch):
    module = import_building_flow_graph_module()
    building_id = "01-80-TAPETEN2-1"
    question = "Varför visas Hornsgatan 104 när jag frågade om Ringvägen 10?"
    metadata = {"byggnadsid": building_id, "building_id": building_id,
                "building_id_from_user": building_id, "address": "Ringvägen 10",
                "address_from_user": "Ringvägen 10", "requested_address": "Ringvägen 10",
                "epc_record_address": "Hornsgatan 104", "same_building_multiple_addresses": True,
                "address_location_hint": "Stockholm",
                "building_identity_check": {"status": "passed", "matched_building_id": building_id},
                "retrieved_facts": {"building_id": building_id, "energy_performance": 142}}
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {
        "address": "Hornsgatan 104", "intents": ["SQL database"], "answer_focus": question,
    }})
    state = module.understand_context_node({
        "last_message": question, "messages": [], "metadata": copy.deepcopy(metadata),
        "session_state": {"metadata": copy.deepcopy(metadata), "context": {"address": "Ringvägen 10"}},
    })
    assert state["metadata"]["building_id_from_user"] == building_id
    assert state["metadata"]["building_identity_check"] == metadata["building_identity_check"]
    assert state["metadata"]["retrieved_facts"] == metadata["retrieved_facts"]
    for key in ("address", "address_from_user", "requested_address", "epc_record_address", "same_building_multiple_addresses"):
        assert state["metadata"][key] == metadata[key]
    assert state["context"]["address"] == "Ringvägen 10"
    assert state["context"]["answer_focus"] == question


@pytest.mark.parametrize("reply", ["Nygatan 11", "select Nygatan 11", "07-80-GUNNARGROEPE9-1"])
def test_candidate_selection_retains_alias_building_and_requires_id(address_selection_flow, reply):
    module, advance, calls = address_selection_flow
    advance("give me information about Kungsgatan 10")
    result = advance("Växjö")
    question = result["metadata"]["clarification"]["question_asked"]
    assert "Kungsgatan 10" in question and "Nygatan 11" in question
    assert len(result["metadata"]["building_candidate_matches"]) == 2
    count = len(calls)
    result = advance(reply)
    if reply != "07-80-GUNNARGROEPE9-1":
        assert result["identity_gate_blocked"] is True
        assert result["metadata"]["building_identity_check"]["matched_building_id"] is None
        assert len(calls) == count
        result = advance("07-80-GUNNARGROEPE9-1")
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == "07-80-GUNNARGROEPE9-1"
    assert calls[-1][1]["building_id"] == "07-80-GUNNARGROEPE9-1"


@pytest.mark.parametrize("reply", ["01-80-UNKNOWN1-1", "I choose Unknownsgatan 99"])
def test_candidate_selection_rejects_unknown_choices_and_recovers(address_selection_flow, reply):
    _, advance, calls = address_selection_flow
    advance("give me information about Kungsgatan 10")
    advance("Stockholm")
    count = len(calls)
    result = advance(reply)
    assert result["identity_gate_blocked"] is True
    assert len(calls) == count
    assert "does not identify one of the listed buildings" in result["metadata"]["clarification"]["question_asked"]
    result = advance("111 38")
    assert "building_selection_error" not in result["metadata"]
    result = advance("01-80-KAAKENHUSEN39-1")
    assert result["metadata"]["building_identity_check"]["status"] == "passed"


def test_new_address_while_selecting_starts_a_new_search(address_selection_flow):
    _, advance, calls = address_selection_flow
    advance("give me information about Kungsgatan 10")
    advance("Stockholm")
    count = len(calls)
    result = advance("Ringvägen 10, Stockholm")
    assert len(calls) == count + 1
    assert calls[-1] == ("building_by_address", {"address": "Ringvägen 10"})
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == "01-80-TAPETEN2-1"
    assert "address_candidate_pool" not in result["metadata"]


def test_candidate_selection_blocks_conflicting_city_and_postcode_then_recovers(address_selection_flow):
    _, advance, _ = address_selection_flow
    advance("give me information about Kungsgatan 10")
    advance("Stockholm")
    result = advance("35233")
    assert result["identity_gate_blocked"] is True
    assert "None of the returned buildings match" in result["metadata"]["clarification"]["question_asked"]
    result = advance("11138")
    assert len(result["metadata"]["building_candidate_matches"]) == 2
    assert result["metadata"]["address_location_hint"] == "11138 Stockholm"


def test_candidate_selection_resets_selected_id_for_new_building_search(address_selection_flow):
    _, advance, calls = address_selection_flow
    advance("give me information about Kungsgatan 10")
    advance("Stockholm")
    advance("01-80-KAAKENHUSEN40-1")
    result = advance("give me information about Ringvägen 10, Stockholm")
    assert calls[-1] == ("building_by_address", {"address": "Ringvägen 10"})
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == "01-80-TAPETEN2-1"
    assert "building_id_from_user" not in result["metadata"]
    assert "pending_address_query" not in result["metadata"]


def test_candidate_list_is_complete_and_not_rewritten_by_model(monkeypatch):
    module = import_building_flow_graph_module()
    rows = [{"byggnadsid": f"01-80-TEST{index}-1", "epc_idadr": f"Testgatan {index}",
             "epc_idkommun": "Stockholm", "epc_idpostnr": "11138"} for index in range(8)]
    module.sql_mapper_layer.execute = lambda *a, **kw: {"ok": True, "data": rows, "trace": {}}
    result = module.generic_sql_agent_node({"metadata": {"address": "Testgatan 10", "address_location_hint": "Stockholm"}, "parallel": {}})
    monkeypatch.setattr(module, "generate_clarification", lambda **kw: pytest.fail("Candidate list must not be rewritten"))
    for node in (module.clarification_node, module.request_address_node):
        answer = node(result)["final_response"]
        for row in rows:
            assert row["byggnadsid"] in answer
            assert row["epc_idadr"] in answer
    assert len(result["metadata"]["building_candidate_matches"]) == 8


def test_candidate_selection_id_lookup_rejects_a_different_returned_building(address_selection_flow):
    module, advance, _ = address_selection_flow
    advance("give me information about Kungsgatan 10")
    advance("Stockholm")
    module.sql_mapper_layer.execute = lambda *a, **kw: {
        "ok": True,
        "data": [{"byggnadsid": "01-80-KAAKENHUSEN39-1", "epc_idadr": "Brunnsgatan 9", "epc_idkommun": "Stockholm"}],
        "trace": {},
    }
    result = advance("01-80-KAAKENHUSEN40-1")
    assert result["identity_gate_blocked"] is True
    assert result["metadata"]["building_identity_check"]["status"] == "conflict"
    assert "agent_data_generic" not in result


def test_candidate_selection_empty_lookup_does_not_pass_identity_from_memory(address_selection_flow):
    module, advance, _ = address_selection_flow
    advance("give me information about Kungsgatan 10")
    advance("Stockholm")
    module.sql_mapper_layer.execute = lambda *a, **kw: {"ok": True, "data": [], "trace": {}}
    result = advance("01-80-KAAKENHUSEN40-1")
    assert result["identity_gate_blocked"] is True
    assert result["metadata"]["building_identity_check"]["status"] == "missing"
    assert "agent_data_generic" not in result


def test_candidate_selection_location_reply_overrides_inherited_parser_location(address_selection_flow, monkeypatch):
    _, advance, calls = address_selection_flow
    advance("give me information about Kungsgatan 10")
    advance("Stockholm")
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {
        "address": "Kungsgatan 10", "address_location_hint": "Stockholm", "intents": ["SQL database"],
    }})
    count = len(calls)
    result = advance("Växjö")
    assert len(calls) == count
    assert result["metadata"]["address_location_hint"] == "Växjö"
    assert {option["byggnadsid"] for option in result["metadata"]["building_candidate_matches"]} == {
        "07-80-GUNNARGROEPE10-1", "07-80-GUNNARGROEPE9-1",
    }


def test_invalid_city_in_original_query_recovers_using_saved_candidates(address_selection_flow):
    _, advance, calls = address_selection_flow
    result = advance("give me information about Kungsgatan 10, Jönköping")
    assert result["identity_gate_blocked"] is True
    assert len(result["metadata"]["address_candidate_pool"]) == 4
    count = len(calls)
    result = advance("Stockholm")
    assert len(calls) == count
    assert {option["byggnadsid"] for option in result["metadata"]["building_candidate_matches"]} == {
        "01-80-KAAKENHUSEN39-1", "01-80-KAAKENHUSEN40-1",
    }
    result = advance("01-80-KAAKENHUSEN40-1")
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert "address_candidate_pool" not in result["metadata"]


def test_candidate_selection_can_accept_exact_listed_id_outside_generic_id_regex():
    module = import_building_flow_graph_module()
    # This ID form occurs in the supplied Kungsgatan response.
    building_id = "12-85-SIBBARP34:12-"
    metadata = {
        "address": "Kungsgatan 10", "address_location_hint": "Hässleholm",
        "clarification": {"needed": True, "reason": "ambiguous_address"},
        "building_identity_check": {"status": "ambiguous"},
        "building_candidate_matches": [{"byggnadsid": building_id, "recorded_addresses": ["Kungsgatan 10"]}],
    }
    assert module._extract_building_id_from_text(building_id) is None
    assert module._apply_address_candidate_reply(metadata, building_id) is True
    assert metadata["building_id_from_user"] == building_id


def test_llm_summarizer_accepts_session_state_history_list():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "what is energy class?",
            "messages": [{"role": "user", "content": "what is energy class?"}],
            "context": {"parsed_intent": "vector database", "intent_list": []},
            "session_state": [{"metadata": {"byggnadsid": "01-80-FILOSOFEN2-3"}}],
            "aggregated": "Vector hits:\nEnergy classes run from A to G.",
            "done_vector": True,
        }
    )

    assert result["final_response"].startswith("Building ID: 01-80-FILOSOFEN2-3")
    assert result["session_state"]["metadata"]["byggnadsid"] == "01-80-FILOSOFEN2-3"
    assert result["session_state"]["last_intent_list"] == []


def test_llm_summarizer_marks_same_building_multiple_addresses():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "Show me the data",
            "messages": [{"role": "user", "content": "Show me the data"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {
                "address": "Artemisgatan 17",
                "address_from_user": "Artemisgatan 17",
                "building_identity_check": {
                    "status": "passed",
                    "matched_building_id": "01-80-SKYTTEN2-2",
                    "matched_address": "Artemisgatan 13",
                    "ambiguous": False,
                },
            },
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-SKYTTEN2-2",
                        "epc_idadr": "Artemisgatan 13",
                        "epc_egienergiklass2020_calc": "F",
                        "epc_egiprimarenergital2020_calc": 145,
                        "epc_egienergiprestanda": 201,
                    }
                ]
            },
            "done_generic_sql": True,
        }
    )

    metadata = result["metadata"]
    assert metadata["same_building_multiple_addresses"] is True
    assert metadata["requested_address"] == "Artemisgatan 17"
    assert metadata["epc_record_address"] == "Artemisgatan 13"
    assert metadata["retrieved_facts"]["address"] == "Artemisgatan 17"
    assert metadata["retrieved_facts"]["epc_idadr"] == "Artemisgatan 13"


def test_vector_db_agent_persists_structured_hits_for_aggregation():
    module = import_building_flow_graph_module()
    module.vector_database.hits = [
        {
            "page_content": "Energy classes run from A to G.",
            "source": "boverket.pdf",
        }
    ]

    result = module.vector_db_agent_node(
        {
            "last_message": "what is energy class?",
            "context": {},
        }
    )

    assert result["done_vector"] is True
    assert result["agent_outputs_vector"] == ["Vector hits:\nEnergy classes run from A to G."]
    assert result["agent_data_vector"] == {
        "hits": [
            {
                "page_content": "Energy classes run from A to G.",
                "source": "boverket.pdf",
            }
        ],
        "sources": ["boverket.pdf"],
        "snippets": ["Energy classes run from A to G."],
    }


def test_understand_context_restores_prior_intent_for_address_only_follow_up():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "",
            "intents": [],
            "address": None,
            "ambigious": True,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "i live in Professorsslingan 51",
            "messages": [
                {"role": "user", "content": "What is the energy class of my building?"},
                {"role": "assistant", "content": "Can you please provide the building address?"},
                {"role": "user", "content": "i live in Professorsslingan 51"},
            ],
            "metadata": {},
            "session_state": {
                "context": {
                    "parsed_intent": "SQL database",
                    "intent_list": ["SQL database"],
                },
                "metadata": {},
            },
        }
    )

    assert result["context"]["address"] == "Professorsslingan 51"
    assert result["context"]["parsed_intent"] == "SQL database"
    assert result["context"]["intent_list"] == ["SQL database"]
    assert result["metadata"]["address"] == "Professorsslingan 51"
    assert result["metadata"]["address_from_user"] == "Professorsslingan 51"


def test_understand_context_recovers_address_from_initial_i_live_in_turn():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": True,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "so help me with this. I live in professorsslingan 10",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "Hi there! How can I help you today?"},
                {"role": "user", "content": "so help me with this. I live in professorsslingan 10"},
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["context"]["address"] == "professorsslingan 10"
    assert result["context"]["parsed_intent"] == "SQL database"
    assert result["metadata"]["address"] == "professorsslingan 10"
    assert result["metadata"]["address_from_user"] == "professorsslingan 10"


def test_understand_context_cleans_conversational_address_follow_up():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "",
            "intents": [],
            "address": "sure, i live in Artemisgatan 17",
            "ambigious": True,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "sure, i live in Artemisgatan 17.",
            "messages": [
                {"role": "user", "content": "How do I improve my energy efficiency?"},
                {
                    "role": "assistant",
                    "content": "To give building-specific advice safely, I need the full building address.",
                },
                {"role": "user", "content": "sure, i live in Artemisgatan 17."},
            ],
            "metadata": {},
            "session_state": {
                "context": {
                    "parsed_intent": "SQL database ; vector database",
                    "intent_list": ["SQL database", "vector database"],
                },
                "metadata": {},
            },
        }
    )

    assert result["context"]["address"] == "Artemisgatan 17"
    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert result["metadata"]["address"] == "Artemisgatan 17"


def test_understand_context_promotes_personal_energy_advice_to_hybrid_intent():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "vector database",
            "intents": ["vector database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "How do I improve my energy efficiency?",
            "messages": [
                {"role": "user", "content": "How do I improve my energy efficiency?"},
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert result["context"]["ambiguous"] is False
    assert result["context"]["advice_type"] == "ecm"
    assert result["context"]["answer_focus"] == "How do I improve my energy efficiency?"
    assert result["metadata"]["pending_building_advice_request"]["type"] == "ecm"


def test_understand_context_restores_pending_ecm_request_for_brf_identity_followup():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    pending = {
        "question": "We are a BRF in Stockholm. How can we reduce our heating costs?",
        "type": "ecm",
        "context": {
            "parsed_intent": "SQL database ; vector database",
            "intent_list": ["SQL database", "vector database"],
            "effective_query": "reduce heating costs energy conservation measures ECM",
            "answer_focus": "We are a BRF in Stockholm. How can we reduce our heating costs?",
            "advice_type": "ecm",
        },
    }

    result = module.understand_context_node(
        {
            "last_message": "ohh, sorry. We are brf Sjöstaden 2",
            "messages": [
                {
                    "role": "user",
                    "content": "We are a BRF in Stockholm. How can we reduce our heating costs?",
                },
                {
                    "role": "assistant",
                    "content": "Please share the full street address so I can use the correct building.",
                },
                {"role": "user", "content": "ohh, sorry. We are brf Sjöstaden 2"},
            ],
            "metadata": {},
            "session_state": {"metadata": {"pending_building_advice_request": pending}},
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert result["context"]["answer_focus"] == pending["question"]
    assert result["context"]["advice_type"] == "ecm"
    assert "energy conservation measures" in result["context"]["effective_query"]


def test_understand_context_promotes_ecm_followup_with_prior_building_context():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "vector database",
            "intents": ["vector database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "so, what are the relevant ecms",
            "messages": [
                {"role": "user", "content": "what are the EPC metrics for Öregrundsgatan 9"},
                {
                    "role": "assistant",
                    "content": "Building ID: 01-80-LISSABON2-2\n\nEnergy class: F",
                },
                {"role": "user", "content": "so, what are the relevant ecms"},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Öregrundsgatan 9",
                    "address_from_user": "Öregrundsgatan 9",
                    "byggnadsid": "01-80-LISSABON2-2",
                    "retrieved_facts": {
                        "energy_class": "F",
                        "energy_performance": 199,
                        "heating_system": "district heating",
                    },
                }
            },
        }
    )

    assert result["context"]["parsed_intent"] == "SQL database ; vector database"
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]
    assert result["context"]["ambiguous"] is False
    assert result["metadata"]["address"] == "Öregrundsgatan 9"
    assert "district heating" in result["context"]["effective_query"]
    assert "energy class: F" in result["context"]["effective_query"]


def test_understand_context_keeps_general_ecm_question_vector_only():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "vector database",
            "intents": ["vector database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "what are ECMs in general?",
            "messages": [{"role": "user", "content": "what are ECMs in general?"}],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Öregrundsgatan 9",
                    "byggnadsid": "01-80-LISSABON2-2",
                }
            },
        }
    )

    assert result["context"]["parsed_intent"] == "vector database"
    assert result["context"]["intent_list"] == ["vector database"]
    assert result["context"]["effective_query"] == "what are ECMs in general?"


def test_understand_context_keeps_stored_address_when_new_turn_omits_it():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "What is the energy class of my building?",
            "messages": [
                {"role": "user", "content": "What is the energy class of my building?"},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Professorsslingan 51",
                    "address_from_user": "Professorsslingan 51",
                }
            },
        }
    )

    assert result["context"]["intent_list"] == ["SQL database"]
    assert result["metadata"]["address"] == "Professorsslingan 51"
    assert result["metadata"]["address_from_user"] == "Professorsslingan 51"


def test_understand_context_does_not_treat_year_comparison_as_address():
    module = import_building_flow_graph_module()
    message = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": message,
            "messages": [
                {
                    "role": "assistant",
                    "classification": "building_specific",
                    "content": "Building ID: 01-80-FYSIKERN1-1\nEnergy class 2019: G\nEnergy class 2020: F",
                },
                {"role": "user", "content": message},
            ],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Professorsslingan 10",
                    "address_from_user": "Professorsslingan 10",
                    "byggnadsid": "01-80-FYSIKERN1-1",
                }
            },
        }
    )

    assert module._extract_address_candidate_from_text(message) is None
    assert result["context"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address_from_user"] == "Professorsslingan 10"


def test_extract_address_candidate_strips_city_context_from_prose():
    module = import_building_flow_graph_module()

    address = module._extract_address_candidate_from_text(
        "For the building at Nimrodsgatan 7 in Stockholm, what is our energy performance?"
    )

    assert address == "Nimrodsgatan 7"
    assert (
        module._extract_address_location_hint_from_text(
            "For the building at Nimrodsgatan 7 in Stockholm, what is our energy performance?",
            address,
        )
        == "Stockholm"
    )


def test_extract_address_candidate_handles_city_context_for_non_suffix_street_name():
    module = import_building_flow_graph_module()

    message = "For Professorsslingan 51 in Stockholm, what is the energy class?"
    address = module._extract_address_candidate_from_text(message)

    assert address == "Professorsslingan 51"
    assert module._extract_address_location_hint_from_text(message, address) == "Stockholm"


def test_extract_address_location_hint_from_later_live_clause():
    module = import_building_flow_graph_module()

    message = (
        "i live in Sveavägen 17. What is the building's energy performance?\n"
        "i live in Örebro"
    )
    address = module._extract_address_candidate_from_text(message)

    assert address == "Sveavägen 17"
    assert module._extract_address_location_hint_from_text(message, address) == "Örebro"


def test_extract_address_location_hint_from_later_clause_without_address_preposition():
    module = import_building_flow_graph_module()

    message = (
        "i live Kungsgatan 10. What is my energy performance?\n"
        "i live in Växjö"
    )
    address = module._extract_address_candidate_from_text(message)

    assert address == "Kungsgatan 10"
    assert module._extract_address_location_hint_from_text(message, address) == "Växjö"


def test_extract_address_without_preposition_for_non_suffix_street_name():
    module = import_building_flow_graph_module()

    message = (
        "i live Professorsslingan 51. What is my energy performance?\n"
        "i live in Stockholm"
    )
    address = module._extract_address_candidate_from_text(message)

    assert address == "Professorsslingan 51"
    assert module._extract_address_location_hint_from_text(message, address) == "Stockholm"


def test_understand_context_recovers_address_and_location_hint_from_raw_message():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "For Ringvägen 10 in Täby, what is the energy class?",
            "messages": [{"role": "user", "content": "For Ringvägen 10 in Täby, what is the energy class?"}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Täby"
    assert result["context"]["address_location_hint"] == "Täby"


def test_understand_context_recovers_comma_city_pair_strictly():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "I live at Ringvägen 10, Huddinge. What is the building's energy performance?",
            "messages": [
                {
                    "role": "user",
                    "content": "I live at Ringvägen 10, Huddinge. What is the building's energy performance?",
                }
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Huddinge"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["address_location_hint"] == "Huddinge"


def test_understand_context_recovers_address_and_later_location_hint_from_raw_message():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }
    message = (
        "i live in Sveavägen 17. What is the building's energy performance?\n"
        "i live in Örebro"
    )

    result = module.understand_context_node(
        {
            "last_message": message,
            "messages": [{"role": "user", "content": message}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Sveavägen 17"
    assert result["metadata"]["address_location_hint"] == "Örebro"
    assert result["context"]["address_location_hint"] == "Örebro"


def test_understand_context_recovers_later_location_hint_without_address_preposition():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }
    message = (
        "i live Kungsgatan 10. What is my energy performance?\n"
        "i live in Växjö"
    )

    result = module.understand_context_node(
        {
            "last_message": message,
            "messages": [{"role": "user", "content": message}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Kungsgatan 10"
    assert result["metadata"]["address_location_hint"] == "Växjö"
    assert result["context"]["address_location_hint"] == "Växjö"


def test_address_location_filters_preserve_oden_primary_address_alias():
    module = import_building_flow_graph_module()
    rows = [
        {"byggnadsid": "01-60-BOKBINDAREN6-1", "epc_idadr": "Ringvägen 10",
         "epc_idkommun": "Täby", "epc_idpostort": "Täby", "epc_idpostnr": "18770"},
        {"byggnadsid": "01-80-TAPETEN2-1", "epc_idadr": "Hornsgatan 104",
         "epc_idkommun": "Stockholm", "epc_idpostort": "Stockholm",
         "epc_idpostnr": "11726", "epc_egiversion": "2010"},
        {"byggnadsid": "01-80-TAPETEN2-1", "epc_idadr": "Hornsgatan 104",
         "epc_idkommun": "Stockholm", "epc_idpostort": "Stockholm",
         "epc_idpostnr": "11726", "epc_egiversion": "2015"},
    ]
    for hint in ("Stockholm", "117 26", "117 26 Stockholm", "Stockholm 11726"):
        matches = module._narrow_address_matches("Ringvägen 10", rows, hint)
        assert len(matches) == 1
        assert matches[0]["byggnadsid"] == "01-80-TAPETEN2-1"
        assert matches[0]["epc_idadr"] == "Hornsgatan 104"
        assert matches[0]["epc_egiversion"] == "2015"
    assert module._narrow_address_matches("Ringvägen 10", rows, "11726 Täby") == []
    assert module._narrow_address_matches("Ringvägen 10", rows, "18770 Stockholm") == []
    assert module._narrow_address_matches("Ringvägen 10", rows, "99999") == []


def test_address_resolution_keeps_distinct_buildings_in_same_municipality():
    module = import_building_flow_graph_module()
    rows = [
        {"byggnadsid": "BUILDING-A", "epc_idadr": "Storgatan 10",
         "epc_idkommun": "Stockholm", "epc_idpostnr": "11726", "epc_egiversion": "2010"},
        {"byggnadsid": "BUILDING-B", "epc_idadr": "Storgatan 10",
         "epc_idkommun": "Stockholm", "epc_idpostnr": "11726", "epc_egiversion": "2020"},
    ]
    matches = module._narrow_address_matches("Storgatan 10", rows, "Stockholm 11726")
    assert {row["byggnadsid"] for row in matches} == {"BUILDING-A", "BUILDING-B"}
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {"ok": True, "data": rows, "trace": {}}
    result = module.generic_sql_agent_node({
        "metadata": {"address": "Storgatan 10", "address_location_hint": "Stockholm 11726"},
        "parallel": {},
    })
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["identity_gate_blocked"] is True


def test_address_location_followups_accumulate_city_and_postcode():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {"context": {"address": None, "intents": [], "ambigious": False}}
    metadata = {
        "address": "Ringvägen 10", "address_from_user": "Ringvägen 10",
        "clarification": {"needed": True, "reason": "ambiguous_address"},
        "building_identity_check": {"status": "ambiguous"},
    }
    for reply, expected_hint in (("its in Stockholm", "Stockholm"), ("117 26", "117 26 Stockholm")):
        result = module.understand_context_node({
            "last_message": reply, "messages": [{"role": "user", "content": reply}],
            "metadata": metadata,
            "session_state": {"context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]}},
        })
        metadata = result["metadata"]
        assert metadata["address"] == "Ringvägen 10"
        assert metadata["address_location_hint"] == expected_hint


def test_understand_context_uses_location_only_followup_for_ambiguous_address():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": None,
            "intents": [],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "oh yes sure, I live in Katrineholm",
            "messages": [{"role": "user", "content": "oh yes sure, I live in Katrineholm"}],
            "metadata": {},
            "session_state": {
                "context": {
                    "parsed_intent": "SQL database",
                    "intent_list": ["SQL database"],
                },
                "metadata": {
                    "address": "Ringvägen 10",
                    "address_from_user": "Ringvägen 10",
                    "clarification": {
                        "needed": True,
                        "reason": "ambiguous_address",
                    },
                    "building_identity_check": {
                        "status": "ambiguous",
                    },
                },
            },
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Katrineholm"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["parsed_intent"] == "SQL database"


def test_understand_context_uses_pending_ambiguous_address_memory_key():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": None,
            "intents": [],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "i live in huddinge",
            "messages": [{"role": "user", "content": "i live in huddinge"}],
            "metadata": {
                "pending_ambiguous_address": "Ringvägen 10",
                "clarification": {
                    "needed": True,
                    "reason": "ambiguous_address",
                },
                "building_identity_check": {
                    "status": "ambiguous",
                },
            },
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "huddinge"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["address_location_hint"] == "huddinge"
    assert result["context"]["parsed_intent"] == "SQL database"


def test_understand_context_uses_first_location_followup_after_live_ambiguous_prompt():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": None,
            "intents": [],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "i live in Huddinge",
            "messages": [
                {
                    "role": "user",
                    "content": "I live at Ringvägen 10. What is the building's energy performance?",
                },
                {
                    "role": "assistant",
                    "content": (
                        "I found more than one possible building match for that address. "
                        "Please provide the city, postcode, municipality, BRF name, "
                        "or exact building ID so I use the correct building."
                    ),
                },
                {"role": "user", "content": "i live in Huddinge"},
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Huddinge"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["address_location_hint"] == "Huddinge"
    assert result["context"]["parsed_intent"] == "SQL database"


def test_understand_context_recovers_ambiguous_address_from_recent_messages_without_metadata():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": None,
            "intents": [],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "i live in Täby",
            "messages": [
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
                },
                {"role": "user", "content": "i live in Täby"},
            ],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Exempelgatan 10"
    assert result["metadata"]["address_location_hint"] == "Täby"
    assert result["context"]["address"] == "Exempelgatan 10"
    assert result["context"]["address_location_hint"] == "Täby"
    assert result["context"]["parsed_intent"] == "SQL database"


def test_understand_context_preserves_location_hint_on_later_building_followup():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "What is the building's energy performance?",
            "messages": [{"role": "user", "content": "What is the building's energy performance?"}],
            "metadata": {
                "address": "Ringvägen 10",
                "address_from_user": "Ringvägen 10",
                "address_location_hint": "Stockholm",
                "clarification": {
                    "needed": False,
                    "reason": None,
                    "resolved": True,
                },
            },
            "session_state": {},
        }
    )

    assert result["metadata"]["address"] == "Ringvägen 10"
    assert result["metadata"]["address_location_hint"] == "Stockholm"
    assert result["context"]["address"] == "Ringvägen 10"
    assert result["context"]["address_location_hint"] == "Stockholm"


def test_understand_context_discards_bad_parser_address_and_uses_stored_address():
    module = import_building_flow_graph_module()
    message = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": message,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": message,
            "messages": [{"role": "user", "content": message}],
            "metadata": {},
            "session_state": {
                "metadata": {
                    "address": "Professorsslingan 10",
                    "address_from_user": "Professorsslingan 10",
                }
            },
        }
    )

    assert result["context"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address"] == "Professorsslingan 10"


def test_understand_context_drops_polluted_metadata_address():
    module = import_building_flow_graph_module()
    bad_address = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "show me the data",
            "messages": [{"role": "user", "content": "show me the data"}],
            "metadata": {
                "address": bad_address,
                "address_from_user": bad_address,
                "byggnadsid": "01-80-FYSIKERN1-1",
            },
            "session_state": {},
        }
    )

    assert result["context"].get("address") is None
    assert "address" not in result["metadata"]
    assert "address_from_user" not in result["metadata"]
    assert result["metadata"]["byggnadsid"] == "01-80-FYSIKERN1-1"


def test_understand_context_extracts_building_id_from_user_text():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "use this building 01-80-BRAEDGAARDEN9-2",
            "messages": [{"role": "user", "content": "use this building 01-80-BRAEDGAARDEN9-2"}],
            "metadata": {},
            "session_state": {},
        }
    )

    assert result["metadata"]["byggnadsid"] == "01-80-BRAEDGAARDEN9-2"
    assert result["metadata"]["building_id_from_user"] == "01-80-BRAEDGAARDEN9-2"


def test_understand_context_prefers_valid_nested_address_over_polluted_address():
    module = import_building_flow_graph_module()
    bad_address = "please explain more. like why is 2019G and 2020 updated as F ? Show me the data"
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "show me the data",
            "messages": [{"role": "user", "content": "show me the data"}],
            "metadata": {
                "address": bad_address,
                "address_from_user": bad_address,
                "building_identity_check": {
                    "matched_address": "Professorsslingan 10",
                    "matched_building_id": "01-80-FYSIKERN1-1",
                },
            },
            "session_state": {},
        }
    )

    assert result["context"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address"] == "Professorsslingan 10"
    assert result["metadata"]["address_from_user"] == "Professorsslingan 10"


def test_understand_context_promotes_nested_building_match_address():
    module = import_building_flow_graph_module()
    DummyParseIntentAgent.result = {
        "context": {
            "parsed_intent": "SQL database",
            "intents": ["SQL database"],
            "address": None,
            "ambigious": False,
        }
    }

    result = module.understand_context_node(
        {
            "last_message": "what is the heating system in my building?",
            "messages": [
                {"role": "user", "content": "what is the heating system in my building?"},
            ],
            "metadata": {
                "building_id": "01-80-SKYTTEN2-2",
                "building_match": {
                    "input_address": "Artemisgatan 17",
                    "matched_address": "Artemisgatan 17",
                },
            },
            "session_state": {},
        }
    )

    assert result["context"]["address"] == "Artemisgatan 17"
    assert result["metadata"]["address"] == "Artemisgatan 17"
    assert result["metadata"]["address_from_user"] == "Artemisgatan 17"


def test_brf_resolution_asks_for_selection_when_brf_has_multiple_buildings():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden",
            "byggnadsid": "01-80-CIGARREN2-1",
            "fastighet": "Cigarren 2",
            "address": "Tegelviksgatan 71",
            "postnr": "11647",
            "postort": "Stockholm",
        },
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden",
            "byggnadsid": "01-80-CIGARREN2-2",
            "fastighet": "Cigarren 2",
            "address": "Tegelviksgatan 77",
            "postnr": "11647",
            "postort": "Stockholm",
        },
    ]

    result = module.brf_resolution_node(
        {
            "last_message": "We are BRF Sjöstaden, what ventilation system do we have?",
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {},
        }
    )

    metadata = result["metadata"]
    assert metadata["brf_name"] == "Sjöstaden"
    assert metadata["clarification"]["needed"] is True
    assert metadata["clarification"]["reason"] == "ambiguous_brf"
    assert "1. 01-80-CIGARREN2-1" in metadata["clarification"]["question_asked"]
    assert "2. 01-80-CIGARREN2-2" in metadata["clarification"]["question_asked"]
    assert metadata["pending_brf_resolution"]["original_question"].startswith("We are BRF")


def test_brf_resolution_uses_recent_brf_name_for_building_specific_followup():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden 1",
            "byggnadsid": "01-80-HALVOEN1-3",
            "fastighet": "Halvön 1",
            "address": "Hammarby Allé 163",
            "postnr": "12065",
            "postort": "Stockholm",
        },
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden 1",
            "byggnadsid": "01-80-HALVOEN1-3",
            "fastighet": "Halvön 1",
            "address": "Hammarby Allé 165",
            "postnr": "12065",
            "postort": "Stockholm",
        },
    ]

    result = module.brf_resolution_node(
        {
            "last_message": "yes, please give me building specific advice",
            "messages": [
                {
                    "role": "user",
                    "content": "I live in brf Sjöstaden 1. How can we improve our heating bills?",
                },
                {"role": "assistant", "classification": "generic", "content": "General advice..."},
                {"role": "user", "content": "yes, please give me building specific advice"},
            ],
            "context": {"parsed_intent": "SQL database ; vector database", "intent_list": ["SQL database", "vector database"]},
            "metadata": {},
        }
    )

    metadata = result["metadata"]
    assert metadata["brf_name"] == "Sjöstaden 1"
    assert metadata["byggnadsid"] == "01-80-HALVOEN1-3"
    assert metadata["brf_resolution"]["status"] == "resolved_unique_building"
    assert metadata["clarification"]["needed"] is False


def test_brf_resolution_does_not_treat_location_phrase_as_brf_name():
    module = import_building_flow_graph_module()

    assert (
        module._extract_brf_name_from_text(
            "We are a BRF in Stockholm. How can we reduce our heating costs?"
        )
        is None
    )
    assert (
        module._extract_brf_name_from_text(
            "We are a BRF with very high heating bills in winter. What can we do?"
        )
        is None
    )
    assert module._extract_brf_name_from_text("How many BRF do we have?") is None


@pytest.mark.parametrize("text, expected", [
    ("brf har fjarrvarme o det blir dyrt", None),
    ("Vår BRF har fjärrvärme och det blir dyrt.", None),
    ("Vår BRF med höga värmekostnader", None),
    ("Vad betyder BRF?", None),
    ("BRF Sjöstaden 1 har fjärrvärme", "Sjöstaden 1"),
    ("BRF Harpan", "Harpan"),
    ("BRF Solgläntan 1", "Solgläntan 1"),
])
def test_swedish_brf_names_match_router_validation(text, expected):
    module = import_building_flow_graph_module()
    from tests.unit.test_agent_router import import_agent_router_module
    router_module = import_agent_router_module()
    assert module._extract_brf_name_from_text(text) == expected
    assert router_module._extract_concrete_brf_name(text) == expected


def test_brf_resolution_clears_failed_lookup_for_new_public_question():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: (_ for _ in ()).throw(
        AssertionError("stale BRF lookup should not be retried")
    )

    result = module.brf_resolution_node(
        {
            "last_message": "what is BRF",
            "messages": [
                {"role": "user", "content": "How many BRF do we have?"},
                {"role": "assistant", "content": "I could not find building addresses for BRF do we have."},
                {"role": "user", "content": "what is BRF"},
            ],
            "context": {"parsed_intent": "vector database", "intent_list": ["vector database"]},
            "metadata": {
                "brf_name": "do we have",
                "brf_resolution": {"status": "not_found", "brf_name": "do we have"},
                "clarification": {
                    "needed": True,
                    "reason": "brf_not_found",
                    "question_asked": "I could not find building addresses for BRF do we have.",
                    "resolved": False,
                },
            },
        }
    )

    metadata = result["metadata"]
    assert "brf_name" not in metadata
    assert "brf_resolution" not in metadata
    assert "clarification" not in metadata


def test_brf_resolution_selects_pending_building_and_restores_original_question():
    module = import_building_flow_graph_module()
    pending = {
        "brf_name": "Sjöstaden",
        "original_question": "We are BRF Sjöstaden, what ventilation system do we have?",
        "original_context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
        "options": [
            {
                "choice": "1",
                "byggnadsid": "01-80-CIGARREN2-1",
                "addresses": ["Tegelviksgatan 71, 11647 Stockholm"],
            },
            {
                "choice": "2",
                "byggnadsid": "01-80-CIGARREN2-2",
                "addresses": ["Tegelviksgatan 77, 11647 Stockholm"],
            },
        ],
        "question": "Which building?",
    }

    result = module.brf_resolution_node(
        {
            "last_message": "2",
            "context": {"parsed_intent": "", "intent_list": []},
            "metadata": {"pending_brf_resolution": pending},
        }
    )

    assert result["last_message"] == pending["original_question"]
    assert result["context"]["intent_list"] == ["SQL database"]
    assert result["metadata"]["byggnadsid"] == "01-80-CIGARREN2-2"
    assert result["metadata"]["selected_brf_lookup_address"] == "Tegelviksgatan 77"
    assert result["metadata"]["clarification"]["needed"] is False
    assert "pending_brf_resolution" not in result["metadata"]


def test_brf_resolution_carries_pending_ecm_question_into_brf_selection():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden 2",
            "byggnadsid": "01-80-BRAEDGAARDEN9-2",
            "address": "Aktergatan 11",
            "postnr": "12066",
            "postort": "Stockholm",
        },
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden 2",
            "byggnadsid": "01-80-BRAEDGAARDEN9-1",
            "address": "Lugnets Allé 22",
            "postnr": "12066",
            "postort": "Stockholm",
        },
    ]

    result = module.brf_resolution_node(
        {
            "last_message": "ohh, sorry. We are brf Sjöstaden 2",
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
                "answer_focus": "We are a BRF in Stockholm. How can we reduce our heating costs?",
                "advice_type": "ecm",
            },
            "metadata": {},
        }
    )

    pending = result["metadata"]["pending_brf_resolution"]
    assert pending["original_question"] == "We are a BRF in Stockholm. How can we reduce our heating costs?"
    assert pending["original_context"]["advice_type"] == "ecm"


def test_brf_resolution_selects_pending_building_from_building_id_reply():
    module = import_building_flow_graph_module()
    pending = {
        "brf_name": "Sjöstaden 2",
        "original_question": "I live in BRF Sjöstaden 2. What ventilation system do we have?",
        "original_context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
        "options": [
            {
                "choice": "1",
                "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                "addresses": ["Aktergatan 11, 12066 Stockholm", "Aktergatan 13, 12066 Stockholm"],
            },
            {
                "choice": "2",
                "byggnadsid": "01-80-BRAEDGAARDEN9-1",
                "addresses": ["Lugnets Allé 22, 12066 Stockholm"],
            },
        ],
        "question": "Which building?",
    }

    result = module.brf_resolution_node(
        {
            "last_message": "use this building 01-80-BRAEDGAARDEN9-2",
            "context": {"parsed_intent": "", "intent_list": []},
            "metadata": {"pending_brf_resolution": pending},
        }
    )

    assert result["last_message"] == pending["original_question"]
    assert result["metadata"]["byggnadsid"] == "01-80-BRAEDGAARDEN9-2"
    assert result["metadata"]["selected_brf_lookup_address"] == "Aktergatan 11"
    assert result["metadata"]["clarification"]["needed"] is False
    assert "pending_brf_resolution" not in result["metadata"]


def test_brf_resolution_ignores_stale_pending_resolution_after_building_selected():
    module = import_building_flow_graph_module()

    result = module.brf_resolution_node(
        {
            "last_message": "when was this building built?",
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {
                "byggnadsid": "01-80-HEDVIG15-1",
                "selected_brf_building_id": "01-80-HEDVIG15-1",
                "pending_brf_resolution": {
                    "brf_name": "Solgläntan 1",
                    "question": "Which building should I use?",
                    "options": [
                        {
                            "choice": "1",
                            "byggnadsid": "01-80-HEDVIG15-1",
                            "addresses": ["Bennebolsgatan 34, 16350 Spånga"],
                        }
                    ],
                },
                "brf_resolution": {
                    "status": "resolved_by_user_selection",
                    "selected_building_id": "01-80-HEDVIG15-1",
                },
            },
        }
    )

    assert result["metadata"]["byggnadsid"] == "01-80-HEDVIG15-1"
    assert "pending_brf_resolution" not in result["metadata"]
    assert "clarification" not in result["metadata"]
    assert "last_message" not in result


def test_brf_resolution_uses_unique_building_id_directly():
    module = import_building_flow_graph_module()
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden",
            "byggnadsid": "01-80-CIGARREN2-1",
            "address": "Tegelviksgatan 71",
            "postnr": "11647",
            "postort": "Stockholm",
        },
        {
            "brf_name": "Bostadsrättsföreningen Sjöstaden",
            "byggnadsid": "01-80-CIGARREN2-1",
            "address": "Tengdahlsgatan 40",
            "postnr": "11647",
            "postort": "Stockholm",
        },
    ]

    result = module.brf_resolution_node(
        {
            "last_message": "I live in BRF Sjöstaden, what is my energy rating?",
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {},
        }
    )

    assert result["metadata"]["byggnadsid"] == "01-80-CIGARREN2-1"
    assert result["metadata"]["brf_resolution"]["status"] == "resolved_unique_building"
    assert result["metadata"]["clarification"]["needed"] is False


def test_generic_sql_agent_blocks_ambiguous_building_matches():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"50a_uuid": "uuid-1", "epc_idadr": "Main Street 1"},
            {"50a_uuid": "uuid-2", "epc_idadr": "Main Street 2"},
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Main Street"},
            "parallel": {},
        }
    )

    assert result["identity_gate_blocked"] is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"


def test_generic_sql_agent_allows_multiple_records_for_same_exact_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-80-SKYTTEN2-2",
                "50a_uuid": "uuid-1",
                "address": "Artemisgatan 17",
                "energy_class": "D",
            },
            {
                "byggnadsid": "01-80-SKYTTEN2-2",
                "50a_uuid": "uuid-1",
                "address": "Artemisgatan 17",
                "heating_system": "district heating",
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["building_identity_check"]["multiple_records_same_address"] is True
    assert result["agent_data_generic"][0]["address"] == "Artemisgatan 17"


def test_generic_sql_agent_allows_multiple_addresses_for_explicit_building_id():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"byggnadsid": "01-80-CIGARREN2-1", "address": "Tegelviksgatan 71", "energy_class": "F"},
            {"byggnadsid": "01-80-CIGARREN2-1", "address": "Tengdahlsgatan 40", "energy_class": "F"},
        ],
        "trace": {"query_type": "buildings_by_single_filter", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"byggnadsid": "01-80-CIGARREN2-1"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["building_identity_check"]["multiple_addresses_same_explicit_building"] is True
    assert len(result["agent_data_generic"]) == 2


def test_generic_sql_agent_prefers_building_id_over_ambiguous_address_and_exposes_construction_year():
    module = import_building_flow_graph_module()
    calls = []
    selected_building_id = "24-82-BURTRAESKS-GAMMELBYN71:4-1"

    def fake_execute(op, **kwargs):
        calls.append((op, kwargs))
        if op == "buildings_by_building_id":
            return {
                "ok": True,
                "data": [
                    {
                        "byggnadsid": selected_building_id,
                        "epc_idadr": "Ringvägen 10",
                        "epc_idpostnr": "93732",
                        "epc_idpostort": "Burträsk",
                        "epc_idkommun": "Skellefteå",
                        "epc_egennybyggar": 1966,
                        "epc_godkand": "2020-12-23",
                        "epc_egiversion": "2020",
                    }
                ],
                "trace": {"query_type": "buildings_by_building_id", "execution_status": "success"},
            }
        raise AssertionError(f"unexpected operation: {op}")

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "building_id_from_user": selected_building_id,
            },
            "parallel": {},
        }
    )

    assert calls == [("buildings_by_building_id", {"building_id": selected_building_id.upper()})]
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == selected_building_id.upper()
    assert result["agent_data_generic"][0]["byggnadsid"] == selected_building_id
    assert result["agent_data_generic"][0]["construction_year"] == 1966
    assert result["metadata"]["generic_sql_trace"]["returned_values_used"]["construction_year"] == 1966


def test_generic_sql_agent_does_not_use_stale_system_building_id_over_location_hint():
    module = import_building_flow_graph_module()
    calls = []

    def fake_execute(op, **kwargs):
        calls.append((op, kwargs))
        if op == "building_by_address":
            return {
                "ok": True,
                "data": [
                    {
                        "byggnadsid": "01-60-BOKBINDAREN6-1",
                        "epc_idadr": "Ringvägen 10",
                        "epc_idpostort": "Täby",
                        "epc_idkommun": "Täby",
                        "epc_idpostnr": "18770",
                        "epc_godkand": "2019-10-31",
                        "epc_egienergiprestanda": 173,
                    },
                    {
                        "byggnadsid": "01-26-SPACKELN6-1",
                        "epc_idadr": "Ringvägen 10",
                        "epc_idpostort": "Huddinge",
                        "epc_idkommun": "Huddinge",
                        "epc_idpostnr": "14131",
                        "epc_godkand": "2018-04-27",
                        "epc_egienergiprestanda": 86,
                    },
                ],
                "trace": {"query_type": "building_by_address", "execution_status": "success"},
            }
        raise AssertionError(f"unexpected operation: {op}")

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Huddinge",
                "byggnadsid": "01-60-BOKBINDAREN6-1",
            },
            "parallel": {},
        }
    )

    assert calls == [("building_by_address", {"address": "Ringvägen 10"})]
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-26-SPACKELN6-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Huddinge"


def test_generic_sql_agent_uses_latest_epc_for_same_building_address_alias():
    module = import_building_flow_graph_module()

    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-80-EXAMPLE77-1",
                "epc_idadr": "Firstgatan 20",
                "epc_idpostort": "Stockholm",
                "epc_idkommun": "Stockholm",
                "epc_idpostnr": "11157",
                "epc_godkand": "2008-11-25",
                "epc_egienergiprestanda": 226,
            },
            {
                "byggnadsid": "01-80-EXAMPLE77-1",
                "epc_idadr": "Secondtorget 2",
                "epc_idpostort": "Stockholm",
                "epc_idkommun": "Stockholm",
                "epc_idpostnr": "11157",
                "epc_godkand": "2019-11-20",
                "epc_egienergiprestanda": 169,
            },
            {
                "byggnadsid": "01-60-OTHER37-1",
                "epc_idadr": "Firstgatan 20",
                "epc_idpostort": "Täby",
                "epc_idkommun": "Täby",
                "epc_idpostnr": "18762",
                "epc_godkand": "2024-01-01",
                "epc_egienergiprestanda": 300,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Firstgatan 20",
                "address_location_hint": "Stockholm",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-80-EXAMPLE77-1"
    assert result["agent_data_generic"][0]["epc_idadr"] == "Secondtorget 2"
    assert result["agent_data_generic"][0]["epc_egienergiprestanda"] == 169


def test_generic_sql_agent_allows_multiple_addresses_for_same_building_id_without_explicit_id():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"byggnadsid": "01-80-CIGARREN2-1", "address": "Tegelviksgatan 71", "energy_class": "F"},
            {"byggnadsid": "01-80-CIGARREN2-1", "address": "Tengdahlsgatan 40", "energy_class": "F"},
        ],
        "trace": {"query_type": "buildings_by_single_filter", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"epc_idadr": "Tegelviksgatan 71"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    identity = result["metadata"]["building_identity_check"]
    assert identity["status"] == "passed"
    assert identity["ambiguous"] is False
    assert identity["multiple_addresses_same_building_id"] is True
    assert result["metadata"]["byggnadsid"] == "01-80-CIGARREN2-1"


def test_extracts_multiple_addresses_from_identity_question():
    module = import_building_flow_graph_module()

    addresses = module._extract_address_candidates_from_text(
        "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?"
    )

    assert addresses == ["Artemisgatan 17", "Nimrodsgatan 7"]


def test_extracts_multiword_addresses_without_leading_conjunction():
    module = import_building_flow_graph_module()

    addresses = module._extract_address_candidates_from_text(
        "Our property has two addresses: Hammarby Allé 163 and Hammarby Allé 165. Which one do you use?"
    )

    assert addresses == ["Hammarby Allé 163", "Hammarby Allé 165"]


def test_extracts_semicolon_separated_address_list_with_postcodes():
    module = import_building_flow_graph_module()

    addresses = module._extract_address_candidates_from_text(
        "Aktergatan 11, 12066 Stockholm; Aktergatan 13, 12066 Stockholm; "
        "Aktergatan 5, 12066 Stockholm; Aktergatan 7, 12066 Stockholm; "
        "Aktergatan 9, 12066 Stockholm; Hammarby Allé 173, 12066 Stockholm"
    )

    assert addresses == [
        "Aktergatan 11",
        "Aktergatan 13",
        "Aktergatan 5",
        "Aktergatan 7",
        "Aktergatan 9",
        "Hammarby Allé 173",
    ]


def test_semicolon_separated_address_list_triggers_multi_address_resolution():
    module = import_building_flow_graph_module()

    text = (
        "Aktergatan 11, 12066 Stockholm; Aktergatan 13, 12066 Stockholm; "
        "Aktergatan 5, 12066 Stockholm; Aktergatan 7, 12066 Stockholm; "
        "Aktergatan 9, 12066 Stockholm; Hammarby Allé 173, 12066 Stockholm"
    )

    assert module._looks_like_multi_address_identity_question(
        text,
        module._extract_address_candidates_from_text(text),
    )


def test_multi_address_resolution_uses_shared_building_id_for_alias_addresses():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        assert op == "building_by_address"
        return {
            "ok": True,
            "data": [
                {
                    "byggnadsid": "01-80-SKYTTEN2-2",
                    "address": kwargs["address"],
                    "energy_class": "F",
                }
            ],
            "trace": {"query_type": "building_by_address", "execution_status": "success"},
        }

    module.sql_mapper_layer.execute = fake_execute

    result = module.multi_address_resolution_node(
        {
            "last_message": "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
            "metadata": {},
        }
    )

    assert result["multi_address_resolution_complete"] is True
    assert result["metadata"]["same_building_multiple_addresses"] is True
    assert result["metadata"]["byggnadsid"] == "01-80-SKYTTEN2-2"
    assert result["metadata"]["address"] == "Artemisgatan 17"
    assert result["metadata"]["alternate_addresses"] == ["Nimrodsgatan 7"]
    assert "Building ID: 01-80-SKYTTEN2-2" in result["final_response"]
    assert "resolve to the same building record" in result["final_response"]
    assert "building ID as the source of truth" in result["final_response"]
    assert "latest available EPC/building record" in result["final_response"]


def test_multi_address_resolution_uses_brf_address_context_when_direct_lookup_misses():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        return {
            "ok": False,
            "data": [],
            "message": "not found",
            "trace": {"query_type": op, "execution_status": "not_found"},
        }

    module.sql_mapper_layer.execute = fake_execute
    module._lookup_brf_addresses = lambda brf_name: [
        {
            "orgnr": "7696062509",
            "brf_name": "Bostadsrättsföreningen Sjöstaden 1",
            "byggnadsid": "01-80-HALVOEN1-3",
            "fastighet": "Halvön 1",
            "address": "Hammarby Allé 163",
            "postnr": "12065",
            "postort": "Stockholm",
        },
        {
            "orgnr": "7696062509",
            "brf_name": "Bostadsrättsföreningen Sjöstaden 1",
            "byggnadsid": "01-80-HALVOEN1-3",
            "fastighet": "Halvön 1",
            "address": "Hammarby Allé 165",
            "postnr": "12065",
            "postort": "Stockholm",
        },
    ]

    result = module.resolve_multi_address_identity_message(
        "Our property has two addresses: Hammarby Allé 163 and Hammarby Allé 165. Which one do you use?",
        {"brf_name": "Sjöstaden 1"},
    )

    assert result["multi_address_resolution_complete"] is True
    assert result["metadata"]["same_building_multiple_addresses"] is True
    assert result["metadata"]["byggnadsid"] == "01-80-HALVOEN1-3"
    assert result["metadata"]["address"] == "Hammarby Allé 163"
    assert result["metadata"]["alternate_addresses"] == ["Hammarby Allé 165"]
    assert result["metadata"]["address_candidates"] == ["Hammarby Allé 163", "Hammarby Allé 165"]
    assert "Building ID: 01-80-HALVOEN1-3" in result["final_response"]


def test_multi_address_resolution_asks_when_addresses_map_to_different_buildings():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        assert op == "building_by_address"
        address = kwargs["address"]
        building_id = "01-80-SKYTTEN2-2" if address == "Artemisgatan 17" else "01-80-NIMROD1-1"
        return {
            "ok": True,
            "data": [{"byggnadsid": building_id, "address": address}],
            "trace": {"query_type": "building_by_address", "execution_status": "success"},
        }

    module.sql_mapper_layer.execute = fake_execute

    result = module.multi_address_resolution_node(
        {
            "last_message": "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
            "metadata": {},
        }
    )

    assert result["multi_address_resolution_complete"] is True
    assert result["metadata"]["clarification"]["needed"] is True
    assert result["metadata"]["clarification"]["reason"] == "multi_address_different_buildings"
    assert "I found different building IDs" in result["final_response"]
    assert "Which building ID should I use?" in result["final_response"]


def test_multi_address_resolution_returns_clarification_when_lookup_errors():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        raise TimeoutError("lookup timeout")

    module.sql_mapper_layer.execute = fake_execute

    result = module.resolve_multi_address_identity_message(
        "Our property has two addresses: Artemisgatan 17 and Nimrodsgatan 7. Which one do you use?",
        {},
    )

    assert result["multi_address_resolution_complete"] is True
    assert result["metadata"]["clarification"]["needed"] is True
    assert result["metadata"]["clarification"]["reason"] == "multi_address_no_match"
    assert "I could not finish the address lookup" in result["final_response"]
    assert "lookup timed out" in result["final_response"]
    assert "Processing timed out" not in result["final_response"]


def test_multi_address_resolution_ignores_stale_candidates_on_single_address_followup():
    module = import_building_flow_graph_module()

    result = module.resolve_multi_address_identity_message(
        "use this address, Artemisgatan 17",
        {
            "address_candidates": ["Artemisgatan 17", "Nimrodsgatan 7"],
            "multi_address_resolution": {
                "status": "no_match",
                "candidates": [
                    {"address": "Artemisgatan 17", "ok": False},
                    {"address": "Nimrodsgatan 7", "ok": False},
                ],
            },
            "clarification": {
                "needed": True,
                "reason": "multi_address_no_match",
            },
        },
    )

    assert result is None


def test_multi_address_lookup_uses_short_timeout_and_restores_default(monkeypatch):
    module = import_building_flow_graph_module()

    class FakeSQL:
        timeout = 15

    seen_timeouts = []
    module.sql_mapper_layer.sql = FakeSQL()

    def fake_execute(op, **kwargs):
        seen_timeouts.append(module.sql_mapper_layer.sql.timeout)
        return {
            "ok": True,
            "data": [{"byggnadsid": "01-80-SKYTTEN2-2", "address": kwargs["address"]}],
        }

    monkeypatch.setenv("ODEN_MULTI_ADDRESS_TIMEOUT", "3")
    module.sql_mapper_layer.execute = fake_execute

    result = module._resolve_single_address_candidate("Artemisgatan 17")

    assert result["building_id"] == "01-80-SKYTTEN2-2"
    assert seen_timeouts == [3]
    assert module.sql_mapper_layer.sql.timeout == 15


def test_multi_address_lookup_default_timeout_allows_slow_oden_address_responses(monkeypatch):
    module = import_building_flow_graph_module()

    class FakeSQL:
        timeout = 15

    seen_timeouts = []
    module.sql_mapper_layer.sql = FakeSQL()

    def fake_execute(op, **kwargs):
        seen_timeouts.append(module.sql_mapper_layer.sql.timeout)
        return {
            "ok": True,
            "data": [{"byggnadsid": "01-80-HALVOEN1-3", "address": kwargs["address"]}],
        }

    monkeypatch.delenv("ODEN_MULTI_ADDRESS_TIMEOUT", raising=False)
    module.sql_mapper_layer.execute = fake_execute

    result = module._resolve_single_address_candidate("Lugnets Allé 25")

    assert result["building_id"] == "01-80-HALVOEN1-3"
    assert seen_timeouts == [10]
    assert module.sql_mapper_layer.sql.timeout == 15


def test_generic_sql_agent_uses_brf_selected_address_fallback_for_building_id():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        if op == "buildings_by_building_id":
            return {
                "ok": False,
                "data": [],
                "trace": {"query_type": "buildings_by_building_id", "execution_status": "not_found"},
            }
        if op == "building_by_address":
            assert kwargs["address"] == "Aktergatan 11"
            return {
                "ok": True,
                "data": [
                    {
                        "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                        "address": "Aktergatan 11",
                        "epc_venttypftx": "Ja",
                    }
                ],
                "trace": {"query_type": "building_by_address", "execution_status": "success"},
            }
        raise AssertionError(op)

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                "selected_brf_lookup_address": "Aktergatan 11",
                "selected_brf_addresses": ["Aktergatan 11, 12066 Stockholm"],
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["generic_sql_trace"]["match_strategy"] == "brf_selected_address_fallback"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-80-BRAEDGAARDEN9-2"


def test_generic_sql_agent_accepts_explicit_building_id_despite_stale_ambiguity():
    module = import_building_flow_graph_module()

    def fake_execute(op, **kwargs):
        assert op == "buildings_by_building_id"
        assert kwargs["building_id"] == "01-80-BRAEDGAARDEN9-2"
        return {
            "ok": True,
            "data": [
                {
                    "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                    "epc_idadr": "Aktergatan 11",
                    "energy_class": "C",
                }
            ],
            "trace": {"query_type": "buildings_by_building_id", "execution_status": "success"},
        }

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "byggnadsid": "01-80-BRAEDGAARDEN9-2",
                "building_id_from_user": "01-80-BRAEDGAARDEN9-2",
                "selected_brf_building_id": "01-80-BRAEDGAARDEN9-2",
                "context": {"ambiguous": True},
                "clarification": {
                    "needed": True,
                    "reason": "ambiguous_brf",
                },
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    identity = result["metadata"]["building_identity_check"]
    assert identity["status"] == "passed"
    assert identity["ambiguous"] is False
    assert identity["matched_building_id"] == "01-80-BRAEDGAARDEN9-2"
    assert identity["explicit_building_id_accepted"] is True
    assert result["metadata"]["clarification"]["needed"] is False
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-80-BRAEDGAARDEN9-2"


def test_generic_sql_agent_success_clears_stale_clarification_metadata():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "50a_uuid": "uuid-1",
                "address": "Artemisgatan 17",
                "energy_class": "D",
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Artemisgatan 17",
                "clarification": {
                    "needed": True,
                    "reason": "ambiguous_brf",
                    "question_asked": "Could you clarify?",
                    "resolved": False,
                },
            },
            "parallel": {},
        }
    )

    clarification = result["metadata"]["clarification"]
    assert clarification["needed"] is False
    assert clarification["resolved"] is True
    assert clarification["reason"] is None


def test_generic_sql_agent_selects_latest_epc_version_for_same_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-80-LISSABON2-2",
                "50a_uuid": "same-building",
                "address": "Öregrundsgatan 9",
                "epc_egiversion": "2010",
                "epc_egienergiklass2020_calc": "G",
            },
            {
                "byggnadsid": "01-80-LISSABON2-2",
                "50a_uuid": "same-building",
                "address": "Öregrundsgatan 9",
                "epc_egiversion": "2020",
                "epc_egienergiklass2020_calc": "F",
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Öregrundsgatan 9"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert len(result["agent_data_generic"]) == 1
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-80-LISSABON2-2"
    assert result["agent_data_generic"][0]["epc_egiversion"] == "2020"
    assert result["agent_data_generic"][0]["calculated_energy_class"] == "F"
    assert "energy_class" not in result["agent_data_generic"][0]
    assert result["metadata"]["generic_sql_trace"]["returned_values_used"]["byggnadsid"] == "01-80-LISSABON2-2"


def test_generic_sql_agent_uses_building_id_and_latest_epc_for_same_building_aliases():
    module = import_building_flow_graph_module()
    calls = []

    def fake_execute(op, **kwargs):
        calls.append((op, kwargs))
        assert op == "buildings_by_building_id"
        assert kwargs["building_id"] == "01-80-SKYTTEN2-2"
        return {
            "ok": True,
            "data": [
                {
                    "byggnadsid": "01-80-SKYTTEN2-2",
                    "address": "Artemisgatan 17",
                    "epc_egiversion": "2009",
                    "epc_egienergiklass2020_calc": "G",
                },
                {
                    "byggnadsid": "01-80-SKYTTEN2-2",
                    "address": "Nimrodsgatan 7",
                    "epc_egiversion": "2019",
                    "epc_egienergiklass2020_calc": "F",
                },
            ],
            "trace": {"query_type": "buildings_by_building_id", "execution_status": "success"},
        }

    module.sql_mapper_layer.execute = fake_execute

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Artemisgatan 17",
                "address_from_user": "Artemisgatan 17",
                "alternate_addresses": ["Nimrodsgatan 7"],
                "byggnadsid": "01-80-SKYTTEN2-2",
                "same_building_multiple_addresses": True,
            },
            "parallel": {},
        }
    )

    assert calls == [("buildings_by_building_id", {"building_id": "01-80-SKYTTEN2-2"})]
    assert result.get("identity_gate_blocked") is not True
    assert len(result["agent_data_generic"]) == 1
    assert result["agent_data_generic"][0]["address"] == "Nimrodsgatan 7"
    assert result["agent_data_generic"][0]["epc_egiversion"] == "2019"
    assert result["agent_data_generic"][0]["calculated_energy_class"] == "F"
    assert "energy_class" not in result["agent_data_generic"][0]


def test_generic_sql_agent_preserves_distinct_ids_with_related_recorded_addresses():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"50a_uuid": "uuid-1", "address": "Artemisgatan 17", "energy_class": "D"},
            {"50a_uuid": "uuid-2", "address": "Artemisgatan 17A", "energy_class": "C"},
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["matched_building_id"] is None
    options = result["metadata"]["building_candidate_matches"]
    assert {option["byggnadsid"] for option in options} == {"UUID-1", "UUID-2"}
    question = result["metadata"]["clarification"]["question_asked"]
    assert "Artemisgatan 17" in question
    assert "Artemisgatan 17A" in question


def test_generic_sql_agent_blocks_specific_address_when_rows_have_different_building_ids_without_addresses():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {"50a_uuid": "uuid-1", "energy_class": "D"},
            {"50a_uuid": "uuid-2", "heating_system": "district heating"},
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"
    question = result["metadata"]["clarification"]["question_asked"]
    assert "UUID-1" in question and "UUID-2" in question
    assert "Address unavailable" in question


def test_generic_sql_agent_blocks_common_address_across_multiple_building_ids():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "03-31-LILLARAMSJOE2:13-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Morgongåva",
                "epc_idkommun": "Heby",
                "epc_idpostnr": "74450",
                "epc_godkand": "2021-08-19",
                "epc_egienergiprestanda": 143,
            },
            {
                "byggnadsid": "01-60-BOKBINDAREN6-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Täby",
                "epc_idkommun": "Täby",
                "epc_idpostnr": "18770",
                "epc_godkand": "2020-12-01",
                "epc_egennybyggar": 1975,
            },
            {
                "byggnadsid": "24-82-BURTRAESKS-GAMMELBYN71:4-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Burträsk",
                "epc_idkommun": "Skellefteå",
                "epc_idpostnr": "93732",
                "epc_godkand": "2020-12-23",
                "epc_egennybyggar": 1966,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Ringvägen 10"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"
    assert "Which city or municipality" in result["metadata"]["clarification"]["question_asked"]
    assert len(result["metadata"]["building_candidate_matches"]) == 3
    assert result["metadata"]["pending_ambiguous_address"] == "Ringvägen 10"
    assert "143 kWh" not in result.get("final_response", "")


def test_generic_sql_agent_uses_location_hint_for_common_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-60-BOKBINDAREN6-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Täby",
                "epc_idkommun": "Täby",
                "epc_idpostnr": "18770",
                "epc_egiversion": "2020",
            },
            {
                "byggnadsid": "24-82-BURTRAESKS-GAMMELBYN71:4-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Burträsk",
                "epc_idkommun": "Skellefteå",
                "epc_idpostnr": "93732",
                "epc_egiversion": "2020",
            },
            {
                "byggnadsid": "01-15-GARNS-EKSKOGEN1:82-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Brottby",
                "epc_idkommun": "Vallentuna",
                "epc_idpostnr": "18697",
                "epc_godkand": "2024-11-15",
                "epc_egienergiprestanda": 221,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Täby",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-60-BOKBINDAREN6-1"
    assert result["metadata"]["generic_sql_trace"]["match_strategy"] == "address_location"


def test_generic_sql_agent_uses_huddinge_hint_before_krokom_row():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "23-09-HISSMOBOELE2:140-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Krokom",
                "epc_idkommun": "Krokom",
                "epc_idpostnr": "83532",
                "epc_godkand": "2020-08-10",
                "epc_egienergiprestanda": 101,
            },
            {
                "byggnadsid": "01-26-SPACKELN6-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Huddinge",
                "epc_idkommun": "Huddinge",
                "epc_idpostnr": "14131",
                "epc_godkand": "2018-04-27",
                "epc_egienergiprestanda": 86,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Huddinge",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "01-26-SPACKELN6-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Huddinge"


def test_generic_sql_agent_uses_location_hint_before_latest_epc_for_common_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-83-LOEVSAANGAREN5-1",
                "epc_idadr": "Sveavägen 17",
                "epc_idpostort": "Sundbyberg",
                "epc_idkommun": "Sundbyberg",
                "epc_idpostnr": "17270",
                "epc_godkand": "2024-01-01",
                "epc_egienergiprestanda": 93,
            },
            {
                "byggnadsid": "18-80-SKRIKAN4-1",
                "epc_idadr": "Sveavägen 17",
                "epc_idpostort": "Örebro",
                "epc_idkommun": "Örebro",
                "epc_idpostnr": "70214",
                "epc_godkand": "2009-01-07",
                "epc_egienergiprestanda": 172,
            },
            {
                "byggnadsid": "18-80-SKRIKAN4-1",
                "epc_idadr": "Sveavägen 17",
                "epc_idpostort": "Örebro",
                "epc_idkommun": "Örebro",
                "epc_idpostnr": "70214",
                "epc_godkand": "2021-07-08",
                "epc_egienergiprestanda": 106,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Sveavägen 17",
                "address_location_hint": "Örebro",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "18-80-SKRIKAN4-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Örebro"
    assert result["agent_data_generic"][0]["epc_godkand"] == "2021-07-08"


def test_generic_sql_agent_uses_location_hint_for_kungsgatan_vaxjo():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "12-66-MALTESHOLM20-1",
                "epc_idadr": "Kungsgatan 10",
                "epc_idpostort": "Hörby",
                "epc_idkommun": "Hörby",
                "epc_idpostnr": "24231",
                "epc_godkand": "2017-06-05",
                "epc_egienergiprestanda": 157,
            },
            {
                "byggnadsid": "07-80-GUNNARGROEPE10-1",
                "epc_idadr": "Kungsgatan 10",
                "epc_idpostort": "Växjö",
                "epc_idkommun": "Växjö",
                "epc_idpostnr": "35233",
                "epc_godkand": "2019-12-19",
                "epc_egienergiprestanda": 104,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Kungsgatan 10",
                "address_location_hint": "Växjö",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "07-80-GUNNARGROEPE10-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Växjö"


def test_generic_sql_agent_blocks_when_city_still_has_multiple_building_ids():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "07-80-GUNNARGROEPE10-1",
                "epc_idadr": "Kungsgatan 10",
                "epc_idpostort": "Växjö",
                "epc_idkommun": "Växjö",
                "epc_idfastbet": "Gunnar Gröpe 10",
                "epc_godkand": "2019-12-19",
                "epc_egienergiprestanda": 104,
            },
            {
                "byggnadsid": "07-80-GUNNARGROEPE9-1",
                "epc_idadr": "Kungsgatan 10",
                "epc_idpostort": "Växjö",
                "epc_idkommun": "Växjö",
                "epc_idfastbet": "Gunnar Gröpe 9",
                "epc_godkand": "2009-11-12",
                "epc_egienergiprestanda": 147,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Kungsgatan 10",
                "address_location_hint": "Växjö",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["building_identity_check"]["candidate_building_ids"] == [
        "07-80-GUNNARGROEPE10-1",
        "07-80-GUNNARGROEPE9-1",
    ]
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"
    assert result.get("agent_data_generic") in (None, [])


def test_generic_sql_agent_uses_municipality_hint_for_common_address():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "04-83-HILLERSTA1:73-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Julita",
                "epc_idkommun": "Katrineholm",
                "epc_idpostnr": "64360",
                "epc_godkand": "2014-10-15",
                "epc_egienergiprestanda": 104,
            },
            {
                "byggnadsid": "01-88-HALLSTA4:30-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Hallstavik",
                "epc_idkommun": "Norrtälje",
                "epc_idpostnr": "76340",
                "epc_godkand": "2017-01-02",
                "epc_egienergiprestanda": 25,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Katrineholm",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["byggnadsid"] == "04-83-HILLERSTA1:73-1"
    assert result["agent_data_generic"][0]["epc_idkommun"] == "Katrineholm"


def test_generic_sql_agent_retains_all_municipality_matches_when_name_is_also_a_town():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "06-80-MAANSARP1:167-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Taberg",
                "epc_idkommun": "Jönköping",
                "epc_idpostnr": "56241",
                "epc_godkand": "2013-02-26",
                "epc_egienergiprestanda": 153,
            },
            {
                "byggnadsid": "06-80-AASKAADAREN12-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Jönköping",
                "epc_idkommun": "Jönköping",
                "epc_idpostnr": "55454",
                "epc_godkand": "2018-02-14",
                "epc_egienergiprestanda": 68,
            },
            {
                "byggnadsid": "01-15-GARNS-EKSKOGEN1:82-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Brottby",
                "epc_idkommun": "Vallentuna",
                "epc_idpostnr": "18697",
                "epc_godkand": "2024-11-15",
                "epc_egienergiprestanda": 221,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Jönköping",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    options = result["metadata"]["building_candidate_matches"]
    assert {option["byggnadsid"] for option in options} == {"06-80-AASKAADAREN12-1", "06-80-MAANSARP1:167-1"}


def test_generic_sql_agent_blocks_when_location_hint_matches_no_returned_location():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-26-SPACKELN6-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Huddinge",
                "epc_idkommun": "Huddinge",
                "epc_idpostnr": "14131",
                "epc_godkand": "2018-05-08",
                "epc_egienergiprestanda": 86,
            },
            {
                "byggnadsid": "01-88-HALLSTA4:30-1",
                "epc_idadr": "Ringvägen 10",
                "epc_idpostort": "Hallstavik",
                "epc_idkommun": "Norrtälje",
                "epc_idpostnr": "76340",
                "epc_godkand": "2017-01-02",
                "epc_egienergiprestanda": 25,
            },
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {
                "address": "Ringvägen 10",
                "address_location_hint": "Sollentuna",
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is True
    assert result["metadata"]["building_identity_check"]["status"] == "ambiguous"
    assert result["metadata"]["building_identity_check"]["location_hint_no_match"] is True
    assert result["metadata"]["clarification"]["reason"] == "ambiguous_address"
    assert result["metadata"]["generic_sql_trace"]["match_strategy"] == "address_location_no_match"
    assert result["metadata"]["generic_sql_trace"]["returned_values_used"] == {}
    assert result.get("agent_data_generic") in (None, [])


def test_generic_sql_agent_exposes_epc_heating_system_alias():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "01-80-SKYTTEN2-2",
                "50a_uuid": "5174853a-d8c1-4b87-8a34-d05c054e443d",
                "01a_fnr": "010113926",
                "address": "Artemisgatan 17",
                "epc_idadr": "Artemisgatan 17",
                "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                "epc_egifjarrvarme": 455130,
            }
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["agent_data_generic"][0]["heating_system"] == "district heating"
    assert result["agent_data_generic"][0]["district_heating_use"] == 455130


def test_generic_sql_agent_keeps_district_heating_breakdown_separate():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": True,
        "data": [
            {
                "byggnadsid": "07-80-GUNNARGROEPE10-1",
                "epc_idadr": "Examplegatan 10",
                "epc_idpostort": "Växjö",
                "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                "epc_egifjarrvarmeuppv": 1169500,
                "epc_egifjarrvarmevv": 33300,
            }
        ],
        "trace": {"query_type": "building_by_address", "execution_status": "success"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Examplegatan 10", "address_location_hint": "Växjö"},
            "parallel": {},
        }
    )

    row = result["agent_data_generic"][0]
    assert row["heating_system"] == "district heating"
    assert "district_heating_use" not in row
    assert row["district_heating_space_heating"] == 1169500
    assert row["district_heating_domestic_hot_water"] == 33300


def test_generic_sql_missing_rows_does_not_block_hybrid_vector_advice():
    module = import_building_flow_graph_module()
    module.sql_mapper_layer.execute = lambda *args, **kwargs: {
        "ok": False,
        "data": [],
        "message": "No buildings matched address 'Artemisgatan 17'.",
        "trace": {"query_type": "building_by_address", "execution_status": "not_found"},
    }

    result = module.generic_sql_agent_node(
        {
            "metadata": {"address": "Artemisgatan 17"},
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
            },
            "parallel": {},
        }
    )

    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["clarification"]["reason"] == "building_not_found"
    assert result["done_generic_sql"] is True


def test_llm_summarizer_adds_freshness_and_uncertainty_metadata():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "what should our building prioritize?",
            "messages": [{"role": "user", "content": "what should our building prioritize?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Main Street 1"},
            "aggregated_data": {
                "generic_sql": {
                    "building_id": "uuid-1",
                    "byggnadsid": "01-80-TESTA1-1",
                    "energy_declaration_year": 2016,
                    "heating_system": "district heating",
                }
            },
        }
    )

    assert result["metadata"]["data_freshness"]["freshness_status"] == "old"
    assert result["metadata"]["uncertainty"]["confidence"] in {"medium", "low", "high"}
    assert "Note:" in result["final_response"]


def test_llm_summarizer_treats_epc_heating_as_confirmed_fact():
    module = import_building_flow_graph_module()

    result = module.llm_summarizer_node(
        {
            "last_message": "what is the heating system in my building?",
            "messages": [{"role": "user", "content": "what is the heating system in my building?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Artemisgatan 17"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-SKYTTEN2-2",
                        "address": "Artemisgatan 17",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                    }
                ]
            },
        }
    )

    facts = result["metadata"]["retrieved_facts"]
    uncertainty = result["metadata"]["uncertainty"]
    assert facts["heating_system"] == "district heating"
    assert "heating_system" in uncertainty["confirmed_facts"]
    assert "heating_system" not in uncertainty["missing_facts"]


def test_llm_summarizer_uses_structured_fallback_on_rate_limit():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Error: Rate limit exceeded. Please retry shortly."
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is my energy performance?",
            "messages": [{"role": "user", "content": "what is my energy performance?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Ringvägen 10"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-26-SPACKELN6-1",
                        "epc_idadr": "Ringvägen 10",
                        "epc_egienergiklass2020_calc": "E",
                        "epc_egienergiprestanda": 86,
                        "epc_egispecifikenergianvandning_calc": 86,
                        "epc_egiprimarenergital2020_calc": 155,
                        "epc_godkand": "2018-05-08",
                    }
                ]
            },
        }
    )

    assert "Error: Rate limit exceeded" not in result["final_response"]
    assert "Building ID: 01-26-SPACKELN6-1" in result["final_response"]
    assert "Your building's declared energy performance is 86 kWh/m2-year." in result["final_response"]
    assert "Energy performance is the value recorded in the energy declaration." in result["final_response"]
    assert "Primary energy number: 155 kWh/m2-year" not in result["final_response"]


def test_llm_summarizer_fallback_labels_district_heating_breakdown():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Error: Rate limit exceeded. Please retry shortly."
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is the district heating breakdown?",
            "messages": [{"role": "user", "content": "what is the district heating breakdown?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Examplegatan 10"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "07-80-GUNNARGROEPE10-1",
                        "epc_idadr": "Examplegatan 10",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                        "epc_egifjarrvarmeuppv": 1169500,
                        "epc_egifjarrvarmevv": 33300,
                    }
                ]
            },
        }
    )

    assert "Total district heating use" not in result["final_response"]
    assert "District heating use: 1169500" not in result["final_response"]
    assert "Your building's district heating for space heating is 1169500 kWh/year." in result["final_response"]
    assert "Your building's district heating for domestic hot water is 33300 kWh/year." in result["final_response"]


def test_llm_summarizer_uses_direct_electricity_facts_without_model_call():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Building ID: 01-84-FURIREN2-1\n\n"
        "Energy class: E\n"
        "Declared energy performance: 136 kWh/m2-year\n"
        "Specific energy use: 129 kWh/m2-year\n"
        "Primary energy number: 103 kWh/m2-year\n"
        "Energy declaration year: 2019\n"
        "Construction year: 1963\n"
        "Heating system: district heating\n"
        "Ventilation: FTX\n"
        "Electricity consumption: 121667 kWh/year"
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is total electricity consumption of my building?",
            "messages": [{"role": "user", "content": "what is total electricity consumption of my building?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Armégatan 32A"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-84-FURIREN2-1",
                        "address": "Armégatan 32A",
                        "epc_el_calc": 121667,
                        "epc_egienergiklass2020_calc": "E",
                        "epc_egienergiprestanda": 136,
                        "epc_egispecifikenergianvandning_calc": 129,
                        "epc_egiprimarenergital2020_calc": 103,
                    }
                ]
            },
        }
    )

    assert "Your building's total electricity consumption is 121667 kWh/year." in result["final_response"]
    assert "This is annual electricity use in the building record." in result["final_response"]
    assert "Energy class: E" not in result["final_response"]
    assert "Ventilation: FTX" not in result["final_response"]
    assert "renovation_information" not in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == "direct_fact_response_targeted"


def test_targeted_fact_response_explains_energy_class_for_beginners():
    module = import_building_flow_graph_module()

    response = module._targeted_building_fact_response(
        user_input="what is my building's energy class?",
        current_address="Armégatan 32A",
        building_id="01-84-FURIREN2-1",
        facts={"energy_class": "E"},
    )

    assert "Your building's declared energy class is E." in response
    assert "Class C corresponds to the new-building reference requirement" in response
    assert "does not establish the building's current condition" in response


def test_llm_summarizer_translates_oden_ventilation_fields_for_beginners():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Energy class is a rating of a building's energy performance."
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is my ventilation system?",
            "messages": [{"role": "user", "content": "what is my ventilation system?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Professorsslingan 51"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-FILOSOFEN2-3",
                        "epc_idadr": "Professorsslingan 51",
                        "epc_venttypf": "Nej",
                        "epc_venttypft": "Nej",
                        "epc_venttypftx": "Ja",
                        "epc_venttypsjalvdrag": "Nej",
                    }
                ]
            },
        }
    )

    assert "Your building's ventilation type is FTX." in result["final_response"]
    assert "FTX ventilation means mechanical supply and exhaust ventilation with heat recovery" in result["final_response"]
    assert "Ventilation matters because it affects air quality" in result["final_response"]
    assert "epc_venttypftx" not in result["final_response"]
    assert "Energy class" not in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == "direct_fact_response_targeted"


def test_llm_summarizer_translates_oden_heating_fields_for_beginners():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "Energy class is a rating of a building's energy performance."
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what is my heating system?",
            "messages": [{"role": "user", "content": "what is my heating system?"}],
            "context": {"parsed_intent": "SQL database", "intent_list": ["SQL database"]},
            "metadata": {"address": "Professorsslingan 51"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-FILOSOFEN2-3",
                        "epc_idadr": "Professorsslingan 51",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                    }
                ]
            },
        }
    )

    assert "Your building's heating system is district heating." in result["final_response"]
    assert "District heating means heat is produced centrally" in result["final_response"]
    assert "epc_huvudsakliguppvarmning_calc" not in result["final_response"]
    assert "Energy class" not in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == "direct_fact_response_targeted"


def test_llm_summarizer_gives_focused_ftx_explanation_for_ventilation_measures():
    module = import_building_flow_graph_module()
    focused_answer = (
        "FTX ventilation means mechanical supply and exhaust ventilation with heat recovery. "
        "Commission the FTX system if checks show imbalanced airflow or poor recovery. "
        "What prompted the question: draughts, noise, or higher electricity use?"
    )
    module.llm_summarizer.generate_response = lambda *args, **kwargs: focused_answer

    result = module.llm_summarizer_node(
        {
            "last_message": "what are efficiency measures for ventilation system?",
            "messages": [{"role": "user", "content": "what are efficiency measures for ventilation system?"}],
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
            },
            "metadata": {"address": "Armégatan 32A"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-84-FURIREN2-1",
                        "address": "Armégatan 32A",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                        "epc_venttypftx": "Ja",
                        "epc_egienergiklass2020_calc": "E",
                        "epc_egienergiprestanda": 136,
                    }
                ]
            },
        }
    )

    assert "FTX ventilation means mechanical supply and exhaust ventilation with heat recovery" in result["final_response"]
    assert "Commission the FTX system" in result["final_response"]
    assert "Energy class:" not in result["final_response"]
    assert "Electricity consumption:" not in result["final_response"]
    assert "renovation_information" not in result["final_response"]
    assert focused_answer in result["final_response"]
    assert "response_fallback" not in result["metadata"]
    assert "Renewable energy" not in result["final_response"]


def test_llm_summarizer_treats_epc_ventilation_electricity_and_date_as_confirmed():
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: (
        "The record reports FT ventilation. Check current operation before ranking measures; "
        "what changes have been made since the 2009 declaration?"
    )

    result = module.llm_summarizer_node(
        {
            "last_message": "what are the ECMs?",
            "messages": [{"role": "user", "content": "what are the ECMs?"}],
            "context": {
                "parsed_intent": "SQL database ; vector database",
                "intent_list": ["SQL database", "vector database"],
            },
            "metadata": {"address": "Öregrundsgatan 9"},
            "aggregated_data": {
                "generic_sql": [
                    {
                        "byggnadsid": "01-80-LISSABON2-2",
                        "address": "Öregrundsgatan 9",
                        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                        "epc_egienergiklass2020_calc": "F",
                        "epc_egispecifikenergianvandning_calc": 199,
                        "epc_egifjarrvarme": 665500,
                        "epc_venttypft": "Ja",
                        "epc_venttypftx": "Nej",
                        "epc_el_calc": 132000,
                        "epc_godkand": "2009-11-23",
                    }
                ]
            },
        }
    )

    facts = result["metadata"]["retrieved_facts"]
    uncertainty = result["metadata"]["uncertainty"]
    assert facts["ventilation_type"] == "FT"
    assert facts["electricity_use"] == 132000
    assert facts["energy_declaration_year"] == 2009
    assert result["metadata"]["data_freshness"]["energy_declaration_year"] == 2009
    assert "ventilation_type" in uncertainty["confirmed_facts"]
    assert "electricity_use" in uncertainty["confirmed_facts"]
    assert "energy_declaration_year" in uncertainty["confirmed_facts"]
    assert "ventilation_type" not in uncertainty["missing_facts"]
    assert "electricity_use" not in uncertainty["missing_facts"]
    assert "energy_declaration_year" not in uncertainty["missing_facts"]
    assert facts["specific_energy_use"] == 199
    assert "energy_performance" not in facts
    assert uncertainty["missing_facts"] == ["energy_class", "energy_performance", "renovation_information"]
    assert "response_fallback" not in result["metadata"]
    assert result["metadata"]["building_match"]["building_id"] == "01-80-LISSABON2-2"
    assert result["metadata"]["retrieved_facts"]["address"] == "Öregrundsgatan 9"
    assert "what changes have been made since the 2009 declaration?" in result["final_response"]


def _window_advice_state():
    return {
        "last_message": "Should we replace the windows to reduce heating costs?",
        "messages": [
            {"role": "user", "content": "We need to improve comfort without major works this year."},
            *[{"role": "assistant", "content": f"Earlier discussion {i}"} for i in range(8)],
            {"role": "user", "content": "Should we replace the windows to reduce heating costs?"},
        ],
        "context": {
            "parsed_intent": "SQL database ; vector database",
            "intent_list": ["SQL database", "vector database"],
        },
        "metadata": {"address": "Examplegatan 1"},
        "aggregated_data": {
            "generic_sql": [{
                "byggnadsid": "TEST-BUILDING-1",
                "address": "Examplegatan 1",
                "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
                "epc_egennybyggar": 1970,
            }],
            "vector": {
                "hits": [{
                    "source": "synthetic-window-guidance.pdf",
                    "page_content": "Inspect seals and frame condition before deciding on replacement.",
                }],
            },
        },
    }


@pytest.mark.parametrize("model_answer", [
    "Replacing windows may help, but first check seals and frame condition. "
    "(Source: synthetic-window-guidance.pdf) Are draughts the main concern?",
    "What prompted window replacement: draughts, damaged frames, or higher heating costs?",
])
def test_advisory_answers_and_diagnostic_questions_survive_without_category_headings(model_answer):
    module = import_building_flow_graph_module()
    captured = {}

    def generate(prompt, message_list=None, *, language=None):
        captured["prompt"] = prompt
        return model_answer

    module.llm_summarizer.generate_response = generate
    state = _window_advice_state()
    state["metadata"]["response_fallback"] = {"reason": "ecm_response_missing_recommendations"}
    state["metadata"]["response_model_error"] = "Error: previous request failed"

    result = module.llm_summarizer_node(state)

    assert model_answer in result["final_response"]
    assert result["final_response"].startswith("Building ID: TEST-BUILDING-1")
    assert "response_fallback" not in result["metadata"]
    assert "response_model_error" not in result["metadata"]
    assert "improve comfort without major works this year" in captured["prompt"]
    assert "synthetic-window-guidance.pdf" in captured["prompt"]
    assert "Inspect seals and frame condition" in captured["prompt"]
    assert '"epc_egennybyggar": 1970' in captured["prompt"]
    assert "solar PV" not in result["final_response"]


@pytest.mark.parametrize(("model_answer", "reason"), [
    ("Error: Rate limit exceeded. Please retry shortly.", "response_model_error"),
    ("", "response_model_empty"),
    ("   ", "response_model_empty"),
    (None, "response_model_empty"),
    ("No content returned from the model.", "response_model_empty"),
])
def test_advisory_generation_failure_does_not_masquerade_as_tailored_recommendations(model_answer, reason):
    module = import_building_flow_graph_module()
    module.llm_summarizer.generate_response = lambda *args, **kwargs: model_answer

    result = module.llm_summarizer_node(_window_advice_state())

    assert "I couldn't generate the advisory answer" in result["final_response"]
    assert "Available building-record facts:" in result["final_response"]
    assert "district heating" in result["final_response"]
    assert "solar PV" not in result["final_response"]
    assert "2-4 week" not in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == reason


def test_advisory_model_exception_returns_honest_fallback_and_keeps_identity():
    module = import_building_flow_graph_module()

    def unavailable(*args, **kwargs):
        raise RuntimeError("Synthetic connection failure")

    module.llm_summarizer.generate_response = unavailable

    result = module.llm_summarizer_node(_window_advice_state())

    assert result["final_response"].startswith("Building ID: TEST-BUILDING-1")
    assert "I couldn't generate the advisory answer" in result["final_response"]
    assert result["metadata"]["response_fallback"]["reason"] == "response_model_exception"
    assert result["metadata"]["response_model_error"] == "RuntimeError"
    assert result["session_state"]["metadata"]["byggnadsid"] == "TEST-BUILDING-1"


def _task2_address_row(building_id, address, town, postcode, version, municipality=None):
    """Controlled EPC rows; fixture versions do not assert live register facts."""
    return {
        "byggnadsid": building_id,
        "epc_idadr": address,
        "epc_idpostort": town,
        "epc_idkommun": municipality or town,
        "epc_idpostnr": postcode,
        "epc_egiversion": version,
        "epc_huvudsakliguppvarmning_calc": "Fjarrvarme",
    }


@pytest.mark.parametrize(("question", "expected_status", "expected_ids"), [
    (
        "Tulegatan 5A, 11353 Stockholm och Rådmansgatan 31, 11353 Stockholm. Vilken byggnad ska vi använda?",
        "same_building", {"01-80-HAELSAN10-2"},
    ),
    (
        "Tulegatan 5A och Rådmansgatan 31, 113 53 Stockholm. Vilken byggnad ska vi använda?",
        "same_building", {"01-80-HAELSAN10-2"},
    ),
    (
        "Tulegatan 5A and Rådmansgatan 31 in Stockholm. Which building should we use?",
        "same_building", {"01-80-HAELSAN10-2"},
    ),
    (
        "Tulegatan 5A i Arboga och Rådmansgatan 31 i Motala. Vilken byggnad ska vi använda?",
        "different_buildings", {"19-84-LEOPARDEN5-1", "05-83-RAEFSAN5-1"},
    ),
    (
        "Tulegatan 5A i Stockholm och Tulegatan 5A i Arboga. Vilken byggnad ska vi använda?",
        "different_buildings", {"01-80-HAELSAN10-2", "19-84-LEOPARDEN5-1"},
    ),
    (
        "Ringvägen 10, 117 26 Stockholm and Kungsgatan 10, 11138 Stockholm. Which building should we use?",
        "different_buildings", {"01-80-TAPETEN2-1", "01-80-KAAKENHUSEN39-1"},
    ),
])
def test_multi_address_location_constraints_are_scoped_to_each_reference(
    monkeypatch, question, expected_status, expected_ids,
):
    module = import_building_flow_graph_module()
    address_rows = {
        "Tulegatan 5A": [
            _task2_address_row("01-80-HAELSAN10-2", "Tulegatan 5A", "Stockholm", "11353", "2019"),
            _task2_address_row("19-84-LEOPARDEN5-1", "Tulegatan 5A", "Arboga", "73245", "2024"),
        ],
        "Rådmansgatan 31": [
            _task2_address_row("01-80-HAELSAN10-2", "Rådmansgatan 31", "Stockholm", "11353", "2019"),
            _task2_address_row("05-83-RAEFSAN5-1", "Rådmansgatan 31", "Motala", "59135", "2024"),
        ],
        "Ringvägen 10": [
            _task2_address_row("01-80-TAPETEN2-1", "Hornsgatan 104", "Stockholm", "11726", "2015"),
            _task2_address_row("01-80-TESTPOST1-1", "Ringvägen 10", "Stockholm", "11727", "2024"),
        ],
        "Kungsgatan 10": [
            _task2_address_row("01-80-KAAKENHUSEN39-1", "Brunnsgatan 9", "Stockholm", "11138", "2010"),
            _task2_address_row("01-80-TESTPOST2-1", "Kungsgatan 10", "Stockholm", "11353", "2024"),
        ],
    }
    calls = []

    def execute(query, **kwargs):
        assert query == "building_by_address"
        calls.append(kwargs["address"])
        return {"ok": True, "data": copy.deepcopy(address_rows[kwargs["address"]]), "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    # The established extraction wrapper keeps its bare-string return type.
    assert all(isinstance(address, str) for address in module._extract_address_candidates_from_text(question))
    result = module.resolve_multi_address_identity_message(question, {"response_language": "sv"})

    assert result is not None
    resolution = result["metadata"]["multi_address_resolution"]
    assert resolution["status"] == expected_status
    actual_ids = {
        building_id
        for candidate in resolution["candidates"]
        for building_id in (candidate.get("building_ids") or [candidate.get("building_id")])
        if building_id
    }
    assert actual_ids == expected_ids
    assert len(resolution["candidates"]) == 2
    assert len(calls) == 2
    if expected_status == "same_building":
        assert result["metadata"]["same_building_multiple_addresses"] is True
        assert result["metadata"]["building_identity_check"]["status"] == "passed"
    else:
        assert result["metadata"]["clarification"]["needed"] is True
        assert result["metadata"].get("building_identity_check", {}).get("status") != "passed"


@pytest.mark.parametrize("entrypoint", ["single-address", "generic-sql"])
def test_independent_city_municipality_and_postcode_metadata_are_all_constraints(monkeypatch, entrypoint):
    module = import_building_flow_graph_module()
    target_id = "06-80-TESTLOCATION1-1"
    rows = [
        _task2_address_row(target_id, "Parkgatan 10", "Huskvarna", "56131", "2019", "Jönköping"),
        _task2_address_row("06-80-TESTLOCATION2-1", "Parkgatan 10", "Huskvarna", "56132", "2024", "Jönköping"),
        _task2_address_row("06-80-TESTLOCATION3-1", "Parkgatan 10", "Jönköping", "55320", "2024", "Jönköping"),
        # Deliberately conflicting register fields exercise conjunction rather
        # than relying on an external postcode-to-municipality lookup.
        _task2_address_row("06-80-TESTLOCATION4-1", "Parkgatan 10", "Huskvarna", "56131", "2024", "Habo"),
    ]
    monkeypatch.setattr(module.sql_mapper_layer, "execute", lambda *args, **kwargs: {
        "ok": True, "data": copy.deepcopy(rows), "trace": {},
    })
    metadata = {"address": "Parkgatan 10", "city": "Huskvarna", "municipality": "Jönköping", "postal_code": "56131"}

    if entrypoint == "single-address":
        result = module._resolve_single_address_candidate("Parkgatan 10", metadata)
        assert result["building_ids"] == [target_id]
        assert result["building_id"] == target_id
    else:
        result = module.generic_sql_agent_node({"metadata": metadata, "parallel": {}})
        assert result.get("identity_gate_blocked") is not True
        assert {row["byggnadsid"] for row in result["agent_data_generic"]} == {target_id}


def test_conflicting_independent_city_and_postcode_metadata_never_selects_a_building(monkeypatch):
    module = import_building_flow_graph_module()
    rows = [
        _task2_address_row("01-80-KAAKENHUSEN39-1", "Brunnsgatan 9", "Stockholm", "11138", "2010"),
        _task2_address_row("07-80-GUNNARGROEPE9-1", "Nygatan 11", "Växjö", "35233", "2024"),
    ]
    monkeypatch.setattr(module.sql_mapper_layer, "execute", lambda *args, **kwargs: {
        "ok": True, "data": copy.deepcopy(rows), "trace": {},
    })

    result = module._resolve_single_address_candidate("Kungsgatan 10", {
        "city": "Stockholm", "postcode": "35233",
    })

    assert result["ok"] is False
    assert result["building_id"] is None
    assert result["building_ids"] == []


def test_multi_address_lookup_keeps_older_distinct_id_and_latest_epc_within_each_id(monkeypatch):
    module = import_building_flow_graph_module()
    rows = [
        _task2_address_row("01-80-KAAKENHUSEN39-1", "Brunnsgatan 7", "Stockholm", "11138", "2000"),
        _task2_address_row("01-80-KAAKENHUSEN39-1", "Brunnsgatan 9", "Stockholm", "11138", "2010"),
        _task2_address_row("01-80-KAAKENHUSEN40-1", "Brunnsgatan 11", "Stockholm", "11138", "2024"),
    ]
    monkeypatch.setattr(module.sql_mapper_layer, "execute", lambda *args, **kwargs: {
        "ok": True, "data": copy.deepcopy(rows), "trace": {},
    })

    result = module._resolve_single_address_candidate("Kungsgatan 10", {"city": "Stockholm"})

    assert set(result["building_ids"]) == {"01-80-KAAKENHUSEN39-1", "01-80-KAAKENHUSEN40-1"}
    assert result["building_id"] is None
    versions = {(row["byggnadsid"], row["epc_egiversion"]) for row in result["rows"]}
    assert versions == {("01-80-KAAKENHUSEN39-1", "2010"), ("01-80-KAAKENHUSEN40-1", "2024")}


def test_multi_address_ambiguous_lookup_plus_unique_match_does_not_prove_alias(monkeypatch):
    module = import_building_flow_graph_module()
    shared = "01-80-HAELSAN10-2"
    other = "01-80-HAELSAN10-1"

    def execute(query, **kwargs):
        assert query == "building_by_address"
        if kwargs["address"] == "Tulegatan 5A":
            rows = [_task2_address_row(shared, "Tulegatan 5A", "Stockholm", "11353", "2019")]
        else:
            rows = [
                _task2_address_row(shared, "Rådmansgatan 31", "Stockholm", "11353", "2019"),
                _task2_address_row(other, "Döbelnsgatan 18", "Stockholm", "11353", "2019"),
            ]
        return {"ok": True, "data": rows, "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    result = module.resolve_multi_address_identity_message(
        "Tulegatan 5A och Rådmansgatan 31 i Stockholm verkar avse samma hus. "
        "Kan du kontrollera det och säga vilket värmesystem som är registrerat?",
        {"response_language": "sv"},
    )

    assert result is not None
    metadata = result["metadata"]
    assert metadata["multi_address_resolution"]["status"] != "same_building"
    assert not metadata.get("same_building_multiple_addresses")
    assert metadata.get("building_identity_check", {}).get("status") != "passed"
    assert metadata["clarification"]["needed"] is True
    for value in (shared, other, "Rådmansgatan 31", "Döbelnsgatan 18"):
        assert value in result["final_response"]


@pytest.mark.parametrize("entrypoint", ["single-address", "multi-address"])
def test_candidate_display_without_city_respects_address_resolution_stage(monkeypatch, entrypoint):
    module = import_building_flow_graph_module()
    rows = [
        _task2_address_row("01-80-KAAKENHUSEN39-1", "Brunnsgatan 7", "Stockholm", "11138", "2000"),
        _task2_address_row("01-80-KAAKENHUSEN39-1", "Brunnsgatan 9", "Stockholm", "11138", "2010"),
        _task2_address_row("07-80-GUNNARGROEPE9-1", "Nygatan 11", "Växjö", "35233", "2024"),
    ]

    def execute(query, **kwargs):
        assert query == "building_by_address"
        data = rows if kwargs["address"] == "Kungsgatan 10" else rows[-1:]
        return {"ok": True, "data": copy.deepcopy(data), "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    monkeypatch.setattr(module, "generate_clarification", lambda **kwargs: pytest.fail("Recorded candidate list must not be rewritten"))
    if entrypoint == "single-address":
        result = module.generic_sql_agent_node({"metadata": {"address": "Kungsgatan 10"}, "parallel": {}})
        answer = module.clarification_node(result)["final_response"]
        assert "Vilken stad eller kommun" in answer
        assert "01-80-KAAKENHUSEN39-1" not in answer
        assert "07-80-GUNNARGROEPE9-1" not in answer
        assert len(result["metadata"]["address_candidate_pool"]) == 2
        return
    else:
        result = module.resolve_multi_address_identity_message(
            "Our property has two addresses: Kungsgatan 10 and Nygatan 11. Which building should we use?", {},
        )
        assert result["metadata"]["clarification"]["needed"] is True
        answer = result["final_response"]

    for value in ("01-80-KAAKENHUSEN39-1", "07-80-GUNNARGROEPE9-1", "Brunnsgatan 7", "Brunnsgatan 9", "Nygatan 11"):
        assert value in answer


@pytest.fixture
def multi_address_multipart_selection_flow(monkeypatch):
    module = import_building_flow_graph_module()
    original = (
        "Our property has two addresses: Kungsgatan 10 and Nygatan 11 in Växjö. "
        "Which heating system is registered, and what should we prioritize?"
    )
    parts = [
        {"question": "Which heating system is registered?", "kind": "fact", "sources": ["SQL database"]},
        {"question": "What should we prioritize?", "kind": "advice", "sources": ["vector database"]},
    ]
    original_context = {
        "request_parts": parts,
        "intent_list": ["SQL database", "vector database"],
        "parsed_intent": "SQL database ; vector database",
        "answer_focus": original,
        "effective_query": original,
    }
    rows = [
        _task2_address_row("07-80-GUNNARGROEPE10-1", "Kungsgatan 10", "Växjö", "35233", "2019"),
        _task2_address_row("07-80-GUNNARGROEPE9-1", "Nygatan 9", "Växjö", "35233", "2010"),
        _task2_address_row("07-80-GUNNARGROEPE9-1", "Nygatan 11", "Växjö", "35233", "2023"),
    ]
    calls = []

    def execute(query, **kwargs):
        calls.append((query, kwargs))
        if query == "buildings_by_building_id":
            data = [row for row in rows if row["byggnadsid"] == kwargs["building_id"]]
        else:
            assert query == "building_by_address"
            data = rows if kwargs["address"] == "Kungsgatan 10" else rows[1:]
        return {"ok": True, "data": copy.deepcopy(data), "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {
        "intents": ["SQL database"], "address": None, "ambigious": False,
    }})
    initial = module.multi_address_resolution_node({
        "last_message": original,
        "messages": [{"role": "user", "content": original}],
        "context": copy.deepcopy(original_context),
        "metadata": {"response_language": "en"},
    })
    assert initial["metadata"]["clarification"]["needed"] is True
    previous = {"metadata": copy.deepcopy(initial["metadata"]), "context": copy.deepcopy(original_context)}

    def advance(reply):
        state = {
            "last_message": reply,
            "messages": [{"role": "user", "content": original}, {"role": "user", "content": reply}],
            "metadata": copy.deepcopy(previous["metadata"]),
            "session_state": copy.deepcopy(previous),
            "parallel": {},
        }
        understood = module.understand_context_node(state)
        lookup = module.generic_sql_agent_node(understood)
        result = {**understood, **lookup}
        previous.update({"metadata": copy.deepcopy(result["metadata"]), "context": copy.deepcopy(result["context"])})
        return result

    return module, initial, advance, calls, original, parts


def test_multi_address_clarification_keeps_original_multipart_request(multi_address_multipart_selection_flow):
    _, initial, _, _, original, parts = multi_address_multipart_selection_flow
    pending = initial["metadata"]["pending_address_query"]

    assert pending["user_message"] == original
    assert pending["context"]["request_parts"] == parts
    assert pending["context"]["intent_list"] == ["SQL database", "vector database"]


@pytest.mark.parametrize("reply", ["07-80-GUNNARGROEPE9-1", "Nygatan 11", "option 2", "2"])
def test_multi_address_selection_resumes_original_fact_and_advice(multi_address_multipart_selection_flow, reply):
    module, initial, advance, calls, original, parts = multi_address_multipart_selection_flow
    options = module._address_candidate_options(initial["metadata"]["building_candidate_matches"])
    expected_id = options[1]["byggnadsid"] if reply in {"option 2", "2"} else "07-80-GUNNARGROEPE9-1"

    result = advance(reply)

    assert result.get("identity_gate_blocked") is not True
    assert calls[-1] == ("buildings_by_building_id", {"building_id": expected_id})
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == expected_id
    assert result["context"]["answer_focus"] == original
    assert result["context"]["effective_query"] == original
    assert result["context"]["request_parts"] == parts
    assert result["context"]["intent_list"] == ["SQL database", "vector database"]


@pytest.mark.parametrize("bad_reply", ["01-80-UNKNOWN1-1", "Unknownsgatan 99", "option 99"])
def test_multi_address_unknown_choice_preserves_options_and_can_recover(multi_address_multipart_selection_flow, bad_reply):
    _, _, advance, calls, original, parts = multi_address_multipart_selection_flow
    lookup_count = len(calls)

    rejected = advance(bad_reply)

    assert rejected["identity_gate_blocked"] is True
    assert len(calls) == lookup_count
    assert rejected["metadata"].get("building_selection_error")
    assert not rejected["metadata"].get("building_id_from_user")
    assert rejected["metadata"]["building_candidate_matches"]
    recovered = advance("Nygatan 11")
    assert recovered.get("identity_gate_blocked") is not True
    assert calls[-1] == ("buildings_by_building_id", {"building_id": "07-80-GUNNARGROEPE9-1"})
    assert recovered["context"]["answer_focus"] == original
    assert recovered["context"]["request_parts"] == parts
    assert recovered["context"]["intent_list"] == ["SQL database", "vector database"]


@pytest.mark.parametrize(("question", "expected_address"), [
    ("What heating system does Teknikringen 37A in Stockholm have?", "Teknikringen 37A"),
    ("What energy performance does Norra Stationsgatan 10 in Stockholm have?", "Norra Stationsgatan 10"),
    ("What energy performance does NorraStationsgatan 10 in Stockholm have?", "NorraStationsgatan 10"),
    ("What heating system does S:t Eriksgatan 10 in Stockholm have?", "S:t Eriksgatan 10"),
    ("Vilket värmesystem har huset på S:t Göransgatan 10 i Stockholm?", "S:t Göransgatan 10"),
])
@pytest.mark.parametrize("stored_key", ["address", "address_from_user", "requested_address"])
def test_address_grammar_cleanup_keeps_full_street_and_separates_city(question, expected_address, stored_key):
    module = import_building_flow_graph_module()

    assert module._extract_address_candidate_from_text(question) == expected_address
    assert module._extract_stored_address_from_metadata({stored_key: question}) == expected_address
    assert module._extract_address_location_hint_from_text(question, expected_address) == "Stockholm"


@pytest.mark.parametrize("fragment", [
    "energy performance 145",
    "heating cost 1200",
    "primärenergital 145 kWh/m² och år",
    "Atemp 6798 m²",
])
@pytest.mark.parametrize("stored_key", ["address", "address_from_user", "requested_address"])
def test_metric_fragments_are_not_graph_or_stored_addresses(fragment, stored_key):
    module = import_building_flow_graph_module()

    assert module._extract_address_candidate_from_text(fragment) is None
    assert module._extract_stored_address_from_metadata({stored_key: fragment}) is None


def test_same_street_city_switch_discards_selected_id_and_facts_before_address_lookup(monkeypatch):
    module = import_building_flow_graph_module()
    stockholm_id = "01-80-TAPETEN2-1"
    arboga_id = "19-84-TESTCITYSWITCH1-1"
    old_metadata = {
        "address": "Ringvägen 10",
        "address_from_user": "Ringvägen 10",
        "address_location_hint": "Stockholm",
        "city": "Stockholm",
        "byggnadsid": stockholm_id,
        "building_id": stockholm_id,
        "building_id_from_user": stockholm_id,
        "selected_address_building_id": stockholm_id,
        "selected_brf_building_id": stockholm_id,
        "selected_brf_addresses": ["Hornsgatan 104, Stockholm"],
        "selected_brf_lookup_address": "Ringvägen 10",
        "brf_resolution": {"status": "resolved_by_user_selection", "selected_building_id": stockholm_id},
        "building_identity_check": {"status": "passed", "matched_building_id": stockholm_id},
        "retrieved_facts": {"energy_class": "G", "heating_system": "district heating"},
        "building_information": {"building_id": stockholm_id, "energy_class": "G"},
    }
    prior_context = {"address": "Ringvägen 10", "address_location_hint": "Stockholm", "intent_list": ["SQL database"]}
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {
        "address": "Ringvägen 10", "address_location_hint": "Stockholm", "intents": ["SQL database"],
    }})
    calls = []

    def execute(query, **kwargs):
        calls.append((query, kwargs))
        assert query == "building_by_address"
        assert kwargs == {"address": "Ringvägen 10"}
        return {"ok": True, "data": [
            _task2_address_row(stockholm_id, "Hornsgatan 104", "Stockholm", "11726", "2024"),
            _task2_address_row(arboga_id, "Ringvägen 10", "Arboga", "73245", "2019"),
        ], "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    question = "What is the energy class for Ringvägen 10 in Arboga?"
    state = module.understand_context_node({
        "last_message": question,
        "messages": [{"role": "user", "content": question}],
        "metadata": copy.deepcopy(old_metadata),
        "session_state": {"metadata": copy.deepcopy(old_metadata), "context": copy.deepcopy(prior_context)},
        "parallel": {},
    })

    for key in ("building_id_from_user", "selected_address_building_id", "selected_brf_building_id", "byggnadsid", "building_id", "retrieved_facts", "building_information"):
        assert key not in state["metadata"]
    assert state["metadata"]["address"] == "Ringvägen 10"
    assert state["metadata"]["address_location_hint"] == "Arboga"
    result = module.generic_sql_agent_node(state)
    assert calls == [("building_by_address", {"address": "Ringvägen 10"})]
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == arboga_id
    assert {row["byggnadsid"] for row in result["agent_data_generic"]} == {arboga_id}


def test_city_only_correction_after_selection_replaces_identity_and_reuses_question(monkeypatch):
    module = import_building_flow_graph_module()
    old_id = "01-80-TAPETEN2-1"
    next_id = "19-84-TESTCORRECTEDCITY1-1"
    original = "Vilken energiklass har Ringvägen 10, Stockholm?"
    context = {"address": "Ringvägen 10", "address_location_hint": "Stockholm",
               "intent_list": ["SQL database"], "answer_focus": original, "effective_query": original,
               "request_parts": [{"question": original, "kind": "fact", "sources": ["SQL database"]}]}
    metadata = {"address": "Ringvägen 10", "address_from_user": "Ringvägen 10", "address_location_hint": "Stockholm",
                "byggnadsid": old_id, "building_id": old_id, "selected_address_building_id": old_id,
                "building_identity_check": {"status": "passed", "matched_building_id": old_id},
                "same_building_multiple_addresses": True, "epc_record_address": "Hornsgatan 104",
                "retrieved_facts": {"building_id": old_id, "energy_class": "G"},
                "pending_building_request": {"question": original, "context": copy.deepcopy(context)}}
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {
        "address": None, "address_location_hint": "Stockholm", "intents": ["SQL database"],
    }})
    calls = []

    def execute(query, **kwargs):
        calls.append((query, kwargs))
        assert query == "building_by_address"
        return {"ok": True, "data": [
            _task2_address_row(old_id, "Hornsgatan 104", "Stockholm", "11726", "2024"),
            _task2_address_row(next_id, "Ringvägen 10", "Arboga", "73245", "2024"),
        ], "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    corrected = module.understand_context_node({
        "last_message": "Jag menade Arboga.", "messages": [], "metadata": copy.deepcopy(metadata),
        "session_state": {"metadata": copy.deepcopy(metadata), "context": copy.deepcopy(context)},
    })
    for key in ("byggnadsid", "building_id", "selected_address_building_id", "building_identity_check",
                "retrieved_facts", "same_building_multiple_addresses", "epc_record_address", "pending_building_request"):
        assert key not in corrected["metadata"]
    assert corrected["metadata"]["address"] == "Ringvägen 10"
    assert corrected["metadata"]["address_location_hint"] == "Arboga"
    assert corrected["context"]["answer_focus"] == original.replace("Stockholm", "Arboga")
    assert corrected["context"]["request_parts"][0]["question"] == original.replace("Stockholm", "Arboga")
    result = module.generic_sql_agent_node(corrected)
    assert calls == [("building_by_address", {"address": "Ringvägen 10"})]
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == next_id


@pytest.mark.parametrize("question", [
    "Rättelse: budgeten är 20 000 kr.",
    "Jag menade värmepump, inte fjärrvärme.",
])
def test_non_location_correction_keeps_selected_identity_despite_noisy_parser(monkeypatch, question):
    module = import_building_flow_graph_module()
    building_id = "01-80-TAPETEN2-1"
    metadata = {"address": "Ringvägen 10", "address_from_user": "Ringvägen 10",
                "address_location_hint": "Stockholm", "byggnadsid": building_id, "building_id": building_id,
                "selected_address_building_id": building_id,
                "building_identity_check": {"status": "passed", "matched_building_id": building_id},
                "retrieved_facts": {"building_id": building_id, "energy_class": "G"},
                "same_building_multiple_addresses": True, "epc_record_address": "Hornsgatan 104"}
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": {
        "address": None, "address_location_hint": "Arboga", "municipality": "Arboga", "intents": ["SQL database"],
    }})
    result = module.understand_context_node({
        "last_message": question, "messages": [], "metadata": copy.deepcopy(metadata),
        "session_state": {"metadata": copy.deepcopy(metadata), "context": {"address": "Ringvägen 10"}},
    })
    for key, value in metadata.items():
        assert result["metadata"][key] == value
    assert result["context"]["address_location_hint"] == "Stockholm"
    assert "municipality" not in result["context"]


def test_multi_address_exact_listed_id_is_blocked_when_lookup_returns_another_id(
    monkeypatch, multi_address_multipart_selection_flow,
):
    module, _, advance, calls, original, parts = multi_address_multipart_selection_flow
    original_execute = module.sql_mapper_layer.execute
    selected_id = "07-80-GUNNARGROEPE9-1"
    returned_id = "07-80-GUNNARGROEPE10-1"

    def mismatched_lookup(query, **kwargs):
        if query != "buildings_by_building_id":
            return original_execute(query, **kwargs)
        calls.append((query, kwargs))
        assert kwargs["building_id"] == selected_id
        return {"ok": True, "data": [
            _task2_address_row(returned_id, "Kungsgatan 10", "Växjö", "35233", "2024"),
        ], "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", mismatched_lookup)
    result = advance(selected_id)

    assert calls[-1] == ("buildings_by_building_id", {"building_id": selected_id})
    assert result["identity_gate_blocked"] is True
    assert result["metadata"]["building_identity_check"]["status"] == "conflict"
    assert "agent_data_generic" not in result
    assert result["context"]["answer_focus"] == original
    assert result["context"]["request_parts"] == parts


def test_swedish_same_house_identity_success_continues_requested_heating_answer(monkeypatch):
    module = import_building_flow_graph_module()
    building_id = "01-80-HAELSAN10-2"

    def execute(query, **kwargs):
        assert query == "building_by_address"
        address = kwargs["address"]
        return {"ok": True, "data": [
            _task2_address_row(building_id, address, "Stockholm", "11353", "2019"),
        ], "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    question = (
        "Tulegatan 5A och Rådmansgatan 31 i Stockholm verkar avse samma hus. "
        "Kan du kontrollera det och säga vilket värmesystem som är registrerat?"
    )
    result = module.resolve_multi_address_identity_message(question, {"response_language": "sv"})

    assert result is not None
    assert result["metadata"]["multi_address_resolution"]["status"] == "same_building"
    assert result["metadata"]["building_identity_check"]["status"] == "passed"
    assert result["multi_address_resolution_complete"] is False


@pytest.fixture
def stale_brf_new_multi_address(monkeypatch):
    module = import_building_flow_graph_module()
    original = (
        "Our property has two addresses: Ringvägen 10 in Arboga and Kungsgatan 10 in Växjö. "
        "Which heating system is registered, and what should we prioritize?"
    )
    parts = [
        {"question": "Which heating system is registered?", "kind": "fact", "sources": ["SQL database"]},
        {"question": "What should we prioritize?", "kind": "advice", "sources": ["vector database"]},
    ]
    original_context = {
        "address": "Ringvägen 10", "address_location_hint": "Arboga",
        "request_parts": parts, "answer_focus": original, "effective_query": original,
        "intent_list": ["SQL database", "vector database"],
        "parsed_intent": "SQL database ; vector database",
    }
    old_id = "01-80-TAPETEN2-1"
    arboga_id = "19-84-TESTCITYSWITCH1-1"
    vaxjo_id = "07-80-GUNNARGROEPE9-1"
    old_metadata = {
        "address": "Ringvägen 10", "address_location_hint": "Stockholm", "city": "Stockholm",
        "building_id": old_id, "byggnadsid": old_id, "selected_brf_building_id": old_id,
        "brf_name": "Earlier Association",
        "brf_resolution": {"status": "resolved_by_user_selection", "selected_building_id": old_id},
        "selected_brf_addresses": ["Hornsgatan 104, Stockholm"],
        "selected_brf_lookup_address": "Ringvägen 10",
        "building_identity_check": {"status": "passed", "matched_building_id": old_id},
        "50a_uuid": "old-epc-uuid", "oden_uuid": "old-oden-uuid",
        "retrieved_facts": {"energy_class": "G"},
    }
    rows = [
        _task2_address_row(old_id, "Hornsgatan 104", "Stockholm", "11726", "2024"),
        _task2_address_row(arboga_id, "Ringvägen 10", "Arboga", "73245", "2019"),
        _task2_address_row(vaxjo_id, "Nygatan 11", "Växjö", "35233", "2019"),
    ]
    calls = []

    def execute(query, **kwargs):
        calls.append((query, kwargs))
        if query == "buildings_by_building_id":
            data = [row for row in rows if row["byggnadsid"] == kwargs["building_id"]]
        else:
            assert query == "building_by_address"
            data = rows[:2] if kwargs["address"] == "Ringvägen 10" else rows[2:]
        return {"ok": True, "data": copy.deepcopy(data), "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    monkeypatch.setattr(DummyParseIntentAgent, "result", {"context": copy.deepcopy(original_context)})
    return module, original, original_context, old_metadata, calls, arboga_id, vaxjo_id


def test_stale_brf_selection_cannot_override_city_reply_to_new_multi_address_choices(stale_brf_new_multi_address):
    module, original, original_context, old_metadata, calls, _, vaxjo_id = stale_brf_new_multi_address
    initial = module.multi_address_resolution_node({
        "last_message": original, "messages": [{"role": "user", "content": original}],
        "metadata": copy.deepcopy(old_metadata), "context": copy.deepcopy(original_context),
    })
    metadata = initial["metadata"]
    assert metadata["clarification"]["needed"] is True
    for key in ("selected_brf_building_id", "brf_resolution", "brf_name", "selected_brf_addresses", "selected_brf_lookup_address", "50a_uuid", "oden_uuid", "retrieved_facts"):
        assert key not in metadata

    followup = module.understand_context_node({
        "last_message": "Växjö",
        "messages": [{"role": "user", "content": original}, {"role": "user", "content": "Växjö"}],
        "metadata": copy.deepcopy(metadata),
        "session_state": {"metadata": copy.deepcopy(metadata), "context": copy.deepcopy(original_context)},
        "parallel": {},
    })
    result = module.generic_sql_agent_node(followup)

    assert calls[-1] == ("buildings_by_building_id", {"building_id": vaxjo_id})
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == vaxjo_id
    assert followup["context"]["answer_focus"] == original
    assert followup["context"]["request_parts"] == original_context["request_parts"]
    assert followup["context"]["intent_list"] == ["SQL database", "vector database"]


def test_checked_initial_multi_address_parse_keeps_choices_and_resumes_original_parts(stale_brf_new_multi_address):
    module, original, original_context, old_metadata, calls, arboga_id, vaxjo_id = stale_brf_new_multi_address
    direct = module.resolve_multi_address_identity_message(original, copy.deepcopy(old_metadata))
    assert direct["metadata"]["clarification"]["needed"] is True
    assert len(calls) == 2
    # BuildingAgent replaces snapshot metadata after the direct resolver. The
    # previous parser context may still describe the earlier Stockholm building.
    old_context = {"address": "Ringvägen 10", "address_location_hint": "Stockholm", "intent_list": ["SQL database"]}
    parsed = module.understand_context_node({
        "last_message": original,
        "messages": [{"role": "user", "content": original}],
        "metadata": copy.deepcopy(direct["metadata"]),
        "session_state": {"metadata": copy.deepcopy(direct["metadata"]), "context": old_context},
        "multi_address_resolution_checked": True,
        "multi_address_resolution_complete": direct["multi_address_resolution_complete"],
        "final_response": direct["final_response"],
        "parallel": {},
    })
    checked = {**parsed, **module.multi_address_resolution_node(parsed)}

    assert len(calls) == 2
    options = module._address_candidate_options(checked["metadata"]["building_candidate_matches"])
    assert {option["byggnadsid"] for option in options} == {arboga_id, vaxjo_id}
    assert checked["metadata"]["pending_address_query"]["user_message"] == original
    assert checked["metadata"]["pending_address_query"]["context"]["request_parts"] == original_context["request_parts"]

    reply = "option 2"
    followup = module.understand_context_node({
        "last_message": reply,
        "messages": [{"role": "user", "content": original}, {"role": "user", "content": reply}],
        "metadata": copy.deepcopy(checked["metadata"]),
        "session_state": {"metadata": copy.deepcopy(checked["metadata"]), "context": copy.deepcopy(checked["context"])},
        "parallel": {},
    })
    result = module.generic_sql_agent_node(followup)

    assert calls[-1] == ("buildings_by_building_id", {"building_id": options[1]["byggnadsid"]})
    assert result.get("identity_gate_blocked") is not True
    assert followup["context"]["answer_focus"] == original
    # Initial advice parsing may add established ECM retrieval keywords. The
    # full user request must remain intact when that query is restored.
    assert followup["context"]["effective_query"].startswith(original)
    assert followup["context"]["effective_query"] == checked["metadata"]["pending_address_query"]["context"]["effective_query"]
    assert followup["context"]["request_parts"] == original_context["request_parts"]
    assert followup["context"]["intent_list"] == ["SQL database", "vector database"]


def test_postcode_sentence_with_comma_city_resolves_existing_address_candidates(monkeypatch, address_selection_flow):
    module, advance, calls = address_selection_flow
    original_execute = module.sql_mapper_layer.execute

    def ring_candidates(query, **kwargs):
        result = original_execute(query, **kwargs)
        if query == "building_by_address" and kwargs.get("address") == "Ringvägen 10":
            result["data"] = list(result["data"]) + [
                _task2_address_row("01-80-TESTPOSTCORRECTION1-1", "Hornsgatan 106", "Stockholm", "11727", "2024"),
                _task2_address_row("19-84-TESTPOSTCORRECTION2-1", "Ringvägen 10", "Arboga", "73245", "2024"),
            ]
        return result

    monkeypatch.setattr(module.sql_mapper_layer, "execute", ring_candidates)
    original = "Which heating system is registered for Ringvägen 10?"
    initial = advance(original)
    assert initial["identity_gate_blocked"] is True
    assert len(initial["metadata"]["building_candidate_matches"]) == 3

    result = advance("The postcode is 11726, Stockholm.")

    assert calls[-1] == ("buildings_by_building_id", {"building_id": "01-80-TAPETEN2-1"})
    assert result.get("identity_gate_blocked") is not True
    assert result["metadata"]["address_location_hint"] == "11726 Stockholm"
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == "01-80-TAPETEN2-1"
    assert result["context"]["answer_focus"] == original


def test_summarizer_receives_selected_address_lookup_evidence_without_unrelated_aliases(monkeypatch):
    module = import_building_flow_graph_module()
    selected_id = "01-80-HAELSAN10-2"
    competing_id = "01-80-HAELSAN10-1"
    unrelated_id = "07-80-TESTUNRELATED1-1"
    latest_epc = _task2_address_row(selected_id, "Tulegatan 5A", "Stockholm", "11353", "2019")

    def execute(query, **kwargs):
        if query == "buildings_by_building_id":
            assert kwargs["building_id"] == selected_id
            # The selected ID lookup returns only its latest primary address.
            rows = [latest_epc]
        else:
            assert query == "building_by_address"
            if kwargs["address"] == "Tulegatan 5A":
                rows = [latest_epc]
            else:
                assert kwargs["address"] == "Rådmansgatan 31"
                rows = [
                    _task2_address_row(selected_id, "Rådmansgatan 31", "Stockholm", "11353", "2019"),
                    _task2_address_row(competing_id, "Döbelnsgatan 18", "Stockholm", "11353", "2019"),
                ]
        return {"ok": True, "data": copy.deepcopy(rows), "trace": {}}

    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    original = (
        "Tulegatan 5A och Rådmansgatan 31 i Stockholm verkar avse samma hus. "
        "Kan du kontrollera det och säga vilket värmesystem som är registrerat?"
    )
    initial = module.resolve_multi_address_identity_message(original, {"response_language": "sv"})
    candidates = initial["metadata"]["multi_address_resolution"]["candidates"]
    radmansgatan = next(candidate for candidate in candidates if candidate["address"] == "Rådmansgatan 31")
    assert radmansgatan["recorded_addresses_by_building_id"][selected_id] == ["Rådmansgatan 31"]
    assert radmansgatan["recorded_addresses_by_building_id"][competing_id] == ["Döbelnsgatan 18"]
    # A lookup that never returned the selected ID must not enter its evidence.
    candidates.append({
        "address": "Other lookup address 99", "building_ids": [unrelated_id],
        "location_hint": "Växjö", "ok": True,
        "recorded_addresses_by_building_id": {unrelated_id: ["Unrelatedsgatan 99"]},
    })
    metadata = copy.deepcopy(initial["metadata"])
    metadata["building_id_from_user"] = selected_id
    lookup = module.generic_sql_agent_node({"metadata": metadata, "parallel": {}})
    assert lookup.get("identity_gate_blocked") is not True
    assert lookup["agent_data_generic"][0]["epc_idadr"] == "Tulegatan 5A"
    captured = {}

    def generate(prompt, message_list=None, *, language=None):
        captured["prompt"] = prompt
        return "Den valda byggnaden har registrerad fjärrvärme. Adressökningen för Rådmansgatan är fortfarande flertydig."

    monkeypatch.setattr(module.llm_summarizer, "generate_response", generate)
    result = module.llm_summarizer_node({
        "last_message": selected_id,
        "messages": [{"role": "user", "content": original}, {"role": "user", "content": selected_id}],
        "context": {"answer_focus": original, "intent_list": ["SQL database", "vector database"]},
        "metadata": lookup["metadata"],
        "agent_data_generic": lookup["agent_data_generic"],
        "aggregated_data": {"generic_sql": lookup["agent_data_generic"]},
    })

    evidence = result["metadata"]["address_identity_evidence"]
    assert evidence["selected_building_id"] == selected_id
    lookups = {item["searched_address"]: item for item in evidence["address_lookups"]}
    assert set(lookups) == {"Tulegatan 5A", "Rådmansgatan 31"}
    assert lookups["Tulegatan 5A"]["unique_match"] is True
    assert lookups["Rådmansgatan 31"]["unique_match"] is False
    assert set(lookups["Rådmansgatan 31"]["returned_building_ids"]) == {selected_id, competing_id}
    assert lookups["Rådmansgatan 31"]["recorded_addresses_for_selected_id"] == ["Rådmansgatan 31"]
    prompt = captured["prompt"]
    for value in ("address_identity_evidence", "recorded_addresses_for_selected_id", "Tulegatan 5A", "Rådmansgatan 31", selected_id, competing_id):
        assert value in prompt
    assert '"unique_match": false' in prompt
    for value in ("Döbelnsgatan 18", "Other lookup address 99", "Unrelatedsgatan 99", unrelated_id):
        assert value not in prompt


def _remaining_tasks_state(question, *, language="en", parts=None):
    return {"last_message": question, "messages": [{"role": "user", "content": question}],
            "context": {"answer_focus": question, "request_parts": parts or [], "intent_list": ["SQL database", "vector database"]},
            "metadata": {"building_id_from_user": "01-80-TEST1-1", "response_language": language},
            "agent_data_generic": [{"byggnadsid": "01-80-TEST1-1", "epc_egienergiklass": "G",
                                    "epc_egienergiprestanda": 201, "epc_huvudsakliguppvarmning_calc": "ElDirekt",
                                    "epc_ventilation": "F", "epc_godkand": "2018-05-02"}]}


@pytest.mark.parametrize("question", [
    "Quote the paragraph in our energy declaration that proves our ventilation is FTX.",
    "Skriv det exakta citatet ur vår senaste OVK-rapport som visar att ventilationen är godkänd.",
])
def test_existing_document_quote_states_missing_original_text_without_generation(question):
    module = import_building_flow_graph_module()
    def unexpected(*args, **kwargs):
        pytest.fail("Original document text is absent; do not generate a purported quotation")
    module.llm_summarizer.generate_response = unexpected
    result = module.llm_summarizer_node(_remaining_tasks_state(question))
    answer = result["final_response"]
    assert "original text" in answer or "originaltexten" in answer
    assert "Paste the relevant passage" in answer or "Klistra in" in answer
    assert "ventilation type is" not in answer
    assert result["metadata"]["response_fallback"]["reason"] == "original_document_text_unavailable"
    assert result["metadata"]["retrieved_facts"]["building_id"] == "01-80-TEST1-1"


def test_missing_document_does_not_drop_general_next_steps_while_identity_missing():
    module = import_building_flow_graph_module()
    question = "Can you summarize the key points from our energy declaration and also explain what a BRF should do next?"
    result = module.request_address_node({"last_message": question, "context": {}, "metadata": {}, "messages": [{"role": "user", "content": question}]})
    assert "original text" in result["final_response"]
    assert "energy bills, operating schedules" in result["final_response"]
    assert "BRF board" in result["final_response"]
    assert result["metadata"]["pending_building_request"]["question"] == question


@pytest.mark.parametrize("language, expected", [("en", "direct electric heating"), ("sv", "direktverkande el")])
def test_known_heating_code_is_readable_without_changing_raw_evidence(language, expected):
    module = import_building_flow_graph_module()
    question = "Vilket uppvärmningssystem är registrerat?" if language == "sv" else "Which heating system is recorded?"
    state = _remaining_tasks_state(question, language=language)
    state["context"]["intent_list"] = ["SQL database"]
    module.llm_summarizer.generate_response = lambda *args, **kwargs: pytest.fail("Simple fact answers must not call the model")
    result = module.llm_summarizer_node(state)
    assert expected in result["final_response"]
    assert result["metadata"]["retrieved_facts"]["epc_huvudsakliguppvarmning_calc"] == "ElDirekt"
    assert result["final_response"].startswith("Byggnadsid" if language == "sv" else "Building ID")
    assert "2018-05-02" in result["final_response"]


@pytest.mark.parametrize("code, expected", [("F", "mekanisk frånluftsventilation"), ("FT", "utan värmeåtervinning"), ("FTX", "med värmeåtervinning"), ("FMED", "frånluftsventilation med värmeåtervinning")])
def test_swedish_ventilation_explanation_matches_actual_code(code, expected):
    module = import_building_flow_graph_module()
    result = module._targeted_building_fact_response(user_input="Vilken ventilation har vi?", current_address="", building_id="01-80-TEST1-1", facts={"ventilation_type": code}, language="sv")
    assert expected in result
    if code != "FTX":
        assert "FTX" not in result


@pytest.mark.parametrize("language", ["en", "sv"])
def test_declared_calculated_class_and_metrics_keep_distinct_bases(language):
    module = import_building_flow_graph_module()
    response = module._targeted_building_fact_response(
        user_input="energiklass, energiprestanda och primärenergital" if language == "sv" else "energy class, energy performance and primary energy number",
        current_address="", building_id="", language=language,
        facts={"energy_class": "G", "declared_energy_class": "G", "calculated_energy_class": "E",
               "epc_egienergiklass2020_calc": "E", "energy_performance": 201, "primary_energy_number": 132, "epc_godkand": "2018-01-01"})
    unit = "kWh/(m²·år)" if language == "sv" else "kWh/m2-year"
    assert f"201 {unit}" in response and f"132 {unit}" in response
    assert "2020" in response and "2018-01-01" in response
    assert ("declared energy class is G" in response and "Calculated energy class" in response) if language == "en" else ("deklarerade energiklass: G" in response and "Beräknad energiklass" in response)
    assert "Class C" in response or "Klass C" in response


def test_swedish_energy_performance_uses_correct_grammar_and_readable_units():
    module = import_building_flow_graph_module()
    response = module._targeted_building_fact_response(
        user_input="Vad är byggnadens energiprestanda?", current_address="Ringvägen 10",
        building_id="01-80-TAPETEN2-1", language="sv",
        facts={"energy_performance": 142, "epc_godkand": "2018-08-30"},
    )
    assert "Byggnadens deklarerade energiprestanda: 142 kWh/(m²·år)." in response
    assert "2018-08-30" in response
    assert "kWh/m2-year" not in response
    assert "Det är inte samma mått som primärenergitalet" not in response
    assert "Kontrollera deklarationens definition" in response
    assert module._format_fact_value_for_user("electricity_use", 12000, "kWh/year", "sv") == "12000 kWh/år"


def test_calculation_only_does_not_become_declared_class_or_missing_performance():
    module = import_building_flow_graph_module()
    response = module._targeted_building_fact_response(user_input="energy class and energy performance", current_address="", building_id="", facts={"energy_class": "E", "epc_egienergiklass2020_calc": "E", "specific_energy_use": 129, "primary_energy_number": 103})
    assert "Calculated energy class" in response
    assert "does not provide declared energy class" in response
    assert "does not provide declared energy performance" in response
    assert "declared energy class is E" not in response
    assert "129 kWh" not in response and "103 kWh" not in response


def test_original_question_restores_part_omitted_by_parser_after_id_reply():
    module = import_building_flow_graph_module()
    question = "Which heating system do we have, and what should we prioritize?"
    parts = [{"question": "Which heating system do we have?", "kind": "fact", "sources": ["SQL database"]}]
    result = module._request_parts_for_response(question, {"request_parts": parts}, {"heating_system": "district heating"})
    assert len(result) == 2
    assert "prioritize" in result[1]["question"] and result[1]["kind"] == "advice"
    assert parts == [{"question": "Which heating system do we have?", "kind": "fact", "sources": ["SQL database"]}]


@pytest.mark.parametrize("fenced", [False, True])
def test_successful_multipart_mapping_renders_every_answer(fenced):
    module = import_building_flow_graph_module()
    question = "Which heating system do we have, and what should we prioritize?"
    parts = [{"question": "Which heating system do we have?", "kind": "fact"}, {"question": "What should we prioritize?", "kind": "advice"}]
    payload = json.dumps({"parts": [{"part_index": 1, "answer": "The recorded heating system is direct electric heating."}, {"part_index": 2, "answer": "Check reported room temperatures and controls before ranking insulation or equipment investments."}]})
    captured = {}
    def generate(prompt, message_list=None, *, language=None):
        captured.update(prompt=prompt, language=language)
        return f"```json\n{payload}\n```" if fenced else payload
    module.llm_summarizer.generate_response = generate
    state = _remaining_tasks_state(question, parts=parts)
    state["last_message"] = "01-80-TEST1-1"
    result = module.llm_summarizer_node(state)
    assert "direct electric heating" in result["final_response"]
    assert "before ranking" in result["final_response"]
    assert '"part_index"' not in result["final_response"]
    assert result["metadata"]["request_part_coverage"]["status"] == "complete"
    assert captured["language"] == "en"
    assert '"multipart_response_format": true' in captured["prompt"]
    assert question in captured["prompt"]


@pytest.mark.parametrize("payload", [
    {"parts": [{"part_index": 1, "answer": "Known heating fact."}]},
    {"parts": [{"part_index": 1, "answer": "Known heating fact."}, {"part_index": 2, "answer": ""}]},
    {"parts": [{"part_index": 1, "answer": "Known heating fact."}, {"part_index": 1, "answer": "Duplicate."}]},
    {"parts": [{"part_index": True, "answer": "Boolean index."}]},
    {"parts": [{"part_index": 999, "answer": "Wrong index."}]},
])
def test_successful_generation_with_missing_part_is_explicitly_partial(payload):
    module = import_building_flow_graph_module()
    parts = [{"question": "Which heating system?", "kind": "fact"}, {"question": "What should we prioritize?", "kind": "advice"}]
    answer, missing, coverage = module._render_multipart_response(json.dumps(payload), parts, {"heating_system": "ElDirekt"}, "en")
    assert "What should we prioritize?" in answer and "remains unanswered" in answer
    assert missing == [{"question": "What should we prioritize?", "kind": "advice"}]
    assert coverage["status"] == "partial"


def test_multipart_cannot_invent_missing_document_quote_even_when_model_succeeds():
    module = import_building_flow_graph_module()
    parts = [{"question": "Quote our OVK report", "kind": "other", "document_text_status": "original_text_unavailable"}, {"question": "What should we do next?", "kind": "advice"}]
    answer, missing, coverage = module._render_multipart_response(json.dumps({"parts": [{"part_index": 1, "answer": "Invented quotation proving approval."}, {"part_index": 2, "answer": "Obtain the signed inspection protocol and ask the manager about any deficiencies."}]}), parts, {}, "en")
    assert "Invented quotation" not in answer
    assert "original text" in answer and "signed inspection protocol" in answer
    assert missing[0]["question"] == "Quote our OVK report"
    assert coverage["status"] == "partial"


def test_user_supplied_document_passage_allows_attributed_interpretation():
    module = import_building_flow_graph_module()
    passage = "The ventilation inspection noted blocked supply vents and required a follow-up inspection."
    messages = [{"role": "assistant", "content": f"OVK report:\n```text\n{passage}\n```"}, {"role": "user", "content": f"OVK-rapport:\n```text\n{passage}\n```"}]
    assert module._provided_document_text(messages) == [passage]
    state = _remaining_tasks_state("Summarize our OVK report")
    state["messages"] = [{"role": "assistant", "content": "Building ID: 01-80-TEST1-1"}] + messages + state["messages"]
    captured = []
    module.llm_summarizer.generate_response = lambda prompt, **kwargs: captured.append(prompt) or "According to the text you provided, follow up on the blocked vents."
    result = module.llm_summarizer_node(state)
    assert "text you provided" in result["final_response"]
    assert "user_supplied_unverified" in captured[0]


def test_verified_diagnostic_answer_resolves_identity_count_and_retains_facts():
    module = import_building_flow_graph_module()
    state = _remaining_tasks_state("What should we check before replacing our ventilation?")
    state["messages"] = [{"role": "user", "content": state["last_message"]}, {"role": "assistant", "content": "Which building?"}, {"role": "user", "content": "Stockholm"}, {"role": "assistant", "content": "Which ID?"}, {"role": "user", "content": "01-80-TEST1-1"}]
    state["metadata"].update(clarification={"needed": True, "reason": "ambiguous_address"}, clarification_started_user_turn=1)
    state["session_state"] = {"metadata": copy.deepcopy(state["metadata"])}
    module.llm_summarizer.generate_response = lambda *args, **kwargs: "Do residents report stale air, noise, or uneven airflow? Check measurements before ranking replacement options."
    result = module.llm_summarizer_node(state)
    assert result["metadata"]["clarification"]["needed"] is False
    assert result["metadata"]["clarification"]["resolved_after_turns"] == 2
    assert result["metadata"]["retrieved_facts"]["energy_class"] == "G"
    assert result["metadata"]["building_match"]["ambiguous"] is False
    assert "Do residents report" in result["final_response"]


def test_whole_question_parser_part_cannot_hide_document_and_advice_parts():
    module = import_building_flow_graph_module()
    question = "Quote our OVK report and also explain what we should do next?"
    parts = module._request_parts_for_response(question, {"request_parts": [{"question": question, "kind": "other"}]}, {})
    assert len(parts) == 2
    assert "OVK" in parts[0]["question"] and "do next" in parts[1]["question"]


def test_legacy_useful_multipart_prose_is_preserved_without_false_unanswered_claim():
    module = import_building_flow_graph_module()
    prose = "You have district heating. For priorities, check control settings and uneven temperatures before ranking investments."
    parts = [{"question": "Which heating system?", "kind": "fact"}, {"question": "What should we prioritize?", "kind": "advice"}]
    answer, missing, coverage = module._render_multipart_response(prose, parts, {"heating_system": "district heating"}, "en")
    assert prose in answer and "remains unanswered" not in answer
    assert "may be incomplete" in answer
    assert missing == [] and coverage["status"] == "unverified"


@pytest.mark.parametrize("other_question", ["Quote our latest OVK report", "Summarize our energy declaration"])
def test_old_document_passage_cannot_follow_a_building_switch(other_question):
    module = import_building_flow_graph_module()
    passage = "The approved energy declaration for the first building records electric heating and class G."
    messages = [{"role": "assistant", "content": "Building ID: 01-80-OLD1-1"},
                {"role": "user", "content": f"Energy declaration text:\n```text\n{passage}\n```"},
                {"role": "user", "content": "Now use 01-80-NEW1-1"},
                {"role": "user", "content": other_question}]
    assert module._provided_document_text(messages, other_question, "01-80-NEW1-1") == []


def test_document_type_must_match_even_with_same_selected_building():
    module = import_building_flow_graph_module()
    messages = [{"role": "assistant", "content": "Building ID: 01-80-TEST1-1"},
                {"role": "user", "content": "Energy declaration:\n```text\nThis declaration contains heating figures and an energy class but no ventilation approval.\n```"},
                {"role": "user", "content": "Quote our OVK report"}]
    assert module._provided_document_text(messages, "Quote our OVK report", "01-80-TEST1-1") == []


def test_new_identity_question_clears_stale_nonidentity_override():
    module = import_building_flow_graph_module()
    previous = {"clarification": {"needed": False, "identity_required": False, "resolved": True},
                "clarification_started_user_turn": 1, "clarification_resolved_user_turn": 2}
    state = {"last_message": "What is the energy class of the other building?", "metadata": copy.deepcopy(previous), "context": {},
             "messages": [{"role": "user", "content": "Old question"}, {"role": "user", "content": "Old selection"}, {"role": "user", "content": "New question"}]}
    requested = module.request_address_node(state)
    assert requested["metadata"]["clarification"]["identity_required"] is True
    counted = module.with_clarification_progress(requested["metadata"], state["messages"], previous)
    assert counted["clarification"]["needed"] is True
    assert counted["clarification_started_user_turn"] == 3


@pytest.mark.parametrize("answer", ['{"parts": [{"part_index": 1, "answer": "truncated', '```json\n{"parts": [', '[{"part_index": 1'])
def test_malformed_internal_json_never_reaches_user(answer):
    module = import_building_flow_graph_module()
    parts = [{"question": "Which heating system?", "kind": "fact"}, {"question": "What should we prioritize?", "kind": "advice"}]
    rendered, missing, coverage = module._render_multipart_response(answer, parts, {"heating_system": "ElDirekt"}, "en")
    assert '"part_index"' not in rendered and "truncated" not in rendered
    assert "direct electric heating" in rendered
    assert "What should we prioritize?" in rendered and "remains unanswered" in rendered
    assert missing == [{"question": "What should we prioritize?", "kind": "advice"}]
    assert coverage["status"] == "partial"


def test_repeated_same_id_does_not_discard_user_document_passage():
    module = import_building_flow_graph_module()
    passage = "The protocol lists blocked supply vents and requires a follow-up ventilation inspection."
    messages = [{"role": "user", "content": f"OVK-rapport för 01-80-TEST1-1:\n```text\n{passage}\n```"},
                {"role": "user", "content": "Quote our OVK report for 01-80-TEST1-1"}]
    assert module._provided_document_text(messages, messages[-1]["content"], "01-80-TEST1-1") == [passage]


def test_each_requested_document_uses_only_its_own_provided_passage():
    module = import_building_flow_graph_module()
    question = "Quote our OVK report and also summarize our energy declaration."
    parts = [{"question": "Quote our OVK report", "kind": "other"}, {"question": "Summarize our energy declaration", "kind": "other"}]
    state = _remaining_tasks_state(question, parts=parts)
    state["messages"] = [{"role": "assistant", "content": "Building ID: 01-80-TEST1-1"},
                         {"role": "user", "content": "OVK-rapport:\n```text\nThe inspection reports blocked supply vents and requires another inspection after remediation.\n```"}] + state["messages"]
    module.llm_summarizer.generate_response = lambda *args, **kwargs: json.dumps({"parts": [{"part_index": 1, "answer": "The user-provided text reports blocked supply vents."}, {"part_index": 2, "answer": "Invented EPC document findings."}]})
    result = module.llm_summarizer_node(state)
    statuses = [part["document_text_status"] for part in result["metadata"]["request_parts"]]
    assert statuses == ["user_supplied_unverified", "original_text_unavailable"]
    assert "blocked supply vents" in result["final_response"]
    assert "original text of your energy declaration" in result["final_response"]
    assert "Invented EPC" not in result["final_response"]


@pytest.mark.parametrize("question, expected, excluded", [
    ("What is our calculated energy class under the 2016 basis?", "2016 calculation basis): D", "2020 calculation basis): E"),
    ("What are our calculated energy classes under the 2016 and 2020 rules?", "2016 calculation basis): D", None),
])
def test_calculated_class_honors_requested_basis(question, expected, excluded):
    module = import_building_flow_graph_module()
    facts = {"energy_class": "G", "declared_energy_class": "G", "calculated_energy_class": "E", "epc_egienergiklass2020_calc": "E", "epc_egienergiklass2016_calc": "D"}
    response = module._targeted_building_fact_response(user_input=question, current_address="", building_id="", facts=facts)
    assert expected in response
    if excluded:
        assert excluded not in response
    else:
        assert "2020 calculation basis): E" in response


def test_multipart_failure_keeps_calculated_class_source_basis():
    module = import_building_flow_graph_module()
    question = "What is the energy class, and what should we prioritize?"
    answer, missing = module._partial_building_response(question=question, parts=[{"question": "What is the energy class?", "kind": "fact"}, {"question": "What should we prioritize?", "kind": "advice"}], building_id="", facts={"epc_egienergiklass2020_calc": "E", "epc_godkand": "2018-01-01"})
    assert "Calculated energy class (2020 calculation basis): E" in answer
    assert "does not provide declared energy class" in answer
    assert "2018-01-01" in answer
    assert missing[-1]["question"] == "What should we prioritize?"


def test_missing_requested_calculation_is_not_replaced_by_declared_class():
    module = import_building_flow_graph_module()
    response = module._targeted_building_fact_response(user_input="What is our calculated energy class under the 2016 basis?", current_address="", building_id="", facts={"energy_class": "G", "declared_energy_class": "G"})
    assert "does not provide calculated energy class (2016 calculation basis)" in response
    assert "declared energy class is G" in response
    assert "2016 calculation basis): G" not in response


def _verified_followup_state(question="Vilken byggnad har du valt och vilken energiklass gäller svaret?"):
    identifier = "01-80-KAAKENHUSEN40-1"
    facts = {
        "byggnadsid": identifier, "energy_class": "B", "declared_energy_class": "B",
        "epc_egienergiklass": "B", "epc_godkand": "2024-04-18",
        "epc_adress": "Brunnsgatan 9", "epc_postort": "Stockholm",
    }
    return {
        "last_message": question, "context": {"answer_focus": question},
        "metadata": {
            "byggnadsid": identifier, "selected_address_building_id": identifier,
            "address": "Kungsgatan 10", "requested_address": "Kungsgatan 10",
            "epc_record_address": "Brunnsgatan 9", "response_language": "sv",
            "retrieved_facts": facts,
            "building_identity_check": {"status": "passed", "matched_building_id": identifier},
            "evidence_scope": {"building_id": identifier},
            "clarification": {"needed": False},
        },
    }


@pytest.mark.parametrize("selected_key", ["selected_address_building_id", "building_id_from_user", "byggnadsid"])
def test_verified_fact_followup_reuses_same_building_without_oden(monkeypatch, selected_key):
    module = import_building_flow_graph_module()
    state = _verified_followup_state()
    identifier = state["metadata"]["byggnadsid"]
    state["metadata"].pop("selected_address_building_id")
    state["metadata"][selected_key] = identifier
    original = copy.deepcopy(state)
    monkeypatch.setattr(module.sql_mapper_layer, "execute", lambda *a, **k: pytest.fail("ODEN lookup on verified follow-up"))

    result = module.generic_sql_agent_node(state)

    assert result["done_generic_sql"] is True
    assert not result.get("identity_gate_blocked")
    assert result["agent_data_generic"][0]["energy_class"] == "B"
    assert result["metadata"]["query_traces"]["generic_sql"]["execution_status"] == "cached"
    assert result["metadata"]["building_identity_check"]["matched_building_id"] == identifier
    assert state == original


@pytest.mark.parametrize("question", [
    "What is the latest energy class?", "Refresh the energy class.",
    "Vilken är den senaste energiklassen?", "Uppdatera energiklassen.",
    "What is the energy class for 00-00-PILOTFINNSEJ999-0?",
    "What is the energy class at Nimrodsgatan 7?",
    "Which heating system do we have?",
    "Compare the energy class at Kungsgatan 10 and Nimrodsgatan 7.",
])
def test_cached_followup_cannot_answer_refresh_new_identity_or_missing_field(question):
    module = import_building_flow_graph_module()
    state = _verified_followup_state(question)
    assert module._verified_cached_building_result(state, state["metadata"]["byggnadsid"]) is None


@pytest.mark.parametrize("invalid", ["identity", "facts", "scope", "pending", "candidates"])
def test_cache_reuse_requires_matching_verified_identity_and_completed_selection(invalid):
    module = import_building_flow_graph_module()
    state = _verified_followup_state()
    metadata = state["metadata"]
    if invalid == "identity":
        metadata["building_identity_check"]["matched_building_id"] = "01-80-OTHER1-1"
    elif invalid == "facts":
        metadata["retrieved_facts"]["building_id"] = "01-80-OTHER1-1"
    elif invalid == "scope":
        metadata["evidence_scope"]["building_id"] = "01-80-OTHER1-1"
    elif invalid == "pending":
        metadata["clarification"]["needed"] = True
    else:
        metadata["building_candidate_matches"] = [{"byggnadsid": "01-80-OTHER1-1"}]
    assert module._verified_cached_building_result(state, metadata["byggnadsid"]) is None


def test_mixed_meter_cache_reuses_only_original_oden_projection_for_class():
    module = import_building_flow_graph_module()
    state = _verified_followup_state()
    identifier = state["metadata"]["byggnadsid"]
    oden = copy.deepcopy(state["metadata"]["retrieved_facts"])
    state["metadata"]["retrieved_facts"].update(electricity_use=123, period="2026-09", meter_id="meter-1")
    state["metadata"]["evidence_scope"]["sources"] = {"specialized_sql": {"accepted_records": 1}}
    state["session_state"] = {"agent_data_generic": [oden]}
    cached = module._verified_cached_building_result(state, identifier)
    assert cached["data"][0]["energy_class"] == "B"
    assert cached["data"][0]["epc_godkand"] == "2024-04-18"
    assert "electricity_use" not in cached["data"][0]
    assert "period" not in cached["data"][0] and "meter_id" not in cached["data"][0]


@pytest.mark.parametrize("question", ["What is the electricity use?", "What is our district heating use?"])
def test_cached_meter_consumption_requires_current_period_and_source(question):
    module = import_building_flow_graph_module()
    state = _verified_followup_state(question)
    identifier = state["metadata"]["byggnadsid"]
    oden = copy.deepcopy(state["metadata"]["retrieved_facts"])
    state["metadata"]["retrieved_facts"].update(electricity_use=123, district_heating_use=456, period="2026-09")
    state["metadata"]["evidence_scope"]["sources"] = {"specialized_sql": {"accepted_records": 1}}
    state["session_state"] = {"agent_data_generic": [oden]}
    assert module._verified_cached_building_result(state, identifier) is None


def test_merged_meter_cache_without_matching_primary_record_is_not_reused():
    module = import_building_flow_graph_module()
    state = _verified_followup_state()
    identifier = state["metadata"]["byggnadsid"]
    state["metadata"]["evidence_scope"]["sources"] = {"specialized_sql": {"accepted_records": 1}}
    state["session_state"] = {"agent_data_generic": [{"byggnadsid": "01-80-OTHER1-1", "energy_class": "B"}]}
    assert module._verified_cached_building_result(state, identifier) is None


@pytest.mark.parametrize("question", [
    "Vilka byggnadsid hittade du för respektive adress?",
    "Which building IDs did you find for each address?",
])
def test_candidate_information_followup_keeps_pending_request_without_lookup(multi_address_multipart_selection_flow, question):
    module, initial, advance, calls, original, parts = multi_address_multipart_selection_flow
    metadata = copy.deepcopy(initial["metadata"])
    metadata["response_language"] = "sv" if question.startswith("Vilka") else "en"
    before = copy.deepcopy(metadata)
    lookup_count = len(calls)

    result = module.resolve_multi_address_identity_message(question, metadata)

    assert result["cached_candidate_answer"] is True
    assert result["multi_address_resolution_complete"] is True
    assert "Kungsgatan 10:" in result["final_response"]
    assert "Nygatan 11:" in result["final_response"]
    assert "07-80-GUNNARGROEPE10-1" in result["final_response"]
    assert "07-80-GUNNARGROEPE9-1" in result["final_response"]
    assert result["metadata"]["pending_address_query"] == before["pending_address_query"]
    assert result["metadata"]["clarification"]["needed"] is True
    assert result["metadata"].get("address_location_hint") == before.get("address_location_hint")
    assert len(calls) == lookup_count
    assert metadata == before
    selected = advance("07-80-GUNNARGROEPE9-1")
    assert selected["context"]["answer_focus"] == original
    assert selected["context"]["request_parts"] == parts


def test_unique_first_address_does_not_replace_second_ambiguous_pending_address(monkeypatch):
    module = import_building_flow_graph_module()
    first, second = "01-80-HAELSAN10-2", "01-80-HAELSAN10-1"
    rows = [
        _task2_address_row(first, "Tulegatan 5A", "Stockholm", "11353", "2019"),
        _task2_address_row(second, "Rådmansgatan 31", "Stockholm", "11425", "2019"),
        _task2_address_row(first, "Rådmansgatan 31", "Stockholm", "11425", "2019"),
    ]
    calls = []
    def execute(query, **kwargs):
        calls.append((query, kwargs))
        found = rows[:1] if kwargs["address"] == "Tulegatan 5A" else rows[1:]
        return {"ok": True, "data": copy.deepcopy(found), "trace": {}}
    monkeypatch.setattr(module.sql_mapper_layer, "execute", execute)
    result = module.resolve_multi_address_identity_message(
        "Är Tulegatan 5A och Rådmansgatan 31 i Stockholm samma byggnad?", {"response_language": "sv"},
    )
    assert result["metadata"]["pending_ambiguous_address"] == "Rådmansgatan 31"
    assert f"Tulegatan 5A: byggnadsid {first}" in result["final_response"]
    assert f"Rådmansgatan 31: flera byggnadsid ({second}, {first})" in result["final_response"]
    assert result["metadata"]["clarification"]["needed"] is True
    assert len(calls) == 2


@pytest.mark.parametrize("question", [
    "Vilka byggnadsid hittade du för respektive adress?",
    "Which building IDs did you find?", "Show me the candidates.",
])
def test_question_about_candidates_is_not_a_location_hint(question):
    module = import_building_flow_graph_module()
    assert module._extract_location_only_hint_from_text(question) is None


@pytest.mark.parametrize("reply, expected", [
    ("Stockholm", "Stockholm"), ("Stockholm?", "Stockholm"),
    ("Jag menade Stockholm", "Stockholm"), ("Ja, Stockholm", "Stockholm"),
    ("117 26", "117 26"),
])
def test_location_replies_still_resolve_with_question_guard(reply, expected):
    module = import_building_flow_graph_module()
    assert module._extract_location_only_hint_from_text(reply) == expected


@pytest.mark.parametrize("address", ["professorsslingan 51", "Ekstigen 2", "Mosebacke Torg 1", "Slottsbacken 3"])
def test_additional_swedish_street_candidates_keep_user_spelling(address):
    module = import_building_flow_graph_module()
    # The lookup candidate supplies no confirmed ID and does not fix spelling.
    candidate = module._extract_address_candidate_from_text(f"also can you tell me about my building {address}")
    assert candidate and candidate.lower() == address.lower()
