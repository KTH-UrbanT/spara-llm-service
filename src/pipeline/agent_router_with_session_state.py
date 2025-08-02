from src.agents.router_agent_llama import RouterAgent
from src.agents.building_agent import BuildingAgent
from src.agents.cluster_agent import ClusterAgent
from src.agents.generic_agent import GenericAgent
from src.agents.aggregator_agent import AggregatorAgent
from src.agents.conversationalist_agent import ConversationalAgent

class AgentRouter:
    def __init__(self):
        self.router = RouterAgent()
        self.building = BuildingAgent()
        self.cluster = ClusterAgent()
        self.generic = GenericAgent()
        self.aggregator = AggregatorAgent()
        self.conversationallist = ConversationalAgent()
        self.sessions = {}  # session state per thread_id

    def get_session_state(self, thread_id):
        if thread_id not in self.sessions:
            self.sessions[thread_id] = {
                "current_agent": None,
                "current_task": None,
                "building_address": None,
                "subtasks": [],
                "simulation": {}
            }
        return self.sessions[thread_id]

    def route_message(self, messages, last_message, metadata, thread_id):
        session_state = self.get_session_state(thread_id)

        # --- 1. Continue if already inside a known agent flow ---
        if session_state.get("current_agent") == "building":
            return self.building.handle_building_query(
                last_message, messages, metadata, thread_id, session_state
            )

        elif session_state.get("current_agent") == "cluster":
            return self.cluster.handle_cluster_query(
                last_message, messages, metadata, thread_id, session_state
            )

        # --- 2. Otherwise classify the new user message ---
        if len(messages) != 1:
            previous_classification = messages[-2].get('classification')
        else:
            previous_classification = None

        classified = self.router.classify_question(last_message, previous_classification)
        print(f"[Classification] {classified}")

        # --- 3. Route based on classification ---
        if classified == 'generic':
            response = self.generic.handle_generic_input(last_message, messages, session_state)
            session_state["current_agent"] = None
            return {
                'content': response,
                'classification': classified,
                'agent_answered': 'generic'
            }

        elif classified == 'building_specific':
            session_state["current_agent"] = "building"
            return self.building.handle_building_query(
                last_message, messages, metadata, thread_id, session_state
            )

        elif classified == 'cluster':
            session_state["current_agent"] = "cluster"
            return self.cluster.handle_cluster_query(
                last_message, messages, metadata, thread_id, session_state
            )

        elif classified == 'conversational':
            response = self.conversationallist.handle_conversational_input(
                last_message, messages, session_state
            )
            session_state["current_agent"] = None
            return {
                'content': response,
                'classification': classified,
                'agent_answered': 'conversationalist'
            }

        else:
            return {
                "error": f"Unknown classification: {classified}"
            }
