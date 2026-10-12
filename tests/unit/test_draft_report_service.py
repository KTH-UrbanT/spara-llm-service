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

    def generate_response(self, prompt, message_list, *, language=None):
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


def test_report_links_are_filtered_before_the_artifact_is_stored(monkeypatch):
    model_response = "# Utkast\n\nLäs [påhittad rapport](https://invented.example/report.pdf)."
    module, redis_client = report_module(monkeypatch, model_response)
    response = module.generate_draft_report_response(
        "checked-report", [{"role": "user", "content": "Skapa ett rapportutkast"}], {},
    )
    artifact = json.loads(redis_client.calls[0][2])
    assert "invented.example" not in artifact["content"]
    assert "utelämnat länkar" in artifact["content"]
    assert response["report_status"] == "generated"
    assert response["downloadable_report"]["report_id"]


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
    assert "någon fil kunde inte skapas" in response["content"]
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
    assert "historiken kunde inte hämtas" in response["content"]
    assert "byggnadshistoriken kunde inte hämtas" in json.loads(redis_client.calls[0][2])["content"]


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
    def generate(agent, prompt, message_list, *, language=None):
        captured.update(json.loads(prompt))
        return "# Draft Energy Report"
    monkeypatch.setattr(StubOpenAIResponseAgent, "generate_response", generate)
    module.generate_draft_report_response("thread-current", [], {
        "building_id": "CURRENT-BUILDING", "building_information": {"energy_class": "C"},
    })
    assert captured["building_facts"]["building_information"]["energy_class"] == "C"
    assert captured["evidence_scope"]["excluded_other_building_ids"] == ["OLD-BUILDING"]
    assert "session_state" not in captured
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


@pytest.mark.parametrize("heading", [
    "## Appendix: Full Session Building Data", "## Bilaga: Byggnadsunderlag från samtalet",
    "### BILAGA – rådata", "# Appendix", "## 7. **Bilaga**", "#### Appendix: Records",
])
@pytest.mark.parametrize("language", ["en", "sv"])
def test_report_replaces_existing_appendix_with_exact_evidence(monkeypatch, heading, language):
    module, _ = report_module(monkeypatch)
    raw_facts = {"byggnadsid": "01-80-TEST1-1", "raw_code": "ElDirekt", "value": 137.1, "nested": [None, False]}
    report = "# Report\n\nKeep this narrative.\n\n" + heading + "\n```json\n{broken invented evidence}\n```\n"
    result = module._ensure_appendix(report, raw_facts, language)
    assert "Keep this narrative." in result
    assert "broken invented evidence" not in result
    canonical = "## Bilaga: Byggnadsunderlag från samtalet" if language == "sv" else "## Appendix: Full Session Building Data"
    assert result.count(canonical) == 1
    evidence = result.split(canonical, 1)[1].split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(evidence) == raw_facts


def test_report_removes_both_appendices_preserving_later_narrative(monkeypatch):
    module, _ = report_module(monkeypatch)
    report = "# Utkast\n\n## Appendix\nwrong\n### Subsection\nwrong too\n## Next steps\nKeep this.\n## Bilaga\nwrong again\n"
    result = module._ensure_appendix(report, {"raw": 7}, "sv")
    assert "wrong" not in result
    assert "## Next steps\nKeep this." in result
    assert "## Appendix" not in result
    assert result.count("## Bilaga") == 1


def test_appendix_heading_inside_code_is_not_a_report_section(monkeypatch):
    module, _ = report_module(monkeypatch)
    report = "# Report\n\n```text\n## Appendix: example only\n```\n\nActual narrative."
    result = module._ensure_appendix(report, {"raw": 7})
    assert report in result


@pytest.mark.parametrize("language", ["en", "sv"])
def test_report_generation_passes_selected_language_and_has_one_appendix(monkeypatch, language):
    module, redis_client = report_module(monkeypatch)
    captured = {}
    def generate(agent, prompt, message_list, *, language=None):
        captured["language"] = language
        return "# Utkast\n\n## Appendix\nwrong\n## Bilaga\nwrong"
    monkeypatch.setattr(StubOpenAIResponseAgent, "generate_response", generate)
    message = "Skapa ett rapportutkast på svenska" if language == "sv" else "Create a draft report in English"
    module.generate_draft_report_response("thread-language", [{"role": "user", "content": message}], {"response_language": language})
    assert captured["language"] == language
    report = json.loads(redis_client.calls[0][2])["content"]
    assert "wrong" not in report
    assert (report.count("## Bilaga"), report.count("## Appendix")) == ((1, 0) if language == "sv" else (0, 1))


