"""Recompute `check_must_include` over frozen traces against the live dataset (v22 close-out).

`replay_rescore.py` replays the six deterministic check values stored in the trace rows
(line 79) and `check_predictions.py` `_det_pass` reads the same stored flags — neither can
re-score after a gold-label edit. This script is the missing recomputing pass: for every
frozen `per_case.jsonl` row it recomputes `check_must_include(final_answer, case)` from the
row's own `final_answer` against the dataset as it exists NOW, rebuilds both `case_pass`
scorings (deterministic-core and judge-inclusive), reruns the pre-registered consensus
McNemar, and names every row whose recomputed value differs from the stored one.

Measurement only — it never re-invokes the graph or the judge. Acceptance (v20-Fix-3): run
against the unchanged dataset first; the flip list must be empty and every pinned number
must reproduce, or the analytic is wrong and no edit may be applied.

    python -m scripts.rescore_deterministic \
        --run-dir artifacts/runs/2026-08-13_v21_r1 --run-dir artifacts/runs/2026-08-13_v21_r2 \
        --run-dir artifacts/runs/2026-08-13_v21_r3 \
        --dataset artifacts/datasets/filtered_cases.jsonl --out artifacts/runs/rescore.md
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.closed_loop.deterministic_checks import check_must_include
from scripts.analyze_results_closed_loop import _load, _mcnemar, _HAS

ARMS = ("A_open", "A_late_only", "A_full")
_CONTRASTS = (("A_open", "A_late_only"), ("A_open", "A_full"), ("A_late_only", "A_full"))
# Trace schema v3 computed checks on the full answer but stored final_answer[:1000], so a
# token past the cap is invisible here. A recomputed True is always sound (a token in the
# prefix is in the full text); a recomputed False on a v3 row stored at the cap is
# inconclusive — the stored value is kept and the row reported, never silently.
# Schema v4 stores the answer untruncated, so v4+ rows are always conclusive.
TRUNCATION_CAP = 1000


def rescore_rows(rows: list[dict], cases: dict[str, dict]) -> list[dict]:
    """Each row annotated with stored vs recomputed views. Defaults mirror
    `deterministic_checks.case_pass` (route False, judge False/None-falsy, rest True)."""
    out = []
    for r in rows:
        answer = r.get("final_answer") or ""
        mi_stored = bool(r.get("must_include_pass", True))
        mi_raw = check_must_include(answer, cases.get(r["case_id"], {}))
        # A missing version field (old fixtures, pre-v3 traces) defaults to 0 and keeps the
        # conservative v3 rule.
        truncating_schema = int(r.get("trace_schema_version") or 0) < 4
        inconclusive = truncating_schema and not mi_raw and len(answer) >= TRUNCATION_CAP
        mi_re = mi_stored if inconclusive else mi_raw
        others = [r.get("route_match", False), r.get("agent_match", True),
                  r.get("building_id_match", True), r.get("field_coverage_pass", True),
                  r.get("must_not_include_pass", True)]
        judge = bool(r.get("semantic_judge_pass") or False)
        out.append({
            "case_id": r["case_id"],
            "mi_stored": mi_stored, "mi_re": mi_re, "inconclusive": inconclusive,
            "det_stored": all(others + [mi_stored]),
            "det_re": all(others + [mi_re]),
            "judge_stored": bool(r.get("case_pass")),
            "judge_re": all(others + [mi_re, judge]),
        })
    return out


def rescore_replicate(run_dir: Path, cases: dict[str, dict]) -> dict:
    """Re-score all three arms of one replicate run, keyed by arm."""
    return {arm: rescore_rows(_load(run_dir / arm / "traces" / "per_case.jsonl"), cases)
            for arm in ARMS}


def flips(reps: dict[str, dict]) -> list[dict]:
    """Rows whose recomputed must-include verdict disagrees with the stored one.

    These are the cases where the frozen trace and a fresh re-score diverge, i.e. the
    ones any claim about the stored numbers has to account for.
    """
    return [{"run": rd, "arm": arm, **x}
            for rd, byarm in reps.items() for arm, rows in byarm.items()
            for x in rows if x["mi_re"] != x["mi_stored"]]


def consensus(reps: dict[str, dict], key: str) -> dict:
    """plan-eil-v21 §4 estimator: per case per arm, pass = majority of replicates."""
    need = len(reps) // 2 + 1
    out = {}
    for a, b in _CONTRASTS:
        tally: dict[str, dict[str, int]] = {a: {}, b: {}}
        ids: set[str] = set()
        for byarm in reps.values():
            for arm in (a, b):
                for x in byarm.get(arm) or []:
                    ids.add(x["case_id"])
                    if x[key]:
                        tally[arm][x["case_id"]] = tally[arm].get(x["case_id"], 0) + 1
        if not ids:
            continue
        cons = {arm: {c: {"case_pass": tally[arm].get(c, 0) >= need} for c in sorted(ids)}
                for arm in (a, b)}
        d = {"gained": sorted(c for c in ids if cons[b][c]["case_pass"] and not cons[a][c]["case_pass"]),
             "lost": sorted(c for c in ids if cons[a][c]["case_pass"] and not cons[b][c]["case_pass"]),
             "pass_a": sum(v["case_pass"] for v in cons[a].values()),
             "pass_b": sum(v["case_pass"] for v in cons[b].values())}
        if _HAS:
            d["mcnemar"] = _mcnemar(cons[a], cons[b])
        out[f"{a} vs {b}"] = d
    return out


def report(reps: dict[str, dict]) -> list[str]:
    """Render the stored-vs-recomputed comparison as markdown lines."""
    md = ["# Recomputed deterministic re-score", "",
          f"Replicates: {', '.join(reps)}", "",
          "## Per-arm counts, stored vs recomputed (n per arm)", "",
          "| replicate | arm | judge-incl stored | judge-incl recomputed | det-core stored | det-core recomputed |",
          "|---|---|---|---|---|---|"]
    for rd, byarm in reps.items():
        for arm, rows in byarm.items():
            if not rows:
                continue
            c = lambda k: sum(1 for x in rows if x[k])
            md.append(f"| {Path(rd).name} | {arm} | {c('judge_stored')}/{len(rows)} "
                      f"| {c('judge_re')}/{len(rows)} | {c('det_stored')}/{len(rows)} "
                      f"| {c('det_re')}/{len(rows)} |")

    md += ["", "## Consensus McNemar (pre-registered §4), recomputed", "",
           "| contrast | scoring | pass a/b | gained | lost | p |", "|---|---|---|---|---|---|"]
    for key, label in (("judge_re", "judge-inclusive"), ("det_re", "deterministic-core")):
        for name, d in consensus(reps, key).items():
            p = (d.get("mcnemar") or {}).get("p_value")
            md.append(f"| {name} | {label} | {d['pass_a']}/{d['pass_b']} "
                      f"| {len(d['gained'])}: {', '.join(d['gained']) or '—'} "
                      f"| {len(d['lost'])}: {', '.join(d['lost']) or '—'} "
                      f"| {'—' if p is None else format(p, '.4f')} |")

    fl = flips(reps)
    md += ["", f"## Flip list — rows where recomputed `must_include_pass` ≠ stored ({len(fl)})", ""]
    if fl:
        md += ["| replicate | arm | case | must_include | det-core case_pass | judge-incl case_pass |",
               "|---|---|---|---|---|---|"]
        arrow = lambda x, a, b: f"{x[a]} → {x[b]}"
        md += [f"| {Path(x['run']).name} | {x['arm']} | {x['case_id']} "
               f"| {arrow(x, 'mi_stored', 'mi_re')} | {arrow(x, 'det_stored', 'det_re')} "
               f"| {arrow(x, 'judge_stored', 'judge_re')} |" for x in fl]
    else:
        md.append("_none — recomputation agrees with every stored row._")

    inc = [{"run": rd, "arm": arm, **x}
           for rd, byarm in reps.items() for arm, rows in byarm.items()
           for x in rows if x["inconclusive"]]
    md += ["", f"## Inconclusive (truncated trace, stored value kept) — {len(inc)} rows", ""]
    md += ([f"- `{Path(x['run']).name}/{x['arm']}` {x['case_id']} (stored {x['mi_stored']})"
            for x in inc] or ["_none_"])
    if not _HAS:
        md += ["", "_numpy/statsmodels unavailable — McNemar skipped._"]
    return md


def main(argv=None):
    """CLI entry point; --run-dir repeats once per replicate."""
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", action="append", required=True, dest="run_dirs")
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    cases = {c["case_id"]: c for c in _load(Path(args.dataset))}
    reps = {d: rescore_replicate(Path(d), cases) for d in args.run_dirs}
    md = "\n".join(report(reps))
    print(md)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md + "\n", encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
