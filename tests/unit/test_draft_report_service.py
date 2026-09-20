import json
from datetime import datetime
import pytest

from tests.support import fresh_import, stub_module


class StubRedisClient:
    def __init__(self):
        self.calls = []

    def setex(self, key, ttl, value):
        self.calls.append((key, ttl, value))
        return True


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


def report_module(monkeypatch, model_response="# Draft Energy Report\n\nGenerated content."):
    redis_client = StubRedisClient()
    stub_module("redis", StrictRedis=lambda *args, **kwargs: redis_client)
    stub_module("src.agents.openai_agent", OpenAIResponseAgent=StubOpenAIResponseAgent)
    stub_module("src.redis.redis_session_store", get_session_state=lambda thread_id: {})
    module = fresh_import("src.services.draft_report_service")
    monkeypatch.setattr(StubOpenAIResponseAgent, "generate_response", lambda *args, **kwargs: model_response)
    return module, redis_client


@pytest.mark.parametrize("model_response", [None, "", "  Error: unavailable", "No content returned", "No content returned.", "No content returned from the model."])
def test_model_failure_creates_explicitly_labelled_basic_summary(monkeypatch, model_response):
    module, redis_client = report_module(monkeypatch, model_response)
    response = module.generate_draft_report_response(
        "thread-fallback", [{"role": "user", "content": "Our objective is less uneven heating"}],
        {"building_id": "BUILDING-1"},
    )
    assert response["report_status"] == "fallback"
    assert "basic session summary" in response["content"]
    assert response["downloadable_report"]["file_name"] == "BUILDING-1.txt"
    artifact = json.loads(redis_client.calls[0][2])
    assert "Our objective is less uneven heating" in artifact["content"]
    assert "No new measures or priorities" in artifact["content"]


def test_storage_failure_returns_copyable_draft_without_download_claim(monkeypatch):
    module, redis_client = report_module(monkeypatch)
    def unavailable(*args, **kwargs):
        raise ConnectionError("redis is unavailable")
    monkeypatch.setattr(redis_client, "setex", unavailable)
    response = module.generate_draft_report_response("thread-storage", [], {"building_id": "BUILDING-1"})
    assert response["report_status"] == "storage_failed"
    assert "downloadable_report" not in response
    assert "could not create a downloadable report" in response["content"]
    assert "Generated content." in response["content"]


def test_unacknowledged_storage_does_not_advertise_a_download(monkeypatch):
    module, redis_client = report_module(monkeypatch)
    monkeypatch.setattr(redis_client, "setex", lambda *args, **kwargs: False)
    response = module.generate_draft_report_response("thread-unacknowledged", [], {})
    assert response["report_status"] == "storage_failed"
    assert "downloadable_report" not in response
    assert "Generated content." in response["content"]


def test_model_exception_and_storage_failure_preserve_copyable_user_context(monkeypatch):
    module, redis_client = report_module(monkeypatch)

    def model_unavailable(*args, **kwargs):
        raise RuntimeError("model unavailable")

    def storage_unavailable(*args, **kwargs):
        raise ConnectionError("storage unavailable")

    monkeypatch.setattr(StubOpenAIResponseAgent, "generate_response", model_unavailable)
    monkeypatch.setattr(redis_client, "setex", storage_unavailable)
    response = module.generate_draft_report_response(
        "thread-both-unavailable",
        [{"role": "user", "content": "Improve comfort without major renovations."}], {},
    )
    assert response["report_status"] == "storage_failed"
    assert "downloadable_report" not in response
    assert "basic session summary" in response["content"]
    assert "Improve comfort without major renovations." in response["content"]


def test_history_read_failure_does_not_discard_available_context(monkeypatch):
    module, redis_client = report_module(monkeypatch)
    def unavailable(*args):
        raise ConnectionError("session history is unavailable")
    monkeypatch.setattr(module, "get_session_state", unavailable)
    response = module.generate_draft_report_response("thread-session", [], {"building_id": "BUILDING-1"})
    assert response["downloadable_report"]["file_name"] == "BUILDING-1.txt"
    assert "history could not be loaded" in response["content"]
    assert "stored building session history could not be loaded" in json.loads(redis_client.calls[0][2])["content"]


