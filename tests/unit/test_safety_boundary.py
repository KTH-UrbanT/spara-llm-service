from src.pipeline.evaluation_metadata import build_message_evidence, build_message_metadata
from src.pipeline.safety_analysis import (
    assess_boundary_safety,
    assess_building_identity,
    build_out_of_scope_response,
)


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
