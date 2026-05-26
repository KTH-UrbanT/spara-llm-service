import json

from tests.support import fresh_import, stub_module


class FakeRedisClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.values = {}
        FakeRedisClient.instances.append(self)

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self._rows = []

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        if "INSERT INTO session_state_snapshots" in sql:
            thread_id, payload = params
            self.connection.snapshots.setdefault(thread_id, []).append(payload.payload)
        elif "SELECT state_payload FROM session_state_snapshots" in sql:
            thread_id = params[0]
            self._rows = [
                (payload,) for payload in self.connection.snapshots.get(thread_id, [])
            ]
        else:
            self._rows = []

    def fetchall(self):
        return list(self._rows)

    def close(self):
        return None


class FakeConnection:
    snapshots = {}

    def __init__(self, *args, **kwargs):
        self.snapshots = type(self).snapshots
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        return None


class FakeJson:
    def __init__(self, payload):
        self.payload = payload


def import_session_store_module():
    FakeRedisClient.instances = []
    FakeConnection.snapshots = {}
    stub_module("redis", StrictRedis=FakeRedisClient)
    stub_module("psycopg2", connect=lambda **kwargs: FakeConnection())
    stub_module("psycopg2.extras", Json=FakeJson)
    return fresh_import("src.redis.redis_session_store")


def test_update_session_state_keeps_redis_history_and_persists_snapshot():
    module = import_session_store_module()
    module.os.environ["SQL_DB_HOST"] = "localhost"
    module.os.environ["SQL_DB_NAME"] = "spara"
    module.os.environ["SQL_DB_USER"] = "tester"
    module.os.environ["SQL_DB_PASSWORD"] = "secret"
    module.os.environ["SQL_DB_PORT"] = "5432"

    module.update_session_state("thread-1", {"metadata": {"building_id": "abc-123"}})

    assert json.loads(module.r.get("session:thread-1")) == [
        {"metadata": {"building_id": "abc-123"}}
    ]
    assert FakeConnection.snapshots["thread-1"] == [
        {"metadata": {"building_id": "abc-123"}}
    ]


def test_get_session_state_falls_back_to_postgres_and_rehydrates_redis():
    module = import_session_store_module()
    module.os.environ["SQL_DB_HOST"] = "localhost"
    module.os.environ["SQL_DB_NAME"] = "spara"
    module.os.environ["SQL_DB_USER"] = "tester"
    module.os.environ["SQL_DB_PASSWORD"] = "secret"
    module.os.environ["SQL_DB_PORT"] = "5432"
    FakeConnection.snapshots["thread-2"] = [
        {"metadata": {"building_id": "abc-123"}},
        {"metadata": {"building_id": "abc-123", "address": "Examplegatan 1"}},
    ]

    restored = module.get_session_state("thread-2")

    assert restored == FakeConnection.snapshots["thread-2"]
    assert json.loads(module.r.get("session:thread-2")) == FakeConnection.snapshots["thread-2"]


def test_get_session_state_prefers_existing_redis_value():
    module = import_session_store_module()
    module.r.set("session:thread-3", json.dumps({"from": "redis"}))
    FakeConnection.snapshots["thread-3"] = [{"from": "postgres"}]

    restored = module.get_session_state("thread-3")

    assert restored == {"from": "redis"}


def test_evaluation_mode_keeps_session_state_in_memory_only():
    module = import_session_store_module()
    module.os.environ["EVALUATION_MODE"] = "true"
    module.os.environ["SQL_DB_HOST"] = "localhost"
    module.os.environ["SQL_DB_NAME"] = "spara"
    module.os.environ["SQL_DB_USER"] = "tester"
    module.os.environ["SQL_DB_PASSWORD"] = "secret"
    module.os.environ["SQL_DB_PORT"] = "5432"

    module.update_session_state("eval-thread-1", {"metadata": {"building_id": "abc-123"}})

    assert module.get_session_state("eval-thread-1") == [
        {"metadata": {"building_id": "abc-123"}}
    ]
    assert module.r.get("session:eval-thread-1") is None
    assert FakeConnection.snapshots == {}
