import json, pytest
from pathlib import Path

def test_snapshot_assembles_union_and_specialists():
    from scripts.run_arm_closed_loop import extract_snapshot
    final = {
        "top_route": "building", "clarification_fired": False, "request_address_fired": False,
        "invoked_generic_sql": True, "invoked_specialized_sql": True, "invoked_vector": False,
        "sql_fields_generic": ["byggnadsid", "energy_performance"],
        "sql_fields_specialized": ["district_heating", "energy_performance"],
        "aggregated_data": {"generic_sql": [{"byggnadsid": "B1", "energy_performance": 100}]},
        "final_response": "ep 100.", "retry_budget": 1,
        "eval_total_tokens": 42, "eval_total_completions": 3,
    }
    snap = extract_snapshot(final)
    assert set(snap["specialists_invoked"]) == {"generic_sql_agent", "specialized_sql_agent"}
    assert "byggnadsid" in snap["sql_fields_used"] and "district_heating" in snap["sql_fields_used"]
    assert snap["sql_fields_used"].count("energy_performance") == 1  # union deduplicates
    assert snap["resolved_building_id"] == "B1"
    assert snap["total_tokens"] == 42

def test_completed_empty(tmp_path):
    from scripts.run_arm_closed_loop import load_completed_case_ids
    assert load_completed_case_ids(tmp_path, "A_open") == set()

def test_completed_from_trace(tmp_path):
    from scripts.run_arm_closed_loop import load_completed_case_ids
    from src.evaluation.closed_loop.trace_schema import append_per_case_trace
    append_per_case_trace({"case_id": "Q001"}, tmp_path, "A_open")
    append_per_case_trace({"case_id": "Q002"}, tmp_path, "A_open")
    assert load_completed_case_ids(tmp_path, "A_open") == {"Q001", "Q002"}

def test_cache_roundtrip(tmp_path):
    from scripts.run_arm_closed_loop import save_case_cache, load_case_cache
    bundle = {"aggregated_data": {"generic_sql": [{"byggnadsid": "B1"}]},
              "invoked_generic_sql": True, "sql_fields_generic": ["byggnadsid"]}
    save_case_cache(tmp_path, "A_open", "Q001", bundle)
    assert load_case_cache(tmp_path, "A_open", "Q001") == bundle

def test_cache_miss(tmp_path):
    from scripts.run_arm_closed_loop import load_case_cache
    assert load_case_cache(tmp_path, "A_open", "Q999") is None

def test_resolve_clarification_passes_only_when_gold_expects_it():
    """Asking for an address is correct behaviour only when the gold route is clarification."""
    from scripts.run_arm_closed_loop import resolve_scoring_verdict
    snap = {"clarification_fired": True, "answer_quality_verdict": None, "final_answer": "Which address?"}
    case = {"question": "q", "expected_route": "clarification"}
    assert resolve_scoring_verdict(snap, case, None)["verdict"] == "pass"

def test_resolve_clarification_fails_when_gold_expects_an_answer():
    """The pathology: every dodged question used to be awarded a pass (21/88 in the 05-30 run)."""
    from scripts.run_arm_closed_loop import resolve_scoring_verdict
    snap = {"clarification_fired": True, "answer_quality_verdict": None, "final_answer": "Which address?"}
    case = {"question": "q", "expected_route": "generic"}
    assert resolve_scoring_verdict(snap, case, None)["verdict"] == "fail"

def test_resolve_request_address_fails_when_gold_expects_an_answer():
    from scripts.run_arm_closed_loop import resolve_scoring_verdict
    snap = {"request_address_fired": True, "answer_quality_verdict": None, "final_answer": "Address?"}
    case = {"question": "q", "expected_route": "building_specific"}
    assert resolve_scoring_verdict(snap, case, None)["verdict"] == "fail"

def test_resolve_short_circuit_beats_a_stale_in_loop_verdict():
    """After a rewind that lands in request_address, score the outcome, not attempt 1."""
    from scripts.run_arm_closed_loop import resolve_scoring_verdict
    snap = {"request_address_fired": True, "final_answer": "Address?",
            "answer_quality_verdict": {"verdict": "pass", "axes": {"calibration": 9}}}
    case = {"question": "q", "expected_route": "generic"}
    assert resolve_scoring_verdict(snap, case, None)["verdict"] == "fail"

def test_resolve_reuses_in_loop_verdict():
    from scripts.run_arm_closed_loop import resolve_scoring_verdict
    snap = {"clarification_fired": False, "request_address_fired": False,
            "answer_quality_verdict": {"verdict": "fail"}, "final_answer": "x"}
    assert resolve_scoring_verdict(snap, {"question": "q"}, None)["verdict"] == "fail"

def test_cache_keys_exclude_this_arms_decision_flags():
    """Caching control-flow flags made the treatment arms inherit decisions they never made,
    and let a stale flag overwrite a real judge verdict with a fake pass (§1.5b)."""
    from scripts.run_arm_closed_loop import _CACHE_KEYS
    assert "clarification_fired" not in _CACHE_KEYS
    assert "request_address_fired" not in _CACHE_KEYS
    # ...but the identity gate IS derived from the cached evidence, so it must carry over:
    # the node that computes it is skipped precisely because the evidence is cached.
    assert "identity_gate_blocked" in _CACHE_KEYS

def test_identity_gate_flag_round_trips_into_the_cached_arm():
    from scripts.run_arm_closed_loop import extract_snapshot, build_initial_state
    snap = extract_snapshot({"top_route": "building", "identity_gate_blocked": True,
                             "final_response": "Could not find that building."})
    state = build_initial_state({"case_id": "X", "question": "q"}, "A_full", snap["cache_bundle"])
    assert state["identity_gate_blocked"] is True

def test_resolve_open_arm_scores_once():
    from unittest.mock import MagicMock
    from scripts.run_arm_closed_loop import resolve_scoring_verdict
    scorer = MagicMock()
    scorer.answer_quality.return_value = MagicMock(verdict="pass", axes={}, composite=8.0)
    snap = {"clarification_fired": False, "request_address_fired": False,
            "answer_quality_verdict": None, "final_answer": "An answer.", "aggregated_data": {}}
    assert resolve_scoring_verdict(snap, {"question": "q"}, lambda: scorer)["verdict"] == "pass"
    scorer.answer_quality.assert_called_once()

def test_load_cost_cap_uses_mult_override(tmp_path):
    """The CLI-overridable mult parameter scales the baseline; default constant unchanged."""
    from scripts.run_arm_closed_loop import (
        load_cost_cap, write_cost_baseline, COST_CAP_MULT,
    )
    # Per-case mean = 1000 tokens (mean of [800, 1000, 1200]).
    write_cost_baseline(tmp_path, "A_open", [800, 1000, 1200])
    # Default uses COST_CAP_MULT (= 5.0): 5 * 1000 = 5000.
    assert load_cost_cap(tmp_path, "A_open") == 5.0 * 1000.0
    # Explicit override.
    assert load_cost_cap(tmp_path, "A_open", mult=999.0) == 999.0 * 1000.0
    # CLI-effectively-disabled.
    assert load_cost_cap(tmp_path, "A_open", mult=1e9) == 1e9 * 1000.0
    # Constant unchanged (pre-registration discipline).
    assert COST_CAP_MULT == 5.0
