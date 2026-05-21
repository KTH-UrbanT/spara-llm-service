"""Compare Q012/Q013 evaluator verdicts: v4 baseline (A2) vs schema-aware (A2_schema).

Reads results.jsonl (one row per question, final verdict already deduplicated).
No labelling required — the test is whether the evaluator's own verdict shifts.
"""
import json
from pathlib import Path

RUNS = [
    ("v4 baseline (A2)",         "artifacts/runs/2026-05-14_v2/A2/results.jsonl"),
    ("schema-aware (A2_schema)", "artifacts/runs/2026-05-21_schema/A2_schema/results.jsonl"),
]

for label, path in RUNS:
    print(f"\n{label}:")
    for line in Path(path).read_text().splitlines():
        r = json.loads(line)
        if r["question_id"] in ("Q012", "Q013"):
            verdict = r.get("eval_verdict", "?")
            scores = r.get("eval_scores", {}) or {}
            print(f"  {r['question_id']}: verdict={verdict}  composite={scores.get('composite_score')}  hard_fail={scores.get('hard_fail')}")