def test_download_expiry_uses_configured_ttl(monkeypatch):
    module, redis_client = report_module(monkeypatch)
    monkeypatch.setattr(module, "REPORT_TTL_SECONDS", 300)
    response = module.generate_draft_report_response("thread-expiry", [], {"building_id": "BUILDING-1"})
    assert redis_client.calls[0][1] == 300
    assert response["downloadable_report"]["expires_at"] in response["content"]
    assert "next 30 minutes" not in response["content"]
    stored = json.loads(redis_client.calls[0][2])
    expiry = datetime.fromisoformat(stored["expires_at"])
    created = datetime.fromisoformat(stored["created_at"])
    assert (expiry - created).total_seconds() == 300
    assert response["downloadable_report"]["expires_at"] == stored["expires_at"]


def test_report_keeps_original_objectives_beyond_last_eight_messages(monkeypatch):
    module, _ = report_module(monkeypatch)
    messages = [{"role": "user", "content": "Avoid major disruption; comfort is the goal"}]
    messages.extend({"role": "user", "content": f"Follow-up {index}"} for index in range(12))
    prompt = module._build_report_prompt(
        thread_id="thread-objective", building_id="BUILDING-1", messages=messages,
        metadata={}, session_snapshot={}, building_facts={},
    )
    assert "Avoid major disruption; comfort is the goal" in prompt


def test_current_selected_building_outranks_old_canonical_id(monkeypatch):
    module, _ = report_module(monkeypatch)
    snapshot = module._normalize_session_state({"metadata": {"byggnadsid": "OLD-BUILDING"}})
    assert module._extract_building_id({"building_id": "CURRENT-BUILDING"}, snapshot) == "CURRENT-BUILDING"
    assert module._extract_building_id({
        "selected_brf_building_id": "CURRENT-BUILDING", "aggregated_data": {"byggnadsid": "OLD-BUILDING"},
    }, snapshot) == "CURRENT-BUILDING"


def test_report_excludes_known_other_building_snapshots(monkeypatch):
    module, redis_client = report_module(monkeypatch)
    monkeypatch.setattr(module, "get_session_state", lambda thread_id: [
        {"metadata": {"byggnadsid": "OLD-BUILDING", "address": "Old Street 1"}, "building_information": {"old_only_fact": 999}},
        {"metadata": {"building_id": "CURRENT-BUILDING", "address": "Current Street 2"}, "building_information": {"energy_class": "D"}},
    ])
    captured = {}
    def generate(agent, prompt, message_list):
        captured.update(json.loads(prompt))
        return "# Draft Energy Report"
    monkeypatch.setattr(StubOpenAIResponseAgent, "generate_response", generate)
    module.generate_draft_report_response("thread-current", [], {
        "building_id": "CURRENT-BUILDING", "building_information": {"energy_class": "C"},
    })
    assert captured["building_facts"]["building_information"]["energy_class"] == "C"
    assert captured["session_state"]["excluded_other_building_ids"] == ["OLD-BUILDING"]
    assert "old_only_fact" not in json.dumps(captured["building_facts"])
    assert "old_only_fact" not in json.loads(redis_client.calls[0][2])["content"]


def test_current_selection_does_not_inherit_an_old_buildings_address(monkeypatch):
    module, _ = report_module(monkeypatch)
    snapshot = module._normalize_session_state({"metadata": {"building_id": "OLD", "address": "Old Street 1"}})
    scoped = module._scope_session_snapshot(snapshot, "CURRENT")
    assert module._extract_address({"building_id": "CURRENT"}, scoped) == ""


def test_retained_current_snapshot_does_not_reintroduce_nested_previous_buildings(monkeypatch):
    module, _ = report_module(monkeypatch)
    previous = {
        "metadata": {"byggnadsid": "OLD-BUILDING"},
        "aggregated_data": {"energy": {"old_building_only_fact": 999}},
    }
    current = {
        "metadata": {"building_id": "CURRENT-BUILDING", "address": "Current Street 2"},
        "aggregated_data": {"energy": {"energy_class": "C"}},
        "session_state": [previous],
        "context": {"states": [previous], "ventilation": "mechanical exhaust"},
    }
    scoped = module._scope_session_snapshot(
        module._normalize_session_state([previous, current]), "CURRENT-BUILDING",
    )
    facts = module._collect_building_facts({"building_id": "CURRENT-BUILDING"}, scoped)
    assert "old_building_only_fact" not in json.dumps(scoped)
    assert "old_building_only_fact" not in json.dumps(facts)
    assert scoped["latest_state"]["aggregated_data"]["energy"]["energy_class"] == "C"
    assert scoped["latest_state"]["context"]["ventilation"] == "mechanical exhaust"
    assert scoped["latest_state"]["metadata"]["address"] == "Current Street 2"
    assert current["session_state"] == [previous]
