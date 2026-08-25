#!/usr/bin/env python3
"""Single-annotator, blinded labelling tool for the SPARA EIL study.

For every ``(question_id, arm)`` pair the runner produced, this script
shows the annotator the question + the SQL/vector evidence + the model's
generated answer, and prompts for a four-axis ordinal rubric plus a
binary overall pass and optional failure tags. The arm identity is
hidden — outputs are referenced by an opaque ``output_id``
(``sha1("{qid}|{arm}")[:8]``) and the labelling order is deterministic
but randomised across arms.

Two modes:

- **label** (default) — primary labelling pass.
- **relabel** — intra-annotator reliability pass; relabels a stratified
  random subset without showing the original labels.

Both modes are resume-safe: a Ctrl-C mid-row discards the partial input
for that row; the next invocation re-offers the same row from scratch.

Cross-references:
  - plan-eil-v2.md §C.3 for the spec.
  - plan-eil-v2.md §B.4 for the rubric.
  - plan-eil-v2.md §B.5 for the intra-annotator design.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


ARMS = ("A1", "A2", "A3", "A4")

ALLOWED_FAILURE_TAGS = {
    "unsupported_claim",
    "numeric_mismatch",
    "constraint_violation",
    "partial_answer",
    "overconfident_uncertainty",
    "irrelevant_extras",
}

LABEL_CSV_FIELDS = (
    "output_id",
    "annotator_id",
    "timestamp_utc",
    "question_id",
    "arm",
    "groundedness",
    "completeness",
    "numeric_fidelity",
    "constraint_satisfaction",
    "overall_pass",
    "failure_tags",
    "notes",
)
RELABEL_CSV_FIELDS = LABEL_CSV_FIELDS + ("relabel_round",)

LABELS_FILENAME = "human_labels.csv"
RELABELS_FILENAME = "intra_annotator_relabels.csv"


# ---------------------------------------------------------------------------
# Data classes (kept as dicts to stay JSON/CSV-friendly without dataclass deps).
# ---------------------------------------------------------------------------
def _output_id(question_id: str, arm: str) -> str:
    """Stable, blinded identifier. The ``|`` separator avoids collisions
    between e.g. (Q1, A1) and (Q1A, 1)."""
    return hashlib.sha1(f"{question_id}|{arm}".encode("utf-8")).hexdigest()[:8]


# ---------------------------------------------------------------------------
# Discovery / loading helpers.
# ---------------------------------------------------------------------------
def discover_arms(run_dir: Path) -> List[str]:
    """Return the arms for which a ``results.jsonl`` exists in ``run_dir``."""
    found = []
    for arm in ARMS:
        if (run_dir / arm / "results.jsonl").exists():
            found.append(arm)
    return found


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


def load_questions(questions_path: Path) -> Dict[str, Dict[str, Any]]:
    """Index ``questions.json`` by ``question_id`` for the join."""
    with questions_path.open(encoding="utf-8") as f:
        data = json.load(f)
    return {q["question_id"]: q for q in data.get("questions", [])
            if isinstance(q.get("question_id"), str)}


def load_aggregated(arm_dir: Path, qid: str) -> Dict[str, Any]:
    """Read the truncated ``aggregated_data`` snapshot for ``(arm, qid)``.
    Missing → empty dict; we still let the annotator score, since the
    answer alone is enough for some axes."""
    f = arm_dir / "aggregated_cache" / f"{qid}.json"
    if not f.exists():
        return {}
    try:
        with f.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return {}


def collect_outputs(run_dir: Path, arms: Sequence[str],
                    questions: Dict[str, Dict[str, Any]],
                    log: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Build the denormalised list of labellable outputs.

    Skip rows that contain an ``error`` field — those represent crashed
    graph invocations and have no answer to label. A summary of skipped
    rows is appended to ``log`` (also printed by the caller).
    """
    outputs: List[Dict[str, Any]] = []
    skipped: List[str] = []
    for arm in arms:
        results_path = run_dir / arm / "results.jsonl"
        for row in _iter_jsonl(results_path):
            qid = row.get("question_id")
            if not isinstance(qid, str):
                continue
            if "error" in row:
                skipped.append(f"{qid}/{arm}: {row['error']!s:.80}")
                continue
            qrec = questions.get(qid)
            if qrec is None:
                # Dataset/results drift — surface but don't crash.
                skipped.append(f"{qid}/{arm}: question_id not in questions.json")
                continue
            outputs.append({
                "output_id": _output_id(qid, arm),
                "question_id": qid,
                "arm": arm,
                "category": qrec.get("category"),
                "question": qrec.get("question", ""),
                "answer": row.get("final_response") or "",
                "aggregated_data": load_aggregated(run_dir / arm, qid),
            })
    if skipped and log is not None:
        log.extend(skipped)
    return outputs


