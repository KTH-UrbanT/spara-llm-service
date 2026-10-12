# src/agents/building_agent.py
import re
import time
from typing import Any, Dict
from src.agents.building_flow_graph import (
    build_building_flow_graph,
    resolve_multi_address_identity_message,
)
from src.agents.building_response_prompt import (
    is_verified_address_explanation_followup,
    merge_identifier_metadata,
)
from src.pipeline.telemetry import record_component_latency
from src.pipeline.safety_analysis import response_language
from src.pipeline.evaluation_metadata import (
    has_verified_building_identity,
    looks_like_identity_request,
    needs_identity_clarification,
    with_clarification_progress,
)
from src.redis.redis_session_store import get_session_state, update_session_state
from src.services.source_link_registry import resolve_source_links


def _vector_source_keys(final_state: Dict[str, Any]) -> list:
    packed_vector = final_state.get("agent_data_vector") or {}
    if isinstance(packed_vector, dict) and packed_vector.get("sources"):
        return packed_vector.get("sources") or []

    agent_data = final_state.get("agent_data") or {}
    if isinstance(agent_data, dict):
        vector_data = agent_data.get("vector") or {}
        if isinstance(vector_data, dict) and vector_data.get("sources"):
            return vector_data.get("sources") or []

    return final_state.get("agent_vector_sources") or []


ADDRESS_DISAMBIGUATION_REQUEST_RE = re.compile(
    r"(?=.*\b(?:address|building|property|brf|adress|byggnad|fastighet)\b)"
    r"(?=.*\b(?:city|postcode|postal\s+code|municipality|postort|postnummer|kommun|"
    r"building\s+id|byggnadsid|brf)\b)"
    r".*\b(?:multiple|several|ambiguous|different|correct|which|choose|use|"
    r"flera|olika|rätt|vilken|välj|använd)\b",
    flags=re.IGNORECASE,
)


def _latest_session_state(session_state: Any) -> Dict[str, Any]:
    if isinstance(session_state, dict):
        return dict(session_state)
    if isinstance(session_state, list):
        for item in reversed(session_state):
            if isinstance(item, dict):
                return dict(item)
    return {}


def _direct_resolution_metadata(
    metadata: Dict[str, Any],
    session_state: Any,
) -> Dict[str, Any]:
    latest_state = _latest_session_state(session_state)
    latest_metadata = latest_state.get("metadata") if isinstance(latest_state.get("metadata"), dict) else {}
    merged = dict(latest_metadata or {})

    for key in (
        "brf_name",
        "brf_resolution",
        "pending_brf_resolution",
        "brf_candidate_buildings",
        "selected_brf_building_id",
        "selected_brf_addresses",
        "selected_brf_lookup_address",
        "byggnadsid",
        "building_id_from_user",
        "selected_address_building_id",
    ):
        value = latest_state.get(key)
        if value not in (None, "", [], {}) and key not in merged:
            merged[key] = value

    for key, value in (metadata or {}).items():
        if value not in (None, "", [], {}):
            merged[key] = value

    brf_resolution = merged.get("brf_resolution")
    if isinstance(brf_resolution, dict):
        selected_building_id = brf_resolution.get("selected_building_id")
        if selected_building_id not in (None, "", [], {}):
            merged.setdefault("selected_brf_building_id", selected_building_id)
            merged.setdefault("byggnadsid", selected_building_id)
        selected_addresses = brf_resolution.get("selected_addresses")
        if selected_addresses not in (None, "", [], {}):
            merged.setdefault("selected_brf_addresses", selected_addresses)

    return merge_identifier_metadata(merged, latest_state)


