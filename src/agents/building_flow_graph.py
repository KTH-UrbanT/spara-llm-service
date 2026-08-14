# src/agents/building_flow_graph.py
from __future__ import annotations
import logging
import re
import os
from typing import Any, Dict, List, Tuple, Optional
import copy
import unicodedata

# --- LangGraph
from langgraph.graph import StateGraph, START, END

# --- Project deps
from src.agents.parse_intent_agent import ParseIntentAgent
from src.agents.generic_sql_layer import SQL_Mapper_Layer
from src.agents.building_response_prompt import merge_identifier_metadata
from src.database.vector_client import VectorClient, VectorClientConfig
from src.agents.openai_agent import OpenAIResponseAgent
from src.agents.evaluator_agent import EvaluatorAgent
from src.agents.specialized_sql_layer import SpecializedSQLLayer
from src.database.hammarby_data import query_address
from src.evaluation import append_evaluation_trace, build_evaluation_trace, truncate_for_trace
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


logger = logging.getLogger(__name__)

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

# LLM evaluator (Azure OpenAI)
evaluator_agent = EvaluatorAgent()
print("[init] EvaluatorAgent initialized ✓", flush=True)

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

    cue_match = re.search(
        r"(?:i\s+live\s+(?:in|at|on)|i\s+am\s+at|i'm\s+at|my\s+address\s+is|address\s+is|"
        r"we\s+live\s+(?:in|at|on)|our\s+address\s+is|"
        r"jag\s+bor\s+p[åa]|vi\s+bor\s+p[åa]|min\s+adress\s+[äa]r|adressen\s+[äa]r)\s+(.+)$",
        candidate,
        flags=re.IGNORECASE,
    )
    if cue_match:
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

    if re.search(r"\d", candidate) and re.search(r"[A-Za-zÅÄÖåäö]", candidate):
        return candidate

    return None


def _iter_metadata_address_candidates(metadata: Dict[str, Any]) -> List[Any]:
    if not isinstance(metadata, dict):
        return []

    candidates: List[Any] = []
    for key in ("address", "address_from_user", "matched_address", "input_address", "official_address"):
        if metadata.get(key) not in (None, ""):
            candidates.append(metadata.get(key))

    for block_key in ("building_match", "retrieved_facts"):
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
    if promoted.get("address") and promoted.get("address_from_user"):
        return promoted
    stored_address = _extract_stored_address_from_metadata(promoted)
    if stored_address:
        promoted.setdefault("address", stored_address)
        promoted.setdefault("address_from_user", stored_address)
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


