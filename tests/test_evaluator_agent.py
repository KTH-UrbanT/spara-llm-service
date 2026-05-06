import os
from unittest.mock import MagicMock, patch

import pytest

from src.agents.evaluator_agent import EvaluatorAgent


@pytest.fixture
def mock_env_vars():
    with patch.dict(
        os.environ,
        {
            "AZURE_ENDPOINT": "https://example.openai.azure.com",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME": "resp-model",
            "OPENAI_RESPONSE_MODEL_API_VERSION": "2024-02-15-preview",
        },
        clear=False,
    ):
        yield


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_returns_pass(mock_azure_client_cls, mock_env_vars):
    mock_client = MagicMock()
    mock_azure_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content='{"verdict":"pass","faithfulness_score":8,"completeness_score":8,"issues":[],"corrective_feedback":""}'))
    ]
    mock_client.chat.completions.create.return_value = mock_resp

    agent = EvaluatorAgent()
    out = agent.evaluate("q", {"a": 1}, "answer")

    assert out["verdict"] == "pass"


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_returns_fail_with_feedback(mock_azure_client_cls, mock_env_vars):
    mock_client = MagicMock()
    mock_azure_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(
            message=MagicMock(
                content=(
                    '{"verdict":"fail","faithfulness_score":4,"completeness_score":5,'
                    '"issues":["unsupported claim"],"corrective_feedback":"Remove unsupported claim."}'
                )
            )
        )
    ]
    mock_client.chat.completions.create.return_value = mock_resp

    agent = EvaluatorAgent()
    out = agent.evaluate("q", {"a": 1}, "answer")

    assert out["verdict"] == "fail"
    assert out["corrective_feedback"] == "Remove unsupported claim."


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_exception_fail_open(mock_azure_client_cls, mock_env_vars):
    mock_client = MagicMock()
    mock_azure_client_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = RuntimeError("boom")

    agent = EvaluatorAgent()
    out = agent.evaluate("q", {"a": 1}, "answer")

    assert out["verdict"] == "pass"
    assert out["evaluator_failed"] is True
    assert "boom" in out["evaluator_failure_reason"]


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_malformed_json_fail_open(mock_azure_client_cls, mock_env_vars):
    mock_client = MagicMock()
    mock_azure_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="not-json"))]
    mock_client.chat.completions.create.return_value = mock_resp

    agent = EvaluatorAgent()
    out = agent.evaluate("q", {"a": 1}, "answer")

    assert out["verdict"] == "pass"
    assert out["evaluator_failed"] is True


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_temperature_unsupported_retries_without_temperature(mock_azure_client_cls, mock_env_vars):
    mock_client = MagicMock()
    mock_azure_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content='{"verdict":"pass","faithfulness_score":7,"completeness_score":7,"issues":[],"corrective_feedback":""}'))
    ]

    mock_client.chat.completions.create.side_effect = [
        RuntimeError("Unsupported value: 'temperature' does not support 0.0 with this model. Only the default (1) value is supported."),
        mock_resp,
    ]

    agent = EvaluatorAgent()
    out = agent.evaluate("q", {"a": 1}, "answer")

    assert out["verdict"] == "pass"
    assert mock_client.chat.completions.create.call_count == 2

    first_kwargs = mock_client.chat.completions.create.call_args_list[0].kwargs
    second_kwargs = mock_client.chat.completions.create.call_args_list[1].kwargs
    assert first_kwargs.get("temperature") == 0.0
    assert "temperature" not in second_kwargs


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_loads_alternate_prompt_when_env_var_set(mock_azure_client_cls, mock_env_vars, tmp_path):
    """Section 0.2: setting EVALUATOR_PROMPT_VERSION must actually load a different prompt file.
    Without this, arm A4 (prompt-variant-B) would silently be identical to arm A2 — the
    most damaging defect possible in the experimental setup.
    """
    mock_azure_client_cls.return_value = MagicMock()

    # Sentinel content unique enough to detect that the variant file was loaded.
    variant_content = "VARIANT_B_PROMPT_SENTINEL_xY7q3K\nThis is the v2 rubric."
    variant_file = tmp_path / "evaluator_prompt_v2.txt"
    variant_file.write_text(variant_content, encoding="utf-8")

    with patch.dict(os.environ, {"EVALUATOR_PROMPT_VERSION": str(variant_file)}, clear=False):
        agent = EvaluatorAgent()

    assert agent.prompt_template == variant_content, (
        "EvaluatorAgent did not load the prompt file pointed to by EVALUATOR_PROMPT_VERSION. "
        "Arm A4 of the experiment will be invalid."
    )
    assert agent.prompt_path == str(variant_file)


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_uses_default_prompt_when_env_var_absent(mock_azure_client_cls, mock_env_vars):
    """Companion to the above: with no env var, the default prompt loads."""
    mock_azure_client_cls.return_value = MagicMock()

    # Defensive: ensure the env var is unset for this test even if .env set it.
    env_without_version = {k: v for k, v in os.environ.items() if k != "EVALUATOR_PROMPT_VERSION"}
    with patch.dict(os.environ, env_without_version, clear=True):
        agent = EvaluatorAgent()

    assert agent.prompt_path.endswith("evaluator_prompt.txt")
    assert "VARIANT_B_PROMPT_SENTINEL" not in agent.prompt_template


