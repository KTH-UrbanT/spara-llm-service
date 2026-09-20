import pytest

from src.agents.completion_options import completion_options


@pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"])
def test_new_deployments_omit_unsupported_sampling_parameters(model):
    options = completion_options(model, temperature=0.9, top_p=0.23)
    assert not {"max_tokens", "temperature", "top_p", "frequency_penalty", "presence_penalty"} & options.keys()
    assert options["max_completion_tokens"] == 4096
    assert options["reasoning_effort"] == "low"


def test_report_budget_includes_room_for_reasoning_and_output():
    assert completion_options("gpt-6-astra", reasoning_tokens=16384)["max_completion_tokens"] == 16384


def test_legacy_model_keeps_sampling_and_output_limit():
    options = completion_options("gpt-4o-mini", max_tokens=600, temperature=0, top_p=1)
    assert options["max_tokens"] == 600
    assert options["temperature"] == 0
    assert "reasoning_effort" not in options


def test_o4_still_uses_completion_token_budget():
    assert completion_options("o4-mini") == {"max_completion_tokens": 4000}
