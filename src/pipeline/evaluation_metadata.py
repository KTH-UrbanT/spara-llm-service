import os
from typing import Any, Dict, List, Optional


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
    "building_id",
    "byggnadsid",
    "50a_uuid",
    "uuid",
    "oden_uuid",
    "01a_fnr",
    "epc_idadr",
)

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
    for key in BUILDING_IDENTIFIER_KEYS:
        value = _stringify(metadata.get(key))
        if value:
            return value

    aggregated = metadata.get("aggregated_data") or {}
    if isinstance(aggregated, dict):
        for nested in aggregated.values():
            if isinstance(nested, dict):
                for key in BUILDING_IDENTIFIER_KEYS:
                    value = _stringify(nested.get(key))
                    if value:
                        return value

    return None


def _extract_retrieved_facts(metadata: Dict[str, Any]) -> Dict[str, Any]:
    metadata = metadata or {}
    explicit = metadata.get("retrieved_facts")
    if isinstance(explicit, dict) and explicit:
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
    response_sources = _normalize_source_list(response.get("sources"))
    if response_sources:
        return response_sources

    vector_sources = _normalize_source_list(response.get("vector_sources"))
    if vector_sources:
        return vector_sources

    aggregated = metadata.get("aggregated_data") or {}
    if isinstance(aggregated, dict):
        vector_payload = aggregated.get("vector") or {}
        if isinstance(vector_payload, dict):
            sources = _normalize_source_list(vector_payload.get("sources"))
            if sources:
                return sources

    return []


def _looks_like_clarification(response_text: str) -> bool:
    lowered = (response_text or "").strip().lower()
    if not lowered:
        return False

    for patterns in CLARIFICATION_PATTERNS.values():
        if any(pattern in lowered for pattern in patterns):
            return True
    return False


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
        if _looks_like_clarification(response.get("content") or ""):
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
        "evaluation_mode": is_evaluation_mode(),
        "building_id": building_id,
        "building_match": building_match,
        "building_identity_check": session_metadata.get("building_identity_check"),
        "retrieved_facts": retrieved_facts,
        "vector_sources": vector_sources,
        "sql_trace": session_metadata.get("sql_trace"),
        "query_traces": session_metadata.get("query_traces"),
        "data_freshness": session_metadata.get("data_freshness"),
        "uncertainty": session_metadata.get("uncertainty"),
        "clarification": clarification,
        "boundary_handling": boundary_handling,
        "model_config": _build_model_config(),
    }

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
