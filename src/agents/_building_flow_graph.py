# src/agents/building_flow_graph.py
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Any, Dict
from src.agents.parse_intent_agent import ParseIntentAgent
from src.agents.generic_sql_layer import SQL_Mapper_Layer
from src.database.vector_client import VectorClient, VectorClientConfig
from src.agents.openai_agent import OpenAIResponseAgent
from src.agents.specialized_sql_layer import SpecializedSQLLayer
import re
import os
import json

# -----------------------------
# Define the Graph State Schema
# -----------------------------

parse_intent_agent = ParseIntentAgent()
sql_mapper_layer = SQL_Mapper_Layer()
try:
    specialized_layer = SpecializedSQLLayer()
    _SPECIALIZED_INIT_ERROR = None
except Exception as e:
    specialized_layer = None
    _SPECIALIZED_INIT_ERROR = f"SpecializedSQLLayer init failed: {e}"


# Initialize VectorClient using its config
_vector_cfg = VectorClientConfig()
vector_database = VectorClient(_vector_cfg)

# LLM summarizer (Azure OpenAI)
llm_summarizer = OpenAIResponseAgent()


class GraphState(TypedDict, total=False):
    thread_id: str
    last_message: str
    messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    session_state: Dict[str, Any]
    context: Dict[str, Any]
    agent_outputs: List[str]
    aggregated: str
    final_response: str


# -----------------------------
# Node Functions
# -----------------------------

def _ensure_building_id_from_address(state: GraphState) -> None:
    """
    If we have an address but no building_id, use the generic SQL layer
    to resolve building_id and stash it in state.metadata.
    Silent no-op on failure; caller handles messaging.
    """
    md = state.setdefault("metadata", {})
    if md.get("building_id"):
        return
    address = md.get("address")
    if not address:
        return
    try:
        result = sql_mapper_layer.execute("building_by_address", {"address": address})
        if result and result.get("ok") and result.get("data"):
            data = result["data"]
            bid = None
            if isinstance(data, dict):
                bid = data.get("building_id") or data.get("ID") or data.get("id")
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                d0 = data[0]
                bid = d0.get("building_id") or d0.get("ID") or d0.get("id")
            if bid:
                md["building_id"] = bid
    except Exception as e:
        # Don't fail the flow—just surface a debug note
        state.setdefault("agent_outputs", []).append(f"Building ID resolution failed: {e}")


def entry_point_node(state: GraphState) -> GraphState:
    print("Entered entry_point")
    return state


def understand_context_node(state: GraphState) -> GraphState:
    print("Running understand_context")
    updated_state = parse_intent_agent(state)
    return updated_state


def clarification_node(state: GraphState) -> GraphState:
    print("Clarifying...")
    original_message = state.get("messages", [])[-1]["content"] if state.get("messages") else ""
    state["final_response"] = (
        f"I'm not entirely sure what you meant by: \"{original_message}\". "
        "Could you please clarify your question or provide more details?"
    )
    return state


def maintain_history_node(state: GraphState) -> GraphState:
    print("Maintaining history")
    if not state.get("last_message") and state.get("messages"):
        state["last_message"] = state["messages"][-1].get("content", "")
    history = state.get("session_state", {}).get("history", [])
    history.append(state.get("last_message", ""))
    state.setdefault("session_state", {})["history"] = history
    return state


def route_after_ambiguity(state: GraphState) -> str:
    print("Routing based on ambiguity")
    return "clarification" if state.get("context", {}).get("ambiguous") else "maintain_history"


# After history, skip address gate for vector queries
def route_post_history(state: GraphState) -> str:
    print("Routing after history")
    intent = (state.get("context", {}) or {}).get("intent", "").lower()
    if intent in {"query vector database", "vector_search"}:
        return "decision"
    return "check_address"


def check_address_node(state: GraphState) -> str:
    print("Checking for address...")
    metadata = state.setdefault("metadata", {})
    last_message = state.get("last_message", "") or ""

    if metadata.get("address"):
        return "address_found"

    pattern = re.compile(
        r"\b(?:bor\s+på|adressen?\s+är|address\s+is|live\s+at|live\s+in)\b\s*[:\-]?\s*"
        r"([A-Za-zÅÄÖåäö0-9 ,.\-/]{3,120}?)"
        r"(?=(?:[.?!]\s|$))",
        re.IGNORECASE,
    )
    match = pattern.search(last_message)
    if match:
        addr = match.group(1).strip(" ,.-/")
        if addr:
            metadata["address"] = addr
            return "address_found"

    return "request_address"


def address_found_node(state: GraphState) -> GraphState:
    print("Address found, proceeding.")
    return state


def request_address_node(state: GraphState) -> GraphState:
    print("Requesting address from user.")
    state["final_response"] = "Can you please provide the building address?"
    return state


