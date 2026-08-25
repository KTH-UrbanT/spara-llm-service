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
# The generic cases whose only failure was a misrouted address request. Fix 5 exists for
# these and nothing else; naming them here is what makes prediction 4 falsifiable.
FIX5_TARGETS = ["EKR_GEN_017", "EKR_GEN_018", "EKR_GEN_019", "DEMO_EXTRA_GEN_001"]
# EKR_GEN_030 fired in only 1 of 3 single-shot replicates, so v21's k=3 majority vote is
# expected to suppress it. Declared in advance as a deliberate trade (a false fire destroys
# a case permanently, §1.4) and moved out of the pinned target list: tracked, not counted.
FIX5_TRACKED = ["EKR_GEN_030"]
# Must NOT be re-routed: asking these for an address is the correct behaviour.
FIX5_GUARDS = ["DEMO_EXTRA_CLAR_001", "DEMO_EXTRA_CLAR_002"]
NOT_JUDGED = ("_fail_open", "_short_circuit", "_no_answer")


def _traces(run_dir: Path, arm: str, kind: str) -> list[dict]:
    """Load one arm's per_case or per_attempt trace file."""
    return _load(run_dir / arm / "traces" / f"{kind}.jsonl")


def _by_id(rows: list[dict]) -> dict:
    """Index trace rows by case_id, for pairing the same case across arms."""
    return {r["case_id"]: r for r in rows}


# v22 R1: the six gold checks, i.e. case_pass minus `semantic_judge_pass` — the one term the
# treatment arms retry until it flips. Defaults mirror deterministic_checks.case_pass.
def _det_pass(r: dict) -> bool:
    """Deterministic-core pass: every gold check EXCEPT the LLM judge.

    The judge-free view of the same row, so a result can be reported without the
    judge in the loop — the reviewer objection that the judge marks its own homework.
    """
    return all([r.get("route_match", False), r.get("agent_match", True),
                r.get("building_id_match", True), r.get("field_coverage_pass", True),
                r.get("must_include_pass", True), r.get("must_not_include_pass", True)])


def _det_view(rows: list[dict]) -> dict:
    """Rows re-keyed with deterministic-core scoring, shaped for `_mcnemar`."""
    return {r["case_id"]: {"case_pass": _det_pass(r)} for r in rows}


def _n77(rows: list[dict]) -> list[dict]:
    """The 11 `combined` cases are concordant across arms, so they move the headline rate
    without moving any p-value. Reported as a named stratum, never silently dropped."""
    return [r for r in rows if r.get("expected_route") != "combined"]


def _rate(rows: list[dict], key: str = "case_pass") -> tuple[int, int]:
    """(passes, total) for one boolean column."""
    return sum(1 for r in rows if r.get(key)), len(rows)


def _fmt(n: int, d: int) -> str:
    """Format a count as "n/d = rate", tolerating d == 0."""
    return f"{n}/{d} = {(n / d if d else 0.0):.3f}"


def _judged(attempts: list[dict]) -> list[dict]:
    """Attempts the judge genuinely scored — no short-circuits, no fail-opens.

    Any rate computed over unfiltered attempts silently counts a crashed judge as a
    pass; see `NOT_JUDGED` and `analysis_filters`.
    """
    out = []
    for a in attempts:
        ax = a.get("evaluator_axes_json") or {}
        if ax and not any(ax.get(m) for m in NOT_JUDGED):
            out.append(a)
    return out


def _verdict(a: dict) -> str | None:
    """The judge's verdict string on one attempt, or None if it never ran."""
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
            # v22 R1: the treatment-independent scoring, alongside the published one.
            "pass_88_det": (sum(1 for r in rows if _det_pass(r)), len(rows)),
            "pass_77_det": (sum(1 for r in _n77(rows) if _det_pass(r)), len(_n77(rows))),
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
            # v21 P5: the diagnostic column, over failing judged attempts only.
            "attrib_diag_fail": _tally(fails, "attribution_diagnostic"),
            "attrib_on_pass": sum(1 for a in _judged(att) if _verdict(a) == "pass"
                                  and a.get("evaluator_stage_attribution_rule") is not None),
            "early_block_exhausted": sum(1 for r in rows if r.get("early_block_exhausted")),
            "forced_route_applied": sum(1 for r in rows if r.get("forced_route_applied")),
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
                    "n88_det": _mcnemar(_det_view(pc[a]), _det_view(pc[b])),
                }

    d["fix5"] = _fix5(pc)
    d["reverse"] = _reverse(pc)
    return d


