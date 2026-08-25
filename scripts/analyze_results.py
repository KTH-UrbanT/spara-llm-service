#!/usr/bin/env python3
"""End-to-end analysis script for the SPARA EIL thesis study (v2).

Inputs (under ``--run-dir``):

  - ``<arm>/results.jsonl``                 — per-(question, arm) summary rows
  - ``<arm>/traces/evaluation_traces.jsonl`` — per-attempt evaluator records
  - ``human_labels.csv``                    — primary labels
  - ``intra_annotator_relabels.csv``        — optional relabels (cooling-off pass)
  - ``run_record.yaml`` or ``protocol.yaml`` — for deployment names (cost calc)

  Plus, separately:
  - ``src/config/questions.json`` (or ``--questions``) — to recover ``category``
    and the gold-answer fields per ``question_id``.

Outputs (in ``<run-dir>/analysis/``):

  - ``summary_table.csv``
  - ``pairwise_deltas.csv``
  - ``per_axis_clean.csv``
  - ``failure_taxonomy.csv``
  - ``latency_table.csv``
  - ``category_breakdown.csv``
  - ``intra_annotator_reliability.csv``  (if the relabels CSV is present)
  - ``figures/quality_by_arm.png``
  - ``figures/per_axis_by_arm.png``
  - ``figures/latency_quality_scatter.png``
  - ``figures/retry_rescue.png``

CLI::

    python -m scripts.analyze_results \\
        --run-dir artifacts/runs/2026-05-15_v2 \\
        --bootstrap-iters 10000 \\
        --bootstrap-seed 42

Cross-reference: plan-eil-v2.md §C.4 specifies the schema.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Pricing constants. Document the date and source on every change.
# ---------------------------------------------------------------------------
# Azure OpenAI pricing as of 2026-05-08, per 1K tokens, USD.
# Update when the deployment changes or pricing shifts.
PRICING_PER_1K_TOKENS: Dict[str, Dict[str, float]] = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.000150, "output": 0.000600},
    "gpt-4-turbo": {"input": 0.01, "output": 0.03},
    # Default fallback if the deployment name doesn't match — log a warning.
    "_default": {"input": 0.001, "output": 0.003},
}

HUMAN_AXES = (
    "groundedness",
    "completeness",
    "numeric_fidelity",
    "constraint_satisfaction",
)

ARMS = ("A1", "A2", "A3", "A4")


# ---------------------------------------------------------------------------
# Statistical primitives (testable in isolation — no I/O).
# ---------------------------------------------------------------------------
def bootstrap_paired_mean_diff(
    x: Sequence[float],
    y: Sequence[float],
    n_iters: int = 10_000,
    seed: int = 42,
    ci: float = 0.95,
) -> Tuple[float, float, float]:
    """Percentile-bootstrap CI on the paired mean difference ``mean(y - x)``.

    Returns ``(mean_diff, ci_low, ci_high)``. ``x`` and ``y`` must be the
    same length and aligned (same i-th item refers to the same paired
    observation).

    Note: this is a percentile interval. The plan §C.4 specifies BCa; for
    N=20 on bounded ordinal data BCa-vs-percentile differences are negligible
    and percentile is easier to audit. State this explicitly in Methods.
    """
    import numpy as np
    arr_x = np.asarray(x, dtype=float)
    arr_y = np.asarray(y, dtype=float)
    if arr_x.shape != arr_y.shape:
        raise ValueError(f"x and y must have same shape, got {arr_x.shape} vs {arr_y.shape}")
    if arr_x.size == 0:
        return float("nan"), float("nan"), float("nan")

    diffs = arr_y - arr_x
    rng = np.random.default_rng(seed)
    n = diffs.size
    means = np.empty(n_iters)
    for i in range(n_iters):
        idx = rng.integers(0, n, size=n)
        means[i] = diffs[idx].mean()

    alpha = (1.0 - ci) / 2.0
    return float(diffs.mean()), float(np.quantile(means, alpha)), float(np.quantile(means, 1 - alpha))


def wilcoxon_signed_rank_paired(x: Sequence[float], y: Sequence[float]) -> float:
    """Two-sided Wilcoxon signed-rank p-value on paired ordinal scores.

    ``mode='auto'`` is essential: ordinal 0-4 axes will produce ties, and
    ``mode='exact'`` is invalid in that case.
    """
    import numpy as np
    from scipy import stats
    arr_x = np.asarray(x, dtype=float)
    arr_y = np.asarray(y, dtype=float)
    if arr_x.size == 0 or np.allclose(arr_x, arr_y):
        return 1.0
    try:
        result = stats.wilcoxon(arr_x, arr_y, zero_method="wilcox", mode="auto")
    except TypeError:
        # Older SciPy (< 1.9) doesn't accept mode= and uses approximation.
        result = stats.wilcoxon(arr_x, arr_y, zero_method="wilcox")
    pvalue = float(result.pvalue)
    return pvalue if pvalue == pvalue else 1.0  # NaN guard


def mcnemar_paired_binary(x: Sequence[int], y: Sequence[int]) -> float:
    """Continuity-corrected McNemar's test on paired binary outcomes.

    Returns the p-value. ``x`` and ``y`` must be 0/1 arrays of the same
    length, aligned by paired observation.
    """
    import numpy as np
    from statsmodels.stats.contingency_tables import mcnemar
    arr_x = np.asarray(x, dtype=int)
    arr_y = np.asarray(y, dtype=int)
    if arr_x.shape != arr_y.shape:
        raise ValueError("x and y must have same shape")
    if arr_x.size == 0:
        return 1.0
    # 2x2 table indexed [x_value][y_value].
    table = [[0, 0], [0, 0]]
    for a, b in zip(arr_x, arr_y):
        table[int(a)][int(b)] += 1
    # Discordant cells are (0,1) and (1,0); use exact when very small.
    n_discordant = table[0][1] + table[1][0]
    use_exact = n_discordant < 25
    res = mcnemar(table, exact=use_exact, correction=True)
    return float(res.pvalue)


def cohens_kappa(rater1: Sequence[Any],
                 rater2: Sequence[Any],
                 weights: Optional[str] = None) -> float:
    """Wraps ``sklearn.metrics.cohen_kappa_score``.

    ``weights='linear'`` for ordinal axes; ``None`` for binary outcomes.
    """
    from sklearn.metrics import cohen_kappa_score
    if not rater1 or not rater2:
        return float("nan")
    return float(cohen_kappa_score(list(rater1), list(rater2), weights=weights))


# ---------------------------------------------------------------------------
# Data loading.
# ---------------------------------------------------------------------------
def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    """Yield each JSON record from a JSONL file; yields nothing if it is absent."""
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def discover_arms(run_dir: Path) -> List[str]:
    """Which arms actually produced results in this run directory."""
    return [a for a in ARMS if (run_dir / a / "results.jsonl").exists()]


def load_results(run_dir: Path, arms: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Per-arm result rows, keyed by arm."""
    return {a: list(_iter_jsonl(run_dir / a / "results.jsonl")) for a in arms}


