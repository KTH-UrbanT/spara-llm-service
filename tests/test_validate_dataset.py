"""Tests for `scripts/validate_dataset.py`.

These tests run with --no-db (no live DB needed). The DB-resolvability check
is exercised in a separate path that mocks `SQLClient.building_by_address`,
so the test suite stays self-contained.

Each check function (check_top_level, check_required_fields, ...) is tested
with both a passing fixture and a deliberately-broken fixture to confirm:
  - it returns (True, []) on valid input
  - it returns (False, [error_messages]) on invalid input
  - the error message is descriptive enough for a student to fix
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from scripts import validate_dataset as vd


# === Helpers: build a fully-valid 40-record dataset for positive tests ===

def _make_valid_record(qid: str, category: str, difficulty: str = "medium",
                      hint_address: str = None) -> Dict[str, Any]:
    return {
        "question_id": qid,
        "category": category,
        "difficulty": difficulty,
        "question": f"Test question {qid}?",
        "hint_address": hint_address,
        "expected_facts": [f"fact for {qid}"],
        "expected_constraints": [],
        "must_avoid": [],
        "notes": "test fixture",
        "created_at": "2026-05-07T00:00:00Z",
    }


def _make_full_dataset() -> Dict[str, Any]:
    """Build a structurally-valid 40-record dataset with the right category counts."""
    records: List[Dict[str, Any]] = []
    qid_counter = 0
    for category, count in vd.EXPECTED_CATEGORY_COUNTS.items():
        for _ in range(count):
            qid_counter += 1
            qid = f"Q{qid_counter:03d}"
            # rotate difficulty to ensure each tier has ≥2 records
            diff_options = ["easy", "medium", "hard"]
            difficulty = diff_options[qid_counter % 3]
            records.append(_make_valid_record(qid, category, difficulty))
    return {
        "dataset_version": "1.0",
        "created_at": "2026-05-07T00:00:00Z",
        "description": "test fixture",
        "questions": records,
    }


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ============================================================================
# check_top_level
# ============================================================================
class TestCheckTopLevel:
    def test_valid_dataset_passes(self):
        ok, errs = vd.check_top_level(_make_full_dataset())
        assert ok and errs == []

    def test_top_level_not_dict_fails(self):
        ok, errs = vd.check_top_level([])
        assert not ok and "not a JSON object" in errs[0]

    def test_missing_dataset_version_fails(self):
        data = _make_full_dataset()
        del data["dataset_version"]
        ok, errs = vd.check_top_level(data)
        assert not ok
        assert any("dataset_version" in e for e in errs)

    def test_missing_questions_fails(self):
        data = _make_full_dataset()
        del data["questions"]
        ok, errs = vd.check_top_level(data)
        assert not ok
        assert any("questions" in e for e in errs)

    def test_questions_not_list_fails(self):
        data = _make_full_dataset()
        data["questions"] = {"not": "a list"}
        ok, errs = vd.check_top_level(data)
        assert not ok
        assert any("must be a list" in e for e in errs)


# ============================================================================
# check_required_fields
# ============================================================================
class TestCheckRequiredFields:
    def test_valid_records_pass(self):
        records = _make_full_dataset()["questions"]
        ok, errs = vd.check_required_fields(records)
        assert ok, f"expected pass, got errors: {errs}"

    def test_missing_required_field_fails(self):
        records = _make_full_dataset()["questions"]
        del records[0]["expected_facts"]
        ok, errs = vd.check_required_fields(records)
        assert not ok
        assert any("expected_facts" in e for e in errs)

    def test_unknown_field_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["surprise_field"] = "oops"
        ok, errs = vd.check_required_fields(records)
        assert not ok
        assert any("surprise_field" in e for e in errs)


# ============================================================================
# check_field_types
# ============================================================================
class TestCheckFieldTypes:
    def test_valid_types_pass(self):
        records = _make_full_dataset()["questions"]
        ok, errs = vd.check_field_types(records)
        assert ok, f"expected pass, got errors: {errs}"

    def test_invalid_category_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["category"] = "made_up_category"
        ok, errs = vd.check_field_types(records)
        assert not ok
        assert any("made_up_category" in e for e in errs)

    def test_invalid_difficulty_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["difficulty"] = "extreme"
        ok, errs = vd.check_field_types(records)
        assert not ok
        assert any("extreme" in e for e in errs)

    def test_empty_question_string_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["question"] = "   "  # whitespace only
        ok, errs = vd.check_field_types(records)
        assert not ok
        assert any("non-empty string" in e for e in errs)

    def test_expected_facts_must_be_list_of_strings(self):
        records = _make_full_dataset()["questions"]
        records[0]["expected_facts"] = "not a list"
        ok, errs = vd.check_field_types(records)
        assert not ok
        assert any("expected_facts must be a list" in e for e in errs)

    def test_expected_facts_with_non_string_element_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["expected_facts"] = ["fine string", 42]
        ok, errs = vd.check_field_types(records)
        assert not ok
        assert any("must all be strings" in e for e in errs)

    def test_expected_numeric_must_be_dict(self):
        records = _make_full_dataset()["questions"]
        records[0]["expected_numeric"] = "not a dict"
        ok, errs = vd.check_field_types(records)
        assert not ok
        assert any("expected_numeric must be a dict" in e for e in errs)

    def test_expected_numeric_value_must_be_numeric(self):
        records = _make_full_dataset()["questions"]
        records[0]["expected_numeric"] = {"count": "five"}
        ok, errs = vd.check_field_types(records)
        assert not ok
        assert any("must be numeric" in e for e in errs)

    def test_expected_numeric_accepts_int_and_float(self):
        records = _make_full_dataset()["questions"]
        records[0]["expected_numeric"] = {"count": 5, "ratio": 0.42}
        ok, errs = vd.check_field_types(records)
        assert ok, f"expected pass, got errors: {errs}"

    def test_invalid_created_at_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["created_at"] = "May 7th 2026"
        ok, errs = vd.check_field_types(records)
        assert not ok
        assert any("ISO 8601" in e for e in errs)

    def test_iso_with_z_suffix_accepted(self):
        records = _make_full_dataset()["questions"]
        records[0]["created_at"] = "2026-05-07T00:00:00Z"
        ok, errs = vd.check_field_types(records)
        assert ok

    def test_iso_with_offset_accepted(self):
        records = _make_full_dataset()["questions"]
        records[0]["created_at"] = "2026-05-07T00:00:00+00:00"
        ok, errs = vd.check_field_types(records)
        assert ok


# ============================================================================
# check_question_ids
# ============================================================================
class TestCheckQuestionIds:
    def test_valid_ids_pass(self):
        records = _make_full_dataset()["questions"]
        ok, errs = vd.check_question_ids(records)
        assert ok, f"expected pass, got errors: {errs}"

    def test_duplicate_id_fails(self):
        records = _make_full_dataset()["questions"]
        records[1]["question_id"] = records[0]["question_id"]
        ok, errs = vd.check_question_ids(records)
        assert not ok
        assert any("duplicate" in e.lower() for e in errs)

    def test_invalid_pattern_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["question_id"] = "Question_1"
        ok, errs = vd.check_question_ids(records)
        assert not ok
        assert any("does not match pattern" in e for e in errs)

    def test_too_short_id_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["question_id"] = "Q1"  # only 1 digit
        ok, errs = vd.check_question_ids(records)
        assert not ok


# ============================================================================
# check_category_counts
# ============================================================================
class TestCheckCategoryCounts:
    def test_correct_counts_pass(self):
        records = _make_full_dataset()["questions"]
        ok, errs = vd.check_category_counts(records)
        assert ok, f"expected pass, got errors: {errs}"

    def test_wrong_category_count_fails(self):
        records = _make_full_dataset()["questions"]
        # Convert one simple_address into vector_sql_hybrid → simple goes 10→9, hybrid 7→8
        for r in records:
            if r["category"] == "simple_address":
                r["category"] = "vector_sql_hybrid"
                break
        ok, errs = vd.check_category_counts(records)
        assert not ok
        assert any("simple_address" in e and "9" in e for e in errs)
        assert any("vector_sql_hybrid" in e and "8" in e for e in errs)

    def test_total_record_count_off_fails(self):
        records = _make_full_dataset()["questions"][:30]   # 30 instead of 40
        ok, errs = vd.check_category_counts(records)
        assert not ok
        assert any("total records" in e for e in errs)


# ============================================================================
# check_difficulty_distribution
# ============================================================================
class TestCheckDifficultyDistribution:
    def test_balanced_distribution_passes(self):
        records = _make_full_dataset()["questions"]
        ok, errs = vd.check_difficulty_distribution(records)
        assert ok, f"expected pass, got errors: {errs}"

    def test_too_few_easy_fails(self):
        records = _make_full_dataset()["questions"]
        for r in records:
            if r["difficulty"] == "easy":
                r["difficulty"] = "medium"
        ok, errs = vd.check_difficulty_distribution(records)
        assert not ok
        assert any("easy" in e for e in errs)


# ============================================================================
# check_db_resolvability (mocked SQLClient)
# ============================================================================
class TestCheckDbResolvability:
    """The check itself imports SQLClient at function-call time, so we patch
    the import target before invoking the check."""

    def test_all_addresses_resolve_passes(self):
        records = _make_full_dataset()["questions"]
        records[0]["hint_address"] = "Real Street 1"
        records[1]["hint_address"] = "Real Street 2"

        with patch("src.database.sql_client.SQLClient") as mock_client_cls:
            instance = mock_client_cls.return_value
            instance.building_by_address.return_value = [{"building_id": 1}]   # always resolves

            ok, errs = vd.check_db_resolvability(records)
        assert ok, f"expected pass, got errors: {errs}"

    def test_unresolvable_address_fails(self):
        records = _make_full_dataset()["questions"]
        records[0]["hint_address"] = "Nonexistent Address 9999"

        with patch("src.database.sql_client.SQLClient") as mock_client_cls:
            instance = mock_client_cls.return_value
            instance.building_by_address.return_value = []   # nothing resolves

            ok, errs = vd.check_db_resolvability(records)
        assert not ok
        assert any("did not resolve" in e for e in errs)

    def test_edge_case_with_missing_data_marker_skips_check(self):
        """edge_case records flagged for the missing-data path must NOT trip
        check 7 even when the address doesn't resolve."""
        records = _make_full_dataset()["questions"]
        edge_record = next(r for r in records if r["category"] == "edge_case")
        edge_record["hint_address"] = "Sandbäcksvägen 999"
        edge_record["notes"] = "Tests missing_data uncertainty path."

        with patch("src.database.sql_client.SQLClient") as mock_client_cls:
            instance = mock_client_cls.return_value
            instance.building_by_address.return_value = []   # would normally fail

            ok, errs = vd.check_db_resolvability(records)
        assert ok, f"edge_case with missing_data marker should be skipped, got: {errs}"

    def test_records_without_hint_address_are_skipped(self):
        records = _make_full_dataset()["questions"]
        # All records have hint_address=None by default; nothing to check.
        with patch("src.database.sql_client.SQLClient") as mock_client_cls:
            instance = mock_client_cls.return_value
            ok, errs = vd.check_db_resolvability(records)
        assert ok
        # SQLClient.building_by_address should never have been called.
        assert instance.building_by_address.call_count == 0


