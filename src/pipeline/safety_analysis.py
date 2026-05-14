from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional


BUILDING_IDENTIFIER_KEYS = (
    "building_id",
    "byggnadsid",
    "50a_uuid",
    "uuid",
    "oden_uuid",
    "01a_fnr",
    "epc_idadr",
)

ADDRESS_KEYS = (
    "address",
    "address_from_user",
    "official_address",
    "epc_idadr",
)

FRESHNESS_YEAR_KEYS = (
    "energy_declaration_year",
    "energideklaration_ar",
    "energydeclarationyear",
    "epc_year",
    "declaration_year",
    "energy_year",
)

IMPORTANT_FACT_KEYS = (
    "energy_class",
    "energy_performance",
    "heating_system",
    "ventilation_type",
    "electricity_use",
    "district_heating_use",
    "energy_declaration_year",
    "renovation_information",
)

OUT_OF_SCOPE_RULES = [
    {
        "category": "legal_advice",
        "phrases": (
            "legally",
            "legal",
            "law",
            "lawsuit",
            "contract dispute",
            "force residents",
            "can our brf force",
        ),
        "redirect_to": "relevant_authority_or_legal_expert",
    },
    {
        "category": "financial_advice",
        "phrases": (
            "which loan should",
            "should we borrow",
            "financial advice",
            "investment portfolio",
            "exact return on investment",
            "guaranteed payback",
        ),
        "redirect_to": "advisor_or_financial_specialist",
    },
    {
        "category": "detailed_engineering_calculation",
        "phrases": (
            "dimension the system",
            "exact duct sizing",
            "load calculation",
            "detailed engineering calculation",
            "structural calculation",
        ),
        "redirect_to": "qualified_engineer_or_installer",
    },
    {
        "category": "installer_or_vendor_recommendation",
        "phrases": (
            "which installer",
            "which vendor",
            "recommend a contractor",
            "best installer",
            "best vendor",
        ),
        "redirect_to": "advisor_or_procurement_process",
    },
    {
        "category": "personal_data_or_privacy_issue",
        "phrases": (
            "personal data",
            "privacy law",
            "gdpr",
            "resident data",
        ),
        "redirect_to": "privacy_contact_or_relevant_authority",
    },
]


