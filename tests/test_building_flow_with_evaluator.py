import importlib
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def flow_module():
    """Import building_flow_graph with heavy dependencies mocked out."""
    with patch.dict(
        os.environ,
        {
            "AZURE_ENDPOINT": "https://example.openai.azure.com",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME": "resp-model",
            "OPENAI_RESPONSE_MODEL_API_VERSION": "2024-02-15-preview",
            "EVALUATOR_ENABLED": "true",
            "EVALUATOR_MODE": "balanced",
            "EVALUATOR_MAX_RETRIES": "1",
            "EVALUATOR_PASS_THRESHOLD": "6",
        },
        clear=False,
    ):
        with ExitStack() as stack:
            stack.enter_context(patch("src.agents.parse_intent_agent.ParseIntentAgent", return_value=MagicMock()))
            stack.enter_context(patch("src.agents.generic_sql_layer.SQL_Mapper_Layer", return_value=MagicMock()))
            stack.enter_context(patch("src.agents.specialized_sql_layer.SpecializedSQLLayer", return_value=MagicMock()))
            stack.enter_context(patch("src.database.vector_client.VectorClientConfig", return_value=MagicMock()))
            stack.enter_context(patch("src.database.vector_client.VectorClient", return_value=MagicMock()))
            stack.enter_context(patch("src.agents.openai_agent.OpenAIResponseAgent", return_value=MagicMock()))
            stack.enter_context(patch("src.agents.evaluator_agent.EvaluatorAgent", return_value=MagicMock()))
            stack.enter_context(patch("src.database.hammarby_data.query_address", return_value=[]))

            module = importlib.import_module("src.agents.building_flow_graph")
            module = importlib.reload(module)
            yield module


def _base_state():
    return {
        "last_message": "What is the building EPC?",
        "aggregated_data": {"generic_sql": [{"epc": "A"}]},
        "messages": [],
        "metadata": {},
    }


def test_first_pass_pass(flow_module):
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "faithfulness_score": 8,
        "completeness_score": 7,
        "issues": [],
        "corrective_feedback": "",
    }

    state = _base_state()
    state.update(flow_module.llm_summarizer_node(state))
    state.update(flow_module.evaluate_response_node(state))

    route = flow_module.route_after_evaluation(state)

    assert route == "end"
    assert state["eval_verdict"] == "pass"
    assert state["eval_retry_count"] == 0


def test_first_fail_retry_then_pass(flow_module):
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.side_effect = ["Answer bad", "Answer fixed"]

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.evaluate.side_effect = [
        {
            "verdict": "fail",
            "faithfulness_score": 7,
            "completeness_score": 6,
            "issues": ["unsupported value"],
            "corrective_feedback": "Remove unsupported value and include EPC.",
        },
        {
            "verdict": "pass",
            "faithfulness_score": 8,
            "completeness_score": 8,
            "issues": [],
            "corrective_feedback": "",
        },
    ]

    state = _base_state()

    # 1st summarization + evaluation (fail)
    state.update(flow_module.llm_summarizer_node(state))
    state.update(flow_module.evaluate_response_node(state))
    assert flow_module.route_after_evaluation(state) == "retry_summarizer"

    # Retry summarization should include evaluator feedback in prompt.
    state.update(flow_module.llm_summarizer_node(state))
    second_prompt = flow_module.llm_summarizer.generate_response.call_args_list[1][0][0]
    assert "Remove unsupported value and include EPC." in second_prompt

    # 2nd evaluation (pass)
    state.update(flow_module.evaluate_response_node(state))
    assert flow_module.route_after_evaluation(state) == "end"
    assert state["eval_retry_count"] == 1


def test_both_fail_end_after_retry_cap(flow_module):
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.side_effect = ["Answer bad", "Answer still bad"]

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.evaluate.side_effect = [
        {
            "verdict": "fail",
            "faithfulness_score": 7,
            "completeness_score": 6,
            "issues": ["missing required numeric detail"],
            "corrective_feedback": "Include the exact numeric value from SQL.",
        },
        {
            "verdict": "fail",
            "faithfulness_score": 7,
            "completeness_score": 6,
            "issues": ["missing required numeric detail"],
            "corrective_feedback": "Still missing exact numeric value.",
        },
    ]

    state = _base_state()

    state.update(flow_module.llm_summarizer_node(state))
    state.update(flow_module.evaluate_response_node(state))
    assert flow_module.route_after_evaluation(state) == "retry_summarizer"

    state.update(flow_module.llm_summarizer_node(state))
    state.update(flow_module.evaluate_response_node(state))

    assert flow_module.route_after_evaluation(state) == "end"
    assert state["eval_retry_count"] == 1
    assert ((state.get("metadata") or {}).get("debug") or {}).get("eval_warning")


