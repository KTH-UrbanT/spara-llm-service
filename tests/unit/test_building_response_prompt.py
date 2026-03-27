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


def test_ensure_building_identifier_in_response_prefixes_missing_id():
    text = ensure_building_identifier_in_response(
        "The building has energy class B.",
        "01-80-FILOSOFEN2-3",
    )

    assert text.startswith("Building ID: 01-80-FILOSOFEN2-3")
