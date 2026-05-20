# src/pipeline/agent_router.py
import re
from typing import Any, Dict, List, Optional

from src.agents.router_agent import RouterAgent
from src.agents.building_agent import BuildingAgent
from src.agents.cluster_agent import ClusterAgent
from src.agents.generic_agent import GenericAgent
from src.agents.aggregator_agent import AggregatorAgent  # imported if you use it elsewhere
from src.agents.conversationalist_agent import ConversationalAgent
from src.services.draft_report_service import generate_draft_report_response
from src.services.expert_handoff_email import send_expert_handoff_email
from src.pipeline.safety_analysis import build_out_of_scope_response, detect_out_of_scope


CLASSIFICATION_ALIASES = {
    "general": "generic",
    "building": "building_specific",
    "building-specific": "building_specific",
    "draft_report": "draft_energy_report",
}

ROUTER_LABEL_PATTERN = re.compile(
    r"\b(building_specific|building-specific|building|generic|general|"
    r"conversational|cluster|draft_energy_report|draft_report|expert_handoff)\b",
    re.IGNORECASE,
)

SITE_SPECIFIC_PATTERNS = (
    r"\bmy building\b",
    r"\bour building\b",
    r"\bmy apartment building\b",
    r"\bour apartment building\b",
    r"\bmy property\b",
    r"\bour property\b",
    r"\bthis building\b",
    r"\bthe building\b",
    r"\bmy address\b",
    r"\bour address\b",
    r"\bi live at\b",
    r"\bwe are at\b",
    r"\bfor my building\b",
    r"\bfor our building\b",
    r"\bmy energy efficiency\b",
    r"\bour energy efficiency\b",
    r"\bmy energy use\b",
    r"\bour energy use\b",
    r"\bmy energy consumption\b",
    r"\bour energy consumption\b",
    r"\bmy heating cost(?:s)?\b",
    r"\bour heating cost(?:s)?\b",
)

BUILDING_DATA_REQUEST_TERMS = (
    "energy performance",
    "energy class",
    "energiprestanda",
    "energiklass",
    "heating system",
    "ventilation",
    "atemp",
    "heated area",
    "district heating",
    "electricity use",
    "energy declaration",
)


def _last_assistant_asked_for_expert_handoff(messages: List[Dict[str, Any]]) -> bool:
    for message in reversed(messages[:-1]):
        if message.get("role") != "assistant":
            continue

        content = str(message.get("content") or "").lower()
        if any(
            phrase in content
            for phrase in (
                "email was sent successfully",
                "i will not send",
                "could not send the email",
            )
        ):
            return False

        return "do you want me to send it" in content and "expert" in content

    return False


def _normalize_classification(classified: Any) -> str:
    text = str(classified or "").strip().lower()
    match = ROUTER_LABEL_PATTERN.search(text)
    if match:
        text = match.group(1).lower()
    return CLASSIFICATION_ALIASES.get(text, text)


def _looks_like_address(message: str) -> bool:
    return bool(
        re.search(
            r"\b[\wåäöÅÄÖ.-]+\s+\d+[A-Za-z]?(?:\s*[-–]\s*\d+[A-Za-z]?)?(?:,\s*[\wåäöÅÄÖ\s.-]+)?\b",
            message or "",
        )
    )


def _requires_building_specific_flow(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False

    if _looks_like_address(message):
        return True

    if re.search(r"\b(?:my|our)\s+(?:energy|heating|electricity)", lowered):
        return True

    site_specific = any(re.search(pattern, lowered) for pattern in SITE_SPECIFIC_PATTERNS)
    if not site_specific:
        return False

    if any(term in lowered for term in BUILDING_DATA_REQUEST_TERMS):
        return True

    return any(
        phrase in lowered
        for phrase in (
            "what should",
            "what is",
            "what are",
            "how can",
            "how much",
            "which",
            "does",
            "do we",
            "should we",
        )
    )


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
        last_assistant_asked_handoff = _last_assistant_asked_for_expert_handoff(messages)
        pending_handoff = bool(base_metadata.get("expert_handoff_pending_confirmation")) or last_assistant_asked_handoff
        explicit_handoff_send = (
            self.router.is_confirmation(last_message)
            and getattr(self.router, "wants_expert_handoff", None)
            and self.router.wants_expert_handoff(last_message)
        )

        if (pending_handoff or explicit_handoff_send) and self.router.is_confirmation(last_message):
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
                    cc_note = " and you were CCed" if updated_metadata.get("user_email") else ""
                    return {
                        "role": "assistant",
                        "content": f"The email was sent successfully to the EKR expert{cc_note}.",
                        "classification": "expert_handoff",
                        "agent_answered": "expert_handoff",
                        "route": "expert_handoff",
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
                "route": "expert_handoff",
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
                "route": "expert_handoff",
            }, updated_metadata

        boundary_case = detect_out_of_scope(last_message)
        if boundary_case:
            updated_metadata = {
                **base_metadata,
                "out_of_scope": True,
                "out_of_scope_type": boundary_case["out_of_scope_type"],
                "redirect_to": boundary_case["redirect_to"],
                "boundary_handling": {
                    "out_of_scope": True,
                    "out_of_scope_type": boundary_case["out_of_scope_type"],
                    "redirect_to": boundary_case["redirect_to"],
                    "safe_general_information_provided": True,
                },
            }
            return {
                "role": "assistant",
                "content": build_out_of_scope_response(
                    boundary_case["out_of_scope_type"],
                    boundary_case["redirect_to"],
                ),
                "classification": "out_of_scope",
                "agent_answered": "boundary",
                "route": "out_of_scope",
            }, updated_metadata

        # Try to read prior classification if present
        if len(messages) != 1:
            previous_classification = (messages[-2] or {}).get("classification")
        else:
            previous_classification = None

        if getattr(self.router, "wants_draft_report", None) and self.router.wants_draft_report(last_message):
            classified = "draft_energy_report"
        elif getattr(self.router, "wants_expert_handoff", None) and self.router.wants_expert_handoff(last_message):
            classified = "expert_handoff"
        else:
            classified = self.router.classify_question(last_message, previous_classification)

        classified = _normalize_classification(classified)
        if classified in {"generic", "conversational"} and _requires_building_specific_flow(last_message):
            classified = "building_specific"

        if pending_handoff and classified != "expert_handoff":
            base_metadata = {
                **base_metadata,
                "expert_handoff_pending_confirmation": False,
            }

        if classified == "draft_energy_report":
            report_response = generate_draft_report_response(thread_id, messages, base_metadata)
            report_response.setdefault("route", "report_generation")
            return report_response, base_metadata

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
                "route": "expert_handoff",
            }, updated_metadata

        if classified == "generic":
            generic_response = self.generic.handle_generic_input(last_message, messages)
            if isinstance(generic_response, dict):
                response_text = generic_response.get("content", "")
                sources = generic_response.get("sources") or []
            else:
                response_text = generic_response or ""
                sources = []

            payload = {
                'role' : 'assistant' , 
                'content' :response_text , 
                'classification' : classified  , 
                'agent_answered' : "generic",
                'route': "generic",
            }
            if sources:
                payload["sources"] = sources
            return payload , base_metadata
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
            out.setdefault("route", "combined")
            return out , metadata_updated

        elif classified == "conversational":
            response_text = self.conversationallist.handle_conversational_input(last_message, messages)
            return {
                'role' : 'assistant' , 
                'content' :response_text, 
                'classification' : classified  , 
                'agent_answered' : "conversationalist",
                'route': "generic",
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
                'agent_answered' : "unknown",
                'route': "generic",
            } , base_metadata
