import redis
import os
import time
from datetime import datetime
import uuid
import json
from src.pipeline.agent_router import *
from src.pipeline.evaluation_metadata import (
    build_message_evidence,
    build_message_metadata,
)
from src.pipeline.telemetry import telemetry_context, telemetry_snapshot
# from src.pipeline.agent_router_langgraph import *

RESOLVED_BRF_STATUSES = {
    "resolved_by_user_selection",
    "resolved_unique_building",
}

TRANSIENT_METADATA_KEYS = (
    "pending_brf_resolution",
)


def _is_resolved_brf_metadata(metadata):
    metadata = metadata or {}
    resolution = metadata.get("brf_resolution")
    status = resolution.get("status") if isinstance(resolution, dict) else None
    return bool(
        status in RESOLVED_BRF_STATUSES
        or metadata.get("selected_brf_building_id")
    )


def _metadata_keys_to_delete(metadata):
    metadata = metadata or {}
    delete_keys = []
    for key in TRANSIENT_METADATA_KEYS:
        if key not in metadata:
            delete_keys.append(key)
    if _is_resolved_brf_metadata(metadata) and "pending_brf_resolution" not in delete_keys:
        delete_keys.append("pending_brf_resolution")
    return delete_keys


def _encode_metadata_value(value):
    if isinstance(value, (list, dict, bool)):
        return json.dumps(value)
    return str(value)


'''
The redis payload will be like this : 
{
    "role" : "user or assistant" , 
    "timestamp": time.time(),
    "added_to_database" : 0 as we donot add anything into database in this service however if its a old converation, it will be 1, 
    "content" : "text" , 
    "classification" : "building_specific/general/cluster" , 
    "agent_answered" : "name of agent which answered" 
}

redis also allows to  store metadata, whose payload will be 
    "metadata" : {
        "building_information" : [
            *information of all building related data*
        ], 
        "simulation_results" : [
            *information of all simulation results*
        ]
    }
}
'''

