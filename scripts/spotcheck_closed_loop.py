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
import argparse, csv, datetime as dt, hashlib, json, random, re
from pathlib import Path

# The v1 rubric's axes. Kept only as the fallback for inputs too old or too empty to
# derive from; every code path prefers the axes actually present in the data, because
# plan-eil-v24 replaced `question_coverage` with `entity_consistency` and both vintages
# of artifact must stay readable (the frozen v22/R4 worklists are v1).
CLOSED_LOOP_AXES = ["faithfulness", "answer_relevance", "question_coverage", "calibration"]

# `judge_*` worklist columns that are NOT axes — excluded when deriving the axis list
# back out of a worklist header.
_NON_AXIS_JUDGE_FIELDS = {"verdict", "composite", "semantic_judge_pass",
                          "fail_open", "evidence_present"}

STRATA = {"generic": 4, "building_specific": 4, "combined": 4, "clarification": 3}  # total 15


def _axes_from_traces(rows: list[dict]) -> list[str]:
    """Axis names as the judge actually emitted them, in first-seen order."""
    seen: list[str] = []
    for r in rows:
        for k in _axes(r):
            if k not in seen and not k.startswith("_"):
                seen.append(k)
    return seen or list(CLOSED_LOOP_AXES)


def _axes_from_worklist(fieldnames) -> list[str]:
    """Axis names recovered from a worklist header (`judge_<axis>` columns).

    This is what makes the tool vintage-agnostic: a v1 worklist yields
    `question_coverage`, a v2 worklist yields `entity_consistency`, and `kappa` /
    `ingest-md` follow whichever they are handed instead of a hardcoded list.
    """
    axes = [f[len("judge_"):] for f in (fieldnames or [])
            if f.startswith("judge_") and f[len("judge_"):] not in _NON_AXIS_JUDGE_FIELDS]
    return axes or list(CLOSED_LOOP_AXES)


def _worklist_fields(axes: list[str]) -> list[str]:
    return [
        # `row_id` first: the same case_id recurs in up to 9 arm-runs of one study, so
        # case_id alone cannot key a worklist drawn from more than one of them.
        "row_id", "case_id", "run_id", "arm", "expected_route", "question",
        "final_answer", "retrieved_evidence_summary",
        # The system's own identity resolution. The annotator MUST see this: the
        # entity_consistency axis is defined (v24 §C2) against the system's resolution,
        # so a human denied it is as blind as the v1 judge was. It is not gold.
        "final_building_id_resolved",
        "judge_verdict", "judge_composite",
        *[f"judge_{a}" for a in axes],
        "judge_semantic_judge_pass",
        # The fail-open marker, recorded explicitly. A blank `judge_faithfulness` used to
        # stand in for it, but after Fix 4 that cell is legitimately blank on every
        # evidence-absent case (61 of 88), so the old filter silently discarded most of
        # the agreement pool.
        "judge_fail_open", "judge_evidence_present",
    ]


def _label_fields(axes: list[str]) -> list[str]:
    return ["row_id", "case_id", "annotator_id", "timestamp_utc",
            *axes, "overall_pass", "notes"]


def _row_id(row: dict) -> str:
    """Stable join/resume key: `<case_id>@<run_id>:<arm>`."""
    return f"{row.get('case_id')}@{row.get('run_id')}:{row.get('arm')}"


def _key(row: dict) -> str:
    """Join key for a worklist/labels row, tolerating pre-v25 files with no row_id."""
    return (row.get("row_id") or "").strip() or row["case_id"]


def _opaque(key: str) -> str:
    """Blind label for a form heading.

    A `row_id` names its arm, and an annotator who can see `A_full` knows the loop may
    already have rewritten that answer — which is exactly the judgement being measured.
    The same device as `label_outputs.py`'s `output_id`: hash it, and resolve back
    through the worklist at ingest time.
    """
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


AUDIT_FIELDS = [
    "case_id", "question", "final_answer",
    # Filled in by the annotator; both blank means "not yet labelled".
    "contains_building_specific_claim", "fabricated_fact_suspected", "notes",
]
FABRICATION_GATE = 2   # pre-registered: >2 of 20 invalidates Fix 4's faithfulness-null policy


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


