"""Analyse a completed run: case-pass rate, Holm-corrected McNemar, breakdowns.

Usage:
    python -m scripts.analyze_results_closed_loop \
        --run-dir artifacts/runs/2026-06-01 --out artifacts/runs/2026-06-01/analysis.md
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

try:
    import numpy as np
    from statsmodels.stats.contingency_tables import mcnemar as _mc
    _HAS = True
except ImportError:
    _HAS = False

ARMS = ("A_open", "A_late_only", "A_full")
CHECKS = ["route_match","agent_match","building_id_match","field_coverage_pass",
          "must_include_pass","must_not_include_pass","semantic_judge_pass"]


def _load(path: Path) -> list[dict]:
    """Read a JSONL trace file; empty list if the arm never ran."""
    # split('\n') (not splitlines) so embedded U+2028/U+2029 in a value can't split a record.
    return [json.loads(l) for l in path.read_text(encoding="utf-8").split("\n") if l.strip()] if path.exists() else []


def _rate(t: list[dict]) -> float:
    """Overall case-pass rate: the headline number for an arm."""
    return sum(1 for x in t if x.get("case_pass")) / len(t) if t else 0.0


def _first_attempt_rate(t: list[dict]) -> float:
    """Pass rate over cases the loop never retried.

    The closed-loop arms are only comparable to the open arm on this subset when the
    loop stayed idle, so a gap here would mean the arms diverged before any rewind fired.
    """
    fa = [x for x in t if (x.get("total_attempts") or 1) <= 1]
    return sum(1 for x in fa if x.get("case_pass")) / len(fa) if fa else 0.0


def _strat(t: list[dict]) -> dict:
    """Pass counts grouped by the case's expected route."""
    by: dict[str, list] = {}
    for x in t:
        by.setdefault(x.get("expected_route","?"), []).append(x)
    return {r: {"n": len(cs), "n_pass": sum(1 for c in cs if c.get("case_pass")),
                "rate": sum(1 for c in cs if c.get("case_pass"))/len(cs)} for r, cs in by.items()}


def _mcnemar(ta: dict, tb: dict) -> dict:
    """Paired significance test on the same cases run through two arms.

    Paired, not two-sample: both arms answer an identical case list, so the test only
    looks at cases where the arms DISAGREED. n01 = arm A failed and B passed (B better),
    n10 = A passed and B failed (B broke it). The concordant cells carry no information
    about which arm is better and drop out of the statistic entirely.

    exact=True uses the binomial test rather than the chi-square approximation: with the
    handful of discordant pairs this study produces, the asymptotic form is not valid.
    """
    shared = sorted(set(ta) & set(tb))
    a = np.array([int(bool(ta[c].get("case_pass"))) for c in shared])
    b = np.array([int(bool(tb[c].get("case_pass"))) for c in shared])
    n01 = int(((a==0)&(b==1)).sum()); n10 = int(((a==1)&(b==0)).sum())
    # Table laid out as [[n00, n01], [n10, n11]] — statsmodels reads the off-diagonal.
    r = _mc(np.array([[int(((a==0)&(b==0)).sum()),n01],[n10,int(((a==1)&(b==1)).sum())]]), exact=True)
    return {"n_discordant": n01+n10, "n_b_better": n01, "n_a_better": n10, "p_value": float(r.pvalue)}


def _hint_eff(attempts: list[dict]) -> dict:
    """Hint-effectiveness counts diffs across SAME-KIND checkpoint records — early
    vs early, or late vs late, within one case — so we measure rewind cycles rather
    than the structural diff between the early-record schema and the late-record
    schema (which would always flag 'changed' even when no rewind fired)."""
    by: dict[tuple[str, str], list] = {}
    for a in attempts:
        kind = a.get("checkpoint_fired") or ""
        by.setdefault((a.get("case_id", ""), kind), []).append(a)
    nr = nc = 0
    for _, recs in by.items():
        if len(recs) < 2:
            continue  # a single record of one kind is not a retry
        rs = sorted(recs, key=lambda x: x.get("attempt_index", 0))
        for i in range(1, len(rs)):
            nr += 1
            p, c = rs[i - 1], rs[i]
            if (p.get("route_picked_this_attempt") != c.get("route_picked_this_attempt")
                    or p.get("specialists_picked_this_attempt") != c.get("specialists_picked_this_attempt")
                    or p.get("answer_drafted_this_attempt") != c.get("answer_drafted_this_attempt")):
                nc += 1
    return {"n_retries": nr, "n_changed": nc, "change_rate": nc/nr if nr else None}


