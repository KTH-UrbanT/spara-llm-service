"""Controlled-answer replay: feed verbatim v4 A2 answers to both evaluators.

The purpose is to close the non-determinism caveat from plan-eil-v5.md §7.1.5:
the schema-aware judge saw a paraphrased answer (summarizer non-determinism),
but we want to show the verdict difference holds for the *exact* answer the
v4 A2 baseline judge passed.

For each of Q012 and Q013:
1. Load the verbatim v4 A2 final_response from artifacts/runs/2026-05-14_v2/A2/results.jsonl
2. Load the question text from src/config/questions.json
3. Load the aggregated_data from artifacts/runs/2026-05-14_v2/A1/aggregated_cache/<qid>.json
4. Call EvaluatorAgent(prompt_path=evaluator_prompt.txt).evaluate(...)         → baseline verdict
5. Call EvaluatorAgent(prompt_path=evaluator_prompt_schema.txt).evaluate(...)  → schema verdict
6. Record both verdicts to results.

Composite score is computed locally (mirroring building_flow_graph.py:_compute_composite_score
at L498-515) since EvaluatorAgent.evaluate() returns only the LLM's raw axis scores; the
production composite is computed downstream in evaluate_response_node.

Output: <output>/results.json  (one record per (qid, judge, run_index))
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Late import: must come after load_dotenv() so the Azure env vars are populated.
from src.agents.evaluator_agent import EvaluatorAgent


def load_question(questions_path: Path, qid: str) -> str:
    data = json.loads(questions_path.read_text(encoding="utf-8"))
    for q in data["questions"]:
        if q["question_id"] == qid:
            return q["question"]
    raise KeyError(qid)


def load_a2_answer(results_path: Path, qid: str) -> str:
    for line in results_path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["question_id"] == qid:
            return r["final_response"]
    raise KeyError(qid)


def load_aggregated(cache_dir: Path, qid: str) -> dict:
    return json.loads((cache_dir / f"{qid}.json").read_text(encoding="utf-8"))


def compute_composite(verdict: dict) -> float | None:
    """Mirrors building_flow_graph.py:_compute_composite_score (L498-515).
    Axis scores are on a 0-10 scale; composite is on the same scale."""
    try:
        g  = float(verdict["groundedness_score"])
        c  = float(verdict["completeness_score"])
        nf = float(verdict["numeric_fidelity_score"])
        cs = float(verdict["constraint_satisfaction_score"])
        uc = float(verdict["uncertainty_calibration_score"])
    except (KeyError, ValueError, TypeError):
        return None
    return round(0.35 * g + 0.25 * nf + 0.20 * cs + 0.15 * c + 0.05 * uc, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--qids", nargs="+", default=["Q012", "Q013"])
    ap.add_argument("--output", required=True, type=Path,
                    help="Where to write the results.json file.")
    ap.add_argument("--questions", type=Path, default=Path("src/config/questions.json"))
    ap.add_argument("--a2-results", type=Path,
                    default=Path("artifacts/runs/2026-05-14_v2/A2/results.jsonl"))
    ap.add_argument("--cache-dir", type=Path,
                    default=Path("artifacts/runs/2026-05-14_v2/A1/aggregated_cache"))
    ap.add_argument("--run-index", type=int, default=1,
                    help="Run index (for multi-run mode-voting; tagged into each row).")
    args = ap.parse_args()

    judges = {
        "baseline": EvaluatorAgent(prompt_path="src/prompts/evaluator_prompt.txt"),
        "schema":   EvaluatorAgent(prompt_path="src/prompts/evaluator_prompt_schema.txt"),
    }

    results = []
    for qid in args.qids:
        question = load_question(args.questions, qid)
        answer = load_a2_answer(args.a2_results, qid)
        agg = load_aggregated(args.cache_dir, qid)
        for judge_name, judge in judges.items():
            print(f"\n[run={args.run_index}] [{qid}] running {judge_name} judge ...",
                  flush=True)
            verdict = judge.evaluate(question=question, aggregated_data=agg, answer=answer)
            composite = compute_composite(verdict) if isinstance(verdict, dict) else None
            v = verdict.get("verdict") if isinstance(verdict, dict) else None
            hard_fail = verdict.get("hard_fail") if isinstance(verdict, dict) else None
            print(f"  verdict={v}  composite={composite}  hard_fail={hard_fail}")
            results.append({
                "run_index": args.run_index,
                "qid": qid,
                "judge": judge_name,
                "verdict": v,
                "composite_score": composite,
                "hard_fail": hard_fail,
                "hard_fail_reason": (verdict.get("hard_fail_reason")
                                     if isinstance(verdict, dict) else None),
                "issues": verdict.get("issues") if isinstance(verdict, dict) else None,
                "axis_scores": {k: verdict.get(k) for k in (
                    "groundedness_score", "completeness_score",
                    "numeric_fidelity_score", "constraint_satisfaction_score",
                    "uncertainty_calibration_score", "faithfulness_score",
                )} if isinstance(verdict, dict) else None,
                "answer_used": answer,
                "evaluator_failed": (verdict.get("evaluator_failed")
                                     if isinstance(verdict, dict) else None),
            })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"\nWrote {args.output} — {len(results)} records.")


if __name__ == "__main__":
    main()
