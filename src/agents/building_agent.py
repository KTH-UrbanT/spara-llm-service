# src/agents/building_agent.py
from typing import Any, Dict
from src.agents.building_flow_graph import build_building_flow_graph
from src.agents.building_response_prompt import merge_identifier_metadata
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
    return []


class BuildingAgent:
    def __init__(self):
        self.building_flow =  ''

    @staticmethod
    def _derive_route(final_state: Dict[str, Any], response_text: str) -> str:
        lowered = (response_text or "").strip().lower()
        if "provide the building address" in lowered or "could you clarify your request" in lowered:
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
        session_state = _session_state 

        initial_state = {
            "thread_id": thread_id,
            "messages": messages,
            "metadata": metadata or {},
            "last_message": last_message,
            "session_state": session_state,
        }
        self.graph = build_building_flow_graph()
        try:
            #print(initial_state)
            final_state = self.graph.invoke(initial_state)
        except Exception as e:
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

        metadata_payload = merge_identifier_metadata(
            md,
            final_state.get("aggregated_data"),
            final_state.get("agent_data"),
            final_state.get("session_state"),
        )
        if final_state.get("aggregated_data"):
            metadata_payload["aggregated_data"] = final_state.get("aggregated_data")
        if final_state.get("agent_data"):
            metadata_payload["agent_data"] = final_state.get("agent_data")
        if ctx:
            metadata_payload["context"] = ctx
        if route == "clarification":
            clarification_reason = (
                "ambiguous_brf"
                if ctx.get("ambiguous") or ctx.get("ambigious")
                else "missing_address"
            )
            metadata_payload["clarification"] = {
                "needed": True,
                "reason": clarification_reason,
                "question_asked": response,
                "resolved": False,
                "resolved_after_turns": None,
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

        return payload , metadata_payload
