"""End-to-end graph wiring: all three arms reach an answer, and rewinds behave."""
import os, pytest
from unittest.mock import MagicMock, patch

@pytest.fixture()
def base_env():
    """Fake Azure/deployment env so the graph imports without real credentials."""
    with patch.dict(os.environ, {
        "OPENAI_API_KEY": "test", "AZURE_ENDPOINT": "https://x.openai.azure.com/",
        "OPENAI_API_VERSION": "2023-05-15",
        "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME": "gpt-4o",
        "GENERIC_MODEL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "ROUTER_MODEL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "CONVERSATIONAL_MODEL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "BUILDING_MODEL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "SPECIALIZED_SQL_DEPLOYMENT_NAME": "gpt-4o-mini",
        "EVALUATOR_MODE": "off",
    }): yield

def _wire_externals(monkeypatch):
    """Stub SQL and vector backends so the graph runs with no database."""
    import src.agents.building_flow_graph as m
    sql = MagicMock()
    sql.execute.return_value = {
        "ok": True, "data": [{"byggnadsid": "B1", "epc_egenatemp": 100}],
        "message": "ok",
        "trace": {"query_type":"generic_sql","execution_status":"success",
                  "rows_returned":1,"match_strategy":"eq","returned_values_used":{"byggnadsid":"B1"}},
    }
    monkeypatch.setattr(m, "sql_mapper_layer", sql)
    monkeypatch.setattr(m.vector_database, "query", MagicMock(return_value=[]))
    summ = MagicMock()
    summ.generate_response.return_value = "Atemp is 100."
    summ._last_call_meta = {"latency_ms": 100, "token_usage": {}}
    monkeypatch.setattr(m, "llm_summarizer", summ)
    parser = MagicMock()
    parser.return_value = {"context": {
        "parsed_intent": "SQL database", "intent_list": ["SQL database"],
        "address": "T 1", "ambiguous": False, "ambigious": False}}
    monkeypatch.setattr(m, "parse_intent_agent", parser)

def _plausible():
    """A passing early verdict, so the route is never rewound."""
    return MagicMock(verdict="plausible",
                     axes={"intent_consistency": 9, "precondition_satisfied": 9},
                     corrective_hint=None)

def _wire_judge(monkeypatch):
    """Stub the judge with passing verdicts, so only the wiring is under test."""
    ev = MagicMock()
    # The node calls the voting wrapper (v21 §2.1); `route_plausible` is what the wrapper
    # calls k times internally, so a mocked evaluator must stub the wrapper.
    ev.route_plausible.return_value = _plausible()
    ev.route_plausible_voted.return_value = _plausible()
    ev.answer_quality.return_value = MagicMock(
        verdict="pass", axes={"faithfulness":9,"answer_relevance":9,"question_coverage":9,"calibration":9},
        composite=9.0, stage_attribution_judge="summarizer", stage_attribution_rule="summarizer", corrective_hint=None)
    ev.last_usage = {"total_tokens": 12, "completion_tokens": 6}  # real dict, not a mock attr
    monkeypatch.setattr("src.evaluation.closed_loop.in_loop_evaluator.InLoopEvaluator", lambda: ev)

def _graph(monkeypatch, arm):
    """Build the compiled graph with EVALUATOR_MODE set for the given arm."""
    _wire_externals(monkeypatch)
    if arm != "A_open":
        _wire_judge(monkeypatch)
    with patch.dict(os.environ, {
        "EVALUATOR_MODE": {"A_open": "off", "A_late_only": "late", "A_full": "full"}[arm],
    }):
        from src.agents.building_flow_graph import build_building_flow_graph
        return build_building_flow_graph()

def _st(arm):
    """A minimal initial graph state for one building question."""
    return {"last_message": "Atemp of T 1?", "messages": [], "metadata": {"address": "T 1"},
            "eval_arm": arm, "retry_budget": 2, "checkpoint_history": [],
            "controller_flags": {}, "attempt_records": [],
            "corrective_hint": None, "hint_target": None}

