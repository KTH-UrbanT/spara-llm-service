from pathlib import Path

from tests.support import fresh_import, stub_module


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


class FakeAzureOpenAI:
    def __init__(self, *args, **kwargs):
        self.chat = type("Chat", (), {"completions": FakeCompletions})()


def import_generic_agent_module():
    stub_module("dotenv", load_dotenv=lambda *args, **kwargs: None)
    stub_module("openai", AzureOpenAI=FakeAzureOpenAI, APIConnectionError=Exception, RateLimitError=Exception, APIStatusError=Exception)
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


def test_handle_generic_input_keeps_reference_context_out_of_user_message(monkeypatch):
    monkeypatch.setenv("AZURE_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("GENERIC_MODEL_DEPLOYMENT_NAME", "generic-model")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("GENERIC_MODEL_API_VERSION", "2024-01-01")

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
    response = agent.handle_generic_input(
        "what does energy performance in a building look like?",
        [{"role": "user", "content": "what does energy performance in a building look like?"}],
    )

    messages = FakeCompletions.last_kwargs["messages"]
    assert messages[-1] == {
        "role": "user",
        "content": "what does energy performance in a building look like?",
    }
    assert any(
        message["role"] == "system" and "Reference context:" in message["content"]
        for message in messages
    )
    assert response["sources"] == [{"name": "guide.pdf", "filename": "guide.pdf", "link": ""}]
