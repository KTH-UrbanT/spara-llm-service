"""Per-stratum faithfulness analysis on a closed-loop run's A_open trace.

Groups by `expected_route` and by case_id source-prefix, prints per-axis stats,
and dumps the K worst-faithfulness cases verbatim for qualitative inspection.
"""
from __future__ import annotations
import argparse, json, statistics
from pathlib import Path

AXES = ["faithfulness", "answer_relevance", "question_coverage", "calibration"]


# Bookkeeping markers that mean "the judge never scored this case": a crashed judge, a
# clarification/address short-circuit, or an empty answer. Rows carrying any of them must
# be excluded from axis means rather than contributing four zeros.
_NOT_JUDGED = ("_fail_open", "_short_circuit", "_no_answer")


def _axes_of(row: dict) -> dict | None:
    """Return the judge's axis scores, or None if this case was never actually judged."""
    v = row.get("answer_quality_verdict") or {}
    axes = v.get("axes") or {}
    if not axes or any(axes.get(m) for m in _NOT_JUDGED):
        return None
    return axes


def _composite_of(row: dict) -> float | None:
    v = row.get("answer_quality_verdict") or {}
    if any((v.get("axes") or {}).get(m) for m in _NOT_JUDGED):
        return None
    c = v.get("composite")
    return float(c) if c is not None else None


def _source_prefix(case_id: str) -> str:
    """Best-effort grouping by the dataset's case-id naming scheme."""
    parts = case_id.split("_")
    if len(parts) >= 2 and parts[0] + "_" + parts[1] in {"DEMO_EXTRA", "BRF_ODEN", "BRF_FACT"}:
        return f"{parts[0]}_{parts[1]}"
    return parts[0] if parts else "OTHER"


def _row_mean(rows: list[dict], axis: str) -> float | None:
    """Mean of one axis. A None score means "not applicable" — faithfulness on a case with
    no evidence — and is excluded, not read as 0; averaging in a not-applicable as a zero
    is the exact error the axis rule exists to prevent. An axis the judge omitted is 0."""
    vals = []
    for r in rows:
        ax = _axes_of(r)
        if ax is not None and ax.get(axis, 0) is not None:
            vals.append(ax.get(axis, 0))
    return round(statistics.mean(vals), 2) if vals else None


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", required=True, type=Path)
    p.add_argument("--arm", default="A_open")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--worst-k", type=int, default=5)
    args = p.parse_args(argv)

    f = args.run_dir / args.arm / "traces" / "per_case.jsonl"
    rows = [json.loads(l) for l in f.read_text(encoding="utf-8").split("\n") if l.strip()]

    md = [f"# Faithfulness drill-down — `{args.run_dir}` / `{args.arm}`",
          "",
          f"Total rows: {len(rows)}  ·  Rows with non-fail-open judge verdicts: "
          f"{sum(1 for r in rows if _axes_of(r) is not None)}",
          ""]

    # --- Per expected_route ---
    by_route: dict[str, list] = {}
    for r in rows:
        by_route.setdefault(r.get("expected_route", "?"), []).append(r)
    md += ["## Per `expected_route`", "",
           "| route | n | faith mean | answer_rel mean | q_cov mean | calib mean | composite mean |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for route, group in sorted(by_route.items()):
        comps = [c for c in (_composite_of(r) for r in group) if c is not None]
        md.append(
            f"| {route} | {len(group)} | "
            f"{_row_mean(group, 'faithfulness')} | "
            f"{_row_mean(group, 'answer_relevance')} | "
            f"{_row_mean(group, 'question_coverage')} | "
            f"{_row_mean(group, 'calibration')} | "
            f"{round(statistics.mean(comps), 2) if comps else 'n/a'} |")

    # --- Per source-prefix within each route ---
    md += ["", "## Per source-prefix within `expected_route`", "",
           "| route | source | n | faith mean | composite mean |",
           "|---|---|---:|---:|---:|"]
    for route, group in sorted(by_route.items()):
        by_src: dict[str, list] = {}
        for r in group:
            by_src.setdefault(_source_prefix(r["case_id"]), []).append(r)
        for src, sub in sorted(by_src.items()):
            comps = [c for c in (_composite_of(r) for r in sub) if c is not None]
            md.append(
                f"| {route} | {src} | {len(sub)} | "
                f"{_row_mean(sub, 'faithfulness')} | "
                f"{round(statistics.mean(comps), 2) if comps else 'n/a'} |")

    # --- Worst K faithfulness cases (verbatim) ---
    # Cases where faithfulness was not applicable (no evidence) have nothing to rank and
    # are skipped; ranking them as 0 would fill this section with correct answers.
    scored = []
    for r in rows:
        ax = _axes_of(r)
        if ax is not None and ax.get("faithfulness", 10) is not None:
            scored.append((ax.get("faithfulness", 10), r))
    scored.sort(key=lambda x: x[0])
    md += ["", f"## Worst {args.worst_k} faithfulness cases (verbatim)"]
    for score, r in scored[:args.worst_k]:
        md += ["",
               f"### `{r['case_id']}` — faithfulness = {score}",
               f"- **expected_route**: {r.get('expected_route')}",
               f"- **final_route_taken**: {r.get('final_route_taken')}",
               f"- **final_answer**: {(r.get('final_answer') or '')[:600]}",
               f"- **sql_fields_used** (first 8): "
               f"{(r.get('final_sql_fields_used') or [])[:8]}",
               f"- **judge axes**: {(r.get('answer_quality_verdict') or {}).get('axes')}",
               f"- **judge composite**: {(r.get('answer_quality_verdict') or {}).get('composite')}"]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
