import os, pytest
from unittest.mock import MagicMock, patch

@pytest.fixture()
def base_env():
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

def _wire_judge(monkeypatch):
    ev = MagicMock()
    ev.route_plausible.return_value = MagicMock(
        verdict="plausible", axes={"intent_consistency":9,"precondition_satisfied":9}, corrective_hint=None)
    ev.answer_quality.return_value = MagicMock(
        verdict="pass", axes={"faithfulness":9,"answer_relevance":9,"question_coverage":9,"calibration":9},
        composite=9.0, stage_attribution_judge="summarizer", stage_attribution_rule="summarizer", corrective_hint=None)
    ev.last_usage = {"total_tokens": 12, "completion_tokens": 6}  # real dict, not a mock attr
    monkeypatch.setattr("src.evaluation.closed_loop.in_loop_evaluator.InLoopEvaluator", lambda: ev)

def _graph(monkeypatch, arm):
    _wire_externals(monkeypatch)
    if arm != "A_open":
        _wire_judge(monkeypatch)
    with patch.dict(os.environ, {
        "RUN_ARM": arm, "EVALUATOR_MODE": "off",
        "EARLY_CHECKPOINT_ENABLED": "true" if arm=="A_full" else "false",
        "LATE_CHECKPOINT_ENABLED": "true" if arm!="A_open" else "false",
    }):
        from src.agents.building_flow_graph import build_building_flow_graph
        return build_building_flow_graph()

def _st(arm):
    return {"last_message": "Atemp of T 1?", "messages": [], "metadata": {"address": "T 1"},
            "eval_arm": arm, "retry_budget": 2, "checkpoint_history": [],
            "controller_flags": {}, "attempt_records": [],
            "corrective_hint": None, "hint_target": None}

def test_open_runs(base_env, monkeypatch):
    assert _graph(monkeypatch, "A_open").invoke(_st("A_open")).get("final_response")

def test_late_only_runs(base_env, monkeypatch):
    assert _graph(monkeypatch, "A_late_only").invoke(_st("A_late_only")).get("final_response")

def test_full_runs(base_env, monkeypatch):
    assert _graph(monkeypatch, "A_full").invoke(_st("A_full")).get("final_response")

def test_backward_compatible(base_env, monkeypatch):
    """With RUN_ARM unset, the graph behaves exactly as the production pipeline."""
    _wire_externals(monkeypatch)
    with patch.dict(os.environ, {"RUN_ARM": "", "EVALUATOR_MODE": "off"}):
        from src.agents.building_flow_graph import build_building_flow_graph
        g = build_building_flow_graph()
    assert g.invoke({"last_message": "Atemp?", "messages": [], "metadata": {"address": "T 1"}}).get("final_response")
