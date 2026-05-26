# src/agents/building_flow_graph.py
from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple, Optional
import copy
import unicodedata

# --- LangGraph
from langgraph.graph import StateGraph, START, END

# --- Project deps
from src.agents.parse_intent_agent import ParseIntentAgent
from src.agents.generic_sql_layer import SQL_Mapper_Layer
from src.agents.building_response_prompt import (
    build_building_response_prompt,
    ensure_building_identifier_in_response,
    merge_identifier_metadata,
    select_preferred_identifier,
)
from src.database.vector_client import VectorClient, VectorClientConfig
from src.agents.openai_agent import OpenAIResponseAgent
from src.agents.specialized_sql_layer import SpecializedSQLLayer
from src.database.hammarby_data import query_address
from src.pipeline.safety_analysis import (
    apply_response_safety_notes,
    assess_building_identity,
    build_clarification_question,
    compute_data_freshness,
    compute_uncertainty,
    extract_retrieved_facts,
)
from typing import Annotated
import operator

# -----------------------------
# Define the Graph State Schema
# -----------------------------

parse_intent_agent = ParseIntentAgent()
sql_mapper_layer = SQL_Mapper_Layer()
try:
    specialized_layer = SpecializedSQLLayer()
    _SPECIALIZED_INIT_ERROR = None
    print("[init] SpecializedSQLLayer initialized ✓", flush=True)
except Exception as e:
    specialized_layer = None
    _SPECIALIZED_INIT_ERROR = f"SpecializedSQLLayer init failed: {e}"
    print(f"[init] {_SPECIALIZED_INIT_ERROR}", flush=True)

# Initialize VectorClient using its config
_vector_cfg = VectorClientConfig()
vector_database = VectorClient(_vector_cfg)
print("[init] VectorClient initialized ✓", flush=True)

# LLM summarizer (Azure OpenAI)
llm_summarizer = OpenAIResponseAgent()
print("[init] OpenAIResponseAgent initialized ✓", flush=True)

# Load list of addresses available in specialized SQL
_address_list_raw = query_address()  # may be list[str] or list[dict] with an address field
try:
    _addr_count = len(_address_list_raw or [])
except Exception:
    _addr_count = 0
print(f"[init] Address list loaded: {_addr_count} entries", flush=True)

# -----------------------------
# Compatibility helpers / const
# -----------------------------
CANONICAL_INTENTS = {
    "SQL database": "query generic database",
    "vector database": "query vector database",
    "Specialized SQL database": "query specific database",
    "specialised sql database": "query specific database",
    "specialized sql": "query specific database",
    "specialised sql": "query specific database",
    "specific database": "query specific database",
    "generic sql": "query generic database",
    "vector": "query vector database",
    "sql": "query generic database",
    "simulations": "simulations",
    "simulation": "simulations",
}

SINGLE_FILTER_KEYS = [
    "byggnadsid", "01a_fnr", "50a_uuid", "50a_deso",
    "epc_egennybyggar", "epc_egenbyggnadstyp", "epc_egenatemp",
    "epc_egenantalplan", "epc_egenantaltrapphus", "epc_idadr",
]

# ================
# Utility helpers
# ================
def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a or {})
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def _latest_session_state(session_state: Any) -> Dict[str, Any]:
    if isinstance(session_state, dict):
        return dict(session_state)
    if isinstance(session_state, list):
        for item in reversed(session_state):
            if isinstance(item, dict):
                return dict(item)
    return {}

def _normalize_address(addr: Optional[str]) -> Optional[str]:
    if not addr:
        return None
    a = re.sub(r"\s+", " ", str(addr)).strip()
    return a.lower()

