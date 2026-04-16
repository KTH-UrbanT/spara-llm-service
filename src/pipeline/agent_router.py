# src/pipeline/agent_router.py
from typing import Any, Dict, List, Optional

from src.agents.router_agent import RouterAgent
from src.agents.building_agent import BuildingAgent
from src.agents.cluster_agent import ClusterAgent
from src.agents.generic_agent import GenericAgent
from src.agents.aggregator_agent import AggregatorAgent  # imported if you use it elsewhere
from src.agents.conversationalist_agent import ConversationalAgent
from src.services.draft_report_service import generate_draft_report_response
from src.services.expert_handoff_email import send_expert_handoff_email


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
        base_metadata = dict(metadata or {})
        pending_handoff = bool(base_metadata.get("expert_handoff_pending_confirmation"))

        if pending_handoff and self.router.is_confirmation(last_message):
            updated_metadata = {
                **base_metadata,
                "expert_handoff_pending_confirmation": False,
            }
            try:
                was_sent = send_expert_handoff_email(thread_id, messages, updated_metadata)
                updated_metadata = {
                    **updated_metadata,
                    "expert_handoff_requested": True,
                    "expert_handoff_sent": bool(was_sent),
                }
                if was_sent:
                    return {
                        "role": "assistant",
                        "content": "The email was sent successfully to the EKR expert.",
                        "classification": "expert_handoff",
                        "agent_answered": "expert_handoff",
                    }, updated_metadata
            except Exception as exc:
                updated_metadata = {
                    **updated_metadata,
                    "expert_handoff_requested": True,
                    "expert_handoff_sent": False,
                    "expert_handoff_error": str(exc),
                }

            return {
                "role": "assistant",
                "content": "I could not send the email to the EKR expert right now. Please try again later.",
                "classification": "expert_handoff",
                "agent_answered": "expert_handoff",
            }, updated_metadata

        if pending_handoff and self.router.is_rejection(last_message):
            updated_metadata = {
                **base_metadata,
                "expert_handoff_pending_confirmation": False,
                "expert_handoff_sent": False,
            }
            return {
                "role": "assistant",
                "content": "Okay, I will not send the conversation to an expert. We can continue here.",
                "classification": "expert_handoff",
                "agent_answered": "expert_handoff",
            }, updated_metadata

        # Try to read prior classification if present
        if len(messages) != 1:
            previous_classification = (messages[-2] or {}).get("classification")
        else:
            previous_classification = None

        classified = self.router.classify_question(last_message, previous_classification)

        if classified == "draft_energy_report":
            return generate_draft_report_response(thread_id, messages, base_metadata), base_metadata

        if classified == "expert_handoff":
            updated_metadata = {
                **base_metadata,
                "expert_handoff_pending_confirmation": True,
            }
            return {
                "role": "assistant",
                "content": "I can email this conversation and the available session details to an EKR expert. Do you want me to send it?",
                "classification": "expert_handoff",
                "agent_answered": "expert_handoff",
            }, updated_metadata

        # If user switched from building_specific to generic, confirm their intent
        if classified == "generic" and previous_classification == "building_specific":
            # return {
            #     'role' : 'assistant' , 
            #     'content' : "Would you like building-specific advice or generic advice?" , 
            #     'classification' : classified 
            # } , metadata
            out , metadata_updated  = self.building.handle_building_query(last_message, messages, base_metadata, thread_id)
            out['role'] = 'assistant'
            out['classification'] = "building_specific"
            return out , metadata_updated

        if classified == "generic":
            response_text = self.generic.handle_generic_input(last_message, messages)
            return {
                'role' : 'assistant' , 
                'content' :response_text , 
                'classification' : classified  , 
                'agent_answered' : "generic"
            } , base_metadata
            # return _normalize_response(
            #     content=response_text,
            #     classification=classified,
            #     agent_answered="generic",
            #     intent=None,
            #     metadata = metadata
            # )

        elif classified == "building_specific":
            # BuildingAgent already returns normalized diagnostics (intent, agent_answered, etc.)
            out , metadata_updated  = self.building.handle_building_query(last_message, messages, base_metadata, thread_id)
            out['role'] = 'assistant'
            out['classification']=classified
            return out , metadata_updated
            # {
            #     'role' : 'assistant' , 
            #     'content' :'response_text' , 
            #     'classification' : classified  , 
            #     'agent_answered' : "building_specific"
            # } , metadata
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
            out  ,  metadata_updated = self.cluster.handle_cluster_query(last_message, messages, base_metadata, thread_id)
            out['role'] = 'assistant'
            out['classification']=classified 
            return out , metadata_updated

        elif classified == "conversational":
            response_text = self.conversationallist.handle_conversational_input(last_message, messages)
            return {
                'role' : 'assistant' , 
                'content' :response_text, 
                'classification' : classified  , 
                'agent_answered' : "conversationalist"
            } , base_metadata
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
            } , base_metadata