NOT_JUDGED = ("_fail_open", "_short_circuit", "_no_answer")


def _axes(row: dict) -> dict:
    return ((row.get("answer_quality_verdict") or {}).get("axes") or {})


def _was_judged(row: dict) -> bool:
    """True when the judge actually scored this answer.

    Short-circuits (clarification / address request) have their `semantic_judge_pass`
    derived from gold rather than judged, and a fail-open produced no scores at all —
    neither belongs in an agreement pool that is supposed to measure the judge.
    """
    ax = _axes(row)
    return bool(ax) and not any(ax.get(m) for m in NOT_JUDGED)


def _load_trace(run_dir, arm: str) -> list[dict]:
    trace = Path(run_dir) / arm / "traces" / "per_case.jsonl"
    rows = [json.loads(l) for l in trace.read_text(encoding="utf-8").split("\n") if l.strip()]
    for r in rows:
        # Trace rows already carry run_id/arm; setdefault keeps hand-made fixtures working.
        r.setdefault("run_id", Path(run_dir).name)
        r.setdefault("arm", arm)
        r["row_id"] = _row_id(r)
    return rows


def _parse_source(spec: str) -> tuple:
    """`--source path/to/run_dir:ARM` → (run_dir, arm)."""
    run_dir, sep, arm = spec.rpartition(":")
    if not sep or not run_dir or not arm:
        raise SystemExit(f"--source must look like RUN_DIR:ARM, got {spec!r}")
    return Path(run_dir), arm


def _load_rows(args) -> list[dict]:
    """Rows from one `--run-dir/--arm`, or from every repeated `--source RUN_DIR:ARM`."""
    if getattr(args, "source", None):
        rows: list[dict] = []
        for spec in args.source:
            rows.extend(_load_trace(*_parse_source(spec)))
        return rows
    return _load_trace(args.run_dir, args.arm)


def _read_include(path) -> list[str]:
    """One `row_id` per line; blank lines and `#` comments ignored."""
    if path is None:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _pick(rows, chosen_ids, pred, n, rng, label):
    """Seeded draw of n rows matching pred, excluding anything already chosen.

    The pool is sorted by row_id first so the draw depends only on the seed, never on
    the order the trace files happened to be read in.
    """
    pool = sorted((r for r in rows
                   if r["row_id"] not in chosen_ids and _was_judged(r) and pred(r)),
                  key=lambda r: r["row_id"])
    if len(pool) < n:
        print(f"WARN: {label} pool has only {len(pool)} rows (asked {n})")
        n = len(pool)
    picked = rng.sample(pool, n)
    chosen_ids.update(r["row_id"] for r in picked)
    print(f"{label}: {len(picked)} of {len(pool)} eligible")
    return picked


