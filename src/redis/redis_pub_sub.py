import redis
import json
import os
import copy
from src.rag import RetrievalGeneration


class RedisQueueManager:
    def __init__(self, redis_host="127.0.0.1", redis_port=6379):
        self.redis_client = redis.StrictRedis(host=redis_host, port=redis_port, decode_responses=True)
        self.language_model = RetrievalGeneration()

    def initialize_thread(self, thread_name, user_message):
        """
        Initializes a new thread by adding a system prompt (if available)
        followed by the user's message.
        """
        if not self.redis_client.exists(thread_name):
            if os.path.exists("prompt.txt"):
                with open("prompt.txt", "r") as prompt_file:
                    prompt = prompt_file.read().strip()
                self.redis_client.rpush(thread_name, json.dumps({"content": prompt, "role": "system"}))
                print(f"Initialized thread {thread_name} with prompt.")
            else:
                print("Error: 'prompt.txt' file not found.")
                return
        self.add_message_to_thread(thread_name, user_message)

    def add_message_to_thread(self, thread_name, user_message):
        """
        Adds a new user message to an existing thread and processes it.
        """
        if self.redis_client.exists(thread_name):
            self.redis_client.rpush(thread_name, json.dumps({"content": user_message, "role": "user"}))
            self.redis_client.publish("thread_events", json.dumps({"event": "message_added", "thread_name": thread_name}))
            print(f"Added message to existing thread: {thread_name}")
        else:
            print(f"Thread {thread_name} does not exist. Initializing a new thread.")
            self.initialize_thread(thread_name, user_message)

    def get_all_threads(self):
        """Returns a list of all existing thread names in the Redis queue."""
        return self.redis_client.keys()

    def delete_all_threads(self):
        """Deletes all existing threads from the Redis queue."""
        thread_keys = self.redis_client.keys()
        if thread_keys:
            self.redis_client.delete(*thread_keys)
            print("All threads deleted successfully.")
        else:
            print("No threads found to delete.")

    def process_thread(self, thread_name):
        """
        Processes a thread by generating a reply based on the latest user message
        and the previous conversation context.
        """
        existing_conversation = self.read_all_messages(thread_name)
        if not existing_conversation:
            print(f"Thread {thread_name} is empty. Skipping...")
            return

        # Handle new conversation scenario (only one message from user)
        if len(existing_conversation) == 1 and existing_conversation[0]["role"] == "user":
            if os.path.exists("prompt.txt"):
                with open("prompt.txt", "r") as prompt_file:
                    prompt = prompt_file.read().strip()
                self.redis_client.lset(thread_name, 0, json.dumps({"content": prompt, "role": "system"}))
                self.redis_client.rpush(thread_name, json.dumps(existing_conversation[0]))  # Add user message again
                print(f"Added system prompt to thread {thread_name} as the first message.")

        messages = copy.deepcopy(existing_conversation)[:-1]
        responses = self.language_model.generate_reply_texts(existing_conversation[-1]['content'], messages, type="text")
        self.redis_client.rpush(thread_name, json.dumps(responses[-1]))
        print(f"Processed and updated thread: {thread_name}")

    def read_all_messages(self, thread_name):
        """Reads all messages from the specified thread."""
        messages = self.redis_client.lrange(thread_name, 0, -1)
        return [json.loads(message) for message in messages]

    def event_listener(self):
        """Listens for updates on the thread and triggers processing."""
        pubsub = self.redis_client.pubsub()
        pubsub.subscribe("thread_events")
        print("Listening for thread events...")
        for message in pubsub.listen():
            if message["type"] == "message":
                event_data = json.loads(message["data"])
                thread_name = event_data.get("thread_name")
                self.process_thread(thread_name)


if __name__ == "__main__":
    manager = RedisQueueManager()
    manager.event_listener()