def _tally(rows: list[dict], key: str) -> dict:
    """Count distinct values of one column, most frequent first."""
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _strata(rows: list[dict]) -> dict:
    """(passes, total) per expected route."""
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r.get("expected_route", "?"), []).append(r)
    return {k: _rate(v) for k, v in sorted(by.items())}


def _fix5(pc: dict) -> dict:
    """Prediction 4: did the early checkpoint rescue the address-misroutes without
    disturbing the two cases where asking for an address is correct?"""
    o, f = _by_id(pc.get("A_open") or []), _by_id(pc.get("A_full") or [])
    _fired = lambda ids: [c for c in ids if (f.get(c, {}).get("total_attempts") or 1) > 1]
    _conv = lambda ids: [c for c in ids if f.get(c, {}).get("case_pass")
                         and not o.get(c, {}).get("case_pass")]
    return {"fired": _fired(FIX5_TARGETS), "converted": _conv(FIX5_TARGETS),
            "guards_held": [c for c in FIX5_GUARDS if f.get(c, {}).get("request_address_fired")],
            "guards_total": len(FIX5_GUARDS),
            # Reported, never gated — see FIX5_TRACKED.
            "tracked_fired": _fired(FIX5_TRACKED), "tracked_converted": _conv(FIX5_TRACKED),
            "forced_route_applied": sorted(c for c, r in f.items()
                                           if r.get("forced_route_applied"))}


def _reverse(pc: dict) -> dict:
    """v21 P1 clause 2 — the cost side of prompt v2's symmetry.

    The capability criterion is symmetric by construction: it rejects `generic` on a
    question that needs the user's building just as it rejects `building` on one that does
    not. That is what converts CLAR_001, and the same mechanism could in principle drag a
    correctly-generic case onto the building route and lose it. Measured at 0 of 33 in the
    pre-run canary (`artifacts/runs/smoke_v21_reverse`); this is the check that it stays 0.
    """
    o, f = _by_id(pc.get("A_open") or []), _by_id(pc.get("A_full") or [])
    return {
        "generic_lost_to_building": [
            c for c, r in f.items()
            if r.get("expected_route") == "generic"
            and o.get(c, {}).get("case_pass") and not r.get("case_pass")
            and r.get("final_route_taken") == "building"],
        "clarification_gained": [
            c for c, r in f.items()
            if r.get("expected_route") == "clarification"
            and r.get("case_pass") and not o.get(c, {}).get("case_pass")],
    }


def consensus_mcnemar(run_dirs: list[Path], a: str = "A_open", b: str = "A_full",
                      scoring: str = "judge") -> dict:
    """The pre-registered primary *interpretive* statistic (plan-eil-v21 §4).

    Per case per arm, `pass` = passes in a majority of replicates; then one exact McNemar
    over the 88 consensus pairs. This is the correct estimator of the systematic case-level
    effect: bidirectional single-replicate churn (§1.5) cancels, while reproducible flips
    survive. Declared before the run, with its own arithmetic stated — 5-0 gives p = 0.0625
    and >= 6-0 is required for p < 0.05 — so it could not be reverse-engineered afterwards.
    """
    need = len(run_dirs) // 2 + 1
    # v22 R1: scoring="det" applies the deterministic-core measure to the same estimator.
    passed = _det_pass if scoring == "det" else (lambda r: bool(r.get("case_pass")))
    tally: dict[str, dict[str, int]] = {a: {}, b: {}}
    for rd in run_dirs:
        for arm in (a, b):
            for r in _traces(rd, arm, "per_case"):
                if passed(r):
                    tally[arm][r["case_id"]] = tally[arm].get(r["case_id"], 0) + 1
    ids = sorted({r["case_id"] for rd in run_dirs for r in _traces(rd, a, "per_case")})
    cons = {arm: {c: {"case_pass": tally[arm].get(c, 0) >= need} for c in ids} for arm in (a, b)}
    out = {"n_replicates": len(run_dirs), "majority_needed": need, "n_cases": len(ids),
           "scoring": scoring,
           "gained": sorted(c for c in ids
                            if cons[b][c]["case_pass"] and not cons[a][c]["case_pass"]),
           "lost": sorted(c for c in ids
                          if cons[a][c]["case_pass"] and not cons[b][c]["case_pass"])}
    if _HAS:
        out["mcnemar"] = _mcnemar(cons[a], cons[b])
    return out


