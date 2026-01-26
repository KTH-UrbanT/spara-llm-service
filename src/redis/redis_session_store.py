# src/utils/redis_session_store.py
import redis
import json
import os

redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
redis_port = int(os.getenv("REDIS_PORT", 6379))
r = redis.StrictRedis(
                    host=redis_host,
                    port=redis_port,
                    decode_responses=True
                )

def get_session_state(thread_id: str) -> dict:
    key = f"session:{thread_id}"
    raw = r.get(key)
    return json.loads(raw) if raw else {}

def update_session_state(thread_id: str, state: dict):
    key = f"session:{thread_id}"
    raw = r.get(key)
    try:
        existing = json.loads(raw) if raw else []
    except Exception:
        existing = []

    if not isinstance(existing, list):
        existing = [existing] if existing else []

    existing.append(state)
    r.set(key, json.dumps(existing))  # expires after 1 hour (optional)

