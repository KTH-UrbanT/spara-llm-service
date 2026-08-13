"""Check the five pre-registered predictions of `plan-eil-v20-fixes.md` across replicates.

`analyze_results_closed_loop.py` reports one run dir at a time and answers "what were the
arm rates?". It cannot answer "did the pre-registered predictions hold?", because four of
the five need either a stratum-specific denominator (n=77), the per-attempt checkpoint
record, or agreement across replicates. This script is that missing pass, and it also
carries the four analytics the fixes doc lists as "still to build":

  n=77 McNemar · capped-rate sensitivity · cross-replicate SD · evidence_present summary
  · early/late checkpoint firing rates

Measurement only — it never re-invokes the graph or the judge.

    python -m scripts.check_predictions --run-dir artifacts/runs/2026-08-13_r1 \
        --run-dir artifacts/runs/2026-08-13_r2 --run-dir artifacts/runs/2026-08-13_r3 \
        --out artifacts/runs/2026-08-13_predictions.md
"""
from __future__ import annotations
import argparse, json, statistics, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.analyze_results_closed_loop import _load, _mcnemar, _HAS

ARMS = ("A_open", "A_late_only", "A_full")
# The five generic cases whose only failure was a misrouted address request. Fix 5 exists
# for these and nothing else; naming them here is what makes prediction 4 falsifiable.
FIX5_TARGETS = ["EKR_GEN_017", "EKR_GEN_018", "EKR_GEN_019", "EKR_GEN_030",
                "DEMO_EXTRA_GEN_001"]
# Must NOT be re-routed: asking these for an address is the correct behaviour.
FIX5_GUARDS = ["DEMO_EXTRA_CLAR_001", "DEMO_EXTRA_CLAR_002"]
NOT_JUDGED = ("_fail_open", "_short_circuit", "_no_answer")


def _traces(run_dir: Path, arm: str, kind: str) -> list[dict]:
    return _load(run_dir / arm / "traces" / f"{kind}.jsonl")


def _by_id(rows: list[dict]) -> dict:
    return {r["case_id"]: r for r in rows}


def _n77(rows: list[dict]) -> list[dict]:
    """The 11 `combined` cases are concordant across arms, so they move the headline rate
    without moving any p-value. Reported as a named stratum, never silently dropped."""
    return [r for r in rows if r.get("expected_route") != "combined"]


def _rate(rows: list[dict], key: str = "case_pass") -> tuple[int, int]:
    return sum(1 for r in rows if r.get(key)), len(rows)


def _fmt(n: int, d: int) -> str:
    return f"{n}/{d} = {(n / d if d else 0.0):.3f}"


def _judged(attempts: list[dict]) -> list[dict]:
    out = []
    for a in attempts:
        ax = a.get("evaluator_axes_json") or {}
        if ax and not any(ax.get(m) for m in NOT_JUDGED):
            out.append(a)
    return out


def _verdict(a: dict) -> str | None:
    return (a.get("evaluator_verdict_json") or {}).get("verdict")


def replicate(run_dir: Path) -> dict:
    """Everything one replicate can answer on its own."""
    pc = {a: _traces(run_dir, a, "per_case") for a in ARMS}
    pa = {a: _traces(run_dir, a, "per_attempt") for a in ARMS}
    d: dict = {"run_dir": str(run_dir), "arms": {}}

    for arm in ARMS:
        rows = pc[arm]
        if not rows:
            continue
        att = pa[arm]
        fails = [a for a in _judged(att) if _verdict(a) == "fail"]
        early = [a for a in att if a.get("checkpoint_fired") == "early"]
        late = [a for a in att if a.get("checkpoint_fired") == "late"]
        # Capped-rate sensitivity: Fix 2 took cost/wall out of case_pass. This is what the
        # rate WOULD be if they still vetoed — the disclosure, not a second headline.
        capped = [r for r in rows
                  if r.get("case_pass") and not (r.get("cost_exceeded")
                                                 or r.get("wall_budget_exceeded"))]
        d["arms"][arm] = {
            "n": len(rows),
            "pass_88": _rate(rows),
            "pass_77": _rate(_n77(rows)),
            "pass_capped": (len(capped), len(rows)),
            "cost_exceeded": sum(1 for r in rows if r.get("cost_exceeded")),
            "wall_exceeded": sum(1 for r in rows if r.get("wall_budget_exceeded")),
            "evidence_absent": sum(1 for r in rows if r.get("evidence_present") is False),
            "clarification_fired": sum(1 for r in rows if r.get("clarification_fired")),
            "request_address_fired": sum(1 for r in rows if r.get("request_address_fired")),
            "rewound": sum(1 for r in rows if (r.get("total_attempts") or 1) > 1),
            "attempts": len(att),
            "early_fired": len(early),
            "early_rewind": sum(1 for a in early if a.get("controller_action") == "rewind"),
            "late_fired": len(late),
            "late_rewind": sum(1 for a in late if a.get("controller_action") == "rewind"),
            "judged_fail": len(fails),
            "judged_pass": sum(1 for a in _judged(att) if _verdict(a) == "pass"),
            # Attribution is only actionable on a FAILING attempt; counting it over passing
            # attempts reports whichever axis happened to be lowest on a good answer.
            "attrib_fail": _tally(fails, "evaluator_stage_attribution_rule"),
            "strata": _strata(rows),
        }

    d["mcnemar"] = {}
    if _HAS:
        for a, b in (("A_open", "A_late_only"), ("A_open", "A_full"),
                     ("A_late_only", "A_full")):
            if pc.get(a) and pc.get(b):
                d["mcnemar"][f"{a} vs {b}"] = {
                    "n88": _mcnemar(_by_id(pc[a]), _by_id(pc[b])),
                    "n77": _mcnemar(_by_id(_n77(pc[a])), _by_id(_n77(pc[b]))),
                }

    d["fix5"] = _fix5(pc)
    return d


