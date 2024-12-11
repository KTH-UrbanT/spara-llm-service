import redis
import json
import os
from main_retrieval_generation import RetrievalGeneration  # Assuming this is the file containing your class
import copy


class RedisQueueManager:
    def __init__(self, redis_host="127.0.0.1", redis_port=6379):
        self.redis_client = redis.StrictRedis(host=redis_host, port=redis_port, decode_responses=True)
        self.language_model = RetrievalGeneration()

    def initialize_thread(self, thread_name, user_message):
        """
        Initializes a new thread with the prompt from 'prompt.txt'
        followed by the user message.
        """
        if not self.redis_client.exists(thread_name):
            # Load the initial prompt from the file
            if os.path.exists("prompt.txt"):
                with open("prompt.txt", "r") as prompt_file:
                    prompt = prompt_file.read().strip()
                # Push the prompt into the thread's queue
                self.redis_client.rpush(thread_name, json.dumps({"content": prompt, "role": "system"}))
                print(f"Initialized thread {thread_name} with prompt.")
            else:
                print("Error: 'prompt.txt' file not found. Cannot initialize the thread.")
                return

        # Push the user message into the thread's queue
        self.redis_client.rpush(thread_name, json.dumps({"content": user_message, "role": "user"}))
        print(f"Added user message to thread {thread_name}.")

    def enqueue_message(self, thread_name, message):
        """Push a new message to the specific Redis thread queue."""
        self.redis_client.rpush(thread_name, json.dumps({"content": message, "role": "user"}))

    def read_all_messages(self, thread_name):
        """Read all messages from a specific Redis thread queue without deleting."""
        messages = self.redis_client.lrange(thread_name, 0, -1)
        return [json.loads(message) for message in messages]

    def process_thread(self, thread_name):
        """Process a single message thread."""
        print(f"Processing thread: {thread_name}")

        # Read all messages from the thread's queue
        existing_conversation = self.read_all_messages(thread_name)
        messages = copy.deepcopy(existing_conversation)[:-1]
        if not existing_conversation:
            print(f"Thread {thread_name} is empty. Skipping...")
            return

        # Prepare the existing conversation structure for the language model
        # existing_conversation = [{"role": msg["role"], "content": msg["content"]} for msg in messages]

        # # Call the language model for responses
        # question = " ".join(msg["content"] for msg in messages if msg["role"] == "user")  # Combine user messages
        responses = self.language_model.generate_reply_texts( existing_conversation[-1]['content'], messages, type="text")

        # Push responses back to the queue
        # for response in responses:
        #     if response["role"] == "assistant":  # Push only assistant responses
        #self.enqueue_message(thread_name, responses[-1])
        self.redis_client.rpush(thread_name, json.dumps( responses[-1]))
        print(f"Processed and updated thread: {thread_name}")

    def process_all_threads(self):
        """Process all active message threads."""
        # Retrieve all keys that match the thread pattern
        thread_keys = self.redis_client.keys("thread:*")
        if not thread_keys:
            print("No active threads found.")
            return

        for thread_key in thread_keys:
            self.process_thread(thread_key)

    def delete_thread(self, thread_name):
        """Delete a specific thread from Redis."""
        if self.redis_client.exists(thread_name):
            self.redis_client.delete(thread_name)
            print(f"Thread {thread_name} deleted.")
        else:
            print(f"Thread {thread_name} does not exist.")

# RedisQueueManager()
# Example Usage
# if __name__ == "__main__":
#     # Initialize the Redis queue manager
#     queue_manager = RedisQueueManager()

#     # Example: Add messages to a new thread
#     queue_manager.initialize_thread("thread:1", "What is the weather today?")
#     queue_manager.initialize_thread("thread:2", "Tell me about AI.")

#     # Process all threads
#     queue_manager.process_all_threads()

#     # Example: Delete a thread
#     # queue_manager.delete_thread("thread:1")
