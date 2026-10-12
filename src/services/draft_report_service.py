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
from src.pipeline.safety_analysis import response_language
from src.services.source_link_registry import guard_response_links, verified_link_context


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
_PRIVATE_EVIDENCE_KEYS = {
    "metadata", "session_state", "session_states", "states", "latest_state",
    "messages", "history", "recent_messages", "thread_id", "session_id",
    "user_id", "user_email", "account_id", "email", "email_address",
    "contact", "contacts", "contact_details", "phone", "phone_number", "telephone",
    "query", "sql", "query_traces", "queries", "debug", "diagnostics",
    "model_response", "model_output", "model_name", "prompt", "parsed_intent",
    "agent_data", "aggregated_data", "action_receipts", "downloadable_report",
    "report_preview", "expert_handoff_preview", "api_key", "password", "token",
    "authorization", "transport", "socket_id", "request_id",
    "error", "error_message", "execution_status",
}
_REPORT_CONTEXT_KEYS = {
    "brf_name", "building_type", "address", "address_from_user", "requested_address",
    "epc_record_address", "same_building_multiple_addresses", "response_language",
    "data_freshness", "uncertainty",
}


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
    selected_id = _extract_building_id(metadata or {}, session_snapshot)
    for source in (metadata or {}, session_snapshot.get("latest_state", {}).get("metadata") or {}):
        if selected_id != "building_id_not_available" and not _metadata_matches_selection(source, selected_id):
            continue
        for key in ("address", "address_from_user"):
            value = _extract_first_nonempty(source.get(key))
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
    for key in ("building_id_from_user", "selected_address_building_id", "selected_brf_building_id"):
        selected = _extract_first_nonempty(source.get(key))
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
    """Project current-building evidence instead of exporting operational state."""
    metadata = metadata or {}
    selected_id = _extract_building_id(metadata or {}, session_snapshot)
    if selected_id == "building_id_not_available":
        return {}
    facts: Dict[str, Any] = {"building_id": selected_id}
    current_identity = metadata.get("building_identity_check") or {}
    if current_identity.get("status") in {"failed", "missing", "ambiguous", "not_found", "unmatched"}:
        facts["identity_status"] = current_identity["status"]
        return facts
    latest_state = session_snapshot.get("latest_state", {})
    latest_metadata = latest_state.get("metadata") or {}
    latest_trace = (latest_metadata.get("query_traces") or {}).get("generic_sql") or {}
    failed_primary_refresh = (bool(latest_state.get("done_generic_sql")) and
                              str(latest_trace.get("execution_status") or "").lower() in {
                                  "error", "not_found", "empty_result", "model_error", "db_error", "not_run", "noop",
                              })

    for source in [session_snapshot.get("latest_state", {}), metadata]:
        if not isinstance(source, dict):
            continue
        source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else source
        source_id = _report_building_identity(source_metadata) or _report_building_identity(source)
        same_source = _same_building(source_id, selected_id)
        identity = source_metadata.get("building_identity_check") or {}
        direct_ids = [source_metadata[key] for key in ("building_id", "byggnadsid") if source_metadata.get(key)]
        consistent_selection = (all(_same_building(identifier, selected_id) for identifier in direct_ids)
                                and (identity.get("status") != "passed"
                                     or _same_building(identity.get("matched_building_id"), selected_id)))
        projection_bound = (same_source and identity.get("status") == "passed"
                            and _same_building(identity.get("matched_building_id"), selected_id))
        retrieved = source_metadata.get("retrieved_facts")
        matching_retrieved = isinstance(retrieved, dict) and _record_matches(retrieved, selected_id)
        if matching_retrieved and not failed_primary_refresh:
            facts["retrieved_facts"] = _sanitize_evidence(retrieved, selected_id)
        source_consistent = same_source and consistent_selection and (not retrieved or matching_retrieved)
        if source_consistent:
            for key in BUILDING_ID_KEYS + ("50a_deso", "address", "address_from_user"):
                if source_metadata.get(key) not in (None, "", [], {}):
                    facts[key] = _sanitize_evidence(source_metadata[key])
            for key in ("building_information", "simulation_results"):
                value = source.get(key, source_metadata.get(key))
                scoped = _scope_records(value, selected_id, projection_bound=source_consistent)
                if scoped:
                    facts[key] = scoped[0] if isinstance(value, dict) and len(scoped) == 1 else scoped
        source_records = facts.setdefault("source_records", {})
        for name, direct_key, done_key in (
            ("generic_sql", "agent_data_generic", "done_generic_sql"),
            ("specialized_sql", "agent_data_specialized", "done_specialized_sql"),
        ):
            trace = (source_metadata.get("query_traces") or {}).get(name) or {}
            status = str(trace.get("execution_status") or "").lower()
            if source.get(done_key) and status in {"error", "not_found", "empty_result", "model_error", "db_error", "not_run", "noop"}:
                source_records.pop(name, None)
                facts.pop("retrieved_facts", None)
                continue
            payload = source.get(direct_key)
            if payload is None:
                for container in (source.get("aggregated_data"), source.get("agent_data")):
                    if isinstance(container, dict) and name in container:
                        payload = container[name]
                        break
            if payload is not None:
                if name == "generic_sql" and failed_primary_refresh:
                    continue
                records = _scope_records(payload, selected_id, projection_bound=(name == "generic_sql" and projection_bound))
                if records:
                    source_records[name] = records
                else:
                    source_records.pop(name, None)
        if not source_records:
            facts.pop("source_records", None)
    return facts


