"""Unit tests for OpenAIResponseAgent.

Section 0.8 of plan-eil-v1.md introduced SUMMARIZER_TEMPERATURE env-var control
plus graceful fallback for o-series deployments that reject explicit temperature.
These tests cover the env-var parser and the fallback-detection helper without
requiring real Azure credentials.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

from src.agents.openai_agent import OpenAIResponseAgent


@pytest.fixture
def mock_env_vars():
    """Provide minimum env vars so OpenAIResponseAgent can be instantiated."""
    with patch.dict(
        os.environ,
        {
            "AZURE_ENDPOINT": "https://example.openai.azure.com",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME": "gpt-4o",
            "OPENAI_RESPONSE_MODEL_API_VERSION": "2024-02-15-preview",
        },
        clear=False,
    ):
        yield


@patch("src.agents.openai_agent.AzureOpenAI")
class TestSummarizerTemperatureEnv:
    def test_default_temperature_is_0_2(self, mock_cls, mock_env_vars):
        """Production default. Run-arm scripts pin to 0.0 explicitly."""
        env_no_var = {k: v for k, v in os.environ.items() if k != "SUMMARIZER_TEMPERATURE"}
        with patch.dict(os.environ, env_no_var, clear=True):
            agent = OpenAIResponseAgent()
            assert agent._get_summarizer_temperature() == 0.2

    def test_env_var_pinning_to_zero(self, mock_cls, mock_env_vars):
        """The thesis manifest sets SUMMARIZER_TEMPERATURE=0.0 to remove sampling noise."""
        with patch.dict(os.environ, {"SUMMARIZER_TEMPERATURE": "0.0"}, clear=False):
            agent = OpenAIResponseAgent()
            assert agent._get_summarizer_temperature() == 0.0

    def test_env_var_arbitrary_float(self, mock_cls, mock_env_vars):
        with patch.dict(os.environ, {"SUMMARIZER_TEMPERATURE": "0.7"}, clear=False):
            agent = OpenAIResponseAgent()
            assert agent._get_summarizer_temperature() == 0.7

    def test_invalid_env_var_falls_back_to_default(self, mock_cls, mock_env_vars):
        """Garbage in env var → fall back to 0.2 with warning, never crash."""
        with patch.dict(os.environ, {"SUMMARIZER_TEMPERATURE": "not-a-number"}, clear=False):
            agent = OpenAIResponseAgent()
            assert agent._get_summarizer_temperature() == 0.2

    def test_empty_env_var_falls_back_to_default(self, mock_cls, mock_env_vars):
        with patch.dict(os.environ, {"SUMMARIZER_TEMPERATURE": ""}, clear=False):
            agent = OpenAIResponseAgent()
            assert agent._get_summarizer_temperature() == 0.2


@patch("src.agents.openai_agent.AzureOpenAI")
class TestTemperatureUnsupportedDetection:
    """Section 0.8: detect Azure errors that mean 'this deployment rejects temperature'."""

    def test_detects_via_param_attribute(self, mock_cls, mock_env_vars):
        agent = OpenAIResponseAgent()
        err = type("E", (Exception,), {})()
        err.param = "temperature"
        assert agent._is_temperature_unsupported_error(err)

    def test_detects_via_unsupported_value_code(self, mock_cls, mock_env_vars):
        agent = OpenAIResponseAgent()
        err = type("E", (Exception,), {})()
        err.code = "unsupported_value"
        err.status_code = 400
        err.args = ("Unsupported temperature value",)
        # str(err) will contain "temperature" via the args.
        assert agent._is_temperature_unsupported_error(err)

    def test_detects_via_message_string(self, mock_cls, mock_env_vars):
        agent = OpenAIResponseAgent()
        err = RuntimeError(
            "Unsupported value: 'temperature' does not support 0.0 with this model. "
            "Only the default (1) value is supported."
        )
        assert agent._is_temperature_unsupported_error(err)

    def test_does_not_match_unrelated_errors(self, mock_cls, mock_env_vars):
        agent = OpenAIResponseAgent()
        err = RuntimeError("Some completely unrelated error")
        assert not agent._is_temperature_unsupported_error(err)


@patch("src.agents.openai_agent.AzureOpenAI")
class TestGenerateResponseTemperaturePropagation:
    """End-to-end: env var → API call params → side-channel meta."""

    def test_temperature_set_in_params(self, mock_cls, mock_env_vars):
        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_resp.usage = None
        mock_client.chat.completions.create.return_value = mock_resp

        with patch.dict(os.environ, {"SUMMARIZER_TEMPERATURE": "0.0"}, clear=False):
            agent = OpenAIResponseAgent()
            agent.generate_response("question", [])

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0.0
        assert agent._last_call_meta["temperature_requested"] == 0.0
        assert agent._last_call_meta["temperature_unsupported"] is False

    def test_falls_back_when_temperature_rejected(self, mock_cls, mock_env_vars):
        """The deployment rejects temperature → second call drops it, flag set."""
        from openai import BadRequestError

        mock_client = MagicMock()
        mock_cls.return_value = mock_client

        # First call: simulate o-series rejection.
        first_error = BadRequestError(
            message="Unsupported value: 'temperature' does not support 0.0",
            response=MagicMock(),
            body=None,
        )
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock(message=MagicMock(content="ok"))]
        mock_resp.usage = None
        mock_client.chat.completions.create.side_effect = [first_error, mock_resp]

        with patch.dict(os.environ, {"SUMMARIZER_TEMPERATURE": "0.0"}, clear=False):
            agent = OpenAIResponseAgent()
            answer = agent.generate_response("question", [])

        # Two calls: first with temperature, second without.
        calls = mock_client.chat.completions.create.call_args_list
        assert len(calls) == 2
        assert calls[0].kwargs.get("temperature") == 0.0
        assert "temperature" not in calls[1].kwargs
        # Side-channel signals the fallback occurred.
        assert agent._last_call_meta["temperature_unsupported"] is True
        assert agent._last_call_meta["temperature_requested"] == 0.0
        assert answer == "ok"
