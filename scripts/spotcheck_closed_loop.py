"""Blinded κ spot-check for the closed-loop judge.

Three sub-commands, each idempotent / resume-safe:

  sample   stratified-sample N cases from <run-dir>/A_full/per_case.jsonl into
           a labelling worklist; the worklist CSV is the single source of truth
           for what the annotator must label and for what the judge said.

  label    interactive annotator pass — for each unlabelled case in the worklist,
           shows the question + retrieved-evidence summary + final answer
           (HIDES the judge's verdict and axes), prompts for 4 axes [0..10] +
           binary overall_pass + free-text notes, appends to <labels-csv>.
           Resume-safe: re-runs skip already-labelled `case_id`s.

  kappa    join the labels CSV with the judge verdicts and compute Cohen's
           binary κ (overall_pass vs semantic_judge_pass) + weighted κ per
           closed-loop axis (faithfulness, answer_relevance, question_coverage,
           calibration), each with a percentile bootstrap CI (10k resamples).
           Writes `<out-json>` with the numbers.

The interactive `label` step asks the annotator to score each closed-loop axis
on 0..10 (matching the judge's scale), so weighted κ is well-defined.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, json, random, re
from pathlib import Path

CLOSED_LOOP_AXES = ["faithfulness", "answer_relevance", "question_coverage", "calibration"]

STRATA = {"generic": 4, "building_specific": 4, "combined": 4, "clarification": 3}  # total 15

WORKLIST_FIELDS = [
    "case_id", "expected_route", "question",
    "final_answer", "retrieved_evidence_summary",
    "judge_verdict", "judge_composite",
    "judge_faithfulness", "judge_answer_relevance",
    "judge_question_coverage", "judge_calibration",
    "judge_semantic_judge_pass",
]
LABEL_FIELDS = [
    "case_id", "annotator_id", "timestamp_utc",
    "faithfulness", "answer_relevance", "question_coverage", "calibration",
    "overall_pass", "notes",
]


def _evidence_summary(row: dict, max_chars: int = 600) -> str:
    """Compact one-line summary of what evidence the pipeline retrieved.

    Trace rows carry the JSON-encoded `final_sql_fields_used` list and the
    `final_vector_chunks_retrieved` list (best-effort metadata). We avoid the
    raw `aggregated_data` blob — that's not what the judge sees either.
    """
    sql_fields = row.get("final_sql_fields_used") or []
    vec = row.get("final_vector_chunks_retrieved") or []
    parts = []
    if sql_fields:
        parts.append(f"SQL fields used (n={len(sql_fields)}): "
                     f"{', '.join(sorted(set(sql_fields))[:12])}")
    if vec:
        parts.append(f"Vector chunks (n={len(vec)}): "
                     f"{', '.join(str((c.get('source') or '?'))[:40] for c in vec[:4])}")
    if not parts:
        parts.append("(no evidence retrieved)")
    s = " | ".join(parts)
    return s[:max_chars] + ("…" if len(s) > max_chars else "")


def _csv_safe(v):
    """csv.DictWriter calls str() on each value, which turns None into the literal
    string 'None' — that breaks downstream filters that expect an empty cell for
    missing data. Coerce None to '' explicitly before writing."""
    return "" if v is None else v


def cmd_sample(args):
    """Build the worklist CSV: one row per sampled case, with judge verdicts pre-filled."""
    trace = Path(args.run_dir) / args.arm / "traces" / "per_case.jsonl"
    rows = [json.loads(l) for l in trace.read_text(encoding="utf-8").split("\n") if l.strip()]
    dataset = {c["case_id"]: c for c in
               (json.loads(l) for l in args.dataset.read_text(encoding="utf-8").split("\n") if l.strip())}

    # Stratified sample.
    rng = random.Random(args.seed)
    by_stratum: dict[str, list] = {}
    for r in rows:
        by_stratum.setdefault(r.get("expected_route", "?"), []).append(r)
    chosen = []
    for stratum, n in STRATA.items():
        pool = by_stratum.get(stratum, [])
        if len(pool) < n:
            print(f"WARN: stratum {stratum!r} has only {len(pool)} rows (asked {n})")
            chosen.extend(pool)
        else:
            chosen.extend(rng.sample(pool, n))

    # Assemble worklist rows. All None-valued fields are coerced to '' so the
    # downstream cmd_kappa skip-filter correctly identifies fail-open rows by
    # an empty judge_faithfulness cell rather than the string 'None'.
    out = []
    for r in chosen:
        cid = r["case_id"]
        ds = dataset.get(cid) or {}
        v = r.get("answer_quality_verdict") or {}
        axes = (v.get("axes") or {})
        out.append({
            "case_id": cid,
            "expected_route": _csv_safe(r.get("expected_route")),
            "question": ds.get("question") or "(question not in dataset)",
            "final_answer": (r.get("final_answer") or "")[:2000],
            "retrieved_evidence_summary": _evidence_summary(r),
            "judge_verdict": _csv_safe(v.get("verdict")),
            "judge_composite": _csv_safe(v.get("composite")),
            "judge_faithfulness": _csv_safe(axes.get("faithfulness")),
            "judge_answer_relevance": _csv_safe(axes.get("answer_relevance")),
            "judge_question_coverage": _csv_safe(axes.get("question_coverage")),
            "judge_calibration": _csv_safe(axes.get("calibration")),
            "judge_semantic_judge_pass": _csv_safe(r.get("semantic_judge_pass")),
        })

    args.worklist.parent.mkdir(parents=True, exist_ok=True)
    with args.worklist.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=WORKLIST_FIELDS)
        w.writeheader()
        for r in out:
            w.writerow(r)
    print(f"Wrote {len(out)} worklist rows to {args.worklist} (seed={args.seed})")


def _ask_int(label: str, lo: int, hi: int) -> int:
    while True:
        s = input(f"  {label} [{lo}-{hi}]: ").strip()
        if s.isdigit() and lo <= int(s) <= hi:
            return int(s)
        print(f"    ! Enter an integer in [{lo}, {hi}].")


def cmd_label(args):
    """Interactive blinded labelling. Resume-safe via the labels CSV."""
    with args.worklist.open(encoding="utf-8") as f:
        worklist = list(csv.DictReader(f))
    done = set()
    if args.labels.exists():
        with args.labels.open(encoding="utf-8") as f:
            done = {row["case_id"] for row in csv.DictReader(f)}
    pending = [r for r in worklist if r["case_id"] not in done]
    if not pending:
        print(f"All {len(worklist)} cases already labelled in {args.labels}.")
        return

    args.labels.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.labels.exists()
    with args.labels.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_FIELDS)
        if write_header:
            w.writeheader()
        for i, r in enumerate(pending, start=1):
            print(f"\n[{i}/{len(pending)}] {r['case_id']} ({r['expected_route']})")
            print(f"  Q: {r['question']}")
            print(f"  Evidence: {r['retrieved_evidence_summary']}")
            print(f"  Answer:\n    {r['final_answer']}")
            print("  -- Now score 0 (worst) to 10 (perfect) on each axis --")
            try:
                row = {
                    "case_id": r["case_id"],
                    "annotator_id": args.annotator_id,
                    "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "faithfulness": _ask_int("faithfulness", 0, 10),
                    "answer_relevance": _ask_int("answer_relevance", 0, 10),
                    "question_coverage": _ask_int("question_coverage", 0, 10),
                    "calibration": _ask_int("calibration", 0, 10),
                    "overall_pass": _ask_int("overall_pass (0=no, 1=yes)", 0, 1),
                    "notes": input("  notes (optional): ").strip(),
                }
            except (KeyboardInterrupt, EOFError):
                print("\nInterrupted; the row in progress was discarded. Re-run to resume.")
                return
            w.writerow(row)
            f.flush()
    print(f"\nDone. Wrote labels for {len(pending)} cases to {args.labels}.")


def _kappa_binary(pairs: list[tuple[int, int]]) -> float:
    """Cohen's κ for binary labels (0/1). Returns 0.0 if denominator is 0."""
    if not pairs: return 0.0
    n = len(pairs)
    p_o = sum(1 for a, b in pairs if a == b) / n
    a_ones = sum(a for a, _ in pairs) / n
    b_ones = sum(b for _, b in pairs) / n
    p_e = a_ones * b_ones + (1 - a_ones) * (1 - b_ones)
    return 0.0 if (1 - p_e) == 0 else (p_o - p_e) / (1 - p_e)


