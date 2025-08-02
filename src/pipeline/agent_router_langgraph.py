# src/pipeline/agent_router.py

from src.pipeline.state import RouterState

# src/pipeline/agent_router_graph.py

from langgraph.graph import StateGraph, END
from src.pipeline.state import RouterState
from src.agents.router_agent import RouterAgent
from src.agents.building_agent import BuildingAgent
from src.agents.cluster_agent import ClusterAgent
from src.agents.generic_agent import GenericAgent
from src.agents.aggregator_agent import AggregatorAgent
from src.agents.conversationalist_agent import ConversationalAgent

# Instantiate agents
router = RouterAgent()
building = BuildingAgent()
cluster = ClusterAgent()
generic = GenericAgent()
aggregator = AggregatorAgent()
conversationalist = ConversationalAgent()

# Nodes
def classify(state: RouterState) -> RouterState:
    previous_classification = None
    if len(state['messages']) > 1:
        previous_classification = state['messages'][-2].get('classification')
    classification = router.classify_question(state['last_message'], previous_classification)
    state['classification'] = classification
    return state

def handle_generic(state: RouterState) -> RouterState:
    result = generic.handle_generic_input(state['last_message'], state['messages'])
    state['agent_response'] = {
        "content": result,
        "classification": "generic",
        "agent_answered": "generic"
    }
    return state

def handle_building(state: RouterState) -> RouterState:
    result = building.handle_building_query(state['last_message'], state['messages'], state['metadata'], state['thread_id'])
    state['agent_response'] = result
    return state

def handle_cluster(state: RouterState) -> RouterState:
    result = cluster.handle_cluster_query(state['last_message'], state['messages'], state['metadata'], state['thread_id'])
    state['agent_response'] = result
    return state

def handle_conversational(state: RouterState) -> RouterState:
    result = conversationalist.handle_conversational_input(state['last_message'], state['messages'])
    state['agent_response'] = {
        "content": result,
        "classification": "conversational",
        "agent_answered": "conversationalist"
    }
    return state

# Router logic
def route(state: RouterState) -> str:
    return state['classification']

# Build LangGraph
def build_router_graph():
    builder = StateGraph(RouterState)

    builder.add_node("classify", classify)
    builder.add_node("generic", handle_generic)
    builder.add_node("building", handle_building)
    builder.add_node("cluster", handle_cluster)
    builder.add_node("conversational", handle_conversational)

    builder.set_entry_point("classify")
    builder.add_conditional_edges(
        "classify",
        route,
        {
            "generic": "generic",
            "building_specific": "building",
            "cluster": "cluster",
            "conversational": "conversational",
        },
    )

    # All agent handlers go to END
    builder.add_edge("generic", END)
    builder.add_edge("building", END)
    builder.add_edge("cluster", END)
    builder.add_edge("conversational", END)

    return builder.compile()


class AgentRouter:
    def __init__(self):
        # Build the LangGraph graph at init
        self.graph = build_router_graph()

    def route_message(self, messages, last_message, metadata, thread_id):
        # Define the input state
        state: RouterState = {
            "messages": messages,
            "last_message": last_message,
            "metadata": metadata,
            "thread_id": thread_id,
            "classification": None,
            "agent_response": None
        }

        # Run the LangGraph
        result = self.graph.invoke(state)

        # Return the agent's response (preserving original return format)
        return result["agent_response"]
