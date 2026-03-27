import json

from tests.support import fresh_import, stub_module


class StubRedisClient:
    def __init__(self):
        self.calls = []

    def setex(self, key, ttl, value):
        self.calls.append((key, ttl, value))


class StubOpenAIResponseAgent:
    def __init__(self, prompt_path):
        self.prompt_path = prompt_path

    def generate_response(self, prompt, message_list):
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