def _is_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _stringify(value: Any) -> Optional[str]:
    if not _is_present(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for nested in value.values():
            yield from _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk(nested)


def _iter_dicts(value: Any) -> Iterable[Dict[str, Any]]:
    for node in _walk(value):
        if isinstance(node, dict):
            yield node


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    return value


def extract_retrieved_facts(*sources: Any) -> Dict[str, Any]:
    facts: Dict[str, Any] = {}
    for source in sources:
        for node in _iter_dicts(source):
            for key, value in node.items():
                if key == "hits":
                    continue
                if isinstance(value, (dict, list)):
                    continue
                if not _is_present(value):
                    continue
                facts.setdefault(str(key), _normalize_scalar(value))
    return facts


def _extract_candidate_ids(*sources: Any) -> List[str]:
    candidates: List[str] = []
    for source in sources:
        for node in _iter_dicts(source):
            lower_map = {str(key).lower(): key for key in node.keys()}
            for key in BUILDING_IDENTIFIER_KEYS:
                original = lower_map.get(key.lower())
                if original is None:
                    continue
                value = _stringify(node.get(original))
                if value and value not in candidates:
                    candidates.append(value)
    return candidates


def _extract_candidate_addresses(*sources: Any) -> List[str]:
    candidates: List[str] = []
    for source in sources:
        for node in _iter_dicts(source):
            lower_map = {str(key).lower(): key for key in node.keys()}
            for key in ADDRESS_KEYS:
                original = lower_map.get(key.lower())
                if original is None:
                    continue
                value = _stringify(node.get(original))
                if value and value not in candidates:
                    candidates.append(value)
    return candidates


def assess_building_identity(
    metadata: Dict[str, Any],
    *retrieved_sources: Any,
) -> Dict[str, Any]:
    metadata = metadata or {}
    existing_ids = _extract_candidate_ids(metadata)
    candidate_ids = _extract_candidate_ids(*retrieved_sources)
    candidate_addresses = _extract_candidate_addresses(*retrieved_sources)

    matched_building_id = candidate_ids[0] if len(candidate_ids) == 1 else existing_ids[0] if len(existing_ids) == 1 else None
    conflicting_metadata = bool(
        existing_ids
        and candidate_ids
        and set(existing_ids).difference(candidate_ids)
        and set(candidate_ids).difference(existing_ids)
    )
    multiple_matches = len(candidate_ids) > 1 or len(candidate_addresses) > 1
    parser_ctx = metadata.get("context") or {}
    ambiguous = multiple_matches or bool(parser_ctx.get("ambiguous") or parser_ctx.get("ambigious"))

    if conflicting_metadata:
        status = "conflict"
    elif ambiguous:
        status = "ambiguous"
    elif matched_building_id:
        status = "passed"
    else:
        status = "missing"

    return {
        "status": status,
        "matched_building_id": matched_building_id,
        "matched_address": candidate_addresses[0] if len(candidate_addresses) == 1 else metadata.get("address"),
        "ambiguous": ambiguous,
        "multiple_matches": multiple_matches,
        "conflicting_metadata": conflicting_metadata,
        "candidate_building_ids": candidate_ids,
        "candidate_addresses": candidate_addresses,
    }


def build_clarification_question(reason: Optional[str]) -> str:
    reason = reason or "incomplete_question"
    prompts = {
        "missing_brf_name": "Which BRF do you mean? If possible, please share the BRF name and full address.",
        "missing_address": "To give building-specific advice safely, I need the BRF name or the full building address.",
        "ambiguous_brf": "I found more than one possible BRF. Could you provide the full BRF name or address so I use the correct building?",
        "ambiguous_address": "I found more than one possible building match. Could you provide the full address or BRF name so I avoid using the wrong building information?",
        "missing_building_data": "I found the building, but I do not have enough building data yet for personalized advice. Could you share any more details you have, such as the BRF name, address, or the specific system you want to ask about?",
        "insufficient_data_for_personalized_advice": "I can give general guidance, but I need more building details before I can personalize the advice. Could you share the BRF name or full address?",
        "incomplete_question": "Could you clarify your request a bit more so I can answer safely?",
    }
    return prompts.get(reason, prompts["incomplete_question"])


def _coerce_year(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", value)
        if match:
            return int(match.group(1))
    return None


def compute_data_freshness(*sources: Any, current_year: Optional[int] = None) -> Dict[str, Any]:
    year = None
    for source in sources:
        for node in _iter_dicts(source):
            lower_map = {str(key).lower(): key for key in node.keys()}
            for key in FRESHNESS_YEAR_KEYS:
                original = lower_map.get(key.lower())
                if original is None:
                    continue
                year = _coerce_year(node.get(original))
                if year:
                    break
            if year:
                break
        if year:
            break

    current = current_year or date.today().year
    if not year:
        return {
            "energy_declaration_year": None,
            "data_age_years": None,
            "freshness_status": "unknown",
            "requires_warning": True,
        }

    age = max(current - year, 0)
    if age <= 3:
        status = "recent"
    elif age <= 7:
        status = "moderate"
    else:
        status = "old"

    return {
        "energy_declaration_year": year,
        "data_age_years": age,
        "freshness_status": status,
        "requires_warning": status == "old",
    }


def compute_uncertainty(
    metadata: Dict[str, Any],
    retrieved_facts: Dict[str, Any],
    *,
    route: Optional[str] = None,
) -> Dict[str, Any]:
    metadata = metadata or {}
    retrieved_facts = retrieved_facts or {}
    confirmed = [key for key in IMPORTANT_FACT_KEYS if _is_present(retrieved_facts.get(key))]
    missing = [key for key in IMPORTANT_FACT_KEYS if key not in confirmed]

    assumptions: List[str] = []
    freshness = metadata.get("data_freshness") or {}
    if freshness.get("requires_warning"):
        year = freshness.get("energy_declaration_year")
        if year:
            assumptions.append(
                f"This answer relies partly on an energy declaration from {year}, so building conditions may have changed since then."
            )
        else:
            assumptions.append(
                "The age of the available energy-declaration data is unknown, so some building details may be outdated."
            )
    if "renovation_information" in missing:
        assumptions.append("No recent renovation history is confirmed in the available data.")

    identity = metadata.get("building_identity_check") or {}
    if identity.get("status") in {"ambiguous", "conflict", "missing"}:
        confidence = "low"
    elif len(confirmed) >= 4:
        confidence = "high"
    elif len(confirmed) >= 2:
        confidence = "medium"
    else:
        confidence = "low"

    if route == "clarification":
        confidence = "low"

    return {
        "confirmed_facts": confirmed,
        "missing_facts": missing,
        "assumptions": assumptions,
        "confidence": confidence,
    }


def apply_response_safety_notes(response_text: str, metadata: Dict[str, Any]) -> str:
    text = (response_text or "").strip()
    metadata = metadata or {}
    additions: List[str] = []

    freshness = metadata.get("data_freshness") or {}
    if freshness.get("requires_warning"):
        year = freshness.get("energy_declaration_year")
        if year:
            additions.append(
                f"This answer is based on an energy declaration from {year}. If the building has changed since then, some details may be outdated."
            )
        else:
            additions.append(
                "Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is."
            )

    uncertainty = metadata.get("uncertainty") or {}
    missing = uncertainty.get("missing_facts") or []
    if missing:
        additions.append(
            "I do not have confirmed data for: " + ", ".join(missing[:4]) + "."
        )

    if not additions:
        return text

    note_block = "Note: " + " ".join(additions)
    if note_block.lower() in text.lower():
        return text
    return f"{text}\n\n{note_block}".strip()


def detect_out_of_scope(message: str) -> Optional[Dict[str, str]]:
    lowered = (message or "").lower()
    if not lowered:
        return None

    for rule in OUT_OF_SCOPE_RULES:
        if any(phrase in lowered for phrase in rule["phrases"]):
            return {
                "out_of_scope_type": rule["category"],
                "redirect_to": rule["redirect_to"],
            }
    return None


def build_out_of_scope_response(out_of_scope_type: str, redirect_to: Optional[str]) -> str:
    explanations = {
        "legal_advice": "I can give general energy-efficiency information, but I should not give legal advice or make legal judgments for a BRF.",
        "financial_advice": "I can discuss general energy measures, but I should not give financial advice or make investment decisions for a BRF.",
        "detailed_engineering_calculation": "I can explain general options, but detailed engineering calculations should be handled by a qualified engineer or installer.",
        "installer_or_vendor_recommendation": "I can explain what to look for, but I should not recommend a specific installer or vendor.",
        "personal_data_or_privacy_issue": "I can explain general principles, but privacy and personal-data questions should be handled through the appropriate authority or responsible contact.",
    }
    next_steps = {
        "relevant_authority_or_legal_expert": "Please check with a legal expert, your BRF's advisor, or the relevant authority.",
        "advisor_or_financial_specialist": "Please discuss this with an advisor or financial specialist before making a decision.",
        "qualified_engineer_or_installer": "A qualified engineer or installer should assess the building before any final decision is made.",
        "advisor_or_procurement_process": "An advisor or a formal procurement process is the safer way to compare vendors.",
        "privacy_contact_or_relevant_authority": "Please contact the relevant privacy lead or authority for guidance.",
    }
    base = explanations.get(
        out_of_scope_type,
        "I can help with general energy-advice questions, but this request goes beyond the safe scope of the system.",
    )
    follow_up = next_steps.get(
        redirect_to or "",
        "Please consult a relevant advisor or authority for a reliable answer.",
    )
    return f"{base} {follow_up}"
