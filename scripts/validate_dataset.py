#!/usr/bin/env python3
"""Validate the gold-question dataset (`questions.json`) and its smoke subset.

The dataset is the single biggest input to the experiment, and a structural
mistake here cascades through every later step: the runner produces wrong
question_ids, the evaluator gets confused, and the analysis joins fail.
This script enforces every structural invariant the analysis pipeline assumes.

Eight checks (each prints PASS/FAIL with detail):
  1. JSON well-formed and has the required top-level keys.
  2. Each record has all required fields.
  3. Field types are correct (lists, dicts, ISO 8601 dates).
  4. question_id values are unique and match Q\\d{3}.
  5. Category counts match the experimental design (10/6/4 = 20 under Option 3).
  6. Difficulty distribution sanity (≥2 of each easy/medium/hard).
  7. (Skipped with --no-db) Every record's hint_address resolves in the live DB.
  8. (Skipped without --smoke) The smoke subset is a strict subset of the
     full dataset with byte-identical content.

Usage (run from inside `llm-service/`):
    cd llm-service
    python -m scripts.validate_dataset \\
        --dataset src/config/questions.json \\
        --smoke   src/config/questions_smoke.json \\
        [--no-db]

Exit code: 0 if every check passes, 1 if any check fails.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# === Schema constants (single source of truth, mirror plan-eil-v1.md §1.C) ===
REQUIRED_FIELDS = {
    "question_id", "category", "difficulty", "question",
    "expected_facts", "expected_constraints", "must_avoid",
    "notes", "created_at",
}
# Optional fields that may or may not be present per record.
OPTIONAL_FIELDS = {"hint_address", "expected_numeric"}

VALID_CATEGORIES = {
    "simple_address",
    "numeric_aggregation",
    "constraint_filtering",
    "vector_sql_hybrid",
    "comparative",
    "edge_case",
}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}

# Fixed by the experimental design.
# Rebalanced 2026-05-10 (plan-eil-v3.md):
#   Option 2 dropped numeric_aggregation + comparative (intent classifier routes
#     DeSO-scoped questions to Specialized SQL, single_filter capped at 50 rows).
#   Option 3 dropped vector_sql_hybrid after Phase 1 dry-run revealed two pre-existing
#     routing bugs in building_flow_graph.py: (1) understand_context_node does not
#     populate intent_list from intents → compound 'SQL database ; vector database'
#     only fires the vector agent via loose-match; (2) vector_db_agent_node writes
#     results to agent_outputs_vector (text) but never to agent_vector_sources /
#     agent_vector_snippets, so vector hits never enter aggregated_data and the
#     evaluator scores groundedness=0 across all hybrid questions.
# Final Option 3 split: 10 simple_address + 6 edge_case + 4 constraint_filtering = 20.
EXPECTED_CATEGORY_COUNTS = {
    "simple_address": 10,
    "numeric_aggregation": 0,
    "constraint_filtering": 4,
    "vector_sql_hybrid": 0,
    "comparative": 0,
    "edge_case": 6,
}
EXPECTED_TOTAL = sum(EXPECTED_CATEGORY_COUNTS.values())  # 20
QUESTION_ID_PATTERN = re.compile(r"^Q\d{3}$")

# Edge-case records that intentionally exercise the missing-data path.
# Their `notes` field must contain one of these markers so check 7 lets them through.
EDGE_CASE_BYPASS_MARKERS = ("missing_data", "unknown_address")


# === Result type for each check ===
CheckResult = Tuple[bool, List[str]]   # (passed, list_of_error_strings)


def _fail(msg: str) -> CheckResult:
    return False, [msg]


def _ok() -> CheckResult:
    return True, []


# ============================================================================
# Check 1 — JSON well-formed + required top-level keys.
# ============================================================================
def check_top_level(data: Any) -> CheckResult:
    if not isinstance(data, dict):
        return _fail("dataset top-level is not a JSON object")
    errors: List[str] = []
    for required_key in ("dataset_version", "questions"):
        if required_key not in data:
            errors.append(f"missing top-level key {required_key!r}")
    if "questions" in data and not isinstance(data["questions"], list):
        errors.append("'questions' must be a list")
    return (not errors), errors


# ============================================================================
# Check 2 — Per-record required fields.
# ============================================================================
def check_required_fields(records: List[Dict[str, Any]]) -> CheckResult:
    errors: List[str] = []
    for rec in records:
        qid = rec.get("question_id", "<unknown>")
        missing = REQUIRED_FIELDS - rec.keys()
        if missing:
            errors.append(f"{qid}: missing required field(s): {sorted(missing)}")
        unknown = (rec.keys() - REQUIRED_FIELDS) - OPTIONAL_FIELDS
        if unknown:
            errors.append(f"{qid}: unknown field(s): {sorted(unknown)}")
    return (not errors), errors


# ============================================================================
# Check 3 — Field types.
# ============================================================================
def _is_iso_8601_utc(s: str) -> bool:
    """Cheap ISO 8601 check. Accepts ...Z or ...+00:00 trailers."""
    if not isinstance(s, str):
        return False
    candidate = s.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return True


def check_field_types(records: List[Dict[str, Any]]) -> CheckResult:
    errors: List[str] = []
    for rec in records:
        qid = rec.get("question_id", "<unknown>")

        if "category" in rec and rec["category"] not in VALID_CATEGORIES:
            errors.append(f"{qid}: category {rec['category']!r} not in "
                          f"{sorted(VALID_CATEGORIES)}")

        if "difficulty" in rec and rec["difficulty"] not in VALID_DIFFICULTIES:
            errors.append(f"{qid}: difficulty {rec['difficulty']!r} not in "
                          f"{sorted(VALID_DIFFICULTIES)}")

        if "question" in rec and not (isinstance(rec["question"], str) and rec["question"].strip()):
            errors.append(f"{qid}: 'question' must be a non-empty string")

        for list_field in ("expected_facts", "expected_constraints", "must_avoid"):
            if list_field in rec:
                value = rec[list_field]
                if not isinstance(value, list):
                    errors.append(f"{qid}: {list_field} must be a list")
                elif not all(isinstance(item, str) for item in value):
                    errors.append(f"{qid}: {list_field} elements must all be strings")

        if "expected_numeric" in rec:
            if not isinstance(rec["expected_numeric"], dict):
                errors.append(f"{qid}: expected_numeric must be a dict")
            else:
                for key, val in rec["expected_numeric"].items():
                    if not isinstance(val, (int, float)):
                        errors.append(f"{qid}: expected_numeric[{key!r}] must be numeric, "
                                      f"got {type(val).__name__}")

        if "notes" in rec and not isinstance(rec["notes"], str):
            errors.append(f"{qid}: notes must be a string")

        if "created_at" in rec and not _is_iso_8601_utc(rec["created_at"]):
            errors.append(f"{qid}: created_at {rec.get('created_at')!r} is not ISO 8601")

        if "hint_address" in rec and rec["hint_address"] is not None:
            if not isinstance(rec["hint_address"], str):
                errors.append(f"{qid}: hint_address must be a string or null")

    return (not errors), errors


# ============================================================================
# Check 4 — question_id uniqueness and pattern.
# ============================================================================
def check_question_ids(records: List[Dict[str, Any]]) -> CheckResult:
    errors: List[str] = []
    seen: Dict[str, int] = {}
    for rec in records:
        qid = rec.get("question_id")
        if not isinstance(qid, str):
            errors.append(f"question_id must be a string, got {type(qid).__name__}")
            continue
        if not QUESTION_ID_PATTERN.match(qid):
            errors.append(f"question_id {qid!r} does not match pattern Q\\d{{3}} (e.g. Q001)")
        seen[qid] = seen.get(qid, 0) + 1
    duplicates = sorted(qid for qid, n in seen.items() if n > 1)
    if duplicates:
        errors.append(f"duplicate question_id(s): {duplicates}")
    return (not errors), errors


# ============================================================================
# Check 5 — Category counts match the experimental design.
# ============================================================================
def check_category_counts(records: List[Dict[str, Any]]) -> CheckResult:
    errors: List[str] = []
    counts: Dict[str, int] = {}
    for rec in records:
        cat = rec.get("category")
        if isinstance(cat, str):
            counts[cat] = counts.get(cat, 0) + 1

    for cat, expected in EXPECTED_CATEGORY_COUNTS.items():
        actual = counts.get(cat, 0)
        if actual != expected:
            errors.append(f"category {cat!r}: expected {expected} questions, found {actual}")

    if len(records) != EXPECTED_TOTAL:
        errors.append(f"total records: expected {EXPECTED_TOTAL}, found {len(records)}")

    return (not errors), errors


# ============================================================================
# Check 6 — Difficulty distribution sanity.
# ============================================================================
def check_difficulty_distribution(records: List[Dict[str, Any]]) -> CheckResult:
    errors: List[str] = []
    counts: Dict[str, int] = {}
    for rec in records:
        diff = rec.get("difficulty")
        if isinstance(diff, str):
            counts[diff] = counts.get(diff, 0) + 1

    for diff in VALID_DIFFICULTIES:
        if counts.get(diff, 0) < 2:
            errors.append(f"difficulty {diff!r}: only {counts.get(diff, 0)} record(s); "
                          f"need at least 2 for distributional sanity")
    return (not errors), errors


# ============================================================================
# Check 7 — DB resolvability of hint_address (skipped with --no-db).
# ============================================================================
def check_db_resolvability(records: List[Dict[str, Any]]) -> CheckResult:
    """Confirm every hint_address resolves to ≥1 row in the live ODEN DB.

    edge_case records whose `notes` flag missing-data intent skip this check —
    they intentionally use addresses that don't resolve.

    This check imports SQLClient lazily so the no-db path doesn't trigger
    HTTP setup or fail when running outside the production environment.
    """
    try:
        from src.database.sql_client import SQLClient
    except Exception as e:
        return _fail(f"could not import SQLClient (configuration issue?): {e}")

    try:
        sql = SQLClient()
    except Exception as e:
        return _fail(f"could not instantiate SQLClient: {e}")

    errors: List[str] = []
    for rec in records:
        qid = rec.get("question_id", "<unknown>")
        addr = rec.get("hint_address")
        if not addr:
            continue   # No hint_address → nothing to resolve.

        notes = rec.get("notes", "") or ""
        if rec.get("category") == "edge_case" and any(
            marker in notes.lower() for marker in EDGE_CASE_BYPASS_MARKERS
        ):
            # Intentional missing-address case; skip.
            continue

        try:
            rows = sql.building_by_address(addr) or []
        except Exception as e:
            errors.append(f"{qid}: SQL lookup failed for {addr!r}: {e}")
            continue

        if not rows:
            errors.append(f"{qid}: hint_address {addr!r} did not resolve to any building")
    return (not errors), errors


# ============================================================================
# Check 8 — Smoke is a strict subset.
# ============================================================================
def check_smoke_subset(full_records: List[Dict[str, Any]],
                      smoke_records: List[Dict[str, Any]]) -> CheckResult:
    errors: List[str] = []
    full_by_id = {r["question_id"]: r for r in full_records if isinstance(r.get("question_id"), str)}

    for s in smoke_records:
        qid = s.get("question_id")
        if not isinstance(qid, str):
            errors.append("smoke record missing valid question_id")
            continue
        if qid not in full_by_id:
            errors.append(f"smoke contains {qid} but it is not in the full dataset")
            continue
        if s != full_by_id[qid]:
            errors.append(f"{qid}: smoke record content differs from full-dataset record "
                          "(must be byte-identical)")
    return (not errors), errors


# ============================================================================
# Driver
# ============================================================================
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate the SPARA gold dataset.")
    p.add_argument("--dataset", required=True, type=Path,
                   help="Path to questions.json (or any candidate dataset file).")
    p.add_argument("--smoke", type=Path, default=None,
                   help="Optional path to the smoke-subset file. When set, "
                        "checks that smoke is a strict subset of dataset.")
    p.add_argument("--no-db", action="store_true",
                   help="Skip the live-DB resolvability check (use in CI without "
                        "Azure / SQL credentials).")
    return p.parse_args(argv)


def _load_json_file(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _print_check(name: str, result: CheckResult) -> bool:
    """Print PASS/FAIL line + indented error details. Returns the boolean."""
    passed, errors = result
    label = "PASS" if passed else "FAIL"
    print(f"[{label}] {name}")
    for err in errors:
        print(f"      ✗ {err}")
    return passed


def run(args: argparse.Namespace) -> int:
    """Run all checks. Returns 0 on full PASS, 1 if any check FAILed."""
    print(f"Validating dataset: {args.dataset}")
    if args.smoke:
        print(f"Smoke subset:       {args.smoke}")
    if args.no_db:
        print("DB resolvability:   SKIPPED (--no-db)")
    print()

    # === Load the dataset ===
    try:
        data = _load_json_file(args.dataset)
    except FileNotFoundError:
        print(f"[FAIL] dataset file not found: {args.dataset}")
        return 1
    except json.JSONDecodeError as e:
        print(f"[FAIL] dataset is not valid JSON: {e}")
        return 1

    all_passed = True

    # === Check 1: top-level structure ===
    all_passed &= _print_check("1. JSON well-formed + top-level keys",
                                check_top_level(data))

    if not isinstance(data, dict) or "questions" not in data \
            or not isinstance(data["questions"], list):
        # Hard prerequisite for every later check; abort early.
        print("\nFAILED: cannot proceed without a valid 'questions' list.")
        return 1
    records: List[Dict[str, Any]] = data["questions"]

    # === Checks 2–6 (per-record / aggregate) ===
    all_passed &= _print_check("2. Per-record required fields",
                                check_required_fields(records))
    all_passed &= _print_check("3. Field types",
                                check_field_types(records))
    all_passed &= _print_check("4. question_id uniqueness + pattern",
                                check_question_ids(records))
    all_passed &= _print_check("5. Category counts (10/0/4/0/0/6 = 20, Option 3)",
                                check_category_counts(records))
    all_passed &= _print_check("6. Difficulty distribution sanity",
                                check_difficulty_distribution(records))

    # === Check 7 (DB) ===
    if args.no_db:
        print("[SKIP] 7. hint_address DB resolvability (--no-db)")
    else:
        all_passed &= _print_check("7. hint_address DB resolvability",
                                    check_db_resolvability(records))

    # === Check 8 (smoke) ===
    if args.smoke is None:
        print("[SKIP] 8. smoke subset (no --smoke argument)")
    else:
        try:
            smoke_data = _load_json_file(args.smoke)
            smoke_records = smoke_data.get("questions", [])
            if not isinstance(smoke_records, list):
                all_passed &= _print_check(
                    "8. smoke strict-subset",
                    _fail("smoke file 'questions' is not a list"),
                )
            else:
                all_passed &= _print_check(
                    "8. smoke strict-subset",
                    check_smoke_subset(records, smoke_records),
                )
        except FileNotFoundError:
            all_passed &= _print_check(
                "8. smoke strict-subset",
                _fail(f"smoke file not found: {args.smoke}"),
            )
        except json.JSONDecodeError as e:
            all_passed &= _print_check(
                "8. smoke strict-subset",
                _fail(f"smoke file is not valid JSON: {e}"),
            )

    # === Final summary ===
    n_total = len(records)
    print()
    if all_passed:
        print(f"{n_total}/{n_total} records OK")
        return 0
    else:
        print(f"VALIDATION FAILED — see [FAIL] lines above ({n_total} records inspected)")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
