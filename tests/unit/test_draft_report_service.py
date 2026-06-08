import json

from tests.support import fresh_import, stub_module


class StubRedisClient:
    def __init__(self):
        self.calls = []

    def setex(self, key, ttl, value):
        self.calls.append((key, ttl, value))


class StubOpenAIResponseAgent:
    last_prompt = None

    def __init__(self, prompt_path):
        self.prompt_path = prompt_path

    def generate_response(self, prompt, message_list):
        type(self).last_prompt = prompt
        return "# Draft Energy Report\n\nGenerated content."


def test_generate_draft_report_stores_plain_text_artifact():
    redis_client = StubRedisClient()

    stub_module("redis", StrictRedis=lambda *args, **kwargs: redis_client)
    stub_module("src.agents.openai_agent", OpenAIResponseAgent=StubOpenAIResponseAgent)
    stub_module(
        "src.redis.redis_session_store",
        get_session_state=lambda thread_id: {
            "metadata": {"address": "Main Street 1", "building_id": "BUILDING-1"}
        },
    )

    module = fresh_import("src.services.draft_report_service")
    module._redis_client = redis_client

    response = module.generate_draft_report_response(
        thread_id="thread-1",
        messages=[{"role": "user", "content": "Create a draft report"}],
        metadata={"building_id": "BUILDING-1"},
    )

    assert response["classification"] == "draft_energy_report"
    assert response["downloadable_report"]["file_name"] == "BUILDING-1.txt"
    assert response["downloadable_report"]["mime_type"] == "text/plain; charset=utf-8"
    assert "text file" in response["content"]

    assert len(redis_client.calls) == 1
    key, ttl, raw_payload = redis_client.calls[0]
    payload = json.loads(raw_payload)

    assert key.startswith(f"{module.REPORT_KEY_PREFIX}:")
    assert ttl == module.REPORT_TTL_SECONDS
    assert payload["file_name"] == "BUILDING-1.txt"
    assert payload["mime_type"] == "text/plain; charset=utf-8"
    assert payload["content"].startswith("# Draft Energy Report")
    assert "content_base64" not in payload


def test_generate_draft_report_recovers_building_id_from_recent_messages():
    redis_client = StubRedisClient()

    stub_module("redis", StrictRedis=lambda *args, **kwargs: redis_client)
    stub_module("src.agents.openai_agent", OpenAIResponseAgent=StubOpenAIResponseAgent)
    stub_module(
        "src.redis.redis_session_store",
        get_session_state=lambda thread_id: {},
    )

    module = fresh_import("src.services.draft_report_service")
    module._redis_client = redis_client

    response = module.generate_draft_report_response(
        thread_id="thread-2",
        messages=[
            {"role": "user", "content": "What is the energy class of my building?"},
            {"role": "assistant", "content": "Can you please provide the building address?"},
            {"role": "user", "content": "Artemisgatan 13"},
            {
                "role": "assistant",
                "content": "Building ID: 01-80-SKYTTEN2-2\nThe latest energy performance certificate (EPC) reports an energy class of G.",
            },
            {"role": "user", "content": "can you give me the energy report ?"},
        ],
        metadata={},
    )

    assert response["downloadable_report"]["file_name"] == "01-80-SKYTTEN2-2.txt"
    assert "01-80-SKYTTEN2-2" in response["content"]

    assert len(redis_client.calls) == 1
    _, _, raw_payload = redis_client.calls[0]
    payload = json.loads(raw_payload)
    assert payload["file_name"] == "01-80-SKYTTEN2-2.txt"


def test_generate_draft_report_uses_address_filename_when_building_id_missing():
    redis_client = StubRedisClient()

    stub_module("redis", StrictRedis=lambda *args, **kwargs: redis_client)
    stub_module("src.agents.openai_agent", OpenAIResponseAgent=StubOpenAIResponseAgent)
    stub_module(
        "src.redis.redis_session_store",
        get_session_state=lambda thread_id: {
            "metadata": {"address": "Artemisgatan 13"}
        },
    )

    module = fresh_import("src.services.draft_report_service")
    module._redis_client = redis_client

    response = module.generate_draft_report_response(
        thread_id="thread-3",
        messages=[{"role": "user", "content": "Create a draft report"}],
        metadata={},
    )

    assert response["downloadable_report"]["file_name"] == "Artemisgatan_13.txt"

    _, _, raw_payload = redis_client.calls[0]
    payload = json.loads(raw_payload)
    assert payload["file_name"] == "Artemisgatan_13.txt"


def test_generate_draft_report_uses_swedish_for_swedish_request():
    redis_client = StubRedisClient()
    StubOpenAIResponseAgent.last_prompt = None

    stub_module("redis", StrictRedis=lambda *args, **kwargs: redis_client)
    stub_module("src.agents.openai_agent", OpenAIResponseAgent=StubOpenAIResponseAgent)
    stub_module(
        "src.redis.redis_session_store",
        get_session_state=lambda thread_id: {
            "metadata": {"address": "Ringvägen 10", "building_id": "BUILDING-1"}
        },
    )

    module = fresh_import("src.services.draft_report_service")
    module._redis_client = redis_client

    response = module.generate_draft_report_response(
        thread_id="thread-4",
        messages=[{"role": "user", "content": "Kan du skapa en energirapport?"}],
        metadata={"building_id": "BUILDING-1"},
    )

    prompt_payload = json.loads(StubOpenAIResponseAgent.last_prompt)
    assert prompt_payload["response_language"] == "sv"
    assert "Jag skapade ett textfilutkast" in response["content"]
