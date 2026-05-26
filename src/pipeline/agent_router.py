# src/pipeline/agent_router.py
import os
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

BUILDING_ID_RE = re.compile(
    r"\b\d{2}-\d{2}-[A-Za-zÅÄÖåäö0-9]+-\d+\b",
    re.IGNORECASE,
)


def _evaluation_mode_enabled() -> bool:
    return str(os.getenv("EVALUATION_MODE", "")).strip().lower() in {"1", "true", "yes", "on"}


def _fast_conversational_response(message: str) -> Optional[str]:
    lowered = str(message or "").strip().lower()
    if not lowered:
        return None

    if re.fullmatch(r"(hi|hello|hey|hej|good\s+morning|good\s+afternoon|good\s+evening)[.!?]*", lowered):
        return "Hi! I can help with energy advice and building-specific information when you share a BRF name or street address."

    if re.search(
        r"\b(what\s+is\s+this\s+(?:app|application|service)|what\s+can\s+you\s+do|"
        r"how\s+does\s+this\s+(?:app|application|service)\s+work)\b",
        lowered,
    ):
        return (
            "SPARA helps BRFs and energy advisors answer questions about energy use, EPC data, "
            "heating, ventilation, and relevant energy-efficiency measures. Share a BRF name or "
            "street address when you want building-specific answers."
        )

    return None

