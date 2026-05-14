# src/utils/redis_session_store.py
import json
import logging
import os
from typing import Any, List

import psycopg2
import redis
from psycopg2.extras import Json


logger = logging.getLogger(__name__)

redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
redis_port = int(os.getenv("REDIS_PORT", 6379))
r = redis.StrictRedis(
    host=redis_host,
    port=redis_port,
    decode_responses=True,
)

_schema_ensured = False


def _db_env_configured() -> bool:
    required = (
        "SQL_DB_HOST",
        "SQL_DB_NAME",
        "SQL_DB_USER",
        "SQL_DB_PASSWORD",
        "SQL_DB_PORT",
    )
    return all(os.getenv(key) for key in required)


def _get_connection():
    if not _db_env_configured():
        return None

    return psycopg2.connect(
        host=os.environ["SQL_DB_HOST"].strip(),
        dbname=os.environ["SQL_DB_NAME"].strip(),
        user=os.environ["SQL_DB_USER"].strip(),
        password=os.environ["SQL_DB_PASSWORD"],
        port=os.environ["SQL_DB_PORT"].strip(),
        connect_timeout=5,
    )


def _close_safely(cursor=None, connection=None):
    if cursor is not None:
        cursor.close()
    if connection is not None:
        connection.close()


def _ensure_snapshot_schema():
    global _schema_ensured

    if _schema_ensured:
        return

    connection = None
    cursor = None
    try:
        connection = _get_connection()
        if connection is None:
            return

        cursor = connection.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS session_state_snapshots (
                snapshot_id SERIAL PRIMARY KEY,
                thread_id VARCHAR(255) NOT NULL,
                state_payload JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_state_snapshots_thread_id
            ON session_state_snapshots (thread_id, snapshot_id);
            """
        )
        connection.commit()
        _schema_ensured = True
    except Exception as exc:  # pragma: no cover - defensive runtime fallback
        if connection is not None:
            connection.rollback()
        logger.warning("Could not ensure session_state_snapshots schema: %s", exc)
    finally:
        _close_safely(cursor, connection)


def _normalize_loaded_state(raw: Any):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _append_state(existing: Any, state: dict) -> List[dict]:
    if isinstance(existing, list):
        history = list(existing)
    elif isinstance(existing, dict) and existing:
        history = [existing]
    else:
        history = []

    history.append(state)
    return history


def _load_session_state_from_db(thread_id: str):
    connection = None
    cursor = None
    try:
        connection = _get_connection()
        if connection is None:
            return {}

        _ensure_snapshot_schema()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT state_payload
            FROM session_state_snapshots
            WHERE thread_id = %s
            ORDER BY snapshot_id ASC;
            """,
            (thread_id,),
        )
        rows = cursor.fetchall()
        states = []
        for row in rows:
            payload = row[0]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = None
            if isinstance(payload, dict):
                states.append(payload)

        if not states:
            return {}
        return states
    except Exception as exc:  # pragma: no cover - defensive runtime fallback
        logger.warning("Could not load session state from Postgres for %s: %s", thread_id, exc)
        return {}
    finally:
        _close_safely(cursor, connection)


def _persist_session_state_to_db(thread_id: str, state: dict) -> None:
    connection = None
    cursor = None
    try:
        connection = _get_connection()
        if connection is None:
            return

        _ensure_snapshot_schema()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO session_state_snapshots (thread_id, state_payload)
            VALUES (%s, %s);
            """,
            (thread_id, Json(state)),
        )
        connection.commit()
    except Exception as exc:  # pragma: no cover - defensive runtime fallback
        if connection is not None:
            connection.rollback()
        logger.warning("Could not persist session state to Postgres for %s: %s", thread_id, exc)
    finally:
        _close_safely(cursor, connection)


def get_session_state(thread_id: str) -> dict:
    key = f"session:{thread_id}"
    raw = r.get(key)
    restored = _normalize_loaded_state(raw)
    if restored:
        return restored

    restored = _load_session_state_from_db(thread_id)
    if restored:
        try:
            r.set(key, json.dumps(restored))
        except Exception as exc:  # pragma: no cover - defensive runtime fallback
            logger.warning("Could not rehydrate Redis session state for %s: %s", thread_id, exc)
    return restored


def update_session_state(thread_id: str, state: dict):
    key = f"session:{thread_id}"
    raw = r.get(key)
    existing = _normalize_loaded_state(raw)
    updated_history = _append_state(existing, state)
    r.set(key, json.dumps(updated_history))
    _persist_session_state_to_db(thread_id, state)