def test_swedish_report_fallback_localizes_empty_history(monkeypatch):
    module, _ = report_module(monkeypatch)
    report = module._fallback_report(thread_id="thread-empty", building_id="", address="", messages=[], building_facts={}, language="sv")
    assert "Ingen tidigare sammanfattning" in report
    assert "Inga önskemål från användaren" in report
    assert "No prior" not in report and "No user" not in report


@pytest.mark.parametrize("heading", ["Bilaga\n------", "Appendix\n========"])
def test_standard_setext_appendix_is_replaced(monkeypatch, heading):
    module, _ = report_module(monkeypatch)
    report = module._ensure_appendix("# Report\n\n" + heading + "\nwrong original evidence\n", {"exact": 123})
    assert "wrong original evidence" not in report
    assert report.count("## Appendix") == 1


def test_final_pilot_swedish_report_preserves_evidence_and_user_constraints(monkeypatch):
    module, redis_client = report_module(monkeypatch)
    building_id = "01-80-SKYTTEN2-2"
    facts = {
        "building_id": building_id,
        "address": "Nimrodsgatan 7",
        "declared_energy_class": "G",
        "calculated_energy_class": "F",
        "heating_system": "district heating",
        "epc_godkand": "2019-11-03",
    }
    observations = "Vi upplever ojämn värme och vill undvika större renoveringar i år."
    request = (
        "Skapa ett nedladdningsbart rapportutkast till styrelsen med underlaget, "
        "våra observationer, osäkerheter och nästa steg."
    )
    messages = [
        {"role": "user", "content": "Visa energiklass och värmesystem för Nimrodsgatan 7, Stockholm."},
        {"role": "assistant", "content": "Deklarerad klass G, beräknad F och fjärrvärme."},
        {"role": "user", "content": observations},
        {"role": "user", "content": request},
    ]
    captured = {}

    def generate(agent, prompt, message_list, *, language=None):
        captured.update(payload=json.loads(prompt), language=language)
        return "# Rapportutkast till styrelsen\n\nEtt utkast för granskning, inte en officiell energideklaration."

    monkeypatch.setattr(StubOpenAIResponseAgent, "generate_response", generate)
    response = module.generate_draft_report_response(
        "final-pilot-f12", messages,
        {"building_id": building_id, "address": "Nimrodsgatan 7", "retrieved_facts": facts},
    )
    assert captured["language"] == "sv"
    assert captured["payload"]["building_id"] == building_id
    assert captured["payload"]["building_facts"]["retrieved_facts"] == facts
    assert observations in [item["content"] for item in captured["payload"]["recent_messages"]]
    assert response["report_status"] == "generated"
    artifact = json.loads(redis_client.calls[0][2])
    assert artifact["report_id"] == response["downloadable_report"]["report_id"]
    assert artifact["mime_type"] == "text/plain; charset=utf-8"
    assert artifact["content"].startswith("# Rapportutkast till styrelsen")
    evidence = artifact["content"].split("## Bilaga: Byggnadsunderlag från samtalet", 1)[1]
    assert json.loads(evidence.split("```json\n", 1)[1].split("\n```", 1)[0]) == captured["payload"]["building_facts"]
    assert "officiell energideklaration" in artifact["content"]


def test_report_does_not_copy_cached_retrieved_facts_for_another_building(monkeypatch):
    module, _ = report_module(monkeypatch)
    facts = module._collect_building_facts(
        {"building_id": "CURRENT", "retrieved_facts": {"building_id": "OLD", "energy_class": "G"}},
        {"latest_state": {}, "states": []},
    )
    assert "retrieved_facts" not in facts


