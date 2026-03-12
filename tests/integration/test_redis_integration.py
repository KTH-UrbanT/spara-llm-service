import os

import pytest


pytestmark = pytest.mark.integration


def test_redis_env_connection_smoke():
    redis = pytest.importorskip("redis")
    host = os.getenv("REDIS_HOST", "127.0.0.1")
    port = int(os.getenv("REDIS_PORT", 6379))
    db = int(os.getenv("REDIS_DB", 0))

    client = redis.StrictRedis(
        host=host,
        port=port,
        db=db,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )

    assert client.ping() is True