def _sd(vals: list[float]) -> float:
    """Sample SD, or 0.0 when there is only one replicate to compare."""
    return statistics.stdev(vals) if len(vals) > 1 else 0.0


_CONTRASTS = (("A_open", "A_late_only"), ("A_open", "A_full"), ("A_late_only", "A_full"))


def _consensus_md(cons: dict) -> list[str]:
    """v22 R1: the §4 estimator under both scorings, in the markdown and not only the JSON."""
    if not cons:
        return []
    md = ["", "## Consensus McNemar (pre-registered §4), both scorings", "",
          "| contrast | scoring | gained | lost | p |", "|---|---|---|---|---|"]
    for s, label in (("judge", "judge-inclusive"), ("det", "deterministic-core")):
        for name, c in cons[s].items():
            p = (c.get("mcnemar") or {}).get("p_value")
            md.append(f"| {name} | {label} | {len(c['gained'])}: {', '.join(c['gained']) or '—'} "
                      f"| {len(c['lost'])}: {', '.join(c['lost']) or '—'} "
                      f"| {'—' if p is None else format(p, '.4f')} |")
    return md


def report(reps: list[dict]) -> list[str]:
    """Render the full cross-replicate markdown report.

    Covers per-arm rates with their between-replicate SD, the deterministic-core view,
    per-stratum breakdowns, and the pre-registered prediction verdicts.
    """
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

    # ---- v22 R1: judge-inclusive vs deterministic-core ----
    md += ["", "## Dual scoring (v22 R1): judge-inclusive vs deterministic-core", "",
           "Deterministic-core drops `semantic_judge_pass` — the one `case_pass` term the "
           "treatment retries until it flips — and keeps the six gold checks.", "",
           "| replicate | arm | judge-inclusive n=88 | deterministic-core n=88 |",
           "|---|---|---|---|"]
    for r in reps:
        for arm, g in r["arms"].items():
            md.append(f"| {Path(r['run_dir']).name} | {arm} | {_fmt(*g['pass_88'])} "
                      f"| {_fmt(*g['pass_88_det'])} |")

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
            for lbl, m in (("88", both["n88"]), ("77", both["n77"]),
                           ("88 det", both["n88_det"])):
                md.append(f"| {Path(r['run_dir']).name} | {name} | {lbl} "
                          f"| {m['n_discordant']} | {m['n_b_better']} | {m['n_a_better']} "
                          f"| {m['p_value']:.4f} |")
    if not _HAS:
        md += ["", "_numpy/statsmodels unavailable — McNemar skipped._"]

    # ---- the five predictions ----
    md += ["", "## The five pre-registered predictions", ""]
    md += _verdicts(reps)
    return md