SITE_SPECIFIC_PATTERNS = (
    r"\b(?:brf|bostadsr[äa]ttsf[öo]reningen|bostadsrattsforeningen)\s+[a-zåäö0-9]",
    r"\b(?:i|we)\s+(?:live\s+in|are)\s+(?:brf|bostadsr[äa]ttsf[öo]reningen|bostadsrattsforeningen)\b",
    r"\bmy building\b",
    r"\bmybuilding\b",
    r"\bour building\b",
    r"\bourbuilding\b",
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

BUILDING_CONTEXT_METADATA_KEYS = (
    "address",
    "address_from_user",
    "matched_address",
    "input_address",
    "official_address",
    "building_id",
    "byggnadsid",
    "50a_uuid",
    "01a_fnr",
)

ECM_FOLLOWUP_PATTERNS = (
    r"\becms?\b",
    r"\benergy conservation measures?\b",
    r"\benergy efficiency measures?\b",
    r"\bconservation measures?\b",
    r"\brelevant\s+(?:measures?|upgrades?|retrofits?|ecms?)\b",
    r"\bsuitable\s+(?:measures?|upgrades?|retrofits?|ecms?)\b",
    r"\bwhich\s+(?:measures?|upgrades?|retrofits?|ecms?)\b",
    r"\bwhat\s+(?:measures?|upgrades?|retrofits?|ecms?)\b",
    r"\brecommend(?:ed|ation|ations)?\b",
    r"\bprioriti[sz]e\b",
    r"\bretrofit(?:s|ting)?\b",
    r"\brenovat(?:e|ion|ions)?\b",
    r"\bupgrade(?:s|d|ing)?\b",
)

BUILDING_PROFILE_FOLLOWUP_PATTERNS = (
    r"\bgive\s+me\s+(?:information|info|details|facts)\s+(?:about|on)\s+(?:my\s+|our\s+|the\s+)?(?:building|property)\b",
    r"\btell\s+me\s+(?:about|more\s+about)\s+(?:my\s+|our\s+|the\s+)?(?:building|property)\b",
    r"\bshow\s+me\s+(?:information|info|details|facts)\s+(?:about|on)\s+(?:my\s+|our\s+|the\s+)?(?:building|property)\b",
    r"\b(?:building|property)\s+(?:information|info|details|facts|profile)\b",
    r"\b(?:mybuilding|ourbuilding)\b",
)

BUILDING_DATA_EXPLANATION_FOLLOWUP_PATTERNS = (
    r"\bshow\s+me\s+(?:the\s+)?(?:data|source|sources|fields|numbers?|values?)\b",
    r"\bexplain\s+(?:more|why|the\s+data|the\s+numbers?).*(?:data|energy\s+class|energiklass|epc|declaration|20\d{2}|19\d{2})\b",
    r"\bwhy\s+.*(?:energy\s+class|energiklass|epc|declaration|20\d{2}|19\d{2}|class\s+[a-g])\b",
    r"\b(?:20\d{2}|19\d{2})\s*[a-g]\b.*\b(?:20\d{2}|19\d{2})\b",
)

CONTENTFUL_FOLLOWUP_PATTERNS = (
    r"\b(?:what|why|how|which|when|where|does|do|did|is|are|can|could|should|would)\b",
    r"\b(?:explain|show|tell|give|compare|summari[sz]e|calculate|estimate|check|look\s+at)\b",
    r"\b(?:more|data|numbers?|values?|class|performance|energy|heating|ventilation|ecms?|measures?|costs?|use|consumption)\b",
)

NON_BUILDING_FOLLOWUP_PATTERNS = (
    r"^\s*(?:hi|hello|hey|thanks|thank\s+you|ok|okay|cool|great|nice)\s*[.!?]*\s*$",
    r"^\s*(?:yes|no|yep|nope)\s*[.!?]*\s*$",
)

EXPLICIT_GENERAL_SCOPE_PATTERNS = (
    r"\bin general\b",
    r"\bgenerally\b",
    r"\bbroadly\b",
    r"\bmore broadly\b",
    r"\bnot building[-\s]?specific\b",
    r"\bgeneral tips?\b",
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


ADDRESS_INTRO_RE = re.compile(
    r"(?:i\s+live\s+(?:in|at|on)|i\s+am\s+at|i'm\s+at|my\s+address\s+is|address\s+is|"
    r"we\s+live\s+(?:in|at|on)|our\s+address\s+is|"
    r"jag\s+bor\s+p[åa]|vi\s+bor\s+p[åa]|min\s+adress\s+[äa]r|adressen\s+[äa]r)\s+(.+)$",
    flags=re.IGNORECASE,
)


def _looks_like_compact_address(text: Optional[str], *, allow_four_digit_number: bool = False) -> bool:
    if not text:
        return False

    candidate = re.sub(r"\s+", " ", str(text)).strip(" .,:;")
    if not candidate or any(mark in candidate for mark in ("?", "!", "\n")):
        return False
    if len(candidate) > 90:
        return False

    word_count = len(re.findall(r"[A-Za-zÅÄÖåäö0-9]+", candidate))
    if word_count > 7:
        return False

    house_number = r"\d{1,4}" if allow_four_digit_number else r"\d{1,3}"
    return bool(
        re.fullmatch(
            rf"[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö .'\-]*\s+{house_number}[A-Za-z]?"
            rf"(?:\s*[-–]\s*{house_number}[A-Za-z]?)?"
            r"(?:\s*,\s*[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö .'\-]*)?",
            candidate,
        )
    )


def _normalize_classification(classified: Any) -> str:
    text = str(classified or "").strip().lower()
    match = ROUTER_LABEL_PATTERN.search(text)
    if match:
        text = match.group(1).lower()
    return CLASSIFICATION_ALIASES.get(text, text)


def _looks_like_address(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    cue_match = ADDRESS_INTRO_RE.search(text)
    if cue_match:
        return _looks_like_compact_address(cue_match.group(1), allow_four_digit_number=True)

    return _looks_like_compact_address(text)


def _metadata_contains_building_context(value: Any) -> bool:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            lower_map = {str(key).lower(): key for key in current.keys()}
            for key in BUILDING_CONTEXT_METADATA_KEYS:
                original = lower_map.get(key.lower())
                if original is not None and current.get(original) not in (None, "", [], {}):
                    return True
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return False


def _has_recent_building_context(messages: List[Dict[str, Any]], metadata: Dict[str, Any]) -> bool:
    if _metadata_contains_building_context(metadata):
        return True

    for message in reversed((messages or [])[:-1]):
        classification = _normalize_classification(message.get("classification"))
        if classification == "building_specific":
            return True

        if _metadata_contains_building_context(message.get("metadata")):
            return True

        content = str(message.get("content") or "")
        if re.search(r"\bbuilding id\s*:\s*[A-Za-z0-9:_.-]+", content, re.IGNORECASE):
            return True
        if re.search(r"\bbyggnadsid\b", content, re.IGNORECASE):
            return True

    return False


def _looks_like_ecm_followup(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False
    if any(re.search(pattern, lowered) for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS):
        return False
    return any(re.search(pattern, lowered) for pattern in ECM_FOLLOWUP_PATTERNS)


def _looks_like_building_profile_followup(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False
    if any(re.search(pattern, lowered) for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS):
        return False
    return any(re.search(pattern, lowered) for pattern in BUILDING_PROFILE_FOLLOWUP_PATTERNS)


def _looks_like_building_data_explanation_followup(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False
    if any(re.search(pattern, lowered) for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS):
        return False
    return any(re.search(pattern, lowered) for pattern in BUILDING_DATA_EXPLANATION_FOLLOWUP_PATTERNS)


def _looks_like_contextual_building_followup(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False
    if any(re.search(pattern, lowered) for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS):
        return False
    if any(re.search(pattern, lowered) for pattern in NON_BUILDING_FOLLOWUP_PATTERNS):
        return False
    return any(re.search(pattern, lowered) for pattern in CONTENTFUL_FOLLOWUP_PATTERNS)


def _requires_building_specific_flow(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False

    if BUILDING_ID_RE.search(message or ""):
        return True

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


def _requires_building_specific_followup(
    message: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> bool:
    return (
        _looks_like_ecm_followup(message)
        or _looks_like_building_profile_followup(message)
        or _looks_like_building_data_explanation_followup(message)
        or _looks_like_contextual_building_followup(message)
    ) and _has_recent_building_context(messages, metadata)


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
            if _evaluation_mode_enabled():
                updated_metadata = {
                    **updated_metadata,
                    "expert_handoff_requested": True,
                    "expert_handoff_sent": False,
                    "expert_handoff_simulated": True,
                }
                return {
                    "role": "assistant",
                    "content": "In this evaluation run, I would send this conversation to an EKR expert. No real email was sent.",
                    "classification": "expert_handoff",
                    "agent_answered": "expert_handoff",
                    "route": "expert_handoff",
                }, updated_metadata
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

        fast_conversational = _fast_conversational_response(last_message)
        if fast_conversational:
            return {
                "role": "assistant",
                "content": fast_conversational,
                "classification": "conversational",
                "agent_answered": "fast_conversational",
                "route": "generic",
            }, base_metadata

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
        if classified in {"generic", "conversational"} and (
            _requires_building_specific_flow(last_message)
            or _requires_building_specific_followup(last_message, messages, base_metadata)
        ):
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