def test_open_runs(base_env, monkeypatch):
    """The open arm reaches a final answer — the graph is wired identically in all arms."""
    assert _graph(monkeypatch, "A_open").invoke(_st("A_open")).get("final_response")

def test_late_only_runs(base_env, monkeypatch):
    """The late-only arm reaches a final answer with the late checkpoint live."""
    assert _graph(monkeypatch, "A_late_only").invoke(_st("A_late_only")).get("final_response")

def test_full_runs(base_env, monkeypatch):
    """The full arm reaches a final answer with both checkpoints live."""
    assert _graph(monkeypatch, "A_full").invoke(_st("A_full")).get("final_response")

def _wire_generic_question_without_address(monkeypatch):
    """A general-advice question the parser mis-reads as needing SQL, and no address.

    This is the EKR_GEN_017 / 019 / 030 shape: route_after_ambiguity sends it to
    request_address, so the user gets "what is your address?" instead of an answer.
    """
    import src.agents.building_flow_graph as m
    parser = MagicMock()
    parser.return_value = {"context": {
        "parsed_intent": "SQL database", "intent_list": ["SQL database"],
        "ambiguous": False, "ambigious": False}}
    monkeypatch.setattr(m, "parse_intent_agent", parser)


def test_early_checkpoint_sees_the_pending_address_request(base_env, monkeypatch):
    """Fix 5: the judge must be told the graph is about to demand an address.

    Without this input it judged "is 'building' plausible?" in the abstract and said yes
    on exactly the cases it was best placed to catch.
    """
    _wire_externals(monkeypatch)
    _wire_generic_question_without_address(monkeypatch)
    ev = MagicMock()
    ev.route_plausible_voted.return_value = _plausible()
    ev.last_usage = {"total_tokens": 12, "completion_tokens": 6}
    monkeypatch.setattr("src.evaluation.closed_loop.in_loop_evaluator.InLoopEvaluator", lambda: ev)

    with patch.dict(os.environ, {"EVALUATOR_MODE": "full"}):
        from src.agents.building_flow_graph import build_building_flow_graph
        g = build_building_flow_graph()
        st = _st("A_full")
        st["metadata"] = {}          # no address anywhere -> the graph will ask for one
        st["last_message"] = "How can I reduce heating costs in an apartment building?"
        g.invoke(st)

    assert ev.route_plausible_voted.call_args.kwargs["about_to_request_address"] is True


def test_early_checkpoint_flag_is_false_when_the_graph_can_answer(base_env, monkeypatch):
    """The flag must not fire on building questions that do have an address."""
    _wire_externals(monkeypatch)
    ev = MagicMock()
    ev.route_plausible_voted.return_value = _plausible()
    ev.answer_quality.return_value = MagicMock(
        verdict="pass", axes={"faithfulness": 9, "answer_relevance": 9,
                              "question_coverage": 9, "calibration": 9},
        composite=9.0, evidence_present=True, stage_attribution_judge="summarizer",
        stage_attribution_rule="summarizer", attribution_diagnostic="summarizer",
        corrective_hint=None)
    ev.last_usage = {"total_tokens": 12, "completion_tokens": 6}
    monkeypatch.setattr("src.evaluation.closed_loop.in_loop_evaluator.InLoopEvaluator", lambda: ev)

    with patch.dict(os.environ, {"EVALUATOR_MODE": "full"}):
        from src.agents.building_flow_graph import build_building_flow_graph
        build_building_flow_graph().invoke(_st("A_full"))

    assert ev.route_plausible_voted.call_args.kwargs["about_to_request_address"] is False


