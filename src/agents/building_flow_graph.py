# src/agents/building_flow_graph.py
from __future__ import annotations
import re
from typing import Any, Dict, List, Tuple, Optional
import copy
import unicodedata
import os

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

ADDRESS_LOCATION_HINT_KEYS = (
    "address_location_hint",
    "address_city",
    "city",
    "postort",
    "municipality",
    "kommun",
    "postcode",
    "postal_code",
    "postnr",
)

ROW_LOCATION_KEYS = (
    "epc_idpostort",
    "postort",
    "postal_town",
    "city",
    "ort",
    "epc_idkommun",
    "kommun",
    "municipality",
    "epc_idpostnr",
    "postnr",
    "postcode",
    "postal_code",
    "zip",
)

ROW_POST_TOWN_KEYS = (
    "epc_idpostort",
    "postort",
    "postal_town",
    "city",
    "ort",
)

ROW_MUNICIPALITY_KEYS = (
    "epc_idkommun",
    "kommun",
    "municipality",
)

ROW_POSTCODE_KEYS = (
    "epc_idpostnr",
    "postnr",
    "postcode",
    "postal_code",
    "zip",
)

PENDING_AMBIGUOUS_ADDRESS_KEY = "pending_ambiguous_address"

ADDRESS_DISAMBIGUATION_REQUEST_RE = re.compile(
    r"(?=.*\b(?:address|building|property|brf|adress|byggnad|fastighet)\b)"
    r"(?=.*\b(?:city|postcode|postal\s+code|municipality|postort|postnummer|kommun|"
    r"building\s+id|byggnadsid|brf)\b)"
    r".*\b(?:multiple|several|ambiguous|different|correct|which|choose|use|"
    r"flera|olika|rätt|vilken|välj|använd)\b",
    flags=re.IGNORECASE,
)

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