def _tally(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _strata(rows: list[dict]) -> dict:
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r.get("expected_route", "?"), []).append(r)
    return {k: _rate(v) for k, v in sorted(by.items())}


def _fix5(pc: dict) -> dict:
    """Prediction 4: did the early checkpoint rescue the five address-misroutes without
    disturbing the two cases where asking for an address is correct?"""
    o, f = _by_id(pc.get("A_open") or []), _by_id(pc.get("A_full") or [])
    fired = [c for c in FIX5_TARGETS
             if (f.get(c, {}).get("total_attempts") or 1) > 1]
    converted = [c for c in FIX5_TARGETS
                 if f.get(c, {}).get("case_pass") and not o.get(c, {}).get("case_pass")]
    guards_ok = [c for c in FIX5_GUARDS if f.get(c, {}).get("request_address_fired")]
    return {"fired": fired, "converted": converted, "guards_held": guards_ok,
            "guards_total": len(FIX5_GUARDS)}


def _sd(vals: list[float]) -> float:
    return statistics.stdev(vals) if len(vals) > 1 else 0.0


def report(reps: list[dict]) -> list[str]:
    md = ["# Pre-registered prediction check", "",
          f"Replicates: {len(reps)} — " + ", ".join(r["run_dir"] for r in reps), ""]

    # ---- cross-replicate arm rates (the "within-arm SD" the fixes doc asks for) ----
    md += ["## Arm rates across replicates", "",
           "| arm | pass n=88 (mean ± SD) | pass n=77 (mean ± SD) | per replicate (n=77) |",
           "|---|---|---|---|"]
    for arm in ARMS:
        got = [r["arms"][arm] for r in reps if arm in r["arms"]]
        if not got:
            continue
        r88 = [n / d for n, d in (g["pass_88"] for g in got)]
        r77 = [n / d for n, d in (g["pass_77"] for g in got)]
        each = ", ".join(_fmt(*g["pass_77"]).split(" = ")[0] for g in got)
        md.append(f"| {arm} | {statistics.mean(r88):.3f} ± {_sd(r88):.3f} "
                  f"| {statistics.mean(r77):.3f} ± {_sd(r77):.3f} | {each} |")

    # ---- evidence + short-circuit summary ----
    md += ["", "## Evidence and short-circuit summary (per replicate)", "",
           "| replicate | arm | evidence absent | clarification | request_address | rewound |",
           "|---|---|---:|---:|---:|---:|"]
    for r in reps:
        for arm, g in r["arms"].items():
            md.append(f"| {Path(r['run_dir']).name} | {arm} | {g['evidence_absent']}/{g['n']} "
                      f"| {g['clarification_fired']} | {g['request_address_fired']} "
                      f"| {g['rewound']} |")

    # ---- checkpoint firing rates ----
    md += ["", "## Checkpoint firing rates", "",
           "| replicate | arm | attempts | early fired | early→rewind | late fired | "
           "late→rewind | judge pass/fail | attribution on failures |",
           "|---|---|---:|---:|---:|---:|---:|---|---|"]
    for r in reps:
        for arm, g in r["arms"].items():
            md.append(f"| {Path(r['run_dir']).name} | {arm} | {g['attempts']} "
                      f"| {g['early_fired']} | {g['early_rewind']} | {g['late_fired']} "
                      f"| {g['late_rewind']} | {g['judged_pass']}/{g['judged_fail']} "
                      f"| {g['attrib_fail'] or '—'} |")

    # ---- capped-rate sensitivity ----
    md += ["", "## Capped-rate sensitivity (Fix 2 disclosure)", "",
           "Fix 2 removed the cost/wall vetoes from `case_pass` because they applied to the "
           "treatment arms only. This is what the rate would have been had they been kept — "
           "the confound, quantified.", "",
           "| replicate | arm | uncapped | capped | cost_exceeded | wall_exceeded |",
           "|---|---|---|---|---:|---:|"]
    for r in reps:
        for arm, g in r["arms"].items():
            md.append(f"| {Path(r['run_dir']).name} | {arm} | {_fmt(*g['pass_88'])} "
                      f"| {_fmt(*g['pass_capped'])} | {g['cost_exceeded']} "
                      f"| {g['wall_exceeded']} |")

    # ---- McNemar, both denominators ----
    md += ["", "## McNemar (exact), n=88 and n=77", "",
           "| replicate | contrast | n | discordant | b better | a better | p |",
           "|---|---|---:|---:|---:|---:|---:|"]
    for r in reps:
        for name, both in r["mcnemar"].items():
            for lbl, m in (("88", both["n88"]), ("77", both["n77"])):
                md.append(f"| {Path(r['run_dir']).name} | {name} | {lbl} "
                          f"| {m['n_discordant']} | {m['n_b_better']} | {m['n_a_better']} "
                          f"| {m['p_value']:.4f} |")
    if not _HAS:
        md += ["", "_numpy/statsmodels unavailable — McNemar skipped._"]

    # ---- the five predictions ----
    md += ["", "## The five pre-registered predictions", ""]
    md += _verdicts(reps)
    return md


