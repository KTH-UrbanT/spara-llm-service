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


# --- v21 §2.5: the run record ----------------------------------------------------------

def test_run_record_pins_the_code_and_config_that_produced_the_traces(tmp_path):
    """v20's analysis script was edited between r1 and r2 and the only evidence was a file
    mtime. A record written next to the numbers makes a between-replicate change visible
    in the artifact itself."""
    import os
    from unittest.mock import patch
    from scripts.run_arm_closed_loop import setup_environment, write_run_record
    ds = tmp_path / "cases.jsonl"
    ds.write_text('{"case_id": "X", "question": "q"}\n', encoding="utf-8")
    # setup_environment writes to os.environ for the rest of the process. Unscoped, it
    # would leave EARLY/LATE_CHECKPOINT_ENABLED=true behind and later test modules would
    # start invoking the real judge.
    with patch.dict(os.environ, {}):
        setup_environment("A_full", "run_1")
        write_run_record(tmp_path, "A_full", "run_1", str(ds))

    rec = json.loads((tmp_path / "A_full" / "run_record.json").read_text())
    assert rec["arm"] == "A_full" and rec["run_id"] == "run_1"
    assert rec["env"]["CLOSED_LOOP_EARLY_VOTE_K"] == "3"          # the vote is on the record
    assert rec["env"]["ROUTE_PLAUSIBILITY_PROMPT_VERSION"] == "route_plausibility_v2.txt"
    assert len(rec["dataset_sha256"]) == 64
    assert rec["constants"]["MAX_RETRIES"] == 2
    # The load-bearing field: git is unavailable in the container (submodule .git is a file
    # pointing outside the mount), and v20's mid-run edit was uncommitted anyway.
    assert len(rec["code_sha256"]) == 64 and rec["code_n_files"] > 20


def test_code_fingerprint_changes_when_a_source_byte_changes(tmp_path, monkeypatch):
    """The property the whole record rests on. A git SHA would have been identical across
    v20's r1/r2/r3 despite the edit; this must not be."""
    from scripts import run_arm_closed_loop as m
    before, n_before = m.code_fingerprint()
    assert m.code_fingerprint() == (before, n_before)      # stable across calls

    prompt = Path(m.__file__).resolve().parents[1] / "src/evaluation/closed_loop/prompts/route_plausibility_v2.txt"
    original = prompt.read_bytes()
    try:
        prompt.write_bytes(original + b"\n# edited mid-run\n")
        after, n_after = m.code_fingerprint()
    finally:
        prompt.write_bytes(original)
    assert after != before and n_after == n_before
    assert m.code_fingerprint()[0] == before               # restored


def test_vote_k_is_pinned_per_arm_not_inherited_from_the_shell():
    """The early checkpoint exists only in A_full; an unset variable would let a stray
    shell value change the treatment and go unrecorded."""
    from scripts.run_arm_closed_loop import _ARM_ENV
    assert _ARM_ENV["A_full"]["CLOSED_LOOP_EARLY_VOTE_K"] == "3"
    assert _ARM_ENV["A_open"]["CLOSED_LOOP_EARLY_VOTE_K"] == "1"
    assert _ARM_ENV["A_late_only"]["CLOSED_LOOP_EARLY_VOTE_K"] == "1"