def cmd_sample(args):
    """Build the worklist CSV: one row per sampled case, with judge verdicts pre-filled."""
    if bool(args.source) == bool(args.run_dir):
        raise SystemExit("pass exactly one of --run-dir (single arm) or --source (repeatable)")
    rows = _load_rows(args)
    axes = _axes_from_traces(rows)
    dataset = {c["case_id"]: c for c in
               (json.loads(l) for l in args.dataset.read_text(encoding="utf-8").split("\n") if l.strip())}

    rng = random.Random(args.seed)
    if args.include_rows or args.pad_null or args.pad_scored:
        # v25 frame: explicit mandatory rows + seeded pads. Every mandatory row is named
        # in the declaration, so the sample is auditable rather than merely reproducible.
        by_id = {r["row_id"]: r for r in rows}
        chosen, chosen_ids = [], set()
        missing = []
        for rid in _read_include(args.include_rows):
            if rid in chosen_ids:
                continue
            r = by_id.get(rid)
            if r is None:
                missing.append(rid)
            else:
                chosen.append(r)
                chosen_ids.add(rid)
        if missing:
            raise SystemExit("mandatory rows absent from the sampling frame:\n  "
                             + "\n  ".join(missing))
        print(f"include: {len(chosen)} mandatory rows")
        ax = args.null_axis
        if args.pad_null:
            chosen += _pick(rows, chosen_ids,
                            lambda r: _axes(r).get(ax, "absent") is None,
                            args.pad_null, rng, f"pad-null[{ax}]")
        if args.pad_scored:
            chosen += _pick(
                rows, chosen_ids,
                lambda r: _axes(r).get(ax) is not None
                and ((r.get("answer_quality_verdict") or {}).get("verdict") == "pass"),
                args.pad_scored, rng, f"pad-scored[{ax}, judge-pass]")
        # Randomise labelling order so position cannot leak the stratum to the annotator.
        rng.shuffle(chosen)
    elif args.all_judged:
        # The whole genuinely-judged pool, not a stratified sample of 15. At n≈67 the
        # agreement estimate stops being dominated by sampling noise, and the gates in
        # plan-eil-v21 §2.4 are stated against this denominator.
        chosen = [r for r in rows if _was_judged(r)]
        print(f"--all-judged: {len(chosen)} of {len(rows)} rows were genuinely judged")
    else:
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
        row_axes = (v.get("axes") or {})
        out.append({
            "row_id": r["row_id"],
            "case_id": cid,
            "run_id": _csv_safe(r.get("run_id")),
            "arm": _csv_safe(r.get("arm")),
            "expected_route": _csv_safe(r.get("expected_route")),
            "question": ds.get("question") or "(question not in dataset)",
            "final_answer": r.get("final_answer") or "",
            "retrieved_evidence_summary": _evidence_summary(r),
            "final_building_id_resolved": _csv_safe(r.get("final_building_id_resolved")),
            "judge_verdict": _csv_safe(v.get("verdict")),
            "judge_composite": _csv_safe(v.get("composite")),
            **{f"judge_{a}": _csv_safe(row_axes.get(a)) for a in axes},
            "judge_semantic_judge_pass": _csv_safe(r.get("semantic_judge_pass")),
            "judge_fail_open": bool(row_axes.get("_fail_open")),
            "judge_evidence_present": _csv_safe(r.get("evidence_present")),
        })

    args.worklist.parent.mkdir(parents=True, exist_ok=True)
    with args.worklist.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_worklist_fields(axes))
        w.writeheader()
        for r in out:
            w.writerow(r)
    print(f"Wrote {len(out)} worklist rows to {args.worklist} "
          f"(seed={args.seed}, axes={','.join(axes)})")


def _ask_int(label: str, lo: int, hi: int) -> int:
    while True:
        s = input(f"  {label} [{lo}-{hi}]: ").strip()
        if s.isdigit() and lo <= int(s) <= hi:
            return int(s)
        print(f"    ! Enter an integer in [{lo}, {hi}].")


def cmd_label(args):
    """Interactive blinded labelling. Resume-safe via the labels CSV."""
    with args.worklist.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        worklist = list(reader)
        axes = _axes_from_worklist(reader.fieldnames)
    done = set()
    if args.labels.exists():
        with args.labels.open(encoding="utf-8") as f:
            done = {_key(row) for row in csv.DictReader(f)}
    pending = [r for r in worklist if _key(r) not in done]
    if not pending:
        print(f"All {len(worklist)} rows already labelled in {args.labels}.")
        return

    args.labels.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.labels.exists()
    with args.labels.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_label_fields(axes))
        if write_header:
            w.writeheader()
        for i, r in enumerate(pending, start=1):
            # The heading deliberately omits run_id/arm as well as every judge column:
            # knowing which arm produced an answer would tell the annotator whether the
            # loop had already rewritten it.
            print(f"\n[{i}/{len(pending)}] {r['case_id']} ({r.get('expected_route', '?')})")
            print(f"  Q: {r['question']}")
            print(f"  Evidence: {r['retrieved_evidence_summary']}")
            if r.get("final_building_id_resolved"):
                print(f"  Building the system identified: {r['final_building_id_resolved']}")
            print(f"  Answer:\n    {r['final_answer']}")
            print("  -- Now score 0 (worst) to 10 (perfect) on each axis --")
            try:
                row = {
                    "row_id": _key(r),
                    "case_id": r["case_id"],
                    "annotator_id": args.annotator_id,
                    "timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                    **{ax: _ask_int(ax, 0, 10) for ax in axes},
                    "overall_pass": _ask_int("overall_pass (0=no, 1=yes)", 0, 1),
                    "notes": input("  notes (optional): ").strip(),
                }
            except (KeyboardInterrupt, EOFError):
                print("\nInterrupted; the row in progress was discarded. Re-run to resume.")
                return
            w.writerow(row)
            f.flush()
    print(f"\nDone. Wrote labels for {len(pending)} rows to {args.labels}.")


