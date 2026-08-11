"""Filter and validate the evaluation cases for the closed-loop experiment.

Usage:
    python scripts/validate_dataset_closed_loop.py \
        --dataset src/evaluation/closed_loop/evaluation_cases.jsonl \
        --out-jsonl artifacts/datasets/filtered_cases.jsonl \
        --out-manifest artifacts/datasets/manifest.csv
"""
from __future__ import annotations
import argparse, csv, json, sys
from pathlib import Path

EXCLUDED_CASE_TYPES = {
    "expert_handoff", "report_generation", "out_of_scope",
    "multi_turn_clarification", "multi_turn_building_specific",
    "multi_turn_generic_to_building_specific",
    "multi_turn_building_specific_followup",
    "multi_turn_ambiguous_address_resolution",
    "multi_turn_expert_handoff",
}
VALID_ROUTES = {
    "generic", "building_specific", "combined", "clarification", "conversational"
}
VALID_AGENTS = {"GenericAgent", "BuildingAgent", "ConversationalistAgent"}

# Expected post-filter counts. Update to the actual values (and commit) if they differ.
EXPECTED_COUNTS = {
    "generic": 38, "building_specific": 34, "combined": 11,
    "clarification": 5, "conversational": 0,
}
EXPECTED_TOTAL = 88


def filter_cases(cases: list[dict]) -> list[dict]:
    """Keep single-turn, in-scope cases with a supported route and agent."""
    result = []
    for c in cases:
        if "turns" in c: continue
        if c.get("case_type") in EXCLUDED_CASE_TYPES: continue
        if c.get("expected_route") not in VALID_ROUTES: continue
        agent = c.get("expected_agent")
        if agent is not None and agent not in VALID_AGENTS: continue
        result.append(c)
    return result


def write_manifest(filtered: list[dict], out_jsonl: Path, out_manifest: Path) -> None:
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for c in filtered:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    fieldnames = ["case_id", "case_type", "expected_route", "expected_agent",
                  "expected_building_id", "expected_fields", "source"]
    with open(out_manifest, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for c in filtered:
            w.writerow({k: c.get(k, "") for k in fieldnames})


def validate_counts(filtered: list[dict]) -> list[str]:
    errors = []
    by_route: dict[str, int] = {}
    for c in filtered:
        r = c.get("expected_route", "unknown")
        by_route[r] = by_route.get(r, 0) + 1
    if len(filtered) != EXPECTED_TOTAL:
        errors.append(f"Expected {EXPECTED_TOTAL} total, got {len(filtered)}")
    for route, n in EXPECTED_COUNTS.items():
        if by_route.get(route, 0) != n:
            errors.append(f"Route {route!r}: expected {n}, got {by_route.get(route, 0)}")
    return errors


def check_field_mapping_completeness(filtered: list[dict]) -> list[str]:
    """Every expected_fields value must be mapped to an ODEN/Postgres column.

    Imported lazily so this validator can run before deterministic_checks exists;
    returns [] (with a notice) until the map is available, then one error per
    unmapped field thereafter.
    """
    try:
        from src.evaluation.closed_loop.deterministic_checks import FIELD_NAME_MAP
    except Exception:
        print("NOTE: FIELD_NAME_MAP not importable yet — implement the checks module "
              "and re-run to enforce field-mapping completeness.")
        return []
    unmapped = sorted({f for c in filtered for f in (c.get("expected_fields") or [])
                       if f not in FIELD_NAME_MAP})
    return [f"expected_fields value not mapped: {f!r}" for f in unmapped]


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--out-jsonl", default="artifacts/datasets/filtered_cases.jsonl")
    p.add_argument("--out-manifest", default="artifacts/datasets/manifest.csv")
    p.add_argument("--strict", action="store_true")
    return p.parse_args(argv)


def run(args) -> int:
    path = Path(args.dataset)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr); return 1
    with open(path, encoding="utf-8") as f:
        cases = [json.loads(l) for l in f if l.strip()]
    print(f"Loaded {len(cases)} cases")
    filtered = filter_cases(cases)
    print(f"After filtering: {len(filtered)} cases")
    by_route: dict[str, int] = {}
    for c in filtered:
        r = c.get("expected_route", "unknown")
        by_route[r] = by_route.get(r, 0) + 1
    for r, n in sorted(by_route.items()):
        print(f"  {r}: {n}")
    errors = validate_counts(filtered) + check_field_mapping_completeness(filtered)
    for e in errors:
        print(f"WARNING: {e}")
    if errors and args.strict:
        return 1
    write_manifest(filtered, Path(args.out_jsonl), Path(args.out_manifest))
    print(f"Written: {args.out_jsonl}\nWritten: {args.out_manifest}")
    return 0


def main(argv=None):
    sys.exit(run(parse_args(argv)))

if __name__ == "__main__":
    main()
