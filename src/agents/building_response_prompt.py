import json
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional


PROMPT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "building_response_generation_prompt.txt"
)
PROMPT_TEMPLATE = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")

BUILDING_IDENTIFIER_PRIORITY = (
    "byggnadsid",
    "building_id",
    "50a_uuid",
    "uuid",
    "oden_uuid",
    "01a_fnr",
)


def _is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _stringify(value: Any) -> Optional[str]:
    if not _is_present(value):
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def _json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except TypeError:
        return json.dumps(str(value), ensure_ascii=False, indent=2)


def extract_identifier_fields(value: Any) -> Dict[str, str]:
    found: Dict[str, str] = {}
    queue = deque([value])

    while queue:
        current = queue.popleft()
        if isinstance(current, dict):
            lower_map = {str(key).lower(): key for key in current.keys()}
            for key in BUILDING_IDENTIFIER_PRIORITY:
                original_key = lower_map.get(key.lower())
                if original_key is None:
                    continue
                text = _stringify(current.get(original_key))
                if text and key not in found:
                    found[key] = text
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)

    return found


def select_preferred_identifier(*sources: Any) -> Optional[str]:
    merged: Dict[str, str] = {}
    for source in sources:
        for key, value in extract_identifier_fields(source).items():
            merged.setdefault(key, value)

    for key in BUILDING_IDENTIFIER_PRIORITY:
        if merged.get(key):
            return merged[key]
    return None


def merge_identifier_metadata(metadata: Optional[Dict[str, Any]], *sources: Any) -> Dict[str, Any]:
    merged = dict(metadata or {})

    for source in sources:
        for key, value in extract_identifier_fields(source).items():
            merged.setdefault(key, value)

    return merged


def build_history_excerpt(messages: List[Dict[str, Any]], keep_last: Optional[int] = None) -> str:
    # The caller owns retention. Dropping earlier turns here loses the BRF's
    # objectives and constraints while retaining only recent clarification.
    selected = messages or []
    if keep_last is not None:
        selected = selected[-keep_last:] if keep_last > 0 else []
    excerpt = []
    for message in selected:
        if (not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}
                or not isinstance(message.get("content"), str)):
            continue
        excerpt.append(
            {
                "role": message.get("role"),
                "content": message.get("content"),
                "classification": message.get("classification"),
                "parsed_intent": message.get("parsed_intent"),
            }
        )
    return _json_text(excerpt)


def build_building_response_prompt(
    *,
    user_input: str,
    current_address: str,
    history: List[Dict[str, Any]],
    action_description: str,
    results: Any,
    metadata: Dict[str, Any],
    building_id: str,
) -> str:
    return PROMPT_TEMPLATE.format(
        user_input=user_input or "",
        current_address=current_address or "not available",
        building_id=building_id or "building_id_not_available",
        history=build_history_excerpt(history),
        action_description=action_description or "No specific action was recorded.",
        results=_json_text(results),
        # Keep operational caches, query traces and old duplicate field aliases
        # out of the model input. The summarizer supplies a scoped fact snapshot.
        metadata=_json_text({
            key: value for key, value in (metadata or {}).items()
            if key in {
                "byggnadsid", "building_id", "brf_name", "building_type", "address",
                "address_from_user", "requested_address", "epc_record_address",
                "same_building_multiple_addresses", "address_context_note",
                "building_identity_check", "retrieved_facts", "data_freshness",
                "evidence_scope", "uncertainty", "clarification",
            }
        }),
    )


def ensure_building_identifier_in_response(text: str, building_id: Optional[str]) -> str:
    identifier = _stringify(building_id)
    if not identifier or identifier == "building_id_not_available":
        return text

    if identifier.lower() in (text or "").lower():
        return text

    return f"Building ID: {identifier}\n\n{text}"
