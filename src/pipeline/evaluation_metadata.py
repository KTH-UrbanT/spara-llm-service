import os
import re
from typing import Any, Dict, List, Optional

from src.pipeline.safety_analysis import assess_boundary_safety


ROUTE_LABELS = {
    "generic",
    "building_specific",
    "combined",
    "clarification",
    "out_of_scope",
    "expert_handoff",
    "report_generation",
}

BUILDING_IDENTIFIER_KEYS = (
    "byggnadsid",
    "building_id",
    "selected_address_building_id",
    "selected_brf_building_id",
    "building_id_from_user",
)

SESSION_MEMORY_KEYS = (
    "address",
    "address_from_user",
    "address_location_hint",
    "address_city",
    "city",
    "postort",
    "municipality",
    "kommun",
    "postcode",
    "postal_code",
    "postnr",
    "pending_ambiguous_address",
    "address_references",
    "address_candidates",
    "multi_address_resolution",
    "address_identity_evidence",
    "building_candidate_matches",
    "pending_address_candidate_ids",
    "pending_address_query",
    "building_selection_error",
    "requested_address",
    "epc_record_address",
    "same_building_multiple_addresses",
    "address_context_note",
    "brf_name",
    "byggnadsid",
    "selected_brf_building_id",
    "selected_brf_addresses",
    "selected_brf_lookup_address",
    "building_id_from_user",
    "selected_address_building_id",
    "pending_brf_resolution",
    "brf_resolution",
    "brf_candidate_buildings",
    "pending_building_request",
    "pending_building_advice_request",
    "request_parts",
    "address_selection_evidence",
    "selected_address_lookup_address",
    "clarification_started_user_turn",
    "clarification_resolved_user_turn",
    "clarification_turn_count",
)

RESOLVED_BRF_STATUSES = {
    "resolved_by_user_selection",
    "resolved_unique_building",
}

CLARIFICATION_PATTERNS = {
    "missing_address": (
        "provide the building address",
        "provide the full address",
        "which address",
    ),
    "missing_brf_name": (
        "which brf",
        "which building",
        "what brf",
    ),
    "incomplete_question": (
        "could you clarify",
        "clarify your request",
    ),
}


