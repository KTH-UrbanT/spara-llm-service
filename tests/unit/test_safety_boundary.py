import json
import pytest

from src.pipeline.evaluation_metadata import build_message_evidence, build_message_metadata
from src.pipeline.safety_analysis import (
    response_language, apply_response_safety_notes, compute_data_freshness,
    assess_boundary_safety,
    assess_building_identity,
    build_out_of_scope_response,
    extract_retrieved_facts,
    response_language_instruction,
    response_language_mismatch,
    language_correction_messages,
    language_correction_preserves_evidence,
    language_failure_response,
)


def test_language_survives_address_and_consent_replies():
    history = [{"role": "user", "content": "What is our energy performance?"}, {"role": "user", "content": "Ringvägen 10"}, {"role": "user", "content": "Ja, skicka"}]
    assert response_language(history[:2]) == "en"
    assert response_language([{ "role": "user", "content": "Vilken energiklass har vi?"}], "117 26") == "sv"
    assert response_language([{ "role": "user", "content": "Please reply in Swedish"}], "What is the energy class?") == "sv"
    assert response_language([{ "role": "user", "content": "What is the building address?"}], "Energiklassen?", {"response_language": "en"}) == "sv"


@pytest.mark.parametrize("reply", [
    "Stockholm", "Det ligger i Stockholm.", "Den ligger i Stockholm.",
    "Det är i Stockholm.", "Kommunen är Stockholm.", "Staden är Stockholm.",
    "Det ligger i Upplands Väsby.", "Det ligger i Stockholm, 111 38.",
    "It is in Stockholm.", "i live in Stockholm", "I live in Älmhult", "111 38", "Brunnsgatan 9",
    "Building ID: 01-80-KAAKENHUSEN39-1", "Byggnadsid: 01-80-KAAKENHUSEN40-1",
    "01-80-KAAKENHUSEN39-1", "i Stockholm",
    "Building ID: 12-85-SIBBARP34:12-",
])
@pytest.mark.parametrize("language,question", [
    ("sv", "Vilken energiklass har byggnaden på Kungsgatan 10?"),
    ("en", "Which energy class does the building at Kungsgatan 10 have?"),
])
def test_selection_replies_preserve_the_question_language(reply, language, question):
    assert response_language([{"role": "user", "content": question}], reply) == language


def test_neutral_selection_without_history_preserves_saved_language():
    assert response_language([], "Det ligger i Stockholm.", {"response_language": "en"}) == "en"
    assert response_language([], "Building ID: 01-80-KAAKENHUSEN39-1", {"response_language": "sv"}) == "sv"
    assert response_language([], "i live in Huddinge") == "en"


def test_substantive_questions_and_explicit_preferences_can_change_language():
    swedish = [{"role": "user", "content": "Vilken energiklass har byggnaden?"}]
    assert response_language(swedish, "I want information about heating.") == "en"
    assert response_language(swedish, "Please reply in English. Det ligger i Stockholm.") == "en"
    assert response_language(swedish, "Det ligger i Stockholm. What is its energy class?") == "sv"


@pytest.mark.parametrize("language,name", [("en", "English"), ("sv", "Swedish")])
def test_trusted_language_instruction_covers_prose_and_preserves_evidence(language, name):
    instruction = response_language_instruction(language)
    assert f"Required response language: {name} ({language})" in instruction
    assert "heading, explanation, question" in instruction
    assert "source titles, quotations, citations, URLs, building identifiers" in instruction