def test_low_composite_fail_skips_retry(flow_module):
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer bad"

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "fail",
        "faithfulness_score": 3,
        "completeness_score": 4,
        "issues": ["major omissions"],
        "corrective_feedback": "N/A",
    }

    state = _base_state()
    state.update(flow_module.llm_summarizer_node(state))
    state.update(flow_module.evaluate_response_node(state))

    assert flow_module.route_after_evaluation(state) == "end"


def test_evaluator_exception_fail_open(flow_module):
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.evaluate.side_effect = RuntimeError("evaluator crash")

    state = _base_state()
    state.update(flow_module.llm_summarizer_node(state))

    # Node itself should remain fail-open because evaluator agent handles errors upstream.
    # Simulate upstream fail-open return directly if exception bubbles from mock.
    with patch.object(
        flow_module.evaluator_agent,
        "evaluate",
        return_value={"verdict": "pass", "evaluator_failed": True, "evaluator_failure_reason": "boom"},
    ):
        state.update(flow_module.evaluate_response_node(state))

    assert flow_module.route_after_evaluation(state) == "end"
    assert state["eval_verdict"] == "pass"
    assert (state.get("eval_scores") or {}).get("evaluated") is False


def test_route_after_summarizer_returns_end_when_off(flow_module):
    """Section 0.9: route_after_summarizer must return 'end' (not 'evaluate_response')
    when EVALUATOR_MODE=off. This is the production-side guarantee that arm A1
    incurs zero evaluator overhead — the graph never enters evaluate_response_node.
    """
    state = _base_state()
    with patch.dict(os.environ, {"EVALUATOR_MODE": "off"}, clear=False):
        assert flow_module.route_after_summarizer(state) == "end"
    with patch.dict(os.environ, {"EVALUATOR_MODE": "balanced"}, clear=False):
        assert flow_module.route_after_summarizer(state) == "evaluate_response"
    with patch.dict(os.environ, {"EVALUATOR_MODE": "strict"}, clear=False):
        assert flow_module.route_after_summarizer(state) == "evaluate_response"


def test_evaluator_bypass_routes_directly_to_end(flow_module):
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer without evaluator"

    flow_module.evaluator_agent = MagicMock()

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATOR_MODE": "off"}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))

        assert flow_module.route_after_summarizer(state) == "end"
        assert ((state.get("metadata") or {}).get("debug") or {}).get("evaluator_bypassed") is True
        flow_module.evaluator_agent.evaluate.assert_not_called()


def test_evaluation_trace_is_written(flow_module, tmp_path):
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "faithfulness_score": 8,
        "completeness_score": 8,
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    trace_jsonl = Path(tmp_path) / "evaluation_traces.jsonl"
    trace_csv = Path(tmp_path) / "evaluation_traces.csv"

    assert trace_jsonl.exists()
    assert trace_csv.exists()

    trace_records = [json.loads(line) for line in trace_jsonl.read_text().splitlines() if line.strip()]
    assert trace_records
    assert trace_records[-1]["evaluation_status"] == "evaluated"
    assert trace_records[-1]["evaluated"] is True


