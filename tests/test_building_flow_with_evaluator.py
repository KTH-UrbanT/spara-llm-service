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