def decision_node(state: GraphState) -> str:
    print("Deciding routing based on intent")
    intent = (state.get("context", {}) or {}).get("intent", "").lower()
    return {
        "query generic database": "generic_sql_agent",
        "query specific database": "specialized_sql_agent",
        "query vector database": "vector_db_agent",
        "simulations": "simulation_agent",
        # backward-compat keys:
        "generic_sql": "generic_sql_agent",
        "vector_search": "vector_db_agent",
        "simulation": "simulation_agent",
    }.get(intent, "other_agent")


def _describe_payload_for_aggregation(data: Any) -> str:
    if data is None:
        return "No data returned."
    if isinstance(data, dict):
        lines = [f"{k}: {data[k]}" for k in sorted(data.keys())]
        return "\n".join(lines)
    if isinstance(data, list):
        return "\n".join([str(x) for x in data[:5]])
    return str(data)



# -----------------------------
# Agent Nodes
# -----------------------------
def generic_sql_agent_node(state: GraphState) -> GraphState:
    op, kwargs = sql_mapper_layer.route(state)
    result = sql_mapper_layer.execute(op, kwargs)

    # If resolved by address, extract building_id then fetch topic
    if op == "building_by_address" and result.get("ok") and result.get("data"):
        data = result["data"]
        building_id = None
        if isinstance(data, dict):
            building_id = data.get("building_id") or data.get("ID") or data.get("id")
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            d0 = data[0]
            building_id = d0.get("building_id") or d0.get("ID") or d0.get("id")

        if building_id:
            state.setdefault("metadata", {})["building_id"] = building_id
            topic_op, topic_kwargs = sql_mapper_layer._route_with_building_id(
                sql_mapper_layer._classify_topic(
                    (state.get("last_message") or "").lower(),
                    [e.lower() for e in (state.get("context", {}).get("entities") or [])],
                ),
                building_id,
            )
            result = sql_mapper_layer.execute(topic_op, topic_kwargs)

    if result.get("ok"):
        state.setdefault("agent_outputs", []).append(
            _describe_payload_for_aggregation(result.get("data"))
        )
        return state

    msg = (result.get("message") or "").lower()
    if "missing_address" in msg:
        state["final_response"] = "Can you please provide the building address?"
        return state

    state.setdefault("agent_outputs", []).append(
        f"Generic SQL lookup failed: {result.get('message')}"
    )
    return state


def specialized_sql_agent_node(state: GraphState) -> GraphState:
    # Check that the layer initialized
    if specialized_layer is None:
        state.setdefault("agent_outputs", []).append(_SPECIALIZED_INIT_ERROR or "Specialized layer unavailable.")
        return state

    try:
        # Try to populate building_id if we only have address
        _ensure_building_id_from_address(state)

        op, kwargs = specialized_layer.route(state)
        result = specialized_layer.execute(op, kwargs)

        if result.get("ok"):
            # Uniform aggregation-friendly formatting
            preview = _describe_payload_for_aggregation(result.get("data"))
            state.setdefault("agent_outputs", []).append(f"Specialized SQL:\n{preview}")
        else:
            state.setdefault("agent_outputs", []).append(f"Specialized SQL error: {result.get('message')}")
    except Exception as e:
        state.setdefault("agent_outputs", []).append(f"Specialized SQL exception: {e}")

    return state


def vector_db_agent_node(state: GraphState) -> GraphState:
    try:
        query = state.get("last_message") or ""
        ctx = state.get("context", {}) or {}
        ents = ctx.get("entities") or []
        ent_str = ", ".join([str(e) for e in ents]) if ents else ""

        q = f"{query}\nEntities: {ent_str}" if ent_str else query
        results = vector_database.query(q)

        if not results:
            state.setdefault("agent_outputs", []).append("Vector DB: no relevant passages found.")
            return state

        lines, sources, snippets = [], [], []
        for i, r in enumerate(results[:5], 1):
            text = (r.get("page_content") or "").strip()
            meta = r.get("metadata") or {}
            src = meta.get("source") or meta.get("url") or meta.get("document_id") or meta.get("doc_id") or meta.get("id")

            # Debug/preview block for aggregator
            preview = text.replace("\n", " ")
            if len(preview) > 300:
                preview = preview[:300] + "…"
            prefix = f"[{i}]"
            if src:
                prefix += f" <{src}>"
                sources.append(src)
            lines.append(f"{prefix} {preview}")

            # Save full text + source for LLM summarizer
            snippets.append({"text": text, "source": src})

        # Deduplicate sources in order
        seen = set()
        dedup_sources = []
        for s in sources:
            if s and s not in seen:
                dedup_sources.append(s)
                seen.add(s)

        md = state.setdefault("metadata", {})
        md["vector_sources"] = dedup_sources
        md["vector_snippets"] = snippets

        state.setdefault("agent_outputs", []).append("Vector results:\n" + "\n".join(lines))
        return state

    except Exception as e:
        state.setdefault("agent_outputs", []).append(f"Vector DB error: {e}")
        return state


def simulation_agent_node(state: GraphState) -> GraphState:
    state.setdefault("agent_outputs", []).append("Simulation result")
    return state


