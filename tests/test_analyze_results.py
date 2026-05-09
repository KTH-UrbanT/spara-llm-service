"""Tests for ``scripts/analyze_results.py``.

Per plan-eil-v2.md §C.4: ten tests covering the statistical primitives
in isolation plus end-to-end smoke runs over synthetic JSONL+CSV
fixtures. No Azure, no live DB.

The tests skip the four matplotlib figures via ``--no-figures`` so the
suite stays fast and CI-friendly.
"""
from __future__ import annotations

import csv
import json
import warnings
from pathlib import Path
from typing import Any

import pytest

# Skip the entire module if scipy/statsmodels/sklearn aren't installed.
pytest.importorskip("numpy")
pytest.importorskip("pandas")
pytest.importorskip("scipy")
pytest.importorskip("statsmodels")
pytest.importorskip("sklearn")

from scripts import analyze_results as ar


# ---------------------------------------------------------------------------
# Synthetic data builders.
# ---------------------------------------------------------------------------
def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _seed_run_dir(tmp_path: Path, *,
                  with_labels: bool = True,
                  inject_error: bool = False,
                  with_a3: bool = False,
                  unknown_deployment: bool = False) -> Path:
    """Create a 4-question x (A1, A2[, A3]) run dir with everything wired."""
    run_dir = tmp_path / "run"

    qids = ["Q001", "Q002", "Q003", "Q004"]
    arms = ["A1", "A2"] + (["A3"] if with_a3 else [])

    # Questions.json (separate file the script joins on).
    questions = {
        "dataset_version": "2.0",
        "questions": [
            {"question_id": q, "category": "simple_address" if i % 2 == 0
             else "numeric_aggregation",
             "difficulty": "medium",
             "question": f"Q{q}?",
             "expected_facts": [], "expected_constraints": [],
             "must_avoid": [], "notes": "",
             "created_at": "2026-05-15T00:00:00Z"}
            for i, q in enumerate(qids)
        ],
    }
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(json.dumps(questions))

    # Per-arm results + traces.
    for arm in arms:
        rows = []
        for i, qid in enumerate(qids):
            if inject_error and arm == "A2" and qid == "Q004":
                rows.append({"question_id": qid, "arm": arm, "error": "boom"})
                continue
            rows.append({
                "question_id": qid, "arm": arm, "run_id": "test",
                "wall_clock_ms": 1000 + i * 200 + (200 if arm != "A1" else 0),
                "final_response": f"answer for {qid} in {arm}",
                # Final verdict in results.jsonl is the POST-retry verdict.
                # For A2 Q001 (i=0) the retry rescued the answer → final="pass"
                # while the attempt-0 trace below shows verdict="fail".
                "eval_verdict": "pass",
                "eval_retry_count": 1 if arm == "A2" and i == 0 else 0,
                "summarizer_latency_ms": 800,
                "summarizer_token_usage": {"input": 200, "output": 80},
                "evaluator_latency_ms": 0 if arm == "A1" else 400,
                "evaluator_token_usage": None if arm == "A1" else {"input": 300, "output": 120},
            })
        _write_jsonl(run_dir / arm / "results.jsonl", rows)

        traces = []
        for i, qid in enumerate(qids):
            if inject_error and arm == "A2" and qid == "Q004":
                continue
            if arm == "A1":
                traces.append({
                    "question_id": qid, "arm": arm, "attempt_index": 0,
                    "evaluation_status": "bypassed",
                    "verdict": "", "score_fallbacks_applied": [],
                })
            else:
                # Q001 in A2 had a retry → 2 trace records.
                if arm == "A2" and i == 0:
                    traces.append({
                        "question_id": qid, "arm": arm, "attempt_index": 0,
                        "evaluation_status": "evaluated",
                        "verdict": "fail", "score_fallbacks_applied": [],
                    })
                    traces.append({
                        "question_id": qid, "arm": arm, "attempt_index": 1,
                        "evaluation_status": "evaluated",
                        "verdict": "pass", "score_fallbacks_applied": [],
                    })
                else:
                    # Mark Q003 with a fallback to test per_axis_clean.
                    fallbacks = ["numeric_fidelity"] if i == 2 else []
                    traces.append({
                        "question_id": qid, "arm": arm, "attempt_index": 0,
                        "evaluation_status": "evaluated",
                        "verdict": "pass",
                        "score_fallbacks_applied": fallbacks,
                    })
        _write_jsonl(run_dir / arm / "traces" / "evaluation_traces.jsonl", traces)

    # Human labels.
    if with_labels:
        labels_path = run_dir / "human_labels.csv"
        rows = []
        for arm in arms:
            for i, qid in enumerate(qids):
                if inject_error and arm == "A2" and qid == "Q004":
                    continue
                # Make A2 strictly better than A1 by +1 on each axis.
                base = 2 if arm == "A1" else 3
                rows.append({
                    "output_id": ar._output_id(qid, arm),
                    "annotator_id": "tester",
                    "timestamp_utc": "2026-05-15T00:00:00Z",
                    "question_id": qid, "arm": arm,
                    "groundedness": base, "completeness": base,
                    "numeric_fidelity": base, "constraint_satisfaction": base,
                    "overall_pass": 0 if arm == "A1" and i == 0 else 1,
                    "failure_tags": "numeric_mismatch" if i == 1 and arm == "A1" else "",
                    "notes": "",
                })
        with labels_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

    # run_record.yaml — known/unknown deployment.
    record = run_dir / "run_record.yaml"
    deployment = "gpt-9000" if unknown_deployment else "gpt-4o"
    record.write_text(
        f"summarizer_deployment: '{deployment}'\n"
        f"evaluator_deployment: '{deployment}'\n",
        encoding="utf-8",
    )
    return run_dir