@pytest.mark.skipif(
    not os.getenv("AZURE_ENDPOINT") or not os.getenv("OPENAI_API_KEY"),
    reason="Requires real Azure credentials; determinism is verified against a live deployment.",
)
def test_evaluator_determinism_same_input_twice_returns_identical_json():
    """Section 0.1: the evaluator's *judgment* must be deterministic across calls
    on the same input. Judgment = verdict + 5 dimension scores + hard_fail.

    We deliberately do NOT compare freeform text fields (corrective_feedback,
    issues, evaluator_failure_reason). On o-series Azure deployments, temperature=0.0
    is silently rejected and the fallback path runs at the model default (~1.0),
    which is non-deterministic for prose. The thesis only depends on the structured
    judgment, so that is what we pin. The freeform variance is logged via the
    warning in evaluator_agent.py:196 and documented as a thesis limitation.
    """
    agent = EvaluatorAgent()
    question = "What is the energy class of the building at Hammarby Gata 10?"
    aggregated_data = {
        "generic_sql": [{"address": "Hammarby Gata 10", "declaredEnergyClass": "B"}],
        "vector": {"sources": [], "snippets": []},
    }
    answer = "The building's declared energy class is B."

    out1 = agent.evaluate(question, aggregated_data, answer)
    out2 = agent.evaluate(question, aggregated_data, answer)

    JUDGMENT_KEYS = {
        "verdict",
        "faithfulness_score",
        "groundedness_score",
        "completeness_score",
        "numeric_fidelity_score",
        "constraint_satisfaction_score",
        "uncertainty_calibration_score",
        "hard_fail",
    }

    def _judgment_only(d: dict) -> dict:
        return {k: d.get(k) for k in JUDGMENT_KEYS}

    assert _judgment_only(out1) == _judgment_only(out2), (
        "Evaluator JUDGMENT differs across calls on identical input. "
        "The deployment must honor temperature=0.0 OR you must accept that "
        "your thesis run cannot claim full determinism. Inspect the latest "
        "warning in logs; if 'Evaluator model rejected temperature=0.0' was "
        "emitted, switch to a deployment that supports temperature override."
    )


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_extended_schema_payload(mock_azure_client_cls, mock_env_vars):
    mock_client = MagicMock()
    mock_azure_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(
            message=MagicMock(
                content=(
                    '{"verdict":"fail","faithfulness_score":7,"groundedness_score":7,'
                    '"completeness_score":6,"numeric_fidelity_score":5,'
                    '"constraint_satisfaction_score":7,"uncertainty_calibration_score":7,'
                    '"hard_fail":false,"hard_fail_reason":"",'
                    '"issues":["Numeric value mismatches SQL row"],'
                    '"corrective_feedback":"Use the exact value from aggregated_data."}'
                )
            )
        )
    ]
    mock_client.chat.completions.create.return_value = mock_resp

    agent = EvaluatorAgent()
    out = agent.evaluate("q", {"a": 1}, "answer")

    assert out["verdict"] == "fail"
    assert out["numeric_fidelity_score"] == 5
    assert out["hard_fail"] is False
    assert out["evaluator_failed"] is False