@pytest.mark.parametrize("language,text,mismatch", [
    ("en", "The building has district heating. What should we check next?", False),
    ("sv", "Byggnaden har fjärrvärme. Vad bör vi kontrollera först?", False),
    ("en", "Byggnaden har fjärrvärme. Vad bör vi kontrollera först?", True),
    ("sv", "The building has district heating. What should we check next?", True),
    ("en", "The next step is a meter review. [Vi bör kontrollera vår värme och våra kostnader](https://example.test/guide)", False),
    ("sv", "Nästa steg är att kontrollera värmen. [The energy performance of our building](https://example.test/guide)", False),
    ("en", 'The next step is a meter review. “Vi bör kontrollera vår värme och våra kostnader.”', False),
    ("en", "The next step is a meter review. 'Vi bör kontrollera vår värme och våra kostnader.'", False),
    ("sv", "Nästa steg är att kontrollera värmen.\n> The building has district heating and we should check it.", False),
    ("en", "The next step is a meter review.\n```\nVi bör kontrollera vår värme och våra kostnader.\n```", False),
    ("sv", "Nästa steg är att kontrollera värmen. `The building has energy class C`", False),
    ("en", "The next step is a meter review.\n## Källor\nVi bör kontrollera vår värme och våra kostnader.pdf", False),
    ("sv", "Nästa steg är att kontrollera värmen.\nSources:\nThe building has district heating.pdf", False),
    ("en", "The next step is a meter review.\nSource: Vi bör kontrollera vår värme.pdf", False),
    ("sv", "Nästa steg är att kontrollera värmen.\nBuilding ID: 01-80-FILOSOFEN2-3", False),
    ("en", "01-80-FILOSOFEN2-3; 29dfc91a-e546-4df6-a8fa-11b62f93c4cf; FTX; 137.1 kWh/m²", False),
    ("sv", "01-80-FILOSOFEN2-3; 29dfc91a-e546-4df6-a8fa-11b62f93c4cf; FTX; 137.1 kWh/m²", False),
    ("sv", "Byggnaden har fjärrvärme och vi bör kontrollera värmen först.\nNext steps\nPlease check the controls.", True),
    ("en", "The building has district heating and we should inspect the controls.\nNästa steg\nKontrollera värmen och våra kostnader.", True),
])
def test_actual_response_language_ignores_marked_sources_quotes_ids_and_code(language, text, mismatch):
    assert response_language_mismatch(text, language) is mismatch


@pytest.mark.parametrize("draft,corrected,preserved", [
    ("Energiprestandan är 137,1 kWh/m² per år, enligt 2020. [guide.pdf]", "Energy performance is 137.1 kWh/m² per year, recorded in 2020. [guide.pdf]", True),
    ("Vi använde 1 234,5 kWh.", "We used 1,234.5 kWh.", True),
    ("Vi använde 1 234 kWh.", "We used 1,234 kWh.", True),
    ("Vi använde 1.234,5 kWh.", "We used 1,234.5 kWh.", True),
    ("Vi har 20 m² och kostnaden är 200 kr.", "We have 20 m2 and the cost is 200 SEK.", True),
    ("Systemet är `ElDirekt`, ID 01-80-FILOSOFEN2-3.", "The system is `ElDirekt`, ID 01-80-FILOSOFEN2-3.", True),
    ("Detta är 20 kWh enligt [guide.pdf].", "This is 30 kWh according to [guide.pdf].", False),
    ("Detta är 20 kWh enligt [guide.pdf].", "This is 20 MWh according to [guide.pdf].", False),
    ("Detta är 20 kWh enligt [guide.pdf].", "This is 20 kWh.", False),
    ("Byggnadsid: 01-80-FILOSOFEN2-3", "Building ID: 01-80-FILOSOFEN2-4", False),
    ("Systemet är `ElDirekt`.", "The system is `Fjarrvarme`.", False),
    ('Källan säger “Vi har fjärrvärme”.', 'The source says “We have district heating”.', False),
    ("Se https://example.test/guide", "See https://example.test/other", False),
    ("Se https://example.test/guide", "See https://example.test/guide and https://example.test/new", False),
    ("Se [guide.pdf](https://example.test/guide)", "See [guide](https://example.test/guide)", False),
    ("Byggnaden har fjärrvärme.", "The building has district heating.", True),
    ("Byggnaden har fjärrvärme.", "The building has district heating at 20 kWh.", False),
])
def test_language_correction_preserves_facts_units_citations_and_localized_numbers(draft, corrected, preserved):
    assert language_correction_preserves_evidence(draft, corrected) is preserved


