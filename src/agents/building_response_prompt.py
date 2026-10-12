import json
import re
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional
from src.pipeline.safety_analysis import response_language, response_language_instruction


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


def _address_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.casefold().split())


def verified_address_relationship(metadata: Dict[str, Any], building_id: Optional[str] = None) -> Dict[str, Any]:
    """Return only the saved requested-to-recorded relationship for a verified ID."""
    metadata = metadata or {}
    selected = str(building_id or metadata.get("byggnadsid") or metadata.get("building_id") or "")
    identity = metadata.get("building_identity_check") or {}
    requested = metadata.get("requested_address") or metadata.get("address_from_user")
    recorded = metadata.get("epc_record_address")
    if (not selected or identity.get("status") != "passed"
            or str(identity.get("matched_building_id") or "").casefold() != selected.casefold()
            or metadata.get("same_building_multiple_addresses") is not True
            or not _address_text(requested) or not _address_text(recorded)
            or _address_text(requested) == _address_text(recorded)):
        return {}
    # A relationship from the previous building must never explain a new ID.
    for source in (metadata, metadata.get("retrieved_facts") or {}):
        if any(str(source[key]).casefold() != selected.casefold()
               for key in ("byggnadsid", "building_id") if source.get(key)):
            return {}
    address_evidence = metadata.get("address_identity_evidence") or {}
    if address_evidence and str(address_evidence.get("selected_building_id") or "").casefold() != selected.casefold():
        return {}
    return {
        "selected_building_id": selected,
        "requested_address": requested,
        "energy_declaration_record_address": recorded,
        "verification": "The saved building identity check passed for the selected ID; its energy-declaration record uses the other address.",
        "scope": "This links the requested lookup to the selected building's recorded declaration address. It does not prove either street address is unique across buildings or municipalities.",
    }


def is_verified_address_explanation_followup(question: str, metadata: Dict[str, Any]) -> bool:
    """Keep an explanation of known addresses separate from a building switch."""
    relationship = verified_address_relationship(metadata)
    text = _address_text(question)
    if not relationship or not text:
        return False
    if re.search(r"\b(?:byt(?:a)?(?:\s+till)?|byta byggnad|g[åa] tillbaka|jag menade|r[äa]ttelse|v[äa]lj|anv[äa]nd|switch|instead|go back|i meant|correction|use|choose)\b", text):
        return False
    mentioned_ids = re.findall(r"\b\d{2}-\d{2}-[a-zåäö0-9:_-]+-\d+\b", text)
    if any(identifier != relationship["selected_building_id"].casefold() for identifier in mentioned_ids):
        return False
    if not re.search(r"\b(?:varf[öo]r|why|visas|shown|samma|same|adress(?:erna)?|addresses)\b", text):
        return False
    for key in ("requested_address", "energy_declaration_record_address"):
        address = _address_text(relationship[key])
        if not re.search(r"(?<!\w)" + re.escape(address) + r"(?!\w)", text):
            return False
    return True


def build_identity_evidence_context(metadata: Dict[str, Any], building_id: Optional[str] = None) -> Dict[str, Any]:
    """Expose compact identity provenance without other buildings' energy facts."""
    metadata = metadata or {}
    selected = str(building_id or metadata.get("selected_brf_building_id")
                   or metadata.get("byggnadsid") or metadata.get("building_id") or "")
    if not selected:
        return {}
    context = {}
    identity = metadata.get("building_identity_check") or {}
    if identity.get("status") == "passed" and str(identity.get("matched_building_id") or "") == selected:
        context["building_identity_check"] = identity
    address_evidence = metadata.get("address_identity_evidence") or {}
    if str(address_evidence.get("selected_building_id") or "") == selected:
        context["address_identity_evidence"] = address_evidence
    relationship = verified_address_relationship(metadata, selected)
    if relationship:
        context["verified_address_relationship"] = relationship
    resolution = metadata.get("brf_resolution") or {}
    candidates = metadata.get("brf_candidate_buildings") or []
    if (resolution.get("status") in {"resolved_by_user_selection", "resolved_unique_building"}
            and str(resolution.get("selected_building_id") or metadata.get("selected_brf_building_id") or "") == selected
            and any(isinstance(row, dict) and str(row.get("byggnadsid") or row.get("building_id") or "") == selected for row in candidates)):
        context["brf_lookup_evidence"] = {
            "source": "SPARA association-to-building register",
            "association_name": metadata.get("brf_name"),
            "selected_building_id": selected,
            "selection_status": resolution.get("status"),
            "returned_buildings": [
                {key: row[key] for key in ("byggnadsid", "building_id", "brf_name", "addresses", "fastighet") if key in row}
                for row in candidates if isinstance(row, dict)
            ],
            "scope": "Buildings returned for this association lookup, not a guarantee of a complete current portfolio. The separate energy-declaration query retrieves only the selected building.",
        }
    return context


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
    language = (metadata or {}).get("response_language")
    if language not in {"en", "sv"}:
        language = response_language(history, user_input, metadata)
    return response_language_instruction(language) + "\n\n" + PROMPT_TEMPLATE.format(
        user_input=user_input or "",
        current_address=current_address or "not available",
        building_id=building_id or "building_id_not_available",
        history=build_history_excerpt(history),
        action_description=action_description or "No specific action was recorded.",
        results=_json_text(results),
        # Keep operational caches, query traces and old duplicate field aliases
        # out of the model input. The summarizer supplies a scoped fact snapshot.
        metadata=_json_text({**{
            key: value for key, value in (metadata or {}).items()
            if key in {
                "byggnadsid", "building_id", "brf_name", "building_type", "address",
                "address_from_user", "requested_address", "epc_record_address",
                "same_building_multiple_addresses", "address_context_note",
                "address_identity_evidence",
                "building_identity_check", "retrieved_facts", "data_freshness",
                "evidence_scope", "uncertainty", "clarification",
                "request_parts",
                "multipart_response_format",
                "response_language",
            }
        }, **build_identity_evidence_context(metadata, building_id)}),
    )


def ensure_building_identifier_in_response(text: str, building_id: Optional[str], *, language: str = "en") -> str:
    identifier = _stringify(building_id)
    if not identifier or identifier == "building_id_not_available":
        return text

    if identifier.lower() in (text or "").lower():
        return text

    label = "Byggnadsid" if language == "sv" else "Building ID"
    return f"{label}: {identifier}\n\n{text}"
