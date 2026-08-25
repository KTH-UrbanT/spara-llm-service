"""The re-scorer licenses a gold-label edit over frozen traces, so its two claims need
their own checks: (1) on an unchanged dataset it reproduces every stored value (else the
acceptance gate is vacuous), (2) after an edit it recomputes rather than replays — the
failure mode that made `replay_rescore.py` unusable for this."""
from scripts.rescore_deterministic import consensus, flips, rescore_rows


def _row(cid, answer, mi, schema=3, **kw):
    """A stored trace row at the given schema version."""
    # schema=3 is what every frozen 2026-08-13 trace carries: final_answer stored [:1000].
    others = dict(route_match=True, agent_match=True, building_id_match=True,
                  field_coverage_pass=True, must_not_include_pass=True,
                  semantic_judge_pass=True)
    others.update(kw)
    return {"case_id": cid, "final_answer": answer, "must_include_pass": mi,
            "trace_schema_version": schema,
            "case_pass": all(others.values()) and mi, **others}


def test_unchanged_dataset_reproduces_stored_values():
    """Re-scoring an unchanged dataset reproduces the stored verdicts exactly."""
    cases = {"c1": {"must_include": ["B", "53"]}, "c2": {"must_include": ["189"]}}
    rows = rescore_rows([_row("c1", "class B, 53 kWh", True),
                         _row("c2", "class B, 53 kWh", False)], cases)
    assert all(x["mi_re"] == x["mi_stored"] for x in rows)
    assert all(x["det_re"] == x["det_stored"] for x in rows)
    assert all(x["judge_re"] == x["judge_stored"] for x in rows)


def test_edited_gold_recomputes_and_flip_list_names_the_row():
    """Stored says fail (old gold F/189); the answer cites B/53; the edited dataset must
    flip the row — and only via recomputation, never by echoing the stored flag."""
    cases = {"c1": {"must_include": ["B", "53"]}}
    reps = {"r1": {"A_open": rescore_rows([_row("c1", "class B, 53 kWh", False)], cases)}}
    fl = flips(reps)
    assert [(x["case_id"], x["mi_stored"], x["mi_re"]) for x in fl] == [("c1", False, True)]
    assert fl[0]["det_re"] and fl[0]["judge_re"] and not fl[0]["det_stored"]


def test_truncated_row_is_inconclusive_not_a_flip():
    """A row stored at the 1000-char cap whose token is past the cap must keep its stored
    value — recomputing False there would relabel on text the trace no longer carries."""
    cases = {"c1": {"must_include": ["zzz"]}, "c2": {"must_include": ["b"]}}
    long = "b" + "x" * 999                       # exactly at cap
    rows = rescore_rows([_row("c1", long, True),   # token invisible → inconclusive, stored kept
                         _row("c2", long, False)], cases)  # token in prefix → True is sound
    assert rows[0]["inconclusive"] and rows[0]["mi_re"] is True
    assert not rows[1]["inconclusive"] and rows[1]["mi_re"] is True
    assert flips({"r1": {"A_open": rows}}) == [{"run": "r1", "arm": "A_open", **rows[1]}]


def test_v4_rows_are_conclusive_because_the_answer_is_stored_whole():
    """Schema v4 stores final_answer untruncated, so a long answer that lacks the token is a
    genuine failure and must flip — holding it inconclusive would silently suppress a real
    correction. A row carrying no version field at all keeps the conservative v3 rule."""
    cases = {"c1": {"must_include": ["zzz"]}}
    long = "b" + "x" * 999                       # at the v3 cap, token genuinely absent
    v4 = rescore_rows([_row("c1", long, True, schema=4)], cases)
    assert not v4[0]["inconclusive"] and v4[0]["mi_re"] is False
    assert [x["case_id"] for x in flips({"r1": {"A_open": v4}})] == ["c1"]

    unversioned = [{k: v for k, v in _row("c1", long, True).items()
                    if k != "trace_schema_version"}]
    out = rescore_rows(unversioned, cases)
    assert out[0]["inconclusive"] and out[0]["mi_re"] is True


def test_judge_none_stays_falsy_in_judge_inclusive_view():
    """An unmeasured judge stays falsy after a re-score, matching `case_pass`."""
    # deterministic_checks.case_pass: an unmeasured judge (None) is not a passing case.
    cases = {"c1": {"must_include": []}}
    rows = rescore_rows([_row("c1", "x", True, semantic_judge_pass=None)], cases)
    assert rows[0]["det_re"] and not rows[0]["judge_re"]


def test_consensus_majority_and_direction():
    """2-of-3 majority per arm; concordant flips stay out of gained/lost, real conversions
    land in `gained` for the b arm."""
    cases = {"conc": {"must_include": ["53"]}, "conv": {"must_include": ["ok"]}}
    def rep(open_ans, full_ans):
        """A two-arm replicate differing only in the answer text."""
        return {"A_open": rescore_rows([_row("conc", "53", False), _row("conv", open_ans, False)], cases),
                "A_full": rescore_rows([_row("conc", "53", False), _row("conv", full_ans, False)], cases)}
    reps = {f"r{i}": rep("no", "ok") for i in (1, 2, 3)}   # conc flips everywhere, conv converts
    out = consensus(reps, "det_re")["A_open vs A_full"]
    assert out["gained"] == ["conv"] and out["lost"] == []
    assert out["pass_a"] == 1 and out["pass_b"] == 2       # conc passes both arms after recompute
