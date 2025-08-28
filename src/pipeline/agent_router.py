# src/pipeline/agent_router.py
from typing import Any, Dict, List, Optional

from src.agents.router_agent import RouterAgent
from src.agents.building_agent import BuildingAgent
from src.agents.cluster_agent import ClusterAgent
from src.agents.generic_agent import GenericAgent
from src.agents.aggregator_agent import AggregatorAgent  # imported if you use it elsewhere
from src.agents.conversationalist_agent import ConversationalAgent


def _normalize_response(
    *,
    content: str,
    classification: str,
    agent_answered: str,
    intent: Optional[str] = None,
    agents_used: Optional[List[Dict[str, Any]]] = None,
    vector_sources: Optional[List[Any]] = None,
    metadata : Dict
) -> Dict[str, Any]:
    """
    Ensure a consistent return schema across all routes.
    """
    return {
        "content": content,
        "classification": classification,
        "agent_answered": agent_answered,
        "parsed_intent": intent,                               # None when not applicable
        "agents_used": agents_used or [],               # [] when not applicable
        "vector_sources": vector_sources or [],         # [] when not applicable
    } , metadata


class AgentRouter:
    def __init__(self):
        self.router = RouterAgent()
        self.building = BuildingAgent()
        self.generic = GenericAgent()
        self.cluster = ClusterAgent()
        self.conversationallist = ConversationalAgent()

    def route_message(self, messages, last_message, metadata, thread_id) -> Dict[str, Any]:
        # Try to read prior classification if present
        if len(messages) != 1:
            previous_classification = (messages[-2] or {}).get("classification")
        else:
            previous_classification = None

        classified = self.router.classify_question(last_message, previous_classification)

        # If user switched from building_specific to generic, confirm their intent
        if classified == "generic" and previous_classification == "building_specific":
            return {
                'role' : 'assistant' , 
                'content' : "Would you like building-specific advice or generic advice?" , 
                'classification' : classified 
            } , metadata
            # return _normalize_response(
            #     content="Would you like building-specific advice or generic advice?",
            #     classification=classified,
            #     agent_answered="uncertain",
            #     intent=None,
            #     metadata = metadata
            # )

        if classified == "generic":
            response_text = self.generic.handle_generic_input(last_message, messages)
            return {
                'role' : 'assistant' , 
                'content' :response_text , 
                'classification' : classified  , 
                'agent_answered' : "generic"
            } , metadata
            # return _normalize_response(
            #     content=response_text,
            #     classification=classified,
            #     agent_answered="generic",
            #     intent=None,
            #     metadata = metadata
            # )

        elif classified == "building_specific":
            # BuildingAgent already returns normalized diagnostics (intent, agent_answered, etc.)
            out , metadata_updated  = self.building.handle_building_query(last_message, messages, metadata, thread_id)
            out['role'] = 'assistant'
            out['classification']=classified
            return out , metadata_updated
            # Ensure any missing keys are filled so schema stays consistent
            # return _normalize_response(
            #     content=out.get("content", "No output was generated."),
            #     classification="building_specific",
            #     agent_answered=out.get("agent_answered", "unknown"),
            #     intent=out.get("intent"),
            #     agents_used=out.get("agents_used"),
            #     vector_sources=out.get("vector_sources"),
            #     metadata=metadata_updated
            # )

        elif classified == "cluster":
            # Normalize whatever the ClusterAgent returns
            out  ,  metadata_updated = self.cluster.handle_cluster_query(last_message, messages, metadata, thread_id)
            out['role'] = 'assistant'
            out['classification']=classified 
            return out , metadata_updated

        elif classified == "conversational":
            response_text = self.conversationallist.handle_conversational_input(last_message, messages)
            return {
                'role' : 'assistant' , 
                'content' :response_text , 
                'classification' : classified  , 
                'agent_answered' : "conversationalist"
            } , metadata
            # return _normalize_response(
            #     content=response_text,
            #     classification=classified,
            #     agent_answered="conversationalist",
            #     intent=None,
            #     metadata = metadata
            # )

        # Fallback
        # return _normalize_response(
        #     content=f"Unknown classification: {classified}",
        #     classification=str(classified),
        #     agent_answered="unknown",
        #     intent=None,
        #     metadata = metadata
        # )
        return {
                'role' : 'assistant' , 
                'content' :f"Unknown classification: {classified}" , 
                'classification' :  str(classified) , 
                'agent_answered' : "unknown"
            } , metadata