# ============================================================================
# check_smoke_subset
# ============================================================================
class TestCheckSmokeSubset:
    def test_strict_subset_passes(self):
        full = _make_full_dataset()["questions"]
        smoke = [copy.deepcopy(full[0]), copy.deepcopy(full[5])]
        ok, errs = vd.check_smoke_subset(full, smoke)
        assert ok, f"expected pass, got errors: {errs}"

    def test_smoke_id_not_in_full_fails(self):
        full = _make_full_dataset()["questions"]
        smoke = [_make_valid_record("Q999", "simple_address")]
        ok, errs = vd.check_smoke_subset(full, smoke)
        assert not ok
        assert any("Q999" in e for e in errs)

    def test_content_drift_fails(self):
        """A smoke record matching by ID but with different content must fail —
        the dataset gets SHA-pinned in pre-registration; drift would invalidate it."""
        full = _make_full_dataset()["questions"]
        smoke = [copy.deepcopy(full[0])]
        smoke[0]["question"] = "Different wording!"
        ok, errs = vd.check_smoke_subset(full, smoke)
        assert not ok
        assert any("byte-identical" in e for e in errs)


# ============================================================================
# End-to-end via run() — exercises the CLI driver
# ============================================================================
class TestEndToEndCliDriver:
    def test_valid_dataset_returns_zero(self, tmp_path):
        dataset_path = tmp_path / "questions.json"
        _write_json(dataset_path, _make_full_dataset())

        args = vd.parse_args([
            "--dataset", str(dataset_path),
            "--no-db",
        ])
        rc = vd.run(args)
        assert rc == 0

    def test_invalid_dataset_returns_one(self, tmp_path):
        broken = _make_full_dataset()
        broken["questions"][0]["category"] = "bogus"
        dataset_path = tmp_path / "questions.json"
        _write_json(dataset_path, broken)

        args = vd.parse_args([
            "--dataset", str(dataset_path),
            "--no-db",
        ])
        rc = vd.run(args)
        assert rc == 1

    def test_smoke_strict_subset_e2e(self, tmp_path):
        full = _make_full_dataset()
        dataset_path = tmp_path / "questions.json"
        _write_json(dataset_path, full)

        smoke = {
            "dataset_version": "1.0",
            "questions": [copy.deepcopy(full["questions"][0]),
                          copy.deepcopy(full["questions"][5])],
        }
        smoke_path = tmp_path / "smoke.json"
        _write_json(smoke_path, smoke)

        args = vd.parse_args([
            "--dataset", str(dataset_path),
            "--smoke", str(smoke_path),
            "--no-db",
        ])
        rc = vd.run(args)
        assert rc == 0

    def test_missing_dataset_file_returns_one(self, tmp_path):
        args = vd.parse_args([
            "--dataset", str(tmp_path / "does_not_exist.json"),
            "--no-db",
        ])
        rc = vd.run(args)
        assert rc == 1

    def test_malformed_json_returns_one(self, tmp_path):
        dataset_path = tmp_path / "questions.json"
        dataset_path.write_text("{ not valid json")
        args = vd.parse_args([
            "--dataset", str(dataset_path),
            "--no-db",
        ])
        rc = vd.run(args)
        assert rc == 1
