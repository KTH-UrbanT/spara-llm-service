import json, pytest
from pathlib import Path

SINGLE_TURN = [
    {"case_id": "C001", "question": "q", "expected_route": "generic",
     "case_type": "generic", "expected_agent": "GenericAgent"},
    {"case_id": "C002", "question": "q", "expected_route": "building_specific",
     "case_type": "building", "expected_agent": "BuildingAgent"},
    {"case_id": "C003", "question": "q", "expected_route": "combined",
     "case_type": "combined", "expected_agent": "BuildingAgent"},
    {"case_id": "C004", "question": "q", "expected_route": "clarification",
     "case_type": "clarification", "expected_agent": "BuildingAgent"},
]

def test_filter_keeps_single_turn():
    from scripts.validate_dataset_closed_loop import filter_cases
    assert len(filter_cases(SINGLE_TURN)) == 4

def test_filter_excludes_turns_field():
    from scripts.validate_dataset_closed_loop import filter_cases
    assert filter_cases([{"case_id": "MT", "question": "q",
        "expected_route": "generic", "turns": [{}]}]) == []

def test_filter_excludes_bad_case_types():
    from scripts.validate_dataset_closed_loop import filter_cases
    for bad in ["expert_handoff", "report_generation", "out_of_scope",
                "multi_turn_clarification", "multi_turn_building_specific",
                "multi_turn_generic_to_building_specific",
                "multi_turn_building_specific_followup",
                "multi_turn_ambiguous_address_resolution",
                "multi_turn_expert_handoff"]:
        assert filter_cases([{"case_id": "X", "question": "q",
            "expected_route": "generic", "case_type": bad}]) == [], bad

def test_filter_excludes_bad_route():
    from scripts.validate_dataset_closed_loop import filter_cases
    assert filter_cases([{"case_id": "X", "question": "q",
        "expected_route": "out_of_scope", "case_type": "oos"}]) == []

def test_filter_excludes_bad_agent():
    from scripts.validate_dataset_closed_loop import filter_cases
    assert filter_cases([{"case_id": "X", "question": "q",
        "expected_route": "generic", "expected_agent": "ExpertHandoffAgent"}]) == []

def test_filter_allows_absent_expected_agent():
    from scripts.validate_dataset_closed_loop import filter_cases
    assert len(filter_cases([{"case_id": "C", "question": "q",
        "expected_route": "generic", "case_type": "generic"}])) == 1

def test_write_manifest(tmp_path):
    from scripts.validate_dataset_closed_loop import filter_cases, write_manifest
    filtered = filter_cases(SINGLE_TURN)
    mf = tmp_path / "m.csv"; jl = tmp_path / "f.jsonl"
    write_manifest(filtered, jl, mf)
    assert mf.exists() and jl.exists()
    assert len(mf.read_text().splitlines()) == len(filtered) + 1
