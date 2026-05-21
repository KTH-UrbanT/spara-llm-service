"""Markdown-form alternative to the interactive relabel session.

Subcommands:
  emit    Generate a fillable markdown form with the same 8 outputs the
          stratified sampler would pick interactively (seed=7, subset=8 by
          default — adjust via --relabel-subset / --relabel-seed).
  ingest  Parse a filled-in markdown form and append rows to
          intra_annotator_relabels.csv (same schema as the interactive mode,
          relabel_round=2). Already-written output_ids are skipped.

Mirrors label_outputs.py exactly for: stratified sample selection, the
blinded view (no arm shown), the rubric axes/scales, allowed failure tags,
CSV schema, and the resume-safety check against existing rows.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse the existing implementation so the sample matches the interactive mode
# byte-for-byte. If label_outputs.py changes its sampler, this stays in sync.
from scripts.label_outputs import (
    ALLOWED_FAILURE_TAGS,
    LABELS_FILENAME,
    RELABEL_CSV_FIELDS,
    RELABELS_FILENAME,
    _stratified_sample,
    append_label_row,
    collect_outputs,
    discover_arms,
    load_existing_label_ids,
    load_questions,
)


AXES = ("groundedness", "completeness",
        "numeric_fidelity", "constraint_satisfaction")


# ---------------------------------------------------------------------------
# emit — produce the fillable form.
# ---------------------------------------------------------------------------
def cmd_emit(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    labels_path = run_dir / LABELS_FILENAME
    if not labels_path.exists():
        print(f"ERROR: {labels_path} not found — run label mode first.", file=sys.stderr)
        return 1

    questions = load_questions(args.questions)
    arms = discover_arms(run_dir)
    if not arms:
        print(f"ERROR: no <arm>/results.jsonl found under {run_dir}", file=sys.stderr)
        return 1

    outputs = collect_outputs(run_dir, arms, questions)
    by_id = {o["output_id"]: o for o in outputs}

    with labels_path.open(encoding="utf-8") as f:
        labelled_ids = {row["output_id"] for row in csv.DictReader(f)
                        if row.get("output_id")}
    candidates = [by_id[oid] for oid in labelled_ids if oid in by_id]
    if not candidates:
        print("ERROR: no overlap between labelled outputs and run dir contents.", file=sys.stderr)
        return 1

    selected = _stratified_sample(
        candidates, target_size=args.relabel_subset, seed=args.relabel_seed,
    )
    if not selected:
        print("ERROR: stratified sample returned 0 outputs.", file=sys.stderr)
        return 1

    cat_counts: Dict[str, int] = {}
    for o in selected:
        cat_counts[o.get("category") or "uncategorised"] = (
            cat_counts.get(o.get("category") or "uncategorised", 0) + 1)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        f.write(_render_form(selected, args.relabel_subset, args.relabel_seed, cat_counts))

    print(f"Wrote {out_path} — {len(selected)} output(s); category mix: {cat_counts}")
    return 0


def _render_form(selected: List[Dict[str, Any]], subset: int, seed: int,
                 cat_counts: Dict[str, int]) -> str:
    parts: List[str] = []
    parts.append(f"""# Intra-annotator relabel form

Fill in the score block under each output. Do NOT edit anything outside the
score blocks (the `output_id`, question, evidence, and answer are inputs and
the ingestor identifies rows by `output_id`).

**Sampler config:** `--relabel-subset {subset} --relabel-seed {seed}` — mix: {cat_counts}

**Scoring rubric (matches the interactive `_prompt_rubric`):**

- `groundedness`, `completeness`, `numeric_fidelity`, `constraint_satisfaction` — integer **0–4** each.
- `overall_pass` — **0** (no) or **1** (yes).
- `failure_tags` — comma-separated list, empty for none. Allowed: `{', '.join(sorted(ALLOWED_FAILURE_TAGS))}`.
- `notes` — one line of free text (optional).

**When you're done, save and run:**

    cd /Users/nico/Documents/GitHub/spara/llm-service
    .venv/bin/python -m scripts.relabel_md ingest {Path('artifacts/relabel_form.md')}

That appends to `artifacts/runs/2026-05-14_v2/intra_annotator_relabels.csv` (resume-safe — already-ingested `output_id`s are skipped).

---
""")
    for i, o in enumerate(selected, start=1):
        parts.append(_render_one(i, len(selected), o))
    return "\n".join(parts)


def _render_one(idx: int, total: int, output: Dict[str, Any]) -> str:
    agg = output.get("aggregated_data") or {}
    agg_str = json.dumps(agg, ensure_ascii=False, indent=2) if agg else "(no evidence captured)"
    return f"""## Output {idx} of {total} — output_id: `{output['output_id']}`

**Question:** {output['question']}

<details>
<summary><b>Evidence (aggregated_data)</b> — click to expand</summary>

```json
{agg_str}
```

</details>

**Generated answer:**

> {output['answer'].replace(chr(10), chr(10) + '> ')}

**Your scores:**

```yaml
output_id: {output['output_id']}
groundedness:            # 0-4
completeness:            # 0-4
numeric_fidelity:        # 0-4
constraint_satisfaction: # 0-4
overall_pass:            # 0 or 1
failure_tags:            # comma-separated or empty
notes:                   # optional one-liner
```

