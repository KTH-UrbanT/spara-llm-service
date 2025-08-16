# src/agents/building_agent.py
from typing import Any, Dict
from src.agents.building_flow_graph import build_building_flow_graph
from src.redis.redis_session_store import get_session_state, update_session_state


class BuildingAgent:
    def __init__(self):
        self.graph = build_building_flow_graph()

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
        session_state = _session_state or get_session_state(thread_id)

        initial_state = {
            "thread_id": thread_id,
            "messages": messages,
            "metadata": metadata or {},
            "last_message": last_message,
            "session_state": session_state,
        }

        try:
            final_state = self.graph.invoke(initial_state)
        except Exception as e:
            return {
                "content": f"An error occurred while handling your building-related request: {e}",
                "agent_answered": "unknown",
                "classification": "building_specific",
                "intent": None,
                "agents_used": [],
                "vector_sources": [],
            }

        # Persist (possibly updated) session state
        new_state = final_state.get("session_state", session_state)
        update_session_state(thread_id, new_state)

        # Compose response content
        response = (
            final_state.get("final_response")
            or final_state.get("response")
            or final_state.get("prompt")
            or "No output was generated."
        )

        # Extract diagnostics
        ctx = final_state.get("context") or {}
        md = final_state.get("metadata") or {}
        intent = ctx.get("intent")
        agent_answered = final_state.get("agent_answered") or md.get("debug", {}).get("agent_answered") or "unknown"
        agents_used = md.get("agents_used") or []
        vector_sources = md.get("vector_sources") or []

        return {
            "content": response,
            "agent_answered": agent_answered,          # 'sql' | 'vector' | 'sql+vector' | 'simulation' | 'other' | 'unknown'
            "classification": "building_specific",     # router-level classification
            "intent": intent,                          # parsed intent label
            "agents_used": agents_used,                # detailed contributors
            "vector_sources": vector_sources,          # helpful for hybrid/vector QA
        }
