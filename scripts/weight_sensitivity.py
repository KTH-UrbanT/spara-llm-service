"""Composite-weight sensitivity analysis on the v4 human_labels.csv.

For each of three weight schemes, compute the per-row composite, then the per-arm
mean, then the A2-A1 delta. Report side-by-side. The headline question: does the
v4 Criterion 1 conclusion (Δ ≈ -0.025, CI excludes 0 → fail) change under
alternative weights?

Note: human_labels.csv exposes 4 axes (groundedness, completeness, numeric_fidelity,
constraint_satisfaction) on a 0-4 scale. uncertainty_calibration is in evaluator
scores only, not human labels. The 4-axis schemes below operate on human labels.

Original (5-axis production weights): 0.35 g / 0.25 nf / 0.20 cs / 0.15 c / 0.05 uc
4-axis renormalised (drop uc, rescale to sum=1):
  0.35/0.95 g / 0.25/0.95 nf / 0.20/0.95 cs / 0.15/0.95 c
  ≈ 0.368 g / 0.263 nf / 0.211 cs / 0.158 c

Uniform 4-axis: 0.25 each.

RAGAS-2axis (faithfulness ≈ groundedness; answer_relevance ≈ completeness):
  0.50 g / 0.50 c (other two zeroed)
"""
from __future__ import annotations
import argparse
import csv
from pathlib import Path

SCHEMES = {
    "original_renorm4": {"groundedness": 0.368, "numeric_fidelity": 0.263,
                          "constraint_satisfaction": 0.211, "completeness": 0.158},
    "uniform_4axis":    {"groundedness": 0.25,  "numeric_fidelity": 0.25,
                          "constraint_satisfaction": 0.25,  "completeness": 0.25},
    "ragas_2axis":      {"groundedness": 0.50,  "numeric_fidelity": 0.0,
                          "constraint_satisfaction": 0.0,   "completeness": 0.50},
}


def composite(row, weights):
    """Weighted composite score for one labelled row."""
    s = 0.0
    for axis, w in weights.items():
        try:
            s += w * float(row[axis])
        except (KeyError, ValueError, TypeError):
            return None
    return s


def main():
    """Recompute per-arm results under each weighting scheme.

    A conclusion that only holds under one arbitrary axis weighting is not a result;
    this is the sensitivity check for that.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True, type=Path,
                    help="Path to human_labels.csv")
    args = ap.parse_args()

    with args.labels.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"{'scheme':<22}  {'A1_mean':>8}  {'A2_mean':>8}  {'delta':>9}  {'n_A1':>5}  {'n_A2':>5}")
    print("-" * 72)
    for name, weights in SCHEMES.items():
        by_arm = {"A1": [], "A2": []}
        for r in rows:
            c = composite(r, weights)
            if c is not None and r["arm"] in by_arm:
                by_arm[r["arm"]].append(c)
        m1 = sum(by_arm["A1"]) / len(by_arm["A1"]) if by_arm["A1"] else float("nan")
        m2 = sum(by_arm["A2"]) / len(by_arm["A2"]) if by_arm["A2"] else float("nan")
        d = m2 - m1
        print(f"{name:<22}  {m1:>8.4f}  {m2:>8.4f}  {d:>+9.4f}  "
              f"{len(by_arm['A1']):>5}  {len(by_arm['A2']):>5}")


if __name__ == "__main__":
    main()
