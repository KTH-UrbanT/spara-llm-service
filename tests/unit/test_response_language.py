from src.pipeline.response_language import (
    detect_response_language,
    language_instruction_for_message,
    response_language_for_message,
)


def test_detects_swedish_question():
    assert detect_response_language("Vad är energiklassen för min byggnad?") == "sv"


def test_english_question_with_swedish_address_stays_english():
    assert detect_response_language("What is the energy class for Öregrundsgatan 9?") == "en"


def test_explicit_response_language_marker_wins_for_constructed_prompts():
    assert detect_response_language("Response language: Swedish\nUser input: What is this?") == "sv"


def test_falls_back_to_recent_user_language_for_short_followup():
    assert (
        response_language_for_message(
            "Ringvägen 10",
            messages=[{"role": "user", "content": "Vad är min energiklass?"}],
        )
        == "sv"
    )


def test_language_instruction_names_swedish_for_swedish_question():
    instruction = language_instruction_for_message("Hur kan vi sänka värmekostnaden?")

    assert "Response language: Swedish" in instruction
