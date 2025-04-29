import redis
import json
import os
import copy
from main_retrieval_generation import RetrievalGeneration
import time
from dotenv import load_dotenv  # For loading environment variables from .env file

# Load environment variables from the .env file
load_dotenv()


class RedisQueueManager:
    def __init__(self, redis_host=None, redis_port=None):
        redis_host = redis_host or os.getenv("REDIS_HOST", "127.0.0.1")
        redis_port = redis_port or int(os.getenv("REDIS_PORT", 6379))

        # Retry logic for Redis connection
        self.redis_client = None
        retries = 5
        while retries > 0:
            try:
                self.redis_client = redis.StrictRedis(host=redis_host, port=redis_port, decode_responses=True)
                self.redis_client.ping()  # Test connection
                print("Connected to Redis")
                break
            except redis.exceptions.ConnectionError:
                retries -= 1
                print(f"Redis connection failed, retries left: {retries}")
                time.sleep(2)  # Wait before retrying
        if not self.redis_client:
            raise Exception("Failed to connect to Redis after several retries.")
        self.language_model = RetrievalGeneration()

    def initialize_thread(self, thread_name, user_message):
        """
        Initializes a new thread by adding a system prompt (if available)
        followed by the user's message.
        """
        try : 
            if not self.redis_client.exists(thread_name):
                #prompt = os.getenv("PROMPT", "").strip()
                if os.path.exists("prompt.txt"):
                    with open("prompt.txt", "r") as prompt_file:
                        prompt = prompt_file.read().strip()
                # if not prompt:
                #         print("Warning: 'PROMPT' environment variable is not set. Using default message.")
                #         prompt = ""
                self.redis_client.rpush(thread_name, json.dumps
                                        (
                                            {
                                                "content": prompt, 
                                                "role": "system" , 
                                                "timestamp": time.time(), 
                                                "added_to_database" : 0
                                            }
                                        ))
                print(f"Initialized thread {thread_name} with prompt.")
            self.add_message_to_thread(thread_name, user_message)
        except redis.RedisError as e:
            print(f"Error: Redis issue while initializing thread '{thread_name}': {e}")

    def add_message_to_thread(self, thread_name, user_message):
        """
        Adds a new user message to an existing thread and processes it.
        """
        if self.redis_client.exists(thread_name):
            self.redis_client.rpush(thread_name, json.dumps
                                    (
                                        {
                                            "content": user_message, 
                                            "role": "user" , 
                                            "timestamp": time.time(),
                                            "added_to_database" : 0
                                        }
                                    ))
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
        try : 
            existing_conversation = self.read_all_messages(thread_name)
            if not existing_conversation:
                print(f"Thread {thread_name} is empty. Skipping...")
                return

            # Handle new conversation scenario (only one message from user)
            if len(existing_conversation) == 1 and existing_conversation[0]["role"] == "user":
                # prompt = os.getenv("PROMPT", "").strip()
                # if not prompt:
                #     print("Warning: 'PROMPT' environment variable is not set. Using default message.")
                #     prompt = ""
                if os.path.exists("prompt.txt"):
                    with open("prompt.txt", "r") as prompt_file:
                        prompt = prompt_file.read().strip()
                system_message = {
                    "content": prompt,
                    "role": "system",
                    "timestamp": time.time(),
                    "added_to_database" : 0
                }
                try:
                    # Update the first message in Redis
                    self.redis_client.lset(thread_name, 0, json.dumps(system_message))
                    # Re-add the user message for context
                    self.redis_client.rpush(thread_name, json.dumps(existing_conversation[0]))
                    print(f"Added system prompt to thread '{thread_name}' as the first message.")
                except redis.RedisError as e:
                    print(f"Error: Redis issue while updating thread '{thread_name}': {e}")
                    return

            if len(existing_conversation) == 1 and existing_conversation[0]["role"] == "user":
                messages = [system_message]
            else : 
                messages = copy.deepcopy(existing_conversation)[:-1]
            user_message = existing_conversation[-1]['content']

            try:
                start_time = time.time()
                responses = self.language_model.generate_reply_texts(user_message, messages, type="text")
                end_time = time.time()
                latency = end_time - start_time
                
                print(f"Language Model response time: {latency:.4f} seconds")

                if not responses or not isinstance(responses, list):
                    print(f"Error: No valid response generated for thread '{thread_name}'.")
                    return

                # Add timestamp
                responses[-1]["timestamp"] = time.time()
                responses[-1]["added_to_database"] =  0

                response_json = json.dumps(responses[-1])  # Validate JSON serialization
                self.redis_client.rpush(thread_name, response_json)
                print(f"Processed and updated thread: '{thread_name}'.")
            except redis.RedisError as e:
                print(f"Redis error while saving response in thread '{thread_name}': {e}")
            except (TypeError, ValueError) as e:
                print(f"JSON serialization error for AI response in thread '{thread_name}': {e}")
            except AttributeError as e:
                print(f"Error: 'language_model.generate_reply_texts' method failed in thread '{thread_name}': {e}")
        except SystemExit:
            print("System exit detected. Exiting process.")
        except Exception as e:
            print(f"Unexpected error in thread '{thread_name}': {e}")        

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
    deployment = os.getenv("LANGUAGE_MODEL_DEPLOYMENT_NAME")
    print(f"[INFO] Language Model deployed: {deployment}")
    manager.event_listener()