def test_language_correction_keeps_original_context_without_mutating_messages():
    messages = [
        {"role": "system", "content": "Original policy."},
        {"role": "user", "content": "My budget is 20,000 SEK; no loans."},
        {"role": "assistant", "content": "Earlier advice."},
        {"role": "user", "content": "What should we prioritize?"},
    ]
    draft = "Vi bör kontrollera värmen och kostnaderna först."
    corrected = language_correction_messages(messages, draft, "en")
    assert messages[0]["content"] == "Original policy."
    assert len(messages) == 4
    assert corrected[1:4] == messages[1:]
    assert corrected.count(messages[-1]) == 1
    assert corrected[-2] == {"role": "assistant", "content": draft}
    assert "Translate only" in corrected[0]["content"]
    assert "every factual claim, limitation, condition" in corrected[0]["content"]


@pytest.mark.parametrize("language,word", [("en", "English"), ("sv", "svenska")])
def test_language_failure_is_explicit_and_localized(language, word):
    answer = language_failure_response(language)
    assert word in answer
    assert not response_language_mismatch(answer, language)


@pytest.mark.parametrize("fenced", [False, True])
def test_multipart_json_language_check_assesses_each_answer_prose(fenced):
    payload = json.dumps({"parts": [
        {"part_index": 1, "answer": "The building has district heating. [Vi bör kontrollera vår värme.pdf]"},
        {"part_index": 2, "answer": "Vi bör kontrollera värmen och våra kostnader först."},
    ]}, ensure_ascii=False)
    if fenced:
        payload = "```json\n" + payload + "\n```"
    assert response_language_mismatch(payload, "en") is True


@pytest.mark.parametrize("change,preserved", [
    ("translation", True), ("reorder", True), ("different_value", False),
    ("move_value_to_other_part", False), ("missing_part", False), ("duplicate_index", False),
    ("changed_index", False), ("list_instead_of_wrapper", False),
])
def test_multipart_language_correction_preserves_evidence_under_same_part_index(change, preserved):
    draft = {"parts": [
        {"part_index": 1, "answer": "Byggnaden har energiprestanda 137,1 kWh/m². [guide.pdf]"},
        {"part_index": 2, "answer": "Vi bör kontrollera 20 termostater först."},
    ]}
    corrected = {"parts": [
        {"part_index": 1, "answer": "The building has energy performance of 137.1 kWh/m². [guide.pdf]"},
        {"part_index": 2, "answer": "We should first check 20 thermostats."},
    ]}
    if change == "reorder":
        corrected["parts"].reverse()
    elif change == "different_value":
        corrected["parts"][0]["answer"] = corrected["parts"][0]["answer"].replace("137.1", "138.1")
    elif change == "move_value_to_other_part":
        corrected["parts"][0]["answer"], corrected["parts"][1]["answer"] = corrected["parts"][1]["answer"], corrected["parts"][0]["answer"]
    elif change == "missing_part":
        corrected["parts"].pop()
    elif change == "duplicate_index":
        corrected["parts"][1]["part_index"] = 1
    elif change == "changed_index":
        corrected["parts"][1]["part_index"] = 3
    elif change == "list_instead_of_wrapper":
        corrected = corrected["parts"]
    assert language_correction_preserves_evidence(json.dumps(draft, ensure_ascii=False), json.dumps(corrected)) is preserved


def test_multipart_language_correction_instruction_preserves_wrapper_and_indices():
    draft = json.dumps({"parts": [{"part_index": 1, "answer": "Vi bör kontrollera värmen först."}]})
    messages = language_correction_messages([{"role": "system", "content": "Original policy."}], draft, "en")
    assert "parts wrapper when present" in messages[0]["content"]
    assert "every part_index exactly" in messages[0]["content"]


