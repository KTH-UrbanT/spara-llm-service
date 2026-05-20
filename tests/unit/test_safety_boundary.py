from src.pipeline.evaluation_metadata import build_message_evidence, build_message_metadata
from src.pipeline.safety_analysis import (
    assess_boundary_safety,
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