# ---------------------------------------------------------------------------
# 1) Statistical primitive: bootstrap.
# ---------------------------------------------------------------------------
def test_bootstrap_paired_mean_diff_known_input():
    import numpy as np
    x = [1, 2, 3, 4]
    y = [2, 3, 4, 5]
    diff, low, high = ar.bootstrap_paired_mean_diff(x, y, n_iters=2000, seed=0)
    # Expected diff is exactly +1 (each pair differs by 1).
    assert diff == pytest.approx(1.0, abs=1e-9)
    # Variance of the diff is zero → CI collapses around the mean.
    assert low == pytest.approx(1.0, abs=1e-9)
    assert high == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 2) Statistical primitive: Wilcoxon ties.
# ---------------------------------------------------------------------------
def test_wilcoxon_handles_ties():
    # Identical arrays: the test should not crash and should return p ~= 1.
    p = ar.wilcoxon_signed_rank_paired([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    assert p == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# 3) Statistical primitive: McNemar known input.
# ---------------------------------------------------------------------------
def test_mcnemar_known_input():
    # 5 discordant: 4 (0→1) and 1 (1→0). Independently computed expected p
    # via continuity-corrected chi^2: ((|4-1|-1)^2)/(4+1) = 4/5 = 0.8 → p ≈ 0.371.
    x = [0] * 4 + [1] + [0, 0, 0]
    y = [1] * 4 + [0] + [0, 0, 0]
    p = ar.mcnemar_paired_binary(x, y)
    assert 0.0 <= p <= 1.0
    # The exact statsmodels value is well above 0.05 — assertion is a sanity range.
    assert p > 0.1


# ---------------------------------------------------------------------------
# 4) End-to-end smoke: summary_table.csv.
# ---------------------------------------------------------------------------
def test_summary_table_smoke(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    questions_path = tmp_path / "questions.json"

    rc = ar.main([
        "--run-dir", str(run_dir),
        "--questions", str(questions_path),
        "--bootstrap-iters", "200",
        "--no-figures",
    ])
    assert rc == 0

    summary_path = run_dir / "analysis" / "summary_table.csv"
    assert summary_path.exists()
    rows = list(csv.DictReader(summary_path.open(encoding="utf-8")))
    assert len(rows) == 2  # A1, A2
    arms = {r["arm"] for r in rows}
    assert arms == {"A1", "A2"}
    for r in rows:
        for col in ("n", "pass_rate_evaluated", "fail_open_rate",
                    "mean_composite_quality", "median_latency_ms",
                    "total_tokens", "estimated_usd"):
            assert col in r


# ---------------------------------------------------------------------------
# 5) End-to-end smoke: pairwise_deltas.csv.
# ---------------------------------------------------------------------------
def test_pairwise_deltas_smoke(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    questions_path = tmp_path / "questions.json"

    rc = ar.main([
        "--run-dir", str(run_dir),
        "--questions", str(questions_path),
        "--bootstrap-iters", "200",
        "--no-figures",
    ])
    assert rc == 0

    pw = list(csv.DictReader(
        (run_dir / "analysis" / "pairwise_deltas.csv").open(encoding="utf-8")
    ))
    assert len(pw) == 1  # A1 vs A2
    row = pw[0]
    assert row["baseline"] == "A1"
    assert row["arm"] == "A2"
    # A2 = A1+1 for each of 4 pairs → delta_mean is exactly +1.
    assert float(row["delta_mean"]) == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# 6) per_axis_clean filter.
# ---------------------------------------------------------------------------
def test_filter_for_per_axis_excludes_fallback_rows(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    questions_path = tmp_path / "questions.json"

    rc = ar.main([
        "--run-dir", str(run_dir),
        "--questions", str(questions_path),
        "--bootstrap-iters", "200",
        "--no-figures",
    ])
    assert rc == 0

    rows = list(csv.DictReader(
        (run_dir / "analysis" / "per_axis_clean.csv").open(encoding="utf-8")
    ))
    # Q003 in A2 had score_fallbacks_applied set → A2 has 3 clean rows
    # for axes; A1 has 4 (no fallbacks recorded for A1).
    a2_rows = [r for r in rows if r["arm"] == "A2"]
    for r in a2_rows:
        assert int(r["n_clean"]) == 3, (
            f"A2 axis {r['axis']} should have n_clean=3 after fallback filter; "
            f"got {r['n_clean']}"
        )
    a1_rows = [r for r in rows if r["arm"] == "A1"]
    for r in a1_rows:
        assert int(r["n_clean"]) == 4


# ---------------------------------------------------------------------------
# 7) Error rows excluded.
# ---------------------------------------------------------------------------
def test_skips_error_rows(tmp_path, capsys):
    run_dir = _seed_run_dir(tmp_path, inject_error=True)
    questions_path = tmp_path / "questions.json"

    rc = ar.main([
        "--run-dir", str(run_dir),
        "--questions", str(questions_path),
        "--bootstrap-iters", "200",
        "--no-figures",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 error row" in out

    summary = list(csv.DictReader(
        (run_dir / "analysis" / "summary_table.csv").open(encoding="utf-8")
    ))
    a2 = next(r for r in summary if r["arm"] == "A2")
    assert int(a2["n"]) == 3  # 4 - 1 errored


# ---------------------------------------------------------------------------
# 8) Missing arm graceful.
# ---------------------------------------------------------------------------
def test_handles_missing_arm_gracefully(tmp_path):
    run_dir = _seed_run_dir(tmp_path)
    # Delete arm A2 entirely.
    import shutil
    shutil.rmtree(run_dir / "A2")
    # Drop A2 labels too.
    labels = list(csv.DictReader((run_dir / "human_labels.csv").open(encoding="utf-8")))
    labels = [r for r in labels if r["arm"] != "A2"]
    with (run_dir / "human_labels.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(labels[0].keys()))
        writer.writeheader()
        for r in labels:
            writer.writerow(r)

    questions_path = tmp_path / "questions.json"
    rc = ar.main([
        "--run-dir", str(run_dir),
        "--questions", str(questions_path),
        "--bootstrap-iters", "200",
        "--no-figures",
    ])
    assert rc == 0

    summary = list(csv.DictReader(
        (run_dir / "analysis" / "summary_table.csv").open(encoding="utf-8")
    ))
    assert len(summary) == 1
    assert summary[0]["arm"] == "A1"

    # Pairwise deltas is empty (no other arm to compare).
    pw_lines = (run_dir / "analysis" / "pairwise_deltas.csv").read_text().splitlines()
    assert len(pw_lines) == 1   # header only.


# ---------------------------------------------------------------------------
# 9) Retry-rescue: first vs final verdict distinct.
# ---------------------------------------------------------------------------
def test_first_pass_vs_final_verdict_distinct(tmp_path):
    """Q001 in A2: attempt0 verdict=fail, final eval_verdict=pass → rescue."""
    run_dir = _seed_run_dir(tmp_path)
    questions_path = tmp_path / "questions.json"

    rc = ar.main([
        "--run-dir", str(run_dir),
        "--questions", str(questions_path),
        "--bootstrap-iters", "200",
        "--no-figures",
    ])
    assert rc == 0

    # Build the master DF and verify directly (the figure isn't a CSV).
    arms = ar.discover_arms(run_dir)
    results = ar.load_results(run_dir, arms)
    traces = ar.load_traces(run_dir, arms)
    questions = ar.load_questions(questions_path)
    labels = ar.load_labels(run_dir / "human_labels.csv")
    df = ar.build_master_dataframe(results, traces, labels, questions)

    a2 = df[df["arm"] == "A2"]
    rescued = a2[(a2["first_attempt_verdict"] == "fail") & (a2["eval_verdict"] == "pass")]
    assert len(rescued) == 1
    first_pass = a2[(a2["eval_retry_count"].fillna(0).astype(int) == 0) &
                    (a2["eval_verdict"] == "pass")]
    assert len(first_pass) == 3   # Q002, Q003, Q004


# ---------------------------------------------------------------------------
# 10) Unknown deployment falls back to _default + warns.
# ---------------------------------------------------------------------------
def test_unknown_deployment_falls_back_to_default_pricing(tmp_path):
    run_dir = _seed_run_dir(tmp_path, unknown_deployment=True)
    questions_path = tmp_path / "questions.json"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rc = ar.main([
            "--run-dir", str(run_dir),
            "--questions", str(questions_path),
            "--bootstrap-iters", "200",
            "--no-figures",
        ])
    assert rc == 0

    # At least one warning should mention the unknown deployment.
    assert any("gpt-9000" in str(w.message) for w in caught), (
        f"expected an 'unknown deployment' warning, got: "
        f"{[str(w.message) for w in caught]}"
    )

    # The summary still got written and includes a finite estimated_usd.
    summary = list(csv.DictReader(
        (run_dir / "analysis" / "summary_table.csv").open(encoding="utf-8")
    ))
    for r in summary:
        usd = float(r["estimated_usd"])
        assert usd >= 0.0