_FORM_PREAMBLE = """# Blinded labelling form — plan-eil-v25

Score every block, then ingest with:

    python scripts/spotcheck_closed_loop.py ingest-md \\
        --form {form} --labels {labels} \\
        --worklist {worklist} --annotator-id <you>

**Rules.** Judge only what is shown. Each block's id is opaque on purpose: it hides
which arm produced the answer, so knowing the loop had a second attempt cannot colour
the score. The building id given is the one *the system resolved* — it is not the gold
answer, and it is what `entity_consistency` is defined against: does every figure the
answer presents as the user's own belong to that building? Leave a score blank to skip
a block; re-ingesting is idempotent.

Scale 0 (worst) – 10 (perfect). `overall_pass` is 0 or 1: would you ship this answer?

---
"""


def cmd_form(args):
    """Emit a markdown labelling form from a worklist (the R4-style annotation path).

    Headings carry `row_id`, not `case_id`: this frame samples the same case from up to
    nine arm-runs (one case appears 7 times), and a case_id-keyed form would silently
    collapse them into one block.
    """
    with args.worklist.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        axes = _axes_from_worklist(reader.fieldnames)

    out = [_FORM_PREAMBLE.format(form=args.form, labels=args.labels,
                                 worklist=args.worklist)]
    for i, r in enumerate(rows, start=1):
        out.append(f"\n## Case {i} / {len(rows)} — `{_opaque(_key(r))}`\n")
        out.append(f"**Expected route:** `{r.get('expected_route', '?')}`\n")
        if r.get("final_building_id_resolved"):
            out.append("**Building the system identified:** "
                       f"`{r['final_building_id_resolved']}`\n")
        out.append(f"\n### Question\n\n> {r['question']}\n")
        out.append(f"\n### Retrieved evidence (summary)\n\n```\n"
                   f"{r['retrieved_evidence_summary']}\n```\n")
        out.append(f"\n### Model's final answer\n\n```\n{r['final_answer']}\n```\n")
        out.append("\n### Your scores\n\n")
        for ax in axes:
            out.append(f"- {ax} (0–10): `[ ]`\n")
        out.append("- overall_pass (0 or 1): `[ ]`\n- notes:\n\n---\n")

    args.form.parent.mkdir(parents=True, exist_ok=True)
    args.form.write_text("".join(out), encoding="utf-8")
    print(f"Wrote {len(rows)} blocks to {args.form} (axes={','.join(axes)})")


def _kappa_binary(pairs: list[tuple[int, int]]) -> float:
    """Cohen's κ for binary labels (0/1). Returns 0.0 if denominator is 0."""
    if not pairs: return 0.0
    n = len(pairs)
    p_o = sum(1 for a, b in pairs if a == b) / n
    a_ones = sum(a for a, _ in pairs) / n
    b_ones = sum(b for _, b in pairs) / n
    p_e = a_ones * b_ones + (1 - a_ones) * (1 - b_ones)
    return 0.0 if (1 - p_e) == 0 else (p_o - p_e) / (1 - p_e)


def _raw_agreement(pairs: list[tuple[int, int]]) -> float:
    return (sum(1 for a, b in pairs if a == b) / len(pairs)) if pairs else 0.0


