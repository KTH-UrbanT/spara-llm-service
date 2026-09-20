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
    socket_connect_timeout=5,
    socket_timeout=5,
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


def _trim_messages(messages: List[Dict[str, Any]], keep_last: Optional[int] = None) -> List[Dict[str, Any]]:
    trimmed = []
    selected = messages or []
    if keep_last is not None:
        selected = selected[-keep_last:] if keep_last > 0 else []
    for message in selected:
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

    direct_sources = [metadata, latest_metadata, latest_aggregated, session_snapshot.get("latest_state", {})]
    direct_sources.extend(reversed(session_snapshot.get("states", [])))
    # Source recency/selection comes before identifier-key priority. Otherwise
    # an old byggnadsid can outrank the current selection's building_id.
    for source in direct_sources:
        preferred_identifier = _report_building_identity(source)
        if preferred_identifier:
            return preferred_identifier

    return "building_id_not_available"


def _report_building_identity(source: Dict[str, Any]) -> Optional[str]:
    if not isinstance(source, dict):
        return None
    selected = _extract_first_nonempty(source.get("selected_brf_building_id"))
    if selected:
        return selected
    direct = {key: source[key] for key in BUILDING_ID_KEYS if source.get(key)}
    return select_preferred_identifier(direct) or select_preferred_identifier(source)


def _scope_session_snapshot(session_snapshot: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    if building_id == "building_id_not_available":
        return session_snapshot
    included = []
    excluded_ids = set()
    unidentified_count = 0
    for state in session_snapshot.get("states", []):
        # Persisted graph states can embed the whole preceding session history.
        # The scoped top-level list already supplies that history; retaining a
        # nested copy would reintroduce facts from excluded buildings.
        state = _without_embedded_session_history(state)
        state_id = _report_building_identity(state.get("metadata") or {}) or _report_building_identity(state)
        if state_id and state_id != building_id:
            excluded_ids.add(state_id)
            continue
        if not state_id:
            unidentified_count += 1
        included.append(state)
    return {
        "history_length": len(included),
        "states": included,
        "latest_state": included[-1] if included else {},
        "excluded_other_building_ids": sorted(excluded_ids),
        "unidentified_state_count": unidentified_count,
    }


def _without_embedded_session_history(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_embedded_session_history(nested)
            for key, nested in value.items()
            if key not in {"session_state", "session_states", "states"}
        }
    if isinstance(value, list):
        return [_without_embedded_session_history(nested) for nested in value]
    return deepcopy(value)


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

    for source in [session_snapshot.get("latest_state", {}), metadata or {}]:
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


def _build_report_filename(building_id: str, address: str) -> str:
    primary = _extract_first_nonempty(building_id)
    if primary and primary != "building_id_not_available":
        return f"{_sanitize_filename_component(primary)}.txt"

    fallback_address = _extract_first_nonempty(address)
    if fallback_address:
        return f"{_sanitize_filename_component(fallback_address)}.txt"

    return "draft_energy_report.txt"


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
        "evidence_scope": {
            "current_building_id": building_id,
            "current_metadata_takes_precedence": True,
            "excluded_other_building_ids": session_snapshot.get("excluded_other_building_ids", []),
            "conversation_history": "May discuss other buildings; retain goals and constraints but do not treat previous-building facts as facts about the current building.",
            "unidentified_snapshots": "Building ownership is unverified; do not attribute those facts to the current building without matching evidence.",
        },
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
    user_context = "\n".join(
        f"- {message['content']}" for message in messages or []
        if message.get("role") == "user" and message.get("content")
    ) or "No user objectives were stored."
    address_line = address or "No building address was stored in the session metadata."

    return (
        "# Draft Energy Report\n\n"
        f"Building ID: {building_id}\n\n"
        f"Session ID: {thread_id}\n\n"
        "## Building and Session Context\n"
        f"- Building ID: {building_id}\n"
        f"- Address: {address_line}\n\n"
        "## Generation Status\n"
        "The report-writing service was unavailable. This is a basic session summary, "
        "not a completed advisory assessment.\n\n"
        "## User-Reported Objectives, Questions, and Constraints\n"
        f"{user_context}\n\n"
        "## Prior Conversation Summary (Not Independently Verified)\n"
        f"{message_excerpt}\n\n"
        "## Potential Energy Efficiency Opportunities\n"
        "- No new measures or priorities have been established in this fallback summary.\n"
        "- An advisor should assess the user's objectives and the available evidence before recommending measures.\n\n"
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
    address: str,
    report_text: str,
) -> Dict[str, Any]:
    report_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(seconds=REPORT_TTL_SECONDS)
    file_name = _build_report_filename(building_id, address)

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
    stored = _redis_client.setex(key, REPORT_TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
    if stored is False:
        raise RuntimeError("Report storage was not acknowledged")

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
    session_unavailable = False
    try:
        session_state = get_session_state(thread_id)
    except Exception:
        session_state = {}
        session_unavailable = True
    session_snapshot = _normalize_session_state(session_state)
    building_id = _extract_building_id(metadata or {}, session_snapshot)
    if building_id == "building_id_not_available":
        building_id_from_messages = _extract_building_id_from_messages(messages or [])
        if building_id_from_messages:
            building_id = building_id_from_messages
    session_snapshot = _scope_session_snapshot(session_snapshot, building_id)
    address = _extract_address(metadata or {}, session_snapshot)
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

    normalized_report = report_markdown.strip() if isinstance(report_markdown, str) else ""
    used_fallback = (
        not normalized_report or normalized_report.lower().startswith("error:")
        or normalized_report.lower().rstrip(".") in {
            "no content returned", "no content returned from the model",
        }
    )
    if used_fallback:
        report_markdown = _fallback_report(
            thread_id=thread_id,
            building_id=building_id,
            address=address,
            messages=messages or [],
            building_facts=building_facts,
        )

    report_text = _ensure_appendix(report_markdown.strip(), building_facts)
    if session_unavailable:
        report_text = (
            "Note: stored building session history could not be loaded. This draft uses "
            "only the available conversation and metadata; some evidence may be missing.\n\n"
            + report_text
        )
    try:
        artifact = _store_report(
            thread_id=thread_id,
            building_id=building_id,
            address=address,
            report_text=report_text,
        )
    except Exception:
        return {
            "role": "assistant",
            "content": (
                "I could not create a downloadable report because report storage is unavailable. "
                "You can copy the draft text below.\n\n" + report_text
            ),
            "classification": "draft_energy_report",
            "agent_answered": "draft_energy_report",
            "report_status": "storage_failed",
        }

    building_label = f"building `{building_id}`" if building_id != "building_id_not_available" else "the available session"
    generation_note = (
        "The report-writing service was unavailable, so this file is a basic session "
        "summary for review, not a completed advisory assessment. " if used_fallback else ""
    )
    history_note = "Stored session history could not be loaded; the draft may be incomplete. " if session_unavailable else ""

    return {
        "role": "assistant",
        "content": (
            f"I created a draft energy report text file for {building_label}. "
            f"{generation_note}{history_note}"
            f"You can download it below until {artifact['expires_at']} (UTC)."
        ),
        "classification": "draft_energy_report",
        "agent_answered": "draft_energy_report",
        "downloadable_report": artifact,
        "report_status": "fallback" if used_fallback else "generated",
    }