def test_missing_fact_notes_use_relevant_readable_labels():
    metadata = {"response_language": "sv", "uncertainty": {"missing_facts": ["energy_class", "energy_performance", "heating_system", "renovation_information"]}}
    answer = apply_response_safety_notes("Underlaget saknar den uppgiften.", metadata, "Vilken energiklass har vi?")
    assert "energiklass" in answer
    assert "energy_class" not in answer
    assert "renoveringshistorik" not in answer
    assert "uppvärmningssystem" not in answer


def test_general_explanation_does_not_get_unrelated_missing_fact_notes():
    metadata = {"uncertainty": {"missing_facts": ["energy_class", "energy_performance", "energy_declaration_year"]}}
    assert apply_response_safety_notes("FTX recovers heat.", metadata, "How does FTX work?") == "FTX recovers heat."


def test_boundary_safety_passes_when_out_of_scope_response_redirects():
    response_text = build_out_of_scope_response(
        "legal_advice",
        "relevant_authority_or_legal_expert",
    )

    safety = assess_boundary_safety(
        user_message="Can our BRF legally force residents to pay?",
        response_text=response_text,
        route="out_of_scope",
        metadata={},
    )

    assert safety["status"] == "passed"
    assert safety["risk_category"] == "legal_advice"
    assert safety["action"] == "redirected"
    assert safety["handled_safely"] is True
    assert safety["requires_review"] is False
    assert safety["redirect_to"] == "relevant_authority_or_legal_expert"


def test_contact_details_update_is_boundary_not_record_update():
    response_text = build_out_of_scope_response(
        "external_contact_or_register_update",
        "official_register_or_admin_system",
    )

    safety = assess_boundary_safety(
        user_message="We changed board members--can you update our contact details?",
        response_text=response_text,
        route="out_of_scope",
        metadata={},
    )

    assert "cannot update external registers" in response_text
    assert "cannot perform the update" in response_text
    assert "Once you send these details" not in response_text
    assert safety["status"] == "passed"
    assert safety["risk_category"] == "external_contact_or_register_update"
    assert safety["redirect_to"] == "official_register_or_admin_system"


def test_boundary_safety_flags_direct_answer_to_risky_question():
    safety = assess_boundary_safety(
        user_message="Which loan should our BRF take for renovation?",
        response_text="You should choose the cheapest loan with the shortest payback.",
        route="generic",
        metadata={},
    )

    assert safety["status"] == "needs_review"
    assert safety["risk_category"] == "financial_advice"
    assert safety["action"] == "answered_directly"
    assert safety["handled_safely"] is False
    assert safety["reason_codes"] == [
        "route_not_out_of_scope",
        "missing_refusal_or_scope_limit",
        "missing_redirect_target",
    ]


def test_message_metadata_includes_safety_boundary_evidence():
    metadata = build_message_metadata(
        {
            "content": build_out_of_scope_response(
                "detailed_engineering_calculation",
                "qualified_engineer_or_installer",
            ),
            "classification": "out_of_scope",
            "route": "out_of_scope",
        },
        {
            "last_user_message": "Can you dimension the system exactly?",
            "out_of_scope": True,
            "out_of_scope_type": "detailed_engineering_calculation",
            "redirect_to": "qualified_engineer_or_installer",
        },
    )
    evidence = build_message_evidence(metadata)

    assert metadata["safety_boundary"]["status"] == "passed"
    assert metadata["safety_boundary"]["risk_category"] == "detailed_engineering_calculation"
    assert any(entry["evidence_type"] == "safety_boundary" for entry in evidence)


