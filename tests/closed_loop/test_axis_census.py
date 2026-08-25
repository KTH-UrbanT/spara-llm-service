"""Tests for scripts/axis_census.py — plan-eil-v25 Step 3.

The census exists to answer one question per axis: does it carry information, or is it
a constant wearing an axis's name (the `question_coverage` defect v24 removed)? These
pin the two ways that answer can go wrong: calling a constant informative, and letting
not-applicable nulls masquerade as scores.
"""
from scripts.axis_census import LATE, OPEN, build, collect, summarise


def _rec(pool, axes, run="v24_r1", arm="A_open"):
    """One census record with the given axis scores."""
    return {"pool": pool, "run": run, "arm": arm, "case_id": "C", "axes": axes}


def test_constant_axis_is_flagged_degenerate():
    """The question_coverage defect: 10 on every scored attempt."""
    recs = [_rec(LATE, {"question_coverage": 10}) for _ in range(50)]
    s = summarise(recs, "question_coverage", 98.0)
    assert s["variance"] == 0.0 and s["pct_modal"] == 100.0
    assert s["degenerate"] is True


def test_varying_axis_is_not_flagged():
    """An axis with real spread is informative and must not be called degenerate."""
    recs = ([_rec(LATE, {"entity_consistency": 10})] * 40
            + [_rec(LATE, {"entity_consistency": 0})] * 10)
    s = summarise(recs, "entity_consistency", 98.0)
    assert s["degenerate"] is False
    assert s["n_sub_floor"] == 10 and s["pct_sub_floor"] == 20.0
    assert s["variance"] > 0


def test_nulls_are_counted_but_never_scored_as_zero():
    """A null axis is not applicable. Counting it as 0 would invent a sub-floor fire
    on every evidence-absent case — the v20 false-negative epidemic, restated."""
    recs = [_rec(LATE, {"entity_consistency": None}) for _ in range(9)]
    recs.append(_rec(LATE, {"entity_consistency": 10}))
    s = summarise(recs, "entity_consistency", 98.0)
    assert s["n_present"] == 10 and s["n_null"] == 9 and s["n_scored"] == 1
    assert s["n_sub_floor"] == 0
    assert s["histogram"] == {"10": 1}


def test_absent_axis_differs_from_null_axis():
    """A row that never carried the axis is out of the denominator entirely."""
    s = summarise([_rec(LATE, {"faithfulness": 9})], "entity_consistency", 98.0)
    assert s["n_present"] == 0 and s["n_null"] == 0


def test_pools_are_reported_separately_and_pooled():
    """Late and open pools are reported both apart and together."""
    recs = [_rec(LATE, {"faithfulness": 10}), _rec(OPEN, {"faithfulness": 0})]
    c = build(recs, 98.0)
    assert c["pools"][LATE]["axes"]["faithfulness"]["n_scored"] == 1
    assert c["pools"][OPEN]["axes"]["faithfulness"]["n_sub_floor"] == 1
    assert c["pools"]["all"]["axes"]["faithfulness"]["n_scored"] == 2


def test_per_group_split_tracks_run_and_arm():
    """Per-group counts keep run and arm distinct, so pooling cannot hide drift."""
    recs = [_rec(LATE, {"entity_consistency": 0}, run="v24_r1", arm="A_full"),
            _rec(LATE, {"entity_consistency": 10}, run="v24_r2", arm="A_full")]
    g = summarise(recs, "entity_consistency", 98.0)["per_group"]
    assert g["v24_r1/A_full"]["n_sub_floor"] == 1
    assert g["v24_r2/A_full"]["n_sub_floor"] == 0


def test_collect_reads_late_attempts_and_open_finals(tmp_path):
    """Each pool is read from its correct trace file: attempts for late, finals for open."""
    import json
    run = tmp_path / "v24_r1"
    for arm, fname, rows in [
        ("A_full", "per_attempt.jsonl", [
            {"case_id": "C1", "checkpoint_fired": "early",
             "evaluator_verdict_json": {"axes": {"intent_consistency": 10}}},
            {"case_id": "C1", "checkpoint_fired": "late",
             "evaluator_verdict_json": {"axes": {"entity_consistency": 0}}}]),
        ("A_open", "per_case.jsonl", [
            {"case_id": "C1", "answer_quality_verdict": {"axes": {"entity_consistency": 9}}}]),
    ]:
        d = run / arm / "traces"
        d.mkdir(parents=True)
        (d / fname).write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    recs = collect([run])
    pools = {r["pool"] for r in recs}
    assert pools == {LATE, OPEN}
    # The early checkpoint's axes must not leak into the answer-quality census.
    assert all("intent_consistency" not in r["axes"] for r in recs)
