# src/utils/redis_session_store.py
import redis
import json
import os

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")
r = redis.Redis.from_url(REDIS_URL)

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

