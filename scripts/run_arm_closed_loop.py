"""Run one experiment arm over the filtered case set.

Run order: A_open first (writes the evidence+instrumentation cache and the cost
baseline), then A_late_only and A_full with --cache-from A_open.

Usage (module mode — adds repo root to sys.path):
    python -m scripts.run_arm_closed_loop --arm A_open \
        --dataset artifacts/datasets/filtered_cases.jsonl \
        --run-dir artifacts/runs/2026-06-01

    python -m scripts.run_arm_closed_loop --arm A_late_only \
        --dataset artifacts/datasets/filtered_cases.jsonl \
        --run-dir artifacts/runs/2026-06-01 --cache-from A_open
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

_PROMPT_ENV = {"ROUTE_PLAUSIBILITY_PROMPT_VERSION": "route_plausibility_v2.txt",
               "ANSWER_QUALITY_PROMPT_VERSION": "answer_quality_v1.txt"}
# CLOSED_LOOP_EARLY_VOTE_K is pinned per arm rather than left to the ambient environment:
# the early checkpoint exists only in A_full, and an unset variable would let a value leak
# in from the shell and go unrecorded. k=3 there, k=1 (a no-op) everywhere else.
_ARM_ENV = {
    "A_open":      {"RUN_ARM":"A_open",      "EARLY_CHECKPOINT_ENABLED":"false","LATE_CHECKPOINT_ENABLED":"false","EVALUATOR_MODE":"off","CLOSED_LOOP_EARLY_VOTE_K":"1", **_PROMPT_ENV},
    "A_late_only": {"RUN_ARM":"A_late_only", "EARLY_CHECKPOINT_ENABLED":"false","LATE_CHECKPOINT_ENABLED":"true", "EVALUATOR_MODE":"off","CLOSED_LOOP_EARLY_VOTE_K":"1", **_PROMPT_ENV},
    "A_full":      {"RUN_ARM":"A_full",      "EARLY_CHECKPOINT_ENABLED":"true", "LATE_CHECKPOINT_ENABLED":"true", "EVALUATOR_MODE":"off","CLOSED_LOOP_EARLY_VOTE_K":"3", **_PROMPT_ENV},
}
# Reported as covariates, NOT vetoes: a budget that can only fire in the treatment
# arms (load_cost_cap returns None without --cache-from) is a confound, not a budget.
# See plan-eil-v20-fixes.md Fix 2.
WALL_BUDGET_S = 300    # per-case wall clock; recorded, never vetoes case_pass
MAX_RETRIES = 2        # per case, across both checkpoints
COST_CAP_MULT = 5.0    # per-case token cap = this x the open-arm per-case mean

_AGENT_FOR_ROUTE = {"generic": "GenericAgent", "building": "BuildingAgent",
                    "conversational": "ConversationalistAgent"}

# The rule: cache verdicts DERIVED FROM the cached evidence; never cache flags produced
# by this arm's own traversal.
#   - identity_gate_blocked is computed inside generic_sql_agent_node, which the cached
#     arms skip precisely because the evidence is cached — so without it they run 14 cases
#     on to the summariser that A_open short-circuited, and the arms are not comparable.
#   - clarification_fired / request_address_fired record where THIS run's control flow
#     went. Caching them made the treatment arms inherit a decision they never made, and
#     let the stale flag overwrite a real judge verdict with a fake pass (§1.5b).
_CACHE_KEYS = ["aggregated_data", "invoked_generic_sql", "invoked_specialized_sql",
               "invoked_vector", "sql_fields_generic", "sql_fields_specialized",
               "vector_chunks_retrieved", "identity_gate_blocked"]

# Snapshotted alongside the evidence (it lives under state.metadata, not at top level) so
# cached arms resolve building_id the same way A_open did — otherwise A_open uses
# matched_building_id and the cached arms fall back to byggnadsid[0] → asymmetric metric.
_CACHED_IDENTITY_KEY = "building_identity_check"


def setup_environment(arm: str, run_id: str) -> None:
    for k, v in _ARM_ENV[arm].items():
        os.environ[k] = v
    os.environ["EXPERIMENT_RUN_ID"] = run_id
    os.environ["EXPERIMENT_ARM"] = arm


def load_dataset(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def load_completed_case_ids(run_dir: Path, arm: str) -> set[str]:
    from src.evaluation.closed_loop.trace_schema import load_per_case_traces
    return {t["case_id"] for t in load_per_case_traces(run_dir, arm) if "case_id" in t}


def save_case_cache(run_dir: Path, arm: str, case_id: str, bundle: dict) -> None:
    p = run_dir / arm / "case_cache" / f"{case_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(bundle, ensure_ascii=False, default=str), encoding="utf-8")


def load_case_cache(run_dir: Path, cache_arm: str, case_id: str) -> dict | None:
    p = run_dir / cache_arm / "case_cache" / f"{case_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_cost_baseline(run_dir: Path, arm: str, per_case_tokens: list[int]) -> None:
    mean = (sum(per_case_tokens) / len(per_case_tokens)) if per_case_tokens else 0.0
    p = run_dir / arm / "cost_baseline.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"per_case_mean_tokens": mean, "n": len(per_case_tokens)}))


def load_cost_cap(run_dir: Path, cache_arm: str | None,
                  mult: float = COST_CAP_MULT) -> float | None:
    if not cache_arm:
        return None
    p = run_dir / cache_arm / "cost_baseline.json"
    if not p.exists():
        return None
    mean = json.loads(p.read_text()).get("per_case_mean_tokens") or 0.0
    return mult * mean if mean else None


_CODE_GLOBS = ("src/**/*.py", "scripts/**/*.py",
               "src/evaluation/closed_loop/prompts/*.txt")


def code_fingerprint() -> tuple[str, int]:
    """SHA-256 over the source that decides behaviour, plus the file count.

    Deliberately NOT a git SHA. v20's mid-run edit to `check_predictions.py` was
    *uncommitted* — the whole file history is one commit made after all three replicates
    finished (§1.10) — so a git SHA would have been identical across r1, r2 and r3 and
    would have recorded nothing. A content hash changes the moment a byte does, which is
    the failure mode actually observed.
    """
    root = Path(__file__).resolve().parents[1]
    files = sorted({p for g in _CODE_GLOBS for p in root.glob(g)
                    if "__pycache__" not in p.parts})
    import hashlib
    h = hashlib.sha256()
    for p in files:
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def write_run_record(run_dir: Path, arm: str, run_id: str, dataset: str) -> None:
    """Pin what actually produced this arm's traces, in the artifact itself.

    v20's `check_predictions.py` was edited between r1 and r2 and the only evidence was a
    file mtime (v21 §1.10). A code fingerprint recorded next to the numbers makes a
    between-replicate change visible to anyone reading the run directory.
    """
    import hashlib, subprocess
    code_sha, n_files = code_fingerprint()
    try:
        # Best-effort only, and empty inside the container: llm-service is a git submodule,
        # so its `.git` is a file pointing at the parent repo's module dir, which is not
        # mounted. `code_sha256` is the load-bearing field.
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                             cwd=Path(__file__).resolve().parents[1], timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                                    text=True, cwd=Path(__file__).resolve().parents[1],
                                    timeout=10).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        sha, dirty = "", None
    ds = Path(dataset)
    rec = {
        "run_id": run_id, "arm": arm,
        "code_sha256": code_sha, "code_n_files": n_files,
        "git_sha": sha, "git_dirty": dirty,
        "dataset": str(dataset),
        "dataset_sha256": hashlib.sha256(ds.read_bytes()).hexdigest() if ds.exists() else None,
        "env": {k: os.environ.get(k) for k in
                ("RUN_ARM", "EARLY_CHECKPOINT_ENABLED", "LATE_CHECKPOINT_ENABLED",
                 "EVALUATOR_MODE", "CLOSED_LOOP_EARLY_VOTE_K",
                 "ROUTE_PLAUSIBILITY_PROMPT_VERSION", "ANSWER_QUALITY_PROMPT_VERSION",
                 "OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME")},
        "constants": {"WALL_BUDGET_S": WALL_BUDGET_S, "MAX_RETRIES": MAX_RETRIES,
                      "COST_CAP_MULT": COST_CAP_MULT},
    }
    p = run_dir / arm / "run_record.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")


def extract_snapshot(final_state: dict) -> dict:
    """Assemble the harness view of a finished case from the raw graph state."""
    by_agent: dict[str, list] = {}
    if final_state.get("invoked_generic_sql"):
        by_agent["generic_sql_agent"] = list(final_state.get("sql_fields_generic") or [])
    if final_state.get("invoked_specialized_sql"):
        by_agent["specialized_sql_agent"] = list(final_state.get("sql_fields_specialized") or [])
    specialists = list(by_agent.keys())
    if final_state.get("invoked_vector"):
        specialists.append("vector_db_agent")
    union: list[str] = []
    for fs in by_agent.values():
        union.extend(fs)
    union = list(dict.fromkeys(union))

    bic = (final_state.get("metadata") or {}).get("building_identity_check") or {}
    resolved_id = bic.get("matched_building_id")
    if not resolved_id:
        rows = (final_state.get("aggregated_data") or {}).get("generic_sql") or []
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            resolved_id = rows[0].get("byggnadsid")

    return {
        "top_route": final_state.get("top_route"),
        "clarification_fired": final_state.get("clarification_fired", False),
        "request_address_fired": final_state.get("request_address_fired", False),
        # False while EKR_GEN_019 converts = the promoter was the sole cause (v21 §2.2).
        "forced_route_applied": bool(final_state.get("forced_route_applied")),
        "specialists_invoked": specialists,
        "resolved_building_id": resolved_id,
        "sql_fields_used": union,
        "sql_fields_used_by_agent": by_agent,
        "vector_chunks_retrieved": final_state.get("vector_chunks_retrieved") or [],
        "final_answer": final_state.get("final_response") or "",
        "aggregated_data": final_state.get("aggregated_data") or {},
        "retry_budget": final_state.get("retry_budget", MAX_RETRIES),
        "controller_flags": final_state.get("controller_flags") or {},
        "attempt_records": final_state.get("attempt_records") or [],
        "answer_quality_verdict": final_state.get("answer_quality_verdict"),
        "total_tokens": int(final_state.get("eval_total_tokens") or 0),
        "total_completions": int(final_state.get("eval_total_completions") or 0),
        "cache_bundle": {
            **{k: final_state.get(k) for k in _CACHE_KEYS},
            _CACHED_IDENTITY_KEY: bic or None,
        },
    }


def build_initial_state(case: dict, arm: str, cached: dict | None) -> dict:
    # The dataset models a known-building context: building/combined cases carry the
    # address (and identifiers) as separate fields, not in the question text. Seed them
    # into metadata so the pipeline can retrieve instead of asking for the address.
    # Clarification cases have no address, so they correctly route to clarification.
    metadata: dict = {"question_id": case.get("case_id"), "dataset_version": "closed_loop"}
    addr = case.get("address")
    if addr:
        metadata["address"] = addr
        metadata["address_from_user"] = addr
    if case.get("city"):
        metadata["city"] = case["city"]
    if case.get("byggnadsid"):
        metadata["building_id"] = case["byggnadsid"]
    state: dict = {
        "last_message": case["question"], "messages": [],
        "metadata": metadata,
        "eval_arm": arm, "retry_budget": MAX_RETRIES,
        "checkpoint_history": [], "controller_flags": {}, "attempt_records": [],
        "corrective_hint": None, "hint_target": None,
        "invoked_generic_sql": False, "invoked_specialized_sql": False, "invoked_vector": False,
        "sql_fields_generic": [], "sql_fields_specialized": [], "vector_chunks_retrieved": [],
        "clarification_fired": False, "request_address_fired": False,
    }
    if cached is not None:
        state["aggregated_data"] = cached.get("aggregated_data") or {}
        state["aggregated_data_cached"] = True
        for k in _CACHE_KEYS:
            if k in cached and k != "aggregated_data":
                state[k] = cached[k]
        # Rehydrate the identity-check verdict under state.metadata so extract_snapshot
        # in the cached arm uses the same matched_building_id as A_open did.
        bic = cached.get(_CACHED_IDENTITY_KEY)
        if bic:
            metadata = dict(state.get("metadata") or {})
            metadata[_CACHED_IDENTITY_KEY] = bic
            state["metadata"] = metadata
    return state


def resolve_scoring_verdict(snap: dict, case: dict, get_scorer) -> dict:
    """The answer-quality verdict that feeds semantic_judge_pass, defined for every arm.

    - A clarification / address-request outcome produced no substantive answer to judge.
      Whether that was the right move is a GOLD question, so it is answered here in the
      offline harness and never by the judge: asking for an address is a pass only when
      the gold label says clarification was expected, and a fail otherwise. Passing it
      unconditionally is what awarded a pass every time the system dodged the question
      (§1.6a) — 21 of 88 cases in the 05-30 run.
    - The closed-loop arms already produced an in-loop verdict (the final attempt's) -> reuse
      it. Note this is checked AFTER the flags, so a rewind that lands in request_address is
      scored on that outcome rather than on a stale attempt-1 verdict.
    - The open arm runs no checkpoint, so the harness scores its single answer once. This call
      is measurement only and is not counted toward the arm's reported pipeline cost.
    """
    if snap.get("clarification_fired") or snap.get("request_address_fired"):
        expected_clarification = case.get("expected_route") == "clarification"
        return {"verdict": "pass" if expected_clarification else "fail",
                "axes": {"_short_circuit": True},
                "composite": 0.0}
    v = snap.get("answer_quality_verdict")
    if v is not None:
        return v
    answer = snap.get("final_answer") or ""
    if not answer:
        return {"verdict": "fail", "axes": {"_no_answer": True}, "composite": 0.0}
    av = get_scorer().answer_quality(
        question=case["question"],
        retrieved_evidence=snap.get("aggregated_data") or {},
        final_answer=answer)
    return {"verdict": av.verdict, "axes": av.axes, "composite": av.composite,
            "evidence_present": av.evidence_present}


def run_case(graph, case: dict, arm: str, cached: dict | None) -> dict:
    start = time.monotonic()
    try:
        # The rewind cycles add supersteps; raise the limit above LangGraph's default of 25.
        snap = extract_snapshot(graph.invoke(build_initial_state(case, arm, cached),
                                             config={"recursion_limit": 50}))
        snap["wall_time_s"] = time.monotonic() - start
        snap["wall_budget_exceeded"] = snap["wall_time_s"] > WALL_BUDGET_S
        return snap
    except Exception as exc:
        print(f"  ERROR case {case.get('case_id')}: {exc}", file=sys.stderr)
        return {"top_route": None, "clarification_fired": False, "request_address_fired": False,
                "forced_route_applied": False,
                "specialists_invoked": [], "resolved_building_id": None, "sql_fields_used": [],
                "sql_fields_used_by_agent": {}, "vector_chunks_retrieved": [], "final_answer": "",
                "aggregated_data": {}, "retry_budget": MAX_RETRIES, "controller_flags": {},
                "attempt_records": [], "answer_quality_verdict": None,
                "total_tokens": 0, "total_completions": 0, "cache_bundle": {},
                "wall_time_s": time.monotonic() - start, "wall_budget_exceeded": True,
                "run_error": str(exc)[:500]}


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--arm", required=True, choices=["A_open", "A_late_only", "A_full"])
    p.add_argument("--dataset", required=True)
    p.add_argument("--run-dir", required=True)
    p.add_argument("--cache-from", default=None)
    p.add_argument("--run-id", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--cost-cap-mult", type=float, default=COST_CAP_MULT,
                   help="Override the pre-registered 5x cost cap. "
                        "Pass 1e9 to effectively disable it (sensitivity analysis only — "
                        "any change from the default constant must be disclosed per the "
                        "pre-registration discipline in plan-eil-v11.md §12).")
    return p.parse_args(argv)


def run(args) -> int:
    run_id = args.run_id or f"run_{int(time.time())}"
    run_dir = Path(args.run_dir)
    print(f"[run_arm] arm={args.arm} run_id={run_id}")
    setup_environment(args.arm, run_id)

    from src.agents.building_flow_graph import build_building_flow_graph
    from src.evaluation.closed_loop.deterministic_checks import run_all_checks, case_pass as passed
    from src.evaluation.closed_loop.trace_schema import (
        append_per_case_trace, append_per_attempt_trace, write_arm_summary)

    # After the imports above: they load the .env, so the model deployment is in os.environ
    # by now and lands in the record instead of being written as null.
    write_run_record(run_dir, args.arm, run_id, args.dataset)

    graph = build_building_flow_graph()
    cases = load_dataset(args.dataset)
    if args.limit:
        cases = cases[:args.limit]
    done = load_completed_case_ids(run_dir, args.arm)
    cost_cap = load_cost_cap(run_dir, args.cache_from, args.cost_cap_mult)  # None for the open arm
    rows: list[dict] = []
    n_pass = n_total = 0

    _scorer: dict = {}
    def _get_scorer():  # lazy: only the open arm instantiates it
        if "s" not in _scorer:
            from src.evaluation.closed_loop.in_loop_evaluator import InLoopEvaluator
            _scorer["s"] = InLoopEvaluator()
        return _scorer["s"]

    for case in cases:
        cid = str(case.get("case_id", ""))
        if cid in done:
            print(f"  skip: {cid}"); continue
        print(f"  running {cid} ({case.get('expected_route')})")
        cached = load_case_cache(run_dir, args.cache_from, cid) if args.cache_from else None
        snap = run_case(graph, case, args.arm, cached)
        if args.arm == "A_open" and snap.get("cache_bundle"):
            save_case_cache(run_dir, args.arm, cid, snap["cache_bundle"])

        score_verdict = resolve_scoring_verdict(snap, case, _get_scorer)
        checks = run_all_checks(snap, snap["final_answer"], case, score_verdict)
        # Recorded as covariates only. Both can fire ONLY in the treatment arms
        # (load_cost_cap returns None without --cache-from), so vetoing case_pass with
        # them manufactured the entire reported negative result (§1.5a). The capped rate
        # is a pre-declared sensitivity analysis, computed from these columns downstream.
        cost_exceeded = bool(cost_cap is not None and snap.get("total_tokens", 0) > cost_cap)
        wall_exceeded = bool(snap.get("wall_budget_exceeded", False))
        cp = passed(checks)
        n_pass += int(cp); n_total += 1
        flags = snap.get("controller_flags") or {}
        row = {
            "run_id": run_id, "arm": args.arm, "case_id": cid,
            "case_type": case.get("case_type"), "source": case.get("source"),
            "expected_route": case.get("expected_route"), "expected_agent": case.get("expected_agent"),
            "expected_building_id": case.get("expected_building_id"),
            "expected_fields": case.get("expected_fields"),
            "final_route_taken": snap.get("top_route"),
            "final_agent_invoked": _AGENT_FOR_ROUTE.get(snap.get("top_route") or "", None),
            "final_building_id_resolved": snap.get("resolved_building_id"),
            "final_sql_fields_used": snap.get("sql_fields_used"),
            "final_sql_fields_used_by_agent": snap.get("sql_fields_used_by_agent"),
            "final_vector_chunks_retrieved": snap.get("vector_chunks_retrieved"),
            "final_answer": snap["final_answer"][:1000],
            "answer_quality_verdict": score_verdict,
            # Control-flow and evidence visibility. evidence_present in particular makes a
            # dead upstream channel a flag on the row instead of a mysteriously low
            # faithfulness score — the evaluator would have caught the outage (§1.3).
            "clarification_fired": bool(snap.get("clarification_fired")),
            "request_address_fired": bool(snap.get("request_address_fired")),
            "forced_route_applied": bool(snap.get("forced_route_applied")),
            "evidence_present": any(bool(v) for v in (snap.get("aggregated_data") or {}).values()),
            "total_attempts": 1 + (MAX_RETRIES - snap["retry_budget"]),  # 1 = no retry
            "early_block_exhausted": flags.get("early_block_exhausted", False),
            "closed_loop_terminated_without_pass": flags.get("closed_loop_terminated_without_pass", False),
            "terminated_router_disallowed": flags.get("terminated_router_disallowed", False),
            "cost_exceeded": cost_exceeded, "wall_budget_exceeded": wall_exceeded,
            "wall_time_s": snap.get("wall_time_s"),
            "total_latency_ms": (snap.get("wall_time_s") or 0.0) * 1000.0,
            "total_tokens": snap.get("total_tokens", 0),
            "total_completions": snap.get("total_completions", 0),
            **checks, "case_pass": cp,
        }
        append_per_case_trace(row, run_dir, args.arm)
        rows.append(row)
        for i, rec in enumerate(snap.get("attempt_records") or []):
            append_per_attempt_trace({"run_id": run_id, "arm": args.arm,
                                      "case_id": cid, "attempt_index": i, **rec},
                                     run_dir, args.arm)
        print(f"    case_pass={cp} attempts={1 + (MAX_RETRIES - snap['retry_budget'])} tokens={snap.get('total_tokens', 0)}")

    if args.arm == "A_open":
        write_cost_baseline(run_dir, args.arm, [r["total_tokens"] for r in rows])

    rate = n_pass / n_total if n_total else 0.0
    attempts = sorted(r["total_attempts"] for r in rows)
    by_route: dict[str, list] = {}
    for r in rows:
        by_route.setdefault(r["expected_route"], []).append(r)
    per_stratum = {rt: sum(1 for x in cs if x["case_pass"]) / len(cs) for rt, cs in by_route.items()}
    print(f"\n[run_arm] {args.arm}: {n_pass}/{n_total} = {rate:.3f}")
    write_arm_summary({
        "run_id": run_id, "arm": args.arm,
        "n_cases": n_total, "n_pass": n_pass, "case_pass_rate": rate,
        "per_stratum_pass_rate": per_stratum,
        "mean_attempts": (sum(attempts) / len(attempts)) if attempts else 0.0,
        "median_attempts": attempts[len(attempts) // 2] if attempts else 0,
        "early_block_exhausted_count": sum(1 for r in rows if r["early_block_exhausted"]),
        "terminated_without_pass_count": sum(1 for r in rows if r["closed_loop_terminated_without_pass"]),
        "terminated_router_disallowed_count": sum(1 for r in rows if r["terminated_router_disallowed"]),
        "cost_exceeded_count": sum(1 for r in rows if r["cost_exceeded"]),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "total_completions": sum(r["total_completions"] for r in rows),
        "total_wall_time_s": sum((r.get("wall_time_s") or 0.0) for r in rows),
    }, run_dir, args.arm)
    return 0


def main(argv=None):
    sys.exit(run(parse_args(argv)))

if __name__ == "__main__":
    main()