def test_question_id_run_id_arm_in_trace(flow_module, tmp_path):
    """Section 0.3: every trace record must carry the experiment identifier fields
    (question_id, run_id, arm, attempt_index, dataset_version) so the analysis
    script can join records across the four arms.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"

    flow_module.evaluator_agent = MagicMock()
    # Defensive: set string attributes to make the trace JSON-serializable.
    # See _get_evaluator_deployment_label / _get_evaluator_prompt_version helpers.
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "faithfulness_score": 8,
        "completeness_score": 8,
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
    }

    state = _base_state()
    # Inject experiment identifiers via metadata + env vars exactly as the runner will.
    state["metadata"] = {
        "question_id": "Q017",
        "dataset_version": "1.0",
    }

    env_overrides = {
        "EVALUATION_TRACE_DIR": str(tmp_path),
        "EXPERIMENT_RUN_ID": "2026-05-04_test_run",
        "EXPERIMENT_ARM": "A2",
    }
    with patch.dict(os.environ, env_overrides, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    trace_jsonl = Path(tmp_path) / "evaluation_traces.jsonl"
    assert trace_jsonl.exists(), "Trace JSONL was not written."

    records = [json.loads(line) for line in trace_jsonl.read_text().splitlines() if line.strip()]
    assert records, "No trace records found."
    last = records[-1]

    assert last["trace_schema_version"] == 2, "Schema version was not bumped to 2."
    assert last["question_id"] == "Q017"
    assert last["dataset_version"] == "1.0"
    assert last["run_id"] == "2026-05-04_test_run"
    assert last["arm"] == "A2"
    assert last["attempt_index"] == 0  # First attempt — no retry yet.
    # Sanity: the trace also contains the version label as a basename, not a full path.
    assert last["prompt_version"] == "evaluator_prompt.txt"
    assert last["model_deployment"] == "test-deployment"


def test_trace_identifiers_default_to_none_outside_experiment(flow_module, tmp_path):
    """Section 0.3: when run outside of an experiment (no env vars, no question_id),
    the identifier fields are None. Lets the analysis script filter non-experimental traces.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "faithfulness_score": 8,
        "completeness_score": 8,
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
    }

    state = _base_state()  # No question_id in metadata.

    # Strip any experiment env vars that may exist from .env.
    env_clean = {k: v for k, v in os.environ.items() if k not in ("EXPERIMENT_RUN_ID", "EXPERIMENT_ARM")}
    env_clean["EVALUATION_TRACE_DIR"] = str(tmp_path)
    with patch.dict(os.environ, env_clean, clear=True):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    records = [json.loads(line) for line in (Path(tmp_path) / "evaluation_traces.jsonl").read_text().splitlines() if line.strip()]
    last = records[-1]
    assert last["question_id"] is None
    assert last["run_id"] is None
    assert last["arm"] is None
    assert last["dataset_version"] is None
    assert last["attempt_index"] == 0


