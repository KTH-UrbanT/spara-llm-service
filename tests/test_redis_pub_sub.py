import pytest
from unittest.mock import MagicMock, patch
from redis_pub_sub import RedisQueueManager
import os
import json
import unittest


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    mock_redis_client = MagicMock()
    return mock_redis_client


# Test Case 1: Test Initialization of a Thread (`initialize_thread`)
def test_initialize_thread(mock_redis):
    thread_name = "test_thread"
    user_message = "Hello, how are you?"

    # Mock the open function to simulate prompt.txt content
    with patch("builtins.open", unittest.mock.mock_open(read_data="This is a system prompt.")):
        # Create instance of RedisQueueManager
        manager = RedisQueueManager(redis_host="mock_host", redis_port=6379)
        manager.redis_client = mock_redis  # Inject mock Redis client

        # Mock Redis client to simulate thread non-existence
        mock_redis.exists.return_value = False

        # Mock the add_message_to_thread to ensure it gets called after the thread is initialized
        manager.add_message_to_thread = MagicMock()

        # Call initialize_thread method
        manager.initialize_thread(thread_name, user_message)

        # Check that the system prompt was added to the thread
        mock_redis.rpush.assert_any_call(thread_name, '{"content": "This is a system prompt.", "role": "system"}')
        
        # Ensure the user's message was added after the system prompt
        manager.add_message_to_thread.assert_called_once_with(thread_name, user_message)


# Test Case 2: Test Initialization of a Thread without prompt.txt (`initialize_thread_no_prompt`)
def test_initialize_thread_no_prompt(mock_redis):
    thread_name = "test_thread"
    user_message = "Hello, how are you?"

    # Mock Redis client to simulate thread non-existence
    mock_redis.exists.return_value = False

    # Patch os.path.exists to always return False, simulating missing prompt.txt
    with patch("os.path.exists", return_value=False):
        # Create instance of RedisQueueManager
        manager = RedisQueueManager(redis_host="mock_host", redis_port=6379)
        manager.redis_client = mock_redis  # Inject mock Redis client

        # Call initialize_thread method
        manager.initialize_thread(thread_name, user_message)

        # Ensure that no system prompt is added to Redis due to the absence of the prompt.txt file
        mock_redis.rpush.assert_not_called()

        # Check that no message is added to the thread because prompt.txt was missing
        mock_redis.publish.assert_not_called()
        
        
# Test Case 3: Test Adding a Message to an Existing Thread (`add_message_to_thread`)
def test_add_message_to_existing_thread(mock_redis):
    thread_name = "test_thread"
    user_message = "What is the weather?"

    # Create instance of RedisQueueManager
    manager = RedisQueueManager(redis_host="mock_host", redis_port=6379)
    manager.redis_client = mock_redis  # Inject mock Redis client

    # Mock Redis client to simulate thread existence
    mock_redis.exists.return_value = True

    # Call add_message_to_thread method
    manager.add_message_to_thread(thread_name, user_message)

    # Check Redis interactions
    mock_redis.rpush.assert_called_with(thread_name, '{"content": "What is the weather?", "role": "user"}')
    mock_redis.publish.assert_called_with("thread_events", '{"event": "message_added", "thread_name": "test_thread"}')


# Test Case 4: Test Reading All Messages from a Thread (`read_all_messages`)
def test_read_all_messages(mock_redis):
    thread_name = "test_thread"
    expected_messages = [
        {"content": "System prompt", "role": "system"},
        {"content": "What is the weather?", "role": "user"}
    ]

    # Mock Redis client to simulate messages in the thread
    mock_redis.lrange.return_value = [json.dumps(msg) for msg in expected_messages]

    # Create instance of RedisQueueManager
    manager = RedisQueueManager(redis_host="mock_host", redis_port=6379)
    manager.redis_client = mock_redis  # Inject mock Redis client

    # Call read_all_messages method
    messages = manager.read_all_messages(thread_name)

    # Assert the expected messages are returned
    assert messages == expected_messages


# Test Case 5: Test Processing a Thread (`process_thread`)
@patch("redis_pub_sub.RetrievalGeneration")
def test_process_thread(mock_retrieval_generation, mock_redis):
    thread_name = "test_thread"
    user_message = "What is the capital of France?"
    existing_conversation = [
        {"content": "System prompt", "role": "system"},
        {"content": "What is the weather?", "role": "user"},
        {"content": user_message, "role": "user"}
    ]
    # Mock Redis client to simulate existing thread
    mock_redis.exists.return_value = True
    mock_redis.lrange.return_value = [json.dumps(msg) for msg in existing_conversation]

    # Mock RetrievalGeneration's generate_reply_texts method
    mock_generate_reply_texts = mock_retrieval_generation.return_value.generate_reply_texts
    mock_generate_reply_texts.return_value = [{"role": "assistant", "content": "Paris"}]

    # Create instance of RedisQueueManager
    manager = RedisQueueManager(redis_host="mock_host", redis_port=6379)
    manager.redis_client = mock_redis  # Inject mock Redis client

    # Call process_thread method
    manager.process_thread(thread_name)

    # Check Redis interactions
    mock_redis.rpush.assert_called_with(thread_name, '{"role": "assistant", "content": "Paris"}')


# Test Case 6: Test Deleting All Threads (`delete_all_threads`)
def test_delete_all_threads(mock_redis):
    # Mock Redis client to simulate existing threads
    mock_redis.keys.return_value = ["thread1", "thread2", "thread3"]

    # Create instance of RedisQueueManager
    manager = RedisQueueManager(redis_host="mock_host", redis_port=6379)
    manager.redis_client = mock_redis  # Inject mock Redis client

    # Call delete_all_threads method
    manager.delete_all_threads()

    # Check Redis interactions
    mock_redis.delete.assert_called_with("thread1", "thread2", "thread3")


# Test Case 7: Test Event Listener (`event_listener`)
@patch("redis_pub_sub.RedisQueueManager.process_thread")
def test_event_listener(mock_process_thread, mock_redis):
    # Mock Redis pubsub
    mock_pubsub = MagicMock()
    mock_pubsub.listen.return_value = [
        {"type": "message", "data": '{"event": "message_added", "thread_name": "test_thread"}' }
    ]
    mock_redis.pubsub.return_value = mock_pubsub

    # Create instance of RedisQueueManager
    manager = RedisQueueManager(redis_host="mock_host", redis_port=6379)
    manager.redis_client = mock_redis  # Inject mock Redis client

    # Call event_listener method
    manager.event_listener()

    # Check if process_thread was called
    mock_process_thread.assert_called_with("test_thread")


# Running the tests
if __name__ == "__main__":
    pytest.main()
