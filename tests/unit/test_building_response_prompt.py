from src.agents.building_response_prompt import (
    build_building_response_prompt,
    ensure_building_identifier_in_response,
    merge_identifier_metadata,
    select_preferred_identifier,
)


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


def test_building_response_prompt_sets_swedish_response_language():
    prompt = build_building_response_prompt(
        user_input="Vad är energiklassen för min byggnad?",
        current_address="Ringvägen 10",
        history=[{"role": "user", "content": "Vad är energiklassen för min byggnad?"}],
        action_description="SQL database",
        results={"energy_class": "C"},
        metadata={"byggnadsid": "01-80-FILOSOFEN2-3"},
        building_id="01-80-FILOSOFEN2-3",
    )

    assert "Response language: Swedish" in prompt
    assert "answer in Swedish" in prompt


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
    assert "Energy conservation / reduce demand and waste" in prompt
    assert "Energy efficiency / improve equipment and building systems" in prompt
    assert "Energy management measures / improve controls, monitoring" in prompt
    assert "Renewable energy / add renewable supply after demand, efficiency, and management" in prompt


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
    assert "generic-looking follow-up after a building-specific answer" in prompt
    assert "what does FTX mean?" in prompt
    assert "other kinds" in prompt
    assert "systems available" in prompt
    assert "FTX ventilation means mechanical supply and exhaust ventilation with heat recovery" in prompt


def test_ensure_building_identifier_in_response_prefixes_missing_id():
    text = ensure_building_identifier_in_response(
        "The building has energy class B.",
        "01-80-FILOSOFEN2-3",
    )

    assert text.startswith("Building ID: 01-80-FILOSOFEN2-3")