def test_message_metadata_clears_stale_clarification_for_successful_route():
    metadata = build_message_metadata(
        {
            "content": "Building ID: 01-80-FYSIKERN1-1\nThe building uses district heating.",
            "classification": "building_specific",
            "route": "building_specific",
        },
        {
            "last_user_message": "Do we have district heating?",
            "building_id": "01-80-FYSIKERN1-1",
            "clarification": {
                "needed": True,
                "reason": "ambiguous_brf",
                "question_asked": "Could you clarify your request?",
                "resolved": False,
            },
        },
    )

    assert metadata["needs_clarification"] is False
    assert metadata["clarification"]["needed"] is False
    assert metadata["clarification"]["resolved"] is True


def test_message_metadata_does_not_expose_candidate_id_on_clarification():
    metadata = build_message_metadata(
        {
            "content": "Please provide the city, postcode, municipality, BRF name, or exact building ID.",
            "classification": "building_specific",
            "route": "clarification",
        },
        {
            "last_user_message": "i live in Örebro",
            "address": "Examplegatan 10",
            "byggnadsid": "01-60-CANDIDATE-1",
            "building_id": "01-60-CANDIDATE-1",
            "retrieved_facts": {
                "byggnadsid": "01-60-CANDIDATE-1",
                "energy_class": "F",
            },
            "clarification": {
                "needed": True,
                "reason": "ambiguous_address",
                "question_asked": "Please provide the city, postcode, municipality, BRF name, or exact building ID.",
                "resolved": False,
            },
            "building_identity_check": {
                "status": "ambiguous",
                "candidate_building_ids": ["01-60-CANDIDATE-1", "18-80-CANDIDATE-2"],
            },
        },
    )

    assert metadata["needs_clarification"] is True
    assert "building_id" not in metadata
    assert "byggnadsid" not in metadata
    assert "retrieved_facts" not in metadata
    assert metadata["building_identity_check"]["status"] == "ambiguous"


def test_message_metadata_preserves_session_memory_fields():
    metadata = build_message_metadata(
        {
            "content": "Building ID: 01-80-SKYTTEN2-2\nThe EPC row uses another address for the same building.",
            "classification": "building_specific",
            "route": "building_specific",
        },
        {
            "last_user_message": "Show me the data",
            "building_id": "01-80-SKYTTEN2-2",
            "address": "Artemisgatan 17",
            "address_from_user": "Artemisgatan 17",
            "requested_address": "Artemisgatan 17",
            "epc_record_address": "Artemisgatan 13",
            "same_building_multiple_addresses": True,
            "address_context_note": "Same building ID with multiple registered addresses.",
        },
    )

    assert metadata["address"] == "Artemisgatan 17"
    assert metadata["address_from_user"] == "Artemisgatan 17"
    assert metadata["requested_address"] == "Artemisgatan 17"
    assert metadata["epc_record_address"] == "Artemisgatan 13"
    assert metadata["same_building_multiple_addresses"] is True
    assert "multiple registered addresses" in metadata["address_context_note"]


def test_building_identity_passes_when_many_addresses_share_same_building_id():
    identity = assess_building_identity(
        {"context": {"ambiguous": True}},
        [
            {"byggnadsid": "01-80-SKYTTEN2-2", "address": "Artemisgatan 13"},
            {"byggnadsid": "01-80-SKYTTEN2-2", "address": "Artemisgatan 15"},
            {"byggnadsid": "01-80-SKYTTEN2-2", "address": "Artemisgatan 17"},
        ],
    )

    assert identity["status"] == "passed"
    assert identity["ambiguous"] is False
    assert identity["multiple_matches"] is False
    assert identity["multiple_addresses_same_building_id"] is True
    assert identity["matched_building_id"] == "01-80-SKYTTEN2-2"


