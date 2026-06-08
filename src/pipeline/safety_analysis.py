from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from src.pipeline.response_language import choose_language_text, response_language_for_message


BUILDING_IDENTIFIER_KEYS = (
    "building_id",
    "byggnadsid",
    "50a_uuid",
    "uuid",
    "oden_uuid",
    "01a_fnr",
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
    "epc_godkand",
    "epc_egiversion",
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
            "juridiskt",
            "juridisk",
            "lagligt",
            "lag",
            "tvinga boende",
            "kan vår brf tvinga",
            "kan var brf tvinga",
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
            "vilket lån",
            "vilket lan",
            "bör vi låna",
            "bor vi lana",
            "ekonomisk rådgivning",
            "ekonomisk radgivning",
            "finansiell rådgivning",
            "finansiell radgivning",
            "garanterad återbetalning",
            "garanterad aterbetalning",
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
            "dimensionera systemet",
            "exakt kanal",
            "lastberäkning",
            "lastberakning",
            "detaljerad teknisk beräkning",
            "detaljerad teknisk berakning",
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
            "vilken installatör",
            "vilken installator",
            "vilken leverantör",
            "vilken leverantor",
            "rekommendera entreprenör",
            "rekommendera entreprenor",
            "bästa installatör",
            "basta installator",
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
            "personuppgifter",
            "integritetslag",
            "boendedata",
        ),
        "redirect_to": "privacy_contact_or_relevant_authority",
    },
    {
        "category": "external_contact_or_register_update",
        "phrases": (
            "changed board members",
            "change board members",
            "new board members",
            "update our contact details",
            "update contact details",
            "update the contact details",
            "update our contacts",
            "update contacts",
            "update board contact",
            "update board-member contact",
            "update board member contact",
            "change our contact details",
            "change contact details",
            "ändrat styrelseledamöter",
            "andrat styrelseledamoter",
            "ändra styrelseledamöter",
            "andra styrelseledamoter",
            "uppdatera våra kontaktuppgifter",
            "uppdatera vara kontaktuppgifter",
            "uppdatera kontaktuppgifter",
            "ändra våra kontaktuppgifter",
            "andra vara kontaktuppgifter",
        ),
        "redirect_to": "official_register_or_admin_system",
    },
]

BOUNDARY_REFUSAL_PHRASES = (
    "should not give",
    "cannot give",
    "can't give",
    "do not give",
    "not give legal",
    "not give financial",
    "should not recommend",
    "should be handled",
    "goes beyond",
    "beyond the safe scope",
    "cannot update",
    "can't update",
    "do not update",
    "cannot make updates",
    "bör inte ge",
    "kan inte ge",
    "ska inte ge",
    "bör inte rekommendera",
    "kan inte uppdatera",
    "kan inte göra uppdateringen",
    "kan inte utföra uppdateringen",
)

REDIRECT_TARGET_PHRASES = {
    "relevant_authority_or_legal_expert": (
        "legal expert",
        "relevant authority",
        "authority",
        "juridisk expert",
        "relevant myndighet",
        "myndighet",
    ),
    "advisor_or_financial_specialist": (
        "financial specialist",
        "advisor",
        "finansiell specialist",
        "rådgivare",
        "radgivare",
    ),
    "qualified_engineer_or_installer": (
        "qualified engineer",
        "installer",
        "kvalificerad ingenjör",
        "kvalificerad ingenjor",
        "installatör",
        "installator",
    ),
    "advisor_or_procurement_process": (
        "advisor",
        "procurement",
        "rådgivare",
        "radgivare",
        "upphandling",
    ),
    "privacy_contact_or_relevant_authority": (
        "privacy",
        "authority",
        "integritet",
        "myndighet",
    ),
    "official_register_or_admin_system": (
        "official",
        "register",
        "admin",
        "property manager",
        "officiellt",
        "register",
        "admin",
        "förvaltare",
        "forvaltare",
    ),
    "ekr_advisor": (
        "ekr",
        "advisor",
        "expert",
    ),
}


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


def _normalize_address_text(value: Any) -> Optional[str]:
    if not _is_present(value):
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return re.sub(r"\s+", " ", text)


def _ascii_fold(value: Any) -> str:
    text = str(value or "")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()


def _first_present(facts: Dict[str, Any], keys: Iterable[str]) -> Any:
    lower_map = {str(key).lower(): key for key in facts.keys()}
    for key in keys:
        original = lower_map.get(key.lower())
        if original is not None and _is_present(facts.get(original)):
            return facts.get(original)
    return None