def test_latency_and_token_usage_in_trace(flow_module, tmp_path):
    """Section 0.4: every trace record must contain summarizer_latency_ms,
    evaluator_latency_ms, and the corresponding token_usage dicts. Without
    these, the thesis cannot report cost/latency overhead per arm.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    # Side-channel meta that the node reads after generate_response().
    flow_module.llm_summarizer._last_call_meta = {
        "latency_ms": 1234,
        "token_usage": {"prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600},
    }

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "faithfulness_score": 8,
        "completeness_score": 8,
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
        # Section 0.4: per-call meta on the result.
        "_latency_ms": 567,
        "_token_usage": {"prompt_tokens": 800, "completion_tokens": 50, "total_tokens": 850},
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    # State carries the meta forward (so a runner can sum across attempts).
    assert state.get("summarizer_latency_ms") == 1234
    assert state.get("summarizer_token_usage") == {
        "prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600,
    }
    assert state.get("evaluator_latency_ms") == 567
    assert state.get("evaluator_token_usage") == {
        "prompt_tokens": 800, "completion_tokens": 50, "total_tokens": 850,
    }

    records = [json.loads(line) for line in (Path(tmp_path) / "evaluation_traces.jsonl").read_text().splitlines() if line.strip()]
    last = records[-1]
    assert last["summarizer_latency_ms"] == 1234
    assert last["evaluator_latency_ms"] == 567
    assert last["summarizer_token_usage"] == {
        "prompt_tokens": 500, "completion_tokens": 100, "total_tokens": 600,
    }
    assert last["evaluator_token_usage"] == {
        "prompt_tokens": 800, "completion_tokens": 50, "total_tokens": 850,
    }


def test_bypass_trace_has_zero_evaluator_latency(flow_module, tmp_path):
    """Section 0.4: in the bypass path (mode=off), the evaluator is never called,
    so its latency must be 0 and token_usage None — *not* missing/null. Lets the
    analysis script compute overhead = balanced_latency - off_latency cleanly.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {
        "latency_ms": 999,
        "token_usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"

    state = _base_state()
    env_overrides = {
        "EVALUATION_TRACE_DIR": str(tmp_path),
        "EVALUATOR_MODE": "off",
    }
    with patch.dict(os.environ, env_overrides, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    records = [json.loads(line) for line in (Path(tmp_path) / "evaluation_traces.jsonl").read_text().splitlines() if line.strip()]
    last = records[-1]
    assert last["evaluation_status"] == "bypassed"
    assert last["summarizer_latency_ms"] == 999
    assert last["evaluator_latency_ms"] == 0
    assert last["evaluator_token_usage"] is None
    flow_module.evaluator_agent.evaluate.assert_not_called()


def test_aggregated_data_present_in_trace(flow_module, tmp_path):
    """Section 0.5: the trace must capture the SQL/vector evidence the evaluator saw.
    Without this, human annotators cannot verify groundedness — they would be scoring
    against memory/imagination rather than the actual context.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 100, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "faithfulness_score": 8,
        "completeness_score": 8,
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
        "_latency_ms": 50,
        "_token_usage": None,
    }

    state = _base_state()
    state["aggregated_data"] = {
        "generic_sql": [
            {"address": "Hammarby Gata 10", "declaredEnergyClass": "B", "numberOfApartments": 24},
            {"address": "Hammarby Gata 12", "declaredEnergyClass": "C", "numberOfApartments": 18},
        ],
        "specialized_sql": [{"building_id": 4521, "energy_kwh_m2": 95.7}],
        "vector": {
            "sources": ["doc-001.pdf", "doc-002.pdf"],
            "snippets": ["Energy class B requires...", "Recent retrofits in Hammarby..."],
        },
    }

    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    records = [json.loads(line) for line in (Path(tmp_path) / "evaluation_traces.jsonl").read_text().splitlines() if line.strip()]
    last = records[-1]
    assert "aggregated_data" in last, "Trace missing aggregated_data field."
    agg = last["aggregated_data"]
    assert agg["generic_sql"][0]["address"] == "Hammarby Gata 10"
    assert agg["generic_sql"][0]["declaredEnergyClass"] == "B"
    assert agg["specialized_sql"][0]["building_id"] == 4521
    assert agg["vector"]["sources"] == ["doc-001.pdf", "doc-002.pdf"]


def test_aggregated_data_truncated_in_trace(flow_module, tmp_path):
    """Section 0.5: long string values inside aggregated_data are truncated to
    keep individual JSONL lines bounded. Vector store snippets can exceed 50 KB;
    we cap at ~4000 chars and append a truncation marker.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 100, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "faithfulness_score": 8,
        "completeness_score": 8,
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
        "_latency_ms": 50,
        "_token_usage": None,
    }

    huge_snippet = "X" * 10000  # 10 KB string — clearly above the 4000 cap.
    state = _base_state()
    state["aggregated_data"] = {
        "vector": {
            "snippets": [huge_snippet, "small one"],
        },
    }

    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    records = [json.loads(line) for line in (Path(tmp_path) / "evaluation_traces.jsonl").read_text().splitlines() if line.strip()]
    truncated = records[-1]["aggregated_data"]["vector"]["snippets"][0]
    short = records[-1]["aggregated_data"]["vector"]["snippets"][1]
    assert len(truncated) <= 4100, f"First snippet was not truncated: len={len(truncated)}"
    assert truncated.endswith("…[truncated]"), "Truncation marker missing."
    assert short == "small one", "Short string should not be modified."


def test_score_fallbacks_applied_field_records_dimensions(flow_module, tmp_path):
    """Section 0.7: when the LLM omits a dimension and the code synthesizes it
    from another axis (e.g., numeric_fidelity ← groundedness), the dimension
    name is appended to `eval_scores.score_fallbacks_applied`. Without this,
    per-axis thesis claims would be silently inflated by synthetic values.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 100, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    # Sloppy LLM output: only groundedness + completeness, missing the other 3 dims.
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "groundedness_score": 8,
        "completeness_score": 7,
        # numeric_fidelity_score, constraint_satisfaction_score, uncertainty_calibration_score MISSING
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
        "_latency_ms": 50,
        "_token_usage": None,
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    scores = state["eval_scores"]
    fallbacks = scores["score_fallbacks_applied"]
    # All three cross-dimension synthesizes fired:
    assert "numeric_fidelity_score" in fallbacks  # synthesized from groundedness
    assert "constraint_satisfaction_score" in fallbacks  # synthesized from completeness
    assert "uncertainty_calibration_score" in fallbacks  # synthesized from completeness
    # The values themselves are the synthesized ones (8 from groundedness, 7 from completeness):
    assert scores["numeric_fidelity_score"] == 8
    assert scores["constraint_satisfaction_score"] == 7
    assert scores["uncertainty_calibration_score"] == 7


def test_score_fallbacks_empty_when_all_dimensions_present(flow_module, tmp_path):
    """Section 0.7: clean LLM output with all 5 dimensions → empty fallback list.
    These are the rows the thesis can use for per-axis claims.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 100, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "groundedness_score": 9,
        "faithfulness_score": 9,
        "completeness_score": 8,
        "numeric_fidelity_score": 9,
        "constraint_satisfaction_score": 8,
        "uncertainty_calibration_score": 8,
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
        "_latency_ms": 50,
        "_token_usage": None,
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    assert state["eval_scores"]["score_fallbacks_applied"] == []


def test_faithfulness_groundedness_alias_not_recorded_as_fallback(flow_module, tmp_path):
    """Section 0.7: faithfulness ↔ groundedness is an alias (prompt enforces equality),
    not a real cross-dimension synthesis. It must NOT appear in score_fallbacks_applied.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 100, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    # LLM omits faithfulness_score (legacy field) but provides everything else.
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        # "faithfulness_score": MISSING — should fall back to groundedness silently
        "groundedness_score": 9,
        "completeness_score": 8,
        "numeric_fidelity_score": 9,
        "constraint_satisfaction_score": 8,
        "uncertainty_calibration_score": 8,
        "issues": [],
        "corrective_feedback": "",
        "evaluator_failed": False,
        "evaluator_failure_reason": "",
        "_latency_ms": 50,
        "_token_usage": None,
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    fallbacks = state["eval_scores"]["score_fallbacks_applied"]
    # Faithfulness was synthesized but it's an alias, so we don't pollute the filter.
    assert "faithfulness_score" not in fallbacks
    assert state["eval_scores"]["faithfulness_score"] == 9


def test_summarizer_temperature_in_trace(flow_module, tmp_path):
    """Section 0.8: temperature_requested + temperature_unsupported flow into the trace.
    The thesis manifest pins SUMMARIZER_TEMPERATURE=0.0; this trace field is the
    audit trail confirming the run actually used that value.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {
        "latency_ms": 100,
        "token_usage": None,
        "temperature_requested": 0.0,
        "temperature_unsupported": False,
    }

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "groundedness_score": 9, "faithfulness_score": 9, "completeness_score": 8,
        "numeric_fidelity_score": 9, "constraint_satisfaction_score": 8,
        "uncertainty_calibration_score": 8,
        "issues": [], "corrective_feedback": "",
        "evaluator_failed": False, "evaluator_failure_reason": "",
        "_latency_ms": 50, "_token_usage": None,
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    records = [json.loads(line) for line in (Path(tmp_path) / "evaluation_traces.jsonl").read_text().splitlines() if line.strip()]
    last = records[-1]
    assert last["summarizer_temperature_requested"] == 0.0
    assert last["summarizer_temperature_unsupported"] is False


def test_summarizer_temperature_unsupported_flag_in_trace(flow_module, tmp_path):
    """Section 0.8: when the deployment rejects temperature, the fallback path
    sets temperature_unsupported=True. This is the audit signal that says
    'this row's quality may have extra variance — model default applied'.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {
        "latency_ms": 100,
        "token_usage": None,
        "temperature_requested": 0.0,
        "temperature_unsupported": True,  # The deployment rejected it.
    }

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/to/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "groundedness_score": 9, "faithfulness_score": 9, "completeness_score": 8,
        "numeric_fidelity_score": 9, "constraint_satisfaction_score": 8,
        "uncertainty_calibration_score": 8,
        "issues": [], "corrective_feedback": "",
        "evaluator_failed": False, "evaluator_failure_reason": "",
        "_latency_ms": 50, "_token_usage": None,
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATION_TRACE_DIR": str(tmp_path)}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    records = [json.loads(line) for line in (Path(tmp_path) / "evaluation_traces.jsonl").read_text().splitlines() if line.strip()]
    assert records[-1]["summarizer_temperature_unsupported"] is True


def test_composite_floor_rejects_when_axes_high(flow_module):
    """Section 0.11: scores that pass every per-axis gate can still fail the composite.

    Strict thresholds: per-axis (groundedness=9, numeric=9, constraint=8, completeness=8)
    AND composite_min=8.7. With g=9, nf=9, cs=8, c=8, uc=0:
    composite = 0.35·9 + 0.25·9 + 0.20·8 + 0.15·8 + 0.05·0 = 8.2 (< 8.7) → FAIL.
    Confirms the composite gate is independent of per-axis gates.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 10, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",  # LLM said pass — code must override based on composite.
        "groundedness_score": 9, "faithfulness_score": 9,
        "completeness_score": 8, "numeric_fidelity_score": 9,
        "constraint_satisfaction_score": 8, "uncertainty_calibration_score": 0,
        "issues": [], "corrective_feedback": "",
        "evaluator_failed": False, "evaluator_failure_reason": "",
        "_latency_ms": 10, "_token_usage": None,
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATOR_MODE": "strict"}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    composite = state["eval_scores"]["composite_score"]
    assert composite < 8.7, f"Test setup wrong: composite={composite} should be below strict threshold 8.7"
    # Every per-axis is at-or-above strict mins. Only the composite forces fail.
    assert state["eval_verdict"] == "fail", (
        "Composite-floor gate did not fire. The thesis claim that 'composite is "
        "an independent gate' would not be defensible without this enforcement."
    )


def test_hard_fail_true_with_high_scores_still_fails(flow_module):
    """Section 0.11: hard_fail=true must override even all-high scores.

    Without this enforcement, a single critical hallucination could be 'passed' by
    composite + per-axis gates and the evaluator's hard-fail rule would be advisory.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 10, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",  # LLM said pass — must be overridden.
        "groundedness_score": 9, "faithfulness_score": 9,
        "completeness_score": 9, "numeric_fidelity_score": 9,
        "constraint_satisfaction_score": 9, "uncertainty_calibration_score": 9,
        "hard_fail": True,  # The override.
        "hard_fail_reason": "Numeric contradiction with aggregated data.",
        "issues": [], "corrective_feedback": "Use the SQL value verbatim.",
        "evaluator_failed": False, "evaluator_failure_reason": "",
        "_latency_ms": 10, "_token_usage": None,
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATOR_MODE": "balanced"}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

    assert state["eval_verdict"] == "fail"
    assert state["eval_scores"]["hard_fail"] is True
    # Hard-fail also disables retry candidacy (per evaluate_response_node logic).
    assert state["eval_scores"]["retry_candidate"] is False, (
        "hard_fail=true must NOT trigger a retry — the answer has a critical "
        "defect that one re-summarization cannot fix."
    )


def test_retry_attempted_at_retry_lower_boundary(flow_module):
    """Section 0.11: composite *exactly* at retry_lower must trigger retry.

    Off-by-one protection. The retry-band rule is `retry_lower ≤ composite < composite_min`,
    so the boundary value is INCLUSIVE on the lower side. Otherwise the analysis
    script's retry_attempt_rate could be off by ~1/40 = 2.5 percentage points.

    Balanced thresholds: retry_lower=6.0, composite_min=7.2. With all axes=6:
    composite = 0.35·6 + 0.25·6 + 0.20·6 + 0.15·6 + 0.05·6 = 6.0 (== retry_lower).
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 10, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "fail",
        "groundedness_score": 6, "faithfulness_score": 6,
        "completeness_score": 6, "numeric_fidelity_score": 6,
        "constraint_satisfaction_score": 6, "uncertainty_calibration_score": 6,
        "issues": ["below per-axis minimums"], "corrective_feedback": "Tighten claims.",
        "evaluator_failed": False, "evaluator_failure_reason": "",
        "_latency_ms": 10, "_token_usage": None,
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATOR_MODE": "balanced"}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

        composite = state["eval_scores"]["composite_score"]
        assert composite == 6.0, f"Test setup wrong: composite={composite} should be exactly 6.0"
        assert state["eval_scores"]["retry_candidate"] is True, (
            f"Composite={composite} == retry_lower=6.0 must be retry-eligible. "
            "If this fails, the retry-band lower bound is exclusive, not inclusive — "
            "analysis script's retry_attempt_rate is silently off."
        )
        # And the router agrees:
        assert flow_module.route_after_evaluation(state) == "retry_summarizer"


def test_balanced_accepts_what_strict_rejects(flow_module):
    """Section 0.11: identical scores produce different verdicts under different modes.

    The dual-direction proof of the strict/balanced trade-off the thesis claims.
    Scores all=8: composite=8.0, per-axis all=8.
    - Strict (per-axis min=9, composite_min=8.7): every per-axis fails AND composite fails → fail.
    - Balanced (per-axis min=7, composite_min=7.2): all gates clear → pass.
    """
    def _make_eval_return():
        return {
            "verdict": "pass",
            "groundedness_score": 8, "faithfulness_score": 8,
            "completeness_score": 8, "numeric_fidelity_score": 8,
            "constraint_satisfaction_score": 8, "uncertainty_calibration_score": 8,
            "issues": [], "corrective_feedback": "",
            "evaluator_failed": False, "evaluator_failure_reason": "",
            "_latency_ms": 10, "_token_usage": None,
        }

    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 10, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/evaluator_prompt.txt"

    # Direction 1: BALANCED accepts.
    flow_module.evaluator_agent.evaluate.return_value = _make_eval_return()
    state_balanced = _base_state()
    with patch.dict(os.environ, {"EVALUATOR_MODE": "balanced"}, clear=False):
        state_balanced.update(flow_module.llm_summarizer_node(state_balanced))
        state_balanced.update(flow_module.evaluate_response_node(state_balanced))
    assert state_balanced["eval_verdict"] == "pass", (
        "Balanced mode must accept all-axes=8 / composite=8.0 (≥ 7.2)."
    )

    # Direction 2: STRICT rejects the EXACT same scores.
    flow_module.evaluator_agent.evaluate.return_value = _make_eval_return()
    state_strict = _base_state()
    with patch.dict(os.environ, {"EVALUATOR_MODE": "strict"}, clear=False):
        state_strict.update(flow_module.llm_summarizer_node(state_strict))
        state_strict.update(flow_module.evaluate_response_node(state_strict))
    assert state_strict["eval_verdict"] == "fail", (
        "Strict mode must reject all-axes=8 (groundedness=8 < strict_min=9). "
        "If both modes return the same verdict, arms A2 vs A3 are identical and "
        "the strict/balanced thesis claim collapses."
    )


def test_trace_has_two_records_on_retry(flow_module, tmp_path):
    """Section 0.11: a fail→retry→pass sequence writes 2 trace records, with
    `attempt_index=0` for the first eval and `attempt_index=1` for the post-retry.

    Without this guarantee, the analysis script cannot compute retry_rescue_rate
    reliably — there'd be no way to identify the first-attempt verdict.
    """
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.side_effect = ["Answer bad", "Answer fixed"]
    flow_module.llm_summarizer._last_call_meta = {"latency_ms": 10, "token_usage": None}

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.prompt_path = "/abs/path/evaluator_prompt.txt"
    flow_module.evaluator_agent.evaluate.side_effect = [
        # First eval: in retry band (composite ≥ retry_lower) so retry triggers.
        {
            "verdict": "fail",
            "groundedness_score": 7, "faithfulness_score": 7,
            "completeness_score": 7, "numeric_fidelity_score": 6,
            "constraint_satisfaction_score": 7, "uncertainty_calibration_score": 7,
            "issues": ["one numeric mismatch"], "corrective_feedback": "Fix the kWh value.",
            "evaluator_failed": False, "evaluator_failure_reason": "",
            "_latency_ms": 10, "_token_usage": None,
        },
        # Second eval (after retry): pass.
        {
            "verdict": "pass",
            "groundedness_score": 9, "faithfulness_score": 9,
            "completeness_score": 8, "numeric_fidelity_score": 9,
            "constraint_satisfaction_score": 8, "uncertainty_calibration_score": 8,
            "issues": [], "corrective_feedback": "",
            "evaluator_failed": False, "evaluator_failure_reason": "",
            "_latency_ms": 10, "_token_usage": None,
        },
    ]

    state = _base_state()
    state["metadata"] = {"question_id": "Q-retry-trace"}
    env_overrides = {
        "EVALUATION_TRACE_DIR": str(tmp_path),
        "EVALUATOR_MODE": "balanced",
        "EXPERIMENT_RUN_ID": "retry-trace-run",
        "EXPERIMENT_ARM": "A2",
    }
    with patch.dict(os.environ, env_overrides, clear=False):
        # Pass 1: summarizer → evaluate (FAIL with retry_candidate).
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))
        assert state["eval_verdict"] == "fail"
        assert flow_module.route_after_evaluation(state) == "retry_summarizer", (
            "Test setup: first-pass scores must be in retry band so retry actually fires."
        )

        # Pass 2: summarizer (with feedback → retry_count increments) → evaluate (PASS).
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))
        assert state["eval_verdict"] == "pass"
        assert flow_module.route_after_evaluation(state) == "end"

    records = [
        json.loads(line)
        for line in (Path(tmp_path) / "evaluation_traces.jsonl").read_text().splitlines()
        if line.strip()
    ]
    # Filter to this question only (in case other tests wrote to the same dir).
    records = [r for r in records if r.get("question_id") == "Q-retry-trace"]
    assert len(records) == 2, f"Expected 2 trace records for retry sequence, got {len(records)}"

    by_attempt = {r["attempt_index"]: r for r in records}
    assert 0 in by_attempt and 1 in by_attempt, "Missing attempt_index=0 or =1 in trace records."

    assert by_attempt[0]["verdict"] == "fail"
    assert by_attempt[1]["verdict"] == "pass"
    # Both records carry the same question_id and arm — that's the join key analysis uses.
    assert by_attempt[0]["arm"] == "A2" and by_attempt[1]["arm"] == "A2"
    assert by_attempt[0]["run_id"] == "retry-trace-run" and by_attempt[1]["run_id"] == "retry-trace-run"