@pytest.mark.parametrize("model_response", ["# Rapportutkast\n\nEtt utkast för granskning.", None])
def test_noisy_restored_state_projects_small_scoped_evidence_into_prompt_and_export(monkeypatch, model_response):
    module, redis_client = report_module(monkeypatch, model_response)
    building_id = "01-80-SKYTTEN2-2"
    record = {
        "byggnadsid": building_id,
        "epc_idadr": "Nimrodsgatan 7",
        "epc_egienergiklass": "G",
        "epc_egienergiklass2020_calc": "F",
        "epc_godkand": "2019-11-03",
        "epc_fjarrvarme": 128350.4,
        "technical_details": {"raw_code": "ElDirekt", "metered_values": [0, None, 137.1]},
        "user_email": "private-owner@example.com",
        "query_traces": {"sql": "SELECT SECRET FROM accounts"},
        "debug": "RAW MODEL DEBUG",
    }
    identity = {"status": "passed", "matched_building_id": building_id}
    metadata = {
        "building_id": building_id, "address": "Nimrodsgatan 7",
        "building_identity_check": identity, "retrieved_facts": record,
        "user_id": "PRIVATE-ACCOUNT-XYZ", "user_email": "private-owner@example.com",
        "session_id": "PRIVATE-SESSION-XYZ", "query_traces": {"generic_sql": {"sql": "SELECT SECRET FROM accounts"}},
        "agent_data": {"generic_sql": [record]}, "aggregated_data": {"generic_sql": [record]},
    }
    previous = {"metadata": {"building_id": "OLD-BUILDING"}, "agent_data_generic": [{"building_id": "OLD-BUILDING", "old_secret_fact": 999}]}
    current = {
        "metadata": metadata, "agent_data_generic": [record, record],
        "aggregated_data": {"generic_sql": [record]}, "agent_data": {"generic_sql": [record]},
        "session_state": [previous] * 30,
        "query": "SELECT SECRET FROM accounts", "user_id": "PRIVATE-ACCOUNT-XYZ",
    }
    monkeypatch.setattr(module, "get_session_state", lambda thread_id: [previous, current])
    captured = {}

    def generate(agent, prompt, message_list, *, language=None):
        captured["payload"] = json.loads(prompt)
        return model_response

    monkeypatch.setattr(StubOpenAIResponseAgent, "generate_response", generate)
    response = module.generate_draft_report_response(
        "PRIVATE-THREAD-XYZ",
        [{"role": "user", "content": "Vi upplever ojämn värme och vill undvika större renoveringar i år.",
          "parsed_intent": {"sql": "SELECT SECRET FROM accounts"}, "metadata": metadata},
         {"role": "user", "content": "Skapa ett rapportutkast till styrelsen."}],
        metadata,
    )
    exported = json.loads(redis_client.calls[0][2])["content"]
    prompt_text = json.dumps(captured["payload"], ensure_ascii=False)
    for text in (prompt_text, exported):
        for forbidden in ("PRIVATE-ACCOUNT-XYZ", "PRIVATE-SESSION-XYZ", "PRIVATE-THREAD-XYZ", "private-owner@example.com",
                          "SELECT SECRET", "RAW MODEL DEBUG", "old_secret_fact", "session_states", "aggregated_data", "parsed_intent"):
            assert forbidden not in text
        assert "Nimrodsgatan 7" in text
        assert "2019-11-03" in text
        assert "ElDirekt" in text
        assert "128350.4" in text
    evidence = captured["payload"]["building_facts"]
    assert len(json.dumps(evidence, ensure_ascii=False)) < 2500
    assert len(evidence["source_records"]["generic_sql"]) == 1
    assert evidence["retrieved_facts"]["epc_egienergiklass"] == "G"
    assert evidence["retrieved_facts"]["epc_egienergiklass2020_calc"] == "F"
    assert evidence["retrieved_facts"]["technical_details"]["metered_values"] == [0, None, 137.1]
    assert response["downloadable_report"]["building_id"] == building_id
    assert response["downloadable_report"]["created_at"]
    assert "ojämn värme" in prompt_text
    assert "undvika större renoveringar" in prompt_text


def test_report_does_not_bind_old_or_unidentified_nested_records_to_new_selection(monkeypatch):
    module, _ = report_module(monkeypatch)
    old = {"building_id": "OLD", "energy_class": "G", "old_only_fact": "do not export"}
    snapshot = module._normalize_session_state({
        "metadata": {"building_id": "NEW", "building_identity_check": {"status": "passed", "matched_building_id": "NEW"}},
        "agent_data_generic": [old, {"building_id": "NEW", "energy_class": "D", "linked_records": [old]}],
        "agent_data_specialized": [{"building_id": 12, "energy_class": "A"}],
    })
    facts = module._collect_building_facts({"building_id": "NEW", "retrieved_facts": old}, snapshot)
    assert facts["source_records"]["generic_sql"][0]["energy_class"] == "D"
    assert "old_only_fact" not in json.dumps(facts)
    assert "specialized_sql" not in facts["source_records"]
    assert "retrieved_facts" not in facts


