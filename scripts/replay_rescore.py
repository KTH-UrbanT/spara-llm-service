"""Re-score a finished run's traces under the current checking rules — no LLM calls.

`analyze_results_closed_loop.py` reads the `case_pass` / `semantic_judge_pass` columns that
were stored when the run happened; it never recomputes them. So it cannot answer "what would
this run have scored under the fixed rules?". This script can, because every per-case row
carries the judge's full axes and each check's own result.

Use it to sanity-check a scoring change against `artifacts/runs/2026-05-30/` BEFORE spending
compute on a re-run. It is measurement-only: it never re-invokes the graph or the judge.

    python -m scripts.replay_rescore --run-dir artifacts/runs/2026-05-30
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.closed_loop.deterministic_checks import case_pass, check_semantic_judge_pass
from src.evaluation.closed_loop.in_loop_evaluator import score_axes

ARMS = ("A_open", "A_late_only", "A_full")
_DETERMINISTIC = ["route_match", "agent_match", "building_id_match",
                  "field_coverage_pass", "must_include_pass", "must_not_include_pass"]


def _load(path: Path) -> list[dict]:
    """Read a JSONL trace file; empty list if it is absent."""
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()]


def _was_short_circuit(row: dict) -> bool:
    """True when this case produced no answer to judge (clarification / address request).

    Two shapes, because this script has to read traces from both sides of the fix:
      - pre-fix: a bare {"verdict": "pass"} with no axes at all, and no flags recorded
        (recording them is Fix 7). The 3 fail-open verdicts of the 05-30 run all carry an
        `_fail_open` axis, so the absent-axes test does not collide with them.
      - post-fix: an explicit {"_short_circuit": True} / {"_no_answer": True} sentinel.
    """
    v = row.get("answer_quality_verdict") or {}
    axes = v.get("axes") or {}
    if not v:
        return False
    return not axes or bool(axes.get("_short_circuit") or axes.get("_no_answer"))


def rescore(row: dict, fix3: bool, fix4: bool) -> dict:
    """Recompute one row's semantic verdict and case_pass under a chosen set of fixes.

    fix3 — tri-state `semantic_judge_pass` plus the gold-conditional short-circuit rule.
    fix4 — faithfulness not applicable when there was no evidence.

    With neither, the stored `semantic_judge_pass` column is used as-is, which isolates
    the effect of removing the cost/wall vetoes from `case_pass`.
    """
    verdict = row.get("answer_quality_verdict") or {}
    axes = verdict.get("axes") or {}
    evidence_present = None
    judged = bool(axes) and not axes.get("_fail_open") and not _was_short_circuit(row)

    if fix4 and judged:
        # Pre-fix runs recorded no aggregated_data on the row, but the SQL-field and
        # vector-chunk columns are exactly what fed it.
        evidence_present = bool(row.get("final_sql_fields_used")
                                or row.get("final_vector_chunks_retrieved"))
        axes, composite, v = score_axes(axes, evidence_present)
        verdict = {"verdict": v, "axes": axes, "composite": composite}

    if not fix3:
        sjp = row.get("semantic_judge_pass") if not fix4 else check_semantic_judge_pass(verdict)
    elif _was_short_circuit(row):
        # Fix 3: asking for an address is correct only when gold expected clarification.
        sjp = row.get("expected_route") == "clarification"
    else:
        sjp = check_semantic_judge_pass(verdict)

    checks = {k: row.get(k) for k in _DETERMINISTIC}
    checks["semantic_judge_pass"] = sjp
    return {"semantic_judge_pass": sjp, "case_pass": case_pass(checks),
            "evidence_present": evidence_present}


def _fmt(n: int, d: int) -> str:
    """Format a count as "n/d = rate", tolerating d == 0."""
    return f"{n}/{d} = {(n / d if d else 0.0):.3f}"


def run(args) -> int:
    """Re-score every arm in a run directory and print the stored-vs-replayed table."""
    rd = Path(args.run_dir)
    arms = [a for a in ARMS if (rd / a).exists()]
    if not arms:
        print(f"ERROR: no arm directories under {rd}", file=sys.stderr)
        return 1

    # Each stage adds ONE change, so a number that moves can be attributed to one fix.
    stages = [
        ("stored (as the run recorded it)", None, None),
        ("+ Fix 2: cost/wall vetoes out of case_pass", False, False),
        ("+ Fix 3: tri-state judge, gold-conditional short-circuit", True, False),
        ("+ Fix 4: faithfulness N/A without evidence", True, True),
    ]
    for label, fix3, fix4 in stages:
        print(f"\n=== {label} ===")
        for arm in arms:
            rows = _load(rd / arm / "traces" / "per_case.jsonl")
            if not rows:
                continue
            if fix3 is None:
                scored = [{"case_pass": r.get("case_pass"),
                           "semantic_judge_pass": r.get("semantic_judge_pass")} for r in rows]
            else:
                scored = [rescore(r, fix3, fix4) for r in rows]
            n_pass = sum(1 for s in scored if s["case_pass"])
            measured = [s for s in scored if s["semantic_judge_pass"] is not None]
            n_sjp = sum(1 for s in measured if s["semantic_judge_pass"])
            # ...and again over cases the JUDGE actually scored, i.e. dropping the
            # short-circuits that Fix 3 decides from gold rather than from a judgement.
            # This is the denominator the hand-computed 27/64 = 0.422 uses.
            judged = [s for r, s in zip(rows, scored)
                      if s["semantic_judge_pass"] is not None and not _was_short_circuit(r)]
            n_judged = sum(1 for s in judged if s["semantic_judge_pass"])
            # n=77 excludes the 11 `combined` cases, which carry zero treatment contrast.
            n_pass_77 = sum(1 for r, s in zip(rows, scored)
                            if s["case_pass"] and r.get("expected_route") != "combined")
            n_77 = sum(1 for r in rows if r.get("expected_route") != "combined")
            print(f"  {arm:<12} case_pass {_fmt(n_pass, len(rows))}"
                  f"  (n=77: {_fmt(n_pass_77, n_77)})"
                  f"   judge {_fmt(n_sjp, len(measured))}"
                  f"   judge-only {_fmt(n_judged, len(judged))}")

    rows = _load(rd / arms[0] / "traces" / "per_case.jsonl")
    by_route: dict[str, list] = {}
    for r in rows:
        by_route.setdefault(r.get("expected_route", "?"), []).append(r)
    print(f"\n--- {arms[0]} by expected_route, all fixes applied ---")
    for route, rs in sorted(by_route.items()):
        n = sum(1 for r in rs if rescore(r, True, True)["case_pass"])
        print(f"      {route:<20} {n}/{len(rs)}")
    n_no_ev = sum(1 for r in rows if rescore(r, True, True)["evidence_present"] is False)
    print(f"\n--- {arms[0]}: evidence inferred absent on {n_no_ev}/{len(rows)} scored cases ---")
    print("    (proxy only — the stored rows predate the evidence_present column, so this")
    print("     counts judged cases with no SQL fields and no vector chunks. Prediction 1")
    print("     is checked against the live column, not this.)")
    return 0


def parse_args(argv=None):
    """CLI: --run-dir is the only argument."""
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True)
    return p.parse_args(argv)


def main(argv=None):
    """Entry point; exit status is `run`'s return code."""
    sys.exit(run(parse_args(argv)))


if __name__ == "__main__":
    main()
