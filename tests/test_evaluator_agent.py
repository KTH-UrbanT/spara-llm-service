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

    assert out == {"verdict": "pass"}


@patch("src.agents.evaluator_agent.AzureOpenAI")
def test_evaluator_malformed_json_fail_open(mock_azure_client_cls, mock_env_vars):
    mock_client = MagicMock()
    mock_azure_client_cls.return_value = mock_client

    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="not-json"))]
    mock_client.chat.completions.create.return_value = mock_resp

    agent = EvaluatorAgent()
    out = agent.evaluate("q", {"a": 1}, "answer")

    assert out == {"verdict": "pass"}


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
