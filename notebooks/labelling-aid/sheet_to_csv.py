"""Parse the filled labelling_sheet.md into the canonical human_labels.csv format.

The sheet has 40 sections, each shaped like:

    ## 17 · `7a3f9e1c`  ·  Q012  ·  *edge_case / hard*
    ...response and reference material...
    **Scores:**
    ```
    G: 3
    C: 4
    NF: 4
    CS: 4
    P: 1
    TAGS: irrelevant_extras,unsupported_claim
    NOTES: agent leaked the YYMM-decoded period
    ```

This script:
  1. Re-derives the (qid, arm) → output_id mapping from results.jsonl so we
     can recover the arm even though the sheet hides it (preserves blinding
     for the labeller, not for the analysis pipeline).
  2. Parses each section's score block.
  3. Validates ranges (G/C/NF/CS in 0..4, P in 0/1, failure_tags within the
     allowed set per label_outputs.py).
  4. Writes the 12-column human_labels.csv at the right path inside the run dir.

Usage from repo root:

    llm-service/.venv/bin/python \
        llm-service/notebooks/labelling-aid/sheet_to_csv.py \
        --run-dir   llm-service/artifacts/runs/2026-05-14_v2 \
        --sheet     llm-service/notebooks/labelling-aid/labelling_sheet.md \
        --annotator huncho
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Mirror label_outputs.py constants so the CSV is byte-compatible
COLUMNS = [
    "output_id", "annotator_id", "timestamp_utc",
    "question_id", "arm",
    "groundedness", "completeness", "numeric_fidelity", "constraint_satisfaction",
    "overall_pass", "failure_tags", "notes",
]
ALLOWED_FAILURE_TAGS = {
    "unsupported_claim", "numeric_mismatch", "constraint_violation",
    "partial_answer", "overconfident_uncertainty", "irrelevant_extras",
}
ARMS = ("A1", "A2")


def output_id(qid: str, arm: str) -> str:
    return hashlib.sha1(f"{qid}|{arm}".encode("utf-8")).hexdigest()[:8]


def build_oid_to_arm(run_dir: Path) -> Dict[str, Tuple[str, str]]:
    """For each results.jsonl row, compute output_id and remember its arm."""
    mapping: Dict[str, Tuple[str, str]] = {}
    for arm in ARMS:
        path = run_dir / arm / "results.jsonl"
        if not path.exists():
            sys.exit(f"missing {path}")
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            qid = row.get("question_id")
            if not isinstance(qid, str):
                continue
            oid = output_id(qid, arm)
            mapping[oid] = (qid, arm)
    return mapping


# Regex for the section header:  "## 01 · `e2d2913c`  ·  Q010  ·  *simple_address / easy*"
SECTION_HEADER = re.compile(
    r"^##\s+\d+\s+·\s+`(?P<oid>[0-9a-f]{8})`\s+·\s+(?P<qid>Q\d{3})\b"
)
SCORE_FIELD = re.compile(r"^(?P<key>G|C|NF|CS|P|TAGS|NOTES)\s*:\s*(?P<val>.*)$")


def parse_sheet(sheet_path: Path) -> List[Dict[str, str]]:
    """Return list of parsed score blocks in order of appearance."""
    text = sheet_path.read_text()
    sections = []
    current: Optional[Dict[str, str]] = None
    in_score_block = False

    for raw in text.splitlines():
        m = SECTION_HEADER.match(raw)
        if m:
            if current is not None:
                sections.append(current)
            current = {"output_id": m.group("oid"), "question_id": m.group("qid")}
            in_score_block = False
            continue
        if current is None:
            continue
        if raw.strip() == "**Scores:**":
            in_score_block = True
            continue
        if in_score_block:
            if raw.strip().startswith("```"):
                # entering or exiting code fence; only meaningful if entering
                continue
            mf = SCORE_FIELD.match(raw.strip())
            if mf:
                current[mf.group("key")] = mf.group("val").strip()

    if current is not None:
        sections.append(current)
    return sections


def coerce(val: str, kind: str, name: str, oid: str, errors: List[str]) -> Optional[int | str]:
    """Coerce a raw string to the right type with friendly errors."""
    val = val.strip()
    # TAGS is optional — empty means "no failure tags" (same as label_outputs.py).
    # NOTES is also optional.
    if val == "" and kind == "tags":
        return ""
    if val == "" and kind == "text":
        return ""
    if val == "":
        errors.append(f"  · {oid}: field {name!r} is empty")
        return None
    if kind == "int04":
        try:
            n = int(val)
        except ValueError:
            errors.append(f"  · {oid}: field {name!r} = {val!r} is not an integer")
            return None
        if n < 0 or n > 4:
            errors.append(f"  · {oid}: field {name!r} = {n} is outside 0..4")
            return None
        return n
    if kind == "int01":
        try:
            n = int(val)
        except ValueError:
            errors.append(f"  · {oid}: field {name!r} = {val!r} is not 0 or 1")
            return None
        if n not in (0, 1):
            errors.append(f"  · {oid}: field {name!r} = {n} is outside 0/1")
            return None
        return n
    if kind == "tags":
        tags = [t.strip() for t in val.split(",") if t.strip()]
        bad = [t for t in tags if t not in ALLOWED_FAILURE_TAGS]
        if bad:
            errors.append(
                f"  · {oid}: unknown failure tag(s) {bad}; allowed: {sorted(ALLOWED_FAILURE_TAGS)}"
            )
            return None
        return ",".join(tags)
    if kind == "text":
        return val
    raise AssertionError(f"unknown coercion kind {kind}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--sheet",   required=True, type=Path)
    p.add_argument("--annotator", required=True)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Override CSV output path. Default: <run-dir>/human_labels.csv",
    )
    p.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow sections with empty score fields (write what is present).",
    )
    args = p.parse_args()

    oid_to_arm = build_oid_to_arm(args.run_dir)
    expected = set(oid_to_arm.keys())
    if len(expected) != 40:
        print(f"WARN: expected 40 outputs, results.jsonl files contain {len(expected)}")

    sections = parse_sheet(args.sheet)
    print(f"Parsed {len(sections)} section(s) from {args.sheet}")
    print(f"results.jsonl has {len(expected)} outputs")

    seen_oids = set()
    rows: List[Dict[str, str]] = []
    errors: List[str] = []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for sec in sections:
        oid = sec.get("output_id")
        if oid not in oid_to_arm:
            errors.append(f"  · unknown output_id {oid!r} (not in results.jsonl); skipping")
            continue
        qid_expected, arm = oid_to_arm[oid]
        if sec.get("question_id") != qid_expected:
            errors.append(
                f"  · {oid}: header qid={sec.get('question_id')} but oid maps to {qid_expected}"
            )

        # Skip unscored sections in partial mode
        any_score = any(sec.get(k, "").strip() for k in ("G", "C", "NF", "CS", "P"))
        if not any_score and args.allow_partial:
            continue
        if not any_score:
            errors.append(f"  · {oid}: no scores filled in")
            continue

        g  = coerce(sec.get("G", ""),  "int04", "G",  oid, errors)
        c  = coerce(sec.get("C", ""),  "int04", "C",  oid, errors)
        nf = coerce(sec.get("NF", ""), "int04", "NF", oid, errors)
        cs = coerce(sec.get("CS", ""), "int04", "CS", oid, errors)
        pp = coerce(sec.get("P", ""),  "int01", "P",  oid, errors)
        tags = coerce(sec.get("TAGS", ""), "tags", "TAGS", oid, errors)
        notes = sec.get("NOTES", "").strip()

        if None in (g, c, nf, cs, pp) or tags is None:
            continue

        if oid in seen_oids:
            errors.append(f"  · {oid}: duplicate section in sheet (using first)")
            continue
        seen_oids.add(oid)

        rows.append({
            "output_id": oid,
            "annotator_id": args.annotator,
            "timestamp_utc": now,
            "question_id": qid_expected,
            "arm": arm,
            "groundedness": g,
            "completeness": c,
            "numeric_fidelity": nf,
            "constraint_satisfaction": cs,
            "overall_pass": pp,
            "failure_tags": tags or "",
            "notes": notes,
        })

    missing = expected - seen_oids
    if missing and not args.allow_partial:
        errors.append(f"  · {len(missing)} output(s) have no labels: {sorted(missing)[:6]}...")

    if errors:
        print()
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(e)
        if not args.allow_partial:
            print()
            print("Refusing to write CSV due to errors. Re-run with --allow-partial to write what is present.")
            return 1

    out_path = args.out or (args.run_dir / "human_labels.csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print()
    print(f"Wrote {out_path} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