def _kappa_weighted(pairs: list[tuple[int, int]], k: int = 11) -> float:
    """Quadratic-weighted κ for ordinal scores in [0, k-1]. (Closed-loop scale: k=11.)"""
    if not pairs: return 0.0
    n = len(pairs)
    obs = [[0] * k for _ in range(k)]
    for a, b in pairs:
        obs[a][b] += 1
    row_marg = [sum(obs[i]) for i in range(k)]
    col_marg = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    num = 0.0; den = 0.0
    for i in range(k):
        for j in range(k):
            w = ((i - j) ** 2) / ((k - 1) ** 2)
            num += w * obs[i][j]
            den += w * (row_marg[i] * col_marg[j] / n)
    return 0.0 if den == 0 else 1 - num / den


def _bootstrap_ci(values_fn, pairs: list, n_resamples: int, seed: int) -> tuple[float, float]:
    """Percentile 95% CI for any κ statistic given (pairs) -> float."""
    rng = random.Random(seed)
    n = len(pairs)
    samples = []
    for _ in range(n_resamples):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        samples.append(values_fn(sample))
    samples.sort()
    return samples[int(0.025 * n_resamples)], samples[int(0.975 * n_resamples) - 1]


def cmd_kappa(args):
    """Compute κ from a labels CSV joined with the worklist's judge fields."""
    with args.worklist.open(encoding="utf-8") as f:
        wl = {row["case_id"]: row for row in csv.DictReader(f)}
    with args.labels.open(encoding="utf-8") as f:
        labels = list(csv.DictReader(f))

    binary_pairs: list[tuple[int, int]] = []
    axis_pairs: dict[str, list[tuple[int, int]]] = {a: [] for a in CLOSED_LOOP_AXES}
    for L in labels:
        cid = L["case_id"]
        w = wl.get(cid)
        if w is None:
            continue
        # Skip rows where the judge was a fail-open (axes will be empty).
        if not w.get("judge_faithfulness"):
            continue
        binary_pairs.append((int(L["overall_pass"]),
                             1 if str(w["judge_semantic_judge_pass"]).lower() == "true" else 0))
        for ax in CLOSED_LOOP_AXES:
            axis_pairs[ax].append((int(L[ax]), int(w[f"judge_{ax}"])))

    out = {
        "n_paired": len(binary_pairs),
        "n_resamples": args.n_resamples,
        "seed": args.seed,
        "kappa_binary": _kappa_binary(binary_pairs),
        "kappa_binary_ci95": _bootstrap_ci(_kappa_binary, binary_pairs,
                                           args.n_resamples, args.seed),
        "per_axis_kappa_weighted": {},
    }
    for ax, pairs in axis_pairs.items():
        out["per_axis_kappa_weighted"][ax] = {
            "k": _kappa_weighted(pairs),
            "ci95": _bootstrap_ci(_kappa_weighted, pairs, args.n_resamples, args.seed),
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {args.out}")


# ---------------------------------------------------------------------------
# ingest-md — parse a filled markdown labelling form into a labels CSV.
# ---------------------------------------------------------------------------
# The blinded annotation is done in a markdown form (see labeling_form.md) whose
# per-case score block looks like:
#
#     ## Case 1 / 15 — `EKR_GEN_006`
#     ...
#     - faithfulness (0–10): `[10]`
#     - answer_relevance (0–10): `[9]`
#     - question_coverage (0–10): `[8]`
#     - calibration (0–10): `[7]`
#     - overall_pass (0 or 1): `[1]`
#     - notes: free text
#
# This bridges that form to the LABEL_FIELDS CSV the `kappa` sub-command reads,
# so the markdown and interactive (`label`) annotation paths are interchangeable.
_CASE_HEADING_RE = re.compile(r"^##\s+Case\s+\d+\s*/\s*\d+\b.*?`([^`]+)`", re.MULTILINE)
_SCORE_LINE_RE = re.compile(r"^-\s*([a-z_]+)\s*\([^)]*\)\s*:\s*`?\[\s*(\d*)\s*\]`?", re.MULTILINE)
_NOTES_LINE_RE = re.compile(r"^-\s*notes\s*:\s*(.*)$", re.MULTILINE)


def _parse_form(form_text: str) -> list[dict]:
    """Parse a filled markdown form into per-case score dicts.

    Each dict has: case_id, the 4 CLOSED_LOOP_AXES, overall_pass, notes. Score
    values are the raw captured strings ('' when the `[ ]` bracket is empty).
    Text before the first case heading (the instructions) is ignored.
    """
    matches = list(_CASE_HEADING_RE.finditer(form_text))
    blocks: list[dict] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(form_text)
        body = form_text[start:end]
        rec = {ax: "" for ax in CLOSED_LOOP_AXES}
        rec["overall_pass"] = ""
        for sm in _SCORE_LINE_RE.finditer(body):
            key, val = sm.group(1), sm.group(2)
            if key in rec:
                rec[key] = val
        nm = _NOTES_LINE_RE.search(body)
        rec["case_id"] = m.group(1)
        rec["notes"] = nm.group(1).strip() if nm else ""
        blocks.append(rec)
    return blocks


def _is_blank_label(rec: dict) -> bool:
    """A block is blank if every score field is empty (not yet filled in)."""
    return all(not str(rec.get(k, "")).strip()
               for k in (*CLOSED_LOOP_AXES, "overall_pass"))


def _validate_label(rec: dict) -> str | None:
    """Return an error string if the parsed scores are out of range, else None."""
    for ax in CLOSED_LOOP_AXES:
        v = str(rec.get(ax, "")).strip()
        if not v.isdigit() or not (0 <= int(v) <= 10):
            return f"{ax}={rec.get(ax)!r} not an integer in [0,10]"
    op = str(rec.get("overall_pass", "")).strip()
    if op not in ("0", "1"):
        return f"overall_pass={op!r} must be 0 or 1"
    return None


def cmd_ingest_md(args):
    """Parse a filled markdown labelling form → labels CSV (LABEL_FIELDS schema).

    Idempotent: case_ids already present in the labels CSV are skipped, so a
    partially-filled form can be re-ingested after more cases are completed.
    Blank (unfilled) blocks are skipped with a notice; out-of-range scores error.
    """
    blocks = _parse_form(args.form.read_text(encoding="utf-8"))
    if not blocks:
        print("ERROR: no '## Case N / M — `case_id`' blocks found in the form.")
        return

    done: set[str] = set()
    if args.labels.exists():
        with args.labels.open(encoding="utf-8") as f:
            done = {r["case_id"] for r in csv.DictReader(f) if r.get("case_id")}

    args.labels.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.labels.exists()
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    written = skipped_done = skipped_blank = errors = 0
    with args.labels.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_FIELDS)
        if write_header:
            w.writeheader()
        for rec in blocks:
            cid = rec["case_id"]
            if cid in done:
                skipped_done += 1
                continue
            if _is_blank_label(rec):
                skipped_blank += 1
                print(f"[skip] {cid}: scores blank — not yet filled in")
                continue
            err = _validate_label(rec)
            if err:
                errors += 1
                print(f"[skip] {cid}: {err}")
                continue
            w.writerow({
                "case_id": cid,
                "annotator_id": args.annotator_id,
                "timestamp_utc": ts,
                "faithfulness": int(rec["faithfulness"]),
                "answer_relevance": int(rec["answer_relevance"]),
                "question_coverage": int(rec["question_coverage"]),
                "calibration": int(rec["calibration"]),
                "overall_pass": int(rec["overall_pass"]),
                "notes": rec["notes"],
            })
            done.add(cid)
            written += 1
            print(f"[ok]   {cid}: overall_pass={rec['overall_pass']}")
    print(f"\nWrote {written}; already-done {skipped_done}; "
          f"blank {skipped_blank}; errors {errors}. → {args.labels}")


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="Build the worklist CSV.")
    s.add_argument("--run-dir", required=True, type=Path)
    s.add_argument("--arm", default="A_full")
    s.add_argument("--dataset", required=True, type=Path,
                   help="Path to artifacts/datasets/filtered_cases.jsonl.")
    s.add_argument("--worklist", required=True, type=Path)
    s.add_argument("--seed", type=int, default=2026)

    l = sub.add_parser("label", help="Interactive blinded annotation.")
    l.add_argument("--worklist", required=True, type=Path)
    l.add_argument("--labels", required=True, type=Path)
    l.add_argument("--annotator-id", required=True)

    k = sub.add_parser("kappa", help="Compute κ from labels + judge verdicts.")
    k.add_argument("--worklist", required=True, type=Path)
    k.add_argument("--labels", required=True, type=Path)
    k.add_argument("--out", required=True, type=Path)
    k.add_argument("--n-resamples", type=int, default=10000)
    k.add_argument("--seed", type=int, default=42)

    im = sub.add_parser("ingest-md",
                        help="Parse a filled markdown labelling form → labels CSV.")
    im.add_argument("--form", required=True, type=Path)
    im.add_argument("--labels", required=True, type=Path)
    im.add_argument("--annotator-id", required=True)

    args = p.parse_args(argv)
    {"sample": cmd_sample, "label": cmd_label, "kappa": cmd_kappa,
     "ingest-md": cmd_ingest_md}[args.cmd](args)


if __name__ == "__main__":
    main()