# ---------------------------------------------------------------------------
# CSV I/O.
# ---------------------------------------------------------------------------
def load_existing_label_ids(csv_path: Path) -> set:
    """Return the set of ``output_id`` values already labelled."""
    if not csv_path.exists():
        return set()
    ids: set = set()
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            oid = row.get("output_id")
            if oid:
                ids.add(oid)
    return ids


def append_label_row(csv_path: Path, fields: Sequence[str],
                     row: Dict[str, Any]) -> None:
    """Append one row to ``csv_path``. Writes the header on first append."""
    is_new = not csv_path.exists()
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(fields))
        if is_new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fields})


# ---------------------------------------------------------------------------
# Validation helpers (the rubric prompts re-prompt on invalid input).
# ---------------------------------------------------------------------------
def _ask(prompt: str) -> str:
    """Single ``input(prompt)`` call. Wrapped so tests can monkeypatch one
    function rather than the whole stdin pipeline."""
    return input(prompt)


def ask_int_in_range(label: str, low: int, high: int) -> int:
    """Prompt until the annotator enters an integer inside [low, high]."""
    while True:
        raw = _ask(f"  {label} ({low}-{high}): ").strip()
        if not raw.isdigit() and not (raw.startswith("-") and raw[1:].isdigit()):
            print(f"    ! must be an integer in [{low},{high}]")
            continue
        value = int(raw)
        if low <= value <= high:
            return value
        print(f"    ! {value} outside [{low},{high}]")


def ask_failure_tags() -> str:
    """Return a CSV-safe comma-joined string of tags. Empty allowed."""
    while True:
        raw = _ask("  failure_tags (comma-separated, empty for none): ").strip()
        if not raw:
            return ""
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        bad = [t for t in tags if t not in ALLOWED_FAILURE_TAGS]
        if bad:
            print(f"    ! unknown tag(s): {bad}; allowed: {sorted(ALLOWED_FAILURE_TAGS)}")
            continue
        return ",".join(tags)


def ask_notes() -> str:
    """Prompt for the optional free-text note on a label."""
    return _ask("  notes (one line): ").strip()


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------
def _format_aggregated(agg: Dict[str, Any]) -> str:
    """Pretty-print the evidence dict; values are already truncated upstream."""
    if not agg:
        return "(no evidence captured)"
    return json.dumps(agg, ensure_ascii=False, indent=2)


def render_output(idx: int, total: int, output: Dict[str, Any]) -> str:
    """Build the printed block. Arm identity is intentionally absent — the
    output_id is the only handle the annotator sees."""
    return (
        f"\n──── Output {idx} of {total}  [output_id={output['output_id']}] ────\n"
        f"Question: {output['question']}\n"
        f"─── Evidence (aggregated_data) ───\n"
        f"{_format_aggregated(output['aggregated_data'])}\n"
        f"─── Generated answer ───\n"
        f"{output['answer']}\n"
        f"────────────────────────────\n"
    )


# ---------------------------------------------------------------------------
# Mode: label
# ---------------------------------------------------------------------------
def run_label_mode(args: argparse.Namespace) -> int:
    """Label every unlabelled output, blinded to which arm produced it.

    Blinding is the point: the annotator sees `_output_id`, never the arm name, so
    knowing which system is under test cannot bias the score.
    """
    run_dir = Path(args.run_dir)
    csv_path = run_dir / LABELS_FILENAME
    questions = load_questions(args.questions)

    arms = discover_arms(run_dir)
    if not arms:
        print(f"ERROR: no <arm>/results.jsonl found under {run_dir}")
        return 1

    log: List[str] = []
    outputs = collect_outputs(run_dir, arms, questions, log=log)
    if log:
        for line in log:
            print(f"[skipped] {line}")

    rng = random.Random(args.shuffle_seed)
    rng.shuffle(outputs)

    done = load_existing_label_ids(csv_path)
    if done:
        print(f"Resuming — {len(done)} output(s) already labelled.")

    pending = [o for o in outputs if o["output_id"] not in done]
    if not pending:
        print("All outputs already labelled. Nothing to do.")
        _print_summary(csv_path)
        return 0

    cap = args.limit if args.limit is not None else len(pending)
    session_target = min(cap, len(pending))
    print(f"Labelling {session_target} output(s) this session "
          f"({len(pending)} pending in total).")

    labelled_this_session = 0
    for i, output in enumerate(pending[:session_target], start=1):
        print(render_output(i, session_target, output))
        try:
            row = _prompt_rubric(output, annotator_id=args.annotator_id)
        except KeyboardInterrupt:
            print("\nInterrupted; partial input discarded. "
                  "Re-run to resume from this output.")
            return 130
        append_label_row(csv_path, LABEL_CSV_FIELDS, row)
        labelled_this_session += 1

    print(f"\nLabelled {labelled_this_session} output(s) this session.")
    _print_summary(csv_path)
    return 0