def test_runner_uses_cached_aggregated_data(flow_module):
    """Section 0.12: when state["aggregated_data_cached"]=True (set by the runner
    via --cache-from), the parallel agents must SKIP all external calls. This
    isolates evaluator effects from upstream-pipeline noise — arms A2/A3/A4
    operate on byte-identical SQL/vector evidence cached from arm A1's run.

    Without this bypass, every arm would re-query the SQL/vector DBs, and a
    single non-deterministic SQL response (e.g., a vector store with float ties)
    would inject confounding noise into the per-arm comparison.
    """
    # The flow_module fixture already mocks the SQL/vector layers. We assert
    # that no .execute() was called on them when the cache flag is set.
    flow_module.sql_mapper_layer = MagicMock()
    flow_module.specialized_sql_layer = MagicMock()
    flow_module.vector_client = MagicMock()

    cached_evidence = {
        "generic_sql": [{"address": "Hammarby Gata 10", "declaredEnergyClass": "B"}],
        "specialized_sql": [{"building_id": 4521, "energy_kwh_m2": 95.7}],
        "vector": {"sources": ["doc-001.pdf"], "snippets": ["Energy class B requires..."]},
    }

    state = {
        "last_message": "What is the energy class?",
        "aggregated_data": cached_evidence,  # Pre-populated by the runner.
        "aggregated_data_cached": True,       # The bypass flag.
        "messages": [],
        "metadata": {"question_id": "Q017", "address": "Hammarby Gata 10"},
        "parallel": {"active": True, "required": {"generic_sql": True, "specialized_sql": True, "vector": True}},
    }

    # All three parallel agents run; each must short-circuit.
    update_g = flow_module.generic_sql_agent_node(state)
    update_s = flow_module.specialized_sql_agent_node(state)
    update_v = flow_module.vector_db_agent_node(state)

    # Each agent marks itself done so wait_for_replies passes.
    assert update_g == {"done_generic_sql": True}
    assert update_s == {"done_specialized_sql": True}
    assert update_v == {"done_vector": True}

    # Critical: no external calls were issued. This is the property that makes
    # arms A2/A3/A4 operate on byte-identical evidence.
    flow_module.sql_mapper_layer.execute.assert_not_called()
    flow_module.specialized_sql_layer.execute.assert_not_called()
    # VectorClient typically has a search method — assert no methods called.
    assert flow_module.vector_client.method_calls == []

    # Aggregator preserves the cached evidence (deep-merge with empty new data
    # returns the cached value untouched).
    state.update(update_g)
    state.update(update_s)
    state.update(update_v)
    agg_update = flow_module.aggregator_node(state)
    assert agg_update.get("aggregated_data") == cached_evidence, (
        "Aggregator clobbered the cached aggregated_data. The deep-merge logic "
        "must preserve cached values when merged_data is empty."
    )


