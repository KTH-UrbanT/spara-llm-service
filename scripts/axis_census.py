#!/usr/bin/env python3
"""Census every judge axis for degeneracy — plan-eil-v25 Step 3.

Why this exists
---------------
plan-eil-v24 replaced `question_coverage` because it was a *constant*: 10 on 855 of 855
attempts, human-agreement κ = 0.0. Its P4 check then verified that the replacement
(`entity_consistency`) actually varies — but only that one axis was ever checked. The
other three have never been censused, and v24 §R.3 recorded `faithfulness` failing two
answers that pass all six gold checks, which is the same disease from the other side
(over-strict rather than blind).

This reads the frozen traces and reports, per axis, whether it carries information:
value histogram, null rate, variance, ceiling and sub-floor rates, and a degeneracy flag
at ≥ `--degenerate-pct` of scored values sitting on one point.

Read-only. No model calls, no writes outside `--out-json` / `--out-md`.

Usage (inside the container):
    python scripts/axis_census.py \
        --run-dir artifacts/runs/2026-08-15_v24_r1 \
        --run-dir artifacts/runs/2026-08-15_v24_r2 \
        --run-dir artifacts/runs/2026-08-15_v24_r3 \
        --out-json artifacts/runs/2026-08-16_v25_axis_census.json \
        --out-md   artifacts/runs/2026-08-16_v25_axis_census.md
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

AXIS_FLOOR = 4      # TAU_AXIS_FLOOR — below this the axis "fired"
CEILING = 10

# Pools the census reports separately, because they answer different questions:
#   late_attempt  — what the live loop actually judged and acted on (446 in v24)
#   open_final    — the measurement judge on the no-loop control (264 in v24), the only
#                   pool where the judge scores answers no checkpoint ever revised.
LATE = "late_attempt"
OPEN = "open_final"


def _iter_jsonl(path: Path):
    """Yield each JSON record from a JSONL file; yields nothing if it is absent."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def collect(run_dirs: list[Path]) -> list[dict]:
    """One record per judged axis-set: {pool, run, arm, case_id, axes}."""
    out = []
    for rd in run_dirs:
        for arm_dir in sorted(p for p in rd.iterdir() if p.is_dir()):
            arm, traces = arm_dir.name, arm_dir / "traces"
            for row in _iter_jsonl(traces / "per_attempt.jsonl"):
                if row.get("checkpoint_fired") != "late":
                    continue
                axes = ((row.get("evaluator_verdict_json") or {}).get("axes")) or {}
                out.append({"pool": LATE, "run": rd.name, "arm": arm,
                            "case_id": row.get("case_id"),
                            "attempt_index": row.get("attempt_index"), "axes": axes})
            if arm == "A_open":
                for row in _iter_jsonl(traces / "per_case.jsonl"):
                    axes = ((row.get("answer_quality_verdict") or {}).get("axes")) or {}
                    out.append({"pool": OPEN, "run": rd.name, "arm": arm,
                                "case_id": row.get("case_id"), "axes": axes})
    return out


def _axis_names(records: list[dict]) -> list[str]:
    """Axis names discovered from the traces themselves, in first-seen order.

    Read from the data rather than hardcoded so a rubric change shows up as a new axis
    instead of being silently dropped from the census.
    """
    seen: list[str] = []
    for r in records:
        for k in r["axes"]:
            if k not in seen and not k.startswith("_"):
                seen.append(k)
    return seen


