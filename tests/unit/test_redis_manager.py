import json

from tests.support import fresh_import, stub_module


class FakeAgentRouter:
    response = {"content": "assistant answer", "classification": "generic", "agent_answered": "generic"}
    metadata = {"address": ["Main Street"], "simulation_results": []}
    calls = []

    def route_message(self, messages, last_message, metadata, thread_id):
        type(self).calls.append((messages, last_message, metadata, thread_id))
        return dict(type(self).response), dict(type(self).metadata)


class FakePubSub:
    def __init__(self, events):
        self.events = events
        self.subscriptions = []

    def subscribe(self, channel):
        self.subscriptions.append(channel)

    def listen(self):
        for event in self.events:
            yield event


class FakeRedisClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.lists = {}
        self.hashes = {}
        self.published = []
        self.pubsub_events = []
        FakeRedisClient.instances.append(self)

    def ping(self):
        return True

    def lrange(self, key, start, stop):
        return self.lists.get(key, [])[start : None if stop == -1 else stop + 1]

    def llen(self, key):
        return len(self.lists.get(key, []))

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hdel(self, key, *fields):
        current = self.hashes.setdefault(key, {})
        removed = 0
        for field in fields:
            if field in current:
                removed += 1
                current.pop(field, None)
        return removed

    def pubsub(self):
        return FakePubSub(self.pubsub_events)


class FakeRedisExceptions:
    ConnectionError = RuntimeError


def import_redis_manager_module():
    FakeAgentRouter.calls = []
    FakeAgentRouter.response = {"content": "assistant answer", "classification": "generic", "agent_answered": "generic"}
    FakeAgentRouter.metadata = {"address": ["Main Street"], "simulation_results": []}
    stub_module("src.pipeline.agent_router", AgentRouter=FakeAgentRouter)
    stub_module(
        "redis",
        StrictRedis=FakeRedisClient,
        exceptions=FakeRedisExceptions,
    )
    return fresh_import("src.redis.redis_manager")


def test_process_thread_event_routes_latest_user_message_and_persists_metadata():
    module = import_redis_manager_module()
    manager = module.RedisQueueManager(pubsub_channel="thread_events")
    manager.redis.lists["thread:thread-1:messages"] = [
        json.dumps({"role": "user", "content": "How can I retrofit?"})
    ]
    manager.redis.hashes["thread:thread-1:meta"] = {"old": json.dumps(["x"])}

    manager.process_thread_event("thread-1")

    assert FakeAgentRouter.calls[-1][1] == "How can I retrofit?"
    saved_meta = manager.redis.hashes["thread:thread-1:meta"]
    assert json.loads(saved_meta["address"]) == ["Main Street"]
    assert json.loads(saved_meta["simulation_results"]) == []
    telemetry = json.loads(saved_meta["telemetry"])
    assert telemetry["operation"] == "live_chat_turn"
    assert telemetry["model_call_count"] == 0
    assert "total_latency_seconds" in telemetry
    saved_messages = manager.redis.lists["thread:thread-1:messages"]
    assert len(saved_messages) == 2
    assistant_message = json.loads(saved_messages[-1])
    assert assistant_message["content"] == "assistant answer"
    assert assistant_message["added_to_database"] == 0
    assert assistant_message["metadata"]["route"] == "generic"
    assert assistant_message["metadata"]["agent"] == "GenericAgent"
    assert assistant_message["metadata"]["telemetry"]["operation"] == "live_chat_turn"
    assert any(
        evidence["evidence_type"] == "building_match"
        for evidence in assistant_message["evidence"]
    )

    channel, payload = manager.redis.published[-1]
    assert channel == "thread_events"
    assert json.loads(payload) == {
        "thread_id": "thread-1",
        "thread_name": "thread-1",
        "messages_key": "thread:thread-1:messages",
        "meta_key": "thread:thread-1:meta",
    }


def test_process_thread_event_skips_non_user_latest_message():
    module = import_redis_manager_module()
    manager = module.RedisQueueManager()
    manager.redis.lists["thread:thread-2:messages"] = [
        json.dumps({"role": "assistant", "content": "already answered"})
    ]

    manager.process_thread_event("thread-2")

    assert FakeAgentRouter.calls == []
    assert manager.redis.published == []


def test_process_thread_event_deletes_stale_pending_brf_resolution_after_selection():
    module = import_redis_manager_module()
    manager = module.RedisQueueManager()
    manager.redis.lists["thread:thread-brf:messages"] = [
        json.dumps({"role": "user", "content": "when was this building built?"})
    ]
    manager.redis.hashes["thread:thread-brf:meta"] = {
        "pending_brf_resolution": json.dumps({"brf_name": "Solgläntan 1"}),
        "brf_resolution": json.dumps(
            {
                "status": "resolved_by_user_selection",
                "selected_building_id": "01-80-HEDVIG15-1",
            }
        ),
        "selected_brf_building_id": "01-80-HEDVIG15-1",
    }
    FakeAgentRouter.metadata = {
        "brf_resolution": {
            "status": "resolved_by_user_selection",
            "selected_building_id": "01-80-HEDVIG15-1",
        },
        "selected_brf_building_id": "01-80-HEDVIG15-1",
        "byggnadsid": "01-80-HEDVIG15-1",
    }

    manager.process_thread_event("thread-brf")

    saved_meta = manager.redis.hashes["thread:thread-brf:meta"]
    assert "pending_brf_resolution" not in saved_meta
    assistant_message = json.loads(manager.redis.lists["thread:thread-brf:messages"][-1])
    assert "pending_brf_resolution" not in assistant_message["metadata"]


def test_event_listener_consumes_pubsub_messages_with_thread_name():
    module = import_redis_manager_module()
    manager = module.RedisQueueManager(pubsub_channel="thread_events")
    manager.redis.pubsub_events = [
        {"type": "subscribe", "data": 1},
        {"type": "message", "data": json.dumps({"thread_name": "thread-3"})},
    ]
    seen = []
    manager.process_thread_event = seen.append

    manager.event_listener()

    assert seen == ["thread-3"]
