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
    'Skapa en kort rapport till styrelsen.',
    'Kan du sammanfatta samtalet i en rapport?',
    'Snälla skriv en energirapport från vår diskussion.',
    'Förbered ett styrelseunderlag om våra energifrågor.',
    'Kan du exportera diskussionen som en rapport?',
    'Skapa ett nedladdningsbart rapportutkast till styrelsen med underlaget, våra observationer, osäkerheter och nästa steg.',
    'Kan du skapa ett energirapportutkast?',
    'Exportera diskussionen som ett rapportutkast.',
    'Sammanställ rapportutkasten till styrelsen.',
    'Förbered styrelseunderlaget.',
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
    'Vad innehåller en energirapport?',
    'Skapa ingen rapport ännu.',
    'Kan du skapa en rapport? Nej, skapa inte rapporten.',
    'Skriv inte en energirapport.',
    'Hur skriver jag en rapport?',
    'Sammanfatta samtalet.',
    'Kan du sammanfatta vår befintliga rapport?',
    'Vad innehåller ett rapportutkast?',
    'Skapa inget rapportutkast ännu.',
    'Skriv inte ett energirapportutkast.',
    'Kan du skapa ett rapportutkast? Nej, skapa inte rapportutkastet.',
    'Kan du skapa ett rapportutkast? Nej, gör inte det.',
    'Sammanfatta våra tre nästa steg.',
    'Kan du sammanfatta vårt befintliga rapportutkast?',
    'Ladda ner vårt senaste rapportutkast.',
    'Sammanfatta rapportutkastet.',
    'Ladda ner rapportutkastet.',
    'Kan du sammanfatta energirapportutkastet?',
    'Visa rapportutkasten.',
])
def test_report_mentions_are_not_creation_requests(router, utterance):
    assert not router.wants_draft_report(utterance)


@pytest.mark.parametrize('utterance', [
    'Could you pass this to an energy advisor so we can discuss the next step?',
    'Please forward the conversation to a climate adviser.',
    'Can you contact an energy advisor for us?',
    'Skicka detta till en energirådgivare.',
    'Kan du kontakta en energi- och klimatrådgivare åt oss?',
    'Mejla samtalet till energirådgivaren.',
    'Vi vill prata med en klimatrådgivare.',
    'Vi behöver hjälp av en energirådgivare.',
])
def test_handoff_action_paraphrases(router, utterance):
    assert router.wants_expert_handoff(utterance)