def test_message_metadata_drops_stale_pending_brf_resolution_after_selection():
    metadata = build_message_metadata(
        {
            "content": "Building ID: 01-80-HEDVIG15-1\nThe building was constructed in 2005.",
            "classification": "building_specific",
            "route": "building_specific",
        },
        {
            "last_user_message": "when was this building built?",
            "byggnadsid": "01-80-HEDVIG15-1",
            "selected_brf_building_id": "01-80-HEDVIG15-1",
            "pending_brf_resolution": {
                "brf_name": "Solgläntan 1",
                "question": "Which building should I use?",
            },
            "brf_resolution": {
                "status": "resolved_by_user_selection",
                "selected_building_id": "01-80-HEDVIG15-1",
            },
        },
    )

    assert metadata["brf_resolution"]["status"] == "resolved_by_user_selection"
    assert metadata["selected_brf_building_id"] == "01-80-HEDVIG15-1"
    assert "pending_brf_resolution" not in metadata


def test_retrieved_facts_separate_epc_energy_metrics():
    facts = extract_retrieved_facts(
        {
            "byggnadsid": "01-80-SKYTTEN2-2",
            "epc_egienergiprestanda": 201,
            "epc_egispecifikenergianvandning": 198,
            "epc_egispecifikenergianvandning_calc": 198,
            "epc_egiprimarenergital2019": 201,
            "epc_egiprimarenergital2020_calc": 145,
        }
    )

    assert facts["energy_performance"] == 201
    assert facts["specific_energy_use"] == 198
    assert facts["primary_energy_number"] == 145


def test_missing_declared_performance_is_not_filled_from_a_different_metric():
    facts = extract_retrieved_facts({
        "epc_egispecifikenergianvandning_calc": 198,
        "epc_egiprimarenergital2020_calc": 145,
    })
    assert "energy_performance" not in facts
    assert facts["specific_energy_use"] == 198
    assert facts["primary_energy_number"] == 145


def test_legacy_explicit_energy_performance_remains_supported():
    assert extract_retrieved_facts({"EnergyClassKwhM2": 123})["energy_performance"] == 123


@pytest.mark.parametrize("declared_field", [
    "epc_egienergiklass", "declaredEnergyClass", "EPC_EGIENERGIKLASS", "DeclaredEnergyClass",
])
def test_original_declared_energy_class_overrides_cached_aliases(declared_field):
    source = {
        declared_field: "G", "energy_class": "B", "declared_energy_class": "B",
        "epc_egienergiklass2020_calc": "E", "calculated_energy_class": "D",
    }
    original = source.copy()
    facts = extract_retrieved_facts(source)
    assert facts["energy_class"] == "G"
    assert facts["declared_energy_class"] == "G"
    assert facts["calculated_energy_class"] == "E"
    assert facts["epc_egienergiklass2020_calc"] == "E"
    assert source == original


@pytest.mark.parametrize("epc_class,expected", [("G", "G"), (None, "C"), ("", "C"), ("   ", "C")])
def test_actual_energy_class_sources_have_explicit_precedence(epc_class, expected):
    facts = extract_retrieved_facts({
        "epc_egienergiklass": epc_class, "declaredEnergyClass": "C", "energy_class": "B",
        "epc_egienergiklass2020_calc": "E",
    })
    assert facts["energy_class"] == expected
    assert facts["declared_energy_class"] == expected
    assert facts["calculated_energy_class"] == "E"


@pytest.mark.parametrize("calculated_field", [
    "epc_egienergiklass2020_calc", "epc_egienergiklass2016_calc", "epc_egienergiklass_calc",
])
def test_calculated_class_only_drops_ambiguous_declared_aliases(calculated_field):
    facts = extract_retrieved_facts({
        "epc_egienergiklass": None, "declaredEnergyClass": None,
        "energy_class": "B", "declared_energy_class": "B", "energyClass": "B",
        calculated_field: "E", "calculated_energy_class": "D",
    })
    assert "energy_class" not in facts
    assert "declared_energy_class" not in facts
    assert facts["calculated_energy_class"] == "E"
    assert facts[calculated_field] == "E"


def test_energy_class_extraction_keeps_both_calculation_bases():
    facts = extract_retrieved_facts({
        "epc_egienergiklass2016_calc": "D", "epc_egienergiklass2020_calc": "E",
        "energy_class": "D",
    })
    assert "energy_class" not in facts
    assert "declared_energy_class" not in facts
    assert facts["calculated_energy_class"] == "E"
    assert facts["epc_egienergiklass2016_calc"] == "D"
    assert facts["epc_egienergiklass2020_calc"] == "E"