def _ac1(pairs: list[tuple[int, int]]) -> float:
    """Gwet's AC1 for binary labels.

    Cohen's κ is unstable at extreme prevalence: the judge passes ~95% of what it scores,
    where 95% raw agreement can still yield κ ≈ 0 (the prevalence paradox). AC1 replaces
    κ's chance-agreement term with one that does not blow up as prevalence goes to 1, so
    it is the statistic v21 gates on; κ is reported beside it, with the caveat.

        p_e = (1/(Q-1)) * Σ_k π_k (1 - π_k),  Q = 2 categories,
        π_k = the mean of the two raters' marginals for category k.
    """
    if not pairs: return 0.0
    n = len(pairs)
    pi1 = sum(a + b for a, b in pairs) / (2 * n)     # mean marginal for class 1
    p_e = 2 * pi1 * (1 - pi1)                        # Q=2 collapses the sum to this
    p_o = _raw_agreement(pairs)
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
    if n == 0:
        # Reachable now that faithfulness pairs are skipped when the axis is null: an
        # all-evidence-absent sample leaves that axis with nothing to resample.
        return (0.0, 0.0)
    samples = []
    for _ in range(n_resamples):
        sample = [pairs[rng.randrange(n)] for _ in range(n)]
        samples.append(values_fn(sample))
    samples.sort()
    return samples[int(0.025 * n_resamples)], samples[int(0.975 * n_resamples) - 1]


AXIS_FLOOR = 4  # TAU_AXIS_FLOOR: at/above = the axis did not fire, below = it fired.


def cmd_kappa(args):
    """Compute κ from a labels CSV joined with the worklist's judge fields."""
    with args.worklist.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        wl = {_key(row): row for row in reader}
        axis_names = _axes_from_worklist(reader.fieldnames)
    with args.labels.open(encoding="utf-8") as f:
        labels = list(csv.DictReader(f))

    binary_pairs: list[tuple[int, int]] = []
    axis_pairs: dict[str, list[tuple[int, int]]] = {a: [] for a in axis_names}
    n_dropped = 0
    for L in labels:
        w = wl.get(_key(L))
        if w is None:
            continue
        # Drop fail-opens — and ONLY fail-opens. This used to test `judge_faithfulness`,
        # which after Fix 4 is legitimately blank on every evidence-absent case, so most of
        # the judged pool was being thrown away (v21 §1.8). Older worklists have no
        # `judge_fail_open` column; fall back to the blank-faithfulness test for those.
        if "judge_fail_open" in w:
            fail_open = str(w.get("judge_fail_open", "")).strip().lower() in ("true", "1")
        else:
            fail_open = not w.get("judge_faithfulness")
        if fail_open:
            n_dropped += 1
            continue
        binary_pairs.append((int(L["overall_pass"]),
                             1 if str(w["judge_semantic_judge_pass"]).lower() == "true" else 0))
        for ax in axis_names:
            # An axis is null when it was not applicable — no evidence to check against
            # (faithfulness), or no identified building (entity_consistency). There is no
            # judge score to pair with, so the pair is dropped rather than read as a 0.
            if str(w.get(f"judge_{ax}", "")).strip() == "":
                continue
            if str(L.get(ax, "")).strip() == "":
                continue
            axis_pairs[ax].append((int(L[ax]), int(w[f"judge_{ax}"])))

    agreement = _raw_agreement(binary_pairs)
    ac1 = _ac1(binary_pairs)
    out = {
        "n_paired": len(binary_pairs),
        "n_dropped_fail_open": n_dropped,
        "n_resamples": args.n_resamples,
        "seed": args.seed,
        "raw_agreement": agreement,
        "ac1": ac1,
        "ac1_ci95": _bootstrap_ci(_ac1, binary_pairs, args.n_resamples, args.seed),
        # Reported, NOT gated: at the judge's ~95% pass prevalence κ is unstable even under
        # near-perfect agreement, so gating on it would be a statistical error (v21 §2.4).
        "kappa_binary": _kappa_binary(binary_pairs),
        "kappa_binary_ci95": _bootstrap_ci(_kappa_binary, binary_pairs,
                                           args.n_resamples, args.seed),
        "kappa_caveat": "prevalence paradox — reported for comparability, not a gate",
        "gates": {"raw_agreement_ge_0.90": agreement >= 0.90, "ac1_ge_0.6": ac1 >= 0.6},
        "per_axis_kappa_weighted": {},
    }
    for ax, pairs in axis_pairs.items():
        k = _kappa_weighted(pairs)
        ci = _bootstrap_ci(_kappa_weighted, pairs, args.n_resamples, args.seed)
        # v25 V2-fb: at 225 scored rows with ~18 fires the axis marginal is skewed, the
        # regime where weighted κ collapses while agreement stays high. Binarise at the
        # floor the loop itself uses (fired / did not fire) and report AC1 beside κ, the
        # same treatment v21/R4 gave the binary verdict.
        binary = [(1 if a >= AXIS_FLOOR else 0, 1 if b >= AXIS_FLOOR else 0) for a, b in pairs]
        out["per_axis_kappa_weighted"][ax] = {
            "n": len(pairs),
            "k": k,
            "ci95": ci,
            "k_gt0_ci_excludes_0": bool(k > 0 and ci[0] > 0),
            "binarised_at_floor": {
                "floor": AXIS_FLOOR,
                "raw_agreement": _raw_agreement(binary),
                "ac1": _ac1(binary),
                "ac1_ci95": _bootstrap_ci(_ac1, binary, args.n_resamples, args.seed),
                "n_judge_fired": sum(1 for _, b in binary if b == 0),
                "n_human_fired": sum(1 for a, _ in binary if a == 0),
            },
        }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {args.out}")


