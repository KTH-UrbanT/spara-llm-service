# src/agents/building_agent.py
from src.agents.building_flow_graph import build_building_flow_graph
from src.redis.redis_session_store import get_session_state, update_session_state

class BuildingAgent:
    def __init__(self):
        self.graph = build_building_flow_graph()

    def handle_building_query(self, last_message, messages, metadata, thread_id, _session_state=None):
        """
        Entry point from AgentRouter when the user message is classified as 'building_specific'.
        Fetches and updates session state from Redis per thread_id.
        """
        session_state = _session_state or get_session_state(thread_id)

        initial_state = {
            "thread_id": thread_id,
            "messages": messages,
            "metadata": metadata,
            "last_message": last_message,
            "session_state": session_state,
        }

        try:
            final_state = self.graph.invoke(initial_state)
        except Exception as e:
            return {
                "content": f"An error occurred while handling your building-related request: {e}",
                "agent_answered": "building",
                "classification": "building_specific"
            }

        # Save session_state back to Redis
        new_state = final_state.get("session_state", session_state)
        update_session_state(thread_id, new_state)

        response = (
            final_state.get("final_response")
            or final_state.get("response")
            or final_state.get("prompt")
            or "No output was generated."
        )

        return {
            "content": response,
            "agent_answered": "building",
            "classification": "building_specific"
        }