def _same_building(value: Any, selected_id: str) -> bool:
    return bool(value) and str(value).strip().casefold() == selected_id.casefold()


def _metadata_matches_selection(metadata: Dict[str, Any], selected_id: str) -> bool:
    for key in ("building_id", "byggnadsid", "building_id_from_user", "selected_address_building_id", "selected_brf_building_id"):
        if metadata.get(key) and not _same_building(metadata[key], selected_id):
            return False
    retrieved = metadata.get("retrieved_facts")
    if isinstance(retrieved, dict) and retrieved and not _record_matches(retrieved, selected_id):
        return False
    identity = metadata.get("building_identity_check") or {}
    return identity.get("status") != "passed" or _same_building(identity.get("matched_building_id"), selected_id)


def _record_matches(record: Dict[str, Any], selected_id: str, *, projection_bound: bool = False) -> bool:
    lower = {str(key).casefold(): value for key, value in record.items()}
    identifiers = [lower.get(key) for key in ("byggnadsid", "building_id") if lower.get(key)]
    if identifiers:
        # Numeric specialized-database IDs are a separate namespace. A national
        # byggnadsid on that row can establish the match; a local ID alone cannot.
        if lower.get("byggnadsid"):
            return _same_building(lower["byggnadsid"], selected_id)
        return all(_same_building(value, selected_id) for value in identifiers)
    return projection_bound


def _sanitize_evidence(value: Any, selected_id: Optional[str] = None) -> Any:
    """Retain technical record values, excluding operational/contact containers."""
    if isinstance(value, dict):
        if selected_id and not _record_matches(value, selected_id, projection_bound=True):
            return {}
        projected = {}
        for key, nested in value.items():
            normalized = re.sub(r"[\s-]+", "_", str(key).casefold())
            if (normalized in _PRIVATE_EVIDENCE_KEYS
                    or normalized.endswith(("_email", "_email_address", "_phone", "_phone_number"))
                    or normalized.startswith(("debug_", "sql_", "query_", "model_", "session_", "user_", "account_", "socket_"))):
                continue
            projected[str(key)] = _sanitize_evidence(nested, selected_id)
        return projected
    if isinstance(value, list):
        return [sanitized for item in value if (sanitized := _sanitize_evidence(item, selected_id)) != {}]
    if isinstance(value, str):
        return re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[contact omitted]", value)
    return deepcopy(value)


def _scope_records(payload: Any, selected_id: str, *, projection_bound: bool = False) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        rows = next((payload[key] for key in ("rows", "data", "records") if isinstance(payload.get(key), list)), [payload])
    else:
        rows = payload if isinstance(payload, list) else []
    scoped, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not _record_matches(row, selected_id, projection_bound=projection_bound):
            continue
        record = _sanitize_evidence(row, selected_id)
        encoded = json.dumps(record, sort_keys=True, ensure_ascii=False)
        if record and encoded not in seen:
            scoped.append(record)
            seen.add(encoded)
    return scoped


