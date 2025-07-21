import os
import json
import time
import pytest
import redis

from redis_pub_sub import RedisQueueManager  # adjust import path
import random 
import string

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
TEST_THREAD = "pytest_dummy_thread:" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=20))

@pytest.fixture(scope="module")
def manager():
    """Yield a RedisQueueManager connected to the test server and clean up afterwards."""
    mgr = RedisQueueManager(redis_host=REDIS_HOST, redis_port=REDIS_PORT)
    yield mgr
    # teardown – delete the test thread so CI remains clean
    try:
        mgr.redis_client.delete(TEST_THREAD)
    except redis.RedisError:
        pass

def test_can_ping_redis(manager):
    """Redis connection should respond to PING."""
    assert manager.redis_client.ping() is True

def test_initialize_and_write_message(manager):
    """The helper should initialise a thread and store a user message."""
    # Start fresh
    manager.redis_client.delete(TEST_THREAD)

    user_msg = "Hello, test!"
    manager.initialize_thread(TEST_THREAD, user_msg)

    # Expect two list entries: system prompt, then user message
    stored = manager.read_all_messages(TEST_THREAD)
    assert len(stored) == 2
    assert stored[1]["content"] == user_msg
    assert stored[1]["role"] == "user"

def test_add_and_fetch_message(manager):
    """Adding a second message should append and publish the event."""
    second_msg = "Second message"
    manager.add_message_to_thread(TEST_THREAD, second_msg)

    stored = manager.read_all_messages(TEST_THREAD)
    assert stored[-1]["content"] == second_msg
    assert stored[-1]["role"] == "user"
