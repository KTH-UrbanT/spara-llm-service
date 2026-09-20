"""Exercise real SDK request construction offline, without reading the local .env."""
from pathlib import Path
import subprocess
import sys

import pytest

from src.azure_openai_v1 import azure_v1_base_url


@pytest.mark.parametrize('endpoint', [
    'https://example.openai.azure.com',
    'https://example.openai.azure.com/',
    'https://example.openai.azure.com/openai/v1',
    'https://example.openai.azure.com/openai/v1/',
])
def test_root_and_v1_endpoints_normalize_once(endpoint):
    assert azure_v1_base_url(endpoint) == 'https://example.openai.azure.com/openai/v1/'


@pytest.mark.parametrize('endpoint', [
    None, '', 'http://example.openai.azure.com',
    'https://example.services.ai.azure.com/api/projects/default',
    'https://example.openai.azure.com/openai/deployments/example',
    'https://example.openai.azure.com/openai/v1/?api-version=2026-07-09',
    'https://user:secret@example.openai.azure.com',
])
def test_wrong_endpoint_formats_fail_before_requests(endpoint):
    with pytest.raises(ValueError):
        azure_v1_base_url(endpoint)


# Existing unit suites stub SDK modules globally. A fresh interpreter ensures these
# checks use installed OpenAI/LangChain packages, with HTTP transport mocked only.
SDK_CHECK = r'''
import importlib
import json
import os
import sys
import types

import dotenv
import httpx
import openai

dotenv.load_dotenv = lambda *a, **k: False
for key in list(os.environ):
    if key.endswith('_API_VERSION') or key.startswith(('OPENAI_', 'AZURE_OPENAI_')):
        os.environ.pop(key)
os.environ.update({
    'AZURE_ENDPOINT': sys.argv[2],
    'OPENAI_API_KEY': 'offline-test-key',
    'OPENAI_API_TYPE': 'azure',
    'OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT': 'text-embedding-3-large',
    'PINECONE_API_KEY': 'offline-test-key',
    'AZURE_DEPLOYMENT': 'sql-deployment',
})
for role in ('GENERIC', 'ROUTER', 'CONVERSATIONAL', 'BUILDING', 'OPENAI_RESPONSE'):
    os.environ[role + '_MODEL_DEPLOYMENT_NAME'] = role.lower() + '-deployment'

requests = []
def respond(request):
    requests.append(request)
    assert request.url.host == 'example.openai.azure.com'
    assert not request.url.query
    assert request.headers['authorization'] == 'Bearer offline-test-key'
    if request.url.path == '/openai/v1/embeddings':
        return httpx.Response(200, json={
            'object': 'list', 'model': 'text-embedding-3-large',
            'data': [{'object': 'embedding', 'index': 0, 'embedding': [0.1, 0.2]}],
            'usage': {'prompt_tokens': 1, 'total_tokens': 1},
        })
    assert request.url.path == '/openai/v1/chat/completions'
    return httpx.Response(200, json={
        'id': 'offline', 'object': 'chat.completion', 'created': 0,
        'model': 'offline', 'choices': [{'index': 0, 'finish_reason': 'stop',
        'message': {'role': 'assistant', 'content': 'ok'}}],
    })

real_client, real_async_client = openai.OpenAI, openai.AsyncOpenAI
def client(**kwargs):
    assert 'api_version' not in kwargs and 'azure_endpoint' not in kwargs
    kwargs['http_client'] = httpx.Client(transport=httpx.MockTransport(respond))
    return real_client(**kwargs)
def async_client(**kwargs):
    kwargs['http_client'] = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return real_async_client(**kwargs)
openai.OpenAI, openai.AsyncOpenAI = client, async_client
sys.modules['src.database.vector_client'] = types.SimpleNamespace(
    VectorClient=object, VectorClientConfig=object)
sys.modules['src.services.source_link_registry'] = types.SimpleNamespace(
    resolve_source_links=lambda sources: [])

target = sys.argv[1]
if target in ('vector_database', 'vector_database_pinecone'):
    sys.modules['pinecone'] = types.SimpleNamespace(Pinecone=lambda **k: object())
    sys.modules['azure.cosmos'] = types.SimpleNamespace(
        CosmosClient=lambda *a, **k: object(), PartitionKey=lambda **k: object())
    sys.modules['src.knowledge_base.azure_cosmos_custom'] = types.SimpleNamespace(
        AzureCosmosDBNoSqlVectorSearchCustom=lambda **k: object())
    module = importlib.import_module('src.knowledge_base.' + target)
    embeddings = module.VectorDataBase({}, {}, 'db', 'container').openai_embeddings
    # Avoid downloading tokenizer files in this offline request-format check.
    embeddings.check_embedding_ctx_length = False
    assert embeddings.embed_query('energy') == [0.1, 0.2]
    expected_model = 'text-embedding-3-large'
else:
    module = importlib.import_module('src.agents.' + target)
    if target in ('router_agent', 'parse_intent_agent'):
        configured_client, expected_model = module.client, module.deployment
    elif target == 'specialized_sql_layer':
        layer = module.SpecializedSQLLayer.__new__(module.SpecializedSQLLayer)
        conf = layer._load_conf()
        configured_client, expected_model = layer._init_client(conf), conf.model_or_deployment
    else:
        cls = getattr(module, {'generic_agent': 'GenericAgent',
            'conversationalist_agent': 'ConversationalAgent',
            'openai_agent': 'OpenAIResponseAgent'}[target])
        agent = cls.__new__(cls)
        agent._initialize_openai_client()
        configured_client, expected_model = agent.client, agent.deployment
    result = configured_client.chat.completions.create(
        model=expected_model, messages=[{'role': 'user', 'content': 'test'}])
    assert result.choices[0].message.content == 'ok'
assert len(requests) == 1
assert json.loads(requests[0].content)['model'] == expected_model
'''


@pytest.mark.parametrize('target', [
    'generic_agent', 'router_agent', 'conversationalist_agent',
    'parse_intent_agent', 'openai_agent', 'specialized_sql_layer',
    'vector_database_pinecone', 'vector_database',
])
@pytest.mark.parametrize('endpoint', [
    'https://example.openai.azure.com/',
    'https://example.openai.azure.com/openai/v1/',
])
def test_real_sdk_uses_azure_v1_without_dated_version(target, endpoint):
    result = subprocess.run(
        [sys.executable, '-c', SDK_CHECK, target, endpoint],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