def run(args) -> int:
    """Build the whole analysis report for a run directory and print or write it."""
    if not _HAS:
        print("ERROR: numpy + statsmodels required", file=sys.stderr); return 1
    rd = Path(args.run_dir)
    avail = [a for a in ARMS if (rd / a).exists()]
    traces = {a: _load(rd / a / "traces" / "per_case.jsonl") for a in avail}
    attempts = {a: _load(rd / a / "traces" / "per_attempt.jsonl") for a in avail}

    L = [f"# Closed-loop analysis: {rd.name}\n", "## Case-pass rate\n",
         "| Arm | N | Pass | Pass rate | 1st-attempt rate |", "|---|---|---|---|---|"]
    for a in avail:
        t = traces[a]
        L.append(f"| {a} | {len(t)} | {sum(1 for x in t if x.get('case_pass'))} | {_rate(t):.3f} | {_first_attempt_rate(t):.3f} |")

    L.append("\n## McNemar paired tests (Holm-corrected)\n")
    results = []
    for aa, ab in [("A_open","A_full"),("A_late_only","A_full")]:
        if aa not in traces or ab not in traces: continue
        ta = {t["case_id"]: t for t in traces[aa]}; tb = {t["case_id"]: t for t in traces[ab]}
        if len(set(ta)&set(tb)) < 2: continue
        results.append(((aa, ab), _mcnemar(ta, tb)))
    # Holm-Bonferroni step-down over the m pairwise tests. Sort p-values ascending and
    # test each against a threshold that relaxes as tests are consumed: 0.05/m, then
    # 0.05/(m-1), ... Controls the family-wise error rate while being uniformly more
    # powerful than plain Bonferroni, which would hold every test to 0.05/m.
    m = len(results)
    order = sorted(range(m), key=lambda i: results[i][1]["p_value"])
    holm, still = {}, True
    for rank, idx in enumerate(order):
        thr = 0.05 / (m - rank)
        rej = still and results[idx][1]["p_value"] < thr
        # `still` is the step-down stop: once one test fails to clear its threshold, every
        # test after it (which has a LARGER p-value) must also fail, regardless of its own
        # threshold. Dropping this would let a later test be "significant" while an earlier,
        # stronger one was not — the non-monotonicity Holm exists to prevent.
        if not rej: still = False
        holm[idx] = (rej, thr)
    for idx, ((aa, ab), r) in enumerate(results):
        rej, thr = holm.get(idx, (False, 0.05))
        L.append(f"**{aa} vs {ab}**: n_discordant={r['n_discordant']}, n({ab} better)={r['n_b_better']}, "
                 f"p={r['p_value']:.4f}, {'reject at Holm '+format(thr,'.4f') if rej else 'ns'}")

    L.append("\n## Stratified pass rate\n")
    for a in avail:
        L.append(f"### {a}\n| Route | N | Pass | Rate |\n|---|---|---|---|")
        for route, d in sorted(_strat(traces[a]).items()):
            L.append(f"| {route} | {d['n']} | {d['n_pass']} | {d['rate']:.3f} |")

    L.append("\n## Per-check pass rate\n")
    L.append("| Check | " + " | ".join(avail) + " |")
    L.append("|---|" + "---|"*len(avail))
    for ck in CHECKS:
        row = [ck]
        for a in avail:
            ap = [x for x in traces[a] if x.get(ck) is not None]
            row.append(f"{sum(1 for x in ap if x.get(ck))/len(ap):.3f}" if ap else "n/a")
        L.append("| " + " | ".join(row) + " |")

    L.append("\n## Hint effectiveness\n")
    for a in ("A_late_only","A_full"):
        if a not in attempts: continue
        he = _hint_eff(attempts[a])
        L.append(f"**{a}**: {he['n_retries']} retries, {he['n_changed']} changed ({(he['change_rate'] or 0):.1%})")

    L.append("\n## Retry, latency & cost\n")
    L.append("| Arm | retried | terminated-no-pass | router-disallowed | mean ms | p95 ms | p99 ms | tokens | cost-exceeded |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for a in avail:
        t = traces[a]
        lat = sorted((x.get("total_latency_ms") or 0.0) for x in t)
        if lat:
            mean = sum(lat)/len(lat)
            p95 = lat[min(len(lat)-1, int(0.95*len(lat)))]
            p99 = lat[min(len(lat)-1, int(0.99*len(lat)))]
        else:
            mean = p95 = p99 = 0.0
        L.append(f"| {a} | {sum(1 for x in t if (x.get('total_attempts') or 1)>1)} | "
                 f"{sum(1 for x in t if x.get('closed_loop_terminated_without_pass'))} | "
                 f"{sum(1 for x in t if x.get('terminated_router_disallowed'))} | "
                 f"{mean:.0f} | {p95:.0f} | {p99:.0f} | {sum(x.get('total_tokens',0) for x in t)} | "
                 f"{sum(1 for x in t if x.get('cost_exceeded'))} |")

    out = "\n".join(L)
    if args.out:
        p = Path(args.out); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out, encoding="utf-8"); print(f"Written: {args.out}")
    else:
        print(out)
    return 0


def parse_args(argv=None):
    """CLI: --run-dir is required, --out defaults to stdout."""
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True)
    p.add_argument("--out", default=None)
    return p.parse_args(argv)


def main(argv=None):
    """Entry point; exit status is `run`'s return code."""
    sys.exit(run(parse_args(argv)))

if __name__ == "__main__":
    main()
