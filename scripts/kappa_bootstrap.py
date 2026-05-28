"""Bootstrap CIs on the intra-annotator κ panel (n=8).

Reads the canonical pair of CSVs:
  artifacts/runs/<run>/human_labels.csv         — primary annotation
  artifacts/runs/<run>/intra_annotator_relabels.csv — relabel round 2

For each axis, joins on output_id, then resamples the n=8 paired rows
with replacement N times, computes weighted Cohen's κ per resample,
and reports the 2.5%/97.5% percentile CI alongside the point estimate.

NaN fraction (resamples where κ is undefined, typically when y_true has a
single label) is reported per axis so the reader can judge interpretability.

Usage (inside the spara-llm-service-dev container, where sklearn lives):
    python scripts/kappa_bootstrap.py \
        --run-dir artifacts/runs/2026-05-14_v2 \
        --n-resamples 10000 \
        --seed 42 \
        --output artifacts/runs/2026-05-14_v2/analysis/intra_annotator_reliability_bootstrap.csv
"""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
import numpy as np
from sklearn.metrics import cohen_kappa_score

AXES_LINEAR = ("groundedness", "completeness",
               "numeric_fidelity", "constraint_satisfaction")
AXIS_BINARY = "overall_pass"


def load_pairs(run_dir: Path):
    primary = {}
    with (run_dir / "human_labels.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            primary[row["output_id"]] = row
    relabel = {}
    with (run_dir / "intra_annotator_relabels.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            relabel[row["output_id"]] = row

    pairs = {axis: [] for axis in (*AXES_LINEAR, AXIS_BINARY)}
    for oid, r in relabel.items():
        p = primary.get(oid)
        if p is None:
            continue
        for axis in (*AXES_LINEAR, AXIS_BINARY):
            try:
                pa = int(p[axis])
                pb = int(r[axis])
                pairs[axis].append((pa, pb))
            except (KeyError, ValueError):
                continue
    return pairs


def kappa(pairs, weights):
    a, b = zip(*pairs)
    return float(cohen_kappa_score(a, b, weights=weights))


def bootstrap(pairs, weights, n_resamples, rng):
    n = len(pairs)
    out = np.empty(n_resamples)
    for i in range(n_resamples):
        idx = rng.integers(0, n, size=n)
        resample = [pairs[j] for j in idx]
        try:
            out[i] = kappa(resample, weights)
        except Exception:
            out[i] = np.nan
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--n-resamples", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    pairs = load_pairs(args.run_dir)
    rng = np.random.default_rng(args.seed)

    rows = []
    for axis in AXES_LINEAR:
        pts = pairs[axis]
        if not pts:
            continue
        point = kappa(pts, weights="linear")
        boot = bootstrap(pts, "linear", args.n_resamples, rng)
        nan_frac = float(np.isnan(boot).mean())
        lo, hi = np.nanpercentile(boot, [2.5, 97.5])
        rows.append({
            "metric": f"{axis}_kappa_linear",
            "point_estimate": round(point, 4),
            "ci_lo_2.5": round(lo, 4),
            "ci_hi_97.5": round(hi, 4),
            "nan_fraction": round(nan_frac, 4),
            "n_pairs": len(pts),
            "n_resamples": args.n_resamples,
        })

    pts = pairs[AXIS_BINARY]
    if pts:
        point = kappa(pts, weights=None)
        boot = bootstrap(pts, None, args.n_resamples, rng)
        nan_frac = float(np.isnan(boot).mean())
        lo, hi = np.nanpercentile(boot, [2.5, 97.5])
        rows.append({
            "metric": f"{AXIS_BINARY}_kappa_binary",
            "point_estimate": round(point, 4),
            "ci_lo_2.5": round(lo, 4),
            "ci_hi_97.5": round(hi, 4),
            "nan_fraction": round(nan_frac, 4),
            "n_pairs": len(pts),
            "n_resamples": args.n_resamples,
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"Wrote {args.output}")
    for r in rows:
        print(f"  {r['metric']:<42}  point={r['point_estimate']:>7.4f}  "
              f"CI=[{r['ci_lo_2.5']:>7.4f}, {r['ci_hi_97.5']:>7.4f}]  "
              f"NaN_frac={r['nan_fraction']:.3f}")


if __name__ == "__main__":
    main()