def _report_context(metadata: Dict[str, Any], building_id: str) -> Dict[str, Any]:
    context = {key: _sanitize_evidence(value) for key, value in (metadata or {}).items() if key in _REPORT_CONTEXT_KEYS}
    if building_id != "building_id_not_available" and not _metadata_matches_selection(metadata or {}, building_id):
        context = {key: value for key, value in context.items() if key == "response_language"}
    identity = (metadata or {}).get("building_identity_check") or {}
    if identity.get("status"):
        context["identity_status"] = (
            "unverified" if identity["status"] == "passed" and not _same_building(identity.get("matched_building_id"), building_id)
            else identity["status"]
        )
        if _same_building(identity.get("matched_building_id"), building_id):
            context["matched_building_id"] = building_id
    evidence = (metadata or {}).get("address_identity_evidence") or {}
    if _same_building(evidence.get("selected_building_id"), building_id):
        context["address_identity_evidence"] = _sanitize_evidence(evidence)
    return context


def _report_messages(messages: List[Dict[str, Any]], metadata: Dict[str, Any], thread_id: str) -> List[Dict[str, Any]]:
    messages = _sanitize_evidence(_trim_messages(messages))
    private_values = {str(thread_id)}
    private_values.update(str(metadata[key]) for key in ("user_id", "user_email", "session_id", "thread_id", "account_id") if metadata.get(key))
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            for value in private_values:
                if value:
                    content = content.replace(value, "[internal identifier omitted]")
            message["content"] = content
    return messages


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
        "building_id": building_id,
        "metadata": _report_context(metadata or {}, building_id),
        "recent_messages": _report_messages(messages, metadata or {}, thread_id),
        "building_facts": _sanitize_evidence(building_facts, building_id),
        "checked_link_catalog": verified_link_context(),
        "evidence_scope": {
            "current_building_id": building_id,
            "current_metadata_takes_precedence": True,
            "excluded_other_building_ids": session_snapshot.get("excluded_other_building_ids", []),
            "conversation_history": "May discuss other buildings; retain goals and constraints but do not treat previous-building facts as facts about the current building.",
            "record_scope": "Only matching current-building records and current retrieved facts are projected. Operational state, account details and query traces are excluded. Simulation results are estimates, not an energy declaration.",
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
    language: Optional[str] = None,
) -> str:
    summary_lines = []
    for message in reversed(messages or []):
        if message.get("role") == "assistant" and message.get("content"):
            summary_lines.append(str(message.get("content")).strip())
            if len(summary_lines) == 2:
                break

    swedish = (language or response_language(messages)) == "sv"
    message_excerpt = "\n\n".join(summary_lines) if summary_lines else (
        "Ingen tidigare sammanfattning från assistenten fanns tillgänglig." if swedish else "No prior assistant summary was available.")
    user_context = "\n".join(
        f"- {message['content']}" for message in messages or []
        if message.get("role") == "user" and message.get("content")
    ) or ("Inga önskemål från användaren fanns sparade." if swedish else "No user objectives were stored.")
    address_line = address or "No building address was stored in the session metadata."

    if swedish:
        return (
            f"# Utkast till energirapport\n\nByggnadsid: {building_id}\n\n"
            f"## Byggnad\nAdress: {address or 'Adress saknas i underlaget.'}\n\n"
            "## Status\nRapporttjänsten var inte tillgänglig. Detta är en sammanfattning av samtalet för granskning, inte en färdig energibedömning.\n\n"
            f"## Användarens frågor och önskemål\n{user_context}\n\n"
            f"## Tidigare svar (inte oberoende verifierade)\n{message_excerpt}\n\n"
            "## Nästa steg\nKontrollera byggnadsid, adress, perioder och viktiga uppgifter. Låt en energi- och klimatrådgivare granska underlaget och möjliga åtgärder.\n"
        )

    return (
        "# Draft Energy Report\n\n"
        f"Building ID: {building_id}\n\n"
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
        "- This draft uses the available conversation and scoped building evidence.\n"
        "- Any missing values should be validated before sharing the report externally.\n\n"
        "## Recommended Next Steps\n"
        "- Review the appendix for the selected building's available source records.\n"
        "- Validate critical fields such as building ID, address, and latest measured values.\n"
        "- Have an advisor review the draft before it is finalized.\n\n"
        "## Appendix: Full Session Building Data\n"
        "```json\n"
        f"{json.dumps(building_facts, indent=2, ensure_ascii=False)}\n"
        "```\n"
    )