@pytest.mark.parametrize("status", ["failed", "missing", "ambiguous", "not_found", "unmatched"])
def test_unverified_current_identity_does_not_export_earlier_record(monkeypatch, status):
    module, _ = report_module(monkeypatch)
    snapshot = module._normalize_session_state({
        "metadata": {"building_id": "CURRENT", "retrieved_facts": {"building_id": "CURRENT", "energy_class": "G"}},
        "agent_data_generic": [{"building_id": "CURRENT", "energy_class": "G"}],
    })
    facts = module._collect_building_facts({"building_id": "CURRENT", "building_identity_check": {"status": status}}, snapshot)
    assert facts == {"building_id": "CURRENT", "identity_status": status}


@pytest.mark.parametrize("restored_metadata", [False, True])
def test_failed_fresh_lookup_does_not_export_deep_merged_old_records(monkeypatch, restored_metadata):
    module, _ = report_module(monkeypatch)
    record = {"building_id": "CURRENT", "energy_class": "G"}
    state = {
        "metadata": {"building_id": "CURRENT", "retrieved_facts": record,
                     "query_traces": {"generic_sql": {"execution_status": "not_found"}}},
        "done_generic_sql": True, "aggregated_data": {"generic_sql": [record]},
    }
    metadata = state["metadata"] if restored_metadata else {"building_id": "CURRENT"}
    facts = module._collect_building_facts(metadata, module._normalize_session_state(state))
    assert "retrieved_facts" not in facts
    assert "source_records" not in facts


def test_report_contact_redaction_preserves_user_goals_and_excludes_message_metadata(monkeypatch):
    module, _ = report_module(monkeypatch)
    prompt = json.loads(module._build_report_prompt(
        thread_id="internal-thread", building_id="CURRENT",
        messages=[{"role": "user", "content": "Budget 20 000 kr. Kontakta private@example.com. Inga stora renoveringar.",
                   "metadata": {"user_id": "hidden-user"}, "parsed_intent": {"sql": "SELECT secret"}}],
        metadata={"user_email": "private@example.com", "user_id": "hidden-user"}, session_snapshot={}, building_facts={},
    ))
    content = prompt["recent_messages"][0]["content"]
    assert "Budget 20 000 kr" in content and "Inga stora renoveringar" in content
    assert "private@example.com" not in json.dumps(prompt)
    assert set(prompt["recent_messages"][0]) == {"role", "content"}
    assert "thread_id" not in prompt and "session_state" not in prompt


def test_explicit_new_selection_outranks_older_cached_identifiers_and_identity(monkeypatch):
    module, _ = report_module(monkeypatch)
    metadata = {
        "building_id_from_user": "NEW", "building_id": "OLD",
        "building_identity_check": {"status": "passed", "matched_building_id": "OLD"},
        "retrieved_facts": {"building_id": "OLD", "energy_class": "G"},
    }
    assert module._extract_building_id(metadata, {}) == "NEW"
    assert "retrieved_facts" not in module._collect_building_facts(metadata, {})
    assert module._report_context(metadata, "NEW")["identity_status"] == "unverified"


def test_new_selection_does_not_relabel_older_unidentified_building_information(monkeypatch):
    module, _ = report_module(monkeypatch)
    facts = module._collect_building_facts({
        "building_id_from_user": "NEW", "building_id": "OLD",
        "building_information": {"energy_class": "G"},
    }, {})
    assert facts == {"building_id": "NEW"}


def test_selection_conflict_does_not_reuse_old_address_or_declaration_freshness(monkeypatch):
    module, _ = report_module(monkeypatch)
    metadata = {
        "building_id_from_user": "NEW", "building_id": "OLD", "address": "Old Street 1",
        "epc_record_address": "Old Street 1", "data_freshness": {"energy_declaration_year": 2010},
        "response_language": "sv", "retrieved_facts": {"building_id": "OLD"},
    }
    assert module._extract_address(metadata, {}) == ""
    assert module._report_context(metadata, "NEW") == {"response_language": "sv"}
