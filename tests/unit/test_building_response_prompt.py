import json
from pathlib import Path
import pytest

from src.agents.building_response_prompt import (
    build_identity_evidence_context,
    build_building_response_prompt,
    build_history_excerpt,
    ensure_building_identifier_in_response,
    is_verified_address_explanation_followup,
    merge_identifier_metadata,
    select_preferred_identifier,
    verified_address_relationship,
)


def _ringvagen_alias_metadata():
    building_id = "01-80-TAPETEN2-1"
    return {
        "byggnadsid": building_id,
        "building_id": building_id,
        "address": "Ringvägen 10",
        "requested_address": "Ringvägen 10",
        "epc_record_address": "Hornsgatan 104",
        "same_building_multiple_addresses": True,
        "building_identity_check": {"status": "passed", "matched_building_id": building_id},
        "retrieved_facts": {"byggnadsid": building_id, "energy_performance": 142},
    }


def test_verified_alias_explanation_recognizes_the_exact_f08_followup():
    metadata = _ringvagen_alias_metadata()
    assert is_verified_address_explanation_followup(
        "Varför visas Hornsgatan 104 när jag frågade om Ringvägen 10?", metadata,
    )
    assert is_verified_address_explanation_followup(
        "Why is Hornsgatan 104 shown when I asked about Ringvägen 10?", metadata,
    )


@pytest.mark.parametrize("question", [
    "Byt till Hornsgatan 104 i stället för Ringvägen 10.",
    "Jag menade Hornsgatan 104, inte Ringvägen 10.",
    "Switch to Hornsgatan 104 instead of Ringvägen 10.",
    "Använd Hornsgatan 104, inte Ringvägen 10.",
    "Byggnadsid 01-80-SKYTTEN2-1: varför visas Hornsgatan 104 för Ringvägen 10?",
    "Varför visas Hornsgatan 104 när jag frågade om Ringvägen 100?",
    "Varför visas Hornsgatan 104?",
    "Vilken energiklass har Hornsgatan 104?",
])
def test_known_alias_guard_does_not_override_switches_or_other_address_requests(question):
    assert not is_verified_address_explanation_followup(question, _ringvagen_alias_metadata())


@pytest.mark.parametrize("patch", [
    {"same_building_multiple_addresses": False},
    {"building_identity_check": {"status": "missing", "matched_building_id": "01-80-TAPETEN2-1"}},
    {"building_identity_check": {"status": "passed", "matched_building_id": "OLD-ID"}},
    {"retrieved_facts": {"byggnadsid": "OLD-ID"}},
    {"address_identity_evidence": {"selected_building_id": "OLD-ID"}},
    {"epc_record_address": ""},
    {"building_id": "NEW-ID"},
])
def test_verified_alias_guard_requires_coherent_saved_evidence(patch):
    metadata = {**_ringvagen_alias_metadata(), **patch}
    assert not verified_address_relationship(metadata)
    assert not is_verified_address_explanation_followup(
        "Varför visas Hornsgatan 104 när jag frågade om Ringvägen 10?", metadata,
    )


def test_f08_prompt_preserves_verified_requested_to_declaration_address_relationship():
    metadata = _ringvagen_alias_metadata()
    prompt = build_building_response_prompt(
        user_input="Varför visas Hornsgatan 104 när jag frågade om Ringvägen 10?",
        current_address="Ringvägen 10", history=[], action_description="Explain saved address relationship",
        results={"energy_performance": 142}, metadata=metadata, building_id="01-80-TAPETEN2-1",
    )
    context, _ = json.JSONDecoder().raw_decode(prompt.split("- Selected context, scoped facts and uncertainty: ", 1)[1])
    relationship = context["verified_address_relationship"]
    assert relationship["selected_building_id"] == "01-80-TAPETEN2-1"
    assert relationship["requested_address"] == "Ringvägen 10"
    assert relationship["energy_declaration_record_address"] == "Hornsgatan 104"
    assert "does not prove either street address is unique" in relationship["scope"]
    assert "Do not withdraw the verified relationship" in prompt
    assert "invent an" in prompt


