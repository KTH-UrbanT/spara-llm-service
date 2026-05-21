#!/usr/bin/env python3
"""Offline batch runner for one experimental arm.

Runs every question in a gold dataset through the building flow with the arm's
evaluator config (EVALUATOR_MODE / EVALUATOR_PROMPT_VERSION / EVALUATOR_MAX_RETRIES)
and writes per-question summary rows + the SQL/vector evidence the graph saw.

CRITICAL: env vars are set BEFORE importing building_flow_graph because the
evaluator agent is instantiated at module import time and reads
EVALUATOR_PROMPT_VERSION from the environment exactly once. To run multiple arms,
invoke this script once per arm as a fresh Python process.

Usage:
    python -m scripts.run_arm \\
        --arm A2 \\
        --dataset src/config/questions.json \\
        --run-id 2026-05-04_A2 \\
        --output-dir artifacts/runs/2026-05-04 \\
        [--cache-from artifacts/runs/2026-05-04/A1] \\
        [--limit 5]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


ARM_CONFIGS: Dict[str, Dict[str, str]] = {
    "A1": {"EVALUATOR_MODE": "off",      "EVALUATOR_MAX_RETRIES": "0",
           "EVALUATOR_PROMPT_VERSION": "evaluator_prompt.txt"},
    "A2": {"EVALUATOR_MODE": "balanced", "EVALUATOR_MAX_RETRIES": "1",
           "EVALUATOR_PROMPT_VERSION": "evaluator_prompt.txt"},
    "A3": {"EVALUATOR_MODE": "strict",   "EVALUATOR_MAX_RETRIES": "1",
           "EVALUATOR_PROMPT_VERSION": "evaluator_prompt.txt"},
    "A4": {"EVALUATOR_MODE": "balanced", "EVALUATOR_MAX_RETRIES": "1",
           "EVALUATOR_PROMPT_VERSION": "evaluator_prompt_v2.txt"},
    "A2_schema": {
        "EVALUATOR_MODE": "balanced",
        "EVALUATOR_MAX_RETRIES": "1",
        "EVALUATOR_PROMPT_VERSION": "evaluator_prompt_schema.txt",
    },
    "A2_judge_A": {"EVALUATOR_MODE": "balanced", "EVALUATOR_MAX_RETRIES": "1",
                   "EVALUATOR_PROMPT_VERSION": "evaluator_prompt_judge_A.txt"},
    "A2_judge_B": {"EVALUATOR_MODE": "balanced", "EVALUATOR_MAX_RETRIES": "1",
                   "EVALUATOR_PROMPT_VERSION": "evaluator_prompt_judge_B.txt"},
    "A2_judge_C": {"EVALUATOR_MODE": "balanced", "EVALUATOR_MAX_RETRIES": "1",
                   "EVALUATOR_PROMPT_VERSION": "evaluator_prompt_judge_C.txt"},
}


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run one experimental arm over the gold dataset.")
    p.add_argument("--arm", required=True, choices=sorted(ARM_CONFIGS))
    p.add_argument("--dataset", required=True, type=Path)
    p.add_argument("--run-id", required=True, help="Stable label for this run, e.g. 2026-05-04_A2.")
    p.add_argument("--output-dir", required=True, type=Path,
                   help="Run-family directory shared across the four arms.")
    p.add_argument("--cache-from", type=Path, default=None,
                   help="Run dir to read aggregated_cache from (typically the A1 run dir).")
    p.add_argument("--limit", type=int, default=None,
                   help="Run only the first N questions (smoke runs / authoring loop).")
    return p.parse_args(argv)


def setup_environment(args: argparse.Namespace) -> Path:
    """Set ALL env vars before any src.* import. Returns the per-arm artifact dir.

    The arm-specific env vars are written into os.environ HERE, before this
    function returns. Any caller that imports src.agents.building_flow_graph
    after calling this function will pick up the right evaluator prompt.
    Reordering this is the single biggest footgun.
    """
    cfg = ARM_CONFIGS[args.arm]
    for k, v in cfg.items():
        os.environ[k] = v
    os.environ["EXPERIMENT_RUN_ID"] = args.run_id
    os.environ["EXPERIMENT_ARM"] = args.arm
    os.environ.setdefault("EVALUATOR_ENABLED", "true")
    os.environ.setdefault("SUMMARIZER_TEMPERATURE", "0.0")

    arm_dir = args.output_dir / args.arm
    traces_dir = arm_dir / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    # Per-arm trace directory keeps arms cleanly separated on disk so the
    # analysis script can join `(arm, question_id)` without a parent-key column.
    os.environ["EVALUATION_TRACE_DIR"] = str(traces_dir)
    return arm_dir


def load_dataset(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if "dataset_version" not in data or "questions" not in data:
        sys.exit(f"Dataset {path} missing 'dataset_version' or 'questions' top-level keys.")
    return data


def load_completed_question_ids(results_path: Path) -> set:
    """Resume safety: question_ids already written to results.jsonl are skipped.

    Reads ONLY results.jsonl (not the trace files) because results rows are
    atomic per question — one append corresponds to one completed run. Trace
    files are append-only across attempts and may be partial when a run was
    interrupted between summarizer and evaluator.
    """
    if not results_path.exists():
        return set()
    completed = set()
    with results_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            qid = row.get("question_id")
            if isinstance(qid, str):
                completed.add(qid)
    return completed


def load_cached_aggregated_data(cache_from: Optional[Path], qid: str) -> Optional[Dict[str, Any]]:
    """Return cached aggregated_data for `qid` if --cache-from is set and the
    file exists. Otherwise None. The caller injects the result into initial state.
    """
    if cache_from is None:
        return None
    f = cache_from / "aggregated_cache" / f"{qid}.json"
    if not f.exists():
        return None
    with f.open(encoding="utf-8") as fh:
        return json.load(fh)


def build_initial_state(question: Dict[str, Any], dataset_version: str,
                        cached_agg: Optional[Dict[str, Any]],
                        thread_id: str) -> Dict[str, Any]:
    """Assemble the LangGraph initial state.

    This is a deterministic, Redis-free, batch-mode equivalent of what
    BuildingAgent.handle_building_query builds for production traffic. The
    differences vs production:
      - No prior conversation turns (`messages: []`).
      - No Redis-backed `session_state`.
      - `metadata` carries experiment identifiers (question_id, dataset_version)
        that the trace builder reads.
      - Optional pre-extracted `address` mirrored from `hint_address` so the
        understand_context node doesn't have to LLM-extract it.
      - Optional `aggregated_data` injection for cache-from runs.
    """
    metadata: Dict[str, Any] = {
        "question_id": question["question_id"],
        "dataset_version": dataset_version,
    }
    hint = question.get("hint_address")
    if hint:
        metadata["address"] = hint

    state: Dict[str, Any] = {
        "thread_id": thread_id,
        "messages": [],
        "metadata": metadata,
        "last_message": question["question"],
        "session_state": {},
    }
    if cached_agg is not None:
        state["aggregated_data"] = cached_agg
        # Flag read by the SQL/vector agent nodes to skip their queries —
        # avoids redundant cost AND ensures all arms see byte-identical evidence.
        state["aggregated_data_cached"] = True
    return state


def write_a1_synthetic_trace(question: Dict[str, Any], dataset_version: str,
                              run_id: str, agg: Dict[str, Any],
                              final_response: str,
                              summarizer_meta: Dict[str, Any]) -> None:
    """Append a synthetic trace record for an A1 (mode=off) question.

    Why this exists: the production graph's `route_after_summarizer` returns
    "end" when EVALUATOR_MODE=off, so `evaluate_response_node` is never entered
    and no trace record gets written. To keep the analysis script's join-on-
    `(arm, question_id)` uniform across arms, the runner writes one synthetic
    record per A1 question with `evaluation_status="bypassed"` and all
    eval-specific fields null/empty.
    """
    # Late import — must come AFTER setup_environment().
    from src.evaluation.trace_store import (
        append_evaluation_trace,
        build_evaluation_trace,
        truncate_for_trace,
    )

    record = build_evaluation_trace(
        question_id=question["question_id"],
        run_id=run_id,
        arm="A1",
        attempt_index=0,
        dataset_version=dataset_version,
        question=question["question"],
        answer=final_response,
        aggregated_data=truncate_for_trace(agg or {}),
        mode="off",
        verdict="",
        evaluated=False,
        evaluation_status="bypassed",
        eval_scores=None,
        eval_retry_count=0,
        model_deployment=None,
        prompt_version=None,
        evaluator_failure_reason="",
        summarizer_latency_ms=summarizer_meta.get("latency_ms"),
        summarizer_token_usage=summarizer_meta.get("token_usage"),
        evaluator_latency_ms=0,
        evaluator_token_usage=None,
        summarizer_temperature_requested=summarizer_meta.get("temperature_requested"),
        summarizer_temperature_unsupported=bool(summarizer_meta.get("temperature_unsupported")),
    )
    append_evaluation_trace(record)


def run(args: argparse.Namespace) -> int:
    """Main run loop. Separated from main() so tests can call it with an
    already-prepared argparse.Namespace and a pre-set environment.
    """
    arm_dir = setup_environment(args)
    # Cache directory lives PER-ARM so that --cache-from <output_dir>/A1 points
    # at A1's aggregated_cache. Each arm gets its own copy of the evidence
    # (whichever it ran with), and the canonical version is whichever arm
    # populates the cache first — typically A1 in the experiment pipeline.
    cache_dir = arm_dir / "aggregated_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    results_path = arm_dir / "results.jsonl"

    # IMPORTANT: import only after setup_environment(). The evaluator agent
    # caches its prompt at module import time (Section 0.2 contract).
    from src.agents.building_flow_graph import build_building_flow_graph
    from src.evaluation.trace_store import truncate_for_trace

    dataset = load_dataset(args.dataset)
    questions: List[Dict[str, Any]] = dataset["questions"]
    if args.limit is not None:
        questions = questions[: args.limit]
    dataset_version = dataset["dataset_version"]

    completed = load_completed_question_ids(results_path)
    if completed:
        print(f"[run_arm:{args.arm}] resuming — skipping {len(completed)} completed questions",
              flush=True)

    graph = build_building_flow_graph()

    for q in questions:
        qid = q["question_id"]
        if qid in completed:
            continue

        thread_id = f"{args.run_id}_{qid}"
        cached_agg = load_cached_aggregated_data(args.cache_from, qid)
        initial_state = build_initial_state(q, dataset_version, cached_agg, thread_id)

        t0 = time.time()
        try:
            final_state = graph.invoke(initial_state)
        except Exception as exc:
            wall_clock_ms = int((time.time() - t0) * 1000)
            err_row = {
                "question_id": qid,
                "arm": args.arm,
                "run_id": args.run_id,
                "dataset_version": dataset_version,
                "wall_clock_ms": wall_clock_ms,
                "error": str(exc),
            }
            with results_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(err_row, ensure_ascii=False) + "\n")
            print(f"[{qid}] ERROR: {exc}", flush=True)
            continue
        wall_clock_ms = int((time.time() - t0) * 1000)

        agg = final_state.get("aggregated_data") or {}
        # Always write the cache copy. For arm A1 (no --cache-from) this seeds
        # the cache that A2/A3/A4 will read with --cache-from. For other arms
        # it's a per-arm paper trail (the canonical version is whichever arm
        # wrote it first, typically A1).
        # truncate_for_trace coerces non-JSON-serializable leaves (e.g. MagicMock
        # in tests) via repr() so the cache write is safe; in production all
        # aggregated_data leaves are already JSON-native (str/int/float/list/dict).
        with (cache_dir / f"{qid}.json").open("w", encoding="utf-8") as f:
            json.dump(truncate_for_trace(agg), f, ensure_ascii=False, indent=2)

        # Per-question summary row. Latency / token fields are the values
        # state carried OUT of evaluate_response_node — for multi-attempt
        # sequences (retry) they reflect the last attempt only. To get totals
        # across attempts, the analysis script sums the per-attempt records
        # in evaluation_traces.jsonl.
        row: Dict[str, Any] = {
            "question_id": qid,
            "arm": args.arm,
            "run_id": args.run_id,
            "dataset_version": dataset_version,
            "wall_clock_ms": wall_clock_ms,
            "final_response": final_state.get("final_response"),
            "eval_verdict": final_state.get("eval_verdict"),
            "eval_scores": final_state.get("eval_scores"),
            "eval_retry_count": final_state.get("eval_retry_count", 0),
            "summarizer_latency_ms": final_state.get("summarizer_latency_ms"),
            "summarizer_token_usage": final_state.get("summarizer_token_usage"),
            "evaluator_latency_ms": final_state.get("evaluator_latency_ms"),
            "evaluator_token_usage": final_state.get("evaluator_token_usage"),
        }
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

        # Synthetic A1 trace (bypass arm has no evaluator-written trace).
        if args.arm == "A1":
            summarizer_meta = {
                "latency_ms": final_state.get("summarizer_latency_ms"),
                "token_usage": final_state.get("summarizer_token_usage"),
                "temperature_requested": final_state.get("summarizer_temperature_requested"),
                "temperature_unsupported": final_state.get("summarizer_temperature_unsupported"),
            }
            write_a1_synthetic_trace(
                q, dataset_version, args.run_id, agg,
                final_state.get("final_response") or "",
                summarizer_meta,
            )

        print(f"[{qid}] verdict={row['eval_verdict']} retry={row['eval_retry_count']} "
              f"latency={wall_clock_ms}ms", flush=True)

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