def test_router_rewind_keeps_retrieval_instrumentation_in_a_cached_arm(base_env, monkeypatch):
    """A cached arm's specialists skip their queries and never re-set invoked_* / sql_fields_*.
    Clearing them on rewind would delete them for good, and the harness would then score the
    case as route 'clarification' with zero field coverage while evidence_present stayed true."""
    import src.agents.building_flow_graph as m
    cached = {"aggregated_data_cached": True, "corrective_hint": "Route to generic.",
              "invoked_generic_sql": True, "sql_fields_generic": ["energy_class"],
              "aggregated_data": {"generic_sql": [{"byggnadsid": "B1"}]}}
    out = m.rewind_to_router_node(cached)
    for k in ("invoked_generic_sql", "sql_fields_generic", "aggregated_data"):
        assert k not in out, f"{k} must not be cleared when the evidence is cached"
    assert out["done_generic_sql"] is None          # barrier still reset
    assert out["identity_gate_blocked"] is False    # stale gate must not re-fire

    live = dict(cached, aggregated_data_cached=False)
    out2 = m.rewind_to_router_node(live)
    assert out2["invoked_generic_sql"] is False and out2["aggregated_data"] == {}


def test_router_hint_reaches_the_parser_but_not_the_question(base_env, monkeypatch):
    """Fix 6: the hint must steer the intent parser, or the rewind is a silent no-op —
    and it must NOT end up in last_message, which the judge reads as the user's question."""
    import src.agents.building_flow_graph as m
    _wire_externals(monkeypatch)
    st = {"last_message": "Atemp of T 1?", "router_hint": "Route to generic instead.",
          "messages": [], "metadata": {"address": "T 1"}}

    out = m.understand_context_node(st)

    parsed_msg = m.parse_intent_agent.call_args.args[0]["last_message"]
    assert "Route to generic instead." in parsed_msg
    assert out["last_message"] == "Atemp of T 1?"      # question left untouched
    assert out.get("router_hint") is None              # consumed, never re-applied


def test_specialists_rewind_keeps_evidence_in_a_cached_arm(base_env, monkeypatch):
    """The §1.7 landmine: this node cleared aggregated_data unconditionally and un-set the
    cache flag. Unreachable today only because question_coverage is never the lowest axis —
    any attribution change would have armed it, so it is defused before that can happen."""
    import src.agents.building_flow_graph as m
    cached = {"aggregated_data_cached": True, "corrective_hint": "Retrieve more fields.",
              "invoked_generic_sql": True, "sql_fields_generic": ["energy_class"],
              "aggregated_data": {"generic_sql": [{"byggnadsid": "B1"}]}}
    out = m.rewind_to_specialists_node(cached)
    for k in ("invoked_generic_sql", "sql_fields_generic", "aggregated_data",
              "aggregated_data_cached"):
        assert k not in out, f"{k} must not be cleared when the evidence is cached"
    assert out["done_generic_sql"] is None            # barrier still reset
    assert out["eval_feedback"] == "Retrieve more fields."

    out2 = m.rewind_to_specialists_node(dict(cached, aggregated_data_cached=False))
    assert out2["invoked_generic_sql"] is False and out2["aggregated_data"] == {}


# --- v21 §2.2: the pipeline must stop overriding the evaluator -------------------------

def _personal_energy_state(**kw):
    """EKR_GEN_019's shape: a first-person energy question with no address."""
    return {"last_message": "How does my electricity use affect my electricity price?",
            "messages": [], "metadata": {}, **kw}


def _wire_vector_only_reparse(monkeypatch):
    """What the parser actually returns once it has seen the corrective hint (§1.3)."""
    import src.agents.building_flow_graph as m
    parser = MagicMock()
    parser.return_value = {"context": {
        "parsed_intent": "vector database", "intent_list": ["vector database"],
        "ambiguous": False, "ambigious": False}}
    monkeypatch.setattr(m, "parse_intent_agent", parser)