def _extract_row_identifier(row: Dict[str, Any]) -> Optional[str]:
    if not isinstance(row, dict):
        return None
    for key in ("byggnadsid", "building_id", "50a_uuid", "uuid", "oden_uuid", "01a_fnr"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


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
        fingerprint = (identifier or "", normalized_address or "", tuple(sorted(row.keys())))
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
    return exact_matches or deduped_rows

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

# Evaluator-related state keys (plain dict keys, no TypedDict schema needed):
# - eval_verdict: Optional[str] -> "pass" | "fail"
# - eval_feedback: Optional[str] -> corrective feedback for summarizer retries
# - eval_retry_count: int -> number of completed retry loops (0 = first attempt,
#   1 = after one retry). Counts summarizer re-runs that followed a failed
#   evaluation — NOT the number of evaluator failures themselves.
# - eval_scores: Optional[dict] -> score bundle + gating metadata


def _ensure_evaluator_state_defaults(state: GraphState) -> Dict[str, Any]:
    """Return a dict of evaluator state keys that are missing or None in `state`.

    LangGraph merges the returned dict into state using `operator.or_`, which means
    any key we return will OVERWRITE the existing value. We therefore only include
    keys that are genuinely absent or uninitialized — never returning a key that
    already has a meaningful value. This is called at node entry to guarantee that
    downstream code can always read these keys safely without None-checks.
    """
    updates: Dict[str, Any] = {}
    if "eval_retry_count" not in state or state.get("eval_retry_count") is None:
        updates["eval_retry_count"] = 0
    if "eval_verdict" not in state:
        updates["eval_verdict"] = None
    if "eval_feedback" not in state:
        updates["eval_feedback"] = None
    if "eval_scores" not in state:
        updates["eval_scores"] = None
    return updates


def _build_trace_identifiers(state: GraphState) -> Dict[str, Any]:
    """Extract experiment identifier fields for the trace.

    These identifiers are how the analysis script joins traces across the four arms.
    They are sourced from:
      - question_id: state.metadata["question_id"] — set by the offline runner per question.
      - dataset_version: state.metadata["dataset_version"] — set by the runner from the gold dataset header.
      - run_id: EXPERIMENT_RUN_ID env var — set by the runner once per process.
      - arm: EXPERIMENT_ARM env var — set by the runner once per process.
      - attempt_index: equals the current eval_retry_count counter at trace-write
        time (0 for first evaluation, 1 for the post-retry evaluation).
    Missing values are returned as None so the analysis script can detect non-experimental traces.
    """
    md = state.get("metadata") or {}
    if not isinstance(md, dict):
        md = {}
    retry_count = int(state.get("eval_retry_count") or 0)
    return {
        "question_id": md.get("question_id"),
        "dataset_version": md.get("dataset_version"),
        "run_id": os.getenv("EXPERIMENT_RUN_ID"),
        "arm": os.getenv("EXPERIMENT_ARM"),
        "attempt_index": retry_count,
    }


def _get_evaluator_prompt_version() -> str:
    """Return the filename of the evaluator prompt that was actually loaded.

    This is recorded in every trace record so that analysis can group runs by
    prompt version and detect whether two arms used different rubric wordings.
    Using the basename of the resolved file path (rather than the env var value)
    is more reliable because it reflects what the agent actually opened on disk,
    even if the env var was set to an absolute path. Falls back to the env var
    or the default filename when the evaluator is mocked in tests and has no
    real `prompt_path` attribute.
    """
    prompt_path = getattr(evaluator_agent, "prompt_path", None)
    if isinstance(prompt_path, str) and prompt_path:
        return os.path.basename(prompt_path)
    return os.getenv("EVALUATOR_PROMPT_VERSION", "evaluator_prompt.txt")


def _get_evaluator_deployment_label() -> Optional[str]:
    """Return the evaluator's Azure deployment name as a plain string for the trace.

    The isinstance guard prevents test MagicMock objects (where any attribute access
    returns another MagicMock) from leaking into the trace and causing JSON
    serialization failures. Returns None when the evaluator is mocked or uninitialized.
    """
    deployment = getattr(evaluator_agent, "deployment", None)
    if isinstance(deployment, str) and deployment:
        return deployment
    return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _get_evaluator_mode() -> str:
    """Return evaluator mode from env with compatibility fallbacks.

    Modes:
    - strict: precision-first thresholds
    - balanced: moderate thresholds (default)
    - off: full evaluator bypass
    """
    if not _env_bool("EVALUATOR_ENABLED", True):
        return "off"

    mode = str(os.getenv("EVALUATOR_MODE", "balanced") or "balanced").strip().lower()
    if mode not in {"strict", "balanced", "off"}:
        mode = "balanced"
    return mode


def _is_evaluator_bypassed() -> bool:
    return _get_evaluator_mode() == "off"


def _coerce_score(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        score = int(round(float(value)))
    except Exception:
        return None
    if score < 0:
        return 0
    if score > 10:
        return 10
    return score


def _derive_hard_fail_from_issues(issues: List[str]) -> bool:
    if not issues:
        return False
    critical_tokens = (
        "critical hallucination",
        "critical unsupported claim",
        "numeric contradiction",
        "unmet explicit constraint",
    )
    for issue in issues:
        text = str(issue).lower()
        if any(token in text for token in critical_tokens):
            return True
    return False


def _evaluation_profile(mode: str, legacy_threshold: int) -> Dict[str, float]:
    """Return threshold profile for verdict gating.

    Legacy threshold is preserved as a floor for main per-axis thresholds.
    """
    profile = {
        "strict": {
            "groundedness_min": 9,
            "numeric_fidelity_min": 9,
            "constraint_satisfaction_min": 8,
            "completeness_min": 8,
            "composite_min": 8.7,
            "retry_lower": 7.5,
        },
        "balanced": {
            "groundedness_min": 7,
            "numeric_fidelity_min": 7,
            "constraint_satisfaction_min": 7,
            "completeness_min": 7,
            "composite_min": 7.2,
            "retry_lower": 6.0,
        },
        "off": {
            "groundedness_min": 0,
            "numeric_fidelity_min": 0,
            "constraint_satisfaction_min": 0,
            "completeness_min": 0,
            "composite_min": 0,
            "retry_lower": 0,
        },
    }.get(mode, {})

    floor = max(0, min(10, int(legacy_threshold)))
    if profile:
        profile["groundedness_min"] = max(profile["groundedness_min"], floor)
        profile["numeric_fidelity_min"] = max(profile["numeric_fidelity_min"], floor)
        profile["constraint_satisfaction_min"] = max(profile["constraint_satisfaction_min"], floor)
        profile["completeness_min"] = max(profile["completeness_min"], floor)
    return profile


def _compute_composite_score(
    groundedness: Optional[int],
    completeness: Optional[int],
    numeric_fidelity: Optional[int],
    constraint_satisfaction: Optional[int],
    uncertainty_calibration: Optional[int],
) -> Optional[float]:
    vals = [groundedness, completeness, numeric_fidelity, constraint_satisfaction, uncertainty_calibration]
    if any(v is None for v in vals):
        return None
    composite = (
        0.35 * float(groundedness)
        + 0.25 * float(numeric_fidelity)
        + 0.20 * float(constraint_satisfaction)
        + 0.15 * float(completeness)
        + 0.05 * float(uncertainty_calibration)
    )
    return round(composite, 2)


def _is_retry_candidate(eval_scores: Dict[str, Any]) -> bool:
    if not isinstance(eval_scores, dict):
        return False
    if bool(eval_scores.get("hard_fail")):
        return False

    explicit = eval_scores.get("retry_candidate")
    if isinstance(explicit, bool):
        return explicit

    composite = eval_scores.get("composite_score")
    if composite is None:
        return True

    mode = _get_evaluator_mode()
    profile = _evaluation_profile(mode, legacy_threshold=int(os.getenv("EVALUATOR_PASS_THRESHOLD", "6")))
    try:
        comp = float(composite)
    except Exception:
        return True
    return profile.get("retry_lower", 0.0) <= comp < profile.get("composite_min", 10.0)

# ================================
# Context understanding (UPDATED)
# ================================

def _latest_session_state(session_state: Any) -> Dict[str, Any]:
    if isinstance(session_state, dict):
        return dict(session_state)
    if isinstance(session_state, list):
        for item in reversed(session_state):
            if isinstance(item, dict):
                return dict(item)
    return {}


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

    # Parse on a SAFE subset to avoid in-place mutation of the live state.
    # A router rewind (closed loop) puts its correction in `router_hint`. The hint has to
    # reach the intent parser — re-parsing the identical question would re-pick the
    # identical route and the rewind would be a silent no-op — but it must NOT reach
    # `state["last_message"]`, which the judges and the summariser read as the user's
    # question. So it is appended to the parser's input only.
    _msg = state.get("last_message")
    _router_hint = state.get("router_hint")
    _had_hint = bool(_router_hint)
    # The route the evaluator just rejected, set by rewind_to_router_node. Consumed in the
    # same pass so a stale key can never suppress the promoter on a later, unrelated turn.
    _rejected_route = state.get("rejected_route")
    state["rejected_route"] = None
    if _router_hint:
        _msg = f"{_msg}\n\n{_router_hint}"
        state["router_hint"] = None  # consumed; never re-apply on a later pass
        print("[understand_context] re-parsing with router hint", flush=True)
    parse_input = {
        "last_message": _msg,
        "messages": state.get("messages", []),
    }
    parsed = parse_intent_agent(copy.deepcopy(parse_input))  # defensive copy
    print(f"[understand_context] parsed keys={list(parsed.keys())}", flush=True)

    # Adopt parser context verbatim (no _safe_ctx_from_parsed, no normalization, no canonicalization)
    ctx = copy.deepcopy(parsed.get("context") or {})
    print(f"[understand_context] ctx keys before ensure={list(ctx.keys())}", flush=True)

    # Ensure required intent keys exist (no transformations)
    if "parsed_intent" not in ctx:
        ctx["parsed_intent"] = None
    if "intent_list" not in ctx or ctx["intent_list"] is None:
        ctx["intent_list"] = []

    # This heuristic reads `last_message`, which by design never carries the router hint, so
    # it cannot tell that a rewind just happened — it re-promoted the very route the early
    # checkpoint had rejected, silently discarding a correction the parser had already
    # obeyed (v21 §1.3: EKR_GEN_019 fired 3/3 and converted 0/3 for exactly this reason).
    # Suppress it only in that situation; normal traffic keeps the heuristic untouched.
    _suppress_promoter = _had_hint and _rejected_route == "building"
    if _suppress_promoter:
        print("[understand_context] promoter suppressed: evaluator rejected route 'building'", flush=True)
    elif _had_hint and _rejected_route:
        # Direction matters. Only the building->generic rejection is observed and tested;
        # an unobserved reverse path is exactly how the §1.7 landmine got planted.
        print(f"[understand_context] forced_route_skipped: rejected_route={_rejected_route!r}", flush=True)
    if not _suppress_promoter and _looks_like_personal_energy_advice(state.get("last_message")):
        normalized_intents = [str(item).strip().lower() for item in (ctx.get("intent_list") or [])]
        if not normalized_intents or normalized_intents == ["vector database"]:
            ctx["intent_list"] = ["SQL database", "vector database"]
            ctx["parsed_intent"] = "SQL database ; vector database"
            ctx["ambiguous"] = False
            ctx["ambigious"] = False
            print("[understand_context] promoted personal energy advice to SQL + vector intent", flush=True)

    # If the parser missed an address on an address-only follow-up, recover it heuristically.
    if ctx.get("address"):
        cleaned_address = _extract_address_candidate_from_text(ctx.get("address"))
        if cleaned_address and cleaned_address != ctx.get("address"):
            ctx["address"] = cleaned_address
            print(f"[understand_context] cleaned parsed address to: {cleaned_address!r}", flush=True)

    if not ctx.get("address") and _last_assistant_requested_address(state.get("messages", [])):
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
        (state.get("metadata") or {}).get("address")
        or (state.get("metadata") or {}).get("address_from_user")
        or (prior_metadata or {}).get("address")
        or (prior_metadata or {}).get("address_from_user")
        or _extract_stored_address_from_metadata(state.get("metadata") or {})
        or _extract_stored_address_from_metadata(prior_metadata or {})
    )

    # Metadata: if address present in parsed input, set both fields
    print(ctx)
    addr = ctx.get("address")
    if addr:
        md = state.get("metadata") or {}
        # Shallow copy to avoid mutating any external references
        md = dict(md)
        md["address"] = addr
        md["address_from_user"] = addr
        state["metadata"] = md  # write back only when modified
        print(f"[understand_context] address set in metadata: {addr!r}", flush=True)

    # Write context back to state (in place) and return state
    state["context"] = ctx
    # The top-level route, derived from the parsed intent, for the early checkpoint.
    _intents = [str(i).lower() for i in (ctx.get("intent_list") or [])]
    _parsed = str(ctx.get("parsed_intent") or "").lower()
    _wants_sql = any(i in {"sql database", "specialized sql database",
                           "query generic database", "query specific database"} for i in _intents) \
                 or (not _intents and "sql" in _parsed)
    state["top_route"] = "building" if _wants_sql else "generic"

    # Safety net. If the parser itself re-picks the rejected route, suppressing the promoter
    # is not enough and the rewind would be wasted (anti-thrash blocks a second one). Flip
    # onto the ORGANIC generic path — the vector-only bypass at route_after_ambiguity — not
    # a new one. The traces say the parser already obeys the hint, so this should never fire:
    # `forced_route_applied` staying false while EKR_GEN_019 converts is the proof that the
    # promoter was the sole cause (v21 §2.2).
    if _suppress_promoter and state["top_route"] == _rejected_route:
        ctx["intent_list"] = ["vector database"]
        ctx["parsed_intent"] = "vector database"
        ctx["ambiguous"] = False
        ctx["ambigious"] = False
        state["top_route"] = "generic"
        state["forced_route_applied"] = True
        print("[understand_context] forced_route_applied: parser re-picked the rejected route", flush=True)

    print(f"[understand_context] parsed_intent={ctx.get('parsed_intent')!r} intent_list={ctx.get('intent_list')}", flush=True)
    print("[understand_context] EXIT", flush=True)
    return state

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
    # When the runner pre-populates state["aggregated_data"] from a previous run's
    # cache, it also sets aggregated_data_cached=True to signal that upstream agents
    # must not re-query. Skipping the SQL query here serves two purposes: it avoids
    # redundant cost, and it ensures all arms in a comparative run see byte-identical
    # evidence. We still mark done so wait_for_replies sees the completion signal;
    # the aggregator preserves the cached data via deep-merge with empty new data.
    if state.get("aggregated_data_cached"):
        print("[generic_sql_agent] skipped: aggregated_data is cached", flush=True)
        return {"done_generic_sql": True}

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
            result = sql_mapper_layer.execute("buildings_by_single_filter", field=key, value=value)

    if result and result.get("ok"):
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
            outs.append("generic_sql: no address/supported single-filter provided.")
        print("[generic_sql_agent] NO DATA", flush=True)
    updates["metadata"] = metadata_updates
    updates["agent_outputs_generic"] = outs
    # Closed-loop instrumentation: per-agent keys (parallel fan-out merges with
    # operator.or_, which overwrites — so the harness unions these afterwards).
    _g_data = (result or {}).get("data") if result else None
    if isinstance(_g_data, list) and _g_data and isinstance(_g_data[0], dict):
        _g_fields = list(_g_data[0].keys())
    elif isinstance(_g_data, dict):
        _g_fields = list(_g_data.keys())
    else:
        _g_fields = []
    updates["invoked_generic_sql"] = True
    updates["sql_fields_generic"] = _g_fields
    print("[generic_sql_agent] EXIT", flush=True)
    return updates

def specialized_sql_agent_node(state: GraphState) -> GraphState:
    # Skip when aggregated_data was pre-loaded from cache — same rationale as
    # generic_sql_agent_node: avoid re-querying and ensure all arms see the same evidence.
    if state.get("aggregated_data_cached"):
        print("[specialized_sql_agent] skipped: aggregated_data is cached", flush=True)
        return {"done_specialized_sql": True}

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
        updates["invoked_specialized_sql"] = True
        updates["sql_fields_specialized"] = list(trace.get("fields_used") or [])
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
    # Skip when aggregated_data was pre-loaded from cache — same rationale as
    # generic_sql_agent_node: avoid re-querying and ensure all arms see the same evidence.
    if state.get("aggregated_data_cached"):
        print("[vector_db_agent] skipped: aggregated_data is cached", flush=True)
        return {"done_vector": True}

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
        updates["invoked_vector"] = True
        updates["vector_chunks_retrieved"] = [
            {"score": h.get("score"),
             "source": (h.get("metadata") or {}).get("source"),
             "page_content_preview": str(h.get("page_content", ""))[:200]}
            for h in (hits or []) if isinstance(h, dict)
        ]
        print(f"[vector_db_agent] hits={count}", flush=True)
        if hits:
            content_from_doc = ' '.join(i['page_content'] for i in hits)
            outs.append("Vector hits:\n" + content_from_doc )
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
    prompt = 'Question : ' + (state.get("last_message") or "") + 'Context : ' + str(state.get('aggregated_data', {}))

    # If evaluator rejected a prior answer, pass corrective guidance to the summarizer.
    eval_feedback = state.get("eval_feedback")
    eval_retry_count = int(state.get("eval_retry_count") or 0)
    if eval_feedback:
        prompt += (
            "\n\nPrevious answer was rejected by evaluator. "
            "Fix these issues in your new answer: "
            + str(eval_feedback)
        )
        # Count retries when we actually perform a corrective summarization pass.
        # eval_retry_count is the number of completed retry loops, NOT the number
        # of evaluator failures. Incrementing here (not in the evaluator node)
        # ensures the counter reflects summarizer re-runs, not eval invocations.
        eval_retry_count += 1

    metadata = state.get("metadata") or {}
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
    metadata = _deep_merge(
        metadata,
        {
            "building_identity_check": identity_check,
            "retrieved_facts": retrieved_facts,
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

    try:
        answer = llm_summarizer.generate_response(prompt, message_list=(state.get('messages') or []))
        answer = apply_response_safety_notes(answer, metadata)
        print("[llm_summarizer] generate_response ✓", flush=True)
    except Exception as e:
        answer = "Sorry, summarizer error."
        print(f"[llm_summarizer] ERROR: {e}", flush=True)

    # Capture per-call latency + token usage from the side-channel meta the summarizer
    # stores on itself after each generate_response call. The isinstance check guards
    # against MagicMock instances in tests where every attribute access returns yet
    # another MagicMock, which would later fail JSON serialization in the trace writer.
    summarizer_meta = getattr(llm_summarizer, "_last_call_meta", None)
    if not isinstance(summarizer_meta, dict):
        summarizer_meta = {}
    summarizer_latency_ms = summarizer_meta.get("latency_ms")
    summarizer_token_usage = summarizer_meta.get("token_usage")
    # Read temperature provenance from the side-channel: temperature_requested is
    # the float we asked for, temperature_unsupported is True when the deployment
    # rejected it and we fell back to the model default. Both fields are written into
    # the trace so any downstream reader can tell whether this run was actually
    # deterministic. Non-numeric and non-bool values (including MagicMock in tests)
    # are coerced to None / False below so they can be JSON-serialized safely.
    summarizer_temperature_requested = summarizer_meta.get("temperature_requested")
    summarizer_temperature_unsupported = bool(summarizer_meta.get("temperature_unsupported"))
    if not isinstance(summarizer_latency_ms, (int, float)):
        summarizer_latency_ms = None
    if not isinstance(summarizer_token_usage, dict):
        summarizer_token_usage = None
    if not isinstance(summarizer_temperature_requested, (int, float)):
        summarizer_temperature_requested = None

    sess = {**(state.get("session_state") or {})}
    sess["metadata"] = metadata
    sess["context"] = ctx
    if ctx.get("intent"):
        sess["last_intent"] = ctx.get("intent")
    if ctx.get("intent_list") is not None:
        sess["last_intent_list"] = ctx.get("intent_list")

    out = (state.get("step_out") or []) + ["llm_summarizer ✓"]
    mode = _get_evaluator_mode()
    bypassed = mode == "off"
    md = state.get("metadata") or {}
    debug = (md.get("debug") or {}) if isinstance(md, dict) else {}
    debug = _deep_merge(debug, {
        "evaluator_mode": mode,
        "evaluator_bypassed": bypassed,
    })

    print("[llm_summarizer] EXIT", flush=True)
    updates = {
        "final_response": answer,
        "session_state": sess,
        "step_out": out,
        "eval_retry_count": eval_retry_count,
        "summarizer_latency_ms": summarizer_latency_ms,
        "summarizer_token_usage": summarizer_token_usage,
        # Temperature provenance is written to state so the trace node can include it.
        "summarizer_temperature_requested": summarizer_temperature_requested,
        "summarizer_temperature_unsupported": summarizer_temperature_unsupported,
        "metadata": _deep_merge(metadata, {"debug": debug}),
    }
    # Closed-loop cost accounting: the open arm fires no checkpoint, so its token
    # total must come from the summarizer. Accumulate across retry re-runs.
    _su = summarizer_token_usage if isinstance(summarizer_token_usage, dict) else {}
    _su_total = int(_su.get("total_tokens") or _su.get("total") or 0)
    updates["eval_total_tokens"] = int(state.get("eval_total_tokens") or 0) + _su_total
    updates["eval_total_completions"] = int(state.get("eval_total_completions") or 0) + 1
    updates.update(_ensure_evaluator_state_defaults(state))
    return updates


def evaluate_response_node(state: GraphState) -> GraphState:
    """Evaluate summarizer output and store structured verdict and retry metadata.

    When EVALUATOR_MODE=off, the production graph NEVER enters this node —
    `route_after_summarizer` routes summarizer → END directly (see
    `build_building_flow_graph`). This means the no-evaluator arm incurs *zero*
    evaluator overhead in real runs (no extra LLM call, no extra wall-clock time,
    no trace record from this node).

    The bypass branch below is a defensive fallback that fires only when this
    function is called *directly* (e.g., in unit tests that bypass the graph
    router). The offline batch runner writes synthetic bypassed-arm trace records
    itself rather than relying on this branch, so trace structure is consistent
    across all arms.
    """
    print("[evaluate_response] ENTER", flush=True)

    question = state.get("last_message") or ""
    aggregated_data = state.get("aggregated_data") or {}
    answer = state.get("final_response") or ""

    if _is_evaluator_bypassed():
        # Defensive: only reached on direct calls (tests). Production graph routes
        # around this node entirely when mode=off.
        print("[evaluate_response] BYPASSED (mode=off)", flush=True)
        md = state.get("metadata") or {}
        debug = (md.get("debug") or {}) if isinstance(md, dict) else {}
        debug = _deep_merge(debug, {
            "evaluator_mode": "off",
            "evaluator_bypassed": True,
        })
        print("[evaluate_response] EXIT", flush=True)
        updates = {
            **_ensure_evaluator_state_defaults(state),
            "eval_verdict": "pass",
            "eval_feedback": None,
            "eval_scores": {
                "bypassed": True,
                "evaluated": False,
                "evaluation_status": "bypassed",
            },
            "metadata": _deep_merge(md, {"debug": debug}),
        }
        trace = build_evaluation_trace(
            **_build_trace_identifiers(state),
            question=question,
            answer=answer,
            # Snapshot the SQL/vector evidence so annotators can verify groundedness
            # against the same context the (skipped) evaluator would have seen.
            aggregated_data=truncate_for_trace(aggregated_data),
            mode="off",
            verdict="pass",
            evaluated=False,
            evaluation_status="bypassed",
            eval_retry_count=int(state.get("eval_retry_count") or 0),
            eval_scores=updates["eval_scores"],
            model_deployment=_get_evaluator_deployment_label(),
            prompt_version=_get_evaluator_prompt_version(),
            # Bypass mode never invokes the evaluator, so its latency is 0 and
            # tokens are None. Summarizer fields are read from state as usual.
            summarizer_latency_ms=state.get("summarizer_latency_ms"),
            summarizer_token_usage=state.get("summarizer_token_usage"),
            evaluator_latency_ms=0,
            evaluator_token_usage=None,
            summarizer_temperature_requested=state.get("summarizer_temperature_requested"),
            summarizer_temperature_unsupported=bool(state.get("summarizer_temperature_unsupported")),
        )
        try:
            append_evaluation_trace(trace)
        except Exception as trace_error:
            logger.warning("Failed to append evaluator bypass trace: %s", trace_error)
        return updates
    mode = _get_evaluator_mode()

    # Ensure evaluator keys are always available.
    base_updates = _ensure_evaluator_state_defaults(state)
    retry_count = int(state.get("eval_retry_count") or 0)

    result = evaluator_agent.evaluate(
        question=question,
        aggregated_data=aggregated_data,
        answer=answer,
    )
    evaluator_failed = bool(result.get("evaluator_failed"))
    evaluator_failure_reason = str(result.get("evaluator_failure_reason") or "").strip()

    # Latency and token usage are attached to the result dict by EvaluatorAgent.
    # Same defensive coercion as in llm_summarizer_node (guards against MagicMock
    # values in tests that would otherwise break trace JSON serialization).
    evaluator_latency_ms = result.get("_latency_ms")
    evaluator_token_usage = result.get("_token_usage")
    if not isinstance(evaluator_latency_ms, (int, float)):
        evaluator_latency_ms = None
    if not isinstance(evaluator_token_usage, dict):
        evaluator_token_usage = None
    summarizer_latency_ms = state.get("summarizer_latency_ms")
    summarizer_token_usage = state.get("summarizer_token_usage")
    if not isinstance(summarizer_latency_ms, (int, float)):
        summarizer_latency_ms = None
    if not isinstance(summarizer_token_usage, dict):
        summarizer_token_usage = None

    legacy_threshold = int(os.getenv("EVALUATOR_PASS_THRESHOLD", "6"))
    profile = _evaluation_profile(mode, legacy_threshold)

    faithfulness = _coerce_score(result.get("faithfulness_score"))
    groundedness = _coerce_score(result.get("groundedness_score"))
    completeness = _coerce_score(result.get("completeness_score"))
    numeric_fidelity = _coerce_score(result.get("numeric_fidelity_score"))
    constraint_satisfaction = _coerce_score(result.get("constraint_satisfaction_score"))
    uncertainty_calibration = _coerce_score(result.get("uncertainty_calibration_score"))

    # Track which dimensions were filled by fallback rather than directly scored.
    # The analysis script uses this list to exclude rows from per-axis breakdowns
    # where the LLM did not score a dimension independently — a synthesized score
    # from another axis is not statistically independent and would contaminate
    # per-axis comparisons. Composite and overall pass rate still include these rows;
    # only per-axis tables filter on this field.
    #
    # faithfulness ↔ groundedness is a prompt-enforced alias, NOT a cross-dimension
    # synthesis, so we do not record it here — only the three real fallbacks below.
    score_fallbacks_applied: List[str] = []

    if groundedness is None and faithfulness is not None:
        groundedness = faithfulness
    if faithfulness is None and groundedness is not None:
        faithfulness = groundedness
    if numeric_fidelity is None and groundedness is not None:
        numeric_fidelity = groundedness
        score_fallbacks_applied.append("numeric_fidelity_score")
    if constraint_satisfaction is None and completeness is not None:
        constraint_satisfaction = completeness
        score_fallbacks_applied.append("constraint_satisfaction_score")
    if uncertainty_calibration is None and completeness is not None:
        uncertainty_calibration = completeness
        score_fallbacks_applied.append("uncertainty_calibration_score")

    composite_score = _compute_composite_score(
        groundedness=groundedness,
        completeness=completeness,
        numeric_fidelity=numeric_fidelity,
        constraint_satisfaction=constraint_satisfaction,
        uncertainty_calibration=uncertainty_calibration,
    )

    issues = result.get("issues") if isinstance(result.get("issues"), list) else []
    feedback = result.get("corrective_feedback") if isinstance(result.get("corrective_feedback"), str) else None
    hard_fail = bool(result.get("hard_fail")) or _derive_hard_fail_from_issues(issues)
    hard_fail_reason = str(result.get("hard_fail_reason") or "").strip() or None

    # Normalize verdict and enforce threshold guardrails if scores are present.
    verdict = str(result.get("verdict") or "pass").lower().strip()

    # Hard fail rules always take precedence.
    if hard_fail:
        verdict = "fail"

    # Prefer strict/balanced profile gates when dimensional scores are available.
    has_dimensional_scores = all(
        metric is not None
        for metric in [groundedness, completeness, numeric_fidelity, constraint_satisfaction]
    )
    if has_dimensional_scores and composite_score is not None:
        if (
            groundedness < profile["groundedness_min"]
            or numeric_fidelity < profile["numeric_fidelity_min"]
            or constraint_satisfaction < profile["constraint_satisfaction_min"]
            or completeness < profile["completeness_min"]
            or composite_score < profile["composite_min"]
        ):
            verdict = "fail"
    elif faithfulness is not None and completeness is not None:
        # Backward-compatible guardrail if only legacy scores are returned.
        if faithfulness < legacy_threshold or completeness < legacy_threshold:
            verdict = "fail"

    if verdict not in {"pass", "fail"}:
        verdict = "pass"

    retry_candidate = False
    if verdict == "fail" and not hard_fail:
        if composite_score is None:
            retry_candidate = True
        else:
            retry_candidate = profile["retry_lower"] <= composite_score < profile["composite_min"]

    scores = {
        "mode": mode,
        "faithfulness_score": faithfulness,
        "groundedness_score": groundedness,
        "completeness_score": completeness,
        "numeric_fidelity_score": numeric_fidelity,
        "constraint_satisfaction_score": constraint_satisfaction,
        "uncertainty_calibration_score": uncertainty_calibration,
        "composite_score": composite_score,
        "hard_fail": hard_fail,
        "hard_fail_reason": hard_fail_reason,
        "retry_candidate": retry_candidate,
        "issues": issues,
        "evaluated": not evaluator_failed,
        "evaluation_status": "failed_open" if evaluator_failed else "evaluated",
        "evaluator_failed": evaluator_failed,
        "evaluator_failure_reason": evaluator_failure_reason,
        # Empty list = every dimension was scored independently by the LLM.
        # Any entry names an axis that was synthesized from another dimension;
        # such rows must be excluded from per-axis statistical comparisons.
        "score_fallbacks_applied": score_fallbacks_applied,
    }

    md = state.get("metadata") or {}
    debug = (md.get("debug") or {}) if isinstance(md, dict) else {}
    debug = _deep_merge(debug, {
        "evaluator_mode": mode,
        "evaluator_bypassed": False,
        "eval_verdict": verdict,
        "eval_scores": scores,
        "eval_retry_count": retry_count,
    })
    if verdict == "fail":
        debug["eval_warning"] = "Evaluator failed answer quality check."

    print(f"[evaluate_response] verdict={verdict} retry_count={retry_count}", flush=True)
    print(
        "[evaluate_response] "
        f"mode={mode} faithfulness={faithfulness} groundedness={groundedness} "
        f"completeness={completeness} numeric={numeric_fidelity} "
        f"constraint={constraint_satisfaction} uncertainty={uncertainty_calibration} "
        f"composite={composite_score} hard_fail={hard_fail}",
        flush=True,
    )
    if issues:
        print(f"[evaluate_response] issues={issues}", flush=True)
    if feedback:
        print(f"[evaluate_response] feedback={feedback}", flush=True)

    trace = build_evaluation_trace(
        **_build_trace_identifiers(state),
        question=question,
        answer=answer,
        # Snapshot the SQL/vector evidence the evaluator actually saw, truncated
        # per-leaf to keep the JSONL line manageable. Anyone reviewing the trace
        # can verify groundedness against this evidence without re-running the pipeline.
        aggregated_data=truncate_for_trace(aggregated_data),
        mode=mode,
        verdict=verdict,
        evaluated=not evaluator_failed,
        evaluation_status="failed_open" if evaluator_failed else "evaluated",
        eval_retry_count=retry_count,
        eval_scores=scores,
        model_deployment=_get_evaluator_deployment_label(),
        prompt_version=_get_evaluator_prompt_version(),
        evaluator_failure_reason=evaluator_failure_reason,
        # This attempt's latency + tokens. Multi-attempt sequences (retry) produce
        # one trace record per attempt, each with its own per-call timing.
        summarizer_latency_ms=summarizer_latency_ms,
        summarizer_token_usage=summarizer_token_usage,
        evaluator_latency_ms=evaluator_latency_ms,
        evaluator_token_usage=evaluator_token_usage,
        # Temperature provenance is sourced from state, written there by the summarizer node.
        summarizer_temperature_requested=state.get("summarizer_temperature_requested"),
        summarizer_temperature_unsupported=bool(state.get("summarizer_temperature_unsupported")),
    )
    try:
        append_evaluation_trace(trace)
    except Exception as trace_error:
        logger.warning("Failed to append evaluator trace: %s", trace_error)

    print("[evaluate_response] EXIT", flush=True)
    return {
        **base_updates,
        "eval_verdict": verdict,
        "eval_feedback": feedback if verdict == "fail" else None,
        "eval_scores": scores,
        "eval_retry_count": retry_count,
        # Persist this attempt's latency/tokens into state so the runner can sum
        # across attempts (first attempt + retry) when writing per-question totals.
        "evaluator_latency_ms": evaluator_latency_ms,
        "evaluator_token_usage": evaluator_token_usage,
        "metadata": _deep_merge(md, {"debug": debug}),
    }


def route_after_summarizer(state: GraphState) -> str:
    """Decide what runs after the summarizer.

    Returns:
        "end" — when EVALUATOR_MODE=off (or EVALUATOR_ENABLED=false). The graph
        skips evaluate_response_node entirely; the no-evaluator arm incurs zero
        evaluator overhead. This is a *complete bypass*, not a zero-threshold
        pass-through: the evaluator node is never invoked at all.
        "evaluate_response" — for balanced/strict modes (arms A2/A3/A4).
    """
    if _is_evaluator_bypassed():
        print("[route_after_summarizer] evaluator bypass enabled → end", flush=True)
        return "end"
    return "evaluate_response"


def route_after_evaluation(state: GraphState) -> str:
    """Route to END on pass or retry cap, otherwise loop back to summarizer once."""
    if _is_evaluator_bypassed():
        return "end"

    verdict = str(state.get("eval_verdict") or "pass").lower().strip()
    retry_count = int(state.get("eval_retry_count") or 0)
    max_retries = int(os.getenv("EVALUATOR_MAX_RETRIES", "1"))

    if verdict == "pass":
        return "end"

    if verdict == "fail" and retry_count < max_retries and _is_retry_candidate(state.get("eval_scores") or {}):
        return "retry_summarizer"
    return "end"

# ======================
# Request address & misc
# ======================
def request_address_node(state: GraphState) -> GraphState:
    print("[request_address] ENTER → END", flush=True)
    md = state.get("metadata", {}) or {}
    clarification = md.get("clarification") or {}
    reason = clarification.get("reason") or "missing_address"
    question = build_clarification_question(reason)
    return {
        "final_response": question,
        "request_address_fired": True,
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
    question = build_clarification_question(reason)
    return {
        "final_response": question,
        "clarification_fired": True,
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

# =========================================================
# Closed-loop checkpoint nodes (no-ops unless RUN_ARM set)
# =========================================================

def _make_checkpoint_evaluator():
    from src.evaluation.closed_loop.in_loop_evaluator import InLoopEvaluator
    return InLoopEvaluator()


def early_route_checkpoint_node(state: GraphState) -> GraphState:
    """Judge the chosen route; in the full arm, rewind the router if implausible."""
    arm = state.get("eval_arm") or os.environ.get("RUN_ARM", "")
    if arm != "A_full" or not _env_bool("EARLY_CHECKPOINT_ENABLED", False):
        return {}
    print(f"[early_route_checkpoint] arm={arm}", flush=True)
    try:
        from src.evaluation.closed_loop.controller import EvaluationController, ControllerState
        ev = _make_checkpoint_evaluator()
        ctrl = ControllerState(
            # `or 2` would silently resurrect an exhausted budget, since `0 or 2` is 2.
            # `.get`'s default only fires when the key is absent, so an exhausted 0 survives.
            retry_budget_remaining=int(state.get("retry_budget", 2)),
            attempt_history=list(state.get("checkpoint_history") or []),
            arm=arm)
        # This checkpoint already sits on the edge that leads to request_address, but it was
        # never told which way that edge is about to go — so it judged "is 'building' a
        # plausible route?" in the abstract and answered "plausible" on generic questions the
        # graph was about to bounce back to the user for an address. route_after_ambiguity is
        # a pure read of the state, so we can ask it here and hand the judge the actual
        # pending decision. Not a threshold change: an added input (plan-eil-v20-fixes Fix 5).
        pending_hop = route_after_ambiguity(state)
        # k=3 self-consistency: a false fire destroys a case permanently while a true fire
        # only probably gains one, so the asymmetry is worth 3x the early-judge calls
        # (v21 §2.1). k=1 is the v20 single-call path unchanged.
        vote_k = max(1, int(os.environ.get("CLOSED_LOOP_EARLY_VOTE_K", "1") or 1))
        verdict = ev.route_plausible_voted(
            question=str(state.get("last_message") or ""),
            chosen_route=str(state.get("top_route") or "building"),
            has_address_flag=bool((state.get("metadata") or {}).get("address")),
            about_to_request_address=(pending_hop == "request_address"),
            dialogue_summary=None, k=vote_k)
        decision = EvaluationController().handle_early_checkpoint(verdict, ctrl)
        rec = {"checkpoint_fired": "early",
               "route_picked_this_attempt": state.get("top_route"),
               "evaluator_verdict_json": {"verdict": verdict.verdict, "axes": verdict.axes},
               "early_vote_k": vote_k,
               "forced_route_applied": bool(state.get("forced_route_applied")),
               "controller_action": decision.action,
               "corrective_hint_passed": decision.corrective_hint}
        u = getattr(ev, "last_usage", {}) or {}
        updates: dict = {
            "retry_budget": ctrl.retry_budget_remaining,
            "checkpoint_history": ctrl.attempt_history,
            "controller_flags": {**(state.get("controller_flags") or {}), **decision.flags},
            "attempt_records": list(state.get("attempt_records") or []) + [rec],
            "hint_target": decision.target_stage,
            "eval_total_tokens": int(state.get("eval_total_tokens") or 0) + int(u.get("total_tokens", 0)),
            "eval_total_completions": int(state.get("eval_total_completions") or 0) + vote_k,
        }
        if decision.corrective_hint:
            updates["corrective_hint"] = decision.corrective_hint
        print(f"[early_route_checkpoint] verdict={verdict.verdict} action={decision.action}", flush=True)
        return updates
    except Exception as e:
        print(f"[early_route_checkpoint] ERROR (fail-open): {e}", flush=True)
        return {"hint_target": None}  # never leave a stale rewind target on error


def answer_quality_checkpoint_node(state: GraphState) -> GraphState:
    """Judge the final answer; rewind to the attributed stage on failure."""
    arm = state.get("eval_arm") or os.environ.get("RUN_ARM", "")
    if arm not in ("A_late_only", "A_full") or not _env_bool("LATE_CHECKPOINT_ENABLED", False):
        return {}
    print(f"[answer_quality_checkpoint] arm={arm}", flush=True)
    try:
        from src.evaluation.closed_loop.in_loop_evaluator import InLoopEvaluator
        from src.evaluation.closed_loop.controller import EvaluationController, ControllerState
        ev = InLoopEvaluator()
        ctrl = ControllerState(
            # See the early checkpoint: `or 2` would resurrect an exhausted budget.
            retry_budget_remaining=int(state.get("retry_budget", 2)),
            attempt_history=list(state.get("checkpoint_history") or []),
            arm=arm)
        verdict = ev.answer_quality(
            question=str(state.get("last_message") or ""),
            retrieved_evidence=state.get("aggregated_data") or {},
            final_answer=str(state.get("final_response") or ""))
        decision = EvaluationController().handle_late_checkpoint(verdict, ctrl)
        specs = [a for a, f in [("generic_sql_agent", "invoked_generic_sql"),
                                ("specialized_sql_agent", "invoked_specialized_sql"),
                                ("vector_db_agent", "invoked_vector")] if state.get(f)]
        _attrib = (lambda v: v) if verdict.verdict == "fail" else (lambda v: None)
        rec = {"checkpoint_fired": "late",
               "answer_drafted_this_attempt": str(state.get("final_response") or "")[:500],
               "specialists_picked_this_attempt": sorted(specs),
               "evaluator_verdict_json": {"verdict": verdict.verdict, "axes": verdict.axes, "composite": verdict.composite},
               "evaluator_axes_json": verdict.axes,
               "evaluator_composite": verdict.composite,
               "evidence_present": verdict.evidence_present,
               # Attribution is only meaningful on a FAILING attempt — on a passing one it
               # reports whichever axis happened to be lowest on a good answer, which
               # pollutes any tally that forgets to filter (v21 §1.6).
               "evaluator_stage_attribution_judge": _attrib(verdict.stage_attribution_judge),
               "evaluator_stage_attribution_rule": _attrib(verdict.stage_attribution_rule),
               "attribution_diagnostic": _attrib(verdict.attribution_diagnostic),
               "controller_action": decision.action,
               "corrective_hint_passed": decision.corrective_hint}
        u = getattr(ev, "last_usage", {}) or {}
        updates: dict = {
            "retry_budget": ctrl.retry_budget_remaining,
            "checkpoint_history": ctrl.attempt_history,
            "controller_flags": {**(state.get("controller_flags") or {}), **decision.flags},
            "attempt_records": list(state.get("attempt_records") or []) + [rec],
            "hint_target": decision.target_stage,
            "answer_quality_verdict": {"verdict": verdict.verdict, "axes": verdict.axes,
                                       "composite": verdict.composite,
                                       "evidence_present": verdict.evidence_present},
            "eval_total_tokens": int(state.get("eval_total_tokens") or 0) + int(u.get("total_tokens", 0)),
            "eval_total_completions": int(state.get("eval_total_completions") or 0) + 1,
        }
        if decision.corrective_hint:
            updates["corrective_hint"] = decision.corrective_hint
        print(f"[answer_quality_checkpoint] verdict={verdict.verdict} action={decision.action}", flush=True)
        return updates
    except Exception as e:
        print(f"[answer_quality_checkpoint] ERROR (fail-open): {e}", flush=True)
        return {"hint_target": None}  # never leave a stale rewind target on error


def rewind_to_router_node(state: GraphState) -> GraphState:
    """Clear the routing decision so understand_context can re-decide it with a hint.

    The hint goes to `router_hint`, NOT onto `last_message`: understand_context_node
    feeds it to the intent parser (which is the only thing that must see it), while
    `last_message` stays the user's original question so the judges and the summariser
    are never shown the correction as if the user had written it.

    Evidence and its instrumentation are cleared only when this arm owns them. In a
    cached arm the specialists skip their queries, so they never re-set `invoked_*` /
    `sql_fields_*` and never re-merge `aggregated_data`. Clearing any of it here would
    delete it permanently — the harness would then read `sql_fields_used: []` and score
    the case as route `clarification` with zero field coverage, while `evidence_present`
    still reported true from the preserved `aggregated_data`. That also breaks the
    byte-identical-evidence guarantee the paired comparison rests on.
    """
    print("[rewind_to_router] re-deciding route with corrective hint", flush=True)
    cached = bool(state.get("aggregated_data_cached"))
    # Always cleared: the barrier flags, so wait_for_replies waits for the new attempt.
    # The specialists re-run and re-set these in both arms (a cached one returns early
    # but still reports done).
    updates: GraphState = {
        "router_hint": state.get("corrective_hint") or "",
        # The route the judge just rejected. understand_context_node reads it to stop the
        # personal-energy-advice heuristic from re-promoting it (v21 §2.2).
        "rejected_route": state.get("top_route"),
        "hint_target": None,
        "done_generic_sql": None, "done_specialized_sql": None, "done_vector": None,
        # Decision flags from the attempt being rewound, including the identity gate —
        # a surviving gate flag would re-force clarification and make the rewind a no-op.
        "clarification_fired": False, "request_address_fired": False,
        "identity_gate_blocked": False,
    }
    if not cached:
        updates.update({
            "invoked_generic_sql": False, "invoked_specialized_sql": False, "invoked_vector": False,
            "sql_fields_generic": [], "sql_fields_specialized": [], "vector_chunks_retrieved": [],
            "aggregated_data": {},
        })
    return updates


def rewind_to_specialists_node(state: GraphState) -> GraphState:
    """Clear specialist state and pass the hint to the summarizer for the re-run.

    Cache-aware for the same reason `rewind_to_router_node` is: a cached arm's specialists
    early-return without re-setting `invoked_*` / `sql_fields_*` or re-merging
    `aggregated_data`, so clearing them here would destroy the shared evidence permanently
    and silently break the paired comparison. Unreachable today — `question_coverage` has
    never been the lowest axis — which is precisely why it was still armed (v21 §1.7).
    """
    print("[rewind_to_specialists] re-selecting specialists", flush=True)
    cached = bool(state.get("aggregated_data_cached"))
    updates: GraphState = {
        "hint_target": None, "eval_feedback": state.get("corrective_hint") or "",
        "done_generic_sql": None, "done_specialized_sql": None, "done_vector": None,
    }
    if not cached:
        updates.update({
            "invoked_generic_sql": False, "invoked_specialized_sql": False, "invoked_vector": False,
            "sql_fields_generic": [], "sql_fields_specialized": [], "vector_chunks_retrieved": [],
            "aggregated_data": {}, "aggregated_data_cached": False,
        })
    return updates


def rewind_to_summarizer_node(state: GraphState) -> GraphState:
    """Pass the corrective hint as eval_feedback; llm_summarizer_node reads it."""
    print("[rewind_to_summarizer] re-summarising with corrective hint", flush=True)
    return {"hint_target": None, "eval_feedback": state.get("corrective_hint") or ""}


def _route_after_early_checkpoint(state: GraphState) -> str:
    if state.get("hint_target") == "understand_context":
        return "rewind_to_router"
    return route_after_ambiguity(state)


def _route_after_summarizer_with_checkpoint(state: GraphState) -> str:
    arm = state.get("eval_arm") or os.environ.get("RUN_ARM", "")
    if arm in ("A_late_only", "A_full") and _env_bool("LATE_CHECKPOINT_ENABLED", False):
        return "answer_quality_checkpoint"
    return route_after_summarizer(state)


def _route_after_answer_quality_checkpoint(state: GraphState) -> str:
    target = state.get("hint_target")
    if target == "understand_context":
        return "rewind_to_router"
    if target == "maintain_history":
        return "rewind_to_specialists"
    if target == "llm_summarizer":
        return "rewind_to_summarizer"
    return "end"


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
    builder.add_node("evaluate_response", evaluate_response_node)

    # Address request
    builder.add_node("request_address", request_address_node)

    # Closed-loop checkpoint + rewind nodes (no-ops unless RUN_ARM selects an arm)
    builder.add_node("early_route_checkpoint", early_route_checkpoint_node)
    builder.add_node("answer_quality_checkpoint", answer_quality_checkpoint_node)
    builder.add_node("rewind_to_router", rewind_to_router_node)
    builder.add_node("rewind_to_specialists", rewind_to_specialists_node)
    builder.add_node("rewind_to_summarizer", rewind_to_summarizer_node)

    # Entry
    builder.set_entry_point("understand_context")

    # After parsing: early route checkpoint, then ambiguity + address gate
    builder.add_edge("understand_context", "early_route_checkpoint")
    builder.add_conditional_edges(
        "early_route_checkpoint",
        _route_after_early_checkpoint,
        {
            "clarification": "clarification",
            "maintain_history": "maintain_history",
            "request_address": "request_address",
            "rewind_to_router": "rewind_to_router",
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
    builder.add_conditional_edges(
        "llm_summarizer",
        _route_after_summarizer_with_checkpoint,
        {
            "answer_quality_checkpoint": "answer_quality_checkpoint",
            "evaluate_response": "evaluate_response",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "answer_quality_checkpoint",
        _route_after_answer_quality_checkpoint,
        {
            "rewind_to_router": "rewind_to_router",
            "rewind_to_specialists": "rewind_to_specialists",
            "rewind_to_summarizer": "rewind_to_summarizer",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "evaluate_response",
        route_after_evaluation,
        {
            "end": END,
            "retry_summarizer": "llm_summarizer",
        },
    )

    # Closed-loop rewind edges (cycles bounded by the retry budget)
    builder.add_edge("rewind_to_router", "understand_context")
    builder.add_edge("rewind_to_specialists", "maintain_history")
    builder.add_edge("rewind_to_summarizer", "llm_summarizer")

    # Early exits
    builder.add_edge("clarification", END)
    builder.add_edge("request_address", END)
    builder.add_edge("await_more", END)


    return builder.compile()