def _ascii_fold(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def _coerce_intent_list(ctx: Dict[str, Any]) -> List[str]:
    intent_list = ctx.get("intent_list")
    if isinstance(intent_list, (list, tuple)):
        return [str(item).strip() for item in intent_list if str(item).strip()]

    intents = ctx.get("intents")
    if isinstance(intents, (list, tuple)):
        return [str(item).strip() for item in intents if str(item).strip()]

    parsed_intent = ctx.get("parsed_intent")
    if isinstance(parsed_intent, str) and parsed_intent.strip():
        return [part.strip() for part in re.split(r"\s*;\s*", parsed_intent) if part.strip()]

    return []


def _last_assistant_requested_address(messages: List[Dict[str, Any]]) -> bool:
    for message in reversed(messages or []):
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content") or "").lower()
        if (
            "address" in content
            and any(token in content for token in ("building", "street", "full", "provide", "share", "need"))
        ):
            return True
        return False
    return False


ADDRESS_INTRO_RE = re.compile(
    r"(?:i\s+live\s+(?:in|at|on)|i\s+am\s+at|i'm\s+at|my\s+address\s+is|address\s+is|"
    r"we\s+live\s+(?:in|at|on)|our\s+address\s+is|"
    r"jag\s+bor\s+p[åa]|vi\s+bor\s+p[åa]|min\s+adress\s+[äa]r|adressen\s+[äa]r)\s+(.+)$",
    flags=re.IGNORECASE,
)

BRF_NAME_RE = re.compile(
    r"\b(?:brf|bostadsr[äa]ttsf[öo]reningen|bostadsrattsforeningen)\s+"
    r"([A-Za-zÅÄÖåäö0-9][A-Za-zÅÄÖåäö0-9 .'\-]*?)"
    r"(?=\s*(?:[,.;:!?]|$|\bwhat\b|\bwhich\b|\bhow\b|\bwhy\b|\bvad\b|\bhur\b|\bvilken\b|\bvilket\b|\bmed\b|\bwith\b))",
    flags=re.IGNORECASE,
)

BUILDING_ID_RE = re.compile(
    r"\b\d{2}-\d{2}-[A-Za-zÅÄÖåäö0-9]+-\d+\b",
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


def _extract_address_candidate_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None

    candidate = str(text).strip()
    if not candidate:
        return None

    candidate = re.sub(
        r"^\s*(?:sure|yes|yeah|yep|ok|okay|of course|absolutely|noo?|nej|ja)\s*[,.:;-]*\s*",
        "",
        candidate,
        flags=re.IGNORECASE,
    )

    had_address_cue = False
    cue_match = ADDRESS_INTRO_RE.search(candidate)
    if cue_match:
        had_address_cue = True
        candidate = cue_match.group(1)

    candidate = re.sub(
        r"^(?:i live in|i live at|i live on|i'm at|i am at|my address is|address is|it's|it is)\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" .,:;")

    if not candidate:
        return None

    if _is_address_recognized(candidate):
        return candidate

    folded = _ascii_fold(candidate)
    if folded and _is_address_recognized(folded):
        return candidate

    if _looks_like_compact_address(candidate, allow_four_digit_number=had_address_cue):
        return candidate

    return None


def _clean_brf_name(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip(" .,:;!?\"'")
    cleaned = re.sub(
        r"^(?:brf|bostadsr[äa]ttsf[öo]reningen|bostadsrattsforeningen)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" .,:;!?\"'")
    return cleaned or None


def _extract_brf_name_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = BRF_NAME_RE.search(str(text))
    if not match:
        return None
    return _clean_brf_name(match.group(1))


def _extract_building_id_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = BUILDING_ID_RE.search(str(text))
    if not match:
        return None
    return match.group(0).upper()


def _lookup_brf_addresses(brf_name: str) -> List[Dict[str, Any]]:
    client = getattr(sql_mapper_layer, "sql", None)
    if client is None or not hasattr(client, "brf_addresses"):
        return []
    rows = client.brf_addresses(brf_name) or []
    return [row for row in rows if isinstance(row, dict)]


def _format_brf_address(row: Dict[str, Any]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    address = str(row.get("address") or "").strip()
    if not address:
        return None
    postnr = str(row.get("postnr") or "").strip()
    postort = str(row.get("postort") or "").strip()
    suffix = " ".join(part for part in (postnr, postort) if part)
    return f"{address}, {suffix}" if suffix else address


def _group_brf_address_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for row in rows or []:
        building_id = str(row.get("byggnadsid") or "").strip()
        if not building_id:
            continue
        if building_id not in grouped:
            grouped[building_id] = {
                "choice": str(len(order) + 1),
                "byggnadsid": building_id,
                "fastighet": row.get("fastighet"),
                "orgnr": row.get("orgnr"),
                "brf_name": row.get("brf_name"),
                "addresses": [],
            }
            order.append(building_id)
        label = _format_brf_address(row)
        if label and label not in grouped[building_id]["addresses"]:
            grouped[building_id]["addresses"].append(label)
    return [grouped[building_id] for building_id in order]


def _address_without_postcode(label: str) -> str:
    return str(label or "").split(",", 1)[0].strip()


def _summarize_brf_addresses(addresses: List[str], limit: int = 6) -> str:
    cleaned = [str(item).strip() for item in addresses or [] if str(item).strip()]
    if not cleaned:
        return "no address listed"
    shown = cleaned[:limit]
    summary = "; ".join(shown)
    remaining = len(cleaned) - len(shown)
    if remaining > 0:
        summary = f"{summary}; +{remaining} more"
    return summary


def _build_brf_selection_question(brf_name: str, options: List[Dict[str, Any]]) -> str:
    lines = [
        f"I found more than one building for BRF {brf_name}. Which building should I use?",
        "",
    ]
    for option in options or []:
        address_summary = _summarize_brf_addresses(option.get("addresses") or [])
        lines.append(f"{option.get('choice')}. {option.get('byggnadsid')} - {address_summary}")
    lines.extend(
        [
            "",
            "Reply with the number, the building ID, or one of the listed addresses.",
        ]
    )
    return "\n".join(lines)


def _normalize_selection_text(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return normalized.strip(" .,:;!?\"'")


def _select_brf_resolution_option(
    message: Optional[str],
    pending_resolution: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    text = _normalize_selection_text(message)
    if not text:
        return None

    options = pending_resolution.get("options") or []
    for option in options:
        if text == _normalize_selection_text(option.get("choice")):
            return dict(option)

    for option in options:
        building_id = _normalize_selection_text(option.get("byggnadsid"))
        if building_id and (text == building_id or building_id in text):
            return dict(option)

    for option in options:
        for address in option.get("addresses") or []:
            full = _normalize_selection_text(address)
            bare = _normalize_selection_text(_address_without_postcode(address))
            if (full and (text == full or full in text)) or (bare and (text == bare or bare in text)):
                selected = dict(option)
                selected["selected_address"] = _address_without_postcode(address)
                return selected

    return None


RESOLVED_BRF_STATUSES = {
    "resolved_by_user_selection",
    "resolved_unique_building",
}


def _has_resolved_brf_selection(metadata: Dict[str, Any]) -> bool:
    metadata = metadata or {}
    resolution = metadata.get("brf_resolution")
    status = resolution.get("status") if isinstance(resolution, dict) else None
    return bool(
        status in RESOLVED_BRF_STATUSES
        or metadata.get("selected_brf_building_id")
    )


def _iter_metadata_address_candidates(metadata: Dict[str, Any]) -> List[Any]:
    if not isinstance(metadata, dict):
        return []

    candidates: List[Any] = []
    for key in ("address", "address_from_user", "matched_address", "input_address", "official_address"):
        if metadata.get(key) not in (None, ""):
            candidates.append(metadata.get(key))

    for block_key in ("building_match", "retrieved_facts", "building_identity_check"):
        block = metadata.get(block_key)
        if not isinstance(block, dict):
            continue
        for key in ("address", "address_from_user", "matched_address", "input_address", "official_address"):
            if block.get(key) not in (None, ""):
                candidates.append(block.get(key))

    query_traces = metadata.get("query_traces")
    if isinstance(query_traces, dict):
        for trace in query_traces.values():
            if not isinstance(trace, dict):
                continue
            filters = trace.get("filters_used")
            if isinstance(filters, dict) and filters.get("address") not in (None, ""):
                candidates.append(filters.get("address"))
            returned = trace.get("returned_values_used")
            if isinstance(returned, dict):
                for key in ("address", "address_from_user", "matched_address", "epc_idadr"):
                    if returned.get(key) not in (None, ""):
                        candidates.append(returned.get(key))

    return candidates


def _extract_stored_address_from_metadata(metadata: Dict[str, Any]) -> Optional[str]:
    for candidate in _iter_metadata_address_candidates(metadata or {}):
        cleaned = _extract_address_candidate_from_text(str(candidate))
        if cleaned and _is_specific_address(cleaned):
            return cleaned
    return None


def _promote_stored_address(metadata: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    promoted = dict(metadata)

    for key in ("address", "address_from_user"):
        value = promoted.get(key)
        if value in (None, ""):
            continue
        cleaned = _extract_address_candidate_from_text(str(value))
        if cleaned and _is_specific_address(cleaned):
            promoted[key] = cleaned
        else:
            promoted.pop(key, None)

    stored_address = _extract_stored_address_from_metadata(promoted)
    if stored_address:
        promoted["address"] = stored_address
        promoted["address_from_user"] = stored_address
    return promoted


def _looks_like_personal_energy_advice(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return False

    has_personal_energy_subject = bool(
        re.search(
            r"\b(?:my|our)\s+(?:energy\s+efficiency|energy\s+use|energy\s+consumption|"
            r"heating\s+costs?|electricity\s+use|electricity\s+consumption)\b",
            lowered,
        )
    )
    has_improvement_verb = bool(
        re.search(r"\b(?:improve|reduce|lower|save|optimi[sz]e|increase|decrease|minska|förbättra)\b", lowered)
    )

    return has_personal_energy_subject or (
        has_improvement_verb
        and bool(re.search(r"\b(?:my|our)\b", lowered))
        and bool(re.search(r"\b(?:energy|heating|electricity|el|värme)\b", lowered))
    )


ECM_ADVICE_PATTERNS = (
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

EXPLICIT_GENERAL_SCOPE_PATTERNS = (
    r"\bin general\b",
    r"\bgenerally\b",
    r"\bbroadly\b",
    r"\bmore broadly\b",
    r"\bnot building[-\s]?specific\b",
    r"\bgeneral tips?\b",
)

BUILDING_CONTEXT_KEYS = (
    "address",
    "address_from_user",
    "matched_address",
    "input_address",
    "official_address",
    "byggnadsid",
    "building_id",
    "50a_uuid",
    "01a_fnr",
)

ADVICE_QUERY_FACT_LABELS = {
    "energy_class": "energy class",
    "energy_performance": "energy performance",
    "heating_system": "heating system",
    "ventilation_type": "ventilation type",
    "district_heating_use": "district heating use",
    "electricity_use": "electricity use",
    "domestic_hot_water": "domestic hot water",
}


def _looks_like_ecm_or_measure_advice(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return False
    if any(re.search(pattern, lowered) for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS):
        return False
    return any(re.search(pattern, lowered) for pattern in ECM_ADVICE_PATTERNS)


def _contains_building_context(value: Any) -> bool:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            lower_map = {str(key).lower(): key for key in current.keys()}
            for key in BUILDING_CONTEXT_KEYS:
                original = lower_map.get(key.lower())
                if original is not None and current.get(original) not in (None, "", [], {}):
                    return True
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return False


def _has_building_context_for_advice(metadata: Dict[str, Any], prior_state: Dict[str, Any]) -> bool:
    return bool(
        _contains_building_context(metadata)
        or _contains_building_context(prior_state)
        or select_preferred_identifier(metadata, prior_state)
        or _extract_stored_address_from_metadata(metadata)
        or _extract_stored_address_from_metadata((prior_state or {}).get("metadata") or {})
    )


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if isinstance(value, str):
            value = value.replace(",", ".")
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if not match:
                return None
            value = match.group(0)
        return float(value)
    except Exception:
        return None


def _build_effective_ecm_query(
    message: str,
    metadata: Dict[str, Any],
    prior_state: Dict[str, Any],
) -> str:
    facts = extract_retrieved_facts(
        metadata,
        prior_state,
        (metadata or {}).get("retrieved_facts"),
        (prior_state or {}).get("aggregated_data"),
        (prior_state or {}).get("agent_data"),
    )
    terms = [
        str(message or "").strip(),
        "energy conservation measures ECM relevant measures Swedish BRF flerbostadshus",
    ]

    for key, label in ADVICE_QUERY_FACT_LABELS.items():
        value = facts.get(key)
        if value not in (None, "", [], {}):
            terms.append(f"{label}: {value}")

    heating = str(facts.get("heating_system") or "").lower()
    if "district heating" in heating:
        terms.append("fjärrvärme undercentral heating curve controls balancing driftoptimering")

    energy_class = str(facts.get("energy_class") or "").strip().upper()
    energy_performance = _coerce_float(facts.get("energy_performance"))
    if energy_class in {"E", "F", "G"} or (energy_performance is not None and energy_performance >= 150):
        terms.append("high energy use poor energy class insulation windows ventilation heat recovery domestic hot water")

    terms.append("EnergiBRFhandboken brfenergieffektiv fjärrvärme undercentral isolering fönster tvättstuga")
    return " ".join(part for part in terms if part)


def _promote_to_building_advice_intent(ctx: Dict[str, Any], message: str) -> None:
    lowered = str(message or "").lower()
    sql_intent = (
        "Specialized SQL database"
        if re.search(r"\b(?:electricity|el|pv|solar|production|usage|consumption)\b", lowered)
        else "SQL database"
    )
    ctx["intent_list"] = [sql_intent, "vector database"]
    ctx["parsed_intent"] = f"{sql_intent} ; vector database"
    ctx["ambiguous"] = False
    ctx["ambigious"] = False

# --- replace your _safe_ctx_from_parsed with this ---
def _safe_ctx_from_parsed(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accept v1 (top-level) and v2 (context={...}) outputs from ParseIntentAgent,
    harmonize fields, prefer context values, and treat top-level keys case-insensitively.
    """
    ctx = {}
    # prefer parser-provided context if present
    if isinstance(parsed.get("context"), dict):
        ctx = dict(parsed["context"])  # copy

    # case-insensitive view of top-level keys
    lower_keys = { (k.lower() if isinstance(k, str) else k): v for k, v in parsed.items() }

    # Lift common keys if not already in ctx
    for k in (
        "parsed_intent", "intent", "address", "entities", "ambigious", "ambiguous",
        "normalized_address", "address_only_message", "use_previous_intent"
    ):
        if k in lower_keys and k not in ctx:
            ctx[k] = lower_keys[k]

    # Harmonize ambigious/ambiguous
    if "ambiguous" not in ctx and "ambigious" in ctx:
        ctx["ambiguous"] = bool(ctx.get("ambigious"))

    # keep the raw intent string (useful for fallback/debug)
    raw_intent = lower_keys.get("parsed_intent") or lower_keys.get("intent")
    if isinstance(raw_intent, (str, dict, list)):
        ctx["intent_raw"] = raw_intent

    return ctx


# --- Intents → agent-done flags (covers canonical + legacy strings) ---
INTENT_TO_FLAG = {
    # Canonical
    "sql database": "done_generic_sql",
    "specialized sql database": "done_specialized_sql",
    "vector database": "done_vector",
    # Legacy / back-compat (your router already accepts these)
    "query generic database": "done_generic_sql",
    "generic_sql": "done_generic_sql",
    "query specific database": "done_specialized_sql",
    "specialized_sql": "done_specialized_sql",
    "query vector database": "done_vector",
    "vector_search": "done_vector",
}

def _normalized_intents_from_context(state: "GraphState") -> list[str]:
    ctx = state.get("context", {}) or {}
    intent_list = ctx.get("intent_list")
    out: list[str] = []
    if isinstance(intent_list, (list, tuple)):
        out = [str(i).strip().lower() for i in intent_list if str(i).strip()]
    else:
        # Fallback to any single-string intent if list is missing
        single = ctx.get("parsed_intent") or ctx.get("intent") or ""
        if single:
            out = [str(single).strip().lower()]
    return out

def _required_flags_for_wait(state: "GraphState") -> list[str]:
    intents = _normalized_intents_from_context(state)
    # Keep order but dedupe while mapping → flags
    seen, required = set(), []
    for it in intents:
        flag = INTENT_TO_FLAG.get(it)
        if flag and flag not in seen:
            seen.add(flag)
            required.append(flag)
    return required


def _context_requests_vector(state: "GraphState") -> bool:
    intents = _normalized_intents_from_context(state)
    raw = str((state.get("context") or {}).get("parsed_intent") or "").lower()
    return any("vector" in intent for intent in intents) or "vector" in raw


def _is_address_recognized(addr: Optional[str]) -> bool:
    if not addr:
        return False
    norm = _normalize_address(addr)
    norm_fold = _ascii_fold(norm or "")
    items = _address_list_raw or []
    for item in items:
        if isinstance(item, str):
            si = _normalize_address(item)
            if si == norm or _ascii_fold(si or "") == norm_fold:
                return True
        elif isinstance(item, dict):
            for key in ("normalized_address", "address", "Address"):
                val = item.get(key)
                if not val:
                    continue
                sv = _normalize_address(val)
                if sv == norm or _ascii_fold(sv or "") == norm_fold:
                    return True
    return False

def _get_previous_meaningful_user_turn(messages: List[Dict[str, Any]]) -> Optional[str]:
    for m in reversed(messages):
        if m.get("role") == "user" and m.get("content"):
            return m.get("content")
    return None

def _describe_payload_for_aggregation(data: Any) -> str:
    if data is None:
        return "No data."
    if isinstance(data, dict):
        items = [f"{k}: {v}" for k, v in list(data.items())[:12]]
        return "; ".join(items)
    if isinstance(data, list):
        if not data:
            return "[]"
        if isinstance(data[0], dict):
            items = [f"{k}: {v}" for k, v in list(data[0].items())[:12]]
            return "Row 1 -> " + "; ".join(items)
        return f"List[{len(data)}]: " + ", ".join(map(str, data[:8]))
    return str(data)

def _compute_agent_answered(state: GraphState) -> str:
    types = set()
    if state.get("agent_data_generic") or state.get("done_generic_sql"):
        types.add("sql")
    if state.get("agent_data_specialized") or state.get("done_specialized_sql"):
        types.add("sql")
    if state.get("agent_vector_sources") or state.get("done_vector"):
        types.add("vector")
    if state.get("done_simulation"):
        types.add("simulation")
    if state.get("done_other"):
        types.add("other")
    if types == {"sql", "vector"}:
        return "sql+vector"
    if "sql" in types and "vector" not in types:
        return "sql"
    if "vector" in types and "sql" not in types:
        return "vector"
    if not types:
        return "unknown"
    return "+".join(sorted(types))

def _has_single_filter(md: Dict[str, Any]) -> bool:
    return any(md.get(k) not in (None, "") for k in SINGLE_FILTER_KEYS)


def _is_specific_address(addr: Optional[str]) -> bool:
    if not addr:
        return False
    text = str(addr).strip()
    return bool(re.search(r"\d", text) and re.search(r"[A-Za-zÅÄÖåäö]", text))


def _extract_row_address(row: Dict[str, Any]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    for key in ("address", "Address", "official_address", "epc_idadr"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _iter_nested_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_nested_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_nested_dicts(nested)


def _extract_first_address_for_keys(sources: Tuple[Any, ...], keys: Tuple[str, ...]) -> Optional[str]:
    for source in sources:
        for node in _iter_nested_dicts(source):
            lower_map = {str(key).lower(): key for key in node.keys()}
            for key in keys:
                original = lower_map.get(key.lower())
                if original is None:
                    continue
                value = node.get(original)
                if value not in (None, ""):
                    return str(value)
    return None


def _extract_row_identifier(row: Dict[str, Any]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    for key in ("byggnadsid", "building_id", "50a_uuid", "uuid", "oden_uuid", "01a_fnr"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _extract_row_value_case_insensitive(row: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    if not isinstance(row, dict):
        return None
    lower_map = {str(key).lower(): key for key in row.keys()}
    for key in keys:
        original = lower_map.get(key.lower())
        if original is not None and row.get(original) not in (None, ""):
            return row.get(original)
    return None


def _parse_epc_version_date(value: Any) -> Optional[Tuple[int, int, int]]:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return (value, 0, 0) if 1900 <= value <= 2199 else None
    if isinstance(value, float):
        as_int = int(value)
        return (as_int, 0, 0) if value.is_integer() and 1900 <= as_int <= 2199 else None

    text = str(value).strip()
    if not text:
        return None

    date_match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})[-/.]?(\d{2})[-/.]?(\d{2})\b", text)
    if date_match:
        year, month, day = (int(part) for part in date_match.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return (year, month, day)

    year_match = re.search(r"\b(19\d{2}|20\d{2}|21\d{2})\b", text)
    if year_match:
        return (int(year_match.group(1)), 0, 0)

    return None


def _epc_version_sort_key(row: Dict[str, Any]) -> Optional[Tuple[int, int, int]]:
    version_value = _extract_row_value_case_insensitive(
        row,
        (
            "epc_egiversion",
            "epc_egiversion_calc",
            "energy_declaration_year",
            "epc_godkand",
        ),
    )
    return _parse_epc_version_date(version_value)


def _select_latest_epc_rows(rows: Any) -> Any:
    if not isinstance(rows, list) or len(rows) <= 1:
        return rows

    keyed_rows = [
        (row, _epc_version_sort_key(row))
        for row in rows
        if isinstance(row, dict)
    ]
    available_keys = [key for _, key in keyed_rows if key is not None]
    if not available_keys:
        return rows

    latest_key = max(available_keys)
    latest_rows = [
        row
        for row, key in keyed_rows
        if key == latest_key
    ]
    return latest_rows or rows


def _enrich_building_fact_aliases(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return row
    enriched = dict(row)
    facts = extract_retrieved_facts(row)
    for key in ("heating_system", "district_heating_use", "energy_class", "energy_performance"):
        if key not in enriched and facts.get(key) not in (None, "", [], {}):
            enriched[key] = facts[key]
    return enriched


def _enrich_building_data(data: Any) -> Any:
    if isinstance(data, list):
        return [_enrich_building_fact_aliases(row) if isinstance(row, dict) else row for row in data]
    if isinstance(data, dict):
        return _enrich_building_fact_aliases(data)
    return data


def _dedupe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for row in rows or []:
        identifier = _extract_row_identifier(row)
        normalized_address = _normalize_address(_extract_row_address(row))
        version_key = _epc_version_sort_key(row)
        version_value = _extract_row_value_case_insensitive(
            row,
            (
                "epc_egiversion",
                "epc_egiversion_calc",
                "energy_declaration_year",
                "epc_godkand",
            ),
        )
        fingerprint = (
            identifier or "",
            normalized_address or "",
            version_key or version_value or "",
            tuple(sorted(row.keys())),
        )
        if fingerprint in seen:
            continue
        deduped.append(row)
        seen.add(fingerprint)
    return deduped


def _narrow_address_matches(address: Optional[str], rows: Any) -> Any:
    if not address or not isinstance(rows, list):
        return rows

    normalized_input = _normalize_address(address)
    if not normalized_input:
        return rows

    deduped_rows = _dedupe_rows(rows)
    exact_matches = [
        row
        for row in deduped_rows
        if _normalize_address(_extract_row_address(row)) == normalized_input
    ]
    return _select_latest_epc_rows(exact_matches or deduped_rows)


def _building_id_matches(row: Dict[str, Any], building_id: Optional[str]) -> bool:
    if not building_id or not isinstance(row, dict):
        return False
    row_id = _extract_row_value_case_insensitive(row, ("byggnadsid", "building_id"))
    return str(row_id or "").strip().upper() == str(building_id).strip().upper()


def _lookup_rows_for_brf_selected_addresses(building_id: Optional[str], metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not building_id:
        return None

    candidates: List[str] = []
    lookup_address = metadata.get("selected_brf_lookup_address")
    if lookup_address not in (None, ""):
        candidates.append(str(lookup_address))
    for address in metadata.get("selected_brf_addresses") or []:
        bare = _address_without_postcode(address)
        if bare and bare not in candidates:
            candidates.append(bare)

    for address in candidates:
        result = sql_mapper_layer.execute("building_by_address", address=address)
        if not (result.get("ok") and isinstance(result.get("data"), list) and result.get("data")):
            continue
        narrowed = _narrow_address_matches(address, result.get("data"))
        matched = [
            row
            for row in (narrowed if isinstance(narrowed, list) else [])
            if _building_id_matches(row, building_id)
        ]
        if not matched and isinstance(narrowed, list):
            matched = narrowed
        if matched:
            trace = result.get("trace") or {}
            trace["fallback_from_building_id"] = building_id
            trace["fallback_address"] = address
            trace["match_strategy"] = "brf_selected_address_fallback"
            trace["rows_returned"] = len(matched)
            result["data"] = matched
            result["trace"] = trace
            return result

    return None

# =================
# Canonicalization
# =================
def _canonicalize_intent_tokens(parsed_intent) -> Tuple[str, List[str]]:
    tokens: List[str] = []

    # Handle dict-style outputs (v2 variants)
    if isinstance(parsed_intent, dict):
        candidates = []
        for k in ("primary", "top", "label", "intent"):
            if parsed_intent.get(k):
                candidates.append(str(parsed_intent[k]))
        for k in ("intents", "labels", "all", "list"):
            if isinstance(parsed_intent.get(k), (list, tuple)):
                candidates.extend(map(str, parsed_intent[k]))
        tokens = [t.lower().strip() for t in candidates if str(t).strip()]

    elif isinstance(parsed_intent, str):
        parts = re.split(r"[;,]|\band\b", parsed_intent.lower())
        tokens = [t.strip() for t in parts if t and t.strip()]

    elif isinstance(parsed_intent, (list, tuple)):
        tokens = [str(t).lower().strip() for t in parsed_intent if str(t).strip()]

    mapped: List[str] = []
    for t in tokens:
        canon = CANONICAL_INTENTS.get(t)
        if not canon:
            for k, v in CANONICAL_INTENTS.items():
                if k in t:  # substring match
                    canon = v
                    break
        if canon and canon not in mapped:
            mapped.append(canon)

    # Heuristic fallback if nothing mapped but we have a raw string like "sql database"
    if not mapped and isinstance(parsed_intent, str):
        raw = parsed_intent.lower().strip()
        if "sql" in raw:
            mapped = ["query generic database"]
        elif "vector" in raw:
            mapped = ["query vector database"]
        elif "sim" in raw:
            mapped = ["simulations"]

    if not mapped:
        return ("", [])
    priority = ["query specific database", "query generic database", "query vector database", "simulations"]
    mapped_sorted = sorted(mapped, key=lambda m: priority.index(m) if m in priority else 999)
    return mapped_sorted[0], mapped_sorted

# ================
# Graph state type
# ================
GraphState = Annotated[Dict[str, Any], operator.or_]

# ================================
# Context understanding (UPDATED)
# ================================

def understand_context_node(state: GraphState) -> GraphState:
    """
    Updated behavior:
    - Parse intent and adopt parser's context verbatim (no intent normalization/canonicalization).
    - Ensure both 'parsed_intent' and 'intent_list' keys exist in context (no transformations).
    - If an address is present in parsed context, update metadata with 'address' and 'address_from_user'.
    - Preserve address-only follow-up behavior for 'effective_query'.
    - Update the passed-in state in place and return it.
    """
    print("[understand_context] ENTER", flush=True)

    prior_state = _latest_session_state(state.get("session_state"))
    prior_metadata = prior_state.get("metadata") if isinstance(prior_state, dict) else {}
    prior_context = prior_state.get("context") if isinstance(prior_state, dict) else {}
    incoming_metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    merged_metadata = dict(prior_metadata or {})
    for key, value in (incoming_metadata or {}).items():
        if value not in (None, "", [], {}):
            merged_metadata[key] = value
    merged_metadata = _promote_stored_address(merged_metadata)
    if merged_metadata:
        state["metadata"] = merge_identifier_metadata(merged_metadata, prior_state)

    explicit_building_id = _extract_building_id_from_text(state.get("last_message"))
    if explicit_building_id:
        md = dict(state.get("metadata") or {})
        md["byggnadsid"] = explicit_building_id
        md["building_id_from_user"] = explicit_building_id
        state["metadata"] = md
        print(f"[understand_context] detected building id from text: {explicit_building_id!r}", flush=True)

    # Parse on a SAFE subset to avoid in-place mutation of the live state
    parse_input = {
        "last_message": state.get("last_message"),
        "messages": state.get("messages", []),
    }
    parsed = parse_intent_agent(copy.deepcopy(parse_input))  # defensive copy
    print(f"[understand_context] parsed keys={list(parsed.keys())}", flush=True)

    # Adopt parser context verbatim (no _safe_ctx_from_parsed, no normalization, no canonicalization)
    ctx = copy.deepcopy(parsed.get("context") or {})
    print(f"[understand_context] ctx keys before ensure={list(ctx.keys())}", flush=True)

    # Normalize parser output shape so downstream nodes always see the same keys.
    intent_list = _coerce_intent_list(ctx)
    ctx["intent_list"] = intent_list
    if "parsed_intent" not in ctx or ctx.get("parsed_intent") in (None, ""):
        ctx["parsed_intent"] = " ; ".join(intent_list) if intent_list else None

    if _looks_like_personal_energy_advice(state.get("last_message")):
        normalized_intents = [str(item).strip().lower() for item in (ctx.get("intent_list") or [])]
        if not normalized_intents or normalized_intents == ["vector database"]:
            ctx["intent_list"] = ["SQL database", "vector database"]
            ctx["parsed_intent"] = "SQL database ; vector database"
            ctx["ambiguous"] = False
            ctx["ambigious"] = False
            print("[understand_context] promoted personal energy advice to SQL + vector intent", flush=True)

    if _looks_like_ecm_or_measure_advice(state.get("last_message")):
        _promote_to_building_advice_intent(ctx, state.get("last_message") or "")
        ctx["effective_query"] = _build_effective_ecm_query(
            state.get("last_message") or "",
            state.get("metadata") or {},
            prior_state,
        )
        if _has_building_context_for_advice(state.get("metadata") or {}, prior_state):
            print("[understand_context] promoted ECM advice follow-up to SQL + vector intent", flush=True)
        else:
            print("[understand_context] ECM advice needs building context; SQL gate will request address", flush=True)

    # If the parser missed an address on an address-only follow-up, recover it heuristically.
    if ctx.get("address"):
        cleaned_address = _extract_address_candidate_from_text(ctx.get("address"))
        if cleaned_address:
            if cleaned_address != ctx.get("address"):
                ctx["address"] = cleaned_address
                print(f"[understand_context] cleaned parsed address to: {cleaned_address!r}", flush=True)
        else:
            print(f"[understand_context] discarded non-address parser value: {ctx.get('address')!r}", flush=True)
            ctx.pop("address", None)

    if not ctx.get("address"):
        recovered_address = _extract_address_candidate_from_text(state.get("last_message"))
        if recovered_address:
            ctx["address"] = recovered_address
            print(f"[understand_context] recovered address from raw message: {recovered_address!r}", flush=True)

    # Preserve intent for address-only followups by using the latest stored building context.
    if not ctx.get("parsed_intent") and ctx.get("address"):
        prior_intent_list = _coerce_intent_list(prior_context or {})
        prior_parsed_intent = (prior_context or {}).get("parsed_intent")
        if prior_intent_list:
            ctx["intent_list"] = copy.deepcopy(prior_intent_list)
        if prior_parsed_intent:
            ctx["parsed_intent"] = prior_parsed_intent
        elif ctx.get("intent_list"):
            ctx["parsed_intent"] = " ; ".join(ctx["intent_list"])
        print(
            f"[understand_context] restored prior intent parsed={ctx.get('parsed_intent')!r} intent_list={ctx.get('intent_list')}",
            flush=True,
        )

    # Keep any previously stored address available across turns even when the parser omits it.
    stored_address = (
        _extract_stored_address_from_metadata(state.get("metadata") or {})
        or _extract_stored_address_from_metadata(prior_metadata or {})
    )

    # Metadata: if address present in parsed input, set both fields
    print(ctx)
    addr = ctx.get("address") or stored_address
    if addr:
        md = state.get("metadata") or {}
        # Shallow copy to avoid mutating any external references
        md = dict(md)
        md["address"] = addr
        md["address_from_user"] = addr
        state["metadata"] = md  # write back only when modified
        ctx["address"] = addr
        print(f"[understand_context] address set in metadata: {addr!r}", flush=True)

    # Write context back to state (in place) and return state
    state["context"] = ctx
    print(f"[understand_context] parsed_intent={ctx.get('parsed_intent')!r} intent_list={ctx.get('intent_list')}", flush=True)
    print("[understand_context] EXIT", flush=True)
    return state


def brf_resolution_node(state: GraphState) -> GraphState:
    print("[brf_resolution] ENTER", flush=True)
    md = dict(state.get("metadata") or {})
    ctx = dict(state.get("context") or {})
    last_message = state.get("last_message") or ""
    pending = md.get("pending_brf_resolution")

    if isinstance(pending, dict):
        if _has_resolved_brf_selection(md):
            md.pop("pending_brf_resolution", None)
            print("[brf_resolution] dropped stale pending resolution after building selection", flush=True)
            return {"metadata": md}

        selected = _select_brf_resolution_option(last_message, pending)
        if selected:
            original_question = pending.get("original_question") or last_message
            original_context = pending.get("original_context") if isinstance(pending.get("original_context"), dict) else ctx
            selected_address = selected.get("selected_address")
            fallback_address = selected_address
            if not fallback_address and selected.get("addresses"):
                fallback_address = _address_without_postcode(selected["addresses"][0])
            md.pop("pending_brf_resolution", None)
            md.update(
                {
                    "brf_name": pending.get("brf_name"),
                    "byggnadsid": selected.get("byggnadsid"),
                    "selected_brf_building_id": selected.get("byggnadsid"),
                    "selected_brf_addresses": selected.get("addresses") or [],
                    "selected_brf_lookup_address": fallback_address,
                    "brf_candidate_buildings": pending.get("options") or [],
                    "brf_resolution": {
                        "status": "resolved_by_user_selection",
                        "brf_name": pending.get("brf_name"),
                        "selected_building_id": selected.get("byggnadsid"),
                        "selected_addresses": selected.get("addresses") or [],
                    },
                    "clarification": {
                        "needed": False,
                        "reason": None,
                        "question_asked": None,
                        "resolved": True,
                        "resolved_after_turns": 1,
                    },
                }
            )
            if selected_address:
                md["address"] = selected_address
                md["address_from_user"] = selected_address
                md["requested_address"] = selected_address
            ctx = dict(original_context)
            intent_list = _coerce_intent_list(ctx)
            if intent_list:
                ctx["intent_list"] = intent_list
                ctx.setdefault("parsed_intent", " ; ".join(intent_list))
            ctx["ambiguous"] = False
            ctx["ambigious"] = False
            print(
                f"[brf_resolution] selected byggnadsid={selected.get('byggnadsid')!r} for original question",
                flush=True,
            )
            return {
                "metadata": md,
                "context": ctx,
                "last_message": original_question,
            }

        question = pending.get("question") or _build_brf_selection_question(
            pending.get("brf_name") or "the BRF",
            pending.get("options") or [],
        )
        md["clarification"] = {
            "needed": True,
            "reason": "ambiguous_brf",
            "question_asked": question,
            "resolved": False,
            "resolved_after_turns": None,
        }
        print("[brf_resolution] pending selection not resolved", flush=True)
        return {"metadata": md}

    brf_name = _extract_brf_name_from_text(last_message)
    if not brf_name:
        print("[brf_resolution] no BRF name found", flush=True)
        return {}

    try:
        rows = _lookup_brf_addresses(brf_name)
    except Exception as exc:
        question = (
            f"I could not look up BRF {brf_name} right now. "
            "Please share the full street address so I can use the correct building."
        )
        md = _deep_merge(
            md,
            {
                "brf_name": brf_name,
                "brf_resolution": {
                    "status": "lookup_error",
                    "brf_name": brf_name,
                    "error": str(exc),
                },
                "clarification": {
                    "needed": True,
                    "reason": "brf_lookup_failed",
                    "question_asked": question,
                    "resolved": False,
                    "resolved_after_turns": None,
                },
            },
        )
        print(f"[brf_resolution] lookup error: {exc}", flush=True)
        return {"metadata": md}

    options = _group_brf_address_rows(rows)
    if not options:
        question = (
            f"I could not find building addresses for BRF {brf_name}. "
            "Please share the full street address so I can use the correct building."
        )
        md = _deep_merge(
            md,
            {
                "brf_name": brf_name,
                "brf_resolution": {
                    "status": "not_found",
                    "brf_name": brf_name,
                },
                "clarification": {
                    "needed": True,
                    "reason": "brf_not_found",
                    "question_asked": question,
                    "resolved": False,
                    "resolved_after_turns": None,
                },
            },
        )
        print("[brf_resolution] no BRF address rows", flush=True)
        return {"metadata": md}

    if len(options) == 1:
        option = options[0]
        md = _deep_merge(
            md,
            {
                "brf_name": brf_name,
                "byggnadsid": option.get("byggnadsid"),
                "selected_brf_building_id": option.get("byggnadsid"),
                "selected_brf_addresses": option.get("addresses") or [],
                "selected_brf_lookup_address": (
                    _address_without_postcode(option["addresses"][0])
                    if option.get("addresses")
                    else None
                ),
                "brf_candidate_buildings": options,
                "brf_resolution": {
                    "status": "resolved_unique_building",
                    "brf_name": brf_name,
                    "selected_building_id": option.get("byggnadsid"),
                    "selected_addresses": option.get("addresses") or [],
                },
                "clarification": {
                    "needed": False,
                    "reason": None,
                    "question_asked": None,
                    "resolved": True,
                    "resolved_after_turns": 0,
                },
            },
        )
        if len(option.get("addresses") or []) == 1:
            selected_address = _address_without_postcode(option["addresses"][0])
            md["address"] = selected_address
            md["address_from_user"] = selected_address
            md["requested_address"] = selected_address
        print(f"[brf_resolution] unique byggnadsid={option.get('byggnadsid')!r}", flush=True)
        return {"metadata": md}

    question = _build_brf_selection_question(brf_name, options)
    md = _deep_merge(
        md,
        {
            "brf_name": brf_name,
            "brf_candidate_buildings": options,
            "pending_brf_resolution": {
                "brf_name": brf_name,
                "original_question": last_message,
                "original_context": ctx,
                "options": options,
                "question": question,
            },
            "brf_resolution": {
                "status": "needs_user_selection",
                "brf_name": brf_name,
                "candidate_count": len(options),
            },
            "clarification": {
                "needed": True,
                "reason": "ambiguous_brf",
                "question_asked": question,
                "resolved": False,
                "resolved_after_turns": None,
            },
        },
    )
    print(f"[brf_resolution] needs selection options={len(options)}", flush=True)
    return {"metadata": md}


def route_after_brf_resolution(state: GraphState) -> str:
    clarification = ((state.get("metadata") or {}).get("clarification") or {})
    if clarification.get("needed") and clarification.get("reason") in {
        "ambiguous_brf",
        "brf_not_found",
        "brf_lookup_failed",
    }:
        print("[route_after_brf_resolution] → clarification", flush=True)
        return "clarification"
    return route_after_ambiguity(state)

# =====================================
# Early ambiguity + address gate router
# =====================================
def route_after_ambiguity(state: GraphState) -> str:
    ctx = state.get("context", {}) or {}
    md = state.get("metadata", {}) or {}

    intents_raw = ctx.get("intent_list") or []
    intents = [str(i).lower() for i in intents_raw]
    addr_present = bool(md.get("address"))
    single_filter = _has_single_filter(md)

    print(f"[route_after_ambiguity] ENTER intents={intents} addr={addr_present} single_filter={single_filter} ambiguous={ctx.get('ambiguous') or ctx.get('ambigious')}", flush=True)

    # Ambiguity gate (support both 'ambiguous' and misspelled 'ambigious')
    if ctx.get("ambiguous") or ctx.get("ambigious"):
        if not (md.get("address") or _has_single_filter(md)):
            print("[route_after_ambiguity] → clarification (ambiguous + no address/filter)", flush=True)
            return "clarification"
        print("[route_after_ambiguity] → maintain_history (ambiguous but address/filter present)", flush=True)
        return "maintain_history"

    # Vector-only bypass: all intents are 'vector database'
    if intents and all(i in {"vector database"} for i in intents):
        print("[route_after_ambiguity] → maintain_history (vector-only)", flush=True)
        return "maintain_history"

    # Determine if SQL is requested (needs address)
    wants_sql = any(i in {"sql database", "specialized sql database"} for i in intents)

    # Fallback to parsed_intent string if intent_list is empty
    if not intents:
        raw_pi = str(ctx.get("parsed_intent") or "").lower()
        if ("sql database" in raw_pi) or ("specialized sql database" in raw_pi) or ("sql" in raw_pi):
            wants_sql = True
        elif "vector database" in raw_pi and "sql" not in raw_pi:
            print("[route_after_ambiguity] → maintain_history (parsed says vector-only)", flush=True)
            return "maintain_history"

    # If intent is SQL but we lack address and single-filter, ask for address now
    if wants_sql and not (md.get("address") or _has_single_filter(md)):
        print("[route_after_ambiguity] → request_address (sql but no address/filter)", flush=True)
        return "request_address"

    # If we have address or single-filter, proceed
    if md.get("address") or _has_single_filter(md):
        print("[route_after_ambiguity] → maintain_history (ready)", flush=True)
        return "maintain_history"

    # No address anywhere and not vector-only -> ask now
    print("[route_after_ambiguity] → request_address (default)", flush=True)
    return "request_address"


# =====================
# Maintain history node
# =====================
def maintain_history_node(state: GraphState) -> GraphState:
    messages = state.get("messages", [])
    last = (state.get("last_message") or "").strip()
    same = (messages[-1].get("content") or "").strip() == last if messages and last else False
    print(f"[maintain_history] ENTER last_matches={same}", flush=True)
    print("[maintain_history] EXIT", flush=True)


# ==================================
# Fan-out vs single-branch router
# ==================================
# --- strengthen the early heuristic in decision_router ---
def decision_router(state: GraphState) -> str:
    ctx = state.get("context", {}) or {}
    md = state.get("metadata", {}) or {}

    # 1) Pull intents (prefer list)
    intents_raw = ctx.get("intent_list") or []
    if not isinstance(intents_raw, (list, tuple)):
        single = intents_raw or ctx.get("parsed_intent") or ctx.get("intent") or ""
        intents_raw = [single] if single else []

    # 2) Normalize
    intents_norm = [str(i).strip().lower() for i in intents_raw]

    # 2b) Fallback to single parsed/intent if list empty
    if not intents_norm:
        pi = str(ctx.get("parsed_intent") or ctx.get("intent") or "").strip().lower()
        if pi:
            intents_norm = [pi]

    print(f"[decision_router] ENTER intents_norm={intents_norm}", flush=True)

    # 3) Canonical/legacy mapping
    intent_to_agent = {
        # Canonical
        "sql database": "generic_sql_agent",
        "specialized sql database": "specialized_sql_agent",
        "vector database": "vector_db_agent",
        # Back-compat / legacy strings
        "query generic database": "generic_sql_agent",
        "generic_sql": "generic_sql_agent",
        "query vector database": "vector_db_agent",
        "vector_search": "vector_db_agent",
        "query specific database": "specialized_sql_agent",
        "specialized_sql": "specialized_sql_agent",
    }

    # 4) Map intents to agents (dedupe, preserve order)
    next_nodes = []
    seen = set()
    for it in intents_norm:
        agent = intent_to_agent.get(it)

        # Loose matching safety net
        if not agent:
            s = it
            if "vector" in s:
                agent = "vector_db_agent"
            elif "specialized" in s or "specific" in s:
                agent = "specialized_sql_agent"
            else:
                import re
                if re.search(r"\bsql\b", s):
                    agent = "generic_sql_agent"

        if agent and agent not in seen:
            seen.add(agent)
            next_nodes.append(agent)

    # 5) Last-resort fallback
    if not next_nodes:
        if md.get("address") or _has_single_filter(md):
            print("[decision_router] FALLBACK → ['generic_sql_agent'] (address/filter present)", flush=True)
            return ["generic_sql_agent"]
        print("[decision_router] FALLBACK → ['other_agent']", flush=True)
        return ["other_agent"]

    print(f"[decision_router] EXIT → {next_nodes}", flush=True)
    return next_nodes

# =======================================
# Fanout orchestration (parallel helpers)
# =======================================
def route_fanout_or_decide(state: GraphState) -> str:
    ctx = state.get("context", {}) or {}
    intents = set([(ctx.get("intent") or "").lower(), *[i.lower() for i in (ctx.get("intent_list") or [])]])
    intents.discard("")

    wants_sql = any(i in {"query generic database", "query specific database"} for i in intents)
    wants_vector = "query vector database" in intents
    route = "fanout_sql_vector" if (wants_sql and wants_vector) else "decision"
    print(f"[route_fanout_or_decide] intents={intents} → {route}", flush=True)
    return route


# def fanout_sql_vector_node(state: GraphState) -> GraphState:
#     md = state.get("metadata", {}) or {}
#     addr = md.get("address")
#     recognized = _is_address_recognized(addr)

#     required = {
#         "generic_sql": True,
#         "specialized_sql": bool(addr and recognized),
#         "vector": True,
#     }

#     print(f"[fanout_sql_vector] addr={bool(addr)} recognized={recognized} required={required}", flush=True)

#     # Signal parallel execution plan; agents will read this and act accordingly
#     return {"parallel": {"active": True, "required": required}}


def join_sql_vector_node(state: GraphState) -> GraphState:
    print("[join_sql_vector] ENTER (no-op)", flush=True)
    return {}


def route_from_join(state: GraphState) -> str:
    par = state.get("parallel") or {}
    req = (par.get("required") or {})

    all_done = True
    if req.get("generic_sql", False):
        all_done = all_done and bool(state.get("done_generic_sql"))
    if req.get("specialized_sql", False):
        all_done = all_done and bool(state.get("done_specialized_sql"))
    if req.get("vector", False):
        all_done = all_done and bool(state.get("done_vector"))

    next_hop = "aggregator" if all_done else "await_more"
    print(f"[route_from_join] required={req} flags: "
          f"gen={state.get('done_generic_sql')} spec={state.get('done_specialized_sql')} vec={state.get('done_vector')} "
          f"→ {next_hop}", flush=True)
    return next_hop

def route_after_wait(state: "GraphState") -> str:
    """
    Router for `wait_for_replies`: if all required flags are present, proceed to aggregator;
    otherwise branch to await_more (END) and try again next turn.
    """
    if state.get("identity_gate_blocked"):
        print("[route_after_wait] identity gate blocked → clarification", flush=True)
        return "clarification"
    required = state.get("waiting_required_flags") or _required_flags_for_wait(state)
    all_done = all(bool(state.get(flag)) for flag in required) if required else True
    next_hop = "aggregator" if all_done else "await_more"
    print(f"[route_after_wait] required={required} all_done={all_done} → {next_hop}", flush=True)
    return next_hop

def wait_for_replies_node(state: "GraphState") -> "GraphState":
    """
    Barrier node that 'waits' for all agent replies corresponding to the user's intent_list.
    It does not decide routing; it only computes what's still missing (for debugging/UX).
    """
    required = _required_flags_for_wait(state)
    missing = [f for f in required if not state.get(f)]
    print(f"[wait_for_replies] required={required} missing={missing}", flush=True)
    return {
        "waiting_required_flags": required,
        "waiting_missing_flags": missing,
        "ready_to_aggregate": len(missing) == 0,
    }

def await_more_node(state: "GraphState") -> "GraphState":
    missing = state.get("waiting_missing_flags")
    label = {
        "done_generic_sql": "generic SQL",
        "done_specialized_sql": "specialized SQL",
        "done_vector": "vector search",
    }
    if isinstance(missing, list) and missing:
        human = ", ".join(label.get(m, m) for m in missing)
        print(f"[await_more] waiting for: {human}", flush=True)
        return {"final_response": f"Awaiting: {human}"}
    print("[await_more] nothing missing (unexpected path)", flush=True)
    return {"final_response": "Awaiting required agent replies..."}

# ===========================================
# Agent nodes (updated for parallel semantics)
# ===========================================
def generic_sql_agent_node(state: GraphState) -> GraphState:
    par = state.get("parallel") or {}
    if par.get("active") and not par.get("required", {}).get("generic_sql", False):
        print("[generic_sql_agent] skipped by parallel plan", flush=True)
        return {"done_generic_sql": True}

    md = state.get("metadata", {}) or {}
    addr = md.get("address")
    print(f"[generic_sql_agent] ENTER addr={addr!r}", flush=True)
    result = None

    if addr:
        result = sql_mapper_layer.execute("building_by_address", address=addr)
        print(f"[generic_sql_agent] building_by_address ok={bool(result and result.get('ok'))}", flush=True)
        if not (result.get("ok") and result.get("data")):
            folded = _ascii_fold(addr)
            if folded and folded != addr:
                print(f"[generic_sql_agent] retry ASCII-folded address={folded!r}", flush=True)
                res2 = sql_mapper_layer.execute("building_by_address", address=folded)
                if res2.get("ok") and res2.get("data"):
                    result = res2
        if result.get("ok") and isinstance(result.get("data"), list):
            original_rows = result.get("data") or []
            narrowed_rows = _narrow_address_matches(addr, original_rows)
            result["data"] = narrowed_rows
            trace = result.get("trace") or {}
            trace["rows_returned"] = len(narrowed_rows or [])
            trace["match_strategy"] = (
                "exact_address"
                if narrowed_rows and len(narrowed_rows) < len(original_rows)
                else "address_lookup"
            )
            result["trace"] = trace
    else:
        key = next((k for k in SINGLE_FILTER_KEYS if md.get(k) not in (None, "")), None)
        if key is not None:
            value = md[key]
            print(f"[generic_sql_agent] single_filter key={key} value={value!r}", flush=True)
            if key == "byggnadsid":
                result = sql_mapper_layer.execute("buildings_by_building_id", building_id=value)
                if not (result.get("ok") and result.get("data")):
                    fallback = _lookup_rows_for_brf_selected_addresses(value, md)
                    if fallback:
                        print("[generic_sql_agent] used BRF selected-address fallback for byggnadsid", flush=True)
                        result = fallback
            else:
                result = sql_mapper_layer.execute("buildings_by_single_filter", field=key, value=value)

    if result and result.get("ok"):
        if isinstance(result.get("data"), list):
            latest_rows = _select_latest_epc_rows(result.get("data"))
            result["data"] = latest_rows
            trace = result.get("trace") or {}
            try:
                trace["rows_returned"] = len(latest_rows or [])
            except Exception:
                pass
            result["trace"] = trace
        result["data"] = _enrich_building_data(result.get("data"))
        trace = result.get("trace") or {}
        data = result.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            trace["returned_values_used"] = data[0]
        elif isinstance(data, dict):
            trace["returned_values_used"] = data
        result["trace"] = trace

    updates: Dict[str, Any] = {"done_generic_sql": True}
    outs: List[str] = []
    trace = (result or {}).get("trace") or {
        "query_type": "generic_sql",
        "execution_status": "not_run",
    }
    metadata_updates = _deep_merge(
        md,
        {
            "generic_sql_trace": trace,
            "query_traces": {"generic_sql": trace},
        },
    )

    if result and result.get("ok"):
        identity_check = assess_building_identity(md, result.get("data"))
        if (
            identity_check.get("status") == "ambiguous"
            and addr
            and _is_specific_address(addr)
            and isinstance(result.get("data"), list)
            and result.get("data")
        ):
            identity_check = _deep_merge(
                identity_check,
                {
                    "status": "passed",
                    "ambiguous": False,
                    "multiple_matches": False,
                    "matched_address": addr,
                    "multiple_records_same_address": True,
                    "accepted_multiple_records_for_specific_address": True,
                },
            )
        metadata_updates = _deep_merge(
            metadata_updates,
            {
                "building_identity_check": identity_check,
            },
        )

        if identity_check.get("status") == "passed":
            updates["agent_data_generic"] = result.get("data")
            metadata_updates = merge_identifier_metadata(metadata_updates, result.get("data"))
            metadata_updates = _deep_merge(
                metadata_updates,
                {
                    "clarification": {
                        "needed": False,
                        "reason": None,
                        "question_asked": None,
                        "resolved": True,
                        "resolved_after_turns": 0,
                    },
                },
            )
            outs.append("generic_sql: ok")
            try:
                n = len(result.get("data") or [])
            except Exception:
                n = "?"
            print(f"[generic_sql_agent] SUCCESS rows={n}", flush=True)
        else:
            clarification_reason = (
                "ambiguous_address"
                if identity_check.get("ambiguous")
                else "missing_building_data"
            )
            metadata_updates = _deep_merge(
                metadata_updates,
                {
                    "clarification": {
                        "needed": True,
                        "reason": clarification_reason,
                        "question_asked": build_clarification_question(clarification_reason),
                        "resolved": False,
                        "resolved_after_turns": None,
                    },
                    "building_candidate_matches": result.get("data")[:5] if isinstance(result.get("data"), list) else result.get("data"),
                },
            )
            updates["identity_gate_blocked"] = True
            outs.append("generic_sql: ambiguous or conflicting building match detected.")
            print(f"[generic_sql_agent] IDENTITY BLOCK status={identity_check.get('status')}", flush=True)
    else:
        if addr:
            outs.append(f"generic_sql: no rows for address {addr!r} (ASCII fallback tried if applicable).")
            clarification_reason = (
                "building_not_found"
                if ((trace or {}).get("execution_status") == "not_found" or result)
                else "missing_building_data"
            )
            metadata_updates = _deep_merge(
                metadata_updates,
                {
                    "clarification": {
                        "needed": True,
                        "reason": clarification_reason,
                        "question_asked": build_clarification_question(clarification_reason),
                        "resolved": False,
                        "resolved_after_turns": None,
                    },
                    "building_identity_check": {
                        "status": "missing",
                        "matched_building_id": None,
                        "matched_address": addr,
                        "ambiguous": False,
                        "multiple_matches": False,
                        "conflicting_metadata": False,
                    },
                },
            )
            if not _context_requests_vector(state):
                updates["identity_gate_blocked"] = True
        else:
            key = next((k for k in SINGLE_FILTER_KEYS if md.get(k) not in (None, "")), None)
            if key:
                value = md.get(key)
                outs.append(f"generic_sql: no rows for {key} {value!r}.")
                metadata_updates = _deep_merge(
                    metadata_updates,
                    {
                        "clarification": {
                            "needed": True,
                            "reason": "building_not_found_by_id" if key == "byggnadsid" else "missing_building_data",
                            "question_asked": build_clarification_question(
                                "building_not_found_by_id" if key == "byggnadsid" else "missing_building_data"
                            ),
                            "resolved": False,
                            "resolved_after_turns": None,
                        },
                        "building_identity_check": {
                            "status": "missing",
                            "matched_building_id": value if key == "byggnadsid" else None,
                            "matched_address": None,
                            "ambiguous": False,
                            "multiple_matches": False,
                            "conflicting_metadata": False,
                        },
                    },
                )
                if not _context_requests_vector(state):
                    updates["identity_gate_blocked"] = True
            else:
                outs.append("generic_sql: no address/supported single-filter provided.")
        print("[generic_sql_agent] NO DATA", flush=True)
    updates["metadata"] = metadata_updates
    updates["agent_outputs_generic"] = outs
    print("[generic_sql_agent] EXIT", flush=True)
    return updates

def specialized_sql_agent_node(state: GraphState) -> GraphState:
    par = state.get("parallel") or {}
    if par.get("active") and not par.get("required", {}).get("specialized_sql", False):
        print("[specialized_sql_agent] skipped by parallel plan", flush=True)
        return {"done_specialized_sql": True}

    updates: Dict[str, Any] = {"done_specialized_sql": True}
    outs: List[str] = []

    if _SPECIALIZED_INIT_ERROR:
        outs.append(_SPECIALIZED_INIT_ERROR)
        print(f"[specialized_sql_agent] INIT ERROR: {_SPECIALIZED_INIT_ERROR}", flush=True)
        updates["agent_outputs_specialized"] = outs
        return updates

    md = state.get("metadata", {}) or {}
    addr = md.get("address")
    recognized = _is_address_recognized(addr)
    print(f"[specialized_sql_agent] ENTER addr={addr!r} recognized={recognized}", flush=True)

    if not addr or not recognized:
        outs.append("Specialized SQL skipped: address not recognized in specialized DB.")
        updates["agent_outputs_specialized"] = outs
        print("[specialized_sql_agent] SKIP (unrecognized address)", flush=True)
        return updates

    try:
        # Use the new 2-step interface: route(...) then execute(...)
        op, kwargs = specialized_layer.route(state)  # provides question + metadata
        # Safeguard: ensure metadata is present in kwargs even if caller didn’t have it
        if md.get("building_id") is not None:
            kwargs["building_id"] = str(md.get("building_id"))
        if md.get("address") is not None:
            kwargs["address"] = str(md.get("address"))

        result = specialized_layer.execute(op, kwargs)
        trace = result.get("sql_trace") or {}
        updates["metadata"] = _deep_merge(
            md,
            {
                "sql_trace": trace,
                "query_traces": {"specialized_sql": trace},
            },
        )

        sql = result.get("sql")
        if sql:
            outs.append("SQL:\n" + sql)

        if result.get("ok"):
            if "data" in result and result["data"] is not None:
                updates["agent_data_specialized"] = result["data"]
                updates["metadata"] = merge_identifier_metadata(updates.get("metadata") or md, result["data"])
                outs.append(_describe_payload_for_aggregation(result["data"]))
            elif "message" in result and result["message"]:
                outs.append(f"Specialized SQL message: {result['message']}")
            print("[specialized_sql_agent] SUCCESS", flush=True)
        else:
            msg = result.get("message")
            outs.append(f"Specialized SQL failed: {msg}")
            print(f"[specialized_sql_agent] FAIL msg={msg}", flush=True)

    except Exception as e:
        outs.append(f"Specialized SQL error: {e}")
        print(f"[specialized_sql_agent] ERROR: {e}", flush=True)

    updates["agent_outputs_specialized"] = outs
    print("[specialized_sql_agent] EXIT", flush=True)
    return updates


def vector_db_agent_node(state: GraphState) -> GraphState:
    par = state.get("parallel") or {}
    if par.get("active") and not par.get("required", {}).get("vector", False):
        print("[vector_db_agent] skipped by parallel plan", flush=True)
        return {"done_vector": True}

    ctx = state.get("context", {}) or {}
    query = ctx.get("effective_query") or state.get("last_message") or ""
    entities = ctx.get("entities") or []
    hint = f" Entities: {', '.join(entities)}" if entities else ""
    q = f"{query}{hint}".strip()

    print(f"[vector_db_agent] ENTER q.len={len(q)} entities={len(entities)}", flush=True)

    updates: Dict[str, Any] = {"done_vector": True}
    outs: List[str] = []

    try:
        hits = vector_database.query(q)
        count = len(hits or [])
        print(f"[vector_db_agent] hits={count}", flush=True)
        if hits:
            snippets = [str((item or {}).get("page_content", "")).strip() for item in hits]
            sources = [str((item or {}).get("source", "")).strip() for item in hits]
            content_from_doc = ' '.join(snippet for snippet in snippets if snippet)
            outs.append("Vector hits:\n" + content_from_doc )
            updates["agent_data_vector"] = {
                "hits": hits,
                "sources": [source for source in sources if source],
                "snippets": [snippet for snippet in snippets if snippet],
            }
        else:
            outs.append("Vector: No relevant passages found.")
    except Exception as e:
        outs.append(f"Vector search error: {e}")
        print(f"[vector_db_agent] ERROR: {e}", flush=True)

    updates["agent_outputs_vector"] = outs
    print("[vector_db_agent] EXIT", flush=True)
    return updates

# ============
# Other agents
# ============
def simulation_agent_node(state: GraphState) -> GraphState:
    print("[simulation_agent] ENTER", flush=True)
    print("[simulation_agent] EXIT", flush=True)
    return {"agent_outputs_simulation": ["Simulation step (stub)."], "done_simulation": True}

def other_agent_node(state: GraphState) -> GraphState:
    print("[other_agent] ENTER (fallback)", flush=True)
    print("[other_agent] EXIT", flush=True)
    return {"agent_outputs_other": ["Routed to the 'other' agent (fallback)."], "done_other": True}

# =====================
# Aggregation & summary
# =====================
def aggregator_node(state: GraphState) -> GraphState:
    print("[aggregator] ENTER", flush=True)

    outputs: List[str] = []
    for k in ("agent_outputs_generic", "agent_outputs_specialized",
              "agent_outputs_vector", "agent_outputs_simulation",
              "agent_outputs_other"):
        outs = state.get(k) or []
        outputs.extend(outs if isinstance(outs, list) else [outs])

    aggregated_text = "\n\n".join(outputs) if outputs else "No agent output."

    merged_data: Dict[str, Any] = {}
    legacy_agent_data: Dict[str, Any] = {}

    # --- SQL sections
    if "agent_data_generic" in state:
        merged_data["generic_sql"] = state["agent_data_generic"]
        legacy_agent_data["generic_sql"] = state["agent_data_generic"]

    if "agent_data_specialized" in state:
        merged_data["specialized_sql"] = state["agent_data_specialized"]
        legacy_agent_data["specialized_sql"] = state["agent_data_specialized"]

    # --- VECTOR sections (be permissive about where they came from)
    vector_block = None

    # Preferred: explicit keys
    if "agent_vector_sources" in state or "agent_vector_snippets" in state:
        vector_block = {
            "sources": state.get("agent_vector_sources", []) or [],
            "snippets": state.get("agent_vector_snippets", []) or [],
        }

    # Fallback: some agents store a packed dict at agent_data_vector
    if vector_block is None and "agent_data_vector" in state:
        vector_block = state["agent_data_vector"]

    # Fallback: reuse whatever was already in agent_data.vector
    if vector_block is None and isinstance(state.get("agent_data"), dict):
        if isinstance(state["agent_data"].get("vector"), dict):
            vector_block = state["agent_data"]["vector"]

    if vector_block is not None:
        merged_data["vector"] = vector_block
        legacy_agent_data["vector"] = vector_block

    debug_label = _compute_agent_answered(state)
    answered_raw = []
    if state.get("done_generic_sql"):
        answered_raw.append({"type": "sql", "name": "generic_sql"})
    if state.get("done_specialized_sql"):
        answered_raw.append({"type": "sql", "name": "specialized_sql"})
    if state.get("done_vector"):
        answered_raw.append({"type": "vector", "name": "vector"})

    new_debug = _deep_merge(state.get("metadata", {}).get("debug", {}), {
        "agent_answered": debug_label,
        "agent_answered_raw": answered_raw
    })

    # Start with a minimal update
    updates: Dict[str, Any] = {
        "aggregated": aggregated_text,
        "agent_outputs": outputs,
        "metadata": _deep_merge(state.get("metadata", {}), {"debug": new_debug}),
    }

    # Deep-merge agent_data so we never drop previous sections
    existing_agent_data = state.get("agent_data", {}) if isinstance(state.get("agent_data"), dict) else {}
    if legacy_agent_data or existing_agent_data:
        updates["agent_data"] = _deep_merge(existing_agent_data, legacy_agent_data)

    # Same for aggregated_data if you use it elsewhere
    existing_agg_data = state.get("aggregated_data", {}) if isinstance(state.get("aggregated_data"), dict) else {}
    if merged_data or existing_agg_data:
        updates["aggregated_data"] = _deep_merge(existing_agg_data, merged_data)

    try:
        out_count = len(outputs)
    except Exception:
        out_count = "?"
    print(f"[aggregator] outputs={out_count} agent_answered={debug_label}", flush=True)
    print("[aggregator] EXIT", flush=True)
    return updates

# def aggregator_node(state: GraphState) -> GraphState:
#     print("[aggregator] ENTER", flush=True)
#     outputs: List[str] = []
#     for k in ("agent_outputs_generic", "agent_outputs_specialized", "agent_outputs_vector",
#               "agent_outputs_simulation", "agent_outputs_other"):
#         outs = state.get(k) or []
#         outputs.extend(outs if isinstance(outs, list) else [outs])

#     aggregated_text = "\n\n".join(outputs) if outputs else "No agent output."

#     merged_data: Dict[str, Any] = {}
#     legacy_agent_data: Dict[str, Any] = {}

#     if "agent_data_generic" in state:
#         merged_data["generic_sql"] = state["agent_data_generic"]
#         legacy_agent_data["generic_sql"] = state["agent_data_generic"]
#     if "agent_data_specialized" in state:
#         merged_data["specialized_sql"] = state["agent_data_specialized"]
#         legacy_agent_data["specialized_sql"] = state["agent_data_specialized"]
#     if "agent_vector_sources" in state or "agent_vector_snippets" in state:
#         merged_data["vector"] = {
#             "sources": state.get("agent_vector_sources", []),
#             "snippets": state.get("agent_vector_snippets", []),
#         }
#         legacy_agent_data["vector"] = merged_data["vector"]

#     debug_label = _compute_agent_answered(state)
#     answered_raw = []
#     if state.get("done_generic_sql"):
#         answered_raw.append({"type": "sql", "name": "generic_sql"})
#     if state.get("done_specialized_sql"):
#         answered_raw.append({"type": "sql", "name": "specialized_sql"})
#     if state.get("done_vector"):
#         answered_raw.append({"type": "vector", "name": "vector"})

#     new_debug = _deep_merge(state.get("metadata", {}).get("debug", {}), {
#         "agent_answered": debug_label,
#         "agent_answered_raw": answered_raw
#     })

#     updates = {
#         "aggregated": aggregated_text,
#         "agent_outputs": outputs,
#         "metadata": _deep_merge(state.get("metadata", {}), {"debug": new_debug}),
#     }
#     if merged_data:
#         updates["aggregated_data"] = copy.deepcopy(merged_data)
#         updates["agent_data"] = copy.deepcopy(legacy_agent_data)

#     try:
#         out_count = len(outputs)
#     except Exception:
#         out_count = "?"
#     print(f"[aggregator] outputs={out_count} agent_answered={debug_label}", flush=True)
#     print("[aggregator] EXIT", flush=True)
#     return updates

def llm_summarizer_node(state: GraphState) -> GraphState:
    print("[llm_summarizer] ENTER", flush=True)
    ctx = state.get("context", {}) or {}
    metadata = merge_identifier_metadata(
        state.get("metadata", {}) or {},
        state.get("aggregated_data"),
        state.get("agent_data"),
        state.get("session_state"),
    )
    current_address = metadata.get("address") or metadata.get("address_from_user") or ""
    building_id = (
        select_preferred_identifier(
            metadata,
            state.get("aggregated_data"),
            state.get("agent_data"),
            state.get("session_state"),
        )
        or "building_id_not_available"
    )
    identity_check = metadata.get("building_identity_check") or assess_building_identity(
        metadata,
        state.get("agent_data_generic"),
        state.get("agent_data_specialized"),
        state.get("aggregated_data"),
        state.get("agent_data"),
    )
    retrieved_facts = extract_retrieved_facts(
        metadata,
        state.get("aggregated_data"),
        state.get("agent_data"),
    )
    epc_record_address = _extract_first_address_for_keys(
        (
            state.get("agent_data_generic"),
            state.get("agent_data_specialized"),
            state.get("aggregated_data"),
            state.get("agent_data"),
        ),
        ("epc_idadr", "official_address"),
    ) or identity_check.get("matched_address")
    metadata = _deep_merge(
        metadata,
        {
            "building_identity_check": identity_check,
            "retrieved_facts": retrieved_facts,
        },
    )
    if current_address and epc_record_address:
        requested_norm = _normalize_address(current_address)
        record_norm = _normalize_address(epc_record_address)
        if requested_norm and record_norm and requested_norm != record_norm and identity_check.get("status") == "passed":
            metadata = _deep_merge(
                metadata,
                {
                    "requested_address": current_address,
                    "epc_record_address": epc_record_address,
                    "same_building_multiple_addresses": True,
                    "address_context_note": (
                        "The requested address and EPC record address differ, but the building identity "
                        "check passed for the same building ID. Treat them as addresses/aliases for the "
                        "same building unless the building ID conflicts."
                    ),
                },
            )
    metadata["data_freshness"] = compute_data_freshness(
        metadata,
        state.get("aggregated_data"),
        state.get("agent_data"),
    )
    route_hint = "clarification" if (metadata.get("clarification") or {}).get("needed") else "building_specific"
    metadata["uncertainty"] = compute_uncertainty(
        metadata,
        retrieved_facts,
        route=route_hint,
    )
    action_description = (
        " ; ".join(str(item) for item in (ctx.get("intent_list") or []) if str(item).strip())
        or str(ctx.get("parsed_intent") or "")
        or "No specific action was recorded."
    )
    prompt = build_building_response_prompt(
        user_input=state.get("last_message") or "",
        current_address=str(current_address),
        history=state.get("messages") or [],
        action_description=action_description,
        results=state.get("aggregated_data") or state.get("aggregated") or {},
        metadata=metadata,
        building_id=building_id,
    )
    try:
        answer = llm_summarizer.generate_response(prompt, message_list=[])
        answer = ensure_building_identifier_in_response(answer, building_id)
        answer = apply_response_safety_notes(answer, metadata)
        print("[llm_summarizer] generate_response ✓", flush=True)
    except Exception as e:
        answer = "Sorry, summarizer error."
        print(f"[llm_summarizer] ERROR: {e}", flush=True)

    sess = _latest_session_state(state.get("session_state"))
    sess["metadata"] = metadata
    sess["context"] = ctx
    if ctx.get("intent"):
        sess["last_intent"] = ctx.get("intent")
    if ctx.get("intent_list") is not None:
        sess["last_intent_list"] = ctx.get("intent_list")

    out = (state.get("step_out") or []) + ["llm_summarizer ✓"]
    print("[llm_summarizer] EXIT", flush=True)
    return {"final_response": answer, "session_state": sess, "step_out": out, "metadata": metadata}

# ======================
# Request address & misc
# ======================
def request_address_node(state: GraphState) -> GraphState:
    print("[request_address] ENTER → END", flush=True)
    md = state.get("metadata", {}) or {}
    clarification = md.get("clarification") or {}
    reason = clarification.get("reason") or "missing_address"
    question = clarification.get("question_asked") or build_clarification_question(reason)
    return {
        "final_response": question,
        "metadata": _deep_merge(
            md,
            {
                "clarification": {
                    "needed": True,
                    "reason": reason,
                    "question_asked": question,
                    "resolved": False,
                    "resolved_after_turns": None,
                }
            },
        ),
    }

def clarification_node(state: GraphState) -> GraphState:
    print("[clarification] ENTER → END", flush=True)
    md = state.get("metadata", {}) or {}
    clarification = md.get("clarification") or {}
    reason = clarification.get("reason") or "incomplete_question"
    question = clarification.get("question_asked") or build_clarification_question(reason)
    return {
        "final_response": question,
        "metadata": _deep_merge(
            md,
            {
                "clarification": {
                    "needed": True,
                    "reason": reason,
                    "question_asked": question,
                    "resolved": False,
                    "resolved_after_turns": None,
                }
            },
        ),
    }

# ====================
# Graph wiring (NEW)
# ====================
def _identity(state: GraphState) -> GraphState:
    print("[identity] passthrough", flush=True)
    return {}

def _route_after_agent(state: GraphState) -> str:
    hop = "join_sql_vector" if state.get("parallel", {}).get("active") else "aggregator"
    print(f"[route_after_agent] → {hop}", flush=True)
    return hop

def build_building_flow_graph() -> StateGraph:
    print("[build_graph] wiring graph ...", flush=True)
    builder = StateGraph(GraphState)

    # Core nodes
    builder.add_node("understand_context", understand_context_node)
    builder.add_node("brf_resolution", brf_resolution_node)
    builder.add_node("clarification", clarification_node)
    builder.add_node("maintain_history", maintain_history_node)

    # Barrier wait + await
    builder.add_node("wait_for_replies", wait_for_replies_node)
    builder.add_node("await_more", await_more_node)

    # Decision (passthrough node kept; router provides multi-send fanout)
    # builder.add_node("decision", _identity)

    # Agent nodes
    builder.add_node("generic_sql_agent", generic_sql_agent_node)
    builder.add_node("specialized_sql_agent", specialized_sql_agent_node)
    builder.add_node("vector_db_agent", vector_db_agent_node)
    builder.add_node("simulation_agent", simulation_agent_node)
    builder.add_node("other_agent", other_agent_node)

    # Aggregation & summary
    builder.add_node("aggregator", aggregator_node)
    builder.add_node("llm_summarizer", llm_summarizer_node)

    # Address request
    builder.add_node("request_address", request_address_node)

    # Entry
    builder.set_entry_point("understand_context")

    builder.add_edge("understand_context", "brf_resolution")

    # After parsing and optional BRF lookup: ambiguity + address/building-id gate
    builder.add_conditional_edges(
        "brf_resolution",
        route_after_brf_resolution,
        {
            "clarification": "clarification",
            "maintain_history": "maintain_history",
            "request_address": "request_address",
        },
    )

    # From history, go straight to decision (we no longer use a separate fanout node)
    # builder.add_edge("maintain_history", "decision")

    # --- FAN-OUT AT DECISION (via intent_list) ---
    # builder.add_conditional_edges("decision", decision_router)
    builder.add_conditional_edges("maintain_history", decision_router)

    # Every selected agent must rejoin at the barrier
    builder.add_edge("generic_sql_agent", "wait_for_replies")
    builder.add_edge("specialized_sql_agent", "wait_for_replies")
    builder.add_edge("vector_db_agent", "wait_for_replies")

    # If simulation/other are used elsewhere, keep their flow into the aggregator
    builder.add_edge("simulation_agent", "aggregator")
    builder.add_edge("other_agent", "aggregator")

    # Join → (optionally await) → aggregate → summarize → END
    builder.add_conditional_edges(
        "wait_for_replies",
        route_after_wait,
        {"aggregator": "aggregator", "await_more": "await_more", "clarification": "clarification"},
    )

    builder.add_edge("aggregator", "llm_summarizer")
    builder.add_edge("llm_summarizer", END)

    # Early exits
    builder.add_edge("clarification", END)
    builder.add_edge("request_address", END)
    builder.add_edge("await_more", END)


    return builder.compile()