def test_router_rewind_records_the_rejected_route(base_env, monkeypatch):
    """The rewind records the rejected route so the router cannot re-promote it (v21 §2.2)."""
    import src.agents.building_flow_graph as m
    out = m.rewind_to_router_node({"top_route": "building", "corrective_hint": "Go generic."})
    assert out["rejected_route"] == "building"


def test_promoter_is_suppressed_after_the_evaluator_rejected_building(base_env, monkeypatch):
    """The v21 headline fix. The parser obeyed the hint and returned vector-only; the
    heuristic then re-promoted it to SQL and the rewind was wasted (EKR_GEN_019, 0/3)."""
    import src.agents.building_flow_graph as m
    _wire_externals(monkeypatch)
    _wire_vector_only_reparse(monkeypatch)
    st = _personal_energy_state(router_hint="Route to generic instead of building.",
                                rejected_route="building")

    out = m.understand_context_node(st)

    assert out["context"]["intent_list"] == ["vector database"]   # promotion did NOT happen
    assert out["top_route"] == "generic"
    assert out.get("forced_route_applied") is not True   # suppression alone was enough
    assert out.get("rejected_route") is None             # consumed


def test_promoter_still_fires_on_normal_traffic(base_env, monkeypatch):
    """The regression test that matters: without a rewind the heuristic is untouched, so
    ordinary users keep the behaviour it was written for."""
    import src.agents.building_flow_graph as m
    _wire_externals(monkeypatch)
    _wire_vector_only_reparse(monkeypatch)

    out = m.understand_context_node(_personal_energy_state())

    assert out["context"]["intent_list"] == ["SQL database", "vector database"]
    assert out["top_route"] == "building"


def test_promoter_still_fires_when_the_rejected_route_was_generic(base_env, monkeypatch):
    """Direction matters. Only building->generic is observed and tested; the reverse path
    keeps today's behaviour rather than shipping untested (§3)."""
    import src.agents.building_flow_graph as m
    _wire_externals(monkeypatch)
    _wire_vector_only_reparse(monkeypatch)
    st = _personal_energy_state(router_hint="Route to building instead.",
                                rejected_route="generic")

    out = m.understand_context_node(st)

    assert out["context"]["intent_list"] == ["SQL database", "vector database"]
    assert out.get("forced_route_applied") is not True


def test_stale_rejected_route_cannot_suppress_a_later_turn(base_env, monkeypatch):
    """The key is consumed in the pass that reads it, so it can never apply twice."""
    import src.agents.building_flow_graph as m
    _wire_externals(monkeypatch)
    _wire_vector_only_reparse(monkeypatch)
    st = _personal_energy_state(rejected_route="building")   # left over, but no hint

    out = m.understand_context_node(st)

    assert out["context"]["intent_list"] == ["SQL database", "vector database"]
    assert out.get("rejected_route") is None


def test_forced_route_is_the_safety_net_when_the_parser_re_picks_building(base_env, monkeypatch):
    """Suppression is not enough if the parser itself re-picks the rejected route: the one
    granted rewind would be wasted. `forced_route_applied` is what tells the two apart."""
    import src.agents.building_flow_graph as m
    _wire_externals(monkeypatch)
    _wire_generic_question_without_address(monkeypatch)   # parser insists on SQL
    st = _personal_energy_state(router_hint="Route to generic instead of building.",
                                rejected_route="building")

    out = m.understand_context_node(st)

    assert out["forced_route_applied"] is True
    assert out["top_route"] == "generic"
    assert out["context"]["intent_list"] == ["vector database"]   # the organic generic path


def test_backward_compatible(base_env, monkeypatch):
    """With EVALUATOR_MODE=off, the graph behaves exactly as the production pipeline."""
    _wire_externals(monkeypatch)
    with patch.dict(os.environ, {"EVALUATOR_MODE": "off"}):
        from src.agents.building_flow_graph import build_building_flow_graph
        g = build_building_flow_graph()
    assert g.invoke({"last_message": "Atemp?", "messages": [], "metadata": {"address": "T 1"}}).get("final_response")
