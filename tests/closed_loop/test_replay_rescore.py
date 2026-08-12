"""The replay is the acceptance test for a scoring change, so its own branches need one."""
from scripts.replay_rescore import _was_short_circuit, rescore

_CHECKS_OK = {"route_match": True, "agent_match": True, "building_id_match": True,
              "field_coverage_pass": True, "must_include_pass": True,
              "must_not_include_pass": True}


def _row(**kw) -> dict:
    d = {**_CHECKS_OK, "expected_route": "generic", "semantic_judge_pass": True,
         "final_sql_fields_used": [], "final_vector_chunks_retrieved": []}
    d.update(kw)
    return d


def test_short_circuit_detected_by_absent_axes():
    assert _was_short_circuit(_row(answer_quality_verdict={"verdict": "pass"})) is True
    assert _was_short_circuit(_row(answer_quality_verdict={"verdict": "pass", "axes": {}})) is True


def test_fail_open_is_not_mistaken_for_a_short_circuit():
    """Both were recorded as a bare "pass"; only the fail-open carries an axis."""
    v = {"verdict": "pass", "composite": 0.0, "axes": {"_fail_open": True}}
    assert _was_short_circuit(_row(answer_quality_verdict=v)) is False
    assert rescore(_row(answer_quality_verdict=v), fix3=True, fix4=False)["semantic_judge_pass"] is None


def test_fix3_short_circuit_follows_the_gold_route():
    v = {"verdict": "pass"}
    wrong = rescore(_row(answer_quality_verdict=v, expected_route="generic"), True, False)
    right = rescore(_row(answer_quality_verdict=v, expected_route="clarification"), True, False)
    assert wrong["semantic_judge_pass"] is False and wrong["case_pass"] is False
    assert right["semantic_judge_pass"] is True and right["case_pass"] is True


def test_without_fix3_the_stored_column_is_used():
    """Isolates Fix 2: the veto removal must not silently drag the judge rule in with it."""
    row = _row(answer_quality_verdict={"verdict": "pass"}, semantic_judge_pass=True)
    assert rescore(row, fix3=False, fix4=False)["case_pass"] is True


def test_post_fix_sentinel_rows_are_recognised_as_short_circuits():
    """The script must read traces from BOTH sides of the fix. A post-fix short-circuit
    carries {"_short_circuit": True}; treating that dict as judge axes would push it through
    _score, zero-fill three axes and re-score a correct clarification case to fail."""
    v = {"verdict": "pass", "axes": {"_short_circuit": True}, "composite": 0.0}
    row = _row(answer_quality_verdict=v, expected_route="clarification")
    assert _was_short_circuit(row) is True
    out = rescore(row, fix3=True, fix4=True)
    assert out["semantic_judge_pass"] is True and out["case_pass"] is True
    assert out["evidence_present"] is None          # never sent through _score

    wrong = _row(answer_quality_verdict=v, expected_route="generic")
    assert rescore(wrong, fix3=True, fix4=True)["semantic_judge_pass"] is False


def test_no_answer_sentinel_is_also_a_short_circuit():
    v = {"verdict": "fail", "axes": {"_no_answer": True}, "composite": 0.0}
    assert _was_short_circuit(_row(answer_quality_verdict=v)) is True


def test_fix4_drops_faithfulness_when_no_evidence_was_retrieved():
    axes = {"faithfulness": 0, "answer_relevance": 9, "question_coverage": 9, "calibration": 9}
    row = _row(answer_quality_verdict={"verdict": "fail", "axes": axes})
    out = rescore(row, fix3=True, fix4=True)
    assert out["evidence_present"] is False
    assert out["semantic_judge_pass"] is True   # 9.0 over the three scored axes

    grounded = _row(answer_quality_verdict={"verdict": "fail", "axes": axes},
                    final_sql_fields_used=["energy_class"])
    out2 = rescore(grounded, fix3=True, fix4=True)
    assert out2["evidence_present"] is True
    assert out2["semantic_judge_pass"] is False  # faithfulness 0 still trips the floor