def test_selected_building_prompt_keeps_distinct_brf_lookup_evidence():
    building_id = '01-80-SKYTTEN2-1'
    metadata = {
        'byggnadsid': building_id, 'brf_name': 'Friskytten',
        'selected_brf_building_id': building_id,
        'brf_resolution': {'status': 'resolved_by_user_selection', 'selected_building_id': building_id},
        'brf_candidate_buildings': [
            {'byggnadsid': building_id, 'addresses': ['Artemisgatan 11'], 'private_marker': 'EXCLUDED_RAW_CACHE'},
            {'byggnadsid': '01-80-SKYTTEN2-2', 'addresses': ['Nimrodsgatan 7'], 'energy_class': 'G'},
        ],
    }
    prompt = build_building_response_prompt(user_input='Jag väljer Artemisgatan 11', current_address='Artemisgatan 11',
        history=[], action_description='SQL database', results=[{'byggnadsid': building_id}], metadata=metadata, building_id=building_id)
    context, _ = json.JSONDecoder().raw_decode(prompt.split('- Selected context, scoped facts and uncertainty: ', 1)[1])
    lookup = context['brf_lookup_evidence']
    assert lookup['selected_building_id'] == building_id
    assert len(lookup['returned_buildings']) == 2
    assert 'EXCLUDED_RAW_CACHE' not in prompt
    assert 'energy_class' not in json.dumps(lookup)


def test_stale_association_and_address_evidence_cannot_follow_building_switch():
    context = build_identity_evidence_context({
        'brf_resolution': {'status': 'resolved_by_user_selection', 'selected_building_id': 'OLD'},
        'brf_candidate_buildings': [{'byggnadsid': 'OLD'}],
        'address_identity_evidence': {'selected_building_id': 'OLD'},
    }, 'NEW')
    assert context == {}


def test_select_preferred_identifier_prefers_byggnadsid():
    identifier = select_preferred_identifier(
        {"building_id": "uuid-1"},
        [{"byggnadsid": "01-80-FILOSOFEN2-3", "50a_uuid": "uuid-1"}],
    )

    assert identifier == "01-80-FILOSOFEN2-3"


def test_merge_identifier_metadata_adds_ids_from_results():
    merged = merge_identifier_metadata(
        {"address": "Main Street 1"},
        [{"byggnadsid": "01-80-FILOSOFEN2-3", "50a_uuid": "uuid-1"}],
    )

    assert merged["address"] == "Main Street 1"
    assert merged["byggnadsid"] == "01-80-FILOSOFEN2-3"
    assert merged["50a_uuid"] == "uuid-1"


def test_build_building_response_prompt_includes_building_id():
    prompt = build_building_response_prompt(
        user_input="How efficient is this building?",
        current_address="Main Street 1",
        history=[{"role": "user", "content": "How efficient is this building?"}],
        action_description="SQL database",
        results={"energy_class": "B"},
        metadata={"byggnadsid": "01-80-FILOSOFEN2-3"},
        building_id="01-80-FILOSOFEN2-3",
    )

    assert "Current building ID: 01-80-FILOSOFEN2-3" in prompt
    assert '"byggnadsid": "01-80-FILOSOFEN2-3"' in prompt