def _metadata_without_unresolved_selection(metadata: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(metadata or {})
    for key in (
        "building_id",
        "byggnadsid",
        "selected_brf_building_id",
        "building_id_from_user",
        "selected_address_building_id",
        "retrieved_facts",
        "building_information",
        "aggregated_data",
        "agent_data",
        "agent_data_generic",
        "agent_data_specialized",
        "evidence_scope",
        "data_freshness",
        "sql_trace",
        "query_traces",
    ):
        cleaned.pop(key, None)
    building_match = cleaned.get("building_match")
    if isinstance(building_match, dict):
        building_match = dict(building_match)
        building_match.pop("building_id", None)
        building_match["match_confidence"] = "low"
        building_match["ambiguous"] = True
        cleaned["building_match"] = building_match
    identity = cleaned.get("building_identity_check")
    if isinstance(identity, dict) and identity.get("status") == "passed":
        identity = dict(identity)
        identity.pop("matched_building_id", None)
        identity.pop("matched_address", None)
        identity["status"] = "ambiguous" if "ambiguous" in str((cleaned.get("clarification") or {}).get("reason") or "") else "missing"
        cleaned["building_identity_check"] = identity
    return cleaned


def _normalize_response_metadata(metadata, route, messages, previous_metadata):
    normalized = dict(metadata or {})
    if route == "clarification":
        normalized = _metadata_without_unresolved_selection(normalized)
    elif has_verified_building_identity(normalized):
        clarification = dict(normalized.get("clarification") or {})
        if clarification.get("needed") and not needs_identity_clarification(normalized):
            question = clarification.get("question_asked")
            if question:
                normalized["diagnostic_question"] = question
        clarification.update(needed=False, resolved=True, reason=None, question_asked=None)
        normalized["clarification"] = clarification
    return with_clarification_progress(normalized, messages, previous_metadata)


def _persist_response_state(state, metadata, route):
    """Persist the same selected scope returned to the router and frontend."""
    state["metadata"] = metadata
    if route == "clarification":
        for key in (
            "building_id", "byggnadsid", "50a_uuid", "uuid", "oden_uuid", "01a_fnr",
            "selected_address_building_id", "selected_brf_building_id", "building_id_from_user",
            "retrieved_facts", "building_information", "aggregated_data", "agent_data",
            "agent_data_generic", "agent_data_specialized", "query_traces", "sql_trace",
            "evidence_scope", "data_freshness",
        ):
            state.pop(key, None)
    return state


class BuildingAgent:
    def __init__(self):
        self.building_flow =  ''

    @staticmethod
    def _derive_route(final_state: Dict[str, Any], response_text: str) -> str:
        metadata = final_state.get("metadata") if isinstance(final_state, dict) else {}
        if needs_identity_clarification(metadata or {}):
            return "clarification"
        if not has_verified_building_identity(metadata or {}) and looks_like_identity_request(response_text):
            return "clarification"

        if final_state.get("done_vector") and (
            final_state.get("done_generic_sql") or final_state.get("done_specialized_sql")
        ):
            return "combined"

        return "building_specific"

    def handle_building_query(
        self,
        last_message: str,
        messages: list,
        metadata: Dict[str, Any],
        thread_id: str,
        _session_state: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Entry point from AgentRouter when the user message is classified as 'building_specific'.
        Fetches and updates session state from Redis per thread_id and returns a standardized payload.

        Returns:
            {
              "content": str,                     # final text shown to user
              "agent_answered": str,              # 'sql' | 'vector' | 'sql+vector' | 'simulation' | 'other' | 'unknown'
              "classification": "building_specific",
              "intent": Optional[str],            # parsed intent label from ParseIntentAgent
              "agents_used": Optional[list],      # e.g., [{"type":"sql","name":"generic_sql"}, {"type":"vector","name":"vector_db"}]
              "vector_sources": Optional[list],   # deduped source identifiers from vector retrieval (if any)
            }
        """
        started_at = time.perf_counter()
        language = response_language(messages, last_message, metadata)
        empty_response = ("Jag kunde inte färdigställa svaret just nu. Försök igen om en stund."
                          if language == "sv" else "No output was generated.")
        session_state = _session_state if _session_state is not None else get_session_state(thread_id)
        latest_state = _latest_session_state(session_state)
        latest_metadata = latest_state.get("metadata") or {}
        previous_metadata = {**latest_metadata, **(metadata or {})}
        context_start = (metadata or {}).get("routing_context_start", 0)
        stored_context_start = latest_metadata.get("routing_context_start", 0)
        if (
            isinstance(context_start, int)
            and isinstance(stored_context_start, int)
            and context_start > stored_context_start
        ):
            # A failed lookup was dismissed since this snapshot. Incoming metadata
            # retains confirmed context; do not restore the abandoned request.
            session_state = {}

        initial_state = {
            "thread_id": thread_id,
            "messages": messages,
            "metadata": metadata or {},
            "last_message": last_message,
            "session_state": session_state,
        }

        resolution_metadata = _direct_resolution_metadata(metadata or {}, session_state)
        verified_address_explanation = is_verified_address_explanation_followup(
            last_message, resolution_metadata,
        )
        if verified_address_explanation:
            # The recorded identity already supports this relationship. Preserve
            # its original request and let the graph explain it, without fresh
            # address lookups that could replace the verified selected building.
            initial_state["metadata"] = resolution_metadata
        direct_multi_address_state = (
            None if verified_address_explanation else resolve_multi_address_identity_message(
                last_message, resolution_metadata,
            )
        )
        direct_metadata = (direct_multi_address_state or {}).get("metadata") or {}
        parse_pending_multi_address = bool(
            direct_metadata.get("address_references")
            and isinstance(direct_metadata.get("pending_address_query"), dict)
            and not (direct_multi_address_state or {}).get("cached_candidate_answer")
        )
        if (
            direct_multi_address_state
            and direct_multi_address_state.get("multi_address_resolution_complete", True)
            and not parse_pending_multi_address
        ):
            if context_start:
                direct_multi_address_state["metadata"] = {
                    **(direct_multi_address_state.get("metadata") or {}),
                    "routing_context_start": context_start,
                }
            response = direct_multi_address_state.get("final_response") or empty_response
            md = direct_multi_address_state.get("metadata") or {}
            route = "clarification" if needs_identity_clarification(md) else "building_specific"
            metadata_payload = (
                _metadata_without_unresolved_selection(md)
                if route == "clarification"
                else merge_identifier_metadata(md, direct_multi_address_state)
            )
            metadata_payload = _normalize_response_metadata(metadata_payload, route, messages, previous_metadata)
            update_session_state(thread_id, _persist_response_state(direct_multi_address_state, metadata_payload, route))
            payload = {
                "content": response,
                "classification": "building_specific",
                "route": route,
                "parsed_intent": None,
                "intent_list": [],
                "agent_answered": ["ODEN API"],
            }
            record_component_latency("building_agent", time.perf_counter() - started_at)
            return payload, metadata_payload

        if direct_multi_address_state:
            # Parse the original facts/advice request after identity resolution,
            # including pending choices, so every part survives the follow-up.
            resolved_metadata = dict(direct_multi_address_state.get("metadata") or {})
            if context_start:
                resolved_metadata["routing_context_start"] = context_start
            initial_state["metadata"] = resolved_metadata
            initial_state["multi_address_resolution_checked"] = True
            initial_state["multi_address_resolution_complete"] = direct_multi_address_state.get("multi_address_resolution_complete", True)
            if parse_pending_multi_address:
                initial_state["final_response"] = direct_multi_address_state.get("final_response") or empty_response
            # Replace the snapshot metadata so omitted stale identity/facts
            # cannot be restored when the graph merges session context.
            resolved_session = _latest_session_state(session_state)
            resolved_session["metadata"] = dict(resolved_metadata)
            for key in (
                "byggnadsid", "building_id", "50a_uuid", "uuid", "oden_uuid", "01a_fnr",
                "building_id_from_user", "selected_address_building_id", "selected_brf_building_id",
                "retrieved_facts", "building_match", "building_information", "aggregated_data", "agent_data",
                "agent_data_generic", "agent_data_specialized", "evidence_scope", "data_freshness",
                "query_traces", "generic_sql_trace", "sql_trace",
            ):
                resolved_session.pop(key, None)
            initial_state["session_state"] = resolved_session

        self.graph = build_building_flow_graph()
        try:
            #print(initial_state)
            final_state = self.graph.invoke(initial_state)
        except Exception as e:
            record_component_latency("building_agent", time.perf_counter() - started_at)
            return {
                "content": ("Jag kunde inte hämta byggnadsuppgifterna just nu. Försök igen om en stund."
                            if language == "sv" else f"An error occurred while handling your building-related request: {e}"),
                "agent_answered": "unknown",
                "classification": "building_specific",
                "parsed_intent": None,
                "agent_answered": [],
                "vector_sources": [],
            } , metadata

        # Persist (possibly updated) session state
        # new_state = final_state.get("session_state", session_state)
        if context_start:
            final_state["metadata"] = {
                **(final_state.get("metadata") or {}),
                "routing_context_start": context_start,
            }

        # Compose response content
        response = (
            final_state.get("final_response")
            or final_state.get("response")
            or final_state.get("prompt")
            or empty_response
        )

        # Extract diagnostics
        final_state = final_state or {}
        ctx = final_state.get("context") or {}
        md = final_state.get("metadata") or {}

        parsed_intent = ctx.get("parsed_intent")            # keep original type if present
        intent_list = ctx.get("intent_list") or []          # empty list if missing

        agents_used = []
        if final_state.get('done_generic_sql') == True:
            agents_used.append('ODEN API')
        if final_state.get('done_specialized_sql') == True:
            agents_used.append('Hammarby dataset used')
        if final_state.get('done_vector') == True:
            agents_used.append('Documents stored in Vector database used')

        agent_answered = ((md.get('debug') or {}).get('agent_answered')) or ""  # empty string if missing
        source_keys = _vector_source_keys(final_state)
        sources = resolve_source_links(source_keys)
        route = self._derive_route(final_state, response)
        print(response)

        if route == "clarification":
            metadata_payload = _metadata_without_unresolved_selection(md)
        else:
            metadata_payload = merge_identifier_metadata(
                md,
                final_state.get("aggregated_data"),
                final_state.get("agent_data"),
                final_state.get("session_state"),
            )
        if route != "clarification" and final_state.get("aggregated_data"):
            metadata_payload["aggregated_data"] = final_state.get("aggregated_data")
        if route != "clarification" and final_state.get("agent_data"):
            metadata_payload["agent_data"] = final_state.get("agent_data")
        if ctx:
            metadata_payload["context"] = ctx
        if route == "clarification":
            existing_clarification = md.get("clarification") or {}
            clarification_reason = existing_clarification.get("reason") or (
                "ambiguous_brf"
                if ctx.get("ambiguous") or ctx.get("ambigious")
                else "missing_address"
            )
            metadata_payload["clarification"] = {
                "needed": True,
                "reason": clarification_reason,
                "question_asked": existing_clarification.get("question_asked") or response,
                "resolved": False,
                "resolved_after_turns": existing_clarification.get("resolved_after_turns"),
            }

        metadata_payload = _normalize_response_metadata(metadata_payload, route, messages, previous_metadata)
        update_session_state(thread_id, _persist_response_state(final_state, metadata_payload, route))

        payload = {
            "content": response,
            "classification": "building_specific",     # router-level classification
            "route": route,
            "parsed_intent": parsed_intent,   # parsed intent label
            'intent_list' : intent_list , 
            "agent_answered": agents_used,        # helpful for hybrid/vector QA
        }
        if sources:
            payload["sources"] = sources

        record_component_latency("building_agent", time.perf_counter() - started_at)
        return payload , metadata_payload
