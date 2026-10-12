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
from src.services.expert_handoff_email import (
    copyable_expert_handoff_summary,
    handoff_response_language,
    expert_handoff_preview_matches,
    prepare_expert_handoff,
    send_expert_handoff_email,
)
from src.pipeline.safety_analysis import (
    build_out_of_scope_response, detect_out_of_scope, response_language, localize_service_error,
    normalize_brf_name, extract_concrete_brf_name, normalize_address_candidate, requests_existing_document,
    STREET_SUFFIX_PATTERN,
)
from src.services.source_link_registry import guard_response_links, resolve_source_links
from src.pipeline.action_state import (
    restore_action_receipts, record_action_response, has_action_receipts,
    is_handoff_status_question, is_report_status_question, wants_report_retrieval,
    render_handoff_status, report_status_response,
)


CLASSIFICATION_ALIASES = {
    "general": "generic",
    "building": "building_specific",
    "building-specific": "building_specific",
    "draft_report": "draft_energy_report",
}

SUPPORTED_CLASSIFICATIONS = frozenset({
    "building_specific", "generic", "conversational", "cluster",
    "draft_energy_report", "expert_handoff",
})

BUILDING_ID_RE = re.compile(
    r"\b\d{2}-\d{2}-[A-Za-zÅÄÖåäö0-9:_-]+-\d+\b",
    re.IGNORECASE,
)

STREET_SUFFIX_RE = STREET_SUFFIX_PATTERN

STREET_ADDRESS_FRAGMENT_RE = re.compile(
    rf"\b("
    rf"(?:[A-ZÅÄÖ][A-Za-zÅÄÖåäö.'\-]*(?:{STREET_SUFFIX_RE})|"
    rf"[A-ZÅÄÖ][A-Za-zÅÄÖåäö.'\-]*(?:\s+[A-ZÅÄÖa-zåäö][A-Za-zÅÄÖåäö.'\-]*){{0,3}}\s+(?:{STREET_SUFFIX_RE}))"
    rf"\s+\d{{1,4}}[A-Za-z]?"
    rf")\b",
    flags=re.IGNORECASE,
)

MULTI_ADDRESS_POLICY_RE = re.compile(
    r"\b(?:two|multiple|several|many|different|flera|två|många|olika)\s+(?:addresses?|adresser)\b|"
    r"\b(?:addresses?|adresser)\b.*\b(?:which|use|choose|använd\w*|vilken|välj)\b|"
    r"\b(?:which|vilken)\s+one\s+do\s+you\s+use\b",
    flags=re.IGNORECASE,
)


def _address_value_count(message: str) -> int:
    word = r"[^\W\d_]+(?:[.:'’\-][^\W\d_]+)*\.?"
    spans = re.finditer(rf"{word}(?:\s+{word}){{0,14}}\s+\d{{1,4}}[A-Za-z]?(?:\s*[-–]\s*\d{{1,4}}[A-Za-z]?)?(?!\w)", str(message or ""))
    count = 0
    for match in spans:
        fragment = re.sub(r"^.*?\b(?:and|och|or|eller|addresses|adresser)\s+", "", match.group(), flags=re.IGNORECASE)
        if normalize_address_candidate(fragment, allow_four_digit_number=True) or STREET_ADDRESS_FRAGMENT_RE.search(fragment):
            count += 1
    return count

ADDRESS_DISAMBIGUATION_REQUEST_RE = re.compile(
    r"(?=.*\b(?:address|building|property|brf|adress|byggnad|fastighet)\b)"
    r"(?=.*\b(?:city|postcode|postal\s+code|municipality|postort|postnummer|kommun|"
    r"building\s+id|byggnadsid|brf)\b)"
    r".*\b(?:multiple|several|ambiguous|different|correct|which|choose|use|"
    r"flera|olika|rätt|vilken|välj|använd)\b",
    flags=re.IGNORECASE,
)


def _evaluation_mode_enabled() -> bool:
    return str(os.getenv("EVALUATION_MODE", "")).strip().lower() in {"1", "true", "yes", "on"}


def _fast_conversational_response(message: str) -> Optional[str]:
    lowered = str(message or "").strip().lower()
    if not lowered:
        return None

    if re.fullmatch(r"(hi|hello|hey|hej|good\s+morning|good\s+afternoon|good\s+evening)[.!?]*", lowered):
        if re.fullmatch(r"hej[.!?]*", lowered):
            return (
                "Hej! Jag kan hjälpa dig med allmänna energiråd. Om du vill ha råd "
                "om en viss byggnad kan du ange föreningens namn, gatuadress eller byggnadsid."
            )
        return (
            "Hi! I can help with general energy advice. If you want building-specific "
            "advice, share a BRF name, street address, or building ID."
        )

    if re.search(
        r"\b(what\s+is\s+this\s+(?:app|application|service)|what\s+can\s+you\s+do|"
        r"how\s+does\s+this\s+(?:app|application|service)\s+work)\b",
        lowered,
    ):
        return (
            "SPARA helps BRFs and energy advisors answer questions about energy use, EPC data, "
            "heating, ventilation, and relevant energy-efficiency measures. You can ask general "
            "questions without identifying a building; share a BRF name, street address, or "
            "building ID when you want building-specific answers."
        )

    return None


def _fast_multi_address_clarification(message: str, language: str = "en") -> Optional[str]:
    text = str(message or "").strip()
    lowered = text.lower()
    if not text:
        return None

    if not MULTI_ADDRESS_POLICY_RE.search(text):
        return None

    if not re.search(
        r"\b(?:my|our|the|this)\s+(?:property|building|brf|association)|"
        r"\b(?:property|building|brf|fastighet\w*|byggnad\w*|förening\w*)\b",
        lowered,
    ):
        return None

    # If the user already gave two real addresses, let the building graph resolve
    # them against ODEN instead of answering only with the policy.
    address_count = _address_value_count(text)
    if address_count >= 2 or BUILDING_ID_RE.search(text):
        return None

    if language == "sv":
        return (
            "Ange de aktuella adresserna, BRF-namnet, organisationsnumret eller byggnadsid. "
            "Jag jämför adressernas byggnadsid: om de har samma id kan båda användas för "
            "samma byggnad. Om de har olika id får du välja vilken byggnad eller entré vi ska använda."
        )
    return (
        "Please share the actual addresses, BRF name, organisation number, or building ID. "
        "I use the building ID/byggnadsid as the source of truth: if multiple addresses map "
        "to the same building ID, either address can be used as an alias; if they map to "
        "different building IDs, I will ask you which building or entrance to use."
    )


def _has_multi_address_values_question(message: str) -> bool:
    text = str(message or "").strip()
    lowered = text.lower()
    if not text:
        return False
    if not MULTI_ADDRESS_POLICY_RE.search(text) and not re.search(r"\b(?:same\s+(?:building|house)|samma\s+(?:hus|byggnad|fastighet))\b", text, re.IGNORECASE):
        return False
    if not re.search(
        r"\b(?:my|our|the|this)\s+(?:property|building|brf|association)|"
        r"\b(?:property|building|house|brf|hus|fastighet\w*|byggnad\w*|förening\w*)\b",
        lowered,
    ):
        return False
    return _address_value_count(text) >= 2 or bool(BUILDING_ID_RE.search(text))


def _awaiting_address_disambiguation(metadata: Dict[str, Any]) -> bool:
    metadata = metadata or {}
    clarification = metadata.get("clarification") or {}
    identity_check = metadata.get("building_identity_check") or {}
    has_address = bool(
        metadata.get("address")
        or metadata.get("address_from_user")
        or metadata.get("pending_ambiguous_address")
    )
    return bool(
        has_address
        and clarification.get("needed")
        and (clarification.get("reason") == "ambiguous_address" or str(clarification.get("reason") or "").startswith("multi_address_"))
        and identity_check.get("status") in (None, "", "ambiguous")
    )


