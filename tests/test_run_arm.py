"""Tests for the offline batch runner (`scripts/run_arm.py`).

These tests use mocked summarizer + evaluator (no Azure calls). The `flow_module`
fixture mocks the heavy dependencies at module load time so that
`build_building_flow_graph()` returns a graph whose summarizer and evaluator
nodes call MagicMocks instead of real LLMs.

Test coverage matches plan-eil-v1.md §3.G:
  - test_run_arm_smoke_a2          : results + cache + traces are written
  - test_run_arm_smoke_a1_writes_synthetic_trace : A1 bypasses evaluator but
                                                   still produces traces
  - test_run_arm_resume_skips_completed : pre-existing rows in results.jsonl
                                          are skipped on a second invocation
  - test_run_arm_uses_cache_from   : SQL/vector layers are NOT called when
                                     --cache-from is set
  - test_run_arm_env_vars_set_before_import : setup_environment() really does
                                              write env BEFORE the runner imports
                                              the graph module
"""
from __future__ import annotations

import importlib
import json
import os
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def flow_module():
    """Import building_flow_graph with heavy dependencies mocked out.

    Mirrors the fixture in test_building_flow_with_evaluator.py so that
    test_run_arm tests can exercise the runner end-to-end without any real
    Azure / SQL / vector calls. The returned module's `llm_summarizer` and
    `evaluator_agent` are MagicMocks that tests can configure per-scenario.
    """
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
            stack.enter_context(patch("src.agents.parse_intent_agent.ParseIntentAgent",
                                      return_value=MagicMock()))
            stack.enter_context(patch("src.agents.generic_sql_layer.SQL_Mapper_Layer",
                                      return_value=MagicMock()))
            stack.enter_context(patch("src.agents.specialized_sql_layer.SpecializedSQLLayer",
                                      return_value=MagicMock()))
            stack.enter_context(patch("src.database.vector_client.VectorClientConfig",
                                      return_value=MagicMock()))
            stack.enter_context(patch("src.database.vector_client.VectorClient",
                                      return_value=MagicMock()))
            stack.enter_context(patch("src.agents.openai_agent.OpenAIResponseAgent",
                                      return_value=MagicMock()))
            stack.enter_context(patch("src.agents.evaluator_agent.EvaluatorAgent",
                                      return_value=MagicMock()))
            stack.enter_context(patch("src.database.hammarby_data.query_address",
                                      return_value=[]))

            module = importlib.import_module("src.agents.building_flow_graph")
            module = importlib.reload(module)
            yield module


def _make_dataset(tmp_path: Path, n: int = 2) -> Path:
    """Write a minimal dataset file with `n` simple questions."""
    questions = []
    for i in range(1, n + 1):
        questions.append({
            "question_id": f"Q{i:03d}",
            "category": "simple_address",
            "difficulty": "easy",
            "question": f"What is the energy class of building {i}?",
            "hint_address": None,
            "expected_facts": [],
            "expected_constraints": [],
            "must_avoid": [],
            "notes": "test fixture",
            "created_at": "2026-05-07T00:00:00Z",
        })
    dataset = {
        "dataset_version": "test-1.0",
        "created_at": "2026-05-07T00:00:00Z",
        "description": "test",
        "questions": questions,
    }
    path = tmp_path / "questions.json"
    path.write_text(json.dumps(dataset, ensure_ascii=False))
    return path


def _configure_passing_evaluator(flow_module):
    """Wire the mocked summarizer + evaluator to return a clean PASS verdict.

    Same pattern used by tests in test_building_flow_with_evaluator.py.
    """
    # The intent parser and SQL layer must return REAL values, not bare MagicMocks.
    # `assess_building_identity` needs exactly one candidate building id to return
    # status "passed"; a MagicMock yields none, so the identity gate blocks and the
    # graph exits via clarification -> END, never reaching the summarizer or the
    # evaluator. (That is what these tests assert on.)
    flow_module.parse_intent_agent = MagicMock(return_value={"context": {
        "parsed_intent": "SQL database", "intent_list": ["SQL database"],
        "address": "Testgatan 1", "ambiguous": False, "ambigious": False}})
    flow_module.sql_mapper_layer = MagicMock()
    flow_module.sql_mapper_layer.execute.return_value = {
        "ok": True,
        "data": [{"byggnadsid": "B1", "address": "Testgatan 1", "energy_class": "A"}],
        "message": "ok",
        "trace": {"query_type": "generic_sql", "execution_status": "success",
                  "rows_returned": 1, "match_strategy": "eq",
                  "returned_values_used": {"byggnadsid": "B1"}},
    }

    flow_module.llm_summarizer = MagicMock()
    flow_module.llm_summarizer.generate_response.return_value = "Mocked answer."
    flow_module.llm_summarizer._last_call_meta = {
        "latency_ms": 100,
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "temperature_requested": 0.0,
        "temperature_unsupported": False,
    }

    flow_module.evaluator_agent = MagicMock()
    # The agent has a `prompt_path` attribute used by _get_evaluator_prompt_version.
    # Setting an explicit string keeps the trace's prompt_version JSON-serializable.
    flow_module.evaluator_agent.prompt_path = "evaluator_prompt.txt"
    flow_module.evaluator_agent.deployment = "test-deployment"
    flow_module.evaluator_agent.evaluate.return_value = {
        "verdict": "pass",
        "groundedness_score": 9,
        "completeness_score": 9,
        "numeric_fidelity_score": 9,
        "constraint_satisfaction_score": 9,
        "uncertainty_calibration_score": 9,
        "faithfulness_score": 9,
        "issues": [],
        "corrective_feedback": "",
        "_latency_ms": 50,
        "_token_usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
    }