def load_traces(run_dir: Path, arms: Sequence[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Per-arm evaluation traces, keyed by arm."""
    return {
        a: list(_iter_jsonl(run_dir / a / "traces" / "evaluation_traces.jsonl"))
        for a in arms
    }


def load_questions(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load the gold question set, keyed by question_id; {} if absent."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return {q["question_id"]: q for q in data.get("questions", [])
            if isinstance(q.get("question_id"), str)}


def load_labels(path: Path):
    """Load human_labels.csv as a pandas DataFrame.

    Returns an empty DataFrame if the file is missing — callers decide
    whether that's fatal. The labels-required path errors loudly.
    """
    import pandas as pd
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def load_deployments(run_dir: Path) -> Dict[str, Optional[str]]:
    """Recover Azure deployment names from ``run_record.yaml`` (preferred)
    or fall back to current-process env vars.

    Returns ``{"summarizer": <name|None>, "evaluator": <name|None>}``.
    """
    import os
    out = {"summarizer": None, "evaluator": None}
    record = run_dir / "run_record.yaml"
    if record.exists():
        for raw in record.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if stripped.startswith("summarizer_deployment:"):
                out["summarizer"] = _yaml_strip(stripped.split(":", 1)[1])
            elif stripped.startswith("evaluator_deployment:"):
                out["evaluator"] = _yaml_strip(stripped.split(":", 1)[1])
    if not out["summarizer"]:
        out["summarizer"] = os.environ.get("OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME")
    if not out["evaluator"]:
        out["evaluator"] = os.environ.get(
            "EVALUATOR_MODEL_DEPLOYMENT_NAME",
            os.environ.get("OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME"),
        )
    return out


def _yaml_strip(value: str) -> Optional[str]:
    """Normalise a YAML scalar, mapping the null spellings to None."""
    value = value.strip()
    if value in ("null", "~", ""):
        return None
    if value and value[0] in ("'", '"') and value[-1] == value[0]:
        return value[1:-1]
    return value


# ---------------------------------------------------------------------------
# Master DataFrame join.
# ---------------------------------------------------------------------------
def _output_id(qid: str, arm: str) -> str:
    """Short stable id for one (question, arm) output — the blinding key for labelling."""
    return hashlib.sha1(f"{qid}|{arm}".encode("utf-8")).hexdigest()[:8]


def build_master_dataframe(
    results: Dict[str, List[Dict[str, Any]]],
    traces: Dict[str, List[Dict[str, Any]]],
    labels,
    questions: Dict[str, Dict[str, Any]],
):
    """Assemble one row per ``(question_id, arm)`` joining all sources.

    Skip rows from ``results.jsonl`` that contain an ``error`` field —
    crashed graph invocations have no answer.
    """
    import pandas as pd

    base_rows: List[Dict[str, Any]] = []
    for arm, rows in results.items():
        for r in rows:
            if "error" in r:
                continue
            qid = r.get("question_id")
            if not isinstance(qid, str):
                continue
            qrec = questions.get(qid, {})
            base_rows.append({
                "output_id": _output_id(qid, arm),
                "question_id": qid,
                "arm": arm,
                "category": qrec.get("category"),
                "difficulty": qrec.get("difficulty"),
                "wall_clock_ms": r.get("wall_clock_ms"),
                "final_response": r.get("final_response"),
                "eval_verdict": r.get("eval_verdict"),
                "eval_retry_count": r.get("eval_retry_count", 0),
                "summarizer_latency_ms": r.get("summarizer_latency_ms"),
                "summarizer_token_usage": r.get("summarizer_token_usage"),
                "evaluator_latency_ms": r.get("evaluator_latency_ms"),
                "evaluator_token_usage": r.get("evaluator_token_usage"),
            })
    df = pd.DataFrame(base_rows)
    if df.empty:
        return df

    # Trace-derived columns: first-attempt verdict, score_fallbacks, evaluation_status.
    first_verdicts: Dict[Tuple[str, str], Optional[str]] = {}
    fallbacks: Dict[Tuple[str, str], List[str]] = {}
    statuses: Dict[Tuple[str, str], str] = {}
    for arm, recs in traces.items():
        for rec in recs:
            qid = rec.get("question_id")
            if not isinstance(qid, str):
                continue
            key = (qid, arm)
            attempt = rec.get("attempt_index", 0)
            if attempt == 0:
                first_verdicts[key] = rec.get("verdict")
            # Final attempt rules: keep updating until the highest attempt seen.
            statuses[key] = rec.get("evaluation_status", "evaluated")
            sf = rec.get("score_fallbacks_applied") or []
            if attempt == 0 or key not in fallbacks:
                fallbacks[key] = list(sf)

    df["first_attempt_verdict"] = df.apply(
        lambda r: first_verdicts.get((r["question_id"], r["arm"])), axis=1,
    )
    df["evaluation_status"] = df.apply(
        lambda r: statuses.get((r["question_id"], r["arm"]), "evaluated"), axis=1,
    )
    df["score_fallbacks_applied"] = df.apply(
        lambda r: fallbacks.get((r["question_id"], r["arm"]), []), axis=1,
    )

    # Human labels.
    if labels is not None and not labels.empty:
        df = df.merge(labels, on=["output_id", "question_id", "arm"], how="left")
    else:
        # Insert empty label columns for downstream code paths.
        for col in HUMAN_AXES + ("overall_pass", "failure_tags", "notes",
                                 "annotator_id", "timestamp_utc"):
            df[col] = None

    # Composite human quality (mean of 4 ordinal axes). NaN if any axis missing.
    def _composite(row):
        """Mean of the human axes for one row; NaN when none were scored."""
        vals = [row.get(a) for a in HUMAN_AXES]
        try:
            nums = [float(v) for v in vals if v is not None and v == v]
        except (TypeError, ValueError):
            return float("nan")
        if len(nums) != 4:
            return float("nan")
        return sum(nums) / 4.0

    df["composite_human_quality"] = df.apply(_composite, axis=1)
    return df


# ---------------------------------------------------------------------------
# Per-table writers.
# ---------------------------------------------------------------------------
def _pricing_for(deployment: Optional[str]) -> Dict[str, float]:
    """Per-1k-token prices for a deployment, falling back to the default table."""
    if deployment and deployment in PRICING_PER_1K_TOKENS:
        return PRICING_PER_1K_TOKENS[deployment]
    if deployment:
        warnings.warn(
            f"Deployment {deployment!r} not in PRICING_PER_1K_TOKENS; "
            f"using _default — cost estimate is approximate.",
            stacklevel=2,
        )
    return PRICING_PER_1K_TOKENS["_default"]


def _sum_token_usage(series, key: str) -> int:
    """Total one token-usage field across a column of usage dicts, skipping nulls."""
    total = 0
    for usage in series.dropna():
        if isinstance(usage, dict):
            v = usage.get(key) or usage.get(f"{key}_tokens") or 0
            try:
                total += int(v)
            except (TypeError, ValueError):
                continue
    return total


def write_summary_table(df, deployments: Dict[str, Optional[str]], out: Path) -> None:
    """Write the headline per-arm table: quality, pass rate, latency and cost."""
    import pandas as pd
    from src.evaluation import analysis_filters as af

    def _per_arm(group_df) -> Dict[str, Any]:
        """Summary statistics for one arm's slice of the master dataframe."""
        n = len(group_df)
        composite = group_df["composite_human_quality"].dropna().tolist()
        mean_q = sum(composite) / len(composite) if composite else float("nan")
        ci_low, ci_high = _single_arm_ci(composite)
        # pass_rate uses the analysis filter helpers.
        records = group_df.to_dict("records")
        # Convert each row into a "trace-like" view for the helper.
        trace_view = [{"evaluation_status": r.get("evaluation_status", "evaluated"),
                       "verdict": "pass" if r.get("overall_pass") in (1, "1", 1.0) else "fail"}
                      for r in records]
        evaluated = [v["verdict"] for v in trace_view if af.is_genuine_evaluation(v)]
        pass_rate_ev = (sum(1 for v in evaluated if v == "pass") / len(evaluated)
                        if evaluated else float("nan"))
        pass_rate_unfiltered = (sum(1 for v in trace_view if v["verdict"] == "pass")
                                / len(trace_view) if trace_view else float("nan"))
        fail_open_rate = (sum(1 for v in trace_view if af.is_fail_open(v))
                          / len(trace_view) if trace_view else 0.0)

        # Retry metrics (per plan §B.6). Meaningful for arms that ran the
        # evaluator with max_retries > 0; A1 will be 0 / NaN by construction.
        retry_counts = group_df["eval_retry_count"].fillna(0).astype(int)
        retried_mask = retry_counts > 0
        n_retried = int(retried_mask.sum())
        retry_attempt_rate = (n_retried / n) if n else float("nan")
        if n_retried:
            retried_pass_mask = retried_mask & (group_df["eval_verdict"] == "pass")
            retry_success_rate_among_attempted = float(retried_pass_mask.sum()) / n_retried
            retry_rescue_rate = float(retried_pass_mask.sum()) / n if n else float("nan")
        else:
            retry_success_rate_among_attempted = float("nan")
            retry_rescue_rate = 0.0 if n else float("nan")

        wall = group_df["wall_clock_ms"].dropna().astype(float)
        sum_inp = _sum_token_usage(group_df["summarizer_token_usage"], "input")
        sum_out = _sum_token_usage(group_df["summarizer_token_usage"], "output")
        eval_inp = _sum_token_usage(group_df["evaluator_token_usage"], "input")
        eval_out = _sum_token_usage(group_df["evaluator_token_usage"], "output")
        sum_pricing = _pricing_for(deployments.get("summarizer"))
        eval_pricing = _pricing_for(deployments.get("evaluator"))
        usd = (sum_inp / 1000.0 * sum_pricing["input"]
               + sum_out / 1000.0 * sum_pricing["output"]
               + eval_inp / 1000.0 * eval_pricing["input"]
               + eval_out / 1000.0 * eval_pricing["output"])

        out_row = {
            "arm": None,  # set by caller
            "n": n,
            "pass_rate_evaluated": pass_rate_ev,
            "pass_rate_unfiltered": pass_rate_unfiltered,
            "fail_open_rate": fail_open_rate,
            "mean_composite_quality": mean_q,
            "composite_ci_low": ci_low,
            "composite_ci_high": ci_high,
            "median_latency_ms": float(wall.median()) if not wall.empty else float("nan"),
            "p95_latency_ms": float(wall.quantile(0.95)) if not wall.empty else float("nan"),
            "retry_attempt_rate": retry_attempt_rate,
            "retry_success_rate_among_attempted": retry_success_rate_among_attempted,
            "retry_rescue_rate": retry_rescue_rate,
            "total_tokens": sum_inp + sum_out + eval_inp + eval_out,
            "estimated_usd": usd,
        }
        for axis in HUMAN_AXES:
            vals = group_df[axis].dropna().astype(float)
            out_row[f"mean_{axis}"] = float(vals.mean()) if not vals.empty else float("nan")
        return out_row

    rows = []
    for arm, group in df.groupby("arm"):
        row = _per_arm(group)
        row["arm"] = arm
        rows.append(row)
    summary = pd.DataFrame(rows)
    if not summary.empty:
        cols = ["arm"] + [c for c in summary.columns if c != "arm"]
        summary = summary[cols].sort_values("arm").reset_index(drop=True)
    summary.to_csv(out, index=False)


def _single_arm_ci(values: Sequence[float], n_iters: int = 2000,
                   seed: int = 42, ci: float = 0.95) -> Tuple[float, float]:
    """Percentile bootstrap CI on a single arm's mean."""
    import numpy as np
    if not values:
        return float("nan"), float("nan")
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = np.empty(n_iters)
    for i in range(n_iters):
        idx = rng.integers(0, arr.size, size=arr.size)
        means[i] = arr[idx].mean()
    alpha = (1.0 - ci) / 2.0
    return float(np.quantile(means, alpha)), float(np.quantile(means, 1 - alpha))


def write_pairwise_deltas(df, baseline: str, out: Path,
                          n_iters: int, seed: int) -> None:
    """Write each arm's quality delta against the baseline with bootstrap CIs.

    Bootstrapped rather than a t-test: the composite quality score is bounded and
    skewed, so a normal approximation would misstate the interval at this sample size.
    """
    import pandas as pd
    arms = sorted(df["arm"].unique())
    if baseline not in arms:
        pd.DataFrame(columns=[
            "baseline", "arm", "n_pairs", "delta_mean",
            "ci_low", "ci_high", "wilcoxon_p", "mcnemar_p",
        ]).to_csv(out, index=False)
        return
    rows = []
    base = df[df["arm"] == baseline]
    for arm in arms:
        if arm == baseline:
            continue
        other = df[df["arm"] == arm]
        merged = base.merge(other, on="question_id", suffixes=("_base", "_other"))
        merged = merged.dropna(subset=["composite_human_quality_base",
                                       "composite_human_quality_other"])
        if merged.empty:
            continue
        x = merged["composite_human_quality_base"].astype(float).tolist()
        y = merged["composite_human_quality_other"].astype(float).tolist()
        mean_diff, ci_low, ci_high = bootstrap_paired_mean_diff(
            x, y, n_iters=n_iters, seed=seed,
        )
        wp = wilcoxon_signed_rank_paired(x, y)
        # McNemar requires binary; only run if overall_pass present in both.
        mcp = float("nan")
        merged_bin = merged.dropna(subset=["overall_pass_base", "overall_pass_other"])
        if not merged_bin.empty:
            mcp = mcnemar_paired_binary(
                merged_bin["overall_pass_base"].astype(int).tolist(),
                merged_bin["overall_pass_other"].astype(int).tolist(),
            )
        rows.append({
            "baseline": baseline,
            "arm": arm,
            "n_pairs": len(merged),
            "delta_mean": mean_diff,
            "ci_low": ci_low,
            "ci_high": ci_high,
            "wilcoxon_p": wp,
            "mcnemar_p": mcp,
        })
    pd.DataFrame(rows, columns=[
        "baseline", "arm", "n_pairs", "delta_mean",
        "ci_low", "ci_high", "wilcoxon_p", "mcnemar_p",
    ]).to_csv(out, index=False)


def write_per_axis_clean(df, out: Path) -> None:
    """Per-axis means restricted to rows where ``score_fallbacks_applied`` is empty."""
    import pandas as pd
    rows = []
    clean_df = df[df["score_fallbacks_applied"].apply(lambda v: not v if isinstance(v, list) else True)]
    for arm, group in clean_df.groupby("arm"):
        for axis in HUMAN_AXES:
            vals = group[axis].dropna().astype(float)
            if vals.empty:
                continue
            ci_low, ci_high = _single_arm_ci(vals.tolist())
            rows.append({
                "arm": arm, "axis": axis, "n_clean": len(vals),
                "mean": float(vals.mean()),
                "ci_low": ci_low, "ci_high": ci_high,
            })
    pd.DataFrame(rows).to_csv(out, index=False)


def write_failure_taxonomy(df, out: Path) -> None:
    """Write per-arm counts of each failure mode."""
    import pandas as pd
    rows = []
    for arm, group in df.groupby("arm"):
        n = len(group)
        tag_counts: Dict[str, int] = {}
        for tags in group["failure_tags"].dropna():
            if not isinstance(tags, str) or not tags.strip():
                continue
            for t in tags.split(","):
                t = t.strip()
                if t:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
        for tag, count in sorted(tag_counts.items()):
            rows.append({
                "arm": arm, "failure_tag": tag,
                "count": count, "rate": count / n if n else float("nan"),
            })
    pd.DataFrame(rows, columns=["arm", "failure_tag", "count", "rate"]).to_csv(out, index=False)


def write_latency_table(df, out: Path) -> None:
    """Write per-arm latency percentiles."""
    import pandas as pd
    rows = []
    for arm, group in df.groupby("arm"):
        wall = group["wall_clock_ms"].dropna().astype(float)
        sum_lat = group["summarizer_latency_ms"].dropna().astype(float)
        eval_lat = group["evaluator_latency_ms"].dropna().astype(float)
        retried = group[group["eval_retry_count"].fillna(0).astype(int) > 0]
        not_retried = group[group["eval_retry_count"].fillna(0).astype(int) == 0]
        retry_overhead = float("nan")
        if not retried.empty and not not_retried.empty:
            r_med = retried["wall_clock_ms"].median()
            n_med = not_retried["wall_clock_ms"].median()
            if n_med:
                retry_overhead = float(r_med / n_med)
        rows.append({
            "arm": arm,
            "mean_ms": float(wall.mean()) if not wall.empty else float("nan"),
            "median_ms": float(wall.median()) if not wall.empty else float("nan"),
            "p95_ms": float(wall.quantile(0.95)) if not wall.empty else float("nan"),
            "summarizer_median_ms": float(sum_lat.median()) if not sum_lat.empty else float("nan"),
            "evaluator_median_ms": float(eval_lat.median()) if not eval_lat.empty else float("nan"),
            "retry_count": int(len(retried)),
            "retry_overhead_ratio": retry_overhead,
        })
    pd.DataFrame(rows).to_csv(out, index=False)


def write_category_breakdown(df, out: Path) -> None:
    """Write per-arm quality and pass rate split by question category."""
    import pandas as pd
    rows = []
    for (arm, cat), group in df.groupby(["arm", "category"], dropna=False):
        vals = group["composite_human_quality"].dropna()
        rows.append({
            "arm": arm, "category": cat,
            "mean_composite": float(vals.mean()) if not vals.empty else float("nan"),
            "n": len(group),
        })
    pd.DataFrame(rows).to_csv(out, index=False)


def write_intra_annotator_reliability(run_dir: Path, out: Path) -> None:
    """Compare ``human_labels.csv`` against ``intra_annotator_relabels.csv``.

    Per-axis weighted Cohen's kappa (linear) on the four ordinal axes,
    plus binary Cohen's kappa on ``overall_pass``.
    """
    import pandas as pd
    labels = pd.read_csv(run_dir / "human_labels.csv")
    relabels = pd.read_csv(run_dir / "intra_annotator_relabels.csv")
    merged = labels.merge(relabels, on="output_id", suffixes=("_orig", "_re"))
    rows = []
    for axis in HUMAN_AXES:
        a = merged[f"{axis}_orig"].dropna().astype(int).tolist()
        b = merged[f"{axis}_re"].dropna().astype(int).tolist()
        # Truncate to common length (defensive).
        m = min(len(a), len(b))
        rows.append({
            "metric": f"{axis}_kappa_linear",
            "value": cohens_kappa(a[:m], b[:m], weights="linear"),
            "n": m,
        })
    a = merged["overall_pass_orig"].dropna().astype(int).tolist()
    b = merged["overall_pass_re"].dropna().astype(int).tolist()
    m = min(len(a), len(b))
    rows.append({
        "metric": "overall_pass_kappa_binary",
        "value": cohens_kappa(a[:m], b[:m], weights=None),
        "n": m,
    })
    pd.DataFrame(rows).to_csv(out, index=False)


# ---------------------------------------------------------------------------
# Plotting (matplotlib Agg backend; tests skip these paths).
# ---------------------------------------------------------------------------
def _setup_matplotlib():
    """Import pyplot with the headless Agg backend — the container has no display."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def plot_quality_by_arm(df, out: Path) -> None:
    """Bar chart of mean composite quality per arm, with CI whiskers."""
    plt = _setup_matplotlib()
    grouped = df.groupby("arm")["composite_human_quality"]
    means, lows, highs, arms = [], [], [], []
    for arm, vals in grouped:
        v = vals.dropna().tolist()
        if not v:
            continue
        ci_low, ci_high = _single_arm_ci(v)
        means.append(sum(v) / len(v))
        lows.append(ci_low)
        highs.append(ci_high)
        arms.append(arm)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(arms, means, yerr=[
        [m - l for m, l in zip(means, lows)],
        [h - m for m, h in zip(means, highs)],
    ], capsize=8)
    ax.set_ylabel("Mean composite human quality")
    ax.set_title("Composite quality by arm (95% bootstrap CI)")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_per_axis_by_arm(df, out: Path) -> None:
    """Grouped bar chart of mean score per human axis, per arm."""
    import numpy as np
    plt = _setup_matplotlib()
    arms = sorted(df["arm"].unique())
    width = 0.8 / max(len(arms), 1)
    fig, ax = plt.subplots(figsize=(8, 4))
    for i, arm in enumerate(arms):
        vals = [df[(df["arm"] == arm)][a].dropna().astype(float).mean()
                for a in HUMAN_AXES]
        x = np.arange(len(HUMAN_AXES)) + i * width
        ax.bar(x, vals, width=width, label=arm)
    ax.set_xticks(np.arange(len(HUMAN_AXES)) + width * (len(arms) - 1) / 2)
    ax.set_xticklabels(HUMAN_AXES, rotation=20, ha="right")
    ax.set_ylabel("Mean score")
    ax.set_title("Per-axis mean by arm")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_latency_quality_scatter(df, out: Path) -> None:
    """Scatter of latency against quality, one series per arm — the cost/benefit view."""
    plt = _setup_matplotlib()
    fig, ax = plt.subplots(figsize=(6, 4))
    for arm, group in df.groupby("arm"):
        ax.scatter(group["wall_clock_ms"], group["composite_human_quality"],
                   label=arm, alpha=0.7)
    ax.set_xlabel("Wall-clock (ms)")
    ax.set_ylabel("Composite human quality")
    ax.set_title("Latency vs quality, per output")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_retry_rescue(df, out: Path) -> None:
    """Stacked bar of retry outcomes for arms that ran retries."""
    plt = _setup_matplotlib()
    arms = sorted(df["arm"].unique())
    no_retry, rescued, rejected = [], [], []
    for arm in arms:
        group = df[df["arm"] == arm]
        retried_mask = group["eval_retry_count"].fillna(0).astype(int) > 0
        retried = group[retried_mask]
        no_retry.append(int((~retried_mask).sum()))
        # Rescue = first-attempt fail AND final verdict pass.
        if retried.empty:
            rescued.append(0)
            rejected.append(0)
            continue
        rescue_mask = (retried["first_attempt_verdict"] == "fail") & \
                      (retried["eval_verdict"] == "pass")
        rescued.append(int(rescue_mask.sum()))
        rejected.append(int((retried["eval_verdict"] != "pass").sum()))
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.bar(arms, no_retry, label="no retry")
    ax.bar(arms, rescued, bottom=no_retry, label="retry → pass")
    ax.bar(arms, rejected,
           bottom=[a + b for a, b in zip(no_retry, rescued)], label="retry → fail")
    ax.set_ylabel("Count")
    ax.set_title("Retry-rescue outcomes per arm")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI driver.
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """CLI: --run-dir in, analysis tables and plots written to <run-dir>/analysis."""
    p = argparse.ArgumentParser(description="Analyse a SPARA EIL run directory.")
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--questions", type=Path,
                   default=Path("src/config/questions.json"))
    p.add_argument("--bootstrap-iters", type=int, default=10_000)
    p.add_argument("--bootstrap-seed", type=int, default=42)
    p.add_argument("--no-figures", action="store_true",
                   help="Skip matplotlib output (tests, headless CI).")
    return p.parse_args(argv)


def count_error_rows(results: Dict[str, List[Dict[str, Any]]]) -> int:
    """Rows that recorded an error, across every arm."""
    return sum(1 for rows in results.values() for r in rows if "error" in r)


def main(argv: Optional[List[str]] = None) -> int:
    """Build every table and plot for a run directory; non-zero on failure."""
    args = parse_args(argv)
    run_dir = Path(args.run_dir)
    out = run_dir / "analysis"
    out.mkdir(exist_ok=True)
    figs = out / "figures"
    figs.mkdir(exist_ok=True)

    arms = discover_arms(run_dir)
    if not arms:
        print(f"ERROR: no <arm>/results.jsonl found under {run_dir}")
        return 1
    print(f"Arms discovered: {arms}")

    results = load_results(run_dir, arms)
    traces = load_traces(run_dir, arms)
    questions = load_questions(args.questions)
    labels_path = run_dir / "human_labels.csv"
    if not labels_path.exists():
        print(f"ERROR: {labels_path} missing — run "
              f"`python -m scripts.label_outputs --run-dir {run_dir} ...` first.")
        return 1
    labels = load_labels(labels_path)

    deployments = load_deployments(run_dir)

    df = build_master_dataframe(results, traces, labels, questions)
    if df.empty:
        print("ERROR: master DataFrame is empty (all rows skipped or missing).")
        return 1

    err = count_error_rows(results)
    if err:
        print(f"WARNING: {err} error row(s) excluded from analysis.")

    # Loud failure if any (qid, arm) lacks a human label.
    missing_labels = df[df["composite_human_quality"].isna()]
    if not missing_labels.empty:
        ids = missing_labels["output_id"].tolist()
        print(f"ERROR: {len(ids)} output(s) missing labels: {ids[:10]}"
              + ("..." if len(ids) > 10 else ""))
        return 1

    write_summary_table(df, deployments, out / "summary_table.csv")
    write_pairwise_deltas(df, baseline="A1", out=out / "pairwise_deltas.csv",
                          n_iters=args.bootstrap_iters, seed=args.bootstrap_seed)
    write_per_axis_clean(df, out / "per_axis_clean.csv")
    write_failure_taxonomy(df, out / "failure_taxonomy.csv")
    write_latency_table(df, out / "latency_table.csv")
    write_category_breakdown(df, out / "category_breakdown.csv")

    relabels_path = run_dir / "intra_annotator_relabels.csv"
    if relabels_path.exists():
        write_intra_annotator_reliability(run_dir, out / "intra_annotator_reliability.csv")

    if not args.no_figures:
        plot_quality_by_arm(df, figs / "quality_by_arm.png")
        plot_per_axis_by_arm(df, figs / "per_axis_by_arm.png")
        plot_latency_quality_scatter(df, figs / "latency_quality_scatter.png")
        plot_retry_rescue(df, figs / "retry_rescue.png")

    print(f"Wrote analysis tables and figures to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