def other_agent_node(state: GraphState) -> GraphState:
    state.setdefault("agent_outputs", []).append("Fallback agent result")
    return state


# -----------------------------
# Aggregation and Output Nodes
# -----------------------------
def aggregator_node(state: GraphState) -> GraphState:
    outputs = state.get("agent_outputs", [])
    state["aggregated"] = "\n".join(outputs) if outputs else "No results found."
    return state


def _build_vector_context_block(state: GraphState) -> str:
    md = state.get("metadata", {}) or {}
    snippets = md.get("vector_snippets") or []
    if not snippets:
        return ""
    lines = []
    for i, sn in enumerate(snippets[:5], 1):
        src = sn.get("source")
        label = os.path.basename(src) if isinstance(src, str) else (str(src) if src else "")
        lines.append(f"[{i}] {sn.get('text','').strip()}\nSOURCE: {label}")
    return "CONTEXT PASSAGES:\n" + "\n\n".join(lines)


def llm_summarizer_node(state: GraphState) -> GraphState:
    ctx = state.get("context", {}) or {}
    intent = (ctx.get("intent") or "").lower()

    # Use LLM for vector answers with retrieved passages as context
    if intent in {"query vector database", "vector_search"}:
        try:
            context_block = _build_vector_context_block(state)
            base_msgs = list(state.get("messages", []))
            if context_block:
                base_msgs = base_msgs + [{"role": "assistant", "content": context_block}]

            answer = llm_summarizer.generate_response(
                last_message=state.get("last_message", ""),
                message_list=base_msgs
            )
            if isinstance(answer, str) and answer.strip():
                state["final_response"] = answer.strip()
                return state
        except Exception as e:
            state.setdefault("agent_outputs", []).append(f"LLM summarize error: {e}")

    # Default: concise user-friendly summary for non-vector intents
    aggregated = state.get("aggregated", "").strip()
    if not aggregated:
        state["final_response"] = "No results to summarize."
        return state

    # If it looks like key:value pairs and short, compress into a single line
    lines = [ln for ln in aggregated.splitlines() if ln.strip()]
    if all(":" in ln for ln in lines) and len(lines) <= 6:
        kv = []
        for ln in lines:
            k, v = ln.split(":", 1)
            kv.append(f"{k.strip()} = {v.strip()}")
        state["final_response"] = " • ".join(kv)
    else:
        state["final_response"] = f"Summary of: {aggregated}"
    return state


# -----------------------------
# Graph Builder
# -----------------------------
def build_building_flow_graph():
    builder = StateGraph(GraphState)

    # Add nodes
    builder.add_node("entry_point", entry_point_node)
    builder.add_node("understand_context", understand_context_node)
    builder.add_node("clarification", clarification_node)
    builder.add_node("maintain_history", maintain_history_node)
    builder.add_node("check_address", lambda state: state)  # identity node; router handles branching
    builder.add_node("request_address", request_address_node)
    builder.add_node("address_found", address_found_node)
    builder.add_node("decision", lambda x: x)  # identity node; router handles branching
    builder.add_node("generic_sql_agent", generic_sql_agent_node)
    builder.add_node("specialized_sql_agent", specialized_sql_agent_node)
    builder.add_node("vector_db_agent", vector_db_agent_node)
    builder.add_node("simulation_agent", simulation_agent_node)
    builder.add_node("other_agent", other_agent_node)
    builder.add_node("aggregator", aggregator_node)
    builder.add_node("llm_summarizer", llm_summarizer_node)

    # Graph entry
    builder.set_entry_point("entry_point")

    # Flow
    builder.add_edge("entry_point", "understand_context")
    builder.add_conditional_edges(
        "understand_context",
        route_after_ambiguity,
        {
            "clarification": "clarification",
            "maintain_history": "maintain_history",
        },
    )
    builder.add_edge("clarification", "maintain_history")

    # After history, vector queries go straight to decision; others check address
    builder.add_conditional_edges(
        "maintain_history",
        route_post_history,
        {
            "decision": "decision",
            "check_address": "check_address",
        },
    )

    builder.add_conditional_edges(
        "check_address",
        check_address_node,
        {
            "address_found": "address_found",
            "request_address": "request_address",
        },
    )

    builder.add_edge("address_found", "decision")
    builder.add_conditional_edges(
        "decision",
        decision_node,
        {
            "generic_sql_agent": "generic_sql_agent",
            "specialized_sql_agent": "specialized_sql_agent",
            "vector_db_agent": "vector_db_agent",
            "simulation_agent": "simulation_agent",
            "other_agent": "other_agent",
        },
    )

    for agent in [
        "generic_sql_agent",
        "specialized_sql_agent",
        "vector_db_agent",
        "simulation_agent",
        "other_agent",
    ]:
        builder.add_edge(agent, "aggregator")

    builder.add_edge("aggregator", "llm_summarizer")
    builder.add_edge("llm_summarizer", END)

    # Exit early if we request address from user
    builder.add_edge("request_address", END)

    return builder.compile()
