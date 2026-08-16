#!/usr/bin/env python3
"""Structural + coherence check of the v25 human labels — run BEFORE kappa.

Deliberately reports NO agreement statistic: reading agreement before the labels are
frozen invites nudging them toward the judge, which is what the pre-registration
forbids. Everything here checks the labels against the *rows themselves*:

  1. blanks sit exactly on the rows where the judge's axis was null (not applicable);
  2. every score is in range, overall_pass present on all 65;
  3. self-consistency: overall_pass=1 with a sub-floor axis score, or overall_pass=0
     with all axes >= 8, is flagged for the annotator to confirm (not to change);
  4. entity coherence: rows whose ANSWER TEXT cites a byggnadsid different from
     final_building_id_resolved but got entity_consistency >= 8 from the human —
     the one label error the answer text alone can expose.

Flags are questions for the annotator, never edits. Exit 0 always (informational),
except structural failures (1/2) which exit 1.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

BID = re.compile(r"\b\d{2}-\d{2,3}-[A-Z0-9ÅÄÖ]+-\d+\b")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--worklist", required=True, type=Path)
    p.add_argument("--labels", required=True, type=Path)
    args = p.parse_args(argv)

    with args.worklist.open(encoding="utf-8") as f:
        wl = {r["row_id"]: r for r in csv.DictReader(f)}
    with args.labels.open(encoding="utf-8") as f:
        labels = list(csv.DictReader(f))

    hard_fails: list[str] = []
    asks: list[str] = []

    # -- 1. blanks exactly where the axis was inapplicable ---------------------------
    blank_lb = {L["row_id"] for L in labels if L["entity_consistency"].strip() == ""}
    null_wl = {rid for rid, w in wl.items() if w["judge_entity_consistency"].strip() == ""}
    if blank_lb != null_wl:
        only_lb = blank_lb - null_wl
        only_wl = null_wl - blank_lb
        if only_lb:
            hard_fails.append(f"blank entity_consistency on SCORABLE rows: {sorted(only_lb)}")
        if only_wl:
            hard_fails.append(f"scored entity_consistency on not-applicable rows: {sorted(only_wl)}")
    print(f"[{'ok' if blank_lb == null_wl else 'FAIL'}] blanks == not-applicable rows "
          f"({len(blank_lb)} blank / {len(null_wl)} n.a.)")

    # -- 2. ranges + coverage --------------------------------------------------------
    n_bad = 0
    axes = ("faithfulness", "answer_relevance", "entity_consistency", "calibration")
    for L in labels:
        for ax in axes:
            v = L[ax].strip()
            if v and not (v.isdigit() and 0 <= int(v) <= 10):
                n_bad += 1
        if L["overall_pass"].strip() not in ("0", "1"):
            n_bad += 1
    if len(labels) != len(wl):
        hard_fails.append(f"{len(labels)} labels for {len(wl)} worklist rows")
    if n_bad:
        hard_fails.append(f"{n_bad} out-of-range values")
    print(f"[{'ok' if not n_bad and len(labels) == len(wl) else 'FAIL'}] "
          f"{len(labels)}/{len(wl)} rows, all values in range")

    # -- 3. self-consistency ---------------------------------------------------------
    for L in labels:
        scored = [(ax, int(L[ax])) for ax in axes if L[ax].strip()]
        low = [(ax, v) for ax, v in scored if v < 4]
        if L["overall_pass"] == "1" and low:
            asks.append(f"PASS but sub-floor {low}: {L['row_id']}  (notes: {L['notes'][:60]!r})")
        if L["overall_pass"] == "0" and scored and all(v >= 8 for _, v in scored):
            asks.append(f"FAIL but all axes >= 8: {L['row_id']}  (notes: {L['notes'][:60]!r})")

    # -- 4. entity coherence against the answer text ---------------------------------
    for L in labels:
        v = L["entity_consistency"].strip()
        if not v or int(v) < 8:
            continue
        w = wl[L["row_id"]]
        resolved = w["final_building_id_resolved"].strip()
        cited = set(BID.findall(w["final_answer"]))
        foreign = cited - {resolved}
        if resolved and foreign:
            asks.append(f"entity={v} but answer cites {sorted(foreign)} beside resolved "
                        f"{resolved}: {L['row_id']}  (notes: {L['notes'][:60]!r})")

    print(f"\n{len(asks)} row(s) to confirm with the annotator "
          f"(questions, not corrections):")
    for a in asks:
        print(f"  - {a}")

    if hard_fails:
        print("\nSTRUCTURAL FAILURES:")
        for h in hard_fails:
            print(f"  ! {h}")
        return 1
    print("\nstructural: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