---
"""


# ---------------------------------------------------------------------------
# ingest — parse filled form, append CSV rows.
# ---------------------------------------------------------------------------
BLOCK_RE = re.compile(
    r"```yaml\s*\n(.*?)\n```",
    re.DOTALL,
)
KV_RE = re.compile(r"^([a-z_]+)\s*:\s*(.*?)(?:\s*#.*)?$")


def cmd_ingest(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    labels_path = run_dir / LABELS_FILENAME
    relabels_path = run_dir / RELABELS_FILENAME
    if not labels_path.exists():
        print(f"ERROR: {labels_path} not found.", file=sys.stderr)
        return 1

    questions = load_questions(args.questions)
    arms = discover_arms(run_dir)
    outputs = collect_outputs(run_dir, arms, questions)
    by_id = {o["output_id"]: o for o in outputs}

    form_text = Path(args.form).read_text(encoding="utf-8")
    blocks = BLOCK_RE.findall(form_text)
    if not blocks:
        print("ERROR: no ```yaml ... ``` score blocks found in the form.", file=sys.stderr)
        return 1

    already = load_existing_label_ids(relabels_path)
    written = skipped_done = skipped_blank = errors = 0

    for block in blocks:
        parsed = _parse_block(block)
        if parsed is None:
            errors += 1
            print(f"[skip] could not parse block:\n{block!r}", file=sys.stderr)
            continue
        oid = parsed.get("output_id")
        if not oid:
            errors += 1
            print(f"[skip] block missing output_id:\n{block!r}", file=sys.stderr)
            continue
        if _is_blank(parsed):
            skipped_blank += 1
            print(f"[skip] {oid}: scores blank — not yet filled in")
            continue
        if oid in already:
            skipped_done += 1
            print(f"[skip] {oid}: already in {RELABELS_FILENAME}")
            continue
        meta = by_id.get(oid)
        if meta is None:
            errors += 1
            print(f"[skip] {oid}: not found in run_dir outputs", file=sys.stderr)
            continue
        row = _build_row(parsed, meta, args.annotator_id)
        validation_error = _validate_row(row)
        if validation_error:
            errors += 1
            print(f"[skip] {oid}: {validation_error}", file=sys.stderr)
            continue
        append_label_row(relabels_path, RELABEL_CSV_FIELDS, row)
        already.add(oid)
        written += 1
        print(f"[ok]   {oid}: wrote row (overall_pass={row['overall_pass']})")

    print(f"\nWrote {written}; already-done {skipped_done}; "
          f"blank {skipped_blank}; errors {errors}.")
    if errors:
        return 2
    return 0


def _parse_block(block: str) -> Optional[Dict[str, str]]:
    out: Dict[str, str] = {}
    for raw in block.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = KV_RE.match(line)
        if not m:
            continue
        out[m.group(1)] = m.group(2).strip()
    return out or None


def _is_blank(parsed: Dict[str, str]) -> bool:
    """A block counts as blank if every rubric field is empty."""
    return all(not parsed.get(k, "").strip() for k in
               (*AXES, "overall_pass"))


def _build_row(parsed: Dict[str, str], meta: Dict[str, Any],
               annotator_id: str) -> Dict[str, Any]:
    return {
        "output_id": meta["output_id"],
        "annotator_id": annotator_id,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "question_id": meta["question_id"],
        "arm": meta["arm"],
        "groundedness": parsed.get("groundedness", ""),
        "completeness": parsed.get("completeness", ""),
        "numeric_fidelity": parsed.get("numeric_fidelity", ""),
        "constraint_satisfaction": parsed.get("constraint_satisfaction", ""),
        "overall_pass": parsed.get("overall_pass", ""),
        "failure_tags": parsed.get("failure_tags", ""),
        "notes": parsed.get("notes", ""),
        "relabel_round": 2,
    }


def _validate_row(row: Dict[str, Any]) -> Optional[str]:
    for axis in AXES:
        val = str(row[axis]).strip()
        if not val.isdigit() or not (0 <= int(val) <= 4):
            return f"{axis}={val!r} not an integer in [0,4]"
    op = str(row["overall_pass"]).strip()
    if op not in ("0", "1"):
        return f"overall_pass={op!r} must be 0 or 1"
    tags = str(row["failure_tags"]).strip()
    if tags:
        parts = [t.strip() for t in tags.split(",") if t.strip()]
        bad = [t for t in parts if t not in ALLOWED_FAILURE_TAGS]
        if bad:
            return f"unknown failure_tags: {bad}"
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("emit", help="Write the markdown form.")
    pe.add_argument("--run-dir", required=True, type=Path)
    pe.add_argument("--output", required=True, type=Path,
                    help="Path to write the markdown form (e.g. artifacts/relabel_form.md).")
    pe.add_argument("--questions", type=Path,
                    default=Path("src/config/questions.json"))
    pe.add_argument("--relabel-subset", type=int, default=8)
    pe.add_argument("--relabel-seed", type=int, default=7)

    pi = sub.add_parser("ingest", help="Read filled form, append CSV rows.")
    pi.add_argument("--run-dir", required=True, type=Path)
    pi.add_argument("--annotator-id", required=True)
    pi.add_argument("form", type=Path,
                    help="Path to the filled markdown form.")
    pi.add_argument("--questions", type=Path,
                    default=Path("src/config/questions.json"))

    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.cmd == "emit":
        return cmd_emit(args)
    if args.cmd == "ingest":
        return cmd_ingest(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
