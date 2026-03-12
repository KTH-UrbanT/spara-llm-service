import os

import pytest


pytestmark = pytest.mark.integration


def test_vector_client_env_connection_smoke():
    dotenv = pytest.importorskip("dotenv")
    dotenv.load_dotenv(override=False)
    pytest.importorskip("pinecone")
    pytest.importorskip("langchain_openai")
    pytest.importorskip("langchain_core")

    required_env = [
        "database_name",
        "container_name",
        "OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT",
        "OPENAI_API_VERSION",
        "AZURE_ENDPOINT",
        "OPENAI_API_KEY",
    ]
    missing = [name for name in required_env if not os.getenv(name)]
    if missing:
        pytest.skip(f"Missing required env vars for vector integration test after loading .env: {missing}")

    from src.database.vector_client import VectorClient, VectorClientConfig

    query = os.getenv("VECTOR_SMOKE_QUERY", "energy retrofit advice")
    client = VectorClient(VectorClientConfig())
    results = client.query(query)

    assert isinstance(results, list)