def test_runner_without_cache_flag_runs_normally(flow_module):
    """Section 0.12 negative test: without aggregated_data_cached=True, the
    parallel agents proceed normally (the bypass must NOT fire by default).
    Otherwise arm A1 (cache-populating run) would skip its own pipeline.
    """
    flow_module.sql_mapper_layer = MagicMock()
    flow_module.sql_mapper_layer.execute.return_value = {"ok": True, "data": [{"a": 1}]}

    state = {
        "last_message": "What is the energy class?",
        "messages": [],
        "metadata": {"address": "Hammarby Gata 10"},
        # NOTE: aggregated_data_cached NOT set.
    }

    flow_module.generic_sql_agent_node(state)

    # The bypass must NOT have fired — the SQL layer was queried.
    assert flow_module.sql_mapper_layer.execute.called, (
        "Generic SQL agent did not query when aggregated_data_cached was absent. "
        "Arm A1 would never populate the cache; the chain breaks."
    )


def test_strict_mode_enforces_high_precision_thresholds(flow_module):
    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Answer v1"

    flow_module.evaluator_agent = MagicMock()
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "groundedness_score": 9,
        "faithfulness_score": 9,
        "completeness_score": 8,
        "numeric_fidelity_score": 8,
        "constraint_satisfaction_score": 8,
        "uncertainty_calibration_score": 8,
        "issues": [],
        "corrective_feedback": "Add exact numeric value from SQL result.",
    }

    state = _base_state()
    with patch.dict(os.environ, {"EVALUATOR_MODE": "strict"}, clear=False):
        state.update(flow_module.llm_summarizer_node(state))
        state.update(flow_module.evaluate_response_node(state))

        assert state["eval_verdict"] == "fail"
        assert (state.get("eval_scores") or {}).get("mode") == "strict"
        assert flow_module.route_after_evaluation(state) == "retry_summarizer"