def is_evaluation_mode() -> bool:
    value = str(os.getenv("EVALUATION_MODE", "false")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _public_building_identifier(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text == "building_id_not_available" or re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return None
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.IGNORECASE):
        return None
    return text


def has_verified_building_identity(metadata: Dict[str, Any]) -> bool:
    identity = (metadata or {}).get("building_identity_check") or {}
    return bool(
        isinstance(identity, dict)
        and identity.get("status") == "passed"
        and _public_building_identifier(identity.get("matched_building_id"))
    )


def needs_identity_clarification(metadata: Dict[str, Any]) -> bool:
    """A diagnostic follow-up does not make a confirmed identity ambiguous."""
    clarification = (metadata or {}).get("clarification") or {}
    if not isinstance(clarification, dict) or not clarification.get("needed"):
        return False
    if clarification.get("identity_required") is False:
        return False
    reason = str(clarification.get("reason") or "")
    if has_verified_building_identity(metadata) and reason in {
        "diagnostic_question", "diagnostic_followup", "missing_measurements",
        "missing_operational_data", "incomplete_advice_context",
    }:
        return False
    return True


def looks_like_identity_request(response_text: str) -> bool:
    """Support legacy standalone prompts, never classify long advisory prose."""
    text = (response_text or "").strip()
    if not text or len(text) > 450 or len(text.splitlines()) > 3:
        return False
    if re.search(r"\b(?:i\s+need|please\s+provide|jag\s+beh[öo]ver)\s+(?:the\s+)?full\s+(?:building|street)\s+address\b", text, re.IGNORECASE):
        return True
    if text.lower().startswith("could you clarify your request"):
        return True
    return bool(re.search(
        r"^(?:(?:please|to\s+[^.!?]{1,100},?|i\s+(?:need|require))\s+)?"
        r"(?:provide|enter|give|supply|confirm|choose|select|which|what\s+is|"
        r"ange|uppge|v[äa]lj|vilken|vilket)\b.*"
        r"\b(?:address|street\s+address|building\s+id|brf|municipality|city|postcode|"
        r"adress|byggnadsid|kommun|postort|postnummer)\b",
        text, re.IGNORECASE,
    ))


def with_clarification_progress(
    metadata: Dict[str, Any], messages: List[Dict[str, Any]],
    previous_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Track replies to an identity question using the full conversation history.

    This function is pure. A completed count is frozen until a new clarification
    starts. Without history, retain a supplied count instead of inventing replies.
    """
    result = dict(metadata or {})
    previous = previous_metadata or {}
    current = result.get("clarification")
    if not isinstance(current, dict):
        return result
    current = dict(current)
    result["clarification"] = current
    prior = previous.get("clarification") or {}
    user_turn = sum(isinstance(item, dict) and item.get("role") == "user" for item in messages or [])
    active = needs_identity_clarification(result)
    was_active = needs_identity_clarification(previous)

    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    start = count(result.get("clarification_started_user_turn"))
    if start is None:
        start = count(previous.get("clarification_started_user_turn"))
    stopped = count(result.get("clarification_resolved_user_turn"))
    if stopped is None:
        stopped = count(previous.get("clarification_resolved_user_turn"))
    if active:
        # An old completed start cannot count towards a new identity question.
        if stopped is not None and not was_active:
            start = None
        if start is None and user_turn:
            start = user_turn
        if start is not None:
            result["clarification_started_user_turn"] = start
        result.pop("clarification_resolved_user_turn", None)
        elapsed = max(0, user_turn - start) if user_turn and start is not None else count(result.get("clarification_turn_count"))
        if elapsed is None:
            elapsed = count(previous.get("clarification_turn_count")) or 0
        result["clarification_turn_count"] = elapsed
        current.update(needed=True, resolved=False, resolved_after_turns=None)
    elif start is not None:
        if user_turn and (was_active or stopped is None):
            stopped = user_turn
        if stopped is not None:
            result["clarification_resolved_user_turn"] = stopped
            elapsed = max(0, stopped - start)
        else:
            elapsed = count(current.get("resolved_after_turns"))
            if elapsed is None:
                elapsed = count(prior.get("resolved_after_turns"))
        result["clarification_started_user_turn"] = start
        if elapsed is not None:
            result["clarification_turn_count"] = elapsed
            current["resolved_after_turns"] = elapsed
        current.update(needed=False, resolved=True)
    return result


def _is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _first_present(*values: Any) -> Any:
    for value in values:
        if _is_present(value):
            return value
    return None


def _stringify(value: Any) -> Optional[str]:
    if not _is_present(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _normalize_source_list(sources: Any) -> List[Any]:
    if not isinstance(sources, list):
        return []
    normalized = []
    for item in sources:
        if _is_present(item):
            normalized.append(item)
    return normalized


def _extract_building_id(metadata: Dict[str, Any]) -> Optional[str]:
    metadata = metadata or {}
    if has_verified_building_identity(metadata):
        return metadata["building_identity_check"]["matched_building_id"].strip()

    for key in BUILDING_IDENTIFIER_KEYS:
        value = _public_building_identifier(metadata.get(key))
        if value:
            return value

    aggregated = metadata.get("aggregated_data") or {}
    if isinstance(aggregated, dict):
        for nested in aggregated.values():
            if isinstance(nested, dict):
                for key in BUILDING_IDENTIFIER_KEYS:
                    value = _public_building_identifier(nested.get(key))
                    if value:
                        return value

    return None


def _extract_retrieved_facts(metadata: Dict[str, Any]) -> Dict[str, Any]:
    metadata = metadata or {}
    explicit = metadata.get("retrieved_facts")
    if isinstance(explicit, dict):
        # An empty scoped snapshot is authoritative after a failed refresh.
        # Do not rebuild it from older aggregated/session data.
        identity = metadata.get("building_identity_check") or {}
        if identity:
            selected = str(identity.get("matched_building_id") or "").strip().casefold()
            fact_ids = [str(explicit[key]).strip().casefold() for key in ("byggnadsid", "building_id") if explicit.get(key) not in (None, "")]
            if identity.get("status") != "passed" or not selected or not fact_ids or any(value != selected for value in fact_ids):
                return {}
        return explicit
    facts: Dict[str, Any] = {}

    aggregated = metadata.get("aggregated_data") or {}
    if isinstance(aggregated, dict):
        for section_name, section_payload in aggregated.items():
            if section_name == "vector" or not _is_present(section_payload):
                continue
            if isinstance(section_payload, dict):
                for key, value in section_payload.items():
                    if _is_present(value):
                        facts[key] = value
            else:
                facts[section_name] = section_payload

    if not facts:
        building_information = metadata.get("building_information")
        if isinstance(building_information, dict):
            for key, value in building_information.items():
                if _is_present(value):
                    facts[key] = value
        elif isinstance(building_information, list) and len(building_information) == 1:
            first = building_information[0]
            if isinstance(first, dict):
                for key, value in first.items():
                    if _is_present(value):
                        facts[key] = value

    for key in ("address", "address_from_user", *BUILDING_IDENTIFIER_KEYS):
        value = metadata.get(key)
        if _is_present(value):
            facts.setdefault(key, value)

    return facts


def _extract_vector_sources(response: Dict[str, Any], metadata: Dict[str, Any]) -> List[Any]:
    from src.services.source_link_registry import resolve_source_links

    def checked(sources):
        # Historical retrieval metadata can contain URLs even when the current
        # agent supplied no source list. Apply the same catalog rule here.
        if any((isinstance(item, str) and item.lower().startswith(("http://", "https://")))
               or (isinstance(item, dict) and (item.get("link") or item.get("url"))) for item in sources):
            return resolve_source_links(sources)
        return sources

    response_sources = _normalize_source_list(response.get("sources"))
    if response_sources:
        return checked(response_sources)

    vector_sources = _normalize_source_list(response.get("vector_sources"))
    if vector_sources:
        return checked(vector_sources)

    aggregated = metadata.get("aggregated_data") or {}
    if isinstance(aggregated, dict):
        vector_payload = aggregated.get("vector") or {}
        if isinstance(vector_payload, dict):
            sources = _normalize_source_list(vector_payload.get("sources"))
            if sources:
                return checked(sources)

    return []


def _looks_like_clarification(response_text: str) -> bool:
    return looks_like_identity_request(response_text)


def infer_route_label(response: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    explicit = response.get("route")
    if explicit in ROUTE_LABELS:
        return explicit

    classification = str(response.get("classification") or "").strip().lower()
    if classification == "draft_energy_report":
        return "report_generation"
    if classification == "expert_handoff":
        return "expert_handoff"
    if classification == "out_of_scope":
        return "out_of_scope"
    if classification in {"generic", "conversational"}:
        return "generic"
    if classification == "cluster":
        return "combined"
    if classification == "building_specific":
        if needs_identity_clarification(metadata):
            return "clarification"
        if not has_verified_building_identity(metadata) and _looks_like_clarification(response.get("content") or ""):
            return "clarification"
        if _extract_vector_sources(response, metadata) and _extract_retrieved_facts(metadata):
            return "combined"
        return "building_specific"
    return "generic"


def _infer_clarification_reason(response_text: str, metadata: Dict[str, Any]) -> Optional[str]:
    lowered = (response_text or "").strip().lower()
    context = metadata.get("context") or {}
    if context.get("ambiguous") or context.get("ambigious"):
        return "ambiguous_brf"

    for reason, patterns in CLARIFICATION_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return reason
    return None


def _build_building_match(metadata: Dict[str, Any]) -> Dict[str, Any]:
    explicit = metadata.get("building_match")
    if has_verified_building_identity(metadata) and not needs_identity_clarification(metadata):
        identity = metadata["building_identity_check"]
        matched = dict(explicit) if isinstance(explicit, dict) else {}
        input_address = _first_present(metadata.get("address_from_user"), metadata.get("address"))
        matched.update(
            building_id=identity["matched_building_id"],
            matched_address=_first_present(identity.get("matched_address"), matched.get("matched_address"), input_address),
            match_confidence="high" if input_address else "medium", ambiguous=False,
        )
        if input_address:
            matched["input_address"] = input_address
        return {key: value for key, value in matched.items() if _is_present(value) or isinstance(value, bool)}
    if isinstance(explicit, dict) and explicit:
        return explicit
    building_id = _extract_building_id(metadata)
    input_address = _first_present(
        metadata.get("address_from_user"),
        metadata.get("address"),
    )
    ambiguous = bool((metadata.get("context") or {}).get("ambiguous") or (metadata.get("context") or {}).get("ambigious"))

    if building_id and input_address and not ambiguous:
        match_confidence = "high"
    elif building_id and not ambiguous:
        match_confidence = "medium"
    elif input_address:
        match_confidence = "low"
    else:
        match_confidence = "unknown"

    payload = {
        "input_address": input_address,
        "matched_address": input_address,
        "building_id": building_id,
        "match_confidence": match_confidence,
        "ambiguous": ambiguous,
    }
    return {key: value for key, value in payload.items() if _is_present(value) or isinstance(value, bool)}


def _build_clarification_metadata(route: str, response_text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    explicit = metadata.get("clarification")
    if route != "clarification":
        resolved_turns = 0
        if isinstance(explicit, dict):
            value = explicit.get("resolved_after_turns")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                resolved_turns = value
        progress = metadata.get("clarification_turn_count")
        if isinstance(progress, int) and not isinstance(progress, bool) and progress >= 0:
            resolved_turns = progress
        return {
            "needed": False,
            "reason": None,
            "question_asked": None,
            "resolved": True,
            "resolved_after_turns": resolved_turns,
        }
    if isinstance(explicit, dict) and explicit:
        return explicit
    reason = _infer_clarification_reason(response_text, metadata)
    return {
        "needed": route == "clarification",
        "reason": reason,
        "question_asked": response_text if route == "clarification" else None,
        "resolved": route != "clarification",
        "resolved_after_turns": 0 if route != "clarification" else None,
    }


def _build_boundary_metadata(route: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
    boundary = metadata.get("boundary_handling") or {}
    out_of_scope_type = boundary.get("out_of_scope_type") or metadata.get("out_of_scope_type")
    out_of_scope = route == "out_of_scope" or bool(boundary.get("out_of_scope")) or bool(metadata.get("out_of_scope"))
    redirect_to = boundary.get("redirect_to") or metadata.get("redirect_to")
    if route == "expert_handoff" and not redirect_to:
        redirect_to = "ekr_advisor"

    return {
        "out_of_scope": out_of_scope,
        "out_of_scope_type": out_of_scope_type,
        "redirect_to": redirect_to,
        "safe_general_information_provided": boundary.get("safe_general_information_provided"),
    }


def _build_model_config() -> Dict[str, Any]:
    payload = {
        "evaluation_mode": is_evaluation_mode(),
        "router_model_name": os.getenv("ROUTER_MODEL_DEPLOYMENT_NAME"),
        "generic_model_name": os.getenv("GENERIC_MODEL_DEPLOYMENT_NAME"),
        "conversational_model_name": os.getenv("CONVERSATIONAL_MODEL_DEPLOYMENT_NAME"),
    }
    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, bool) or _is_present(value)
    }


def _normalize_agent_name(classification: str, raw_agent: Any) -> str:
    if isinstance(raw_agent, str) and raw_agent.strip().lower() in {"router", "routeragent"}:
        return "RouterAgent"
    mapping = {
        "generic": "GenericAgent",
        "building_specific": "BuildingAgent",
        "cluster": "ClusterAgent",
        "conversational": "ConversationalAgent",
        "draft_energy_report": "ReportAgent",
        "expert_handoff": "ExpertHandoffAgent",
        "out_of_scope": "BoundaryAgent",
    }
    if classification in mapping:
        return mapping[classification]

    if isinstance(raw_agent, str) and raw_agent.strip():
        return raw_agent.strip()

    return "UnknownAgent"


def _has_resolved_brf_selection(metadata: Dict[str, Any]) -> bool:
    metadata = metadata or {}
    resolution = metadata.get("brf_resolution")
    status = resolution.get("status") if isinstance(resolution, dict) else None
    return bool(
        status in RESOLVED_BRF_STATUSES
        or metadata.get("selected_brf_building_id")
    )


def build_message_metadata(response: Dict[str, Any], session_metadata: Dict[str, Any]) -> Dict[str, Any]:
    response = response or {}
    session_metadata = session_metadata or {}
    classification = str(response.get("classification") or "").strip().lower()
    route = infer_route_label(response, session_metadata)
    clarification = _build_clarification_metadata(
        route,
        response.get("content") or "",
        session_metadata,
    )
    boundary_handling = _build_boundary_metadata(route, session_metadata)
    building_match = _build_building_match(session_metadata)
    building_id = _extract_building_id(session_metadata)
    vector_sources = _extract_vector_sources(response, session_metadata)
    retrieved_facts = _extract_retrieved_facts(session_metadata)
    building_evidence = route in {"building_specific", "combined", "report_generation"}
    if not building_evidence:
        retrieved_facts = {}
    safety_boundary = assess_boundary_safety(
        user_message=session_metadata.get("last_user_message") or "",
        response_text=response.get("content") or "",
        route=route,
        metadata=session_metadata,
    )

    payload = {
        "route": route,
        "agent": _normalize_agent_name(classification, response.get("agent_answered")),
        "classification": classification or None,
        "intent": response.get("parsed_intent") or response.get("intent"),
        "intent_list": response.get("intent_list") or [],
        "needs_clarification": clarification["needed"],
        "out_of_scope": boundary_handling["out_of_scope"],
        "out_of_scope_type": boundary_handling["out_of_scope_type"],
        "expert_handoff_triggered": route == "expert_handoff",
        "report_generation_triggered": route == "report_generation",
        "report_status": response.get("report_status") if route == "report_generation" else None,
        "evaluation_mode": is_evaluation_mode(),
        "building_id": building_id,
        "building_match": building_match,
        "building_identity_check": session_metadata.get("building_identity_check"),
        "retrieved_facts": retrieved_facts,
        "evidence_scope": session_metadata.get("evidence_scope") if building_evidence else None,
        "request_parts": session_metadata.get("request_parts"),
        "response_language": session_metadata.get("response_language"),
        "unanswered_request_parts": session_metadata.get("unanswered_request_parts"),
        "vector_sources": vector_sources,
        "sql_trace": session_metadata.get("sql_trace") if building_evidence else None,
        "query_traces": session_metadata.get("query_traces") if building_evidence else None,
        "data_freshness": session_metadata.get("data_freshness") if building_evidence else None,
        "uncertainty": session_metadata.get("uncertainty"),
        "clarification": clarification,
        "boundary_handling": boundary_handling,
        "safety_boundary": safety_boundary,
        "telemetry": session_metadata.get("telemetry"),
        "model_config": _build_model_config(),
    }
    if route == "expert_handoff":
        for key in (
            "expert_handoff_pending_confirmation", "expert_handoff_sent",
            "expert_handoff_status", "expert_handoff_simulated",
            "expert_handoff_cc_submitted",
            "expert_handoff_language",
        ):
            if key in session_metadata:
                payload[key] = session_metadata[key]
    for key in SESSION_MEMORY_KEYS:
        if key == "pending_brf_resolution" and _has_resolved_brf_selection(session_metadata):
            continue
        payload[key] = session_metadata.get(key)

    if clarification["needed"]:
        for key in (
            "building_id",
            "byggnadsid",
            "selected_brf_building_id",
            "building_id_from_user",
            "selected_address_building_id",
            "retrieved_facts",
        ):
            payload.pop(key, None)
        building_match = payload.get("building_match")
        if isinstance(building_match, dict):
            building_match = dict(building_match)
            building_match.pop("building_id", None)
            if building_match:
                building_match["match_confidence"] = "low"
                building_match["ambiguous"] = True
                payload["building_match"] = building_match
            else:
                payload.pop("building_match", None)

    return {
        key: value
        for key, value in payload.items()
        if isinstance(value, bool) or _is_present(value)
    }


def build_message_evidence(message_metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    message_metadata = message_metadata or {}
    evidence_entries = []
    for evidence_type in (
        "building_match",
        "building_identity_check",
        "sql_trace",
        "query_traces",
        "retrieved_facts",
        "vector_sources",
        "data_freshness",
        "uncertainty",
        "clarification",
        "boundary_handling",
        "safety_boundary",
        "model_config",
    ):
        payload = message_metadata.get(evidence_type)
        if not _is_present(payload):
            continue
        evidence_entries.append(
            {
                "evidence_type": evidence_type,
                "evidence_payload": payload,
            }
        )
    return evidence_entries