def _normalize_location(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    normalized = _ascii_fold(str(value)) or ""
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _compact_digits(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    digits = re.sub(r"\D+", "", str(value))
    return digits or None


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
    r"(?:i\s+live(?:\s+(?:in|at|on))?|i\s+am\s+at|i'm\s+at|my\s+address\s+is|address\s+is|"
    r"we\s+live(?:\s+(?:in|at|on))?|our\s+address\s+is|"
    r"jag\s+bor\s+p[åa]|vi\s+bor\s+p[åa]|min\s+adress\s+[äa]r|adressen\s+[äa]r)\s+"
    r"(.+?)(?=\s*(?:[.?!\n]|$))",
    flags=re.IGNORECASE,
)

BRF_NAME_RE = re.compile(
    r"\b(?:brf|bostadsr[äa]ttsf[öo]reningen|bostadsrattsforeningen)\s+"
    r"([A-Za-zÅÄÖåäö0-9][A-Za-zÅÄÖåäö0-9 .'\-]*?)"
    r"(?=\s*(?:[,.;:!?]|$|\bwhat\b|\bwhich\b|\bhow\b|\bwhy\b|\bdo\b|\bdoes\b|\bis\b|\bare\b|\bhave\b|\bhas\b|\bcan\b|\bcould\b|\bshould\b|\bwould\b|\bplease\b|\bvad\b|\bhur\b|\bvilken\b|\bvilket\b|\bmed\b|\bwith\b))",
    flags=re.IGNORECASE,
)

INVALID_BRF_NAME_START_RE = re.compile(
    r"^(?:a|an|the|with|without|in|at|on|from|for|to|of|do|does|did|can|could|"
    r"should|would|will|is|are|was|were|have|has|had|what|which|how|why|when|"
    r"where|who|please|tell|give|show|list|count|many|home|owner|homeowner|"
    r"housing|building|buildings|company|companies|municipality|kommun)\b",
    flags=re.IGNORECASE,
)

INVALID_BRF_NAME_PHRASE_RE = re.compile(
    r"\b(?:do\s+we\s+have|how\s+many|what\s+is\s+brf|home\s*owner|"
    r"heating\s+bills?|heating\s+costs?|what\s+can\s+we\s+do|stockholmshem)\b",
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

BUILDING_ID_RE = re.compile(
    r"\b\d{2}-\d{2}-[A-Za-zÅÄÖåäö0-9:_-]+-\d+\b",
    flags=re.IGNORECASE,
)

STREET_SUFFIX_RE = (
    r"gatan|vägen|vagen|gränd|grand|allén|allen|allé|alle|väg|road|street|avenue|lane"
)

STREET_ADDRESS_FRAGMENT_RE = re.compile(
    rf"\b("
    rf"(?:[A-ZÅÄÖ][A-Za-zÅÄÖåäö.'\-]*(?:{STREET_SUFFIX_RE})|"
    rf"[A-ZÅÄÖ][A-Za-zÅÄÖåäö.'\-]*(?:\s+[A-ZÅÄÖa-zåäö][A-Za-zÅÄÖåäö.'\-]*){{0,3}}\s+(?:{STREET_SUFFIX_RE}))"
    rf"\s+\d{{1,4}}[A-Za-z]?"
    rf")\b",
    flags=re.IGNORECASE,
)

MULTI_ADDRESS_SPLIT_RE = re.compile(r"\s*(?:,|;|/|&|\band\b|\boch\b|\beller\b|\bor\b)\s*", flags=re.IGNORECASE)

MULTI_ADDRESS_IDENTITY_RE = re.compile(
    r"\b(?:two|multiple|several|different|same|which|use|choose|addresses?|property|building|entrance|"
    r"två|flera|samma|vilken|använd|valj|välj|adresser|fastighet|byggnad|entré)\b",
    flags=re.IGNORECASE,
)

LOCATION_HINT_STOP_RE = re.compile(
    r"\b(?:what|which|how|why|when|show|tell|give|does|do|is|are|can|could|please|"
    r"vad|vilken|vilket|hur|varf[öo]r|visa|ber[äa]tta|kan)\b",
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


def _extract_street_address_fragment(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = STREET_ADDRESS_FRAGMENT_RE.search(str(text))
    if not match:
        match = re.search(
            rf"\b("
            rf"[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö.'\-]*?(?:{STREET_SUFFIX_RE})"
            rf"\s+\d{{1,4}}[A-Za-z]?"
            rf")\b",
            str(text),
            flags=re.IGNORECASE,
        )
    if not match:
        return None
    fragment = match.group(1).strip(" .,:;!?\"'")
    if _is_address_recognized(fragment) or _looks_like_compact_address(fragment, allow_four_digit_number=True):
        return fragment
    folded = _ascii_fold(fragment)
    if folded and (_is_address_recognized(folded) or _looks_like_compact_address(folded, allow_four_digit_number=True)):
        return fragment
    return None


def _clean_location_hint(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None

    candidate = re.sub(r"\s+", " ", str(value)).strip(" .,:;!?\"'")
    if not candidate:
        return None

    if LOCATION_HINT_STOP_RE.match(candidate):
        return None

    candidate = re.split(r"[,.;?!]", candidate, maxsplit=1)[0].strip(" .,:;!?\"'")
    candidate = LOCATION_HINT_STOP_RE.split(candidate, maxsplit=1)[0].strip(" .,:;!?\"'")
    if not candidate or len(candidate) > 80:
        return None
    if not re.search(r"[A-Za-zÅÄÖåäö0-9]", candidate):
        return None
    return candidate


def _strip_location_tail_from_address_candidate(
    text: Optional[str],
    *,
    allow_four_digit_number: bool = False,
) -> Optional[str]:
    if not text:
        return None

    candidate = re.sub(r"\s+", " ", str(text)).strip(" .,:;!?\"'")
    if not candidate:
        return None

    match = re.match(
        r"^(.+?\s+\d{1,4}[A-Za-z]?(?:\s*[-–]\s*\d{1,4}[A-Za-z]?)?)"
        r"(?:\s*,\s*|\s+(?:in|i)\s+)"
        r"(?:\d{3}\s?\d{2}\s+)?[A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö .'\-]{0,80}"
        r"(?=\s*(?:[,.;?!]|$))",
        candidate,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    address = match.group(1).strip(" .,:;!?\"'")
    address = re.sub(
        r"^.*\b(?:at|on|for|address|building|byggnad(?:en)?|p[åa])\s+",
        "",
        address,
        flags=re.IGNORECASE,
    ).strip(" .,:;!?\"'")
    if _is_address_recognized(address) or _looks_like_compact_address(
        address,
        allow_four_digit_number=allow_four_digit_number,
    ):
        return address

    folded = _ascii_fold(address)
    if folded and (_is_address_recognized(folded) or _looks_like_compact_address(
        folded,
        allow_four_digit_number=allow_four_digit_number,
    )):
        return address

    return None


def _extract_address_location_hint_from_text(
    text: Optional[str],
    address: Optional[str],
) -> Optional[str]:
    if not text or not address:
        return None

    raw = str(text)
    normalized_address = _normalize_address(address)
    target_match = None

    literal_match = re.search(re.escape(str(address).strip()), raw, flags=re.IGNORECASE)
    if literal_match:
        target_match = literal_match

    for match in STREET_ADDRESS_FRAGMENT_RE.finditer(raw):
        if target_match is not None:
            break
        fragment = match.group(1).strip(" .,:;!?\"'")
        if _normalize_address(fragment) == normalized_address:
            target_match = match
            break

    if target_match is None:
        return None

    tail = raw[target_match.end():]
    postcode_match = re.match(
        r"^\s*,?\s*(\d{3}\s?\d{2})(?:\s+([A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö .'\-]{0,60}))?",
        tail,
        flags=re.IGNORECASE,
    )
    if postcode_match:
        postcode = postcode_match.group(1)
        town = postcode_match.group(2) or ""
        return _clean_location_hint(f"{postcode} {town}".strip())

    location_match = re.match(
        r"^\s*(?:,|\bin\b|\bi\b)\s*([A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö .'\-]{0,60})",
        tail,
        flags=re.IGNORECASE,
    )
    if location_match:
        return _clean_location_hint(location_match.group(1))

    followup_location_match = re.search(
        r"(?:^|[.?!;\n]\s*)"
        r"(?:oh\s+yes\s+sure|oh\s+yes|sure|yes|yeah|yep|ok|okay|of\s+course|"
        r"absolutely|noo?|nej|ja)?\s*[,.:;-]*\s*"
        r"(?:"
        r"(?:i|we)\s+live\s+(?:in|at)|"
        r"city\s+is|municipality\s+is|postcode\s+is|postal\s+code\s+is|"
        r"postort(?:en)?\s+[äa]r|kommun(?:en)?\s+[äa]r|postnummer(?:et)?\s+[äa]r|"
        r"jag\s+bor\s+i|vi\s+bor\s+i"
        r")\s+"
        r"([A-Za-zÅÄÖåäö][A-Za-zÅÄÖåäö .'\-]{0,60})"
        r"(?=\s*(?:[,.;?!\n]|$))",
        tail,
        flags=re.IGNORECASE,
    )
    if followup_location_match:
        return _clean_location_hint(followup_location_match.group(1))

    return None


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

    street_fragment = _extract_street_address_fragment(candidate)
    if street_fragment:
        return street_fragment

    had_address_cue = False
    cue_match = ADDRESS_INTRO_RE.search(candidate)
    if cue_match:
        had_address_cue = True
        candidate = cue_match.group(1)

    candidate = re.sub(
        r"^(?:i live in|i live at|i live on|i live|i'm at|i am at|my address is|address is|it's|it is)\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" .,:;")

    if not candidate:
        return None

    compact_prefix_match = re.match(
        r"^(.+?\s+\d{1,4}[A-Za-z]?(?:\s*[-–]\s*\d{1,4}[A-Za-z]?)?)"
        r"(?=\s*(?:[,.;?!]|\n|$))",
        candidate,
        flags=re.IGNORECASE,
    )
    if compact_prefix_match:
        compact_prefix = compact_prefix_match.group(1).strip(" .,:;!?\"'")
        if _looks_like_compact_address(
            compact_prefix,
            allow_four_digit_number=had_address_cue,
        ):
            return compact_prefix

    stripped_location = _strip_location_tail_from_address_candidate(
        candidate,
        allow_four_digit_number=had_address_cue,
    )
    if stripped_location:
        return stripped_location

    if _is_address_recognized(candidate):
        return candidate

    folded = _ascii_fold(candidate)
    if folded and _is_address_recognized(folded):
        return candidate

    address_fragment = STREET_ADDRESS_FRAGMENT_RE.search(candidate)
    if address_fragment:
        fragment = address_fragment.group(1).strip(" .,:;!?\"'")
        if _is_address_recognized(fragment):
            return fragment
        folded_fragment = _ascii_fold(fragment)
        if folded_fragment and _is_address_recognized(folded_fragment):
            return fragment
        if _looks_like_compact_address(fragment, allow_four_digit_number=had_address_cue):
            return fragment

    if _looks_like_compact_address(candidate, allow_four_digit_number=had_address_cue):
        return candidate

    return None


def _extract_location_only_hint_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None

    candidate = re.sub(r"\s+", " ", str(text)).strip(" .,:;!?\"'")
    if not candidate:
        return None

    candidate = re.sub(
        r"^\s*(?:oh\s+yes\s+sure|oh\s+yes|sure|yes|yeah|yep|ok|okay|of course|absolutely|noo?|nej|ja)\s*[,.:;-]*\s*",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(
        r"^(?:i\s+live\s+(?:in|at)|we\s+live\s+(?:in|at)|"
        r"i\s+live|we\s+live|"
        r"city\s+is|municipality\s+is|postcode\s+is|postal\s+code\s+is|"
        r"postort(?:en)?\s+[äa]r|kommun(?:en)?\s+[äa]r|postnummer(?:et)?\s+[äa]r|"
        r"i|in)\s+",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = _clean_location_hint(candidate)
    if not candidate:
        return None

    if _extract_building_id_from_text(candidate) or _extract_address_candidate_from_text(candidate):
        return None

    return candidate


def _awaiting_address_location_disambiguation(metadata: Optional[Dict[str, Any]]) -> bool:
    metadata = metadata or {}
    clarification = metadata.get("clarification") or {}
    identity_check = metadata.get("building_identity_check") or {}
    return bool(
        clarification.get("needed")
        and clarification.get("reason") == "ambiguous_address"
        and (
            metadata.get("address")
            or metadata.get("address_from_user")
            or metadata.get(PENDING_AMBIGUOUS_ADDRESS_KEY)
        )
        and not _extract_metadata_location_hint(metadata)
        and identity_check.get("status") in (None, "", "ambiguous")
    )


def _extract_pending_ambiguous_address_from_messages(messages: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    last_user_address: Optional[str] = None
    pending_address: Optional[str] = None

    for message in messages or []:
        role = str((message or {}).get("role") or "").lower()
        content = str((message or {}).get("content") or "")
        if role == "user":
            address = _extract_address_candidate_from_text(content)
            if address:
                last_user_address = address
        elif role == "assistant" and ADDRESS_DISAMBIGUATION_REQUEST_RE.search(content):
            if last_user_address:
                pending_address = last_user_address

    return pending_address


def _dedupe_address_candidates(candidates: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for candidate in candidates or []:
        cleaned = re.sub(r"\s+", " ", str(candidate or "")).strip(" .,:;!?\"'")
        cleaned = re.sub(r"^\s*(?:and|or|och|eller)\s+", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip(" .,:;!?\"'")
        key = _normalize_address(cleaned)
        if not cleaned or not key or key in seen:
            continue
        deduped.append(cleaned)
        seen.add(key)
    return deduped


def _clean_address_candidate_fragment(fragment: Optional[str]) -> Optional[str]:
    if not fragment:
        return None

    candidate = re.sub(r"\s+", " ", str(fragment)).strip()
    if not candidate:
        return None

    candidate = re.sub(
        r"^.*\b(?:addresses?|adresser|adressen|adress)\b\s*[:\-]*\s*",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.split(r"[?!.]\s+", candidate, maxsplit=1)[0]
    candidate = re.sub(r"^\s*(?:and|or|och|eller)\s+", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.strip(" .,:;!?\"'")

    cleaned = _extract_address_candidate_from_text(candidate)
    if cleaned:
        return cleaned

    match = STREET_ADDRESS_FRAGMENT_RE.search(candidate)
    if match:
        return match.group(1).strip(" .,:;!?\"'")

    return None


def _extract_address_candidates_from_text(text: Optional[str]) -> List[str]:
    if not text:
        return []

    raw = str(text)
    candidates = [match.group(1) for match in STREET_ADDRESS_FRAGMENT_RE.finditer(raw)]
    if len(_dedupe_address_candidates(candidates)) >= 2:
        return _dedupe_address_candidates(candidates)

    fragments = MULTI_ADDRESS_SPLIT_RE.split(raw)
    for fragment in fragments:
        cleaned = _clean_address_candidate_fragment(fragment)
        if cleaned:
            candidates.append(cleaned)

    return _dedupe_address_candidates(candidates)


def _segment_is_address_only(segment: str) -> bool:
    cleaned = _clean_address_candidate_fragment(segment)
    if not cleaned:
        return False

    remainder = str(segment or "")
    remainder = re.sub(re.escape(cleaned), "", remainder, count=1, flags=re.IGNORECASE)
    remainder = re.sub(r",?\s*\b\d{3}\s?\d{2}\b\s+[A-Za-zÅÄÖåäö .'\-]+", "", remainder)
    remainder = re.sub(r"[\s,.;:()\-]+", "", remainder)
    return not remainder


def _looks_like_address_list_message(text: Optional[str], candidates: List[str]) -> bool:
    if len(candidates or []) < 2:
        return False

    raw = str(text or "").strip()
    if not raw or (";" not in raw and "\n" not in raw):
        return False

    segments = [segment.strip() for segment in re.split(r"\s*(?:;|\n)+\s*", raw) if segment.strip()]
    return len(segments) >= 2 and all(_segment_is_address_only(segment) for segment in segments)


def _looks_like_multi_address_identity_question(text: Optional[str], candidates: List[str]) -> bool:
    if len(candidates or []) < 2:
        return False

    lowered = str(text or "").lower()
    if not lowered.strip():
        return False
    if _looks_like_address_list_message(text, candidates):
        return True

    return bool(
        MULTI_ADDRESS_IDENTITY_RE.search(lowered)
        and (
            "which" in lowered
            or "vilken" in lowered
            or "use" in lowered
            or "använd" in lowered
            or "address" in lowered
            or "adress" in lowered
            or "property" in lowered
            or "fastighet" in lowered
        )
    )


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
    if re.fullmatch(r"(?:in|at|on|from|i|på)\s+[A-Za-zÅÄÖåäö .'\-]+", cleaned, flags=re.IGNORECASE):
        return None
    if INVALID_BRF_NAME_START_RE.search(cleaned):
        return None
    if INVALID_BRF_NAME_PHRASE_RE.search(cleaned):
        return None
    return cleaned or None


def _extract_brf_name_from_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    match = BRF_NAME_RE.search(str(text))
    if not match:
        return None
    return _clean_brf_name(match.group(1))


def _extract_recent_brf_name_from_messages(messages: Optional[List[Dict[str, Any]]]) -> Optional[str]:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages[:-1]):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        brf_name = _extract_brf_name_from_text(message.get("content"))
        if brf_name:
            return brf_name
    return None


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
    choice_match = re.fullmatch(r"(?:use\s+)?(?:option|choice|number|nr|#)?\s*(\d+)", text)
    choice_text = choice_match.group(1) if choice_match else text
    for option in options:
        if choice_text == _normalize_selection_text(option.get("choice")):
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


def _metadata_has_failed_brf_lookup(metadata: Optional[Dict[str, Any]]) -> bool:
    metadata = metadata or {}
    clarification = metadata.get("clarification")
    if isinstance(clarification, dict) and clarification.get("reason") in STALE_BRF_CLARIFICATION_REASONS:
        return True

    resolution = metadata.get("brf_resolution")
    if isinstance(resolution, dict) and resolution.get("status") in STALE_BRF_RESOLUTION_STATUSES:
        return True

    return False


def _clear_failed_brf_lookup_state(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cleaned = dict(metadata or {})
    clarification = cleaned.get("clarification")
    if isinstance(clarification, dict) and clarification.get("reason") in STALE_BRF_CLARIFICATION_REASONS:
        cleaned.pop("clarification", None)

    resolution = cleaned.get("brf_resolution")
    if isinstance(resolution, dict) and resolution.get("status") in STALE_BRF_RESOLUTION_STATUSES:
        cleaned.pop("brf_resolution", None)
        cleaned.pop("brf_name", None)

    cleaned.pop("pending_brf_resolution", None)
    return cleaned


def _looks_like_brf_resolution_continuation(message: Optional[str]) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    normalized = _normalize_selection_text(text)
    return bool(
        _extract_brf_name_from_text(text)
        or _extract_building_id_from_text(text)
        or _looks_like_compact_address(text, allow_four_digit_number=True)
        or re.fullmatch(r"(?:use\s+)?(?:option|choice|number|nr|#)?\s*\d+", normalized)
    )


def _looks_like_new_question_away_from_brf_resolution(message: Optional[str]) -> bool:
    text = str(message or "").strip()
    if not text or _looks_like_brf_resolution_continuation(text):
        return False

    lowered = text.lower()
    return bool(
        "?" in text
        or re.search(r"\b(?:what|how|why|when|where|which|who|tell|show|give|list|count)\b", lowered)
    )


def _iter_metadata_address_candidates(metadata: Dict[str, Any]) -> List[Any]:
    if not isinstance(metadata, dict):
        return []

    candidates: List[Any] = []
    for key in (
        "address",
        "address_from_user",
        PENDING_AMBIGUOUS_ADDRESS_KEY,
        "matched_address",
        "input_address",
        "official_address",
    ):
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
    r"\bbuilding[-\s]?specific\s+advice\b",
    r"\b(?:tailored|personalized|personalised)\s+advice\b",
    r"\bspecific\s+(?:advice|recommendations?|measures?)\b",
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
    "address_location_hint",
    PENDING_AMBIGUOUS_ADDRESS_KEY,
    "matched_address",
    "input_address",
    "official_address",
    "byggnadsid",
    "building_id",
    "50a_uuid",
    "01a_fnr",
)

PENDING_BUILDING_ADVICE_KEY = "pending_building_advice_request"

ADVICE_QUERY_FACT_LABELS = {
    "energy_class": "energy class",
    "energy_performance": "energy performance",
    "specific_energy_use": "specific energy use",
    "primary_energy_number": "primary energy number",
    "heating_system": "heating system",
    "ventilation_type": "ventilation type",
    "district_heating_use": "district heating use",
    "district_heating_space_heating": "district heating for space heating",
    "district_heating_domestic_hot_water": "district heating for domestic hot water",
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


def _looks_like_building_ecm_advice(text: Optional[str]) -> bool:
    return _looks_like_personal_energy_advice(text) or _looks_like_ecm_or_measure_advice(text)


def _looks_like_identity_followup(message: Optional[str], ctx: Dict[str, Any]) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    if _looks_like_building_ecm_advice(text):
        return False
    if re.search(r"\b(?:what|why|how|which|when|where|can|could|should|would|recommend|reduce|lower|improve)\b", lowered):
        return False

    return bool(
        _extract_building_id_from_text(text)
        or _extract_brf_name_from_text(text)
        or ctx.get("address")
    )


def _pending_advice_context(question: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "question": question,
        "type": "ecm",
        "context": {
            "parsed_intent": ctx.get("parsed_intent"),
            "intent_list": copy.deepcopy(ctx.get("intent_list") or []),
            "effective_query": ctx.get("effective_query"),
            "answer_focus": question,
            "advice_type": "ecm",
        },
    }


def _restore_pending_advice_context(
    ctx: Dict[str, Any],
    pending: Dict[str, Any],
) -> Dict[str, Any]:
    pending_context = pending.get("context") if isinstance(pending, dict) else {}
    if not isinstance(pending_context, dict):
        return ctx

    restored = dict(ctx or {})
    current_address = restored.get("address")
    restored.update(copy.deepcopy(pending_context))
    if current_address:
        restored["address"] = current_address
    restored["ambiguous"] = False
    restored["ambigious"] = False
    return restored


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


def _is_model_error_response(text: Optional[str]) -> bool:
    if not isinstance(text, str):
        return False
    return text.strip().lower().startswith("error:")


def _format_fact_value(value: Any, unit: Optional[str] = None) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value)
    return f"{text} {unit}" if unit and text else text


def _format_fact_value_for_user(key: str, value: Any, unit: Optional[str] = None) -> str:
    if key == "ventilation_type":
        normalized = str(value or "").strip().upper()
        if normalized == "FMED":
            return "FMED (mechanical exhaust ventilation with heat recovery)"
        if normalized == "F WITH HEAT RECOVERY":
            return "F with heat recovery (mechanical exhaust ventilation with heat recovery)"
    return _format_fact_value(value, unit)


FACT_CONCEPTS: Tuple[Dict[str, Any], ...] = (
    {
        "key": "energy_class",
        "label": "energy class",
        "patterns": (r"\benergy\s+class\b", r"\benergiklass\b"),
        "explanation": (
            "Energy classes usually run from A to G, where A is best and G is weakest; "
            "class {value} means there is likely room to improve the building's energy performance."
        ),
    },
    {
        "key": "energy_performance",
        "label": "declared energy performance",
        "patterns": (r"\benergy\s+performance\b", r"\benergiprestanda\b"),
        "unit": "kWh/m2-year",
        "explanation": (
            "Energy performance is the building's annual energy use per square meter of heated area. "
            "Lower numbers generally mean a more energy-efficient building."
        ),
    },
    {
        "key": "specific_energy_use",
        "label": "specific energy use",
        "patterns": (r"\benergy\s+index\b", r"\bspecific\s+energy\s+use\b", r"\beindex\b"),
        "unit": "kWh/m2-year",
        "preface": "I interpret energy index as specific energy use.",
        "explanation": (
            "Specific energy use is an annual kWh per square meter number, which makes buildings of different sizes easier to compare. "
            "Lower is generally better."
        ),
    },
    {
        "key": "primary_energy_number",
        "label": "primary energy number",
        "patterns": (r"\bprimary\s+energy\b", r"\bprimary\s+energy\s+number\b", r"\bprim[aä]renergital\b"),
        "unit": "kWh/m2-year",
        "explanation": (
            "The primary energy number is the official weighted energy figure used in Swedish energy-class rules. "
            "Lower is generally better."
        ),
    },
    {
        "key": "electricity_use",
        "label": "total electricity consumption",
        "patterns": (r"\b(?:total\s+)?electricity\s+(?:consumption|use|usage)\b", r"\bel(?:f[oö]rbrukning)?\b"),
        "unit": "kWh/year",
        "explanation": (
            "This is annual electricity use in the building record. "
            "Depending on the data source, it may describe shared/property electricity rather than every apartment's household electricity."
        ),
    },
    {
        "key": "heating_system",
        "label": "heating system",
        "patterns": (r"\bheating\s+system\b", r"\bmain\s+heating\b", r"\buppv[aä]rmning(?:ssystem)?\b", r"\bv[aä]rmesystem\b"),
        "explanation": "The heating system is how the building produces or receives heat for space heating and hot water.",
    },
    {
        "key": "ventilation_type",
        "label": "ventilation type",
        "patterns": (
            r"\bventilation\b",
            r"\bventiallation\b",
            r"\bventillation\b",
            r"\bftx\b",
            r"\bfmed\b",
            r"\bventilation\s+system\b",
            r"\bventiallation\s+system\b",
            r"\bventillation\s+system\b",
        ),
        "explanation": "Ventilation matters because it affects air quality, comfort, heat loss, and fan electricity.",
    },
    {
        "key": "construction_year",
        "label": "construction year",
        "patterns": (r"\b(?:construction|built|build)\s+year\b", r"\bwhen\s+was\b.*\b(?:built|constructed)\b"),
        "explanation": (
            "The construction year gives useful context about likely original insulation, windows, and ventilation design, "
            "but renovations can change performance a lot."
        ),
    },
    {
        "key": "energy_declaration_year",
        "label": "energy declaration year",
        "patterns": (r"\benergy\s+declaration\s+year\b", r"\bdeclaration\s+year\b", r"\bepc\s+year\b"),
        "explanation": (
            "This is the year the energy declaration data was approved. "
            "If the building has been renovated since then, the current performance may be different."
        ),
    },
    {
        "key": "district_heating_space_heating",
        "label": "district heating for space heating",
        "patterns": (r"\bdistrict\s+heating\b.*\bspace\s+heating\b", r"\bspace\s+heating\b"),
        "unit": "kWh/year",
        "explanation": "This is the heat used to warm apartments and common areas, separate from domestic hot water.",
    },
    {
        "key": "district_heating_domestic_hot_water",
        "label": "district heating for domestic hot water",
        "patterns": (r"\bdistrict\s+heating\b.*\bhot\s+water\b", r"\bdomestic\s+hot\s+water\b"),
        "unit": "kWh/year",
        "explanation": "This is the heat used for tap and shower hot water.",
    },
    {
        "key": "district_heating_use",
        "label": "total district heating use",
        "patterns": (r"\btotal\s+district\s+heating\b", r"\bdistrict\s+heating\s+(?:consumption|use|usage)\b"),
        "unit": "kWh/year",
        "explanation": (
            "This is annual purchased district heat for the building. "
            "High use can come from space heating, domestic hot water, control settings, or heat losses."
        ),
    },
    {
        "key": "epc_egenatemp",
        "label": "Atemp/heated area",
        "patterns": (r"\barea\b", r"\batemp\b", r"\bheated\s+area\b"),
        "unit": "m2",
        "explanation": "Atemp is the heated floor area used in Swedish energy declarations.",
    },
)

FACT_CONCEPT_BY_KEY: Dict[str, Dict[str, Any]] = {str(concept["key"]): concept for concept in FACT_CONCEPTS}

DIRECT_FACT_SPECS: Tuple[Tuple[str, str, Tuple[str, ...], Optional[str], Optional[str]], ...] = tuple(
    (
        str(concept["key"]),
        str(concept["label"]),
        tuple(concept.get("patterns") or ()),
        concept.get("unit"),
        concept.get("preface"),
    )
    for concept in FACT_CONCEPTS
)

FACT_VALUE_EXPLANATIONS: Dict[str, Dict[str, str]] = {
    "ventilation_type": {
        "FMED": (
            "FMED means mechanical exhaust ventilation with heat recovery: fans remove used indoor air, "
            "and some of that outgoing heat is recovered instead of being wasted."
        ),
        "F WITH HEAT RECOVERY": (
            "F with heat recovery means mechanical exhaust ventilation where some heat is recovered from outgoing air."
        ),
        "FTX": (
            "FTX ventilation means mechanical supply and exhaust ventilation with heat recovery, "
            "so outgoing air helps preheat incoming fresh air."
        ),
        "FT": "FT ventilation means mechanical supply and exhaust ventilation without heat recovery.",
        "F": "F ventilation means mechanical exhaust ventilation, where fans remove indoor air.",
        "S": "Self-draught ventilation relies mainly on natural pressure and temperature differences.",
        "SJALVDRAG": "Self-draught ventilation relies mainly on natural pressure and temperature differences.",
        "SJÄLVDRAG": "Self-draught ventilation relies mainly on natural pressure and temperature differences.",
    },
    "heating_system": {
        "DISTRICT HEATING": (
            "District heating means heat is produced centrally and delivered to the building through hot water pipes. "
            "The main savings usually come from controls, balancing, hot water, and reducing heat losses."
        ),
    },
}


def _fact_value_explanation_keys(value: Any) -> List[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    keys = [raw.upper()]
    folded = _ascii_fold(raw)
    if folded:
        keys.append(folded.upper())
    return list(dict.fromkeys(keys))


def _value_specific_fact_explanation(key: str, value: Any) -> Optional[str]:
    explanations = FACT_VALUE_EXPLANATIONS.get(key) or {}
    for candidate in _fact_value_explanation_keys(value):
        if candidate in explanations:
            return explanations[candidate]
    return None


PROFILE_FACT_LABEL_RE = re.compile(
    r"(?im)^\s*(?:energy class|declared energy performance|specific energy use|primary energy number|"
    r"energy declaration year|construction year|heating system|ventilation|electricity consumption|"
    r"district heating for space heating|district heating for domestic hot water|total district heating use)\s*:"
)


def _ventilation_explanation(value: Any) -> Optional[str]:
    return _value_specific_fact_explanation("ventilation_type", value)


def _plain_language_fact_explanation(key: str, value: Any) -> Optional[str]:
    concept = FACT_CONCEPT_BY_KEY.get(key)
    if not concept:
        return None

    value_specific = _value_specific_fact_explanation(key, value)
    concept_explanation = str(concept.get("explanation") or "").strip()
    if concept_explanation:
        concept_explanation = concept_explanation.format(value=_format_fact_value_for_user(key, value))

    if value_specific and concept_explanation and concept_explanation not in value_specific:
        return f"{value_specific} {concept_explanation}"
    return value_specific or concept_explanation or None


def _concept_request_mentions_known_fact_area(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return False
    return bool(
        re.search(
            r"\b(?:ventilation|ventiallation|ventillation|ftx|fmed|heating|district\s+heating|"
            r"energy\s+class|energy\s+performance|energy\s+index|specific\s+energy|primary\s+energy|"
            r"electricity|atemp|heated\s+area)\b",
            lowered,
        )
    )


def _looks_like_explicit_general_scope(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    return bool(lowered.strip() and any(re.search(pattern, lowered) for pattern in EXPLICIT_GENERAL_SCOPE_PATTERNS))


def _looks_like_fact_concept_overview_request(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    if (
        not lowered.strip()
        or _looks_like_explicit_general_scope(lowered)
        or not _concept_request_mentions_known_fact_area(lowered)
    ):
        return False
    return bool(
        re.search(r"\b(?:other|different|common|main|available)\s+types?\b", lowered)
        or re.search(r"\btypes?\s+of\b", lowered)
        or re.search(r"\bwhat\s+are\b.*\btypes?\b", lowered)
        or re.search(r"\b(?:difference|compare|comparison|vs\.?|versus)\b", lowered)
        or re.search(r"\b(?:explain|overview|describe)\b.*\b(?:systems?|types?|options?)\b", lowered)
        or re.search(
            r"\bwhat\s+(?:is|does)\s+(?:ftx|fmed|sj[äa]lvdrag|district\s+heating|fj[äa]rrv[äa]rme|"
            r"atemp|energy\s+class|energy\s+performance|specific\s+energy\s+use|primary\s+energy\s+number)\b",
            lowered,
        )
        or re.search(
            r"\bwhat\s+does\s+(?:ftx|fmed|sj[äa]lvdrag|district\s+heating|fj[äa]rrv[äa]rme|"
            r"atemp|energy\s+class|energy\s+performance|specific\s+energy\s+use|primary\s+energy\s+number)\s+"
            r"(?:mean|stand\s+for)\b",
            lowered,
        )
    )


def _looks_like_generic_building_concept_followup(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip() or _looks_like_explicit_general_scope(lowered):
        return False
    return bool(
        re.search(r"\bwhat\s+does\s+(?:that|this|it)\s+mean\b", lowered)
        or re.search(r"\b(?:explain|describe)\s+(?:that|this|it|more)\b", lowered)
        or re.search(r"\bis\s+(?:that|this|it)\s+(?:good|bad|normal|efficient|high|low)\b", lowered)
        or re.search(r"\bhow\s+(?:good|bad|efficient|normal)\s+(?:is\s+)?(?:that|this|it)\b", lowered)
        or re.search(r"\bhow\s+does\s+(?:that|this|it)\s+compare\b", lowered)
    )


def _looks_like_fact_concept_hybrid_request(text: Optional[str]) -> bool:
    return _looks_like_fact_concept_overview_request(text) or _looks_like_generic_building_concept_followup(text)


def _build_effective_fact_concept_query(message: str, metadata: Dict[str, Any], prior_state: Dict[str, Any]) -> str:
    facts = extract_retrieved_facts(
        metadata,
        prior_state,
        (metadata or {}).get("retrieved_facts"),
        (prior_state or {}).get("aggregated_data"),
        (prior_state or {}).get("agent_data"),
    )
    terms = [str(message or "").strip()]
    for key, label in (
        ("ventilation_type", "current ventilation type"),
        ("heating_system", "current heating system"),
        ("energy_class", "current energy class"),
        ("energy_performance", "current energy performance"),
        ("specific_energy_use", "current specific energy use"),
        ("primary_energy_number", "current primary energy number"),
        ("electricity_use", "current electricity use"),
    ):
        value = facts.get(key)
        if _fact_present(value):
            terms.append(f"{label}: {value}")
    if re.search(r"\b(?:ventilation|ventiallation|ventillation|ftx|fmed)\b", str(message or "").lower()):
        terms.append("Swedish apartment building ventilation types F FT FTX self-draught heat recovery")
    if re.search(r"\b(?:heating|district\s+heating|fj[aä]rrv[aä]rme)\b", str(message or "").lower()):
        terms.append("Swedish apartment building heating systems district heating heat pump direct electric")
    if re.search(r"\b(?:energy\s+class|energy\s+performance|energy\s+index|specific\s+energy|primary\s+energy)\b", str(message or "").lower()):
        terms.append("Swedish energy declaration metrics energy class A to G energy performance primary energy number specific energy use")
    return " ".join(part for part in terms if part)


def _requested_fact_specs(user_input: Optional[str]) -> List[Tuple[str, str, Optional[str], Optional[str]]]:
    lowered = str(user_input or "").lower()
    if not lowered.strip():
        return []
    if _looks_like_fact_concept_overview_request(lowered):
        return []

    specs: List[Tuple[str, str, Optional[str], Optional[str]]] = []
    for key, label, patterns, unit, preface in DIRECT_FACT_SPECS:
        if any(re.search(pattern, lowered) for pattern in patterns):
            specs.append((key, label, unit, preface))

    if re.search(r"\bdistrict\s+heating\b", lowered) and re.search(
        r"\b(?:breakdown|split|space\s+heating|hot\s+water)\b",
        lowered,
    ):
        for extra in (
            ("district_heating_space_heating", "district heating for space heating", "kWh/year", None),
            (
                "district_heating_domestic_hot_water",
                "district heating for domestic hot water",
                "kWh/year",
                None,
            ),
        ):
            if not any(key == extra[0] for key, *_ in specs):
                specs.append(extra)

    if (
        re.search(r"\b(?:area|atemp|heated\s+area)\b", lowered)
        and not any(key == "epc_egenatemp" for key, *_ in specs)
    ):
        specs.append(("epc_egenatemp", "Atemp/heated area", "m2", None))

    return specs


def _response_looks_like_full_profile(text: Optional[str]) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    return len(PROFILE_FACT_LABEL_RE.findall(text)) >= 5


def _targeted_building_fact_response(
    *,
    user_input: str,
    current_address: str,
    building_id: str,
    facts: Dict[str, Any],
) -> Optional[str]:
    specs = _requested_fact_specs(user_input)
    if not specs:
        return None

    lines: List[str] = []
    if building_id and building_id != "building_id_not_available":
        lines.append(f"Building ID: {building_id}")

    fact_lines: List[str] = []
    explanations: List[str] = []
    for key, label, unit, preface in specs:
        value = facts.get(key)
        if value in (None, "", [], {}):
            value = _first_fact_value(facts, (key,))
        if value in (None, "", [], {}):
            continue
        if preface and preface not in explanations:
            explanations.append(preface)
        fact_lines.append(f"Your building's {label} is {_format_fact_value_for_user(key, value, unit)}.")
        explanation = _plain_language_fact_explanation(key, value)
        if explanation and explanation not in explanations:
            explanations.append(explanation)

    if not fact_lines:
        return None

    if lines:
        lines.append("")
    lines.extend(fact_lines[:3])
    if explanations:
        lines.extend(explanations[:3])
    return "\n".join(lines)


def _deterministic_building_fact_response(
    *,
    user_input: str = "",
    current_address: str,
    building_id: str,
    facts: Dict[str, Any],
) -> str:
    targeted = _targeted_building_fact_response(
        user_input=user_input,
        current_address=current_address,
        building_id=building_id,
        facts=facts,
    )
    if targeted:
        return targeted

    lines: List[str] = []
    if current_address:
        lines.append(str(current_address))
    if building_id and building_id != "building_id_not_available":
        lines.append(f"Building ID: {building_id}")

    fact_lines: List[str] = []
    for key, label, unit in (
        ("energy_class", "Energy class", None),
        ("energy_performance", "Declared energy performance", "kWh/m2-year"),
        ("specific_energy_use", "Specific energy use", "kWh/m2-year"),
        ("primary_energy_number", "Primary energy number", "kWh/m2-year"),
        ("energy_declaration_year", "Energy declaration year", None),
        ("construction_year", "Construction year", None),
        ("heating_system", "Heating system", None),
        ("ventilation_type", "Ventilation", None),
        ("electricity_use", "Electricity consumption", "kWh/year"),
        ("district_heating_use", "Total district heating use", "kWh/year"),
        ("district_heating_space_heating", "District heating for space heating", "kWh/year"),
        (
            "district_heating_domestic_hot_water",
            "District heating for domestic hot water",
            "kWh/year",
        ),
    ):
        value = facts.get(key)
        if value not in (None, "", [], {}):
            fact_lines.append(f"{label}: {_format_fact_value(value, unit)}")

    if fact_lines:
        if lines:
            lines.append("")
        lines.extend(fact_lines)
        return "\n".join(lines)

    if lines:
        lines.append("")
    lines.append(
        "I found the building record, but the response model is temporarily busy. "
        "Please retry shortly if you need a fuller explanation."
    )
    return "\n".join(lines)


def _fact_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _first_fact_value(facts: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    lower_map = {str(key).lower(): key for key in (facts or {}).keys()}
    for key in keys:
        original = lower_map.get(key.lower())
        if original is not None and _fact_present((facts or {}).get(original)):
            return facts.get(original)
    return None


def _normalize_frontend_building_metadata(
    *,
    metadata: Dict[str, Any],
    retrieved_facts: Dict[str, Any],
    current_address: str,
    building_id: str,
    identity_check: Dict[str, Any],
    epc_record_address: Optional[str],
) -> Dict[str, Any]:
    normalized = dict(metadata or {})
    facts = dict(retrieved_facts or {})
    usable_building_id = (
        str(building_id).strip()
        if building_id and str(building_id).strip() != "building_id_not_available"
        else None
    )
    requested_address = (
        current_address
        or normalized.get("requested_address")
        or normalized.get("address_from_user")
        or normalized.get("address")
        or _first_fact_value(facts, ("address", "official_address", "epc_idadr"))
        or (identity_check or {}).get("matched_address")
        or ""
    )
    matched_address = (
        (identity_check or {}).get("matched_address")
        or epc_record_address
        or _first_fact_value(facts, ("address", "official_address", "epc_idadr"))
        or requested_address
    )
    matched_building_id = (
        (identity_check or {}).get("matched_building_id")
        or normalized.get("byggnadsid")
        or normalized.get("building_id")
        or usable_building_id
    )

    if usable_building_id:
        normalized.setdefault("byggnadsid", usable_building_id)
        facts.setdefault("byggnadsid", usable_building_id)
        facts.setdefault("building_id", usable_building_id)
    if requested_address:
        normalized.setdefault("address", requested_address)
        normalized.setdefault("address_from_user", requested_address)
        normalized.setdefault("requested_address", requested_address)
        facts.setdefault("address", requested_address)
        facts.setdefault("address_from_user", requested_address)
    if epc_record_address:
        normalized.setdefault("epc_record_address", epc_record_address)
        facts.setdefault("epc_idadr", epc_record_address)

    existing_match = normalized.get("building_match") if isinstance(normalized.get("building_match"), dict) else {}
    status = str((identity_check or {}).get("status") or "").lower()
    normalized["building_match"] = _deep_merge(
        existing_match,
        {
            "input_address": requested_address or existing_match.get("input_address"),
            "matched_address": matched_address or existing_match.get("matched_address"),
            "building_id": matched_building_id or existing_match.get("building_id"),
            "match_confidence": "high" if status == "passed" else existing_match.get("match_confidence", "medium"),
            "ambiguous": bool((identity_check or {}).get("ambiguous", False)),
        },
    )
    normalized["retrieved_facts"] = facts
    return normalized


def _building_fact_summary_for_ecm(facts: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key, label, unit in (
        ("energy_class", "energy class", None),
        ("energy_performance", "declared energy performance", "kWh/m2-year"),
        ("specific_energy_use", "specific energy use", "kWh/m2-year"),
        ("primary_energy_number", "primary energy number", "kWh/m2-year"),
        ("energy_declaration_year", "energy declaration year", None),
        ("construction_year", "construction year", None),
        ("heating_system", "heating system", None),
        ("ventilation_type", "ventilation", None),
    ):
        value = (facts or {}).get(key)
        if _fact_present(value):
            parts.append(f"{label}: {_format_fact_value(value, unit)}")
    return "; ".join(parts)


def _short_building_fact_summary_for_advice(facts: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key, label, unit in (
        ("energy_class", "energy class", None),
        ("energy_performance", "energy performance", "kWh/m2-year"),
        ("heating_system", "heating", None),
        ("ventilation_type", "ventilation", None),
    ):
        value = (facts or {}).get(key)
        if _fact_present(value):
            parts.append(f"{label}: {_format_fact_value(value, unit)}")
    return "; ".join(parts[:4])


def _looks_like_ventilation_focused_request(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    return bool(re.search(r"\b(?:ventilation|ftx|airflow|air\s+flow|fan|filters?)\b", lowered))


def _response_has_ecm_recommendations(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    if not lowered.strip():
        return False
    required_sections = (
        "energy conservation",
        "energy efficiency",
        "energy management",
        "renewable",
    )
    if not all(section in lowered for section in required_sections):
        return False
    action_terms = (
        "check",
        "adjust",
        "optimize",
        "optimise",
        "balance",
        "monitor",
        "measure",
        "commission",
        "setpoint",
        "heating curve",
        "domestic hot water",
        "ventilation",
        "solar",
    )
    return sum(1 for term in action_terms if term in lowered) >= 3


def _deterministic_ecm_response(
    *,
    user_input: str = "",
    current_address: str,
    building_id: str,
    facts: Dict[str, Any],
) -> str:
    lines: List[str] = []
    if building_id and building_id != "building_id_not_available":
        lines.append(f"Building ID: {building_id}")
    if current_address:
        lines.append(f"Address: {current_address}")

    summary = _short_building_fact_summary_for_advice(facts)
    if summary:
        lines.extend(["", f"Based on the known facts: {summary}."])

    heating = str((facts or {}).get("heating_system") or "").lower()
    ventilation = str((facts or {}).get("ventilation_type") or "").lower()
    has_district_heating = "district heating" in heating
    has_ftx = "ftx" in ventilation
    ventilation_explanation = _ventilation_explanation((facts or {}).get("ventilation_type"))

    if _looks_like_ventilation_focused_request(user_input):
        if ventilation_explanation:
            lines.extend(["", ventilation_explanation])
        lines.extend(
            [
                "",
                "Energy Conservation Measures (ECMs)",
                "",
                "1. Energy conservation / reduce demand and waste",
                "- Check whether airflow or operating hours are higher than needed while still meeting indoor-air requirements.",
                "",
                "2. Energy efficiency / improve equipment and building systems",
                (
                    "- Commission the FTX system: clean/replace filters, verify airflow balance, heat-recovery function, bypass settings, and fan operation."
                    if has_ftx
                    else "- Verify ventilation airflows, fan operation, filters, and pressure settings; avoid over-ventilation while maintaining indoor air quality."
                ),
                "",
                "3. Energy management measures / controls, monitoring, and routines",
                "- Track fan electricity, complaints, filter pressure, and heat-recovery/temperature trends so faults are caught early.",
                "",
                "4. Renewable energy / add supply after demand is reduced",
                "- Renewables are not usually the first ventilation measure; consider solar PV only after ventilation demand, controls, and fan efficiency are optimized.",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "",
            "Energy Conservation Measures (ECMs)",
            "",
            "1. Energy conservation / reduce demand and waste",
            "- Map indoor temperatures, overheating, open-window behavior, and domestic-hot-water waste before adding more heat.",
            "",
            "2. Energy efficiency / improve equipment and building systems",
            (
                "- Review the district-heating substation, heat exchangers, pumps, and radiator valves; tune the heating curve and return temperatures."
                if has_district_heating
                else "- Review the main heating plant, pumps, valves, and heat distribution so delivered heat matches actual demand."
            ),
            "",
            "3. Energy management measures / controls, monitoring, and routines",
            "- Track heat, hot water, electricity, indoor temperatures, and complaints against outdoor temperature; use this for seasonal tuning.",
            "",
            "4. Renewable energy / add supply after demand is reduced",
            "- Assess solar PV after low-cost demand, efficiency, and control measures are understood; check roof area, shading, structure, grid connection, and self-consumption.",
            "",
            "Good first step: run a 2-4 week diagnostic, then prioritize measures with measured evidence.",
        ]
    )
    return "\n".join(lines)


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


def _row_identity_key(row: Dict[str, Any], fallback_index: int = 0) -> str:
    identifier = _extract_row_identifier(row)
    if identifier:
        return f"id:{str(identifier).strip().upper()}"

    address = _normalize_address(_extract_row_address(row)) or ""
    location_values = [
        normalized
        for normalized in (_normalize_location(value) for value in _row_location_values(row))
        if normalized
    ]
    if address or location_values:
        return f"addr:{address}|loc:{'|'.join(sorted(set(location_values)))}"

    return f"row:{fallback_index}"


def _select_latest_epc_rows_preserving_ambiguity(rows: Any) -> Any:
    if not isinstance(rows, list) or len(rows) <= 1:
        return rows

    groups: Dict[str, List[Dict[str, Any]]] = {}
    passthrough: List[Any] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            passthrough.append(row)
            continue
        groups.setdefault(_row_identity_key(row, index), []).append(row)

    if len(groups) <= 1 and not passthrough:
        return _select_latest_epc_rows(rows)

    selected_rows: List[Any] = []
    for grouped_rows in groups.values():
        selected = _select_latest_epc_rows(grouped_rows)
        if isinstance(selected, list):
            selected_rows.extend(selected)
        else:
            selected_rows.append(selected)
    selected_rows.extend(passthrough)
    return selected_rows


def _enrich_building_fact_aliases(row: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, dict):
        return row
    enriched = dict(row)
    facts = extract_retrieved_facts(row)
    for key in (
        "heating_system",
        "district_heating_use",
        "district_heating_space_heating",
        "district_heating_domestic_hot_water",
        "energy_class",
        "energy_performance",
        "specific_energy_use",
        "primary_energy_number",
        "domestic_hot_water",
        "energy_declaration_year",
        "construction_year",
    ):
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


def _extract_metadata_location_hint(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    metadata = metadata or {}
    for key in ADDRESS_LOCATION_HINT_KEYS:
        value = metadata.get(key)
        if value not in (None, "", [], {}):
            return str(value).strip()
    return None


def _row_values_for_keys(row: Dict[str, Any], keys: Tuple[str, ...]) -> List[str]:
    if not isinstance(row, dict):
        return []

    lower_map = {str(key).lower(): key for key in row.keys()}
    values: List[str] = []
    for key in keys:
        original = lower_map.get(key.lower())
        if original is None:
            continue
        value = row.get(original)
        if value not in (None, ""):
            values.append(str(value).strip())
    return values


def _row_location_values(row: Dict[str, Any]) -> List[str]:
    return _row_values_for_keys(row, ROW_LOCATION_KEYS)


def _row_matches_location_values(
    row: Dict[str, Any],
    location_hint: Optional[str],
    keys: Tuple[str, ...],
    *,
    exact: bool,
) -> bool:
    normalized_hint = _normalize_location(location_hint)
    if not normalized_hint:
        return False

    hint_digits = _compact_digits(location_hint)
    for value in _row_values_for_keys(row, keys):
        if keys == ROW_POSTCODE_KEYS:
            value_digits = _compact_digits(value)
            if value_digits and hint_digits and len(value_digits) >= 5 and value_digits in hint_digits:
                return True
            continue

        normalized_value = _normalize_location(value)
        if not normalized_value:
            continue
        if exact and normalized_value == normalized_hint:
            return True
        if not exact and (
            normalized_value == normalized_hint
            or normalized_value in normalized_hint
            or normalized_hint in normalized_value
        ):
            return True

    return False


def _location_hint_matches_row(location_hint: Optional[str], row: Dict[str, Any]) -> bool:
    normalized_hint = _normalize_location(location_hint)
    if not normalized_hint:
        return False

    hint_digits = _compact_digits(location_hint)
    for value in _row_location_values(row):
        normalized_value = _normalize_location(value)
        if normalized_value and (
            normalized_value == normalized_hint
            or normalized_value in normalized_hint
            or normalized_hint in normalized_value
        ):
            return True

        value_digits = _compact_digits(value)
        if value_digits and hint_digits and len(value_digits) >= 5 and value_digits in hint_digits:
            return True

    return False


def _filter_rows_by_location_hint(rows: Any, location_hint: Optional[str]) -> Any:
    if not location_hint or not isinstance(rows, list):
        return rows

    tiers = (
        (ROW_POSTCODE_KEYS, True),
        (ROW_POST_TOWN_KEYS, True),
        (ROW_MUNICIPALITY_KEYS, True),
        (ROW_POST_TOWN_KEYS, False),
        (ROW_MUNICIPALITY_KEYS, False),
    )
    for keys, exact in tiers:
        matched = [
            row
            for row in rows
            if _row_matches_location_values(row, location_hint, keys, exact=exact)
        ]
        if matched:
            return matched

    return []


def _rows_conflict_with_location_hint(rows: Any, location_hint: Optional[str]) -> bool:
    if not location_hint:
        return False

    candidate_rows = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
    rows_with_location = [
        row
        for row in candidate_rows
        if isinstance(row, dict) and _row_location_values(row)
    ]
    if not rows_with_location:
        return False

    return not any(_location_hint_matches_row(location_hint, row) for row in rows_with_location)


def _expand_rows_to_same_building_ids(
    seed_rows: Any,
    all_rows: List[Dict[str, Any]],
    location_hint: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(seed_rows, list) or not seed_rows:
        return seed_rows if isinstance(seed_rows, list) else []

    building_ids = {
        str(identifier).strip().upper()
        for identifier in (_extract_row_identifier(row) for row in seed_rows if isinstance(row, dict))
        if identifier not in (None, "")
    }
    if not building_ids:
        return seed_rows

    expanded = [
        row
        for row in all_rows
        if isinstance(row, dict)
        and str(_extract_row_identifier(row) or "").strip().upper() in building_ids
    ]
    if location_hint:
        expanded = _filter_rows_by_location_hint(expanded, location_hint)
    return expanded or seed_rows


def _narrow_address_matches(
    address: Optional[str],
    rows: Any,
    location_hint: Optional[str] = None,
) -> Any:
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
    candidate_rows = exact_matches or deduped_rows
    candidate_rows = _filter_rows_by_location_hint(candidate_rows, location_hint)
    candidate_rows = _expand_rows_to_same_building_ids(candidate_rows, deduped_rows, location_hint)
    return _select_latest_epc_rows_preserving_ambiguity(candidate_rows)


def _building_id_matches(row: Dict[str, Any], building_id: Optional[str]) -> bool:
    if not building_id or not isinstance(row, dict):
        return False
    row_id = _extract_row_value_case_insensitive(row, ("byggnadsid", "building_id"))
    return str(row_id or "").strip().upper() == str(building_id).strip().upper()


def _explicit_building_id_from_metadata(metadata: Dict[str, Any]) -> Optional[str]:
    metadata = metadata or {}
    for key in ("building_id_from_user", "selected_brf_building_id"):
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value).strip().upper()

    resolution = metadata.get("brf_resolution")
    if isinstance(resolution, dict) and resolution.get("status") in RESOLVED_BRF_STATUSES:
        value = resolution.get("selected_building_id")
        if value not in (None, ""):
            return str(value).strip().upper()

    if metadata.get("same_building_multiple_addresses"):
        value = metadata.get("byggnadsid") or metadata.get("building_id")
        if value not in (None, "") and BUILDING_ID_RE.search(str(value)):
            return str(value).strip().upper()

    # Treat raw byggnadsid/building_id metadata as system memory, not a fresh
    # user selection. If an address/location hint is active, the address path
    # must re-check the registry location instead of trusting a stale ID.
    has_address_context = bool(
        metadata.get("address")
        or metadata.get("address_from_user")
        or _extract_metadata_location_hint(metadata)
    )
    if not has_address_context:
        for key in ("byggnadsid", "building_id"):
            value = metadata.get(key)
            if value not in (None, "") and BUILDING_ID_RE.search(str(value)):
                return str(value).strip().upper()

    return None


def _all_retrieved_rows_match_building_id(data: Any, building_id: Optional[str]) -> bool:
    if not building_id:
        return False

    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    checked_rows = [row for row in rows if isinstance(row, dict)]
    return bool(checked_rows) and all(_building_id_matches(row, building_id) for row in checked_rows)


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
        narrowed = _narrow_address_matches(
            address,
            result.get("data"),
            _extract_metadata_location_hint(metadata),
        )
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


def _metadata_brf_name(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    metadata = metadata or {}
    value = metadata.get("brf_name")
    if value not in (None, ""):
        cleaned = _clean_brf_name(str(value).strip())
        if cleaned:
            return cleaned

    for key in ("brf_resolution", "pending_brf_resolution"):
        block = metadata.get(key)
        if isinstance(block, dict) and block.get("brf_name") not in (None, ""):
            cleaned = _clean_brf_name(str(block.get("brf_name")).strip())
            if cleaned:
                return cleaned

    return None


def _brf_context_rows(metadata: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    metadata = metadata or {}
    rows: List[Dict[str, Any]] = []

    option_sets = []
    if isinstance(metadata.get("brf_candidate_buildings"), list):
        option_sets.append(metadata.get("brf_candidate_buildings") or [])
    pending = metadata.get("pending_brf_resolution")
    if isinstance(pending, dict) and isinstance(pending.get("options"), list):
        option_sets.append(pending.get("options") or [])

    for options in option_sets:
        for option in options:
            if not isinstance(option, dict):
                continue
            for address_label in option.get("addresses") or []:
                address = _address_without_postcode(address_label)
                if address:
                    rows.append(
                        {
                            "byggnadsid": option.get("byggnadsid"),
                            "brf_name": option.get("brf_name"),
                            "orgnr": option.get("orgnr"),
                            "fastighet": option.get("fastighet"),
                            "address": address,
                        }
                    )

    brf_name = _metadata_brf_name(metadata)
    if brf_name:
        try:
            rows.extend(_lookup_brf_addresses(brf_name))
        except Exception:
            pass

    return [row for row in rows if isinstance(row, dict)]


def _address_norms(value: Optional[str]) -> set:
    bare = _address_without_postcode(str(value or ""))
    norms = {_normalize_address(bare)}
    folded = _ascii_fold(bare)
    if folded and folded != bare:
        norms.add(_normalize_address(folded))
    return {norm for norm in norms if norm}


def _resolve_address_candidate_from_brf_context(
    address: str,
    metadata: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    target_norms = _address_norms(address)
    if not target_norms:
        return None

    matched_rows: List[Dict[str, Any]] = []
    for row in _brf_context_rows(metadata):
        row_label = _format_brf_address(row) or row.get("address")
        row_address = _address_without_postcode(str(row_label or ""))
        if not row_address:
            continue
        if target_norms.intersection(_address_norms(row_address)):
            matched = dict(row)
            matched["address"] = row_address
            matched_rows.append(matched)

    if not matched_rows:
        return None

    building_ids = [
        identifier
        for identifier in (_extract_row_identifier(row) for row in matched_rows)
        if identifier
    ]
    unique_building_ids = list(dict.fromkeys(building_ids))
    matched_address = next((row.get("address") for row in matched_rows if row.get("address")), None)

    return {
        "address": address,
        "ok": bool(unique_building_ids),
        "building_id": unique_building_ids[0] if len(unique_building_ids) == 1 else None,
        "building_ids": unique_building_ids,
        "matched_address": matched_address,
        "rows": matched_rows,
        "message": None,
    }


def _resolve_single_address_candidate(address: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    sql_client = getattr(sql_mapper_layer, "sql", None)
    previous_timeout = getattr(sql_client, "timeout", None)
    try:
        fast_timeout = int(os.getenv("ODEN_MULTI_ADDRESS_TIMEOUT", "10"))
    except (TypeError, ValueError):
        fast_timeout = 10

    try:
        if sql_client is not None and previous_timeout and fast_timeout > 0:
            sql_client.timeout = min(previous_timeout, fast_timeout)
        result = sql_mapper_layer.execute("building_by_address", address=address) or {}
        if not (result.get("ok") and result.get("data")):
            folded = _ascii_fold(address)
            if folded and folded != address:
                fallback = sql_mapper_layer.execute("building_by_address", address=folded) or {}
                if fallback.get("ok") and fallback.get("data"):
                    result = fallback
        if not (result.get("ok") and result.get("data")):
            fallback = sql_mapper_layer.execute("buildings_by_single_filter", field="epc_idadr", value=address) or {}
            if fallback.get("ok") and fallback.get("data"):
                result = fallback
    except Exception as exc:
        brf_result = _resolve_address_candidate_from_brf_context(address, metadata)
        if brf_result:
            return brf_result
        return {
            "address": address,
            "ok": False,
            "building_id": None,
            "building_ids": [],
            "matched_address": None,
            "rows": [],
            "message": str(exc),
        }
    finally:
        if sql_client is not None and previous_timeout is not None:
            sql_client.timeout = previous_timeout

    rows = result.get("data") if result.get("ok") else []
    if not rows:
        brf_result = _resolve_address_candidate_from_brf_context(address, metadata)
        if brf_result:
            return brf_result

    if isinstance(rows, list):
        rows = _select_latest_epc_rows(
            _narrow_address_matches(
                address,
                rows,
                _extract_metadata_location_hint(metadata),
            )
        )
    rows = _enrich_building_data(rows)
    row_list = rows if isinstance(rows, list) else [rows] if isinstance(rows, dict) else []
    building_ids = [
        identifier
        for identifier in (_extract_row_identifier(row) for row in row_list)
        if identifier
    ]
    unique_building_ids = list(dict.fromkeys(building_ids))
    matched_address = next(
        (matched for matched in (_extract_row_address(row) for row in row_list) if matched),
        None,
    )

    return {
        "address": address,
        "ok": bool(row_list),
        "building_id": unique_building_ids[0] if len(unique_building_ids) == 1 else None,
        "building_ids": unique_building_ids,
        "matched_address": matched_address,
        "rows": row_list,
        "message": result.get("message"),
    }


def _format_multi_address_candidate_line(candidate: Dict[str, Any]) -> str:
    address = candidate.get("address") or "Unknown address"
    building_id = candidate.get("building_id")
    if building_id:
        return f"- {address}: Building ID {building_id}"
    if candidate.get("building_ids"):
        return f"- {address}: multiple building IDs ({', '.join(candidate['building_ids'])})"
    message = str(candidate.get("message") or "").lower()
    if "timeout" in message or "timed out" in message:
        return f"- {address}: lookup timed out before a building record could be confirmed"
    return f"- {address}: no building record found"


def _build_multi_address_resolution_response(
    candidates: List[Dict[str, Any]],
    status: str,
    shared_building_id: Optional[str] = None,
) -> str:
    addresses = [str(candidate.get("address") or "").strip() for candidate in candidates if candidate.get("address")]
    first_address = addresses[0] if addresses else "the first address"
    alternate_addresses = [address for address in addresses[1:] if address]
    address_summary = " and ".join(addresses[:2]) if len(addresses) == 2 else ", ".join(addresses)

    if status == "same_building" and shared_building_id:
        alternate_text = (
            f" I will treat {', '.join(alternate_addresses)} as alternate address(es) for the same building."
            if alternate_addresses
            else ""
        )
        return (
            f"Building ID: {shared_building_id}\n\n"
            f"{address_summary} resolve to the same building record, so I can use either address. "
            f"I use the building ID as the source of truth and will use the latest available EPC/building record for that building."
            f"{alternate_text} You can override this by giving a specific address or building ID."
        )

    lines = [_format_multi_address_candidate_line(candidate) for candidate in candidates]
    if status == "different_buildings":
        return (
            "I found different building IDs for those addresses, so I should not choose automatically.\n\n"
            + "\n".join(lines)
            + "\n\nWhich building ID should I use? You can reply with the building ID, option number, or one of the listed addresses."
        )

    if status == "partial_match":
        return (
            "I could only resolve some of those addresses. I use the building ID as the source of truth, so please confirm which building ID to use.\n\n"
            + "\n".join(lines)
        )

    timed_out = any(
        "timeout" in str(candidate.get("message") or "").lower()
        or "timed out" in str(candidate.get("message") or "").lower()
        for candidate in candidates
    )
    if timed_out:
        return (
            "I could not finish the address lookup before ODEN responded. Please try again, or provide the BRF name, organization number, or exact building ID.\n\n"
            + "\n".join(lines)
        )

    return (
        "I could not resolve those addresses to a building record. Please provide the city/postcode, BRF name, organization number, or exact building ID.\n\n"
        + "\n".join(lines)
    )


def resolve_multi_address_identity_message(
    last_message: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    md = dict(metadata or {})
    candidates = _extract_address_candidates_from_text(last_message)
    if len(candidates) < 2:
        return None

    if not _looks_like_multi_address_identity_question(last_message, candidates):
        return None

    resolved = [_resolve_single_address_candidate(address, md) for address in candidates]
    matched = [candidate for candidate in resolved if candidate.get("ok")]
    matched_ids = [
        candidate.get("building_id")
        for candidate in matched
        if candidate.get("building_id")
    ]
    unique_ids = list(dict.fromkeys(matched_ids))

    if len(matched) == len(resolved) and len(unique_ids) == 1:
        status = "same_building"
        shared_building_id = unique_ids[0]
    elif matched and len(unique_ids) > 1:
        status = "different_buildings"
        shared_building_id = None
    elif matched:
        status = "partial_match"
        shared_building_id = unique_ids[0] if len(unique_ids) == 1 else None
    else:
        status = "no_match"
        shared_building_id = None

    response = _build_multi_address_resolution_response(
        resolved,
        status,
        shared_building_id=shared_building_id,
    )
    metadata_updates = {
        "address_candidates": candidates,
        "multi_address_resolution": {
            "status": status,
            "candidates": [
                {
                    "address": candidate.get("address"),
                    "matched_address": candidate.get("matched_address"),
                    "building_id": candidate.get("building_id"),
                    "building_ids": candidate.get("building_ids"),
                    "ok": candidate.get("ok"),
                    "message": candidate.get("message"),
                }
                for candidate in resolved
            ],
        },
    }

    if status == "same_building" and shared_building_id:
        metadata_updates.update(
            {
                "address": candidates[0],
                "address_from_user": candidates[0],
                "requested_address": candidates[0],
                "alternate_addresses": candidates[1:],
                "byggnadsid": shared_building_id,
                "same_building_multiple_addresses": True,
                "building_identity_check": {
                    "status": "passed",
                    "matched_building_id": shared_building_id,
                    "matched_address": candidates[0],
                    "ambiguous": False,
                    "multiple_matches": False,
                    "candidate_building_ids": [shared_building_id],
                    "candidate_addresses": candidates,
                    "multiple_addresses_same_explicit_building": True,
                },
                "clarification": {
                    "needed": False,
                    "reason": None,
                    "question_asked": None,
                    "resolved": True,
                    "resolved_after_turns": 0,
                },
            }
        )
    elif status in {"different_buildings", "partial_match", "no_match"}:
        metadata_updates["clarification"] = {
            "needed": True,
            "reason": f"multi_address_{status}",
            "question_asked": response,
            "resolved": False,
            "resolved_after_turns": None,
        }

    return {
        "final_response": response,
        "metadata": _deep_merge(md, metadata_updates),
        "multi_address_resolution_complete": True,
    }

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
    if not _extract_stored_address_from_metadata(merged_metadata):
        pending_address = _extract_pending_ambiguous_address_from_messages(state.get("messages") or [])
        if pending_address:
            merged_metadata = _deep_merge(
                merged_metadata,
                {
                    "address": pending_address,
                    "address_from_user": pending_address,
                    PENDING_AMBIGUOUS_ADDRESS_KEY: pending_address,
                    "clarification": {
                        "needed": True,
                        "reason": "ambiguous_address",
                        "resolved": False,
                    },
                    "building_identity_check": {
                        "status": "ambiguous",
                    },
                },
            )
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

    current_message_is_advice = _looks_like_building_ecm_advice(state.get("last_message"))
    current_message_is_fact_concept_overview = _looks_like_fact_concept_hybrid_request(state.get("last_message"))

    if _looks_like_personal_energy_advice(state.get("last_message")):
        normalized_intents = [str(item).strip().lower() for item in (ctx.get("intent_list") or [])]
        if not normalized_intents or normalized_intents == ["vector database"]:
            ctx["intent_list"] = ["SQL database", "vector database"]
            ctx["parsed_intent"] = "SQL database ; vector database"
            ctx["ambiguous"] = False
            ctx["ambigious"] = False
            print("[understand_context] promoted personal energy advice to SQL + vector intent", flush=True)

    if current_message_is_advice:
        _promote_to_building_advice_intent(ctx, state.get("last_message") or "")
        ctx["effective_query"] = _build_effective_ecm_query(
            state.get("last_message") or "",
            state.get("metadata") or {},
            prior_state,
        )
        ctx["answer_focus"] = state.get("last_message") or ""
        ctx["advice_type"] = "ecm"
        if not _has_building_context_for_advice(state.get("metadata") or {}, prior_state):
            md = dict(state.get("metadata") or {})
            md[PENDING_BUILDING_ADVICE_KEY] = _pending_advice_context(
                state.get("last_message") or "",
                ctx,
            )
            state["metadata"] = md
        if _has_building_context_for_advice(state.get("metadata") or {}, prior_state):
            print("[understand_context] promoted ECM advice follow-up to SQL + vector intent", flush=True)
        else:
            print("[understand_context] ECM advice needs building context; SQL gate will request address", flush=True)

    if current_message_is_fact_concept_overview:
        if _has_building_context_for_advice(state.get("metadata") or {}, prior_state):
            ctx["intent_list"] = ["SQL database", "vector database"]
            ctx["parsed_intent"] = "SQL database ; vector database"
            ctx["effective_query"] = _build_effective_fact_concept_query(
                state.get("last_message") or "",
                state.get("metadata") or {},
                prior_state,
            )
            ctx["answer_focus"] = state.get("last_message") or ""
            ctx["ambiguous"] = False
            ctx["ambigious"] = False
            print("[understand_context] promoted fact concept overview to SQL + vector intent", flush=True)
        else:
            normalized_intents = [str(item).strip().lower() for item in (ctx.get("intent_list") or [])]
            if not normalized_intents or normalized_intents == ["sql database"]:
                ctx["intent_list"] = ["vector database"]
                ctx["parsed_intent"] = "vector database"
                ctx["ambiguous"] = False
                ctx["ambigious"] = False
            print("[understand_context] fact concept overview stays vector-only without building context", flush=True)

    address_location_hint = _extract_metadata_location_hint(ctx)

    # If the parser missed an address on an address-only follow-up, recover it heuristically.
    if ctx.get("address"):
        raw_parser_address = ctx.get("address")
        cleaned_address = _extract_address_candidate_from_text(raw_parser_address)
        if cleaned_address:
            address_location_hint = (
                address_location_hint
                or _extract_address_location_hint_from_text(raw_parser_address, cleaned_address)
                or _extract_address_location_hint_from_text(state.get("last_message"), cleaned_address)
            )
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
            address_location_hint = (
                address_location_hint
                or _extract_address_location_hint_from_text(state.get("last_message"), recovered_address)
            )
            print(f"[understand_context] recovered address from raw message: {recovered_address!r}", flush=True)

    stored_address_for_disambiguation = (
        _extract_stored_address_from_metadata(state.get("metadata") or {})
        or _extract_stored_address_from_metadata(prior_metadata or {})
    )
    if (
        not ctx.get("address")
        and not address_location_hint
        and stored_address_for_disambiguation
        and (
            _awaiting_address_location_disambiguation(state.get("metadata") or {})
            or _awaiting_address_location_disambiguation(prior_metadata or {})
        )
    ):
        location_only_hint = _extract_location_only_hint_from_text(state.get("last_message"))
        if location_only_hint:
            address_location_hint = location_only_hint
            ctx["address"] = stored_address_for_disambiguation
            prior_intent_list = _coerce_intent_list(prior_context or {}) or ["SQL database"]
            ctx["intent_list"] = copy.deepcopy(prior_intent_list)
            ctx["parsed_intent"] = (prior_context or {}).get("parsed_intent") or " ; ".join(prior_intent_list)
            ctx["ambiguous"] = False
            ctx["ambigious"] = False
            print(
                "[understand_context] resolved ambiguous address with location hint: "
                f"{location_only_hint!r}",
                flush=True,
            )

    pending_advice = (state.get("metadata") or {}).get(PENDING_BUILDING_ADVICE_KEY)
    if (
        isinstance(pending_advice, dict)
        and _looks_like_identity_followup(state.get("last_message"), ctx)
    ):
        ctx = _restore_pending_advice_context(ctx, pending_advice)
        print("[understand_context] restored pending ECM advice request after identity follow-up", flush=True)

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
        prior_location_hint = (
            _extract_metadata_location_hint(md)
            or _extract_metadata_location_hint(prior_metadata or {})
        )
        if not address_location_hint:
            address_location_hint = _extract_address_location_hint_from_text(state.get("last_message"), addr)
        if (
            not address_location_hint
            and stored_address
            and _normalize_address(addr) == _normalize_address(stored_address)
        ):
            address_location_hint = prior_location_hint
        md["address"] = addr
        md["address_from_user"] = addr
        if address_location_hint:
            md["address_location_hint"] = address_location_hint
            ctx["address_location_hint"] = address_location_hint
            print(f"[understand_context] address location hint set: {address_location_hint!r}", flush=True)
        elif _normalize_address(addr) != _normalize_address(stored_address):
            md.pop("address_location_hint", None)
            ctx.pop("address_location_hint", None)
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
    current_brf_name = _extract_brf_name_from_text(last_message)

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
            explicit_reply_building_id = _extract_building_id_from_text(last_message)
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
            if explicit_reply_building_id:
                md["building_id_from_user"] = explicit_reply_building_id
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

        if _looks_like_new_question_away_from_brf_resolution(last_message):
            md.pop("pending_brf_resolution", None)
            resolution = md.get("brf_resolution")
            if isinstance(resolution, dict) and resolution.get("status") == "needs_user_selection":
                md.pop("brf_resolution", None)
                md.pop("brf_candidate_buildings", None)
                md.pop("brf_name", None)
            clarification = md.get("clarification")
            if isinstance(clarification, dict) and clarification.get("reason") == "ambiguous_brf":
                md.pop("clarification", None)
            print("[brf_resolution] cleared pending BRF selection after topic change", flush=True)
            return {"metadata": md}

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

    if _has_resolved_brf_selection(md) and not current_brf_name:
        print("[brf_resolution] BRF selection already resolved", flush=True)
        return {}

    failed_brf_lookup = _metadata_has_failed_brf_lookup(md)
    should_reuse_brf_context = bool(
        current_brf_name
        or _looks_like_identity_followup(last_message, ctx)
        or (not failed_brf_lookup and _looks_like_building_ecm_advice(last_message))
    )
    brf_name = current_brf_name
    if not brf_name and should_reuse_brf_context:
        brf_name = _metadata_brf_name(md) or _extract_recent_brf_name_from_messages(state.get("messages"))

    if not brf_name:
        if failed_brf_lookup:
            cleaned_md = _clear_failed_brf_lookup_state(md)
            if cleaned_md != md:
                print("[brf_resolution] cleared failed BRF lookup state", flush=True)
                return {"metadata": cleaned_md}
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
                "original_question": ctx.get("answer_focus") or last_message,
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


def multi_address_resolution_node(state: GraphState) -> GraphState:
    print("[multi_address_resolution] ENTER", flush=True)
    result = resolve_multi_address_identity_message(
        state.get("last_message") or "",
        state.get("metadata") or {},
    )
    if not result:
        print("[multi_address_resolution] no multi-address identity question", flush=True)
        return {}

    status = ((result.get("metadata") or {}).get("multi_address_resolution") or {}).get("status")
    print(f"[multi_address_resolution] status={status}", flush=True)
    return result


def route_after_multi_address_resolution(state: GraphState) -> str:
    if state.get("multi_address_resolution_complete"):
        print("[route_after_multi_address_resolution] → direct_response", flush=True)
        return "direct_response"
    return route_after_brf_resolution(state)


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
    explicit_building_id = _explicit_building_id_from_metadata(md)
    if explicit_building_id:
        addr = None
    print(f"[generic_sql_agent] ENTER addr={addr!r}", flush=True)
    result = None

    if explicit_building_id:
        print(f"[generic_sql_agent] selected byggnadsid={explicit_building_id!r}", flush=True)
        result = sql_mapper_layer.execute("buildings_by_building_id", building_id=explicit_building_id)
        if not (result.get("ok") and result.get("data")):
            fallback = _lookup_rows_for_brf_selected_addresses(explicit_building_id, md)
            if fallback:
                print("[generic_sql_agent] used BRF selected-address fallback for byggnadsid", flush=True)
                result = fallback
                addr = None
            elif md.get("address"):
                print("[generic_sql_agent] fallback to address lookup after byggnadsid miss", flush=True)
                addr = md.get("address")
                result = None
        else:
            addr = None

    if result is None and addr:
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
            location_hint = _extract_metadata_location_hint(md)
            narrowed_rows = _narrow_address_matches(addr, original_rows, location_hint)
            result["data"] = narrowed_rows
            trace = result.get("trace") or {}
            trace["rows_returned"] = len(narrowed_rows or [])
            trace["returned_values_used"] = (
                narrowed_rows[0]
                if isinstance(narrowed_rows, list)
                and narrowed_rows
                and isinstance(narrowed_rows[0], dict)
                else {}
            )
            location_hint_no_match = bool(location_hint and original_rows and not narrowed_rows)
            trace["match_strategy"] = (
                "address_location_no_match"
                if location_hint_no_match
                else
                "exact_address_location"
                if location_hint and narrowed_rows and len(narrowed_rows) < len(original_rows)
                else "exact_address"
                if narrowed_rows and len(narrowed_rows) < len(original_rows)
                else "address_lookup"
            )
            if location_hint:
                trace["address_location_hint"] = location_hint
            if location_hint_no_match:
                trace["location_hint_no_match"] = True
            result["trace"] = trace
    elif result is None:
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
            latest_rows = _select_latest_epc_rows_preserving_ambiguity(result.get("data"))
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
        else:
            trace["returned_values_used"] = {}
        result["trace"] = trace

    if result and result.get("ok"):
        location_hint_for_identity = _extract_metadata_location_hint(md)
        if _rows_conflict_with_location_hint(result.get("data"), location_hint_for_identity):
            trace = result.get("trace") or {}
            trace["address_location_hint"] = location_hint_for_identity
            trace["location_hint_no_match"] = True
            trace["match_strategy"] = "address_location_conflict"
            trace["rows_returned"] = 0
            result["trace"] = trace
            result["data"] = []

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
    if addr and not explicit_building_id:
        metadata_updates.pop("byggnadsid", None)
        metadata_updates.pop("building_id", None)

    if result and result.get("ok"):
        identity_check = assess_building_identity(metadata_updates, result.get("data"))
        if (trace or {}).get("location_hint_no_match"):
            identity_check = _deep_merge(
                identity_check,
                {
                    "status": "ambiguous",
                    "matched_building_id": None,
                    "ambiguous": True,
                    "multiple_matches": True,
                    "location_hint_no_match": True,
                    "location_hint": (trace or {}).get("address_location_hint"),
                },
            )
        if (
            explicit_building_id
            and _all_retrieved_rows_match_building_id(result.get("data"), explicit_building_id)
        ):
            identity_check = _deep_merge(
                identity_check,
                {
                    "status": "passed",
                    "matched_building_id": explicit_building_id,
                    "ambiguous": False,
                    "multiple_matches": False,
                    "explicit_building_id_accepted": True,
                },
            )
        if (
            identity_check.get("status") == "ambiguous"
            and addr
            and _is_specific_address(addr)
            and isinstance(result.get("data"), list)
            and result.get("data")
            and len(set(identity_check.get("candidate_building_ids") or [])) <= 1
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
            metadata_updates.pop(PENDING_AMBIGUOUS_ADDRESS_KEY, None)
            outs.append("generic_sql: ok")
            try:
                n = len(result.get("data") or [])
            except Exception:
                n = "?"
            print(f"[generic_sql_agent] SUCCESS rows={n}", flush=True)
        else:
            clarification_reason = (
                "ambiguous_address"
                if identity_check.get("ambiguous") or (trace or {}).get("location_hint_no_match")
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
                    PENDING_AMBIGUOUS_ADDRESS_KEY: addr,
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
    current_address = (
        current_address
        or _first_fact_value(retrieved_facts, ("address", "official_address", "epc_idadr"))
        or identity_check.get("matched_address")
        or ""
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
    metadata = _normalize_frontend_building_metadata(
        metadata=metadata,
        retrieved_facts=retrieved_facts,
        current_address=str(current_address),
        building_id=str(building_id),
        identity_check=identity_check,
        epc_record_address=epc_record_address,
    )
    retrieved_facts = metadata.get("retrieved_facts") or retrieved_facts
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
    user_input_for_response = ctx.get("answer_focus") or state.get("last_message") or ""
    ecm_request = _looks_like_building_ecm_advice(user_input_for_response)
    prompt = build_building_response_prompt(
        user_input=user_input_for_response,
        current_address=str(current_address),
        history=state.get("messages") or [],
        action_description=action_description,
        results=state.get("aggregated_data") or state.get("aggregated") or {},
        metadata=metadata,
        building_id=building_id,
    )
    try:
        answer = llm_summarizer.generate_response(prompt, message_list=[])
        if _is_model_error_response(answer):
            metadata = _deep_merge(metadata, {"response_model_error": answer})
            answer = _deterministic_building_fact_response(
                user_input=user_input_for_response,
                current_address=str(current_address),
                building_id=str(building_id),
                facts=retrieved_facts,
            )
            print("[llm_summarizer] model error; used deterministic building-fact fallback", flush=True)
        targeted_fact_answer = _targeted_building_fact_response(
            user_input=user_input_for_response,
            current_address=str(current_address),
            building_id=str(building_id),
            facts=retrieved_facts,
        )
        if targeted_fact_answer and not ecm_request:
            fallback_reason = (
                "direct_fact_response_was_too_broad"
                if _response_looks_like_full_profile(answer)
                else "direct_fact_response_targeted"
            )
            metadata = _deep_merge(
                metadata,
                {
                    "response_fallback": {
                        "reason": fallback_reason,
                    }
                },
            )
            answer = targeted_fact_answer
            print("[llm_summarizer] used targeted direct-fact response", flush=True)
        if ecm_request and not _response_has_ecm_recommendations(answer):
            metadata = _deep_merge(
                metadata,
                {
                    "response_fallback": {
                        "reason": "ecm_response_missing_recommendations",
                        "hierarchy": [
                            "energy_conservation",
                            "energy_efficiency",
                            "energy_management_measures",
                            "renewable_energy",
                        ],
                    }
                },
            )
            answer = _deterministic_ecm_response(
                user_input=user_input_for_response,
                current_address=str(current_address),
                building_id=str(building_id),
                facts=retrieved_facts,
            )
            print("[llm_summarizer] used deterministic ECM hierarchy fallback", flush=True)
        answer = ensure_building_identifier_in_response(answer, building_id)
        answer = apply_response_safety_notes(answer, metadata, user_message=user_input_for_response)
        print("[llm_summarizer] generate_response ✓", flush=True)
    except Exception as e:
        answer = "Sorry, summarizer error."
        print(f"[llm_summarizer] ERROR: {e}", flush=True)

    sess = _latest_session_state(state.get("session_state"))
    if not (metadata.get("clarification") or {}).get("needed"):
        metadata.pop(PENDING_BUILDING_ADVICE_KEY, None)
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
    builder.add_node("multi_address_resolution", multi_address_resolution_node)
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
    builder.add_edge("brf_resolution", "multi_address_resolution")

    # After parsing and optional BRF lookup: ambiguity + address/building-id gate
    builder.add_conditional_edges(
        "multi_address_resolution",
        route_after_multi_address_resolution,
        {
            "direct_response": END,
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
