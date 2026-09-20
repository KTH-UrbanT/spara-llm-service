"""Exercise real confirmation parsing without loading credentials or calling a model."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest


@pytest.fixture
def router(monkeypatch):
    for key, value in {
        "AZURE_ENDPOINT": "https://example.openai.azure.com",
        "ROUTER_MODEL_DEPLOYMENT_NAME": "router-test",
        "OPENAI_API_KEY": "test-key",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(sys.modules, "dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(
        OpenAI=lambda **kwargs: object(), APIConnectionError=ConnectionError,
        RateLimitError=RuntimeError, APIStatusError=ValueError,
    ))
    # Load separately: other routing tests replace src.agents.router_agent with a fake.
    path = Path(__file__).resolve().parents[2] / "src" / "agents" / "router_agent.py"
    spec = importlib.util.spec_from_file_location("_spara_confirmation_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RouterAgent.__new__(module.RouterAgent)


@pytest.mark.parametrize("reply", ["yes", " Sure. ", "Please send it!", "go ahead"])
def test_explicit_confirmation_is_recognized(router, reply):
    assert router.is_confirmation(reply) is True
    assert router.is_rejection(reply) is False


@pytest.mark.parametrize("reply", [
    "No, do not send anything.", "No, don't send anything!",
    "do not send it", "don't send anything", "cancel the email",
    "cancel the handoff", "not now", "No thanks.",
])
def test_cancellation_cannot_be_mistaken_for_confirmation(router, reply):
    assert router.is_rejection(reply) is True
    assert router.is_confirmation(reply) is False


@pytest.mark.parametrize("reply", [
    "", "Yes, but remove my contact details first.",
    "Send it to a different advisor instead.", "Can you explain what will be sent?",
    "Create a draft report first.",
])
def test_modified_or_ambiguous_requests_do_not_authorize_submission(router, reply):
    assert router.is_confirmation(reply) is False
