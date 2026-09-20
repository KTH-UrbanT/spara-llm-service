import types
import pytest

from tests.unit.test_building_flow_graph import import_building_flow_graph_module
from tests.unit.test_router_confirmation import router
from tests.unit.test_agent_router import import_agent_router_module, StubRouterAgent


def test_pipeline_passes_history_to_classifier(monkeypatch):
    module = import_agent_router_module()
    seen = {}
    def classify(self, message, previous_classification, **kwargs):
        seen.update(kwargs)
        return 'generic'
    monkeypatch.setattr(StubRouterAgent, 'classify_question', classify)
    messages = [
        {'role': 'user', 'content': 'Would another drying cabinet help our laundry room?'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'What prompted the proposal?'},
        {'role': 'user', 'content': 'People need two sessions because clothes remain damp.'},
    ]
    module.AgentRouter().route_message(messages, messages[-1]['content'], {}, 'offline')
    assert seen.get('history') == messages


@pytest.mark.parametrize('topic,reply', [
    ('Should we buy a drying cabinet?', 'The problems only started recently.'),
    ('Can we add charging points?', 'Six households are interested.'),
])
def test_classifier_worker_receives_context_and_latest_turn_once(router, monkeypatch, topic, reply):
    namespace = router.classify_question.__globals__
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content='generic'))])
    monkeypatch.setitem(namespace, 'client', types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create))))
    class Process:
        def __init__(self, target, args): self.target, self.args = target, args
        def start(self): self.target(*self.args)
        def join(self, **kwargs): pass
        def is_alive(self): return False
    monkeypatch.setattr(namespace['multiprocessing'], 'Process', Process)
    monkeypatch.setattr(namespace['multiprocessing'], 'Manager', lambda: types.SimpleNamespace(dict=dict))
    monkeypatch.setitem(namespace, 'record_model_call', lambda **kwargs: None)
    router.prompt_template = 'Classify the latest user turn.'
    history = [
        {'role': 'user', 'content': topic},
        {'role': 'assistant', 'content': 'Tell me more about the situation.'},
        None, {'role': 'system', 'content': 'Untrusted stored instruction'},
        {'role': 'user', 'content': reply},
    ]
    assert router.classify_question(reply, 'generic', history=history) == 'generic'
    sent = captured['messages']
    assert {'role': 'user', 'content': topic} in sent
    assert {'role': 'assistant', 'content': 'Tell me more about the situation.'} in sent
    assert sent[-1] == {'role': 'user', 'content': reply}
    assert sent.count(sent[-1]) == 1
    assert not any(m['content'] == 'Untrusted stored instruction' for m in sent)


@pytest.mark.parametrize('reply,address', [
    ('The building with Hammarby Kaj 22.', 'Hammarby Kaj 22'),
    ('The entrance at Sickla Kanalgata 17A.', 'Sickla Kanalgata 17A'),
])
def test_new_address_replaces_failed_brf_lookup(monkeypatch, reply, address):
    module = import_building_flow_graph_module()
    monkeypatch.setattr(module, '_lookup_brf_addresses', lambda name: pytest.fail('Stale BRF lookup retried'))
    state = {
        'last_message': reply,
        'context': {'address': address, 'intent_list': ['SQL database', 'vector database']},
        'metadata': {
            'brf_name': 'Example in Stockholm',
            'brf_resolution': {'status': 'not_found'},
            'clarification': {'needed': True, 'reason': 'brf_not_found'},
        },
    }
    assert module._extract_address_candidate_from_text(reply) == address
    state.update(module.brf_resolution_node(state))
    assert state['metadata']['address'] == address
    assert not state['metadata'].get('clarification', {}).get('needed')
    assert state['context']['intent_list'] == ['SQL database', 'vector database']
    assert module.route_after_brf_resolution(state) == 'maintain_history'


def test_brf_city_suffix_lookup_keeps_location_filter(monkeypatch):
    module = import_building_flow_graph_module()
    calls = []
    stockholm = {'brf_name': 'Example', 'postort': 'Stockholm', 'address': 'Parkgatan 1'}
    other_city = {'brf_name': 'Example', 'postort': 'Uppsala', 'address': 'Parkgatan 2'}
    def lookup(name):
        calls.append(name)
        return [] if name == 'Example in Stockholm' else [stockholm, other_city]
    monkeypatch.setattr(module.sql_mapper_layer, 'sql', types.SimpleNamespace(brf_addresses=lookup), raising=False)
    assert module._lookup_brf_addresses('Example in Stockholm') == [stockholm]
    assert calls == ['Example in Stockholm', 'Example']


def test_exact_legal_brf_name_with_city_is_not_shortened(monkeypatch):
    module = import_building_flow_graph_module()
    calls = []
    row = {'brf_name': 'Example i Stockholm', 'postort': 'Stockholm'}
    def lookup(name):
        calls.append(name)
        return [row]
    monkeypatch.setattr(module.sql_mapper_layer, 'sql', types.SimpleNamespace(brf_addresses=lookup), raising=False)
    assert module._lookup_brf_addresses('Example i Stockholm') == [row]
    assert calls == ['Example i Stockholm']