def _ensure_appendix(report_markdown: str, building_facts: Dict[str, Any], language: str = "en") -> str:
    """Replace generated appendix sections with one exact stored-evidence block."""
    appendix_title = "## Bilaga: Byggnadsunderlag från samtalet" if language == "sv" else "## Appendix: Full Session Building Data"
    lines = str(report_markdown or "").splitlines(keepends=True)
    headings = []
    fence = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            headings.append((index, len(heading.group(1)), heading.group(2)))
        elif index + 1 < len(lines) and stripped and not re.match(r"^\s{0,3}(?:={3,}|-{3,})\s*$", line):
            underline = re.match(r"^\s{0,3}(={3,}|-{3,})\s*$", lines[index + 1])
            if underline:
                headings.append((index, 1 if underline.group(1)[0] == "=" else 2, stripped))
    removed = set()
    for position, (start, level, title) in enumerate(headings):
        title = re.sub(r"^\d+[.)]\s*", "", title.strip(" *_")).strip(" *_")
        if not re.match(r"^(?:appendix|bilaga)\b", title, re.IGNORECASE):
            continue
        end = next((index for index, next_level, _ in headings[position + 1:] if next_level <= level), len(lines))
        removed.update(range(start, end))
    body = "".join(line for index, line in enumerate(lines) if index not in removed).rstrip()
    appendix = (
        f"\n\n{appendix_title}\n"
        "```json\n"
        f"{json.dumps(building_facts, indent=2, ensure_ascii=False)}\n"
        "```\n"
    )
    return body + appendix


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
        "created_at": payload["created_at"],
        "expires_at": payload["expires_at"],
        "building_id": building_id if building_id != "building_id_not_available" else None,
    }


def generate_draft_report_response(
    thread_id: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    session_unavailable = False
    language = response_language(messages, metadata=metadata)
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
    report_messages = _report_messages(messages or [], metadata or {}, thread_id)

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
        report_markdown = agent.generate_response(prompt, message_list=[], language=language)
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
            messages=report_messages,
            building_facts=building_facts,
            language=language,
        )

    report_text = _ensure_appendix(guard_response_links(report_markdown.strip(), language), building_facts, language)
    if session_unavailable:
        report_text = (
            ("Observera: den sparade byggnadshistoriken kunde inte hämtas. Utkastet använder det tillgängliga samtalet och underlaget; vissa uppgifter kan saknas.\n\n" if language == "sv" else "Note: stored building session history could not be loaded. This draft uses only the available conversation and metadata; some evidence may be missing.\n\n")
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
                ("Rapportlagringen är inte tillgänglig, så någon fil kunde inte skapas. Du kan kopiera utkastet nedan.\n\n" if language == "sv" else "I could not create a downloadable report because report storage is unavailable. You can copy the draft text below.\n\n") + report_text
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
    if language == "sv":
        building_label = f"byggnad `{building_id}`" if building_id != "building_id_not_available" else "samtalet"
        generation_note = "Rapporttjänsten var inte tillgänglig. Filen innehåller en sammanfattning för granskning, inte en färdig energibedömning. " if used_fallback else ""
        history_note = "Den sparade historiken kunde inte hämtas; utkastet kan vara ofullständigt. " if session_unavailable else ""

    return {
        "role": "assistant",
        "content": (
            f"Jag har skapat ett rapportutkast som textfil för {building_label}. {generation_note}{history_note}Du kan hämta filen nedan till {artifact['expires_at']} (UTC)." if language == "sv" else
            f"I created a draft energy report text file for {building_label}. "
            f"{generation_note}{history_note}"
            f"You can download it below until {artifact['expires_at']} (UTC)."
        ),
        "classification": "draft_energy_report",
        "agent_answered": "draft_energy_report",
        "downloadable_report": artifact,
        "report_status": "fallback" if used_fallback else "generated",
    }