def _verdicts(reps: list[dict]) -> list[str]:
    out = ["| # | prediction | observed | verdict |", "|---|---|---|---|"]

    ev = [r["arms"]["A_open"]["evidence_absent"] for r in reps if "A_open" in r["arms"]]
    ok1 = all(v >= 45 for v in ev)
    out.append(f"| 1 | `evidence_present == false` on ≥45 of 88 A_open | {ev} "
               f"| {'PASS' if ok1 else 'FAIL'} |")

    p2 = [r["arms"]["A_open"]["pass_77"] for r in reps if "A_open" in r["arms"]]
    ok2 = all(30 <= n <= 38 for n, _ in p2)
    out.append(f"| 2 | A_open `case_pass` in 30–38 of 77 | {[n for n, _ in p2]} "
               f"| {'PASS' if ok2 else 'FAIL'} |")

    lr, attr = [], []
    for r in reps:
        for arm in ("A_late_only", "A_full"):
            if arm in r["arms"]:
                lr.append(r["arms"][arm]["late_rewind"])
                attr.append(next(iter(r["arms"][arm]["attrib_fail"]), None))
    ok3 = all(v < 20 for v in lr) and not any(a == "summarizer" for a in attr if a)
    out.append(f"| 3 | late rewinds < 20 **and** top attribution ≠ summarizer "
               f"| rewinds {lr}, attribution {attr} | {'PASS' if ok3 else 'FAIL'} |")

    f5 = [r["fix5"] for r in reps]
    ok4 = all(len(x["fired"]) >= 3 and len(x["converted"]) >= 2
              and len(x["guards_held"]) == x["guards_total"] for x in f5)
    out.append(f"| 4 | Fix 5 fires ≥3 of 5, ≥2 convert, both guards hold "
               f"| fired {[len(x['fired']) for x in f5]}, "
               f"converted {[len(x['converted']) for x in f5]}, "
               f"guards {[len(x['guards_held']) for x in f5]} | {'PASS' if ok4 else 'FAIL'} |")

    wins = sum(1 for r in reps
               if "A_full" in r["arms"] and "A_open" in r["arms"]
               and r["arms"]["A_full"]["pass_88"][0] >= r["arms"]["A_open"]["pass_88"][0])
    # "2 of 3" is undecidable before 3 replicates: fewer than 2 wins with runs still to
    # come is PENDING, not FAIL. Only a third replicate can settle it either way.
    if wins >= 2:
        v5 = "PASS"
    elif len(reps) - wins >= 2:
        v5 = "FAIL"          # already lost 2 — a third win cannot rescue it
    else:
        v5 = f"PENDING ({3 - len(reps)} replicate(s) to go)"
    out.append(f"| 5 | A_full ≥ A_open in ≥2 of 3 replicates | {wins} of {len(reps)} "
               f"| {v5} |")

    out += ["", "Per-replicate Fix 5 detail:", ""]
    for r, x in zip(reps, f5):
        out.append(f"- `{Path(r['run_dir']).name}` fired={x['fired']} "
                   f"converted={x['converted']} guards_held={x['guards_held']}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", action="append", required=True, dest="run_dirs")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    reps = [replicate(Path(d)) for d in args.run_dirs]
    reps = [r for r in reps if r["arms"]]
    if not reps:
        print("ERROR: no arm traces found in any run dir", file=sys.stderr)
        return 1

    # A run still in flight has a short arm, and every rate below would read as final.
    # Prediction 4 in particular fails spuriously, because its two guard cases sit at the
    # end of the dataset and simply have not run yet.
    for r in reps:
        counts = {a: g["n"] for a, g in r["arms"].items()}
        if len(set(counts.values())) > 1 or len(counts) < len(ARMS):
            print(f"WARNING: {r['run_dir']} looks incomplete — rows per arm {counts}. "
                  f"Verdicts below are provisional.", file=sys.stderr)

    md = "\n".join(report(reps))
    print(md)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md + "\n", encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