def _prompt_rubric(output: Dict[str, Any], annotator_id: str) -> Dict[str, Any]:
    """Drive the four ordinal axes + binary pass + tags + notes."""
    groundedness = ask_int_in_range("groundedness", 0, 4)
    completeness = ask_int_in_range("completeness", 0, 4)
    numeric_fidelity = ask_int_in_range("numeric_fidelity", 0, 4)
    constraint_satisfaction = ask_int_in_range("constraint_satisfaction", 0, 4)
    overall_pass = ask_int_in_range("overall_pass (0=no, 1=yes)", 0, 1)
    failure_tags = ask_failure_tags()
    notes = ask_notes()
    return {
        "output_id": output["output_id"],
        "annotator_id": annotator_id,
        "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "question_id": output["question_id"],
        "arm": output["arm"],
        "groundedness": groundedness,
        "completeness": completeness,
        "numeric_fidelity": numeric_fidelity,
        "constraint_satisfaction": constraint_satisfaction,
        "overall_pass": overall_pass,
        "failure_tags": failure_tags,
        "notes": notes,
    }


def _print_summary(csv_path: Path) -> None:
    """Quick per-arm sanity preview; full numbers come from analyze_results.py."""
    if not csv_path.exists():
        return
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    if not rows:
        return
    by_arm: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_arm.setdefault(r["arm"], []).append(r)
    print("\nPer-arm preview:")
    for arm, group in sorted(by_arm.items()):
        scores = []
        n_with_tags = 0
        for r in group:
            try:
                composite = sum(
                    int(r[k]) for k in
                    ("groundedness", "completeness",
                     "numeric_fidelity", "constraint_satisfaction")
                ) / 4.0
                scores.append(composite)
            except (TypeError, ValueError):
                continue
            if r.get("failure_tags"):
                n_with_tags += 1
        mean_q = sum(scores) / len(scores) if scores else float("nan")
        print(f"  {arm}: n={len(group)}  mean_composite={mean_q:.2f}  "
              f"failure-tagged={n_with_tags}")


# ---------------------------------------------------------------------------
# Mode: relabel (intra-annotator reliability).
# ---------------------------------------------------------------------------
def run_relabel_mode(args: argparse.Namespace) -> int:
    """Re-label a sample of already-labelled outputs, for intra-annotator agreement."""
    run_dir = Path(args.run_dir)
    labels_path = run_dir / LABELS_FILENAME
    relabels_path = run_dir / RELABELS_FILENAME

    if not labels_path.exists():
        print(f"ERROR: {labels_path} not found — run label mode first.")
        return 1

    questions = load_questions(args.questions)
    arms = discover_arms(run_dir)
    if not arms:
        print(f"ERROR: no <arm>/results.jsonl found under {run_dir}")
        return 1

    outputs = collect_outputs(run_dir, arms, questions)
    by_id = {o["output_id"]: o for o in outputs}

    # Restrict to outputs that have a primary label (the join target).
    with labels_path.open(encoding="utf-8") as f:
        labelled_ids = {row["output_id"] for row in csv.DictReader(f)
                        if row.get("output_id")}
    # Sort by output_id so the stratified sampler is process-stable across
    # invocations. Python set iteration order is not stable across processes,
    # which would otherwise make the seeded sampler non-reproducible.
    candidates = sorted(
        (by_id[oid] for oid in labelled_ids if oid in by_id),
        key=lambda o: o["output_id"],
    )

    if not candidates:
        print("ERROR: no overlap between labelled outputs and run dir contents.")
        return 1

    selected = _stratified_sample(
        candidates, target_size=args.relabel_subset, seed=args.relabel_seed,
    )
    if not selected:
        print("ERROR: could not select any outputs for relabel.")
        return 1

    # Document which categories were picked.
    cat_counts: Dict[str, int] = {}
    for o in selected:
        cat = o.get("category") or "uncategorised"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    print(f"Relabelling {len(selected)} output(s); category mix: {cat_counts}")

    done = load_existing_label_ids(relabels_path)
    pending = [o for o in selected if o["output_id"] not in done]
    if not pending:
        print("All selected outputs already relabelled. Nothing to do.")
        _print_relabel_diagnostics(labels_path, relabels_path)
        return 0

    for i, output in enumerate(pending, start=1):
        print(render_output(i, len(pending), output))
        try:
            row = _prompt_rubric(output, annotator_id=args.annotator_id)
        except KeyboardInterrupt:
            print("\nInterrupted; partial input discarded.")
            return 130
        row["relabel_round"] = 2
        append_label_row(relabels_path, RELABEL_CSV_FIELDS, row)

    _print_relabel_diagnostics(labels_path, relabels_path)
    return 0