class RedisQueueManager:
    def __init__(self, redis_host=None, redis_port=None, db=0, pubsub_channel='thread_events'):
        redis_host = redis_host or os.getenv("REDIS_HOST", "127.0.0.1")
        redis_port = redis_port or int(os.getenv("REDIS_PORT", 6379))
        self.agent_router = AgentRouter()
        self.redis = None
        retries = 5

        while retries > 0:
            try:
                self.redis = redis.StrictRedis(
                    host=redis_host,
                    port=redis_port,
                    db=db,
                    decode_responses=True
                )
                self.redis.ping()
                print(f"✅ Connected to Redis at {redis_host}:{redis_port}")
                break
            except redis.exceptions.ConnectionError as e:
                retries -= 1
                print(f"⚠️ Redis connection failed ({e}), retries left: {retries}")
                time.sleep(2)

        if not self.redis:
            raise Exception("❌ Failed to connect to Redis after several retries.")

        self.pubsub_channel = pubsub_channel

    def get_thread_messages(self, thread_id):
        """Return the entire message history for a given thread."""
        return [json.loads(msg) for msg in self.redis.lrange(f"thread:{thread_id}:messages", 0, -1)]

    def add_message_to_thread(self, thread_id, role, content):
        """Add a message with full metadata to the thread's Redis list."""
        thread_key = f"thread:{thread_id}"
        is_first_message = self.redis.llen(thread_key) == 0

        message = {
            "role": role,
            "timestamp": time.time(),
            "added_to_database": int(not is_first_message),
            "content": content,
            "classification": None,
            "agent_answered": None,
        }

        self.redis.rpush(thread_key, json.dumps(message))
        self.redis.publish(self.pubsub_channel, json.dumps({"thread_name": thread_id}))
    def event_listener(self):
        """Listens for thread events and processes user messages."""
        pubsub = self.redis.pubsub()
        pubsub.subscribe(self.pubsub_channel)
        print("🔁 Listening for thread events...")

        for message in pubsub.listen():
            if message["type"] != "message":
                continue

            try:
                event_data = json.loads(message["data"])
                thread_id = event_data.get("thread_name")
                if not thread_id:
                    continue

                self.process_thread_event(thread_id)

            except Exception as e:
                print(f"❌ Error parsing pubsub message: {e}")

    # def process_thread_event(self, thread_id):
    #     """Process an individual thread's latest user message and respond."""
    #     try:
    #         thread_key = f"thread:{thread_id}"
    #         messages = self.get_thread_messages(thread_id)
    #         if not messages:
    #             print(f"⚠️ No messages found for thread {thread_id}.")
    #             return
    #         last_message = messages[-1]
    #         if last_message.get("role") != "user":
    #             print(f"ℹ️ Last message in thread {thread_id} not from user. Ignoring.")
    #             return

    #         print(f"📨 Routing message from thread {thread_id} via agent_router...")
    #         try :
    #             raw_metadata = self.redis.hgetall(thread_key)

    #             metadata = {}

    #             for k, v in raw_metadata.items():
    #                 try:
    #                     # Try to decode JSON strings back to Python objects
    #                     metadata[k] = json.loads(v)
    #                 except (json.JSONDecodeError, TypeError):
    #                     # If not JSON, keep as string
    #                     metadata[k] = v                
    #         except : 
    #             metadata = {}    
    #         start_time = time.time()
    #         #print(messages, last_message['content'] , metadata , thread_id)
    #         response  , metadata = self.agent_router.route_message(messages, last_message['content'] , metadata , thread_id)

    #         if not response:
    #             print(f"⚠️ No response from agent for thread {thread_id}.")
    #             return
    #         # print(response)
    #         response['timestamp'] = time.time()
    #         response['added_to_database'] = 0
    #         # print(response)
    #         print(metadata)
    #         # assistant_message = {
    #         #     "role": "assistant",
    #         #     "timestamp": time.time(),
    #         #     "added_to_database": 0,
    #         #     "content": response.get("content", ""),
    #         #     "classification": response.get("classification", ""),
    #         #     "agent_answered": response.get("agent_answered", "")
    #         # }
    #         # metadata = response.get("metadata", {})
    #         if metadata!= {} : 
    #             if len(metadata['address'])!= 0 or len(metadata['simulation_results'])!= 0 : 
    #                 encoded_metadata = {}

    #                 for k, v in metadata.items():
    #                     # If value is a list or dict, json.dumps it
    #                     if isinstance(v, (list, dict)):
    #                         encoded_metadata[k] = json.dumps(v)
    #                     else:
    #                         encoded_metadata[k] = str(v)

    #                 # Store all fields at once
    #                 self.redis.hset(thread_key, mapping=encoded_metadata)

    #         end_time = time.time()
    #         print(f"The question was answered in {end_time - start_time} seconds")
    #         self.redis.rpush(thread_key, json.dumps(response))
    #         self.redis.publish(self.pubsub_channel, json.dumps({"thread_name": thread_id}))
    #         print(f"✅ Response added to thread {thread_id} and event published.")
    #     except Exception as e:
    #         print(f"❌ Error processing thread {thread_id}: {e}")
    def process_thread_event(self, thread_id):
        """Process an individual thread's latest user message and respond."""
        try:
            import json, time  # ensure imported here or at module top

            meta_key = f"thread:{thread_id}:meta"        # hash for metadata
            msg_key  = f"thread:{thread_id}:messages"    # list for messages

            messages = self.get_thread_messages(thread_id)  # make sure this reads from msg_key
            if not messages:
                print(f"⚠️ No messages found for thread {thread_id}.")
                return

            last_message = messages[-1]
            if last_message.get("role") != "user":
                print(f"ℹ️ Last message in thread {thread_id} not from user. Ignoring.")
                return

            print(f"📨 Routing message from thread {thread_id} via agent_router...")

            # ---- Read metadata (hash) safely
            try:
                raw_metadata = self.redis.hgetall(meta_key)
                metadata = {}
                for k, v in raw_metadata.items():
                    try:
                        metadata[k] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        metadata[k] = v
            except Exception as e:
                print(f"ℹ️ Could not read metadata for {thread_id}: {e}")
                metadata = {}

            start_time = time.time()

            with telemetry_context("live_chat_turn"):
                response, metadata = self.agent_router.route_message(
                    messages, last_message['content'], metadata, thread_id
                )
                metadata = {
                    **(metadata or {}),
                    "last_user_message": last_message.get("content"),
                    "telemetry": telemetry_snapshot(),
                }

            if not response:
                print(f"⚠️ No response from agent for thread {thread_id}.")
                return

            response['timestamp'] = time.time()
            response['added_to_database'] = 0
            response_metadata = build_message_metadata(response, metadata)
            response["metadata"] = {
                **(response.get("metadata") or {}),
                **response_metadata,
            }
            response["evidence"] = build_message_evidence(response["metadata"])

            # ---- Write metadata back so cross-turn state like expert handoff confirmation persists
            if metadata:
                delete_keys = _metadata_keys_to_delete(metadata)
                if delete_keys:
                    self.redis.hdel(meta_key, *delete_keys)
                encoded_metadata = {
                    k: _encode_metadata_value(v)
                    for k, v in metadata.items()
                    if v is not None
                }
                self.redis.hset(meta_key, mapping=encoded_metadata)

            # ---- Append assistant response to the thread message list
            self.redis.rpush(msg_key, json.dumps(response))

            # ---- Publish event (payload clarified)
            self.redis.publish(self.pubsub_channel, json.dumps({
                "thread_id": thread_id,
                "thread_name": thread_id,
                "messages_key": msg_key,
                "meta_key": meta_key
            }))

            end_time = time.time()
            print(f"✅ Response added to thread {thread_id} and event published.")
            print(f"⏱️ The question was answered in {end_time - start_time:.3f} seconds")

        except Exception as e:
            print(f"❌ Error processing thread {thread_id}: {e}")