# ---------------------------------------------------------------------------
# audit-ungrounded — the only instrument that can see the failure mode Fix 4 created.
# ---------------------------------------------------------------------------
# With `faithfulness` marked not-applicable on the evidence-absent cases, nothing grounds
# those answers: a fabricated Swedish regulation scores answer_relevance 10 and passes.
# This samples them, hides every judge field, and asks a human two questions.

def cmd_audit_ungrounded(args):
    """Sample evidence-absent answers for a fabrication audit; tally once filled.

    Idempotent by design: the first run writes the sheet, later runs read it back and
    report the counts, preserving whatever the annotator has filled in so far.
    """
    if args.sheet.exists():
        with args.sheet.open(encoding="utf-8") as f:
            sheet = list(csv.DictReader(f))
        _t = lambda k: sum(1 for r in sheet
                           if str(r.get(k, "")).strip().lower() in ("1", "true", "yes", "y"))
        labelled = [r for r in sheet
                    if str(r.get("contains_building_specific_claim", "")).strip()
                    or str(r.get("fabricated_fact_suspected", "")).strip()]
        fab = _t("fabricated_fact_suspected")
        result = {"sheet": str(args.sheet), "n_rows": len(sheet), "n_labelled": len(labelled),
                  "contains_building_specific_claim": _t("contains_building_specific_claim"),
                  "fabricated_fact_suspected": fab,
                  "gate": f"fabrication <= {FABRICATION_GATE}",
                  "gate_pass": fab <= FABRICATION_GATE,
                  "complete": len(labelled) == len(sheet)}
        print(json.dumps(result, indent=2))
        if not result["complete"]:
            print(f"\n{len(sheet) - len(labelled)} row(s) still blank — "
                  f"the gate verdict is provisional until every row is labelled.")
        return

    rows = _load_rows(args)
    ungrounded = [r for r in rows if r.get("evidence_present") is False and r.get("final_answer")]
    if not ungrounded:
        print("No evidence-absent answers found — nothing to audit.")
        return
    dataset = {c["case_id"]: c for c in
               (json.loads(l) for l in args.dataset.read_text(encoding="utf-8").split("\n") if l.strip())}
    rng = random.Random(args.seed)
    chosen = ungrounded if len(ungrounded) <= args.n else rng.sample(ungrounded, args.n)

    args.sheet.parent.mkdir(parents=True, exist_ok=True)
    with args.sheet.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
        w.writeheader()
        for r in sorted(chosen, key=lambda x: x["case_id"]):
            w.writerow({"case_id": r["case_id"],
                        "question": (dataset.get(r["case_id"]) or {}).get("question")
                                    or "(question not in dataset)",
                        "final_answer": r.get("final_answer") or "",
                        "contains_building_specific_claim": "",
                        "fabricated_fact_suspected": "", "notes": ""})
    print(f"Wrote {len(chosen)} of {len(ungrounded)} evidence-absent rows to {args.sheet} "
          f"(seed={args.seed}).\nMark each row 0/1, then re-run this command to tally.")


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

    Axis names come from the form's own score lines, so a v1 form yields
    `question_coverage` and a v2 form yields `entity_consistency` with no flag to set.
    Score values are the raw captured strings ('' when the `[ ]` bracket is empty).
    Text before the first case heading (the instructions) is ignored.
    """
    matches = list(_CASE_HEADING_RE.finditer(form_text))
    blocks: list[dict] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(form_text)
        body = form_text[start:end]
        rec = {"overall_pass": ""}
        for sm in _SCORE_LINE_RE.finditer(body):
            rec[sm.group(1)] = sm.group(2)
        nm = _NOTES_LINE_RE.search(body)
        # The heading carries a row_id (`case@run:arm`) in v25 forms and a bare case_id
        # in the frozen v1 forms. Keep both so either vintage ingests correctly.
        heading = m.group(1)
        rec["heading"] = heading
        rec["row_id"] = heading if "@" in heading else ""
        rec["case_id"] = heading.split("@", 1)[0]
        rec["notes"] = nm.group(1).strip() if nm else ""
        blocks.append(rec)
    return blocks


def _form_axes(blocks: list[dict]) -> list[str]:
    """Axis names seen across parsed blocks, in first-seen order."""
    skip = {"heading", "row_id", "case_id", "notes", "overall_pass"}
    seen: list[str] = []
    for rec in blocks:
        for k in rec:
            if k not in skip and k not in seen:
                seen.append(k)
    return seen or list(CLOSED_LOOP_AXES)


def _is_blank_label(rec: dict, axes: list[str]) -> bool:
    """A block is blank if every score field is empty (not yet filled in)."""
    return all(not str(rec.get(k, "")).strip() for k in (*axes, "overall_pass"))


def _validate_label(rec: dict, axes: list[str]) -> str | None:
    """Return an error string if the parsed scores are out of range, else None."""
    for ax in axes:
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
        print("ERROR: no '## Case N / M — `id`' blocks found in the form.")
        return
    axes = _form_axes(blocks)

    # Resolve opaque form headings back to their row. Without the worklist an opaque
    # heading cannot be attributed to anything, so this errors rather than guessing.
    if getattr(args, "worklist", None):
        with args.worklist.open(encoding="utf-8") as f:
            wl = {_opaque(_key(r)): r for r in csv.DictReader(f)}
        unresolved = []
        for rec in blocks:
            w = wl.get(rec["heading"])
            if w is not None:
                rec["row_id"], rec["case_id"] = _key(w), w["case_id"]
            elif not rec["row_id"]:
                unresolved.append(rec["heading"])
        if unresolved:
            raise SystemExit("form headings not found in the worklist: "
                             + ", ".join(unresolved[:5])
                             + (f" (+{len(unresolved) - 5} more)" if len(unresolved) > 5 else ""))
    elif any(len(b["heading"]) == 8 and b["heading"].isalnum() and not b["row_id"]
             for b in blocks):
        raise SystemExit("this form uses opaque ids — pass --worklist to resolve them")

    done: set[str] = set()
    if args.labels.exists():
        with args.labels.open(encoding="utf-8") as f:
            done = {_key(r) for r in csv.DictReader(f) if r.get("case_id")}

    args.labels.parent.mkdir(parents=True, exist_ok=True)
    write_header = not args.labels.exists()
    ts = dt.datetime.now(dt.timezone.utc).isoformat()
    written = skipped_done = skipped_blank = errors = 0
    with args.labels.open("a", newline="", encoding="utf-8") as f:
        # Markdown forms carry no run/arm, so `row_id` stays empty and `_key` falls back
        # to case_id. That is correct for the single-arm forms this path was built for;
        # a multi-arm frame must use the interactive `label` path instead.
        w = csv.DictWriter(f, fieldnames=_label_fields(axes))
        if write_header:
            w.writeheader()
        for rec in blocks:
            cid, key = rec["case_id"], _key(rec)
            if key in done:
                skipped_done += 1
                continue
            if _is_blank_label(rec, axes):
                skipped_blank += 1
                print(f"[skip] {key}: scores blank — not yet filled in")
                continue
            err = _validate_label(rec, axes)
            if err:
                errors += 1
                print(f"[skip] {key}: {err}")
                continue
            w.writerow({
                "row_id": rec["row_id"],
                "case_id": cid,
                "annotator_id": args.annotator_id,
                "timestamp_utc": ts,
                **{ax: int(rec[ax]) for ax in axes},
                "overall_pass": int(rec["overall_pass"]),
                "notes": rec["notes"],
            })
            done.add(key)
            written += 1
            print(f"[ok]   {key}: overall_pass={rec['overall_pass']}")
    print(f"\nWrote {written}; already-done {skipped_done}; "
          f"blank {skipped_blank}; errors {errors}. → {args.labels}")


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="Build the worklist CSV.")
    s.add_argument("--run-dir", type=Path,
                   help="Single-source frame. Mutually exclusive with --source.")
    s.add_argument("--arm", default="A_full")
    s.add_argument("--source", action="append", metavar="RUN_DIR:ARM",
                   help="Repeatable multi-arm frame, e.g. "
                        "--source artifacts/runs/2026-08-15_v24_r1:A_open")
    s.add_argument("--dataset", required=True, type=Path,
                   help="Path to artifacts/datasets/filtered_cases.jsonl.")
    s.add_argument("--worklist", required=True, type=Path)
    s.add_argument("--seed", type=int, default=2026)
    s.add_argument("--all-judged", action="store_true",
                   help="Take every genuinely-judged row instead of the 15-case stratified "
                        "sample. This is the denominator v21's agreement gates are stated on.")
    s.add_argument("--include-rows", type=Path,
                   help="File of mandatory row_ids (`CASE@RUN:ARM`), one per line, "
                        "'#' comments allowed. Errors if any row is not in the frame.")
    s.add_argument("--pad-null", type=int, default=0,
                   help="Seeded pad of rows where --null-axis is null (not applicable).")
    s.add_argument("--pad-scored", type=int, default=0,
                   help="Seeded pad of judge-pass rows where --null-axis is scored.")
    s.add_argument("--null-axis", default="entity_consistency",
                   help="Axis whose null/scored split defines the pad strata.")

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

    fm = sub.add_parser("form", help="Emit a markdown labelling form from a worklist.")
    fm.add_argument("--worklist", required=True, type=Path)
    fm.add_argument("--form", required=True, type=Path)
    fm.add_argument("--labels", type=Path, default=Path("labels.csv"),
                    help="Only used to print the ingest command in the preamble.")

    im = sub.add_parser("ingest-md",
                        help="Parse a filled markdown labelling form → labels CSV.")
    im.add_argument("--form", required=True, type=Path)
    im.add_argument("--labels", required=True, type=Path)
    im.add_argument("--worklist", type=Path,
                    help="Required for forms with opaque headings; resolves them to rows.")
    im.add_argument("--annotator-id", required=True)

    au = sub.add_parser("audit-ungrounded",
                        help="Sample evidence-absent answers for a fabrication audit; "
                             "re-run once filled to tally against the gate.")
    au.add_argument("--run-dir", required=True, type=Path)
    au.add_argument("--arm", default="A_open")
    au.add_argument("--dataset", required=True, type=Path)
    au.add_argument("--sheet", required=True, type=Path)
    au.add_argument("-n", type=int, default=20)
    au.add_argument("--seed", type=int, default=2026)

    args = p.parse_args(argv)
    {"sample": cmd_sample, "label": cmd_label, "kappa": cmd_kappa, "form": cmd_form,
     "ingest-md": cmd_ingest_md, "audit-ungrounded": cmd_audit_ungrounded}[args.cmd](args)


if __name__ == "__main__":
    main()