def test_building_response_prompt_keeps_scoped_address_identity_evidence_and_excludes_lookup_cache():
    building_id = "01-80-HAELSAN10-2"
    identity_evidence = {
        "source": "ODEN address endpoint",
        "selected_building_id": building_id,
        "address_lookups": [
            {
                "requested_address": "Tulegatan 5A",
                "returned_building_ids": [building_id],
                "selected_building_id": building_id,
                "recorded_addresses_for_selected_id": ["Tulegatan 5A", "Rådmansgatan 31"],
                "unique_match": True,
            },
            {
                "requested_address": "Rådmansgatan 31",
                "returned_building_ids": [building_id],
                "selected_building_id": building_id,
                "recorded_addresses_for_selected_id": ["Tulegatan 5A", "Rådmansgatan 31"],
                "unique_match": True,
            },
        ],
        "interpretation": "Both address lookups uniquely returned the selected ID.",
    }
    scoped_metadata = {
        "byggnadsid": building_id,
        "address_identity_evidence": identity_evidence,
        "building_identity_check": {"status": "passed", "matched_building_id": building_id},
        "same_building_multiple_addresses": True,
    }
    raw_cache = {
        "status": "same_building",
        "candidates": [{"raw_records": [{"private_marker": "RAW_ODEN_RECORDS_MUST_NOT_REACH_MODEL"}]}],
    }
    prompt = build_building_response_prompt(
        user_input="Do these two addresses refer to the same building?",
        current_address="Tulegatan 5A",
        history=[],
        action_description="SQL database",
        results={"heating_system": "district heating"},
        metadata={
            **scoped_metadata,
            "multi_address_resolution": raw_cache,
            "query_traces": [{"private_marker": "RAW_QUERY_TRACE_MUST_NOT_REACH_MODEL"}],
        },
        building_id=building_id,
    )

    context_text = prompt.split("- Selected context, scoped facts and uncertainty: ", 1)[1]
    included_metadata, _ = json.JSONDecoder().raw_decode(context_text)
    assert included_metadata == scoped_metadata
    assert included_metadata["address_identity_evidence"] == identity_evidence
    assert "multi_address_resolution" not in prompt
    assert "RAW_ODEN_RECORDS_MUST_NOT_REACH_MODEL" not in prompt
    assert "RAW_QUERY_TRACE_MUST_NOT_REACH_MODEL" not in prompt


def test_building_response_prompt_requires_ecm_style_for_heating_cost_advice():
    prompt = build_building_response_prompt(
        user_input="How can we reduce our heating costs?",
        current_address="Main Street 1",
        history=[],
        action_description="SQL database ; vector database",
        results={"energy_class": "F", "heating_system": "district heating"},
        metadata={"byggnadsid": "01-80-FILOSOFEN2-3"},
        building_id="01-80-FILOSOFEN2-3",
    )

    assert "Energy Conservation Measures (ECMs)" in prompt
    assert "reduce heating costs" in prompt
    assert "energy pyramid hierarchy" in prompt
    assert "energy conservation / reduce demand and waste" in prompt
    assert "energy efficiency / improve systems and controls" in prompt
    assert "then renewable energy" in prompt
    assert "Do not require four sections" in prompt
    assert "one or two useful follow-up questions" in prompt
    assert "never as proven higher savings or a diagnosis" in prompt


def test_building_response_prompt_keeps_simple_fact_answers_focused_and_explains_terms():
    prompt = build_building_response_prompt(
        user_input="What is total electricity consumption of my building?",
        current_address="Main Street 1",
        history=[],
        action_description="SQL database",
        results={"electricity_use": 121667, "ventilation_type": "FTX"},
        metadata={"byggnadsid": "01-80-FILOSOFEN2-3"},
        building_id="01-80-FILOSOFEN2-3",
    )

    assert "Answer the user's actual question first" in prompt
    assert "Assume the user has zero prior knowledge" in prompt
    assert "value -> what it means -> why it matters or what to check next" in prompt
    assert "Do not turn a simple fact question into a full building profile" in prompt
    assert "Do not repeat that full context" in prompt
    assert "raw ODEN/EPC fields" in prompt
    assert "epc_venttypftx" in prompt
    assert "epc_huvudsakliguppvarmning_calc" in prompt
    assert "FTX ventilation means mechanical supply and exhaust ventilation with heat recovery" in prompt


