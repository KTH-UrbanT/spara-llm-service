import json
import os
import re
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import redis

from src.agents.openai_agent import OpenAIResponseAgent
from src.agents.building_response_prompt import select_preferred_identifier
from src.redis.redis_session_store import get_session_state


REPORT_TTL_SECONDS = int(os.getenv("DRAFT_REPORT_TTL_SECONDS", "1800"))
REPORT_KEY_PREFIX = os.getenv("DRAFT_REPORT_KEY_PREFIX", "draft_report")
REPORT_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "draft_energy_report_prompt.txt"
)

_redis_client = redis.StrictRedis(
    host=os.getenv("REDIS_HOST", "127.0.0.1"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    decode_responses=True,
)

BUILDING_ID_KEYS = (
    "building_id",
    "byggnadsid",
    "50a_uuid",
    "uuid",
    "oden_uuid",
    "01a_fnr",
    "epc_idadr",
)
BUILDING_FACT_KEYS = (
    "building_id",
    "byggnadsid",
    "50a_uuid",
    "uuid",
    "oden_uuid",
    "01a_fnr",
    "50a_deso",
    "epc_idadr",
    "address",
    "address_from_user",
    "building_information",
    "simulation_results",
    "aggregated_data",
    "agent_data",
    "metadata",
)


def _trim_messages(messages: List[Dict[str, Any]], keep_last: int = 8) -> List[Dict[str, Any]]:
    trimmed = []
    for message in (messages or [])[-keep_last:]:
        trimmed.append(
            {
                "role": message.get("role"),
                "content": message.get("content"),
                "classification": message.get("classification"),
                "parsed_intent": message.get("parsed_intent"),
            }
        )
    return trimmed


def _normalize_session_state(session_state: Any) -> Dict[str, Any]:
    if isinstance(session_state, list):
        states = [item for item in session_state if isinstance(item, dict)]
        latest = states[-1] if states else {}
        return {
            "history_length": len(states),
            "states": states,
            "latest_state": latest,
        }
    if isinstance(session_state, dict):
        return {
            "history_length": 1,
            "states": [session_state],
            "latest_state": session_state,
        }
    return {
        "history_length": 0,
        "states": [],
        "latest_state": {},
    }


def _walk_values(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_values(nested)


def _extract_first_nonempty(value: Any) -> Optional[str]:
    if isinstance(value, list) and value:
        value = value[0]
    if value in (None, "", [], {}):
        return None
    return str(value)


def _extract_address(metadata: Dict[str, Any], session_snapshot: Dict[str, Any]) -> str:
    candidates = [
        metadata.get("address"),
        metadata.get("address_from_user"),
        (session_snapshot.get("latest_state", {}).get("metadata") or {}).get("address"),
        (session_snapshot.get("latest_state", {}).get("metadata") or {}).get("address_from_user"),
    ]
    for candidate in candidates:
        value = _extract_first_nonempty(candidate)
        if value:
            return value
    return ""


def _extract_building_id(metadata: Dict[str, Any], session_snapshot: Dict[str, Any]) -> str:
    metadata = metadata or {}
    latest_metadata = (session_snapshot.get("latest_state", {}).get("metadata") or {})
    latest_aggregated = session_snapshot.get("latest_state", {}).get("aggregated_data") or {}

    direct_sources = [metadata, latest_metadata, latest_aggregated, session_snapshot.get("latest_state", {}), session_snapshot.get("states", [])]
    preferred_identifier = select_preferred_identifier(*direct_sources)
    if preferred_identifier:
        return preferred_identifier

    return "building_id_not_available"


def _extract_building_id_from_messages(messages: List[Dict[str, Any]]) -> Optional[str]:
    patterns = [
        r"Building ID:\s*([A-Za-z0-9\-]+)",
        r"byggnadsid[:\s]*([A-Za-z0-9\-]+)",
    ]

    for message in reversed(messages or []):
        content = str(message.get("content") or "")
        if not content:
            continue
        for pattern in patterns:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()

    return None


def _collect_building_facts(metadata: Dict[str, Any], session_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    facts: Dict[str, Any] = {}

    for source in [metadata or {}, session_snapshot.get("latest_state", {})]:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if key in BUILDING_FACT_KEYS and value not in (None, "", [], {}):
                facts[key] = deepcopy(value)

    raw_states = session_snapshot.get("states", [])
    if raw_states:
        facts["session_states"] = deepcopy(raw_states)

    return facts


def _sanitize_filename_component(value: str) -> str:
    value = str(value or "").strip()
    cleaned = re.sub(r'[<>:"/\\|?*\s]+', "_", value).strip("._")
    return cleaned or "building_id_not_available"


def _build_report_prompt(
    *,
    thread_id: str,
    building_id: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    session_snapshot: Dict[str, Any],
    building_facts: Dict[str, Any],
) -> str:
    payload = {
        "thread_id": thread_id,
        "building_id": building_id,
        "metadata": deepcopy(metadata or {}),
        "recent_messages": _trim_messages(messages),
        "building_facts": building_facts,
        "session_state": session_snapshot,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _fallback_report(
    *,
    thread_id: str,
    building_id: str,
    address: str,
    messages: List[Dict[str, Any]],
    building_facts: Dict[str, Any],
) -> str:
    summary_lines = []
    for message in reversed(messages or []):
        if message.get("role") == "assistant" and message.get("content"):
            summary_lines.append(str(message.get("content")).strip())
            if len(summary_lines) == 2:
                break

    message_excerpt = "\n\n".join(summary_lines) if summary_lines else "No prior assistant summary was available."
    address_line = address or "No building address was stored in the session metadata."

    return (
        "# Draft Energy Report\n\n"
        f"Building ID: {building_id}\n\n"
        f"Session ID: {thread_id}\n\n"
        "## Building and Session Context\n"
        f"- Building ID: {building_id}\n"
        f"- Address: {address_line}\n\n"
        "## Key Observations\n"
        f"{message_excerpt}\n\n"
        "## Potential Energy Efficiency Opportunities\n"
        "- Review building envelope, ventilation, and heating control settings based on the stored data.\n"
        "- Validate whether additional metering or historical trend data is needed before final recommendations.\n\n"
        "## Data Gaps and Assumptions\n"
        "- This draft is based only on the information stored in the session state.\n"
        "- Any missing values should be validated before sharing the report externally.\n\n"
        "## Recommended Next Steps\n"
        "- Review the appendix for the full stored building context.\n"
        "- Validate critical fields such as building ID, address, and latest measured values.\n"
        "- Have an advisor review the draft before it is finalized.\n\n"
        "## Appendix: Full Session Building Data\n"
        "```json\n"
        f"{json.dumps(building_facts, indent=2, ensure_ascii=False)}\n"
        "```\n"
    )


def _ensure_appendix(report_markdown: str, building_facts: Dict[str, Any]) -> str:
    appendix_title = "## Appendix: Full Session Building Data"
    if appendix_title in report_markdown:
        return report_markdown
    appendix = (
        f"\n\n{appendix_title}\n"
        "```json\n"
        f"{json.dumps(building_facts, indent=2, ensure_ascii=False)}\n"
        "```\n"
    )
    return report_markdown.rstrip() + appendix


def _store_report(
    *,
    thread_id: str,
    building_id: str,
    report_text: str,
) -> Dict[str, Any]:
    report_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(seconds=REPORT_TTL_SECONDS)
    file_name = f"{_sanitize_filename_component(building_id)}.txt"

    payload = {
        "report_id": report_id,
        "thread_id": thread_id,
        "file_name": file_name,
        "mime_type": "text/plain; charset=utf-8",
        "content": report_text,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    key = f"{REPORT_KEY_PREFIX}:{report_id}"
    _redis_client.setex(key, REPORT_TTL_SECONDS, json.dumps(payload, ensure_ascii=False))

    return {
        "report_id": report_id,
        "file_name": file_name,
        "mime_type": payload["mime_type"],
        "expires_at": payload["expires_at"],
    }


def generate_draft_report_response(
    thread_id: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    session_state = get_session_state(thread_id)
    session_snapshot = _normalize_session_state(session_state)
    address = _extract_address(metadata or {}, session_snapshot)
    building_id = _extract_building_id(metadata or {}, session_snapshot)
    if building_id == "building_id_not_available":
        building_id_from_messages = _extract_building_id_from_messages(messages or [])
        if building_id_from_messages:
            building_id = building_id_from_messages
    building_facts = _collect_building_facts(metadata or {}, session_snapshot)

    prompt = _build_report_prompt(
        thread_id=thread_id,
        building_id=building_id,
        messages=messages or [],
        metadata=metadata or {},
        session_snapshot=session_snapshot,
        building_facts=building_facts,
    )

    report_markdown = None
    try:
        agent = OpenAIResponseAgent(prompt_path=str(REPORT_PROMPT_PATH))
        report_markdown = agent.generate_response(prompt, message_list=[])
    except Exception:
        report_markdown = None

    if not isinstance(report_markdown, str) or not report_markdown.strip() or report_markdown.startswith("Error:"):
        report_markdown = _fallback_report(
            thread_id=thread_id,
            building_id=building_id,
            address=address,
            messages=messages or [],
            building_facts=building_facts,
        )

    report_text = _ensure_appendix(report_markdown.strip(), building_facts)
    artifact = _store_report(
        thread_id=thread_id,
        building_id=building_id,
        report_text=report_text,
    )

    return {
        "role": "assistant",
        "content": (
            f"I created a draft energy report text file for building `{building_id}` from the "
            "full stored session context. You can download it below for the next 30 minutes."
        ),
        "classification": "draft_energy_report",
        "agent_answered": "draft_energy_report",
        "downloadable_report": artifact,
    }
