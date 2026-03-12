import runpy
import types


def test_generation_starts_redis_event_listener(monkeypatch):
    calls = {"event_listener": 0}

    class FakeRedisQueueManager:
        def event_listener(self):
            calls["event_listener"] += 1

    fake_module = types.ModuleType("src.redis.redis_manager")
    fake_module.RedisQueueManager = FakeRedisQueueManager
    monkeypatch.setitem(__import__("sys").modules, "src.redis.redis_manager", fake_module)

    runpy.run_path("generation.py", run_name="__main__")

    assert calls["event_listener"] == 1