@pytest.mark.parametrize('utterance', [
    'What would an energy advisor check first?',
    'How can I contact an energy advisor?',
    'Please explain what I should send to an energy advisor.',
    'Could you draft a message for an energy advisor?',
    'Vad gör en energirådgivare?',
    'Skicka inte detta till en energirådgivare.',
    'Kontakta ingen energirådgivare.',
    'Hur kan jag kontakta en energirådgivare?',
    'Kan du förklara vad jag ska skicka till en energirådgivare?',
    'Kan du skriva ett utkast till en energirådgivare?',
    'Jag behöver fler råd om vår uppvärmning.',
    'Vi behöver inte hjälp av en energirådgivare.',
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
@pytest.mark.parametrize('question', [
    'Can you make a short report from our discussion?',
    'Kan du sammanfatta samtalet i en rapport?',
    'Skapa ett nedladdningsbart rapportutkast till styrelsen med underlaget, våra observationer, osäkerheter och nästa steg.',
    'Kan du skapa ett energirapportutkast?',
])
def test_report_action_creates_artifact_with_existing_building_context(router, monkeypatch, pending_address, question):
    module, pipeline = configured_pipeline(router)
    redis = StubRedisClient()
    stub_module('redis', StrictRedis=lambda *args, **kwargs: redis)
    stub_module('src.redis.redis_session_store', get_session_state=lambda thread_id: {})
    stub_module('src.agents.openai_agent', OpenAIResponseAgent=StubOpenAIResponseAgent)
    reports = fresh_import('src.services.draft_report_service')
    monkeypatch.setattr(module, 'generate_draft_report_response', reports.generate_draft_report_response)
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


def test_exact_f12_compound_draft_request_dispatches_before_classifier(router, monkeypatch):
    module = import_agent_router_module()
    pipeline = module.AgentRouter()
    pipeline.router = router
    router.classify_question = lambda *args, **kwargs: pytest.fail('Explicit draft output should not need a classifier label')
    monkeypatch.setattr(pipeline.building, 'handle_building_query', lambda *args: pytest.fail('Draft request entered a building lookup'))
    question = 'Skapa ett nedladdningsbart rapportutkast till styrelsen med underlaget, våra observationer, osäkerheter och nästa steg.'
    messages = [
        {'role': 'user', 'content': 'Visa energiklass och värmesystem för Nimrodsgatan 7, Stockholm.'},
        {'role': 'assistant', 'classification': 'building_specific', 'content': 'Byggnadsid: 01-80-SKYTTEN2-2.'},
        {'role': 'user', 'content': 'Vi upplever ojämn värme och vill undvika större renoveringar i år.'},
        {'role': 'assistant', 'classification': 'building_specific', 'content': 'Undersök styrning och värmefördelning först.'},
        {'role': 'user', 'content': question},
    ]
    metadata = {
        'byggnadsid': '01-80-SKYTTEN2-2', 'address': 'Nimrodsgatan 7',
        'building_identity_check': {'status': 'passed', 'matched_building_id': '01-80-SKYTTEN2-2'},
    }
    calls = []

    def generate(thread_id, history, context):
        calls.append((thread_id, history, context))
        return {
            'role': 'assistant', 'content': 'Rapportutkastet är klart.',
            'classification': 'draft_energy_report', 'report_status': 'generated',
            'downloadable_report': {'report_id': 'f12-report'},
        }

    monkeypatch.setattr(module, 'generate_draft_report_response', generate)
    response, _ = pipeline.route_message(messages, question, metadata, 'exact-f12-draft')
    assert response['route'] == 'report_generation'
    assert response['downloadable_report']['report_id'] == 'f12-report'
    assert len(calls) == 1
    assert calls[0][0:2] == ('exact-f12-draft', messages)
    assert calls[0][2]['byggnadsid'] == '01-80-SKYTTEN2-2'
    assert calls[0][2]['building_identity_check'] == metadata['building_identity_check']


@pytest.mark.parametrize('question', [
    'Could you pass this to an energy advisor so we can discuss the next step?',
    'Skicka detta till en energirådgivare.',
    'Kan du kontakta en energi- och klimatrådgivare åt oss?',
])
def test_advisor_action_previews_without_sending_in_building_conversation(router, monkeypatch, question):
    module, pipeline = configured_pipeline(router)
    monkeypatch.setattr(module, 'send_expert_handoff_email', lambda *args: pytest.fail('Sent without confirmation'))
    messages = [
        {'role': 'assistant', 'classification': 'building_specific', 'content': 'The supply capacity is unknown.'},
        {'role': 'user', 'content': question},
    ]
    response, metadata = pipeline.route_message(messages, question, {'building_id': 'TEST-BUILDING-1'}, 'thread-preview')
    assert response['route'] == 'expert_handoff'
    assert metadata['expert_handoff_pending_confirmation'] is True
    assert not metadata.get('expert_handoff_sent')


@pytest.mark.parametrize('utterance', [
    'Jag vill att en energirådgivare får en sammanfattning av samtalet. Visa vad som skulle skickas.',
    'Visa en sammanfattning som kan skickas till en energirådgivare, med mottagaren tydligt angiven.',
    'Show a preview for an energy advisor, including the recipient.',
    'Can you show a summary to send to the energy advisor?',
    'Can I talk to an EKR expert about this building?',
])
def test_requested_advisor_preview_is_an_action_without_submission(router, utterance):
    assert router.wants_expert_handoff(utterance)
    assert not router.is_confirmation(utterance)


@pytest.mark.parametrize('utterance', [
    'This is complicated.', 'This is too hard.', 'I need more help with heating.',
    'How can I contact an expert?', 'How do I send this conversation to an expert?',
    'Show a summary of what an energy advisor does.',
    'Visa en sammanfattning av vad en energirådgivare gör.',
    "Don't send this conversation to an expert.",
    'Please do not contact an energy advisor.',
])
def test_advice_mentions_negation_and_complexity_do_not_request_handoff(router, utterance):
    assert not router.wants_expert_handoff(utterance)


@pytest.mark.parametrize('utterance', [
    'Jag vill att en energirådgivare inte får en sammanfattning av samtalet.',
    'Can you create a report? No, do not create it.',
    'Please generate a report. Actually, do not generate the report.',
])
def test_negative_action_clause_vetoes_initial_positive_request(router, utterance):
    assert not router.wants_expert_handoff(utterance)
    assert not router.wants_draft_report(utterance)


EXISTING_DOCUMENT_REQUESTS = [
    'Skriv det exakta citatet ur vår senaste OVK-rapport som visar att ventilationen är godkänd.',
    'Quote the paragraph in our energy declaration that proves our ventilation is FTX.',
    'Please give me our existing audit-report.',
    'Write the exact quotation from our uploaded inspection report.',
    'Please provide the contents of our latest OVK-report.',
    'Skriv innehållet i vår befintliga energi-rapport.',
    'Visa vår senaste OVK–rapport.',
    'Please download the existing energy report.',
    'Kan du sammanfatta vår befintliga OVK-rapport?',
]


@pytest.mark.parametrize('utterance', EXISTING_DOCUMENT_REQUESTS)
def test_source_document_requests_do_not_create_new_report(router, utterance):
    from src.pipeline.safety_analysis import requests_existing_document
    assert requests_existing_document(utterance)
    assert not router.wants_draft_report(utterance)


@pytest.mark.parametrize('utterance', [
    'Create a new energy report using our existing audit report as background.',
    'Please write a short report based on our latest OVK-report.',
    'Prepare a board briefing from the existing inspection report.',
    'Skapa en energirapport baserad på vår senaste OVK-rapport.',
    'Förbered ett styrelseunderlag med vår befintliga OVK-rapport som källa.',
    'Skriv en ny rapport med citat ur vår befintliga energideklaration.',
    'Skapa ett rapportutkast med citat ur vår befintliga energideklaration.',
    'Sammanställ ett energirapportutkast med vår senaste OVK-rapport som källa.',
    'Sammanfatta rapportutkastet till ett nytt styrelseunderlag.',
])
def test_new_report_output_is_distinguished_from_its_existing_sources(router, utterance):
    from src.pipeline.safety_analysis import requests_existing_document
    assert not requests_existing_document(utterance)
    assert router.wants_draft_report(utterance)


@pytest.mark.parametrize('utterance', EXISTING_DOCUMENT_REQUESTS)
def test_existing_document_flow_rejects_report_model_misclassification(router, monkeypatch, utterance):
    module, pipeline = configured_pipeline(router)
    router.classify_question = lambda *args, **kwargs: 'draft_energy_report'
    monkeypatch.setattr(module, 'generate_draft_report_response', lambda *args: pytest.fail('Created an unrelated artifact'))
    messages = [
        {'role': 'assistant', 'classification': 'building_specific', 'content': 'Selected building facts are available.'},
        {'role': 'user', 'content': utterance},
    ]
    response, _ = pipeline.route_message(
        messages, utterance, {'address': 'Tulegatan 5A', 'building_id': '01-80-HAELSAN10-2'}, 'source-document-chat',
    )
    assert response['classification'] == 'building_specific'
    assert response['route'] != 'report_generation'
    assert not response.get('downloadable_report')


@pytest.mark.parametrize('utterance', [
    "I'd like to talk to an energy advisor.", 'I would like to speak with an expert.',
    'Connect me to a human.', 'Please connect me to a person.',
    'Jag vill prata med en energirådgivare.', 'Koppla mig till en människa.',
    'Jag behöver mänsklig hjälp.',
])
def test_actual_human_help_requests_reach_preview_with_real_helpers(router, monkeypatch, utterance):
    assert router.wants_expert_handoff(utterance)
    module, pipeline = configured_pipeline(router)
    monkeypatch.setattr(module, 'send_expert_handoff_email', lambda *args: pytest.fail('Submitted without consent'))
    response, metadata = pipeline.route_message(
        [{'role': 'user', 'content': utterance}], utterance, {}, 'actual-human-request',
    )
    assert response['route'] == 'expert_handoff'
    assert metadata['expert_handoff_status'] == 'pending_confirmation'
    assert metadata['expert_handoff_pending_confirmation'] is True


@pytest.mark.parametrize('utterance', [
    "I'd like an energy report from our discussion.", 'I need a short board briefing.',
    'Please summarize this conversation as a report.', 'Turn our conversation into a report.',
    'Jag vill ha en energirapport.', 'Vi behöver ett styrelseunderlag.',
    'Jag vill ha ett rapportutkast.', 'Vi behöver ett kort energirapportutkast.',
    'Vi behöver ett nytt nedladdningsbart rapportutkast.',
])
def test_explicit_desired_report_outputs_are_recognized(router, utterance):
    assert router.wants_draft_report(utterance)


@pytest.mark.parametrize('model_label', ['expert_handoff', 'draft_energy_report'])
@pytest.mark.parametrize('utterance', [
    'This is complicated.', 'How can electricity use affect my electricity bill?',
    'What would an energy advisor check first?', 'Do not generate an energy report.',
])
def test_valid_model_action_label_cannot_initiate_unrequested_action(router, monkeypatch, model_label, utterance):
    module, pipeline = configured_pipeline(router)
    router.classify_question = lambda *args, **kwargs: model_label

    def unexpected_action(*args, **kwargs):
        pytest.fail('Model classification initiated an unrequested action')

    monkeypatch.setattr(module, 'generate_draft_report_response', unexpected_action)
    monkeypatch.setattr(module, 'prepare_expert_handoff', unexpected_action)
    monkeypatch.setattr(module, 'send_expert_handoff_email', unexpected_action)
    response, metadata = pipeline.route_message(
        [{'role': 'user', 'content': utterance}], utterance, {}, 'unsolicited-model-action',
    )
    assert response['route'] not in {'report_generation', 'expert_handoff'}
    assert not response.get('downloadable_report')
    assert not metadata.get('expert_handoff_pending_confirmation')


@pytest.mark.parametrize('model_label', ['generic', 'building_specific', 'draft_energy_report'])
def test_declaration_source_and_general_next_steps_retain_complete_request(router, monkeypatch, model_label):
    from tests.unit.test_agent_router import StubBuildingAgent
    module, pipeline = configured_pipeline(router)
    router.classify_question = lambda *args, **kwargs: model_label
    monkeypatch.setattr(module, 'generate_draft_report_response', lambda *args: pytest.fail('Invented declaration artifact'))
    question = 'Can you summarize the key points from our energy declaration and also explain what a BRF should do next?'
    messages = [{'role': 'user', 'content': question}]
    response, _ = pipeline.route_message(messages, question, {}, 'D03-preserved-document-question')
    assert response['classification'] == 'building_specific'
    assert StubBuildingAgent.last_call[0] == question
    assert StubBuildingAgent.last_call[1] == messages
    assert not StubBuildingAgent.last_call[2].get('building_id')
    assert not response.get('downloadable_report')