def _build_args(arm: str, dataset: Path, run_id: str, output_dir: Path,
                cache_from: Path = None, limit: int = None):
    """Construct an argparse.Namespace equivalent to a real CLI invocation."""
    import argparse
    return argparse.Namespace(
        arm=arm,
        dataset=dataset,
        run_id=run_id,
        output_dir=output_dir,
        cache_from=cache_from,
        limit=limit,
    )


def test_run_arm_smoke_a2(flow_module, tmp_path, monkeypatch):
    """Plan §3.G case 1: A2 over a 2-question fixture writes results, cache,
    and traces with the right shape.
    """
    _configure_passing_evaluator(flow_module)

    dataset = _make_dataset(tmp_path, n=2)
    output_dir = tmp_path / "runs"

    # Import run_arm AFTER the flow_module fixture has loaded the (mocked)
    # graph module — `from src.agents.building_flow_graph import ...` inside
    # run_arm.run() will then resolve to the patched module.
    from scripts import run_arm

    args = _build_args("A2", dataset, "test_a2", output_dir)
    rc = run_arm.run(args)
    assert rc == 0

    arm_dir = output_dir / "A2"
    results_path = arm_dir / "results.jsonl"
    cache_dir = arm_dir / "aggregated_cache"
    traces_path = arm_dir / "traces" / "evaluation_traces.jsonl"

    # results.jsonl: 2 rows (one per question)
    assert results_path.exists()
    rows = [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 2
    assert {r["question_id"] for r in rows} == {"Q001", "Q002"}
    assert all(r["arm"] == "A2" for r in rows)
    assert all(r["eval_verdict"] == "pass" for r in rows)
    assert all(isinstance(r["wall_clock_ms"], int) for r in rows)

    # aggregated_cache: one file per question
    assert (cache_dir / "Q001.json").exists()
    assert (cache_dir / "Q002.json").exists()

    # traces: 2 records (one per question — pass on first attempt, no retry)
    assert traces_path.exists()
    trace_records = [json.loads(line) for line in traces_path.read_text().splitlines() if line.strip()]
    assert len(trace_records) == 2
    assert all(r["arm"] == "A2" for r in trace_records)
    assert all(r["evaluation_status"] == "evaluated" for r in trace_records)


def test_run_arm_smoke_a1_writes_synthetic_trace(flow_module, tmp_path):
    """Plan §3.G case 2: with --arm A1, the evaluator never runs but the
    runner still writes one synthetic 'bypassed' trace per question.
    """
    _configure_passing_evaluator(flow_module)

    dataset = _make_dataset(tmp_path, n=2)
    output_dir = tmp_path / "runs"

    from scripts import run_arm

    args = _build_args("A1", dataset, "test_a1", output_dir)
    rc = run_arm.run(args)
    assert rc == 0

    arm_dir = output_dir / "A1"
    traces_path = arm_dir / "traces" / "evaluation_traces.jsonl"

    assert traces_path.exists()
    trace_records = [json.loads(line) for line in traces_path.read_text().splitlines() if line.strip()]
    assert len(trace_records) == 2, f"expected 2 synthetic traces, got {len(trace_records)}"

    for rec in trace_records:
        assert rec["arm"] == "A1"
        assert rec["evaluation_status"] == "bypassed"
        assert rec["mode"] == "off"
        assert rec["eval_scores"] is None
        # evaluator_latency_ms is exactly 0 for A1 because the evaluator wasn't run.
        assert rec["evaluator_latency_ms"] == 0


def test_run_arm_resume_skips_completed(flow_module, tmp_path):
    """Plan §3.G case 3: a row already present in results.jsonl is not re-run.

    Pre-populates results.jsonl with Q001 and confirms the second invocation
    only writes Q002 (so total rows after = 2, but evaluator was called once).
    """
    _configure_passing_evaluator(flow_module)

    dataset = _make_dataset(tmp_path, n=2)
    output_dir = tmp_path / "runs"
    arm_dir = output_dir / "A2"
    arm_dir.mkdir(parents=True, exist_ok=True)
    results_path = arm_dir / "results.jsonl"

    # Pre-populate with a Q001 row.
    pre_existing = {
        "question_id": "Q001",
        "arm": "A2",
        "run_id": "previous_run",
        "wall_clock_ms": 999,
        "eval_verdict": "pass",
    }
    results_path.write_text(json.dumps(pre_existing) + "\n")

    from scripts import run_arm

    args = _build_args("A2", dataset, "test_resume", output_dir)
    rc = run_arm.run(args)
    assert rc == 0

    # After the run: 2 rows total (the pre-existing Q001 + the newly-run Q002).
    rows = [json.loads(line) for line in results_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 2
    # Q001 was preserved (still labelled with the previous run_id) — the runner
    # did NOT re-process it.
    q001 = next(r for r in rows if r["question_id"] == "Q001")
    assert q001["run_id"] == "previous_run"
    # Q002 carries the new run_id.
    q002 = next(r for r in rows if r["question_id"] == "Q002")
    assert q002["run_id"] == "test_resume"

    # The evaluator was called exactly once (only for Q002).
    assert flow_module.evaluator_agent.evaluate.call_count == 1


def test_run_arm_uses_cache_from(flow_module, tmp_path):
    """Plan §3.G case 4: when --cache-from points at a directory containing
    an aggregated_cache, the SQL/vector agent nodes do NOT issue queries.

    We verify by checking the print-output-free fact that
    `state["aggregated_data_cached"] = True` is propagated and the cached
    aggregated_data is preserved in the trace.
    """
    _configure_passing_evaluator(flow_module)

    dataset = _make_dataset(tmp_path, n=1)
    output_dir = tmp_path / "runs"

    # Pre-populate a cache as if A1 already ran.
    cache_root = tmp_path / "previous_run_a1"
    cache_dir = cache_root / "aggregated_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached_evidence = {
        "generic_sql": [{"address": "Mocked Address 1", "energy_class": "A"}],
        "specialized_sql": None,
        "vector": None,
    }
    (cache_dir / "Q001.json").write_text(json.dumps(cached_evidence))

    from scripts import run_arm

    args = _build_args("A2", dataset, "test_cache", output_dir, cache_from=cache_root)
    rc = run_arm.run(args)
    assert rc == 0

    # The trace's aggregated_data must mirror what we put in the cache —
    # proving that the runner injected the cache (not the SQL layer's output).
    arm_dir = output_dir / "A2"
    traces_path = arm_dir / "traces" / "evaluation_traces.jsonl"
    trace_records = [json.loads(line) for line in traces_path.read_text().splitlines() if line.strip()]
    assert len(trace_records) == 1
    trace_agg = trace_records[0]["aggregated_data"]
    assert trace_agg["generic_sql"] == cached_evidence["generic_sql"], (
        "aggregated_data in trace did not match the injected cache — "
        "the SQL layer was likely run instead of bypassed."
    )


def test_run_arm_env_vars_set_before_import(monkeypatch, tmp_path):
    """Plan §3.G case 5: setup_environment() writes the arm's env vars BEFORE
    src.agents.building_flow_graph is imported.

    Without this contract, the evaluator agent would load the wrong prompt
    file at module import time, silently invalidating arm A4 (variant-B prompt).
    """
    # Clear the relevant env vars so the test can detect the writes.
    monkeypatch.delenv("EVALUATOR_MODE", raising=False)
    monkeypatch.delenv("EVALUATOR_PROMPT_VERSION", raising=False)
    monkeypatch.delenv("EXPERIMENT_RUN_ID", raising=False)
    monkeypatch.delenv("EXPERIMENT_ARM", raising=False)

    from scripts import run_arm

    args = _build_args(
        "A4",
        tmp_path / "questions.json",  # the file doesn't have to exist for setup_environment
        "test_env_order",
        tmp_path / "runs",
    )

    # Snapshot env BEFORE setup_environment.
    before = {k: os.environ.get(k) for k in
              ("EVALUATOR_MODE", "EVALUATOR_PROMPT_VERSION",
               "EXPERIMENT_RUN_ID", "EXPERIMENT_ARM")}
    assert before == {"EVALUATOR_MODE": None, "EVALUATOR_PROMPT_VERSION": None,
                      "EXPERIMENT_RUN_ID": None, "EXPERIMENT_ARM": None}

    run_arm.setup_environment(args)

    # AFTER setup: arm A4's config is in os.environ, ready for any subsequent
    # `from src.agents.building_flow_graph import ...`. The same os.environ is
    # what evaluator_agent reads at module import time.
    assert os.environ["EVALUATOR_MODE"] == "balanced"
    assert os.environ["EVALUATOR_PROMPT_VERSION"] == "evaluator_prompt_v2.txt"
    assert os.environ["EVALUATOR_MAX_RETRIES"] == "1"
    assert os.environ["EXPERIMENT_RUN_ID"] == "test_env_order"
    assert os.environ["EXPERIMENT_ARM"] == "A4"
    # Defaults that should be set if not already present.
    assert os.environ.get("EVALUATOR_ENABLED") == "true"
    assert os.environ.get("SUMMARIZER_TEMPERATURE") == "0.0"