def _stratified_sample(candidates: List[Dict[str, Any]], target_size: int,
                       seed: int) -> List[Dict[str, Any]]:
    """Stratified random selection: take ``target_size`` outputs spread
    across categories. Uses a fixed seed so the choice is reproducible."""
    if target_size <= 0:
        return []
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for o in candidates:
        by_cat.setdefault(o.get("category") or "uncategorised", []).append(o)

    rng = random.Random(seed)
    for group in by_cat.values():
        rng.shuffle(group)

    cats = sorted(by_cat.keys())
    selected: List[Dict[str, Any]] = []
    cursor = {c: 0 for c in cats}

    # Round-robin across categories until we hit the target or run out.
    while len(selected) < target_size:
        progressed = False
        for cat in cats:
            if len(selected) >= target_size:
                break
            idx = cursor[cat]
            if idx < len(by_cat[cat]):
                selected.append(by_cat[cat][idx])
                cursor[cat] += 1
                progressed = True
        if not progressed:
            break
    return selected


def _print_relabel_diagnostics(labels_path: Path, relabels_path: Path) -> None:
    """Quick per-axis count of identical / off-by-1 / off-by-2 vs original.
    Full Cohen's kappa is left to ``analyze_results.py``."""
    if not relabels_path.exists():
        return
    with labels_path.open(encoding="utf-8") as f:
        original = {r["output_id"]: r for r in csv.DictReader(f)}
    with relabels_path.open(encoding="utf-8") as f:
        relabels = list(csv.DictReader(f))

    axes = ("groundedness", "completeness",
            "numeric_fidelity", "constraint_satisfaction")
    print("\nIntra-annotator preview (per axis: identical / |delta|>=1 / |delta|>=2):")
    for axis in axes:
        same = ge1 = ge2 = 0
        for r in relabels:
            oid = r["output_id"]
            if oid not in original:
                continue
            try:
                a = int(original[oid][axis])
                b = int(r[axis])
            except (TypeError, ValueError):
                continue
            d = abs(a - b)
            if d == 0:
                same += 1
            if d >= 1:
                ge1 += 1
            if d >= 2:
                ge2 += 1
        print(f"  {axis:>26}: identical={same}  |delta|>=1={ge1}  |delta|>=2={ge2}")


# ---------------------------------------------------------------------------
# CLI driver.
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """CLI: --mode label or relabel, over a run directory."""
    p = argparse.ArgumentParser(
        description="Single-annotator blinded labelling for SPARA EIL outputs.",
    )
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--annotator-id", required=True)
    p.add_argument("--mode", choices=("label", "relabel"), default="label")
    p.add_argument("--questions", type=Path,
                   default=Path("src/config/questions.json"),
                   help="Questions dataset to join question text + category.")
    p.add_argument("--shuffle-seed", type=int, default=42)
    p.add_argument("--limit", type=int, default=None,
                   help="Per-session cap (label mode only).")
    p.add_argument("--relabel-subset", type=int, default=8,
                   help="Number of outputs to relabel (relabel mode).")
    p.add_argument("--relabel-seed", type=int, default=7)
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point dispatching label / relabel mode."""
    args = parse_args(argv)
    if args.mode == "relabel":
        return run_relabel_mode(args)
    return run_label_mode(args)


if __name__ == "__main__":
    sys.exit(main())
