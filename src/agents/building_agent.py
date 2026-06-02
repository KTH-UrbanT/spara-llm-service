# src/agents/building_agent.py
import re
import time
from typing import Any, Dict
from src.agents.building_flow_graph import (
    build_building_flow_graph,
    resolve_multi_address_identity_message,
)
from src.agents.building_response_prompt import merge_identifier_metadata
from src.pipeline.telemetry import record_component_latency
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
        "retrieved_facts",
        "aggregated_data",
        "agent_data",
    ):
        cleaned.pop(key, None)
    building_match = cleaned.get("building_match")
    if isinstance(building_match, dict):
        building_match = dict(building_match)
        building_match.pop("building_id", None)
        building_match["match_confidence"] = "low"
        building_match["ambiguous"] = True
        cleaned["building_match"] = building_match
    return cleaned


class BuildingAgent:
    def __init__(self):
        self.building_flow =  ''

    @staticmethod
    def _derive_route(final_state: Dict[str, Any], response_text: str) -> str:
        metadata = final_state.get("metadata") if isinstance(final_state, dict) else {}
        clarification = metadata.get("clarification") if isinstance(metadata, dict) else {}
        if isinstance(clarification, dict) and clarification.get("needed"):
            return "clarification"

        lowered = (response_text or "").strip().lower()
        if (
            "provide the building address" in lowered
            or "full building address" in lowered
            or "full street address" in lowered
            or "need the full building address" in lowered
            or "could you clarify your request" in lowered
            or ADDRESS_DISAMBIGUATION_REQUEST_RE.search(response_text or "")
        ):
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
        session_state = _session_state if _session_state is not None else get_session_state(thread_id)

        initial_state = {
            "thread_id": thread_id,
            "messages": messages,
            "metadata": metadata or {},
            "last_message": last_message,
            "session_state": session_state,
        }

        direct_multi_address_state = resolve_multi_address_identity_message(
            last_message,
            _direct_resolution_metadata(metadata or {}, session_state),
        )
        if direct_multi_address_state:
            update_session_state(thread_id, direct_multi_address_state)
            response = direct_multi_address_state.get("final_response") or "No output was generated."
            md = direct_multi_address_state.get("metadata") or {}
            route = "clarification" if (md.get("clarification") or {}).get("needed") else "building_specific"
            metadata_payload = (
                _metadata_without_unresolved_selection(md)
                if route == "clarification"
                else merge_identifier_metadata(md, direct_multi_address_state)
            )
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

        self.graph = build_building_flow_graph()
        try:
            #print(initial_state)
            final_state = self.graph.invoke(initial_state)
        except Exception as e:
            record_component_latency("building_agent", time.perf_counter() - started_at)
            return {
                "content": f"An error occurred while handling your building-related request: {e}",
                "agent_answered": "unknown",
                "classification": "building_specific",
                "parsed_intent": None,
                "agent_answered": [],
                "vector_sources": [],
            } , metadata

        # Persist (possibly updated) session state
        # new_state = final_state.get("session_state", session_state)
        update_session_state(thread_id, final_state)

        # Compose response content
        response = (
            final_state.get("final_response")
            or final_state.get("response")
            or final_state.get("prompt")
            or "No output was generated."
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
                "resolved": existing_clarification.get("resolved", False),
                "resolved_after_turns": existing_clarification.get("resolved_after_turns"),
            }

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
