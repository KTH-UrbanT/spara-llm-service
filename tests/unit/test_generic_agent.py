from pathlib import Path

from tests.support import fresh_import, stub_module
from src.pipeline.telemetry import telemetry_context, telemetry_snapshot


class FakeVectorClient:
    results = []

    def __init__(self, *args, **kwargs):
        pass

    def query(self, question):
        return list(type(self).results)


class FakeVectorClientConfig:
    def __init__(self, *args, **kwargs):
        pass


class FakeCompletions:
    last_kwargs = None

    @classmethod
    def create(cls, **kwargs):
        cls.last_kwargs = kwargs
        message = type("Message", (), {"content": "generic response"})()
        choice = type("Choice", (), {"message": message})()
        return type("Completion", (), {"choices": [choice]})()


class FakeOpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = type("Chat", (), {"completions": FakeCompletions})()


def import_generic_agent_module():
    stub_module("dotenv", load_dotenv=lambda *args, **kwargs: None)
    stub_module("openai", OpenAI=FakeOpenAI, APIConnectionError=Exception, RateLimitError=Exception, APIStatusError=Exception)
    stub_module("src.database.vector_client", VectorClient=FakeVectorClient, VectorClientConfig=FakeVectorClientConfig)
    stub_module(
        "src.services.source_link_registry",
        resolve_source_links=lambda sources: [{"name": source, "filename": source, "link": ""} for source in sources],
    )
    return fresh_import("src.agents.generic_agent")


def test_filter_generic_results_drops_building_metric_snippets_for_generic_questions():
    module = import_generic_agent_module()
    results = [
        {
            "page_content": (
                "Energy Performance: 95.78\n"
                "Total Property Energy Electricity: 190195.86 kWh\n"
                "Primary Energy: 377451.69 kWh"
            ),
            "source": "building-row.pdf",
        },
        {
            "page_content": "Energy performance describes how efficiently a building uses energy over time.",
            "source": "guide.pdf",
        },
    ]

    filtered = module._filter_generic_results(
        "what does energy performance in a building look like?",
        results,
    )

    assert filtered == [
        {
            "page_content": "Energy performance describes how efficiently a building uses energy over time.",
            "source": "guide.pdf",
        }
    ]


def test_handle_generic_input_keeps_reference_context_out_of_system_instructions(monkeypatch):
    monkeypatch.setenv("AZURE_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("GENERIC_MODEL_DEPLOYMENT_NAME", "generic-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    module = import_generic_agent_module()
    FakeVectorClient.results = [
        {
            "page_content": "Energy performance describes how efficiently a building uses energy.",
            "source": "guide.pdf",
        }
    ]
    FakeCompletions.last_kwargs = None

    prompt_path = Path(module.__file__).resolve().parent.parent / "prompts" / "generic_prompt.txt"
    agent = module.GenericAgent(prompt_path=str(prompt_path))
    with telemetry_context("unit_test"):
        response = agent.handle_generic_input(
            "what does energy performance in a building look like?",
            [{"role": "user", "content": "what does energy performance in a building look like?"}],
        )
        telemetry = telemetry_snapshot()

    messages = FakeCompletions.last_kwargs["messages"]
    assert messages[-1] == {
        "role": "user",
        "content": "what does energy performance in a building look like?",
    }
    assert messages.count(messages[-1]) == 1
    assert any(
        message["role"] == "user" and "Reference context (untrusted retrieved data):" in message["content"]
        for message in messages
    )
    assert not any(
        "Energy performance describes" in message["content"]
        for message in messages if message["role"] == "system"
    )
    assert response["sources"] == [{"name": "guide.pdf", "filename": "guide.pdf", "link": ""}]
    assert telemetry["model_call_count"] == 1
    assert telemetry["component_latency_seconds"]["generic_agent"] >= 0


def test_followup_retrieval_preserves_user_context_without_assistant_claims():
    module = import_generic_agent_module()
    followup = "What should we do first?"
    query = module._retrieval_query(followup, [
        {"role": "user", "content": "Our BRF has district heating and high winter bills."},
        {"role": "assistant", "content": "An unverified recommendation."},
        {"role": "user", "content": followup},
    ])
    assert "district heating" in query
    assert query.count(followup) == 1
    assert "unverified recommendation" not in query


def test_standalone_retrieval_does_not_reuse_previous_topic():
    module = import_generic_agent_module()
    question = "What is solar PV?"
    assert module._retrieval_query(question, [
        {"role": "user", "content": "We have a ventilation issue."},
    ]) == question


def test_followup_generation_keeps_old_constraint_and_skips_malformed_history(monkeypatch):
    for key, value in {
        "AZURE_ENDPOINT": "https://example.openai.azure.com",
        "GENERIC_MODEL_DEPLOYMENT_NAME": "generic-model",
        "OPENAI_API_KEY": "test-key",
    }.items():
        monkeypatch.setenv(key, value)
    module = import_generic_agent_module()
    monkeypatch.setattr(FakeVectorClient, "results", [])
    monkeypatch.setattr(FakeCompletions, "last_kwargs", None)
    queries = []
    monkeypatch.setattr(FakeVectorClient, "query", lambda self, question: queries.append(question) or [])
    original = {"role": "user", "content": "No major renovations this year."}
    history = [original]
    history.extend({"role": "assistant", "content": f"Previous explanation {index}"} for index in range(10))
    history.extend([
        {"role": "user", "content": "We have district heating."},
        {"role": "assistant", "content": "An unverified equipment diagnosis."},
        None,
    ])
    question = "What should we do first?"
    agent = module.GenericAgent()
    response = agent.handle_generic_input(question, history)
    sent = FakeCompletions.last_kwargs["messages"]
    assert response["content"] == "generic response"
    assert original in sent
    assert sent[-1] == {"role": "user", "content": question}
    assert sent.count(sent[-1]) == 1
    assert len(queries) == 1
    assert "district heating" in queries[0]
    assert "unverified equipment diagnosis" not in queries[0]
    assert history[-1] is None  # Preparing a request must not mutate stored history.