def verdicts(reps: list[dict]) -> list[dict]:
    """The five pre-registered predictions of `plan-eil-v20-fixes.md`, as data.

    Built as dicts so the markdown and the `--out-json` dump are the same numbers rather
    than two renderings that can drift apart (v21 §2.5).
    """
    out: list[dict] = []
    _v = lambda ok: "PASS" if ok else "FAIL"

    ev = [r["arms"]["A_open"]["evidence_absent"] for r in reps if "A_open" in r["arms"]]
    out.append({"n": 1, "prediction": "`evidence_present == false` on ≥45 of 88 A_open",
                "observed": ev, "verdict": _v(all(v >= 45 for v in ev))})

    p2 = [n for n, _ in (r["arms"]["A_open"]["pass_77"] for r in reps if "A_open" in r["arms"])]
    out.append({"n": 2, "prediction": "A_open `case_pass` in 30–38 of 77",
                "observed": p2, "verdict": _v(all(30 <= n <= 38 for n in p2))})

    lr, attr = [], []
    for r in reps:
        for arm in ("A_late_only", "A_full"):
            if arm in r["arms"]:
                lr.append(r["arms"][arm]["late_rewind"])
                attr.append(next(iter(r["arms"][arm]["attrib_fail"]), None))
    out.append({"n": 3, "prediction": "late rewinds < 20 **and** top attribution ≠ summarizer",
                "observed": {"late_rewinds": lr, "top_attribution": attr},
                "verdict": _v(all(v < 20 for v in lr)
                              and not any(a == "summarizer" for a in attr if a))})

    f5 = [r["fix5"] for r in reps]
    out.append({"n": 4,
                "prediction": f"Fix 5 fires ≥3 of {len(FIX5_TARGETS)}, ≥2 convert, both guards hold",
                "observed": {"fired": [len(x["fired"]) for x in f5],
                             "converted": [len(x["converted"]) for x in f5],
                             "guards_held": [len(x["guards_held"]) for x in f5],
                             "tracked_fired": [x["tracked_fired"] for x in f5]},
                "verdict": _v(all(len(x["fired"]) >= 3 and len(x["converted"]) >= 2
                                  and len(x["guards_held"]) == x["guards_total"] for x in f5))})

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
    out.append({"n": 5, "prediction": "A_full ≥ A_open in ≥2 of 3 replicates",
                "observed": f"{wins} of {len(reps)}", "verdict": v5})
    return out


def _verdicts(reps: list[dict]) -> list[str]:
    """Render `verdicts` as a markdown table of prediction vs observed."""
    out = ["| # | prediction | observed | verdict |", "|---|---|---|---|"]
    for v in verdicts(reps):
        obs = v["observed"]
        if isinstance(obs, dict):
            obs = ", ".join(f"{k} {val}" for k, val in obs.items())
        out.append(f"| {v['n']} | {v['prediction']} | {obs} | {v['verdict']} |")

    out += ["", "Per-replicate Fix 5 detail:", ""]
    for r in reps:
        x = r["fix5"]
        out.append(f"- `{Path(r['run_dir']).name}` fired={x['fired']} "
                   f"converted={x['converted']} guards_held={x['guards_held']} "
                   f"tracked={x['tracked_fired']} forced_route={x['forced_route_applied']}")
    return out


def main(argv=None):
    """CLI entry point; --run-dir repeats once per replicate."""
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", action="append", required=True, dest="run_dirs")
    p.add_argument("--out", default=None)
    p.add_argument("--out-json", default=None,
                   help="Dump {replicates, verdicts} — the machine-readable form the v20 "
                        "run lacked, so a later re-analysis can be diffed against it.")
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

    rds = [Path(d) for d in args.run_dirs]
    cons = ({s: {f"{a} vs {b}": consensus_mcnemar(rds, a, b, scoring=s)
                 for a, b in _CONTRASTS} for s in ("judge", "det")}
            if len(rds) > 1 else {})
    md = "\n".join(report(reps) + _consensus_md(cons))
    print(md)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md + "\n", encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out_json).write_text(
            json.dumps({"replicates": reps, "verdicts": verdicts(reps),
                        # The §4 primary interpretive statistic belongs in the artifact, not
                        # only in a write-up that could drift from it. Both scorings (v22 R1).
                        "consensus": cons.get("judge", {}),
                        "consensus_det": cons.get("det", {})},
                       indent=2, default=str) + "\n", encoding="utf-8")
        print(f"Wrote {args.out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