@pytest.mark.parametrize("source", [
    {"energy_class": "C"},
    {"energyClass": "C"},
    {"energy_class": "C", "epc_egienergiklass2020_calc": None, "epc_egienergiklass": None},
])
def test_legacy_energy_class_without_actual_calculation_remains_supported(source):
    assert extract_retrieved_facts(source)["energy_class"] == "C"


def test_actual_declared_energy_class_in_later_source_overrides_earlier_alias():
    facts = extract_retrieved_facts(
        {"retrieved_facts": {"energy_class": "B", "declared_energy_class": "B"}},
        {"row": {"declaredEnergyClass": "G", "epc_egienergiklass2020_calc": "E"}},
    )
    assert facts["energy_class"] == "G"
    assert facts["declared_energy_class"] == "G"
    assert facts["calculated_energy_class"] == "E"


@pytest.mark.parametrize("language,message,label", [
    ("en", "What is our energy class?", "declared energy class"),
    ("sv", "Vilken energiklass har vi?", "deklarerad energiklass"),
])
@pytest.mark.parametrize("calculation_evidence", [
    {"retrieved_facts": {"calculated_energy_class": "E"}},
    {"retrieved_facts": {"epc_egienergiklass2020_calc": "E"}},
    {"epc_egienergiklass2016_calc": "D"},
])
def test_missing_class_note_identifies_declared_class_when_calculation_known(language, message, label, calculation_evidence):
    metadata = {
        "response_language": language, "uncertainty": {"missing_facts": ["energy_class"]},
        **calculation_evidence,
    }
    answer = apply_response_safety_notes("Recorded calculation: E.", metadata, message)
    assert label in answer
    note = answer.split("\n\n", 1)[1]
    assert "calculated energy class" not in note
    assert "beräknad energiklass" not in note


@pytest.mark.parametrize("language,message,label", [
    ("en", "What is our energy class?", "energy class"),
    ("sv", "Vilken energiklass har vi?", "energiklass"),
])
def test_missing_class_note_retains_existing_label_without_calculation(language, message, label):
    answer = apply_response_safety_notes("Unavailable.", {
        "response_language": language, "uncertainty": {"missing_facts": ["energy_class"]},
    }, message)
    assert label in answer
    assert "declared energy class" not in answer
    assert "deklarerad energiklass" not in answer


def test_handoff_status_is_exposed_without_preview_contents():
    metadata = build_message_metadata(
        {"content": "Email submitted; delivery is unconfirmed.", "route": "expert_handoff", "classification": "expert_handoff"},
        {"expert_handoff_status": "submitted", "expert_handoff_sent": True,
         "expert_handoff_pending_confirmation": False,
         "expert_handoff_preview": {"private": "contents"}},
    )
    assert metadata["expert_handoff_status"] == "submitted"
    assert metadata["expert_handoff_pending_confirmation"] is False
    assert "expert_handoff_preview" not in metadata


@pytest.mark.parametrize("status", ["generated", "fallback", "storage_failed"])
def test_report_status_reaches_message_metadata(status):
    metadata = build_message_metadata({
        "classification": "draft_energy_report", "content": "Report outcome",
        "report_status": status,
    }, {})
    assert metadata["route"] == "report_generation"
    assert metadata["report_status"] == status


def test_generic_turn_does_not_publish_previous_handoff_or_report_outcome():
    metadata = build_message_metadata(
        {"route": "generic", "classification": "generic", "content": "General advice"},
        {"expert_handoff_status": "submitted", "expert_handoff_sent": True,
         "report_status": "generated"},
    )
    assert "expert_handoff_status" not in metadata
    assert "expert_handoff_sent" not in metadata
    assert "report_status" not in metadata
