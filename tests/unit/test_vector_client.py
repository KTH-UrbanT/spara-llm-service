import json
import tempfile

from tests.support import fresh_import, stub_module
from src.pipeline.telemetry import telemetry_context, telemetry_snapshot


CONFIG_JSON = json.dumps(
    {
        "indexing_policy": {"mode": "consistent"},
        "vector_embedding_policy": {"dims": 1536},
    }
)


class FakeVectorDataBase:
    instances = []

    def __init__(self, indexing_policy, vector_embedding_policy, database_name, container_name):
        self.indexing_policy = indexing_policy
        self.vector_embedding_policy = vector_embedding_policy
        self.database_name = database_name
        self.container_name = container_name
        self.vector_search = object()
        self.openai_embeddings = object()
        self.setup_calls = 0
        FakeVectorDataBase.instances.append(self)

    def setup_connection(self):
        self.setup_calls += 1


class FakeRetrievalText:
    instances = []
    docs = [{"metadata": {"text": "chunk 1", "source": "doc-1"}}]
    should_raise = False

    def __init__(self, vector_search, openai_embeddings):
        self.vector_search = vector_search
        self.openai_embeddings = openai_embeddings
        FakeRetrievalText.instances.append(self)

    def search_vector(self, question, top_k=None, metadata_filter=None):
        if type(self).should_raise:
            raise RuntimeError("vector failure")
        return list(type(self).docs)


class FakeDocument:
    pass


def import_vector_client_module():
    stub_module("dotenv", load_dotenv=lambda *args, **kwargs: None)
    stub_module("src.knowledge_base.vector_database_pinecone", VectorDataBase=FakeVectorDataBase)
    stub_module(
        "src.context_retrieval.main_content_retrieval_pinecone",
        RetrievalText=FakeRetrievalText,
        Document=FakeDocument,
    )
    return fresh_import("src.database.vector_client")


def write_config_file():
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        handle.write(CONFIG_JSON)
        return handle.name


def test_vector_client_config_reads_json_and_environment(monkeypatch):
    module = import_vector_client_module()
    config_path = write_config_file()
    monkeypatch.setenv("database_name", "db-main")
    monkeypatch.setenv("container_name", "container-main")

    config = module.VectorClientConfig(config_path)

    assert config.get_vector_config() == {
        "indexing_policy": {"mode": "consistent"},
        "vector_embedding_policy": {"dims": 1536},
        "database_name": "db-main",
        "container_name": "container-main",
    }


def test_vector_client_initializes_connection_and_queries_documents(monkeypatch):
    module = import_vector_client_module()
    config_path = write_config_file()
    monkeypatch.setenv("database_name", "db-main")
    monkeypatch.setenv("container_name", "container-main")
    FakeVectorDataBase.instances.clear()
    FakeRetrievalText.instances.clear()
    FakeRetrievalText.docs = [{"metadata": {"text": "district heating", "source": "source-a"}}]
    FakeRetrievalText.should_raise = False

    client = module.VectorClient(module.VectorClientConfig(config_path))
    with telemetry_context("unit_test"):
        result = client.query("How do I save energy?")
        telemetry = telemetry_snapshot()

    assert FakeVectorDataBase.instances[0].setup_calls == 1
    assert FakeRetrievalText.instances[0].vector_search is FakeVectorDataBase.instances[0].vector_search
    assert result == [{"page_content": "district heating", "source": "source-a"}]
    assert telemetry["retrieval_call_count"] == 1
    assert telemetry["component_latency_seconds"]["vector_retrieval"] >= 0


def test_vector_client_returns_empty_list_when_retrieval_fails(monkeypatch):
    module = import_vector_client_module()
    config_path = write_config_file()
    monkeypatch.setenv("database_name", "db-main")
    monkeypatch.setenv("container_name", "container-main")
    FakeRetrievalText.should_raise = True

    client = module.VectorClient(module.VectorClientConfig(config_path))

    with telemetry_context("unit_test"):
        assert client.query("trigger failure") == []
        telemetry = telemetry_snapshot()

    assert telemetry["retrieval_call_count"] == 1
    assert telemetry["failures"][0]["component"] == "vector_retrieval"
