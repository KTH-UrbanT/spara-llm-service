"""Triage `must_include` gold labels against the frozen A_open evidence caches (v22 R2).

Read-only: no dataset edits, no re-scoring, no verdicts on which side is stale — that
decision goes back to a human with this list in hand. Per token it asks "is this string
reachable in the evidence the pipeline actually retrieved for this case?", and sorts every
case into the defect classes of `plan-eil-v22.md` R2:

  single-char    — a one-letter token; the substring check in `check_must_include` is
                   satisfied by any English sentence, so it carries no signal.
  no-evidence    — nothing cached for this case (registry/vector strata) — expected.
  record-choice  — numeric token present, but alongside >1 EPC version of the same field;
                   whether the answer cites the gold version is a lottery.
  version-slice  — token absent from this case's own retrieval but present (word-bounded)
                   in a sibling case's retrieval of the SAME expected_building_id: a
                   retrieval-consistency defect, not a label error.
  stale-gold     — token absent from every retrieval of that building, siblings included:
                   the gold value exists nowhere the pipeline can reach.
  indirect       — token absent from the evidence at string level, yet this case PASSED
                   `must_include` in the reference replicate: the answer derives it by
                   translation ("ElDirekt" → "electric"). The label works; no action.
  ok             — token present, single EPC version; the label does its job.

    python -m scripts.audit_must_include \
        --dataset artifacts/datasets/filtered_cases.jsonl \
        --run-dir artifacts/runs/2026-08-13_v21_r1/A_open \
        --out artifacts/runs/2026-08-13_v22_must_include_audit.md
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

# Worst class wins at case level; order matters for the triage summary.
SEVERITY = ("stale-gold", "version-slice", "record-choice", "indirect", "single-char",
            "no-evidence", "ok")


def _rows(agg: dict) -> list[dict]:
    return [r for v in (agg or {}).values() if isinstance(v, list)
            for r in v if isinstance(r, dict)]


def bounded(tok: str, text: str) -> bool:
    """Word-boundary presence: `155` must not count inside `155406` or `155.4`."""
    return re.search(rf"(?<![\w.]){re.escape(tok)}(?![\w.])", text) is not None


def _versions(rows: list[dict]) -> set[str]:
    """Distinct energy-performance values across the retrieved EPC records."""
    return {str(r[k]) for r in rows for k in ("energy_performance", "epc_egienergiprestanda")
            if r.get(k) is not None}


class _Caches:
    def __init__(self, cache_dir: Path):
        self.dir, self._mem = cache_dir, {}

    def get(self, case_id: str) -> tuple[str | None, list[dict]]:
        if case_id not in self._mem:
            p = self.dir / f"{case_id}.json"
            if not p.exists():
                self._mem[case_id] = (None, [])
            else:
                agg = json.loads(p.read_text(encoding="utf-8")).get("aggregated_data") or {}
                self._mem[case_id] = (json.dumps(agg, ensure_ascii=False).lower(), _rows(agg))
        return self._mem[case_id]


def audit(dataset: Path, run_dir: Path) -> list[dict]:
    cases = [json.loads(l) for l in dataset.read_text(encoding="utf-8").split("\n") if l.strip()]
    caches = _Caches(run_dir / "case_cache")
    tr = run_dir / "traces" / "per_case.jsonl"
    trace = {r["case_id"]: r
             for r in (json.loads(l) for l in tr.read_text(encoding="utf-8").split("\n") if l.strip())} \
        if tr.exists() else {}
    fc = {cid: r.get("field_coverage_pass") for cid, r in trace.items()}
    mi = {cid: r.get("must_include_pass") for cid, r in trace.items()}
    by_building: dict[str, list[str]] = {}
    for c in cases:
        if c.get("expected_building_id"):
            by_building.setdefault(c["expected_building_id"], []).append(c["case_id"])

    findings = []
    for c in cases:
        for tok in (c.get("must_include") or []):
            t = str(tok).lower()
            f = {"case_id": c["case_id"], "token": tok,
                 "building_id": c.get("expected_building_id"),
                 "field_coverage_pass": fc.get(c["case_id"])}
            text, rows = caches.get(c["case_id"])
            if len(t) == 1:
                f |= {"class": "single-char",
                      "detail": "satisfied by any sentence under the substring check"}
            elif text is None or not rows:
                f |= {"class": "no-evidence",
                      "detail": "no cached evidence for this case (registry/vector stratum)"}
            elif bounded(t, text):
                vs = _versions(rows)
                if t.isdigit() and len(vs) > 1:
                    f |= {"class": "record-choice",
                          "detail": f"present, but {len(vs)} EPC versions retrieved: "
                                    f"{sorted(vs)} — which one the answer cites is a lottery"}
                else:
                    f |= {"class": "ok", "detail": "present in evidence"}
            elif mi.get(c["case_id"]) is True:
                f |= {"class": "indirect",
                      "detail": "absent from evidence at string level, but the answer passed "
                                "the token in the reference replicate — translated/derived; "
                                "label OK"}
            else:
                substr = " (matches only inside a longer number)" if t in text else ""
                sibs = [s for s in by_building.get(c.get("expected_building_id"), [])
                        if s != c["case_id"]]
                hit = [s for s in sibs if (caches.get(s)[0] or "") and bounded(t, caches.get(s)[0])]
                if hit:
                    f |= {"class": "version-slice",
                          "detail": f"absent from own retrieval{substr}; present in sibling "
                                    f"{hit[0]} for the same building — different EPC slice"}
                else:
                    f |= {"class": "stale-gold",
                          "detail": f"absent from every retrieval of this building "
                                    f"({len(sibs)} sibling(s) checked){substr}"}
            findings.append(f)
    return findings


def report(findings: list[dict]) -> str:
    rank = {c: i for i, c in enumerate(SEVERITY)}
    worst: dict[str, dict] = {}
    for f in findings:
        if f["case_id"] not in worst or rank[f["class"]] < rank[worst[f["case_id"]]["class"]]:
            worst[f["case_id"]] = f
    md = ["# `must_include` audit (v22 R2) — triage list, no labels touched", "",
          f"{len(findings)} tokens over {len(worst)} cases.", "",
          "## Case-level triage (worst token class per case)", "",
          "| class | cases |", "|---|---|"]
    for cls in SEVERITY:
        ids = sorted(cid for cid, f in worst.items() if f["class"] == cls)
        if ids:
            md.append(f"| {cls} | {len(ids)}: {', '.join(ids)} |")
    md += ["", "## Every flagged token (`ok` omitted)", "",
           "| case | token | class | field_coverage_pass | detail |", "|---|---|---|---|---|"]
    for f in findings:
        if f["class"] != "ok":
            md.append(f"| {f['case_id']} | `{f['token']}` | {f['class']} "
                      f"| {f['field_coverage_pass']} | {f['detail']} |")
    return "\n".join(md)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", default="artifacts/datasets/filtered_cases.jsonl")
    p.add_argument("--run-dir", default="artifacts/runs/2026-08-13_v21_r1/A_open")
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    md = report(audit(Path(args.dataset), Path(args.run_dir)))
    print(md)
    if args.out:
        Path(args.out).write_text(md + "\n", encoding="utf-8")
        print(f"\nWrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