@pytest.mark.parametrize("route", ["generic", "building"])
@pytest.mark.parametrize("language,question", [
    ("sv", "Vad säger OVK-protokollet? Kan jag ladda upp det här?"),
    ("en", "What does our OVK protocol say? Can I upload it here?"),
])
def test_active_advice_prompts_offer_pasted_excerpts_instead_of_a_nonexistent_upload(route, language, question):
    if route == "generic":
        prompt = (Path(__file__).resolve().parents[2] / "src/prompts/generic_prompt.txt").read_text(encoding="utf-8")
    else:
        prompt = build_building_response_prompt(
            user_input=question,
            current_address="Nimrodsgatan 7",
            history=[],
            action_description="SQL database",
            results={},
            metadata={"byggnadsid": "01-80-SKYTTEN2-2", "response_language": language},
            building_id="01-80-SKYTTEN2-2",
        )
        assert question in prompt

    assert "SPARA has no document or image upload control" in prompt
    assert "Never ask the user to upload\n  or attach a file" in prompt
    assert "For an OVK protocol or other document, ask for relevant excerpts\n  pasted into the chat" in prompt
    assert "relevant dates, findings and building IDs" in prompt
    assert "text as user-supplied information, not an authenticated complete document" in prompt


def test_ensure_building_identifier_in_response_prefixes_missing_id():
    text = ensure_building_identifier_in_response(
        "The building has energy class B.",
        "01-80-FILOSOFEN2-3",
    )

    assert text.startswith("Building ID: 01-80-FILOSOFEN2-3")


def test_history_preserves_early_objective_and_later_correction():
    history = [{"role": "user", "content": "Our aim is comfort; major works must wait."}]
    history += [{"role": "assistant", "content": f"Discussion turn {i}"} for i in range(9)]
    history += [{"role": "user", "content": "Correction: the windows were repaired last year."}]

    excerpt = json.loads(build_history_excerpt(history))

    assert len(excerpt) == len(history)
    assert excerpt[0]["content"] == history[0]["content"]
    assert excerpt[-1]["content"] == history[-1]["content"]
    assert len(json.loads(build_history_excerpt(history, keep_last=2))) == 2
    assert json.loads(build_history_excerpt(history, keep_last=0)) == []


def test_shared_system_prompt_uses_construction_year_mapping():
    prompt_path = Path(__file__).resolve().parents[2] / "src/prompts/openai_response_prompt.txt"
    prompt = prompt_path.read_text(encoding="utf-8")
    field_descriptions = json.loads(prompt[prompt.index("{"):])

    assert field_descriptions["epc_egennybyggar"]["name"] == "EPC: construction year"
    assert "year built" in field_descriptions["epc_egennybyggar"]["description"]


@pytest.mark.parametrize("language,name", [("en", "English"), ("sv", "Swedish")])
def test_building_generation_prompt_carries_trusted_selected_language(language, name):
    metadata = {"response_language": language, "byggnadsid": "01-80-FILOSOFEN2-3"}
    prompt = build_building_response_prompt(
        user_input="Which heating system is recorded?", current_address="Tulegatan 5A",
        history=[{"role": "assistant", "content": "An older reply in a different language."}],
        action_description="SQL database", results={"heating_system": "ElDirekt"},
        metadata=metadata, building_id="01-80-FILOSOFEN2-3",
    )
    assert prompt.startswith(f"Required response language: {name} ({language})")
    assert f"Write every heading, explanation, question and missing-information statement in {name}." in prompt
    assert "ElDirekt" in prompt
    assert '"response_language": "' + language + '"' in prompt
    assert metadata == {"response_language": language, "byggnadsid": "01-80-FILOSOFEN2-3"}


def test_building_prompt_uses_actual_user_language_when_selected_language_is_absent():
    prompt = build_building_response_prompt(
        user_input="Vilket värmesystem har vi?", current_address="Ringvägen 10",
        history=[], action_description="SQL database", results={}, metadata={}, building_id="01-80-FILOSOFEN2-3",
    )
    assert prompt.startswith("Required response language: Swedish (sv)")


def test_building_identifier_heading_can_follow_swedish_language():
    answer = ensure_building_identifier_in_response("Byggnaden har fjärrvärme.", "01-80-FILOSOFEN2-3", language="sv")
    assert answer.startswith("Byggnadsid: 01-80-FILOSOFEN2-3")
