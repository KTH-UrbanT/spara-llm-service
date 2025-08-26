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
from src.database.vector_client import VectorClient, VectorClientConfig
from src.agents.openai_agent import OpenAIResponseAgent
from src.agents.specialized_sql_layer import SpecializedSQLLayer
from src.database.hammarby_data import query_address
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

def _normalize_address(addr: Optional[str]) -> Optional[str]:
    if not addr:
        return None
    a = re.sub(r"\s+", " ", str(addr)).strip()
    return a.lower()

def _ascii_fold(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")

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

    # Ensure required intent keys exist (no transformations)
    if "parsed_intent" not in ctx:
        ctx["parsed_intent"] = None
    if "intent_list" not in ctx or ctx["intent_list"] is None:
        ctx["intent_list"] = []

    # Preserve effective_query for address-only followups (logic kept as requested)
    if ctx["parsed_intent"] == "" and  ctx.get("address") != "":
        message_list = state.get("messages", [])
        if len(message_list) >= 2:
            try:
                ctx["intent_list"], ctx["parsed_intent"] = message_list[-2]['intent_list'], message_list[-3]['parsed_intent']
                print("[understand_context] restored intent from -3 message", flush=True)
            except Exception as e:
                print(f"[understand_context] restore intent failed: {e}", flush=True)

    # Metadata: if address present in parsed input, set both fields
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
    else:
        key = next((k for k in SINGLE_FILTER_KEYS if md.get(k) not in (None, "")), None)
        if key is not None:
            value = md[key]
            print(f"[generic_sql_agent] single_filter key={key} value={value!r}", flush=True)
            result = sql_mapper_layer.execute("single_filter", key=key, value=value)

    updates: Dict[str, Any] = {"done_generic_sql": True}
    outs: List[str] = []

    if result and result.get("ok"):
        updates["agent_data_generic"] = result.get("data")
        outs.append("generic_sql: ok")
        try:
            n = len(result.get("data") or [])
        except Exception:
            n = "?"
        print(f"[generic_sql_agent] SUCCESS rows={n}", flush=True)
    else:
        if addr:
            outs.append(f"generic_sql: no rows for address {addr!r} (ASCII fallback tried if applicable).")
        else:
            outs.append("generic_sql: no address/supported single-filter provided.")
        print("[generic_sql_agent] NO DATA", flush=True)
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
        result = specialized_layer.execute_with_building(state)
        if result.get("ok"):
            updates["agent_data_specialized"] = result.get("data")
            outs.append(_describe_payload_for_aggregation(result.get("data")))
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
    for k in ("agent_outputs_generic", "agent_outputs_specialized", "agent_outputs_vector",
              "agent_outputs_simulation", "agent_outputs_other"):
        outs = state.get(k) or []
        outputs.extend(outs if isinstance(outs, list) else [outs])

    aggregated_text = "\n\n".join(outputs) if outputs else "No agent output."

    merged_data: Dict[str, Any] = {}
    legacy_agent_data: Dict[str, Any] = {}

    if "agent_data_generic" in state:
        merged_data["generic_sql"] = state["agent_data_generic"]
        legacy_agent_data["generic_sql"] = state["agent_data_generic"]
    if "agent_data_specialized" in state:
        merged_data["specialized_sql"] = state["agent_data_specialized"]
        legacy_agent_data["specialized_sql"] = state["agent_data_specialized"]
    if "agent_vector_sources" in state or "agent_vector_snippets" in state:
        merged_data["vector"] = {
            "sources": state.get("agent_vector_sources", []),
            "snippets": state.get("agent_vector_snippets", []),
        }
        legacy_agent_data["vector"] = merged_data["vector"]

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

    updates = {
        "aggregated": aggregated_text,
        "agent_outputs": outputs,
        "metadata": _deep_merge(state.get("metadata", {}), {"debug": new_debug}),
    }
    if merged_data:
        updates["aggregated_data"] = copy.deepcopy(merged_data)
        updates["agent_data"] = copy.deepcopy(legacy_agent_data)

    try:
        out_count = len(outputs)
    except Exception:
        out_count = "?"
    print(f"[aggregator] outputs={out_count} agent_answered={debug_label}", flush=True)
    print("[aggregator] EXIT", flush=True)
    return updates

def llm_summarizer_node(state: GraphState) -> GraphState:
    print("[llm_summarizer] ENTER", flush=True)
    ctx = state.get("context", {}) or {}
    prompt = 'Question : ' + (state.get("last_message") or "") + 'Context : ' + str(state.get('aggregated_data', {}))
    try:
        answer = llm_summarizer.generate_response(prompt, message_list=(state.get('messages') or []))
        print("[llm_summarizer] generate_response ✓", flush=True)
    except Exception as e:
        answer = "Sorry, summarizer error."
        print(f"[llm_summarizer] ERROR: {e}", flush=True)

    sess = {**(state.get("session_state") or {})}
    if ctx.get("intent"):
        sess["last_intent"] = ctx.get("intent")
    if ctx.get("intent_list") is not None:
        sess["last_intent_list"] = ctx.get("intent_list")

    out = (state.get("step_out") or []) + ["llm_summarizer ✓"]
    print("[llm_summarizer] EXIT", flush=True)
    return {"final_response": answer, "session_state": sess, "step_out": out}

# ======================
# Request address & misc
# ======================
def request_address_node(state: GraphState) -> GraphState:
    print("[request_address] ENTER → END", flush=True)
    return {"final_response": "Can you please provide the building address?"}

def clarification_node(state: GraphState) -> GraphState:
    print("[clarification] ENTER → END", flush=True)
    return {"final_response": "Could you clarify your request?"}

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

    # Address request
    builder.add_node("request_address", request_address_node)

    # Entry
    builder.set_entry_point("understand_context")

    # After parsing: ambiguity + address gate
    builder.add_conditional_edges(
        "understand_context",
        route_after_ambiguity,
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
        {"aggregator": "aggregator", "await_more": "await_more"},
    )

    builder.add_edge("aggregator", "llm_summarizer")
    builder.add_edge("llm_summarizer", END)

    # Early exits
    builder.add_edge("clarification", END)
    builder.add_edge("request_address", END)
    builder.add_edge("await_more", END)


    return builder.compile()
