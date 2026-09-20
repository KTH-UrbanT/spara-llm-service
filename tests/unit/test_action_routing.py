import json
import pytest

from tests.unit.test_router_confirmation import router
from tests.unit.test_agent_router import import_agent_router_module
from tests.unit.test_draft_report_service import StubRedisClient, StubOpenAIResponseAgent
from tests.support import fresh_import, stub_module


@pytest.mark.parametrize('utterance', [
    'I need something for the board. Can you make a short report from this discussion?',
    'Please write a concise report about our charging options.',
    'Could you prepare a one-page board briefing?',
    'Export this discussion as a report for our meeting.',
    'Can you give me the energy report?',
])
def test_report_action_paraphrases(router, utterance):
    assert router.wants_draft_report(utterance)


@pytest.mark.parametrize('utterance', [
    'What does an energy report usually contain?',
    'The energy report says our heating system was upgraded.',
    'Do not create a report yet.',
    "Don't generate a report.",
    'Please explain the findings in our existing audit report.',
    'How do I write a report?',
])
def test_report_mentions_are_not_creation_requests(router, utterance):
    assert not router.wants_draft_report(utterance)


@pytest.mark.parametrize('utterance', [
    'Could you pass this to an energy advisor so we can discuss the next step?',
    'Please forward the conversation to a climate adviser.',
    'Can you contact an energy advisor for us?',
])
def test_handoff_action_paraphrases(router, utterance):
    assert router.wants_expert_handoff(utterance)


@pytest.mark.parametrize('utterance', [
    'What would an energy advisor check first?',
    'How can I contact an energy advisor?',
    'Please explain what I should send to an energy advisor.',
    'Could you draft a message for an energy advisor?',
])
def test_advisor_mentions_do_not_request_sending(router, utterance):
    assert not router.wants_expert_handoff(utterance)


def configured_pipeline(router):
    module = import_agent_router_module()
    pipeline = module.AgentRouter()
    # Real intent helpers; a misclassification must not hijack explicit actions.
    router.classify_question = lambda *args, **kwargs: 'building_specific'
    pipeline.router = router
    return module, pipeline


@pytest.mark.parametrize('pending_address', [False, True])
def test_report_action_creates_artifact_with_existing_building_context(router, monkeypatch, pending_address):
    module, pipeline = configured_pipeline(router)
    redis = StubRedisClient()
    stub_module('redis', StrictRedis=lambda *args, **kwargs: redis)
    stub_module('src.redis.redis_session_store', get_session_state=lambda thread_id: {})
    stub_module('src.agents.openai_agent', OpenAIResponseAgent=StubOpenAIResponseAgent)
    reports = fresh_import('src.services.draft_report_service')
    monkeypatch.setattr(module, 'generate_draft_report_response', reports.generate_draft_report_response)
    question = 'Can you make a short report from our discussion?'
    messages = [
        {'role': 'user', 'content': 'We want charging points for six households.'},
        {'role': 'assistant', 'content': 'We still need to check the available capacity.', 'classification': 'building_specific'},
        {'role': 'user', 'content': question},
    ]
    metadata = {'building_id': 'TEST-BUILDING-1', 'address': 'Examplegatan 1'}
    if pending_address:
        monkeypatch.setattr(module, '_awaiting_address_disambiguation', lambda metadata: True)
    response, _ = pipeline.route_message(messages, question, metadata, 'thread-report')
    assert response['route'] == 'report_generation'
    assert response['report_status'] == 'generated'
    artifact = response['downloadable_report']
    assert len(redis.calls) == 1
    key, ttl, raw = redis.calls[0]
    stored = json.loads(raw)
    assert key.endswith(artifact['report_id'])
    assert stored['thread_id'] == 'thread-report'
    assert stored['file_name'] == artifact['file_name'] == 'TEST-BUILDING-1.txt'
    assert 'Generated content.' in stored['content']


def test_advisor_action_previews_without_sending_in_building_conversation(router, monkeypatch):
    module, pipeline = configured_pipeline(router)
    monkeypatch.setattr(module, 'send_expert_handoff_email', lambda *args: pytest.fail('Sent without confirmation'))
    question = 'Could you pass this to an energy advisor so we can discuss the next step?'
    messages = [
        {'role': 'assistant', 'classification': 'building_specific', 'content': 'The supply capacity is unknown.'},
        {'role': 'user', 'content': question},
    ]
    response, metadata = pipeline.route_message(messages, question, {'building_id': 'TEST-BUILDING-1'}, 'thread-preview')
    assert response['route'] == 'expert_handoff'
    assert metadata['expert_handoff_pending_confirmation'] is True
    assert not metadata.get('expert_handoff_sent')