def _recent_assistant_requested_address_disambiguation(messages: List[Dict[str, Any]]) -> bool:
    for message in reversed((messages or [])[-6:]):
        if str((message or {}).get("role") or "").lower() != "assistant":
            continue
        clarification = ((message or {}).get("metadata") or {}).get("clarification")
        if isinstance(clarification, dict) and "needed" in clarification:
            return bool(clarification.get("needed") and (clarification.get("reason") == "ambiguous_address" or str(clarification.get("reason") or "").startswith("multi_address_")))
        if _normalize_classification((message or {}).get("classification")) == "generic":
            return False
        content = str((message or {}).get("content") or "")
        return bool(ADDRESS_DISAMBIGUATION_REQUEST_RE.search(content))
    return False


SITE_SPECIFIC_PATTERNS = (
    r"\b(?:v[åa]r|v[åa]rt|min|mitt|denna)\s+(?:byggnad|fastighet|brf|energiklass|energiprestanda|energideklaration)\b",
    r"\b(?:brf|bostadsr[äa]ttsf[öo]reningen|bostadsrattsforeningen)\s+[a-zåäö0-9]",
    r"\b(?:i|we)\s+(?:live\s+in|are)\s+(?:brf|bostadsr[äa]ttsf[öo]reningen|bostadsrattsforeningen)\b",
    r"\b(?:i|we)\s+represent\s+(?:a\s+)?(?:brf|bostadsr[äa]ttsf[öo]reningen|bostadsrattsforeningen)\b",
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

FACTUAL_BUILDING_DATA_REQUEST_TERMS = (
    "energy performance",
    "energy class",
    "energiprestanda",
    "energiklass",
    "energianvändning",
    "energianvandning",
    "heating system",
    "ventilation",
    "atemp",
    "heated area",
    "district heating",
    "electricity use",
    "energy declaration",
    "construction year",
    "built",
    "u-value",
    "u-values",
    "ovk",
    "energy audit",
)

ADVISORY_REQUEST_TERMS = (
    "energiråd",
    "energirad",
    "spara energi",
    "energieffektiv",
    "energianvändning",
    "energianvandning",
    "värmekostnad",
    "varmekostnad",
    "uppvärmningskostnad",
    "uppvarmningskostnad",
    "värmeräkning",
    "varmerakning",
    "energy advice",
    "energy efficiency",
    "heating costs",
    "heating bills",
    "reduce heating",
    "reduce costs",
    "save energy",
    "energy measures",
    "energy efficiency measures",
    "recommend measures",
    "recommended measures",
    "recommendations",
    "relevant measures",
    "which measures",
    "what measures",
    "prioritize",
    "prioritise",
    "ecm",
    "ecms",
    "solar pv",
    "solar panels",
    "ventilation issues",
    "geothermal",
    "bergvärme",
    "heat pump",
    "solceller",
    "kylning",
    "cooling",
    "overheating",
    "övertemperatur",
)

GENERIC_ADVISORY_CONTEXT_TERMS = ADVISORY_REQUEST_TERMS + (
    "district heating",
    "fjärrvärme",
    "heating bill",
    "heating bills",
    "renovation priorities",
    "low-regret",
)

GENERIC_ADVISORY_CONTINUATION_PATTERNS = (
    r"\b(?:summari[sz]e|summarise|sammanfatta|f[öo]rklara enklare)\b",
    r"\b(?:vad|vilk(?:en|et|a))\b.*\b(?:n[äa]sta steg|budget|begr[äa]nsningar|utg[åa]r)\b",
    r"\bb[öo]r vi\b.*\b(?:best[äa]lla|k[öo]pa|v[äa]lja|g[öo]ra|kontrollera)\b",
    r"\bkan du\b.*\b(?:hur stor|dimensioner|storlek)\b",
    r"\b(?:what size|how large)\b.*\b(?:buy|installation|system|array)\b",
    r"\bkan du\b.*\b(?:ange|s[äa]ga|f[öo]rklara|ber[äa]kna)\b.*\b(?:besparing(?:en|ar)?|[åa]terbetalningstid(?:en)?)\b",
    r"\b(?:can|could) you\b.*\b(?:give|tell|explain|provide|calculate|estimate)\b.*\b(?:savings?|payback(?: period)?)\b",
    r"\bwhat\s+should\s+(?:we|i)\s+do\s+first\b",
    r"\bwhat\s+(?:should|can|could)\s+(?:we|i)\s+do\b",
    r"\b(?:so\s+)?what\s+does\s+that\s+mean\b",
    r"\bthe\s+building\s+is\s+from\s+the\s+\d{2,4}s?\b",
    r"\b(?:district heating|fj[äa]rrv[äa]rme)\b",
    r"\b(?:multi[-\s]?family|apartment building|brf)\b",
    r"\b(?:courtyard|inner yard|roof|shading|tariff|heating bills?|ventilation issues?)\b",
)

BUILDING_CONTEXT_METADATA_KEYS = (
    "address",
    "address_from_user",
    "address_location_hint",
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
    r"\befficiency measures?\b",
    r"\bconservation measures?\b",
    r"\b(?:measures?|upgrades?|retrofits?|ecms?)\s+for\b",
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
    r"\b(?:allm[äa]n(?:na|t)?|generell(?:a|t)?)\s+(?:r[åa]d|tips|information)\b",
    r"\b(?:i allm[äa]nhet|generellt)\b",
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
        handoff_status = (message.get("metadata") or {}).get("expert_handoff_status")
        if handoff_status:
            return handoff_status == "pending_confirmation"
        if any(
            phrase in content
            for phrase in (
                "email was sent successfully",
                "i will not send",
                "could not send the email",
                "inget e-postmeddelande har skickats",
                "e-postservern tog emot",
                "kunde inte skicka e-postmeddelandet",
            )
        ):
            return False

        return (
            "do you want me to send it" in content and "expert" in content
        ) or ("vill du att jag skickar" in content and "rådgivare" in content)

    return False


ADDRESS_INTRO_RE = re.compile(
    r"(?:i\s+live\s+(?:in|at|on)|i\s+am\s+at|i'm\s+at|my\s+address\s+is|address\s+is|"
    r"we\s+live\s+(?:in|at|on)|our\s+address\s+is|"
    r"jag\s+bor\s+p[åa]|vi\s+bor\s+p[åa]|min\s+adress\s+[äa]r|adressen\s+[äa]r)\s+(.+)$",
    flags=re.IGNORECASE,
)

STALE_BRF_CLARIFICATION_REASONS = {
    "missing_brf_name",
    "brf_not_found",
    "brf_lookup_failed",
}

STALE_BRF_RESOLUTION_STATUSES = {
    "not_found",
    "lookup_error",
}

FAILED_BUILDING_CLARIFICATION_REASONS = {
    "building_not_found", "building_not_found_by_id", "building_lookup_failed",
}


def _looks_like_compact_address(text: Optional[str], *, allow_four_digit_number: bool = False) -> bool:
    return normalize_address_candidate(text, allow_four_digit_number=allow_four_digit_number) is not None


def _normalize_classification(classified: Any) -> Optional[str]:
    """Accept a complete label, including exact legacy aliases in stored history."""
    if not isinstance(classified, str):
        return None
    text = classified.strip().lower()
    label = CLASSIFICATION_ALIASES.get(text, text)
    return label if label in SUPPORTED_CLASSIFICATIONS else None


def _looks_like_address(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    cue_match = ADDRESS_INTRO_RE.search(text)
    if cue_match:
        return _looks_like_compact_address(cue_match.group(1), allow_four_digit_number=True)

    return _looks_like_compact_address(text)


def _clean_brf_name(value: Optional[str]) -> Optional[str]:
    return normalize_brf_name(value)


def _extract_concrete_brf_name(message: Optional[str]) -> Optional[str]:
    return extract_concrete_brf_name(message)


def _has_recent_concrete_brf_context(messages: List[Dict[str, Any]]) -> bool:
    for message in reversed((messages or [])[:-1]):
        if message.get("role") != "user":
            continue
        if _extract_concrete_brf_name(message.get("content")):
            return True
    return False


def _looks_like_building_specific_advice_request(message: str) -> bool:
    lowered = str(message or "").lower()
    return bool(
        re.search(r"\bbuilding[-\s]?specific\b", lowered)
        or re.search(r"\b(?:tailored|personalized|personalised)\s+advice\b", lowered)
        or re.search(r"\bspecific\s+(?:advice|recommendations?|measures?)\b", lowered)
    )


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
            # Historical report/handoff attribution is not the active building
            # context and must not reroute general advice to a past building.
            stack.extend(item for key, item in current.items() if key != "action_receipts")
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


def _has_recent_generic_advisory_context(messages: List[Dict[str, Any]]) -> bool:
    for message in reversed((messages or [])[:-1]):
        content = str(message.get("content") or "")
        lowered = content.lower()

        if not content.strip():
            continue
        classification = _normalize_classification(message.get("classification"))
        if classification == "building_specific":
            return False
        # Only user-supplied identity changes the advisory scope. An assistant
        # may cite a handbook whose title contains BRF without selecting a BRF.
        if message.get("role") == "user":
            if _has_explicit_site_identifier(content):
                return False
            if _looks_like_factual_building_data_request(content):
                continue
        if classification == "generic" and re.search(
            r"\b(?:energy|heating|ventilation|solar|geothermal|ecms?|measures?|renovation)\b",
            lowered,
        ):
            return True

        if _looks_like_advisory_request(content):
            return True
        if any(term in lowered for term in GENERIC_ADVISORY_CONTEXT_TERMS):
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


def _has_explicit_site_identifier(message: str) -> bool:
    text = str(message or "")
    return bool(
        BUILDING_ID_RE.search(text)
        or _looks_like_address(text)
        or _address_value_count(text)
        or _extract_concrete_brf_name(text)
    )


def _generic_public_brf_or_org_question_without_identity(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False
    if _has_explicit_site_identifier(message):
        return False
    if re.search(r"\b(?:dataset|database|portfolio|databas|best[åa]nd|j[äa]mf[öo]r)\b", lowered):
        return False

    patterns = (
        r"\bvad\s+(?:[äa]r|betyder)\s+(?:en\s+)?(?:brf|bostadsr[äa]ttsf[öo]rening)\b",
        r"\bvad\s+st[åa]r\s+brf\s+f[öo]r\b",
        r"\bhur\s+m[åa]nga\s+(?:brf(?:er|:er|s)?|bostadsr[äa]ttsf[öo]reningar)\b",
        r"\bhur\s+m[åa]nga\s+byggnader\b.*\bstockholmshem\b",
        r"\bwhat\s+(?:is|does)\s+(?:a\s+)?brf\b",
        r"\bwhat\s+does\s+brf\s+(?:mean|stand\s+for)\b",
        r"\bhow\s+many\s+brfs?\b",
        r"\bhow\s+many\s+brf\b",
        r"\b(?:home\s*owner|homeowner|housing)\s+(?:companies|associations?)\b",
        r"\bstockholmshem\b.*\b(?:how\s+many|buildings?|names?)\b",
        r"\bhow\s+many\b.*\bstockholmshem\b.*\bbuildings?\b",
        r"\bhow\s+many\s+buildings?\b.*\bstockholmshem\b",
    )
    return any(re.search(pattern, lowered) for pattern in patterns)


def _looks_like_general_energy_relationship_question(message: str) -> bool:
    lowered = str(message or "").lower()
    return bool(
        re.search(r"\bhow\s+(?:do|does|can|would)\b[^.!?;]*\b(?:affect|influence|relate|impact|depend)\b", lowered)
        and re.search(r"\b(?:energy|electricity|heating|ventilation|costs?|prices?|bills?)\b", lowered)
    )


def _looks_like_advisory_request(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False
    if _looks_like_general_energy_relationship_question(message):
        return True
    if any(term in lowered for term in ADVISORY_REQUEST_TERMS):
        return True
    if re.search(r"\bbrf\b", lowered) and re.search(
        r"\b(?:fj[äa]rrv[äa]rme|v[äa]rme|energi|el|ventilation)\b", lowered
    ) and re.search(r"\b(?:dyrt?|dyra|problem|h[öo]ga|hj[äa]lp)\b", lowered):
        return True
    return bool(
        re.search(r"\b(?:hur|vad|vilka)\b.*\b(?:minska|s[äa]nka|spara|f[öo]rb[äa]ttra)\b.*\b(?:energi|v[äa]rme|el|kostnader|ventilation)\b", lowered)
        or
        re.search(r"\b(?:how|what|which)\b.*\b(?:reduce|lower|save|improve|optimi[sz]e|prioriti[sz]e)\b", lowered)
        and re.search(r"\b(?:energy|heating|electricity|ventilation|solar|costs?|bills?)\b", lowered)
    )


def _looks_like_project_financial_calculation_request(message: str) -> bool:
    lowered = str(message or "").lower()
    if not re.search(r"\b(?:besparing(?:en|ar)?|[åa]terbetalningstid(?:en)?|savings?|payback)\b", lowered):
        return False
    calculation = bool(re.search(r"\b(?:ber[äa]kna|r[äa]kna ut|calculate|estimate)\b", lowered))
    supplied_inputs = bool(re.search(
        r"\d(?:[\d\s.,]*\d)?\s*(?:kwh|kwp?|m[²2]|kr|sek|kronor|%|percent|procent)\b", lowered,
    ))
    requested_result = bool(re.search(r"\b(?:ange|s[äa]ga|give|tell|provide)\b", lowered))
    requests_measurements = bool(
        re.search(r"\b(?:h[äa]mta|visa|retrieve|fetch|look up)\b", lowered)
        and re.search(
            r"\b(?:elanv[äa]ndning|elf[öo]rbrukning|energianv[äa]ndning|m[äa]tdata|"
            r"m[äa]tningar|m[äa]tv[äa]rden|electricity (?:use|consumption)|"
            r"energy consumption|meter(?:ing)? data|measurements?)\b", lowered,
        )
    )
    # An explanation of an unavailable figure differs from a calculation using
    # newly supplied project inputs. Do not let a continuation override the latter.
    return bool(
        (calculation or requested_result) and supplied_inputs
        or requests_measurements
    )


def _looks_like_factual_building_data_request(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False
    if _looks_like_project_financial_calculation_request(message):
        return True
    # Asking how to improve a system is general advice until the user asks for
    # their actual records. Keep a factual clause in a multipart request intact.
    asks_for_own_fact = bool(
        re.search(
            r"\b(?:what|which|when|where|how\s+(?:much|many)|vad|vilken|vilket|n[äa]r)\b"
            r"[^.!?;,]*\b(?:is|are|was|were|have|has|does|do|did|[äa]r|har|byggdes)\b"
            r"[^.!?;,]*\b(?:our|my|we|this|v[åa]r|v[åa]rt|min|mitt|vi)\b",
            lowered,
        )
        or re.search(
            r"\b(?:what|which|when|where|how\s+(?:much|many)|vad|vilken|vilket|n[äa]r)\b"
            r"[^.!?;,]*\b(?:our|my|we|this|v[åa]r|v[åa]rt|min|mitt|vi)\b"
            r"[^.!?;,]*\b(?:is|are|was|were|have|has|does|do|did|[äa]r|har|byggdes)\b",
            lowered,
        )
        or re.search(
            r"\b(?:show|give|tell|explain|provide|find|check|look\s+up|quote|read|visa|ber[äa]tta|h[äa]mta|l[äa]s)\b"
            r"[^.!?;]*\b(?:our|my|this|v[åa]r|v[åa]rt|min|mitt)\b",
            lowered,
        )
    )
    advisory_question = bool(
        re.search(
            r"\b(?:what|which|how|vad|vilka|hur)\b[^.!?;]*"
            r"\b(?:should|could|recommend|prioriti[sz]e|improve|reduce|save|"
            r"b[öo]r|unders[öo]ka|minska|spara|f[öo]rb[äa]ttra|prioritera)\b",
            lowered,
        )
    )
    if advisory_question and not asks_for_own_fact:
        return False
    has_factual_term = any(term in lowered for term in FACTUAL_BUILDING_DATA_REQUEST_TERMS)
    asks_for_fact = bool(
        re.search(r"\b(?:vad\s+(?:[äa]r|har)|vilken|vilket|n[äa]r\s+byggdes)\b", lowered)
        or re.search(r"^\s*(?:what|which|when|where|does|do|is|are)\b", lowered)
        or re.search(r"\b(?:what|which|when|where)\b", lowered)
        or re.search(r"\bhow\s+(?:much|many)\b", lowered)
        or re.search(
            r"\b(?:know|tell|show|give|provide|find|check|look\s+up|quote|list|calculate|read|explain)\b",
            lowered,
        )
        or re.search(r"\bright\s*\??\s*$", lowered)
    )
    if has_factual_term and asks_for_fact:
        return True
    return bool(
        re.search(r"\b(?:vilken|vilket|vad\s+(?:[äa]r|har)|n[äa]r\s+byggdes)\b.*\b(?:v[åa]r|v[åa]rt|min|mitt)\b.*\b(?:byggnad|fastighet|brf|energiklass|energiprestanda)\b", lowered)
        or re.search(r"\b(?:what|which|when|does|do|is|are)\b.*\b(?:our|my|this)\b.*\b(?:building|property|brf|declaration|ovk)\b", lowered)
        or re.search(r"\b(?:do|does)\s+(?:we|our|my)\s+have\b", lowered)
    )


def _asks_for_personalized_priorities_without_identity(message: str) -> bool:
    lowered = str(message or "").lower()
    return bool(
        re.search(r"\b(?:specifikt|skr[äa]ddarsydda?)\b", lowered)
        or re.search(r"\bspecifically\b", lowered)
        or re.search(r"\bgiven\s+(?:our|my)\b", lowered)
        or re.search(r"\b(?:for|in)\s+(?:our|my|this)\s+(?:building|property|brf)\b", lowered)
        or re.search(r"\b(?:our|my)\s+(?:building|property|brf(?!\s+board\b))\b[^.!?;]*\b(?:prioriti[sz]e|recommend|measure|ecm)\b", lowered)
    )


def _advisory_without_resolved_building_context(
    message: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> bool:
    if not _looks_like_advisory_request(message):
        return False
    if _has_explicit_site_identifier(message):
        return False
    if _has_recent_building_context(messages, metadata):
        return False
    if _has_recent_concrete_brf_context(messages):
        return False
    if _looks_like_factual_building_data_request(message):
        return False
    if _asks_for_personalized_priorities_without_identity(message):
        return False
    return True


def _generic_advisory_continuation_without_resolved_building(
    message: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False
    if _has_explicit_site_identifier(message):
        return False
    if _has_recent_building_context(messages, metadata):
        return False
    if _looks_like_factual_building_data_request(message):
        return False
    if _asks_for_personalized_priorities_without_identity(message):
        return False
    if _looks_like_building_specific_advice_request(message) and _has_recent_concrete_brf_context(messages):
        return False
    if not _has_recent_generic_advisory_context(messages):
        return False
    return bool(
        _looks_like_advisory_request(message)
        or _looks_like_contextual_building_followup(message)
        or any(re.search(pattern, lowered) for pattern in GENERIC_ADVISORY_CONTINUATION_PATTERNS)
    )


def _requires_building_specific_flow(message: str) -> bool:
    lowered = str(message or "").lower()
    if not lowered.strip():
        return False

    if _has_explicit_site_identifier(message):
        return True

    if _looks_like_project_financial_calculation_request(message):
        return True

    if (
        _looks_like_general_energy_relationship_question(message)
        and not _looks_like_factual_building_data_request(message)
    ):
        return False

    if _looks_like_factual_building_data_request(message) and re.search(
        r"\b(?:we|our|my|vi|v[åa]r|v[åa]rt|min|mitt)\b", lowered,
    ):
        return True

    if re.search(
        r"\b(?:my|our)\s+(?:energy\s+performance|energy\s+class|heating\s+system|"
        r"ventilation|electricity\s+use|district\s+heating|energy\s+declaration)\b",
        lowered,
    ):
        return True

    if "energy audit" in lowered and re.search(
        r"\b(?:my|our|we|brf|bostadsr[äa]ttsf[öo]rening|bostadsrattsforening|building|property|association)\b",
        lowered,
    ):
        return True

    site_specific = any(re.search(pattern, lowered) for pattern in SITE_SPECIFIC_PATTERNS)
    if not site_specific:
        return False

    if _extract_concrete_brf_name(message) and _looks_like_advisory_request(message):
        return True

    if _looks_like_factual_building_data_request(message):
        return True

    return bool(
        re.search(r"\bhow much\b", lowered)
        or re.search(r"\b(?:do|does)\s+(?:we|our|my)\b", lowered)
        or re.search(r"\bwhat\s+(?:is|are)\s+(?:our|my)\b", lowered)
        or re.search(r"\bwhich\s+(?:building|address|entrance)\b", lowered)
    )


def _requires_building_specific_followup(
    message: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> bool:
    if (
        _looks_like_building_specific_advice_request(message)
        and _has_recent_concrete_brf_context(messages)
        and not _has_recent_building_context(messages, metadata)
    ):
        return True
    if (
        _looks_like_ecm_followup(message)
        and _has_recent_concrete_brf_context(messages)
        and not _has_recent_building_context(messages, metadata)
    ):
        return True

    return (
        _looks_like_ecm_followup(message)
        or _looks_like_building_profile_followup(message)
        or _looks_like_building_data_explanation_followup(message)
        or _looks_like_contextual_building_followup(message)
    ) and _has_recent_building_context(messages, metadata)


def _recover_classification(
    message: str,
    messages: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> Optional[str]:
    """Recover a non-action route from the current request and relevant context."""
    lowered = str(message or "").strip().lower()
    if not lowered:
        return None
    if (
        any(re.search(pattern, lowered) for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS)
        and not (BUILDING_ID_RE.search(message) or _looks_like_address(message) or _address_value_count(message))
        and not _looks_like_factual_building_data_request(message)
    ):
        return "generic"
    if _requires_building_specific_flow(message):
        return "building_specific"
    if (
        _generic_public_brf_or_org_question_without_identity(message)
        or any(re.search(pattern, lowered) for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS)
        or _advisory_without_resolved_building_context(message, messages, metadata)
    ):
        return "generic"

    # A previous building classification alone does not make a new question
    # building-specific. Require a reference or an elliptical continuation.
    explicit_building_reference = bool(re.search(
        r"\b(?:this|that|our|my|the)\s+(?:building|property|brf)\b|"
        r"\b(?:denna|den|v[åa]r|min)\s+(?:byggnad|byggnaden|fastighet|fastigheten)\b", lowered,
    ))
    elliptical_reference = bool(re.search(
        r"^\s*(?:and|also|what\s+about|och|ocks[åa]|hur\s+[äa]r\s+det\s+med)\s+"
        r"(?:the\s+|its\s+|dess\s+)?(?:heating|ventilation|energy\s+(?:class|performance)|"
        r"construction\s+year|heated\s+area|energiklass|energiprestanda|v[äa]rmesystem|bygg[åa]r)\b|"
        r"\b(?:its|dess)\b", lowered,
    ))
    latest_assistant_label = next((
        _normalize_classification(prior.get("classification"))
        for prior in reversed((messages or [])[:-1]) if prior.get("role") == "assistant"
    ), None)
    refers_to_building = explicit_building_reference or (
        elliptical_reference and latest_assistant_label == "building_specific"
    )
    if refers_to_building and _requires_building_specific_followup(message, messages, metadata):
        return "building_specific"

    energy_topic = bool(re.search(
        r"\b(?:energy|electricity|heating|ventilation|insulation|solar|geothermal|"
        r"heat\s+pumps?|district\s+heating|drying\s+cabinets?|charging\s+points?|"
        r"energi\w*|el|v[äa]rme\w*|fj[äa]rrv[äa]rme|bergv[äa]rme|"
        r"solceller|isolering|tv[äa]ttstuga|ladd\w*)\b", lowered,
    ))
    if energy_topic or _looks_like_advisory_request(message):
        return "generic"
    if _generic_advisory_continuation_without_resolved_building(message, messages, metadata):
        return "generic"
    constraint_or_observation = bool(re.search(
        r"\b(?:budget|loan|loans|borrowing|financ\w*|costs?|shad\w*|roof|"
        r"courtyard|damp|recently|households?|sessions?|residents?|temperatures?|"
        r"measurements?|measur\w*|maintenance|renovations?|"
        r"l[åa]n|tak|skugg\w*|innerg[åa]rd|fukt\w*|boende|hush[åa]ll|"
        r"temperatur\w*|m[äa]t\w*|underh[åa]ll|renover\w*)\b", lowered,
    ))
    recent_advisory_context = _has_recent_generic_advisory_context(messages)
    if not recent_advisory_context:
        # An assistant may ask a short diagnostic question without repeating
        # energy vocabulary, including laundry and EV charging conversations.
        for prior in reversed((messages or [])[:-1]):
            if prior.get("role") != "user":
                continue
            prior_text = str(prior.get("content") or "")
            if _has_explicit_site_identifier(prior_text):
                break
            if re.search(r"\b(?:laundry\s+room|drying\s+cabinet|charging\s+points?|tv[äa]ttstuga|ladd\w*)\b", prior_text, re.IGNORECASE):
                recent_advisory_context = True
                break
    generic_subject_followup = (
        latest_assistant_label == "generic"
        and (
            elliptical_reference
            or any(re.search(pattern, lowered) for pattern in GENERIC_ADVISORY_CONTINUATION_PATTERNS)
            or re.search(r"\bwhat\s+(?:should|can|could)\s+(?:we|i)\s+(?:check|measure|prioriti[sz]e)\b", lowered)
        )
    )
    if (constraint_or_observation or generic_subject_followup) and recent_advisory_context:
        return "generic"
    if re.fullmatch(
        r"(?:thanks|thank\s+you|tack|ok|okay|yes|no|ja|nej|goodbye|bye|hej\s+d[åa])\s*[.!?]*",
        lowered,
    ):
        return "conversational"
    return None


def _metadata_has_failed_brf_lookup(metadata: Dict[str, Any]) -> bool:
    metadata = metadata or {}
    clarification = metadata.get("clarification") or {}
    brf_resolution = metadata.get("brf_resolution") or {}
    return bool(
        clarification.get("reason") in STALE_BRF_CLARIFICATION_REASONS
        or brf_resolution.get("status") in STALE_BRF_RESOLUTION_STATUSES
    )


def _looks_like_brf_resolution_continuation(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    return bool(
        BUILDING_ID_RE.search(text)
        or _looks_like_address(text)
        or STREET_ADDRESS_FRAGMENT_RE.search(text)
        or _extract_concrete_brf_name(text)
        or re.fullmatch(r"\d+", text)
    )


def _clear_failed_brf_lookup_state(metadata: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(metadata or {})
    cleaned.pop("brf_name", None)
    cleaned.pop("brf_resolution", None)
    cleaned.pop("pending_brf_resolution", None)
    cleaned.pop("brf_candidate_buildings", None)
    cleaned.pop("pending_building_advice_request", None)
    cleaned.pop("pending_building_request", None)
    cleaned.pop("request_parts", None)
    cleaned.pop("unanswered_request_parts", None)
    cleaned.pop("context", None)
    clarification = cleaned.get("clarification")
    if isinstance(clarification, dict) and clarification.get("reason") in STALE_BRF_CLARIFICATION_REASONS:
        cleaned.pop("clarification", None)
    return cleaned


def _metadata_has_failed_lookup(metadata: Dict[str, Any]) -> bool:
    return bool(
        _metadata_has_failed_brf_lookup(metadata)
        or (metadata.get("clarification") or {}).get("reason") in FAILED_BUILDING_CLARIFICATION_REASONS
    )


def _clear_failed_lookup_state(metadata: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = _clear_failed_brf_lookup_state(metadata)
    if (metadata.get("clarification") or {}).get("reason") in FAILED_BUILDING_CLARIFICATION_REASONS:
        cleaned.pop("clarification", None)
    # Keep a verified building; an unsuccessful lookup is not verified identity.
    if (metadata.get("building_identity_check") or {}).get("status") != "passed":
        for key in (
            "address", "address_from_user", "requested_address", "address_location_hint",
            "pending_ambiguous_address", "building_identity_check", "building_match",
            "building_candidate_matches", "byggnadsid", "building_id", "building_id_from_user",
            "selected_address_building_id", "pending_address_candidate_ids", "pending_address_query", "building_selection_error",
            "selected_brf_building_id", "selected_brf_addresses", "selected_brf_lookup_address",
        ):
            cleaned.pop(key, None)
    return cleaned


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

    def _prepare_handoff_response(self, thread_id, messages, metadata):
        preview = dict(prepare_expert_handoff(thread_id, messages, metadata))
        sharing_scope = preview.get("sharing_scope") or metadata.get("expert_handoff_scope") or "full_conversation"
        if sharing_scope not in {"summary_only", "full_conversation"}:
            sharing_scope = "full_conversation"
        preview["sharing_scope"] = sharing_scope
        summary_only = sharing_scope == "summary_only"
        enabled = preview["enabled"]
        language = preview.get("language") or handoff_response_language(messages, metadata)
        swedish = language == "sv"
        updated_metadata = {
            **metadata,
            "expert_handoff_preview": preview,
            "expert_handoff_scope": sharing_scope,
            "expert_handoff_pending_confirmation": enabled,
            "expert_handoff_confirmed": False,
            "expert_handoff_status": "pending_confirmation" if enabled else "disabled",
            "expert_handoff_sent": False,
            "expert_handoff_simulated": False,
            "expert_handoff_cc_submitted": False,
            "expert_handoff_error": None,
            "expert_handoff_language": language,
            "expert_handoff_cancelled": False,
        }
        if enabled and swedish:
            cc_note = f"\nKopia till dig: {preview['cc']}" if preview.get("cc") else ""
            sharing_note = (
                "**Det som delas:** endast den exakta sammanfattningen nedan. Ingen samtalsbilaga "
                "eller separat bilaga med byggnads- och ärendeuppgifter skickas. Sammanfattningen "
                "kan innehålla byggnadsuppgifter och personuppgifter; granska dess innehåll innan du godkänner.\n\n"
                if summary_only else
                "**Det som delas:** sammanfattningen nedan och en bilaga med hela samtalet "
                "fram till den här förhandsvisningen samt tillgängliga uppgifter om byggnaden "
                "och ärendet. Bilagan kan innehålla namn, kontaktuppgifter och andra personliga "
                "uppgifter som du har lämnat i tjänsten, inklusive i chatten.\n\n"
            )
            content = (
                f"Jag kan skicka ett e-postmeddelande till den konfigurerade energi- och klimatrådgivaren.\n\n"
                f"**Mottagare:** {preview['recipient']}{cc_note}\n\n"
                + sharing_note
                + f"{preview['summary']}\n\n"
                "Vill du att jag skickar detta till rådgivaren? "
                "Svara **Ja, skicka** för att godkänna eller **Nej, skicka inte** för att avbryta. "
                "Inget skickas innan du har godkänt."
            )
        elif enabled:
            cc_note = f" A copy will also be submitted to {preview['cc']}." if preview.get("cc") else ""
            sharing_note = (
                "Only the exact summary below will be shared. No transcript or separate attachment "
                "with building/session details will be sent. The summary may contain building or personal "
                "details; review its contents before approving.\n\n"
                if summary_only else
                "The email will contain the summary below and an attachment with the full "
                "conversation and available building/session details, including any contact "
                "information you shared.\n\n"
            )
            content = (
                f"I can email the configured energy advisor at {preview['recipient']}.{cc_note} "
                + sharing_note
                + f"{preview['summary']}\n\nDo you want me to send it? "
                "Reply 'Yes, send it' to confirm or 'No, do not send' to cancel. Nothing is sent before confirmation."
            )
        elif swedish:
            content = (
                "E-postöverlämning är inte aktiverad eller färdigkonfigurerad. Inget e-postmeddelande har skickats. "
                "Du kan kopiera sammanfattningen nedan och själv kontakta kommunens energi- och klimatrådgivning via kommunens webbplats.\n\n"
                f"{preview['summary']}"
            )
        else:
            content = (
                "Email handoff is not enabled or fully configured, so no email was sent. "
                "You can copy the summary below and contact your municipality's energy "
                "and climate advisory service (energi- och klimatradgivning) through its website.\n\n"
                f"{preview['summary']}"
            )
        if metadata.get("expert_handoff_requested_changes"):
            content = (
                "Begärd ändring av mottagare eller borttagning av personuppgifter har inte tillämpats. Jag kan inte ändra den konfigurerade mottagaren "
                "eller tillämpa begärd borttagning av personuppgifter via chatten. Granska därför hela "
                "förhandsvisningen nedan innan du väljer om du vill dela detta.\n\n"
                if swedish else
                "Requested recipient changes or removal of personal details have not been applied. I cannot change the configured recipient "
                "or apply requested removal of personal details through chat. Review "
                "the complete preview below before deciding whether to share it.\n\n"
            ) + content
        return {
            "role": "assistant", "content": content,
            "classification": "expert_handoff", "agent_answered": "expert_handoff",
            "route": "expert_handoff",
        }, updated_metadata

    def route_message(self, messages, last_message, metadata, thread_id) -> Dict[str, Any]:
        response, updated_metadata = self._route_message(messages, last_message, metadata, thread_id)
        if isinstance(response, dict):
            response = dict(response)
            updated_metadata = dict(updated_metadata or {})
            prior_receipts = restore_action_receipts(messages, metadata or {})
            if has_action_receipts(prior_receipts) and not updated_metadata.get("action_receipts"):
                updated_metadata["action_receipts"] = prior_receipts
            receipts = record_action_response(response, updated_metadata)
            if has_action_receipts(receipts):
                updated_metadata["action_receipts"] = receipts
                # Backend persistence keeps this private structured state even
                # after Redis hydration/reload; public payloads omit it.
                response["metadata"] = {**(response.get("metadata") or {}), "action_receipts": receipts}
            language = (updated_metadata or {}).get("response_language") or response_language(messages, last_message, metadata)
            # Handoff previews are filtered when created so the displayed and
            # approved summary remains identical to the submitted summary.
            if response.get("route") != "expert_handoff":
                response["content"] = guard_response_links(response.get("content"), language)
            for key in ("sources", "vector_sources"):
                if response.get(key):
                    response[key] = resolve_source_links(response[key])
        return response, updated_metadata

    def _route_message(self, messages, last_message, metadata, thread_id) -> Dict[str, Any]:
        base_metadata = dict(metadata or {})
        prior_clarification = base_metadata.get("clarification")
        if isinstance(prior_clarification, dict) and prior_clarification.get("reason") == "classification_failed":
            base_metadata.pop("clarification", None)
        base_metadata["response_language"] = response_language(messages, last_message, base_metadata)
        receipts = restore_action_receipts(messages, base_metadata)
        if has_action_receipts(receipts):
            base_metadata["action_receipts"] = receipts
        conversation_messages = messages
        full_message_count = len(messages)
        routing_messages = messages
        context_start = base_metadata.get("routing_context_start", 0)
        if isinstance(context_start, int) and 0 < context_start < full_message_count:
            routing_messages = messages[context_start:]
        cleared_failed_brf_lookup = False
        if (
            _metadata_has_failed_lookup(base_metadata)
            and not _looks_like_brf_resolution_continuation(last_message)
        ):
            base_metadata = _clear_failed_lookup_state(base_metadata)
            # Retain the transcript; start a new active context after the failed lookup.
            base_metadata["routing_context_start"] = max(0, full_message_count - 1)
            routing_messages = messages[-1:]
            cleared_failed_brf_lookup = True

        last_assistant_asked_handoff = _last_assistant_asked_for_expert_handoff(messages)
        pending_handoff = bool(base_metadata.get("expert_handoff_pending_confirmation")) or last_assistant_asked_handoff
        handoff_language = (
            (base_metadata.get("expert_handoff_preview") or {}).get("language")
            or base_metadata.get("expert_handoff_language")
            or handoff_response_language(messages, base_metadata)
        )
        swedish_handoff = handoff_language == "sv"
        # Action facts do not require a model classification, a new preview,
        # a building lookup or another SMTP attempt. A status query also keeps
        # an outstanding preview intact; it is never approval or cancellation.
        if is_handoff_status_question(last_message):
            return {
                "role": "assistant", "classification": "expert_handoff",
                "agent_answered": "router", "route": "action_status",
                "content": render_handoff_status(receipts, base_metadata, base_metadata["response_language"]),
            }, base_metadata
        report_requested_by_phrase = bool(
            getattr(self.router, "wants_draft_report", None)
            and self.router.wants_draft_report(last_message)
        )
        if (
            not report_requested_by_phrase and not requests_existing_document(last_message)
            and (is_report_status_question(last_message) or wants_report_retrieval(last_message))
        ):
            report_reply, clarification_reason = report_status_response(receipts, base_metadata, base_metadata["response_language"])
            if clarification_reason:
                base_metadata["clarification"] = {
                    "needed": True, "reason": clarification_reason,
                    "question_asked": report_reply["content"], "resolved": False,
                    "resolved_after_turns": None,
                }
            return {
                **report_reply, "role": "assistant", "classification": "conversational",
                "agent_answered": "router", "route": "clarification" if clarification_reason else "action_status",
            }, base_metadata
        scope_parser = getattr(self.router, "requested_handoff_scope", None)
        requested_scope = scope_parser(last_message) if callable(scope_parser) else None
        if pending_handoff and self.router.is_rejection(last_message):
            updated_metadata = {
                **base_metadata,
                "expert_handoff_pending_confirmation": False,
                "expert_handoff_sent": False,
                "expert_handoff_confirmed": False,
                "expert_handoff_status": "cancelled",
                "expert_handoff_cancelled": True,
                "expert_handoff_simulated": False,
                "expert_handoff_requested_changes": None,
                "expert_handoff_language": handoff_language,
            }
            return {
                "role": "assistant",
                "content": (
                    "Överlämningen är avbruten. Inget e-postmeddelande har skickats för denna förhandsvisning. Vi kan fortsätta här."
                    if swedish_handoff else "Okay, I will not send the conversation to an expert. We can continue here."
                ),
                "classification": "expert_handoff",
                "agent_answered": "expert_handoff",
                "route": "expert_handoff",
            }, updated_metadata

        preview_change = getattr(self.router, "wants_handoff_preview_change", None)
        if pending_handoff and callable(preview_change) and preview_change(last_message):
            updated_metadata = {
                **base_metadata,
                "expert_handoff_scope": requested_scope if requested_scope in {"summary_only", "full_conversation"} else base_metadata.get("expert_handoff_scope", "full_conversation"),
                "expert_handoff_preview": None,
                "expert_handoff_pending_confirmation": True,
                "expert_handoff_confirmed": False,
                "expert_handoff_sent": False,
                "expert_handoff_simulated": False,
                "expert_handoff_cancelled": False,
                "expert_handoff_status": "changes_requested",
                "expert_handoff_requested_changes": last_message,
                "expert_handoff_language": handoff_language,
            }
            return {
                "role": "assistant",
                "content": (
                    "Inget e-postmeddelande har skickats för denna förhandsvisning. Den tidigare förhandsvisningen är inte längre godkänd. "
                    "Jag kan inte ändra den konfigurerade mottagaren eller tillämpa begärd borttagning av "
                    "personuppgifter via chatten. Om du vill gå vidare behöver du granska en ny förhandsvisning "
                    "med den begränsningen och sedan godkänna separat. Du kan också avbryta."
                    if swedish_handoff else
                    "No email has been sent for this preview. The previous preview is no longer approved. I cannot change "
                    "the configured recipient or apply requested removal of personal details "
                    "through chat. To continue, review a new preview with that limitation and confirm separately. "
                    "You can also cancel."
                ),
                "classification": "expert_handoff", "agent_answered": "expert_handoff", "route": "expert_handoff",
            }, updated_metadata

        if pending_handoff and requested_scope in {"summary_only", "full_conversation"}:
            # A scope request changes the preview, even if it contains "yes"
            # or repeats the existing scope. Sending needs a later approval.
            scope_metadata = {
                **base_metadata,
                "expert_handoff_scope": requested_scope,
                "expert_handoff_confirmed": False,
                "expert_handoff_sent": False,
                "expert_handoff_simulated": False,
            }
            source_messages = messages
            old_preview = base_metadata.get("expert_handoff_preview") or {}
            message_count = old_preview.get("message_count")
            scope_only_parser = getattr(self.router, "is_handoff_scope_only_request", None)
            scope_only = callable(scope_only_parser) and scope_only_parser(last_message)
            if scope_only and isinstance(message_count, int) and 0 < message_count <= len(messages):
                subsequent_users = [item for item in messages[message_count:] if item.get("role") == "user"]
                if len(subsequent_users) == 1 and subsequent_users[0].get("content") == last_message:
                    # The service authenticates this unchanged source before
                    # reusing its summary. Do not regenerate it for a scope edit.
                    source_messages = messages[:message_count]
            response, updated_metadata = self._prepare_handoff_response(thread_id, source_messages, scope_metadata)
            response["content"] = (
                "Inget e-postmeddelande har skickats. Granska förhandsvisningen med den valda delningen "
                "och godkänn i ett separat svar om du vill skicka.\n\n"
                if updated_metadata.get("expert_handoff_language") == "sv" else
                "No email has been sent. Review the preview with the selected sharing scope "
                "and confirm in a separate reply if you want to send it.\n\n"
            ) + response["content"]
            return response, updated_metadata

        # Text-only historical prompts are not sufficient consent: the configured
        # recipient and payload must match a concrete preview shown to the user.
        if pending_handoff and self.router.is_confirmation(last_message):
            if not expert_handoff_preview_matches(thread_id, messages, base_metadata):
                response, refreshed_metadata = self._prepare_handoff_response(thread_id, messages, base_metadata)
                response["content"] = (
                    "Förhandsvisningen har ändrats eller gått ut. Granska den nya förhandsvisningen och godkänn igen.\n\n"
                    if refreshed_metadata.get("expert_handoff_language") == "sv"
                    else "The preview has changed or expired. Please review the new preview and confirm again.\n\n"
                ) + response["content"]
                return response, refreshed_metadata
            updated_metadata = {
                **base_metadata,
                "expert_handoff_pending_confirmation": False,
                "expert_handoff_confirmed": True,
                "expert_handoff_simulated": False,
                "expert_handoff_error": None,
                "expert_handoff_language": handoff_language,
            }
            if _evaluation_mode_enabled():
                updated_metadata = {
                    **updated_metadata,
                    "expert_handoff_requested": True,
                    "expert_handoff_sent": False,
                    "expert_handoff_simulated": True,
                    "expert_handoff_status": "simulated",
                }
                return {
                    "role": "assistant",
                    "content": (
                        "Överlämningen simulerades i utvärderingsläget. Inget e-postmeddelande har skickats."
                        if swedish_handoff else "The handoff was simulated in evaluation mode. No real email was sent."
                    ),
                    "classification": "expert_handoff",
                    "agent_answered": "expert_handoff",
                    "route": "expert_handoff",
                }, updated_metadata
            try:
                result = send_expert_handoff_email(thread_id, messages, updated_metadata)
                status = result.get("status", "unknown")
                if status == "sending":
                    status = "unknown"
                was_submitted = status == "submitted"
                updated_metadata = {
                    **updated_metadata,
                    "expert_handoff_requested": True,
                    "expert_handoff_sent": was_submitted,
                    "expert_handoff_status": status,
                    "expert_handoff_cc_submitted": bool(result.get("cc_submitted")),
                }
                if was_submitted:
                    recipient = updated_metadata["expert_handoff_preview"]["recipient"]
                    cc_note = " Your copy was also accepted." if result.get("cc_submitted") else ""
                    if updated_metadata["expert_handoff_preview"].get("cc") and not result.get("cc_submitted"):
                        cc_note = " Your CC copy was not accepted."
                    repeat_note = " This request was already submitted; no duplicate email was sent." if result.get("duplicate") else ""
                    if swedish_handoff:
                        cc_note = " E-postservern tog också emot din kopia." if result.get("cc_submitted") else ""
                        if updated_metadata["expert_handoff_preview"].get("cc") and not result.get("cc_submitted"):
                            cc_note = " Din kopia kunde inte lämnas till e-postservern."
                        repeat_note = " Den här begäran hade redan lämnats till e-postservern. Inget dubblettmeddelande har skickats." if result.get("duplicate") else ""
                    return {
                        "role": "assistant",
                        "content": (
                            f"E-postservern tog emot meddelandet till {recipient}.{cc_note} "
                            f"Leverans till mottagarens inkorg är inte bekräftad.{repeat_note}"
                            if swedish_handoff else
                            f"The mail server accepted the email for {recipient}.{cc_note} Delivery to the inbox is not confirmed.{repeat_note}"
                        ),
                        "classification": "expert_handoff",
                        "agent_answered": "expert_handoff",
                        "route": "expert_handoff",
                    }, updated_metadata
            except Exception as exc:
                updated_metadata = {
                    **updated_metadata,
                    "expert_handoff_requested": True,
                    "expert_handoff_sent": False,
                    "expert_handoff_status": "unknown",
                    "expert_handoff_error": type(exc).__name__,
                }

            status = updated_metadata.get("expert_handoff_status")
            failure_note = (
                "I could not confirm whether the mail server accepted the email. "
                "I will not automatically resend it because that could create a duplicate."
                if status in {"unknown", "sending"} else
                "I could not send the email to the configured energy advisor."
            )
            if swedish_handoff:
                failure_note = (
                    "Jag kunde inte bekräfta om e-postservern tog emot meddelandet. "
                    "Jag skickar det inte igen automatiskt eftersom det kan skapa en dubblett."
                    if status in {"unknown", "sending"} else
                    "Jag kunde inte skicka e-postmeddelandet till den konfigurerade energi- och klimatrådgivaren."
                )
            copy_note = (
                " Du kan kopiera sammanfattningen nedan och själv kontakta kommunens energi- och klimatrådgivning via kommunens webbplats.\n\n"
                if swedish_handoff else " You can copy the summary below and contact your municipality's energy and climate advisory service through its website.\n\n"
            )
            return {
                "role": "assistant",
                "content": (
                    failure_note + copy_note
                    + (base_metadata.get("expert_handoff_preview", {}).get("summary")
                       or copyable_expert_handoff_summary(messages, base_metadata))
                ),
                "classification": "expert_handoff",
                "agent_answered": "expert_handoff",
                "route": "expert_handoff",
            }, updated_metadata

        if pending_handoff:
            # A new question or topic supersedes the pending consent, including
            # fast conversational/clarification routes that return before classification.
            base_metadata = {
                **base_metadata,
                "expert_handoff_pending_confirmation": False,
                "expert_handoff_confirmed": False,
                "expert_handoff_status": "superseded",
            }

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
                    base_metadata["response_language"],
                ),
                "classification": "out_of_scope",
                "agent_answered": "boundary",
                "route": "out_of_scope",
            }, updated_metadata

        report_requested = bool(
            getattr(self.router, "wants_draft_report", None)
            and self.router.wants_draft_report(last_message)
        )
        expert_handoff_requested_by_phrase = bool(
            getattr(self.router, "wants_expert_handoff", None)
            and self.router.wants_expert_handoff(last_message)
        )
        if report_requested:
            report_response = generate_draft_report_response(thread_id, messages, base_metadata)
            report_response.setdefault("route", "report_generation")
            return report_response, base_metadata
        if expert_handoff_requested_by_phrase:
            if requested_scope in {"summary_only", "full_conversation"}:
                base_metadata["expert_handoff_scope"] = requested_scope
            if callable(preview_change) and preview_change(last_message):
                base_metadata["expert_handoff_requested_changes"] = last_message
            return self._prepare_handoff_response(thread_id, messages, base_metadata)

        messages = routing_messages
        public_brf_or_org_question = _generic_public_brf_or_org_question_without_identity(last_message)
        explicit_general_request = any(
            re.search(pattern, str(last_message or "").lower())
            for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS
        ) and not _has_explicit_site_identifier(last_message)
        broad_advice_without_identity = _advisory_without_resolved_building_context(
            last_message, messages, base_metadata,
        )
        independent_general_request = (
            public_brf_or_org_question or explicit_general_request or broad_advice_without_identity
        )

        if not independent_general_request and (
            _awaiting_address_disambiguation(base_metadata)
            or _recent_assistant_requested_address_disambiguation(messages)
        ):
            out, metadata_updated = self.building.handle_building_query(
                last_message,
                messages,
                base_metadata,
                thread_id,
            )
            out["role"] = "assistant"
            out["classification"] = "building_specific"
            return out, metadata_updated

        fast_conversational = _fast_conversational_response(last_message)
        if fast_conversational:
            return {
                "role": "assistant",
                "content": fast_conversational,
                "classification": "conversational",
                "agent_answered": "fast_conversational",
                "route": "generic",
            }, base_metadata

        fast_multi_address = _fast_multi_address_clarification(last_message, base_metadata["response_language"])
        if fast_multi_address:
            updated_metadata = {
                **base_metadata,
                "clarification": {
                    "needed": True,
                    "reason": "missing_multi_address_values",
                    "question_asked": fast_multi_address,
                    "resolved": False,
                    "resolved_after_turns": None,
                },
                "building_match": {
                    "match_confidence": "unknown",
                    "ambiguous": True,
                },
            }
            return {
                "role": "assistant",
                "content": fast_multi_address,
                "classification": "building_specific",
                "agent_answered": "fast_multi_address_clarification",
                "route": "clarification",
            }, updated_metadata

        if _has_multi_address_values_question(last_message):
            out, metadata_updated = self.building.handle_building_query(
                last_message,
                messages,
                base_metadata,
                thread_id,
            )
            out["role"] = "assistant"
            out["classification"] = "building_specific"
            return out, metadata_updated

        # Try to read prior classification if present
        if len(messages) >= 2 and not cleared_failed_brf_lookup:
            previous_classification = (messages[-2] or {}).get("classification")
        else:
            previous_classification = None

        classified = self.router.classify_question(last_message, previous_classification, history=messages)

        classified = _normalize_classification(classified)
        if classified == "draft_energy_report" and requests_existing_document(last_message):
            classified = (
                "building_specific" if _has_recent_building_context(messages, base_metadata)
                or _requires_building_specific_flow(last_message)
                or re.search(r"\b(?:our|my|vår|vårt|min|mitt)\b", last_message, re.IGNORECASE)
                else "generic"
            )
        generic_advisory_continuation = _generic_advisory_continuation_without_resolved_building(
            last_message,
            messages,
            base_metadata,
        )
        public_brf_or_org_question = _generic_public_brf_or_org_question_without_identity(last_message)
        requires_building_specific = (
            _requires_building_specific_flow(last_message)
            or _requires_building_specific_followup(last_message, messages, base_metadata)
        )
        if classified is None:
            classified = _recover_classification(last_message, messages, base_metadata)
        elif (
            broad_advice_without_identity
            or generic_advisory_continuation
            or public_brf_or_org_question
            or explicit_general_request
        ) and not expert_handoff_requested_by_phrase:
            if classified in {"building_specific", "expert_handoff", "conversational"}:
                classified = "generic"
        elif classified in {"generic", "conversational"} and requires_building_specific:
            classified = "building_specific"
        elif classified == "expert_handoff" and requires_building_specific and not expert_handoff_requested_by_phrase:
            classified = "building_specific"

        if (
            classified == "expert_handoff" and not expert_handoff_requested_by_phrase
            or classified == "draft_energy_report" and not report_requested
        ):
            # A model label supplies routing evidence, not permission to create
            # an artifact or propose an unsolicited handoff.
            classified = _recover_classification(last_message, messages, base_metadata)

        if pending_handoff and classified != "expert_handoff":
            base_metadata = {
                **base_metadata,
                "expert_handoff_pending_confirmation": False,
                "expert_handoff_confirmed": False,
            }

        if classified == "draft_energy_report":
            report_response = generate_draft_report_response(thread_id, conversation_messages, base_metadata)
            report_response.setdefault("route", "report_generation")
            return report_response, base_metadata

        if classified == "expert_handoff":
            if requested_scope in {"summary_only", "full_conversation"}:
                base_metadata["expert_handoff_scope"] = requested_scope
            if callable(preview_change) and preview_change(last_message):
                base_metadata["expert_handoff_requested_changes"] = last_message
            return self._prepare_handoff_response(thread_id, conversation_messages, base_metadata)

        if classified == "generic":
            # Identity recovery can shorten routing context. Advisory generation
            # still needs the user's original symptoms, constraints and corrections.
            if has_action_receipts(receipts) and getattr(self.generic, "supports_action_receipts", False):
                generic_response = self.generic.handle_generic_input(
                    last_message, conversation_messages, action_receipts=receipts,
                )
            else:
                generic_response = self.generic.handle_generic_input(last_message, conversation_messages)
            if isinstance(generic_response, dict):
                response_text = generic_response.get("content", "")
                sources = generic_response.get("sources") or []
            else:
                response_text = generic_response or ""
                sources = []

            payload = {
                'role' : 'assistant' , 
                'content' :localize_service_error(response_text, base_metadata["response_language"]) ,
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
                'content' :localize_service_error(response_text, base_metadata["response_language"]),
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

        # Recovery cannot determine a supported route: ask without dispatching.
        clarification_text = (
            "Jag kunde inte avgöra hur frågan ska hanteras. Kan du formulera om den?"
            if base_metadata["response_language"] == "sv"
            else "I could not determine how to handle that question. Could you rephrase it?"
        )
        base_metadata = {
            **base_metadata,
            "clarification": {
                "needed": True,
                "reason": "classification_failed",
                "question_asked": clarification_text,
                "resolved": False,
                "resolved_after_turns": None,
            },
        }
        return {
                'role' : 'assistant' , 
                'content' : clarification_text,
                'classification' : "conversational",
                'agent_answered' : "router",
                'route': "clarification",
            } , base_metadata
