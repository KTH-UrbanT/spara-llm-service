import types
import pytest

from tests.unit.test_building_flow_graph import import_building_flow_graph_module
from tests.unit.test_router_confirmation import router
from tests.unit.test_agent_router import (
    import_agent_router_module,
    StubRouterAgent,
    StubBuildingAgent,
    StubGenericAgent,
)


@pytest.mark.parametrize('question', [
    'Kan du sammanfatta de tre viktigaste nästa stegen?',
    'Bör vi alltså beställa nya fönster direkt?',
    'Vilken budget och vilka begränsningar utgår du från?',
])
@pytest.mark.parametrize('label', [None, 'conversational', 'building_specific'])
def test_swedish_advisory_followups_continue_even_with_bad_classifier(monkeypatch, question, label):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', label)
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    messages = [
        {'role': 'user', 'content': 'Vi har ojämn värme och vill minska värmekostnaderna. Budgeten är 20 000 kr.'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'Kontrollera styrningen innan ni väljer en investering.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(messages, question, {}, 'sv-followup')
    assert response['route'] == 'generic'
    assert StubGenericAgent.last_call == (question, messages)
    assert StubBuildingAgent.last_call is None


def test_failed_identity_recovery_preserves_corrected_advisory_history(monkeypatch):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', 'generic')
    question = 'Vilken budget och vilka begränsningar utgår du från?'
    messages = [
        {'role': 'user', 'content': 'Vi har ojämn värme och en budget på 50 000 kr.'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'Börja med driftkontroller.'},
        {'role': 'user', 'content': 'Vi vill undvika större renoveringar i år.'},
        {'role': 'user', 'content': 'Rättelse: budgeten är 20 000 kr, inte 50 000.'},
        {'role': 'assistant', 'content': 'Tack för rättelsen.'},
        {'role': 'user', 'content': question},
    ]
    response, updated = module.AgentRouter().route_message(messages, question, {
        'address': 'inte 50', 'clarification': {'reason': 'building_not_found'},
    }, 'budget-after-failed-lookup')
    assert response['route'] == 'generic'
    assert StubGenericAgent.last_call == (question, messages)
    assert 'address' not in updated


def test_solar_sizing_limitations_do_not_force_address_clarification(monkeypatch):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', 'building_specific')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    question = 'Kan du ändå säga hur stor anläggning vi bör köpa?'
    messages = [
        {'role': 'user', 'content': 'Vi undersöker solceller för vår BRF.'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'Vilken takyta och elanvändning har ni?'},
        {'role': 'user', 'content': 'Vi känner inte till takytan eller fastighetens elanvändning.'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'Samla förbrukning och undersök taket först.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(messages, question, {}, 'solar-no-design-data')
    assert response['route'] == 'generic'
    assert StubGenericAgent.last_call == (question, messages)
    assert StubBuildingAgent.last_call is None


@pytest.mark.parametrize('question', [
    'Kan du ange exakt besparing och återbetalningstid?',
    'Kan du beräkna besparing och återbetalningstid?',
    'Kan du beräkna vår besparing och återbetalningstid?',
    'Can you give exact savings and payback?',
    'Can you calculate savings/payback?',
    'Can you calculate our savings and payback?',
])
@pytest.mark.parametrize('label', [None, 'building_specific', 'generic', 'conversational'])
def test_solar_financial_followup_retains_known_missing_inputs(monkeypatch, question, label):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', label)
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    monkeypatch.setattr(StubGenericAgent, 'next_response', 'En exakt beräkning kräver takyta, elanvändning och kostnadsantaganden.')
    messages = [
        {'role': 'user', 'content': 'Vi vill undersöka solceller för vår BRF. Var börjar vi?'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'Ta fram takyta och elanvändning.'},
        {'role': 'user', 'content': 'Vi känner inte till takytan eller fastighetens elanvändning.'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'En pålitlig dimensionering kräver dessa uppgifter.'},
        {'role': 'user', 'content': 'Kan du ändå säga hur stor anläggning vi bör köpa?'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'Jag kan inte rekommendera en exakt storlek utan underlaget.'},
        {'role': 'user', 'content': question},
    ]
    response, updated = module.AgentRouter().route_message(messages, question, {}, 'f06-financial-followup')
    assert response['route'] == 'generic'
    assert response['classification'] == 'generic'
    assert StubGenericAgent.last_call == (question, messages)
    assert StubBuildingAgent.last_call is None
    assert not updated.get('clarification', {}).get('needed')


@pytest.mark.parametrize('question', [
    'Kan du ange exakt besparing och återbetalningstid för 01-80-SKYTTEN2-2?',
    'Hämta vår registrerade elanvändning och beräkna vår besparing och återbetalningstid.',
    'Vi har nu 500 m2 tak och en offert på 400 000 kr. Beräkna vår besparing och återbetalningstid.',
    'Kan du ange exakt besparing och återbetalningstid? Vi har nu 500 m2 tak, 100 000 kWh per år och en offert på 400 000 kr.',
    'Calculate savings and payback using 500 m2 roof, 100000 kWh annual demand and 400000 SEK investment.',
])
@pytest.mark.parametrize('label', [None, 'building_specific', 'generic'])
def test_financial_followup_keeps_explicit_identity_records_or_new_calculation_inputs(monkeypatch, question, label):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', label)
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    messages = [
        {'role': 'user', 'content': 'Vi undersöker solceller för vår BRF men saknar takyta och elanvändning.'},
        {'role': 'assistant', 'classification': 'generic', 'content': 'Samla in underlaget först.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(messages, question, {}, 'f06-new-project-inputs')
    assert response['classification'] == 'building_specific'
    assert StubBuildingAgent.last_call[0] == question
    assert StubBuildingAgent.last_call[1] == messages


def test_financial_followup_preserves_already_selected_building(monkeypatch):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', 'building_specific')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    question = 'Kan du ange exakt besparing och återbetalningstid?'
    metadata = {'byggnadsid': '01-80-SKYTTEN2-2', 'building_identity_check': {'status': 'passed'}}
    messages = [
        {'role': 'user', 'content': 'Vi undersöker solceller för Nimrodsgatan 7, Stockholm.'},
        {'role': 'assistant', 'classification': 'building_specific', 'content': 'Byggnaden är identifierad, men takytan saknas.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(messages, question, metadata, 'f06-selected-building')
    assert response['classification'] == 'building_specific'
    assert StubBuildingAgent.last_call[0:2] == (question, messages)
    assert StubBuildingAgent.last_call[2]['byggnadsid'] == '01-80-SKYTTEN2-2'


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


# These include the exact false names saved by the live evaluation. They must
# remain invalid when only the extracted fragment survives in session metadata.
AUDITED_TASK1_ADVICE_QUESTIONS = [
    ('SV_13', 'En leverantör säger att vår BRF alltid tjänar på att byta från fjärrvärme till bergvärme. Hur kan vi bedöma det på ett sakligt sätt?'),
    ('SV_16', 'Vad kan en BRF undersöka för att minska energianvändningen i tvättstuga, trapphus och källare utan stora investeringar?'),
    ('SV_24', 'Vilka bidrag kan vår BRF söka för solceller just nu? Kan du lova att vi får stöd?'),
    ('SV_27', 'De översta lägenheterna i vår BRF blir väldigt varma på sommaren. Vilka uppgifter bör vi samla innan vi bestämmer oss för kylning?'),
    ('EKR_GEN_019', 'How does my electricity use affect my electricity price?'),
]


INVALID_BRF_FRAGMENTS = [
    'board wants energy advice',
    'överväger solceller',
    'funderar på bergvärme',
    'has district heating and high bills',
    'alltid tjänar på att byta från fjärrvärme till bergvärme',
    'undersöka för att minska energianvändningen i tvättstuga',
    'söka för solceller just nu',
    'blir väldigt varma på sommaren',
    'board prioritize energy measures when the budget',
    'replace the windows to save energy',
    'reduce its energy bills',
    'name',
    'generic',
    'unknown',
    'unnamed',
    'forgotten',
    'whose name I forgot',
    'name is unknown',
    'vars namn jag har glömt',
    'okänd',
    'namnlös',
]


def test_cited_brf_handbook_does_not_select_a_building_for_general_followup(monkeypatch):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', 'building_specific')
    monkeypatch.setattr(StubGenericAgent, 'next_response', 'Coordinate the roof and solar planning.')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    question = 'We are replacing our roof next year, and the board wants to know what to coordinate.'
    messages = [
        {'role': 'user', 'content': 'Could solar panels help a BRF reduce its energy bills?'},
        {'role': 'assistant', 'classification': 'generic',
         'content': 'Solar panels can help. Which roof should you use? See BRF Energieffektiv 2015 for general guidance.'},
        {'role': 'user', 'content': question},
    ]
    response, metadata = module.AgentRouter().route_message(messages, question, {}, 'handbook-followup')
    assert response['classification'] == 'generic'
    assert response['route'] == 'generic'
    assert StubBuildingAgent.last_call is None
    assert not metadata.get('brf_name')
    assert not metadata.get('clarification', {}).get('needed')


@pytest.mark.parametrize('sources', [['SQL database'], 'SQL database'])
@pytest.mark.parametrize('prior_reason', [None, 'incomplete_question', 'missing_building_data'])
def test_recorded_heating_fact_requests_identity_before_open_clarification(sources, prior_reason):
    module = import_building_flow_graph_module()
    question = "What heating system do we have? I don't remember the BRF name."
    state = {
        'last_message': question,
        'messages': [{'role': 'user', 'content': question}],
        'metadata': {},
        'context': {
            'intent_list': ['SQL database'], 'ambiguous': True, 'ambigious': True,
            'request_parts': [{'question': 'What heating system do we have?', 'kind': 'fact', 'sources': sources}],
        },
    }
    if prior_reason:
        state['metadata']['clarification'] = {
            'needed': True, 'reason': prior_reason,
            'question_asked': 'Can you share a photo of the heating equipment?',
        }
    assert module.route_after_ambiguity(state) == 'request_address'
    update = module.request_address_node(state)
    assert 'address' in update['final_response'].lower()
    assert update['metadata']['clarification']['reason'] == 'missing_address'
    assert update['metadata']['pending_building_request']['question'] == question
    # understand_context_node normalizes the supplied ID into the SQL filter
    # before this gate runs; use the same state here.
    state['metadata'] = {'building_id_from_user': '01-80-SKYTTEN2-2', 'byggnadsid': '01-80-SKYTTEN2-2'}
    assert module.route_after_ambiguity(state) == 'maintain_history'


@pytest.mark.parametrize('question', [
    'BRF board wants energy advice.',
    'Vår BRF överväger solceller. Vad behöver vi undersöka?',
    'Vår BRF funderar på bergvärme.',
    'BRF has district heating and high bills.',
    'Should our BRF replace the windows to save energy?',
    'Could solar panels help a BRF reduce its energy bills?',
    'Our BRF whose name I forgot needs energy advice.',
    'Vår BRF vars namn jag har glömt behöver energiråd.',
    "What heating system do we have? I don't remember the BRF name.",
    'not in BRF X',
    'inte i BRF X',
    'not our BRF X',
] + [f'BRF {fragment}' for fragment in INVALID_BRF_FRAGMENTS]
  + [pytest.param(question, id=case_id) for case_id, question in AUDITED_TASK1_ADVICE_QUESTIONS])
def test_brf_descriptions_are_not_names_in_either_extraction_wrapper(question):
    router_module = import_agent_router_module()
    graph_module = import_building_flow_graph_module()

    assert router_module._extract_concrete_brf_name(question) is None
    assert graph_module._extract_brf_name_from_text(question) is None


@pytest.mark.parametrize('question,expected_name', [
    ('I represent brf friskytten.', 'friskytten'),
    ('BRF Åsängens Äppelgård Östra.', 'Åsängens Äppelgård Östra'),
    ('BRF Solgläntan 1.', 'Solgläntan 1'),
    ('BRF Sjöstaden 12.', 'Sjöstaden 12'),
    ('BRF 123', '123'),
    ('BRF S:t Erik', 'S:t Erik'),
    ('Bostadsrättsföreningen Ängsbackens Övre Gård.', 'Ängsbackens Övre Gård'),
    ('BRF Åsängens Äppelgård i Stockholm.', 'Åsängens Äppelgård i Stockholm'),
    ('BRF friskytten in Stockholm.', 'friskytten in Stockholm'),
    ('Not BRF X; we are BRF Solgläntan 1.', 'Solgläntan 1'),
    ('I forgot the BRF name but BRF Solgläntan 1', 'Solgläntan 1'),
])
def test_concrete_brf_names_keep_legal_spelling_in_either_extraction_wrapper(
    question, expected_name,
):
    router_module = import_agent_router_module()
    graph_module = import_building_flow_graph_module()

    assert router_module._extract_concrete_brf_name(question) == expected_name
    assert graph_module._extract_brf_name_from_text(question) == expected_name
    assert router_module._clean_brf_name(expected_name) == expected_name
    assert graph_module._clean_brf_name(expected_name) == expected_name
    assert graph_module._metadata_brf_name({'brf_name': expected_name}) == expected_name


@pytest.mark.parametrize('fragment', INVALID_BRF_FRAGMENTS)
def test_saved_descriptions_are_rejected_by_both_name_normalizers(fragment):
    router_module = import_agent_router_module()
    graph_module = import_building_flow_graph_module()

    assert router_module._clean_brf_name(fragment) is None
    assert graph_module._clean_brf_name(fragment) is None
    assert graph_module._metadata_brf_name({'brf_name': fragment}) is None
    assert graph_module._metadata_brf_name({
        'brf_resolution': {'brf_name': fragment},
    }) is None
    assert graph_module._metadata_brf_name({
        'pending_brf_resolution': {'brf_name': fragment},
    }) is None


@pytest.mark.parametrize('question', [
    'Our BRF board wants energy advice. What should we prioritize with a limited budget?',
    'Vår BRF överväger solceller. Vad bör styrelsen undersöka först?',
    'Vår BRF funderar på bergvärme. Vilka saker bör vi överväga?',
    'How should a BRF board prioritize energy measures when the budget is tight?',
    'Should our BRF replace the windows to save energy?',
    'Could solar panels help a BRF reduce its energy bills?',
    'What should a BRF board prioritize to improve ventilation?',
    'What is ventilation, and how can we improve it?',
    'Vår BRF har fjärrvärme och höga kostnader. Hur kan vi spara energi?',
    'Our BRF whose name I forgot needs energy advice. What should the board consider?',
] + [pytest.param(question, id=case_id) for case_id, question in AUDITED_TASK1_ADVICE_QUESTIONS])
@pytest.mark.parametrize('after_failed_lookup', [False, True], ids=['new-session', 'failed-name'])
def test_general_board_advice_overrides_building_classifier_without_identity(
    monkeypatch, question, after_failed_lookup,
):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', 'building_specific')
    monkeypatch.setattr(StubGenericAgent, 'next_response', 'General energy advice.')
    monkeypatch.setattr(StubGenericAgent, 'last_call', None)
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)

    def unexpected_lookup(*args, **kwargs):
        pytest.fail('General advice entered the building lookup flow')

    monkeypatch.setattr(StubBuildingAgent, 'handle_building_query', unexpected_lookup)
    messages = []
    metadata = {}
    if after_failed_lookup:
        fake_name = 'board prioritize energy measures when the budget'
        messages = [
            {'role': 'user', 'content': 'How should a BRF board prioritize energy measures when the budget is tight?'},
            {'role': 'assistant', 'classification': 'building_specific',
             'content': f'I could not find building addresses for BRF {fake_name}. Please share the full street address.'},
        ]
        metadata = {
            'brf_name': fake_name,
            'brf_resolution': {'status': 'not_found', 'brf_name': fake_name},
            'clarification': {'needed': True, 'reason': 'brf_not_found'},
        }
    messages.append({'role': 'user', 'content': question})

    response, updated_metadata = module.AgentRouter().route_message(
        messages, question, metadata, 'brf-general-advice',
    )

    assert response['classification'] == 'generic'
    assert response['route'] == 'generic'
    assert response['content'] == 'General energy advice.'
    assert StubGenericAgent.last_call[0] == question
    assert StubBuildingAgent.last_call is None
    assert not updated_metadata.get('brf_name')
    assert not updated_metadata.get('clarification', {}).get('needed')


@pytest.mark.parametrize('question', [
    'What heating system does our building have?',
    'Do we have district heating?',
    'Which heating system do we have, and what should we prioritize?',
    pytest.param("What heating system do we have? I don't remember the BRF name.",
                 id='READINESS_A06_NO_BRF_NAME_HEATING_SYSTEM'),
    'How much district heating do we use, and how can we reduce costs?',
    'What energy performance did our building achieve, and how can we reduce heating costs?',
    'Vad är vår energianvändning?',
    'What is our ventilation and how can we improve it?',
])
def test_unidentified_heating_fact_still_uses_building_flow(monkeypatch, question):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', 'generic')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)

    def unexpected_generic(*args, **kwargs):
        pytest.fail('An actual building heating fact was treated as general advice')

    monkeypatch.setattr(StubGenericAgent, 'handle_generic_input', unexpected_generic)
    response, _ = module.AgentRouter().route_message(
        [{'role': 'user', 'content': question}], question, {}, 'brf-heating-fact',
    )

    assert response['classification'] == 'building_specific'
    assert response['content'] == 'building answer'
    assert StubBuildingAgent.last_call[0] == question


@pytest.mark.parametrize('failed_name_lookup', [False, True], ids=['selected-building', 'failed-name-with-selected-building'])
def test_forgotten_brf_name_keeps_verified_selected_building(monkeypatch, failed_name_lookup):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', 'generic')
    captured = {}

    def building_answer(self, last_message, messages, metadata, thread_id):
        captured.update(metadata)
        return {
            'content': 'building answer', 'agent_answered': 'building', 'route': 'combined',
        }, dict(metadata)

    def unexpected_generic(*args, **kwargs):
        pytest.fail('Forgetting the BRF name discarded an already verified building')

    monkeypatch.setattr(StubBuildingAgent, 'handle_building_query', building_answer)
    monkeypatch.setattr(StubGenericAgent, 'handle_generic_input', unexpected_generic)
    building_id = '01-80-HAELSAN10-2'
    recorded_addresses = ['Tulegatan 5A, Stockholm', 'Rådmansgatan 31, Stockholm']
    metadata = {
        'address': 'Tulegatan 5A',
        'byggnadsid': building_id,
        'building_id': building_id,
        'selected_brf_building_id': building_id,
        'selected_brf_addresses': recorded_addresses,
        'selected_brf_lookup_address': 'Tulegatan 5A',
        'building_identity_check': {'status': 'passed'},
        'brf_name': 'Solgläntan 1',
        'brf_resolution': {'status': 'resolved_by_user_selection', 'brf_name': 'Solgläntan 1'},
    }
    if failed_name_lookup:
        metadata.update({
            'brf_name': 'name',
            'brf_resolution': {'status': 'not_found', 'brf_name': 'name'},
            'clarification': {'needed': True, 'reason': 'brf_not_found'},
        })
    question = "What heating system do we have? I don't remember the BRF name."
    messages = [
        {'role': 'user', 'content': 'The selected building is Tulegatan 5A in Stockholm.'},
        {'role': 'assistant', 'classification': 'building_specific',
         'content': f'The verified building ID is {building_id}.'},
        {'role': 'user', 'content': question},
    ]

    response, updated_metadata = module.AgentRouter().route_message(
        messages, question, metadata, 'forgotten-name-selected-building',
    )

    assert response['classification'] == 'building_specific'
    assert response['content'] == 'building answer'
    for value in (captured, updated_metadata):
        assert value['building_id'] == building_id
        assert value['byggnadsid'] == building_id
        assert value['selected_brf_building_id'] == building_id
        assert value['selected_brf_addresses'] == recorded_addresses
        assert value['selected_brf_lookup_address'] == 'Tulegatan 5A'
        assert value['address'] == 'Tulegatan 5A'
        assert value['building_identity_check']['status'] == 'passed'


@pytest.mark.parametrize('fragment', INVALID_BRF_FRAGMENTS)
@pytest.mark.parametrize('origin', ['current-message', 'metadata', 'previous-message'])
def test_descriptive_brf_fragments_never_call_address_lookup(monkeypatch, fragment, origin):
    module = import_building_flow_graph_module()

    def unexpected_lookup(name):
        pytest.fail(f'Descriptive BRF fragment was sent to address lookup: {name}')

    monkeypatch.setattr(module, '_lookup_brf_addresses', unexpected_lookup)
    question = 'What measures would you recommend for our building?'
    assert module._looks_like_building_ecm_advice(question)
    metadata = {}
    messages = []
    if origin == 'current-message':
        question = f'BRF {fragment}'
    elif origin == 'metadata':
        metadata['brf_name'] = fragment
        assert module._metadata_brf_name(metadata) is None
    else:
        messages = [
            {'role': 'user', 'content': f'BRF {fragment}'},
            {'role': 'assistant', 'content': 'Let us discuss general energy measures.'},
        ]
    messages.append({'role': 'user', 'content': question})
    state = {
        'last_message': question,
        'messages': messages,
        'context': {'intent_list': ['SQL database', 'vector database']},
        'metadata': metadata,
    }

    update = module.brf_resolution_node(state)

    assert not update.get('metadata', {}).get('clarification', {}).get('needed')
    assert update.get('metadata', {}).get('brf_resolution', {}).get('status') not in {
        'not_found', 'lookup_error', 'needs_user_selection',
    }


@pytest.mark.parametrize('invalid_output', [
    None, '', 'generic | building_specific', 'This should be draft_energy_report',
    'expert_handoff because the situation is complex',
])
@pytest.mark.parametrize('topic,assistant_reply,constraint', [
    (
        'We have high heating bills. What energy measures should we consider?',
        'Start with heating controls and radiator balancing. What constraints do you have?',
        'The building is from the 60s and has district heating.',
    ),
    (
        'Could solar panels help our BRF reduce energy costs?',
        'Consider roof work, available area, and the budget. What is already planned?',
        'We are replacing the roof next year and have a tight budget.',
    ),
    (
        'Can we install geothermal heating?',
        'Ground conditions and space affect feasibility. Tell me about the site.',
        "We're a multi-family BRF, and the courtyard is small.",
    ),
    (
        'Would another drying cabinet help our laundry room?',
        'Check the current equipment and actual drying needs. What prompted the proposal?',
        'People need two sessions because clothes remain damp.',
    ),
])
def test_invalid_classification_keeps_substantive_advisory_constraints(monkeypatch, invalid_output, topic, assistant_reply, constraint):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', invalid_output)
    monkeypatch.setattr(StubGenericAgent, 'next_response', 'Advice that uses the supplied constraints.')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    messages = [
        {'role': 'user', 'content': topic},
        {'role': 'assistant', 'classification': 'generic', 'content': assistant_reply},
        {'role': 'user', 'content': constraint},
    ]
    response, metadata = module.AgentRouter().route_message(
        messages, constraint, {}, 'invalid-constraint-classification',
    )

    assert response['classification'] == 'generic'
    assert response['route'] == 'generic'
    assert response['content'] == 'Advice that uses the supplied constraints.'
    assert StubGenericAgent.last_call == (constraint, messages)
    assert StubBuildingAgent.last_call is None
    assert not metadata.get('clarification', {}).get('needed')


@pytest.mark.parametrize('prior_label', [
    'draft_energy_report', 'expert_handoff', 'cluster',
    'generic | building_specific', 'building_specific because the user has a building',
])
@pytest.mark.parametrize('question', [
    'What heating system does our building have?',
    'What is our energy class?',
    'Vilken energiklass har vår byggnad?',
    'What heating system do we have, and what should we prioritize?',
    'Give me information about Ringvägen 10, Stockholm.',
])
def test_invalid_classifier_recovery_uses_current_building_request(monkeypatch, prior_label, question):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', None)
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    messages = [
        {'role': 'user', 'content': 'Earlier topic.'},
        {'role': 'assistant', 'classification': prior_label, 'content': 'Earlier answer.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(
        messages, question, {}, 'current-building-request-recovery',
    )

    assert response['classification'] == 'building_specific'
    assert response['content'] == 'building answer'
    assert StubBuildingAgent.last_call[0] == question


@pytest.mark.parametrize('prior_label', [
    'draft_energy_report', 'expert_handoff', 'cluster',
    'building_specific because a building was discussed', 'generic | building_specific',
])
def test_invalid_history_label_does_not_create_building_context(monkeypatch, prior_label):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', None)
    monkeypatch.setattr(StubGenericAgent, 'next_response', 'Prioritize low cost energy checks first.')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    question = 'What should we do first?'
    messages = [
        {'role': 'user', 'content': 'We have high heating bills. How can we reduce energy costs?'},
        {'role': 'assistant', 'classification': prior_label,
         'content': 'General energy measures include heating controls and balancing.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(
        messages, question, {}, 'invalid-history-advisory-context',
    )

    assert response['classification'] == 'generic'
    assert StubBuildingAgent.last_call is None
    assert StubGenericAgent.last_call == (question, messages)


@pytest.mark.parametrize('question', [
    'How does a heat pump work?',
    'Also, how does a heat pump work?',
    'What is the difference between district heating and a heat pump?',
    'And what is the difference between district heating and a heat pump?',
])
def test_invalid_classifier_general_energy_topic_does_not_inherit_selected_building(monkeypatch, question):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', None)
    monkeypatch.setattr(StubGenericAgent, 'next_response', 'General explanation of heating technology.')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    messages = [
        {'role': 'user', 'content': 'What is the energy class of Tulegatan 5A, Stockholm?'},
        {'role': 'assistant', 'classification': 'building_specific',
         'content': 'The selected building ID is 01-80-HAELSAN10-2.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(
        messages, question,
        {'address': 'Tulegatan 5A', 'byggnadsid': '01-80-HAELSAN10-2',
         'building_identity_check': {'status': 'passed'}},
        'invalid-classifier-new-heating-topic',
    )

    assert response['classification'] == 'generic'
    assert response['content'] == 'General explanation of heating technology.'
    assert StubBuildingAgent.last_call is None


@pytest.mark.parametrize('question', [
    'And what about its ventilation?',
    'What measures should we prioritize for that building?',
    'Please explain why that building has energy class D.',
])
def test_invalid_classifier_preserves_actual_selected_building_followup(monkeypatch, question):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', None)
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    messages = [
        {'role': 'user', 'content': 'What is the energy class of Tulegatan 5A, Stockholm?'},
        {'role': 'assistant', 'classification': 'building_specific',
         'content': 'The selected building ID is 01-80-HAELSAN10-2.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(
        messages, question,
        {'address': 'Tulegatan 5A', 'byggnadsid': '01-80-HAELSAN10-2',
         'building_identity_check': {'status': 'passed'}},
        'invalid-classifier-retained-building',
    )

    assert response['classification'] == 'building_specific'
    assert StubBuildingAgent.last_call[0] == question


@pytest.mark.parametrize('invalid_output', [
    None, '', 'generic\nconversational', 'The correct route is generic.',
])
def test_invalid_classifier_preserves_exact_advisory03_measurement_reply(monkeypatch, invalid_output):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', invalid_output)
    monkeypatch.setattr(StubGenericAgent, 'next_response', 'Collect indoor temperature observations first.')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    question = 'We have not collected any temperature measurements yet.'
    messages = [
        {'role': 'user', 'content': 'We want to reduce heating costs in our BRF without major renovations this year.'},
        {'role': 'assistant', 'classification': 'generic',
         'content': 'Start with low cost heating checks. Which heating system do you have?'},
        {'role': 'user', 'content': 'We have district heating.'},
        {'role': 'assistant', 'classification': 'generic',
         'content': 'Review heating controls and distribution. Have residents reported uneven temperatures?'},
        {'role': 'user', 'content': 'Some residents say their flats are too warm.'},
        {'role': 'assistant', 'classification': 'generic',
         'content': 'Treat this as reported overheating, not a verified fault. What indoor temperatures have you measured?'},
        {'role': 'user', 'content': question},
    ]
    response, metadata = module.AgentRouter().route_message(
        messages, question, {}, 'exact-advisory03-measurement-recovery',
    )

    assert response['classification'] == 'generic'
    assert response['route'] == 'generic'
    assert response['content'] == 'Collect indoor temperature observations first.'
    assert StubGenericAgent.last_call == (question, messages)
    assert StubBuildingAgent.last_call is None
    assert not metadata.get('clarification', {}).get('needed')


@pytest.mark.parametrize('question', [
    'What about its maintenance?',
    'And what should we check first?',
])
def test_invalid_classifier_current_generic_topic_wins_over_older_selected_building(monkeypatch, question):
    module = import_agent_router_module()
    monkeypatch.setattr(StubRouterAgent, 'next_classification', None)
    monkeypatch.setattr(StubGenericAgent, 'next_response', 'General heat pump maintenance guidance.')
    monkeypatch.setattr(StubBuildingAgent, 'last_call', None)
    messages = [
        {'role': 'user', 'content': 'What energy class does Tulegatan 5A, Stockholm, have?'},
        {'role': 'assistant', 'classification': 'building_specific',
         'content': 'Selected building ID: 01-80-HAELSAN10-2.'},
        {'role': 'user', 'content': 'How does a heat pump work?'},
        {'role': 'assistant', 'classification': 'generic',
         'content': 'A heat pump uses electricity to transfer heat for heating a building.'},
        {'role': 'user', 'content': question},
    ]
    response, _ = module.AgentRouter().route_message(
        messages, question,
        {'address': 'Tulegatan 5A', 'byggnadsid': '01-80-HAELSAN10-2',
         'building_identity_check': {'status': 'passed'}},
        'current-generic-topic-with-old-building',
    )

    assert response['classification'] == 'generic'
    assert response['content'] == 'General heat pump maintenance guidance.'
    assert StubGenericAgent.last_call == (question, messages)
    assert StubBuildingAgent.last_call is None