def _truthy_building_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    folded = _ascii_fold(value).strip()
    return folded not in {"", "0", "0.0", "false", "no", "nej", "none", "null", "nan"}


def _normalize_heating_system(value: Any) -> Optional[str]:
    raw = _stringify(value)
    if not raw:
        return None
    folded = _ascii_fold(raw)
    if "fjarrvarme" in folded or "district heating" in folded:
        return "district heating"
    if "bergvarme" in folded or "ground source" in folded:
        return "ground source heat pump"
    if "franluft" in folded or "exhaust air" in folded:
        return "exhaust air heat pump"
    if "luftvatten" in folded or "air water" in folded:
        return "air-to-water heat pump"
    if "luftluft" in folded or "air air" in folded:
        return "air-to-air heat pump"
    if "direktel" in folded or "electric" in folded:
        return "direct electric heating"
    if "olja" in folded or "oil" in folded:
        return "oil heating"
    if "gas" in folded:
        return "gas heating"
    if "ved" in folded or "wood" in folded:
        return "wood heating"
    return raw


def _is_affirmative(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    folded = _ascii_fold(value).strip()
    return folded in {"yes", "ja", "true", "1", "y"}


def _derive_ventilation_type(facts: Dict[str, Any]) -> Optional[str]:
    if _is_affirmative(_first_present(facts, ("epc_venttypftx", "venttypftx"))):
        return "FTX"
    if _is_affirmative(_first_present(facts, ("epc_venttypft", "venttypft"))):
        return "FT"
    if _is_affirmative(_first_present(facts, ("epc_venttypfmed", "venttypfmed"))):
        return "F with heat recovery"
    if _is_affirmative(_first_present(facts, ("epc_venttypf", "venttypf"))):
        return "F"
    if _is_affirmative(_first_present(facts, ("epc_venttypsjalvdrag", "venttypsjalvdrag"))):
        return "Självdrag"
    return None


def _derive_fact_aliases(facts: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(facts, dict):
        return facts

    heating_raw = _first_present(
        facts,
        (
            "heating_system",
            "epc_huvudsakliguppvarmning_calc",
            "huvudsaklig_uppvarmning",
            "primary_heating",
            "main_heating_system",
        ),
    )
    if not _is_present(facts.get("heating_system")) and heating_raw is not None:
        normalized = _normalize_heating_system(heating_raw)
        if normalized:
            facts["heating_system"] = normalized

    district_heating_total = _first_present(
        facts,
        (
            "district_heating_use",
            "epc_egifjarrvarme",
        ),
    )
    if not _is_present(facts.get("district_heating_use")) and district_heating_total is not None:
        facts["district_heating_use"] = district_heating_total

    district_heating_space_heating = _first_present(
        facts,
        (
            "district_heating_space_heating",
            "epc_egifjarrvarmeuppv",
        ),
    )
    if (
        not _is_present(facts.get("district_heating_space_heating"))
        and district_heating_space_heating is not None
    ):
        facts["district_heating_space_heating"] = district_heating_space_heating

    district_heating_domestic_hot_water = _first_present(
        facts,
        (
            "district_heating_domestic_hot_water",
            "district_heating_hot_water",
            "epc_egifjarrvarmevv",
        ),
    )
    if (
        not _is_present(facts.get("district_heating_domestic_hot_water"))
        and district_heating_domestic_hot_water is not None
    ):
        facts["district_heating_domestic_hot_water"] = district_heating_domestic_hot_water

    domestic_hot_water = _first_present(
        facts,
        (
            "domestic_hot_water",
            "epc_egitappvarmvatten_calc",
            "epc_egitappvarmvatten",
        ),
    )
    if not _is_present(facts.get("domestic_hot_water")) and domestic_hot_water is not None:
        facts["domestic_hot_water"] = domestic_hot_water

    district_heating_raw = _first_present(
        facts,
        (
            "district_heating_use",
            "district_heating_space_heating",
            "district_heating_domestic_hot_water",
            "epc_egigruppfjarrvarme",
        ),
    )

    if not _is_present(facts.get("heating_system")) and _truthy_building_value(district_heating_raw):
        facts["heating_system"] = "district heating"

    if not _is_present(facts.get("energy_class")):
        value = _first_present(
            facts,
            (
                "declaredEnergyClass",
                "epc_egienergiklass2020_calc",
                "epc_egienergiklass2016_calc",
                "energyClass",
            ),
        )
        if value is not None:
            facts["energy_class"] = value

    if not _is_present(facts.get("energy_performance")):
        value = _first_present(
            facts,
            (
                "EnergyClassKwhM2",
                "energy_performance",
                "epc_egienergiprestanda",
                "epc_egiprimarenergital2019",
                "epc_egiprimarenergital2020_calc",
                "epc_egiprimarenergital2020",
                "epc_egispecifikenergianvandning",
                "epc_egispecifikenergianvandning_calc",
            ),
        )
        if value is not None:
            facts["energy_performance"] = value

    if not _is_present(facts.get("specific_energy_use")):
        value = _first_present(
            facts,
            (
                "specific_energy_use",
                "epc_egispecifikenergianvandning",
                "epc_egispecifikenergianvandning_calc",
                "epc_egispecifikenergianvandning_eindex_calc",
            ),
        )
        if value is not None:
            facts["specific_energy_use"] = value

    if not _is_present(facts.get("primary_energy_number")):
        value = _first_present(
            facts,
            (
                "primary_energy_number",
                "primary_energy",
                "epc_egiprimarenergital2020_calc",
                "epc_egiprimarenergital2020",
                "epc_egiprimarenergital2019",
                "epc_egiprimarenergital",
            ),
        )
        if value is not None:
            facts["primary_energy_number"] = value

    if not _is_present(facts.get("ventilation_type")):
        value = _first_present(
            facts,
            (
                "ventilation_type",
                "ventilation",
                "ventilation_system",
                "epc_ventilation_type",
            ),
        )
        if value is None:
            value = _derive_ventilation_type(facts)
        if value is not None:
            facts["ventilation_type"] = value

    if not _is_present(facts.get("electricity_use")):
        value = _first_present(
            facts,
            (
                "electricity_use",
                "epc_el_calc",
                "epc_egisumma2",
                "epc_egisumma2_calc",
                "epc_egifastighet",
                "electricity_purchased",
                "electricity_use_property",
            ),
        )
        if value is not None:
            facts["electricity_use"] = value

    if not _is_present(facts.get("construction_year")):
        value = _first_present(
            facts,
            (
                "construction_year",
                "year_built",
                "built_year",
                "byggar",
                "epc_egennybyggar",
            ),
        )
        year = _coerce_year(value)
        if year is not None:
            facts["construction_year"] = year

    if not _is_present(facts.get("energy_declaration_year")):
        value = _first_present(
            facts,
            (
                "energy_declaration_year",
                "epc_godkand",
                "epc_egiversion",
            ),
        )
        year = _coerce_year(value)
        if year is not None:
            facts["energy_declaration_year"] = year

    return facts


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
    return _derive_fact_aliases(facts)


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
                if value:
                    if value not in candidates:
                        candidates.append(value)
                    break
    return candidates


def _extract_candidate_addresses(*sources: Any) -> List[str]:
    candidates: List[str] = []
    seen_normalized = set()
    for source in sources:
        for node in _iter_dicts(source):
            lower_map = {str(key).lower(): key for key in node.keys()}
            for key in ADDRESS_KEYS:
                original = lower_map.get(key.lower())
                if original is None:
                    continue
                value = _stringify(node.get(original))
                normalized = _normalize_address_text(value)
                if value and normalized not in seen_normalized:
                    candidates.append(value)
                    seen_normalized.add(normalized)
    return candidates


def assess_building_identity(
    metadata: Dict[str, Any],
    *retrieved_sources: Any,
) -> Dict[str, Any]:
    metadata = metadata or {}
    existing_ids = _extract_candidate_ids(metadata)
    candidate_ids = _extract_candidate_ids(*retrieved_sources)
    candidate_addresses = _extract_candidate_addresses(*retrieved_sources)
    input_address = _stringify(metadata.get("address") or metadata.get("address_from_user"))
    normalized_input_address = _normalize_address_text(input_address)
    normalized_candidate_addresses = {
        normalized
        for normalized in (_normalize_address_text(address) for address in candidate_addresses)
        if normalized
    }
    unique_candidate_ids = set(candidate_ids)
    same_input_address = bool(
        normalized_input_address
        and normalized_candidate_addresses
        and normalized_candidate_addresses == {normalized_input_address}
    )
    multiple_candidate_building_ids = len(unique_candidate_ids) > 1
    multiple_records_same_address = bool(
        same_input_address
        and not multiple_candidate_building_ids
        and (len(candidate_ids) == 1 or len(candidate_addresses) > 1)
    )
    multiple_addresses_same_explicit_building = bool(
        len(existing_ids) == 1
        and candidate_ids
        and set(candidate_ids) == {existing_ids[0]}
        and len(candidate_addresses) > 1
    )
    multiple_addresses_same_building_id = bool(
        len(candidate_ids) == 1
        and len(candidate_addresses) > 1
    )

    matched_building_id = candidate_ids[0] if len(candidate_ids) == 1 else existing_ids[0] if len(existing_ids) == 1 else None
    conflicting_metadata = bool(
        existing_ids
        and candidate_ids
        and set(existing_ids).difference(candidate_ids)
        and set(candidate_ids).difference(existing_ids)
    )
    multiple_matches = (
        (len(candidate_ids) > 1 or len(candidate_addresses) > 1)
        and not multiple_records_same_address
        and not multiple_addresses_same_explicit_building
        and not multiple_addresses_same_building_id
    )
    parser_ctx = metadata.get("context") or {}
    resolved_to_single_building = bool(
        matched_building_id
        or multiple_records_same_address
        or multiple_addresses_same_explicit_building
        or multiple_addresses_same_building_id
        or normalized_candidate_addresses == {normalized_input_address}
    )
    ambiguous = multiple_matches or (
        bool(parser_ctx.get("ambiguous") or parser_ctx.get("ambigious"))
        and not resolved_to_single_building
    )

    if conflicting_metadata:
        status = "conflict"
    elif ambiguous:
        status = "ambiguous"
    elif resolved_to_single_building:
        status = "passed"
    else:
        status = "missing"

    return {
        "status": status,
        "matched_building_id": matched_building_id,
        "matched_address": candidate_addresses[0] if len(candidate_addresses) == 1 else input_address,
        "ambiguous": ambiguous,
        "multiple_matches": multiple_matches,
        "multiple_records_same_address": multiple_records_same_address,
        "multiple_addresses_same_explicit_building": multiple_addresses_same_explicit_building,
        "multiple_addresses_same_building_id": multiple_addresses_same_building_id,
        "conflicting_metadata": conflicting_metadata,
        "candidate_building_ids": candidate_ids,
        "candidate_addresses": candidate_addresses,
    }


def build_clarification_question(reason: Optional[str], language: Optional[str] = None) -> str:
    reason = reason or "incomplete_question"
    language = response_language_for_message("", metadata={"response_language": language})
    prompts_en = {
        "missing_brf_name": "Which building do you mean? Please share the full street address so I use the correct building.",
        "missing_address": "To give building-specific advice safely, I need the full building address.",
        "ambiguous_brf": "I found more than one possible building. Could you provide the full street address so I use the correct building?",
        "brf_not_found": "I could not find building addresses for that BRF. Please share the full street address so I use the correct building.",
        "brf_lookup_failed": "I could not look up that BRF right now. Please share the full street address so I use the correct building.",
        "building_not_found_by_id": "I could not find enough building data for that building ID. Please share one of the building's street addresses so I can try the address lookup.",
        "ambiguous_address": "I found more than one possible building match for that address. Please provide the street address followed by the city or municipality, for example 'Ringvägen 10, Huddinge', or provide the exact building ID.",
        "building_not_found": "I could not find enough building data for that address. I can still give general guidance, or you can share another full street address if this one was misspelled.",
        "missing_building_data": "I found the building, but I do not have enough building data yet for personalized advice. Could you share any more details you have, such as the full address or the specific system you want to ask about?",
        "insufficient_data_for_personalized_advice": "I can give general guidance, but I need the full building address before I can personalize the advice.",
        "incomplete_question": "Could you clarify your request a bit more so I can answer safely?",
    }
    prompts_sv = {
        "missing_brf_name": "Vilken byggnad menar du? Dela den fullständiga gatuadressen så att jag använder rätt byggnad.",
        "missing_address": "För att ge byggnadsspecifika råd säkert behöver jag byggnadens fullständiga adress.",
        "ambiguous_brf": "Jag hittade fler än en möjlig byggnad. Kan du ange den fullständiga gatuadressen så att jag använder rätt byggnad?",
        "brf_not_found": "Jag kunde inte hitta byggnadsadresser för den BRF:en. Dela den fullständiga gatuadressen så att jag använder rätt byggnad.",
        "brf_lookup_failed": "Jag kunde inte slå upp den BRF:en just nu. Dela den fullständiga gatuadressen så att jag använder rätt byggnad.",
        "building_not_found_by_id": "Jag kunde inte hitta tillräckligt med byggnadsdata för det byggnadsid:t. Dela en av byggnadens gatuadresser så kan jag prova adressuppslagningen.",
        "ambiguous_address": "Jag hittade fler än en möjlig byggnadsträff för den adressen. Ange gatuadressen följt av stad eller kommun, till exempel 'Ringvägen 10, Huddinge', eller ange exakt byggnadsid.",
        "building_not_found": "Jag kunde inte hitta tillräckligt med byggnadsdata för den adressen. Jag kan ändå ge allmän vägledning, eller så kan du dela en annan fullständig gatuadress om den första var felstavad.",
        "missing_building_data": "Jag hittade byggnaden, men har inte tillräckligt med byggnadsdata för personlig rådgivning än. Kan du dela fler detaljer, till exempel full adress eller vilket system du vill fråga om?",
        "insufficient_data_for_personalized_advice": "Jag kan ge allmän vägledning, men behöver byggnadens fullständiga adress innan jag kan anpassa råden.",
        "incomplete_question": "Kan du förtydliga din fråga lite så att jag kan svara säkert?",
    }
    prompts = prompts_sv if language == "sv" else prompts_en
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
    confirmed = []
    for key in IMPORTANT_FACT_KEYS:
        if key == "district_heating_use":
            if any(
                _is_present(retrieved_facts.get(candidate))
                for candidate in (
                    "district_heating_use",
                    "district_heating_space_heating",
                    "district_heating_domestic_hot_water",
                )
            ):
                confirmed.append(key)
            continue
        if _is_present(retrieved_facts.get(key)):
            confirmed.append(key)
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


def _missing_fact_relevant_to_message(missing_fact: str, user_message: Optional[str]) -> bool:
    lowered = (user_message or "").lower()
    if missing_fact == "renovation_information":
        return bool(
            re.search(
                r"\b(?:renovation|renovated|retrofit|upgrade|insulation|window|windows|facade|fa[cç]ade|roof|envelope)\b",
                lowered,
            )
        )
    if missing_fact == "ventilation_type":
        return bool(re.search(r"\b(?:ventilation|ftx|airflow|fan|filters?)\b", lowered))
    if missing_fact == "heating_system":
        return bool(re.search(r"\b(?:heating|heat|district\s+heating|fj[aä]rrv[aä]rme)\b", lowered))
    if missing_fact == "electricity_use":
        return bool(re.search(r"\b(?:electricity|el|power|solar|pv)\b", lowered))
    return True


def apply_response_safety_notes(
    response_text: str,
    metadata: Dict[str, Any],
    user_message: Optional[str] = None,
) -> str:
    text = (response_text or "").strip()
    metadata = metadata or {}
    language = response_language_for_message(user_message, metadata=metadata)
    additions: List[str] = []

    freshness = metadata.get("data_freshness") or {}
    if freshness.get("requires_warning"):
        year = freshness.get("energy_declaration_year")
        if year:
            additions.append(
                choose_language_text(
                    language,
                    english=f"This answer is based on an energy declaration from {year}. If the building has changed since then, some details may be outdated.",
                    swedish=f"Det här svaret bygger på en energideklaration från {year}. Om byggnaden har ändrats sedan dess kan vissa uppgifter vara inaktuella.",
                )
            )
        else:
            additions.append(
                choose_language_text(
                    language,
                    english="Some building details may be outdated because I could not confirm how recent the underlying energy-declaration data is.",
                    swedish="Vissa byggnadsuppgifter kan vara inaktuella eftersom jag inte kunde bekräfta hur aktuell den underliggande energideklarationsdatan är.",
                )
            )

    uncertainty = metadata.get("uncertainty") or {}
    missing = [
        fact
        for fact in (uncertainty.get("missing_facts") or [])
        if _missing_fact_relevant_to_message(str(fact), user_message)
    ]
    if missing:
        additions.append(
            choose_language_text(
                language,
                english="I do not have confirmed data for: " + ", ".join(missing[:4]) + ".",
                swedish="Jag saknar bekräftade data för: " + ", ".join(missing[:4]) + ".",
            )
        )

    if not additions:
        return text

    note_block = choose_language_text(
        language,
        english="Note: " + " ".join(additions),
        swedish="Obs: " + " ".join(additions),
    )
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


def build_out_of_scope_response(
    out_of_scope_type: str,
    redirect_to: Optional[str],
    language: Optional[str] = None,
) -> str:
    language = response_language_for_message("", metadata={"response_language": language})
    explanations_en = {
        "legal_advice": "I can give general energy-efficiency information, but I should not give legal advice or make legal judgments for a building association.",
        "financial_advice": "I can discuss general energy measures, but I should not give financial advice or make investment decisions for a building association.",
        "detailed_engineering_calculation": "I can explain general options, but detailed engineering calculations should be handled by a qualified engineer or installer.",
        "installer_or_vendor_recommendation": "I can explain what to look for, but I should not recommend a specific installer or vendor.",
        "personal_data_or_privacy_issue": "I can explain general principles, but privacy and personal-data questions should be handled through the appropriate authority or responsible contact.",
        "external_contact_or_register_update": "I cannot update external registers, contact lists, board-member records, or property-management systems automatically.",
    }
    explanations_sv = {
        "legal_advice": "Jag kan ge allmän information om energieffektivisering, men jag bör inte ge juridisk rådgivning eller göra juridiska bedömningar för en bostadsrättsförening.",
        "financial_advice": "Jag kan diskutera allmänna energiåtgärder, men jag bör inte ge finansiell rådgivning eller fatta investeringsbeslut åt en bostadsrättsförening.",
        "detailed_engineering_calculation": "Jag kan förklara allmänna alternativ, men detaljerade tekniska beräkningar bör hanteras av en kvalificerad ingenjör eller installatör.",
        "installer_or_vendor_recommendation": "Jag kan förklara vad ni bör titta efter, men jag bör inte rekommendera en specifik installatör eller leverantör.",
        "personal_data_or_privacy_issue": "Jag kan förklara allmänna principer, men frågor om integritet och personuppgifter bör hanteras via rätt myndighet eller ansvarig kontakt.",
        "external_contact_or_register_update": "Jag kan inte uppdatera externa register, kontaktlistor, styrelseuppgifter eller fastighetsförvaltningssystem automatiskt.",
    }
    next_steps_en = {
        "relevant_authority_or_legal_expert": "Please check with a legal expert, your building association's advisor, or the relevant authority.",
        "advisor_or_financial_specialist": "Please discuss this with an advisor or financial specialist before making a decision.",
        "qualified_engineer_or_installer": "A qualified engineer or installer should assess the building before any final decision is made.",
        "advisor_or_procurement_process": "An advisor or a formal procurement process is the safer way to compare vendors.",
        "privacy_contact_or_relevant_authority": "Please contact the relevant privacy lead or authority for guidance.",
        "official_register_or_admin_system": "Update those details in the BRF's official register or admin system, property-manager contact list, website/contact page, and any relevant authority/register where the BRF maintains board information. I can help draft a checklist or neutral update message, but I cannot perform the update.",
    }
    next_steps_sv = {
        "relevant_authority_or_legal_expert": "Kontrollera detta med en juridisk expert, föreningens rådgivare eller relevant myndighet.",
        "advisor_or_financial_specialist": "Diskutera detta med en rådgivare eller finansiell specialist innan ni fattar beslut.",
        "qualified_engineer_or_installer": "En kvalificerad ingenjör eller installatör bör bedöma byggnaden innan ett slutligt beslut fattas.",
        "advisor_or_procurement_process": "En rådgivare eller en formell upphandlingsprocess är ett säkrare sätt att jämföra leverantörer.",
        "privacy_contact_or_relevant_authority": "Kontakta relevant integritetsansvarig eller myndighet för vägledning.",
        "official_register_or_admin_system": "Uppdatera uppgifterna i BRF:ens officiella register eller administrationssystem, förvaltarens kontaktlista, webbplats/kontaktsida och relevanta myndighetsregister där BRF:en håller styrelseinformation. Jag kan hjälpa till att skriva en checklista eller ett neutralt uppdateringsmeddelande, men jag kan inte utföra uppdateringen.",
    }
    explanations = explanations_sv if language == "sv" else explanations_en
    next_steps = next_steps_sv if language == "sv" else next_steps_en
    base = explanations.get(
        out_of_scope_type,
        choose_language_text(
            language,
            english="I can help with general energy-advice questions, but this request goes beyond the safe scope of the system.",
            swedish="Jag kan hjälpa till med allmänna energirådgivningsfrågor, men den här begäran ligger utanför systemets säkra område.",
        ),
    )
    follow_up = next_steps.get(
        redirect_to or "",
        choose_language_text(
            language,
            english="Please consult a relevant advisor or authority for a reliable answer.",
            swedish="Kontakta en relevant rådgivare eller myndighet för ett tillförlitligt svar.",
        ),
    )
    return f"{base} {follow_up}"


def _contains_any(text: str, phrases: Iterable[str]) -> bool:
    lowered = (text or "").lower()
    return any(phrase in lowered for phrase in phrases)


def _response_has_redirect(response_text: str, redirect_to: Optional[str]) -> bool:
    if not redirect_to:
        return _contains_any(response_text, ("advisor", "expert", "authority", "qualified", "installer"))
    phrases = REDIRECT_TARGET_PHRASES.get(redirect_to, ())
    return _contains_any(response_text, phrases)


def assess_boundary_safety(
    *,
    user_message: str,
    response_text: str,
    route: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    metadata = metadata or {}
    route_label = str(route or "").strip().lower()
    boundary = metadata.get("boundary_handling") or {}
    detected = detect_out_of_scope(user_message or "")

    out_of_scope_type = (
        boundary.get("out_of_scope_type")
        or metadata.get("out_of_scope_type")
        or (detected or {}).get("out_of_scope_type")
    )
    redirect_to = (
        boundary.get("redirect_to")
        or metadata.get("redirect_to")
        or (detected or {}).get("redirect_to")
    )
    detected_risk = bool(out_of_scope_type)
    routed_out_of_scope = route_label == "out_of_scope" or bool(boundary.get("out_of_scope")) or bool(metadata.get("out_of_scope"))
    response_has_refusal = _contains_any(response_text, BOUNDARY_REFUSAL_PHRASES)
    response_has_redirect = _response_has_redirect(response_text, redirect_to)

    if route_label == "expert_handoff":
        return {
            "status": "passed",
            "detected_risk": detected_risk,
            "risk_category": out_of_scope_type,
            "action": "expert_handoff",
            "handled_safely": True,
            "requires_review": False,
            "should_escalate": True,
            "escalation_target": redirect_to or "ekr_advisor",
            "redirect_to": redirect_to or "ekr_advisor",
            "response_has_refusal": response_has_refusal,
            "response_has_redirect": response_has_redirect,
            "reason_codes": [],
        }

    if detected_risk:
        reason_codes: List[str] = []
        if not routed_out_of_scope:
            reason_codes.append("route_not_out_of_scope")
        if not response_has_refusal:
            reason_codes.append("missing_refusal_or_scope_limit")
        if not response_has_redirect:
            reason_codes.append("missing_redirect_target")

        handled_safely = not reason_codes
        return {
            "status": "passed" if handled_safely else "needs_review",
            "detected_risk": True,
            "risk_category": out_of_scope_type,
            "action": "redirected" if routed_out_of_scope else "answered_directly",
            "handled_safely": handled_safely,
            "requires_review": not handled_safely,
            "should_escalate": True,
            "escalation_target": redirect_to,
            "redirect_to": redirect_to,
            "response_has_refusal": response_has_refusal,
            "response_has_redirect": response_has_redirect,
            "reason_codes": reason_codes,
        }

    if routed_out_of_scope:
        return {
            "status": "needs_review",
            "detected_risk": False,
            "risk_category": out_of_scope_type,
            "action": "over_refusal",
            "handled_safely": False,
            "requires_review": True,
            "should_escalate": False,
            "escalation_target": redirect_to,
            "redirect_to": redirect_to,
            "response_has_refusal": response_has_refusal,
            "response_has_redirect": response_has_redirect,
            "reason_codes": ["unexpected_boundary_route"],
        }

    return {
        "status": "not_applicable",
        "detected_risk": False,
        "risk_category": None,
        "action": "answered",
        "handled_safely": True,
        "requires_review": False,
        "should_escalate": False,
        "escalation_target": None,
        "redirect_to": None,
        "response_has_refusal": response_has_refusal,
        "response_has_redirect": False,
        "reason_codes": [],
    }