def summarise(records: list[dict], axis: str, degenerate_pct: float) -> dict:
    """Stats for one axis over one pool. Present-but-null is counted, absent is not."""
    present = [r for r in records if axis in r["axes"]]
    scored = [r["axes"][axis] for r in present if r["axes"][axis] is not None]
    n_null = len(present) - len(scored)
    hist = Counter(scored)
    n = len(scored)
    mean = sum(scored) / n if n else 0.0
    var = sum((v - mean) ** 2 for v in scored) / n if n else 0.0
    modal_value, modal_n = (hist.most_common(1)[0] if hist else (None, 0))
    pct_modal = 100.0 * modal_n / n if n else 0.0

    per_group: dict[str, dict] = {}
    for r in present:
        g = f"{r['run']}/{r['arm']}"
        b = per_group.setdefault(g, {"n_scored": 0, "n_null": 0, "n_sub_floor": 0})
        v = r["axes"][axis]
        if v is None:
            b["n_null"] += 1
        else:
            b["n_scored"] += 1
            b["n_sub_floor"] += int(v < AXIS_FLOOR)

    return {
        "n_present": len(present),
        "n_scored": n,
        "n_null": n_null,
        "pct_null": (100.0 * n_null / len(present)) if present else 0.0,
        "histogram": {str(k): hist[k] for k in sorted(hist)},
        "mean": mean,
        "variance": var,
        "n_at_ceiling": hist.get(CEILING, 0),
        "pct_at_ceiling": 100.0 * hist.get(CEILING, 0) / n if n else 0.0,
        "n_sub_floor": sum(c for v, c in hist.items() if v < AXIS_FLOOR),
        "pct_sub_floor": 100.0 * sum(c for v, c in hist.items() if v < AXIS_FLOOR) / n if n else 0.0,
        "modal_value": modal_value,
        "pct_modal": pct_modal,
        # The `question_coverage` disease: one value carrying (almost) the whole mass.
        "degenerate": bool(n and pct_modal >= degenerate_pct),
        "per_group": per_group,
    }


def build(records: list[dict], degenerate_pct: float) -> dict:
    """Score-distribution census per axis, flagging any axis that is near-constant.

    A degenerate axis carries no information — this is what retired `question_coverage`.
    """
    axes = _axis_names(records)
    pools = {}
    for pool in (LATE, OPEN):
        subset = [r for r in records if r["pool"] == pool]
        pools[pool] = {
            "n_judgements": len(subset),
            "axes": {ax: summarise(subset, ax, degenerate_pct) for ax in axes},
        }
    pools["all"] = {
        "n_judgements": len(records),
        "axes": {ax: summarise(records, ax, degenerate_pct) for ax in axes},
    }
    return {"axis_floor": AXIS_FLOOR, "degenerate_pct_threshold": degenerate_pct,
            "axes": axes, "pools": pools}


def to_md(census: dict) -> str:
    """Render the census as a markdown report."""
    L = ["# Axis census — plan-eil-v25 Step 3", "",
         f"Degeneracy flag: ≥ {census['degenerate_pct_threshold']:.0f} % of *scored* values "
         f"on one point. Sub-floor = score < {census['axis_floor']} (the axis fired).", ""]
    titles = {LATE: "Live late-checkpoint judgements (loop arms)",
              OPEN: "Measurement judge on the control arm (`A_open`)",
              "all": "All judgements pooled"}
    for pool, title in titles.items():
        p = census["pools"][pool]
        L += [f"## {title} — n = {p['n_judgements']}", "",
              "| axis | scored | null | mean | var | % ceiling | % sub-floor | modal (share) | degenerate |",
              "|---|---|---|---|---|---|---|---|---|"]
        for ax, s in p["axes"].items():
            L.append(
                f"| `{ax}` | {s['n_scored']} | {s['n_null']} | {s['mean']:.2f} | "
                f"{s['variance']:.2f} | {s['pct_at_ceiling']:.1f} % | "
                f"{s['pct_sub_floor']:.1f} % | {s['modal_value']} ({s['pct_modal']:.1f} %) | "
                f"{'**YES**' if s['degenerate'] else 'no'} |")
        L.append("")
    L += ["## Histograms (all judgements pooled)", ""]
    for ax, s in census["pools"]["all"]["axes"].items():
        hist = ", ".join(f"{k}×{v}" for k, v in s["histogram"].items()) or "(none scored)"
        L.append(f"- `{ax}`: {hist} — null ×{s['n_null']}")
    return "\n".join(L) + "\n"


def main(argv=None):
    """CLI entry point; --run-dir repeats to pool several runs."""
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", action="append", required=True, type=Path,
                   help="Repeatable. A study run dir containing per-arm subdirectories.")
    p.add_argument("--out-json", required=True, type=Path)
    p.add_argument("--out-md", required=True, type=Path)
    p.add_argument("--degenerate-pct", type=float, default=98.0)
    args = p.parse_args(argv)

    records = collect(args.run_dir)
    if not records:
        raise SystemExit("no judged records found — check --run-dir paths")
    census = build(records, args.degenerate_pct)

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(census, indent=2), encoding="utf-8")
    args.out_md.write_text(to_md(census), encoding="utf-8")
    print(to_md(census))
    print(f"Wrote {args.out_json} and {args.out_md}")


if __name__ == "__main__":
    main()
