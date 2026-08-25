#!/usr/bin/env python3
"""Gate the audit worklist before any labelling starts — plan-eil-v25 Step 5.

Checks the three things that would silently invalidate the audit:
  1. every mandatory row_id actually made it into the worklist;
  2. the annotator has what they need — an answer, and the system's resolved building id
     (without it `entity_consistency` cannot be judged at all);
  3. the blindness contract holds — no gold column, no judge verdict leaking into a
     field the annotator reads.

Exit code 1 if any check fails, so it can gate a pipeline.

    python scripts/verify_worklist.py \
        --worklist artifacts/runs/2026-08-16_v25_worklist.csv \
        --include-rows artifacts/datasets/v25_mandatory_rows.txt \
        --null-axis entity_consistency
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

# Fields the annotator is shown by `spotcheck_closed_loop.py label`. Anything derived
# from gold appearing here would turn the human into a gold proxy rather than an
# independent instrument.
ANNOTATOR_VISIBLE = ("case_id", "expected_route", "question", "final_answer",
                     "retrieved_evidence_summary", "final_building_id_resolved")
GOLD_MARKERS = ("expected_building", "must_include", "must_not_include",
                "expected_fields", "gold")


def main(argv=None):
    """Run every structural check over a worklist CSV; exit non-zero on any failure."""
    p = argparse.ArgumentParser()
    p.add_argument("--worklist", required=True, type=Path)
    p.add_argument("--include-rows", type=Path)
    p.add_argument("--null-axis", default="entity_consistency")
    args = p.parse_args(argv)

    with args.worklist.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fields = reader.fieldnames or []
    axes = [f[len("judge_"):] for f in fields if f.startswith("judge_")
            and f[len("judge_"):] not in
            {"verdict", "composite", "semantic_judge_pass", "fail_open", "evidence_present"}]

    fails: list[str] = []

    def check(ok: bool, msg: str):
        """Record one pass/fail line; failures accumulate into the exit status."""
        print(f"[{'ok  ' if ok else 'FAIL'}] {msg}")
        if not ok:
            fails.append(msg)

    print(f"worklist: {args.worklist}  rows={len(rows)}  axes={','.join(axes)}\n")

    ids = {r["row_id"] for r in rows}
    check(len(ids) == len(rows), f"row_id unique ({len(ids)} distinct / {len(rows)} rows)")

    if args.include_rows:
        mand = [ln.split("#", 1)[0].strip()
                for ln in args.include_rows.read_text(encoding="utf-8").splitlines()]
        mand = [m for m in mand if m]
        missing = [m for m in mand if m not in ids]
        check(not missing, f"all {len(mand)} mandatory rows present"
                           + (f" — MISSING {missing}" if missing else ""))

    n_null = sum(1 for r in rows if r.get(f"judge_{args.null_axis}", "") == "")
    print(f"       {args.null_axis}: {n_null} null / {len(rows) - n_null} scored")

    check(all(r["final_answer"].strip() for r in rows), "every row has a final answer")
    check(all(r["judge_verdict"].strip() for r in rows), "every row has a judge verdict")

    # Only scored rows can be judged on the null axis, so only they need the building id.
    need_id = [r for r in rows if r.get(f"judge_{args.null_axis}", "") != ""]
    have_id = sum(1 for r in need_id if r["final_building_id_resolved"].strip())
    check(have_id == len(need_id),
          f"every {args.null_axis}-scored row carries final_building_id_resolved "
          f"({have_id}/{len(need_id)})")

    leaked = [c for c in fields if any(g in c.lower() for g in GOLD_MARKERS)]
    check(not leaked, f"no gold column in the worklist{' — LEAKED ' + str(leaked) if leaked else ''}")

    spill = [c for c in ANNOTATOR_VISIBLE if c.startswith("judge_")]
    check(not spill, "no judge field inside the annotator-visible set")

    print("\nframe coverage:")
    for k in ("run_id", "arm"):
        print(f"  {k}: {dict(sorted(Counter(r[k] for r in rows).items()))}")

    print("\nRESULT:", "PASS" if not fails else f"FAIL ({len(fails)})")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
